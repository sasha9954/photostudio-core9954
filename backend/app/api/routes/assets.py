from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field
import logging
import os
import re
import base64
import hashlib
import io
import zipfile
import mimetypes
import urllib.request
import urllib.parse
import tempfile
import subprocess
from pathlib import Path
from app.core.static_paths import ASSETS_DIR, ensure_static_dirs, asset_url, resolve_asset_filename_with_image_fallback

router = APIRouter()
logger = logging.getLogger(__name__)

ALLOWED_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp"}
ALLOWED_VIDEO_EXT = {".mp4", ".mov", ".webm", ".m4v", ".mkv"}
ALLOWED_AUDIO_EXT = {".mp3", ".wav", ".ogg", ".m4a"}

ALLOWED_IMAGE_MIME = {"image/png", "image/jpeg", "image/webp"}
ALLOWED_VIDEO_MIME = {"video/mp4", "video/webm", "video/quicktime", "video/x-m4v", "video/x-matroska"}
ALLOWED_AUDIO_MIME = {"audio/mpeg", "audio/mp3", "audio/wav", "audio/x-wav", "audio/ogg", "audio/mp4", "audio/m4a", "audio/x-m4a"}

EXTENSION_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
}


def _ensure_assets_dir():
    try:
        ensure_static_dirs()
    except Exception:
        pass


def _normalize_ext(ext: str | None) -> str:
    ext = str(ext or "").strip().lower()
    if not ext:
        return ""
    if not ext.startswith("."):
        ext = f".{ext}"
    if ext in {".jpe", ".jfif"}:
        return ".jpg"
    return ext


def _guess_ext_from_content_type(ct: str | None) -> str:
    ct = (ct or "").split(";", 1)[0].strip().lower()
    if not ct:
        return ""
    # manual fixes for media types where mimetypes can vary by OS
    if ct in ("audio/mpeg", "audio/mp3"):
        return ".mp3"
    if ct in ("audio/wav", "audio/x-wav"):
        return ".wav"
    if ct in ("audio/ogg",):
        return ".ogg"
    if ct in ("audio/mp4", "audio/m4a", "audio/x-m4a"):
        return ".m4a"
    if ct in ("video/quicktime",):
        return ".mov"
    if ct in ("video/x-m4v",):
        return ".m4v"
    if ct in ("video/x-matroska",):
        return ".mkv"
    ext = mimetypes.guess_extension(ct) or ""
    return _normalize_ext(ext)


def _classify_media(ext: str) -> str | None:
    if ext in ALLOWED_IMAGE_EXT:
        return "image"
    if ext in ALLOWED_VIDEO_EXT:
        return "video"
    if ext in ALLOWED_AUDIO_EXT:
        return "audio"
    return None


def _expected_mimes_for_ext(ext: str) -> set[str]:
    kind = _classify_media(ext)
    if kind == "image":
        return ALLOWED_IMAGE_MIME
    if kind == "video":
        return ALLOWED_VIDEO_MIME
    if kind == "audio":
        return ALLOWED_AUDIO_MIME
    return set()


def _validate_upload_media(*, ext: str, content_type: str) -> tuple[str, str]:
    normalized_ext = _normalize_ext(ext)
    kind = _classify_media(normalized_ext)
    logger.debug("[ASSET UPLOAD VALIDATE] ext=%s normalized_ext=%s content_type=%s kind=%s", ext or "none", normalized_ext or "none", (content_type or "none"), kind or "none")
    if not kind:
        raise HTTPException(status_code=400, detail=f"unsupported_ext:{normalized_ext or 'none'}")

    ct = (content_type or "").split(";", 1)[0].strip().lower()
    if ct:
        allowed_mimes = _expected_mimes_for_ext(normalized_ext)
        if ct not in allowed_mimes:
            raise HTTPException(status_code=400, detail=f"invalid_mime:{ct}")

    return normalized_ext, kind

