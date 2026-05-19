from __future__ import annotations

from dataclasses import dataclass
import logging
import os
import re
import time
from typing import Any

from pydub import AudioSegment


_WORD_CLEAN_RE = re.compile(r"\s+")
_SENTENCE_PUNCT_RE = re.compile(r"[.!?…:;]+[\"')\]]*$")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ManualTimingAsrSettings:
    language: str = "en"
    split_mode: str = "pause_based"
    min_pause_sec: float = 0.45
    max_phrase_sec: float = 8.0
    min_phrase_sec: float = 1.2
    padding_sec: float = 0.0
    model_size: str = "small"
    split_on_punctuation: bool = True
    split_by_long_gap: bool = True
    split_by_max_duration: bool = True


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        n = float(value)
    except Exception:
        return float(default)
    if not (n == n) or n in (float("inf"), float("-inf")):
        return float(default)
    return float(n)


def _round_sec(value: Any) -> float:
    return round(max(0.0, _safe_float(value)), 3)


def _clamp_settings(settings: ManualTimingAsrSettings) -> ManualTimingAsrSettings:
    min_pause = max(0.05, min(3.0, _safe_float(settings.min_pause_sec, 0.45)))
    max_phrase = max(1.0, min(30.0, _safe_float(settings.max_phrase_sec, 8.0)))
    min_phrase = max(0.1, min(max_phrase, _safe_float(settings.min_phrase_sec, 1.2)))
    padding = max(0.0, min(0.15, _safe_float(settings.padding_sec, 0.0)))
    language = (settings.language or "en").strip().lower() or "en"
    split_mode = (settings.split_mode or "pause_based").strip().lower() or "pause_based"
    if split_mode in {"song_lines", "short_phrases"}:
        min_pause = 0.26 if split_mode == "song_lines" else 0.22
        max_phrase = 3.6 if split_mode == "song_lines" else 3.0
        min_phrase = 0.6 if split_mode == "song_lines" else 0.5
    model_size = (settings.model_size or os.getenv("MANUAL_TIMING_ASR_MODEL") or "small").strip() or "small"
    return ManualTimingAsrSettings(
        language=language,
        split_mode=split_mode,
        min_pause_sec=min_pause,
        max_phrase_sec=max_phrase,
        min_phrase_sec=min_phrase,
        padding_sec=padding,
        model_size=model_size,
        split_on_punctuation=bool(getattr(settings, "split_on_punctuation", True)),
        split_by_long_gap=bool(getattr(settings, "split_by_long_gap", True)),
        split_by_max_duration=bool(getattr(settings, "split_by_max_duration", True)),
    )


def get_audio_duration_sec(audio_path: str) -> float:
    segment = AudioSegment.from_file(audio_path)
    return _round_sec(len(segment) / 1000.0)


def _audio_rms_for_range(audio: AudioSegment, start_sec: float, end_sec: float) -> float:
    start_ms = max(0, int(round(_safe_float(start_sec, 0.0) * 1000.0)))
    end_ms = max(start_ms + 1, int(round(_safe_float(end_sec, start_sec) * 1000.0)))
    chunk = audio[start_ms:end_ms]
    return _safe_float(getattr(chunk, "rms", 0.0), 0.0)


def _normalize_word(raw: Any, idx: int) -> dict[str, Any] | None:
    text = _WORD_CLEAN_RE.sub(" ", str(getattr(raw, "word", "") or "")).strip()
    start = _safe_float(getattr(raw, "start", None), -1.0)
    end = _safe_float(getattr(raw, "end", None), -1.0)
    if not text or start < 0 or end <= start:
        return None
    probability = getattr(raw, "probability", None)
    confidence = _safe_float(probability, 0.0)
    return {
        "word": text,
        "start_sec": _round_sec(start),
        "end_sec": _round_sec(end),
        "confidence": round(max(0.0, min(1.0, confidence)), 4),
        "_idx": idx,
    }


def _cuda_available() -> bool:
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:
        try:
            import torch
            return bool(torch.cuda.is_available())
        except Exception:
            return False


