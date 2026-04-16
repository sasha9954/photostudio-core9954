from __future__ import annotations

from typing import Any

# Immediate stabilization policy for heavy scenario stages.
# NOTE: This does not fully solve long-form generation (3-10 min inputs).
# True robustness for long-form should move heavy stages to chunked execution.
SCENARIO_STAGE_TIMEOUTS: dict[str, int] = {
    "story_core": 180,
    "role_plan": 180,
    "scene_plan": 180,
    "scene_prompts": 240,
    "final_video_prompt": 240,
}

# Extension point for future chunked execution orchestration.
SCENARIO_STAGE_CHUNKED_FUTURE_SUPPORT: dict[str, bool] = {
    "story_core": False,
    "role_plan": False,
    "scene_plan": True,
    "scene_prompts": True,
    "final_video_prompt": True,
}


def get_scenario_stage_timeout(stage_name: str, default: int = 90) -> int:
    return int(SCENARIO_STAGE_TIMEOUTS.get(str(stage_name or "").strip(), default))


def scenario_timeout_policy_name(stage_name: str) -> str:
    return f"scenario_stage_timeout:{str(stage_name or '').strip() or 'unknown'}"


def is_timeout_error(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return any(
        token in text
        for token in (
            "readtimeout",
            "read timeout",
            "timed out",
            "timeout",
            "time-out",
            "deadline exceeded",
        )
    )
