from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
import requests
from requests import RequestException
import tempfile
import subprocess
import math
import base64
import json
import re
import os
import io
import mimetypes
import time
from urllib.parse import urlparse
from uuid import uuid4

from PIL import Image, ImageDraw

from app.core.config import settings
from app.core.static_paths import ASSETS_DIR, ensure_static_dirs, asset_url
from app.engine.video_engine import _download_image_from_source
from app.engine.gemini_rest import post_generate_content

router = APIRouter()


class ClipImageIn(BaseModel):
    sceneId: str
    prompt: str | None = None
    sceneDelta: str | None = None
    style: str | None = "default"
    width: int | None = 1024
    height: int | None = 1024
    refs: "ClipImageRefsIn | None" = None
    sceneText: str | None = None


class ClipImageRefsIn(BaseModel):
    character: list[str] = Field(default_factory=list)
    location: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    propAnchorLabel: str | None = None
    sessionCharacterAnchor: str | None = None
    sessionLocationAnchor: str | None = None
    sessionStyleAnchor: str | None = None
    sessionBaseline: dict | None = None
    worldScaleContext: str | None = None
    entityScaleAnchors: dict | None = None
    previousContinuityMemory: dict | None = None
    previousSceneImageUrl: str | None = None


class AudioSliceIn(BaseModel):
    sceneId: str
    t0: float
    t1: float
    audioUrl: str


class ClipVideoIn(BaseModel):
    sceneId: str
    imageUrl: str | None = None
    videoPrompt: str | None = None
    requestedDurationSec: int | float | None = 5
    transitionType: str | None = "single"
    startImageUrl: str | None = None
    endImageUrl: str | None = None
    transitionActionPrompt: str | None = None
    format: str | None = "9:16"
    lipSync: bool | None = False
    renderMode: str | None = None
    sceneType: str | None = None
    shotType: str | None = None
    audioSliceUrl: str | None = None


def _kie_headers() -> dict:
    return {
        "Authorization": f"Bearer {settings.KIE_API_KEY}",
        "Content-Type": "application/json",
    }


def _kie_upload_image_bytes(*, image_bytes: bytes, filename: str, mime_type: str) -> tuple[str | None, str | None]:
    upload_url = os.getenv("KIE_UPLOAD_URL", "https://kieai.redpandaai.co/api/file-base64-upload").strip()
    upload_path = os.getenv("KIE_UPLOAD_PATH", "images/photostudio").strip() or "images/photostudio"

    if not upload_url:
        return None, "upload_url_is_empty"

    safe_filename = str(filename or "source.jpg").strip() or "source.jpg"
    safe_mime = str(mime_type or "image/jpeg").strip() or "image/jpeg"
    b64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "base64Data": f"data:{safe_mime};base64,{b64}",
        "uploadPath": upload_path,
        "fileName": safe_filename,
    }

    try:
        resp = requests.post(upload_url, headers=_kie_headers(), json=payload, timeout=60)
        if resp.status_code >= 400:
            return None, f"upload_http_{resp.status_code}:{resp.text[:300]}"
        data = resp.json()
    except RequestException as exc:
        return None, f"upload_request_error:{str(exc)[:300]}"
    except Exception as exc:
        return None, f"upload_parse_error:{str(exc)[:300]}"

    image_url = None
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        image_url = (data["data"].get("downloadUrl") or data["data"].get("url") or "").strip()

    if not image_url:
        return None, f"upload_url_missing:{str(data)[:300]}"
    return image_url, None


def _kie_upload_audio_bytes(*, audio_bytes: bytes, filename: str, mime_type: str) -> tuple[str | None, str | None]:
    upload_url = os.getenv("KIE_UPLOAD_URL", "https://kieai.redpandaai.co/api/file-base64-upload").strip()
    upload_path = os.getenv("KIE_UPLOAD_AUDIO_PATH", "audio/photostudio").strip() or "audio/photostudio"

    if not upload_url:
        return None, "upload_url_is_empty"

    safe_filename = str(filename or "source.mp3").strip() or "source.mp3"
    safe_mime = str(mime_type or "audio/mpeg").strip() or "audio/mpeg"
    b64 = base64.b64encode(audio_bytes).decode("utf-8")

    payload = {
        "base64Data": f"data:{safe_mime};base64,{b64}",
        "uploadPath": upload_path,
        "fileName": safe_filename,
    }

    try:
        resp = requests.post(upload_url, headers=_kie_headers(), json=payload, timeout=60)
        if resp.status_code >= 400:
            return None, f"upload_http_{resp.status_code}:{resp.text[:300]}"
        data = resp.json()
    except RequestException as exc:
        return None, f"upload_request_error:{str(exc)[:300]}"
    except Exception as exc:
        return None, f"upload_parse_error:{str(exc)[:300]}"

    audio_url = None
    if isinstance(data, dict) and isinstance(data.get("data"), dict):
        audio_url = (data["data"].get("downloadUrl") or data["data"].get("url") or "").strip()

    if not audio_url:
        return None, f"upload_url_missing:{str(data)[:300]}"
    return audio_url, None


def _extract_task_id(data: dict) -> str | None:
    if not isinstance(data, dict):
        return None
    candidates = [
        data.get("taskId"),
        data.get("task_id"),
        ((data.get("data") or {}).get("taskId") if isinstance(data.get("data"), dict) else None),
        ((data.get("data") or {}).get("task_id") if isinstance(data.get("data"), dict) else None),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _extract_task_status(data: dict) -> str:
    if not isinstance(data, dict):
        return ""
    candidates = [
        data.get("status"),
        data.get("state"),
        data.get("taskStatus"),
        ((data.get("data") or {}).get("status") if isinstance(data.get("data"), dict) else None),
        ((data.get("data") or {}).get("state") if isinstance(data.get("data"), dict) else None),
        ((data.get("data") or {}).get("taskStatus") if isinstance(data.get("data"), dict) else None),
    ]
    for value in candidates:
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _extract_video_url_from_kie_payload(payload: object) -> str | None:
    if isinstance(payload, str):
        raw = payload.strip()
        if not raw:
            return None
        if raw.startswith("http://") or raw.startswith("https://"):
            return raw
        if raw.startswith("{") or raw.startswith("["):
            try:
                return _extract_video_url_from_kie_payload(json.loads(raw))
            except Exception:
                return None
        return None

    if isinstance(payload, list):
        for item in payload:
            url = _extract_video_url_from_kie_payload(item)
            if url:
                return url
        return None

    if isinstance(payload, dict):
        result_urls = payload.get("resultUrls") or payload.get("result_urls")
        if isinstance(result_urls, list):
            for item in result_urls:
                if isinstance(item, str) and item.startswith(("http://", "https://")):
                    return item

        direct_keys = [
            "videoUrl", "video_url", "url", "downloadUrl", "download_url",
            "src", "mp4",
        ]
        for key in direct_keys:
            value = payload.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value

        nested_keys = ["data", "output", "result", "resultJson", "result_json", "response"]
        for key in nested_keys:
            if key in payload:
                url = _extract_video_url_from_kie_payload(payload.get(key))
                if url:
                    return url
    return None


def _kie_create_video_task(*, model: str, image_url: str, start_image_url: str | None = None, end_image_url: str | None = None, prompt: str = "", duration: str = "5", audio_url: str | None = None, send_audio: bool = False, aspect_ratio: str | None = None, mode: str = "single") -> tuple[str | None, str | None]:
    endpoint = f"{settings.KIE_BASE_URL.rstrip('/')}/jobs/createTask"

    normalized_mode = str(mode or "single").strip().lower()
    if normalized_mode == "lipsync":
        input_payload = {
            "image_url": str(image_url or "").strip(),
            "audio_url": str(audio_url or "").strip(),
            "prompt": str(prompt or "").strip(),
        }
    else:
        input_payload = {"prompt": prompt, "sound": bool(send_audio), "duration": duration}

    if normalized_mode == "continuous":
        start = str(start_image_url or image_url or "").strip()
        end = str(end_image_url or image_url or "").strip()
        input_payload["image_url"] = start
        input_payload["tail_image_url"] = end
        payload_preview = json.dumps(input_payload, ensure_ascii=False, separators=(",", ":"))[:500]
        print(f"[CLIP VIDEO] continuous_model={model}")
        print(f"[CLIP VIDEO] continuous_image_url={start}")
        print(f"[CLIP VIDEO] continuous_tail_image_url={end}")
        print(f"[CLIP VIDEO] continuous_provider_input_keys={sorted(list(input_payload.keys()))}")
        print(f"[CLIP VIDEO] continuous_provider_payload_preview={payload_preview}")
    elif normalized_mode != "lipsync":
        input_payload["image_urls"] = [image_url]

    if normalized_mode != "lipsync" and (aspect_ratio or "").strip():
        input_payload["aspect_ratio"] = str(aspect_ratio).strip()

    body = {
        "model": model,
        "input": input_payload,
    }
    if normalized_mode != "lipsync" and send_audio and (audio_url or "").strip():
        body["input"]["audio_url"] = str(audio_url).strip()

    print(f"[CLIP VIDEO] provider_payload_has_audio={bool(body['input'].get('audio_url'))}")
    print(f"[CLIP VIDEO] provider_payload_sound={body['input'].get('sound')}")
    print(f"[CLIP VIDEO] provider_payload_model={body.get('model')}")
    print(f"[CLIP VIDEO] provider_input_keys={sorted(list(body.get('input', {}).keys()))}")

    callback_url = (settings.KIE_CALLBACK_URL or "").strip()
    if callback_url:
        body["callBackUrl"] = callback_url

    try:
        resp = requests.post(endpoint, headers=_kie_headers(), json=body, timeout=60)
        if resp.status_code >= 400:
            return None, f"createTask_http_{resp.status_code}:{resp.text[:300]}"
        data = resp.json()
    except RequestException as exc:
        return None, f"createTask_request_error:{str(exc)[:300]}"
    except Exception as exc:
        return None, f"createTask_parse_error:{str(exc)[:300]}"

    task_id = _extract_task_id(data)
    if not task_id:
        return None, f"createTask_task_id_missing:{str(data)[:300]}"
    return task_id, None


def _kie_query_task(task_id: str) -> tuple[dict | None, str | None]:
    endpoint = f"{settings.KIE_BASE_URL.rstrip('/')}/jobs/recordInfo"
    try:
        resp = requests.get(endpoint, headers=_kie_headers(), params={"taskId": task_id}, timeout=60)
        if resp.status_code >= 400:
            return None, f"queryTask_http_{resp.status_code}:{resp.text[:300]}"
        data = resp.json()
        if not isinstance(data, dict):
            return None, f"queryTask_malformed:{str(data)[:300]}"
        return data, None
    except RequestException as exc:
        return None, f"queryTask_request_error:{str(exc)[:300]}"
    except Exception as exc:
        return None, f"queryTask_parse_error:{str(exc)[:300]}"


def _piapi_headers() -> dict:
    return {
        "X-API-Key": str(settings.PIAPI_API_KEY or "").strip(),
        "Content-Type": "application/json",
    }


def _piapi_create_omnihuman_task(*, image_url: str, audio_url: str, prompt: str) -> tuple[str | None, str | None]:
    endpoint = f"{settings.PIAPI_BASE_URL.rstrip('/')}/task"
    body = {
        "model": str(settings.PIAPI_OMNIHUMAN_MODEL or "omni-human").strip() or "omni-human",
        "task_type": str(settings.PIAPI_OMNIHUMAN_TASK or "omni-human-1.5").strip() or "omni-human-1.5",
        "input": {
            "image_url": str(image_url or "").strip(),
            "audio_url": str(audio_url or "").strip(),
            "prompt": str(prompt or "").strip(),
            "fast_mode": True,
        },
    }

    try:
        resp = requests.post(endpoint, headers=_piapi_headers(), json=body, timeout=60)
        if resp.status_code >= 400:
            return None, f"createTask_http_{resp.status_code}:{resp.text[:300]}"
        data = resp.json()
    except RequestException as exc:
        return None, f"createTask_request_error:{str(exc)[:300]}"
    except Exception as exc:
        return None, f"createTask_parse_error:{str(exc)[:300]}"

    task_id = _extract_task_id(data)
    if not task_id:
        return None, f"createTask_task_id_missing:{str(data)[:300]}"
    return task_id, None


def _piapi_get_task(task_id: str) -> tuple[dict | None, str | None]:
    endpoint = f"{settings.PIAPI_BASE_URL.rstrip('/')}/task/{task_id}"
    try:
        resp = requests.get(endpoint, headers=_piapi_headers(), timeout=60)
        if resp.status_code >= 400:
            return None, f"queryTask_http_{resp.status_code}:{resp.text[:300]}"
        data = resp.json()
        if not isinstance(data, dict):
            return None, f"queryTask_malformed:{str(data)[:300]}"
        return data, None
    except RequestException as exc:
        return None, f"queryTask_request_error:{str(exc)[:300]}"
    except Exception as exc:
        return None, f"queryTask_parse_error:{str(exc)[:300]}"


def _piapi_wait_for_omnihuman_video(task_id: str, *, poll_interval_sec: int, poll_timeout_sec: int) -> tuple[str | None, str | None, str | None]:
    started = time.time()
    fail_statuses = {"failed", "fail", "error", "canceled", "cancelled"}

    while time.time() - started < poll_timeout_sec:
        data, err = _piapi_get_task(task_id)
        if err:
            return None, "PIAPI_TASK_FAILED", err

        status = _extract_task_status(data or {})
        if status in {"success", "succeeded", "done", "completed"}:
            video_url = _extract_video_url_from_kie_payload(data)
            if not video_url and isinstance(data, dict):
                video_url = _extract_video_url_from_kie_payload(data.get("data"))
            if not video_url:
                return None, "PIAPI_RESULT_MISSING", "result_url_not_found_in_piapi_payload"
            return video_url, None, None

        if status in fail_statuses:
            return None, "PIAPI_TASK_FAILED", f"piapi_task_status_{status}"

        time.sleep(max(1, int(poll_interval_sec)))

    return None, "PIAPI_TASK_TIMEOUT", f"poll_timeout_{poll_timeout_sec}s"


def _normalize_clip_video_transition_type(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return "single"
    aliases = {
        "single": "single",
        "hard_cut": "single",
        "hardcut": "single",
        "transition": "transition",
        "continuous": "continuous",
    }
    return aliases.get(raw, "single")


def _is_clip_video_transition_mode(transition_type: str, start_image_url: str, end_image_url: str) -> bool:
    if transition_type in {"continuous", "transition"}:
        return True
    return bool(start_image_url and end_image_url)


def _is_localhost_url(url: str) -> bool:
    try:
        host = (urlparse(str(url or "").strip()).hostname or "").strip().lower()
    except Exception:
        return False
    return host in {"127.0.0.1", "localhost", "0.0.0.0"}


def _extract_asset_filename_from_url(url: str) -> str:
    try:
        path = (urlparse(str(url or "").strip()).path or "").strip()
    except Exception:
        return ""

    marker = "/static/assets/"
    if marker not in path:
        return ""

    tail = path.split(marker, 1)[1]
    filename = os.path.basename(tail)
    return filename.strip()


def _normalize_source_image_url_for_kie(source_image_url: str) -> str:
    source_image_url = str(source_image_url or "").strip()
    if not source_image_url:
        return ""
    if not _is_localhost_url(source_image_url):
        return source_image_url

    public_base_url = str(settings.PUBLIC_BASE_URL or "").strip()
    if not public_base_url or _is_localhost_url(public_base_url):
        return source_image_url

    filename = _extract_asset_filename_from_url(source_image_url)
    if not filename:
        return source_image_url

    return _asset_url(filename)


def _prepare_provider_image_url(source_url: str) -> tuple[str, str | None]:
    source = str(source_url or "").strip()
    if not source:
        return "", None

    image_bytes = None
    image_ext = None
    image_read_error = None
    try:
        image_bytes, image_ext = _download_image_from_source(source)
    except Exception as exc:
        image_bytes = None
        image_read_error = str(exc)

    if image_bytes is not None:
        ext = (image_ext or "jpg").lower().replace(".", "")
        mime = mimetypes.types_map.get(f".{ext}", "image/jpeg")
        upload_filename = f"clip_source_{uuid4().hex}.{ext if ext in {'jpg', 'jpeg', 'png', 'webp'} else 'jpg'}"
        uploaded_image_url, upload_err = _kie_upload_image_bytes(
            image_bytes=image_bytes,
            filename=upload_filename,
            mime_type=mime,
        )
        if upload_err or not uploaded_image_url:
            return "", upload_err or "upload_failed"
        return uploaded_image_url, None

    if _is_localhost_url(source):
        return "", f"localhost_image_read_failed:{(image_read_error or 'read_failed')[:300]}"

    return source, None


def _is_public_media_url(url: str) -> bool:
    source = str(url or "").strip()
    if not source:
        return False
    parsed = urlparse(source)
    if parsed.scheme not in {"http", "https"}:
        return False
    return not _is_localhost_url(source)


def _read_audio_bytes_from_source(source_url: str) -> tuple[bytes | None, str | None, str | None, str | None]:
    source = str(source_url or "").strip()
    if not source:
        return None, None, None, "audio_source_is_empty"

    resolved_local_path = _resolve_audio_asset_path(source)
    if resolved_local_path:
        try:
            with open(resolved_local_path, "rb") as f:
                audio_bytes = f.read()
            ext = os.path.splitext(resolved_local_path)[1].lower().replace(".", "") or "mp3"
            mime = mimetypes.types_map.get(f".{ext}", "audio/mpeg")
            return audio_bytes, ext, mime, None
        except Exception as exc:
            return None, None, None, f"local_audio_read_error:{str(exc)[:300]}"

    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        try:
            resp = requests.get(source, timeout=60)
            if resp.status_code >= 400:
                return None, None, None, f"audio_http_{resp.status_code}:{resp.text[:300]}"
            audio_bytes = resp.content or b""
            if not audio_bytes:
                return None, None, None, "audio_http_empty_body"
            content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            ext = os.path.splitext(parsed.path or "")[1].lower().replace(".", "")
            if not ext and content_type:
                guessed_ext = mimetypes.guess_extension(content_type) or ""
                ext = guessed_ext.lower().replace(".", "")
            if not ext:
                ext = "mp3"
            mime = content_type or mimetypes.types_map.get(f".{ext}", "audio/mpeg")
            return audio_bytes, ext, mime, None
        except RequestException as exc:
            return None, None, None, f"audio_request_error:{str(exc)[:300]}"
        except Exception as exc:
            return None, None, None, f"audio_read_error:{str(exc)[:300]}"

    return None, None, None, "audio_source_unsupported"


def _prepare_provider_audio_url(source_url: str) -> tuple[str, str | None]:
    source = str(source_url or "").strip()
    if not source:
        return "", "audio_source_is_empty"

    if _is_public_media_url(source):
        return source, None

    audio_bytes, audio_ext, audio_mime, audio_err = _read_audio_bytes_from_source(source)
    if audio_err or not audio_bytes:
        return "", audio_err or "audio_read_failed"

    normalized_ext = (audio_ext or "mp3").lower().replace(".", "")
    if normalized_ext not in {"mp3", "wav", "ogg", "m4a", "aac", "flac"}:
        normalized_ext = "mp3"
    upload_filename = f"clip_source_audio_{uuid4().hex}.{normalized_ext}"
    upload_mime = str(audio_mime or mimetypes.types_map.get(f".{normalized_ext}", "audio/mpeg")).strip() or "audio/mpeg"

    uploaded_audio_url, upload_err = _kie_upload_audio_bytes(
        audio_bytes=audio_bytes,
        filename=upload_filename,
        mime_type=upload_mime,
    )
    if upload_err or not uploaded_audio_url:
        return "", upload_err or "audio_upload_failed"
    return uploaded_audio_url, None


def _kie_wait_for_video_result(task_id: str, *, poll_interval_sec: int, poll_timeout_sec: int) -> tuple[str | None, str | None, str | None]:
    started = time.time()
    while time.time() - started < poll_timeout_sec:
        data, err = _kie_query_task(task_id)
        if err:
            return None, "KIE_TASK_FAILED", err

        status = _extract_task_status(data or {})
        if status in {"success", "succeeded", "done", "completed"}:
            video_url = _extract_video_url_from_kie_payload(data)
            if not video_url and isinstance(data, dict):
                video_url = _extract_video_url_from_kie_payload(data.get("data"))
            if not video_url:
                return None, "KIE_RESULT_MISSING", "result_url_not_found_in_kie_payload"
            return video_url, None, None

        if status in {"failed", "error", "canceled", "cancelled"}:
            return None, "KIE_TASK_FAILED", f"kie_task_status_{status}"

        time.sleep(max(1, int(poll_interval_sec)))

    return None, "KIE_TASK_TIMEOUT", f"poll_timeout_{poll_timeout_sec}s"


def _ensure_assets_dir() -> None:
    ensure_static_dirs()


def _asset_url(filename: str) -> str:
    return asset_url(filename)


def _save_bytes_as_asset(raw: bytes, ext: str = "png") -> str:
    _ensure_assets_dir()
    ext = (ext or "png").lower().replace(".", "")
    if ext not in {"png", "jpg", "jpeg", "webp"}:
        ext = "png"
    filename = f"clip_scene_{uuid4().hex}.{ext}"
    fpath = os.path.join(str(ASSETS_DIR), filename)
    with open(fpath, "wb") as f:
        f.write(raw)
    return _asset_url(filename)


def _resolve_audio_asset_path(audio_url: str) -> str | None:
    if not audio_url:
        return None

    parsed = urlparse(audio_url)
    path = parsed.path
    if path.startswith("/static/assets/"):
        filename = os.path.basename(path[len("/static/assets/"):])
    elif path.startswith("/assets/"):
        filename = os.path.basename(path[len("/assets/"):])
    else:
        return None

    if not filename:
        return None

    base = os.path.splitext(filename)[0]
    if not base:
        return None

    dirs = [ASSETS_DIR]
    names = [filename, base, f"{base}.mp3", f"{base}.wav", f"{base}.ogg", f"{base}.m4a"]
    seen = set()
    candidates: list[str] = []
    for d in dirs:
        for n in names:
            p = os.path.join(d, n)
            if p in seen:
                continue
            seen.add(p)
            candidates.append(p)

    for p in candidates:
        if os.path.isfile(p):
            return p

    return None


def _ffmpeg_audio_slice(input_path: str, output_path: str, t0: float, t1: float) -> tuple[bool, str]:
    dur = max(0.0, t1 - t0)
    if dur < 0.05:
        dur = 0.05

    first_cmd = [
        "ffmpeg", "-y",
        "-ss", str(t0),
        "-to", str(t1),
        "-i", input_path,
        "-c", "copy",
        output_path,
    ]
    try:
        first = subprocess.run(first_cmd, capture_output=True, text=True)
        if (
            first.returncode == 0
            and os.path.isfile(output_path)
            and os.path.getsize(output_path) > 1024
        ):
            return True, ""

        fallback_cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-ss", str(t0),
            "-t", str(dur),
            "-vn",
            "-acodec", "libmp3lame",
            "-b:a", "192k",
            output_path,
        ]
        fallback = subprocess.run(fallback_cmd, capture_output=True, text=True)
        if (
            fallback.returncode == 0
            and os.path.isfile(output_path)
            and os.path.getsize(output_path) > 1024
        ):
            return True, ""

        err = (fallback.stderr or first.stderr or "ffmpeg_failed").strip()
        return False, err[:500]
    except FileNotFoundError:
        return False, "ffmpeg_missing_install_and_add_to_PATH"


def _debug_audio_slice(audio_url: str, resolved_path: str | None) -> None:
    if (settings.PS_ENV or "").lower() != "dev":
        return

    candidate_debug = []
    parsed = urlparse(audio_url or "")
    path = parsed.path or ""
    if path.startswith("/static/assets/"):
        filename = os.path.basename(path[len("/static/assets/"):])
        base = os.path.splitext(filename)[0]
    elif path.startswith("/assets/"):
        filename = os.path.basename(path[len("/assets/"):])
        base = os.path.splitext(filename)[0]
    else:
        filename = ""
        base = ""

    if filename and base:
        dirs = [ASSETS_DIR]
        names = [filename, base, f"{base}.mp3", f"{base}.wav", f"{base}.ogg", f"{base}.m4a"]
        seen = set()
        for d in dirs:
            for n in names:
                p = os.path.join(d, n)
                if p in seen:
                    continue
                seen.add(p)
                candidate_debug.append(p)

    print("AUDIO SLICE DEBUG")
    print("audioUrl:", audio_url)
    print("resolved path:", resolved_path)
    print("ASSETS_DIR:", str(ASSETS_DIR))
    print("candidate paths (first 10):")
    for p in candidate_debug[:10]:
        print(" -", p, "exists=", os.path.isfile(p))


def _mock_scene_image(scene_id: str, width: int, height: int) -> str:
    _ensure_assets_dir()
    w = max(256, min(2048, int(width or 1024)))
    h = max(256, min(2048, int(height or 1024)))
    img = Image.new("RGB", (w, h), color=(44, 48, 58))
    draw = ImageDraw.Draw(img)
    text = f"MOCK\n{scene_id or 'scene'}"
    draw.multiline_text((32, 32), text, fill=(230, 235, 245), spacing=8)
    filename = f"clip_scene_mock_{uuid4().hex}.png"
    img.save(os.path.join(str(ASSETS_DIR), filename), format="PNG")
    return _asset_url(filename)


def _decode_gemini_image(resp: dict) -> tuple[bytes, str] | None:
    try:
        for cand in (resp.get("candidates") or []):
            content = (cand or {}).get("content") or {}
            for part in (content.get("parts") or []):
                inline = part.get("inlineData") or {}
                b64 = inline.get("data")
                mime = (inline.get("mimeType") or "image/png").lower()
                if isinstance(b64, str) and b64:
                    raw = base64.b64decode(b64)
                    ext = "jpg" if "jpeg" in mime or "jpg" in mime else "png"
                    return raw, ext
    except Exception:
        return None
    return None


def _normalize_ref_list(items, max_items: int = 8) -> list[str]:
    out = []
    if not items:
        return out
    for it in items:
        if isinstance(it, str):
            url = str(it).strip()
        elif isinstance(it, dict):
            url = str(it.get("url") or "").strip()
        else:
            url = str(getattr(it, "url", "") or "").strip()
        if url:
            out.append(url)
    return out[:max_items]


def _clean_anchor_label(label: str | None) -> str:
    v = str(label or "").strip()
    v = re.sub(r"\s+", " ", v)
    return v[:120]


def _build_prop_anchor(label: str | None) -> dict | None:
    cleaned = _clean_anchor_label(label)
    if not cleaned:
        return None
    return {
        "label": cleaned,
        "source": "ref",
    }


def _planner_input_signature(*, character_refs: list[str], location_refs: list[str], style_refs: list[str], props_refs: list[str], text: str, audio_url: str, mode: str, scenario_key: str, shoot_key: str, style_key: str, freeze_style: bool, want_lipsync: bool) -> str:
    signature_payload = {
        "characterRefs": character_refs,
        "locationRefs": location_refs,
        "styleRefs": style_refs,
        "propsRefs": props_refs,
        "text": str(text or "").strip(),
        "audioUrl": str(audio_url or "").strip(),
        "mode": str(mode or "").strip(),
        "settings": {
            "scenarioKey": str(scenario_key or "").strip(),
            "shootKey": str(shoot_key or "").strip(),
            "styleKey": str(style_key or "").strip(),
            "freezeStyle": bool(freeze_style),
            "wantLipSync": bool(want_lipsync),
        },
    }
    return json.dumps(signature_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _infer_prop_anchor_label(props_images: list[dict], api_key: str, model_used: str) -> str:
    if not props_images:
        return ""
    prompt = (
        "You must identify one single object shown across all reference photos. "
        "Treat all photos as different angles/details of the SAME object. "
        "Return STRICT JSON only: {\"label\":\"...\"}. "
        "Label must be short, stable, concrete, in English (2-6 words), no punctuation, no alternatives. "
        "If uncertain, output a stable fallback generic object label."
    )
    parts = [{"text": prompt}, *props_images]
    body = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0.0,
            "responseMimeType": "application/json",
        },
    }
    resp = post_generate_content(api_key, model_used, body, timeout=60)
    raw = _extract_gemini_text(resp if isinstance(resp, dict) else {})
    parsed = _parse_json_from_text(raw)
    label = ""
    if isinstance(parsed, dict):
        label = _clean_anchor_label(parsed.get("label"))
    if not label:
        label = "anchored reference object"
    return label


