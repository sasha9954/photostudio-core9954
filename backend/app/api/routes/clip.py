from fastapi import APIRouter
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
import requests
from requests import RequestException
import tempfile
import subprocess
import math
import logging
import base64
import json
import re
import unicodedata
import os
import io
import mimetypes
import time
import threading
from urllib.parse import urlparse
from uuid import uuid4
from typing import Any

from PIL import Image, ImageColor, ImageDraw, ImageFont

from app.core.config import settings
from app.core.static_paths import ASSETS_DIR, ensure_static_dirs, asset_url
from app.engine.video_engine import _download_image_from_source
from app.engine.gemini_rest import post_generate_content
from app.engine.comfy_reference_profile import build_reference_profiles, resolve_reference_role_type, summarize_profiles
from app.engine.comfy_remote import run_comfy_image_to_video
from app.engine.audio_analyzer import analyze_audio
from app.engine.prompt_layers import build_clip_video_motion_prompt, build_physics_first_image_blocks

router = APIRouter()
logger = logging.getLogger(__name__)

COMFY_REF_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"]
COMFY_ACTIVE_DIRECTIVES = {"hero", "supporting", "environment_required", "required"}
COMFY_FALLBACK_ROLE_PRIORITY = ["character_1", "character_2", "character_3", "group", "animal", "location", "props", "style"]
COMFY_CAST_ROLES = {"character_1", "character_2", "character_3", "animal", "group"}
COMFY_WORLD_ANCHOR_ROLES = {"location", "style"}
GROUP_NARRATIVE_REQUIRED_HINTS = {"protest", "riot", "mob", "audience", "chorus", "crowd chant", "mass panic", "митинг", "бунт", "толпа", "массов", "хор"}

class ClipImageIn(BaseModel):
    sceneId: str
    prompt: str | None = None
    sceneDelta: str | None = None
    style: str | None = "default"
    width: int | None = 1024
    height: int | None = 1024
    refs: "ClipImageRefsIn | None" = None
    sceneText: str | None = None
    promptDebug: dict | None = None


class ClipImageRefsIn(BaseModel):
    character: list[str] = Field(default_factory=list)
    location: list[str] = Field(default_factory=list)
    style: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    refsByRole: dict | None = None
    connectedInputs: dict | None = None
    text: str | None = None
    audioUrl: str | None = None
    mode: str | None = None
    stylePreset: str | None = None
    sceneId: str | None = None
    sceneGoal: str | None = None
    sceneNarrativeStep: str | None = None
    continuity: str | None = None
    refsUsed: list[str] | dict | None = None
    refDirectives: dict | None = None
    primaryRole: str | None = None
    secondaryRoles: list[str] | None = None
    sceneActiveRoles: list[str] | None = None
    refsUsedByRole: dict | None = None
    participants: list[str] | None = None
    plannerMeta: dict | None = None
    propAnchorLabel: str | None = None
    sessionCharacterAnchor: str | None = None
    sessionLocationAnchor: str | None = None
    sessionStyleAnchor: str | None = None
    sessionBaseline: dict | None = None
    worldScaleContext: str | None = None
    entityScaleAnchors: dict | None = None
    previousContinuityMemory: dict | None = None
    previousSceneImageUrl: str | None = None
    heroEntityId: str | None = None
    supportEntityIds: list[str] | None = None
    mustAppear: list[str] | None = None
    mustNotAppear: list[str] | None = None
    environmentLock: bool | None = None
    styleLock: bool | None = None
    identityLock: bool | None = None
    promptSource: str | None = None
    referenceProfiles: dict | None = None
    directorGenreIntent: str | None = None
    directorGenreReason: str | None = None
    directorToneBias: str | None = None
    duetLockEnabled: bool | None = None
    duetIdentityContract: str | None = None


class AudioSliceIn(BaseModel):
    sceneId: str
    audioUrl: str
    startSec: float | None = None
    endSec: float | None = None
    t0: float | None = None
    t1: float | None = None
    audioStoryMode: str | None = None


class ClipVideoIn(BaseModel):
    sceneId: str
    imageUrl: str | None = None
    videoPrompt: str | None = None
    requestedDurationSec: int | float | None = 5
    transitionType: str | None = "single"
    startImageUrl: str | None = None
    endImageUrl: str | None = None
    transitionActionPrompt: str | None = None
    sceneHumanVisualAnchors: list[str] | None = None
    format: str | None = "9:16"
    lipSync: bool | None = False
    renderMode: str | None = None
    sceneType: str | None = None
    shotType: str | None = None
    audioSliceUrl: str | None = None
    provider: str | None = None
    ltxMode: str | None = None
    imageStrategy: str | None = None
    resolvedWorkflowKey: str | None = None
    resolvedModelKey: str | None = None
    workflowFileOverride: str | None = None
    modelFileOverride: str | None = None
    continuation: bool | None = None
    continuationFromPrevious: bool | None = None
    continuationSourceSceneId: str | None = None
    continuationSourceAssetUrl: str | None = None
    continuationSourceAssetType: str | None = None
    requiresTwoFrames: bool | None = None
    requiresContinuation: bool | None = None
    requiresAudioSensitiveVideo: bool | None = None
    sceneContract: dict | None = None
    sceneActiveRoles: list[str] | None = None
    duetLockEnabled: bool | None = None
    duetIdentityContract: str | None = None
    directorGenreIntent: str | None = None


class AssembleSceneIn(BaseModel):
    sceneId: str | None = None
    videoUrl: str
    requestedDurationSec: int | float | None = None
    providerDurationSec: int | float | None = None
    mode: str | None = None
    model: str | None = None


class AssembleIntroIn(BaseModel):
    nodeId: str | None = None
    title: str | None = None
    autoTitle: bool | None = True
    stylePreset: str | None = "cinematic_dark"
    durationSec: int | float | None = 2.5
    imageUrl: str | None = None


class IntroGenerateIn(BaseModel):
    title: str | None = None
    manualTitleRaw: str | None = None
    autoTitle: bool | None = True
    stylePreset: str | None = "cinematic_dark"
    previewFormat: str | None = "16:9"
    durationSec: int | float | None = 2.5
    storyContext: str | None = None
    titleContext: str | None = None
    sceneCount: int | None = 0
    sourceNodeTypes: list[str] = Field(default_factory=list)
    connectedRefsByRole: dict | None = None
    roleAwareCastSummary: str | None = None
    heroParticipants: list[str] = Field(default_factory=list)
    supportingParticipants: list[str] = Field(default_factory=list)
    importantProps: list[str] = Field(default_factory=list)
    worldContext: str | None = None
    styleContext: str | None = None
    introMustAppear: list[str] = Field(default_factory=list)
    introMustNotAppear: list[str] = Field(default_factory=list)
    connectedGenderLocksByRole: dict | None = None
    connectedSpeciesLocksByRole: dict | None = None
    storySummary: str | None = None
    previewPrompt: str | None = None
    world: str | None = None
    roles: list[str] = Field(default_factory=list)
    toneStyleDirection: str | None = None


class AssembleClipIn(BaseModel):
    audioUrl: str | None = None
    format: str | None = "9:16"
    scenes: list[AssembleSceneIn]
    intro: AssembleIntroIn | None = None


CLIP_ASSEMBLE_JOBS: dict[str, dict] = {}
CLIP_ASSEMBLE_JOBS_LOCK = threading.Lock()

CLIP_VIDEO_JOBS: dict[str, dict] = {}
CLIP_VIDEO_JOBS_LOCK = threading.Lock()


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
        video_url = payload.get("video_url")
        if isinstance(video_url, str) and video_url.startswith("http"):
            return video_url

        output = payload.get("output")
        if isinstance(output, dict):
            url = output.get("video_url") or output.get("url") or output.get("video")
            if isinstance(url, str) and url.startswith("http"):
                return url

        if isinstance(output, str) and output.startswith("http"):
            return output

        data = payload.get("data")
        if isinstance(data, dict):
            nested = _extract_video_url_from_kie_payload(data)
            if nested:
                return nested

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


LTX_WORKFLOW_KEY_TO_FILE = {
    "i2v": "image-video.json",
    "f_l": "imag-imag-video-bz.json",
    "continuation": "image-video.json",
    "lip_sync": "image-lipsink-video-music.json",
}
LTX_WORKFLOW_FILE_TO_KEY = {
    "image-video.json": "i2v",
    "image-video-golos-zvuk.json": "i2v",
    "imag-imag-video-bz.json": "f_l",
    "imag-imag-video-zvuk.json": "f_l",
    "image-lipsink-video-music.json": "lip_sync",
}

LTX_SINGLE_IMAGE_WORKFLOW_KEYS = {"i2v", "lip_sync"}
LTX_FIRST_LAST_WORKFLOW_KEYS = {"f_l"}
LTX_CONTINUATION_WORKFLOW_KEYS = {"continuation"}
LTX_LEGACY_WORKFLOW_ALIASES = {"i2v_as": "i2v", "f_l_as": "f_l"}

LTX_MODE_TO_WORKFLOW_KEY = {
    "i2v": "i2v",
    # legacy input alias (do not treat as production key)
    "i2v_as": "i2v",
    "f_l": "f_l",
    # legacy input alias (do not treat as production key)
    "f_l_as": "f_l",
    "continuation": "continuation",
    "lip_sync": "lip_sync",
}
LTX_MODEL_KEY_TO_MODEL_SPEC = {
    "ltx23_dev_fp8": {
        "key": "ltx23_dev_fp8",
        "ckpt_name": "ltx-2.3-22b-dev-fp8.safetensors",
        "compatible_workflow_keys": {"i2v", "f_l"},
    },
    "ltx23_distilled_fp8": {
        "key": "ltx23_distilled_fp8",
        "ckpt_name": "ltx-2.3-22b-distilled-fp8.safetensors",
        "compatible_workflow_keys": {"i2v", "f_l"},
    },
    "ltx23_dev_fp16": {
        "key": "ltx23_dev_fp16",
        "ckpt_name": "ltx-2.3-22b-dev-fp16.safetensors",
        "compatible_workflow_keys": {"i2v", "f_l"},
    },
    "ltx23_distilled_fp16": {
        "key": "ltx23_distilled_fp16",
        "ckpt_name": "ltx-2.3-22b-distilled-fp16.safetensors",
        "compatible_workflow_keys": {"i2v", "f_l"},
    },
    "ltx23_13b_dev_fp8": {
        "key": "ltx23_13b_dev_fp8",
        "ckpt_name": "ltx-2.3-13b-dev-fp8.safetensors",
        "compatible_workflow_keys": {"i2v", "f_l"},
    },
    "ltx23_13b_distilled_fp8": {
        "key": "ltx23_13b_distilled_fp8",
        "ckpt_name": "ltx-2.3-13b-distilled-fp8.safetensors",
        "compatible_workflow_keys": {"i2v", "f_l"},
    },
}
LTX_WORKFLOW_KEY_DEFAULT_MODEL_KEY = {
    "i2v": "ltx23_dev_fp8",
    "lip_sync": "ltx23_dev_fp8",
    "f_l": "ltx23_distilled_fp8",
}


def _normalize_ltx_workflow_key(candidate: str | None) -> str:
    raw = str(candidate or "").strip().lower()
    if not raw:
        return ""
    if raw in LTX_LEGACY_WORKFLOW_ALIASES:
        return LTX_LEGACY_WORKFLOW_ALIASES[raw]
    if raw in LTX_WORKFLOW_KEY_TO_FILE:
        return LTX_LEGACY_WORKFLOW_ALIASES.get(raw, raw)
    if raw in LTX_WORKFLOW_FILE_TO_KEY:
        return LTX_LEGACY_WORKFLOW_ALIASES.get(LTX_WORKFLOW_FILE_TO_KEY[raw], LTX_WORKFLOW_FILE_TO_KEY[raw])
    return ""


def _resolve_ltx_workflow_selection(
    *,
    payload_workflow_key: str,
    ltx_mode: str,
    render_mode: str,
    is_lipsync: bool,
    transition_type: str,
    start_image_url: str,
    end_image_url: str,
) -> tuple[str, str, str, str]:
    normalized_payload_key = _normalize_ltx_workflow_key(payload_workflow_key)
    normalized_ltx_mode = str(ltx_mode or "").strip().lower()
    fallback_workflow_key = ""
    source = "default"

    if normalized_ltx_mode in LTX_MODE_TO_WORKFLOW_KEY:
        fallback_workflow_key = LTX_MODE_TO_WORKFLOW_KEY[normalized_ltx_mode]
        source = "ltx_mode"
    else:
        is_continuous = _is_clip_video_transition_mode(transition_type, start_image_url, end_image_url)
        if is_lipsync or render_mode == "avatar_lipsync":
            fallback_workflow_key = "lip_sync"
            source = "legacy_render_mode"
        elif is_continuous:
            fallback_workflow_key = "f_l"
            source = "legacy_transition"
        else:
            fallback_workflow_key = "i2v"
            source = "legacy_default"

    final_workflow_key = normalized_payload_key if normalized_payload_key else fallback_workflow_key
    workflow_source = "payload" if normalized_payload_key else source
    workflow_file = LTX_WORKFLOW_KEY_TO_FILE.get(final_workflow_key) or LTX_WORKFLOW_KEY_TO_FILE["i2v"]
    workflow_path = f"app/workflows/{workflow_file}"
    return final_workflow_key, fallback_workflow_key, workflow_source, workflow_path


def _resolve_ltx_model_selection(*, payload_model_key: str, workflow_key: str) -> tuple[str, dict | None, str]:
    raw_model_key = str(payload_model_key or "").strip().lower()
    if not raw_model_key:
        raw_model_key = LTX_WORKFLOW_KEY_DEFAULT_MODEL_KEY.get(str(workflow_key or "").strip().lower(), "")
        source = "workflow_default"
    else:
        source = "payload"
    model_spec = LTX_MODEL_KEY_TO_MODEL_SPEC.get(raw_model_key)
    return raw_model_key, model_spec, source


def _resolve_model_key_from_override(model_file_override: str | None) -> str:
    override = str(model_file_override or "").strip().lower()
    if not override:
        return ""
    if override in LTX_MODEL_KEY_TO_MODEL_SPEC:
        return override
    for model_key, spec in LTX_MODEL_KEY_TO_MODEL_SPEC.items():
        if str(spec.get("ckpt_name") or "").strip().lower() == override:
            return model_key
    return ""


def _detect_scenario_asset_type(asset_url: str | None, asset_type_hint: str | None = None) -> str:
    hinted = str(asset_type_hint or "").strip().lower()
    if hinted in {"image", "frame", "video"}:
        return hinted
    normalized_url = str(asset_url or "").strip().lower()
    if not normalized_url:
        return "unknown"
    if normalized_url.endswith((".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv")):
        return "video"
    if normalized_url.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".heic", ".heif")):
        return "image"
    return "unknown"


def _validate_ltx_workflow_strategy(
    *,
    scene_id: str,
    workflow_key: str,
    image_strategy: str,
    requires_two_frames: bool,
    image_url: str,
    start_image_url: str,
    end_image_url: str,
    audio_slice_url: str,
    continuation_source_scene_id: str,
    continuation_source_asset_url: str,
    continuation_source_asset_type: str,
) -> tuple[str | None, str | None]:
    normalized_strategy = str(image_strategy or "").strip().lower()
    has_image = bool(image_url)
    has_start = bool(start_image_url)
    has_end = bool(end_image_url)
    has_audio = bool(audio_slice_url)

    print(
        "[LTX ROUTER VALIDATION] "
        f"sceneId={scene_id} "
        f"imageStrategy={normalized_strategy or 'n/a'} "
        f"requiresTwoFrames={requires_two_frames} "
        f"hasImageUrl={has_image} "
        f"hasStartImageUrl={has_start} "
        f"hasEndImageUrl={has_end} "
        f"hasAudioSliceUrl={has_audio}"
    )

    if workflow_key in LTX_FIRST_LAST_WORKFLOW_KEYS:
        if normalized_strategy and normalized_strategy != "first_last":
            return "LTX_IMAGE_STRATEGY_MISMATCH", "first_last_workflow_requires_imageStrategy_first_last"
        if not (has_start and has_end):
            return "LTX_SECOND_FRAME_REQUIRED", "startImageUrl_and_endImageUrl_required_for_first_last_workflow"
    elif workflow_key in LTX_SINGLE_IMAGE_WORKFLOW_KEYS:
        if normalized_strategy == "first_last":
            return "LTX_IMAGE_STRATEGY_MISMATCH", "single_image_workflow_not_compatible_with_first_last_strategy"
        if not (has_image or has_start or has_end):
            return "VIDEO_SOURCE_IMAGE_REQUIRED", "imageUrl_or_startImageUrl_required"

    if workflow_key == "lip_sync" and not has_audio:
        return "LTX_AUDIO_REQUIRED_FOR_LIPSYNC", "audioSliceUrl_required_for_lip_sync_workflow"

    return None, None


def _validate_continuation_source(
    *,
    continuation_source_scene_id: str,
    continuation_source_asset_url: str,
    continuation_source_asset_type: str,
) -> tuple[str | None, str | None]:
    source_url = str(continuation_source_asset_url or "").strip()
    if not source_url:
        return "LTX_CONTINUATION_SOURCE_REQUIRED", "continuation mode requires a valid continuation source asset"

    normalized_source_type = str(continuation_source_asset_type or "").strip().lower()
    if normalized_source_type == "video":
        return (
            "LTX_CONTINUATION_SOURCE_INCOMPATIBLE",
            "continuation source resolved to video asset but current continuation path requires image/frame source",
        )
    return None, None



def _resolve_clip_video_dimensions(output_format: str | None) -> tuple[int, int]:
    normalized = str(output_format or "9:16").strip() or "9:16"
    if normalized == "16:9":
        return 1280, 720
    if normalized == "1:1":
        return 1024, 1024
    return 720, 1280

def _is_clip_video_transition_mode(transition_type: str, start_image_url: str, end_image_url: str) -> bool:
    if transition_type in {"continuous", "transition"}:
        return True
    return bool(start_image_url and end_image_url)


def _is_back_view_scene_prompt(prompt: str | None) -> bool:
    normalized_prompt = str(prompt or "").strip().lower()
    if not normalized_prompt:
        return False

    back_view_markers = [
        "from behind",
        "back view",
        "walking away",
        "rear shot",
        "rear tracking shot",
        "character seen from behind",
    ]
    return any(marker in normalized_prompt for marker in back_view_markers)


def _prompt_preview(value: str | None, limit: int = 500) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw[: max(80, int(limit or 500))]


def _looks_like_human_scene(*values: Any, scene_human_visual_anchors: list[str] | None = None) -> bool:
    if isinstance(scene_human_visual_anchors, list) and any(str(item or "").strip() for item in scene_human_visual_anchors):
        return True
    normalized = " ".join(str(value or "").strip().lower() for value in values if str(value or "").strip())
    if not normalized:
        return False
    human_markers = (
        "woman",
        "women",
        "man",
        "men",
        "girl",
        "boy",
        "person",
        "people",
        "human",
        "face",
        "character",
        "couple",
        "two people",
    )
    return any(marker in normalized for marker in human_markers)


def _build_hard_identity_lock_block(*, scene_human_visual_anchors: list[str] | None = None) -> str:
    safe_anchors: list[str] = []
    for item in (scene_human_visual_anchors or []):
        raw = str(item or "").strip()
        if not raw:
            continue
        normalized = re.sub(r"^\s*(?:woman|man|girl|boy)\s*([1-3])\s*:\s*", lambda m: f"character_{m.group(1)}: ", raw, flags=re.IGNORECASE)
        safe_anchors.append(normalized)
    has_role_anchor = any(re.search(r"\bcharacter_[1-3]\b", anchor) for anchor in safe_anchors)
    lines = [
        "HARD IDENTITY LOCK (NON-NEGOTIABLE):",
        "- primary identity keys: character_1 / character_2 / character_3 (from refsByRole / sceneActiveRoles / mustAppear / refsUsed)",
        "- fallback identity keys only if role keys are unavailable: person_1 / person_2",
        "- preserve exact same subjects from source frame and connected role refs",
        "- do not replace faces",
        "- do not introduce new people",
        "- preserve hair, clothing, body proportions, and age impression",
        "- maintain exact identity continuity",
        "- motion only, no redesign",
        "- no identity drift",
        "- no face drift",
        "- no costume drift",
        "- woman/man labels may be used as weak descriptors only, never as primary identity ids",
    ]
    if not has_role_anchor:
        lines.append("- role anchors missing in scene-specific anchors: apply neutral person_1/person_2 fallback without changing identity")
    if safe_anchors:
        lines.extend([
            "",
            "SCENE-SPECIFIC HUMAN VISUAL ANCHORS (SOURCE FRAME):",
            *[f"- {anchor}" for anchor in safe_anchors[:4]],
        ])
    return "\n".join(lines).strip()


def _build_duet_hardening_block(*, active_roles: list[str] | None = None) -> str:
    roles = [str(role or "").strip() for role in (active_roles or []) if str(role or "").strip()]
    has_duet = "character_1" in roles and "character_2" in roles
    has_multi_humans = has_duet or len([role for role in roles if role.startswith("character_")]) >= 2
    if not has_multi_humans:
        return ""
    lines = [
        "DUET / MULTI-CHARACTER SEPARATION CONTRACT (HARD):",
        "- character_1 and character_2 must remain two different human identities in the same coherent shot",
        "- do not merge, average, twinize, soften, or visually converge them",
        "- preserve distinct face structure, hair identity, body silhouette, and outfit silhouette for both simultaneously",
        "- secondary character must remain visibly legible, not dissolved into background and not a softened echo",
        "- maintain readable two-person composition when duet contract is active",
    ]
    if has_duet:
        lines.append("- character_2 must not become a softened copy of character_1")
    return "\n".join(lines)


def _normalize_genre_intent(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"horror_dread", "tragic_social_drama", "neutral_drama"}:
        return normalized
    return ""


def _build_genre_hardening_block(*, genre_intent: str) -> str:
    intent = _normalize_genre_intent(genre_intent)
    if intent == "horror_dread":
        return "\n".join([
            "GENRE HARDENING (HORROR DREAD):",
            "- sustain escalating dread and latent menace in framing, lighting, and subject behavior",
            "- keep persistent threat presence and tangible danger cues in the environment",
            "- preserve unease, uncanny tension, and anticipatory fear rather than generic sadness",
            "- avoid reducing tone to plain gritty drama or melancholic realism",
            "- emphasize hostile ambiguity, predatory negative space, and unstable safety cues",
        ])
    if intent == "tragic_social_drama":
        return "\n".join([
            "GENRE HARDENING (TRAGIC SOCIAL DRAMA):",
            "- preserve grounded social realism with emotionally heavy consequences",
            "- emphasize systemic pressure, human vulnerability, and lived-world hardship",
            "- avoid horror menace signals, monster-like threat cues, or supernatural framing",
        ])
    if intent == "neutral_drama":
        return "\n".join([
            "GENRE HARDENING (NEUTRAL DRAMA):",
            "- keep tone observational and emotionally coherent without forced genre stylization",
            "- avoid injecting horror menace cues or excessive tragic stylization by default",
        ])
    return ""


def _resolve_genre_hardening_from_sources(
    *,
    scene_contract: dict[str, Any] | None = None,
    planner_meta: dict[str, Any] | None = None,
    direct_genre_intent: str | None = None,
) -> tuple[str, str]:
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    if _normalize_genre_intent(contract.get("directorGenreIntent")):
        return _normalize_genre_intent(contract.get("directorGenreIntent")), "scene_contract.directorGenreIntent"
    if _normalize_genre_intent(contract.get("director_genre_intent")):
        return _normalize_genre_intent(contract.get("director_genre_intent")), "scene_contract.director_genre_intent"
    meta = planner_meta if isinstance(planner_meta, dict) else {}
    if _normalize_genre_intent(meta.get("directorGenreIntent")):
        return _normalize_genre_intent(meta.get("directorGenreIntent")), "planner_meta.directorGenreIntent"
    if _normalize_genre_intent(direct_genre_intent):
        return _normalize_genre_intent(direct_genre_intent), "normalized_field.directorGenreIntent"
    return "", "none"


def _detect_duet_contract_for_video(
    *,
    scene_contract: dict[str, Any] | None = None,
    scene_active_roles: list[str] | None = None,
    duet_lock_enabled: bool | None = None,
    duet_identity_contract: str | None = None,
    anchor_roles: list[str] | None = None,
) -> tuple[bool, str, dict[str, Any]]:
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    contract_roles = [str(role or "").strip() for role in (contract.get("activeRoles") or contract.get("sceneActiveRoles") or []) if str(role or "").strip()]
    normalized_roles = [str(role or "").strip() for role in (scene_active_roles or []) if str(role or "").strip()]
    anchor = [str(role or "").strip() for role in (anchor_roles or []) if str(role or "").strip()]

    explicit_duet_lock = bool(contract.get("duetLockEnabled")) or bool(duet_lock_enabled)
    explicit_duet_identity = bool(str(contract.get("duetIdentityContract") or duet_identity_contract or "").strip())
    explicit_duet_roles = "character_1" in contract_roles and "character_2" in contract_roles
    if explicit_duet_lock or explicit_duet_identity or explicit_duet_roles:
        return True, "scene_contract", {
            "contractActiveRoles": contract_roles,
            "duetLockEnabled": explicit_duet_lock,
            "hasDuetIdentityContract": explicit_duet_identity,
        }

    normalized_duet_roles = "character_1" in normalized_roles and "character_2" in normalized_roles
    if normalized_duet_roles:
        return True, "normalized_scene_active_roles", {
            "sceneActiveRoles": normalized_roles,
        }

    anchor_duet_roles = "character_1" in anchor and "character_2" in anchor
    if anchor_duet_roles:
        return True, "anchor_regex_fallback", {
            "anchorRoles": anchor,
        }

    return False, "none", {
        "contractActiveRoles": contract_roles,
        "sceneActiveRoles": normalized_roles,
        "anchorRoles": anchor,
    }


def _compose_video_effective_prompt(
    *,
    video_prompt: str,
    transition_action_prompt: str,
    output_format: str,
    requested_duration_sec: int | float | None,
    scene_human_visual_anchors: list[str] | None = None,
    scene_type: str | None = None,
    shot_type: str | None = None,
    scene_contract: dict[str, Any] | None = None,
    scene_active_roles: list[str] | None = None,
    duet_lock_enabled: bool | None = None,
    duet_identity_contract: str | None = None,
    director_genre_intent: str | None = None,
) -> tuple[str, dict]:
    base_prompt = str(video_prompt or "").strip()
    transition_prompt = str(transition_action_prompt or "").strip()
    base_effective_prompt = build_clip_video_motion_prompt(
        base_prompt=base_prompt,
        transition_prompt=transition_prompt,
        fmt=output_format,
        seconds=requested_duration_sec,
    )
    has_humans = _looks_like_human_scene(
        base_prompt,
        transition_prompt,
        scene_type,
        shot_type,
        scene_human_visual_anchors=scene_human_visual_anchors,
    )
    identity_lock_block = _build_hard_identity_lock_block(scene_human_visual_anchors=scene_human_visual_anchors) if has_humans else ""
    anchor_roles: list[str] = []
    for anchor in (scene_human_visual_anchors or []):
        text = str(anchor or "")
        for role in re.findall(r"\bcharacter_[1-3]\b", text):
            if role not in anchor_roles:
                anchor_roles.append(role)
    duet_contract_detected, duet_hardening_source, duet_contract_preview = _detect_duet_contract_for_video(
        scene_contract=scene_contract,
        scene_active_roles=scene_active_roles,
        duet_lock_enabled=duet_lock_enabled,
        duet_identity_contract=duet_identity_contract,
        anchor_roles=anchor_roles,
    )
    duet_roles_for_hardening: list[str] = list(anchor_roles)
    if duet_contract_detected and "character_1" not in duet_roles_for_hardening:
        duet_roles_for_hardening.append("character_1")
    if duet_contract_detected and "character_2" not in duet_roles_for_hardening:
        duet_roles_for_hardening.append("character_2")
    duet_hardening_block = _build_duet_hardening_block(active_roles=duet_roles_for_hardening) if has_humans and duet_contract_detected else ""
    genre_intent, genre_source = _resolve_genre_hardening_from_sources(
        scene_contract=scene_contract,
        direct_genre_intent=director_genre_intent,
    )
    # Genre hardening must stay independent from human presence so environment-only /
    # location-only / threat-presence shots still preserve intended directing tone.
    genre_hardening_block = _build_genre_hardening_block(genre_intent=genre_intent)
    effective_prompt = "\n\n".join(
        part for part in [base_effective_prompt, identity_lock_block, genre_hardening_block, duet_hardening_block] if str(part or "").strip()
    ).strip()
    return effective_prompt, {
        "has_humans": has_humans,
        "requestedPromptPreview": _prompt_preview(base_prompt, 500),
        "effectivePromptPreview": _prompt_preview(effective_prompt, 500),
        "effectivePromptLength": len(effective_prompt),
        "videoPromptLength": len(base_prompt),
        "transitionActionPromptLength": len(transition_prompt),
        "sceneHumanVisualAnchors": [str(item or "").strip() for item in (scene_human_visual_anchors or []) if str(item or "").strip()],
        "identityLockApplied": bool(identity_lock_block),
        "genreHardeningApplied": bool(genre_hardening_block),
        "genreHardeningSource": genre_source,
        "genreHardeningPreview": _prompt_preview(genre_hardening_block, 320),
        "duetHardeningApplied": bool(duet_hardening_block),
        "duetHardeningSource": duet_hardening_source,
        "duetContractDetected": duet_contract_detected,
        "duetContractPreview": duet_contract_preview,
    }


def _infer_selected_view_hint(*values: Any) -> str:
    normalized = " ".join(str(value or "").strip().lower() for value in values if str(value or "").strip())
    if not normalized:
        return "any"

    any_markers = [
        "wide",
        "establishing",
        "occluded face",
        "hidden face",
        "face partly hidden",
    ]
    back_markers = [
        "back view",
        "from behind",
        "rear shot",
        "rear view",
        "walking away",
        "back shot",
        "rear",
        "rear-facing",
        "back-facing",
        "from the back",
        "seen from the back",
        "silhouette from behind",
        "back silhouette",
        "back profile",
        "rear tracking",
        "rear follow shot",
        "over the shoulder",
    ]
    side_markers = [
        "profile",
        "side view",
        "side shot",
        "side angle",
        "three-quarter profile",
        "3/4 profile",
        "quarter profile",
        "profile silhouette",
        "side portrait",
        "side-facing",
        "seen in profile",
        "over-shoulder",
        "over shoulder",
        "over-the-shoulder",
    ]
    detail_markers = [
        "close-up",
        "close up",
        "macro",
        "detail",
        "face detail",
        "facial detail",
        "portrait",
        "eye detail",
        "hand detail",
        "close facial crop",
        "extreme close-up",
        "extreme close up",
        "tight close-up",
        "tight close up",
    ]
    front_markers = [
        "front view",
        "frontal",
        "head-on",
        "straight-on",
        "straight on",
        "direct frontal",
        "front-facing",
        "facing camera",
    ]

    if any(token in normalized for token in any_markers):
        return "any"
    if any(token in normalized for token in back_markers):
        return "back"
    if any(token in normalized for token in side_markers):
        return "side/profile"
    if any(token in normalized for token in detail_markers):
        return "detail"
    if any(token in normalized for token in front_markers):
        return "front"
    return "any"


def _selected_view_requirement_line(selected_view_hint: str) -> str:
    normalized = str(selected_view_hint or "any").strip().lower() or "any"
    mapping = {
        "back": "Selected camera view: BACK VIEW — character must be seen from behind.",
        "side/profile": "Selected camera view: SIDE/PROFILE VIEW — character must be shown in profile.",
        "detail": "Selected camera view: DETAIL VIEW — face or specified detail must dominate the frame.",
        "front": "Selected camera view: FRONT VIEW — frontal identity must be clearly visible.",
    }
    return mapping.get(
        normalized,
        "Selected camera view: ANY — use a flexible view only when no matching reference angle exists or scene composition strictly requires it.",
    )


def _infer_reference_view_label(url: str, index: int = 0) -> str:
    normalized = str(url or "").strip().lower()
    if not normalized:
        return "unknown"
    markers = [
        ("front", ["front", "frontal", "face", "straight"]),
        ("side/profile", ["side", "profile", "3-4", "three-quarter", "three quarter"]),
        ("back", ["back", "rear", "behind", "from-behind", "from_behind"]),
        ("detail", ["detail", "closeup", "close-up", "close_up", "macro"]),
    ]
    for label, tokens in markers:
        if any(token in normalized for token in tokens):
            return label
    fallback_by_index = {0: "front", 1: "side/profile", 2: "back", 3: "detail"}
    return fallback_by_index.get(index, "unknown")


def _selected_view_fallback_order(selected_view_hint: str) -> list[str]:
    normalized = str(selected_view_hint or "any").strip().lower() or "any"
    mapping = {
        "front": ["front", "side/profile", "detail", "back", "unknown"],
        "side/profile": ["side/profile", "front", "back", "detail", "unknown"],
        "back": ["back", "side/profile", "front", "detail", "unknown"],
        "detail": ["detail", "front", "side/profile", "back", "unknown"],
        "any": ["front", "side/profile", "back", "detail", "unknown"],
    }
    return list(mapping.get(normalized, mapping["any"]))


