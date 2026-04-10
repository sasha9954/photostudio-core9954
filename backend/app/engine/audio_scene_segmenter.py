from __future__ import annotations

import base64
import ipaddress
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.engine.gemini_rest import post_generate_content

logger = logging.getLogger(__name__)

GEMINI_SEGMENTATION_PROMPT_VERSION = "gemini_audio_segmentation_v2"
GEMINI_SEGMENTATION_MODEL = "gemini-3.1-pro-preview"
_MAX_INLINE_AUDIO_BYTES = 18 * 1024 * 1024
_SCENE_WINDOWS_MAX_START_GAP_SEC = 1.0
_SCENE_WINDOWS_MAX_END_GAP_SEC = 1.2
_SCENE_WINDOWS_MAX_INTER_GAP_SEC = 1.2


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _round3(value: float) -> float:
    return round(float(value), 3)


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp.get("candidates"), list) else []
    if not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
    parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            chunks.append(part.get("text") or "")
    return "\n".join(chunks).strip()


def _extract_json_obj(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        first, last = raw.find("{"), raw.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(raw[first : last + 1])
            except Exception:
                return {}
    return {}


def _build_prompt(
    *,
    duration_sec: float,
    story_core: dict[str, Any],
    content_type: str,
    story_core_mode: str,
    narrative_directive: str,
    director_note: str,
) -> str:
    compact_context = {
        "duration_sec": _round3(duration_sec),
        "content_type": str(content_type or "music_video"),
        "story_core_mode": str(story_core_mode or "creative"),
        "story_core": {
            "story_summary": str(story_core.get("story_summary") or "")[:500],
            "opening_anchor": str(story_core.get("opening_anchor") or "")[:250],
            "ending_callback_rule": str(story_core.get("ending_callback_rule") or "")[:250],
            "global_arc": str(story_core.get("global_arc") or "")[:250],
        },
        "narrative_directive": str(narrative_directive or "")[:1200],
        "director_note": str(director_note or "")[:1200],
    }
    return (
        "You are a cinematic audio segmentation engine for video scene planning.\n"
        "Analyze the attached audio track and return STRICT JSON only (no markdown, no prose).\n"
        "Track duration is authoritative. Never exceed it.\n"
        f"duration_sec={_round3(duration_sec)}\n\n"
        "SEGMENTATION RULES:\n"
        "1) Detect track_type: vocal or instrumental.\n"
        "2) If vocal: segment by phrase endings, never cut in the middle of a spoken/sung word.\n"
        "3) If instrumental: segment by musical phrases, energy shifts, and natural transitions.\n"
        "4) Scene length target: typically 3-6 sec.\n"
        "5) 2.3-3.0 sec is allowed for short phrase endings.\n"
        "6) 6-8 sec is rare and only when natural.\n"
        "7) >8 sec is forbidden.\n"
        "8) If there is a long instrumental tail, split into 2 useful edit windows when natural.\n"
        "9) Avoid mechanical equal-time grids. Prefer semantic/music-aware cuts.\n"
        "10) Use exact overall_duration_sec close to the provided duration_sec.\n\n"
        "SCENE FUNCTION LABELS (use ONLY this set):\n"
        "- setup = first establishing phrase or opening hook.\n"
        "- build = momentum grows.\n"
        "- turn = emotional or lyrical shift.\n"
        "- release = phrase resolves.\n"
        "- afterimage = outro, fading residue, reflective tail.\n"
        "- bridge = connective transition.\n"
        "- accent = short emphatic highlight.\n"
        "- climax = strongest peak or final push.\n"
        "Distribute scene_function labels meaningfully across the track timeline. Do not overuse a single label without a clear reason.\n"
        "Choose labels based on both lyric structure and energy role of each window.\n\n"
        "PHRASE semantic_weight guidance:\n"
        "- high: hook, repeated motif, or emotionally important phrase.\n"
        "- medium: connective or descriptive phrase.\n"
        "- low: weak transition, filler, or low-information tail.\n"
        "Avoid flat labeling; use low/medium/high contrast that is useful for downstream scene planning.\n\n"
        "transcript_confidence guidance (for scene_windows and phrase_units):\n"
        "- high only if words are clearly audible.\n"
        "- medium if wording is probable but not perfectly clear.\n"
        "- low if phrase is uncertain / guessed from unclear audio.\n"
        "Do not overstate confidence.\n\n"
        "Return EXACT contract keys and structure:\n"
        "{\n"
        '  "transcript_available": true,\n'
        '  "track_type": "vocal",\n'
        '  "overall_duration_sec": 29.465,\n'
        '  "global_notes": {"segmentation_strategy": "", "warnings": []},\n'
        '  "scene_windows": [{"id": "sc_1", "t0": 0.0, "t1": 0.0, "duration_sec": 0.0, "phrase_text": "", "transcript_confidence": "high", "cut_reason": "", "energy": "low", "scene_function": "setup", "no_mid_word_cut": true}],\n'
        '  "phrase_units": [{"id": "ph_1", "t0": 0.0, "t1": 0.0, "text": "", "semantic_weight": "low", "can_cut_after": true, "transcript_confidence": "high"}],\n'
        '  "candidate_cut_points_sec": [],\n'
        '  "no_split_ranges": [{"t0": 0.0, "t1": 0.0, "reason": ""}]\n'
        "}\n\n"
        f"CONTEXT:\n{json.dumps(compact_context, ensure_ascii=False)}"
    )


def _normalize_gemini_payload(payload: dict[str, Any], duration_sec: float) -> dict[str, Any]:
    duration = max(0.0, _coerce_float(duration_sec, 0.0))
    normalized = {
        "transcript_available": bool(payload.get("transcript_available")),
        "track_type": str(payload.get("track_type") or "unknown").strip().lower() or "unknown",
        "overall_duration_sec": _round3(_clamp(_coerce_float(payload.get("overall_duration_sec"), duration), 0.0, max(duration, 0.0))),
        "global_notes": {
            "segmentation_strategy": str(_safe_dict(payload.get("global_notes")).get("segmentation_strategy") or "").strip(),
            "warnings": [str(item) for item in _safe_list(_safe_dict(payload.get("global_notes")).get("warnings")) if str(item).strip()],
        },
        "scene_windows": [],
        "phrase_units": [],
        "candidate_cut_points_sec": [],
        "no_split_ranges": [],
    }

    phrase_units: list[dict[str, Any]] = []
    for idx, item in enumerate(_safe_list(payload.get("phrase_units")), start=1):
        row = _safe_dict(item)
        t0 = _round3(_clamp(_coerce_float(row.get("t0"), 0.0), 0.0, duration))
        t1 = _round3(_clamp(_coerce_float(row.get("t1"), t0), 0.0, duration))
        if t1 <= t0:
            continue
        phrase_units.append(
            {
                "id": str(row.get("id") or f"ph_{idx}"),
                "t0": t0,
                "t1": t1,
                "text": str(row.get("text") or "").strip(),
                "semantic_weight": str(row.get("semantic_weight") or "low").strip().lower() or "low",
                "can_cut_after": bool(row.get("can_cut_after", True)),
                "transcript_confidence": str(row.get("transcript_confidence") or "medium").strip().lower() or "medium",
            }
        )
    phrase_units.sort(key=lambda x: (float(x.get("t0") or 0.0), float(x.get("t1") or 0.0)))

    scene_windows: list[dict[str, Any]] = []
    for idx, item in enumerate(_safe_list(payload.get("scene_windows")), start=1):
        row = _safe_dict(item)
        t0 = _round3(_clamp(_coerce_float(row.get("t0"), 0.0), 0.0, duration))
        t1 = _round3(_clamp(_coerce_float(row.get("t1"), t0), 0.0, duration))
        if t1 <= t0:
            continue
        scene_windows.append(
            {
                "id": str(row.get("id") or f"sc_{idx}"),
                "t0": t0,
                "t1": t1,
                "duration_sec": _round3(max(0.0, t1 - t0)),
                "phrase_text": str(row.get("phrase_text") or "").strip(),
                "transcript_confidence": str(row.get("transcript_confidence") or "medium").strip().lower() or "medium",
                "cut_reason": str(row.get("cut_reason") or "").strip(),
                "energy": str(row.get("energy") or "medium").strip().lower() or "medium",
                "scene_function": str(row.get("scene_function") or "beat").strip().lower() or "beat",
                "no_mid_word_cut": bool(row.get("no_mid_word_cut", True)),
            }
        )
    scene_windows.sort(key=lambda x: (float(x.get("t0") or 0.0), float(x.get("t1") or 0.0)))

    candidate_points: list[float] = []
    for value in _safe_list(payload.get("candidate_cut_points_sec")):
        point = _round3(_clamp(_coerce_float(value, -1.0), 0.0, duration))
        if 0.0 <= point <= duration:
            candidate_points.append(point)
    normalized["candidate_cut_points_sec"] = sorted(set(candidate_points))

    no_split_ranges: list[dict[str, Any]] = []
    for row in _safe_list(payload.get("no_split_ranges")):
        item = _safe_dict(row)
        t0 = _round3(_clamp(_coerce_float(item.get("t0"), 0.0), 0.0, duration))
        t1 = _round3(_clamp(_coerce_float(item.get("t1"), t0), 0.0, duration))
        if t1 <= t0:
            continue
        no_split_ranges.append({"t0": t0, "t1": t1, "reason": str(item.get("reason") or "").strip()})

    normalized["phrase_units"] = phrase_units
    normalized["scene_windows"] = scene_windows
    normalized["no_split_ranges"] = no_split_ranges

    if normalized["overall_duration_sec"] <= 0 and duration > 0:
        normalized["overall_duration_sec"] = _round3(duration)

    warnings = normalized["global_notes"]["warnings"]
    scene_functions = [str(row.get("scene_function") or "").strip().lower() for row in scene_windows if str(row.get("scene_function") or "").strip()]
    semantic_weights = [str(row.get("semantic_weight") or "").strip().lower() for row in phrase_units if str(row.get("semantic_weight") or "").strip()]
    transcript_confidences = [
        str(row.get("transcript_confidence") or "").strip().lower()
        for row in [*scene_windows, *phrase_units]
        if str(row.get("transcript_confidence") or "").strip()
    ]
    if len(set(scene_functions)) == 1 and scene_functions:
        warnings.append(f"scene_function_flat:{scene_functions[0]}")
    if len(set(semantic_weights)) == 1 and semantic_weights:
        warnings.append(f"semantic_weight_flat:{semantic_weights[0]}")
    if len(set(transcript_confidences)) == 1 and transcript_confidences:
        warnings.append(f"transcript_confidence_flat:{transcript_confidences[0]}")
    normalized["global_notes"]["warnings"] = list(dict.fromkeys(warnings))

    # Optional post-split of too-long outro, if a natural cut point exists.
    if scene_windows:
        last = scene_windows[-1]
        span = float(last.get("duration_sec") or 0.0)
        if span > 6.8:
            last_t0 = float(last.get("t0") or 0.0)
            last_t1 = float(last.get("t1") or last_t0)
            interior_points = [p for p in normalized["candidate_cut_points_sec"] if (last_t0 + 2.3) <= p <= (last_t1 - 2.3)]
            if interior_points:
                split_at = interior_points[-1]
                scene_windows[-1] = {
                    **last,
                    "t1": _round3(split_at),
                    "duration_sec": _round3(max(0.0, split_at - last_t0)),
                    "cut_reason": (str(last.get("cut_reason") or "") + " | auto_tail_split_a").strip(" |"),
                }
                scene_windows.append(
                    {
                        "id": f"sc_{len(scene_windows) + 1}",
                        "t0": _round3(split_at),
                        "t1": _round3(last_t1),
                        "duration_sec": _round3(max(0.0, last_t1 - split_at)),
                        "phrase_text": "",
                        "transcript_confidence": str(last.get("transcript_confidence") or "medium"),
                        "cut_reason": "auto_tail_split_b",
                        "energy": str(last.get("energy") or "medium"),
                        "scene_function": "outro",
                        "no_mid_word_cut": True,
                    }
                )
                scene_windows.sort(key=lambda x: (float(x.get("t0") or 0.0), float(x.get("t1") or 0.0)))

    for idx, row in enumerate(scene_windows, start=1):
        row["id"] = f"sc_{idx}"
        row["duration_sec"] = _round3(max(0.0, float(row.get("t1") or 0.0) - float(row.get("t0") or 0.0)))
    for idx, row in enumerate(phrase_units, start=1):
        row["id"] = f"ph_{idx}"

    return normalized


def _validate_gemini_payload(payload: dict[str, Any], duration_sec: float) -> str:
    duration = max(0.0, _coerce_float(duration_sec, 0.0))
    if not isinstance(payload, dict):
        return "gemini_payload_not_object"

    overall_duration = _coerce_float(payload.get("overall_duration_sec"), 0.0)
    if duration > 0 and abs(overall_duration - duration) > 1.2:
        return "overall_duration_mismatch"

    scene_windows = _safe_list(payload.get("scene_windows"))
    if not scene_windows:
        return "scene_windows_missing"

    prev_t1 = -1.0
    first_t0 = -1.0
    last_t1 = -1.0
    for idx, raw in enumerate(scene_windows):
        row = _safe_dict(raw)
        t0 = _coerce_float(row.get("t0"), -1.0)
        t1 = _coerce_float(row.get("t1"), -1.0)
        if t0 < 0.0 or t1 < 0.0:
            return f"scene_window_invalid_time_{idx}"
        if duration > 0 and t1 > (duration + 0.001):
            return f"scene_window_out_of_range_{idx}"
        if t1 <= t0:
            return f"scene_window_non_positive_span_{idx}"
        declared = _coerce_float(row.get("duration_sec"), t1 - t0)
        if abs(declared - (t1 - t0)) > 0.25:
            return f"scene_window_duration_mismatch_{idx}"
        span = t1 - t0
        if span > 8.0:
            return f"scene_window_too_long_{idx}"
        if idx == 0:
            first_t0 = t0
        else:
            gap = t0 - prev_t1
            if gap > _SCENE_WINDOWS_MAX_INTER_GAP_SEC:
                return f"scene_windows_gap_too_large_{idx}"
        if prev_t1 >= 0 and t0 + 0.12 < prev_t1:
            return f"scene_window_overlap_{idx}"
        prev_t1 = t1
        last_t1 = t1

    if first_t0 > _SCENE_WINDOWS_MAX_START_GAP_SEC:
        return "scene_windows_start_gap_too_large"
    if duration > 0 and (duration - last_t1) > _SCENE_WINDOWS_MAX_END_GAP_SEC:
        return "scene_windows_end_gap_too_large"

    for idx, value in enumerate(_safe_list(payload.get("candidate_cut_points_sec"))):
        point = _coerce_float(value, -1.0)
        if point < 0.0 or point > duration + 0.001:
            return f"candidate_cut_point_out_of_range_{idx}"

    return ""


def _is_local_or_private_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    if parsed.scheme not in {"http", "https"}:
        return True
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return True
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".lan") or host.endswith(".home") or host.endswith(".ts.net"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return not ip.is_global
    except Exception:
        return False


def _guess_audio_mime(audio_path: str) -> str:
    guessed, _ = mimetypes.guess_type(audio_path)
    if guessed and guessed.startswith("audio/"):
        return guessed
    suffix = Path(audio_path).suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".wav":
        return "audio/wav"
    if suffix == ".m4a":
        return "audio/mp4"
    return "audio/mpeg"


def build_gemini_audio_segmentation(
    *,
    api_key: str,
    audio_path: str,
    audio_url: str,
    duration_sec: float,
    story_core: dict[str, Any],
    content_type: str,
    story_core_mode: str,
    narrative_directive: str = "",
    director_note: str = "",
) -> dict[str, Any]:
    if not api_key:
        return {"ok": False, "error": "gemini_api_key_missing", "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION, "used_model": GEMINI_SEGMENTATION_MODEL}
    if duration_sec <= 0:
        return {"ok": False, "error": "duration_missing", "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION, "used_model": GEMINI_SEGMENTATION_MODEL}

    prompt = _build_prompt(
        duration_sec=duration_sec,
        story_core=story_core,
        content_type=content_type,
        story_core_mode=story_core_mode,
        narrative_directive=narrative_directive,
        director_note=director_note,
    )

    parts: list[dict[str, Any]] = [{"text": prompt}]

    if audio_path:
        try:
            data = Path(audio_path).read_bytes()
            if len(data) <= _MAX_INLINE_AUDIO_BYTES:
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": _guess_audio_mime(audio_path),
                            "data": base64.b64encode(data).decode("ascii"),
                        }
                    }
                )
            elif audio_url and not _is_local_or_private_url(audio_url):
                parts.append(
                    {
                        "fileData": {
                            "mimeType": _guess_audio_mime(audio_path),
                            "fileUri": str(audio_url).strip(),
                        }
                    }
                )
            else:
                return {
                    "ok": False,
                    "error": "audio_too_large_no_public_url",
                    "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION,
                    "used_model": GEMINI_SEGMENTATION_MODEL,
                }
        except Exception as exc:  # noqa: BLE001
            logger.exception("[audio_scene_segmenter] failed to attach audio")
            return {
                "ok": False,
                "error": f"audio_attach_failed:{exc}",
                "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION,
                "used_model": GEMINI_SEGMENTATION_MODEL,
            }
    elif audio_url and not _is_local_or_private_url(audio_url):
        parts.append({"fileData": {"mimeType": "audio/mpeg", "fileUri": str(audio_url).strip()}})
    else:
        return {"ok": False, "error": "audio_source_missing_or_private_url", "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION, "used_model": GEMINI_SEGMENTATION_MODEL}

    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "temperature": 0.1,
            "topP": 0.9,
        },
    }

    response = post_generate_content(api_key=api_key, model=GEMINI_SEGMENTATION_MODEL, body=body, timeout=120)
    if isinstance(response, dict) and response.get("__http_error__"):
        return {
            "ok": False,
            "error": f"gemini_http_error:{response.get('status')}:{response.get('text')}",
            "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION,
            "used_model": GEMINI_SEGMENTATION_MODEL,
        }

    parsed = _extract_json_obj(_extract_gemini_text(response))
    if not parsed:
        return {"ok": False, "error": "gemini_json_parse_failed", "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION, "used_model": GEMINI_SEGMENTATION_MODEL}

    normalized = _normalize_gemini_payload(parsed, duration_sec)
    validation_error = _validate_gemini_payload(normalized, duration_sec)
    if validation_error:
        return {
            "ok": False,
            "error": f"gemini_validation_failed:{validation_error}",
            "validation_error": validation_error,
            "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION,
            "payload": normalized,
            "used_model": GEMINI_SEGMENTATION_MODEL,
        }

    return {
        "ok": True,
        "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION,
        "payload": normalized,
        "used_model": GEMINI_SEGMENTATION_MODEL,
    }
