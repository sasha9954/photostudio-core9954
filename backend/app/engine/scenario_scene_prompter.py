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


def _compact_join(parts: list[str], *, sep: str = "; ", max_len: int = 600) -> str:
    text = sep.join([p.strip() for p in parts if str(p or "").strip()])
    return text[:max_len]


def _build_identity_lock_summary(story_core: dict[str, Any]) -> str:
    identity_lock = _safe_dict(story_core.get("identity_lock"))
    direct_summary = str(identity_lock.get("summary") or "").strip()
    if direct_summary:
        return direct_summary[:600]

    hero = _safe_dict(identity_lock.get("hero"))
    name = str(hero.get("name") or "").strip()
    appearance = str(hero.get("appearance_notes") or "").strip()
    core_trait = str(hero.get("core_trait") or "").strip()

    parts: list[str] = []
    if name:
        parts.append(f"Hero: {name}")
    if appearance:
        parts.append(f"Appearance: {appearance}")
    if core_trait:
        parts.append(f"Core trait: {core_trait}")
    return _compact_join(parts, max_len=600)


def _build_world_lock_summary(story_core: dict[str, Any]) -> str:
    world_lock = _safe_dict(story_core.get("world_lock"))
    direct_summary = str(world_lock.get("summary") or "").strip()
    if direct_summary:
        return direct_summary[:600]

    setting = str(world_lock.get("setting") or "").strip()
    setting_description = str(world_lock.get("setting_description") or "").strip()
    rules = str(world_lock.get("rules") or "").strip()
    mood_and_tone = str(world_lock.get("mood_and_tone") or "").strip()
    social_mood = str(world_lock.get("social_mood") or "").strip()
    key_locations = ", ".join([str(v).strip() for v in _safe_list(world_lock.get("key_locations")) if str(v).strip()])
    key_themes = ", ".join([str(v).strip() for v in _safe_list(world_lock.get("key_themes")) if str(v).strip()])

    parts: list[str] = []
    if setting:
        parts.append(f"Setting: {setting}")
    if setting_description:
        parts.append(f"Setting details: {setting_description}")
    if rules:
        parts.append(f"World rules: {rules}")
    if social_mood:
        parts.append(f"Social mood: {social_mood}")
    if mood_and_tone:
        parts.append(f"Mood/tone: {mood_and_tone}")
    if key_locations:
        parts.append(f"Key locations: {key_locations}")
    if key_themes:
        parts.append(f"Key themes: {key_themes}")
    return _compact_join(parts, max_len=600)


def _build_style_lock_summary(story_core: dict[str, Any]) -> str:
    style_lock = _safe_dict(story_core.get("style_lock"))
    direct_summary = str(style_lock.get("summary") or "").strip()
    if direct_summary:
        return direct_summary[:600]

    visual_style = str(style_lock.get("visual_style") or "").strip()
    visual_style_tags = ", ".join([str(v).strip() for v in _safe_list(style_lock.get("visual_style_tags")) if str(v).strip()])
    visual_mood = str(style_lock.get("visual_mood") or "").strip()
    color_palette = str(style_lock.get("color_palette") or "").strip()
    lighting = str(style_lock.get("lighting") or "").strip()
    camera_work = str(style_lock.get("camera_work") or "").strip()
    mood_and_tone = str(style_lock.get("mood_and_tone") or style_lock.get("overall_tone") or "").strip()
    audio_style = str(style_lock.get("audio_style") or "").strip()
    has_negative_style = bool(style_lock.get("negative_prompts") or style_lock.get("negative_style_tags"))

    parts: list[str] = []
    if visual_style:
        parts.append(f"Visual style: {visual_style}")
    if visual_style_tags:
        parts.append(f"Style tags: {visual_style_tags}")
    if visual_mood:
        parts.append(f"Visual mood: {visual_mood}")
    if color_palette:
        parts.append(f"Palette: {color_palette}")
    if lighting:
        parts.append(f"Lighting: {lighting}")
    if camera_work:
        parts.append(f"Camera: {camera_work}")
    if mood_and_tone:
        parts.append(f"Tone: {mood_and_tone}")
    if audio_style:
        parts.append(f"Audio style: {audio_style}")
    if has_negative_style:
        parts.append("Respect negative style constraints")
    return _compact_join(parts, max_len=600)