def _enforce_prop_anchor_text(text: str, prop_anchor_label: str, *, lang: str) -> str:
    clean_text = str(text or "").strip()
    label = _clean_anchor_label(prop_anchor_label)
    if not label:
        return clean_text

    if lang == "ru":
        anchor_phrase = f"тот же предмет из референса ({label})"
        conflict_terms = [
            r"equipment\s+bag",
            r"generic\s+equipment",
            r"toolbox",
            r"backpack",
            r"\bbag\b",
            r"рюкзак",
            r"сумк[аеиу]",
            r"ящик\s+с\s+инструментами",
        ]
    else:
        anchor_phrase = f"the {label} from reference"
        conflict_terms = [
            r"equipment\s+bag",
            r"generic\s+equipment",
            r"toolbox",
            r"backpack",
            r"\bbag\b",
        ]

    out = clean_text
    for pattern in conflict_terms:
        out = re.sub(pattern, anchor_phrase, out, flags=re.I)

    if re.search(re.escape(label), out, flags=re.I) or re.search(r"from\s+reference|из\s+референса", out, flags=re.I):
        return out.strip()

    if not out:
        return anchor_phrase

    suffix = f" В кадре остаётся {anchor_phrase}." if lang == "ru" else f" Keep {anchor_phrase} visible."
    return (out + suffix).strip()


def _guess_image_mime(url: str, headers: dict, raw: bytes) -> str:
    header_mime = str((headers or {}).get("Content-Type") or "").split(";")[0].strip().lower()
    if header_mime.startswith("image/"):
        return header_mime

    guessed, _ = mimetypes.guess_type(url or "")
    guessed = (guessed or "").lower()
    if guessed.startswith("image/"):
        return guessed

    try:
        fmt = (Image.open(io.BytesIO(raw)).format or "").lower()
    except Exception:
        fmt = ""
    if fmt == "jpeg":
        return "image/jpeg"
    if fmt:
        return f"image/{fmt}"
    return "image/jpeg"


def _load_reference_image_inline(url: str) -> dict | None:
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        raw = r.content
        if not raw:
            return None
        mime = _guess_image_mime(url, dict(r.headers), raw)
        return {
            "inlineData": {
                "mimeType": mime,
                "data": base64.b64encode(raw).decode("ascii"),
            }
        }
    except Exception:
        return None


class RefUrlItem(BaseModel):
    url: str


class BrainRefsIn(BaseModel):
    character: list[RefUrlItem] = []
    location: list[RefUrlItem] = []
    props: list[RefUrlItem] = []
    style: RefUrlItem | list[RefUrlItem] | None = None
    propAnchorLabel: str | None = None


class BrainIn(BaseModel):
    audioUrl: str | None = None
    text: str | None = None
    mode: str | None = None

    # brain settings (optional)
    scenarioKey: str | None = None   # e.g. "beat_rhythm" | "song_meaning"
    shootKey: str | None = None      # e.g. "cinema"
    styleKey: str | None = None      # e.g. "realism"
    freezeStyle: bool | None = None

    # refs (urls)
    refs: BrainRefsIn | None = None
    propAnchorLabel: str | None = None
    characterRefs: list[RefUrlItem] | None = None
    character_refs: list[str] | None = None
    locationRefs: list[RefUrlItem] | None = None
    propsRefs: list[RefUrlItem] | None = None
    styleRef: RefUrlItem | None = None

    # legacy single-url refs (backward compatibility)
    refCharacter: str | None = None
    refLocation: str | None = None
    refStyle: str | None = None
    refItems: str | None = None

    # informational (optional)
    audioType: str | None = None     # "song" | "bg"
    textType: str | None = None      # "lyrics" | "story" | "notes"
    wantLipSync: bool | None = None


def _build_session_world_anchors(*, text: str, character_refs: list[str], location_refs: list[str], style_refs: list[str], style_key: str) -> dict[str, str]:
    text_l = (text or "").strip().lower()
    style_hint = (style_key or "").strip()

    world_cue_to_anchor = [
        ("stage", "a live performance stage environment with visible rigging, stage floor, and audience-facing orientation"),
        ("concert", "a live concert venue with performance staging, crowd space, and show-ready production design"),
        ("club", "an intimate music club venue with performance area, audience zone, and nightlife atmosphere"),
        ("theater", "a theater performance venue with a formal stage, controlled house lighting, and audience seating"),
        ("audience", "a performance venue world with audience space, stage focus, and event atmosphere"),
        ("spotlight", "a stage-oriented performance world where lighting rigs and spotlight focus define the environment"),
        ("crowd", "a live event venue with crowd area, performer focal point, and concert-like staging"),
        ("factory", "an industrial factory environment with heavy structural materials and utilitarian spatial design"),
        ("warehouse", "a large warehouse environment with raw industrial textures and open utilitarian layout"),
        ("forest", "a forest environment with natural vegetation, trees, and layered outdoor depth"),
        ("beach", "a beachside environment with open shoreline space, coastal textures, and horizon depth"),
        ("subway", "an urban subway environment with platforms, transit architecture, and underground infrastructure"),
        ("rooftop", "a rooftop environment above the city with open skyline views and elevated urban context"),
        ("church", "a church interior environment with sacred architecture and formal spatial symmetry"),
        ("bar", "a bar or lounge environment with seating, counter zones, and social nightlife mood"),
        ("studio", "a controlled studio environment for performance or filming with production equipment context"),
        ("alley", "a narrow city alley environment with enclosed urban walls and gritty street texture"),
        ("street", "an urban street environment with road geometry, surrounding buildings, and city circulation"),
        ("city", "a city environment with urban density, architecture, and active metropolitan context"),
        ("desert", "a desert environment with arid terrain, open horizon, and dry atmospheric conditions"),
        ("mountain", "a mountain environment with elevated terrain, expansive natural scale, and rugged topography"),
        ("office", "an office environment with workspaces, desks, and structured professional interior layout"),
        ("school", "a school environment with classroom/campus context and academic institutional design"),
        ("hospital", "a hospital environment with clinical infrastructure, medical spaces, and sterile interior language"),
        ("room", "an interior room environment defined by enclosed architecture and localized scene staging"),
    ]
    style_cue_to_anchor = [
        ("dimly lit", "dim, low-key lighting with selective visibility and restrained exposure"),
        ("single spotlight", "single-spotlight performance lighting with strong subject isolation and high contrast"),
        ("spotlight", "spotlight-driven lighting mood with strong subject focus and controlled falloff"),
        ("concert lighting", "concert-style dynamic stage lighting with performance-driven highlights and atmosphere"),
        ("stage lights", "stage-lighting visual language with directional beams and show-like illumination rhythm"),
        ("club lighting", "club-style lighting atmosphere with nightlife contrast and color-accent illumination"),
        ("neon", "neon-accented visual style with saturated practical glows and urban nighttime energy"),
        ("smoky", "smoky atmospheric look with suspended particulates and softened depth transitions"),
        ("warm light", "warm-toned lighting palette with amber highlights and inviting contrast"),
        ("cold light", "cool-toned lighting palette with blue/cyan bias and crisp emotional distance"),
        ("moody", "moody cinematic styling with controlled contrast, emotional shadows, and restrained brightness"),
        ("cinematic", "cinematic visual language with intentional contrast, composition, and narrative lighting"),
        ("dark atmosphere", "dark atmospheric styling with low-key exposure and dramatic tonal separation"),
        ("dramatic lighting", "dramatic lighting with strong chiaroscuro, shaped highlights, and emotional contrast"),
        ("fog", "fog-rich atmosphere with volumetric depth and softened distant detail"),
        ("haze", "haze-based atmosphere with gentle diffusion, light bloom, and layered depth"),
    ]

    text_world_anchor = ""
    text_style_anchor = ""
    for cue, anchor in world_cue_to_anchor:
        if cue in text_l:
            text_world_anchor = anchor
            break
    for cue, anchor in style_cue_to_anchor:
        if cue in text_l:
            text_style_anchor = anchor
            break

    if character_refs:
        character_anchor = "same exact person identity as character reference images"
    else:
        if any(word in text_l for word in ["woman", "girl", "her", "she"]):
            character_anchor = "a solitary woman in her early 30s with short dark hair, expressive eyes, and a dark winter coat"
        else:
            character_anchor = "a solitary man in his early 30s with short dark hair and a trimmed beard, wearing a dark winter coat"

    if location_refs:
        location_anchor = "same exact world/location identity as location reference images"
    else:
        location_anchor = text_world_anchor or "a narrow European winter street with old brick buildings and wet cobblestone pavement"

    if style_refs:
        style_anchor = "same exact style identity from style reference images: weather, season, palette, atmosphere, and lighting mood"
    else:
        style_anchor = text_style_anchor or style_hint or "cold cinematic realism, muted winter palette, overcast sky, wet reflective pavement, atmospheric haze"

    return {
        "character": character_anchor,
        "location": location_anchor,
        "style": style_anchor,
    }


def _inject_session_world_anchors(prompt: str, anchors: dict[str, str]) -> str:
    base = (prompt or "").strip()
    anchor_text = (
        "SESSION WORLD ANCHORS:\n"
        f"Character anchor: {anchors.get('character', '')}\n"
        f"Location anchor: {anchors.get('location', '')}\n"
        f"Style anchor: {anchors.get('style', '')}\n\n"
        "These anchors define the persistent identity of the clip world and must remain unchanged across all frames."
    )
    return f"{base}\n\n{anchor_text}" if base else anchor_text


def _trim_continuity_value(value: str, limit: int = 220) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _derive_production_scale(*, session_world_anchors: dict[str, str], scene: dict) -> str:
    hints: list[str] = []
    for key in ["location", "style"]:
        v = _trim_continuity_value((session_world_anchors or {}).get(key) or "", 260)
        if v:
            hints.append(v.lower())
    for key in ["productionScale", "venueScale", "venueType", "audienceScale", "worldState", "eventState", "visualDescription"]:
        v = _trim_continuity_value((scene or {}).get(key) or "", 260)
        if v:
            hints.append(v.lower())

    combined = " ".join(hints)
    if any(token in combined for token in ["arena", "stadium", "festival", "massive stage", "pyro tower", "jumbotron"]):
        return "large arena/festival production scale"
    if any(token in combined for token in ["club", "small venue", "intimate", "indoor room", "bar stage", "medium venue"]):
        return "small-to-medium intimate concert production scale"
    return "same established concert production scale class from opening scenes"


_WORLD_SCALE_CONTEXTS = {
    "human_world",
    "hero_vs_giant",
    "micro_world",
    "animal_scale",
    "space_scale",
    "mythic_world",
}


def _normalize_world_scale_context(value: str | None) -> str:
    ctx = str(value or "").strip().lower()
    return ctx if ctx in _WORLD_SCALE_CONTEXTS else ""


def _extract_entity_scale_anchors(raw: dict | None) -> dict[str, float]:
    if not isinstance(raw, dict):
        return {}
    anchors: dict[str, float] = {}
    for key, value in raw.items():
        name = re.sub(r"[^a-zA-Z0-9_\-]", "", str(key or "").strip().lower())
        if not name:
            continue
        try:
            num = float(value)
        except Exception:
            continue
        if num > 0:
            anchors[name] = round(num, 2)
    return anchors


def _detect_world_scale_context(*, text: str, scenes: list[dict], session_world_anchors: dict[str, str]) -> str:
    tokens = " ".join(
        [
            str(text or ""),
            " ".join(str((session_world_anchors or {}).get(k) or "") for k in ["location", "style"]),
            " ".join(str((s or {}).get("visualDescription") or "") for s in scenes[:4]),
            " ".join(str((s or {}).get("visualPrompt") or "") for s in scenes[:4]),
        ]
    ).lower()
    if any(k in tokens for k in ["planet", "spaceship", "starship", "cosmic", "orbit", "galaxy", "space"]):
        return "space_scale"
    if any(k in tokens for k in ["tiny human", "tiny person", "insect", "ant", "beetle", "spider", "ladybug", "blade of grass", "macro world", "micro"]):
        return "micro_world"
    if any(k in tokens for k in ["dragon", "hydra", "myth", "mythic", "leviathan", "behemoth"]):
        return "mythic_world"
    if any(k in tokens for k in ["giant", "towering", "colossal", "titan", "kaiju", "monster", "beast", "colossus", "mech", "giant creature", "massive creature"]):
        return "hero_vs_giant"
    if any(k in tokens for k in ["horse", "elephant", "predator", "beast", "wolf", "animal"]):
        return "animal_scale"
    return "human_world"


def _default_entity_scale_anchors(context: str) -> dict[str, float]:
    defaults = {
        "human_world": {"human": 1.0},
        "hero_vs_giant": {"hero": 1.0, "threat": 6.0},
        "micro_world": {"human": 0.1, "insect": 3.0, "environment": 10.0},
        "animal_scale": {"human": 1.0, "large_animal": 4.0},
        "space_scale": {"human": 1.0, "fighter": 20.0, "capital_ship": 300.0},
        "mythic_world": {"human": 1.0, "mythic_creature": 12.0},
    }
    return dict(defaults.get(context) or {"human": 1.0})


def _format_entity_scale_anchors(anchors: dict[str, float]) -> str:
    if not anchors:
        return ""
    ordered = sorted(anchors.items(), key=lambda kv: kv[1])
    return ", ".join(f"{k}:{v:g}" for k, v in ordered)


_TRANSITION_TYPES = {"continuous", "single", "hard_cut"}


def _normalize_transition_type(value) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in _TRANSITION_TYPES else "single"


def _infer_transition_type(scene: dict) -> str:
    if not isinstance(scene, dict):
        return "single"

    def _norm_text(value) -> str:
        if value is None:
            return ""
        if isinstance(value, dict):
            return " ".join(f"{k}:{v}" for k, v in value.items())
        if isinstance(value, list):
            return " ".join(str(v) for v in value)
        return str(value)

    def _norm_compact(value) -> str:
        return re.sub(r"\s+", " ", _norm_text(value).strip().lower())

    continuity_memory = scene.get("continuityMemory") if isinstance(scene.get("continuityMemory"), dict) else {}
    previous_continuity_memory = scene.get("previousContinuityMemory") if isinstance(scene.get("previousContinuityMemory"), dict) else {}

    token_fields = [
        scene.get("sceneType"),
        scene.get("shotPurpose"),
        scene.get("visualDescription"),
        scene.get("visualPrompt"),
        scene.get("reason"),
        scene.get("motion"),
        scene.get("camera"),
    ]
    continuity_fields = [
        continuity_memory.get("location"),
        continuity_memory.get("worldState"),
        continuity_memory.get("worldScaleContext"),
        previous_continuity_memory.get("location"),
        previous_continuity_memory.get("worldState"),
        previous_continuity_memory.get("worldScaleContext"),
    ]
    tokens = " ".join(_norm_text(v) for v in [*token_fields, *continuity_fields]).lower()

    hard_cut_tokens = [
        "location change",
        "new location",
        "another location",
        "different location",
        "different city",
        "new city",
        "different world",
        "new place",
        "time jump",
        "time skip",
        "next day",
        "later that night",
        "meanwhile",
        "elsewhere",
        "new chapter",
        "chapter break",
        "new world",
        "hard cut",
        "cut to",
        "montage",
        "flashback",
        "flash forward",
        "dream",
        "memory",
        "vision",
    ]
    continuous_tokens = [
        "reveal",
        "revealing",
        "emerge",
        "emerging",
        "emergence",
        "rise",
        "rising",
        "eruption",
        "erupts",
        "bursts through",
        "forms",
        "forming",
        "approach",
        "approaching",
        "advance",
        "advancing",
        "walk",
        "walking",
        "run",
        "running",
        "chase",
        "transformation",
        "transforming",
        "escalation",
        "impact",
        "clash",
        "combat",
        "build-up",
        "buildup",
        "progression",
        "movement",
        "motion",
        "unfolds",
        "develops",
    ]

    hard_cut_evidence = any(token in tokens for token in hard_cut_tokens)
    continuous_evidence = any(token in tokens for token in continuous_tokens)

    continuity_changed = False
    changed_fields = []
    for field in ["location", "worldState", "worldScaleContext"]:
        previous_value = _norm_compact(previous_continuity_memory.get(field))
        current_value = _norm_compact(continuity_memory.get(field))
        if previous_value and current_value and previous_value != current_value:
            changed_fields.append(field)
    if changed_fields:
        continuity_changed = True

    if hard_cut_evidence or (continuity_changed and (hard_cut_evidence or "new" in tokens or "different" in tokens or "change" in tokens or "switch" in tokens)):
        return "hard_cut"

    if continuous_evidence:
        return "continuous"
    return "single"


