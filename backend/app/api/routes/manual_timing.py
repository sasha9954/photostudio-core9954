from __future__ import annotations

import mimetypes
import os
import re
import tempfile
from typing import Annotated
from urllib.parse import urlparse

import requests
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from requests import RequestException

from app.core.static_paths import ASSETS_DIR, ensure_static_dirs
from app.engine.manual_timing_asr import ManualTimingAsrSettings, build_manual_timing_audio_phrase_map

router = APIRouter(prefix="/manual-timing")


class ManualTimingAudioPhrasesIn(BaseModel):
    audio_url: str | None = Field(default=None, alias="audioUrl")
    language: str | None = "auto"
    split_mode: str = "pause_based"
    min_pause_sec: float = 0.45
    max_phrase_sec: float = 8.0
    min_phrase_sec: float = 1.2
    padding_sec: float = 0.0
    model_size: str | None = None

    model_config = {"populate_by_name": True}


def _resolve_audio_asset_path(audio_url: str) -> str | None:
    source = str(audio_url or "").strip()
    if not source:
        return None
    parsed = urlparse(source)
    raw_path = str(parsed.path or "").strip()
    if not raw_path and source.startswith("static/assets/"):
        raw_path = "/" + source
    path = "/" + raw_path.lstrip("/")
    normalized_path = re.sub(r"/+", "/", path)
    if normalized_path.startswith("/static/assets/"):
        relative_asset_path = normalized_path[len("/static/assets/"):].strip("/")
    elif normalized_path.startswith("/assets/"):
        relative_asset_path = normalized_path[len("/assets/"):].strip("/")
    else:
        return None
    if not relative_asset_path:
        return None

    filename = os.path.basename(relative_asset_path)
    base = os.path.splitext(filename)[0]
    if not filename or not base:
        return None

    candidates = [relative_asset_path, filename, base, f"{base}.mp3", f"{base}.wav", f"{base}.ogg", f"{base}.m4a", f"{base}.aac", f"{base}.flac"]
    seen: set[str] = set()
    for name in candidates:
        candidate = str(ASSETS_DIR / name)
        if candidate in seen:
            continue
        seen.add(candidate)
        if os.path.isfile(candidate):
            return candidate
    return None


def _extension_from_mime_or_name(mime: str | None, name: str | None) -> str:
    filename_ext = os.path.splitext(str(name or ""))[1].lower().replace(".", "")
    if filename_ext in {"mp3", "wav", "ogg", "m4a", "aac", "flac", "mp4", "webm"}:
        return filename_ext
    guessed = (mimetypes.guess_extension(str(mime or "").split(";")[0].strip()) or "").lower().replace(".", "")
    if guessed == "oga":
        guessed = "ogg"
    return guessed if guessed in {"mp3", "wav", "ogg", "m4a", "aac", "flac", "mp4", "webm"} else "mp3"


