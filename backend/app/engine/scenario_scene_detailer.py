from __future__ import annotations

import json
import os
from copy import deepcopy
from typing import Any

from app.engine.gemini_rest import post_generate_content
try:
    from app.engine.scenario_timeouts import get_scenario_stage_timeout
except Exception:  # pragma: no cover - fallback for older deployments
    from app.engine.scenario_stage_timeout_policy import get_scenario_stage_timeout

SCENE_DETAIL_PROMPT_VERSION = "scene_detail_v1"
SCENE_DETAIL_MODEL = os.getenv("SCENE_DETAIL_MODEL", "gemini-2.5-flash")

DETAIL_FIELDS = (
    "scene_goal",
    "visual_payoff",
    "action_detail",
    "blocking",
    "camera",
    "performance",
    "environment",
    "continuity",
    "motion_constraints",
    "must_show",
    "must_avoid",
    "prompt_bridge_notes",
)

LOCKED_FIELDS = (
    "scene_id",
    "segment_id",
    "segment_ids",
    "t0",
    "t1",
    "duration",
    "route",
    "timeline_role",
    "primary_role",
    "visual_focus_role",
    "secondary_roles",
    "lipSync",
    "requiresAudioSensitiveVideo",
    "speaker_role",
    "mouth_visible_required",
    "singing_readiness_required",
    "story_beat_type",
    "routeLocked",
    "route_lock_source",
)


def _safe_dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _safe_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _default_detail_value(key: str) -> Any:
    if key == "camera":
        return {
            "framing": "",
            "angle": "",
            "movement": "",
            "lens_feel": "",
            "focus_priority": "",
        }
    if key == "performance":
        return {
            "facial_expression": "",
            "body_language": "",
            "energy": "",
            "lip_sync_readability": "",
        }
    if key == "environment":
        return {
            "setting_detail": "",
            "foreground": "",
            "background": "",
            "atmosphere": "",
            "lighting": "",
        }
    if key == "continuity":
        return {
            "must_preserve": [],
            "identity_lock_notes": "",
            "world_lock_notes": "",
        }
    if key == "motion_constraints":
        return {
            "safe_motion": "",
            "avoid": [],
        }
    if key in {"must_show", "must_avoid"}:
        return []
    return ""


def _extract_json_payload(text: str) -> tuple[dict[str, Any] | None, str]:
    raw = str(text or "").strip()
    if not raw:
        return None, "empty"

    def _try_parse(candidate: str, mode: str) -> tuple[dict[str, Any] | None, str]:
        try:
            parsed = json.loads(candidate)
            return _safe_dict(parsed), mode
        except Exception:
            return None, mode

    if raw.startswith("{") and raw.endswith("}"):
        parsed, mode = _try_parse(raw, "raw_json")
        if parsed is not None:
            return parsed, mode

    if "```json" in raw.lower():
        lower = raw.lower()
        start = lower.find("```json")
        if start >= 0:
            after = raw[start + 7 :]
            end = after.find("```")
            if end >= 0:
                block = after[:end].strip()
                parsed, mode = _try_parse(block, "fenced_json")
                if parsed is not None:
                    return parsed, mode

    first = raw.find("{")
    last = raw.rfind("}")
    if first >= 0 and last > first:
        parsed, mode = _try_parse(raw[first : last + 1], "brace_slice")
        if parsed is not None:
            return parsed, mode

    return None, "failed"



def _extract_gemini_text(response: Any) -> str:
    if isinstance(response, str):
        return response
    if not isinstance(response, dict):
        return ""
    try:
        candidates = response.get("candidates") or []
        if not candidates:
            return ""
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        out = []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                out.append(part.get("text"))
        return "\n".join(out).strip()
    except Exception:
        return ""

