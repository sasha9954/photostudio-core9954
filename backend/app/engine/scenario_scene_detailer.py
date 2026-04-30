from __future__ import annotations
import json
from typing import Any
from app.engine.gemini_rest import post_generate_content

SCENE_DETAIL_PROMPT_VERSION = "scene_detail_v1"

def _safe_dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}

def _safe_list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def build_gemini_scene_detail(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    scene_plan = _safe_dict(package.get("scene_plan"))
    rows = _safe_list(scene_plan.get("scenes") or scene_plan.get("storyboard"))
    payload = {
        "audio_map": _safe_dict(package.get("audio_map")),
        "director_contract": _safe_dict(package.get("director_contract")),
        "story_core": _safe_dict(package.get("story_core")),
        "role_plan": _safe_dict(package.get("role_plan")),
        "scene_plan": {"scenes": rows},
    }
    instruction = (
        "Ты detail-expander уже утверждённых сцен. Не меняй structural fields: "
        "segment_id, route, primary_role, secondary_roles, timeline, start/end, lip_sync, order, core purpose. "
        "Верни JSON с ключами scene_detail_version, source_stage='scenes', scenes[]. "
        "Для каждой сцены сохрани locked fields и добавь scene_goal, visual_payoff, action_detail, blocking, "
        "camera{framing,angle,movement,lens_feel,focus_priority}, performance{facial_expression,body_language,energy,lip_sync_readability}, "
        "environment{setting_detail,foreground,background,atmosphere,lighting}, continuity, motion_constraints, must_show, must_avoid, prompt_bridge_notes. "
        "Сделай кинематографично, зрелищно и LTX-safe."
    )
    prompt = instruction + "\n\nINPUT:\n" + json.dumps(payload, ensure_ascii=False)
    text, _ = post_generate_content(api_key=api_key, model="gemini-2.5-pro", prompt=prompt)
    try:
        parsed = json.loads(text)
    except Exception:
        return {"ok": False, "error": "scene_detail_invalid_json", "scene_detail": {"scene_detail_version": "v1", "source_stage": "scenes", "scenes": []}}
    detail = _safe_dict(parsed)
    detail.setdefault("scene_detail_version", "v1")
    detail.setdefault("source_stage", "scenes")
    detail["scenes"] = _safe_list(detail.get("scenes"))
    return {"ok": True, "scene_detail": detail}
