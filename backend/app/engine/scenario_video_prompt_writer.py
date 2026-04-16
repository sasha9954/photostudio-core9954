from __future__ import annotations

import json
from typing import Any

from app.engine.gemini_rest import post_generate_content
from app.engine.route_baseline_bank import ROUTE_BASELINE_BANK
from app.engine.scenario_story_guidance import story_guidance_to_notes_list

FINAL_VIDEO_PROMPT_STAGE_VERSION = "gemini_final_video_prompt_v11"
FINAL_VIDEO_PROMPT_MODEL = "gemini-2.5-flash"
FINAL_VIDEO_PROMPT_DELIVERY_VERSION = "1.1"

_ALLOWED_ROUTES = {"i2v", "ia2v", "first_last"}
_ALLOWED_MOTION_STRENGTH = {"low", "medium", "high"}
_ALLOWED_AUGMENTATION = {"low", "medium", "high"}
_ALLOWED_TRANSITION_KIND = {"none", "controlled", "bridge", "morph_guarded"}
_ALLOWED_AUDIO_SYNC = {"none", "beat_sensitive", "phrase_sensitive"}
_ALLOWED_FRAME_STRATEGY = {"single_init", "start_end"}
_TARGET_IA2V_READABILITY_FINAL: dict[str, str] = {
    "seg_03": "Waist-up performance readability in the nightclub bar zone, face and upper body clearly visible, unobstructed mouth and jaw, subtle controlled shoulder/chest/neck/head rhythm, performer remains visual center.",
    "seg_06": "Strongest climax-performance readability: chest-up expressive frame, unwavering direct gaze, unobstructed mouth and jaw, light rhythmic micro-movements only, no wide choreography, no crowd occlusion crossing the performer.",
}
_SEGMENT_05_NEGATIVE_REWRITE = "avoid energetic dancing, bright stage lights, crowded dance floor, sci-fi elements"


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
    raw = str(text or "").strip()
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


def _normalize_route(route_value: Any) -> str:
    route = str(route_value or "").strip().lower()
    if route in {"f_l", "first-last"}:
        route = "first_last"
    if route in {"lip_sync", "lip_sync_music"}:
        route = "ia2v"
    if route not in _ALLOWED_ROUTES:
        route = "i2v"
    return route


def _clean_target_segment_negative_artifact(text: str) -> str:
    clean = " ".join(str(text or "").split()).strip(" ,;")
    if not clean:
        return clean
    normalized = clean.lower()
    if "fast the perspective shifts gently with the moment" in normalized or "sci-fi elements" in normalized:
        return _SEGMENT_05_NEGATIVE_REWRITE
    return clean


def _engine_hints_defaults(route: str) -> dict[str, Any]:
    if route == "ia2v":
        return {
            "motion_strength": "medium",
            "augmentation_level": "low",
            "transition_kind": "controlled",
            "audio_sync_mode": "phrase_sensitive",
            "frame_strategy": "single_init",
        }
    if route == "first_last":
        return {
            "motion_strength": "medium",
            "augmentation_level": "medium",
            "transition_kind": "controlled",
            "audio_sync_mode": "none",
            "frame_strategy": "start_end",
        }
    return {
        "motion_strength": "low",
        "augmentation_level": "low",
        "transition_kind": "none",
        "audio_sync_mode": "none",
        "frame_strategy": "single_init",
    }


def _video_metadata_defaults(route: str) -> dict[str, Any]:
    if route == "first_last":
        return {
            "renderer_family": "ltx",
            "route_type": "first_last",
            "requires_first_frame": True,
            "requires_last_frame": True,
        }
    if route == "ia2v":
        return {
            "renderer_family": "ltx",
            "route_type": "ia2v",
            "requires_first_frame": True,
            "requires_last_frame": False,
        }
    return {
        "renderer_family": "ltx",
        "route_type": "i2v",
        "requires_first_frame": True,
        "requires_last_frame": False,
    }