def build_gemini_scene_detail(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    scene_plan = _safe_dict(package.get("scene_plan"))
    source_rows = _safe_list(scene_plan.get("scenes") or scene_plan.get("storyboard"))
    source_by_segment_id = {
        str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row)
        for row in source_rows
        if str(_safe_dict(row).get("segment_id") or "").strip()
    }

    diagnostics: dict[str, Any] = {
        "scene_detail_prompt_version": SCENE_DETAIL_PROMPT_VERSION,
        "scene_detail_model": SCENE_DETAIL_MODEL,
        "scene_detail_source_scene_count": len(source_rows),
        "scene_detail_model_scene_count": 0,
        "scene_detail_output_scene_count": 0,
        "scene_detail_segment_coverage_ok": False,
        "scene_detail_missing_segment_ids": [],
        "scene_detail_extra_segment_ids": [],
        "scene_detail_locked_fields_repaired_count": 0,
        "scene_detail_json_parse_mode": "unknown",
    }
    if not source_rows:
        return {
            "ok": False,
            "error": "scene_detail_missing_scene_plan",
            "diagnostics": diagnostics,
            "scene_detail": {"scene_detail_version": "v1", "source_stage": "scenes", "scenes": []},
        }

    payload = {
        "audio_map": _safe_dict(package.get("audio_map")),
        "director_contract": _safe_dict(package.get("director_contract")),
        "story_core": _safe_dict(package.get("story_core")),
        "role_plan": _safe_dict(package.get("role_plan")),
        "scene_plan": {"scenes": source_rows},
    }
    instruction = (
        "Ты detail-expander уже утверждённых сцен. Не меняй structural fields: "
        "segment_id, route, primary_role, secondary_roles, timeline, start/end, lip_sync, order, core purpose. "
        "Верни JSON с ключами scene_detail_version, source_stage='scenes', scenes[]. "
        "Для каждой сцены сохрани locked fields и добавь scene_goal, visual_payoff, action_detail, blocking, "
        "camera{framing,angle,movement,lens_feel,focus_priority}, performance{facial_expression,body_language,energy,lip_sync_readability}, "
        "environment{setting_detail,foreground,background,atmosphere,lighting}, continuity, motion_constraints, must_show, must_avoid, prompt_bridge_notes. "
        "Сделай сцены кинематографичными, зрелищными, продуктивными для PROMPTS и LTX-safe. Не пересказывай сухо. "
        "Для каждой сцены добавь visual payoff, читаемое действие, blocking, камеру, атмосферу, передний/задний план, эмоцию и безопасное движение. "
        "Для ia2v сцен делай performer-first: readable face, mouth, emotional vocal delivery, subtle body movement, controlled camera. "
        "Для i2v сцен делай action/world/beat-first: physical action, environment interaction, spatial storytelling, controlled readable motion. "
        "Не добавляй хаотичные орбиты, невозможную акробатику, резкие скачки камеры, лишних персонажей или новые сюжетные события, которых нет в skeleton."
    )

    prompt_text = instruction + "\n\nINPUT:\n" + json.dumps(payload, ensure_ascii=False)
    diagnostics["scene_detail_prompt_chars"] = len(prompt_text)
    diagnostics["scene_detail_payload_scene_count"] = len(source_rows)
    try:
        configured_timeout = get_scenario_stage_timeout("scene_detail")
    except Exception:
        configured_timeout = 300
    diagnostics["scene_detail_configured_timeout_sec"] = configured_timeout

    response = post_generate_content(
        api_key=str(api_key or "").strip(),
        model=SCENE_DETAIL_MODEL,
        body={
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt_text}
                    ],
                }
            ],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.2,
            },
        },
        timeout=configured_timeout,
    )

    if isinstance(response, dict) and response.get("__http_error__"):
        diagnostics["scene_detail_gemini_http_error"] = True
        diagnostics["scene_detail_gemini_http_status"] = response.get("status")
        diagnostics["scene_detail_gemini_http_text"] = str(
            response.get("text") or response.get("error") or response
        )[:2000]
        diagnostics["scene_detail_gemini_model"] = SCENE_DETAIL_MODEL
        error_text = str(response.get("text") or response.get("error") or "")[:500]
        return {
            "ok": False,
            "error": f"gemini_http_error:{response.get('status')}:{error_text}",
            "diagnostics": diagnostics,
            "scene_detail": {
                "scene_detail_version": "v1",
                "source_stage": "scenes",
                "scenes": [],
            },
        }

    text = _extract_gemini_text(response)
    diagnostics["scene_detail_raw_model_response_preview"] = str(text or "")[:500]

    parsed, parse_mode = _extract_json_payload(text)
    diagnostics["scene_detail_json_parse_mode"] = parse_mode
    if parsed is None:
        return {
            "ok": False,
            "error": "scene_detail_invalid_json",
            "diagnostics": diagnostics,
            "scene_detail": {"scene_detail_version": "v1", "source_stage": "scenes", "scenes": []},
        }

    detail = _safe_dict(parsed)
    detail.setdefault("scene_detail_version", "v1")
    detail.setdefault("source_stage", "scenes")
    model_scenes = _safe_list(detail.get("scenes"))
    diagnostics["scene_detail_model_scene_count"] = len(model_scenes)
    model_by_segment = {
        str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row)
        for row in model_scenes
        if str(_safe_dict(row).get("segment_id") or "").strip()
    }

    output_scenes: list[dict[str, Any]] = []
    missing_segment_ids: list[str] = []
    repaired_count = 0

    for source in source_rows:
        source_scene = _safe_dict(source)
        segment_id = str(source_scene.get("segment_id") or "").strip()
        model_scene = model_by_segment.get(segment_id, {})
        if not model_scene:
            missing_segment_ids.append(segment_id)

        merged = deepcopy(source_scene)
        for key in DETAIL_FIELDS:
            if key in model_scene:
                merged[key] = deepcopy(model_scene.get(key))
            elif key not in merged:
                merged[key] = _default_detail_value(key)

        for key in LOCKED_FIELDS:
            if merged.get(key) != source_scene.get(key):
                repaired_count += 1
            merged[key] = deepcopy(source_scene.get(key))
        output_scenes.append(merged)

    source_segment_ids = [str(_safe_dict(row).get("segment_id") or "").strip() for row in source_rows]
    model_segment_ids = [sid for sid in model_by_segment.keys() if sid]
    extra_segment_ids = [sid for sid in model_segment_ids if sid not in set(source_segment_ids)]

    diagnostics["scene_detail_output_scene_count"] = len(output_scenes)
    diagnostics["scene_detail_missing_segment_ids"] = [sid for sid in missing_segment_ids if sid]
    diagnostics["scene_detail_extra_segment_ids"] = extra_segment_ids
    diagnostics["scene_detail_locked_fields_repaired_count"] = repaired_count
    diagnostics["scene_detail_segment_coverage_ok"] = len(diagnostics["scene_detail_missing_segment_ids"]) == 0

    detail["scenes"] = output_scenes
    detail["missing_detail_segments"] = diagnostics["scene_detail_missing_segment_ids"]
    return {"ok": True, "scene_detail": detail, "diagnostics": diagnostics}