def transcribe_words_faster_whisper(audio_path: str, settings: ManualTimingAsrSettings) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    # Lazy import keeps the API bootable in environments where ASR deps/models are not loaded yet.
    from faster_whisper import WhisperModel

    safe = _clamp_settings(settings)
    requested_device = (os.getenv("MANUAL_TIMING_ASR_DEVICE") or "cpu").strip().lower() or "cpu"
    device = requested_device
    if requested_device == "cuda" and not _cuda_available():
        if (os.getenv("MANUAL_TIMING_ASR_FALLBACK_CPU") or "").strip().lower() in {"1", "true", "yes", "on"}:
            logger.warning("Manual Timing ASR requested CUDA but CUDA is unavailable; falling back to CPU")
            device = "cpu"
        else:
            raise RuntimeError("MANUAL_TIMING_ASR_DEVICE=cuda, но CUDA недоступна для backend. Проверьте GPU/драйверы/CUDA или включите MANUAL_TIMING_ASR_FALLBACK_CPU=true для fallback на CPU.")
    compute_type = (os.getenv("MANUAL_TIMING_ASR_COMPUTE_TYPE") or ("int8" if device == "cpu" else "float16")).strip()
    started_at = time.monotonic()
    logger.info(
        "Manual Timing ASR starting: model=%s device=%s compute_type=%s audio_path=%s",
        safe.model_size,
        device,
        compute_type,
        audio_path,
    )
    model = WhisperModel(safe.model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        audio_path,
        language=safe.language,
        word_timestamps=True,
        vad_filter=True,
        beam_size=int(_safe_float(os.getenv("MANUAL_TIMING_ASR_BEAM_SIZE") or 5, 5)),
    )

    words: list[dict[str, Any]] = []
    for segment in segments:
        for raw_word in list(getattr(segment, "words", None) or []):
            normalized = _normalize_word(raw_word, len(words))
            if normalized:
                words.append(normalized)

    words.sort(key=lambda item: (float(item["start_sec"]), float(item["end_sec"])))
    for item in words:
        item.pop("_idx", None)
    duration_sec = round(time.monotonic() - started_at, 3)
    metadata = {
        "backend": "faster-whisper",
        "model_size": safe.model_size,
        "requested_device": requested_device,
        "device": device,
        "compute_type": compute_type,
        "duration_sec": duration_sec,
        "language": getattr(info, "language", safe.language),
        "language_probability": _safe_float(getattr(info, "language_probability", 0.0), 0.0),
    }
    logger.info(
        "Manual Timing ASR finished: model=%s device=%s compute_type=%s duration=%.3fs word_count=%s",
        safe.model_size,
        device,
        compute_type,
        duration_sec,
        len(words),
    )
    return words, metadata


def _phrase_text(words: list[dict[str, Any]]) -> str:
    return _WORD_CLEAN_RE.sub(" ", " ".join(str(word.get("word") or "").strip() for word in words)).strip()


def _word_confidence(words: list[dict[str, Any]]) -> float:
    values = [_safe_float(word.get("confidence"), 0.0) for word in words]
    values = [value for value in values if value > 0]
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _is_punctuation_boundary(word: dict[str, Any]) -> bool:
    return bool(_SENTENCE_PUNCT_RE.search(str(word.get("word") or "").strip()))