def _build_scene_continuity_memory(*, scene: dict, session_world_anchors: dict[str, str], prop_anchor_label: str) -> dict[str, str]:

    location = _trim_continuity_value(
        session_world_anchors.get("location")
        or "same established world/location identity, architecture/set identity, and environment geometry"
    )
    style_anchor = _trim_continuity_value(session_world_anchors.get("style") or "")

    lighting = _trim_continuity_value(
        f"persistent lighting logic from established world: {style_anchor}"
        if style_anchor
        else "same lighting source logic, direction style, and contrast/softness feel"
    )
    color_palette = _trim_continuity_value(
        f"persistent production palette/grade mood from style anchor: {style_anchor}"
        if style_anchor
        else "same dominant grade and palette mood (warm/cold/neon/desaturated)"
    )

    world_state_candidates = []
    for token in [scene.get("worldState"), scene.get("eventState"), scene.get("atmosphere"), scene.get("timeOfDay")]:
        cleaned = _trim_continuity_value(token or "", 180)
        if cleaned:
            world_state_candidates.append(cleaned)
    world_state = _trim_continuity_value(
        "; ".join(dict.fromkeys(world_state_candidates)),
        240,
    )
    if not world_state:
        world_state = "same persistent world condition: weather/time-of-day/event-state remain coherent; update only scene-local action"

    return {
        "location": location,
        "lighting": lighting,
        "colorPalette": color_palette,
        "cameraLanguage": _trim_continuity_value(
            "same production camera language and lens feel (handheld/smooth/locked/depth style); vary framing for progression"
        ),
        "characterState": _trim_continuity_value(
            session_world_anchors.get("character") or "same character identity, wardrobe, and persistent visual traits"
        ),
        "worldState": world_state,
        "propState": _trim_continuity_value(
            f"same persistent prop identities and scale class: {prop_anchor_label}" if prop_anchor_label else "same important prop identities and scale class"
        ),
        "worldScaleContext": _trim_continuity_value(str((scene or {}).get("worldScaleContext") or "same persistent world scale context"), 120),
        "entityScaleAnchors": _trim_continuity_value(str((scene or {}).get("entityScaleAnchors") or "same entity relative size anchors across scenes"), 220),
        "productionScale": _trim_continuity_value(
            _derive_production_scale(session_world_anchors=session_world_anchors, scene=scene),
            220,
        ),
        "audienceState": _trim_continuity_value(
            "same event audience identity, crowd scale class, density logic, and front-row geometry; reactions may intensify without changing who this crowd is"
        ),
    }


def _sanitize_continuity_memory(memory: dict | None) -> dict[str, str] | None:
    if not isinstance(memory, dict):
        return None
    cleaned = {}
    for key in ["location", "lighting", "colorPalette", "cameraLanguage", "characterState", "worldState", "propState", "worldScaleContext", "entityScaleAnchors", "productionScale", "audienceState"]:
        value = _trim_continuity_value(memory.get(key) or "")
        if value:
            cleaned[key] = value
    return cleaned or None


def _scene_value(scene: dict, keys: list[str], limit: int = 160) -> str:
    for key in keys:
        raw = scene.get(key)
        if raw is None:
            continue
        text = _trim_continuity_value(raw, limit)
        if text:
            return text
    return ""


def _build_scene_delta(scene: dict, previous_scene: dict | None = None) -> str:
    """Build a delta-focused scene summary (changes only, not full-world restatement)."""
    prev = previous_scene or {}
    parts: list[str] = []

    action = _scene_value(scene, ["action", "actionBeat", "momentAction", "blocking", "shotPurpose", "reason"], 180)
    emotion = _scene_value(scene, ["emotionalBeat", "emotion", "mood", "lyricFragment", "lipSyncText"], 150)
    camera = _scene_value(scene, ["framingChange", "framing", "camera", "shotType", "sceneType"], 150)
    motion = _scene_value(scene, ["motion", "cameraMove", "movement", "movementType"], 130)
    intensity = _scene_value(scene, ["intensityProgression", "intensity", "energyShift", "energy", "dynamic"], 140)
    crowd = _scene_value(scene, ["crowdVisibility", "crowdReaction", "audienceVisibility", "audienceReaction"], 140)
    escalation = _scene_value(scene, ["eventEscalation", "eventState", "eventBeat", "worldState"], 160)

    if action:
        parts.append(f"action: {action}")

    if emotion:
        parts.append(f"emotion: {emotion}")

    prev_camera = _scene_value(prev, ["framingChange", "framing", "camera", "shotType", "sceneType"], 150)
    shot_change_detail = camera
    if motion:
        shot_change_detail = f"{shot_change_detail}; motion: {motion}" if shot_change_detail else f"motion: {motion}"
    if shot_change_detail:
        if prev_camera and camera and camera != prev_camera:
            parts.append(f"shot change: {prev_camera} -> {shot_change_detail}")
        else:
            parts.append(f"shot change: {shot_change_detail}")

    prev_intensity = _scene_value(prev, ["intensityProgression", "intensity", "energyShift", "energy", "dynamic"], 120)
    if intensity:
        if prev_intensity and intensity != prev_intensity:
            parts.append(f"intensity: {prev_intensity} -> {intensity}")
        else:
            parts.append(f"intensity: {intensity}")

    if crowd:
        parts.append(f"crowd: {crowd}")

    prev_escalation = _scene_value(prev, ["eventEscalation", "eventState", "eventBeat", "worldState"], 120)
    if escalation:
        if prev_escalation and escalation != prev_escalation:
            parts.append(f"event escalation: {prev_escalation} -> {escalation}")
        else:
            parts.append(f"event escalation: {escalation}")

    if parts:
        return " | ".join(parts)

    fallback_action = _scene_value(scene, ["visualDescription", "reason", "shotPurpose"], 180)
    return f"action: {fallback_action}" if fallback_action else "action: continue next beat"


def _extract_gemini_text(resp: dict) -> str:
    try:
        cands = resp.get("candidates") or []
        if not cands:
            return ""
        content = (cands[0] or {}).get("content") or {}
        parts = content.get("parts") or []
        texts = []
        for p in parts:
            t = p.get("text")
            if isinstance(t, str) and t.strip():
                texts.append(t)
        return "\n".join(texts).strip()
    except Exception:
        return ""


def _parse_json_from_text(s: str) -> dict | None:
    if not s:
        return None

    def _balance_json_tail(chunk: str) -> str:
        stack = []
        in_string = False
        escape = False
        for ch in chunk:
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "[{":
                stack.append(ch)
            elif ch in "]}":
                if stack and ((ch == "]" and stack[-1] == "[") or (ch == "}" and stack[-1] == "{")):
                    stack.pop()
        if in_string:
            chunk += '"'
        for opener in reversed(stack):
            chunk += "]" if opener == "[" else "}"
        return chunk

    s2 = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", s.strip(), flags=re.M)
    s2 = re.sub(r"\s*```\s*$", "", s2, flags=re.M)
    m = re.search(r"\{[\s\S]*\}", s2)
    chunks_to_try = [m.group(0)] if m else []

    first_brace = s2.find("{")
    if first_brace >= 0:
        tail = s2[first_brace:]
        last_closed = max(tail.rfind("}"), tail.rfind("]"))
        if last_closed > 0:
            chunks_to_try.append(tail[: last_closed + 1])
        chunks_to_try.append(tail)

    seen = set()
    for chunk in chunks_to_try:
        if not chunk or chunk in seen:
            continue
        seen.add(chunk)
        for candidate in (chunk, re.sub(r",\s*([}\]])", r"\1", chunk), _balance_json_tail(chunk)):
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                continue
    return None


def _combined_error_text(resp: dict | None) -> str:
    if not isinstance(resp, dict):
        return ""
    parts = [
        resp.get("text"),
        resp.get("error"),
        resp.get("detail"),
    ]
    out = []
    for part in parts:
        if part is None:
            continue
        if isinstance(part, str):
            out.append(part)
        else:
            out.append(json.dumps(part, ensure_ascii=False))
    return "\n".join([x for x in out if x]).strip()


def _is_model_unsupported_error(text: str) -> bool:
    s = (text or "").lower()
    needles = [
        "not found for api version",
        "not supported for generatecontent",
        "model not found",
    ]
    return any(n in s for n in needles)


def _pick_fallback_model(model_used: str | None) -> str:
    model = (model_used or "").strip()
    for candidate in ("gemini-2.5-flash", "gemini-2.0-flash"):
        if candidate and candidate != model:
            return candidate
    return "gemini-2.5-flash"


def get_audio_duration(url: str) -> float:
    """Получаем длительность аудио через ffprobe"""
    try:
        if os.path.isfile(url):
            path = url
            temp_path = None
        else:
            r = requests.get(url, timeout=20)
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".mp3") as f:
                f.write(r.content)
                path = f.name
                temp_path = path

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
        result = subprocess.run(cmd, capture_output=True, text=True)
        dur = float((result.stdout or "").strip())
        if math.isfinite(dur) and dur > 0:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)
            return float(dur)
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)
        return 30.0
    except Exception:
        return 30.0


def _probe_audio_duration(path: str) -> float | None:
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
        result = subprocess.run(cmd, capture_output=True, text=True)
        dur = float((result.stdout or "").strip())
        if math.isfinite(dur) and dur > 0:
            return float(dur)
    except Exception:
        return None
    return None


def _load_audio_for_planner(audio_url: str | None) -> tuple[float, bytes | None, str, dict]:
    duration: float | None = None
    audio_mime = "audio/mpeg"
    debug = {
        "inputAudioUrl": audio_url or None,
        "resolvedPath": None,
        "audioBytesFound": False,
        "audioBytesSource": "none",
        "audioMime": audio_mime,
        "durationSec": None,
        "durationSource": "unknown",
        "audioLoadError": None,
        "hint": "",
    }

    if not audio_url:
        debug["durationSource"] = "default_no_audio"
        debug["hint"] = "audio_url_missing"
        return 30.0, None, audio_mime, debug

    resolved_path = _resolve_audio_asset_path(audio_url)
    if resolved_path and os.path.isfile(resolved_path):
        debug["resolvedPath"] = resolved_path
        ext = (os.path.splitext(resolved_path)[1] or "").lower()
        if ext == ".wav":
            audio_mime = "audio/wav"
        elif ext == ".ogg":
            audio_mime = "audio/ogg"
        elif ext == ".m4a":
            audio_mime = "audio/mp4"
        duration = _probe_audio_duration(resolved_path)
        if duration is not None:
            debug["durationSec"] = duration
            debug["durationSource"] = "local_ffprobe"
        try:
            with open(resolved_path, "rb") as f:
                audio_bytes = f.read()
            if audio_bytes:
                debug["audioBytesFound"] = True
                debug["audioBytesSource"] = "local_asset"
                debug["audioMime"] = audio_mime
                debug["hint"] = "audio_loaded_from_local_asset"
                return float(duration or 30.0), audio_bytes, audio_mime, debug
        except Exception as e:
            debug["audioLoadError"] = f"local_asset_read_failed:{str(e)[:180]}"

    try:
        r = requests.get(audio_url, timeout=30)
        r.raise_for_status()
        header_mime = str(r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if header_mime:
            audio_mime = header_mime
        audio_bytes = r.content
        if audio_bytes:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".audio") as f:
                f.write(audio_bytes)
                tmp_path = f.name
            try:
                probed = _probe_audio_duration(tmp_path)
                if probed is not None:
                    duration = probed
                    debug["durationSec"] = duration
                    debug["durationSource"] = "http_ffprobe"
            finally:
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            debug["audioBytesFound"] = True
            debug["audioBytesSource"] = "http"
            debug["audioMime"] = audio_mime
            debug["hint"] = "audio_loaded_over_http"
            return float(duration or 30.0), audio_bytes, audio_mime, debug
    except Exception as e:
        debug["audioLoadError"] = f"http_audio_load_failed:{str(e)[:180]}"

    if duration is not None:
        debug["durationSec"] = duration
        if debug["durationSource"] == "unknown":
            debug["durationSource"] = "ffprobe_without_audio_bytes"
    else:
        debug["durationSource"] = "default_fallback"
    debug["hint"] = "audio_not_found_or_unreachable_planner_built_without_audio_bytes"
    return float(duration or 30.0), None, audio_mime, debug


def _validate_storyboard_timeline(duration: float, scenes: list[dict]) -> tuple[bool, str | None, list[str]]:
    if not scenes:
        return False, "scenes_empty", []
    tol_edge = 0.75
    tol_touch = 0.3
    max_gap = 0.75
    warnings: list[str] = []

    starts = [float(scene.get("start") or 0.0) for scene in scenes]
    if starts != sorted(starts):
        return False, "timeline_unsorted", warnings

    sorted_scenes = scenes

    for idx, scene in enumerate(sorted_scenes):
        start = float(scene.get("start") or 0.0)
        end = float(scene.get("end") or 0.0)
        if start < -tol_edge:
            return False, f"timeline_scene_start_oob_at_{idx}", warnings
        if end > float(duration) + tol_edge:
            return False, f"timeline_scene_end_oob_at_{idx}", warnings

    first_start = float(sorted_scenes[0].get("start") or 0.0)
    last_end = float(sorted_scenes[-1].get("end") or 0.0)

    if abs(first_start - 0.0) > tol_edge:
        return False, "timeline_bad_start", warnings
    if abs(last_end - float(duration)) > tol_edge:
        return False, "timeline_bad_end", warnings

    for idx in range(1, len(sorted_scenes)):
        prev_end = float(sorted_scenes[idx - 1].get("end") or 0.0)
        cur_start = float(sorted_scenes[idx].get("start") or 0.0)
        delta = cur_start - prev_end
        if delta < -tol_touch:
            return False, f"timeline_overlap_at_{idx}", warnings
        if delta > max_gap:
            return False, f"timeline_gap_at_{idx}", warnings
        if abs(delta) > tol_touch:
            warnings.append(f"timeline_micro_gap_at_{idx}")
    return True, None, warnings


def _format_audio_analysis_summary(audio_analysis: dict) -> str:
    duration = float(audio_analysis.get("duration") or 0.0)
    bpm = float(audio_analysis.get("bpm") or 0.0)
    downbeats = audio_analysis.get("downbeats") or []
    vocal_phrases = audio_analysis.get("vocalPhrases") or []
    energy_peaks = audio_analysis.get("energyPeaks") or []
    sections = audio_analysis.get("sections") or []

    section_lines = []
    for sec in sections[:6]:
        sec_type = str(sec.get("type") or "section")
        sec_start = float(sec.get("start") or 0.0)
        sec_end = float(sec.get("end") or 0.0)
        section_lines.append(f"{sec_type}({sec_start:.2f}-{sec_end:.2f})")

    phrase_lines = []
    for phr in vocal_phrases[:6]:
        p0 = float(phr.get("start") or 0.0)
        p1 = float(phr.get("end") or 0.0)
        phrase_lines.append(f"{p0:.2f}-{p1:.2f}")

    peak_lines = [f"{float(t):.2f}" for t in energy_peaks[:8]]

    summary = "\nAUDIO ANALYSIS:"
    summary += f"\nduration={duration:.2f}"
    summary += f"\nbpm={bpm:.0f}" if bpm > 0 else "\nbpm=0"
    summary += f"\ndownbeats={len(downbeats)}"
    summary += f"\nvocalPhrases={len(vocal_phrases)}"
    summary += f"\nenergyPeaks={len(energy_peaks)}"
    summary += "\nsections=" + (", ".join(section_lines) if section_lines else "none")
    if phrase_lines:
        summary += "\nvocalPhrases(first6):\n" + "\n".join(phrase_lines)
    if peak_lines:
        summary += "\nenergyPeaks(first8):\n" + "\n".join(peak_lines)
    return summary


def _fallback_plan(duration: float, text: str | None):
    scene_len = 5.0
    scene_count = max(1, math.ceil(duration / scene_len))
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()] if text else []
    chunks = []
    if lines:
        step = max(1, math.floor(len(lines) / scene_count))
        for i in range(scene_count):
            part = lines[i * step : (i + 1) * step]
            chunks.append(" ".join(part))
    else:
        for i in range(scene_count):
            chunks.append(f"Scene {i+1}")

    scenes = []
    t = 0.0
    for i in range(scene_count):
        ch = chunks[i] if i < len(chunks) else ""
        t1 = min(duration, t + scene_len)
        scenes.append({
            "id": f"s{i+1:02d}",
            "start": float(t),
            "end": float(t1),
            "why": "резервная нарезка по равным сегментам",
            "sceneText": ch,
            "imagePrompt": f"Кинематографичная сцена: {ch}",
            "videoPrompt": "Кинематографичное движение камеры, драматичный свет, зерно плёнки",
            "audioType": "mixed",
            "sceneType": "visual_rhythm",
            "hasVocals": False,
            "isLipSync": False,
            "lyricFragment": "",
            "timingReason": "резервная нарезка на равные по длительности отрезки",
            "beatAnchor": "bar_start",
            "performanceType": "cinematic_visual",
            "shotType": "wide",
        })
        t = t1
        if t >= duration:
            break
    # ensure last end == duration
    if scenes:
        scenes[-1]["end"] = float(duration)
    return scenes


def _normalize_scenes(duration: float, scenes: list[dict]) -> list[dict]:
    """Ensure scenes are valid and cover full duration."""
    out = []
    allowed_product_views = {"hero", "wide", "side", "detail", "interaction", "macro"}
    for i, s in enumerate(scenes or []):
        try:
            t0 = float(s.get("start", s.get("t0", 0.0)))
            t1 = float(s.get("end", s.get("t1", 0.0)))
        except Exception:
            continue
        if not (math.isfinite(t0) and math.isfinite(t1)):
            continue
        if t1 <= t0:
            continue
        audio_type = str(s.get("audioType") or "mixed")
        scene_type = str(s.get("sceneType") or "visual_rhythm")
        has_vocals = bool(s.get("hasVocals") is True)
        is_lipsync = bool(s.get("isLipSync") is True or s.get("lipSync") is True)
        lyric_fragment = str(s.get("lyricFragment") or "").strip()
        timing_reason = str(s.get("timingReason") or s.get("why") or "")

        performance_type = str(s.get("performanceType") or "cinematic_visual")
        shot_type = str(s.get("shotType") or "")
        product_view = str(s.get("productView") or "").strip().lower()
        if product_view not in allowed_product_views:
            product_view = ""

        wants_lipsync = is_lipsync or scene_type == "lipSync"
        missing_vocal_phrase = not lyric_fragment
        instrumental_slice = audio_type == "instrumental" or not has_vocals
        seg_duration = t1 - t0
        too_short_lipsync = seg_duration < 1.0
        short_with_lyric = seg_duration < 1.5 and bool(lyric_fragment)
        if wants_lipsync and (instrumental_slice or missing_vocal_phrase or too_short_lipsync or short_with_lyric):
            only_missing_phrase_issue = missing_vocal_phrase and audio_type != "instrumental" and has_vocals
            if not only_missing_phrase_issue:
                has_vocals = False
            is_lipsync = False
            scene_type = "vocal" if (audio_type != "instrumental" and has_vocals) else "visual_rhythm"
            performance_type = "cinematic_visual"
            if shot_type == "mouth_closeup":
                shot_type = "medium"
            if too_short_lipsync and missing_vocal_phrase:
                fallback_reason = "lipSync disabled: segment too short and lyricFragment is empty"
            elif short_with_lyric:
                fallback_reason = "lipSync disabled: segment too short for a coherent vocal phrase"
            else:
                fallback_reason = "lipSync disabled: vocal phrase not confirmed for this segment"
            timing_reason = f"{timing_reason}; {fallback_reason}" if timing_reason else fallback_reason

        normalized_scene = {
            "id": str(s.get("id") or f"s{i+1:02d}"),
            "start": round(t0, 2),
            "end": round(t1, 2),
            "why": str(s.get("why") or ""),
            "sceneText": str(s.get("sceneText") or ""),
            "imagePrompt": str(s.get("imagePrompt") or s.get("prompt") or s.get("sceneText") or ""),
            "videoPrompt": str(s.get("videoPrompt") or ""),
            "audioType": audio_type,
            "sceneType": scene_type,
            "hasVocals": has_vocals,
            "isLipSync": is_lipsync,
            "lyricFragment": lyric_fragment,
            "timingReason": timing_reason,
            "beatAnchor": str(s.get("beatAnchor") or ""),
            "performanceType": performance_type,
            "shotType": shot_type,
        }
        if product_view:
            normalized_scene["productView"] = product_view
        out.append(normalized_scene)
    if not out:
        return out
    # clamp and sort
    out.sort(key=lambda x: x["start"])
    # clamp to [0,duration]
    for s in out:
        s["start"] = max(0.0, min(float(duration), float(s["start"])))
        s["end"] = max(0.0, min(float(duration), float(s["end"])))
        if s["end"] <= s["start"]:
            s["end"] = min(float(duration), s["start"] + 0.5)
    # force first start 0 and last end duration (soft)
    out[0]["start"] = 0.0
    out[-1]["end"] = float(duration)
    # remove overlaps / make monotonic
    for i in range(1, len(out)):
        if out[i]["start"] < out[i-1]["end"]:
            out[i]["start"] = out[i-1]["end"]
            if out[i]["end"] <= out[i]["start"]:
                out[i]["end"] = min(float(duration), out[i]["start"] + 0.5)
    out[-1]["end"] = float(duration)
    return out


def _minimum_scene_count_for_repair(duration: float) -> int:
    if duration >= 60:
        return 10
    if duration >= 45:
        return 8
    if duration >= 30:
        return 7
    if duration >= 15:
        return 5
    return 3