def _probe_audio_duration_sec(raw: bytes, ext: str) -> float | None:
    """Return audio duration in seconds using ffprobe. Returns None if unavailable."""
    try:
        # write temp file
        suffix = ext or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as f:
            f.write(raw)
            tmp_path = f.name
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                tmp_path,
            ]
            r = subprocess.run(cmd, capture_output=True, text=True)
            s = (r.stdout or "").strip()
            dur = float(s) if s else None
            if dur and dur > 0:
                return dur
            return None
        finally:
            try:
                os.remove(tmp_path)
            except Exception:
                pass
    except Exception:
        return None


def _probe_audio_file_duration_sec(path: str) -> float | None:
    try:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        s = (r.stdout or "").strip()
        dur = float(s) if s else None
        if dur and dur > 0:
            return dur
        return None
    except Exception:
        return None


def _round_audio_sec(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    if not (number == number) or number < 0:
        number = default
    return round(number, 6)


def _resolve_static_audio_source(url: str) -> str | None:
    raw = str(url or "").strip()
    if not raw:
        return None
    try:
        parsed = urllib.parse.urlparse(raw)
        path = parsed.path or raw
    except Exception:
        path = raw.split("?", 1)[0].split("#", 1)[0]

    marker = "/static/assets/"
    if marker not in path:
        return None
    rel = urllib.parse.unquote(path.split(marker, 1)[1]).strip().lstrip("/")
    if not rel or ".." in rel.split("/"):
        return None
    candidate = (ASSETS_DIR / rel).resolve()
    try:
        assets_root = ASSETS_DIR.resolve()
        candidate.relative_to(assets_root)
    except Exception:
        return None
    if not candidate.is_file():
        return None
    return str(candidate)


def _podcast_audio_error(status_code: int, code: str, **payload):
    body = {"ok": False, "code": code}
    body.update(payload)
    return JSONResponse(status_code=status_code, content=body)


_PODCAST_AUDIO_URL_KEYS = ("source_url", "url", "asset_url", "assetUrl", "server_url", "public_url", "publicUrl")


def _first_audio_url(row: dict | None) -> str:
    for key in _PODCAST_AUDIO_URL_KEYS:
        value = str((row or {}).get(key) or "").strip()
        if value and not value.startswith("blob:"):
            return value
    return ""


def _find_actor_audio_source_url(source_id: str, actor_audios: list, saved_clips: list) -> str:
    safe_id = str(source_id or "").strip()
    if not safe_id:
        return ""
    for row in actor_audios if isinstance(actor_audios, list) else []:
        if str((row or {}).get("id") or "").strip() != safe_id:
            continue
        value = _first_audio_url(row)
        if value:
            return value
    for row in saved_clips if isinstance(saved_clips, list) else []:
        identifiers = (
            str((row or {}).get("source_audio_id") or "").strip(),
            str((row or {}).get("id") or "").strip(),
            str((row or {}).get("saved_clip_id") or "").strip(),
            str((row or {}).get("inserted_phrase_id") or "").strip(),
        )
        if safe_id not in identifiers:
            continue
        value = _first_audio_url(row)
        if value:
            return value
        nested_source_id = str((row or {}).get("source_audio_id") or "").strip()
        if nested_source_id and nested_source_id != safe_id:
            nested_value = _find_actor_audio_source_url(nested_source_id, actor_audios, [])
            if nested_value:
                return nested_value
    return ""


def _block_source_url(block: dict, *, original_audio_url: str, actor_audios: list, saved_clips: list) -> str:
    source_id = str((block or {}).get("source_audio_id") or "main").strip() or "main"
    if source_id == "main":
        return str((block or {}).get("source_url") or original_audio_url or "").strip()
    value = _first_audio_url(block)
    if value:
        return value
    for key in ("saved_clip_id", "inserted_phrase_id", "phrase_id"):
        clip_id = str((block or {}).get(key) or "").strip()
        if not clip_id:
            continue
        value = _find_actor_audio_source_url(clip_id, actor_audios, saved_clips)
        if value:
            return value
    return _find_actor_audio_source_url(source_id, actor_audios, saved_clips)


class PodcastAudioRenderIn(BaseModel):
    sourceNodeId: str | None = None
    originalAudioUrl: str | None = None
    blocks: list[dict] = Field(default_factory=list)
    actorAudios: list[dict] = Field(default_factory=list)
    savedClips: list[dict] = Field(default_factory=list)
    deletionMarkers: list[dict] = Field(default_factory=list)
    finalDurationSec: float | None = None
    podcastEditManifest: dict | None = None


@router.post("/podcast-audio/render-to-asset")
def render_podcast_audio_to_asset(payload: PodcastAudioRenderIn):
    _ensure_assets_dir()
    blocks = payload.blocks if isinstance(payload.blocks, list) else []
    actor_audios = payload.actorAudios if isinstance(payload.actorAudios, list) else []
    saved_clips = payload.savedClips if isinstance(payload.savedClips, list) else []
    final_duration_sec = _round_audio_sec(payload.finalDurationSec, 0.0)
    source_node_id = str(payload.sourceNodeId or "").strip()
    original_audio_url = str(payload.originalAudioUrl or "").strip()

    print("[PODCAST AUDIO RENDER START]", {
        "blockCount": len(blocks),
        "finalDurationSec": final_duration_sec,
        "sourceNodeId": source_node_id,
    })

    if not blocks:
        return _podcast_audio_error(400, "PODCAST_AUDIO_RENDER_FAILED", message="blocks_empty")

    with tempfile.TemporaryDirectory(prefix="podcast_audio_render_") as tmpdir:
        tmp_path = Path(tmpdir)
        segment_paths: list[Path] = []
        concat_lines: list[str] = []

        for index, block in enumerate(blocks):
            block = block or {}
            block_id = str(block.get("id") or block.get("block_id") or f"block_{index}")
            source_id = str(block.get("source_audio_id") or "main").strip() or "main"
            source_kind = str(block.get("source_kind") or block.get("type") or ("silence" if source_id == "silence" else "audio")).strip()
            is_silence = source_id == "silence" or str(block.get("type") or "").strip() == "silence" or source_kind == "silence"
            source_start_sec = _round_audio_sec(block.get("source_start_sec"), 0.0)
            source_end_sec = _round_audio_sec(block.get("source_end_sec"), 0.0)
            duration_sec = _round_audio_sec(block.get("duration_sec") or block.get("durationSec"), 0.0)
            if duration_sec <= 0 and source_end_sec > source_start_sec:
                duration_sec = _round_audio_sec(source_end_sec - source_start_sec, 0.0)
            if duration_sec <= 0:
                timeline_start = _round_audio_sec(block.get("timeline_start_sec"), 0.0)
                timeline_end = _round_audio_sec(block.get("timeline_end_sec"), 0.0)
                if timeline_end > timeline_start:
                    duration_sec = _round_audio_sec(timeline_end - timeline_start, 0.0)
            if duration_sec <= 0:
                continue

            source_url = "" if is_silence else _block_source_url(block, original_audio_url=original_audio_url, actor_audios=actor_audios, saved_clips=saved_clips)
            print("[PODCAST AUDIO RENDER SEGMENT]", {
                "index": index,
                "blockId": block_id,
                "sourceKind": "silence" if is_silence else source_kind,
                "sourceUrl": source_url,
                "sourceStartSec": source_start_sec,
                "sourceEndSec": source_end_sec,
                "durationSec": duration_sec,
            })

            out_segment = tmp_path / f"segment_{index:05d}.wav"
            if is_silence:
                cmd = [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-t", f"{duration_sec:.6f}",
                    "-ac", "2",
                    "-ar", "44100",
                    "-c:a", "pcm_s16le",
                    str(out_segment),
                ]
            else:
                source_path = _resolve_static_audio_source(source_url)
                if not source_path:
                    return _podcast_audio_error(400, "PODCAST_AUDIO_SOURCE_NOT_FOUND", blockId=block_id, sourceUrl=source_url)
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{source_start_sec:.6f}",
                    "-t", f"{duration_sec:.6f}",
                    "-i", source_path,
                    "-vn",
                    "-ac", "2",
                    "-ar", "44100",
                    "-c:a", "pcm_s16le",
                    str(out_segment),
                ]

            try:
                r = subprocess.run(cmd, capture_output=True, text=True)
            except FileNotFoundError:
                return _podcast_audio_error(500, "PODCAST_AUDIO_RENDER_FAILED", blockId=block_id, message="ffmpeg_not_found")
            if r.returncode != 0 or not out_segment.exists():
                return _podcast_audio_error(500, "PODCAST_AUDIO_RENDER_FAILED", blockId=block_id, message=(r.stderr or r.stdout or "ffmpeg_failed")[-2000:])
            segment_paths.append(out_segment)
            escaped = str(out_segment).replace("'", "'\\''")
            concat_lines.append(f"file '{escaped}'")

        if not segment_paths:
            return _podcast_audio_error(400, "PODCAST_AUDIO_RENDER_FAILED", message="no_renderable_segments")

        concat_file = tmp_path / "concat.txt"
        concat_file.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        temp_output = tmp_path / "podcast_composer_final.mp3"
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_file),
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(temp_output),
        ]
        try:
            r = subprocess.run(concat_cmd, capture_output=True, text=True)
        except FileNotFoundError:
            return _podcast_audio_error(500, "PODCAST_AUDIO_RENDER_FAILED", message="ffmpeg_not_found")
        if r.returncode != 0 or not temp_output.exists():
            return _podcast_audio_error(500, "PODCAST_AUDIO_RENDER_FAILED", message=(r.stderr or r.stdout or "ffmpeg_concat_failed")[-2000:])

        raw = temp_output.read_bytes()
        hid = _hash_bytes(raw)
        filename = f"podcast_audio_composer_{hid}.mp3"
        output_path = ASSETS_DIR / filename
        if not output_path.exists():
            output_path.write_bytes(raw)

    duration_sec = _probe_audio_file_duration_sec(str(output_path)) or final_duration_sec
    duration_sec = _round_audio_sec(duration_sec, final_duration_sec)
    output_url = asset_url(filename)
    print("[PODCAST AUDIO RENDER DONE]", {
        "outputUrl": output_url,
        "durationSec": duration_sec,
        "outputPath": str(output_path),
    })
    return {
        "ok": True,
        "url": output_url,
        "assetUrl": output_url,
        "asset_url": output_url,
        "publicUrl": output_url,
        "public_url": output_url,
        "filename": filename,
        "name": filename,
        "duration_sec": duration_sec,
        "durationSec": duration_sec,
        "duration_ms": int(round(duration_sec * 1000)),
        "durationMs": int(round(duration_sec * 1000)),
        "mime_type": "audio/mpeg",
        "mime": "audio/mpeg",
        "source": "podcast_audio_composer",
    }


