from __future__ import annotations

import json
from typing import Any

from app.engine.gemini_rest import post_generate_content

SCENE_PROMPTS_PROMPT_VERSION = "scene_prompts_v1"
ALLOWED_ROUTES = {"i2v", "ia2v", "first_last"}

_GLOBAL_NEGATIVE_PROMPT = (
    "no anatomy drift, no identity drift, no outfit drift, no lighting/world drift, "
    "no abrupt body twists, no chaotic hand motion, no unstable legs, no unnatural spin, "
    "no camera chaos, no surreal deformation, no extra limbs, no face or mouth distortion, "
    "no background teleportation"
)

_GLOBAL_PROMPT_RULES = [
    "Preserve hero identity, world anchor, style family, and realistic lighting continuity across all scenes.",
    "Keep prompts short, production-friendly, and route-aware; one clear action + one clear camera idea per video prompt.",
    "Respect wardrobe continuity and only reveal special dress in explicitly private/final progression scenes.",
    "Enforce LTX-safe motion and anatomy-safe constraints for all routes.",
]


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _round3(value: Any) -> float:
    try:
        return round(float(value), 3)
    except Exception:
        return 0.0


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


def _build_scene_windows(audio_map: dict[str, Any]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for idx, row_raw in enumerate(_safe_list(audio_map.get("scene_candidate_windows")), start=1):
        row = _safe_dict(row_raw)
        t0 = _round3(row.get("t0"))
        t1 = _round3(row.get("t1"))
        if t1 <= t0:
            continue
        windows.append(
            {
                "scene_id": str(row.get("id") or f"sc_{idx}"),
                "t0": t0,
                "t1": t1,
                "duration_sec": _round3(row.get("duration_sec") or (t1 - t0)),
                "phrase_text": str(row.get("phrase_text") or "").strip(),
                "scene_function": str(row.get("scene_function") or "").strip(),
                "energy": str(row.get("energy") or "").strip(),
            }
        )
    return windows


def _build_scene_role_lookup(role_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(role_plan.get("scene_roles")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id:
            lookup[scene_id] = row
    return lookup


def _build_compact_context(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan = _safe_dict(package.get("scene_plan"))

    scene_windows = _build_scene_windows(audio_map)
    role_lookup = _build_scene_role_lookup(role_plan)

    compact_context = {
        "mode": "clip",
        "content_type": str(input_pkg.get("content_type") or ""),
        "format": str(input_pkg.get("format") or ""),
        "director_note": str(input_pkg.get("director_note") or input_pkg.get("note") or "")[:1200],
        "story_core": {
            "story_summary": str(story_core.get("story_summary") or "")[:1200],
            "opening_anchor": str(story_core.get("opening_anchor") or "")[:600],
            "ending_callback_rule": str(story_core.get("ending_callback_rule") or "")[:600],
            "global_arc": str(story_core.get("global_arc") or "")[:600],
            "identity_lock_summary": str(_safe_dict(story_core.get("identity_lock")).get("summary") or "")[:600],
            "world_lock_summary": str(_safe_dict(story_core.get("world_lock")).get("summary") or "")[:600],
            "style_lock_summary": str(_safe_dict(story_core.get("style_lock")).get("summary") or "")[:600],
        },
        "audio_map": {
            "scene_windows": scene_windows,
            "sections": _safe_list(audio_map.get("sections")),
            "cut_policy": _safe_dict(audio_map.get("cut_policy")),
        },
        "role_plan": {
            "world_continuity": _safe_dict(role_plan.get("world_continuity")),
            "scene_roles": [
                {
                    "scene_id": sid,
                    "primary_role": str(_safe_dict(role).get("primary_role") or ""),
                    "scene_presence_mode": str(_safe_dict(role).get("scene_presence_mode") or ""),
                    "performance_focus": bool(_safe_dict(role).get("performance_focus")),
                }
                for sid, role in role_lookup.items()
            ],
            "continuity_notes": _safe_list(role_plan.get("continuity_notes")),
        },
        "scene_plan": {
            "route_mix_summary": _safe_dict(scene_plan.get("route_mix_summary")),
            "scenes": [
                {
                    "scene_id": str(_safe_dict(row).get("scene_id") or ""),
                    "t0": _round3(_safe_dict(row).get("t0")),
                    "t1": _round3(_safe_dict(row).get("t1")),
                    "duration_sec": _round3(_safe_dict(row).get("duration_sec")),
                    "scene_function": str(_safe_dict(row).get("scene_function") or ""),
                    "route": str(_safe_dict(row).get("route") or ""),
                    "route_reason": str(_safe_dict(row).get("route_reason") or ""),
                    "emotional_intent": str(_safe_dict(row).get("emotional_intent") or ""),
                    "motion_intent": str(_safe_dict(row).get("motion_intent") or ""),
                    "watchability_role": str(_safe_dict(row).get("watchability_role") or ""),
                }
                for row in _safe_list(scene_plan.get("scenes"))
            ],
        },
        "prompt_policy": {
            "ltx_safe_motion": True,
            "realism_required": True,
            "world_continuity_required": True,
            "identity_continuity_required": True,
        },
    }

    aux = {
        "scene_rows": _safe_list(scene_plan.get("scenes")),
        "role_lookup": role_lookup,
        "story_core": story_core,
        "world_continuity": _safe_dict(role_plan.get("world_continuity")),
    }
    return compact_context, aux


def _build_prompt(context: dict[str, Any]) -> str:
    return (
        "You are SCENE PROMPTS stage for scenario pipeline.\\n"
        "Return STRICT JSON only. No markdown.\\n"
        "MODE is clip only.\\n"
        "Task: build planning-to-generation bridge prompts for later storyboard/render stages.\\n"
        "Do NOT produce render payloads or API calls.\\n"
        "For each scene from scene_plan, write route-aware photo_prompt and video_prompt with compact production language.\\n"
        "Preserve identity/world/style continuity and realism.\\n"
        "Prompt text must be short, usable, and not overloaded.\\n"
        "Video prompts must be LTX-safe and anatomy-safe.\\n"
        "Route rules:\\n"
        "- i2v: one observable action, simple/smooth camera, safe body motion.\\n"
        "- ia2v: AUDIO-SLICE-DRIVEN singing/performance for local scene audio slice; readable face and mouth; emotionally synced vocal delivery; upper-body emphasis; stable base; smooth camera; no abrupt choreography/spins/unstable legs.\\n"
        "- first_last: one meaningful A->B progression with plausible continuity; no teleport/chaotic multi-transform.\\n"
        "Always include compact negative_prompt with safety constraints.\\n"
        "Set prompt_notes.audio_driven=true for ia2v scenes.\\n"
        "Return EXACT contract keys:\\n"
        "{\\n"
        '  \"plan_version\": \"scene_prompts_v1\",\\n'
        '  \"mode\": \"clip\",\\n'
        '  \"scenes\": [{\"scene_id\": \"sc_1\", \"route\": \"i2v\", \"photo_prompt\": \"\", \"video_prompt\": \"\", \"negative_prompt\": \"\", \"prompt_notes\": {\"shot_intent\": \"\", \"continuity_anchor\": \"\", \"world_anchor\": \"\", \"identity_anchor\": \"\", \"lighting_anchor\": \"\", \"motion_safety\": \"\", \"audio_driven\": false}}],\\n'
        '  \"global_prompt_rules\": [\"\"]\\n'
        "}\\n\\n"
        f"SCENE_PROMPTS_CONTEXT:\\n{json.dumps(context, ensure_ascii=False)}"
    )


def _prompt_notes_template(route: str) -> dict[str, Any]:
    clean_route = route if route in ALLOWED_ROUTES else "i2v"
    return {
        "shot_intent": "",
        "continuity_anchor": "keep identity/world/style continuity from previous scene",
        "world_anchor": "same realistic world and cultural environment",
        "identity_anchor": "same hero face, body proportions, and wardrobe logic",
        "lighting_anchor": "plausible lighting progression within same realism family",
        "motion_safety": "single clear motion line, smooth camera, anatomy-safe body dynamics",
        "audio_driven": clean_route == "ia2v",
    }


def _build_fallback_scene_prompts(
    scene_plan_row: dict[str, Any],
    role_row: dict[str, Any],
    story_core: dict[str, Any],
    world_continuity: dict[str, Any],
) -> dict[str, Any]:
    scene_id = str(scene_plan_row.get("scene_id") or "")
    route = str(scene_plan_row.get("route") or "i2v").strip()
    if route not in ALLOWED_ROUTES:
        route = "i2v"

    primary_role = str(role_row.get("primary_role") or scene_plan_row.get("primary_role") or "character_1")
    scene_function = str(scene_plan_row.get("scene_function") or "scene beat")
    emotional = str(scene_plan_row.get("emotional_intent") or "grounded emotion")
    motion_intent = str(scene_plan_row.get("motion_intent") or "subtle motion")
    world_anchor = str(world_continuity.get("environment_family") or world_continuity.get("country_or_region") or "grounded realistic world")
    opening_anchor = str(story_core.get("opening_anchor") or "")

    if route == "ia2v":
        photo_prompt = (
            f"Medium three-quarter shot of {primary_role} in {world_anchor}, delivering a vocal phrase with readable face and mouth, "
            f"emotionally engaged expression, realistic lighting, continuity with established look and wardrobe."
        )
        video_prompt = (
            "Medium/three-quarter framing. Local audio slice drives visible singing of the phrase. "
            "Readable face and mouth, emotionally synced vocal delivery, restrained upper-body performance in neck/shoulders/hands, "
            "stable legs and body base, smooth gentle camera move, no abrupt turns or choreography."
        )
    elif route == "first_last":
        photo_prompt = (
            f"Key transition frame of {primary_role} in {world_anchor}, showing the hinge between start and end state for {scene_function}, "
            "realistic composition, continuity-safe wardrobe and lighting progression."
        )
        video_prompt = (
            "Show one clear A->B progression with plausible body and world continuity. "
            "Main action follows a single transition line with smooth camera support, emotional release stays readable, "
            "no teleporting, no chaotic multi-change in pose/wardrobe/world/lighting."
        )
    else:
        photo_prompt = (
            f"Realistic keyframe of {primary_role} in {world_anchor}, {scene_function} beat, clear composition, "
            f"emotion: {emotional}, continuity with prior scenes and lighting arc."
        )
        video_prompt = (
            "One observable action with a simple motion line: "
            f"{motion_intent}. Use minimal or gentle camera move, keep body motion stable and natural, preserve identity/world/lighting continuity."
        )

    fallback_notes = _prompt_notes_template(route)
    fallback_notes["shot_intent"] = scene_function
    fallback_notes["continuity_anchor"] = (
        f"{opening_anchor[:120]}" if opening_anchor else fallback_notes["continuity_anchor"]
    )

    return {
        "scene_id": scene_id,
        "route": route,
        "photo_prompt": photo_prompt,
        "video_prompt": video_prompt,
        "negative_prompt": _GLOBAL_NEGATIVE_PROMPT,
        "prompt_notes": fallback_notes,
    }


def _normalize_scene_prompts(
    raw: dict[str, Any],
    *,
    scene_rows: list[dict[str, Any]],
    role_lookup: dict[str, dict[str, Any]],
    story_core: dict[str, Any],
    world_continuity: dict[str, Any],
) -> tuple[dict[str, Any], bool, str, int, int, int]:
    by_id: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(raw.get("scenes")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id:
            by_id[scene_id] = row

    scenes: list[dict[str, Any]] = []
    used_fallback = False
    validation_errors: list[str] = []
    missing_photo_count = 0
    missing_video_count = 0

    for scene_raw in scene_rows:
        scene = _safe_dict(scene_raw)
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            continue

        expected_route = str(scene.get("route") or "i2v").strip()
        if expected_route not in ALLOWED_ROUTES:
            expected_route = "i2v"

        base = _safe_dict(by_id.get(scene_id))
        fallback_row = _build_fallback_scene_prompts(scene, _safe_dict(role_lookup.get(scene_id)), story_core, world_continuity)

        actual_route = str(base.get("route") or expected_route).strip()
        if actual_route != expected_route:
            used_fallback = True
            validation_errors.append(f"route_mismatch:{scene_id}")
            actual_route = expected_route

        photo_prompt = str(base.get("photo_prompt") or "").strip()
        if not photo_prompt:
            missing_photo_count += 1
            used_fallback = True
            photo_prompt = str(fallback_row.get("photo_prompt") or "")

        video_prompt = str(base.get("video_prompt") or "").strip()
        if not video_prompt:
            missing_video_count += 1
            used_fallback = True
            video_prompt = str(fallback_row.get("video_prompt") or "")

        negative_prompt = str(base.get("negative_prompt") or "").strip() or _GLOBAL_NEGATIVE_PROMPT
        if not str(base.get("negative_prompt") or "").strip():
            used_fallback = True

        prompt_notes = _safe_dict(base.get("prompt_notes"))
        normalized_notes = _prompt_notes_template(actual_route)
        normalized_notes.update(
            {
                "shot_intent": str(prompt_notes.get("shot_intent") or fallback_row["prompt_notes"].get("shot_intent") or ""),
                "continuity_anchor": str(
                    prompt_notes.get("continuity_anchor") or fallback_row["prompt_notes"].get("continuity_anchor") or ""
                ),
                "world_anchor": str(prompt_notes.get("world_anchor") or fallback_row["prompt_notes"].get("world_anchor") or ""),
                "identity_anchor": str(prompt_notes.get("identity_anchor") or fallback_row["prompt_notes"].get("identity_anchor") or ""),
                "lighting_anchor": str(prompt_notes.get("lighting_anchor") or fallback_row["prompt_notes"].get("lighting_anchor") or ""),
                "motion_safety": str(prompt_notes.get("motion_safety") or fallback_row["prompt_notes"].get("motion_safety") or ""),
                "audio_driven": bool(prompt_notes.get("audio_driven")) if "audio_driven" in prompt_notes else (actual_route == "ia2v"),
            }
        )
        if actual_route == "ia2v":
            normalized_notes["audio_driven"] = True

        scenes.append(
            {
                "scene_id": scene_id,
                "route": actual_route,
                "photo_prompt": photo_prompt,
                "video_prompt": video_prompt,
                "negative_prompt": negative_prompt,
                "prompt_notes": normalized_notes,
            }
        )

    normalized = {
        "plan_version": SCENE_PROMPTS_PROMPT_VERSION,
        "mode": "clip",
        "scenes": scenes,
        "global_prompt_rules": _safe_list(raw.get("global_prompt_rules")) or list(_GLOBAL_PROMPT_RULES),
    }
    validation_error = ";".join(dict.fromkeys(validation_errors))
    ia2v_audio_driven_count = sum(
        1 for row in scenes if str(row.get("route") or "") == "ia2v" and bool(_safe_dict(row.get("prompt_notes")).get("audio_driven"))
    )
    return normalized, used_fallback, validation_error, missing_photo_count, missing_video_count, ia2v_audio_driven_count


def build_gemini_scene_prompts(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    context, aux = _build_compact_context(package)
    scene_rows = _safe_list(aux.get("scene_rows"))
    role_lookup = _safe_dict(aux.get("role_lookup"))

    diagnostics = {
        "prompt_version": SCENE_PROMPTS_PROMPT_VERSION,
        "scene_count": len(scene_rows),
        "missing_photo_count": 0,
        "missing_video_count": 0,
        "ia2v_audio_driven_count": 0,
    }

    if not scene_rows:
        empty = {
            "plan_version": SCENE_PROMPTS_PROMPT_VERSION,
            "mode": "clip",
            "scenes": [],
            "global_prompt_rules": list(_GLOBAL_PROMPT_RULES),
        }
        return {
            "ok": False,
            "scene_prompts": empty,
            "error": "scene_plan_missing",
            "validation_error": "scene_plan_missing",
            "used_fallback": True,
            "diagnostics": diagnostics,
        }

    prompt = _build_prompt(context)
    try:
        response = post_generate_content(
            api_key=str(api_key or "").strip(),
            model="gemini-2.5-pro",
            body={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            timeout=90,
        )
        if isinstance(response, dict) and response.get("__http_error__"):
            raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")

        parsed = _extract_json_obj(_extract_gemini_text(response))
        scene_prompts, used_fallback, validation_error, missing_photo, missing_video, ia2v_audio_driven = _normalize_scene_prompts(
            parsed,
            scene_rows=scene_rows,
            role_lookup=role_lookup,
            story_core=_safe_dict(aux.get("story_core")),
            world_continuity=_safe_dict(aux.get("world_continuity")),
        )
        diagnostics.update(
            {
                "missing_photo_count": int(missing_photo),
                "missing_video_count": int(missing_video),
                "ia2v_audio_driven_count": int(ia2v_audio_driven),
            }
        )
        return {
            "ok": bool(_safe_list(scene_prompts.get("scenes"))),
            "scene_prompts": scene_prompts,
            "error": "" if _safe_list(scene_prompts.get("scenes")) else "invalid_scene_prompts",
            "validation_error": validation_error,
            "used_fallback": used_fallback,
            "diagnostics": diagnostics,
        }
    except Exception as exc:  # noqa: BLE001
        scene_prompts, used_fallback, validation_error, missing_photo, missing_video, ia2v_audio_driven = _normalize_scene_prompts(
            {},
            scene_rows=scene_rows,
            role_lookup=role_lookup,
            story_core=_safe_dict(aux.get("story_core")),
            world_continuity=_safe_dict(aux.get("world_continuity")),
        )
        diagnostics.update(
            {
                "missing_photo_count": int(missing_photo),
                "missing_video_count": int(missing_video),
                "ia2v_audio_driven_count": int(ia2v_audio_driven),
            }
        )
        return {
            "ok": bool(_safe_list(scene_prompts.get("scenes"))),
            "scene_prompts": scene_prompts,
            "error": str(exc),
            "validation_error": validation_error,
            "used_fallback": True,
            "diagnostics": diagnostics,
        }
