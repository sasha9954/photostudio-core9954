from __future__ import annotations

import copy
import hashlib
import logging
import json
import math
import socket
import time
from collections import deque
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

import requests
from requests import ConnectTimeout, ReadTimeout, RequestException, Response

from app.core.config import settings

logger = logging.getLogger(__name__)
_COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED: bool | None = None

COMFY_LTX_CAPABILITIES = {
    "single_image": True,
    "first_last": True,
    "audio_sensitive": True,
    "lip_sync": True,
    "continuation": False,
}
COMFY_AUDIO_WORKFLOW_FILES = {
    # production audio-sensitive workflow key
    "lip_sync": "image-lipsink-video-music.json",
}
COMFY_AUDIO_INPUT_NODE_CLASS_NAMES = {
    "loadaudio",
    "vhs_loadaudio",
    "vhs_loadaudioupload",
    "loadaudiofromurl",
    "loadaudiofrompath",
    "mediautilities_audiourlloader",
}
COMFY_AUDIO_SOURCE_NODE_CLASS_NAMES = {
    "loadaudio",
    "vhs_loadaudio",
    "vhs_loadaudioupload",
    "loadaudiofromurl",
    "loadaudiofrompath",
    "mediautilities_audiourlloader",
}
COMFY_AUDIO_DOWNSTREAM_NODE_CLASS_NAMES = {
    "trimaudioduration",
    "ltxvaudiovaeencode",
    "ltxvaudiovaedecode",
    "createvideo",
    "ltxvconcatavlatent",
    "ltxvseparateavlatent",
}
COMFY_AUDIO_INPUT_KEYS = ("audio", "audio_file", "audio_path", "path", "filename", "url")
COMFY_AUDIO_INPUT_WIDGET_FALLBACK_KEYS = ("value", "text")
COMFY_AUDIO_TITLE_HINTS = ("audio", "music", "sound", "lipsync", "lip sync", "wav", "mp3")
COMFY_LIPSYNC_WORKFLOW_AUDIO_FALLBACK_CLASS_NAMES = {"ltxvemptylatentaudio"}
COMFY_MAIN_VIDEO_BRANCH_CLASS_NAMES = {
    "ltxvconcatavlatent",
    "ltxvseparateavlatent",
    "ltxvaudiovaeencode",
    "ltxvaudiovaedecode",
    "trimaudioduration",
    "createvideo",
    "savevideo",
}
COMFY_LTX_AV_AUDIO_PATH_CLASS_NAMES = {
    "ltxvaudiovaeencode",
    "ltxvconcatavlatent",
    "ltxvseparateavlatent",
    "ltxvaudiovaedecode",
    "createvideo",
}
COMFY_MOUTH_CONTROL_HINTS = ("phoneme", "viseme", "mouth", "avatar", "face", "wav2lip", "sadtalker")
COMFY_NON_MOUTH_LIPSYNC_TITLES = ("lipsink", "lip sink", "lipsync-video", "lip-sync-video")
COMFY_AUDIO_UNSAFE_URL_HOSTS = {"localhost", "127.0.0.1", "0.0.0.0"}
COMFY_AUDIO_URL_COMPATIBLE_INPUT_KEYS = {"url", "path", "audio_path"}
COMFY_AUDIO_URL_COMPATIBLE_CLASS_NAMES = {"loadaudiofromurl", "loadaudiofrompath", "mediautilities_audiourlloader"}
COMFY_AUDIO_UPLOAD_FILENAME_CLASS_NAMES = {"loadaudio", "vhs_loadaudio", "vhs_loadaudioupload"}
COMFY_DYNAMIC_DISCOVERY_CLASS_NAMES = {
    "prompt_text": {"primitivestringmultiline"},
    "prompt_encode": {"cliptextencode"},
    "image_input": {"loadimage"},
    "audio_input": {
        "loadaudio",
        "vhs_loadaudio",
        "vhs_loadaudioupload",
        "loadaudiofromurl",
        "loadaudiofrompath",
        "mediautilities_audiourlloader",
    },
    "trim_audio": {"trimaudioduration"},
    "audio_encode": {"ltxvaudiovaeencode"},
    "save_video": {"savevideo"},
    "create_video": {"createvideo"},
}

COMFY_LTX_WORKFLOW_REQUIREMENTS = {
    "i2v": {"single_image": True, "first_last": False, "audio_sensitive": False, "lip_sync": False, "continuation": False},
    "f_l": {"single_image": False, "first_last": True, "audio_sensitive": False, "lip_sync": False, "continuation": False},
    "continuation": {"single_image": False, "first_last": False, "audio_sensitive": False, "lip_sync": False, "continuation": True},
    "lip_sync": {"single_image": True, "first_last": False, "audio_sensitive": True, "lip_sync": True, "continuation": False},
}
COMFY_LEGACY_WORKFLOW_ALIASES = {"i2v_as": "i2v", "f_l_as": "f_l"}
COMFY_WORKFLOW_FAMILY = {
    "i2v": "ltx_image_video",
    "f_l": "ltx_first_last",
    "continuation": "ltx_continuation",
    "lip_sync": "comfy_lip_sync_audio",
}
COMFY_MODEL_GATED_WORKFLOW_KEYS = {"i2v", "f_l", "continuation"}
MODEL_KEY_TO_MODEL_SPEC = {
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
MODEL_PATCH_NODE_TYPES = {"CheckpointLoaderSimple", "LTXAVTextEncoderLoader", "LTXVAudioVAELoader"}


def _validate_comfy_ltx_request(
    *,
    workflow_key: str,
    start_image_bytes: bytes | None,
    end_image_bytes: bytes | None,
    audio_bytes: bytes | None,
    audio_url: str | None,
    continuation_source_asset_url: str | None = None,
    continuation_source_asset_type: str | None = None,
) -> tuple[str | None, str | None]:
    raw_key = str(workflow_key or "i2v").strip().lower() or "i2v"
    key = COMFY_LEGACY_WORKFLOW_ALIASES.get(raw_key, raw_key)
    requirements = COMFY_LTX_WORKFLOW_REQUIREMENTS.get(key)
    if not requirements:
        return "LTX_MODE_NOT_IMPLEMENTED", f"workflow_key_not_supported:{key}"

    if requirements["lip_sync"] and not (audio_bytes or str(audio_url or "").strip()):
        return "LTX_AUDIO_REQUIRED_FOR_LIPSYNC", "audio_input_required_for_lip_sync_mode"
    if requirements["lip_sync"] and not COMFY_LTX_CAPABILITIES["lip_sync"]:
        return "LTX_LIPSYNC_NOT_IMPLEMENTED", "lip_sync_mode_not_implemented_for_comfy_remote"
    if requirements["first_last"] and not COMFY_LTX_CAPABILITIES["first_last"]:
        return "LTX_FIRST_LAST_NOT_IMPLEMENTED", "first_last_mode_not_implemented_for_comfy_remote"
    if requirements["lip_sync"] and requirements["audio_sensitive"] and not COMFY_LTX_CAPABILITIES["audio_sensitive"]:
        return "LTX_AUDIO_REACTIVE_NOT_IMPLEMENTED", "lip_sync_audio_path_not_implemented_for_comfy_remote"
    if requirements["single_image"] and not COMFY_LTX_CAPABILITIES["single_image"]:
        return "LTX_MODE_NOT_IMPLEMENTED", "single_image_mode_not_implemented_for_comfy_remote"

    if requirements["first_last"] and not (start_image_bytes and end_image_bytes):
        return "LTX_SECOND_FRAME_REQUIRED", "start_image_and_end_image_required_for_first_last_mode"
    if requirements["continuation"]:
        source_url = str(continuation_source_asset_url or "").strip()
        if not source_url:
            return "LTX_CONTINUATION_SOURCE_REQUIRED", "continuation_source_asset_url_missing"
        normalized_source_type = str(continuation_source_asset_type or "").strip().lower()
        if normalized_source_type == "video":
            return (
                "LTX_CONTINUATION_SOURCE_INCOMPATIBLE",
                "continuation source resolved to video asset but current continuation path requires image/frame source",
            )
        if not COMFY_LTX_CAPABILITIES["continuation"]:
            return "LTX_CONTINUATION_NOT_IMPLEMENTED", "continuation execution strategy is not implemented for comfy_remote"

    return None, None


def _detect_continuation_asset_type(asset_url: str | None, asset_type_hint: str | None = None) -> str:
    hinted = str(asset_type_hint or "").strip().lower()
    if hinted in {"image", "frame", "video"}:
        return hinted
    source = str(asset_url or "").strip().lower()
    if source.endswith((".mp4", ".webm", ".mov", ".m4v", ".avi", ".mkv")):
        return "video"
    if source.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".heic", ".heif")):
        return "image"
    return "unknown"


def load_workflow_json(path: str) -> dict:
    raw_path = str(path or "").strip()
    if not raw_path:
        raise ValueError("missing_workflow_path")

    workflow_path = Path(raw_path)
    if not workflow_path.is_absolute():
        workflow_path = Path(__file__).resolve().parents[2] / raw_path
    workflow_path = workflow_path.resolve()

    if not workflow_path.exists() or not workflow_path.is_file():
        raise ValueError(f"workflow_not_found:{workflow_path}")

    try:
        data = json.loads(workflow_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"workflow_parse_failed:{workflow_path}:{str(exc)[:200]}") from exc

    if not isinstance(data, dict):
        raise ValueError("workflow_invalid_json_root")
    return data


def _build_workflow_fingerprint(*, workflow: dict, workflow_key: str, workflow_path: str) -> dict:
    raw_path = str(workflow_path or "").strip()
    workflow_file_path = Path(raw_path)
    if not workflow_file_path.is_absolute():
        workflow_file_path = Path(__file__).resolve().parents[2] / raw_path
    absolute_path = workflow_file_path.resolve()
    file_exists = absolute_path.exists() and absolute_path.is_file()
    file_size_bytes = 0
    file_mtime = 0.0
    file_md5 = ""
    if file_exists:
        stat = absolute_path.stat()
        file_size_bytes = int(stat.st_size)
        file_mtime = float(stat.st_mtime)
        file_md5 = hashlib.md5(absolute_path.read_bytes()).hexdigest()

    discovered_audio_source_classes = sorted(
        {
            str((node or {}).get("class_type") or "").strip().lower()
            for node in (workflow or {}).values()
            if isinstance(node, dict)
            and str((node or {}).get("class_type") or "").strip().lower() in COMFY_AUDIO_SOURCE_NODE_CLASS_NAMES
        }
    )
    all_class_types = {
        str((node or {}).get("class_type") or "").strip().lower()
        for node in (workflow or {}).values()
        if isinstance(node, dict)
    }
    return {
        "workflowKey": str(workflow_key or "").strip(),
        "workflowPath": raw_path,
        "absoluteWorkflowPath": str(absolute_path),
        "fileExists": bool(file_exists),
        "fileSizeBytes": file_size_bytes,
        "fileMtime": file_mtime,
        "md5": file_md5,
        "topLevelNodeCount": len(workflow) if isinstance(workflow, dict) else 0,
        "hasLoadAudio": "loadaudio" in all_class_types,
        "hasLoadAudioFromUrl": bool({"loadaudiofromurl"} & all_class_types),
        "hasLoadAudioFromPath": "loadaudiofrompath" in all_class_types,
        "hasTrimAudioDuration": "trimaudioduration" in all_class_types,
        "hasLTXVAudioVAEEncode": "ltxvaudiovaeencode" in all_class_types,
        "hasCreateVideo": "createvideo" in all_class_types,
        "hasSaveVideo": "savevideo" in all_class_types,
        "discoveredAudioSourceClassTypes": discovered_audio_source_classes,
    }


def _response_body_snippet(resp: Response) -> str:
    try:
        return (resp.text or "")[:300]
    except Exception:
        return ""


def _parse_json_response(resp: Response, *, stage: str) -> tuple[dict | None, str | None]:
    try:
        payload = resp.json()
    except Exception as exc:
        body_snippet = _response_body_snippet(resp)
        logger.warning(
            "[COMFY REMOTE] %s invalid json status=%s body=%r error=%s",
            stage,
            resp.status_code,
            body_snippet,
            str(exc)[:200],
        )
        return None, f"{stage}_invalid_json:{str(exc)[:200]}:body={body_snippet}"

    if not isinstance(payload, dict):
        logger.warning("[COMFY REMOTE] %s response_json_not_dict type=%s", stage, type(payload).__name__)
        return None, f"{stage}_invalid_json_root:{type(payload).__name__}"

    return payload, None


def _preview_value(value, *, limit: int = 280):
    try:
        raw = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list, tuple)) else str(value)
    except Exception:
        raw = repr(value)
    if len(raw) <= limit:
        return raw
    return f"{raw[:limit]}…"


def _extract_comfy_failed_trace(history_entry: dict) -> dict:
    if not isinstance(history_entry, dict):
        return {}
    status_payload = history_entry.get("status") if isinstance(history_entry.get("status"), dict) else {}
    messages = status_payload.get("messages") if isinstance(status_payload.get("messages"), list) else []
    execution_error_payload = {}
    for item in messages:
        if isinstance(item, list) and len(item) >= 2 and str(item[0]).strip().lower() == "execution_error" and isinstance(item[1], dict):
            execution_error_payload = item[1]
            break
    status_str = str(status_payload.get("status_str") or status_payload.get("status") or "").strip().lower()
    completed = bool(status_payload.get("completed"))
    has_execution_error = bool(execution_error_payload)
    errors = history_entry.get("errors") if isinstance(history_entry.get("errors"), list) else []
    failed = status_str in {"error", "failed"} or has_execution_error or bool(errors)
    if not failed:
        return {}
    return {
        "comfy_status": status_str or "unknown",
        "failed_node_id": str(execution_error_payload.get("node_id") or execution_error_payload.get("node") or ""),
        "failed_node_type": str(execution_error_payload.get("node_type") or execution_error_payload.get("class_type") or ""),
        "exception_type": str(execution_error_payload.get("exception_type") or execution_error_payload.get("type") or ""),
        "error_message": str(
            execution_error_payload.get("exception_message")
            or execution_error_payload.get("error")
            or execution_error_payload.get("message")
            or history_entry.get("error")
            or ""
        ),
        "traceback_preview": _preview_value(execution_error_payload.get("traceback") or execution_error_payload.get("tb") or ""),
        "execution_error_payload": execution_error_payload,
        "status_payload_preview": _preview_value(status_payload),
        "outputs_payload_preview": _preview_value(history_entry.get("outputs")),
    }


def _collect_progress_bar_io_failure_markers(failed_trace: dict) -> tuple[bool, list[str]]:
    if not isinstance(failed_trace, dict):
        return False, []
    execution_error_payload = (
        failed_trace.get("execution_error_payload")
        if isinstance(failed_trace.get("execution_error_payload"), dict)
        else {}
    )
    merged_parts = [
        failed_trace.get("traceback_preview"),
        failed_trace.get("error_message"),
        failed_trace.get("exception_type"),
        execution_error_payload.get("traceback"),
        execution_error_payload.get("tb"),
        execution_error_payload.get("exception_message"),
        execution_error_payload.get("error"),
        execution_error_payload.get("message"),
    ]
    merged_text = " ".join(_preview_value(part, limit=2000) for part in merged_parts if part is not None).lower()
    marker_candidates = (
        "tqdm",
        "trange",
        "tqdm/std.py",
        "tqdm/asyncio.py",
        "model_trange",
        "print_status",
        "fp_write",
        "prestartup_script.py",
        "comfyui_manager",
        "logger.py",
    )
    matched_markers = [marker for marker in marker_candidates if marker in merged_text]
    has_oserror_invalid_arg = "oserror" in merged_text and "invalid argument" in merged_text
    return bool(has_oserror_invalid_arg and matched_markers), matched_markers


def _is_progress_bar_io_failure(failed_trace: dict) -> bool:
    is_failure, _ = _collect_progress_bar_io_failure_markers(failed_trace)
    return is_failure


