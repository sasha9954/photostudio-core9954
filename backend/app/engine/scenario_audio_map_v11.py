from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

RHYTHMIC_ANCHORS = ("beat", "drop", "transition", "none")

ERROR_AUDIO_EMPTY_MAP = "AUDIO_EMPTY_MAP"
ERROR_AUDIO_GAP = "AUDIO_GAP_ERROR"
ERROR_AUDIO_OVERLAP = "AUDIO_OVERLAP_ERROR"
ERROR_AUDIO_TIMING = "AUDIO_TIMING_VIOLATION"
ERROR_AUDIO_PLOT_LEAKAGE = "AUDIO_PLOT_LEAKAGE"
ERROR_AUDIO_NO_SPLIT_CONFLICT = "AUDIO_NO_SPLIT_CONFLICT"
ERROR_AUDIO_SCHEMA_INVALID = "AUDIO_SCHEMA_INVALID"


class AudioSegmentV11(BaseModel):
    segment_id: str
    t0: float
    t1: float
    transcript_slice: str
    intensity: float = Field(ge=0.0, le=1.0)
    is_lip_sync_candidate: bool
    rhythmic_anchor: Literal["beat", "drop", "transition", "none"]


class NoSplitRangeV11(BaseModel):
    start: float
    end: float


class AudioDiagnosticsV11(BaseModel):
    total_segments_duration: float
    coverage_ok: bool
    energy_peak_detected: bool
    transcript_used: bool
    dynamics_used: bool
    validation_notes: list[str] = Field(default_factory=list)


class AudioMapV11(BaseModel):
    audio_map_version: Literal["1.1"]
    audio_id: str
    segments: list[AudioSegmentV11]
    no_split_ranges: list[NoSplitRangeV11] = Field(default_factory=list)
    diagnostics: AudioDiagnosticsV11


@dataclass
class AudioMapValidationResult:
    ok: bool
    error_code: str
    message: str
    errors: list[str]
    normalized: dict[str, Any]


_PLACEHOLDER_TRANSCRIPT = {
    "",
    "...",
    "n/a",
    "na",
    "none",
    "null",
    "unknown",
    "placeholder",
    "tbd",
}

_PLOT_LEAKAGE_PATTERNS = [
    re.compile(r"\b(camera|close\s?up|wide\s?shot|pan|tilt|dolly|zoom|rack\s?focus|lens|frame)\b", re.IGNORECASE),
    re.compile(r"\b(scene|shot\s?list|storyboard|render|prompt|visual|lighting|composition)\b", re.IGNORECASE),
    re.compile(r"\b(hero|protagonist|character\s*[0-9]*|villain|antagonist|role\b)\b", re.IGNORECASE),
]


def _fmt(idx: int, msg: str) -> str:
    return f"segment[{idx}] {msg}"


def _is_placeholder_text(value: str) -> bool:
    text = str(value or "").strip().lower()
    return text in _PLACEHOLDER_TRANSCRIPT or bool(re.fullmatch(r"[\[\(]?\s*(lyrics|text|transcript)\s*[\]\)]?", text))


def _plot_leakage_hits(value: str) -> list[str]:
    hits: list[str] = []
    for pattern in _PLOT_LEAKAGE_PATTERNS:
        if pattern.search(str(value or "")):
            hits.append(pattern.pattern)
    return hits


