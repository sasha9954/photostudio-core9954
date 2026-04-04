from __future__ import annotations

import copy
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
COMFY_AUDIO_URL_COMPATIBLE_CLASS_NAMES = {"loadaudiofromurl", "loadaudiofrompath"}

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
    url = f"{str(settings.COMFY_BASE_URL).rstrip('/')}/upload/audio"
    safe_name = str(filename or "source.mp3").strip() or "source.mp3"
    size_bytes = len(audio_bytes or b"")
    connect_timeout = max(20, int(settings.COMFY_UPLOAD_CONNECT_TIMEOUT_SEC or 20))
    read_timeout = max(180, int(settings.COMFY_UPLOAD_READ_TIMEOUT_SEC or 180))
    max_attempts = max(4, int(settings.COMFY_UPLOAD_MAX_ATTEMPTS or 4))

    logger.info(
        "[COMFY REMOTE] audio upload start url=%s filename=%s size_bytes=%s connect_timeout=%s read_timeout=%s max_attempts=%s",
        url,
        safe_name,
        size_bytes,
        connect_timeout,
        read_timeout,
        max_attempts,
    )
    logger.info(
        "[COMFY AUDIO UPLOAD ATTEMPT] %s",
        {
            "endpoint": url,
            "method": "POST",
            "workflowKey": str(workflow_key or "").strip(),
            "workflowFile": str(workflow_file or "").strip(),
            "transportMode": str(transport_mode or "").strip(),
            "status": None,
            "success": False,
            "bodyPreview": "",
        },
    )

    files = {
        "audio": (safe_name, audio_bytes, "application/octet-stream"),
    }
    data = {"type": "input", "overwrite": "true"}

    last_error = "audio_upload_unknown_error"
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.post(url, files=files, data=data, timeout=(connect_timeout, read_timeout))
            body_snippet = _response_body_snippet(resp)
            logger.info(
                "[COMFY REMOTE] audio upload response attempt=%s status=%s body=%r",
                attempt,
                resp.status_code,
                body_snippet,
            )
            logger.info(
                "[COMFY AUDIO UPLOAD ATTEMPT] %s",
                {
                    "endpoint": url,
                    "method": "POST",
                    "workflowKey": str(workflow_key or "").strip(),
                    "workflowFile": str(workflow_file or "").strip(),
                    "transportMode": str(transport_mode or "").strip(),
                    "status": int(resp.status_code),
                    "success": bool(resp.status_code < 400),
                    "bodyPreview": body_snippet,
                },
            )
            if resp.status_code >= 400:
                if resp.status_code == 405:
                    _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED = False
                return None, f"audio_upload_non_200:status={resp.status_code}:body={body_snippet}"
            payload, parse_err = _parse_json_response(resp, stage="audio_upload_response")
            if parse_err or not payload:
                return None, parse_err or "audio_upload_response_invalid_json"

            name = str(payload.get("name") or payload.get("filename") or "").strip()
            if name:
                return name, None

            return None, f"audio_upload_name_missing:{str(payload)[:300]}"
        except ConnectTimeout as exc:
            last_error = f"audio_upload_connect_timeout:{str(exc)[:300]}"
        except ReadTimeout as exc:
            last_error = f"audio_upload_read_timeout:{str(exc)[:300]}"
        except RequestException as exc:
            return None, f"audio_upload_request_error:{str(exc)[:300]}"

        if attempt < max_attempts:
            time.sleep(min(8.0, 2.0 * attempt))

    return None, last_error


def submit_comfy_prompt(workflow: dict) -> tuple[str | None, str | None]:
    url = f"{str(settings.COMFY_BASE_URL).rstrip('/')}/prompt"
    connect_timeout = max(20, int(settings.COMFY_PROMPT_CONNECT_TIMEOUT_SEC or 20))
    read_timeout = max(120, int(settings.COMFY_PROMPT_READ_TIMEOUT_SEC or 120))
    logger.info(
        "[COMFY REMOTE] request prompt url=%s connect_timeout=%s read_timeout=%s",
        url,
        connect_timeout,
        read_timeout,
    )
    try:
        resp = requests.post(url, json={"prompt": workflow}, timeout=(connect_timeout, read_timeout))
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


def wait_for_comfy_result(prompt_id: str, timeout_sec: int, poll_interval_sec: int) -> tuple[dict | None, str | None]:
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


