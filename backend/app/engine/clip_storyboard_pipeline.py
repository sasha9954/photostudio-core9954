import json
import logging
import os
import re
import io
from base64 import b64encode, b64decode
from dataclasses import dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
import ipaddress
import mimetypes

import librosa
import soundfile as sf
import requests
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings
from app.engine.gemini_rest import post_generate_content

logger = logging.getLogger(__name__)

ALLOWED_CLIP_ROUTES = ("i2v", "ia2v", "first_last")
CLIP_PIPELINE_MODEL = "gemini-3.1-pro-preview"
WHOLE_MAP_RETRY_COUNT = 3
CHUNK_RETRY_COUNT = 3
REPAIR_RETRY_COUNT = 2
FULL_COVERAGE_TOLERANCE_SEC = 0.3
CLIP_REF_ROLES = ("character_1", "character_2", "character_3", "animal", "group", "location", "style", "props")
CLIP_CONTEXT_VERSION = "clip_pipeline_context_v1"
CHUNK_AUDIO_SLICE_OVERLAP_SEC = 1.5


class ClipPipelineError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 422, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class WholeTrackSection(BaseModel):
    section_id: str
    t0: float
    t1: float
    section_type: str
    energy: int = 5
    recurring_group_id: str | None = None
    suggested_visual_role: str = ""


class TimeRangeReason(BaseModel):
    t0: float
    t1: float
    reason: str = ""


class ChunkBoundary(BaseModel):
    chunk_id: str
    t0: float
    t1: float


class WholeTrackMapResponse(BaseModel):
    track_id: str
    mode: str
    duration_sec: float
    global_arc: str
    world_lock: dict[str, Any] = Field(default_factory=dict)
    identity_lock: dict[str, Any] = Field(default_factory=dict)
    style_lock: dict[str, Any] = Field(default_factory=dict)
    sections: list[WholeTrackSection] = Field(default_factory=list)
    no_split_ranges: list[TimeRangeReason] = Field(default_factory=list)
    suggested_chunk_boundaries: list[ChunkBoundary] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_clip_mode(self) -> "WholeTrackMapResponse":
        if self.mode != "clip":
            raise ValueError("mode must be clip")
        if self.duration_sec <= 0:
            raise ValueError("duration_sec must be > 0")
        return self


class ContinuityTailState(BaseModel):
    hero_state: str = ""
    location_state: str = ""
    camera_state: str = ""
    continuity_tokens: list[str] = Field(default_factory=list)


class ContinuityIn(BaseModel):
    previous_chunk_id: str | None = None
    tail_state: ContinuityTailState = Field(default_factory=ContinuityTailState)


class ChunkMapRef(BaseModel):
    section_ids: list[str] = Field(default_factory=list)
    recurring_group_ids: list[str] = Field(default_factory=list)


class ChunkStoryboardRequest(BaseModel):
    track_id: str
    mode: str = "clip"
    chunk_id: str
    t0: float
    t1: float
    allowed_scene_routes: list[str] = Field(default_factory=lambda: list(ALLOWED_CLIP_ROUTES))
    global_map_ref: ChunkMapRef = Field(default_factory=ChunkMapRef)
    continuity_in: ContinuityIn = Field(default_factory=ContinuityIn)
    creative_note: str = ""
    identity_lock: bool = True
    world_lock: bool = True
    style_lock: bool = True


class ClipScene(BaseModel):
    scene_id: str
    t0: float
    t1: float
    section_type: str
    route: str
    goal: str
    continuity_tokens: list[str] = Field(default_factory=list)
    is_boundary_scene: bool = False
    recurring_group_id: str | None = None
    frame_prompt: str | None = None
    camera_prompt: str | None = None
    motion_prompt: str | None = None
    first_frame_prompt: str | None = None
    last_frame_prompt: str | None = None
    transition_prompt: str | None = None


class ChunkStoryboardResponse(BaseModel):
    track_id: str
    mode: str
    chunk_id: str
    t0: float
    t1: float
    continuity_out: dict[str, Any] = Field(default_factory=dict)
    scenes: list[ClipScene] = Field(default_factory=list)


@dataclass
class MergeIssue:
    code: str
    message: str
    chunk_left: str | None = None
    chunk_right: str | None = None


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp.get("candidates"), list) else []
    if not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
    parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
    out = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            out.append(part.get("text") or "")
    return "\n".join(out).strip()