class PodcastAudioExtractPhraseIn(BaseModel):
    sourceAudioUrl: str
    sourceStartSec: float = 0.0
    sourceEndSec: float | None = None
    durationSec: float | None = None
    label: str | None = None
    sourceNodeId: str | None = None


@router.post("/podcast-audio/extract-phrase-to-asset")
def extract_podcast_phrase_to_asset(payload: PodcastAudioExtractPhraseIn):
    _ensure_assets_dir()
    source_url = str(payload.sourceAudioUrl or "").strip()
    source_path = _resolve_static_audio_source(source_url)
    if not source_path:
        return _podcast_audio_error(400, "PODCAST_AUDIO_SOURCE_NOT_FOUND", sourceUrl=source_url)

    source_start_sec = _round_audio_sec(payload.sourceStartSec, 0.0)
    source_end_sec = _round_audio_sec(payload.sourceEndSec, 0.0) if payload.sourceEndSec is not None else 0.0
    duration_sec = _round_audio_sec(payload.durationSec, 0.0)
    if duration_sec <= 0 and source_end_sec > source_start_sec:
        duration_sec = _round_audio_sec(source_end_sec - source_start_sec, 0.0)
    if duration_sec <= 0:
        return _podcast_audio_error(400, "PODCAST_AUDIO_EXTRACT_FAILED", message="duration_empty")

    raw_label = str(payload.label or "phrase").strip() or "phrase"
    safe_label = re.sub(r"[^0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ_-]+", "_", raw_label).strip("_")[:48] or "phrase"
    source_node_id = re.sub(r"[^0-9A-Za-z_-]+", "_", str(payload.sourceNodeId or "podcast").strip())[:48] or "podcast"

    with tempfile.TemporaryDirectory(prefix="podcast_phrase_extract_") as tmpdir:
        tmp_path = Path(tmpdir)
        temp_output = tmp_path / "podcast_saved_phrase.mp3"
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{source_start_sec:.6f}",
            "-t", f"{duration_sec:.6f}",
            "-i", source_path,
            "-vn",
            "-ac", "2",
            "-ar", "44100",
            "-c:a", "libmp3lame",
            "-b:a", "192k",
            str(temp_output),
        ]
        print("[PODCAST SAVED PHRASE EXTRACT START]", {
            "sourceUrl": source_url,
            "sourceStartSec": source_start_sec,
            "durationSec": duration_sec,
            "label": raw_label,
            "sourceNodeId": str(payload.sourceNodeId or ""),
        })
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError:
            return _podcast_audio_error(500, "PODCAST_AUDIO_EXTRACT_FAILED", message="ffmpeg_not_found")
        if r.returncode != 0 or not temp_output.exists():
            return _podcast_audio_error(500, "PODCAST_AUDIO_EXTRACT_FAILED", message=(r.stderr or r.stdout or "ffmpeg_failed")[-2000:])

        raw = temp_output.read_bytes()
        hid = _hash_bytes(raw)
        filename = f"podcast_saved_phrase_{source_node_id}_{safe_label}_{hid}.mp3"
        output_path = ASSETS_DIR / filename
        if not output_path.exists():
            output_path.write_bytes(raw)

    probed_duration_sec = _probe_audio_file_duration_sec(str(output_path)) or duration_sec
    probed_duration_sec = _round_audio_sec(probed_duration_sec, duration_sec)
    output_url = asset_url(filename)
    print("[PODCAST SAVED PHRASE EXTRACT DONE]", {
        "url": output_url,
        "durationSec": probed_duration_sec,
        "filename": filename,
    })
    return {
        "ok": True,
        "url": output_url,
        "assetUrl": output_url,
        "asset_url": output_url,
        "server_url": output_url,
        "publicUrl": output_url,
        "public_url": output_url,
        "filename": filename,
        "name": filename,
        "duration_sec": probed_duration_sec,
        "durationSec": probed_duration_sec,
        "duration_ms": int(round(probed_duration_sec * 1000)),
        "durationMs": int(round(probed_duration_sec * 1000)),
        "mime_type": "audio/mpeg",
        "mime": "audio/mpeg",
        "source": "podcast_saved_phrase",
    }


