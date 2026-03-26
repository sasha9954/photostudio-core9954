from __future__ import annotations

import copy
import logging
import json
import math
import time
from pathlib import Path
from urllib.parse import quote

import requests
from requests import ConnectTimeout, ReadTimeout, RequestException, Response

from app.core.config import settings

logger = logging.getLogger(__name__)

COMFY_LTX_CAPABILITIES = {
    "single_image": True,
    "first_last": True,
    "audio_sensitive": True,
    "lip_sync": False,
    "continuation": False,
}
COMFY_AUDIO_WORKFLOW_FILES = {
    "i2v_as": "image-video-golos-zvuk.json",
    "f_l_as": "imag-imag-video-zvuk.json",
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

COMFY_LTX_WORKFLOW_REQUIREMENTS = {
    "i2v": {"single_image": True, "first_last": False, "audio_sensitive": False, "lip_sync": False, "continuation": False},
    "i2v_as": {"single_image": True, "first_last": False, "audio_sensitive": True, "lip_sync": False, "continuation": False},
    "f_l": {"single_image": False, "first_last": True, "audio_sensitive": False, "lip_sync": False, "continuation": False},
    "f_l_as": {"single_image": False, "first_last": True, "audio_sensitive": True, "lip_sync": False, "continuation": False},
    "continuation": {"single_image": False, "first_last": False, "audio_sensitive": False, "lip_sync": False, "continuation": True},
    "lip_sync": {"single_image": True, "first_last": False, "audio_sensitive": True, "lip_sync": True, "continuation": False},
}
MODEL_KEY_TO_MODEL_SPEC = {
    "ltx23_dev_fp8": {
        "key": "ltx23_dev_fp8",
        "ckpt_name": "ltx-2.3-22b-dev-fp8.safetensors",
        "compatible_workflow_keys": {"i2v", "i2v_as", "f_l", "f_l_as"},
    },
    "ltx23_distilled_fp8": {
        "key": "ltx23_distilled_fp8",
        "ckpt_name": "ltx-2.3-22b-distilled-fp8.safetensors",
        "compatible_workflow_keys": {"i2v", "i2v_as", "f_l", "f_l_as"},
    },
    "ltx23_dev_fp16": {
        "key": "ltx23_dev_fp16",
        "ckpt_name": "ltx-2.3-22b-dev-fp16.safetensors",
        "compatible_workflow_keys": {"i2v", "i2v_as", "f_l", "f_l_as"},
    },
    "ltx23_distilled_fp16": {
        "key": "ltx23_distilled_fp16",
        "ckpt_name": "ltx-2.3-22b-distilled-fp16.safetensors",
        "compatible_workflow_keys": {"i2v", "i2v_as", "f_l", "f_l_as"},
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
    key = str(workflow_key or "i2v").strip().lower() or "i2v"
    requirements = COMFY_LTX_WORKFLOW_REQUIREMENTS.get(key)
    if not requirements:
        return "LTX_MODE_NOT_IMPLEMENTED", f"workflow_key_not_supported:{key}"

    if requirements["lip_sync"] and not COMFY_LTX_CAPABILITIES["lip_sync"]:
        return "LTX_LIPSYNC_NOT_IMPLEMENTED", "lip_sync_mode_not_implemented_for_comfy_remote"
    if requirements["first_last"] and not COMFY_LTX_CAPABILITIES["first_last"]:
        return "LTX_FIRST_LAST_NOT_IMPLEMENTED", "first_last_mode_not_implemented_for_comfy_remote"
    if requirements["audio_sensitive"] and not COMFY_LTX_CAPABILITIES["audio_sensitive"]:
        return "LTX_AUDIO_REACTIVE_NOT_IMPLEMENTED", "audio_sensitive_mode_not_implemented_for_comfy_remote"
    if requirements["single_image"] and not COMFY_LTX_CAPABILITIES["single_image"]:
        return "LTX_MODE_NOT_IMPLEMENTED", "single_image_mode_not_implemented_for_comfy_remote"

    if requirements["first_last"] and not (start_image_bytes and end_image_bytes):
        return "LTX_SECOND_FRAME_REQUIRED", "start_image_and_end_image_required_for_first_last_mode"
    if requirements["audio_sensitive"] and not (audio_bytes or str(audio_url or "").strip()):
        return "LTX_AUDIO_REQUIRED", "audio_input_required_for_audio_sensitive_mode"
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
                logger.warning(
                    "[COMFY REMOTE] history temporary non-200 prompt_id=%s status=%s body=%r",
                    safe_prompt_id,
                    resp.status_code,
                    body_snippet,
                )
                time.sleep(sleep_sec)
                continue
            payload, parse_err = _parse_json_response(resp, stage="history_response")
            if parse_err or not payload:
                logger.warning(
                    "[COMFY REMOTE] history temporary invalid response prompt_id=%s err=%s",
                    safe_prompt_id,
                    parse_err or "history_response_invalid_json",
                )
                time.sleep(sleep_sec)
                continue
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
            logger.warning(
                "[COMFY REMOTE] history connect timeout prompt_id=%s error=%s",
                safe_prompt_id,
                str(exc)[:200],
            )
            time.sleep(sleep_sec)
            continue

        except ReadTimeout as exc:
            logger.warning(
                "[COMFY REMOTE] history read timeout prompt_id=%s error=%s",
                safe_prompt_id,
                str(exc)[:200],
            )
            time.sleep(sleep_sec)
            continue

        except RequestException as exc:
            logger.warning(
                "[COMFY REMOTE] history request error prompt_id=%s error=%s",
                safe_prompt_id,
                str(exc)[:200],
            )
            time.sleep(sleep_sec)
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


def build_public_comfy_file_url(filename_or_subpath: str) -> str:
    raw = str(filename_or_subpath or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw

    base = str(settings.COMFY_BASE_URL).rstrip("/")
    if raw.startswith("/view?") or raw.startswith("view?"):
        return f"{base}/{raw.lstrip('/')}"

    clean = raw.lstrip("/")
    if clean.startswith("output/"):
        return f"{base}/{clean}"

    filename = clean.split("/")[-1]
    subfolder = "/".join(clean.split("/")[:-1]).strip("/")
    if subfolder:
        return f"{base}/view?filename={quote(filename)}&subfolder={quote(subfolder)}&type=output"
    return f"{base}/view?filename={quote(filename)}&type=output"


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
    return bool(start_node_ids and end_node_ids), start_node_ids, end_node_ids


def _validate_audio_workflow_file(*, workflow_key: str, workflow_source: str) -> tuple[bool, str | None]:
    expected_name = COMFY_AUDIO_WORKFLOW_FILES.get(str(workflow_key or "").strip().lower())
    actual_name = Path(str(workflow_source or "")).name
    if not expected_name:
        return False, f"unsupported_audio_workflow_key:{workflow_key}"
    if actual_name != expected_name:
        return False, f"workflow_file_mismatch:expected={expected_name};actual={actual_name}"
    return True, None


def _patch_audio_input_nodes(workflow: dict, *, audio_url: str) -> tuple[list[str], str | None]:
    safe_audio_url = str(audio_url or "").strip()
    if not safe_audio_url:
        return [], "audio_url_empty"
    patched_node_ids: list[str] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "").strip().lower()
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue
        if class_type not in COMFY_AUDIO_INPUT_NODE_CLASS_NAMES:
            continue
        applied = False
        for input_key in COMFY_AUDIO_INPUT_KEYS:
            if input_key in inputs:
                inputs[input_key] = safe_audio_url
                patched_node_ids.append(str(node_id))
                applied = True
                break
        if not applied:
            return [], f"audio_loader_input_missing:{node_id}:{class_type}"

    if not patched_node_ids:
        return [], "audio_loader_nodes_not_found"
    return patched_node_ids, None


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
    normalized_workflow_key = str(workflow_key or "i2v").strip().lower() or "i2v"
    normalized_model_key = str(model_key or "").strip().lower()
    effective_model_spec = model_spec if isinstance(model_spec, dict) else MODEL_KEY_TO_MODEL_SPEC.get(normalized_model_key)
    if not effective_model_spec:
        return None, f"capability_error:LTX_MODEL_NOT_FOUND:unknown_model_key:{normalized_model_key or 'empty'}"
    compatible_workflow_keys = set(effective_model_spec.get("compatible_workflow_keys") or set())
    if normalized_workflow_key != "continuation" and compatible_workflow_keys and normalized_workflow_key not in compatible_workflow_keys:
        return None, f"capability_error:LTX_MODEL_WORKFLOW_INCOMPATIBLE:model={normalized_model_key};workflow={normalized_workflow_key}"
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
    if start_image_bytes and normalized_workflow_key in {"f_l", "f_l_as"}:
        uploaded_start_name, start_upload_err = upload_image_to_comfy(start_image_bytes, f"{Path(image_filename).stem}_start.jpg")
        if start_upload_err or not uploaded_start_name:
            return None, f"upload_failed:{start_upload_err or 'start_image_upload_failed'}"
    if end_image_bytes and normalized_workflow_key in {"f_l", "f_l_as"}:
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
    patched_model_node_ids, model_patch_err = _apply_model_spec_to_workflow(
        patched_workflow,
        model_spec=effective_model_spec,
    )
    if model_patch_err:
        return None, f"workflow_model_patch_failed:{model_patch_err}"
    first_last_applied = False
    first_last_start_node_ids: list[str] = []
    first_last_end_node_ids: list[str] = []
    if normalized_workflow_key in {"f_l", "f_l_as"}:
        if not uploaded_end_name:
            return None, "capability_error:LTX_SECOND_FRAME_REQUIRED:end_image_missing_for_first_last_workflow"
        first_last_applied, first_last_start_node_ids, first_last_end_node_ids = _patch_first_last_images(
            patched_workflow,
            start_image_name=uploaded_start_name,
            end_image_name=uploaded_end_name,
        )
        if not first_last_applied:
            return None, "capability_error:LTX_FIRST_LAST_NOT_IMPLEMENTED:second_frame_patch_not_applied"
    audio_patch_node_ids: list[str] = []
    if normalized_workflow_key in {"i2v_as", "f_l_as"}:
        valid_audio_workflow, workflow_audio_err = _validate_audio_workflow_file(
            workflow_key=normalized_workflow_key,
            workflow_source=workflow_source,
        )
        if not valid_audio_workflow:
            return None, f"capability_error:LTX_AUDIO_WORKFLOW_PATCH_FAILED:{workflow_audio_err}"
        audio_patch_node_ids, audio_patch_err = _patch_audio_input_nodes(
            patched_workflow,
            audio_url=str(audio_url or "").strip(),
        )
        if audio_patch_err:
            if audio_patch_err == "audio_loader_nodes_not_found":
                return None, "capability_error:LTX_AUDIO_REACTIVE_NOT_IMPLEMENTED:audio_sensitive_mode_requested_but_audio_loader_nodes_not_found"
            return None, f"capability_error:LTX_AUDIO_WORKFLOW_PATCH_FAILED:{audio_patch_err}"
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

    prompt_id, submit_err = submit_comfy_prompt(patched_workflow)
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

    video_url = build_public_comfy_file_url(file_ref)
    if not video_url:
        return None, "video_url_empty"

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
            "model_key": normalized_model_key,
            "model_ckpt_applied": str(effective_model_spec.get("ckpt_name") or ""),
            "actual_mode": normalized_workflow_key,
            "patched_node_ids": list(dict.fromkeys([*patched_model_node_ids, *first_last_start_node_ids, *first_last_end_node_ids, *audio_patch_node_ids])),
            "first_last_start_node_ids": first_last_start_node_ids,
            "first_last_end_node_ids": first_last_end_node_ids,
            "start_image_used": bool(first_last_start_node_ids),
            "end_image_used": bool(first_last_end_node_ids),
            "audio_used": bool(audio_patch_node_ids),
            "continuation_used": continuation_used,
            "continuation_source_asset_type": continuation_asset_type,
            "continuation_source_asset_url_present": bool(str(continuation_source_asset_url or "").strip()),
            "audio_patch_node_ids": audio_patch_node_ids,
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
            "frames": frame_count,
            "fps": fps,
        },
    }, None
