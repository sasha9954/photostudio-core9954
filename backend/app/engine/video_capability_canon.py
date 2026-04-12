from __future__ import annotations

from copy import deepcopy
from typing import Any

CAPABILITY_RULES_SOURCE_VERSION = "video_capability_canon_ltx_2_3_v1"
DEFAULT_VIDEO_MODEL_ID = "ltx_2_3"
KNOWN_ROUTE_TYPES = ("i2v", "ia2v", "first_last", "lipsync")
RULE_STATUSES = ("verified_safe", "experimental", "blocked")


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


_CANON_REGISTRY: dict[str, dict[str, Any]] = {
    "ltx_2_3": {
        "notes": [
            "LTX 2.3 capability canon is route-aware and conservative by default.",
            "Unknown/unvalidated patterns should not be normalized into defaults.",
        ],
        "route_profiles": {
            "i2v": {
                "capability_status": "verified_in_production_like_tests",
                "verified_safe": [
                    "restrained forward walk",
                    "smooth push-in",
                    "side tracking with controlled parallax",
                    "subtle side glance / head turn",
                    "tension / suspicion micro-acting",
                    "one dominant motion idea per shot",
                    "grounded real-time motion",
                    "modest fabric response",
                    "stable world continuity",
                    "modest increase of pace without aggressive stylization",
                ],
                "experimental": [
                    "stronger lateral reveal",
                    "more active tracking with moderate parallax",
                    "brief controlled turn with fabric sweep",
                    "stronger purposeful movement if still single-dominant-action",
                ],
                "blocked": [
                    "small orbit camera",
                    "camera arc as default expressive move",
                    "360-degree camera move",
                    "overloaded multi-motion choreography",
                    "vague make it more dynamic",
                    "complex combined camera+body+cloth action",
                    "stylized bullet-time / matrix-like motion",
                    "aggressive cloth physics showcase",
                    "complex crowd interaction as default",
                ],
                "scene_grammar_hints": {
                    "preferred_grammar": [
                        "single dominant action line",
                        "camera-led readability",
                        "stable world and identity continuity",
                    ],
                    "avoid_grammar": [
                        "stacked choreography with multiple simultaneous deltas",
                        "orbit-first scene logic",
                    ],
                },
                "prompt_hints": {
                    "camera_behavior": ["smooth push-in", "gentle lateral tracking", "controlled parallax"],
                    "motion_language": ["grounded real-time", "readable human-scale motion"],
                    "blocked_motion_filters": [
                        "orbit",
                        "360 camera",
                        "bullet-time",
                        "aggressive cloth physics",
                    ],
                },
                "notes": [
                    "Experimental moves are opt-in and should not be default choices.",
                ],
            },
            "first_last": {
                "capability_status": "verified_for_continuity_transitions",
                "verified_safe": [
                    "same world family",
                    "same character identity",
                    "same framing family",
                    "same lighting family",
                    "same wardrobe/prop state",
                    "one controlled state delta",
                    "subtle progression between start and end",
                    "push-in / pull-back / state-change logic",
                    "same-scene continuation logic",
                ],
                "experimental": [
                    "slightly changed angle within same framing family",
                    "small parallax reveal between first and last",
                    "emotional state shift with minimal geometry change",
                ],
                "blocked": [
                    "location swap",
                    "hard composition reset",
                    "dramatic camera orbit",
                    "wild body pose change",
                    "multiple simultaneous deltas",
                    "large time-of-day jump unless explicitly requested",
                ],
                "scene_grammar_hints": {
                    "preferred_grammar": [
                        "same-scene micro-transition",
                        "single subtle delta",
                        "continuity-first framing",
                    ],
                    "avoid_grammar": [
                        "travel progression",
                        "hard reset between frames",
                    ],
                },
                "prompt_hints": {
                    "camera_behavior": ["micro push/pull", "settled framing", "small controlled side arc"],
                    "motion_language": ["single subtle delta", "continuity-preserving transition"],
                    "blocked_motion_filters": ["location jump", "dramatic orbit", "pose reset", "geometry rewrite"],
                },
                "notes": [
                    "first_last should solve controlled continuity progression, not geography change.",
                ],
            },
            "lipsync": {
                "capability_status": "verified_for_readable_performance_staging",
                "verified_safe": [
                    "readable face",
                    "stable framing",
                    "subtle upper-body motion",
                    "controlled hand gestures",
                    "audio-driven emotion",
                    "restrained expressivity",
                    "no risky full-body choreography",
                ],
                "experimental": [
                    "stronger performance emotion with limited torso/hand motion",
                    "slightly more active gesture language if face stays readable",
                ],
                "blocked": [
                    "aggressive dance motion during lipsync",
                    "strong full-body movement",
                    "unstable framing",
                    "complex arm choreography",
                    "crowded interaction-heavy lipsync staging",
                ],
                "scene_grammar_hints": {
                    "preferred_grammar": [
                        "face-readable performance scenes",
                        "controlled torso/hand support",
                    ],
                    "avoid_grammar": [
                        "dance-first staging",
                        "crowd-heavy interaction",
                    ],
                },
                "prompt_hints": {
                    "camera_behavior": ["stable framing", "gentle camera drift", "readability-first composition"],
                    "motion_language": ["audio-phrase-driven expression", "restrained upper-body performance"],
                    "blocked_motion_filters": ["aggressive dance", "full-body choreography", "unstable framing"],
                },
                "notes": [
                    "Lipsync priorities: articulation readability and emotional clarity.",
                ],
            },
            "ia2v": {
                "capability_status": "partially_defined",
                "verified_safe": [
                    "conservative defaults only",
                ],
                "experimental": [
                    "most ia2v behaviors are still experimental",
                    "readability-first performance with limited movement",
                ],
                "blocked": [
                    "overclaiming untested ia2v physical capabilities",
                ],
                "scene_grammar_hints": {
                    "preferred_grammar": [
                        "conservative readable staging",
                        "single expressive objective",
                    ],
                    "avoid_grammar": [
                        "high-complexity choreography",
                        "untested high-amplitude camera/body combinations",
                    ],
                },
                "prompt_hints": {
                    "camera_behavior": ["stable to gently active camera only"],
                    "motion_language": ["mostly experimental, keep conservative wording"],
                    "blocked_motion_filters": ["untested extreme dynamics"],
                },
                "notes": [
                    "ia2v canon is incomplete: avoid fake confidence and keep explicit uncertainty.",
                ],
            },
        },
        "first_last_pairing_rules": {
            "same_identity_required": True,
            "same_world_geometry_family_required": True,
            "same_lighting_family_required": True,
            "same_costume_prop_state_required": True,
            "allowed_delta_types": [
                "gaze shift",
                "posture release / tighten",
                "gentle forward intention",
                "mild camera distance change",
                "emotional shift",
                "same-scene continuation",
            ],
            "blocked_delta_types": [
                "location change",
                "drastic pose reset",
                "hard angle reset",
                "drastic time jump",
                "wardrobe change",
                "geometry rewrite",
            ],
            "preferred_transition_families": [
                "push-in",
                "pull-back",
                "camera settle",
                "visibility reveal",
            ],
        },
        "lipsync_rules": {
            "face_readability_required": True,
            "stable_framing_required": True,
            "allowed_expressivity": ["restrained", "controlled", "audio-driven"],
            "blocked_expressivity": ["aggressive dance", "full-body choreography", "unstable framing"],
        },
    }
}


