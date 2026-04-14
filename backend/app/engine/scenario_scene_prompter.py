from __future__ import annotations

import json
import re
from typing import Any

from app.engine.gemini_rest import post_generate_content
from app.engine.video_capability_canon import (
    DEFAULT_VIDEO_MODEL_ID,
    build_capability_diagnostics_summary,
    get_capability_rules_source_version,
    get_first_last_pairing_rules,
    get_lipsync_rules,
    get_video_model_capability_profile,
)

SCENE_PROMPTS_PROMPT_VERSION = "scene_prompts_v1"
ALLOWED_ROUTES = {"i2v", "ia2v", "first_last"}

_GLOBAL_NEGATIVE_PROMPT = (
    "identity drift, outfit drift, lighting/world drift, unstable anatomy, extra limbs, surreal deformation, chaotic camera, layout change"
)

_LIP_SYNC_NEGATIVE_PROMPT = (
    "unreadable mouth, broken face motion, frantic dance, flailing arms, unstable anatomy, balance loss, chaotic camera, identity drift, outfit drift, surreal deformation"
)

_FIRST_LAST_NEGATIVE_PROMPT = (
    "camera drift, zoom spikes, chaotic reframing, body-axis jump, step, crouch, bow, torso dip, large arm action, spin, added actors, layout change, temporal instability, identity drift, outfit drift, finger choreography near face, wearable-touch micro choreography"
)

_GLOBAL_PROMPT_RULES = [
    "Preserve hero identity, world anchor, style family, and realistic lighting continuity across all scenes.",
    "Keep prompts short, production-friendly, and route-aware; one clear action + one clear camera idea per video prompt.",
    "Respect wardrobe continuity when current input/story locks wardrobe; do not invent wardrobe progression defaults unless explicitly provided by current story/refs.",
    "Enforce LTX-safe motion and anatomy-safe constraints for all routes.",
]

_NEGATIVE_LEAK_TOKENS = (
    "low quality",
    "blurry",
    "worst quality",
    "distorted features",
    "morphing",
    "flickering",
    "extra limbs",
    "unrealistic physics",
    "neon",
    "club lighting",
    "warehouse",
    "distorted anatomy",
    "bad quality",
    "deformed",
)
_STALE_WORLD_TOKENS = ("apartment", "cassette", "stale wardrobe token")
_EXPLICIT_NEGATIVE_MARKERS = (
    "[negative:",
    "(negative:",
    "negative:",
    "avoid:",
    "do not show:",
)
FIRST_LAST_MODES = {
    "push_in_emotional",
    "pull_back_release",
    "small_side_arc",
    "reveal_face_from_shadow",
    "foreground_parallax",
    "camera_settle",
    "visibility_reveal",
}
SAFE_MOTION_CANON = (
    "slow walk / steady transit",
    "head turn",
    "gaze shift",
    "shoulder drop",
    "exhale / breath release",
    "weight shift",
    "controlled sway",
    "stillness with atmosphere motion",
    "subtle upper-body performance",
    "steady stare / direct gaze",
    "simple body reorientation",
    "camera push-in",
    "camera pull-back",
    "gentle lateral tracking",
    "small parallax / small arc around mostly stable subject",
)
I2V_MOTION_FAMILIES = {
    "push_in_follow",
    "side_tracking_walk",
    "look_reveal_follow",
    "baseline_forward_walk",
    "tension_head_turn",
    "pull_back_release",
}
_OWNERSHIP_ROLE_MAP = {
    "main": "character_1",
    "support": "character_2",
    "antagonist": "character_3",
    "shared": "shared",
    "world": "environment",
}
_BINDING_TYPES = {"carried", "worn", "held", "pocketed", "nearby", "environment"}


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _compact_prompt_payload(value: Any) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = _compact_prompt_payload(item)
            if cleaned in (None, "", [], {}):
                continue
            compact[str(key)] = cleaned
        return compact
    if isinstance(value, list):
        compact_list: list[Any] = []
        for item in value:
            cleaned = _compact_prompt_payload(item)
            if cleaned in (None, "", [], {}):
                continue
            compact_list.append(cleaned)
        return compact_list
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_ref_meta(meta: Any) -> dict[str, str]:
    row = _safe_dict(meta)
    ownership_role = str(row.get("ownershipRole") or row.get("ownership_role") or "auto").strip().lower() or "auto"
    ownership_mapped = str(row.get("ownershipRoleMapped") or row.get("ownership_role_mapped") or "").strip().lower()
    if ownership_mapped not in {"character_1", "character_2", "character_3", "shared", "environment"}:
        ownership_mapped = _OWNERSHIP_ROLE_MAP.get(ownership_role, "")
    binding_type = str(row.get("bindingType") or row.get("binding_type") or "nearby").strip().lower() or "nearby"
    if binding_type not in _BINDING_TYPES:
        binding_type = "nearby"
    return {
        "ownershipRole": ownership_role,
        "ownershipRoleMapped": ownership_mapped,
        "bindingType": binding_type,
    }


