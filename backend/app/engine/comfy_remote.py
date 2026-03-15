from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from urllib.parse import quote

import requests
from requests import RequestException

from app.core.config import settings


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


def upload_image_to_comfy(image_bytes: bytes, filename: str) -> tuple[str | None, str | None]:
    url = f"{str(settings.COMFY_BASE_URL).rstrip('/')}/upload/image"
    safe_name = str(filename or "source.jpg").strip() or "source.jpg"

    files = {
        "image": (safe_name, image_bytes, "application/octet-stream"),
    }
    data = {"type": "input", "overwrite": "true"}

    try:
        resp = requests.post(url, files=files, data=data, timeout=60)
        if resp.status_code >= 400:
            return None, f"upload_http_{resp.status_code}:{resp.text[:300]}"
        payload = resp.json()
    except RequestException as exc:
        return None, f"upload_request_error:{str(exc)[:300]}"
    except Exception as exc:
        return None, f"upload_parse_error:{str(exc)[:300]}"

    if isinstance(payload, dict):
        name = str(payload.get("name") or payload.get("filename") or "").strip()
        if name:
            return name, None

    return None, f"upload_name_missing:{str(payload)[:300]}"


def submit_comfy_prompt(workflow: dict) -> tuple[str | None, str | None]:
    url = f"{str(settings.COMFY_BASE_URL).rstrip('/')}/prompt"
    try:
        resp = requests.post(url, json={"prompt": workflow}, timeout=60)
        if resp.status_code >= 400:
            return None, f"prompt_http_{resp.status_code}:{resp.text[:300]}"
        payload = resp.json()
    except RequestException as exc:
        return None, f"prompt_request_error:{str(exc)[:300]}"
    except Exception as exc:
        return None, f"prompt_parse_error:{str(exc)[:300]}"

    prompt_id = ""
    if isinstance(payload, dict):
        prompt_id = str(payload.get("prompt_id") or "").strip()
    if not prompt_id:
        return None, f"prompt_id_missing:{str(payload)[:300]}"
    return prompt_id, None


def wait_for_comfy_result(prompt_id: str, timeout_sec: int, poll_interval_sec: int) -> tuple[dict | None, str | None]:
    safe_prompt_id = str(prompt_id or "").strip()
    if not safe_prompt_id:
        return None, "prompt_id_empty"

    deadline = time.time() + max(5, int(timeout_sec or 0))
    sleep_sec = max(1, int(poll_interval_sec or 1))

    while time.time() < deadline:
        url = f"{str(settings.COMFY_BASE_URL).rstrip('/')}/history/{safe_prompt_id}"
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code >= 400:
                return None, f"history_http_{resp.status_code}:{resp.text[:300]}"
            payload = resp.json()
        except RequestException as exc:
            return None, f"history_request_error:{str(exc)[:300]}"
        except Exception as exc:
            return None, f"history_parse_error:{str(exc)[:300]}"

        if isinstance(payload, dict) and isinstance(payload.get(safe_prompt_id), dict):
            return payload, None
        time.sleep(sleep_sec)

    return None, "timeout"


def extract_video_result(history_payload: dict) -> tuple[str | None, str | None]:
    if not isinstance(history_payload, dict):
        return None, "history_not_dict"

    for _, entry in history_payload.items():
        if not isinstance(entry, dict):
            continue
        outputs = entry.get("outputs")
        if not isinstance(outputs, dict):
            continue

        for _, node_output in outputs.items():
            if not isinstance(node_output, dict):
                continue

            videos = node_output.get("videos")
            if isinstance(videos, list) and videos:
                first = videos[0]
                if isinstance(first, dict):
                    filename = str(first.get("filename") or "").strip()
                    subfolder = str(first.get("subfolder") or "").strip()
                    if filename and subfolder:
                        return f"{subfolder}/{filename}", None
                    if filename:
                        return filename, None
                if isinstance(first, str) and first.strip():
                    return first.strip(), None

            for key in ("filename", "video", "url", "video_url"):
                candidate = node_output.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip(), None

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
}

# These seed node ids are intentionally pinned to image-video-silent-directprompt.json.
FIXED_SEED_NODES = (("267:216", "noise_seed"), ("267:237", "noise_seed"))


def _patch_workflow_inputs(workflow: dict, *, image_name: str, prompt: str, width: int, height: int, length: int, seed: int | None) -> tuple[dict | None, str | None]:
    wf = copy.deepcopy(workflow)

    patch_values = [
        (*FIXED_IMAGE_VIDEO_NODES["image"], image_name),
        (*FIXED_IMAGE_VIDEO_NODES["prompt"], prompt),
        (*FIXED_IMAGE_VIDEO_NODES["width"], int(width)),
        (*FIXED_IMAGE_VIDEO_NODES["height"], int(height)),
        (*FIXED_IMAGE_VIDEO_NODES["length"], int(length)),
    ]
    for node_id, key, value in patch_values:
        ok, err = _set_node_input(wf, node_id, key, value)
        if not ok:
            return None, err

    if seed is not None:
        for node_id, key in FIXED_SEED_NODES:
            ok, err = _set_node_input(wf, node_id, key, int(seed))
            if not ok:
                return None, err

    return wf, None


def run_comfy_image_to_video(
    *,
    image_bytes: bytes,
    image_filename: str,
    prompt: str,
    width: int,
    height: int,
    length: int,
    seed: int | None = None,
) -> tuple[dict | None, str | None]:
    try:
        workflow = load_workflow_json(str(settings.COMFY_IMAGE_VIDEO_WORKFLOW or ""))
    except Exception as exc:
        return None, f"workflow_load_failed:{str(exc)[:300]}"

    uploaded_name, upload_err = upload_image_to_comfy(image_bytes, image_filename)
    if upload_err or not uploaded_name:
        return None, f"upload_failed:{upload_err or 'unknown_upload_error'}"

    patched_workflow, patch_err = _patch_workflow_inputs(
        workflow,
        image_name=uploaded_name,
        prompt=str(prompt or "").strip(),
        width=int(width),
        height=int(height),
        length=int(length),
        seed=seed,
    )
    if patch_err or not patched_workflow:
        return None, f"workflow_patch_failed:{patch_err or 'unknown_patch_error'}"

    prompt_id, submit_err = submit_comfy_prompt(patched_workflow)
    if submit_err or not prompt_id:
        return None, f"prompt_submit_failed:{submit_err or 'unknown_submit_error'}"

    history, wait_err = wait_for_comfy_result(
        prompt_id,
        timeout_sec=max(10, int(settings.COMFY_POLL_TIMEOUT_SEC or 600)),
        poll_interval_sec=max(1, int(settings.COMFY_POLL_INTERVAL_SEC or 2)),
    )
    if wait_err or not history:
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
        "requestedDurationSec": round(float(length) / 24.0, 3),
        "taskId": prompt_id,
        "debug": {
            "workflow": str(settings.COMFY_IMAGE_VIDEO_WORKFLOW or ""),
            "usedNodeIds": {
                "image": FIXED_IMAGE_VIDEO_NODES["image"][0],
                "promptSource": FIXED_IMAGE_VIDEO_NODES["prompt"][0],
                "width": FIXED_IMAGE_VIDEO_NODES["width"][0],
                "height": FIXED_IMAGE_VIDEO_NODES["height"][0],
                "length": FIXED_IMAGE_VIDEO_NODES["length"][0],
                "noiseSeed": [node_id for node_id, _ in FIXED_SEED_NODES],
            },
            "uploadedImage": uploaded_name,
            "fileRef": file_ref,
        },
    }, None