def _build_human_subject_label(role_row: dict[str, Any], story_core: dict[str, Any], scene_plan_row: dict[str, Any]) -> str:
    hero = _safe_dict(_safe_dict(story_core.get("identity_lock")).get("hero"))
    hero_name = str(hero.get("name") or "").strip()
    if hero_name:
        return hero_name

    age_bracket = str(hero.get("age_bracket") or "").strip().lower()
    gender = str(hero.get("gender_presentation") or "").strip().lower()
    appearance = str(hero.get("appearance_notes") or "").strip()
    world_lock = _safe_dict(story_core.get("world_lock"))
    setting = str(world_lock.get("setting") or world_lock.get("setting_description") or "").strip()

    age_hint = "young" if "young" in age_bracket else ""
    gender_hint = ""
    if "female" in gender or "woman" in gender:
        gender_hint = "woman"
    elif "male" in gender or "man" in gender:
        gender_hint = "man"
    elif gender:
        gender_hint = "person"

    setting_token = ""
    setting_lower = setting.lower()
    for token in ["iranian", "persian", "arab", "european", "asian", "latin", "african"]:
        if token in setting_lower:
            setting_token = token
            break

    descriptor = " ".join([p for p in [age_hint, setting_token, gender_hint] if p]).strip()
    if descriptor:
        return f"a {descriptor}"

    if appearance:
        if "woman" in appearance.lower():
            return "a woman"
        if "man" in appearance.lower():
            return "a man"
        return "a protagonist with distinctive appearance"

    primary_role = str(role_row.get("primary_role") or scene_plan_row.get("primary_role") or "").strip()
    if primary_role and primary_role != "character_1":
        return primary_role

    if gender_hint:
        return f"{gender_hint} protagonist"
    return "the protagonist"


def _build_scene_anchor_bundle(
    *,
    package: dict[str, Any],
    story_core: dict[str, Any],
    role_row: dict[str, Any],
    scene_plan_row: dict[str, Any],
    world_continuity: dict[str, Any],
) -> dict[str, str]:
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    active_roles = [str(role).strip() for role in _safe_list(role_row.get("active_roles")) if str(role).strip()]
    hero_ref_hint = "same heroine from character_1 reference" if _safe_dict(refs_inventory.get("ref_character_1")) else ""
    location_ref_hint = "same location from reference" if _safe_dict(refs_inventory.get("ref_location")) else ""

    identity_lock = _safe_dict(story_core.get("identity_lock"))
    hero_raw = identity_lock.get("hero")
    identity_parts: list[str] = [hero_ref_hint, "same hero identity continuity"]
    if isinstance(hero_raw, dict):
        hero = _safe_dict(hero_raw)
        age_band = str(hero.get("age_bracket") or "").strip()
        hair = str(hero.get("hair_signature") or hero.get("appearance_notes") or "").strip()
        outfit = str(hero.get("outfit_essentials") or "").strip()
        identity_parts.extend([age_band, hair, outfit])
    elif isinstance(hero_raw, str):
        hero_compact = " ".join(hero_raw.strip().split())[:140]
        if hero_compact:
            identity_parts.append(hero_compact)
    identity_anchor = ", ".join([part for part in identity_parts if part])[:220]

    world_lock = _safe_dict(story_core.get("world_lock"))
    style_lock = _safe_dict(story_core.get("style_lock"))
    environment = str(
        world_continuity.get("environment_family")
        or world_lock.get("setting")
        or world_lock.get("setting_description")
        or "same environment"
    ).strip()
    lighting = str(style_lock.get("lighting") or "same lighting family").strip()
    world_anchor = ", ".join([part for part in [location_ref_hint, environment, "same world continuity"] if part])[:220]
    lighting_anchor = lighting[:160]

    route = str(scene_plan_row.get("route") or "i2v").strip().lower()
    continuity_anchor = f"{str(scene_plan_row.get('scene_function') or 'scene beat').strip()}, route={route}, active_roles={','.join(active_roles) or 'none'}"
    return {
        "identity_anchor": identity_anchor or "same hero identity continuity",
        "world_anchor": world_anchor or "same world continuity",
        "lighting_anchor": lighting_anchor or "same lighting family",
        "continuity_anchor": continuity_anchor[:240],
    }


