from __future__ import annotations

import base64
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
from app.engine.audio_transcript_aligner import resolve_transcript_alignment_with_diagnostics
from app.engine.audio_scene_segmenter import build_gemini_audio_segmentation
from app.engine.gemini_rest import post_generate_content
from app.engine.scenario_role_planner import ROLE_PLAN_PROMPT_VERSION, build_gemini_role_plan
from app.engine.scenario_scene_planner import SCENE_PLAN_PROMPT_VERSION, build_gemini_scene_plan
from app.engine.scenario_scene_prompter import SCENE_PROMPTS_PROMPT_VERSION, build_gemini_scene_prompts

logger = logging.getLogger(__name__)

MAX_STORY_CORE_IMAGE_BYTES = 8 * 1024 * 1024

STAGE_IDS = (
    "input_package",
    "story_core",
    "audio_map",
    "role_plan",
    "scene_plan",
    "scene_prompts",
    "finalize",
)

STAGE_DEPENDENCIES: dict[str, list[str]] = {
    "input_package": [],
    "story_core": ["input_package"],
    "audio_map": ["story_core"],
    "role_plan": ["story_core", "audio_map"],
    "scene_plan": ["story_core", "audio_map", "role_plan"],
    "scene_prompts": ["story_core", "audio_map", "role_plan", "scene_plan"],
    "finalize": ["story_core", "audio_map", "role_plan", "scene_plan", "scene_prompts"],
}

DOWNSTREAM_BY_STAGE: dict[str, list[str]] = {
    stage_id: [candidate for candidate, deps in STAGE_DEPENDENCIES.items() if stage_id in deps]
    for stage_id in STAGE_IDS
}

MANUAL_RESET_DOWNSTREAM: dict[str, list[str]] = {
    "story_core": ["audio_map", "role_plan", "scene_plan", "scene_prompts", "finalize"],
    "audio_map": ["role_plan", "scene_plan", "scene_prompts", "finalize"],
    "role_plan": ["scene_plan", "scene_prompts", "finalize"],
    "scene_plan": ["scene_prompts", "finalize"],
    "scene_prompts": ["finalize"],
    "finalize": [],
}

STAGE_SECTION_RESETTERS: dict[str, Any] = {
    "audio_map": lambda: {},
    "role_plan": lambda: {},
    "scene_plan": lambda: {"scenes": []},
    "scene_prompts": lambda: {"scenes": []},
    "finalize": lambda: {"scenes": []},
}

STAGE_DIAGNOSTIC_PREFIXES: dict[str, tuple[str, ...]] = {
    "story_core": ("story_core_",),
    "audio_map": ("audio_", "transcript_"),
    "role_plan": ("role_plan_",),
    "scene_plan": ("scene_plan_",),
    "scene_prompts": ("scene_prompts_",),
    "finalize": ("finalize_",),
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _pick_story_core_character_ref(refs_inventory: dict[str, Any]) -> tuple[str, str]:
    primary = _safe_dict(refs_inventory.get("ref_character_1"))
    from_value = str(primary.get("value") or "").strip()
    if from_value:
        return from_value, "refs_inventory.ref_character_1.value"
    refs = _safe_list(primary.get("refs"))
    for idx, ref in enumerate(refs):
        value = str(ref or "").strip()
        if value:
            return value, f"refs_inventory.ref_character_1.refs[{idx}]"
    return "", ""


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
    }


def _detect_story_core_mode(input_pkg: dict[str, Any]) -> str:
    narrative_fields = (
        input_pkg.get("text"),
        input_pkg.get("story_text"),
        input_pkg.get("note"),
        input_pkg.get("director_note"),
    )
    has_directive = any(bool(str(value or "").strip()) for value in narrative_fields)
    return "directed" if has_directive else "creative"


