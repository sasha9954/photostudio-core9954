from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Dict, List

import librosa
import numpy as np
from scipy.signal import find_peaks


def _safe_float(value: float) -> float:
    return float(np.round(float(value), 4))


def _safe_scalar(value: object, default: float = 0.0) -> float:
    try:
        arr = np.asarray(value, dtype=float)
        if arr.size == 0:
            return float(default)
        return float(arr.reshape(-1)[0])
    except Exception:
        try:
            return float(value)  # type: ignore[arg-type]
        except Exception:
            return float(default)


def _merge_segments(segments: List[Dict[str, float]], max_gap: float = 0.25) -> List[Dict[str, float]]:
    if not segments:
        return []

    merged = [segments[0].copy()]
    for seg in segments[1:]:
        prev = merged[-1]
        if seg["start"] - prev["end"] <= max_gap:
            prev["end"] = max(prev["end"], seg["end"])
        else:
            merged.append(seg.copy())
    return merged


def _normalize(arr: np.ndarray) -> np.ndarray:
    if arr.size == 0:
        return arr
    arr = np.asarray(arr, dtype=float)
    span = np.max(arr) - np.min(arr)
    if span <= 1e-9:
        return np.zeros_like(arr)
    return (arr - np.min(arr)) / span


def _estimate_vocal_phrases(y: np.ndarray, sr: int) -> List[Dict[str, float]]:
    # Non-silent intervals are candidate phrase zones.
    intervals = librosa.effects.split(y, top_db=28)
    if len(intervals) == 0:
        return []

    n_fft = 2048
    hop = 512
    spec = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop)) ** 2
    total_energy = np.sum(spec, axis=0) + 1e-9
    freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

    # Vocal-heavy speech/singing band approximation.
    vocal_band = (freqs >= 300) & (freqs <= 3400)
    band_energy = np.sum(spec[vocal_band, :], axis=0)
    vocal_ratio = band_energy / total_energy
    flatness = librosa.feature.spectral_flatness(S=spec)[0]

    rms = librosa.feature.rms(S=spec)[0]
    time_frames = librosa.frames_to_time(np.arange(len(vocal_ratio)), sr=sr, hop_length=hop)

    min_rms = np.percentile(rms, 40) if rms.size else 0.0
    flatness_thr = np.percentile(flatness, 55) if flatness.size else 0.0
    phrases: List[Dict[str, float]] = []

    for start_sample, end_sample in intervals:
        start_t = start_sample / sr
        end_t = end_sample / sr
        dur = end_t - start_t
        if dur < 0.4 or dur > 12.0:
            continue

        idx = np.where((time_frames >= start_t) & (time_frames <= end_t))[0]
        if idx.size == 0:
            continue

        ratio = float(np.mean(vocal_ratio[idx]))
        rms_seg = float(np.mean(rms[idx]))
        flat_seg = float(np.mean(flatness[idx]))

        if ratio > 0.38 and flat_seg > flatness_thr and rms_seg >= min_rms:
            phrases.append({"start": _safe_float(start_t), "end": _safe_float(end_t)})

    return _merge_segments(phrases, max_gap=0.3)


def _derive_pause_points(vocal_phrases: List[Dict[str, float]], duration: float) -> List[float]:
    if not vocal_phrases:
        return []

    pauses: List[float] = []
    ordered = sorted(vocal_phrases, key=lambda x: float(x.get("start", 0.0)))
    for idx in range(1, len(ordered)):
        prev_end = float(ordered[idx - 1].get("end", 0.0))
        cur_start = float(ordered[idx].get("start", 0.0))
        gap = cur_start - prev_end
        if gap >= 0.2:
            pauses.append(_safe_float(prev_end + gap * 0.5))

    # Also include phrase endings as safe cut points.
    for phrase in ordered:
        end = float(phrase.get("end", 0.0))
        if 0.0 < end < duration:
            pauses.append(_safe_float(end))

    dedup: List[float] = []
    for value in sorted(set(pauses)):
        if not dedup or abs(value - dedup[-1]) >= 0.15:
            dedup.append(value)
    return dedup