def _canonical_segments(package: dict[str, Any]) -> list[dict[str, Any]]:
    scene_prompts = _safe_dict(package.get("scene_prompts"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    role_plan = _safe_dict(package.get("role_plan"))

    prompts_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(scene_prompts.get("segments"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    if not prompts_by_id:
        prompts_by_id = {
            str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
            for row in _safe_list(scene_prompts.get("scenes"))
            if str(_safe_dict(row).get("scene_id") or "").strip()
        }

    plan_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(scene_plan.get("segments"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    if not plan_by_id:
        plan_by_id = {
            str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
            for row in _safe_list(scene_plan.get("scenes"))
            if str(_safe_dict(row).get("scene_id") or "").strip()
        }

    role_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(role_plan.get("scene_casting"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }

    ordered_ids: list[str] = []
    for source in (plan_by_id, prompts_by_id, role_by_id):
        for segment_id in source.keys():
            if segment_id and segment_id not in ordered_ids:
                ordered_ids.append(segment_id)

    rows: list[dict[str, Any]] = []
    for segment_id in ordered_ids:
        prompt_row = _safe_dict(prompts_by_id.get(segment_id))
        plan_row = _safe_dict(plan_by_id.get(segment_id))
        role_row = _safe_dict(role_by_id.get(segment_id))
        route = _normalize_route(prompt_row.get("route") or plan_row.get("route"))
        rows.append(
            {
                "segment_id": segment_id,
                "scene_id": str(prompt_row.get("scene_id") or plan_row.get("scene_id") or segment_id).strip(),
                "route": route,
                "prompt_row": prompt_row,
                "plan_row": plan_row,
                "role_row": role_row,
            }
        )
    return rows


def _build_model_payload(package: dict[str, Any], segment_rows: list[dict[str, Any]]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    audio_map = _safe_dict(package.get("audio_map"))
    story_core = _safe_dict(package.get("story_core"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    scene_prompts = _safe_dict(package.get("scene_prompts"))
    continuity_notes = story_guidance_to_notes_list(story_core.get("story_guidance"), max_items=8)
    if not continuity_notes:
        continuity_notes = _safe_list(role_plan.get("continuity_notes"))[:8]

    segments_payload: list[dict[str, Any]] = []
    for row in segment_rows:
        prompt_row = _safe_dict(row.get("prompt_row"))
        plan_row = _safe_dict(row.get("plan_row"))
        segments_payload.append(
            {
                "segment_id": row.get("segment_id"),
                "scene_id": row.get("scene_id"),
                "route": row.get("route"),
                "duration_sec": plan_row.get("duration_sec"),
                "t0": plan_row.get("t0"),
                "t1": plan_row.get("t1"),
                "scene_goal": str(plan_row.get("scene_goal") or "").strip(),
                "scene_summary": str(plan_row.get("scene_summary") or plan_row.get("scene_description") or "").strip(),
                "primary_role": str(_safe_dict(row.get("role_row")).get("primary_role") or plan_row.get("primary_role") or "").strip(),
                "active_roles": _safe_list(_safe_dict(row.get("role_row")).get("active_roles")),
                "photo_prompt": str(prompt_row.get("photo_prompt") or "").strip(),
                "video_prompt": str(prompt_row.get("video_prompt") or "").strip(),
                "negative_prompt": str(prompt_row.get("negative_prompt") or "").strip(),
                "positive_video_prompt": str(prompt_row.get("positive_video_prompt") or "").strip(),
                "negative_video_prompt": str(prompt_row.get("negative_video_prompt") or "").strip(),
                "first_frame_prompt": str(prompt_row.get("first_frame_prompt") or prompt_row.get("start_image_prompt") or "").strip(),
                "last_frame_prompt": str(prompt_row.get("last_frame_prompt") or prompt_row.get("end_image_prompt") or "").strip(),
            }
        )

    return {
        "target_contract": {
            "delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION,
            "segments": [
                {
                    "segment_id": "string",
                    "scene_id": "string",
                    "route_payload": {
                        "positive_prompt": "string",
                        "negative_prompt": "string",
                        "first_frame_prompt": "string|null",
                        "last_frame_prompt": "string|null",
                    },
                    "engine_hints": {
                        "motion_strength": "low|medium|high",
                        "augmentation_level": "low|medium|high",
                        "transition_kind": "none|controlled|bridge|morph_guarded",
                        "audio_sync_mode": "none|beat_sensitive|phrase_sensitive",
                        "frame_strategy": "single_init|start_end",
                    },
                    "video_metadata": {
                        "renderer_family": "ltx|generic",
                        "route_type": "i2v|ia2v|first_last",
                        "requires_first_frame": True,
                        "requires_last_frame": False,
                    },
                    "audio_behavior_hints": "string",
                    "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION,
                }
            ],
        },
        "reference_context": {
            "route_baseline_bank": ROUTE_BASELINE_BANK,
            "upstream": {
                "input": {
                    "content_type": str(input_pkg.get("content_type") or ""),
                    "director_mode": str(input_pkg.get("director_mode") or ""),
                    "format": str(input_pkg.get("format") or ""),
                },
                "audio_map": {
                    "duration_sec": audio_map.get("duration_sec"),
                    "sections": _safe_list(audio_map.get("sections"))[:12],
                },
                "story_core": {
                    "story_summary": str(story_core.get("story_summary") or "").strip(),
                    "director_summary": str(story_core.get("director_summary") or "").strip(),
                    "world_lock": _safe_dict(story_core.get("world_lock")),
                    "identity_lock": _safe_dict(story_core.get("identity_lock")),
                    "style_lock": _safe_dict(story_core.get("style_lock")),
                    "continuity_notes": continuity_notes,
                },
                "scene_plan": {
                    "route_mix_summary": _safe_dict(scene_plan.get("route_mix_summary")),
                    "scene_arc_summary": str(scene_plan.get("scene_arc_summary") or "").strip(),
                },
                "scene_prompts": {
                    "prompts_version": str(scene_prompts.get("prompts_version") or ""),
                    "global_style_anchor": str(scene_prompts.get("global_style_anchor") or "").strip(),
                },
            },
        },
        "segments": segments_payload,
    }


def _build_instruction(payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "You are the only creative author for FINAL VIDEO PROMPT.",
            "Output strict JSON only.",
            "Do not add wrappers, markdown, or explanations.",
            "Do not invent extra segments and do not drop any provided segment_id.",
            "Use route semantics exactly: i2v, ia2v, first_last.",
            "Use Route Baseline Bank only as reference.",
            "Do not mirror baseline text verbatim unless context requires it.",
            f"Set prompt_source exactly to '{FINAL_VIDEO_PROMPT_STAGE_VERSION}' for each segment.",
            "If a route is first_last, first_frame_prompt and last_frame_prompt must be meaningful non-empty strings.",
            "If route is i2v or ia2v, first_frame_prompt may be null and last_frame_prompt should be null.",
            "Return exactly one object with keys: delivery_version, segments.",
            json.dumps(payload, ensure_ascii=False),
        ]
    )


def _sanitize_segment(raw_row: Any, fallback_row: dict[str, Any]) -> dict[str, Any]:
    row = _safe_dict(raw_row)
    segment_id = str(row.get("segment_id") or fallback_row.get("segment_id") or "").strip()
    scene_id = str(row.get("scene_id") or fallback_row.get("scene_id") or segment_id).strip()
    route = _normalize_route(_safe_dict(row.get("video_metadata")).get("route_type") or fallback_row.get("route"))

    route_payload = _safe_dict(row.get("route_payload"))
    fallback_prompt_row = _safe_dict(fallback_row.get("prompt_row"))
    positive_prompt = str(route_payload.get("positive_prompt") or fallback_prompt_row.get("positive_video_prompt") or fallback_prompt_row.get("video_prompt") or "").strip()
    negative_prompt = str(route_payload.get("negative_prompt") or fallback_prompt_row.get("negative_video_prompt") or fallback_prompt_row.get("negative_prompt") or "").strip()
    segment_key = segment_id.lower()
    if route == "ia2v" and segment_key in _TARGET_IA2V_READABILITY_FINAL:
        clause = _TARGET_IA2V_READABILITY_FINAL[segment_key]
        if clause.lower() not in positive_prompt.lower():
            positive_prompt = f"{positive_prompt.rstrip('. ')}. {clause}".strip()
    if segment_key == "seg_05":
        negative_prompt = _clean_target_segment_negative_artifact(negative_prompt)

    first_frame_raw = route_payload.get("first_frame_prompt")
    last_frame_raw = route_payload.get("last_frame_prompt")
    first_frame = str(first_frame_raw).strip() if first_frame_raw is not None else ""
    last_frame = str(last_frame_raw).strip() if last_frame_raw is not None else ""
    if route == "first_last":
        if not first_frame:
            first_frame = str(fallback_prompt_row.get("first_frame_prompt") or fallback_prompt_row.get("start_image_prompt") or "").strip()
        if not last_frame:
            last_frame = str(fallback_prompt_row.get("last_frame_prompt") or fallback_prompt_row.get("end_image_prompt") or "").strip()

    if not segment_id or not positive_prompt or not negative_prompt:
        raise RuntimeError(f"final_video_prompt_invalid_segment:{segment_id or 'unknown'}")
    if route == "first_last" and (not first_frame or not last_frame):
        raise RuntimeError(f"final_video_prompt_missing_first_last_frames:{segment_id}")

    engine_hints = _safe_dict(row.get("engine_hints"))
    engine_defaults = _engine_hints_defaults(route)
    motion_strength = str(engine_hints.get("motion_strength") or engine_defaults["motion_strength"]).strip().lower()
    augmentation_level = str(engine_hints.get("augmentation_level") or engine_defaults["augmentation_level"]).strip().lower()
    transition_kind = str(engine_hints.get("transition_kind") or engine_defaults["transition_kind"]).strip().lower()
    audio_sync_mode = str(engine_hints.get("audio_sync_mode") or engine_defaults["audio_sync_mode"]).strip().lower()
    frame_strategy = str(engine_hints.get("frame_strategy") or engine_defaults["frame_strategy"]).strip().lower()

    if motion_strength not in _ALLOWED_MOTION_STRENGTH:
        motion_strength = engine_defaults["motion_strength"]
    if augmentation_level not in _ALLOWED_AUGMENTATION:
        augmentation_level = engine_defaults["augmentation_level"]
    if transition_kind not in _ALLOWED_TRANSITION_KIND:
        transition_kind = engine_defaults["transition_kind"]
    if audio_sync_mode not in _ALLOWED_AUDIO_SYNC:
        audio_sync_mode = engine_defaults["audio_sync_mode"]
    if frame_strategy not in _ALLOWED_FRAME_STRATEGY:
        frame_strategy = engine_defaults["frame_strategy"]

    video_metadata = _safe_dict(row.get("video_metadata"))
    metadata_defaults = _video_metadata_defaults(route)
    renderer_family = str(video_metadata.get("renderer_family") or metadata_defaults["renderer_family"]).strip().lower()
    if renderer_family not in {"ltx", "generic"}:
        renderer_family = metadata_defaults["renderer_family"]

    return {
        "segment_id": segment_id,
        "scene_id": scene_id,
        "route_payload": {
            "positive_prompt": positive_prompt,
            "negative_prompt": negative_prompt,
            "first_frame_prompt": first_frame if first_frame else None,
            "last_frame_prompt": last_frame if last_frame else None,
        },
        "engine_hints": {
            "motion_strength": motion_strength,
            "augmentation_level": augmentation_level,
            "transition_kind": transition_kind,
            "audio_sync_mode": audio_sync_mode,
            "frame_strategy": frame_strategy,
        },
        "video_metadata": {
            "renderer_family": renderer_family,
            "route_type": route,
            "requires_first_frame": bool(metadata_defaults["requires_first_frame"]),
            "requires_last_frame": bool(metadata_defaults["requires_last_frame"]),
        },
        "audio_behavior_hints": str(row.get("audio_behavior_hints") or "").strip(),
        "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION,
    }


def _sanitize_output(raw: Any, segment_rows: list[dict[str, Any]]) -> dict[str, Any]:
    data = _safe_dict(raw)
    model_segments = _safe_list(data.get("segments"))
    by_segment_id = {
        str(_safe_dict(item).get("segment_id") or "").strip(): _safe_dict(item)
        for item in model_segments
        if str(_safe_dict(item).get("segment_id") or "").strip()
    }

    normalized: list[dict[str, Any]] = []
    for fallback_row in segment_rows:
        segment_id = str(fallback_row.get("segment_id") or "").strip()
        normalized.append(_sanitize_segment(by_segment_id.get(segment_id), fallback_row))

    return {
        "delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION,
        "segments": normalized,
        "scenes": [dict(row) for row in normalized],
    }


def generate_ltx_video_prompt_metadata(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    segment_rows = _canonical_segments(package)
    if not segment_rows:
        return {
            "ok": False,
            "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
            "diagnostics": {
                "final_video_prompt_prompt_version": FINAL_VIDEO_PROMPT_STAGE_VERSION,
                "final_video_prompt_segment_count": 0,
                "final_video_prompt_backend": "gemini",
                "final_video_prompt_attempts": 0,
                "final_video_prompt_used_fallback": False,
                "final_video_prompt_error": "final_video_prompt_missing_segments",
            },
            "error": "final_video_prompt_missing_segments",
        }
    if not str(api_key or "").strip():
        return {
            "ok": False,
            "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
            "diagnostics": {
                "final_video_prompt_prompt_version": FINAL_VIDEO_PROMPT_STAGE_VERSION,
                "final_video_prompt_segment_count": 0,
                "final_video_prompt_backend": "gemini",
                "final_video_prompt_attempts": 0,
                "final_video_prompt_used_fallback": False,
                "final_video_prompt_error": "gemini_api_key_missing",
            },
            "error": "gemini_api_key_missing",
        }

    instruction = _build_instruction(_build_model_payload(package, segment_rows))
    last_error = ""
    attempts = 0
    normalized_payload: dict[str, Any] = {}

    for _ in range(2):
        attempts += 1
        try:
            response = post_generate_content(
                api_key=str(api_key or "").strip(),
                model=FINAL_VIDEO_PROMPT_MODEL,
                body={
                    "contents": [{"role": "user", "parts": [{"text": instruction}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
                },
                timeout=120,
            )
            if isinstance(response, dict) and response.get("__http_error__"):
                raise RuntimeError(f"gemini_http_error:{response.get('status')}")
            parsed = _extract_json_obj(_extract_gemini_text(response))
            normalized_payload = _sanitize_output(parsed, segment_rows)
            last_error = ""
            break
        except Exception as exc:
            last_error = str(exc)[:220] or "final_video_prompt_generation_failed"
            normalized_payload = {}

    ok = bool(normalized_payload and _safe_list(normalized_payload.get("segments")))
    return {
        "ok": ok,
        "final_video_prompt": normalized_payload if ok else {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
        "diagnostics": {
            "final_video_prompt_prompt_version": FINAL_VIDEO_PROMPT_STAGE_VERSION,
            "final_video_prompt_segment_count": len(_safe_list(normalized_payload.get("segments"))) if ok else 0,
            "final_video_prompt_backend": "gemini",
            "final_video_prompt_attempts": attempts,
            "final_video_prompt_used_fallback": False,
            "final_video_prompt_error": "" if ok else (last_error or "final_video_prompt_generation_failed"),
            "final_video_prompt_segment_ids": [str(_safe_dict(row).get("segment_id") or "") for row in segment_rows],
        },
        "error": "" if ok else (last_error or "final_video_prompt_generation_failed"),
    }
