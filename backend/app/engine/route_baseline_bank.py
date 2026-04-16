from __future__ import annotations

import copy
from typing import Any

ROUTE_BASELINE_BANK_VERSION = "1.1"

_ROUTE_BASELINE_BANK: dict[str, dict[str, Any]] = {
    "i2v": {
        "status": "tested_production_safe_leaning",
        "tested_phrasing": [
            "restrained or purposeful walk",
            "smooth push-in",
            "side tracking with controlled parallax",
            "head turn / tension glance",
            "grounded real-time motion",
        ],
        "forbidden_patterns": [
            "strong camera arc as default",
            "orbit logic",
            "overloaded multi-motion choreography",
            "bullet-time feel",
            "arc-first choreography",
        ],
        "route_constraints": [
            "one clear body action + one clear camera action",
            "avoid orbit/arc-first defaults",
            "first_frame_prompt must be null",
            "last_frame_prompt must be null",
        ],
        "prompt_structure_template": "[Subject], [Action], [Environment], [Camera]. Keep grounded real-time motion and controlled camera.",
        "notes": [
            "Arc patterns remain blocked/experimental and must not be default behavior.",
        ],
    },
    "ia2v": {
        "status": "conservative_meaningful",
        "tested_phrasing": [
            "singing-ready image anchor",
            "face and mouth readable",
            "expressive lip sync",
            "natural jaw motion",
            "subtle cheek tension",
            "small eyebrow movement",
            "slight rhythmic head motion",
            "clear camera direction",
            "mostly in place, no walking",
        ],
        "forbidden_patterns": [
            "mannequin feel",
            "overloaded negative prompt",
            "broken hands",
            "distorted mouth",
            "intersections",
            "identity drift",
            "walking choreography",
        ],
        "route_constraints": [
            "performance-first, mouth readability prioritized",
            "camera movement must remain clear and controlled",
            "first_frame_prompt must be null",
            "last_frame_prompt must be null",
            "audio_sync_mode should normally be phrase_sensitive",
        ],
        "prompt_structure_template": "[Performer], [Lip-sync behavior], [Facial articulation], [Camera direction], [Environment continuity].",
        "notes": [
            "Avoid body travel; motion should be mostly in-place and performance-led.",
        ],
    },
    "first_last": {
        "status": "canonical_transition_mode",
        "tested_phrasing": [
            "Anchor A -> Event -> Anchor B",
            "same world / identity / lighting family / geometry continuity",
            "one visible controlled delta",
        ],
        "forbidden_patterns": [
            "lip-sync focus",
            "subtle facial-only acting",
            "crowd chaos",
            "too small delta",
            "too large delta",
            "random transition logic",
        ],
        "route_constraints": [
            "positive_prompt is transition video prompt",
            "first_frame_prompt and last_frame_prompt are required",
            "frame_strategy must be start_end",
            "transition_kind should typically be controlled or bridge",
        ],
        "prompt_structure_template": "Anchor A description -> controlled event -> Anchor B description, preserving world/identity/lighting geometry family.",
        "notes": [
            "Best for reveal, activation, destruction, impact, or atmosphere shift with controlled delta.",
        ],
    },
}


def get_route_baseline_bank() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(_ROUTE_BASELINE_BANK)