def upload_image_to_comfy(image_bytes: bytes, filename: str) -> tuple[str | None, str | None]:
    url = f"{str(settings.COMFY_BASE_URL).rstrip('/')}/upload/image"
    safe_name = str(filename or "source.jpg").strip() or "source.jpg"
    size_bytes = len(image_bytes or b"")
    connect_timeout = max(20, int(settings.COMFY_UPLOAD_CONNECT_TIMEOUT_SEC or 20))
    read_timeout = max(180, int(settings.COMFY_UPLOAD_READ_TIMEOUT_SEC or 180))
    max_attempts = max(4, int(settings.COMFY_UPLOAD_MAX_ATTEMPTS or 4))

    logger.info(
        "[COMFY REMOTE] upload start url=%s filename=%s size_bytes=%s connect_timeout=%s read_timeout=%s max_attempts=%s",
        url,
        safe_name,
        size_bytes,
        connect_timeout,
        read_timeout,
        max_attempts,
    )

    files = {
        "image": (safe_name, image_bytes, "application/octet-stream"),
    }
    data = {"type": "input", "overwrite": "true"}

    last_error = "upload_unknown_error"
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(url, files=files, data=data, timeout=(connect_timeout, read_timeout))
            body_snippet = _response_body_snippet(resp)
            logger.info(
                "[COMFY REMOTE] upload response attempt=%s status=%s body=%r",
                attempt,
                resp.status_code,
                body_snippet,
            )
            if resp.status_code >= 400:
                return None, f"upload_non_200:status={resp.status_code}:body={body_snippet}"
            payload, parse_err = _parse_json_response(resp, stage="upload_response")
            if parse_err or not payload:
                return None, parse_err or "upload_response_invalid_json"

            name = str(payload.get("name") or payload.get("filename") or "").strip()
            if name:
                return name, None

            return None, f"upload_name_missing:{str(payload)[:300]}"
        except ConnectTimeout as exc:
            last_error = f"upload_connect_timeout:{str(exc)[:300]}"
            logger.warning("[COMFY REMOTE] upload connect timeout attempt=%s url=%s error=%s", attempt, url, str(exc)[:200])
        except ReadTimeout as exc:
            last_error = f"upload_read_timeout:{str(exc)[:300]}"
            logger.warning(
                "[COMFY REMOTE] upload read timeout attempt=%s url=%s size_bytes=%s error=%s",
                attempt,
                url,
                size_bytes,
                str(exc)[:200],
            )
        except RequestException as exc:
            last_error = f"upload_request_error:{str(exc)[:300]}"
            logger.warning("[COMFY REMOTE] upload request error attempt=%s url=%s error=%s", attempt, url, str(exc)[:200])
            return None, last_error

        if attempt < max_attempts:
            backoff_sec = min(8.0, 2.0 * attempt)
            logger.info("[COMFY REMOTE] upload retrying attempt=%s next_attempt=%s sleep_sec=%.1f", attempt, attempt + 1, backoff_sec)
            time.sleep(backoff_sec)

    return None, last_error


def upload_audio_to_comfy(
    audio_bytes: bytes,
    filename: str,
    *,
    workflow_key: str = "",
    workflow_file: str = "",
    transport_mode: str = "upload",
) -> tuple[str | None, str | None]:
    global _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED
    base_url = str(settings.COMFY_BASE_URL).rstrip("/")
    safe_name = str(filename or "source.mp3").strip() or "source.mp3"
    size_bytes = len(audio_bytes or b"")
    connect_timeout = max(20, int(settings.COMFY_UPLOAD_CONNECT_TIMEOUT_SEC or 20))
    read_timeout = max(180, int(settings.COMFY_UPLOAD_READ_TIMEOUT_SEC or 180))
    max_attempts = max(4, int(settings.COMFY_UPLOAD_MAX_ATTEMPTS or 4))
    workflow_key_safe = str(workflow_key or "").strip()
    workflow_file_safe = str(workflow_file or "").strip()
    transport_mode_safe = str(transport_mode or "").strip()
    endpoint_variants = []
    if _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED is not False:
        endpoint_variants.append({"path": "/upload/audio", "file_field": "audio"})
    endpoint_variants.append({"path": "/upload/image", "file_field": "image"})
    last_error = "upload_unknown_error"
    unsupported_route_attempts: list[str] = []
    for endpoint_variant in endpoint_variants:
        upload_url = f"{base_url}{endpoint_variant['path']}"
        file_field_name = str(endpoint_variant["file_field"])
        form_field_names = [file_field_name, "type", "overwrite"]
        logger.info(
            "[COMFY REMOTE] audio upload start url=%s filename=%s size_bytes=%s connect_timeout=%s read_timeout=%s max_attempts=%s",
            upload_url,
            safe_name,
            size_bytes,
            connect_timeout,
            read_timeout,
            max_attempts,
        )
        logger.info(
            "[COMFY AUDIO UPLOAD REQUEST] %s",
            {
                "endpoint": upload_url,
                "method": "POST",
                "fieldNames": form_field_names,
                "filename": safe_name,
                "workflowKey": workflow_key_safe,
                "workflowFile": workflow_file_safe,
                "transportMode": transport_mode_safe,
                "status": None,
                "success": False,
                "bodyPreview": "",
            },
        )
        files = {
            file_field_name: (safe_name, audio_bytes, "application/octet-stream"),
        }
        data = {"type": "input", "overwrite": "true"}

        for attempt in range(1, max_attempts + 1):
            try:
                resp = requests.post(upload_url, files=files, data=data, timeout=(connect_timeout, read_timeout))
                body_snippet = _response_body_snippet(resp)
                logger.info(
                    "[COMFY REMOTE] audio upload response endpoint=%s attempt=%s status=%s body=%r",
                    endpoint_variant["path"],
                    attempt,
                    resp.status_code,
                    body_snippet,
                )
                logger.info(
                    "[COMFY AUDIO UPLOAD REQUEST] %s",
                    {
                        "endpoint": upload_url,
                        "method": "POST",
                        "fieldNames": form_field_names,
                        "filename": safe_name,
                        "workflowKey": workflow_key_safe,
                        "workflowFile": workflow_file_safe,
                        "transportMode": transport_mode_safe,
                        "status": int(resp.status_code),
                        "success": bool(resp.status_code < 400),
                        "bodyPreview": body_snippet,
                    },
                )
                if resp.status_code >= 400:
                    if resp.status_code in {404, 405}:
                        unsupported_route_attempts.append(
                            f"path={endpoint_variant['path']}:status={resp.status_code}:body={body_snippet}"
                        )
                        if endpoint_variant["path"] == "/upload/audio":
                            _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED = False
                        break
                    return None, f"upload_non_200:status={resp.status_code}:body={body_snippet}"
                payload, parse_err = _parse_json_response(resp, stage="upload_response")
                if parse_err or not payload:
                    return None, parse_err or "upload_response_invalid_json"

                name = str(payload.get("name") or payload.get("filename") or "").strip()
                if name:
                    if endpoint_variant["path"] == "/upload/audio":
                        _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED = True
                    return name, None
                return None, f"upload_name_missing:{str(payload)[:300]}"
            except ConnectTimeout as exc:
                last_error = f"upload_connect_timeout:{str(exc)[:300]}"
            except ReadTimeout as exc:
                last_error = f"upload_read_timeout:{str(exc)[:300]}"
            except RequestException as exc:
                return None, f"upload_request_error:{str(exc)[:300]}"

            if attempt < max_attempts:
                time.sleep(min(8.0, 2.0 * attempt))

    if unsupported_route_attempts:
        return (
            None,
            "capability_error:COMFY_AUDIO_UPLOAD_ENDPOINT_UNSUPPORTED:"
            + ";".join(unsupported_route_attempts)[:500],
        )
    return None, last_error


def submit_comfy_prompt(workflow: dict) -> tuple[str | None, str | None]:
    url = f"{str(settings.COMFY_BASE_URL).rstrip('/')}/prompt"
    connect_timeout = max(20, int(settings.COMFY_PROMPT_CONNECT_TIMEOUT_SEC or 20))
    read_timeout = max(120, int(settings.COMFY_PROMPT_READ_TIMEOUT_SEC or 120))
    disable_pbar = bool(getattr(settings, "COMFY_DISABLE_PBAR_FOR_REMOTE", True))
    disable_pbar_top_level = bool(getattr(settings, "COMFY_DISABLE_PBAR_COMPAT_TOP_LEVEL", True))
    request_payload: dict = {"prompt": workflow}
    if disable_pbar:
        request_payload["extra_data"] = {"disable_pbar": True}
        if disable_pbar_top_level:
            # Compatibility fallback for desktop/fork wrappers that inspect top-level request fields
            # instead of reading extra_data forwarded to queue execution context.
            request_payload["disable_pbar"] = True
    logger.info(
        "[COMFY REMOTE] request prompt url=%s connect_timeout=%s read_timeout=%s disable_pbar=%s disable_pbar_top_level=%s payload_keys=%s",
        url,
        connect_timeout,
        read_timeout,
        disable_pbar,
        disable_pbar_top_level,
        sorted(list(request_payload.keys())),
    )
    try:
        resp = requests.post(url, json=request_payload, timeout=(connect_timeout, read_timeout))
        body_snippet = _response_body_snippet(resp)
        logger.info("[COMFY REMOTE] prompt response status=%s body=%r", resp.status_code, body_snippet)
        if resp.status_code >= 400:
            return None, f"prompt_non_200:status={resp.status_code}:body={body_snippet}"
        payload, parse_err = _parse_json_response(resp, stage="prompt_response")
        if parse_err or not payload:
            return None, parse_err or "prompt_response_invalid_json"
    except ConnectTimeout as exc:
        return None, f"prompt_connect_timeout:{str(exc)[:300]}"
    except ReadTimeout as exc:
        return None, f"prompt_read_timeout:{str(exc)[:300]}"
    except RequestException as exc:
        return None, f"prompt_request_error:{str(exc)[:300]}"
    except Exception as exc:
        logger.exception("[COMFY REMOTE] prompt unexpected error url=%s", url)
        return None, f"prompt_unexpected_error:{str(exc)[:300]}"

    prompt_id = str(payload.get("prompt_id") or "").strip()
    if not prompt_id:
        return None, f"prompt_id_missing:{str(payload)[:300]}"
    return prompt_id, None


def wait_for_comfy_result(
    prompt_id: str,
    timeout_sec: int,
    poll_interval_sec: int,
    *,
    workflow_key: str = "",
    workflow_file: str = "",
) -> tuple[dict | None, str | None]:
    safe_prompt_id = str(prompt_id or "").strip()
    if not safe_prompt_id:
        return None, "prompt_id_empty"

    deadline = time.time() + max(1800, int(timeout_sec or 0))
    sleep_sec = max(2, int(poll_interval_sec or 2))
    connect_timeout = max(20, int(settings.COMFY_PROMPT_CONNECT_TIMEOUT_SEC or 20))
    read_timeout = max(120, int(settings.COMFY_PROMPT_READ_TIMEOUT_SEC or 120))
    last_valid_payload: dict | None = None
    saw_valid_history_payload = False
    last_response_status: int | None = None
    last_response_body_snippet = ""
    transient_invalid_json_streak = 0
    transient_transport_streak = 0

    def _next_sleep(base_sec: int, streak: int) -> float:
        return float(min(12, max(1, int(base_sec)) + min(6, streak)))

    while time.time() < deadline:
        url = f"{str(settings.COMFY_BASE_URL).rstrip('/')}/history/{safe_prompt_id}"
        logger.info(
            "[COMFY REMOTE] request history url=%s connect_timeout=%s read_timeout=%s",
            url,
            connect_timeout,
            read_timeout,
        )
        try:
            resp = requests.get(url, timeout=(connect_timeout, read_timeout))
            body_snippet = _response_body_snippet(resp)
            last_response_status = resp.status_code
            last_response_body_snippet = body_snippet
            logger.info("[COMFY REMOTE] history response status=%s body=%r", resp.status_code, body_snippet)
            if resp.status_code >= 400:
                transient_transport_streak += 1
                logger.warning(
                    "[COMFY REMOTE] history temporary non-200 prompt_id=%s status=%s body=%r",
                    safe_prompt_id,
                    resp.status_code,
                    body_snippet,
                )
                time.sleep(_next_sleep(sleep_sec, transient_transport_streak))
                continue
            payload, parse_err = _parse_json_response(resp, stage="history_response")
            if parse_err or not payload:
                transient_invalid_json_streak += 1
                body_preview = (body_snippet or "")[:220]
                logger.warning(
                    "[COMFY REMOTE] history temporary invalid response prompt_id=%s err=%s streak=%s body_preview=%r",
                    safe_prompt_id,
                    parse_err or "history_response_invalid_json",
                    transient_invalid_json_streak,
                    body_preview,
                )
                time.sleep(_next_sleep(sleep_sec, transient_invalid_json_streak))
                continue
            transient_invalid_json_streak = 0
            transient_transport_streak = 0
            saw_valid_history_payload = True
            last_valid_payload = payload
            logger.info(
                "[COMFY REMOTE] history payload prompt_id=%s top_level_keys=%s contains_prompt_id=%s",
                safe_prompt_id,
                list(payload.keys())[:20],
                safe_prompt_id in payload,
            )
            if isinstance(payload.get(safe_prompt_id), dict):
                entry = payload.get(safe_prompt_id) or {}
                logger.info(
                    "[COMFY REMOTE] history prompt entry keys prompt_id=%s keys=%s",
                    safe_prompt_id,
                    list(entry.keys())[:50],
                )
                failed_trace = _extract_comfy_failed_trace(entry)
                if failed_trace:
                    progress_bar_io_failure, progress_bar_io_markers = _collect_progress_bar_io_failure_markers(failed_trace)
                    logger.error(
                        "[COMFY FAILED TRACE] %s",
                        {
                            "prompt_id": safe_prompt_id,
                            "workflow_key": str(workflow_key or "").strip(),
                            "workflow_file": str(workflow_file or "").strip(),
                            **failed_trace,
                            "disable_pbar_requested": bool(getattr(settings, "COMFY_DISABLE_PBAR_FOR_REMOTE", True)),
                            "disable_pbar_top_level_requested": bool(getattr(settings, "COMFY_DISABLE_PBAR_COMPAT_TOP_LEVEL", True)),
                            "progress_bar_io_failure": progress_bar_io_failure,
                            "progress_bar_io_markers": progress_bar_io_markers,
                        },
                    )
                    failed_node_id = str(failed_trace.get("failed_node_id") or "").strip()
                    failure_code = "comfy_node_execution_failed" if failed_node_id else "comfy_execution_failed"
                    if progress_bar_io_failure:
                        failure_code = "comfy_progress_bar_io_failed"
                    logger.error(
                        "[COMFY EXECUTION FAILURE CLASSIFICATION] %s",
                        {
                            "prompt_id": safe_prompt_id,
                            "workflow_key": str(workflow_key or "").strip(),
                            "workflow_file": str(workflow_file or "").strip(),
                            "failureCode": failure_code,
                            "failedNodeId": failed_node_id,
                            "failedNodeType": str(failed_trace.get("failed_node_type") or "").strip(),
                            "progressBarFailure": progress_bar_io_failure,
                            "progressBarMarkers": progress_bar_io_markers,
                            "disablePbarRequested": bool(getattr(settings, "COMFY_DISABLE_PBAR_FOR_REMOTE", True)),
                            "disablePbarTopLevelRequested": bool(
                                getattr(settings, "COMFY_DISABLE_PBAR_COMPAT_TOP_LEVEL", True)
                            ),
                        },
                    )
                    return payload, f"{failure_code}:{failed_trace.get('error_message') or failed_trace.get('exception_type') or failed_trace.get('comfy_status')}"
                outputs = entry.get("outputs")
                if isinstance(outputs, dict):
                    logger.info(
                        "[COMFY REMOTE] history outputs keys prompt_id=%s keys=%s",
                        safe_prompt_id,
                        list(outputs.keys())[:50],
                    )
                    if outputs:
                        return payload, None
        except ConnectTimeout as exc:
            transient_transport_streak += 1
            logger.warning(
                "[COMFY REMOTE] history connect timeout prompt_id=%s error=%s",
                safe_prompt_id,
                str(exc)[:200],
            )
            time.sleep(_next_sleep(sleep_sec, transient_transport_streak))
            continue

        except ReadTimeout as exc:
            transient_transport_streak += 1
            logger.warning(
                "[COMFY REMOTE] history read timeout prompt_id=%s error=%s",
                safe_prompt_id,
                str(exc)[:200],
            )
            time.sleep(_next_sleep(sleep_sec, transient_transport_streak))
            continue

        except RequestException as exc:
            transient_transport_streak += 1
            logger.warning(
                "[COMFY REMOTE] history request error prompt_id=%s error=%s",
                safe_prompt_id,
                str(exc)[:200],
            )
            time.sleep(_next_sleep(sleep_sec, transient_transport_streak))
            continue
        except Exception as exc:
            logger.exception("[COMFY REMOTE] history unexpected error url=%s prompt_id=%s", url, safe_prompt_id)
            return None, f"history_unexpected_error:{str(exc)[:300]}"

        time.sleep(sleep_sec)

    if isinstance(last_valid_payload, dict) and isinstance(last_valid_payload.get(safe_prompt_id), dict):
        logger.info(
            "[COMFY REMOTE] history final payload used after deadline prompt_id=%s timeout_sec=%s",
            safe_prompt_id,
            timeout_sec,
        )
        return last_valid_payload, None

    logger.warning(
        "[COMFY REMOTE] history wait timeout prompt_id=%s timeout_sec=%s saw_valid_history_payload=%s last_status=%s last_body=%r",
        safe_prompt_id,
        timeout_sec,
        saw_valid_history_payload,
        last_response_status,
        last_response_body_snippet,
    )
    return None, "timeout"