def _build_story_core_prompt(
    input_pkg: dict[str, Any],
    refs_inventory: dict[str, Any],
    assigned_roles: dict[str, Any],
    story_core_mode: str,
    prop_contracts: list[dict[str, Any]],
    ref_attachment_summary: dict[str, Any],
    grounding_level: str,
) -> str:
    compact_input = {
        "audio_url": str(input_pkg.get("audio_url") or ""),
        "audio_duration_sec": float(input_pkg.get("audio_duration_sec") or 0.0),
        "text": str(input_pkg.get("text") or ""),
        "story_text": str(input_pkg.get("story_text") or ""),
        "note": str(input_pkg.get("note") or ""),
        "director_note": str(input_pkg.get("director_note") or ""),
        "content_type": str(input_pkg.get("content_type") or "music_video"),
        "format": str(input_pkg.get("format") or "9:16"),
        "selected_refs": _safe_dict(input_pkg.get("selected_refs")),
        "refs_by_role": _safe_dict(input_pkg.get("refs_by_role")),
        "connected_context_summary": _safe_dict(input_pkg.get("connected_context_summary")),
        "story_core_prop_contracts": prop_contracts,
        "story_core_ref_attachment_summary": ref_attachment_summary,
        "story_core_grounding_level": grounding_level,
    }
    mode = "directed" if story_core_mode == "directed" else "creative"
    mode_instructions = (
        "MODE: DIRECTED MODE\n"
        "- User text is the mandatory narrative source of truth for the plot.\n"
        "- Do NOT replace user plot with a different premise.\n"
        "- Preserve explicitly specified world, actions, relationships, and narrative direction.\n"
        "- You may only structure, clarify, improve wording, increase cinematic visuality, and fill missing connective tissue.\n"
        "- Do NOT ignore clearly stated user events.\n"
    )
    if mode == "creative":
        mode_instructions = (
            "MODE: CREATIVE MODE\n"
            "- User did not provide narrative directive text.\n"
            "- Invent an original story core.\n"
            "- Be cinematic, emotionally clear, visually strong, and compelling.\n"
            "- Use audio, hero, world/location, style, and props references to shape concept, arc, mood, opening, ending, and emotional journey.\n"
        )
    return (
        "You are STORY CORE stage of a scenario pipeline.\n"
        "Return STRICT JSON only, no markdown.\n"
        "story_core is source of truth for arc/identity/world/style, not a storyboard.\n"
        "Do NOT output scenes, prompts, shot list, or giant plan.\n"
        "Use roles/refs/content type to infer protagonist and supporting cast.\n"
        "If a character image reference is attached, treat it as the source of truth for hero appearance and gender presentation.\n"
        "Connected prop refs are source-of-truth for object identity and object category.\n"
        "Do not replace a referenced prop with a semantically related but different object.\n"
        "Clothing/accessory props must stay clothing/accessory props.\n"
        "Do not reinterpret wearable objects as weapons/tools unless explicitly stated in user text.\n"
        "If prop is cap/hat/headwear, it must remain headwear. baseball cap is not baseball bat.\n"
        "If character ref attachment failed, keep character visuals conservative: keep only reliable role/gender-energy hints and avoid specific visual identity claims.\n"
        "At CORE stage do not inject arbitrary accent colors or symbolic props not grounded in refs/audio/text.\n"
        "Do not invent a contradictory hero identity against the attached character image reference.\n"
        "Use the character image reference to infer hero gender presentation (male/female/androgynous), approximate age, visual mood, and core appearance markers.\n"
        "Keep appearance notes compact and production-usable; do not describe every tiny detail, only stable identity-relevant ones.\n"
        "Keep each field compact and actionable.\n"
        "Required keys only: story_summary, opening_anchor, ending_callback_rule, global_arc, identity_lock, world_lock, style_lock.\n"
        "identity_lock/world_lock/style_lock must be JSON objects.\n\n"
        f"{mode_instructions}\n"
        f"story_core_mode={mode}\n\n"
        f"INPUT_SUMMARY:\n{json.dumps(compact_input, ensure_ascii=False)[:3500]}\n\n"
        f"ASSIGNED_ROLES:\n{json.dumps(assigned_roles, ensure_ascii=False)[:1200]}\n\n"
        f"CONTEXT_REFS:\n{json.dumps(refs_inventory, ensure_ascii=False)[:2200]}\n"
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
    refs_inventory = _safe_dict(req.get("context_refs"))
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
        "format": str(_safe_dict(req.get("director_controls")).get("format") or req.get("format") or "9:16"),
        "connected_context_summary": _safe_dict(req.get("connected_context_summary")),
        "refs_by_role": _safe_dict(req.get("refsByRole")),
        "selected_refs": {
            "character_1": str(req.get("selectedCharacterRefUrl") or "").strip(),
            "style": str(req.get("selectedStyleRefUrl") or "").strip(),
            "location": str(req.get("selectedLocationRefUrl") or "").strip(),
            "props": [str(item).strip() for item in _safe_list(req.get("selectedPropsRefUrls")) if str(item).strip()],
        },
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
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    scene_prompts = _safe_dict(package.get("scene_prompts"))
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

    scene_ids: list[str] = []
    for source in (plan_by_scene, prompts_by_scene, role_by_scene):
        for scene_id in source.keys():
            if scene_id and scene_id not in scene_ids:
                scene_ids.append(scene_id)

    final_scenes: list[dict[str, Any]] = []
    for idx, scene_id in enumerate(scene_ids, start=1):
        scene_plan_row = _safe_dict(plan_by_scene.get(scene_id))
        role_row = _safe_dict(role_by_scene.get(scene_id))
        prompt_row = _safe_dict(prompts_by_scene.get(scene_id))
        prompt_notes = _safe_dict(prompt_row.get("prompt_notes"))

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
            "audio_slice_start_sec": audio_slice_start_sec,
            "audio_slice_end_sec": audio_slice_end_sec,
            "audio_slice_expected_duration_sec": audio_slice_expected_duration_sec,
            "prompt_notes": prompt_notes,
            "scene_presence_mode": str(role_row.get("scene_presence_mode") or scene_plan_row.get("scene_presence_mode") or "").strip(),
            "route_reason": str(scene_plan_row.get("route_reason") or "").strip(),
            "motion_intent": str(scene_plan_row.get("motion_intent") or "").strip(),
            "watchability_role": str(scene_plan_row.get("watchability_role") or "").strip(),
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
            first_frame_prompt = _first_text(
                prompt_row.get("first_frame_prompt"),
                prompt_row.get("firstFramePrompt"),
                prompt_row.get("start_frame_prompt"),
                prompt_row.get("startFramePrompt"),
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
    package["diagnostics"] = diagnostics
    _append_diag_event(package, f"final_storyboard built scenes={len(final_scenes)}", stage_id="finalize")
    return package


def _run_story_core_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    assigned_roles = _safe_dict(package.get("assigned_roles"))
    story_core_mode = _detect_story_core_mode(input_pkg)
    fallback = _default_story_core(input_pkg)
    prop_contracts, prop_guard_applied = _normalize_story_core_prop_contracts(input_pkg, refs_inventory)
    ref_attachment_summary = {
        "character_1": {"attached": False, "error": "", "source": ""},
        "props": {"connected": bool(prop_contracts), "contracts_count": len(prop_contracts)},
    }
    grounding_level = "strict" if prop_contracts else "standard"
    prompt = _build_story_core_prompt(
        input_pkg,
        refs_inventory,
        assigned_roles,
        story_core_mode,
        prop_contracts,
        ref_attachment_summary,
        grounding_level,
    )
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
    package["diagnostics"] = diagnostics
    try:
        api_key = str(os.getenv("GEMINI_API_KEY") or "").strip()
        parts: list[dict[str, Any]] = [{"text": prompt}]
        character_ref_url, character_ref_source = _pick_story_core_character_ref(refs_inventory)
        if character_ref_url:
            inline_part, inline_error = _load_image_inline_part(character_ref_url)
            diagnostics = _safe_dict(package.get("diagnostics"))
            diagnostics["story_core_character_ref_source"] = character_ref_source
            if inline_part:
                parts.append(inline_part)
                diagnostics["story_core_character_ref_attached"] = True
                diagnostics["story_core_character_ref_error"] = ""
                diagnostics["story_core_ref_attachment_summary"] = {
                    **_safe_dict(diagnostics.get("story_core_ref_attachment_summary")),
                    "character_1": {"attached": True, "error": "", "source": character_ref_source},
                }
                diagnostics["story_core_grounding_level"] = "strict"
            else:
                diagnostics["story_core_character_ref_attached"] = False
                diagnostics["story_core_character_ref_error"] = str(inline_error or "image_attach_failed")
                diagnostics["story_core_ref_attachment_summary"] = {
                    **_safe_dict(diagnostics.get("story_core_ref_attachment_summary")),
                    "character_1": {
                        "attached": False,
                        "error": str(inline_error or "image_attach_failed"),
                        "source": character_ref_source,
                    },
                }
                diagnostics["story_core_grounding_level"] = "cautious"
            package["diagnostics"] = diagnostics
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
        }
        if not _is_usable_story_core(story_core):
            raise ValueError("story_core_unusable_after_parse")
        package["story_core"] = story_core
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
    local_rel_path = _extract_local_static_asset_relative_path(audio_url)
    if local_rel_path:
        try:
            decoded_rel_path = urllib.parse.unquote(local_rel_path)
            assets_root = Path(ASSETS_DIR).resolve()
            file_path = (assets_root / decoded_rel_path).resolve()
            if assets_root in file_path.parents and file_path.exists() and file_path.is_file():
                return str(file_path), None
        except Exception:
            return "", None

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
    if normalized:
        return normalized
    return windows


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
    duration = _coerce_duration_sec(audio_map.get("duration_sec"))
    sections = _safe_list(audio_map.get("sections"))
    return duration > 0 and bool(sections)


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


def _run_audio_map_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    diagnostics = _safe_dict(package.get("diagnostics"))

    audio_url = str(input_pkg.get("audio_url") or "").strip()
    duration_sec = _coerce_duration_sec(input_pkg.get("audio_duration_sec"))
    content_type = str(input_pkg.get("content_type") or "").strip().lower() or "music_video"
    story_core_mode = str(diagnostics.get("story_core_mode") or _detect_story_core_mode(input_pkg)).strip().lower() or "creative"

    diagnostics["audio_map_source_audio_url"] = audio_url
    diagnostics["audio_map_used_model"] = ""
    diagnostics["audio_map_analysis_mode"] = "timing_heuristics_v1"
    diagnostics["audio_map_used_fallback"] = False
    diagnostics["audio_map_phrase_mode"] = "audio_dynamics_v2"
    diagnostics["audio_map_alignment_source"] = ""
    diagnostics["audio_map_alignment_backend"] = ""
    diagnostics["audio_map_alignment_attempted"] = False
    diagnostics["audio_map_alignment_unavailable_reason"] = ""
    diagnostics["audio_map_alignment_error_detail"] = ""
    diagnostics["audio_map_segmentation_backend"] = "gemini"
    diagnostics["audio_map_segmentation_used_fallback"] = False
    diagnostics["audio_map_segmentation_validation_error"] = ""
    diagnostics["audio_map_segmentation_prompt_version"] = ""
    diagnostics["audio_map_segmentation_error"] = ""
    diagnostics["audio_map_segmentation_error_detail"] = ""
    diagnostics["audio_map_segmentation_payload_summary"] = {}
    diagnostics["audio_map_segmentation_normalized_summary"] = {}
    diagnostics["audio_segmentation_source_mode"] = "none"
    diagnostics["audio_segmentation_local_path_found"] = False
    diagnostics["audio_segmentation_inline_attempted"] = False
    diagnostics["audio_segmentation_inline_bytes_size"] = 0
    diagnostics["audio_segmentation_url_used"] = ""
    diagnostics["audio_segmentation_transport_error"] = ""
    diagnostics["audio_map_stage_branch"] = ""
    diagnostics["audio_map_primary_fallback_reason"] = ""
    diagnostics["audio_map_dynamics_error"] = ""
    diagnostics["audio_map_primary_source"] = "fallback"
    diagnostics["audio_map_model_led_segmentation"] = False
    diagnostics["audio_map_validation_flags"] = []
    diagnostics["audio_map_backend_repair_applied"] = False
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

    if not _is_usable_story_core(story_core):
        raise RuntimeError("audio_map_requires_story_core")

    if duration_sec <= 0:
        warnings = _safe_list(diagnostics.get("warnings"))
        warnings.append({"stage_id": "audio_map", "message": "audio_duration_missing_or_invalid"})
        diagnostics["warnings"] = warnings[-80:]
        diagnostics["audio_map_used_fallback"] = True
        package["diagnostics"] = diagnostics
        raise RuntimeError("audio_map_duration_missing")

    used_fallback = False
    fallback_reason = ""
    primary_source = "fallback"
    model_led_segmentation = False
    analysis_mode = "timing_heuristics_v1"
    transcript_text = _extract_audio_transcript_text(input_pkg)
    provided_alignment = _extract_input_alignment_payload(input_pkg)
    try:
        if audio_url:
            analysis_path = ""
            cleanup_path: str | None = None
            raw_analysis: dict[str, Any] = {}
            try:
                analysis_path, cleanup_path = _resolve_audio_analysis_path(audio_url)
                if not analysis_path:
                    diagnostics["audio_map_dynamics_error"] = "audio_source_unavailable_for_dynamics"
                    _append_diag_event(
                        package,
                        "audio_map dynamics unavailable: source not resolved; proceeding with gemini segmentation",
                        stage_id="audio_map",
                    )
                else:
                    try:
                        raw_analysis = analyze_audio(analysis_path, debug=False)
                    except Exception as analysis_exc:  # noqa: BLE001
                        diagnostics["audio_map_dynamics_error"] = str(analysis_exc)
                        raw_analysis = {}
                        _append_diag_event(
                            package,
                            f"audio_map dynamics analysis failed; proceeding with gemini segmentation: {analysis_exc}",
                            stage_id="audio_map",
                        )
                _append_diag_event(package, "audio_map gemini segmentation requested", stage_id="audio_map")
                gemini_result = build_gemini_audio_segmentation(
                    api_key=str(os.getenv("GEMINI_API_KEY") or "").strip(),
                    audio_path=analysis_path,
                    audio_url=audio_url,
                    duration_sec=duration_sec,
                    story_core=story_core,
                    content_type=content_type,
                    story_core_mode=story_core_mode,
                    narrative_directive=transcript_text,
                    director_note=str(input_pkg.get("director_note") or input_pkg.get("note") or ""),
                )
                diagnostics["audio_map_segmentation_prompt_version"] = str(gemini_result.get("prompt_version") or "")
                diagnostics["audio_map_used_model"] = str(gemini_result.get("used_model") or diagnostics.get("audio_map_used_model") or "")
                diagnostics["audio_map_segmentation_validation_error"] = str(gemini_result.get("validation_error") or "")
                diagnostics["audio_map_segmentation_payload_summary"] = _safe_dict(gemini_result.get("payload_summary"))
                diagnostics["audio_map_segmentation_normalized_summary"] = _safe_dict(gemini_result.get("normalized_summary"))
                transport_meta = _safe_dict(gemini_result.get("transport_meta"))
                diagnostics["audio_segmentation_source_mode"] = str(transport_meta.get("audio_segmentation_source_mode") or "none")
                diagnostics["audio_segmentation_local_path_found"] = bool(transport_meta.get("audio_segmentation_local_path_found"))
                diagnostics["audio_segmentation_inline_attempted"] = bool(transport_meta.get("audio_segmentation_inline_attempted"))
                diagnostics["audio_segmentation_inline_bytes_size"] = int(transport_meta.get("audio_segmentation_inline_bytes_size") or 0)
                diagnostics["audio_segmentation_url_used"] = str(transport_meta.get("audio_segmentation_url_used") or "")
                diagnostics["audio_segmentation_transport_error"] = str(transport_meta.get("audio_segmentation_transport_error") or "")
                _append_diag_event(
                    package,
                    "audio_map gemini result "
                    f"ok={bool(gemini_result.get('ok'))} "
                    f"prompt={diagnostics.get('audio_map_segmentation_prompt_version') or ''} "
                    f"model={diagnostics.get('audio_map_used_model') or ''} "
                    f"source_mode={diagnostics.get('audio_segmentation_source_mode') or ''} "
                    f"local_path_found={bool(diagnostics.get('audio_segmentation_local_path_found'))} "
                    f"inline_attempted={bool(diagnostics.get('audio_segmentation_inline_attempted'))} "
                    f"inline_bytes={int(diagnostics.get('audio_segmentation_inline_bytes_size') or 0)} "
                    f"url_used={diagnostics.get('audio_segmentation_url_used') or ''} "
                    f"transport_error={diagnostics.get('audio_segmentation_transport_error') or ''} "
                    f"validation_error={diagnostics.get('audio_map_segmentation_validation_error') or ''} "
                    f"error={str(gemini_result.get('error') or '')}",
                    stage_id="audio_map",
                )
                gemini_payload = _safe_dict(gemini_result.get("payload"))
                has_segmentation_rows = bool(
                    _safe_list(gemini_payload.get("scene_candidate_windows")) or _safe_list(gemini_payload.get("phrase_units"))
                )
                can_try_music_video_direct_accept = bool(
                    content_type == "music_video"
                    and gemini_payload
                    and has_segmentation_rows
                )
                if bool(gemini_result.get("ok")) or can_try_music_video_direct_accept:
                    payload = gemini_payload
                    diagnostics["audio_map_segmentation_normalized_summary"] = _summarize_audio_map_payload(payload)
                    candidate_map = _build_audio_map_from_gemini_payload(
                        payload,
                        duration_sec,
                        analysis_mode="gemini_semantic_segmentation_v1",
                    )
                    hard_validation_error = ""
                    if content_type == "music_video":
                        candidate_map, hard_validation_error = _finalize_music_video_model_led_audio_map(candidate_map, duration_sec)
                        if hard_validation_error:
                            raise ValueError(f"gemini_hard_validation_failed:{hard_validation_error}")
                    audio_map = candidate_map
                    analysis_mode = "gemini_semantic_segmentation_v1"
                    primary_source = "gemini"
                    model_led_segmentation = True
                    diagnostics["audio_map_segmentation_backend"] = "gemini"
                    diagnostics["audio_map_segmentation_error"] = ""
                    diagnostics["audio_map_segmentation_error_detail"] = ""
                    diagnostics["audio_map_segmentation_validation_error"] = hard_validation_error
                    diagnostics["audio_map_stage_branch"] = "gemini_primary"
                    _append_diag_event(package, "audio_map gemini segmentation resolved", stage_id="audio_map")
                else:
                    gemini_error = str(gemini_result.get("error") or "gemini_segmentation_failed")
                    diagnostics["audio_map_segmentation_used_fallback"] = True
                    diagnostics["audio_map_segmentation_backend"] = "gemini"
                    diagnostics["audio_map_segmentation_error"] = gemini_error
                    diagnostics["audio_map_segmentation_error_detail"] = str(gemini_result.get("validation_error") or "")
                    diagnostics["audio_map_stage_branch"] = "fallback_after_gemini_failure"
                    _append_diag_event(
                        package,
                        f"audio_map gemini segmentation invalid, falling back to local splitter: {gemini_error}",
                        stage_id="audio_map",
                    )
                    if content_type == "music_video":
                        if raw_analysis:
                            analysis_mode = "music_video_emergency_fallback_v1"
                            audio_map = _build_audio_map_from_dynamics(
                                duration_sec,
                                story_core,
                                raw_analysis,
                                analysis_mode=analysis_mode,
                            )
                            diagnostics["audio_map_backend_repair_applied"] = True
                            _append_diag_event(
                                package,
                                "audio_map emergency fallback applied (music_video dynamics)",
                                stage_id="audio_map",
                            )
                        else:
                            analysis_mode = "timing_heuristics_v1"
                            audio_map = _build_audio_map_from_duration(
                                duration_sec,
                                story_core,
                                analysis_mode=analysis_mode,
                                content_type=content_type,
                            )
                            _append_diag_event(
                                package,
                                "audio_map emergency fallback applied (music_video duration-only)",
                                stage_id="audio_map",
                            )
                    else:
                        alignment, alignment_diag = resolve_transcript_alignment_with_diagnostics(
                            audio_path=analysis_path,
                            duration_sec=duration_sec,
                            transcript_hint=transcript_text,
                            provided_alignment=provided_alignment,
                        )
                        alignment_reason = str(alignment_diag.get("reason") or "").strip()
                        diagnostics["audio_map_alignment_backend"] = str(alignment_diag.get("backend") or "local")
                        diagnostics["audio_map_alignment_attempted"] = bool(alignment_diag.get("attempted"))
                        diagnostics["audio_map_alignment_unavailable_reason"] = alignment_reason
                        diagnostics["audio_map_alignment_error_detail"] = str(alignment_diag.get("error_detail") or "")
                        if alignment:
                            aligned_map = _build_audio_map_from_real_alignment(duration_sec, story_core, raw_analysis, alignment)
                            if aligned_map and _is_usable_audio_map(aligned_map):
                                analysis_mode = "transcript_alignment_v2"
                                audio_map = aligned_map
                                diagnostics["audio_map_alignment_unavailable_reason"] = ""
                                diagnostics["audio_map_alignment_error_detail"] = ""
                                _append_diag_event(package, "audio_map transcript alignment resolved", stage_id="audio_map")
                            else:
                                diagnostics["audio_map_alignment_unavailable_reason"] = "aligned_audio_map_unusable"
                                alignment = None
                        if not alignment:
                            failure_reason = str(diagnostics.get("audio_map_alignment_unavailable_reason") or "alignment_unavailable")
                            _append_diag_event(
                                package,
                                f"audio_map transcript alignment failed: {failure_reason}",
                                stage_id="audio_map",
                            )
                            phrase_first_map = _build_phrase_first_audio_map(duration_sec, story_core, raw_analysis, transcript_text)
                            if phrase_first_map:
                                analysis_mode = "approximate_phrase_grouping_v1"
                                audio_map = phrase_first_map
                                audio_map["analysis_mode"] = "approximate_phrase_grouping_v1"
                                audio_map["transcript_available"] = bool(transcript_text)
                                audio_map["audio_map_alignment_source"] = "approximate_transcript_grouping"
                            else:
                                analysis_mode = "audio_dynamics_v2"
                                audio_map = _build_audio_map_from_dynamics(
                                    duration_sec,
                                    story_core,
                                    raw_analysis,
                                    analysis_mode=analysis_mode,
                                )
                        if analysis_mode == "transcript_alignment_v2":
                            audio_map["audio_map_alignment_source"] = str(_safe_dict(audio_map.get("transcript_alignment")).get("source") or "")
                        else:
                            audio_map.setdefault("audio_map_alignment_source", "")
            finally:
                if cleanup_path:
                    try:
                        os.unlink(cleanup_path)
                    except OSError:
                        pass
        else:
            diagnostics["audio_map_stage_branch"] = "duration_only_no_audio_url"
            audio_map = _build_audio_map_from_duration(
                duration_sec,
                story_core,
                analysis_mode=analysis_mode,
                content_type=content_type,
            )
        if not _is_usable_audio_map(audio_map):
            raise ValueError("audio_map_unusable_primary")
    except Exception as exc:  # noqa: BLE001
        used_fallback = True
        fallback_reason = str(exc)
        analysis_mode = "timing_heuristics_v1"
        audio_map = _build_audio_map_from_duration(
            duration_sec,
            story_core,
            analysis_mode=analysis_mode,
            content_type=content_type,
        )
        if not _is_usable_audio_map(audio_map):
            raise RuntimeError(f"audio_map_failed_no_fallback:{exc}") from exc
        warnings = _safe_list(diagnostics.get("warnings"))
        warnings.append({"stage_id": "audio_map", "message": f"fallback_used:{exc}"})
        diagnostics["warnings"] = warnings[-80:]
        _append_diag_event(package, f"audio_map fallback used: {exc}", stage_id="audio_map")

    audio_map["content_type"] = content_type
    audio_map["story_core_mode"] = story_core_mode
    audio_map["story_core_arc_ref"] = str(story_core.get("global_arc") or "")
    audio_map.setdefault(
        "transcript_available",
        bool(transcript_text and str(audio_map.get("analysis_mode") or "") in {"transcript_alignment_v2", "approximate_phrase_grouping_v1"}),
    )
    audio_map.setdefault("phrase_units", [])
    audio_map.setdefault("scene_candidate_windows", [])
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
    if not audio_url:
        warnings = _safe_list(diagnostics.get("warnings"))
        warnings.append({"stage_id": "audio_map", "message": "audio_url_missing_used_duration_only"})
        diagnostics["warnings"] = warnings[-80:]

    grid_metrics = _audio_map_grid_metrics(audio_map)
    validation_flags = _validate_audio_map_soft(audio_map)
    audio_dynamics_summary = _safe_dict(audio_map.get("audio_dynamics_summary"))
    dynamics_available = bool(
        int(audio_dynamics_summary.get("pause_points_count") or 0)
        or int(audio_dynamics_summary.get("phrase_points_count") or 0)
        or int(audio_dynamics_summary.get("energy_peaks_count") or 0)
        or int(audio_dynamics_summary.get("detected_sections_count") or 0)
    )
    music_signal_mode = "dynamics+phrase_like_boundaries" if dynamics_available else "duration_fallback_non_grid"
    audio_map.update(grid_metrics)
    audio_map["audio_map_primary_source"] = primary_source
    audio_map["audio_map_model_led_segmentation"] = model_led_segmentation
    audio_map["audio_map_validation_flags"] = validation_flags
    audio_map["audio_map_backend_repair_applied"] = bool(diagnostics.get("audio_map_backend_repair_applied"))
    audio_map["audio_map_music_signal_mode"] = music_signal_mode
    audio_map["audio_map_dynamics_available"] = dynamics_available

    diagnostics["audio_map_analysis_mode"] = analysis_mode
    diagnostics["audio_map_used_fallback"] = bool(used_fallback)
    if not fallback_reason and primary_source != "gemini":
        fallback_reason = (
            str(diagnostics.get("audio_map_segmentation_error") or "")
            or str(diagnostics.get("audio_map_dynamics_error") or "")
            or str(analysis_mode or "timing_heuristics_v1")
        )
    diagnostics["audio_map_primary_fallback_reason"] = fallback_reason
    diagnostics["audio_map_segmentation_backend"] = "gemini" if primary_source == "gemini" else "local_fallback"
    diagnostics["audio_map_segmentation_used_fallback"] = bool(
        diagnostics.get("audio_map_segmentation_used_fallback")
        or analysis_mode in {"transcript_alignment_v2", "approximate_phrase_grouping_v1", "audio_dynamics_v2", "timing_heuristics_v1"}
    )
    diagnostics["audio_map_segmentation_error"] = str(diagnostics.get("audio_map_segmentation_error") or "")
    diagnostics["audio_map_segmentation_error_detail"] = str(diagnostics.get("audio_map_segmentation_error_detail") or "")
    diagnostics["audio_map_validation_flags"] = validation_flags
    diagnostics["audio_map_primary_source"] = primary_source
    diagnostics["audio_map_model_led_segmentation"] = model_led_segmentation
    diagnostics["audio_map_backend_repair_applied"] = bool(diagnostics.get("audio_map_backend_repair_applied"))
    diagnostics["audio_map_phrase_mode"] = str(audio_map.get("analysis_mode") or analysis_mode or "timing_heuristics_v1")
    diagnostics["transcript_available"] = bool(audio_map.get("transcript_available"))
    diagnostics["word_timestamp_count"] = len(_safe_list(_safe_dict(audio_map.get("transcript_alignment")).get("words")))
    diagnostics["phrase_unit_count"] = len(_safe_list(audio_map.get("phrase_units")))
    diagnostics["scene_candidate_count"] = len(_safe_list(audio_map.get("scene_candidate_windows")))
    diagnostics["audio_map_alignment_source"] = str(audio_map.get("audio_map_alignment_source") or "")
    diagnostics["audio_map_alignment_backend"] = str(diagnostics.get("audio_map_alignment_backend") or "")
    diagnostics["audio_map_alignment_attempted"] = bool(diagnostics.get("audio_map_alignment_attempted"))
    diagnostics["audio_map_alignment_unavailable_reason"] = str(diagnostics.get("audio_map_alignment_unavailable_reason") or "")
    diagnostics["audio_map_alignment_error_detail"] = str(diagnostics.get("audio_map_alignment_error_detail") or "")
    diagnostics.update(grid_metrics)
    diagnostics["audio_map_music_signal_mode"] = music_signal_mode
    diagnostics["audio_map_dynamics_available"] = dynamics_available
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
        package["role_plan"] = {}
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
    package["role_plan"] = role_plan

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
    diagnostics["scene_plan_has_adjacent_ia2v"] = False
    diagnostics["scene_plan_has_adjacent_first_last"] = False
    diagnostics["scene_plan_route_spacing_warning"] = False
    diagnostics["scene_plan_validation_error"] = ""
    diagnostics["scene_plan_error"] = ""
    diagnostics["scene_plan_empty"] = False
    package["diagnostics"] = diagnostics

    result = build_gemini_scene_plan(
        api_key=str(os.getenv("GEMINI_API_KEY") or "").strip(),
        package=package,
    )
    scene_plan = _safe_dict(result.get("scene_plan"))
    package["scene_plan"] = scene_plan

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
    diagnostics["scene_plan_has_adjacent_ia2v"] = bool(scene_diag.get("scene_plan_has_adjacent_ia2v"))
    diagnostics["scene_plan_has_adjacent_first_last"] = bool(scene_diag.get("scene_plan_has_adjacent_first_last"))
    diagnostics["scene_plan_route_spacing_warning"] = bool(scene_diag.get("scene_plan_route_spacing_warning"))
    diagnostics["scene_plan_validation_error"] = str(result.get("validation_error") or "")
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
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["scene_prompts_backend"] = "gemini"
    diagnostics["scene_prompts_prompt_version"] = SCENE_PROMPTS_PROMPT_VERSION
    diagnostics["scene_prompts_used_fallback"] = False
    diagnostics["scene_prompts_scene_count"] = 0
    diagnostics["scene_prompts_missing_photo_count"] = 0
    diagnostics["scene_prompts_missing_video_count"] = 0
    diagnostics["scene_prompts_ia2v_audio_driven_count"] = 0
    diagnostics["scene_prompts_route_semantics_mismatch_count"] = 0
    diagnostics["scene_prompts_validation_error"] = ""
    diagnostics["scene_prompts_error"] = ""
    diagnostics["scene_prompts_empty"] = False
    package["diagnostics"] = diagnostics

    result = build_gemini_scene_prompts(
        api_key=str(os.getenv("GEMINI_API_KEY") or "").strip(),
        package=package,
    )

    scene_prompts = _safe_dict(result.get("scene_prompts"))
    package["scene_prompts"] = scene_prompts

    prompts_diag = _safe_dict(result.get("diagnostics"))
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["scene_prompts_backend"] = "gemini"
    diagnostics["scene_prompts_prompt_version"] = str(prompts_diag.get("prompt_version") or SCENE_PROMPTS_PROMPT_VERSION)
    diagnostics["scene_prompts_used_fallback"] = bool(result.get("used_fallback"))
    diagnostics["scene_prompts_scene_count"] = int(prompts_diag.get("scene_count") or len(_safe_list(scene_prompts.get("scenes"))))
    diagnostics["scene_prompts_missing_photo_count"] = int(prompts_diag.get("missing_photo_count") or 0)
    diagnostics["scene_prompts_missing_video_count"] = int(prompts_diag.get("missing_video_count") or 0)
    diagnostics["scene_prompts_ia2v_audio_driven_count"] = int(prompts_diag.get("ia2v_audio_driven_count") or 0)
    diagnostics["scene_prompts_route_semantics_mismatch_count"] = int(
        prompts_diag.get("scene_prompts_route_semantics_mismatch_count") or 0
    )
    diagnostics["scene_prompts_validation_error"] = str(result.get("validation_error") or "")
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
        elif stage_id == "story_core":
            pkg = _run_story_core_stage(pkg)
        elif stage_id == "audio_map":
            pkg = _run_audio_map_stage(pkg)
        elif stage_id == "role_plan":
            pkg = _run_role_plan_stage(pkg)
        elif stage_id == "scene_plan":
            pkg = _run_scene_plan_stage(pkg)
        elif stage_id == "scene_prompts":
            pkg = _run_scene_prompts_stage(pkg)
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


def run_manual_stage(stage_id: str, package: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if stage_id not in STAGE_IDS:
        raise ValueError(f"unknown_stage:{stage_id}")
    pkg = deepcopy(_safe_dict(package)) if package else create_storyboard_package(payload)
    if stage_id == "story_core":
        pkg = run_stage("input_package", pkg, payload)
        if str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get("input_package")).get("status") or "") == "error":
            return pkg
    pkg = invalidate_downstream_stages(pkg, stage_id, reason=f"manual_rerun:{stage_id}")
    return run_stage(stage_id, pkg, payload)


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