def _enrich_prompt_with_anchor(prompt: str, identity_anchor: str, world_anchor: str) -> str:
    clean = str(prompt or "").strip()
    prefix = "; ".join([part for part in [identity_anchor, world_anchor] if part]).strip()
    if not prefix:
        return clean
    if prefix.lower() in clean.lower():
        return clean
    joined = f"{prefix}. {clean}" if clean else prefix
    return joined[:900]


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
            "identity_lock_summary": _build_identity_lock_summary(story_core),
            "world_lock_summary": _build_world_lock_summary(story_core),
            "style_lock_summary": _build_style_lock_summary(story_core),
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
        "Avoid unnecessary world/geography decoration (no forced urban/industrial/location labels unless explicitly grounded in inputs).\\n"
        "Video prompts must be LTX-safe and anatomy-safe.\\n"
        "Route rules:\\n"
        "- i2v: one observable action, simple/smooth camera, safe body motion.\\n"
        "- ia2v: AUDIO-SLICE-DRIVEN singing/performance for local scene audio slice; readable face and mouth; emotionally synced vocal delivery; upper-body emphasis; stable base; smooth camera; no abrupt choreography/spins/unstable legs. ia2v is a rare accent route, not every performance beat.\\n"
        "- first_last: controlled micro-transition only. Two near-neighbor states in same world/hero/location/outfit/lighting/framing family, with exactly one controlled action/state progression. Must include TWO standalone image prompts: start_image_prompt and end_image_prompt.\\n"
        "FIRST_LAST STRICT RULES:\\n"
        "- same hero identity, same place/location family, same world continuity.\\n"
        "- same outfit continuity unless outfit change is the explicit controlled reveal.\\n"
        "- same lighting family and same camera/framing family.\\n"
        "- only one controlled action or emotional state changes.\\n"
        "- start_image_prompt and end_image_prompt must each be production-ready standalone still-image prompts (not just notes).\\n"
        "- no location jumps, no wardrobe jump (except explicit reveal), no camera grammar jump, no chaotic pose discontinuity, no identity drift, no background teleportation.\\n"
        "Scene-level quality beats (if scene ids exist):\\n"
        "- sc_1: intro-observational, more static, more closed, shadow-heavy, restrained framing intent.\\n"
        "- sc_2 (first_last): controlled internal shift. START: head lowered, gaze down, cap obscures more face. END: head slightly raised, gaze slightly higher, emotion starts to surface while cap still partially closes face.\\n"
        "- sc_5: breather with internal defiance; quiet but tense pause with readable subtle emotional charge (not dead static).\\n"
        "- sc_7 (first_last): START face partly under cap shadow, hand just beginning toward brim. END brim lifted, face open, direct camera gaze, culminating reveal.\\n"
        "Honor scene_plan route semantics exactly: first_last must stay strict first_last contract; ia2v must stay audio-driven singing/performance; i2v must stay simple observable action.\\n"
        "Always include compact negative_prompt with safety constraints.\\n"
        "Set prompt_notes.audio_driven=true for ia2v scenes.\\n"
        "Return EXACT contract keys:\\n"
        "{\\n"
        '  \"plan_version\": \"scene_prompts_v1\",\\n'
        '  \"mode\": \"clip\",\\n'
        '  \"scenes\": [{\"scene_id\": \"sc_1\", \"route\": \"i2v\", \"photo_prompt\": \"\", \"video_prompt\": \"\", \"negative_prompt\": \"\", \"start_image_prompt\": \"\", \"end_image_prompt\": \"\", \"prompt_notes\": {\"shot_intent\": \"\", \"continuity_anchor\": \"\", \"world_anchor\": \"\", \"identity_anchor\": \"\", \"lighting_anchor\": \"\", \"motion_safety\": \"\", \"audio_driven\": false}}],\\n'
        '  \"global_prompt_rules\": [\"\"]\\n'
        "}\\n\\n"
        f"SCENE_PROMPTS_CONTEXT:\\n{json.dumps(context, ensure_ascii=False)}"
    )