def _estimate_downbeats_and_bars(
    beats: List[float],
    beat_frames: np.ndarray,
    onset_env: np.ndarray,
) -> tuple[List[float], List[float]]:
    if not beats or beat_frames.size == 0:
        return [], []

    beat_frames = np.asarray(beat_frames, dtype=int)
    beat_frames = np.clip(beat_frames, 0, max(len(onset_env) - 1, 0))
    beat_strength = onset_env[beat_frames] if len(onset_env) else np.zeros(len(beat_frames), dtype=float)

    phase_scores = []
    for offset in range(4):
        idx = np.arange(offset, len(beats), 4)
        score = float(np.mean(beat_strength[idx])) if idx.size else -1.0
        phase_scores.append(score)
    best_offset = int(np.argmax(phase_scores))

    raw_downbeat_idx: List[int] = []
    for start in range(best_offset, len(beats), 4):
        stop = min(start + 4, len(beats))
        window_idx = np.arange(start, stop)
        if window_idx.size == 0:
            continue
        local_max_idx = int(window_idx[np.argmax(beat_strength[window_idx])])
        raw_downbeat_idx.append(local_max_idx)

    if not raw_downbeat_idx:
        raw_downbeat_idx = list(range(best_offset, len(beats), 4))

    corrected_idx: List[int] = [raw_downbeat_idx[0]]
    for idx in raw_downbeat_idx[1:]:
        expected = corrected_idx[-1] + 4
        if abs(idx - expected) > 1:
            idx = int(np.clip(expected, 0, len(beats) - 1))
        if idx <= corrected_idx[-1]:
            idx = min(len(beats) - 1, corrected_idx[-1] + 1)
        corrected_idx.append(idx)

    unique_idx = sorted(set(corrected_idx))
    downbeats = [_safe_float(beats[i]) for i in unique_idx]
    bars = downbeats.copy()
    return downbeats, bars


def _estimate_sections(y: np.ndarray, sr: int, duration: float) -> List[Dict[str, float | str]]:
    if duration <= 0.0:
        return []

    # Build low-dimensional descriptor over short windows.
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=12, hop_length=hop)
    feat = np.vstack([mfcc, rms[np.newaxis, :]]).T

    frame_times = librosa.frames_to_time(np.arange(feat.shape[0]), sr=sr, hop_length=hop)
    win = 4.0
    step = 2.0
    starts = np.arange(0, max(duration - win, 0) + 1e-6, step)
    if starts.size == 0:
        starts = np.array([0.0])

    window_vecs = []
    for s in starts:
        e = min(s + win, duration)
        idx = np.where((frame_times >= s) & (frame_times < e))[0]
        if idx.size == 0:
            window_vecs.append(np.zeros(feat.shape[1], dtype=float))
        else:
            window_vecs.append(np.mean(feat[idx], axis=0))

    window_vecs = np.asarray(window_vecs)
    boundaries = [0.0]

    if len(window_vecs) > 1:
        diffs = np.linalg.norm(np.diff(window_vecs, axis=0), axis=1)
        if np.any(diffs > 0):
            thr = np.percentile(diffs, 75)
            for i, d in enumerate(diffs, start=1):
                if d >= thr and (starts[i] - boundaries[-1]) >= 6.0:
                    boundaries.append(float(starts[i]))

    if duration - boundaries[-1] >= 3.0:
        boundaries.append(duration)
    elif len(boundaries) == 1:
        boundaries = [0.0, duration]
    else:
        boundaries[-1] = duration

    # Deduplicate/cleanup boundaries and enforce minimum section length.
    clean = [boundaries[0]]
    for b in boundaries[1:]:
        if b - clean[-1] >= 6.0:
            clean.append(b)
        else:
            clean[-1] = b

    if len(clean) == 1:
        clean = [0.0, duration]
    elif clean[-1] != duration:
        clean[-1] = duration

    # Section labels: mode-agnostic by relative energy.
    sec_energy = []
    for i in range(len(clean) - 1):
        s, e = clean[i], clean[i + 1]
        idx = np.where((frame_times >= s) & (frame_times < e))[0]
        sec_energy.append(float(np.mean(rms[idx])) if idx.size else 0.0)

    if sec_energy:
        low_thr = float(np.percentile(sec_energy, 33))
        high_thr = float(np.percentile(sec_energy, 66))
    else:
        low_thr = high_thr = 0.0
    sections: List[Dict[str, float | str]] = []

    for i in range(len(clean) - 1):
        s = _safe_float(clean[i])
        e = _safe_float(clean[i + 1])
        if i == 0:
            sec_type = "opening"
        elif sec_energy[i] <= low_thr:
            sec_type = "energy_low"
        elif sec_energy[i] >= high_thr:
            sec_type = "energy_high"
        else:
            sec_type = "energy_mid"
        sections.append({"start": s, "end": e, "type": sec_type})

    return sections


