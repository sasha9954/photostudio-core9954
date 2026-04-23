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

GEMINI_SEGMENTATION_PROMPT_VERSION = "gemini_audio_map_v1_4_dramaturgic_hints"
GEMINI_SEGMENTATION_MODEL = "gemini-3.1-pro-preview"
_MAX_INLINE_AUDIO_BYTES = 18 * 1024 * 1024


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
    if host.endswith((".local", ".internal", ".lan", ".home", ".ts.net")):
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


def _build_prompt(
    *,
    duration_sec: float,
    audio_id: str,
    transcript_text: str,
    dynamics_summary: dict[str, Any],
    role_identity_mapping: dict[str, Any],
    validation_feedback: str = "",
) -> str:
    evidence = {
        "audio_id": audio_id,
        "duration_sec": round(float(duration_sec or 0.0), 3),
        "transcript_excerpt": str(transcript_text or "")[:3000],
        "dynamics_summary": dynamics_summary,
        "role_identity_mapping": role_identity_mapping,
    }
    feedback_block = ""
    if validation_feedback:
        feedback_block = (
            "\nPREVIOUS OUTPUT WAS REJECTED BY STRICT VALIDATOR.\n"
            f"Fix these exact issues and regenerate full JSON: {validation_feedback}\n"
        )

    return (
        "You are generating AUDIO stage only.\n"
        "Return STRICT JSON only (no markdown, no prose).\n"
        "AUDIO stage scope: timing segmentation map only.\n"
        "Forbidden: story, scenes, camera, motion, role authoring, prompt language, invented lyrics.\n"
        "Use transcript/dynamics only as evidence.\n"
        f"Track duration is authoritative: {round(float(duration_sec or 0.0), 3)} seconds.\n"
        "Schema contract:\n"
        "{\n"
        '  "audio_map_version": "1.1",\n'
        '  "audio_id": "string",\n'
        '  "vocal_profile": {"vocal_gender": "female|male|mixed|unknown", "vocal_owner_role": "character_1|character_2|character_3|unknown", "confidence": 0.0, "reason": "short explanation"},\n'
        '  "vocal_gender": "female|male|mixed|unknown",\n'
        '  "vocal_owner_role": "character_1|character_2|character_3|unknown",\n'
        '  "vocal_owner_confidence": 0.0,\n'
        '  "phrase_units": [{"id": "ph_01", "t0": 0.0, "t1": 1.3, "duration_sec": 1.3, "transcript_slice": "string", "text": "string", "intensity": 0.0}],\n'
        '  "segments": [{"segment_id": "string", "t0": 0.0, "t1": 4.2, "duration_sec": 4.2, "transcript_slice": "string", "intensity": 0.0, "is_lip_sync_candidate": false, "rhythmic_anchor": "beat", "first_last_candidate": false, "route_hints": {"i2v_fit": "good", "lip_sync_fit": "ok", "first_last_fit": "too_short"}, "local_energy_band": "low|medium|high|surge|settle", "energy_delta_vs_prev": "rise|hold|soften|release|spike|reset", "delivery_mode": "declarative|reflective|assertive|intimate|suspended|final|observational|pressurized", "semantic_weight": "low|medium|high", "semantic_turn_candidate": false, "release_candidate": false, "finality_candidate": "none|continuation|hinge|closure|tail_hit", "visual_density_hint": "sparse|moderate|dense", "stillness_candidate": false, "lyrical_density": "low|medium|high"}],\n'
        '  "no_split_ranges": [{"start": 0.0, "end": 0.0}],\n'
        '  "diagnostics": {"total_segments_duration": 0.0, "coverage_ok": true, "energy_peak_detected": false, "transcript_used": false, "dynamics_used": false, "validation_notes": [], "audio_map_local_energy_variation_score": 0.0, "audio_map_delivery_mode_distribution": {"declarative": 0}, "audio_map_semantic_weight_distribution": {"low": 0, "medium": 0, "high": 0}, "audio_map_semantic_turn_candidate_count": 0, "audio_map_release_candidate_count": 0, "audio_map_stillness_candidate_count": 0, "audio_map_finality_candidate_count": 0, "audio_map_flat_energy_warning": false, "audio_map_flat_delivery_warning": false, "audio_map_flat_semantic_weight_warning": false, "audio_map_contrast_potential_summary": "short text", "audio_map_progression_hint_summary": "short text"}\n'
        "}\n"
        "VIDEO-READY SEGMENTATION CANON:\n"
        "You are creating audio_map for video generation, not only phrase transcription.\n"
        "Separate two concepts:\n"
        "1) phrase_units: short vocal/dialogue/music phrases and reactions in audio evidence.\n"
        "2) segments: final video-ready generation windows.\n"
        "phrase_units may be short (0.5-2.5s), but segments must be renderable video windows.\n"
        "Rules:\n"
        "- segment_id format is strict: seg_01, seg_02, ... seg_99 (prefix seg_ + exactly two digits).\n"
        "- Never emit seg_001, seg_0, segment_1, or any alternative ID format.\n"
        "- Every segment must include: t0, t1, duration_sec.\n"
        "- duration_sec must equal (t1 - t0) using natural sub-second precision.\n"
        "- Hard maximum segment duration is 7.0 seconds.\n"
        "- segments must be ordered and contiguous without overlaps/gaps outside tiny tolerance.\n"
        "- Full audio coverage is required from 0.0 to full track duration: no gaps, no overlaps.\n"
        "- Never round boundaries to whole seconds just because they look neat.\n"
        "- Use natural sub-second precision; do not bias toward integer timestamps.\n"
        "- Never cut mid-word.\n"
        "- Never extend a segment by cutting into the next spoken word only to satisfy duration.\n"
        "- Never fake or pad duration mechanically.\n"
        "- Prefer natural dramatic/audio windows over mechanical duration targets.\n"
        "- Preserve emotional meaning and original phrase order after merges.\n"
        "- Do not cut a sung phrase before it is acoustically complete.\n"
        "- Respect final consonants, vocal tail, breath release, and reverb decay before ending a phrase segment.\n"
        "- A ~0.5s early vocal cut is considered bad segmentation and must be avoided.\n"
        "- Phrase endings must follow sung completion, not rough text chunk boundaries.\n"
        "- Prefer musical phrase closure over evenly spaced or visually pretty timing.\n"
        "- Do not split inside a continuing sung phrase.\n"
        "- phrase_units may be short (0.5-2.5s), but a short phrase_unit must not automatically become a standalone segment.\n"
        "- If a phrase_unit is shorter than 2.5 seconds, merge it with the next phrase_unit, nearby pause, breath, reaction, or emotional tail.\n"
        "- If a short phrase_unit is at the end, merge it with the previous phrase_unit.\n"
        "- Do not blindly merge short phrases into overly long segments.\n"
        "- If a clean phrase window is 2.7-3.0 seconds and ends naturally, it may remain a compact i2v-only segment.\n"
        "- A 2.7-3.0s compact segment must not be marked first_last_candidate.\n"
        "- A 2.7-3.0s compact segment should not be preferred for lip_sync unless natural reaction/tail makes it stable.\n"
        "- If a 2.7-3.0s phrase immediately continues into another word/phrase, merge naturally instead of cutting.\n"
        "- 0 <= intensity <= 1.\n"
        "- rhythmic_anchor must be one of: beat, drop, transition, none.\n"
        "- Route duration fit canon:\n"
        "  * i2v: acceptable compact window 2.8-4.2s; ideal 3.0-4.2s; avoid >4.5s unless observational/tense/slow/emotionally sustained.\n"
        "  * lip_sync: minimum 3.0s; ideal 3.0-5.0s; use full vocal/dialogue phrase windows.\n"
        "  * first_last: minimum 4.0s; ideal 4.5-5.5s.\n"
        "- first_last_candidate may be true only when duration_sec >= 4.0.\n"
        "- route_hints is optional and for downstream hints only; values must be one of good|ok|too_short|too_long for i2v_fit/lip_sync_fit/first_last_fit.\n"
        "- New dramaturgic hint fields are required per segment, but they are hints only (never mandatory scene functions).\n"
        "- Do NOT output setup/reveal/pivot/peak/afterimage labels or any fixed story arc labels.\n"
        "- local_energy_band must reflect local neighborhood modulation, not only absolute intensity.\n"
        "- energy_delta_vs_prev describes change from previous segment.\n"
        "- delivery_mode must reflect vocal/performance delivery character, not scene design.\n"
        "- semantic_weight and semantic_turn_candidate are transcript/audio meaning hints only.\n"
        "- release_candidate/stillness_candidate are opportunities for CORE, not instructions.\n"
        "- finality_candidate must be one of none|continuation|hinge|closure|tail_hit and stay non-prescriptive.\n"
        "- visual_density_hint reflects audiovisual pressure opportunity only.\n"
        "- lyrical_density reflects words-per-duration / pressure; keep universal.\n"
        "- Prefer fewer stronger scenes over many tiny fragments, but avoid overlong i2v scenes without reason.\n"
        "- transcript_slice must be literal transcript evidence snippets, never placeholders.\n"
        "- no_split_ranges must not conflict with segment boundaries.\n"
        "VOICE OWNERSHIP (strict, non-creative):\n"
        "- Determine voice ownership from audible voice only; do NOT infer story intent.\n"
        "- Use role_identity_mapping only for deterministic role lookup by gender_hint/identity_label.\n"
        "- If single clear female voice: vocal_gender=female.\n"
        "- If single clear male voice: vocal_gender=male.\n"
        "- If mixed voices/dialogue/multiple vocalists/unclear: vocal_gender=mixed or unknown; vocal_owner_role=unknown.\n"
        "- For vocal_gender=female|male: assign vocal_owner_role only if exactly one role has matching gender hint.\n"
        "- If multiple roles share that gender or evidence is weak: vocal_owner_role=unknown.\n"
        "- Set confidence in [0,1], lower when mixed/unknown/ambiguous.\n"
        "- Keep top-level vocal_gender/vocal_owner_role/vocal_owner_confidence aligned with vocal_profile.\n"
        "- reason must be short factual audio evidence explanation.\n"
        "Before returning JSON, validate each segment with video-ready logic: avoid standalone <2.5s; avoid standalone <2.8s unless natural tail/reaction and near-usable compact i2v; first_last_candidate only if duration_sec >= 4.0; no mid-word cuts; full coverage with no gaps/overlaps.\n"
        "If any candidate segment violates these rules, regenerate natural merges (do not mechanically pad).\n"
        f"{feedback_block}"
        f"EVIDENCE:\n{json.dumps(evidence, ensure_ascii=False)}"
    )