def extract_video_result(history_payload: dict) -> tuple[str | None, str | None]:
    if not isinstance(history_payload, dict):
        return None, "history_not_dict"

    logger.info("[COMFY REMOTE] history top-level keys=%s", list(history_payload.keys()))

    def _extract_file_ref(candidate) -> tuple[str | None, dict | None]:
        if isinstance(candidate, str):
            value = candidate.strip()
            return (value or None), None
        if not isinstance(candidate, dict):
            return None, None

        filename = str(candidate.get("filename") or "").strip()
        subfolder = str(candidate.get("subfolder") or "").strip()
        file_type = str(candidate.get("type") or "").strip()
        if filename and subfolder:
            return f"{subfolder}/{filename}", {"filename": filename, "subfolder": subfolder, "type": file_type}
        if filename:
            return filename, {"filename": filename, "subfolder": subfolder, "type": file_type}

        for key in ("name", "path", "video", "url", "video_url"):
            value = candidate.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip(), {"filename": "", "subfolder": subfolder, "type": file_type, "source_key": key}
        return None, None

    def _is_mp4(candidate, file_meta: dict | None) -> bool:
        filename = str((file_meta or {}).get("filename") or "").strip().lower()
        if filename.endswith(".mp4"):
            return True
        if isinstance(candidate, str):
            return candidate.strip().lower().endswith(".mp4")
        return False

    for history_key, entry in history_payload.items():
        if not isinstance(entry, dict):
            continue
        outputs = entry.get("outputs")
        if not isinstance(outputs, dict):
            continue

        logger.info("[COMFY REMOTE] outputs keys history=%s keys=%s", history_key, list(outputs.keys()))
        if "75" in outputs:
            logger.info("[COMFY REMOTE] outputs[75]=%s", outputs.get("75"))

        inspected_node_ids: list[str] = []
        fallback_file_ref: str | None = None
        for node_id, node_output in outputs.items():
            inspected_node_ids.append(str(node_id))
            if not isinstance(node_output, dict):
                logger.info("[COMFY REMOTE] output node skipped history=%s node_id=%s reason=not_dict", history_key, node_id)
                continue

            logger.info(
                "[COMFY REMOTE] inspect output node history=%s node_id=%s keys=%s has_videos=%s has_gifs=%s has_images=%s has_files=%s",
                history_key,
                node_id,
                list(node_output.keys())[:50],
                isinstance(node_output.get("videos"), list),
                isinstance(node_output.get("gifs"), list),
                isinstance(node_output.get("images"), list),
                isinstance(node_output.get("files"), list),
            )

            for list_key in ("videos", "gifs", "files"):
                items = node_output.get(list_key)
                if not isinstance(items, list):
                    continue
                logger.info(
                    "[COMFY REMOTE] inspect output list history=%s node_id=%s list_key=%s items=%s",
                    history_key,
                    node_id,
                    list_key,
                    len(items),
                )
                for item in items:
                    file_ref, file_meta = _extract_file_ref(item)
                    if not file_ref:
                        continue
                    if _is_mp4(item, file_meta):
                        logger.info(
                            "[COMFY REMOTE] selected mp4 history=%s node_id=%s list_key=%s filename=%s subfolder=%s type=%s",
                            history_key,
                            node_id,
                            list_key,
                            file_meta.get("filename") if file_meta else "",
                            file_meta.get("subfolder") if file_meta else "",
                            file_meta.get("type") if file_meta else "",
                        )
                        return file_ref, None
                    if fallback_file_ref is None:
                        fallback_file_ref = file_ref

            for list_key in ("videos", "files", "gifs", "images"):
                items = node_output.get(list_key)
                if not isinstance(items, list):
                    continue
                logger.info(
                    "[COMFY REMOTE] inspect output list history=%s node_id=%s list_key=%s items=%s",
                    history_key,
                    node_id,
                    list_key,
                    len(items),
                )
                for item in items:
                    file_ref, _ = _extract_file_ref(item)
                    if file_ref and fallback_file_ref is None:
                        fallback_file_ref = file_ref

            file_ref, file_meta = _extract_file_ref(node_output)
            if file_ref:
                if _is_mp4(node_output, file_meta):
                    logger.info(
                        "[COMFY REMOTE] selected direct mp4 history=%s node_id=%s filename=%s subfolder=%s type=%s",
                        history_key,
                        node_id,
                        file_meta.get("filename") if file_meta else "",
                        file_meta.get("subfolder") if file_meta else "",
                        file_meta.get("type") if file_meta else "",
                    )
                else:
                    logger.info("[COMFY REMOTE] matched direct output node_id=%s file_ref=%s", node_id, file_ref)
                    if fallback_file_ref is None:
                        fallback_file_ref = file_ref

        if fallback_file_ref:
            return fallback_file_ref, None

        logger.warning(
            "[COMFY REMOTE] no video output found history=%s inspected_node_ids=%s available_output_keys=%s",
            history_key,
            inspected_node_ids,
            list(outputs.keys()),
        )

    return None, "video_output_missing"


def parse_comfy_file_ref(filename_or_subpath: str) -> dict | None:
    raw = str(filename_or_subpath or "").strip()
    if not raw:
        return None
    if raw.startswith("http://") or raw.startswith("https://"):
        return {
            "raw_file_ref": raw,
            "filename": "",
            "subfolder": "",
            "type": "output",
            "format": Path(raw.split("?", 1)[0]).suffix.lstrip(".").lower(),
            "is_absolute_url": True,
        }

    clean = raw.lstrip("/")
    parts = [segment for segment in clean.split("/") if segment]
    if not parts:
        return None
    filename = str(parts[-1]).strip()
    subfolder_parts = parts[:-1]
    if subfolder_parts and subfolder_parts[0].lower() == "output":
        subfolder_parts = subfolder_parts[1:]
    subfolder = "/".join(subfolder_parts).strip("/")
    return {
        "raw_file_ref": raw,
        "filename": filename,
        "subfolder": subfolder,
        "type": "output",
        "format": Path(filename).suffix.lstrip(".").lower(),
        "is_absolute_url": False,
    }


def _build_comfy_view_url(file_meta: dict, *, base_url: str) -> str:
    safe_base = str(base_url or "").strip().rstrip("/")
    if not safe_base:
        return ""
    if file_meta.get("is_absolute_url"):
        return str(file_meta.get("raw_file_ref") or "").strip()
    filename = str(file_meta.get("filename") or "").strip()
    if not filename:
        return ""
    subfolder = str(file_meta.get("subfolder") or "").strip()
    file_type = str(file_meta.get("type") or "output").strip() or "output"
    if subfolder:
        return f"{safe_base}/view?filename={quote(filename)}&subfolder={quote(subfolder)}&type={quote(file_type)}"
    return f"{safe_base}/view?filename={quote(filename)}&type={quote(file_type)}"


def build_public_comfy_file_url(filename_or_subpath: str) -> tuple[str, dict | None, str]:
    file_meta = parse_comfy_file_ref(filename_or_subpath)
    if not file_meta:
        return "", None, "unknown"
    strategy = str(settings.COMFY_OUTPUT_HANDOFF_STRATEGY or "backend_proxy").strip().lower() or "backend_proxy"

    direct_url = _build_comfy_view_url(file_meta, base_url=str(settings.COMFY_BASE_URL))
    if not direct_url:
        return "", file_meta, strategy

    if strategy == "direct_comfy_url" or file_meta.get("is_absolute_url"):
        return direct_url, file_meta, "direct_comfy_url"

    public_base = str(settings.PUBLIC_BASE_URL or "").strip().rstrip("/")
    if not public_base:
        return "", file_meta, "backend_proxy_url"
    filename = quote(str(file_meta.get("filename") or "").strip())
    subfolder = quote(str(file_meta.get("subfolder") or "").strip())
    file_type = quote(str(file_meta.get("type") or "output").strip() or "output")
    return f"{public_base}/api/clip/video/comfy-output?filename={filename}&subfolder={subfolder}&type={file_type}", file_meta, "backend_proxy_url"


def validate_comfy_output_access(file_meta: dict) -> tuple[bool, str | None]:
    comfy_url = _build_comfy_view_url(file_meta, base_url=str(settings.COMFY_BASE_URL))
    if not comfy_url:
        return False, "COMFY_OUTPUT_URL_INVALID"
    connect_timeout = max(5, int(settings.COMFY_PROMPT_CONNECT_TIMEOUT_SEC or 10))
    read_timeout = max(20, int(settings.COMFY_PROMPT_READ_TIMEOUT_SEC or 60))
    try:
        response = requests.get(comfy_url, timeout=(connect_timeout, read_timeout), stream=True)
        status_code = int(response.status_code or 0)
        content_type = str(response.headers.get("content-type") or "").strip().lower()
        response.close()
        logger.info(
            "[COMFY RESULT URL VALIDATION] comfy_url=%s status=%s content_type=%s",
            comfy_url,
            status_code,
            content_type,
        )
        if status_code >= 400:
            return False, f"COMFY_OUTPUT_NOT_ACCESSIBLE:status={status_code}"
        if "video" not in content_type and "octet-stream" not in content_type:
            logger.warning("[COMFY RESULT URL VALIDATION] unexpected content-type=%s comfy_url=%s", content_type, comfy_url)
        return True, None
    except RequestException as exc:
        logger.warning("[COMFY RESULT URL VALIDATION] request failed comfy_url=%s error=%s", comfy_url, str(exc)[:300])
        return False, f"COMFY_OUTPUT_NOT_ACCESSIBLE:{str(exc)[:300]}"


def _set_node_input(workflow: dict, node_id: str, input_key: str, value) -> tuple[bool, str | None]:
    node = workflow.get(str(node_id))
    if not isinstance(node, dict):
        return False, f"missing_node:{node_id}"
    inputs = node.get("inputs")
    if not isinstance(inputs, dict):
        return False, f"missing_input_map:{node_id}"
    if input_key not in inputs:
        return False, f"missing_input:{node_id}.{input_key}"
    inputs[input_key] = value
    return True, None


# These node ids are intentionally pinned to image-video-silent-directprompt.json.
FIXED_IMAGE_VIDEO_NODES = {
    "image": ("269", "image"),
    "prompt": ("267:266", "value"),
    "width": ("267:257", "value"),
    "height": ("267:258", "value"),
    "length": ("267:225", "value"),
    "fps": ("267:260", "value"),
}

# These seed node ids are intentionally pinned to image-video-silent-directprompt.json.
FIXED_SEED_NODES = (("267:216", "noise_seed"), ("267:237", "noise_seed"))
FIXED_PROMPT_PATCH_NODE_IDS = [FIXED_IMAGE_VIDEO_NODES["prompt"][0]]
LIPSYNC_PRIMARY_NODE_IDS = {
    "image": "269",
    "audio": "276",
    "prompt": "340:319",
    "duration": "340:331",
    "fps": "340:323",
}


def _node_title_lower(node: dict) -> str:
    return str(((node.get("_meta") or {}).get("title") if isinstance(node.get("_meta"), dict) else "") or "").strip().lower()


def _discover_lip_sync_nodes(workflow: dict) -> dict:
    discovered = {
        "prompt_text_node_id": "",
        "prompt_encode_node_id": "",
        "width_node_id": "",
        "height_node_id": "",
        "length_node_id": "",
        "duration_node_id": "",
        "fps_node_id": "",
        "image_node_ids": [],
        "audio_node_ids": [],
        "trim_audio_node_ids": [],
        "audio_encode_node_ids": [],
        "save_video_node_ids": [],
        "create_video_node_ids": [],
        "save_video_filename_prefix": "",
    }
    if not isinstance(workflow, dict):
        return discovered

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "").strip().lower()
        title_l = _node_title_lower(node)
        inputs = node.get("inputs")
        input_map = inputs if isinstance(inputs, dict) else {}

        if class_type in COMFY_DYNAMIC_DISCOVERY_CLASS_NAMES["prompt_text"] and (
            "prompt" in title_l or not discovered["prompt_text_node_id"]
        ):
            discovered["prompt_text_node_id"] = str(node_id)
        if "width" in title_l and "value" in input_map and not discovered["width_node_id"]:
            discovered["width_node_id"] = str(node_id)
        if "height" in title_l and "value" in input_map and not discovered["height_node_id"]:
            discovered["height_node_id"] = str(node_id)
        if any(hint in title_l for hint in ("length", "frame", "duration")) and "value" in input_map and not discovered["length_node_id"]:
            discovered["length_node_id"] = str(node_id)
        if class_type == "primitivefloat" and "value" in input_map and (
            "duration" in title_l or str(node_id) == LIPSYNC_PRIMARY_NODE_IDS["duration"]
        ):
            discovered["duration_node_id"] = str(node_id)
        if "fps" in title_l and "value" in input_map and not discovered["fps_node_id"]:
            discovered["fps_node_id"] = str(node_id)

        if class_type in COMFY_DYNAMIC_DISCOVERY_CLASS_NAMES["image_input"] and "image" in input_map:
            discovered["image_node_ids"].append(str(node_id))
        if class_type in COMFY_DYNAMIC_DISCOVERY_CLASS_NAMES["audio_input"]:
            discovered["audio_node_ids"].append(str(node_id))
        if class_type in COMFY_DYNAMIC_DISCOVERY_CLASS_NAMES["trim_audio"]:
            discovered["trim_audio_node_ids"].append(str(node_id))
        if class_type in COMFY_DYNAMIC_DISCOVERY_CLASS_NAMES["audio_encode"]:
            discovered["audio_encode_node_ids"].append(str(node_id))
        if class_type in COMFY_DYNAMIC_DISCOVERY_CLASS_NAMES["save_video"]:
            discovered["save_video_node_ids"].append(str(node_id))
            if not discovered["save_video_filename_prefix"]:
                discovered["save_video_filename_prefix"] = str(input_map.get("filename_prefix") or "").strip()
        if class_type in COMFY_DYNAMIC_DISCOVERY_CLASS_NAMES["create_video"]:
            discovered["create_video_node_ids"].append(str(node_id))

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "").strip().lower()
        if class_type not in COMFY_DYNAMIC_DISCOVERY_CLASS_NAMES["prompt_encode"]:
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        text_link = inputs.get("text")
        title_l = _node_title_lower(node)
        if (
            isinstance(text_link, list)
            and text_link
            and str(text_link[0]).strip() == discovered["prompt_text_node_id"]
        ):
            discovered["prompt_encode_node_id"] = str(node_id)
            break
        if not discovered["prompt_encode_node_id"] and "prompt" in title_l:
            discovered["prompt_encode_node_id"] = str(node_id)

    for key in (
        "image_node_ids",
        "audio_node_ids",
        "trim_audio_node_ids",
        "audio_encode_node_ids",
        "save_video_node_ids",
        "create_video_node_ids",
    ):
        discovered[key] = list(dict.fromkeys(discovered[key]))
    if not discovered["duration_node_id"] and discovered["length_node_id"]:
        discovered["duration_node_id"] = discovered["length_node_id"]
    return discovered


def _resolve_workflow_fps(workflow: dict, *, preferred_fps_node_id: str = "", default_fps: int = 24) -> int:
    fps_node_id, fps_input_key = FIXED_IMAGE_VIDEO_NODES["fps"]
    if preferred_fps_node_id:
        fps_node_id = preferred_fps_node_id
        fps_input_key = "value"
    node = workflow.get(str(fps_node_id))
    if isinstance(node, dict):
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            try:
                fps_value = int(inputs.get(fps_input_key) or default_fps)
                if fps_value > 0:
                    return fps_value
            except Exception:
                pass
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        title_l = _node_title_lower(node)
        if "fps" in title_l and "value" in inputs:
            try:
                fps_value = int(inputs.get("value") or default_fps)
                if fps_value > 0:
                    return fps_value
            except Exception:
                pass
    return int(default_fps)


def _resolve_lipsync_patch_node_id(
    workflow: dict,
    *,
    expected_node_id: str,
    class_types: set[str],
    required_input_key: str,
) -> str:
    expected = str(expected_node_id or "").strip()
    expected_node = workflow.get(expected) if expected else None
    if isinstance(expected_node, dict):
        expected_inputs = expected_node.get("inputs")
        expected_class = str(expected_node.get("class_type") or "").strip().lower()
        if isinstance(expected_inputs, dict) and required_input_key in expected_inputs and expected_class in class_types:
            return expected

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "").strip().lower()
        if class_type not in class_types:
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and required_input_key in inputs:
            return str(node_id)
    return ""


def _patch_audio_frames(workflow: dict, frames: int) -> None:
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict) or "frames_number" not in inputs:
            continue
        frame_value = inputs.get("frames_number")
        if isinstance(frame_value, list):
            continue
        inputs["frames_number"] = int(frames)


