from __future__ import annotations

import json
import re
from typing import Any

from app.engine.gemini_rest import post_generate_content
from app.engine.scenario_story_guidance import story_guidance_to_notes_list

FINAL_VIDEO_PROMPT_STAGE_VERSION = "gemini_final_video_prompt_v1"
FINAL_VIDEO_PROMPT_MODEL = "gemini-2.5-flash"

_ALLOWED_MOTION_TAGS = {
    "slow_walk",
    "restrained_walk",
    "purposeful_walk",
    "walk_push_in",
    "push_in",
    "pull_back",
    "side_tracking",
    "head_turn",
    "attention_shift",
    "lateral_reveal",
}

_ALLOWED_CAMERA_TAGS = {
    "locked",
    "push_in",
    "pull_back",
    "side_tracking",
    "head_turn",
    "attention_shift",
    "lateral_reveal",
}

_NEGATIVE_FALLBACK = (
    "identity drift, broken anatomy, camera shake, surreal motion, background morphing"
)
_FINAL_VIDEO_METADATA_ALLOWED_ROUTES = {"i2v"}


_CANON_SYSTEM_INSTRUCTION = """
You are a Technical Video Director for LTX image-to-video generation.
Write compact prompts only. No literary prose, no abstract cinematic poetry.
Use formula: [Subject], [Action], [Environment], [Camera].
Exactly one body action and one camera move. Max one emotional accent.
No conflicting motion instructions.
Safe motion families: restrained walk, purposeful walk, push-in, pull-back, side tracking, head turn, attention shift, lateral reveal.
Unsafe defaults forbidden: strong camera arc, orbit, acrobatics, spin choreography, complex multi-action motion.
Return strict JSON only with keys:
ltx_positive, ltx_negative, motion_tag, camera_tag, prompt_source.
Tags must be snake_case.
""".strip()


_FEW_SHOT = [
    {
        "input": "person crossing wet alley, low tension, short scene",
        "output": {
            "ltx_positive": "Person in dark coat walking with restrained pace, wet alley with neon reflections, grounded real-time motion, gentle side tracking camera.",
            "ltx_negative": "identity drift, broken anatomy, camera shake, surreal motion, background morphing",
            "motion_tag": "slow_walk",
            "camera_tag": "side_tracking",
            "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION,
        },
    },
    {
        "input": "portrait beat, emotional focus",
        "output": {
            "ltx_positive": "Single subject holding still with a small head turn, realistic interior background, controlled emotional accent, gentle push-in camera.",
            "ltx_negative": "identity drift, face distortion, camera jump, surreal motion",
            "motion_tag": "head_turn",
            "camera_tag": "push_in",
            "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION,
        },
    },
    {
        "input": "bad: subject runs, spins, jumps while camera orbits fast",
        "output": {
            "ltx_positive": "Subject makes one purposeful forward step, stable urban background, grounded real-time motion, controlled pull-back camera.",
            "ltx_negative": "orbit camera, acrobatics, spin choreography, unstable anatomy, camera shake",
            "motion_tag": "purposeful_walk",
            "camera_tag": "pull_back",
            "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION,
        },
    },
]


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


