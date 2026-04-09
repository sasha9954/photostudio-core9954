from __future__ import annotations

import base64
import json
import logging
import mimetypes
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.static_paths import ASSETS_DIR
from app.engine.gemini_rest import post_generate_content

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
    "role_plan": ["story_core"],
    "scene_plan": ["audio_map", "role_plan"],
    "scene_prompts": ["scene_plan"],
    "finalize": ["scene_prompts"],
}

DOWNSTREAM_BY_STAGE: dict[str, list[str]] = {
    stage_id: [candidate for candidate, deps in STAGE_DEPENDENCIES.items() if stage_id in deps]
    for stage_id in STAGE_IDS
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


def _run_story_core_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    assigned_roles = _safe_dict(package.get("assigned_roles"))
    story_core_mode = _detect_story_core_mode(input_pkg)
    fallback = _default_story_core(input_pkg)
    prompt = _build_story_core_prompt(input_pkg, refs_inventory, assigned_roles, story_core_mode)
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["story_core_mode"] = story_core_mode
    diagnostics["story_core_character_ref_attached"] = False
    diagnostics["story_core_character_ref_source"] = ""
    diagnostics["story_core_character_ref_error"] = ""
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
            else:
                diagnostics["story_core_character_ref_attached"] = False
                diagnostics["story_core_character_ref_error"] = str(inline_error or "image_attach_failed")
            package["diagnostics"] = diagnostics
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        }
        response = post_generate_content(
            api_key=api_key,
            model="gemini-2.5-pro",
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

    # phrase endpoints: section boundaries + regular phrase cadence inside each section.
    phrase_step = 4.0 if duration <= 90 else 6.0
    phrase_points: list[float] = []
    if duration > 0:
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
    return {
        "duration_sec": round(duration, 3),
        "analysis_mode": analysis_mode,
        "sections": sections,
        "phrase_endpoints_sec": phrase_endpoints,
        "no_split_ranges": no_split_ranges,
        "candidate_cut_points_sec": sorted(set(candidate_cut_points)),
        "pacing_profile": {
            "segment_count": len(sections),
            "phrase_step_sec": phrase_step,
            "dominant_energy": dominant_energy,
        },
        "mood_progression": mood_progression,
        "audio_arc_summary": f"Audio map follows story_core arc '{arc_short}' with {len(sections)} timing sections.",
        "section_summary": [f"{sec.get('label')}:{sec.get('energy')}/{sec.get('mood')}" for sec in sections],
        "lip_sync_candidate_ranges": lip_sync_ranges,
    }


def _is_usable_audio_map(audio_map: dict[str, Any]) -> bool:
    if not isinstance(audio_map, dict):
        return False
    duration = _coerce_duration_sec(audio_map.get("duration_sec"))
    sections = _safe_list(audio_map.get("sections"))
    return duration > 0 and bool(sections)


def _run_audio_map_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    diagnostics = _safe_dict(package.get("diagnostics"))

    audio_url = str(input_pkg.get("audio_url") or "").strip()
    duration_sec = _coerce_duration_sec(input_pkg.get("audio_duration_sec"))
    content_type = str(input_pkg.get("content_type") or "").strip().lower() or "music_video"
    story_core_mode = str(diagnostics.get("story_core_mode") or _detect_story_core_mode(input_pkg)).strip().lower() or "creative"

    diagnostics["audio_map_source_audio_url"] = audio_url
    diagnostics["audio_map_analysis_mode"] = "timing_heuristics_v1"
    diagnostics["audio_map_used_fallback"] = False
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
    analysis_mode = "timing_heuristics_v1"
    try:
        audio_map = _build_audio_map_from_duration(duration_sec, story_core, analysis_mode=analysis_mode)
        if not _is_usable_audio_map(audio_map):
            raise ValueError("audio_map_unusable_primary")
    except Exception as exc:  # noqa: BLE001
        used_fallback = True
        analysis_mode = "duration_fallback_v1"
        audio_map = _build_audio_map_from_duration(duration_sec, story_core, analysis_mode=analysis_mode)
        if not _is_usable_audio_map(audio_map):
            raise RuntimeError(f"audio_map_failed_no_fallback:{exc}") from exc
        warnings = _safe_list(diagnostics.get("warnings"))
        warnings.append({"stage_id": "audio_map", "message": f"fallback_used:{exc}"})
        diagnostics["warnings"] = warnings[-80:]
        _append_diag_event(package, f"audio_map fallback used: {exc}", stage_id="audio_map")

    audio_map["content_type"] = content_type
    audio_map["story_core_mode"] = story_core_mode
    audio_map["story_core_arc_ref"] = str(story_core.get("global_arc") or "")
    if not audio_url:
        warnings = _safe_list(diagnostics.get("warnings"))
        warnings.append({"stage_id": "audio_map", "message": "audio_url_missing_used_duration_only"})
        diagnostics["warnings"] = warnings[-80:]

    diagnostics["audio_map_analysis_mode"] = analysis_mode
    diagnostics["audio_map_used_fallback"] = bool(used_fallback)
    package["diagnostics"] = diagnostics
    package["audio_map"] = audio_map
    _append_diag_event(package, "audio_map generated", stage_id="audio_map")
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
        _set_stage_status(pkg, stage_id, "error", error=f"missing_dependencies:{','.join(missing)}")
        _safe_dict(pkg.get("diagnostics")).setdefault("errors", []).append(f"{stage_id}: missing dependencies {missing}")
        return pkg

    try:
        if stage_id == "input_package":
            pkg = _run_input_package_stage(pkg)
        elif stage_id == "story_core":
            pkg = _run_story_core_stage(pkg)
        elif stage_id == "audio_map":
            pkg = _run_audio_map_stage(pkg)
        elif stage_id == "role_plan":
            pkg["role_plan"] = pkg.get("role_plan") or {"status": "placeholder"}
        elif stage_id == "scene_plan":
            pkg["scene_plan"] = pkg.get("scene_plan") or {"scenes": []}
        elif stage_id == "scene_prompts":
            pkg["scene_prompts"] = pkg.get("scene_prompts") or {"scenes": []}
        elif stage_id == "finalize":
            pkg["final_storyboard"] = pkg.get("final_storyboard") or {"scenes": []}
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
