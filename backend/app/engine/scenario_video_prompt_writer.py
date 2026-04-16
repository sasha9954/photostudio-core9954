from __future__ import annotations

import json
import re
from typing import Any

from app.engine.gemini_rest import post_generate_content
from app.engine.route_baseline_bank import ROUTE_BASELINE_BANK_VERSION, get_route_baseline_bank

FINAL_VIDEO_PROMPT_STAGE_VERSION = "gemini_final_video_prompt_v1_1"
FINAL_VIDEO_PROMPT_DELIVERY_VERSION = "1.1"
FINAL_VIDEO_PROMPT_MODEL = "gemini-2.5-flash"

FINAL_VIDEO_PROMPT_EMPTY = "FINAL_VIDEO_PROMPT_EMPTY"
FINAL_VIDEO_PROMPT_SCHEMA_INVALID = "FINAL_VIDEO_PROMPT_SCHEMA_INVALID"
FINAL_VIDEO_PROMPT_SEGMENT_ID_MISMATCH = "FINAL_VIDEO_PROMPT_SEGMENT_ID_MISMATCH"
FINAL_VIDEO_PROMPT_ROUTE_MISMATCH = "FINAL_VIDEO_PROMPT_ROUTE_MISMATCH"
FINAL_VIDEO_PROMPT_UPSTREAM_ROUTE_MISSING = "FINAL_VIDEO_PROMPT_UPSTREAM_ROUTE_MISSING"
FINAL_VIDEO_PROMPT_MISSING_ENGINE_HINTS = "FINAL_VIDEO_PROMPT_MISSING_ENGINE_HINTS"
FINAL_VIDEO_PROMPT_MISSING_FRAME_PROMPTS = "FINAL_VIDEO_PROMPT_MISSING_FRAME_PROMPTS"
FINAL_VIDEO_PROMPT_WORKFLOW_CONFLICT = "FINAL_VIDEO_PROMPT_WORKFLOW_CONFLICT"
FINAL_VIDEO_PROMPT_STORY_REAUTHORING = "FINAL_VIDEO_PROMPT_STORY_REAUTHORING"
FINAL_VIDEO_PROMPT_RENDERER_FAMILY_CONFLICT = "FINAL_VIDEO_PROMPT_RENDERER_FAMILY_CONFLICT"

_ALLOWED_ROUTE_TYPES = {"i2v", "ia2v", "first_last"}
_ALLOWED_RENDERER_FAMILIES = {"ltx", "svd", "runway", "generic"}
_ALLOWED_MOTION_STRENGTH = {"low", "medium", "high"}
_ALLOWED_AUGMENTATION_LEVEL = {"low", "medium", "high"}
_ALLOWED_TRANSITION_KIND = {"none", "controlled", "bridge", "morph_guarded"}
_ALLOWED_AUDIO_SYNC_MODE = {"none", "beat_sensitive", "phrase_sensitive"}
_ALLOWED_FRAME_STRATEGY = {"single_init", "start_end"}

_PROMPT_NOTES_LEAKAGE_TOKENS = {
    "prompt_notes",
    "route_reason",
    "watchability_role",
    "motion_intent",
    "camera_pattern",
    "do_not_include",
}