def split_words_to_phrases(words: list[dict[str, Any]], settings: ManualTimingAsrSettings, *, audio_duration_sec: float = 0.0) -> list[dict[str, Any]]:
    safe = _clamp_settings(settings)
    ordered = sorted(
        [word for word in words if _safe_float(word.get("end_sec"), 0.0) > _safe_float(word.get("start_sec"), 0.0)],
        key=lambda item: (_safe_float(item.get("start_sec"), 0.0), _safe_float(item.get("end_sec"), 0.0)),
    )
    phrases: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []

    def close_current() -> None:
        nonlocal current
        if current:
            phrases.append(current)
            current = []

    for word in ordered:
        if not current:
            current = [word]
            continue

        prev = current[-1]
        gap = _safe_float(word.get("start_sec"), 0.0) - _safe_float(prev.get("end_sec"), 0.0)
        current_duration_if_added = _safe_float(word.get("end_sec"), 0.0) - _safe_float(current[0].get("start_sec"), 0.0)
        current_duration = _safe_float(prev.get("end_sec"), 0.0) - _safe_float(current[0].get("start_sec"), 0.0)

        pause_boundary = gap >= safe.min_pause_sec and current_duration >= safe.min_phrase_sec
        punctuation_boundary = safe.split_on_punctuation and current_duration >= safe.min_phrase_sec and _is_punctuation_boundary(prev)
        max_boundary = current_duration_if_added > safe.max_phrase_sec and (punctuation_boundary or current_duration >= safe.max_phrase_sec * 0.85)

        if ((safe.split_by_long_gap and pause_boundary) or punctuation_boundary or (safe.split_by_max_duration and max_boundary)):
            close_current()
            current = [word]
        else:
            current.append(word)

    close_current()

    # Merge very short phrase fragments into a neighbor when it can be done without exceeding max too much.
    merged: list[list[dict[str, Any]]] = []
    for phrase in phrases:
        duration = _safe_float(phrase[-1].get("end_sec"), 0.0) - _safe_float(phrase[0].get("start_sec"), 0.0)
        if merged and duration < safe.min_phrase_sec:
            gap_to_previous = _safe_float(phrase[0].get("start_sec"), 0.0) - _safe_float(merged[-1][-1].get("end_sec"), 0.0)
            candidate_duration = _safe_float(phrase[-1].get("end_sec"), 0.0) - _safe_float(merged[-1][0].get("start_sec"), 0.0)
            if gap_to_previous < safe.min_pause_sec and candidate_duration <= safe.max_phrase_sec * 1.25:
                merged[-1].extend(phrase)
                continue
        merged.append(phrase)

    duration_limit = max(0.0, _safe_float(audio_duration_sec, 0.0))
    result: list[dict[str, Any]] = []
    for idx, phrase_words in enumerate(merged, start=1):
        start = _safe_float(phrase_words[0].get("start_sec"), 0.0) - safe.padding_sec
        end = _safe_float(phrase_words[-1].get("end_sec"), 0.0) + safe.padding_sec
        if duration_limit > 0:
            start = max(0.0, min(duration_limit, start))
            end = max(0.0, min(duration_limit, end))
        if end <= start:
            continue
        result.append({
            "phrase_id": f"phr_{idx:03d}",
            "start_sec": _round_sec(start),
            "end_sec": _round_sec(end),
            "text_en": _phrase_text(phrase_words),
            "text_ru": "",
            "meaning_ru": "",
            "status": "asr_raw",
            "confidence": _word_confidence(phrase_words),
        })
    if safe.max_phrase_sec > 0:
        result = _split_long_phrases(result, ordered, safe, duration_limit)
    return result


def _split_long_phrases(
    phrases: list[dict[str, Any]],
    ordered_words: list[dict[str, Any]],
    safe: ManualTimingAsrSettings,
    duration_limit: float,
) -> list[dict[str, Any]]:
    refined_word_groups: list[list[dict[str, Any]]] = []
    max_duration = min(4.0, safe.max_phrase_sec)
    for phrase in phrases:
        start = _safe_float(phrase.get("start_sec"), 0.0)
        end = _safe_float(phrase.get("end_sec"), start)
        duration = end - start
        phrase_words = [w for w in ordered_words if _safe_float(w.get("start_sec"), 0.0) >= start - 0.001 and _safe_float(w.get("end_sec"), 0.0) <= end + 0.001]
        if duration <= max_duration or len(phrase_words) < 2:
            refined_word_groups.append(phrase_words if phrase_words else [])
            continue
        current: list[dict[str, Any]] = []
        for word in phrase_words:
            if not current:
                current = [word]
                continue
            prev = current[-1]
            gap = _safe_float(word.get("start_sec"), 0.0) - _safe_float(prev.get("end_sec"), 0.0)
            seg_duration_if_added = _safe_float(word.get("end_sec"), 0.0) - _safe_float(current[0].get("start_sec"), 0.0)
            should_split = (gap >= safe.min_pause_sec and seg_duration_if_added >= safe.min_phrase_sec) or seg_duration_if_added > safe.max_phrase_sec
            if should_split:
                refined_word_groups.append(current)
                current = [word]
            else:
                current.append(word)
        if current:
            refined_word_groups.append(current)
    result: list[dict[str, Any]] = []
    for idx, phrase_words in enumerate([g for g in refined_word_groups if g], start=1):
        start = _safe_float(phrase_words[0].get("start_sec"), 0.0) - safe.padding_sec
        end = _safe_float(phrase_words[-1].get("end_sec"), 0.0) + safe.padding_sec
        if duration_limit > 0:
            start = max(0.0, min(duration_limit, start))
            end = max(0.0, min(duration_limit, end))
        if end <= start:
            continue
        result.append({
            "phrase_id": f"phr_{idx:03d}",
            "start_sec": _round_sec(start),
            "end_sec": _round_sec(end),
            "text_en": _phrase_text(phrase_words),
            "text_ru": "",
            "meaning_ru": "",
            "status": "asr_raw",
            "confidence": _word_confidence(phrase_words),
        })
    return result