def analyze_audio(path: str, debug: bool = False) -> dict:
    """Analyze an audio file and return rhythmic + structural metadata for video planning."""
    audio_path = Path(path)
    if not audio_path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")

    y, sr = librosa.load(str(audio_path), sr=22050, mono=True)
    if y.size == 0:
        return {
            "duration": 0.0,
            "bpm": 0.0,
            "beats": [],
            "downbeats": [],
            "bars": [],
            "vocalPhrases": [],
            "pausePoints": [],
            "phraseBoundaries": [],
            "energyPeaks": [],
            "sections": [],
        }

    duration = librosa.get_duration(y=y, sr=sr)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, units="frames")
    beats = librosa.frames_to_time(beat_frames, sr=sr).tolist() if len(beat_frames) else []

    beats = [_safe_float(t) for t in beats]
    tempo_scalar = _safe_scalar(tempo, 0.0)
    bpm = _safe_float(tempo_scalar) if np.isfinite(tempo_scalar) else 0.0

    downbeats, bars = _estimate_downbeats_and_bars(
        beats=beats,
        beat_frames=np.asarray(beat_frames),
        onset_env=onset_env,
    )

    # Energy curve from RMS + spectral flux, then peak-picking.
    hop = 512
    rms = librosa.feature.rms(y=y, hop_length=hop)[0]
    flux = onset_env
    # Align lengths for combination.
    n = min(len(rms), len(flux))
    rms_n = _normalize(rms[:n])
    flux_n = _normalize(flux[:n])
    energy_curve = 0.6 * rms_n + 0.4 * flux_n

    min_peak_distance_frames = max(1, int(1.0 * sr / hop))
    dynamic_prominence = max(
        0.05,
        float(np.percentile(energy_curve, 80) - np.percentile(energy_curve, 50)) if energy_curve.size else 0.05,
    )
    peaks, props = find_peaks(
        energy_curve,
        distance=min_peak_distance_frames,
        prominence=dynamic_prominence,
        height=np.percentile(energy_curve, 65) if len(energy_curve) else None,
    )
    prominences = props.get("prominences", np.zeros(len(peaks), dtype=float))

    max_peaks = max(1, int(np.ceil(duration / 10.0) * 5))
    peak_order = np.argsort(prominences)[::-1] if len(prominences) else np.array([], dtype=int)
    selected_peaks: List[float] = []
    min_sep = 0.8
    for order_idx in peak_order:
        candidate_t = librosa.frames_to_time(peaks[order_idx], sr=sr, hop_length=hop)
        if any(abs(candidate_t - t) < min_sep for t in selected_peaks):
            continue
        selected_peaks.append(float(candidate_t))
        if len(selected_peaks) >= max_peaks:
            break

    energy_peaks = [_safe_float(t) for t in sorted(selected_peaks)]

    vocal_phrases = _estimate_vocal_phrases(y=y, sr=sr)
    pause_points = _derive_pause_points(vocal_phrases=vocal_phrases, duration=float(duration))
    phrase_boundaries = sorted(
        {
            _safe_float(float(item.get("start", 0.0)))
            for item in vocal_phrases
            if 0.0 <= float(item.get("start", 0.0)) <= float(duration)
        }.union(
            {
                _safe_float(float(item.get("end", 0.0)))
                for item in vocal_phrases
                if 0.0 <= float(item.get("end", 0.0)) <= float(duration)
            }
        )
    )
    sections = _estimate_sections(y=y, sr=sr, duration=float(duration))

    if debug:
        print(f"BPM: {bpm}")
        print(f"beat_count: {len(beats)}")
        print(f"downbeat_count: {len(downbeats)}")
        print(f"vocal_phrase_count: {len(vocal_phrases)}")
        print(f"energy_peak_count: {len(energy_peaks)}")
        print(f"section_count: {len(sections)}")

    return {
        "duration": _safe_float(duration),
        "bpm": bpm,
        "beats": beats,
        "downbeats": downbeats,
        "bars": bars,
        "vocalPhrases": vocal_phrases,
        "pausePoints": pause_points,
        "phraseBoundaries": phrase_boundaries,
        "energyPeaks": energy_peaks,
        "sections": sections,
    }


