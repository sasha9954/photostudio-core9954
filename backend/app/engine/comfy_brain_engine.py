import base64
import json
import logging
import mimetypes
import re
import socket
import urllib.error
import urllib.request
from typing import Any

from app.core.config import settings
from app.engine.clip_scene_planner import _load_audio_analysis, plan_comfy_clip
from app.engine.comfy_reference_profile import (
    _load_image_inline_part,
    _read_local_static_asset,
    _resolve_reference_url,
    build_reference_profiles,
    summarize_profiles,
)
from app.engine.gemini_rest import post_generate_content

logger = logging.getLogger(__name__)


FALLBACK_GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_ONLY_PLANNER_MODEL_FALLBACKS = [
    FALLBACK_GEMINI_MODEL,
    "gemini-2.5-pro",
]
PROMPT_SYNC_STATUS_SYNCED = "synced"
PROMPT_SYNC_STATUS_NEEDS_SYNC = "needs_sync"
PROMPT_SYNC_STATUS_SYNCING = "syncing"
PROMPT_SYNC_STATUS_SYNC_ERROR = "sync_error"
COMFY_REF_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"]
COMFY_REF_DIRECTIVES = {"hero", "supporting", "environment_required", "required", "optional", "omit"}
COMFY_ACTIVE_DIRECTIVES = {"hero", "supporting", "environment_required", "required"}
COMFY_FALLBACK_ROLE_PRIORITY = ["character_1", "character_2", "character_3", "group", "animal", "location", "props", "style"]
COMFY_PLANNER_MODES = {"legacy", "gemini_only"}
COMFY_GENRES = {"horror", "romance", "comedy", "drama", "action", "thriller", "noir", "dreamy", "melancholy", "fashion", "surreal", "performance", "experimental"}
GEMINI_ONLY_MEDIA_ROLE_PRIORITY = ["character_1", "character_2", "character_3", "group", "animal", "props", "location", "style"]
MAX_GEMINI_IMAGE_PARTS = 8
MAX_GEMINI_AUDIO_INLINE_BYTES = 20 * 1024 * 1024
GEMINI_ONLY_TRANSITION_TYPES = {"start", "continuation", "enter_transition", "justified_cut", "match_cut", "perspective_shift"}
GEMINI_ONLY_HUMAN_ANCHOR_TYPES = {"character", "POV", "human_trace", "none"}
GEMINI_ONLY_VISUAL_MODE_DEFAULT = "cinematic_real_world"


def _to_float(value: Any) -> float | None:
    try:
        n = float(value)
    except Exception:
        return None
    return n if n == n and n != float("inf") and n != float("-inf") else None


def _round_sec(value: float | None) -> float | None:
    return round(float(value), 3) if value is not None else None


def _clean_refs_by_role(refs_by_role: dict[str, Any] | None) -> dict[str, list[dict[str, str]]]:
    roles = COMFY_REF_ROLES
    src = refs_by_role if isinstance(refs_by_role, dict) else {}
    out: dict[str, list[dict[str, str]]] = {}
    for role in roles:
        items = src.get(role)
        clean = []
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                clean.append({"url": url, "name": str(item.get("name") or "").strip()})
        out[role] = clean
    return out


def _resolve_scene_active_roles(
    refs_used: list[str] | dict[str, Any] | None,
    directives: dict[str, str],
    available_roles: set[str],
    primary_role: str,
) -> list[str]:
    selected_from_used: list[str] = []
    if isinstance(refs_used, list):
        selected_from_used = [str(role).strip() for role in refs_used if str(role).strip() in COMFY_REF_ROLES]
    elif isinstance(refs_used, dict):
        selected_from_used = [str(role).strip() for role, include in refs_used.items() if str(role).strip() in COMFY_REF_ROLES and bool(include)]
    selected_from_used = [role for role in selected_from_used if role in available_roles and directives.get(role) != "omit"]

    selected_from_directives = [
        role
        for role in COMFY_REF_ROLES
        if role in available_roles and directives.get(role) in COMFY_ACTIVE_DIRECTIVES and directives.get(role) != "omit"
    ]

    active_roles: list[str] = []
    for role in selected_from_used + selected_from_directives:
        if role not in active_roles:
            active_roles.append(role)

    if not active_roles and primary_role in available_roles and directives.get(primary_role) != "omit":
        active_roles = [primary_role]
    if not active_roles:
        fallback_role = next(
            (role for role in COMFY_FALLBACK_ROLE_PRIORITY if role in available_roles and directives.get(role) != "omit"),
            None,
        )
        if fallback_role:
            active_roles = [fallback_role]
    return active_roles


def _normalize_scene_ref_roles(src: dict[str, Any], available_refs_by_role: dict[str, list[dict[str, str]]] | None) -> tuple[list[str], dict[str, str], str, list[str]]:
    available = available_refs_by_role if isinstance(available_refs_by_role, dict) else {}
    available_roles = {role for role in COMFY_REF_ROLES if isinstance(available.get(role), list) and len(available.get(role) or []) > 0}

    refs_used_raw = src.get("refsUsed")
    refs_used: list[str] = []
    if isinstance(refs_used_raw, list):
        refs_used = [str(role).strip() for role in refs_used_raw if str(role).strip() in COMFY_REF_ROLES]
    elif isinstance(refs_used_raw, dict):
        refs_used = [str(role).strip() for role, include in refs_used_raw.items() if str(role).strip() in COMFY_REF_ROLES and bool(include)]
    refs_used = list(dict.fromkeys([role for role in refs_used if role in available_roles]))

    primary_role = str(src.get("primaryRole") or "").strip()
    if primary_role not in COMFY_REF_ROLES or primary_role not in available_roles:
        primary_role = next((role for role in COMFY_FALLBACK_ROLE_PRIORITY if role in available_roles), "character_1")

    secondary_roles_raw = src.get("secondaryRoles")
    secondary_roles = [
        role for role in ([str(item).strip() for item in secondary_roles_raw] if isinstance(secondary_roles_raw, list) else [])
        if role in COMFY_REF_ROLES and role in available_roles and role != primary_role
    ]
    secondary_roles = list(dict.fromkeys(secondary_roles))

    directives_raw = src.get("refDirectives") if isinstance(src.get("refDirectives"), dict) else {}
    directives: dict[str, str] = {role: "omit" for role in COMFY_REF_ROLES}
    for role, value in directives_raw.items():
        clean_role = str(role).strip()
        clean_value = str(value).strip()
        if clean_role in COMFY_REF_ROLES and clean_value in COMFY_REF_DIRECTIVES:
            directives[clean_role] = clean_value

    directives[primary_role] = "hero" if primary_role in {"character_1", "character_2", "character_3", "group", "animal"} else "required"
    for role in secondary_roles:
        if role == "location":
            directives[role] = "environment_required"
        elif role == "style":
            directives[role] = "optional"
        elif role == "props":
            directives[role] = "required"
        else:
            directives[role] = "supporting"

    for role in refs_used:
        if directives.get(role) == "omit":
            if role == "location":
                directives[role] = "environment_required"
            elif role == "style":
                directives[role] = "optional"
            elif role == "props":
                directives[role] = "required"
            else:
                directives[role] = "supporting"

    active_roles = _resolve_scene_active_roles(refs_used, directives, available_roles, primary_role)

    if primary_role not in active_roles and active_roles:
        primary_role = active_roles[0]
        directives[primary_role] = "hero" if primary_role in {"character_1", "character_2", "character_3", "group", "animal"} else "required"

    return active_roles, directives, primary_role, secondary_roles


def _normalize_genre(value: Any) -> str:
    raw = str(value or "").strip()
    return raw if raw.lower() in COMFY_GENRES else ""


def normalize_comfy_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    mode = str(data.get("mode") or "clip").strip().lower()
    if mode not in {"clip", "kino", "reklama", "scenario"}:
        mode = "clip"
    planner_mode = str(data.get("plannerMode") or "legacy").strip().lower()
    if planner_mode not in COMFY_PLANNER_MODES:
        planner_mode = "legacy"
    output = str(data.get("output") or "comfy image").strip().lower()
    if output not in {"comfy image", "comfy text"}:
        output = "comfy image"

    audio_story_mode = str(data.get("audioStoryMode") or "lyrics_music").strip().lower()
    if audio_story_mode not in {"lyrics_music", "music_only", "music_plus_text", "speech_narrative"}:
        audio_story_mode = "lyrics_music"

    return {
        "mode": mode,
        "plannerMode": planner_mode,
        "output": output,
        "audioStoryMode": audio_story_mode,
        "stylePreset": str(data.get("stylePreset") or "realism").strip().lower(),
        "genre": _normalize_genre(data.get("genre")),
        "freezeStyle": bool(data.get("freezeStyle")),
        "text": str(data.get("text") or "").strip(),
        "lyricsText": str(data.get("lyricsText") or "").strip(),
        "transcriptText": str(data.get("transcriptText") or "").strip(),
        "spokenTextHint": str(data.get("spokenTextHint") or "").strip(),
        "audioSemanticHints": data.get("audioSemanticHints") if isinstance(data.get("audioSemanticHints"), (list, dict, str)) else "",
        "audioSemanticSummary": str(data.get("audioSemanticSummary") or "").strip(),
        "audioUrl": str(data.get("audioUrl") or "").strip(),
        "audioDurationSec": _to_float(data.get("audioDurationSec")),
        "refsByRole": _clean_refs_by_role(data.get("refsByRole")),
        "storyControlMode": str(data.get("storyControlMode") or "").strip(),
        "storyMissionSummary": str(data.get("storyMissionSummary") or "").strip(),
        "timelineSource": str(data.get("timelineSource") or "").strip(),
        "narrativeSource": str(data.get("narrativeSource") or "").strip(),
        "sceneCandidates": data.get("sceneCandidates") if isinstance(data.get("sceneCandidates"), list) else (data.get("scenes") if isinstance(data.get("scenes"), list) else []),
    }


def _summarize_profile_value(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if str(item).strip()]
        return ", ".join(parts[:8])
    if isinstance(value, dict):
        parts = [f"{key}: {str(item).strip()}" for key, item in value.items() if str(item).strip()]
        return "; ".join(parts[:8])
    return ""


def normalize_entity_type(raw_type: Any) -> str:
    value = str(raw_type or "").strip().lower()
    if not value:
        return "unknown"

    compact = value.replace("-", "_").replace(" ", "_")
    direct_map = {
        "human": "human",
        "person": "human",
        "people": "human",
        "character": "human",
        "character_ref": "human",
        "woman": "human",
        "man": "human",
        "girl": "human",
        "boy": "human",
        "actor": "human",
        "actress": "human",
        "animal": "animal",
        "pet": "animal",
        "dog": "animal",
        "cat": "animal",
        "horse": "animal",
        "bird": "animal",
        "wolf": "animal",
        "object": "object",
        "prop": "object",
        "props": "object",
        "item": "object",
        "accessory": "object",
        "thing": "object",
        "location": "location",
        "environment": "location",
        "place": "location",
        "scene": "location",
        "background": "location",
        "style": "style",
        "aesthetic": "style",
        "visual_style": "style",
        "look": "style",
        "group": "group",
        "crowd": "group",
        "people_group": "group",
    }
    if compact in direct_map:
        return direct_map[compact]

    if any(token in compact for token in ["character", "person", "human", "woman", "man", "actor", "actress"]):
        return "human"
    if any(token in compact for token in ["animal", "pet", "dog", "cat", "horse", "bird", "wolf"]):
        return "animal"
    if any(token in compact for token in ["object", "prop", "item", "accessory", "thing"]):
        return "object"
    if any(token in compact for token in ["location", "environment", "place", "scene", "background"]):
        return "location"
    if any(token in compact for token in ["style", "aesthetic", "visual"]):
        return "style"
    if any(token in compact for token in ["group", "crowd", "people"]):
        return "group"
    return "unknown"


def _normalize_transition_type(raw_value: Any, idx: int) -> str:
    value = str(raw_value or "").strip().lower().replace("-", "_").replace(" ", "_")
    alias_map = {
        "continuous": "continuation",
        "continue": "continuation",
        "same_camera": "continuation",
        "single": "justified_cut",
        "hard_cut": "justified_cut",
        "cut": "justified_cut",
        "entry_transition": "enter_transition",
        "enter": "enter_transition",
        "match": "match_cut",
        "pov_shift": "perspective_shift",
    }
    clean = alias_map.get(value, value)
    if clean in GEMINI_ONLY_TRANSITION_TYPES:
        return clean
    return "start" if idx == 0 else "continuation"


def _normalize_human_anchor_type(raw_value: Any, active_refs: list[str], src: dict[str, Any]) -> str:
    value = str(raw_value or "").strip()
    if value in GEMINI_ONLY_HUMAN_ANCHOR_TYPES:
        return value

    if any(role in active_refs for role in ["character_1", "character_2", "character_3", "group"]):
        return "character"

    text_blob = " ".join(
        [
            str(src.get("imagePromptRu") or src.get("imagePrompt") or ""),
            str(src.get("imagePromptEn") or ""),
            str(src.get("videoPromptRu") or src.get("videoPrompt") or ""),
            str(src.get("videoPromptEn") or ""),
            str(src.get("sceneAction") or ""),
            str(src.get("visualDescription") or ""),
            str(src.get("cameraPlan") or src.get("cameraIntent") or ""),
        ]
    ).lower()
    if any(token in text_blob for token in ["pov", "point of view", "first-person", "first person", "through the eyes", "from the explorer's view"]):
        return "POV"
    if any(token in text_blob for token in ["footprint", "footprints", "shadow", "hand", "hands", "glove", "breath", "breathing", "flashlight beam", "helmet cam", "equipment"]):
        return "human_trace"
    return "none"


def _infer_camera_type(camera_text: str) -> str:
    text = str(camera_text or "").strip().lower()
    if not text:
        return "locked_camera"
    camera_markers = [
        ("drone", ["drone", "aerial", "bird's-eye", "birds-eye", "overhead flyover", "helicopter"]),
        ("handheld", ["handheld", "shaky cam", "shoulder cam", "body cam", "helmet cam"]),
        ("dolly", ["dolly", "track", "tracking shot", "slider"]),
        ("crane", ["crane", "jib"]),
        ("steadicam", ["steadicam", "gimbal", "stabilized follow"]),
        ("POV", ["pov", "point of view", "first-person", "first person"]),
        ("static", ["static", "locked off", "tripod", "still frame"]),
        ("push_in", ["push in", "push-in"]),
    ]
    for label, markers in camera_markers:
        if any(marker in text for marker in markers):
            return label
    return "cinematic_camera"


def _extract_audio_mime_type(url: str, headers: dict[str, str], data: bytes) -> str:
    header_mime = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    if header_mime.startswith("audio/"):
        return header_mime
    guessed_from_url, _ = mimetypes.guess_type(url)
    if guessed_from_url and guessed_from_url.startswith("audio/"):
        return guessed_from_url
    if data.startswith(b"ID3") or data[:2] == b"\xff\xfb":
        return "audio/mpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    return ""


def _load_audio_inline_part(audio_url: str) -> tuple[dict[str, Any] | None, str | None, str | None]:
    resolved = _resolve_reference_url(audio_url)
    if not resolved:
        return None, "missing_audio_url", None

    data: bytes
    data_source_for_mime = resolved
    headers: dict[str, str] = {}
    local_data, local_source, local_error = _read_local_static_asset(resolved)
    if local_error and local_error != "local_asset_not_found":
        return None, local_error, None
    if local_data is not None:
        data = local_data
        data_source_for_mime = local_source
    else:
        req = urllib.request.Request(resolved, headers={"User-Agent": "photostudio-gemini-planner/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
                headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            return None, "audio_http_error", f"http_status:{exc.code}"
        except (socket.timeout, TimeoutError):
            return None, "audio_timeout", None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                return None, "audio_timeout", None
            return None, "audio_download_failed", str(exc.reason)[:180] if exc.reason else None
        except ValueError:
            return None, "audio_download_failed", None
        except Exception as exc:
            return None, "audio_download_failed", str(exc)[:180]

    if not data:
        return None, "audio_download_failed", None

    mime_type = _extract_audio_mime_type(data_source_for_mime, headers, data)
    if not mime_type:
        return None, "audio_invalid_mime", None
    if len(data) > MAX_GEMINI_AUDIO_INLINE_BYTES:
        return None, "audio_too_large_for_inline", f"bytes:{len(data)}"

    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(data).decode("ascii"),
        }
    }, "inline_audio_attached", None