def _download_audio_to_temp(audio_url: str, temp_paths: list[str]) -> str:
    local = _resolve_audio_asset_path(audio_url)
    if local:
        return local
    source = str(audio_url or "").strip()
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="audio_url must be a /static/assets URL or public http(s) URL")
    try:
        resp = requests.get(source, timeout=90)
        if resp.status_code >= 400:
            raise HTTPException(status_code=400, detail=f"audio_url HTTP {resp.status_code}")
        content = resp.content or b""
        if not content:
            raise HTTPException(status_code=400, detail="audio_url returned empty body")
        ext = _extension_from_mime_or_name(resp.headers.get("Content-Type"), parsed.path)
        tmp = tempfile.NamedTemporaryFile(prefix="manual_timing_asr_url_", suffix=f".{ext}", delete=False)
        with tmp:
            tmp.write(content)
        temp_paths.append(tmp.name)
        return tmp.name
    except HTTPException:
        raise
    except RequestException as exc:
        raise HTTPException(status_code=400, detail=f"audio_url request failed: {str(exc)[:240]}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"audio_url read failed: {str(exc)[:240]}") from exc


def _settings_from_values(
    *,
    language: str | None = "auto",
    split_mode: str = "pause_based",
    min_pause_sec: float = 0.45,
    max_phrase_sec: float = 8.0,
    min_phrase_sec: float = 1.2,
    padding_sec: float = 0.0,
    model_size: str | None = None,
) -> ManualTimingAsrSettings:
    return ManualTimingAsrSettings(
        language=language or "auto",
        split_mode=split_mode or "pause_based",
        min_pause_sec=float(min_pause_sec or 0.45),
        max_phrase_sec=float(max_phrase_sec or 8.0),
        min_phrase_sec=float(min_phrase_sec or 1.2),
        padding_sec=float(padding_sec or 0.0),
        model_size=(model_size or os.getenv("MANUAL_TIMING_ASR_MODEL") or "small"),
    )


def _run_asr(audio_path: str, settings: ManualTimingAsrSettings) -> dict:
    try:
        return build_manual_timing_audio_phrase_map(audio_path, settings)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=f"ASR runtime error: {str(exc)[:300]}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"ASR failed: {str(exc)[:300]}") from exc


@router.post("/audio-phrases")
async def create_manual_timing_audio_phrases(
    request: Request,
    audio_file: Annotated[UploadFile | None, File(alias="audio_file")] = None,
    audio_url: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = "auto",
    split_mode: Annotated[str, Form()] = "pause_based",
    min_pause_sec: Annotated[float, Form()] = 0.45,
    max_phrase_sec: Annotated[float, Form()] = 8.0,
    min_phrase_sec: Annotated[float, Form()] = 1.2,
    padding_sec: Annotated[float, Form()] = 0.0,
    model_size: Annotated[str | None, Form()] = None,
):
    """Build ASR word timestamps and pause-based phrase map for Manual Timing.

    Accepts either multipart form data (audio_file or audio_url) or JSON with audio_url.
    """
    ensure_static_dirs()
    content_type = str(request.headers.get("content-type") or "").lower()
    if "application/json" in content_type:
        try:
            raw_json = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON body") from exc
        payload = ManualTimingAudioPhrasesIn.model_validate(raw_json if isinstance(raw_json, dict) else {})
        audio_url = payload.audio_url
        language = payload.language
        split_mode = payload.split_mode
        min_pause_sec = payload.min_pause_sec
        max_phrase_sec = payload.max_phrase_sec
        min_phrase_sec = payload.min_phrase_sec
        padding_sec = payload.padding_sec
        model_size = payload.model_size

    temp_paths: list[str] = []
    try:
        if audio_file is not None:
            content = await audio_file.read()
            if not content:
                raise HTTPException(status_code=400, detail="audio_file is empty")
            ext = _extension_from_mime_or_name(audio_file.content_type, audio_file.filename)
            tmp = tempfile.NamedTemporaryFile(prefix="manual_timing_asr_upload_", suffix=f".{ext}", delete=False)
            with tmp:
                tmp.write(content)
            temp_paths.append(tmp.name)
            audio_path = tmp.name
        elif audio_url:
            audio_path = _download_audio_to_temp(audio_url, temp_paths)
        else:
            raise HTTPException(status_code=400, detail="Provide audio_file or audio_url")

        settings = _settings_from_values(
            language=language,
            split_mode=split_mode,
            min_pause_sec=min_pause_sec,
            max_phrase_sec=max_phrase_sec,
            min_phrase_sec=min_phrase_sec,
            padding_sec=padding_sec,
            model_size=model_size,
        )
        return _run_asr(audio_path, settings)
    finally:
        for path in temp_paths:
            try:
                os.unlink(path)
            except Exception:
                pass


@router.post("/audio-phrases/json")
def create_manual_timing_audio_phrases_json(payload: ManualTimingAudioPhrasesIn):
    ensure_static_dirs()
    temp_paths: list[str] = []
    try:
        audio_url = str(payload.audio_url or "").strip()
        if not audio_url:
            raise HTTPException(status_code=400, detail="Provide audio_url")
        audio_path = _download_audio_to_temp(audio_url, temp_paths)
        settings = _settings_from_values(
            language=payload.language,
            split_mode=payload.split_mode,
            min_pause_sec=payload.min_pause_sec,
            max_phrase_sec=payload.max_phrase_sec,
            min_phrase_sec=payload.min_phrase_sec,
            padding_sec=payload.padding_sec,
            model_size=payload.model_size,
        )
        return _run_asr(audio_path, settings)
    finally:
        for path in temp_paths:
            try:
                os.unlink(path)
            except Exception:
                pass