def _patch_first_last_images(workflow: dict, *, start_image_name: str, end_image_name: str) -> tuple[bool, list[str], list[str]]:
    start_node_ids: list[str] = []
    end_node_ids: list[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "").strip().lower()
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if "image" not in inputs:
            continue
        label_blob = " ".join(
            [
                class_type,
                str(node.get("_meta", {}).get("title") or ""),
                str(node.get("title") or ""),
                str(node_id),
            ]
        ).lower()
        if "end" in label_blob or "last" in label_blob or "second" in label_blob:
            inputs["image"] = end_image_name
            end_node_ids.append(str(node_id))
            continue
        if "start" in label_blob or "first" in label_blob:
            inputs["image"] = start_image_name
            start_node_ids.append(str(node_id))
            continue

    if not start_node_ids:
        image_node_id, image_input_key = FIXED_IMAGE_VIDEO_NODES["image"]
        ok, _ = _set_node_input(workflow, image_node_id, image_input_key, start_image_name)
        if ok:
            start_node_ids.append(str(image_node_id))

    if not end_node_ids:
        image_to_video_nodes = [
            str(node_id)
            for node_id, node in workflow.items()
            if isinstance(node, dict) and str(node.get("class_type") or "").strip() == "LTXVImgToVideoInplace"
        ]
        if len(image_to_video_nodes) >= 2:
            second_img2video_node_id = image_to_video_nodes[1]
            second_img2video_inputs = (workflow.get(second_img2video_node_id) or {}).get("inputs")
            second_image_link = second_img2video_inputs.get("image") if isinstance(second_img2video_inputs, dict) else None
            if isinstance(second_image_link, list) and second_image_link:
                upstream_preprocess_node_id = str(second_image_link[0])
                upstream_preprocess_node = workflow.get(upstream_preprocess_node_id)
                upstream_preprocess_inputs = (
                    upstream_preprocess_node.get("inputs")
                    if isinstance(upstream_preprocess_node, dict)
                    else None
                )
                upstream_image_link = upstream_preprocess_inputs.get("image") if isinstance(upstream_preprocess_inputs, dict) else None
                upstream_resize_node_id = str(upstream_image_link[0]) if isinstance(upstream_image_link, list) and upstream_image_link else ""
                upstream_resize_node = workflow.get(upstream_resize_node_id) if upstream_resize_node_id else None
                upstream_resize_inputs = upstream_resize_node.get("inputs") if isinstance(upstream_resize_node, dict) else None
                upstream_mask_link = upstream_resize_inputs.get("images") if isinstance(upstream_resize_inputs, dict) else None
                upstream_mask_node_id = str(upstream_mask_link[0]) if isinstance(upstream_mask_link, list) and upstream_mask_link else ""
                upstream_mask_node = workflow.get(upstream_mask_node_id) if upstream_mask_node_id else None
                upstream_mask_inputs = upstream_mask_node.get("inputs") if isinstance(upstream_mask_node, dict) else None

                if (
                    isinstance(upstream_preprocess_node, dict)
                    and isinstance(upstream_resize_node, dict)
                    and isinstance(upstream_mask_node, dict)
                    and isinstance(upstream_preprocess_inputs, dict)
                    and isinstance(upstream_resize_inputs, dict)
                    and isinstance(upstream_mask_inputs, dict)
                ):
                    load_image_node_id = "270"
                    resize_mask_node_id = "267:338"
                    resize_images_node_id = "267:339"
                    preprocess_node_id = "267:340"

                    if load_image_node_id not in workflow:
                        workflow[load_image_node_id] = {
                            "inputs": {"image": end_image_name},
                            "class_type": "LoadImage",
                            "_meta": {"title": "LoadImage End Frame"},
                        }
                    else:
                        load_inputs = workflow.get(load_image_node_id, {}).get("inputs")
                        if isinstance(load_inputs, dict):
                            load_inputs["image"] = end_image_name

                    if resize_mask_node_id not in workflow:
                        workflow[resize_mask_node_id] = copy.deepcopy(upstream_mask_node)
                    if resize_images_node_id not in workflow:
                        workflow[resize_images_node_id] = copy.deepcopy(upstream_resize_node)
                    if preprocess_node_id not in workflow:
                        workflow[preprocess_node_id] = copy.deepcopy(upstream_preprocess_node)

                    resize_mask_inputs = workflow.get(resize_mask_node_id, {}).get("inputs")
                    resize_images_inputs = workflow.get(resize_images_node_id, {}).get("inputs")
                    preprocess_inputs = workflow.get(preprocess_node_id, {}).get("inputs")
                    second_node_inputs = workflow.get(second_img2video_node_id, {}).get("inputs")
                    if (
                        isinstance(resize_mask_inputs, dict)
                        and isinstance(resize_images_inputs, dict)
                        and isinstance(preprocess_inputs, dict)
                        and isinstance(second_node_inputs, dict)
                    ):
                        resize_mask_inputs["input"] = [load_image_node_id, 0]
                        resize_images_inputs["images"] = [resize_mask_node_id, 0]
                        preprocess_inputs["image"] = [resize_images_node_id, 0]
                        second_node_inputs["image"] = [preprocess_node_id, 0]
                        end_node_ids.extend(
                            [load_image_node_id, resize_mask_node_id, resize_images_node_id, preprocess_node_id, second_img2video_node_id]
                        )

    second_frame_patch_applied = bool(start_node_ids and end_node_ids)
    return second_frame_patch_applied, start_node_ids, end_node_ids


def _validate_audio_workflow_file(*, workflow_key: str, workflow_source: str) -> tuple[bool, str | None]:
    expected_name = COMFY_AUDIO_WORKFLOW_FILES.get(str(workflow_key or "").strip().lower())
    actual_name = Path(str(workflow_source or "")).name
    if not expected_name:
        return False, f"unsupported_audio_workflow_key:{workflow_key}"
    if actual_name != expected_name:
        return False, f"workflow_file_mismatch:expected={expected_name};actual={actual_name}"
    return True, None


def _patch_audio_input_nodes(workflow: dict, *, audio_value: str, audio_targets: list[dict] | None = None) -> tuple[list[str], str | None]:
    safe_audio_value = str(audio_value or "").strip()
    if not safe_audio_value:
        return [], "audio_value_empty"
    patched_node_ids: list[str] = []
    if isinstance(audio_targets, list) and audio_targets:
        for target in audio_targets:
            target_node_id = str((target or {}).get("node_id") or "").strip()
            input_key = str((target or {}).get("input_key") or "").strip()
            if not target_node_id or not input_key:
                continue
            node = workflow.get(target_node_id)
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            allow_create = bool((target or {}).get("allow_create"))
            if input_key in inputs or allow_create:
                inputs[input_key] = safe_audio_value
                patched_node_ids.append(target_node_id)
        if patched_node_ids:
            return list(dict.fromkeys(patched_node_ids)), None
        return [], "audio_target_patch_failed"

    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "").strip().lower()
        title = str(((node.get("_meta") or {}).get("title") if isinstance(node.get("_meta"), dict) else "") or "").strip().lower()
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        is_audio_node = class_type in COMFY_AUDIO_INPUT_NODE_CLASS_NAMES or "audio" in title or "lipsync" in title
        if not is_audio_node:
            continue
        applied = False
        for input_key in COMFY_AUDIO_INPUT_KEYS:
            if input_key in inputs:
                inputs[input_key] = safe_audio_value
                patched_node_ids.append(str(node_id))
                applied = True
                break
        if not applied and class_type in COMFY_AUDIO_INPUT_NODE_CLASS_NAMES:
            for fallback_key in COMFY_AUDIO_INPUT_WIDGET_FALLBACK_KEYS:
                if fallback_key in inputs and isinstance(inputs.get(fallback_key), str):
                    inputs[fallback_key] = safe_audio_value
                    patched_node_ids.append(str(node_id))
                    applied = True
                    break
        if not applied:
            return [], f"audio_loader_input_missing:{node_id}:{class_type}"

    if not patched_node_ids:
        return [], "audio_loader_nodes_not_found"
    return patched_node_ids, None


def _class_audio_input_key_priority(class_type: str) -> tuple[str, ...]:
    normalized = str(class_type or "").strip().lower()
    if normalized in {"loadaudio", "vhs_loadaudio", "vhs_loadaudioupload"}:
        return ("audio", "audio_file", "filename")
    if normalized in {"loadaudiofromurl", "mediautilities_audiourlloader"}:
        return ("url", "audio_path", "path", "audio")
    if normalized == "loadaudiofrompath":
        return ("path", "audio_path", "audio", "filename")
    return COMFY_AUDIO_INPUT_KEYS


def _collect_audio_input_targets(
    workflow: dict,
    *,
    workflow_key: str = "",
    workflow_file: str = "",
) -> dict:
    source_targets: list[dict] = []
    downstream_nodes: list[dict] = []
    normalized_workflow_key = str(workflow_key or "").strip().lower()
    workflow_filename = Path(str(workflow_file or "")).name.strip().lower()
    expected_lipsync_file = COMFY_AUDIO_WORKFLOW_FILES.get("lip_sync", "").strip().lower()
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "").strip().lower()
        title = str(((node.get("_meta") or {}).get("title") if isinstance(node.get("_meta"), dict) else "") or "").strip().lower()
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        input_keys = list(inputs.keys())
        normalized_input_keys = {str(key).strip().lower(): str(key) for key in input_keys}
        if class_type in COMFY_AUDIO_DOWNSTREAM_NODE_CLASS_NAMES:
            downstream_nodes.append(
                {
                    "node_id": str(node_id),
                    "class_type": class_type,
                    "title": title,
                    "input_keys": input_keys,
                    "matched_by": "downstream_class",
                }
            )
        matched_by = "class_type" if class_type in COMFY_AUDIO_SOURCE_NODE_CLASS_NAMES else ""
        if not matched_by:
            continue
        found_source_target = False
        for input_key in _class_audio_input_key_priority(class_type):
            actual_input_key = normalized_input_keys.get(str(input_key).strip().lower())
            if actual_input_key and actual_input_key in inputs:
                source_targets.append(
                    {
                        "node_id": str(node_id),
                        "class_type": class_type,
                        "title": title,
                        "input_keys": input_keys,
                        "input_key": actual_input_key,
                        "matched_by": matched_by,
                    }
                )
                found_source_target = True
                break
        if not found_source_target:
            for fallback_key in COMFY_AUDIO_INPUT_WIDGET_FALLBACK_KEYS:
                if fallback_key in inputs and isinstance(inputs.get(fallback_key), str):
                    source_targets.append(
                        {
                            "node_id": str(node_id),
                            "class_type": class_type,
                            "title": title,
                            "input_keys": input_keys,
                            "input_key": fallback_key,
                            "matched_by": "fallback",
                        }
                    )
                    break

    if (
        not source_targets
        and normalized_workflow_key == "lip_sync"
        and workflow_filename == expected_lipsync_file
    ):
        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type") or "").strip().lower()
            inputs = node.get("inputs")
            if not isinstance(inputs, dict):
                continue
            if class_type in COMFY_LIPSYNC_WORKFLOW_AUDIO_FALLBACK_CLASS_NAMES:
                source_targets.append(
                    {
                        "node_id": str(node_id),
                        "class_type": class_type,
                        "title": str(((node.get("_meta") or {}).get("title") if isinstance(node.get("_meta"), dict) else "") or "").strip().lower(),
                        "input_keys": list(inputs.keys()),
                        "input_key": "audio",
                        "matched_by": "workflow_specific_class_fallback",
                        "allow_create": True,
                    }
                )
                break
    return {
        "source_targets": source_targets,
        "downstream_nodes": downstream_nodes,
    }


def _snapshot_node_inputs(workflow: dict, node_ids: list[str], *, allowed_keys: set[str] | None = None) -> list[dict]:
    snapshots: list[dict] = []
    for node_id in node_ids:
        safe_node_id = str(node_id or "").strip()
        if not safe_node_id:
            continue
        node = workflow.get(safe_node_id) if isinstance(workflow, dict) else None
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        filtered_inputs = {}
        for key, value in inputs.items():
            if allowed_keys and str(key) not in allowed_keys:
                continue
            filtered_inputs[str(key)] = _preview_value(value)
        snapshots.append(
            {
                "node_id": safe_node_id,
                "class_type": str(node.get("class_type") or "").strip(),
                "inputs": filtered_inputs,
            }
        )
    return snapshots


def _build_workflow_adjacency(workflow: dict) -> dict[str, list[dict]]:
    adjacency: dict[str, list[dict]] = {}
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        for input_key, input_value in inputs.items():
            if isinstance(input_value, list) and len(input_value) >= 1:
                source_node_id = str(input_value[0] or "").strip()
                if not source_node_id:
                    continue
                adjacency.setdefault(source_node_id, []).append(
                    {
                        "to_node_id": str(node_id),
                        "input_key": str(input_key),
                    }
                )
    return adjacency


def _is_mouth_control_node(class_type: str, title: str) -> bool:
    class_l = str(class_type or "").strip().lower()
    title_l = str(title or "").strip().lower()
    if any(hint in class_l for hint in COMFY_MOUTH_CONTROL_HINTS):
        return True
    if any(hint in title_l for hint in COMFY_NON_MOUTH_LIPSYNC_TITLES):
        return False
    return any(hint in title_l for hint in COMFY_MOUTH_CONTROL_HINTS)


def _inspect_audio_path_mode(workflow: dict, *, audio_patch_node_ids: list[str]) -> dict:
    patched_audio_node_class = ""
    patched_audio_node_title = ""
    patched_audio_node_downstream_summary = {"visitedNodeCount": 0, "pathPreview": [], "reachedClassTypes": []}
    audio_reaches_main_video_branch = False
    audio_reaches_mouth_control_branch = False
    workflow_lip_sync_capable = False
    explicit_mouth_control_branch_present = False
    workflow_uses_av_audio_path = False
    av_audio_driven_generation_present = False

    if not isinstance(workflow, dict):
        return {
            "patchedAudioNodeClass": patched_audio_node_class,
            "patchedAudioNodeTitle": patched_audio_node_title,
            "patchedAudioNodeDownstreamSummary": patched_audio_node_downstream_summary,
            "workflowLipSyncCapable": workflow_lip_sync_capable,
            "audioReachesMainVideoBranch": audio_reaches_main_video_branch,
            "audioReachesMouthControlBranch": audio_reaches_mouth_control_branch,
            "workflowUsesAVAudioPath": workflow_uses_av_audio_path,
            "avAudioDrivenGenerationPresent": av_audio_driven_generation_present,
            "explicitMouthControlBranchPresent": explicit_mouth_control_branch_present,
        }

    adjacency = _build_workflow_adjacency(workflow)
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        class_type_l = str(node.get("class_type") or "").strip().lower()
        title_l = str(((node.get("_meta") or {}).get("title") if isinstance(node.get("_meta"), dict) else "") or "").strip().lower()
        if _is_mouth_control_node(class_type_l, title_l):
            workflow_lip_sync_capable = True
            explicit_mouth_control_branch_present = True
        if class_type_l in COMFY_LTX_AV_AUDIO_PATH_CLASS_NAMES:
            workflow_uses_av_audio_path = True

    first_node_id = str((audio_patch_node_ids or [""])[0] or "").strip()
    first_node = workflow.get(first_node_id) if first_node_id else None
    if isinstance(first_node, dict):
        patched_audio_node_class = str(first_node.get("class_type") or "").strip()
        patched_audio_node_title = str(((first_node.get("_meta") or {}).get("title") if isinstance(first_node.get("_meta"), dict) else "") or "").strip()

    if not audio_patch_node_ids:
        return {
            "patchedAudioNodeClass": patched_audio_node_class,
            "patchedAudioNodeTitle": patched_audio_node_title,
            "patchedAudioNodeDownstreamSummary": patched_audio_node_downstream_summary,
            "workflowLipSyncCapable": workflow_lip_sync_capable,
            "audioReachesMainVideoBranch": audio_reaches_main_video_branch,
            "audioReachesMouthControlBranch": audio_reaches_mouth_control_branch,
            "workflowUsesAVAudioPath": workflow_uses_av_audio_path,
            "avAudioDrivenGenerationPresent": av_audio_driven_generation_present,
            "explicitMouthControlBranchPresent": explicit_mouth_control_branch_present,
        }

    queue = deque([str(node_id) for node_id in audio_patch_node_ids if str(node_id or "").strip()])
    visited: set[str] = set()
    reached_classes: set[str] = set()
    path_preview: list[str] = []
    while queue:
        current_node_id = queue.popleft()
        if current_node_id in visited:
            continue
        visited.add(current_node_id)
        for edge in adjacency.get(current_node_id, []):
            downstream_node_id = str(edge.get("to_node_id") or "").strip()
            downstream_node = workflow.get(downstream_node_id)
            if not isinstance(downstream_node, dict):
                continue
            downstream_class = str(downstream_node.get("class_type") or "").strip()
            downstream_title = str(((downstream_node.get("_meta") or {}).get("title") if isinstance(downstream_node.get("_meta"), dict) else "") or "").strip()
            downstream_class_l = downstream_class.lower()
            reached_classes.add(downstream_class)
            if downstream_class_l in COMFY_MAIN_VIDEO_BRANCH_CLASS_NAMES:
                audio_reaches_main_video_branch = True
            if downstream_class_l in COMFY_LTX_AV_AUDIO_PATH_CLASS_NAMES:
                av_audio_driven_generation_present = True
            if _is_mouth_control_node(downstream_class_l, downstream_title.lower()):
                audio_reaches_mouth_control_branch = True
            if len(path_preview) < 16:
                path_preview.append(
                    f"{current_node_id} -[{str(edge.get('input_key') or '').strip()}]-> {downstream_node_id}:{downstream_class or 'unknown'}"
                )
            if downstream_node_id not in visited:
                queue.append(downstream_node_id)

    patched_audio_node_downstream_summary = {
        "visitedNodeCount": len(visited),
        "pathPreview": path_preview,
        "reachedClassTypes": sorted([item for item in reached_classes if item]),
    }
    return {
        "patchedAudioNodeClass": patched_audio_node_class,
        "patchedAudioNodeTitle": patched_audio_node_title,
        "patchedAudioNodeDownstreamSummary": patched_audio_node_downstream_summary,
        "workflowLipSyncCapable": workflow_lip_sync_capable,
        "audioReachesMainVideoBranch": audio_reaches_main_video_branch,
        "audioReachesMouthControlBranch": audio_reaches_mouth_control_branch,
        "workflowUsesAVAudioPath": workflow_uses_av_audio_path,
        "avAudioDrivenGenerationPresent": av_audio_driven_generation_present,
        "explicitMouthControlBranchPresent": explicit_mouth_control_branch_present,
    }