def build_gemini_audio_segmentation(
    *,
    api_key: str,
    audio_path: str,
    audio_url: str,
    duration_sec: float,
    audio_id: str,
    transcript_text: str,
    dynamics_summary: dict[str, Any] | None = None,
    role_identity_mapping: dict[str, Any] | None = None,
    validation_feedback: str = "",
) -> dict[str, Any]:
    transport_meta: dict[str, Any] = {
        "audio_segmentation_source_mode": "none",
        "audio_segmentation_local_path_found": bool(str(audio_path or "").strip()),
        "audio_segmentation_inline_attempted": False,
        "audio_segmentation_inline_bytes_size": 0,
        "audio_segmentation_url_used": "",
        "audio_segmentation_transport_error": "",
    }

    def _fail(error: str, *, raw_text: str = "", parsed_json: dict[str, Any] | None = None) -> dict[str, Any]:
        transport_meta["audio_segmentation_transport_error"] = str(error or "")
        return {
            "ok": False,
            "error": error,
            "raw_text": raw_text,
            "parsed_json": parsed_json or {},
            "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION,
            "used_model": GEMINI_SEGMENTATION_MODEL,
            "transport_meta": dict(transport_meta),
        }

    if not api_key:
        return _fail("gemini_api_key_missing")
    if duration_sec <= 0:
        return _fail("duration_missing")

    prompt = _build_prompt(
        duration_sec=duration_sec,
        audio_id=audio_id,
        transcript_text=transcript_text,
        dynamics_summary=dynamics_summary or {},
        role_identity_mapping=role_identity_mapping or {},
        validation_feedback=validation_feedback,
    )

    parts: list[dict[str, Any]] = [{"text": prompt}]
    if audio_path:
        try:
            data = Path(audio_path).read_bytes()
            transport_meta["audio_segmentation_inline_attempted"] = True
            transport_meta["audio_segmentation_inline_bytes_size"] = len(data)
            if len(data) <= _MAX_INLINE_AUDIO_BYTES:
                parts.append({"inlineData": {"mimeType": _guess_audio_mime(audio_path), "data": base64.b64encode(data).decode("ascii")}})
                transport_meta["audio_segmentation_source_mode"] = "inline_bytes"
            elif audio_url and not _is_local_or_private_url(audio_url):
                parts.append({"fileData": {"mimeType": _guess_audio_mime(audio_path), "fileUri": str(audio_url).strip()}})
                transport_meta["audio_segmentation_source_mode"] = "public_url"
                transport_meta["audio_segmentation_url_used"] = str(audio_url).strip()
            else:
                return _fail("audio_too_large_no_public_url")
        except Exception as exc:  # noqa: BLE001
            logger.exception("[audio_scene_segmenter] failed to attach audio")
            return _fail(f"audio_attach_failed:{exc}")
    elif audio_url and not _is_local_or_private_url(audio_url):
        parts.append({"fileData": {"mimeType": "audio/mpeg", "fileUri": str(audio_url).strip()}})
        transport_meta["audio_segmentation_source_mode"] = "public_url"
        transport_meta["audio_segmentation_url_used"] = str(audio_url).strip()
    else:
        return _fail("audio_source_missing_or_private_url")

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
        return _fail(f"gemini_http_error:{response.get('status')}:{response.get('text')}")

    raw_text = _extract_gemini_text(response)
    parsed = _extract_json_obj(raw_text)
    if not parsed:
        return _fail("gemini_json_parse_failed", raw_text=raw_text)

    return {
        "ok": True,
        "prompt_version": GEMINI_SEGMENTATION_PROMPT_VERSION,
        "payload": parsed,
        "raw_text": raw_text,
        "parsed_json": parsed,
        "used_model": GEMINI_SEGMENTATION_MODEL,
        "transport_meta": dict(transport_meta),
    }
