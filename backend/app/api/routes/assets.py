from fastapi import APIRouter, Request, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
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
