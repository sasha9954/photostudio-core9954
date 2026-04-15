from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import mimetypes
import os
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.static_paths import ASSETS_DIR
from app.engine.audio_analyzer import analyze_audio
from app.engine.audio_scene_segmenter import build_gemini_audio_segmentation
from app.engine.scenario_audio_map_v11 import validate_audio_map_v11
from app.engine.gemini_rest import post_generate_content
from app.engine.scenario_role_planner import ROLE_PLAN_PROMPT_VERSION, build_gemini_role_plan
from app.engine.scenario_scene_planner import SCENE_PLAN_PROMPT_VERSION, build_gemini_scene_plan
from app.engine.scenario_scene_prompter import SCENE_PROMPTS_PROMPT_VERSION, build_gemini_scene_prompts
from app.engine.scenario_video_prompt_writer import FINAL_VIDEO_PROMPT_STAGE_VERSION, generate_ltx_video_prompt_metadata
from app.engine.video_capability_canon import (
    DEFAULT_VIDEO_MODEL_ID,
    build_capability_diagnostics_summary,
    get_capability_rules_source_version,
    get_video_model_capability_profile,
)

logger = logging.getLogger(__name__)

MAX_STORY_CORE_IMAGE_BYTES = 8 * 1024 * 1024

STAGE_IDS = (
    "input_package",
    "audio_map",
    "story_core",
    "role_plan",
    "scene_plan",
    "scene_prompts",
    "final_video_prompt",
    "finalize",
)

STAGE_DEPENDENCIES: dict[str, list[str]] = {
    "input_package": [],
    "audio_map": ["input_package"],
    "story_core": ["input_package", "audio_map"],
    "role_plan": ["input_package", "audio_map", "story_core"],
    "scene_plan": ["input_package", "audio_map", "story_core", "role_plan"],
    "scene_prompts": ["input_package", "audio_map", "story_core", "role_plan", "scene_plan"],
    "final_video_prompt": ["input_package", "audio_map", "story_core", "role_plan", "scene_plan", "scene_prompts"],
    "finalize": ["input_package", "audio_map", "story_core", "role_plan", "scene_plan", "scene_prompts", "final_video_prompt"],
}

DOWNSTREAM_BY_STAGE: dict[str, list[str]] = {
    stage_id: [candidate for candidate, deps in STAGE_DEPENDENCIES.items() if stage_id in deps]
    for stage_id in STAGE_IDS
}

MANUAL_RESET_DOWNSTREAM: dict[str, list[str]] = {
    "audio_map": ["story_core", "role_plan", "scene_plan", "scene_prompts", "final_video_prompt", "finalize"],
    "story_core": ["role_plan", "scene_plan", "scene_prompts", "final_video_prompt", "finalize"],
    "role_plan": ["scene_plan", "scene_prompts", "final_video_prompt", "finalize"],
    "scene_plan": ["scene_prompts", "final_video_prompt", "finalize"],
    "scene_prompts": ["final_video_prompt", "finalize"],
    "final_video_prompt": ["finalize"],
    "finalize": [],
}

STAGE_SECTION_RESETTERS: dict[str, Any] = {
    "story_core": lambda: {},
    "audio_map": lambda: {},
    "role_plan": lambda: {},
    "scene_plan": lambda: {"scenes": []},
    "scene_prompts": lambda: {"scenes": []},
    "final_video_prompt": lambda: {"scenes": []},
    "finalize": lambda: {"scenes": []},
}

