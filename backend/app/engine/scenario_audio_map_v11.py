from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

RHYTHMIC_ANCHORS = ("beat", "drop", "transition", "none")
VOCAL_GENDERS = ("female", "male", "mixed", "unknown")
VOCAL_OWNER_ROLES = ("character_1", "character_2", "character_3", "unknown")

ERROR_AUDIO_EMPTY_MAP = "AUDIO_EMPTY_MAP"
ERROR_AUDIO_GAP = "AUDIO_GAP_ERROR"
ERROR_AUDIO_OVERLAP = "AUDIO_OVERLAP_ERROR"
ERROR_AUDIO_TIMING = "AUDIO_TIMING_VIOLATION"
ERROR_AUDIO_PLOT_LEAKAGE = "AUDIO_PLOT_LEAKAGE"
ERROR_AUDIO_NO_SPLIT_CONFLICT = "AUDIO_NO_SPLIT_CONFLICT"
ERROR_AUDIO_SCHEMA_INVALID = "AUDIO_SCHEMA_INVALID"
ERROR_AUDIO_MAP_INVALID_SHORT_SEGMENT = "AUDIO_MAP_INVALID_SHORT_SEGMENT"
ERROR_AUDIO_MAP_INVALID_FIRST_LAST_DURATION = "AUDIO_MAP_INVALID_FIRST_LAST_DURATION"
ERROR_AUDIO_MAP_INVALID_TIMELINE = "AUDIO_MAP_INVALID_TIMELINE"


class AudioSegmentV11(BaseModel):
    segment_id: str
    t0: float
    t1: float
    duration_sec: float
    transcript_slice: str
    intensity: float = Field(ge=0.0, le=1.0)
    is_lip_sync_candidate: bool
    rhythmic_anchor: Literal["beat", "drop", "transition", "none"]
    first_last_candidate: bool = False
    route_hints: dict[str, Literal["good", "ok", "too_short", "too_long"]] | None = None


class PhraseUnitV11(BaseModel):
    id: str | None = None
    t0: float
    t1: float
    duration_sec: float | None = None
    text: str | None = None
    transcript_slice: str | None = None
    intensity: float | None = Field(default=None, ge=0.0, le=1.0)


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


class VocalProfileV11(BaseModel):
    vocal_gender: Literal["female", "male", "mixed", "unknown"] = "unknown"
    vocal_owner_role: Literal["character_1", "character_2", "character_3", "unknown"] = "unknown"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""


class AudioMapV11(BaseModel):
    audio_map_version: Literal["1.1"]
    audio_id: str
    vocal_profile: VocalProfileV11 = Field(default_factory=VocalProfileV11)
    vocal_gender: Literal["female", "male", "mixed", "unknown"] = "unknown"
    vocal_owner_role: Literal["character_1", "character_2", "character_3", "unknown"] = "unknown"
    vocal_owner_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    segments: list[AudioSegmentV11]
    phrase_units: list[PhraseUnitV11] = Field(default_factory=list)
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
_SEGMENT_ID_CANON_PATTERN = re.compile(r"^seg_\d{2}$")


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


def _has_natural_tail_hint(transcript_slice: str) -> bool:
    text = str(transcript_slice or "").strip().lower()
    if not text:
        return False
    tail_tokens = ("...", "—", "-", ",", ".", "!", "?", "breath", "pause", "sigh", "hmm", "mm")
    return any(token in text for token in tail_tokens)


def _has_compact_i2v_evidence(seg: AudioSegmentV11) -> bool:
    if seg.first_last_candidate:
        return False
    if float(seg.duration_sec or 0.0) < 2.7:
        return False
    route_hints = seg.route_hints or {}
    i2v_fit = str(route_hints.get("i2v_fit") or "").strip().lower()
    lip_sync_fit = str(route_hints.get("lip_sync_fit") or "").strip().lower()
    return i2v_fit in {"good", "ok"} and lip_sync_fit in {"", "too_short", "ok"}


