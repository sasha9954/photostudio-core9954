from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from app.engine.gemini_rest import post_generate_content
from app.engine.scenario_stage_timeout_policy import (
    get_scenario_stage_timeout,
    is_timeout_error,
    scenario_timeout_policy_name,
)
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
ALLOWED_PACING = {"stable", "slow", "medium", "fast"}
ALLOWED_ENERGY_ALIGNMENT = {"match", "counterpoint", "build_against", "release_after"}
ALLOWED_FRAMING = {"close_up", "medium", "wide", "detail", "silhouette", "overhead"}
ALLOWED_SUBJECT_PRIORITY = {"hero", "ensemble", "object", "environment"}
ALLOWED_LAYOUT = {"centered", "rule_of_thirds", "off_balance", "symmetrical"}
ALLOWED_DEPTH_STRATEGY = {"flat", "layered", "deep"}
ALLOWED_LIP_SYNC_PRIORITY = {"none", "low", "medium", "high"}
ALLOWED_STORY_BEAT_TYPES = {"physical_event", "vocal_emotion", "state_transition"}
UNKNOWN_SPEAKER_ROLE = "unknown"
UNKNOWN_VOCAL_GENDER = "unknown"
UNKNOWN_VOCAL_OWNER_ROLE = "unknown"
ALLOWED_VOCAL_GENDERS = {"female", "male", "mixed", "unknown"}
_ROLE_KEYS = ("character_1", "character_2", "character_3")
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
    "identity_source",
    "identity_rule",
    "identity_reference_rule",
    "connected_visual_reference",
    "canonical source of truth",
    "source of truth",
    "payload",
    "diagnostics",
    "route strategy",
}
SCENES_FREE_TEXT_LEAK_FIELD_PATHS = (
    ("route_selection_reason",),
    ("route_reason",),
    ("scene_goal",),
    ("narrative_function",),
    ("photo_staging_goal",),
    ("ltx_video_goal",),
    ("background_story_evidence",),
    ("foreground_performance_rule",),
    ("audio_visual_sync",),
    ("visual_motion", "subject_motion"),
    ("visual_motion", "camera_intent"),
)
SCENES_FREE_TEXT_CLEANUP_TOKENS = {
    *SCENES_FORBIDDEN_LEAK_TOKENS,
    "route",
    "i2v",
    "ia2v",
    "first_last",
    "lip-sync",
    "lip_sync",
    "json",
    "schema",
    "provider",
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
WORLD_FOCUS_ROLE_PRIORITY = ("environment", "location", "locals", "crowd", "props", "object", "threshold", "aftermath")
WORLD_BEAT_HINTS = {"social_texture", "world_pressure", "threshold", "aftermath", "release", "world_observation", "instrumental", "outro"}
INSTRUMENTAL_NO_VOCAL_MARKERS = (
    "[instrumental",
    "instrumental intro",
    "instrumental outro",
    "no vocal",
    "no-vocal",
    "outro",
    "intro",
)
SCENE_PLAN_COMPOSITION_ENUM_ALIASES: dict[str, dict[str, str]] = {
    "composition.framing": {
        "close-up": "close_up",
        "close up": "close_up",
        "medium_close_up": "medium",
        "medium close up": "medium",
        "medium_wide": "wide",
        "medium wide": "wide",
        "wide_shot": "wide",
        "wide shot": "wide",
    },
    "composition.depth_strategy": {
        "shallow": "flat",
        "shallow_focus": "flat",
        "shallow focus": "flat",
        "deep_focus": "deep",
        "deep focus": "deep",
    },
    "composition.layout": {
        "asymmetrical": "off_balance",
        "balanced": "symmetrical",
        "rule of thirds": "rule_of_thirds",
        "rule-of-thirds": "rule_of_thirds",
    },
}


_FINAL_PAYOFF_HINTS = {
    "final",
    "last line",
    "last phrase",
    "tail",
    "ending",
    "outro",
    "payoff",
    "resolution",
    "afterimage",
    "release",
    "climax",
}


def _is_final_emotional_payoff_candidate(source_row: dict[str, Any], *, idx: int, total: int) -> bool:
    if total <= 0:
        return False
    near_tail = idx >= max(0, total - 2)
    if not near_tail:
        return False
    transcript = str(source_row.get("transcript_slice") or "").strip().lower()
    beat_mode = str(source_row.get("beat_mode") or "").strip().lower()
    hero_world_mode = str(source_row.get("hero_world_mode") or "").strip().lower()
    emotional_key = str(source_row.get("emotional_key") or "").strip().lower()
    arc_role = str(source_row.get("arc_role") or "").strip().lower()
    beat_purpose = str(source_row.get("beat_purpose") or "").strip().lower()
    blob = " ".join([transcript, beat_mode, hero_world_mode, emotional_key, arc_role, beat_purpose])
    has_tail_hint = any(token in blob for token in _FINAL_PAYOFF_HINTS)
    has_vocal_hint = bool(source_row.get("is_lip_sync_candidate")) or bool(transcript)
    return bool(has_vocal_hint and (has_tail_hint or beat_mode == "performance" or hero_world_mode == "hero_foreground"))


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clamp_ratio(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except Exception:
        return float(default)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        if isinstance(value, str):
            cleaned = value.strip().replace(",", ".")
            if not cleaned:
                return float(default)
            return float(cleaned)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _normalize_gender_hint(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    female_tokens = (
        "female", "woman", "girl", "girlfriend", "wife", "lady", "she", "her",
        "девушка", "женщина", "жена", "она", "жен", "feminine",
    )
    male_tokens = (
        "male", "man", "guy", "boy", "boyfriend", "husband", "he", "him",
        "парень", "мужчина", "муж", "он", "masculine",
    )
    if any(t in token for t in female_tokens):
        return "female"
    if any(t in token for t in male_tokens):
        return "male"
    return ""


def _normalize_identity_label_hint(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    female_tokens = ("девушка", "женщина", "girl", "woman", "female", "lady", "feminine")
    male_tokens = ("парень", "мужчина", "guy", "man", "male", "masculine")
    if any(t in token for t in female_tokens):
        return "female"
    if any(t in token for t in male_tokens):
        return "male"
    return ""


def _normalize_scene_plan_composition_enum_alias(field_name: str, field_value: Any) -> tuple[str, bool]:
    raw_value = str(field_value or "").strip().lower()
    if not raw_value:
        return "", False
    aliases = SCENE_PLAN_COMPOSITION_ENUM_ALIASES.get(field_name) or {}
    if raw_value in aliases:
        normalized = str(aliases.get(raw_value) or "").strip().lower()
        return normalized or raw_value, bool(normalized and normalized != raw_value)
    canonical_like = "_".join(token for token in raw_value.replace("-", " ").replace("_", " ").split() if token).strip("_")
    if canonical_like in aliases:
        normalized = str(aliases.get(canonical_like) or "").strip().lower()
        return normalized or canonical_like, bool(normalized and normalized != raw_value)
    return canonical_like or raw_value, bool(canonical_like and canonical_like != raw_value)


def normalize_character_appearance_mode(value: Any) -> str:
    token = str(value or "").strip().lower()
    if token in {"only_lipsync", "lip-sync only"}:
        return "lip_sync_only"
    if token == "voice_only":
        return "offscreen_voice"
    if token == "silhouette":
        return "background_only"
    if token == "everywhere_meaningful":
        return "story_visible"
    if token == "background":
        return "background_only"
    if token == "offscreen":
        return "offscreen_voice"
    if token in {"auto", "story_visible", "lip_sync_only", "background_only", "offscreen_voice"}:
        return token
    return "auto"


def character_presence_mode_from_appearance_mode(value: Any) -> str:
    appearance_mode = normalize_character_appearance_mode(value)
    if appearance_mode == "lip_sync_only":
        return "vocal_anchor"
    if appearance_mode == "story_visible":
        return "adaptive_presence"
    if appearance_mode == "background_only":
        return "background_presence"
    if appearance_mode == "offscreen_voice":
        return "offscreen_voice"
    return "adaptive_presence"


def _extract_character_appearance_modes(input_pkg: dict[str, Any]) -> dict[str, str]:
    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    nested_sources: list[dict[str, Any]] = [
        _safe_dict(input_pkg.get("role_identity_mapping")),
        _safe_dict(input_pkg.get("character_identity_by_role")),
        _safe_dict(connected_summary.get("role_identity_mapping")),
        _safe_dict(connected_summary.get("character_identity_by_role")),
    ]
    out: dict[str, str] = {}
    for source in nested_sources:
        for role in _ROLE_KEYS:
            if role in out:
                continue
            row = _safe_dict(source.get(role))
            out[role] = normalize_character_appearance_mode(
                row.get("appearanceMode")
                or row.get("screenPresenceMode")
                or row.get("appearance_mode")
                or row.get("screen_presence_mode")
                or row.get("character_presence_mode")
            )
    for role in _ROLE_KEYS:
        out.setdefault(role, "auto")
    return out


def _select_world_focus_role(active_roles: list[str]) -> str:
    active = [str(role or "").strip() for role in active_roles if str(role or "").strip()]
    for preferred in WORLD_FOCUS_ROLE_PRIORITY:
        if preferred in active:
            return preferred
    for fallback in active:
        if fallback != "character_1":
            return fallback
    return ""


def _is_world_beat(source_row: dict[str, Any]) -> bool:
    beat_primary_subject = str(source_row.get("beat_primary_subject") or "").strip().lower()
    hero_world_mode = str(source_row.get("hero_world_mode") or "").strip().lower()
    beat_mode = str(source_row.get("beat_mode") or "").strip().lower()
    subject_presence_requirement = str(source_row.get("subject_presence_requirement") or "").strip().lower()
    if beat_primary_subject in {"world", "environment", "city", "location", "crowd"}:
        return True
    if hero_world_mode == "world_foreground":
        return True
    if beat_mode in {"world_observation", "social_texture", "world_pressure", "release", "symbolic_environment"}:
        return True
    if any(token in subject_presence_requirement for token in ("world", "context", "offscreen", "implied")):
        return True
    blob = " ".join(
        [
            str(source_row.get("arc_role") or ""),
            str(source_row.get("beat_purpose") or ""),
            str(source_row.get("emotional_key") or ""),
        ]
    ).lower()
    return any(token in blob for token in WORLD_BEAT_HINTS)


def _is_instrumental_or_no_vocal_text(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in INSTRUMENTAL_NO_VOCAL_MARKERS)


def _can_enforce_ia2v_row(
    *,
    source_row: dict[str, Any],
    spoken_line: str,
    transcript_slice: str,
    is_lip_sync_candidate: bool,
    active_roles: list[str],
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not (spoken_line or transcript_slice):
        reasons.append("ia2v_missing_spoken_content")
    if _is_instrumental_or_no_vocal_text(spoken_line) or _is_instrumental_or_no_vocal_text(transcript_slice):
        reasons.append("ia2v_instrumental_or_no_vocal_segment")
    if not is_lip_sync_candidate:
        reasons.append("audio_map_permission_missing")
    duration_sec = float(source_row.get("duration_sec") or 0.0)
    if duration_sec < 2.8:
        reasons.append("duration_too_short_for_lipsync")
    if duration_sec > 7.0:
        reasons.append("duration_too_long_for_lipsync")
    if _is_world_beat(source_row):
        reasons.append("ia2v_non_vocal_world_beat")
    if active_roles and "character_1" not in active_roles:
        reasons.append("ia2v_character_1_not_present")
    return not reasons, reasons


def _extract_role_identity_gender_map(input_pkg: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    nested_identity_sources: list[dict[str, Any]] = [
        _safe_dict(input_pkg.get("role_identity_mapping")),
        _safe_dict(input_pkg.get("character_identity_by_role")),
        _safe_dict(connected_summary.get("role_identity_mapping")),
        _safe_dict(connected_summary.get("character_identity_by_role")),
    ]
    for source in nested_identity_sources:
        for role in _ROLE_KEYS:
            if role in out:
                continue
            identity_row = _safe_dict(source.get(role))
            hint = _normalize_gender_hint(identity_row.get("gender_hint"))
            if not hint:
                hint = _normalize_identity_label_hint(identity_row.get("identity_label"))
            if hint:
                out[role] = hint
    return out


def _resolve_vocal_gender(audio_map: dict[str, Any], input_pkg: dict[str, Any]) -> str:
    _ = input_pkg
    candidate_sources: list[dict[str, Any]] = [
        _safe_dict(audio_map.get("vocal_profile")),
        audio_map,
    ]
    for source in candidate_sources:
        for key in ("vocal_gender", "vocalGender"):
            raw = str(source.get(key) or "").strip().lower()
            if raw in ALLOWED_VOCAL_GENDERS:
                return raw
            normalized = _normalize_gender_hint(raw)
            if normalized in {"female", "male"}:
                return normalized
            if raw in {"duet", "multiple", "multi", "both"}:
                return "mixed"
    return UNKNOWN_VOCAL_GENDER


def _resolve_vocal_owner_role(vocal_gender: str, role_gender_map: dict[str, str]) -> str:
    if vocal_gender not in {"female", "male"}:
        return UNKNOWN_VOCAL_OWNER_ROLE
    matching_roles = [role for role in _ROLE_KEYS if role_gender_map.get(role) == vocal_gender]
    if len(matching_roles) == 1:
        return matching_roles[0]
    return UNKNOWN_VOCAL_OWNER_ROLE


def _normalize_creative_config(raw_config: Any) -> dict[str, Any]:
    row = _safe_dict(raw_config)
    route_strategy_mode = str(row.get("route_strategy_mode") or row.get("routeStrategyMode") or "auto").strip().lower() or "auto"
    if route_strategy_mode not in {"auto", "preset", "custom_counts"}:
        route_strategy_mode = "auto"
    route_strategy_preset = str(row.get("route_strategy_preset") or row.get("routeStrategyPreset") or "").strip()
    base_scene_count = max(1, int(row.get("base_scene_count") or row.get("baseSceneCount") or 8))
    route_targets_raw = _safe_dict(row.get("route_targets_per_block") or row.get("routeTargetsPerBlock"))
    route_targets_per_block = {
        "i2v": max(0, int(route_targets_raw.get("i2v") or 0)),
        "ia2v": max(0, int(route_targets_raw.get("ia2v") or 0)),
        "first_last": max(0, int(route_targets_raw.get("first_last") or route_targets_raw.get("firstLast") or 0)),
    }
    if route_strategy_mode == "auto" and sum(route_targets_per_block.values()) <= 0:
        route_targets_per_block = {"i2v": 4, "ia2v": 2, "first_last": 2}
    route_mix_mode = str(row.get("route_mix_mode") or row.get("routeMixMode") or ("auto" if route_strategy_mode == "auto" else "custom")).strip().lower() or "auto"
    if route_mix_mode not in {"auto", "custom"}:
        route_mix_mode = "auto"

    lipsync_ratio = _clamp_ratio(row.get("lipsync_ratio"), route_targets_per_block["ia2v"] / base_scene_count)
    first_last_ratio = _clamp_ratio(row.get("first_last_ratio"), route_targets_per_block["first_last"] / base_scene_count)
    i2v_ratio = _clamp_ratio(row.get("i2v_ratio"), route_targets_per_block["i2v"] / base_scene_count)
    if sum(route_targets_per_block.values()) > 0:
        lipsync_ratio = round(route_targets_per_block["ia2v"] / base_scene_count, 3)
        first_last_ratio = round(route_targets_per_block["first_last"] / base_scene_count, 3)
        i2v_ratio = round(route_targets_per_block["i2v"] / base_scene_count, 3)

    preferred_routes = [str(item).strip().lower() for item in _safe_list(row.get("preferred_routes")) if str(item).strip()]
    preferred_routes = [route for route in preferred_routes if route in {"i2v", "ia2v", "first_last"}] or ["i2v", "ia2v", "first_last"]

    try:
        max_consecutive_ia2v = int(row.get("max_consecutive_ia2v") or row.get("maxConsecutiveIa2v") or row.get("max_consecutive_lipsync") or 2)
    except Exception:
        max_consecutive_ia2v = 2
    max_consecutive_ia2v = max(1, min(8, max_consecutive_ia2v))

    hard_map_raw = row.get("hard_route_assignments_by_segment")
    if hard_map_raw is None:
        hard_map_raw = row.get("hardRouteAssignmentsBySegment")
    if hard_map_raw is None:
        hard_map_raw = row.get("route_assignments_by_segment")
    if hard_map_raw is None:
        hard_map_raw = row.get("routeAssignmentsBySegment")
    hard_map_obj = _safe_dict(hard_map_raw)
    hard_route_assignments_by_segment: dict[str, str] = {}
    for k, v in hard_map_obj.items():
        seg = str(k or "").strip()
        route = str(v or "").strip().lower()
        if seg and route in ALLOWED_ROUTES:
            hard_route_assignments_by_segment[seg] = route
    targets_are_soft = bool(
        row.get("targets_are_soft")
        if row.get("targets_are_soft") is not None
        else (row.get("targetsAreSoft") if row.get("targetsAreSoft") is not None else True)
    )
    if hard_route_assignments_by_segment:
        targets_are_soft = False

    return {
        "route_strategy_mode": route_strategy_mode,
        "route_strategy_preset": route_strategy_preset,
        "route_block_duration_sec": int(row.get("route_block_duration_sec") or row.get("routeBlockDurationSec") or 30),
        "base_scene_count": base_scene_count,
        "extra_scene_policy": str(row.get("extra_scene_policy") or row.get("extraScenePolicy") or "add_i2v").strip() or "add_i2v",
        "route_targets_per_block": route_targets_per_block,
        "max_consecutive_ia2v": max_consecutive_ia2v,
        "targets_are_soft": targets_are_soft,
        "instrumental_policy": str(row.get("instrumental_policy") or row.get("instrumentalPolicy") or "use_i2v_for_non_vocal_or_instrumental_gaps").strip() or "use_i2v_for_non_vocal_or_instrumental_gaps",
        "vocal_policy": str(row.get("vocal_policy") or row.get("vocalPolicy") or "ia2v_only_on_vocal_windows").strip() or "ia2v_only_on_vocal_windows",
        "long_vocal_split_policy": str(row.get("long_vocal_split_policy") or row.get("longVocalSplitPolicy") or "prefer_ia2v_3_to_7_sec_allow_strong_vocal_opening_anchor_up_to_7_sec").strip() or "prefer_ia2v_3_to_7_sec_allow_strong_vocal_opening_anchor_up_to_7_sec",
        "route_mix_mode": route_mix_mode,
        "lipsync_ratio": round(lipsync_ratio, 3),
        "first_last_ratio": round(first_last_ratio, 3),
        "i2v_ratio": round(i2v_ratio, 3),
        "preferred_routes": preferred_routes,
        "max_consecutive_lipsync": max_consecutive_ia2v,
        "hard_route_assignments_by_segment": hard_route_assignments_by_segment,
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
    story_core_v1 = _safe_dict(story_core.get("story_core_v1"))
    if not story_core_v1 and str(story_core.get("schema_version") or "").startswith("core_v1"):
        story_core_v1 = story_core
    scenes_core = story_core_v1.get("scenes_core")
    if scenes_core and isinstance(scenes_core, list):
        scenes: list[dict[str, Any]] = []
        audio_segments_source = (
            _safe_list(audio_map.get("segments"))
            or _safe_list(audio_map.get("scene_candidate_windows"))
            or _safe_list(audio_map.get("phrase_units"))
        )
        audio_lookup = {
            str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row)
            for row in audio_segments_source
            if str(_safe_dict(row).get("segment_id") or "").strip()
        }
        role_lookup = _build_scene_role_lookup(role_plan)
        for sc_raw in scenes_core:
            sc = _safe_dict(sc_raw)
            segment_id = str(sc.get("segment_id") or "").strip()
            if not segment_id:
                continue
            audio_row = _safe_dict(audio_lookup.get(segment_id))
            role_row = _safe_dict(role_lookup.get(segment_id))
            t0 = _round3(audio_row.get("t0")) if isinstance(audio_row.get("t0"), (int, float)) else 0.0
            t1 = _round3(audio_row.get("t1")) if isinstance(audio_row.get("t1"), (int, float)) else t0
            camera_obj = _safe_dict(sc.get("camera"))
            if not camera_obj:
                camera_obj = {
                    "framing": "medium",
                    "movement": "static",
                    "angle": "eye-level",
                }
            scene = {
                "scene_id": segment_id,
                "segment_id": segment_id,
                "t0": t0,
                "t1": t1,
                "duration": _round3(t1 - t0) if t1 >= t0 else 0.0,
                "duration_sec": _round3(t1 - t0) if t1 >= t0 else 0.0,
                "primary_role": str(role_row.get("primary_role") or "").strip(),
                "secondary_roles": [str(v).strip() for v in _safe_list(role_row.get("secondary_roles")) if str(v).strip()],
                "scene_goal": str(sc.get("scene_goal") or "").strip(),
                "location": str(sc.get("location") or "").strip(),
                "action": str(sc.get("action") or "").strip(),
                "environment_interaction": str(sc.get("environment_interaction") or "").strip(),
                "visual_hook": str(sc.get("visual_hook") or "").strip(),
                "camera": camera_obj,
                "cut_type": str(sc.get("cut_type") or "").strip(),
                "energy": (
                    str(sc.get("energy") or "").strip()
                    or str(audio_row.get("energy") or "").strip()
                ),
                "continuity_rule": str(sc.get("continuity_rule") or "").strip(),
            }
            scenes.append(scene)
        audio_segment_count = len(
            [
                row
                for row in audio_segments_source
                if str(_safe_dict(row).get("segment_id") or "").strip()
            ]
        )
        scenes_have_required_timing = all(
            str(_safe_dict(scene).get("segment_id") or "").strip()
            and isinstance(_safe_dict(scene).get("t0"), (int, float))
            and isinstance(_safe_dict(scene).get("t1"), (int, float))
            and _safe_dict(scene).get("t1") >= _safe_dict(scene).get("t0")
            for scene in scenes
        )
        if scenes and len(scenes) == audio_segment_count and scenes_have_required_timing:
            print(f"[SCENES] built {len(scenes)} scenes from scenes_core")
            return scenes, []
        print("[SCENES] scenes_core present but failed validation; falling back to legacy scene builder")
    beat_map = _safe_dict(story_core_v1.get("beat_map"))
    beat_rows = [_safe_dict(row) for row in _safe_list(beat_map.get("beats"))]
    beat_rows_by_segment = {
        str(row.get("source_segment_id") or "").strip(): row
        for row in beat_rows
        if str(row.get("source_segment_id") or "").strip()
    }
    cast_rows = {_safe_dict(row).get("segment_id"): _safe_dict(row) for row in _safe_list(role_plan.get("scene_casting"))}

    normalized: list[dict[str, Any]] = []
    missing_core_source_segments: list[str] = []
    for idx, segment in enumerate(audio_segments, start=1):
        segment_id = str(segment.get("segment_id") or f"seg_{idx}").strip()
        core_raw = core_rows.get(segment_id)
        if not isinstance(core_raw, dict):
            missing_core_source_segments.append(segment_id)
        core = _safe_dict(core_raw)
        beat = _safe_dict(beat_rows_by_segment.get(segment_id))
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
                "is_lip_sync_candidate": bool(segment.get("is_lip_sync_candidate")),
                "arc_role": str(core.get("arc_role") or "").strip(),
                "beat_purpose": str(core.get("beat_purpose") or "").strip(),
                "emotional_key": str(core.get("emotional_key") or "").strip(),
                "primary_role": str(cast.get("primary_role") or "").strip(),
                "secondary_roles": [str(v).strip() for v in _safe_list(cast.get("secondary_roles")) if str(v).strip()],
                "presence_mode": str(cast.get("presence_mode") or "").strip(),
                "presence_weight": str(cast.get("presence_weight") or "").strip(),
                "performance_focus": bool(cast.get("performance_focus")),
                "beat_mode": str(beat.get("beat_mode") or "").strip(),
                "hero_world_mode": str(beat.get("hero_world_mode") or "").strip(),
                "beat_primary_subject": str(beat.get("beat_primary_subject") or "").strip(),
                "subject_presence_requirement": str(beat.get("subject_presence_requirement") or "").strip(),
            }
        )
    if normalized:
        return normalized, list(dict.fromkeys(missing_core_source_segments))

    # Beat-map fallback (source of truth) for pipelines that have no audio_map.segments.
    # FINAL requires scenes[] with timing; derive segment rows directly from beat_map.beats.
    for idx, beat in enumerate(beat_rows, start=1):
        segment_id = str(beat.get("source_segment_id") or f"seg_{idx}").strip()
        if not segment_id:
            segment_id = f"seg_{idx}"
        time_range = _safe_dict(beat.get("time_range"))
        t0 = _round3(time_range.get("t0"))
        t1 = _round3(time_range.get("t1"))
        if t1 < t0:
            t1 = t0
        beat_primary_subject = str(beat.get("beat_primary_subject") or "").strip()
        beat_mode = str(beat.get("beat_mode") or "").strip()
        beat_purpose = str(beat.get("beat_purpose") or beat.get("purpose") or beat_mode).strip()
        hero_world_mode = str(beat.get("hero_world_mode") or "").strip()
        has_vocal = beat_primary_subject.lower() == "character_1"
        route = "ia2v" if has_vocal else "i2v"
        role_row = _safe_dict(cast_rows.get(segment_id))
        normalized.append(
            {
                "segment_id": segment_id,
                "scene_id": segment_id,
                "t0": t0,
                "t1": t1,
                "duration_sec": _round3(max(0.0, t1 - t0)),
                "transcript_slice": "",
                "rhythmic_anchor": "",
                "intensity": "",
                "is_lip_sync_candidate": has_vocal,
                "arc_role": "",
                "beat_purpose": beat_purpose,
                "emotional_key": "",
                "primary_role": str(role_row.get("primary_role") or beat_primary_subject).strip(),
                "secondary_roles": [str(v).strip() for v in _safe_list(role_row.get("secondary_roles")) if str(v).strip()],
                "presence_mode": str(role_row.get("presence_mode") or "").strip(),
                "presence_weight": str(role_row.get("presence_weight") or "").strip(),
                "performance_focus": bool(role_row.get("performance_focus")) or has_vocal,
                "beat_mode": beat_mode,
                "hero_world_mode": hero_world_mode,
                "beat_primary_subject": beat_primary_subject,
                "subject_presence_requirement": str(beat.get("subject_presence_requirement") or "").strip(),
                # Keep scene-level fallback fields available to downstream prompt/final adapters.
                "route": route,
                "has_vocal": has_vocal,
                "video_prompt": str(beat.get("video_prompt") or beat_purpose).strip(),
                "image_prompt": str(beat.get("image_prompt") or beat_purpose).strip(),
                "continuity": "Preserve identity, world, and style continuity across scenes.",
            }
        )
    return normalized, list(dict.fromkeys(missing_core_source_segments))


def _apply_route_to_row(
    *,
    row: dict[str, Any],
    route: str,
    source_row: dict[str, Any],
    role_row: dict[str, Any],
    character_1_appearance_mode: str,
) -> dict[str, Any]:
    active_roles = [
        str(v).strip()
        for v in (
            role_row.get("active_roles")
            or [role_row.get("primary_role"), *_safe_list(role_row.get("secondary_roles"))]
        )
        if str(v).strip()
    ]
    out = _safe_dict(deepcopy(row))
    out["route"] = route
    out["story_beat_type"] = _repair_story_beat_type(str(out.get("story_beat_type") or ""), route)
    composition = _safe_dict(out.get("composition"))
    if route == "ia2v":
        transcript_slice = str(source_row.get("transcript_slice") or "").strip()
        out["speaker_role"] = "character_1"
        out["vocal_owner_role"] = "character_1"
        out["lip_sync_allowed"] = True
        out["lip_sync_priority"] = "high"
        out["mouth_visible_required"] = True
        out["singing_readiness_required"] = True
        out["object_action_allowed"] = False
        out["foreground_performance_rule"] = str(out.get("foreground_performance_rule") or "").strip() or "face_readability_for_vocal_window"
        out["spoken_line"] = str(out.get("spoken_line") or "").strip() or transcript_slice
        out["visual_focus_role"] = "character_1"
        composition["subject_priority"] = "hero"
    else:
        out["speaker_role"] = ""
        out["vocal_owner_role"] = ""
        out["spoken_line"] = ""
        out["lip_sync_allowed"] = False
        out["lip_sync_priority"] = "none"
        out["mouth_visible_required"] = False
        out["singing_readiness_required"] = False
        out["foreground_performance_rule"] = ""
        if route == "i2v":
            out["object_action_allowed"] = bool(out.get("object_action_allowed", True))
            if character_1_appearance_mode == "lip_sync_only":
                composition["subject_priority"] = "environment"
            if _is_world_beat(source_row):
                world_focus = _select_world_focus_role(active_roles)
                out["visual_focus_role"] = world_focus or ""
        elif route == "first_last":
            composition["subject_priority"] = composition.get("subject_priority") or "hero"
    out["composition"] = composition
    return out


def _final_semantic_route_rebalance(
    *,
    normalized_storyboard: list[dict[str, Any]],
    scene_segment_rows: list[dict[str, Any]],
    role_lookup: dict[str, dict[str, Any]],
    route_budget_target: dict[str, int],
    character_1_appearance_mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not normalized_storyboard or len(normalized_storyboard) != len(scene_segment_rows):
        return normalized_storyboard, {"applied": False, "reason": "storyboard_size_mismatch"}
    source_by_segment = {str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row) for row in scene_segment_rows}
    row_by_segment = {str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row) for row in normalized_storyboard}
    ordered_ids = [str(_safe_dict(row).get("segment_id") or "").strip() for row in scene_segment_rows]
    if any(not segment_id or segment_id not in row_by_segment for segment_id in ordered_ids):
        return normalized_storyboard, {"applied": False, "reason": "segment_coverage_mismatch"}
    target = {
        "i2v": max(0, int(_safe_dict(route_budget_target).get("i2v") or 0)),
        "ia2v": max(0, int(_safe_dict(route_budget_target).get("ia2v") or 0)),
        "first_last": max(0, int(_safe_dict(route_budget_target).get("first_last") or 0)),
    }
    counts = {
        "i2v": sum(1 for sid in ordered_ids if str(row_by_segment[sid].get("route") or "").strip().lower() == "i2v"),
        "ia2v": sum(1 for sid in ordered_ids if str(row_by_segment[sid].get("route") or "").strip().lower() == "ia2v"),
        "first_last": sum(1 for sid in ordered_ids if str(row_by_segment[sid].get("route") or "").strip().lower() == "first_last"),
    }
    before_counts = dict(counts)
    if counts == target:
        return normalized_storyboard, {"applied": False, "reason": "already_balanced"}

    changed_segments: list[dict[str, str]] = []
    ia2v_deficit = max(0, target["ia2v"] - counts["ia2v"])
    if ia2v_deficit > 0 and counts["i2v"] > target["i2v"]:
        promote_candidates: list[tuple[int, str]] = []
        for idx, segment_id in enumerate(ordered_ids):
            row = row_by_segment[segment_id]
            if str(row.get("route") or "").strip().lower() != "i2v":
                continue
            source_row = source_by_segment.get(segment_id, {})
            role_row = _safe_dict(role_lookup.get(segment_id))
            active_roles = [
                str(v).strip()
                for v in (
                    role_row.get("active_roles")
                    or [role_row.get("primary_role"), *_safe_list(role_row.get("secondary_roles"))]
                )
                if str(v).strip()
            ]
            can_enforce, _reasons = _can_enforce_ia2v_row(
                source_row=source_row,
                spoken_line=str(row.get("spoken_line") or "").strip(),
                transcript_slice=str(source_row.get("transcript_slice") or "").strip(),
                is_lip_sync_candidate=bool(source_row.get("is_lip_sync_candidate")),
                active_roles=active_roles,
            )
            if not can_enforce:
                continue
            beat_mode = str(source_row.get("beat_mode") or "").strip().lower()
            beat_primary_subject = str(source_row.get("beat_primary_subject") or "").strip().lower()
            hero_world_mode = str(source_row.get("hero_world_mode") or "").strip().lower()
            score = 0
            if beat_primary_subject == "character_1":
                score += 6
            if beat_mode == "performance":
                score += 5
            if hero_world_mode == "hero_foreground":
                score += 4
            if bool(source_row.get("is_lip_sync_candidate")):
                score += 3
            if str(source_row.get("transcript_slice") or "").strip():
                score += 2
            if _is_world_beat(source_row):
                score -= 6
            if _is_final_emotional_payoff_candidate(source_row, idx=idx, total=len(ordered_ids)):
                score += 8
            promote_candidates.append((score - idx, segment_id))
        for _score, segment_id in sorted(promote_candidates, key=lambda item: item[0], reverse=True)[:ia2v_deficit]:
            row_by_segment[segment_id] = _apply_route_to_row(
                row=row_by_segment[segment_id],
                route="ia2v",
                source_row=source_by_segment.get(segment_id, {}),
                role_row=_safe_dict(role_lookup.get(segment_id)),
                character_1_appearance_mode=character_1_appearance_mode,
            )
            changed_segments.append({"segment_id": segment_id, "from": "i2v", "to": "ia2v"})

    counts = {
        "i2v": sum(1 for sid in ordered_ids if str(row_by_segment[sid].get("route") or "").strip().lower() == "i2v"),
        "ia2v": sum(1 for sid in ordered_ids if str(row_by_segment[sid].get("route") or "").strip().lower() == "ia2v"),
        "first_last": sum(1 for sid in ordered_ids if str(row_by_segment[sid].get("route") or "").strip().lower() == "first_last"),
    }
    i2v_deficit = max(0, target["i2v"] - counts["i2v"])
    if i2v_deficit > 0 and counts["ia2v"] > target["ia2v"]:
        demote_candidates: list[tuple[int, str]] = []
        for idx, segment_id in enumerate(ordered_ids):
            row = row_by_segment[segment_id]
            if str(row.get("route") or "").strip().lower() != "ia2v":
                continue
            source_row = source_by_segment.get(segment_id, {})
            if _is_final_emotional_payoff_candidate(source_row, idx=idx, total=len(ordered_ids)):
                continue
            score = 0
            if _is_world_beat(source_row):
                score += 7
            if str(source_row.get("beat_mode") or "").strip().lower() in {"world_observation", "social_texture", "world_pressure", "release", "symbolic_environment"}:
                score += 5
            if str(source_row.get("hero_world_mode") or "").strip().lower() == "world_foreground":
                score += 4
            if str(source_row.get("beat_primary_subject") or "").strip().lower() in {"world", "environment", "city", "location", "crowd"}:
                score += 4
            demote_candidates.append((score + idx, segment_id))
        for _score, segment_id in sorted(demote_candidates, key=lambda item: item[0], reverse=True)[:i2v_deficit]:
            row_by_segment[segment_id] = _apply_route_to_row(
                row=row_by_segment[segment_id],
                route="i2v",
                source_row=source_by_segment.get(segment_id, {}),
                role_row=_safe_dict(role_lookup.get(segment_id)),
                character_1_appearance_mode=character_1_appearance_mode,
            )
            changed_segments.append({"segment_id": segment_id, "from": "ia2v", "to": "i2v"})

    rebalanced_rows = [row_by_segment[segment_id] for segment_id in ordered_ids]
    final_counts = {
        "i2v": sum(1 for row in rebalanced_rows if str(row.get("route") or "").strip().lower() == "i2v"),
        "ia2v": sum(1 for row in rebalanced_rows if str(row.get("route") or "").strip().lower() == "ia2v"),
        "first_last": sum(1 for row in rebalanced_rows if str(row.get("route") or "").strip().lower() == "first_last"),
    }
    return rebalanced_rows, {
        "applied": bool(changed_segments),
        "target_counts": target,
        "before_counts": before_counts,
        "after_counts": final_counts,
        "changed_segments": changed_segments,
    }


def _expected_scene_count_from_package(package: dict[str, Any]) -> int:
    audio_map = _safe_dict(package.get("audio_map"))
    audio_count = len(_safe_list(audio_map.get("scene_candidate_windows")))
    if audio_count <= 0:
        audio_count = len(_safe_list(audio_map.get("phrase_units")))
    if audio_count > 0:
        return audio_count
    story_core = _safe_dict(package.get("story_core"))
    story_core_v1 = _safe_dict(story_core.get("story_core_v1"))
    if not story_core_v1 and str(story_core.get("schema_version") or "").startswith("core_v1"):
        story_core_v1 = story_core
    beat_map = _safe_dict(story_core_v1.get("beat_map") or package.get("beat_map"))
    return len(_safe_list(beat_map.get("beats")))


def _build_scene_role_lookup(role_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    print("[SCENES] using scene_casting as source of truth")
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
    for row_raw in _safe_list(role_plan.get("scene_casting")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("segment_id") or "").strip()
        if scene_id:
            lookup[scene_id] = {
                "scene_id": scene_id,
                "segment_id": scene_id,
                "primary_role": str(row.get("primary_role") or "").strip(),
                "secondary_roles": _safe_list(row.get("secondary_roles")),
            }
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
    director_config = _safe_dict(input_pkg.get("director_config"))
    ia2v_ratio = director_config.get("ia2v_ratio")
    i2v_ratio = director_config.get("i2v_ratio")
    ia2v_locations = _safe_list(director_config.get("ia2v_locations")) or []
    i2v_locations = _safe_list(director_config.get("i2v_locations")) or []
    intro_scenes = _safe_list(director_config.get("intro_scenes")) or []
    camera_style = director_config.get("camera_style")
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    compiled_contract = _safe_dict(role_plan.get("compiled_contract"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    ownership_binding_inventory = _build_ref_binding_inventory(refs_inventory)
    scene_windows = _build_scene_windows(audio_map)
    scene_segment_rows, missing_core_source_segments = _build_scene_segment_rows(audio_map, story_core, role_plan)
    role_identity_gender_map = _extract_role_identity_gender_map(input_pkg)
    character_appearance_modes_by_role = _extract_character_appearance_modes(input_pkg)
    character_presence_modes_by_role = {
        role: character_presence_mode_from_appearance_mode(mode)
        for role, mode in character_appearance_modes_by_role.items()
    }
    vocal_gender = _resolve_vocal_gender(audio_map, input_pkg)
    vocal_owner_role = _resolve_vocal_owner_role(vocal_gender, role_identity_gender_map)
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    hard_route_assignments = _safe_dict(creative_config.get("hard_route_assignments_by_segment"))
    hard_route_map_applied = bool(hard_route_assignments)
    resolved_scene_count = _expected_scene_count_from_package(package)
    route_budget_target, hard_short_clip_target = _route_budget_target_for_plan(resolved_scene_count, creative_config)
    if ia2v_ratio is not None:
        vocal_segments = [
            s for s in scene_segment_rows
            if s.get("is_lip_sync_candidate")
        ]
        max_possible_ia2v = len(vocal_segments)
        if max_possible_ia2v == 0:
            ia2v_count = 0
            i2v_count = resolved_scene_count
        else:
            ia2v_count = min(
                max_possible_ia2v,
                max(1, int(round(resolved_scene_count * float(ia2v_ratio))))
            )
            # NOTE: if new route types are added in future, this line must be adjusted
            i2v_count = resolved_scene_count - ia2v_count
        route_budget_target["ia2v"] = ia2v_count
        route_budget_target["i2v"] = i2v_count
    route_budget_target["first_last"] = 0
    route_budget_original_targets = {
        "i2v": max(0, int(_safe_dict(creative_config.get("route_targets_per_block")).get("i2v") or 0)),
        "ia2v": max(0, int(_safe_dict(creative_config.get("route_targets_per_block")).get("ia2v") or 0)),
        "first_last": max(0, int(_safe_dict(creative_config.get("route_targets_per_block")).get("first_last") or 0)),
    }
    route_budget_preset = str(creative_config.get("route_strategy_preset") or "").strip().lower()
    route_budget_resolved_from = "audio_map_segments_count" if route_budget_preset == "no_first_last_50_50_0" else "creative_config"
    route_strategy_active = _route_strategy_active(creative_config)
    story_guidance = story_guidance_route_mix_doctrine(story_core.get("story_guidance"))
    world_summary, world_summary_used = _build_scene_world_summary(role_plan, story_core)
    model_id = _resolve_active_video_model_id(package)
    route_capability_profiles = {
        route: {
            "route_type": route,
            "profile": get_video_model_capability_profile(model_id, route),
            "scene_grammar_hints": get_scene_grammar_hints(model_id, route),
        }
        for route in ("i2v", "ia2v")
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
            "narrative_segments": _safe_list(story_core.get("narrative_segments")),
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
            "vocal_gender": vocal_gender,
            "vocal_owner_role": vocal_owner_role,
        },
        "character_appearance_modes_by_role": character_appearance_modes_by_role,
        "character_presence_modes_by_role": character_presence_modes_by_role,
        "director_control": {
            "ia2v_ratio": ia2v_ratio,
            "i2v_ratio": i2v_ratio,
            "ia2v_locations": ia2v_locations,
            "i2v_locations": i2v_locations,
            "intro_scenes": intro_scenes,
            "camera_style": camera_style,
        },
        "role_plan": {
            "roles_version": str(role_plan.get("roles_version") or ""),
            "roster": _safe_list(role_plan.get("roster")),
            "scene_casting": _safe_list(role_plan.get("scene_casting")),
            "world_continuity": _safe_dict(story_core.get("world_lock")) or _safe_dict(role_plan.get("world_continuity")),
            "world_summary": world_summary,
            "scene_roles": _safe_list(role_plan.get("scene_casting")),
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
            "route_budget_contract": {
                "target_total_scenes": resolved_scene_count,
                "target_counts": route_budget_target,
                "original_target_counts": route_budget_original_targets,
                "resolved_scene_count": resolved_scene_count,
                "resolved_from": route_budget_resolved_from,
                "first_last_forbidden": True,
                "ia2v_requires_vocal_or_speech_window": True,
                "targets_are_hard_for_short_clip": bool(hard_short_clip_target),
                "gemini_must_choose_segment_assignment": not hard_route_map_applied,
                "backend_must_not_choose_dramaturgy": True,
                "route_strategy_active": route_strategy_active,
                "route_strategy_mode": str(creative_config.get("route_strategy_mode") or "auto"),
                "route_strategy_preset": route_budget_preset,
                "hard_route_assignments_by_segment": hard_route_assignments,
                "hardRouteMapApplied": hard_route_map_applied,
                "route_assignment_source": "creative_config.hard_route_assignments_by_segment" if hard_route_map_applied else "gemini",
                "routeAssignmentSource": "creative_config.hard_route_assignments_by_segment" if hard_route_map_applied else "gemini",
            },
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
        "vocal_gender": vocal_gender,
        "vocal_owner_role": vocal_owner_role,
        "role_identity_gender_map": role_identity_gender_map,
        "character_appearance_modes_by_role": character_appearance_modes_by_role,
        "character_presence_modes_by_role": character_presence_modes_by_role,
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


def _build_compact_route_budget_retry_context(context: dict[str, Any]) -> dict[str, Any]:
    audio_segments: list[dict[str, Any]] = []
    for row in _safe_list(_safe_dict(context.get("audio_map")).get("segments")):
        segment = _safe_dict(row)
        audio_segments.append(
            {
                "segment_id": str(segment.get("segment_id") or "").strip(),
                "t0": _round3(segment.get("t0")),
                "t1": _round3(segment.get("t1")),
                "duration_sec": _round3(segment.get("duration_sec")),
                "transcript_slice": str(segment.get("transcript_slice") or "").strip(),
                "is_lip_sync_candidate": bool(segment.get("is_lip_sync_candidate")),
            }
        )
    narrative_segments: list[dict[str, Any]] = []
    story_core_obj = _safe_dict(context.get("story_core"))
    for row in _safe_list(story_core_obj.get("narrative_segments")):
        segment = _safe_dict(row)
        narrative_segments.append(
            {
                "segment_id": str(segment.get("segment_id") or "").strip(),
                "beat_purpose": str(segment.get("beat_purpose") or "").strip(),
                "emotional_key": str(segment.get("emotional_key") or "").strip(),
                "arc_role": str(segment.get("arc_role") or "").strip(),
            }
        )
    if not narrative_segments:
        for row in _safe_list(_safe_dict(context.get("audio_map")).get("segments")):
            segment = _safe_dict(row)
            narrative_segments.append(
                {
                    "segment_id": str(segment.get("segment_id") or "").strip(),
                    "transcript_slice": str(segment.get("transcript_slice") or "").strip(),
                }
            )
    route_budget_contract = _safe_dict(_safe_dict(context.get("clip_scene_policy")).get("route_budget_contract"))
    target_total_scenes = int(route_budget_contract.get("target_total_scenes") or len(audio_segments))
    target_counts = _safe_dict(route_budget_contract.get("target_counts"))
    return {
        "audio_map": {"segments": audio_segments, "vocal_owner_role": str(_safe_dict(context.get("audio_map")).get("vocal_owner_role") or "")},
        "story_core": {"narrative_segments": narrative_segments},
        "role_plan": {"scene_casting": _safe_list(_safe_dict(context.get("role_plan")).get("scene_casting"))},
        "character_appearance_modes_by_role": _safe_dict(context.get("character_appearance_modes_by_role")),
        "route_budget_contract": {
            "target_total_scenes": target_total_scenes,
            "target_counts": target_counts,
            "first_last_forbidden": bool(route_budget_contract.get("first_last_forbidden")),
            "preset": str(route_budget_contract.get("route_strategy_preset") or ""),
            "hard_route_assignments_by_segment": _safe_dict(route_budget_contract.get("hard_route_assignments_by_segment")),
        },
    }


def _build_director_control_prompt_block(context: dict[str, Any]) -> str:
    director_control = _safe_dict(context.get("director_control"))
    ia2v_ratio = director_control.get("ia2v_ratio")
    i2v_ratio = director_control.get("i2v_ratio")
    ia2v_locations = [str(item).strip() for item in _safe_list(director_control.get("ia2v_locations")) if str(item).strip()]
    i2v_locations = [str(item).strip() for item in _safe_list(director_control.get("i2v_locations")) if str(item).strip()]
    intro_scenes = [str(item).strip() for item in _safe_list(director_control.get("intro_scenes")) if str(item).strip()]
    camera_style = str(director_control.get("camera_style") or "").strip().lower()

    if not any([ia2v_ratio is not None, i2v_ratio is not None, ia2v_locations, i2v_locations, intro_scenes, camera_style]):
        return ""

    lines = [
        "DIRECTOR CONFIG:\\n",
        "If present, this is the primary user-approved creative brief. Use it before creative_config.\\n",
        "* Respect ia2v_ratio vs i2v_ratio when distributing scenes.\\n",
        "* ia2v scenes MUST be used for vocal/performance moments.\\n",
        "* i2v scenes MUST be used for environment, intro, transitions.\\n",
    ]
    if ia2v_ratio is not None or i2v_ratio is not None:
        lines.append(f"* Ratio target hint: ia2v_ratio={ia2v_ratio}, i2v_ratio={i2v_ratio}.\\n")
    if ia2v_locations:
        lines.append(f"* ia2v_locations hard scope: {json.dumps(ia2v_locations, ensure_ascii=False)}.\\n")
        lines.append("* Only place performance (ia2v) scenes in these locations.\\n")
    if i2v_locations:
        lines.append(f"* i2v_locations hard scope: {json.dumps(i2v_locations, ensure_ascii=False)}.\\n")
        lines.append("* Use these locations for environment scenes.\\n")
    if intro_scenes:
        lines.append(f"* intro_scenes hard order hint: {json.dumps(intro_scenes, ensure_ascii=False)}.\\n")
        lines.append("* The first 1–3 scenes MUST match these types in order.\\n")
        lines.append("* Example: intro_scenes=[\"station_wide\",\"train_arrival\"] => first scenes reflect station -> train -> boarding.\\n")
        lines.append("* INTRO SCENE TYPES MAPPING:\\n")
        lines.append("  - \"station_wide\" → wide environment, large scale, no close character\\n")
        lines.append("  - \"train_arrival\" → train movement, anticipation\\n")
        lines.append("  - \"boarding\" → entering space, transition moment\\n")
        lines.append("  - \"window_reflection\" → introspective reflection shot\\n")
        lines.append("  - \"hero_closeup\" → emotional face close-up\\n")
    lines.append("* For each scene, ALWAYS assign location_zone.\\n")
    lines.append("* location_zone MUST match allowed ia2v_locations or i2v_locations when provided.\\n")
    lines.extend(
        [
            "* camera_style must influence composition.framing and visual_motion.camera_intent.\\n",
            "* camera_style=\"cinematic_glide\": medium/wide + slow push-in.\\n",
            "* camera_style=\"still_witness\": static wide shot.\\n",
            "* camera_style=\"emotional_proximity\": close_up + minimal motion.\\n",
        ]
    )
    if camera_style:
        lines.append(f"* Requested camera_style: \"{camera_style}\".\\n")
    lines.append("Director control has HIGHER priority than generic scene diversity. If conflict occurs, follow director_config.\\n")
    lines.append("Do not ignore director_config. It is a hard constraint unless impossible.\\n")
    return "".join(lines)


def _build_prompt(context: dict[str, Any], *, validation_feedback: str = "", prompt_mode: str = "default") -> str:
    feedback_block = ""
    if validation_feedback:
        feedback_block = (
            "PREVIOUS OUTPUT FAILED ROUTE-BUDGET VALIDATION.\n"
            f"Fix exactly: {validation_feedback}\n"
        )
    hard_route_map = _safe_dict(
        _safe_dict(_safe_dict(context.get("clip_scene_policy")).get("route_budget_contract")).get("hard_route_assignments_by_segment")
    )
    route_assignment_instruction = (
        "Preserve the provided route for each segment_id whenever that route remains scene-valid after vocal/instrumental validation.\n"
        if hard_route_map
        else "Choose segment routes according to target_counts and dramaturgy.\\n"
    )
    if prompt_mode == "compact_route_budget_retry":
        compact_context = _build_compact_route_budget_retry_context(context)
        compact_budget = _safe_dict(compact_context.get("route_budget_contract"))
        compact_target_counts = _safe_dict(compact_budget.get("target_counts"))
        compact_target_total_scenes = int(compact_budget.get("target_total_scenes") or 0)
        ia2v_target = int(compact_target_counts.get("ia2v") or 0)
        i2v_target = int(compact_target_counts.get("i2v") or 0)
        first_last_target = int(compact_target_counts.get("first_last") or 0)
        first_last_line = "No first_last scenes allowed.\n"
        return (
            "You are SCENES stage only.\n"
            "Return STRICT JSON only. No markdown, no prose.\n"
            "Return exactly one storyboard row per segment_id from audio_map.segments.\n"
            "Do not invent/remove segment_id and do not return empty storyboard.\n"
            "Route budget contract is HARD and must be matched exactly.\n"
            f"Return exactly {compact_target_total_scenes} storyboard rows.\n"
            "Required route budget:\n"
            f"ia2v: {ia2v_target}\n"
            f"i2v: {i2v_target}\n"
            f"first_last: {first_last_target}\n"
            f"{first_last_line}"
            "If any scene is classified as state_transition, convert it to i2v.\n"
            "state_transition is allowed as narrative concept,\n"
            "but must always be rendered as i2v.\n"
            "ia2v scenes must be assigned ONLY to segments where is_lip_sync_candidate=true.\n"
            "Prefer assigning ia2v to the strongest vocal or emotional peaks, not just any valid segment.\n"
            "Do not assign ia2v to instrumental segments.\n"
            "If route_budget_contract.hard_route_assignments_by_segment is present:\n"
            "- treat it as requested creative route map that should be preserved whenever valid;\n"
            "- never keep ia2v on instrumental / no-vocal / non-lipsync-invalid windows; downgrade those rows to i2v;\n"
            "- keep target_counts as close as possible after route-validity checks; do not fake ia2v just to satisfy counts.\n"
            "If character_1 appearanceMode is lip_sync_only:\n"
            "- Treat as character_presence_mode=vocal_anchor preference.\n"
            "- ia2v rows: character_1 is physical speaker; speaker_role=character_1; lip_sync_allowed=true; mouth_visible_required=true.\n"
            "- i2v rows: prefer non-primary character_1 framing, but character_1 MAY still appear as silhouette/walking/background presence.\n"
            "World beats must be truly world-driven (social texture, pressure, threshold, aftermath, instrumental release) with no fake singer-presence.\n"
            f"Fix exactly: {validation_feedback}\n"
            "Output contract:\n"
            "{\n"
            '  "scenes_version":"1.1",\n'
            '  "storyboard":[{"segment_id":"seg_01","route":"i2v","route_reason":"","route_selection_reason":"","scene_goal":"","narrative_function":"","story_beat_type":"physical_event|vocal_emotion|state_transition","photo_staging_goal":"","ltx_video_goal":"","background_story_evidence":"","foreground_performance_rule":"","object_action_allowed":true,"singing_readiness_required":false,"ia2v_photo_readability_notes":"","speaker_role":"","vocal_owner_role":"","spoken_line":"","speaker_confidence":0.0,"lip_sync_allowed":false,"lip_sync_priority":"none","mouth_visible_required":false,"listener_reaction_allowed":true,"reaction_role":"","visual_motion":{"subject_motion":"","camera_intent":"","pacing":"stable","energy_alignment":"match"},"composition":{"framing":"medium","subject_priority":"hero","layout":"centered","depth_strategy":"layered"},"audio_visual_sync":"","starts_from_previous_logic":"","ends_with_state":"","continuity_with_next":"","potential_contradiction":"","fix_if_needed":"","lip_sync_shot_variant":"","performance_pose":"","camera_angle":"","gesture":"","location_zone":"","mouth_readability":"","why_this_lip_sync_shot_is_different":""}]\n'
            "}\n\n"
            f"SCENE_PLANNING_CONTEXT:\n{json.dumps(_compact_prompt_payload(compact_context), ensure_ascii=False)}"
        )
    return (
        "You are SCENES stage only.\\n"
        "Return STRICT JSON only. No markdown, no prose.\\n"
        "Return one storyboard row per segment_id from audio_map.segments.\\n"
        "Return exactly one scene per audio segment and preserve all segment_id values exactly once.\\n"
        "Do not invent or remove segments.\\n"
        "Do not mutate cast. Use role_plan.scene_casting/roster as cast source; compiled_contract is legacy fallback only.\\n"
        "WHOLE-STORY CONTINUITY is mandatory: treat all scenes as one continuous music video progression.\\n"
        "Do not teleport hero back to prior position after completed movement unless explicit justified match cut.\\n"
        "Track previous ending position/state, next start position/state, movement direction, location zone, object state, emotional progression, and performance energy progression.\\n"
        "For ia2v/lip-sync scenes ensure vocal-shot diversity across adjacent scenes while preserving mouth readability.\\n"
        "No first_last scenes allowed.\\n"
        "first_last route is forbidden for this clip mode.\\n"
        "If any scene is classified as state_transition, convert it to i2v.\\n"
        "state_transition is allowed as narrative concept,\\n"
        "but must always be rendered as i2v.\\n"
        "ia2v scenes must be assigned ONLY to segments where is_lip_sync_candidate=true.\\n"
        "Prefer assigning ia2v to the strongest vocal or emotional peaks, not just any valid segment.\\n"
        "Do not assign ia2v to instrumental segments.\\n"
        "speaker_role is independent from primary_role: primary_role is visual focus; speaker_role is who actually speaks this segment.\\n"
        "audio_map.segments[].is_lip_sync_candidate is permission only, not obligation and not a speaker-identity oracle.\\n"
        "Do not treat audio_map.vocal_owner_role as strict guard for route eligibility.\\n"
        "Choose ia2v by scene evidence: spoken_line or transcript_slice, lip-sync permission, readable on-screen mouth/face framing, valid duration window, non-world-beat logic, and character_1 present in frame.\\n"
        "World beats / observer beats / environment beats / purely reaction beats should stay i2v (not ia2v).\\n"
        "For i2v scenes, set speaker_role to an empty string. Do not use 'unknown'.\\n"
        "For ia2v scenes, if vocal/performance evidence is real and character_1 is on-screen, you may output ia2v even when speaker_role/vocal_owner_role are uncertain.\\n"
        "Backend canonicalizes valid ia2v rows to character_1 roles and lip-sync flags; invalid ia2v rows automatically fallback to i2v.\\n"
        "For ia2v rows still prefer setting: speaker_role=character_1, vocal_owner_role=character_1, lip_sync_allowed=true, mouth_visible_required=true, singing_readiness_required=true.\\n"
        "For ia2v rows, spoken_line must be copied from segment transcript/transcript_slice when available.\\n"
        "For i2v scenes enforce: speaker_role=\"\", vocal_owner_role=\"\", lip_sync_allowed=false, mouth_visible_required=false.\\n"
        "For ia2v scenes enforce controlled camera: no chaotic handheld, one movement axis, safe motion.\\n"
        "Respect character_appearance_modes_by_role contract from context.\\n"
        "Treat character appearanceMode as behavior preference, not hard scene constraint. Never reject or drop scenes because of appearanceMode alone.\\n"
        "Map character_1 appearanceMode into character_presence_mode policy:\\n"
        "- lip_sync_only => vocal_anchor: character_1 must be visible in vocal ia2v scenes; in i2v scenes character_1 MAY appear as silhouette/walking/background while non-character_1 world focus is still preferred.\\n"
        "- story_visible/everywhere_meaningful => adaptive_presence: adapt visibility to scene importance and narrative clarity.\\n"
        "- background_only/background => background_presence: character_1 may appear but never as primary subject in i2v.\\n"
        "- offscreen_voice/offscreen => offscreen_voice: voice-only with no visual presence.\\n"
        "Do not write stock noir shorthand loops (same empty streets/courtyard/port/gate beats repeated with renamed locations).\\n"
        "Strengthen scene writing with specific lived-in behavior, social tension signals, witness detail, threshold pauses, and aftermath traces.\\n"
        "For each row keep scene_goal / narrative_function / photo_staging_goal / ltx_video_goal distinct and dramaturgic, not generic filler.\\n"
        "For two-active conflict scenes keep lip-sync sparse: strong lines only, no more than 2 consecutive lip-sync scenes.\\n"
        "Do not output prompt language, quality buzzwords, renderer parameters, API/workflow payload, or final video payload.\\n"
        "Do not use raw director text as free authoring source beyond this package context.\\n"
        "Use story_core doctrine as guidance only; do not change doctrine.\\n"
        "Use technical capability canon only as allowed/discouraged/unstable route behavior context.\\n"
        "Use route baseline only as capability context if available, not as prompt text.\\n"
        "Do not change timing grid. segment_id/t0/t1/duration are fixed by input segments.\\n"
        "Allowed enums:\\n"
        "- route: i2v|ia2v\\n"
        "- pacing: fluid|staccato|stable\\n"
        "- energy_alignment: match|counterpoint|build_against|release_after\\n"
        "- framing: close_up|medium|wide|detail|silhouette|overhead\\n"
        "- subject_priority: hero|ensemble|object|environment\\n"
        "- layout: centered|rule_of_thirds|off_balance|symmetrical\\n"
        "- depth_strategy: flat|layered|deep\\n"
        "DIRECTOR CONFIG (if present) has priority over creative_config hints.\\n"
        "If director_config is missing, use creative_config as fallback only.\\n"
        "USER ROUTE STRATEGY IS A HARD CREATIVE CONSTRAINT only when explicit director-config ratio is present.\\n"
        "If route_budget_contract.hard_route_assignments_by_segment is non-empty, preserve requested route per segment_id whenever that route is scene-valid.\n"
        "If a requested ia2v row is invalid for lip-sync (instrumental/no-vocal, non-speaking world beat, missing valid mouth-readable performance evidence), downgrade it to i2v instead of faking ia2v.\n"
        "If route_budget_contract.targets_are_hard_for_short_clip=true, satisfy target_counts as closely as possible after route-validity checks; do not force invalid ia2v just to hit counts.\n"
        f"{route_assignment_instruction}"
        "Do not let backend assign dramaturgy; backend only validates budget compliance.\\n"
        "For ia2v: vocal emotional performance scene, not physical action scene. Prefer strong vocal/speech windows, performance moments, emotional peaks, hooks; "
        "set speaker_role when possible; do not reject ia2v only because speaker_role/vocal_owner_role are not prefilled; lip_sync_allowed=true only with valid scene evidence; "
        "mouth_visible_required=true for real lip-sync scenes.\\n"
        "For i2v: one physical story action as foreground event; prefer movement/transit/atmosphere/world continuity/cutaway/wide visual breathing room; "
        "do not require mouth-visible lip-sync.\\n"
        "No first_last scenes allowed.\\n"
        "If any scene is classified as state_transition, convert it to i2v.\\n"
        f"{_build_director_control_prompt_block(context)}"
        f"{feedback_block}"
        "Output contract:\\n"
        "{\\n"
        '  "scenes_version":"1.1",\\n'
        '  "storyboard":[{"segment_id":"seg_01","route":"i2v","route_reason":"","route_selection_reason":"","scene_goal":"","narrative_function":"","story_beat_type":"physical_event|vocal_emotion|state_transition","photo_staging_goal":"","ltx_video_goal":"","background_story_evidence":"","foreground_performance_rule":"","object_action_allowed":true,"singing_readiness_required":false,"ia2v_photo_readability_notes":"","speaker_role":"","vocal_owner_role":"","spoken_line":"","speaker_confidence":0.0,"lip_sync_allowed":false,"lip_sync_priority":"none","mouth_visible_required":false,"listener_reaction_allowed":true,"reaction_role":"","visual_motion":{"subject_motion":"","camera_intent":"","pacing":"stable","energy_alignment":"match"},"composition":{"framing":"medium","subject_priority":"hero","layout":"centered","depth_strategy":"layered"},"audio_visual_sync":"","starts_from_previous_logic":"","ends_with_state":"","continuity_with_next":"","potential_contradiction":"","fix_if_needed":"","lip_sync_shot_variant":"","performance_pose":"","camera_angle":"","gesture":"","location_zone":"","mouth_readability":"","why_this_lip_sync_shot_is_different":""}]\\n'
        "}\\n\\n"
        f"SCENE_PLANNING_CONTEXT:\\n{json.dumps(_compact_prompt_payload(context), ensure_ascii=False)}"
    )


def _route_strategy_active(creative_config: dict[str, Any]) -> bool:
    mode = str(creative_config.get("route_strategy_mode") or "auto").strip().lower()
    targets = _safe_dict(creative_config.get("route_targets_per_block"))
    total_target = sum(max(0, int(targets.get(k) or 0)) for k in ("i2v", "ia2v", "first_last"))
    return mode in {"preset", "custom_counts"} and total_target > 0


def compute_no_first_last_50_50_targets(scene_count: int) -> dict[str, int]:
    n = max(0, int(scene_count or 0))
    return {
        "i2v": n // 2,
        "ia2v": (n + 1) // 2,
        "first_last": 0,
    }


def _resolve_mapped_scene_ia2v_ratio(
    creative_config: dict[str, Any],
    director_config: dict[str, Any],
) -> tuple[float, str]:
    director_ia2v_ratio = director_config.get("ia2v_ratio")
    if director_ia2v_ratio is not None:
        return _clamp_ratio(director_ia2v_ratio, 0.5), "director_config.ia2v_ratio"

    creative_targets = _safe_dict(creative_config.get("route_targets_per_block"))
    target_total = max(0, int(creative_targets.get("i2v") or 0)) + max(0, int(creative_targets.get("ia2v") or 0))
    if target_total > 0:
        ia2v_target = max(0, int(creative_targets.get("ia2v") or 0))
        return _clamp_ratio(float(ia2v_target) / float(target_total), 0.5), "creative_config.route_targets_per_block"

    preset_name = str(creative_config.get("route_strategy_preset") or "").strip().lower()
    if preset_name == "no_first_last_50_50_0":
        return 0.5, "preset_fallback.no_first_last_50_50_0"

    creative_lipsync_ratio = creative_config.get("lipsync_ratio")
    if creative_lipsync_ratio is not None:
        return _clamp_ratio(creative_lipsync_ratio, 0.5), "creative_config.lipsync_ratio"
    return 0.5, "default_fallback"


def _apply_route_budget_to_scene_rows(
    rows: list[dict[str, Any]],
    audio_map: dict[str, Any],
    creative_config: dict[str, Any],
    director_config: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    director_cfg = _safe_dict(director_config)
    segments = [seg for seg in _safe_list(audio_map.get("segments")) if isinstance(seg, dict)]
    windows = [seg for seg in _safe_list(audio_map.get("scene_candidate_windows")) if isinstance(seg, dict)]
    by_segment_id: dict[str, dict[str, Any]] = {}
    for source in (*segments, *windows):
        src = _safe_dict(source)
        seg_id = str(src.get("segment_id") or src.get("scene_id") or "").strip()
        if seg_id and seg_id not in by_segment_id:
            by_segment_id[seg_id] = src

    # Mapped scene_candidate_windows path currently supports only i2v/ia2v.
    # first_last is intentionally disabled here until start/end frame generation is route-aware.
    route_targets = _safe_dict(creative_config.get("route_targets_per_block"))
    first_last_count = 0

    ia2v_ratio, ia2v_ratio_source = _resolve_mapped_scene_ia2v_ratio(creative_config, director_cfg)
    candidate_scores: list[tuple[float, int, str]] = []
    candidate_segment_ids: set[str] = set()
    source_match_mode_by_segment: dict[str, str] = {}
    source_match_mode_by_index: dict[str, str] = {}
    for idx, row_raw in enumerate(rows):
        row = _safe_dict(row_raw)
        seg_id = str(row.get("segment_id") or row.get("scene_id") or "").strip()
        source_mode = "none"
        source = _safe_dict(by_segment_id.get(seg_id))
        if source:
            source_mode = "by_segment_id"
        elif idx < len(segments):
            source = _safe_dict(segments[idx])
            source_mode = "by_index_segments"
        elif idx < len(windows):
            source = _safe_dict(windows[idx])
            source_mode = "by_index_windows"
        if not seg_id:
            seg_id = str(source.get("segment_id") or source.get("scene_id") or f"seg_{idx + 1:02d}").strip()
            if seg_id:
                row["segment_id"] = seg_id
                row["scene_id"] = str(row.get("scene_id") or seg_id).strip()
        source_match_mode_by_index[str(idx)] = source_mode
        if seg_id:
            source_match_mode_by_segment[seg_id] = source_mode
        candidate = (
            bool(source.get("is_lip_sync_candidate"))
            or bool(source.get("has_vocal"))
            or bool(str(source.get("transcript_slice") or "").strip())
            or bool(str(source.get("transcript") or "").strip())
            or bool(str(row.get("transcript_slice") or "").strip())
            or bool(str(row.get("spoken_line") or "").strip())
        )
        if not candidate:
            continue
        candidate_segment_ids.add(seg_id)
        local_energy_band = str(source.get("local_energy_band") or "").strip().lower()
        semantic_weight = str(source.get("semantic_weight") or "").strip().lower()
        delivery_mode = str(source.get("delivery_mode") or "").strip().lower()
        release_candidate = bool(source.get("release_candidate"))
        intensity = _to_float(source.get("intensity"), 0.0)
        score = intensity
        if local_energy_band in {"high", "surge"}:
            score += 3.0
        if semantic_weight == "high":
            score += 2.0
        if delivery_mode in {"assertive", "pressurized", "final", "intimate"}:
            score += 2.0
        if release_candidate:
            score += 1.5
        candidate_scores.append((score, idx, seg_id))

    max_possible_ia2v = len(candidate_scores)
    if max_possible_ia2v <= 0:
        ia2v_count = 0
    else:
        ia2v_count = min(max_possible_ia2v, max(0, int(round(len(rows) * ia2v_ratio))))
    i2v_count = max(0, len(rows) - ia2v_count - first_last_count)

    candidate_scores.sort(key=lambda item: (-item[0], item[1]))
    selected_ia2v_indices = {idx for _, idx, _ in candidate_scores[:ia2v_count]}

    applied_rows: list[dict[str, Any]] = []
    selected_ia2v_segments: list[str] = []
    selected_i2v_segments: list[str] = []
    budget_route_locks_by_segment: dict[str, str] = {}
    for idx, row_raw in enumerate(rows):
        row = dict(_safe_dict(row_raw))
        seg_id = str(row.get("segment_id") or row.get("scene_id") or "").strip()
        source = _safe_dict(by_segment_id.get(seg_id))
        if not source:
            if idx < len(segments):
                source = _safe_dict(segments[idx])
            elif idx < len(windows):
                source = _safe_dict(windows[idx])
        if not seg_id:
            fallback_id = str(source.get("segment_id") or source.get("scene_id") or f"seg_{idx + 1:02d}").strip()
            row["segment_id"] = fallback_id
            row["scene_id"] = str(row.get("scene_id") or fallback_id).strip()
            seg_id = fallback_id
        if idx in selected_ia2v_indices:
            speaker_role = str(row.get("speaker_role") or row.get("primary_role") or "character_1").strip() or "character_1"
            spoken_line = str(row.get("spoken_line") or "").strip()
            if not spoken_line:
                spoken_line = str(
                    source.get("transcript_slice")
                    or source.get("transcript")
                    or row.get("transcript_slice")
                    or ""
                ).strip()
            row["route"] = "ia2v"
            row["lipSync"] = True
            row["renderMode"] = "lip_sync_music"
            row["requiresAudioSensitiveVideo"] = True
            row["speaker_role"] = speaker_role
            row["vocal_owner_role"] = str(row.get("vocal_owner_role") or speaker_role).strip() or speaker_role
            row["primary_role"] = str(row.get("primary_role") or speaker_role).strip() or speaker_role
            row["visual_focus_role"] = str(row.get("visual_focus_role") or speaker_role).strip() or speaker_role
            row["lip_sync_allowed"] = True
            row["lip_sync_priority"] = "high"
            row["mouth_visible_required"] = True
            row["singing_readiness_required"] = True
            row["audio_visual_sync"] = str(row.get("audio_visual_sync") or "vocal performance aligned to this audio segment").strip()
            row["story_beat_type"] = str(row.get("story_beat_type") or "performance").strip()
            row["scene_goal"] = str(row.get("scene_goal") or "vocal performance beat").strip()
            row["photo_staging_goal"] = str(
                row.get("photo_staging_goal") or "mouth-readable emotional performance shot"
            ).strip()
            row["ltx_video_goal"] = str(
                row.get("ltx_video_goal") or "natural singing performance, visible mouth, controlled motion"
            ).strip()
            row["spoken_line"] = spoken_line
            row["routeLocked"] = True
            row["route_lock_source"] = "mapped_route_budget"
            row["route_assignment_source"] = "mapped_route_budget"
            selected_ia2v_segments.append(seg_id)
        else:
            row["route"] = "i2v"
            row["lipSync"] = False
            row["requiresAudioSensitiveVideo"] = False
            row["lip_sync_allowed"] = False
            row["mouth_visible_required"] = False
            row["singing_readiness_required"] = False
            row["story_beat_type"] = str(row.get("story_beat_type") or "world_memory").strip()
            row["scene_goal"] = str(row.get("scene_goal") or "atmospheric world / memory beat").strip()
            row["routeLocked"] = True
            row["route_lock_source"] = "mapped_route_budget"
            row["route_assignment_source"] = "mapped_route_budget"
            selected_i2v_segments.append(seg_id)
        if seg_id:
            budget_route_locks_by_segment[seg_id] = str(row.get("route") or "").strip().lower()
        applied_rows.append(row)

    diagnostics = {
        "scene_plan_route_budget_applied_to_mapped_rows": True,
        "scene_plan_route_budget_source": "creative_config/director_config",
        "scene_plan_route_budget_ratio_source": ia2v_ratio_source,
        "scene_plan_route_budget_target_counts": {"i2v": i2v_count, "ia2v": ia2v_count, "first_last": 0},
        "scene_plan_route_budget_first_last_disabled_for_mapped_path": True,
        "scene_plan_mapped_first_last_target_removed": True,
        "scene_plan_mapped_ia2v_contract_filled": True,
        "scene_plan_route_budget_candidate_ia2v_count": max_possible_ia2v,
        "scene_plan_route_budget_selected_ia2v_segments": selected_ia2v_segments,
        "scene_plan_route_budget_selected_i2v_segments": selected_i2v_segments,
        "scene_plan_route_budget_candidate_segments": sorted(candidate_segment_ids),
        "scene_plan_route_locks_by_segment": budget_route_locks_by_segment,
        "scene_plan_requested_route_locks_by_segment": budget_route_locks_by_segment,
        "scene_plan_route_lock_applied": True,
        "scene_plan_route_lock_source": "mapped_route_budget",
        "scene_plan_route_assignment_source": "mapped_route_budget",
        "scene_plan_route_budget_after_lock": {"i2v": i2v_count, "ia2v": ia2v_count, "first_last": 0},
        "scene_plan_route_budget_rows_in": len(rows),
        "scene_plan_route_budget_rows_out": len(applied_rows),
        "scene_plan_route_budget_rows_missing_segment_id": sum(
            1 for row in applied_rows if not str(_safe_dict(row).get("segment_id") or "").strip()
        ),
        "scene_plan_route_budget_source_match_mode": source_match_mode_by_segment,
        "scene_plan_route_budget_source_match_mode_by_index": source_match_mode_by_index,
        "scene_plan_director_config_applied": bool(director_cfg),
        "scene_plan_director_config_keys": sorted(
            key
            for key in ("ia2v_ratio", "i2v_ratio", "ia2v_locations", "i2v_locations", "camera_style")
            if director_cfg.get(key) is not None
        ),
    }
    return applied_rows, diagnostics


def _route_budget_target_for_plan(total_scenes: int, creative_config: dict[str, Any]) -> tuple[dict[str, int], bool]:
    if total_scenes <= 0:
        return {"i2v": 0, "ia2v": 0, "first_last": 0}, False
    hard_map = _safe_dict(creative_config.get("hard_route_assignments_by_segment"))
    if not hard_map:
        hard_map = _safe_dict(creative_config.get("route_assignments_by_segment"))
    if hard_map:
        budget = {"i2v": 0, "ia2v": 0, "first_last": 0}
        for route in hard_map.values():
            clean = str(route or "").strip().lower()
            if clean in budget:
                budget[clean] += 1
        return budget, True
    preset_name = str(creative_config.get("route_strategy_preset") or "").strip().lower()
    if preset_name == "no_first_last_50_50_0":
        return compute_no_first_last_50_50_targets(total_scenes), True
    if not _route_strategy_active(creative_config):
        return _target_route_budget(total_scenes), False
    base_scene_count = max(1, int(creative_config.get("base_scene_count") or 8))
    targets = _safe_dict(creative_config.get("route_targets_per_block"))
    target_budget = {
        "i2v": max(0, int(targets.get("i2v") or 0)),
        "ia2v": max(0, int(targets.get("ia2v") or 0)),
        "first_last": max(0, int(targets.get("first_last") or 0)),
    }
    full_blocks = total_scenes // base_scene_count
    remainder = total_scenes % base_scene_count
    budget = {route: count * full_blocks for route, count in target_budget.items()}

    if remainder > 0:
        partial = dict(target_budget)
        reduction_order = ("first_last", "i2v", "ia2v")
        while sum(partial.values()) > remainder:
            reduced = False
            for route in reduction_order:
                if partial[route] > 0 and sum(partial.values()) > remainder:
                    partial[route] -= 1
                    reduced = True
            if not reduced:
                break
        if (
            total_scenes > base_scene_count
            and str(creative_config.get("extra_scene_policy") or "add_i2v").strip().lower() == "add_i2v"
            and partial["i2v"] == 0
            and sum(partial.values()) > 0
        ):
            if partial["ia2v"] > 0:
                partial["ia2v"] -= 1
                partial["i2v"] += 1
            elif partial["first_last"] > 0:
                partial["first_last"] -= 1
                partial["i2v"] += 1
        for route in ("i2v", "ia2v", "first_last"):
            budget[route] += partial[route]

    is_hard_short_clip_target = _route_strategy_active(creative_config) and sum(budget.values()) == total_scenes
    return budget, is_hard_short_clip_target


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


def _adjacent_route_pairs(scenes: list[dict[str, Any]], route_name: str) -> list[list[str]]:
    route = str(route_name or "")
    pairs: list[list[str]] = []
    for idx in range(1, len(scenes)):
        left = _safe_dict(scenes[idx - 1])
        right = _safe_dict(scenes[idx])
        if str(left.get("route") or "") != route or str(right.get("route") or "") != route:
            continue
        left_id = str(left.get("segment_id") or left.get("scene_id") or "").strip()
        right_id = str(right.get("segment_id") or right.get("scene_id") or "").strip()
        pairs.append([left_id, right_id])
    return pairs


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



def _collect_free_text_technical_leaks(raw_row: dict[str, Any]) -> list[dict[str, str]]:
    leaks: list[dict[str, str]] = []
    for field_path in SCENES_FREE_TEXT_LEAK_FIELD_PATHS:
        value: Any = raw_row
        for key in field_path:
            if not isinstance(value, dict):
                value = ""
                break
            value = value.get(key)
        text = str(value or "").strip()
        if not text:
            continue
        lower = text.lower()
        for token in sorted(SCENES_FREE_TEXT_CLEANUP_TOKENS, key=len, reverse=True):
            idx = lower.find(token)
            if idx < 0:
                continue
            excerpt = text[max(0, idx - 24): min(len(text), idx + len(token) + 24)]
            leaks.append({
                "field": ".".join(field_path),
                "token": token,
                "excerpt": excerpt,
            })
    return leaks


def _sanitize_free_text_technical_leaks(raw_row: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    cleaned = json.loads(json.dumps(raw_row, ensure_ascii=False))
    changed = False
    for field_path in SCENES_FREE_TEXT_LEAK_FIELD_PATHS:
        parent: Any = cleaned
        for key in field_path[:-1]:
            if not isinstance(parent, dict):
                parent = None
                break
            parent = parent.get(key)
        leaf = field_path[-1]
        if not isinstance(parent, dict):
            continue
        current = str(parent.get(leaf) or "")
        updated = current
        for token in sorted(SCENES_FREE_TEXT_CLEANUP_TOKENS, key=len, reverse=True):
            updated = updated.replace(token, " ").replace(token.upper(), " ").replace(token.title(), " ")
        updated = " ".join(updated.split()).strip(" ,.;:-")
        if updated != current:
            parent[leaf] = updated
            changed = True
    return cleaned, changed


def _default_story_beat_type_for_route(route: str) -> str:
    if route == "ia2v":
        return "vocal_emotion"
    if route == "first_last":
        return "state_transition"
    return "physical_event"


def _repair_story_beat_type(value: str, route: str) -> str:
    token = str(value or "").strip().lower()
    if token in ALLOWED_STORY_BEAT_TYPES:
        return token
    if token in {"environment", "atmosphere", "city_cutaway", "street_observation", "observational"}:
        return "physical_event"
    return _default_story_beat_type_for_route(route)


def _segment_route_semantic_scores(source_row: dict[str, Any], plan_row: dict[str, Any]) -> tuple[int, int, int]:
    beat_mode = str(source_row.get("beat_mode") or "").strip().lower()
    hero_world_mode = str(source_row.get("hero_world_mode") or "").strip().lower()
    beat_primary_subject = str(source_row.get("beat_primary_subject") or "").strip().lower()
    story_beat_type = str(plan_row.get("story_beat_type") or "").strip().lower()
    narrative_function = str(plan_row.get("narrative_function") or "").strip().lower()

    performance_score = 0
    world_score = 0
    first_last_score = 0
    if beat_mode == "performance":
        performance_score += 4
    if hero_world_mode == "hero_foreground":
        performance_score += 3
    if beat_primary_subject == "character_1":
        performance_score += 3
    if story_beat_type == "vocal_emotion":
        performance_score += 2

    if beat_mode in {"world_observation", "social_texture", "world_pressure", "release", "transition"}:
        world_score += 4
    if hero_world_mode == "world_foreground":
        world_score += 3
    if beat_primary_subject in {"world", "environment", "city", "location", "crowd"}:
        world_score += 2
    if story_beat_type == "physical_event":
        world_score += 1

    if beat_mode in {"transition", "release"}:
        first_last_score += 2
    if story_beat_type == "state_transition":
        first_last_score += 3
    if any(token in narrative_function for token in ("transition", "release", "afterglow", "afterimage")):
        first_last_score += 1
    return performance_score, world_score, first_last_score


def _source_row_route_seed(
    source_row: dict[str, Any],
    *,
    target_budget: dict[str, int],
) -> tuple[str, str, str]:
    text_blob = " ".join(
        [
            str(source_row.get("transcript_slice") or ""),
            str(source_row.get("beat_purpose") or ""),
            str(source_row.get("arc_role") or ""),
            str(source_row.get("emotional_key") or ""),
        ]
    ).strip().lower()
    world_tokens = {"world_observation", "social_texture", "world_pressure", "release", "symbolic_environment", "city", "environment"}
    performance_tokens = {"performance", "hero_foreground", "sing", "voice", "vocal", "confession", "emotion", "character_1"}
    performance_score = 0
    world_score = 0
    if bool(source_row.get("performance_focus")):
        performance_score += 3
    if bool(source_row.get("is_lip_sync_candidate")) and not _is_instrumental_or_no_vocal_text(source_row.get("transcript_slice")):
        performance_score += 3
    if any(token in text_blob for token in performance_tokens):
        performance_score += 2
    if any(token in text_blob for token in world_tokens) or _is_world_beat(source_row):
        world_score += 3
    if not text_blob:
        world_score += 1
    force_no_first_last = int(_safe_dict(target_budget).get("first_last") or 0) == 0
    route = "ia2v" if performance_score >= world_score else "i2v"
    if not force_no_first_last and "transition" in text_blob and world_score >= performance_score:
        route = "first_last"
    story_beat_type = _default_story_beat_type_for_route(route)
    route_reason = "grounded_empty_plan_fallback_seed"
    return route, story_beat_type, route_reason


def _build_grounded_empty_scene_plan_fallback(
    *,
    scene_segment_rows: list[dict[str, Any]],
    role_lookup: dict[str, dict[str, Any]],
    creative_config: dict[str, Any],
    character_appearance_modes_by_role: dict[str, str],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    expected_scene_count = len(scene_segment_rows)
    if expected_scene_count <= 0:
        return [], {"applied": False, "reason": "no_scene_segments"}
    if any(not str(_safe_dict(row).get("segment_id") or "").strip() for row in scene_segment_rows):
        return [], {"applied": False, "reason": "segment_id_missing"}
    if any(not _safe_dict(role_lookup.get(str(_safe_dict(row).get("segment_id") or "").strip())) for row in scene_segment_rows):
        return [], {"applied": False, "reason": "role_lookup_missing_segments"}

    route_budget_target, hard_short_clip_target = _route_budget_target_for_plan(expected_scene_count, creative_config)
    if not hard_short_clip_target:
        return [], {"applied": False, "reason": "route_budget_not_hard"}

    seeded_rows: list[dict[str, Any]] = []
    for source_row in scene_segment_rows:
        segment_id = str(source_row.get("segment_id") or "").strip()
        route, story_beat_type, route_reason = _source_row_route_seed(source_row, target_budget=route_budget_target)
        seeded_rows.append(
            {
                "segment_id": segment_id,
                "route": route,
                "route_reason": route_reason,
                "route_selection_reason": route_reason,
                "scene_goal": str(source_row.get("emotional_key") or "").strip(),
                "narrative_function": str(source_row.get("beat_purpose") or source_row.get("arc_role") or "").strip(),
                "story_beat_type": story_beat_type,
                "photo_staging_goal": "",
                "ltx_video_goal": "",
                "background_story_evidence": "",
                "foreground_performance_rule": "face_readability_for_vocal_window" if route == "ia2v" else "",
                "object_action_allowed": False if route == "ia2v" else True,
                "singing_readiness_required": bool(route == "ia2v"),
                "ia2v_photo_readability_notes": "",
                "visual_motion": {
                    "subject_motion": "",
                    "camera_intent": "",
                    "pacing": "stable",
                    "energy_alignment": "match",
                },
                "composition": {
                    "framing": "medium",
                    "subject_priority": "hero" if route == "ia2v" else "environment",
                    "layout": "centered",
                    "depth_strategy": "layered",
                },
                "audio_visual_sync": "",
                "speaker_role": "character_1" if route == "ia2v" else "",
                "vocal_owner_role": "character_1" if route == "ia2v" else UNKNOWN_VOCAL_OWNER_ROLE,
                "spoken_line": str(source_row.get("transcript_slice") or "").strip() if route == "ia2v" else "",
                "lip_sync_allowed": bool(route == "ia2v"),
                "lip_sync_priority": "high" if route == "ia2v" else "none",
                "mouth_visible_required": bool(route == "ia2v"),
                "listener_reaction_allowed": True,
                "reaction_role": "",
                "speaker_confidence": 0.6 if route == "ia2v" else 0.0,
            }
        )

    repaired_rows, repair_details = _repair_scene_plan_routes_for_budget(
        storyboard_rows=seeded_rows,
        scene_segment_rows=scene_segment_rows,
        role_lookup=role_lookup,
        target_budget=route_budget_target,
        character_1_appearance_mode=normalize_character_appearance_mode(character_appearance_modes_by_role.get("character_1")),
    )
    if not bool(_safe_dict(repair_details).get("applied")):
        return [], {"applied": False, "reason": f'budget_repair_failed:{str(_safe_dict(repair_details).get("reason") or "unknown")}'}
    return repaired_rows, {
        "applied": True,
        "type": "grounded_zero_row_from_segments",
        "target_budget": route_budget_target,
        "repair_details": repair_details,
    }


def _repair_scene_plan_routes_for_budget(
    *,
    storyboard_rows: list[dict[str, Any]],
    scene_segment_rows: list[dict[str, Any]],
    role_lookup: dict[str, dict[str, Any]],
    target_budget: dict[str, int],
    character_1_appearance_mode: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not storyboard_rows:
        return storyboard_rows, {"applied": False, "reason": "empty_storyboard"}
    ordered_segment_ids = [str(_safe_dict(row).get("segment_id") or "").strip() for row in scene_segment_rows]
    ordered_segment_ids = [segment_id for segment_id in ordered_segment_ids if segment_id]
    if not ordered_segment_ids:
        return storyboard_rows, {"applied": False, "reason": "missing_segment_ids"}
    row_by_segment = {str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row) for row in storyboard_rows}
    source_by_segment = {str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row) for row in scene_segment_rows}
    if any(segment_id not in row_by_segment for segment_id in ordered_segment_ids):
        return storyboard_rows, {"applied": False, "reason": "segment_coverage_mismatch"}

    total = len(ordered_segment_ids)
    target_ia2v = max(0, int(_safe_dict(target_budget).get("ia2v") or 0))
    target_first_last = max(0, int(_safe_dict(target_budget).get("first_last") or 0))
    if target_ia2v + target_first_last > total:
        target_first_last = max(0, total - target_ia2v)
    target_i2v = max(0, total - target_ia2v - target_first_last)
    normalized_target = {"i2v": target_i2v, "ia2v": target_ia2v, "first_last": target_first_last}

    ranked: list[dict[str, Any]] = []
    for idx, segment_id in enumerate(ordered_segment_ids):
        row = row_by_segment.get(segment_id, {})
        source = source_by_segment.get(segment_id, {})
        perf_score, world_score, first_last_score = _segment_route_semantic_scores(source, row)
        ranked.append(
            {
                "segment_id": segment_id,
                "idx": idx,
                "current_route": str(row.get("route") or "").strip().lower(),
                "performance_score": perf_score,
                "world_score": world_score,
                "first_last_score": first_last_score,
            }
        )

    ia2v_ids = [
        item["segment_id"]
        for item in sorted(
            ranked,
            key=lambda item: (
                -(item["performance_score"] - item["world_score"]),
                -item["performance_score"],
                item["current_route"] != "ia2v",
                item["idx"],
            ),
        )[:target_ia2v]
    ]
    ia2v_set = set(ia2v_ids)
    remaining = [item for item in ranked if item["segment_id"] not in ia2v_set]
    first_last_ids = [
        item["segment_id"]
        for item in sorted(
            remaining,
            key=lambda item: (-item["first_last_score"], item["current_route"] != "first_last", item["idx"]),
        )[:target_first_last]
    ]
    first_last_set = set(first_last_ids)

    repaired_rows: list[dict[str, Any]] = []
    repaired_route_by_segment: dict[str, str] = {}
    for segment_id in ordered_segment_ids:
        row = _safe_dict(deepcopy(row_by_segment.get(segment_id)))
        role_row = _safe_dict(role_lookup.get(segment_id))
        active_roles = [
            str(v).strip()
            for v in (
                role_row.get("active_roles")
                or [
                    role_row.get("primary_role"),
                    *_safe_list(role_row.get("secondary_roles")),
                ]
            )
            if str(v).strip()
        ]
        route = "i2v"
        if segment_id in ia2v_set:
            route = "ia2v"
        elif segment_id in first_last_set:
            route = "first_last"
        repaired_route_by_segment[segment_id] = route
        row["route"] = route
        row["story_beat_type"] = _repair_story_beat_type(str(row.get("story_beat_type") or ""), route)
        row["route_selection_reason"] = f'{str(row.get("route_selection_reason") or row.get("route_reason") or "").strip()} | route_budget_repair'.strip(" |")
        row["route_reason"] = f'{str(row.get("route_reason") or "").strip()} | route_budget_repair'.strip(" |")
        composition = _safe_dict(row.get("composition"))
        if route == "ia2v":
            row["speaker_role"] = "character_1"
            row["vocal_owner_role"] = "character_1"
            row["lip_sync_allowed"] = True
            row["lip_sync_priority"] = "high"
            row["mouth_visible_required"] = True
            row["singing_readiness_required"] = True
            row["object_action_allowed"] = False
            row["visual_focus_role"] = "character_1"
            composition["subject_priority"] = "hero"
        else:
            row["speaker_role"] = ""
            row["vocal_owner_role"] = ""
            row["lip_sync_allowed"] = False
            row["lip_sync_priority"] = "none"
            row["mouth_visible_required"] = False
            row["singing_readiness_required"] = False
            if route == "i2v":
                row["object_action_allowed"] = bool(row.get("object_action_allowed", True))
                row["foreground_performance_rule"] = ""
                if character_1_appearance_mode == "lip_sync_only":
                    composition["subject_priority"] = "environment"
                    if str(row.get("visual_focus_role") or "").strip().lower() == "character_1":
                        row["visual_focus_role"] = _select_world_focus_role(active_roles)
        row["composition"] = composition
        repaired_rows.append(row)

    repaired_counts = {
        "i2v": sum(1 for route in repaired_route_by_segment.values() if route == "i2v"),
        "ia2v": sum(1 for route in repaired_route_by_segment.values() if route == "ia2v"),
        "first_last": sum(1 for route in repaired_route_by_segment.values() if route == "first_last"),
    }
    return repaired_rows, {
        "applied": True,
        "target_counts": normalized_target,
        "actual_counts": repaired_counts,
        "route_by_segment": repaired_route_by_segment,
    }


def _normalize_scene_plan(
    raw_plan: dict[str, Any],
    *,
    scene_segment_rows: list[dict[str, Any]],
    role_lookup: dict[str, dict[str, Any]],
    creative_config: dict[str, Any],
    force_route_mode: str = "",
    forced_routes: list[str] | None = None,
    structure: str = "",
    vocal_gender: str = UNKNOWN_VOCAL_GENDER,
    vocal_owner_role: str = UNKNOWN_VOCAL_OWNER_ROLE,
    include_debug_raw: bool = False,
    character_appearance_modes_by_role: dict[str, str] | None = None,
    empty_plan_fallback_allowed: bool = False,
    used_model: str = "",
    audio_map: dict[str, Any] | None = None,
    director_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], bool, str, int, dict[str, Any], str]:
    raw_storyboard_value = raw_plan.get("storyboard")
    storyboard_missing = "storyboard" not in raw_plan
    raw_storyboard = [_safe_dict(row) for row in _safe_list(raw_storyboard_value)]
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
    scene_plan_empty_detected = False
    scene_plan_empty_reason = ""
    scene_plan_fallback_applied = False
    scene_plan_fallback_type = ""
    scene_plan_fallback_row_count = 0

    if storyboard_missing:
        scene_plan_empty_detected = True
        scene_plan_empty_reason = "missing_storyboard"
    elif not raw_storyboard:
        scene_plan_empty_detected = True
        scene_plan_empty_reason = "empty_storyboard"

    if duplicate_ids and not scene_plan_empty_detected:
        validation_error = "duplicate_segment_id"
        error_code = "SCENES_SEGMENT_ID_MISMATCH"

    if not validation_error and not scene_plan_empty_detected and (len(raw_storyboard) != len(scene_segment_rows) or model_segment_ids != expected_segment_ids):
        validation_error = "segment_id_sequence_mismatch"
        error_code = "SCENES_SEGMENT_ID_MISMATCH"

    prompt_leaks_detected = 0
    technical_leaks_detected = 0
    scene_plan_technical_leak_field = ""
    scene_plan_technical_leak_token = ""
    scene_plan_technical_leak_excerpt = ""
    scene_plan_technical_leak_fields: set[str] = set()
    scene_plan_technical_leak_tokens: set[str] = set()
    scene_plan_technical_leak_cleaned_locally = False
    enum_invalid_count = 0
    enum_invalid_rows: list[dict[str, Any]] = []
    enum_invalid_field = ""
    enum_invalid_value = ""
    enum_invalid_allowed_values: list[str] = []
    enum_invalid_segment_id = ""
    enum_repair_applied = False
    enum_repair_rows: list[dict[str, Any]] = []
    enum_unrepaired_count = 0
    enum_unrepaired_rows: list[dict[str, Any]] = []
    enum_alias_normalized_count = 0
    enum_alias_normalized_rows: list[dict[str, Any]] = []
    illegal_route_count = 0
    cast_mutation_count = 0
    speaker_role_invalid_count = 0
    ia2v_route_requires_speaker_because_current_provider_uses_lipsync_workflow = False
    lip_sync_rejected_reasons: dict[str, list[str]] = {}
    lip_sync_voice_role_mismatch_segments: list[str] = []
    route_selection_reasons_by_segment: dict[str, str] = {}
    final_route_by_segment: dict[str, str] = {}
    primary_role_by_segment: dict[str, str] = {}
    visual_focus_role_by_segment: dict[str, str] = {}
    speaker_role_by_segment: dict[str, str] = {}
    reaction_role_by_segment: dict[str, str] = {}
    lip_sync_decision_by_segment: dict[str, str] = {}
    consecutive_lip_sync_count = 0
    max_consecutive_lip_sync_count = 0
    lip_sync_selected_count = 0
    max_consecutive_allowed = int(creative_config.get("max_consecutive_lipsync") or 2)
    hard_route_map = _safe_dict(creative_config.get("hard_route_assignments_by_segment"))
    if not hard_route_map:
        hard_route_map = _safe_dict(creative_config.get("route_assignments_by_segment"))
    appearance_modes = _safe_dict(character_appearance_modes_by_role)
    scene_character_visibility_policy: list[dict[str, Any]] = []
    mapped_route_budget_lock_detected = False
    mapped_route_budget_override_prevented = False
    mapped_route_budget_post_normalization_applied = False
    mapped_route_budget_post_normalization_diag: dict[str, Any] = {}
    mapped_route_budget_target_adjusted_for_mapped_no_first_last = False
    mapped_default_vocal_role = "character_1"

    forced_route_list = [
        str(item).strip().lower()
        for item in _safe_list(forced_routes)
        if str(item).strip().lower() in ALLOWED_ROUTES
    ]

    for idx, source_row in enumerate(scene_segment_rows):
        segment_id = str(source_row.get("segment_id") or "").strip()
        raw_row = _safe_dict(storyboard_by_id.get(segment_id))
        if not raw_row:
            if not scene_plan_empty_detected:
                scene_plan_empty_detected = True
                scene_plan_empty_reason = scene_plan_empty_reason or "zero_normalized_rows"
            validation_error = validation_error or "missing_storyboard_row"
            error_code = error_code or "SCENES_SEGMENT_ID_MISMATCH"
            continue

        route = str(raw_row.get("route") or "").strip().lower()
        hard_route = str(hard_route_map.get(segment_id) or "").strip().lower()
        row_route_assignment_source = str(raw_row.get("route_assignment_source") or "").strip().lower()
        row_route_lock_source = str(raw_row.get("route_lock_source") or "").strip().lower()
        row_is_mapped_route_budget = (
            row_route_assignment_source == "mapped_route_budget"
            or row_route_lock_source == "mapped_route_budget"
            or bool(raw_row.get("routeLocked"))
        )
        if row_is_mapped_route_budget:
            mapped_route_budget_lock_detected = True
        if hard_route in ALLOWED_ROUTES and not row_is_mapped_route_budget:
            route = hard_route
        elif hard_route in ALLOWED_ROUTES and row_is_mapped_route_budget:
            mapped_route_budget_override_prevented = True
        if str(structure or "").strip().lower() == "performance_cut":
            has_vocal = bool(source_row.get("has_vocal"))
            if not has_vocal:
                has_vocal = bool(source_row.get("is_lip_sync_candidate")) or bool(str(source_row.get("transcript_slice") or "").strip())
            perf_route = "ia2v" if has_vocal else "i2v"
            route = perf_route
            hard_route = perf_route
        elif str(force_route_mode or "").strip().lower() == "strict_ai" and forced_route_list:
            ai_route = forced_route_list[idx % len(forced_route_list)]
            route = ai_route
            hard_route = ai_route
        if route not in ALLOWED_ROUTES:
            illegal_route_count += 1
            validation_error = validation_error or "illegal_route"
            error_code = error_code or "SCENES_ILLEGAL_ROUTE"

        visual_motion = _safe_dict(raw_row.get("visual_motion"))
        composition = _safe_dict(raw_row.get("composition"))

        raw_story_beat_type = str(raw_row.get("story_beat_type") or "").strip().lower()
        pacing = str(visual_motion.get("pacing") or "").strip().lower()
        energy_alignment = str(visual_motion.get("energy_alignment") or "").strip().lower()
        framing = str(composition.get("framing") or "").strip().lower()
        subject_priority = str(composition.get("subject_priority") or "").strip().lower()
        layout = str(composition.get("layout") or "").strip().lower()
        depth_strategy = str(composition.get("depth_strategy") or "").strip().lower()
        for field_name, current_value in (
            ("composition.framing", framing),
            ("composition.layout", layout),
            ("composition.depth_strategy", depth_strategy),
        ):
            normalized_value, changed = _normalize_scene_plan_composition_enum_alias(field_name, current_value)
            if changed:
                enum_alias_normalized_count += 1
                enum_alias_normalized_rows.append(
                    {
                        "segment_id": segment_id,
                        "field": field_name,
                        "value": current_value,
                        "normalized_to": normalized_value,
                    }
                )
            if field_name == "composition.framing":
                framing = normalized_value
            elif field_name == "composition.layout":
                layout = normalized_value
            elif field_name == "composition.depth_strategy":
                depth_strategy = normalized_value

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
        if has_prompt_leak:
            prompt_leaks_detected += 1
            validation_error = validation_error or "prompt_leaking"
            error_code = error_code or "SCENES_PROMPT_LEAKING"

        technical_leaks = _collect_free_text_technical_leaks(raw_row)
        if technical_leaks:
            technical_leaks_detected += len(technical_leaks)
            scene_plan_technical_leak_fields.update(
                str(item.get("field") or "") for item in technical_leaks if str(item.get("field") or "")
            )
            scene_plan_technical_leak_tokens.update(
                str(item.get("token") or "") for item in technical_leaks if str(item.get("token") or "")
            )
            sanitized_row, cleaned_locally = _sanitize_free_text_technical_leaks(raw_row)
            raw_row = sanitized_row if cleaned_locally else raw_row
            visual_motion = _safe_dict(raw_row.get("visual_motion"))
            technical_leaks_after_cleanup = _collect_free_text_technical_leaks(raw_row)
            if technical_leaks_after_cleanup:
                validation_error = validation_error or "technical_leaking"
                error_code = error_code or "SCENES_TECHNICAL_LEAKING"
                first_leak = technical_leaks_after_cleanup[0]
                scene_plan_technical_leak_field = first_leak.get("field") or ""
                scene_plan_technical_leak_token = first_leak.get("token") or ""
                scene_plan_technical_leak_excerpt = first_leak.get("excerpt") or ""
            elif cleaned_locally:
                scene_plan_technical_leak_cleaned_locally = True

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

        role_row = _safe_dict(role_lookup.get(segment_id))
        active_roles = [
            str(v).strip()
            for v in (
                role_row.get("active_roles")
                or [
                    role_row.get("primary_role"),
                    *_safe_list(role_row.get("secondary_roles")),
                ]
            )
            if str(v).strip()
        ]
        role_primary = str(role_row.get("primary_role") or "").strip()
        primary_role = role_primary if role_primary in active_roles else (active_roles[0] if active_roles else "")
        world_led_beat = _is_world_beat(source_row)
        if world_led_beat and primary_role == "character_1" and route != "ia2v":
            primary_role = ""
        visual_focus_role = primary_role
        character_1_appearance_mode = normalize_character_appearance_mode(appearance_modes.get("character_1"))
        character_1_presence_mode = character_presence_mode_from_appearance_mode(character_1_appearance_mode)
        speaker_role = str(raw_row.get("speaker_role") or "").strip()
        spoken_line = str(raw_row.get("spoken_line") or "").strip()
        lip_sync_allowed = bool(raw_row.get("lip_sync_allowed"))
        lip_sync_priority = str(raw_row.get("lip_sync_priority") or "none").strip().lower() or "none"
        mouth_visible_required = bool(raw_row.get("mouth_visible_required"))
        listener_reaction_allowed = bool(raw_row.get("listener_reaction_allowed"))
        reaction_role = str(raw_row.get("reaction_role") or "").strip()
        speaker_confidence = _clamp_ratio(raw_row.get("speaker_confidence"), 0.0)
        is_lip_sync_candidate = bool(source_row.get("is_lip_sync_candidate"))
        transcript_slice = str(source_row.get("transcript_slice") or "").strip()
        row_vocal_owner_role = str(raw_row.get("vocal_owner_role") or vocal_owner_role or UNKNOWN_VOCAL_OWNER_ROLE).strip() or UNKNOWN_VOCAL_OWNER_ROLE

        ia2v_evidence_reject_reasons: list[str] = []
        forced_ia2v_invalid_downgraded = False
        if route == "ia2v":
            can_enforce_ia2v, ia2v_evidence_reject_reasons = _can_enforce_ia2v_row(
                source_row=source_row,
                spoken_line=spoken_line,
                transcript_slice=transcript_slice,
                is_lip_sync_candidate=is_lip_sync_candidate,
                active_roles=active_roles,
            )
            if can_enforce_ia2v:
                speaker_role = "character_1"
                row_vocal_owner_role = "character_1"
                lip_sync_allowed = True
                lip_sync_priority = "high"
                mouth_visible_required = True
                if transcript_slice and not spoken_line:
                    spoken_line = transcript_slice
            else:
                if hard_route == "ia2v":
                    forced_ia2v_invalid_downgraded = True
                    ia2v_evidence_reject_reasons.append("forced_ia2v_invalid_downgraded_to_i2v")
                route = "i2v"
                speaker_role = ""
                row_vocal_owner_role = ""
                lip_sync_allowed = False
                lip_sync_priority = "none"
                mouth_visible_required = False
        else:
            speaker_role = ""
            row_vocal_owner_role = ""
            lip_sync_allowed = False
            lip_sync_priority = "none"
            mouth_visible_required = False
        if character_1_appearance_mode == "offscreen_voice":
            if route == "ia2v":
                route = "i2v"
            speaker_role = ""
            row_vocal_owner_role = ""
            lip_sync_allowed = False
            lip_sync_priority = "none"
            mouth_visible_required = False
        elif character_1_appearance_mode == "lip_sync_only" and route == "i2v":
            visual_focus_role = _select_world_focus_role(active_roles)
        elif character_1_appearance_mode == "background_only" and route == "i2v" and primary_role == "character_1":
            visual_focus_role = _select_world_focus_role(active_roles) or visual_focus_role

        enum_checks: list[tuple[str, str, set[str], str]] = [
            ("story_beat_type", raw_story_beat_type, ALLOWED_STORY_BEAT_TYPES, _repair_story_beat_type(raw_story_beat_type, route)),
            ("visual_motion.pacing", pacing, ALLOWED_PACING, "stable"),
            ("composition.framing", framing, ALLOWED_FRAMING, "medium"),
            (
                "composition.subject_priority",
                subject_priority,
                ALLOWED_SUBJECT_PRIORITY,
                "hero" if route == "ia2v" else "environment",
            ),
            ("visual_motion.energy_alignment", energy_alignment, ALLOWED_ENERGY_ALIGNMENT, "match"),
            ("composition.layout", layout, ALLOWED_LAYOUT, "centered"),
            ("composition.depth_strategy", depth_strategy, ALLOWED_DEPTH_STRATEGY, "layered"),
            ("lip_sync_priority", lip_sync_priority, ALLOWED_LIP_SYNC_PRIORITY, "high" if route == "ia2v" else "none"),
        ]
        repaired_values: dict[str, str] = {}
        for field_name, field_value, allowed_values, fallback_value in enum_checks:
            if field_value in allowed_values:
                repaired_values[field_name] = field_value
                continue
            enum_invalid_count += 1
            invalid_row = {
                "segment_id": segment_id,
                "field": field_name,
                "value": field_value,
                "allowed_values": sorted(allowed_values),
            }
            enum_invalid_rows.append(invalid_row)
            if not enum_invalid_field:
                enum_invalid_field = field_name
                enum_invalid_value = field_value
                enum_invalid_allowed_values = sorted(allowed_values)
                enum_invalid_segment_id = segment_id
            if fallback_value in allowed_values:
                repaired_values[field_name] = fallback_value
                enum_repair_applied = True
                enum_repair_rows.append(
                    {
                        "segment_id": segment_id,
                        "field": field_name,
                        "value": field_value,
                        "repaired_to": fallback_value,
                        "allowed_values": sorted(allowed_values),
                    }
                )
            else:
                enum_unrepaired_count += 1
                enum_unrepaired_rows.append(invalid_row)
                repaired_values[field_name] = field_value
        raw_story_beat_type = repaired_values.get("story_beat_type", raw_story_beat_type)
        pacing = repaired_values.get("visual_motion.pacing", pacing)
        framing = repaired_values.get("composition.framing", framing)
        subject_priority = repaired_values.get("composition.subject_priority", subject_priority)
        energy_alignment = repaired_values.get("visual_motion.energy_alignment", energy_alignment)
        layout = repaired_values.get("composition.layout", layout)
        depth_strategy = repaired_values.get("composition.depth_strategy", depth_strategy)
        lip_sync_priority = repaired_values.get("lip_sync_priority", lip_sync_priority)
        if route == "i2v":
            if character_1_appearance_mode == "lip_sync_only":
                subject_priority = "environment"
            elif subject_priority == "hero" and _is_world_beat(source_row):
                subject_priority = "environment"

        row_rejected_reasons: list[str] = list(ia2v_evidence_reject_reasons)
        if speaker_role and speaker_role not in active_roles:
            speaker_role_invalid_count += 1
            row_rejected_reasons.append("speaker_role_not_in_present_cast")
        if lip_sync_priority not in ALLOWED_LIP_SYNC_PRIORITY:
            row_rejected_reasons.append("lip_sync_priority_invalid")
        if lip_sync_allowed and not speaker_role:
            speaker_role_invalid_count += 1
            row_rejected_reasons.append("lip_sync_with_unknown_speaker")
        if lip_sync_allowed and not (spoken_line or transcript_slice):
            row_rejected_reasons.append("lip_sync_without_spoken_line")
        if route == "ia2v" and lip_sync_allowed and not speaker_role:
            speaker_role_invalid_count += 1
            row_rejected_reasons.append("route_ia2v_lipsync_requires_speaker_role")
            row_rejected_reasons.append("ia2v_route_requires_speaker_because_current_provider_uses_lipsync_workflow")
            ia2v_route_requires_speaker_because_current_provider_uses_lipsync_workflow = True
        if route == "first_last" and lip_sync_allowed:
            row_rejected_reasons.append("first_last_disallows_lip_sync")
        if mouth_visible_required and speaker_role not in active_roles:
            row_rejected_reasons.append("mouth_visible_requires_visible_speaker")
        if lip_sync_allowed and not is_lip_sync_candidate:
            row_rejected_reasons.append("audio_map_permission_missing")
        if lip_sync_allowed and float(source_row.get("duration_sec") or 0.0) < 2.8:
            row_rejected_reasons.append("duration_too_short_for_lipsync")
        if lip_sync_allowed and float(source_row.get("duration_sec") or 0.0) > 7.0:
            row_rejected_reasons.append("duration_too_long_for_lipsync")
        if lip_sync_allowed and not mouth_visible_required:
            row_rejected_reasons.append("mouth_visible_required_for_lipsync")
        if lip_sync_allowed and (row_vocal_owner_role == UNKNOWN_VOCAL_OWNER_ROLE or speaker_role != row_vocal_owner_role):
            row_rejected_reasons.append("SCENE_LIPSYNC_VOICE_ROLE_MISMATCH")
            lip_sync_voice_role_mismatch_segments.append(segment_id)
        if reaction_role and reaction_role not in active_roles:
            row_rejected_reasons.append("reaction_role_not_in_present_cast")
        if route == "ia2v" and listener_reaction_allowed and reaction_role == speaker_role and reaction_role:
            row_rejected_reasons.append("reaction_role_equals_speaker_role")

        if route == "ia2v":
            if speaker_role in active_roles and speaker_role:
                visual_focus_role = speaker_role
            elif primary_role:
                visual_focus_role = primary_role
        elif route == "first_last":
            lip_sync_allowed = False
            if primary_role:
                visual_focus_role = primary_role
        elif route == "i2v":
            world_focus = _select_world_focus_role(active_roles)
            if world_focus:
                visual_focus_role = world_focus
            elif primary_role and primary_role != "character_1":
                visual_focus_role = primary_role
            elif _is_world_beat(source_row):
                visual_focus_role = ""
            elif primary_role:
                visual_focus_role = primary_role
        elif primary_role:
            visual_focus_role = primary_role

        if route == "i2v":
            if character_1_appearance_mode == "lip_sync_only" and visual_focus_role == "character_1":
                visual_focus_role = _select_world_focus_role(active_roles)
            if visual_focus_role == "character_1":
                visual_focus_role = ""
            reaction_role = ""

        if row_rejected_reasons:
            if route == "ia2v":
                route = "i2v"
                speaker_role = ""
                row_vocal_owner_role = ""
                lip_sync_allowed = False
                lip_sync_priority = "none"
                mouth_visible_required = False
            else:
                lip_sync_allowed = False
                lip_sync_priority = "none"
                mouth_visible_required = False
                speaker_role = ""
                row_vocal_owner_role = ""
            if not speaker_role:
                speaker_confidence = 0.0
            if route == "ia2v" and speaker_role != "character_1":
                validation_error = validation_error or "speaker_role_invalid"
                error_code = error_code or "SCENE_SPEAKER_ROLE_INVALID"

        if not reaction_role:
            reaction_role = ""
        elif reaction_role not in active_roles:
            reaction_role = ""

        if route == "ia2v":
            speaker_role = "character_1"
            row_vocal_owner_role = "character_1"
            lip_sync_allowed = True
            lip_sync_priority = "high"
            mouth_visible_required = True
            if transcript_slice and not spoken_line:
                spoken_line = transcript_slice
        else:
            speaker_role = ""
            row_vocal_owner_role = ""
            lip_sync_allowed = False
            lip_sync_priority = "none"
            mouth_visible_required = False

        if lip_sync_allowed:
            consecutive_lip_sync_count += 1
            max_consecutive_lip_sync_count = max(max_consecutive_lip_sync_count, consecutive_lip_sync_count)
            lip_sync_selected_count += 1
            lip_sync_decision_by_segment[segment_id] = "selected"
        else:
            consecutive_lip_sync_count = 0
            lip_sync_decision_by_segment[segment_id] = "rejected" if row_rejected_reasons else "not_selected"

        if row_rejected_reasons:
            lip_sync_rejected_reasons[segment_id] = row_rejected_reasons
        primary_role_by_segment[segment_id] = primary_role
        visual_focus_role_by_segment[segment_id] = visual_focus_role
        speaker_role_by_segment[segment_id] = speaker_role
        reaction_role_by_segment[segment_id] = reaction_role
        character_1_visual_policy = (
            "offscreen_voiceover"
            if character_1_presence_mode == "offscreen_voice"
            else "vocal_anchor_background_optional"
            if character_1_presence_mode == "vocal_anchor" and route == "i2v"
            else "background_or_silhouette"
            if character_1_presence_mode == "background_presence"
            else "adaptive_presence"
        )
        scene_character_visibility_policy.append(
            {
                "segment_id": segment_id,
                "route": route,
                "primary_role": primary_role,
                "character_1_appearanceMode": character_1_appearance_mode,
                "character_1_presence_mode": character_1_presence_mode,
                "character_1_visual_policy": character_1_visual_policy,
                "characterRefAttachedAllowed": not (route == "i2v" and character_1_presence_mode == "offscreen_voice"),
            }
        )

        normalized_storyboard.append(
            {
                "segment_id": segment_id,
                "primary_role": primary_role,
                "visual_focus_role": visual_focus_role,
                "route": route,
                "route_reason": (
                    f'{str(raw_row.get("route_reason") or "").strip()} | forced_ia2v_invalid_downgraded_to_i2v'.strip(" |")
                    if forced_ia2v_invalid_downgraded
                    else str(raw_row.get("route_reason") or "").strip()
                ),
                "route_selection_reason": (
                    f'{str(raw_row.get("route_selection_reason") or raw_row.get("route_reason") or "").strip()} | forced_ia2v_invalid_downgraded_to_i2v'.strip(" |")
                    if forced_ia2v_invalid_downgraded
                    else str(raw_row.get("route_selection_reason") or raw_row.get("route_reason") or "").strip()
                ),
                "scene_goal": str(raw_row.get("scene_goal") or "").strip(),
                "narrative_function": str(raw_row.get("narrative_function") or "").strip(),
                "story_beat_type": str(
                    raw_story_beat_type
                    or _default_story_beat_type_for_route(route)
                ).strip(),
                "photo_staging_goal": str(raw_row.get("photo_staging_goal") or "").strip(),
                "ltx_video_goal": str(raw_row.get("ltx_video_goal") or "").strip(),
                "background_story_evidence": str(raw_row.get("background_story_evidence") or "").strip(),
                "foreground_performance_rule": str(raw_row.get("foreground_performance_rule") or "").strip() if route == "ia2v" else "",
                "object_action_allowed": False if route == "ia2v" else bool(raw_row.get("object_action_allowed", True)),
                "singing_readiness_required": True if route == "ia2v" else False,
                "ia2v_photo_readability_notes": str(raw_row.get("ia2v_photo_readability_notes") or "").strip(),
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
                "speaker_role": speaker_role,
                "vocal_owner_role": row_vocal_owner_role,
                "spoken_line": spoken_line,
                "lip_sync_allowed": lip_sync_allowed,
                "lip_sync_priority": lip_sync_priority,
                "mouth_visible_required": mouth_visible_required,
                "listener_reaction_allowed": listener_reaction_allowed,
                "reaction_role": reaction_role,
                "speaker_confidence": round(float(speaker_confidence), 3),
                "routeLocked": bool(raw_row.get("routeLocked")) or row_is_mapped_route_budget,
                "route_lock_source": (
                    "mapped_route_budget"
                    if row_is_mapped_route_budget
                    else str(raw_row.get("route_lock_source") or "").strip()
                ),
                "route_assignment_source": (
                    "mapped_route_budget"
                    if row_is_mapped_route_budget
                    else str(raw_row.get("route_assignment_source") or "").strip()
                ),
            }
        )
        route_selection_reasons_by_segment[segment_id] = (
            f'{str(raw_row.get("route_selection_reason") or raw_row.get("route_reason") or "").strip()} | forced_ia2v_invalid_downgraded_to_i2v'.strip(" |")
            if forced_ia2v_invalid_downgraded
            else str(raw_row.get("route_selection_reason") or raw_row.get("route_reason") or "").strip()
        )
        final_route_by_segment[segment_id] = route

    if max_consecutive_lip_sync_count > max_consecutive_allowed:
        validation_error = validation_error or "speaker_role_invalid"
        error_code = error_code or "SCENE_SPEAKER_ROLE_INVALID"

    if scene_plan_empty_detected and empty_plan_fallback_allowed:
        fallback_rows, fallback_details = _build_grounded_empty_scene_plan_fallback(
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            creative_config=creative_config,
            character_appearance_modes_by_role=appearance_modes,
        )
        if fallback_rows:
            normalized_storyboard = fallback_rows
            used_fallback = True
            scene_plan_fallback_applied = True
            scene_plan_fallback_type = str(_safe_dict(fallback_details).get("type") or "grounded_zero_row_from_segments")
            scene_plan_fallback_row_count = len(fallback_rows)
            validation_error = ""
            error_code = ""
            scene_plan_empty_reason = "fallback_applied"
    if scene_plan_empty_detected and not scene_plan_fallback_applied:
        validation_error = "empty_scene_plan"
        error_code = "SCENES_EMPTY_PLAN"

    route_mix_mode = str(creative_config.get("route_mix_mode") or "auto").strip().lower() or "auto"
    total_segments = len(scene_segment_rows)
    if route_mix_mode == "auto" and total_segments == 8 and lip_sync_selected_count > 3:
        validation_error = validation_error or "speaker_role_invalid"
        error_code = error_code or "SCENE_SPEAKER_ROLE_INVALID"

    expected_scene_count = len(scene_segment_rows)
    scene_candidate_windows_present = bool(_safe_list(_safe_dict(audio_map).get("scene_candidate_windows")))
    mapped_path = bool(
        str(used_model or "").strip().lower() == "mapped_from_audio_map.scene_candidate_windows"
        or mapped_route_budget_post_normalization_applied
        or mapped_route_budget_lock_detected
    )
    if mapped_path and normalized_storyboard:
        normalized_storyboard, mapped_route_budget_post_normalization_diag = _apply_route_budget_to_scene_rows(
            normalized_storyboard,
            _safe_dict(audio_map),
            creative_config,
            _safe_dict(director_config),
        )
        mapped_route_budget_post_normalization_applied = True
        mapped_route_budget_lock_detected = True
        mapped_path = True
        for row in normalized_storyboard:
            role = str(_safe_dict(row).get("primary_role") or "").strip()
            if role:
                mapped_default_vocal_role = role
                break
    route_budget_target, hard_short_clip_target = _route_budget_target_for_plan(expected_scene_count, creative_config)
    if mapped_path:
        mapped_ia2v_count = int(_safe_dict(mapped_route_budget_post_normalization_diag.get("scene_plan_route_budget_target_counts")).get("ia2v") or 0)
        route_budget_target = {
            "i2v": max(0, expected_scene_count - mapped_ia2v_count),
            "ia2v": max(0, mapped_ia2v_count),
            "first_last": 0,
        }
        hard_short_clip_target = True
        mapped_route_budget_target_adjusted_for_mapped_no_first_last = True
    route_budget_original_targets = {
        "i2v": max(0, int(_safe_dict(creative_config.get("route_targets_per_block")).get("i2v") or 0)),
        "ia2v": max(0, int(_safe_dict(creative_config.get("route_targets_per_block")).get("ia2v") or 0)),
        "first_last": max(0, int(_safe_dict(creative_config.get("route_targets_per_block")).get("first_last") or 0)),
    }
    route_budget_preset = str(creative_config.get("route_strategy_preset") or "").strip().lower()
    route_budget_resolved_from = "audio_map_segments_count" if route_budget_preset == "no_first_last_50_50_0" else "creative_config"
    pre_repair_route_by_segment = {
        str(row.get("segment_id") or "").strip(): str(row.get("route") or "").strip().lower()
        for row in normalized_storyboard
        if str(row.get("segment_id") or "").strip()
    }
    pre_repair_counts = {
        route_name: sum(1 for route_value in pre_repair_route_by_segment.values() if route_value == route_name)
        for route_name in ("i2v", "ia2v", "first_last")
    }
    route_budget_repair_applied = False
    route_budget_repair_details: dict[str, Any] = {}
    if (
        hard_short_clip_target
        and len(normalized_storyboard) == expected_scene_count
        and pre_repair_counts != route_budget_target
        and not (scene_plan_empty_detected and not scene_plan_fallback_applied)
    ):
        repaired_storyboard, repair_details = _repair_scene_plan_routes_for_budget(
            storyboard_rows=normalized_storyboard,
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            target_budget=route_budget_target,
            character_1_appearance_mode=normalize_character_appearance_mode(appearance_modes.get("character_1")),
        )
        if bool(_safe_dict(repair_details).get("applied")):
            normalized_storyboard = repaired_storyboard
            route_budget_repair_applied = True
            route_budget_repair_details = repair_details

    pre_final_rebalance_route_counts = {
        route_name: sum(1 for row in normalized_storyboard if str(row.get("route") or "").strip().lower() == route_name)
        for route_name in ("i2v", "ia2v", "first_last")
    }
    final_semantic_rebalance_applied = False
    final_semantic_rebalance_details: dict[str, Any] = {}
    post_validity_route_rebalance: dict[str, Any] = {}
    mapped_generic_rebalance_skipped = False
    if (
        hard_short_clip_target
        and len(normalized_storyboard) == expected_scene_count
        and pre_final_rebalance_route_counts != route_budget_target
        and not (scene_plan_empty_detected and not scene_plan_fallback_applied)
    ):
        if mapped_path:
            mapped_generic_rebalance_skipped = True
            post_validity_route_rebalance = {
                "attempted": False,
                "skipped": True,
                "reason": "mapped_route_budget_already_applied",
            }
        else:
            rebalanced_storyboard, rebalance_details = _final_semantic_route_rebalance(
                normalized_storyboard=normalized_storyboard,
                scene_segment_rows=scene_segment_rows,
                role_lookup=role_lookup,
                route_budget_target=route_budget_target,
                character_1_appearance_mode=normalize_character_appearance_mode(appearance_modes.get("character_1")),
            )
            final_semantic_rebalance_details = _safe_dict(rebalance_details)
            if bool(_safe_dict(rebalance_details).get("applied")):
                normalized_storyboard = rebalanced_storyboard
                final_semantic_rebalance_applied = True
            post_validity_route_rebalance = {
                "attempted": True,
                "skipped": False,
                **final_semantic_rebalance_details,
            }
    if not post_validity_route_rebalance:
        post_validity_route_rebalance = {
            "attempted": False,
            "skipped": bool(mapped_path),
            "reason": "mapped_route_budget_already_applied" if mapped_path else "not_required",
        }

    validated_route_by_segment = {
        str(row.get("segment_id") or "").strip(): str(row.get("route") or "").strip().lower()
        for row in normalized_storyboard
        if str(row.get("segment_id") or "").strip() and str(row.get("route") or "").strip().lower() in ALLOWED_ROUTES
    }
    final_route_by_segment = dict(validated_route_by_segment)
    final_route_counts = {
        route_name: sum(1 for route_value in final_route_by_segment.values() if route_value == route_name)
        for route_name in ("i2v", "ia2v", "first_last")
    }
    if mapped_path:
        mapped_diag = _safe_dict(mapped_route_budget_post_normalization_diag)
        mapped_target_counts = _safe_dict(mapped_diag.get("scene_plan_route_budget_target_counts"))
        mapped_ia2v_target = int(mapped_target_counts.get("ia2v") or 0)
        if mapped_ia2v_target <= 0:
            mapped_ia2v_target = final_route_counts.get("ia2v", 0)
        route_budget_target = {
            "i2v": max(0, expected_scene_count - mapped_ia2v_target),
            "ia2v": max(0, mapped_ia2v_target),
            "first_last": 0,
        }
        hard_short_clip_target = True
        mapped_route_budget_target_adjusted_for_mapped_no_first_last = True
    if mapped_route_budget_lock_detected:
        requested_route_locks_by_segment = dict(final_route_by_segment)
    else:
        requested_route_locks_by_segment = {
            str(seg).strip(): str(route).strip().lower()
            for seg, route in hard_route_map.items()
            if str(seg).strip() and str(route).strip().lower() in ALLOWED_ROUTES
        }
    director_cfg = _safe_dict(director_config)
    if director_cfg.get("ia2v_ratio") is not None:
        route_budget_mode = "director_config_hard"
    elif _route_strategy_active(creative_config) and not bool(creative_config.get("targets_are_soft")):
        route_budget_mode = "creative_config_hard"
    else:
        route_budget_mode = "creative_config_soft"
    route_budget_tolerance = 1
    target_ia2v = int(_safe_dict(route_budget_target).get("ia2v") or 0)
    ia2v_delta = abs(int(final_route_counts.get("ia2v") or 0) - target_ia2v)
    if route_budget_mode == "director_config_hard":
        route_budget_mismatch = bool(ia2v_delta > route_budget_tolerance)
    elif route_budget_mode == "creative_config_hard":
        route_budget_mismatch = bool(hard_short_clip_target and final_route_counts != route_budget_target)
    else:
        route_budget_mismatch = False
    if int(final_route_counts.get("first_last") or 0) > 0:
        route_budget_mismatch = True
    validation_errors: list[str] = []
    error_codes: list[str] = []
    if enum_unrepaired_count > 0:
        validation_errors.append("enum_invalid")
        error_codes.append("SCENES_ENUM_INVALID")
        validation_error = validation_error or "enum_invalid"
        error_code = error_code or "SCENES_ENUM_INVALID"
    if route_budget_mismatch and not (scene_plan_empty_detected and not scene_plan_fallback_applied):
        validation_errors.append("route_budget_mismatch")
        error_codes.append("SCENES_ROUTE_BUDGET_MISMATCH")
        validation_error = "route_budget_mismatch"
        error_code = "SCENES_ROUTE_BUDGET_MISMATCH"

    legacy_scenes: list[dict[str, Any]] = []
    for row, source_row in zip(normalized_storyboard, scene_segment_rows, strict=False):
        segment_id = str(row.get("segment_id") or "").strip()
        validated_route = str(final_route_by_segment.get(segment_id) or row.get("route") or "").strip().lower()
        motion = _safe_dict(row.get("visual_motion"))
        legacy_scenes.append(
            {
                "scene_id": str(row.get("segment_id") or ""),
                "segment_id": str(row.get("segment_id") or ""),
                "t0": _round3(source_row.get("t0")),
                "t1": _round3(source_row.get("t1")),
                "duration_sec": _round3(source_row.get("duration_sec")),
                "route": validated_route,
                "route_reason": str(row.get("route_reason") or ""),
                "route_selection_reason": str(row.get("route_selection_reason") or row.get("route_reason") or ""),
                "scene_function": str(row.get("narrative_function") or ""),
                "emotional_intent": str(row.get("scene_goal") or ""),
                "motion_intent": str(motion.get("subject_motion") or ""),
                "primary_role": str(row.get("primary_role") or ""),
                "visual_focus_role": str(row.get("visual_focus_role") or ""),
                "speaker_role": str(row.get("speaker_role") or ""),
                "vocal_owner_role": str(row.get("vocal_owner_role") or ""),
                "spoken_line": str(row.get("spoken_line") or ""),
                "lip_sync_allowed": bool(row.get("lip_sync_allowed")),
                "lip_sync_priority": str(row.get("lip_sync_priority") or "none"),
                "mouth_visible_required": bool(row.get("mouth_visible_required")),
                "listener_reaction_allowed": bool(row.get("listener_reaction_allowed")),
                "reaction_role": str(row.get("reaction_role") or ""),
                "speaker_confidence": _clamp_ratio(row.get("speaker_confidence"), 0.0),
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
            "i2v": final_route_counts["i2v"],
            "ia2v": final_route_counts["ia2v"],
            "first_last": final_route_counts["first_last"],
        },
        "route_locks_by_segment": final_route_by_segment,
        "scenes": legacy_scenes,
        "scene_arc_summary": "",
        "route_strategy_notes": ["scene_candidate_windows and compiled_contract are legacy bridge inputs"],
        "deprecated_bridge": True,
    }

    has_adjacent_ia2v = _has_adjacent_route(legacy_scenes, "ia2v")
    has_adjacent_first_last = _has_adjacent_route(legacy_scenes, "first_last")
    adjacent_first_last_pairs = _adjacent_route_pairs(legacy_scenes, "first_last")

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
        "scene_plan_technical_leaks_detected": technical_leaks_detected,
        "scene_plan_technical_leak_field": scene_plan_technical_leak_field,
        "scene_plan_technical_leak_token": scene_plan_technical_leak_token,
        "scene_plan_technical_leak_excerpt": scene_plan_technical_leak_excerpt,
        "scene_plan_technical_leak_fields": sorted(scene_plan_technical_leak_fields),
        "scene_plan_technical_leak_tokens": sorted(scene_plan_technical_leak_tokens),
        "scene_plan_technical_leak_cleaned_locally": scene_plan_technical_leak_cleaned_locally,
        "enum_invalid_count": enum_invalid_count,
        "enum_unrepaired_count": enum_unrepaired_count,
        "scene_plan_validation_errors": validation_errors,
        "scene_plan_error_codes": error_codes,
        "scene_plan_enum_invalid_detected": bool(enum_invalid_count),
        "scene_plan_enum_invalid_count": enum_invalid_count,
        "scene_plan_enum_invalid_field": enum_invalid_field,
        "scene_plan_enum_invalid_value": enum_invalid_value,
        "scene_plan_enum_invalid_allowed_values": enum_invalid_allowed_values,
        "scene_plan_enum_invalid_segment_id": enum_invalid_segment_id,
        "scene_plan_enum_invalid_rows": enum_invalid_rows,
        "scene_plan_enum_repair_applied": enum_repair_applied,
        "scene_plan_enum_repair_count": len(enum_repair_rows),
        "scene_plan_enum_repair_rows": enum_repair_rows,
        "scene_plan_enum_unrepaired_count": enum_unrepaired_count,
        "scene_plan_enum_unrepaired_rows": enum_unrepaired_rows,
        "scene_plan_enum_alias_normalized_count": enum_alias_normalized_count,
        "scene_plan_enum_alias_normalized_rows": enum_alias_normalized_rows,
        "illegal_route_count": illegal_route_count,
        "cast_mutation_count": cast_mutation_count,
        "speaker_role_invalid_count": speaker_role_invalid_count,
        "vocal_gender": vocal_gender if vocal_gender in ALLOWED_VOCAL_GENDERS else UNKNOWN_VOCAL_GENDER,
        "vocal_owner_role": vocal_owner_role if vocal_owner_role in {*_ROLE_KEYS, UNKNOWN_VOCAL_OWNER_ROLE} else UNKNOWN_VOCAL_OWNER_ROLE,
        "primary_role_by_segment": primary_role_by_segment,
        "visual_focus_role_by_segment": visual_focus_role_by_segment,
        "speaker_role_by_segment": speaker_role_by_segment,
        "reaction_role_by_segment": reaction_role_by_segment,
        "characterAppearanceModesByRole": {
            role: normalize_character_appearance_mode(mode)
            for role, mode in appearance_modes.items()
            if str(role or "").strip()
        },
        "sceneCharacterVisibilityPolicy": scene_character_visibility_policy,
        "lip_sync_voice_role_mismatch_segments": lip_sync_voice_role_mismatch_segments,
        "lip_sync_decision_by_segment": lip_sync_decision_by_segment,
        "lip_sync_rejected_reasons": lip_sync_rejected_reasons,
        "scene_plan_route_selection_reasons_by_segment": route_selection_reasons_by_segment,
        "final_route_by_segment": final_route_by_segment,
        "lip_sync_selected_count": lip_sync_selected_count,
        "consecutive_lip_sync_count": max_consecutive_lip_sync_count,
        "ia2v_route_requires_speaker_because_current_provider_uses_lipsync_workflow": ia2v_route_requires_speaker_because_current_provider_uses_lipsync_workflow,
        "target_route_mix": route_budget_target,
        "actual_route_mix": final_route_counts,
        "route_budget_pre_repair_actual_mix": pre_repair_counts,
        "route_budget_repair_applied": route_budget_repair_applied,
        "route_budget_repair_details": route_budget_repair_details,
        "final_semantic_rebalance_applied": final_semantic_rebalance_applied,
        "final_semantic_rebalance_details": final_semantic_rebalance_details,
        "scene_plan_route_strategy_active": _route_strategy_active(creative_config),
        "scene_plan_route_strategy_preset": route_budget_preset,
        "scene_plan_route_targets_per_block": _safe_dict(creative_config.get("route_targets_per_block")),
        "route_budget_original_targets": route_budget_original_targets,
        "route_budget_resolved_scene_count": expected_scene_count,
        "route_budget_resolved_targets": route_budget_target,
        "route_budget_resolved_from": route_budget_resolved_from,
        "route_budget_preset": route_budget_preset,
        "hardRouteMapApplied": bool(hard_route_map),
        "routeAssignmentSource": "creative_config.hard_route_assignments_by_segment" if hard_route_map else "gemini",
        "hard_route_assignments_by_segment": requested_route_locks_by_segment,
        "requested_route_locks_by_segment": requested_route_locks_by_segment,
        "scene_plan_route_lock_applied": bool(requested_route_locks_by_segment),
        "scene_plan_route_lock_source": "mapped_route_budget" if mapped_route_budget_lock_detected else "gemini_semantic_route_selection",
        "scene_plan_route_assignment_source": "mapped_route_budget" if mapped_route_budget_lock_detected else "gemini_semantic_route_selection",
        "scene_plan_route_budget_after_lock": final_route_counts,
        "scene_plan_route_locks_by_segment": requested_route_locks_by_segment,
        "scene_plan_route_budget_target_adjusted_for_mapped_no_first_last": mapped_route_budget_target_adjusted_for_mapped_no_first_last,
        "scene_plan_mapped_first_last_target_removed": mapped_path,
        "scene_plan_mapped_ia2v_contract_filled": mapped_path,
        "scene_plan_mapped_path_final_validation": mapped_path,
        "scene_plan_mapped_final_target_for_validation": route_budget_target,
        "scene_plan_mapped_generic_rebalance_skipped": mapped_generic_rebalance_skipped,
        "scene_plan_mapped_first_last_removed_from_final_validation": bool(mapped_path),
        "scene_plan_post_validity_route_rebalance": post_validity_route_rebalance,
        "scene_plan_mapped_default_vocal_role": mapped_default_vocal_role,
        "scene_plan_mapped_route_budget_lock_detected": mapped_route_budget_lock_detected,
        "scene_plan_mapped_route_budget_override_prevented": mapped_route_budget_override_prevented,
        "scene_plan_mapped_route_budget_post_normalization_applied": mapped_route_budget_post_normalization_applied,
        "scene_plan_mapped_route_budget_scene_candidate_windows_present": scene_candidate_windows_present,
        "scene_plan_mapped_route_budget_used_model": str(used_model or ""),
        "scene_plan_mapped_route_budget_post_normalization_diag": mapped_route_budget_post_normalization_diag,
        "scene_plan_route_budget_target": route_budget_target,
        "scene_plan_route_budget_actual": final_route_counts,
        "scene_plan_route_budget_mismatch": route_budget_mismatch,
        "scene_plan_route_budget_retry_used": False,
        "scene_plan_route_budget_retry_suppressed": bool(route_budget_mismatch),
        "scene_plan_route_budget_mismatch_reason": "gemini_did_not_respect_user_route_strategy" if route_budget_mismatch else "",
        "scene_plan_route_budget_mode": route_budget_mode,
        "scene_plan_route_budget_tolerance": route_budget_tolerance,
        "scene_plan_first_last_forbidden": True,
        "scene_plan_user_route_strategy_was_sent": bool(_route_strategy_active(creative_config)),
        "scene_plan_user_route_strategy_hard_constraint": bool(hard_short_clip_target),
        "scene_plan_empty_detected": scene_plan_empty_detected,
        "scene_plan_empty_reason": scene_plan_empty_reason,
        "scene_plan_fallback_applied": scene_plan_fallback_applied,
        "scene_plan_fallback_type": scene_plan_fallback_type,
        "scene_plan_fallback_row_count": scene_plan_fallback_row_count,
        "route_spacing": {
            "has_adjacent_ia2v": has_adjacent_ia2v,
            "has_adjacent_first_last": has_adjacent_first_last,
            "adjacent_first_last_pairs": adjacent_first_last_pairs,
            "warning": "adjacent_first_last_not_allowed" if has_adjacent_first_last else "",
        },
    }
    if include_debug_raw:
        normalization_diag["original_scenes"] = raw_storyboard

    if validation_error and not error_code:
        error_code = "SCENES_SCHEMA_INVALID"
    return plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, error_code

def build_gemini_scene_plan(
    *,
    api_key: str,
    package: dict[str, Any],
    validation_feedback: str = "",
    prompt_mode: str = "default",
) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    director_config = _safe_dict(input_pkg.get("director_config"))
    connected_context = _safe_dict(input_pkg.get("connected_context_summary"))
    present_cast_roles_raw = _safe_list(connected_context.get("presentCastRoles"))
    present_cast_roles = [
        str(role).strip()
        for role in present_cast_roles_raw
        if str(role).strip()
    ]
    if not present_cast_roles:
        role_identity_mapping = _safe_dict(connected_context.get("role_identity_mapping"))
        present_cast_roles = [
            str(role).strip()
            for role, payload in role_identity_mapping.items()
            if str(role).strip() and bool(_safe_dict(payload).get("presentCastRole"))
        ]
    default_vocal_role = present_cast_roles[0] if len(present_cast_roles) == 1 else "character_1"
    audio_map = _safe_dict(package.get("audio_map"))
    scene_windows = _safe_list(audio_map.get("scene_candidate_windows"))
    audio_segments = [_safe_dict(seg) for seg in _safe_list(audio_map.get("segments"))]
    segment_ids_by_index = [str(seg.get("segment_id") or "").strip() for seg in audio_segments]

    def _build_mapped_scene_plan_fallback(*, reason: str) -> dict[str, Any]:
        if not scene_windows:
            raise ValueError("SCENES_ERROR: scene_candidate_windows missing")
        mapped_scene_plan: list[dict[str, Any]] = []
        for idx, window_raw in enumerate(scene_windows):
            window = _safe_dict(window_raw)
            fallback_segment_id = segment_ids_by_index[idx] if idx < len(segment_ids_by_index) else ""
            segment_id = str(
                window.get("segment_id") or window.get("scene_id") or fallback_segment_id or f"seg_{idx + 1:02d}"
            ).strip()
            mapped_scene_plan.append(
                {
                    "segment_id": segment_id,
                    "scene_id": str(window.get("scene_id") or segment_id).strip(),
                    "t0": window.get("t0"),
                    "t1": window.get("t1"),
                    "duration": window.get("duration_sec"),
                    "energy": window.get("local_energy_band"),
                    "cut_reason": window.get("cut_reason"),
                    "density": window.get("visual_density_hint"),
                    "location": "",
                    "action": "",
                    "environment_interaction": "",
                    "visual_hook": "",
                    "camera": {"framing": "", "movement": "", "angle": ""},
                    "route": "i2v",
                    "primary_role": default_vocal_role,
                    "visual_focus_role": default_vocal_role,
                }
            )
        mapped_rows_missing_segment_id = [
            idx for idx, row in enumerate(mapped_scene_plan) if not str(_safe_dict(row).get("segment_id") or "").strip()
        ]
        mapped_row_segment_ids = [str(_safe_dict(row).get("segment_id") or "").strip() for row in mapped_scene_plan]
        mapped_scene_plan, route_budget_diag = _apply_route_budget_to_scene_rows(
            mapped_scene_plan,
            audio_map,
            creative_config,
            director_config,
        )
        route_mix_summary = {
            "i2v": sum(1 for row in mapped_scene_plan if str(_safe_dict(row).get("route") or "").strip().lower() == "i2v"),
            "ia2v": sum(1 for row in mapped_scene_plan if str(_safe_dict(row).get("route") or "").strip().lower() == "ia2v"),
            "first_last": 0,
        }
        mapped_final_segment_ids = [str(_safe_dict(row).get("segment_id") or "").strip() for row in mapped_scene_plan]
        mapped_final_missing_segment_id_count = sum(1 for seg_id in mapped_final_segment_ids if not seg_id)
        scene_plan_payload = {
            "scenes_version": SCENES_VERSION,
            "storyboard": mapped_scene_plan,
            "scenes": deepcopy(mapped_scene_plan),
            "route_mix_summary": route_mix_summary,
        }
        package["scene_plan"] = scene_plan_payload
        print(f"[SCENES] mapped fallback from candidate windows: {len(mapped_scene_plan)} scenes ({reason})")
        return {
            "ok": True,
            "scene_plan": scene_plan_payload,
            "error": "",
            "validation_error": "",
            "error_code": "",
            "used_fallback": True,
            "diagnostics": {
                "prompt_version": SCENE_PLAN_PROMPT_VERSION,
                "scene_plan_primary_strategy": "gemini_first",
                "scene_plan_fallback_used": True,
                "scene_plan_mapped_fallback_used": True,
                "scene_plan_mapped_fallback_reason": reason,
                "scene_plan_gemini_attempted": True,
                "scene_plan_used_model": "mapped_from_audio_map.scene_candidate_windows",
                "used_model": "mapped_from_audio_map.scene_candidate_windows",
                "scene_count": len(mapped_scene_plan),
                "scene_plan_scenes_version": SCENES_VERSION,
                "scene_plan_uses_segment_id_canonical": True,
                "mapped_debug_scene_windows_count": len(scene_windows),
                "mapped_debug_audio_segments_count": len(audio_segments),
                "mapped_debug_initial_row_count": len(mapped_scene_plan),
                "mapped_debug_initial_segment_ids": mapped_row_segment_ids,
                "mapped_debug_missing_segment_id_indices": mapped_rows_missing_segment_id,
                "mapped_debug_route_counts_after_budget": route_mix_summary,
                "mapped_debug_route_budget_diag": route_budget_diag,
                "scene_plan_mapped_default_vocal_role": default_vocal_role,
                "mapped_debug_final_segment_ids": mapped_final_segment_ids,
                "mapped_debug_final_missing_segment_id_count": mapped_final_missing_segment_id_count,
                "scene_plan_first_last_forbidden": True,
                **route_budget_diag,
            },
        }

    context, aux = _build_scene_planning_context(package)
    scene_segment_rows = _safe_list(aux.get("scene_segment_rows"))
    role_lookup = _safe_dict(aux.get("role_lookup"))
    compiled_contract = _safe_dict(aux.get("compiled_contract"))
    world_summary_used = bool(aux.get("world_summary_used"))
    include_debug_raw = _scene_plan_debug_enabled(package)
    input_payload = _safe_dict(package.get("input"))
    creative_config = _normalize_creative_config(input_payload.get("creative_config"))
    force_route_mode = str(input_payload.get("forceRouteMode") or "").strip().lower()
    forced_routes = [str(item).strip().lower() for item in _safe_list(input_payload.get("forced_routes")) if str(item).strip()]
    structure = str(input_payload.get("structure") or "").strip().lower()
    story_core_raw = _safe_dict(package.get("story_core"))
    role_plan_raw = _safe_dict(package.get("role_plan"))
    beat_map_raw = _safe_dict(package.get("beat_map"))
    route_preset = str(creative_config.get("route_strategy_preset") or "").strip().lower()
    empty_plan_fallback_allowed = bool(
        scene_segment_rows
        and role_lookup
        and _safe_list(story_core_raw.get("narrative_segments"))
        and (beat_map_raw or _safe_list(story_core_raw.get("narrative_segments")))
        and (route_preset or _route_strategy_active(creative_config))
        and _safe_list(role_plan_raw.get("scene_casting"))
    )
    vocal_gender = str(aux.get("vocal_gender") or UNKNOWN_VOCAL_GENDER).strip().lower() or UNKNOWN_VOCAL_GENDER
    vocal_owner_role = str(aux.get("vocal_owner_role") or UNKNOWN_VOCAL_OWNER_ROLE).strip() or UNKNOWN_VOCAL_OWNER_ROLE
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
        "scene_plan_primary_strategy": "gemini_first",
        "scene_plan_gemini_attempted": True,
        "scene_plan_gemini_retry_attempted": False,
        "scene_plan_gemini_validation_error": "",
        "scene_plan_fallback_used": False,
        "scene_plan_mapped_fallback_used": False,
        "scene_plan_mapped_fallback_reason": "",
        "scene_plan_first_last_forbidden": True,
        "scene_plan_director_config_present": bool(director_config),
        "scene_plan_director_config_keys": sorted(str(k).strip() for k in director_config.keys() if str(k).strip()),
        "scene_candidate_windows_bridge": bool(aux.get("uses_legacy_scene_candidate_windows_bridge")),
        "compiled_contract_bridge": bool(compiled_contract),
        "role_source_precedence": _safe_list(aux.get("scene_role_source_precedence")),
        "used_model": SCENE_PLAN_MODEL,
        "scene_plan_used_model": SCENE_PLAN_MODEL,
        "scene_count": len(scene_segment_rows),
        "scene_plan_scenes_version": SCENES_VERSION,
        "scene_plan_uses_segment_id_canonical": True,
        "watchability_fallback_count": 0,
        "world_summary_used": world_summary_used,
        "configured_timeout_sec": get_scenario_stage_timeout("scene_plan"),
        "timeout_stage_policy_name": scenario_timeout_policy_name("scene_plan"),
        "timed_out": False,
        "timeout_retry_attempted": False,
        "response_was_empty_after_timeout": False,
        "scene_plan_retry_prompt_mode": str(prompt_mode or "default"),
        **capability_diag,
    }
    audio_map = _safe_dict(package.get("audio_map"))
    audio_segments = _safe_list(audio_map.get("segments"))
    audio_by_id = {
        str(_safe_dict(seg).get("segment_id") or "").strip(): _safe_dict(seg)
        for seg in audio_segments
        if str(_safe_dict(seg).get("segment_id") or "").strip()
    }
    scene_segment_timing_debug: list[dict[str, Any]] = []
    for idx, row in enumerate(scene_segment_rows):
        row_obj = _safe_dict(row)
        segment_id = str(row_obj.get("segment_id") or "").strip()
        audio_seg = _safe_dict(audio_by_id.get(segment_id))
        if audio_seg:
            t0 = _round3(audio_seg.get("t0"))
            t1 = _round3(audio_seg.get("t1"))
            row_obj["t0"] = t0
            row_obj["t1"] = t1
            row_obj["duration_sec"] = _round3(max(0.0, t1 - t0))
            scene_segment_rows[idx] = row_obj
        scene_segment_timing_debug.append(
            {
                "segment_id": segment_id or "unknown",
                "t0": _round3(row_obj.get("t0")),
                "t1": _round3(row_obj.get("t1")),
            }
        )
    diagnostics["scene_segment_timing_debug"] = scene_segment_timing_debug
    invalid_timing_segments: list[str] = []
    for row in scene_segment_rows:
        row_obj = _safe_dict(row)
        segment_id = str(row_obj.get("segment_id") or "").strip() or "unknown"
        t0 = _round3(row_obj.get("t0"))
        t1 = _round3(row_obj.get("t1"))
        duration_sec = _round3(row_obj.get("duration_sec"))
        if t0 < 0.0 or t1 <= t0 or duration_sec <= 0.0 or abs(duration_sec - (t1 - t0)) > 0.02:
            invalid_timing_segments.append(segment_id)

    def _ensure_scene_plan_scenes(scene_plan: dict[str, Any]) -> None:
        existing_scenes = _safe_list(scene_plan.get("scenes"))
        existing_prompts_by_segment = {
            str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
            for row in existing_scenes
            if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
        }
        prompt_lookup = {
            str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
            for row in _safe_list(_safe_dict(package.get("scene_prompts")).get("segments"))
            if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
        }
        if not prompt_lookup:
            prompt_lookup = {
                str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
                for row in _safe_list(_safe_dict(package.get("scene_prompts")).get("scenes"))
                if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
            }
        beat_lookup = {
            str(_safe_dict(row).get("source_segment_id") or "").strip(): _safe_dict(row)
            for row in _safe_list(_safe_dict(_safe_dict(package.get("story_core")).get("beat_map")).get("beats"))
            if str(_safe_dict(row).get("source_segment_id") or "").strip()
        }
        scene_segment_rows_for_plan: list[dict[str, Any]] = []
        for source_row in scene_segment_rows:
            source = _safe_dict(source_row)
            segment_id = str(source.get("segment_id") or source.get("scene_id") or "").strip()
            if not segment_id:
                continue
            beat_row = _safe_dict(beat_lookup.get(segment_id))
            prompt_row = _safe_dict(prompt_lookup.get(segment_id))
            existing_row = _safe_dict(existing_prompts_by_segment.get(segment_id))
            beat_primary_subject = str(beat_row.get("beat_primary_subject") or source.get("beat_primary_subject") or "").strip().lower()
            has_vocal = beat_primary_subject == "character_1"
            route = "ia2v" if has_vocal else "i2v"
            fallback_prompt = str(
                beat_row.get("beat_focus_hint")
                or source.get("beat_focus_hint")
                or beat_row.get("beat_purpose")
                or source.get("beat_purpose")
                or ""
            ).strip()
            video_prompt = str(
                prompt_row.get("video_prompt")
                or prompt_row.get("positive_video_prompt")
                or existing_row.get("video_prompt")
                or source.get("video_prompt")
                or fallback_prompt
            ).strip()
            image_prompt = str(
                prompt_row.get("image_prompt")
                or prompt_row.get("photo_prompt")
                or existing_row.get("image_prompt")
                or source.get("image_prompt")
                or video_prompt
                or fallback_prompt
            ).strip()
            scene_segment_rows_for_plan.append(
                {
                    "scene_id": segment_id,
                    "segment_id": segment_id,
                    "t0": _round3(source.get("t0")),
                    "t1": _round3(source.get("t1")),
                    "duration_sec": _round3(source.get("duration_sec")),
                    "route": route,
                    "video_prompt": video_prompt,
                    "image_prompt": image_prompt,
                }
            )
        if scene_segment_rows_for_plan:
            scene_plan["scenes"] = scene_segment_rows_for_plan
        else:
            raise ValueError("scene_plan has no scenes after build")
        diagnostics["scene_plan_scenes_count"] = len(_safe_list(scene_plan.get("scenes")))

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
            "adjacent_first_last_pairs": _safe_list(spacing.get("adjacent_first_last_pairs")),
            "scene_plan_route_spacing_retry_used": bool(normalization_diag.get("scene_plan_route_spacing_retry_used")),
            "vocal_gender": str(normalization_diag.get("vocal_gender") or vocal_gender or UNKNOWN_VOCAL_GENDER),
            "vocal_owner_role": str(normalization_diag.get("vocal_owner_role") or vocal_owner_role or UNKNOWN_VOCAL_OWNER_ROLE),
            "primary_role_by_segment": _safe_dict(normalization_diag.get("primary_role_by_segment")),
            "visual_focus_role_by_segment": _safe_dict(normalization_diag.get("visual_focus_role_by_segment")),
            "speaker_role_by_segment": _safe_dict(normalization_diag.get("speaker_role_by_segment")),
            "reaction_role_by_segment": _safe_dict(normalization_diag.get("reaction_role_by_segment")),
            "lip_sync_voice_role_mismatch_segments": _safe_list(normalization_diag.get("lip_sync_voice_role_mismatch_segments")),
            "lip_sync_decision_by_segment": _safe_dict(normalization_diag.get("lip_sync_decision_by_segment")),
            "lip_sync_rejected_reasons": _safe_dict(normalization_diag.get("lip_sync_rejected_reasons")),
            "lip_sync_selected_count": int(normalization_diag.get("lip_sync_selected_count") or 0),
            "consecutive_lip_sync_count": int(normalization_diag.get("consecutive_lip_sync_count") or 0),
            "ia2v_route_requires_speaker_because_current_provider_uses_lipsync_workflow": bool(
                normalization_diag.get(
                    "ia2v_route_requires_speaker_because_current_provider_uses_lipsync_workflow"
                )
            ),
            "scene_plan_technical_leaks_detected": int(normalization_diag.get("scene_plan_technical_leaks_detected") or 0),
            "scene_plan_technical_leak_field": str(normalization_diag.get("scene_plan_technical_leak_field") or ""),
            "scene_plan_technical_leak_token": str(normalization_diag.get("scene_plan_technical_leak_token") or ""),
            "scene_plan_technical_leak_excerpt": str(normalization_diag.get("scene_plan_technical_leak_excerpt") or ""),
            "scene_plan_technical_leak_fields": _safe_list(normalization_diag.get("scene_plan_technical_leak_fields")),
            "scene_plan_technical_leak_tokens": _safe_list(normalization_diag.get("scene_plan_technical_leak_tokens")),
            "scene_plan_technical_leak_cleaned_locally": bool(normalization_diag.get("scene_plan_technical_leak_cleaned_locally")),
            "scene_plan_validation_errors": _safe_list(normalization_diag.get("scene_plan_validation_errors")),
            "scene_plan_error_codes": _safe_list(normalization_diag.get("scene_plan_error_codes")),
            "scene_plan_enum_invalid_detected": bool(normalization_diag.get("scene_plan_enum_invalid_detected")),
            "scene_plan_enum_invalid_count": int(normalization_diag.get("scene_plan_enum_invalid_count") or 0),
            "scene_plan_enum_invalid_field": str(normalization_diag.get("scene_plan_enum_invalid_field") or ""),
            "scene_plan_enum_invalid_value": str(normalization_diag.get("scene_plan_enum_invalid_value") or ""),
            "scene_plan_enum_invalid_allowed_values": _safe_list(normalization_diag.get("scene_plan_enum_invalid_allowed_values")),
            "scene_plan_enum_invalid_segment_id": str(normalization_diag.get("scene_plan_enum_invalid_segment_id") or ""),
            "scene_plan_enum_invalid_rows": _safe_list(normalization_diag.get("scene_plan_enum_invalid_rows")),
            "scene_plan_enum_repair_applied": bool(normalization_diag.get("scene_plan_enum_repair_applied")),
            "scene_plan_enum_repair_count": int(normalization_diag.get("scene_plan_enum_repair_count") or 0),
            "scene_plan_enum_repair_rows": _safe_list(normalization_diag.get("scene_plan_enum_repair_rows")),
            "scene_plan_enum_unrepaired_count": int(normalization_diag.get("scene_plan_enum_unrepaired_count") or 0),
            "scene_plan_enum_unrepaired_rows": _safe_list(normalization_diag.get("scene_plan_enum_unrepaired_rows")),
            "scene_plan_route_strategy_active": bool(normalization_diag.get("scene_plan_route_strategy_active")),
            "scene_plan_route_strategy_preset": str(normalization_diag.get("scene_plan_route_strategy_preset") or ""),
            "scene_plan_route_targets_per_block": _safe_dict(normalization_diag.get("scene_plan_route_targets_per_block")),
            "route_budget_original_targets": _safe_dict(normalization_diag.get("route_budget_original_targets")),
            "route_budget_resolved_scene_count": int(normalization_diag.get("route_budget_resolved_scene_count") or 0),
            "route_budget_resolved_targets": _safe_dict(normalization_diag.get("route_budget_resolved_targets")),
            "route_budget_resolved_from": str(normalization_diag.get("route_budget_resolved_from") or ""),
            "route_budget_preset": str(normalization_diag.get("route_budget_preset") or ""),
            "scene_plan_route_budget_target": _safe_dict(normalization_diag.get("scene_plan_route_budget_target")),
            "scene_plan_route_budget_actual": _safe_dict(normalization_diag.get("scene_plan_route_budget_actual")),
            "scene_plan_route_budget_mismatch": bool(normalization_diag.get("scene_plan_route_budget_mismatch")),
            "scene_plan_route_budget_retry_used": bool(normalization_diag.get("scene_plan_route_budget_retry_used")),
            "scene_plan_route_budget_retry_suppressed": bool(normalization_diag.get("scene_plan_route_budget_retry_suppressed")),
            "scene_plan_route_budget_mismatch_reason": str(normalization_diag.get("scene_plan_route_budget_mismatch_reason") or ""),
            "scene_plan_user_route_strategy_was_sent": bool(normalization_diag.get("scene_plan_user_route_strategy_was_sent")),
            "scene_plan_user_route_strategy_hard_constraint": bool(normalization_diag.get("scene_plan_user_route_strategy_hard_constraint")),
            "scene_plan_empty_detected": bool(normalization_diag.get("scene_plan_empty_detected")),
            "scene_plan_empty_reason": str(normalization_diag.get("scene_plan_empty_reason") or ""),
            "scene_plan_fallback_applied": bool(normalization_diag.get("scene_plan_fallback_applied")),
            "scene_plan_fallback_type": str(normalization_diag.get("scene_plan_fallback_type") or ""),
            "scene_plan_fallback_row_count": int(normalization_diag.get("scene_plan_fallback_row_count") or 0),
            "scene_plan_route_selection_reasons_by_segment": _safe_dict(normalization_diag.get("scene_plan_route_selection_reasons_by_segment")),
            "scene_plan_final_route_by_segment": _safe_dict(normalization_diag.get("final_route_by_segment")),
            "scene_plan_requested_route_locks_by_segment": _safe_dict(normalization_diag.get("requested_route_locks_by_segment")),
            "scene_plan_route_locks_by_segment": _safe_dict(scene_plan.get("route_locks_by_segment")),
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
            creative_config=creative_config,
            force_route_mode=force_route_mode,
            forced_routes=forced_routes,
            structure=structure,
            vocal_gender=vocal_gender,
            vocal_owner_role=vocal_owner_role,
            include_debug_raw=include_debug_raw,
            empty_plan_fallback_allowed=empty_plan_fallback_allowed,
        )
        _ensure_scene_plan_scenes(plan)
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

    if invalid_timing_segments:
        plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, _ = _normalize_scene_plan(
            {},
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            creative_config=creative_config,
            force_route_mode=force_route_mode,
            forced_routes=forced_routes,
            structure=structure,
            vocal_gender=vocal_gender,
            vocal_owner_role=vocal_owner_role,
            include_debug_raw=include_debug_raw,
            empty_plan_fallback_allowed=empty_plan_fallback_allowed,
        )
        _ensure_scene_plan_scenes(plan)
        diagnostics["error_code"] = "SCENES_AUDIO_TIMING_REQUIRED"
        diagnostics["validation_error"] = "audio_segments_missing_timing"
        diagnostics["invalid_timing_segment_ids"] = list(dict.fromkeys(invalid_timing_segments))
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
            "error": "audio_segments_missing_timing",
            "validation_error": "audio_segments_missing_timing",
            "error_code": "SCENES_AUDIO_TIMING_REQUIRED",
            "used_fallback": used_fallback,
            "diagnostics": diagnostics,
        }

    missing_role_segments = [str(row.get("segment_id") or "") for row in scene_segment_rows if not _safe_dict(role_lookup.get(str(row.get("segment_id") or "")))]
    missing_core_segments = [str(item).strip() for item in _safe_list(aux.get("missing_core_source_segments")) if str(item).strip()]
    if missing_core_segments:
        plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, _ = _normalize_scene_plan(
            {},
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            creative_config=creative_config,
            force_route_mode=force_route_mode,
            forced_routes=forced_routes,
            structure=structure,
            vocal_gender=vocal_gender,
            vocal_owner_role=vocal_owner_role,
            include_debug_raw=include_debug_raw,
            empty_plan_fallback_allowed=empty_plan_fallback_allowed,
        )
        _ensure_scene_plan_scenes(plan)
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
            creative_config=creative_config,
            force_route_mode=force_route_mode,
            forced_routes=forced_routes,
            structure=structure,
            vocal_gender=vocal_gender,
            vocal_owner_role=vocal_owner_role,
            include_debug_raw=include_debug_raw,
            empty_plan_fallback_allowed=empty_plan_fallback_allowed,
        )
        _ensure_scene_plan_scenes(plan)
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

    prompt = _build_prompt(context, validation_feedback=validation_feedback, prompt_mode=prompt_mode)
    configured_timeout = get_scenario_stage_timeout("scene_plan")

    def _run_generation(prompt_text: str) -> tuple[dict[str, Any], bool, str, int, dict[str, Any], str]:
        response = post_generate_content(
            api_key=str(api_key or "").strip(),
            model=SCENE_PLAN_MODEL,
            body={
                "contents": [{"role": "user", "parts": [{"text": prompt_text}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            timeout=configured_timeout,
        )
        if isinstance(response, dict) and response.get("__http_error__"):
            raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")
        parsed = _extract_json_obj(_extract_gemini_text(response))
        if "storyboard" not in parsed and _safe_list(parsed.get("scenes")):
            parsed["storyboard"] = _safe_list(parsed.get("scenes"))
        return _normalize_scene_plan(
            parsed,
            scene_segment_rows=scene_segment_rows,
            role_lookup=role_lookup,
            creative_config=creative_config,
            force_route_mode=force_route_mode,
            forced_routes=forced_routes,
            structure=structure,
            vocal_gender=vocal_gender,
            vocal_owner_role=vocal_owner_role,
            include_debug_raw=include_debug_raw,
            character_appearance_modes_by_role=_safe_dict(aux.get("character_appearance_modes_by_role")),
            empty_plan_fallback_allowed=empty_plan_fallback_allowed,
            used_model=SCENE_PLAN_MODEL,
            audio_map=audio_map,
            director_config=director_config,
        )

    try:
        scene_plan, used_fallback, validation_error, watchability_fallback_count, normalization_diag, error_code = _run_generation(prompt)
        _ensure_scene_plan_scenes(scene_plan)
        normalization_diag["scene_plan_route_spacing_retry_used"] = False
        diagnostics["scene_plan_gemini_validation_error"] = str(validation_error or "")
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
        if bool(storyboard_rows) and not has_validation_error:
            diagnostics["scene_plan_used_model"] = SCENE_PLAN_MODEL
            diagnostics["scene_plan_fallback_used"] = False
            diagnostics["scene_plan_mapped_fallback_used"] = False
            return {
                "ok": True,
                "scene_plan": scene_plan,
                "error": "",
                "validation_error": "",
                "error_code": error_code,
                "used_fallback": used_fallback,
                "diagnostics": diagnostics,
            }
        diagnostics["scene_plan_gemini_retry_attempted"] = True
        retry_feedback = str(validation_feedback or validation_error or error_code or "scene_plan_invalid")
        retry_prompt = _build_prompt(context, validation_feedback=retry_feedback, prompt_mode="compact_route_budget_retry")
        retry_plan, retry_used_fallback, retry_validation_error, retry_watchability_fallback_count, retry_diag, retry_error_code = _run_generation(retry_prompt)
        _ensure_scene_plan_scenes(retry_plan)
        retry_diag["scene_plan_route_spacing_retry_used"] = True
        diagnostics["scene_plan_gemini_validation_error"] = str(retry_validation_error or validation_error or "")
        diagnostics["error_code"] = retry_error_code
        diagnostics.update(
            _collect_scene_plan_diagnostics(
                scene_plan=retry_plan,
                normalization_diag=retry_diag,
                watchability_fallback_count=retry_watchability_fallback_count,
                include_presence_modes=True,
            )
        )
        retry_storyboard_rows = _safe_list(retry_plan.get("storyboard"))
        retry_has_error = bool(str(retry_validation_error or "").strip())
        if bool(retry_storyboard_rows) and not retry_has_error:
            diagnostics["scene_plan_used_model"] = SCENE_PLAN_MODEL
            diagnostics["scene_plan_fallback_used"] = False
            diagnostics["scene_plan_mapped_fallback_used"] = False
            return {
                "ok": True,
                "scene_plan": retry_plan,
                "error": "",
                "validation_error": "",
                "error_code": retry_error_code,
                "used_fallback": retry_used_fallback,
                "diagnostics": diagnostics,
            }
        return _build_mapped_scene_plan_fallback(reason=str(retry_validation_error or validation_error or "invalid_scene_plan"))
    except Exception as exc:  # noqa: BLE001
        timeout_error = is_timeout_error(exc)
        if timeout_error:
            diagnostics["timed_out"] = True
            diagnostics["response_was_empty_after_timeout"] = True
        diagnostics["scene_plan_gemini_validation_error"] = str(exc)
        return _build_mapped_scene_plan_fallback(reason=str(exc))