def derive_audio_semantic_profile(analysis: Dict[str, object] | None) -> Dict[str, object]:
    """Build deterministic semantic hints from structural audio analysis (no ASR)."""
    data = analysis if isinstance(analysis, dict) else {}
    duration = float(data.get("duration") or 0.0)
    bpm = float(data.get("bpm") or 0.0)
    sections = data.get("sections") if isinstance(data.get("sections"), list) else []
    vocal_phrases = data.get("vocalPhrases") if isinstance(data.get("vocalPhrases"), list) else []
    pauses = data.get("pausePoints") if isinstance(data.get("pausePoints"), list) else []
    energy_peaks = data.get("energyPeaks") if isinstance(data.get("energyPeaks"), list) else []

    section_types = [str((section or {}).get("type") or "").strip().lower() for section in sections if isinstance(section, dict)]
    high_energy_count = sum(1 for sec in section_types if "energy_high" in sec or "chorus" in sec or "hook" in sec)
    mid_energy_count = sum(1 for sec in section_types if "energy_mid" in sec or "verse" in sec)
    intro_present = bool(section_types and section_types[0] in {"intro", "opening"})

    hints: List[str] = []
    summary_parts: List[str] = []

    if intro_present:
        hints.append("intro atmosphere")
        summary_parts.append("intro atmosphere")
    if high_energy_count > 0:
        hints.append("rising high-energy progression")
        summary_parts.append("rising high-energy progression")
    elif mid_energy_count > 1:
        hints.append("steady mid-energy progression")
        summary_parts.append("steady mid-energy progression")

    duration_min = max(duration / 60.0, 1e-6)
    vocal_density = len(vocal_phrases) / duration_min
    if vocal_density >= 28:
        hints.append("dense vocal delivery")
        summary_parts.append("dense vocal delivery with short gaps")
    elif vocal_density >= 14:
        hints.append("consistent vocal narration")
        summary_parts.append("consistent vocal presence")
    elif len(vocal_phrases) > 0:
        hints.append("sparse vocal phrases")
        summary_parts.append("spare vocal phrasing")
    else:
        hints.append("instrumental-forward structure")
        summary_parts.append("instrumental-forward structure")

    pause_density = len(pauses) / duration_min
    if pause_density >= 10:
        hints.append("frequent pauses between phrases")
    elif len(pauses) > 0:
        hints.append("measured phrase pauses")

    peak_density = len(energy_peaks) / duration_min
    if peak_density >= 10:
        hints.append("repeated high-energy peaks")
        summary_parts.append("repeated high-energy peaks")
    elif peak_density >= 5:
        hints.append("clear dynamic accents")
        summary_parts.append("clear dynamic accents")
    elif len(energy_peaks) > 0:
        hints.append("restrained dynamic contour")
        summary_parts.append("restrained dynamic contour")

    if bpm >= 140:
        hints.append("fast momentum pacing")
    elif bpm >= 100:
        hints.append("mid-tempo cinematic pacing")
    elif bpm > 0:
        hints.append("slow-burn pacing")

    if section_types and section_types[-1] in {"intro", "verse"}:
        hints.append("soft release ending")
        summary_parts.append("softer release")
    elif high_energy_count > 0:
        hints.append("strong high-energy emphasis")

    dedup_hints = list(dict.fromkeys([h for h in hints if h]))[:8]
    summary = ", ".join(dict.fromkeys([p for p in summary_parts if p]))
    if not summary:
        summary = "audio-led structure with evolving intensity and cinematic pacing"

    return {
        "audioSemanticSummary": summary,
        "audioSemanticHints": dedup_hints,
        "semanticStats": {
            "sectionCount": len(sections),
            "vocalPhraseCount": len(vocal_phrases),
            "pausePointCount": len(pauses),
            "energyPeakCount": len(energy_peaks),
            "bpm": _safe_float(bpm),
            "duration": _safe_float(duration),
        },
    }