def build_manual_timing_audio_phrase_map(audio_path: str, settings: ManualTimingAsrSettings) -> dict[str, Any]:
    safe = _clamp_settings(settings)
    duration = get_audio_duration_sec(audio_path)
    words, metadata = transcribe_words_faster_whisper(audio_path, safe)
    phrases = split_words_to_phrases(words, safe, audio_duration_sec=duration)
    asr_gaps = _detect_unrecognized_vocal_gaps(audio_path, phrases, duration)
    metadata["phrase_count"] = len(phrases)
    metadata["word_count"] = len(words)
    metadata["gap_count"] = len(asr_gaps)
    logger.info(
        "Manual Timing ASR phrase map: model=%s device=%s compute_type=%s duration=%.3fs phrase_count=%s word_count=%s",
        metadata.get("model_size"),
        metadata.get("device"),
        metadata.get("compute_type"),
        _safe_float(metadata.get("duration_sec"), 0.0),
        len(phrases),
        len(words),
    )
    return {
        "ok": True,
        "audio_duration_sec": duration,
        "words": words,
        "audio_phrases": phrases,
        "asr_gaps": asr_gaps,
        "asr": metadata,
        "split_settings": {
            "split_mode": safe.split_mode,
            "min_pause_sec": safe.min_pause_sec,
            "max_phrase_sec": safe.max_phrase_sec,
            "min_phrase_sec": safe.min_phrase_sec,
            "padding_sec": safe.padding_sec,
            "split_on_punctuation": safe.split_on_punctuation,
        },
    }


def _detect_unrecognized_vocal_gaps(
    audio_path: str,
    phrases: list[dict[str, Any]],
    audio_duration_sec: float,
    *,
    min_gap_sec: float = 0.8,
) -> list[dict[str, Any]]:
    ordered = sorted(
        [p for p in phrases if _safe_float(p.get("end_sec"), 0.0) > _safe_float(p.get("start_sec"), 0.0)],
        key=lambda item: (_safe_float(item.get("start_sec"), 0.0), _safe_float(item.get("end_sec"), 0.0)),
    )
    if len(ordered) < 2:
        return []
    audio = AudioSegment.from_file(audio_path)
    silence_rms_threshold = max(20.0, _safe_float(getattr(audio, "rms", 0.0), 0.0) * 0.18)
    gaps: list[dict[str, Any]] = []
    for idx in range(1, len(ordered)):
        prev_end = _safe_float(ordered[idx - 1].get("end_sec"), 0.0)
        next_start = _safe_float(ordered[idx].get("start_sec"), 0.0)
        gap_duration = next_start - prev_end
        if gap_duration <= min_gap_sec:
            continue
        start_sec = max(0.0, min(_safe_float(audio_duration_sec, next_start), prev_end))
        end_sec = max(start_sec, min(_safe_float(audio_duration_sec, next_start), next_start))
        if end_sec - start_sec <= min_gap_sec:
            continue
        gap_rms = _audio_rms_for_range(audio, start_sec, end_sec)
        if gap_rms <= silence_rms_threshold:
            continue
        gaps.append({
            "gap_id": f"gap_{len(gaps) + 1:03d}",
            "start_sec": _round_sec(start_sec),
            "end_sec": _round_sec(end_sec),
            "duration_sec": _round_sec(end_sec - start_sec),
            "type": "unrecognized_vocal",
            "note": "Vocal audio present but ASR produced no words",
            "rms": round(gap_rms, 3),
        })
    return gaps