def _resolve_selected_primary_view(available_views: list[str], selected_view_hint: str) -> tuple[str, str]:
    useful_views = [str(view or "unknown") for view in available_views if str(view or "unknown") in {"front", "side/profile", "back", "detail"}]
    if not useful_views:
        return "unknown", "unknown"

    normalized = str(selected_view_hint or "any").strip().lower() or "any"
    if normalized == "any":
        fallback_order = _selected_view_fallback_order("any")
        for view in fallback_order:
            if view in useful_views:
                return view, "any"
        return useful_views[0], "any"

    if normalized in useful_views:
        return normalized, "exact"

    fallback_order = _selected_view_fallback_order(normalized)
    for view in fallback_order:
        if view in useful_views:
            return view, "fallback"
    return useful_views[0], "fallback"


def _prioritize_role_refs_for_selected_view(
    _role: str,
    annotations: list[dict[str, Any]],
    selected_view_hint: str,
) -> tuple[list[dict[str, Any]], str, str]:
    clean_annotations = [item for item in (annotations or []) if isinstance(item, dict)]
    if not clean_annotations:
        return [], "unknown", "unknown"

    available_views = [str(item.get("view") or "unknown") for item in clean_annotations]
    primary_view, match_mode = _resolve_selected_primary_view(available_views, selected_view_hint)
    view_order = _selected_view_fallback_order(selected_view_hint)
    order_map = {view: idx for idx, view in enumerate(view_order)}

    prioritized = sorted(
        clean_annotations,
        key=lambda item: (
            order_map.get(str(item.get("view") or "unknown"), len(view_order)),
            int(item.get("originalIndex") or 0),
        ),
    )
    return prioritized, primary_view, match_mode


def _order_role_refs_for_multi_view(_role: str, urls: list[str], selected_view_hint: str = "any") -> tuple[list[str], list[dict[str, Any]], str, str]:
    clean_urls = [str(url or "").strip() for url in (urls or []) if str(url or "").strip()]
    annotated: list[dict[str, Any]] = []
    default_order = {"front": 0, "side/profile": 1, "back": 2, "detail": 3, "unknown": 4}
    for idx, url in enumerate(clean_urls):
        annotated.append({
            "url": url,
            "view": _infer_reference_view_label(url, idx),
            "originalIndex": idx,
        })

    normalized = str(selected_view_hint or "any").strip().lower() or "any"
    if normalized == "any":
        annotated.sort(key=lambda item: (default_order.get(str(item.get("view") or "unknown"), 4), int(item.get("originalIndex") or 0)))
        primary_view, match_mode = _resolve_selected_primary_view(
            [str(item.get("view") or "unknown") for item in annotated],
            normalized,
        )
    else:
        annotated, primary_view, match_mode = _prioritize_role_refs_for_selected_view(_role, annotated, normalized)
    return [str(item.get("url") or "") for item in annotated], annotated, primary_view, match_mode


def _build_role_type_by_role(
    active_roles: list[str] | None,
    reference_profiles: dict[str, Any] | None = None,
) -> dict[str, str]:
    profiles = reference_profiles if isinstance(reference_profiles, dict) else {}
    role_types: dict[str, str] = {}
    for role in [str(role or "").strip() for role in (active_roles or []) if str(role or "").strip() in COMFY_REF_ROLES]:
        profile = profiles.get(role) if isinstance(profiles.get(role), dict) else {}
        role_types[role] = resolve_reference_role_type(role, [{"roleType": profile.get("roleType")}])
    return role_types


def _build_multi_view_role_context(
    refs_by_role: dict[str, list[str]] | None,
    active_roles: list[str] | None,
    selected_view_hint: str,
    role_type_by_role: dict[str, str] | None = None,
) -> tuple[dict[str, list[str]], dict[str, int], dict[str, dict[str, Any]], list[str], dict[str, str], dict[str, str]]:
    raw_refs = refs_by_role if isinstance(refs_by_role, dict) else {}
    roles = [str(role or "").strip() for role in (active_roles or []) if str(role or "").strip() in COMFY_REF_ROLES]
    ordered_refs: dict[str, list[str]] = {role: list(raw_refs.get(role) or []) for role in COMFY_REF_ROLES}
    multi_view_count_by_role: dict[str, int] = {}
    reference_profile: dict[str, dict[str, Any]] = {}
    structured_lines: list[str] = []
    selected_primary_view_by_role: dict[str, str] = {}
    selected_view_match_mode_by_role: dict[str, str] = {}
    semantic_role_types = role_type_by_role if isinstance(role_type_by_role, dict) else {}

    for role in roles:
        ordered_urls, annotations, primary_view, match_mode = _order_role_refs_for_multi_view(role, raw_refs.get(role) or [], selected_view_hint)
        ordered_refs[role] = ordered_urls
        if not ordered_urls:
            continue
        role_views = [str(item.get("view") or "unknown") for item in annotations]
        multi_view_count_by_role[role] = len(ordered_urls)
        selected_primary_view_by_role[role] = primary_view
        selected_view_match_mode_by_role[role] = match_mode
        reference_profile[role] = {
            "views": role_views,
            "identity_locked": True,
            "roleType": semantic_role_types.get(role, "unknown"),
            "selected_view_hint": selected_view_hint,
            "selected_primary_view": primary_view,
            "selected_view_match_mode": match_mode,
        }
        structured_lines.append(f"{role} reference set:")
        structured_lines.extend([
            f"- image_{idx + 1}: {view}"
            for idx, view in enumerate(role_views)
        ])
        structured_lines.append("Use these as a unified identity reference set.")

    return (
        ordered_refs,
        multi_view_count_by_role,
        reference_profile,
        structured_lines,
        selected_primary_view_by_role,
        selected_view_match_mode_by_role,
    )


def _is_face_too_small_for_lipsync(prompt: str | None) -> bool:
    normalized_prompt = str(prompt or "").strip().lower()
    if not normalized_prompt:
        return False

    distance_markers = [
        "wide shot",
        "long shot",
        "extreme long shot",
        "figure becomes small",
        "distant figure",
        "character far away",
        "silhouette in distance",
        "tiny figure",
    ]

    return any(marker in normalized_prompt for marker in distance_markers)


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


INTRO_FRAME_STYLE_PRESETS: dict[str, dict[str, Any]] = {'youtube_shock': {'key': 'youtube_shock',
                   'label': 'YouTube Shock',
                   'shortDescription': 'High-urgency thumbnail hook with bold contrast and one obvious hero moment.',
                   'compositionBias': 'subject-dominant hero framing with aggressive readability',
                   'palette': 'warm yellow, red-orange, charcoal',
                   'mood': 'urgent, explosive, clickable',
                   'textTreatment': 'bold compact headline support, high contrast, minimal words',
                   'graphicAccentsPreference': 'glow edges, impact streaks, restrained arrows only if needed',
                   'overlays': 'tight glow plates and punchy light sweeps',
                   'promptFragment': 'premium high-energy thumbnail with immediate stop-scroll impact, one dominant '
                                     'hero subject, bold contrast, and clean clickable urgency',
                   'promptRules': ['immediate thumbnail readability and one dominant focal point',
                                   'high-energy premium hook without spammy clickbait',
                                   'subject stays clear while accents stay secondary'],
                   'negativeRules': ['no cheap meme trash', 'no fake subscriber UI', 'no overcrowded collage'],
                   'accent': '#ffcf5c',
                   'secondary': '#ff6b3d',
                   'uiHint': 'Shock hook'},
 'reaction_result': {'key': 'reaction_result',
                     'label': 'Reaction Result',
                     'shortDescription': 'Outcome-first composition pairing a readable reaction with the revealed '
                                         'result.',
                     'compositionBias': 'split emphasis between hero reaction and revealed outcome',
                     'palette': 'gold, coral, deep navy',
                     'mood': 'surprised, satisfying, payoff-driven',
                     'textTreatment': 'short payoff phrase, supportive not dominant',
                     'graphicAccentsPreference': 'light callout accents, comparison framing, controlled glow',
                     'overlays': 'soft result halos and subtle directional emphasis',
                     'promptFragment': 'premium thumbnail where the emotional reaction and the revealed outcome are '
                                       'instantly understandable and tightly linked',
                     'promptRules': ['result is instantly understandable',
                                     'reaction and outcome stay tightly linked',
                                     'premium payoff energy without clutter'],
                     'negativeRules': ['no meme-face distortion', 'no noisy badges', 'no cluttered comparison spam'],
                     'accent': '#ffb86c',
                     'secondary': '#ff6f91',
                     'uiHint': 'Payoff frame'},
 'breaking_alert': {'key': 'breaking_alert',
                    'label': 'Breaking Alert',
                    'shortDescription': 'News-flash urgency with crisp hierarchy and alert-style lighting accents.',
                    'compositionBias': 'headline-supporting alert composition with urgent subject focus',
                    'palette': 'red, amber, dark steel',
                    'mood': 'urgent, alarming, immediate',
                    'textTreatment': 'compact alert headline plate with strong contrast',
                    'graphicAccentsPreference': 'signal bars, alert glows, restrained warning lines',
                    'overlays': 'broadcast streaks and emergency-light bloom',
                    'promptFragment': 'premium breaking-alert thumbnail with immediate urgency, crisp hierarchy, '
                                      'signal-style lighting, and strong readability',
                    'promptRules': ['premium breaking alert tone',
                                    'urgent but clean hierarchy',
                                    'thumbnail readability first'],
                    'negativeRules': ['no tabloid chaos',
                                      'no fake ticker spam',
                                      'no alert graphics hiding the subject'],
                    'accent': '#ff5a5f',
                    'secondary': '#ffd166',
                    'uiHint': 'Alert mode'},
 'tutorial_clickable': {'key': 'tutorial_clickable',
                        'label': 'Tutorial Clickable',
                        'shortDescription': 'Clean instructional thumbnail built around clarity, guidance, and one '
                                            'teachable focal point.',
                        'compositionBias': 'demonstration-first layout with readable subject/object steps',
                        'palette': 'cyan, blue, white, graphite',
                        'mood': 'clear, confident, helpful',
                        'textTreatment': 'short informative text with clean spacing and no shouting',
                        'graphicAccentsPreference': 'simple pointers, guide frames, minimal progress cues',
                        'overlays': 'soft UI emphasis bars and controlled highlights',
                        'promptFragment': 'clean premium tutorial thumbnail with obvious instructional focus, one '
                                          'teachable focal point, and strong small-size readability',
                        'promptRules': ['clarity and teachability first',
                                        'guidance accents stay minimal',
                                        'approachable premium look'],
                        'negativeRules': ['no arrow overload',
                                          'no fake software UI spam',
                                          'no confusing multi-step collage'],
                        'accent': '#5dd6ff',
                        'secondary': '#7a8cff',
                        'uiHint': 'How-to clarity'},
 'big_object_focus': {'key': 'big_object_focus',
                      'label': 'Big Object Focus',
                      'shortDescription': 'Hero-object composition where scale, shape, and readability of the main '
                                          'item dominate.',
                      'compositionBias': 'large object hero framing with supportive text and accents',
                      'palette': 'electric blue, orange spark, dark slate',
                      'mood': 'impressive, punchy, object-centric',
                      'textTreatment': 'minimal bold support near edges, never over the object core',
                      'graphicAccentsPreference': 'scale cues, rim glow, impact flares',
                      'overlays': 'light bloom and object-edge emphasis',
                      'promptFragment': 'iconic oversized hero object thumbnail where the item feels large, clear, '
                                        'readable, and instantly legible in one glance',
                      'promptRules': ['main object feels iconic and large',
                                      'lighting celebrates scale',
                                      'design stays secondary to the object'],
                      'negativeRules': ['no clutter weakening the object silhouette',
                                        'no decorative text covering the main item',
                                        'no busy collage'],
                      'accent': '#55c7ff',
                      'secondary': '#ff9f43',
                      'uiHint': 'Object hero'},
 'cyber_neon': {'key': 'cyber_neon',
                'label': 'Cyber Neon',
                'shortDescription': 'Dark futuristic scene with premium cyan-magenta neon and clean subject '
                                    'separation.',
                'compositionBias': 'moody cyber subject framing with illuminated edge contrast',
                'palette': 'cyan, magenta, violet, ink black',
                'mood': 'charged, futuristic, immersive',
                'textTreatment': 'sleek luminous type support with restrained density',
                'graphicAccentsPreference': 'neon rims, holographic lines, signal streaks',
                'overlays': 'selective glow fog and cyber light trails',
                'promptFragment': 'premium cyber-neon thumbnail with controlled glow, moody futuristic depth, clean '
                                  'subject separation, and preserved focal clarity',
                'promptRules': ['controlled neon accents',
                                'subject readability survives the glow',
                                'immersive but premium'],
                'negativeRules': ['no acid rainbow overload',
                                  'no unreadable overglow',
                                  'no chaotic cheap cyber clutter'],
                'accent': '#6ef2ff',
                'secondary': '#b86dff',
                'uiHint': 'Cyber glow'},
 'ai_tech_explainer': {'key': 'ai_tech_explainer',
                       'label': 'AI Tech Explainer',
                       'shortDescription': 'Smart editorial tech look blending innovation cues with clear explanatory '
                                           'hierarchy.',
                       'compositionBias': 'explanatory hero focus with tech-context support',
                       'palette': 'teal, icy blue, white, deep navy',
                       'mood': 'intelligent, innovative, trustworthy',
                       'textTreatment': 'clean modern text support, medium weight, no forced shouting',
                       'graphicAccentsPreference': 'data halos, schematic lines, subtle interface cues',
                       'overlays': 'soft grid light and analytical glow panels',
                       'promptFragment': 'premium AI-tech explainer thumbnail with smart clarity, modern innovation '
                                         'cues, and readable explanatory hierarchy',
                       'promptRules': ['innovation + explanation at a glance',
                                       'tasteful tech signals only',
                                       'trustworthy modern polish'],
                       'negativeRules': ['no cheesy robot clichés',
                                         'no fake dashboards covering the core subject',
                                         'no overloaded charts'],
                       'accent': '#7cf7e6',
                       'secondary': '#70a1ff',
                       'uiHint': 'Explainer'},
 'futuristic_ui': {'key': 'futuristic_ui',
                   'label': 'Futuristic UI',
                   'shortDescription': 'Interface-inspired future aesthetic with strong geometry and premium HUD '
                                       'restraint.',
                   'compositionBias': 'style-forward geometry supporting a central hero focus',
                   'palette': 'cyan, indigo, silver, obsidian',
                   'mood': 'precise, advanced, sleek',
                   'textTreatment': 'thin-to-medium sci-fi supportive text, tightly controlled',
                   'graphicAccentsPreference': 'HUD frames, scans, circular guides, glass panels',
                   'overlays': 'clean holographic overlays and interface glints',
                   'promptFragment': 'sleek futuristic interface-inspired thumbnail with premium HUD restraint, '
                                     'geometric polish, and a strong central focal hierarchy',
                   'promptRules': ['interface geometry as design language, not clutter',
                                   'strong central readability',
                                   'advanced premium feel'],
                   'negativeRules': ['no dense dashboard clutter',
                                     'no unreadable micro-elements',
                                     'no full-screen fake UI takeover'],
                   'accent': '#7af0ff',
                   'secondary': '#8f7cff',
                   'uiHint': 'HUD polish'},
 'glitch_signal': {'key': 'glitch_signal',
                   'label': 'Glitch Signal',
                   'shortDescription': 'Controlled signal corruption aesthetic with deliberate disruption and readable '
                                       'focal anchor.',
                   'compositionBias': 'focal anchor first, glitch treatment second',
                   'palette': 'mint green, hot magenta, deep navy',
                   'mood': 'unstable, digital, tense',
                   'textTreatment': 'short crisp text support with occasional digital texture',
                   'graphicAccentsPreference': 'scanlines, signal tears, channel splits',
                   'overlays': 'selective glitch bands and digital breakup',
                   'promptFragment': 'controlled glitch-signal thumbnail where digital distortion feels intentional, '
                                     'premium, and never destroys the main focal anchor',
                   'promptRules': ['intentional digital disruption',
                                   'hero remains readable',
                                   'sharp contemporary finish'],
                   'negativeRules': ['no full-frame corruption', 'no broken-image sludge', 'no unreadable visual mess'],
                   'accent': '#8eff8c',
                   'secondary': '#ff6ef2',
                   'uiHint': 'Signal break'},
 'dark_system': {'key': 'dark_system',
                 'label': 'Dark System',
                 'shortDescription': 'Cold shadow-heavy system aesthetic with disciplined contrast and ominous '
                                     'structure.',
                 'compositionBias': 'low-key structure with disciplined focal isolation',
                 'palette': 'graphite, steel blue, muted red',
                 'mood': 'controlled, severe, ominous',
                 'textTreatment': 'stark minimal support with industrial contrast',
                 'graphicAccentsPreference': 'system bars, hard-edge glows, sparse diagnostics',
                 'overlays': 'shadow gradients and subtle machine-light strips',
                 'promptFragment': 'dark system-grade thumbnail with cold disciplined contrast, ominous atmosphere, '
                                   'and precise focal isolation',
                 'promptRules': ['cold disciplined darkness', 'focal isolation via structure', 'ominous but premium'],
                 'negativeRules': ['no muddy darkness', 'no chaotic cyber clutter', 'no noisy red alerts everywhere'],
                 'accent': '#8da2c9',
                 'secondary': '#ff6b6b',
                 'uiHint': 'System dark'},
 'cinematic_dark': {'key': 'cinematic_dark',
                    'label': 'Cinematic Dark',
                    'shortDescription': 'Premium film-poster darkness with dramatic light shaping and story-driven '
                                        'weight.',
                    'compositionBias': 'cinematic subject hierarchy with dramatic negative space',
                    'palette': 'gold ember, teal shadow, black',
                    'mood': 'serious, expensive, dramatic',
                    'textTreatment': 'elegant bold title support, restrained and premium',
                    'graphicAccentsPreference': 'light shafts, haze, subtle lens bloom',
                    'overlays': 'film-grade vignettes and dramatic glow pockets',
                    'promptFragment': 'premium dark cinematic poster frame with dramatic motivated light, intentional '
                                      'hierarchy, and expensive story-driven mood',
                    'promptRules': ['opening-film still / premium poster',
                                    'dramatic light and negative space',
                                    'story-driven expensive mood'],
                    'negativeRules': ['no cheap clickbait styling',
                                      'no fake UI, arrows, badges, or circles',
                                      'no tabloid clutter'],
                    'accent': '#f6d365',
                    'secondary': '#5ee7df',
                    'uiHint': 'Poster-grade'},
 'mystery_horror': {'key': 'mystery_horror',
                    'label': 'Mystery Horror',
                    'shortDescription': 'Suspenseful eerie frame driven by unknown threat, darkness, and controlled '
                                        'dread.',
                    'compositionBias': 'threat-aware focal composition with obscured mystery zones',
                    'palette': 'crimson, sickly amber, midnight black',
                    'mood': 'eerie, tense, unsettling',
                    'textTreatment': 'minimal ominous support, never comedic or campy',
                    'graphicAccentsPreference': 'mist, scratches, selective warning glows',
                    'overlays': 'fog, shadow bloom, distressed light leaks',
                    'promptFragment': 'eerie mystery-horror thumbnail with controlled dread, unknown threat energy, '
                                      'atmospheric darkness, and readable fear focus',
                    'promptRules': ['suspense and unknown threat',
                                    'selective highlights + shadow',
                                    'premium grounded horror'],
                    'negativeRules': ['no gore overload', 'no comedy-horror exaggeration', 'no slasher-poster clichés'],
                    'accent': '#ff6b6b',
                    'secondary': '#ffd166',
                    'uiHint': 'Dread mood'},
 'epic_fantasy': {'key': 'epic_fantasy',
                  'label': 'Epic Fantasy',
                  'shortDescription': 'Mythic adventure framing with grand scale, luminous atmosphere, and heroic '
                                      'focus.',
                  'compositionBias': 'heroic central subject with sweeping world support',
                  'palette': 'royal blue, gold, emerald, dusk purple',
                  'mood': 'mythic, aspirational, grand',
                  'textTreatment': 'ornate-but-readable support with premium fantasy restraint',
                  'graphicAccentsPreference': 'magic particles, aura rims, sweeping light arcs',
                  'overlays': 'atmospheric mist, enchanted glow, cinematic embers',
                  'promptFragment': 'epic fantasy thumbnail with mythic scale, luminous atmosphere, adventurous '
                                    'silhouette, and heroic readability',
                  'promptRules': ['grand mythic world',
                                  'iconic adventurous silhouette',
                                  'luminous atmosphere without losing clarity'],
                  'negativeRules': ['no cheap game-ad clutter', 'no muddy fantasy chaos', 'no noisy spell overload'],
                  'accent': '#ffd166',
                  'secondary': '#7bed9f',
                  'uiHint': 'Mythic hero'},
 'emotional_story': {'key': 'emotional_story',
                     'label': 'Emotional Story',
                     'shortDescription': 'Human-centered thumbnail driven by feeling, intimacy, and sincere visual '
                                         'storytelling.',
                     'compositionBias': 'face-and-feeling dominant composition with soft support',
                     'palette': 'rose, warm amber, muted blue-gray',
                     'mood': 'empathetic, sincere, intimate',
                     'textTreatment': 'short emotional support phrase, gentle but clear',
                     'graphicAccentsPreference': 'soft flares, depth haze, subtle highlights',
                     'overlays': 'warm bloom and emotional atmosphere layers',
                     'promptFragment': 'emotionally resonant thumbnail with intimate storytelling, strong human '
                                       'readability, and sincere feeling-first composition',
                     'promptRules': ['emotion first in faces and posture',
                                     'intimate not melodramatic',
                                     'softness supports clarity'],
                     'negativeRules': ['no soap-opera excess', 'no manipulative clutter', 'no text overload'],
                     'accent': '#ff9aa2',
                     'secondary': '#ffd6a5',
                     'uiHint': 'Human feeling'},
 'minimal_premium': {'key': 'minimal_premium',
                     'label': 'Minimal Premium',
                     'shortDescription': 'Refined luxury thumbnail with restrained composition, space, and premium '
                                         'finish.',
                     'compositionBias': 'style-led minimal hierarchy with elegant negative space',
                     'palette': 'ivory, soft gold, charcoal, muted taupe',
                     'mood': 'quiet, refined, premium',
                     'textTreatment': 'short elegant support, small footprint, no aggressive styling',
                     'graphicAccentsPreference': 'micro glows, fine lines, subtle gradients',
                     'overlays': 'soft vignette and polished light wash',
                     'promptFragment': 'minimal premium thumbnail with refined restraint, elegant spacing, luxury '
                                       'polish, and strong curated hierarchy',
                     'promptRules': ['restraint and elegant spacing',
                                     'minimal accents',
                                     'luxurious highly curated feel'],
                     'negativeRules': ['no loud clickbait graphics', 'no overcrowded text', 'no decorative noise'],
                     'accent': '#f4d7a1',
                     'secondary': '#dfe7fd',
                     'uiHint': 'Minimal luxe'}}


def _normalize_intro_style_preset(style_preset: str | None) -> str:
    normalized = str(style_preset or "cinematic_dark").strip().lower()
    return normalized if normalized in INTRO_FRAME_STYLE_PRESETS else "cinematic_dark"


def _get_intro_style_meta(style_preset: str | None) -> dict[str, Any]:
    return INTRO_FRAME_STYLE_PRESETS[_normalize_intro_style_preset(style_preset)]


def _append_unique_prompt_lines(target: list[str], *sections: list[str]) -> None:
    seen = {str(line or "").strip().lower() for line in target if str(line or "").strip()}
    for section in sections:
        for line in section or []:
            normalized = str(line or "").strip()
            if not normalized:
                continue
            marker = normalized.lower()
            if marker in seen:
                continue
            target.append(normalized)
            seen.add(marker)


_HOOK_STOPWORDS = {
    "a", "an", "and", "at", "for", "from", "in", "into", "of", "on", "or", "the", "to", "with",
    "about", "after", "before", "how", "why", "what", "when", "where", "your",
    "а", "в", "во", "для", "за", "и", "из", "к", "ко", "на", "над", "не", "но", "о", "об", "от", "по", "под", "при", "с", "со", "у",
    "как", "почему", "что", "это", "этот", "эта", "эти", "там", "тут",
}
_GENERIC_HOOK_PATTERNS = [
    "ЧТО ТУТ ПРОИСХОДИТ?",
    "ТЫ ЭТО ВИДИШЬ?",
    "ЭТО РЕАЛЬНО?",
    "Я НЕ ОЖИДАЛ ЭТОГО",
    "ПОСМОТРИ ЧТО ТУТ",
    "ЧТО-ТО НЕ ТАК...",
    "ЭТО МЕНЯЕТ ВСЁ",
]
_HOOK_PATTERNS_BY_STYLE = {
    "mystery_horror": [
        "ЧТО-ТО НЕ ТАК...",
        "ОНА ЭТО УВИДЕЛА...",
        "ЧТО ОНА НАШЛА?",
        "ТАМ КТО-ТО ЕСТЬ?",
    ],
    "tutorial_clickable": [
        "КАК ЭТО РАБОТАЕТ?",
        "ВОТ В ЧЁМ СЕКРЕТ",
        "ЧТО ВАЖНО ЗНАТЬ?",
        "ПОСМОТРИ КАК ЭТО",
    ],
    "ai_tech_explainer": [
        "ЧТО УМЕЕТ AI?",
        "КАК ЭТО РАБОТАЕТ?",
        "ВОТ ЧТО ИЗМЕНИЛОСЬ",
        "ПОЧЕМУ ЭТО ВАЖНО?",
    ],
    "youtube_shock": [
        "Я НЕ ОЖИДАЛ ЭТОГО",
        "ТЫ ЭТО ВИДИШЬ?",
        "ЭТО РЕАЛЬНО?",
        "ПОСМОТРИ ЧТО ТУТ",
    ],
    "breaking_alert": [
        "ЭТО МЕНЯЕТ ВСЁ",
        "ЧТО СЛУЧИЛОСЬ?",
        "СРОЧНО: СМОТРИ СЮДА",
        "ЭТО УЖЕ ПРОИСХОДИТ",
    ],
}
_HOOK_SEMANTIC_HINTS = {
    "person_seen": {
        "tokens": {"она", "он", "they", "she", "he", "girl", "woman", "man", "девушка", "женщина", "мужчина"},
        "patterns": ["ОНА ЭТО УВИДЕЛА...", "ЧТО ОН УВИДЕЛ?", "ПОСМОТРИ НА ЕГО РЕАКЦИЮ"],
    },
    "discovery": {
        "tokens": {"find", "found", "founder", "нашла", "нашел", "нашёл", "нашли", "discover", "discovered", "secret", "секрет", "тайна"},
        "patterns": ["ЧТО ОНА НАШЛА?", "ВОТ ЧТО НАШЛИ", "ТУТ ЕСТЬ СЕКРЕТ"],
    },
    "warning": {
        "tokens": {"warning", "danger", "alert", "breaking", "news", "risk", "опасно", "опасность", "тревога", "срочно", "warning:"},
        "patterns": ["ЧТО-ТО НЕ ТАК...", "СРОЧНО: СМОТРИ СЮДА", "ЭТО УЖЕ ПРОИСХОДИТ"],
    },
    "tech": {
        "tokens": {"ai", "tool", "tools", "app", "workflow", "prompt", "model", "tech", "нейросеть", "ии", "технология"},
        "patterns": ["ЧТО УМЕЕТ AI?", "КАК ЭТО РАБОТАЕТ?", "ВОТ ЧТО ИЗМЕНИЛОСЬ"],
    },
    "result": {
        "tokens": {"result", "results", "after", "before", "outcome", "итог", "результат", "после", "до"},
        "patterns": ["ВОТ ЧТО ВЫШЛО", "ЭТО МЕНЯЕТ ВСЁ", "ПОСМОТРИ НА РЕЗУЛЬТАТ"],
    },
}
_STRONG_HOOK_MARKERS = ("?", "!", ":")
_HOOK_TEMPLATE_TOKEN_LIMIT = 5


def _is_strong_hook_title(title: str) -> bool:
    normalized = str(title or "").strip()
    if not normalized:
        return False
    words = [word for word in re.split(r"\s+", normalized) if word]
    if not words:
        return False
    if len(words) <= 5 and any(marker in normalized for marker in _STRONG_HOOK_MARKERS):
        return True
    if len(words) <= 4 and any(marker in normalized for marker in ("...", "…")):
        return any(char.isupper() for char in normalized if char.isalpha())
    alpha_chars = [char for char in normalized if char.isalpha()]
    if alpha_chars and sum(1 for char in alpha_chars if char.isupper()) / len(alpha_chars) >= 0.75 and len(words) <= 5:
        return True
    impactful_words = sum(1 for word in words if len(re.sub(r"[^\wА-Яа-яЁё]", "", word, flags=re.UNICODE)) >= 4)
    if len(words) <= 4 and impactful_words >= 2 and (normalized == normalized.upper() or normalized == normalized.title()):
        return True
    return False


def _clean_hook_token(token: str) -> str:
    return token.strip(" \t\n\r\"'“”‘’.,!?:;()[]{}")


def _extract_hook_tokens(title: str) -> tuple[list[str], list[str], list[str]]:
    raw_tokens = [_clean_hook_token(token) for token in re.split(r"\s+", title) if _clean_hook_token(token)]
    meaningful_tokens = [token for token in raw_tokens if token and token.lower() not in _HOOK_STOPWORDS]
    semantic_tokens = [re.sub(r"[^\wА-Яа-яЁё-]", "", token, flags=re.UNICODE).lower() for token in meaningful_tokens]
    semantic_tokens = [token for token in semantic_tokens if token]
    return raw_tokens, meaningful_tokens, semantic_tokens