_CANON_SYSTEM = """
You author the canonical FINAL VIDEO PROMPT stage for PhotoStudio.
Hard rules:
- You are the ONLY creative author.
- Backend/frontend are NOT authors.
- Immutable upstream inputs: do NOT alter route, timing, role assignment, or narrative meaning.
- No silent repair. If upstream is insufficient, preserve intent conservatively and stay within provided facts.
- Return strict JSON only.
- Do not include markdown, comments, prose outside JSON.

Output schema:
{
  "delivery_version": "1.1",
  "segments": [
    {
      "segment_id": "string",
      "route_payload": {
        "positive_prompt": "string|null",
        "negative_prompt": "string|null",
        "first_frame_prompt": "string|null",
        "last_frame_prompt": "string|null"
      },
      "engine_hints": {
        "motion_strength": "low|medium|high",
        "augmentation_level": "low|medium|high",
        "transition_kind": "none|controlled|bridge|morph_guarded",
        "audio_sync_mode": "none|beat_sensitive|phrase_sensitive",
        "frame_strategy": "single_init|start_end"
      },
      "video_metadata": {
        "renderer_family": "ltx|svd|runway|generic",
        "route_type": "i2v|ia2v|first_last",
        "requires_first_frame": true,
        "requires_last_frame": false
      },
      "audio_behavior_hints": "string"
    }
  ]
}

Route rules:
- i2v: one clear body action + one clear camera action. Grounded real-time motion. first/last frame prompts must be null.
- ia2v: performance-first, readable face/mouth, controlled lip-sync articulation, mostly in place no walking, audio_sync_mode typically phrase_sensitive. first/last frame prompts must be null.
- first_last: Anchor A -> Event -> Anchor B. Include first_frame_prompt + last_frame_prompt. frame_strategy must be start_end. transition_kind usually controlled/bridge.

Do not emit extra keys outside this contract.
""".strip()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


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


def _extract_json_obj(text: str) -> Any:
    raw = _clean_text(text)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        first_obj, last_obj = raw.find("{"), raw.rfind("}")
        if first_obj >= 0 and last_obj > first_obj:
            try:
                return json.loads(raw[first_obj : last_obj + 1])
            except Exception:
                return {}
    return {}


def _normalize_route(value: Any) -> str:
    route = _clean_text(value).lower()
    return route if route in _ALLOWED_ROUTE_TYPES else ""


def _segment_id(row: dict[str, Any], idx: int) -> str:
    return _clean_text(row.get("segment_id") or row.get("scene_id") or row.get("id") or f"seg_{idx + 1}")


def _build_upstream_segments(package: dict[str, Any]) -> list[dict[str, Any]]:
    scene_prompts = _safe_dict(package.get("scene_prompts"))
    src_segments = _safe_list(scene_prompts.get("segments"))
    if not src_segments:
        src_segments = _safe_list(scene_prompts.get("scenes"))

    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(src_segments):
        row = _safe_dict(raw)
        segment_id = _segment_id(row, idx)
        if not segment_id:
            continue
        route = _normalize_route(row.get("route") or row.get("video_generation_route") or row.get("route_type"))
        if not route:
            raise ValueError(FINAL_VIDEO_PROMPT_UPSTREAM_ROUTE_MISSING)
        out.append(
            {
                "segment_id": segment_id,
                "route": route,
                "t0": row.get("t0"),
                "t1": row.get("t1"),
                "duration_sec": row.get("duration_sec"),
                "scene_goal": _clean_text(row.get("scene_goal") or row.get("summary")),
                "video_prompt": _clean_text(row.get("video_prompt") or row.get("positive_video_prompt")),
                "negative_video_prompt": _clean_text(row.get("negative_video_prompt") or row.get("negative_prompt")),
                "first_frame_prompt": _clean_text(row.get("first_frame_prompt") or row.get("firstFramePrompt")),
                "last_frame_prompt": _clean_text(row.get("last_frame_prompt") or row.get("lastFramePrompt")),
                "prompt_notes": _safe_dict(row.get("prompt_notes")),
            }
        )
    return out


