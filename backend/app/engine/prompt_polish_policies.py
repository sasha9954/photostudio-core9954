from __future__ import annotations

import re

IA2V_READABILITY_POLICY = (
    "Face and upper-body performance stays clearly readable with mouth and jaw unobstructed.",
    "Performer remains the visual center; no crowd occlusion between performer and viewer.",
    "Allow only subtle shoulder, chest, neck, and head rhythm; no wide choreography.",
    "No aggressive motion blur, no extreme turn away from camera, and no hands covering the face.",
)

_IA2V_FOCUS_TOKENS = (
    "build",
    "climax",
    "pivot",
    "emotional",
    "performance",
    "expressive",
    "hero",
    "face",
    "portrait",
    "close",
    "chest-up",
    "waist-up",
    "upper body",
)

_IA2V_POLICY_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (IA2V_READABILITY_POLICY[0], ("face", "upper", "mouth", "jaw", "readable")),
    (IA2V_READABILITY_POLICY[1], ("visual center", "crowd occlusion", "occlusion")),
    (IA2V_READABILITY_POLICY[2], ("subtle", "rhythm", "wide choreography")),
    (IA2V_READABILITY_POLICY[3], ("motion blur", "turn away", "hands covering")),
)

NEGATIVE_PROMPT_ARTIFACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\bfast\s+the\s+(?:perspective|view|camera)\s+shifts?\s+gently\s+with\s+the\s+moment\b",
            re.IGNORECASE,
        ),
        "rapid perspective shifts",
    ),
    (
        re.compile(
            r"\bfast\s+attention\s+moves?\s+closer\s+to\s+her\s+expression\b",
            re.IGNORECASE,
        ),
        "abrupt push-in emphasis",
    ),
)


def build_ia2v_readability_clauses(*, existing_text: str, semantic_context: str = "") -> list[str]:
    merged = " ".join((str(existing_text or ""), str(semantic_context or ""))).lower()
    performance_focused = any(token in str(semantic_context or "").lower() for token in _IA2V_FOCUS_TOKENS)
    max_items = 3 if performance_focused else 2
    clauses: list[str] = []
    for clause, keywords in _IA2V_POLICY_KEYWORDS:
        if any(keyword in merged for keyword in keywords):
            continue
        clauses.append(clause)
        if len(clauses) >= max_items:
            break
    return clauses


def clean_negative_prompt_artifacts(text: str) -> str:
    clean = " ".join(str(text or "").split()).strip(" ,;")
    if not clean:
        return clean
    out = clean
    for pattern, replacement in NEGATIVE_PROMPT_ARTIFACT_PATTERNS:
        out = pattern.sub(replacement, out)
    out = re.sub(r"\s*,\s*", ", ", out)
    out = re.sub(r"\s{2,}", " ", out).strip(" ,;")
    return out