def validate_audio_map_v11(payload: dict[str, Any], *, audio_duration_sec: float) -> AudioMapValidationResult:
    errors: list[str] = []
    normalized: dict[str, Any] = {}
    try:
        model = AudioMapV11.model_validate(payload)
    except ValidationError as exc:
        return AudioMapValidationResult(False, ERROR_AUDIO_SCHEMA_INVALID, "schema validation failed", [str(exc)], normalized)

    normalized = model.model_dump(mode="python")
    if isinstance(normalized, dict):
        vocal_profile = normalized.setdefault("vocal_profile", {}) if isinstance(normalized.get("vocal_profile"), dict) else {}
        profile_gender = str(vocal_profile.get("vocal_gender") or "").strip().lower()
        profile_owner = str(vocal_profile.get("vocal_owner_role") or "").strip()
        profile_confidence = float(vocal_profile.get("confidence") or 0.0)
        normalized["vocal_gender"] = (
            str(normalized.get("vocal_gender") or "").strip().lower()
            if str(normalized.get("vocal_gender") or "").strip().lower() in VOCAL_GENDERS
            else (profile_gender if profile_gender in VOCAL_GENDERS else "unknown")
        )
        normalized["vocal_owner_role"] = (
            str(normalized.get("vocal_owner_role") or "").strip()
            if str(normalized.get("vocal_owner_role") or "").strip() in VOCAL_OWNER_ROLES
            else (profile_owner if profile_owner in VOCAL_OWNER_ROLES else "unknown")
        )
        try:
            top_conf = float(normalized.get("vocal_owner_confidence") or 0.0)
        except Exception:
            top_conf = 0.0
        normalized["vocal_owner_confidence"] = max(0.0, min(1.0, top_conf if top_conf > 0.0 else profile_confidence))
        vocal_profile["vocal_gender"] = normalized["vocal_gender"]
        vocal_profile["vocal_owner_role"] = normalized["vocal_owner_role"]
        vocal_profile["confidence"] = normalized["vocal_owner_confidence"]
        vocal_profile["reason"] = str(vocal_profile.get("reason") or "")
    segments = model.segments
    duration = max(0.0, float(audio_duration_sec or 0.0))
    gap_tolerance = 0.12
    boundary_tolerance = 0.12
    overlap_tolerance = 0.001
    boundary_lock_tolerance = 0.08

    if not segments:
        return AudioMapValidationResult(False, ERROR_AUDIO_EMPTY_MAP, "segments are required", ["segments empty"], normalized)

    prev_t1: float | None = None
    gap_sum_sec = 0.0
    overlap_sum_sec = 0.0
    for idx, seg in enumerate(segments):
        if not _SEGMENT_ID_CANON_PATTERN.fullmatch(str(seg.segment_id or "").strip()):
            errors.append(_fmt(idx, f"segment_id must match canonical format seg_01..seg_99; got {seg.segment_id!r}"))
        if seg.t0 < 0.0 or seg.t1 < 0.0:
            errors.append(_fmt(idx, "negative timestamps are forbidden"))
        if seg.t0 >= seg.t1:
            errors.append(_fmt(idx, "t0 must be strictly less than t1"))
        if seg.duration_sec < 2.5:
            errors.append(_fmt(idx, f"duration_sec too short for standalone video segment (<2.5s): {seg.duration_sec:.3f}"))
        elif seg.duration_sec < 2.8 and not (_has_natural_tail_hint(seg.transcript_slice) or _has_compact_i2v_evidence(seg)):
            errors.append(_fmt(idx, f"duration_sec <2.8s without natural tail/reaction evidence: {seg.duration_sec:.3f}"))
        if seg.first_last_candidate and seg.duration_sec < 4.0:
            errors.append(_fmt(idx, f"first_last_candidate requires duration_sec >= 4.0; got {seg.duration_sec:.3f}"))
        expected_duration = max(0.0, seg.t1 - seg.t0)
        if abs(expected_duration - seg.duration_sec) > 0.12:
            errors.append(
                _fmt(idx, f"duration_sec mismatch; expected {expected_duration:.3f} from t1-t0, got {seg.duration_sec:.3f}")
            )
        if _is_placeholder_text(seg.transcript_slice):
            errors.append(_fmt(idx, "transcript_slice placeholder/empty is forbidden"))
        if prev_t1 is not None:
            if seg.t0 + overlap_tolerance < prev_t1:
                overlap_sum_sec += max(0.0, prev_t1 - seg.t0)
                return AudioMapValidationResult(False, ERROR_AUDIO_MAP_INVALID_TIMELINE, "segment overlap detected", [_fmt(idx, f"overlap with prev ending at {prev_t1:.3f}")], normalized)
            gap = seg.t0 - prev_t1
            if gap > 0.0:
                gap_sum_sec += gap
            if gap > gap_tolerance:
                return AudioMapValidationResult(False, ERROR_AUDIO_MAP_INVALID_TIMELINE, "segment gap exceeds tolerance", [_fmt(idx, f"gap={gap:.3f}s")], normalized)
        prev_t1 = seg.t1

    t0s = [row.t0 for row in segments]
    if any(t0s[i] > t0s[i + 1] + overlap_tolerance for i in range(len(t0s) - 1)):
        return AudioMapValidationResult(False, ERROR_AUDIO_MAP_INVALID_TIMELINE, "segments must be sorted by t0", ["segments not sorted"], normalized)

    if errors:
        has_short_segment_error = any("too short for standalone video segment" in msg or "<2.8s without natural tail/reaction evidence" in msg for msg in errors)
        has_first_last_error = any("first_last_candidate requires duration_sec >= 4.0" in msg for msg in errors)
        if has_short_segment_error:
            return AudioMapValidationResult(False, ERROR_AUDIO_MAP_INVALID_SHORT_SEGMENT, "video-ready short segment violation", errors, normalized)
        if has_first_last_error:
            return AudioMapValidationResult(False, ERROR_AUDIO_MAP_INVALID_FIRST_LAST_DURATION, "first_last duration violation", errors, normalized)
        return AudioMapValidationResult(False, ERROR_AUDIO_MAP_INVALID_TIMELINE, "segment timing/fields invalid", errors, normalized)

    first_t0 = segments[0].t0
    if first_t0 > boundary_tolerance:
        return AudioMapValidationResult(False, ERROR_AUDIO_MAP_INVALID_TIMELINE, "start coverage gap exceeds tolerance", [f"first_t0={first_t0:.3f}"], normalized)

    last_t1 = segments[-1].t1
    if duration > 0.0 and (duration - last_t1) > boundary_tolerance:
        return AudioMapValidationResult(False, ERROR_AUDIO_MAP_INVALID_TIMELINE, "end coverage gap exceeds tolerance", [f"last_t1={last_t1:.3f}, duration={duration:.3f}"], normalized)
    if duration > 0.0 and last_t1 > duration + boundary_tolerance:
        return AudioMapValidationResult(False, ERROR_AUDIO_MAP_INVALID_TIMELINE, "segment exceeds audio duration", [f"last_t1={last_t1:.3f}, duration={duration:.3f}"], normalized)

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

    normalized_diag = normalized.setdefault("diagnostics", {}) if isinstance(normalized, dict) else {}
    if isinstance(normalized_diag, dict):
        normalized_diag["gap_sum_sec"] = round(gap_sum_sec, 4)
        normalized_diag["overlap_sum_sec"] = round(overlap_sum_sec, 4)

    return AudioMapValidationResult(True, "", "", [], normalized)