def _build_authoring_prompt(*, upstream_segments: list[dict[str, Any]], package: dict[str, Any], feedback: str = "") -> str:
    story_core = _safe_dict(package.get("story_core"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    audio_map = _safe_dict(package.get("audio_map"))
    capability = _safe_dict(package.get("video_capability_canon") or package.get("capability_canon"))
    bank = get_route_baseline_bank()

    payload = {
        "delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION,
        "allowed_inputs": {
            "scene_prompts_segments": upstream_segments,
            "scene_plan_summary": {
                "route_mix_summary": _safe_dict(scene_plan.get("route_mix_summary")),
                "scene_count": len(_safe_list(scene_plan.get("scenes"))),
            },
            "role_plan_summary": {
                "scene_casting_count": len(_safe_list(role_plan.get("scene_casting"))),
                "roster_count": len(_safe_list(role_plan.get("roster"))),
            },
            "audio_map_summary": {
                "duration_sec": audio_map.get("duration_sec"),
                "sections_count": len(_safe_list(audio_map.get("sections"))),
            },
            "story_core_summary": {
                "story_summary": _clean_text(story_core.get("story_summary")),
                "director_summary": _clean_text(story_core.get("director_summary")),
            },
            "capability_canon": capability,
            "route_baseline_bank": {
                "version": ROUTE_BASELINE_BANK_VERSION,
                "routes": bank,
            },
        },
    }
    lines = [_CANON_SYSTEM, "", "Input payload:", json.dumps(payload, ensure_ascii=False)]
    if feedback:
        lines.extend(["", "Validation feedback for retry (must fix):", feedback])
    lines.extend(["", "Return strict JSON only."])
    return "\n".join(lines)


def _validate_segment_row(
    row: dict[str, Any],
    upstream_by_segment: dict[str, dict[str, Any]],
) -> list[str]:
    errors: list[str] = []
    segment_id = _clean_text(row.get("segment_id"))
    if not segment_id or segment_id not in upstream_by_segment:
        return [FINAL_VIDEO_PROMPT_SEGMENT_ID_MISMATCH]

    upstream = upstream_by_segment[segment_id]
    route_payload = _safe_dict(row.get("route_payload"))
    engine_hints = _safe_dict(row.get("engine_hints"))
    video_metadata = _safe_dict(row.get("video_metadata"))

    route_type = _normalize_route(video_metadata.get("route_type"))
    upstream_route = _normalize_route(upstream.get("route"))
    if not route_type or route_type != upstream_route:
        errors.append(FINAL_VIDEO_PROMPT_ROUTE_MISMATCH)

    if not engine_hints:
        errors.append(FINAL_VIDEO_PROMPT_MISSING_ENGINE_HINTS)
    else:
        if _clean_text(engine_hints.get("motion_strength")) not in _ALLOWED_MOTION_STRENGTH:
            errors.append(FINAL_VIDEO_PROMPT_SCHEMA_INVALID)
        if _clean_text(engine_hints.get("augmentation_level")) not in _ALLOWED_AUGMENTATION_LEVEL:
            errors.append(FINAL_VIDEO_PROMPT_SCHEMA_INVALID)
        if _clean_text(engine_hints.get("transition_kind")) not in _ALLOWED_TRANSITION_KIND:
            errors.append(FINAL_VIDEO_PROMPT_SCHEMA_INVALID)
        if _clean_text(engine_hints.get("audio_sync_mode")) not in _ALLOWED_AUDIO_SYNC_MODE:
            errors.append(FINAL_VIDEO_PROMPT_SCHEMA_INVALID)
        if _clean_text(engine_hints.get("frame_strategy")) not in _ALLOWED_FRAME_STRATEGY:
            errors.append(FINAL_VIDEO_PROMPT_WORKFLOW_CONFLICT)

    renderer_family = _clean_text(video_metadata.get("renderer_family"))
    if renderer_family not in _ALLOWED_RENDERER_FAMILIES:
        errors.append(FINAL_VIDEO_PROMPT_RENDERER_FAMILY_CONFLICT)

    requires_first_frame = bool(video_metadata.get("requires_first_frame"))
    requires_last_frame = bool(video_metadata.get("requires_last_frame"))
    first_frame_prompt = route_payload.get("first_frame_prompt")
    last_frame_prompt = route_payload.get("last_frame_prompt")

    if route_type == "first_last":
        if _clean_text(first_frame_prompt) == "" or _clean_text(last_frame_prompt) == "":
            errors.append(FINAL_VIDEO_PROMPT_MISSING_FRAME_PROMPTS)
        if _clean_text(engine_hints.get("frame_strategy")) != "start_end":
            errors.append(FINAL_VIDEO_PROMPT_WORKFLOW_CONFLICT)
        if not requires_first_frame:
            errors.append(FINAL_VIDEO_PROMPT_WORKFLOW_CONFLICT)
    else:
        if first_frame_prompt is not None or last_frame_prompt is not None:
            errors.append(FINAL_VIDEO_PROMPT_MISSING_FRAME_PROMPTS)
        if _clean_text(engine_hints.get("frame_strategy")) != "single_init":
            errors.append(FINAL_VIDEO_PROMPT_WORKFLOW_CONFLICT)
        if requires_first_frame or requires_last_frame:
            errors.append(FINAL_VIDEO_PROMPT_WORKFLOW_CONFLICT)

    positive_prompt = _clean_text(route_payload.get("positive_prompt"))
    negative_prompt = _clean_text(route_payload.get("negative_prompt"))
    if not positive_prompt or not negative_prompt:
        errors.append(FINAL_VIDEO_PROMPT_SCHEMA_INVALID)

    merged_text = f"{positive_prompt}\n{negative_prompt}".lower()
    if any(token in merged_text for token in _PROMPT_NOTES_LEAKAGE_TOKENS):
        errors.append(FINAL_VIDEO_PROMPT_STORY_REAUTHORING)

    upstream_goal = _clean_text(upstream.get("scene_goal") or upstream.get("video_prompt")).lower()
    if upstream_goal and len(upstream_goal) >= 16 and upstream_goal[:16] not in merged_text and len(positive_prompt) < 24:
        errors.append(FINAL_VIDEO_PROMPT_STORY_REAUTHORING)

    return errors


def _validate_payload(payload: dict[str, Any], upstream_segments: list[dict[str, Any]]) -> tuple[bool, list[str], dict[str, list[str]]]:
    if not payload:
        return False, [FINAL_VIDEO_PROMPT_EMPTY], {}
    if _clean_text(payload.get("delivery_version")) != FINAL_VIDEO_PROMPT_DELIVERY_VERSION:
        return False, [FINAL_VIDEO_PROMPT_SCHEMA_INVALID], {}

    segments = _safe_list(payload.get("segments"))
    if not segments:
        return False, [FINAL_VIDEO_PROMPT_EMPTY], {}

    upstream_by_segment = {row["segment_id"]: row for row in upstream_segments if row.get("segment_id")}
    if len(segments) != len(upstream_by_segment):
        return False, [FINAL_VIDEO_PROMPT_SEGMENT_ID_MISMATCH], {}

    errors: list[str] = []
    by_segment: dict[str, list[str]] = {}
    seen: set[str] = set()
    for raw in segments:
        row = _safe_dict(raw)
        segment_id = _clean_text(row.get("segment_id"))
        if not segment_id or segment_id in seen:
            errors.append(FINAL_VIDEO_PROMPT_SEGMENT_ID_MISMATCH)
            continue
        seen.add(segment_id)
        row_errors = _validate_segment_row(row, upstream_by_segment)
        if row_errors:
            by_segment[segment_id] = sorted(set(row_errors))
            errors.extend(row_errors)

    if seen != set(upstream_by_segment.keys()):
        errors.append(FINAL_VIDEO_PROMPT_SEGMENT_ID_MISMATCH)

    unique_errors = sorted(set(errors))
    return not unique_errors, unique_errors, by_segment


def _apply_deprecated_bridge(payload: dict[str, Any]) -> dict[str, Any]:
    segments = []
    scenes_alias = []
    for raw in _safe_list(payload.get("segments")):
        row = _safe_dict(raw)
        route_payload = _safe_dict(row.get("route_payload"))
        video_metadata = {**_safe_dict(row.get("video_metadata"))}
        # Deprecated deterministic aliases (no creative rewrite).
        video_metadata["ltx_positive"] = route_payload.get("positive_prompt")
        video_metadata["ltx_negative"] = route_payload.get("negative_prompt")
        bridged = {
            **row,
            "video_metadata": video_metadata,
            "scene_id": _clean_text(row.get("segment_id")),
            "first_frame_prompt": route_payload.get("first_frame_prompt"),
            "last_frame_prompt": route_payload.get("last_frame_prompt"),
            "deprecated_bridge_used": True,
        }
        segments.append(bridged)
        scenes_alias.append(bridged)
    return {
        "delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION,
        "segments": segments,
        "scenes": scenes_alias,
        "deprecated_bridge": {
            "enabled": True,
            "alias_type": "deterministic_field_alias_only",
            "notes": [
                "video_metadata.ltx_positive <- route_payload.positive_prompt",
                "video_metadata.ltx_negative <- route_payload.negative_prompt",
                "first_frame_prompt/last_frame_prompt mirrored for first_last",
            ],
        },
    }


def generate_ltx_video_prompt_metadata(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    upstream_segments: list[dict[str, Any]] = []
    upstream_error = ""
    try:
        upstream_segments = _build_upstream_segments(package)
    except Exception as exc:
        upstream_error = _clean_text(exc) or FINAL_VIDEO_PROMPT_UPSTREAM_ROUTE_MISSING

    diagnostics: dict[str, Any] = {
        "final_video_prompt_backend": "gemini",
        "final_video_prompt_prompt_version": FINAL_VIDEO_PROMPT_STAGE_VERSION,
        "final_video_prompt_delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION,
        "final_video_prompt_scene_count": 0,
        "final_video_prompt_segment_count": len(upstream_segments),
        "final_video_prompt_retry_count": 0,
        "final_video_prompt_errors": [],
        "final_video_prompt_validation_errors_by_segment": {},
        "final_video_prompt_route_baseline_bank_version": ROUTE_BASELINE_BANK_VERSION,
    }
    if upstream_error:
        diagnostics["final_video_prompt_errors"] = [upstream_error]
        return {
            "ok": False,
            "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
            "diagnostics": diagnostics,
            "error": upstream_error,
        }

    if not upstream_segments:
        diagnostics["final_video_prompt_errors"] = [FINAL_VIDEO_PROMPT_EMPTY]
        return {"ok": False, "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []}, "diagnostics": diagnostics, "error": FINAL_VIDEO_PROMPT_EMPTY}

    if not _clean_text(api_key):
        diagnostics["final_video_prompt_errors"] = ["gemini_api_key_missing"]
        return {"ok": False, "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []}, "diagnostics": diagnostics, "error": "gemini_api_key_missing"}

    attempts = 0
    feedback = ""
    last_error = FINAL_VIDEO_PROMPT_EMPTY
    parsed: dict[str, Any] = {}

    while attempts < 2:
        attempts += 1
        diagnostics["final_video_prompt_retry_count"] = attempts - 1
        prompt = _build_authoring_prompt(upstream_segments=upstream_segments, package=package, feedback=feedback)
        try:
            response = post_generate_content(
                api_key=_clean_text(api_key),
                model=FINAL_VIDEO_PROMPT_MODEL,
                body={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
                },
                timeout=120,
            )
            if isinstance(response, dict) and response.get("__http_error__"):
                raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")
            parsed = _safe_dict(_extract_json_obj(_extract_gemini_text(response)))
        except Exception as exc:
            last_error = _clean_text(exc) or "gemini_request_failed"
            if attempts >= 2:
                diagnostics["final_video_prompt_errors"] = [last_error]
                return {
                    "ok": False,
                    "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
                    "diagnostics": diagnostics,
                    "error": last_error,
                }
            feedback = f"Previous attempt failed with request error: {last_error}. Return valid strict JSON schema."
            continue

        valid, errors, by_segment = _validate_payload(parsed, upstream_segments)
        diagnostics["final_video_prompt_errors"] = errors
        diagnostics["final_video_prompt_validation_errors_by_segment"] = by_segment
        if valid:
            bridged = _apply_deprecated_bridge(parsed)
            diagnostics["final_video_prompt_scene_count"] = len(_safe_list(bridged.get("segments")))
            diagnostics["final_video_prompt_deprecated_bridge_used"] = True
            return {"ok": True, "final_video_prompt": bridged, "diagnostics": diagnostics, "error": ""}

        last_error = ",".join(errors) or FINAL_VIDEO_PROMPT_SCHEMA_INVALID
        if attempts >= 2:
            return {
                "ok": False,
                "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
                "diagnostics": diagnostics,
                "error": last_error,
            }
        feedback = json.dumps({"errors": errors, "errors_by_segment": by_segment}, ensure_ascii=False)

    return {
        "ok": False,
        "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
        "diagnostics": diagnostics,
        "error": last_error,
    }