def _extract_json(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    first, last = text.find("{"), text.rfind("}")
    if first >= 0 and last > first:
        try:
            return json.loads(text[first : last + 1])
        except Exception:
            return None
    return None


def _estimate_request_payload_metrics(body: dict[str, Any]) -> dict[str, Any]:
    audio_part_size_bytes = 0
    refs_total_size_bytes = 0
    total_inline_parts = 0
    contents = body.get("contents") if isinstance(body.get("contents"), list) else []
    for content in contents:
        parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
        for idx, part in enumerate(parts):
            inline = part.get("inlineData") if isinstance(part, dict) and isinstance(part.get("inlineData"), dict) else {}
            if not inline:
                continue
            total_inline_parts += 1
            encoded = str(inline.get("data") or "")
            approx_bytes = int((len(encoded) * 3) / 4) if encoded else 0
            text_hint = str((parts[idx - 1].get("text") if idx > 0 and isinstance(parts[idx - 1], dict) else "") or "").lower()
            if "master audio" in text_hint:
                audio_part_size_bytes += approx_bytes
            else:
                refs_total_size_bytes += approx_bytes
    schema_enabled = bool(((body.get("generationConfig") or {}).get("responseJsonSchema")) if isinstance(body.get("generationConfig"), dict) else False)
    whole_map_request_size_estimate = len(json.dumps(body, ensure_ascii=False))
    return {
        "audio_part_size_bytes": audio_part_size_bytes,
        "refs_total_size_bytes": refs_total_size_bytes,
        "total_inline_parts": total_inline_parts,
        "whole_map_request_size_estimate": whole_map_request_size_estimate,
        "schema_enabled": schema_enabled,
    }


def _call_gemini_json(
    *,
    api_key: str,
    body: dict[str, Any],
    retry_count: int = 3,
    stage: str = "unknown",
    chunk_id: str | None = None,
    request_started: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    last_error = "gemini_invalid_json"
    request_finished = False
    diagnostics: dict[str, Any] = {"model": CLIP_PIPELINE_MODEL, "retries": 0, "stage": stage, "chunk_id": chunk_id}
    req_metrics = _estimate_request_payload_metrics(body)
    diagnostics.update(req_metrics)
    logger.info(
        "[CLIP PIPELINE GEMINI PAYLOAD] stage=%s chunk_id=%s schema_enabled=%s audio_bytes=%s refs_bytes=%s inline_parts=%s request_size_estimate=%s",
        stage,
        chunk_id,
        req_metrics.get("schema_enabled"),
        req_metrics.get("audio_part_size_bytes"),
        req_metrics.get("refs_total_size_bytes"),
        req_metrics.get("total_inline_parts"),
        req_metrics.get("whole_map_request_size_estimate"),
    )
    if int(req_metrics.get("whole_map_request_size_estimate") or 0) > 15_000_000:
        logger.warning(
            "[CLIP PIPELINE GEMINI PAYLOAD DIAGNOSTICS] stage=%s chunk_id=%s audio_part_size_bytes=%s refs_total_size_bytes=%s total_inline_parts=%s whole_map_request_size_estimate=%s",
            stage,
            chunk_id,
            req_metrics.get("audio_part_size_bytes"),
            req_metrics.get("refs_total_size_bytes"),
            req_metrics.get("total_inline_parts"),
            req_metrics.get("whole_map_request_size_estimate"),
        )
    for attempt in range(retry_count):
        logger.info(
            "[CLIP PIPELINE GEMINI CALL] stage=%s chunk_id=%s attempt=%s event=before_post_generate_content request_started=%s",
            stage,
            chunk_id,
            attempt + 1,
            request_started,
        )
        try:
            resp = post_generate_content(api_key, CLIP_PIPELINE_MODEL, body, timeout=120)
            request_finished = True
        except Exception as exc:
            diagnostics["retries"] = attempt + 1
            last_error = "gemini_request_exception"
            logger.exception(
                "[CLIP PIPELINE GEMINI CALL] stage=%s chunk_id=%s attempt=%s event=after_post_generate_content_exception error=%s",
                stage,
                chunk_id,
                attempt + 1,
                str(exc),
            )
            continue
        http_error = not isinstance(resp, dict) or resp.get("status") not in {None, 200}
        diagnostics["retries"] = attempt + 1
        logger.info(
            "[CLIP PIPELINE GEMINI CALL] stage=%s chunk_id=%s attempt=%s event=after_post_generate_content response_status=%s httpError=%s",
            stage,
            chunk_id,
            attempt + 1,
            resp.get("status") if isinstance(resp, dict) else "unknown",
            http_error,
        )
        if http_error:
            status = resp.get("status") if isinstance(resp, dict) else "unknown"
            last_error = f"gemini_http_error:{status}"
            if isinstance(resp, dict):
                diagnostics["geminiError"] = {
                    "status": resp.get("status"),
                    "message": resp.get("message"),
                    "text": resp.get("text"),
                    "error": resp.get("error"),
                    "body": resp.get("body"),
                }
                if isinstance(status, int) and 400 <= status <= 599:
                    schema = ((body.get("generationConfig") or {}).get("responseJsonSchema") if isinstance(body.get("generationConfig"), dict) else None)
                    logger.warning(
                        "clip_pipeline_gemini_http_error status=%s model=%s schema=%s error=%s",
                        status,
                        CLIP_PIPELINE_MODEL,
                        json.dumps(schema, ensure_ascii=False) if isinstance(schema, dict) else None,
                        json.dumps(diagnostics.get("geminiError"), ensure_ascii=False),
                    )
            continue
        raw = _extract_gemini_text(resp)
        parsed = _extract_json(raw)
        logger.info(
            "[CLIP PIPELINE GEMINI CALL] stage=%s chunk_id=%s attempt=%s response_status=%s httpError=%s parsed_text_exists=%s parsed_json_exists=%s",
            stage,
            chunk_id,
            attempt + 1,
            resp.get("status") if isinstance(resp, dict) else "unknown",
            http_error,
            bool(raw),
            isinstance(parsed, dict),
        )
        if isinstance(parsed, dict):
            return parsed, diagnostics
        last_error = "gemini_invalid_or_truncated_json"
    raise ClipPipelineError(
        "retryable_fail",
        "Gemini returned invalid JSON.",
        status_code=502,
        details={
            "reason": last_error,
            "stage": stage,
            "chunk_id": chunk_id,
            "attempt": diagnostics.get("retries"),
            "request_started": request_started,
            "request_finished": request_finished,
            **req_metrics,
            **diagnostics,
        },
    )


def _is_local_or_private_url(url: str) -> bool:
    raw = str(url or "").strip()
    if not raw:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        return True
    if host.endswith(".local") or host.endswith(".internal") or host.endswith(".lan") or host.endswith(".home") or host.endswith(".ts.net"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return not ip.is_global
    except Exception:
        return False


def _guess_mime_type(url: str, fallback_mime: str) -> str:
    parsed = urlparse(str(url or "").strip())
    guessed = mimetypes.guess_type((parsed.path or "").strip())[0]
    return str(guessed or fallback_mime or "application/octet-stream")


def _try_read_local_static_asset(url: str) -> bytes | None:
    raw = str(url or "").strip()
    if not raw:
        return None
    parsed = urlparse(raw)
    path = str(parsed.path or "").lstrip("/")
    if not path.startswith("static/assets/"):
        return None
    filename = path.split("static/assets/", 1)[1]
    if not filename:
        return None
    assets_dir = Path(__file__).resolve().parents[2] / "static" / "assets"
    candidate = (assets_dir / filename).resolve()
    assets_root = assets_dir.resolve()
    if assets_root not in candidate.parents and candidate != assets_root:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate.read_bytes()


def _fetch_media_bytes(url: str) -> bytes:
    media_url = str(url or "").strip()
    parsed = urlparse(media_url)
    normalized_path = str(parsed.path or "").lstrip("/")
    is_static_asset_path = normalized_path.startswith("static/assets/")
    is_local_or_private = _is_local_or_private_url(media_url)
    assets_dir = Path(__file__).resolve().parents[2] / "static" / "assets"
    local_path = str((assets_dir / normalized_path.split("static/assets/", 1)[1]).resolve()) if is_static_asset_path else None

    local_bytes = _try_read_local_static_asset(media_url)
    if local_bytes is not None:
        logger.warning(
            "[CLIP PIPELINE MEDIA READ] url=%s local_path=%s used_local=%s fallback_http=%s reason=%s",
            media_url,
            local_path,
            True,
            False,
            "local_static_asset_read_success",
        )
        return local_bytes

    if is_static_asset_path and is_local_or_private:
        logger.warning(
            "[CLIP PIPELINE MEDIA READ] url=%s local_path=%s used_local=%s fallback_http=%s reason=%s",
            media_url,
            local_path,
            False,
            False,
            "local_static_asset_not_found",
        )
        raise FileNotFoundError(f"local static asset not found: {media_url}")

    logger.warning(
        "[CLIP PIPELINE MEDIA READ] url=%s local_path=%s used_local=%s fallback_http=%s reason=%s",
        media_url,
        local_path,
        False,
        True,
        "fallback_http_fetch",
    )
    resp = requests.get(media_url, timeout=30)
    resp.raise_for_status()
    return resp.content


def _build_inline_media_part_from_url(url: str, *, fallback_mime: str) -> dict[str, Any]:
    media_url = str(url or "").strip()
    if not media_url:
        return {}
    try:
        raw = _fetch_media_bytes(media_url)
        mime = _guess_mime_type(media_url, fallback_mime)
        return {"inlineData": {"mimeType": mime, "data": b64encode(raw).decode("ascii")}}
    except Exception:
        logger.warning("clip_pipeline_inline_media_failed media_url=%s", media_url, exc_info=True)
        return {}


def _normalize_media_to_inline_part(url: str, *, fallback_mime: str) -> dict[str, Any]:
    media_url = str(url or "").strip()
    if not media_url:
        return {}
    if _is_local_or_private_url(media_url):
        logger.info(
            "clip_pipeline_media_transport_fallback media_url=%s reason=local_or_private_url_not_allowed_for_gemini_fileUri fallback_transport=inlineData",
            media_url,
        )
    return _build_inline_media_part_from_url(media_url, fallback_mime=fallback_mime)


def _decode_audio_inline_bytes(context: dict[str, Any]) -> tuple[bytes, str]:
    audio_source = context.get("audio_source") if isinstance(context.get("audio_source"), dict) else {}
    audio_part = audio_source.get("media_part") if isinstance(audio_source.get("media_part"), dict) else {}
    inline = audio_part.get("inlineData") if isinstance(audio_part.get("inlineData"), dict) else {}
    data = str(inline.get("data") or "").strip()
    if data:
        mime = str(inline.get("mimeType") or "audio/mpeg").strip() or "audio/mpeg"
        return (b64decode(data), mime)
    audio_url = str(context.get("audio_url") or (audio_source.get("uri") if isinstance(audio_source, dict) else "") or "").strip()
    if not audio_url:
        return (b"", "")
    raw = _fetch_media_bytes(audio_url)
    return (raw, _guess_mime_type(audio_url, "audio/mpeg"))


def _get_or_build_decoded_master_audio(context: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    cache = context.get("decoded_master_audio_cache")
    if isinstance(cache, dict) and cache.get("ready"):
        return cache, {"decoded_master_audio_cached": True, "decoded_master_audio_build_sec": 0.0, "decoded_master_audio_reused_for_chunks": True}
    if not isinstance(cache, dict):
        cache = {}
        context["decoded_master_audio_cache"] = cache
    started = perf_counter()
    try:
        raw, mime = _decode_audio_inline_bytes(context)
        if not raw:
            cache.update({"ready": False, "error": "master_audio_unavailable"})
            return cache, {
                "decoded_master_audio_cached": False,
                "decoded_master_audio_build_sec": round(max(0.0, perf_counter() - started), 3),
                "decoded_master_audio_reused_for_chunks": False,
                "error": "master_audio_unavailable",
            }
        audio, sr = librosa.load(io.BytesIO(raw), sr=None, mono=False)
        total_samples = int(audio.shape[-1]) if hasattr(audio, "shape") else 0
        if total_samples <= 0 or sr <= 0:
            cache.update({"ready": False, "error": "decoded_audio_empty"})
            return cache, {
                "decoded_master_audio_cached": False,
                "decoded_master_audio_build_sec": round(max(0.0, perf_counter() - started), 3),
                "decoded_master_audio_reused_for_chunks": False,
                "error": "decoded_audio_empty",
            }
        duration_sec = max(0.0, total_samples / float(sr))
        channels = int(audio.shape[0]) if getattr(audio, "ndim", 1) > 1 else 1
        cache.update(
            {
                "ready": True,
                "audio": audio,
                "sample_rate": int(sr),
                "total_samples": total_samples,
                "duration_sec": round(duration_sec, 6),
                "channels": channels,
                "source_mime": mime,
                "build_sec": round(max(0.0, perf_counter() - started), 3),
            }
        )
        return cache, {
            "decoded_master_audio_cached": True,
            "decoded_master_audio_build_sec": cache.get("build_sec", 0.0),
            "decoded_master_audio_reused_for_chunks": False,
        }
    except Exception:
        logger.warning("clip_pipeline_master_audio_decode_failed", exc_info=True)
        cache.update({"ready": False, "error": "decode_failed"})
        return cache, {
            "decoded_master_audio_cached": False,
            "decoded_master_audio_build_sec": round(max(0.0, perf_counter() - started), 3),
            "decoded_master_audio_reused_for_chunks": False,
            "error": "decode_failed",
        }


def _sanitize_response_context(context: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(context, dict):
        return {}
    sanitized = dict(context)
    sanitized.pop("decoded_master_audio_cache", None)
    return sanitized


def _build_chunk_audio_slice(
    *,
    decoded_audio_cache: dict[str, Any],
    chunk_t0: float,
    chunk_t1: float,
    track_duration_sec: float,
    overlap_sec: float = CHUNK_AUDIO_SLICE_OVERLAP_SEC,
) -> tuple[dict[str, Any], dict[str, Any]]:
    build_started = perf_counter()
    t0 = max(0.0, float(chunk_t0) - overlap_sec)
    t1 = max(t0, float(chunk_t1) + overlap_sec)
    if track_duration_sec > 0:
        t1 = min(float(track_duration_sec), t1)
        t0 = min(t0, t1)
    try:
        if not bool(decoded_audio_cache.get("ready")):
            return {}, {"local_audio_slice": False, "slice_t0": round(t0, 3), "slice_t1": round(t1, 3), "error": str(decoded_audio_cache.get("error") or "master_audio_unavailable"), "chunk_audio_slice_build_sec": round(max(0.0, perf_counter() - build_started), 3)}
        audio = decoded_audio_cache.get("audio")
        sr = int(decoded_audio_cache.get("sample_rate") or 0)
        total_samples = int(decoded_audio_cache.get("total_samples") or 0)
        if audio is None or total_samples <= 0 or sr <= 0:
            return {}, {"local_audio_slice": False, "slice_t0": round(t0, 3), "slice_t1": round(t1, 3), "error": "decoded_audio_empty", "chunk_audio_slice_build_sec": round(max(0.0, perf_counter() - build_started), 3)}
        real_track_duration = max(0.0, total_samples / float(sr))
        t1 = min(t1, real_track_duration)
        start_idx = int(max(0, round(t0 * sr)))
        end_idx = int(min(total_samples, round(t1 * sr)))
        if end_idx <= start_idx:
            return {}, {"local_audio_slice": False, "slice_t0": round(t0, 3), "slice_t1": round(t1, 3), "error": "slice_bounds_invalid", "chunk_audio_slice_build_sec": round(max(0.0, perf_counter() - build_started), 3)}
        sliced = audio[..., start_idx:end_idx]
        buff = io.BytesIO()
        sf.write(buff, sliced.T if getattr(sliced, "ndim", 1) > 1 else sliced, sr, format="WAV", subtype="PCM_16")
        out = buff.getvalue()
        part = {"inlineData": {"mimeType": "audio/wav", "data": b64encode(out).decode("ascii")}}
        return part, {
            "local_audio_slice": True,
            "slice_t0": round(t0, 3),
            "slice_t1": round(t1, 3),
            "audio_part_size_bytes": len(out),
            "slice_overlap_sec": overlap_sec,
            "chunk_audio_slice_build_sec": round(max(0.0, perf_counter() - build_started), 3),
        }
    except Exception:
        logger.warning("clip_pipeline_chunk_audio_slice_failed", exc_info=True)
        return {}, {"local_audio_slice": False, "slice_t0": round(t0, 3), "slice_t1": round(t1, 3), "error": "slice_generation_failed", "chunk_audio_slice_build_sec": round(max(0.0, perf_counter() - build_started), 3)}


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _extract_refs_by_role(payload: dict[str, Any]) -> dict[str, list[str]]:
    refs_out: dict[str, list[str]] = {role: [] for role in CLIP_REF_ROLES}
    connected = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    for role in CLIP_REF_ROLES:
        src = connected.get(role) if isinstance(connected.get(role), dict) else {}
        refs = [str(item).strip() for item in (src.get("refs") if isinstance(src.get("refs"), list) else []) if str(item).strip()]
        if refs:
            refs_out[role] = refs
    return refs_out


def _prepare_clip_pipeline_context(payload: dict[str, Any], provided_context: dict[str, Any] | None = None) -> dict[str, Any]:
    ctx = provided_context if isinstance(provided_context, dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    audio_url = str(
        ctx.get("audio_url")
        or ((ctx.get("audio") or {}).get("audio_url") if isinstance(ctx.get("audio"), dict) else "")
        or source.get("source_value")
        or payload.get("audioUrl")
        or ""
    ).strip()
    refs_by_role = ctx.get("refs_by_role") if isinstance(ctx.get("refs_by_role"), dict) else _extract_refs_by_role(payload)
    audio_part = (ctx.get("audio") or {}).get("media_part") if isinstance(ctx.get("audio"), dict) else {}
    if not isinstance(audio_part, dict) or not audio_part:
        audio_part = _normalize_media_to_inline_part(audio_url, fallback_mime="audio/mpeg")
    refs_media: dict[str, list[dict[str, Any]]] = {}
    for role in CLIP_REF_ROLES:
        role_refs = refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []
        role_parts: list[dict[str, Any]] = []
        for ref_url in role_refs[:6]:
            role_parts.append(_normalize_media_to_inline_part(str(ref_url), fallback_mime="image/jpeg"))
        refs_media[role] = [part for part in role_parts if part]
    cache_handle = str(ctx.get("cache_handle") or ctx.get("cache_id") or "").strip()
    cache_id = str(ctx.get("cache_id") or cache_handle).strip()
    created_at = str(ctx.get("created_at") or "").strip() or _now_iso()
    has_audio = bool(audio_part)
    has_refs = any(refs_media.get(role) for role in CLIP_REF_ROLES)
    if cache_handle:
        context_state = "prepared_remote_cache_context"
        used_context_mode = "remote_cache_context"
    elif has_audio:
        context_state = "prepared_local_media_context"
        used_context_mode = "local_media_context"
    else:
        context_state = "missing_audio_media"
        used_context_mode = "local_media_context"
    refs_contract = {
        role: {
            "handles": [str(ref).strip() for ref in (refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []) if str(ref).strip()],
            "uris": [str(ref).strip() for ref in (refs_by_role.get(role) if isinstance(refs_by_role.get(role), list) else []) if str(ref).strip()],
            "media_parts": refs_media.get(role) if isinstance(refs_media.get(role), list) else [],
        }
        for role in CLIP_REF_ROLES
    }
    return {
        "context_version": CLIP_CONTEXT_VERSION,
        "context_state": context_state,
        "context_status": context_state,
        "used_context_mode": used_context_mode,
        "is_reusable": bool(has_audio or cache_handle),
        "created_at": created_at,
        "updated_at": _now_iso(),
        "cache_handle": cache_handle or None,
        "cache_id": cache_id or None,
        "audio_source": {
            "handle": str(ctx.get("audio_source_handle") or "master_track"),
            "uri": audio_url,
            "file_reference": str(ctx.get("audio_file_reference") or audio_url),
            "attached": has_audio,
            "media_part": audio_part,
        },
        "audio_url": audio_url,
        "refs_by_role": refs_by_role,
        "refs_contract": refs_contract,
        "refs_media_parts_by_role": refs_media,
        "refs_roles_attached": [role for role in CLIP_REF_ROLES if refs_contract.get(role, {}).get("media_parts")],
        "audio_media_attached": has_audio,
        "has_refs_media": has_refs,
        "system_instruction": "clip mode music video production storyboard",
    }


def _normalize_clip_context(payload: dict[str, Any], provided_context: dict[str, Any] | None = None) -> dict[str, Any]:
    return _prepare_clip_pipeline_context(payload, provided_context)


def _build_context_diagnostics(context: dict[str, Any], *, stage: str, chunk_id: str | None = None) -> dict[str, Any]:
    return {
        "stage": stage,
        "chunk_id": chunk_id,
        "context_version": context.get("context_version"),
        "context_state": context.get("context_state"),
        "used_context_mode": context.get("used_context_mode"),
        "is_reusable": bool(context.get("is_reusable")),
        "audio_media_attached": bool(context.get("audio_media_attached")),
        "ref_roles_attached": list(context.get("refs_roles_attached") or []),
        "cache_handle_present": bool(context.get("cache_handle")),
    }


def _build_scene_schema(*, required_only: bool = False) -> dict[str, Any]:
    prompt_fields = {
        "frame_prompt": {"type": "string"},
        "camera_prompt": {"type": "string"},
        "motion_prompt": {"type": "string"},
        "first_frame_prompt": {"type": "string"},
        "last_frame_prompt": {"type": "string"},
        "transition_prompt": {"type": "string"},
    }
    base = {
        "type": "object",
        "properties": {
            "scene_id": {"type": "string"},
            "t0": {"type": "number"},
            "t1": {"type": "number"},
            "section_type": {"type": "string"},
            "route": {"type": "string", "enum": list(ALLOWED_CLIP_ROUTES)},
            "goal": {"type": "string"},
            "continuity_tokens": {"type": "array", "items": {"type": "string"}},
            "is_boundary_scene": {"type": "boolean"},
            "recurring_group_id": {"type": "string"},
            **prompt_fields,
        },
        "required": ["scene_id", "t0", "t1", "section_type", "route", "goal"],
    }
    if required_only:
        return {k: v for k, v in base.items() if k != "additionalProperties"}
    return base


def _build_whole_track_map_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "track_id": {"type": "string"},
            "mode": {"type": "string", "enum": ["clip"]},
            "duration_sec": {"type": "number"},
            "global_arc": {"type": "string"},
            "world_lock": {"type": "object"},
            "identity_lock": {"type": "object"},
            "style_lock": {"type": "object"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "section_id": {"type": "string"},
                        "t0": {"type": "number"},
                        "t1": {"type": "number"},
                        "section_type": {"type": "string"},
                        "energy": {"type": "integer"},
                        "recurring_group_id": {"type": "string"},
                        "suggested_visual_role": {"type": "string"},
                    },
                    "required": ["section_id", "t0", "t1", "section_type"],
                },
            },
            "no_split_ranges": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"t0": {"type": "number"}, "t1": {"type": "number"}, "reason": {"type": "string"}},
                    "required": ["t0", "t1"],
                },
            },
            "suggested_chunk_boundaries": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"chunk_id": {"type": "string"}, "t0": {"type": "number"}, "t1": {"type": "number"}},
                    "required": ["chunk_id", "t0", "t1"],
                },
            },
        },
        "required": ["track_id", "mode", "duration_sec", "global_arc", "sections"],
    }