def _to_snake_tag(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text


def _fallback_metadata(scene: dict[str, Any]) -> dict[str, str]:
    positive = str(
        scene.get("video_prompt")
        or scene.get("videoPromptEn")
        or scene.get("videoPrompt")
        or scene.get("summary")
        or ""
    ).strip()
    if not positive:
        positive = "Single subject with restrained natural motion, stable environment, controlled camera movement."
    return {
        "ltx_positive": positive,
        "ltx_negative": _NEGATIVE_FALLBACK,
        "motion_tag": "restrained_walk",
        "camera_tag": "locked",
        "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION,
    }


def _sanitize_metadata(raw: Any, scene: dict[str, Any]) -> dict[str, str]:
    row = _safe_dict(raw)
    fallback = _fallback_metadata(scene)
    motion_tag = _to_snake_tag(row.get("motion_tag"))
    camera_tag = _to_snake_tag(row.get("camera_tag"))
    if motion_tag not in _ALLOWED_MOTION_TAGS:
        motion_tag = fallback["motion_tag"]
    if camera_tag not in _ALLOWED_CAMERA_TAGS:
        camera_tag = fallback["camera_tag"]

    ltx_positive = str(row.get("ltx_positive") or "").strip() or fallback["ltx_positive"]
    ltx_negative = str(row.get("ltx_negative") or "").strip() or fallback["ltx_negative"]

    return {
        "ltx_positive": ltx_positive,
        "ltx_negative": ltx_negative,
        "motion_tag": motion_tag,
        "camera_tag": camera_tag,
        "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION,
    }


def _is_video_metadata_route_allowed(scene: dict[str, Any]) -> bool:
    route = str(scene.get("route") or "").strip().lower()
    return route in _FINAL_VIDEO_METADATA_ALLOWED_ROUTES


def _build_scene_input_payload(scene: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    continuity = {
        "world_continuity": _safe_dict(story_core.get("world_lock")) or _safe_dict(role_plan.get("world_continuity")),
        "continuity_notes": story_guidance_to_notes_list(story_core.get("story_guidance"), max_items=8) or _safe_list(role_plan.get("continuity_notes"))[:8],
        "route_mix_summary": _safe_dict(scene_plan.get("route_mix_summary")),
    }
    return {
        "scene_id": str(scene.get("scene_id") or "").strip(),
        "route": str(scene.get("route") or "").strip(),
        "duration_sec": scene.get("duration_sec"),
        "scene_intent": str(
            scene.get("scene_goal")
            or scene.get("scene_summary")
            or scene.get("summary")
            or scene.get("video_prompt")
            or ""
        ).strip(),
        "scene_package": {
            "video_prompt": str(scene.get("video_prompt") or "").strip(),
            "photo_prompt": str(scene.get("photo_prompt") or "").strip(),
            "negative_video_prompt": str(scene.get("negative_video_prompt") or "").strip(),
            "primary_role": str(scene.get("primary_role") or "").strip(),
            "active_roles": _safe_list(scene.get("active_roles")),
            "route": str(scene.get("route") or "").strip(),
        },
        "refs": {
            "selected_refs": _safe_dict(input_pkg.get("selected_refs")),
            "refs_by_role": _safe_dict(input_pkg.get("refs_by_role")),
        },
        "continuity": continuity,
    }


def _build_prompt(scene_payload: dict[str, Any]) -> str:
    few_shot_lines = []
    for example in _FEW_SHOT:
        few_shot_lines.append(f"INPUT: {example['input']}")
        few_shot_lines.append(f"OUTPUT: {json.dumps(example['output'], ensure_ascii=False)}")
    return "\n".join(
        [
            _CANON_SYSTEM_INSTRUCTION,
            "",
            "Few-shot calibration:",
            *few_shot_lines,
            "",
            "Now write JSON for this scene:",
            json.dumps(scene_payload, ensure_ascii=False),
            "Return JSON only.",
        ]
    )


def generate_ltx_video_prompt_metadata(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    scene_prompts = _safe_dict(package.get("scene_prompts"))
    scenes = [
        _safe_dict(scene)
        for scene in _safe_list(scene_prompts.get("scenes"))
        if str(_safe_dict(scene).get("scene_id") or "").strip()
    ]
    out_scenes: list[dict[str, Any]] = []
    debug_rows: list[dict[str, Any]] = []

    for scene in scenes:
        scene_payload = _build_scene_input_payload(scene, package)
        fallback = _fallback_metadata(scene)
        route_allowed = _is_video_metadata_route_allowed(scene)
        metadata: dict[str, str] = {}
        if route_allowed:
            metadata = fallback
        if route_allowed and str(api_key or "").strip():
            try:
                prompt = _build_prompt(scene_payload)
                response = post_generate_content(
                    api_key=str(api_key or "").strip(),
                    model=FINAL_VIDEO_PROMPT_MODEL,
                    body={
                        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                        "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
                    },
                    timeout=90,
                )
                if isinstance(response, dict) and response.get("__http_error__"):
                    raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")
                parsed = _extract_json_obj(_extract_gemini_text(response))
                metadata = _sanitize_metadata(parsed, scene)
            except Exception:
                metadata = fallback

        next_scene = dict(scene)
        next_scene["video_metadata"] = metadata
        out_scenes.append(next_scene)
        debug_rows.append(
            {
                "sceneId": str(scene.get("scene_id") or "").strip(),
                "route": str(scene.get("route") or "").strip(),
                "metadata_route_allowed": route_allowed,
                "prompt_source": str(metadata.get("prompt_source") or ""),
                "ltx_positive_preview": str(metadata.get("ltx_positive") or "")[:220],
                "ltx_negative_preview": str(metadata.get("ltx_negative") or "")[:220],
                "motion_tag": str(metadata.get("motion_tag") or ""),
                "camera_tag": str(metadata.get("camera_tag") or ""),
            }
        )

    return {
        "ok": bool(out_scenes),
        "final_video_prompt": {"scenes": out_scenes, "prompt_source": FINAL_VIDEO_PROMPT_STAGE_VERSION},
        "diagnostics": {
            "final_video_prompt_prompt_version": FINAL_VIDEO_PROMPT_STAGE_VERSION,
            "final_video_prompt_scene_count": len(out_scenes),
            "final_video_prompt_backend": "gemini",
            "final_video_prompt_used_fallback": not bool(str(api_key or "").strip()),
            "final_video_prompt_debug_rows": debug_rows,
        },
        "error": "" if out_scenes else "final_video_prompt_empty",
    }
