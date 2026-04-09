from __future__ import annotations

import os
import re
from typing import Any

"""
Transcript alignment resolver for audio_map.

Environment overrides (all optional):
- AUDIO_MAP_ASR_MODEL: faster-whisper model name. Default: "tiny".
- AUDIO_MAP_ASR_COMPUTE_TYPE: faster-whisper compute type. Default: "int8".
- AUDIO_MAP_ASR_DEVICE: execution device. Default: "auto".
"""

DEFAULT_ASR_MODEL = "tiny"
DEFAULT_ASR_COMPUTE_TYPE = "int8"
DEFAULT_ASR_DEVICE = "auto"


def _clean_token(token: str) -> str:
    text = str(token or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def _normalize_word_rows(words: list[dict[str, Any]], duration_sec: float) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for row in words:
        if not isinstance(row, dict):
            continue
        text = _clean_token(str(row.get("text") or row.get("word") or row.get("token") or ""))
        if not text:
            continue
        try:
            t0 = float(row.get("t0") if row.get("t0") is not None else row.get("start"))
            t1 = float(row.get("t1") if row.get("t1") is not None else row.get("end"))
        except Exception:
            continue
        if t1 <= t0:
            continue
        t0 = max(0.0, min(duration_sec, t0))
        t1 = max(0.0, min(duration_sec, t1))
        if t1 <= t0:
            continue
        normalized.append({"text": text, "t0": round(t0, 3), "t1": round(t1, 3)})
    return normalized


def _build_phrases_from_words(words: list[dict[str, Any]], duration_sec: float) -> list[dict[str, Any]]:
    if not words:
        return []
    phrases: list[dict[str, Any]] = []
    current_words: list[dict[str, Any]] = []
    for idx, word in enumerate(words):
        current_words.append(word)
        text = str(word.get("text") or "")
        pause_after = 0.0
        if idx + 1 < len(words):
            pause_after = float(words[idx + 1].get("t0") or 0.0) - float(word.get("t1") or 0.0)
        is_terminal = bool(re.search(r"[.!?…:;]$", text))
        phrase_dur = float(current_words[-1].get("t1") or 0.0) - float(current_words[0].get("t0") or 0.0)
        should_break = is_terminal or pause_after >= 0.45 or phrase_dur >= 6.5
        if should_break:
            t0 = max(0.0, min(duration_sec, float(current_words[0].get("t0") or 0.0)))
            t1 = max(0.0, min(duration_sec, float(current_words[-1].get("t1") or 0.0)))
            phrase_text = " ".join(str(item.get("text") or "").strip() for item in current_words).strip()
            if phrase_text and t1 > t0:
                phrases.append({"text": phrase_text, "t0": round(t0, 3), "t1": round(t1, 3)})
            current_words = []
    if current_words:
        t0 = max(0.0, min(duration_sec, float(current_words[0].get("t0") or 0.0)))
        t1 = max(0.0, min(duration_sec, float(current_words[-1].get("t1") or 0.0)))
        phrase_text = " ".join(str(item.get("text") or "").strip() for item in current_words).strip()
        if phrase_text and t1 > t0:
            phrases.append({"text": phrase_text, "t0": round(t0, 3), "t1": round(t1, 3)})
    return phrases


def _whisper_word_alignment(audio_path: str, *, transcript_hint: str = "") -> dict[str, Any] | None:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except Exception:
        return None

    # Keep defaults lightweight/safe for local/dev setups and allow explicit env overrides.
    model_name = os.getenv("AUDIO_MAP_ASR_MODEL", DEFAULT_ASR_MODEL)
    compute_type = os.getenv("AUDIO_MAP_ASR_COMPUTE_TYPE", DEFAULT_ASR_COMPUTE_TYPE)
    device = os.getenv("AUDIO_MAP_ASR_DEVICE", DEFAULT_ASR_DEVICE)

    model = WhisperModel(model_name, device=device, compute_type=compute_type)
    segments, _info = model.transcribe(
        audio_path,
        vad_filter=True,
        word_timestamps=True,
        beam_size=1,
        best_of=1,
        initial_prompt=transcript_hint[:500] if transcript_hint else None,
    )

    words: list[dict[str, Any]] = []
    phrases: list[dict[str, Any]] = []
    transcript_chunks: list[str] = []
    for segment in segments:
        seg_text = _clean_token(str(getattr(segment, "text", "") or ""))
        seg_start = float(getattr(segment, "start", 0.0) or 0.0)
        seg_end = float(getattr(segment, "end", seg_start) or seg_start)
        seg_words = []
        for w in getattr(segment, "words", None) or []:
            token = _clean_token(str(getattr(w, "word", "") or ""))
            start = float(getattr(w, "start", 0.0) or 0.0)
            end = float(getattr(w, "end", start) or start)
            if not token or end <= start:
                continue
            word_row = {"text": token, "t0": round(start, 3), "t1": round(end, 3)}
            words.append(word_row)
            seg_words.append(word_row)

        if seg_text:
            transcript_chunks.append(seg_text)
        if seg_words:
            phrases.append(
                {
                    "text": " ".join(str(item["text"]) for item in seg_words).strip(),
                    "t0": round(float(seg_words[0]["t0"]), 3),
                    "t1": round(float(seg_words[-1]["t1"]), 3),
                }
            )
        elif seg_text and seg_end > seg_start:
            phrases.append({"text": seg_text, "t0": round(seg_start, 3), "t1": round(seg_end, 3)})

    transcript_text = " ".join(transcript_chunks).strip()
    if not words:
        return None

    return {
        "transcript_text": transcript_text,
        "words": words,
        "phrases": phrases,
        "source": "faster_whisper",
        "mode": "transcript_alignment_v2",
    }


def resolve_transcript_alignment(
    *,
    audio_path: str,
    duration_sec: float,
    transcript_hint: str = "",
    provided_alignment: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Resolve transcript+word alignment for audio_map.

    Priority:
    1) externally provided alignment with word timestamps
    2) ASR backend with real word timestamps

    ASR env knobs:
    - AUDIO_MAP_ASR_MODEL
    - AUDIO_MAP_ASR_COMPUTE_TYPE
    - AUDIO_MAP_ASR_DEVICE
    """
    provided = provided_alignment if isinstance(provided_alignment, dict) else {}
    provided_words = _normalize_word_rows(
        provided.get("words") if isinstance(provided.get("words"), list) else [],
        duration_sec,
    )
    if provided_words:
        transcript_text = _clean_token(str(provided.get("transcript_text") or provided.get("transcriptText") or ""))
        phrases = provided.get("phrases") if isinstance(provided.get("phrases"), list) else []
        normalized_phrases = _normalize_word_rows(phrases, duration_sec)
        phrase_rows = (
            [{"text": row["text"], "t0": row["t0"], "t1": row["t1"]} for row in normalized_phrases]
            if normalized_phrases
            else _build_phrases_from_words(provided_words, duration_sec)
        )
        if not transcript_text:
            transcript_text = " ".join(str(row.get("text") or "") for row in provided_words).strip()
        return {
            "transcript_text": transcript_text,
            "words": provided_words,
            "phrases": phrase_rows,
            "source": str(provided.get("source") or "provided_alignment_words"),
            "mode": "transcript_alignment_v2",
        }

    whisper_alignment = _whisper_word_alignment(audio_path, transcript_hint=transcript_hint)
    if whisper_alignment:
        normalized_words = _normalize_word_rows(whisper_alignment.get("words") or [], duration_sec)
        if not normalized_words:
            return None
        normalized_phrases = _normalize_word_rows(whisper_alignment.get("phrases") or [], duration_sec)
        phrase_rows = (
            [{"text": row["text"], "t0": row["t0"], "t1": row["t1"]} for row in normalized_phrases]
            if normalized_phrases
            else _build_phrases_from_words(normalized_words, duration_sec)
        )
        return {
            "transcript_text": _clean_token(str(whisper_alignment.get("transcript_text") or "")),
            "words": normalized_words,
            "phrases": phrase_rows,
            "source": str(whisper_alignment.get("source") or "faster_whisper"),
            "mode": "transcript_alignment_v2",
        }

    return None