def _build_ref_binding_inventory(refs_inventory: dict[str, Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for key, value in refs_inventory.items():
        row = _safe_dict(value)
        meta = _normalize_ref_meta(row.get("meta"))
        if not meta["ownershipRoleMapped"] and meta["bindingType"] == "nearby":
            continue
        out.append(
            {
                "ref_id": str(key),
                "ownershipRoleMapped": meta["ownershipRoleMapped"],
                "bindingType": meta["bindingType"],
            }
        )
    return out[:16]


def _binding_prompt_clause(primary_role: str, ownership_binding_inventory: list[dict[str, str]]) -> str:
    role = str(primary_role or "").strip().lower()
    for item in ownership_binding_inventory:
        owner = str(_safe_dict(item).get("ownershipRoleMapped") or "").strip().lower()
        binding = str(_safe_dict(item).get("bindingType") or "").strip().lower()
        if role and owner and owner != role:
            continue
        if binding == "carried":
            return " Keep the same owner-bound carried object close to body; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
        if binding == "held":
            return (
                " Keep the same owner-bound held object across transit/evasion/release beats with readable handling only; "
                "it is not a replaceable random prop and one hand/handling attention stays committed, shaping posture, pace, and route decisions even off center."
            )
        if binding == "worn":
            return " Preserve worn-object silhouette continuity; treat it as look anchor, not choreography driver."
        if binding == "pocketed":
            return " Pocketed owner-bound object may stay implicit (not always visible) but continuity must hold."
        if binding == "nearby":
            return " Keep owner-bound object nearby/within reach when scene logic allows."
        if binding == "environment":
            return " Treat bound object as environment anchor in local scene, not hand choreography prop."
    return ""




def _is_owner_carried_active_scene(scene_plan_row: dict[str, Any], role_row: dict[str, Any], ownership_binding_inventory: list[dict[str, str]]) -> bool:
    primary_role = str(role_row.get("primary_role") or scene_plan_row.get("primary_role") or "").strip().lower()
    if not primary_role:
        return False
    active_roles = {str(v).strip().lower() for v in _safe_list(role_row.get("active_roles") or scene_plan_row.get("active_roles")) if str(v).strip()}
    if "props" not in active_roles:
        return False
    for item in ownership_binding_inventory:
        owner = str(_safe_dict(item).get("ownershipRoleMapped") or "").strip().lower()
        binding = str(_safe_dict(item).get("bindingType") or "").strip().lower()
        if owner == primary_role and binding == "carried":
            return True
    return False


def _is_owner_held_active_scene(scene_plan_row: dict[str, Any], role_row: dict[str, Any], ownership_binding_inventory: list[dict[str, str]]) -> bool:
    primary_role = str(role_row.get("primary_role") or scene_plan_row.get("primary_role") or "").strip().lower()
    if not primary_role:
        return False
    active_roles = {str(v).strip().lower() for v in _safe_list(role_row.get("active_roles") or scene_plan_row.get("active_roles")) if str(v).strip()}
    if "props" not in active_roles:
        return False
    for item in ownership_binding_inventory:
        owner = str(_safe_dict(item).get("ownershipRoleMapped") or "").strip().lower()
        binding = str(_safe_dict(item).get("bindingType") or "").strip().lower()
        if owner == primary_role and binding == "held":
            return True
    return False


def _resolve_active_video_model_id(package: dict[str, Any]) -> str:
    input_pkg = _safe_dict(package.get("input"))
    for key in ("video_model", "video_model_id", "model_id"):
        value = str(input_pkg.get(key) or "").strip().lower()
        if value:
            return value
    return DEFAULT_VIDEO_MODEL_ID


def _round3(value: Any) -> float:
    try:
        return round(float(value), 3)
    except Exception:
        return 0.0


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
                pass
        first_arr, last_arr = raw.find("["), raw.rfind("]")
        if first_arr >= 0 and last_arr > first_arr:
            try:
                return json.loads(raw[first_arr : last_arr + 1])
            except Exception:
                return {}
    return {}


def _coerce_scene_prompts_payload(raw: Any) -> dict[str, Any]:
    data = _safe_dict(raw)
    if isinstance(raw, list):
        return {"scenes": _safe_list(raw)}
    scenes = _safe_list(data.get("scenes"))
    if scenes:
        return {"scenes": scenes, "global_prompt_rules": _safe_list(data.get("global_prompt_rules"))}
    for key in ("result", "data", "output"):
        nested = _safe_dict(data.get(key))
        nested_scenes = _safe_list(nested.get("scenes"))
        if nested_scenes:
            return {"scenes": nested_scenes, "global_prompt_rules": _safe_list(data.get("global_prompt_rules")) or _safe_list(nested.get("global_prompt_rules"))}
    return {"scenes": [], "global_prompt_rules": _safe_list(data.get("global_prompt_rules"))}


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
    lighting_contract_anchor = _lighting_anchor_from_contract(world_continuity)
    lighting = str(style_lock.get("lighting") or "").strip() or lighting_contract_anchor
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


def _trim_sentence(text: str, *, max_len: int = 220) -> str:
    clean = " ".join(str(text or "").strip().split())
    return clean[:max_len]


def _lighting_anchor_from_contract(world_continuity: dict[str, Any]) -> str:
    lighting = _safe_dict(world_continuity.get("lighting_continuity"))
    tod = str(lighting.get("time_of_day_base") or "").replace("_", " ").strip()
    contrast = str(lighting.get("contrast_profile") or "").replace("_", " ").strip()
    shadows = str(lighting.get("shadow_behavior") or "").replace("_", " ").strip()
    practicals = ", ".join([str(item).replace("_", " ").strip() for item in _safe_list(lighting.get("practical_sources")) if str(item).strip()])
    parts = []
    if tod:
        parts.append(f"{tod} natural light")
    if contrast:
        parts.append(f"{contrast} contrast")
    if shadows:
        parts.append(shadows)
    if practicals:
        parts.append(f"stable practical sources ({practicals})")
    return "; ".join(parts)[:220] or "stable naturalistic light continuity"


def _is_high_motion_risk(scene_plan_row: dict[str, Any]) -> bool:
    risk = _safe_dict(scene_plan_row.get("motion_risk"))
    return any(
        str(risk.get(key) or "").strip().lower() == "high"
        for key in ("ltx_motion_risk", "finger_precision_risk", "prop_interaction_complexity", "face_occlusion_risk")
    )


def _detect_attached_prop_token(*texts: str) -> str:
    blob = " ".join([str(item or "").lower() for item in texts])
    for token in ("cap", "hat", "helmet", "glasses", "mask", "scarf", "headphones"):
        if token in blob:
            return token
    return ""


def _build_first_last_visual_delta(
    *,
    scene_plan_row: dict[str, Any],
    primary_role: str,
    attached_prop_token: str,
) -> tuple[str, str, str]:
    first_field = _trim_sentence(str(scene_plan_row.get("first_state") or "").strip(), max_len=180)
    last_field = _trim_sentence(str(scene_plan_row.get("last_state") or "").strip(), max_len=180)
    transition_action = _trim_sentence(str(scene_plan_row.get("transition_action") or "").strip(), max_len=180)
    scene_goal = _trim_sentence(str(scene_plan_row.get("scene_goal") or "").strip(), max_len=180)
    frame_description = _trim_sentence(str(scene_plan_row.get("frame_description") or "").strip(), max_len=180)
    motion_intent = _trim_sentence(str(scene_plan_row.get("motion_intent") or "").strip(), max_len=180)
    emotional_intent = _trim_sentence(str(scene_plan_row.get("emotional_intent") or "").strip(), max_len=180)

    visual_hints = ("head", "gaze", "eye", "hand", "brim", "face", "shoulder", "posture", "cap", "hat", "mask", "glasses")
    abstract_hints = ("emotion", "emotional", "mood", "tension", "defiant", "internal", "feeling")

    def _is_visual(text: str) -> bool:
        low = text.lower()
        if not low:
            return False
        if any(token in low for token in visual_hints):
            return True
        return not any(token in low for token in abstract_hints)

    source_fields = [first_field, frame_description, transition_action, scene_goal, motion_intent]
    first_state = next((text for text in source_fields if _is_visual(text)), "") or (
        f"{primary_role} holds the exact start pose before the controlled shift"
    )

    prop_stability = ""
    if attached_prop_token:
        if attached_prop_token in ("cap", "hat", "helmet"):
            prop_stability = f"{attached_prop_token} remains worn on head"
        elif attached_prop_token in ("glasses", "mask"):
            prop_stability = f"{attached_prop_token} remains in place"
        else:
            prop_stability = f"{attached_prop_token} remains attached with no detachment"

    candidate_delta = next((text for text in [last_field, transition_action, scene_goal, frame_description, motion_intent] if _is_visual(text)), "")
    if not candidate_delta:
        candidate_delta = emotional_intent or "one small posture/gaze shift with camera settle"
    candidate_delta_low = candidate_delta.lower()
    if any(token in candidate_delta_low for token in ("finger", "brim", "pinch", "grip", "regrip", "tiny hand")):
        candidate_delta = "gaze lifts slightly while shoulder line relaxes and framing settles"
    delta_parts = [_trim_sentence(candidate_delta, max_len=180)]
    if prop_stability and attached_prop_token not in candidate_delta.lower():
        delta_parts.append(prop_stability)
    delta_phrase = " while ".join([part for part in delta_parts if part])
    last_state = _trim_sentence(delta_phrase, max_len=180)

    if prop_stability and attached_prop_token not in last_state.lower():
        last_state = _trim_sentence(f"{last_state}; {prop_stability}", max_len=180)
    if prop_stability and attached_prop_token not in delta_phrase.lower():
        delta_phrase = _trim_sentence(f"{delta_phrase} while {prop_stability}", max_len=180)
    return _trim_sentence(first_state, max_len=180), _trim_sentence(last_state, max_len=180), _trim_sentence(delta_phrase, max_len=180)


def _build_first_last_start_image_prompt(
    *,
    primary_role: str,
    scene_space: str,
    first_state: str,
    attached_prop_token: str,
) -> str:
    prop_clause = f", {attached_prop_token} stays worn in the same place" if attached_prop_token else ""
    return (
        f"Start frame still of {primary_role} in {scene_space}: {first_state}. "
        f"Keep same subject, same world, same wardrobe, same framing family, same perspective, same camera distance, same body line{prop_clause}."
    )


def _build_first_last_end_image_prompt(
    *,
    primary_role: str,
    scene_space: str,
    last_state: str,
    attached_prop_token: str,
) -> str:
    prop_clause = f", {attached_prop_token} remains worn in the same place" if attached_prop_token else ""
    return (
        f"End frame still of {primary_role} in {scene_space}, one subtle visible delta only: {last_state}. "
        f"Keep same subject, same world, same wardrobe, same framing family, same perspective, same camera distance, same body line{prop_clause}."
    )


def _build_first_last_negative_prompt(*, attached_prop_token: str) -> str:
    base = _FIRST_LAST_NEGATIVE_PROMPT
    if not attached_prop_token:
        return base
    return (
        f"{base}, detached {attached_prop_token}, floating {attached_prop_token}, {attached_prop_token} teleportation, {attached_prop_token} drift"
    )


def _build_first_last_prompt_pair(
    *,
    primary_role: str,
    scene_space: str,
    first_state: str,
    last_state: str,
    visual_delta: str,
    attached_prop_token: str,
    first_last_mode: str = "",
) -> tuple[str, str, str, str]:
    start_image_prompt = _build_first_last_start_image_prompt(
        primary_role=primary_role,
        scene_space=scene_space,
        first_state=_trim_sentence(first_state, max_len=180),
        attached_prop_token=attached_prop_token,
    )
    end_image_prompt = _build_first_last_end_image_prompt(
        primary_role=primary_role,
        scene_space=scene_space,
        last_state=_trim_sentence(last_state, max_len=180),
        attached_prop_token=attached_prop_token,
    )
    prop_clause = f" Keep {attached_prop_token} attached/worn with no drift or detachment." if attached_prop_token else ""
    clean_mode = first_last_mode if first_last_mode in FIRST_LAST_MODES else "camera_settle"
    camera_clause_map = {
        "push_in_emotional": "camera performs a smooth minimal push-in",
        "pull_back_release": "camera performs a smooth minimal pull-back",
        "small_side_arc": "camera performs a small controlled side arc",
        "reveal_face_from_shadow": "camera settles to slightly improve face visibility",
        "foreground_parallax": "camera allows subtle foreground parallax pass",
        "camera_settle": "camera settles with no perspective jump",
        "visibility_reveal": "camera reframes minimally to reveal visibility shift",
    }
    camera_clause = camera_clause_map.get(clean_mode, "camera settles with no perspective jump")
    positive_video_prompt = (
        f"Controlled first_last transition in {scene_space}: {camera_clause} while {primary_role} keeps a broad readable state shift, {_trim_sentence(visual_delta, max_len=180)}. "
        "Keep same subject/world/outfit/shot family, same framing family, smooth continuity, no abrupt zoom spikes, no large perspective jump, no fine-motor prop choreography. "
        f"Only one subtle visible delta, with no added actors or layout change.{prop_clause}"
    )
    negative_video_prompt = _build_first_last_negative_prompt(attached_prop_token=attached_prop_token)
    return start_image_prompt[:650], end_image_prompt[:700], positive_video_prompt[:850], negative_video_prompt


def _build_compact_context(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    compiled_contract = _safe_dict(role_plan.get("compiled_contract"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))

    scene_windows = _build_scene_windows(audio_map)
    role_lookup = _build_scene_role_lookup(role_plan)
    model_id = _resolve_active_video_model_id(package)
    route_profiles = {
        route: get_video_model_capability_profile(model_id, route)
        for route in ("i2v", "ia2v", "first_last", "lipsync")
    }
    ownership_binding_inventory = _build_ref_binding_inventory(refs_inventory)

    compact_context = {
        "mode": "clip",
        "content_type": str(input_pkg.get("content_type") or ""),
        "format": str(input_pkg.get("format") or ""),
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
            "audio_dramaturgy": _safe_dict(audio_map.get("audio_dramaturgy")),
        },
        "role_plan": {
            "world_continuity": _safe_dict(role_plan.get("world_continuity")),
            "compiled_contract": {
                "global_contract": _safe_dict(compiled_contract.get("global_contract")),
                "scene_contracts": _safe_list(compiled_contract.get("scene_contracts")),
            },
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
                    "shot_scale": str(_safe_dict(row).get("shot_scale") or ""),
                    "camera_intimacy": str(_safe_dict(row).get("camera_intimacy") or ""),
                    "performance_openness": str(_safe_dict(row).get("performance_openness") or ""),
                    "visual_event_type": str(_safe_dict(row).get("visual_event_type") or ""),
                    "repeat_variation_rule": str(_safe_dict(row).get("repeat_variation_rule") or ""),
                    "first_last_mode": str(_safe_dict(row).get("first_last_mode") or ""),
                    "motion_risk": _safe_dict(_safe_dict(row).get("motion_risk")),
                }
                for row in _safe_list(scene_plan.get("scenes"))
            ],
        },
        "ownership_binding_inventory": ownership_binding_inventory,
        "prompt_policy": {
            "ltx_safe_motion": True,
            "realism_required": True,
            "world_continuity_required": True,
            "identity_continuity_required": True,
        },
        "video_capability_canon": {
            "model_id": model_id,
            "capability_rules_source_version": get_capability_rules_source_version(),
            "route_profiles": route_profiles,
            "first_last_pairing_rules": get_first_last_pairing_rules(model_id),
            "lipsync_rules": get_lipsync_rules(model_id),
            "usage_policy": {
                "prefer_verified_safe_by_default": True,
                "experimental_is_opt_in_not_default": True,
                "blocked_patterns_must_be_filtered": True,
            },
        },
    }

    aux = {
        "scene_rows": _safe_list(scene_plan.get("scenes")),
        "role_lookup": role_lookup,
        "story_core": story_core,
        "world_continuity": _safe_dict(role_plan.get("world_continuity")),
        "ownership_binding_inventory": ownership_binding_inventory,
        "compiled_contract": compiled_contract,
    }
    return _compact_prompt_payload(compact_context), aux