def _raise_upload_bad_request(*, file: UploadFile | None, ext: str, content_type: str, size: int | None, detail: str) -> None:
    logger.warning(
        "[ASSET UPLOAD 400] filename=%s ext=%s content_type=%s size=%s detail=%s",
        getattr(file, "filename", None),
        ext or "none",
        content_type or "none",
        size if size is not None else "unknown",
        detail,
    )
    raise HTTPException(status_code=400, detail=detail)


@router.post("/assets/upload")
async def upload_file(request: Request, file: UploadFile = File(...)):
    """Generic upload endpoint for small user-provided files (images/video/audio).

    Returns a stable /static/assets URL without applying image transforms to video/audio uploads.
    """
    _ensure_assets_dir()

    if file is None:
        _raise_upload_bad_request(file=file, ext="", content_type="", size=None, detail="no_file")

    ct = (file.content_type or "").split(";", 1)[0].strip().lower()
    try:
        raw = await file.read()
    except Exception:
        _raise_upload_bad_request(file=file, ext="", content_type=ct, size=None, detail="read_failed")

    if not raw:
        _raise_upload_bad_request(file=file, ext="", content_type=ct, size=0, detail="empty")

    # soft limits to avoid abuse in local dev
    if len(raw) > 60 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="file_too_large")

    filename_ext = ""
    try:
        filename_ext = _normalize_ext(os.path.splitext(file.filename or "")[1])
    except Exception:
        filename_ext = ""

    guessed_ext = _guess_ext_from_content_type(ct)
    ext = filename_ext or guessed_ext
    if not ext:
        _raise_upload_bad_request(file=file, ext="", content_type=ct, size=len(raw), detail="unsupported_ext:none")

    logger.debug("[ASSET UPLOAD] filename=%s ext=%s filename_ext=%s guessed_ext=%s content_type=%s size=%s", file.filename or "", ext or "none", filename_ext or "none", guessed_ext or "none", ct or "none", len(raw))

    try:
        ext, media_kind = _validate_upload_media(ext=ext, content_type=ct)
    except HTTPException as exc:
        if int(getattr(exc, "status_code", 500)) == 400:
            _raise_upload_bad_request(file=file, ext=ext, content_type=ct, size=len(raw), detail=str(exc.detail))
        raise

    hid = _hash_bytes(raw)
    fn = f"{hid}{ext}"
    out_path = os.path.join(ASSETS_DIR, fn)

    # idempotent write
    if not os.path.exists(out_path):
        try:
            with open(out_path, "wb") as f:
                f.write(raw)
        except Exception:
            raise HTTPException(status_code=500, detail="write_failed")

    print("saved asset =", str(out_path), os.path.exists(out_path))

    duration_sec = None
    if media_kind == "audio":
        try:
            duration_sec = _probe_audio_duration_sec(raw, ext)
        except Exception:
            duration_sec = None

    logger.debug("[ASSET UPLOAD DECISION] filename=%s normalized_ext=%s media_kind=%s response_mime=%s", file.filename or "", ext or "none", media_kind, (ct or EXTENSION_TO_MIME.get(ext, "application/octet-stream")))

    response_mime = ct or EXTENSION_TO_MIME.get(ext, "application/octet-stream")
    url = asset_url(fn)
    original_name = file.filename or fn
    return {
        "ok": True,
        "url": url,
        "assetUrl": url,
        "asset_url": url,
        "publicUrl": url,
        "public_url": url,
        "path": url,
        "mime": response_mime,
        "mime_type": response_mime,
        "bytes": len(raw),
        "size_bytes": len(raw),
        "durationSec": duration_sec,
        "duration_sec": duration_sec,
        "name": original_name,
        "filename": original_name,
        "fileName": original_name,
        "kind": media_kind,
    }