def _prompt_notes_template(route: str) -> dict[str, Any]:
    clean_route = route if route in ALLOWED_ROUTES else "i2v"
    notes = {
        "shot_intent": "",
        "continuity_anchor": "keep identity/world/style continuity from previous scene",
        "world_anchor": "same grounded world tone and atmosphere",
        "identity_anchor": "same hero face, body proportions, and wardrobe logic",
        "lighting_anchor": "plausible lighting progression within same realism family",
        "motion_safety": "single clear motion line, smooth camera, anatomy-safe body dynamics",
        "audio_driven": clean_route == "ia2v",
    }
    if clean_route == "first_last":
        notes.update(
            {
                "transition_contract": "controlled_micro_transition",
                "first_state": "",
                "last_state": "",
                "same_world_required": True,
                "same_outfit_required": True,
                "same_lighting_required": True,
                "same_camera_family_required": True,
            }
        )
    return notes


def _route_semantics_mismatch(scene_row: dict[str, Any]) -> bool:
    route = str(scene_row.get("route") or "").strip()
    photo = str(scene_row.get("photo_prompt") or "").lower()
    video = str(scene_row.get("video_prompt") or "").lower()
    notes = _safe_dict(scene_row.get("prompt_notes"))
    combined = f"{photo} {video}"
    if route == "ia2v":
        has_audio_lang = any(token in combined for token in ("audio", "sing", "vocal", "performance"))
        return not (bool(notes.get("audio_driven")) and has_audio_lang)
    if route == "first_last":
        continuity_flags_ok = all(
            bool(notes.get(key))
            for key in (
                "same_world_required",
                "same_outfit_required",
                "same_lighting_required",
                "same_camera_family_required",
            )
        )
        has_two_image_prompts = bool(str(scene_row.get("start_image_prompt") or "").strip()) and bool(
            str(scene_row.get("end_image_prompt") or "").strip()
        )
        has_transition_lang = any(token in combined for token in ("a->b", "transition", "start state", "end state", "near-neighbor"))
        return not (
            continuity_flags_ok
            and has_transition_lang
            and has_two_image_prompts
            and str(notes.get("transition_contract") or "") == "controlled_micro_transition"
        )
    if route == "i2v":
        return any(token in combined for token in ("audio-slice-driven", "sings", "vocal phrase")) or "controlled micro-transition" in combined
    return False