def _normalize_intro_title_input(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _choose_hook_pattern(style_key: str, semantic_tokens: list[str]) -> str:
    for hint in _HOOK_SEMANTIC_HINTS.values():
        if any(token in hint["tokens"] for token in semantic_tokens):
            for pattern in hint["patterns"]:
                if pattern:
                    return pattern
    style_patterns = _HOOK_PATTERNS_BY_STYLE.get(style_key) or []
    if style_patterns:
        return style_patterns[0]
    return _GENERIC_HOOK_PATTERNS[0]


def _build_hook_title_payload(originalTitle: str | None, styleKey: str | None = None) -> dict[str, Any]:
    title = _normalize_intro_title_input(originalTitle)
    if not title:
        return {"title": "Intro frame", "transformed": False, "patternUsed": None}
    if _is_strong_hook_title(title):
        return {"title": title, "transformed": False, "patternUsed": None}

    style_key = _normalize_intro_style_preset(styleKey)
    raw_tokens, meaningful_tokens, semantic_tokens = _extract_hook_tokens(title)
    if not raw_tokens:
        return {"title": title, "transformed": False, "patternUsed": None}

    short_meaningful = [token for token in meaningful_tokens if len(token) >= 3][:3]
    if len(raw_tokens) <= _HOOK_TEMPLATE_TOKEN_LIMIT and len(title) <= 48:
        tightened = " ".join(raw_tokens[: min(len(raw_tokens), _HOOK_TEMPLATE_TOKEN_LIMIT)]).strip()
        tightened = re.sub(r"\s+", " ", tightened).strip()
        if tightened and len(tightened.split()) >= 2 and tightened != title:
            return {"title": tightened, "transformed": True, "patternUsed": "semantic_trim"}

    anchor = ""
    for token in short_meaningful or raw_tokens:
        normalized = re.sub(r"[^\wА-Яа-яЁё-]", "", token, flags=re.UNICODE)
        if len(normalized) >= 3:
            anchor = normalized.upper()
            break
    pattern = _choose_hook_pattern(style_key, semantic_tokens)
    candidate = pattern
    if anchor and style_key in {"tutorial_clickable", "ai_tech_explainer"} and len(pattern.split()) <= 4:
        candidate = f"{pattern[:-1]} {anchor}?" if pattern.endswith("?") else f"{pattern} {anchor}"
    candidate = re.sub(r"\s+", " ", candidate).strip()[:64].rstrip()
    if not candidate:
        return {"title": title, "transformed": False, "patternUsed": None}
    return {"title": candidate, "transformed": candidate != title, "patternUsed": pattern if candidate != title else None}


def buildHookTitle(originalTitle: str | None, styleKey: str | None = None) -> str:
    return _build_hook_title_payload(originalTitle, styleKey).get("title") or "Intro frame"


def splitTitleIntoLines(title: str | None) -> list[str]:
    normalized = _normalize_intro_title_input(title)
    if not normalized:
        return []
    tokens = [token for token in normalized.split(" ") if token]
    if len(tokens) <= 2:
        return [normalized]
    if len(tokens) <= 4:
        pivot = math.ceil(len(tokens) / 2)
        return [" ".join(tokens[:pivot]), " ".join(tokens[pivot:])]

    best_lines = [normalized]
    best_score = None
    for line_count in (2, 3):
        if len(tokens) < line_count:
            continue
        chunk = math.ceil(len(tokens) / line_count)
        lines = [" ".join(tokens[i:i + chunk]) for i in range(0, len(tokens), chunk)]
        lines = [line for line in lines if line]
        if len(lines) > 3:
            continue
        longest = max(len(line) for line in lines)
        shortest = min(len(line) for line in lines)
        score = (longest - shortest) + abs(len(lines) - 2) * 3
        if best_score is None or score < best_score:
            best_lines = lines
            best_score = score
    return best_lines[:3]


def _resolve_intro_composition_plan(
    connected_refs_by_role: dict[str, list[str]] | None,
    hero_participants: list[str] | None = None,
    supporting_participants: list[str] | None = None,
    important_props: list[str] | None = None,
) -> dict[str, Any]:
    refs = connected_refs_by_role if isinstance(connected_refs_by_role, dict) else {}
    cast_roles = [role for role in COMFY_CAST_ROLES if len(refs.get(role) or []) > 0]
    prop_roles = ["props"] if len(refs.get("props") or []) > 0 else []
    hero_role_signals = [role for role in (hero_participants or []) if role in COMFY_CAST_ROLES and role not in cast_roles]
    support_role_signals = [role for role in (supporting_participants or []) if role in COMFY_CAST_ROLES and role not in cast_roles and role not in hero_role_signals]
    prop_signal_labels = [str(item or "").strip() for item in (important_props or []) if str(item or "").strip()]
    subject_roles = cast_roles + [role for role in hero_role_signals + support_role_signals if role not in cast_roles]
    has_subjects = bool(subject_roles or prop_roles or prop_signal_labels)
    mode = "subject_led" if has_subjects else "style_led"
    focus_targets = subject_roles + prop_roles
    prompt_lines = [
        f"Composition mode: {'subject-led composition' if mode == 'subject_led' else 'style-led composition'}.",
    ]
    if mode == "subject_led":
        prompt_lines.extend([
            "Subjects MUST remain dominant in the thumbnail.",
            "Visible characters and/or objects MUST remain the main heroes of the thumbnail.",
            "Subjects should occupy roughly 50-60% of the visual attention.",
            "Text, glow, arrows, accents, overlays, and decorative elements should occupy the remaining 40-50%.",
            "Do NOT let text, graphics, glow, or decorative overlays overpower the subject heroes.",
            "Do NOT hide faces, bodies, eyes, or the main object core unless absolutely necessary for readability.",
            "Subject readability is priority over style treatment when the two conflict.",
            "Subject readability and focal clarity are high priority and must survive the final composition.",
        ])
    else:
        prompt_lines.extend([
            "If no strong characters or objects are present, rely on style-driven composition.",
            "Use the selected preset's mood, palette, atmosphere, light treatment, text treatment, and graphic language to carry the frame.",
            "Still preserve one clear focal hierarchy and keep the frame clickable, clean, and premium.",
        ])
    return {
        "mode": mode,
        "focusTargets": focus_targets,
        "subjectRoles": subject_roles,
        "propSignals": prop_signal_labels,
        "promptLines": prompt_lines,
        "weights": {"subject": "50-60%", "support": "40-50%"} if mode == "subject_led" else {"subject": "style-dependent", "support": "style-dependent"},
    }


def _normalize_intro_preview_format(preview_format: str | None) -> str:
    value = str(preview_format or "16:9").strip()
    return value if value in {"9:16", "1:1", "16:9"} else "16:9"


def _resolve_intro_preview_dimensions(preview_format: str | None) -> tuple[int, int]:
    normalized = _normalize_intro_preview_format(preview_format)
    if normalized == "9:16":
        return 1024, 1820
    if normalized == "1:1":
        return 1024, 1024
    return 1344, 768


def _normalize_intro_connected_refs_by_role(raw_refs_by_role: Any) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    source = raw_refs_by_role if isinstance(raw_refs_by_role, dict) else {}
    for role in COMFY_REF_ROLES:
        items = source.get(role) if isinstance(source, dict) else None
        urls: list[str] = []
        for item in (items or []):
            value = item if isinstance(item, str) else (item.get("url") if isinstance(item, dict) else "")
            value = str(value or "").strip()
            if value and value not in urls:
                urls.append(value)
        normalized[role] = urls
    return normalized


def _normalize_intro_text_list(items: Any, max_items: int = 8) -> list[str]:
    out: list[str] = []
    if not isinstance(items, list):
        return out
    for item in items:
        value = str(item or "").strip()
        if value and value not in out:
            out.append(value)
        if len(out) >= max_items:
            break
    return out


def _normalized_gender_presentation(raw: Any) -> str:
    value = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    if not value:
        return ""
    if any(token in value for token in ["female", "woman", "girl", "feminine"]):
        return "female"
    if any(token in value for token in ["male", "man", "boy", "masculine"]):
        return "male"
    return ""


def _normalize_intro_lock_map(raw: Any, *, allowed_roles: set[str] | None = None, normalizer=None) -> dict[str, str]:
    source = raw if isinstance(raw, dict) else {}
    out: dict[str, str] = {}
    for key, value in source.items():
        role = str(key or "").strip()
        if not role or (allowed_roles is not None and role not in allowed_roles):
            continue
        normalized = normalizer(value) if callable(normalizer) else str(value or "").strip()
        normalized = str(normalized or "").strip()
        if normalized:
            out[role] = normalized
    return out


_INTRO_SPECIES_CYRILLIC_MAP = str.maketrans({
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
})

_INTRO_SPECIES_ALIAS_MAP = {
    "dog": "dog",
    "dogs": "dog",
    "canine": "dog",
    "puppy": "dog",
    "hound": "dog",
    "sobaka": "dog",
    "pes": "dog",
    "pyos": "dog",
    "shchenok": "dog",
    "cat": "cat",
    "cats": "cat",
    "feline": "cat",
    "kitten": "cat",
    "kitty": "cat",
    "koshka": "cat",
    "kot": "cat",
    "kotenok": "cat",
    "wolf": "wolf",
    "wolves": "wolf",
    "canis lupus": "wolf",
    "volk": "wolf",
    "volchitsa": "wolf",
    "horse": "horse",
    "horses": "horse",
    "equine": "horse",
    "stallion": "horse",
    "mare": "horse",
    "loshad": "horse",
    "kon": "horse",
    "bird": "bird",
    "avian": "bird",
    "parrot": "bird",
    "eagle": "bird",
    "owl": "bird",
    "ptitsa": "bird",
    "popugay": "bird",
    "orel": "bird",
    "sova": "bird",
}

_INTRO_SPECIES_KEYWORD_GROUPS = [
    ("dog", ("dog", "dogs", "sobaka", "pes", "pyos", "puppy", "hound", "canine")),
    ("cat", ("cat", "cats", "kot", "koshka", "kitten", "kitty", "feline")),
    ("wolf", ("wolf", "wolves", "volk", "volchitsa", "canis lupus")),
    ("horse", ("horse", "horses", "loshad", "kon", "equine", "stallion", "mare")),
    ("bird", ("bird", "ptitsa", "popugay", "orel", "sova", "avian", "parrot", "eagle", "owl")),
]


def _intro_species_from_keywords(normalized: str) -> str:
    normalized_value = re.sub(r"\s+", " ", str(normalized or "").strip().lower())
    if not normalized_value:
        return ""
    padded = f" {normalized_value} "
    for canonical, keywords in _INTRO_SPECIES_KEYWORD_GROUPS:
        for keyword in keywords:
            if " " in keyword:
                if keyword in normalized_value:
                    return canonical
                continue
            if f" {keyword} " in padded:
                return canonical
    return normalized_value


def _normalize_intro_species_lock(raw: Any) -> str:
    value = re.sub(r"\s+", " ", str(raw or "").strip().lower())
    if not value:
        return ""
    transliterated = unicodedata.normalize("NFKD", value).translate(_INTRO_SPECIES_CYRILLIC_MAP)
    ascii_value = transliterated.encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-z0-9\- ]+", " ", ascii_value)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    normalized_alias = _INTRO_SPECIES_ALIAS_MAP.get(normalized, normalized)
    return _intro_species_from_keywords(normalized_alias)


def _normalize_intro_role_list(items: Any, *, allowed_roles: set[str] | None = None, max_items: int = 8) -> list[str]:
    out: list[str] = []
    for role in _normalize_intro_text_list(items, max_items=max_items):
        if allowed_roles is not None and role not in allowed_roles:
            continue
        if role not in out:
            out.append(role)
    return out


def _intro_role_descriptor(role: str, *, gender_locks: dict[str, str] | None = None, species_locks: dict[str, str] | None = None) -> str:
    role_key = str(role or "").strip()
    label = _intro_role_label(role_key)
    gender_value = str((gender_locks or {}).get(role_key) or "").strip()
    species_value = str((species_locks or {}).get(role_key) or "").strip()
    if gender_value:
        return f"{label} ({gender_value})"
    if species_value:
        return f"{label} ({species_value})"
    return label


def _intro_role_package_summary(active_roles: list[str], *, gender_locks: dict[str, str] | None = None, species_locks: dict[str, str] | None = None) -> str:
    descriptors = [_intro_role_descriptor(role, gender_locks=gender_locks, species_locks=species_locks) for role in active_roles]
    return ", ".join(item for item in descriptors if item) or "none"


def _intro_cast_contract_preview(
    *,
    active_roles: list[str],
    must_appear: list[str],
    must_not_appear: list[str],
    gender_locks: dict[str, str],
    species_locks: dict[str, str],
) -> list[str]:
    preview_lines = [
        f"active cast roles: {_intro_role_package_summary(active_roles, gender_locks=gender_locks, species_locks=species_locks)}",
        f"must appear: {_intro_role_package_summary(must_appear, gender_locks=gender_locks, species_locks=species_locks)}",
        f"must not appear: {_intro_role_package_summary(must_not_appear, gender_locks=gender_locks, species_locks=species_locks)}",
    ]
    if gender_locks:
        preview_lines.append(
            "gender locks: " + ", ".join(f"{_intro_role_label(role)}={value}" for role, value in gender_locks.items())
        )
    if species_locks:
        preview_lines.append(
            "species locks: " + ", ".join(f"{_intro_role_label(role)}={value}" for role, value in species_locks.items())
        )
    return preview_lines


def _intro_role_label(role: str) -> str:
    return {
        "character_1": "character 1",
        "character_2": "character 2",
        "character_3": "character 3",
        "animal": "animal",
        "group": "group",
        "location": "location",
        "style": "style",
        "props": "props",
    }.get(str(role or "").strip(), str(role or "").strip().replace("_", " "))


def _build_intro_reference_inline_parts(connected_refs_by_role: dict[str, list[str]]) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    inline_parts: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    attached_roles: list[str] = []
    per_role_limit = {
        "character_1": 2,
        "character_2": 2,
        "character_3": 2,
        "animal": 2,
        "group": 1,
        "location": 1,
        "style": 1,
        "props": 2,
    }
    max_total = 10
    for role in COMFY_REF_ROLES:
        remaining = max_total - len(inline_parts)
        if remaining <= 0:
            break
        parts: list[dict[str, Any]] = []
        role_limit = min(per_role_limit.get(role, 1), remaining)
        for ref_url in (connected_refs_by_role.get(role) or [])[:role_limit]:
            if len(parts) >= remaining:
                break
            inline_part = _load_reference_image_inline(ref_url)
            if inline_part:
                parts.append(inline_part)
        counts[role] = len(parts)
        if parts:
            attached_roles.append(role)
            inline_parts.extend(parts)
    attached_debug = {
        "attachedInlineReferenceRoles": attached_roles,
        "rolesWithImageParts": attached_roles,
        "roleAttachedImageCounts": {role: counts.get(role, 0) for role in COMFY_REF_ROLES},
        "totalInlineImages": len(inline_parts),
        "dogRoleAttached": counts.get("animal", 0) > 0,
        "animalRefAttached": counts.get("animal", 0) > 0,
        "character1RefAttached": counts.get("character_1", 0) > 0,
        "character2RefAttached": counts.get("character_2", 0) > 0,
    }
    return inline_parts, counts, attached_debug


def _hex_to_rgba(hex_value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    rgb = ImageColor.getrgb(str(hex_value or "#ffffff"))
    return rgb[0], rgb[1], rgb[2], max(0, min(255, int(alpha)))


def _get_intro_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    normalized_size = max(24, int(size))
    filename_candidates = [
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "Arial Bold.ttf" if bold else "Arial.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    ]
    path_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/local/share/fonts/DejaVuSans-Bold.ttf" if bold else "/usr/local/share/fonts/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Arial Bold.ttf" if bold else "C:/Windows/Fonts/Arial.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
    ]
    for font_name in filename_candidates:
        try:
            return ImageFont.truetype(font_name, normalized_size)
        except Exception:
            continue
    for path in path_candidates:
        if not path:
            continue
        try:
            return ImageFont.truetype(path, normalized_size)
        except Exception:
            continue
    print("[INTRO FRAME FONT WARNING] truetype font not found, fallback used")
    return ImageFont.load_default()


def _wrap_intro_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> list[str]:
    preset_lines = splitTitleIntoLines(text)
    if preset_lines and len(preset_lines) <= max_lines:
        candidate_boxes = [draw.textbbox((0, 0), line, font=font, stroke_width=2) for line in preset_lines]
        if candidate_boxes and all(bbox and (bbox[2] - bbox[0]) <= max_width for bbox in candidate_boxes):
            return preset_lines
    words = [word for word in re.split(r"\s+", str(text or "").strip()) if word]
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        test = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), test, font=font, stroke_width=2)
        if bbox and (bbox[2] - bbox[0]) <= max_width:
            current = test
            continue
        lines.append(current)
        current = word
        if len(lines) >= max_lines - 1:
            break
    if len(lines) < max_lines:
        lines.append(current)
    remaining_words = words[len(" ".join(lines).split()):]
    if remaining_words:
        lines[-1] = (lines[-1].rstrip(" .,!?:;-") + "…").strip()
    return lines[:max_lines]


def _fit_intro_title(draw: ImageDraw.ImageDraw, title: str, box_width: int, box_height: int, preview_format: str) -> tuple[ImageFont.ImageFont, list[str], int]:
    normalized_title = str(title or "").strip() or "Intro frame"
    normalized_title_word_count = len([word for word in re.split(r"\s+", normalized_title) if word])
    max_lines = 3 if preview_format == "9:16" else 2
    width_factor = 0.205 if preview_format == "16:9" else 0.195 if preview_format == "1:1" else 0.18
    max_font = max(72, min(248, int(box_width * width_factor)))
    min_font = 34 if preview_format == "9:16" else 44
    font_step = 3 if preview_format == "9:16" else 4
    spacing_factor = 0.14 if preview_format == "9:16" else 0.16
    stroke_width = 2
    for font_size in range(max_font, min_font - 1, -font_step):
        font = _get_intro_font(font_size, bold=True)
        lines = _wrap_intro_text(draw, normalized_title, font, box_width, max_lines)
        if not lines:
            continue
        rendered_word_count = len([word for line in lines for word in re.split(r"\s+", str(line).replace("…", " ").strip()) if word])
        truncated = rendered_word_count < normalized_title_word_count
        spacing = max(8 if preview_format == "9:16" else 10, int(font_size * spacing_factor))
        bbox = draw.multiline_textbbox((0, 0), "\n".join(lines), font=font, spacing=spacing, stroke_width=stroke_width)
        if not bbox:
            continue
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width <= box_width and height <= box_height and not truncated:
            return font, lines, spacing
        if width <= box_width and height <= box_height and font_size == min_font:
            return font, lines, spacing
    fallback_size = max(min_font, 34 if preview_format == "9:16" else 48)
    fallback_font = _get_intro_font(fallback_size, bold=True)
    fallback_lines = _wrap_intro_text(draw, normalized_title, fallback_font, box_width, max_lines) or [normalized_title[:80]]
    fallback_spacing = max(8 if preview_format == "9:16" else 10, int(fallback_size * spacing_factor))
    return fallback_font, fallback_lines[:max_lines], fallback_spacing


def _draw_text_tracking(draw: ImageDraw.ImageDraw, position: tuple[float, float], text: str, font: ImageFont.ImageFont, fill: tuple[int, int, int, int], tracking: int = 0) -> None:
    x, y = position
    for char in str(text or ""):
        draw.text((x, y), char, font=font, fill=fill)
        bbox = draw.textbbox((x, y), char, font=font)
        fallback_size = getattr(font, "size", 16)
        advance = (bbox[2] - bbox[0]) if bbox else fallback_size * 0.5
        x += advance + tracking


def _draw_intro_vertical_fade(overlay: Image.Image, *, top_alpha: int, bottom_alpha: int) -> None:
    width, height = overlay.size
    draw = ImageDraw.Draw(overlay)
    for y in range(height):
        top_ratio = max(0.0, 1.0 - (y / max(1, height * 0.52)))
        bottom_ratio = max(0.0, (y - (height * 0.45)) / max(1, height * 0.55))
        alpha = int(max(top_ratio * top_alpha, bottom_ratio * bottom_alpha))
        if alpha <= 0:
            continue
        draw.line((0, y, width, y), fill=(6, 9, 18, min(255, alpha)))


def _render_intro_frame_asset(raw: bytes, *, title: str, style_preset: str, preview_format: str) -> tuple[bytes, dict[str, Any]]:
    base = Image.open(io.BytesIO(raw)).convert("RGBA")
    width, height = base.size
    normalized_preview_format = _normalize_intro_preview_format(preview_format)
    style_meta = _get_intro_style_meta(style_preset)
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    accent = _hex_to_rgba(style_meta.get("accent", "#f6d365"), 255)
    apply_vertical_fade = str(os.getenv("INTRO_PREVIEW_VERTICAL_FADE", "")).strip().lower() in {"1", "true", "yes", "on"}
    show_style_watermark = str(os.getenv("INTRO_PREVIEW_STYLE_WATERMARK", "")).strip().lower() in {"1", "true", "yes", "on"}
    if apply_vertical_fade:
        _draw_intro_vertical_fade(overlay, top_alpha=32, bottom_alpha=64)
    draw = ImageDraw.Draw(overlay)
    margin_x = int(width * (0.065 if normalized_preview_format == "16:9" else 0.078))
    brand_font = _get_intro_font(max(12, int(width * 0.013)), bold=True)
    footer_font = _get_intro_font(max(11, int(width * 0.0105)), bold=False)
    brand_x = margin_x
    brand_y = max(16, int(height * 0.045))
    footer_text = "ava-studio product 2026"
    footer_y = height - int(height * 0.04) - getattr(footer_font, "size", 12)
    watermark_text = (style_meta.get("label") or "ava-studio") if show_style_watermark else ""
    watermark_font_size = 0
    if watermark_text:
        watermark_font = _get_intro_font(max(10, int(width * 0.009)), bold=False)
        watermark_bbox = draw.textbbox((0, 0), watermark_text, font=watermark_font)
        watermark_w = (watermark_bbox[2] - watermark_bbox[0]) if watermark_bbox else int(width * 0.08)
        watermark_h = (watermark_bbox[3] - watermark_bbox[1]) if watermark_bbox else getattr(watermark_font, "size", 10)
        watermark_x = max(16, width - margin_x - watermark_w)
        watermark_y = max(brand_y, footer_y - watermark_h - max(12, int(height * 0.018)))
        watermark_font_size = int(getattr(watermark_font, "size", 0) or 0)

    _draw_text_tracking(draw, (brand_x, brand_y), "ava-studio", brand_font, (255, 255, 255, 178), tracking=max(0, int(width * 0.0009)))
    _draw_text_tracking(draw, (margin_x, footer_y), footer_text, footer_font, (232, 236, 255, 150), tracking=max(0, int(width * 0.0007)))
    if watermark_text:
        draw.text((watermark_x, watermark_y), watermark_text, font=watermark_font, fill=(accent[0], accent[1], accent[2], 72))

    overlay_debug = {
        "title": title,
        "previewFormat": normalized_preview_format,
        "mainTitleRenderedBy": "gemini",
        "verticalFadeApplied": apply_vertical_fade,
        "brandingOverlayApplied": True,
        "footerOverlayApplied": True,
        "watermarkOverlayApplied": bool(str(watermark_text or "").strip()),
        "brandingText": "ava-studio",
        "footerText": footer_text,
        "watermarkText": watermark_text,
        "brandingFontSize": int(getattr(brand_font, "size", 0) or 0),
        "footerFontSize": int(getattr(footer_font, "size", 0) or 0),
        "watermarkFontSize": watermark_font_size,
    }
    print(
        "[INTRO FRAME OVERLAY] "
        + json.dumps(
            {
                "title": title,
                "previewFormat": normalized_preview_format,
                "overlay.mainTitleRenderedBy": overlay_debug["mainTitleRenderedBy"],
                "overlay.verticalFadeApplied": overlay_debug["verticalFadeApplied"],
                "overlay.brandingOverlayApplied": overlay_debug["brandingOverlayApplied"],
                "overlay.footerOverlayApplied": overlay_debug["footerOverlayApplied"],
                "overlay.watermarkOverlayApplied": overlay_debug["watermarkOverlayApplied"],
                "overlay.brandingFontSize": overlay_debug["brandingFontSize"],
                "overlay.footerFontSize": overlay_debug["footerFontSize"],
                "overlay.watermarkFontSize": overlay_debug["watermarkFontSize"],
            },
            ensure_ascii=False,
        )
    )

    composed = Image.alpha_composite(base, overlay).convert("RGB")
    out = io.BytesIO()
    composed.save(out, format="PNG", optimize=True)
    return out.getvalue(), overlay_debug


def _build_intro_frame_prompt(payload: IntroGenerateIn) -> tuple[str, dict[str, Any]]:
    style_preset = _normalize_intro_style_preset(payload.stylePreset)
    preview_format = _normalize_intro_preview_format(payload.previewFormat)
    style_meta = _get_intro_style_meta(style_preset)
    manual_title_raw = _normalize_intro_title_input(getattr(payload, "manualTitleRaw", None))
    original_title = _normalize_intro_title_input(payload.title) or manual_title_raw or "Intro frame"
    hook_title_payload = _build_hook_title_payload(original_title, style_preset)
    title = str(hook_title_payload.get("title") or original_title or "Intro frame")
    title_transformed = bool(hook_title_payload.get("transformed"))
    hook_pattern_used = hook_title_payload.get("patternUsed")
    title_context = str(payload.titleContext or "").strip()
    story_context = str(payload.storyContext or "").strip()
    source_node_types = [str(item or "").strip() for item in (payload.sourceNodeTypes or []) if str(item or "").strip()]
    try:
        duration_sec = max(0.5, min(8.0, float(payload.durationSec or 2.5)))
    except Exception:
        duration_sec = 2.5
    try:
        scene_count = max(0, min(24, int(payload.sceneCount or 0)))
    except Exception:
        scene_count = 0
    connected_refs_by_role = _normalize_intro_connected_refs_by_role(getattr(payload, "connectedRefsByRole", None))
    connected_roles = [role for role, urls in connected_refs_by_role.items() if urls]
    connected_cast_roles = [role for role in connected_roles if role in COMFY_CAST_ROLES]
    connected_world_roles = [role for role in connected_roles if role in COMFY_WORLD_ANCHOR_ROLES]
    connected_prop_roles = [role for role in connected_roles if role == "props"]
    role_aware_cast_summary = str(getattr(payload, "roleAwareCastSummary", "") or "").strip()
    hero_participants = _normalize_intro_text_list(getattr(payload, "heroParticipants", None), max_items=5)
    supporting_participants = _normalize_intro_text_list(getattr(payload, "supportingParticipants", None), max_items=6)
    important_props = _normalize_intro_text_list(getattr(payload, "importantProps", None), max_items=5)
    world_context = str(getattr(payload, "worldContext", "") or "").strip()
    style_context = str(getattr(payload, "styleContext", "") or "").strip()
    story_summary = str(getattr(payload, "storySummary", "") or "").strip()
    preview_prompt = str(getattr(payload, "previewPrompt", "") or "").strip()
    world = str(getattr(payload, "world", "") or "").strip()
    roles = _normalize_intro_text_list(getattr(payload, "roles", None), max_items=12)
    tone_style_direction = str(getattr(payload, "toneStyleDirection", "") or "").strip()
    intro_must_appear = _normalize_intro_role_list(getattr(payload, "introMustAppear", None), allowed_roles=COMFY_CAST_ROLES, max_items=6)
    intro_must_not_appear = _normalize_intro_role_list(getattr(payload, "introMustNotAppear", None), allowed_roles=COMFY_CAST_ROLES, max_items=6)
    role_gender_locks = _normalize_intro_lock_map(
        getattr(payload, "connectedGenderLocksByRole", None),
        allowed_roles=COMFY_CAST_ROLES,
        normalizer=_normalized_gender_presentation,
    )
    animal_species_locks = _normalize_intro_lock_map(
        getattr(payload, "connectedSpeciesLocksByRole", None),
        allowed_roles=COMFY_CAST_ROLES,
        normalizer=_normalize_intro_species_lock,
    )

    intro_active_cast_roles = [role for role in connected_cast_roles if role in COMFY_CAST_ROLES]
    if not intro_must_appear:
        intro_must_appear = list(intro_active_cast_roles)
    hero_participants_resolved: list[str] = []
    for role in hero_participants + supporting_participants + intro_must_appear + intro_active_cast_roles:
        if role in COMFY_CAST_ROLES and role not in hero_participants_resolved:
            hero_participants_resolved.append(role)
    intro_connected_role_counts = {role: len(connected_refs_by_role.get(role) or []) for role in connected_roles}
    composition_plan = _resolve_intro_composition_plan(
        connected_refs_by_role,
        hero_participants=hero_participants,
        supporting_participants=supporting_participants,
        important_props=important_props,
    )
    intro_cast_contract_preview = _intro_cast_contract_preview(
        active_roles=intro_active_cast_roles,
        must_appear=intro_must_appear,
        must_not_appear=intro_must_not_appear,
        gender_locks=role_gender_locks,
        species_locks=animal_species_locks,
    )
    connected_ref_counts = {role: len(connected_refs_by_role.get(role) or []) for role in COMFY_REF_ROLES}
    strict_identity_package_roles = [role for role in intro_active_cast_roles if connected_ref_counts.get(role, 0) > 0]
    strict_identity_package_summary = _intro_role_package_summary(
        strict_identity_package_roles,
        gender_locks=role_gender_locks,
        species_locks=animal_species_locks,
    )
    female_locked_roles = [role for role, gender_value in role_gender_locks.items() if gender_value == "female"]
    female_roles_with_refs = [
        role
        for role in ("character_1", "character_2", "character_3")
        if role_gender_locks.get(role) == "female" and connected_ref_counts.get(role, 0) > 0
    ]
    role_identity_lock_lines: list[str] = [f"- {role} must match its reference image exactly" for role in strict_identity_package_roles]
    if connected_ref_counts.get("animal", 0) > 0 and "animal" not in strict_identity_package_roles:
        role_identity_lock_lines.append("- animal must match its reference image exactly")
    strict_role_mapping_lines = [
        f"- {_intro_role_label(role)} may only use attached {_intro_role_label(role)} reference images"
        for role in COMFY_REF_ROLES
        if connected_ref_counts.get(role, 0) > 0
    ]

    format_rule = {
        "9:16": "vertical opening frame, mobile-first composition, strong center-of-interest, generous vertical negative space",
        "1:1": "square hero thumbnail composition, balanced central hierarchy, strong read at feed size",
        "16:9": "widescreen opening frame, cinematic panoramic balance, horizontal staging with premium depth",
    }[preview_format]

    base_thumbnail_prompt_lines = [
        "Create one premium opening-frame still image for a clip intro preview.",
        f"Target aspect ratio: {preview_format}. Composition rule: {format_rule}.",
        f"Style preset: {style_meta['label']}. Visual intent: {style_meta['shortDescription']}",
        f"Style preset composition bias: {style_meta['compositionBias']}.",
        f"Style palette / mood: {style_meta['palette']} / {style_meta['mood']}.",
        f"Text treatment: {style_meta['textTreatment']}. Graphic accents: {style_meta['graphicAccentsPreference']}. Overlays: {style_meta['overlays']}.",
        f"Title concept for story guidance only: {title}.",
        "Keep the image as one clean hook frame, not a collage, poster mockup, or UI layout.",
        "Attached reference images are the exact cast package and exact world anchors.",
        "Use the attached references as strict identity anchors, not loose inspiration.",
        "The main headline title must be rendered directly inside the image as part of the thumbnail design.",
        "Do not leave the main title for backend overlay.",
        "The title should be large, bold, clickable, readable, and stylistically integrated into the thumbnail.",
        "Preserve the original title casing from the user; do not force uppercase treatment.",
        "Preserve a safe readable area for title text.",
        "Do not place text over the main face if it can be avoided.",
        "Do not place text over the main object core if it can be avoided.",
        "Title should support the composition, not destroy it.",
        "Avoid turning the thumbnail into mostly text.",
        "Keep the thumbnail readable at small size.",
        "DESIGN BRAIN RULES:",
        "- This is not just an image — this is a high-performing YouTube thumbnail.",
        "- You must design it like a thumbnail designer, not a photographer.",
        "- Apply strong visual hierarchy: first attention is bold readable headline text, second attention is the main subject, third attention is background and context.",
        "- Use rule of thirds: place subjects in lower or side zones and reserve clean space for text.",
        "- Create a strong visual hook with emotion, tension, curiosity, or contrast and avoid passive or neutral scenes.",
        "- Enhance subjects: increase brightness and contrast on faces, slightly darken or simplify the background with a vignette feel, and add rim light or glow for separation.",
        "- Make the thumbnail readable at small size.",
        "- Avoid flat composition, weak contrast, caption-like text, and lifeless poses.",
    ]
    style_fragment_lines = [
        f"Prompt fragment: {style_meta['promptFragment']}.",
    ]
    style_rule_lines = [
        "INTRO FRAME STYLE RULES:",
        *[f"- {rule}" for rule in style_meta["promptRules"]],
    ]
    composition_lines = [
        "COMPOSITION SYSTEM:",
        *composition_plan["promptLines"],
    ]
    text_line_layout = splitTitleIntoLines(title)
    hook_rule_lines = [
        "HOOK RULES:",
        "- Create curiosity and tension.",
        "- The viewer must instantly wonder what is happening.",
        "- Use incomplete action or reaction.",
        "- Avoid fully explained scenes.",
        "- Strong hooks include surprised face, unexpected interaction, visual contradiction, and hidden or unclear cause.",
        "- The thumbnail must trigger a click.",
    ]
    text_rule_lines = [
        "TEXT RULES:",
        "- Text must feel like a headline, not a caption.",
        "- Break into 2-3 lines with visually balanced lengths when possible.",
        "- Keep words short and impactful.",
        "- Use strong contrast like white or yellow on dark areas.",
        "- Add shadow or glow for readability.",
        "- Placement: top center or top side.",
        "- Text must not overlap faces.",
        "- Text must stay inside the safe area.",
        "- IMPORTANT: preserve original letter case from user input and DO NOT auto-uppercase.",
        f"- Suggested line layout: {' / '.join(text_line_layout) if text_line_layout else title}.",
    ]
    text_placement_intelligence_rule_lines = [
        "TEXT PLACEMENT INTELLIGENCE:",
        "- Place title text in the cleanest readable area of the frame.",
        "- If faces are centered, place text above or to the side.",
        "- If the top area is busy, prefer a side zone with cleaner negative space.",
        "- Avoid placing text over eyes, faces, hands, or the core of the main object.",
        "- Prefer sky, blurred background, soft wall, fog, water, or other lower-detail zones for text placement.",
        "- Keep the text block compact and visually stable.",
        "- The title should feel intentionally designed into the composition, not stamped on top of it.",
    ]
    contrast_rule_lines = [
        "VISUAL CONTRAST RULES:",
        "- Brighten subjects slightly.",
        "- Darken or simplify background.",
        "- Increase vibrance moderately, not oversaturated.",
        "- Maintain clean separation between subject and background.",
        "- Goal: subjects must pop instantly.",
    ]
    frame_adjustment_rule_lines = [
        "FRAME ADJUSTMENT RULES:",
        "- Crop or zoom the framing if needed to emphasize the main subjects.",
        "- Avoid wide empty areas that weaken clickability.",
        "- Favor closer framing on faces, reactions, or the main object when it improves impact.",
        "- Keep enough negative space for the title, but do not waste the frame.",
        "- The thumbnail should feel focused, intentional, and high-impact at small size.",
    ]
    emotion_boost_rule_lines = [
        "EMOTION BOOST RULES:",
        "- Slightly exaggerate facial expressions when it improves clickability.",
        "- Increase emotional clarity in eyes, eyebrows, and mouth.",
        "- Avoid blank or neutral expressions if the scene is meant to feel intriguing.",
        "- Keep expressions believable, premium, and not meme-like.",
        "- If a face is present, emotional readability should be one of the strongest hooks.",
    ]
    focus_rule_lines = [
        "FOCUS RULES:",
        "- Emphasize faces and eyes.",
        "- If face present, it becomes the focal anchor.",
        "- If no face is present, the main object becomes the focal anchor.",
    ]
    prompt_lines = [
        "STRICT INTRO CONTRACT:",
        f"- active cast package: {_intro_role_package_summary(intro_active_cast_roles, gender_locks=role_gender_locks, species_locks=animal_species_locks)}",
        f"- must appear: {_intro_role_package_summary(intro_must_appear, gender_locks=role_gender_locks, species_locks=animal_species_locks)}",
        f"- must not appear: {_intro_role_package_summary(intro_must_not_appear, gender_locks=role_gender_locks, species_locks=animal_species_locks)}",
        f"- strict identity package: {strict_identity_package_summary}",
        "- render exactly the connected participants",
        "- render exactly the connected cast package",
        "- no extra humans",
        "- no background crowd",
        "- no hidden extra faces",
        "- no random replacement characters",
        "- do not replace connected cast with generic people",
        "- if character refs exist, preserve face, hair, identity, and gender presentation exactly",
        "- do not replace the lead with a generic random person",
        "- do not merge multiple roles into one person",
        "- do not merge roles",
        "- do not replace one role with another role",
        "- do not omit any required connected role",
        "EXACT CAST PACKAGE LOCK:",
        "- attached reference images are the exact participants of the intro frame",
        "- render exactly the connected participants and no others",
        "- all connected participants must be visible in the final frame",
        "- do not omit any participant",
        "- do not add extra humans",
        "- do not replace participants with lookalikes",
        "- do not merge two women into one heroine",
        "- do not turn the cast package into a generic crowd scene",
        "ROLE SEPARATION LOCK:",
    ]
    exact_role_separation_lines = []
    for role in intro_active_cast_roles:
        if connected_ref_counts.get(role, 0) > 0:
            exact_role_separation_lines.append(f"- {role} must remain the exact {_intro_role_label(role)} from {role} refs")
    if exact_role_separation_lines:
        prompt_lines.extend(exact_role_separation_lines)
    else:
        prompt_lines.append("- keep connected role identities separated and exact")
    prompt_lines.append("- do not swap identities between roles")
    prompt_lines.append(
        "- exact visible cast in frame must match this package: "
        + _intro_role_package_summary(
            intro_active_cast_roles,
            gender_locks=role_gender_locks,
            species_locks=animal_species_locks,
        )
    )
    prompt_lines.extend([
        "WORLD SOURCE RULE:",
        "- world, environment, and location must come from story context and opening beats",
        "- storySummary and previewPrompt are primary source-of-truth for what is happening in this frame",
        "- use refs for who is in frame",
        "- use story context for where the frame happens",
        "- do not invent bedroom / home interior / random room if story context suggests industrial / abandoned / gym / tension space",
    ])
    if intro_must_appear:
        prompt_lines.append("- if introMustAppear is set, those roles must appear in composition")
    if hero_participants_resolved:
        prompt_lines.append("- heroParticipants resolved must be prioritized in composition and visual hierarchy")
    if story_summary or preview_prompt:
        prompt_lines.append("- if storySummary or previewPrompt implies a specific conflict/event, render that exact conflict/event visually")
    if role_identity_lock_lines:
        prompt_lines.extend([
            "ROLE IDENTITY LOCK:",
            *role_identity_lock_lines,
        ])
    if strict_role_mapping_lines:
        prompt_lines.extend([
            "ROLE → REF MAPPING LOCK:",
            *strict_role_mapping_lines,
        ])
    if len(strict_identity_package_roles) >= 2:
        prompt_lines.append("- if 2 connected character roles exist, render exactly 2 distinct people")
    if connected_ref_counts.get("animal", 0) > 0:
        prompt_lines.extend([
            "VISIBLE DOG LOCK:" if animal_species_locks.get("animal") == "dog" else "ANIMAL LOCK:",
            "- one dog must be clearly visible in the final frame" if animal_species_locks.get("animal") == "dog" else "- animal must appear clearly in frame",
            "- the dog must be in foreground or midground" if animal_species_locks.get("animal") == "dog" else "- if animal role is connected, animal must be visible in frame",
            "- the dog must not be cropped out" if animal_species_locks.get("animal") == "dog" else "- animal must remain fully readable in frame",
            "- the dog must match the attached animal reference" if animal_species_locks.get("animal") == "dog" else "- animal must match its attached reference exactly",
            "- do not omit the dog even in tight framing" if animal_species_locks.get("animal") == "dog" else "- do not omit or replace the animal",
        ])
    context_lines: list[str] = []
    if title_context:
        context_lines.append(f"Title context: {title_context}")
    if story_context:
        context_lines.append(f"Story context: {story_context}")
        context_lines.append(f"Opening beats / location source of truth: {story_context}")
    if source_node_types:
        context_lines.append(f"Connected source node types: {', '.join(source_node_types)}")
    if connected_roles:
        context_lines.append(
            "Connected reference roles: "
            + ", ".join(f"{_intro_role_label(role)} ({len(connected_refs_by_role.get(role) or [])})" for role in connected_roles)
        )
    if connected_cast_roles:
        context_lines.append("Cast package roles: " + ", ".join(_intro_role_label(role) for role in connected_cast_roles))
    if connected_world_roles:
        context_lines.append("World anchors: " + ", ".join(_intro_role_label(role) for role in connected_world_roles))
    if connected_prop_roles:
        context_lines.append("Key prop anchors are connected and should be visually integrated when relevant.")
    if role_aware_cast_summary:
        context_lines.append(f"Role-aware cast summary: {role_aware_cast_summary}")
    if hero_participants_resolved:
        context_lines.append("Hero participants resolved: " + ", ".join(hero_participants_resolved))
    if important_props:
        context_lines.append(f"Important props: {', '.join(important_props)}")
    if world_context:
        context_lines.append(f"World context: {world_context}")
    if story_summary:
        context_lines.append(f"Story summary (source-of-truth): {story_summary}")
    if preview_prompt:
        context_lines.append(f"Preview prompt (source-of-truth): {preview_prompt}")
    if world:
        context_lines.append(f"World (source-of-truth): {world}")
    if roles:
        context_lines.append(f"Scenario roles (source-of-truth): {', '.join(roles)}")
    if tone_style_direction:
        context_lines.append(f"Tone/style direction (source-of-truth): {tone_style_direction}")
    if style_context:
        context_lines.append(f"Style context: {style_context}")
    if role_gender_locks:
        prompt_lines.append("CONNECTED GENDER LOCKS:")
        for role, gender_value in role_gender_locks.items():
            if gender_value == "female":
                prompt_lines.extend([
                    f"- {_intro_role_label(role)} must remain a distinct female character",
                    "- no male replacement",
                ])
                if connected_ref_counts.get(role, 0) > 0:
                    prompt_lines.append(f"- use connected {_intro_role_label(role)} references as exact female identity anchor")
            elif gender_value == "male":
                prompt_lines.extend([
                    f"- {_intro_role_label(role)} must remain a distinct male character",
                    "- no female replacement",
                ])
                if connected_ref_counts.get(role, 0) > 0:
                    prompt_lines.append(f"- use connected {_intro_role_label(role)} references as exact male identity anchor")
    if animal_species_locks:
        prompt_lines.append("CONNECTED SPECIES LOCKS:")
        for role, species_value in animal_species_locks.items():
            prompt_lines.append(f"- {_intro_role_label(role)} must appear as {species_value}")
            if role == "animal" and species_value == "dog":
                prompt_lines.extend([
                    "- render a dog, not any other species",
                    "- no cat",
                    "- no wolf",
                ])
            else:
                prompt_lines.extend([
                    f"- preserve {_intro_role_label(role)} species lock exactly",
                ])
            if connected_ref_counts.get(role, 0) > 0:
                prompt_lines.append(f"- use connected {_intro_role_label(role)} references as exact {species_value} identity anchor")
    if len(female_locked_roles) >= 2:
        prompt_lines.extend([
            "FEMALE IDENTITY LOCK:",
            "- render two distinct female characters",
            "- if 2 female roles are connected, render exactly 2 distinct women matching their refs",
            "- both must match their reference images",
            "- do not change gender",
        ])
    elif len(female_roles_with_refs) == 1:
        prompt_lines.extend([
            "FEMALE IDENTITY LOCK:",
            "- preserve the connected female participant exactly",
            "- do not replace with a generic woman",
            "- do not change gender",
        ])
    if strict_identity_package_roles:
        prompt_lines.append(
            "STRICT CAST PACKAGE SUMMARY: "
            + ", ".join(
                f"{_intro_role_label(role)} uses {connected_ref_counts.get(role, 0)} connected reference image(s)"
                for role in strict_identity_package_roles
            )
        )
    negative_rule_lines = [
        "INTRO FRAME FORBIDDEN ELEMENTS:",
        *[f"- {rule}" for rule in style_meta["negativeRules"]],
        _comfy_text_rendering_block(allow_designed_text=False),
    ]
    if scene_count > 0:
        prompt_lines.append(f"Storyboard scene count: {scene_count}")
    prompt_lines.append(f"Intended intro duration reference: {duration_sec:.1f} seconds.")

    final_prompt_lines: list[str] = []
    final_prompt_parts = [
        base_thumbnail_prompt_lines,
        style_fragment_lines,
        style_rule_lines,
        composition_lines,
        hook_rule_lines,
        text_rule_lines,
        text_placement_intelligence_rule_lines,
        contrast_rule_lines,
        frame_adjustment_rule_lines,
        emotion_boost_rule_lines,
        focus_rule_lines,
        context_lines,
        prompt_lines,
        negative_rule_lines,
    ]
    _append_unique_prompt_lines(final_prompt_lines, *final_prompt_parts)
    prompt = "\n".join(line for line in final_prompt_lines if str(line or "").strip())
    debug = {
        "rawConnectedRefsByRoleCounts": {role: len(connected_refs_by_role.get(role) or []) for role in connected_refs_by_role},
        "title": title,
        "manualTitleRaw": manual_title_raw or original_title,
        "originalTitle": original_title,
        "previewTitleUsed": original_title,
        "stylePreset": style_preset,
        "styleKey": style_preset,
        "previewFormat": preview_format,
        "sceneCount": scene_count,
        "durationSec": round(duration_sec, 1),
        "compositionMode": composition_plan["mode"],
        "hookUsed": title_transformed,
        "titleTransformed": title_transformed,
        "hookPatternUsed": hook_pattern_used,
        "textPlacementMode": "auto_prompted",
        "frameAdjustmentEnabled": True,
        "emotionBoostEnabled": True,
        "subjectWeight": composition_plan["weights"].get("subject"),
        "textLinesCount": len(text_line_layout) if text_line_layout else 1,
        "splitTitleLines": text_line_layout,
        "compositionFocusTargets": composition_plan["focusTargets"],
        "compositionWeights": composition_plan["weights"],
        "sourceNodeTypes": source_node_types,
        "connectedRoles": connected_roles,
        "connectedRoleCounts": intro_connected_role_counts,
        "connectedRefCounts": connected_ref_counts,
        "roleAwareCastSummary": role_aware_cast_summary or None,
        "heroParticipants": hero_participants,
        "supportingParticipants": supporting_participants,
        "importantProps": important_props,
        "worldContext": world_context or None,
        "styleContext": style_context or None,
        "storySummary": story_summary or None,
        "previewPrompt": preview_prompt or None,
        "world": world or None,
        "roles": roles,
        "toneStyleDirection": tone_style_direction or None,
        "styleLabel": style_meta["label"],
        "styleDescription": style_meta["shortDescription"],
        "styleCompositionBias": style_meta["compositionBias"],
        "stylePalette": style_meta["palette"],
        "styleMood": style_meta["mood"],
        "styleTextTreatment": style_meta["textTreatment"],
        "styleGraphicAccentsPreference": style_meta["graphicAccentsPreference"],
        "styleOverlays": style_meta["overlays"],
        "stylePromptFragment": style_meta["promptFragment"],
        "styleRules": style_meta["promptRules"],
        "negativeRules": style_meta["negativeRules"],
        "forbidden": style_meta["negativeRules"],
        "introActiveCastRoles": intro_active_cast_roles,
        "introMustAppear": intro_must_appear,
        "introMustNotAppear": intro_must_not_appear,
        "introConnectedRoleCounts": {role: intro_connected_role_counts.get(role, 0) for role in intro_active_cast_roles},
        "introRoleGenderLocks": role_gender_locks,
        "introAnimalSpeciesLocks": animal_species_locks,
        "introHeroParticipantsResolved": hero_participants_resolved,
        "introCastContractPreview": intro_cast_contract_preview,
        "introStrictIdentityPackageRoles": strict_identity_package_roles,
        "introStrictIdentityPackageSummary": strict_identity_package_summary,
        "promptPreview": prompt[:1600],
    }
    return prompt, debug


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