def _normalize_audio_url_for_remote_transport(audio_url: str | None) -> tuple[str, dict]:
    original_url = str(audio_url or "").strip()
    public_base_url = str(settings.PUBLIC_BASE_URL or "").strip()
    details = {
        "originalAudioUrl": original_url,
        "normalizedAudioUrl": original_url,
        "wasNormalized": False,
        "normalizationReason": "not_required",
        "publicBaseUrl": public_base_url,
    }
    if not original_url:
        details["normalizationReason"] = "audio_url_missing"
        return original_url, details
    try:
        parsed_original = urlparse(original_url)
    except Exception:
        details["normalizationReason"] = "audio_url_parse_failed"
        return original_url, details
    source_host = str(parsed_original.hostname or "").strip().lower()
    if not source_host:
        details["normalizationReason"] = "audio_url_hostname_missing"
        return original_url, details

    comfy_base = str(settings.COMFY_BASE_URL or "").strip()
    comfy_host = ""
    if comfy_base:
        try:
            comfy_host = str(urlparse(comfy_base).hostname or "").strip().lower()
        except Exception:
            comfy_host = ""

    source_is_backend_local = source_host in COMFY_AUDIO_UNSAFE_URL_HOSTS or (comfy_host and source_host == comfy_host)
    if not source_is_backend_local:
        details["normalizationReason"] = "source_already_non_local"
        return original_url, details
    if not public_base_url:
        details["normalizationReason"] = "public_base_url_missing"
        return original_url, details

    try:
        parsed_public = urlparse(public_base_url)
    except Exception:
        details["normalizationReason"] = "public_base_url_parse_failed"
        return original_url, details
    public_scheme = str(parsed_public.scheme or "").strip().lower()
    public_netloc = str(parsed_public.netloc or "").strip()
    public_host = str(parsed_public.hostname or "").strip().lower()
    if not public_scheme or not public_netloc:
        details["normalizationReason"] = "public_base_url_invalid"
        return original_url, details
    if public_host in COMFY_AUDIO_UNSAFE_URL_HOSTS:
        details["normalizationReason"] = "public_base_url_localhost"
        return original_url, details

    normalized_url = urlunparse(
        (
            parsed_public.scheme,
            parsed_public.netloc,
            parsed_original.path,
            parsed_original.params,
            parsed_original.query,
            parsed_original.fragment,
        )
    )
    details["normalizedAudioUrl"] = normalized_url
    details["wasNormalized"] = normalized_url != original_url
    details["normalizationReason"] = "replaced_local_backend_host_with_public_base"
    return normalized_url, details


def _assess_remote_audio_url_safety(audio_url: str) -> tuple[bool, str]:
    source = str(audio_url or "").strip()
    if not source:
        return False, "audio_url_missing"
    try:
        parsed = urlparse(source)
    except Exception:
        return False, "audio_url_parse_failed"

    scheme = str(parsed.scheme or "").strip().lower()
    if scheme not in {"http", "https"}:
        return False, "audio_url_scheme_not_http"

    host = str(parsed.hostname or "").strip().lower()
    if not host:
        return False, "audio_url_hostname_missing"
    if host in COMFY_AUDIO_UNSAFE_URL_HOSTS:
        return False, "normalized_url_still_localhost"

    try:
        socket.getaddrinfo(host, parsed.port or (443 if scheme == "https" else 80))
    except Exception:
        return False, "normalized_host_not_reachable"

    return True, "ok"


def _is_remote_safe_audio_url(audio_url: str) -> bool:
    return _assess_remote_audio_url_safety(audio_url)[0]


def _targets_support_url_transport(audio_targets: list[dict]) -> bool:
    for target in audio_targets:
        input_key = str((target or {}).get("input_key") or "").strip().lower()
        class_type = str((target or {}).get("class_type") or "").strip().lower()
        if input_key in COMFY_AUDIO_URL_COMPATIBLE_INPUT_KEYS:
            return True
        if class_type in COMFY_AUDIO_URL_COMPATIBLE_CLASS_NAMES:
            return True
    return False