def _hash_bytes(data: bytes) -> str:
    h = hashlib.sha256()
    h.update(data)
    return h.hexdigest()[:16]

def _safe_filename_from_url(url: str) -> str:
    try:
        p = urllib.parse.urlparse(url)
        name = os.path.basename(p.path)
        return name
    except Exception:
        return ""


class FromDataUrlIn(BaseModel):
    dataUrl: str

class FromUrlIn(BaseModel):
    url: str

class ZipIn(BaseModel):
    urls: list[str]

def _ensure_dir():
    ensure_static_dirs()

def _guess_ext(mime: str) -> str:
    m = (mime or "").lower()
    if m.startswith("audio/"):
        return ".mp3"
    if m == "image/png":
        return ".png"
    if m in ("image/jpeg", "image/jpg"):
        return ".jpg"
    if m == "image/webp":
        return ".webp"
    return ".png"

@router.post("/assets/fromDataUrl")
def from_data_url(req: Request, body: FromDataUrlIn):
    s = body.dataUrl or ""
    if not s.startswith("data:"):
        raise HTTPException(status_code=400, detail="dataUrl must start with data:")
    m = re.match(r"^data:([^;]+);base64,(.+)$", s, re.IGNORECASE | re.DOTALL)
    if not m:
        raise HTTPException(status_code=400, detail="Invalid dataUrl")
    mime = m.group(1).strip()
    b64 = m.group(2).strip()
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid base64")

    _ensure_dir()
    h = hashlib.sha256(raw).hexdigest()[:16]
    ext = _guess_ext(mime)
    fn = f"{h}{ext}"
    path = os.path.join(ASSETS_DIR, fn)

    # write once (idempotent)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(raw)

    # return absolute URL (frontend needs correct host)
    url = asset_url(fn)
    return {"url": url, "mime": mime, "bytes": len(raw)}