def _resolve_audio_slice_source(audio_url: str, temp_files: list[str]) -> tuple[str | None, str | None]:
    resolved_path = _resolve_audio_asset_path(audio_url)
    if resolved_path:
        return resolved_path, None
    return _resolve_media_input(audio_url, temp_files)


def _ffmpeg_audio_slice(input_path: str, output_path: str, t0: float, t1: float) -> tuple[bool, str]:
    dur = max(0.0, t1 - t0)
    if dur < 0.05:
        dur = 0.05

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-ss", f"{t0:.3f}",
        "-t", f"{dur:.3f}",
        "-vn",
        "-map", "a:0?",
        "-acodec", "libmp3lame",
        "-b:a", "192k",
        output_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return False, "ffmpeg_missing_install_and_add_to_PATH"

    if proc.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 1024:
        return True, ""

    err = (proc.stderr or proc.stdout or "ffmpeg_failed").strip()
    return False, err[:500]


def _run_ffmpeg(cmd: list[str]) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        return False, "ffmpeg_missing_install_and_add_to_PATH"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout or "ffmpeg_failed").strip()[:500]
    return True, ""


def _ffprobe_duration(path: str) -> tuple[float | None, str | None]:
    try:
        proc = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None, "ffprobe_missing_install_and_add_to_PATH"
    if proc.returncode != 0:
        return None, (proc.stderr or proc.stdout or "ffprobe_failed").strip()[:500]
    try:
        duration = float((proc.stdout or "").strip())
    except Exception:
        return None, "ffprobe_duration_parse_failed"
    if duration <= 0:
        return None, "ffprobe_duration_non_positive"
    return duration, None


def _resolve_media_input(url: str, temp_files: list[str]) -> tuple[str | None, str | None]:
    source = str(url or "").strip()
    if not source:
        return None, "media_url_empty"

    def _resolve_static_path(path_value: str) -> str | None:
        if path_value.startswith("/static/assets/"):
            name = os.path.basename(path_value[len("/static/assets/"):])
        elif path_value.startswith("/assets/"):
            name = os.path.basename(path_value[len("/assets/"):])
        elif path_value.startswith("static/assets/"):
            name = os.path.basename(path_value[len("static/assets/"):])
        else:
            return None
        if not name:
            return None
        candidate = os.path.join(str(ASSETS_DIR), name)
        if os.path.isfile(candidate):
            return candidate
        return None

    if os.path.isfile(source):
        return source, None

    if source.startswith("data:"):
        try:
            header, encoded = source.split(",", 1)
        except ValueError:
            return None, "invalid_data_url"
        if not header.lower().startswith("data:"):
            return None, "invalid_data_url_header"
        metadata = header[5:]
        metadata_parts = [part.strip() for part in metadata.split(";")]
        mime_type = "application/octet-stream"
        is_base64 = False
        if metadata_parts:
            first_part = metadata_parts[0]
            if first_part:
                mime_type = first_part.strip().lower()
            for part in metadata_parts[1:]:
                if part.lower() == "base64":
                    is_base64 = True
        if "/" not in mime_type:
            mime_type = "application/octet-stream"
        extension = mimetypes.guess_extension(mime_type)
        if not extension and mime_type == "image/svg+xml":
            extension = ".svg"
        if not extension:
            extension = ".bin"
        fd, temp_path = tempfile.mkstemp(prefix="clip_assemble_data_", suffix=extension)
        os.close(fd)
        try:
            payload = encoded.encode("utf-8")
            data = base64.b64decode(payload) if is_base64 else requests.utils.unquote_to_bytes(encoded)
            with open(temp_path, "wb") as f:
                f.write(data)
        except Exception:
            try:
                if os.path.isfile(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass
            return None, "invalid_data_url_payload"
        temp_files.append(temp_path)
        return temp_path, None

    parsed = urlparse(source)
    path = parsed.path or ""
    static_candidate = _resolve_static_path(path or source)
    if static_candidate:
        return static_candidate, None

    is_http = parsed.scheme in {"http", "https"}
    if not is_http:
        return None, "unsupported_media_url"

    try:
        response = requests.get(source, timeout=60)
        response.raise_for_status()
    except RequestException as exc:
        return None, f"download_failed:{str(exc)[:220]}"

    suffix = os.path.splitext(path)[1] or ".bin"
    fd, temp_path = tempfile.mkstemp(prefix="clip_assemble_", suffix=suffix)
    os.close(fd)
    with open(temp_path, "wb") as f:
        f.write(response.content)
    temp_files.append(temp_path)
    return temp_path, None


def _build_public_static_url(filename: str) -> str:
    return _asset_url(filename)


def _resolve_assembly_video_geometry(format_value: str | None) -> tuple[int, int]:
    normalized = str(format_value or "9:16").strip()
    if normalized == "1:1":
        return 1024, 1024
    if normalized == "16:9":
        return 1344, 768
    return 1024, 1820


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
                if not isinstance(part, dict):
                    continue
                inline = part.get("inlineData") or part.get("inline_data") or {}
                b64 = inline.get("data")
                mime = (inline.get("mimeType") or inline.get("mime_type") or "image/png").lower()
                if isinstance(b64, str) and b64:
                    raw = base64.b64decode(b64)
                    ext = "jpg" if "jpeg" in mime or "jpg" in mime else "png"
                    return raw, ext
                # Some wrappers place raw base64 directly under part.data.
                direct_b64 = part.get("data")
                if isinstance(direct_b64, str) and direct_b64:
                    raw = base64.b64decode(direct_b64)
                    return raw, "png"
    except Exception:
        return None
    return None


def _summarize_gemini_image_response(resp: dict) -> dict:
    candidates = resp.get("candidates") or []
    image_part_count = 0
    text_part_count = 0
    file_part_count = 0
    finish_reasons = []
    for cand in candidates:
        if not isinstance(cand, dict):
            continue
        finish_reason = str(cand.get("finishReason") or cand.get("finish_reason") or "").strip()
        if finish_reason:
            finish_reasons.append(finish_reason)
        content = cand.get("content") or {}
        for part in (content.get("parts") or []):
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data") or {}
            if isinstance(inline, dict) and inline.get("data"):
                image_part_count += 1
            if isinstance(part.get("text"), str) and part.get("text").strip():
                text_part_count += 1
            file_data = part.get("fileData") or part.get("file_data") or {}
            if isinstance(file_data, dict) and (file_data.get("fileUri") or file_data.get("file_uri")):
                file_part_count += 1

    return {
        "httpError": bool(resp.get("__http_error__")),
        "status": resp.get("status"),
        "candidateCount": len(candidates),
        "imagePartCount": image_part_count,
        "textPartCount": text_part_count,
        "filePartCount": file_part_count,
        "finishReasons": finish_reasons,
    }


@router.post("/clip/intro/generate")
def clip_intro_generate(payload: IntroGenerateIn):
    style_preset = _normalize_intro_style_preset(payload.stylePreset)
    preview_format = _normalize_intro_preview_format(payload.previewFormat)
    prompt, debug = _build_intro_frame_prompt(payload)
    title = str(debug.get("title") or buildHookTitle(_normalize_intro_title_input(payload.title) or "Intro frame", style_preset))
    width, height = _resolve_intro_preview_dimensions(preview_format)
    connected_refs_by_role = _normalize_intro_connected_refs_by_role(getattr(payload, "connectedRefsByRole", None))
    raw_connected_ref_counts = {role: len(connected_refs_by_role.get(role) or []) for role in COMFY_REF_ROLES}
    total_connected_refs = sum(raw_connected_ref_counts.values())
    refs_pipeline_warning = "intro went without refs: connectedRefsByRole is empty, pipeline should be checked" if total_connected_refs <= 0 else ""
    print("[INTRO FRAME REFS RECEIVED] " + json.dumps({
        "rawConnectedRefsByRoleCounts": raw_connected_ref_counts,
        "refsReceivedByRole": raw_connected_ref_counts,
        "totalConnectedRefs": total_connected_refs,
    }, ensure_ascii=False))
    if total_connected_refs <= 0:
        print("[INTRO FRAME WARNING] " + json.dumps({
            "message": refs_pipeline_warning,
            "rawConnectedRefsByRoleCounts": raw_connected_ref_counts,
            "refsReceivedByRole": raw_connected_ref_counts,
            "totalConnectedRefs": total_connected_refs,
        }, ensure_ascii=False))
    inline_parts, inline_part_counts, inline_part_debug = _build_intro_reference_inline_parts(connected_refs_by_role)
    print("[INTRO FRAME INLINE IMAGES] " + json.dumps({
        "rawConnectedRefsByRoleCounts": raw_connected_ref_counts,
        "refsReceivedByRole": raw_connected_ref_counts,
        "inlineImagesAttachedByRole": inline_part_counts,
        "attachedInlineReferenceRoles": inline_part_debug.get("attachedInlineReferenceRoles") or [],
        "attachedInlineImageTotal": len(inline_parts),
        "totalInlineImages": inline_part_debug.get("totalInlineImages") or len(inline_parts),
        "character1RefAttached": bool(inline_part_debug.get("character1RefAttached")),
        "character2RefAttached": bool(inline_part_debug.get("character2RefAttached")),
        "animalRefAttached": bool(inline_part_debug.get("animalRefAttached")),
    }, ensure_ascii=False))
    print("[INTRO PREVIEW BACKEND] " + json.dumps({
        "refsReceivedByRole": raw_connected_ref_counts,
        "heroParticipants": debug.get("heroParticipants") or [],
        "introMustAppear": debug.get("introMustAppear") or [],
        "hasStorySummary": bool(debug.get("storySummary")),
        "hasPreviewPrompt": bool(debug.get("previewPrompt")),
        "hasWorld": bool(debug.get("world")),
        "hasRoles": bool(debug.get("roles")),
        "hasToneStyleDirection": bool(debug.get("toneStyleDirection")),
    }, ensure_ascii=False))
    api_key = (settings.GEMINI_API_KEY or "").strip()
    if not api_key:
        return JSONResponse(
            status_code=503,
            content={
                "ok": False,
                "code": "GEMINI_API_KEY_MISSING",
                "hint": "gemini_api_key_missing_for_intro_generation",
            },
        )

    model = settings.GEMINI_IMAGE_MODEL or "gemini-2.5-flash-image-preview"
    body = {
        "contents": [{
            "role": "user",
            "parts": [*inline_parts, {"text": prompt}],
        }],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }

    try:
        print(
            "[INTRO FRAME CAST] "
            + json.dumps(
                {
                    "introActiveCastRoles": debug.get("introActiveCastRoles") or [],
                    "introMustAppear": debug.get("introMustAppear") or [],
                    "introMustNotAppear": debug.get("introMustNotAppear") or [],
                    "introRoleGenderLocks": debug.get("introRoleGenderLocks") or {},
                    "introAnimalSpeciesLocks": debug.get("introAnimalSpeciesLocks") or {},
                    "rawConnectedRefsByRoleCounts": raw_connected_ref_counts,
                    "connectedRefCounts": debug.get("connectedRefCounts") or {},
                    "refsReceivedByRole": raw_connected_ref_counts,
                    "attachedInlineReferenceRoles": inline_part_debug.get("attachedInlineReferenceRoles") or [],
                    "rolesWithImageParts": inline_part_debug.get("rolesWithImageParts") or [],
                    "roleAttachedImageCounts": inline_part_debug.get("roleAttachedImageCounts") or {},
                    "totalInlineImages": inline_part_debug.get("totalInlineImages") or len(inline_parts),
                    "dogRoleAttached": bool(inline_part_debug.get("dogRoleAttached")),
                    "animalRefAttached": bool(inline_part_debug.get("animalRefAttached")),
                    "character1RefAttached": bool(inline_part_debug.get("character1RefAttached")),
                    "character2RefAttached": bool(inline_part_debug.get("character2RefAttached")),
                    "introWentWithoutRefs": total_connected_refs <= 0,
                },
                ensure_ascii=False,
            )
        )
        print(
            "[INTRO FRAME PROMPT DEBUG] "
            + json.dumps(
                {
                    "rawConnectedRefsByRoleCounts": debug.get("rawConnectedRefsByRoleCounts") or raw_connected_ref_counts,
                    "attachedInlineReferenceRoles": inline_part_debug.get("attachedInlineReferenceRoles") or [],
                    "totalInlineImages": inline_part_debug.get("totalInlineImages") or len(inline_parts),
                    "introMustAppear": debug.get("introMustAppear") or [],
                    "introActiveCastRoles": debug.get("introActiveCastRoles") or [],
                    "promptPreview": debug.get("promptPreview") or "",
                },
                ensure_ascii=False,
            )
        )
        print(
            "[INTRO FRAME REFS DEBUG] "
            + json.dumps(
                {
                    "rawConnectedRefsByRoleCounts": raw_connected_ref_counts,
                    "refsReceivedByRole": raw_connected_ref_counts,
                    "inlineImagesAttachedByRole": inline_part_counts,
                    "attachedInlineReferenceRoles": inline_part_debug.get("attachedInlineReferenceRoles") or [],
                    "roleAttachedImageCounts": inline_part_debug.get("roleAttachedImageCounts") or {},
                    "totalInlineImages": inline_part_debug.get("totalInlineImages") or len(inline_parts),
                    "character1RefAttached": bool(inline_part_debug.get("character1RefAttached")),
                    "character2RefAttached": bool(inline_part_debug.get("character2RefAttached")),
                    "rolesWithImageParts": inline_part_debug.get("rolesWithImageParts") or [],
                    "animalRefAttached": bool(inline_part_debug.get("animalRefAttached")),
                    "introActiveCastRoles": debug.get("introActiveCastRoles") or [],
                    "introMustAppear": debug.get("introMustAppear") or [],
                    "introMustNotAppear": debug.get("introMustNotAppear") or [],
                    "introWentWithoutRefs": total_connected_refs <= 0,
                    "refsPipelineWarning": refs_pipeline_warning or None,
                },
                ensure_ascii=False,
            )
        )
        print(
            "[INTRO FRAME GEMINI] request",
            json.dumps(
                {
                    "manualTitleRaw": debug.get("manualTitleRaw") or "",
                    "previewTitleUsed": debug.get("previewTitleUsed") or "",
                    "title": title,
                    "stylePreset": style_preset,
                    "previewFormat": preview_format,
                    "width": width,
                    "height": height,
                    "sceneCount": debug.get("sceneCount"),
                    "rawConnectedRefsByRoleCounts": raw_connected_ref_counts,
                    "connectedRefCounts": debug.get("connectedRefCounts") or {},
                    "refsReceivedByRole": raw_connected_ref_counts,
                    "inlineImagesAttachedByRole": inline_part_counts,
                    "attachedReferenceParts": inline_part_counts,
                    "attachedReferencePartTotal": len(inline_parts),
                    "attachedInlineReferenceRoles": inline_part_debug.get("attachedInlineReferenceRoles") or [],
                    "rolesWithImageParts": inline_part_debug.get("rolesWithImageParts") or [],
                    "roleAttachedImageCounts": inline_part_debug.get("roleAttachedImageCounts") or {},
                    "totalInlineImages": inline_part_debug.get("totalInlineImages") or len(inline_parts),
                    "dogRoleAttached": bool(inline_part_debug.get("dogRoleAttached")),
                    "animalRefAttached": bool(inline_part_debug.get("animalRefAttached")),
                    "character1RefAttached": bool(inline_part_debug.get("character1RefAttached")),
                    "character2RefAttached": bool(inline_part_debug.get("character2RefAttached")),
                    "introActiveCastRoles": debug.get("introActiveCastRoles") or [],
                    "introMustAppear": debug.get("introMustAppear") or [],
                    "introMustNotAppear": debug.get("introMustNotAppear") or [],
                    "introWentWithoutRefs": total_connected_refs <= 0,
                    "refsPipelineWarning": refs_pipeline_warning or None,
                    "model": model,
                },
                ensure_ascii=False,
            ),
        )
        resp = post_generate_content(api_key, model, body, timeout=120)
        resp_dict = resp if isinstance(resp, dict) else {}
        response_summary = _summarize_gemini_image_response(resp_dict)
        print("[INTRO FRAME GEMINI] response summary=" + json.dumps(response_summary, ensure_ascii=False))
        decoded = _decode_gemini_image(resp_dict)
        if not decoded:
            return JSONResponse(
                status_code=502,
                content={
                    "ok": False,
                    "code": "INTRO_IMAGE_GENERATION_FAILED",
                    "hint": "gemini_returned_no_image_for_intro_frame",
                    "stylePreset": style_preset,
                    "previewFormat": preview_format,
                    "debug": {
                        **debug,
                        "rawConnectedRefsByRoleCounts": raw_connected_ref_counts,
                        "refsReceivedByRole": raw_connected_ref_counts,
                        "inlineImagesAttachedByRole": inline_part_counts,
                        "responseSummary": response_summary,
                        "attachedReferenceParts": inline_part_counts,
                        "attachedReferencePartTotal": len(inline_parts),
                        "attachedInlineReferenceRoles": inline_part_debug.get("attachedInlineReferenceRoles") or [],
                        "totalInlineImages": inline_part_debug.get("totalInlineImages") or len(inline_parts),
                        "animalRefAttached": bool(inline_part_debug.get("animalRefAttached")),
                        "dogRoleAttached": bool(inline_part_debug.get("dogRoleAttached")),
                        "character1RefAttached": bool(inline_part_debug.get("character1RefAttached")),
                        "character2RefAttached": bool(inline_part_debug.get("character2RefAttached")),
                        "introWentWithoutRefs": total_connected_refs <= 0,
                        "refsPipelineWarning": refs_pipeline_warning or None,
                    },
                },
            )

        raw, ext = decoded
        branded, overlay_debug = _render_intro_frame_asset(raw, title=title, style_preset=style_preset, preview_format=preview_format)
        image_url = _save_bytes_as_asset(branded, "png")
        generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        return {
            "ok": True,
            "imageUrl": image_url,
            "title": title,
            "stylePreset": style_preset,
            "previewFormat": preview_format,
            "generatedAt": generated_at,
            "engine": "gemini",
            "modelUsed": model,
            "debug": {
                **debug,
                "rawConnectedRefsByRoleCounts": raw_connected_ref_counts,
                "refsReceivedByRole": raw_connected_ref_counts,
                "inlineImagesAttachedByRole": inline_part_counts,
                "responseSummary": response_summary,
                "attachedReferenceParts": inline_part_counts,
                "attachedReferencePartTotal": len(inline_parts),
                "attachedInlineReferenceRoles": inline_part_debug.get("attachedInlineReferenceRoles") or [],
                "totalInlineImages": inline_part_debug.get("totalInlineImages") or len(inline_parts),
                "animalRefAttached": bool(inline_part_debug.get("animalRefAttached")),
                "dogRoleAttached": bool(inline_part_debug.get("dogRoleAttached")),
                "character1RefAttached": bool(inline_part_debug.get("character1RefAttached")),
                "character2RefAttached": bool(inline_part_debug.get("character2RefAttached")),
                "introWentWithoutRefs": total_connected_refs <= 0,
                "refsPipelineWarning": refs_pipeline_warning or None,
                "backendBrandedAsset": True,
                "overlay": overlay_debug,
                "overlay.mainTitleRenderedBy": overlay_debug.get("mainTitleRenderedBy"),
                "overlay.brandingOverlayApplied": bool(overlay_debug.get("brandingOverlayApplied")),
                "overlay.footerOverlayApplied": bool(overlay_debug.get("footerOverlayApplied")),
            },
        }
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "code": "INTRO_IMAGE_GENERATION_FAILED",
                "hint": str(exc)[:300],
                "stylePreset": style_preset,
                "previewFormat": preview_format,
                "debug": {
                    **debug,
                    "rawConnectedRefsByRoleCounts": raw_connected_ref_counts,
                    "refsReceivedByRole": raw_connected_ref_counts,
                    "inlineImagesAttachedByRole": inline_part_counts,
                    "attachedReferenceParts": inline_part_counts,
                    "attachedReferencePartTotal": len(inline_parts),
                    "attachedInlineReferenceRoles": inline_part_debug.get("attachedInlineReferenceRoles") or [],
                    "totalInlineImages": inline_part_debug.get("totalInlineImages") or len(inline_parts),
                    "animalRefAttached": bool(inline_part_debug.get("animalRefAttached")),
                    "dogRoleAttached": bool(inline_part_debug.get("dogRoleAttached")),
                    "character1RefAttached": bool(inline_part_debug.get("character1RefAttached")),
                    "character2RefAttached": bool(inline_part_debug.get("character2RefAttached")),
                    "introWentWithoutRefs": total_connected_refs <= 0,
                    "refsPipelineWarning": refs_pipeline_warning or None,
                },
            },
        )


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




def _adapt_outfit_prompt_for_character_refs(text: str, *, has_character_refs: bool) -> str:
    value = str(text or "").strip()
    if not value or not has_character_refs:
        return value

    adapted = value
    # Prefer visual character reference for wardrobe identity instead of text color guesses.
    adapted = re.sub(
        r"\bwearing\s+(?:a|an|the)?\s*(?:[a-z]+\s+){0,4}(tracksuit|hoodie|jacket|sportswear|outfit|suit)\b",
        r"wearing the same \1 from the character reference",
        adapted,
        flags=re.IGNORECASE,
    )

    if has_character_refs and "preserve the exact outfit color" not in adapted.lower():
        adapted = (
            f"{adapted}. preserve the exact outfit color, material, and logo placement from the character reference"
            if adapted
            else "preserve the exact outfit color, material, and logo placement from the character reference"
        )
    return re.sub(r"\s+", " ", adapted).strip()

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


