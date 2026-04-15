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

GEMINI_SEGMENTATION_PROMPT_VERSION = "gemini_audio_map_v1_2_phrase_safe"
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
    validation_feedback: str = "",
) -> str:
    evidence = {
        "audio_id": audio_id,
        "duration_sec": round(float(duration_sec or 0.0), 3),
        "transcript_excerpt": str(transcript_text or "")[:3000],
        "dynamics_summary": dynamics_summary,
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
        '  "segments": [{"segment_id": "string", "t0": 0.0, "t1": 1.0, "transcript_slice": "string", "intensity": 0.0, "is_lip_sync_candidate": false, "rhythmic_anchor": "beat"}],\n'
        '  "no_split_ranges": [{"start": 0.0, "end": 0.0}],\n'
        '  "diagnostics": {"total_segments_duration": 0.0, "coverage_ok": true, "energy_peak_detected": false, "transcript_used": false, "dynamics_used": false, "validation_notes": []}\n'
        "}\n"
        "Rules:\n"
        "- segments must be ordered and contiguous without overlaps/gaps outside tiny tolerance.\n"
        "- Never round boundaries to whole seconds just because they look neat.\n"
        "- Use natural sub-second precision; do not bias toward integer timestamps.\n"
        "- Do not cut a sung phrase before it is acoustically complete.\n"
        "- Respect final consonants, vocal tail, breath release, and reverb decay before ending a phrase segment.\n"
        "- A ~0.5s early vocal cut is considered bad segmentation and must be avoided.\n"
        "- Phrase endings must follow sung completion, not rough text chunk boundaries.\n"
        "- Prefer musical phrase closure over evenly spaced or visually pretty timing.\n"
        "- Do not split inside a continuing sung phrase.\n"
        "- 0 <= intensity <= 1.\n"
        "- rhythmic_anchor must be one of: beat, drop, transition, none.\n"
        "- transcript_slice must be literal transcript evidence snippets, never placeholders.\n"
        "- no_split_ranges must not conflict with segment boundaries.\n"
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