def analyze_audio_semantics_fallback(transcript_text: str = "", *, hint: str = "semantic_unavailable") -> dict[str, Any]:
    transcript = str(transcript_text or "").strip()
    has_transcript = bool(transcript)
    return {
        "ok": has_transcript,
        "transcript": transcript,
        "semanticSummary": "Transcript available but semantic extraction fallback mode is active." if has_transcript else "",
        "narrativeCore": "",
        "worldContext": "",
        "entities": [],
        "impliedEvents": [],
        "tone": "",
        "confidence": 0.2 if has_transcript else 0.0,
        "hint": hint,
    }


def analyze_audio_semantics(audio_path: str | None = None, *, transcript_text: str = "") -> dict[str, Any]:
    transcript = str(transcript_text or "").strip()
    if not transcript:
        return analyze_audio_semantics_fallback(transcript, hint="no_asr_transcript")

    text_lower = transcript.lower()
    entities: list[str] = []
    implied_events: list[str] = []
    tone_signals: list[str] = []

    # Keep semantic extraction story-agnostic: do not hardcode domain/country/case-specific
    # entities/events that could silently drag future stories into legacy contexts.
    entity_rules: list[tuple[tuple[str, ...], str]] = []
    event_rules: list[tuple[tuple[str, ...], str]] = []
    tone_rules = [
        (("urgent", "critical", "сроч", "немед"), "urgent"),
        (("danger", "threat", "опас", "угроз"), "threatening"),
        (("secret", "classified", "секрет"), "secretive"),
        (("calm", "steady", "спокой"), "calm"),
    ]

    for needles, label in entity_rules:
        if any(needle in text_lower for needle in needles):
            entities.append(label)
    for needles, label in event_rules:
        if any(needle in text_lower for needle in needles):
            implied_events.append(label)
    for needles, label in tone_rules:
        if any(needle in text_lower for needle in needles):
            tone_signals.append(label)

    dedup_entities = list(dict.fromkeys([item for item in entities if item]))
    dedup_events = list(dict.fromkeys([item for item in implied_events if item]))
    dedup_tone = list(dict.fromkeys([item for item in tone_signals if item]))

    narrative_sentences = [s.strip() for s in re.split(r"[.!?\n]+", transcript) if s.strip()]
    narrative_core = narrative_sentences[0][:320] if narrative_sentences else transcript[:320]
    world_context = ""
    if dedup_entities:
        world_context = f"Audio references: {', '.join(dedup_entities)}."
    elif dedup_events:
        world_context = f"Implied world events: {', '.join(dedup_events)}."

    summary_bits: list[str] = []
    summary_bits.append(narrative_core[:220] if narrative_core else transcript[:220])
    if dedup_entities:
        summary_bits.append(f"Entities: {', '.join(dedup_entities)}")
    if dedup_events:
        summary_bits.append(f"Events: {', '.join(dedup_events)}")
    semantic_summary = ". ".join([bit for bit in summary_bits if bit]).strip()

    confidence = 0.45
    if dedup_entities:
        confidence += 0.2
    if dedup_events:
        confidence += 0.15
    if len(transcript) >= 80:
        confidence += 0.1
    if dedup_tone:
        confidence += 0.05

    return {
        "ok": True,
        "transcript": transcript,
        "semanticSummary": semantic_summary[:1200],
        "narrativeCore": narrative_core[:600],
        "worldContext": world_context[:600],
        "entities": dedup_entities[:12],
        "impliedEvents": dedup_events[:12],
        "tone": ", ".join(dedup_tone[:4]),
        "confidence": float(max(0.0, min(1.0, round(confidence, 3)))),
        "hint": "transcript_semantic_ok",
        "audioPathProvided": bool(str(audio_path or "").strip()),
    }