def _extract_filesystem_audio_path(audio_value: str | None) -> str:
    raw = str(audio_value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
    except Exception:
        return ""
    scheme = str(parsed.scheme or "").strip().lower()
    if scheme == "file":
        return str(parsed.path or "").strip()
    if not scheme:
        return raw
    return ""


def _resolve_audio_transport_mode_for_targets(
    *,
    audio_targets: list[dict],
    normalized_audio_url: str,
    normalized_audio_url_safe: bool,
    path_audio_value: str,
    has_audio_bytes: bool,
) -> tuple[str, str]:
    has_url_target = any(
        str((target or {}).get("class_type") or "").strip().lower() in COMFY_AUDIO_URL_COMPATIBLE_CLASS_NAMES for target in audio_targets
    )
    has_path_target = any(str((target or {}).get("class_type") or "").strip().lower() == "loadaudiofrompath" for target in audio_targets)
    has_upload_filename_target = any(
        str((target or {}).get("class_type") or "").strip().lower() in COMFY_AUDIO_UPLOAD_FILENAME_CLASS_NAMES for target in audio_targets
    )

    if has_url_target and bool(normalized_audio_url) and normalized_audio_url_safe:
        return "url", "url_target_with_remote_safe_url"
    if has_url_target and not bool(normalized_audio_url):
        return "none", "url_target_but_audio_url_missing"
    if has_url_target and not normalized_audio_url_safe:
        return "none", "url_target_but_audio_url_not_remote_safe"

    if has_path_target and bool(path_audio_value):
        return "path", "path_target_with_filesystem_path"
    if has_upload_filename_target and has_audio_bytes:
        return "upload", "source_file_node_with_audio_upload"
    if has_upload_filename_target and not has_audio_bytes:
        return "none", "source_file_node_requires_upload_but_audio_bytes_missing"
    if has_path_target and not bool(path_audio_value):
        return "none", "path_target_but_filesystem_path_missing"
    return "none", "no_compatible_audio_transport_for_discovered_targets"


def _resolve_audio_patch_value_for_target(
    *,
    target: dict,
    selected_transport_mode: str,
    original_audio_url: str,
    normalized_audio_url: str,
    path_audio_value: str,
    uploaded_audio_name: str,
) -> tuple[str | None, str, str | None]:
    class_type = str((target or {}).get("class_type") or "").strip().lower()

    if class_type in COMFY_AUDIO_UPLOAD_FILENAME_CLASS_NAMES:
        if selected_transport_mode != "upload":
            return None, "rejected_incompatible_transport", "upload_filename_transport_required_for_source_file_node"
        value = str(uploaded_audio_name or "").strip()
        if not value:
            return None, "rejected_incompatible_transport", "uploaded_audio_name_missing_for_source_file_node"
        return value, "upload_filename_for_loadaudio", None

    if class_type in {"loadaudiofromurl", "mediautilities_audiourlloader"}:
        if selected_transport_mode != "url":
            return None, "rejected_incompatible_transport", f"url_transport_required_for_{class_type}"
        value = str(normalized_audio_url or "").strip() or str(original_audio_url or "").strip()
        if not value:
            return None, "rejected_incompatible_transport", f"audio_url_missing_for_{class_type}"
        return value, f"normalized_url_for_{class_type}", None

    if class_type == "loadaudiofrompath":
        if selected_transport_mode != "path":
            return None, "rejected_incompatible_transport", "filesystem_path_transport_required_for_loadaudiofrompath"
        value = str(path_audio_value or "").strip()
        if not value:
            return None, "rejected_incompatible_transport", "audio_path_missing_for_loadaudiofrompath"
        return value, "filesystem_path_for_loadaudiofrompath", None

    return None, "rejected_incompatible_transport", f"unsupported_audio_source_class:{class_type or 'unknown'}"


def _patch_workflow_inputs(
    workflow: dict,
    *,
    image_name: str,
    prompt: str,
    width: int,
    height: int,
    requested_duration_sec: float,
    seed: int | None,
    workflow_key: str = "",
    workflow_path: str = "",
) -> tuple[dict | None, str | None, int | None, int | None, dict]:
    wf = copy.deepcopy(workflow)
    normalized_workflow_key = str(workflow_key or "").strip().lower()
    discovery = _discover_lip_sync_nodes(wf) if normalized_workflow_key == "lip_sync" else {}
    used_legacy_fallback_ids = False

    discovered_fps_id = str(discovery.get("fps_node_id") or "").strip() if isinstance(discovery, dict) else ""
    if normalized_workflow_key == "lip_sync" and not discovered_fps_id:
        discovered_fps_id = LIPSYNC_PRIMARY_NODE_IDS["fps"]
    fps = _resolve_workflow_fps(wf, preferred_fps_node_id=discovered_fps_id)
    frames = max(1, int(math.ceil(float(requested_duration_sec) * float(fps))))
    print("[COMFY LENGTH APPLY]", {
        "requestedDurationSec": float(requested_duration_sec),
        "fps": int(fps),
        "frames": int(frames),
    })

    patch_values = []
    if normalized_workflow_key == "lip_sync":
        resolved_image_node_id = _resolve_lipsync_patch_node_id(
            wf,
            expected_node_id=LIPSYNC_PRIMARY_NODE_IDS["image"],
            class_types={"loadimage"},
            required_input_key="image",
        )
        resolved_prompt_node_id = _resolve_lipsync_patch_node_id(
            wf,
            expected_node_id=LIPSYNC_PRIMARY_NODE_IDS["prompt"],
            class_types={"primitivestringmultiline"},
            required_input_key="value",
        )
        resolved_duration_node_id = _resolve_lipsync_patch_node_id(
            wf,
            expected_node_id=LIPSYNC_PRIMARY_NODE_IDS["duration"],
            class_types={"primitivefloat"},
            required_input_key="value",
        )
        resolved_fps_node_id = _resolve_lipsync_patch_node_id(
            wf,
            expected_node_id=LIPSYNC_PRIMARY_NODE_IDS["fps"],
            class_types={"primitiveint"},
            required_input_key="value",
        )
        if not resolved_image_node_id:
            return None, "missing_lipsync_image_node", None, None, {}
        if not resolved_prompt_node_id:
            return None, "missing_lipsync_prompt_node", None, None, {}
        if not resolved_duration_node_id:
            return None, "missing_lipsync_duration_node", None, None, {}
        if not resolved_fps_node_id:
            return None, "missing_lipsync_fps_node", None, None, {}
        patch_values.append((resolved_image_node_id, "image", image_name))
        patch_values.append((resolved_prompt_node_id, "value", prompt))
        patch_values.append((resolved_duration_node_id, "value", float(requested_duration_sec)))
        patch_values.append((resolved_fps_node_id, "value", int(fps)))
        used_legacy_fallback_ids = any(
            [
                resolved_image_node_id != LIPSYNC_PRIMARY_NODE_IDS["image"],
                resolved_prompt_node_id != LIPSYNC_PRIMARY_NODE_IDS["prompt"],
                resolved_duration_node_id != LIPSYNC_PRIMARY_NODE_IDS["duration"],
                resolved_fps_node_id != LIPSYNC_PRIMARY_NODE_IDS["fps"],
            ]
        )
        if used_legacy_fallback_ids:
            logger.info(
                "[COMFY LIPSYNC NODE ID DRIFT] %s",
                {
                    "expected": LIPSYNC_PRIMARY_NODE_IDS,
                    "resolved": {
                        "image": resolved_image_node_id,
                        "prompt": resolved_prompt_node_id,
                        "duration": resolved_duration_node_id,
                        "fps": resolved_fps_node_id,
                    },
                },
            )
    else:
        patch_values.extend([
            (*FIXED_IMAGE_VIDEO_NODES["image"], image_name),
            (*FIXED_IMAGE_VIDEO_NODES["prompt"], prompt),
        ])
    if normalized_workflow_key != "lip_sync":
        patch_values.extend([
            (*FIXED_IMAGE_VIDEO_NODES["width"], int(width)),
            (*FIXED_IMAGE_VIDEO_NODES["height"], int(height)),
            (*FIXED_IMAGE_VIDEO_NODES["length"], int(frames)),
        ])
    for node_id, key, value in patch_values:
        ok, err = _set_node_input(wf, node_id, key, value)
        if not ok:
            return None, err, None, None, {}
    patched_node_by_key: dict[str, str] = {}
    for node_id, key, _ in patch_values:
        if str(key) not in patched_node_by_key:
            patched_node_by_key[str(key)] = str(node_id)

    _patch_audio_frames(wf, frames)

    if seed is not None:
        for node_id, key in FIXED_SEED_NODES:
            ok, err = _set_node_input(wf, node_id, key, int(seed))
            if not ok:
                return None, err, None, None, {}

    discovery_debug = {
        "workflow_key": normalized_workflow_key,
        "workflow_path": workflow_path,
        "discoveredPromptTextNodeId": str(discovery.get("prompt_text_node_id") or ""),
        "discoveredPromptEncodeNodeId": str(discovery.get("prompt_encode_node_id") or ""),
        "discoveredWidthNodeId": str(discovery.get("width_node_id") or ""),
        "discoveredHeightNodeId": str(discovery.get("height_node_id") or ""),
        "discoveredLengthNodeId": str(discovery.get("length_node_id") or ""),
        "discoveredDurationNodeId": str(discovery.get("duration_node_id") or ""),
        "discoveredFpsNodeId": str(discovery.get("fps_node_id") or ""),
        "discoveredImageNodeIds": discovery.get("image_node_ids") or [],
        "discoveredAudioNodeIds": discovery.get("audio_node_ids") or [],
        "discoveredTrimAudioNodeIds": discovery.get("trim_audio_node_ids") or [],
        "discoveredAudioEncodeNodeIds": discovery.get("audio_encode_node_ids") or [],
        "discoveredSaveVideoNodeIds": discovery.get("save_video_node_ids") or [],
        "discoveredCreateVideoNodeIds": discovery.get("create_video_node_ids") or [],
        "saveVideoFilenamePrefix": str(discovery.get("save_video_filename_prefix") or ""),
        "usedLegacyFallbackIds": bool(used_legacy_fallback_ids),
        "patchedPromptNodeId": str((patched_node_by_key.get("value") or discovery.get("prompt_text_node_id") or LIPSYNC_PRIMARY_NODE_IDS["prompt"]) if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["prompt"][0]),
        "patchedImageNodeId": str((patched_node_by_key.get("image") or (discovery.get("image_node_ids") or [LIPSYNC_PRIMARY_NODE_IDS["image"]])[0]) if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["image"][0]),
        "patchedDurationNodeId": str((discovery.get("duration_node_id") or LIPSYNC_PRIMARY_NODE_IDS["duration"]) if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["length"][0]),
        "patchedFpsNodeId": str((discovery.get("fps_node_id") or LIPSYNC_PRIMARY_NODE_IDS["fps"]) if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["fps"][0]),
        "patchedWidthNodeId": str((discovery.get("width_node_id") or FIXED_IMAGE_VIDEO_NODES["width"][0])),
        "patchedHeightNodeId": str((discovery.get("height_node_id") or FIXED_IMAGE_VIDEO_NODES["height"][0])),
        "patchedLengthNodeId": str((discovery.get("length_node_id") or FIXED_IMAGE_VIDEO_NODES["length"][0])),
    }
    logger.info("[COMFY WORKFLOW DISCOVERY] %s", discovery_debug)
    logger.info(
        "[COMFY PATCH TARGETS] %s",
        {
            "workflow_key": normalized_workflow_key,
            "workflow_path": workflow_path,
            "patchTargets": [
                {"node_id": str(node_id), "input_key": str(key)}
                for node_id, key, _ in patch_values
            ],
        },
    )
    return wf, None, frames, fps, discovery_debug


def _apply_model_spec_to_workflow(workflow: dict, *, model_spec: dict) -> tuple[list[str], str | None]:
    ckpt_name = str((model_spec or {}).get("ckpt_name") or "").strip()
    if not ckpt_name:
        return [], "model_ckpt_name_missing"
    patched_node_ids: list[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        class_type = str(node.get("class_type") or "").strip()
        if class_type in MODEL_PATCH_NODE_TYPES and "ckpt_name" in inputs:
            inputs["ckpt_name"] = ckpt_name
            patched_node_ids.append(str(node_id))
            continue
        if "ckpt_name" in inputs and isinstance(inputs.get("ckpt_name"), str) and "ltx" in str(inputs.get("ckpt_name")).lower():
            inputs["ckpt_name"] = ckpt_name
            patched_node_ids.append(str(node_id))
    return patched_node_ids, None


def run_comfy_image_to_video(
    *,
    scene_id: str | None = None,
    image_bytes: bytes,
    image_filename: str,
    prompt: str,
    width: int,
    height: int,
    requested_duration_sec: float,
    workflow_path: str | None = None,
    workflow_key: str | None = None,
    model_key: str | None = None,
    model_spec: dict | None = None,
    start_image_bytes: bytes | None = None,
    end_image_bytes: bytes | None = None,
    audio_bytes: bytes | None = None,
    audio_url: str | None = None,
    continuation_source_asset_url: str | None = None,
    continuation_source_asset_type: str | None = None,
    requested_mode: str | None = None,
    seed: int | None = None,
) -> tuple[dict | None, str | None]:
    raw_workflow_key = str(workflow_key or "i2v").strip().lower() or "i2v"
    normalized_workflow_key = COMFY_LEGACY_WORKFLOW_ALIASES.get(raw_workflow_key, raw_workflow_key)
    workflow_family = COMFY_WORKFLOW_FAMILY.get(normalized_workflow_key, "unknown")
    model_gating_required = normalized_workflow_key in COMFY_MODEL_GATED_WORKFLOW_KEYS
    normalized_model_key = str(model_key or "").strip().lower()
    effective_model_spec = model_spec if isinstance(model_spec, dict) else MODEL_KEY_TO_MODEL_SPEC.get(normalized_model_key)
    capability_skip_reason = ""
    if model_gating_required:
        if not effective_model_spec:
            logger.info(
                "[COMFY REMOTE CAPABILITY] %s",
                {
                    "sceneId": str(scene_id or "").strip(),
                    "stage": "reject",
                    "requestedModelKey": normalized_model_key,
                    "workflowKey": normalized_workflow_key,
                    "workflowFamily": workflow_family,
                    "workflowFile": str(workflow_path or settings.COMFY_IMAGE_VIDEO_WORKFLOW or "").strip(),
                    "modelGatingRequired": True,
                    "capabilityCheckSkipped": False,
                    "reason": "model_not_found",
                },
            )
            return None, f"capability_error:LTX_MODEL_NOT_FOUND:unknown_model_key:{normalized_model_key or 'empty'}"
        compatible_workflow_keys = set(effective_model_spec.get("compatible_workflow_keys") or set())
        if normalized_workflow_key != "continuation" and compatible_workflow_keys and normalized_workflow_key not in compatible_workflow_keys:
            logger.info(
                "[COMFY REMOTE CAPABILITY] %s",
                {
                    "sceneId": str(scene_id or "").strip(),
                    "stage": "reject",
                    "requestedModelKey": normalized_model_key,
                    "workflowKey": normalized_workflow_key,
                    "workflowFamily": workflow_family,
                    "workflowFile": str(workflow_path or settings.COMFY_IMAGE_VIDEO_WORKFLOW or "").strip(),
                    "modelGatingRequired": True,
                    "capabilityCheckSkipped": False,
                    "reason": "model_workflow_incompatible",
                },
            )
            return None, f"capability_error:LTX_MODEL_WORKFLOW_INCOMPATIBLE:model={normalized_model_key};workflow={normalized_workflow_key}"
    else:
        capability_skip_reason = "non_ltx_model_gating_workflow_family"
    logger.info(
        "[COMFY REMOTE CAPABILITY] %s",
        {
            "sceneId": str(scene_id or "").strip(),
            "stage": "model_gate",
            "requestedModelKey": normalized_model_key,
            "workflowKey": normalized_workflow_key,
            "workflowFamily": workflow_family,
            "workflowFile": str(workflow_path or settings.COMFY_IMAGE_VIDEO_WORKFLOW or "").strip(),
            "modelGatingRequired": bool(model_gating_required),
            "capabilityCheckSkipped": bool(not model_gating_required),
            "reason": capability_skip_reason or "passed",
        },
    )
    capability_code, capability_hint = _validate_comfy_ltx_request(
        workflow_key=normalized_workflow_key,
        start_image_bytes=start_image_bytes,
        end_image_bytes=end_image_bytes,
        audio_bytes=audio_bytes,
        audio_url=audio_url,
        continuation_source_asset_url=continuation_source_asset_url,
        continuation_source_asset_type=continuation_source_asset_type,
    )
    if capability_code:
        logger.info(
            "[COMFY REMOTE CAPABILITY] %s",
            {
                "sceneId": str(scene_id or "").strip(),
                "stage": "reject",
                "requestedModelKey": normalized_model_key,
                "workflowKey": normalized_workflow_key,
                "workflowFamily": workflow_family,
                "workflowFile": str(workflow_path or settings.COMFY_IMAGE_VIDEO_WORKFLOW or "").strip(),
                "modelGatingRequired": bool(model_gating_required),
                "capabilityCheckSkipped": bool(not model_gating_required),
                "reason": capability_hint or capability_code,
            },
        )
        return None, f"capability_error:{capability_code}:{capability_hint or ''}"

    logger.info(
        "[COMFY REMOTE] enter run_comfy_image_to_video scene_id=%s workflow_key=%s filename=%s size_bytes=%s width=%s height=%s requested_duration_sec=%s seed=%s",
        str(scene_id or "").strip(),
        normalized_workflow_key,
        str(image_filename or "").strip() or "source.jpg",
        len(image_bytes or b""),
        int(width),
        int(height),
        float(requested_duration_sec),
        seed,
    )
    workflow_source = str(workflow_path or settings.COMFY_IMAGE_VIDEO_WORKFLOW or "").strip()
    try:
        workflow = load_workflow_json(workflow_source)
    except Exception as exc:
        return None, f"workflow_load_failed:{str(exc)[:300]}"
    workflow_fingerprint = _build_workflow_fingerprint(
        workflow=workflow,
        workflow_key=normalized_workflow_key,
        workflow_path=workflow_source,
    )
    logger.info(
        "[COMFY WORKFLOW FINGERPRINT] %s",
        {
            key: value
            for key, value in workflow_fingerprint.items()
            if key != "discoveredAudioSourceClassTypes"
        },
    )
    logger.info(
        "[COMFY WORKFLOW FINGERPRINT] discoveredAudioSourceClassTypes=%s",
        workflow_fingerprint.get("discoveredAudioSourceClassTypes") or [],
    )

    logger.info(
        "[COMFY REMOTE] calling upload_image_to_comfy filename=%s size_bytes=%s",
        str(image_filename or "").strip() or "source.jpg",
        len(image_bytes or b""),
    )
    logger.info(
        "[COMFY REMOTE SUBMIT FLOW] %s",
        {
            "sceneId": str(scene_id or "").strip(),
            "workflowKey": normalized_workflow_key,
            "workflowFamily": workflow_family,
            "workflowFile": workflow_source,
            "stage": "before_upload",
            "submitReachedUpload": True,
            "submitReachedPromptCreation": False,
        },
    )
    uploaded_name, upload_err = upload_image_to_comfy(image_bytes, image_filename)
    logger.info(
        "[COMFY REMOTE] upload_image_to_comfy returned uploaded_name=%s upload_err=%s",
        uploaded_name,
        upload_err,
    )
    if upload_err or not uploaded_name:
        return None, f"upload_failed:{upload_err or 'unknown_upload_error'}"

    effective_prompt = str(prompt or "").strip()
    uploaded_start_name = uploaded_name
    uploaded_end_name = ""
    if start_image_bytes and normalized_workflow_key in {"f_l"}:
        uploaded_start_name, start_upload_err = upload_image_to_comfy(start_image_bytes, f"{Path(image_filename).stem}_start.jpg")
        if start_upload_err or not uploaded_start_name:
            return None, f"upload_failed:{start_upload_err or 'start_image_upload_failed'}"
    if end_image_bytes and normalized_workflow_key in {"f_l"}:
        uploaded_end_name, end_upload_err = upload_image_to_comfy(end_image_bytes, f"{Path(image_filename).stem}_end.jpg")
        if end_upload_err or not uploaded_end_name:
            return None, f"upload_failed:{end_upload_err or 'end_image_upload_failed'}"

    patched_workflow, patch_err, frame_count, fps, workflow_discovery_debug = _patch_workflow_inputs(
        workflow,
        image_name=uploaded_name,
        prompt=effective_prompt,
        width=int(width),
        height=int(height),
        requested_duration_sec=float(requested_duration_sec),
        seed=seed,
        workflow_key=normalized_workflow_key,
        workflow_path=workflow_source,
    )
    if patch_err or not patched_workflow:
        return None, f"workflow_patch_failed:{patch_err or 'unknown_patch_error'}"
    patched_model_node_ids: list[str] = []
    if effective_model_spec:
        patched_model_node_ids, model_patch_err = _apply_model_spec_to_workflow(
            patched_workflow,
            model_spec=effective_model_spec,
        )
        if model_patch_err:
            return None, f"workflow_model_patch_failed:{model_patch_err}"
    first_last_applied = False
    first_last_start_node_ids: list[str] = []
    first_last_end_node_ids: list[str] = []
    if normalized_workflow_key in {"f_l"}:
        if not uploaded_end_name:
            return None, "capability_error:LTX_SECOND_FRAME_REQUIRED:end_image_missing_for_first_last_workflow"
        first_last_applied, first_last_start_node_ids, first_last_end_node_ids = _patch_first_last_images(
            patched_workflow,
            start_image_name=uploaded_start_name,
            end_image_name=uploaded_end_name,
        )
        logger.info(
            "[COMFY FIRST_LAST ROUTE] %s",
            {
                "sceneId": str(scene_id or "").strip(),
                "workflowPath": workflow_source,
                "firstFrameNodePatched": bool(first_last_start_node_ids),
                "lastFrameNodePatched": bool(first_last_end_node_ids),
                "promptNodePatched": bool(FIXED_PROMPT_PATCH_NODE_IDS),
            },
        )
        if not first_last_applied:
            return None, "capability_error:LTX_FIRST_LAST_NOT_IMPLEMENTED:second_frame_patch_not_applied"
    audio_patch_node_ids: list[str] = []
    audio_targets: list[dict] = []
    downstream_audio_nodes: list[dict] = []
    audio_transport_mode = "none"
    effective_audio_value = str(audio_url or "").strip()
    audio_targets_found = 0
    lip_sync_proof_reason = ""
    proof_reason_detailed = ""
    probable_actual_workflow_mode = "generic_i2v"
    patched_audio_node_class = ""
    patched_audio_node_title = ""
    patched_audio_node_downstream_summary: dict = {}
    workflow_lip_sync_capable = False
    audio_reaches_main_video_branch = False
    audio_reaches_mouth_control_branch = False
    workflow_uses_av_audio_path = False
    av_audio_driven_generation_present = False
    explicit_mouth_control_branch_present = False
    if normalized_workflow_key == "lip_sync":
        global _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED
        valid_audio_workflow, workflow_audio_err = _validate_audio_workflow_file(
            workflow_key=normalized_workflow_key,
            workflow_source=workflow_source,
        )
        if not valid_audio_workflow:
            return None, f"capability_error:LTX_AUDIO_WORKFLOW_PATCH_FAILED:{workflow_audio_err}"
        audio_target_plan = _collect_audio_input_targets(
            patched_workflow,
            workflow_key=normalized_workflow_key,
            workflow_file=workflow_source,
        )
        audio_targets = list((audio_target_plan or {}).get("source_targets") or [])
        downstream_audio_nodes = list((audio_target_plan or {}).get("downstream_nodes") or [])
        audio_targets_found = len(audio_targets)
        has_audio_url = bool(effective_audio_value)
        has_audio_bytes = bool(audio_bytes)
        original_audio_url = str(effective_audio_value or "").strip()
        normalized_audio_url = original_audio_url
        is_original_audio_url_safe, original_audio_url_safety_reason = _assess_remote_audio_url_safety(original_audio_url) if has_audio_url else (False, "audio_url_missing")
        normalization_log_payload = {
            "originalAudioUrl": original_audio_url,
            "normalizedAudioUrl": normalized_audio_url,
            "wasNormalized": False,
            "normalizationReason": "audio_url_missing",
            "publicBaseUrl": str(settings.PUBLIC_BASE_URL or "").strip(),
        }
        if has_audio_url:
            normalized_audio_url, normalization_log_payload = _normalize_audio_url_for_remote_transport(original_audio_url)
        is_normalized_audio_url_safe, normalized_audio_url_safety_reason = _assess_remote_audio_url_safety(normalized_audio_url) if bool(normalized_audio_url) else (False, "audio_url_missing")
        effective_audio_value = normalized_audio_url
        supports_url_transport = _targets_support_url_transport(audio_targets)
        path_audio_value = _extract_filesystem_audio_path(original_audio_url)
        audio_transport_reason = "workflow_has_audio_input_nodes" if audio_targets else "workflow_has_no_patchable_audio_input_nodes"
        has_upload_filename_target = any(
            str((target or {}).get("class_type") or "").strip().lower() in COMFY_AUDIO_UPLOAD_FILENAME_CLASS_NAMES
            for target in audio_targets
        )
        upload_audio_endpoint_supported_hint = _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED
        if has_upload_filename_target and has_audio_bytes:
            selected_transport_mode = "upload"
            selected_transport_reason = "lipsync_source_file_node_with_audio_upload"
            selected_by = "lip_sync_upload_primary_mode"
        else:
            selected_transport_mode, selected_transport_reason = _resolve_audio_transport_mode_for_targets(
                audio_targets=audio_targets,
                normalized_audio_url=normalized_audio_url,
                normalized_audio_url_safe=is_normalized_audio_url_safe,
                path_audio_value=path_audio_value,
                has_audio_bytes=has_audio_bytes,
            )
            selected_by = "lip_sync_compat_fallback_mode"
        source_audio_target_classes = [
            {
                "node_id": str((target or {}).get("node_id") or "").strip(),
                "class_type": str((target or {}).get("class_type") or "").strip().lower(),
                "input_key": str((target or {}).get("input_key") or "").strip(),
            }
            for target in audio_targets
            if isinstance(target, dict)
        ]
        downstream_audio_node_classes = [
            {
                "node_id": str((node or {}).get("node_id") or "").strip(),
                "class_type": str((node or {}).get("class_type") or "").strip().lower(),
            }
            for node in downstream_audio_nodes
            if isinstance(node, dict)
        ]
        logger.info(
            "[COMFY AUDIO TARGET DISCOVERY] %s",
            {
                "workflowKey": normalized_workflow_key,
                "workflowFile": workflow_source,
                "sourceAudioTargets": audio_targets,
                "sourceAudioTargetClasses": source_audio_target_classes,
                "downstreamAudioNodeClasses": downstream_audio_node_classes,
                "selectedTransportMode": selected_transport_mode,
                "selectedTransportReason": selected_transport_reason,
            },
        )
        upload_fallback_allowed = bool(has_upload_filename_target)
        upload_guard_reason = "allowed" if upload_fallback_allowed else selected_transport_reason
        normalization_reason = str((normalization_log_payload or {}).get("normalizationReason") or "").strip() or "unknown"
        if normalization_reason in {"public_base_url_missing", "public_base_url_invalid", "public_base_url_parse_failed", "public_base_url_localhost"}:
            normalized_audio_url_safety_reason = normalization_reason
        logger.info(
            "[LIP_SYNC COMFY AUDIO TRANSPORT PRESELECT] %s",
            {
                "workflowKey": normalized_workflow_key,
                "workflowFile": workflow_source,
                "originalAudioUrl": original_audio_url,
                "normalizedAudioUrl": normalized_audio_url,
                "wasNormalized": bool((normalization_log_payload or {}).get("wasNormalized")),
                "normalizationReason": normalization_reason,
                "publicBaseUrl": str(settings.PUBLIC_BASE_URL or "").strip(),
                "originalAudioUrlSafe": is_original_audio_url_safe,
                "normalizedAudioUrlSafe": is_normalized_audio_url_safe,
                "normalizedAudioUrlSafetyReason": normalized_audio_url_safety_reason,
                "supportsUrlTransport": supports_url_transport,
                "pathAudioValue": path_audio_value,
                "uploadAudioEndpointSupportedHint": upload_audio_endpoint_supported_hint,
                "uploadFallbackAllowed": upload_fallback_allowed,
                "selectedTransportMode": selected_transport_mode,
                "selectedBy": selected_by,
                "reason": selected_transport_reason,
            },
        )
        logger.info(
            "[COMFY LIPSYNC AUDIO URL RESOLUTION] %s",
            {
                "rawAudioSliceUrl": original_audio_url,
                "normalizedAudioSliceUrl": normalized_audio_url,
                "isRemoteSafe": is_normalized_audio_url_safe,
                "selectedTransportMode": selected_transport_mode,
                "supportsUrlTransport": supports_url_transport,
            },
        )
        logger.info(
            "[COMFY AUDIO PATCH PLAN] %s",
            {
                "workflowKey": normalized_workflow_key,
                "workflowFile": workflow_source,
                "sourceAudioTargets": audio_targets,
                "downstreamAudioNodes": downstream_audio_nodes,
                "selectedTransportMode": selected_transport_mode,
                "selectedBy": selected_by,
                "selectedTransportReason": selected_transport_reason,
                "rawAudioValuePreview": _preview_value(effective_audio_value),
                "rawAudioValueType": type(effective_audio_value).__name__,
            },
        )
        logger.info("[COMFY AUDIO URL NORMALIZATION] %s", normalization_log_payload)
        logger.info(
            "[COMFY AUDIO TRANSPORT DECISION] %s",
            {
                "workflowKey": normalized_workflow_key,
                "workflowFile": workflow_source,
                "audioTransportMode": "pending",
                "hasAudioBytes": has_audio_bytes,
                "hasAudioUrl": has_audio_url,
                "audioInputTargets": audio_targets,
                "originalAudioUrl": original_audio_url,
                "normalizedAudioUrl": normalized_audio_url,
                "wasNormalized": bool((normalization_log_payload or {}).get("wasNormalized")),
                "originalAudioUrlSafe": is_original_audio_url_safe,
                "normalizedAudioUrlSafe": is_normalized_audio_url_safe,
                "normalizedAudioUrlSafetyReason": normalized_audio_url_safety_reason,
                "supportsUrlTransport": supports_url_transport,
                "pathAudioValue": path_audio_value,
                "uploadFallbackAllowed": upload_fallback_allowed,
                "reason": audio_transport_reason,
            },
        )
        if audio_targets:
            uploaded_audio_name = ""
            audio_transport_mode = selected_transport_mode
            logger.info(
                "[COMFY AUDIO UPLOAD GUARD] %s",
                {
                    "workflowKey": normalized_workflow_key,
                    "workflowFile": workflow_source,
                    "uploadFallbackAllowed": upload_fallback_allowed,
                    "reason": upload_guard_reason,
                    "uploadBlockedBecauseEndpointUnsupported": False,
                },
            )
            if audio_transport_mode in {"url", "upload", "path"}:
                if audio_transport_mode == "upload":
                    if not has_audio_bytes:
                        return None, "capability_error:LTX_AUDIO_TRANSPORT_UNAVAILABLE:audio_bytes_missing_for_upload"
                    uploaded_audio_name, upload_audio_err = upload_audio_to_comfy(
                        audio_bytes=audio_bytes or b"",
                        filename="source.mp3",
                        workflow_key=normalized_workflow_key,
                        workflow_file=workflow_source,
                        transport_mode=audio_transport_mode,
                    )
                    if upload_audio_err or not uploaded_audio_name:
                        if str(upload_audio_err or "").startswith("capability_error:"):
                            return None, str(upload_audio_err)
                        return None, f"upload_failed:{upload_audio_err or 'upload_name_missing'}"
                audio_patch_node_ids = []
                lip_sync_proof_reason = "audio_patch_applied"
                audio_patch_types = []
                for target in audio_targets:
                    target_node_id = str((target or {}).get("node_id") or "").strip()
                    target_input_key = str((target or {}).get("input_key") or "").strip()
                    target_class_type = str((target or {}).get("class_type") or "").strip().lower()
                    node = patched_workflow.get(target_node_id)
                    actual_class_type = str((node or {}).get("class_type") or "").strip().lower() if isinstance(node, dict) else ""
                    actual_inputs = (node or {}).get("inputs") if isinstance(node, dict) else None
                    actual_input_keys = sorted(actual_inputs.keys()) if isinstance(actual_inputs, dict) else []
                    logger.info(
                        "[COMFY AUDIO TARGET SNAPSHOT] %s",
                        {
                            "nodeId": target_node_id,
                            "discoveredClassType": target_class_type,
                            "actualClassType": actual_class_type,
                            "discoveredInputKey": target_input_key,
                            "actualInputKeys": actual_input_keys,
                        },
                    )

                    resolved_value, value_strategy, value_err = _resolve_audio_patch_value_for_target(
                        target=target,
                        selected_transport_mode=audio_transport_mode,
                        original_audio_url=original_audio_url,
                        normalized_audio_url=normalized_audio_url,
                        path_audio_value=path_audio_value,
                        uploaded_audio_name=uploaded_audio_name,
                    )
                    logger.info(
                        "[COMFY AUDIO VALUE STRATEGY] %s",
                        {
                            "workflowKey": normalized_workflow_key,
                            "workflowFile": workflow_source,
                            "targetNodeId": target_node_id,
                            "targetClassType": target_class_type,
                            "targetInputKey": target_input_key,
                            "selectedTransportMode": audio_transport_mode,
                            "valueStrategy": value_strategy,
                            "patchedValueType": type(resolved_value).__name__ if resolved_value is not None else "none",
                            "patchedValuePreview": _preview_value(resolved_value),
                        },
                    )
                    if value_err:
                        return None, f"capability_error:LTX_AUDIO_TRANSPORT_INCOMPATIBLE:{value_err}"
                    if not target_node_id or not target_input_key or not isinstance(node, dict):
                        continue
                    inputs = node.get("inputs")
                    if not isinstance(inputs, dict):
                        continue
                    allow_create = bool((target or {}).get("allow_create"))
                    if target_input_key not in inputs and not allow_create:
                        continue
                    inputs[target_input_key] = resolved_value
                    audio_patch_node_ids.append(target_node_id)
                if not audio_patch_node_ids:
                    return None, "capability_error:LTX_AUDIO_WORKFLOW_PATCH_FAILED:audio_target_patch_failed"

                patched_workflow_source_nodes = []
                for target in audio_targets:
                    node_id = str((target or {}).get("node_id") or "").strip()
                    if not node_id:
                        continue
                    node = patched_workflow.get(node_id)
                    if not isinstance(node, dict):
                        continue
                    inputs = node.get("inputs")
                    if not isinstance(inputs, dict):
                        inputs = {}
                    patched_workflow_source_nodes.append(
                        {
                            "nodeId": node_id,
                            "actualClassType": str(node.get("class_type") or "").strip(),
                            "actualInputKeys": sorted(inputs.keys()),
                            "audioPreview": _preview_value(inputs.get("audio")),
                            "urlPreview": _preview_value(inputs.get("url")),
                        }
                    )
                logger.info(
                    "[COMFY AUDIO PATCHED WORKFLOW SUMMARY] %s",
                    {"sourceNodes": patched_workflow_source_nodes},
                )

                patched_nodes_set = set(audio_patch_node_ids)
                for target in audio_targets:
                    node_id = str((target or {}).get("node_id") or "").strip()
                    if not node_id or node_id not in patched_nodes_set:
                        continue
                    node = patched_workflow.get(node_id)
                    if not isinstance(node, dict):
                        continue
                    inputs = node.get("inputs")
                    if not isinstance(inputs, dict):
                        continue
                    if str(node.get("class_type") or "").strip().lower() == "loadaudio":
                        input_key = "audio"
                    else:
                        input_key = str((target or {}).get("input_key") or "").strip()
                    if not input_key or input_key not in inputs:
                        continue
                    patched_value = inputs.get(input_key)
                    audio_patch_types.append(
                        {
                            "node_id": node_id,
                            "class_type": str(node.get("class_type") or "").strip(),
                            "input_key": input_key,
                            "patched_value_type": type(patched_value).__name__,
                            "patched_value_preview": _preview_value(patched_value),
                        }
                    )
                logger.info(
                    "[COMFY AUDIO PATCH APPLY] %s",
                    {
                        "workflowKey": normalized_workflow_key,
                        "workflowFile": workflow_source,
                        "patchedSourceNodeIds": audio_patch_node_ids,
                        "patchedSourceNodeClasses": list(
                            dict.fromkeys(
                                [
                                    str((patched_workflow.get(node_id) or {}).get("class_type") or "").strip()
                                    for node_id in audio_patch_node_ids
                                    if isinstance(patched_workflow.get(node_id), dict)
                                ]
                            )
                        ),
                        "patchedInputKeys": list(
                            dict.fromkeys([str(item.get("input_key") or "").strip() for item in audio_patch_types if isinstance(item, dict)])
                        ),
                        "patchedValueType": "class_aware",
                        "patchedValuePreview": "see_[COMFY_AUDIO_PATCH_TYPES]",
                    },
                )
                logger.info(
                    "[COMFY AUDIO PATCH TYPES] %s",
                    {
                        "workflowKey": normalized_workflow_key,
                        "workflowFile": workflow_source,
                        "audioTransportMode": audio_transport_mode,
                        "audioPatchTypes": audio_patch_types,
                    },
                )
            else:
                return None, f"capability_error:LTX_AUDIO_TRANSPORT_UNAVAILABLE:{selected_transport_reason}"
            final_reason = audio_transport_reason
        else:
            audio_transport_mode = "skip_no_audio_target"
            selected_by = "no_patchable_audio_target"
            final_reason = audio_transport_reason
            lip_sync_proof_reason = "audio_targets_not_found"
            logger.critical(
                "[COMFY LIPSYNC DEGRADED CRITICAL] %s",
                {
                    "sceneId": str(scene_id or "").strip(),
                    "workflowKey": normalized_workflow_key,
                    "workflowFile": workflow_source,
                    "reason": lip_sync_proof_reason,
                    "audioTransportMode": audio_transport_mode,
                },
            )
        logger.info(
            "[COMFY AUDIO TRANSPORT SAFETY] %s",
            {
                "workflowKey": normalized_workflow_key,
                "workflowFile": workflow_source,
                "audioUrl": effective_audio_value,
                "isRemoteSafeAudioUrl": is_normalized_audio_url_safe,
                "chosenTransportMode": audio_transport_mode,
                "reason": selected_by,
            },
        )
        logger.info(
            "[COMFY AUDIO TRANSPORT DECISION] %s",
            {
                "workflowKey": normalized_workflow_key,
                "workflowFile": workflow_source,
                "hasAudioBytes": has_audio_bytes,
                "hasAudioUrl": has_audio_url,
                "selectedTransportMode": audio_transport_mode,
                "selectedBy": selected_by,
                "reason": final_reason,
                "originalAudioUrl": original_audio_url,
                "normalizedAudioUrl": normalized_audio_url,
                "wasNormalized": bool((normalization_log_payload or {}).get("wasNormalized")),
                "originalAudioUrlSafe": is_original_audio_url_safe,
                "normalizedAudioUrlSafe": is_normalized_audio_url_safe,
                "normalizedAudioUrlSafetyReason": normalized_audio_url_safety_reason,
                "originalAudioUrlSafetyReason": original_audio_url_safety_reason,
                "supportsUrlTransport": supports_url_transport,
                "uploadFallbackAllowed": upload_fallback_allowed,
            },
        )
        audio_used = bool(audio_patch_node_ids)
        lip_sync_proof_confirmed = bool(
            audio_used
            and audio_targets_found > 0
            and audio_transport_mode in {"url", "upload", "path"}
            and bool(str(effective_audio_value).strip())
        )
        if not lip_sync_proof_confirmed and not lip_sync_proof_reason:
            if not audio_used:
                lip_sync_proof_reason = "audio_patch_node_ids_empty"
            elif audio_targets_found <= 0:
                lip_sync_proof_reason = "audio_targets_not_found"
            elif audio_transport_mode not in {"url", "upload", "path"}:
                lip_sync_proof_reason = f"audio_transport_mode_invalid:{audio_transport_mode}"
            elif not str(effective_audio_value).strip():
                lip_sync_proof_reason = "audio_input_value_empty"
            else:
                lip_sync_proof_reason = "audio_proof_contract_not_satisfied"
        logger.info(
            "[COMFY AUDIO PATCH RESULT] %s",
            {
                "sceneId": str(scene_id or "").strip(),
                "workflowKey": normalized_workflow_key,
                "workflowFile": workflow_source,
                "audioPatchNodeIds": audio_patch_node_ids,
                "audioUsed": audio_used,
                "lipSyncProofConfirmedProvisional": lip_sync_proof_confirmed,
                "lipSyncProofReason": lip_sync_proof_reason or "ok",
            },
        )
        inspection = _inspect_audio_path_mode(
            patched_workflow,
            audio_patch_node_ids=audio_patch_node_ids,
        )
        patched_audio_node_class = str(inspection.get("patchedAudioNodeClass") or "").strip()
        patched_audio_node_title = str(inspection.get("patchedAudioNodeTitle") or "").strip()
        patched_audio_node_downstream_summary = inspection.get("patchedAudioNodeDownstreamSummary") if isinstance(inspection.get("patchedAudioNodeDownstreamSummary"), dict) else {}
        workflow_lip_sync_capable = bool(inspection.get("workflowLipSyncCapable"))
        audio_reaches_main_video_branch = bool(inspection.get("audioReachesMainVideoBranch"))
        audio_reaches_mouth_control_branch = bool(inspection.get("audioReachesMouthControlBranch"))
        workflow_uses_av_audio_path = bool(inspection.get("workflowUsesAVAudioPath"))
        av_audio_driven_generation_present = bool(inspection.get("avAudioDrivenGenerationPresent"))
        explicit_mouth_control_branch_present = bool(inspection.get("explicitMouthControlBranchPresent"))
        logger.info(
            "[COMFY LIPSYNC WORKFLOW INSPECTION] %s",
            {
                "sceneId": str(scene_id or "").strip(),
                "workflowKey": normalized_workflow_key,
                "workflowFile": workflow_source,
                "patchedAudioNodeClass": patched_audio_node_class,
                "patchedAudioNodeTitle": patched_audio_node_title,
                "patchedAudioNodeDownstreamSummary": patched_audio_node_downstream_summary,
                "workflowLipSyncCapable": workflow_lip_sync_capable,
                "audioReachesMainVideoBranch": audio_reaches_main_video_branch,
                "audioReachesMouthControlBranch": audio_reaches_mouth_control_branch,
                "workflowUsesAVAudioPath": workflow_uses_av_audio_path,
                "avAudioDrivenGenerationPresent": av_audio_driven_generation_present,
                "explicitMouthControlBranchPresent": explicit_mouth_control_branch_present,
            },
        )
    audio_used = bool(audio_patch_node_ids)
    lip_sync_proof_confirmed = True
    lip_sync_degraded_to_i2v = False
    probable_fallback_mode = ""
    if normalized_workflow_key == "lip_sync":
        base_audio_patch_contract_confirmed = bool(
            audio_used
            and audio_targets_found > 0
            and audio_transport_mode in {"url", "upload", "path"}
            and bool(str(effective_audio_value).strip())
        )
        explicit_mouth_control_lipsync_confirmed = bool(base_audio_patch_contract_confirmed and audio_reaches_mouth_control_branch)
        ltx_av_audio_driven_confirmed = bool(
            base_audio_patch_contract_confirmed
            and workflow_uses_av_audio_path
            and av_audio_driven_generation_present
            and audio_reaches_main_video_branch
        )
        lip_sync_proof_confirmed = bool(explicit_mouth_control_lipsync_confirmed or ltx_av_audio_driven_confirmed)
        if explicit_mouth_control_lipsync_confirmed:
            probable_actual_workflow_mode = "explicit_mouth_control_lipsync"
        elif ltx_av_audio_driven_confirmed:
            probable_actual_workflow_mode = "ltx_av_audio_driven_performance"
        else:
            probable_actual_workflow_mode = "generic_i2v"
        lip_sync_degraded_to_i2v = probable_actual_workflow_mode == "generic_i2v"
        probable_fallback_mode = "i2v_like" if lip_sync_degraded_to_i2v else "audio_driven_character_performance"
        if explicit_mouth_control_lipsync_confirmed:
            lip_sync_proof_reason = "audio_patch_contract_confirmed"
            proof_reason_detailed = "mouth_control_branch_confirmed"
        elif ltx_av_audio_driven_confirmed:
            lip_sync_proof_reason = "audio_patch_contract_confirmed"
            proof_reason_detailed = "ltx_av_audio_driven_generation_path_confirmed"
        elif not lip_sync_proof_reason:
            lip_sync_proof_reason = "audio_proof_contract_not_satisfied"
        if not proof_reason_detailed:
            if not base_audio_patch_contract_confirmed:
                proof_reason_detailed = "audio_patch_contract_not_satisfied"
            elif audio_used and audio_reaches_main_video_branch and workflow_uses_av_audio_path and not audio_reaches_mouth_control_branch:
                proof_reason_detailed = "ltx_av_audio_path_confirmed_without_explicit_mouth_control_branch"
            elif not audio_used:
                proof_reason_detailed = "audio_patch_not_applied"
            elif not audio_reaches_main_video_branch:
                proof_reason_detailed = "audio_branch_does_not_reach_main_video_path"
            elif not explicit_mouth_control_branch_present:
                proof_reason_detailed = "workflow_has_no_mouth_lipsync_capable_nodes"
            else:
                proof_reason_detailed = "real_mouth_lipsync_path_not_proven"
    elif normalized_workflow_key == "i2v":
        probable_actual_workflow_mode = "generic_i2v"
    if normalized_workflow_key == "lip_sync":
        logger.info(
            "[COMFY LIPSYNC MODE RESOLUTION] %s",
            {
                "routeRequestWorkflowKey": "lip_sync",
                "actualWorkflowMode": probable_actual_workflow_mode,
                "audioDrivenProofConfirmed": lip_sync_proof_confirmed,
                "realMouthSyncProven": explicit_mouth_control_lipsync_confirmed,
                "proofReasonDetailed": proof_reason_detailed or "",
            },
        )
    continuation_used = False
    continuation_asset_type = _detect_continuation_asset_type(continuation_source_asset_url, continuation_source_asset_type)
    if normalized_workflow_key == "continuation":
        if not str(continuation_source_asset_url or "").strip():
            return None, "capability_error:LTX_CONTINUATION_SOURCE_REQUIRED:continuation_source_asset_url_missing"
        if continuation_asset_type == "video":
            return None, "capability_error:LTX_CONTINUATION_SOURCE_INCOMPATIBLE:continuation source resolved to video asset but current continuation path requires image/frame source"
        return None, "capability_error:LTX_CONTINUATION_NOT_IMPLEMENTED:continuation mode requested by scene but current continuation execution strategy is not implemented yet"

    discovered_audio_node_ids = [str(item) for item in (workflow_discovery_debug.get("discoveredAudioNodeIds") if isinstance(workflow_discovery_debug, dict) else []) or [] if str(item).strip()]
    discovered_trim_audio_node_ids = [str(item) for item in (workflow_discovery_debug.get("discoveredTrimAudioNodeIds") if isinstance(workflow_discovery_debug, dict) else []) or [] if str(item).strip()]
    discovered_audio_encode_node_ids = [str(item) for item in (workflow_discovery_debug.get("discoveredAudioEncodeNodeIds") if isinstance(workflow_discovery_debug, dict) else []) or [] if str(item).strip()]
    discovered_create_video_node_ids = [str(item) for item in (workflow_discovery_debug.get("discoveredCreateVideoNodeIds") if isinstance(workflow_discovery_debug, dict) else []) or [] if str(item).strip()]
    discovered_save_video_node_ids = [str(item) for item in (workflow_discovery_debug.get("discoveredSaveVideoNodeIds") if isinstance(workflow_discovery_debug, dict) else []) or [] if str(item).strip()]
    logger.info(
        "[COMFY PATCHED VALUE SNAPSHOT] %s",
        {
            "workflow_key": normalized_workflow_key,
            "workflow_file": workflow_source,
            "discoveredPromptTextNodeId": str((workflow_discovery_debug.get("discoveredPromptTextNodeId") if isinstance(workflow_discovery_debug, dict) else "") or ""),
            "discoveredImageNodeIds": (workflow_discovery_debug.get("discoveredImageNodeIds") if isinstance(workflow_discovery_debug, dict) else []) or [],
            "discoveredAudioNodeIds": discovered_audio_node_ids,
            "discoveredTrimAudioNodeIds": discovered_trim_audio_node_ids,
            "discoveredAudioEncodeNodeIds": discovered_audio_encode_node_ids,
            "discoveredCreateVideoNodeIds": discovered_create_video_node_ids,
            "discoveredSaveVideoNodeIds": discovered_save_video_node_ids,
            "saveVideoFilenamePrefix": str((workflow_discovery_debug.get("saveVideoFilenamePrefix") if isinstance(workflow_discovery_debug, dict) else "") or ""),
            "patched_prompt_value_preview": _preview_value(
                patched_workflow.get(
                    str(
                        (workflow_discovery_debug.get("patchedPromptNodeId") if isinstance(workflow_discovery_debug, dict) else "")
                        or (LIPSYNC_PRIMARY_NODE_IDS["prompt"] if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["prompt"][0])
                    ),
                    {},
                ).get("inputs", {}).get("value")
            ),
            "patched_image_value": _preview_value(
                patched_workflow.get(str((workflow_discovery_debug.get("patchedImageNodeId") if isinstance(workflow_discovery_debug, dict) else "") or "269"), {}).get("inputs", {}).get("image")
            ),
            "patched_audio_input_value": _preview_value(effective_audio_value),
            "patched_trim_audio_inputs": _snapshot_node_inputs(
                patched_workflow,
                discovered_trim_audio_node_ids,
                allowed_keys={"audio", "duration", "duration_sec", "frames", "frames_number", "fps", "sample_rate"},
            ),
            "patched_audio_encode_inputs": _snapshot_node_inputs(
                patched_workflow,
                discovered_audio_encode_node_ids,
                allowed_keys={"audio", "samples", "sample_rate", "vae", "audio_vae"},
            ),
            "patched_create_video_inputs": _snapshot_node_inputs(
                patched_workflow,
                discovered_create_video_node_ids,
                allowed_keys={"images", "image", "audio", "latents", "vae", "fps", "frames", "num_frames"},
            ),
            "patched_savevideo_prefix": _snapshot_node_inputs(
                patched_workflow,
                discovered_save_video_node_ids,
                allowed_keys={"filename_prefix"},
            ),
        },
    )
    if normalized_workflow_key == "lip_sync":
        logger.info(
            "[COMFY WORKING LIPSYNC PATCH SUMMARY] %s",
            {
                "imageNodeId": str((workflow_discovery_debug.get("patchedImageNodeId") if isinstance(workflow_discovery_debug, dict) else "") or ""),
                "audioNodeId": str((audio_patch_node_ids or [""])[0] or ""),
                "promptNodeId": str((workflow_discovery_debug.get("patchedPromptNodeId") if isinstance(workflow_discovery_debug, dict) else "") or ""),
                "durationNodeId": str((workflow_discovery_debug.get("patchedDurationNodeId") if isinstance(workflow_discovery_debug, dict) else "") or ""),
                "fpsNodeId": str((workflow_discovery_debug.get("patchedFpsNodeId") if isinstance(workflow_discovery_debug, dict) else "") or ""),
                "patchedImagePreview": _preview_value(
                    patched_workflow.get(str((workflow_discovery_debug.get("patchedImageNodeId") if isinstance(workflow_discovery_debug, dict) else "") or ""), {}).get("inputs", {}).get("image")
                ),
                "patchedAudioPreview": _preview_value(effective_audio_value),
                "patchedDuration": patched_workflow.get(
                    str((workflow_discovery_debug.get("patchedDurationNodeId") if isinstance(workflow_discovery_debug, dict) else "") or ""),
                    {},
                ).get("inputs", {}).get("value"),
                "patchedFps": patched_workflow.get(
                    str((workflow_discovery_debug.get("patchedFpsNodeId") if isinstance(workflow_discovery_debug, dict) else "") or ""),
                    {},
                ).get("inputs", {}).get("value"),
            },
        )
    logger.info(
        "[COMFY REMOTE PROMPT TRANSPORT] scene_id=%s workflow_key=%s model_key=%s prompt_patched_node_ids=%s final_prompt_length=%s final_prompt_preview=%r",
        str(scene_id or "").strip(),
        normalized_workflow_key,
        normalized_model_key,
        [
            str(
                (workflow_discovery_debug.get("patchedPromptNodeId") if isinstance(workflow_discovery_debug, dict) else "")
                or (LIPSYNC_PRIMARY_NODE_IDS["prompt"] if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["prompt"][0])
            )
        ],
        len(effective_prompt),
        effective_prompt[:500],
    )
    logger.info(
        "[COMFY REMOTE SUBMIT FLOW] %s",
        {
            "sceneId": str(scene_id or "").strip(),
            "workflowKey": normalized_workflow_key,
            "workflowFamily": workflow_family,
            "workflowFile": workflow_source,
            "audioTargetsFound": audio_targets_found,
            "audioTransportMode": audio_transport_mode,
            "audioInputValuePresent": bool(str(effective_audio_value).strip()),
            "stage": "before_prompt_create",
            "submitReachedUpload": True,
            "submitReachedPromptCreation": True,
        },
    )

    prompt_id, submit_err = submit_comfy_prompt(patched_workflow)
    logger.info(
        "[COMFY REMOTE SUBMIT FLOW] %s",
        {
            "sceneId": str(scene_id or "").strip(),
            "workflowKey": normalized_workflow_key,
            "workflowFamily": workflow_family,
            "workflowFile": workflow_source,
            "stage": "after_prompt_create",
            "submitReachedUpload": True,
            "submitReachedPromptCreation": True,
            "promptIdReceived": bool(prompt_id and not submit_err),
        },
    )
    if submit_err or not prompt_id:
        return None, f"prompt_submit_failed:{submit_err or 'unknown_submit_error'}"
    logger.info(
        "[COMFY REMOTE EXECUTION STAGE] prompt submit succeeded, waiting execution history prompt_id=%s workflow_key=%s workflow_file=%s",
        prompt_id,
        normalized_workflow_key,
        workflow_source,
    )

    poll_timeout_sec = max(10, int(settings.COMFY_POLL_TIMEOUT_SEC or 600))
    history, wait_err = wait_for_comfy_result(
        prompt_id,
        timeout_sec=poll_timeout_sec,
        poll_interval_sec=max(2, int(settings.COMFY_POLL_INTERVAL_SEC or 2)),
        workflow_key=normalized_workflow_key,
        workflow_file=workflow_source,
    )
    if wait_err or not history:
        logger.warning(
            "[COMFY REMOTE] history wait failed prompt_id=%s timeout_sec=%s jobId=%s err=%s history_present=%s execution_stage_failure=%s",
            prompt_id,
            poll_timeout_sec,
            prompt_id,
            wait_err or 'unknown_wait_error',
            bool(history),
            bool(prompt_id),
        )
        return None, f"history_wait_failed:{wait_err or 'unknown_wait_error'}"

    file_ref, extract_err = extract_video_result(history)
    if extract_err or not file_ref:
        return None, f"extract_failed:{extract_err or 'unknown_extract_error'}"
    video_url, file_meta, handoff_strategy = build_public_comfy_file_url(file_ref)
    logger.info(
        "[COMFY RESULT FILE REF] prompt_id=%s raw_file_ref=%s filename=%s subfolder=%s type=%s format=%s",
        prompt_id,
        str(file_ref or "").strip(),
        str((file_meta or {}).get("filename") or "").strip(),
        str((file_meta or {}).get("subfolder") or "").strip(),
        str((file_meta or {}).get("type") or "").strip(),
        str((file_meta or {}).get("format") or "").strip(),
    )
    logger.info(
        "[COMFY RESULT URL BUILD] prompt_id=%s base_comfy=%s base_public=%s strategy=%s final_video_url=%s",
        prompt_id,
        str(settings.COMFY_BASE_URL).rstrip("/"),
        str(settings.PUBLIC_BASE_URL).rstrip("/"),
        handoff_strategy,
        video_url,
    )
    if not video_url:
        return None, "COMFY_OUTPUT_URL_INVALID:video_url_empty"
    is_accessible, access_err = validate_comfy_output_access(file_meta or {})
    if not is_accessible:
        return None, access_err or "COMFY_OUTPUT_NOT_ACCESSIBLE"
    logger.info(
        "[COMFY FINAL LIPSYNC PROOF] %s",
        {
            "provider": "comfy_remote",
            "sceneId": str(scene_id or "").strip(),
            "workflowKey": normalized_workflow_key,
            "workflowFile": workflow_source,
            "audioUsed": audio_used,
            "audioPatchNodeIds": audio_patch_node_ids,
            "lipSyncProofConfirmed": lip_sync_proof_confirmed,
            "lipSyncDegradedToI2V": lip_sync_degraded_to_i2v,
            "probableActualWorkflowMode": probable_actual_workflow_mode,
            "workflowLipSyncCapable": workflow_lip_sync_capable,
            "audioReachesMainVideoBranch": audio_reaches_main_video_branch,
            "audioReachesMouthControlBranch": audio_reaches_mouth_control_branch,
            "workflowUsesAVAudioPath": workflow_uses_av_audio_path,
            "avAudioDrivenGenerationPresent": av_audio_driven_generation_present,
            "explicitMouthControlBranchPresent": explicit_mouth_control_branch_present,
            "reason": lip_sync_proof_reason or "ok",
            "proofReasonDetailed": proof_reason_detailed or "",
        },
    )

    return {
        "provider": "comfy_remote",
        "mode": normalized_workflow_key,
        "videoUrl": video_url,
        "model": normalized_model_key,
        "requestedDurationSec": round(float(requested_duration_sec), 3),
        "taskId": prompt_id,
        "debug": {
            "scene_id": str(scene_id or "").strip(),
            "provider": "comfy_remote",
            "requested_mode": str(requested_mode or "").strip().lower() or normalized_workflow_key,
            "resolved_workflow_key": normalized_workflow_key,
            "workflow_key": normalized_workflow_key,
            "workflow_file": Path(str(workflow_source)).name,
            "workflow": workflow_source,
            "workflow_path": workflow_source,
            "workflow_family": workflow_family,
            "model_key": normalized_model_key,
            "model_ckpt_applied": str((effective_model_spec or {}).get("ckpt_name") or ""),
            "model_gating_required": bool(model_gating_required),
            "capability_check_skipped": bool(not model_gating_required),
            "capability_skip_reason": capability_skip_reason,
            "actual_mode": normalized_workflow_key,
            "patched_node_ids": list(dict.fromkeys([*patched_model_node_ids, *first_last_start_node_ids, *first_last_end_node_ids, *audio_patch_node_ids])),
            "prompt_patched_node_ids": [
                str(
                    (workflow_discovery_debug.get("patchedPromptNodeId") if isinstance(workflow_discovery_debug, dict) else "")
                    or (LIPSYNC_PRIMARY_NODE_IDS["prompt"] if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["prompt"][0])
                )
            ],
            "workflow_discovery": workflow_discovery_debug if isinstance(workflow_discovery_debug, dict) else {},
            "first_last_start_node_ids": first_last_start_node_ids,
            "first_last_end_node_ids": first_last_end_node_ids,
            "start_image_used": bool(first_last_start_node_ids),
            "end_image_used": bool(first_last_end_node_ids),
            "second_frame_patch_applied": bool(first_last_applied),
            "audio_used": bool(audio_patch_node_ids),
            "audio_targets_found": audio_targets_found,
            "audio_targets_summary": audio_targets[:8],
            "audio_downstream_nodes_summary": downstream_audio_nodes[:8],
            "audio_transport_mode": audio_transport_mode,
            "audio_input_value": effective_audio_value,
            "continuation_used": continuation_used,
            "continuation_source_asset_type": continuation_asset_type,
            "continuation_source_asset_url_present": bool(str(continuation_source_asset_url or "").strip()),
            "audio_patch_node_ids": audio_patch_node_ids,
            "audioProofConfirmed": lip_sync_proof_confirmed,
            "lipSyncProofConfirmed": lip_sync_proof_confirmed,
            "lipSyncDegradedToI2V": lip_sync_degraded_to_i2v,
            "lipSyncProofReason": lip_sync_proof_reason or ("ok" if lip_sync_proof_confirmed else "audio_proof_contract_not_satisfied"),
            "proofReasonDetailed": proof_reason_detailed or "",
            "probableFallbackMode": probable_fallback_mode,
            "probableActualWorkflowMode": probable_actual_workflow_mode,
            "patchedAudioNodeClass": patched_audio_node_class,
            "patchedAudioNodeTitle": patched_audio_node_title,
            "patchedAudioNodeDownstreamSummary": patched_audio_node_downstream_summary,
            "workflowLipSyncCapable": workflow_lip_sync_capable,
            "audioReachesMainVideoBranch": audio_reaches_main_video_branch,
            "audioReachesMouthControlBranch": audio_reaches_mouth_control_branch,
            "workflowUsesAVAudioPath": workflow_uses_av_audio_path,
            "avAudioDrivenGenerationPresent": av_audio_driven_generation_present,
            "explicitMouthControlBranchPresent": explicit_mouth_control_branch_present,
            "capabilities": COMFY_LTX_CAPABILITIES,
            "capabilities_snapshot": COMFY_LTX_CAPABILITIES,
            "inputsUsed": {
                "image": True,
                "startImage": bool(start_image_bytes),
                "endImage": bool(end_image_bytes),
                "audio": bool(audio_bytes or str(audio_url or "").strip()),
            },
            "inputsProvided": {
                "startImage": bool(start_image_bytes),
                "endImage": bool(end_image_bytes),
                "audioBytes": bool(audio_bytes),
                "audioUrl": bool(str(audio_url or "").strip()),
            },
            "usedNodeIds": {
                "image": str(
                    (workflow_discovery_debug.get("patchedImageNodeId") if isinstance(workflow_discovery_debug, dict) else "")
                    or (LIPSYNC_PRIMARY_NODE_IDS["image"] if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["image"][0])
                ),
                "promptSource": str(
                    (workflow_discovery_debug.get("patchedPromptNodeId") if isinstance(workflow_discovery_debug, dict) else "")
                    or (LIPSYNC_PRIMARY_NODE_IDS["prompt"] if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["prompt"][0])
                ),
                "duration": str(
                    (workflow_discovery_debug.get("patchedDurationNodeId") if isinstance(workflow_discovery_debug, dict) else "")
                    or (LIPSYNC_PRIMARY_NODE_IDS["duration"] if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["length"][0])
                ),
                "fps": str(
                    (workflow_discovery_debug.get("patchedFpsNodeId") if isinstance(workflow_discovery_debug, dict) else "")
                    or (LIPSYNC_PRIMARY_NODE_IDS["fps"] if normalized_workflow_key == "lip_sync" else FIXED_IMAGE_VIDEO_NODES["fps"][0])
                ),
                "noiseSeed": [node_id for node_id, _ in FIXED_SEED_NODES],
            },
            "uploadedImage": uploaded_name,
            "fileRef": file_ref,
            "rawComfyFileRef": str(file_ref or "").strip(),
            "fileRefMeta": file_meta,
            "promptId": prompt_id,
            "handoffStrategy": handoff_strategy,
            "finalVideoUrl": video_url,
            "frames": frame_count,
            "fps": fps,
            "final_prompt_length": len(effective_prompt),
            "final_prompt_preview": effective_prompt[:500],
            "final_prompt_sent": effective_prompt,
        },
    }, None