def _build_chunk_response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "track_id": {"type": "string"},
            "mode": {"type": "string", "enum": ["clip"]},
            "chunk_id": {"type": "string"},
            "t0": {"type": "number"},
            "t1": {"type": "number"},
            "continuity_out": {"type": "object"},
            "scenes": {"type": "array", "items": _build_scene_schema()},
        },
        "required": ["track_id", "mode", "chunk_id", "t0", "t1", "scenes"],
    }


def _build_repair_response_schema() -> dict[str, Any]:
    edge_scene_schema = _build_scene_schema(required_only=True)
    return {
        "type": "object",
        "properties": {
            "repaired_chunk_edges": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "left_scene": edge_scene_schema,
                        "right_scene": edge_scene_schema,
                    },
                },
            },
            "transition_scene": _build_scene_schema(required_only=True),
        },
    }


def _validate_chunk_response(chunk: ChunkStoryboardResponse) -> None:
    if chunk.mode != "clip":
        raise ClipPipelineError("retryable_fail", "chunk mode is not clip", details={"chunk_id": chunk.chunk_id})
    if not chunk.scenes:
        raise ClipPipelineError("retryable_fail", "empty scenes", details={"chunk_id": chunk.chunk_id})
    for scene in chunk.scenes:
        if scene.route not in ALLOWED_CLIP_ROUTES:
            raise ClipPipelineError("retryable_fail", "invalid scene route", details={"route": scene.route, "chunk_id": chunk.chunk_id})
        if scene.t1 <= scene.t0:
            raise ClipPipelineError("retryable_fail", "invalid scene timestamps", details={"scene_id": scene.scene_id, "chunk_id": chunk.chunk_id})
        if scene.route in {"i2v", "ia2v"}:
            if not (str(scene.frame_prompt or "").strip() and str(scene.camera_prompt or "").strip() and str(scene.motion_prompt or "").strip()):
                raise ClipPipelineError("retryable_fail", "missing i2v/ia2v prompts", details={"scene_id": scene.scene_id, "chunk_id": chunk.chunk_id})
        if scene.route == "first_last":
            if not (str(scene.first_frame_prompt or "").strip() and str(scene.last_frame_prompt or "").strip() and str(scene.transition_prompt or "").strip()):
                raise ClipPipelineError("retryable_fail", "missing first_last prompts", details={"scene_id": scene.scene_id, "chunk_id": chunk.chunk_id})


