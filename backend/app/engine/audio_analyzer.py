from __future__ import annotations

from pathlib import Path
from typing import List, Dict

import librosa
import numpy as np
from scipy.signal import find_peaks


def _safe_float(value: float) -> float:
    return float(np.round(float(value), 4))


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

    # Section labels: intro + verse/chorus by relative energy.
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
        if sec_energy[i] <= low_thr:
            sec_type = "intro"
        elif sec_energy[i] >= high_thr:
            sec_type = "chorus"
        else:
            sec_type = "verse"
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
            "energyPeaks": [],
            "sections": [],
        }

    duration = librosa.get_duration(y=y, sr=sr)

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    tempo, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, units="frames")
    beats = librosa.frames_to_time(beat_frames, sr=sr).tolist() if len(beat_frames) else []

    beats = [_safe_float(t) for t in beats]
    bpm = _safe_float(float(tempo)) if np.isfinite(tempo) else 0.0

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