def _build_fallback_scene_prompts(
    package: dict[str, Any],
    scene_plan_row: dict[str, Any],
    role_row: dict[str, Any],
    story_core: dict[str, Any],
    world_continuity: dict[str, Any],
) -> dict[str, Any]:
    scene_id = str(scene_plan_row.get("scene_id") or "")
    route = str(scene_plan_row.get("route") or "i2v").strip()
    if route not in ALLOWED_ROUTES:
        route = "i2v"

    primary_role = _build_human_subject_label(role_row, story_core, scene_plan_row)
    scene_function = str(scene_plan_row.get("scene_function") or "scene beat")
    emotional = str(scene_plan_row.get("emotional_intent") or "grounded emotion")
    motion_intent = str(scene_plan_row.get("motion_intent") or "subtle motion")
    world_anchor = str(world_continuity.get("environment_family") or world_continuity.get("country_or_region") or "grounded realistic world")
    opening_anchor = str(story_core.get("opening_anchor") or "")
    anchors = _build_scene_anchor_bundle(
        package=package,
        story_core=story_core,
        role_row=role_row,
        scene_plan_row=scene_plan_row,
        world_continuity=world_continuity,
    )

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
        first_state = str(scene_plan_row.get("motion_intent") or "").strip() or f"{primary_role} initiates the key hinge action"
        last_state = str(scene_plan_row.get("emotional_intent") or "").strip() or f"{primary_role} completes the same hinge action"
        photo_prompt = (
            f"One transition keyframe of {primary_role} in the same {world_anchor} scene space, hinge moment for {scene_function}, "
            "subject and environment remain stable, same outfit/light/framing family."
        )
        start_image_prompt = (
            f"Start frame still of {primary_role} in the same wall-side setup, head lowered and gaze down, cap brim hiding more of the face, "
            "closed guarded emotional state, same outfit, same lighting family, same camera family."
        )
        end_image_prompt = (
            f"End frame still of {primary_role} in the same wall-side setup, head slightly raised and gaze slightly higher, emotion becoming visible, "
            "cap still partially shading the face, same outfit, same lighting family, same camera family."
        )
        if scene_id == "sc_2":
            start_image_prompt = (
                f"Start frame still of {primary_role} in the same wall-side setup, head lowered, gaze down, face more hidden under the cap brim, "
                "closed inward emotional state, shadow-heavy continuity, same outfit/light/camera family."
            )
            end_image_prompt = (
                f"End frame still of {primary_role} in the same wall-side setup, head slightly raised, gaze a little higher, emotion beginning to surface, "
                "cap still keeps partial facial closure, same outfit/light/camera family."
            )
        elif scene_id == "sc_7":
            start_image_prompt = (
                f"Start frame still of {primary_role} in the same wall-side setup, face still partly in cap shadow, hand just initiating movement toward the brim, "
                "contained pre-reveal tension, same outfit/light/camera family."
            )
            end_image_prompt = (
                f"End frame still of {primary_role} in the same wall-side setup, brim lifted, face open and readable, direct gaze to camera, "
                "culmination reveal while preserving same outfit/light/camera family."
            )
        video_prompt = (
            "Controlled micro-transition in the same exact scene space. Same subject, same outfit continuity, same lighting family, "
            "same framing family. Start state and end state are near-neighbor moments of one action. "
            "Only one meaningful progression occurs. No background jump, no wardrobe jump, no identity drift, no pose discontinuity, no multi-change."
        )
    else:
        if scene_id == "sc_1":
            photo_prompt = (
                f"Intro keyframe of {primary_role}, static and observational, closed posture near the same wall, shadow-heavy composition, "
                f"emotion: restrained {emotional}, continuity with prior scenes and lighting arc."
            )
            video_prompt = (
                "Very restrained intro beat with nearly static body line and subtle breath-level motion only. "
                "Camera intent is observational and controlled, preserving closed mood and shadow-heavy framing."
            )
        elif scene_id == "sc_5":
            photo_prompt = (
                f"Quiet tension keyframe of {primary_role} near the same wall, internal defiance gathering under stillness, "
                "subtle but readable emotional charge in face/shoulders, continuity with prior scenes and lighting arc."
            )
            video_prompt = (
                "Breather beat with micro-performance only: controlled pause, slight posture reset, contained energy building before final push. "
                "Keep movement subtle but alive, no dead static, no chaotic motion."
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
    fallback_notes["continuity_anchor"] = anchors["continuity_anchor"] if anchors["continuity_anchor"] else (
        f"{opening_anchor[:120]}" if opening_anchor else fallback_notes["continuity_anchor"]
    )
    fallback_notes["world_anchor"] = anchors["world_anchor"]
    fallback_notes["identity_anchor"] = anchors["identity_anchor"]
    fallback_notes["lighting_anchor"] = anchors["lighting_anchor"]
    if route == "first_last":
        fallback_notes["first_state"] = first_state
        fallback_notes["last_state"] = last_state

    return {
        "scene_id": scene_id,
        "route": route,
        "photo_prompt": _enrich_prompt_with_anchor(photo_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
        "video_prompt": _enrich_prompt_with_anchor(video_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
        "start_image_prompt": _enrich_prompt_with_anchor(start_image_prompt, anchors["identity_anchor"], anchors["world_anchor"])
        if route == "first_last"
        else "",
        "end_image_prompt": _enrich_prompt_with_anchor(end_image_prompt, anchors["identity_anchor"], anchors["world_anchor"])
        if route == "first_last"
        else "",
        "negative_prompt": _GLOBAL_NEGATIVE_PROMPT,
        "prompt_notes": fallback_notes,
    }


def _normalize_scene_prompts(
    package: dict[str, Any],
    raw: dict[str, Any],
    *,
    scene_rows: list[dict[str, Any]],
    role_lookup: dict[str, dict[str, Any]],
    story_core: dict[str, Any],
    world_continuity: dict[str, Any],
) -> tuple[dict[str, Any], bool, str, int, int, int, int]:
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
    route_semantics_mismatch_count = 0

    for scene_raw in scene_rows:
        scene = _safe_dict(scene_raw)
        scene_id = str(scene.get("scene_id") or "").strip()
        if not scene_id:
            continue

        expected_route = str(scene.get("route") or "i2v").strip()
        if expected_route not in ALLOWED_ROUTES:
            expected_route = "i2v"

        base = _safe_dict(by_id.get(scene_id))
        role_row = _safe_dict(role_lookup.get(scene_id))
        fallback_row = _build_fallback_scene_prompts(package, scene, role_row, story_core, world_continuity)
        anchors = _build_scene_anchor_bundle(
            package=package,
            story_core=story_core,
            role_row=role_row,
            scene_plan_row=scene,
            world_continuity=world_continuity,
        )

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
        if actual_route == "first_last":
            start_image_prompt = str(base.get("start_image_prompt") or "").strip() or str(fallback_row.get("start_image_prompt") or "").strip()
            end_image_prompt = str(base.get("end_image_prompt") or "").strip() or str(fallback_row.get("end_image_prompt") or "").strip()
            normalized_notes["transition_contract"] = "controlled_micro_transition"
            normalized_notes["first_state"] = str(
                prompt_notes.get("first_state") or fallback_row["prompt_notes"].get("first_state") or "start of one controlled action"
            )
            normalized_notes["last_state"] = str(
                prompt_notes.get("last_state") or fallback_row["prompt_notes"].get("last_state") or "completion of the same controlled action"
            )
            normalized_notes["same_world_required"] = bool(
                prompt_notes.get("same_world_required") if "same_world_required" in prompt_notes else True
            )
            normalized_notes["same_outfit_required"] = bool(
                prompt_notes.get("same_outfit_required") if "same_outfit_required" in prompt_notes else True
            )
            normalized_notes["same_lighting_required"] = bool(
                prompt_notes.get("same_lighting_required") if "same_lighting_required" in prompt_notes else True
            )
            normalized_notes["same_camera_family_required"] = bool(
                prompt_notes.get("same_camera_family_required") if "same_camera_family_required" in prompt_notes else True
            )
            if not start_image_prompt or not end_image_prompt:
                used_fallback = True
                validation_errors.append(f"first_last_image_prompt_missing:{scene_id}")
        else:
            start_image_prompt = ""
            end_image_prompt = ""

        scene_out = {
            "scene_id": scene_id,
            "route": actual_route,
            "photo_prompt": _enrich_prompt_with_anchor(photo_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
            "video_prompt": _enrich_prompt_with_anchor(video_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
            "start_image_prompt": _enrich_prompt_with_anchor(start_image_prompt, anchors["identity_anchor"], anchors["world_anchor"])
            if actual_route == "first_last"
            else "",
            "end_image_prompt": _enrich_prompt_with_anchor(end_image_prompt, anchors["identity_anchor"], anchors["world_anchor"])
            if actual_route == "first_last"
            else "",
            "negative_prompt": negative_prompt,
            "prompt_notes": normalized_notes,
        }
        if _route_semantics_mismatch(scene_out):
            route_semantics_mismatch_count += 1
        scenes.append(scene_out)

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
    return (
        normalized,
        used_fallback,
        validation_error,
        missing_photo_count,
        missing_video_count,
        ia2v_audio_driven_count,
        route_semantics_mismatch_count,
    )


def build_gemini_scene_prompts(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    context, aux = _build_compact_context(package)
    scene_rows = _safe_list(aux.get("scene_rows"))
    role_lookup = _safe_dict(aux.get("role_lookup"))

    used_model = "gemini-3-flash-preview"

    diagnostics = {
        "prompt_version": SCENE_PROMPTS_PROMPT_VERSION,
        "used_model": used_model,
        "scene_count": len(scene_rows),
        "missing_photo_count": 0,
        "missing_video_count": 0,
        "ia2v_audio_driven_count": 0,
        "scene_prompts_route_semantics_mismatch_count": 0,
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
            model=used_model,
            body={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            timeout=90,
        )
        if isinstance(response, dict) and response.get("__http_error__"):
            raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")

        parsed = _extract_json_obj(_extract_gemini_text(response))
        (
            scene_prompts,
            used_fallback,
            validation_error,
            missing_photo,
            missing_video,
            ia2v_audio_driven,
            route_semantics_mismatch_count,
        ) = _normalize_scene_prompts(
            package,
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
                "scene_prompts_route_semantics_mismatch_count": int(route_semantics_mismatch_count),
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
        (
            scene_prompts,
            used_fallback,
            validation_error,
            missing_photo,
            missing_video,
            ia2v_audio_driven,
            route_semantics_mismatch_count,
        ) = _normalize_scene_prompts(
            package,
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
                "scene_prompts_route_semantics_mismatch_count": int(route_semantics_mismatch_count),
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