_GENERIC_SCENE_ID_RE = re.compile(r"^(?:scene|sc|s)?_?\d+$", re.IGNORECASE)


def _canonical_clip_scene_id(chunk_id: str, scene_index: int, old_scene_id: str) -> str:
    base_id = f"{chunk_id}_scene_{scene_index + 1:02d}"
    old = str(old_scene_id or "").strip()
    if not old or _GENERIC_SCENE_ID_RE.match(old):
        return base_id
    suffix = re.sub(r"[^a-z0-9]+", "_", old.lower()).strip("_")
    if not suffix or suffix == base_id:
        return base_id
    return f"{base_id}_{suffix[:48]}"


def _canonicalize_chunk_scene_ids(chunk: ChunkStoryboardResponse) -> tuple[ChunkStoryboardResponse, dict[str, Any]]:
    scene_id_map: dict[str, str] = {}
    canonicalized_scenes: list[ClipScene] = []
    applied = 0
    for idx, scene in enumerate(chunk.scenes):
        old_scene_id = str(scene.scene_id or "").strip()
        new_scene_id = _canonical_clip_scene_id(chunk.chunk_id, idx, old_scene_id)
        if new_scene_id != old_scene_id:
            applied += 1
        scene_payload = scene.model_dump(mode="json", exclude_none=True)
        scene_payload["scene_id"] = new_scene_id
        canonicalized_scenes.append(ClipScene.model_validate(scene_payload))
        scene_id_map[old_scene_id or f"__empty__{idx + 1}"] = new_scene_id
    canonical_chunk = ChunkStoryboardResponse.model_validate(
        {
            **chunk.model_dump(mode="json", exclude_none=True),
            "scenes": [scene.model_dump(mode="json", exclude_none=True) for scene in canonicalized_scenes],
        }
    )
    return canonical_chunk, {"canonicalSceneIdApplied": applied, "canonicalSceneIdMap": scene_id_map}