def _validate_planner_scenes_quality(duration: float, scenario_key: str, scenes: list[dict]) -> dict:
    scene_count = len(scenes or [])
    empty_scene_text_count = 0
    empty_image_prompt_count = 0
    empty_video_prompt_count = 0
    empty_core_scene_count = 0

    for scene in scenes or []:
        scene_text = str(scene.get("sceneText") or "").strip()
        image_prompt = str(scene.get("imagePrompt") or "").strip()
        video_prompt = str(scene.get("videoPrompt") or "").strip()

        is_scene_text_empty = not scene_text
        is_image_prompt_empty = not image_prompt
        is_video_prompt_empty = not video_prompt

        if is_scene_text_empty:
            empty_scene_text_count += 1
        if is_image_prompt_empty:
            empty_image_prompt_count += 1
        if is_video_prompt_empty:
            empty_video_prompt_count += 1
        if is_scene_text_empty and is_image_prompt_empty and is_video_prompt_empty:
            empty_core_scene_count += 1

    warnings: list[str] = []
    rejected_reasons: list[str] = []
    scenario = (scenario_key or "").strip().lower()
    min_clip_scenes_for_repair = _minimum_scene_count_for_repair(duration) if scenario == "clip" else 0
    is_weak_clip_plan = bool(scenario == "clip" and scene_count < min_clip_scenes_for_repair)
    if scenario == "clip":
        if duration >= 12 and scene_count < 2:
            warnings.append("scene_count_below_min_for_12s")
        if duration >= 20 and scene_count < 3:
            warnings.append("scene_count_below_min_for_20s")
        if duration >= 30 and scene_count < 4:
            warnings.append("scene_count_below_min_for_30s")
        if scene_count < min_clip_scenes_for_repair:
            warnings.append(f"scene_count_below_repair_min_for_clip:{scene_count}<{min_clip_scenes_for_repair}")
        if is_weak_clip_plan:
            warnings.append("weak_clip_plan")

    if scene_count == 1:
        only = scenes[0]
        coverage = max(0.0, float(only.get("end") or 0.0) - float(only.get("start") or 0.0))
        only_scene_text = str(only.get("sceneText") or "").strip()
        only_image_prompt = str(only.get("imagePrompt") or "").strip()
        only_video_prompt = str(only.get("videoPrompt") or "").strip()
        only_core_empty = not only_scene_text and not only_image_prompt and not only_video_prompt

        if duration > 0 and (coverage / duration) >= 0.9 and only_core_empty:
            rejected_reasons.append("single_scene_covers_almost_entire_track")

    if empty_scene_text_count > 0:
        warnings.append("has_empty_sceneText")
    if empty_image_prompt_count > 0:
        warnings.append("has_empty_imagePrompt")
    if empty_video_prompt_count > 0:
        warnings.append("has_empty_videoPrompt")

    if scene_count == 0:
        rejected_reasons.append("empty_scenes")
    if scene_count > 0 and empty_core_scene_count > (scene_count / 2):
        rejected_reasons.append("more_than_half_scenes_empty_core_fields")

    rejected_reason = ",".join(rejected_reasons) if rejected_reasons else None
    return {
        "scenario": scenario,
        "sceneCount": scene_count,
        "emptySceneTextCount": empty_scene_text_count,
        "emptyImagePromptCount": empty_image_prompt_count,
        "emptyVideoPromptCount": empty_video_prompt_count,
        "emptyCoreSceneCount": empty_core_scene_count,
        "warnings": warnings,
        "rejectedReason": rejected_reason,
        "repairRetryUsed": False,
        "weakClipPlan": is_weak_clip_plan,
    }


def _build_planning_semantics(
    text: str,
    scenario_key: str,
    audio_type_hint: str,
    text_type_hint: str,
    want_lipsync: bool,
    character_refs: list[str],
    location_refs: list[str],
    props_refs: list[str],
    style_key: str,
) -> dict:
    text_l = (text or "").lower()
    style_l = (style_key or "").lower()
    hint_audio_l = (audio_type_hint or "").lower()
    hint_text_l = (text_type_hint or "").lower()

    product_keywords = [
        "прод", "товар", "описан", "аппарат", "product", "commercial", "sale", "selling", "welding",
    ]
    is_product_text = bool(text_l) and any(k in text_l for k in product_keywords)

    text_types = []
    if hint_text_l:
        text_types.append(hint_text_l)
    if is_product_text:
        text_types.extend(["commercial description", "product narrative"])
    if not text_types and text_l:
        text_types.append("story")

    has_song_vocals = hint_audio_l in {"song", "song_with_vocals", "vocals"} or want_lipsync
    audio_type = "song_with_vocals" if has_song_vocals else (hint_audio_l or "mixed")

    has_character = bool(character_refs)
    has_location = bool(location_refs)
    has_style = bool(style_key)
    product_ref_count = len(props_refs)
    product_mode = bool(product_ref_count and (is_product_text or "product" in hint_text_l or "commercial" in hint_text_l))

    props_role = "multi-angle product reference" if product_mode and product_ref_count > 1 else "generic props"
    mode_key = (scenario_key or "").strip().lower()
    if mode_key == "clip" and product_mode:
        mode_interpretation = "clip_product_performance"
    elif mode_key == "clip":
        mode_interpretation = "music_driven_visual_montage"
    else:
        mode_interpretation = "generic_storyboard"

    return {
        "textType": text_types,
        "audioType": audio_type,
        "storySource": "TEXT" if text_l else "AUDIO",
        "timingSource": "AUDIO" if mode_key == "clip" else "TEXT",
        "speechSource": "AUDIO" if has_song_vocals else ("TEXT" if text_l else "NONE"),
        "audioRole": ["emotion source", "rhythm source", "timing source"] if has_song_vocals else ["timing source"],
        "propsRole": props_role,
        "productMode": product_mode,
        "productRefCount": product_ref_count,
        "hasCharacter": has_character,
        "hasLocation": has_location,
        "hasStyle": has_style,
        "modeInterpretation": mode_interpretation,
        "styleApplication": "historical_world_modern_product" if "18" in style_l and product_mode else "default",
    }


def _normalize_lipsync_shot_type(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "close": "close_up",
        "closeup": "close_up",
        "close_up": "close_up",
        "mouth_closeup": "close_up",
        "medium": "medium_close",
        "medium_close": "medium_close",
        "mediumclose": "medium_close",
        "waist": "waist_up",
        "waistup": "waist_up",
        "waist_up": "waist_up",
    }
    return aliases.get(raw, "waist_up")


def _build_lipsync_avatar_prompt(base_prompt: str, shot_type: str) -> str:
    shot = _normalize_lipsync_shot_type(shot_type)
    shot_phrase = {
        "close_up": "close-up framing",
        "medium_close": "medium close framing",
        "waist_up": "waist-up framing",
    }.get(shot, "waist-up framing")
    constraints = (
        f"character singing to camera, {shot_phrase}, clear visible mouth, natural facial motion, "
        "no extreme head turns, no hands blocking mouth, cinematic lighting, identity preserved"
    )
    if base_prompt.strip():
        return f"{base_prompt.strip()}, {constraints}"
    return constraints


def _apply_lipsync_performance_rules(*, scenes: list[dict], duration: float, vocal_phrases: list[dict], want_lipsync: bool) -> list[dict]:
    if not scenes:
        return []

    normalized_vocal_phrases: list[tuple[float, float]] = []
    for phr in vocal_phrases or []:
        try:
            start = float(phr.get("start"))
            end = float(phr.get("end"))
        except Exception:
            continue
        if not (math.isfinite(start) and math.isfinite(end)):
            continue
        if end <= start:
            continue
        normalized_vocal_phrases.append((max(0.0, start), min(float(duration), end)))

    window_size = 30.0
    per_window_target = 3
    selected_ids: set[str] = set()

    def score_scene(scene: dict, win_start: float, win_end: float) -> float:
        s0 = float(scene.get("start") or 0.0)
        s1 = float(scene.get("end") or 0.0)
        overlap = max(0.0, min(s1, win_end) - max(s0, win_start))
        if overlap <= 0.0:
            return -1.0
        dur = max(0.0, s1 - s0)
        duration_score = 1.0 - min(1.0, abs(dur - 4.0) / 2.0)
        vocal_bonus = 1.0 if bool(scene.get("hasVocals") or scene.get("lyricFragment")) else 0.0
        return overlap + duration_score + vocal_bonus

    if want_lipsync:
        t = 0.0
        while t < float(duration):
            win_start = t
            win_end = min(float(duration), t + window_size)
            candidates = sorted(
                scenes,
                key=lambda sc: score_scene(sc, win_start, win_end),
                reverse=True,
            )
            picked = 0
            for scene in candidates:
                if picked >= per_window_target:
                    break
                sid = str(scene.get("id") or "")
                if not sid or sid in selected_ids:
                    continue
                if score_scene(scene, win_start, win_end) <= 0:
                    continue
                selected_ids.add(sid)
                picked += 1
            t += window_size

    updated: list[dict] = []
    phrase_idx = 0
    for i, scene in enumerate(scenes):
        obj = dict(scene)
        sid = str(obj.get("id") or f"scene_{i+1:03d}")
        s0 = float(obj.get("start") or 0.0)
        s1 = float(obj.get("end") or 0.0)
        seg_dur = max(0.0, s1 - s0)
        is_lipsync_scene = bool(want_lipsync and sid in selected_ids)

        if is_lipsync_scene:
            shot_type = _normalize_lipsync_shot_type(obj.get("shotType"))
            obj["type"] = "performance"
            obj["sceneType"] = "performance"
            obj["lipSync"] = True
            obj["isLipSync"] = True
            obj["renderMode"] = "avatar_lipsync"
            obj["provider"] = "piapi"
            obj["model"] = "omni-human-1.5"
            obj["shotType"] = shot_type
            obj["mouthVisible"] = True
            obj["requestedDurationSec"] = round(max(3.0, min(5.0, seg_dur if seg_dur > 0 else 4.0)), 3)

            while phrase_idx < len(normalized_vocal_phrases) and normalized_vocal_phrases[phrase_idx][1] <= s0:
                phrase_idx += 1
            phrase = normalized_vocal_phrases[phrase_idx] if phrase_idx < len(normalized_vocal_phrases) else None
            if phrase:
                a0 = max(s0, phrase[0])
                a1 = min(s1, phrase[1])
                if a1 - a0 < 0.2:
                    a0 = s0
                    a1 = min(s1, s0 + min(5.0, max(3.0, seg_dur)))
            else:
                a0 = s0
                a1 = min(s1, s0 + min(5.0, max(3.0, seg_dur if seg_dur > 0 else 4.0)))
            obj["audioSliceStartSec"] = round(a0, 3)
            obj["audioSliceEndSec"] = round(max(a0 + 0.2, a1), 3)
            base_prompt = str(obj.get("prompt") or obj.get("videoPrompt") or "").strip()
            obj["prompt"] = base_prompt
            obj["videoPrompt"] = base_prompt
            obj["transitionType"] = "single"
        else:
            obj["type"] = obj.get("type") or "cinematic"
            obj["lipSync"] = False
            obj["isLipSync"] = False
            obj["renderMode"] = "standard_video"
            obj["provider"] = "kie"

        updated.append(obj)

    return updated