def _build_scene_contract_lookup(compiled_contract: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(compiled_contract.get("scene_contracts")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id:
            out[scene_id] = row
    return out, _safe_dict(compiled_contract.get("global_contract"))


def _presence_policy_clause(presence_policy: dict[str, Any]) -> str:
    policy = str(_safe_dict(presence_policy).get("presence_policy") or "").strip().upper()
    if policy == "STRICT":
        return "No extra visible people in frame; keep only required contract actors."
    if policy == "MINIMAL":
        return "Extras absent or extremely sparse; no identifiable secondary hero unless explicitly contracted."
    if policy == "ADDITIVE":
        return "Allow only anonymous atmospheric background presence; no second identifiable hero unless contract allows."
    return ""


def _append_compact_clauses(prompt: str, clauses: list[str], *, max_len: int = 900) -> str:
    text = str(prompt or "").strip()
    if not clauses:
        return text[:max_len]
    low = text.lower()
    for clause in clauses:
        clean_clause = str(clause or "").strip().rstrip(".")
        if not clean_clause:
            continue
        if clean_clause.lower() in low:
            continue
        text = f"{text.rstrip('. ')}. {clean_clause}".strip() if text else clean_clause
        low = text.lower()
    return text[:max_len]


def _build_prompt(context: dict[str, Any]) -> str:
    canon = _safe_dict(context.get("video_capability_canon"))
    route_profiles = _safe_dict(canon.get("route_profiles"))
    i2v_profile = _safe_dict(route_profiles.get("i2v"))
    first_last_profile = _safe_dict(route_profiles.get("first_last"))
    lipsync_profile = _safe_dict(route_profiles.get("lipsync"))
    i2v_safe = ", ".join([str(v).strip() for v in _safe_list(i2v_profile.get("verified_safe")) if str(v).strip()])
    i2v_blocked = ", ".join([str(v).strip() for v in _safe_list(i2v_profile.get("blocked")) if str(v).strip()])
    first_last_blocked = ", ".join([str(v).strip() for v in _safe_list(first_last_profile.get("blocked")) if str(v).strip()])
    lipsync_blocked = ", ".join([str(v).strip() for v in _safe_list(lipsync_profile.get("blocked")) if str(v).strip()])
    return (
        "You are SCENE PROMPTS stage for scenario pipeline.\\n"
        "Return STRICT JSON only. No markdown.\\n"
        "MODE is clip only.\\n"
        "Task: build planning-to-generation bridge prompts for later storyboard/render stages.\\n"
        "Do not access raw Scenario Director text directly; treat role_plan.compiled_contract as source of cast/world/presence constraints.\\n"
        "Prompts are translation layer only: do not invent new plot geography beyond upstream story/role/scene contracts.\\n"
        "Do NOT produce render payloads or API calls.\\n"
        "For each scene from scene_plan, write route-aware photo_prompt and video_prompt with compact production language.\\n"
        "Use only CURRENT PACKAGE context in this request; do not reuse stale or previous package prompts.\\n"
        "Preserve identity/world/style continuity and realism.\\n"
        "Clip-mode principle: visual/emotional arc under music energy, not default literal travel plot.\\n"
        "Prompt text must be short, usable, and not overloaded.\\n"
        "Avoid unnecessary world/geography decoration (no forced urban/industrial/location labels unless explicitly grounded in inputs).\\n"
        "Use scene visual progression attributes (shot_scale, camera_intimacy, performance_openness, visual_event_type, repeat_variation_rule) to keep repeated phrases visually different.\\n"
        "Use lighting continuity contract as stable anchor and translate it to natural cinematic language, not numeric dump.\\n"
        "If motion_risk shows high complexity, simplify action wording: broad readable motion only, no tiny finger-sequence choreography.\\n"
        f"Video capability canon model={str(canon.get('model_id') or DEFAULT_VIDEO_MODEL_ID)} version={str(canon.get('capability_rules_source_version') or '')}.\\n"
        f"Use VERIFIED_SAFE defaults first: {i2v_safe}.\\n"
        "Experimental patterns are opt-in only and must not be default.\\n"
        f"Blocked i2v patterns (filter out): {i2v_blocked}.\\n"
        f"Blocked first_last patterns (filter out): {first_last_blocked}.\\n"
        f"Blocked lipsync patterns (filter out): {lipsync_blocked}.\\n"
        "Camera-led transitions are preferred over fine-motor body actions when either can express the same beat.\\n"
        "When wardrobe or worn-object anchors are present, preserve continuity and avoid default item-manipulation choreography unless explicitly requested by current input.\\n"
        "Use ownership_binding_inventory for owner/binding grammar: carried/held stronger owner continuity, worn silhouette continuity, pocketed/nearby lighter continuity, environment world-anchor behavior.\\n"
        "Do not randomly detach owner-bound carried/held objects from owner continuity.\\n"
        "Video prompts must be LTX-native, anatomy-safe, and motion-first.\\n"
        "Write prompts in natural cinematic English, present tense, one connected paragraph, chronological motion logic.\\n"
        "Describe what starts happening after the still image; do NOT mechanically re-describe all static elements already visible.\\n"
        "Keep one primary motion idea per scene and avoid contradictory instruction stacks.\\n"
        "Hard constraints must be compressed into a short safety tail at the end only.\\n"
        "Route rules:\\n"
        "- i2v (normal): motion-first continuation from the still image, one visible action line, camera behavior, energy/atmosphere, short safety tail at end.\\n"
        "- ia2v (lip_sync_music/performance): performance-first, readable face/mouth, musical phrasing drives upper-body expressivity, stable balance, smooth camera, short safety tail at end.\\n"
        "- first_last (locked transition): controlled camera/framing/state transition between near-matched anchor frames with one subtle visible delta only; same subject/stance/world/costume/shot feeling; must include TWO standalone prompts start_image_prompt and end_image_prompt; short safety tail at end.\\n"
        "- first_last must honor scene_plan.first_last_mode when present: push_in_emotional, pull_back_release, small_side_arc, reveal_face_from_shadow, foreground_parallax, camera_settle, visibility_reveal.\\n"
        "Energy tier behavior (mandatory): low-energy i2v -> restrained motion and held tension/afterimage; medium-energy i2v -> forward motion with controlled camera support; high-energy ia2v -> expressive but readable upper-body performance; first_last -> continuity-first micro-transition and never transit/geography change.\\n"
        "FIRST_LAST FORBIDDEN BY DEFAULT: stepping, crouching, bowing, torso dip, dance choreography, large arm action, spinning, dramatic camera movement, added background actors, layout changes, fine-motor hand/prop choreography.\\n"
        "Do NOT use dance/performance language in first_last unless scene contract explicitly asks for it.\\n"
        "Scene-level quality beats (if scene ids exist):\\n"
        "- sc_1: intro-observational, more static, more closed, shadow-heavy, restrained framing intent.\\n"
        "- sc_5: breather with internal defiance; quiet but tense pause with readable subtle emotional charge (not dead static).\\n"
        "Honor scene_plan route semantics exactly: first_last must stay strict first_last contract; ia2v must stay audio-driven singing/performance; i2v must stay simple observable action.\\n"
        "Always include compact negative_prompt with safety constraints as short tail text.\\n"
        "Never mix negative prompt text into positive video_prompt; keep positive and negative fields separated.\\n"
        "For first_last, return both positive_video_prompt and negative_video_prompt fields (negative_video_prompt is mandatory for first_last).\\n"
        "Set prompt_notes.audio_driven=true for ia2v scenes.\\n"
        "Return EXACT contract keys:\\n"
        "{\\n"
        '  \"plan_version\": \"scene_prompts_v1\",\\n'
        '  \"mode\": \"clip\",\\n'
        '  \"scenes\": [{\"scene_id\": \"sc_1\", \"route\": \"i2v\", \"photo_prompt\": \"\", \"video_prompt\": \"\", \"negative_prompt\": \"\", \"positive_video_prompt\": \"\", \"negative_video_prompt\": \"\", \"start_image_prompt\": \"\", \"end_image_prompt\": \"\", \"prompt_notes\": {\"shot_intent\": \"\", \"continuity_anchor\": \"\", \"world_anchor\": \"\", \"identity_anchor\": \"\", \"lighting_anchor\": \"\", \"motion_safety\": \"\", \"audio_driven\": false}}],\\n'
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
    if clean_route == "i2v":
        notes.update(
            {
                "i2v_motion_family": "baseline_forward_walk",
                "pace_class": "purposeful",
                "camera_pattern": "stable_follow",
                "reveal_target": "none",
                "parallax_required": False,
                "allow_head_turn": False,
                "allow_simple_hand_motion": True,
                "forbid_complex_hand_motion": True,
                "forbid_slow_motion_feel": True,
                "forbid_bullet_time": True,
                "forbid_stylized_action": True,
                "require_real_time_pacing": True,
                "max_camera_intensity": "low",
                "i2v_prompt_duration_hint_sec": 0.0,
                "template_built": False,
            }
        )
    if clean_route == "first_last":
        notes.update(
            {
                "transition_contract": "controlled_micro_transition",
                "first_last_mode": "",
                "first_state": "",
                "last_state": "",
                "same_world_required": True,
                "same_outfit_required": True,
                "same_lighting_required": True,
                "same_camera_family_required": True,
            }
        )
    return notes


def _scene_plan_semantics_lock(scene_plan_row: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene_id": str(scene_plan_row.get("scene_id") or "").strip(),
        "route": str(scene_plan_row.get("route") or "i2v").strip().lower(),
        "scene_function": str(scene_plan_row.get("scene_function") or "").strip(),
        "emotional_intent": str(scene_plan_row.get("emotional_intent") or "").strip(),
        "motion_intent": str(scene_plan_row.get("motion_intent") or "").strip(),
        "shot_scale": str(scene_plan_row.get("shot_scale") or "").strip(),
        "camera_intimacy": str(scene_plan_row.get("camera_intimacy") or "").strip(),
        "performance_openness": str(scene_plan_row.get("performance_openness") or "").strip(),
        "visual_event_type": str(scene_plan_row.get("visual_event_type") or "").strip(),
        "first_last_mode": str(scene_plan_row.get("first_last_mode") or "").strip(),
        "i2v_motion_family": str(scene_plan_row.get("i2v_motion_family") or "").strip(),
        "pace_class": str(scene_plan_row.get("pace_class") or "").strip(),
        "camera_pattern": str(scene_plan_row.get("camera_pattern") or "").strip(),
        "reveal_target": str(scene_plan_row.get("reveal_target") or "").strip(),
        "parallax_required": bool(scene_plan_row.get("parallax_required")),
        "allow_head_turn": bool(scene_plan_row.get("allow_head_turn")),
        "allow_simple_hand_motion": bool(scene_plan_row.get("allow_simple_hand_motion")),
        "forbid_complex_hand_motion": bool(scene_plan_row.get("forbid_complex_hand_motion")),
        "forbid_slow_motion_feel": bool(scene_plan_row.get("forbid_slow_motion_feel")),
        "forbid_bullet_time": bool(scene_plan_row.get("forbid_bullet_time")),
        "forbid_stylized_action": bool(scene_plan_row.get("forbid_stylized_action")),
        "require_real_time_pacing": bool(scene_plan_row.get("require_real_time_pacing")),
        "max_camera_intensity": str(scene_plan_row.get("max_camera_intensity") or "").strip(),
        "i2v_prompt_duration_hint_sec": _round3(scene_plan_row.get("i2v_prompt_duration_hint_sec")),
        "motion_risk": _safe_dict(scene_plan_row.get("motion_risk")),
    }


def _detect_scene_prompt_contract_mismatch(*, expected_route: str, scene_plan_row: dict[str, Any], model_row: dict[str, Any]) -> tuple[bool, bool]:
    actual_route = str(model_row.get("route") or expected_route).strip().lower()
    route_mismatch = actual_route != expected_route
    if not model_row:
        return route_mismatch, False

    notes = _safe_dict(model_row.get("prompt_notes"))
    blob = " ".join(
        [
            str(model_row.get("photo_prompt") or ""),
            str(model_row.get("video_prompt") or ""),
            str(model_row.get("positive_video_prompt") or ""),
            str(model_row.get("start_image_prompt") or ""),
            str(model_row.get("end_image_prompt") or ""),
        ]
    ).lower()
    has_first_last_terms = any(token in blob for token in ("micro-transition", "one subtle", "start frame", "end frame", "subtle delta"))
    has_performance_terms = any(token in blob for token in ("audio", "vocal", "sing", "lip", "performance"))
    has_face_readability = any(token in blob for token in ("readable face", "mouth", "upper-body", "upper body"))
    has_transition_transit_language = any(token in blob for token in ("travel", "walk to", "transit", "location change", "geography change"))

    semantic_mismatch = False
    scene_function = str(scene_plan_row.get("scene_function") or "").lower()
    if expected_route == "ia2v":
        if has_first_last_terms:
            semantic_mismatch = True
        if not bool(notes.get("audio_driven")):
            semantic_mismatch = True
        if not (has_performance_terms and has_face_readability):
            semantic_mismatch = True
        if ("climax" in scene_function or "performance" in scene_function) and has_first_last_terms:
            semantic_mismatch = True
    elif expected_route == "first_last":
        has_start = bool(str(model_row.get("start_image_prompt") or "").strip())
        has_end = bool(str(model_row.get("end_image_prompt") or "").strip())
        if not has_start or not has_end:
            semantic_mismatch = True
        if str(notes.get("transition_contract") or "") != "controlled_micro_transition":
            semantic_mismatch = True
        if not has_first_last_terms:
            semantic_mismatch = True
        if has_performance_terms or has_transition_transit_language:
            semantic_mismatch = True
    else:  # i2v
        if has_first_last_terms:
            semantic_mismatch = True
        if has_performance_terms and bool(notes.get("audio_driven")):
            semantic_mismatch = True
        if str(model_row.get("start_image_prompt") or "").strip() or str(model_row.get("end_image_prompt") or "").strip():
            semantic_mismatch = True

    return route_mismatch, semantic_mismatch


def _sanitize_positive_prompt(text: str, negative_text: str) -> tuple[str, bool]:
    clean = str(text or "").strip()
    if not clean:
        return "", False
    changed = False
    low = clean.lower()
    neg_low = str(negative_text or "").strip().lower()
    cut_idx = -1
    for token in _NEGATIVE_LEAK_TOKENS:
        idx = low.find(token)
        if idx >= 0 and (cut_idx < 0 or idx < cut_idx):
            cut_idx = idx
    for marker in _EXPLICIT_NEGATIVE_MARKERS:
        idx = low.find(marker)
        if idx >= 0 and (cut_idx < 0 or idx < cut_idx):
            cut_idx = idx
    if cut_idx >= 0:
        clean = clean[:cut_idx].rstrip(" ,;.")
        changed = True
    if neg_low and neg_low in clean.lower():
        clean = clean[: clean.lower().find(neg_low)].rstrip(" ,;.")
        changed = True
    before_cleanup = clean
    clean = re.sub(r"[\[\(]\s*$", "", clean).strip()
    clean = re.sub(r"[\]\)]", "", clean)
    clean = re.sub(r"\s*[,;:.!?-]\s*[,;:.!?-]\s*", ", ", clean)
    clean = re.sub(r"[,;:\-]+\s*$", "", clean)
    clean = re.sub(r"\s{2,}", " ", clean).strip(" ,;:.")
    if clean != before_cleanup:
        changed = True
    return clean[:900], changed


def _build_package_anchor_fingerprint(package: dict[str, Any], story_core: dict[str, Any], world_continuity: dict[str, Any]) -> dict[str, Any]:
    refs = _safe_dict(package.get("refs_inventory"))
    hero = _safe_dict(_safe_dict(story_core.get("identity_lock")).get("hero"))
    style_lock = _safe_dict(story_core.get("style_lock"))
    world_lock = _safe_dict(story_core.get("world_lock"))
    anchor_tokens = [
        str(hero.get("outfit_essentials") or ""),
        str(hero.get("appearance_notes") or ""),
        str(world_continuity.get("environment_family") or ""),
        str(world_lock.get("setting") or ""),
        str(world_lock.get("setting_description") or ""),
        str(style_lock.get("lighting") or ""),
        str(_safe_dict(refs.get("ref_location")).get("value") or ""),
        str(_safe_dict(refs.get("ref_character_1")).get("value") or ""),
    ]
    token_words: set[str] = set()
    for chunk in anchor_tokens:
        for word in re.findall(r"[a-zA-Z]{4,}", chunk.lower()):
            token_words.add(word)
    return {
        "hero_anchor": _build_identity_lock_summary(story_core),
        "world_anchor": _build_world_lock_summary(story_core),
        "lighting_anchor": str(style_lock.get("lighting") or "").strip(),
        "continuity_tokens": sorted(token_words)[:40],
    }


def _row_looks_unrelated_to_current_package(row: dict[str, Any], fingerprint: dict[str, Any]) -> bool:
    blob = " ".join(
        [
            str(row.get("photo_prompt") or ""),
            str(row.get("video_prompt") or ""),
            str(row.get("positive_video_prompt") or ""),
            str(_safe_dict(row.get("prompt_notes")).get("world_anchor") or ""),
            str(_safe_dict(row.get("prompt_notes")).get("identity_anchor") or ""),
        ]
    ).lower()
    if not blob:
        return False
    stale_hits = sum(1 for token in _STALE_WORLD_TOKENS if token in blob)
    continuity_tokens = _safe_list(fingerprint.get("continuity_tokens"))
    anchor_hits = sum(1 for token in continuity_tokens if token and str(token) in blob)
    return stale_hits > 0 and anchor_hits == 0


def _build_i2v_base_guardrail(*, role_label: str, world_anchor: str, lighting_anchor: str) -> str:
    return (
        f"Exact first-frame identity anchor for {role_label}: same primary subject identity, same face, same wardrobe family when locked. "
        f"Keep the same world family ({world_anchor}) and lighting family ({lighting_anchor or 'current locked lighting family'}) when locked by current context. "
        "Preserve same background geometry and grounded documentary realism. "
        "No identity drift, no wardrobe change, no location change, no broken anatomy, no floating limbs, no leg warping, no face deformation, no camera shake, no slow-motion feel, no stylized action feel, no bullet-time effect."
    )


def _build_i2v_motion_family_prompt(scene_plan_row: dict[str, Any]) -> tuple[str, str]:
    family = str(scene_plan_row.get("i2v_motion_family") or "").strip()
    pace_class = str(scene_plan_row.get("pace_class") or "").strip().lower()
    camera_pattern = str(scene_plan_row.get("camera_pattern") or "").strip().lower()
    reveal_target = str(scene_plan_row.get("reveal_target") or "").strip().lower()
    hint_sec = _round3(scene_plan_row.get("i2v_prompt_duration_hint_sec"))
    duration_hint = f" Keep pacing real-time around ~{hint_sec:.1f}s." if hint_sec > 0 else " Keep pacing real-time."
    pace_prefix_map = {
        "restrained": "Restrained, controlled pacing.",
        "purposeful": "Purposeful, readable pacing.",
        "energetic": "Energetic but controlled pacing.",
    }
    camera_clause_map = {
        "push_in": "Camera pattern: push_in with smooth forward pressure only.",
        "side_track": "Camera pattern: side_track with stable lateral travel and coherent parallax.",
        "follow_reveal": "Camera pattern: follow_reveal that follows attention into a clear reveal.",
        "pull_back": "Camera pattern: pull_back opening depth while keeping framing stable.",
        "stable_follow": "Camera pattern: stable_follow with minimal reframing spikes.",
    }
    reveal_clause_map = {
        "forward_path": "Reveal target: forward_path.",
        "side_space": "Reveal target: side_space.",
        "noticed_object": "Reveal target: noticed_object.",
    }
    templates = {
        "push_in_follow": (
            "Natural forward motion line. Smooth push-in from medium-full framing toward a controlled medium shot; physically natural motion with stable legs/feet, natural simple arm swing only, subtle fabric/hair response, no dramatic camera turn.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn, aggressive zoom spikes, extreme close-up crash-in",
        ),
        "side_tracking_walk": (
            "Forward walk continuation while camera tracks sideways with clearly visible but controlled parallax. Keep environment geometry stable, physically coherent body travel, and no background collapse.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn, unstable parallax, no parallax",
        ),
        "look_reveal_follow": (
            "Subject keeps moving forward and shifts attention; slight head/upper-body turn without stopping. Camera follows attention via lateral move plus pan, opening a revealed traveling view with believable parallax and stable geometry.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn, unstable parallax, no parallax, head turn without camera follow",
        ),
        "baseline_forward_walk": (
            "Restrained natural forward walk: one to two calm steps or short grounded walk continuation, mostly stable frontal/stable-follow camera, subtle fabric/hair response, safe realism.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn",
        ),
        "tension_head_turn": (
            "Slight slowdown with restrained side glance/cautious check, subtle shoulder tension, simple body motion, suspicious/alert feel, no large gestures.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn",
        ),
        "pull_back_release": (
            "Camera slowly pulls back while subject remains grounded in motion/stance; world depth opens behind with stable geometry and ambient life; restrained emotional tone for release/aftermath distance.",
            "identity drift, different subject, different face, different clothes, different wearable anchors, different location family, different lighting family, broken anatomy, extra limbs, extra fingers, warped legs, twisted knees, foot sliding, floating body, face distortion, flicker, surreal motion, background morphing, unstable geometry, slow motion, bullet time, matrix effect, dramatic camera rotation, 90-degree camera turn, static background collapse, artificial zoom feel",
        ),
    }
    motion_prompt, negative_prompt = templates.get("baseline_forward_walk")
    if family in templates:
        motion_prompt, negative_prompt = templates[family]
    pace_clause = pace_prefix_map.get(pace_class, "Purposeful, readable pacing.")
    camera_clause = camera_clause_map.get(camera_pattern, "Camera pattern: stable_follow with minimal reframing spikes.")
    reveal_clause = ""
    if family == "look_reveal_follow":
        reveal_clause = f" {reveal_clause_map.get(reveal_target, 'Reveal target: forward_path.')}"
    return f"{pace_clause} {camera_clause} {motion_prompt}{reveal_clause}{duration_hint}", negative_prompt


def _build_i2v_negative_prompt(scene_plan_row: dict[str, Any]) -> str:
    _, negative_prompt = _build_i2v_motion_family_prompt(scene_plan_row)
    return negative_prompt


def _build_i2v_prompt_bundle(
    *,
    role_label: str,
    scene_plan_row: dict[str, Any],
    world_anchor: str,
    lighting_anchor: str,
    identity_anchor: str,
) -> dict[str, str]:
    guardrail = _build_i2v_base_guardrail(role_label=role_label, world_anchor=world_anchor, lighting_anchor=lighting_anchor)
    motion_prompt, _ = _build_i2v_motion_family_prompt(scene_plan_row)
    negative_prompt = _build_i2v_negative_prompt(scene_plan_row)
    photo_prompt = (
        f"Exact anchor frame of {role_label} in the same locked world family and same locked lighting family; "
        "same face/wardrobe continuity anchors and stable geometry, grounded documentary realism."
    )
    positive_video_prompt = f"{guardrail} {motion_prompt}".strip()
    return {
        "photo_prompt": _enrich_prompt_with_anchor(photo_prompt, identity_anchor, world_anchor),
        "video_prompt": _enrich_prompt_with_anchor(positive_video_prompt, identity_anchor, world_anchor),
        "positive_video_prompt": _enrich_prompt_with_anchor(positive_video_prompt, identity_anchor, world_anchor),
        "negative_video_prompt": negative_prompt,
        "negative_prompt": negative_prompt,
    }


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
    ownership_binding_inventory = _build_ref_binding_inventory(_safe_dict(package.get("refs_inventory")))
    binding_clause = _binding_prompt_clause(str(role_row.get("primary_role") or ""), ownership_binding_inventory)

    positive_video_prompt = ""
    negative_video_prompt = ""
    high_motion_risk = _is_high_motion_risk(scene_plan_row)

    if route == "ia2v":
        photo_prompt = (
            f"Performance portrait of {primary_role} in {world_anchor}, framed to keep face and mouth readable while the vocal phrase carries emotion through eyes, shoulders, and hands."
        )
        video_prompt = (
            "The still frame opens into a performance beat as the vocal phrase leads subtle rhythmic sway, a controlled torso pulse, a gentle head turn, and soft hand phrasing while the face stays readable and emotionally alive. "
            f"Camera motion stays smooth and supportive.{binding_clause} Safety tail: stable anatomy and balance, no frantic dance, spins, flailing arms, or camera gimmicks."
        )
        negative_video_prompt = _LIP_SYNC_NEGATIVE_PROMPT
    elif route == "first_last":
        first_last_mode = str(scene_plan_row.get("first_last_mode") or "").strip().lower()
        if first_last_mode not in FIRST_LAST_MODES:
            first_last_mode = "camera_settle"
        attached_prop_token = _detect_attached_prop_token(
            str(scene_plan_row.get("first_state") or ""),
            str(scene_plan_row.get("last_state") or ""),
            str(scene_plan_row.get("transition_action") or ""),
            str(scene_plan_row.get("scene_goal") or ""),
            str(scene_plan_row.get("frame_description") or ""),
            str(scene_plan_row.get("motion_intent") or ""),
            str(scene_plan_row.get("emotional_intent") or ""),
            scene_function,
            str(scene_plan_row.get("scene_summary") or ""),
            str(story_core.get("story_summary") or ""),
        )
        first_state, last_state, visual_delta = _build_first_last_visual_delta(
            scene_plan_row=scene_plan_row,
            primary_role=primary_role,
            attached_prop_token=attached_prop_token,
        )
        scene_space = _trim_sentence(f"the same {world_anchor} scene space", max_len=90)
        photo_prompt = (
            f"One transition keyframe of {primary_role} in the same {world_anchor} scene space, hinge moment for {scene_function}, "
            "subject and environment remain stable, same outfit/light/framing family."
        )
        start_image_prompt, end_image_prompt, positive_video_prompt, negative_video_prompt = _build_first_last_prompt_pair(
            primary_role=primary_role,
            scene_space=scene_space,
            first_state=first_state,
            last_state=last_state,
            visual_delta=visual_delta,
            attached_prop_token=attached_prop_token,
            first_last_mode=first_last_mode,
        )
        video_prompt = positive_video_prompt
    else:
        if scene_id == "sc_1":
            photo_prompt = (
                f"Intro keyframe of {primary_role}, static and observational, closed posture near the same wall, shadow-heavy composition, "
                f"emotion: restrained {emotional}, continuity with prior scenes and lighting arc."
            )
            video_prompt = (
                "Very restrained intro beat with nearly static body line and subtle breath-level motion only. "
                f"Camera intent is observational and controlled, preserving closed mood and shadow-heavy framing.{binding_clause}"
            )
        elif scene_id == "sc_5":
            photo_prompt = (
                f"Quiet tension keyframe of {primary_role} near the same wall, internal defiance gathering under stillness, "
                "subtle but readable emotional charge in face/shoulders, continuity with prior scenes and lighting arc."
            )
            video_prompt = (
                "Breather beat with micro-performance only: controlled pause, slight posture reset, contained energy building before final push. "
                f"Keep movement subtle but alive, no dead static, no chaotic motion.{binding_clause}"
            )
        else:
            photo_prompt = (
                f"Realistic keyframe of {primary_role} in {world_anchor}, {scene_function} beat, clear composition, "
                f"emotion: {emotional}, continuity with prior scenes and lighting arc."
            )
            video_prompt = (
                f"After the still frame, the moment moves forward through one clear action: {motion_intent}, with camera behavior that follows the action and keeps the atmosphere grounded in {emotional}. "
                f"Safety tail: preserve identity/world continuity, stable anatomy, and controlled camera.{binding_clause}"
            )

    if high_motion_risk and route in {"i2v", "ia2v"}:
        simplified = (
            f"Use one broad readable action only in {world_anchor}: controlled gaze/head/shoulder shift with minimal hand emphasis, no tiny finger sequencing near face, no wearable-adjustment micro details, no multistep prop manipulation. "
            "Prefer smooth camera settle/push/pull over micro hand actions."
        )
        video_prompt = simplified
        positive_video_prompt = simplified

    fallback_notes = _prompt_notes_template(route)
    fallback_notes["shot_intent"] = scene_function
    fallback_notes["continuity_anchor"] = anchors["continuity_anchor"] if anchors["continuity_anchor"] else (
        f"{opening_anchor[:120]}" if opening_anchor else fallback_notes["continuity_anchor"]
    )
    fallback_notes["world_anchor"] = anchors["world_anchor"]
    fallback_notes["identity_anchor"] = anchors["identity_anchor"]
    fallback_notes["lighting_anchor"] = anchors["lighting_anchor"]
    fallback_notes["shot_scale"] = str(scene_plan_row.get("shot_scale") or "")
    fallback_notes["camera_intimacy"] = str(scene_plan_row.get("camera_intimacy") or "")
    fallback_notes["performance_openness"] = str(scene_plan_row.get("performance_openness") or "")
    fallback_notes["visual_event_type"] = str(scene_plan_row.get("visual_event_type") or "")
    fallback_notes["repeat_variation_rule"] = str(scene_plan_row.get("repeat_variation_rule") or "")
    fallback_notes["motion_risk"] = _safe_dict(scene_plan_row.get("motion_risk"))
    fallback_notes["risk_simplified"] = bool(high_motion_risk and route in {"i2v", "ia2v"})
    if route == "first_last":
        fallback_notes["first_state"] = first_state
        fallback_notes["last_state"] = last_state
        fallback_notes["first_last_mode"] = first_last_mode

    return {
        "scene_id": scene_id,
        "route": route,
        "photo_prompt": _enrich_prompt_with_anchor(photo_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
        "video_prompt": _enrich_prompt_with_anchor(video_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
        "positive_video_prompt": _enrich_prompt_with_anchor(
            positive_video_prompt or video_prompt,
            anchors["identity_anchor"],
            anchors["world_anchor"],
        ),
        "negative_video_prompt": negative_video_prompt or _GLOBAL_NEGATIVE_PROMPT,
        "start_image_prompt": _enrich_prompt_with_anchor(start_image_prompt, anchors["identity_anchor"], anchors["world_anchor"])
        if route == "first_last"
        else "",
        "end_image_prompt": _enrich_prompt_with_anchor(end_image_prompt, anchors["identity_anchor"], anchors["world_anchor"])
        if route == "first_last"
        else "",
        "negative_prompt": (negative_video_prompt or _GLOBAL_NEGATIVE_PROMPT),
        "prompt_notes": fallback_notes,
    }


def _normalize_scene_prompts(
    package: dict[str, Any],
    raw: dict[str, Any],
    *,
    scene_rows: list[dict[str, Any]],
    role_lookup: dict[str, dict[str, Any]],
    scene_contract_lookup: dict[str, dict[str, Any]],
    global_contract: dict[str, Any],
    story_core: dict[str, Any],
    world_continuity: dict[str, Any],
) -> tuple[dict[str, Any], bool, str, int, int, int, int, int, int, dict[str, Any]]:
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
    route_mismatch_count = 0
    semantic_mismatch_count = 0
    rows_rebuilt_from_scene_plan_count = 0
    positive_negative_leak_stripped_count = 0
    repaired_from_current_package_count = 0
    unrelated_rows_discarded_count = 0
    i2v_template_rebuilt_count = 0
    i2v_unknown_family_fallback_count = 0
    i2v_template_override_applied = False
    i2v_prompt_family_counts = {family: 0 for family in sorted(I2V_MOTION_FAMILIES)}
    fingerprint = _build_package_anchor_fingerprint(package, story_core, world_continuity)

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
        scene_contract = _safe_dict(scene_contract_lookup.get(scene_id))
        fallback_row = _build_fallback_scene_prompts(package, scene, role_row, story_core, world_continuity)
        ownership_binding_inventory = _build_ref_binding_inventory(_safe_dict(package.get("refs_inventory")))
        carried_active_scene = _is_owner_carried_active_scene(scene, role_row, ownership_binding_inventory)
        held_active_scene = _is_owner_held_active_scene(scene, role_row, ownership_binding_inventory)
        anchors = _build_scene_anchor_bundle(
            package=package,
            story_core=story_core,
            role_row=role_row,
            scene_plan_row=scene,
            world_continuity=world_continuity,
        )
        required_world_anchor = str(scene_contract.get("required_world_anchor") or _safe_dict(global_contract.get("persisted_world_state")).get("world_anchor") or "").strip()
        if required_world_anchor:
            anchors["world_anchor"] = required_world_anchor
        required_props = [str(v).strip() for v in _safe_list(scene_contract.get("required_continuity_props")) if str(v).strip()]
        forbidden_actor_ids = {
            str(v).strip()
            for v in [*_safe_list(scene_contract.get("forbidden_actor_ids")), *_safe_list(_safe_dict(global_contract.get("actor_registry")).get("forbidden_actor_ids"))]
            if str(v).strip()
        }
        presence_policy = _safe_dict(scene_contract.get("presence_policy"))
        presence_clause = _presence_policy_clause(presence_policy)

        actual_route = str(base.get("route") or expected_route).strip()
        row_repaired_from_current_package = False
        if base and _row_looks_unrelated_to_current_package(base, fingerprint):
            used_fallback = True
            row_repaired_from_current_package = True
            unrelated_rows_discarded_count += 1
            validation_errors.append(f"unrelated_prompt_row_discarded:{scene_id}")
            base = {}
        route_mismatch, semantic_mismatch = _detect_scene_prompt_contract_mismatch(
            expected_route=expected_route,
            scene_plan_row=scene,
            model_row=base,
        )
        if route_mismatch or semantic_mismatch:
            used_fallback = True
            row_repaired_from_current_package = True
            rows_rebuilt_from_scene_plan_count += 1
            if route_mismatch:
                route_mismatch_count += 1
                validation_errors.append(f"route_mismatch:{scene_id}")
            if semantic_mismatch:
                semantic_mismatch_count += 1
                validation_errors.append(f"semantic_mismatch:{scene_id}")
            base = {}
        actual_route = expected_route

        photo_prompt = str(base.get("photo_prompt") or "").strip()
        if not photo_prompt:
            missing_photo_count += 1
            used_fallback = True
            row_repaired_from_current_package = True
            photo_prompt = str(fallback_row.get("photo_prompt") or "")

        video_prompt = str(base.get("video_prompt") or "").strip()
        positive_video_prompt = str(base.get("positive_video_prompt") or "").strip()
        negative_video_prompt = str(base.get("negative_video_prompt") or "").strip()
        if not video_prompt:
            missing_video_count += 1
            used_fallback = True
            row_repaired_from_current_package = True
            video_prompt = str(fallback_row.get("video_prompt") or "")

        if actual_route == "first_last":
            positive_video_prompt = positive_video_prompt or video_prompt or str(fallback_row.get("positive_video_prompt") or "")
            video_prompt = positive_video_prompt or video_prompt
            negative_video_prompt = (
                negative_video_prompt
                or str(base.get("negative_prompt") or "").strip()
                or str(fallback_row.get("negative_video_prompt") or "").strip()
                or _FIRST_LAST_NEGATIVE_PROMPT
            )
            negative_prompt = negative_video_prompt
        elif actual_route == "ia2v":
            negative_video_prompt = negative_video_prompt or str(base.get("negative_prompt") or "").strip() or _LIP_SYNC_NEGATIVE_PROMPT
            positive_video_prompt = positive_video_prompt or video_prompt
            negative_prompt = negative_video_prompt
        else:
            positive_video_prompt = positive_video_prompt or video_prompt
            negative_video_prompt = negative_video_prompt or str(base.get("negative_prompt") or "").strip() or _GLOBAL_NEGATIVE_PROMPT
            negative_prompt = negative_video_prompt
        if carried_active_scene and "close to body" not in video_prompt.lower():
            video_prompt = (
                f"{video_prompt} Keep the same owner-bound carried object close to body across transit/evasion/release beats, "
                "even when it is not the frame center; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
            ).strip()
        if held_active_scene and "owner-bound held object" not in video_prompt.lower():
            video_prompt = (
                f"{video_prompt} Keep the same owner-bound held object continuous across transit/evasion/release beats, "
                "with readable handling only; one hand/handling attention remains committed so posture, pace, and route decisions stay constrained, "
                "and this is not a replaceable random prop even when off center."
            ).strip()
        video_prompt, video_sanitized = _sanitize_positive_prompt(video_prompt, negative_prompt)
        if carried_active_scene and "close to body" not in positive_video_prompt.lower():
            positive_video_prompt = (
                f"{(positive_video_prompt or video_prompt)} Keep the same owner-bound carried object close to body across transit/evasion/release beats, "
                "even when it is not the frame center; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
            ).strip()
        if held_active_scene and "owner-bound held object" not in positive_video_prompt.lower():
            positive_video_prompt = (
                f"{(positive_video_prompt or video_prompt)} Keep the same owner-bound held object continuous across transit/evasion/release beats, "
                "with readable handling only; one hand/handling attention remains committed so posture, pace, and route decisions stay constrained, "
                "and this is not a replaceable random prop even when off center."
            ).strip()
        positive_video_prompt, positive_sanitized = _sanitize_positive_prompt(positive_video_prompt or video_prompt, negative_prompt)
        if video_sanitized:
            positive_negative_leak_stripped_count += 1
        if positive_sanitized:
            positive_negative_leak_stripped_count += 1
        if not (
            str(base.get("negative_prompt") or "").strip() or str(base.get("negative_video_prompt") or "").strip()
        ):
            used_fallback = True

        prompt_notes = _safe_dict(base.get("prompt_notes"))
        normalized_notes = _prompt_notes_template(actual_route)
        normalized_notes.update(
            {
                "shot_intent": str(prompt_notes.get("shot_intent") or fallback_row["prompt_notes"].get("shot_intent") or ""),
                "continuity_anchor": str(
                    prompt_notes.get("continuity_anchor") or fallback_row["prompt_notes"].get("continuity_anchor") or ""
                ) + ("; same owner-bound carried object stays close to body through transit/evasion/release (not a replaceable random prop)" if carried_active_scene else ""),
                "world_anchor": str(prompt_notes.get("world_anchor") or fallback_row["prompt_notes"].get("world_anchor") or ""),
                "identity_anchor": str(prompt_notes.get("identity_anchor") or fallback_row["prompt_notes"].get("identity_anchor") or ""),
                "lighting_anchor": str(prompt_notes.get("lighting_anchor") or fallback_row["prompt_notes"].get("lighting_anchor") or ""),
                "motion_safety": str(prompt_notes.get("motion_safety") or fallback_row["prompt_notes"].get("motion_safety") or ""),
                "audio_driven": bool(prompt_notes.get("audio_driven")) if "audio_driven" in prompt_notes else (actual_route == "ia2v"),
                "shot_scale": str(prompt_notes.get("shot_scale") or scene.get("shot_scale") or fallback_row["prompt_notes"].get("shot_scale") or ""),
                "camera_intimacy": str(prompt_notes.get("camera_intimacy") or scene.get("camera_intimacy") or fallback_row["prompt_notes"].get("camera_intimacy") or ""),
                "performance_openness": str(
                    prompt_notes.get("performance_openness")
                    or scene.get("performance_openness")
                    or fallback_row["prompt_notes"].get("performance_openness")
                    or ""
                ),
                "visual_event_type": str(prompt_notes.get("visual_event_type") or scene.get("visual_event_type") or fallback_row["prompt_notes"].get("visual_event_type") or ""),
                "repeat_variation_rule": str(
                    prompt_notes.get("repeat_variation_rule")
                    or scene.get("repeat_variation_rule")
                    or fallback_row["prompt_notes"].get("repeat_variation_rule")
                    or ""
                ),
                "motion_risk": _safe_dict(prompt_notes.get("motion_risk")) or _safe_dict(scene.get("motion_risk")) or _safe_dict(fallback_row["prompt_notes"].get("motion_risk")),
            }
        )
        if held_active_scene:
            normalized_notes["continuity_anchor"] = (
                f"{normalized_notes['continuity_anchor']}; same owner-bound held object persists across transit/evasion/release with readable handling continuity (not replaceable, survives off-center framing)"
            ).strip("; ")
        if actual_route == "ia2v":
            normalized_notes["audio_driven"] = True
        if actual_route == "first_last":
            start_image_prompt = str(base.get("start_image_prompt") or "").strip() or str(fallback_row.get("start_image_prompt") or "").strip()
            end_image_prompt = str(base.get("end_image_prompt") or "").strip() or str(fallback_row.get("end_image_prompt") or "").strip()
            first_state = str(
                prompt_notes.get("first_state") or fallback_row["prompt_notes"].get("first_state") or "start of one controlled action"
            ).strip()
            last_state = str(
                prompt_notes.get("last_state") or fallback_row["prompt_notes"].get("last_state") or "completion of the same controlled action"
            ).strip()
            delta_scene_row = dict(scene)
            delta_scene_row["first_state"] = first_state
            delta_scene_row["last_state"] = last_state
            attached_prop_token = _detect_attached_prop_token(
                start_image_prompt,
                end_image_prompt,
                first_state,
                last_state,
                str(scene.get("transition_action") or ""),
                str(scene.get("scene_goal") or ""),
                str(scene.get("frame_description") or ""),
                photo_prompt,
                str(scene.get("scene_function") or ""),
            )
            first_state, last_state, visual_delta = _build_first_last_visual_delta(
                scene_plan_row=delta_scene_row,
                primary_role=_build_human_subject_label(role_row, story_core, scene),
                attached_prop_token=attached_prop_token,
            )
            first_last_mode = str(scene.get("first_last_mode") or prompt_notes.get("first_last_mode") or "").strip().lower()
            if first_last_mode not in FIRST_LAST_MODES:
                first_last_mode = "camera_settle"
            strict_start, strict_end, strict_positive, strict_negative = _build_first_last_prompt_pair(
                primary_role=_build_human_subject_label(role_row, story_core, scene),
                scene_space=_trim_sentence(str(world_continuity.get("environment_family") or "the same fixed scene space"), max_len=90),
                first_state=first_state,
                last_state=last_state,
                visual_delta=visual_delta,
                attached_prop_token=attached_prop_token,
                first_last_mode=first_last_mode,
            )
            start_image_prompt = strict_start
            end_image_prompt = strict_end
            positive_video_prompt = strict_positive
            video_prompt = strict_positive
            negative_video_prompt = strict_negative
            negative_prompt = strict_negative
            normalized_notes["transition_contract"] = "controlled_micro_transition"
            normalized_notes["first_state"] = first_state
            normalized_notes["last_state"] = last_state
            normalized_notes["first_last_mode"] = first_last_mode
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
            normalized_notes["one_transition_only"] = True
            normalized_notes["prop_attachment_required"] = bool(attached_prop_token)
            normalized_notes["attached_prop"] = attached_prop_token
            if not start_image_prompt or not end_image_prompt:
                used_fallback = True
                validation_errors.append(f"first_last_image_prompt_missing:{scene_id}")
        else:
            start_image_prompt = ""
            end_image_prompt = ""
        if actual_route == "i2v":
            family = str(scene.get("i2v_motion_family") or "").strip()
            if family not in I2V_MOTION_FAMILIES:
                family = "baseline_forward_walk"
                i2v_unknown_family_fallback_count += 1
            i2v_prompt_family_counts[family] = i2v_prompt_family_counts.get(family, 0) + 1
            i2v_scene_row = dict(scene)
            i2v_scene_row["i2v_motion_family"] = family
            bundle = _build_i2v_prompt_bundle(
                role_label=_build_human_subject_label(role_row, story_core, scene),
                scene_plan_row=i2v_scene_row,
                world_anchor=anchors["world_anchor"],
                lighting_anchor=anchors["lighting_anchor"],
                identity_anchor=anchors["identity_anchor"],
            )
            photo_prompt = str(bundle.get("photo_prompt") or photo_prompt)
            video_prompt = str(bundle.get("video_prompt") or video_prompt)
            positive_video_prompt = str(bundle.get("positive_video_prompt") or video_prompt)
            negative_video_prompt = str(bundle.get("negative_video_prompt") or negative_video_prompt or _GLOBAL_NEGATIVE_PROMPT)
            negative_prompt = str(bundle.get("negative_prompt") or negative_video_prompt or _GLOBAL_NEGATIVE_PROMPT)
            normalized_notes["i2v_motion_family"] = family
            normalized_notes["pace_class"] = str(scene.get("pace_class") or "purposeful")
            normalized_notes["camera_pattern"] = str(scene.get("camera_pattern") or "stable_follow")
            normalized_notes["reveal_target"] = str(scene.get("reveal_target") or "none")
            normalized_notes["parallax_required"] = bool(scene.get("parallax_required"))
            normalized_notes["allow_head_turn"] = bool(scene.get("allow_head_turn"))
            normalized_notes["allow_simple_hand_motion"] = bool(scene.get("allow_simple_hand_motion", True))
            normalized_notes["forbid_complex_hand_motion"] = bool(scene.get("forbid_complex_hand_motion", True))
            normalized_notes["forbid_slow_motion_feel"] = bool(scene.get("forbid_slow_motion_feel", True))
            normalized_notes["forbid_bullet_time"] = bool(scene.get("forbid_bullet_time", True))
            normalized_notes["forbid_stylized_action"] = bool(scene.get("forbid_stylized_action", True))
            normalized_notes["require_real_time_pacing"] = bool(scene.get("require_real_time_pacing", True))
            normalized_notes["max_camera_intensity"] = str(scene.get("max_camera_intensity") or "low")
            normalized_notes["i2v_prompt_duration_hint_sec"] = _round3(scene.get("i2v_prompt_duration_hint_sec"))
            normalized_notes["template_built"] = True
            i2v_template_rebuilt_count += 1
            i2v_template_override_applied = True
        if carried_active_scene and "close to body" not in video_prompt.lower():
            video_prompt = (
                f"{video_prompt} Keep the same owner-bound carried object close to body across transit/evasion/release beats, "
                "even when it is not the frame center; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
            ).strip()
        if held_active_scene and "owner-bound held object" not in video_prompt.lower():
            video_prompt = (
                f"{video_prompt} Keep the same owner-bound held object continuous across transit/evasion/release beats, "
                "with readable handling only; one hand/handling attention remains committed so posture, pace, and route decisions stay constrained, "
                "and this is not a replaceable random prop even when off center."
            ).strip()
        video_prompt, video_sanitized = _sanitize_positive_prompt(video_prompt, negative_prompt)
        if carried_active_scene and "close to body" not in positive_video_prompt.lower():
            positive_video_prompt = (
                f"{(positive_video_prompt or video_prompt)} Keep the same owner-bound carried object close to body across transit/evasion/release beats, "
                "even when it is not the frame center; it affects posture, pace, concealment, and route, and is not a replaceable random prop."
            ).strip()
        if held_active_scene and "owner-bound held object" not in positive_video_prompt.lower():
            positive_video_prompt = (
                f"{(positive_video_prompt or video_prompt)} Keep the same owner-bound held object continuous across transit/evasion/release beats, "
                "with readable handling only; one hand/handling attention remains committed so posture, pace, and route decisions stay constrained, "
                "and this is not a replaceable random prop even when off center."
            ).strip()
        positive_video_prompt, positive_sanitized = _sanitize_positive_prompt(positive_video_prompt or video_prompt, negative_prompt)
        photo_prompt, photo_sanitized = _sanitize_positive_prompt(photo_prompt, negative_prompt)
        if video_sanitized:
            positive_negative_leak_stripped_count += 1
        if positive_sanitized:
            positive_negative_leak_stripped_count += 1
        if photo_sanitized:
            positive_negative_leak_stripped_count += 1
        if actual_route == "first_last":
            start_image_prompt, start_sanitized = _sanitize_positive_prompt(start_image_prompt, negative_prompt)
            end_image_prompt, end_sanitized = _sanitize_positive_prompt(end_image_prompt, negative_prompt)
            if start_sanitized:
                positive_negative_leak_stripped_count += 1
            if end_sanitized:
                positive_negative_leak_stripped_count += 1
        if _is_high_motion_risk(scene) and actual_route in {"i2v", "ia2v"}:
            if actual_route == "i2v":
                simplify_suffix = (
                    " Keep one readable motion line only. Avoid complex hand choreography and fine-motor prop action."
                )
                video_prompt = f"{video_prompt.rstrip('. ')}.{simplify_suffix}".strip()
                positive_video_prompt = f"{positive_video_prompt.rstrip('. ')}.{simplify_suffix}".strip()
            else:
                simplified_video = (
                    "Single readable motion line only: controlled gaze/head/shoulder shift, minimal hand emphasis, no tiny finger choreography near face, no wearable-adjustment micro detail, no multistep prop manipulation. "
                    "Prefer camera settle/push/pull with continuity-first behavior."
                )
                video_prompt = simplified_video
                positive_video_prompt = simplified_video
            normalized_notes["risk_simplified"] = True
        else:
            normalized_notes["risk_simplified"] = False

        hard_constraints = _safe_dict(global_contract.get("hard_constraints"))
        image_contract_clauses: list[str] = []
        if required_world_anchor:
            image_contract_clauses.append("Keep required world anchor continuity; no world-family drift")
        if required_props:
            props_clause = " Keep required continuity prop identity consistent across frames; do not replace with new key prop."
            video_prompt = f"{video_prompt.rstrip('. ')}.{props_clause}".strip()
            positive_video_prompt = f"{(positive_video_prompt or video_prompt).rstrip('. ')}.{props_clause}".strip()
            image_contract_clauses.append("Keep required continuity prop identity consistent; do not replace key continuity props")
            normalized_notes["continuity_anchor"] = (
                f"{normalized_notes.get('continuity_anchor', '').strip('; ')}; required continuity props: {', '.join(required_props)}"
            ).strip("; ")
        if forbidden_actor_ids and bool(hard_constraints.get("must_not_invent_cast", True)):
            cast_clause = " Do not introduce extra identifiable cast; keep only contract-authorized actors."
            video_prompt = f"{video_prompt.rstrip('. ')}.{cast_clause}".strip()
            positive_video_prompt = f"{(positive_video_prompt or video_prompt).rstrip('. ')}.{cast_clause}".strip()
            image_contract_clauses.append("Do not introduce extra identifiable cast; keep only contract-authorized actors")
            normalized_notes["cast_constraint"] = "must_not_invent_cast"
        if presence_clause:
            video_prompt = f"{video_prompt.rstrip('. ')}. {presence_clause}".strip()
            positive_video_prompt = f"{(positive_video_prompt or video_prompt).rstrip('. ')}. {presence_clause}".strip()
            image_contract_clauses.append(presence_clause)
            normalized_notes["presence_policy"] = str(presence_policy.get("presence_policy") or "")
            normalized_notes["presence_clause"] = presence_clause
        photo_prompt = _append_compact_clauses(photo_prompt, image_contract_clauses)
        if actual_route == "first_last":
            start_image_prompt = _append_compact_clauses(start_image_prompt, image_contract_clauses)
            end_image_prompt = _append_compact_clauses(end_image_prompt, image_contract_clauses)
        if bool(scene_contract.get("allow_scene_local_props", True)):
            normalized_notes["scene_local_props_policy"] = "decor_allowed_non_continuity"
        else:
            normalized_notes["scene_local_props_policy"] = "decor_restricted"

        scene_out = {
            "scene_id": scene_id,
            "route": actual_route,
            "photo_prompt": _enrich_prompt_with_anchor(photo_prompt, anchors["identity_anchor"], anchors["world_anchor"]),
            "video_prompt": (
                video_prompt[:900]
                if actual_route == "first_last"
                else _enrich_prompt_with_anchor(video_prompt, anchors["identity_anchor"], anchors["world_anchor"])
            ),
            "positive_video_prompt": (
                (positive_video_prompt or video_prompt)[:900]
                if actual_route == "first_last"
                else _enrich_prompt_with_anchor(
                    positive_video_prompt or video_prompt,
                    anchors["identity_anchor"],
                    anchors["world_anchor"],
                )
            ),
            "negative_video_prompt": negative_video_prompt,
            "start_image_prompt": (start_image_prompt[:900] if actual_route == "first_last" else ""),
            "end_image_prompt": (end_image_prompt[:900] if actual_route == "first_last" else ""),
            "negative_prompt": negative_prompt,
            "prompt_notes": normalized_notes,
        }
        semantics_lock = _scene_plan_semantics_lock(scene)
        scene_out["route"] = semantics_lock["route"] if semantics_lock["route"] in ALLOWED_ROUTES else actual_route
        scene_out["prompt_notes"].update(semantics_lock)
        scene_out["prompt_notes"]["row_repaired_from_scene_plan"] = bool(row_repaired_from_current_package)
        if row_repaired_from_current_package:
            repaired_from_current_package_count += 1
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
    normalization_diag = {
        "rows_source_count": len(scene_rows),
        "rows_model_count": len(_safe_list(raw.get("scenes"))),
        "rows_normalized_count": len(scenes),
        "repaired_from_current_package_count": repaired_from_current_package_count,
        "unrelated_rows_discarded_count": unrelated_rows_discarded_count,
        "scene_prompts_route_mismatch_count": route_mismatch_count,
        "scene_prompts_semantic_mismatch_count": semantic_mismatch_count,
        "scene_prompts_rows_rebuilt_from_scene_plan_count": rows_rebuilt_from_scene_plan_count,
        "scene_prompts_positive_negative_leak_stripped_count": positive_negative_leak_stripped_count,
        "i2v_template_rebuilt_count": i2v_template_rebuilt_count,
        "i2v_unknown_family_fallback_count": i2v_unknown_family_fallback_count,
        "i2v_prompt_family_counts": i2v_prompt_family_counts,
        "i2v_template_override_applied": i2v_template_override_applied,
        "stage_source": "current_package",
    }
    return (
        normalized,
        used_fallback,
        validation_error,
        missing_photo_count,
        missing_video_count,
        ia2v_audio_driven_count,
        route_mismatch_count,
        semantic_mismatch_count,
        rows_rebuilt_from_scene_plan_count,
        positive_negative_leak_stripped_count,
        normalization_diag,
    )


def build_gemini_scene_prompts(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    context, aux = _build_compact_context(package)
    scene_rows = _safe_list(aux.get("scene_rows"))
    role_lookup = _safe_dict(aux.get("role_lookup"))
    scene_contract_lookup, global_contract = _build_scene_contract_lookup(_safe_dict(aux.get("compiled_contract")))

    used_model = "gemini-3-flash-preview"
    model_id = str(_safe_dict(context.get("video_capability_canon")).get("model_id") or DEFAULT_VIDEO_MODEL_ID)
    capability_diag = build_capability_diagnostics_summary(
        model_id=model_id,
        route_type="mixed",
        story_core_guard_applied=False,
        scene_plan_guard_applied=False,
        prompt_guard_applied=True,
    )

    diagnostics = {
        "prompt_version": SCENE_PROMPTS_PROMPT_VERSION,
        "used_model": used_model,
        "scene_count": len(scene_rows),
        "missing_photo_count": 0,
        "missing_video_count": 0,
        "ia2v_audio_driven_count": 0,
        "scene_prompts_route_mismatch_count": 0,
        "scene_prompts_semantic_mismatch_count": 0,
        "scene_prompts_rows_rebuilt_from_scene_plan_count": 0,
        "scene_prompts_positive_negative_leak_stripped_count": 0,
        "scene_prompts_route_semantics_mismatch_count": 0,
        "i2v_template_rebuilt_count": 0,
        "i2v_unknown_family_fallback_count": 0,
        "i2v_prompt_family_counts": {},
        "i2v_template_override_applied": False,
        **capability_diag,
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

        parsed = _coerce_scene_prompts_payload(_extract_json_obj(_extract_gemini_text(response)))
        (
            scene_prompts,
            used_fallback,
            validation_error,
            missing_photo,
            missing_video,
            ia2v_audio_driven,
            route_mismatch_count,
            semantic_mismatch_count,
            rows_rebuilt_from_scene_plan_count,
            positive_negative_leak_stripped_count,
            normalization_diag,
        ) = _normalize_scene_prompts(
            package,
            parsed,
            scene_rows=scene_rows,
            role_lookup=role_lookup,
            scene_contract_lookup=scene_contract_lookup,
            global_contract=global_contract,
            story_core=_safe_dict(aux.get("story_core")),
            world_continuity=_safe_dict(aux.get("world_continuity")),
        )
        diagnostics.update(
            {
                "missing_photo_count": int(missing_photo),
                "missing_video_count": int(missing_video),
                "ia2v_audio_driven_count": int(ia2v_audio_driven),
                "scene_prompts_route_mismatch_count": int(route_mismatch_count),
                "scene_prompts_semantic_mismatch_count": int(semantic_mismatch_count),
                "scene_prompts_rows_rebuilt_from_scene_plan_count": int(rows_rebuilt_from_scene_plan_count),
                "scene_prompts_positive_negative_leak_stripped_count": int(positive_negative_leak_stripped_count),
                "scene_prompts_route_semantics_mismatch_count": int(route_mismatch_count + semantic_mismatch_count),
                "i2v_template_rebuilt_count": int(normalization_diag.get("i2v_template_rebuilt_count") or 0),
                "i2v_unknown_family_fallback_count": int(normalization_diag.get("i2v_unknown_family_fallback_count") or 0),
                "i2v_prompt_family_counts": _safe_dict(normalization_diag.get("i2v_prompt_family_counts")),
                "i2v_template_override_applied": bool(normalization_diag.get("i2v_template_override_applied")),
                "rows_source_count": int(normalization_diag.get("rows_source_count") or 0),
                "rows_model_count": int(normalization_diag.get("rows_model_count") or 0),
                "rows_normalized_count": int(normalization_diag.get("rows_normalized_count") or 0),
                "repaired_from_current_package_count": int(normalization_diag.get("repaired_from_current_package_count") or 0),
                "unrelated_rows_discarded_count": int(normalization_diag.get("unrelated_rows_discarded_count") or 0),
                "stage_source": str(normalization_diag.get("stage_source") or "current_package"),
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
            route_mismatch_count,
            semantic_mismatch_count,
            rows_rebuilt_from_scene_plan_count,
            positive_negative_leak_stripped_count,
            normalization_diag,
        ) = _normalize_scene_prompts(
            package,
            {},
            scene_rows=scene_rows,
            role_lookup=role_lookup,
            scene_contract_lookup=scene_contract_lookup,
            global_contract=global_contract,
            story_core=_safe_dict(aux.get("story_core")),
            world_continuity=_safe_dict(aux.get("world_continuity")),
        )
        diagnostics.update(
            {
                "missing_photo_count": int(missing_photo),
                "missing_video_count": int(missing_video),
                "ia2v_audio_driven_count": int(ia2v_audio_driven),
                "scene_prompts_route_mismatch_count": int(route_mismatch_count),
                "scene_prompts_semantic_mismatch_count": int(semantic_mismatch_count),
                "scene_prompts_rows_rebuilt_from_scene_plan_count": int(rows_rebuilt_from_scene_plan_count),
                "scene_prompts_positive_negative_leak_stripped_count": int(positive_negative_leak_stripped_count),
                "scene_prompts_route_semantics_mismatch_count": int(route_mismatch_count + semantic_mismatch_count),
                "i2v_template_rebuilt_count": int(normalization_diag.get("i2v_template_rebuilt_count") or 0),
                "i2v_unknown_family_fallback_count": int(normalization_diag.get("i2v_unknown_family_fallback_count") or 0),
                "i2v_prompt_family_counts": _safe_dict(normalization_diag.get("i2v_prompt_family_counts")),
                "i2v_template_override_applied": bool(normalization_diag.get("i2v_template_override_applied")),
                "rows_source_count": int(normalization_diag.get("rows_source_count") or 0),
                "rows_model_count": int(normalization_diag.get("rows_model_count") or 0),
                "rows_normalized_count": int(normalization_diag.get("rows_normalized_count") or 0),
                "repaired_from_current_package_count": int(normalization_diag.get("repaired_from_current_package_count") or 0),
                "unrelated_rows_discarded_count": int(normalization_diag.get("unrelated_rows_discarded_count") or 0),
                "stage_source": str(normalization_diag.get("stage_source") or "current_package"),
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