def _extract_semantic_whitelist(*, text: str, session_world_anchors: dict[str, str], location_refs: list[str], props_refs: list[str]) -> dict[str, Any]:
    token_re = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_\-]+")
    concept_cues: dict[str, tuple[str, ...]] = {
        "sky": ("sky", "небо", "horizon", "гориз", "cloud", "облак"),
        "night": ("night", "ноч", "moon", "луна", "star", "звезд"),
        "wind": ("wind", "ветер", "breeze"),
        "open_space": ("open", "простор", "field", "поле", "distance", "даль"),
        "emotion": ("love", "люб", "heart", "сердц", "emotion", "чувств"),
        "light": ("light", "свет", "sun", "солн", "dawn", "закат"),
        "city": ("city", "город", "street", "улиц", "stage", "сцен"),
        "nature": ("mountain", "гор", "forest", "лес", "sea", "море", "water", "вода"),
    }
    text_l = (text or "").lower()
    concepts: list[str] = []
    for concept, cues in concept_cues.items():
        if any(c in text_l for c in cues):
            concepts.append(concept)

    anchor_tokens: set[str] = set()
    for anchor_value in session_world_anchors.values():
        for token in token_re.findall(str(anchor_value or "").lower()):
            if len(token) >= 4:
                anchor_tokens.add(token)
    for ref_url in [*(location_refs or []), *(props_refs or [])]:
        tail = str(ref_url or "").split("/")[-1].split("?")[0].replace("-", " ").replace("_", " ")
        for token in token_re.findall(tail.lower()):
            if len(token) >= 4:
                anchor_tokens.add(token)

    return {
        "concepts": concepts,
        "anchorTokens": sorted(anchor_tokens),
    }


def _tokenize_semantic(text: str) -> set[str]:
    tokens = re.findall(r"[a-zA-Zа-яА-ЯёЁ0-9]+", str(text or "").lower())
    stopwords = {
        "the", "and", "or", "in", "on", "at", "to", "of", "for", "with", "from", "into",
        "same", "world", "location", "environment", "scene", "shot", "cinematic", "main",
        "a", "an", "is", "are", "this", "that", "through", "over", "under", "by",
        "и", "в", "на", "с", "по", "к", "у", "за", "для", "из", "под", "над", "как", "что",
    }
    return {t for t in tokens if len(t) > 2 and t not in stopwords}