def get_video_model_capability_profile(model_id: str, route_type: str) -> dict[str, Any]:
    model_key = str(model_id or DEFAULT_VIDEO_MODEL_ID).strip().lower() or DEFAULT_VIDEO_MODEL_ID
    route_key = str(route_type or "i2v").strip().lower() or "i2v"
    model_profile = _safe_dict(_CANON_REGISTRY.get(model_key))
    route_profile = _safe_dict(_safe_dict(model_profile.get("route_profiles")).get(route_key))
    if route_profile:
        return deepcopy(route_profile)
    fallback_profile = _safe_dict(_safe_dict(model_profile.get("route_profiles")).get("i2v"))
    out = deepcopy(fallback_profile)
    out["route_fallback_from"] = route_key
    out["notes"] = _safe_list(out.get("notes")) + [f"route '{route_key}' not defined; i2v profile used as conservative fallback"]
    return out


def get_scene_grammar_hints(model_id: str, route_type: str) -> dict[str, Any]:
    profile = get_video_model_capability_profile(model_id, route_type)
    hints = _safe_dict(profile.get("scene_grammar_hints"))
    return deepcopy(hints)


def is_pattern_allowed(model_id: str, route_type: str, pattern: str) -> bool:
    token = str(pattern or "").strip().lower()
    if not token:
        return False
    profile = get_video_model_capability_profile(model_id, route_type)
    blocked = {str(v).strip().lower() for v in _safe_list(profile.get("blocked")) if str(v).strip()}
    if token in blocked:
        return False
    verified = {str(v).strip().lower() for v in _safe_list(profile.get("verified_safe")) if str(v).strip()}
    experimental = {str(v).strip().lower() for v in _safe_list(profile.get("experimental")) if str(v).strip()}
    if token in verified or token in experimental:
        return True
    return False


def get_first_last_pairing_rules(model_id: str) -> dict[str, Any]:
    model_key = str(model_id or DEFAULT_VIDEO_MODEL_ID).strip().lower() or DEFAULT_VIDEO_MODEL_ID
    model_profile = _safe_dict(_CANON_REGISTRY.get(model_key))
    return deepcopy(_safe_dict(model_profile.get("first_last_pairing_rules")))


def get_lipsync_rules(model_id: str) -> dict[str, Any]:
    model_key = str(model_id or DEFAULT_VIDEO_MODEL_ID).strip().lower() or DEFAULT_VIDEO_MODEL_ID
    model_profile = _safe_dict(_CANON_REGISTRY.get(model_key))
    return deepcopy(_safe_dict(model_profile.get("lipsync_rules")))


def build_capability_diagnostics_summary(
    *,
    model_id: str,
    route_type: str,
    story_core_guard_applied: bool,
    scene_plan_guard_applied: bool,
    prompt_guard_applied: bool,
) -> dict[str, Any]:
    return {
        "active_video_model_capability_profile": str(model_id or DEFAULT_VIDEO_MODEL_ID).strip().lower() or DEFAULT_VIDEO_MODEL_ID,
        "active_route_capability_mode": str(route_type or "i2v").strip().lower() or "i2v",
        "story_core_capability_guard_applied": bool(story_core_guard_applied),
        "scene_plan_capability_guard_applied": bool(scene_plan_guard_applied),
        "prompt_capability_guard_applied": bool(prompt_guard_applied),
        "capability_rules_source_version": CAPABILITY_RULES_SOURCE_VERSION,
    }


def get_capability_rules_source_version() -> str:
    return CAPABILITY_RULES_SOURCE_VERSION