def _flatten_ref_parts(ref_parts_by_role: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for role in ("character_1", "location", "style", "props", "character_2", "character_3", "animal", "group"):
        items = ref_parts_by_role.get(role) if isinstance(ref_parts_by_role.get(role), list) else []
        if items:
            out.append({"text": f"Reference role: {role}"})
        out.extend(items)
    return out


def _build_whole_track_map_request(payload: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    runtime = {
        "content_type": ((payload.get("director_controls") or {}).get("contentType") if isinstance(payload.get("director_controls"), dict) else ""),
        "audio_duration_sec": payload.get("audioDurationSec"),
        "refs_roles_present": [role for role, refs in (context.get("refs_by_role") or {}).items() if isinstance(refs, list) and refs],
        "context_state": context.get("context_state"),
        "used_context_mode": context.get("used_context_mode"),
    }
    parts: list[dict[str, Any]] = [
        {"text": "Build WholeTrackMapResponse JSON for clip mode only."},
        {"text": "No giant transcript. Keep lean map with sections/no_split_ranges/suggested_chunk_boundaries."},
        {"text": f"Runtime={json.dumps(runtime, ensure_ascii=False)}"},
    ]
    audio_part = ((context.get("audio_source") or {}).get("media_part") if isinstance(context.get("audio_source"), dict) else {})
    if isinstance(audio_part, dict) and audio_part:
        parts.append({"text": "Master audio input:"})
        parts.append(audio_part)
    parts.extend(_flatten_ref_parts(context.get("refs_media_parts_by_role") if isinstance(context.get("refs_media_parts_by_role"), dict) else {}))
    return {
        "systemInstruction": {"parts": [{"text": str(context.get("system_instruction") or "You are a production clip storyboard planner. Return strict JSON only.")}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseJsonSchema": _build_whole_track_map_schema(),
            "maxOutputTokens": 8192,
        },
    }


def _build_chunk_request(
    *,
    req: ChunkStoryboardRequest,
    whole_map: WholeTrackMapResponse,
    context: dict[str, Any],
    decoded_audio_cache: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    runtime = {
        "context": {
            "audio_url": context.get("audio_url"),
            "context_state": context.get("context_state"),
            "used_context_mode": context.get("used_context_mode"),
        },
        "whole_track_map": whole_map.model_dump(mode="json"),
        "chunk_request": req.model_dump(mode="json"),
    }
    parts: list[dict[str, Any]] = [
        {"text": "Return ChunkStoryboardResponse JSON for CLIP mode only."},
        {"text": "Allowed routes only: i2v, ia2v, first_last. No transcript/audioStructure/semanticTimeline."},
        {"text": f"Runtime={json.dumps(runtime, ensure_ascii=False)}"},
    ]
    chunk_audio_part, chunk_audio_diag = _build_chunk_audio_slice(
        decoded_audio_cache=decoded_audio_cache,
        chunk_t0=req.t0,
        chunk_t1=req.t1,
        track_duration_sec=float(whole_map.duration_sec or 0.0),
    )
    if isinstance(chunk_audio_part, dict) and chunk_audio_part:
        parts.append({"text": "Local chunk audio slice input (window + overlap):"})
        parts.append(chunk_audio_part)
    else:
        raise ClipPipelineError(
            "retryable_fail",
            "chunk local audio slice is required but unavailable",
            status_code=502,
            details={
                "chunk_id": req.chunk_id,
                "local_audio_slice": False,
                "slice_t0": chunk_audio_diag.get("slice_t0"),
                "slice_t1": chunk_audio_diag.get("slice_t1"),
                "reason": chunk_audio_diag.get("error") or "slice_unavailable",
            },
        )
    parts.extend(_flatten_ref_parts(context.get("refs_media_parts_by_role") if isinstance(context.get("refs_media_parts_by_role"), dict) else {}))
    body = {
        "systemInstruction": {"parts": [{"text": str(context.get("system_instruction") or "You are a production clip storyboard planner. Return strict JSON only.")}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "responseJsonSchema": _build_chunk_response_schema(),
            "maxOutputTokens": 8192,
        },
    }
    transport_diag = {
        "stage": "chunk",
        "chunk_id": req.chunk_id,
        "cached_context_used": False,
        "cache_handle_present": bool(context.get("cache_handle")),
        "duplicated_master_audio": False,
        "local_audio_slice": bool(chunk_audio_diag.get("local_audio_slice")),
        "local_audio_slice_t0": chunk_audio_diag.get("slice_t0"),
        "local_audio_slice_t1": chunk_audio_diag.get("slice_t1"),
        "chunk_audio_bytes": int(chunk_audio_diag.get("audio_part_size_bytes") or 0),
        "refs_reattached": bool(_flatten_ref_parts(context.get("refs_media_parts_by_role") if isinstance(context.get("refs_media_parts_by_role"), dict) else {})),
        "chunk_audio_error": chunk_audio_diag.get("error"),
        "decoded_master_audio_cached": bool(decoded_audio_cache.get("ready")),
        "decoded_master_audio_build_sec": float(decoded_audio_cache.get("build_sec") or 0.0),
        "decoded_master_audio_reused_for_chunks": bool(decoded_audio_cache.get("ready")),
        "chunk_audio_slice_build_sec": float(chunk_audio_diag.get("chunk_audio_slice_build_sec") or 0.0),
    }
    return body, transport_diag


def _plan_chunks(whole_map: WholeTrackMapResponse) -> list[ChunkBoundary]:
    if whole_map.suggested_chunk_boundaries:
        return whole_map.suggested_chunk_boundaries
    step = 30.0
    overlap = 3.0
    chunks: list[ChunkBoundary] = []
    t = 0.0
    idx = 1
    no_split = [(float(r.t0), float(r.t1)) for r in whole_map.no_split_ranges if r.t1 > r.t0]
    guard = 0
    while t < whole_map.duration_sec:
        guard += 1
        if guard > 10000:
            break
        end = min(whole_map.duration_sec, t + step)
        for r0, r1 in no_split:
            if r0 < end < r1:
                end = min(whole_map.duration_sec, r1)
                break
        if end <= t + 0.01:
            end = min(whole_map.duration_sec, t + max(1.0, step / 2))
        chunks.append(ChunkBoundary(chunk_id=f"ch_{idx:03d}", t0=round(t, 3), t1=round(end, 3)))
        next_t = max(0.0, end - overlap)
        if next_t <= t:
            next_t = end
        t = next_t
        idx += 1
    return chunks


def _scene_prompt_signature(scene_payload: dict[str, Any]) -> str:
    source = " ".join(
        str(scene_payload.get(key) or "").strip().lower()
        for key in ("goal", "frame_prompt", "camera_prompt", "motion_prompt", "first_frame_prompt", "last_frame_prompt", "transition_prompt")
    )
    return " ".join(source.split())


def _are_similar_boundary_scenes(left: dict[str, Any], right: dict[str, Any]) -> bool:
    same_route = str(left.get("route") or "") == str(right.get("route") or "")
    same_group = str(left.get("recurring_group_id") or "") == str(right.get("recurring_group_id") or "")
    span_close = abs(float(left.get("t0") or 0.0) - float(right.get("t0") or 0.0)) < 0.75 and abs(float(left.get("t1") or 0.0) - float(right.get("t1") or 0.0)) < 0.75
    ratio = SequenceMatcher(None, _scene_prompt_signature(left), _scene_prompt_signature(right)).ratio()
    return same_route and same_group and span_close and ratio >= 0.92


def _local_merge(track_id: str, responses: list[ChunkStoryboardResponse]) -> tuple[dict[str, Any], list[MergeIssue], dict[str, Any]]:
    issues: list[MergeIssue] = []
    ordered = sorted(responses, key=lambda x: (x.t0, x.t1, x.chunk_id))
    merged_scenes: list[dict[str, Any]] = []
    chunk_scene_map: dict[str, list[str]] = {}
    diagnostics: dict[str, Any] = {"dedup_exact": 0, "dedup_similarity": 0, "boundary_normalized": 0, "overlap_adjustments": 0}
    fingerprints: set[tuple[str, int, int, str, str]] = set()
    prev_end = 0.0
    for idx, chunk in enumerate(ordered):
        chunk_scene_map[chunk.chunk_id] = []
        if chunk.t0 > prev_end + 0.25 and idx > 0:
            issues.append(MergeIssue(code="gap", message="gap between chunks", chunk_left=ordered[idx - 1].chunk_id, chunk_right=chunk.chunk_id))
        if chunk.t0 < prev_end - 0.25 and idx > 0:
            issues.append(MergeIssue(code="overlap", message="overlap between chunks", chunk_left=ordered[idx - 1].chunk_id, chunk_right=chunk.chunk_id))
        for scene in sorted(chunk.scenes, key=lambda s: (s.t0, s.t1, s.scene_id)):
            scene_payload = scene.model_dump(mode="json", exclude_none=True)
            fingerprint = (
                str(scene_payload.get("route") or ""),
                int(round(float(scene_payload.get("t0") or 0.0) * 100)),
                int(round(float(scene_payload.get("t1") or 0.0) * 100)),
                str(scene_payload.get("recurring_group_id") or ""),
                _scene_prompt_signature(scene_payload),
            )
            if fingerprint in fingerprints:
                diagnostics["dedup_exact"] += 1
                continue
            if merged_scenes:
                prev = merged_scenes[-1]
                if abs(float(prev.get("t0", 0.0)) - scene.t0) < 0.01 and abs(float(prev.get("t1", 0.0)) - scene.t1) < 0.01:
                    diagnostics["dedup_exact"] += 1
                    continue
                if _are_similar_boundary_scenes(prev, scene_payload):
                    diagnostics["dedup_similarity"] += 1
                    continue
                if float(scene_payload.get("t0") or 0.0) < float(prev.get("t1") or 0.0):
                    scene_payload["t0"] = round(float(prev.get("t1") or 0.0), 3)
                    diagnostics["overlap_adjustments"] += 1
            fingerprints.add(fingerprint)
            merged_scenes.append(scene_payload)
            chunk_scene_map[chunk.chunk_id].append(scene.scene_id)
        prev_end = max(prev_end, chunk.t1)
    for i in range(1, len(merged_scenes)):
        left = merged_scenes[i - 1]
        right = merged_scenes[i]
        if left.get("is_boundary_scene") and right.get("is_boundary_scene"):
            out_tokens = left.get("continuity_tokens") if isinstance(left.get("continuity_tokens"), list) else []
            in_tokens = right.get("continuity_tokens") if isinstance(right.get("continuity_tokens"), list) else []
            normalized = list(dict.fromkeys([str(x).strip() for x in out_tokens + in_tokens if str(x).strip()]))
            left["continuity_tokens"] = normalized
            right["continuity_tokens"] = normalized
            diagnostics["boundary_normalized"] += 1
    merged = {
        "track_id": track_id,
        "mode": "clip",
        "scenes": merged_scenes,
        "chunk_scene_map": chunk_scene_map,
    }
    return merged, issues, diagnostics


def _build_repair_prompt(*, merged: dict[str, Any], issues: list[MergeIssue]) -> str:
    issue_rows = [issue.__dict__ for issue in issues]
    return (
        "Repair only chunk-boundary logic. Do not rewrite full storyboard. "
        "First rewrite adjacent edge scenes, suggest transition scene only if strictly required. "
        "Return JSON with keys: repaired_chunk_edges[] and optional transition_scene. "
        f"Runtime={json.dumps({'issues': issue_rows, 'merged': merged}, ensure_ascii=False)}"
    )


def _run_optional_repair(*, api_key: str, merged: dict[str, Any], issues: list[MergeIssue]) -> dict[str, Any]:
    if not issues:
        return {"applied": False, "issues": []}
    body = {
        "systemInstruction": {"parts": [{"text": "You repair chunk-boundary scenes only. Return strict JSON only."}]},
        "contents": [{"role": "user", "parts": [{"text": _build_repair_prompt(merged=merged, issues=issues)}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
            "responseJsonSchema": _build_repair_response_schema(),
            "maxOutputTokens": 4096,
        },
    }
    logger.info("[CLIP PIPELINE GEMINI] stage=repair event=request_start")
    parsed, _ = _call_gemini_json(api_key=api_key, body=body, retry_count=REPAIR_RETRY_COUNT, stage="repair", request_started=True)
    logger.info("[CLIP PIPELINE GEMINI] stage=repair event=request_done status=ok")
    logger.info("[CLIP PIPELINE GEMINI] stage=repair event=parse_start")
    logger.info("[CLIP PIPELINE GEMINI] stage=repair event=parse_done valid=%s", isinstance(parsed, dict))
    return {"applied": True, "issues": [issue.__dict__ for issue in issues], "result": parsed}


def _apply_repair_result(merged: dict[str, Any], repair_data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    scenes = [dict(scene) for scene in (merged.get("scenes") if isinstance(merged.get("scenes"), list) else [])]
    scene_index = {str(scene.get("scene_id") or ""): idx for idx, scene in enumerate(scenes) if str(scene.get("scene_id") or "").strip()}
    result = repair_data.get("result") if isinstance(repair_data.get("result"), dict) else {}
    edges = result.get("repaired_chunk_edges") if isinstance(result.get("repaired_chunk_edges"), list) else []
    applied_edges = 0
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        left_scene = edge.get("left_scene") if isinstance(edge.get("left_scene"), dict) else {}
        right_scene = edge.get("right_scene") if isinstance(edge.get("right_scene"), dict) else {}
        left_id = str(left_scene.get("scene_id") or "").strip()
        right_id = str(right_scene.get("scene_id") or "").strip()
        if left_id and left_id in scene_index:
            scenes[scene_index[left_id]] = left_scene
            applied_edges += 1
        if right_id and right_id in scene_index:
            scenes[scene_index[right_id]] = right_scene
            applied_edges += 1
    transition_applied = False
    transition_scene = result.get("transition_scene") if isinstance(result.get("transition_scene"), dict) else {}
    if transition_scene and str(transition_scene.get("scene_id") or "").strip():
        trans_id = str(transition_scene.get("scene_id") or "").strip()
        if trans_id not in scene_index:
            insert_at = len(scenes)
            t0 = float(transition_scene.get("t0") or 0.0)
            for idx, scene in enumerate(scenes):
                if float(scene.get("t0") or 0.0) > t0:
                    insert_at = idx
                    break
            scenes.insert(insert_at, transition_scene)
            transition_applied = True
    return {**merged, "scenes": scenes}, {"edge_rewrites_applied": applied_edges, "transition_scene_applied": transition_applied}


def _validate_merged_storyboard(merged: dict[str, Any]) -> dict[str, Any]:
    scenes = merged.get("scenes") if isinstance(merged.get("scenes"), list) else []
    errors: list[str] = []
    seen_ids: set[str] = set()
    prev_t0 = -1.0
    prev_t1 = -1.0
    for idx, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            errors.append(f"scene[{idx}] is not object")
            continue
        scene_id = str(scene.get("scene_id") or "").strip()
        route = str(scene.get("route") or "").strip()
        if not scene_id:
            errors.append(f"scene[{idx}] missing scene_id")
        elif scene_id in seen_ids:
            errors.append(f"duplicate scene_id={scene_id}")
        seen_ids.add(scene_id)
        if route not in ALLOWED_CLIP_ROUTES:
            errors.append(f"scene[{idx}] invalid route={route}")
        t0 = float(scene.get("t0") or 0.0)
        t1 = float(scene.get("t1") or 0.0)
        if t0 < 0 or t1 <= 0 or t1 <= t0:
            errors.append(f"scene[{idx}] invalid time range t0={t0}, t1={t1}")
        if idx > 0 and t0 < prev_t0:
            errors.append(f"scene[{idx}] out of order t0={t0} < previous_t0={prev_t0}")
        if idx > 0 and t0 < prev_t1:
            errors.append(f"scene[{idx}] overlap conflict t0={t0} < previous_t1={prev_t1}")
        if route in {"i2v", "ia2v"}:
            for field in ("frame_prompt", "camera_prompt", "motion_prompt"):
                if not str(scene.get(field) or "").strip():
                    errors.append(f"scene[{idx}] missing {field} for route={route}")
        if route == "first_last":
            for field in ("first_frame_prompt", "last_frame_prompt", "transition_prompt"):
                if not str(scene.get(field) or "").strip():
                    errors.append(f"scene[{idx}] missing {field} for route=first_last")
        continuity_tokens = scene.get("continuity_tokens")
        if continuity_tokens is not None and not isinstance(continuity_tokens, list):
            errors.append(f"scene[{idx}] continuity_tokens must be list")
        prev_t0 = t0
        prev_t1 = t1
    return {"valid": not errors, "scene_count": len(scenes), "errors": errors}


def _validate_repair_applied_storyboard(pre_repair: dict[str, Any], post_repair: dict[str, Any]) -> dict[str, Any]:
    post_validation = _validate_merged_storyboard(post_repair)
    pre_count = len(pre_repair.get("scenes") if isinstance(pre_repair.get("scenes"), list) else [])
    post_count = len(post_repair.get("scenes") if isinstance(post_repair.get("scenes"), list) else [])
    if post_count <= 0:
        post_validation["errors"].append("repair produced empty storyboard")
    return {
        "valid": bool(post_validation.get("valid")),
        "pre_scene_count": pre_count,
        "post_scene_count": post_count,
        "errors": list(post_validation.get("errors") or []),
    }


def _final_scene_end(merged: dict[str, Any]) -> float:
    scenes = merged.get("scenes") if isinstance(merged.get("scenes"), list) else []
    if not scenes:
        return 0.0
    return max(float(scene.get("t1") or 0.0) for scene in scenes if isinstance(scene, dict))


def _ensure_route_prompts(scene: dict[str, Any]) -> dict[str, Any]:
    route = str(scene.get("route") or "").strip()
    goal = str(scene.get("goal") or "").strip()
    frame_prompt = str(scene.get("frame_prompt") or goal or "Cinematic frame aligned with music phrase.").strip()
    camera_prompt = str(scene.get("camera_prompt") or "Cinematic camera with readable motion.").strip()
    motion_prompt = str(scene.get("motion_prompt") or "Beat-synced movement with continuity.").strip()
    scene["frame_prompt"] = frame_prompt
    scene["camera_prompt"] = camera_prompt
    scene["motion_prompt"] = motion_prompt
    if route == "first_last":
        scene["first_frame_prompt"] = str(scene.get("first_frame_prompt") or frame_prompt).strip()
        scene["last_frame_prompt"] = str(scene.get("last_frame_prompt") or frame_prompt).strip()
        scene["transition_prompt"] = str(scene.get("transition_prompt") or motion_prompt).strip()
    return scene


def _apply_clip_route_mix_policy(merged: dict[str, Any], whole_map: WholeTrackMapResponse) -> tuple[dict[str, Any], dict[str, Any]]:
    scenes = [dict(scene) for scene in (merged.get("scenes") if isinstance(merged.get("scenes"), list) else []) if isinstance(scene, dict)]
    if not scenes:
        return merged, {"changed": 0, "route_counts": {}}

    def _route_for_scene(scene: dict[str, Any]) -> str:
        text = f"{scene.get('section_type') or ''} {scene.get('goal') or ''}".lower()
        if any(token in text for token in ("hook", "chorus", "reveal", "transformation", "drop", "signature")):
            return "first_last"
        if any(token in text for token in ("vocal", "singer", "performance", "lyric", "phrase")):
            return "ia2v"
        if any(token in text for token in ("bridge", "instrumental", "mood", "environment", "world", "movement")):
            return "i2v"
        return str(scene.get("route") or "i2v").strip() if str(scene.get("route") or "").strip() in ALLOWED_CLIP_ROUTES else "i2v"

    changed = 0
    for scene in scenes:
        desired = _route_for_scene(scene)
        if desired != str(scene.get("route") or "").strip():
            scene["route"] = desired
            changed += 1
        _ensure_route_prompts(scene)

    approx_music_video = 20.0 <= float(whole_map.duration_sec) <= 45.0
    has_repeat = any(str(sec.section_type or "").lower() in {"chorus", "hook"} or sec.recurring_group_id for sec in whole_map.sections)
    has_first_last = any(str(scene.get("route") or "") == "first_last" for scene in scenes)
    if approx_music_video and has_repeat and not has_first_last:
        anchor_idx = max(0, min(len(scenes) - 1, len(scenes) // 2))
        scenes[anchor_idx]["route"] = "first_last"
        _ensure_route_prompts(scenes[anchor_idx])
        changed += 1

    route_counts: dict[str, int] = {}
    for scene in scenes:
        route = str(scene.get("route") or "i2v")
        route_counts[route] = route_counts.get(route, 0) + 1
    return {**merged, "scenes": scenes}, {"changed": changed, "route_counts": route_counts}


def _generate_whole_map_with_retry(*, api_key: str, payload: dict[str, Any], context: dict[str, Any]) -> tuple[WholeTrackMapResponse, dict[str, Any], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    last_error: ClipPipelineError | None = None
    for attempt in range(1, WHOLE_MAP_RETRY_COUNT + 1):
        req_body = _build_whole_track_map_request(payload, context)
        try:
            logger.info("[CLIP PIPELINE GEMINI] stage=whole_map event=request_start attempt=%s", attempt)
            raw, call_diag = _call_gemini_json(api_key=api_key, body=req_body, retry_count=2, stage="whole_map", request_started=True)
            logger.info("[CLIP PIPELINE GEMINI] stage=whole_map event=request_done status=ok attempt=%s", attempt)
            logger.info("[CLIP PIPELINE GEMINI] stage=whole_map event=parse_start attempt=%s", attempt)
            model = WholeTrackMapResponse.model_validate(raw)
            logger.info("[CLIP PIPELINE GEMINI] stage=whole_map event=parse_done valid=true attempt=%s", attempt)
            diagnostics.append(
                {
                    "stage": "whole_map",
                    "attempt": attempt,
                    "response_schema_enabled": True,
                    "context_state": context.get("context_state"),
                    "full_audio_attached": bool(context.get("audio_media_attached")),
                    "full_audio_bytes": int(call_diag.get("audio_part_size_bytes") or 0),
                    "refs_total_size_bytes": int(call_diag.get("refs_total_size_bytes") or 0),
                }
            )
            return model, call_diag, diagnostics
        except ValidationError as exc:
            logger.warning("[CLIP PIPELINE GEMINI] stage=whole_map event=parse_done valid=false attempt=%s", attempt)
            reason = "invalid whole track map contract"
            diagnostics.append({"stage": "whole_map", "attempt": attempt, "reason": reason, "errors": exc.errors()})
            last_error = ClipPipelineError("retryable_fail", reason, details={"attempt": attempt, "errors": exc.errors()})
        except ClipPipelineError as exc:
            logger.warning("[CLIP PIPELINE GEMINI] stage=whole_map event=request_done status=error attempt=%s reason=%s", attempt, exc.message)
            diagnostics.append({"stage": "whole_map", "attempt": attempt, "reason": exc.message})
            last_error = exc
    raise last_error or ClipPipelineError("retryable_fail", "invalid whole track map contract")


def _generate_chunk_with_retry(
    *,
    api_key: str,
    req: ChunkStoryboardRequest,
    whole_map: WholeTrackMapResponse,
    context: dict[str, Any],
    decoded_audio_cache: dict[str, Any],
) -> tuple[ChunkStoryboardResponse, list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    last_error: ClipPipelineError | None = None
    for attempt in range(1, CHUNK_RETRY_COUNT + 1):
        try:
            body, transport_diag = _build_chunk_request(
                req=req,
                whole_map=whole_map,
                context=context,
                decoded_audio_cache=decoded_audio_cache,
            )
            logger.info("[CLIP PIPELINE GEMINI] stage=chunk chunk_id=%s event=request_start attempt=%s", req.chunk_id, attempt)
            parsed_chunk, call_diag = _call_gemini_json(api_key=api_key, body=body, retry_count=2, stage="chunk", chunk_id=req.chunk_id, request_started=True)
            logger.info("[CLIP PIPELINE GEMINI] stage=chunk chunk_id=%s event=request_done status=ok attempt=%s", req.chunk_id, attempt)
            logger.info("[CLIP PIPELINE GEMINI] stage=chunk chunk_id=%s event=parse_start attempt=%s", req.chunk_id, attempt)
            chunk = ChunkStoryboardResponse.model_validate(parsed_chunk)
            chunk, canonical_diag = _canonicalize_chunk_scene_ids(chunk)
            _validate_chunk_response(chunk)
            logger.info("[CLIP PIPELINE GEMINI] stage=chunk chunk_id=%s event=parse_done valid=true attempt=%s", req.chunk_id, attempt)
            diagnostics.append(
                {
                    "stage": "chunk",
                    "chunk_id": req.chunk_id,
                    "attempt": attempt,
                    "response_schema_enabled": True,
                    "context_state": context.get("context_state"),
                    "gemini_retries": call_diag.get("retries") if isinstance(call_diag, dict) else None,
                    "canonicalSceneIdApplied": canonical_diag.get("canonicalSceneIdApplied", 0),
                    "canonicalSceneIdMap": canonical_diag.get("canonicalSceneIdMap", {}),
                    "transportDiagnostics": transport_diag,
                }
            )
            return chunk, diagnostics
        except ValidationError as exc:
            logger.warning("[CLIP PIPELINE GEMINI] stage=chunk chunk_id=%s event=parse_done valid=false attempt=%s", req.chunk_id, attempt)
            diagnostics.append({"stage": "chunk", "chunk_id": req.chunk_id, "attempt": attempt, "reason": "invalid chunk contract", "errors": exc.errors()})
            last_error = ClipPipelineError("retryable_fail", "invalid chunk contract", details={"chunk_id": req.chunk_id, "attempt": attempt, "errors": exc.errors()})
        except ClipPipelineError as exc:
            logger.warning("[CLIP PIPELINE GEMINI] stage=chunk chunk_id=%s event=request_done status=error attempt=%s reason=%s", req.chunk_id, attempt, exc.message)
            diagnostics.append({"stage": "chunk", "chunk_id": req.chunk_id, "attempt": attempt, "reason": exc.message})
            last_error = exc
    raise last_error or ClipPipelineError("retryable_fail", "invalid chunk", details={"chunk_id": req.chunk_id})


def run_clip_storyboard_pipeline(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ClipPipelineError("fatal_fail", "GEMINI_API_KEY is missing for clip pipeline.", status_code=503)

    context = _normalize_clip_context(payload)
    decoded_audio_cache, decoded_audio_diag = _get_or_build_decoded_master_audio(context)
    response_context = _sanitize_response_context(context)
    state_history = [context.get("context_state") or "context_prepared"]
    retry_diagnostics: list[dict[str, Any]] = []
    schema_diagnostics = {"whole_map_schema_enabled": True, "chunk_schema_enabled": True, "repair_schema_enabled": True}
    context_diagnostics = [_build_context_diagnostics(context, stage="stage_0_context_prepare")]
    total_started = perf_counter()
    whole_map_started = 0.0
    chunk_secs: list[float] = []
    merge_started = 0.0
    merge_sec = 0.0
    retry_count = 0
    last_seen: dict[str, Any] = {
        "stage": "context_prepare",
        "attempt": 0,
        "chunk_id": None,
        "request_started": False,
        "request_finished": False,
    }
    try:
        whole_map_started = perf_counter()
        last_seen.update({"stage": "whole_map", "attempt": 1, "request_started": True, "request_finished": False})
        whole_map, map_diag, map_retry = _generate_whole_map_with_retry(api_key=api_key, payload=payload, context=context)
        last_seen.update({"stage": "whole_map", "request_finished": True})
        retry_diagnostics.extend(map_retry)
        retry_count += len(map_retry)
        whole_map_sec = round(max(0.0, perf_counter() - whole_map_started), 3)

        state_history.append("track_mapped")
        chunks = _plan_chunks(whole_map)
        state_history.append("chunks_planned")

        chunk_results: list[ChunkStoryboardResponse] = []
        continuity_tail = ContinuityTailState()
        for boundary in chunks:
            state_history.append("chunk_running")
            chunk_started = perf_counter()
            last_seen.update({"stage": f"chunk_{boundary.chunk_id}", "chunk_id": boundary.chunk_id, "request_started": True, "request_finished": False})
            section_ids = [sec.section_id for sec in whole_map.sections if not (sec.t1 <= boundary.t0 or sec.t0 >= boundary.t1)]
            recurring_ids = list(dict.fromkeys([sec.recurring_group_id for sec in whole_map.sections if sec.recurring_group_id]))
            req = ChunkStoryboardRequest(
                track_id=whole_map.track_id,
                chunk_id=boundary.chunk_id,
                t0=boundary.t0,
                t1=boundary.t1,
                global_map_ref=ChunkMapRef(section_ids=section_ids, recurring_group_ids=[x for x in recurring_ids if x]),
                continuity_in=ContinuityIn(previous_chunk_id=chunk_results[-1].chunk_id if chunk_results else None, tail_state=continuity_tail),
                creative_note=str((payload.get("metadata") or {}).get("creativeNote") or "") if isinstance(payload.get("metadata"), dict) else "",
            )
            chunk, chunk_retry = _generate_chunk_with_retry(
                api_key=api_key,
                req=req,
                whole_map=whole_map,
                context=context,
                decoded_audio_cache=decoded_audio_cache,
            )
            last_seen.update({"stage": f"chunk_{boundary.chunk_id}", "chunk_id": boundary.chunk_id, "request_finished": True})
            retry_diagnostics.extend(chunk_retry)
            retry_count += len(chunk_retry)
            chunk_secs.append(round(max(0.0, perf_counter() - chunk_started), 3))
            continuity_tail = ContinuityTailState.model_validate(chunk.continuity_out if isinstance(chunk.continuity_out, dict) else {})
            chunk_results.append(chunk)
            state_history.append("chunk_done")

        state_history.append("merging")
        merge_started = perf_counter()
        last_seen.update({"stage": "merge", "request_started": True, "request_finished": False, "chunk_id": None})
        merged, issues, merge_diag = _local_merge(whole_map.track_id, chunk_results)
        merge_sec = round(max(0.0, perf_counter() - merge_started), 3)
        last_seen.update({"stage": "merge", "request_finished": True})
        merged_validation = _validate_merged_storyboard(merged)
        if not merged_validation.get("valid"):
            raise ClipPipelineError("retryable_fail", "merged storyboard validation failed", details={"errors": merged_validation.get("errors")})
        repair_data = {"applied": False, "issues": []}
        repair_apply_diag = {"edge_rewrites_applied": 0, "transition_scene_applied": False}
        repair_validation_diag: dict[str, Any] = {"attempted": False, "accepted": False, "errors": []}
        if issues:
            state_history.append("repairing")
            last_seen.update({"stage": "repair", "request_started": True, "request_finished": False})
            repair_data = _run_optional_repair(api_key=api_key, merged=merged, issues=issues)
            last_seen.update({"stage": "repair", "request_finished": True})
            pre_repair_merged = dict(merged)
            merged_candidate, repair_apply_diag = _apply_repair_result(merged, repair_data)
            repair_validation_diag = {"attempted": True, **_validate_repair_applied_storyboard(pre_repair_merged, merged_candidate)}
            if repair_validation_diag.get("valid"):
                merged = merged_candidate
                repair_validation_diag["accepted"] = True
            else:
                repair_data["repair_rejected_reason"] = "post_repair_validation_failed"
                repair_data["repair_rejected_errors"] = repair_validation_diag.get("errors") or []
                repair_validation_diag["accepted"] = False

        merged, route_mix_diag = _apply_clip_route_mix_policy(merged, whole_map)
        merged_validation = _validate_merged_storyboard(merged)
        if not merged_validation.get("valid"):
            raise ClipPipelineError("retryable_fail", "route-mix merged storyboard validation failed", details={"errors": merged_validation.get("errors")})

        audio_duration_sec = float(payload.get("audioDurationSec") or whole_map.duration_sec or 0.0)
        final_scene_end = _final_scene_end(merged)
        full_coverage_achieved = final_scene_end >= max(0.0, audio_duration_sec - FULL_COVERAGE_TOLERANCE_SEC)
        tail_repair_attempted = False
        if not full_coverage_achieved and chunks:
            tail_repair_attempted = True
            last_boundary = chunks[-1]
            section_ids = [sec.section_id for sec in whole_map.sections if not (sec.t1 <= last_boundary.t0 or sec.t0 >= last_boundary.t1)]
            recurring_ids = list(dict.fromkeys([sec.recurring_group_id for sec in whole_map.sections if sec.recurring_group_id]))
            repair_req = ChunkStoryboardRequest(
                track_id=whole_map.track_id,
                chunk_id=last_boundary.chunk_id,
                t0=last_boundary.t0,
                t1=last_boundary.t1,
                global_map_ref=ChunkMapRef(section_ids=section_ids, recurring_group_ids=[x for x in recurring_ids if x]),
                continuity_in=ContinuityIn(previous_chunk_id=chunk_results[-2].chunk_id if len(chunk_results) > 1 else None, tail_state=continuity_tail),
                creative_note="TAIL COVERAGE REPAIR: ensure last scene reaches track end.",
            )
            repaired_last_chunk, tail_retry_diag = _generate_chunk_with_retry(
                api_key=api_key,
                req=repair_req,
                whole_map=whole_map,
                context=context,
                decoded_audio_cache=decoded_audio_cache,
            )
            last_seen.update({"stage": f"chunk_{last_boundary.chunk_id}", "chunk_id": last_boundary.chunk_id, "request_finished": True})
            retry_diagnostics.extend(tail_retry_diag)
            retry_count += len(tail_retry_diag)
            chunk_results[-1] = repaired_last_chunk
            merged, issues, merge_diag = _local_merge(whole_map.track_id, chunk_results)
            merged, route_mix_diag = _apply_clip_route_mix_policy(merged, whole_map)
            merged_validation = _validate_merged_storyboard(merged)
            if not merged_validation.get("valid"):
                raise ClipPipelineError("retryable_fail", "tail-repair merged storyboard validation failed", details={"errors": merged_validation.get("errors")})
            final_scene_end = _final_scene_end(merged)
            full_coverage_achieved = final_scene_end >= max(0.0, audio_duration_sec - FULL_COVERAGE_TOLERANCE_SEC)
        if not full_coverage_achieved:
            missing_tail_sec = max(0.0, round(audio_duration_sec - final_scene_end, 3))
            raise ClipPipelineError(
                "clip_pipeline_tail_not_covered",
                "Final storyboard does not cover full audio duration.",
                status_code=422,
                details={
                    "audioDurationSec": round(audio_duration_sec, 3),
                    "finalSceneEnd": round(final_scene_end, 3),
                    "missingTailSec": missing_tail_sec,
                    "retryable": True,
                },
            )

        logger.warning(
            "[CLIP PIPELINE BACKEND RESPONSE SUMMARY] pipeline=%s scene_count=%s final_scene_end=%s audio_duration=%s whole_map_sections=%s chunk_count=%s",
            "clip_chunked_v1",
            len(merged.get("scenes") or []),
            round(final_scene_end, 3),
            round(audio_duration_sec, 3),
            len(whole_map.sections or []),
            len(chunk_results),
        )
        state_history.append("complete")
        scene_durations = [
            max(0.0, float(scene.get("t1") or 0.0) - float(scene.get("t0") or 0.0))
            for scene in (merged.get("scenes") if isinstance(merged.get("scenes"), list) else [])
            if isinstance(scene, dict)
        ]
        total_sec = round(max(0.0, perf_counter() - total_started), 3)
        return {
            "ok": True,
            "mode": "clip",
            "pipeline": "clip_chunked_v1",
            "job": {
                "job_type": "storyboard_generation",
                "mode": "clip",
                "content_type": "music_video",
                "audio_source": "master_track",
                "allowed_scene_routes": list(ALLOWED_CLIP_ROUTES),
            },
            "state": state_history[-1],
            "state_history": state_history,
            "context": response_context,
            "whole_track_map": whole_map.model_dump(mode="json"),
            "chunks": [chunk.model_dump(mode="json", exclude_none=True) for chunk in chunk_results],
            "merged_storyboard": merged,
            "repair": repair_data,
            "meta": {
                "model": CLIP_PIPELINE_MODEL,
                "pipelineUsed": "clip_chunked_v1",
                "legacyPathUsed": False,
                "wholeMapUsed": True,
                "sceneCount": len(merged.get("scenes") or []),
                "chunkCount": len(chunk_results),
                "finalSceneEnd": round(final_scene_end, 3),
                "audioDurationSec": round(audio_duration_sec, 3),
                "wholeMapSectionCount": len(whole_map.sections or []),
                "fullCoverageAchieved": full_coverage_achieved,
                "tailRepairAttempted": tail_repair_attempted,
                "mapDiagnostics": map_diag,
                "retryDiagnostics": retry_diagnostics,
                "mergeDiagnostics": merge_diag,
                "mergedValidationDiagnostics": merged_validation,
                "routeMixDiagnostics": route_mix_diag,
                "repairApplyDiagnostics": repair_apply_diag,
                "repairValidationDiagnostics": repair_validation_diag,
                "schemaDiagnostics": schema_diagnostics,
                "contextDiagnostics": context_diagnostics,
                "timingDiagnostics": {
                    "total_sec": total_sec,
                    "whole_map_sec": whole_map_sec,
                    "chunk_secs": chunk_secs,
                    "merge_sec": merge_sec,
                    "decoded_master_audio_build_sec": float(decoded_audio_diag.get("decoded_master_audio_build_sec") or 0.0),
                },
                "resultDiagnostics": {
                    "scene_count": len(merged.get("scenes") or []),
                    "chunk_count": len(chunk_results),
                    "retry_count": retry_count,
                    "scenes_below_3_sec_count": sum(1 for d in scene_durations if d < 3.0),
                    "scenes_above_8_sec_count": sum(1 for d in scene_durations if d > 8.0),
                    "decoded_master_audio_cached": bool(decoded_audio_diag.get("decoded_master_audio_cached")),
                    "decoded_master_audio_reused_for_chunks": bool(decoded_audio_diag.get("decoded_master_audio_cached")),
                },
            },
        }
    except ClipPipelineError as exc:
        state_history.append("retryable_fail")
        exc_attempt = (exc.details or {}).get("attempt") if isinstance(exc.details, dict) else None
        exc_chunk_id = (exc.details or {}).get("chunk_id") if isinstance(exc.details, dict) else None
        exc.details = {
            **(exc.details if isinstance(exc.details, dict) else {}),
            "last_seen_stage": last_seen.get("stage"),
            "last_seen_attempt": exc_attempt if exc_attempt is not None else last_seen.get("attempt"),
            "last_seen_chunk_id": exc_chunk_id if exc_chunk_id is not None else last_seen.get("chunk_id"),
            "request_started": bool(last_seen.get("request_started")),
            "request_finished": bool(last_seen.get("request_finished")),
            "state_history": state_history,
        }
        raise
    except Exception as exc:
        state_history.append("fatal_fail")
        raise ClipPipelineError(
            "fatal_fail",
            "clip pipeline failed",
            status_code=500,
            details={
                "error": str(exc),
                "state_history": state_history,
                "last_seen_stage": last_seen.get("stage"),
                "last_seen_attempt": last_seen.get("attempt"),
                "last_seen_chunk_id": last_seen.get("chunk_id"),
                "request_started": bool(last_seen.get("request_started")),
                "request_finished": bool(last_seen.get("request_finished")),
            },
        ) from exc


def regenerate_clip_chunk(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ClipPipelineError("gemini_api_key_missing", "GEMINI_API_KEY is missing for clip regenerate.", status_code=503)

    global_map_payload = payload.get("whole_track_map") if isinstance(payload.get("whole_track_map"), dict) else {}
    chunk_payload = payload.get("chunk") if isinstance(payload.get("chunk"), dict) else {}
    if not global_map_payload:
        raise ClipPipelineError("whole_track_map_missing", "whole_track_map is required.")
    if not chunk_payload:
        raise ClipPipelineError("chunk_missing", "chunk is required.")

    whole_map = WholeTrackMapResponse.model_validate(global_map_payload)
    chunk_id = str(chunk_payload.get("chunk_id") or "").strip()
    if not chunk_id:
        raise ClipPipelineError("chunk_id_missing", "chunk.chunk_id is required.")
    t0 = float(chunk_payload.get("t0") or 0.0)
    t1 = float(chunk_payload.get("t1") or t0)
    continuity_in = chunk_payload.get("continuity_in") if isinstance(chunk_payload.get("continuity_in"), dict) else {}
    creative_note = str(chunk_payload.get("creative_note") or "").strip()

    req = ChunkStoryboardRequest(
        track_id=whole_map.track_id,
        chunk_id=chunk_id,
        t0=t0,
        t1=t1,
        continuity_in=ContinuityIn.model_validate(continuity_in),
        creative_note=creative_note,
        global_map_ref=ChunkMapRef(
            section_ids=[sec.section_id for sec in whole_map.sections if not (sec.t1 <= t0 or sec.t0 >= t1)],
            recurring_group_ids=list(dict.fromkeys([sec.recurring_group_id for sec in whole_map.sections if sec.recurring_group_id])),
        ),
    )
    context = _normalize_clip_context(payload, payload.get("context") if isinstance(payload.get("context"), dict) else None)
    decoded_audio_cache, decoded_audio_diag = _get_or_build_decoded_master_audio(context)
    response_context = _sanitize_response_context(context)
    chunk, retry_diag = _generate_chunk_with_retry(
        api_key=api_key,
        req=req,
        whole_map=whole_map,
        context=context,
        decoded_audio_cache=decoded_audio_cache,
    )
    regenerate_diagnostics = {
        "stage": "regenerate_chunk",
        "chunk_id": chunk_id,
        "response_schema_enabled": True,
        "used_context_mode": context.get("used_context_mode"),
        "audio_media_attached": bool(context.get("audio_media_attached")),
        "ref_roles_attached": list(context.get("refs_roles_attached") or []),
        "attempt_count": len(retry_diag),
        "decoded_master_audio_cached": bool(decoded_audio_diag.get("decoded_master_audio_cached")),
        "decoded_master_audio_build_sec": float(decoded_audio_diag.get("decoded_master_audio_build_sec") or 0.0),
        "decoded_master_audio_reused_for_chunks": bool(decoded_audio_diag.get("decoded_master_audio_cached")),
    }
    return {
        "ok": True,
        "mode": "clip",
        "state": "chunk_done",
        "chunk": chunk.model_dump(mode="json", exclude_none=True),
        "context": response_context,
        "meta": {
            "model": CLIP_PIPELINE_MODEL,
            "retryDiagnostics": retry_diag,
            "schemaDiagnostics": {"chunk_schema_enabled": True},
            "contextDiagnostics": [_build_context_diagnostics(context, stage="regenerate_context_prepare", chunk_id=chunk_id)],
            "regenerateDiagnostics": regenerate_diagnostics,
        },
    }