@router.post("/assets/fromUrl")
def from_url(payload: FromUrlIn, request: Request):
    url = (payload.url or "").strip()
    if not url or not re.match(r"^https?://", url, flags=re.I):
        raise HTTPException(status_code=400, detail="bad_url")

    _ensure_assets_dir()

    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PhotoStudio/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = resp.read()
            ct = resp.headers.get("Content-Type", "")
    except Exception as e:
        raise HTTPException(status_code=400, detail="fetch_failed")

    if not data or len(data) < 32:
        raise HTTPException(status_code=400, detail="empty")

    ext = _guess_ext_from_content_type(ct)
    if not ext:
        # fallback by path
        name = _safe_filename_from_url(url).lower()
        ext = os.path.splitext(name)[1]
    if ext not in [".png", ".jpg", ".jpeg", ".webp"]:
        # default to png if unknown
        ext = ".png"
    if ext == ".jpeg":
        ext = ".jpg"

    hid = _hash_bytes(data)
    fn = f"{hid}{ext}"
    out_path = os.path.join(ASSETS_DIR, fn)
    try:
        with open(out_path, "wb") as f:
            f.write(data)
    except Exception:
        raise HTTPException(status_code=500, detail="write_failed")

    return {"url": asset_url(fn)}


@router.post("/zip")
def zip_assets(payload: ZipIn, request: Request):
    _ensure_assets_dir()
    urls = payload.urls or []
    urls = [u for u in urls if isinstance(u, str) and u.strip()]
    if not urls:
        raise HTTPException(status_code=400, detail="no_urls")

    # allow only our /static/assets files
    files = []
    for u in urls[:50]:
        u = u.strip()
        try:
            p = urllib.parse.urlparse(u)
            path = p.path or ""
        except Exception:
            path = u
        if "/static/assets/" not in path:
            continue
        fn = path.split("/static/assets/")[-1]
        fn = os.path.basename(fn)
        if not re.match(r"^[0-9a-f]{8,32}\.(png|jpg|jpeg|webp)$", fn, flags=re.I):
            continue
        resolved_fn = resolve_asset_filename_with_image_fallback(fn) or fn
        fp = os.path.join(ASSETS_DIR, resolved_fn)
        if os.path.exists(fp):
            files.append((resolved_fn, fp))

    if not files:
        raise HTTPException(status_code=400, detail="no_local_assets")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for i, (fn, fp) in enumerate(files, start=1):
            arc = f"{i:02d}_{fn}"
            z.write(fp, arcname=arc)
    buf.seek(0)

    headers = {"Content-Disposition": 'attachment; filename="prints.zip"'}
    return StreamingResponse(buf, media_type="application/zip", headers=headers)