def _scene_semantic_guardrail(
    *,
    scene: dict,
    semantic_whitelist: dict[str, Any],
    source_text: str,
    session_world_anchors: dict[str, str],
    prop_anchor_label: str,
) -> tuple[dict, bool]:
    banned_terms = [
        "microchip", "microchips", "chipset", "motherboard", "circuit", "pcb", "silicon",
        "server rack", "datacenter", "data center", "cyberpunk computer", "quantum processor",
    ]
    token_space = " ".join([
        str(scene.get("visualDescription") or ""),
        str(scene.get("visualPrompt") or ""),
        str(scene.get("reason") or ""),
        str(scene.get("environment") or ""),
        str(scene.get("sceneNarrative") or ""),
    ]).lower()

    concept_token_map: dict[str, tuple[str, ...]] = {
        "sky": ("sky", "cloud", "horizon", "небо", "обла", "гориз"),
        "night": ("night", "moon", "star", "ноч", "луна", "звезд"),
        "wind": ("wind", "breeze", "ветер"),
        "open_space": ("field", "distance", "wide landscape", "horizon", "поле", "даль", "простор"),
        "emotion": ("emotion", "feeling", "lonely", "heart", "чувств", "одинок", "сердц"),
        "light": ("light", "sun", "glow", "свет", "солн", "сия"),
        "city": ("city", "street", "stage", "город", "улиц", "сцен"),
        "nature": ("mountain", "forest", "sea", "water", "гор", "лес", "море", "вода"),
    }
    tech_terms = [
        "robot", "android", "cyberpunk", "hologram", "computer", "terminal", "server", "processor",
        "interface", "digital grid", "matrix", "virtual reality",
    ]

    grounding_text = " ".join([
        (source_text or "").lower(),
        " ".join(str(v or "").lower() for v in (session_world_anchors or {}).values()),
        str(prop_anchor_label or "").lower(),
        " ".join((semantic_whitelist or {}).get("anchorTokens") or []),
    ])
    source_text_l = (source_text or "").lower()

    found_ungrounded = [term for term in banned_terms if term in token_space and term not in grounding_text]
    fallback_reason: str | None = None
    if found_ungrounded:
        fallback_reason = "banned_object"

    if fallback_reason is None:
        scene_environment_tokens = _tokenize_semantic(str(scene.get("environment") or ""))
        anchor_environment_tokens = _tokenize_semantic(
            " ".join([
                str((session_world_anchors or {}).get("location") or ""),
                str((session_world_anchors or {}).get("style") or ""),
            ])
        )
        if scene_environment_tokens and anchor_environment_tokens:
            has_environment_overlap = any(
                s == a or s.startswith(a) or a.startswith(s) or s in a or a in s
                for s in scene_environment_tokens
                for a in anchor_environment_tokens
            )
            if not has_environment_overlap:
                fallback_reason = "ungrounded_environment"

    if fallback_reason is None:
        concepts = [str(c).strip().lower() for c in (semantic_whitelist or {}).get("concepts") or [] if str(c).strip()]
        if concepts:
            concept_grounded = False
            for concept in concepts:
                for cue in concept_token_map.get(concept, (concept,)):
                    if cue and cue in token_space:
                        concept_grounded = True
                        break
                if concept_grounded:
                    break
            if not concept_grounded:
                fallback_reason = "domain_mismatch"

    if fallback_reason is None:
        scene_has_tech = any(term in token_space for term in tech_terms)
        lyrics_has_tech_cues = any(
            cue in source_text_l
            for cue in [*tech_terms, "technology", "tech", "digital", "cyber", "computer", "robot"]
        )
        if scene_has_tech and not lyrics_has_tech_cues:
            fallback_reason = "domain_mismatch"

    if fallback_reason is None:
        return scene, False

    fallback_environment = str(session_world_anchors.get("location") or session_world_anchors.get("style") or "same main location with continuity")
    fallback_narrative = f"The scene stays in the same world location ({fallback_environment}) and continues the emotional moment from the lyrics."
    patched = dict(scene)
    patched["visualDescription"] = fallback_narrative
    patched["reason"] = f"Semantic fallback ({fallback_reason}): keep location and emotional continuity from lyrics."
    patched["visualPrompt"] = f"cinematic shot in {fallback_environment}, consistent world continuity, no unrelated objects"
    patched["environment"] = fallback_environment
    patched["semanticGuardrailTriggered"] = True
    patched["semanticGuardrailReason"] = fallback_reason
    if not str(patched.get("sceneNarrative") or "").strip():
        patched["sceneNarrative"] = fallback_narrative
    if not str(patched.get("sceneGoal") or "").strip():
        patched["sceneGoal"] = "Maintain the same story world while advancing the lyric emotion"
    if not str(patched.get("characterAction") or "").strip():
        patched["characterAction"] = "continues the emotional action introduced in the previous scene"
    if not str(patched.get("cameraMotion") or "").strip():
        patched["cameraMotion"] = "alternate cinematic angle in the same environment"
    return patched, True


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

    semantic_whitelist = _extract_semantic_whitelist(
        text=text,
        session_world_anchors=session_world_anchors,
        location_refs=location_refs,
        props_refs=props_refs,
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

SEMANTIC WHITELIST RESTRICTION (CLIP MODE):
Build a semantic whitelist from lyrics/audio semantics and text cues (for example: sky, night, wind, open space, emotion, distance, light, horizon).
Every scene environment/object choice must be grounded in at least one of:
- lyrics/text meaning
- character/location/style/props references
- current session world anchors
Reject ungrounded elements.

SEMANTIC CONSISTENCY CHECK (PER SCENE):
Before finalizing each scene, verify scene objects and environment stay within the extracted semantic domain.
Invalid example: sky/night/love lyrics + microchips/server racks/cyberpunk computers with no grounding source.
Valid example: sky, clouds, night city lights, open field, mountain horizon, stage under open sky.

HALLUCINATION PREVENTION / FALLBACK RULE:
If environment cannot be inferred confidently from lyrics/references, do not invent unrelated worlds.
Fallback to the main location with variations in lighting, framing, camera distance, and character action.
Keep same world, same props, and same narrative continuity unless lyrics explicitly imply transformation/metaphor.

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
    "sceneGoal": string,
    "sceneNarrative": string,
    "characterAction": string,
    "cameraMotion": string,
    "environment": string,
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
        "semanticWhitelist": semantic_whitelist,
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
            if not str(scene.get("sceneGoal") or scene.get("shotPurpose") or "").strip():
                return False, f"scene_{idx}_sceneGoal_missing"
            if not str(scene.get("sceneNarrative") or scene.get("visualDescription") or "").strip():
                return False, f"scene_{idx}_sceneNarrative_missing"
            if not str(scene.get("characterAction") or scene.get("motion") or "").strip():
                return False, f"scene_{idx}_characterAction_missing"
            if not str(scene.get("cameraMotion") or scene.get("camera") or "").strip():
                return False, f"scene_{idx}_cameraMotion_missing"
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
        scene_goal = str(s.get("sceneGoal") or s.get("shotPurpose") or "").strip()
        scene_narrative = str(s.get("sceneNarrative") or s.get("visualDescription") or "").strip()
        character_action = str(s.get("characterAction") or s.get("motion") or "").strip()
        camera_motion = str(s.get("cameraMotion") or s.get("camera") or "").strip()
        scene_environment = str(s.get("environment") or "").strip()
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
        guarded_scene, semantic_fallback_used = _scene_semantic_guardrail(
            scene={
                **s,
                "visualDescription": visual_desc,
                "visualPrompt": visual_prompt,
                "reason": reason_text,
                "sceneNarrative": scene_narrative,
                "environment": scene_environment,
            },
            semantic_whitelist=semantic_whitelist,
            source_text=text,
            session_world_anchors=session_world_anchors,
            prop_anchor_label=prop_anchor_label or "",
        )
        if semantic_fallback_used:
            visual_desc = str(guarded_scene.get("visualDescription") or visual_desc).strip()
            visual_prompt = str(guarded_scene.get("visualPrompt") or visual_prompt).strip()
            reason_text = str(guarded_scene.get("reason") or reason_text).strip()
            scene_narrative = str(guarded_scene.get("sceneNarrative") or scene_narrative).strip()
            scene_goal = str(guarded_scene.get("sceneGoal") or scene_goal).strip()
            character_action = str(guarded_scene.get("characterAction") or character_action).strip()
            camera_motion = str(guarded_scene.get("cameraMotion") or camera_motion).strip()
            scene_environment = str(guarded_scene.get("environment") or scene_environment).strip()

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
            "sceneGoal": scene_goal or "Advance the lyrical narrative in the same world",
            "sceneNarrative": scene_narrative or scene_text_ru,
            "characterAction": character_action or "Character performs the current emotional beat",
            "cameraMotion": camera_motion or "Cinematic movement aligned with rhythm",
            "environment": scene_environment or str(session_world_anchors.get("location") or "same main location"),
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
                "semanticWhitelist": semantic_whitelist,
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


def _clean_refs_by_role_for_image(refs_by_role: dict | None) -> dict[str, list[str]]:
    src = refs_by_role if isinstance(refs_by_role, dict) else {}
    role_aliases = {
        "ref_props": "props",
        "ref_items": "props",
        "items": "props",
        "item": "props",
        "objects": "props",
        "object": "props",
    }
    out: dict[str, list[str]] = {}
    for role in COMFY_REF_ROLES:
        items = src.get(role)
        if items is None:
            for alias_key, canonical in role_aliases.items():
                if canonical == role and src.get(alias_key) is not None:
                    items = src.get(alias_key)
                    break
        urls: list[str] = []
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    url = str(item.get("url") or "").strip()
                else:
                    url = str(item or "").strip()
                if url:
                    urls.append(url)
        out[role] = list(dict.fromkeys(urls))
    human_roles = ["character_1", "character_2", "character_3"]
    shared_group_urls = set(out.get("group") or [])
    claimed_human_url_to_role: dict[str, str] = {}
    for role in human_roles:
        role_urls = out.get(role) or []
        filtered_urls: list[str] = []
        for url in role_urls:
            if not url:
                continue
            if url in shared_group_urls:
                filtered_urls.append(url)
                continue
            if url in claimed_human_url_to_role:
                continue
            claimed_human_url_to_role[url] = role
            filtered_urls.append(url)
        out[role] = list(dict.fromkeys(filtered_urls))
    return out


def _resolve_scene_active_roles_for_image(
    refs_used: list[str] | dict | None,
    ref_directives: dict | None,
    available_refs_by_role: dict[str, list[str]],
    primary_role: str | None = None,
) -> list[str]:
    available_roles = {role for role in COMFY_REF_ROLES if len(available_refs_by_role.get(role) or []) > 0}

    selected_from_used: list[str] = []
    if isinstance(refs_used, list):
        selected_from_used = [str(role).strip() for role in refs_used if str(role).strip() in COMFY_REF_ROLES]
    elif isinstance(refs_used, dict):
        selected_from_used = [str(role).strip() for role, include in refs_used.items() if str(role).strip() in COMFY_REF_ROLES and bool(include)]
    selected_from_used = [
        role
        for role in selected_from_used
        if role in available_roles and str((ref_directives or {}).get(role) or "") != "omit"
    ]

    selected_from_directives: list[str] = []
    if isinstance(ref_directives, dict):
        for role in COMFY_REF_ROLES:
            directive = str(ref_directives.get(role) or "").strip()
            if role not in available_roles:
                continue
            if directive == "omit":
                continue
            if directive in COMFY_ACTIVE_DIRECTIVES:
                selected_from_directives.append(role)

    active_roles: list[str] = []
    for role in selected_from_used + selected_from_directives:
        if role not in active_roles:
            active_roles.append(role)

    primary = str(primary_role or "").strip()
    if not active_roles and primary in available_roles and str((ref_directives or {}).get(primary) or "") != "omit":
        active_roles = [primary]

    if not active_roles:
        fallback_role = next(
            (
                role
                for role in COMFY_FALLBACK_ROLE_PRIORITY
                if role in available_roles and str((ref_directives or {}).get(role) or "") != "omit"
            ),
            None,
        )
        if fallback_role:
            active_roles = [fallback_role]

    return active_roles


def _normalize_scene_entity_contract_for_image(
    *,
    primary_role: str,
    secondary_roles: list[str],
    refs_used: list[str] | dict | None,
    refs_used_by_role: dict | None,
    ref_directives: dict | None,
    available_refs_by_role: dict[str, list[str]],
    hero_entity_id: str | None,
    support_entity_ids: list[str] | None,
    must_appear: list[str] | None,
    must_not_appear: list[str] | None,
    environment_lock: bool | None,
    style_lock: bool | None,
    identity_lock: bool | None,
    scene_goal: str | None = None,
    scene_text: str | None = None,
    scene_delta: str | None = None,
    image_prompt: str | None = None,
    video_prompt: str | None = None,
) -> dict[str, Any]:
    def _group_narratively_required() -> bool:
        must_roles = {str(role or "").strip().lower() for role in (must_appear or []) if str(role or "").strip()}
        if "group" in must_roles:
            return True
        directives = ref_directives if isinstance(ref_directives, dict) else {}
        group_directive = str(directives.get("group") or "").strip().lower()
        if group_directive in {"required", "hero"}:
            return True
        scene_signal = " ".join([
            str(scene_goal or "").strip().lower(),
            str(scene_text or "").strip().lower(),
            str(scene_delta or "").strip().lower(),
            str(image_prompt or "").strip().lower(),
            str(video_prompt or "").strip().lower(),
        ])
        return any(hint in scene_signal for hint in GROUP_NARRATIVE_REQUIRED_HINTS)

    group_required = _group_narratively_required()
    refs_used_merged: list[str] | dict | None = refs_used
    if isinstance(refs_used_by_role, dict) and refs_used_by_role:
        role_keys = [str(role or "").strip() for role in refs_used_by_role.keys() if str(role or "").strip()]
        if isinstance(refs_used_merged, dict):
            refs_used_merged = {**refs_used_merged, **{role: True for role in role_keys if role not in refs_used_merged}}
        elif isinstance(refs_used_merged, list):
            refs_used_merged = list(dict.fromkeys([*refs_used_merged, *role_keys]))
        else:
            refs_used_merged = role_keys
    resolved_roles = _resolve_scene_active_roles_for_image(refs_used_merged, ref_directives, available_refs_by_role, primary_role)
    resolved_roles = [role for role in resolved_roles if role in COMFY_REF_ROLES]
    must = [str(r or "").strip() for r in (must_appear or []) if str(r or "").strip() in COMFY_REF_ROLES]
    must = list(dict.fromkeys(must))
    must_not = [str(r or "").strip() for r in (must_not_appear or []) if str(r or "").strip() in COMFY_REF_ROLES]
    must_not = list(dict.fromkeys(must_not))
    if not group_required and "group" not in must_not:
        must_not.append("group")

    active_roles: list[str] = []
    for role in resolved_roles + must:
        if role in COMFY_REF_ROLES and role not in must_not and role not in active_roles:
            active_roles.append(role)
    if not group_required:
        active_roles = [role for role in active_roles if role != "group"]

    hero = str(hero_entity_id or primary_role or "").strip()
    if hero not in COMFY_REF_ROLES or hero not in active_roles:
        hero = active_roles[0] if active_roles else ""

    supports = [str(r or "").strip() for r in (support_entity_ids or secondary_roles or [])]
    supports = [r for r in supports if r in COMFY_REF_ROLES and r in active_roles and r != hero]
    supports = list(dict.fromkeys(supports))

    must = list(dict.fromkeys([r for r in must if r in active_roles]))
    if not must:
        must = [hero] + supports if hero else list(active_roles)
    must = list(dict.fromkeys([r for r in must if r in active_roles]))

    degraded_roles: list[str] = []
    for role in resolved_roles + must:
        if role in COMFY_REF_ROLES and len(available_refs_by_role.get(role) or []) == 0 and role not in degraded_roles:
            degraded_roles.append(role)

    return {
        "activeRoles": active_roles,
        "heroEntityId": hero,
        "supportEntityIds": supports,
        "mustAppear": must,
        "mustNotAppear": must_not,
        "environmentLock": bool(environment_lock if environment_lock is not None else "location" in must),
        "styleLock": bool(style_lock if style_lock is not None else "style" in active_roles),
        "identityLock": bool(identity_lock if identity_lock is not None else any(r in active_roles for r in ["character_1","character_2","character_3","group","animal","props"])),
        "degradedRoles": degraded_roles,
        "resolvedRoles": resolved_roles,
    }


def _shorten_text(value: str, limit: int = 700) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + " …"


def _looks_like_scene_meta_label(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw:
        return False
    normalized = raw.lower()
    return bool(
        re.fullmatch(r"scene\s*\d+", raw, flags=re.IGNORECASE)
        or re.fullmatch(r"сцена\s*\d+", raw, flags=re.IGNORECASE)
        or re.fullmatch(r"step[_\s-]*\d+", raw, flags=re.IGNORECASE)
        or normalized == "story_action"
        or normalized.startswith("scene title:")
        or normalized.startswith("narrative step:")
        or normalized.startswith("scene goal:")
        or normalized.startswith("mode:")
        or normalized.startswith("style preset:")
        or normalized.startswith("timing:")
        or normalized.startswith("primary role:")
        or normalized.startswith("secondary roles:")
        or normalized.startswith("refs used:")
        or normalized.startswith("source image:")
    )


def _sanitize_visual_prompt_text(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    lines: list[str] = []
    for line in re.split(r"\n+", raw):
        cleaned = str(line or "").strip()
        if not cleaned or _looks_like_scene_meta_label(cleaned):
            continue
        lines.append(cleaned)
    return "\n".join(lines).strip()


def _normalize_scene_goal_for_image(scene_goal: str, planner_meta: dict[str, Any] | None = None) -> str:
    cleaned = _sanitize_visual_prompt_text(scene_goal)
    if cleaned:
        return cleaned
    return _sanitize_visual_prompt_text(str((planner_meta or {}).get("storyMissionSummary") or ""))


def _normalize_scene_narrative_step_for_image(scene_narrative_step: str) -> str:
    cleaned = _sanitize_visual_prompt_text(scene_narrative_step)
    if not cleaned or _looks_like_scene_meta_label(cleaned):
        return ""
    return cleaned


def _scene_explicitly_requests_designed_text(*values: Any) -> bool:
    markers = [
        "{title}",
        "title text",
        "stylized title",
        "movie title",
        "poster title",
        "thumbnail title",
        "logo text",
        "wordmark",
        "typography",
        "font",
        "lettering",
        "caption text",
    ]
    for value in values:
        normalized = str(value or "").strip().lower()
        if not normalized:
            continue
        if any(marker in normalized for marker in markers):
            return True
    return False


def _comfy_text_rendering_block(*, allow_designed_text: bool = False) -> str:
    if allow_designed_text:
        return "\n".join([
            "TEXT RENDERING RULE (EXPLICITLY REQUESTED):",
            "- readable title text/typography is allowed only because the prompt explicitly requests it",
            "- integrate the text naturally into the scene composition, never inside a cheap overlay box or UI frame",
            "- keep the text cinematic, production-grade, and consistent with scene lighting/perspective/materials",
            "- still forbid subtitles, debug text, service labels, watermarks, scene numbers, fake UI, and random extra copy",
        ])
    return "\n".join([
        "TEXT OVERLAY SAFETY (DEFAULT RULE):",
        "- no text overlays in the generated image",
        "- no captions, subtitles, labels, UI text, lower-thirds, watermarks, side annotations, typography, or debug strings",
        "- do not draw scene numbers, scene titles, scene ids, step labels, story_action, or any service/meta text",
        "- only allow readable text when the scene explicitly requests integrated title/typography treatment or requires a real in-world sign or object with text",
    ])


def _build_solo_character_guard_block(
    *,
    contract: dict[str, Any],
    refs_by_role: dict[str, list[str]],
    connected_inputs: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    human_roles = ["character_1", "character_2", "character_3"]
    active_roles = [str(role or "").strip() for role in (contract.get("activeRoles") or []) if str(role or "").strip()]
    must_appear = [str(role or "").strip() for role in (contract.get("mustAppear") or []) if str(role or "").strip()]
    hero_entity = str(contract.get("heroEntityId") or "").strip()

    principal_candidates = [role for role in [hero_entity, *must_appear, *active_roles] if role in human_roles]
    principal_role = principal_candidates[0] if principal_candidates else ""
    active_human_roles = [role for role in dict.fromkeys([*active_roles, *must_appear]) if role in human_roles]
    is_solo_human_scene = bool(principal_role) and len(active_human_roles) == 1

    connected_refs_by_role = (connected_inputs.get("refsByRole") or {}) if isinstance(connected_inputs, dict) else {}
    connected_human_roles = {
        role for role in human_roles
        if bool(refs_by_role.get(role))
        or bool((isinstance(connected_refs_by_role, dict) and connected_refs_by_role.get(role)))
    }
    inactive_human_roles = sorted([role for role in connected_human_roles if role != principal_role and role not in active_human_roles])

    if not is_solo_human_scene:
        return "", {
            "applied": False,
            "reason": "not_single_human_scene",
            "principalRole": principal_role,
            "activeHumanRoles": active_human_roles,
            "inactiveHumanRoles": inactive_human_roles,
        }

    lines = [
        "SOLO CHARACTER ENFORCEMENT (STRICT):",
        f"- only {principal_role} is the principal human subject in this frame",
        f"- {principal_role} must remain the only readable hero-level foreground person",
        "- other humans are allowed only as anonymous background extras with no individual readability",
        "- crowd/background extras must not look like a connected named character",
        "- do not introduce any inactive connected cast role as a second protagonist",
        "- do not place a character_2-like (or other inactive-role-like) silhouette in foreground prominence",
        "- preserve marketplace/crowd realism while keeping hero-subject hierarchy locked",
    ]
    if inactive_human_roles:
        lines.append(f"- inactive connected cast that must stay non-hero/absent: {', '.join(inactive_human_roles)}")

    return "\n".join(lines), {
        "applied": True,
        "reason": "single_human_scene",
        "principalRole": principal_role,
        "activeHumanRoles": active_human_roles,
        "inactiveHumanRoles": inactive_human_roles,
    }


def _build_comfy_image_prompt_assembly(
    *,
    scene_delta: str,
    scene_text: str,
    style: str,
    style_anchor: str,
    lighting_anchor: str,
    location_anchor: str,
    environment_anchor: str,
    weather_anchor: str,
    surface_anchor: str,
    world_scale_context: str,
    entity_scale_anchor_text: str,
    refs_by_role: dict[str, list[str]],
    connected_inputs: dict[str, Any],
    text_input: str,
    audio_url: str,
    mode_input: str,
    style_preset_input: str,
    scene_goal: str,
    scene_narrative_step: str,
    continuity_input: str,
    planner_meta: dict[str, Any],
    session_baseline: dict[str, Any] | None,
    effective_character_anchor: str,
    effective_location_anchor: str,
    effective_style_anchor: str,
    scene_id: str,
    scene_contract: dict[str, Any] | None = None,
    reference_profiles: dict[str, Any] | None = None,
    selected_view_hint: str = "any",
    multi_view_reference_profile: dict[str, Any] | None = None,
    multi_view_context_lines: list[str] | None = None,
) -> tuple[str, dict[str, Any]]:
    cast_roles = ["character_1", "character_2", "character_3", "group", "animal"]
    cast_entities = [role for role in cast_roles if refs_by_role.get(role)]
    world_entities = [role for role in ["location"] if refs_by_role.get(role)]
    style_entities = [role for role in ["style"] if refs_by_role.get(role)]
    props_entities = [role for role in ["props"] if refs_by_role.get(role)]
    contract = scene_contract if isinstance(scene_contract, dict) else {}
    profiles = reference_profiles if isinstance(reference_profiles, dict) else {}
    multi_view_profile = multi_view_reference_profile if isinstance(multi_view_reference_profile, dict) else {}
    multi_view_lines = [str(line or "").strip() for line in (multi_view_context_lines or []) if str(line or "").strip()]

    def _normalized_gender_presentation(raw: Any) -> str:
        value = re.sub(r"\s+", " ", str(raw or "").strip().lower())
        if not value:
            return ""
        if any(token in value for token in ["female", "woman", "girl", "feminine"]):
            return "female"
        if any(token in value for token in ["male", "man", "boy", "masculine"]):
            return "male"
        return ""

    role_blocks: list[str] = []
    if cast_entities:
        role_blocks.append(f"Cast/entity anchors (must be present and active): {', '.join(cast_entities)}.")
    if world_entities:
        role_blocks.append("World/location anchor is connected and must define environment identity.")
    if style_entities:
        role_blocks.append("Style anchor is connected and must define visual language, palette, weather and atmosphere.")
    if props_entities:
        role_blocks.append("Props/items anchor is connected and must preserve object identity.")

    profile_contract_lines: list[str] = []
    visual_profile_lines: list[str] = []
    anatomy_contract_lines: list[str] = []
    forbidden_changes: list[str] = []
    active_roles = [str(r or "").strip() for r in (contract.get("activeRoles") or []) if str(r or "").strip() in COMFY_REF_ROLES]
    duet_contract_detected, duet_hardening_source, duet_contract_preview = _detect_duet_contract_for_video(
        scene_contract=contract,
        scene_active_roles=active_roles,
        duet_lock_enabled=contract.get("duetLockEnabled"),
        duet_identity_contract=contract.get("duetIdentityContract"),
        anchor_roles=[],
    )
    duet_hardening_block = _build_duet_hardening_block(active_roles=active_roles) if duet_contract_detected else ""
    solo_character_guard_block, solo_character_guard_debug = _build_solo_character_guard_block(
        contract=contract,
        refs_by_role=refs_by_role,
        connected_inputs=connected_inputs,
    )
    genre_intent, genre_hardening_source = _resolve_genre_hardening_from_sources(
        scene_contract=contract,
        planner_meta=planner_meta,
    )
    genre_hardening_block = _build_genre_hardening_block(genre_intent=genre_intent)
    role_type_by_role = _build_role_type_by_role(active_roles, profiles)
    for role in active_roles:
        profile = profiles.get(role) if isinstance(profiles.get(role), dict) else {}
        inv = profile.get("invariants") if isinstance(profile.get("invariants"), list) else []
        forb = profile.get("forbiddenChanges") if isinstance(profile.get("forbiddenChanges"), list) else []
        if inv:
            profile_contract_lines.append(f"- {role}: " + "; ".join(str(x) for x in inv[:4]))
        visual_profile = profile.get("visualProfile") if isinstance(profile.get("visualProfile"), dict) else {}
        if visual_profile:
            visual_bits = []
            for k, v in list(visual_profile.items())[:6]:
                value = str(v or "").strip()
                if value:
                    visual_bits.append(f"{k}={value}")
            if visual_bits:
                visual_profile_lines.append(f"- {role}: " + "; ".join(visual_bits))
            normalized_gender = _normalized_gender_presentation(visual_profile.get("genderPresentation"))
            if normalized_gender == "female":
                anatomy_contract_lines.extend([
                    f"- {role}: preserve gender-consistent anatomy across all visible body parts",
                    f"- {role}: all visible hands, fingers, wrists, forearms, shoulders, neck, clavicles, torso, waist, hips and legs must match the established female anatomy of the character",
                    f"- {role}: no masculine hands, masculine forearms, masculine shoulders, masculine neck, masculine torso cues, or mixed-sex anatomy",
                    f"- {role}: every visible body fragment must remain identity-consistent and sex-consistent even in partial-body crops or detail shots",
                ])
            elif normalized_gender == "male":
                anatomy_contract_lines.extend([
                    f"- {role}: preserve gender-consistent anatomy across all visible body parts",
                    f"- {role}: all visible hands, fingers, wrists, forearms, shoulders, neck, clavicles, torso, waist, hips and legs must match the established male anatomy of the character",
                    f"- {role}: no feminine hands, feminine shoulder line, feminine torso cues, or mixed-sex anatomy unless explicitly requested",
                    f"- {role}: every visible body fragment must remain identity-consistent and sex-consistent even in partial-body crops or detail shots",
                ])
        if forb:
            forbidden_changes.extend([f"{role}:{str(x)}" for x in forb])

    identity_lock_block = "\n".join([
        "IDENTITY CONTRACT (STRICT):",
        f"- hero entity: {contract.get('heroEntityId') or 'none'}",
        f"- support entities: {', '.join(contract.get('supportEntityIds') or []) or 'none'}",
        f"- must appear: {', '.join(contract.get('mustAppear') or []) or 'none'}",
        f"- must not appear: {', '.join(contract.get('mustNotAppear') or []) or 'none'}",
        f"- identityLock={bool(contract.get('identityLock'))} environmentLock={bool(contract.get('environmentLock'))} styleLock={bool(contract.get('styleLock'))}",
        "- camera/pose may change, identity may not change",
    ] + (profile_contract_lines or ["- no active profile contracts"]))

    priority_contract_block = "\n".join([
        "HERO PRIORITY CONTRACT (STRICT):",
        "- hero identity is highest-priority visual truth",
        "- props identity must be preserved exactly when connected",
        "- location defines environment/world identity",
        "- style layer controls palette/lighting/cinematic treatment only",
        "- style cannot override actor identity, outfit identity, hair identity, animal coat/species identity, object silhouette/material/color, or world anchors",
        "- sex-consistent anatomy must stay locked to the established character gender presentation across every visible body part",
        "- previous generated scene image is continuity reference, not identity override",
        "- camera/pose/composition may change; identity cannot change",
    ])

    character_role_priority_lines = [
        "CHARACTER ROLE PRIORITY:",
        "- hero: primary visual focus, drives scene",
        "- support: secondary presence, reacts or assists",
        "- antagonist: opposing force, creates tension",
        "Rules:",
        "- hero must be the most visually readable subject",
        "- support characters must not override hero presence",
        "- antagonist must be visually distinct but NOT replace hero identity",
    ]
    if role_type_by_role:
        character_role_priority_lines.append(
            "- active role types: " + ", ".join(f"{role}={role_type_by_role.get(role, 'unknown')}" for role in active_roles)
        )
    character_role_priority_block = "\n".join(character_role_priority_lines)

    role_visual_hierarchy_lines = [
        "ROLE VISUAL HIERARCHY:",
        "- hero must be clearly visible",
        "- hero must not be occluded by other characters",
        "- hero must not be visually downgraded",
        "- support can be partially visible",
        "- support can be secondary in framing",
        "- antagonist may dominate mood",
        "- antagonist must NOT replace hero identity",
    ]
    if contract.get("heroEntityId"):
        role_visual_hierarchy_lines.append(f"- scene hero entity: {contract.get('heroEntityId')}")
    if contract.get("supportEntityIds"):
        role_visual_hierarchy_lines.append("- scene support entities: " + ", ".join(contract.get("supportEntityIds") or []))
    role_visual_hierarchy_block = "\n".join(role_visual_hierarchy_lines)

    role_camera_guidance_block = "\n".join([
        "ROLE → CAMERA GUIDANCE:",
        "- hero → primary framing (center, focus, dominant subject)",
        "- support → secondary framing (side, background, partial)",
        "- antagonist → tension framing (shadow, silhouette, contrast)",
    ])

    forbidden_changes_block = "\n".join([
        "FORBIDDEN CHANGES:",
        *([f"- {line}" for line in forbidden_changes] or ["- none"]) 
    ])

    anti_collage_block = "\n".join([
        "ANTI-COLLAGE / SINGLE-FRAME GUARDRAIL:",
        "- single image only",
        "- exactly one coherent photographic frame",
        "- no collage",
        "- no diptych",
        "- no triptych",
        "- no split screen",
        "- no multi-panel composition",
        "- no storyboard sheet",
        "- no contact sheet",
        "- no repeated copies of the subject",
        "- no stacked frames",
        "- no grid layout",
        "- no multiple photos inside one image",
    ])

    continuity_value = _sanitize_visual_prompt_text(continuity_input or str((planner_meta or {}).get("globalContinuity") or "").strip())
    scene_goal_value = _normalize_scene_goal_for_image(scene_goal, planner_meta)
    scene_narrative_step_value = _normalize_scene_narrative_step_for_image(scene_narrative_step)
    allow_designed_text = _scene_explicitly_requests_designed_text(
        scene_delta,
        scene_text,
        text_input,
        scene_goal_value,
        scene_narrative_step_value,
        continuity_value,
    )
    planner_meta_preview = {
        "storyControlMode": str((planner_meta or {}).get("storyControlMode") or "").strip(),
        "timelineSource": str((planner_meta or {}).get("timelineSource") or "").strip(),
        "narrativeSource": str((planner_meta or {}).get("narrativeSource") or "").strip(),
    }

    identity_layer_block = "\n".join([
        "IDENTITY LAYER (PRIORITY 1):",
        f"- hero entity: {contract.get('heroEntityId') or 'none'}",
        f"- active roles: {', '.join(active_roles) or 'none'}",
        "- preserve face/hair/body/outfit/accessories and forbidden identity changes from references",
        "- multiple images for the same role DO NOT represent different people",
        "REFERENCE PRIORITY RULE:",
        "- reference images ALWAYS override text description",
        "- if text conflicts with references: IGNORE the text and FOLLOW the reference images",
        "- text may control: action, pose, emotion, camera",
        "- text must NOT redefine: identity, face, hair, clothing, body proportions, accessories",
    ] + (visual_profile_lines or ["- no detailed visualProfile extracted"]))

    anatomy_lock_block = "\n".join([
        "GENDER-CONSISTENT ANATOMY LOCK (STRICT PRIORITY 1A):",
        "- preserve gender-consistent anatomy across all visible body parts",
        "- no mixed-sex anatomy",
    ] + (anatomy_contract_lines or ["- no explicit genderPresentation lock extracted from active human profiles"]))

    multi_view_lock_block = "\n".join([
        "REFERENCE USAGE — MULTI-VIEW CHARACTER LOCK:",
        "Each character role (e.g. character_1) may contain multiple reference images representing the SAME entity from different angles (front, side, back, profile, motion, detail).",
        "Treat ALL images of the same role as ONE unified identity set.",
        "VIEW SELECTION PRIORITY (STRICT):",
        "- The camera direction MUST match selectedViewHint when a matching reference exists.",
        "- if selectedViewHint = back, character MUST be shown from behind.",
        "- if selectedViewHint = side/profile, character MUST be shown in profile.",
        "- if selectedViewHint = detail, face or specific detail MUST dominate the frame.",
        "- if selectedViewHint = front, frontal identity must be clearly visible.",
        "- The model is NOT allowed to ignore selectedViewHint if matching references exist.",
        "- fallback to any ONLY when no matching reference exists AND scene composition strictly requires it.",
        "- View consistency is mandatory when matching reference angles are available.",
        "When generating a scene:",
        "- Select the most appropriate reference image based on the camera angle required by the scene (front / side / back / profile / over-shoulder / wide).",
        "- If the exact angle is not available, infer it ONLY from the provided references.",
        "- NEVER invent new identity details.",
        "MULTI-VIEW CONSISTENCY RULE:",
        "- all views (front / side / back / detail) represent the SAME entity",
        "- reuse identity across angles",
        "- do NOT reinterpret character per view",
        "- do NOT generate alternative versions",
        "- changing angle must NOT change face, hair, outfit, or body",
        "- only camera perspective changes",
        "Identity must remain STRICTLY consistent across all views:",
        "- face structure",
        "- hairstyle and color",
        "- body proportions",
        "- clothing design",
        "- accessories",
        "FORBIDDEN:",
        "- changing hairstyle",
        "- changing clothing",
        "- modifying facial structure",
        "- adding/removing accessories",
        "- generating a different version of the same character",
        "Only camera angle may change — identity must remain identical.",
        "All outputs must feel like the SAME person filmed from different angles.",
        "Multiple images for the same role DO NOT represent different people.",
        f"SelectedViewHint (STRICT): {selected_view_hint or 'any'}.",
        f"{_selected_view_requirement_line(selected_view_hint)}",
    ] + (multi_view_lines or ["- no explicit multi-view context for active roles"]))

    scene_meaning_lines = [
        "SCENE LAYER (PRIORITY 2):",
        "SCENE MEANING:",
        f"- scene delta: {_sanitize_visual_prompt_text(scene_delta)}",
        f"- scene text/context: {_sanitize_visual_prompt_text(scene_text or text_input or '')}",
    ]
    if scene_goal_value:
        scene_meaning_lines.append(f"- scene goal: {scene_goal_value}")
    if scene_narrative_step_value:
        scene_meaning_lines.append(f"- scene progression cue: {scene_narrative_step_value}")
    scene_meaning_block = "\n".join(scene_meaning_lines)

    camera_view_consistency_block = "\n".join([
        "CAMERA → VIEW CONSISTENCY:",
        "- camera direction MUST align with the selected reference view",
        "- rear tracking shot -> use back view",
        "- profile close-up -> use side/profile",
        "- over-shoulder -> use back or side",
        "- frontal portrait -> use front",
        "- camera description MUST NOT conflict with reference usage",
        "- if conflict happens: PRIORITIZE reference view and ADJUST camera interpretation",
        f"- active selectedViewHint: {selected_view_hint or 'any'}",
        f"- {_selected_view_requirement_line(selected_view_hint)}",
    ])

    view_continuity_block = "\n".join([
        "VIEW CONTINUITY RULE:",
        "- across scenes, camera angle transitions must be natural",
        "- allowed: front -> side -> back",
        "- allowed: wide -> close-up -> detail",
        "- forbidden: identity shift between angles",
        "- forbidden: random face changes between shots",
        "- character must remain visually identical across all scene transitions",
    ])

    continuity_block = "\n".join([
        "CONTINUITY:",
        f"- continuity constraints: {continuity_value}",
        f"- session baseline: {json.dumps(session_baseline or {}, ensure_ascii=False)}",
    ])

    source_control_block = "\n".join([
        "CONNECTED INPUTS (all connected sources must be consumed by role):",
        f"- text input: {bool(text_input.strip())}",
        f"- audio input: {bool(audio_url.strip())}",
        f"- mode: {mode_input or ''}",
        f"- style preset: {style_preset_input or style}",
        f"- planner meta: {json.dumps(planner_meta_preview, ensure_ascii=False)}",
        "- use audio as rhythm/emotion/timing influence when connected",
        "- use text as semantic/narrative influence when connected",
        "- use mode/style preset as cinematic interpretation layer when connected",
        "- if any connected input is ignored this is a logic error",
    ])

    physics_blocks = build_physics_first_image_blocks(
        scene_delta=scene_delta,
        scene_text=scene_text or text_input or "",
        scene_goal=scene_goal_value,
        style=style,
        lighting_anchor=lighting_anchor,
        location_anchor=location_anchor,
        environment_anchor=environment_anchor,
        weather_anchor=weather_anchor,
        surface_anchor=surface_anchor,
        world_scale_context=world_scale_context,
        entity_scale_anchor_text=entity_scale_anchor_text,
        effective_character_anchor=effective_character_anchor,
        effective_location_anchor=effective_location_anchor,
        effective_style_anchor=effective_style_anchor,
        continuity_hint=continuity_value,
    )

    assembled_prompt = "\n\n".join([
        identity_layer_block,
        physics_blocks["lightWorldBlock"],
        physics_blocks["subjectIdentityBlock"],
        physics_blocks["physicalSceneStateBlock"],
        physics_blocks["environmentContactBlock"],
        physics_blocks["geometryBlock"],
        physics_blocks["textureBlock"],
        physics_blocks["moodPhysicsBlock"],
        "CAST / ENTITY ANCHORS:\n" + ("\n".join(role_blocks) if role_blocks else "- none explicitly connected"),
        "WORLD / LOCATION ANCHOR:\n" + f"- {effective_location_anchor or location_anchor}",
        "STYLE ANCHOR:\n" + f"- {effective_style_anchor or style_anchor}",
        "PROPS ANCHOR:\n" + ("- props/items connected and must be preserved" if props_entities else "- no explicit props refs"),
        anti_collage_block,
        physics_blocks["negativeConstraintsBlock"],
        _comfy_text_rendering_block(allow_designed_text=allow_designed_text),
        genre_hardening_block if genre_hardening_block else "GENRE HARDENING: not required (neutral or unspecified scene contract).",
        priority_contract_block,
        character_role_priority_block,
        role_visual_hierarchy_block,
        role_camera_guidance_block,
        identity_lock_block or "IDENTITY LOCK: no character_1 ref connected.",
        duet_hardening_block if duet_hardening_block else "DUET / MULTI-CHARACTER SEPARATION CONTRACT: not required for this scene.",
        solo_character_guard_block if solo_character_guard_block else "SOLO CHARACTER ENFORCEMENT: not required for this scene.",
        anatomy_lock_block,
        multi_view_lock_block,
        camera_view_consistency_block,
        view_continuity_block,
        forbidden_changes_block,
        "CHARACTER ANCHOR:\n" + f"- {effective_character_anchor or 'coherent single-character identity across all scenes'}",
    ])

    connected_summary = {
        "refsByRole": {role: len(urls) for role, urls in refs_by_role.items()},
        "connectedInputs": connected_inputs,
        "activeRoles": sorted([role for role, urls in refs_by_role.items() if urls]),
        "hasCharacter1Ref": bool(refs_by_role.get("character_1")),
        "hasLocationRef": bool(refs_by_role.get("location")),
        "hasStyleRef": bool(refs_by_role.get("style")),
        "hasPropsRef": bool(refs_by_role.get("props")),
        "hasAudio": bool(audio_url.strip()),
        "hasText": bool(text_input.strip()),
    }

    consumed_inputs = set(["sceneDelta", "sceneText", "style", "continuity", "plannerMeta", "mode", "stylePreset", "audio", "text"])
    for role, urls in refs_by_role.items():
        if urls:
            consumed_inputs.add(f"ref:{role}")

    expected_connected = set()
    refs_connected = (connected_inputs.get("refsByRole") or {}) if isinstance(connected_inputs, dict) else {}
    if isinstance(refs_connected, dict):
        for role, is_connected in refs_connected.items():
            if is_connected:
                expected_connected.add(f"ref:{role}")
    if text_input.strip() or bool((connected_inputs or {}).get("hasText")):
        expected_connected.add("text")
    if audio_url.strip() or bool((connected_inputs or {}).get("hasAudio")):
        expected_connected.add("audio")
    if mode_input.strip() or bool((connected_inputs or {}).get("hasMode")):
        expected_connected.add("mode")
    if style_preset_input.strip() or bool((connected_inputs or {}).get("hasStylePreset")):
        expected_connected.add("stylePreset")
    if continuity_value:
        expected_connected.add("continuity")
    if planner_meta:
        expected_connected.add("plannerMeta")

    unused_connected_inputs = sorted(expected_connected - consumed_inputs)

    debug = {
        "sceneId": scene_id,
        "connectedNodesSummary": connected_summary,
        "refsByRole": refs_by_role,
        "rolesActive": connected_summary["activeRoles"],
        "identityLockBlockPreview": identity_lock_block,
        "priorityContractBlockPreview": priority_contract_block,
        "characterRolePriorityBlockPreview": character_role_priority_block,
        "roleVisualHierarchyBlockPreview": role_visual_hierarchy_block,
        "roleCameraGuidanceBlockPreview": role_camera_guidance_block,
        "anatomyLockBlockPreview": anatomy_lock_block,
        "duetHardeningBlockPreview": duet_hardening_block,
        "duetHardeningApplied": bool(duet_hardening_block),
        "duetHardeningSource": duet_hardening_source,
        "duetContractDetected": duet_contract_detected,
        "duetContractPreview": duet_contract_preview,
        "genreHardeningApplied": bool(genre_hardening_block),
        "genreHardeningSource": genre_hardening_source,
        "genreHardeningPreview": genre_hardening_block,
        "multiViewLockBlockPreview": multi_view_lock_block,
        "multiViewReferenceProfile": multi_view_profile,
        "forbiddenChangesBlockPreview": forbidden_changes_block,
        "antiCollageBlockPreview": anti_collage_block,
        "lightWorldBlockPreview": physics_blocks["lightWorldBlock"],
        "subjectIdentityBlockPreview": physics_blocks["subjectIdentityBlock"],
        "sceneMeaningBlockPreview": scene_meaning_block,
        "continuityBlockPreview": continuity_block,
        "sourceControlBlockPreview": source_control_block,
        "physicalSceneStateBlockPreview": physics_blocks["physicalSceneStateBlock"],
        "environmentContactBlockPreview": physics_blocks["environmentContactBlock"],
        "geometryBlockPreview": physics_blocks["geometryBlock"],
        "textureBlockPreview": physics_blocks["textureBlock"],
        "moodPhysicsBlockPreview": physics_blocks["moodPhysicsBlock"],
        "negativeConstraintsBlockPreview": physics_blocks["negativeConstraintsBlock"],
        "finalImagePromptPreview": _shorten_text(assembled_prompt, 1800),
        "unusedConnectedInputs": unused_connected_inputs,
    }
    return assembled_prompt, debug


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
    scene_text = _sanitize_visual_prompt_text((payload.sceneText or "").strip())
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

    raw_refs_by_role_incoming = getattr(refs_obj, "refsByRole", None)
    print("[COMFY IMAGE DEBUG] incoming refs.raw.refsByRole=" + json.dumps(raw_refs_by_role_incoming, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] incoming refs.raw.character=" + json.dumps(getattr(refs_obj, "character", None), ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] incoming refs.raw.heroEntityId=" + json.dumps(getattr(refs_obj, "heroEntityId", None), ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] incoming refs.raw.refsUsed=" + json.dumps(getattr(refs_obj, "refsUsed", None), ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] incoming refs.raw.primaryRole=" + json.dumps(getattr(refs_obj, "primaryRole", None), ensure_ascii=False))
    incoming_character_1 = []
    if isinstance(raw_refs_by_role_incoming, dict):
        incoming_character_1 = raw_refs_by_role_incoming.get("character_1") or []
    print("[COMFY IMAGE DEBUG] incoming refs.raw.refsByRole.character_1=" + json.dumps(incoming_character_1, ensure_ascii=False))
    incoming_character_2 = []
    if isinstance(raw_refs_by_role_incoming, dict):
        incoming_character_2 = raw_refs_by_role_incoming.get("character_2") or []
    print("[COMFY IMAGE DEBUG] incoming refs.raw.refsByRole.character_2=" + json.dumps(incoming_character_2, ensure_ascii=False))

    comfy_refs_by_role = _clean_refs_by_role_for_image(raw_refs_by_role_incoming)
    print("[COMFY IMAGE DEBUG] cleaned refsByRole=" + json.dumps(comfy_refs_by_role, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] cleaned refsByRole counts=" + json.dumps({role: len(comfy_refs_by_role.get(role) or []) for role in COMFY_REF_ROLES}, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] cleaned refsByRole.character_1=" + json.dumps(comfy_refs_by_role.get("character_1") or [], ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] cleaned refsByRole.character_2=" + json.dumps(comfy_refs_by_role.get("character_2") or [], ensure_ascii=False))
    reference_profiles = build_reference_profiles({
        role: [{"url": url, "name": ""} for url in (comfy_refs_by_role.get(role) or [])]
        for role in COMFY_REF_ROLES
    })
    reference_profiles_summary = summarize_profiles(reference_profiles)
    connected_inputs = getattr(refs_obj, "connectedInputs", None)
    connected_inputs = connected_inputs if isinstance(connected_inputs, dict) else {}
    comfy_roles = COMFY_REF_ROLES

    scene_refs_used = getattr(refs_obj, "refsUsed", None)
    scene_refs_used_by_role = getattr(refs_obj, "refsUsedByRole", None)
    scene_ref_directives = getattr(refs_obj, "refDirectives", None)
    scene_participants = getattr(refs_obj, "participants", None)
    scene_primary_role = str(getattr(refs_obj, "primaryRole", "") or "").strip()
    scene_secondary_roles = [
        str(role or "").strip()
        for role in (getattr(refs_obj, "secondaryRoles", None) or [])
        if str(role or "").strip()
    ]
    scene_contract = _normalize_scene_entity_contract_for_image(
        primary_role=scene_primary_role,
        secondary_roles=scene_secondary_roles,
        refs_used=scene_refs_used,
        refs_used_by_role=scene_refs_used_by_role,
        ref_directives=scene_ref_directives,
        available_refs_by_role=comfy_refs_by_role,
        hero_entity_id=getattr(refs_obj, "heroEntityId", None),
        support_entity_ids=getattr(refs_obj, "supportEntityIds", None),
        must_appear=getattr(refs_obj, "mustAppear", None),
        must_not_appear=getattr(refs_obj, "mustNotAppear", None),
        environment_lock=getattr(refs_obj, "environmentLock", None),
        style_lock=getattr(refs_obj, "styleLock", None),
        identity_lock=getattr(refs_obj, "identityLock", None),
        scene_goal=getattr(refs_obj, "sceneGoal", None),
        scene_text=scene_text,
        scene_delta=scene_delta,
        image_prompt=prompt,
        video_prompt=getattr(refs_obj, "sceneNarrativeStep", None),
    )
    scene_active_roles = [
        str(role or "").strip()
        for role in (scene_contract.get("activeRoles") or [])
        if str(role or "").strip() in comfy_roles
    ]
    incoming_scene_active_roles = [
        str(role or "").strip()
        for role in (getattr(refs_obj, "sceneActiveRoles", None) or [])
        if str(role or "").strip() in comfy_roles
    ]
    for role in incoming_scene_active_roles:
        if role not in scene_active_roles:
            scene_active_roles.append(role)
    if not scene_active_roles:
        scene_active_roles = [
            role for role in COMFY_CAST_ROLES
            if len(comfy_refs_by_role.get(role) or []) > 0
        ]
    scene_contract["activeRoles"] = scene_active_roles
    must_not_appear_roles = set(scene_contract.get("mustNotAppear") or [])
    if {"character_1", "character_2"}.issubset(set(scene_active_roles)) and "group" not in must_not_appear_roles:
        must_not_appear_roles.add("group")
        scene_contract["mustNotAppear"] = list(dict.fromkeys([*(scene_contract.get("mustNotAppear") or []), "group"]))
    if must_not_appear_roles:
        scene_active_roles = [role for role in scene_active_roles if role not in must_not_appear_roles]
        scene_contract["activeRoles"] = scene_active_roles
    is_environment_only_scene = {"character_1", "character_2", "character_3", "group"}.issubset(must_not_appear_roles)
    if is_environment_only_scene:
        for role in ("character_1", "character_2", "character_3", "group"):
            comfy_refs_by_role[role] = []
        scene_active_roles = [role for role in scene_active_roles if role not in {"character_1", "character_2", "character_3", "group"}]
        scene_contract["activeRoles"] = scene_active_roles
    if "group" in must_not_appear_roles:
        comfy_refs_by_role["group"] = []
        scene_active_roles = [role for role in scene_active_roles if role != "group"]
        scene_contract["activeRoles"] = scene_active_roles

    hero_entity_id = str(scene_contract.get("heroEntityId") or "").strip()
    if hero_entity_id not in scene_active_roles:
        hero_entity_id = scene_active_roles[0] if scene_active_roles else ""

    support_entity_ids = [
        str(role or "").strip()
        for role in (scene_contract.get("supportEntityIds") or [])
        if str(role or "").strip() in scene_active_roles and str(role or "").strip() != hero_entity_id
    ]
    support_entity_ids = list(dict.fromkeys(support_entity_ids))
    if "group" in must_not_appear_roles:
        support_entity_ids = [role for role in support_entity_ids if role != "group"]

    must_appear_roles = [
        str(role or "").strip()
        for role in (scene_contract.get("mustAppear") or [])
        if str(role or "").strip() in scene_active_roles
    ]
    if not must_appear_roles:
        must_appear_roles = [hero_entity_id] + support_entity_ids if hero_entity_id else list(scene_active_roles)
    must_appear_roles = list(dict.fromkeys([role for role in must_appear_roles if role in scene_active_roles]))
    if "group" in must_not_appear_roles:
        must_appear_roles = [role for role in must_appear_roles if role != "group"]
    if incoming_scene_active_roles and not must_appear_roles:
        must_appear_roles = list(dict.fromkeys([role for role in incoming_scene_active_roles if role in scene_active_roles]))

    scene_contract["heroEntityId"] = hero_entity_id
    scene_contract["supportEntityIds"] = support_entity_ids
    scene_contract["mustAppear"] = must_appear_roles
    scene_contract["mustAppearCastRoles"] = must_appear_roles
    dropped_by_must_not_appear = sorted([role for role in (scene_contract.get("resolvedRoles") or []) if role in must_not_appear_roles])
    connected_refs_by_role = (connected_inputs.get("refsByRole") or {}) if isinstance(connected_inputs, dict) else {}

    contract_environment_lock = bool(scene_contract.get("environmentLock"))
    contract_style_lock = bool(scene_contract.get("styleLock"))
    refs_used_roles = set()
    if isinstance(scene_refs_used, list):
        refs_used_roles = {str(role or "").strip() for role in scene_refs_used if str(role or "").strip()}
    elif isinstance(scene_refs_used, dict):
        refs_used_roles = {str(role or "").strip() for role in scene_refs_used.keys() if str(role or "").strip()}
    prop_anchor_signal = bool(
        prop_anchor_label
        or "props" in refs_used_roles
        or (isinstance(scene_ref_directives, dict) and isinstance(scene_ref_directives.get("props"), dict) and bool(scene_ref_directives.get("props")))
    )

    selected_view_hint = _infer_selected_view_hint(scene_delta, scene_text, prompt)

    scene_cast_roles = [role for role in scene_active_roles if role in COMFY_CAST_ROLES]
    if "character_1" in scene_cast_roles and "character_2" in scene_cast_roles and "group" in scene_cast_roles:
        scene_cast_roles = [role for role in scene_cast_roles if role != "group"]
        scene_active_roles = [role for role in scene_active_roles if role != "group"]
        scene_contract["activeRoles"] = scene_active_roles
        must_appear_roles = [role for role in must_appear_roles if role != "group"]
        scene_contract["mustAppear"] = must_appear_roles
        scene_contract["supportEntityIds"] = [role for role in (scene_contract.get("supportEntityIds") or []) if role != "group"]
    if "group" in must_not_appear_roles:
        scene_cast_roles = [role for role in scene_cast_roles if role != "group"]
        scene_active_roles = [role for role in scene_active_roles if role != "group"]
        scene_contract["activeRoles"] = scene_active_roles
        scene_contract["mustAppear"] = [role for role in (scene_contract.get("mustAppear") or []) if role != "group"]
        scene_contract["supportEntityIds"] = [role for role in (scene_contract.get("supportEntityIds") or []) if role != "group"]
        comfy_refs_by_role["group"] = []
    world_anchor_roles: list[str] = []
    for role in ["location", "style"]:
        role_urls = comfy_refs_by_role.get(role) or []
        if not role_urls:
            continue
        if role in must_not_appear_roles:
            continue
        world_anchor_roles.append(role)

    allowed_props = bool(
        (comfy_refs_by_role.get("props") or []) and (
            ("props" in scene_active_roles)
            or prop_anchor_signal
            or (isinstance(scene_ref_directives, dict) and isinstance(scene_ref_directives.get("props"), dict))
        )
    )

    allowed_roles_for_image = set(scene_cast_roles) | set(world_anchor_roles)
    if allowed_props:
        allowed_roles_for_image.add("props")

    filtered_out_by_scene_contract: list[dict[str, Any]] = []
    if scene_active_roles:
        next_refs_by_role: dict[str, list[str]] = {}
        for role in comfy_roles:
            role_urls = comfy_refs_by_role.get(role) or []
            allowed = role in allowed_roles_for_image
            if not allowed and role_urls:
                reason = "filtered_out_not_in_scene_cast"
                if role in COMFY_WORLD_ANCHOR_ROLES:
                    reason = "filtered_out_world_anchor_must_not_appear" if role in must_not_appear_roles else "filtered_out_world_anchor_disabled"
                filtered_out_by_scene_contract.append({
                    "role": role,
                    "reason": reason,
                    "urlCount": len(role_urls),
                    "connected": bool(connected_refs_by_role.get(role)),
                    "environmentLock": contract_environment_lock if role == "location" else None,
                    "styleLock": contract_style_lock if role == "style" else None,
                })
            next_refs_by_role[role] = role_urls if allowed else []
        comfy_refs_by_role = next_refs_by_role

    (
        comfy_refs_by_role,
        multi_view_count_by_role,
        multi_view_reference_profile,
        multi_view_context_lines,
        selected_primary_view_by_role,
        selected_view_match_mode_by_role,
    ) = _build_multi_view_role_context(
        comfy_refs_by_role,
        scene_active_roles,
        selected_view_hint,
        _build_role_type_by_role(scene_active_roles, reference_profiles),
    )
    attached_view_labels_by_role = {
        role: list(((multi_view_reference_profile.get(role) or {}).get("views") or []))
        for role in comfy_roles
        if multi_view_reference_profile.get(role)
    }
    for role, views in attached_view_labels_by_role.items():
        logger.debug(
            "[MULTI_VIEW] role=%s views=%s selected=%s primary=%s match=%s",
            role,
            views,
            selected_view_hint,
            selected_primary_view_by_role.get(role, "unknown"),
            selected_view_match_mode_by_role.get(role, "unknown"),
        )

    comfy_counts = {role: len(comfy_refs_by_role.get(role) or []) for role in comfy_roles}
    connected_active_roles = sorted([
        role for role in comfy_roles
        if bool(connected_refs_by_role.get(role))
    ])
    print("[COMFY IMAGE DEBUG] refsByRole counts=" + json.dumps(comfy_counts, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] refsByRole raw=" + json.dumps(comfy_refs_by_role, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] connected active roles=" + json.dumps(connected_active_roles, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] scene refsUsed=" + json.dumps(scene_refs_used, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] scene refsUsedByRoleKeys=" + json.dumps(sorted(list((scene_refs_used_by_role or {}).keys())) if isinstance(scene_refs_used_by_role, dict) else [], ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] scene refDirectives=" + json.dumps(scene_ref_directives, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] scene participants=" + json.dumps(scene_participants if isinstance(scene_participants, list) else [], ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] scene primaryRole=" + json.dumps(scene_primary_role, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] scene secondaryRoles=" + json.dumps(scene_secondary_roles, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] scene contract=" + json.dumps(scene_contract, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] scene active roles=" + json.dumps(scene_active_roles, ensure_ascii=False))
    print("[COMFY IMAGE DEBUG] has character_2 signal=" + json.dumps({
        "incomingSceneActiveRoles": "character_2" in incoming_scene_active_roles,
        "sceneActiveRoles": "character_2" in scene_active_roles,
        "sceneSecondaryRoles": "character_2" in scene_secondary_roles,
        "sceneContractMustAppear": "character_2" in (scene_contract.get("mustAppear") or []),
        "sceneContractSupportEntityIds": "character_2" in (scene_contract.get("supportEntityIds") or []),
        "refsByRoleCharacter2Count": len(comfy_refs_by_role.get("character_2") or []),
    }, ensure_ascii=False))
    if dropped_by_must_not_appear:
        print("[COMFY IMAGE DEBUG] scene droppedByMustNotAppear=" + json.dumps(dropped_by_must_not_appear, ensure_ascii=False))
    if scene_contract.get("degradedRoles"):
        print("[COMFY IMAGE DEBUG] WARNING scene_selected_role_without_valid_reference=" + json.dumps(scene_contract.get("degradedRoles"), ensure_ascii=False))
    for role in comfy_roles:
        has_urls = bool(comfy_refs_by_role.get(role))
        print(f"[COMFY IMAGE DEBUG] role {role} hasUrls={has_urls}")

    comfy_inline_parts_by_role: dict[str, list[dict]] = {role: [] for role in comfy_roles}
    for role in comfy_roles:
        for ref_url in comfy_refs_by_role.get(role) or []:
            inline_part = _load_reference_image_inline(ref_url)
            if inline_part:
                comfy_inline_parts_by_role[role].append(inline_part)
        inline_count = len(comfy_inline_parts_by_role[role])
        print(f"[COMFY IMAGE DEBUG] inline parts {role}={inline_count}")
        if (comfy_refs_by_role.get(role) or []) and inline_count == 0:
            print(f"[COMFY IMAGE DEBUG] WARNING role received but no inline image created: {role}")

    text_input = str(getattr(refs_obj, "text", "") or "").strip()
    audio_input_url = str(getattr(refs_obj, "audioUrl", "") or "").strip()
    mode_input = str(getattr(refs_obj, "mode", "") or "").strip()
    style_preset_input = str(getattr(refs_obj, "stylePreset", "") or "").strip()
    scene_goal_input = _normalize_scene_goal_for_image(str(getattr(refs_obj, "sceneGoal", "") or "").strip())
    scene_narrative_step_input = _normalize_scene_narrative_step_for_image(str(getattr(refs_obj, "sceneNarrativeStep", "") or "").strip())
    continuity_input = _sanitize_visual_prompt_text(str(getattr(refs_obj, "continuity", "") or "").strip())
    prompt_source = str(getattr(refs_obj, "promptSource", "") or "").strip() or str((getattr(payload, "promptDebug", None) or {}).get("promptSource") or "").strip()
    request_prompt_debug = getattr(payload, "promptDebug", None)
    request_prompt_debug = request_prompt_debug if isinstance(request_prompt_debug, dict) else {}
    planner_meta_input = getattr(refs_obj, "plannerMeta", None)
    planner_meta_input = planner_meta_input if isinstance(planner_meta_input, dict) else {}
    scene_contract["directorGenreIntent"] = (
        str(getattr(refs_obj, "directorGenreIntent", "") or "").strip()
        or str((planner_meta_input or {}).get("directorGenreIntent") or "").strip()
        or str((request_prompt_debug or {}).get("directorGenreIntent") or "").strip()
    )
    scene_contract["directorGenreReason"] = (
        str(getattr(refs_obj, "directorGenreReason", "") or "").strip()
        or str((planner_meta_input or {}).get("directorGenreReason") or "").strip()
        or str((request_prompt_debug or {}).get("directorGenreReason") or "").strip()
    )
    scene_contract["directorToneBias"] = (
        str(getattr(refs_obj, "directorToneBias", "") or "").strip()
        or str((planner_meta_input or {}).get("directorToneBias") or "").strip()
        or str((request_prompt_debug or {}).get("directorToneBias") or "").strip()
    )
    scene_contract["duetLockEnabled"] = bool(getattr(refs_obj, "duetLockEnabled", False) or scene_contract.get("duetLockEnabled"))
    scene_contract["duetIdentityContract"] = str(getattr(refs_obj, "duetIdentityContract", "") or "").strip() or str(scene_contract.get("duetIdentityContract") or "").strip()

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

    role_type_by_role = _build_role_type_by_role(scene_active_roles, reference_profiles)

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
        "sceneRefsUsed": scene_refs_used if isinstance(scene_refs_used, (list, dict)) else [],
        "sceneRefDirectives": scene_ref_directives if isinstance(scene_ref_directives, dict) else {},
        "sceneActiveRoles": scene_active_roles,
        "primaryRole": scene_primary_role,
        "secondaryRoles": scene_secondary_roles,
        "heroEntityId": scene_contract.get("heroEntityId"),
        "supportEntityIds": scene_contract.get("supportEntityIds") or [],
        "mustAppear": scene_contract.get("mustAppear") or [],
        "mustAppearCastRoles": scene_contract.get("mustAppearCastRoles") or [],
        "mustNotAppear": scene_contract.get("mustNotAppear") or [],
        "identityLock": bool(scene_contract.get("identityLock")),
        "environmentLock": bool(scene_contract.get("environmentLock")),
        "styleLock": bool(scene_contract.get("styleLock")),
        "degradedConsistency": bool(scene_contract.get("degradedRoles")),
        "degradedRoles": scene_contract.get("degradedRoles") or [],
        "droppedByMustNotAppear": dropped_by_must_not_appear,
        "resolvedRoles": scene_contract.get("resolvedRoles") or [],
        "sceneCastRoles": scene_cast_roles,
        "worldAnchorRoles": world_anchor_roles,
        "attachedWorldAnchorRoles": [role for role in world_anchor_roles if len(comfy_refs_by_role.get(role) or []) > 0],
        "allowedRolesForImage": sorted(list(allowed_roles_for_image)),
        "filteredOutBySceneContract": filtered_out_by_scene_contract,
        "incomingReadyRefsByRole": {role: len(comfy_refs_by_role.get(role) or []) for role in comfy_roles},
        "rawRefsByRole": {role: len((getattr(refs_obj, "refsByRole", {}) or {}).get(role) or []) for role in comfy_roles},
        "filteredRefsByRole": {role: len(comfy_refs_by_role.get(role) or []) for role in comfy_roles},
        "referenceProfilesSummary": reference_profiles_summary,
        "roleTypeByRole": role_type_by_role,
        "multiViewCountByRole": multi_view_count_by_role,
        "selectedViewHint": selected_view_hint,
        "multiViewReferenceProfile": multi_view_reference_profile,
        "attachedViewLabelsByRole": attached_view_labels_by_role,
        "selectedPrimaryViewByRole": selected_primary_view_by_role,
        "selectedViewMatchModeByRole": selected_view_match_mode_by_role,
        "promptDebug": {
            "sceneId": scene_id,
            "sceneGoal": scene_goal_input or None,
            "sceneNarrativeStep": scene_narrative_step_input or None,
            "continuity": continuity_input or None,
            "promptPreview": (prompt or "")[:400],
            "sceneDeltaPreview": (scene_delta or "")[:400],
            "sceneTextPreview": (scene_text or "")[:400],
            "promptSource": prompt_source or (request_prompt_debug.get("promptSource") if isinstance(request_prompt_debug, dict) else None) or "unknown",
        },
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
            "Scene text may be Russian and visual prompt may be English. Use both when available: visual prompt defines composition/action, and scene text defines narrative context and emotion. "
            "NEVER mention filenames, upload names, or reference preview labels anywhere in natural-language scene text, visual prompt text, or generated prose. Use canonical role IDs only (character_1/character_2/character_3/location/style/props) when identity tokens are required. "
            "DEFAULT NO-TEXT RULE: generated scene images must not contain captions, labels, subtitles, UI overlays, watermarks, scene numbers, scene titles, debug/meta text, side annotations, or typography unless the scene explicitly requests integrated title/typography treatment or requires real in-world signage."
        )

        parts = [{"text": system_prompt}]
        if multi_view_context_lines:
            parts.append({
                "text": "MULTI-VIEW REFERENCE CONTEXT:\n" + "\n".join([
                    f"Selected camera view hint: {selected_view_hint}.",
                    _selected_view_requirement_line(selected_view_hint),
                    *multi_view_context_lines,
                ])
            })

        role_attach_order: list[str] = []
        attached_counts_by_role: dict[str, int] = {role: 0 for role in comfy_roles}
        skipped_roles: list[dict[str, Any]] = []
        inline_load_failures_by_role: dict[str, int] = {role: 0 for role in comfy_roles}

        ordered_cast_roles: list[str] = []
        if hero_entity_id and hero_entity_id in scene_cast_roles:
            ordered_cast_roles.append(hero_entity_id)
        ordered_cast_roles.extend([role for role in support_entity_ids if role in scene_cast_roles and role != hero_entity_id])
        ordered_cast_roles.extend([
            role for role in scene_cast_roles
            if role not in ordered_cast_roles and role not in {"location", "style", "props"}
        ])
        ordered_world_roles = [role for role in ["location", "style", "props"] if role in allowed_roles_for_image]
        ordered_roles_for_attach = ordered_cast_roles + ordered_world_roles

        for role in ordered_roles_for_attach:
            role_parts = comfy_inline_parts_by_role.get(role) or []
            role_urls = comfy_refs_by_role.get(role) or []
            role_connected = bool(connected_refs_by_role.get(role))
            role_attach_order.append(role)
            if role_parts:
                parts.append({"text": f"COMFY role reference images for {role}."})
                parts.extend(role_parts)
                attached_counts_by_role[role] = len(role_parts)
            elif role_urls:
                inline_load_failures_by_role[role] = len(role_urls)
                skipped_roles.append({"role": role, "reason": "inline_load_failed", "urlCount": len(role_urls), "connected": role_connected})
            else:
                skipped_roles.append({"role": role, "reason": "no_urls", "urlCount": 0, "connected": role_connected})

        if character_images:
            parts.append({"text": "Legacy character reference images (compatibility path)."})
            parts.extend(character_images)

        if location_images:
            parts.append({"text": "Legacy location reference images (compatibility path)."})
            parts.extend(location_images)

        if style_images:
            parts.append({"text": "Legacy style reference images (compatibility path)."})
            parts.extend(style_images)

        if props_images:
            parts.append({"text": "Legacy props reference images (compatibility path)."})
            parts.extend(props_images)
            parts.append({"text": "The prop identity is defined by the reference images and must not be replaced."})
            if prop_anchor_label:
                parts.append({"text": f"Session prop anchor label: {prop_anchor_label}. Keep exactly this prop identity."})

        scene_delta = _sanitize_visual_prompt_text(scene_delta)
        scene_text = _sanitize_visual_prompt_text(scene_text)

        if prop_anchor_label:
            scene_delta = _enforce_prop_anchor_text(scene_delta, prop_anchor_label, lang="en")
            scene_text = _enforce_prop_anchor_text(scene_text, prop_anchor_label, lang="ru")

        has_character_refs = bool(character_images or character_refs)
        scene_delta = _adapt_outfit_prompt_for_character_refs(scene_delta, has_character_refs=has_character_refs)
        scene_text = _adapt_outfit_prompt_for_character_refs(scene_text, has_character_refs=has_character_refs)

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

        comfy_assembled_prompt, comfy_assembly_debug = _build_comfy_image_prompt_assembly(
            scene_delta=scene_delta,
            scene_text=scene_text,
            style=style,
            style_anchor=style_anchor,
            lighting_anchor=lighting_anchor,
            location_anchor=location_anchor,
            environment_anchor=environment_anchor,
            weather_anchor=weather_anchor,
            surface_anchor=surface_anchor,
            world_scale_context=world_scale_context,
            entity_scale_anchor_text=entity_scale_anchor_text,
            refs_by_role=comfy_refs_by_role,
            connected_inputs=connected_inputs,
            text_input=text_input,
            audio_url=audio_input_url,
            mode_input=mode_input,
            style_preset_input=style_preset_input,
            scene_goal=scene_goal_input,
            scene_narrative_step=scene_narrative_step_input,
            continuity_input=continuity_input,
            planner_meta=planner_meta_input,
            session_baseline=session_baseline if isinstance(session_baseline, dict) else None,
            effective_character_anchor=effective_character_anchor,
            effective_location_anchor=effective_location_anchor,
            effective_style_anchor=effective_style_anchor,
            scene_id=scene_id,
            scene_contract=scene_contract,
            reference_profiles=reference_profiles,
            selected_view_hint=selected_view_hint,
            multi_view_reference_profile=multi_view_reference_profile,
            multi_view_context_lines=multi_view_context_lines,
        )
        assembled_prompt = comfy_assembled_prompt
        refs_debug["comfyAssemblyDebug"] = comfy_assembly_debug
        print("[COMFY IMAGE ASSEMBLY]", json.dumps(comfy_assembly_debug, ensure_ascii=False))

        has_role_aware_refs = any(len(comfy_refs_by_role.get(role) or []) > 0 for role in comfy_roles)
        has_incoming_role_refs = False
        if isinstance(raw_refs_by_role_incoming, dict):
            normalized_incoming_refs_by_role = _clean_refs_by_role_for_image(raw_refs_by_role_incoming)
            for role in comfy_roles:
                if len(normalized_incoming_refs_by_role.get(role) or []) > 0:
                    has_incoming_role_refs = True
                    break
        has_role_contract = bool(scene_primary_role or scene_secondary_roles or scene_active_roles or must_appear_roles)
        generation_mode = "reference_driven" if (has_role_aware_refs or has_role_contract or has_incoming_role_refs) else ("continuity_chain" if previous_scene_image_inline else "baseline_only")
        print("[SCENARIO IMAGE BACKEND] " + json.dumps({
            "sceneId": scene_id,
            "generationMode": generation_mode,
            "attachedCountsByRole": {role: len(comfy_inline_parts_by_role.get(role) or []) for role in comfy_roles},
            "allowedRolesForImage": sorted(list(allowed_roles_for_image)),
            "sceneActiveRoles": scene_active_roles,
            "primaryRole": scene_primary_role,
            "mustAppear": must_appear_roles,
        }, ensure_ascii=False))

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
            "selectedViewHint": selected_view_hint,
            "multiViewCountByRole": multi_view_count_by_role,
            "referenceProfile": multi_view_reference_profile,
        }
        parts.append({"text": "Scene payload:\n" + json.dumps(scene_payload, ensure_ascii=False)})

        body = {
            "contents": [{
                "role": "user",
                "parts": parts,
            }],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }
        text_parts_total = sum(1 for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
        image_parts_total = sum(1 for part in parts if isinstance(part, dict) and isinstance(part.get("inlineData"), dict))
        hero_attached = bool(hero_entity_id and attached_counts_by_role.get(hero_entity_id, 0) > 0)
        model_parts_summary = {
            "textParts": text_parts_total,
            "imageParts": image_parts_total,
            "characterParts": len(character_images) + len(comfy_inline_parts_by_role.get("character_1") or []) + len(comfy_inline_parts_by_role.get("character_2") or []) + len(comfy_inline_parts_by_role.get("character_3") or []),
            "animalParts": len(comfy_inline_parts_by_role.get("animal") or []),
            "locationParts": len(location_images) + len(comfy_inline_parts_by_role.get("location") or []),
            "styleParts": len(style_images) + len(comfy_inline_parts_by_role.get("style") or []),
            "propsParts": len(props_images) + len(comfy_inline_parts_by_role.get("props") or []),
            "previousSceneParts": 1 if previous_scene_image_inline else 0,
            "heroEntityId": hero_entity_id or None,
            "heroAttached": hero_attached,
            "attachOrder": role_attach_order,
            "attachedCountsByRole": attached_counts_by_role,
            "skippedRoles": skipped_roles + filtered_out_by_scene_contract,
            "inlineLoadFailuresByRole": inline_load_failures_by_role,
            "failedInlineRoleCount": len([role for role in comfy_roles if inline_load_failures_by_role.get(role, 0) > 0]),
            "failedInlineUrlCountByRole": {role: inline_load_failures_by_role.get(role, 0) for role in comfy_roles},
            "comfyRoleAwarePartsByRole": {role: len(comfy_inline_parts_by_role.get(role) or []) for role in comfy_roles},
            "legacyPartsSummary": {
                "character": len(character_images),
                "location": len(location_images),
                "style": len(style_images),
                "props": len(props_images),
            },
            "activeRoles": scene_active_roles,
            "multiViewCountByRole": multi_view_count_by_role,
            "selectedViewHint": selected_view_hint,
        }
        refs_debug["attachOrder"] = role_attach_order
        refs_debug["attachedCountsByRole"] = attached_counts_by_role
        refs_debug["heroAttached"] = hero_attached
        refs_debug["heroEntityId"] = hero_entity_id or None
        refs_debug["inlineLoadFailuresByRole"] = inline_load_failures_by_role
        refs_debug["failedInlineRoleCount"] = len([role for role in comfy_roles if inline_load_failures_by_role.get(role, 0) > 0])
        refs_debug["failedInlineUrlCountByRole"] = {role: inline_load_failures_by_role.get(role, 0) for role in comfy_roles}
        refs_debug["comfyRoleAwarePartsByRole"] = {role: len(comfy_inline_parts_by_role.get(role) or []) for role in comfy_roles}
        refs_debug["legacyPartsSummary"] = {
            "character": len(character_images),
            "location": len(location_images),
            "style": len(style_images),
            "props": len(props_images),
        }
        refs_debug["skippedRoles"] = skipped_roles + filtered_out_by_scene_contract
        refs_debug["modelPartsSummary"] = model_parts_summary
        print("[COMFY IMAGE DEBUG] model parts summary=" + json.dumps(model_parts_summary, ensure_ascii=False))
        print("[CLIP IMAGE GEMINI] request model=" + str(model))
        print("[CLIP IMAGE GEMINI] request config=" + json.dumps(body.get("generationConfig") or {}, ensure_ascii=False))
        resp = post_generate_content(api_key, model, body, timeout=120)
        resp_dict = resp if isinstance(resp, dict) else {}
        response_summary = _summarize_gemini_image_response(resp_dict)
        print("[CLIP IMAGE GEMINI] response summary=" + json.dumps(response_summary, ensure_ascii=False))
        if response_summary.get("httpError"):
            print("[CLIP IMAGE GEMINI] response error text=" + str((resp_dict.get("text") or "")[:500]))

        decoded = _decode_gemini_image(resp_dict)
        image_found = bool(decoded)
        print("[CLIP IMAGE GEMINI] decoded image found=" + json.dumps({"found": image_found}, ensure_ascii=False))
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

        fallback_reason = "gemini_http_error" if response_summary.get("httpError") else "gemini_no_image_part"
        print("[CLIP IMAGE GEMINI] fallback chosen=" + json.dumps({"reason": fallback_reason, "sceneId": scene_id}, ensure_ascii=False))
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


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
        if math.isnan(number) or math.isinf(number):
            return None
        return number
    except Exception:
        return None


def _safe_speech_boundaries_from_analysis(analysis: dict[str, Any]) -> list[float]:
    candidates: list[float] = []
    for phrase in analysis.get("vocalPhrases") or []:
        if not isinstance(phrase, dict):
            continue
        for key in ("start", "end"):
            value = _safe_float(phrase.get(key))
            if value is not None:
                candidates.append(round(value, 3))
    for key in ("pausePoints", "phraseBoundaries"):
        for item in analysis.get(key) or []:
            value = _safe_float(item)
            if value is not None:
                candidates.append(round(value, 3))
    return sorted(set(candidates))


def _speech_boundary_inside_phrase(value: float, analysis: dict[str, Any], tolerance: float = 0.1) -> bool:
    for phrase in analysis.get("vocalPhrases") or []:
        if not isinstance(phrase, dict):
            continue
        start = _safe_float(phrase.get("start"))
        end = _safe_float(phrase.get("end"))
        if start is None or end is None or end <= start:
            continue
        if (start + tolerance) < value < (end - tolerance):
            return True
    return False


def _adjust_speech_safe_slice(t0: float, t1: float, analysis: dict[str, Any]) -> tuple[float, float, dict[str, Any]]:
    debug = {
        "speechSafeAdjusted": False,
        "speechSafeShiftMs": 0,
        "sliceMayCutSpeech": False,
    }
    safe_points = _safe_speech_boundaries_from_analysis(analysis)
    if not safe_points:
        debug["sliceMayCutSpeech"] = _speech_boundary_inside_phrase(t0, analysis) or _speech_boundary_inside_phrase(t1, analysis)
        return t0, t1, debug

    original = (t0, t1)

    def nearest_safe(target: float, left_limit: float, right_limit: float) -> float:
        candidates = [point for point in safe_points if left_limit <= point <= right_limit]
        if not candidates:
            return target
        return min(candidates, key=lambda point: (abs(point - target), point))

    max_shift = 0.85
    if _speech_boundary_inside_phrase(t0, analysis):
        t0 = nearest_safe(t0, max(0.0, t0 - max_shift), min(t1 - 0.2, t0 + max_shift))
    if _speech_boundary_inside_phrase(t1, analysis):
        t1 = nearest_safe(t1, max(t0 + 0.2, t1 - max_shift), t1 + max_shift)

    if t1 <= t0:
        t0, t1 = original

    total_shift_ms = int(round((abs(t0 - original[0]) + abs(t1 - original[1])) * 1000))
    debug["speechSafeAdjusted"] = total_shift_ms > 0
    debug["speechSafeShiftMs"] = total_shift_ms
    debug["sliceMayCutSpeech"] = _speech_boundary_inside_phrase(t0, analysis) or _speech_boundary_inside_phrase(t1, analysis)
    return round(t0, 3), round(t1, 3), debug


def _clip_audio_slice_response(payload: AudioSliceIn):
    scene_id = (payload.sceneId or "").strip()
    if not scene_id:
        return JSONResponse(status_code=400, content={"ok": False, "code": "BAD_REQUEST", "hint": "sceneId_required"})

    start_raw = payload.startSec if payload.startSec is not None else payload.t0
    end_raw = payload.endSec if payload.endSec is not None else payload.t1
    if start_raw is None:
        return JSONResponse(status_code=400, content={"ok": False, "code": "bad_startSec", "hint": "startSec_required"})
    if end_raw is None:
        return JSONResponse(status_code=400, content={"ok": False, "code": "bad_endSec", "hint": "endSec_required"})

    t0 = round(float(start_raw), 3)
    t1 = round(float(end_raw), 3)
    if t0 < 0:
        return JSONResponse(status_code=400, content={"ok": False, "code": "bad_startSec", "hint": "startSec_must_be_non_negative"})
    if t1 <= t0:
        return JSONResponse(status_code=400, content={"ok": False, "code": "bad_range", "hint": "endSec_must_be_greater_than_startSec"})
    if (t1 - t0) > 300.0:
        return JSONResponse(status_code=400, content={"ok": False, "code": "slice_too_long", "hint": "max_slice_sec_300"})

    temp_files: list[str] = []
    try:
        path, resolve_error = _resolve_audio_slice_source(payload.audioUrl, temp_files)
        if not path:
            _debug_audio_slice(payload.audioUrl, path)
            return JSONResponse(status_code=400, content={"ok": False, "code": "invalid_audioUrl", "hint": resolve_error or "audio_source_not_resolved"})

        speech_slice_debug = {
            "speechSafeAdjusted": False,
            "speechSafeShiftMs": 0,
            "sliceMayCutSpeech": False,
        }
        if str(payload.audioStoryMode or "").strip().lower() == "speech_narrative":
            try:
                analysis = analyze_audio(path)
                t0, t1, speech_slice_debug = _adjust_speech_safe_slice(t0, t1, analysis)
            except Exception as exc:
                speech_slice_debug["sliceMayCutSpeech"] = True
                speech_slice_debug["speechSafeAnalysisError"] = str(exc)[:160]

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

        duration = round(t1 - t0, 3)
        asset = _asset_url(filename)
        return {
            "ok": True,
            "sceneId": scene_id,
            "audioUrl": payload.audioUrl,
            "audioSliceUrl": asset,
            "sliceUrl": asset,
            "startSec": t0,
            "endSec": t1,
            "durationSec": duration,
            "t0": t0,
            "t1": t1,
            "duration": duration,
            "audioSliceBackendDurationSec": duration,
            **speech_slice_debug,
        }
    finally:
        for temp_path in temp_files:
            try:
                if temp_path and os.path.isfile(temp_path):
                    os.remove(temp_path)
            except Exception:
                pass


@router.post("/clip/audio-slice")
def clip_audio_slice(payload: AudioSliceIn):
    return _clip_audio_slice_response(payload)


@router.post("/clip/audio/slice")
def clip_audio_slice_v2(payload: AudioSliceIn):
    return _clip_audio_slice_response(payload)



def _update_clip_assemble_job(job_id: str, **updates):
    with CLIP_ASSEMBLE_JOBS_LOCK:
        job = CLIP_ASSEMBLE_JOBS.get(job_id)
        if not job:
            return
        updates["updatedAt"] = time.time()
        job.update(updates)


def _has_valid_intro_image(intro: AssembleIntroIn | None) -> bool:
    return bool(str(getattr(intro, "imageUrl", "") or "").strip())


def _run_clip_assemble_job(job_id: str, payload: AssembleClipIn):
    scenes = payload.scenes or []
    intro = payload.intro
    _ensure_assets_dir()
    temp_files: list[str] = []
    generated_temp_assets: list[str] = []
    prepared_scenes: list[tuple[str, float]] = []
    final_path: str | None = None

    scene_count = len(scenes)
    has_intro = _has_valid_intro_image(intro)
    intro_steps = 1 if has_intro else 0
    total_steps = scene_count + intro_steps
    intro_duration_raw = getattr(intro, "durationSec", 2.5) if intro else 2.5
    try:
        intro_duration = max(0.1, float(intro_duration_raw or 2.5))
    except Exception:
        intro_duration = 2.5
    print(
        "[CLIP ASSEMBLE] source resolution",
        json.dumps(
            {
                "jobId": job_id,
                "sceneCount": scene_count,
                "introPresent": has_intro,
                "introNodeId": str(getattr(intro, "nodeId", "") or ""),
                "introDurationSec": intro_duration if has_intro else 0,
                "total": total_steps,
                "audioPresent": bool(str(payload.audioUrl or "").strip()),
                "format": str(payload.format or "9:16"),
            },
            ensure_ascii=False,
        ),
    )
    print(f"[CLIP ASSEMBLE] job start {job_id}")
    _update_clip_assemble_job(
        job_id,
        status="running",
        stage="preparing",
        label="preparing scenes",
        progressPercent=5,
        current=0,
        total=total_steps,
        totalSteps=total_steps,
        sceneCount=scene_count,
        introIncluded=has_intro,
        introDurationSec=intro_duration if has_intro else 0,
        totalSegments=total_steps,
    )

    try:
        if has_intro:
            intro_image_url = str(getattr(intro, "imageUrl", "") or "").strip()
            intro_label = f"intro 1/{total_steps}" if total_steps > 0 else "preparing intro"
            intro_progress = 10
            if total_steps > 0:
                intro_progress = 10 + int((1 / total_steps) * 60)
            _update_clip_assemble_job(
                job_id,
                status="running",
                stage="preparing",
                label=intro_label,
                current=1,
                total=total_steps,
                totalSteps=total_steps,
                progressPercent=max(10, min(70, intro_progress)),
            )
            intro_path, intro_err = _resolve_media_input(intro_image_url, temp_files)
            if intro_err or not intro_path:
                raise RuntimeError(f"INTRO_MEDIA_RESOLVE_FAILED:{intro_err or 'unknown'}")

            intro_filename = f"clip_intro_{uuid4().hex}.mp4"
            intro_video_path = os.path.join(str(ASSETS_DIR), intro_filename)
            generated_temp_assets.append(intro_video_path)
            width, height = _resolve_assembly_video_geometry(payload.format)
            ffmpeg_ok, ffmpeg_err = _run_ffmpeg([
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                intro_path,
                "-t",
                f"{intro_duration:.3f}",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p",
                "-r",
                "24",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-preset",
                "veryfast",
                "-an",
                intro_video_path,
            ])
            if not ffmpeg_ok:
                if ffmpeg_err == "ffmpeg_missing_install_and_add_to_PATH":
                    raise RuntimeError("FFMPEG_MISSING")
                raise RuntimeError(f"INTRO_RENDER_FAILED:{ffmpeg_err}")

            prepared_scenes.append((intro_video_path, intro_duration))
            print(
                "[CLIP ASSEMBLE] intro segment prepared",
                json.dumps(
                    {
                        "jobId": job_id,
                        "introNodeId": str(getattr(intro, "nodeId", "") or ""),
                        "title": str(getattr(intro, "title", "") or ""),
                        "autoTitle": bool(getattr(intro, "autoTitle", True)),
                        "stylePreset": str(getattr(intro, "stylePreset", "cinematic_dark") or "cinematic_dark"),
                        "durationSec": intro_duration,
                        "width": width,
                        "height": height,
                    },
                    ensure_ascii=False,
                ),
            )

        for idx, scene in enumerate(scenes):
            with CLIP_ASSEMBLE_JOBS_LOCK:
                should_stop = CLIP_ASSEMBLE_JOBS.get(job_id, {}).get("status") == "stopped"
            if should_stop:
                _update_clip_assemble_job(job_id, stage="stopped", label="stopped")
                return

            current_step = intro_steps + idx + 1
            stage_label = f"scene {idx + 1}/{scene_count}"
            print(f"[CLIP ASSEMBLE] stage=preparing label={stage_label}")
            progress = 10
            if total_steps > 0:
                progress = 10 + int((current_step / total_steps) * 60)
            _update_clip_assemble_job(
                job_id,
                status="running",
                stage="preparing",
                label=stage_label,
                current=current_step,
                total=total_steps,
                totalSteps=total_steps,
                progressPercent=max(10, min(70, progress)),
            )

            scene_url = str(scene.videoUrl or "").strip()
            if not scene_url:
                raise RuntimeError(f"scene_{idx}_videoUrl_required")

            scene_path, scene_err = _resolve_media_input(scene_url, temp_files)
            if scene_err or not scene_path:
                continue

            scene_duration, probe_err = _ffprobe_duration(scene_path)
            if probe_err == "ffprobe_missing_install_and_add_to_PATH":
                raise RuntimeError("FFPROBE_MISSING")
            if probe_err or scene_duration is None:
                continue

            requested = scene.requestedDurationSec
            if requested is None:
                requested = scene.providerDurationSec
            if requested is None:
                requested = 5
            try:
                requested_duration = max(0.1, float(requested))
            except Exception:
                requested_duration = 5.0

            trim_duration = min(scene_duration, requested_duration)
            trimmed_filename = f"clip_assembled_scene_{idx}_{uuid4().hex}.mp4"
            trimmed_path = os.path.join(str(ASSETS_DIR), trimmed_filename)
            generated_temp_assets.append(trimmed_path)
            ffmpeg_ok, ffmpeg_err = _run_ffmpeg([
                "ffmpeg", "-y",
                "-i", scene_path,
                "-t", f"{trim_duration:.3f}",
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                "-preset", "veryfast",
                "-an",
                trimmed_path,
            ])
            if not ffmpeg_ok:
                if ffmpeg_err == "ffmpeg_missing_install_and_add_to_PATH":
                    raise RuntimeError("FFMPEG_MISSING")
                continue

            prepared_scenes.append((trimmed_path, trim_duration))

        if not prepared_scenes:
            raise RuntimeError("ASSEMBLE_NO_VALID_SCENES")

        print("[CLIP ASSEMBLE] stage=concat")
        _update_clip_assemble_job(
            job_id,
            status="running",
            stage="concat",
            label="concat scenes",
            progressPercent=80,
            current=total_steps,
            total=total_steps,
            totalSteps=total_steps,
        )

        concat_list_fd, concat_list_path = tempfile.mkstemp(prefix="clip_concat_", suffix=".txt")
        os.close(concat_list_fd)
        temp_files.append(concat_list_path)
        with open(concat_list_path, "w", encoding="utf-8") as f:
            for pth, _ in prepared_scenes:
                escaped = pth.replace("\\", "/").replace("'", "'\\''")
                f.write(f"file '{escaped}'\n")

        assembled_no_audio = os.path.join(str(ASSETS_DIR), f"clip_final_base_{uuid4().hex}.mp4")
        generated_temp_assets.append(assembled_no_audio)
        concat_ok, concat_err = _run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_list_path,
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "veryfast",
            assembled_no_audio,
        ])
        if not concat_ok:
            if concat_err == "ffmpeg_missing_install_and_add_to_PATH":
                raise RuntimeError("FFMPEG_MISSING")
            raise RuntimeError(f"ASSEMBLE_FAILED:{concat_err}")

        final_filename = f"clip_final_{uuid4().hex}.mp4"
        final_path = os.path.join(str(ASSETS_DIR), final_filename)
        audio_applied = False
        audio_delay_sec = intro_duration if has_intro and intro_duration > 0 else 0.0

        audio_url = str(payload.audioUrl or "").strip()
        if audio_url:
            print(f"[CLIP ASSEMBLE] stage=audio_mux delaySec={audio_delay_sec:.3f}")
            _update_clip_assemble_job(
                job_id,
                status="running",
                stage="audio_mux",
                label="adding audio",
                progressPercent=92,
            )
            audio_path, audio_err = _resolve_media_input(audio_url, temp_files)
            if not audio_err and audio_path:
                audio_probe, audio_probe_err = _ffprobe_duration(audio_path)
                if audio_probe_err == "ffprobe_missing_install_and_add_to_PATH":
                    raise RuntimeError("FFPROBE_MISSING")
                if not audio_probe_err and audio_probe is not None:
                    audio_mux_cmd = [
                        "ffmpeg", "-y",
                        "-i", assembled_no_audio,
                    ]
                    if audio_delay_sec > 0:
                        audio_mux_cmd.extend(["-itsoffset", f"{audio_delay_sec:.3f}"])
                    audio_mux_cmd.extend([
                        "-i", audio_path,
                        "-map", "0:v:0",
                        "-map", "1:a:0",
                        "-c:v", "copy",
                        "-c:a", "aac",
                        "-shortest",
                        final_path,
                    ])
                    audio_ok, audio_ffmpeg_err = _run_ffmpeg(audio_mux_cmd)
                    if audio_ok:
                        audio_applied = True
                    else:
                        if audio_ffmpeg_err == "ffmpeg_missing_install_and_add_to_PATH":
                            raise RuntimeError("FFMPEG_MISSING")
                        copy_ok, copy_err = _run_ffmpeg(["ffmpeg", "-y", "-i", assembled_no_audio, "-c", "copy", final_path])
                        if not copy_ok:
                            if copy_err == "ffmpeg_missing_install_and_add_to_PATH":
                                raise RuntimeError("FFMPEG_MISSING")
                            raise RuntimeError(f"ASSEMBLE_FAILED:{copy_err}")
                else:
                    copy_ok, copy_err = _run_ffmpeg(["ffmpeg", "-y", "-i", assembled_no_audio, "-c", "copy", final_path])
                    if not copy_ok:
                        if copy_err == "ffmpeg_missing_install_and_add_to_PATH":
                            raise RuntimeError("FFMPEG_MISSING")
                        raise RuntimeError(f"ASSEMBLE_FAILED:{copy_err}")
            else:
                copy_ok, copy_err = _run_ffmpeg(["ffmpeg", "-y", "-i", assembled_no_audio, "-c", "copy", final_path])
                if not copy_ok:
                    if copy_err == "ffmpeg_missing_install_and_add_to_PATH":
                        raise RuntimeError("FFMPEG_MISSING")
                    raise RuntimeError(f"ASSEMBLE_FAILED:{copy_err}")
        else:
            copy_ok, copy_err = _run_ffmpeg(["ffmpeg", "-y", "-i", assembled_no_audio, "-c", "copy", final_path])
            if not copy_ok:
                if copy_err == "ffmpeg_missing_install_and_add_to_PATH":
                    raise RuntimeError("FFMPEG_MISSING")
                raise RuntimeError(f"ASSEMBLE_FAILED:{copy_err}")

        if not os.path.isfile(final_path):
            raise RuntimeError("final_file_not_created")

        final_video_url = _build_public_static_url(final_filename)
        print(f"[CLIP ASSEMBLE] done {final_video_url}")
        _update_clip_assemble_job(
            job_id,
            status="done",
            stage="done",
            label="done",
            progressPercent=100,
            finalVideoUrl=final_video_url,
            audioApplied=audio_applied,
            current=total_steps,
            total=total_steps,
            totalSteps=total_steps,
            sceneCount=scene_count,
            introIncluded=has_intro,
            introDurationSec=intro_duration if has_intro else 0,
            totalSegments=len(prepared_scenes),
            error=None,
        )
    except Exception as exc:
        with CLIP_ASSEMBLE_JOBS_LOCK:
            is_stopped = CLIP_ASSEMBLE_JOBS.get(job_id, {}).get("status") == "stopped"
        if is_stopped:
            _update_clip_assemble_job(job_id, stage="stopped", label="stopped")
            return
        message = str(exc)[:500]
        print(f"[CLIP ASSEMBLE] error {message}")
        _update_clip_assemble_job(
            job_id,
            status="error",
            stage="error",
            label="error",
            error=message,
        )
    finally:
        for pth in generated_temp_assets:
            try:
                if pth and pth != final_path and os.path.isfile(pth):
                    os.remove(pth)
            except Exception:
                pass
        for pth in temp_files:
            try:
                if os.path.isfile(pth):
                    os.remove(pth)
            except Exception:
                pass


@router.post("/clip/assemble")
def clip_assemble(payload: AssembleClipIn):
    scenes = payload.scenes or []
    if not scenes:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "BAD_REQUEST", "hint": "scenes_required_and_must_be_non_empty"},
        )

    job_id = uuid4().hex
    scene_count = len(scenes)
    has_intro = _has_valid_intro_image(payload.intro)
    intro_steps = 1 if has_intro else 0
    total_steps = scene_count + intro_steps
    intro_duration_raw = getattr(payload.intro, "durationSec", 2.5) if payload.intro else 2.5
    try:
        intro_duration = max(0.1, float(intro_duration_raw or 2.5)) if has_intro else 0
    except Exception:
        intro_duration = 2.5 if has_intro else 0
    with CLIP_ASSEMBLE_JOBS_LOCK:
        CLIP_ASSEMBLE_JOBS[job_id] = {
            "jobId": job_id,
            "status": "queued",
            "stage": "queued",
            "label": "queued",
            "current": 0,
            "total": total_steps,
            "totalSteps": total_steps,
            "progressPercent": 0,
            "finalVideoUrl": None,
            "audioApplied": False,
            "sceneCount": scene_count,
            "introIncluded": has_intro,
            "introDurationSec": intro_duration,
            "totalSegments": total_steps,
            "error": None,
            "updatedAt": time.time(),
        }

    threading.Thread(target=_run_clip_assemble_job, args=(job_id, payload), daemon=True).start()
    return {"ok": True, "jobId": job_id}