def validate_audio_map_v11(payload: dict[str, Any], *, audio_duration_sec: float) -> AudioMapValidationResult:
    errors: list[str] = []
    normalized: dict[str, Any] = {}
    try:
        model = AudioMapV11.model_validate(payload)
    except ValidationError as exc:
        return AudioMapValidationResult(False, ERROR_AUDIO_SCHEMA_INVALID, "schema validation failed", [str(exc)], normalized)

    normalized = model.model_dump(mode="python")
    segments = model.segments
    duration = max(0.0, float(audio_duration_sec or 0.0))
    gap_tolerance = 0.35
    boundary_tolerance = 0.35
    overlap_tolerance = 0.001
    boundary_lock_tolerance = 0.08

    if not segments:
        return AudioMapValidationResult(False, ERROR_AUDIO_EMPTY_MAP, "segments are required", ["segments empty"], normalized)

    prev_t1: float | None = None
    for idx, seg in enumerate(segments):
        if seg.t0 < 0.0 or seg.t1 < 0.0:
            errors.append(_fmt(idx, "negative timestamps are forbidden"))
        if seg.t0 >= seg.t1:
            errors.append(_fmt(idx, "t0 must be strictly less than t1"))
        if _is_placeholder_text(seg.transcript_slice):
            errors.append(_fmt(idx, "transcript_slice placeholder/empty is forbidden"))
        if prev_t1 is not None:
            if seg.t0 + overlap_tolerance < prev_t1:
                return AudioMapValidationResult(False, ERROR_AUDIO_OVERLAP, "segment overlap detected", [_fmt(idx, f"overlap with prev ending at {prev_t1:.3f}")], normalized)
            gap = seg.t0 - prev_t1
            if gap > gap_tolerance:
                return AudioMapValidationResult(False, ERROR_AUDIO_GAP, "segment gap exceeds tolerance", [_fmt(idx, f"gap={gap:.3f}s")], normalized)
        prev_t1 = seg.t1

    t0s = [row.t0 for row in segments]
    if any(t0s[i] > t0s[i + 1] + overlap_tolerance for i in range(len(t0s) - 1)):
        return AudioMapValidationResult(False, ERROR_AUDIO_TIMING, "segments must be sorted by t0", ["segments not sorted"], normalized)

    if errors:
        return AudioMapValidationResult(False, ERROR_AUDIO_TIMING, "segment timing/fields invalid", errors, normalized)

    first_t0 = segments[0].t0
    if first_t0 > boundary_tolerance:
        return AudioMapValidationResult(False, ERROR_AUDIO_GAP, "start coverage gap exceeds tolerance", [f"first_t0={first_t0:.3f}"], normalized)

    last_t1 = segments[-1].t1
    if duration > 0.0 and (duration - last_t1) > boundary_tolerance:
        return AudioMapValidationResult(False, ERROR_AUDIO_GAP, "end coverage gap exceeds tolerance", [f"last_t1={last_t1:.3f}, duration={duration:.3f}"], normalized)
    if duration > 0.0 and last_t1 > duration + boundary_tolerance:
        return AudioMapValidationResult(False, ERROR_AUDIO_TIMING, "segment exceeds audio duration", [f"last_t1={last_t1:.3f}, duration={duration:.3f}"], normalized)

    for idx, row in enumerate(model.no_split_ranges):
        if row.start < 0.0 or row.end < 0.0 or row.start >= row.end:
            return AudioMapValidationResult(False, ERROR_AUDIO_NO_SPLIT_CONFLICT, "invalid no_split range", [f"no_split_ranges[{idx}] invalid"], normalized)
        for seg_idx, seg in enumerate(segments[1:-1], start=1):
            for boundary in (seg.t0, seg.t1):
                if (row.start + boundary_lock_tolerance) < boundary < (row.end - boundary_lock_tolerance):
                    return AudioMapValidationResult(
                        False,
                        ERROR_AUDIO_NO_SPLIT_CONFLICT,
                        "segment boundary conflicts with no_split range",
                        [f"boundary={boundary:.3f} inside no_split[{idx}]={row.start:.3f}..{row.end:.3f} at segment[{seg_idx}]"],
                        normalized,
                    )

    leakage_errors: list[str] = []
    for idx, seg in enumerate(segments):
        hits = _plot_leakage_hits(seg.transcript_slice)
        if hits:
            leakage_errors.append(_fmt(idx, f"plot leakage vocabulary detected: {seg.transcript_slice[:120]!r}"))
    if leakage_errors:
        return AudioMapValidationResult(False, ERROR_AUDIO_PLOT_LEAKAGE, "audio payload leaked scene/plot vocabulary", leakage_errors, normalized)

    return AudioMapValidationResult(True, "", "", [], normalized)
