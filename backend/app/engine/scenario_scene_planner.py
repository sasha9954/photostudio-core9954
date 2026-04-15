from __future__ import annotations

import json
from typing import Any

from app.engine.gemini_rest import post_generate_content
from app.engine.scenario_story_guidance import story_guidance_route_mix_doctrine, story_guidance_to_notes_list
from app.engine.video_capability_canon import (
    DEFAULT_VIDEO_MODEL_ID,
    build_capability_diagnostics_summary,
    get_capability_rules_source_version,
    get_first_last_pairing_rules,
    get_lipsync_rules,
    get_scene_grammar_hints,
    get_video_model_capability_profile,
)

SCENE_PLAN_PROMPT_VERSION = "scene_plan_v1"
SCENE_PLAN_MODEL = "gemini-3.1-pro-preview"
SCENES_VERSION = "1.1"
ALLOWED_ROUTES = {"i2v", "ia2v", "first_last"}
ALLOWED_PACING = {"fluid", "staccato", "stable"}
ALLOWED_ENERGY_ALIGNMENT = {"match", "counterpoint", "build_against", "release_after"}
ALLOWED_FRAMING = {"close_up", "medium", "wide", "detail", "silhouette", "overhead"}
ALLOWED_SUBJECT_PRIORITY = {"hero", "ensemble", "object", "environment"}
ALLOWED_LAYOUT = {"centered", "rule_of_thirds", "off_balance", "symmetrical"}
ALLOWED_DEPTH_STRATEGY = {"flat", "layered", "deep"}
SCENES_FORBIDDEN_LEAK_TOKENS = {
    "8k",
    "cinematic quality",
    "highly detailed",
    "masterpiece",
    "fps",
    "lens",
    "seed",
    "sampler",
    "positive_prompt",
    "negative_prompt",
    "workflow",
    "ltx",
    "renderer_family",
}
FIRST_LAST_MODES = {
    "push_in_emotional",
    "pull_back_release",
    "small_side_arc",
    "reveal_face_from_shadow",
    "foreground_parallax",
    "camera_settle",
    "visibility_reveal",
}
TRANSIT_I2V_FAMILIES = {"baseline_forward_walk", "side_tracking_walk", "push_in_follow"}
TRANSIT_LIKE_VISUAL_EVENTS = {
    "transit",
    "environment",
    "character_movement",
    "environment_reveal",
    "threshold_crossing",
    "vertical_transition",
}
GENERIC_ENVIRONMENT_FAMILIES = {"urban", "city", "interior", "outdoor"}
TURN_FUNCTION_HINTS = {
    "turn",
    "reveal",
    "payoff",
    "release",
    "callback",
    "climax",
    "afterimage",
    "resolution",
    "drop",
}
FIRST_LAST_EXCLUSION_HINTS = {"transit", "environment_anchor", "location_change", "world_jump", "montage", "travel", "alley", "courtyard", "corner"}
IA2V_ADJACENCY_PENALTY = 9
FIRST_LAST_ADJACENCY_PENALTY = 2
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
CAUTION_MOTION_CANON = (
    "hand to chest",
    "one hand opening outward",
    "wearable silhouette reveal without complex finger choreography",
    "partial body turn with face readability preserved",
    "close prop hold with minimal motion",
    "slight posture reconfiguration",
)
FORBIDDEN_MOTION_CANON = (
    "cap adjustment with fingers as default action",
    "tiny finger choreography near face",
    "multi-step prop manipulation",
    "gripping/regripping small object with finger precision",
    "complex hand choreography around face",
    "fine-motor micro-actions as scene focus",
    "violent spins",
    "high-velocity orbit",
    "jerky dance",
    "flailing arms",
    "complex body choreography",
    "drastic perspective reconstruction",
    "180-270 degree orbit around subject as standard move",
)
I2V_MOTION_FAMILIES = {
    "push_in_follow",
    "side_tracking_walk",
    "look_reveal_follow",
    "baseline_forward_walk",
    "tension_head_turn",
    "pull_back_release",
}
I2V_PROMPT_DURATION_HINT_RANGE: dict[str, tuple[float, float]] = {
    "push_in_follow": (3.8, 4.2),
    "side_tracking_walk": (4.0, 4.5),
    "look_reveal_follow": (4.5, 5.0),
    "tension_head_turn": (3.8, 4.2),
    "pull_back_release": (4.5, 5.0),
    "baseline_forward_walk": (4.0, 4.5),
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


def _clamp_ratio(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except Exception:
        return float(default)


def _normalize_creative_config(raw_config: Any) -> dict[str, Any]:
    row = _safe_dict(raw_config)
    route_mix_mode = str(row.get("route_mix_mode") or row.get("routeMixMode") or "auto").strip().lower() or "auto"
    if route_mix_mode not in {"auto", "custom"}:
        route_mix_mode = "auto"
    lipsync_ratio = _clamp_ratio(row.get("lipsync_ratio"), 0.25)
    first_last_ratio = _clamp_ratio(row.get("first_last_ratio"), 0.25)
    i2v_ratio = max(0.0, 1.0 - lipsync_ratio - first_last_ratio)
    try:
        max_consecutive_lipsync = int(row.get("max_consecutive_lipsync"))
    except Exception:
        max_consecutive_lipsync = 2
    max_consecutive_lipsync = max(1, min(6, max_consecutive_lipsync))
    return {
        "route_mix_mode": route_mix_mode,
        "lipsync_ratio": round(lipsync_ratio, 3),
        "first_last_ratio": round(first_last_ratio, 3),
        "i2v_ratio": round(i2v_ratio, 3),
        "max_consecutive_lipsync": max_consecutive_lipsync,
        "preferred_routes": [str(item).strip().lower() for item in _safe_list(row.get("preferred_routes")) if str(item).strip()],
    }


def _compact_scene_row(row: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "scene_id": str(row.get("scene_id") or ""),
        "t0": _round3(row.get("t0")),
        "t1": _round3(row.get("t1")),
        "duration_sec": _round3(row.get("duration_sec")),
        "route": str(row.get("route") or ""),
        "route_reason": str(row.get("route_reason") or ""),
        "emotional_intent": str(row.get("emotional_intent") or ""),
        "motion_intent": str(row.get("motion_intent") or ""),
        "watchability_role": str(row.get("watchability_role") or ""),
        "shot_scale": str(row.get("shot_scale") or ""),
        "camera_intimacy": str(row.get("camera_intimacy") or ""),
        "visual_event_type": str(row.get("visual_event_type") or ""),
    }

    if str(row.get("route") or "") == "first_last":
        first_last_mode = str(row.get("first_last_mode") or "").strip()
        if first_last_mode:
            compact["first_last_mode"] = first_last_mode

    route_validation_status = str(row.get("route_validation_status") or "").strip().lower() or "ok"
    if route_validation_status != "ok":
        compact["route_validation_status"] = route_validation_status

    route_validation_reason = str(row.get("route_validation_reason") or "").strip()
    if route_validation_reason:
        compact["route_validation_reason"] = route_validation_reason

    suggested_route = str(row.get("suggested_route") or "").strip()
    if suggested_route:
        compact["suggested_route"] = suggested_route

    for warnings_key in ("capability_warnings", "continuity_warnings", "anti_repeat_warnings"):
        warnings = [str(item).strip() for item in _safe_list(row.get(warnings_key)) if str(item).strip()]
        if warnings:
            compact[warnings_key] = warnings
    return compact


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
                "label": str(row.get("source_label") or row.get("value") or key).strip()[:120],
                "ownershipRoleMapped": meta["ownershipRoleMapped"],
                "bindingType": meta["bindingType"],
            }
        )
    return out[:16]


def _is_world_bound_binding_row(row: dict[str, Any]) -> bool:
    ref_id = str(row.get("ref_id") or "").strip().lower()
    owner = str(row.get("ownershipRoleMapped") or "").strip().lower()
    binding = str(row.get("bindingType") or "").strip().lower()
    if owner == "environment":
        return True
    if ref_id in {"ref_world", "ref_location", "ref_props", "ref_style"}:
        return True
    if ref_id in {"ref_character_1", "ref_character_2", "ref_character_3"}:
        return False
    return binding == "environment"