@router.get("/clip/assemble/status/{job_id}")
def clip_assemble_status(job_id: str):
    safe_job_id = str(job_id or "").strip()
    with CLIP_ASSEMBLE_JOBS_LOCK:
        job = CLIP_ASSEMBLE_JOBS.get(safe_job_id)
        if not job:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "code": "ASSEMBLE_JOB_NOT_FOUND", "hint": "job_id_not_found_or_expired"},
            )
        return {"ok": True, **job}


@router.post("/clip/assemble/stop/{job_id}")
def clip_assemble_stop(job_id: str):
    safe_job_id = str(job_id or "").strip()
    with CLIP_ASSEMBLE_JOBS_LOCK:
        job = CLIP_ASSEMBLE_JOBS.get(safe_job_id)
        if not job:
            return JSONResponse(
                status_code=404,
                content={"ok": False, "code": "ASSEMBLE_JOB_NOT_FOUND", "hint": "job_id_not_found_or_expired"},
            )
        job.update({"status": "stopped", "stage": "stopped", "label": "stopped", "error": None, "updatedAt": time.time()})
    return {"ok": True, "jobId": safe_job_id, "status": "stopped"}


def _normalize_clip_video_response_payload(response_obj) -> tuple[dict, int]:
    if isinstance(response_obj, JSONResponse):
        status_code = int(getattr(response_obj, "status_code", 500) or 500)
        body_raw = getattr(response_obj, "body", b"{}")
        try:
            parsed = json.loads(body_raw.decode("utf-8")) if isinstance(body_raw, (bytes, bytearray)) else dict(body_raw or {})
        except Exception:
            parsed = {"ok": False, "code": "VIDEO_RESPONSE_PARSE_FAILED", "hint": "invalid_json_response"}
        return parsed if isinstance(parsed, dict) else {"ok": False}, status_code

    if isinstance(response_obj, dict):
        return response_obj, 200

    return {"ok": False, "code": "VIDEO_RESPONSE_INVALID", "hint": "unexpected_response_type"}, 500