def _resolve_workflow_fps(workflow: dict, default_fps: int = 24) -> int:
    fps_node_id, fps_input_key = FIXED_IMAGE_VIDEO_NODES["fps"]
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
    return int(default_fps)


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


def _collect_audio_input_targets(
    workflow: dict,
    *,
    workflow_key: str = "",
    workflow_file: str = "",
) -> list[dict]:
    targets: list[dict] = []
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
        matched_by = "class_type" if class_type in COMFY_AUDIO_INPUT_NODE_CLASS_NAMES else ""
        if not matched_by and any(hint in title for hint in COMFY_AUDIO_TITLE_HINTS):
            matched_by = "title"
        if not matched_by and any(any(hint in str(key).lower() for hint in COMFY_AUDIO_TITLE_HINTS) for key in input_keys):
            matched_by = "input_key_hint"
        if not matched_by:
            continue
        found_target = False
        for input_key in COMFY_AUDIO_INPUT_KEYS:
            if input_key in inputs:
                targets.append(
                    {
                        "node_id": str(node_id),
                        "class_type": class_type,
                        "title": title,
                        "input_keys": input_keys,
                        "input_key": input_key,
                        "matched_by": matched_by,
                    }
                )
                found_target = True
                break
        if not found_target and class_type in COMFY_AUDIO_INPUT_NODE_CLASS_NAMES:
            for fallback_key in COMFY_AUDIO_INPUT_WIDGET_FALLBACK_KEYS:
                if fallback_key in inputs and isinstance(inputs.get(fallback_key), str):
                    targets.append(
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
        not targets
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
                targets.append(
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
    return targets


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


def _patch_workflow_inputs(
    workflow: dict,
    *,
    image_name: str,
    prompt: str,
    width: int,
    height: int,
    requested_duration_sec: float,
    seed: int | None,
) -> tuple[dict | None, str | None, int | None, int | None]:
    wf = copy.deepcopy(workflow)

    fps = _resolve_workflow_fps(wf)
    frames = max(1, int(math.ceil(float(requested_duration_sec) * float(fps))))
    print("[COMFY LENGTH APPLY]", {
        "requestedDurationSec": float(requested_duration_sec),
        "fps": int(fps),
        "frames": int(frames),
    })

    patch_values = [
        (*FIXED_IMAGE_VIDEO_NODES["image"], image_name),
        (*FIXED_IMAGE_VIDEO_NODES["prompt"], prompt),
        (*FIXED_IMAGE_VIDEO_NODES["width"], int(width)),
        (*FIXED_IMAGE_VIDEO_NODES["height"], int(height)),
        (*FIXED_IMAGE_VIDEO_NODES["length"], int(frames)),
    ]
    for node_id, key, value in patch_values:
        ok, err = _set_node_input(wf, node_id, key, value)
        if not ok:
            return None, err, None, None

    _patch_audio_frames(wf, frames)

    if seed is not None:
        for node_id, key in FIXED_SEED_NODES:
            ok, err = _set_node_input(wf, node_id, key, int(seed))
            if not ok:
                return None, err, None, None

    return wf, None, frames, fps


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

    patched_workflow, patch_err, frame_count, fps = _patch_workflow_inputs(
        workflow,
        image_name=uploaded_name,
        prompt=effective_prompt,
        width=int(width),
        height=int(height),
        requested_duration_sec=float(requested_duration_sec),
        seed=seed,
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
        audio_targets = _collect_audio_input_targets(
            patched_workflow,
            workflow_key=normalized_workflow_key,
            workflow_file=workflow_source,
        )
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
        normalized_audio_url_available = bool(normalized_audio_url) and is_normalized_audio_url_safe
        if normalized_audio_url_available and not supports_url_transport:
            supports_url_transport = True
        audio_transport_reason = "workflow_has_audio_input_nodes" if audio_targets else "workflow_has_no_patchable_audio_input_nodes"
        upload_fallback_allowed = bool(
            has_audio_bytes
            and not supports_url_transport
            and not is_normalized_audio_url_safe
            and _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED is not False
        )
        upload_guard_reason = "allowed"
        if not upload_fallback_allowed:
            if not has_audio_bytes:
                upload_guard_reason = "audio_bytes_missing"
            elif _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED is False:
                upload_guard_reason = "upload_endpoint_previously_unsupported"
            elif supports_url_transport:
                upload_guard_reason = "url_or_path_transport_supported"
            elif is_normalized_audio_url_safe:
                upload_guard_reason = "normalized_remote_safe_url_available"
        selected_by = "pending"
        selected_transport_reason = audio_transport_reason
        if bool(normalized_audio_url) and is_normalized_audio_url_safe and supports_url_transport:
            selected_by = "normalized_remote_safe_url+target_support"
            selected_transport_mode = "url"
            selected_transport_reason = "remote_safe_normalized_url_available"
        elif upload_fallback_allowed:
            selected_by = "audio_upload_fallback"
            selected_transport_mode = "upload"
            selected_transport_reason = "url_transport_unavailable_fallback_to_upload"
        elif bool(normalized_audio_url) and not is_normalized_audio_url_safe:
            selected_by = "normalized_url_rejected_unsafe_for_remote"
            selected_transport_mode = "none"
            selected_transport_reason = normalized_audio_url_safety_reason
        elif bool(normalized_audio_url) and not supports_url_transport:
            selected_by = "url_rejected_target_not_url_compatible"
            selected_transport_mode = "none"
            selected_transport_reason = "targets_not_url_compatible"
        elif has_audio_bytes and not upload_fallback_allowed:
            selected_by = f"upload_guard_blocked:{upload_guard_reason}"
            selected_transport_mode = "none"
            selected_transport_reason = upload_guard_reason
        else:
            selected_transport_mode = "none"
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
                "uploadFallbackAllowed": upload_fallback_allowed,
                "selectedTransportMode": selected_transport_mode,
                "selectedBy": selected_by,
                "reason": selected_transport_reason,
            },
        )
        logger.info(
            "[COMFY AUDIO TARGETS INSPECTION] %s",
            {
                "workflowKey": normalized_workflow_key,
                "workflowFile": workflow_source,
                "audioTargetsFound": len(audio_targets),
                "audioTargets": audio_targets,
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
                "uploadFallbackAllowed": upload_fallback_allowed,
                "reason": audio_transport_reason,
            },
        )
        if audio_targets:
            selected_by = "no_audio_payload"
            if bool(normalized_audio_url) and is_normalized_audio_url_safe and supports_url_transport:
                audio_transport_mode = "url"
                selected_by = "normalized_remote_safe_url+target_support"
            elif upload_fallback_allowed:
                audio_filename = f"{Path(image_filename).stem}_audio.mp3"
                uploaded_audio_name, audio_upload_err = upload_audio_to_comfy(
                    audio_bytes,
                    audio_filename,
                    workflow_key=normalized_workflow_key,
                    workflow_file=workflow_source,
                    transport_mode="upload",
                )
                if audio_upload_err or not uploaded_audio_name:
                    if "audio_upload_non_200:status=405" in str(audio_upload_err or ""):
                        _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED = False
                        logger.warning(
                            "[COMFY AUDIO UPLOAD GUARD] %s",
                            {
                                "workflowKey": normalized_workflow_key,
                                "workflowFile": workflow_source,
                                "uploadFallbackAllowed": False,
                                "reason": "upload_endpoint_unsupported_status_405",
                                "uploadBlockedBecauseEndpointUnsupported": True,
                            },
                        )
                        return None, "capability_error:LTX_AUDIO_UPLOAD_ENDPOINT_UNSUPPORTED:audio_upload_non_200:status=405"
                    return None, f"capability_error:LTX_AUDIO_UPLOAD_FAILED:{audio_upload_err or 'audio_upload_failed'}"
                effective_audio_value = str(uploaded_audio_name).strip()
                audio_transport_mode = "upload"
                selected_by = "audio_upload_fallback"
            elif bool(normalized_audio_url) and not is_normalized_audio_url_safe:
                audio_transport_mode = "none"
                selected_by = f"normalized_url_rejected_unsafe_for_remote:{normalized_audio_url_safety_reason}"
            elif bool(normalized_audio_url) and not supports_url_transport:
                audio_transport_mode = "none"
                selected_by = "url_rejected_target_not_url_compatible"
            elif has_audio_bytes and not upload_fallback_allowed:
                audio_transport_mode = "none"
                selected_by = f"upload_guard_blocked:{upload_guard_reason}"
            if (
                audio_transport_mode == "none"
                and _COMFY_AUDIO_UPLOAD_ENDPOINT_SUPPORTED is False
                and not is_normalized_audio_url_safe
            ):
                remote_url_reason = normalized_audio_url_safety_reason or "normalized_audio_url_unsafe"
                return None, f"capability_error:LTX_AUDIO_REMOTE_URL_UNAVAILABLE:{remote_url_reason}"
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
            if audio_transport_mode in {"url", "upload"}:
                audio_patch_node_ids, audio_patch_err = _patch_audio_input_nodes(
                    patched_workflow,
                    audio_value=effective_audio_value,
                    audio_targets=audio_targets,
                )
                if audio_patch_err:
                    return None, f"capability_error:LTX_AUDIO_WORKFLOW_PATCH_FAILED:{audio_patch_err}"
                lip_sync_proof_reason = "audio_patch_applied"
            else:
                return None, f"capability_error:LTX_AUDIO_TRANSPORT_UNAVAILABLE:{selected_by}"
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
            and audio_transport_mode in {"url", "upload"}
            and bool(str(effective_audio_value).strip())
        )
        if not lip_sync_proof_confirmed and not lip_sync_proof_reason:
            if not audio_used:
                lip_sync_proof_reason = "audio_patch_node_ids_empty"
            elif audio_targets_found <= 0:
                lip_sync_proof_reason = "audio_targets_not_found"
            elif audio_transport_mode not in {"url", "upload"}:
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
            and audio_transport_mode in {"url", "upload"}
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
                "realMouthSyncProven": lip_sync_proof_confirmed,
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

    logger.info(
        "[COMFY REMOTE] patched workflow values %s",
        {
            "uploaded_name": uploaded_name,
            "effective_prompt": effective_prompt,
            "image": patched_workflow.get("269", {}).get("inputs", {}).get("image"),
            "prompt": patched_workflow.get("267:266", {}).get("inputs", {}).get("value"),
            "width": patched_workflow.get("267:257", {}).get("inputs", {}).get("value"),
            "height": patched_workflow.get("267:258", {}).get("inputs", {}).get("value"),
            "fps": fps,
            "length": patched_workflow.get("267:225", {}).get("inputs", {}).get("value"),
            "audioFrames": patched_workflow.get("267:214", {}).get("inputs", {}).get("frames_number"),
        },
    )
    logger.info(
        "[COMFY REMOTE PROMPT TRANSPORT] scene_id=%s workflow_key=%s model_key=%s prompt_patched_node_ids=%s final_prompt_length=%s final_prompt_preview=%r",
        str(scene_id or "").strip(),
        normalized_workflow_key,
        normalized_model_key,
        FIXED_PROMPT_PATCH_NODE_IDS,
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

    poll_timeout_sec = max(10, int(settings.COMFY_POLL_TIMEOUT_SEC or 600))
    history, wait_err = wait_for_comfy_result(
        prompt_id,
        timeout_sec=poll_timeout_sec,
        poll_interval_sec=max(2, int(settings.COMFY_POLL_INTERVAL_SEC or 2)),
    )
    if wait_err or not history:
        logger.warning(
            "[COMFY REMOTE] history wait failed prompt_id=%s timeout_sec=%s jobId=%s err=%s history_present=%s",
            prompt_id,
            poll_timeout_sec,
            prompt_id,
            wait_err or 'unknown_wait_error',
            bool(history),
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
            "prompt_patched_node_ids": FIXED_PROMPT_PATCH_NODE_IDS,
            "first_last_start_node_ids": first_last_start_node_ids,
            "first_last_end_node_ids": first_last_end_node_ids,
            "start_image_used": bool(first_last_start_node_ids),
            "end_image_used": bool(first_last_end_node_ids),
            "second_frame_patch_applied": bool(first_last_applied),
            "audio_used": bool(audio_patch_node_ids),
            "audio_targets_found": audio_targets_found,
            "audio_targets_summary": audio_targets[:8],
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
                "image": FIXED_IMAGE_VIDEO_NODES["image"][0],
                "promptSource": FIXED_IMAGE_VIDEO_NODES["prompt"][0],
                "width": FIXED_IMAGE_VIDEO_NODES["width"][0],
                "height": FIXED_IMAGE_VIDEO_NODES["height"][0],
                "length": FIXED_IMAGE_VIDEO_NODES["length"][0],
                "fps": FIXED_IMAGE_VIDEO_NODES["fps"][0],
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