@router.post("/clip/plan")
def clip_plan(payload: BrainIn):
    """Gemini-first clip planner: Gemini analyzes audio/text/refs and returns strict JSON storyboard."""
    text = (payload.text or "").strip()
    mode = (getattr(payload, "mode", None) or payload.scenarioKey or "clip").strip().lower() or "clip"

    duration, audio_bytes, audio_mime, audio_debug = _load_audio_for_planner(payload.audioUrl)



    refs_obj = payload.refs
    character_refs = _normalize_ref_list((refs_obj.character if refs_obj else None))
    if not character_refs:
        character_refs = _normalize_ref_list(payload.characterRefs)
    if not character_refs:
        character_refs = _normalize_ref_list(payload.character_refs)

    location_refs = _normalize_ref_list((refs_obj.location if refs_obj else None))
    if not location_refs:
        location_refs = _normalize_ref_list(payload.locationRefs)

    props_refs = _normalize_ref_list((refs_obj.props if refs_obj else None))
    if not props_refs:
        props_refs = _normalize_ref_list(payload.propsRefs)

    style_refs = []
    if refs_obj and getattr(refs_obj, "style", None):
        style_value = refs_obj.style
        if isinstance(style_value, list):
            style_refs = _normalize_ref_list(style_value)
        else:
            u = str(getattr(style_value, "url", "") or "").strip()
            if u:
                style_refs = [u]
    if not style_refs and payload.styleRef:
        u = str(getattr(payload.styleRef, "url", "") or "").strip()
        if u:
            style_refs = [u]

    if payload.refCharacter:
        character_refs.append(str(payload.refCharacter).strip())
    if not location_refs and payload.refLocation:
        location_refs = [str(payload.refLocation).strip()]
    if not props_refs and payload.refItems:
        props_refs = [str(payload.refItems).strip()]
    if not style_refs and payload.refStyle:
        style_refs = [str(payload.refStyle).strip()]

    character_refs = list(dict.fromkeys([url for url in character_refs if url]))[:8]
    location_refs = list(dict.fromkeys([url for url in location_refs if url]))[:8]
    props_refs = list(dict.fromkeys([url for url in props_refs if url]))[:8]
    style_refs = list(dict.fromkeys([url for url in style_refs if url]))[:1]

    scenario_key = (payload.scenarioKey or "").strip()
    shoot_key = (payload.shootKey or "").strip()
    style_key = (payload.styleKey or "").strip()
    freeze_style = bool(payload.freezeStyle)
    want_lipsync = bool(payload.wantLipSync)

    session_world_anchors = _build_session_world_anchors(
        text=text,
        character_refs=character_refs,
        location_refs=location_refs,
        style_refs=style_refs,
        style_key=style_key,
    )

    planner_input_signature = _planner_input_signature(
        character_refs=character_refs,
        location_refs=location_refs,
        style_refs=style_refs,
        props_refs=props_refs,
        text=text,
        audio_url=payload.audioUrl or "",
        mode=mode,
        scenario_key=scenario_key,
        shoot_key=shoot_key,
        style_key=style_key,
        freeze_style=freeze_style,
        want_lipsync=want_lipsync,
    )

    input_state_debug = {
        "characterRefCount": len(character_refs),
        "locationRefCount": len(location_refs),
        "styleRefCount": len(style_refs),
        "propsRefCount": len(props_refs),
        "textPresent": bool(text),
        "audioPresent": bool(payload.audioUrl),
        "mode": mode,
        "signature": planner_input_signature,
    }

    character_images = []
    for ref_url in character_refs:
        inline_part = _load_reference_image_inline(ref_url)
        if inline_part:
            character_images.append(inline_part)

    location_images = []
    for ref_url in location_refs:
        inline_part = _load_reference_image_inline(ref_url)
        if inline_part:
            location_images.append(inline_part)

    props_images = []
    for ref_url in props_refs:
        inline_part = _load_reference_image_inline(ref_url)
        if inline_part:
            props_images.append(inline_part)

    print("CLIP DEBUG character_refs:", character_refs)
    print("CLIP DEBUG attached character images:", len(character_images))
    print("CLIP DEBUG location_refs:", location_refs)
    print("CLIP DEBUG attached location images:", len(location_images))

    refs_debug = {
        "characterRefCount": len(character_refs),
        "characterImagesAttached": len(character_images),
        "locationRefCount": len(location_refs),
        "locationImagesAttached": len(location_images),
        "styleRefCount": len(style_refs),
        "propsRefCount": len(props_refs),
        "propsImagesAttached": len(props_images),
    }

    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "code": "GEMINI_API_KEY_MISSING",
                "detail": "Gemini API key is missing for clip planning",
                "plannerDebug": {
                    "inputState": input_state_debug,
                    "refsDebug": refs_debug,
                },
            },
        )

    prop_anchor_label = _clean_anchor_label(
        getattr(refs_obj, "propAnchorLabel", None) or getattr(payload, "propAnchorLabel", None)
    )
    prop_anchor_source = "payload" if prop_anchor_label else "fallback"
    if props_images and not prop_anchor_label:
        anchor_model = (getattr(settings, "GEMINI_VISION_MODEL", None) or "gemini-1.5-flash").strip()
        prop_anchor_label = _infer_prop_anchor_label(props_images, api_key, anchor_model)
        prop_anchor_source = "inferred" if prop_anchor_label else "fallback"

    prop_anchor = _build_prop_anchor(prop_anchor_label)
    if prop_anchor:
        prop_anchor["source"] = prop_anchor_source
    refs_debug["propAnchor"] = prop_anchor
    refs_debug["propAnchorLabel"] = prop_anchor_label or None
    refs_debug["propAnchorSource"] = prop_anchor_source

    style_anchor = (
        "season, weather, color palette and cinematic visual language must be taken directly from style reference images"
        if style_refs
        else ((payload.styleKey or "").strip() or "neutral cinematic realism")
    )
    lighting_anchor = (
        "light direction, softness, exposure and color temperature must match the lighting implied by style reference images"
        if style_refs
        else "environment-driven cinematic lighting derived from the location and world state"
    )
    location_anchor = (
        "architecture style, street geometry, paving materials and environmental aging must match location reference images"
        if location_refs
        else "coherent single-location environment"
    )
    environment_anchor = "weather, atmosphere, surface materials and environmental mood must remain stable across scenes"
    weather_anchor = (
        "weather state must be taken directly from style reference images and remain unchanged across scenes"
        if style_refs
        else "coherent stable weather state across scenes"
    )
    surface_anchor = (
        "ground/surface state must be taken directly from style reference images, including snow traces, wetness, reflections, and material condition"
        if style_refs
        else "coherent stable ground and surface condition across scenes"
    )
    has_visual_inputs = bool(audio_bytes or character_images or location_images or props_images)
    if has_visual_inputs:
        model_used = getattr(settings, "GEMINI_VISION_MODEL", None) or "gemini-1.5-flash"
    else:
        model_used = getattr(settings, "GEMINI_TEXT_MODEL", None) or "gemini-2.5-flash"
    model_used = model_used.strip()

    system_rules = f"""You are a professional music video director and editor.
Build the clip storyboard directly from audio/text/refs with strict continuity and rhythm logic.
Return ONLY valid JSON object, no markdown, no explanation, no code fences.

Hard rules:
- Analyze audio yourself: BPM, sections, vocal phrases, energy events.
- Cover full track from 0 to track.durationSec with no gaps and no overlap between scenes.
- Scene durations should be logical: 1-2 sec only for fast inserts, 2-4 sec common, 4-6 sec atmospheric.
- Scene boundaries should align with beat accents, section transitions, and vocal phrase boundaries.
- Do not invent random disconnected scenes.
- Maintain continuity: same character identity, same world/location logic, same style language.
- If refs are provided, refs are source of truth and have priority over free imagination.
- lipSyncText must be non-empty only when there is real vocal phrase in that scene.
- If no audio is available, still build coherent storyboard from text+refs.
- If no text/refs, still build coherent storyboard from audio only.

TEXT NODE PRIORITY RULE:

If TEXT is provided, it is the primary narrative source for the clip.

TEXT must define:

- what happens in each scene
- event progression across scenes
- scene-specific actions and interactions
- character intention
- emotional beats

AUDIO PRIORITY RULE:

AUDIO defines:

- timing
- rhythm
- intensity
- segmentation
- vocal emphasis

AUDIO must not overwrite or replace TEXT narrative content.
If both TEXT and AUDIO are present:

- TEXT defines story content
- AUDIO defines pacing and cut structure

NO GENERIC PLANNER REWRITE:

Do not rewrite TEXT narrative into generic cinematic fallback.
Do not collapse specific actions/events into neutral atmospheric portraits.
If TEXT specifies concrete actions, gestures, interactions, events, or dramatic progression,
preserve them explicitly in the scene list.

TEXT WORLD OVERRIDE RULE:

If location/style refs are absent, but TEXT explicitly defines a concrete world, venue, or environment,
derive the session world from TEXT instead of generic planner fallback world initialization.

TEXT STYLE OVERRIDE RULE:

If style refs are absent, but TEXT explicitly defines lighting, performance mood, venue atmosphere,
or visual styling cues, derive the style anchor from TEXT instead of generic fallback style.

GENERIC FALLBACK LIMIT:

Generic planner fallback world/style may only be used when user input does not define
a concrete world or concrete style cues.

MODE INTERPRETATION:

The storyboard engine supports two narrative modes:

CLIP MODE
ADVERTISEMENT MODE

Each mode has different narrative priorities.

CLIP MODE NARRATIVE RULE:

When mode = CLIP:

The storyboard must be driven primarily by the song lyrics and musical emotion.

Lyrics define:

- narrative meaning
- character motivation
- emotional transitions
- symbolic story moments

Music defines:

- rhythm
- pacing
- scene duration
- cut timing

Scenes must visualize the emotional meaning of the lyrics.

Each scene should interpret a phrase or emotional moment from the song.

Reference images only define:

- character appearance
- environment
- visual style

They must NOT override the lyrical narrative.

PROP ROLE IN CLIP MODE:

Props must not become the central narrative subject unless the lyrics explicitly reference the object.

Props are environmental elements used by the character.

Avoid product-style shots such as:

- isolated prop hero shots
- repeated product close-ups
- scenes where the prop dominates the composition

The focus must remain on the character and the story.

LYRIC INTERPRETATION RULE:

If lyrics text is available,
each scene should interpret a phrase or emotional fragment from the lyrics.

Scenes should represent:

- emotional meaning
- metaphor
- character decisions
- narrative progression

The storyboard must feel like a visual interpretation of the song.

ADVERTISEMENT MODE RULE:

When mode = ADVERTISEMENT:

The product becomes the central subject of the visual narrative.

Scenes must highlight:

- product visibility
- product interaction
- product functionality
- product hero moments

The product may appear prominently in multiple shots.

ADVERTISEMENT AUDIO MODE:

If advertisement audio narration is provided,
scene structure should follow the spoken marketing script.

Each scene should illustrate the feature or benefit described in the narration.

MODE PRIORITY SWITCH:

When mode = CLIP:

lyrics and emotional narrative override props and product focus.

When mode = ADVERTISEMENT:

product focus overrides lyrical or narrative interpretation.

MASTER WORLD CONTEXT (session-level):
- Character: from character refs if present
- Location: from location refs if present
- Style: from style refs if present
- Prop anchor: {prop_anchor_label or "none"}
All scenes must respect this world context.

SESSION WORLD ANCHORS:

Character anchor: {session_world_anchors["character"]}

Location anchor: {session_world_anchors["location"]}

Style anchor: {session_world_anchors["style"]}

Lighting anchor: {lighting_anchor}

Environment anchor: {environment_anchor}

Weather anchor: {weather_anchor}

Surface anchor: {surface_anchor}

All scenes must inherit these anchors.

Do not change these anchors between scenes.

STYLE-DEFINED ENVIRONMENT STATE:

If style reference images are present, they define:

- season
- weather state
- lighting mood
- color palette
- surface condition
- atmospheric mood

These style-defined environmental states must remain stable across all scenes.

Do not reinterpret or weaken them in later scenes.

If the style references imply winter snow, snow traces and cold winter atmosphere,
do not switch later scenes to generic wet cloudy weather without snow.

WEATHER STATE LOCK:

Weather must remain the same across the whole session unless explicitly changed by text.

If the style reference implies:

- snow
- winter cold
- overcast winter weather

then all scenes must preserve that same weather state.

Do NOT switch between:

- snow
- rain
- dry cloudy weather
- neutral weather

unless explicitly requested.

Weather continuity includes:

- presence/absence of snow
- snow traces on roofs and ground
- wetness level
- atmospheric coldness

SURFACE STATE LOCK:

Ground and surface conditions must remain visually consistent across scenes.

Maintain:

- same pavement material
- same wetness level
- same snow traces
- same reflection behavior
- same environmental wear

If the first scene shows wet cobblestone with snow traces,
later scenes must preserve that same surface logic.

GLOBAL ENVIRONMENT STATE:

Style reference images define the global environment state for the entire session.

This includes:

- season
- weather
- lighting mood
- color palette
- atmospheric conditions
- ground surface state

These properties must remain constant across all scenes.

Camera framing or shot type (wide, medium, close-up, macro) must NOT weaken these environmental constraints.

VISIBLE WEATHER LOCK:

If snow is part of the style-defined environment state,
snow must remain visible in every frame.

Snow accumulation or snow traces must remain visible on at least some of:

- ground edges
- rooftops
- pavement gaps
- horizontal surfaces
- street borders
- environmental surfaces

Do not reduce snowy winter state into generic wet cold weather.

Visible weather cues must remain present even in close-up and macro shots.

SUBJECT RELIGHTING RULE:

Character lighting must be derived entirely from the environment.

Do not preserve lighting baked into character reference images.

The generated subject must match the environment in:

- light direction
- color temperature
- ambient bounce light
- shadow softness
- exposure
- atmospheric haze

The character must not look studio-lit inside an outdoor cinematic environment.

PHYSICAL SUBJECT INTEGRATION:

The character must appear physically present inside the same world as the background.

Match:

- ambient depth
- edge contrast
- environmental color bounce
- surface reflections
- ground contact shadows
- local atmospheric perspective

Do not render the character as:
- pasted
- composited
- cut out
- separately lit
- cleaner than the environment

The subject must feel photographed in the same place and lighting conditions as the environment.

SUBJECT AND PROP ENVIRONMENT MATCH:

Character and prop must inherit the same environmental qualities as the scene.

Match:

- ambient haze
- dust
- smoke diffusion
- reflected dirty light
- floor color bounce
- local contrast softness
- environmental color contamination

Do not render the character or prop as cleaner, sharper, or separately lit than the environment.

Character and prop must feel physically present in the same air, same light, and same atmosphere as the scene.

NO CUTOUT / NO COMPOSITE LOOK:

Do not render the character or prop as:

- pasted
- composited
- cut out
- sticker-like
- separately exposed
- separately color-graded

They must feel captured inside the same environment,
with the same atmospheric depth and lighting logic.

Edges, contrast, color temperature, and softness must match the environment.

CHARACTER INTEGRATION LOCK:

The character must inherit:

- local ambient light
- environmental shadow softness
- atmospheric haze
- reflected floor color
- industrial / urban environmental contamination when applicable

Do not keep the character unnaturally clean or studio-like if the world is dusty, hazy, smoky, wet, dirty, snowy, or industrial.

The subject must feel photographed in the same environment,
not inserted afterward.

ATMOSPHERIC DEPTH RULE:

All visible elements must be affected by the same atmosphere.

Apply consistent haze, moisture, light scattering and atmospheric depth to:

- background
- character
- props

Do not keep the subject artificially crisp if the environment is soft, hazy, cold, wet, snowy, or diffuse-lit.

WORLD SCALE CONTEXT LOCK:
The storyboard must use one fixed worldScaleContext for the entire session (human_world, hero_vs_giant, micro_world, animal_scale, space_scale, or mythic_world).
Detect it from text/refs and keep it stable across scenes.

ENTITY SCALE ANCHOR LOCK:
Define fixed relative scale anchors (entityScaleAnchors) and preserve them across every scene.
Do not randomly rescale entities between shots.
Even in close-ups, preserve perceived scale via framing, perspective, crop, and depth layering.

THREAT DOMINANCE RULE:
When a threat entity exists (monster, predator, boss), it must visually dominate frame presence via scale, occupancy, or spatial pressure even when partially visible.

KEYFRAME STORYBOARD ENGINE:
The storyboard must classify each scene into one of three types:
- continuous
- single
- hard_cut

continuous:
Use when the same local event develops over time and should be shown as a visual transition.
For continuous scenes, return:
- startFramePrompt
- endFramePrompt
- transitionActionPrompt

single:
Use when the scene is one important visual beat or one static cinematic moment.
For single scenes, return:
- framePrompt

hard_cut:
Use when the next scene starts a new location, time block, or narrative chapter.
For hard_cut scenes, return:
- framePrompt

SCENE TYPE SELECTION RULE:
If the event evolves naturally in the same local world situation, use continuous.
If the scene is only one strong visual beat, use single.
If the narrative jumps to another block, use hard_cut.

CONTINUOUS CHAIN RULE:
For continuous scenes, the end frame should feel like the natural visual destination of the start frame.
The transitionActionPrompt must describe what visually happens between start and end.
Use physical progression, not abstract poetic wording.

EXAMPLES:
- hero walking across dune -> sand begins trembling
- sand swelling -> monster emerging
- fighter raising sword -> clash impact
- singer stepping toward microphone -> close emotional performance moment

SINGLE SCENE RULE:
Single scenes should describe one complete cinematic moment.
No start/end pair is needed.

HARD CUT RULE:
Hard cut scenes begin a new block.
Do not force visual interpolation from the previous scene when a hard cut is more natural.

VISUAL CAUSALITY RULE:
For continuous scenes, there must be visible cause-and-effect progression between start and end.
Do not skip directly from setup to payoff if an intermediate event is visually implied.

KEYFRAME CLARITY RULE:
All frame prompts must describe what the image should look like at that exact moment.
Do not describe a full animation inside a single frame prompt.

TRANSITION ACTION RULE:
transitionActionPrompt must describe motion/process/change between the two keyframes.
This prompt will later be used for image-to-video generation.

CAMERA CONTINUITY ENGINE:
The storyboard must follow cinematic camera progression rules.
Scenes must not repeat identical framing or camera logic.
Use natural cinematic shot variation.

SHOT SCALE PROGRESSION:
Shots should evolve across the scene sequence.

Example progression patterns:
- wide → medium → close → impact
- wide → tracking → close → reaction
- establishing → character → action → aftermath

Avoid repeating the same shot scale in consecutive scenes.

SHOT TYPES:
The planner may use the following cinematic shot types:
- establishing shot
- wide shot
- medium shot
- close-up
- extreme close-up
- over-shoulder shot
- tracking shot
- reaction shot
- impact shot

Scenes should naturally mix these shot types.

CAMERA MOVEMENT:
Camera movement should evolve with energy and rhythm.

Allowed movements:
- static frame
- slow push-in
- tracking shot
- orbit movement
- handheld motion
- dramatic push-in

High energy scenes may use faster or more dynamic camera movement.
Calm scenes should prefer stable or slow camera movement.

VISUAL VARIATION RULE:
Two consecutive scenes must not look visually identical.

Even if narrative content is similar, vary:
- camera angle
- shot scale
- camera movement
- subject framing

This prevents storyboard frames from appearing duplicated.

ACTION FOCUS RULE:
If the scene contains action (combat, chase, movement):
Use tighter framing and dynamic camera movement.

If the scene is emotional or reflective:
Use slower camera movement and closer framing.

WIDE SHOT SCALE RULE:
When large entities exist (monsters, ships, giant environments):
Occasionally include wide shots that reveal true scale relationships.

This reinforces world scale context.

REACTION SHOT RULE:
Important emotional moments may include reaction shots.

Examples:
- character reaction
- enemy reaction
- environment reaction

Reaction shots improve cinematic pacing.

SCENE DIVERSITY RULE:
A storyboard sequence should include varied shot scales and camera styles.

Avoid sequences like:
- close → close → close
- medium → medium → medium

Instead aim for cinematic variation.

PROP SIZE CLASS LOCK:

The prop must belong to a stable real-world size class across all scenes.

The object must not change its physical class between frames.

Example:
A portable welding machine must remain portable-welder sized in every frame.

It must not become:
- oversized
- generator-sized
- miniaturized
- enlarged to fit composition
- distorted in apparent volume

The prop must remain physically plausible relative to the human body.

HARD PROP SIZE CLASS LOCK:

The prop belongs to a fixed real-world size class.

If the prop is a portable welding machine,
its physical class is compact carryable equipment,
approximately small-suitcase class.

It must never become:

- oversized
- generator-sized
- floor-machine sized
- enlarged to dominate the frame
- visually inflated to reveal more detail

The prop must keep the same physical size class across all scenes.

BODY-RELATIVE SCALE REFERENCE:

The prop must remain consistent relative to the human body.

Use stable body-relative references such as:

- hand grip
- shin height
- knee level
- lower leg size
- forearm carry scale

Do not change prop size class between:

- wide shots
- medium shots
- close-ups
- macro shots

Framing must not justify scaling the object larger or smaller.

ANATOMIC ANCHORING:

Object scale must be anchored relative to the human body.

Use stable body-relative proportions such as:

- knee height
- lower leg height
- hand-carryable size
- forearm / torso relation

The prop must keep the same body-relative scale across all shots.

Do not resize the object just because the framing changes.

MACRO CONTEXT LOCK:

In close-up or macro shots, the environment state must remain visible through the surface context.

If the wide-shot environment is snowy wet cobblestone street,
then close-up shots must preserve that same surface logic.

Macro shots must not forget:
- snow traces
- wetness
- pavement material
- winter environment cues

Close framing must not weaken global world continuity.

SCENE-TO-SCENE CONTINUITY RULE:
Each new scene must be treated as the next moment of the same story world, not as an independent image.

PERSISTENT WORLD RULE:
Preserve across scenes unless storyboard explicitly changes them:
- location identity
- lighting logic
- color palette / grade mood
- environment materials
- weather / time of day
- character wardrobe and identity
- prop identity and scale class
- historical/cultural setting
- event identity

SCENE DELTA RULE:
Only the current scene action, framing, emotional beat, and local event progression should change.

NO COMPOSITION CLONE RULE:
Do not copy the previous frame composition exactly.
Do not freeze pose or framing.
Keep continuity, but generate a new valid shot of the next moment.

CINEMATIC SCENE PROGRESSION RULES:
Scenes must behave like a cinematic storyboard.
Each scene must represent a new visual moment in time.
Consecutive scenes must not repeat the same composition, camera position, or character pose.
Every new scene must introduce at least one visible change.

Allowed visible changes:
- camera angle change
- camera distance change
- camera position change
- character pose / movement / orientation change
- framing change
- interaction with environment

If a character is moving, scenes must show different stages of movement:
- starting movement
- continuing movement
- approaching destination
- stopping
- turning
- reacting

Avoid repeating the same shot type in consecutive scenes.

Use natural cinematic progression like:
- wide → medium → close
- back → side → front
- movement → pause → reaction
- environment → subject → detail

SHOT CLARITY RULE:
Each scene must focus on one clear visual moment.
Do not overload one scene with too many narrative beats.
Discovery, reaction, important object, and realization moments should usually be split into separate shots.

SPATIAL PROGRESSION RULE:
Scenes must show progression through space and time.
If the character moves through a location, the environment perspective must evolve accordingly.
Show movement progression clearly: moving through environment, approaching target, stopping near target, then reacting.

SPATIAL ORIENTATION RULE:
When a character moves toward a distant object, location, or target
(such as a fire, light, building, person, or landmark),
the spatial orientation of the scene must reflect that direction.

Prefer compositions where:
- the character faces the direction of travel
- the target object is ahead of the character
- the viewer can visually understand the direction of movement

Avoid compositions where the character walks toward the camera
while the destination remains behind them,
unless the storyboard explicitly requires that composition.

TARGET CONSISTENCY RULE:
If a scene includes a distant target or object of interest,
such as a fire, light, building, vehicle, or person,
the target should remain spatially consistent across scenes.

As the character approaches:
- the target gradually appears closer
- the character moves toward the target
- the spatial relationship remains consistent

Do not randomly reposition the target relative to the character.

APPROACH SHOT RULE:
When a character approaches a distant target,
use cinematic compositions that clearly communicate movement direction.

Preferred approaches include:
- back view (character walking away from camera)
- over-the-shoulder view
- side tracking shot

These compositions visually reinforce the direction of travel.

STATIC FRAME PREVENTION:
If two scenes are narratively similar, their camera composition must still be visibly different.
Never produce consecutive scenes that look like identical frames with only textual differences.
Every scene must contain an observable visual change.

CHARACTER POSE VARIATION RULE:
Reference images define character identity only.
They must preserve:
- face identity
- body proportions
- hairstyle
- clothing / logos
- accessories

Reference images must NOT lock the character pose.
Character pose should change naturally between scenes
according to action, movement, and cinematic progression.

Allow natural variation in:
- body orientation
- step position
- arm movement
- hand position
- head direction
- weight distribution
- stance
- posture

Maintain identity consistency but avoid repeating the exact reference pose.

POSE PROGRESSION RULE:
When a character is walking, running, turning, searching, reacting,
or interacting with the environment,
each scene should show a different stage of movement.

Examples:
walking progression:
- left step
- right step
- slowing down
- stopping
- shifting weight

reaction progression:
- noticing
- head turn
- focusing attention
- emotional response

POSE REPETITION PREVENTION:
Avoid repeating the same body pose, stance, or gesture
across consecutive scenes.
Adjacent scenes must not show the exact same pose
unless storyboard intent explicitly requires stillness.

REFERENCE POSE RELEASE RULE:
The visible pose in reference images must not dominate planner-generated storyboard scenes.
References must be used strictly for identity guidance.
The character should behave like a live actor performing the current story beat.

CINEMATIC BODY LANGUAGE RULE:
Character body language should reflect the current story beat.

Examples:
- movement -> active posture
- suspicion -> tense posture
- fear -> defensive posture
- curiosity -> leaning forward
- reaction -> sudden shift in stance or motion

Body language must evolve naturally from scene to scene.

SAME PRODUCTION RULE:
All scenes should feel as if they were shot by the same production:
- same camera package and lens language
- same lighting setup logic
- same set/world

SESSION WORLD CONSISTENCY RULES:

All scenes must look like they belong to the SAME continuous world.

Do NOT treat scenes as independent illustrations.

Maintain continuity of:

- lighting conditions
- weather
- architectural language
- street geometry
- color palette
- environmental mood

Scenes must feel like different camera shots from the same film scene,
not different locations or times.

Lighting, atmosphere and architecture must remain consistent.

IMPORTANT:
Use the FIRST generated scene as the baseline world state.
All following scenes must inherit the same visual environment.

LIGHTING CONTINUITY:

All scenes must share the same lighting logic.

The first scene defines the lighting conditions.

If the first scene implies:

- overcast sky
- diffuse winter light
- cold color temperature

then all following scenes must maintain the SAME lighting conditions.

Do NOT switch to:

- sunny light
- warm sunset light
- studio lighting
- dramatic spotlight lighting

unless explicitly specified in refs or text.

Lighting must remain consistent across the whole storyboard.

Maintain:

- same shadow softness
- same exposure level
- same color temperature
- same light direction

LOCATION CONTINUITY LOCK:

If a location reference exists,
all scenes must appear to take place in the SAME environment.

Do not generate completely different streets,
cities or architectural styles.

Maintain continuity of:

- building materials
- architectural era
- street width
- pavement type
- environmental aging

Scenes must feel like different camera positions
within the same district or street world.

Camera angle may change,
but the environment must remain recognizably the same.

STRICT OBJECT LOCK:
- If props refs exist, they define one anchored prop identity for the whole session.
- Treat multiple props photos as different angles/details of the same object.
- Never reinterpret, replace, rename, generalize, or downgrade anchored prop identity.
- If scene wording conflicts with prop anchor identity, prop anchor identity wins.

PROP INTEGRATION LOCK:

The prop must be physically integrated into the scene.

Ensure that the prop:

- matches scene lighting
- matches scene exposure
- matches color temperature
- matches perspective
- matches scale relative to the character

The prop must NOT appear:

- pasted
- floating
- overly clean compared to the environment
- composited from another image

The prop must interact naturally with the character:

- realistic hand grip
- correct weight orientation
- correct physical contact

The prop must visually belong to the environment.

PROP INTEGRATION HARD LOCK:

The prop must be integrated into the environment with the same:

- ambient light
- shadow softness
- color temperature
- reflected floor color
- atmospheric softness
- dirt / haze / smoke context

Do not render the prop as a clean product render inside a dirty scene.

The prop must visually belong to the same world as the floor, air, and surrounding light.

ENVIRONMENTAL CONTAMINATION LOCK:

If the environment contains:

- dust
- smoke
- industrial haze
- wet reflections
- cold fog
- snow residue
- dirty floor bounce

then character and prop must inherit that environmental contamination visually.

They must not look isolated from the environmental conditions.

SOURCE PRIORITY RULES

Use the following source priority:

1. character reference images define exact person identity
2. location reference images define exact world/location identity
3. style reference images define season, weather, palette, atmosphere, and visual language
4. props reference images define exact object identity
5. scene text defines action, emotion, placement, interaction, and narrative meaning
6. audio defines timing, rhythm, energy, lipsync structure, and scene intensity
7. shoot mode defines camera language
8. styleKey is only a fallback when no style reference images are present
9. free imagination is allowed only when no higher-priority source defines that element

Higher-priority sources must never be overridden by lower-priority ones.

PER-SOURCE INTERPRETATION LOCKS

CHARACTER refs:
- text may change pose/action/emotion
- text must not change who the person is

LOCATION refs:
- text may change position within the same place
- text must not change the place itself

STYLE refs:
- text may change dramatic emphasis
- text must not replace season/weather/palette defined by style refs

PROPS refs:
- text may describe prop use/placement
- text must not rename or replace the object

PROP SCALE LOCK:

The prop must preserve the same real-world physical size class across all scenes.

The object must remain physically plausible relative to the human body.

Do not:

- enlarge it
- shrink it
- exaggerate it
- miniaturize it
- distort its real-world scale between shots

The prop must keep stable human-relative scale in every frame.

Example:
If the prop is a portable welding machine,
it must remain portable-welder sized in every scene,
not suitcase-sized in one scene and generator-sized in another.

PROP PHYSICAL CONSISTENCY:

Keep consistent:

- size relative to hands
- size relative to torso/legs
- grip logic
- weight impression
- handle/cable behavior
- ground contact behavior

The prop must not look weightless, oversized, undersized, or physically inconsistent between scenes.

If the prop is handheld,
its scale must remain realistically liftable by the character.

AUDIO:
- may control scene timing, pacing, emotion intensity, and lipsync
- must not redefine character/location/prop identity

SHOOT MODE:
- may control camera framing and movement language
- must not redefine world identity or character identity

STYLE KEY:
- use only if style refs are absent
- if style refs exist, style refs win

REFERENCE PRIORITY RULES

If character reference images are attached:
- Describe the SAME person from the reference images.
- Do not invent another man/woman.
- Do not change gender.
- Do not replace the outfit unless the story explicitly requests a wardrobe change.
- Do not invent a different hairstyle, age, or body type.
- All scenes must refer to the same exact person from the reference images.

If location reference images are attached:
- Describe the SAME environment from the reference images.
- Do not replace the setting with another room, street, or world.
- Architecture, mood, and setting must come from the reference images.

If reference images are attached, they override free imagination.

CHARACTER CONFLICT RESOLUTION

If scene text conflicts with character reference identity:
- Character refs always win.
- Conflicting text about gender, facial identity, age, hairstyle, clothing identity, or visible accessories must be ignored.
- Do not mix contradictory identity signals.
- Do not partially preserve incorrect text claims when they contradict character refs.
- Example: if text says "girl" but the character reference clearly shows a man, describe the man from reference and ignore the incorrect text identity label.

REFERENCE DETAIL ACCURACY

- Describe only details that are clearly supported by reference images.
- Do not invent accessories, wearable items, or carried objects that are not clearly visible.
- If a detail is ambiguous, do not state it as fact.
- Prefer omission over hallucination.

CLOTHING DETAIL INTERPRETATION RULES

- Hoodie drawstrings, garment cords, seams, folds, logo edges, shadows, and fabric details must not be misidentified as headphones, necklaces, wires, or accessories.
- Clothing details must remain clothing details unless clearly identifiable as separate objects.
- Logos must remain logos and must not be turned into separate accessories.

NO INVENTED ACCESSORIES RULE

Do not add headphones, glasses, jewelry, bags, backpacks, hats, watches, necklaces, or other accessories unless:
- They are clearly visible in reference images, or
- They are explicitly defined by a higher-priority reference node.

Scene text alone must not invent small visual accessories when character refs contradict or do not support them.

CONTINUITY MEMORY REQUIREMENT:
For each scene, fill continuityMemory with short structured persistent state summary.
continuityMemory captures persistent world setup for the next scene (location, lighting, color palette, camera language, character state, world state, prop state, production scale, audience state).
This is continuity reference, NOT composition lock.
Do not force exact pose/framing repetition.
WORLD SCALE CONTEXT REQUIREMENT:
Detect and return one stable session-level worldScaleContext from: human_world, hero_vs_giant, micro_world, animal_scale, space_scale, mythic_world.
Define entityScaleAnchors as stable relative size anchors (for example hero:1, monster:6).
Do not randomly rescale anchored entities across scenes.
In close-ups, scale must still be implied through framing, perspective, crop logic, and foreground/background separation.
If a threat entity exists (monster/predator/boss), enforce threat visual dominance via scale, spatial occupation, or presence even when partially visible.


Response schema (all keys required):
{{
  "track": {{"durationSec": number, "bpm": number, "timeSignature": string, "energyProfile": string}},
  "sections": [{{"start": number, "end": number, "type": string, "energy": string}}],
  "vocalPhrases": [{{"start": number, "end": number, "text": string}}],
  "energyEvents": [{{"time": number, "type": string, "description": string}}],
  "worldScaleContext": string,
  "entityScaleAnchors": {{"entity_name": number}},
  "scenes": [{{
    "id": "scene_001",
    "start": number,
    "end": number,
    "transitionType": "continuous | single | hard_cut",
    "sceneType": string,
    "shotPurpose": string,
    "visualDescription": string,
    "startFramePrompt": string,
    "endFramePrompt": string,
    "framePrompt": string,
    "transitionActionPrompt": string,
    "visualPrompt": string,
    "lipSyncText": string,
    "camera": string,
    "motion": string,
    "reason": string,
    "continuityMemory": {{
      "location": string,
      "lighting": string,
      "colorPalette": string,
      "cameraLanguage": string,
      "characterState": string,
      "worldState": string,
      "propState": string,
      "worldScaleContext": string,
      "entityScaleAnchors": string
    }}
  }}]
}}

CHARACTER IDENTITY LOCK

If character reference images are provided:
- All images represent the SAME person
- This character must appear in every scene
- Do not redesign or replace the character
- Maintain identical facial identity
- Maintain same age, gender, hair, body type
- Treat these images as the source of truth

All scenes must describe the SAME character.

REFERENCE UNDERSTANDING RULES

Character reference images:
- All images depict the SAME person
- Use this character in every scene
- Do not change gender
- Do not change facial identity
- Clothing from reference images should remain consistent unless the story explicitly changes it
- Do not invent new hairstyles or body types
- Avoid generic invented phrases when references are specific

Location reference images:
- These images define the environment of the clip
- Scenes should take place in this world
- Architecture and atmosphere should match these references
- Avoid generic environment wording that ignores the reference details

STYLE REFERENCE RULES
- If style reference images are attached, they define season, atmosphere, palette, texture, weather, environment mood, and overall visual styling.
- Do not ignore style references.
- If style references indicate winter / snow / cold season / icy environment, scenes must reflect that visually.
- Do not default to neutral weather or generic city mood when style references specify a distinct season or atmosphere.

PROPS REFERENCE RULES
- If props reference images are attached, they define key objects of the scene.
- If there is only one props reference, treat it as a primary prop.
- Do not omit the prop when the scene can logically include it.
- Scene descriptions and visual prompts must explicitly mention the prop whenever relevant.
- Avoid treating props as optional decoration when they are clearly intended as key scene objects.

PROP PRIORITY RULES

If props reference images are attached:
- Props refs define exact object identity.
- Scene text may describe prop action, role, placement, or interaction.
- Scene text must not replace or rename the object into a different item.
- Object identity comes from refs, not from text.
- Example enforcement: if the prop ref is a welding machine, it must remain a welding machine and must not become a backpack, bag, suitcase, toolbox, speaker, generator, or generic equipment case.

If props refs are absent:
- Props may be inferred from scene text.

When references are present:
- Scene descriptions must explicitly describe the same man/woman from the reference images.
- Scene descriptions must explicitly describe the same environment from the reference images.
- Do not output generic placeholders like "young woman in a room" when references indicate a different person/place.
- When style refs exist, visualDescription and visualPrompt must explicitly reflect the style-defining season, atmosphere, weather, palette, and texture.
- When props refs exist, visualDescription and visualPrompt must explicitly mention and integrate the key prop in relevant scenes.
- When props refs exist, visualDescription and visualPrompt must preserve exact prop identity from refs and must never replace or rename the prop based on scene text.
- visualDescription and visualPrompt must not include invented small accessories or unsupported wardrobe details.
- If an accessory is uncertain, omit it and do not guess.

If reference images exist they override imagination.

IMPORTANT LANGUAGE ENFORCEMENT

All human-readable descriptive output must be written in Russian.
Return all user-facing scene descriptions strictly in Russian.
Do not output English for storyboard scene descriptions.
Prompts for generation may remain technical if needed, but UI scene descriptions must be Russian.

The following fields must ALWAYS be in Russian:
- visualDescription
- reason
- camera
- motion
- lipSyncText
- sections.type
- sections.energy
- vocalPhrases.text
- energyEvents.description

Only visualPrompt may remain in English because it is intended for image generation.

If any of the required descriptive fields are returned in English, the output is invalid.
"""

    user_input = {
        "mode": mode,
        "shootMode": payload.shootKey or payload.mode or "",
        "styleKey": payload.styleKey or "",
        "audioUrl": payload.audioUrl or "",
        "audioDurationHintSec": duration,
        "text": text,
        "refs": {
            "character": character_refs,
            "location": location_refs,
            "style": style_refs,
            "props": props_refs,
        },
        "propAnchor": prop_anchor,
    }

    parts = [{"text": system_rules}]

    if character_images:
        parts.append({"text": "Character reference images. All images depict the SAME main character."})
        parts.extend(character_images)

    if location_images:
        parts.append({"text": "Location reference images. These images define the world and environment of the clip."})
        parts.extend(location_images)

    if props_images:
        parts.append({"text": "Props reference images. All images depict the SAME single object identity from different angles/details."})
        parts.extend(props_images)
        parts.append({"text": f"Session prop anchor label: {prop_anchor_label}"})

    parts.append({"text": "Input payload:\n" + json.dumps(user_input, ensure_ascii=False)})

    if audio_bytes:
        parts.append({
            "inlineData": {
                "mimeType": audio_mime,
                "data": base64.b64encode(audio_bytes).decode("ascii")
            }
        })

    generation_config = {
        "temperature": 0.2,
        "responseMimeType": "application/json",
    }

    def _call_gemini(request_parts, model_name: str):
        body = {
            "contents": [{"role": "user", "parts": request_parts}],
            "generationConfig": generation_config,
        }
        resp = post_generate_content(api_key, model_name, body, timeout=120)
        raw = _extract_gemini_text(resp if isinstance(resp, dict) else {})
        parsed = _parse_json_from_text(raw)
        return resp, raw, parsed

    def _resolve_timeline_duration(plan: dict) -> float:
        track = plan.get("track") or {}
        try:
            gemini_track_duration = float(track.get("durationSec"))
            if not math.isfinite(gemini_track_duration) or gemini_track_duration <= 0:
                gemini_track_duration = None
        except Exception:
            gemini_track_duration = None

        duration_source = str(audio_debug.get("durationSource") or "")
        has_real_audio_duration = duration_source in {"local_ffprobe", "http_ffprobe", "ffprobe_without_audio_bytes"}
        if has_real_audio_duration and duration > 0:
            return float(duration)
        if gemini_track_duration is not None:
            return float(gemini_track_duration)
        if duration > 0:
            return float(duration)
        return 30.0

    def _validate_plan(plan: dict) -> tuple[bool, str | None]:
        if not isinstance(plan, dict):
            return False, "response_not_json_object"
        track = plan.get("track")
        scenes = plan.get("scenes")
        if not isinstance(track, dict):
            return False, "track_missing"
        if not isinstance(scenes, list) or not scenes:
            return False, "scenes_missing_or_empty"
        for idx, scene in enumerate(scenes):
            if not isinstance(scene, dict):
                return False, f"scene_{idx}_not_object"
            try:
                start = float(scene.get("start"))
                end = float(scene.get("end"))
            except Exception:
                return False, f"scene_{idx}_invalid_time"
            if not (start < end):
                return False, f"scene_{idx}_start_not_less_than_end"
            visual_prompt = str(scene.get("visualPrompt") or "").strip()
            visual_desc = str(scene.get("visualDescription") or "").strip()
            frame_prompt = str(scene.get("framePrompt") or "").strip()
            start_frame_prompt = str(scene.get("startFramePrompt") or "").strip()
            end_frame_prompt = str(scene.get("endFramePrompt") or "").strip()
            if not (visual_prompt or visual_desc or frame_prompt or start_frame_prompt or end_frame_prompt):
                return False, f"scene_{idx}_visual_empty"
        return True, None

    retry_used = False
    validation_warnings: list[str] = []
    validation_rejected_reason: str | None = None

    resp, raw_text, parsed = _call_gemini(parts, model_used)
    err_text = _combined_error_text(resp if isinstance(resp, dict) else {})
    if _is_model_unsupported_error(err_text):
        model_used = _pick_fallback_model(model_used)
        resp, raw_text, parsed = _call_gemini(parts, model_used)

    is_valid, reason = _validate_plan(parsed)
    if is_valid:
        timeline_duration = _resolve_timeline_duration(parsed)
        timeline_ok, timeline_reason, timeline_warnings = _validate_storyboard_timeline(timeline_duration, parsed.get("scenes") or [])
        validation_warnings.extend(timeline_warnings)
        if not timeline_ok:
            is_valid = False
            reason = timeline_reason

    if not is_valid:
        retry_used = True
        validation_warnings = []
        retry_parts = parts + [{"text": f"Previous output invalid ({reason}). Return ONLY one valid JSON object matching required schema."}]
        resp, raw_text, parsed = _call_gemini(retry_parts, model_used)
        is_valid, reason = _validate_plan(parsed)
        if is_valid:
            timeline_duration = _resolve_timeline_duration(parsed)
            timeline_ok, timeline_reason, timeline_warnings = _validate_storyboard_timeline(timeline_duration, parsed.get("scenes") or [])
            validation_warnings.extend(timeline_warnings)
            if not timeline_ok:
                is_valid = False
                reason = timeline_reason

    validation_rejected_reason = reason if not is_valid else None

    if not is_valid:
        err = _combined_error_text(resp if isinstance(resp, dict) else {}) or raw_text or reason or "invalid_gemini_json"
        return JSONResponse(
            status_code=502,
            content={
                "ok": False,
                "code": "CLIP_PLAN_VALIDATION_FAILED",
                "detail": str(err)[:1200],
                "modelUsed": model_used,
                "hint": reason,
                "plannerDebug": {
                    "audio": audio_debug,
                    "inputState": input_state_debug,
                    "model": {
                        "modelUsed": model_used,
                        "hasVisualInputs": has_visual_inputs,
                        "hasVisualRefsAttached": bool(character_images or location_images or props_images),
                    },
                    "refsDebug": refs_debug,
                    "validation": {
                        "scenario": mode,
                        "sceneCount": len((parsed or {}).get("scenes") or []),
                        "rejectedReason": validation_rejected_reason,
                        "repairRetryUsed": retry_used,
                        "warnings": validation_warnings,
                    },
                },
            },
        )

    plan = parsed
    track = dict(plan.get("track") or {})
    audio_duration = _resolve_timeline_duration(plan)
    track["durationSec"] = audio_duration
    scenes = plan.get("scenes") or []
    world_scale_context = _normalize_world_scale_context(plan.get("worldScaleContext"))
    if not world_scale_context:
        world_scale_context = _detect_world_scale_context(text=text, scenes=scenes, session_world_anchors=session_world_anchors)

    entity_scale_anchors = _extract_entity_scale_anchors(plan.get("entityScaleAnchors"))
    if not entity_scale_anchors:
        entity_scale_anchors = _default_entity_scale_anchors(world_scale_context)
    entity_scale_anchor_text = _format_entity_scale_anchors(entity_scale_anchors)

    normalized_scenes = []
    previous_scene = None
    previous_continuity_memory = None
    session_baseline = {
        "character": session_world_anchors["character"],
        "location": session_world_anchors["location"],
        "style": session_world_anchors["style"],
        "lighting": lighting_anchor,
        "environment": environment_anchor,
        "weather": weather_anchor,
        "surface": surface_anchor,
        "propAnchorLabel": prop_anchor_label or None,
        "worldScaleContext": world_scale_context,
        "entityScaleAnchors": entity_scale_anchors,
        "productionScale": _derive_production_scale(session_world_anchors=session_world_anchors, scene=scenes[0] if scenes else {}),
        "audienceState": "same event audience identity, crowd scale class, density logic, and front-row geometry across all scenes",
    }
    for idx, s in enumerate(scenes):
        start = float(s.get("start"))
        end = float(s.get("end"))
        visual_prompt = str(s.get("visualPrompt") or "").strip()
        visual_desc = str(s.get("visualDescription") or "").strip()
        lip_sync_text = str(s.get("lipSyncText") or "").strip()
        lyric_fragment = str(s.get("lyricFragment") or lip_sync_text).strip()
        video_prompt = str(s.get("videoPrompt") or visual_prompt or visual_desc).strip()
        reason_text = str(s.get("reason") or "").strip()
        if prop_anchor_label:
            visual_prompt = _enforce_prop_anchor_text(visual_prompt, prop_anchor_label, lang="en")
            video_prompt = _enforce_prop_anchor_text(video_prompt, prop_anchor_label, lang="en")
            visual_desc = _enforce_prop_anchor_text(visual_desc, prop_anchor_label, lang="ru")
            reason_text = _enforce_prop_anchor_text(reason_text, prop_anchor_label, lang="ru")

        raw_transition_type = str(s.get("transitionType") or "").strip().lower()
        if raw_transition_type in _TRANSITION_TYPES:
            transition_type = raw_transition_type
        else:
            transition_type = _infer_transition_type(s)

        scene_type = str(s.get("sceneType") or "visual_rhythm").strip() or "visual_rhythm"
        start_frame_prompt = ""
        end_frame_prompt = ""
        frame_prompt = ""
        transition_action_prompt = ""

        if transition_type == "continuous":
            start_frame_prompt = str(s.get("startFramePrompt") or "").strip()
            end_frame_prompt = str(s.get("endFramePrompt") or "").strip()
            transition_action_prompt = str(s.get("transitionActionPrompt") or "").strip()

            if not start_frame_prompt:
                start_frame_prompt = str(s.get("visualDescription") or s.get("visualPrompt") or "").strip()
            if not end_frame_prompt:
                end_frame_prompt = str(s.get("visualPrompt") or s.get("visualDescription") or "").strip()
            if not transition_action_prompt:
                transition_action_prompt = str(s.get("reason") or s.get("motion") or s.get("visualDescription") or "").strip()

            video_prompt = (
                transition_action_prompt
                or str(s.get("videoPrompt") or "").strip()
                or reason_text
                or str(s.get("motion") or "").strip()
                or visual_desc
            )

        elif transition_type in {"single", "hard_cut"}:
            frame_prompt = str(s.get("framePrompt") or s.get("visualPrompt") or s.get("visualDescription") or "").strip()

        if transition_type == "continuous":
            prompt_value = end_frame_prompt or start_frame_prompt or visual_prompt or visual_desc
            image_prompt_value = start_frame_prompt or end_frame_prompt or visual_prompt or visual_desc
        else:
            prompt_value = frame_prompt or visual_prompt or visual_desc
            image_prompt_value = frame_prompt or visual_prompt or visual_desc

        continuity_memory = _sanitize_continuity_memory(s.get("continuityMemory"))
        if not continuity_memory:
            continuity_memory = _build_scene_continuity_memory(
                scene={
                    **s,
                    "worldScaleContext": world_scale_context,
                    "entityScaleAnchors": entity_scale_anchor_text,
                    "sceneText": visual_desc,
                    "imagePrompt": visual_prompt,
                    "why": reason_text,
                },
                session_world_anchors=session_world_anchors,
                prop_anchor_label=prop_anchor_label,
            )
        scene_delta = _build_scene_delta(s, previous_scene)
        scene_text_ru = visual_desc or reason_text or lyric_fragment
        scene_obj = {
            **s,
            "id": str(s.get("id") or f"scene_{idx + 1:03d}"),
            "start": start,
            "end": end,
            "transitionType": transition_type,
            "startFramePrompt": start_frame_prompt,
            "endFramePrompt": end_frame_prompt,
            "framePrompt": frame_prompt,
            "transitionActionPrompt": transition_action_prompt,
            "prompt": prompt_value,
            "sceneDelta": scene_delta,
            "sceneText": scene_text_ru,
            "imagePrompt": image_prompt_value,
            "videoPrompt": video_prompt,
            "why": reason_text,
            "sceneType": scene_type,
            "isLipSync": bool(lip_sync_text),
            "lipSyncText": lip_sync_text,
            "lyricFragment": lyric_fragment,
            "continuityMemory": continuity_memory,
            "previousContinuityMemory": previous_continuity_memory,
            "worldScaleContext": world_scale_context,
            "entityScaleAnchors": entity_scale_anchors,
            "productionScale": (session_baseline or {}).get("productionScale") if isinstance(session_baseline, dict) else None,
            "audienceState": (session_baseline or {}).get("audienceState") if isinstance(session_baseline, dict) else None,
        }
        normalized_scenes.append(scene_obj)
        previous_scene = s
        previous_continuity_memory = continuity_memory

    normalized_scenes = _apply_lipsync_performance_rules(
        scenes=normalized_scenes,
        duration=float(audio_duration),
        vocal_phrases=plan.get("vocalPhrases") if isinstance(plan.get("vocalPhrases"), list) else [],
        want_lipsync=bool(payload.wantLipSync),
    )
    lip_sync_scenes = []
    for scene in normalized_scenes:
        if not isinstance(scene, dict):
            continue
        if bool(scene.get("lipSync") or scene.get("isLipSync") or str(scene.get("renderMode") or "").strip().lower() == "avatar_lipsync"):
            lip_sync_scenes.append(scene)

    return {
        "ok": True,
        "engine": "gemini",
        "modelUsed": model_used,
        "fallbackUsed": False,
        "hint": None if audio_bytes else "plan_built_without_audio_bytes",
        "audioDuration": audio_duration,
        "track": track,
        "sections": plan.get("sections") if isinstance(plan.get("sections"), list) else [],
        "vocalPhrases": plan.get("vocalPhrases") if isinstance(plan.get("vocalPhrases"), list) else [],
        "energyEvents": plan.get("energyEvents") if isinstance(plan.get("energyEvents"), list) else [],
        "worldScaleContext": world_scale_context,
        "entityScaleAnchors": entity_scale_anchors,
        "scenes": normalized_scenes,
        "propAnchor": prop_anchor,
        "sessionWorldAnchors": {
            "character": session_world_anchors["character"],
            "location": session_world_anchors["location"],
            "style": session_world_anchors["style"],
        },
        "sessionBaseline": session_baseline,
        "plannerDebug": {
            "audio": audio_debug,
            "inputState": input_state_debug,
            "model": {
                "modelUsed": model_used,
                "hasVisualInputs": has_visual_inputs,
                "hasVisualRefsAttached": bool(character_images or location_images or props_images),
            },
            "refsDebug": refs_debug,
            "validation": {
                "scenario": mode,
                "sceneCount": len(normalized_scenes),
                "rejectedReason": validation_rejected_reason,
                "repairRetryUsed": retry_used,
                "warnings": validation_warnings,
            },
            "summary": {
                "totalSceneCount": len(normalized_scenes),
                "totalLipSyncCandidatesSelected": len(lip_sync_scenes),
            },
            "lipSyncDebug": {
                "wantLipSync": bool(payload.wantLipSync),
                "lipSyncSceneCount": len(lip_sync_scenes),
                "lipSyncSceneIds": [str(scene.get("id") or "") for scene in lip_sync_scenes if str(scene.get("id") or "").strip()],
                "lipSyncScenes": [
                    {
                        "sceneId": str(scene.get("id") or ""),
                        "lipSync": bool(scene.get("lipSync")),
                        "isLipSync": bool(scene.get("isLipSync")),
                        "renderMode": str(scene.get("renderMode") or ""),
                        "sceneType": str(scene.get("sceneType") or ""),
                        "shotType": str(scene.get("shotType") or ""),
                        "audioSliceStartSec": scene.get("audioSliceStartSec"),
                        "audioSliceEndSec": scene.get("audioSliceEndSec"),
                    }
                    for scene in lip_sync_scenes
                ],
            },
        },
    }