STAGE_DIAGNOSTIC_PREFIXES: dict[str, tuple[str, ...]] = {
    "story_core": ("story_core_",),
    "audio_map": ("audio_", "transcript_"),
    "role_plan": ("role_plan_",),
    "scene_plan": ("scene_plan_",),
    "scene_prompts": ("scene_prompts_",),
    "final_video_prompt": ("final_video_prompt_",),
    "finalize": ("finalize_",),
}
STAGE_PACKAGE_FIELD_BY_STAGE: dict[str, str] = {
    "input_package": "input",
    "audio_map": "audio_map",
    "story_core": "story_core",
    "role_plan": "role_plan",
    "scene_plan": "scene_plan",
    "scene_prompts": "scene_prompts",
    "final_video_prompt": "final_video_prompt",
    "finalize": "final_storyboard",
}
_OWNERSHIP_ROLE_MAP = {
    "main": "character_1",
    "support": "character_2",
    "antagonist": "character_3",
    "shared": "shared",
    "world": "environment",
}
_BINDING_TYPES = {"carried", "worn", "held", "pocketed", "nearby", "environment"}
_SUBJECT_REF_TOKENS = {"char", "character", "person", "subject", "talent", "hero", "protagonist", "human", "face"}
_OBJECT_REF_TOKENS = {"prop", "object", "item", "wardrobe", "vehicle", "accessory", "outfit", "tool", "bag", "phone"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stage_output_field(stage_id: str) -> str:
    return STAGE_PACKAGE_FIELD_BY_STAGE.get(stage_id, stage_id)


def _has_stage_output(package: dict[str, Any], stage_id: str) -> bool:
    safe_pkg = _safe_dict(package)
    output = safe_pkg.get(_stage_output_field(stage_id))
    if stage_id == "input_package":
        return bool(_safe_dict(output))
    if stage_id == "audio_map":
        return _is_usable_audio_map(_safe_dict(output))
    if stage_id in {"scene_plan", "scene_prompts", "final_video_prompt", "finalize"}:
        return isinstance(output, dict) and "scenes" in output
    return isinstance(output, dict) and bool(output)


def _can_reuse_stage_output(package: dict[str, Any], stage_id: str) -> bool:
    statuses = _safe_dict(_safe_dict(package).get("stage_statuses"))
    status = str(_safe_dict(statuses.get(stage_id)).get("status") or "").strip().lower()
    return status == "done" and _has_stage_output(package, stage_id)


def _compact_prompt_payload(value: Any) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = _compact_prompt_payload(item)
            if cleaned in (None, "", [], {}):
                continue
            compact[str(key)] = cleaned
        return compact
    if isinstance(value, list):
        compact_list: list[Any] = []
        for item in value:
            cleaned = _compact_prompt_payload(item)
            if cleaned in (None, "", [], {}):
                continue
            compact_list.append(cleaned)
        return compact_list
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_ref_meta(meta: Any) -> dict[str, Any]:
    row = _safe_dict(meta)
    ownership_role = str(row.get("ownershipRole") or row.get("ownership_role") or "auto").strip().lower() or "auto"
    ownership_mapped = str(row.get("ownershipRoleMapped") or row.get("ownership_role_mapped") or "").strip().lower()
    if ownership_mapped not in {"character_1", "character_2", "character_3", "shared", "environment"}:
        ownership_mapped = _OWNERSHIP_ROLE_MAP.get(ownership_role, "")
    binding_type = str(row.get("bindingType") or row.get("binding_type") or "nearby").strip().lower() or "nearby"
    if binding_type not in _BINDING_TYPES:
        binding_type = "nearby"
    return {
        **row,
        "ownershipRole": ownership_role,
        "ownershipRoleMapped": ownership_mapped,
        "bindingType": binding_type,
    }


def _normalize_refs_inventory(refs_inventory: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized: dict[str, Any] = {}
    ownership_binding_inventory: list[dict[str, Any]] = []
    for key, value in refs_inventory.items():
        if not isinstance(value, dict):
            normalized[str(key)] = value
            continue
        row = dict(value)
        meta = _normalize_ref_meta(row.get("meta"))
        row["meta"] = meta
        normalized[str(key)] = row
        if meta.get("ownershipRoleMapped") or meta.get("bindingType") != "nearby":
            ownership_binding_inventory.append(
                {
                    "ref_id": str(key),
                    "label": str(row.get("source_label") or row.get("value") or key).strip()[:120],
                    "ownershipRole": str(meta.get("ownershipRole") or "auto"),
                    "ownershipRoleMapped": str(meta.get("ownershipRoleMapped") or ""),
                    "bindingType": str(meta.get("bindingType") or "nearby"),
                }
            )
    return normalized, ownership_binding_inventory[:24]


def _extract_audio_url_from_refs(refs_inventory: dict[str, Any]) -> str:
    audio_in = _safe_dict(refs_inventory.get("audio_in"))
    meta = _safe_dict(audio_in.get("meta"))
    candidates = (
        audio_in.get("value"),
        meta.get("url"),
        audio_in.get("preview"),
    )
    for item in candidates:
        value = str(item or "").strip()
        if value:
            return value
    return ""


def _resolve_active_video_model_id(input_pkg: dict[str, Any]) -> str:
    for key in ("video_model", "video_model_id", "model_id"):
        value = str(input_pkg.get(key) or "").strip().lower()
        if value:
            return value
    return DEFAULT_VIDEO_MODEL_ID


def _extract_ref_urls(ref_node: Any) -> list[str]:
    def _looks_like_ref_url(value: str) -> bool:
        lower = value.lower()
        if lower.startswith(("http://", "https://", "blob:", "data:")):
            return True
        if value.startswith("/"):
            return True
        if "static/assets/" in lower:
            return True
        return "/" in value

    if isinstance(ref_node, str):
        value = ref_node.strip()
        return [value] if value and _looks_like_ref_url(value) else []
    node = _safe_dict(ref_node)
    urls: list[str] = []
    for candidate in (
        node.get("value"),
        _safe_dict(node.get("meta")).get("url"),
    ):
        value = str(candidate or "").strip()
        if value and _looks_like_ref_url(value):
            urls.append(value)
    for ref in _safe_list(node.get("refs")):
        value = str(ref or "").strip()
        if value and _looks_like_ref_url(value):
            urls.append(value)
    return list(dict.fromkeys(urls))


def _role_to_ref_key(role: str) -> str:
    clean = str(role or "").strip()
    if not clean:
        return ""
    return clean if clean.startswith("ref_") else f"ref_{clean}"


def _build_refs_by_role_fallback(
    refs_inventory: dict[str, Any],
    active_roles: list[Any],
    primary_role: str,
) -> dict[str, list[str]]:
    roles = [
        str(role).strip()
        for role in [primary_role, *active_roles]
        if str(role).strip()
    ]
    resolved: dict[str, list[str]] = {}
    for role in roles:
        ref_key = _role_to_ref_key(role)
        if not ref_key:
            continue
        urls = _extract_ref_urls(refs_inventory.get(ref_key))
        if urls:
            resolved[role] = urls
    return resolved


def _normalize_input_audio_source(input_payload: dict[str, Any], refs_inventory: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(_safe_dict(input_payload))
    audio_url = str(normalized.get("audio_url") or "").strip()
    refs_audio_url = _extract_audio_url_from_refs(refs_inventory)
    if not audio_url and refs_audio_url:
        audio_url = refs_audio_url
    normalized["audio_url"] = audio_url

    source = _safe_dict(normalized.get("source"))
    source_value = str(source.get("source_value") or source.get("sourceValue") or "").strip()
    if not source_value:
        source_value = audio_url
    if source_value:
        source["source_value"] = source_value
    normalized["source"] = source
    return normalized


def _resolve_director_mode(raw_mode: Any, *, content_type: str = "") -> str:
    normalized = str(raw_mode or "").strip().lower()
    if normalized in {"clip", "story", "ad"}:
        return normalized
    if normalized in {"music_video", "клип"}:
        return "clip"
    if normalized in {"история"}:
        return "story"
    if normalized in {"реклама", "reklama"}:
        return "ad"
    fallback_content_type = str(content_type or "").strip().lower()
    if fallback_content_type == "music_video":
        return "clip"
    if fallback_content_type == "ad":
        return "ad"
    return "story"


def _clamp_ratio(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except Exception:
        return float(default)


def _normalize_creative_config(raw_config: Any) -> dict[str, Any]:
    row = _safe_dict(raw_config)
    route_mix_mode = str(row.get("route_mix_mode") or row.get("routeMixMode") or "auto").strip().lower() or "auto"
    if route_mix_mode not in {"auto", "custom"}:
        route_mix_mode = "auto"
    lipsync_ratio = _clamp_ratio(row.get("lipsync_ratio"), 0.25)
    first_last_ratio = _clamp_ratio(row.get("first_last_ratio"), 0.25)
    remaining = max(0.0, 1.0 - lipsync_ratio - first_last_ratio)
    preferred_routes = [str(item).strip().lower() for item in _safe_list(row.get("preferred_routes")) if str(item).strip()]
    preferred_routes = [route for route in preferred_routes if route in {"i2v", "ia2v", "first_last"}]
    if not preferred_routes:
        preferred_routes = ["i2v", "first_last"]
    try:
        max_consecutive_lipsync = int(row.get("max_consecutive_lipsync"))
    except Exception:
        max_consecutive_lipsync = 2
    max_consecutive_lipsync = max(1, min(6, max_consecutive_lipsync))
    return {
        "route_mix_mode": route_mix_mode,
        "lipsync_ratio": round(lipsync_ratio, 3),
        "first_last_ratio": round(first_last_ratio, 3),
        "i2v_ratio": round(remaining, 3),
        "preferred_routes": preferred_routes,
        "max_consecutive_lipsync": max_consecutive_lipsync,
    }


def _compute_route_budget_for_total(total_scenes: int, creative_config: dict[str, Any]) -> dict[str, int]:
    if total_scenes <= 0:
        return {"i2v": 0, "ia2v": 0, "first_last": 0}
    if total_scenes == 1:
        return {"i2v": 1, "ia2v": 0, "first_last": 0}

    lipsync_ratio = _clamp_ratio(creative_config.get("lipsync_ratio"), 0.25)
    first_last_ratio = _clamp_ratio(creative_config.get("first_last_ratio"), 0.25)
    ia2v = int(round(total_scenes * lipsync_ratio))
    first_last = int(round(total_scenes * first_last_ratio))
    i2v = total_scenes - ia2v - first_last

    if i2v < 1:
        deficit = 1 - i2v
        i2v = 1
        reducible_fl = min(deficit, max(0, first_last))
        first_last -= reducible_fl
        deficit -= reducible_fl
        if deficit > 0:
            ia2v = max(0, ia2v - deficit)
    while ia2v + i2v + first_last < total_scenes:
        i2v += 1
    while ia2v + i2v + first_last > total_scenes:
        if first_last > 0:
            first_last -= 1
        elif ia2v > 0:
            ia2v -= 1
        else:
            i2v = max(0, i2v - 1)
    return {"i2v": i2v, "ia2v": ia2v, "first_last": first_last}


def _validate_scene_plan_route_budget(
    *,
    package: dict[str, Any],
    scene_plan: dict[str, Any],
    diagnostics: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    scene_rows = [row for row in _safe_list(scene_plan.get("scenes")) if isinstance(row, dict)]
    route_counts = {"i2v": 0, "ia2v": 0, "first_last": 0}
    longest_lipsync_streak = 0
    current_lipsync_streak = 0
    for row in scene_rows:
        route = str(row.get("route") or "").strip().lower()
        if route in route_counts:
            route_counts[route] += 1
        if route == "ia2v":
            current_lipsync_streak += 1
            longest_lipsync_streak = max(longest_lipsync_streak, current_lipsync_streak)
        else:
            current_lipsync_streak = 0

    target_budget = _compute_route_budget_for_total(len(scene_rows), creative_config)
    max_consecutive = int(creative_config.get("max_consecutive_lipsync") or 2)
    tolerance = 1 if len(scene_rows) >= 6 else 0
    errors: list[str] = []
    for route_name in ("ia2v", "i2v", "first_last"):
        if abs(route_counts.get(route_name, 0) - target_budget.get(route_name, 0)) > tolerance:
            errors.append(
                f"route {route_name} count={route_counts.get(route_name, 0)} target≈{target_budget.get(route_name, 0)}"
            )
    if longest_lipsync_streak > max_consecutive:
        errors.append(f"too many consecutive lipsync scenes: streak={longest_lipsync_streak} max={max_consecutive}")
    if route_counts.get("first_last", 0) <= 0 and len(scene_rows) >= 4:
        errors.append("first_last share missing for visual variety")

    mode = str(creative_config.get("route_mix_mode") or "auto")
    duration_sec = float(input_pkg.get("audio_duration_sec") or 0.0)
    feedback_prefix = (
        "short clip default expects mixed route distribution near 25/50/25"
        if mode == "auto" and duration_sec > 0 and duration_sec <= 45
        else "route distribution violated creative_config doctrine"
    )
    feedback = f"{feedback_prefix}; " + "; ".join(errors) if errors else ""
    details = {
        "target_route_mix": target_budget,
        "actual_route_mix": route_counts,
        "max_consecutive_lipsync": max_consecutive,
        "longest_lipsync_streak": longest_lipsync_streak,
        "route_mix_mode": mode,
        "creative_config": creative_config,
    }
    return (len(errors) == 0), feedback, details


def _resolve_audio_semantic_source_type(input_pkg: dict[str, Any]) -> str:
    lyrics_text = str(input_pkg.get("lyrics_text") or "").strip()
    transcript_text = str(input_pkg.get("transcript_text") or "").strip()
    spoken_text_hint = str(input_pkg.get("spoken_text_hint") or "").strip()
    if lyrics_text:
        return "lyric_vocal"
    if transcript_text or spoken_text_hint:
        return "spoken_music"
    return "mixed_voice_music"


def _extract_mime_type(url: str, headers: dict[str, str], data: bytes) -> str:
    header_mime = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    if header_mime.startswith("image/"):
        return header_mime
    guessed_from_url, _ = mimetypes.guess_type(url)
    if guessed_from_url and guessed_from_url.startswith("image/"):
        return guessed_from_url
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _resolve_reference_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        return raw
    if raw.startswith("//"):
        return f"http:{raw}"
    base = str(settings.PUBLIC_BASE_URL).rstrip("/")
    if raw.startswith("/"):
        return f"{base}{raw}"
    return f"{base}/{raw}"


def _extract_local_static_asset_relative_path(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None

    parsed = urllib.parse.urlparse(raw)
    path = raw
    if parsed.scheme in {"http", "https"}:
        host = (parsed.hostname or "").lower()
        local_hosts = {"127.0.0.1", "localhost"}
        public_base = (settings.PUBLIC_BASE_URL or "").strip()
        if public_base:
            try:
                public_host = (urllib.parse.urlparse(public_base).hostname or "").lower()
                if public_host:
                    local_hosts.add(public_host)
            except Exception:
                pass
        if host not in local_hosts:
            return None
        path = parsed.path or ""
    elif raw.startswith("//"):
        return None

    normalized = path.lstrip("/")
    prefix = "static/assets/"
    if not normalized.startswith(prefix):
        return None
    rel_path = normalized[len(prefix) :]
    return rel_path or None


def _read_local_static_asset(url: str) -> tuple[bytes | None, str, str | None]:
    rel_path = _extract_local_static_asset_relative_path(url)
    if not rel_path:
        return None, "", None
    try:
        decoded_rel_path = urllib.parse.unquote(rel_path)
        assets_root = Path(ASSETS_DIR).resolve()
        file_path = (assets_root / decoded_rel_path).resolve()
        if assets_root not in file_path.parents:
            return None, "", "local_asset_not_found"
        if not file_path.exists() or not file_path.is_file():
            return None, "", "local_asset_not_found"
        return file_path.read_bytes(), file_path.as_uri(), None
    except OSError:
        return None, "", "local_asset_read_failed"
    except Exception:
        return None, "", "local_asset_read_failed"


def _load_image_inline_part(url: str) -> tuple[dict[str, Any] | None, str | None]:
    resolved = _resolve_reference_url(url)
    if not resolved:
        return None, "image_download_failed"
    headers: dict[str, str] = {}
    data_source_for_mime = resolved
    local_data, local_source, local_error = _read_local_static_asset(resolved)
    if local_error:
        return None, local_error
    if local_data is not None:
        data = local_data
        data_source_for_mime = local_source
    else:
        req = urllib.request.Request(resolved, headers={"User-Agent": "photostudio-story-core/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
        except urllib.error.HTTPError:
            return None, "image_http_error"
        except (socket.timeout, TimeoutError):
            return None, "image_timeout"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                return None, "image_timeout"
            return None, "image_download_failed"
        except ValueError:
            return None, "image_download_failed"
        except Exception:
            return None, "image_download_failed"

    if not data:
        return None, "image_download_failed"
    if len(data) > MAX_STORY_CORE_IMAGE_BYTES:
        return None, "image_too_large"
    mime_type = _extract_mime_type(data_source_for_mime, headers, data)
    if not mime_type:
        return None, "image_invalid_mime"

    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(data).decode("ascii"),
        }
    }, None


def _pick_story_core_ref_urls_by_role(
    *,
    input_pkg: dict[str, Any],
    refs_inventory: dict[str, Any],
    max_per_role: int = 1,
) -> dict[str, list[dict[str, str]]]:
    selected_refs = _safe_dict(input_pkg.get("selected_refs"))
    refs_by_role = _safe_dict(input_pkg.get("refs_by_role"))
    role_order = ["character_1", "props", "location", "style"]
    per_role: dict[str, list[dict[str, str]]] = {role: [] for role in role_order}
    seen_by_role: dict[str, set[str]] = {role: set() for role in role_order}

    def _add(role: str, url: str, source: str) -> None:
        clean_url = str(url or "").strip()
        if not clean_url or clean_url in seen_by_role[role]:
            return
        if len(per_role[role]) >= max_per_role:
            return
        per_role[role].append({"url": clean_url, "source": source})
        seen_by_role[role].add(clean_url)

    # 1) Explicit selections first.
    _add("character_1", str(selected_refs.get("character_1") or ""), "input.selected_refs.character_1")
    _add("location", str(selected_refs.get("location") or ""), "input.selected_refs.location")
    _add("style", str(selected_refs.get("style") or ""), "input.selected_refs.style")
    for idx, value in enumerate(_safe_list(selected_refs.get("props"))):
        _add("props", str(value or ""), f"input.selected_refs.props[{idx}]")

    # 2) refs_by_role from input.
    for role in role_order:
        for idx, value in enumerate(_safe_list(refs_by_role.get(role))):
            _add(role, str(value or ""), f"input.refs_by_role.{role}[{idx}]")

    # 3) refs_inventory fallback (connected/context refs).
    for role in role_order:
        ref_key = _role_to_ref_key(role)
        for idx, value in enumerate(_extract_ref_urls(refs_inventory.get(ref_key))):
            _add(role, str(value or ""), f"refs_inventory.{ref_key}[{idx}]")

    return per_role


def _build_story_core_inline_ref_parts(
    *,
    input_pkg: dict[str, Any],
    refs_inventory: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    role_order = ["character_1", "props", "location", "style"]
    picked = _pick_story_core_ref_urls_by_role(input_pkg=input_pkg, refs_inventory=refs_inventory, max_per_role=1)
    inline_parts: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        role: {"attached": False, "error": "", "source": "", "url": ""}
        for role in role_order
    }
    attached_roles: list[str] = []

    for role in role_order:
        candidates = _safe_list(picked.get(role))
        if not candidates:
            summary[role] = {"attached": False, "error": "missing", "source": "", "url": ""}
            continue
        chosen = _safe_dict(candidates[0])
        ref_url = str(chosen.get("url") or "").strip()
        ref_source = str(chosen.get("source") or "").strip()
        if not ref_url:
            summary[role] = {"attached": False, "error": "missing", "source": ref_source, "url": ""}
            continue
        inline_part, inline_error = _load_image_inline_part(ref_url)
        if inline_part:
            inline_parts.append(inline_part)
            summary[role] = {"attached": True, "error": "", "source": ref_source, "url": ref_url}
            attached_roles.append(role)
        else:
            summary[role] = {
                "attached": False,
                "error": str(inline_error or "image_attach_failed"),
                "source": ref_source,
                "url": ref_url,
            }

    diagnostics = {
        "story_core_attached_ref_roles": attached_roles,
        "story_core_attached_ref_count": len(attached_roles),
        "story_core_ref_attachment_summary": summary,
        "story_core_character_ref_attached": bool(summary.get("character_1", {}).get("attached")),
        "story_core_character_ref_source": str(summary.get("character_1", {}).get("source") or ""),
        "story_core_character_ref_error": str(summary.get("character_1", {}).get("error") or ""),
        "story_core_props_ref_attached": bool(summary.get("props", {}).get("attached")),
        "story_core_location_ref_attached": bool(summary.get("location", {}).get("attached")),
        "story_core_style_ref_attached": bool(summary.get("style", {}).get("attached")),
    }
    return inline_parts, diagnostics


def _collect_prop_hint_texts(input_pkg: dict[str, Any], refs_inventory: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    props_node = _safe_dict(refs_inventory.get("ref_props"))
    for candidate in (
        props_node.get("value"),
        props_node.get("preview"),
        props_node.get("source_label"),
        _safe_dict(props_node.get("meta")).get("url"),
    ):
        text = str(candidate or "").strip()
        if text:
            hints.append(text)
    for ref in _safe_list(props_node.get("refs")):
        text = str(ref or "").strip()
        if text:
            hints.append(text)
    selected_props = _safe_list(_safe_dict(input_pkg.get("selected_refs")).get("props"))
    for value in selected_props:
        text = str(value or "").strip()
        if text:
            hints.append(text)
    for value in _safe_list(_safe_dict(input_pkg.get("refs_by_role")).get("props")):
        text = str(value or "").strip()
        if text:
            hints.append(text)
    return hints


def _normalize_story_core_prop_contracts(input_pkg: dict[str, Any], refs_inventory: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    hint_texts = _collect_prop_hint_texts(input_pkg, refs_inventory)
    if not hint_texts:
        return [], False
    hint_blob = " ".join(hint_texts).lower()
    has_cap_signal = bool(re.search(r"\b(baseball\s*cap|cap|hat|headwear)\b", hint_blob)) or ("бейсболк" in hint_blob)
    contracts: list[dict[str, Any]] = []
    confusion_guard_applied = False
    if has_cap_signal:
        contracts.append(
            {
                "object_type": "baseball cap",
                "object_label": "baseball cap / бейсболка",
                "usage_mode": "wearable head accessory",
                "category": "clothing/accessory",
                "forbidden_confusions": ["baseball bat", "helmet", "hood", "weapon"],
                "source_hints": hint_texts[:6],
            }
        )
        confusion_guard_applied = True
    else:
        contracts.append(
            {
                "object_type": "grounded prop object",
                "object_label": str(_safe_dict(refs_inventory.get("ref_props")).get("source_label") or "connected props reference").strip(),
                "usage_mode": "follow connected prop role",
                "category": "props",
                "forbidden_confusions": [],
                "source_hints": hint_texts[:6],
            }
        )
    return contracts, confusion_guard_applied


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp.get("candidates"), list) else []
    if not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
    parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            chunks.append(part.get("text") or "")
    return "\n".join(chunks).strip()


def _extract_json_obj(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        first, last = raw.find("{"), raw.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(raw[first : last + 1])
            except Exception:
                return {}
    return {}


def _default_story_core(input_pkg: dict[str, Any]) -> dict[str, Any]:
    source_note = str(
        input_pkg.get("note")
        or input_pkg.get("story_text")
        or input_pkg.get("director_note")
        or input_pkg.get("text")
        or ""
    ).strip()
    source_note = source_note[:800]
    return {
        "story_summary": source_note or "Music-driven visual story with continuity locks.",
        "opening_anchor": "Open with a stable hero/world establishing frame.",
        "ending_callback_rule": "Last beat should echo opening anchor with emotional change.",
        "global_arc": "setup→rise→turn→release→afterimage",
        "identity_lock": {"rule": "Keep hero identity stable across all scenes."},
        "world_lock": {"rule": "Keep world/location logic coherent without random jumps."},
        "style_lock": {"rule": "Keep one cinematic style language across the whole track."},
        "story_guidance": _default_story_core_guidance(),
    }


def _story_text_bundle(input_pkg: dict[str, Any]) -> dict[str, str]:
    return {
        "text": str(input_pkg.get("text") or "").strip(),
        "story_text": str(input_pkg.get("story_text") or "").strip(),
        "note": str(input_pkg.get("note") or "").strip(),
        "director_note": str(input_pkg.get("director_note") or "").strip(),
    }


def _coerce_scene_slots(audio_map: dict[str, Any]) -> list[dict[str, Any]]:
    slots = [row for row in _safe_list(audio_map.get("scene_slots")) if isinstance(row, dict)]
    if slots:
        return slots
    fallback_slots: list[dict[str, Any]] = []
    for idx, row in enumerate(_safe_list(audio_map.get("scene_candidate_windows")), start=1):
        if not isinstance(row, dict):
            continue
        fallback_slots.append(
            {
                "id": str(row.get("id") or f"slot_{idx}"),
                "t0": round(_to_float(row.get("t0"), 0.0), 3),
                "t1": round(_to_float(row.get("t1"), 0.0), 3),
                "duration_sec": round(max(0.0, _to_float(row.get("t1"), 0.0) - _to_float(row.get("t0"), 0.0)), 3),
                "primary_phrase_text": str(row.get("primary_phrase_text") or row.get("label") or "").strip()[:280],
                "audio_features": {},
            }
        )
    return fallback_slots


def _slot_story_function(slot: dict[str, Any], index: int, total: int) -> str:
    energy = _to_float(_safe_dict(slot.get("audio_features")).get("energy_score"), 0.5)
    vocal = _to_float(_safe_dict(slot.get("audio_features")).get("vocal_ratio"), 0.4)
    if total <= 1:
        return "single_arc_beat"
    pos = index / max(1, total - 1)
    if pos <= 0.18:
        return "opening_anchor"
    if pos >= 0.86 and energy <= 0.62:
        return "afterimage_release"
    if energy >= 0.72:
        return "climax_pressure"
    if 0.35 <= pos <= 0.75 and vocal >= 0.52:
        return "narrative_development"
    if 0.35 <= pos <= 0.85:
        return "transition_turn"
    return "build_progression"


def _extract_forbidden_drift(note_text: str) -> list[str]:
    text = str(note_text or "").strip().lower()
    if not text:
        return []
    drift: list[str] = []
    patterns = (
        r"(?:no|avoid|without)\s+([a-z0-9_\- ]{3,36})",
        r"(?:не|без)\s+([a-zа-я0-9_\- ]{3,36})",
    )
    for pattern in patterns:
        for match in re.findall(pattern, text):
            token = str(match or "").strip(" ,.;:!?")
            if token:
                drift.append(f"forbid:{token.replace(' ', '_')[:48]}")
    for known in ("neon", "club", "fantasy", "cyberpunk", "horror", "gore"):
        if re.search(rf"(?:no|avoid|without|не|без)\s+{re.escape(known)}", text):
            drift.append(f"forbid:{known}")
    return list(dict.fromkeys(drift))[:12]


def _ref_record(ref_id: str, row: dict[str, Any]) -> dict[str, str]:
    meta = _normalize_ref_meta(row.get("meta"))
    label = _first_text(row.get("source_label"), row.get("label"), row.get("value"), ref_id)[:120]
    return {
        "ref_id": str(ref_id),
        "label": label,
        "type": str(row.get("type") or row.get("kind") or meta.get("type") or "").strip().lower(),
        "ownership_role": str(meta.get("ownershipRoleMapped") or meta.get("ownershipRole") or "shared"),
        "binding_type": str(meta.get("bindingType") or "nearby"),
    }


def _canonical_subject_id(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    matched = re.search(r"(?:character|char)[_\-\s]*(\d+)", token)
    if matched:
        return f"character_{matched.group(1)}"
    if token in {"main", "lead", "primary"}:
        return "character_1"
    if token in {"support", "secondary"}:
        return "character_2"
    if token in {"antagonist", "villain"}:
        return "character_3"
    return token if re.fullmatch(r"[a-z0-9_]{3,40}", token) else ""


def _is_subject_ref(record: dict[str, str]) -> bool:
    type_token = f"{record.get('type') or ''} {record.get('ref_id') or ''}".lower()
    return any(token in type_token for token in _SUBJECT_REF_TOKENS)


def _is_object_ref(record: dict[str, str], meta: dict[str, Any]) -> bool:
    type_token = f"{record.get('type') or ''} {record.get('ref_id') or ''} {str(meta.get('type') or '')}".lower()
    if any(token in type_token for token in _SUBJECT_REF_TOKENS):
        return False
    return any(token in type_token for token in _OBJECT_REF_TOKENS)


def _build_world_model_normalized(
    *,
    content_type: str,
    text_bundle: dict[str, str],
    opening_anchor: str,
    location_hints: list[str],
    style_hints: list[str],
    forbidden_drift: list[str],
) -> str:
    setting = _first_text(location_hints[0] if location_hints else "", opening_anchor, "grounded_environment")
    mood = _first_text(style_hints[0] if style_hints else "", text_bundle.get("note"), text_bundle.get("director_note"), "coherent_cinematic")
    premise = _first_text(text_bundle.get("story_text"), text_bundle.get("text"), "audio_synchronized_progression")
    premise_tokens = [token.strip(" ,.;:!?") for token in re.split(r"[\n,.;:!?]+", premise) if token.strip()]
    compact_premise = " / ".join(premise_tokens[:2])[:120] or "audio_synchronized_progression"
    drift_guard = ", ".join([token.replace("forbid:", "").replace("_", " ") for token in forbidden_drift[:2]]) or "identity replacement"
    return f"type:{content_type}; setting:{setting[:64]}; mood:{mood[:64]}; premise:{compact_premise}; guard:no {drift_guard}"


def _build_story_core_v11(
    *,
    input_pkg: dict[str, Any],
    audio_map: dict[str, Any],
    refs_inventory: dict[str, Any],
    assigned_roles: dict[str, Any],
    parsed_story_core: dict[str, Any],
    fallback_story_core: dict[str, Any],
) -> dict[str, Any]:
    text_bundle = _story_text_bundle(input_pkg)
    content_type = str(input_pkg.get("content_type") or input_pkg.get("contentType") or "music_video").strip() or "music_video"
    director_mode = _resolve_director_mode(input_pkg.get("director_mode"), content_type=content_type)
    content_format = str(input_pkg.get("format") or input_pkg.get("content_format") or "").strip() or "short_form_video"
    ownership_binding_inventory = [row for row in _safe_list(input_pkg.get("ownership_binding_inventory")) if isinstance(row, dict)]
    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    slots = _coerce_scene_slots(audio_map)
    total_slots = len(slots)

    primary_subject = _canonical_subject_id(assigned_roles.get("primary_role")) or "character_1"
    role_subjects = [
        str(v).strip()
        for v in _safe_list(assigned_roles.get("active_roles"))
        if _canonical_subject_id(v) and _canonical_subject_id(v) != primary_subject
    ]
    connected_subjects = [
        str(v).strip()
        for v in (
            _safe_list(connected_summary.get("support_roles"))
            + _safe_list(connected_summary.get("secondary_subjects"))
            + _safe_list(connected_summary.get("subject_candidates"))
        )
        if _canonical_subject_id(v) and _canonical_subject_id(v) != primary_subject
    ]
    ref_subjects: list[str] = []
    secondary_subject_labels: dict[str, str] = {}
    continuity_objects: list[dict[str, Any]] = []
    for ref_id, value in refs_inventory.items():
        if not isinstance(value, dict):
            continue
        record = _ref_record(str(ref_id), value)
        meta = _normalize_ref_meta(value.get("meta"))
        if _is_subject_ref(record):
            canonical_ref_subject = _canonical_subject_id(record["ref_id"]) or _canonical_subject_id(record["label"])
            if canonical_ref_subject and canonical_ref_subject != primary_subject:
                ref_subjects.append(canonical_ref_subject)
                if record["label"]:
                    secondary_subject_labels[canonical_ref_subject] = record["label"]
        if _is_object_ref(record, meta):
            continuity_objects.append(
                {
                    "ref_id": record["ref_id"],
                    "label": record["label"],
                    "ownership_role": record["ownership_role"],
                    "binding_type": record["binding_type"],
                    "source_label": str(value.get("source_label") or ""),
                    "visibility_expectation": "persistent" if record["binding_type"] in {"worn", "held", "carried"} else "recurring",
                }
            )
    for row in ownership_binding_inventory[:18]:
        ref_id = str(row.get("ref_id") or "").strip()
        label = str(row.get("label") or "").strip()
        if not (ref_id or label):
            continue
        semantic_token = f"{ref_id} {label}".lower()
        if any(token in semantic_token for token in _SUBJECT_REF_TOKENS):
            continue
        if not any(token in semantic_token for token in _OBJECT_REF_TOKENS):
            continue
        continuity_objects.append(
            {
                "ref_id": ref_id,
                "label": label[:120],
                "ownership_role": str(row.get("ownershipRoleMapped") or row.get("ownershipRole") or "shared"),
                "binding_type": str(row.get("bindingType") or "nearby"),
                "source_label": str(row.get("source_label") or ""),
                "visibility_expectation": "persistent" if str(row.get("bindingType") or "").strip().lower() in {"worn", "held", "carried"} else "recurring",
            }
        )
    continuity_objects = list({f"{obj.get('ref_id')}::{obj.get('label')}": obj for obj in continuity_objects if obj.get("label") or obj.get("ref_id")}.values())[:16]

    for candidate in [*role_subjects, *connected_subjects]:
        canonical_candidate = _canonical_subject_id(candidate)
        if canonical_candidate and canonical_candidate != primary_subject:
            ref_subjects.append(canonical_candidate)
            if canonical_candidate not in secondary_subject_labels and str(candidate).strip():
                secondary_subject_labels[canonical_candidate] = str(candidate).strip()[:120]
    secondary_subjects = list(dict.fromkeys(ref_subjects))[:6]
    if not secondary_subjects and continuity_objects:
        mapped_subject = _canonical_subject_id(continuity_objects[0].get("ownership_role"))
        secondary_subjects = [mapped_subject] if mapped_subject and mapped_subject != primary_subject else []

    opening_anchor = _first_text(parsed_story_core.get("opening_anchor"), fallback_story_core.get("opening_anchor"), text_bundle.get("story_text"), text_bundle.get("text"))[:220]
    ending_callback_rule = _first_text(parsed_story_core.get("ending_callback_rule"), fallback_story_core.get("ending_callback_rule"))
    style_rule = _first_text(_safe_dict(parsed_story_core.get("style_lock")).get("rule"), _safe_dict(fallback_story_core.get("style_lock")).get("rule"))
    note_text = " ".join([text_bundle.get("note", ""), text_bundle.get("director_note", "")]).strip()
    forbidden_drift = _extract_forbidden_drift(note_text) or ["forbid:identity_replacement", "forbid:ungrounded_world_jump"]

    beats: list[dict[str, Any]] = []
    slot_groups: list[dict[str, Any]] = []
    group_reason: list[dict[str, Any]] = []
    current_group: list[str] = []
    current_function = ""
    previous_phrase = ""
    repeated_count = 0
    mode_weight = {
        "music_video": {"audio": 1.2, "continuity": 1.0, "causality": 0.9},
        "story": {"audio": 0.9, "continuity": 1.2, "causality": 1.2},
        "film": {"audio": 0.9, "continuity": 1.2, "causality": 1.2},
        "ad": {"audio": 1.0, "continuity": 1.3, "causality": 1.1},
        "news": {"audio": 0.8, "continuity": 1.0, "causality": 1.35},
    }.get(content_type, {"audio": 1.0, "continuity": 1.0, "causality": 1.0})
    has_persistent_objects = any(str(obj.get("visibility_expectation") or "") == "persistent" for obj in continuity_objects)
    for idx, slot in enumerate(slots):
        slot_id = str(slot.get("id") or f"slot_{idx + 1}")
        phrase = str(slot.get("primary_phrase_text") or "").strip()
        fn = _slot_story_function(slot, idx, total_slots)
        phrase_key = re.sub(r"\s+", " ", phrase.lower()).strip()
        energy = _to_float(_safe_dict(slot.get("audio_features")).get("energy_score"), 0.5)
        vocal = _to_float(_safe_dict(slot.get("audio_features")).get("vocal_ratio"), 0.4)
        semantic_density = "high" if (vocal >= 0.55 or len(phrase.split()) >= 8) else ("low" if not phrase else "medium")
        narrative_load_score = (energy * mode_weight["audio"]) + (0.3 if fn in {"transition_turn", "climax_pressure"} else 0.0)
        narrative_load = "high" if narrative_load_score >= 0.9 else ("medium" if narrative_load_score >= 0.6 else "low")
        object_presence_required = bool(continuity_objects) and (
            fn in {"transition_turn", "climax_pressure"} or (has_persistent_objects and narrative_load in {"high", "medium"})
        )
        continuity_pressure = "high" if object_presence_required else "medium"
        primary_shift_allowed = bool(re.search(r"\b(we|they|together|crowd|everyone)\b", phrase_key)) and fn in {"transition_turn", "climax_pressure"}
        beat_primary_subject = primary_subject if not (primary_shift_allowed and secondary_subjects) else secondary_subjects[0]

        if not current_group:
            current_group = [slot_id]
            current_function = fn
        elif fn == current_function and len(current_group) < 3 and phrase_key and phrase_key == previous_phrase and repeated_count < 2:
            current_group.append(slot_id)
            repeated_count += 1
        else:
            group_id = f"group_{len(slot_groups) + 1}"
            slot_groups.append({"group_id": group_id, "slot_ids": list(current_group)})
            group_reason.append({"group_id": group_id, "reason": f"semantic_continuity:{current_function}|phrase_pattern:{'repeat' if repeated_count else 'progression'}"})
            current_group = [slot_id]
            current_function = fn
            repeated_count = 0

        beat_secondary = secondary_subjects[:2] if idx % 2 == 0 else secondary_subjects[1:3] or secondary_subjects[:1]
        beats.append(
            {
                "beat_id": f"beat_{idx + 1}",
                "slot_ids": [slot_id],
                "time_range": {"t0": round(_to_float(slot.get("t0"), 0.0), 3), "t1": round(_to_float(slot.get("t1"), 0.0), 3)},
                "story_function": fn,
                "beat_primary_subject": beat_primary_subject,
                "beat_secondary_subjects": beat_secondary,
                "semantic_density": semantic_density,
                "narrative_load": narrative_load,
                "subject_presence_requirement": "primary_subject_visible_unless_explicit_handoff",
                "continuity_visibility_requirement": "object_anchor_required" if object_presence_required else "world_anchor_or_subject_callback",
                "beat_focus_hint": phrase[:180] or fn,
                "source_slot_id": slot_id,
                "group_reason": f"{fn}|density:{semantic_density}|continuity:{continuity_pressure}",
            }
        )
        previous_phrase = phrase_key
    if current_group:
        group_id = f"group_{len(slot_groups) + 1}"
        slot_groups.append({"group_id": group_id, "slot_ids": list(current_group)})
        group_reason.append({"group_id": group_id, "reason": f"semantic_continuity:{current_function}|phrase_pattern:{'repeat' if repeated_count else 'progression'}"})

    location_hints = [
        _first_text(row.get("source_label"), row.get("value"), ref_id)
        for ref_id, row in refs_inventory.items()
        if isinstance(row, dict) and any(token in str(ref_id).lower() for token in ("location", "place", "env", "bg"))
    ][:4]
    style_hints = [
        _first_text(row.get("source_label"), row.get("value"), ref_id)
        for ref_id, row in refs_inventory.items()
        if isinstance(row, dict) and any(token in str(ref_id).lower() for token in ("style", "look", "mood", "grade"))
    ][:4]
    world_model = _build_world_model_normalized(
        content_type=content_type,
        text_bundle=text_bundle,
        opening_anchor=opening_anchor,
        location_hints=location_hints,
        style_hints=style_hints,
        forbidden_drift=forbidden_drift,
    )[:240]
    world_definition = {
        "world_model": world_model or "grounded_continuity_world",
        "world_axioms": [
            "same_world_family_across_beats",
            "subject_identity_must_stay_consistent",
            "object_bindings_follow_ownership_and_binding_meta",
            "audio_slots_define_temporal_order",
            f"mode_weighting:{content_type}:audio={mode_weight['audio']},continuity={mode_weight['continuity']},causality={mode_weight['causality']}",
        ],
        "environment_anchor": _first_text(location_hints[0] if location_hints else "", opening_anchor, "text_derived_environment_anchor"),
        "style_anchor": _first_text(style_hints[0] if style_hints else "", style_rule, "coherent_cinematic_style"),
        "allowed_variation": ["lighting_shift", "framing_shift", "performance_intensity_shift", "weather_or_time_shift_if_text_supported"],
        "forbidden_drift": forbidden_drift,
        "world_continuity_rules": [
            "location_and_style_changes_require_semantic_bridge",
            "forbidden_drift_tokens_cannot_be_introduced",
            "if_location_refs_missing_use_text_guidance_not_global_arc",
        ],
    }
    primary_spine = f"{primary_subject} carries narrative progression via {_first_text(text_bundle.get('story_text'), text_bundle.get('text'), parsed_story_core.get('story_summary'), fallback_story_core.get('story_summary'))[:120] or 'audio_text progression'}"
    continuity_matrix = {
        "subject_to_objects": [
            {
                "subject": str(item.get("ownership_role") or "shared"),
                "object_label": str(item.get("label") or ""),
                "binding_type": str(item.get("binding_type") or "nearby"),
                "visibility_expectation": str(item.get("visibility_expectation") or "recurring"),
            }
            for item in continuity_objects[:14]
        ]
    }
    transition_events = []
    for i in range(max(0, len(beats) - 1)):
        left, right = beats[i], beats[i + 1]
        evt_type = "semantic_progression"
        if left.get("story_function") != right.get("story_function"):
            evt_type = "function_turn"
        if left.get("beat_primary_subject") != right.get("beat_primary_subject"):
            evt_type = "subject_handoff_explicit"
        transition_events.append(
            {
                "from_beat": left["beat_id"],
                "to_beat": right["beat_id"],
                "type": evt_type,
                "object_carry_required": bool(continuity_objects),
            }
        )

    primary_per_beat = [str(item.get("beat_primary_subject") or "") for item in beats]
    shadow_count = sum(1 for subject in primary_per_beat if subject and subject != primary_subject)
    subject_shadowing = shadow_count > max(1, math.floor(len(beats) * 0.35))
    required_object_beats = [idx for idx, beat in enumerate(beats) if str(beat.get("continuity_visibility_requirement") or "") == "object_anchor_required"]
    required_object_beats_set = set(required_object_beats)
    legitimized_transition_indices = {
        idx + 1
        for idx, event in enumerate(transition_events)
        if str(event.get("type") or "") in {"function_turn", "subject_handoff_explicit"}
    }
    has_long_required_run = False
    run_size = 0
    for idx in range(len(beats)):
        if idx in required_object_beats_set:
            run_size += 1
            has_long_required_run = has_long_required_run or run_size >= 3
        else:
            run_size = 0
    has_unlegitimized_required_run = has_long_required_run and not any(idx in legitimized_transition_indices for idx in required_object_beats)
    continuity_break = bool(
        continuity_objects
        and (
            not continuity_matrix["subject_to_objects"]
            or (has_persistent_objects and not required_object_beats)
            or has_unlegitimized_required_run
        )
    )
    world_anchor_text = " ".join(
        [
            str(world_definition.get("environment_anchor") or ""),
            str(world_definition.get("style_anchor") or ""),
            str(opening_anchor or ""),
        ]
    ).lower()
    world_drift = any(
        token.replace("forbid:", "").replace("_", " ") in world_anchor_text
        for token in forbidden_drift
        if token.startswith("forbid:")
    )
    arc_tokens = [str(item.get("story_function") or "") for item in beats]
    flatline_segments: list[dict[str, Any]] = []
    start = 0
    while start < len(arc_tokens):
        end = start + 1
        while end < len(arc_tokens) and arc_tokens[end] == arc_tokens[start]:
            end += 1
        if (end - start) >= 3:
            flatline_segments.append({"from_beat": f"beat_{start + 1}", "to_beat": f"beat_{end}", "story_function": arc_tokens[start]})
        start = end
    semantic_delta_score = round(1.0 - min(0.7, len(flatline_segments) * 0.18) - (0.15 if subject_shadowing else 0.0) - (0.15 if continuity_break else 0.0), 3)
    opening_mismatch = bool(beats and primary_subject not in str(beats[0].get("beat_primary_subject") or ""))
    dangling_tail = bool(beats and "afterimage" not in str(beats[-1].get("story_function") or "") and "release" not in str(beats[-1].get("story_function") or ""))
    callback_missing = bool(opening_anchor and ending_callback_rule and not beats)
    continuity_object_dropout = bool(continuity_objects and not continuity_matrix["subject_to_objects"])

    story_core_v1 = {
        "schema_version": "core_v1.1",
        "director_mode": director_mode,
        "story_truth_source": "note_refs_primary" if director_mode == "clip" else "mixed_inputs",
        "audio_truth_scope": "timing_plus_emotion" if director_mode == "clip" else "timing_structure",
        "world_definition": world_definition,
        "narrative_backbone": {
            "primary_narrative_spine": primary_spine,
            "secondary_subjects": secondary_subjects,
            "secondary_subject_labels": {key: secondary_subject_labels[key] for key in secondary_subjects if key in secondary_subject_labels},
            "continuity_objects": continuity_objects,
            "continuity_matrix": continuity_matrix,
            "subject_priority_rules": [
                "global_primary_spine_drives_interpretation",
                "beat_primary_subject_cannot_shift_without_text_or_turn_signal",
            ],
            "emotional_voice_rules": ["keep_voice_consistent_with_global_arc", "avoid_unmotivated_tonal_inversion"],
            "subject_transition_rules": ["subject_handoff_allowed_only_on_transition_turn_or_explicit_phrase_signal", "secondary_subject_cannot_dominate_without_story_reason"],
            "object_transition_rules": ["ownership_or_binding_change_must_be_explicit_in_transition_events", "persistent_objects_should_reappear_within_two_beats"],
            "transition_events": transition_events[:16],
        },
        "semantic_arc": {
            "global_intent": str(parsed_story_core.get("story_summary") or fallback_story_core.get("story_summary") or ""),
            "opening_statement": opening_anchor,
            "arc_segments": [item.get("story_function") for item in beats[:8]],
            "turn_points": [item["beat_id"] for item in beats if item.get("story_function") in {"transition_turn", "climax_pressure"}][:4],
            "climax_definition": "max narrative pressure synchronized with high-energy audio slot(s)",
            "ending_resolution": ending_callback_rule,
            "afterimage_rule": "final beat must preserve world identity while reducing pressure",
            "callback_rules": ["ending_echoes_opening_anchor_with_contextual_change"],
        },
        "beat_map": {
            "slot_groups": slot_groups,
            "beats": beats,
            "group_reason": group_reason,
            "beat_primary_subject": {item["beat_id"]: item["beat_primary_subject"] for item in beats},
            "beat_secondary_subjects": {item["beat_id"]: item["beat_secondary_subjects"] for item in beats},
            "story_function": {item["beat_id"]: item["story_function"] for item in beats},
            "semantic_density": {item["beat_id"]: item["semantic_density"] for item in beats},
            "narrative_load": {item["beat_id"]: item["narrative_load"] for item in beats},
            "subject_presence_requirement": "every_beat_requires_subject_visibility_per_priority_rules",
            "continuity_visibility_requirement": "every_beat_requires_world_or_object_continuity_marker",
            "beat_focus_hint": {item["beat_id"]: item["beat_focus_hint"] for item in beats},
        },
        "validation": {
            "validation_flags": {
                "audio_slots_present": bool(slots),
                "beat_slot_binding_valid": all(bool(_safe_list(item.get("slot_ids"))) for item in beats),
                "world_anchor_present": bool(world_definition.get("environment_anchor")),
                "narrative_spine_present": bool(primary_spine.strip()),
                "subject_shadowing": subject_shadowing,
                "continuity_break": continuity_break,
                "world_drift": world_drift,
                "audio_semantic_mismatch": bool(slots and not any(token in arc_tokens for token in ("transition_turn", "climax_pressure", "afterimage_release"))),
                "dangling_tail": dangling_tail,
                "opening_mismatch": opening_mismatch,
                "missing_callback": callback_missing,
                "continuity_object_dropout": continuity_object_dropout,
                "semantic_stagnation_warning": bool(flatline_segments),
            },
            "warnings": (
                [] if slots else ["beat_map_generated_without_scene_slots"]
            )
            + (["subject_shadowing_detected"] if subject_shadowing else [])
            + (["continuity_break_risk"] if continuity_break else [])
            + (["world_drift_risk"] if world_drift else [])
            + (["semantic_flatline_detected"] if flatline_segments else []),
            "consistency_score": round(max(0.0, min(1.0, semantic_delta_score)), 3),
            "semantic_delta_score": semantic_delta_score,
            "arc_flatline_segments": flatline_segments,
            "core_fail_conditions": [
                "missing_audio_map_scene_slots",
                "missing_primary_narrative_spine",
                "empty_world_definition",
            ],
        },
        "prompt_interface_contract": {
            "contract_version": "prompt_interface_v1.1",
            "input_channels": ["world_definition", "narrative_backbone", "semantic_arc", "beat_map"],
            "must_remain_same": ["primary_subject_identity", "world_family", "core_continuity_objects", "slot_timing"],
            "may_vary": ["lighting", "framing", "performance_intensity", "semantic_emphasis_per_beat"],
            "must_be_visible": [primary_subject, *[str(item.get("label") or "") for item in continuity_objects[:2] if str(item.get("label") or "").strip()]],
            "may_be_offscreen": secondary_subjects[:3],
            "continuity_priority": ["subject_identity", "object_binding", "world_anchor"],
            "world_prompt_constraints": world_definition.get("world_continuity_rules", []),
            "identity_prompt_constraints": _safe_list(parsed_story_core.get("identity_lock")) or [_first_text(_safe_dict(parsed_story_core.get("identity_lock")).get("rule"), _safe_dict(fallback_story_core.get("identity_lock")).get("rule"))],
            "object_prompt_constraints": ["respect_ownership_role_mapping", "do_not_drop_persistent_objects_without_transition_event"],
            "forbidden_insertions": ["unreferenced_main_character", "ungrounded_magic_object", "unauthorized_world_reset"],
            "forbidden_style_drift": forbidden_drift,
            "required_callback_elements": [opening_anchor[:120], ending_callback_rule[:120]],
            "beat_focus_hint": {item["beat_id"]: item["beat_focus_hint"] for item in beats},
            "downstream_constraints": {
                "must_preserve_slot_timing": True,
                "must_preserve_subject_priority_rules": True,
                "must_preserve_world_continuity_rules": True,
            },
            "source_text_bundle": text_bundle,
            "content_contract": {"content_type": content_type, "format": content_format},
            "refs_contract": {
                "connected_refs_summary": _safe_dict(input_pkg.get("connected_context_summary")),
                "refs_inventory_keys": sorted([str(key) for key in refs_inventory.keys()])[:40],
            },
        },
    }
    return story_core_v1


def _detect_story_core_mode(input_pkg: dict[str, Any]) -> str:
    narrative_fields = (
        input_pkg.get("text"),
        input_pkg.get("story_text"),
        input_pkg.get("note"),
        input_pkg.get("director_note"),
    )
    has_directive = any(bool(str(value or "").strip()) for value in narrative_fields)
    return "directed" if has_directive else "creative"


def _has_textual_directive(input_pkg: dict[str, Any]) -> bool:
    return _detect_story_core_mode(input_pkg) == "directed"


def _build_audio_dramaturgy_summary(audio_map: dict[str, Any], input_pkg: dict[str, Any], content_type: str) -> dict[str, Any]:
    windows = [row for row in _safe_list(audio_map.get("scene_candidate_windows")) if isinstance(row, dict)]
    low_ids: list[str] = []
    medium_ids: list[str] = []
    high_ids: list[str] = []
    build_ids: list[str] = []
    release_ids: list[str] = []
    tail_ids: list[str] = []
    performance_ids: list[str] = []
    micro_transition_ids: list[str] = []
    observational_ids: list[str] = []

    for idx, row in enumerate(windows, start=1):
        scene_id = str(row.get("id") or f"sc_{idx}").strip()
        if not scene_id:
            continue
        energy = str(row.get("energy") or "").strip().lower()
        function = str(row.get("scene_function") or "").strip().lower()
        t1 = _to_float(row.get("t1"), 0.0)
        duration = max(_coerce_duration_sec(audio_map.get("duration_sec")), 0.001)
        near_tail = t1 >= max(duration - 0.01, duration * 0.84)

        if energy == "low":
            low_ids.append(scene_id)
        elif energy == "high":
            high_ids.append(scene_id)
        else:
            medium_ids.append(scene_id)

        if any(token in function for token in ("build", "rise", "pressure", "escalat")):
            build_ids.append(scene_id)
        if any(token in function for token in ("release", "drop", "resolve", "afterimage", "payoff")):
            release_ids.append(scene_id)
        if near_tail or any(token in function for token in ("tail", "afterimage", "outro", "resolution")):
            tail_ids.append(scene_id)

        if energy == "high" or any(token in function for token in ("peak", "climax", "performance")):
            performance_ids.append(scene_id)
        if any(token in function for token in ("transition", "turn", "reveal", "callback")):
            micro_transition_ids.append(scene_id)
        if energy == "low" or any(token in function for token in ("setup", "observe", "anchor", "breather", "release")):
            observational_ids.append(scene_id)

    dominant_energy = "medium"
    if len(high_ids) > max(len(low_ids), len(medium_ids)):
        dominant_energy = "high"
    elif len(low_ids) > max(len(high_ids), len(medium_ids)):
        dominant_energy = "low"

    return {
        "dramaturgy_source": "audio_primary",
        "audio_drives_dramaturgy": True,
        "content_type": str(content_type or "music_video"),
        "textual_directive_present": bool(_has_textual_directive(input_pkg)),
        "energy_profile": {
            "low": len(low_ids),
            "medium": len(medium_ids),
            "high": len(high_ids),
            "total_windows": len(windows),
        },
        "dominant_energy": dominant_energy,
        "energy_curve_summary": str(audio_map.get("global_arc_hint") or audio_map.get("analysis_mode") or "audio_arc_driven"),
        "peak_window_ids": high_ids[:6],
        "low_energy_window_ids": low_ids[:8],
        "medium_energy_window_ids": medium_ids[:8],
        "high_energy_window_ids": high_ids[:8],
        "build_window_ids": list(dict.fromkeys(build_ids))[:8],
        "release_window_ids": list(dict.fromkeys(release_ids))[:8],
        "tail_resolution_window_ids": list(dict.fromkeys(tail_ids))[:8],
        "performance_candidate_window_ids": list(dict.fromkeys(performance_ids))[:8],
        "micro_transition_candidate_window_ids": list(dict.fromkeys(micro_transition_ids))[:8],
        "observational_candidate_window_ids": list(dict.fromkeys(observational_ids))[:8],
        "suggested_arc": "ground -> build -> peak -> release -> afterimage",
    }


def _build_story_core_rules() -> dict[str, Any]:
    # Headwear rules must remain universal across future modes and subjects; do not overfit to baseball-cap-only scenarios.
    return {
        "audio_priority": {
            "primary_signals": [
                "rhythm_scene_timing_and_cut_structure_from_audio_map",
                "emotional_arc_from_audio_map",
            ],
            "secondary_signal": "lyrics_meaning_optional_only",
            "forbid_lyrics_only_story_derivation": True,
            "fallback_when_lyrics_weak": [
                "rhythm",
                "energy_pattern",
                "repetition_pattern",
                "emotional_contour",
                "user_concept",
            ],
        },
        "ref_usage": {
            "refs_are_cast_and_objects": True,
            "refs_are_world_anchors": True,
            "props_can_drive_dramaturgy": True,
            "forbid_rewriting_face_identity": True,
            "forbid_rewriting_key_prop_identity": True,
        },
        "appearance_policy": {
            "allow_controlled_styling_variation": True,
            "forbid_full_character_reinvention": True,
            "must_keep_face_identity": True,
            "must_keep_key_prop_identity": True,
            "must_keep_core_silhouette": True,
            "headwear_optional_not_mandatory": True,
            "forbid_default_headwear_takeoff_puton_actions": True,
            "forbid_scene_to_scene_full_wardrobe_replacement": True,
            "limit_active_object_complexity_per_scene": True,
        },
        "headwear_hair_compatibility": {
            "headwear_optional_not_mandatory": True,
            "headwear_is_look_component_not_default_action_object": True,
            "hair_must_be_physically_compatible_with_headwear": True,
            "hair_should_remain_natural_and_readable": True,
            "forbid_unnatural_hair_stuffing_under_headwear": True,
            "forbid_incompatible_top_volume_conflicts": True,
            "prefer_believable_arrangements_over_awkward_compression": True,
            "example_believable_arrangements": [
                "long loose hair",
                "long straight hair",
                "low ponytail",
                "tucked side hair when compatible",
            ],
            "examples_are_guidance_not_exhaustive_law": True,
        },
        "world_lock": {
            "respect_user_world_constraints": True,
            "examples": ["no neon", "no club", "realism", "local environment tone"],
            "allow_user_authorized_style_override_only": True,
            "forbid_wholesale_identity_replacement": True,
            "director_note_world_lock_is_hard_constraint": True,
            "forbid_neon_club_warehouse_drift_when_director_note_blocks_it": True,
            "audio_cannot_override_explicit_world_lock": True,
        },
        "core_stage_boundaries": {
            "forbid_route_selection": True,
            "forbid_i2v_ia2v_first_last_selection": True,
            "forbid_final_prompt_writing": True,
            "forbid_final_package_assembly": True,
        },
    }


def _extract_story_user_concept(input_pkg: dict[str, Any]) -> str:
    for key in ("director_note", "note", "story_text", "text"):
        value = str(input_pkg.get(key) or "").strip()
        if value:
            return value[:1400]
    return ""


def _collect_story_core_refs_by_role(
    input_pkg: dict[str, Any],
    refs_inventory: dict[str, Any],
    connected_summary: dict[str, Any],
) -> tuple[dict[str, list[str]], list[str]]:
    normalized: dict[str, list[str]] = {}
    sources_used: set[str] = set()
    refs_by_role = _safe_dict(input_pkg.get("refs_by_role"))
    connected_refs_by_role = _safe_dict(connected_summary.get("refsByRole"))
    connected_refs_present = _safe_dict(connected_summary.get("refsPresentByRole"))
    connected_attached_present = _safe_dict(connected_summary.get("connectedRefsPresentByRole"))

    def _append(role: str, candidate: str, source: str) -> None:
        clean_role = str(role or "").strip()
        clean_url = str(candidate or "").strip()
        if not clean_role or not clean_url:
            return
        bucket = normalized.setdefault(clean_role, [])
        if clean_url not in bucket:
            bucket.append(clean_url)
            sources_used.add(source)

    for role, urls in refs_by_role.items():
        for value in _safe_list(urls):
            _append(str(role), str(value), "refs_by_role")
    for role, urls in connected_refs_by_role.items():
        for value in _safe_list(urls):
            _append(str(role), str(value), "connected_refs_by_role")
    for role, urls in connected_refs_present.items():
        for value in _safe_list(urls):
            _append(str(role), str(value), "refs_present_by_role")
    for role, urls in connected_attached_present.items():
        for value in _safe_list(urls):
            _append(str(role), str(value), "connected_refs_present_by_role")
    for role in ("character_1", "character_2", "character_3", "props", "location", "style", "animal", "group"):
        ref_key = _role_to_ref_key(role)
        for value in _extract_ref_urls(refs_inventory.get(ref_key)):
            _append(role, value, "refs_inventory")
        selected_refs = _safe_dict(input_pkg.get("selected_refs"))
        selected_value = selected_refs.get(role)
        if isinstance(selected_value, list):
            for value in _safe_list(selected_value):
                _append(role, str(value), "selected_refs")
        else:
            _append(role, str(selected_value or ""), "selected_refs")
        if bool(connected_refs_present.get(role)) and role not in normalized:
            normalized[role] = []
        if bool(connected_attached_present.get(role)) and role not in normalized:
            normalized[role] = []
    return normalized, sorted(sources_used)


def _build_story_core_compact_refs_manifest(
    *,
    refs_by_role: dict[str, list[str]],
    ref_attachment_summary: dict[str, Any],
    connected_summary: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    attached = _safe_dict(ref_attachment_summary)
    connected_present = _safe_dict(connected_summary.get("refsPresentByRole"))
    connected_attached_present = _safe_dict(connected_summary.get("connectedRefsPresentByRole"))
    connected_role_ids = [str(role).strip() for role in _safe_list(connected_summary.get("connectedRoleIds")) if str(role).strip()]
    ordered_roles = [
        "character_1", "character_2", "character_3", "props", "location", "style", "animal", "group",
    ]
    role_set: set[str] = set(connected_role_ids)
    role_set.update(str(role).strip() for role in refs_by_role.keys() if str(role).strip())
    for role in ordered_roles:
        if bool(connected_present.get(role)) or bool(connected_attached_present.get(role)):
            role_set.add(role)
    available_roles = sorted(role_set)
    label_map = {
        "character_1": "Character 1",
        "character_2": "Character 2",
        "character_3": "Character 3",
        "props": "Props",
        "location": "Location",
        "style": "Style",
        "animal": "Animal",
        "group": "Group",
    }
    attached_refs: dict[str, Any] = {}
    attached_roles: list[str] = []
    for role in available_roles:
        urls = _safe_list(refs_by_role.get(role))
        attachment_row = _safe_dict(attached.get(role))
        is_attached = bool(attachment_row.get("attached")) or bool(urls) or bool(connected_attached_present.get(role))
        if is_attached:
            attached_roles.append(role)
        attached_refs[role] = {
            "count": len(urls) or int(attachment_row.get("count") or 0) or (1 if bool(connected_attached_present.get(role)) else 0),
            "attached": is_attached,
            "label": label_map.get(role) or role.replace("_", " ").title(),
        }
    return {"available_roles": available_roles, "attached_refs": attached_refs}, attached_roles


def _extract_director_world_lock_summary(input_pkg: dict[str, Any], story_user_concept: str) -> str:
    user_text = " ".join(
        [
            str(input_pkg.get("director_note") or "").strip(),
            str(input_pkg.get("note") or "").strip(),
            str(input_pkg.get("story_text") or "").strip(),
            str(input_pkg.get("text") or "").strip(),
            str(story_user_concept or "").strip(),
        ]
    ).strip().lower()
    if not user_text:
        return ""
    markers = []
    for token in ("not club", "не клуб", "not neon", "не неон", "not warehouse", "не warehouse", "realistic"):
        if token in user_text:
            markers.append(token)
    return ", ".join(markers[:8])


def _build_story_core_input_context(
    *,
    input_pkg: dict[str, Any],
    audio_map: dict[str, Any],
    refs_inventory: dict[str, Any],
    prop_contracts: list[dict[str, Any]],
    ref_attachment_summary: dict[str, Any],
    grounding_level: str,
) -> dict[str, Any]:
    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    normalized_refs_by_role, refs_sources_used = _collect_story_core_refs_by_role(input_pkg, refs_inventory, connected_summary)
    compact_refs_manifest, attached_ref_roles = _build_story_core_compact_refs_manifest(
        refs_by_role=normalized_refs_by_role,
        ref_attachment_summary=ref_attachment_summary,
        connected_summary=connected_summary,
    )
    story_user_concept = _extract_story_user_concept(input_pkg)
    model_id = _resolve_active_video_model_id(input_pkg)
    i2v_profile = get_video_model_capability_profile(model_id, "i2v")
    audio_dramaturgy = _safe_dict(audio_map.get("audio_dramaturgy"))
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    scene_windows = _safe_list(audio_map.get("scene_candidate_windows"))
    total_scene_windows = len(scene_windows)
    default_route_budget = _compute_route_budget_for_total(total_scene_windows, creative_config)
    audio_summary = {
        "duration_sec": _to_float(audio_map.get("duration_sec"), 0.0),
        "analysis_mode": str(audio_map.get("analysis_mode") or ""),
        "global_arc_hint": str(audio_map.get("global_arc_hint") or ""),
        "energy_curve_summary": str(audio_dramaturgy.get("energy_curve_summary") or ""),
        "dominant_energy": str(audio_dramaturgy.get("dominant_energy") or ""),
        "window_counts": _safe_dict(audio_dramaturgy.get("window_counts")),
        "scene_candidate_window_count": total_scene_windows,
    }
    director_world_lock_summary = _extract_director_world_lock_summary(input_pkg, story_user_concept)
    connected_role_summary = {
        "connectedRoleIds": [str(role).strip() for role in _safe_list(connected_summary.get("connectedRoleIds")) if str(role).strip()],
        "refsPresentByRole": _safe_dict(connected_summary.get("refsPresentByRole")),
        "connectedRefsPresentByRole": _safe_dict(connected_summary.get("connectedRefsPresentByRole")),
    }
    compact_context = {
        "director_note": str(input_pkg.get("director_note") or "").strip(),
        "story_text": str(input_pkg.get("story_text") or "").strip(),
        "note": str(input_pkg.get("note") or "").strip(),
        "user_concept": story_user_concept,
        "audio_summary": audio_summary,
        "creative_config": creative_config,
        "route_mix_doctrine_seed": {
            "target_route_mix": default_route_budget,
            "target_route_ratios": {
                "ia2v": float(creative_config.get("lipsync_ratio") or 0.25),
                "i2v": float(creative_config.get("i2v_ratio") or 0.5),
                "first_last": float(creative_config.get("first_last_ratio") or 0.25),
            },
            "max_consecutive_lipsync": int(creative_config.get("max_consecutive_lipsync") or 2),
            "policy_note": "CORE writes doctrine only. SCENES assigns concrete route per segment.",
        },
        "connected_role_summary": connected_role_summary,
        "compact_refs_manifest": compact_refs_manifest,
        "assigned_roles": _safe_dict(input_pkg.get("assigned_roles_override")),
        "story_core_prop_contracts": prop_contracts,
        "ownership_binding_inventory": _safe_list(input_pkg.get("ownership_binding_inventory")),
        "world_style_identity_constraints": {
            "director_world_lock_summary": director_world_lock_summary,
            "grounding_level": grounding_level,
        },
        "video_capability_canon": {
            "model_id": model_id,
            "capability_rules_source_version": get_capability_rules_source_version(),
            "story_core_planning_bounds": {
                "verified_safe": _safe_list(i2v_profile.get("verified_safe")),
                "experimental": _safe_list(i2v_profile.get("experimental")),
                "blocked": _safe_list(i2v_profile.get("blocked")),
                "policy": {
                    "verified_safe_can_drive_default_planning": True,
                    "experimental_is_opt_in_not_default": True,
                    "blocked_must_not_be_normalized": True,
                },
            },
        },
        "story_rules": _build_story_core_rules(),
    }
    return {
        **compact_context,
        "available_roles": _safe_list(compact_refs_manifest.get("available_roles")),
        "refs_by_role": normalized_refs_by_role,
        "story_core_attached_ref_roles": attached_ref_roles,
        "story_core_director_world_lock_summary": director_world_lock_summary,
        "story_core_compact_context_size_estimate": len(json.dumps(_compact_prompt_payload(compact_context), ensure_ascii=False)),
        "story_core_refs_sources_used": refs_sources_used,
    }


def _default_story_core_guidance() -> dict[str, Any]:
    return {
        "route_mix_doctrine_for_scenes": {
            "core_scope_only": "doctrine_not_segment_assignment",
            "short_clip_default_target_ratios": {"ia2v": 0.25, "i2v": 0.5, "first_last": 0.25},
            "lipsync_candidate_is_permission_not_obligation": True,
            "avoid_long_consecutive_lipsync_streaks": True,
            "prioritize_lipsync_for_strong_performance_windows": True,
        },
        "world_progression_hints": [
            "single coherent world; world/location specifics must come from current text, refs, props, and already established context",
            "preserve identity, world, and style continuity across the full clip",
            "allow clip progression through performance intensity, framing variation, spatial variation inside one world family, emotional modulation, and camera intimacy",
            "do not introduce concrete setting lore when current input does not provide it",
        ],
        "viewer_contrast_rules": [
            "wide_framing_vs_close_intimacy",
            "held_stillness_vs_dynamic_motion",
            "environment_context_vs_subject_micro_expression",
            "measured_rhythm_vs_peak_intensity",
        ],
        "unexpected_realistic_beats": [
            "small environmental motion that supports realism",
            "brief framing reset that keeps subject/world continuity",
            "short pause or breath beat before renewed performance intensity",
            "camera distance variation without changing world family",
        ],
        "prop_guidance": {
            "continuity_object_role": "continuity/reference anchor",
            "carried_object_role": "owner-linked continuity/reference anchor when present",
            "binding_grammar": {
                "carried": "owner-bound continuity object; may affect visibility, handling, silhouette, and pose constraints when relevant",
                "held": "owner hand-occupied continuity object; visible handling possible without finger micro-choreography",
                "worn": "silhouette/look continuity anchor; not a default action driver",
                "pocketed": "owner-linked continuity object; optional visual emphasis",
                "nearby": "owner-adjacent continuity object; within reach when scene logic allows",
                "environment": "world-anchored scene element; not owner-locked prop",
            },
            "ownership_grammar": {
                "character_1": "primary-role owned object",
                "character_2": "support-role owned object",
                "character_3": "antagonist-role owned object",
                "shared": "multi-role shared object",
                "environment": "world/environment object",
            },
            "must_keep_same_object_identity_across_clip": True,
            "forbid_random_object_identity_changes": True,
            "object_is_functional_not_symbolic_decoration_by_default": True,
            "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present": True,
            "forbid_overused_hand_choreography_around_object": True,
            "forbid_magic_or_metaphor_prop_behavior": True,
        },
        "narrative_pressure_rules": [
            "primary narrative role remains the action spine unless current input explicitly reassigns it",
            "secondary performance role must not steal narrative spine unless current input explicitly asks for co-lead behavior",
            "do not invent extra characters unless grounded by current input, refs, or planner output",
            "do not impose ungrounded high-stakes narrative pressure unless current input explicitly supports it",
            "avoid repetitive scene function without meaningful variation",
            "meaningful variation may come from framing, emotional intensity, performance mode, spatial relation, camera distance, or local world detail",
        ],
        "world_richness_rules": [
            "grounded realism only; no spectacle-first escalation",
            "one coherent world logic across clip",
            "restrained motion scale; avoid complex hand/cloth gimmicks",
            "support performance roles may appear only when grounded by current input and refs while staying in the same world family",
            "do not inject hardcoded genre/location restrictions in fallback guidance",
        ],
    }


def _normalize_story_core_guidance(raw_guidance: Any) -> dict[str, Any]:
    fallback = _default_story_core_guidance()
    row = _safe_dict(raw_guidance)
    prop_guidance = _safe_dict(row.get("prop_guidance"))
    fallback_prop_guidance = _safe_dict(fallback.get("prop_guidance"))
    return {
        "route_mix_doctrine_for_scenes": _safe_dict(row.get("route_mix_doctrine_for_scenes"))
        or _safe_dict(fallback.get("route_mix_doctrine_for_scenes")),
        "world_progression_hints": [str(item).strip() for item in _safe_list(row.get("world_progression_hints")) if str(item).strip()]
        or _safe_list(fallback.get("world_progression_hints")),
        "viewer_contrast_rules": [str(item).strip() for item in _safe_list(row.get("viewer_contrast_rules")) if str(item).strip()]
        or _safe_list(fallback.get("viewer_contrast_rules")),
        "unexpected_realistic_beats": [str(item).strip() for item in _safe_list(row.get("unexpected_realistic_beats")) if str(item).strip()]
        or _safe_list(fallback.get("unexpected_realistic_beats")),
        "prop_guidance": {
            "continuity_object_role": str(
                prop_guidance.get("continuity_object_role")
                or fallback_prop_guidance.get("continuity_object_role")
                or ""
            ).strip(),
            "carried_object_role": str(
                prop_guidance.get("carried_object_role")
                or fallback_prop_guidance.get("carried_object_role")
                or ""
            ).strip(),
            "binding_grammar": _safe_dict(prop_guidance.get("binding_grammar")) or _safe_dict(fallback_prop_guidance.get("binding_grammar")),
            "ownership_grammar": _safe_dict(prop_guidance.get("ownership_grammar")) or _safe_dict(fallback_prop_guidance.get("ownership_grammar")),
            "must_keep_same_object_identity_across_clip": bool(
                prop_guidance.get("must_keep_same_object_identity_across_clip")
                if "must_keep_same_object_identity_across_clip" in prop_guidance
                else fallback_prop_guidance.get("must_keep_same_object_identity_across_clip")
            ),
            "forbid_random_object_identity_changes": bool(
                prop_guidance.get("forbid_random_object_identity_changes")
                if "forbid_random_object_identity_changes" in prop_guidance
                else fallback_prop_guidance.get("forbid_random_object_identity_changes")
            ),
            "object_is_functional_not_symbolic_decoration_by_default": bool(
                prop_guidance.get("object_is_functional_not_symbolic_decoration_by_default")
                if "object_is_functional_not_symbolic_decoration_by_default" in prop_guidance
                else fallback_prop_guidance.get("object_is_functional_not_symbolic_decoration_by_default")
            ),
            "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present": bool(
                prop_guidance.get("carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present")
                if "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present" in prop_guidance
                else (
                    prop_guidance.get("carried_object_affects_posture_speed_balance_concealment_route_choice_body_tension_if_present")
                    if "carried_object_affects_posture_speed_balance_concealment_route_choice_body_tension_if_present" in prop_guidance
                    else (
                        fallback_prop_guidance.get(
                            "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present"
                        )
                        if "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present"
                        in fallback_prop_guidance
                        else fallback_prop_guidance.get(
                            "carried_object_affects_posture_speed_balance_concealment_route_choice_body_tension_if_present"
                        )
                    )
                )
            ),
            "forbid_overused_hand_choreography_around_object": bool(
                prop_guidance.get("forbid_overused_hand_choreography_around_object")
                if "forbid_overused_hand_choreography_around_object" in prop_guidance
                else fallback_prop_guidance.get("forbid_overused_hand_choreography_around_object")
            ),
            "forbid_magic_or_metaphor_prop_behavior": bool(
                prop_guidance.get("forbid_magic_or_metaphor_prop_behavior")
                if "forbid_magic_or_metaphor_prop_behavior" in prop_guidance
                else fallback_prop_guidance.get("forbid_magic_or_metaphor_prop_behavior")
            ),
        },
        "narrative_pressure_rules": [str(item).strip() for item in _safe_list(row.get("narrative_pressure_rules")) if str(item).strip()]
        or _safe_list(fallback.get("narrative_pressure_rules")),
        "world_richness_rules": [str(item).strip() for item in _safe_list(row.get("world_richness_rules")) if str(item).strip()]
        or _safe_list(fallback.get("world_richness_rules")),
    }


def _build_story_core_prompt(
    core_input_context: dict[str, Any],
    assigned_roles: dict[str, Any],
    story_core_mode: str,
    capability_bounds_text: str,
) -> str:
    compact_input = _compact_prompt_payload(core_input_context)
    compact_assigned_roles = _compact_prompt_payload(assigned_roles)
    mode = "directed" if story_core_mode == "directed" else "creative"
    mode_instructions = (
        "MODE: DIRECTED MODE\n"
        "- User text is narrative/directorial source of meaning.\n"
        "- Do NOT replace user plot with a different premise.\n"
        "- Preserve explicitly specified world, actions, relationships, and narrative direction.\n"
        "- Audio map still controls pacing, escalation, release pattern, and scene behavior rhythm.\n"
        "- Do NOT let text fully override audio-driven temporal behavior.\n"
    )
    if mode == "creative":
        mode_instructions = (
            "MODE: CREATIVE MODE\n"
            "- User did not provide narrative directive text.\n"
            "- Audio energy is the default dramaturgic driver.\n"
            "- Clip mode is visual/emotional arc, not literal travel-story by default.\n"
            "- Build compact emotional/visual arc from energy, phrasing, escalation, release, afterimage.\n"
            "- Prefer one coherent world anchor with escalating emotional and physical intensity.\n"
            "- Progress via framing intimacy, camera relationship, pressure/release, and performance openness.\n"
            "- Do NOT invent multi-location travel plot or literal geography chain unless refs explicitly demand it.\n"
            "- Do NOT expand a single locked environment into city/alley/courtyard/market progression from nowhere.\n"
            "- Keep clip premise cinematic but concise, not over-plotted.\n"
        )
    else:
        mode_instructions += (
            "- Keep story core compact and clip-like, not screenplay-like.\n"
            "- Avoid unnecessary geography expansion beyond user directive and refs.\n"
        )
    return (
        "You are STORY CORE stage of a scenario pipeline.\n"
        "Return STRICT JSON only, no markdown.\n"
        "story_core is source of truth for arc/identity/world/style/scenario layer.\n"
        "Write narrative/scenario from track timing + refs cast + optional user concept.\n"
        "Use refs as actors/objects anchors, not decorative prose inspiration.\n"
        "If a character image reference is attached, treat it as the source of truth for hero appearance and gender presentation.\n"
        "Connected prop refs are source-of-truth for object identity and object category.\n"
        "Do not replace a referenced prop with a semantically related but different object.\n"
        "Clothing/accessory props must stay clothing/accessory props.\n"
        "Do not reinterpret wearable objects as weapons/tools unless explicitly stated in user text.\n"
        "If prop is casual headwear (cap/hat/hood/scarf/other worn headwear), it must remain headwear.\n"
        "Do not reinterpret headwear as weapon/tool unless explicitly stated in user text (example: baseball cap is not baseball bat).\n"
        "Treat worn headwear as part of look continuity/silhouette, not default action object.\n"
        "Ensure hair compatibility with headwear silhouette; preserve natural, readable hair behavior.\n"
        "Avoid unnatural hair stuffing/compression under worn headwear and avoid incompatible top-volume conflicts.\n"
        "If character ref attachment failed, keep character visuals conservative: keep only reliable role/gender-energy hints and avoid specific visual identity claims.\n"
        "At CORE stage do not inject arbitrary accent colors or symbolic props not grounded in refs/audio/text.\n"
        "Do not output route planning (no i2v/ia2v/first_last), no final prompts, no final package assembly.\n"
        "Do not invent a contradictory hero identity against the attached character image reference.\n"
        "Use the character image reference to infer hero gender presentation (male/female/androgynous), approximate age, visual mood, and core appearance markers.\n"
        "Keep appearance notes compact and production-usable; do not describe every tiny detail, only stable identity-relevant ones.\n"
        "Audio must be primary dramaturgic driver; lyrics are secondary optional signal.\n"
        f"Video model capability bounds (must obey): {capability_bounds_text}\n"
        "HARD CONTRACT: Director note/user concept world constraints are NON-NEGOTIABLE and override generic music-video tropes.\n"
        "HARD CONTRACT: If note says no club/no neon/no warehouse and requests realistic grounded environment, NEVER switch to neon/club/warehouse aesthetics.\n"
        "HARD CONTRACT: Audio affects rhythm/emotional arc/pacing only; audio cannot rewrite locked world constraints.\n"
        "If lyrics are weak/repetitive, rely on rhythm/energy/repetition/emotional contour and user concept.\n"
        "Scenes must be distinct even for repeating musical structures (action/space/shot scale/angle/world relation/prop relation/emotional evolution).\n"
        "Narrative spine must stay with the designated primary narrative role; secondary performance roles must remain support unless current input explicitly sets co-lead behavior.\n"
        "Use ownership_binding_inventory as active planning grammar (main/support/antagonist/shared/world x carried/worn/held/pocketed/nearby/environment).\n"
        "Owner-bound carried/held objects should influence continuity pressure and movement behavior without forced tiny hand choreography.\n"
        "Do not randomly detach owner-bound carried/held objects from their owner unless context explicitly motivates it.\n"
        "Performance peaks must remain grounded in the same realistic world family and must not switch clip logic into concert/pop-video mode unless explicitly requested.\n"
        "In story_guidance include route_mix_doctrine_for_scenes as global doctrine only (distribution intent, anti-streak limits, priority windows), not per-scene route assignment.\n"
        "Required top-level keys only: story_summary, opening_anchor, ending_callback_rule, global_arc, identity_lock, world_lock, style_lock, story_guidance.\n"
        "identity_lock/world_lock/style_lock must be JSON objects.\n\n"
        "story_guidance must be JSON object with keys:\n"
        "route_mix_doctrine_for_scenes, world_progression_hints, viewer_contrast_rules, unexpected_realistic_beats, prop_guidance, narrative_pressure_rules, world_richness_rules.\n"
        "Do NOT output scene rows, per-scene actions, or explicit scene-by-scene choreography in story_core.\n"
        f"{mode_instructions}\n"
        f"story_core_mode={mode}\n\n"
        f"CORE_INPUT_CONTEXT:\n{json.dumps(compact_input, ensure_ascii=False)[:4200]}\n\n"
        f"ASSIGNED_ROLES:\n{json.dumps(compact_assigned_roles, ensure_ascii=False)[:1200]}\n\n"
    )


def _is_usable_story_core(story_core: dict[str, Any]) -> bool:
    if not isinstance(story_core, dict):
        return False
    return all(
        bool(str(story_core.get(key) or "").strip())
        for key in ("story_summary", "opening_anchor", "ending_callback_rule")
    )


def create_storyboard_package(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    req = _safe_dict(payload)
    metadata = _safe_dict(req.get("metadata"))
    refs_inventory, ownership_binding_inventory = _normalize_refs_inventory(_safe_dict(req.get("context_refs")))
    source = _safe_dict(req.get("source"))
    source_mode = str(source.get("source_mode") or source.get("sourceMode") or "").strip().lower()
    source_text = str(source.get("source_value") or source.get("sourceValue") or "").strip() if source_mode == "text" else ""
    text_in = _safe_dict(refs_inventory.get("text_in"))
    connected_text = str(text_in.get("value") or "").strip()
    local_text = str(req.get("directorNote") or req.get("director_note") or req.get("note") or "").strip()
    primary_narrative_text = connected_text or local_text or source_text
    base_input = {
        "text": str(req.get("text") or "").strip() or primary_narrative_text,
        "story_text": str(req.get("storyText") or req.get("story_text") or "").strip() or primary_narrative_text,
        "note": str(req.get("note") or "").strip() or primary_narrative_text,
        "director_note": str(req.get("directorNote") or req.get("director_note") or "").strip() or primary_narrative_text,
        "source": _safe_dict(req.get("source")),
        "audio_url": str(req.get("audioUrl") or "").strip(),
        "audio_duration_sec": float(req.get("audioDurationSec") or 0.0),
        "transcript_text": str(req.get("transcriptText") or req.get("transcript") or "").strip(),
        "lyrics_text": str(req.get("lyricsText") or req.get("lyrics") or "").strip(),
        "spoken_text_hint": str(req.get("spokenTextHint") or "").strip(),
        "content_type": str(_safe_dict(req.get("director_controls")).get("contentType") or "music_video"),
        "director_mode": _resolve_director_mode(
            req.get("director_mode") or metadata.get("director_mode"),
            content_type=str(_safe_dict(req.get("director_controls")).get("contentType") or "music_video"),
        ),
        "format": str(_safe_dict(req.get("director_controls")).get("format") or req.get("format") or "9:16"),
        "creative_config": _normalize_creative_config(
            req.get("creative_config")
            or _safe_dict(req.get("director_controls")).get("creative_config")
            or _safe_dict(req.get("metadata")).get("creative_config")
        ),
        "connected_context_summary": _safe_dict(req.get("connected_context_summary")),
        "refs_by_role": _safe_dict(req.get("refsByRole")),
        "selected_refs": {
            "character_1": str(req.get("selectedCharacterRefUrl") or "").strip(),
            "style": str(req.get("selectedStyleRefUrl") or "").strip(),
            "location": str(req.get("selectedLocationRefUrl") or "").strip(),
            "props": [str(item).strip() for item in _safe_list(req.get("selectedPropsRefUrls")) if str(item).strip()],
        },
        "ownership_binding_inventory": ownership_binding_inventory,
    }
    normalized_input = _normalize_input_audio_source(base_input, refs_inventory)
    stages = {
        stage_id: {
            "status": "idle",
            "updated_at": "",
            "error": "",
            "run_count": 0,
        }
        for stage_id in STAGE_IDS
    }
    return {
        "package_version": "scenario_stage_pipeline_v1",
        "pipeline_mode": str(metadata.get("pipelineMode") or "scenario_stage_v1"),
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
        "input": normalized_input,
        "refs_inventory": refs_inventory,
        "assigned_roles": _safe_dict(req.get("roleTypeByRole")),
        "story_core": {},
        "audio_map": {},
        "role_plan": {},
        "scene_plan": {"scenes": []},
        "scene_prompts": {"scenes": []},
        "final_video_prompt": {"scenes": []},
        "final_storyboard": {"scenes": []},
        "diagnostics": {
            "warnings": [],
            "events": [],
            "errors": [],
            "stale_reason": "",
            "last_action": "",
            "story_core_mode": "creative",
            "story_core_used_fallback": False,
            "story_core_character_ref_attached": False,
            "story_core_character_ref_source": "",
            "story_core_character_ref_error": "",
            "story_core_prop_contracts": [],
            "story_core_prop_confusion_guard_applied": False,
            "story_core_ref_attachment_summary": {},
            "story_core_grounding_level": "standard",
        },
        "stage_statuses": stages,
        "contracts": {
            "entities": {
                "character": {"views": []},
                "location": {"views": []},
                "prop": {"views": []},
            },
            "scene": {
                "required": ["camera_angle", "shot_size", "subject_facing"],
            },
        },
    }


def pick_best_view_for_scene(entity: dict[str, Any] | None, scene_context: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate = _safe_dict(entity)
    views = _safe_list(candidate.get("views"))
    if not views:
        return {}
    # Hook for future ranking by scene_context.
    return views[0] if isinstance(views[0], dict) else {"id": str(views[0])}


def mark_stale_downstream(package: dict[str, Any], from_stage_id: str, reason: str = "") -> dict[str, Any]:
    pkg = deepcopy(_safe_dict(package))
    queue = list(DOWNSTREAM_BY_STAGE.get(from_stage_id, []))
    visited: set[str] = set()
    statuses = _safe_dict(pkg.get("stage_statuses"))
    while queue:
        stage_id = queue.pop(0)
        if stage_id in visited:
            continue
        visited.add(stage_id)
        stage_state = _safe_dict(statuses.get(stage_id))
        stage_state["status"] = "stale"
        stage_state["updated_at"] = _utc_iso()
        statuses[stage_id] = stage_state
        queue.extend(DOWNSTREAM_BY_STAGE.get(stage_id, []))
    pkg["stage_statuses"] = statuses
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    diagnostics["stale_reason"] = str(reason or "manual_input_changed")
    pkg["diagnostics"] = diagnostics
    pkg["updated_at"] = _utc_iso()
    return pkg


def _clear_stage_diagnostics(diagnostics: dict[str, Any], stage_id: str) -> dict[str, Any]:
    prefixes = STAGE_DIAGNOSTIC_PREFIXES.get(stage_id, ())
    if not prefixes:
        return diagnostics
    next_diagnostics = dict(diagnostics)
    for key in list(next_diagnostics.keys()):
        if any(str(key).startswith(prefix) for prefix in prefixes):
            next_diagnostics.pop(key, None)
    return next_diagnostics


def invalidate_downstream_stages(package: dict[str, Any], from_stage_id: str, reason: str = "") -> dict[str, Any]:
    pkg = deepcopy(_safe_dict(package))
    downstream = MANUAL_RESET_DOWNSTREAM.get(from_stage_id, [])
    statuses = _safe_dict(pkg.get("stage_statuses"))
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    for stage_id in downstream:
        if stage_id == "finalize":
            pkg["final_storyboard"] = {"scenes": []}
        elif stage_id in STAGE_SECTION_RESETTERS:
            section_name = "final_storyboard" if stage_id == "finalize" else stage_id
            pkg[section_name] = STAGE_SECTION_RESETTERS[stage_id]()
        stage_state = _safe_dict(statuses.get(stage_id))
        stage_state["status"] = "stale"
        stage_state["updated_at"] = _utc_iso()
        stage_state["error"] = ""
        statuses[stage_id] = stage_state
        diagnostics = _clear_stage_diagnostics(diagnostics, stage_id)
    pkg["stage_statuses"] = statuses
    diagnostics["stale_reason"] = str(reason or f"rerun:{from_stage_id}")
    pkg["diagnostics"] = diagnostics
    pkg["updated_at"] = _utc_iso()
    return pkg


def _set_stage_status(package: dict[str, Any], stage_id: str, status: str, *, error: str = "") -> None:
    statuses = _safe_dict(package.get("stage_statuses"))
    stage_state = _safe_dict(statuses.get(stage_id))
    stage_state["status"] = status
    stage_state["updated_at"] = _utc_iso()
    stage_state["error"] = str(error or "")
    if status in {"done", "error"}:
        stage_state["run_count"] = int(stage_state.get("run_count") or 0) + 1
    statuses[stage_id] = stage_state
    package["stage_statuses"] = statuses


def _append_diag_event(package: dict[str, Any], message: str, *, stage_id: str = "") -> None:
    diagnostics = _safe_dict(package.get("diagnostics"))
    events = _safe_list(diagnostics.get("events"))
    events.append({"at": _utc_iso(), "stage_id": stage_id, "message": message})
    diagnostics["events"] = events[-80:]
    package["diagnostics"] = diagnostics


def _estimate_json_size_bytes(value: Any) -> int:
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        return len(payload.encode("utf-8"))
    except Exception:
        return 0


def build_runtime_diagnostics_summary(
    package: dict[str, Any],
    *,
    current_stage: str = "",
    include_debug: bool = False,
) -> dict[str, Any]:
    pkg = _safe_dict(package)
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    warnings = _safe_list(diagnostics.get("warnings"))
    validation_error = _first_text(
        diagnostics.get(f"{current_stage}_validation_error") if current_stage else "",
        diagnostics.get("validation_error"),
    )
    used_model = _first_text(
        diagnostics.get(f"{current_stage}_used_model") if current_stage else "",
        diagnostics.get("scene_prompts_used_model"),
        diagnostics.get("scene_plan_used_model"),
        diagnostics.get("role_plan_used_model"),
        diagnostics.get("story_core_used_model"),
    )
    summary: dict[str, Any] = {
        "current_stage": str(current_stage or ""),
        "used_model": used_model,
        "warnings_count": len(warnings),
        "validation_error": str(validation_error or ""),
    }
    if include_debug:
        section_sizes = {
            "storyboardPackage": _estimate_json_size_bytes(pkg),
            "final_storyboard": _estimate_json_size_bytes(_safe_dict(pkg.get("final_storyboard"))),
            "story_core": _estimate_json_size_bytes(_safe_dict(pkg.get("story_core"))),
            "diagnostics": _estimate_json_size_bytes(diagnostics),
            "diagnostics.events": _estimate_json_size_bytes(_safe_list(diagnostics.get("events"))),
        }
        summary["size_counters"] = section_sizes
        summary["downstream_modes"] = {
            "role_plan_mode": str(_safe_dict(pkg.get("role_plan")).get("director_mode") or ""),
            "scene_plan_mode": str(_safe_dict(pkg.get("scene_plan")).get("director_mode") or ""),
            "scene_prompts_mode": str(_safe_dict(pkg.get("scene_prompts")).get("director_mode") or ""),
            "final_storyboard_mode": str(_safe_dict(pkg.get("final_storyboard")).get("director_mode") or ""),
        }
        summary["events"] = _safe_list(diagnostics.get("events"))
        summary["errors"] = _safe_list(diagnostics.get("errors"))
    return summary


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        if hasattr(value, "item") and callable(getattr(value, "item")):
            value = value.item()
        elif isinstance(value, (list, tuple)) and value:
            value = value[0]
        elif hasattr(value, "__len__") and hasattr(value, "__getitem__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]
        parsed = float(value)
        if math.isfinite(parsed):
            return round(parsed, 3)
    except Exception:
        pass
    return fallback


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _resolve_downstream_mode_metadata(package: dict[str, Any]) -> dict[str, str]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    story_core_v1 = _safe_dict(story_core.get("story_core_v1"))
    content_type = str(input_pkg.get("content_type") or "")
    director_mode = _resolve_director_mode(input_pkg.get("director_mode"), content_type=content_type)
    story_truth_source = str(story_core_v1.get("story_truth_source") or "").strip()
    audio_truth_scope = str(story_core_v1.get("audio_truth_scope") or "").strip()
    if not story_truth_source:
        story_truth_source = "note_refs_primary" if director_mode == "clip" else "mixed_inputs"
    if not audio_truth_scope:
        audio_truth_scope = "timing_plus_emotion" if director_mode == "clip" else "timing_structure"
    return {
        "director_mode": director_mode,
        "story_truth_source": story_truth_source,
        "audio_truth_scope": audio_truth_scope,
    }


def _attach_downstream_mode_metadata(stage_payload: Any, package: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_dict(stage_payload)
    metadata = _resolve_downstream_mode_metadata(package)
    payload["director_mode"] = metadata["director_mode"]
    payload["story_truth_source"] = metadata["story_truth_source"]
    payload["audio_truth_scope"] = metadata["audio_truth_scope"]
    return payload


def _scene_prompts_upstream_signature(package: dict[str, Any]) -> str:
    snapshot = {
        "story_core": _safe_dict(package.get("story_core")),
        "audio_map": _safe_dict(package.get("audio_map")),
        "role_plan": _safe_dict(package.get("role_plan")),
        "scene_plan": _safe_dict(package.get("scene_plan")),
        "refs_inventory": _safe_dict(package.get("refs_inventory")),
    }
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _normalize_route_contract(route_value: Any) -> dict[str, Any]:
    raw = str(route_value or "").strip().lower()
    if raw in {"f_l", "first-last"}:
        raw = "first_last"
    if raw in {"lip_sync", "lip_sync_music"}:
        raw = "ia2v"
    if raw not in {"i2v", "ia2v", "first_last"}:
        raw = "i2v"

    if raw == "ia2v":
        return {
            "route": "ia2v",
            "ltx_mode": "lip_sync_music",
            "render_mode": "lip_sync_music",
            "transition_type": "audio_performance",
            "lip_sync": True,
            "needs_two_frames": False,
        }
    if raw == "first_last":
        return {
            "route": "first_last",
            "ltx_mode": "f_l",
            "render_mode": "first_last",
            "transition_type": "micro_transition",
            "lip_sync": False,
            "needs_two_frames": True,
            "continuity_priority": "high",
            "environment_lock_required": True,
            "identity_lock_required": True,
            "wardrobe_lock_required": True,
            "two_frame_micro_transition": True,
        }
    return {
        "route": "i2v",
        "ltx_mode": "i2v",
        "render_mode": "image_to_video",
        "transition_type": "continuous",
        "lip_sync": False,
        "needs_two_frames": False,
    }


def _run_finalize_stage(package: dict[str, Any]) -> dict[str, Any]:
    # Guardrail: Final package assembly must remain local and deterministic.
    # Do not route finalize/final_storyboard assembly through Gemini/LLM.
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    scene_prompts = _safe_dict(package.get("scene_prompts"))
    final_video_prompt = _safe_dict(package.get("final_video_prompt"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    assigned_roles = _safe_dict(package.get("assigned_roles"))

    role_by_scene = {
        str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(role_plan.get("scene_roles"))
        if str(_safe_dict(row).get("scene_id") or "").strip()
    }
    plan_by_scene = {
        str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(scene_plan.get("scenes"))
        if str(_safe_dict(row).get("scene_id") or "").strip()
    }
    prompts_by_scene = {
        str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(scene_prompts.get("scenes"))
        if str(_safe_dict(row).get("scene_id") or "").strip()
    }

    final_video_prompt_by_scene = {
        str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(final_video_prompt.get("scenes"))
        if str(_safe_dict(row).get("scene_id") or "").strip()
    }

    scene_ids: list[str] = []
    for source in (plan_by_scene, prompts_by_scene, role_by_scene, final_video_prompt_by_scene):
        for scene_id in source.keys():
            if scene_id and scene_id not in scene_ids:
                scene_ids.append(scene_id)

    final_scenes: list[dict[str, Any]] = []
    for idx, scene_id in enumerate(scene_ids, start=1):
        scene_plan_row = _safe_dict(plan_by_scene.get(scene_id))
        role_row = _safe_dict(role_by_scene.get(scene_id))
        prompt_row = _safe_dict(prompts_by_scene.get(scene_id))
        final_video_prompt_row = _safe_dict(final_video_prompt_by_scene.get(scene_id))
        prompt_notes = _safe_dict(prompt_row.get("prompt_notes"))
        video_metadata = _safe_dict(final_video_prompt_row.get("video_metadata"))

        t0 = _to_float(
            scene_plan_row.get("t0")
            if scene_plan_row.get("t0") is not None
            else role_row.get("t0"),
            0.0,
        )
        t1 = _to_float(
            scene_plan_row.get("t1")
            if scene_plan_row.get("t1") is not None
            else role_row.get("t1"),
            t0,
        )
        if t1 < t0:
            t1 = t0
        duration_sec = _to_float(
            scene_plan_row.get("duration_sec")
            if scene_plan_row.get("duration_sec") is not None
            else (t1 - t0),
            max(0.0, t1 - t0),
        )
        if duration_sec <= 0 and t1 >= t0:
            duration_sec = _to_float(t1 - t0, 0.0)

        route_contract = _normalize_route_contract(
            prompt_row.get("route")
            or scene_plan_row.get("route")
        )
        active_roles = _safe_list(role_row.get("active_roles"))
        secondary_roles = _safe_list(role_row.get("secondary_roles"))
        primary_role = str(role_row.get("primary_role") or scene_plan_row.get("primary_role") or "").strip()
        must_appear = list(
            dict.fromkeys(
                [
                    primary_role,
                    *[str(role).strip() for role in active_roles if str(role).strip()],
                ]
            )
        )
        refs_by_role_input = _safe_dict(input_pkg.get("refs_by_role"))
        refs_used_by_role_from_input = {
            role: _safe_list(refs_by_role_input.get(role))
            for role in must_appear
            if _safe_list(refs_by_role_input.get(role))
        }
        refs_used_by_role = (
            refs_used_by_role_from_input
            if refs_used_by_role_from_input
            else _build_refs_by_role_fallback(refs_inventory, active_roles, primary_role)
        )
        refs_used = list(refs_used_by_role.keys())
        audio_slice_start_sec = t0 if route_contract["route"] == "ia2v" else 0.0
        audio_slice_end_sec = t1 if route_contract["route"] == "ia2v" else 0.0
        audio_slice_expected_duration_sec = max(0.0, audio_slice_end_sec - audio_slice_start_sec)
        summary = _first_text(
            scene_plan_row.get("scene_summary"),
            scene_plan_row.get("scene_description"),
            scene_plan_row.get("summary"),
            scene_plan_row.get("scene_goal"),
            scene_plan_row.get("narrative_function"),
            scene_plan_row.get("scene_function"),
            scene_plan_row.get("emotional_intent"),
            scene_plan_row.get("watchability_role"),
        )
        scene_goal = _first_text(
            scene_plan_row.get("scene_goal"),
            scene_plan_row.get("emotional_intent"),
            scene_plan_row.get("scene_description"),
            scene_plan_row.get("scene_summary"),
        )
        narrative_function = _first_text(
            scene_plan_row.get("narrative_function"),
            scene_plan_row.get("scene_function"),
            scene_plan_row.get("watchability_role"),
        )

        scene_contract: dict[str, Any] = {
            "scene_id": scene_id or f"sc_{idx}",
            "t0": t0,
            "t1": t1,
            "duration_sec": duration_sec,
            "summary": summary,
            "scene_goal": scene_goal,
            "narrative_function": narrative_function,
            "route": route_contract["route"],
            "ltx_mode": route_contract["ltx_mode"],
            "render_mode": route_contract["render_mode"],
            "transition_type": route_contract["transition_type"],
            "lip_sync": bool(route_contract["lip_sync"]),
            "needs_two_frames": bool(route_contract["needs_two_frames"]),
            "primary_role": primary_role,
            "secondary_roles": secondary_roles,
            "active_roles": active_roles,
            "mustAppear": must_appear,
            "refsUsed": refs_used,
            "refsUsedByRole": refs_used_by_role,
            "image_prompt": str(prompt_row.get("photo_prompt") or "").strip(),
            "video_prompt": str(prompt_row.get("video_prompt") or "").strip(),
            "negative_prompt": str(prompt_row.get("negative_prompt") or "").strip(),
            "positive_video_prompt": str(prompt_row.get("positive_video_prompt") or "").strip(),
            "negative_video_prompt": str(prompt_row.get("negative_video_prompt") or "").strip(),
            "positiveVideoPrompt": str(prompt_row.get("positive_video_prompt") or "").strip(),
            "negativeVideoPrompt": str(prompt_row.get("negative_video_prompt") or "").strip(),
            "audio_slice_start_sec": audio_slice_start_sec,
            "audio_slice_end_sec": audio_slice_end_sec,
            "audio_slice_expected_duration_sec": audio_slice_expected_duration_sec,
            "prompt_notes": prompt_notes,
            "video_metadata": video_metadata,
            "scene_presence_mode": str(role_row.get("scene_presence_mode") or scene_plan_row.get("scene_presence_mode") or "").strip(),
            "route_reason": str(scene_plan_row.get("route_reason") or "").strip(),
            "motion_intent": str(scene_plan_row.get("motion_intent") or "").strip(),
            "watchability_role": str(scene_plan_row.get("watchability_role") or "").strip(),
            "i2v_motion_family": str(scene_plan_row.get("i2v_motion_family") or "").strip(),
            "pace_class": str(scene_plan_row.get("pace_class") or "").strip(),
            "camera_pattern": str(scene_plan_row.get("camera_pattern") or "").strip(),
            "reveal_target": str(scene_plan_row.get("reveal_target") or "").strip(),
            "allow_head_turn": bool(scene_plan_row.get("allow_head_turn")),
            "allow_simple_hand_motion": bool(scene_plan_row.get("allow_simple_hand_motion")),
            "forbid_complex_hand_motion": bool(scene_plan_row.get("forbid_complex_hand_motion")),
            "forbid_slow_motion_feel": bool(scene_plan_row.get("forbid_slow_motion_feel")),
            "forbid_bullet_time": bool(scene_plan_row.get("forbid_bullet_time")),
            "forbid_stylized_action": bool(scene_plan_row.get("forbid_stylized_action")),
            "require_real_time_pacing": bool(scene_plan_row.get("require_real_time_pacing")),
            "parallax_required": bool(scene_plan_row.get("parallax_required")),
            "max_camera_intensity": str(scene_plan_row.get("max_camera_intensity") or "").strip(),
            "i2v_prompt_duration_hint_sec": _to_float(scene_plan_row.get("i2v_prompt_duration_hint_sec"), 0.0),
            "context_refs": refs_inventory,
            "connected_context_summary": _safe_dict(input_pkg.get("connected_context_summary")),
            "role_type_by_role": assigned_roles,
            "world_continuity": _safe_dict(role_plan.get("world_continuity")),
            "continuity_notes": _safe_list(role_plan.get("continuity_notes")),
            "story_locks": {
                "identity_lock": _safe_dict(story_core.get("identity_lock")),
                "world_lock": _safe_dict(story_core.get("world_lock")),
                "style_lock": _safe_dict(story_core.get("style_lock")),
            },
        }

        if route_contract["route"] == "first_last":
            if scene_contract.get("positive_video_prompt"):
                scene_contract["video_prompt"] = str(scene_contract.get("positive_video_prompt") or "").strip()
            if scene_contract.get("negative_video_prompt"):
                scene_contract["negative_prompt"] = str(scene_contract.get("negative_video_prompt") or "").strip()
            start_image_prompt = _first_text(
                prompt_row.get("start_image_prompt"),
                prompt_row.get("startImagePrompt"),
                prompt_row.get("first_frame_prompt"),
                prompt_row.get("firstFramePrompt"),
                prompt_row.get("photo_prompt"),
            )
            end_image_prompt = _first_text(
                prompt_row.get("end_image_prompt"),
                prompt_row.get("endImagePrompt"),
                prompt_row.get("last_frame_prompt"),
                prompt_row.get("lastFramePrompt"),
                prompt_row.get("resolved_frame_prompt"),
                prompt_row.get("resolvedFramePrompt"),
            )
            first_frame_prompt = _first_text(
                prompt_row.get("first_frame_prompt"),
                prompt_row.get("firstFramePrompt"),
                prompt_row.get("start_frame_prompt"),
                prompt_row.get("startFramePrompt"),
                start_image_prompt,
                prompt_row.get("photo_prompt"),
                prompt_row.get("image_prompt"),
                prompt_row.get("frame_prompt"),
                scene_plan_row.get("frame_description"),
                scene_plan_row.get("scene_description"),
            )
            last_frame_prompt = _first_text(
                prompt_row.get("last_frame_prompt"),
                prompt_row.get("lastFramePrompt"),
                prompt_row.get("end_frame_prompt"),
                prompt_row.get("endFramePrompt"),
                end_image_prompt,
                prompt_row.get("resolved_frame_prompt"),
                prompt_row.get("resolvedFramePrompt"),
                prompt_row.get("video_prompt"),
                prompt_row.get("transition_prompt"),
                prompt_row.get("motion_prompt"),
                scene_plan_row.get("scene_goal"),
                scene_plan_row.get("emotional_intent"),
            )
            scene_contract["continuity_priority"] = route_contract.get("continuity_priority", "high")
            scene_contract["environment_lock_required"] = bool(route_contract.get("environment_lock_required"))
            scene_contract["identity_lock_required"] = bool(route_contract.get("identity_lock_required"))
            scene_contract["wardrobe_lock_required"] = bool(route_contract.get("wardrobe_lock_required"))
            scene_contract["two_frame_micro_transition"] = bool(route_contract.get("two_frame_micro_transition"))
            scene_contract["start_image_prompt"] = start_image_prompt
            scene_contract["end_image_prompt"] = end_image_prompt
            scene_contract["first_frame_prompt"] = first_frame_prompt
            scene_contract["last_frame_prompt"] = last_frame_prompt

        final_scenes.append(scene_contract)

    final_storyboard = {
        "mode": "clip",
        "content_type": str(input_pkg.get("content_type") or ""),
        "format": str(input_pkg.get("format") or ""),
        "audio_url": str(input_pkg.get("audio_url") or "").strip(),
        "audio_duration_sec": _to_float(
            input_pkg.get("audio_duration_sec")
            if input_pkg.get("audio_duration_sec") is not None
            else audio_map.get("duration_sec"),
            _to_float(audio_map.get("duration_sec"), 0.0),
        ),
        "story_summary": str(story_core.get("story_summary") or "").strip(),
        "director_summary": str(story_core.get("director_summary") or story_core.get("story_summary") or "").strip(),
        "opening_anchor": str(story_core.get("opening_anchor") or "").strip(),
        "ending_callback_rule": str(story_core.get("ending_callback_rule") or "").strip(),
        "global_arc": str(story_core.get("global_arc") or "").strip(),
        "story_locks": {
            "identity_lock": _safe_dict(story_core.get("identity_lock")),
            "world_lock": _safe_dict(story_core.get("world_lock")),
            "style_lock": _safe_dict(story_core.get("style_lock")),
            "narrative_locks": _safe_dict(story_core.get("narrative_locks")),
        },
        "world_continuity": _safe_dict(role_plan.get("world_continuity")),
        "role_arc_summary": str(role_plan.get("role_arc_summary") or "").strip(),
        "continuity_notes": _safe_list(role_plan.get("continuity_notes")),
        "route_mix_summary": _safe_dict(scene_plan.get("route_mix_summary")),
        "scene_arc_summary": str(scene_plan.get("scene_arc_summary") or "").strip(),
        "route_strategy_notes": _safe_list(scene_plan.get("route_strategy_notes")),
        "refsByRole": _safe_dict(input_pkg.get("refs_by_role")),
        "roleTypeByRole": assigned_roles,
        "context_refs": refs_inventory,
        "connected_context_summary": _safe_dict(input_pkg.get("connected_context_summary")),
        "scenes": final_scenes,
    }
    final_storyboard = _attach_downstream_mode_metadata(final_storyboard, package)
    logger.info(
        "[FINALIZE STORYBOARD BUILD] planSceneCount=%s promptSceneCount=%s roleSceneCount=%s finalSceneCount=%s sceneIds=%s",
        len(plan_by_scene),
        len(prompts_by_scene),
        len(role_by_scene),
        len(final_scenes),
        scene_ids,
    )
    package["final_storyboard"] = final_storyboard

    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["finalize_scene_count"] = len(final_scenes)
    diagnostics["finalize_used_scene_prompts"] = bool(prompts_by_scene)
    diagnostics["finalize_used_scene_plan"] = bool(plan_by_scene)
    diagnostics["finalize_used_role_plan"] = bool(role_by_scene)
    diagnostics["finalize_used_final_video_prompt"] = bool(final_video_prompt_by_scene)
    package["diagnostics"] = diagnostics
    _append_diag_event(package, f"final_storyboard built scenes={len(final_scenes)}", stage_id="finalize")
    return package


def _run_story_core_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    audio_map = _safe_dict(package.get("audio_map"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    assigned_roles = _safe_dict(package.get("assigned_roles"))
    story_core_mode = _detect_story_core_mode(input_pkg)
    model_id = _resolve_active_video_model_id(input_pkg)
    capability_profile = get_video_model_capability_profile(model_id, "i2v")
    fallback = _default_story_core(input_pkg)
    if not _is_usable_audio_map(audio_map):
        raise RuntimeError("story_core_requires_audio_map")
    fallback["story_guidance"] = _default_story_core_guidance()
    fallback_core_v1 = _build_story_core_v11(
        input_pkg=input_pkg,
        audio_map=audio_map,
        refs_inventory=refs_inventory,
        assigned_roles=assigned_roles,
        parsed_story_core=fallback,
        fallback_story_core=fallback,
    )
    fallback["story_core_v1"] = fallback_core_v1
    prop_contracts, prop_guard_applied = _normalize_story_core_prop_contracts(input_pkg, refs_inventory)
    ref_attachment_summary = {
        "character_1": {"attached": False, "error": "", "source": "", "url": ""},
        "props": {"attached": False, "error": "", "source": "", "url": "", "connected": bool(prop_contracts), "contracts_count": len(prop_contracts)},
        "location": {"attached": False, "error": "", "source": "", "url": ""},
        "style": {"attached": False, "error": "", "source": "", "url": ""},
    }
    grounding_level = "strict" if prop_contracts else "standard"
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["story_core_mode"] = story_core_mode
    diagnostics["story_core_used_model"] = "gemini-3.1-pro-preview"
    diagnostics["story_core_character_ref_attached"] = False
    diagnostics["story_core_character_ref_source"] = ""
    diagnostics["story_core_character_ref_error"] = ""
    diagnostics["story_core_prop_contracts"] = prop_contracts
    diagnostics["story_core_prop_confusion_guard_applied"] = bool(prop_guard_applied)
    diagnostics["story_core_ref_attachment_summary"] = ref_attachment_summary
    diagnostics["story_core_grounding_level"] = grounding_level
    diagnostics["story_core_audio_informed"] = bool(audio_map)
    diagnostics["story_core_audio_dramaturgy_source"] = "audio_map" if audio_map else ""
    diagnostics["story_core_textual_directive_present"] = bool(_has_textual_directive(input_pkg))
    diagnostics["story_core_available_roles"] = []
    diagnostics["story_core_attached_ref_roles"] = []
    diagnostics["story_core_attached_ref_count"] = 0
    diagnostics["story_core_props_ref_attached"] = False
    diagnostics["story_core_location_ref_attached"] = False
    diagnostics["story_core_style_ref_attached"] = False
    diagnostics["story_core_payload_mode"] = "full"
    diagnostics["story_core_available_roles_resolved"] = []
    diagnostics["story_core_director_world_lock_summary"] = ""
    diagnostics["story_core_compact_context_size_estimate"] = 0
    diagnostics["story_core_refs_sources_used"] = []
    diagnostics["active_video_model_capability_profile"] = model_id
    diagnostics["active_route_capability_mode"] = "story_core_planning_bounds"
    diagnostics["story_core_capability_guard_applied"] = True
    diagnostics["scene_plan_capability_guard_applied"] = False
    diagnostics["prompt_capability_guard_applied"] = False
    diagnostics["capability_rules_source_version"] = get_capability_rules_source_version()
    package["diagnostics"] = diagnostics
    _append_diag_event(package, "story_core audio-informed build requested", stage_id="story_core")
    try:
        api_key = str(os.getenv("GEMINI_API_KEY") or "").strip()
        inline_ref_parts, inline_ref_diag = _build_story_core_inline_ref_parts(
            input_pkg=input_pkg,
            refs_inventory=refs_inventory,
        )
        if bool(inline_ref_diag.get("story_core_character_ref_attached")):
            grounding_level = "strict"
        elif bool(inline_ref_diag.get("story_core_attached_ref_count")):
            grounding_level = "standard"
        else:
            grounding_level = "cautious"
        ref_attachment_summary = _safe_dict(inline_ref_diag.get("story_core_ref_attachment_summary"))
        props_summary = _safe_dict(ref_attachment_summary.get("props"))
        props_summary["connected"] = bool(prop_contracts)
        props_summary["contracts_count"] = len(prop_contracts)
        ref_attachment_summary["props"] = props_summary
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics.update(inline_ref_diag)
        diagnostics["story_core_ref_attachment_summary"] = ref_attachment_summary
        diagnostics["story_core_grounding_level"] = grounding_level
        package["diagnostics"] = diagnostics
        input_with_assignments = dict(input_pkg)
        input_with_assignments["assigned_roles_override"] = assigned_roles
        core_input_context = _build_story_core_input_context(
            input_pkg=input_with_assignments,
            audio_map=audio_map,
            refs_inventory=refs_inventory,
            prop_contracts=prop_contracts,
            ref_attachment_summary=ref_attachment_summary,
            grounding_level=grounding_level,
        )
        diagnostics["story_core_available_roles"] = _safe_list(core_input_context.get("available_roles"))
        diagnostics["story_core_available_roles_resolved"] = _safe_list(core_input_context.get("available_roles"))
        diagnostics["story_core_attached_ref_roles"] = _safe_list(core_input_context.get("story_core_attached_ref_roles"))
        diagnostics["story_core_director_world_lock_summary"] = str(core_input_context.get("story_core_director_world_lock_summary") or "")
        diagnostics["story_core_compact_context_size_estimate"] = int(core_input_context.get("story_core_compact_context_size_estimate") or 0)
        diagnostics["story_core_refs_sources_used"] = _safe_list(core_input_context.get("story_core_refs_sources_used"))
        diagnostics["story_core_payload_mode"] = "lean"
        diagnostics.update(
            build_capability_diagnostics_summary(
                model_id=model_id,
                route_type="story_core_planning_bounds",
                story_core_guard_applied=True,
                scene_plan_guard_applied=False,
                prompt_guard_applied=False,
            )
        )
        package["diagnostics"] = diagnostics
        capability_bounds_text = (
            f"model={model_id}; verified_safe={', '.join([str(v) for v in _safe_list(capability_profile.get('verified_safe'))])}; "
            f"experimental(opt-in)={', '.join([str(v) for v in _safe_list(capability_profile.get('experimental'))])}; "
            f"blocked={', '.join([str(v) for v in _safe_list(capability_profile.get('blocked'))])}"
        )[:1500]
        prompt = _build_story_core_prompt(
            core_input_context,
            assigned_roles,
            story_core_mode,
            capability_bounds_text,
        )
        parts: list[dict[str, Any]] = [{"text": prompt}, *inline_ref_parts]
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        }
        response = post_generate_content(
            api_key=api_key,
            model="gemini-3.1-pro-preview",
            body=body,
            timeout=90,
        )
        if isinstance(response, dict) and response.get("__http_error__"):
            raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")
        parsed = _extract_json_obj(_extract_gemini_text(response))
        story_core = {
            "story_summary": str(parsed.get("story_summary") or fallback["story_summary"]).strip(),
            "opening_anchor": str(parsed.get("opening_anchor") or fallback["opening_anchor"]).strip(),
            "ending_callback_rule": str(parsed.get("ending_callback_rule") or fallback["ending_callback_rule"]).strip(),
            "global_arc": parsed.get("global_arc") or fallback["global_arc"],
            "identity_lock": _safe_dict(parsed.get("identity_lock")) or fallback["identity_lock"],
            "world_lock": _safe_dict(parsed.get("world_lock")) or fallback["world_lock"],
            "style_lock": _safe_dict(parsed.get("style_lock")) or fallback["style_lock"],
            "story_guidance": _normalize_story_core_guidance(parsed.get("story_guidance")),
        }
        story_core_v1 = _build_story_core_v11(
            input_pkg=input_pkg,
            audio_map=audio_map,
            refs_inventory=refs_inventory,
            assigned_roles=assigned_roles,
            parsed_story_core=story_core,
            fallback_story_core=fallback,
        )
        story_core["story_core_v1"] = story_core_v1
        if not _is_usable_story_core(story_core):
            raise ValueError("story_core_unusable_after_parse")
        package["story_core"] = story_core
        package["story_core_v1"] = story_core_v1
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics["story_core_used_fallback"] = False
        package["diagnostics"] = diagnostics
        _append_diag_event(package, "story_core generated", stage_id="story_core")
        return package
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scenario_stage_pipeline] story_core failed")
        if _is_usable_story_core(fallback):
            diagnostics = _safe_dict(package.get("diagnostics"))
            warnings = _safe_list(diagnostics.get("warnings"))
            warnings.append({"stage_id": "story_core", "message": f"fallback_used:{exc}"})
            diagnostics["warnings"] = warnings[-80:]
            diagnostics["story_core_used_fallback"] = True
            package["diagnostics"] = diagnostics
            package["story_core"] = fallback
            package["story_core_v1"] = _safe_dict(fallback.get("story_core_v1"))
            _append_diag_event(package, f"story_core fallback used: {exc}", stage_id="story_core")
            return package
        raise RuntimeError(f"story_core_failed_no_fallback:{exc}") from exc


def _run_input_package_stage(package: dict[str, Any]) -> dict[str, Any]:
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    package["refs_inventory"] = refs_inventory
    package["input"] = _normalize_input_audio_source(_safe_dict(package.get("input")), refs_inventory)
    _append_diag_event(package, "input_package normalized", stage_id="input_package")
    return package


def _coerce_duration_sec(value: Any) -> float:
    try:
        if hasattr(value, "item") and callable(getattr(value, "item")):
            value = value.item()
        elif isinstance(value, (list, tuple)) and value:
            value = value[0]
        elif hasattr(value, "__len__") and hasattr(value, "__getitem__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]
        duration = float(value)
    except Exception:
        duration = 0.0
    if duration < 0.0:
        return 0.0
    return duration


def _clamp_time(value: float, duration_sec: float) -> float:
    if duration_sec <= 0:
        return max(0.0, float(value))
    return max(0.0, min(float(value), duration_sec))


def _segment_count_for_duration(duration_sec: float) -> int:
    if duration_sec <= 30:
        return 3
    if duration_sec <= 70:
        return 4
    if duration_sec <= 130:
        return 5
    return 6


def _split_duration_evenly(duration_sec: float, segments: int) -> list[tuple[float, float]]:
    safe_segments = max(1, int(segments or 1))
    if duration_sec <= 0:
        return [(0.0, 0.0)]
    result: list[tuple[float, float]] = []
    for idx in range(safe_segments):
        t0 = (duration_sec * idx) / safe_segments
        t1 = (duration_sec * (idx + 1)) / safe_segments
        result.append((round(t0, 3), round(t1, 3)))
    return result


def _labels_for_segment_count(segment_count: int) -> list[str]:
    if segment_count <= 3:
        return ["intro", "turn", "release"]
    if segment_count == 4:
        return ["intro", "rise", "turn", "release"]
    if segment_count == 5:
        return ["intro", "rise", "turn", "release", "outro"]
    return ["intro", "rise", "turn", "release", "afterglow", "outro"]


def _energy_curve_for_count(segment_count: int) -> list[str]:
    if segment_count <= 3:
        return ["low", "high", "medium"]
    if segment_count == 4:
        return ["low", "medium", "high", "medium"]
    if segment_count == 5:
        return ["low", "medium", "high", "medium", "low"]
    return ["low", "medium", "high", "high", "medium", "low"]


def _mood_curve_from_story_core(story_core: dict[str, Any], segment_count: int) -> list[str]:
    arc = str(story_core.get("global_arc") or "").strip().lower()
    if "setup" in arc and "afterimage" in arc:
        base = ["anticipation", "momentum", "tension", "release", "afterimage"]
        if segment_count <= len(base):
            return base[:segment_count]
        return base + ["calm"] * (segment_count - len(base))
    if segment_count <= 3:
        return ["calm", "intense", "resolved"]
    if segment_count == 4:
        return ["calm", "building", "intense", "resolved"]
    if segment_count == 5:
        return ["calm", "building", "intense", "resolved", "afterglow"]
    return ["calm", "building", "intense", "urgent", "resolved", "afterglow"]


def _build_audio_map_from_duration(
    duration_sec: float,
    story_core: dict[str, Any],
    *,
    analysis_mode: str,
    content_type: str = "",
) -> dict[str, Any]:
    duration = _coerce_duration_sec(duration_sec)
    segment_count = _segment_count_for_duration(duration)
    windows = _split_duration_evenly(duration, segment_count)
    labels = _labels_for_segment_count(segment_count)
    energies = _energy_curve_for_count(segment_count)
    moods = _mood_curve_from_story_core(story_core, segment_count)

    sections: list[dict[str, Any]] = []
    for idx, (t0, t1) in enumerate(windows):
        sections.append(
            {
                "id": f"sec_{idx + 1}",
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "label": labels[idx] if idx < len(labels) else f"part_{idx + 1}",
                "energy": energies[idx] if idx < len(energies) else "medium",
                "mood": moods[idx] if idx < len(moods) else "neutral",
            }
        )

    # phrase endpoints: keep section boundaries; for music_video fallback do not synthesize pseudo-cadence grid.
    is_music_video = str(content_type or "").strip().lower() == "music_video"
    phrase_step = 4.0 if duration <= 90 else 6.0
    phrase_points: list[float] = []
    if duration > 0 and not is_music_video:
        cursor = phrase_step
        while cursor < max(0.0, duration - 0.001):
            phrase_points.append(round(cursor, 3))
            cursor += phrase_step
    for sec in sections:
        boundary = _clamp_time(float(sec.get("t1") or 0.0), duration)
        if 0.0 < boundary < duration:
            phrase_points.append(round(boundary, 3))
    phrase_endpoints = sorted(set(phrase_points))

    no_split_ranges: list[dict[str, Any]] = []
    if duration > 0:
        no_split_ranges.append({"t0": 0.0, "t1": round(min(1.5, duration), 3), "reason": "intro_protection"})
        tail_start = round(max(0.0, duration - 1.5), 3)
        if tail_start < duration:
            no_split_ranges.append({"t0": tail_start, "t1": round(duration, 3), "reason": "outro_tail_protection"})
    for sec in sections:
        t0 = float(sec.get("t0") or 0.0)
        t1 = float(sec.get("t1") or 0.0)
        if (t1 - t0) >= 5.0:
            pivot = round(t0 + (t1 - t0) * 0.5, 3)
            no_split_ranges.append(
                {"t0": round(max(t0, pivot - 0.35), 3), "t1": round(min(t1, pivot + 0.35), 3), "reason": "phrase_core_window"}
            )

    candidate_cut_points: list[float] = []
    for point in phrase_endpoints:
        if point <= 0.0 or point >= duration:
            continue
        inside_blocked = any(float(r.get("t0") or 0.0) <= point <= float(r.get("t1") or 0.0) for r in no_split_ranges)
        if not inside_blocked:
            candidate_cut_points.append(point)

    mood_progression = [
        {
            "t0": float(section.get("t0") or 0.0),
            "t1": float(section.get("t1") or 0.0),
            "mood": str(section.get("mood") or "neutral"),
            "energy": str(section.get("energy") or "medium"),
        }
        for section in sections
    ]
    energy_counts = {"low": 0, "medium": 0, "high": 0}
    for section in sections:
        energy = str(section.get("energy") or "medium").lower()
        if energy in energy_counts:
            energy_counts[energy] += 1
    dominant_energy = max(energy_counts.items(), key=lambda item: item[1])[0] if sections else "medium"

    lip_sync_ranges: list[dict[str, float]] = []
    content_hint = str(story_core.get("story_summary") or "").lower()
    if "speak" in content_hint or "sing" in content_hint or "vocal" in content_hint:
        for section in sections:
            if str(section.get("energy") or "").lower() in {"medium", "high"}:
                lip_sync_ranges.append(
                    {
                        "t0": round(float(section.get("t0") or 0.0), 3),
                        "t1": round(float(section.get("t1") or 0.0), 3),
                    }
                )

    arc_short = str(story_core.get("global_arc") or "").strip() or "unknown_arc"
    phrase_units: list[dict[str, Any]] = []
    prev_t = 0.0
    for idx, t1 in enumerate(phrase_endpoints, start=1):
        current_t1 = _clamp_time(float(t1), duration)
        if current_t1 <= prev_t:
            continue
        phrase_units.append(
            {
                "id": f"ph_{idx}",
                "t0": round(prev_t, 3),
                "t1": round(current_t1, 3),
                "duration_sec": round(current_t1 - prev_t, 3),
                "text": "",
                "word_count": 0,
                "semantic_weight": "medium",
            }
        )
        prev_t = current_t1
    if duration > prev_t:
        phrase_units.append(
            {
                "id": f"ph_{len(phrase_units) + 1}",
                "t0": round(prev_t, 3),
                "t1": round(duration, 3),
                "duration_sec": round(max(0.0, duration - prev_t), 3),
                "text": "",
                "word_count": 0,
                "semantic_weight": "medium",
            }
        )
    scene_windows = _build_scene_candidate_windows(phrase_units, duration) if phrase_units else []

    return {
        "duration_sec": round(duration, 3),
        "analysis_mode": analysis_mode,
        "sections": sections,
        "phrase_endpoints_sec": phrase_endpoints,
        "phrase_units": phrase_units,
        "scene_candidate_windows": scene_windows,
        "no_split_ranges": no_split_ranges,
        "candidate_cut_points_sec": sorted(set(candidate_cut_points)),
        "pacing_profile": {
            "segment_count": len(sections),
            "phrase_step_sec": None if is_music_video else phrase_step,
            "dominant_energy": dominant_energy,
        },
        "mood_progression": mood_progression,
        "audio_arc_summary": f"Audio map follows story_core arc '{arc_short}' with {len(sections)} timing sections.",
        "section_summary": [f"{sec.get('label')}:{sec.get('energy')}/{sec.get('mood')}" for sec in sections],
        "lip_sync_candidate_ranges": lip_sync_ranges,
    }


def _resolve_audio_analysis_path(audio_url: str) -> tuple[str, str | None]:
    raw = str(audio_url or "").strip()
    if raw:
        try:
            direct_path = Path(raw).expanduser().resolve()
            if direct_path.exists() and direct_path.is_file():
                return str(direct_path), None
        except Exception:
            pass

    local_path, _ = _resolve_local_audio_asset_path(audio_url=audio_url)
    if local_path:
        return local_path, None

    resolved = _resolve_reference_url(audio_url)
    if not resolved:
        return "", None

    parsed = urllib.parse.urlparse(resolved)
    suffix = Path(parsed.path or "").suffix or ".audio"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    req = urllib.request.Request(resolved, headers={"User-Agent": "photostudio-audio-map/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp, open(tmp_path, "wb") as fh:
            fh.write(resp.read())
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return "", None
    return tmp_path, tmp_path


def _collect_audio_resolution_candidates(
    audio_url: str,
    input_payload: dict[str, Any] | None = None,
    refs_inventory: dict[str, Any] | None = None,
) -> list[str]:
    input_pkg = _safe_dict(input_payload)
    refs = _safe_dict(refs_inventory)
    audio_in = _safe_dict(refs.get("audio_in"))
    audio_in_meta = _safe_dict(audio_in.get("meta"))
    source = _safe_dict(input_pkg.get("source"))
    connected = _safe_dict(input_pkg.get("connected_context_summary"))
    connected_audio = _safe_dict(connected.get("audio_in"))

    raw_candidates = [
        audio_url,
        source.get("source_value"),
        source.get("sourceValue"),
        input_pkg.get("source_value"),
        input_pkg.get("sourceValue"),
        audio_in.get("value"),
        audio_in_meta.get("url"),
        audio_in_meta.get("fileName"),
        connected.get("audio_url"),
        connected.get("audioUrl"),
        connected.get("source_value"),
        connected.get("sourceValue"),
        connected.get("fileName"),
        connected_audio.get("value"),
        _safe_dict(connected_audio.get("meta")).get("url"),
        _safe_dict(connected_audio.get("meta")).get("fileName"),
    ]
    seen: set[str] = set()
    values: list[str] = []
    for item in raw_candidates:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _extract_asset_rel_path_from_audio_candidate(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    source = parsed.path or raw
    decoded = urllib.parse.unquote(str(source).strip())
    normalized = decoded.replace("\\", "/").lstrip("/")
    marker = "static/assets/"
    lower = normalized.lower()
    idx = lower.find(marker)
    if idx >= 0:
        return normalized[idx + len(marker) :].strip("/")
    if normalized.lower().startswith("assets/"):
        return normalized[len("assets/") :].strip("/")
    return ""


def _resolve_local_audio_asset_path(
    *,
    audio_url: str,
    input_payload: dict[str, Any] | None = None,
    refs_inventory: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    assets_root = Path(ASSETS_DIR).resolve()
    candidates = _collect_audio_resolution_candidates(audio_url, input_payload, refs_inventory)
    basename_candidates: list[str] = []
    selected_rel_path = ""

    debug: dict[str, Any] = {
        "audio_url": str(audio_url or ""),
        "extracted_asset_relative_path": "",
        "final_local_path": "",
        "exists": False,
        "fallback_to_basename_lookup": False,
    }

    for candidate in candidates:
        rel_path = _extract_asset_rel_path_from_audio_candidate(candidate)
        parsed = urllib.parse.urlparse(candidate)
        basename = Path(urllib.parse.unquote(parsed.path or candidate)).name
        if basename and basename not in basename_candidates:
            basename_candidates.append(basename)
        if not rel_path:
            continue
        if not selected_rel_path:
            selected_rel_path = rel_path
        try:
            file_path = (assets_root / rel_path).resolve()
            if assets_root not in file_path.parents:
                continue
            if file_path.exists() and file_path.is_file():
                debug["extracted_asset_relative_path"] = rel_path
                debug["final_local_path"] = str(file_path)
                debug["exists"] = True
                return str(file_path), debug
        except Exception:
            continue

    debug["extracted_asset_relative_path"] = selected_rel_path
    debug["fallback_to_basename_lookup"] = bool(basename_candidates)
    for basename in basename_candidates:
        try:
            candidate_path = (assets_root / basename).resolve()
            if assets_root not in candidate_path.parents:
                continue
            if candidate_path.exists() and candidate_path.is_file():
                debug["final_local_path"] = str(candidate_path)
                debug["exists"] = True
                return str(candidate_path), debug
        except Exception:
            continue

    return "", debug


def _float_points(values: Any, duration_sec: float, *, min_t: float = 0.0, max_t: float | None = None) -> list[float]:
    max_bound = duration_sec if max_t is None else max_t
    points: list[float] = []
    for value in _safe_list(values):
        try:
            t = float(value)
        except Exception:
            continue
        if t < min_t or t > max_bound:
            continue
        points.append(round(t, 3))
    points.sort()
    dedup: list[float] = []
    for point in points:
        if not dedup or abs(point - dedup[-1]) >= 0.18:
            dedup.append(point)
    return dedup


def _extract_audio_transcript_text(input_pkg: dict[str, Any]) -> str:
    candidates = (
        input_pkg.get("transcript_text"),
        input_pkg.get("transcriptText"),
        input_pkg.get("transcript"),
        input_pkg.get("lyrics_text"),
        input_pkg.get("lyricsText"),
        input_pkg.get("lyrics"),
        input_pkg.get("spoken_text_hint"),
        input_pkg.get("spokenTextHint"),
    )
    for item in candidates:
        text = str(item or "").strip()
        if text:
            return text
    return ""


def _words_from_text(text: str) -> list[str]:
    return [token for token in re.findall(r"[^\s]+", str(text or "").strip()) if token]


def _is_instrumental_tail_marker_text(text: Any) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    normalized = re.sub(r"[\[\]\(\)\{\}_\-:;,.!?]", " ", raw.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    marker_tokens = set(normalized.split())
    instrumental_tokens = {
        "instrumental",
        "interlude",
        "outro",
        "tail",
        "residual",
        "residue",
        "ending",
        "fade",
        "music",
        "nonlexical",
        "non",
        "lexical",
        "vocal",
        "vocals",
        "no",
    }
    if not marker_tokens.issubset(instrumental_tokens):
        return False
    return bool(marker_tokens.intersection({"instrumental", "interlude", "outro", "tail", "residual", "residue", "fade"}))


def _estimate_word_count_from_text(text: Any) -> int:
    if _is_instrumental_tail_marker_text(text):
        return 0
    return len(_words_from_text(str(text or "")))


def _phrase_word_count(phrase: dict[str, Any]) -> int:
    direct_count = int(max(0, _to_float(phrase.get("word_count"), -1.0)))
    if direct_count > 0:
        return direct_count
    return _estimate_word_count_from_text(phrase.get("text"))


def _semantic_weight_for_phrase(text: str, word_count: int, *, is_last: bool) -> str:
    clean = str(text or "").strip()
    if is_last or any(mark in clean for mark in ("!", "?", "…", "...", "—")):
        return "high"
    if word_count >= 9 or "," in clean or ";" in clean or ":" in clean:
        return "medium"
    return "low"


def _build_phrase_units_from_alignment(
    analysis: dict[str, Any],
    duration_sec: float,
    transcript_text: str,
) -> list[dict[str, Any]]:
    return _build_phrase_units_from_approximate_alignment(analysis, duration_sec, transcript_text)


def _build_phrase_units_from_approximate_alignment(
    analysis: dict[str, Any],
    duration_sec: float,
    transcript_text: str,
) -> list[dict[str, Any]]:
    vocal_phrases = [item for item in _safe_list(analysis.get("vocalPhrases")) if isinstance(item, dict)]
    if not vocal_phrases:
        return []
    words = _words_from_text(transcript_text)
    if not words:
        return []

    ordered = sorted(vocal_phrases, key=lambda item: float(item.get("start") or 0.0))
    spans: list[tuple[float, float]] = []
    for phrase in ordered:
        t0 = _clamp_time(float(phrase.get("start") or 0.0), duration_sec)
        t1 = _clamp_time(float(phrase.get("end") or 0.0), duration_sec)
        if t1 - t0 < 0.2:
            continue
        spans.append((t0, t1))
    if not spans:
        return []

    durations = [max(0.2, t1 - t0) for t0, t1 in spans]
    total_duration = max(0.001, sum(durations))
    remaining = len(words)
    allocations: list[int] = []
    for idx, span_duration in enumerate(durations):
        if idx == len(durations) - 1:
            count = remaining
        else:
            ratio = span_duration / total_duration
            count = max(1, int(round(len(words) * ratio)))
            remaining_after = len(durations) - idx - 1
            count = min(count, max(1, remaining - remaining_after))
        allocations.append(count)
        remaining -= count

    phrase_units: list[dict[str, Any]] = []
    cursor = 0
    for idx, ((t0, t1), count) in enumerate(zip(spans, allocations, strict=False), start=1):
        phrase_words = words[cursor : cursor + count]
        cursor += count
        if not phrase_words:
            continue
        text = " ".join(phrase_words).strip()
        phrase_units.append(
            {
                "id": f"ph_{idx}",
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "text": text,
                "word_count": len(phrase_words),
                "can_cut_after": True,
                "semantic_weight": _semantic_weight_for_phrase(text, len(phrase_words), is_last=idx == len(spans)),
                "words": [],
                "word_timestamps": [],
            }
        )
    return phrase_units


def _extract_input_alignment_payload(input_pkg: dict[str, Any]) -> dict[str, Any]:
    alignment = _safe_dict(input_pkg.get("transcript_alignment"))
    if alignment:
        return alignment
    alignment = _safe_dict(input_pkg.get("transcriptAlignment"))
    if alignment:
        return alignment
    words = _safe_list(input_pkg.get("word_timestamps") or input_pkg.get("wordTimestamps"))
    phrases = _safe_list(input_pkg.get("phrase_timestamps") or input_pkg.get("phraseTimestamps"))
    if words:
        return {
            "transcript_text": str(input_pkg.get("transcript_text") or input_pkg.get("transcriptText") or "").strip(),
            "words": words,
            "phrases": phrases,
            "source": "input_payload_word_timestamps",
        }
    return {}


def _build_phrase_units_from_real_alignment(
    alignment: dict[str, Any],
    duration_sec: float,
) -> list[dict[str, Any]]:
    def _append_phrase_chunk(words_chunk: list[dict[str, Any]], preferred_text: str, is_last: bool) -> None:
        if not words_chunk:
            return
        resolved_t0 = float(words_chunk[0].get("t0") or 0.0)
        resolved_t1 = float(words_chunk[-1].get("t1") or resolved_t0)
        text = preferred_text or " ".join(str(word.get("text") or "") for word in words_chunk).strip()
        phrase_units.append(
            {
                "id": f"ph_{len(phrase_units) + 1}",
                "t0": round(resolved_t0, 3),
                "t1": round(resolved_t1, 3),
                "text": text,
                "word_count": len(words_chunk),
                "words": words_chunk,
                "word_timestamps": words_chunk,
                "can_cut_after": True,
                "semantic_weight": _semantic_weight_for_phrase(text, len(words_chunk), is_last=is_last),
            }
        )

    words = [
        item
        for item in _safe_list(alignment.get("words"))
        if isinstance(item, dict) and float(item.get("t1") or 0.0) > float(item.get("t0") or 0.0)
    ]
    if not words:
        return []

    phrases_raw = [
        item
        for item in _safe_list(alignment.get("phrases"))
        if isinstance(item, dict) and float(item.get("t1") or 0.0) > float(item.get("t0") or 0.0)
    ]
    if not phrases_raw:
        min_t0 = float(words[0].get("t0") or 0.0)
        max_t1 = float(words[-1].get("t1") or min_t0)
        phrases_raw = [{"text": str(alignment.get("transcript_text") or "").strip(), "t0": min_t0, "t1": max_t1}]

    phrase_units: list[dict[str, Any]] = []
    word_idx = 0
    for phrase_idx, phrase in enumerate(phrases_raw, start=1):
        t0 = _clamp_time(float(phrase.get("t0") or 0.0), duration_sec)
        t1 = _clamp_time(float(phrase.get("t1") or 0.0), duration_sec)
        if t1 - t0 < 0.12:
            continue
        phrase_words: list[dict[str, Any]] = []
        while word_idx < len(words):
            word = words[word_idx]
            w0 = float(word.get("t0") or 0.0)
            w1 = float(word.get("t1") or 0.0)
            if w1 <= t0:
                word_idx += 1
                continue
            if w0 >= t1:
                break
            phrase_words.append(
                {
                    "text": str(word.get("text") or "").strip(),
                    "t0": round(_clamp_time(w0, duration_sec), 3),
                    "t1": round(_clamp_time(w1, duration_sec), 3),
                }
            )
            word_idx += 1
        if not phrase_words:
            continue
        phrase_text = str(phrase.get("text") or "").strip() or " ".join(str(word.get("text") or "") for word in phrase_words).strip()
        phrase_span = float(phrase_words[-1].get("t1") or 0.0) - float(phrase_words[0].get("t0") or 0.0)
        if phrase_span <= 8.0 or len(phrase_words) <= 3:
            _append_phrase_chunk(phrase_words, phrase_text, is_last=phrase_idx == len(phrases_raw))
            continue
        # Aggressive split for oversized phrase: cut only at real word boundaries.
        chunk: list[dict[str, Any]] = []
        for w_idx, word in enumerate(phrase_words):
            chunk.append(word)
            chunk_span = float(chunk[-1].get("t1") or 0.0) - float(chunk[0].get("t0") or 0.0)
            is_last_word = w_idx == len(phrase_words) - 1
            if chunk_span >= 6.0 or (chunk_span >= 3.0 and is_last_word):
                _append_phrase_chunk(chunk, "", is_last=is_last_word and phrase_idx == len(phrases_raw))
                chunk = []
        if chunk:
            _append_phrase_chunk(chunk, "", is_last=phrase_idx == len(phrases_raw))

    return phrase_units


def _build_phrase_units_from_music_dynamics(
    duration_sec: float,
    analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    duration = _coerce_duration_sec(duration_sec)
    if duration <= 0:
        return []

    pause_points = _float_points(analysis.get("pausePoints"), duration, min_t=0.2, max_t=max(0.0, duration - 0.2))
    phrase_points = _float_points(analysis.get("phraseBoundaries"), duration, min_t=0.2, max_t=max(0.0, duration - 0.2))
    section_edges = []
    for section in _safe_list(analysis.get("sections")):
        if not isinstance(section, dict):
            continue
        end_point = _coerce_duration_sec(section.get("end"))
        if 0.2 <= end_point <= max(0.0, duration - 0.2):
            section_edges.append(round(end_point, 3))
    section_points = _float_points(section_edges, duration, min_t=0.2, max_t=max(0.0, duration - 0.2))
    energy_peaks = _float_points(analysis.get("energyPeaks"), duration, min_t=0.2, max_t=max(0.0, duration - 0.2))

    score_by_point: dict[float, float] = {}
    for point in pause_points:
        score_by_point[point] = max(score_by_point.get(point, 0.0), 1.0)
    for point in phrase_points:
        score_by_point[point] = max(score_by_point.get(point, 0.0), 0.9)
    for point in section_points:
        score_by_point[point] = max(score_by_point.get(point, 0.0), 0.82)
    for point in energy_peaks:
        score_by_point[point] = max(score_by_point.get(point, 0.0), 0.66)

    boundaries: list[float] = [0.0]
    min_phrase = 2.0
    pref_min = 3.0
    pref_max = 6.0
    hard_max = 8.0
    prev_span = 0.0
    phrase_idx = 0
    candidates = sorted(score_by_point.keys())

    while boundaries[-1] < duration - 0.12:
        start = boundaries[-1]
        best_t = None
        best_score = -1e9
        for point in candidates:
            if point <= start + min_phrase:
                continue
            span = point - start
            if span > hard_max:
                break
            score = score_by_point.get(point, 0.0)
            if pref_min <= span <= pref_max:
                score += 0.75
            score -= abs(span - 4.4) * 0.22
            if prev_span > 0 and abs(span - prev_span) <= 0.12:
                score -= 0.65
            if abs(span - 4.0) <= 0.05:
                score -= 0.2
            if score > best_score:
                best_score = score
                best_t = point

        if best_t is None:
            remaining = duration - start
            if remaining <= hard_max:
                best_t = duration
            else:
                best_t = _clamp_time(start + pref_max, duration)

        if duration - best_t < 1.8:
            best_t = duration
        if best_t <= start + 0.15:
            break
        prev_span = max(0.0, best_t - start)
        boundaries.append(round(best_t, 3))
        phrase_idx += 1
        if boundaries[-1] >= duration - 0.001:
            boundaries[-1] = round(duration, 3)
            break

    if boundaries[-1] < duration:
        boundaries.append(round(duration, 3))

    phrase_units: list[dict[str, Any]] = []
    for idx in range(1, len(boundaries)):
        t0 = boundaries[idx - 1]
        t1 = boundaries[idx]
        span = t1 - t0
        if span <= 0.15:
            continue
        boundary_strength = score_by_point.get(round(t1, 3), 0.0)
        semantic_weight = "high" if boundary_strength >= 0.9 else "medium" if boundary_strength >= 0.7 else "low"
        phrase_units.append(
            {
                "id": f"ph_{len(phrase_units) + 1}",
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "duration_sec": round(span, 3),
                "text": "",
                "word_count": 0,
                "semantic_weight": semantic_weight,
            }
        )
    return phrase_units


def _build_scene_candidate_windows(
    phrase_units: list[dict[str, Any]],
    duration_sec: float,
    *,
    target_min: float = 3.0,
    target_max: float = 6.0,
    hard_max: float = 8.0,
) -> list[dict[str, Any]]:
    if not phrase_units:
        return []
    windows: list[dict[str, Any]] = []

    def _append_window(start_idx: int, end_idx: int) -> None:
        win_start = _clamp_time(float(phrase_units[start_idx].get("t0") or 0.0), duration_sec)
        win_end = _clamp_time(float(phrase_units[end_idx].get("t1") or win_start), duration_sec)
        windows.append(
            {
                "id": f"sc_{len(windows) + 1}",
                "t0": round(win_start, 3),
                "t1": round(win_end, 3),
                "duration_sec": round(max(0.0, win_end - win_start), 3),
                "phrase_ids": [str(phrase_units[k].get("id") or "") for k in range(start_idx, end_idx + 1)],
                "cut_after_phrase_id": str(phrase_units[end_idx].get("id") or ""),
            }
        )

    idx = 0
    prev_window_duration = 0.0
    while idx < len(phrase_units):
        start = float(phrase_units[idx].get("t0") or 0.0)
        best_j = idx
        best_score = -1e9
        for j in range(idx, len(phrase_units)):
            end = float(phrase_units[j].get("t1") or start)
            span = end - start
            if span <= 0:
                continue
            if span > hard_max and j > idx:
                break
            center_target = (target_min + target_max) * 0.5
            closeness = -abs(span - center_target)
            in_target_bonus = 1.2 if target_min <= span <= target_max else 0.0
            near_hard_cap_penalty = -0.8 if span > target_max else 0.0
            semantic_bonus = 0.35 if str(phrase_units[j].get("semantic_weight") or "") == "high" else 0.0
            equal_prev_penalty = -0.8 if prev_window_duration > 0 and abs(span - prev_window_duration) <= 0.1 else 0.0
            near_four_run_penalty = (
                -0.45 if prev_window_duration > 0 and abs(prev_window_duration - 4.0) <= 0.1 and abs(span - 4.0) <= 0.1 else 0.0
            )
            score = closeness + in_target_bonus + near_hard_cap_penalty + semantic_bonus + equal_prev_penalty + near_four_run_penalty
            if span >= target_min and score > best_score:
                best_score = score
                best_j = j

        if best_j < idx:
            best_j = idx
        _append_window(idx, best_j)
        if windows:
            prev_window_duration = float(windows[-1].get("duration_sec") or 0.0)
        idx = best_j + 1

    # Aggressive post-split if any window is over hard cap: split only on phrase boundaries (never mid-word).
    normalized: list[dict[str, Any]] = []
    for win in windows:
        span = float(win.get("duration_sec") or 0.0)
        if span <= hard_max:
            normalized.append(win)
            continue
        phrase_ids = [str(item) for item in _safe_list(win.get("phrase_ids")) if str(item)]
        if len(phrase_ids) <= 1:
            normalized.append(win)
            continue
        current: list[str] = []
        current_t0 = float(win.get("t0") or 0.0)
        for phrase_id in phrase_ids:
            phrase = next((item for item in phrase_units if str(item.get("id") or "") == phrase_id), None)
            if not phrase:
                continue
            proposed = current + [phrase_id]
            proposed_t1 = float(phrase.get("t1") or current_t0)
            proposed_span = proposed_t1 - current_t0
            if current and proposed_span > hard_max:
                end_phrase = next((item for item in phrase_units if str(item.get("id") or "") == current[-1]), None)
                end_t1 = float((end_phrase or {}).get("t1") or current_t0)
                normalized.append(
                    {
                        "id": f"sc_{len(normalized) + 1}",
                        "t0": round(current_t0, 3),
                        "t1": round(end_t1, 3),
                        "duration_sec": round(max(0.0, end_t1 - current_t0), 3),
                        "phrase_ids": current,
                        "cut_after_phrase_id": str(current[-1]),
                    }
                )
                current = [phrase_id]
                current_t0 = float(phrase.get("t0") or current_t0)
            else:
                current = proposed
        if current:
            end_phrase = next((item for item in phrase_units if str(item.get("id") or "") == current[-1]), None)
            end_t1 = float((end_phrase or {}).get("t1") or current_t0)
            normalized.append(
                {
                    "id": f"sc_{len(normalized) + 1}",
                    "t0": round(current_t0, 3),
                    "t1": round(end_t1, 3),
                    "duration_sec": round(max(0.0, end_t1 - current_t0), 3),
                    "phrase_ids": current,
                    "cut_after_phrase_id": str(current[-1]),
                }
            )
    def _repair_short_final_tail(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(candidates) < 2:
            return candidates
        repaired = [dict(item) for item in candidates]
        last = repaired[-1]
        prev = repaired[-2]
        last_span = float(last.get("duration_sec") or 0.0)
        min_tail_sec = min(2.0, max(0.0, target_min - 0.1))
        if last_span >= min_tail_sec and last_span >= max(0.0, target_min - 0.8):
            return repaired
        prev_t0 = float(prev.get("t0") or 0.0)
        last_t1 = float(last.get("t1") or prev_t0)
        if last_t1 <= prev_t0:
            return repaired
        merged_phrase_ids = [
            *[str(item) for item in _safe_list(prev.get("phrase_ids")) if str(item)],
            *[str(item) for item in _safe_list(last.get("phrase_ids")) if str(item)],
        ]
        prev["t1"] = round(last_t1, 3)
        prev["duration_sec"] = round(max(0.0, last_t1 - prev_t0), 3)
        prev["phrase_ids"] = list(dict.fromkeys(merged_phrase_ids))
        prev["cut_after_phrase_id"] = str(last.get("cut_after_phrase_id") or prev.get("cut_after_phrase_id") or "")
        repaired = repaired[:-1]
        for idx, row in enumerate(repaired, start=1):
            row["id"] = f"sc_{idx}"
        return repaired

    if normalized:
        return _repair_short_final_tail(normalized)
    return _repair_short_final_tail(windows)


def _to_word_rows(alignment: dict[str, Any], duration_sec: float) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for item in _safe_list(alignment.get("words")):
        if not isinstance(item, dict):
            continue
        t0 = _clamp_time(_to_float(item.get("t0"), 0.0), duration_sec)
        t1 = _clamp_time(_to_float(item.get("t1"), t0), duration_sec)
        if t1 - t0 < 0.01:
            continue
        rows.append({"t0": round(t0, 3), "t1": round(t1, 3)})
    return rows


def _align_cut_to_word_boundary(cut_t: float, word_rows: list[dict[str, float]], duration_sec: float) -> float:
    cut = _clamp_time(cut_t, duration_sec)
    for row in word_rows:
        w0 = float(row.get("t0") or 0.0)
        w1 = float(row.get("t1") or w0)
        if w0 < cut < w1:
            if abs(cut - w0) <= abs(w1 - cut):
                return round(w0, 3)
            return round(w1, 3)
    return round(cut, 3)


def _repair_scene_slot_boundaries(
    raw_slots: list[dict[str, Any]],
    *,
    duration_sec: float,
    word_rows: list[dict[str, float]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    repaired: list[dict[str, Any]] = []
    stats = {
        "dropped_near_zero": 0,
        "boundary_repaired": 0,
        "word_boundary_repaired": 0,
        "orphan_tail_merged": 0,
    }
    prev_t1 = 0.0
    for row in raw_slots:
        t0 = _clamp_time(_to_float(row.get("t0"), prev_t1), duration_sec)
        t1 = _clamp_time(_to_float(row.get("t1"), t0), duration_sec)
        if repaired:
            t0 = round(prev_t1, 3)
            if abs(t0 - _to_float(row.get("t0"), t0)) >= 0.001:
                stats["boundary_repaired"] += 1
        if word_rows:
            aligned_t1 = _align_cut_to_word_boundary(t1, word_rows, duration_sec)
            if abs(aligned_t1 - t1) >= 0.001:
                stats["word_boundary_repaired"] += 1
            t1 = aligned_t1
        if t1 <= t0:
            stats["dropped_near_zero"] += 1
            continue
        span = t1 - t0
        if span < 0.1:
            stats["dropped_near_zero"] += 1
            continue
        fixed = dict(row)
        fixed["t0"] = round(t0, 3)
        fixed["t1"] = round(t1, 3)
        fixed["duration_sec"] = round(span, 3)
        repaired.append(fixed)
        prev_t1 = float(fixed["t1"])
    if len(repaired) >= 2:
        tail_span = float(repaired[-1].get("duration_sec") or 0.0)
        if tail_span < 0.5:
            repaired[-2]["t1"] = round(float(repaired[-1].get("t1") or repaired[-2].get("t1") or 0.0), 3)
            repaired[-2]["duration_sec"] = round(
                max(0.0, float(repaired[-2].get("t1") or 0.0) - float(repaired[-2].get("t0") or 0.0)), 3
            )
            repaired[-2]["phrase_ids"] = list(
                dict.fromkeys(
                    [str(item) for item in _safe_list(repaired[-2].get("phrase_ids")) if str(item)]
                    + [str(item) for item in _safe_list(repaired[-1].get("phrase_ids")) if str(item)]
                )
            )
            repaired.pop()
            stats["orphan_tail_merged"] += 1
    for idx, slot in enumerate(repaired, start=1):
        slot["id"] = f"slot_{idx}"
        slot["duration_sec"] = round(max(0.0, float(slot.get("t1") or 0.0) - float(slot.get("t0") or 0.0)), 3)
    return repaired, stats


def _build_scene_slots(
    *,
    audio_map: dict[str, Any],
    analysis: dict[str, Any],
    duration_sec: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    phrase_units = [item for item in _safe_list(audio_map.get("phrase_units")) if isinstance(item, dict)]
    if not phrase_units:
        return [], {"audio_map_scene_slots_repair_stats": {}}
    phrase_index = {str(item.get("id") or ""): item for item in phrase_units}
    ordered_phrases = sorted(phrase_units, key=lambda item: (_to_float(item.get("t0"), 0.0), _to_float(item.get("t1"), 0.0)))
    seed_rows = [item for item in _safe_list(audio_map.get("scene_candidate_windows")) if isinstance(item, dict)] or phrase_units

    def _slot_phrase_overlap(slot_t0: float, slot_t1: float, phrase: dict[str, Any]) -> float:
        phrase_t0 = _to_float(phrase.get("t0"), 0.0)
        phrase_t1 = _to_float(phrase.get("t1"), phrase_t0)
        return max(0.0, min(slot_t1, phrase_t1) - max(slot_t0, phrase_t0))

    def _intersected_phrase_rows(slot_t0: float, slot_t1: float) -> list[dict[str, Any]]:
        slot_span = max(0.001, slot_t1 - slot_t0)
        intersected: list[dict[str, Any]] = []
        for phrase in ordered_phrases:
            phrase_t0 = _to_float(phrase.get("t0"), 0.0)
            phrase_t1 = _to_float(phrase.get("t1"), phrase_t0)
            phrase_span = max(0.001, phrase_t1 - phrase_t0)
            overlap = _slot_phrase_overlap(slot_t0, slot_t1, phrase)
            if overlap <= 0.0:
                continue
            if overlap >= 0.1 or overlap / slot_span >= 0.18 or overlap / phrase_span >= 0.18:
                intersected.append(phrase)
        return intersected

    def _nearest_phrase_row(slot_t0: float, slot_t1: float) -> dict[str, Any] | None:
        if not ordered_phrases:
            return None
        center = (slot_t0 + slot_t1) / 2.0
        nearest: dict[str, Any] | None = None
        nearest_dist = float("inf")
        for phrase in ordered_phrases:
            phrase_t0 = _to_float(phrase.get("t0"), 0.0)
            phrase_t1 = _to_float(phrase.get("t1"), phrase_t0)
            phrase_center = (phrase_t0 + phrase_t1) / 2.0
            dist = abs(phrase_center - center)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = phrase
        return nearest

    raw_slots: list[dict[str, Any]] = []
    for seed in seed_rows:
        phrase_ids = [str(item) for item in _safe_list(seed.get("phrase_ids")) if str(item)]
        if not phrase_ids and str(seed.get("id") or "").startswith("ph_"):
            phrase_ids = [str(seed.get("id") or "")]
        resolved_phrases = [phrase_index[item] for item in phrase_ids if item in phrase_index]
        t0 = _to_float(seed.get("t0"), 0.0)
        t1 = _to_float(seed.get("t1"), t0)
        if resolved_phrases:
            t0 = _to_float(resolved_phrases[0].get("t0"), t0)
            t1 = _to_float(resolved_phrases[-1].get("t1"), t1)
        primary_text = ""
        for phrase in resolved_phrases:
            text = str(phrase.get("text") or "").strip()
            if text:
                primary_text = text[:280]
                break
        raw_slots.append(
            {
                "id": str(seed.get("id") or ""),
                "t0": round(_clamp_time(t0, duration_sec), 3),
                "t1": round(_clamp_time(t1, duration_sec), 3),
                "duration_sec": round(max(0.0, t1 - t0), 3),
                "phrase_ids": phrase_ids,
                "primary_phrase_text": primary_text,
            }
        )

    word_rows = _to_word_rows(_safe_dict(audio_map.get("transcript_alignment")), duration_sec)
    slots, repair_stats = _repair_scene_slot_boundaries(raw_slots, duration_sec=duration_sec, word_rows=word_rows)
    if not slots:
        return [], {"audio_map_scene_slots_repair_stats": repair_stats}
    def _hydrate_slot_phrase_data(slot: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], int, str, bool]:
        t0 = float(slot.get("t0") or 0.0)
        t1 = float(slot.get("t1") or t0)
        existing_ids = [str(item) for item in _safe_list(slot.get("phrase_ids")) if str(item)]
        slot_phrases_by_id = [phrase_index[item] for item in existing_ids if item in phrase_index]
        slot_phrase_rows = slot_phrases_by_id + _intersected_phrase_rows(t0, t1)
        if not slot_phrase_rows:
            nearest_phrase = _nearest_phrase_row(t0, t1)
            if nearest_phrase is not None:
                slot_phrase_rows = [nearest_phrase]

        deduped_phrases: list[dict[str, Any]] = []
        seen_phrase_ids: set[str] = set()
        for phrase in sorted(
            slot_phrase_rows,
            key=lambda item: (_to_float(item.get("t0"), 0.0), _to_float(item.get("t1"), 0.0), str(item.get("id") or "")),
        ):
            phrase_id = str(phrase.get("id") or "")
            if not phrase_id or phrase_id in seen_phrase_ids:
                continue
            deduped_phrases.append(phrase)
            seen_phrase_ids.add(phrase_id)

        slot_phrases = deduped_phrases
        phrase_ids = [str(item.get("id") or "") for item in slot_phrases if str(item.get("id") or "")]
        word_count = sum(_phrase_word_count(phrase) for phrase in slot_phrases)
        primary_phrase_text = ""
        for phrase in slot_phrases:
            text = str(phrase.get("text") or "").strip()
            if text:
                primary_phrase_text = text[:280]
                break
        is_instrumental_tail_slot = bool(slot_phrases) and all(
            _is_instrumental_tail_marker_text(str(phrase.get("text") or "")) for phrase in slot_phrases
        )
        return slot_phrases, phrase_ids, word_count, primary_phrase_text, is_instrumental_tail_slot

    for slot in slots:
        slot_phrases, phrase_ids, word_count, primary_phrase_text, is_instrumental_tail_slot = _hydrate_slot_phrase_data(slot)
        slot["_hydrated_phrases"] = slot_phrases
        slot["phrase_ids"] = phrase_ids
        slot["phrase_count"] = len(slot_phrases)
        slot["word_count"] = word_count
        slot["primary_phrase_text"] = primary_phrase_text
        slot["_is_instrumental_tail_slot"] = is_instrumental_tail_slot

    if len(slots) >= 2:
        last = slots[-1]
        prev = slots[-2]
        last_t0 = float(last.get("t0") or 0.0)
        last_t1 = float(last.get("t1") or last_t0)
        last_span = max(0.0, last_t1 - last_t0)
        marker_text_only = bool(last.get("_hydrated_phrases")) and bool(last.get("_is_instrumental_tail_slot"))
        tail_has_no_words = int(last.get("word_count") or 0) == 0
        if 0.0 < last_span <= 1.2 and marker_text_only and tail_has_no_words:
            prev_t0 = float(prev.get("t0") or 0.0)
            prev["t1"] = round(last_t1, 3)
            prev["duration_sec"] = round(max(0.0, last_t1 - prev_t0), 3)
            prev["phrase_ids"] = list(
                dict.fromkeys(
                    [str(item) for item in _safe_list(prev.get("phrase_ids")) if str(item)]
                    + [str(item) for item in _safe_list(last.get("phrase_ids")) if str(item)]
                )
            )
            slots.pop()
            repair_stats["orphan_tail_merged"] = int(repair_stats.get("orphan_tail_merged", 0)) + 1
            for slot_idx, row in enumerate(slots, start=1):
                row["id"] = f"slot_{slot_idx}"
            for slot in slots:
                slot_phrases, phrase_ids, word_count, primary_phrase_text, is_instrumental_tail_slot = _hydrate_slot_phrase_data(slot)
                slot["_hydrated_phrases"] = slot_phrases
                slot["phrase_ids"] = phrase_ids
                slot["phrase_count"] = len(slot_phrases)
                slot["word_count"] = word_count
                slot["primary_phrase_text"] = primary_phrase_text
                slot["_is_instrumental_tail_slot"] = is_instrumental_tail_slot

    beats = _float_points(analysis.get("beats"), duration_sec)
    energy_peaks = _float_points(analysis.get("energyPeaks"), duration_sec)
    vocal_phrases = [item for item in _safe_list(analysis.get("vocalPhrases")) if isinstance(item, dict)]
    alignment = _safe_dict(audio_map.get("transcript_alignment"))
    transcript_available = bool(str(alignment.get("transcript_text") or "").strip() or _safe_list(alignment.get("words")))
    diagnostics_notes: list[str] = []
    if not beats:
        diagnostics_notes.append("beat_alignment_score is approximate: beats unavailable")
    if not vocal_phrases:
        diagnostics_notes.append("vocal_ratio is approximate: vocal phrases unavailable")

    for idx, slot in enumerate(slots):
        t0 = float(slot.get("t0") or 0.0)
        t1 = float(slot.get("t1") or t0)
        span = max(0.001, t1 - t0)
        slot_phrases = [item for item in _safe_list(slot.get("_hydrated_phrases")) if isinstance(item, dict)]
        phrase_ids = [str(item) for item in _safe_list(slot.get("phrase_ids")) if str(item)]
        word_count = int(slot.get("word_count") or 0)
        words_per_sec = word_count / span if span > 0 else 0.0
        primary_phrase_text = str(slot.get("primary_phrase_text") or "")
        is_instrumental_tail_slot = bool(slot.get("_is_instrumental_tail_slot"))

        vocal_overlap = 0.0
        for vp in vocal_phrases:
            v0 = _clamp_time(_to_float(vp.get("start"), 0.0), duration_sec)
            v1 = _clamp_time(_to_float(vp.get("end"), v0), duration_sec)
            vocal_overlap += max(0.0, min(t1, v1) - max(t0, v0))
        if vocal_phrases:
            vocal_ratio = round(max(0.0, min(1.0, vocal_overlap / span)), 4)
        else:
            phrase_coverage = max(0.0, min(1.0, sum(_slot_phrase_overlap(t0, t1, phrase) for phrase in slot_phrases) / span))
            has_phrase_text = bool(primary_phrase_text)
            if is_instrumental_tail_slot:
                fallback_vocal_ratio = 0.03 + 0.07 * phrase_coverage
                vocal_ratio = round(max(0.0, min(0.12, fallback_vocal_ratio)), 4)
            elif transcript_available and (phrase_ids or has_phrase_text):
                fallback_vocal_ratio = 0.45 + 0.35 * phrase_coverage + 0.20 * min(1.0, words_per_sec / 3.2)
                vocal_ratio = round(max(0.0, min(0.95, fallback_vocal_ratio)), 4)
            elif has_phrase_text:
                fallback_vocal_ratio = 0.22 + 0.33 * phrase_coverage + 0.15 * min(1.0, words_per_sec / 3.2)
                vocal_ratio = round(max(0.0, min(0.7, fallback_vocal_ratio)), 4)
            else:
                vocal_ratio = 0.0
        phrase_density = len(slot_phrases) / span
        energy_score = round(max(0.0, min(1.0, 0.55 * vocal_ratio + 0.25 * min(1.0, words_per_sec / 3.0) + 0.2 * min(1.0, phrase_density))), 4)
        energy_variance = round(max(0.0, min(1.0, min(1.0, len(slot_phrases) / 3.0) * (0.4 + 0.6 * min(1.0, words_per_sec / 3.5)))), 4)

        slot_beats = [b for b in beats if t0 <= b <= t1]
        sync_points = sorted({round(item, 3) for item in ([b for b in slot_beats] + [p for p in energy_peaks if t0 <= p <= t1])})
        sync_points = sync_points[:8]
        rhythmic_intensity = round(max(0.0, min(1.0, len(sync_points) / max(1.0, span * 1.2))), 4)
        if beats:
            nearest_beat_dist = min(abs(t1 - beat) for beat in beats)
            beat_alignment_score = round(max(0.0, min(1.0, 1.0 - nearest_beat_dist / 0.25)), 4)
        else:
            beat_alignment_score = round(max(0.0, 1.0 - rhythmic_intensity * 0.5), 4)

        short_slot_warning = span < 2.8
        long_slot_warning = span > 8.0
        opening_risk = idx == 0 and (short_slot_warning or energy_score < 0.35 or vocal_ratio < 0.2)
        high_density_warning = words_per_sec > 3.2
        low_vocal_confidence = vocal_ratio > 0.2 and word_count == 0 and not word_rows
        beat_misalignment_warning = beat_alignment_score < 0.35 and len(slot_beats) >= 2
        orphan_tail_risk = idx == len(slots) - 1 and 0.5 <= span < 1.2

        merge_priority = "low"
        if short_slot_warning and (high_density_warning or idx == len(slots) - 1):
            merge_priority = "high"
        elif short_slot_warning or beat_misalignment_warning:
            merge_priority = "medium"
        hold_candidate = span >= 5.2 and energy_variance <= 0.42
        tail_ok = idx == len(slots) - 1 and span >= 1.8 and not orphan_tail_risk
        afterimage_candidate = idx == len(slots) - 1 and span >= 2.4 and energy_score <= 0.65

        slot["audio_features"] = {
            "vocal_ratio": vocal_ratio,
            "energy_score": energy_score,
            "energy_variance": energy_variance,
            "rhythmic_intensity": rhythmic_intensity,
            "sync_points": sync_points,
            "beat_alignment_score": beat_alignment_score,
        }
        slot["integrity_flags"] = {
            "short_slot_warning": short_slot_warning,
            "long_slot_warning": long_slot_warning,
            "opening_risk": opening_risk,
            "high_density_warning": high_density_warning,
            "low_vocal_confidence": low_vocal_confidence,
            "beat_misalignment_warning": beat_misalignment_warning,
            "orphan_tail_risk": orphan_tail_risk,
        }
        slot["hints"] = {
            "vocal_focus_potential": round(max(0.0, min(1.0, 0.75 * vocal_ratio + 0.25 * (1.0 - rhythmic_intensity))), 4),
            "motion_potential": round(max(0.0, min(1.0, 0.65 * rhythmic_intensity + 0.35 * energy_variance)), 4),
            "tail_ok": tail_ok,
            "afterimage_candidate": afterimage_candidate,
            "hold_candidate": hold_candidate,
            "dialogue_candidate": bool(vocal_ratio >= 0.55 and rhythmic_intensity <= 0.45),
            "merge_candidate_priority": merge_priority,
        }
        slot["phrase_ids"] = phrase_ids
        slot["phrase_count"] = len(slot_phrases)
        slot["word_count"] = word_count
        slot["primary_phrase_text"] = primary_phrase_text
        slot.pop("_hydrated_phrases", None)
        slot.pop("_is_instrumental_tail_slot", None)

    diagnostics_patch = {
        "audio_map_scene_slots_repair_stats": repair_stats,
        "audio_map_scene_slot_feature_notes": diagnostics_notes,
    }
    return slots, diagnostics_patch


def _max_equal_duration_streak(durations: list[float], *, tolerance: float = 0.08) -> int:
    if not durations:
        return 0
    best = 1
    current = 1
    for idx in range(1, len(durations)):
        if abs(durations[idx] - durations[idx - 1]) <= tolerance:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def _audio_map_grid_metrics(audio_map: dict[str, Any]) -> dict[str, Any]:
    phrase_durations = [
        float(item.get("duration_sec") or max(0.0, float(item.get("t1") or 0.0) - float(item.get("t0") or 0.0)))
        for item in _safe_list(audio_map.get("phrase_units"))
        if isinstance(item, dict)
    ]
    scene_durations = [
        float(item.get("duration_sec") or max(0.0, float(item.get("t1") or 0.0) - float(item.get("t0") or 0.0)))
        for item in _safe_list(audio_map.get("scene_candidate_windows"))
        if isinstance(item, dict)
    ]
    phrase_near_4 = [d for d in phrase_durations if abs(d - 4.0) <= 0.15]
    scene_near_4 = [d for d in scene_durations if abs(d - 4.0) <= 0.15]
    equal_phrase_pairs = sum(
        1 for idx in range(1, len(phrase_durations)) if abs(phrase_durations[idx] - phrase_durations[idx - 1]) <= 0.08
    )
    max_equal_phrase_streak = _max_equal_duration_streak(phrase_durations)
    max_equal_scene_streak = _max_equal_duration_streak(scene_durations)
    phrase_near_4_ratio = (len(phrase_near_4) / len(phrase_durations)) if phrase_durations else 0.0
    scene_near_4_ratio = (len(scene_near_4) / len(scene_durations)) if scene_durations else 0.0
    grid_like = bool(
        (len(phrase_durations) >= 4 and phrase_near_4_ratio >= 0.72 and max_equal_phrase_streak >= 3)
        or (len(scene_durations) >= 4 and scene_near_4_ratio >= 0.72 and max_equal_scene_streak >= 3)
    )
    return {
        "phrase_near_4_sec_count": len(phrase_near_4),
        "phrase_near_4_sec_ratio": round(phrase_near_4_ratio, 3),
        "equal_phrase_duration_adjacent_pairs": equal_phrase_pairs,
        "max_equal_phrase_duration_streak": max_equal_phrase_streak,
        "scene_near_4_sec_count": len(scene_near_4),
        "scene_near_4_sec_ratio": round(scene_near_4_ratio, 3),
        "max_equal_scene_duration_streak": max_equal_scene_streak,
        "audio_map_grid_like_segmentation": grid_like,
    }


def _validate_audio_map_soft(audio_map: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    duration = _coerce_duration_sec(audio_map.get("duration_sec"))
    if duration <= 0:
        return ["duration_missing"]

    phrase_units = [item for item in _safe_list(audio_map.get("phrase_units")) if isinstance(item, dict)]
    scene_windows = [item for item in _safe_list(audio_map.get("scene_candidate_windows")) if isinstance(item, dict)]
    if not phrase_units:
        flags.append("phrase_units_missing")
    if not scene_windows:
        flags.append("scene_candidate_windows_missing")

    prev_phrase_t1 = -1.0
    for idx, unit in enumerate(phrase_units):
        t0 = float(unit.get("t0") or 0.0)
        t1 = float(unit.get("t1") or 0.0)
        if t1 <= t0:
            flags.append(f"phrase_non_positive_span_{idx}")
        if prev_phrase_t1 >= 0 and t0 + 0.12 < prev_phrase_t1:
            flags.append(f"phrase_overlap_{idx}")
        prev_phrase_t1 = max(prev_phrase_t1, t1)
        if (t1 - t0) > 12.0:
            flags.append(f"phrase_too_long_{idx}")

    prev_scene_t1 = -1.0
    for idx, window in enumerate(scene_windows):
        t0 = float(window.get("t0") or 0.0)
        t1 = float(window.get("t1") or 0.0)
        if t1 <= t0:
            flags.append(f"scene_non_positive_span_{idx}")
        if prev_scene_t1 >= 0 and t0 + 0.2 < prev_scene_t1:
            flags.append(f"scene_overlap_{idx}")
        prev_scene_t1 = max(prev_scene_t1, t1)
        if (t1 - t0) > 12.0:
            flags.append(f"scene_too_long_{idx}")

    if scene_windows:
        scene_start = float(scene_windows[0].get("t0") or 0.0)
        scene_end = float(scene_windows[-1].get("t1") or 0.0)
        if scene_start > 1.5:
            flags.append("scene_start_gap_large")
        if duration - scene_end > 1.8:
            flags.append("scene_end_gap_large")
    return sorted(set(flags))


def _build_phrase_first_audio_map(
    duration_sec: float,
    story_core: dict[str, Any],
    analysis: dict[str, Any],
    transcript_text: str,
) -> dict[str, Any] | None:
    phrase_units = _build_phrase_units_from_approximate_alignment(analysis, duration_sec, transcript_text)
    if not phrase_units:
        return None
    base_map = _build_audio_map_from_dynamics(duration_sec, story_core, analysis, analysis_mode="approximate_phrase_grouping_v1")
    scene_windows = _build_scene_candidate_windows(phrase_units, duration_sec)
    phrase_boundaries = [
        round(float(item.get("t1") or 0.0), 3)
        for item in phrase_units
        if 0.0 < float(item.get("t1") or 0.0) < duration_sec
    ]
    base_map["analysis_mode"] = "approximate_phrase_grouping_v1"
    base_map["transcript_available"] = True
    base_map["phrase_units"] = phrase_units
    base_map["scene_candidate_windows"] = scene_windows
    base_map["phrase_endpoints_sec"] = sorted(set(phrase_boundaries))
    base_map["candidate_cut_points_sec"] = sorted(set(phrase_boundaries))
    base_map["cut_policy"] = {
        "min_scene_sec": 3,
        "target_scene_sec_min": 3,
        "target_scene_sec_max": 6,
        "hard_max_scene_sec": 8,
        "no_mid_word_cut": True,
        "prefer_phrase_endings": True,
        "prefer_semantic_boundaries": True,
    }
    base_map["audio_dynamics_summary"] = {
        **_safe_dict(base_map.get("audio_dynamics_summary")),
        "pause_points_count": len(_safe_list(analysis.get("pausePoints"))),
        "phrase_points_count": len(phrase_boundaries),
        "energy_peaks_count": len(_safe_list(analysis.get("energyPeaks"))),
        "detected_sections_count": len(_safe_list(analysis.get("sections"))),
    }
    return base_map


def _build_audio_map_from_real_alignment(
    duration_sec: float,
    story_core: dict[str, Any],
    analysis: dict[str, Any],
    alignment: dict[str, Any],
) -> dict[str, Any] | None:
    phrase_units = _build_phrase_units_from_real_alignment(alignment, duration_sec)
    if not phrase_units:
        return None
    base_map = _build_audio_map_from_dynamics(duration_sec, story_core, analysis, analysis_mode="transcript_alignment_v2")
    scene_windows = _build_scene_candidate_windows(phrase_units, duration_sec)
    phrase_boundaries = [round(float(item.get("t1") or 0.0), 3) for item in phrase_units if 0.0 < float(item.get("t1") or 0.0) < duration_sec]
    no_split_ranges = []
    for phrase in phrase_units:
        words = [word for word in _safe_list(phrase.get("words")) if isinstance(word, dict)]
        if len(words) >= 3:
            center_idx = len(words) // 2
            center_word = words[center_idx]
            no_split_ranges.append(
                {
                    "t0": round(_clamp_time(float(center_word.get("t0") or 0.0) - 0.12, duration_sec), 3),
                    "t1": round(_clamp_time(float(center_word.get("t1") or 0.0) + 0.12, duration_sec), 3),
                    "reason": f"phrase_core:{phrase.get('id')}",
                }
            )
    base_map["analysis_mode"] = "transcript_alignment_v2"
    base_map["transcript_available"] = bool(
        str(alignment.get("transcript_text") or "").strip() or _safe_list(alignment.get("words"))
    )
    base_map["transcript_text"] = str(alignment.get("transcript_text") or "").strip()
    base_map["transcript_alignment"] = {
        "transcript_text": str(alignment.get("transcript_text") or "").strip(),
        "words": _safe_list(alignment.get("words")),
        "phrases": _safe_list(alignment.get("phrases")),
        "source": str(alignment.get("source") or ""),
    }
    base_map["phrase_units"] = phrase_units
    base_map["scene_candidate_windows"] = scene_windows
    base_map["phrase_endpoints_sec"] = sorted(set(phrase_boundaries))
    base_map["candidate_cut_points_sec"] = sorted(set(phrase_boundaries))
    base_map["no_split_ranges"] = sorted(
        [item for item in _safe_list(base_map.get("no_split_ranges")) if isinstance(item, dict)] + no_split_ranges,
        key=lambda item: float(item.get("t0") or 0.0),
    )
    base_map["cut_policy"] = {
        "min_scene_sec": 3,
        "target_scene_sec_min": 3,
        "target_scene_sec_max": 6,
        "hard_max_scene_sec": 8,
        "max_scene_sec_forbidden": 8,
        "no_mid_word_cut": True,
        "prefer_phrase_endings": True,
        "prefer_semantic_boundaries": True,
        "requires_word_aligned_cut_points": True,
        "alignment_source": str(alignment.get("source") or "unknown"),
    }
    return base_map


def _add_scored_candidate(
    score_by_point: dict[float, float],
    point: float,
    score: float,
    duration_sec: float,
    *,
    edge_padding_sec: float = 0.65,
) -> None:
    t = _clamp_time(point, duration_sec)
    if not (edge_padding_sec <= t <= max(0.0, duration_sec - edge_padding_sec)):
        return
    key = round(t, 3)
    score_by_point[key] = max(score_by_point.get(key, 0.0), float(score))


def _choose_section_boundaries(
    duration_sec: float,
    segment_count: int,
    score_by_point: dict[float, float],
) -> list[float]:
    if duration_sec <= 0:
        return [0.0]
    target_boundaries = max(0, segment_count - 1)
    if target_boundaries <= 0:
        return [0.0, round(duration_sec, 3)]
    min_gap = max(2.4, min(6.0, duration_sec / (segment_count * 1.8)))
    sorted_candidates = sorted(score_by_point.items(), key=lambda item: item[0])
    boundaries = [0.0]
    for idx in range(1, target_boundaries + 1):
        ideal = duration_sec * idx / segment_count
        best: tuple[float, float] | None = None
        for point, base_score in sorted_candidates:
            if point <= boundaries[-1] + min_gap or point >= duration_sec - min_gap:
                continue
            distance_penalty = abs(point - ideal) / max(0.001, duration_sec)
            score = base_score - distance_penalty
            if best is None or score > best[1]:
                best = (point, score)
        if best is None:
            fallback = round(_clamp_time(ideal, duration_sec), 3)
            if fallback <= boundaries[-1] + min_gap:
                fallback = round(min(duration_sec - min_gap, boundaries[-1] + min_gap), 3)
            boundaries.append(fallback)
        else:
            boundaries.append(round(best[0], 3))
    cleaned = [0.0]
    for value in sorted(set(boundaries[1:])):
        if value - cleaned[-1] >= min_gap:
            cleaned.append(value)
    if cleaned[-1] < duration_sec - min_gap * 0.5:
        cleaned.append(round(duration_sec, 3))
    else:
        cleaned[-1] = round(duration_sec, 3)
    if len(cleaned) < 2:
        return [0.0, round(duration_sec, 3)]
    return cleaned


def _build_audio_map_from_dynamics(
    duration_sec: float,
    story_core: dict[str, Any],
    analysis: dict[str, Any],
    *,
    analysis_mode: str,
) -> dict[str, Any]:
    duration = _coerce_duration_sec(duration_sec)
    segment_count = _segment_count_for_duration(duration)
    labels = _labels_for_segment_count(segment_count)
    moods = _mood_curve_from_story_core(story_core, segment_count)

    score_by_point: dict[float, float] = {}
    sections_analysis = _safe_list(analysis.get("sections"))
    pause_points = _float_points(analysis.get("pausePoints"), duration, min_t=0.15, max_t=max(0.0, duration - 0.15))
    phrase_points = _float_points(analysis.get("phraseBoundaries"), duration, min_t=0.15, max_t=max(0.0, duration - 0.15))
    energy_peaks = _float_points(analysis.get("energyPeaks"), duration, min_t=0.15, max_t=max(0.0, duration - 0.15))
    downbeats = _float_points(analysis.get("downbeats"), duration, min_t=0.15, max_t=max(0.0, duration - 0.15))

    for section in sections_analysis:
        t0 = _coerce_duration_sec(section.get("start"))
        t1 = _coerce_duration_sec(section.get("end"))
        if t1 - t0 < 0.9:
            continue
        _add_scored_candidate(score_by_point, t0, 0.62, duration)
        _add_scored_candidate(score_by_point, t1, 0.98, duration)
    for point in pause_points:
        _add_scored_candidate(score_by_point, point, 1.0, duration)
    for point in phrase_points:
        _add_scored_candidate(score_by_point, point, 0.72, duration)
    for point in energy_peaks:
        _add_scored_candidate(score_by_point, point, 0.55, duration)
    for point in downbeats:
        _add_scored_candidate(score_by_point, point, 0.42, duration)

    boundaries = _choose_section_boundaries(duration, segment_count, score_by_point)
    if len(boundaries) < 2:
        raise ValueError("audio_dynamics_v2_boundaries_missing")

    sections: list[dict[str, Any]] = []
    section_energies: list[float] = []
    for idx in range(len(boundaries) - 1):
        t0 = round(boundaries[idx], 3)
        t1 = round(boundaries[idx + 1], 3)
        section_mid = (t0 + t1) * 0.5
        density_score = 0.0
        for point in energy_peaks:
            if t0 <= point <= t1:
                density_score += 1.0
        for point in pause_points:
            if t0 <= point <= t1:
                density_score -= 0.5
        density_score += 0.15 * math.sin(section_mid / max(duration, 1.0) * math.pi)
        section_energies.append(density_score)
        sections.append(
            {
                "id": f"sec_{idx + 1}",
                "t0": t0,
                "t1": t1,
                "label": labels[idx] if idx < len(labels) else f"part_{idx + 1}",
                "mood": moods[idx] if idx < len(moods) else "neutral",
            }
        )

    if section_energies:
        low_thr = float(sorted(section_energies)[max(0, int(len(section_energies) * 0.3) - 1)])
        high_thr = float(sorted(section_energies)[min(len(section_energies) - 1, int(len(section_energies) * 0.7))])
    else:
        low_thr = high_thr = 0.0
    for idx, section in enumerate(sections):
        energy_value = section_energies[idx] if idx < len(section_energies) else 0.0
        if energy_value <= low_thr:
            section["energy"] = "low"
        elif energy_value >= high_thr:
            section["energy"] = "high"
        else:
            section["energy"] = "medium"

    phrase_endpoints = sorted(
        set(
            [
                point
                for point in (pause_points + phrase_points + [round(float(s.get("t1") or 0.0), 3) for s in sections])
                if 0.0 < point < duration
            ]
        )
    )

    no_split_ranges: list[dict[str, Any]] = []
    if duration > 0:
        no_split_ranges.append({"t0": 0.0, "t1": round(min(1.2, duration), 3), "reason": "intro_protection"})
        tail_start = round(max(0.0, duration - 1.2), 3)
        if tail_start < duration:
            no_split_ranges.append({"t0": tail_start, "t1": round(duration, 3), "reason": "outro_tail_protection"})
    for peak in energy_peaks:
        no_split_ranges.append(
            {
                "t0": round(max(0.0, peak - 0.28), 3),
                "t1": round(min(duration, peak + 0.28), 3),
                "reason": "energy_core_protection",
            }
        )

    candidate_cut_points: list[float] = []
    for point in sorted(score_by_point.keys()):
        if point <= 0.0 or point >= duration:
            continue
        inside_blocked = any(float(r.get("t0") or 0.0) <= point <= float(r.get("t1") or 0.0) for r in no_split_ranges)
        if inside_blocked:
            continue
        if score_by_point.get(point, 0.0) >= 0.58:
            candidate_cut_points.append(point)

    mood_progression = [
        {
            "t0": float(section.get("t0") or 0.0),
            "t1": float(section.get("t1") or 0.0),
            "mood": str(section.get("mood") or "neutral"),
            "energy": str(section.get("energy") or "medium"),
        }
        for section in sections
    ]
    energy_counts = {"low": 0, "medium": 0, "high": 0}
    for section in sections:
        energy = str(section.get("energy") or "medium").lower()
        if energy in energy_counts:
            energy_counts[energy] += 1
    dominant_energy = max(energy_counts.items(), key=lambda item: item[1])[0] if sections else "medium"

    lip_sync_ranges: list[dict[str, float]] = []
    content_hint = str(story_core.get("story_summary") or "").lower()
    if "speak" in content_hint or "sing" in content_hint or "vocal" in content_hint:
        for section in sections:
            if str(section.get("energy") or "").lower() in {"medium", "high"}:
                lip_sync_ranges.append({"t0": round(float(section.get("t0") or 0.0), 3), "t1": round(float(section.get("t1") or 0.0), 3)})

    phrase_units = _build_phrase_units_from_music_dynamics(duration, analysis)
    if phrase_units:
        phrase_endpoints = sorted(
            {
                round(float(item.get("t1") or 0.0), 3)
                for item in phrase_units
                if 0.0 < float(item.get("t1") or 0.0) < duration
            }
        )
    scene_windows = _build_scene_candidate_windows(phrase_units, duration) if phrase_units else []

    arc_short = str(story_core.get("global_arc") or "").strip() or "unknown_arc"
    return {
        "duration_sec": round(duration, 3),
        "analysis_mode": analysis_mode,
        "sections": sections,
        "phrase_endpoints_sec": phrase_endpoints,
        "phrase_units": phrase_units,
        "scene_candidate_windows": scene_windows,
        "no_split_ranges": no_split_ranges,
        "candidate_cut_points_sec": sorted(set(candidate_cut_points)),
        "pacing_profile": {
            "segment_count": len(sections),
            "dominant_energy": dominant_energy,
            "candidate_density": round(len(score_by_point) / max(duration, 1.0), 3),
        },
        "mood_progression": mood_progression,
        "audio_arc_summary": f"Audio map follows story_core arc '{arc_short}' and aligns sections to detected dynamics/pause anchors.",
        "section_summary": [f"{sec.get('label')}:{sec.get('energy')}/{sec.get('mood')}" for sec in sections],
        "lip_sync_candidate_ranges": lip_sync_ranges,
        "audio_dynamics_summary": {
            "pause_points_count": len(pause_points),
            "phrase_points_count": len(phrase_points),
            "energy_peaks_count": len(energy_peaks),
            "detected_sections_count": len(sections_analysis),
        },
    }


def _is_usable_audio_map(audio_map: dict[str, Any]) -> bool:
    if not isinstance(audio_map, dict):
        return False
    if str(audio_map.get("audio_map_version") or "").strip() == "1.1":
        segments = _safe_list(audio_map.get("segments"))
        duration = _coerce_duration_sec(audio_map.get("duration_sec"))
        return duration > 0 and bool(segments)
    duration = _coerce_duration_sec(audio_map.get("duration_sec"))
    sections = _safe_list(audio_map.get("sections"))
    return duration > 0 and bool(sections)


def _audio_map_story_core_guard_fingerprint(audio_map: dict[str, Any]) -> str:
    node = _safe_dict(audio_map)

    def _norm_rows(rows: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for idx, row in enumerate(_safe_list(rows), start=1):
            if not isinstance(row, dict):
                continue
            normalized.append(
                {
                    "id": str(row.get("id") or row.get("scene_id") or row.get("slot_id") or idx),
                    "t0": round(_to_float(row.get("t0"), 0.0), 3),
                    "t1": round(_to_float(row.get("t1"), 0.0), 3),
                }
            )
        return normalized

    payload = {
        "duration_sec": round(_coerce_duration_sec(node.get("duration_sec")), 3),
        "phrase_units_count": len(_safe_list(node.get("phrase_units"))),
        "scene_candidate_windows_count": len(_safe_list(node.get("scene_candidate_windows"))),
        "scene_slots_count": len(_safe_list(node.get("scene_slots"))),
        "phrase_units": _norm_rows(node.get("phrase_units")),
        "scene_candidate_windows": _norm_rows(node.get("scene_candidate_windows")),
        "scene_slots": _norm_rows(node.get("scene_slots")),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_audio_rows_for_music_video(rows: list[dict[str, Any]], duration_sec: float) -> list[dict[str, Any]]:
    duration = _coerce_duration_sec(duration_sec)
    normalized: list[dict[str, Any]] = []
    cursor = 0.0
    for row in sorted(rows, key=lambda item: (_to_float(item.get("t0"), 0.0), _to_float(item.get("t1"), 0.0))):
        t0 = round(_clamp_time(_to_float(row.get("t0"), 0.0), duration), 3)
        t1 = round(_clamp_time(_to_float(row.get("t1"), t0), duration), 3)
        t0 = max(t0, round(cursor, 3))
        if t1 <= t0:
            continue
        fixed = dict(row)
        fixed["t0"] = t0
        fixed["t1"] = t1
        fixed["duration_sec"] = round(max(0.0, t1 - t0), 3)
        normalized.append(fixed)
        cursor = t1
    return normalized


def _finalize_music_video_model_led_audio_map(audio_map: dict[str, Any], duration_sec: float) -> tuple[dict[str, Any], str]:
    duration = _coerce_duration_sec(duration_sec)
    if duration <= 0:
        return audio_map, "duration_missing"

    phrase_units_raw = [item for item in _safe_list(audio_map.get("phrase_units")) if isinstance(item, dict)]
    scene_windows_raw = [item for item in _safe_list(audio_map.get("scene_candidate_windows")) if isinstance(item, dict)]
    if not phrase_units_raw and not scene_windows_raw:
        return audio_map, "segmentation_units_missing"

    phrase_units = _normalize_audio_rows_for_music_video(phrase_units_raw, duration)
    scene_windows = _normalize_audio_rows_for_music_video(scene_windows_raw, duration)
    if not phrase_units and not scene_windows:
        return audio_map, "segmentation_units_invalid_after_normalization"

    coverage_rows = scene_windows or phrase_units
    first_t0 = _to_float(coverage_rows[0].get("t0"), 0.0) if coverage_rows else 0.0
    last_t1 = _to_float(coverage_rows[-1].get("t1"), 0.0) if coverage_rows else 0.0
    covered_span = max(0.0, last_t1 - first_t0)
    min_required_coverage = max(1.0, duration * 0.35)
    if covered_span < min_required_coverage:
        return audio_map, "segmentation_coverage_too_low"

    sections = [item for item in _safe_list(audio_map.get("sections")) if isinstance(item, dict)]
    normalized_sections = _normalize_audio_rows_for_music_video(sections, duration)
    if not normalized_sections:
        source_rows = scene_windows or phrase_units
        normalized_sections = [
            {
                "id": f"sec_{idx + 1}",
                "t0": float(item.get("t0") or 0.0),
                "t1": float(item.get("t1") or 0.0),
                "label": str(item.get("scene_function") or item.get("label") or f"part_{idx + 1}"),
                "energy": str(item.get("energy") or "medium"),
                "mood": str(item.get("mood") or "neutral"),
            }
            for idx, item in enumerate(source_rows)
        ]

    finalized = dict(audio_map)
    finalized["duration_sec"] = round(duration, 3)
    finalized["overall_duration_sec"] = round(duration, 3)
    finalized["phrase_units"] = phrase_units
    finalized["scene_candidate_windows"] = scene_windows
    finalized["sections"] = normalized_sections
    finalized["phrase_endpoints_sec"] = sorted(
        {
            round(_to_float(item.get("t1"), 0.0), 3)
            for item in phrase_units
            if 0.0 < _to_float(item.get("t1"), 0.0) < duration
        }
    )
    finalized["candidate_cut_points_sec"] = sorted(
        {
            round(_to_float(value, -1.0), 3)
            for value in _safe_list(finalized.get("candidate_cut_points_sec"))
            if 0.0 < _to_float(value, -1.0) < duration
        }
    )
    return finalized, ""


def _build_audio_map_from_gemini_payload(payload: dict[str, Any], duration_sec: float, *, analysis_mode: str) -> dict[str, Any]:
    duration = _coerce_duration_sec(duration_sec)
    phrase_units = [item for item in _safe_list(payload.get("phrase_units")) if isinstance(item, dict)]
    scene_windows = [item for item in _safe_list(payload.get("scene_candidate_windows")) if isinstance(item, dict)]
    phrase_endpoints = sorted(
        {
            round(_to_float(value, -1.0), 3)
            for value in _safe_list(payload.get("phrase_endpoints_sec"))
            if 0.0 < _to_float(value, -1.0) < duration
        }
    )
    if not phrase_endpoints and phrase_units:
        phrase_endpoints = sorted(
            {
                round(float(item.get("t1") or 0.0), 3)
                for item in phrase_units
                if 0.0 < float(item.get("t1") or 0.0) < duration
            }
        )
    return {
        "duration_sec": round(duration, 3),
        "analysis_mode": analysis_mode,
        "transcript_available": bool(payload.get("transcript_available")),
        "track_type": str(payload.get("track_type") or "unknown"),
        "overall_duration_sec": float(payload.get("overall_duration_sec") or duration),
        "global_notes": _safe_dict(payload.get("global_notes")),
        "sections": [item for item in _safe_list(payload.get("sections")) if isinstance(item, dict)],
        "phrase_endpoints_sec": phrase_endpoints,
        "phrase_units": phrase_units,
        "scene_candidate_windows": scene_windows,
        "candidate_cut_points_sec": [value for value in _safe_list(payload.get("candidate_cut_points_sec"))],
        "no_split_ranges": [item for item in _safe_list(payload.get("no_split_ranges")) if isinstance(item, dict)],
        "lip_sync_candidate_ranges": [item for item in _safe_list(payload.get("lip_sync_candidate_ranges")) if isinstance(item, dict)],
        "audio_map_alignment_source": "gemini_semantic_segmentation",
    }


def _summarize_audio_map_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sections = [item for item in _safe_list(payload.get("sections")) if isinstance(item, dict)]
    phrase_units = [item for item in _safe_list(payload.get("phrase_units")) if isinstance(item, dict)]
    scene_windows = [item for item in _safe_list(payload.get("scene_candidate_windows")) if isinstance(item, dict)]
    all_rows = sections + phrase_units + scene_windows
    t0_values = [_to_float(item.get("t0"), -1.0) for item in all_rows]
    t1_values = [_to_float(item.get("t1"), -1.0) for item in all_rows]
    first_t0 = min((v for v in t0_values if v >= 0.0), default=None)
    last_t1 = max((v for v in t1_values if v >= 0.0), default=None)
    return {
        "sections_count": len(sections),
        "phrase_units_count": len(phrase_units),
        "scene_candidate_windows_count": len(scene_windows),
        "phrase_endpoints_count": len(_safe_list(payload.get("phrase_endpoints_sec"))),
        "first_t0": round(first_t0, 3) if first_t0 is not None else None,
        "last_t1": round(last_t1, 3) if last_t1 is not None else None,
        "overall_duration_sec": _to_float(payload.get("overall_duration_sec"), 0.0),
    }


def _summarize_audio_map_v11(payload: dict[str, Any]) -> dict[str, Any]:
    segments = [item for item in _safe_list(payload.get("segments")) if isinstance(item, dict)]
    first_t0 = _to_float(_safe_dict(segments[0]).get("t0"), -1.0) if segments else -1.0
    last_t1 = _to_float(_safe_dict(segments[-1]).get("t1"), -1.0) if segments else -1.0
    return {
        "audio_map_version": str(payload.get("audio_map_version") or ""),
        "audio_id": str(payload.get("audio_id") or "")[:120],
        "segments_count": len(segments),
        "first_t0": round(first_t0, 3) if first_t0 >= 0 else None,
        "last_t1": round(last_t1, 3) if last_t1 >= 0 else None,
        "no_split_ranges_count": len(_safe_list(payload.get("no_split_ranges"))),
    }


def _build_legacy_compat_audio_payload_from_segments_v11(payload: dict[str, Any], *, duration_sec: float, analysis_mode: str) -> dict[str, Any]:
    """
    Build temporary legacy compatibility fields from AUDIO v1.1 segments.

    Canonical AUDIO source of truth is `segments[]` (Gemini-authored). Derived fields
    (`sections`, `phrase_units`, `scene_candidate_windows`, `candidate_cut_points_sec`)
    exist only as a deprecated compatibility bridge for downstream consumers that have
    not migrated to direct `segments[]` consumption yet.
    """
    segments = [item for item in _safe_list(payload.get("segments")) if isinstance(item, dict)]
    phrase_units: list[dict[str, Any]] = []
    scene_candidate_windows: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    phrase_endpoints: list[float] = []
    for idx, seg in enumerate(segments, start=1):
        t0 = round(_to_float(seg.get("t0"), 0.0), 3)
        t1 = round(_to_float(seg.get("t1"), t0), 3)
        if t1 <= t0:
            continue
        text = str(seg.get("transcript_slice") or "").strip()
        intensity = _to_float(seg.get("intensity"), 0.0)
        rhythmic_anchor = str(seg.get("rhythmic_anchor") or "none").strip().lower() or "none"
        if rhythmic_anchor not in {"beat", "drop", "transition", "none"}:
            rhythmic_anchor = "none"
        energy = "high" if intensity >= 0.67 else ("medium" if intensity >= 0.33 else "low")
        phrase_units.append(
            {
                "id": f"ph_{idx}",
                "t0": t0,
                "t1": t1,
                "duration_sec": round(max(0.0, t1 - t0), 3),
                "text": text,
                "word_count": len(text.split()),
                "semantic_weight": "medium",
            }
        )
        scene_candidate_windows.append(
            {
                "id": f"sc_{idx}",
                "t0": t0,
                "t1": t1,
                "duration_sec": round(max(0.0, t1 - t0), 3),
                "phrase_text": text,
                "transcript_confidence": "high" if text else "low",
                "cut_reason": rhythmic_anchor,
                "energy": energy,
                "scene_function": rhythmic_anchor if rhythmic_anchor in {"beat", "drop", "transition"} else "bridge",
                "no_mid_word_cut": True,
            }
        )
        sections.append({"id": f"sec_{idx}", "t0": t0, "t1": t1, "label": rhythmic_anchor or "segment", "energy": energy, "mood": "neutral"})
        if 0.0 < t1 < duration_sec:
            phrase_endpoints.append(round(t1, 3))

    no_split_ranges = []
    for row in _safe_list(payload.get("no_split_ranges")):
        item = _safe_dict(row)
        no_split_ranges.append(
            {
                "t0": round(_to_float(item.get("start"), 0.0), 3),
                "t1": round(_to_float(item.get("end"), 0.0), 3),
                "reason": "model_guardrail",
            }
        )

    return {
        "audio_map_version": "1.1",
        "audio_id": str(payload.get("audio_id") or ""),
        "duration_sec": round(duration_sec, 3),
        "analysis_mode": analysis_mode,
        "diagnostics": _safe_dict(payload.get("diagnostics")),
        "segments": segments,
        "sections": sections,
        "phrase_endpoints_sec": sorted(set(phrase_endpoints)),
        "phrase_units": phrase_units,
        "scene_candidate_windows": scene_candidate_windows,
        "no_split_ranges": no_split_ranges,
        "candidate_cut_points_sec": sorted(set(phrase_endpoints)),
        "transcript_available": bool(_safe_dict(payload.get("diagnostics")).get("transcript_used")),
        "audio_map_alignment_source": "gemini_audio_map_v1_1",
    }


def _run_audio_map_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    diagnostics = _safe_dict(package.get("diagnostics"))

    audio_url = str(input_pkg.get("audio_url") or "").strip()
    duration_sec = _coerce_duration_sec(input_pkg.get("audio_duration_sec"))
    content_type = str(input_pkg.get("content_type") or "").strip().lower() or "music_video"
    director_mode = _resolve_director_mode(input_pkg.get("director_mode"), content_type=content_type)
    story_core_mode = str(diagnostics.get("story_core_mode") or _detect_story_core_mode(input_pkg)).strip().lower() or "creative"

    diagnostics["audio_map_source_audio_url"] = audio_url
    diagnostics["audio_map_used_model"] = ""
    diagnostics["audio_map_analysis_mode"] = "audio_map_v1_1_strict"
    diagnostics["audio_map_used_fallback"] = False
    diagnostics["audio_map_phrase_mode"] = "audio_map_v1_1_strict"
    diagnostics["audio_map_segmentation_backend"] = "gemini"
    diagnostics["audio_map_segmentation_used_fallback"] = False
    diagnostics["audio_map_segmentation_validation_error"] = ""
    diagnostics["audio_map_segmentation_validation_errors"] = []
    diagnostics["audio_map_segmentation_prompt_version"] = ""
    diagnostics["audio_map_segmentation_error"] = ""
    diagnostics["audio_map_segmentation_error_detail"] = ""
    diagnostics["audio_map_segmentation_payload_summary"] = {}
    diagnostics["audio_map_segmentation_normalized_summary"] = {}
    diagnostics["audio_map_segmentation_retry_used"] = False
    diagnostics["audio_map_segmentation_retry_feedback"] = ""
    diagnostics["audio_map_segmentation_last_error_code"] = ""
    diagnostics["audio_map_segmentation_last_errors"] = []
    diagnostics["audio_segmentation_source_mode"] = "none"
    diagnostics["audio_segmentation_local_path_found"] = False
    diagnostics["audio_segmentation_inline_attempted"] = False
    diagnostics["audio_segmentation_inline_bytes_size"] = 0
    diagnostics["audio_segmentation_url_used"] = ""
    diagnostics["audio_segmentation_transport_error"] = ""
    diagnostics["audio_map_stage_branch"] = ""
    diagnostics["audio_map_primary_fallback_reason"] = ""
    diagnostics["audio_map_dynamics_error"] = ""
    diagnostics["audio_map_primary_source"] = "gemini"
    diagnostics["audio_map_model_led_segmentation"] = True
    diagnostics["audio_map_validation_flags"] = []
    diagnostics["audio_map_source_of_truth"] = "segments_v1_1"
    diagnostics["audio_map_legacy_compat_bridge"] = True
    diagnostics["audio_map_legacy_scene_slots_derived"] = False
    diagnostics["audio_map_legacy_scene_slots_deprecated"] = True
    diagnostics["audio_map_gap_sum_sec"] = 0.0
    diagnostics["audio_map_overlap_sum_sec"] = 0.0
    diagnostics["phrase_near_4_sec_count"] = 0
    diagnostics["phrase_near_4_sec_ratio"] = 0.0
    diagnostics["equal_phrase_duration_adjacent_pairs"] = 0
    diagnostics["max_equal_phrase_duration_streak"] = 0
    diagnostics["scene_near_4_sec_count"] = 0
    diagnostics["scene_near_4_sec_ratio"] = 0.0
    diagnostics["max_equal_scene_duration_streak"] = 0
    diagnostics["audio_map_grid_like_segmentation"] = False
    diagnostics["audio_map_music_signal_mode"] = "none"
    diagnostics["audio_map_dynamics_available"] = False
    diagnostics["transcript_available"] = False
    diagnostics["word_timestamp_count"] = 0
    diagnostics["phrase_unit_count"] = 0
    diagnostics["scene_candidate_count"] = 0
    package["diagnostics"] = diagnostics

    if duration_sec <= 0:
        raise RuntimeError("AUDIO_TIMING_VIOLATION:audio_duration_missing_or_invalid")

    transcript_text = _extract_audio_transcript_text(input_pkg)
    raw_analysis: dict[str, Any] = {}
    analysis_path = ""
    cleanup_path: str | None = None
    if audio_url:
        local_audio_path, local_resolution_debug = _resolve_local_audio_asset_path(
            audio_url=audio_url,
            input_payload=input_pkg,
            refs_inventory=refs_inventory,
        )
        _append_diag_event(
            package,
            "audio_map local audio resolution "
            f"audio_url={str(local_resolution_debug.get('audio_url') or '')} "
            f"asset_rel={str(local_resolution_debug.get('extracted_asset_relative_path') or '')} "
            f"local_path={str(local_resolution_debug.get('final_local_path') or '')} "
            f"exists={bool(local_resolution_debug.get('exists'))} "
            f"basename_fallback={bool(local_resolution_debug.get('fallback_to_basename_lookup'))}",
            stage_id="audio_map",
        )
        analysis_path, cleanup_path = _resolve_audio_analysis_path(local_audio_path or audio_url)
        if analysis_path:
            try:
                raw_analysis = analyze_audio(analysis_path, debug=False)
            except Exception as analysis_exc:  # noqa: BLE001
                diagnostics["audio_map_dynamics_error"] = str(analysis_exc)
                raw_analysis = {}
    diagnostics["audio_map_stage_branch"] = "gemini_strict_v11"

    def _validation_feedback(code: str, errors: list[str]) -> str:
        detail = "; ".join([str(item) for item in errors[:6]])
        prefix = "Hard boundary violation. " if code == "AUDIO_PLOT_LEAKAGE" else ""
        return f"{prefix}{code}: {detail}"[:1500]

    strict_result = None
    last_error_code = "AUDIO_SCHEMA_INVALID"
    last_errors: list[str] = []
    validation_feedback = ""
    retry_used = False
    try:
        for attempt in range(2):
            gemini_result = build_gemini_audio_segmentation(
                api_key=str(os.getenv("GEMINI_API_KEY") or "").strip(),
                audio_path=analysis_path,
                audio_url=audio_url,
                duration_sec=duration_sec,
                audio_id=str(input_pkg.get("job_id") or input_pkg.get("id") or "audio_source"),
                transcript_text=transcript_text,
                dynamics_summary=_safe_dict(raw_analysis.get("summary")),
                validation_feedback=validation_feedback,
            )
            diagnostics["audio_map_segmentation_prompt_version"] = str(gemini_result.get("prompt_version") or "")
            diagnostics["audio_map_used_model"] = str(gemini_result.get("used_model") or diagnostics.get("audio_map_used_model") or "")
            transport_meta = _safe_dict(gemini_result.get("transport_meta"))
            diagnostics["audio_segmentation_source_mode"] = str(transport_meta.get("audio_segmentation_source_mode") or "none")
            diagnostics["audio_segmentation_local_path_found"] = bool(transport_meta.get("audio_segmentation_local_path_found"))
            diagnostics["audio_segmentation_inline_attempted"] = bool(transport_meta.get("audio_segmentation_inline_attempted"))
            diagnostics["audio_segmentation_inline_bytes_size"] = int(transport_meta.get("audio_segmentation_inline_bytes_size") or 0)
            diagnostics["audio_segmentation_url_used"] = str(transport_meta.get("audio_segmentation_url_used") or "")
            diagnostics["audio_segmentation_transport_error"] = str(transport_meta.get("audio_segmentation_transport_error") or "")
            diagnostics["audio_map_segmentation_raw_response"] = str(gemini_result.get("raw_text") or "")
            diagnostics["audio_map_segmentation_retry_used"] = retry_used

            if not bool(gemini_result.get("ok")):
                last_error_code = "AUDIO_SCHEMA_INVALID"
                last_errors = [str(gemini_result.get("error") or "gemini_generation_failed")]
                validation_feedback = _validation_feedback(last_error_code, last_errors)
                diagnostics["audio_map_segmentation_last_error_code"] = last_error_code
                diagnostics["audio_map_segmentation_last_errors"] = last_errors
                diagnostics["audio_map_segmentation_retry_feedback"] = validation_feedback
            else:
                gemini_payload = _safe_dict(gemini_result.get("payload"))
                strict_result = validate_audio_map_v11(gemini_payload, audio_duration_sec=duration_sec)
                diagnostics["audio_map_segmentation_normalized_summary"] = _summarize_audio_map_v11(strict_result.normalized or gemini_payload)
                diagnostics["audio_map_segmentation_validation_error"] = strict_result.error_code if not strict_result.ok else ""
                diagnostics["audio_map_segmentation_validation_errors"] = strict_result.errors
                diagnostics["audio_map_segmentation_last_error_code"] = strict_result.error_code if not strict_result.ok else ""
                diagnostics["audio_map_segmentation_last_errors"] = strict_result.errors if not strict_result.ok else []
                if strict_result.ok:
                    normalized_diag = _safe_dict(strict_result.normalized.get("diagnostics")) if isinstance(strict_result.normalized, dict) else {}
                    diagnostics["audio_map_gap_sum_sec"] = _to_float(normalized_diag.get("gap_sum_sec"), 0.0)
                    diagnostics["audio_map_overlap_sum_sec"] = _to_float(normalized_diag.get("overlap_sum_sec"), 0.0)
                    break
                last_error_code = strict_result.error_code
                last_errors = strict_result.errors
                validation_feedback = _validation_feedback(last_error_code, last_errors)
                diagnostics["audio_map_segmentation_retry_feedback"] = validation_feedback

            if attempt == 0:
                retry_used = True
                diagnostics["audio_map_segmentation_retry_used"] = True
                _append_diag_event(package, f"audio_map strict validation failed, requesting one retry: {validation_feedback}", stage_id="audio_map")
                continue
            break

        if not strict_result or not strict_result.ok:
            raise RuntimeError(f"{last_error_code}:{'; '.join(last_errors[:3])}")

        audio_map = _build_legacy_compat_audio_payload_from_segments_v11(
            strict_result.normalized,
            duration_sec=duration_sec,
            analysis_mode="audio_map_v1_1_strict",
        )
    finally:
        if cleanup_path:
            try:
                os.unlink(cleanup_path)
            except OSError:
                pass

    audio_map["content_type"] = content_type
    audio_map["director_mode"] = director_mode
    audio_map["semantic_source_type"] = _resolve_audio_semantic_source_type(input_pkg)
    audio_map["audio_truth_scope"] = "timing_plus_emotion" if director_mode == "clip" else "timing_structure"
    audio_map["story_core_mode"] = story_core_mode
    audio_map["story_core_arc_ref"] = str(story_core.get("global_arc") or "")
    audio_map["audio_dramaturgy"] = _build_audio_dramaturgy_summary(audio_map, input_pkg, content_type)
    audio_map.setdefault(
        "transcript_available",
        bool(transcript_text and str(audio_map.get("analysis_mode") or "") in {"transcript_alignment_v2", "approximate_phrase_grouping_v1"}),
    )
    audio_map.setdefault("phrase_units", [])
    audio_map.setdefault("scene_candidate_windows", [])
    scene_slots, scene_slot_diag = _build_scene_slots(
        audio_map=audio_map,
        analysis=raw_analysis,
        duration_sec=duration_sec,
    )
    # Deprecated compatibility bridge: downstream should migrate to direct segments[] usage.
    for slot in scene_slots:
        if isinstance(slot, dict):
            slot.setdefault("legacy_derived", True)
            slot.setdefault("deprecated_bridge", True)
            slot.setdefault("canonical_source", "segments_v1_1")
    audio_map["scene_slots"] = scene_slots
    audio_map["audio_map_source_of_truth"] = "segments_v1_1"
    audio_map["audio_map_legacy_compat_bridge"] = True
    audio_map["audio_map_legacy_scene_slots_derived"] = True
    audio_map["audio_map_legacy_scene_slots_deprecated"] = True
    audio_map["audio_map_legacy_bridge_note"] = (
        "Derived fields (sections/phrase_units/scene_candidate_windows/scene_slots) are deprecated compatibility bridge; "
        "segments[] is canonical source of truth."
    )
    audio_map.setdefault("audio_map_alignment_source", "")
    audio_map.setdefault(
        "cut_policy",
        {
            "min_scene_sec": 3,
            "target_scene_sec_min": 3,
            "target_scene_sec_max": 6,
            "hard_max_scene_sec": 8,
            "no_mid_word_cut": True,
            "prefer_phrase_endings": True,
        },
    )
    grid_metrics = _audio_map_grid_metrics(audio_map)
    validation_flags = _validate_audio_map_soft(audio_map)
    audio_dynamics_summary = _safe_dict(audio_map.get("audio_dynamics_summary"))
    dynamics_available = bool(
        int(audio_dynamics_summary.get("pause_points_count") or 0)
        or int(audio_dynamics_summary.get("phrase_points_count") or 0)
        or int(audio_dynamics_summary.get("energy_peaks_count") or 0)
        or int(audio_dynamics_summary.get("detected_sections_count") or 0)
    )
    music_signal_mode = "dynamics+transcript" if dynamics_available else "transcript_only"
    audio_map.update(grid_metrics)
    audio_map["audio_map_primary_source"] = "gemini"
    audio_map["audio_map_model_led_segmentation"] = True
    audio_map["audio_map_validation_flags"] = validation_flags
    audio_map["audio_map_backend_repair_applied"] = False
    audio_map["audio_map_music_signal_mode"] = music_signal_mode
    audio_map["audio_map_dynamics_available"] = dynamics_available

    diagnostics["audio_map_analysis_mode"] = "audio_map_v1_1_strict"
    diagnostics["audio_map_used_fallback"] = False
    diagnostics["audio_map_primary_fallback_reason"] = ""
    diagnostics["audio_map_segmentation_backend"] = "gemini"
    diagnostics["audio_map_segmentation_used_fallback"] = False
    diagnostics["audio_map_segmentation_error"] = str(diagnostics.get("audio_map_segmentation_error") or "")
    diagnostics["audio_map_segmentation_error_detail"] = str(diagnostics.get("audio_map_segmentation_error_detail") or "")
    diagnostics["audio_map_validation_flags"] = validation_flags
    diagnostics["audio_map_primary_source"] = "gemini"
    diagnostics["audio_map_model_led_segmentation"] = True
    diagnostics["audio_map_backend_repair_applied"] = False
    diagnostics["audio_map_phrase_mode"] = str(audio_map.get("analysis_mode") or "audio_map_v1_1_strict")
    diagnostics["transcript_available"] = bool(audio_map.get("transcript_available"))
    diagnostics["word_timestamp_count"] = 0
    diagnostics["phrase_unit_count"] = len(_safe_list(audio_map.get("phrase_units")))
    diagnostics["scene_candidate_count"] = len(_safe_list(audio_map.get("scene_candidate_windows")))
    diagnostics["scene_slot_count"] = len(scene_slots)
    diagnostics["audio_map_source_of_truth"] = "segments_v1_1"
    diagnostics["audio_map_legacy_compat_bridge"] = True
    diagnostics["audio_map_legacy_scene_slots_derived"] = True
    diagnostics["audio_map_legacy_scene_slots_deprecated"] = True
    diagnostics["audio_map_legacy_bridge_note"] = str(audio_map.get("audio_map_legacy_bridge_note") or "")
    diagnostics.update(_safe_dict(scene_slot_diag))
    diagnostics["audio_map_alignment_source"] = str(audio_map.get("audio_map_alignment_source") or "gemini_audio_map_v1_1")
    diagnostics.update(grid_metrics)
    diagnostics["audio_map_music_signal_mode"] = music_signal_mode
    diagnostics["audio_map_dynamics_available"] = dynamics_available
    diagnostics["audio_map_dramaturgy_source"] = str(_safe_dict(audio_map.get("audio_dramaturgy")).get("dramaturgy_source") or "")
    diagnostics["audio_map_textual_directive_present"] = bool(_safe_dict(audio_map.get("audio_dramaturgy")).get("textual_directive_present"))
    package["diagnostics"] = diagnostics
    package["audio_map"] = audio_map
    _append_diag_event(package, "audio_map generated", stage_id="audio_map")
    return package


def _run_role_plan_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    content_type = str(input_pkg.get("content_type") or "").strip().lower()
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["role_plan_backend"] = "gemini"
    diagnostics["role_plan_prompt_version"] = ROLE_PLAN_PROMPT_VERSION
    diagnostics["role_plan_error"] = ""
    diagnostics["role_plan_validation_error"] = ""
    diagnostics["validation_error"] = ""
    diagnostics["role_plan_used_fallback"] = False
    diagnostics["role_plan_scene_count"] = 0
    diagnostics["role_plan_present_roles"] = []
    diagnostics["role_plan_character_roles_count"] = 0
    diagnostics["role_plan_world_roles_count"] = 0
    diagnostics["role_plan_world_anchor_mode"] = ""
    diagnostics["role_plan_country_or_region"] = ""
    diagnostics["role_plan_presence_modes"] = []
    diagnostics["role_plan_presence_flat"] = False
    diagnostics["role_plan_performance_focus_flat"] = False
    diagnostics["role_plan_skipped"] = False
    diagnostics["role_plan_skip_reason"] = ""
    diagnostics["role_plan_empty"] = False
    package["diagnostics"] = diagnostics

    if content_type and content_type not in {"music_video", "clip", "story"}:
        package["role_plan"] = _attach_downstream_mode_metadata({}, package)
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics["role_plan_error"] = f"unsupported_content_type:{content_type}"
        diagnostics["role_plan_used_fallback"] = True
        diagnostics["role_plan_skipped"] = True
        diagnostics["role_plan_skip_reason"] = f"unsupported_content_type:{content_type}"
        package["diagnostics"] = diagnostics
        _append_diag_event(package, f"role_plan skipped for content_type={content_type}", stage_id="role_plan")
        return package

    result = build_gemini_role_plan(
        api_key=str(os.getenv("GEMINI_API_KEY") or "").strip(),
        package=package,
    )
    role_plan = _safe_dict(result.get("role_plan"))
    package["role_plan"] = _attach_downstream_mode_metadata(role_plan, package)

    role_diag = _safe_dict(result.get("diagnostics"))
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["role_plan_backend"] = "gemini"
    diagnostics["role_plan_prompt_version"] = str(role_diag.get("prompt_version") or ROLE_PLAN_PROMPT_VERSION)
    diagnostics["role_plan_used_fallback"] = bool(result.get("used_fallback"))
    diagnostics["role_plan_scene_count"] = int(role_diag.get("scene_count") or len(_safe_list(role_plan.get("scene_roles"))))
    diagnostics["role_plan_present_roles"] = _safe_list(role_diag.get("present_roles"))
    diagnostics["role_plan_character_roles_count"] = int(role_diag.get("character_roles_count") or 0)
    diagnostics["role_plan_world_roles_count"] = int(role_diag.get("world_roles_count") or 0)
    diagnostics["role_plan_world_anchor_mode"] = str(role_diag.get("role_plan_world_anchor_mode") or "")
    diagnostics["role_plan_country_or_region"] = str(role_diag.get("role_plan_country_or_region") or "")
    diagnostics["role_plan_presence_modes"] = _safe_list(role_diag.get("role_plan_presence_modes"))
    diagnostics["role_plan_presence_flat"] = bool(role_diag.get("role_plan_presence_flat"))
    diagnostics["role_plan_performance_focus_flat"] = bool(role_diag.get("role_plan_performance_focus_flat"))
    diagnostics["role_plan_error"] = str(result.get("error") or "")
    diagnostics["role_plan_validation_error"] = str(result.get("validation_error") or "")
    diagnostics["validation_error"] = str(result.get("validation_error") or "")
    diagnostics["role_plan_skipped"] = False
    diagnostics["role_plan_skip_reason"] = ""
    diagnostics["role_plan_empty"] = not bool(role_plan and _safe_list(role_plan.get("scene_roles")))
    package["diagnostics"] = diagnostics

    if role_plan and _safe_list(role_plan.get("scene_roles")):
        _append_diag_event(package, "role_plan generated", stage_id="role_plan")
        if diagnostics.get("role_plan_presence_flat"):
            _append_diag_event(package, "role_plan_presence_flat warning", stage_id="role_plan")
        if diagnostics.get("role_plan_performance_focus_flat"):
            _append_diag_event(package, "role_plan_performance_focus_flat warning", stage_id="role_plan")
    else:
        _append_diag_event(package, "role_plan empty", stage_id="role_plan")
    return package


def _run_scene_plan_stage(package: dict[str, Any]) -> dict[str, Any]:
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["scene_plan_backend"] = "gemini"
    diagnostics["scene_plan_prompt_version"] = SCENE_PLAN_PROMPT_VERSION
    diagnostics["scene_plan_used_model"] = ""
    diagnostics["scene_plan_used_fallback"] = False
    diagnostics["scene_plan_scene_count"] = 0
    diagnostics["scene_plan_route_counts"] = {"i2v": 0, "ia2v": 0, "first_last": 0}
    diagnostics["scene_plan_presence_modes"] = []
    diagnostics["scene_plan_route_flat"] = False
    diagnostics["scene_plan_watchability_fallback_count"] = 0
    diagnostics["scene_plan_world_summary_used"] = False
    diagnostics["scene_plan_window_count_source"] = 0
    diagnostics["scene_plan_window_count_model"] = 0
    diagnostics["scene_plan_window_count_normalized"] = 0
    diagnostics["scene_plan_repaired_to_audio_windows"] = False
    diagnostics["scene_plan_synthetic_rows_dropped"] = 0
    diagnostics["scene_plan_missing_rows_filled"] = 0
    diagnostics["scene_plan_has_adjacent_ia2v"] = False
    diagnostics["scene_plan_has_adjacent_first_last"] = False
    diagnostics["scene_plan_route_spacing_warning"] = False
    diagnostics["active_video_model_capability_profile"] = str(diagnostics.get("active_video_model_capability_profile") or "")
    diagnostics["active_route_capability_mode"] = str(diagnostics.get("active_route_capability_mode") or "")
    diagnostics["scene_plan_capability_guard_applied"] = False
    diagnostics["scene_plan_validation_error"] = ""
    diagnostics["scene_plan_route_budget_retry_used"] = False
    diagnostics["scene_plan_route_budget_feedback"] = ""
    diagnostics["scene_plan_route_budget_ok"] = True
    diagnostics["scene_plan_route_budget_target"] = {}
    diagnostics["scene_plan_route_budget_actual"] = {}
    diagnostics["scene_plan_max_consecutive_lipsync"] = 0
    diagnostics["scene_plan_longest_lipsync_streak"] = 0
    diagnostics["validation_error"] = ""
    diagnostics["scene_plan_error"] = ""
    diagnostics["scene_plan_empty"] = False
    package["diagnostics"] = diagnostics

    result = build_gemini_scene_plan(
        api_key=str(os.getenv("GEMINI_API_KEY") or "").strip(),
        package=package,
    )
    scene_plan = _safe_dict(result.get("scene_plan"))
    route_budget_ok, route_budget_feedback, route_budget_meta = _validate_scene_plan_route_budget(
        package=package,
        scene_plan=scene_plan,
        diagnostics=diagnostics,
    )
    if not route_budget_ok:
        diagnostics["scene_plan_route_budget_retry_used"] = True
        diagnostics["scene_plan_route_budget_feedback"] = route_budget_feedback
        _append_diag_event(package, f"scene_plan route budget validation failed, retrying once: {route_budget_feedback}", stage_id="scene_plan")
        retry_result = build_gemini_scene_plan(
            api_key=str(os.getenv("GEMINI_API_KEY") or "").strip(),
            package=package,
            validation_feedback=route_budget_feedback,
        )
        retry_scene_plan = _safe_dict(retry_result.get("scene_plan"))
        retry_ok, retry_feedback, retry_meta = _validate_scene_plan_route_budget(
            package=package,
            scene_plan=retry_scene_plan,
            diagnostics=diagnostics,
        )
        result = retry_result
        scene_plan = retry_scene_plan
        route_budget_ok = retry_ok
        route_budget_feedback = retry_feedback
        route_budget_meta = retry_meta
        if not route_budget_ok:
            result["ok"] = False
            result["validation_error"] = route_budget_feedback or "scene_plan_route_budget_validation_failed"
            result["error"] = result.get("error") or "scene_plan_route_budget_validation_failed"

    package["scene_plan"] = _attach_downstream_mode_metadata(scene_plan, package)

    scene_diag = _safe_dict(result.get("diagnostics"))
    route_counts = _safe_dict(scene_diag.get("route_counts"))
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["scene_plan_backend"] = "gemini"
    diagnostics["scene_plan_prompt_version"] = str(scene_diag.get("prompt_version") or SCENE_PLAN_PROMPT_VERSION)
    diagnostics["scene_plan_used_model"] = str(scene_diag.get("used_model") or diagnostics.get("scene_plan_used_model") or "")
    diagnostics["scene_plan_used_fallback"] = bool(result.get("used_fallback"))
    diagnostics["scene_plan_scene_count"] = int(scene_diag.get("scene_count") or len(_safe_list(scene_plan.get("scenes"))))
    diagnostics["scene_plan_route_counts"] = {
        "i2v": int(route_counts.get("i2v") or _safe_dict(scene_plan.get("route_mix_summary")).get("i2v") or 0),
        "ia2v": int(route_counts.get("ia2v") or _safe_dict(scene_plan.get("route_mix_summary")).get("ia2v") or 0),
        "first_last": int(route_counts.get("first_last") or _safe_dict(scene_plan.get("route_mix_summary")).get("first_last") or 0),
    }
    diagnostics["scene_plan_presence_modes"] = _safe_list(scene_diag.get("presence_modes"))
    diagnostics["scene_plan_route_flat"] = bool(scene_diag.get("route_flat"))
    diagnostics["scene_plan_watchability_fallback_count"] = int(scene_diag.get("watchability_fallback_count") or 0)
    diagnostics["scene_plan_world_summary_used"] = bool(scene_diag.get("world_summary_used"))
    diagnostics["scene_plan_window_count_source"] = int(scene_diag.get("window_count_source") or 0)
    diagnostics["scene_plan_window_count_model"] = int(scene_diag.get("window_count_model") or 0)
    diagnostics["scene_plan_window_count_normalized"] = int(scene_diag.get("window_count_normalized") or 0)
    diagnostics["scene_plan_repaired_to_audio_windows"] = bool(scene_diag.get("repaired_to_audio_windows"))
    diagnostics["scene_plan_synthetic_rows_dropped"] = int(scene_diag.get("synthetic_rows_dropped") or 0)
    diagnostics["scene_plan_missing_rows_filled"] = int(scene_diag.get("missing_rows_filled") or 0)
    diagnostics["scene_plan_has_adjacent_ia2v"] = bool(scene_diag.get("scene_plan_has_adjacent_ia2v"))
    diagnostics["scene_plan_has_adjacent_first_last"] = bool(scene_diag.get("scene_plan_has_adjacent_first_last"))
    diagnostics["scene_plan_route_spacing_warning"] = bool(scene_diag.get("scene_plan_route_spacing_warning"))
    diagnostics["active_video_model_capability_profile"] = str(
        scene_diag.get("active_video_model_capability_profile") or diagnostics.get("active_video_model_capability_profile") or ""
    )
    diagnostics["active_route_capability_mode"] = str(
        scene_diag.get("active_route_capability_mode") or diagnostics.get("active_route_capability_mode") or ""
    )
    diagnostics["scene_plan_capability_guard_applied"] = bool(scene_diag.get("scene_plan_capability_guard_applied"))
    diagnostics["capability_rules_source_version"] = str(
        scene_diag.get("capability_rules_source_version") or diagnostics.get("capability_rules_source_version") or ""
    )
    diagnostics["scene_plan_validation_error"] = str(result.get("validation_error") or "")
    diagnostics["scene_plan_route_budget_ok"] = bool(route_budget_ok)
    diagnostics["scene_plan_route_budget_target"] = _safe_dict(route_budget_meta.get("target_route_mix"))
    diagnostics["scene_plan_route_budget_actual"] = _safe_dict(route_budget_meta.get("actual_route_mix"))
    diagnostics["scene_plan_max_consecutive_lipsync"] = int(route_budget_meta.get("max_consecutive_lipsync") or 0)
    diagnostics["scene_plan_longest_lipsync_streak"] = int(route_budget_meta.get("longest_lipsync_streak") or 0)
    if not route_budget_ok:
        diagnostics["scene_plan_validation_error"] = route_budget_feedback or diagnostics["scene_plan_validation_error"]
    diagnostics["validation_error"] = str(diagnostics.get("scene_plan_validation_error") or "")
    diagnostics["scene_plan_error"] = str(result.get("error") or "")
    diagnostics["scene_plan_empty"] = not bool(scene_plan and _safe_list(scene_plan.get("scenes")))
    package["diagnostics"] = diagnostics

    if scene_plan and _safe_list(scene_plan.get("scenes")):
        _append_diag_event(package, "scene_plan generated", stage_id="scene_plan")
        if diagnostics.get("scene_plan_route_flat"):
            _append_diag_event(package, "scene_plan_route_flat warning", stage_id="scene_plan")
        if diagnostics.get("scene_plan_route_spacing_warning"):
            _append_diag_event(package, "scene_plan_route_spacing_warning", stage_id="scene_plan")
    else:
        _append_diag_event(package, "scene_plan empty", stage_id="scene_plan")
    return package




def _run_scene_prompts_stage(package: dict[str, Any]) -> dict[str, Any]:
    current_signature = _scene_prompts_upstream_signature(package)
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["scene_prompts_backend"] = "gemini"
    diagnostics["scene_prompts_prompt_version"] = SCENE_PROMPTS_PROMPT_VERSION
    diagnostics["scene_prompts_stage_source"] = "current_package"
    diagnostics["scene_prompts_rows_source_count"] = 0
    diagnostics["scene_prompts_rows_model_count"] = 0
    diagnostics["scene_prompts_rows_normalized_count"] = 0
    diagnostics["scene_prompts_repaired_from_current_package_count"] = 0
    diagnostics["scene_prompts_unrelated_rows_discarded_count"] = 0
    diagnostics["scene_prompts_used_fallback"] = False
    diagnostics["scene_prompts_scene_count"] = 0
    diagnostics["scene_prompts_missing_photo_count"] = 0
    diagnostics["scene_prompts_missing_video_count"] = 0
    diagnostics["scene_prompts_ia2v_audio_driven_count"] = 0
    diagnostics["scene_prompts_route_semantics_mismatch_count"] = 0
    diagnostics["prompt_capability_guard_applied"] = False
    diagnostics["scene_prompts_validation_error"] = ""
    diagnostics["validation_error"] = ""
    diagnostics["scene_prompts_error"] = ""
    diagnostics["scene_prompts_empty"] = False
    previous_signature = str(diagnostics.get("scene_prompts_upstream_signature") or "")
    diagnostics["scene_prompts_upstream_changed"] = bool(previous_signature and previous_signature != current_signature)
    diagnostics["scene_prompts_upstream_signature"] = current_signature
    package["diagnostics"] = diagnostics
    package["scene_prompts"] = {"scenes": []}

    result = build_gemini_scene_prompts(
        api_key=str(os.getenv("GEMINI_API_KEY") or "").strip(),
        package=package,
    )

    scene_prompts = _safe_dict(result.get("scene_prompts"))
    package["scene_prompts"] = _attach_downstream_mode_metadata(scene_prompts, package)

    prompts_diag = _safe_dict(result.get("diagnostics"))
    diagnostics = _safe_dict(package.get("diagnostics"))
    for key, value in prompts_diag.items():
        if str(key).startswith("scene_prompts_"):
            diagnostics[str(key)] = value

    stage_keys = (
        "prompt_version",
        "stage_source",
        "rows_source_count",
        "rows_model_count",
        "rows_normalized_count",
        "repaired_from_current_package_count",
        "unrelated_rows_discarded_count",
        "missing_photo_count",
        "missing_video_count",
        "ia2v_audio_driven_count",
        "active_video_model_capability_profile",
        "active_route_capability_mode",
        "capability_rules_source_version",
        "scene_prompts_runtime_marker",
    )
    for key in stage_keys:
        if key in prompts_diag:
            diagnostics[key] = prompts_diag.get(key)

    alias_map = {
        "prompt_version": "scene_prompts_prompt_version",
        "stage_source": "scene_prompts_stage_source",
        "rows_source_count": "scene_prompts_rows_source_count",
        "rows_model_count": "scene_prompts_rows_model_count",
        "rows_normalized_count": "scene_prompts_rows_normalized_count",
        "repaired_from_current_package_count": "scene_prompts_repaired_from_current_package_count",
        "unrelated_rows_discarded_count": "scene_prompts_unrelated_rows_discarded_count",
        "missing_photo_count": "scene_prompts_missing_photo_count",
        "missing_video_count": "scene_prompts_missing_video_count",
        "ia2v_audio_driven_count": "scene_prompts_ia2v_audio_driven_count",
    }
    for source_key, target_key in alias_map.items():
        if source_key in prompts_diag:
            diagnostics[target_key] = prompts_diag.get(source_key)

    diagnostics["scene_prompts_backend"] = str(
        prompts_diag.get("scene_prompts_backend") or diagnostics.get("scene_prompts_backend") or "gemini"
    )
    diagnostics["scene_prompts_used_fallback"] = bool(result.get("used_fallback"))
    diagnostics["scene_prompts_scene_count"] = int(prompts_diag.get("scene_count") or len(_safe_list(scene_prompts.get("scenes"))))
    diagnostics["scene_prompts_route_semantics_mismatch_count"] = int(
        prompts_diag.get("scene_prompts_route_semantics_mismatch_count")
        or diagnostics.get("scene_prompts_route_semantics_mismatch_count")
        or 0
    )
    diagnostics["active_video_model_capability_profile"] = str(
        prompts_diag.get("active_video_model_capability_profile") or diagnostics.get("active_video_model_capability_profile") or ""
    )
    diagnostics["active_route_capability_mode"] = str(
        prompts_diag.get("active_route_capability_mode") or diagnostics.get("active_route_capability_mode") or ""
    )
    diagnostics["prompt_capability_guard_applied"] = bool(prompts_diag.get("prompt_capability_guard_applied"))
    diagnostics["capability_rules_source_version"] = str(
        prompts_diag.get("capability_rules_source_version") or diagnostics.get("capability_rules_source_version") or ""
    )
    diagnostics["scene_prompts_validation_error"] = str(result.get("validation_error") or "")
    diagnostics["validation_error"] = str(diagnostics.get("scene_prompts_validation_error") or "")
    diagnostics["scene_prompts_error"] = str(result.get("error") or "")
    diagnostics["scene_prompts_empty"] = not bool(scene_prompts and _safe_list(scene_prompts.get("scenes")))
    package["diagnostics"] = diagnostics

    if scene_prompts and _safe_list(scene_prompts.get("scenes")):
        _append_diag_event(package, "scene_prompts generated", stage_id="scene_prompts")
        if int(diagnostics.get("scene_prompts_route_semantics_mismatch_count") or 0) > 0:
            _append_diag_event(package, "scene_prompts_route_semantics_mismatch warning", stage_id="scene_prompts")
    else:
        _append_diag_event(package, "scene_prompts empty", stage_id="scene_prompts")
    return package

def _run_final_video_prompt_stage(package: dict[str, Any]) -> dict[str, Any]:
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["final_video_prompt_backend"] = "gemini"
    diagnostics["final_video_prompt_prompt_version"] = FINAL_VIDEO_PROMPT_STAGE_VERSION
    diagnostics["final_video_prompt_scene_count"] = 0
    diagnostics["final_video_prompt_error"] = ""
    package["diagnostics"] = diagnostics
    package["final_video_prompt"] = {"scenes": []}

    result = generate_ltx_video_prompt_metadata(
        api_key=str(os.getenv("GEMINI_API_KEY") or "").strip(),
        package=package,
    )
    final_video_prompt = _safe_dict(result.get("final_video_prompt"))
    package["final_video_prompt"] = final_video_prompt

    diag = _safe_dict(result.get("diagnostics"))
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["final_video_prompt_backend"] = str(diag.get("final_video_prompt_backend") or "gemini")
    diagnostics["final_video_prompt_prompt_version"] = str(
        diag.get("final_video_prompt_prompt_version") or FINAL_VIDEO_PROMPT_STAGE_VERSION
    )
    diagnostics["final_video_prompt_scene_count"] = int(diag.get("final_video_prompt_scene_count") or 0)
    diagnostics["final_video_prompt_used_fallback"] = bool(diag.get("final_video_prompt_used_fallback"))
    diagnostics["final_video_prompt_error"] = str(result.get("error") or "")
    package["diagnostics"] = diagnostics

    for row in _safe_list(diag.get("final_video_prompt_debug_rows")):
        logger.info("[FINAL VIDEO PROMPT STAGE] %s", json.dumps(_safe_dict(row), ensure_ascii=False))

    if _safe_list(final_video_prompt.get("scenes")):
        _append_diag_event(package, "final_video_prompt generated", stage_id="final_video_prompt")
    else:
        _append_diag_event(package, "final_video_prompt empty", stage_id="final_video_prompt")
    return package


def run_stage(stage_id: str, package: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if stage_id not in STAGE_IDS:
        raise ValueError(f"unknown_stage:{stage_id}")
    pkg = deepcopy(_safe_dict(package)) if package else create_storyboard_package(payload)
    _set_stage_status(pkg, stage_id, "running")
    pkg["updated_at"] = _utc_iso()

    deps = STAGE_DEPENDENCIES.get(stage_id, [])
    statuses = _safe_dict(pkg.get("stage_statuses"))
    missing = [dep for dep in deps if str(_safe_dict(statuses.get(dep)).get("status") or "") not in {"done"}]
    if missing:
        error_code = "missing_upstream_stage" if stage_id == "finalize" else "missing_dependencies"
        _set_stage_status(pkg, stage_id, "error", error=f"{error_code}:{','.join(missing)}")
        _safe_dict(pkg.get("diagnostics")).setdefault("errors", []).append(f"{stage_id}: {error_code} {missing}")
        return pkg

    try:
        if stage_id == "input_package":
            pkg = _run_input_package_stage(pkg)
        elif stage_id == "audio_map":
            pkg = _run_audio_map_stage(pkg)
        elif stage_id == "story_core":
            pkg = _run_story_core_stage(pkg)
        elif stage_id == "role_plan":
            pkg = _run_role_plan_stage(pkg)
        elif stage_id == "scene_plan":
            pkg = _run_scene_plan_stage(pkg)
        elif stage_id == "scene_prompts":
            pkg = _run_scene_prompts_stage(pkg)
        elif stage_id == "final_video_prompt":
            pkg = _run_final_video_prompt_stage(pkg)
        elif stage_id == "finalize":
            pkg = _run_finalize_stage(pkg)
        _set_stage_status(pkg, stage_id, "done")
    except Exception as exc:  # noqa: BLE001
        _set_stage_status(pkg, stage_id, "error", error=str(exc))
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        errors = _safe_list(diagnostics.get("errors"))
        errors.append(f"{stage_id}: {exc}")
        diagnostics["errors"] = errors[-80:]
        pkg["diagnostics"] = diagnostics

    pkg["updated_at"] = _utc_iso()
    return pkg


def run_manual_stage(
    stage_id: str,
    package: dict[str, Any],
    payload: dict[str, Any] | None = None,
    *,
    return_executed_stage_ids: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], list[str]]:
    if stage_id not in STAGE_IDS:
        raise ValueError(f"unknown_stage:{stage_id}")
    pkg = deepcopy(_safe_dict(package)) if package else create_storyboard_package(payload)
    executed_stage_ids: list[str] = []
    if stage_id == "finalize":
        # Guardrail: pressing FINAL must not retrigger upstream creative Gemini stages.
        # Finalize can run only from already prepared normalized outputs.
        pkg = invalidate_downstream_stages(pkg, stage_id, reason=f"manual_rerun:{stage_id}")
        pkg = run_stage(stage_id, pkg, payload)
        executed_stage_ids.append(stage_id)
        return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg
    dep_sequence = resolve_stage_sequence([stage_id], include_dependencies=True)[:-1]
    reusable_upstream = [dep_stage for dep_stage in dep_sequence if _can_reuse_stage_output(pkg, dep_stage)]
    missing_upstream = [dep_stage for dep_stage in dep_sequence if dep_stage not in reusable_upstream]
    continuation_mode = "reuse_existing_package" if not missing_upstream else "recompute_missing_upstream"
    if missing_upstream:
        first_missing_idx = dep_sequence.index(missing_upstream[0])
        dep_sequence = dep_sequence[first_missing_idx:]
    else:
        dep_sequence = []
    preserve_audio_map_for_story_core = stage_id == "story_core" and _is_usable_audio_map(_safe_dict(pkg.get("audio_map")))
    if preserve_audio_map_for_story_core:
        # Manual CORE rerun must preserve upstream audio_map and skip audio_map dependency rebuild.
        dep_sequence = [dep_stage for dep_stage in dep_sequence if dep_stage != "audio_map"]
    preserved_audio_map_snapshot = deepcopy(_safe_dict(pkg.get("audio_map"))) if preserve_audio_map_for_story_core else {}
    guarded_fingerprint_before = (
        _audio_map_story_core_guard_fingerprint(preserved_audio_map_snapshot) if preserve_audio_map_for_story_core else ""
    )
    upstream_guard_snapshots: dict[str, dict[str, Any]] = {
        dep_stage: deepcopy(_safe_dict(pkg.get(_stage_output_field(dep_stage))))
        for dep_stage in reusable_upstream
    }
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    diagnostics["continuation_mode"] = continuation_mode
    diagnostics["upstream_package_complete"] = not bool(missing_upstream)
    diagnostics["reused_upstream_stages"] = reusable_upstream
    diagnostics["regenerated_stages"] = list(dep_sequence) + [stage_id]
    if missing_upstream:
        diagnostics["recompute_missing_upstream_stages"] = missing_upstream
    pkg["diagnostics"] = diagnostics
    for dep_stage in dep_sequence:
        pkg = run_stage(dep_stage, pkg, payload)
        executed_stage_ids.append(dep_stage)
        if str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get(dep_stage)).get("status") or "") == "error":
            return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg
    pkg = invalidate_downstream_stages(pkg, stage_id, reason=f"manual_rerun:{stage_id}")
    pkg = run_stage(stage_id, pkg, payload)
    executed_stage_ids.append(stage_id)
    if reusable_upstream:
        statuses = _safe_dict(pkg.get("stage_statuses"))
        guard_restored: list[str] = []
        for upstream_stage in reusable_upstream:
            output_key = _stage_output_field(upstream_stage)
            before_snapshot = upstream_guard_snapshots.get(upstream_stage, {})
            after_snapshot = _safe_dict(pkg.get(output_key))
            if before_snapshot and after_snapshot != before_snapshot:
                pkg[output_key] = deepcopy(before_snapshot)
                stage_state = _safe_dict(statuses.get(upstream_stage))
                stage_state["status"] = "done"
                stage_state["error"] = ""
                stage_state["updated_at"] = _utc_iso()
                statuses[upstream_stage] = stage_state
                guard_restored.append(upstream_stage)
        pkg["stage_statuses"] = statuses
        if guard_restored:
            diagnostics = _safe_dict(pkg.get("diagnostics"))
            warnings = _safe_list(diagnostics.get("warnings"))
            warnings.append(
                {
                    "stage_id": stage_id,
                    "message": "manual_rerun_upstream_mutation_guard_triggered",
                    "restored_stages": guard_restored,
                }
            )
            diagnostics["warnings"] = warnings[-80:]
            diagnostics["manual_rerun_upstream_mutation_guard_triggered"] = True
            diagnostics["manual_rerun_upstream_restored_stages"] = guard_restored
            pkg["diagnostics"] = diagnostics
    if preserve_audio_map_for_story_core:
        guarded_audio_map_after = _safe_dict(pkg.get("audio_map"))
        guarded_fingerprint_after = _audio_map_story_core_guard_fingerprint(guarded_audio_map_after)
        audio_map_was_mutated = guarded_audio_map_after != preserved_audio_map_snapshot
        if audio_map_was_mutated:
            diagnostics = _safe_dict(pkg.get("diagnostics"))
            warnings = _safe_list(diagnostics.get("warnings"))
            warnings.append(
                {
                    "stage_id": "story_core",
                    "message": "story_core_audio_map_mutation_guard_triggered",
                }
            )
            diagnostics["warnings"] = warnings[-80:]
            diagnostics["story_core_audio_map_mutation_guard_triggered"] = True
            diagnostics["story_core_audio_map_mutation_guard_fingerprint_changed"] = bool(
                guarded_fingerprint_before and guarded_fingerprint_after != guarded_fingerprint_before
            )
            pkg["diagnostics"] = diagnostics
            _append_diag_event(pkg, "story_core audio_map mutation guard restored upstream audio_map", stage_id="story_core")
        # Always restore preserved upstream audio_map snapshot for manual story_core rerun.
        pkg["audio_map"] = preserved_audio_map_snapshot
    return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg


def run_pipeline(stage_ids: list[str], package: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    pkg = deepcopy(_safe_dict(package)) if package else create_storyboard_package(payload)
    for stage_id in stage_ids:
        pkg = run_stage(stage_id, pkg, payload)
        if str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get(stage_id)).get("status") or "") == "error":
            break
    return pkg


def _resolve_stage_with_dependencies(stage_id: str, ordered: list[str], visited: set[str]) -> None:
    if stage_id in visited:
        return
    visited.add(stage_id)
    for dep in STAGE_DEPENDENCIES.get(stage_id, []):
        _resolve_stage_with_dependencies(dep, ordered, visited)
    if stage_id not in ordered:
        ordered.append(stage_id)


def resolve_stage_sequence(
    requested_stage_ids: list[str] | None = None,
    *,
    auto_mode: bool = False,
    include_dependencies: bool = False,
) -> list[str]:
    if auto_mode:
        return list(STAGE_IDS)
    stage_ids = [stage_id for stage_id in (requested_stage_ids or []) if stage_id in STAGE_IDS]
    if not stage_ids:
        stage_ids = ["story_core"]
    if not include_dependencies:
        return stage_ids
    ordered: list[str] = []
    visited: set[str] = set()
    for stage_id in stage_ids:
        _resolve_stage_with_dependencies(stage_id, ordered, visited)
    return ordered