def _build_gemini_only_multimodal_parts(normalized: dict[str, Any], gemini_payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    parts: list[dict[str, Any]] = [{"text": _build_gemini_only_planner_prompt(gemini_payload)}]
    refs_by_role = normalized.get("refsByRole") if isinstance(normalized.get("refsByRole"), dict) else {}

    audio_part_attached = False
    audio_attach_reason = "missing_audio_url"
    audio_attach_error = None
    audio_url = str(normalized.get("audioUrl") or "").strip()
    if audio_url:
        audio_part, audio_attach_reason, audio_attach_error = _load_audio_inline_part(audio_url)
        if audio_part:
            parts.append(audio_part)
            audio_part_attached = True

    attached_ref_roles: list[str] = []
    skipped_ref_roles: dict[str, str] = {}
    image_attach_errors: list[str] = []
    image_parts_attached_count = 0

    for role in GEMINI_ONLY_MEDIA_ROLE_PRIORITY:
        if image_parts_attached_count >= MAX_GEMINI_IMAGE_PARTS:
            skipped_ref_roles[role] = "global_image_part_limit_reached"
            continue
        refs = refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []
        if not refs:
            skipped_ref_roles[role] = "no_refs"
            continue

        attached_for_role = False
        first_error = None
        for item in refs[:2]:
            ref_url = str((item or {}).get("url") or "").strip()
            if not ref_url:
                first_error = first_error or "missing_ref_url"
                continue
            image_part, image_error = _load_image_inline_part(ref_url)
            if image_part:
                parts.append({"text": f"Reference image for role {role}."})
                parts.append(image_part)
                attached_ref_roles.append(role)
                image_parts_attached_count += 1
                attached_for_role = True
                break
            first_error = first_error or image_error or "image_attach_failed"
        if attached_for_role:
            continue
        skipped_ref_roles[role] = first_error or "image_attach_failed"
        image_attach_errors.append(f"{role}:{first_error or 'image_attach_failed'}")

    return parts, {
        "audioPartAttached": audio_part_attached,
        "audioAttachReason": audio_attach_reason,
        "audioAttachError": audio_attach_error,
        "imagePartsAttachedCount": image_parts_attached_count,
        "attachedRefRoles": attached_ref_roles,
        "skippedRefRoles": skipped_ref_roles,
        "imageAttachErrors": image_attach_errors,
        "mediaAttachSummary": {
            "audio": "attached" if audio_part_attached else "not_attached",
            "audioReason": audio_attach_reason,
            "imagePartsAttachedCount": image_parts_attached_count,
            "attachedRefRoles": attached_ref_roles,
            "skippedRefRoleCount": len(skipped_ref_roles),
        },
    }


def _collect_world_signal_text(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ["text", "lyricsText", "transcriptText", "spokenTextHint", "audioSemanticSummary", "storyMissionSummary"]:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    hints = payload.get("audioSemanticHints")
    if isinstance(hints, str) and hints.strip():
        parts.append(hints.strip())
    elif isinstance(hints, list):
        parts.extend([str(item).strip() for item in hints if str(item).strip()])
    elif isinstance(hints, dict):
        parts.extend([f"{key}: {str(item).strip()}" for key, item in hints.items() if str(item).strip()])

    scene_candidates = payload.get("sceneCandidates") if isinstance(payload.get("sceneCandidates"), list) else []
    for scene in scene_candidates[:3]:
        if not isinstance(scene, dict):
            continue
        for key in ["sceneMeaning", "visualDescription", "sceneAction", "environmentMotion", "continuity"]:
            value = scene.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    return " ".join(parts).strip().lower()


def _infer_world_detail(signal_text: str, mapping: dict[str, list[str]], fallback: str) -> str:
    haystack = f" {signal_text} "
    for label, variants in mapping.items():
        for variant in variants:
            needle = str(variant or "").strip().lower()
            if needle and f" {needle} " in haystack:
                return label
    return fallback


def _append_unique_strings(items: list[str], additions: list[str]) -> list[str]:
    out: list[str] = []
    for item in [*items, *additions]:
        clean = str(item or "").strip()
        if clean and clean not in out:
            out.append(clean)
    return out


def _has_refs(refs_by_role: dict[str, Any] | None) -> bool:
    refs = refs_by_role if isinstance(refs_by_role, dict) else {}
    return any(isinstance(items, list) and len(items) > 0 for items in refs.values())


def _derive_gemini_only_story_context(payload: dict[str, Any]) -> dict[str, Any]:
    has_audio = bool(str(payload.get("audioUrl") or "").strip())
    text_value = str(payload.get("text") or "").strip()
    has_text = bool(text_value)
    has_refs = _has_refs(payload.get("refsByRole"))
    audio_story_mode = str(payload.get("audioStoryMode") or "lyrics_music").strip().lower() or "lyrics_music"
    transcript_text = str(payload.get("transcriptText") or "").strip()
    spoken_text_hint = str(payload.get("spokenTextHint") or "").strip()
    audio_semantic_summary = str(payload.get("audioSemanticSummary") or "").strip()
    lyrics_text = str(payload.get("lyricsText") or "").strip()
    audio_semantic_hints = payload.get("audioSemanticHints")
    has_audio_semantic_hints = False
    if isinstance(audio_semantic_hints, list):
        has_audio_semantic_hints = any(str(item or "").strip() for item in audio_semantic_hints)
    elif isinstance(audio_semantic_hints, dict):
        has_audio_semantic_hints = any(str(item or "").strip() for item in audio_semantic_hints.values())
    elif isinstance(audio_semantic_hints, str):
        has_audio_semantic_hints = bool(audio_semantic_hints.strip())

    story_source = "none"
    narrative_source = "none"
    timeline_source = str(payload.get("timelineSource") or "").strip()
    story_mission_summary = str(payload.get("storyMissionSummary") or "").strip()
    genre = str(payload.get("genre") or "").strip()
    warnings: list[str] = []
    errors: list[str] = []
    weak_semantic_context = False
    semantic_context_reason = ""

    if has_audio:
        story_source = "audio"
        narrative_source = "audio"
        if audio_story_mode == "speech_narrative":
            timeline_source = "spoken semantic flow"
            if not story_mission_summary:
                story_mission_summary = "Build scenes from spoken meaning and semantic progression."
            semantic_support_present = any([transcript_text, spoken_text_hint, audio_semantic_summary, text_value])
            weak_semantic_context = not semantic_support_present
            if weak_semantic_context:
                semantic_context_reason = "audio present but no transcript/hints/text support"
                warnings.append("weak_semantic_context:audio present but no transcript/hints/text support")
        elif not timeline_source:
            timeline_source = "audio rhythm"
        if not story_mission_summary:
            if audio_story_mode == "music_only":
                story_mission_summary = "Build scenes from audio rhythm and emotional contour."
            elif audio_story_mode == "music_plus_text" and has_text:
                story_mission_summary = text_value[:220]
            else:
                story_mission_summary = "Build scenes from audio meaning, pacing and progression."
    elif has_text:
        story_source = "text"
        narrative_source = "text"
        if not timeline_source:
            timeline_source = "text semantic flow"
        if not story_mission_summary:
            story_mission_summary = text_value[:220]
    else:
        errors.append("no_story_source")
        warnings.append("narrative_source_missing")
        if not timeline_source:
            timeline_source = "none"
        if not story_mission_summary:
            story_mission_summary = "Narrative source missing."

    story_source, narrative_source = _normalize_story_sources(story_source, narrative_source)

    return {
        "storySource": story_source,
        "narrativeSource": narrative_source,
        "timelineSource": timeline_source,
        "storyMissionSummary": story_mission_summary,
        "genre": genre,
        "weakSemanticContext": weak_semantic_context,
        "semanticContextReason": semantic_context_reason,
        "warnings": warnings,
        "errors": errors,
        "hasAudio": has_audio,
        "hasText": has_text,
        "hasRefs": has_refs,
        "hasTranscriptText": bool(transcript_text),
        "hasSpokenTextHint": bool(spoken_text_hint),
        "hasAudioSemanticSummary": bool(audio_semantic_summary),
        "hasAudioSemanticHints": has_audio_semantic_hints,
        "hasLyricsText": bool(lyrics_text),
    }


def _build_gemini_only_model_candidates(requested_model: str) -> list[str]:
    candidates: list[str] = []
    for model in [requested_model, *GEMINI_ONLY_PLANNER_MODEL_FALLBACKS]:
        clean = str(model or "").strip()
        if clean and clean not in candidates:
            candidates.append(clean)
    return candidates


def _normalize_story_sources(story_source: Any, narrative_source: Any) -> tuple[str, str]:
    normalized_story = str(story_source or "").strip().lower()
    if normalized_story not in {"audio", "text", "none"}:
        normalized_story = "none"

    normalized_narrative = str(narrative_source or "").strip().lower()
    if normalized_story == "audio":
        if normalized_narrative not in {"audio", "audio_primary"}:
            normalized_narrative = "audio"
        else:
            normalized_narrative = "audio"
    elif normalized_story == "text":
        if normalized_narrative not in {"text", "text_primary"}:
            normalized_narrative = "text"
        else:
            normalized_narrative = "text"
    else:
        normalized_narrative = "none"

    return normalized_story, normalized_narrative


def _humanize_storyboard_error(error_code: Any) -> str:
    code = str(error_code or "").strip()
    if not code:
        return ""
    if code == "no_story_source":
        return "No audio or text source for storyboard planning"
    if code == "gemini_model_not_supported":
        return "Gemini model is not supported for generateContent"
    if code == "gemini_invalid_json":
        return "Gemini returned invalid JSON"
    if code == "gemini_request_failed":
        return "Gemini request failed"
    if code == "gemini_api_key_missing":
        return "GEMINI_API_KEY is missing"
    if code.startswith("gemini_http_error:"):
        status_code = code.split(":", 1)[1].strip() or "unknown"
        return f"Gemini request failed with HTTP {status_code}"
    return code.replace("_", " ")


def _sanitize_gemini_error(diagnostics: dict[str, Any], resp: dict[str, Any] | None = None) -> tuple[str, str]:
    http_status = diagnostics.get("httpStatus")
    error_text = str((resp or {}).get("text") or diagnostics.get("errorText") or "").strip()
    error_text_l = error_text.lower()
    unsupported_markers = ["not supported", "unsupported", "not found", "generatecontent"]
    looks_unsupported = (
        any(marker in error_text_l for marker in unsupported_markers)
        and "model" in error_text_l
    ) or "model is not supported" in error_text_l or "not supported for generatecontent" in error_text_l or "unsupported for generatecontent" in error_text_l

    if http_status in {400, 404} and looks_unsupported:
        return "gemini_model_not_supported", "Gemini model is not supported for generateContent"
    if http_status:
        return f"gemini_http_error:{http_status}", f"Gemini request failed with HTTP {http_status}"
    if isinstance(resp, dict) and resp.get("errors") == ["gemini_invalid_json"]:
        return "gemini_invalid_json", "Gemini returned invalid JSON"
    return "gemini_request_failed", "Gemini request failed"


def _should_fallback_gemini_model(resp: dict[str, Any] | None, diagnostics: dict[str, Any]) -> bool:
    if not isinstance(resp, dict):
        return False
    http_status = diagnostics.get("httpStatus")
    error_text = str(resp.get("text") or diagnostics.get("errorText") or "").lower()
    if http_status not in {400, 404}:
        return False

    unsupported_markers = [
        "not supported",
        "unsupported",
        "not found",
        "generatecontent",
    ]
    has_unsupported_marker = any(marker in error_text for marker in unsupported_markers)
    model_hint = "model" in error_text or "models/" in error_text

    if http_status == 404:
        return has_unsupported_marker
    if http_status == 400:
        return has_unsupported_marker and model_hint
    return False


def _build_world_lock(payload: dict[str, Any], reference_profiles: dict[str, Any]) -> dict[str, Any]:
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    location_profile = reference_profiles.get("location") if isinstance(reference_profiles.get("location"), dict) else {}
    style_profile = reference_profiles.get("style") if isinstance(reference_profiles.get("style"), dict) else {}
    location_refs = refs_by_role.get("location") if isinstance(refs_by_role.get("location"), list) else []
    style_refs = refs_by_role.get("style") if isinstance(refs_by_role.get("style"), list) else []
    visual_style = str(payload.get("stylePreset") or "realism").strip() or "realism"
    signal_text = _collect_world_signal_text(payload)
    location_name = str(((location_refs[0] or {}).get("name")) if location_refs else "").strip() or "anchored_main_location"
    location_summary = _summarize_profile_value(location_profile.get("visualProfile") or location_profile.get("summary")) or location_name
    style_summary = _summarize_profile_value(style_profile.get("visualProfile") or style_profile.get("summary")) or visual_style
    location_visual = location_profile.get("visualProfile") if isinstance(location_profile.get("visualProfile"), dict) else {}
    style_visual = style_profile.get("visualProfile") if isinstance(style_profile.get("visualProfile"), dict) else {}

    environment_type = _summarize_profile_value(location_visual.get("environmentType")) or _infer_world_detail(
        signal_text,
        {
            "desert": ["desert", "dune", "dunes", "sandstorm", "arid"],
            "bunker": ["bunker", "blast door", "underground base", "missile silo", "tunnel"],
            "forest": ["forest", "woods", "woodland", "oak", "pine", "jungle"],
            "city": ["city", "street", "downtown", "urban", "skyscraper"],
            "industrial": ["industrial", "factory", "warehouse", "plant", "concrete complex"],
        },
        location_name or "anchored_main_location",
    )
    environment_subtype = _summarize_profile_value(location_profile.get("subtype")) or _infer_world_detail(
        signal_text,
        {
            "oak forest": ["oak forest", "oak woods", "oak grove"],
            "pine forest": ["pine forest", "conifer forest", "taiga"],
            "concrete bunker": ["concrete bunker", "brutalist bunker", "reinforced bunker"],
            "sand dunes": ["sand dunes", "dunes", "erg"],
            "industrial city": ["industrial city", "factory district", "port city"],
        },
        _summarize_profile_value(location_profile.get("entityType")) or "single_continuous_world",
    )
    time_of_day = _summarize_profile_value(style_profile.get("timeOfDay")) or _infer_world_detail(
        signal_text,
        {
            "sunset": ["sunset", "golden hour", "dusk"],
            "night": ["night", "moonlight", "midnight", "after dark"],
            "artificial light": ["artificial light", "fluorescent", "flashlight", "emergency light", "neon"],
            "day": ["day", "daylight", "morning", "noon", "afternoon"],
        },
        "locked_from_input",
    )
    lighting_model = _summarize_profile_value(style_profile.get("lightingLogic") or style_profile.get("lighting")) or _infer_world_detail(
        signal_text,
        {
            "natural sunlight": ["sunlight", "natural light", "daylight", "sunlit"],
            "flashlight": ["flashlight", "torch beam", "searchlight"],
            "industrial": ["industrial light", "fluorescent", "sodium vapor", "warehouse lighting"],
            "firelight": ["firelight", "torchlight", "ember glow"],
        },
        f"{visual_style} continuity lighting",
    )
    atmosphere = _summarize_profile_value(style_profile.get("atmosphere")) or _infer_world_detail(
        signal_text,
        {
            "dusty": ["dusty", "dust", "grit", "sand haze"],
            "humid": ["humid", "wet air", "sweaty", "tropical"],
            "fog": ["fog", "mist", "haze"],
            "dry heat": ["dry heat", "arid heat", "heat shimmer"],
            "sterile industrial air": ["sterile", "clinical", "recycled air"],
        },
        f"{visual_style} atmosphere continuity",
    )
    material_language = _summarize_profile_value(location_profile.get("materials") or location_visual.get("surfaceState") or location_profile.get("visualProfile")) or _infer_world_detail(
        signal_text,
        {
            "sand": ["sand", "dune", "dust"],
            "concrete": ["concrete", "cement", "reinforced"],
            "metal": ["metal", "steel", "iron", "aluminum"],
            "wood": ["wood", "timber", "oak", "pine"],
            "stone": ["stone", "rock", "basalt", "granite"],
        },
        "preserve dominant material language",
    )
    color_palette = _summarize_profile_value(style_profile.get("palette") or style_profile.get("visualProfile")) or _infer_world_detail(
        signal_text,
        {
            "warm desert": ["warm desert", "amber sand", "sun-baked", "ochre"],
            "cold industrial": ["cold industrial", "steel blue", "cyan gray", "fluorescent gray"],
            "earthy forest": ["earthy forest", "moss green", "brown bark", "green canopy"],
            "neon urban": ["neon", "magenta", "electric blue", "city glow"],
        },
        style_summary,
    )
    continuity_rules = [
        "Keep one continuous world unless narration explicitly transitions elsewhere.",
        "Do not change location family, time of day, lighting logic, or material language without a story cue.",
        "References constrain world identity; audio/text drive semantic scene selection inside that world.",
    ]
    world_continuity_rules = [
        "environment must remain consistent unless explicit transition",
        "lighting must remain physically consistent",
        "materials must not change randomly",
        "vegetation type must not change (oak ≠ pine)",
        "architecture style must remain stable",
    ]
    forbidden_world_changes = [
        "changing forest type without transition",
        "changing desert type or sand color dramatically",
        "switching from natural light to artificial without cause",
        "changing architecture language (brutalist → sci-fi)",
    ]
    return {
        "worldType": location_summary or location_name,
        "locationType": location_name,
        "locationSubtype": environment_subtype,
        "environmentType": environment_type,
        "environmentSubtype": environment_subtype,
        "timeOfDay": time_of_day,
        "time_of_day": time_of_day,
        "lighting": lighting_model,
        "lighting_model": lighting_model,
        "shadows": _summarize_profile_value(style_profile.get("shadowLogic")) or "keep shadow logic stable scene to scene",
        "weather": _summarize_profile_value(style_profile.get("weather")) or "hold stable unless story explicitly transitions",
        "materials": material_language,
        "material_language": material_language,
        "architecture": _summarize_profile_value(location_profile.get("architecture") or location_profile.get("visualProfile")) or "preserve architecture language",
        "vegetation": _summarize_profile_value(location_profile.get("vegetation")) or "do not drift vegetation family without transition",
        "spacePhysics": _summarize_profile_value(location_profile.get("spacePhysics")) or "consistent spatial physics and scale",
        "palette": color_palette,
        "color_palette": color_palette,
        "atmosphere": atmosphere,
        "continuityRules": _append_unique_strings(continuity_rules, world_continuity_rules),
        "world_continuity_rules": world_continuity_rules,
        "forbiddenWorldDrift": _append_unique_strings(
            [
                "No abrupt biome swap without transition.",
                "No unexplained day/night jump.",
                "No random architecture or weather reset between adjacent scenes.",
            ],
            forbidden_world_changes,
        ),
        "forbidden_world_changes": forbidden_world_changes,
        "signalSources": {
            "textInput": bool(str(payload.get("text") or "").strip()),
            "audioMeaning": bool(str(payload.get("transcriptText") or payload.get("audioSemanticSummary") or payload.get("spokenTextHint") or "").strip()),
            "firstScenes": bool(payload.get("sceneCandidates")),
        },
        "sourceRefs": {
            "location": [str(item.get("url") or "").strip() for item in location_refs if str(item.get("url") or "").strip()],
            "style": [str(item.get("url") or "").strip() for item in style_refs if str(item.get("url") or "").strip()],
        },
    }


def _build_entity_locks(payload: dict[str, Any], reference_profiles: dict[str, Any]) -> dict[str, Any]:
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    entity_locks: dict[str, Any] = {}
    for role in COMFY_REF_ROLES:
        profile = reference_profiles.get(role) if isinstance(reference_profiles.get(role), dict) else None
        refs = refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []
        if not profile and not refs:
            continue
        visual_profile = profile.get("visualProfile") if isinstance((profile or {}).get("visualProfile"), dict) else {}
        raw_entity_type = str((profile or {}).get("entityType") or role).strip() or role
        normalized_entity_type = normalize_entity_type(raw_entity_type or role)
        canonical_details: dict[str, Any] = {}
        if normalized_entity_type == "human":
            canonical_details = {
                "gender_presentation": _summarize_profile_value(visual_profile.get("genderPresentation")) or "locked_from_reference",
                "body_type": _summarize_profile_value(visual_profile.get("bodyType")) or "locked_from_reference",
                "hair": _summarize_profile_value(visual_profile.get("hair")) or "locked_from_reference",
                "outfit": {
                    "top": _summarize_profile_value(visual_profile.get("outfitTop") or visual_profile.get("outfit")) or "locked_from_reference",
                    "bottom": _summarize_profile_value(visual_profile.get("outfitBottom") or visual_profile.get("outfit")) or "locked_from_reference",
                    "shoes": _summarize_profile_value(visual_profile.get("shoes") or visual_profile.get("footwear")) or "locked_from_reference",
                },
                "silhouette": _summarize_profile_value(visual_profile.get("silhouette") or visual_profile.get("bodyType")) or "locked_from_reference",
                "accessories": _summarize_profile_value(visual_profile.get("accessories")) or "locked_from_reference",
            }
        elif normalized_entity_type == "animal":
            canonical_details = {
                "species": _summarize_profile_value(visual_profile.get("species") or visual_profile.get("speciesLock")) or "locked_from_reference",
                "breed_type": _summarize_profile_value(visual_profile.get("breedLikeAppearance")) or "locked_from_reference",
                "fur_pattern": _summarize_profile_value(visual_profile.get("furPattern") or visual_profile.get("coat")) or "locked_from_reference",
                "color": _summarize_profile_value(visual_profile.get("dominantColors") or visual_profile.get("coat")) or "locked_from_reference",
                "proportions": _summarize_profile_value(visual_profile.get("bodyType") or visual_profile.get("morphology") or visual_profile.get("bodyBuild")) or "locked_from_reference",
            }
        elif normalized_entity_type == "object":
            canonical_details = {
                "object_type": _summarize_profile_value(visual_profile.get("objectCategory")) or "locked_from_reference",
                "shape": _summarize_profile_value(visual_profile.get("silhouette")) or "locked_from_reference",
                "material": _summarize_profile_value(visual_profile.get("material")) or "locked_from_reference",
                "color": _summarize_profile_value(visual_profile.get("dominantColors")) or "locked_from_reference",
                "scale_class": _summarize_profile_value(visual_profile.get("scaleClass")) or "locked_from_reference",
            }
        forbidden_changes = (profile or {}).get("forbiddenChanges") if isinstance((profile or {}).get("forbiddenChanges"), list) else []
        forbidden_changes = _append_unique_strings(
            [str(item).strip() for item in forbidden_changes if str(item).strip()],
            [
                "do not change outfit",
                "do not change hair",
                "do not change body type",
                "do not replace object",
                "do not change material",
            ],
        )
        entity_locks[role] = {
            "refId": role,
            "role": role,
            "label": str(((refs[0] or {}).get("name")) if refs else "").strip() or role,
            "entityType": normalized_entity_type,
            "rawEntityType": raw_entity_type,
            "normalizedEntityType": normalized_entity_type,
            "visualProfile": visual_profile,
            "canonicalDetails": canonical_details,
            "invariants": (profile or {}).get("invariants") if isinstance((profile or {}).get("invariants"), list) else [],
            "forbiddenChanges": forbidden_changes or [
                "Do not swap identity with a different entity.",
                "Do not mutate outfit/material/species without explicit story cause.",
            ],
            "forbidden_changes": forbidden_changes or [
                "do not change outfit",
                "do not change hair",
                "do not change body type",
                "do not replace object",
                "do not change material",
            ],
            "sourceRefUrls": [str(item.get("url") or "").strip() for item in refs if str(item.get("url") or "").strip()],
        }
    return entity_locks


def _build_gemini_planner_payload(payload: dict[str, Any], world_lock: dict[str, Any], entity_locks: dict[str, Any]) -> dict[str, Any]:
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    story_context = _derive_gemini_only_story_context(payload)
    return {
        "plannerMode": "gemini_only",
        "mode": payload.get("mode") or "clip",
        "genre": payload.get("genre") or "",
        "audio": {
            "audioUrl": payload.get("audioUrl") or "",
            "durationSec": payload.get("audioDurationSec"),
            "audioIsPrimaryMeaningSource": story_context.get("storySource") == "audio",
            "audioStoryMode": payload.get("audioStoryMode") or "lyrics_music",
        },
        "textContext": {
            "scriptText": payload.get("text") or "",
            "lyricsText": payload.get("lyricsText") or "",
            "transcriptText": payload.get("transcriptText") or "",
            "spokenTextHint": payload.get("spokenTextHint") or "",
            "audioSemanticSummary": payload.get("audioSemanticSummary") or "",
            "audioSemanticHints": payload.get("audioSemanticHints") or "",
            "textRole": "fallback_only" if payload.get("audioStoryMode") == "speech_narrative" and story_context.get("storySource") == "audio" else ("primary" if story_context.get("storySource") == "text" else "support"),
        },
        "worldContext": {
            "worldLock": world_lock,
            "styleSummary": payload.get("stylePreset") or "realism",
            "sceneCandidates": payload.get("sceneCandidates") or [],
            "locationRefs": refs_by_role.get("location") or [],
            "characterRefs": [item for role in ["character_1", "character_2", "character_3"] for item in (refs_by_role.get(role) or [])],
            "animalRefs": refs_by_role.get("animal") or [],
            "propRefs": refs_by_role.get("props") or [],
            "styleRefs": refs_by_role.get("style") or [],
            "entityLocks": entity_locks,
            "globalRules": world_lock.get("continuityRules") or [],
        },
        "planningRules": {
            "sceneDurationMinSec": 3,
            "sceneDurationMaxSec": 8,
            "oneCompleteThoughtPerScene": True,
            "refsConstrainVisualsNotMeaning": True,
            "allowEnvironmentalScenesWithoutCharacters": True,
            "chooseActiveRefsPerScene": True,
            "avoidGenericEpicFiller": True,
            "avoidSlideshowOnlyCameraMoves": True,
            "requireSceneActionOrEnvironmentMotion": True,
            "directorLayer": {
                "sequenceMindset": "connected_cinematic_sequence",
                "cameraContinuity": "prefer continuation over cut; camera persists across scenes unless a justified cut occurs",
                "cameraIdentity": "camera is a physical entity and cannot teleport or randomly change form",
                "allowedTransitionTypes": sorted(GEMINI_ONLY_TRANSITION_TYPES),
                "defaultTransitionType": "continuation",
                "scaleTransitionRule": "sky_ground_underground transitions allowed only through continuous physical camera movement",
                "forbiddenScaleTransitions": [
                    "cross-section",
                    "earth layers",
                    "schematic depth explanation",
                    "diagrammatic cutaway",
                    "abstract globe/map transition",
                ],
                "humanAnchorCoverageTarget": 0.3,
                "humanAnchorEarlyBias": True,
                "defaultVisualMode": GEMINI_ONLY_VISUAL_MODE_DEFAULT,
                "imageVideoPromptAlignment": "imagePrompt is a frame from current shot; videoPrompt continues motion from that frame",
            },
        },
        "storyContext": story_context,
    }


def _build_gemini_only_planner_prompt(gemini_payload: dict[str, Any]) -> str:
    story_context = gemini_payload.get("storyContext") if isinstance(gemini_payload.get("storyContext"), dict) else {}
    story_source = str(story_context.get("storySource") or "audio").strip() or "audio"
    timeline_source = str(story_context.get("timelineSource") or "logic").strip() or "logic"
    story_mission_summary = str(story_context.get("storyMissionSummary") or "").strip()
    genre = str(gemini_payload.get("genre") or story_context.get("genre") or "").strip()
    weak_semantic_context = bool(story_context.get("weakSemanticContext"))
    semantic_context_reason = str(story_context.get("semanticContextReason") or "").strip()
    return (
        "You are COMFY Gemini Brain planner. Return strict JSON only.\n"
        "Top-level JSON fields required: worldLock, entityLocks, scenes, preview, warnings, debug.\n"
        "Scene contract required for every scene:\n"
        "sceneId,startSec,endSec,durationSec,spokenText,sceneMeaning,emotion,imagePrompt,videoPrompt,sceneAction,environmentMotion,cameraPlan,sfxSuggestion,activeRefs,refUsageReason,characterRoleLogic,continuity,continuityLocksUsed,confidence,focalSubject,visualClue,cameraIntent,forbiddenInsertions,transitionType,cameraType,cameraMovement,cameraPosition,visualMode,humanAnchorType.\n"
        "Rules:\n"
        "- plannerMode is gemini_only, so you own semantic scene planning completely.\n"
        f"- Primary story source for this request is {story_source}.\n"
        "- If storySource=audio, audio meaning is primary and refs only constrain the world/continuity.\n"
        "- If storySource=text, text semantics are primary and refs only constrain the world/continuity.\n"
        "- If refs exist, use them as optional continuity anchors, not as mandatory narrative source.\n"
        "- First lock the world, then lock entities, then plan scenes, then select preview from the resulting scenes.\n"
        "- Keep one stable world unless the story explicitly transitions.\n"
        "- Choose active refs per scene; do not force every ref into every scene.\n"
        "- Scene duration target is 3-8 sec, usually 3-7 sec.\n"
        "- For speech_narrative, scenes over 8.0 sec are invalid unless immediately split into smaller sub-scenes with natural speech-safe boundaries.\n"
        "- Each strong scene must contain at least two of three: character/entity action, environment motion, camera movement.\n"
        "- Avoid slideshow risk where only the camera moves.\n"
        "- This is not a list of isolated shots. This is a connected cinematic sequence.\n"
        "- Every scene must follow the previous scene logically, preserve momentum, preserve world continuity, preserve camera logic, and feel editable into one coherent montage.\n"
        "- The camera must persist across scenes unless a justified cut occurs.\n"
        "- Prefer continuation over cut. Cuts should be rare and meaningful.\n"
        "- Treat the camera as a physical entity inside the world. It cannot teleport. It cannot instantly change form without reason.\n"
        "- Allowed scene-to-scene transitions: continuation, enter_transition, justified_cut, match_cut, perspective_shift. First scene must use start.\n"
        "- Forbidden camera behavior: random drone -> handheld -> dolly switching without narrative motivation, unexplained camera-type changes, sudden abstract observer perspective.\n"
        "- Scale transitions between sky / ground / underground are allowed only through continuous physical camera movement or a logical shot chain: aerial descent, zoom into terrain, dive toward a specific location, approach a real entrance, enter a physical opening.\n"
        "- Forbidden scale transitions: cross-section, earth layers, schematic depth explanation, diagrammatic cutaway, abstract globe/map transition.\n"
        f"- visualMode defaults to {GEMINI_ONLY_VISUAL_MODE_DEFAULT} unless the request explicitly demands another grounded mode.\n"
        "- At least 30% of scenes should include a human anchor through one of: character, POV, human_trace. If the sequence runs multiple scenes, introduce that anchor early instead of only at the end.\n"
        "- If character refs exist, use them softly as observer / explorer / witness rather than forcing them into every scene.\n"
        "- If character refs do not exist, human anchor may be footprints, shadow, hands, flashlight beam, breathing POV, equipment, or other subtle human trace.\n"
        "- imagePrompt and videoPrompt must stay compatible with the locked world and entity continuity and must describe the same shot family.\n"
        "- imagePrompt should read like a frame from the current camera position; videoPrompt should continue motion from that same frame instead of jumping to a different shot.\n"
        "- Write each scene as a narrative beat, not a wallpaper description: name the focal subject, the exact current event/action, the visual clue that carries narration meaning, and what the camera is meant to capture right now.\n"
        "- Avoid generic establishing-shot filler unless the scene is explicitly an establishing scene.\n"
        "- Do not invent dominant unexplained foreground props or oversized machines/devices/artifacts unless narration or refs explicitly require them. If no prop is required, keep the frame clean and semantically grounded.\n"
        "- preview must be extracted from the scenario, not invented separately.\n"
        "WORLD CONTINUITY:\n"
        "- environment must remain consistent across scenes.\n"
        "- lighting must remain physically consistent.\n"
        "- no random environment changes.\n"
        "ENTITY LOCK:\n"
        "- characters must keep same appearance.\n"
        "- objects must not change shape/material.\n"
        "- animals must keep species and pattern.\n"
        "SCENE QUALITY:\n"
        "- each scene must include action OR environment motion.\n"
        "- the focal visual clue must come from narration meaning, not random object invention.\n"
        "- avoid static shots.\n"
        "- avoid slideshow camera-only scenes.\n"
        "- transitionType should default to continuation whenever possible.\n"
        "- cameraType, cameraMovement, and cameraPosition should preserve physical continuity from scene to scene.\n"
        "- justified_cut and perspective_shift require explicit narrative reason inside continuity or refUsageReason.\n"
        "REF LOGIC:\n"
        "- refs constrain visuals, not narrative meaning.\n"
        "- choose refs per scene, not globally.\n"
        "PREVIEW:\n"
        "- one scene must be strong enough to be preview.\n"
        "- it must be understandable visually.\n"
        f"- timelineSource={timeline_source}.\n"
        f"- storyMissionSummary={story_mission_summary or 'not_provided'}.\n"
        f"- selectedGenre={genre or 'not_provided'}.\n"
        f"- weakSemanticContext={json.dumps({'value': weak_semantic_context, 'reason': semantic_context_reason}, ensure_ascii=False)}.\n"
        "DEBUG:\n"
        "- In debug include cameraContinuityScore, transitionTypesByScene, humanAnchorCoverage, scenesWithHumanAnchor, visualModesByScene, cameraTypesByScene, continuationChainCount, and randomCutRisk when possible.\n"
        "Return no markdown, only JSON.\n"
        f"INPUT={json.dumps(gemini_payload, ensure_ascii=False)}"
    )


def _normalize_scene_timeline(scenes: list[dict[str, Any]], audio_duration_sec: float | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    safe_audio_duration = _to_float(audio_duration_sec)
    if safe_audio_duration is None or safe_audio_duration <= 0:
        total_sum = sum(max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in scenes)
        timeline_end = max((_to_float(scene.get("endSec")) or 0.0) for scene in scenes) if scenes else 0.0
        return scenes, {
            "audioDurationSec": None,
            "timelineDurationSec": _round_sec(timeline_end),
            "sceneDurationTotalSec": _round_sec(total_sum),
            "sceneCount": len(scenes),
            "normalizationApplied": False,
            "normalizationReason": None,
            "timelineScale": 1.0,
        }

    original_end = max((_to_float(scene.get("endSec")) or 0.0) for scene in scenes) if scenes else 0.0
    original_sum = sum(max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in scenes)
    needs_fix = original_end > (safe_audio_duration + 0.25)
    scale = 1.0
    reason = None
    if needs_fix and original_end > 0:
        scale = safe_audio_duration / original_end
        reason = f"timeline_scaled_to_audio:{_round_sec(original_end)}->{_round_sec(safe_audio_duration)}"

    normalized: list[dict[str, Any]] = []
    cursor = 0.0
    for idx, scene in enumerate(scenes):
        start = _to_float(scene.get("startSec"))
        end = _to_float(scene.get("endSec"))
        duration = _to_float(scene.get("durationSec"))

        if start is not None and end is not None and end >= start:
            next_start = start * scale if needs_fix else start
            next_end = end * scale if needs_fix else end
        else:
            guessed_duration = duration if duration is not None and duration > 0 else 0.0
            if guessed_duration <= 0 and safe_audio_duration > 0:
                guessed_duration = safe_audio_duration / max(1, len(scenes))
            next_start = cursor
            next_end = cursor + guessed_duration

        next_start = max(0.0, min(safe_audio_duration, next_start))
        next_end = max(next_start, min(safe_audio_duration, next_end))

        if idx == len(scenes) - 1:
            next_end = safe_audio_duration
            next_start = min(next_start, next_end)

        cursor = next_end
        normalized.append(
            {
                **scene,
                "startSec": _round_sec(next_start),
                "endSec": _round_sec(next_end),
                "durationSec": _round_sec(max(0.0, next_end - next_start)),
            }
        )

    normalized_end = max((_to_float(scene.get("endSec")) or 0.0) for scene in normalized) if normalized else 0.0
    normalized_sum = sum(max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in normalized)
    return normalized, {
        "audioDurationSec": _round_sec(safe_audio_duration),
        "timelineDurationSec": _round_sec(normalized_end),
        "sceneDurationTotalSec": _round_sec(normalized_sum),
        "sceneCount": len(normalized),
        "normalizationApplied": bool(reason),
        "normalizationReason": reason,
        "timelineScale": _round_sec(scale),
        "originalTimelineDurationSec": _round_sec(original_end),
        "originalSceneDurationTotalSec": _round_sec(original_sum),
    }


def _split_speech_text_chunks(text: str, pieces: int) -> list[str]:
    clean = re.sub(r"\s+", " ", str(text or "").strip())
    if pieces <= 1 or not clean:
        return [clean] if clean else []

    sentences = [chunk.strip(" -—–") for chunk in re.split(r"(?<=[.!?…])\s+|\s*[;:]\s*", clean) if chunk.strip(" -—–")]
    source_parts = sentences if len(sentences) >= pieces else [clean]
    if len(source_parts) >= pieces:
        per_chunk = max(1, len(source_parts) // pieces)
        chunks: list[str] = []
        cursor = 0
        for idx in range(pieces):
            remaining_parts = len(source_parts) - cursor
            remaining_slots = pieces - idx
            take = max(1, round(remaining_parts / remaining_slots))
            chunk = " ".join(source_parts[cursor:cursor + take]).strip()
            if chunk:
                chunks.append(chunk)
            cursor += take
        return chunks[:pieces]

    words = clean.split(" ")
    approx = max(4, round(len(words) / pieces))
    chunks = []
    for idx in range(pieces):
        start = idx * approx
        end = len(words) if idx == pieces - 1 else min(len(words), (idx + 1) * approx)
        chunk = " ".join(words[start:end]).strip()
        if chunk:
            chunks.append(chunk)
    return chunks[:pieces] or [clean]


def _build_speech_split_candidates(start_sec: float, end_sec: float, analysis: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    window = max(0.0, end_sec - start_sec)
    margin = min(0.75, max(0.35, window * 0.08))

    def add_candidate(raw: Any, reason: str, priority: int) -> None:
        point = _to_float(raw)
        if point is None:
            return
        if point <= (start_sec + margin) or point >= (end_sec - margin):
            return
        candidates.append({"time": _round_sec(point), "reason": reason, "priority": priority})

    for phrase in analysis.get("vocalPhrases") or []:
        if not isinstance(phrase, dict):
            continue
        add_candidate(phrase.get("end"), "sentence_endings", 0)
    for pause in analysis.get("pausePoints") or []:
        add_candidate(pause, "spoken_pauses", 1)
    for boundary in analysis.get("phraseBoundaries") or []:
        add_candidate(boundary, "semantic_breakpoints", 2)

    deduped: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda item: (item["time"], item["priority"])):
        if deduped and abs(float(candidate["time"]) - float(deduped[-1]["time"])) < 0.45:
            if int(candidate["priority"]) < int(deduped[-1]["priority"]):
                deduped[-1] = candidate
            continue
        deduped.append(candidate)
    return deduped


def _pick_speech_split_points(start_sec: float, end_sec: float, analysis: dict[str, Any], pieces: int) -> tuple[list[float], list[str]]:
    duration = max(0.0, end_sec - start_sec)
    if pieces <= 1 or duration <= 8.0:
        return [], []

    candidates = _build_speech_split_candidates(start_sec, end_sec, analysis)
    targets = [start_sec + (duration * idx / pieces) for idx in range(1, pieces)]
    chosen: list[dict[str, Any]] = []
    min_gap = max(2.0, min(6.5, duration / (pieces + 0.2) * 0.68))

    for target in targets:
        best = None
        for candidate in candidates:
            point = float(candidate["time"])
            if any(abs(point - float(existing["time"])) < min_gap for existing in chosen):
                continue
            if point <= start_sec or point >= end_sec:
                continue
            score = (int(candidate["priority"]), abs(point - target), point)
            if best is None or score < best[0]:
                best = (score, candidate)
        if best is not None:
            chosen.append(best[1])

    fallback_counter = 0
    while len(chosen) < pieces - 1:
        fallback_counter += 1
        midpoint = start_sec + (duration * len(chosen) / pieces) + (duration / pieces / 2.0)
        midpoint = max(start_sec + 0.8, min(end_sec - 0.8, midpoint))
        if any(abs(midpoint - float(existing["time"])) < 1.2 for existing in chosen):
            midpoint += 0.6 * fallback_counter
            midpoint = max(start_sec + 0.8, min(end_sec - 0.8, midpoint))
        chosen.append({"time": _round_sec(midpoint), "reason": "approximate_midpoint", "priority": 9})

    chosen = sorted(chosen[:pieces - 1], key=lambda item: float(item["time"]))
    return [float(item["time"]) for item in chosen], [str(item["reason"]) for item in chosen]


def _split_oversized_speech_scenes(
    scenes: list[dict[str, Any]],
    normalized: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    audio_story_mode = str(normalized.get("audioStoryMode") or "").strip().lower()
    debug = {
        "oversizedSpeechScenesDetected": 0,
        "oversizedSpeechScenesSplitCount": 0,
        "speechSplitReasons": [],
    }
    if audio_story_mode != "speech_narrative" or not scenes:
        return scenes, debug, []

    analysis, analysis_debug = _load_audio_analysis(str(normalized.get("audioUrl") or ""), _to_float(normalized.get("audioDurationSec")))
    split_scenes: list[dict[str, Any]] = []
    warnings: list[str] = []

    for scene in scenes:
        duration = max(0.0, _to_float(scene.get("durationSec")) or 0.0)
        start_sec = _to_float(scene.get("startSec")) or 0.0
        end_sec = _to_float(scene.get("endSec")) or start_sec
        if duration <= 8.0 or end_sec <= start_sec:
            split_scenes.append(scene)
            continue

        debug["oversizedSpeechScenesDetected"] += 1
        pieces = max(2, int(duration // 7.2) + (1 if (duration % 7.2) > 0.2 else 0))
        pieces = min(pieces, max(2, int(duration // 2.6)))
        split_points, split_reasons = _pick_speech_split_points(start_sec, end_sec, analysis, pieces)
        boundaries = [start_sec, *split_points, end_sec]
        if len(boundaries) < 3:
            warnings.append(f"speech_scene_split_failed:{scene.get('sceneId')}")
            split_scenes.append(scene)
            continue

        text_chunks = _split_speech_text_chunks(str(scene.get("spokenText") or scene.get("sceneText") or ""), len(boundaries) - 1)
        meaning_chunks = _split_speech_text_chunks(str(scene.get("sceneMeaning") or ""), len(boundaries) - 1)
        for idx in range(len(boundaries) - 1):
            part_start = boundaries[idx]
            part_end = boundaries[idx + 1]
            part_duration = max(0.0, part_end - part_start)
            if part_duration < 1.2:
                continue
            part_scene = dict(scene)
            part_scene["sceneId"] = f"{scene.get('sceneId') or 'scene'}-s{idx + 1}"
            part_scene["title"] = str(scene.get("title") or f"Scene {scene.get('sceneId') or ''}").strip()
            part_scene["startSec"] = _round_sec(part_start)
            part_scene["endSec"] = _round_sec(part_end)
            part_scene["durationSec"] = _round_sec(part_duration)
            if idx < len(text_chunks):
                part_scene["spokenText"] = text_chunks[idx]
                if not str(part_scene.get("sceneText") or "").strip():
                    part_scene["sceneText"] = text_chunks[idx]
            if idx < len(meaning_chunks) and meaning_chunks[idx]:
                part_scene["sceneMeaning"] = meaning_chunks[idx]
            part_scene["continuity"] = "; ".join(
                item for item in [
                    str(scene.get("continuity") or "").strip(),
                    f"speech beat {idx + 1}/{len(boundaries) - 1}",
                ]
                if item
            )
            split_scenes.append(part_scene)
        debug["oversizedSpeechScenesSplitCount"] += max(0, len(boundaries) - 2)
        debug["speechSplitReasons"].append({
            "sceneId": str(scene.get("sceneId") or ""),
            "durationSec": _round_sec(duration),
            "analysisSource": analysis_debug.get("source") or "none",
            "splitReasons": split_reasons or ["approximate_midpoint"],
            "resultSceneCount": len(boundaries) - 1,
        })

    return split_scenes, debug, warnings


def build_comfy_planner_prompt(payload: dict[str, Any]) -> str:
    audio_story_mode = str(payload.get("audioStoryMode") or "lyrics_music").strip().lower()
    if audio_story_mode not in {"lyrics_music", "music_only", "music_plus_text", "speech_narrative"}:
        audio_story_mode = "lyrics_music"

    # DEBUG VALIDATION CHECKLIST (manual):
    # 1) lyrics_music -> same song with lyrics should produce story beats that follow lyrical meaning.
    # 2) music_only -> same song should avoid lyric-derived plot; beats follow rhythm/energy only.
    # 3) music_plus_text -> same song + separate TEXT storyline should follow TEXT storyline; audio drives pace/energy.
    # 4) speech_narrative -> spoken meaning should drive scene-by-scene documentary/story planning.
    audio_story_rules = (
        "AUDIO STORY MODE RULES (STRICT):\n"
        "- lyrics_music: lyrics semantics are explicitly allowed and should be used as a narrative driver when vocals exist. You may use lyrical meaning, verse/chorus structure, emotional lyrical phrases, and explicit lyrical motifs to shape scene goals and transitions. Build scenes from lyrics+music together, not from music alone.\n"
        "- music_only: ignore lyrical semantics completely. Do not derive plot, events, world, objects, characters, or story beats from sung words. Do not build storyline from vocal text and do not substitute musical analysis with lyric interpretation. Use only rhythm, tempo, energy, dynamics, pacing, and emotional contour. If vocals exist, treat vocals as musical texture/emotional signal, never as narrative source.\n"
        "- music_plus_text: lyrics semantics must be ignored completely. TEXT node is the narrative driver for plot/events/world/objects/characters/story beats. AUDIO controls pacing, scene timing, montage rhythm, energy and emotional modulation. If lyrics conflict with TEXT, ignore lyrics semantics and follow TEXT. If TEXT is empty, fall back to a neutral music-driven storyboard without lyrics meaning.\n"
        "- speech_narrative: spoken meaning is the primary narrative driver. transcriptText, spokenTextHint, and audioSemanticSummary must drive scene planning scene-by-scene. Audio is semantic content, not only rhythm/emotion. TEXT node only supplements and clarifies the spoken meaning. If TEXT conflicts with the spoken meaning, the spoken meaning wins. Do not drift into generic cinematic mood unrelated to the speech content. If the speech topic is military, bunker, underground base, infrastructure, archival, documentary, or surveillance, stay inside that topic. Never invent romance, sunset, lifestyle, fashion, or music-video scenes unless the speech explicitly requires them.\n"
        "- speech_narrative hard rule: for every scene first build a human-readable scene visual brief containing sceneText, sceneMeaning, visualDescription, cameraPlan, motionPlan, and sfxPlan before writing prompts. imagePromptRu must directly depict the spoken meaning; videoPromptRu must describe meaningful motion in that exact scene. Abstract style-only prompts are forbidden unless the spoken segment itself is abstract. If the speech mentions desert, bunker, tunnel, blast door, missile, satellite, map, entrance, or underground facility, those objects/environments must appear in visualDescription, imagePromptRu, and videoPromptRu.\n"
        "- Non-compliance is an error: for music_only and music_plus_text never claim lyric semantics drove the story; for speech_narrative never ignore explicit spoken meaning."
    )
    segmentation_rules = (
        "SCENE SEGMENTATION RULES (HIGHEST PRIORITY):\n"
        "- Scene boundaries must follow meaningful phrase endings and real transition points.\n"
        "- Prefer cuts at: (1) vocal/semantic phrase ending, (2) musical phrase ending, (3) clear energy/rhythm/arrangement transition, (4) end of a visual micro-action, (5) emotional intention change.\n"
        "- Never split into equal-sized time blocks (forbidden: mechanical 5s, 10s, or evenly spaced chunks).\n"
        "- Duration is only a guardrail, not the main segmentation driver.\n"
        "- Guardrails: avoid <2.0s unless there is a strong accent cut; avoid >8.0s unless one continuous meaningful phrase/action justifies it.\n"
        "- If a scene exceeds 8.0s, include an explicit justification in sceneNarrativeStep or continuity.\n"
        "- Boundaries should feel cinematic and natural, not grid-based.\n"
    )
    audio_mode_segmentation = (
        "AUDIO MODE SEGMENTATION FOCUS:\n"
        "- lyrics_music: boundaries can follow lyric/sentence/sung-line endings plus music transitions.\n"
        "- music_only: ignore lyrics meaning; boundaries follow musical phrasing, energy shifts, rhythmic transitions, and structure only.\n"
        "- music_plus_text: ignore lyrics meaning; boundaries follow musical phrasing + meaningful TEXT chunks, synced to transition points.\n"
        "- speech_narrative: boundaries must follow spoken pauses, sentence endings, topic shifts, and meaningful semantic beats. Do not segment by equal chunks. Do not use music rhythm unless spoken structure is absent.\n"
    )

    return (
        "You are COMFY storyboard planner. Return strict JSON only.\n"
        "Fields: ok, planMeta, globalContinuity, scenes, warnings, errors, debug.\n"
        f"Selected audioStoryMode={audio_story_mode}.\n"
        f"{audio_story_rules}\n"
        f"{segmentation_rules}\n"
        f"{audio_mode_segmentation}\n"
        "AUDIO is primary source for rhythm, emotional contour, dramatic shifts and timing.\n"
        "If INPUT.audioDurationSec is provided and > 0, scene timeline MUST stay inside [0, audioDurationSec].\n"
        "TEXT is optional support that clarifies intent.\n"
        "REFS are optional anchors for character/location/style/props continuity.\n"
        "REFERENCE CAST CONTRACT (STRICT):\n"
        "- All reference inputs are globally visible to you.\n"
        "- Treat references as cast members and world anchors.\n"
        "- For every scene explicitly decide which roles appear and which roles do not appear.\n"
        "- If a role has reference images, do not reinterpret that entity freely.\n"
        "- Human references must preserve identity, hair, face, outfit and body signature.\n"
        "- Never substitute selected human refs with a generic human.\n"
        "- Animal references must preserve species, breed-like appearance, coat color/pattern and body type.\n"
        "- Never substitute selected animal refs with different species/breed/coat identity.\n"
        "- Object references must preserve object category, silhouette, material, dominant colors and distinctive parts.\n"
        "- Never substitute selected props refs with another object type, geometry, or material family.\n"
        "- A scene may use one actor, multiple actors, only props, only environment, or any justified subset.\n"
        "- Never include unselected actors in a scene.\n"
        "- If a role is not selected for the scene, do not bring it into frame.\n"
        "- Never replace a selected actor with a generic invented version.\n"
        "- HARD NO-CHARACTERS RULE: if there are no character refs and the transcript/text does not explicitly require people, charactersAllowed=false and you must not invent humans, women, men, crowds, portraits, or lifestyle extras. Use environment-only, infrastructure-only, archive-only, map-only, machinery-only, or object-only visuals instead.\n"
        "- Style references define visual language only and cannot cancel identity contracts.\n"
        "- Location references define world/environment identity anchors for the scene.\n"
        "- If a role is chosen as hero, that role must dominate shot semantics.\n"
        "Each scene must include: sceneId,title,startSec,endSec,durationSec,sceneNarrativeStep,sceneGoal,storyMission,"
        "sceneOutputRule,primaryRole,secondaryRoles,continuity,imagePromptRu,imagePromptEn,videoPromptRu,videoPromptEn,refsUsed,refDirectives,sceneSemanticSource,focalSubject,sceneAction,visualClue,cameraIntent,environmentMotion,forbiddenInsertions,"
        "heroEntityId,supportEntityIds,mustAppear,mustNotAppear,environmentLock,styleLock,identityLock,roleSelectionReason.\n"
        "For speech_narrative scenes also include: sceneText,sceneMeaning,visualDescription,cameraPlan,motionPlan,sfxPlan.\n"
        "LANGUAGE CONTRACT (MANDATORY): imagePromptRu MUST be Russian; imagePromptEn MUST be English; videoPromptRu MUST be Russian; videoPromptEn MUST be English. Non-compliance is an error.\n"
        "Treat every scene as a narrative beat, not a generic landscape description. Specify the focal subject, the exact action/event happening now, the visual clue that carries narration meaning, and the camera intent.\n"
        "Avoid generic establishing-shot filler unless the scene is explicitly an establishing scene.\n"
        "Do not invent dominant unexplained foreground props. Do not introduce oversized machines, devices, or artifacts unless the narration meaning or explicit refs require them. If no prop is required, keep the frame clean and semantically grounded.\n"
        "Do NOT include runtime render-state fields in planner output (for example imageUrl, videoUrl, audioSliceUrl).\n"
        "Scenes should feel cinematic and watchable; avoid dry static actions unless story requires it.\n"
        "In debug include segmentationMode and segmentationReason briefly explaining why boundaries were selected, plus audioStoryMode, textSource, transcriptAvailable, spokenMeaningPrimary, charactersAllowed, sceneSemanticSource per scene, peopleAutoAddedCount, oversizedSpeechScenesDetected, oversizedSpeechScenesSplitCount, speechSplitReasons, promptLanguageStatus, ruPromptMissing, enPromptPresent, and objectHallucinationRisk when available.\n"
        f"INPUT={json.dumps(payload, ensure_ascii=False)}"
    )


def build_comfy_planner_refinement_prompt(payload: dict[str, Any], previous_scenes: list[dict[str, Any]], refinement_reason: str) -> str:
    base_prompt = build_comfy_planner_prompt(payload)
    compact_scene_map = [
        {
            "sceneId": str(scene.get("sceneId") or ""),
            "startSec": _round_sec(_to_float(scene.get("startSec"))),
            "endSec": _round_sec(_to_float(scene.get("endSec"))),
            "durationSec": _round_sec(_to_float(scene.get("durationSec"))),
            "title": str(scene.get("title") or ""),
            "sceneNarrativeStep": str(scene.get("sceneNarrativeStep") or ""),
        }
        for scene in previous_scenes
    ]
    refinement_rules = (
        "SECOND PASS REFINEMENT (SEGMENTATION ONLY):\n"
        "- Your previous segmentation was too coarse/mechanical and needs refinement.\n"
        "- Keep the same story direction and continuity. This is NOT a totally new story.\n"
        "- Refine scene boundaries around meaningful phrase endings and real transition points.\n"
        "- If a scene is too long, split it into smaller phrase-complete scenes.\n"
        "- Avoid large generic blocks and avoid equal-duration chunking.\n"
        "- Prefer shorter meaningful scenes over broad time chunks when in doubt.\n"
        "- Keep boundaries natural, cinematic, and motivated by transitions.\n"
        "- Respect audioDurationSec and do not exceed the total audio timeline.\n"
        "- Preserve narrative continuity while improving segmentation granularity.\n"
        "- Keep audioStoryMode logic strict:\n"
        "  * lyrics_music: lyric phrase endings + music transitions.\n"
        "  * music_only: ignore lyrics semantics, use musical phrasing/energy/structure transitions only.\n"
        "  * music_plus_text: ignore lyrics semantics, follow TEXT chunks + music transitions.\n"
        "  * speech_narrative: spoken pauses, sentence endings, topic shifts, and semantic beats. Never equal chunks.\n"
    )
    return (
        f"{base_prompt}\n\n"
        f"Refinement trigger reason: {refinement_reason}.\n"
        f"Previous coarse segmentation snapshot={json.dumps(compact_scene_map, ensure_ascii=False)}\n"
        f"{refinement_rules}"
    )


def _extract_text(resp: dict[str, Any]) -> str:
    try:
        parts = (((resp or {}).get("candidates") or [])[0].get("content") or {}).get("parts") or []
        text_parts = [str(p.get("text") or "") for p in parts if isinstance(p, dict)]
        return "\n".join([x for x in text_parts if x])
    except Exception:
        return ""


def _extract_json(raw: str) -> dict[str, Any] | None:
    s = str(raw or "").strip()
    if not s:
        return None
    if s.startswith("```"):
        s = s.strip("`")
        if s.startswith("json"):
            s = s[4:]
    try:
        parsed = json.loads(s)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass
    start = s.find("{")
    end = s.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(s[start:end + 1])
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _call_gemini_plan(api_key: str, model: str, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    logger.info("[COMFY PLAN] gemini request start model=%s", model)
    resp = post_generate_content(api_key, model, body, timeout=120)
    raw = _extract_text(resp if isinstance(resp, dict) else {})
    raw_preview = raw[:3000] if raw else str((resp or {}).get("text") or "")[:3000]
    http_status = int(resp.get("status") or 0) if isinstance(resp, dict) and resp.get("__http_error__") else None
    diagnostics = {
        "requestedModel": model,
        "effectiveModel": model,
        "httpStatus": http_status,
        "rawPreview": raw_preview,
        "errorText": str((resp or {}).get("text") or "")[:3000] if isinstance(resp, dict) else "",
        "fallbackFrom": None,
        "fallbackTo": None,
        "sanitizedError": "",
    }
    if isinstance(resp, dict) and resp.get("__http_error__"):
        error_code, sanitized_error = _sanitize_gemini_error(diagnostics, resp)
        diagnostics["sanitizedError"] = sanitized_error
        logger.warning("[COMFY PLAN] gemini http error model=%s status=%s code=%s", model, resp.get("status"), error_code)
        return {"errors": [error_code], "debug": {"httpStatus": http_status, "sanitizedError": sanitized_error}}, diagnostics

    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        diagnostics["sanitizedError"] = _humanize_storyboard_error("gemini_invalid_json")
        logger.warning("[COMFY PLAN] gemini invalid json model=%s", model)
        return {"errors": ["gemini_invalid_json"]}, diagnostics

    return parsed, diagnostics


def _call_gemini_plan_with_model_fallback(api_key: str, requested_model: str, body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    model_candidates = _build_gemini_only_model_candidates(requested_model)
    last_parsed: dict[str, Any] = {"errors": ["gemini_http_error"]}
    last_diagnostics: dict[str, Any] = {
        "requestedModel": requested_model,
        "effectiveModel": requested_model,
        "httpStatus": None,
        "rawPreview": "",
        "errorText": "",
        "fallbackFrom": None,
        "fallbackTo": None,
        "modelCandidates": model_candidates,
        "sanitizedError": "",
    }

    for idx, candidate_model in enumerate(model_candidates):
        parsed, diagnostics = _call_gemini_plan(api_key, candidate_model, body)
        diagnostics["requestedModel"] = requested_model
        diagnostics["modelCandidates"] = model_candidates
        if idx > 0:
            diagnostics["fallbackFrom"] = model_candidates[idx - 1]
            diagnostics["fallbackTo"] = candidate_model
        if not _should_fallback_gemini_model(parsed, diagnostics):
            return parsed, diagnostics
        last_parsed = parsed
        last_diagnostics = diagnostics
        logger.warning(
            "[COMFY PLAN] gemini_only model fallback requested=%s fallback_from=%s fallback_to=%s status=%s",
            requested_model,
            diagnostics.get("fallbackFrom") or candidate_model,
            candidate_model,
            diagnostics.get("httpStatus"),
        )

    return last_parsed, last_diagnostics


def _normalize_scene(scene: dict[str, Any], idx: int, available_refs_by_role: dict[str, list[dict[str, str]]] | None = None) -> dict[str, Any]:
    src = scene if isinstance(scene, dict) else {}
    start_sec = src.get("startSec")
    end_sec = src.get("endSec")
    duration_sec = src.get("durationSec")

    if start_sec is None and isinstance(src.get("timeRange"), dict):
        start_sec = src["timeRange"].get("startSec")
        end_sec = src["timeRange"].get("endSec")
    if start_sec is None:
        start_sec = src.get("start")
    if end_sec is None:
        end_sec = src.get("end")

    try:
        start_n = float(start_sec) if start_sec is not None else None
    except Exception:
        start_n = None
    try:
        end_n = float(end_sec) if end_sec is not None else None
    except Exception:
        end_n = None
    try:
        duration_n = float(duration_sec) if duration_sec is not None else None
    except Exception:
        duration_n = None

    if duration_n is None and start_n is not None and end_n is not None:
        duration_n = max(0.0, end_n - start_n)

    refs_used, ref_directives, primary_role, secondary_roles = _normalize_scene_ref_roles(src, available_refs_by_role)
    available_roles = {
        role for role in COMFY_REF_ROLES if isinstance((available_refs_by_role or {}).get(role), list) and len((available_refs_by_role or {}).get(role) or []) > 0
    }
    hero_entity_id = str(src.get("heroEntityId") or primary_role or "").strip() or primary_role
    support_entity_ids = [
        str(item or "").strip()
        for item in (src.get("supportEntityIds") if isinstance(src.get("supportEntityIds"), list) else secondary_roles)
        if str(item or "").strip() in COMFY_REF_ROLES and str(item or "").strip() != hero_entity_id
    ]
    support_entity_ids = list(dict.fromkeys(support_entity_ids))
    must_appear = [
        str(item or "").strip()
        for item in (src.get("mustAppear") if isinstance(src.get("mustAppear"), list) else [hero_entity_id] + support_entity_ids)
        if str(item or "").strip() in COMFY_REF_ROLES
    ]
    must_appear = list(dict.fromkeys([r for r in must_appear if r in available_roles]))
    must_not_appear = [
        str(item or "").strip()
        for item in (src.get("mustNotAppear") if isinstance(src.get("mustNotAppear"), list) else [])
        if str(item or "").strip() in COMFY_REF_ROLES
    ]
    must_not_appear = list(dict.fromkeys(must_not_appear))

    image_prompt_ru = str(src.get("imagePromptRu") or src.get("imagePrompt") or "").strip()
    image_prompt_en = str(src.get("imagePromptEn") or "").strip()
    video_prompt_ru = str(src.get("videoPromptRu") or src.get("videoPrompt") or "").strip()
    video_prompt_en = str(src.get("videoPromptEn") or "").strip()
    active_refs = src.get("activeRefs") if isinstance(src.get("activeRefs"), list) else refs_used
    active_refs = [str(role).strip() for role in active_refs if str(role).strip() in COMFY_REF_ROLES]
    if not active_refs:
        active_refs = refs_used
    scene_action = str(src.get("sceneAction") or src.get("visualAction") or src.get("sceneNarrativeStep") or "").strip()
    environment_motion = str(src.get("environmentMotion") or src.get("motionPlan") or "").strip()
    camera_plan = str(src.get("cameraPlan") or src.get("cameraIntent") or "").strip()
    transition_type = _normalize_transition_type(src.get("transitionType"), idx)
    camera_type = str(src.get("cameraType") or "").strip() or _infer_camera_type(camera_plan)
    camera_movement = str(src.get("cameraMovement") or src.get("cameraMove") or environment_motion or "").strip()
    camera_position = str(src.get("cameraPosition") or src.get("cameraPlacement") or "").strip()
    visual_mode = str(src.get("visualMode") or GEMINI_ONLY_VISUAL_MODE_DEFAULT).strip() or GEMINI_ONLY_VISUAL_MODE_DEFAULT
    ref_usage_reason = str(src.get("refUsageReason") or src.get("roleSelectionReason") or "").strip()
    continuity_locks_used = src.get("continuityLocksUsed") if isinstance(src.get("continuityLocksUsed"), list) else []
    continuity_locks_used = [str(item).strip() for item in continuity_locks_used if str(item).strip()]
    if not scene_action and not environment_motion:
        scene_action = "character slightly shifts position, breathes, interacts subtly with environment"
    role_logic_action = scene_action or environment_motion or "supports the scene beat"
    character_role_logic = src.get("characterRoleLogic") if isinstance(src.get("characterRoleLogic"), list) else []
    if not character_role_logic:
        character_role_logic = [
            {
                "refId": role,
                "roleInScene": "actor" if role == primary_role else "background",
                "action": role_logic_action,
                "reason": ref_usage_reason or "selected because this entity is visually relevant to the scene meaning",
            }
            for role in [primary_role, *secondary_roles]
            if role in COMFY_REF_ROLES
        ]
    else:
        normalized_role_logic: list[dict[str, Any]] = []
        for item in character_role_logic:
            if not isinstance(item, dict):
                continue
            ref_id = str(item.get("refId") or item.get("role") or "").strip()
            if ref_id not in COMFY_REF_ROLES:
                continue
            role_in_scene = str(item.get("roleInScene") or "").strip().lower()
            if role_in_scene not in {"observer", "actor", "background"}:
                role_in_scene = "actor" if ref_id == primary_role else "background"
            normalized_role_logic.append(
                {
                    "refId": ref_id,
                    "roleInScene": role_in_scene,
                    "action": str(item.get("action") or role_logic_action).strip(),
                    "reason": str(item.get("reason") or ref_usage_reason or "selected because this entity is relevant to the scene meaning").strip(),
                }
            )
        character_role_logic = normalized_role_logic

    dynamic_score = int(bool(scene_action)) + int(bool(environment_motion)) + int(bool(camera_plan))
    weak_scene = dynamic_score < 2
    hallucination_text = " ".join([image_prompt_en, video_prompt_en, str(src.get("visualDescription") or "")]).lower()
    object_hallucination_risk = "high" if ("props" not in refs_used and any(token in hallucination_text for token in ["giant", "massive", "oversized", "huge machine", "device", "artifact", "monolith", "foreground object"])) else "low"
    human_anchor_type = _normalize_human_anchor_type(src.get("humanAnchorType"), active_refs, src)
    continuity_parts = [str(src.get("continuity") or "").strip()]
    if scene_action or environment_motion:
        continuity_parts.append("scene contains active motion")
    if transition_type == "continuation":
        continuity_parts.append("prefer continued camera movement from previous scene")
    elif transition_type == "enter_transition":
        continuity_parts.append("camera physically enters the next space")
    elif transition_type in {"justified_cut", "perspective_shift", "match_cut"}:
        continuity_parts.append(f"{transition_type} must be narratively justified")
    continuity_text = "; ".join([part for part in continuity_parts if part]).strip("; ")

    image_missing_langs: list[str] = []
    video_missing_langs: list[str] = []

    if image_prompt_ru and image_prompt_en:
        image_sync_status = PROMPT_SYNC_STATUS_SYNCED
    elif image_prompt_ru or image_prompt_en:
        image_sync_status = PROMPT_SYNC_STATUS_NEEDS_SYNC
        if not image_prompt_ru:
            image_missing_langs.append("ru")
        if not image_prompt_en:
            image_missing_langs.append("en")
    else:
        image_sync_status = PROMPT_SYNC_STATUS_NEEDS_SYNC
        image_missing_langs.extend(["ru", "en"])

    if video_prompt_ru and video_prompt_en:
        video_sync_status = PROMPT_SYNC_STATUS_SYNCED
    elif video_prompt_ru or video_prompt_en:
        video_sync_status = PROMPT_SYNC_STATUS_NEEDS_SYNC
        if not video_prompt_ru:
            video_missing_langs.append("ru")
        if not video_prompt_en:
            video_missing_langs.append("en")
    else:
        video_sync_status = PROMPT_SYNC_STATUS_NEEDS_SYNC
        video_missing_langs.extend(["ru", "en"])

    prompt_language_status = {
        "image": "ru_en_present" if image_prompt_ru and image_prompt_en else ("ru_missing_en_fallback" if image_prompt_en else ("en_missing_ru_only" if image_prompt_ru else "missing_both")),
        "video": "ru_en_present" if video_prompt_ru and video_prompt_en else ("ru_missing_en_fallback" if video_prompt_en else ("en_missing_ru_only" if video_prompt_ru else "missing_both")),
    }

    return {
        "sceneId": str(src.get("sceneId") or f"scene-{idx + 1}"),
        "title": str(src.get("title") or f"Scene {idx + 1}"),
        "startSec": start_n,
        "endSec": end_n,
        "durationSec": duration_n,
        "sceneText": str(src.get("sceneText") or ""),
        "sceneMeaning": str(src.get("sceneMeaning") or ""),
        "visualDescription": str(src.get("visualDescription") or ""),
        "cameraPlan": camera_plan,
        "cameraType": camera_type,
        "cameraMovement": camera_movement,
        "cameraPosition": camera_position,
        "motionPlan": str(src.get("motionPlan") or ""),
        "sfxPlan": str(src.get("sfxPlan") or ""),
        "sceneAction": scene_action,
        "focalSubject": str(src.get("focalSubject") or src.get("primarySubject") or primary_role or "").strip(),
        "visualClue": str(src.get("visualClue") or src.get("visualEvidence") or src.get("visualDescription") or "").strip(),
        "cameraIntent": str(src.get("cameraIntent") or camera_plan or "").strip(),
        "transitionType": transition_type,
        "visualMode": visual_mode,
        "humanAnchorType": human_anchor_type,
        "forbiddenInsertions": [str(item).strip() for item in (src.get("forbiddenInsertions") if isinstance(src.get("forbiddenInsertions"), list) else []) if str(item).strip()],
        "environmentMotion": environment_motion,
        "sfxSuggestion": str(src.get("sfxSuggestion") or src.get("sfxPlan") or "").strip(),
        "sceneNarrativeStep": str(src.get("sceneNarrativeStep") or ""),
        "sceneGoal": str(src.get("sceneGoal") or ""),
        "storyMission": str(src.get("storyMission") or ""),
        "sceneOutputRule": str(src.get("sceneOutputRule") or "scene image first"),
        "primaryRole": primary_role,
        "secondaryRoles": secondary_roles,
        "continuity": continuity_text,
        "continuityLocksUsed": continuity_locks_used,
        "imagePrompt": image_prompt_en,
        "videoPrompt": video_prompt_en,
        "imagePromptRu": image_prompt_ru,
        "imagePromptEn": image_prompt_en,
        "videoPromptRu": video_prompt_ru,
        "videoPromptEn": video_prompt_en,
        "imagePromptSyncStatus": image_sync_status,
        "videoPromptSyncStatus": video_sync_status,
        "promptMissingLangs": {
            "image": image_missing_langs,
            "video": video_missing_langs,
        },
        "promptLanguageStatus": prompt_language_status,
        "ruPromptMissing": {"image": not bool(image_prompt_ru), "video": not bool(video_prompt_ru)},
        "enPromptPresent": {"image": bool(image_prompt_en), "video": bool(video_prompt_en)},
        "refsUsed": refs_used,
        "activeRefs": active_refs,
        "refUsageReason": ref_usage_reason,
        "characterRoleLogic": character_role_logic,
        "sceneDynamicScore": dynamic_score,
        "weakScene": weak_scene,
        "objectHallucinationRisk": object_hallucination_risk,
        "refDirectives": ref_directives,
        "heroEntityId": hero_entity_id,
        "supportEntityIds": support_entity_ids,
        "mustAppear": must_appear,
        "mustNotAppear": must_not_appear,
        "environmentLock": bool(src.get("environmentLock", "location" in must_appear or ref_directives.get("location") == "environment_required")),
        "styleLock": bool(src.get("styleLock", "style" in refs_used or ref_directives.get("style") in {"required", "optional"})),
        "identityLock": bool(src.get("identityLock", any(role in refs_used for role in ["character_1", "character_2", "character_3", "group", "animal", "props"]))),
        "spokenText": str(src.get("spokenText") or ""),
        "confidence": _to_float(src.get("confidence")) or 0.0,
        "roleSelectionReason": str(src.get("roleSelectionReason") or "").strip(),
        # Runtime render-state fields are intentionally initialized outside planner contract.
        "imageUrl": "",
        "videoUrl": "",
    }


def _normalize_gemini_scenes(
    scenes: list[dict[str, Any]],
    available_refs_by_role: dict[str, list[dict[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    return [_normalize_scene(scene, idx, available_refs_by_role) for idx, scene in enumerate(scenes)]


def _build_director_debug(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    if not scenes:
        return {
            "cameraContinuityScore": 0.0,
            "transitionTypesByScene": {},
            "humanAnchorCoverage": 0.0,
            "scenesWithHumanAnchor": [],
            "visualModesByScene": {},
            "cameraTypesByScene": {},
            "continuationChainCount": 0,
            "randomCutRisk": "unknown",
        }

    transition_types_by_scene = {str(scene.get("sceneId") or f"scene-{idx + 1}"): str(scene.get("transitionType") or "") for idx, scene in enumerate(scenes)}
    visual_modes_by_scene = {str(scene.get("sceneId") or f"scene-{idx + 1}"): str(scene.get("visualMode") or "") for idx, scene in enumerate(scenes)}
    camera_types_by_scene = {str(scene.get("sceneId") or f"scene-{idx + 1}"): str(scene.get("cameraType") or "") for idx, scene in enumerate(scenes)}
    scenes_with_human_anchor = [
        str(scene.get("sceneId") or f"scene-{idx + 1}")
        for idx, scene in enumerate(scenes)
        if str(scene.get("humanAnchorType") or "none") != "none"
    ]
    human_anchor_coverage = len(scenes_with_human_anchor) / max(1, len(scenes))

    continuation_chain_count = 0
    continuity_points = 0
    possible_points = max(0, len(scenes) - 1)
    for idx in range(1, len(scenes)):
        prev = scenes[idx - 1]
        cur = scenes[idx]
        transition_type = str(cur.get("transitionType") or "")
        prev_camera = str(prev.get("cameraType") or "")
        cur_camera = str(cur.get("cameraType") or "")
        if transition_type in {"continuation", "enter_transition"}:
            continuation_chain_count += 1
            continuity_points += 1
            if prev_camera and cur_camera and prev_camera == cur_camera:
                continuity_points += 1
        elif transition_type == "justified_cut":
            continuity_points += 0.5
        elif transition_type == "match_cut":
            continuity_points += 0.75
        elif transition_type == "perspective_shift":
            continuity_points += 0.5

    max_points = max(1.0, possible_points * 2.0)
    camera_continuity_score = round((continuity_points / max_points) * 100.0, 1)
    unjustified_cut_like_count = sum(1 for idx, scene in enumerate(scenes) if idx > 0 and str(scene.get("transitionType") or "") == "justified_cut")
    random_cut_risk = "low"
    if unjustified_cut_like_count >= max(2, len(scenes) // 3):
        random_cut_risk = "medium"
    if unjustified_cut_like_count >= max(3, len(scenes) // 2):
        random_cut_risk = "high"

    return {
        "cameraContinuityScore": camera_continuity_score,
        "transitionTypesByScene": transition_types_by_scene,
        "humanAnchorCoverage": round(human_anchor_coverage, 3),
        "scenesWithHumanAnchor": scenes_with_human_anchor,
        "visualModesByScene": visual_modes_by_scene,
        "cameraTypesByScene": camera_types_by_scene,
        "continuationChainCount": continuation_chain_count,
        "randomCutRisk": random_cut_risk,
    }


def _build_segmentation_debug(scenes: list[dict[str, Any]], audio_story_mode: str, timing_debug: dict[str, Any]) -> dict[str, Any]:
    durations = [max(0.0, _to_float(scene.get("durationSec")) or 0.0) for scene in scenes]
    avg_duration = (sum(durations) / len(durations)) if durations else 0.0
    max_duration = max(durations) if durations else 0.0
    min_duration = min(durations) if durations else 0.0
    short_count = sum(1 for d in durations if 0.0 < d < 2.0)
    long_count = sum(1 for d in durations if d > 8.0)

    suspicious_even_chunks = False
    if len(durations) >= 3 and avg_duration > 0:
        max_delta = max(abs(d - avg_duration) for d in durations)
        suspicious_even_chunks = max_delta <= 0.35

    mode_reason_map = {
        "lyrics_music": "semantic_and_vocal_phrases_with_music_transitions",
        "music_only": "music_phrase_energy_and_structure_transitions",
        "music_plus_text": "text_meaning_chunks_synced_to_music_transitions",
        "speech_narrative": "spoken_pauses_sentence_endings_topic_shifts_and_semantic_beats",
    }
    mode_reason = mode_reason_map.get(audio_story_mode, "music_driven_transitions")

    return {
        "averageSceneDurationSec": _round_sec(avg_duration),
        "maxSceneDurationSec": _round_sec(max_duration),
        "minSceneDurationSec": _round_sec(min_duration),
        "shortSceneCountUnder2Sec": short_count,
        "longSceneCountOver8Sec": long_count,
        "normalizationApplied": bool(timing_debug.get("normalizationApplied")),
        "normalizationReason": timing_debug.get("normalizationReason"),
        "segmentationMode": "phrase_transition_oriented",
        "segmentationReason": mode_reason,
        "suspiciousEqualChunking": suspicious_even_chunks,
    }


def _needs_segmentation_refinement(segmentation_debug: dict[str, Any], audio_duration_sec: float | None, scene_count: int) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    avg_duration = _to_float(segmentation_debug.get("averageSceneDurationSec")) or 0.0
    duration = _to_float(audio_duration_sec)

    if bool(segmentation_debug.get("suspiciousEqualChunking")):
        reasons.append("suspicious_equal_chunking")
    if int(segmentation_debug.get("longSceneCountOver8Sec") or 0) > 0:
        reasons.append("has_scene_over_8_sec")
    if avg_duration > 7.0:
        reasons.append("average_scene_too_long")
    if duration is not None and 25.0 <= duration <= 35.0 and scene_count < 4:
        reasons.append("too_few_scenes_for_25_35_sec_track")

    # Mechanical coarse blocks often look both long and near-uniform.
    if avg_duration >= 6.0 and bool(segmentation_debug.get("suspiciousEqualChunking")):
        reasons.append("large_uniform_blocks_detected")

    return (len(reasons) > 0), reasons


def _build_preview_from_scenes(scenes: list[dict[str, Any]], world_lock: dict[str, Any]) -> dict[str, Any]:
    if not scenes:
        return {
            "sourceSceneId": "",
            "previewType": "none",
            "activeRefs": [],
            "imagePrompt": "",
            "previewScore": 0,
            "continuityNotes": str(world_lock.get("atmosphere") or ""),
        }

    def _preview_score(scene: dict[str, Any]) -> int:
        score = 0
        if str(scene.get("sceneAction") or "").strip():
            score += 2
        strong_visual_focus = bool(scene.get("primaryRole")) or "location" in (scene.get("activeRefs") or []) or "props" in (scene.get("activeRefs") or [])
        if strong_visual_focus:
            score += 2
        if str(scene.get("sceneMeaning") or scene.get("visualDescription") or scene.get("imagePromptEn") or scene.get("imagePrompt") or "").strip():
            score += 2
        if any(role in (scene.get("activeRefs") or []) for role in ["character_1", "character_2", "character_3", "group", "animal"]):
            score += 1
        if "contrast" in str(scene.get("imagePromptEn") or scene.get("imagePrompt") or "").lower() or "light" in str(scene.get("continuity") or "").lower():
            score += 1
        return score

    scored_scenes = [(scene, _preview_score(scene)) for scene in scenes]
    best_scene, best_score = max(
        scored_scenes,
        key=lambda item: (
            item[1],
            _to_float(item[0].get("sceneDynamicScore")) or 0.0,
            _to_float(item[0].get("confidence")) or 0.0,
            _to_float(item[0].get("durationSec")) or 0.0,
        ),
    )
    preview_type = "environment_scene"
    if str(best_scene.get("sceneAction") or "").strip():
        preview_type = "action_scene"
    elif any(role in (best_scene.get("activeRefs") or []) for role in ["character_1", "character_2", "character_3", "group", "animal"]):
        preview_type = "hero_scene"
    return {
        "sourceSceneId": str(best_scene.get("sceneId") or ""),
        "previewType": preview_type,
        "activeRefs": list(best_scene.get("activeRefs") or []),
        "imagePrompt": str(best_scene.get("imagePromptEn") or best_scene.get("imagePrompt") or ""),
        "previewScore": best_score,
        "worldLock": world_lock,
        "entityLocksUsed": list(best_scene.get("activeRefs") or []),
        "continuityNotes": str(best_scene.get("continuity") or world_lock.get("atmosphere") or ""),
    }


def _run_comfy_plan_gemini_only(normalized: dict[str, Any]) -> dict[str, Any]:
    story_context = _derive_gemini_only_story_context(normalized)
    story_source, narrative_source = _normalize_story_sources(
        story_context.get("storySource") or normalized.get("storySource"),
        story_context.get("narrativeSource") or normalized.get("narrativeSource"),
    )
    story_context = {
        **story_context,
        "storySource": story_source,
        "narrativeSource": narrative_source,
    }
    normalized = {
        **normalized,
        "storySource": story_source,
        "narrativeSource": narrative_source,
        "timelineSource": story_context.get("timelineSource") or normalized.get("timelineSource"),
        "storyMissionSummary": story_context.get("storyMissionSummary") or normalized.get("storyMissionSummary"),
    }
    reference_profiles = build_reference_profiles(normalized.get("refsByRole") or {})
    world_lock = _build_world_lock(normalized, reference_profiles)
    entity_locks = _build_entity_locks(normalized, reference_profiles)
    gemini_payload = _build_gemini_planner_payload(normalized, world_lock, entity_locks)
    multimodal_parts, media_debug = _build_gemini_only_multimodal_parts(normalized, gemini_payload)

    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        missing_key_error = "gemini_api_key_missing"
        return {
            "ok": False,
            "planMeta": {"plannerMode": "gemini_only"},
            "globalContinuity": world_lock,
            "scenes": [],
            "warnings": [],
            "errors": [missing_key_error, *story_context.get("errors", [])],
            "debug": {
                "plannerMode": "gemini_only",
                "sanitizedError": _humanize_storyboard_error(missing_key_error),
                "storySource": story_context.get("storySource"),
                "narrativeSource": story_context.get("narrativeSource"),
                "weakSemanticContext": story_context.get("weakSemanticContext"),
                "semanticContextReason": story_context.get("semanticContextReason"),
                "hasAudio": story_context.get("hasAudio"),
                "hasText": story_context.get("hasText"),
                "hasRefs": story_context.get("hasRefs"),
                "worldLock": world_lock,
                "entityLocks": entity_locks,
                "geminiPayload": gemini_payload,
                **media_debug,
                "rawEntityTypesByRole": {
                    role: lock.get("rawEntityType")
                    for role, lock in entity_locks.items()
                    if isinstance(lock, dict)
                },
                "normalizedEntityTypesByRole": {
                    role: lock.get("normalizedEntityType") or lock.get("entityType")
                    for role, lock in entity_locks.items()
                    if isinstance(lock, dict)
                },
            },
        }

    if story_context.get("errors"):
        primary_story_error = str((story_context.get("errors") or [""])[0] or "").strip()
        return {
            "ok": False,
            "planMeta": {
                "plannerMode": "gemini_only",
                "storyMissionSummary": normalized.get("storyMissionSummary"),
                "timelineSource": normalized.get("timelineSource"),
                "narrativeSource": normalized.get("narrativeSource"),
            },
            "globalContinuity": world_lock,
            "scenes": [],
            "warnings": story_context.get("warnings") or [],
            "errors": story_context.get("errors") or [],
            "debug": {
                "plannerMode": "gemini_only",
                "requestedModel": (settings.GEMINI_TEXT_MODEL or FALLBACK_GEMINI_MODEL or "gemini-2.5-flash").strip(),
                "fallbackFrom": None,
                "fallbackTo": None,
                "effectiveModel": None,
                "parseFailedReason": primary_story_error,
                "sanitizedError": _humanize_storyboard_error(primary_story_error),
                "storySource": story_context.get("storySource"),
                "narrativeSource": story_context.get("narrativeSource"),
                "weakSemanticContext": story_context.get("weakSemanticContext"),
                "semanticContextReason": story_context.get("semanticContextReason"),
                "hasAudio": story_context.get("hasAudio"),
                "hasText": story_context.get("hasText"),
                "hasRefs": story_context.get("hasRefs"),
                "errorText": "",
                "worldLock": world_lock,
                "entityLocks": entity_locks,
                "geminiPayload": gemini_payload,
                **media_debug,
            },
        }

    requested_model = (settings.GEMINI_TEXT_MODEL or FALLBACK_GEMINI_MODEL or "gemini-2.5-flash").strip()
    body = {
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
        "contents": [{"role": "user", "parts": multimodal_parts}],
    }
    parsed, diagnostics = _call_gemini_plan_with_model_fallback(api_key, requested_model, body)

    warnings = [
        *[str(item) for item in (parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []) if str(item).strip()],
        *[str(item) for item in (story_context.get("warnings") if isinstance(story_context.get("warnings"), list) else []) if str(item).strip()],
    ]
    errors = [str(item) for item in (parsed.get("errors") if isinstance(parsed.get("errors"), list) else []) if str(item).strip()]
    if diagnostics.get("httpStatus"):
        error_code, sanitized_error = _sanitize_gemini_error(diagnostics, parsed)
        if error_code not in errors:
            errors.append(error_code)
        if sanitized_error and not diagnostics.get("sanitizedError"):
            diagnostics["sanitizedError"] = sanitized_error
    if diagnostics.get("fallbackFrom") and diagnostics.get("fallbackTo"):
        warnings.append(f"gemini_model_fallback:{diagnostics['fallbackFrom']}->{diagnostics['fallbackTo']}")

    parsed_world_lock = parsed.get("worldLock") if isinstance(parsed.get("worldLock"), dict) else {}
    merged_world_lock = {**world_lock, **parsed_world_lock}
    parsed_entity_locks = parsed.get("entityLocks") if isinstance(parsed.get("entityLocks"), dict) else {}
    merged_entity_locks = {**entity_locks, **parsed_entity_locks}

    raw_scenes = parsed.get("scenes") if isinstance(parsed.get("scenes"), list) else []
    scenes = _normalize_gemini_scenes(raw_scenes, normalized.get("refsByRole"))
    scenes, timing_debug = _normalize_scene_timeline(scenes, normalized.get("audioDurationSec"))
    scenes, speech_split_debug, speech_split_warnings = _split_oversized_speech_scenes(scenes, normalized)
    warnings.extend(speech_split_warnings)
    timing_debug["sceneCountAfterSpeechSplit"] = len(scenes)
    segmentation_debug = {**_build_segmentation_debug(scenes, normalized.get("audioStoryMode") or "lyrics_music", timing_debug), **speech_split_debug}
    director_debug = _build_director_debug(scenes)
    if float(director_debug.get("humanAnchorCoverage") or 0.0) < 0.3:
        warnings.append("director_human_anchor_coverage_below_target")
    preview_raw = parsed.get("preview") if isinstance(parsed.get("preview"), dict) else {}
    preview = {
        **_build_preview_from_scenes(scenes, merged_world_lock),
        **preview_raw,
    }
    if not preview.get("sourceSceneId") and scenes:
        preview["sourceSceneId"] = str((scenes[0] or {}).get("sceneId") or "")

    plan_meta = {
        "mode": normalized.get("mode"),
        "plannerMode": "gemini_only",
        "output": normalized.get("output"),
        "stylePreset": normalized.get("stylePreset"),
        "audioStoryMode": normalized.get("audioStoryMode"),
        "genre": normalized.get("genre"),
        "storyControlMode": normalized.get("storyControlMode"),
        "storyMissionSummary": normalized.get("storyMissionSummary"),
        "timelineSource": normalized.get("timelineSource") or "gemini_semantic_scene_planning",
        "narrativeSource": narrative_source,
        "storySource": story_source,
        "weakSemanticContext": bool(story_context.get("weakSemanticContext")),
        "semanticContextReason": story_context.get("semanticContextReason") or "",
        "audioDurationSec": timing_debug.get("audioDurationSec"),
        "timelineDurationSec": timing_debug.get("timelineDurationSec"),
        "sceneDurationTotalSec": timing_debug.get("sceneDurationTotalSec"),
        "worldLock": merged_world_lock,
        "entityLocks": merged_entity_locks,
        "preview": preview,
        "summary": {
            "sceneCount": len(scenes),
            "cameraContinuityScore": director_debug.get("cameraContinuityScore"),
            "humanAnchorCoverage": director_debug.get("humanAnchorCoverage"),
            "continuationChainCount": director_debug.get("continuationChainCount"),
        },
    }
    return {
        "ok": len(errors) == 0,
        "planMeta": plan_meta,
        "globalContinuity": merged_world_lock,
        "scenes": scenes,
        "warnings": warnings,
        "errors": errors,
        "debug": {
            **(parsed.get("debug") if isinstance(parsed.get("debug"), dict) else {}),
            "plannerMode": "gemini_only",
            "requestedModel": diagnostics.get("requestedModel") or requested_model,
            "fallbackFrom": diagnostics.get("fallbackFrom"),
            "fallbackTo": diagnostics.get("fallbackTo"),
            "effectiveModel": diagnostics.get("effectiveModel") or requested_model,
            "httpStatus": diagnostics.get("httpStatus"),
            "rawPreview": diagnostics.get("rawPreview") or "",
            "errorText": diagnostics.get("errorText") or "",
            "parseFailedReason": "; ".join(errors) if errors else "",
            "sanitizedError": diagnostics.get("sanitizedError") or _humanize_storyboard_error((errors[0] if errors else "")),
            "storySource": story_context.get("storySource"),
            "narrativeSource": story_context.get("narrativeSource"),
            "weakSemanticContext": bool(story_context.get("weakSemanticContext")),
            "semanticContextReason": story_context.get("semanticContextReason") or "",
            "hasAudio": story_context.get("hasAudio"),
            "hasText": story_context.get("hasText"),
            "hasRefs": story_context.get("hasRefs"),
            "geminiPayload": gemini_payload,
            "worldLock": merged_world_lock,
            "worldLockSummary": {
                "environmentType": merged_world_lock.get("environmentType"),
                "environmentSubtype": merged_world_lock.get("environmentSubtype"),
                "timeOfDay": merged_world_lock.get("timeOfDay"),
                "lighting": merged_world_lock.get("lighting"),
                "atmosphere": merged_world_lock.get("atmosphere"),
                "palette": merged_world_lock.get("palette"),
            },
            "entityLocks": merged_entity_locks,
            "entityLockSummary": {
                role: {
                    "entityType": lock.get("entityType"),
                    "rawEntityType": lock.get("rawEntityType"),
                    "normalizedEntityType": lock.get("normalizedEntityType"),
                    "label": lock.get("label"),
                    "canonicalDetails": lock.get("canonicalDetails"),
                    "forbiddenChanges": lock.get("forbiddenChanges"),
                }
                for role, lock in merged_entity_locks.items()
                if isinstance(lock, dict)
            },
            "preview": preview,
            "timing": timing_debug,
            "segmentation": segmentation_debug,
            **director_debug,
            "referenceProfilesSummary": summarize_profiles(reference_profiles),
            "activeRolesByScene": {str(scene.get("sceneId") or ""): list(scene.get("activeRefs") or []) for scene in scenes},
            **media_debug,
            "rawEntityTypesByRole": {
                role: lock.get("rawEntityType")
                for role, lock in merged_entity_locks.items()
                if isinstance(lock, dict)
            },
            "normalizedEntityTypesByRole": {
                role: lock.get("normalizedEntityType") or lock.get("entityType")
                for role, lock in merged_entity_locks.items()
                if isinstance(lock, dict)
            },
            "sceneDynamicScores": {
                str(scene.get("sceneId") or ""): {
                    "sceneDynamicScore": scene.get("sceneDynamicScore"),
                    "weakScene": scene.get("weakScene"),
                }
                for scene in scenes
            },
            "previewScore": preview.get("previewScore"),
        },
    }


def run_comfy_plan(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_comfy_payload(payload)
    if normalized.get("plannerMode") == "gemini_only":
        return _run_comfy_plan_gemini_only(normalized)
    if normalized.get("mode") == "clip":
        logger.info(
            "[COMFY PLAN][clip] plannerMode=%s audioStoryMode=%s text=%s lyricsText=%s transcriptText=%s spokenHint=%s semanticHints=%s semanticSummary=%s audio=%s",
            normalized.get("plannerMode"),
            normalized.get("audioStoryMode"),
            bool(normalized.get("text")),
            bool(normalized.get("lyricsText")),
            bool(normalized.get("transcriptText")),
            bool(normalized.get("spokenTextHint")),
            bool(normalized.get("audioSemanticHints")),
            bool(normalized.get("audioSemanticSummary")),
            bool(normalized.get("audioUrl")),
        )
        clip_result = plan_comfy_clip(normalized)
        clip_meta = clip_result.get("planMeta") if isinstance(clip_result, dict) else {}
        if isinstance(clip_meta, dict):
            clip_meta["plannerMode"] = normalized.get("plannerMode") or "legacy"
        clip_debug = clip_result.get("debug") if isinstance(clip_result.get("debug"), dict) else {}
        if isinstance(clip_debug, dict):
            clip_debug["plannerMode"] = normalized.get("plannerMode") or "legacy"
        logger.info(
            "[COMFY PLAN][clip] resolved textSource=%s exactLyricsAvailable=%s transcriptAvailable=%s usedSemanticFallback=%s semanticHintCount=%s",
            (clip_meta or {}).get("textSource"),
            (clip_meta or {}).get("exactLyricsAvailable"),
            (clip_meta or {}).get("transcriptAvailable"),
            (clip_meta or {}).get("usedSemanticFallback"),
            (clip_meta or {}).get("semanticHintCount"),
        )
        return clip_result

    reference_profiles = build_reference_profiles(normalized.get("refsByRole") or {})
    normalized["referenceProfiles"] = reference_profiles
    refs_presence = {k: len(v) for k, v in normalized["refsByRole"].items()}
    debug_signature = "COMFY_DEBUG_STEP_V1"
    module_file = __file__
    # TEMP HARD DEBUG STEP (REMOVE AFTER CONFIRMATION):
    # VERIFY EXACT FILE + EXACT MODEL for COMFY planner requests.
    hard_debug_disable_fallback = True
    logger.info(
        "[COMFY PLAN] request summary plannerMode=%s mode=%s output=%s style=%s audioStoryMode=%s",
        normalized["plannerMode"],
        normalized["mode"],
        normalized["output"],
        normalized["stylePreset"],
        normalized["audioStoryMode"],
    )
    logger.info("[COMFY PLAN] text/audio/refs presence text=%s audio=%s refs=%s", bool(normalized["text"]), bool(normalized["audioUrl"]), refs_presence)
    logger.warning("[%s] run_comfy_plan entered module_file=%s", debug_signature, module_file)
    print(f"[{debug_signature}] ENTER run_comfy_plan")
    print(f"[{debug_signature}] FILE = {module_file}")

    api_key = (settings.GEMINI_API_KEY or "").strip()
    # TEMP DEBUG STEP: hard pin model to remove ambiguity for diagnostic run.
    requested_model = "gemini-2.5-flash"
    logger.warning("[%s] hard_requested_model=%s", debug_signature, requested_model)
    logger.warning("[%s] effective_model_before_request=%s", debug_signature, requested_model)
    print(f"[{debug_signature}] HARD MODEL = {requested_model}")
    if not api_key:
        return {"ok": False, "planMeta": {}, "globalContinuity": {}, "scenes": [], "warnings": [], "errors": ["gemini_api_key_missing"], "debug": {"debugSignature": debug_signature, "moduleFile": module_file, "requestedModel": requested_model, "effectiveModel": None, "httpStatus": None, "rawPreview": "", "sanitizedError": _humanize_storyboard_error("gemini_api_key_missing"), "normalizedPayload": normalized, "fallbackFrom": None, "normalizedScenesCount": 0}}

    body = {
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.4},
        "contents": [{"role": "user", "parts": [{"text": build_comfy_planner_prompt(normalized)}]}],
    }

    parsed, diagnostics = _call_gemini_plan(api_key, requested_model, body)
    warnings: list[str] = []
    errors: list[str] = []

    if not hard_debug_disable_fallback and diagnostics["httpStatus"] == 404 and requested_model != FALLBACK_GEMINI_MODEL:
        logger.info("[COMFY PLAN] fallback_from=%s fallback_to=%s", requested_model, FALLBACK_GEMINI_MODEL)
        warnings.append(f"gemini_model_fallback:{requested_model}->{FALLBACK_GEMINI_MODEL}")
        parsed_fb, diagnostics_fb = _call_gemini_plan(api_key, FALLBACK_GEMINI_MODEL, body)
        diagnostics = {
            **diagnostics_fb,
            "requestedModel": requested_model,
            "effectiveModel": diagnostics_fb.get("effectiveModel") or FALLBACK_GEMINI_MODEL,
            "fallbackFrom": requested_model,
        }
        parsed = parsed_fb

    if diagnostics.get("httpStatus"):
        errors.append(f"gemini_http_error:{diagnostics['httpStatus']}")
    elif isinstance(parsed, dict) and "errors" in parsed and parsed.get("errors") == ["gemini_invalid_json"]:
        errors.append("gemini_invalid_json")
        parsed = {}

    raw_scenes = parsed.get("scenes") if isinstance(parsed.get("scenes"), list) else []
    scenes = [_normalize_scene(scene, idx, normalized.get("refsByRole")) for idx, scene in enumerate(raw_scenes)]
    prompt_contract_warnings: list[str] = []
    for scene in scenes:
        scene_id = str(scene.get("sceneId") or "unknown_scene")
        missing = scene.get("promptMissingLangs") if isinstance(scene.get("promptMissingLangs"), dict) else {}
        image_missing = missing.get("image") if isinstance(missing.get("image"), list) else []
        video_missing = missing.get("video") if isinstance(missing.get("video"), list) else []
        if image_missing:
            prompt_contract_warnings.append(f"scene:{scene_id}:image_missing_languages:{','.join(sorted(set(str(x) for x in image_missing)))}")
        if video_missing:
            prompt_contract_warnings.append(f"scene:{scene_id}:video_missing_languages:{','.join(sorted(set(str(x) for x in video_missing)))}")
    if prompt_contract_warnings:
        warnings.append("planner_prompt_language_contract_not_fully_met")
        warnings.extend(prompt_contract_warnings)

    scenes, timing_debug = _normalize_scene_timeline(scenes, normalized.get("audioDurationSec"))
    scenes, speech_split_debug, speech_split_warnings = _split_oversized_speech_scenes(scenes, normalized)
    warnings.extend(speech_split_warnings)
    timing_debug["sceneCountAfterSpeechSplit"] = len(scenes)
    segmentation_debug = {**_build_segmentation_debug(scenes, normalized.get("audioStoryMode") or "lyrics_music", timing_debug), **speech_split_debug}
    initial_segmentation_debug = dict(segmentation_debug)
    initial_scene_count = len(scenes)
    refinement_attempted = False
    refinement_succeeded = False
    refinement_reasons: list[str] = []
    refinement_pass_count = 0
    refinement_errors: list[str] = []
    refinement_warnings: list[str] = []

    needs_refinement, refinement_reasons = _needs_segmentation_refinement(
        segmentation_debug,
        normalized.get("audioDurationSec"),
        len(scenes),
    )

    if needs_refinement and len(scenes) > 0 and len(errors) == 0:
        refinement_attempted = True
        refinement_pass_count = 1
        refinement_reason_str = ",".join(refinement_reasons)
        refinement_body = {
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.35},
            "contents": [{"role": "user", "parts": [{"text": build_comfy_planner_refinement_prompt(normalized, scenes, refinement_reason_str)}]}],
        }
        refined_parsed, refined_diagnostics = _call_gemini_plan(api_key, requested_model, refinement_body)
        refined_http_status = refined_diagnostics.get("httpStatus")
        if refined_http_status:
            warnings.append(f"segmentation_refinement_http_error:{refined_http_status}")
        else:
            refinement_errors = [str(err) for err in (refined_parsed.get("errors") if isinstance(refined_parsed.get("errors"), list) else []) if str(err)]
            refinement_warnings = [str(warn) for warn in (refined_parsed.get("warnings") if isinstance(refined_parsed.get("warnings"), list) else []) if str(warn)]
            if len(refinement_errors) > 0:
                warnings.append("segmentation_refinement_failed_with_errors")
                warnings.append(f"segmentation_refinement_errors:{'|'.join(refinement_errors)}")
            if len(refinement_warnings) > 0:
                warnings.append(f"segmentation_refinement_warnings:{'|'.join(refinement_warnings)}")
            refined_raw_scenes = refined_parsed.get("scenes") if isinstance(refined_parsed.get("scenes"), list) else []
            refined_scenes = [_normalize_scene(scene, idx, normalized.get("refsByRole")) for idx, scene in enumerate(refined_raw_scenes)]
            valid_refined_scenes = [
                scene for scene in refined_scenes
                if (_to_float(scene.get("endSec")) or 0.0) > (_to_float(scene.get("startSec")) or 0.0)
            ]
            if len(refined_scenes) == 0:
                warnings.append("segmentation_refinement_returned_no_scenes")
            elif len(valid_refined_scenes) == 0:
                warnings.append("segmentation_refinement_returned_invalid_scenes")
            elif len(refinement_errors) == 0:
                scenes, timing_debug = _normalize_scene_timeline(valid_refined_scenes, normalized.get("audioDurationSec"))
                scenes, speech_split_debug, speech_split_warnings = _split_oversized_speech_scenes(scenes, normalized)
                warnings.extend(speech_split_warnings)
                timing_debug["sceneCountAfterSpeechSplit"] = len(scenes)
                segmentation_debug = {**_build_segmentation_debug(scenes, normalized.get("audioStoryMode") or "lyrics_music", timing_debug), **speech_split_debug}
                parsed = refined_parsed
                refinement_succeeded = True
                diagnostics = {
                    **diagnostics,
                    "refinement": {
                        "httpStatus": refined_diagnostics.get("httpStatus"),
                        "rawPreview": refined_diagnostics.get("rawPreview") or "",
                        "errors": refinement_errors,
                        "warnings": refinement_warnings,
                    },
                }
                warnings.append("segmentation_refined_second_pass")
            else:
                warnings.append("segmentation_refinement_not_applied_due_to_errors")

    if segmentation_debug.get("suspiciousEqualChunking"):
        warnings.append("segmentation_suspicious_equal_chunks")

    still_coarse, still_coarse_reasons = _needs_segmentation_refinement(
        segmentation_debug,
        normalized.get("audioDurationSec"),
        len(scenes),
    )
    still_coarse_after_refinement = bool(refinement_attempted and still_coarse)
    if still_coarse_after_refinement:
        reasons_suffix = f":{','.join(still_coarse_reasons)}" if still_coarse_reasons else ""
        warnings.append(f"segmentation_still_coarse_after_refinement{reasons_suffix}")
    logger.info("[COMFY PLAN] normalized scenes count=%s", len(scenes))

    parsed_errors = parsed.get("errors") if isinstance(parsed.get("errors"), list) else []
    all_errors = parsed_errors + errors

    plan_meta = (
        {
            **({"mode": normalized["mode"], "plannerMode": normalized["plannerMode"], "output": normalized["output"], "stylePreset": normalized["stylePreset"], "genre": normalized.get("genre"), "audioStoryMode": normalized["audioStoryMode"]}),
            **(parsed.get("planMeta") if isinstance(parsed.get("planMeta"), dict) else {}),
        }
    )
    plan_meta.update({
        "audioDurationSec": timing_debug.get("audioDurationSec"),
        "timelineDurationSec": timing_debug.get("timelineDurationSec"),
        "sceneDurationTotalSec": timing_debug.get("sceneDurationTotalSec"),
    })

    scene_refs_debug = _build_scene_refs_debug(scenes, normalized.get("refsByRole") or {})

    result = {
        "ok": len(all_errors) == 0,
        "planMeta": plan_meta,
        "globalContinuity": parsed.get("globalContinuity") if isinstance(parsed.get("globalContinuity"), (dict, str)) else {},
        "scenes": scenes,
        "warnings": (parsed.get("warnings") if isinstance(parsed.get("warnings"), list) else []) + warnings,
        "errors": all_errors,
        "debug": {
            **(parsed.get("debug") if isinstance(parsed.get("debug"), dict) else {}),
            "debugSignature": debug_signature,
            "moduleFile": module_file,
            "requestedModel": diagnostics.get("requestedModel") or requested_model,
            "effectiveModel": diagnostics.get("effectiveModel") or requested_model,
            "httpStatus": diagnostics.get("httpStatus"),
            "rawPreview": diagnostics.get("rawPreview") or "",
            "normalizedPayload": normalized,
            "plannerMode": normalized["plannerMode"],
            "fallbackFrom": diagnostics.get("fallbackFrom"),
            "normalizedScenesCount": len(scenes),
            "timing": timing_debug,
            "segmentation": segmentation_debug,
            "initialSegmentationDebug": initial_segmentation_debug,
            "finalSegmentationDebug": segmentation_debug,
            "initialSceneCount": initial_scene_count,
            "finalSceneCount": len(scenes),
            "initialAverageSceneDurationSec": initial_segmentation_debug.get("averageSceneDurationSec"),
            "finalAverageSceneDurationSec": segmentation_debug.get("averageSceneDurationSec"),
            "refinementApplied": refinement_attempted,
            "refinementAttempted": refinement_attempted,
            "refinementSucceeded": refinement_succeeded,
            "refinementReason": ",".join(refinement_reasons) if refinement_reasons else None,
            "refinementPassCount": refinement_pass_count,
            "refinementErrors": refinement_errors,
            "refinementWarnings": refinement_warnings,
            "stillCoarseAfterRefinement": still_coarse_after_refinement,
            "stillCoarseReasons": still_coarse_reasons if still_coarse_after_refinement else [],
            "promptContractWarnings": prompt_contract_warnings,
            "availableRefsByRoleSummary": {role: len((normalized.get("refsByRole") or {}).get(role) or []) for role in COMFY_REF_ROLES},
            "referenceProfilesSummary": summarize_profiles(reference_profiles),
            "rolesGloballyAvailable": [role for role in COMFY_REF_ROLES if len((normalized.get("refsByRole") or {}).get(role) or []) > 0],
            "sceneRoleSelection": scene_refs_debug,
            "activeRolesByScene": {item.get("sceneId"): item.get("activeRoles") for item in scene_refs_debug},
        },
    }
    if timing_debug.get("normalizationApplied"):
        result["warnings"].append(str(timing_debug.get("normalizationReason") or "timeline_normalized_to_audio"))
    first_scene = scenes[0] if scenes else {}
    logger.info(
        "[%s] result ok=%s mode=%s output=%s style=%s audioStoryMode=%s scenes=%s warnings=%s errors=%s requestedModel=%s effectiveModel=%s httpStatus=%s firstSceneId=%s firstSceneTitle=%s",
        debug_signature,
        result["ok"],
        result.get("planMeta", {}).get("mode"),
        result.get("planMeta", {}).get("output"),
        result.get("planMeta", {}).get("stylePreset"),
        result.get("planMeta", {}).get("audioStoryMode"),
        len(scenes),
        len(result["warnings"]),
        len(result["errors"]),
        result["debug"].get("requestedModel"),
        result["debug"].get("effectiveModel"),
        result["debug"].get("httpStatus"),
        first_scene.get("sceneId") if isinstance(first_scene, dict) else None,
        first_scene.get("title") if isinstance(first_scene, dict) else None,
    )
    return result



def _build_scene_refs_debug(scenes: list[dict[str, Any]], refs_by_role: dict[str, list[dict[str, str]]]) -> list[dict[str, Any]]:
    available_summary = {role: len(refs_by_role.get(role) or []) for role in COMFY_REF_ROLES}
    out: list[dict[str, Any]] = []
    for scene in scenes:
        ref_directives = scene.get("refDirectives") if isinstance(scene.get("refDirectives"), dict) else {}
        refs_used = scene.get("refsUsed") if isinstance(scene.get("refsUsed"), (list, dict)) else []
        available_roles = {role for role, count in available_summary.items() if count > 0}
        primary_role = str(scene.get("primaryRole") or "").strip()
        active_roles = _resolve_scene_active_roles(refs_used, ref_directives, available_roles, primary_role)
        secondary_roles_raw = scene.get("secondaryRoles")
        secondary_roles = [
            str(role or "").strip()
            for role in (secondary_roles_raw if isinstance(secondary_roles_raw, list) else [])
            if str(role or "").strip()
        ]
        out.append({
            "sceneId": str(scene.get("sceneId") or ""),
            "availableRefsByRoleSummary": available_summary,
            "refsUsed": refs_used,
            "refDirectives": ref_directives,
            "primaryRole": primary_role,
            "secondaryRoles": secondary_roles,
            "activeRoles": active_roles,
            "heroEntityId": scene.get("heroEntityId"),
            "supportEntityIds": scene.get("supportEntityIds") if isinstance(scene.get("supportEntityIds"), list) else [],
            "mustAppear": scene.get("mustAppear") if isinstance(scene.get("mustAppear"), list) else [],
            "mustNotAppear": scene.get("mustNotAppear") if isinstance(scene.get("mustNotAppear"), list) else [],
            "identityLock": bool(scene.get("identityLock")),
            "environmentLock": bool(scene.get("environmentLock")),
            "styleLock": bool(scene.get("styleLock")),
            "selectionReason": str(scene.get("roleSelectionReason") or "").strip() or "derived_from_refs_used_and_directives",
        })
    return out

def build_comfy_prompt_sync_prompt(payload: dict[str, Any]) -> str:
    return (
        "You are a prompt adaptation engine for visual generation. Return strict JSON only.\n"
        "Fields: ok, translatedPrompt, normalizedPrompt, debug, error.\n"
        "Rules:\n"
        "- sourceLang and targetLang are mandatory.\n"
        "- Convert source text into model-ready prompt in target language.\n"
        "- Preserve story meaning, style cues, camera and motion intent.\n"
        "- Keep concise, no explanations, no markdown, no quotes wrappers.\n"
        "- If promptType=image: prioritize visual composition, subject, light, lens/camera if present.\n"
        "- If promptType=video: preserve motion, timing, camera movement, atmosphere beats.\n"
        f"INPUT={json.dumps(payload, ensure_ascii=False)}"
    )


def _extract_text_from_response(resp: dict[str, Any]) -> str:
    return _extract_text(resp if isinstance(resp, dict) else {})


def run_comfy_prompt_sync(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    source_text = str(data.get("sourceText") or "").strip()
    source_lang = str(data.get("sourceLang") or "ru").strip().lower()
    target_lang = str(data.get("targetLang") or "en").strip().lower()
    prompt_type = str(data.get("promptType") or "image").strip().lower()
    if prompt_type not in {"image", "video"}:
        prompt_type = "image"

    if not source_text:
        return {"ok": False, "translatedPrompt": "", "normalizedPrompt": "", "error": "empty_source_text", "debug": {}}

    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        return {"ok": False, "translatedPrompt": "", "normalizedPrompt": "", "error": "GEMINI_API_KEY missing", "debug": {}}

    normalized_payload = {
        "sourceText": source_text,
        "sourceLang": source_lang,
        "targetLang": target_lang,
        "promptType": prompt_type,
        "sceneContext": data.get("sceneContext") if isinstance(data.get("sceneContext"), dict) else {},
        "stylePreset": str(data.get("stylePreset") or "").strip(),
        "mode": str(data.get("mode") or "").strip(),
    }

    body = {
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
        "contents": [{"role": "user", "parts": [{"text": build_comfy_prompt_sync_prompt(normalized_payload)}]}],
    }
    model = "gemini-2.5-flash"
    resp = post_generate_content(api_key, model, body, timeout=90)
    if isinstance(resp, dict) and resp.get("__http_error__"):
        return {
            "ok": False,
            "translatedPrompt": "",
            "normalizedPrompt": "",
            "error": f"gemini_http_error:{resp.get('status')}",
            "debug": {"status": resp.get("status"), "raw": str(resp.get("text") or "")[:1000]},
        }

    raw = _extract_text_from_response(resp)
    parsed = _extract_json(raw)
    if not isinstance(parsed, dict):
        return {
            "ok": False,
            "translatedPrompt": "",
            "normalizedPrompt": "",
            "error": "gemini_invalid_json",
            "debug": {"raw": raw[:1200]},
        }

    translated = str(parsed.get("translatedPrompt") or parsed.get("normalizedPrompt") or "").strip()
    normalized_prompt = str(parsed.get("normalizedPrompt") or translated).strip()
    if not translated:
        return {
            "ok": False,
            "translatedPrompt": "",
            "normalizedPrompt": "",
            "error": "empty_translated_prompt",
            "debug": {"raw": raw[:1200], "parsed": parsed},
        }

    return {
        "ok": bool(parsed.get("ok", True)),
        "translatedPrompt": translated,
        "normalizedPrompt": normalized_prompt,
        "error": str(parsed.get("error") or "").strip() or None,
        "debug": parsed.get("debug") if isinstance(parsed.get("debug"), dict) else {},
    }