@router.post("/clip/image")
def clip_image(payload: ClipImageIn):
    scene_id = (payload.sceneId or "").strip()
    prompt = (payload.prompt or "").strip()
    scene_delta = (payload.sceneDelta or prompt).strip()
    style = (payload.style or "default").strip()

    if not scene_id:
        return JSONResponse(status_code=400, content={"ok": False, "code": "BAD_REQUEST", "hint": "sceneId_required"})
    if not scene_delta:
        return JSONResponse(status_code=400, content={"ok": False, "code": "BAD_REQUEST", "hint": "sceneDelta_or_prompt_required"})

    width = max(256, min(2048, int(payload.width or 1024)))
    height = max(256, min(2048, int(payload.height or 1024)))
    scene_text = (payload.sceneText or "").strip()
    refs_obj = payload.refs
    character_refs = _normalize_ref_list(getattr(refs_obj, "character", None))
    location_refs = _normalize_ref_list(getattr(refs_obj, "location", None))
    style_refs = _normalize_ref_list(getattr(refs_obj, "style", None))
    props_refs = _normalize_ref_list(getattr(refs_obj, "props", None))
    prop_anchor_label = _clean_anchor_label(getattr(refs_obj, "propAnchorLabel", None))
    session_character_anchor = str(getattr(refs_obj, "sessionCharacterAnchor", "") or "").strip()
    session_location_anchor = str(getattr(refs_obj, "sessionLocationAnchor", "") or "").strip()
    session_style_anchor = str(getattr(refs_obj, "sessionStyleAnchor", "") or "").strip()
    session_baseline = getattr(refs_obj, "sessionBaseline", None)
    world_scale_context = _normalize_world_scale_context(getattr(refs_obj, "worldScaleContext", None))
    if not world_scale_context and isinstance(session_baseline, dict):
        world_scale_context = _normalize_world_scale_context((session_baseline or {}).get("worldScaleContext"))
    entity_scale_anchors = _extract_entity_scale_anchors(getattr(refs_obj, "entityScaleAnchors", None))
    if not entity_scale_anchors and isinstance(session_baseline, dict):
        entity_scale_anchors = _extract_entity_scale_anchors((session_baseline or {}).get("entityScaleAnchors"))
    if not world_scale_context:
        world_scale_context = _detect_world_scale_context(
            text=f"{scene_text} {scene_delta}",
            scenes=[],
            session_world_anchors={"location": session_location_anchor, "style": session_style_anchor},
        )
    if not entity_scale_anchors:
        entity_scale_anchors = _default_entity_scale_anchors(world_scale_context)
    entity_scale_anchor_text = _format_entity_scale_anchors(entity_scale_anchors)
    previous_continuity_memory = _sanitize_continuity_memory(getattr(refs_obj, "previousContinuityMemory", None))
    previous_scene_image_url = str(getattr(refs_obj, "previousSceneImageUrl", "") or "").strip()
    previous_scene_image_inline = _load_reference_image_inline(previous_scene_image_url) if previous_scene_image_url else None

    character_images = []
    for ref_url in character_refs:
        inline_part = _load_reference_image_inline(ref_url)
        if inline_part:
            character_images.append(inline_part)

    location_images = []
    for ref_url in location_refs:
        inline_part = _load_reference_image_inline(ref_url)
        if inline_part:
            location_images.append(inline_part)

    style_images = []
    for ref_url in style_refs:
        inline_part = _load_reference_image_inline(ref_url)
        if inline_part:
            style_images.append(inline_part)

    props_images = []
    for ref_url in props_refs:
        inline_part = _load_reference_image_inline(ref_url)
        if inline_part:
            props_images.append(inline_part)

    prop_anchor_source = "payload" if prop_anchor_label else "fallback"
    if props_images and not prop_anchor_label:
        api_key_for_anchor = (settings.GEMINI_API_KEY or "").strip()
        anchor_model = (getattr(settings, "GEMINI_VISION_MODEL", None) or "gemini-1.5-flash").strip()
        if api_key_for_anchor:
            prop_anchor_label = _infer_prop_anchor_label(props_images, api_key_for_anchor, anchor_model)
            prop_anchor_source = "inferred" if prop_anchor_label else "fallback"

    refs_debug = {
        "characterRefCount": len(character_refs),
        "characterImagesAttached": len(character_images),
        "locationRefCount": len(location_refs),
        "locationImagesAttached": len(location_images),
        "styleRefCount": len(style_refs),
        "styleImagesAttached": len(style_images),
        "propsRefCount": len(props_refs),
        "propsImagesAttached": len(props_images),
        "propAnchorLabel": prop_anchor_label or None,
        "propAnchorSource": prop_anchor_source,
        "sessionCharacterAnchor": session_character_anchor or None,
        "sessionLocationAnchor": session_location_anchor or None,
        "sessionStyleAnchor": session_style_anchor or None,
        "worldScaleContext": world_scale_context or None,
        "entityScaleAnchors": entity_scale_anchors,
        "hasSessionBaseline": bool(isinstance(session_baseline, dict) and session_baseline),
        "hasPreviousContinuityMemory": bool(previous_continuity_memory),
        "hasPreviousSceneImage": bool(previous_scene_image_inline),
    }

    style_anchor = (
        "season, weather, color palette and cinematic visual language must be taken directly from style reference images"
        if style_refs
        else ((style or "").strip() or "world-coherent cinematic realism")
    )
    lighting_anchor = (
        "light direction, softness, exposure and color temperature must match the lighting implied by style reference images"
        if style_refs
        else "environment-driven cinematic lighting derived from the location and world state"
    )
    location_anchor = (
        "architecture style, street geometry, paving materials and environmental aging must match location reference images"
        if location_refs
        else "coherent single-location environment"
    )
    environment_anchor = "weather, atmosphere, surface materials and environmental mood must remain stable across scenes"
    weather_anchor = (
        "weather state must be taken directly from style reference images and remain unchanged across scenes"
        if style_refs
        else "coherent stable weather state across scenes"
    )
    surface_anchor = (
        "ground/surface state must be taken directly from style reference images, including snow traces, wetness, reflections, and material condition"
        if style_refs
        else "coherent stable ground and surface condition across scenes"
    )

    has_visual_refs_attached = bool(character_images or location_images or style_images or props_images)
    # Normalize aspect label for prompt
    if height > width:
        aspect_ratio = "9:16"
    elif width > height:
        aspect_ratio = "16:9"
    else:
        aspect_ratio = "1:1"

    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        image_url = _mock_scene_image(scene_id, width, height)
        return {
            "ok": True,
            "sceneId": scene_id,
            "imageUrl": image_url,
            "engine": "mock",
            "hint": "no_gemini_key",
            "modelUsed": None,
            "refsDebug": refs_debug,
        }

    try:
        model = settings.GEMINI_IMAGE_MODEL or "gemini-2.5-flash-image-preview"

        system_prompt = (
            "You are a professional film director, cinematographer and visual production designer creating scenes for a cinematic music video. "
            "All scenes belong to the same continuous world and moment in time. "
            "SCENE-TO-SCENE CONTINUITY: treat this frame as the next moment after the previous scene, preserving persistent world setup while allowing new action and framing. "
            "PERSISTENT VS DELTA: keep location identity, lighting logic, palette, camera language, character identity, world state, and prop identity stable unless explicitly changed by scene payload; only action/emotion/framing progression should change. "
            "PRODUCTION SCALE LOCK: keep the same show production scale class across scenes unless the storyboard explicitly changes venue scale. Energy/intensity may rise, but do not upgrade small/medium concert staging into arena/festival scale without explicit instruction. Preserve stage geometry class, rig scale, and production footprint continuity. "
            "AUDIENCE SCALE/IDENTITY LOCK: preserve the same event crowd across scenes: same crowd scale class, same density logic, same front-row geometry logic, and same overall audience identity. Crowd emotion may intensify, but it must still read as the same audience from the same event. "
            "DELTA PRECISION RULE: if scene delta requests a close-up, microphone detail, hand detail, or emotional facial beat, the frame must prioritize that exact beat and framing intent, and must not drift back to a generic wide performance shot. "
            "NO COMPOSITION CLONE: do not copy previous frame composition exactly; continuity reference is soft and cinematic, not pose/framing lock. "
            "GLOBAL WORLD CONTINUITY: preserve consistency for time of day, lighting conditions, sky brightness and color, street light intensity, ambient brightness, atmospheric haze/fog, and environmental color grading. "
            "WEATHER CONSISTENCY: maintain consistent snow/rain/fog/wind, snow coverage, wet/dry surfaces, atmospheric particles, and visible breath in cold air when applicable. "
            "LOCATION WORLD LOCK: keep architecture style, building proportions, street layout, materials/textures, signage style, and cultural environment as the same location. "
            "TIME PERIOD CONSISTENCY: architecture, vehicles, clothing, signage, and technology must remain in the same historical era. "
            "REFERENCE RULES: use all provided references as source of truth. Character references define the same person. Location references define the same world and architecture. Style references define weather, season, palette, atmosphere, and cinematic language. Props references define key objects. "
            "SOURCE PRIORITY RULES: Character references define who the person is. Location references define where the scene exists. Style references define season, weather, palette, atmosphere and visual language. Props references define exact object identity. Scene text defines action, emotion, narrative, interaction and placement. Visual prompt defines composition and shot content. Audio (if available) defines timing, rhythm, intensity, and lipsync energy. Shoot mode defines camera language. styleKey/style field is fallback style only when no style references exist. Free imagination is lowest priority and is allowed only when no higher-priority source defines that element. "
            "If any lower-priority input conflicts with higher-priority references, higher-priority references win. Higher-priority sources must never be overridden by lower-priority ones. "
            "Character refs cannot be overridden by scene text. Location refs cannot be overridden by scene text. Style refs cannot be overridden by scene text or generic visual prompt. Props refs cannot be overridden by scene text or generic visual prompt. "
            "WORLD LIGHTING PRIORITY: location/world/style references define the lighting model, atmosphere, palette, and environmental state. Character and prop references must adapt to that world state. Characters must not preserve reference-image lighting. Props must not preserve reference-image lighting. Props never define scene lighting. "
            "If style refs are absent, styleKey/style may influence the image as fallback. "
            "PROP PRIORITY RULES: if props reference images are attached, props refs define exact object identity. Scene text may describe how the object is used and where it is placed, but must not redefine, replace, or rename what the object is. If text conflicts with props refs, props refs win. If props refs are absent, text may define scene objects. "
            "STRICT OBJECT LOCK: The prop reference image defines the exact prop identity for this session. The prop must remain the same object across all scenes. Never reinterpret, replace, rename, generalize, or downgrade it into another object. "
            "CHARACTER IDENTITY LOCK: preserve facial structure, hairstyle, body proportions, skin tone, facial hair, gender, and age appearance. Do not redesign the person. "
            "CHARACTER DETAIL LOCK: preserve clothing type/colors, logos/brand marks, accessories, hairstyle, and carried items unless scene text explicitly changes wardrobe. "
            "PROP CONSISTENCY: maintain prop design, materials, dimensions, cables/attachments, brand markings, and wear/texture. Do not redesign props. "
            "PHYSICAL SCALE CONSISTENCY: maintain realistic human-relative scale; handheld objects must remain realistically liftable and consistent across scenes. "
            "BACKGROUND CHARACTER CONTROL: background people may appear but must remain subtle and non-distracting. "
            "WORLD DETAIL CONSISTENCY: keep vegetation, street furniture, parked vehicles, shop signs, decorations, snow accumulation, and ground texture consistent without random major changes. "
            "CINEMATIC STYLE CONSISTENCY: preserve coherent color grading, lighting mood, contrast, film atmosphere, and lens feel across scenes. "
            "SUBJECT INTEGRATION: character and objects must match environment in lighting direction/intensity, color temperature, reflections, ambient light, and shadows. "
            "GROUND CONTACT: ensure believable physical interaction with surfaces via contact shadows, footprints/compression, and wet reflections when appropriate. "
            "CINEMATIC ATMOSPHERE: use natural depth of field, atmospheric perspective, subtle haze/light scattering, realistic materials/textures, and filmic grading. Avoid plastic skin, flat lighting, and synthetic artifacts. "
            "FINAL RULE: generate ONE cinematic still frame that looks like real footage from a professional film production, never an artificial collage. "
            "WORLD CONSISTENCY: Maintain the same environment, lighting and visual style as previous scenes from the storyboard. The world state must remain stable. Do not change weather, lighting, architecture, season, or color palette. Treat this frame as another camera shot from the same film scene. "
            "ENVIRONMENT CONTINUITY: The environment must remain visually consistent. Maintain same street type, same architectural style, same weather conditions, same surface materials, and same atmosphere. The viewer should feel that all frames belong to the same real location. "
            f"SESSION WORLD ANCHORS:\n"
            f"Style anchor: {style_anchor}\n"
            f"Lighting anchor: {lighting_anchor}\n"
            f"Location anchor: {location_anchor}\n"
            f"Environment anchor: {environment_anchor}\n\n"
            f"Weather anchor: {weather_anchor}\n"
            f"Surface anchor: {surface_anchor}\n\n"
            "Use these anchors as global constraints. "
            "All generated frames must obey these anchors. "
            "Do not reinterpret them. "
            "STYLE-DEFINED ENVIRONMENT STATE: If style reference images are present, they define season, weather state, lighting mood, color palette, surface condition, and atmospheric mood. These style-defined environmental states must remain stable across all scenes. Do not reinterpret or weaken them in later scenes. If the style references imply winter snow, snow traces and cold winter atmosphere, do not switch later scenes to generic wet cloudy weather without snow. "
            "WEATHER STATE LOCK: Weather must remain the same across the whole session unless explicitly changed by text. If the style reference implies snow, winter cold, or overcast winter weather, then all scenes must preserve that same weather state. Do NOT switch between snow, rain, dry cloudy weather, and neutral weather unless explicitly requested. Weather continuity includes presence/absence of snow, snow traces on roofs and ground, wetness level, and atmospheric coldness. "
            "SURFACE STATE LOCK: Ground and surface conditions must remain visually consistent across scenes. Maintain same pavement material, same wetness level, same snow traces, same reflection behavior, and same environmental wear. If the first scene shows wet cobblestone with snow traces, later scenes must preserve that same surface logic. "
            "GLOBAL ENVIRONMENT STATE: Style reference images define the global environment state for the entire session. This includes season, weather, lighting mood, color palette, atmospheric conditions, and ground surface state. These properties must remain constant across all scenes. Camera framing or shot type (wide, medium, close-up, macro) must NOT weaken these environmental constraints. "
            "VISIBLE WEATHER LOCK: If snow is part of the style-defined environment state, snow must remain visible in every frame. Snow accumulation or snow traces must remain visible on at least some of ground edges, rooftops, pavement gaps, horizontal surfaces, street borders, and environmental surfaces. Do not reduce snowy winter state into generic wet cold weather. Visible weather cues must remain present even in close-up and macro shots. "
            "SUBJECT RELIGHTING RULE: Character lighting must be derived entirely from the environment. Do not preserve lighting baked into character reference images. The generated subject must match the environment in light direction, color temperature, ambient bounce light, shadow softness, exposure, and atmospheric haze. The character must not look studio-lit inside an outdoor cinematic environment. "
            "PHYSICAL SUBJECT INTEGRATION: The character must appear physically present inside the same world as the background. Match ambient depth, edge contrast, environmental color bounce, surface reflections, ground contact shadows, and local atmospheric perspective. Do not render the character as pasted, composited, cut out, separately lit, or cleaner than the environment. The subject must feel photographed in the same place and lighting conditions as the environment. "
            "SUBJECT AND PROP ENVIRONMENT MATCH: Character and prop must inherit the same environmental qualities as the scene. Match ambient haze, dust, smoke diffusion, reflected dirty light, floor color bounce, local contrast softness, and environmental color contamination. Do not render the character or prop as cleaner, sharper, or separately lit than the environment. Character and prop must feel physically present in the same air, same light, and same atmosphere as the scene. "
            "NO CUTOUT / NO COMPOSITE LOOK: Do not render the character or prop as pasted, composited, cut out, sticker-like, separately exposed, or separately color-graded. They must feel captured inside the same environment, with the same atmospheric depth and lighting logic. Edges, contrast, color temperature, and softness must match the environment. "
            "CHARACTER INTEGRATION LOCK: The character must inherit local ambient light, environmental shadow softness, atmospheric haze, reflected floor color, and industrial/urban environmental contamination when applicable. Do not keep the character unnaturally clean or studio-like if the world is dusty, hazy, smoky, wet, dirty, snowy, or industrial. The subject must feel photographed in the same environment, not inserted afterward. "
            "ATMOSPHERIC DEPTH RULE: All visible elements must be affected by the same atmosphere. Apply consistent haze, moisture, light scattering and atmospheric depth to background, character, and props. Do not keep the subject artificially crisp if the environment is soft, hazy, cold, wet, snowy, or diffuse-lit. "
            "PROP SCALE LOCK: The prop must preserve the same real-world physical size class across all scenes. The object must remain physically plausible relative to the human body. Do not enlarge it, shrink it, exaggerate it, miniaturize it, or distort its real-world scale between shots. The prop must keep stable human-relative scale in every frame. Example: if the prop is a portable welding machine, it must remain portable-welder sized in every scene, not suitcase-sized in one scene and generator-sized in another. "
            "PROP SIZE CLASS LOCK: The prop must belong to a stable real-world size class across all scenes. The object must not change its physical class between frames. Example: a portable welding machine must remain portable-welder sized in every frame. It must not become oversized, generator-sized, miniaturized, enlarged to fit composition, or distorted in apparent volume. The prop must remain physically plausible relative to the human body. "
            "HARD PROP SIZE CLASS LOCK: The prop belongs to a fixed real-world size class. If the prop is a portable welding machine, its physical class is compact carryable equipment, approximately small-suitcase class. It must never become oversized, generator-sized, floor-machine sized, enlarged to dominate the frame, or visually inflated to reveal more detail. The prop must keep the same physical size class across all scenes. "
            "BODY-RELATIVE SCALE REFERENCE: The prop must remain consistent relative to the human body. Use stable body-relative references such as hand grip, shin height, knee level, lower leg size, and forearm carry scale. Do not change prop size class between wide shots, medium shots, close-ups, and macro shots. Framing must not justify scaling the object larger or smaller. "
            "CAMERA DISTANCE RULE: Changes in framing must come from camera distance, not object scaling. Wide shots, medium shots, close-ups, and macro shots must NOT change the physical size of the prop. The prop must remain the same real-world object size. When a shot becomes closer, the camera moves closer to the object instead of enlarging it. Do not increase prop size to reveal detail. Framing changes must be achieved through camera movement, lens choice, or crop — not object scaling. "
            "PROP DETAIL RULE: When a shot requires more visible detail of the prop, reveal detail through camera proximity, lighting, and focus. Do NOT increase the object's physical size. "
            "HUMAN HAND SCALE RULE: Handheld props must remain consistent with human hand size. If a prop is carried by one hand, its dimensions must remain believable for a single-hand grip. Do not enlarge handheld props beyond realistic carryable scale. "
            "ANATOMIC ANCHORING: Object scale must be anchored relative to the human body. Use stable body-relative proportions such as knee height, lower leg height, hand-carryable size, and forearm/torso relation. The prop must keep the same body-relative scale across all shots. Do not resize the object just because the framing changes. "
            "MACRO CONTEXT LOCK: In close-up or macro shots, the environment state must remain visible through the surface context. If the wide-shot environment is snowy wet cobblestone street, then close-up shots must preserve that same surface logic. Macro shots must not forget snow traces, wetness, pavement material, and winter environment cues. Close framing must not weaken global world continuity. "
            "PROP INTEGRATION HARD LOCK: The prop must be integrated into the environment with the same ambient light, shadow softness, color temperature, reflected floor color, atmospheric softness, and dirt/haze/smoke context. Do not render the prop as a clean product render inside a dirty scene. The prop must visually belong to the same world as the floor, air, and surrounding light. "
            "PROP RELIGHTING RULE: Do not preserve lighting baked into prop reference images. Prop references define object identity, category, silhouette, material cues, and usage only. Every prop must be fully relit by the current scene environment and must match world light direction, color temperature, ambient bounce, atmospheric diffusion, environmental reflections, shadow softness, shadow direction, and environment color contamination. "
            "ENVIRONMENTAL CONTAMINATION LOCK: If the environment contains dust, smoke, industrial haze, wet reflections, cold fog, snow residue, or dirty floor bounce, then character and prop must inherit that environmental contamination visually. They must not look isolated from the environmental conditions. "
            "SURFACE INTERACTION RULE: Any object placed on ground, floor, table, or other support surface must show physical contact with that surface. Require contact shadows, plausible contact pressure, subtle local bounce or reflections when appropriate, and slight dust/dirt grounding when appropriate. No floating look and no clean studio isolation inside dirty, industrial, snowy, or wet environments. "
            "PROP CATEGORY AND SCALE LOCK: Prop references define object class (portable_tool, handheld_object, device, furniture, large_machine, vehicle, environment_object). Keep category-faithful shape, function, and human-relative scale across all shots. Portable and handheld props must keep realistic body-relative size and must not randomly grow or shrink between scenes. "
            "CLIP WORLD LOCK: Every shot in one clip must belong to one shared world identity. Preserve the same lighting logic, atmosphere, weather state, palette, material response, and dust/fog/snow/rain state across shots. Shot variation is allowed, but world identity must remain unchanged. "
            "PROP PHYSICAL CONSISTENCY: Keep consistent size relative to hands, size relative to torso/legs, grip logic, weight impression, handle/cable behavior, and ground contact behavior. The prop must not look weightless, oversized, undersized, or physically inconsistent between scenes. If the prop is handheld, its scale must remain realistically liftable by the character. "
            "Scene text may be Russian and visual prompt may be English. Use both when available: visual prompt defines composition/action, and scene text defines narrative context and emotion."
        )

        parts = [{"text": system_prompt}]

        if character_images:
            parts.append({"text": "Character reference images. All depict the SAME main character."})
            parts.extend(character_images)

        if location_images:
            parts.append({"text": "Location reference images. These define the same world/environment."})
            parts.extend(location_images)

        if style_images:
            parts.append({"text": "Style reference images. These define season, weather, palette, texture, atmosphere, and overall visual language. Apply them explicitly and visibly in the final frame."})
            parts.extend(style_images)

        if props_images:
            parts.append({"text": "Props reference images. These are key scene objects. Keep them prominent when relevant; if only one prop is attached, treat it as primary and do not omit it."})
            parts.extend(props_images)
            parts.append({"text": "The prop identity is defined by the reference images and must not be replaced."})
            if prop_anchor_label:
                parts.append({"text": f"Session prop anchor label: {prop_anchor_label}. Keep exactly this prop identity."})

        if prop_anchor_label:
            scene_delta = _enforce_prop_anchor_text(scene_delta, prop_anchor_label, lang="en")
            scene_text = _enforce_prop_anchor_text(scene_text, prop_anchor_label, lang="ru")

        effective_character_anchor = str((session_baseline or {}).get("character") or session_character_anchor or "").strip()
        effective_location_anchor = str((session_baseline or {}).get("location") or session_location_anchor or "").strip()
        effective_style_anchor = str((session_baseline or {}).get("style") or session_style_anchor or "").strip()

        assembled_prompt = (
            _inject_session_world_anchors(
                scene_delta,
                {
                    "character": effective_character_anchor or "coherent single-character identity across all scenes",
                    "location": effective_location_anchor or location_anchor,
                    "style": effective_style_anchor or style_anchor,
                },
            )
            + "\n\n"
            + f"Lighting anchor: {lighting_anchor}\n"
            + f"Environment anchor: {environment_anchor}\n"
            + f"Weather anchor: {weather_anchor}\n"
            + f"Surface anchor: {surface_anchor}\n\n"
            "WORLD SCALE CONTEXT RULES:\n\n"
            f"World scale context: {world_scale_context}.\n"
            f"Entity scale anchors: {entity_scale_anchor_text}.\n"
            "Keep these anchors stable across all scenes.\n"
            "Do not randomly rescale anchored entities between shots.\n"
            "In close-up framing, preserve perceived scale using perspective, crop, partial-body cues, and foreground/background layering.\n"
            "Threat entities (monster/predator/boss) must visually dominate frame presence through scale, occupancy, or spatial pressure even when partially visible.\n\n"
            "Wide shots should clearly reveal the relative size relationship between anchored entities whenever possible.\n\n"
            "PHYSICAL SCALE RULES:\n\n"
            "Keep the prop at the same realistic real-world size across all frames.\n"
            "The object must remain physically plausible relative to the person.\n"
            "Do not change object scale between shots.\n\n"
            "WEATHER / SURFACE RULES:\n\n"
            "Keep the same weather state and surface condition as defined by the style references.\n"
            "Do not remove snow if snow is part of the style-defined world state.\n"
            "Do not switch surface logic between snowy, wet, and dry unless explicitly requested.\n\n"
            "SUBJECT / SCALE / ATMOSPHERE RULES:\n\n"
            "Keep the character fully integrated into the environment.\n"
            "Match subject lighting to the scene.\n"
            "Preserve visible weather cues from the style-defined world state.\n"
            "Keep the prop at the same realistic real-world size class across all frames.\n"
            "Do not resize the prop for composition.\n"
            "Maintain the same surface logic in wide, medium, close-up and macro shots.\n\n"
            "SUBJECT / PROP REALISM RULES:\n\n"
            "Keep the character and prop fully integrated into the environment.\n"
            "Do not allow pasted or cutout appearance.\n"
            "Match local atmosphere, dirty light, ambient haze, reflections, and contrast softness.\n"
            "Keep the prop at the same compact real-world size class across all frames.\n"
            "Do not enlarge the prop for visibility or composition.\n\n"
            "HARD WORLD RELIGHTING RULES:\n\n"
            "Location/style/world references are the lighting authority for this frame.\n"
            "Character and prop references define identity only and must be fully relit by world lighting and atmosphere.\n"
            "Never preserve reference-image lighting for props or characters.\n"
            "Any grounded object must show contact shadows and believable surface interaction.\n"
            "All visible objects must inherit environmental color contamination, haze, and weather response.\n"
            "Keep one world identity across clip shots: stable lighting logic, palette, atmosphere, weather, and material response."
        )

        generation_mode = "continuity_chain" if previous_scene_image_inline else "baseline_only"

        if isinstance(session_baseline, dict) and session_baseline:
            parts.append({
                "text": "Session baseline (persistent world anchors for whole storyboard):\n" + json.dumps(session_baseline, ensure_ascii=False)
            })

        if previous_scene_image_inline:
            parts.append({"text": "Previous generated scene image (visual continuity reference, do not clone composition):"})
            parts.append(previous_scene_image_inline)

        if previous_continuity_memory:
            parts.append({
                "text": "Previous scene continuity memory (persistent state to inherit; keep as soft continuity reference, not composition clone):\n" + json.dumps(previous_continuity_memory, ensure_ascii=False)
            })
            parts.append({
                "text": (
                    "CONTINUITY EXECUTION RULES:\n"
                    "PERSIST from continuity memory: world/location identity, lighting logic, color palette/grade, camera language, character identity, key props, global event/world condition, production scale class, and audience identity/scale logic.\n"
                    "CHANGE for the current scene: action beat, pose, expression, blocking, framing, camera distance/angle, and moment progression.\n"
                    "DELTA PRECISION: if sceneDelta asks for close-up/microphone detail/hand detail/emotional facial beat, keep that exact focal beat in-frame rather than reverting to a generic wide shot.\n"
                    "Do not copy previous composition or freeze previous pose. This must feel like the next cinematic moment in the same film world.\n\n"
                    "CINEMATIC SCENE PROGRESSION RULES:\n"
                    "Scenes must behave like a cinematic storyboard.\n"
                    "Each scene must represent a new visual moment in time.\n"
                    "Consecutive scenes must not repeat the same composition, camera position, or character pose.\n"
                    "Every new scene must introduce at least one visible change.\n"
                    "Allowed visible changes: camera angle/distance/position, character pose/movement/orientation, framing change, or interaction with environment.\n"
                    "If a character is moving, show progression stages across scenes (start movement, continue, approach destination, stop, turn, react).\n"
                    "Avoid repeating the same shot type in consecutive scenes.\n"
                    "Use natural cinematic progression like wide→medium→close, back→side→front, movement→pause→reaction, or environment→subject→detail.\n"
                    "Each scene must feel like the next camera shot from the same film sequence, never a repeated frame.\n\n"
                    "CHARACTER POSE VARIATION RULE:\n"
                    "Reference images define character identity only.\n"
                    "References must preserve face identity, body proportions, hairstyle, clothing/logos, and accessories.\n"
                    "Reference images must not lock character pose.\n"
                    "Character pose must change naturally between scenes according to action, movement, and cinematic progression.\n"
                    "Allow natural variation in body orientation, step position, arm movement, hand position, head direction, weight distribution, stance, and posture.\n"
                    "Maintain identity consistency but avoid copying the exact reference pose.\n\n"
                    "POSE PROGRESSION RULE:\n"
                    "When a character is walking, running, turning, searching, reacting, or interacting with environment, each scene must show a different stage of movement.\n"
                    "Walking progression examples: left step, right step, slowing down, stopping, shifting weight.\n"
                    "Reaction progression examples: noticing, head turn, focus, emotional response.\n\n"
                    "POSE REPETITION PREVENTION:\n"
                    "Avoid repeating the same body pose, stance, or gesture across consecutive scenes.\n"
                    "Adjacent scenes must not show the exact same pose unless storyboard intent explicitly requires stillness.\n\n"
                    "REFERENCE POSE RELEASE RULE:\n"
                    "The visible pose in reference images must not dominate generated scenes.\n"
                    "Use references strictly for identity guidance; characters must behave like live actors performing current scene action.\n\n"
                    "CINEMATIC BODY LANGUAGE RULE:\n"
                    "Character body language must reflect the current story beat and evolve naturally scene-to-scene.\n"
                    "Movement beats require active posture; suspicion requires tension; fear requires defensive posture; curiosity requires leaning-forward intent; reaction beats require sudden shift in stance or motion.\n\n"
                    "SHOT CLARITY RULE:\n"
                    "Each scene must focus on one clear visual moment.\n"
                    "Do not overload one shot with too many narrative beats.\n"
                    "Discovery, reaction, important object, and realization moments should usually be split into separate shots.\n\n"
                    "SPATIAL PROGRESSION RULE:\n"
                    "Scenes must show progression through space and time.\n"
                    "If the character moves through a location, the environment perspective must evolve accordingly.\n"
                    "Show movement progression clearly: moving through environment, approaching target, stopping near target, then reacting.\n\n"
                    "STATIC FRAME PREVENTION:\n"
                    "If two scenes are narratively similar, their camera composition must still be visibly different.\n"
                    "Never output consecutive scenes that look like identical frames with only textual differences.\n"
                    "Every scene must contain an observable visual change."
                )
            })
        else:
            parts.append({
                "text": "No previous continuity memory for this scene (opening beat). Establish a strong persistent world baseline that later scenes can inherit."
            })

        scene_payload = {
            "sceneId": scene_id,
            "style": style,
            "styleKey": style,
            "aspectRatio": aspect_ratio,
            "resolution": f"{width}x{height}",
            "sceneText": scene_text,
            "sceneDelta": scene_delta,
            "visualPrompt": assembled_prompt,
            "generationMode": generation_mode,
            "propAnchorLabel": prop_anchor_label or None,
            "sessionCharacterAnchor": session_character_anchor or None,
            "sessionLocationAnchor": session_location_anchor or None,
            "sessionStyleAnchor": session_style_anchor or None,
            "previousContinuityMemory": previous_continuity_memory,
            "worldScaleContext": world_scale_context,
            "entityScaleAnchors": entity_scale_anchors,
            "productionScale": (session_baseline or {}).get("productionScale") if isinstance(session_baseline, dict) else None,
            "audienceState": (session_baseline or {}).get("audienceState") if isinstance(session_baseline, dict) else None,
            "styleAnchor": style_anchor,
            "lightingAnchor": lighting_anchor,
            "locationAnchor": location_anchor,
            "environmentAnchor": environment_anchor,
            "weatherAnchor": weather_anchor,
            "surfaceAnchor": surface_anchor,
        }
        parts.append({"text": "Scene payload:\n" + json.dumps(scene_payload, ensure_ascii=False)})

        body = {
            "contents": [{
                "role": "user",
                "parts": parts,
            }],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        resp = post_generate_content(api_key, model, body, timeout=120)
        decoded = _decode_gemini_image(resp if isinstance(resp, dict) else {})
        if decoded:
            raw, ext = decoded
            image_url = _save_bytes_as_asset(raw, ext)
            return {
                "ok": True,
                "sceneId": scene_id,
                "imageUrl": image_url,
                "engine": "gemini",
                "modelUsed": model,
                "refsDebug": refs_debug,
                "generationMode": generation_mode,
            }

        image_url = _mock_scene_image(scene_id, width, height)
        return {
            "ok": True,
            "sceneId": scene_id,
            "imageUrl": image_url,
            "engine": "mock",
            "hint": "gemini_no_image",
            "modelUsed": model,
            "refsDebug": refs_debug,
            "generationMode": generation_mode,
        }
    except ValueError as e:
        return JSONResponse(status_code=400, content={"ok": False, "code": "BAD_REQUEST", "hint": str(e)[:300]})
    except Exception as e:
        try:
            image_url = _mock_scene_image(scene_id, width, height)
            return {
                "ok": True,
                "sceneId": scene_id,
                "imageUrl": image_url,
                "engine": "mock",
                "hint": f"gemini_error:{str(e)[:200]}",
                "modelUsed": model if 'model' in locals() else None,
                "refsDebug": refs_debug,
                "generationMode": generation_mode if 'generation_mode' in locals() else "baseline_only",
            }
        except Exception:
            return JSONResponse(status_code=500, content={"ok": False, "code": "IMAGE_GENERATION_FAILED", "hint": str(e)[:300]})


@router.post("/clip/audio-slice")
def clip_audio_slice(payload: AudioSliceIn):
    scene_id = (payload.sceneId or "").strip()
    if not scene_id:
        return JSONResponse(status_code=400, content={"ok": False, "code": "BAD_REQUEST", "hint": "sceneId_required"})

    t0 = round(float(payload.t0), 3)
    t1 = round(float(payload.t1), 3)
    if t0 < 0:
        return JSONResponse(status_code=400, content={"ok": False, "code": "bad_t0", "hint": "t0_must_be_non_negative"})
    if t1 <= t0:
        return JSONResponse(status_code=400, content={"ok": False, "code": "bad_range", "hint": "t1_must_be_greater_than_t0"})
    if (t1 - t0) > 300.0:
        return JSONResponse(status_code=400, content={"ok": False, "code": "slice_too_long", "hint": "max_slice_sec_300"})

    path = _resolve_audio_asset_path(payload.audioUrl)
    if not path:
        _debug_audio_slice(payload.audioUrl, path)
        return JSONResponse(status_code=400, content={"ok": False, "code": "invalid_audioUrl", "hint": "audioUrl_must_point_to_/static/assets/<file>_or_/assets/<file>"})

    _ensure_assets_dir()
    safe_scene = re.sub(r"[^a-zA-Z0-9_-]", "_", scene_id) or "scene"
    t0_ms = int(round(t0 * 1000))
    t1_ms = int(round(t1 * 1000))
    filename = f"clip_audio_{safe_scene}_{t0_ms}_{t1_ms}_{uuid4().hex[:8]}.mp3"
    output_path = os.path.join(str(ASSETS_DIR), filename)

    ok, err = _ffmpeg_audio_slice(path, output_path, t0, t1)
    if not ok:
        _debug_audio_slice(payload.audioUrl, path)
        return JSONResponse(status_code=500, content={"ok": False, "code": "slice_failed", "hint": err})

    return {
        "ok": True,
        "audioUrl": payload.audioUrl,
        "audioSliceUrl": _asset_url(filename),
        "sliceUrl": _asset_url(filename),
        "t0": t0,
        "t1": t1,
        "duration": round(t1 - t0, 3),
        "audioSliceBackendDurationSec": round(t1 - t0, 3),
    }


@router.post("/clip/video")
def clip_video(payload: ClipVideoIn):
    scene_id = str(payload.sceneId or "").strip() or "scene"
    transition_type = _normalize_clip_video_transition_type(payload.transitionType)
    render_mode = str(payload.renderMode or "").strip().lower()
    is_lipsync = bool(payload.lipSync is True or render_mode == "avatar_lipsync")
    audio_slice_url = str(payload.audioSliceUrl or "").strip()
    image_url = str(payload.imageUrl or "").strip()
    start_image_url = str(payload.startImageUrl or "").strip()
    end_image_url = str(payload.endImageUrl or "").strip()
    output_format = str(payload.format or "9:16").strip() or "9:16"

    if render_mode == "avatar_lipsync" or is_lipsync:
        mode = "lipsync"
    elif render_mode == "standard_video":
        mode = "continuous" if _is_clip_video_transition_mode(transition_type, start_image_url, end_image_url) else "single"
    elif _is_clip_video_transition_mode(transition_type, start_image_url, end_image_url):
        mode = "continuous"
    else:
        mode = "single"

    if mode == "lipsync":
        if not (settings.PIAPI_API_KEY or "").strip():
            return JSONResponse(
                status_code=500,
                content={"ok": False, "code": "PIAPI_NOT_CONFIGURED", "hint": "missing_PIAPI_API_KEY", "details": "Set PIAPI_API_KEY in environment."},
            )
    elif not (settings.KIE_API_KEY or "").strip():
        return JSONResponse(
            status_code=500,
            content={"ok": False, "code": "KIE_NOT_CONFIGURED", "hint": "missing_KIE_API_KEY", "details": "Set KIE_API_KEY in environment."},
        )

    print(
        "[CLIP VIDEO ROUTING] "
        f"sceneId={scene_id} "
        f"lipSync={bool(payload.lipSync)} "
        f"renderMode={render_mode or 'n/a'} "
        f"transitionType={transition_type} "
        f"resolved_mode={mode}"
    )

    effective_prompt = ""
    if mode == "continuous":
        effective_prompt = str(payload.transitionActionPrompt or payload.videoPrompt or "").strip()
    else:
        effective_prompt = str(payload.videoPrompt or payload.transitionActionPrompt or "").strip()
    if mode == "lipsync":
        effective_prompt = _build_lipsync_avatar_prompt(effective_prompt, str(payload.shotType or ""))
    if not effective_prompt:
        effective_prompt = "cinematic motion, natural movement"

    print(f"[CLIP VIDEO] transition_type={transition_type}")
    print(f"[CLIP VIDEO] effective_prompt={effective_prompt[:300]}")
    print(f"[CLIP VIDEO] audio_slice_url={audio_slice_url}")

    if mode == "single":
        source_image_url = image_url or start_image_url or end_image_url
    else:
        source_image_url = start_image_url or image_url or end_image_url

    public_base_url = str(settings.PUBLIC_BASE_URL or "").strip()
    source_image_url = _normalize_source_image_url_for_kie(source_image_url)
    print(f"[CLIP VIDEO] public_base_url={public_base_url}")
    print(f"[CLIP VIDEO] normalized_source_image_url={source_image_url}")

    if not source_image_url:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "code": "VIDEO_SOURCE_IMAGE_REQUIRED",
                "hint": "imageUrl_or_startImageUrl_required",
                "details": "Provide imageUrl for single mode or startImageUrl/endImageUrl for transition modes.",
            },
        )

    provider_image_url, provider_image_err = _prepare_provider_image_url(source_image_url)
    if provider_image_err or not provider_image_url:
        err_text = provider_image_err or "provider_image_prepare_failed"
        is_local_error = "localhost" in err_text
        return JSONResponse(
            status_code=400 if is_local_error else 500,
            content={
                "ok": False,
                "code": "KIE_LOCAL_IMAGE_READ_FAILED" if is_local_error else "KIE_UPLOAD_FAILED",
                "hint": "provider_requires_public_or_uploaded_asset" if is_local_error else "provider_file_upload_error",
                "details": err_text,
            },
        )

    provider_start_image_url = ""
    provider_end_image_url = ""
    if mode == "continuous":
        start_source_image_url = str(start_image_url or "").strip()
        end_source_image_url = str(end_image_url or "").strip()

        if not start_source_image_url:
            start_source_image_url = str(image_url or "").strip()
            print("[CLIP VIDEO] continuous_start_fallback=image_url")
        if not end_source_image_url:
            end_source_image_url = str(image_url or "").strip()
            print("[CLIP VIDEO] continuous_end_fallback=image_url")

        start_source_image_url = _normalize_source_image_url_for_kie(start_source_image_url)
        end_source_image_url = _normalize_source_image_url_for_kie(end_source_image_url)

        provider_start_image_url, provider_start_err = _prepare_provider_image_url(start_source_image_url)
        provider_end_image_url, provider_end_err = _prepare_provider_image_url(end_source_image_url)

        if provider_start_err or provider_end_err or not provider_start_image_url or not provider_end_image_url:
            details = "; ".join([
                f"start={provider_start_err or ('missing' if not provider_start_image_url else 'ok')}",
                f"end={provider_end_err or ('missing' if not provider_end_image_url else 'ok')}",
            ])
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "code": "KIE_CONTINUOUS_IMAGE_PREP_FAILED",
                    "hint": "provider_requires_uploaded_or_public_images",
                    "details": details,
                },
            )

    if (mode == "lipsync" or render_mode == "avatar_lipsync") and not audio_slice_url:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "code": "LIPSYNC_AUDIO_REQUIRED",
                "hint": "audioSliceUrl_required_for_avatar_lipsync",
                "details": "LipSync scenes require audioSliceUrl for avatar generation.",
            },
        )

    provider_audio_url = audio_slice_url
    if mode == "lipsync":
        is_public_audio_url = _is_public_media_url(audio_slice_url)
        print(f"[CLIP LIPSYNC AUDIO] source_audio_url={audio_slice_url}")
        print(f"[CLIP LIPSYNC AUDIO] is_public_audio_url={is_public_audio_url}")

        provider_audio_url, provider_audio_err = _prepare_provider_audio_url(audio_slice_url)
        if provider_audio_err or not provider_audio_url:
            err_text = provider_audio_err or "provider_audio_prepare_failed"
            print(f"[CLIP LIPSYNC AUDIO] audio_prepare_error={err_text}")
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "code": "LIPSYNC_AUDIO_PREP_FAILED",
                    "hint": "provider_requires_public_audio_url",
                    "details": err_text,
                },
            )

        print(f"[CLIP LIPSYNC AUDIO] provider_audio_url={provider_audio_url}")

    if mode == "lipsync":
        selected_model = str(settings.PIAPI_OMNIHUMAN_TASK or "omni-human-1.5").strip() or "omni-human-1.5"
    elif mode == "continuous":
        selected_model = (settings.KIE_VIDEO_MODEL_CONTINUOUS or "").strip()
    else:
        selected_model = (settings.KIE_VIDEO_MODEL_SINGLE or "").strip()

    if mode == "continuous" and not selected_model:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "code": "KIE_CONTINUOUS_MODEL_UNVERIFIED",
                "hint": "provider_model_format_unknown",
                "details": "Set KIE_VIDEO_MODEL_CONTINUOUS to a provider-verified model string for continuous mode.",
            },
        )

    if mode != "lipsync" and not selected_model:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "code": "KIE_MODEL_NOT_CONFIGURED",
                "hint": "video_model_is_empty",
                "details": f"No model configured for mode={mode}.",
            },
        )

    print(f"[CLIP VIDEO ROUTING] selected_model={selected_model}")

    try:
        requested_duration = float(payload.requestedDurationSec or 5)
    except Exception:
        requested_duration = 5.0
    if mode == "lipsync":
        requested_duration = max(3, min(5, requested_duration))
    else:
        requested_duration = max(1, min(10, requested_duration))
    provider_duration = "5" if requested_duration <= 5.0 else "10"
    provider_duration_sec = int(provider_duration)

    send_audio_to_provider = mode == "lipsync"

    print(f"[CLIP VIDEO] mode={mode}")
    print(f"[CLIP VIDEO] selected_model={selected_model}")
    print(f"[CLIP VIDEO] requested_duration_sec={requested_duration}")
    print(f"[CLIP VIDEO] provider_duration_sec={provider_duration_sec}")
    print(f"[CLIP VIDEO] duration={provider_duration}")
    print(f"[CLIP VIDEO] format={output_format}")
    print(f"[CLIP VIDEO] image_url={image_url}")
    print(f"[CLIP VIDEO] start_image_url={start_image_url}")
    print(f"[CLIP VIDEO] end_image_url={end_image_url}")
    if mode == "continuous":
        print(f"[CLIP VIDEO] provider_start_image_url={provider_start_image_url}")
        print(f"[CLIP VIDEO] provider_end_image_url={provider_end_image_url}")
    print(f"[CLIP VIDEO] transition_action_prompt={str(payload.transitionActionPrompt or '').strip()[:300]}")
    print(f"[CLIP VIDEO] video_prompt={str(payload.videoPrompt or '').strip()[:300]}")
    print(f"[CLIP VIDEO] has_audio_slice={bool(audio_slice_url)}")
    print(f"[CLIP VIDEO] sending_audio_to_provider={send_audio_to_provider}")
    if mode == "lipsync":
        print(f"[CLIP VIDEO] provider_audio_url={provider_audio_url}")

    if mode == "lipsync":
        task_id, create_err = _piapi_create_omnihuman_task(
            image_url=provider_image_url,
            audio_url=provider_audio_url,
            prompt=effective_prompt,
        )
        if create_err or not task_id:
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "code": "PIAPI_CREATE_TASK_FAILED",
                    "hint": "provider_create_task_error",
                    "details": create_err or "create_task_failed",
                },
            )

        print("[PIAPI] task created:", task_id)
        poll_interval_sec = max(1, int(settings.PIAPI_POLL_INTERVAL_SEC or 5))
        poll_timeout_sec = max(10, int(settings.PIAPI_POLL_TIMEOUT_SEC or 300))
        video_url, wait_code, wait_hint = _piapi_wait_for_omnihuman_video(
            task_id,
            poll_interval_sec=poll_interval_sec,
            poll_timeout_sec=poll_timeout_sec,
        )
        if wait_code or not video_url:
            print("[PIAPI] error:", wait_code, wait_hint)
            status_code = {
                "PIAPI_TASK_TIMEOUT": 504,
                "PIAPI_RESULT_MISSING": 500,
                "PIAPI_TASK_FAILED": 500,
            }.get(wait_code or "", 500)
            return JSONResponse(
                status_code=status_code,
                content={
                    "ok": False,
                    "code": wait_code or "PIAPI_TASK_FAILED",
                    "hint": wait_hint or "video_generation_failed",
                    "details": "PIAPI OmniHuman task did not return a playable video URL.",
                },
            )
        print("[PIAPI] video url:", video_url)
    else:
        task_id, create_err = _kie_create_video_task(
            model=selected_model,
            image_url=provider_image_url,
            start_image_url=provider_start_image_url,
            end_image_url=provider_end_image_url,
            prompt=effective_prompt,
            duration=provider_duration,
            audio_url=provider_audio_url,
            send_audio=send_audio_to_provider,
            aspect_ratio=output_format,
            mode=mode,
        )
        if create_err or not task_id:
            return JSONResponse(
                status_code=500,
                content={
                    "ok": False,
                    "code": "KIE_CREATE_TASK_FAILED",
                    "hint": "provider_create_task_error",
                    "details": create_err or "create_task_failed",
                },
            )

        print("[KIE] task created:", task_id)
        poll_interval_sec = max(1, int(settings.KIE_POLL_INTERVAL_SEC or 5))
        poll_timeout_sec = max(10, int(settings.KIE_POLL_TIMEOUT_SEC or 300))
        video_url, wait_code, wait_hint = _kie_wait_for_video_result(
            task_id,
            poll_interval_sec=poll_interval_sec,
            poll_timeout_sec=poll_timeout_sec,
        )
        if wait_code or not video_url:
            print("[KIE] error:", wait_code, wait_hint)
            status_code = {
                "KIE_TASK_TIMEOUT": 504,
                "KIE_RESULT_MISSING": 500,
                "KIE_TASK_FAILED": 500,
            }.get(wait_code or "", 500)
            return JSONResponse(
                status_code=status_code,
                content={
                    "ok": False,
                    "code": wait_code or "KIE_TASK_FAILED",
                    "hint": wait_hint or "video_generation_failed",
                    "details": "KIE task did not return a playable video URL.",
                },
            )
        print("[KIE] video url:", video_url)

    return {
        "ok": True,
        "sceneId": scene_id,
        "videoUrl": video_url,
        "provider": "piapi" if mode == "lipsync" else "kie",
        "model": selected_model,
        "taskId": task_id,
        "mode": mode,
        "requestedDurationSec": round(requested_duration, 3),
        "providerDurationSec": provider_duration_sec,
    }