def _resolve_active_video_model_id(package: dict[str, Any]) -> str:
    input_pkg = _safe_dict(package.get("input"))
    for key in ("video_model", "video_model_id", "model_id"):
        value = str(input_pkg.get(key) or "").strip().lower()
        if value:
            return value
    return DEFAULT_VIDEO_MODEL_ID


def _scene_plan_debug_enabled(package: dict[str, Any]) -> bool:
    input_pkg = _safe_dict(package.get("input"))
    for key in ("scene_plan_debug", "scene_plan_expanded", "debug_scene_plan", "debug"):
        value = input_pkg.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if str(value or "").strip().lower() in {"1", "true", "yes", "on", "debug", "verbose"}:
            return True
    return False


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
    # Transitional bridge input: scene_candidate_windows remains legacy until full segment_id-first SCENES flow.
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


def _build_scene_segment_rows(
    audio_map: dict[str, Any],
    story_core: dict[str, Any],
    role_plan: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    audio_segments = [_safe_dict(row) for row in _safe_list(audio_map.get("segments"))]
    core_rows = {_safe_dict(row).get("segment_id"): _safe_dict(row) for row in _safe_list(story_core.get("narrative_segments"))}
    cast_rows = {_safe_dict(row).get("segment_id"): _safe_dict(row) for row in _safe_list(role_plan.get("scene_casting"))}

    normalized: list[dict[str, Any]] = []
    missing_core_source_segments: list[str] = []
    for idx, segment in enumerate(audio_segments, start=1):
        segment_id = str(segment.get("segment_id") or f"seg_{idx}").strip()
        core_raw = core_rows.get(segment_id)
        if not isinstance(core_raw, dict):
            missing_core_source_segments.append(segment_id)
        core = _safe_dict(core_raw)
        cast = _safe_dict(cast_rows.get(segment_id))
        t0 = _round3(segment.get("t0"))
        t1 = _round3(segment.get("t1"))
        normalized.append(
            {
                "segment_id": segment_id,
                "scene_id": segment_id,
                "t0": t0,
                "t1": t1,
                "duration_sec": _round3(segment.get("duration_sec") or max(0.0, t1 - t0)),
                "transcript_slice": str(segment.get("transcript_slice") or segment.get("text") or "").strip(),
                "rhythmic_anchor": str(segment.get("rhythmic_anchor") or "").strip(),
                "intensity": str(segment.get("intensity") or "").strip(),
                "arc_role": str(core.get("arc_role") or "").strip(),
                "beat_purpose": str(core.get("beat_purpose") or "").strip(),
                "emotional_key": str(core.get("emotional_key") or "").strip(),
                "primary_role": str(cast.get("primary_role") or "").strip(),
                "secondary_roles": [str(v).strip() for v in _safe_list(cast.get("secondary_roles")) if str(v).strip()],
                "presence_mode": str(cast.get("presence_mode") or "").strip(),
                "presence_weight": str(cast.get("presence_weight") or "").strip(),
                "performance_focus": bool(cast.get("performance_focus")),
            }
        )
    return normalized, list(dict.fromkeys(missing_core_source_segments))


def _build_scene_role_lookup(role_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    scene_casting = _safe_list(role_plan.get("scene_casting"))
    if scene_casting:
        for row_raw in scene_casting:
            row = _safe_dict(row_raw)
            segment_id = str(row.get("segment_id") or "").strip()
            if segment_id:
                lookup[segment_id] = {
                    "scene_id": segment_id,
                    "segment_id": segment_id,
                    "primary_role": str(row.get("primary_role") or "").strip(),
                    "secondary_roles": _safe_list(row.get("secondary_roles")),
                    "scene_presence_mode": str(row.get("presence_mode") or "").strip(),
                    "presence_weight": str(row.get("presence_weight") or "").strip(),
                    "performance_focus": str(row.get("performance_focus") or "").strip(),
                    "active_roles": list(
                        dict.fromkeys(
                            [
                                str(row.get("primary_role") or "").strip(),
                                *[str(role).strip() for role in _safe_list(row.get("secondary_roles")) if str(role).strip()],
                            ]
                        )
                    ),
                }
        return lookup
    for row_raw in _safe_list(role_plan.get("scene_roles")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id:
            lookup[scene_id] = row
    return lookup


def _build_scene_world_summary(role_plan: dict[str, Any], story_core: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    world = _safe_dict(story_core.get("world_lock"))
    if not world:
        world = _safe_dict(role_plan.get("world_continuity"))
    environment_family = str(world.get("environment_family") or "").strip()
    country = str(world.get("country_or_region") or "").strip()
    location_progression = [str(item).strip() for item in _safe_list(world.get("location_progression")) if str(item).strip()]
    style_anchor = str(world.get("style_anchor") or "").strip()
    realism_contract = str(world.get("realism_contract") or "").strip()
    story_summary = str(story_core.get("story_summary") or "").strip()
    opening_anchor = str(story_core.get("opening_anchor") or "").strip()

    is_generic_env = environment_family.lower() in GENERIC_ENVIRONMENT_FAMILIES or len(environment_family) < 5
    strengthened_environment_family = environment_family
    world_planning_summary = environment_family
    used_strengthened_summary = False

    if is_generic_env:
        summary_parts: list[str] = []
        if realism_contract:
            summary_parts.append(realism_contract)
        if country:
            summary_parts.append(f"{country} setting")
        if location_progression:
            summary_parts.append(f"location flow: {' -> '.join(location_progression[:4])}")
        if style_anchor:
            summary_parts.append(style_anchor)
        if opening_anchor:
            summary_parts.append(f"opening anchor: {opening_anchor}")
        if story_summary:
            summary_parts.append(f"story arc: {story_summary}")
        world_planning_summary = "; ".join(summary_parts)[:700] or "grounded contemporary public-to-private progression"
        strengthened_environment_family = world_planning_summary
        used_strengthened_summary = True

    return (
        {
            "environment_family": environment_family,
            "strengthened_environment_family": strengthened_environment_family[:400],
            "world_planning_summary": world_planning_summary,
            "country_or_region": country,
            "location_progression": location_progression,
            "style_anchor": style_anchor,
            "realism_contract": realism_contract,
        },
        used_strengthened_summary,
    )


def _build_scene_planning_context(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    compiled_contract = _safe_dict(role_plan.get("compiled_contract"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    ownership_binding_inventory = _build_ref_binding_inventory(refs_inventory)
    scene_windows = _build_scene_windows(audio_map)
    scene_segment_rows, missing_core_source_segments = _build_scene_segment_rows(audio_map, story_core, role_plan)
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    story_guidance = story_guidance_route_mix_doctrine(story_core.get("story_guidance"))
    world_summary, world_summary_used = _build_scene_world_summary(role_plan, story_core)
    model_id = _resolve_active_video_model_id(package)
    route_capability_profiles = {
        route: {
            "route_type": route,
            "profile": get_video_model_capability_profile(model_id, route),
            "scene_grammar_hints": get_scene_grammar_hints(model_id, route),
        }
        for route in ("i2v", "ia2v", "first_last")
    }

    context = {
        "mode": "clip",
        "content_type": str(input_pkg.get("content_type") or ""),
        "format": str(input_pkg.get("format") or ""),
        "story_core": {
            "story_summary": str(story_core.get("story_summary") or "")[:1200],
            "opening_anchor": str(story_core.get("opening_anchor") or "")[:600],
            "ending_callback_rule": str(story_core.get("ending_callback_rule") or "")[:600],
            "global_arc": str(story_core.get("global_arc") or "")[:600],
            "identity_lock": _safe_dict(story_core.get("identity_lock")),
            "world_lock": _safe_dict(story_core.get("world_lock")),
            "style_lock": _safe_dict(story_core.get("style_lock")),
            "story_guidance": _safe_dict(story_core.get("story_guidance")),
            "route_mix_doctrine_for_scenes": story_guidance,
        },
        "audio_map": {
            "sections": _safe_list(audio_map.get("sections")),
            "segments": scene_segment_rows,
            "scene_windows": scene_windows,
            "cut_policy": _safe_dict(audio_map.get("cut_policy")),
            "audio_dramaturgy": _safe_dict(audio_map.get("audio_dramaturgy")),
        },
        "role_plan": {
            "roles_version": str(role_plan.get("roles_version") or ""),
            "roster": _safe_list(role_plan.get("roster")),
            "scene_casting": _safe_list(role_plan.get("scene_casting")),
            "world_continuity": _safe_dict(story_core.get("world_lock")) or _safe_dict(role_plan.get("world_continuity")),
            "world_summary": world_summary,
            "scene_roles": _safe_list(role_plan.get("scene_roles")),
            "compiled_contract": {
                "global_contract": _safe_dict(compiled_contract.get("global_contract")),
                "scene_contracts": _safe_list(compiled_contract.get("scene_contracts")),
            },
            "role_arc_summary": str(role_plan.get("role_arc_summary") or ""),
            "continuity_notes": story_guidance_to_notes_list(story_core.get("story_guidance"), max_items=8) or _safe_list(role_plan.get("continuity_notes")),
        },
        "ownership_binding_inventory": ownership_binding_inventory,
        "clip_scene_policy": {
            "target_route_mix_for_8_scenes": {"i2v": 4, "ia2v": 2, "first_last": 2},
            "target_route_mix_is_soft_heuristic_only": True,
            "creative_config": creative_config,
            "ia2v_definition": "emotion-first performance shot; readable face/mouth; smooth camera; restrained motion",
            "i2v_definition": "baseline clip route for observation, transit, environment and connective montage scenes",
            "first_last_definition": "explicit state transition A->B for reveal/turn/payoff/release/callback scenes",
            "clip_mode_core_principle": "visual/emotional arc under audio energy, not literal travel-story plot",
            "camera_led_transitions_preferred": True,
            "safe_motion_canon": list(SAFE_MOTION_CANON),
            "caution_motion_canon": list(CAUTION_MOTION_CANON),
            "forbidden_motion_canon": list(FORBIDDEN_MOTION_CANON),
            "wearable_anchor_policy": "wearable continuity anchors are silhouette/look anchors; not default action drivers",
            "first_last_modes": sorted(FIRST_LAST_MODES),
        },
        "video_capability_canon": {
            "model_id": model_id,
            "capability_rules_source_version": get_capability_rules_source_version(),
            "route_profiles": route_capability_profiles,
            "first_last_pairing_rules": get_first_last_pairing_rules(model_id),
            "lipsync_rules": get_lipsync_rules(model_id),
            "usage_policy": {
                "prefer_verified_safe_by_default": True,
                "experimental_is_opt_in_not_default": True,
                "blocked_patterns_must_be_avoided": True,
            },
        },
    }
    aux = {
        "scene_windows": scene_windows,
        "scene_segment_rows": scene_segment_rows,
        "missing_core_source_segments": missing_core_source_segments,
        "role_lookup": _build_scene_role_lookup(role_plan),
        "world_summary_used": world_summary_used,
        "ownership_binding_inventory": ownership_binding_inventory,
        "compiled_contract": compiled_contract,
        "scene_role_source_precedence": ["role_plan.scene_casting", "role_plan.roster", "legacy scene_roles / compiled_contract fallback"],
        # Bridge markers: scene_candidate_windows/compiled_contract are deprecated transition inputs.
        "uses_legacy_scene_candidate_windows_bridge": bool(scene_windows),
        "uses_legacy_compiled_contract_bridge": bool(compiled_contract),
    }
    return context, aux


def _build_scene_contract_lookup(compiled_contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(compiled_contract.get("scene_contracts")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        if scene_id:
            out[scene_id] = row
    return out


def _apply_scene_contract_constraints(
    *,
    scene_row: dict[str, Any],
    role_row: dict[str, Any],
    scene_contract: dict[str, Any],
    global_contract: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    out = dict(scene_row)
    warnings: list[str] = []
    contract = _safe_dict(scene_contract)
    actor_registry = _safe_dict(global_contract.get("actor_registry"))
    hard_constraints = _safe_dict(global_contract.get("hard_constraints"))

    required_actors = [str(v).strip() for v in _safe_list(contract.get("required_actors")) if str(v).strip()]
    forbidden_actor_ids = {
        str(v).strip()
        for v in [*_safe_list(contract.get("forbidden_actor_ids")), *_safe_list(actor_registry.get("forbidden_actor_ids"))]
        if str(v).strip()
    }
    active_roles = [str(v).strip() for v in _safe_list(out.get("active_roles")) if str(v).strip()]
    active_roles = [v for v in active_roles if v not in forbidden_actor_ids]
    if required_actors:
        required_primary = required_actors[0]
        out["primary_role"] = required_primary
        if required_primary not in active_roles:
            active_roles.insert(0, required_primary)
    out["active_roles"] = list(dict.fromkeys(active_roles))

    presence_policy = _safe_dict(contract.get("presence_policy"))
    if presence_policy:
        out["contract_presence_policy"] = presence_policy
        out["scene_presence_mode"] = str(contract.get("scene_presence_mode") or out.get("scene_presence_mode") or "").strip()
        policy = str(presence_policy.get("presence_policy") or "").strip().upper()
        if policy == "STRICT":
            out["visual_event_type"] = "face" if str(out.get("route") or "") == "ia2v" else "character_action"
            warnings.append("contract_presence_strict")

    required_world_anchor = str(contract.get("required_world_anchor") or "").strip()
    if required_world_anchor:
        out["required_world_anchor"] = required_world_anchor
    required_props = [str(v).strip() for v in _safe_list(contract.get("required_continuity_props")) if str(v).strip()]
    if required_props:
        if "props" not in out["active_roles"]:
            out["active_roles"].append("props")
        out["required_continuity_props"] = required_props

    if forbidden_actor_ids:
        out["forbidden_actor_ids"] = sorted(forbidden_actor_ids)
        for forbidden in forbidden_actor_ids:
            if forbidden in out["active_roles"]:
                out["active_roles"] = [r for r in out["active_roles"] if r != forbidden]
                warnings.append(f"forbidden_actor_removed:{forbidden}")

    if bool(hard_constraints.get("must_not_invent_cast")) and forbidden_actor_ids:
        warnings.append("must_not_invent_cast_enforced")
    out["active_roles"] = list(dict.fromkeys(out.get("active_roles") or []))
    return out, warnings


def _build_prompt(context: dict[str, Any], *, validation_feedback: str = "") -> str:
    feedback_block = ""
    if validation_feedback:
        feedback_block = (
            "PREVIOUS OUTPUT FAILED ROUTE-BUDGET VALIDATION.\n"
            f"Fix exactly: {validation_feedback}\n"
        )
    return (
        "You are SCENES stage only.\\n"
        "Return STRICT JSON only. No markdown, no prose.\\n"
        "Return one storyboard row per segment_id from audio_map.segments.\\n"
        "Do not invent or remove segments.\\n"
        "Do not mutate cast. Use role_plan.scene_casting/roster as cast source; compiled_contract is legacy fallback only.\\n"
        "Do not output prompt language, quality buzzwords, renderer parameters, API/workflow payload, or final video payload.\\n"
        "Do not use raw director text as free authoring source beyond this package context.\\n"
        "Use story_core doctrine as guidance only; do not change doctrine.\\n"
        "Use technical capability canon only as allowed/discouraged/unstable route behavior context.\\n"
        "Use route baseline only as capability context if available, not as prompt text.\\n"
        "Do not change timing grid. segment_id/t0/t1/duration are fixed by input segments.\\n"
        "Allowed enums:\\n"
        "- route: i2v|ia2v|first_last\\n"
        "- pacing: fluid|staccato|stable\\n"
        "- energy_alignment: match|counterpoint|build_against|release_after\\n"
        "- framing: close_up|medium|wide|detail|silhouette|overhead\\n"
        "- subject_priority: hero|ensemble|object|environment\\n"
        "- layout: centered|rule_of_thirds|off_balance|symmetrical\\n"
        "- depth_strategy: flat|layered|deep\\n"
        f"{feedback_block}"
        "Output contract:\\n"
        "{\\n"
        '  "scenes_version":"1.1",\\n'
        '  "storyboard":[{"segment_id":"seg_1","route":"i2v","route_reason":"","scene_goal":"","narrative_function":"","visual_motion":{"subject_motion":"","camera_intent":"","pacing":"stable","energy_alignment":"match"},"composition":{"framing":"medium","subject_priority":"hero","layout":"centered","depth_strategy":"layered"},"audio_visual_sync":""}]\\n'
        "}\\n\\n"
        f"SCENE_PLANNING_CONTEXT:\\n{json.dumps(_compact_prompt_payload(context), ensure_ascii=False)}"
    )


def _target_route_budget(total_scenes: int) -> dict[str, int]:
    if total_scenes <= 0:
        return {"i2v": 0, "ia2v": 0, "first_last": 0}
    if total_scenes == 8:
        return {"i2v": 4, "ia2v": 2, "first_last": 2}
    if total_scenes == 1:
        return {"i2v": 1, "ia2v": 0, "first_last": 0}
    if total_scenes == 2:
        return {"i2v": 1, "ia2v": 0, "first_last": 1}

    ia2v = max(1, int(round(total_scenes * 0.25)))
    first_last = max(1, int(round(total_scenes * 0.25)))
    i2v = total_scenes - ia2v - first_last
    while i2v < max(1, total_scenes // 2):
        if first_last > 1:
            first_last -= 1
        elif ia2v > 1:
            ia2v -= 1
        else:
            break
        i2v = total_scenes - ia2v - first_last
    if i2v < 1:
        i2v = 1
    overflow = i2v + ia2v + first_last - total_scenes
    while overflow > 0 and first_last > 1:
        first_last -= 1
        overflow -= 1
    while overflow > 0 and ia2v > 1:
        ia2v -= 1
        overflow -= 1
    while overflow > 0 and i2v > 1:
        i2v -= 1
        overflow -= 1
    return {"i2v": i2v, "ia2v": ia2v, "first_last": first_last}


def _is_first_last_candidate(scene: dict[str, Any], idx: int, total: int) -> bool:
    scene_function = str(scene.get("scene_function") or "").strip().lower()
    presence_mode = str(scene.get("scene_presence_mode") or "").strip().lower()
    motion_intent = str(scene.get("motion_intent") or "").strip().lower()
    watchability_role = str(scene.get("watchability_role") or "").strip().lower()
    has_turn = any(hint in scene_function for hint in TURN_FUNCTION_HINTS)
    exclusion_blob = " ".join([presence_mode, scene_function, motion_intent, watchability_role])
    has_exclusion = any(hint in exclusion_blob for hint in FIRST_LAST_EXCLUSION_HINTS)
    energy = str(scene.get("energy") or "").strip().lower()
    if energy == "high" and "release" not in scene_function:
        return False
    return bool(has_turn and not has_exclusion) or (idx == total - 1 and "release" in scene_function)


def _route_adjacency_penalty(scenes: list[dict[str, Any]], idx: int, route: str) -> int:
    if route not in ALLOWED_ROUTES:
        return 0
    penalty = 0
    for near_idx in (idx - 1, idx + 1):
        if near_idx < 0 or near_idx >= len(scenes):
            continue
        near_route = str(_safe_dict(scenes[near_idx]).get("route") or "")
        if route == "ia2v" and near_route == "ia2v":
            penalty += IA2V_ADJACENCY_PENALTY
        if route == "first_last" and near_route == "first_last":
            penalty += FIRST_LAST_ADJACENCY_PENALTY
    return penalty


def _has_adjacent_route(scenes: list[dict[str, Any]], route_name: str) -> bool:
    route = str(route_name or "")
    for idx in range(1, len(scenes)):
        if str(_safe_dict(scenes[idx - 1]).get("route") or "") == route and str(_safe_dict(scenes[idx]).get("route") or "") == route:
            return True
    return False


def _route_scores(scene: dict[str, Any], idx: int, total: int, *, scenes: list[dict[str, Any]] | None = None) -> dict[str, int]:
    presence_mode = str(scene.get("scene_presence_mode") or "").strip().lower()
    scene_function = str(scene.get("scene_function") or "").strip().lower()
    performance_focus = bool(scene.get("performance_focus"))
    energy = str(scene.get("energy") or "").strip().lower()

    scores = {"i2v": 1, "ia2v": 0, "first_last": 0}

    if presence_mode in {"transit", "environment_anchor", "solo_observational"}:
        scores["i2v"] += 3
    if presence_mode in {"solo_performance", "private_release"}:
        scores["ia2v"] += 4
    if performance_focus:
        scores["ia2v"] += 4
    if energy == "high":
        scores["ia2v"] += 5
    elif energy == "medium":
        scores["i2v"] += 2
    elif energy == "low":
        scores["i2v"] += 2
        scores["first_last"] += 1 if "release" in scene_function or idx == total - 1 else 0

    if _is_first_last_candidate(scene, idx, total):
        scores["first_last"] += 4
    elif any(hint in scene_function for hint in TURN_FUNCTION_HINTS):
        scores["first_last"] += 1
    if idx == total - 1:
        scores["first_last"] += 2
    if idx == 0:
        scores["i2v"] += 2

    if "release" in scene_function and performance_focus:
        scores["first_last"] += 2
    if scenes:
        for route in ("i2v", "ia2v", "first_last"):
            scores[route] -= _route_adjacency_penalty(scenes, idx, route)

    return scores


def _default_route(scene: dict[str, Any], idx: int, total: int, *, scenes: list[dict[str, Any]] | None = None) -> str:
    scores = _route_scores(scene, idx, total, scenes=scenes)
    return max(("i2v", "ia2v", "first_last"), key=lambda route: (scores[route], route == "i2v"))


def _progression_by_position(idx: int, total: int) -> tuple[str, str, str]:
    if total <= 1:
        return "medium", "observational", "restrained"
    ratio = idx / max(total - 1, 1)
    if ratio < 0.2:
        return "wide", "distant", "closed"
    if ratio < 0.45:
        return "medium", "observational", "restrained"
    if ratio < 0.7:
        return "close", "near", "opening"
    if ratio < 0.9:
        return "close", "intimate", "exposed"
    return "detail", "near", "restrained"


def _visual_event_type(scene: dict[str, Any]) -> str:
    scene_function = str(scene.get("scene_function") or "").lower()
    route = str(scene.get("route") or "").lower()
    presence_mode = str(scene.get("scene_presence_mode") or "").lower()
    if "callback" in scene_function or "afterimage" in scene_function:
        return "callback"
    if any(token in scene_function for token in ("crowd", "market", "density", "compression")):
        return "crowd_compression"
    if any(token in scene_function for token in ("crossing", "intersection", "threshold", "passage")):
        return "threshold_crossing"
    if any(token in scene_function for token in ("stair", "slope", "rooftop", "vertical")):
        return "vertical_transition"
    if "transit" in presence_mode or "transit" in scene_function:
        return "transit"
    if any(token in scene_function for token in ("reveal", "overlook", "terrace", "courtyard", "city edge")):
        return "environment_reveal"
    if route == "ia2v":
        return "face"
    if "environment" in scene_function or "anchor" in scene_function:
        return "environment"
    if "hand" in scene_function or "prop" in scene_function:
        return "hands"
    if route == "first_last":
        return "body"
    return "body"


def _infer_motion_risk(scene: dict[str, Any], phrase_text: str) -> dict[str, str]:
    blob = " ".join(
        [
            str(scene.get("scene_function") or ""),
            str(scene.get("motion_intent") or ""),
            str(scene.get("emotional_intent") or ""),
            str(scene.get("watchability_role") or ""),
            str(phrase_text or ""),
        ]
    ).lower()
    finger_tokens = ("finger", "fingertip", "brim", "grip", "pinch", "small object", "button", "cassette", "ring")
    prop_tokens = ("cap", "hat", "cassette", "phone", "cigarette", "necklace", "mask", "glasses", "prop")
    face_tokens = ("face", "mouth", "lip", "cheek", "eye", "gaze", "near face")
    tiny_steps_tokens = ("then", "after that", "while", "sequence", "multi-step", "precise")

    finger_hit = any(token in blob for token in finger_tokens)
    prop_hit = any(token in blob for token in prop_tokens)
    face_hit = any(token in blob for token in face_tokens)
    tiny_steps_hit = any(token in blob for token in tiny_steps_tokens)

    high_triplet = finger_hit and prop_hit and face_hit
    return {
        "motion_complexity": "high" if tiny_steps_hit or high_triplet else ("medium" if prop_hit else "low"),
        "prop_interaction_complexity": "high" if (prop_hit and finger_hit) else ("medium" if prop_hit else "low"),
        "finger_precision_risk": "high" if (finger_hit and prop_hit) else ("medium" if finger_hit else "low"),
        "face_occlusion_risk": "high" if (face_hit and (prop_hit or finger_hit)) else ("medium" if face_hit else "low"),
        "identity_drift_risk": "high" if high_triplet else ("medium" if face_hit else "low"),
        "ltx_motion_risk": "high" if (high_triplet or tiny_steps_hit) else ("medium" if prop_hit or face_hit else "low"),
    }


def _should_auto_downgrade_first_last(
    *,
    route: str,
    route_validation_status: str,
    suggested_route: str,
    route_validation_reason: str,
    continuity_warnings: list[str],
    visual_event_type: str,
) -> bool:
    if route != "first_last" or route_validation_status != "risky" or suggested_route != "i2v":
        return False
    risk_markers = {
        str(route_validation_reason or "").strip().lower(),
        *[str(item).strip().lower() for item in continuity_warnings if str(item).strip()],
    }
    has_continuity_risk = any("continuity_risk" in marker for marker in risk_markers)
    weak_visual_delta = str(visual_event_type or "").strip().lower() in {"environment", "transit", "character_movement"}
    return has_continuity_risk or weak_visual_delta


def _pick_i2v_duration_hint(scene_duration_sec: Any, family: str) -> float:
    low, high = I2V_PROMPT_DURATION_HINT_RANGE.get(family, I2V_PROMPT_DURATION_HINT_RANGE["baseline_forward_walk"])
    target = round((low + high) / 2.0, 2)
    try:
        actual = float(scene_duration_sec)
    except Exception:
        actual = 0.0
    if actual > 0:
        target = min(target, actual)
        if target < 1.2:
            target = max(0.8, round(actual, 2))
    return round(max(0.8, target), 2)


def _select_i2v_motion_family(
    scene: dict[str, Any],
    *,
    idx: int,
    total: int,
    prev_i2v_family: str,
    transit_streak: int,
) -> dict[str, Any]:
    scene_function = str(scene.get("scene_function") or "").lower()
    scene_presence_mode = str(scene.get("scene_presence_mode") or "").lower()
    emotional_intent = str(scene.get("emotional_intent") or "").lower()
    motion_intent = str(scene.get("motion_intent") or "").lower()
    watchability_role = str(scene.get("watchability_role") or "").lower()
    visual_event_type = str(scene.get("visual_event_type") or "").lower()
    shot_scale = str(scene.get("shot_scale") or "").lower()
    camera_intimacy = str(scene.get("camera_intimacy") or "").lower()
    motion_risk = _safe_dict(scene.get("motion_risk"))
    energy = str(scene.get("energy") or "").lower()
    risk_level = str(motion_risk.get("ltx_motion_risk") or "").lower()
    blob = " ".join([scene_function, scene_presence_mode, emotional_intent, motion_intent, watchability_role])

    reveal_tokens = ("notice", "notic", "reveal", "check", "react", "look", "direction", "opening")
    tension_tokens = ("suspicion", "watched", "pursuit", "parano", "cautious", "alert", "danger", "followed")
    release_tokens = ("release", "afterimage", "aftermath", "distance", "isolation", "swallow")
    transit_like = ("transit" in scene_presence_mode) or visual_event_type == "transit" or "travel" in blob
    needs_reveal = any(token in blob for token in reveal_tokens)
    tension_mode = any(token in blob for token in tension_tokens)
    release_mode = any(token in blob for token in release_tokens)
    high_energy = energy == "high" or "high" in str(scene.get("performance_openness") or "").lower()
    medium_or_low_energy = energy in {"", "low", "medium"}
    too_wide = shot_scale in {"wide", "establishing"} or camera_intimacy in {"distant", "far"}
    i2v_mid_or_late = idx >= max(1, int(total * 0.3))

    if release_mode and medium_or_low_energy:
        family = "pull_back_release"
    elif tension_mode:
        family = "tension_head_turn"
    elif needs_reveal:
        family = "look_reveal_follow"
    elif (
        scene_function in {"build", "accent", "climax"}
        and visual_event_type in {"transit", "character_action", "body"}
        and not too_wide
        and high_energy
        and not needs_reveal
        and i2v_mid_or_late
    ):
        family = "push_in_follow"
    elif transit_like and transit_streak >= 2 and not high_energy:
        family = "look_reveal_follow"
    elif transit_like and (prev_i2v_family == "push_in_follow" or "world" in blob or "space" in blob):
        family = "side_tracking_walk"
    elif transit_like and not too_wide and high_energy:
        family = "push_in_follow"
    elif transit_like and transit_streak >= 2:
        family = "look_reveal_follow"
    elif transit_like:
        family = "side_tracking_walk"
    else:
        family = "baseline_forward_walk"

    if family not in I2V_MOTION_FAMILIES:
        family = "baseline_forward_walk"
    if risk_level == "high" and family in {"push_in_follow", "look_reveal_follow"}:
        family = "baseline_forward_walk"

    reveal_target = "none"
    if family == "look_reveal_follow":
        if "object" in blob or "sign" in blob:
            reveal_target = "noticed_object"
        elif "side" in blob:
            reveal_target = "side_space"
        else:
            reveal_target = "forward_path"
    elif family == "side_tracking_walk":
        reveal_target = "side_space"
    elif family in {"push_in_follow", "baseline_forward_walk"} and transit_like:
        reveal_target = "forward_path"

    pace_class = "purposeful"
    if family in {"tension_head_turn", "pull_back_release"}:
        pace_class = "restrained"
    elif family == "push_in_follow" and high_energy:
        pace_class = "energetic"
    elif family in {"side_tracking_walk", "look_reveal_follow"} and high_energy:
        pace_class = "purposeful"

    camera_pattern_by_family = {
        "push_in_follow": "push_in",
        "side_tracking_walk": "side_track",
        "look_reveal_follow": "follow_reveal",
        "baseline_forward_walk": "stable_follow",
        "tension_head_turn": "stable_follow",
        "pull_back_release": "pull_back",
    }
    allow_head_turn = family in {"look_reveal_follow", "tension_head_turn"}
    allow_simple_hand_motion_by_family = {
        "push_in_follow": True,
        "side_tracking_walk": False,
        "look_reveal_follow": False,
        "baseline_forward_walk": False,
        "tension_head_turn": False,
        "pull_back_release": False,
    }
    parallax_required = family in {"side_tracking_walk", "look_reveal_follow"}

    return {
        "i2v_motion_family": family,
        "pace_class": pace_class,
        "camera_pattern": camera_pattern_by_family.get(family, "stable_follow"),
        "reveal_target": reveal_target,
        "allow_head_turn": allow_head_turn,
        "allow_simple_hand_motion": bool(allow_simple_hand_motion_by_family.get(family, False)),
        "forbid_complex_hand_motion": True,
        "forbid_slow_motion_feel": True,
        "forbid_bullet_time": True,
        "forbid_stylized_action": True,
        "require_real_time_pacing": True,
        "parallax_required": parallax_required,
        "max_camera_intensity": "medium" if family in {"push_in_follow", "side_tracking_walk", "look_reveal_follow"} else "low",
        "i2v_prompt_duration_hint_sec": _pick_i2v_duration_hint(scene.get("duration_sec"), family),
    }


def _is_transit_like_scene(scene_row: dict[str, Any]) -> bool:
    scene_presence_mode = str(scene_row.get("scene_presence_mode") or "").strip().lower()
    route = str(scene_row.get("route") or "").strip().lower()
    i2v_motion_family = str(scene_row.get("i2v_motion_family") or "").strip().lower()
    visual_event_type = str(scene_row.get("visual_event_type") or "").strip().lower()
    motion_intent = str(scene_row.get("motion_intent") or "").strip().lower()
    watchability_role = str(scene_row.get("watchability_role") or "").strip().lower()
    scene_function = str(scene_row.get("scene_function") or "").strip().lower()

    if scene_presence_mode == "transit":
        return True
    if route == "i2v" and i2v_motion_family in TRANSIT_I2V_FAMILIES:
        return True
    if visual_event_type == "transit":
        return True

    blob = " ".join([motion_intent, watchability_role, scene_function])
    movement_tokens = (
        "walk",
        "moving",
        "movement",
        "transit",
        "cross",
        "enter",
        "exit",
        "pass",
        "climb",
        "descent",
        "reveal",
        "progress",
        "advance",
        "follow",
        "through space",
    )
    movement_signal = any(token in blob for token in movement_tokens)
    visual_transit_candidate = visual_event_type in TRANSIT_LIKE_VISUAL_EVENTS
    if visual_transit_candidate and movement_signal:
        return True
    if movement_signal and route == "i2v" and ("travel" in blob or "route" in blob or "space" in blob):
        return True
    return False


def _pick_transit_anti_repeat_event(scene_row: dict[str, Any]) -> str:
    blob = " ".join(
        [
            str(scene_row.get("motion_intent") or "").lower(),
            str(scene_row.get("watchability_role") or "").lower(),
            str(scene_row.get("scene_function") or "").lower(),
            str(scene_row.get("visual_event_type") or "").lower(),
        ]
    )
    if any(token in blob for token in ("threshold", "door", "entry", "exit", "cross")):
        return "threshold_crossing"
    return "environment_reveal"


def _infer_first_last_mode(scene: dict[str, Any], idx: int, total: int) -> str:
    scene_function = str(scene.get("scene_function") or "").lower()
    emotional_intent = str(scene.get("emotional_intent") or "").lower()
    motion_intent = str(scene.get("motion_intent") or "").lower()
    watchability_role = str(scene.get("watchability_role") or "").lower()
    blob = " ".join([scene_function, emotional_intent, motion_intent, watchability_role])
    if "shadow" in blob:
        return "reveal_face_from_shadow"
    if any(token in blob for token in ("reveal", "visibility", "surface", "open face")):
        return "visibility_reveal"
    if idx == total - 1 or "release" in blob or "afterimage" in blob:
        return "pull_back_release"
    if any(token in blob for token in ("turn", "threshold", "intimate", "closer", "exposure")):
        return "push_in_emotional"
    if "parallax" in blob:
        return "foreground_parallax"
    if "arc_experimental" in blob or "side_arc" in blob:
        return "small_side_arc"
    return "camera_settle"


def _stabilize_first_last_mode(mode: str, scene: dict[str, Any], idx: int, total: int) -> str:
    selected = str(mode or "").strip().lower()
    if selected != "small_side_arc":
        return selected if selected in FIRST_LAST_MODES else _infer_first_last_mode(scene, idx, total)
    blob = " ".join(
        [
            str(scene.get("scene_function") or "").lower(),
            str(scene.get("emotional_intent") or "").lower(),
            str(scene.get("motion_intent") or "").lower(),
            str(scene.get("watchability_role") or "").lower(),
        ]
    )
    if "arc_experimental" in blob or "side_arc" in blob:
        return "small_side_arc"
    if idx == total - 1 or any(token in blob for token in ("release", "afterimage", "resolution", "settle")):
        return "pull_back_release"
    if any(token in blob for token in ("reveal", "visibility", "open", "surface")):
        return "visibility_reveal"
    if any(token in blob for token in ("push", "intimate", "threshold", "closer")):
        return "push_in_emotional"
    return "camera_settle"


def _is_weak_watchability_role(value: str, *, route: str, scene_function: str) -> bool:
    raw = str(value or "").strip().lower()
    if not raw:
        return True
    weak_exact = {
        "character_1",
        "character 1",
        "hero",
        "main character",
        "protagonist",
        "lead",
        "i2v",
        "ia2v",
        "first_last",
    }
    if raw in weak_exact:
        return True
    if raw == str(route or "").strip().lower():
        return True
    fnorm = str(scene_function or "").strip().lower()
    return bool(fnorm and raw == fnorm)


def _infer_watchability_role(scene: dict[str, Any], idx: int, total: int) -> str:
    scene_function = str(scene.get("scene_function") or "").strip().lower()
    presence_mode = str(scene.get("scene_presence_mode") or "").strip().lower()
    route = str(scene.get("route") or "").strip().lower()
    performance_focus = bool(scene.get("performance_focus"))
    is_final = idx == max(total - 1, 0)

    if "environment_anchor" in presence_mode or "environment_anchor" in scene_function:
        return "anchor world and atmosphere"
    if any(k in scene_function for k in {"crowd", "market", "density", "compression"}):
        return "add public pressure and crowd texture"
    if any(k in scene_function for k in {"crossing", "threshold", "passage"}):
        return "mark threshold crossing into next space"
    if any(k in scene_function for k in {"courtyard", "terrace", "overlook", "city edge", "reveal"}):
        return "open spatial relief with city-layer reveal"
    if "transit" in presence_mode or "transit" in scene_function:
        return "carry momentum while refreshing spatial texture"
    if ("setup" in scene_function or idx == 0) and "observational" in presence_mode:
        return "establish hero in public world"
    if route == "ia2v" and (performance_focus or any(k in scene_function for k in {"tension", "conflict", "pressure"})):
        return "deepen emotional connection through performance"
    if route == "first_last" and any(k in scene_function for k in {"reveal", "turn", "transform", "transition", "callback"}):
        return "mark visual transformation"
    if "private_release" in presence_mode or "private_release" in scene_function:
        return "deliver cathartic release"
    if is_final and any(k in scene_function for k in {"release", "afterimage", "resolution", "payoff"}):
        return "close arc with emotional payoff"
    if is_final:
        return "close arc with calm payoff"
    if route == "ia2v":
        return "deepen emotional connection through performance"
    if route == "first_last":
        return "settle frame into a clear state shift"
    return "sustain watchable continuity and momentum"



def _normalize_scene_plan(
    raw_plan: dict[str, Any],
    *,
    scene_segment_rows: list[dict[str, Any]],
    role_lookup: dict[str, dict[str, Any]],
    include_debug_raw: bool = False,
) -> tuple[dict[str, Any], bool, str, int, dict[str, Any], str]:
    raw_storyboard = [_safe_dict(row) for row in _safe_list(raw_plan.get("storyboard"))]
    storyboard_by_id: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []
    for row in raw_storyboard:
        segment_id = str(row.get("segment_id") or "").strip()
        if not segment_id:
            continue
        if segment_id in storyboard_by_id:
            duplicate_ids.append(segment_id)
            continue
        storyboard_by_id[segment_id] = row

    expected_segment_ids = [str(row.get("segment_id") or "").strip() for row in scene_segment_rows]
    model_segment_ids = [str(row.get("segment_id") or "").strip() for row in raw_storyboard if str(row.get("segment_id") or "").strip()]

    normalized_storyboard: list[dict[str, Any]] = []
    validation_error = ""
    error_code = ""
    watchability_fallback_count = 0
    used_fallback = False

    if duplicate_ids:
        validation_error = "duplicate_segment_id"
        error_code = "SCENES_SEGMENT_ID_MISMATCH"

    if not validation_error and (len(raw_storyboard) != len(scene_segment_rows) or model_segment_ids != expected_segment_ids):
        validation_error = "segment_id_sequence_mismatch"
        error_code = "SCENES_SEGMENT_ID_MISMATCH"

    prompt_leaks_detected = 0
    technical_leaks_detected = 0
    enum_invalid_count = 0
    illegal_route_count = 0
    cast_mutation_count = 0

    for idx, source_row in enumerate(scene_segment_rows):
        segment_id = str(source_row.get("segment_id") or "").strip()
        raw_row = _safe_dict(storyboard_by_id.get(segment_id))
        if not raw_row:
            validation_error = validation_error or "missing_storyboard_row"
            error_code = error_code or "SCENES_SEGMENT_ID_MISMATCH"
            continue

        route = str(raw_row.get("route") or "").strip().lower()
        if route not in ALLOWED_ROUTES:
            illegal_route_count += 1
            validation_error = validation_error or "illegal_route"
            error_code = error_code or "SCENES_ILLEGAL_ROUTE"

        visual_motion = _safe_dict(raw_row.get("visual_motion"))
        composition = _safe_dict(raw_row.get("composition"))

        pacing = str(visual_motion.get("pacing") or "").strip().lower()
        energy_alignment = str(visual_motion.get("energy_alignment") or "").strip().lower()
        framing = str(composition.get("framing") or "").strip().lower()
        subject_priority = str(composition.get("subject_priority") or "").strip().lower()
        layout = str(composition.get("layout") or "").strip().lower()
        depth_strategy = str(composition.get("depth_strategy") or "").strip().lower()

        if pacing not in ALLOWED_PACING or energy_alignment not in ALLOWED_ENERGY_ALIGNMENT or framing not in ALLOWED_FRAMING or subject_priority not in ALLOWED_SUBJECT_PRIORITY or layout not in ALLOWED_LAYOUT or depth_strategy not in ALLOWED_DEPTH_STRATEGY:
            enum_invalid_count += 1
            validation_error = validation_error or "enum_invalid"
            error_code = error_code or "SCENES_ENUM_INVALID"

        content_blob = " ".join(
            [
                str(raw_row.get("route_reason") or ""),
                str(raw_row.get("scene_goal") or ""),
                str(raw_row.get("narrative_function") or ""),
                str(raw_row.get("audio_visual_sync") or ""),
                str(visual_motion.get("subject_motion") or ""),
                str(visual_motion.get("camera_intent") or ""),
            ]
        ).lower()
        has_prompt_leak = any(token in content_blob for token in {"8k", "cinematic quality", "highly detailed", "masterpiece", "positive_prompt", "negative_prompt"})
        has_technical_leak = any(token in content_blob for token in {"fps", "lens", "seed", "sampler", "workflow", "ltx", "renderer_family"})
        if has_prompt_leak:
            prompt_leaks_detected += 1
            validation_error = validation_error or "prompt_leaking"
            error_code = error_code or "SCENES_PROMPT_LEAKING"
        if has_technical_leak:
            technical_leaks_detected += 1
            validation_error = validation_error or "technical_leaking"
            error_code = error_code or "SCENES_TECHNICAL_LEAKING"

        row_str = json.dumps(raw_row, ensure_ascii=False).lower()
        if any(token in row_str for token in SCENES_FORBIDDEN_LEAK_TOKENS):
            technical_leaks_detected += 1
            validation_error = validation_error or "technical_leaking"
            error_code = error_code or "SCENES_TECHNICAL_LEAKING"

        if raw_row.get("primary_role") is not None or raw_row.get("secondary_roles") is not None or raw_row.get("active_roles") is not None:
            cast_mutation_count += 1
            validation_error = validation_error or "cast_mutation"
            error_code = error_code or "SCENES_CAST_MUTATION"

        source_t0 = _round3(source_row.get("t0"))
        source_t1 = _round3(source_row.get("t1"))
        out_t0 = _round3(raw_row.get("t0") if raw_row.get("t0") is not None else source_t0)
        out_t1 = _round3(raw_row.get("t1") if raw_row.get("t1") is not None else source_t1)
        if out_t0 != source_t0 or out_t1 != source_t1:
            validation_error = validation_error or "timing_drift"
            error_code = error_code or "SCENES_TIMING_DRIFT"

        normalized_storyboard.append(
            {
                "segment_id": segment_id,
                "route": route,
                "route_reason": str(raw_row.get("route_reason") or "").strip(),
                "scene_goal": str(raw_row.get("scene_goal") or "").strip(),
                "narrative_function": str(raw_row.get("narrative_function") or "").strip(),
                "visual_motion": {
                    "subject_motion": str(visual_motion.get("subject_motion") or "").strip(),
                    "camera_intent": str(visual_motion.get("camera_intent") or "").strip(),
                    "pacing": pacing,
                    "energy_alignment": energy_alignment,
                },
                "composition": {
                    "framing": framing,
                    "subject_priority": subject_priority,
                    "layout": layout,
                    "depth_strategy": depth_strategy,
                },
                "audio_visual_sync": str(raw_row.get("audio_visual_sync") or "").strip(),
            }
        )

    route_counts = {route_name: sum(1 for row in normalized_storyboard if row.get("route") == route_name) for route_name in ("i2v", "ia2v", "first_last")}

    legacy_scenes: list[dict[str, Any]] = []
    for row, source_row in zip(normalized_storyboard, scene_segment_rows, strict=False):
        motion = _safe_dict(row.get("visual_motion"))
        legacy_scenes.append(
            {
                "scene_id": str(row.get("segment_id") or ""),
                "segment_id": str(row.get("segment_id") or ""),
                "t0": _round3(source_row.get("t0")),
                "t1": _round3(source_row.get("t1")),
                "duration_sec": _round3(source_row.get("duration_sec")),
                "route": str(row.get("route") or ""),
                "route_reason": str(row.get("route_reason") or ""),
                "scene_function": str(row.get("narrative_function") or ""),
                "emotional_intent": str(row.get("scene_goal") or ""),
                "motion_intent": str(motion.get("subject_motion") or ""),
                "deprecated_bridge": True,
            }
        )

    if not normalized_storyboard and not validation_error:
        validation_error = "scene_plan_empty_after_normalization"
        error_code = "SCENES_SCHEMA_INVALID"

    plan = {
        "plan_version": SCENE_PLAN_PROMPT_VERSION,
        "mode": "clip",
        "scenes_version": SCENES_VERSION,
        "storyboard": normalized_storyboard,
        "route_mix_summary": {
            "total_scenes": len(normalized_storyboard),
            "i2v": route_counts["i2v"],
            "ia2v": route_counts["ia2v"],
            "first_last": route_counts["first_last"],
        },
        "scenes": legacy_scenes,
        "scene_arc_summary": "",
        "route_strategy_notes": ["scene_candidate_windows and compiled_contract are legacy bridge inputs"],
        "deprecated_bridge": True,
    }

    normalization_diag: dict[str, Any] = {
        "window_count_source": len(scene_segment_rows),
        "window_count_model": len(raw_storyboard),
        "window_count_normalized": len(normalized_storyboard),
        "segment_count_expected": len(expected_segment_ids),
        "segment_count_actual": len(normalized_storyboard),
        "segment_coverage_ok": bool(expected_segment_ids == [str(row.get("segment_id") or "") for row in normalized_storyboard]),
        "uses_segment_id_canonical": True,
        "prompt_leaks_detected": prompt_leaks_detected,
        "technical_leaks_detected": technical_leaks_detected,
        "enum_invalid_count": enum_invalid_count,
        "illegal_route_count": illegal_route_count,
        "cast_mutation_count": cast_mutation_count,
        "target_route_mix": _target_route_budget(len(normalized_storyboard)),
        "actual_route_mix": route_counts,
        "route_spacing": {
            "has_adjacent_ia2v": _has_adjacent_route(legacy_scenes, "ia2v"),
            "has_adjacent_first_last": _has_adjacent_route(legacy_scenes, "first_last"),
            "warning": "",
        },
    }
    if include_debug_raw:
        normalization_diag["original_scenes"] = raw_storyboard

    if validation_error and not error_code:
        error_code = "SCENES_SCHEMA_INVALID"
    return plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, error_code

def build_gemini_scene_plan(*, api_key: str, package: dict[str, Any], validation_feedback: str = "") -> dict[str, Any]:
    context, aux = _build_scene_planning_context(package)
    scene_segment_rows = _safe_list(aux.get("scene_segment_rows"))
    role_lookup = _safe_dict(aux.get("role_lookup"))
    compiled_contract = _safe_dict(aux.get("compiled_contract"))
    world_summary_used = bool(aux.get("world_summary_used"))
    include_debug_raw = _scene_plan_debug_enabled(package)
    model_id = str(_safe_dict(context.get("video_capability_canon")).get("model_id") or DEFAULT_VIDEO_MODEL_ID)
    capability_diag = build_capability_diagnostics_summary(
        model_id=model_id,
        route_type="mixed",
        story_core_guard_applied=False,
        scene_plan_guard_applied=True,
        prompt_guard_applied=False,
    )

    diagnostics = {
        "prompt_version": SCENE_PLAN_PROMPT_VERSION,
        "scene_candidate_windows_bridge": bool(aux.get("uses_legacy_scene_candidate_windows_bridge")),
        "compiled_contract_bridge": bool(compiled_contract),
        "role_source_precedence": _safe_list(aux.get("scene_role_source_precedence")),
        "used_model": SCENE_PLAN_MODEL,
        "scene_count": len(scene_segment_rows),
        "scene_plan_scenes_version": SCENES_VERSION,
        "scene_plan_uses_segment_id_canonical": True,
        "watchability_fallback_count": 0,
        "world_summary_used": world_summary_used,
        **capability_diag,
    }

    def _collect_scene_plan_diagnostics(
        *,
        scene_plan: dict[str, Any],
        normalization_diag: dict[str, Any],
        watchability_fallback_count: int,
        include_presence_modes: bool,
    ) -> dict[str, Any]:
        route_summary = _safe_dict(scene_plan.get("route_mix_summary"))
        route_counts = {
            "i2v": int(route_summary.get("i2v") or 0),
            "ia2v": int(route_summary.get("ia2v") or 0),
            "first_last": int(route_summary.get("first_last") or 0),
        }
        spacing = _safe_dict(normalization_diag.get("route_spacing"))
        payload = {
            "route_counts": route_counts,
            "presence_modes": (
                sorted(
                    {
                        str(_safe_dict(role_lookup.get(str(row.get("scene_id") or ""))).get("scene_presence_mode") or "").strip()
                        for row in _safe_list(scene_plan.get("scenes"))
                    }
                    - {""}
                )
                if include_presence_modes
                else []
            ),
            "route_flat": bool(_safe_list(scene_plan.get("scenes")) and len({r for r, c in route_counts.items() if c > 0}) <= 1),
            "watchability_fallback_count": int(watchability_fallback_count),
            "window_count_source": int(normalization_diag.get("window_count_source") or 0),
            "window_count_model": int(normalization_diag.get("window_count_model") or 0),
            "window_count_normalized": int(normalization_diag.get("window_count_normalized") or 0),
            "segment_count_expected": int(normalization_diag.get("segment_count_expected") or 0),
            "segment_count_actual": int(normalization_diag.get("segment_count_actual") or 0),
            "segment_coverage_ok": bool(normalization_diag.get("segment_coverage_ok")),
            "uses_segment_id_canonical": bool(normalization_diag.get("uses_segment_id_canonical")),
            "repaired_to_audio_windows": False,
            "synthetic_rows_dropped": 0,
            "missing_rows_filled": 0,
            "normalization_mode": "validate_only",
            "creative_rewrite_applied": False,
            "route_swaps_applied": 0,
            "warnings_count": int(normalization_diag.get("enum_invalid_count") or 0),
            "unsupported_scene_count": int(normalization_diag.get("illegal_route_count") or 0),
            "risky_scene_count": 0,
            "scene_plan_has_adjacent_ia2v": bool(spacing.get("has_adjacent_ia2v")),
            "scene_plan_has_adjacent_first_last": bool(spacing.get("has_adjacent_first_last")),
            "scene_plan_route_spacing_warning": str(spacing.get("warning") or ""),
        }
        if include_debug_raw:
            payload["scene_plan_debug"] = {
                "normalization_mode": str(normalization_diag.get("normalization_mode") or ""),
                "creative_rewrite_applied": bool(normalization_diag.get("creative_rewrite_applied")),
                "route_swaps_applied": int(normalization_diag.get("route_swaps_applied") or 0),
                "warnings_count": int(normalization_diag.get("warnings_count") or 0),
                "unsupported_scene_count": int(normalization_diag.get("unsupported_scene_count") or 0),
                "risky_scene_count": int(normalization_diag.get("risky_scene_count") or 0),
                "target_route_mix": _safe_dict(normalization_diag.get("target_route_mix")),
                "actual_route_mix": _safe_dict(normalization_diag.get("actual_route_mix")),
                "deviation_summary": _safe_dict(normalization_diag.get("deviation_summary")),
                "route_spacing": spacing,
                "i2v_motion_family_counts": _safe_dict(normalization_diag.get("i2v_motion_family_counts")),
                "unsupported_i2v_family_count": int(normalization_diag.get("unsupported_i2v_family_count") or 0),
                "i2v_rows_enriched_count": int(normalization_diag.get("i2v_rows_enriched_count") or 0),
                "original_scenes_count": int(normalization_diag.get("original_scenes_count") or 0),
                "original_scenes": _safe_list(normalization_diag.get("original_scenes")),
            }
        return payload

    if not scene_segment_rows:
        plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, error_code = _normalize_scene_plan(
            {},
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            include_debug_raw=include_debug_raw,
        )
        diagnostics["error_code"] = error_code or "SCENES_SCHEMA_INVALID"
        diagnostics.update(
            _collect_scene_plan_diagnostics(
                scene_plan=plan,
                normalization_diag=normalization_diag,
                watchability_fallback_count=watchability_fallback_count,
                include_presence_modes=False,
            )
        )
        return {
            "ok": False,
            "scene_plan": plan,
            "error": "segment_rows_missing",
            "validation_error": validation_error or "SCENES_SCHEMA_INVALID",
            "error_code": diagnostics["error_code"],
            "used_fallback": True,
            "diagnostics": diagnostics,
        }

    missing_role_segments = [str(row.get("segment_id") or "") for row in scene_segment_rows if not _safe_dict(role_lookup.get(str(row.get("segment_id") or "")))]
    missing_core_segments = [str(item).strip() for item in _safe_list(aux.get("missing_core_source_segments")) if str(item).strip()]
    if missing_core_segments:
        plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, _ = _normalize_scene_plan(
            {},
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            include_debug_raw=include_debug_raw,
        )
        diagnostics["error_code"] = "SCENES_CORE_SOURCE_MISSING"
        diagnostics["validation_error"] = "missing_core_source_for_segments"
        diagnostics["missing_core_source_segments"] = missing_core_segments
        diagnostics.update(
            _collect_scene_plan_diagnostics(
                scene_plan=plan,
                normalization_diag=normalization_diag,
                watchability_fallback_count=watchability_fallback_count,
                include_presence_modes=False,
            )
        )
        return {
            "ok": False,
            "scene_plan": plan,
            "error": "core_source_missing",
            "validation_error": "missing_core_source_for_segments",
            "error_code": "SCENES_CORE_SOURCE_MISSING",
            "used_fallback": used_fallback,
            "diagnostics": diagnostics,
        }

    if missing_role_segments:
        plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, _ = _normalize_scene_plan(
            {},
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            include_debug_raw=include_debug_raw,
        )
        diagnostics["error_code"] = "SCENES_ROLE_SOURCE_MISSING"
        diagnostics["validation_error"] = "missing_role_source_for_segments"
        diagnostics["missing_role_source_segments"] = missing_role_segments
        diagnostics.update(
            _collect_scene_plan_diagnostics(
                scene_plan=plan,
                normalization_diag=normalization_diag,
                watchability_fallback_count=watchability_fallback_count,
                include_presence_modes=False,
            )
        )
        return {
            "ok": False,
            "scene_plan": plan,
            "error": "role_source_missing",
            "validation_error": "missing_role_source_for_segments",
            "error_code": "SCENES_ROLE_SOURCE_MISSING",
            "used_fallback": used_fallback,
            "diagnostics": diagnostics,
        }

    prompt = _build_prompt(context, validation_feedback=validation_feedback)
    try:
        response = post_generate_content(
            api_key=str(api_key or "").strip(),
            model=SCENE_PLAN_MODEL,
            body={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            timeout=90,
        )
        if isinstance(response, dict) and response.get("__http_error__"):
            raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")

        parsed = _extract_json_obj(_extract_gemini_text(response))
        scene_plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, error_code = _normalize_scene_plan(
            parsed,
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            include_debug_raw=include_debug_raw,
        )
        diagnostics["error_code"] = error_code
        diagnostics.update(
            _collect_scene_plan_diagnostics(
                scene_plan=scene_plan,
                normalization_diag=normalization_diag,
                watchability_fallback_count=watchability_fallback_count,
                include_presence_modes=True,
            )
        )
        storyboard_rows = _safe_list(scene_plan.get("storyboard"))
        has_validation_error = bool(str(validation_error or "").strip())
        ok = bool(storyboard_rows) and not has_validation_error
        error_text = ""
        if not ok:
            error_text = str(validation_error or error_code or "invalid_scene_plan")
        return {
            "ok": ok,
            "scene_plan": scene_plan,
            "error": error_text,
            "validation_error": validation_error,
            "error_code": error_code,
            "used_fallback": used_fallback,
            "diagnostics": diagnostics,
        }
    except Exception as exc:  # noqa: BLE001
        scene_plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, error_code = _normalize_scene_plan(
            {},
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            include_debug_raw=include_debug_raw,
        )
        diagnostics["error_code"] = error_code or "SCENES_SCHEMA_INVALID"
        diagnostics.update(
            _collect_scene_plan_diagnostics(
                scene_plan=scene_plan,
                normalization_diag=normalization_diag,
                watchability_fallback_count=watchability_fallback_count,
                include_presence_modes=True,
            )
        )
        return {
            "ok": False,
            "scene_plan": scene_plan,
            "error": str(exc),
            "validation_error": validation_error,
            "error_code": diagnostics["error_code"],
            "used_fallback": True,
            "diagnostics": diagnostics,
        }
