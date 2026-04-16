from __future__ import annotations

from typing import Any

ROUTE_BASELINE_BANK_VERSION = "1.0"

ROUTE_BASELINE_BANK: dict[str, Any] = {
    "route_baselines_version": ROUTE_BASELINE_BANK_VERSION,
    "routes": {
        "i2v": {
            "intent": "single-image grounded motion",
            "preferred_patterns": [
                "safe push-in",
                "side tracking with controlled parallax",
                "restrained walk",
                "attention shift with readable continuity",
            ],
            "avoid_patterns": [
                "weak arc with no clear intent",
                "ambiguous camera/body action mix",
                "overloaded multi-action choreography",
            ],
        },
        "ia2v": {
            "intent": "audio-attentive performer readability",
            "preferred_patterns": [
                "singer-first frame",
                "readable face and mouth",
                "directional camera",
                "gentle push-in, pull-back, or lateral movement",
            ],
            "avoid_patterns": [
                "face too small for readability",
                "camera language that hides mouth performance",
                "crowd-chaos framing around the performer",
            ],
        },
        "first_last": {
            "intent": "two-frame continuity transition",
            "preferred_patterns": [
                "Anchor A -> Event -> Anchor B",
                "stable continuity across world/identity/wardrobe",
                "clear state delta",
                "controlled transition arc",
            ],
            "avoid_patterns": [
                "lip-sync-centric behaviors",
                "crowd-chaos transitions",
                "subtle-only facial beats without visible state change",
            ],
        },
    },
}
