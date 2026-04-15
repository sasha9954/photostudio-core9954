from __future__ import annotations

from typing import Any


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_note(value: Any, *, max_len: int = 280) -> str:
    text = " ".join(str(value or "").strip().split())
    return text[:max_len]


def story_guidance_to_notes_list(raw_story_guidance: Any, *, max_items: int = 8) -> list[str]:
    """Normalize story_core.story_guidance into deterministic short continuity notes."""
    if isinstance(raw_story_guidance, list):
        out = [_normalize_note(item) for item in raw_story_guidance]
        return [item for item in out if item][:max_items]

    if isinstance(raw_story_guidance, dict):
        row = _safe_dict(raw_story_guidance)
        notes: list[str] = []

        for key in (
            "continuity_notes",
            "notes",
            "guidance_notes",
            "summary_notes",
            "story_notes",
            "summary",
            "guidance_summary",
        ):
            value = row.get(key)
            if isinstance(value, list):
                notes.extend([_normalize_note(item) for item in value])
            elif isinstance(value, str):
                notes.append(_normalize_note(value))

        if not notes:
            for key, value in row.items():
                if key == "route_mix_doctrine_for_scenes":
                    continue
                if isinstance(value, str):
                    notes.append(_normalize_note(value))
                elif isinstance(value, list):
                    notes.extend([_normalize_note(item) for item in value if isinstance(item, (str, int, float))])

        out = [item for item in notes if item]
        return out[:max_items]

    return []


def story_guidance_route_mix_doctrine(raw_story_guidance: Any) -> dict[str, Any]:
    guidance = _safe_dict(raw_story_guidance)
    return _safe_dict(guidance.get("route_mix_doctrine_for_scenes"))
