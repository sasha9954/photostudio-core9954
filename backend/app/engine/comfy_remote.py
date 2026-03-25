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


def run_comfy_image_to_video(
    *,
    image_bytes: bytes,
    image_filename: str,
    prompt: str,
    width: int,
    height: int,
    requested_duration_sec: float,
    workflow_path: str | None = None,
    seed: int | None = None,
) -> tuple[dict | None, str | None]:
    logger.info(
        "[COMFY REMOTE] enter run_comfy_image_to_video filename=%s size_bytes=%s width=%s height=%s requested_duration_sec=%s seed=%s",
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
        "mode": "single",
        "videoUrl": video_url,
        "model": "ltx-2.3",
        "requestedDurationSec": round(float(requested_duration_sec), 3),
        "taskId": prompt_id,
        "debug": {
            "workflow": workflow_source,
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