@router.get("/clip/video/comfy-output")
def clip_video_comfy_output(filename: str, subfolder: str = "", type: str = "output"):
    safe_filename = str(filename or "").strip()
    safe_subfolder = str(subfolder or "").strip()
    safe_type = str(type or "output").strip() or "output"
    if not safe_filename:
        return JSONResponse(
            status_code=400,
            content={"ok": False, "code": "COMFY_OUTPUT_URL_INVALID", "hint": "filename_required"},
        )

    comfy_base = str(settings.COMFY_BASE_URL or "").strip().rstrip("/")
    public_base = str(settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    handoff_strategy = str(settings.COMFY_OUTPUT_HANDOFF_STRATEGY or "backend_proxy").strip().lower() or "backend_proxy"
    logger.debug(
        "[COMFY OUTPUT PROXY] chosen COMFY_BASE_URL=%s PUBLIC_BASE_URL=%s handoff_strategy=%s",
        comfy_base,
        public_base,
        handoff_strategy,
    )
    if not comfy_base:
        return JSONResponse(
            status_code=500,
            content={"ok": False, "code": "COMFY_OUTPUT_PROXY_FAILED", "hint": "COMFY_BASE_URL_not_configured"},
        )

    comfy_view_url = f"{comfy_base}/view?filename={requests.utils.quote(safe_filename)}&type={requests.utils.quote(safe_type)}"
    if safe_subfolder:
        comfy_view_url = (
            f"{comfy_base}/view?filename={requests.utils.quote(safe_filename)}"
            f"&subfolder={requests.utils.quote(safe_subfolder)}&type={requests.utils.quote(safe_type)}"
        )
    print(
        "[COMFY RESULT PROXY] "
        f"filename={safe_filename} subfolder={safe_subfolder} type={safe_type} comfy_view_url={comfy_view_url}"
    )
    try:
        upstream = requests.get(comfy_view_url, stream=True, timeout=(10, 120))
    except RequestException as exc:
        return JSONResponse(
            status_code=502,
            content={"ok": False, "code": "COMFY_OUTPUT_PROXY_FAILED", "hint": str(exc)[:300]},
        )
    logger.debug(
        "[COMFY OUTPUT PROXY] upstream status=%s content_type=%s",
        upstream.status_code,
        str(upstream.headers.get("content-type") or "").strip(),
    )

    if upstream.status_code >= 400:
        body_snippet = ""
        try:
            body_snippet = (upstream.text or "")[:240]
        except Exception:
            body_snippet = ""
        finally:
            upstream.close()
        return JSONResponse(
            status_code=upstream.status_code,
            content={
                "ok": False,
                "code": "COMFY_OUTPUT_NOT_ACCESSIBLE",
                "hint": f"upstream_status={upstream.status_code}",
                "details": body_snippet,
            },
        )

    content_type = str(upstream.headers.get("content-type") or "application/octet-stream").strip() or "application/octet-stream"
    response_headers = {
        "Content-Type": content_type,
        "Cache-Control": "no-cache",
    }
    if upstream.headers.get("content-length"):
        response_headers["Content-Length"] = str(upstream.headers.get("content-length"))

    def _stream_and_close():
        try:
            for chunk in upstream.iter_content(chunk_size=1024 * 256):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(_stream_and_close(), headers=response_headers, status_code=200)


def _run_clip_video_job(job_id: str, payload: ClipVideoIn):
    source_image_url = str(payload.imageUrl or payload.startImageUrl or payload.endImageUrl or "").strip()
    print(f"[CLIP VIDEO JOB WORKER] start jobId={job_id} source_image_url={source_image_url}")
    with CLIP_VIDEO_JOBS_LOCK:
        job = CLIP_VIDEO_JOBS.get(job_id)
        if not job:
            return
        job.update({"status": "running", "updatedAt": time.time()})

    try:
        response_obj = clip_video(payload)
        out, status_code = _normalize_clip_video_response_payload(response_obj)
        status = "done" if status_code < 400 and bool(out.get("ok")) else "error"
        video_url = str(out.get("videoUrl") or "").strip()
        provider_name = str(out.get("provider") or payload.provider or "").strip().lower()
        if status == "done" and not video_url:
            status = "error"
            out = {
                **(out if isinstance(out, dict) else {}),
                "ok": False,
                "code": "COMFY_OUTPUT_URL_INVALID" if provider_name == "comfy_remote" else "VIDEO_URL_MISSING",
                "hint": "provider_marked_done_without_video_url",
            }
        if video_url:
            print(f"[CLIP VIDEO JOB WORKER] result_received jobId={job_id} video_url={video_url}")
        with CLIP_VIDEO_JOBS_LOCK:
            job = CLIP_VIDEO_JOBS.get(job_id)
            if not job:
                return
            job.update({
                "status": status,
                "videoUrl": video_url or None,
                "provider": provider_name or str(job.get("provider") or payload.provider or "").strip().lower() or None,
                "mode": str(out.get("mode") or "").strip(),
                "model": str(out.get("model") or "").strip(),
                "workflowKey": str(((out.get("debug") or {}).get("workflow_key") if isinstance(out.get("debug"), dict) else "") or "").strip(),
                "providerJobId": str(out.get("taskId") or job.get("providerJobId") or "").strip(),
                "requestedDurationSec": out.get("requestedDurationSec"),
                "providerDurationSec": out.get("providerDurationSec"),
                "error": None if status == "done" else str(out.get("details") or out.get("hint") or out.get("code") or f"HTTP_{status_code}"),
                "requestedPromptPreview": str(((out.get("debug") or {}).get("requestedPromptPreview") if isinstance(out.get("debug"), dict) else "") or ""),
                "effectivePromptPreview": str(((out.get("debug") or {}).get("effectivePromptPreview") if isinstance(out.get("debug"), dict) else "") or ""),
                "effectivePromptLength": int(((out.get("debug") or {}).get("effectivePromptLength") if isinstance(out.get("debug"), dict) else 0) or 0),
                "promptPatchedNodeIds": ((out.get("debug") or {}).get("promptPatchedNodeIds") if isinstance(out.get("debug"), dict) and isinstance((out.get("debug") or {}).get("promptPatchedNodeIds"), list) else []),
                "updatedAt": time.time(),
                "completedAt": time.time() if status == "done" else None,
            })
        if status == "done":
            print(f"[CLIP VIDEO JOB WORKER] terminal_transition jobId={job_id} status=done final_video_url={video_url}")
            print(f"[CLIP VIDEO JOB WORKER] status_done jobId={job_id}")
        else:
            print(
                "[CLIP VIDEO JOB FINALIZE] "
                f"jobId={job_id} status=error provider={provider_name} "
                f"code={str(out.get('code') or '').strip()} error={str(out.get('details') or out.get('hint') or out.get('code') or '')[:300]}"
            )
    except Exception as exc:
        print(f"[CLIP VIDEO JOB WORKER] failed jobId={job_id} error={str(exc)[:300]}")
        with CLIP_VIDEO_JOBS_LOCK:
            job = CLIP_VIDEO_JOBS.get(job_id)
            if not job:
                return
            job.update({"status": "error", "error": str(exc), "updatedAt": time.time(), "completedAt": None})
        print(f"[CLIP VIDEO JOB WORKER] terminal_transition jobId={job_id} status=error")


@router.post("/clip/video/start")
def clip_video_start(payload: ClipVideoIn):
    scene_id = str(payload.sceneId or "").strip() or "scene"
    provider = str(payload.provider or settings.VIDEO_PROVIDER_DEFAULT or "kie").strip().lower() or "kie"
    job_id = uuid4().hex
    with CLIP_VIDEO_JOBS_LOCK:
        CLIP_VIDEO_JOBS[job_id] = {
            "ok": True,
            "jobId": job_id,
            "sceneId": scene_id,
            "provider": provider,
            "providerJobId": None,
            "status": "queued",
            "videoUrl": None,
            "mode": None,
            "model": None,
            "workflowKey": None,
            "requestedDurationSec": None,
            "providerDurationSec": None,
            "error": None,
            "requestedPromptPreview": "",
            "effectivePromptPreview": "",
            "effectivePromptLength": 0,
            "promptPatchedNodeIds": [],
            "updatedAt": time.time(),
            "completedAt": None,
        }

    print(f"[CLIP VIDEO JOB] created jobId={job_id} sceneId={scene_id} provider={provider}")

    threading.Thread(target=_run_clip_video_job, args=(job_id, payload), daemon=True).start()
    return {"ok": True, "jobId": job_id, "sceneId": scene_id, "status": "queued"}


@router.get("/clip/video/status/{job_id}")
def clip_video_status(job_id: str):
    safe_job_id = str(job_id or "").strip()
    with CLIP_VIDEO_JOBS_LOCK:
        job = CLIP_VIDEO_JOBS.get(safe_job_id)
        if not job:
            print(f"[CLIP VIDEO JOB STATUS] read jobId={safe_job_id} status=not_found hasVideoUrl=False")
            return {"ok": False, "status": "not_found", "code": "VIDEO_JOB_NOT_FOUND", "hint": "job_id_not_found_or_expired"}
        print(
            "[CLIP VIDEO JOB STATUS] "
            f"read jobId={safe_job_id} status={job.get('status')} hasVideoUrl={bool(str(job.get('videoUrl') or '').strip())}"
        )
        return {"ok": True, **job}


@router.post("/clip/video")
def clip_video(payload: ClipVideoIn):
    scene_id = str(payload.sceneId or "").strip() or "scene"
    transition_type = _normalize_clip_video_transition_type(payload.transitionType)
    render_mode = str(payload.renderMode or "").strip().lower()
    is_lipsync = bool(payload.lipSync is True or render_mode == "avatar_lipsync")
    audio_slice_url = str(payload.audioSliceUrl or "").strip()
    continuation_source_scene_id = str(payload.continuationSourceSceneId or "").strip()
    continuation_source_asset_url = str(payload.continuationSourceAssetUrl or "").strip()
    continuation_source_asset_type = _detect_scenario_asset_type(
        continuation_source_asset_url,
        str(payload.continuationSourceAssetType or "").strip(),
    )
    image_url = str(payload.imageUrl or "").strip()
    start_image_url = str(payload.startImageUrl or "").strip()
    end_image_url = str(payload.endImageUrl or "").strip()
    output_format = str(payload.format or "9:16").strip() or "9:16"
    explicit_model = str(payload.resolvedModelKey or "").strip().lower()
    explicit_model_override = _resolve_model_key_from_override(payload.modelFileOverride)
    if explicit_model_override:
        explicit_model = explicit_model_override

    if render_mode == "avatar_lipsync" or is_lipsync:
        mode = "lipsync"
    elif render_mode == "standard_video":
        mode = "continuous" if _is_clip_video_transition_mode(transition_type, start_image_url, end_image_url) else "single"
    elif _is_clip_video_transition_mode(transition_type, start_image_url, end_image_url):
        mode = "continuous"
    else:
        mode = "single"
    legacy_mode = mode

    workflow_override_candidate = str(payload.workflowFileOverride or "").strip()
    if workflow_override_candidate:
        workflow_override_candidate = _normalize_ltx_workflow_key(workflow_override_candidate) or workflow_override_candidate
    final_workflow_key, fallback_workflow_key, workflow_source, workflow_path = _resolve_ltx_workflow_selection(
        payload_workflow_key=workflow_override_candidate or str(payload.resolvedWorkflowKey or ""),
        ltx_mode=str(payload.ltxMode or ""),
        render_mode=render_mode,
        is_lipsync=is_lipsync,
        transition_type=transition_type,
        start_image_url=start_image_url,
        end_image_url=end_image_url,
    )
    payload_workflow_hint = str(payload.resolvedWorkflowKey or "").strip().lower()
    ltx_mode_hint = str(payload.ltxMode or "").strip().lower()
    requires_two_frames_hint = bool(payload.requiresTwoFrames)
    two_frame_payload_hint = bool(start_image_url and end_image_url)
    two_frame_workflow_hint = payload_workflow_hint in {"imag-imag-video-bz", "f_l", "first_last"}
    two_frame_mode_hint = ltx_mode_hint in {"f_l", "first_last"}
    force_two_frame_mode = bool(
        final_workflow_key in LTX_FIRST_LAST_WORKFLOW_KEYS
        or requires_two_frames_hint
        or two_frame_payload_hint
        or two_frame_workflow_hint
        or two_frame_mode_hint
    )

    continuation_requested = bool(
        payload.requiresContinuation
        or payload.continuation
        or payload.continuationFromPrevious
        or ltx_mode_hint == "continuation"
    ) and not force_two_frame_mode
    if force_two_frame_mode:
        final_workflow_key = "f_l"
    elif continuation_requested:
        final_workflow_key = "continuation"

    resolved_model_key, resolved_model_spec, model_source = _resolve_ltx_model_selection(
        payload_model_key=explicit_model,
        workflow_key=final_workflow_key,
    )

    if final_workflow_key in LTX_FIRST_LAST_WORKFLOW_KEYS:
        mode = "continuous"
    elif final_workflow_key in LTX_CONTINUATION_WORKFLOW_KEYS:
        mode = "continuous"
    elif final_workflow_key == "lip_sync":
        mode = "lipsync"
    else:
        mode = "single"

    requested_provider = str(payload.provider or "").strip().lower()
    provider = requested_provider or str(settings.VIDEO_PROVIDER_DEFAULT or "kie").strip().lower() or "kie"
    provider_reason = "payload_or_default"
    if final_workflow_key == "lip_sync":
        provider = requested_provider or "kie"
        provider_reason = "dedicated_lipsync_provider_strategy"
    print(
        "[CLIP VIDEO PROVIDER] "
        f"sceneId={scene_id} ltxMode={str(payload.ltxMode or '').strip()} "
        f"provider={provider} reason={provider_reason} mode={mode} transitionType={transition_type} format={output_format} "
        f"resolvedWorkflowKey={final_workflow_key} resolvedModelKey={resolved_model_key}"
    )
    print(
        "[LTX ROUTER] "
        f"sceneId={scene_id} ltxMode={str(payload.ltxMode or '').strip()} "
        f"resolvedWorkflowKey={str(payload.resolvedWorkflowKey or '').strip()} "
        f"finalWorkflowKey={final_workflow_key} workflowFile={LTX_WORKFLOW_KEY_TO_FILE.get(final_workflow_key, '')} "
        f"provider={provider} mode={mode} "
        f"resolvedModelKey={resolved_model_key}"
    )

    if not resolved_model_spec:
        if final_workflow_key == "continuation":
            resolved_model_key = resolved_model_key or LTX_WORKFLOW_KEY_DEFAULT_MODEL_KEY.get("i2v", "")
            resolved_model_spec = LTX_MODEL_KEY_TO_MODEL_SPEC.get(resolved_model_key)
        if not resolved_model_spec:
            return JSONResponse(
                status_code=422,
                content={"ok": False, "code": "LTX_MODEL_NOT_FOUND", "hint": f"unknown_model_key:{resolved_model_key or 'empty'}"},
            )
    if final_workflow_key != "continuation" and final_workflow_key not in set(resolved_model_spec.get("compatible_workflow_keys") or set()):
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "code": "LTX_MODEL_WORKFLOW_INCOMPATIBLE",
                "hint": f"model={resolved_model_key} is not compatible with workflow={final_workflow_key}",
            },
        )
    if provider != "comfy_remote" and final_workflow_key in (LTX_FIRST_LAST_WORKFLOW_KEYS | LTX_CONTINUATION_WORKFLOW_KEYS):
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "code": "LTX_PROVIDER_MODE_INCOMPATIBLE",
                "hint": f"provider={provider} is not compatible with workflow={final_workflow_key}",
            },
        )
    if provider == "comfy_remote" and final_workflow_key == "lip_sync":
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "code": "LTX_PROVIDER_MODE_INCOMPATIBLE",
                "hint": "provider=comfy_remote is not compatible with workflow=lip_sync",
            },
        )

    continuation_debug = {
        "requested_mode": str(payload.ltxMode or "").strip().lower() or final_workflow_key,
        "resolved_workflow_key": final_workflow_key,
        "actual_mode": "continuation" if final_workflow_key == "continuation" else mode,
        "continuation_used": final_workflow_key == "continuation",
        "continuation_source_asset_type": continuation_source_asset_type,
        "continuation_source_asset_url_present": bool(continuation_source_asset_url),
        "continuation_source_scene_id_present": bool(continuation_source_scene_id),
        "provider": provider,
        "force_two_frame_mode": force_two_frame_mode,
        "requires_two_frames_hint": requires_two_frames_hint,
        "two_frame_payload_hint": two_frame_payload_hint,
        "two_frame_workflow_hint": two_frame_workflow_hint,
    }
    if final_workflow_key == "continuation":
        continuation_validation_code, continuation_validation_hint = _validate_continuation_source(
            continuation_source_scene_id=continuation_source_scene_id,
            continuation_source_asset_url=continuation_source_asset_url,
            continuation_source_asset_type=continuation_source_asset_type,
        )
        if continuation_validation_code:
            return JSONResponse(
                status_code=422,
                content={
                    "ok": False,
                    "code": continuation_validation_code,
                    "hint": continuation_validation_hint,
                    "debug": continuation_debug,
                },
            )
        return JSONResponse(
            status_code=422,
            content={
                "ok": False,
                "code": "LTX_CONTINUATION_NOT_IMPLEMENTED",
                "hint": "continuation mode requested by scene but current continuation execution strategy is not implemented yet",
                "debug": {**continuation_debug, "strategy_layer_reached": True},
            },
        )

    if provider == "comfy_remote":
        source_image_url = image_url or start_image_url or end_image_url
        if not source_image_url:
            return JSONResponse(
                status_code=400,
                content={
                    "ok": False,
                    "code": "VIDEO_SOURCE_IMAGE_REQUIRED",
                    "hint": "imageUrl_required_for_comfy_remote",
                },
            )

        width, height = _resolve_clip_video_dimensions(output_format)
        try:
            requested_duration = float(payload.requestedDurationSec or 5)
        except Exception:
            requested_duration = 5.0
        requested_duration = max(1.0, min(8.0, requested_duration))

        scene_human_visual_anchors = [str(item or "").strip() for item in (payload.sceneHumanVisualAnchors or []) if str(item or "").strip()]
        effective_prompt, prompt_debug = _compose_video_effective_prompt(
            video_prompt=str(payload.videoPrompt or "").strip(),
            transition_action_prompt=str(payload.transitionActionPrompt or "").strip(),
            output_format=output_format,
            requested_duration_sec=requested_duration,
            scene_human_visual_anchors=scene_human_visual_anchors,
            scene_type=str(payload.sceneType or "").strip(),
            shot_type=str(payload.shotType or "").strip(),
            scene_contract=payload.sceneContract if isinstance(payload.sceneContract, dict) else None,
            scene_active_roles=payload.sceneActiveRoles,
            duet_lock_enabled=payload.duetLockEnabled,
            duet_identity_contract=payload.duetIdentityContract,
            director_genre_intent=payload.directorGenreIntent,
        )
        print(
            "[CLIP VIDEO PROMPT TRANSPORT] "
            f"sceneId={scene_id} workflowKey={final_workflow_key} modelKey={resolved_model_key} source_image_url={source_image_url} "
            f"videoPromptLength={prompt_debug.get('videoPromptLength')} transitionActionPromptLength={prompt_debug.get('transitionActionPromptLength')} "
            f"effectivePromptLength={prompt_debug.get('effectivePromptLength')} "
            f"requestedPromptPreview={_prompt_preview(str(payload.videoPrompt or ''), 320)} "
            f"effectivePromptPreview={str(prompt_debug.get('effectivePromptPreview') or '')}"
        )

        try:
            image_bytes, image_ext = _download_image_from_source(source_image_url)
        except Exception as exc:
            return JSONResponse(
                status_code=400,
                content={"ok": False, "code": "comfy_upload_failed", "hint": f"image_download_failed:{str(exc)[:240]}"},
            )

        start_image_bytes = None
        end_image_bytes = None
        if final_workflow_key in LTX_FIRST_LAST_WORKFLOW_KEYS:
            if not end_image_url:
                return JSONResponse(
                    status_code=422,
                    content={"ok": False, "code": "LTX_SECOND_FRAME_REQUIRED", "hint": "endImageUrl_required_for_first_last_workflow"},
                )
            try:
                if start_image_url:
                    start_image_bytes, _ = _download_image_from_source(start_image_url)
                else:
                    start_image_bytes = image_bytes
                end_image_bytes, _ = _download_image_from_source(end_image_url)
            except Exception as exc:
                return JSONResponse(
                    status_code=400,
                    content={"ok": False, "code": "VIDEO_SOURCE_IMAGE_REQUIRED", "hint": f"first_last_image_download_failed:{str(exc)[:240]}"},
                )
        if final_workflow_key == "lip_sync" and not audio_slice_url:
            return JSONResponse(
                status_code=422,
                content={"ok": False, "code": "LTX_AUDIO_REQUIRED_FOR_LIPSYNC", "hint": "audioSliceUrl_required_for_lip_sync_workflow"},
            )

        image_filename = f"{scene_id}_{int(time.time())}.{(image_ext or 'jpg').lower()}"
        print(
            "[COMFY REMOTE] "
            f"workflow={workflow_path} width={width} height={height} requestedDurationSec={requested_duration}"
        )

        comfy_out, comfy_err = run_comfy_image_to_video(
            image_bytes=image_bytes,
            image_filename=image_filename,
            prompt=effective_prompt,
            width=width,
            height=height,
            requested_duration_sec=requested_duration,
            workflow_path=workflow_path,
            workflow_key=final_workflow_key,
            model_key=resolved_model_key,
            model_spec=resolved_model_spec,
            scene_id=scene_id,
            start_image_bytes=start_image_bytes,
            end_image_bytes=end_image_bytes,
            audio_url=audio_slice_url,
            continuation_source_asset_url=continuation_source_asset_url,
            continuation_source_asset_type=continuation_source_asset_type,
            requested_mode=str(payload.ltxMode or ""),
        )
        if comfy_err or not comfy_out:
            err_text = str(comfy_err or "comfy_remote_failed")
            code = "comfy_unreachable"
            status_code = 500
            if err_text.startswith("capability_error:"):
                parts = err_text.split(":", 2)
                capability_code = parts[1] if len(parts) > 1 else "LTX_MODE_NOT_IMPLEMENTED"
                capability_hint = parts[2] if len(parts) > 2 else "requested_ltx_mode_not_supported_by_comfy_remote"
                return JSONResponse(
                    status_code=422,
                    content={"ok": False, "code": capability_code, "hint": capability_hint[:300]},
                )
            if "upload_failed:upload_connect_timeout" in err_text:
                code = "comfy_upload_connect_timeout"
                status_code = 504
            elif "upload_failed:upload_read_timeout" in err_text:
                code = "comfy_upload_read_timeout"
                status_code = 504
            elif "upload_failed:upload_non_200" in err_text:
                code = "comfy_upload_http_error"
                status_code = 502
            elif "upload_failed:upload_response_invalid_json" in err_text or "upload_failed:upload_response_invalid_json_root" in err_text:
                code = "comfy_upload_invalid_response"
                status_code = 502
            elif "upload_failed" in err_text:
                code = "comfy_upload_failed"
            elif "prompt_submit_failed:prompt_connect_timeout" in err_text:
                code = "comfy_prompt_connect_timeout"
                status_code = 504
            elif "prompt_submit_failed:prompt_read_timeout" in err_text:
                code = "comfy_prompt_read_timeout"
                status_code = 504
            elif "prompt_submit_failed" in err_text:
                code = "comfy_prompt_submit_failed"
            elif "history_wait_failed:timeout" in err_text:
                code = "comfy_timeout"
                status_code = 504
            elif "COMFY_OUTPUT_URL_INVALID" in err_text:
                code = "COMFY_OUTPUT_URL_INVALID"
                status_code = 502
            elif "COMFY_OUTPUT_NOT_ACCESSIBLE" in err_text:
                code = "COMFY_OUTPUT_NOT_ACCESSIBLE"
                status_code = 502
            elif "COMFY_OUTPUT_PROXY_FAILED" in err_text:
                code = "COMFY_OUTPUT_PROXY_FAILED"
                status_code = 502
            elif "extract_failed" in err_text:
                code = "comfy_output_missing"
            elif "workflow_" in err_text or "missing_node" in err_text or "missing_input" in err_text:
                code = "comfy_invalid_workflow"
            return JSONResponse(status_code=status_code, content={"ok": False, "code": code, "hint": err_text[:300]})

        video_url = str(comfy_out.get("videoUrl") or "").strip()
        prompt_id = str(comfy_out.get("taskId") or "").strip()
        comfy_debug = comfy_out.get("debug") if isinstance(comfy_out, dict) and isinstance(comfy_out.get("debug"), dict) else {}
        print(
            "[CLIP VIDEO PROMPT PATCHED NODES] "
            f"sceneId={scene_id} workflowKey={final_workflow_key} modelKey={resolved_model_key} "
            f"promptPatchedNodeIds={comfy_debug.get('prompt_patched_node_ids') or []} "
            f"finalPromptPreview={str(comfy_debug.get('final_prompt_preview') or '')}"
        )
        print(f"[COMFY REMOTE] prompt_id={prompt_id}")
        print(f"[COMFY REMOTE] video_url={video_url}")

        return {
            "ok": True,
            "sceneId": scene_id,
            "videoUrl": video_url,
            "provider": "comfy_remote",
            "model": resolved_model_key,
            "taskId": prompt_id,
            "mode": str(comfy_out.get("mode") or mode),
            "requestedDurationSec": round(float(requested_duration), 3),
            "providerDurationSec": round(float(comfy_out.get("requestedDurationSec") or requested_duration), 3),
            "debug": {
                **comfy_debug,
                "requestedPromptPreview": prompt_debug.get("requestedPromptPreview"),
                "effectivePromptPreview": prompt_debug.get("effectivePromptPreview"),
                "effectivePromptLength": prompt_debug.get("effectivePromptLength"),
                "genreHardeningApplied": prompt_debug.get("genreHardeningApplied"),
                "genreHardeningSource": prompt_debug.get("genreHardeningSource"),
                "genreHardeningPreview": prompt_debug.get("genreHardeningPreview"),
                "duetHardeningApplied": prompt_debug.get("duetHardeningApplied"),
                "duetHardeningSource": prompt_debug.get("duetHardeningSource"),
                "duetContractDetected": prompt_debug.get("duetContractDetected"),
                "duetContractPreview": prompt_debug.get("duetContractPreview"),
                "promptPatchedNodeIds": comfy_debug.get("prompt_patched_node_ids") or [],
            },
        }

    guard_prompt = " ".join([
        str(payload.videoPrompt or "").strip(),
        str(payload.transitionActionPrompt or "").strip(),
    ]).strip()
    if mode == "lipsync":
        if _is_back_view_scene_prompt(guard_prompt):
            payload.lipSync = False
            is_lipsync = False
            mode = "continuous"
            print("[CLIP LIPSYNC GUARD] disabled (back view scene)")
        elif _is_face_too_small_for_lipsync(guard_prompt):
            payload.lipSync = False
            is_lipsync = False
            mode = "continuous"
            print("[CLIP LIPSYNC GUARD] disabled (face too small / distant shot)")

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

    validation_code, validation_hint = _validate_ltx_workflow_strategy(
        scene_id=scene_id,
        workflow_key=final_workflow_key,
        image_strategy=str(payload.imageStrategy or ""),
        requires_two_frames=bool(payload.requiresTwoFrames),
        image_url=image_url,
        start_image_url=start_image_url,
        end_image_url=end_image_url,
        audio_slice_url=audio_slice_url,
        continuation_source_scene_id=continuation_source_scene_id,
        continuation_source_asset_url=continuation_source_asset_url,
        continuation_source_asset_type=continuation_source_asset_type,
    )
    if validation_code:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "code": validation_code,
                "hint": validation_hint,
            },
        )

    print(
        "[LTX ROUTER] "
        f"sceneId={scene_id} "
        f"ltxMode={str(payload.ltxMode or '').strip()} "
        f"imageStrategy={str(payload.imageStrategy or '').strip()} "
        f"payloadResolvedWorkflowKey={str(payload.resolvedWorkflowKey or '').strip()} "
        f"fallbackResolvedWorkflowKey={fallback_workflow_key} "
        f"finalWorkflowKey={final_workflow_key} "
        f"workflowFile={workflow_path} "
        f"resolvedModelKey={str(payload.resolvedModelKey or '').strip()} "
        f"finalResolvedModelKey={resolved_model_key} "
        f"modelSource={model_source} "
        f"legacyMode={legacy_mode} "
        f"finalMode={mode} "
        f"workflowSource={workflow_source}"
    )

    scene_human_visual_anchors = [str(item or "").strip() for item in (payload.sceneHumanVisualAnchors or []) if str(item or "").strip()]
    effective_prompt, prompt_debug = _compose_video_effective_prompt(
        video_prompt=str(payload.videoPrompt or "").strip(),
        transition_action_prompt=str(payload.transitionActionPrompt or "").strip(),
        output_format=output_format,
        requested_duration_sec=payload.requestedDurationSec,
        scene_human_visual_anchors=scene_human_visual_anchors,
        scene_type=str(payload.sceneType or "").strip(),
        shot_type=str(payload.shotType or "").strip(),
        scene_contract=payload.sceneContract if isinstance(payload.sceneContract, dict) else None,
        scene_active_roles=payload.sceneActiveRoles,
        duet_lock_enabled=payload.duetLockEnabled,
        duet_identity_contract=payload.duetIdentityContract,
        director_genre_intent=payload.directorGenreIntent,
    )
    if mode == "lipsync":
        effective_prompt = _build_lipsync_avatar_prompt(effective_prompt, str(payload.shotType or ""))
    if not effective_prompt:
        effective_prompt = build_clip_video_motion_prompt(base_prompt="", fmt=output_format, seconds=payload.requestedDurationSec)

    print(f"[CLIP VIDEO] transition_type={transition_type}")
    print(f"[CLIP VIDEO] effective_prompt={effective_prompt[:300]}")
    print(
        "[CLIP VIDEO PROMPT TRANSPORT] "
        f"sceneId={scene_id} workflowKey={final_workflow_key} modelKey={resolved_model_key or 'n/a'} "
        f"source_image_url=pending videoPromptLength={prompt_debug.get('videoPromptLength')} "
        f"transitionActionPromptLength={prompt_debug.get('transitionActionPromptLength')} effectivePromptLength={len(effective_prompt)} "
        f"requestedPromptPreview={str(prompt_debug.get('requestedPromptPreview') or '')} "
        f"effectivePromptPreview={_prompt_preview(effective_prompt, 500)}"
    )
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
    if explicit_model and explicit_model not in LTX_MODEL_KEY_TO_MODEL_SPEC:
        selected_model = explicit_model

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

    if selected_model == "omni-human-1.5" and mode == "lipsync":
        effective_prompt += """
    The character should perform naturally with controlled emotional motion.
    Allow clearer mouth articulation and slightly stronger lip opening while singing, matching the emotional intensity of the audio.
    Allow subtle expressive hand gestures near the torso and chest, and slight shoulder movement, as long as the hands do not block the mouth or distort the outfit.

    Keep the body mostly facing the camera.
    Avoid strong body rotations, avoid turning the hips or legs sideways, and avoid large pose changes.
    Avoid dramatic full-body movement.

    Preserve the clothing exactly as in the reference image.
    Preserve the exact outfit colors, logos, materials, and details from the reference.
    Do not change the color of the outfit.
    Do not redraw, distort, remove, or hallucinate logos or clothing patterns.
    Do not modify the design of the hoodie or pants during motion.
    Keep the lower-body clothing appearance stable during motion.

    Motion should feel emotional, musical, and alive, but controlled enough to keep the outfit and logos intact.
    """

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

    print(
        "[LTX ROUTER] "
        f"sceneId={scene_id} "
        f"finalWorkflowKey={final_workflow_key} "
        f"workflowFile={workflow_path} "
        f"resolvedModelKey={resolved_model_key or 'n/a'} "
        f"finalModelKey={selected_model}"
    )

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
        "debug": {
            "requestedPromptPreview": prompt_debug.get("requestedPromptPreview"),
            "effectivePromptPreview": _prompt_preview(effective_prompt, 500),
            "effectivePromptLength": len(effective_prompt),
            "genreHardeningApplied": prompt_debug.get("genreHardeningApplied"),
            "genreHardeningSource": prompt_debug.get("genreHardeningSource"),
            "genreHardeningPreview": prompt_debug.get("genreHardeningPreview"),
            "duetHardeningApplied": prompt_debug.get("duetHardeningApplied"),
            "duetHardeningSource": prompt_debug.get("duetHardeningSource"),
            "duetContractDetected": prompt_debug.get("duetContractDetected"),
            "duetContractPreview": prompt_debug.get("duetContractPreview"),
            "promptPatchedNodeIds": [],
        },
    }
