from __future__ import annotations

import base64
import hashlib
import itertools
import json
import logging
import math
import mimetypes
import os
import re
import socket
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import settings
from app.core.static_paths import ASSETS_DIR
from app.engine.audio_analyzer import analyze_audio
from app.engine.audio_scene_segmenter import build_gemini_audio_segmentation
from app.engine.scenario_audio_map_v11 import validate_audio_map_v11
from app.engine.gemini_rest import post_generate_content, resolve_gemini_api_key
from app.engine.scenario_role_planner import ROLE_PLAN_PROMPT_VERSION, build_gemini_role_plan
from app.engine.scenario_scene_planner import SCENE_PLAN_PROMPT_VERSION, build_gemini_scene_plan
from app.engine.scenario_scene_prompter import SCENE_PROMPTS_PROMPT_VERSION, build_gemini_scene_prompts
from app.engine.scenario_video_prompt_writer import (
    FINAL_VIDEO_PROMPT_DELIVERY_VERSION,
    FINAL_VIDEO_PROMPT_STAGE_VERSION,
    IA2V_MINIMAL_NEGATIVE_PROMPT,
    generate_ltx_video_prompt_metadata,
)
from app.engine.scenario_story_guidance import story_guidance_to_notes_list
from app.engine.scenario_stage_timeout_policy import (
    get_scenario_stage_timeout,
    is_timeout_error,
    scenario_timeout_policy_name,
)
from app.engine.video_capability_canon import (
    DEFAULT_VIDEO_MODEL_ID,
    build_capability_diagnostics_summary,
    get_capability_rules_source_version,
    get_video_model_capability_profile,
)

logger = logging.getLogger(__name__)

MAX_STORY_CORE_IMAGE_BYTES = 8 * 1024 * 1024
CORE_SCHEMA_INVALID = "CORE_SCHEMA_INVALID"
CORE_ID_MISMATCH = "CORE_ID_MISMATCH"
CORE_TIMING_DRIFT = "CORE_TIMING_DRIFT"
CORE_ROLE_SPAWNING = "CORE_ROLE_SPAWNING"
CORE_TECHNICAL_SPAWNING = "CORE_TECHNICAL_SPAWNING"
CORE_IDENTITY_CONFLICT = "CORE_IDENTITY_CONFLICT"
CORE_ROLE_BINDING_CONTRADICTION = "CORE_ROLE_BINDING_CONTRADICTION"
CORE_QUALITY_GATES_FAILED = "CORE_QUALITY_GATES_FAILED"
STORY_CORE_EMPTY_RESULT = "STORY_CORE_EMPTY_RESULT"
STORY_CORE_SCHEMA_INVALID = "STORY_CORE_SCHEMA_INVALID"
_FEMALE_CODED_TERMS = ("woman", "women", "female", "feminine", "girl", "lady", "her", "she", "heroine")
_MALE_CODED_TERMS = ("man", "men", "male", "masculine", "boy", "gentleman", "his", "he", "hero")


def _resolve_stage_gemini_api_key(
    package: dict[str, Any],
    *,
    stage_id: str,
) -> str:
    diagnostics = _safe_dict(package.get("diagnostics"))
    key_resolution = resolve_gemini_api_key()
    diagnostics["gemini_api_key_source"] = str(key_resolution.get("source") or "missing")
    diagnostics["gemini_api_key_valid"] = bool(key_resolution.get("valid"))
    diagnostics["gemini_api_key_error"] = str(key_resolution.get("error") or "")
    package["diagnostics"] = diagnostics
    if not bool(key_resolution.get("valid")):
        _append_diag_event(
            package,
            f"{stage_id} invalid gemini key: {diagnostics['gemini_api_key_error'] or 'empty'}",
            stage_id=stage_id,
        )
        raise RuntimeError(f"GEMINI_API_KEY_INVALID:{diagnostics['gemini_api_key_error'] or 'empty'}")
    return str(key_resolution.get("api_key") or "").strip()

STAGE_IDS = (
    "input_package",
    "audio_map",
    "story_core",
    "role_plan",
    "scene_plan",
    "scene_prompts",
    "final_video_prompt",
    "finalize",
)

STAGE_DEPENDENCIES: dict[str, list[str]] = {
    "input_package": [],
    "audio_map": ["input_package"],
    "story_core": ["input_package", "audio_map"],
    "role_plan": ["input_package", "audio_map", "story_core"],
    "scene_plan": ["input_package", "audio_map", "story_core", "role_plan"],
    "scene_prompts": ["input_package", "audio_map", "story_core", "role_plan", "scene_plan"],
    "final_video_prompt": ["input_package", "audio_map", "story_core", "role_plan", "scene_plan", "scene_prompts"],
    "finalize": ["input_package", "audio_map", "story_core", "role_plan", "scene_plan", "scene_prompts", "final_video_prompt"],
}

DOWNSTREAM_BY_STAGE: dict[str, list[str]] = {
    stage_id: [candidate for candidate, deps in STAGE_DEPENDENCIES.items() if stage_id in deps]
    for stage_id in STAGE_IDS
}

MANUAL_RESET_DOWNSTREAM: dict[str, list[str]] = {
    "audio_map": ["story_core", "role_plan", "scene_plan", "scene_prompts", "final_video_prompt", "finalize"],
    "story_core": ["role_plan", "scene_plan", "scene_prompts", "final_video_prompt", "finalize"],
    "role_plan": ["scene_plan", "scene_prompts", "final_video_prompt", "finalize"],
    "scene_plan": ["scene_prompts", "final_video_prompt", "finalize"],
    "scene_prompts": ["final_video_prompt", "finalize"],
    "final_video_prompt": ["finalize"],
    "finalize": [],
}

STAGE_SECTION_RESETTERS: dict[str, Any] = {
    "story_core": lambda: {},
    "audio_map": lambda: {},
    "role_plan": lambda: {},
    "scene_plan": lambda: {"scenes": []},
    "scene_prompts": lambda: {"scenes": []},
    "final_video_prompt": lambda: {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
    "finalize": lambda: {"final_storyboard_version": "1.1", "render_manifest": [], "scenes": []},
}

STAGE_DIAGNOSTIC_PREFIXES: dict[str, tuple[str, ...]] = {
    "story_core": ("story_core_",),
    "audio_map": ("audio_", "transcript_"),
    "role_plan": ("role_plan_",),
    "scene_plan": ("scene_plan_",),
    "scene_prompts": ("scene_prompts_",),
    "final_video_prompt": ("final_video_prompt_",),
    "finalize": ("finalize_",),
}
STAGE_PACKAGE_FIELD_BY_STAGE: dict[str, str] = {
    "input_package": "input",
    "audio_map": "audio_map",
    "story_core": "story_core",
    "role_plan": "role_plan",
    "scene_plan": "scene_plan",
    "scene_prompts": "scene_prompts",
    "final_video_prompt": "final_video_prompt",
    "finalize": "final_storyboard",
}
_OWNERSHIP_ROLE_MAP = {
    "main": "character_1",
    "support": "character_2",
    "antagonist": "character_3",
    "shared": "shared",
    "world": "environment",
}
_BINDING_TYPES = {"carried", "worn", "held", "pocketed", "nearby", "environment"}
_SUBJECT_REF_TOKENS = {"char", "character", "person", "subject", "talent", "hero", "protagonist", "human", "face"}
_OBJECT_REF_TOKENS = {"prop", "object", "item", "wardrobe", "vehicle", "accessory", "outfit", "tool", "bag", "phone"}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stable_hash_payload(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        raw = str(value or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _compute_input_signatures(package: dict[str, Any]) -> dict[str, str]:
    pkg = _safe_dict(package)
    input_pkg = _safe_dict(pkg.get("input"))
    refs_inventory = _safe_dict(pkg.get("refs_inventory"))
    text_payload = {
        "text": str(input_pkg.get("text") or "").strip(),
        "story_text": str(input_pkg.get("story_text") or "").strip(),
        "note": str(input_pkg.get("note") or "").strip(),
        "director_note": str(input_pkg.get("director_note") or "").strip(),
    }
    audio_payload = {
        "audio_url": str(input_pkg.get("audio_url") or "").strip(),
    }
    creative_payload = _safe_dict(input_pkg.get("creative_config"))
    refs_payload = {
        "refs_inventory": refs_inventory,
        "refs_by_role": _safe_dict(input_pkg.get("refs_by_role")),
        "selected_refs": _safe_dict(input_pkg.get("selected_refs")),
        "assigned_roles": _safe_dict(pkg.get("assigned_roles")),
    }
    input_text_signature = _stable_hash_payload(text_payload)
    audio_url_signature = _stable_hash_payload(audio_payload)
    refs_signature = _stable_hash_payload(refs_payload)
    creative_config_signature = _stable_hash_payload(creative_payload)
    scenario_input_signature = _stable_hash_payload(
        {
            "text": text_payload,
            "audio": audio_payload,
            "refs": refs_payload,
            "creative_config": creative_payload,
        }
    )
    return {
        "input_text_signature": input_text_signature,
        "audio_url_signature": audio_url_signature,
        "refs_signature": refs_signature,
        "creative_config_signature": creative_config_signature,
        "scenario_input_signature": scenario_input_signature,
    }


def _current_scenario_input_signature(package: dict[str, Any]) -> str:
    diagnostics = _safe_dict(_safe_dict(package).get("diagnostics"))
    signature = str(diagnostics.get("scenario_input_signature") or "").strip()
    if signature:
        return signature
    return str(_compute_input_signatures(package).get("scenario_input_signature") or "").strip()


def _payload_key_for_stage(stage_id: str) -> str:
    return "final_storyboard" if stage_id == "finalize" else stage_id


def _clear_downstream_stage_outputs(package: dict[str, Any], from_stage: str, reason: str) -> dict[str, Any]:
    pkg = deepcopy(_safe_dict(package))
    if from_stage not in STAGE_IDS:
        return pkg
    from_idx = STAGE_IDS.index(from_stage)
    downstream = [stage_id for stage_id in STAGE_IDS[from_idx + 1 :]]
    statuses = _safe_dict(pkg.get("stage_statuses"))
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    cleared_payloads: list[str] = []
    for stage_id in downstream:
        payload_key = _payload_key_for_stage(stage_id)
        resetter = STAGE_SECTION_RESETTERS.get(stage_id)
        if resetter:
            pkg[payload_key] = resetter()
            cleared_payloads.append(payload_key)
        stage_state = _safe_dict(statuses.get(stage_id))
        stage_state["status"] = "stale"
        stage_state["updated_at"] = _utc_iso()
        stage_state["error"] = ""
        stage_state["stale_reason"] = str(reason or "")
        statuses[stage_id] = stage_state
        diagnostics = _clear_stage_diagnostics(diagnostics, stage_id)

    # Hard cleanup for finalize compatibility fields / stale payload mirrors.
    if from_stage in {"input_package", "audio_map", "story_core", "role_plan", "scene_plan", "scene_prompts", "final_video_prompt"}:
        pkg["final_storyboard"] = STAGE_SECTION_RESETTERS["finalize"]()
        if "final_storyboard" not in cleared_payloads:
            cleared_payloads.append("final_storyboard")
        final_payload = _safe_dict(pkg.get("final_storyboard"))
        final_payload["render_manifest"] = []
        final_payload["scenes"] = []
        pkg["final_storyboard"] = final_payload
        for key in ("storyboard", "scenes", "render_manifest"):
            if key not in pkg:
                continue
            if key == "render_manifest":
                pkg[key] = []
            elif key == "scenes":
                pkg[key] = []
            else:
                pkg[key] = {}

    if from_stage in {"input_package", "audio_map", "story_core", "role_plan", "scene_plan", "scene_prompts"}:
        pkg["final_video_prompt"] = STAGE_SECTION_RESETTERS["final_video_prompt"]()
        if "final_video_prompt" not in cleared_payloads:
            cleared_payloads.append("final_video_prompt")

    if "render_manifest" not in cleared_payloads:
        cleared_payloads.append("render_manifest")

    previous_signature = str(diagnostics.get("scenario_input_signature") or "")
    signatures = _compute_input_signatures(pkg)
    current_signature = str(signatures.get("scenario_input_signature") or "")
    diagnostics.update(signatures)
    diagnostics["stale_reason"] = str(reason or f"rerun:{from_stage}")
    diagnostics["downstream_clear"] = {
        "trigger_stage": from_stage,
        "reason": str(reason or ""),
        "cleared_stages": downstream,
        "cleared_payloads": cleared_payloads,
        "previous_signature": previous_signature,
        "current_signature": current_signature,
        "input_signature_changed": bool(previous_signature and current_signature and previous_signature != current_signature),
    }
    pkg["diagnostics"] = diagnostics
    pkg["stage_statuses"] = statuses
    pkg["updated_at"] = _utc_iso()
    return pkg


def _strip_literal_quoted_dialogue(text: str) -> str:
    raw = str(text or "")
    raw = re.sub(
        r'(?i)mouth\s+moving\s+in\s+sync\s+with\s+the\s+words\s*[\'"]([^\'"]){1,220}[\'"]',
        "mouth moving in sync with the provided audio phrase",
        raw,
    )
    raw = re.sub(
        r'(?i)mouth\s+moving\s+in\s+sync\s+with\s*[\'"]([^\'"]){1,220}[\'"]',
        "mouth moving in sync with the provided audio phrase",
        raw,
    )
    raw = re.sub(
        r'(?i)mouth\s+moving\s+in\s+sync\s+with(?:\s+the\s+phrase)?\s*[\'"]([^\'"]){1,220}[\'"]',
        "mouth moving in sync with the provided audio phrase",
        raw,
    )
    raw = re.sub(r'["\'][^"\']{2,180}["\']', " ", raw)
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _stage_output_field(stage_id: str) -> str:
    return STAGE_PACKAGE_FIELD_BY_STAGE.get(stage_id, stage_id)


def _has_non_empty_collection(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value)
    if isinstance(value, list):
        return bool(value)
    return False


def _has_valid_story_core_payload(output: Any) -> bool:
    payload = _safe_dict(output)
    return bool(str(payload.get("core_version") or "").strip()) and bool(_safe_list(payload.get("narrative_segments")))


def _validate_story_core_result(output: Any) -> tuple[bool, str, list[str]]:
    if output is None:
        return False, STORY_CORE_EMPTY_RESULT, ["story_core_missing"]
    if not isinstance(output, dict):
        return False, STORY_CORE_EMPTY_RESULT, ["story_core_not_dict"]
    story_core = _safe_dict(output)
    if not story_core:
        return False, STORY_CORE_EMPTY_RESULT, ["story_core_empty_object"]

    errors: list[str] = []
    for field_name in ("story_summary", "opening_anchor", "ending_callback_rule"):
        if not str(story_core.get(field_name) or "").strip():
            errors.append(f"missing_or_empty:{field_name}")

    identity_doctrine = _safe_dict(story_core.get("identity_doctrine"))
    identity_lock = _safe_dict(story_core.get("identity_lock"))
    has_identity = bool(identity_doctrine) or bool(identity_lock)
    if not has_identity:
        errors.append("missing_identity_doctrine_or_identity_lock")

    world_lock = _safe_dict(story_core.get("world_lock"))
    world_doctrine = str(story_core.get("world_doctrine") or identity_doctrine.get("world_doctrine") or "").strip()
    if not (world_doctrine or bool(world_lock)):
        errors.append("missing_world_doctrine_or_world_lock")

    style_lock = _safe_dict(story_core.get("style_lock"))
    style_doctrine = str(story_core.get("style_doctrine") or identity_doctrine.get("style_doctrine") or "").strip()
    if not (style_doctrine or bool(style_lock)):
        errors.append("missing_style_doctrine_or_style_lock")

    if not _safe_list(story_core.get("narrative_segments")):
        errors.append("narrative_segments_missing_or_empty")

    if errors:
        return False, STORY_CORE_SCHEMA_INVALID, errors
    return True, "", []


def _has_valid_role_plan_payload(output: Any) -> bool:
    payload = _safe_dict(output)
    roles_version = str(payload.get("roles_version") or "").strip()
    roster = _safe_list(payload.get("roster"))
    scene_casting = _safe_list(payload.get("scene_casting"))
    return bool(roles_version) and bool(roster) and bool(scene_casting)


def _has_valid_scene_plan_payload(output: Any) -> bool:
    payload = _safe_dict(output)
    if bool(_safe_list(payload.get("segments"))):
        return True
    if bool(_safe_list(payload.get("scenes"))):
        return True
    storyboard = payload.get("storyboard")
    return _has_non_empty_collection(storyboard)


def _has_valid_scene_plan_payload_for_scene_prompts(package: dict[str, Any]) -> bool:
    scene_plan = _safe_dict(_safe_dict(package).get("scene_plan"))
    rows = _safe_list(scene_plan.get("storyboard") or scene_plan.get("scenes"))
    if not rows:
        return False

    seen: list[str] = []
    for row in rows:
        item = _safe_dict(row)
        segment_id = str(item.get("segment_id") or item.get("scene_id") or "").strip()
        route = str(item.get("route") or "").strip().lower()
        if not segment_id:
            return False
        if route not in {"i2v", "ia2v"}:
            return False
        seen.append(segment_id)

    return len(seen) == len(set(seen))


def _has_valid_scene_prompts_payload(output: Any) -> bool:
    payload = _safe_dict(output)
    prompts_version = str(payload.get("prompts_version") or "").strip()
    segments = _safe_list(payload.get("segments"))
    return prompts_version == "1.1" and bool(segments)


def _scene_prompts_result_has_no_blocking_errors(result: dict[str, Any]) -> bool:
    diagnostics = _safe_dict(result.get("diagnostics"))
    prompts = _safe_dict(result.get("scene_prompts"))
    segments = _safe_list(prompts.get("segments")) or _safe_list(prompts.get("scenes"))
    missing_photo_count = int(diagnostics.get("scene_prompts_missing_photo_count") or diagnostics.get("missing_photo_count") or 0)
    missing_video_count = int(diagnostics.get("scene_prompts_missing_video_count") or diagnostics.get("missing_video_count") or 0)
    response_empty_after_timeout = bool(
        diagnostics.get("scene_prompts_response_was_empty_after_timeout") or diagnostics.get("response_was_empty_after_timeout")
    )
    return (
        bool(segments)
        and not str(result.get("validation_error") or "").strip()
        and not str(result.get("error_code") or "").strip()
        and not str(result.get("error") or "").strip()
        and not str(diagnostics.get("scene_prompts_validation_error") or "").strip()
        and not str(diagnostics.get("scene_prompts_error_code") or "").strip()
        and not str(diagnostics.get("scene_prompts_error") or "").strip()
        and not str(diagnostics.get("scene_prompts_technical_tagging_token") or "").strip()
        and missing_photo_count == 0
        and missing_video_count == 0
        and not response_empty_after_timeout
    )


def _has_stage_output(package: dict[str, Any], stage_id: str) -> bool:
    safe_pkg = _safe_dict(package)
    output = safe_pkg.get(_stage_output_field(stage_id))
    if stage_id == "input_package":
        return bool(_safe_dict(output))
    if stage_id == "audio_map":
        return _is_usable_audio_map(_safe_dict(output))
    if stage_id == "story_core":
        return _has_valid_story_core_payload(output)
    if stage_id == "role_plan":
        return _has_valid_role_plan_payload(output)
    if stage_id == "scene_plan":
        return _has_valid_scene_plan_payload(output)
    if stage_id == "scene_prompts":
        return _has_valid_scene_prompts_payload(output)
    if stage_id == "final_video_prompt":
        payload = _safe_dict(output)
        return bool(_safe_list(payload.get("segments"))) or bool(_safe_list(payload.get("scenes")))
    if stage_id == "finalize":
        return isinstance(output, dict) and bool(_safe_list(_safe_dict(output).get("render_manifest")))
    return isinstance(output, dict) and bool(output)


def _can_reuse_stage_output(package: dict[str, Any], stage_id: str) -> bool:
    statuses = _safe_dict(_safe_dict(package).get("stage_statuses"))
    status = str(_safe_dict(statuses.get(stage_id)).get("status") or "").strip().lower()
    if not (status == "done" and _has_stage_output(package, stage_id)):
        return False
    if stage_id in {"scene_prompts", "final_video_prompt", "finalize"}:
        current_signature = _current_scenario_input_signature(package)
        payload = _safe_dict(_safe_dict(package).get(_payload_key_for_stage(stage_id)))
        payload_signature = str(payload.get("created_for_signature") or "").strip()
        if current_signature and payload_signature and payload_signature != current_signature:
            return False
    return True


def _stage_is_marked_stale_or_invalid(package: dict[str, Any], stage_id: str) -> bool:
    stage_state = _safe_dict(_safe_dict(package).get("stage_statuses")).get(stage_id)
    row = _safe_dict(stage_state)
    status = str(row.get("status") or "").strip().lower()
    if status == "stale":
        return True
    for key in (
        "invalidated",
        "invalid",
        "dirty",
        "stale",
        "staleReason",
        "stale_reason",
        "invalidateReason",
        "invalidatedReason",
    ):
        value = row.get(key)
        if isinstance(value, bool) and value:
            return True
        if isinstance(value, str) and value.strip():
            return True
    return False


def _can_run_scene_prompts_from_existing_scene_plan(package: dict[str, Any]) -> bool:
    pkg = _safe_dict(package)
    scene_plan_ready = _can_reuse_stage_output(pkg, "scene_plan")
    if not scene_plan_ready or _stage_is_marked_stale_or_invalid(pkg, "scene_plan"):
        return False
    required_upstream = ("input_package", "audio_map", "story_core", "role_plan")
    if not all(_has_stage_output(pkg, stage) for stage in required_upstream):
        return False
    return True


def _scene_plan_payload_supports_scene_prompts(package: dict[str, Any]) -> bool:
    ok, _ = _scene_plan_payload_supports_scene_prompts_with_reason(package)
    return ok


def _scene_prompts_dependency_payload_ok(package: dict[str, Any], dependency_stage_id: str) -> bool:
    pkg = _safe_dict(package)
    if dependency_stage_id == "input_package":
        return bool(_safe_dict(pkg.get("input")))
    if dependency_stage_id == "audio_map":
        return _has_valid_audio_map_payload_for_scene_prompts(pkg)
    if dependency_stage_id == "story_core":
        return _has_valid_story_core_payload(_safe_dict(pkg.get("story_core")))
    if dependency_stage_id == "role_plan":
        return _has_valid_role_plan_payload(_safe_dict(pkg.get("role_plan")))
    if dependency_stage_id == "scene_plan":
        return _has_valid_scene_plan_payload_for_scene_prompts(pkg)
    return _can_reuse_stage_output(pkg, dependency_stage_id)


def _collect_scene_prompts_dependency_gate_state(
    package: dict[str, Any], dependencies: list[str]
) -> tuple[dict[str, bool], dict[str, str], bool]:
    pkg = _safe_dict(package)
    statuses = _safe_dict(pkg.get("stage_statuses"))
    payload_ok_by_stage: dict[str, bool] = {}
    status_by_stage: dict[str, str] = {}
    false_positive_prevented = False
    for dep_stage in dependencies:
        payload_ok = _scene_prompts_dependency_payload_ok(pkg, dep_stage)
        payload_ok_by_stage[dep_stage] = payload_ok
        dep_status = str(_safe_dict(statuses.get(dep_stage)).get("status") or "").strip().lower()
        status_by_stage[dep_stage] = dep_status
        if payload_ok and _stage_is_marked_stale_or_invalid(pkg, dep_stage):
            false_positive_prevented = True
    return payload_ok_by_stage, status_by_stage, false_positive_prevented


def _can_run_scene_prompts_from_existing_payload(
    package: dict[str, Any], dependencies: list[str] | None = None
) -> bool:
    deps = dependencies or resolve_stage_sequence(["scene_prompts"], include_dependencies=True)[:-1]
    payload_ok_by_stage, _, _ = _collect_scene_prompts_dependency_gate_state(package, deps)
    return all(bool(payload_ok_by_stage.get(dep_stage)) for dep_stage in deps)


def _role_plan_covers_audio_segments(package: dict[str, Any]) -> bool:
    pkg = _safe_dict(package)
    role_plan = _safe_dict(pkg.get("role_plan"))
    audio_segments = [row for row in _safe_list(_safe_dict(pkg.get("audio_map")).get("segments")) if isinstance(row, dict)]
    expected_segment_ids = [str(_safe_dict(row).get("segment_id") or "").strip() for row in audio_segments]
    expected_segment_ids = [segment_id for segment_id in expected_segment_ids if segment_id]
    if not expected_segment_ids:
        return False
    scene_casting_rows = [row for row in _safe_list(role_plan.get("scene_casting")) if isinstance(row, dict)]
    seen_segment_ids = [str(_safe_dict(row).get("segment_id") or "").strip() for row in scene_casting_rows]
    seen_segment_ids = [segment_id for segment_id in seen_segment_ids if segment_id]
    if len(seen_segment_ids) != len(expected_segment_ids):
        return False
    return set(seen_segment_ids) == set(expected_segment_ids)


def _is_role_plan_signature_compatible(package: dict[str, Any]) -> bool:
    pkg = _safe_dict(package)
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    role_plan = _safe_dict(pkg.get("role_plan"))
    current_signature = _current_scenario_input_signature(pkg)
    payload_signature = str(role_plan.get("created_for_signature") or diagnostics.get("role_plan_created_for_signature") or "").strip()
    if current_signature and payload_signature and payload_signature != current_signature:
        return False
    return True


def _scene_plan_dependency_payload_ok(package: dict[str, Any], dependency_stage_id: str) -> bool:
    pkg = _safe_dict(package)
    if dependency_stage_id == "input_package":
        return bool(_safe_dict(pkg.get("input")))
    if dependency_stage_id == "audio_map":
        return _has_valid_audio_map_payload(pkg)
    if dependency_stage_id == "story_core":
        return _has_valid_story_core_payload(_safe_dict(pkg.get("story_core")))
    if dependency_stage_id == "role_plan":
        return (
            _has_valid_role_plan_payload(_safe_dict(pkg.get("role_plan")))
            and _role_plan_covers_audio_segments(pkg)
            and _is_role_plan_signature_compatible(pkg)
        )
    return _can_reuse_stage_output(pkg, dependency_stage_id)


def _collect_scene_plan_dependency_gate_state(
    package: dict[str, Any], dependencies: list[str]
) -> tuple[dict[str, bool], dict[str, str], bool]:
    pkg = _safe_dict(package)
    statuses = _safe_dict(pkg.get("stage_statuses"))
    payload_ok_by_stage: dict[str, bool] = {}
    status_by_stage: dict[str, str] = {}
    false_positive_prevented = False
    for dep_stage in dependencies:
        payload_ok = _scene_plan_dependency_payload_ok(pkg, dep_stage)
        payload_ok_by_stage[dep_stage] = payload_ok
        dep_status = str(_safe_dict(statuses.get(dep_stage)).get("status") or "").strip().lower()
        status_by_stage[dep_stage] = dep_status
        if payload_ok and _stage_is_marked_stale_or_invalid(pkg, dep_stage):
            false_positive_prevented = True
    return payload_ok_by_stage, status_by_stage, false_positive_prevented


def _can_run_scene_plan_from_existing_payload(
    package: dict[str, Any], dependencies: list[str] | None = None
) -> bool:
    deps = dependencies or resolve_stage_sequence(["scene_plan"], include_dependencies=True)[:-1]
    payload_ok_by_stage, _, _ = _collect_scene_plan_dependency_gate_state(package, deps)
    return all(bool(payload_ok_by_stage.get(dep_stage)) for dep_stage in deps)


def _is_audio_map_dependency_satisfied(package: dict[str, Any]) -> bool:
    audio_map = _safe_dict(_safe_dict(package).get("audio_map"))
    segments = _safe_list(audio_map.get("segments"))
    if not segments:
        return False
    for row in segments:
        seg = _safe_dict(row)
        t0 = _to_float(seg.get("t0"), -1.0)
        t1 = _to_float(seg.get("t1"), -1.0)
        duration_sec = _to_float(seg.get("duration_sec"), -1.0)
        if t0 < 0.0 or t1 <= t0:
            return False
        if duration_sec <= 0.0 or abs(duration_sec - (t1 - t0)) > 0.02:
            return False
    analyzer = _safe_dict(audio_map.get("audio_analyzer"))
    beats = _safe_list(analyzer.get("beats"))
    vocal_phrases = _safe_list(analyzer.get("vocal_phrases"))
    analyzer_segments = _safe_list(analyzer.get("segments"))
    if not beats or not vocal_phrases or analyzer_segments != segments:
        return False
    diagnostics = _safe_dict(audio_map.get("diagnostics"))
    coverage_ok = diagnostics.get("coverage_ok")
    if isinstance(coverage_ok, str):
        return coverage_ok.strip().lower() in {"1", "true", "yes", "ok"}
    return bool(coverage_ok)


def _has_valid_audio_map_payload_for_scene_prompts(package: dict[str, Any]) -> bool:
    audio_map = _safe_dict(_safe_dict(package).get("audio_map"))
    segments = _safe_list(audio_map.get("segments"))
    if not segments:
        return False
    seen: list[str] = []
    for raw in segments:
        seg = _safe_dict(raw)
        segment_id = str(seg.get("segment_id") or "").strip()
        if not segment_id:
            return False
        try:
            t0 = float(seg.get("t0"))
            t1 = float(seg.get("t1"))
        except Exception:
            return False
        if t1 <= t0:
            return False
        seen.append(segment_id)
    return len(seen) == len(set(seen))


def _has_valid_audio_map_payload_for_downstream_video(package: dict[str, Any]) -> bool:
    return _has_valid_audio_map_payload_for_scene_prompts(package)


def _has_valid_audio_map_payload_for_finalize(package: dict[str, Any]) -> bool:
    return _has_valid_audio_map_payload_for_downstream_video(package)


def _has_valid_scene_prompts_payload_for_final_video_prompt(package: dict[str, Any]) -> bool:
    scene_prompts = _safe_dict(_safe_dict(package).get("scene_prompts"))
    prompts_version = str(scene_prompts.get("prompts_version") or "").strip()
    if prompts_version != "1.1":
        return False
    rows = _safe_list(scene_prompts.get("segments")) or _safe_list(scene_prompts.get("scenes"))
    if not rows:
        return False

    for raw in rows:
        item = _safe_dict(raw)
        segment_id = str(item.get("segment_id") or "").strip()
        route = str(item.get("route") or "").strip().lower()
        photo_prompt = str(item.get("photo_prompt") or item.get("positive_prompt") or "").strip()
        video_prompt = str(item.get("video_prompt") or item.get("positive_video_prompt") or "").strip()
        if not segment_id:
            return False
        if route not in {"i2v", "ia2v"}:
            return False
        if not photo_prompt or not video_prompt:
            return False

    return True


def _has_valid_audio_map_payload(package: dict[str, Any]) -> bool:
    audio_map = _safe_dict(_safe_dict(package).get("audio_map"))
    if not audio_map:
        return False
    segments = audio_map.get("segments")
    if not isinstance(segments, list) or not segments:
        return False
    for row in segments:
        seg = _safe_dict(row)
        if not str(seg.get("segment_id") or "").strip():
            return False
        if seg.get("t0") is None or seg.get("t1") is None:
            return False
    return True


def _scene_plan_audio_map_stage_error_present(package: dict[str, Any]) -> bool:
    pkg = _safe_dict(package)
    stage_error = str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get("audio_map")).get("error") or "").strip()
    if stage_error:
        return True
    for row in _safe_list(_safe_dict(pkg.get("diagnostics")).get("errors")):
        if "audio_map" in str(row or "").strip().lower():
            return True
    return False


def _final_video_prompt_dependency_payload_ok(package: dict[str, Any], dependency_stage_id: str) -> bool:
    pkg = _safe_dict(package)
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    if dependency_stage_id == "input_package":
        return bool(_safe_dict(pkg.get("input")))
    if dependency_stage_id == "audio_map":
        return _has_valid_audio_map_payload_for_downstream_video(pkg)
    if dependency_stage_id == "story_core":
        story_core = _safe_dict(pkg.get("story_core"))
        segments = _safe_list(story_core.get("narrative_segments")) or _safe_list(
            _safe_dict(story_core.get("story_core_v1")).get("narrative_segments")
        )
        return bool(story_core) and bool(segments)
    if dependency_stage_id == "role_plan":
        role_plan = _safe_dict(pkg.get("role_plan"))
        roster = _safe_list(role_plan.get("roster"))
        scene_casting = _safe_list(role_plan.get("scene_casting"))
        coverage_ok = diagnostics.get("role_plan_segment_coverage_ok")
        return bool(role_plan) and bool(roster) and (bool(scene_casting) or bool(coverage_ok))
    if dependency_stage_id == "scene_plan":
        scene_plan = _safe_dict(pkg.get("scene_plan"))
        storyboard = _safe_list(scene_plan.get("storyboard"))
        coverage_ok = diagnostics.get("scene_plan_segment_coverage_ok")
        if isinstance(coverage_ok, bool):
            return bool(scene_plan) and bool(storyboard) and coverage_ok
        return bool(scene_plan) and bool(storyboard)
    if dependency_stage_id == "scene_prompts":
        return _has_valid_scene_prompts_payload_for_final_video_prompt(pkg)
    return _can_reuse_stage_output(pkg, dependency_stage_id)


def _finalize_dependency_payload_ok(package: dict[str, Any], dependency_stage_id: str) -> bool:
    pkg = _safe_dict(package)
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    if dependency_stage_id == "input_package":
        return bool(_safe_dict(pkg.get("input")))
    if dependency_stage_id == "audio_map":
        return _has_valid_audio_map_payload_for_finalize(pkg)
    if dependency_stage_id == "story_core":
        story_core = _safe_dict(pkg.get("story_core"))
        segments = _safe_list(story_core.get("narrative_segments")) or _safe_list(
            _safe_dict(story_core.get("story_core_v1")).get("narrative_segments")
        )
        return bool(story_core) and len(segments) > 0
    if dependency_stage_id == "role_plan":
        role_plan = _safe_dict(pkg.get("role_plan"))
        roster = _safe_list(role_plan.get("roster"))
        scene_casting = _safe_list(role_plan.get("scene_casting"))
        coverage_ok = diagnostics.get("role_plan_segment_coverage_ok")
        return bool(role_plan) and bool(roster) and (bool(scene_casting) or bool(coverage_ok))
    if dependency_stage_id == "scene_plan":
        scene_plan = _safe_dict(pkg.get("scene_plan"))
        storyboard = _safe_list(scene_plan.get("storyboard"))
        scenes = _safe_list(scene_plan.get("scenes"))
        segments = _safe_list(scene_plan.get("segments"))
        coverage_ok = diagnostics.get("scene_plan_segment_coverage_ok")
        has_rows = bool(storyboard or scenes or segments)
        if isinstance(coverage_ok, bool):
            return bool(scene_plan) and has_rows and coverage_ok
        return bool(scene_plan) and has_rows
    if dependency_stage_id == "scene_prompts":
        scene_prompts = _safe_dict(pkg.get("scene_prompts"))
        scenes = _safe_list(scene_prompts.get("scenes"))
        segments = _safe_list(scene_prompts.get("segments"))
        prompts_version = str(scene_prompts.get("prompts_version") or "").strip()
        count = len(segments) or len(scenes)
        return bool(scene_prompts) and count > 0 and bool(prompts_version)
    if dependency_stage_id == "final_video_prompt":
        final_video_prompt = _safe_dict(pkg.get("final_video_prompt"))
        delivery_version = str(final_video_prompt.get("delivery_version") or "").strip()
        segments = _safe_list(final_video_prompt.get("segments")) or _safe_list(final_video_prompt.get("scenes"))
        if not final_video_prompt or not delivery_version or not segments:
            return False
        for raw in segments:
            item = _safe_dict(raw)
            segment_id = str(item.get("segment_id") or "").strip()
            route = str(item.get("route") or "").strip().lower()
            video_prompt = str(item.get("positive_video_prompt") or item.get("video_prompt") or "").strip()
            if not segment_id or route not in {"i2v", "ia2v"} or not video_prompt:
                return False
        return True
    return _can_reuse_stage_output(pkg, dependency_stage_id)


def _final_video_prompt_dependency_reason(package: dict[str, Any], dependency_stage_id: str) -> str:
    pkg = _safe_dict(package)
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    if dependency_stage_id == "story_core":
        story_core = _safe_dict(pkg.get("story_core"))
        if not story_core:
            return "missing_story_core_payload"
        segments = _safe_list(story_core.get("narrative_segments")) or _safe_list(
            _safe_dict(story_core.get("story_core_v1")).get("narrative_segments")
        )
        return "" if segments else "empty_story_core_segments"
    if dependency_stage_id == "role_plan":
        role_plan = _safe_dict(pkg.get("role_plan"))
        if not role_plan:
            return "missing_role_plan_payload"
        if not _safe_list(role_plan.get("roster")):
            return "missing_role_plan_payload"
        has_scene_casting = bool(_safe_list(role_plan.get("scene_casting")))
        has_coverage = bool(diagnostics.get("role_plan_segment_coverage_ok"))
        return "" if (has_scene_casting or has_coverage) else "empty_role_plan_scene_casting"
    if dependency_stage_id == "scene_plan":
        scene_plan = _safe_dict(pkg.get("scene_plan"))
        if not scene_plan:
            return "missing_scene_plan_payload"
        storyboard = _safe_list(scene_plan.get("storyboard"))
        if not storyboard:
            return "empty_scene_plan_storyboard"
        coverage_ok = diagnostics.get("scene_plan_segment_coverage_ok")
        if isinstance(coverage_ok, bool) and not coverage_ok:
            return "empty_scene_plan_storyboard"
        return ""
    if dependency_stage_id == "scene_prompts":
        return "" if _has_valid_scene_prompts_payload_for_final_video_prompt(pkg) else "scene_prompts_payload_invalid_for_final_video_prompt"
    if dependency_stage_id == "input_package":
        return "" if bool(_safe_dict(pkg.get("input"))) else "missing_input_package_payload"
    if dependency_stage_id == "audio_map":
        return "" if _has_valid_audio_map_payload_for_downstream_video(pkg) else "audio_map_payload_invalid_for_final_video_prompt"
    return f"missing_{dependency_stage_id}_payload"


def _finalize_dependency_reason(package: dict[str, Any], dependency_stage_id: str) -> str:
    pkg = _safe_dict(package)
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    if dependency_stage_id == "input_package":
        return "" if bool(_safe_dict(pkg.get("input"))) else "missing_input_package_payload"
    if dependency_stage_id == "audio_map":
        return "" if _has_valid_audio_map_payload_for_finalize(pkg) else "audio_map_payload_invalid_for_finalize"
    if dependency_stage_id == "story_core":
        story_core = _safe_dict(pkg.get("story_core"))
        if not story_core:
            return "missing_story_core_payload"
        segments = _safe_list(story_core.get("narrative_segments")) or _safe_list(
            _safe_dict(story_core.get("story_core_v1")).get("narrative_segments")
        )
        return "" if segments else "empty_story_core_segments"
    if dependency_stage_id == "role_plan":
        role_plan = _safe_dict(pkg.get("role_plan"))
        if not role_plan:
            return "missing_role_plan_payload"
        if not _safe_list(role_plan.get("roster")):
            return "missing_role_plan_payload"
        has_scene_casting = bool(_safe_list(role_plan.get("scene_casting")))
        has_coverage = bool(diagnostics.get("role_plan_segment_coverage_ok"))
        return "" if (has_scene_casting or has_coverage) else "empty_role_plan_scene_casting"
    if dependency_stage_id == "scene_plan":
        scene_plan = _safe_dict(pkg.get("scene_plan"))
        if not scene_plan:
            return "missing_scene_plan_payload"
        rows = (
            _safe_list(scene_plan.get("storyboard"))
            or _safe_list(scene_plan.get("scenes"))
            or _safe_list(scene_plan.get("segments"))
        )
        if not rows:
            return "empty_scene_plan_storyboard"
        coverage_ok = diagnostics.get("scene_plan_segment_coverage_ok")
        if isinstance(coverage_ok, bool) and not coverage_ok:
            return "empty_scene_plan_storyboard"
        return ""
    if dependency_stage_id == "scene_prompts":
        scene_prompts = _safe_dict(pkg.get("scene_prompts"))
        if not scene_prompts:
            return "missing_scene_prompts_payload"
        scenes = _safe_list(scene_prompts.get("scenes")) or _safe_list(scene_prompts.get("segments"))
        if not scenes:
            return "empty_scene_prompts_scenes"
        prompts_version = str(scene_prompts.get("prompts_version") or "").strip()
        return "" if prompts_version else "missing_scene_prompts_prompts_version"
    if dependency_stage_id == "final_video_prompt":
        return (
            ""
            if _finalize_dependency_payload_ok(pkg, "final_video_prompt")
            else "final_video_prompt_payload_invalid_for_finalize"
        )
    return f"missing_{dependency_stage_id}_payload"


def _collect_final_video_prompt_dependency_gate_state(
    package: dict[str, Any], dependencies: list[str]
) -> tuple[list[str], dict[str, bool], dict[str, str], bool]:
    pkg = _safe_dict(package)
    statuses = _safe_dict(pkg.get("stage_statuses"))
    payload_ok_by_stage: dict[str, bool] = {}
    status_by_stage: dict[str, str] = {}
    missing_reasons: list[str] = []
    false_positive_prevented = False
    for dep_stage in dependencies:
        payload_ok = _final_video_prompt_dependency_payload_ok(pkg, dep_stage)
        payload_ok_by_stage[dep_stage] = payload_ok
        dep_status = str(_safe_dict(statuses.get(dep_stage)).get("status") or "").strip().lower()
        status_by_stage[dep_stage] = dep_status
        if dep_status == "stale" and payload_ok:
            false_positive_prevented = True
        if not payload_ok:
            reason = _final_video_prompt_dependency_reason(pkg, dep_stage)
            missing_reasons.append(reason or f"missing_{dep_stage}_payload")
    audio_map = _safe_dict(pkg.get("audio_map"))
    audio_segments = _safe_list(audio_map.get("segments"))
    audio_map_payload_valid = _has_valid_audio_map_payload_for_downstream_video(pkg)
    scene_prompts = _safe_dict(pkg.get("scene_prompts"))
    scene_prompts_segments = _safe_list(scene_prompts.get("segments")) or _safe_list(scene_prompts.get("scenes"))
    scene_prompts_payload_valid = _has_valid_scene_prompts_payload_for_final_video_prompt(pkg)
    payload_ok_by_stage["audio_map"] = bool(audio_map_payload_valid)
    payload_ok_by_stage["scene_prompts"] = bool(scene_prompts_payload_valid)
    missing_reasons = [
        _final_video_prompt_dependency_reason(pkg, dep_stage) or f"missing_{dep_stage}_payload"
        for dep_stage in dependencies
        if not bool(payload_ok_by_stage.get(dep_stage))
    ]
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    diagnostics["final_video_prompt_dependency_audio_map_payload_valid"] = bool(audio_map_payload_valid)
    diagnostics["final_video_prompt_dependency_audio_map_segment_count"] = len(audio_segments)
    diagnostics["final_video_prompt_dependency_audio_map_coverage_ok"] = bool(
        _safe_dict(audio_map.get("diagnostics")).get("coverage_ok")
    )
    diagnostics["final_video_prompt_dependency_scene_prompts_payload_valid"] = bool(scene_prompts_payload_valid)
    diagnostics["final_video_prompt_dependency_scene_prompts_segment_count"] = len(scene_prompts_segments)
    diagnostics["final_video_prompt_dependency_gate_recomputed_after_payload_overrides"] = True
    pkg["diagnostics"] = diagnostics
    return missing_reasons, payload_ok_by_stage, status_by_stage, false_positive_prevented


def _collect_finalize_dependency_gate_state(
    package: dict[str, Any], dependencies: list[str]
) -> tuple[list[str], dict[str, bool], dict[str, str], bool]:
    pkg = _safe_dict(package)
    statuses = _safe_dict(pkg.get("stage_statuses"))
    payload_ok_by_stage: dict[str, bool] = {}
    status_by_stage: dict[str, str] = {}
    missing_reasons: list[str] = []
    false_positive_prevented = False
    for dep_stage in dependencies:
        payload_ok = _finalize_dependency_payload_ok(pkg, dep_stage)
        payload_ok_by_stage[dep_stage] = payload_ok
        dep_status = str(_safe_dict(statuses.get(dep_stage)).get("status") or "").strip().lower()
        status_by_stage[dep_stage] = dep_status
        if dep_status == "stale" and payload_ok:
            false_positive_prevented = True
        if not payload_ok:
            reason = _finalize_dependency_reason(pkg, dep_stage)
            missing_reasons.append(reason or f"missing_{dep_stage}_payload")
    audio_map = _safe_dict(pkg.get("audio_map"))
    audio_segments = _safe_list(audio_map.get("segments"))
    audio_map_payload_valid = _has_valid_audio_map_payload_for_finalize(pkg)
    payload_ok_by_stage["audio_map"] = bool(audio_map_payload_valid)
    missing_reasons = [
        _finalize_dependency_reason(pkg, dep_stage) or f"missing_{dep_stage}_payload"
        for dep_stage in dependencies
        if not bool(payload_ok_by_stage.get(dep_stage))
    ]
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    diagnostics["finalize_dependency_audio_map_payload_valid"] = bool(audio_map_payload_valid)
    diagnostics["finalize_dependency_audio_map_segment_count"] = len(audio_segments)
    diagnostics["finalize_dependency_audio_map_coverage_ok"] = bool(_safe_dict(audio_map.get("diagnostics")).get("coverage_ok"))
    diagnostics["finalize_dependency_gate_recomputed_after_payload_overrides"] = True
    pkg["diagnostics"] = diagnostics
    return missing_reasons, payload_ok_by_stage, status_by_stage, false_positive_prevented


def _restore_payload_valid_upstream_statuses_for_stage(
    pkg: dict[str, Any],
    stage_id: str,
    deps: list[str],
    payload_ok_by_stage: dict[str, bool],
) -> dict[str, Any]:
    package = deepcopy(_safe_dict(pkg))
    diagnostics = _safe_dict(package.get("diagnostics"))
    restored_stages: list[str] = []
    restore_reason = "payload_validity_gate_accepted_reused_outputs"
    diagnostics["final_video_prompt_reused_upstream_statuses_restored"] = False
    diagnostics["final_video_prompt_reused_upstream_statuses_restored_stages"] = []
    diagnostics["final_video_prompt_reused_upstream_status_restore_reason"] = restore_reason
    diagnostics["finalize_reused_upstream_statuses_restored"] = False
    diagnostics["finalize_reused_upstream_statuses_restored_stages"] = []
    diagnostics["finalize_reused_upstream_status_restore_reason"] = restore_reason
    diagnostics["scene_prompts_reused_upstream_statuses_restored"] = False
    diagnostics["scene_prompts_reused_upstream_statuses_restored_stages"] = []
    diagnostics["scene_prompts_reused_upstream_status_restore_reason"] = restore_reason
    if stage_id not in {"scene_prompts", "final_video_prompt", "finalize"}:
        package["diagnostics"] = diagnostics
        return package

    statuses = _safe_dict(package.get("stage_statuses"))
    allowed_deps = {
        "scene_prompts": {"input_package", "audio_map", "story_core", "role_plan", "scene_plan"},
        "final_video_prompt": {"input_package", "audio_map", "story_core", "role_plan", "scene_plan", "scene_prompts"},
        "finalize": {"story_core", "role_plan", "scene_plan", "scene_prompts", "final_video_prompt"},
    }.get(stage_id, set())
    for dep_stage in deps:
        if dep_stage not in allowed_deps:
            continue
        if not bool(payload_ok_by_stage.get(dep_stage)):
            continue
        stage_state = _safe_dict(statuses.get(dep_stage))
        dep_status = str(stage_state.get("status") or "").strip().lower()
        if dep_status not in {"stale", "error"}:
            continue
        stage_state["status"] = "done"
        stage_state["error"] = ""
        stage_state["restored_at"] = _utc_iso()
        for key in (
            "invalidated",
            "invalid",
            "dirty",
            "stale",
            "staleReason",
            "stale_reason",
            "reason",
            "statusReason",
            "invalidateReason",
            "invalidatedReason",
        ):
            stage_state.pop(key, None)
        statuses[dep_stage] = stage_state
        restored_stages.append(dep_stage)

    package["stage_statuses"] = statuses
    if stage_id == "scene_prompts":
        diagnostics["scene_prompts_reused_upstream_statuses_restored"] = bool(restored_stages)
        diagnostics["scene_prompts_reused_upstream_statuses_restored_stages"] = restored_stages
        diagnostics["scene_prompts_reused_upstream_status_restore_reason"] = restore_reason
    if stage_id == "final_video_prompt":
        diagnostics["final_video_prompt_reused_upstream_statuses_restored"] = bool(restored_stages)
        diagnostics["final_video_prompt_reused_upstream_statuses_restored_stages"] = restored_stages
        diagnostics["final_video_prompt_reused_upstream_status_restore_reason"] = restore_reason
    if stage_id == "finalize":
        diagnostics["finalize_reused_upstream_statuses_restored"] = bool(restored_stages)
        diagnostics["finalize_reused_upstream_statuses_restored_stages"] = restored_stages
        diagnostics["finalize_reused_upstream_status_restore_reason"] = restore_reason
    package["diagnostics"] = diagnostics
    return package


def _is_stage_dependency_satisfied(package: dict[str, Any], stage_id: str, dependency_stage_id: str) -> bool:
    if stage_id == "scene_plan":
        return _scene_plan_dependency_payload_ok(package, dependency_stage_id)
    if stage_id == "scene_prompts":
        return _scene_prompts_dependency_payload_ok(package, dependency_stage_id)
    if stage_id == "final_video_prompt":
        return _final_video_prompt_dependency_payload_ok(package, dependency_stage_id)
    if stage_id == "finalize":
        return _finalize_dependency_payload_ok(package, dependency_stage_id)
    if _can_reuse_stage_output(package, dependency_stage_id):
        return True
    if stage_id == "story_core" and dependency_stage_id == "audio_map":
        return _is_audio_map_dependency_satisfied(package)
    return False


def _compact_prompt_payload(value: Any) -> Any:
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = _compact_prompt_payload(item)
            if cleaned in (None, "", [], {}):
                continue
            compact[str(key)] = cleaned
        return compact
    if isinstance(value, list):
        compact_list: list[Any] = []
        for item in value:
            cleaned = _compact_prompt_payload(item)
            if cleaned in (None, "", [], {}):
                continue
            compact_list.append(cleaned)
        return compact_list
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_ref_meta(meta: Any) -> dict[str, Any]:
    row = _safe_dict(meta)
    ownership_role = str(row.get("ownershipRole") or row.get("ownership_role") or "auto").strip().lower() or "auto"
    ownership_mapped = str(row.get("ownershipRoleMapped") or row.get("ownership_role_mapped") or "").strip().lower()
    if ownership_mapped not in {"character_1", "character_2", "character_3", "shared", "environment"}:
        ownership_mapped = _OWNERSHIP_ROLE_MAP.get(ownership_role, "")
    binding_type = str(row.get("bindingType") or row.get("binding_type") or "nearby").strip().lower() or "nearby"
    if binding_type not in _BINDING_TYPES:
        binding_type = "nearby"
    return {
        **row,
        "ownershipRole": ownership_role,
        "ownershipRoleMapped": ownership_mapped,
        "bindingType": binding_type,
    }


_CHARACTER_VIEW_ORDER = ("front_primary", "side_profile", "performance_medium", "back_optional")
_CHARACTER_VIEW_LABELS = {
    "front_primary": "Фронт / основной",
    "side_profile": "Бок / профиль",
    "performance_medium": "Полутело / lip-sync",
    "back_optional": "Сзади / optional",
}


def _extract_ref_entry_url(entry: Any) -> str:
    if isinstance(entry, str):
        return str(entry).strip()
    row = _safe_dict(entry)
    return str(
        row.get("url")
        or row.get("asset_path")
        or row.get("assetPath")
        or row.get("src")
        or row.get("value")
        or ""
    ).strip()


def _normalize_character_views(row: dict[str, Any], role: str) -> dict[str, dict[str, Any]]:
    refs_list = _safe_list(row.get("refs"))
    incoming_views = _safe_dict(row.get("characterViews") or row.get("character_views") or _safe_dict(row.get("meta")).get("character_views"))
    refs_by_view: dict[str, dict[str, Any]] = {}
    for idx, ref_item in enumerate(refs_list):
        ref_row = _safe_dict(ref_item)
        declared = str(ref_row.get("view_type") or ref_row.get("viewType") or "").strip().lower()
        if declared in _CHARACTER_VIEW_ORDER and declared not in refs_by_view:
            refs_by_view[declared] = ref_row
            continue
        fallback_key = _CHARACTER_VIEW_ORDER[idx] if idx < len(_CHARACTER_VIEW_ORDER) else ""
        if fallback_key and fallback_key not in refs_by_view:
            refs_by_view[fallback_key] = ref_row
    normalized: dict[str, dict[str, Any]] = {}
    for idx, view_key in enumerate(_CHARACTER_VIEW_ORDER):
        view_src = _safe_dict(incoming_views.get(view_key))
        entry = refs_by_view.get(view_key) or _safe_dict(refs_list[idx] if idx < len(refs_list) else {})
        url = str(view_src.get("url") or _extract_ref_entry_url(entry)).strip()
        if not url:
            continue
        normalized[view_key] = {
            "url": url,
            "asset_path": str(view_src.get("asset_path") or view_src.get("assetPath") or url).strip(),
            "view_type": view_key,
            "label": str(view_src.get("label") or _safe_dict(entry).get("label") or _CHARACTER_VIEW_LABELS.get(view_key) or "").strip(),
            "order": idx,
            "priority": idx,
            "is_primary": view_key == "front_primary",
            "role": role,
        }
    return normalized


def _normalize_refs_inventory(refs_inventory: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    normalized: dict[str, Any] = {}
    ownership_binding_inventory: list[dict[str, Any]] = []
    for key, value in refs_inventory.items():
        if not isinstance(value, dict):
            normalized[str(key)] = value
            continue
        row = dict(value)
        meta = _normalize_ref_meta(row.get("meta"))
        row["meta"] = meta
        if str(key) in {"ref_character_1", "ref_character_2", "ref_character_3"}:
            role = "character_1" if str(key) == "ref_character_1" else ("character_2" if str(key) == "ref_character_2" else "character_3")
            character_views = _normalize_character_views(row, role)
            if character_views:
                row["characterViews"] = character_views
                row["character_views"] = character_views
                row["meta"] = {**_safe_dict(row.get("meta")), "character_views": character_views}
        normalized[str(key)] = row
        if meta.get("ownershipRoleMapped") or meta.get("bindingType") != "nearby":
            ownership_binding_inventory.append(
                {
                    "ref_id": str(key),
                    "label": str(row.get("source_label") or row.get("value") or key).strip()[:120],
                    "ownershipRole": str(meta.get("ownershipRole") or "auto"),
                    "ownershipRoleMapped": str(meta.get("ownershipRoleMapped") or ""),
                    "bindingType": str(meta.get("bindingType") or "nearby"),
                }
            )
    return normalized, ownership_binding_inventory[:24]


def _extract_audio_url_from_refs(refs_inventory: dict[str, Any]) -> str:
    audio_in = _safe_dict(refs_inventory.get("audio_in"))
    meta = _safe_dict(audio_in.get("meta"))
    candidates = (
        audio_in.get("value"),
        meta.get("url"),
        audio_in.get("preview"),
    )
    for item in candidates:
        value = str(item or "").strip()
        if value:
            return value
    return ""


def _resolve_active_video_model_id(input_pkg: dict[str, Any]) -> str:
    for key in ("video_model", "video_model_id", "model_id"):
        value = str(input_pkg.get(key) or "").strip().lower()
        if value:
            return value
    return DEFAULT_VIDEO_MODEL_ID


def _extract_ref_urls(ref_node: Any) -> list[str]:
    def _looks_like_ref_url(value: str) -> bool:
        lower = value.lower()
        if lower.startswith(("http://", "https://", "blob:", "data:")):
            return True
        if value.startswith("/"):
            return True
        if "static/assets/" in lower:
            return True
        return "/" in value

    if isinstance(ref_node, str):
        value = ref_node.strip()
        return [value] if value and _looks_like_ref_url(value) else []
    node = _safe_dict(ref_node)
    urls: list[str] = []
    for candidate in (
        node.get("value"),
        _safe_dict(node.get("meta")).get("url"),
    ):
        value = str(candidate or "").strip()
        if value and _looks_like_ref_url(value):
            urls.append(value)
    for ref in _safe_list(node.get("refs")):
        value = _extract_ref_entry_url(ref)
        if value and _looks_like_ref_url(value):
            urls.append(value)
    return list(dict.fromkeys(urls))


def _role_to_ref_key(role: str) -> str:
    clean = str(role or "").strip()
    if not clean:
        return ""
    return clean if clean.startswith("ref_") else f"ref_{clean}"


def _visual_ref_identity_rule(role: str) -> str:
    clean_role = str(role or "").strip() or "character_1"
    return (
        f"{clean_role} identity must be taken from the connected {clean_role} image reference as the canonical source of truth. "
        "Preserve the same face, age impression, body proportions, hairstyle, clothing, silhouette, and overall visual identity "
        "from the reference across all scenes. Text descriptions of clothing, age, body, or face are auxiliary only and must not "
        "override the visual reference."
    )


def _has_connected_visual_ref(
    package: dict[str, Any],
    input_pkg: dict[str, Any],
    refs_inventory: dict[str, Any],
    role: str,
) -> tuple[bool, str]:
    clean_role = str(role or "").strip()
    if not clean_role:
        return False, ""

    def _map_has_non_empty_ref_url(raw_map: Any, source_prefix: str) -> tuple[bool, str]:
        role_map = _safe_dict(raw_map)
        values = _safe_list(role_map.get(clean_role))
        if any(bool(str(value or "").strip()) for value in values):
            return True, f"{source_prefix}.{clean_role}"
        return False, ""

    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    checks: list[tuple[bool, str]] = [
        _map_has_non_empty_ref_url(connected_summary.get("refsPresentByRole"), "input.connected_context_summary.refsPresentByRole"),
        _map_has_non_empty_ref_url(
            connected_summary.get("connectedRefsPresentByRole"),
            "input.connected_context_summary.connectedRefsPresentByRole",
        ),
        _map_has_non_empty_ref_url(input_pkg.get("refs_by_role"), "input.refs_by_role"),
        _map_has_non_empty_ref_url(input_pkg.get("refsByRole"), "input.refsByRole"),
    ]
    for has_ref, source in checks:
        if has_ref:
            return True, source

    ref_key = _role_to_ref_key(clean_role)
    inventory_row = _safe_dict(refs_inventory.get(ref_key))
    if _safe_list(inventory_row.get("refs")):
        return True, f"refs_inventory.{ref_key}.refs"
    if str(inventory_row.get("value") or "").strip():
        return True, f"refs_inventory.{ref_key}.value"
    return False, ""


def _append_visual_ref_auxiliary_note(existing_text: str, role: str) -> str:
    clean_text = str(existing_text or "").strip()
    if not clean_text:
        return ""
    if "as seen in the reference" in clean_text.lower():
        return clean_text
    return f"{clean_text} (auxiliary only, as seen in the reference)"


def _apply_visual_ref_identity_lock_to_story_core(
    story_core: dict[str, Any],
    story_core_v1: dict[str, Any],
    role: str,
    *,
    apply_hero_anchor_lock: bool = True,
) -> None:
    rule = _visual_ref_identity_rule(role)
    if apply_hero_anchor_lock:
        identity_doctrine = _safe_dict(story_core.get("identity_doctrine"))
        existing_hero_anchor = _ref_safe_identity_text(str(identity_doctrine.get("hero_anchor") or "").strip(), role)
        hero_anchor_suffix = _append_visual_ref_auxiliary_note(existing_hero_anchor, role)
        merged_anchor = f"{rule} {hero_anchor_suffix}".strip() if hero_anchor_suffix else rule
        identity_doctrine["hero_anchor"] = _ref_safe_identity_text(merged_anchor, role)
        story_core["identity_doctrine"] = identity_doctrine
        story_core["identity_lock"] = {"rule": rule}

    prompt_contract = _safe_dict(story_core_v1.get("prompt_interface_contract"))
    constraints = [str(item or "").strip() for item in _safe_list(prompt_contract.get("identity_prompt_constraints")) if str(item or "").strip()]
    filtered_constraints = [item for item in constraints if item.lower() != rule.lower()]
    prompt_contract["identity_prompt_constraints"] = [rule, *filtered_constraints]
    story_core_v1["prompt_interface_contract"] = prompt_contract


def _apply_visual_ref_identity_lock_to_role_plan(role_plan: dict[str, Any], role: str) -> None:
    rule = _visual_ref_identity_rule(role)
    normalized_aux_rule = "visual reference is canonical; text appearance is auxiliary"
    must_match_ref = f"must match connected {role} reference exactly"
    rule_with_aux = f"{rule} {normalized_aux_rule}. {must_match_ref}."

    for row in _safe_list(role_plan.get("roster")):
        if not isinstance(row, dict):
            continue
        row_role = (
            _canonical_subject_id(row.get("entity_id"))
            or _canonical_subject_id(row.get("role"))
            or _canonical_subject_id(row.get("id"))
            or _canonical_subject_id(row.get("character_id"))
        )
        if row_role != role:
            continue
        row["identity_reference_rule"] = rule_with_aux
        for key in ("continuity_rule", "identity_lock", "appearance", "description", "role_notes"):
            value = row.get(key)
            if isinstance(value, str):
                row[key] = f"{value.strip()} {normalized_aux_rule}; {must_match_ref}.".strip()
            elif isinstance(value, list):
                cleaned = [str(item or "").strip() for item in value if str(item or "").strip()]
                row[key] = [*cleaned, f"{normalized_aux_rule}; {must_match_ref}"]

    for row in _safe_list(role_plan.get("scene_casting")):
        if not isinstance(row, dict):
            continue
        primary_role = _canonical_subject_id(row.get("primary_role"))
        active_roles = [_canonical_subject_id(item) for item in _safe_list(row.get("active_roles"))]
        visual_focus_role = _canonical_subject_id(row.get("visual_focus_role"))
        role_is_present = role in {primary_role, visual_focus_role} or role in [item for item in active_roles if item]
        if not role_is_present:
            continue
        row["identity_source"] = "connected_visual_reference"
        row["identity_rule"] = rule
        notes = _safe_list(row.get("notes"))
        note_line = f"{normalized_aux_rule}; {must_match_ref}"
        if note_line not in notes:
            row["notes"] = [*notes, note_line]


def _collect_visual_ref_identity_diagnostics(
    package: dict[str, Any],
    input_pkg: dict[str, Any],
    refs_inventory: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, str]]:
    applied: dict[str, bool] = {}
    source: dict[str, str] = {}
    for role in ("character_1", "character_2", "character_3"):
        has_ref, ref_source = _has_connected_visual_ref(package, input_pkg, refs_inventory, role)
        applied[role] = has_ref
        if has_ref:
            source[role] = ref_source
    return applied, source


def _build_refs_by_role_fallback(
    refs_inventory: dict[str, Any],
    active_roles: list[Any],
    primary_role: str,
) -> dict[str, list[str]]:
    roles = [
        str(role).strip()
        for role in [primary_role, *active_roles]
        if str(role).strip()
    ]
    resolved: dict[str, list[str]] = {}
    for role in roles:
        ref_key = _role_to_ref_key(role)
        if not ref_key:
            continue
        urls = _extract_ref_urls(refs_inventory.get(ref_key))
        if urls:
            resolved[role] = urls
    return resolved


def _normalize_input_audio_source(input_payload: dict[str, Any], refs_inventory: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(_safe_dict(input_payload))
    audio_url = str(normalized.get("audio_url") or "").strip()
    refs_audio_url = _extract_audio_url_from_refs(refs_inventory)
    if not audio_url and refs_audio_url:
        audio_url = refs_audio_url
    normalized["audio_url"] = audio_url

    source = _safe_dict(normalized.get("source"))
    source_value = str(source.get("source_value") or source.get("sourceValue") or "").strip()
    if not source_value:
        source_value = audio_url
    if source_value:
        source["source_value"] = source_value
    normalized["source"] = source
    return normalized


def _resolve_director_mode(raw_mode: Any, *, content_type: str = "") -> str:
    normalized = str(raw_mode or "").strip().lower()
    if normalized in {"clip", "story", "ad"}:
        return normalized
    if normalized in {"music_video", "клип"}:
        return "clip"
    if normalized in {"история"}:
        return "story"
    if normalized in {"реклама", "reklama"}:
        return "ad"
    fallback_content_type = str(content_type or "").strip().lower()
    if fallback_content_type == "music_video":
        return "clip"
    if fallback_content_type == "ad":
        return "ad"
    return "story"


def _clamp_ratio(value: Any, default: float) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except Exception:
        return float(default)




def _sync_role_scene_route_semantics(package: dict[str, Any]) -> dict[str, int]:
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    scene_casting = _safe_list(role_plan.get("scene_casting"))
    storyboard = _safe_list(scene_plan.get("storyboard")) or _safe_list(scene_plan.get("scenes")) or _safe_list(scene_plan.get("segments"))
    if not scene_casting or not storyboard:
        return {"updated": 0, "ia2v_forced_visible": 0, "i2v_offscreen_repairs": 0}

    scene_rows_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in storyboard
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    updated = 0
    ia2v_forced_visible = 0
    i2v_offscreen_repairs = 0
    for idx, row in enumerate(scene_casting):
        casting_row = dict(_safe_dict(row))
        seg_id = str(casting_row.get("segment_id") or "").strip()
        if not seg_id:
            continue
        scene_row = _safe_dict(scene_rows_by_id.get(seg_id))
        if not scene_row:
            continue
        route = str(scene_row.get("route") or "").strip().lower()
        presence_mode = str(casting_row.get("presence_mode") or "").strip().lower()
        perf_focus = str(casting_row.get("performance_focus") or "").strip()
        changed = False
        if route == "ia2v":
            if presence_mode != "physical":
                casting_row["presence_mode"] = "physical"
                changed = True
                ia2v_forced_visible += 1
            if not perf_focus or any(token in perf_focus.lower() for token in ("offscreen", "voiceover", "implied", "background")):
                casting_row["performance_focus"] = "Visible on-screen vocal performance with readable emotional delivery."
                changed = True
            casting_row["presence_weight"] = str(casting_row.get("presence_weight") or "anchor").strip().lower() or "anchor"
        elif route == "i2v":
            visual_focus = str(scene_row.get("visual_focus_role") or "").strip().lower()
            speaker_role = str(scene_row.get("speaker_role") or "").strip().lower()
            is_env_cutaway = visual_focus == "environment" and not speaker_role
            if is_env_cutaway and presence_mode == "physical" and any(token in perf_focus.lower() for token in ("lip", "vocal", "on-screen", "onscreen", "performance")):
                casting_row["presence_mode"] = "voiceover"
                casting_row["performance_focus"] = "World-detail cutaway with singer offscreen."
                changed = True
                i2v_offscreen_repairs += 1
        if changed:
            scene_casting[idx] = casting_row
            updated += 1

    role_plan["scene_casting"] = scene_casting
    package["role_plan"] = _attach_downstream_mode_metadata(role_plan, package)
    return {"updated": updated, "ia2v_forced_visible": ia2v_forced_visible, "i2v_offscreen_repairs": i2v_offscreen_repairs}

def _build_route_strategy_signature_payload(creative_config: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalize_creative_config(creative_config)
    return {
        "route_strategy_mode": str(normalized.get("route_strategy_mode") or "auto"),
        "route_strategy_preset": str(normalized.get("route_strategy_preset") or ""),
        "route_targets_per_block": _safe_dict(normalized.get("route_targets_per_block")),
        "route_strategy_normalized_targets": _safe_dict(normalized.get("route_strategy_normalized_targets")),
        "lipsync_ratio": float(normalized.get("lipsync_ratio") or 0.0),
        "i2v_ratio": float(normalized.get("i2v_ratio") or 0.0),
        "first_last_ratio": float(normalized.get("first_last_ratio") or 0.0),
        "max_consecutive_ia2v": int(normalized.get("max_consecutive_ia2v") or 0),
        "max_consecutive_lipsync": int(normalized.get("max_consecutive_lipsync") or 0),
    }


def _route_strategy_signature_from_input(input_pkg: dict[str, Any]) -> str:
    creative_config = _safe_dict(_safe_dict(input_pkg).get("creative_config"))
    signature_payload = _build_route_strategy_signature_payload(creative_config)
    return _stable_hash_payload(signature_payload)


def _route_strategy_signature_for_package(package: dict[str, Any]) -> str:
    return _route_strategy_signature_from_input(_safe_dict(_safe_dict(package).get("input")))


def _normalize_long_vocal_split_policy(policy: Any, default: str) -> str:
    raw_policy = str(policy or "").strip()
    if not raw_policy:
        raw_policy = default
    aliases = {
        "split_long_vocal_ranges_into_ia2v_scenes_3_to_6_sec": "split_long_vocal_ranges_into_ia2v_scenes_3_to_7_sec",
        "prefer_ia2v_3_to_6_sec_allow_strong_vocal_opening_anchor_up_to_7_sec": "prefer_ia2v_3_to_7_sec_allow_strong_vocal_opening_anchor_up_to_7_sec",
    }
    return aliases.get(raw_policy, raw_policy)


def _normalize_creative_config(raw_config: Any) -> dict[str, Any]:
    row = _safe_dict(raw_config)
    new_route_strategy_keys = {
        "route_strategy_mode",
        "routeStrategyMode",
        "route_strategy_preset",
        "routeStrategyPreset",
        "route_targets_per_block",
        "routeTargetsPerBlock",
        "route_block_duration_sec",
        "routeBlockDurationSec",
        "base_scene_count",
        "baseSceneCount",
        "extra_scene_policy",
        "extraScenePolicy",
        "max_consecutive_ia2v",
        "maxConsecutiveIa2v",
        "instrumental_policy",
        "instrumentalPolicy",
        "vocal_policy",
        "vocalPolicy",
        "long_vocal_split_policy",
        "longVocalSplitPolicy",
    }
    route_strategy_present = any(key in row for key in new_route_strategy_keys)
    route_strategy_mode = str(row.get("route_strategy_mode") or row.get("routeStrategyMode") or "auto").strip().lower() or "auto"
    if route_strategy_mode not in {"auto", "preset", "custom_counts"}:
        route_strategy_mode = "auto"
    route_strategy_preset = str(row.get("route_strategy_preset") or row.get("routeStrategyPreset") or "").strip()
    base_scene_count = max(1, int(row.get("base_scene_count") or row.get("baseSceneCount") or 8))
    route_targets_raw = _safe_dict(row.get("route_targets_per_block") or row.get("routeTargetsPerBlock"))
    route_targets_per_block = {
        "i2v": max(0, int(route_targets_raw.get("i2v") or 0)),
        "ia2v": max(0, int(route_targets_raw.get("ia2v") or 0)),
        "first_last": max(0, int(route_targets_raw.get("first_last") or route_targets_raw.get("firstLast") or 0)),
    }
    explicit_route_targets = bool(route_targets_raw)
    has_route_targets = sum(route_targets_per_block.values()) > 0
    use_route_targets = bool(route_strategy_present and route_strategy_mode != "auto" and has_route_targets)
    if not route_strategy_present:
        route_targets_per_block = {}
    elif route_strategy_mode == "auto":
        route_targets_per_block = {}
    route_mix_mode = str(row.get("route_mix_mode") or row.get("routeMixMode") or ("auto" if route_strategy_mode == "auto" else "custom")).strip().lower() or "auto"
    if route_mix_mode not in {"auto", "custom"}:
        route_mix_mode = "auto"

    legacy_default_i2v = 0.5
    legacy_default_ia2v = 0.25
    legacy_default_first_last = 0.25
    lipsync_ratio = _clamp_ratio(
        row.get("lipsync_ratio"),
        route_targets_per_block.get("ia2v", 0) / base_scene_count if use_route_targets else legacy_default_ia2v,
    )
    first_last_ratio = _clamp_ratio(
        row.get("first_last_ratio"),
        route_targets_per_block.get("first_last", 0) / base_scene_count if use_route_targets else legacy_default_first_last,
    )
    i2v_ratio = _clamp_ratio(
        row.get("i2v_ratio"),
        route_targets_per_block.get("i2v", 0) / base_scene_count if use_route_targets else legacy_default_i2v,
    )
    if use_route_targets:
        lipsync_ratio = round(route_targets_per_block["ia2v"] / base_scene_count, 3)
        first_last_ratio = round(route_targets_per_block["first_last"] / base_scene_count, 3)
        i2v_ratio = round(route_targets_per_block["i2v"] / base_scene_count, 3)
    explicit_mode = any(key in row for key in ("route_strategy_mode", "routeStrategyMode"))
    explicit_preset = bool(route_strategy_preset) and any(key in row for key in ("route_strategy_preset", "routeStrategyPreset"))
    route_strategy_active = bool(route_strategy_present and route_strategy_mode != "auto")

    preferred_routes = [str(item).strip().lower() for item in _safe_list(row.get("preferred_routes")) if str(item).strip()]
    preferred_routes = [route for route in preferred_routes if route in {"i2v", "ia2v", "first_last"}] or ["i2v", "ia2v", "first_last"]

    try:
        max_consecutive_ia2v = int(row.get("max_consecutive_ia2v") or row.get("maxConsecutiveIa2v") or row.get("max_consecutive_lipsync") or 2)
    except Exception:
        max_consecutive_ia2v = 2
    max_consecutive_ia2v = max(1, min(8, max_consecutive_ia2v))

    return {
        "route_strategy_mode": route_strategy_mode,
        "route_strategy_preset": route_strategy_preset,
        "has_new_route_strategy": route_strategy_present,
        "route_strategy_present": route_strategy_present,
        "route_strategy_active": route_strategy_active,
        "route_strategy_explicit_mode": explicit_mode,
        "route_strategy_explicit_preset": explicit_preset,
        "route_strategy_explicit_targets": explicit_route_targets,
        "route_block_duration_sec": int(row.get("route_block_duration_sec") or row.get("routeBlockDurationSec") or 30),
        "base_scene_count": base_scene_count,
        "extra_scene_policy": str(row.get("extra_scene_policy") or row.get("extraScenePolicy") or "add_i2v").strip() or "add_i2v",
        "route_targets_per_block": route_targets_per_block,
        "route_strategy_normalized_targets": dict(route_targets_per_block) if route_strategy_mode != "auto" else {},
        "max_consecutive_ia2v": max_consecutive_ia2v,
        "targets_are_soft": bool(row.get("targets_are_soft") if row.get("targets_are_soft") is not None else (row.get("targetsAreSoft") if row.get("targetsAreSoft") is not None else True)),
        "instrumental_policy": str(row.get("instrumental_policy") or row.get("instrumentalPolicy") or "use_i2v_for_non_vocal_or_instrumental_gaps").strip() or "use_i2v_for_non_vocal_or_instrumental_gaps",
        "vocal_policy": str(row.get("vocal_policy") or row.get("vocalPolicy") or "ia2v_only_on_vocal_windows").strip() or "ia2v_only_on_vocal_windows",
        "long_vocal_split_policy": _normalize_long_vocal_split_policy(
            row.get("long_vocal_split_policy") or row.get("longVocalSplitPolicy"),
            default="split_long_vocal_ranges_into_ia2v_scenes_3_to_7_sec",
        ),
        "route_mix_mode": route_mix_mode,
        "lipsync_ratio": round(lipsync_ratio, 3),
        "first_last_ratio": round(first_last_ratio, 3),
        "i2v_ratio": round(i2v_ratio, 3),
        "preferred_routes": preferred_routes,
        "max_consecutive_lipsync": max_consecutive_ia2v,
    }



def _clear_stale_stage_failure_diagnostics(package: dict[str, Any], stage_id: str) -> None:
    diagnostics = _safe_dict(package.get("diagnostics"))
    errors = _safe_list(diagnostics.get("errors"))
    warnings = _safe_list(diagnostics.get("warnings"))
    stage_prefix = f"{stage_id}:"
    failure_tokens = (CORE_QUALITY_GATES_FAILED, "hard_fail:")

    def _drop_error(item: Any) -> bool:
        text = str(item or "").strip()
        return bool(text and text.startswith(stage_prefix))

    def _drop_warning(item: Any) -> bool:
        if isinstance(item, dict):
            warning_stage_id = str(item.get("stage_id") or "").strip()
            message = str(item.get("message") or "").strip()
            return warning_stage_id == stage_id and any(token in message for token in failure_tokens)
        text = str(item or "").strip()
        return bool(text and text.startswith(stage_prefix) and any(token in text for token in failure_tokens))

    diagnostics["errors"] = [item for item in errors if not _drop_error(item)][-80:]
    diagnostics["warnings"] = [item for item in warnings if not _drop_warning(item)][-80:]
    package["diagnostics"] = diagnostics


def _inject_route_strategy_diagnostics(package: dict[str, Any]) -> None:
    input_pkg = _safe_dict(package.get("input"))
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    route_strategy_signature = _route_strategy_signature_from_input(input_pkg)
    diagnostics = _safe_dict(package.get("diagnostics"))
    route_strategy_present = bool(creative_config.get("route_strategy_present"))
    route_strategy_active = bool(creative_config.get("route_strategy_active"))
    route_strategy_mode = str(creative_config.get("route_strategy_mode") or "auto")
    normalized_targets = _safe_dict(creative_config.get("route_strategy_normalized_targets"))
    diagnostics["input_route_strategy_present"] = route_strategy_present
    diagnostics["input_route_strategy_mode"] = route_strategy_mode
    diagnostics["input_route_strategy_active"] = route_strategy_active
    diagnostics["input_route_strategy_normalized_targets"] = normalized_targets
    diagnostics["audio_route_strategy_note"] = "audio stage does not author routes; route strategy is preserved for downstream"
    diagnostics["story_core_route_strategy_present"] = route_strategy_present
    diagnostics["story_core_route_strategy_mode"] = route_strategy_mode
    diagnostics["story_core_route_strategy_active"] = route_strategy_active
    diagnostics["story_core_route_strategy_normalized_targets"] = normalized_targets
    diagnostics["story_core_route_strategy_preset"] = str(creative_config.get("route_strategy_preset") or "")
    diagnostics["roles_route_strategy_present"] = route_strategy_present
    diagnostics["roles_route_strategy_mode"] = route_strategy_mode
    diagnostics["roles_route_strategy_active"] = route_strategy_active
    diagnostics["roles_route_strategy_normalized_targets"] = normalized_targets
    diagnostics["scene_plan_route_strategy_present"] = route_strategy_present
    diagnostics["scene_plan_route_strategy_active"] = route_strategy_active
    diagnostics["scene_plan_route_strategy_mode"] = route_strategy_mode
    diagnostics["scene_plan_route_strategy_preset"] = str(creative_config.get("route_strategy_preset") or "")
    diagnostics["scene_plan_route_strategy_normalized_targets"] = normalized_targets
    diagnostics["scene_plan_route_strategy_signature"] = route_strategy_signature
    diagnostics["scene_plan_route_targets_per_block"] = _safe_dict(creative_config.get("route_targets_per_block"))
    diagnostics["scene_plan_extra_scene_policy"] = str(creative_config.get("extra_scene_policy") or "add_i2v")
    diagnostics["scene_plan_targets_are_soft"] = bool(creative_config.get("targets_are_soft"))
    diagnostics["scene_plan_instrumental_policy"] = str(creative_config.get("instrumental_policy") or "")
    diagnostics["scene_plan_vocal_policy"] = str(creative_config.get("vocal_policy") or "")
    diagnostics["scene_plan_long_vocal_split_policy"] = str(creative_config.get("long_vocal_split_policy") or "")
    diagnostics["scene_prompts_route_strategy_present"] = route_strategy_present
    diagnostics["scene_prompts_route_strategy_mode"] = route_strategy_mode
    diagnostics["scene_prompts_route_strategy_active"] = route_strategy_active
    diagnostics["scene_prompts_route_strategy_normalized_targets"] = normalized_targets
    diagnostics["final_video_prompt_route_strategy_present"] = route_strategy_present
    diagnostics["final_video_prompt_route_strategy_mode"] = route_strategy_mode
    diagnostics["final_video_prompt_route_strategy_active"] = route_strategy_active
    diagnostics["final_video_prompt_route_strategy_normalized_targets"] = normalized_targets
    diagnostics["final_route_strategy_present"] = route_strategy_present
    diagnostics["final_route_strategy_mode"] = route_strategy_mode
    diagnostics["final_route_strategy_active"] = route_strategy_active
    diagnostics["final_route_strategy_normalized_targets"] = normalized_targets
    diagnostics["route_strategy_signature"] = route_strategy_signature
    package["diagnostics"] = diagnostics

def _build_route_mix_doctrine_for_scenes(creative_config: dict[str, Any]) -> tuple[dict[str, Any], str]:
    mode = str(creative_config.get("route_mix_mode") or "auto").strip().lower() or "auto"
    source = "custom_creative_config" if mode == "custom" else "auto_default"
    if mode == "custom":
        target_ratios = {
            "ia2v": float(_clamp_ratio(creative_config.get("lipsync_ratio"), 0.25)),
            "i2v": float(_clamp_ratio(creative_config.get("i2v_ratio"), 0.5)),
            "first_last": float(_clamp_ratio(creative_config.get("first_last_ratio"), 0.25)),
        }
    else:
        target_ratios = {"ia2v": 0.25, "i2v": 0.5, "first_last": 0.25}
    doctrine = {
        "core_scope_only": "doctrine_not_segment_assignment",
        "short_clip_default_target_ratios": target_ratios,
        "max_consecutive_lipsync": int(creative_config.get("max_consecutive_lipsync") or 2),
        "preferred_routes": _safe_list(creative_config.get("preferred_routes")),
        "lipsync_candidate_is_permission_not_obligation": True,
        "avoid_long_consecutive_lipsync_streaks": True,
        "prioritize_lipsync_for_strong_performance_windows": True,
    }
    return doctrine, source


def _extract_request_creative_config(req: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    direct = req.get("creative_config")
    if isinstance(direct, dict):
        return direct
    director_controls = _safe_dict(req.get("director_controls"))
    if not director_controls:
        director_controls = _safe_dict(req.get("directorControls"))
    from_controls = director_controls.get("creative_config")
    if isinstance(from_controls, dict):
        return from_controls
    from_metadata = _safe_dict(metadata).get("creative_config")
    return from_metadata if isinstance(from_metadata, dict) else {}


def _compute_route_budget_for_total(total_scenes: int, creative_config: dict[str, Any]) -> dict[str, int]:
    if total_scenes <= 0:
        return {"i2v": 0, "ia2v": 0, "first_last": 0}
    if total_scenes == 1:
        return {"i2v": 1, "ia2v": 0, "first_last": 0}
    preset_name = str(creative_config.get("route_strategy_preset") or "").strip().lower()
    if preset_name == "no_first_last_50_50_0":
        return compute_no_first_last_50_50_targets(total_scenes)

    base_scene_count = max(1, int(creative_config.get("base_scene_count") or 8))
    targets = _safe_dict(creative_config.get("route_targets_per_block"))
    has_targets = any(int(targets.get(k) or 0) > 0 for k in ("i2v", "ia2v", "first_last"))
    has_new_route_strategy = bool(
        creative_config.get("has_new_route_strategy")
        if creative_config.get("has_new_route_strategy") is not None
        else creative_config.get("route_strategy_present")
    )
    route_strategy_mode = str(creative_config.get("route_strategy_mode") or "auto").strip().lower() or "auto"
    if has_targets and has_new_route_strategy and route_strategy_mode != "auto":
        def _budget_for_count(count: int) -> dict[str, int]:
            if count <= 0:
                return {"i2v": 0, "ia2v": 0, "first_last": 0}
            if count == 1:
                return {"i2v": 1, "ia2v": 0, "first_last": 0}
            i2v = max(0, int(targets.get("i2v") or 0))
            ia2v = max(0, int(targets.get("ia2v") or 0))
            first_last = max(0, int(targets.get("first_last") or 0))
            if count > base_scene_count:
                i2v += count - base_scene_count
            elif count < base_scene_count:
                deficit = base_scene_count - count
                reduce_fl = min(deficit, first_last)
                first_last -= reduce_fl
                deficit -= reduce_fl
                if deficit > 0:
                    reduce_i2v = min(deficit, i2v)
                    i2v -= reduce_i2v
                    deficit -= reduce_i2v
                if deficit > 0:
                    ia2v = max(0, ia2v - deficit)
            return {"i2v": i2v, "ia2v": ia2v, "first_last": first_last}

        full_blocks = total_scenes // base_scene_count
        remainder = total_scenes % base_scene_count
        base_i2v = max(0, int(targets.get("i2v") or 0))
        base_ia2v = max(0, int(targets.get("ia2v") or 0))
        base_first_last = max(0, int(targets.get("first_last") or 0))
        budget = {
            "i2v": base_i2v * full_blocks,
            "ia2v": base_ia2v * full_blocks,
            "first_last": base_first_last * full_blocks,
        }
        if remainder > 0:
            partial = _budget_for_count(remainder)
            budget["i2v"] += int(partial.get("i2v") or 0)
            budget["ia2v"] += int(partial.get("ia2v") or 0)
            budget["first_last"] += int(partial.get("first_last") or 0)
        return budget

    lipsync_ratio = _clamp_ratio(creative_config.get("lipsync_ratio"), 0.25)
    first_last_ratio = _clamp_ratio(creative_config.get("first_last_ratio"), 0.25)
    route_mix_mode = str(creative_config.get("route_mix_mode") or "auto").strip().lower()
    ia2v = int(round(total_scenes * lipsync_ratio))
    first_last = int(round(total_scenes * first_last_ratio))
    i2v = total_scenes - ia2v - first_last

    if route_mix_mode == "auto" and i2v < 1:
        deficit = 1 - i2v
        i2v = 1
        reducible_fl = min(deficit, max(0, first_last))
        first_last -= reducible_fl
        deficit -= reducible_fl
        if deficit > 0:
            ia2v = max(0, ia2v - deficit)
    while ia2v + i2v + first_last < total_scenes:
        i2v += 1
    while ia2v + i2v + first_last > total_scenes:
        if first_last > 0:
            first_last -= 1
        elif ia2v > 0:
            ia2v -= 1
        else:
            i2v = max(0, i2v - 1)
    return {"i2v": i2v, "ia2v": ia2v, "first_last": first_last}


def compute_no_first_last_50_50_targets(scene_count: int) -> dict[str, int]:
    n = max(0, int(scene_count or 0))
    return {
        "i2v": n // 2,
        "ia2v": (n + 1) // 2,
        "first_last": 0,
    }


def _expected_scene_count_from_package(package: dict[str, Any]) -> int:
    audio_segments = _safe_list(_safe_dict(package.get("audio_map")).get("segments"))
    return len(audio_segments)


def _expected_scene_segment_ids_from_package(package: dict[str, Any]) -> list[str]:
    segment_ids: list[str] = []
    for idx, row in enumerate(_safe_list(_safe_dict(package.get("audio_map")).get("segments")), start=1):
        segment_id = str(_safe_dict(row).get("segment_id") or f"seg_{idx:02d}").strip()
        if segment_id:
            segment_ids.append(segment_id)
    return segment_ids


_SCENE_PLAN_TECHNICAL_FORBIDDEN_TERMS = [
    "identity_source",
    "identity_rule",
    "identity_reference_rule",
    "connected_visual_reference",
    "canonical source of truth",
    "connected character_1 image reference",
    "image reference",
    "refs",
    "payload",
    "diagnostics",
    "route strategy",
    "visual_ref_identity_lock",
]


def _scene_plan_safe_identity_constraints(package: dict[str, Any]) -> dict[str, Any]:
    _ = package
    return {
        "character_1": {
            "role": "main",
            "scene_safe_rule": (
                "Keep character_1 visually consistent with the provided character reference in every scene. "
                "Do not change her face, age impression, body proportions, hairstyle, clothing, silhouette, or overall look."
            ),
            "forbidden_output_terms": list(_SCENE_PLAN_TECHNICAL_FORBIDDEN_TERMS),
        },
        "character_2": {
            "role": "secondary_unreferenced",
            "scene_safe_rule": (
                "The boyfriend is an unreferenced secondary person. Show him only partially, from behind, in silhouette, "
                "as a shoulder/arm/back near the door. Do not make him the main subject."
            ),
        },
    }


def _build_scene_plan_prompt_package(package: dict[str, Any]) -> dict[str, Any]:
    scene_pkg = deepcopy(_safe_dict(package))
    input_pkg = _safe_dict(scene_pkg.get("input"))
    role_plan = _safe_dict(scene_pkg.get("role_plan"))

    for key in (
        "connected_visual_reference",
        "identity_source",
        "identity_rule",
        "identity_reference_rule",
        "visual_ref_identity_lock_applied",
        "visual_ref_identity_lock_source",
        "diagnostics",
        "refs_inventory",
        "connectedRefsPresentByRole",
        "refsPresentByRole",
    ):
        input_pkg.pop(key, None)

    for key in ("diagnostics", "refs_inventory", "connectedRefsPresentByRole", "refsPresentByRole"):
        scene_pkg.pop(key, None)

    clean_scene_casting: list[dict[str, Any]] = []
    for row_raw in _safe_list(role_plan.get("scene_casting")):
        row = deepcopy(_safe_dict(row_raw))
        for key in (
            "identity_source",
            "identity_rule",
            "identity_reference_rule",
            "connected_visual_reference",
            "visual_ref_identity_lock_applied",
            "visual_ref_identity_lock_source",
        ):
            row.pop(key, None)
        clean_scene_casting.append(row)
    role_plan["scene_casting"] = clean_scene_casting

    def _sanitize_scene_plan_roster_value(value: Any) -> Any:
        if isinstance(value, str):
            sanitized = value
            for token in (
                "canonical source of truth",
                "connected character_1 image reference",
                "connected character_2 image reference",
                "connected character_3 image reference",
                "image reference",
                "refs",
                "visual_ref_identity_lock",
                "identity_source",
                "identity_rule",
                "identity_reference_rule",
                "connected_visual_reference",
            ):
                sanitized = re.sub(re.escape(token), "", sanitized, flags=re.IGNORECASE)
            return re.sub(r"\s+", " ", sanitized).strip(" ,.;:-")
        if isinstance(value, list):
            sanitized_list = [_sanitize_scene_plan_roster_value(item) for item in value]
            return [item for item in sanitized_list if item not in ("", None, [], {})]
        return value

    clean_roster: list[dict[str, Any]] = []
    for row_raw in _safe_list(role_plan.get("roster")):
        row = deepcopy(_safe_dict(row_raw))
        row.pop("identity_reference_rule", None)
        for key in ("continuity_rule", "identity_lock", "appearance", "description", "role_notes"):
            if key in row:
                row[key] = _sanitize_scene_plan_roster_value(row.get(key))
        clean_roster.append(row)
    role_plan["roster"] = clean_roster

    connected_summary = deepcopy(_safe_dict(input_pkg.get("connected_context_summary")))
    for key in (
        "refsPresentByRole",
        "connectedRefsPresentByRole",
        "role_identity_mapping",
        "character_identity_by_role",
    ):
        connected_summary.pop(key, None)
    safe_connected_summary: dict[str, Any] = {}
    for key in (
        "characterCount",
        "presentCastRoles",
        "activeSourceMode",
        "hasActiveSource",
        "hasLocation",
        "hasProps",
        "hasStyle",
    ):
        if key in connected_summary:
            safe_connected_summary[key] = connected_summary.get(key)
    input_pkg["connected_context_summary"] = safe_connected_summary

    role_plan["scene_safe_identity_constraints"] = _scene_plan_safe_identity_constraints(scene_pkg)
    role_plan["scene_prompt_rules"] = {
        "speaker_role_rule": (
            "Only ia2v scenes may include speaker_role, and it must be character_1. "
            "For i2v scenes, set speaker_role to an empty string. "
            "Do not use 'unknown'. For ia2v scenes, speaker_role must be character_1."
        ),
        "technical_vocabulary_forbidden": (
            "Do not output internal/backend/debug terminology. "
            "Use only cinematic storyboard language."
        ),
    }
    input_pkg["scene_safe_identity_constraints"] = _scene_plan_safe_identity_constraints(scene_pkg)
    scene_pkg["input"] = input_pkg
    scene_pkg["role_plan"] = role_plan
    return scene_pkg


def _scene_plan_rows_for_validation(scene_plan: dict[str, Any]) -> list[dict[str, Any]]:
    scene_rows = [row for row in _safe_list(scene_plan.get("scenes")) if isinstance(row, dict)]
    if scene_rows:
        return scene_rows
    return [row for row in _safe_list(scene_plan.get("storyboard")) if isinstance(row, dict)]


def _scene_plan_route_counts(scene_plan: dict[str, Any]) -> dict[str, int]:
    counts = {"i2v": 0, "ia2v": 0, "first_last": 0}
    for row in _scene_plan_rows_for_validation(scene_plan):
        route = str(_safe_dict(row).get("route") or "").strip().lower()
        if route in counts:
            counts[route] += 1
    return counts


def _scene_plan_signature_matches_current(package: dict[str, Any], scene_plan: dict[str, Any]) -> bool:
    diagnostics = _safe_dict(package.get("diagnostics"))
    current_signature = _current_scenario_input_signature(package)
    payload_signature = str(
        scene_plan.get("created_for_signature")
        or diagnostics.get("scene_plan_created_for_signature")
        or ""
    ).strip()
    if current_signature and payload_signature and payload_signature != current_signature:
        return False
    return True


def _scene_plan_route_strategy_signature_matches_current(package: dict[str, Any], scene_plan: dict[str, Any]) -> bool:
    diagnostics = _safe_dict(package.get("diagnostics"))
    current_signature = str(_route_strategy_signature_for_package(package) or "").strip()

    scene_plan_signature = str(scene_plan.get("route_strategy_signature") or "").strip()
    if scene_plan_signature:
        return not (current_signature and scene_plan_signature != current_signature)

    # Legacy fallback: old scene_plan payloads may not contain route_strategy_signature.
    # In this case we can compare diagnostics signature, but only when there are explicit
    # legacy markers indicating the package predates scene_plan.route_strategy_signature.
    legacy_package = bool(
        diagnostics.get("scene_plan_uses_legacy_scene_candidate_windows_bridge")
        or diagnostics.get("scene_plan_uses_legacy_compiled_contract_bridge")
        or diagnostics.get("scene_prompts_uses_legacy_bridge")
        or diagnostics.get("scene_prompts_legacy_bridge_present")
    )
    if not legacy_package:
        return True

    diagnostics_signature = str(
        diagnostics.get("scene_plan_route_strategy_signature")
        or diagnostics.get("route_strategy_signature")
        or ""
    ).strip()
    if current_signature and diagnostics_signature and diagnostics_signature != current_signature:
        return False
    return True


def _scene_plan_route_locks_by_segment_valid(scene_plan: dict[str, Any]) -> bool:
    locks_raw = _safe_dict(scene_plan.get("route_locks_by_segment"))
    if not locks_raw:
        return False
    scene_rows = _scene_plan_rows_for_validation(scene_plan)
    if not scene_rows:
        return False
    supported_routes = {"i2v", "ia2v", "first_last"}
    expected_segment_ids: list[str] = []
    for idx, row in enumerate(scene_rows, start=1):
        segment_id = str(
            _safe_dict(row).get("segment_id")
            or _safe_dict(row).get("scene_id")
            or f"seg_{idx:02d}"
        ).strip()
        if not segment_id:
            return False
        expected_segment_ids.append(segment_id)
    for segment_id in expected_segment_ids:
        route = str(locks_raw.get(segment_id) or "").strip().lower()
        if route not in supported_routes:
            return False
    return True


def _scene_plan_payload_supports_scene_prompts_with_reason(package: dict[str, Any]) -> tuple[bool, str]:
    pkg = _safe_dict(package)
    scene_plan = _safe_dict(pkg.get("scene_plan"))
    if not _has_valid_scene_plan_payload(scene_plan):
        return False, "missing_scene_plan_payload"

    scene_rows = _scene_plan_rows_for_validation(scene_plan)
    if not scene_rows:
        return False, "empty_scene_plan_rows"

    if not _scene_plan_signature_matches_current(pkg, scene_plan):
        return False, "scene_plan_signature_mismatch"
    if not _scene_plan_route_strategy_signature_matches_current(pkg, scene_plan):
        return False, "scene_plan_route_strategy_signature_mismatch"

    audio_segments = [row for row in _safe_list(_safe_dict(pkg.get("audio_map")).get("segments")) if isinstance(row, dict)]
    expected_segment_ids = [str(_safe_dict(row).get("segment_id") or "").strip() for row in audio_segments]
    expected_segment_ids = [segment_id for segment_id in expected_segment_ids if segment_id]
    expected_count = len(expected_segment_ids)
    if expected_count <= 0:
        return False, "audio_map_segments_missing"
    if len(scene_rows) != expected_count:
        return False, "scene_plan_scene_count_mismatch"

    scene_segment_ids = [
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip() for row in scene_rows
    ]
    scene_segment_ids = [segment_id for segment_id in scene_segment_ids if segment_id]
    if len(scene_segment_ids) != len(expected_segment_ids):
        return False, "scene_plan_segment_count_mismatch"
    if set(scene_segment_ids) != set(expected_segment_ids):
        return False, "scene_plan_segment_coverage_mismatch"

    route_counts = _scene_plan_route_counts(scene_plan)
    route_locks_valid = _scene_plan_route_locks_by_segment_valid(scene_plan)
    route_budget_ok = bool(_safe_dict(pkg.get("diagnostics")).get("scene_plan_route_budget_ok"))
    if not route_budget_ok and not route_locks_valid:
        return False, "scene_plan_route_budget_or_locks_invalid"
    return True, ""


def _build_scene_plan_retry_feedback(validation_error: str, error_code: str, scene_diag: dict[str, Any]) -> str:
    base = (
        f"Previous output invalid: validation_error={validation_error}; "
        f"error_code={error_code or 'SCENES_SCHEMA_INVALID'}"
    )
    ve = validation_error.strip().lower()
    leak_trigger = str(_safe_dict(scene_diag).get("scene_plan_technical_leak_token") or "").strip()
    if ve == "technical_leaking" or leak_trigger:
        return (
            base
            + "\nYour previous response included internal technical terms. Rewrite the scene_plan using only "
            "cinematic/storyboard language. Do not output or mention: identity_source, identity_rule, "
            "identity_reference_rule, connected_visual_reference, canonical source of truth, image reference, refs, "
            "payload, diagnostics, route strategy, visual_ref_identity_lock."
        )
    if ve == "speaker_role_invalid":
        return (
            base
            + "\nFor i2v scenes, speaker_role must be an empty string. "
            "For ia2v scenes, speaker_role must be character_1. "
            "Do not use unknown/none/location/props/character_2 as speaker_role."
        )
    if ve == "route_budget_mismatch":
        expected_count = int(_safe_dict(scene_diag).get("segment_count_expected") or 0)
        target = _safe_dict(scene_diag.get("route_budget_resolved_targets") or scene_diag.get("scene_plan_route_budget_target"))
        return (
            "You returned wrong route distribution.\n"
            f"There are exactly {expected_count} segment_id rows.\n"
            "Return exactly one storyboard row per segment_id.\n"
            "Required route budget:\n"
            f"ia2v: {int(target.get('ia2v') or 0)}\n"
            f"i2v: {int(target.get('i2v') or 0)}\n"
            f"first_last: {int(target.get('first_last') or 0)}\n\n"
            "No first_last is allowed when target first_last is 0.\n"
            "Because character_1 appearanceMode can be lip_sync_only: ia2v rows require character_1 as physical speaker "
            "(speaker_role=character_1, lip_sync_allowed=true, mouth_visible_required=true). "
            "For i2v rows, character_1 must not be primary physical subject and speaker_role must be empty with lip_sync_allowed=false. "
            "Use environment/city/street/port/courtyard/people/atmosphere as visual subject when needed.\n"
            "Do not invent or remove segment_id.\n"
            "Do not return empty storyboard."
        )
    return base


def _scene_prompt_rows_for_validation(scene_prompts: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row for row in _safe_list(scene_prompts.get("segments")) if isinstance(row, dict)]
    if rows:
        return rows
    return [row for row in _safe_list(scene_prompts.get("scenes")) if isinstance(row, dict)]


def _scene_prompt_identity_anchor(role: str, role_plan: dict[str, Any], scene_row: dict[str, Any], package: dict[str, Any]) -> str:
    _ = role_plan, scene_row
    clean_role = _canonical_subject_id(role)
    if not clean_role:
        return ""
    input_pkg = _safe_dict(package.get("input"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    has_ref, _ = _has_connected_visual_ref(package, input_pkg, refs_inventory, clean_role)
    if not has_ref:
        return ""
    return (
        f"Show the same person as the provided {clean_role} reference. "
        "Preserve the same face, age impression, body proportions, hairstyle, clothing, silhouette, and overall look in this scene."
    )


def _character_1_lip_sync_only(package: dict[str, Any]) -> bool:
    input_pkg = _safe_dict(package.get("input"))
    summary = _safe_dict(input_pkg.get("connected_context_summary"))
    role_map = _safe_dict(summary.get("role_identity_mapping"))
    char1 = _safe_dict(role_map.get("character_1"))
    appearance = str(char1.get("appearanceMode") or char1.get("appearance_mode") or "").strip().lower()
    presence = str(char1.get("screenPresenceMode") or char1.get("screen_presence_mode") or "").strip().lower()
    return appearance == "lip_sync_only" or presence == "lip_sync_only"


def _secondary_presence_mode_hints(scene_row: dict[str, Any], role_plan: dict[str, Any], package: dict[str, Any]) -> list[str]:
    _ = scene_row, role_plan
    input_pkg = _safe_dict(package.get("input"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    has_ref, _ = _has_connected_visual_ref(package, input_pkg, refs_inventory, "character_2")
    if has_ref:
        return []
    return [
        "partial_silhouette",
        "blurred_presence",
        "memory_haze_insert",
        "fantasy_impression",
        "reflection_fragment",
        "object_association_presence",
    ]


def _scene_prompt_has_explicit_reference_anchor(text: Any, role: str) -> bool:
    body = str(text or "").strip().lower()
    clean_role = _canonical_subject_id(role)
    if not body or not clean_role:
        return False
    role_reference_tokens = (
        f"provided {clean_role} reference",
        f"{clean_role} reference",
        "provided reference",
    )
    continuity_tokens = (
        "same current character_1 from the connected character_1 reference",
        "same current performer from the connected character_1 reference",
        "same person",
        "keep the same",
        "same character",
    )
    if not any(token in body for token in role_reference_tokens):
        return False
    return any(token in body for token in continuity_tokens)


def _prepend_clause_once(base: Any, clause: str) -> str:
    text = str(base or "").strip()
    addition = str(clause or "").strip()
    if not addition:
        return text
    if addition.lower() in text.lower():
        return text
    if not text:
        return addition
    return f"{addition} {text}".strip()


def _scene_roles_for_segment(
    segment_id: str,
    prompt_row: dict[str, Any],
    scene_plan_rows: dict[str, dict[str, Any]],
    role_plan_rows: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scene_row = _safe_dict(scene_plan_rows.get(segment_id))
    role_row = _safe_dict(role_plan_rows.get(segment_id))
    primary_role = (
        _canonical_subject_id(prompt_row.get("primary_role"))
        or _canonical_subject_id(scene_row.get("primary_role"))
        or _canonical_subject_id(role_row.get("primary_role"))
    )
    visual_focus_role = (
        _canonical_subject_id(prompt_row.get("visual_focus_role"))
        or _canonical_subject_id(scene_row.get("visual_focus_role"))
        or _canonical_subject_id(role_row.get("visual_focus_role"))
    )
    speaker_role = (
        _canonical_subject_id(prompt_row.get("speaker_role"))
        or _canonical_subject_id(scene_row.get("speaker_role"))
        or _canonical_subject_id(role_row.get("speaker_role"))
    )
    active_roles = [
        _canonical_subject_id(value)
        for value in (
            _safe_list(prompt_row.get("active_roles"))
            + _safe_list(scene_row.get("active_roles"))
            + _safe_list(role_row.get("active_roles"))
        )
    ]
    secondary_roles = [
        _canonical_subject_id(value)
        for value in (
            _safe_list(prompt_row.get("secondary_roles"))
            + _safe_list(scene_row.get("secondary_roles"))
            + _safe_list(role_row.get("secondary_roles"))
        )
    ]
    present_roles = {role for role in [primary_role, visual_focus_role, speaker_role, *active_roles, *secondary_roles] if role}
    return {
        "scene_row": scene_row,
        "role_row": role_row,
        "primary_role": primary_role,
        "visual_focus_role": visual_focus_role,
        "speaker_role": speaker_role,
        "present_roles": present_roles,
    }


def _enforce_scene_prompts_identity_and_presence(
    package: dict[str, Any],
    scene_prompts: dict[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    normalized = deepcopy(_safe_dict(scene_prompts))
    scene_plan = _safe_dict(package.get("scene_plan"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan_rows = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _scene_plan_rows_for_validation(scene_plan)
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    role_plan_rows = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(role_plan.get("scene_casting"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }

    identity_enforced_count = 0
    secondary_hints_applied_count = 0
    identity_drift_segment = ""
    lip_sync_only = _character_1_lip_sync_only(package)
    for row in _scene_prompt_rows_for_validation(normalized):
        segment_id = str(row.get("segment_id") or row.get("scene_id") or "").strip()
        if not segment_id:
            continue
        role_state = _scene_roles_for_segment(segment_id, row, scene_plan_rows, role_plan_rows)
        scene_row = _safe_dict(role_state.get("scene_row"))
        role_row = _safe_dict(role_state.get("role_row"))
        present_roles = set(role_state.get("present_roles") or set())
        character_1_involved = (
            role_state.get("primary_role") == "character_1"
            or role_state.get("visual_focus_role") == "character_1"
            or role_state.get("speaker_role") == "character_1"
            or "character_1" in present_roles
        )
        route = str(row.get("route") or scene_row.get("route") or "").strip().lower()
        if character_1_involved and not (lip_sync_only and route == "i2v"):
            anchor = _scene_prompt_identity_anchor("character_1", role_row, scene_row, package)
            if anchor:
                row["photo_prompt"] = _prepend_clause_once(row.get("photo_prompt"), anchor)
                row["video_prompt"] = _prepend_clause_once(row.get("video_prompt"), anchor)
                identity_enforced_count += 1
            photo_has_anchor = _scene_prompt_has_explicit_reference_anchor(row.get("photo_prompt"), "character_1")
            video_has_anchor = _scene_prompt_has_explicit_reference_anchor(row.get("video_prompt"), "character_1")
            if not photo_has_anchor and not video_has_anchor:
                identity_drift_segment = segment_id
                break

        character_2_involved = "character_2" in present_roles
        presence_modes = _secondary_presence_mode_hints(scene_row, role_row, package) if character_2_involved else []
        if presence_modes:
            safety_clause = (
                "If character_2 appears, keep him as an unreferenced secondary presence only: partial silhouette, "
                "back, shoulder, shadow, or soft blur near the doorway. Avoid a clear frontal face. "
                "Optional styles when creatively useful: blurred presence, reflection fragment, or object association presence."
            )
            row["photo_prompt"] = _prepend_clause_once(row.get("photo_prompt"), safety_clause)
            row["video_prompt"] = _prepend_clause_once(row.get("video_prompt"), safety_clause)
            secondary_hints_applied_count += 1

    validation_error = f"identity_drift:{identity_drift_segment}" if identity_drift_segment else ""
    diagnostics = {
        "scene_prompts_identity_anchor_enforced_count": identity_enforced_count,
        "scene_prompts_secondary_presence_hints_applied_count": secondary_hints_applied_count,
        "scene_prompts_secondary_presence_modes_supported": [
            "partial_silhouette",
            "blurred_presence",
            "memory_haze_insert",
            "fantasy_impression",
            "reflection_fragment",
            "object_association_presence",
        ],
    }
    return normalized, validation_error, diagnostics


def _scene_prompts_quality_pass(
    package: dict[str, Any],
    scene_prompts: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    def _build_universal_world_style_anchor(
        pkg: dict[str, Any],
        prompts_payload: dict[str, Any],
    ) -> tuple[str, bool]:
        input_pkg = _safe_dict(pkg.get("input"))
        story_core = _safe_dict(pkg.get("story_core"))
        identity_doctrine = _safe_dict(story_core.get("identity_doctrine"))
        world_lock = _safe_dict(story_core.get("world_lock"))
        style_lock = _safe_dict(story_core.get("style_lock"))

        existing_anchor = str(_safe_dict(prompts_payload).get("global_style_anchor") or "").strip()
        source_texts: list[tuple[str, str]] = [
            ("input.text", str(input_pkg.get("text") or "").strip()),
            ("input.story_text", str(input_pkg.get("story_text") or "").strip()),
            ("input.note", str(input_pkg.get("note") or "").strip()),
            ("input.director_note", str(input_pkg.get("director_note") or "").strip()),
            ("story_core.world_lock.rule", str(world_lock.get("rule") or "").strip()),
            ("story_core.identity_doctrine.world_doctrine", str(identity_doctrine.get("world_doctrine") or "").strip()),
            ("story_core.style_lock.rule", str(style_lock.get("rule") or "").strip()),
            ("story_core.identity_doctrine.style_doctrine", str(identity_doctrine.get("style_doctrine") or "").strip()),
            ("scene_prompts.global_style_anchor", existing_anchor),
        ]
        bundle = " ".join(text for _, text in source_texts if text).lower()
        if not bundle:
            return existing_anchor, False

        cue_catalog: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] = {
            "environment": (
                ("port city", ("port city", "harbor city", "harbour city", "dock district")),
                ("club", ("club", "nightclub", "dance floor", "bar interior")),
                ("kitchen", ("kitchen", "galley", "cooktop", "kitchen drama")),
                ("alley", ("alley", "alleyway", "lane", "backstreet")),
                ("apartment", ("apartment", "flat", "living room", "bedroom interior")),
                ("village", ("village", "rural lane", "hamlet")),
                ("industrial zone", ("industrial", "factory", "warehouse", "shipyard")),
                ("seaside", ("seaside", "sea", "shore", "coast", "embankment", "quay")),
                ("desert", ("desert", "sand", "arid")),
                ("courtyard", ("courtyard", "inner yard", "patio")),
                ("street", ("street", "boulevard", "avenue", "road")),
                ("interior", ("interior", "indoors", "inside")),
                ("exterior", ("exterior", "outdoor", "outside")),
            ),
            "materials": (
                ("stone", ("stone", "weathered stone", "limestone")),
                ("concrete", ("concrete", "cement")),
                ("wood", ("wood", "wooden")),
                ("dust", ("dust", "dusty")),
                ("rust", ("rust", "rusted", "oxidized metal")),
                ("tile", ("tile", "tiled")),
                ("cobblestone", ("cobblestone", "cobbled")),
                ("metal", ("metal", "steel", "iron")),
                ("glass", ("glass", "window glare", "glass reflections")),
                ("plaster", ("plaster", "stucco")),
                ("fabric atmosphere", ("fabric", "textile", "cloth texture")),
            ),
            "air_light": (
                ("salt air", ("salt air", "sea air", "salty wind", "sea breeze")),
                ("humidity", ("humidity", "humid air", "moist air")),
                ("harsh sun", ("harsh sun", "hard sunlight", "blazing sun")),
                ("pale morning light", ("pale morning light", "morning light", "dawn light")),
                ("neon haze", ("neon haze", "neon glow", "neon spill")),
                ("warm tungsten interior", ("warm tungsten", "tungsten interior", "warm practical lights")),
                ("cold dawn", ("cold dawn", "cold morning", "blue dawn")),
                ("rainy air", ("rainy air", "wet air", "rain mist", "drizzle haze")),
                ("smoke/steam", ("smoke", "steam", "haze", "vapor")),
                ("sea wind", ("sea wind", "coastal wind", "shore wind")),
            ),
            "architecture": (
                ("archways", ("archway", "archways", "arches")),
                ("balconies", ("balcony", "balconies")),
                ("narrow streets", ("narrow street", "narrow streets", "tight alley")),
                ("courtyards", ("courtyard", "courtyards")),
                ("port cranes", ("port crane", "port cranes", "harbor crane")),
                ("tram lines", ("tram line", "tram lines", "tram tracks")),
                ("corridor", ("corridor", "hallway", "passage")),
                ("stage", ("stage", "performance stage")),
                ("kitchen table", ("kitchen table", "tabletop kitchen")),
                ("embankment", ("embankment", "seafront walk", "waterfront edge")),
                ("roofline", ("roofline", "rooftops", "roof edge")),
                ("stairway", ("stairway", "stairs", "staircase")),
            ),
            "realism": (
                ("lived-in realism", ("lived-in realism", "lived in", "worn-in world", "grounded realism")),
                ("no tourist postcard", ("no tourist postcard", "avoid postcard", "not postcard pretty")),
                ("no generic cinematic filler", ("no generic cinematic filler", "avoid generic cinematic", "avoid stock cinematic")),
                ("grounded world continuity", ("grounded world continuity", "world continuity", "world-consistent realism")),
                ("no glossy stock-image vibe", ("no glossy stock-image vibe", "avoid glossy stock", "not glossy stock-image")),
            ),
        }
        strong_sources = {
            "story_core.world_lock.rule",
            "story_core.identity_doctrine.world_doctrine",
            "story_core.style_lock.rule",
            "story_core.identity_doctrine.style_doctrine",
            "scene_prompts.global_style_anchor",
        }
        detected: dict[str, dict[str, set[str]]] = {key: {} for key in cue_catalog}
        for source_name, text in source_texts:
            lowered = str(text or "").lower()
            if not lowered:
                continue
            for category, entries in cue_catalog.items():
                for label, aliases in entries:
                    if any(alias in lowered for alias in aliases):
                        bucket = detected[category].setdefault(label, set())
                        bucket.add(source_name)

        selected: dict[str, list[str]] = {}
        for category, labels in detected.items():
            ranked = sorted(
                labels.items(),
                key=lambda item: (
                    max(1 if src in strong_sources else 0 for src in item[1]),
                    len(item[1]),
                    -len(item[0]),
                ),
                reverse=True,
            )
            selected_labels: list[str] = []
            for label, srcs in ranked:
                mentions = len(srcs)
                if mentions < 2 and not any(src in strong_sources for src in srcs):
                    continue
                if label in existing_anchor.lower():
                    continue
                selected_labels.append(label)
                if len(selected_labels) >= 2:
                    break
            selected[category] = selected_labels

        if not any(selected.values()) and not existing_anchor:
            return "", False

        if not selected["realism"]:
            realism_floor = "lived-in realism, grounded world continuity, no tourist-postcard gloss, no generic cinematic filler"
            if realism_floor not in existing_anchor.lower():
                selected["realism"] = [realism_floor]

        parts: list[str] = []
        if selected["environment"]:
            parts.append(f"environment: {', '.join(selected['environment'])}")
        if selected["materials"]:
            parts.append(f"materials: {', '.join(selected['materials'])}")
        if selected["air_light"]:
            parts.append(f"air/light: {', '.join(selected['air_light'])}")
        if selected["architecture"]:
            parts.append(f"spatial cues: {', '.join(selected['architecture'])}")
        if selected["realism"]:
            parts.append(f"tone: {', '.join(selected['realism'])}")

        addition = "; ".join(part for part in parts if part).strip()
        if not addition:
            return existing_anchor, False

        if existing_anchor:
            enhanced = f"{existing_anchor} {addition}".strip()
        else:
            enhanced = addition
        return enhanced, enhanced != existing_anchor

    normalized = deepcopy(_safe_dict(scene_prompts))
    segments = [row for row in _safe_list(normalized.get("segments")) if isinstance(row, dict)]
    if not segments:
        return normalized, {
            "scene_prompts_quality_pass_applied": False,
            "scene_prompts_quality_ia2v_variant_applied_count": 0,
            "scene_prompts_quality_world_i2v_conflict_fixed_count": 0,
            "scene_prompts_quality_video_prompt_deduped_count": 0,
            "scene_prompts_quality_world_anchor_strengthened": False,
        }

    scene_plan_rows = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _scene_plan_rows_for_validation(_safe_dict(package.get("scene_plan")))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    core_rows = {
        str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(_safe_dict(package.get("story_core")).get("narrative_segments"))
        if str(_safe_dict(row).get("segment_id") or "").strip()
    }

    anchor_strengthened = False
    enhanced_anchor, anchor_strengthened = _build_universal_world_style_anchor(package, normalized)
    if enhanced_anchor:
        normalized["global_style_anchor"] = enhanced_anchor

    boilerplate_tokens = (
        "use the uploaded image as the exact first frame and identity anchor",
        "a performance shot of the same performer singing an emotional line",
        "clear expressive lip sync",
        "allow expressive but controlled gestures",
        "performer-first composition",
        "global continuity lock",
    )
    singer_conflict_patterns: tuple[re.Pattern[str], ...] = (
        re.compile(r"\bvisible(?:\s+on-screen)?\s+singing\b", re.IGNORECASE),
        re.compile(r"\blip[\s\-]?sync\b", re.IGNORECASE),
        re.compile(r"\breadable\s+mouth\b", re.IGNORECASE),
        re.compile(r"\bdominant\s+performer\b", re.IGNORECASE),
    )
    world_modes = {"world_observation", "world_pressure", "social_texture", "threshold", "aftermath", "release", "transition", "cutaway"}
    ia2v_variant_applied_count = 0
    world_i2v_conflict_fixed_count = 0
    video_prompt_deduped_count = 0
    prev_ia2v_variant = ""

    def _dedupe_prompt_sentences(text: str) -> tuple[str, bool]:
        body = re.sub(r"\s+", " ", str(text or "").strip())
        if not body:
            return "", False
        parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", body) if p.strip()]
        seen_norm: set[str] = set()
        out: list[str] = []
        changed = False
        for part in parts:
            norm = re.sub(r"[^a-z0-9]+", " ", part.lower()).strip()
            if not norm:
                continue
            is_duplicate = norm in seen_norm
            if not is_duplicate:
                for token in boilerplate_tokens:
                    if token in norm and any(token in prior for prior in seen_norm):
                        is_duplicate = True
                        break
            if is_duplicate:
                changed = True
                continue
            seen_norm.add(norm)
            out.append(part)
        return " ".join(out).strip(), changed

    def _ia2v_variant_clause(segment_id: str, row: dict[str, Any]) -> str:
        nonlocal prev_ia2v_variant
        scene_row = _safe_dict(scene_plan_rows.get(segment_id))
        core_row = _safe_dict(core_rows.get(segment_id))
        arc_role = str(row.get("arc_role") or scene_row.get("arc_role") or core_row.get("arc_role") or "").strip().lower()
        beat_mode = str(row.get("beat_mode") or scene_row.get("beat_mode") or core_row.get("beat_mode") or "").strip().lower()
        visual_scale = str(row.get("visual_scale") or scene_row.get("visual_scale") or "").strip().lower()
        motion_profile = str(row.get("motion_profile") or scene_row.get("motion_profile") or "").strip().lower()
        emotional_key = str(row.get("emotional_key") or scene_row.get("emotional_key") or core_row.get("emotional_key") or "").strip().lower()
        location_zone = str(row.get("location_zone") or scene_row.get("location_zone") or core_row.get("location_zone") or "").strip()
        candidates = [
            ("intimate_close_lipsync", "Singer under a weathered stone archway, close face framing with shoulder context, natural daylight, subtle hand motion, background passage depth."),
            ("grounded_static_authority", "Singer beside a cracked plaster wall and iron gate, mostly locked waist-up framing, natural side light, minimal gesture, steady eye-line with real street depth."),
            ("slow_walk_and_sing", "Singer walking slowly along a narrow Odessa street, cobblestones and doorways visible, gentle handheld forward drift, readable mouth, passersby in soft background."),
            ("waist_up_declamatory", "Waist-up frame in a lived-in courtyard with hanging laundry and worn stone, chest-to-head composition, measured emphatic gestures, natural daylight, stable cadence."),
            ("close_emotional_pressure", "Close framing near a narrow arch corridor, textured walls and shadow gradients visible, micro-expression emphasis, controlled tension, ambient daylight only."),
            ("wide_heroic_singer_in_world", "Wide shot on seaside embankment wall, singer grounded in real pedestrian space, wind in clothing, deep background horizon, no stage lighting."),
            ("final_restrained_decisive_line", "Singer near a quiet street corner at dusk, still posture, restrained gesture, natural fading light, architectural lines framing a resolved final delivery."),
        ]
        selected = "intimate_close_lipsync"
        if arc_role in {"climax", "pivot"} or "pressure" in emotional_key:
            selected = "close_emotional_pressure"
        elif arc_role in {"release", "afterglow"} or beat_mode in {"release", "aftermath"}:
            selected = "final_restrained_decisive_line"
        elif "wide" in visual_scale or "wide" in location_zone.lower() or "embankment" in location_zone.lower():
            selected = "wide_heroic_singer_in_world"
        elif "walk" in motion_profile or "walk" in beat_mode:
            selected = "slow_walk_and_sing"
        elif "static" in motion_profile:
            selected = "grounded_static_authority"
        elif "waist" in visual_scale or beat_mode in {"performance", "declaration"}:
            selected = "waist_up_declamatory"
        if selected == prev_ia2v_variant:
            for alt_key, _ in candidates:
                if alt_key != prev_ia2v_variant:
                    selected = alt_key
                    break
        prev_ia2v_variant = selected
        for key, clause in candidates:
            if key == selected:
                return clause
        return ""

    rewritten_segments: list[dict[str, Any]] = []
    total_segments = len(segments)
    world_concrete_clauses = [
        "Wide environmental framing in a specific Odessa location: courtyard, archway, street, or seaside with visible depth.",
        "Include physical texture cues: cracked plaster, worn stone, cobblestones, iron railings, laundry lines, sea wind, or weathered walls.",
        "Use natural light behavior and clear composition: foreground/midground/background separation with stable camera intent.",
    ]
    for idx, raw_row in enumerate(segments, start=1):
        row = deepcopy(_safe_dict(raw_row))
        segment_id = str(row.get("segment_id") or row.get("scene_id") or f"seg_{idx:02d}").strip()
        route = str(row.get("route") or _safe_dict(scene_plan_rows.get(segment_id)).get("route") or "").strip().lower()
        scene_row = _safe_dict(scene_plan_rows.get(segment_id))
        core_row = _safe_dict(core_rows.get(segment_id))
        video_prompt = str(row.get("video_prompt") or "").strip()
        deduped_prompt, deduped_changed = _dedupe_prompt_sentences(video_prompt)
        if deduped_changed:
            row["video_prompt"] = deduped_prompt
            video_prompt = deduped_prompt
            video_prompt_deduped_count += 1

        if route == "ia2v":
            clause = _ia2v_variant_clause(segment_id, row)
            if clause and clause.lower() not in video_prompt.lower():
                row["video_prompt"] = _prepend_clause_once(video_prompt, clause)
                video_prompt = str(row.get("video_prompt") or "").strip()
                ia2v_variant_applied_count += 1

        visual_focus_role = str(row.get("visual_focus_role") or scene_row.get("visual_focus_role") or "").strip().lower()
        speaker_role = str(row.get("speaker_role") or scene_row.get("speaker_role") or "").strip().lower()
        primary_role = str(row.get("primary_role") or scene_row.get("primary_role") or "").strip().lower()
        beat_mode = str(row.get("beat_mode") or scene_row.get("beat_mode") or core_row.get("beat_mode") or "").strip().lower()
        world_i2v = bool(
            route == "i2v"
            and (
                beat_mode in world_modes
                or visual_focus_role in {"world", "environment"}
                or primary_role in {"world", "environment"}
                or (not speaker_role and "offscreen" in video_prompt.lower())
            )
        )
        if world_i2v:
            cleaned = video_prompt
            for pattern in singer_conflict_patterns:
                cleaned = pattern.sub("", cleaned)
            cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,;")
            world_clause = "World/cutaway mode: no visible singer or lip-sync framing; keep vocalist offscreen or non-dominant."
            if world_clause.lower() not in cleaned.lower():
                cleaned = f"{world_clause} {cleaned}".strip()
            for clause in world_concrete_clauses:
                if clause.lower() not in cleaned.lower():
                    cleaned = _prepend_clause_once(cleaned, clause)
            if cleaned != video_prompt:
                row["video_prompt"] = cleaned
                world_i2v_conflict_fixed_count += 1

        if idx >= max(1, total_segments - 1):
            closure_clause = (
                "Final closure framing: longer hold, quieter motion, greater distance, and residual presence "
                "(subject leaving frame or empty location after action)."
            )
            for key in ("video_prompt", "photo_prompt"):
                current = str(row.get(key) or "").strip()
                if current and closure_clause.lower() not in current.lower():
                    row[key] = _prepend_clause_once(current, closure_clause)

        rewritten_segments.append(row)

    normalized["segments"] = rewritten_segments
    if _safe_list(normalized.get("scenes")):
        normalized["scenes"] = [deepcopy(row) for row in rewritten_segments]
    return normalized, {
        "scene_prompts_quality_pass_applied": True,
        "scene_prompts_quality_ia2v_variant_applied_count": ia2v_variant_applied_count,
        "scene_prompts_quality_world_i2v_conflict_fixed_count": world_i2v_conflict_fixed_count,
        "scene_prompts_quality_video_prompt_deduped_count": video_prompt_deduped_count,
        "scene_prompts_quality_world_anchor_strengthened": anchor_strengthened,
    }


_SCENE_PROMPTS_DETECH_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bpeak[\-\s]?threshold pocket\b", re.IGNORECASE), "quiet kitchen aftermath"),
    (re.compile(r"\bthreshold pocket\b", re.IGNORECASE), "doorway area"),
    (re.compile(r"\bpeak[\-\s]?threshold\b", re.IGNORECASE), "emotional turning point"),
    (re.compile(r"\broute pocket\b", re.IGNORECASE), "scene moment"),
    (re.compile(r"\btechnical labels?\b", re.IGNORECASE), "on-screen text or UI marks"),
    (re.compile(r"\bcamera jargon\b", re.IGNORECASE), "unnatural camera instructions"),
    (re.compile(r"\btechnical jargon\b", re.IGNORECASE), "unnatural wording"),
    (re.compile(r"\bmetadata\b", re.IGNORECASE), "unwanted text"),
    (re.compile(r"\bdebug\b", re.IGNORECASE), "unwanted text"),
    (re.compile(r"\bpayload\b", re.IGNORECASE), "unwanted text"),
)

_SCENE_PROMPTS_GENERAL_SAFE_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\btechnical\b", re.IGNORECASE), "artificial"),
    (re.compile(r"\bmetadata\b", re.IGNORECASE), "unwanted text"),
    (re.compile(r"\bdebug\b", re.IGNORECASE), "unwanted text"),
    (re.compile(r"\bpayload\b", re.IGNORECASE), "unwanted text"),
    (re.compile(r"\bjargon\b", re.IGNORECASE), "unnatural wording"),
    (re.compile(r"\blabels?\b", re.IGNORECASE), "on-screen text"),
    (re.compile(r"\broute\b", re.IGNORECASE), "path"),
)

_SCENE_PROMPTS_TECHNICAL_FORBIDDEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("peak-threshold pocket", re.compile(r"\bpeak[\-\s]?threshold pocket\b", re.IGNORECASE)),
    ("threshold pocket", re.compile(r"\bthreshold pocket\b", re.IGNORECASE)),
    ("peak threshold", re.compile(r"\bpeak[\-\s]?threshold\b", re.IGNORECASE)),
    ("route pocket", re.compile(r"\broute pocket\b", re.IGNORECASE)),
    ("technical", re.compile(r"\btechnical\b", re.IGNORECASE)),
    ("metadata", re.compile(r"\bmetadata\b", re.IGNORECASE)),
    ("debug", re.compile(r"\bdebug\b", re.IGNORECASE)),
    ("payload", re.compile(r"\bpayload\b", re.IGNORECASE)),
    ("route", re.compile(r"\broute\b", re.IGNORECASE)),
    ("jargon", re.compile(r"\bjargon\b", re.IGNORECASE)),
    ("labels", re.compile(r"\blabels?\b", re.IGNORECASE)),
)

_SCENE_PROMPTS_STRUCTURAL_STRING_SKIP_KEYS: set[str] = {
    "segment_id",
    "scene_id",
    "route",
    "route_type",
    "ltx_mode",
    "speaker_role",
    "primary_role",
    "visual_focus_role",
}


def _sanitize_scene_prompts_text_value(text: str, *, is_negative_prompt: bool = False) -> tuple[str, bool]:
    value = " ".join(str(text or "").strip().split())
    if not value:
        return "", False
    original = value
    for pattern, replacement in _SCENE_PROMPTS_DETECH_REPLACEMENTS:
        value = pattern.sub(replacement, value)
    for pattern, replacement in _SCENE_PROMPTS_GENERAL_SAFE_REPLACEMENTS:
        value = pattern.sub(replacement, value)
    if is_negative_prompt:
        for _, pattern in _SCENE_PROMPTS_TECHNICAL_FORBIDDEN_PATTERNS:
            value = pattern.sub("", value)
        safe_items = ["on-screen text", "UI elements", "watermarks", "artificial overlays", "unnatural wording"]
        existing_items = [item.strip(" ,;") for item in re.split(r"[,;]", value) if item.strip(" ,;")]
        merged: list[str] = []
        for token in [*existing_items, *safe_items]:
            low = token.lower()
            if low not in {m.lower() for m in merged}:
                merged.append(token)
        value = ", ".join(merged)
        value = re.sub(r"\s{2,}", " ", value).strip(" ,;")
        if not value:
            value = "on-screen text, UI elements, watermarks, artificial overlays"
    value = re.sub(r"\s{2,}", " ", value).strip(" ,;")
    return value, value != original


def _scene_prompts_diag_field_for_path(path: tuple[str, ...]) -> str:
    if path == ("photo_prompt",):
        return "photo_prompt"
    if path == ("video_prompt",):
        return "video_prompt"
    if path in {("negative_prompt",), ("negative_video_prompt",)}:
        return "negative_prompt"
    if path == ("prompt_notes", "world_anchor"):
        return "prompt_notes.world_anchor"
    if path == ("prompt_notes", "emotion"):
        return "prompt_notes.emotion"
    if len(path) >= 3 and path[0] == "prompt_notes" and path[1] == "notes":
        return "prompt_notes.notes[]"
    return "nested_strings"


def _sanitize_scene_prompts_segment_strings(
    value: Any,
    *,
    path: tuple[str, ...] = (),
    field_counts: dict[str, int] | None = None,
) -> tuple[Any, bool]:
    counts = field_counts if field_counts is not None else {}
    changed_any = False
    if isinstance(value, str):
        key = path[-1] if path else ""
        rewritten, changed = _sanitize_scene_prompts_text_value(
            value,
            is_negative_prompt=key in {"negative_prompt", "negative_video_prompt"},
        )
        if changed:
            diag_field = _scene_prompts_diag_field_for_path(path)
            counts[diag_field] = int(counts.get(diag_field) or 0) + 1
        return rewritten, changed
    if isinstance(value, list):
        out: list[Any] = []
        for idx, item in enumerate(value):
            rewritten, changed = _sanitize_scene_prompts_segment_strings(item, path=path + (str(idx),), field_counts=counts)
            changed_any = changed_any or changed
            out.append(rewritten)
        return out, changed_any
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if key_str in _SCENE_PROMPTS_STRUCTURAL_STRING_SKIP_KEYS:
                out[key] = item
                continue
            rewritten, changed = _sanitize_scene_prompts_segment_strings(item, path=path + (key_str,), field_counts=counts)
            changed_any = changed_any or changed
            out[key] = rewritten
        return out, changed_any
    return value, False


def _scan_scene_prompt_segment_for_technical_tagging(segment: dict[str, Any]) -> tuple[str, str]:
    def _scan(node: Any, current_path: tuple[str, ...]) -> tuple[str, str]:
        if isinstance(node, str):
            for token, pattern in _SCENE_PROMPTS_TECHNICAL_FORBIDDEN_PATTERNS:
                if pattern.search(node):
                    return token, ".".join(current_path)
            return "", ""
        if isinstance(node, list):
            for idx, item in enumerate(node):
                token, field = _scan(item, current_path + (str(idx),))
                if token:
                    return token, field
            return "", ""
        if isinstance(node, dict):
            for key, item in node.items():
                token, field = _scan(item, current_path + (str(key),))
                if token:
                    return token, field
            return "", ""
        return "", ""

    return _scan(segment, ())


def _postprocess_scene_prompts_technical_tagging(result: dict[str, Any]) -> dict[str, Any]:
    prompts = deepcopy(_safe_dict(result.get("scene_prompts")))
    diagnostics = _safe_dict(result.get("diagnostics"))
    segments = _safe_list(prompts.get("segments"))
    changed_segments: list[str] = []
    field_counts: dict[str, int] = {
        "photo_prompt": 0,
        "video_prompt": 0,
        "negative_prompt": 0,
        "prompt_notes.world_anchor": 0,
        "prompt_notes.emotion": 0,
        "prompt_notes.notes[]": 0,
        "nested_strings": 0,
    }

    sanitized_segments: list[dict[str, Any]] = []
    for segment in segments:
        raw_segment = _safe_dict(segment)
        rewritten_segment, changed = _sanitize_scene_prompts_segment_strings(raw_segment, field_counts=field_counts)
        segment_out = _safe_dict(rewritten_segment)
        if changed:
            changed_segments.append(str(segment_out.get("segment_id") or segment_out.get("scene_id") or "").strip())
        sanitized_segments.append(segment_out)
    prompts["segments"] = sanitized_segments

    token = ""
    field = ""
    tagged_segment_id = ""
    for idx, segment in enumerate(sanitized_segments):
        seg_id = str(_safe_dict(segment).get("segment_id") or _safe_dict(segment).get("scene_id") or f"seg_{idx + 1}").strip()
        token, field = _scan_scene_prompt_segment_for_technical_tagging(_safe_dict(segment))
        if token:
            tagged_segment_id = seg_id
            break

    if token and tagged_segment_id:
        repaired_segments: list[dict[str, Any]] = []
        for segment in sanitized_segments:
            seg = _safe_dict(segment)
            seg_id = str(seg.get("segment_id") or seg.get("scene_id") or "").strip()
            if seg_id != tagged_segment_id:
                repaired_segments.append(seg)
                continue
            repaired_segment, _ = _sanitize_scene_prompts_segment_strings(seg, field_counts=field_counts)
            repaired_segments.append(_safe_dict(repaired_segment))
        prompts["segments"] = repaired_segments
        token = ""
        field = ""
        tagged_segment_id = ""
        for idx, segment in enumerate(repaired_segments):
            seg_id = str(_safe_dict(segment).get("segment_id") or _safe_dict(segment).get("scene_id") or f"seg_{idx + 1}").strip()
            token, field = _scan_scene_prompt_segment_for_technical_tagging(_safe_dict(segment))
            if token:
                tagged_segment_id = seg_id
                break

    non_zero_counts = {k: v for k, v in field_counts.items() if v > 0}
    diagnostics["scene_prompts_de_technicalization_applied"] = bool(changed_segments)
    diagnostics["scene_prompts_de_technicalized_segment_ids"] = [seg for seg in changed_segments if seg]
    diagnostics["scene_prompts_de_technicalized_field_counts"] = non_zero_counts
    diagnostics["scene_prompts_technical_tagging_token"] = token
    diagnostics["scene_prompts_technical_tagging_field"] = field
    diagnostics["scene_prompts_technical_tagging_segment"] = tagged_segment_id

    if token and tagged_segment_id:
        result["validation_error"] = f"technical_tagging:{tagged_segment_id}"
        result["error_code"] = "PROMPTS_TECHNICAL_TAGGING"
    else:
        existing_validation_error = str(result.get("validation_error") or "").strip().lower()
        existing_error_code = str(result.get("error_code") or "").strip().upper()
        existing_error = str(result.get("error") or "").strip().lower()
        if existing_validation_error.startswith("technical_tagging:"):
            result["validation_error"] = ""
        if existing_error_code == "PROMPTS_TECHNICAL_TAGGING":
            result["error_code"] = ""
        if existing_error in {"prompts_technical_tagging", "scene_prompts_validation_failed"}:
            result["error"] = ""

        diag_validation_error = str(diagnostics.get("scene_prompts_validation_error") or "").strip().lower()
        diag_error_code = str(diagnostics.get("scene_prompts_error_code") or "").strip().upper()
        diag_error = str(diagnostics.get("scene_prompts_error") or "").strip().lower()
        if diag_validation_error.startswith("technical_tagging:"):
            diagnostics["scene_prompts_validation_error"] = ""
            diagnostics["validation_error"] = ""
        if diag_error_code == "PROMPTS_TECHNICAL_TAGGING":
            diagnostics["scene_prompts_error_code"] = ""
        if diag_error in {"prompts_technical_tagging", "scene_prompts_validation_failed"}:
            diagnostics["scene_prompts_error"] = ""

    result["scene_prompts"] = prompts
    result["diagnostics"] = diagnostics
    if (not token) and (not tagged_segment_id) and _scene_prompts_result_has_no_blocking_errors(result):
        result["ok"] = True
        result["error"] = ""
    return result


def _apply_scene_prompts_enforcement_result(
    result: dict[str, Any],
    normalized_scene_prompts: dict[str, Any],
    normalized_validation_error: str,
    normalized_diag: dict[str, Any],
) -> dict[str, Any]:
    previous_validation_error = str(result.get("validation_error") or "").strip().lower()
    previous_error_code = str(result.get("error_code") or "").strip().upper()
    previous_error = str(result.get("error") or "").strip().lower()

    result["scene_prompts"] = normalized_scene_prompts

    if normalized_validation_error:
        result["validation_error"] = normalized_validation_error
        if not str(result.get("error_code") or "").strip():
            result["error_code"] = "PROMPTS_IDENTITY_DRIFT"
    else:
        if previous_validation_error.startswith("identity_drift:"):
            result["validation_error"] = ""
        if previous_error_code == "PROMPTS_IDENTITY_DRIFT":
            result["error_code"] = ""
        if previous_error in {"prompts_identity_drift", "scene_prompts_validation_failed"}:
            result["error"] = ""

    result_diag = _safe_dict(result.get("diagnostics"))
    if not normalized_validation_error:
        diag_validation_error = str(result_diag.get("scene_prompts_validation_error") or "").strip().lower()
        diag_error_code = str(result_diag.get("scene_prompts_error_code") or "").strip().upper()
        diag_error = str(result_diag.get("scene_prompts_error") or "").strip().lower()

        if previous_validation_error.startswith("identity_drift:") or diag_validation_error.startswith("identity_drift:"):
            result_diag["scene_prompts_validation_error"] = ""
            result_diag["validation_error"] = ""

        if previous_error_code == "PROMPTS_IDENTITY_DRIFT" or diag_error_code == "PROMPTS_IDENTITY_DRIFT":
            result_diag["scene_prompts_error_code"] = ""

        if previous_error in {"prompts_identity_drift", "scene_prompts_validation_failed"} or diag_error in {
            "prompts_identity_drift",
            "scene_prompts_validation_failed",
        }:
            result_diag["scene_prompts_error"] = ""

    result_diag.update(normalized_diag)
    result["diagnostics"] = result_diag
    return result


def _build_scene_prompts_retry_feedback(validation_error: str, error_code: str) -> str:
    base = (
        f"Previous output invalid: validation_error={validation_error}; "
        f"error_code={error_code or 'PROMPTS_SCHEMA_INVALID'}"
    )
    if validation_error.strip().lower().startswith("identity_drift:"):
        return (
            "Your previous prompts weakened the character identity anchor. Rewrite all affected segments so that every "
            "prompt for character_1 explicitly states that it is the same current character_1 from the connected "
            "character_1 reference, preserving the same face, age impression, body proportions, hairstyle, clothing, "
            "and silhouette. "
            "Use cinematic language only."
        )
    return base


def _is_all_lipsync_override_mode(
    *,
    total_scenes: int,
    creative_config: dict[str, Any],
    target_budget: dict[str, int],
) -> bool:
    if total_scenes <= 0:
        return False
    target_i2v = int(target_budget.get("i2v") or 0)
    target_ia2v = int(target_budget.get("ia2v") or 0)
    target_first_last = int(target_budget.get("first_last") or 0)
    if target_i2v == 0 and target_first_last == 0 and target_ia2v == total_scenes:
        return True

    lipsync_ratio = _clamp_ratio(creative_config.get("lipsync_ratio"), 0.25)
    i2v_ratio = _clamp_ratio(creative_config.get("i2v_ratio"), 0.5)
    first_last_ratio = _clamp_ratio(creative_config.get("first_last_ratio"), 0.25)
    return lipsync_ratio >= 0.999 and i2v_ratio <= 0.001 and first_last_ratio <= 0.001


def _validate_scene_plan_route_budget(
    *,
    package: dict[str, Any],
    scene_plan: dict[str, Any],
    diagnostics: dict[str, Any],
) -> tuple[bool, str, dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    director_config = _safe_dict(input_pkg.get("director_config"))
    scene_rows = _scene_plan_rows_for_validation(scene_plan)
    route_counts = {"i2v": 0, "ia2v": 0, "first_last": 0}
    invalid_route_count = 0
    longest_lipsync_streak = 0
    current_lipsync_streak = 0
    for row in scene_rows:
        route = str(row.get("route") or "").strip().lower()
        if route in route_counts:
            route_counts[route] += 1
        else:
            invalid_route_count += 1
        if route == "ia2v":
            current_lipsync_streak += 1
            longest_lipsync_streak = max(longest_lipsync_streak, current_lipsync_streak)
        else:
            current_lipsync_streak = 0

    expected_scene_count = _expected_scene_count_from_package(package)
    target_budget = _compute_route_budget_for_total(expected_scene_count, creative_config)
    mode = str(creative_config.get("route_mix_mode") or "auto").strip().lower() or "auto"
    preset_name = str(creative_config.get("route_strategy_preset") or "").strip().lower()
    original_targets = {
        "i2v": max(0, int(_safe_dict(creative_config.get("route_targets_per_block")).get("i2v") or 0)),
        "ia2v": max(0, int(_safe_dict(creative_config.get("route_targets_per_block")).get("ia2v") or 0)),
        "first_last": max(0, int(_safe_dict(creative_config.get("route_targets_per_block")).get("first_last") or 0)),
    }
    resolved_from = "audio_map_segments_count" if preset_name == "no_first_last_50_50_0" else "creative_config"
    strict_no_first_last_50_50_0 = preset_name == "no_first_last_50_50_0"
    all_lipsync_override = _is_all_lipsync_override_mode(
        total_scenes=expected_scene_count,
        creative_config=creative_config,
        target_budget=target_budget,
    )
    validation_mode = "all_lipsync_override" if all_lipsync_override else "mixed"
    max_consecutive = int(creative_config.get("max_consecutive_lipsync") or 2)
    tolerance = 0 if strict_no_first_last_50_50_0 else (1 if expected_scene_count >= 6 else 0)
    streak_guard_relaxed = all_lipsync_override
    lipsync_streak_warning = "all_lipsync_override_active" if streak_guard_relaxed else ""

    first_last_forbidden = bool(
        diagnostics.get("scene_plan_first_last_forbidden")
        or _safe_dict(scene_plan.get("diagnostics")).get("scene_plan_first_last_forbidden")
        or True
    )
    first_last_missing_is_ok = bool(first_last_forbidden and int(route_counts.get("first_last") or 0) == 0)
    old_first_last_requirement_suppressed = False

    route_budget_mode = "creative_config_soft"
    director_ratio_raw = director_config.get("ia2v_ratio")
    director_ratio = None
    if director_ratio_raw is not None:
        director_ratio = _clamp_ratio(director_ratio_raw, 0.0)
        route_budget_mode = "director_config_hard"
    elif bool(creative_config.get("route_strategy_active")) and not bool(creative_config.get("targets_are_soft")):
        route_budget_mode = "creative_config_hard"

    audio_segments = [_safe_dict(seg) for seg in _safe_list(_safe_dict(package.get("audio_map")).get("segments"))]
    vocal_candidate_count = sum(1 for seg in audio_segments if _is_vocal_lipsync_candidate_segment(seg))

    expected_ia2v_director = 0
    ia2v_delta = 0
    route_budget_tolerance = 1
    if route_budget_mode == "director_config_hard" and director_ratio is not None:
        expected_ia2v_director = int(round(expected_scene_count * director_ratio))
        expected_ia2v_director = min(max(0, expected_ia2v_director), max(0, expected_scene_count), vocal_candidate_count or expected_scene_count)
        ia2v_delta = abs(int(route_counts.get("ia2v") or 0) - expected_ia2v_director)
    else:
        route_budget_tolerance = tolerance

    errors: list[str] = []
    if invalid_route_count > 0:
        errors.append(f"route distribution invalid: unsupported route count={invalid_route_count}")
    if expected_scene_count > 0 and len(scene_rows) != expected_scene_count:
        errors.append(f"scene count mismatch count={len(scene_rows)} expected={expected_scene_count}")
    if mode == "custom" and expected_scene_count > 0 and sum(route_counts.values()) <= 0:
        errors.append("route distribution invalid: no supported routes found")
    if longest_lipsync_streak > max_consecutive and not streak_guard_relaxed:
        errors.append(f"too many consecutive lipsync scenes: streak={longest_lipsync_streak} max={max_consecutive}")

    route_budget_ok = True
    target_first_last = int(target_budget.get("first_last") or 0)
    first_last_actual = int(route_counts.get("first_last") or 0)
    if first_last_forbidden:
        if first_last_actual > 0:
            route_budget_ok = False
            errors.append(f"first_last forbidden in clip mode but count={first_last_actual}")
        elif mode == "auto" and target_first_last > 0 and expected_scene_count >= 4:
            old_first_last_requirement_suppressed = True
    elif mode == "auto" and target_first_last > 0 and first_last_actual <= 0 and expected_scene_count >= 4:
        errors.append("first_last share missing for visual variety")

    if route_budget_mode == "director_config_hard":
        if ia2v_delta > route_budget_tolerance:
            route_budget_ok = False
            errors.append(
                f"director_config ia2v count={route_counts.get('ia2v', 0)} target≈{expected_ia2v_director} tolerance={route_budget_tolerance}"
            )
    elif route_budget_mode == "creative_config_hard":
        for route_name in ("ia2v", "i2v", "first_last"):
            if abs(route_counts.get(route_name, 0) - target_budget.get(route_name, 0)) > tolerance:
                route_budget_ok = False
                errors.append(
                    f"route {route_name} count={route_counts.get(route_name, 0)} target≈{target_budget.get(route_name, 0)}"
                )

    if errors and route_budget_ok:
        route_budget_ok = False

    duration_sec = float(input_pkg.get("audio_duration_sec") or 0.0)
    feedback_prefix = (
        "short clip default expects mixed route distribution near 25/50/25"
        if mode == "auto" and duration_sec > 0 and duration_sec <= 45
        else (
            "route distribution violated custom target ratios"
            if mode == "custom"
            else "route distribution violated creative_config doctrine"
        )
    )
    feedback = f"{feedback_prefix}; " + "; ".join(errors) if errors else ""
    if strict_no_first_last_50_50_0 and errors:
        feedback = (
            "route distribution must follow preset no_first_last_50_50_0 as closely as possible after route-validity checks: "
            f"total={expected_scene_count}, i2v={int(target_budget.get('i2v') or 0)}, "
            f"ia2v={int(target_budget.get('ia2v') or 0)}, first_last={target_first_last}; "
            "keep ia2v only on valid vocal windows with character_1 mouth/face visibility; "
            "downgrade invalid requested ia2v rows to i2v instead of faking lipsync; no first_last scenes."
        )
    details = {
        "target_route_mix": target_budget,
        "actual_route_mix": route_counts,
        "max_consecutive_lipsync": max_consecutive,
        "longest_lipsync_streak": longest_lipsync_streak,
        "route_mix_mode": mode,
        "route_budget_validation_mode": validation_mode,
        "all_lipsync_mode": all_lipsync_override,
        "lipsync_streak_guard_relaxed": streak_guard_relaxed,
        "lipsync_streak_warning": lipsync_streak_warning,
        "creative_config": creative_config,
        "route_strategy_preset": preset_name,
        "strict_preset_enforced": strict_no_first_last_50_50_0,
        "route_budget_original_targets": original_targets,
        "route_budget_resolved_scene_count": expected_scene_count,
        "route_budget_resolved_targets": target_budget,
        "route_budget_resolved_from": resolved_from,
        "route_budget_preset": preset_name,
        "route_budget_mode": route_budget_mode,
        "route_budget_tolerance": route_budget_tolerance,
        "scene_plan_first_last_forbidden": first_last_forbidden,
        "scene_plan_first_last_missing_is_ok": first_last_missing_is_ok,
        "scene_plan_route_budget_old_first_last_requirement_suppressed": old_first_last_requirement_suppressed,
        "director_config_ia2v_ratio": director_ratio,
        "route_budget_expected_ia2v": expected_ia2v_director,
        "route_budget_ia2v_delta": ia2v_delta,
        "route_budget_vocal_candidate_cap": vocal_candidate_count,
    }
    return route_budget_ok, feedback, details


def _extract_user_hard_route_map(input_pkg: dict[str, Any]) -> dict[str, str]:
    creative_config = _safe_dict(input_pkg.get("creative_config"))
    hard_map_raw = _safe_dict(
        creative_config.get("route_assignments_by_segment")
        or creative_config.get("routeAssignmentsBySegment")
    )
    return {
        str(segment_id).strip(): str(route).strip().lower()
        for segment_id, route in hard_map_raw.items()
        if str(segment_id).strip() and str(route).strip().lower() in {"i2v", "ia2v", "first_last"}
    }


def _is_vocal_lipsync_candidate_segment(segment: dict[str, Any]) -> bool:
    row = _safe_dict(segment)
    transcript_slice = str(row.get("transcript_slice") or row.get("transcript") or "").strip()
    if _is_instrumental_tail_marker_text(transcript_slice):
        return False
    if bool(
        row.get("is_lip_sync_candidate")
        or row.get("is_lipsync_candidate")
        or row.get("lip_sync_candidate")
        or row.get("lipSyncCandidate")
        or row.get("singing_readiness_required")
    ):
        return True
    return bool(transcript_slice)


def _segment_duration_sec(segment: dict[str, Any]) -> float:
    row = _safe_dict(segment)
    for key in ("duration_sec", "duration", "segment_duration_sec"):
        try:
            value = float(row.get(key))
            if value > 0:
                return value
        except Exception:
            continue
    try:
        t0 = float(row.get("t0"))
        t1 = float(row.get("t1"))
        if t1 > t0:
            return t1 - t0
    except Exception:
        pass
    return 0.0


def build_no_first_last_50_50_hard_route_map(package: dict[str, Any]) -> dict[str, str]:
    audio_segments = _safe_list(_safe_dict(package.get("audio_map")).get("segments"))
    segment_ids = [
        str(_safe_dict(segment).get("segment_id") or f"seg_{idx:02d}").strip()
        for idx, segment in enumerate(audio_segments, start=1)
    ]
    segment_ids = [segment_id for segment_id in segment_ids if segment_id]
    scene_count = len(segment_ids)
    if scene_count <= 0:
        return {}
    ia2v_target = int(math.ceil(scene_count / 2))
    max_consecutive_lipsync = 2

    candidate_scores: dict[str, float] = {}
    vocal_candidates: list[str] = []
    for idx, segment in enumerate(audio_segments):
        segment_id = segment_ids[idx] if idx < len(segment_ids) else ""
        if not segment_id:
            continue
        if not _is_vocal_lipsync_candidate_segment(_safe_dict(segment)):
            continue
        vocal_candidates.append(segment_id)
        duration_sec = _segment_duration_sec(_safe_dict(segment))
        transcript_blob = " ".join(
            [
                str(_safe_dict(segment).get("transcript_slice") or ""),
                str(_safe_dict(segment).get("emotion_hint") or ""),
                str(_safe_dict(segment).get("story_beat") or ""),
            ]
        ).lower()
        score = 0.0
        if idx == 0:
            score += 12.0
        if idx == scene_count - 1:
            score += 10.0
        if idx % 2 == 0:
            score += 4.0
        if 3.0 <= duration_sec <= 7.0:
            score += 3.5
        elif duration_sec > 7.0 and idx != 0:
            score -= 1.5
        if bool(_safe_dict(segment).get("release_candidate")):
            score += 2.5
        delivery_mode = str(_safe_dict(segment).get("delivery_mode") or "").strip().lower()
        rhythmic_anchor = str(_safe_dict(segment).get("rhythmic_anchor") or "").strip().lower()
        if delivery_mode in {"final", "assertive"}:
            score += 1.5
        if rhythmic_anchor == "drop":
            score += 1.5
        if bool(_safe_dict(segment).get("stillness_candidate")) and delivery_mode == "intimate":
            score -= 1.0
        if any(token in transcript_blob for token in ("peak", "climax", "release", "hook", "drop", "emotion", "cry", "love", "heart")):
            score += 2.5
        candidate_scores[segment_id] = score

    ia2v_selected: list[str] = []
    if vocal_candidates:
        first_segment_id = segment_ids[0]
        last_segment_id = segment_ids[-1]
        if first_segment_id in vocal_candidates:
            ia2v_selected.append(first_segment_id)
        if len(ia2v_selected) < ia2v_target and last_segment_id in vocal_candidates and last_segment_id not in ia2v_selected:
            ia2v_selected.append(last_segment_id)
        ranked_candidates = sorted(vocal_candidates, key=lambda seg_id: (-candidate_scores.get(seg_id, 0.0), segment_ids.index(seg_id)))
        for segment_id in ranked_candidates:
            if len(ia2v_selected) >= ia2v_target:
                break
            if segment_id in ia2v_selected:
                continue
            idx = segment_ids.index(segment_id)
            left_selected = idx > 0 and segment_ids[idx - 1] in ia2v_selected
            right_selected = idx + 1 < scene_count and segment_ids[idx + 1] in ia2v_selected
            if left_selected and right_selected and max_consecutive_lipsync <= 2:
                continue
            ia2v_selected.append(segment_id)

    if len(ia2v_selected) < ia2v_target:
        for idx, segment_id in enumerate(segment_ids):
            if len(ia2v_selected) >= ia2v_target:
                break
            if segment_id in ia2v_selected:
                continue
            if idx % 2 == 0:
                ia2v_selected.append(segment_id)
    if len(ia2v_selected) < ia2v_target:
        for segment_id in segment_ids:
            if len(ia2v_selected) >= ia2v_target:
                break
            if segment_id not in ia2v_selected:
                ia2v_selected.append(segment_id)

    ia2v_set = set(ia2v_selected[:ia2v_target])
    return {segment_id: ("ia2v" if segment_id in ia2v_set else "i2v") for segment_id in segment_ids}


def _scene_plan_routes_by_segment(scene_plan: dict[str, Any]) -> dict[str, str]:
    routes: dict[str, str] = {}
    for idx, row in enumerate(_scene_plan_rows_for_validation(scene_plan), start=1):
        route_row = _safe_dict(row)
        segment_id = str(route_row.get("segment_id") or route_row.get("scene_id") or f"seg_{idx:02d}").strip()
        route = str(route_row.get("route") or "").strip().lower()
        if segment_id and route in {"i2v", "ia2v", "first_last"}:
            routes[segment_id] = route
    return routes


def _fallback_scene_plan_route_fill(scene_plan: dict[str, Any]) -> dict[str, str]:
    fallback_map: dict[str, str] = {}
    for idx, row in enumerate(_scene_plan_rows_for_validation(scene_plan), start=1):
        route_row = _safe_dict(row)
        segment_id = str(route_row.get("segment_id") or route_row.get("scene_id") or f"seg_{idx:02d}").strip()
        if not segment_id:
            continue
        route = str(route_row.get("route") or "").strip().lower()
        if route in {"i2v", "ia2v", "first_last"}:
            continue
        story_beat_type = str(route_row.get("story_beat_type") or "").strip().lower()
        singing_required = bool(route_row.get("singing_readiness_required"))
        if story_beat_type == "vocal_emotion" or singing_required:
            fallback_map[segment_id] = "ia2v"
        else:
            fallback_map[segment_id] = "i2v"
    return fallback_map


def _resolve_scene_plan_route_locks(
    *,
    package: dict[str, Any],
    scene_plan: dict[str, Any],
    previous_scene_plan: dict[str, Any],
) -> tuple[dict[str, str], str]:
    input_pkg = _safe_dict(package.get("input"))
    scene_rows = _scene_plan_rows_for_validation(scene_plan)
    if not scene_rows:
        return {}, ""
    hard_map = _extract_user_hard_route_map(input_pkg)
    if hard_map:
        return hard_map, "creative_config.route_assignments_by_segment"
    gemini_routes = _scene_plan_routes_by_segment(scene_plan)
    if len(gemini_routes) == len(scene_rows):
        return gemini_routes, "gemini_semantic_route_selection"
    if gemini_routes:
        fallback_map = _fallback_scene_plan_route_fill(scene_plan)
        merged = dict(gemini_routes)
        merged.update(fallback_map)
        return merged, "fallback_backend_route_fill"
    return _fallback_scene_plan_route_fill(scene_plan), "fallback_backend_route_fill"


def _preferred_validated_route_locks(scene_plan: dict[str, Any], requested_route_locks: dict[str, str]) -> tuple[dict[str, str], str]:
    rows = _scene_plan_rows_for_validation(scene_plan)
    if not rows:
        return dict(requested_route_locks), ""
    validated = {
        str(segment_id).strip(): str(route).strip().lower()
        for segment_id, route in _safe_dict(scene_plan.get("route_locks_by_segment")).items()
        if str(segment_id).strip() and str(route).strip().lower() in {"i2v", "ia2v", "first_last"}
    }
    if validated and len(validated) == len(rows):
        return validated, "scene_plan_validated_routes"
    current_rows = _scene_plan_routes_by_segment(scene_plan)
    if current_rows and len(current_rows) == len(rows):
        return current_rows, "scene_plan_row_routes"
    return dict(requested_route_locks), ""


def _apply_scene_plan_route_locks(
    scene_plan: dict[str, Any],
    route_locks: dict[str, str],
    *,
    overwrite_existing: bool = True,
) -> tuple[dict[str, Any], dict[str, int]]:
    normalized = deepcopy(_safe_dict(scene_plan))
    locks = {str(k).strip(): str(v).strip().lower() for k, v in _safe_dict(route_locks).items() if str(k).strip()}
    for field in ("scenes", "storyboard"):
        rows = [deepcopy(_safe_dict(row)) for row in _safe_list(normalized.get(field)) if isinstance(row, dict)]
        rewritten_rows: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            segment_id = str(row.get("segment_id") or row.get("scene_id") or f"seg_{idx:02d}").strip()
            locked_route = str(locks.get(segment_id) or "").strip().lower()
            current_route = str(row.get("route") or "").strip().lower()
            should_apply_lock = overwrite_existing or current_route not in {"i2v", "ia2v", "first_last"}
            if locked_route in {"i2v", "ia2v", "first_last"} and should_apply_lock:
                row["route"] = locked_route
            rewritten_rows.append(row)
        if rewritten_rows:
            normalized[field] = rewritten_rows
    normalized["route_locks_by_segment"] = locks
    normalized["route_mix_summary"] = _scene_plan_route_counts(normalized)
    return normalized, _scene_plan_route_counts(normalized)


def _repair_scene_plan_final_semantics(
    *,
    package: dict[str, Any],
    scene_plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    normalized = deepcopy(_safe_dict(scene_plan))
    if not normalized:
        return normalized, {"rows_updated": 0, "world_rows_repaired": 0, "primary_role_filled": 0}

    core_rows_by_segment: dict[str, dict[str, Any]] = {
        str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(_safe_dict(package.get("story_core")).get("narrative_segments"))
        if str(_safe_dict(row).get("segment_id") or "").strip()
    }
    role_rows_by_segment: dict[str, dict[str, Any]] = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(_safe_dict(package.get("role_plan")).get("scene_casting"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }

    world_rows_repaired = 0
    primary_role_filled = 0
    rows_updated = 0
    route_lock_updates: dict[str, str] = {}
    world_modes = {"world_observation", "world_pressure", "threshold", "social_texture", "aftermath", "release", "transition"}

    def _upstream_primary_role(segment_id: str, row: dict[str, Any]) -> str:
        core_row = _safe_dict(core_rows_by_segment.get(segment_id))
        beat_primary_subject = _canonical_subject_id(core_row.get("beat_primary_subject"))
        beat_mode = str(core_row.get("beat_mode") or "").strip().lower()
        hero_world_mode = str(core_row.get("hero_world_mode") or "").strip().lower()
        if beat_primary_subject == "world" or hero_world_mode == "world_foreground" or beat_mode in world_modes:
            return "world"
        if beat_primary_subject:
            return beat_primary_subject
        role_primary = _canonical_subject_id(_safe_dict(role_rows_by_segment.get(segment_id)).get("primary_role"))
        if role_primary:
            return role_primary
        return _canonical_subject_id(row.get("primary_role"))

    for field in ("storyboard", "scenes"):
        rows = [deepcopy(_safe_dict(row)) for row in _safe_list(normalized.get(field)) if isinstance(row, dict)]
        if not rows:
            continue
        rewritten_rows: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, start=1):
            segment_id = str(row.get("segment_id") or row.get("scene_id") or f"seg_{idx:02d}").strip()
            if not segment_id:
                rewritten_rows.append(row)
                continue
            route = str(row.get("route") or "").strip().lower()
            visual_focus_role = _canonical_subject_id(row.get("visual_focus_role"))
            speaker_role = _canonical_subject_id(row.get("speaker_role"))
            lip_sync_allowed = bool(row.get("lip_sync_allowed"))
            primary_role = _canonical_subject_id(row.get("primary_role"))
            resolved_primary = _upstream_primary_role(segment_id, row)
            changed = False

            if resolved_primary == "world":
                if primary_role != "world":
                    row["primary_role"] = "world"
                    changed = True
                if visual_focus_role != "world":
                    row["visual_focus_role"] = "world"
                    changed = True
                if route != "i2v":
                    row["route"] = "i2v"
                    route_lock_updates[segment_id] = "i2v"
                    changed = True
                if speaker_role:
                    row["speaker_role"] = ""
                    changed = True
                if bool(row.get("lip_sync_allowed")):
                    row["lip_sync_allowed"] = False
                    changed = True
                if bool(row.get("mouth_visible_required")):
                    row["mouth_visible_required"] = False
                    changed = True
                if changed:
                    world_rows_repaired += 1
            elif (
                not primary_role
                and route == "ia2v"
                and (visual_focus_role == "character_1" or speaker_role == "character_1" or lip_sync_allowed or resolved_primary == "character_1")
            ):
                row["primary_role"] = "character_1"
                primary_role_filled += 1
                changed = True

            if changed:
                rows_updated += 1
            rewritten_rows.append(row)
        normalized[field] = rewritten_rows

    if route_lock_updates:
        existing_locks = {
            str(k).strip(): str(v).strip().lower()
            for k, v in _safe_dict(normalized.get("route_locks_by_segment")).items()
            if str(k).strip()
        }
        existing_locks.update(route_lock_updates)
        normalized["route_locks_by_segment"] = existing_locks
    normalized["route_mix_summary"] = _scene_plan_route_counts(normalized)
    return normalized, {
        "rows_updated": rows_updated,
        "world_rows_repaired": world_rows_repaired,
        "primary_role_filled": primary_role_filled,
    }


def _sync_scene_plan_storyboard_mirror(
    scene_plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, int]]:
    normalized = deepcopy(_safe_dict(scene_plan))
    canonical_rows = _safe_list(normalized.get("storyboard"))
    source = "storyboard"
    if not canonical_rows:
        canonical_rows = _safe_list(normalized.get("scenes"))
        source = "scenes"
    if not canonical_rows:
        canonical_rows = _safe_list(normalized.get("segments"))
        source = "segments"

    canonical = [deepcopy(_safe_dict(row)) for row in canonical_rows if isinstance(row, dict)]
    if not canonical:
        return normalized, {"synced": 0, "rows": 0, "source": source}

    current_storyboard = [deepcopy(_safe_dict(row)) for row in _safe_list(normalized.get("storyboard")) if isinstance(row, dict)]
    current_scenes = [deepcopy(_safe_dict(row)) for row in _safe_list(normalized.get("scenes")) if isinstance(row, dict)]
    synced = int(current_storyboard != canonical) + int(current_scenes != canonical)
    normalized["storyboard"] = [deepcopy(row) for row in canonical]
    normalized["scenes"] = [deepcopy(row) for row in canonical]
    normalized["route_mix_summary"] = _scene_plan_route_counts(normalized)
    return normalized, {"synced": synced, "rows": len(canonical), "source": source}


def _collect_scene_plan_route_semantic_mismatches(scene_plan: dict[str, Any]) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []
    for idx, row in enumerate(_scene_plan_rows_for_validation(scene_plan), start=1):
        route_row = _safe_dict(row)
        segment_id = str(route_row.get("segment_id") or route_row.get("scene_id") or f"seg_{idx:02d}").strip()
        route = str(route_row.get("route") or "").strip().lower()
        story_beat_type = str(route_row.get("story_beat_type") or "").strip().lower()
        object_action_allowed = bool(route_row.get("object_action_allowed"))
        singing_required = bool(route_row.get("singing_readiness_required"))
        if route == "ia2v" and story_beat_type == "physical_event":
            mismatches.append(
                {"segment_id": segment_id, "route": route, "story_beat_type": story_beat_type, "reason": "ia2v_for_physical_event"}
            )
        if route == "ia2v" and object_action_allowed and not singing_required:
            mismatches.append(
                {"segment_id": segment_id, "route": route, "story_beat_type": story_beat_type, "reason": "ia2v_with_object_action_and_no_singing_readiness"}
            )
        if route == "i2v" and story_beat_type == "vocal_emotion":
            mismatches.append(
                {"segment_id": segment_id, "route": route, "story_beat_type": story_beat_type, "reason": "i2v_for_vocal_emotion"}
            )
        if route == "i2v" and singing_required:
            mismatches.append(
                {"segment_id": segment_id, "route": route, "story_beat_type": story_beat_type, "reason": "i2v_with_singing_readiness_required"}
            )
    return mismatches


def _rebalance_scene_plan_routes_after_validity_repairs(
    *,
    package: dict[str, Any],
    scene_plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    normalized = deepcopy(_safe_dict(scene_plan))
    rows = _scene_plan_rows_for_validation(normalized)
    if not rows:
        return normalized, {"attempted": False, "upgraded": 0, "missing_ia2v": 0, "target": {}, "actual_before": {}, "actual_after": {}}

    input_pkg = _safe_dict(package.get("input"))
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    expected_scene_count = _expected_scene_count_from_package(package)
    target_budget = _compute_route_budget_for_total(expected_scene_count, creative_config)
    actual_before = _scene_plan_route_counts(normalized)
    missing_ia2v = max(0, int(target_budget.get("ia2v") or 0) - int(actual_before.get("ia2v") or 0))
    spare_i2v = max(0, int(actual_before.get("i2v") or 0) - int(target_budget.get("i2v") or 0))
    needed_upgrades = min(missing_ia2v, spare_i2v)
    if needed_upgrades <= 0:
        return normalized, {
            "attempted": False,
            "upgraded": 0,
            "missing_ia2v": missing_ia2v,
            "target": target_budget,
            "actual_before": actual_before,
            "actual_after": actual_before,
            "upgraded_segment_ids": [],
        }

    max_consecutive = int(creative_config.get("max_consecutive_lipsync") or 2)
    all_lipsync_override = _is_all_lipsync_override_mode(
        total_scenes=expected_scene_count,
        creative_config=creative_config,
        target_budget=target_budget,
    )
    world_beat_types = {"world_observation", "world_pressure", "threshold", "social_texture", "aftermath", "release", "transition", "cutaway"}
    performance_beat_types = {"vocal_emotion", "performance", "singer_performance"}

    working_routes: list[str] = [str(_safe_dict(row).get("route") or "").strip().lower() for row in rows]

    def _longest_lipsync_streak(route_values: list[str]) -> int:
        longest = 0
        current = 0
        for value in route_values:
            if value == "ia2v":
                current += 1
                longest = max(longest, current)
            else:
                current = 0
        return longest

    def _candidate_score(row: dict[str, Any], idx: int) -> tuple[int, int, int]:
        route_row = _safe_dict(row)
        story_beat_type = str(route_row.get("story_beat_type") or "").strip().lower()
        primary_role = _canonical_subject_id(route_row.get("primary_role"))
        visual_focus_role = _canonical_subject_id(route_row.get("visual_focus_role"))
        speaker_role = _canonical_subject_id(route_row.get("speaker_role"))
        singing_required = bool(route_row.get("singing_readiness_required"))
        lip_sync_allowed = bool(route_row.get("lip_sync_allowed"))
        mouth_visible_required = bool(route_row.get("mouth_visible_required"))
        object_action_allowed = bool(route_row.get("object_action_allowed"))

        if working_routes[idx] != "i2v":
            return (-1, -1, -idx)
        if visual_focus_role != "character_1":
            return (-1, -1, -idx)
        if story_beat_type in world_beat_types:
            return (-1, -1, -idx)
        if primary_role in {"world", "environment"}:
            return (-1, -1, -idx)
        if object_action_allowed and not singing_required:
            return (-1, -1, -idx)

        singer_centered = bool(primary_role == "character_1" or speaker_role == "character_1")
        performance_ready = bool(
            singing_required
            or lip_sync_allowed
            or mouth_visible_required
            or story_beat_type in performance_beat_types
        )
        if not singer_centered and not performance_ready:
            return (-1, -1, -idx)

        trial_routes = list(working_routes)
        trial_routes[idx] = "ia2v"
        if not all_lipsync_override and _longest_lipsync_streak(trial_routes) > max_consecutive:
            return (-1, -1, -idx)

        score = 0
        if story_beat_type in performance_beat_types:
            score += 5
        if singing_required:
            score += 4
        if primary_role == "character_1":
            score += 3
        if speaker_role == "character_1":
            score += 2
        if lip_sync_allowed or mouth_visible_required:
            score += 1
        neighbor_penalty = int((idx > 0 and working_routes[idx - 1] == "ia2v") or (idx + 1 < len(working_routes) and working_routes[idx + 1] == "ia2v"))
        return (score, -neighbor_penalty, -idx)

    upgraded_segment_ids: list[str] = []
    for _ in range(needed_upgrades):
        scored: list[tuple[tuple[int, int, int], int, str]] = []
        for idx, row in enumerate(rows):
            segment_id = str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
            if not segment_id:
                continue
            score = _candidate_score(_safe_dict(row), idx)
            if score[0] < 0:
                continue
            scored.append((score, idx, segment_id))
        if not scored:
            break
        scored.sort(reverse=True)
        _, best_idx, best_segment_id = scored[0]
        working_routes[best_idx] = "ia2v"
        upgraded_segment_ids.append(best_segment_id)

    if upgraded_segment_ids:
        upgraded_set = set(upgraded_segment_ids)
        for field in ("scenes", "storyboard"):
            rewritten: list[dict[str, Any]] = []
            for idx, raw in enumerate(_safe_list(normalized.get(field)), start=1):
                row = deepcopy(_safe_dict(raw))
                segment_id = str(row.get("segment_id") or row.get("scene_id") or f"seg_{idx:02d}").strip()
                if segment_id in upgraded_set:
                    row["route"] = "ia2v"
                rewritten.append(row)
            if rewritten:
                normalized[field] = rewritten
        route_locks = {
            str(k).strip(): str(v).strip().lower()
            for k, v in _safe_dict(normalized.get("route_locks_by_segment")).items()
            if str(k).strip()
        }
        for segment_id in upgraded_segment_ids:
            route_locks[segment_id] = "ia2v"
        normalized["route_locks_by_segment"] = route_locks

    normalized["route_mix_summary"] = _scene_plan_route_counts(normalized)
    actual_after = _scene_plan_route_counts(normalized)
    return normalized, {
        "attempted": True,
        "upgraded": len(upgraded_segment_ids),
        "missing_ia2v": missing_ia2v,
        "target": target_budget,
        "actual_before": actual_before,
        "actual_after": actual_after,
        "upgraded_segment_ids": upgraded_segment_ids,
        "max_consecutive_lipsync": max_consecutive,
        "all_lipsync_override": all_lipsync_override,
    }


def _scene_plan_snapshot_restore_is_safe(
    *,
    package: dict[str, Any],
    previous_scene_plan: dict[str, Any],
    resolved_target_budget: dict[str, Any],
) -> bool:
    if not _has_valid_scene_plan_payload(previous_scene_plan):
        return False
    expected_segment_ids = _expected_scene_segment_ids_from_package(package)
    if not expected_segment_ids:
        return False
    previous_rows = _scene_plan_rows_for_validation(previous_scene_plan)
    previous_segment_ids = [
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
        for row in previous_rows
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    ]
    if previous_segment_ids != expected_segment_ids:
        return False
    previous_mix = _scene_plan_route_counts(previous_scene_plan)
    target_mix = {
        "i2v": int(_safe_dict(resolved_target_budget).get("i2v") or 0),
        "ia2v": int(_safe_dict(resolved_target_budget).get("ia2v") or 0),
        "first_last": int(_safe_dict(resolved_target_budget).get("first_last") or 0),
    }
    if previous_mix != target_mix:
        return False
    current_signature = _current_scenario_input_signature(package)
    previous_signature = str(_safe_dict(previous_scene_plan).get("created_for_signature") or "")
    if current_signature and previous_signature and current_signature != previous_signature:
        return False
    return True


def _resolve_audio_semantic_source_type(input_pkg: dict[str, Any]) -> str:
    lyrics_text = str(input_pkg.get("lyrics_text") or "").strip()
    transcript_text = str(input_pkg.get("transcript_text") or "").strip()
    spoken_text_hint = str(input_pkg.get("spoken_text_hint") or "").strip()
    if lyrics_text:
        return "lyric_vocal"
    if transcript_text or spoken_text_hint:
        return "spoken_music"
    return "mixed_voice_music"


def _extract_mime_type(url: str, headers: dict[str, str], data: bytes) -> str:
    header_mime = str(headers.get("content-type") or "").split(";")[0].strip().lower()
    if header_mime.startswith("image/"):
        return header_mime
    guessed_from_url, _ = mimetypes.guess_type(url)
    if guessed_from_url and guessed_from_url.startswith("image/"):
        return guessed_from_url
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _resolve_reference_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https"}:
        return raw
    if raw.startswith("//"):
        return f"http:{raw}"
    base = str(settings.PUBLIC_BASE_URL).rstrip("/")
    if raw.startswith("/"):
        return f"{base}{raw}"
    return f"{base}/{raw}"


def _extract_local_static_asset_relative_path(url: str) -> str | None:
    raw = (url or "").strip()
    if not raw:
        return None

    parsed = urllib.parse.urlparse(raw)
    path = raw
    if parsed.scheme in {"http", "https"}:
        host = (parsed.hostname or "").lower()
        local_hosts = {"127.0.0.1", "localhost"}
        public_base = (settings.PUBLIC_BASE_URL or "").strip()
        if public_base:
            try:
                public_host = (urllib.parse.urlparse(public_base).hostname or "").lower()
                if public_host:
                    local_hosts.add(public_host)
            except Exception:
                pass
        if host not in local_hosts:
            return None
        path = parsed.path or ""
    elif raw.startswith("//"):
        return None

    normalized = path.lstrip("/")
    prefix = "static/assets/"
    if not normalized.startswith(prefix):
        return None
    rel_path = normalized[len(prefix) :]
    return rel_path or None


def _read_local_static_asset(url: str) -> tuple[bytes | None, str, str | None]:
    rel_path = _extract_local_static_asset_relative_path(url)
    if not rel_path:
        return None, "", None
    try:
        decoded_rel_path = urllib.parse.unquote(rel_path)
        assets_root = Path(ASSETS_DIR).resolve()
        file_path = (assets_root / decoded_rel_path).resolve()
        if assets_root not in file_path.parents:
            return None, "", "local_asset_not_found"
        if not file_path.exists() or not file_path.is_file():
            return None, "", "local_asset_not_found"
        return file_path.read_bytes(), file_path.as_uri(), None
    except OSError:
        return None, "", "local_asset_read_failed"
    except Exception:
        return None, "", "local_asset_read_failed"


def _load_image_inline_part(url: str) -> tuple[dict[str, Any] | None, str | None]:
    resolved = _resolve_reference_url(url)
    if not resolved:
        return None, "image_download_failed"
    headers: dict[str, str] = {}
    data_source_for_mime = resolved
    local_data, local_source, local_error = _read_local_static_asset(resolved)
    if local_error:
        return None, local_error
    if local_data is not None:
        data = local_data
        data_source_for_mime = local_source
    else:
        req = urllib.request.Request(resolved, headers={"User-Agent": "photostudio-story-core/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
                headers = {str(k).lower(): str(v) for k, v in resp.headers.items()}
        except urllib.error.HTTPError:
            return None, "image_http_error"
        except (socket.timeout, TimeoutError):
            return None, "image_timeout"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, socket.timeout):
                return None, "image_timeout"
            return None, "image_download_failed"
        except ValueError:
            return None, "image_download_failed"
        except Exception:
            return None, "image_download_failed"

    if not data:
        return None, "image_download_failed"
    if len(data) > MAX_STORY_CORE_IMAGE_BYTES:
        return None, "image_too_large"
    mime_type = _extract_mime_type(data_source_for_mime, headers, data)
    if not mime_type:
        return None, "image_invalid_mime"

    return {
        "inlineData": {
            "mimeType": mime_type,
            "data": base64.b64encode(data).decode("ascii"),
        }
    }, None


def _pick_story_core_ref_urls_by_role(
    *,
    input_pkg: dict[str, Any],
    refs_inventory: dict[str, Any],
    max_per_role: int = 1,
) -> dict[str, list[dict[str, str]]]:
    selected_refs = _safe_dict(input_pkg.get("selected_refs"))
    refs_by_role = _safe_dict(input_pkg.get("refs_by_role"))
    role_order = ["character_1", "props", "location", "style"]
    per_role: dict[str, list[dict[str, str]]] = {role: [] for role in role_order}
    seen_by_role: dict[str, set[str]] = {role: set() for role in role_order}

    def _add(role: str, url: str, source: str) -> None:
        clean_url = str(url or "").strip()
        if not clean_url or clean_url in seen_by_role[role]:
            return
        if len(per_role[role]) >= max_per_role:
            return
        per_role[role].append({"url": clean_url, "source": source})
        seen_by_role[role].add(clean_url)

    # 1) Explicit selections first.
    _add("character_1", str(selected_refs.get("character_1") or ""), "input.selected_refs.character_1")
    _add("location", str(selected_refs.get("location") or ""), "input.selected_refs.location")
    _add("style", str(selected_refs.get("style") or ""), "input.selected_refs.style")
    for idx, value in enumerate(_safe_list(selected_refs.get("props"))):
        _add("props", str(value or ""), f"input.selected_refs.props[{idx}]")

    # 2) refs_by_role from input.
    for role in role_order:
        for idx, value in enumerate(_safe_list(refs_by_role.get(role))):
            _add(role, str(value or ""), f"input.refs_by_role.{role}[{idx}]")

    # 3) refs_inventory fallback (connected/context refs).
    for role in role_order:
        ref_key = _role_to_ref_key(role)
        for idx, value in enumerate(_extract_ref_urls(refs_inventory.get(ref_key))):
            _add(role, str(value or ""), f"refs_inventory.{ref_key}[{idx}]")

    return per_role


def _build_story_core_inline_ref_parts(
    *,
    input_pkg: dict[str, Any],
    refs_inventory: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    role_order = ["character_1", "props", "location", "style"]
    picked = _pick_story_core_ref_urls_by_role(input_pkg=input_pkg, refs_inventory=refs_inventory, max_per_role=1)
    inline_parts: list[dict[str, Any]] = []
    summary: dict[str, Any] = {
        role: {"attached": False, "error": "", "source": "", "url": ""}
        for role in role_order
    }
    attached_roles: list[str] = []

    for role in role_order:
        candidates = _safe_list(picked.get(role))
        if not candidates:
            summary[role] = {"attached": False, "error": "missing", "source": "", "url": ""}
            continue
        chosen = _safe_dict(candidates[0])
        ref_url = str(chosen.get("url") or "").strip()
        ref_source = str(chosen.get("source") or "").strip()
        if not ref_url:
            summary[role] = {"attached": False, "error": "missing", "source": ref_source, "url": ""}
            continue
        inline_part, inline_error = _load_image_inline_part(ref_url)
        if inline_part:
            inline_parts.append(inline_part)
            summary[role] = {"attached": True, "error": "", "source": ref_source, "url": ref_url}
            attached_roles.append(role)
        else:
            summary[role] = {
                "attached": False,
                "error": str(inline_error or "image_attach_failed"),
                "source": ref_source,
                "url": ref_url,
            }

    diagnostics = {
        "story_core_attached_ref_roles": attached_roles,
        "story_core_attached_ref_count": len(attached_roles),
        "story_core_ref_attachment_summary": summary,
        "story_core_character_ref_attached": bool(summary.get("character_1", {}).get("attached")),
        "story_core_character_ref_source": str(summary.get("character_1", {}).get("source") or ""),
        "story_core_character_ref_error": str(summary.get("character_1", {}).get("error") or ""),
        "story_core_props_ref_attached": bool(summary.get("props", {}).get("attached")),
        "story_core_location_ref_attached": bool(summary.get("location", {}).get("attached")),
        "story_core_style_ref_attached": bool(summary.get("style", {}).get("attached")),
    }
    return inline_parts, diagnostics


def _collect_prop_hint_texts(input_pkg: dict[str, Any], refs_inventory: dict[str, Any]) -> list[str]:
    hints: list[str] = []
    props_node = _safe_dict(refs_inventory.get("ref_props"))
    for candidate in (
        props_node.get("value"),
        props_node.get("preview"),
        props_node.get("source_label"),
        _safe_dict(props_node.get("meta")).get("url"),
    ):
        text = str(candidate or "").strip()
        if text:
            hints.append(text)
    for ref in _safe_list(props_node.get("refs")):
        text = str(ref or "").strip()
        if text:
            hints.append(text)
    selected_props = _safe_list(_safe_dict(input_pkg.get("selected_refs")).get("props"))
    for value in selected_props:
        text = str(value or "").strip()
        if text:
            hints.append(text)
    for value in _safe_list(_safe_dict(input_pkg.get("refs_by_role")).get("props")):
        text = str(value or "").strip()
        if text:
            hints.append(text)
    return hints


def _normalize_story_core_prop_contracts(input_pkg: dict[str, Any], refs_inventory: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
    hint_texts = _collect_prop_hint_texts(input_pkg, refs_inventory)
    if not hint_texts:
        return [], False
    hint_blob = " ".join(hint_texts).lower()
    has_cap_signal = bool(re.search(r"\b(baseball\s*cap|cap|hat|headwear)\b", hint_blob)) or ("бейсболк" in hint_blob)
    contracts: list[dict[str, Any]] = []
    confusion_guard_applied = False
    if has_cap_signal:
        contracts.append(
            {
                "object_type": "baseball cap",
                "object_label": "baseball cap / бейсболка",
                "usage_mode": "wearable head accessory",
                "category": "clothing/accessory",
                "forbidden_confusions": ["baseball bat", "helmet", "hood", "weapon"],
                "source_hints": hint_texts[:6],
            }
        )
        confusion_guard_applied = True
    else:
        contracts.append(
            {
                "object_type": "grounded prop object",
                "object_label": str(_safe_dict(refs_inventory.get("ref_props")).get("source_label") or "connected props reference").strip(),
                "usage_mode": "follow connected prop role",
                "category": "props",
                "forbidden_confusions": [],
                "source_hints": hint_texts[:6],
            }
        )
    return contracts, confusion_guard_applied


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp.get("candidates"), list) else []
    if not candidates:
        return ""
    content = candidates[0].get("content") if isinstance(candidates[0], dict) else {}
    parts = content.get("parts") if isinstance(content, dict) and isinstance(content.get("parts"), list) else []
    chunks: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            chunks.append(part.get("text") or "")
    return "\n".join(chunks).strip()


def _extract_json_obj(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        first, last = raw.find("{"), raw.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(raw[first : last + 1])
            except Exception:
                return {}
    return {}


def _default_story_core(input_pkg: dict[str, Any]) -> dict[str, Any]:
    source_note = str(
        input_pkg.get("note")
        or input_pkg.get("story_text")
        or input_pkg.get("director_note")
        or input_pkg.get("text")
        or ""
    ).strip()
    source_note = source_note[:800]
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    return {
        "story_summary": source_note or "Music-driven visual story with continuity locks.",
        "opening_anchor": "Open with a stable hero/world establishing frame.",
        "ending_callback_rule": "Last beat should echo opening anchor with emotional change.",
        "global_arc": "setup→rise→turn→release→afterimage",
        "identity_lock": {"rule": "Keep hero identity stable across all scenes."},
        "world_lock": {"rule": "Keep world/location logic coherent without random jumps."},
        "style_lock": {"rule": "Keep one cinematic style language across the whole track."},
        "story_guidance": _default_story_core_guidance(creative_config),
    }


def _story_text_bundle(input_pkg: dict[str, Any]) -> dict[str, str]:
    return {
        "text": str(input_pkg.get("text") or "").strip(),
        "story_text": str(input_pkg.get("story_text") or "").strip(),
        "note": str(input_pkg.get("note") or "").strip(),
        "director_note": str(input_pkg.get("director_note") or "").strip(),
    }


def _coerce_scene_slots(audio_map: dict[str, Any]) -> list[dict[str, Any]]:
    slots = [row for row in _safe_list(audio_map.get("scene_slots")) if isinstance(row, dict)]
    if slots:
        return slots
    fallback_slots: list[dict[str, Any]] = []
    for idx, row in enumerate(_safe_list(audio_map.get("scene_candidate_windows")), start=1):
        if not isinstance(row, dict):
            continue
        fallback_slots.append(
            {
                "id": str(row.get("id") or f"slot_{idx}"),
                "t0": round(_to_float(row.get("t0"), 0.0), 3),
                "t1": round(_to_float(row.get("t1"), 0.0), 3),
                "duration_sec": round(max(0.0, _to_float(row.get("t1"), 0.0) - _to_float(row.get("t0"), 0.0)), 3),
                "primary_phrase_text": str(row.get("primary_phrase_text") or row.get("label") or "").strip()[:280],
                "audio_features": {},
            }
        )
    return fallback_slots


def _coerce_core_audio_segments(audio_map: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    segments = [row for row in _safe_list(audio_map.get("segments")) if isinstance(row, dict)]
    if segments:
        normalized: list[dict[str, Any]] = []
        for idx, row in enumerate(segments, start=1):
            segment_id = str(row.get("segment_id") or row.get("id") or f"segment_{idx}").strip()
            t0 = round(_to_float(row.get("t0"), 0.0), 3)
            t1 = round(_to_float(row.get("t1"), t0), 3)
            normalized.append(
                {
                    "segment_id": segment_id,
                    "t0": t0,
                    "t1": t1,
                    "duration_sec": round(max(0.0, t1 - t0), 3),
                    "transcript_slice": str(row.get("transcript_slice") or row.get("text") or "").strip()[:320],
                    "intensity": max(0.0, min(1.0, _to_float(row.get("intensity"), 0.5))),
                    "is_lip_sync_candidate": bool(row.get("is_lip_sync_candidate")),
                    "rhythmic_anchor": str(row.get("rhythmic_anchor") or "none").strip().lower() or "none",
                }
            )
        return normalized, "audio_map.segments[]"
    # Legacy bridge only, non-canonical.
    slots = _coerce_scene_slots(audio_map)
    fallback: list[dict[str, Any]] = []
    for idx, slot in enumerate(slots, start=1):
        segment_id = str(slot.get("segment_id") or slot.get("id") or f"segment_{idx}").strip()
        t0 = round(_to_float(slot.get("t0"), 0.0), 3)
        t1 = round(_to_float(slot.get("t1"), t0), 3)
        audio_features = _safe_dict(slot.get("audio_features"))
        fallback.append(
            {
                "segment_id": segment_id,
                "t0": t0,
                "t1": t1,
                "duration_sec": round(max(0.0, t1 - t0), 3),
                "transcript_slice": str(slot.get("primary_phrase_text") or "").strip()[:320],
                "intensity": max(0.0, min(1.0, _to_float(audio_features.get("energy_score"), 0.5))),
                "is_lip_sync_candidate": bool(_to_float(audio_features.get("vocal_ratio"), 0.0) >= 0.55),
                "rhythmic_anchor": "none",
            }
        )
    return fallback, "legacy_bridge_scene_slots_or_windows"


def _slot_story_function(slot: dict[str, Any], index: int, total: int) -> str:
    energy = _to_float(_safe_dict(slot.get("audio_features")).get("energy_score"), 0.5)
    vocal = _to_float(_safe_dict(slot.get("audio_features")).get("vocal_ratio"), 0.4)
    if total <= 1:
        return "single_arc_beat"
    pos = index / max(1, total - 1)
    if pos <= 0.18:
        return "opening_anchor"
    if pos >= 0.86 and energy <= 0.62:
        return "afterimage_release"
    if energy >= 0.72:
        return "climax_pressure"
    if 0.35 <= pos <= 0.75 and vocal >= 0.52:
        return "narrative_development"
    if 0.35 <= pos <= 0.85:
        return "transition_turn"
    return "build_progression"


def _is_domestic_argument_duet(text_bundle: dict[str, str], active_subjects: list[str]) -> bool:
    normalized = " ".join(
        [
            str(text_bundle.get("story_text") or ""),
            str(text_bundle.get("text") or ""),
            str(text_bundle.get("note") or ""),
            str(text_bundle.get("director_note") or ""),
        ]
    ).lower()
    domestic_tokens = ("argument", "fight", "quarrel", "ссора", "скандал", "конфликт", "бытов")
    has_duet = "character_1" in active_subjects and "character_2" in active_subjects
    return has_duet and any(token in normalized for token in domestic_tokens)


def _has_two_active_participant_interaction(text_bundle: dict[str, str], active_subjects: list[str]) -> bool:
    if "character_1" not in active_subjects or "character_2" not in active_subjects:
        return False
    normalized = " ".join(
        [
            str(text_bundle.get("story_text") or ""),
            str(text_bundle.get("text") or ""),
            str(text_bundle.get("note") or ""),
            str(text_bundle.get("director_note") or ""),
        ]
    ).lower()
    interaction_tokens = (
        "two-character",
        "two character",
        "argument",
        "domestic dispute",
        "dialogue conflict",
        "conversation",
        "confrontation",
        "duet",
        "couple interaction",
        "direct interaction",
        "ссора",
        "скандал",
        "конфликт",
        "диалог",
        "противостояние",
    )
    return any(token in normalized for token in interaction_tokens)


def _domestic_argument_story_function(index: int, total: int) -> str:
    arc = [
        "setup",
        "accusation",
        "reaction",
        "escalation",
        "breaking_point",
        "counterattack",
        "exhaustion",
        "unresolved_silence",
    ]
    if total <= 1:
        return arc[0]
    pointer = int(round((index / max(1, total - 1)) * (len(arc) - 1)))
    return arc[max(0, min(len(arc) - 1, pointer))]


def _extract_forbidden_drift(note_text: str) -> list[str]:
    text = str(note_text or "").strip().lower()
    if not text:
        return []
    drift: list[str] = []
    patterns = (
        r"(?:no|avoid|without)\s+([a-z0-9_\- ]{3,36})",
        r"(?:не|без)\s+([a-zа-я0-9_\- ]{3,36})",
    )
    for pattern in patterns:
        for match in re.findall(pattern, text):
            token = str(match or "").strip(" ,.;:!?")
            if token:
                drift.append(f"forbid:{token.replace(' ', '_')[:48]}")
    for known in ("neon", "club", "fantasy", "cyberpunk", "horror", "gore"):
        if re.search(rf"(?:no|avoid|without|не|без)\s+{re.escape(known)}", text):
            drift.append(f"forbid:{known}")
    return list(dict.fromkeys(drift))[:12]


def _ref_record(ref_id: str, row: dict[str, Any]) -> dict[str, str]:
    meta = _normalize_ref_meta(row.get("meta"))
    label = _first_text(row.get("source_label"), row.get("label"), row.get("value"), ref_id)[:120]
    return {
        "ref_id": str(ref_id),
        "label": label,
        "type": str(row.get("type") or row.get("kind") or meta.get("type") or "").strip().lower(),
        "ownership_role": str(meta.get("ownershipRoleMapped") or meta.get("ownershipRole") or "shared"),
        "binding_type": str(meta.get("bindingType") or "nearby"),
    }


def _canonical_subject_id(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    matched = re.search(r"(?:character|char)[_\-\s]*(\d+)", token)
    if matched:
        return f"character_{matched.group(1)}"
    if token in {"main", "lead", "primary"}:
        return "character_1"
    if token in {"support", "secondary"}:
        return "character_2"
    if token in {"antagonist", "villain"}:
        return "character_3"
    return token if re.fullmatch(r"[a-z0-9_]{3,40}", token) else ""


def _is_subject_ref(record: dict[str, str]) -> bool:
    type_token = f"{record.get('type') or ''} {record.get('ref_id') or ''}".lower()
    return any(token in type_token for token in _SUBJECT_REF_TOKENS)


def _is_object_ref(record: dict[str, str], meta: dict[str, Any]) -> bool:
    type_token = f"{record.get('type') or ''} {record.get('ref_id') or ''} {str(meta.get('type') or '')}".lower()
    if any(token in type_token for token in _SUBJECT_REF_TOKENS):
        return False
    return any(token in type_token for token in _OBJECT_REF_TOKENS)


def _build_world_model_normalized(
    *,
    content_type: str,
    text_bundle: dict[str, str],
    opening_anchor: str,
    location_hints: list[str],
    style_hints: list[str],
    forbidden_drift: list[str],
) -> str:
    setting = _first_text(location_hints[0] if location_hints else "", opening_anchor, "grounded_environment")
    mood = _first_text(style_hints[0] if style_hints else "", text_bundle.get("note"), text_bundle.get("director_note"), "coherent_cinematic")
    premise = _first_text(text_bundle.get("story_text"), text_bundle.get("text"), "audio_synchronized_progression")
    premise_tokens = [token.strip(" ,.;:!?") for token in re.split(r"[\n,.;:!?]+", premise) if token.strip()]
    compact_premise = " / ".join(premise_tokens[:2])[:120] or "audio_synchronized_progression"
    drift_guard = ", ".join([token.replace("forbid:", "").replace("_", " ") for token in forbidden_drift[:2]]) or "identity replacement"
    return f"type:{content_type}; setting:{setting[:64]}; mood:{mood[:64]}; premise:{compact_premise}; guard:no {drift_guard}"


def _legacy_story_function_from_canonical(segment: dict[str, Any], default: str) -> str:
    arc_role = str(segment.get("arc_role") or "").strip().lower()
    beat_mode = str(segment.get("beat_mode") or "").strip().lower()
    if arc_role == "setup":
        return "opening_anchor"
    if arc_role == "build":
        return "narrative_development" if beat_mode in {"social_texture", "world_observation", "world_pressure"} else "build_progression"
    if arc_role == "pivot":
        return "transition_turn"
    if arc_role == "climax":
        return "climax_pressure"
    if arc_role in {"release", "afterglow"}:
        return "afterimage_release" if beat_mode in {"aftermath", "release"} else "build_progression"
    return default


def _subject_presence_for_canonical_beat(*, beat_mode: str, hero_world_mode: str, lip_sync_only: bool, default: str) -> tuple[str, str]:
    world_modes = {"social_texture", "world_pressure", "threshold", "aftermath", "world_observation", "release", "transition"}
    if beat_mode == "performance":
        return "character_1", "primary_subject_visible_for_performance_beat"
    world_first = hero_world_mode == "world_foreground" or beat_mode in world_modes
    if world_first and lip_sync_only:
        return "world", "world_or_context_primary_hero_may_be_offscreen"
    if world_first:
        return "world", "context_driven_visibility_hero_optional"
    return default, "primary_subject_visible_unless_explicit_handoff"


def _ref_safe_identity_text(text: str, role: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return _visual_ref_identity_rule(role)
    banned_patterns = (
        r"\bflat\s*cap\b",
        r"\btrack\s*jacket\b",
        r"\bsunglasses?\b",
        r"\bbaseball\s*cap\b",
        r"\btattoo(?:s)?\b",
        r"\bbeard(?:ed)?\b",
        r"\bmustache\b",
    )
    lowered = raw.lower()
    if any(re.search(pattern, lowered) for pattern in banned_patterns):
        return _visual_ref_identity_rule(role)
    split_pattern = re.compile(r"(?:\n\s*\n+|(?<=[.!?])\s+)")
    clauses: list[str] = []
    seen_norm: set[str] = set()
    for chunk in split_pattern.split(raw):
        candidate = str(chunk or "").strip()
        if not candidate:
            continue
        candidate_norm = re.sub(r"\s+", " ", candidate).strip(" .!?;,").lower()
        if not candidate_norm or candidate_norm in seen_norm:
            continue
        seen_norm.add(candidate_norm)
        clauses.append(candidate)
    deduped = " ".join(clauses).strip()
    return deduped or raw


def _modulate_peak_flatline_story_functions(
    beats: list[dict[str, Any]],
    *,
    canonical_by_segment_id: dict[str, dict[str, Any]],
) -> None:
    if len(beats) < 3:
        return
    idx = 0
    while idx < len(beats):
        fn = str(_safe_dict(beats[idx]).get("story_function") or "").strip()
        run_end = idx + 1
        while run_end < len(beats) and str(_safe_dict(beats[run_end]).get("story_function") or "").strip() == fn:
            run_end += 1
        run_size = run_end - idx
        if fn == "climax_pressure" and run_size >= 3:
            for offset in range(1, run_size - 1):
                beat = _safe_dict(beats[idx + offset])
                segment_id = str(beat.get("source_segment_id") or "").strip()
                canonical_row = _safe_dict(canonical_by_segment_id.get(segment_id))
                beat_mode = str(canonical_row.get("beat_mode") or "").strip().lower()
                hero_world_mode = str(canonical_row.get("hero_world_mode") or "").strip().lower()
                replacement = "transition_turn" if (beat_mode in {"world_pressure", "social_texture", "world_observation", "threshold"} or hero_world_mode == "world_foreground") else "build_progression"
                beats[idx + offset]["story_function"] = replacement
        idx = run_end


def _has_closure_signal(
    beats: list[dict[str, Any]],
    narrative_segments: list[dict[str, Any]],
    ending_callback_rule: str,
) -> bool:
    if not beats:
        return False
    tail_beats = beats[-2:] if len(beats) >= 2 else beats
    if any(any(token in str(_safe_dict(item).get("story_function") or "") for token in ("afterimage", "release")) for item in tail_beats):
        return True
    tail_segments = narrative_segments[-2:] if len(narrative_segments) >= 2 else narrative_segments
    if any(str(_safe_dict(row).get("arc_role") or "").strip().lower() in {"release", "afterglow"} for row in tail_segments):
        return True
    callback_text = str(ending_callback_rule or "").strip().lower()
    return any(token in callback_text for token in ("echo", "resolve", "release", "closure", "afterglow"))


def _build_story_core_v11(
    *,
    input_pkg: dict[str, Any],
    audio_map: dict[str, Any],
    refs_inventory: dict[str, Any],
    assigned_roles: dict[str, Any],
    parsed_story_core: dict[str, Any],
    fallback_story_core: dict[str, Any],
) -> dict[str, Any]:
    text_bundle = _story_text_bundle(input_pkg)
    content_type = str(input_pkg.get("content_type") or input_pkg.get("contentType") or "music_video").strip() or "music_video"
    director_mode = _resolve_director_mode(input_pkg.get("director_mode"), content_type=content_type)
    content_format = str(input_pkg.get("format") or input_pkg.get("content_format") or "").strip() or "short_form_video"
    ownership_binding_inventory = [row for row in _safe_list(input_pkg.get("ownership_binding_inventory")) if isinstance(row, dict)]
    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    role_identity_mapping = _safe_dict(connected_summary.get("role_identity_mapping"))
    character_1_identity = _safe_dict(role_identity_mapping.get("character_1"))
    character_1_appearance_mode = str(character_1_identity.get("appearanceMode") or character_1_identity.get("appearance_mode") or "").strip().lower()
    character_1_presence_mode = str(character_1_identity.get("screenPresenceMode") or character_1_identity.get("screen_presence_mode") or "").strip().lower()
    character_1_lip_sync_only = character_1_appearance_mode == "lip_sync_only" or character_1_presence_mode == "lip_sync_only"
    core_segments, core_source = _coerce_core_audio_segments(audio_map)
    total_slots = len(core_segments)

    primary_subject = _canonical_subject_id(assigned_roles.get("primary_role")) or "character_1"
    role_subjects = [
        str(v).strip()
        for v in _safe_list(assigned_roles.get("active_roles"))
        if _canonical_subject_id(v) and _canonical_subject_id(v) != primary_subject
    ]
    connected_subjects = [
        str(v).strip()
        for v in (
            _safe_list(connected_summary.get("support_roles"))
            + _safe_list(connected_summary.get("secondary_subjects"))
            + _safe_list(connected_summary.get("subject_candidates"))
        )
        if _canonical_subject_id(v) and _canonical_subject_id(v) != primary_subject
    ]
    ref_subjects: list[str] = []
    secondary_subject_labels: dict[str, str] = {}
    continuity_objects: list[dict[str, Any]] = []
    for ref_id, value in refs_inventory.items():
        if not isinstance(value, dict):
            continue
        record = _ref_record(str(ref_id), value)
        meta = _normalize_ref_meta(value.get("meta"))
        if _is_subject_ref(record):
            canonical_ref_subject = _canonical_subject_id(record["ref_id"]) or _canonical_subject_id(record["label"])
            if canonical_ref_subject and canonical_ref_subject != primary_subject:
                ref_subjects.append(canonical_ref_subject)
                if record["label"]:
                    secondary_subject_labels[canonical_ref_subject] = record["label"]
        if _is_object_ref(record, meta):
            continuity_objects.append(
                {
                    "ref_id": record["ref_id"],
                    "label": record["label"],
                    "ownership_role": record["ownership_role"],
                    "binding_type": record["binding_type"],
                    "source_label": str(value.get("source_label") or ""),
                    "visibility_expectation": "persistent" if record["binding_type"] in {"worn", "held", "carried"} else "recurring",
                }
            )
    for row in ownership_binding_inventory[:18]:
        ref_id = str(row.get("ref_id") or "").strip()
        label = str(row.get("label") or "").strip()
        if not (ref_id or label):
            continue
        semantic_token = f"{ref_id} {label}".lower()
        if any(token in semantic_token for token in _SUBJECT_REF_TOKENS):
            continue
        if not any(token in semantic_token for token in _OBJECT_REF_TOKENS):
            continue
        continuity_objects.append(
            {
                "ref_id": ref_id,
                "label": label[:120],
                "ownership_role": str(row.get("ownershipRoleMapped") or row.get("ownershipRole") or "shared"),
                "binding_type": str(row.get("bindingType") or "nearby"),
                "source_label": str(row.get("source_label") or ""),
                "visibility_expectation": "persistent" if str(row.get("bindingType") or "").strip().lower() in {"worn", "held", "carried"} else "recurring",
            }
        )
    continuity_objects = list({f"{obj.get('ref_id')}::{obj.get('label')}": obj for obj in continuity_objects if obj.get("label") or obj.get("ref_id")}.values())[:16]

    for candidate in [*role_subjects, *connected_subjects]:
        canonical_candidate = _canonical_subject_id(candidate)
        if canonical_candidate and canonical_candidate != primary_subject:
            ref_subjects.append(canonical_candidate)
            if canonical_candidate not in secondary_subject_labels and str(candidate).strip():
                secondary_subject_labels[canonical_candidate] = str(candidate).strip()[:120]
    secondary_subjects = list(dict.fromkeys(ref_subjects))[:6]
    if not secondary_subjects and continuity_objects:
        mapped_subject = _canonical_subject_id(continuity_objects[0].get("ownership_role"))
        secondary_subjects = [mapped_subject] if mapped_subject and mapped_subject != primary_subject else []
    active_subjects = list(dict.fromkeys([primary_subject, *secondary_subjects]))
    domestic_argument_duet = _is_domestic_argument_duet(text_bundle, active_subjects)
    if domestic_argument_duet:
        secondary_subjects = list(dict.fromkeys(["character_2", *secondary_subjects]))
    active_subjects = list(dict.fromkeys([primary_subject, *secondary_subjects]))

    opening_anchor = _first_text(parsed_story_core.get("opening_anchor"), fallback_story_core.get("opening_anchor"), text_bundle.get("story_text"), text_bundle.get("text"))[:220]
    ending_callback_rule = _first_text(parsed_story_core.get("ending_callback_rule"), fallback_story_core.get("ending_callback_rule"))
    style_rule = _first_text(_safe_dict(parsed_story_core.get("style_lock")).get("rule"), _safe_dict(fallback_story_core.get("style_lock")).get("rule"))
    note_text = " ".join([text_bundle.get("note", ""), text_bundle.get("director_note", "")]).strip()
    forbidden_drift = _extract_forbidden_drift(note_text) or ["forbid:identity_replacement", "forbid:ungrounded_world_jump"]
    parsed_narrative_segments = [row for row in _safe_list(parsed_story_core.get("narrative_segments")) if isinstance(row, dict)]
    canonical_by_segment_id = {
        str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row)
        for row in parsed_narrative_segments
        if str(_safe_dict(row).get("segment_id") or "").strip()
    }

    beats: list[dict[str, Any]] = []
    slot_groups: list[dict[str, Any]] = []
    group_reason: list[dict[str, Any]] = []
    current_group: list[str] = []
    current_function = ""
    previous_phrase = ""
    repeated_count = 0
    mode_weight = {
        "music_video": {"audio": 1.2, "continuity": 1.0, "causality": 0.9},
        "story": {"audio": 0.9, "continuity": 1.2, "causality": 1.2},
        "film": {"audio": 0.9, "continuity": 1.2, "causality": 1.2},
        "ad": {"audio": 1.0, "continuity": 1.3, "causality": 1.1},
        "news": {"audio": 0.8, "continuity": 1.0, "causality": 1.35},
    }.get(content_type, {"audio": 1.0, "continuity": 1.0, "causality": 1.0})
    has_persistent_objects = any(str(obj.get("visibility_expectation") or "") == "persistent" for obj in continuity_objects)
    for idx, segment in enumerate(core_segments):
        slot_id = str(segment.get("segment_id") or f"segment_{idx + 1}")
        phrase = str(segment.get("transcript_slice") or "").strip()
        slot = {
            "audio_features": {
                "energy_score": _to_float(segment.get("intensity"), 0.5),
                "vocal_ratio": 0.7 if bool(segment.get("is_lip_sync_candidate")) else 0.3,
            }
        }
        fn = _domestic_argument_story_function(idx, total_slots) if domestic_argument_duet else _slot_story_function(slot, idx, total_slots)
        canonical_row = {}
        if parsed_narrative_segments:
            canonical_row = _safe_dict(canonical_by_segment_id.get(slot_id))
            fn = _legacy_story_function_from_canonical(canonical_row, fn)
        phrase_key = re.sub(r"\s+", " ", phrase.lower()).strip()
        energy = _to_float(_safe_dict(slot.get("audio_features")).get("energy_score"), 0.5)
        vocal = _to_float(_safe_dict(slot.get("audio_features")).get("vocal_ratio"), 0.4)
        beat_mode = str(canonical_row.get("beat_mode") or "").strip().lower()
        hero_world_mode = str(canonical_row.get("hero_world_mode") or "").strip().lower()
        if beat_mode in {"world_pressure", "threshold", "social_texture"}:
            semantic_density = "high"
        elif beat_mode in {"aftermath", "release"}:
            semantic_density = "low"
        else:
            semantic_density = "high" if (vocal >= 0.55 or len(phrase.split()) >= 8) else ("low" if not phrase else "medium")
        narrative_load_score = (energy * mode_weight["audio"]) + (0.3 if fn in {"transition_turn", "climax_pressure"} else 0.0)
        if beat_mode in {"performance", "world_pressure", "threshold"}:
            narrative_load = "high" if narrative_load_score >= 0.65 else "medium"
        elif beat_mode in {"aftermath", "release"}:
            narrative_load = "low" if narrative_load_score <= 0.85 else "medium"
        else:
            narrative_load = "high" if narrative_load_score >= 0.9 else ("medium" if narrative_load_score >= 0.6 else "low")
        object_presence_required = bool(continuity_objects) and (
            fn in {"transition_turn", "climax_pressure"} or (has_persistent_objects and narrative_load in {"high", "medium"})
        )
        continuity_pressure = "high" if object_presence_required else "medium"
        primary_shift_allowed = bool(re.search(r"\b(we|they|together|crowd|everyone)\b", phrase_key)) and fn in {"transition_turn", "climax_pressure"}
        if domestic_argument_duet:
            beat_primary_subject = "character_1" if idx % 2 == 0 else "character_2"
        else:
            beat_primary_subject = primary_subject if not (primary_shift_allowed and secondary_subjects) else secondary_subjects[0]
            beat_primary_subject, presence_requirement = _subject_presence_for_canonical_beat(
                beat_mode=beat_mode,
                hero_world_mode=hero_world_mode,
                lip_sync_only=character_1_lip_sync_only,
                default=beat_primary_subject,
            )

        if not current_group:
            current_group = [slot_id]
            current_function = fn
        elif fn == current_function and len(current_group) < 3 and phrase_key and phrase_key == previous_phrase and repeated_count < 2:
            current_group.append(slot_id)
            repeated_count += 1
        else:
            group_id = f"group_{len(slot_groups) + 1}"
            slot_groups.append({"group_id": group_id, "slot_ids": list(current_group)})
            group_reason.append({"group_id": group_id, "reason": f"semantic_continuity:{current_function}|phrase_pattern:{'repeat' if repeated_count else 'progression'}"})
            current_group = [slot_id]
            current_function = fn
            repeated_count = 0

        if domestic_argument_duet:
            beat_secondary = ["character_2"] if beat_primary_subject == "character_1" else ["character_1"]
            subject_presence_requirement = "both_conflict_participants_visible_or_implied_nearby"
        else:
            if beat_primary_subject == "world":
                beat_secondary = []
            else:
                beat_secondary = secondary_subjects[:2] if idx % 2 == 0 else secondary_subjects[1:3] or secondary_subjects[:1]
            subject_presence_requirement = presence_requirement
        beats.append(
            {
                "beat_id": f"beat_{idx + 1}",
                "slot_ids": [slot_id],
                "time_range": {"t0": round(_to_float(segment.get("t0"), 0.0), 3), "t1": round(_to_float(segment.get("t1"), 0.0), 3)},
                "story_function": fn,
                "beat_primary_subject": beat_primary_subject,
                "beat_secondary_subjects": beat_secondary,
                "semantic_density": semantic_density,
                "narrative_load": narrative_load,
                "subject_presence_requirement": subject_presence_requirement,
                "continuity_visibility_requirement": "object_anchor_required" if object_presence_required else "world_anchor_or_subject_callback",
                "beat_focus_hint": phrase[:180] or fn,
                "source_segment_id": slot_id,
                "group_reason": f"{fn}|mode:{beat_mode or 'heuristic'}|density:{semantic_density}|continuity:{continuity_pressure}",
            }
        )
        previous_phrase = phrase_key
    _modulate_peak_flatline_story_functions(beats, canonical_by_segment_id=canonical_by_segment_id)
    if current_group:
        group_id = f"group_{len(slot_groups) + 1}"
        slot_groups.append({"group_id": group_id, "slot_ids": list(current_group)})
        group_reason.append({"group_id": group_id, "reason": f"semantic_continuity:{current_function}|phrase_pattern:{'repeat' if repeated_count else 'progression'}"})

    location_hints = [
        _first_text(row.get("source_label"), row.get("value"), ref_id)
        for ref_id, row in refs_inventory.items()
        if isinstance(row, dict) and any(token in str(ref_id).lower() for token in ("location", "place", "env", "bg"))
    ][:4]
    style_hints = [
        _first_text(row.get("source_label"), row.get("value"), ref_id)
        for ref_id, row in refs_inventory.items()
        if isinstance(row, dict) and any(token in str(ref_id).lower() for token in ("style", "look", "mood", "grade"))
    ][:4]
    world_model = _build_world_model_normalized(
        content_type=content_type,
        text_bundle=text_bundle,
        opening_anchor=opening_anchor,
        location_hints=location_hints,
        style_hints=style_hints,
        forbidden_drift=forbidden_drift,
    )[:240]
    world_definition = {
        "world_model": world_model or "grounded_continuity_world",
        "world_axioms": [
            "same_world_family_across_beats",
            "subject_identity_must_stay_consistent",
            "object_bindings_follow_ownership_and_binding_meta",
            "audio_slots_define_temporal_order",
            f"mode_weighting:{content_type}:audio={mode_weight['audio']},continuity={mode_weight['continuity']},causality={mode_weight['causality']}",
        ],
        "environment_anchor": _first_text(location_hints[0] if location_hints else "", opening_anchor, "text_derived_environment_anchor"),
        "style_anchor": _first_text(style_hints[0] if style_hints else "", style_rule, "coherent_cinematic_style"),
        "allowed_variation": ["lighting_shift", "framing_shift", "performance_intensity_shift", "weather_or_time_shift_if_text_supported"],
        "forbidden_drift": forbidden_drift,
        "world_continuity_rules": [
            "location_and_style_changes_require_semantic_bridge",
            "forbidden_drift_tokens_cannot_be_introduced",
            "if_location_refs_missing_use_text_guidance_not_global_arc",
        ],
    }
    primary_spine = f"{primary_subject} carries narrative progression via {_first_text(text_bundle.get('story_text'), text_bundle.get('text'), parsed_story_core.get('story_summary'), fallback_story_core.get('story_summary'))[:120] or 'audio_text progression'}"
    continuity_matrix = {
        "subject_to_objects": [
            {
                "subject": str(item.get("ownership_role") or "shared"),
                "object_label": str(item.get("label") or ""),
                "binding_type": str(item.get("binding_type") or "nearby"),
                "visibility_expectation": str(item.get("visibility_expectation") or "recurring"),
            }
            for item in continuity_objects[:14]
        ]
    }
    transition_events = []
    for i in range(max(0, len(beats) - 1)):
        left, right = beats[i], beats[i + 1]
        evt_type = "semantic_progression"
        handoff_explicit = _transition_handoff_is_explicit(
            left,
            right,
            content_type=content_type,
            director_mode=director_mode,
        )
        if left.get("story_function") != right.get("story_function"):
            evt_type = "function_turn"
        if handoff_explicit:
            evt_type = "subject_handoff_explicit"
        transition_events.append(
            {
                "from_beat": left["beat_id"],
                "to_beat": right["beat_id"],
                "type": evt_type,
                "object_carry_required": bool(continuity_objects),
            }
        )

    primary_per_beat = [str(item.get("beat_primary_subject") or "") for item in beats]
    shadow_count = sum(1 for subject in primary_per_beat if subject and subject != primary_subject)
    intentional_clip_alternation = _is_intentional_clip_subject_alternation(
        primary_per_beat,
        primary_subject=primary_subject,
        content_type=content_type,
        director_mode=director_mode,
    )
    subject_shadowing = (shadow_count > max(1, math.floor(len(beats) * 0.35))) and not intentional_clip_alternation
    required_object_beats = [idx for idx, beat in enumerate(beats) if str(beat.get("continuity_visibility_requirement") or "") == "object_anchor_required"]
    required_object_beats_set = set(required_object_beats)
    legitimized_transition_indices = {
        idx + 1
        for idx, event in enumerate(transition_events)
        if str(event.get("type") or "") in {"function_turn", "subject_handoff_explicit"}
    }
    has_long_required_run = False
    run_size = 0
    for idx in range(len(beats)):
        if idx in required_object_beats_set:
            run_size += 1
            has_long_required_run = has_long_required_run or run_size >= 3
        else:
            run_size = 0
    has_unlegitimized_required_run = has_long_required_run and not any(idx in legitimized_transition_indices for idx in required_object_beats)
    continuity_break = bool(
        continuity_objects
        and (
            not continuity_matrix["subject_to_objects"]
            or (has_persistent_objects and not required_object_beats)
            or has_unlegitimized_required_run
        )
    )
    world_anchor_text = " ".join(
        [
            str(world_definition.get("environment_anchor") or ""),
            str(world_definition.get("style_anchor") or ""),
            str(opening_anchor or ""),
        ]
    ).lower()
    world_drift = any(
        token.replace("forbid:", "").replace("_", " ") in world_anchor_text
        for token in forbidden_drift
        if token.startswith("forbid:")
    )
    arc_tokens = [str(item.get("story_function") or "") for item in beats]
    flatline_segments: list[dict[str, Any]] = []
    start = 0
    while start < len(arc_tokens):
        end = start + 1
        while end < len(arc_tokens) and arc_tokens[end] == arc_tokens[start]:
            end += 1
        if (end - start) >= 3:
            flatline_segments.append({"from_beat": f"beat_{start + 1}", "to_beat": f"beat_{end}", "story_function": arc_tokens[start]})
        start = end
    semantic_delta_score = round(1.0 - min(0.7, len(flatline_segments) * 0.18) - (0.15 if subject_shadowing else 0.0) - (0.15 if continuity_break else 0.0), 3)
    dangling_tail = False
    callback_missing = bool(opening_anchor and ending_callback_rule and not beats)
    continuity_object_dropout = bool(continuity_objects and not continuity_matrix["subject_to_objects"])
    beat_presence_requirements = {str(item.get("subject_presence_requirement") or "") for item in beats}
    duet_presence_rule = (
        "for_two_character_argument_keep_character_1_and_character_2_present_as_conflict_participants"
        if domestic_argument_duet
        else "not_applicable"
    )
    has_interaction_duet_signal = _has_two_active_participant_interaction(text_bundle, active_subjects)
    has_conflict_visibility_signal = "both_conflict_participants_visible_or_implied_nearby" in beat_presence_requirements
    support_presence_tokens = ("secondary", "support", "background", "memory", "offscreen", "off-screen", "voice")
    support_signal_text = " ".join(
        [
            str(text_bundle.get("story_text") or ""),
            str(text_bundle.get("text") or ""),
            str(text_bundle.get("note") or ""),
            str(text_bundle.get("director_note") or ""),
            str(secondary_subject_labels.get("character_2") or ""),
            " ".join(str(v) for v in _safe_list(connected_summary.get("support_roles"))),
        ]
    ).lower()
    character_2_support_optional = (
        "character_2" in secondary_subjects
        and not (has_interaction_duet_signal or has_conflict_visibility_signal or domestic_argument_duet)
        and any(token in support_signal_text for token in support_presence_tokens)
    )
    has_world_context_beats = any(
        str(item.get("beat_primary_subject") or "") == "world"
        or "hero_may_be_offscreen" in str(item.get("subject_presence_requirement") or "")
        for item in beats
    )
    if has_interaction_duet_signal or has_conflict_visibility_signal or duet_presence_rule != "not_applicable":
        visibility_mode = "two_active_participants"
        must_be_visible = ["character_1", "character_2"]
        may_be_offscreen: list[str] = []
        contract_presence_requirement = "both_active_participants_visible_or_implied_nearby"
    elif character_1_lip_sync_only and has_world_context_beats:
        visibility_mode = "performance_world_split"
        must_be_visible = []
        may_be_offscreen = list(dict.fromkeys(["character_1", *secondary_subjects[:3]]))
        contract_presence_requirement = "performance_beats_require_singer_world_beats_allow_offscreen_hero"
    elif len(active_subjects) >= 3:
        visibility_mode = "ensemble"
        must_be_visible = list(dict.fromkeys([primary_subject, *active_subjects[1:3]]))
        may_be_offscreen = [subject for subject in active_subjects[3:6] if subject not in must_be_visible]
        contract_presence_requirement = "multi_subject_visibility_prioritized_by_beat_context"
    elif character_2_support_optional:
        visibility_mode = "support_optional"
        must_be_visible = [primary_subject]
        may_be_offscreen = ["character_2"]
        contract_presence_requirement = "primary_subject_visible_support_subject_optional"
    else:
        visibility_mode = "single_protagonist"
        must_be_visible = [primary_subject]
        may_be_offscreen = ["character_2"] if "character_2" in secondary_subjects else secondary_subjects[:3]
        contract_presence_requirement = "primary_subject_visible_unless_explicit_handoff"

    if parsed_narrative_segments:
        narrative_segments = [
            {
                "segment_id": str(row.get("segment_id") or ""),
                "arc_role": str(row.get("arc_role") or ""),
                "beat_purpose": str(row.get("beat_purpose") or ""),
                "emotional_key": str(row.get("emotional_key") or ""),
                **_normalize_story_core_segment_structured_fields(row),
            }
            for row in parsed_narrative_segments
        ]
    else:
        role_map = {
            "opening_anchor": "setup",
            "build_progression": "build",
            "narrative_development": "build",
            "transition_turn": "pivot",
            "climax_pressure": "climax",
            "afterimage_release": "afterglow",
            "setup": "setup",
            "accusation": "build",
            "reaction": "pivot",
            "escalation": "climax",
            "breaking_point": "climax",
            "counterattack": "release",
            "exhaustion": "release",
            "unresolved_silence": "afterglow",
        }
        narrative_segments = [
            {
                "segment_id": str(item.get("source_segment_id") or ""),
                "arc_role": role_map.get(str(item.get("story_function") or ""), "release"),
                "beat_purpose": str(item.get("beat_focus_hint") or item.get("story_function") or ""),
                "emotional_key": str(item.get("narrative_load") or "medium"),
                "visual_scale": ("medium" if idx % 3 == 0 else ("wide" if idx % 3 == 1 else "intimate")),
                "visual_density": ("dense" if idx % 3 == 0 else ("moderate" if idx % 3 == 1 else "sparse")),
                "motion_profile": ("controlled" if idx % 3 == 0 else ("dynamic" if idx % 3 == 1 else "still")),
                "hero_world_mode": ("hero_foreground" if idx % 2 == 0 else "world_foreground"),
                "beat_mode": (
                    "performance"
                    if "vocal" in str(item.get("beat_focus_hint") or "").lower() or idx % 2 == 0
                    else "world_observation"
                ),
                "subtext_mode": ("second_order" if idx in {1, max(1, total_slots - 2)} else "coded"),
                "association_target": ("level_2_plus" if idx in {1, max(1, total_slots - 2)} else "level_1"),
            }
            for idx, item in enumerate(beats)
        ]
    first_beat_subject = str(beats[0].get("beat_primary_subject") or "").strip() if beats else ""
    first_beat_function = str(beats[0].get("story_function") or "").strip() if beats else ""
    first_segment = _safe_dict(narrative_segments[0]) if narrative_segments else {}
    first_segment_role = str(first_segment.get("arc_role") or "").strip().lower()
    opening_world_anchor = _has_opening_world_anchor_signal(opening_anchor)
    opening_clip_world_aligned = (
        _is_music_video_clip_mode(content_type, director_mode)
        and opening_world_anchor
        and first_beat_subject == "world"
        and first_beat_function in {"opening_anchor", "setup", "build_progression"}
        and first_segment_role in {"setup", "build"}
    )
    opening_mismatch = bool(beats and first_beat_subject != primary_subject and not opening_clip_world_aligned)

    canonical_arc_segments = (
        [
            _legacy_story_function_from_canonical(_safe_dict(row), "build_progression")
            for row in parsed_narrative_segments
            if str(_safe_dict(row).get("segment_id") or "").strip()
        ]
        if parsed_narrative_segments
        else [item.get("story_function") for item in beats[:8]]
    )
    dangling_tail = bool(beats) and not _has_closure_signal(beats, narrative_segments, ending_callback_rule)
    story_core_v1 = {
        "schema_version": "core_v1.1",
        "director_mode": director_mode,
        "story_truth_source": "note_refs_primary" if director_mode == "clip" else "mixed_inputs",
        "audio_canonical_source_for_core": core_source,
        "audio_truth_scope": "timing_plus_emotion" if director_mode == "clip" else "timing_structure",
        "world_definition": world_definition,
        "narrative_backbone": {
            "primary_narrative_spine": primary_spine,
            "secondary_subjects": secondary_subjects,
            "secondary_subject_labels": {key: secondary_subject_labels[key] for key in secondary_subjects if key in secondary_subject_labels},
            "continuity_objects": continuity_objects,
            "continuity_matrix": continuity_matrix,
            "subject_priority_rules": [
                "global_primary_spine_drives_interpretation",
                "beat_primary_subject_cannot_shift_without_text_or_turn_signal",
            ],
            "emotional_voice_rules": ["keep_voice_consistent_with_global_arc", "avoid_unmotivated_tonal_inversion"],
            "subject_transition_rules": ["subject_handoff_allowed_only_on_transition_turn_or_explicit_phrase_signal", "secondary_subject_cannot_dominate_without_story_reason"],
            "duet_presence_rule": duet_presence_rule,
            "object_transition_rules": ["ownership_or_binding_change_must_be_explicit_in_transition_events", "persistent_objects_should_reappear_within_two_beats"],
            "transition_events": transition_events[:16],
        },
        "semantic_arc": {
            "global_intent": str(parsed_story_core.get("story_summary") or fallback_story_core.get("story_summary") or ""),
            "opening_statement": opening_anchor,
            "arc_segments": canonical_arc_segments[:8],
            "turn_points": [item["beat_id"] for item in beats if item.get("story_function") in {"transition_turn", "climax_pressure"}][:4],
            "climax_definition": "max narrative pressure synchronized with high-energy audio slot(s)",
            "ending_resolution": ending_callback_rule,
            "afterimage_rule": "final beat must preserve world identity while reducing pressure",
            "callback_rules": ["ending_echoes_opening_anchor_with_contextual_change"],
        },
        "narrative_segments": narrative_segments,
        "beat_map": {
            "slot_groups": slot_groups,
            "beats": beats,
            "group_reason": group_reason,
            "beat_primary_subject": {item["beat_id"]: item["beat_primary_subject"] for item in beats},
            "beat_secondary_subjects": {item["beat_id"]: item["beat_secondary_subjects"] for item in beats},
            "story_function": {item["beat_id"]: item["story_function"] for item in beats},
            "semantic_density": {item["beat_id"]: item["semantic_density"] for item in beats},
            "narrative_load": {item["beat_id"]: item["narrative_load"] for item in beats},
            "subject_presence_requirement": "derive_per_beat_from_canonical_beat_mode_and_hero_world_mode",
            "continuity_visibility_requirement": "every_beat_requires_world_or_object_continuity_marker",
            "beat_focus_hint": {item["beat_id"]: item["beat_focus_hint"] for item in beats},
        },
        "validation": {
            "validation_flags": {
                "audio_segments_present": bool(core_segments),
                "beat_slot_binding_valid": all(bool(_safe_list(item.get("slot_ids"))) for item in beats),
                "world_anchor_present": bool(world_definition.get("environment_anchor")),
                "narrative_spine_present": bool(primary_spine.strip()),
                "subject_shadowing": subject_shadowing,
                "continuity_break": continuity_break,
                "world_drift": world_drift,
                "audio_semantic_mismatch": bool(core_segments and not any(token in arc_tokens for token in ("transition_turn", "climax_pressure", "afterimage_release"))),
                "dangling_tail": dangling_tail,
                "opening_mismatch": opening_mismatch,
                "missing_callback": callback_missing,
                "continuity_object_dropout": continuity_object_dropout,
                "semantic_stagnation_warning": bool(flatline_segments),
            },
            "warnings": (
                [] if core_segments else ["beat_map_generated_without_audio_segments"]
            )
            + (["subject_shadowing_detected"] if subject_shadowing else [])
            + (["continuity_break_risk"] if continuity_break else [])
            + (["world_drift_risk"] if world_drift else [])
            + (["semantic_flatline_detected"] if flatline_segments else []),
            "consistency_score": round(max(0.0, min(1.0, semantic_delta_score)), 3),
            "semantic_delta_score": semantic_delta_score,
            "arc_flatline_segments": flatline_segments,
            "core_fail_conditions": [
                "missing_audio_map_segments",
                "missing_primary_narrative_spine",
                "empty_world_definition",
            ],
        },
        "prompt_interface_contract": {
            "contract_version": "prompt_interface_v1.1",
            "input_channels": ["world_definition", "narrative_backbone", "semantic_arc", "beat_map"],
            "must_remain_same": ["primary_subject_identity", "world_family", "core_continuity_objects", "slot_timing"],
            "may_vary": ["lighting", "framing", "performance_intensity", "semantic_emphasis_per_beat"],
            "visibility_mode": visibility_mode,
            "subject_presence_requirement": contract_presence_requirement,
            "must_be_visible": must_be_visible,
            "may_be_offscreen": may_be_offscreen,
            "continuity_priority": ["subject_identity", "object_binding", "world_anchor"],
            "world_prompt_constraints": world_definition.get("world_continuity_rules", []),
            "identity_prompt_constraints": [
                _visual_ref_identity_rule(primary_subject),
                "Text identity descriptions are auxiliary only and must not override connected visual references.",
            ],
            "object_prompt_constraints": ["respect_ownership_role_mapping", "do_not_drop_persistent_objects_without_transition_event"],
            "forbidden_insertions": ["unreferenced_main_character", "ungrounded_magic_object", "unauthorized_world_reset"],
            "forbidden_style_drift": forbidden_drift,
            "required_callback_elements": [opening_anchor[:120], ending_callback_rule[:120]],
            "beat_focus_hint": {item["beat_id"]: item["beat_focus_hint"] for item in beats},
            "downstream_constraints": {
                "must_preserve_slot_timing": True,
                "must_preserve_subject_priority_rules": True,
                "must_preserve_world_continuity_rules": True,
            },
            "source_text_bundle": text_bundle,
            "content_contract": {"content_type": content_type, "format": content_format},
            "refs_contract": {
                "connected_refs_summary": _safe_dict(input_pkg.get("connected_context_summary")),
                "refs_inventory_keys": sorted([str(key) for key in refs_inventory.keys()])[:40],
            },
        },
    }
    return story_core_v1


def _detect_story_core_mode(input_pkg: dict[str, Any]) -> str:
    narrative_fields = (
        input_pkg.get("text"),
        input_pkg.get("story_text"),
        input_pkg.get("note"),
        input_pkg.get("director_note"),
    )
    has_directive = any(bool(str(value or "").strip()) for value in narrative_fields)
    return "directed" if has_directive else "creative"


def _has_textual_directive(input_pkg: dict[str, Any]) -> bool:
    return _detect_story_core_mode(input_pkg) == "directed"


def _build_audio_dramaturgy_summary(audio_map: dict[str, Any], input_pkg: dict[str, Any], content_type: str) -> dict[str, Any]:
    windows = [row for row in _safe_list(audio_map.get("scene_candidate_windows")) if isinstance(row, dict)]
    low_ids: list[str] = []
    medium_ids: list[str] = []
    high_ids: list[str] = []
    build_ids: list[str] = []
    release_ids: list[str] = []
    tail_ids: list[str] = []
    performance_ids: list[str] = []
    micro_transition_ids: list[str] = []
    observational_ids: list[str] = []

    for idx, row in enumerate(windows, start=1):
        scene_id = str(row.get("id") or f"sc_{idx}").strip()
        if not scene_id:
            continue
        local_energy_band = str(row.get("local_energy_band") or "").strip().lower()
        energy = str(row.get("energy") or "").strip().lower()
        if local_energy_band in {"low"}:
            energy = "low"
        elif local_energy_band in {"medium", "settle"}:
            energy = "medium"
        elif local_energy_band in {"high", "surge"}:
            energy = "high"
        if energy not in {"low", "medium", "high"}:
            energy = "medium"
        function = str(row.get("scene_function") or "").strip().lower()
        delivery_mode = str(row.get("delivery_mode") or "").strip().lower()
        finality_candidate = str(row.get("finality_candidate") or "").strip().lower()
        release_candidate = bool(row.get("release_candidate"))
        stillness_candidate = bool(row.get("stillness_candidate"))
        semantic_turn_candidate = bool(row.get("semantic_turn_candidate"))
        rhythmic_cut_reason = str(row.get("cut_reason") or "").strip().lower()
        t1 = _to_float(row.get("t1"), 0.0)
        duration = max(_coerce_duration_sec(audio_map.get("duration_sec")), 0.001)
        near_tail = t1 >= max(duration - 0.01, duration * 0.84)

        if energy == "low":
            low_ids.append(scene_id)
        elif energy == "high":
            high_ids.append(scene_id)
        else:
            medium_ids.append(scene_id)

        if any(token in function for token in ("build", "rise", "pressure", "escalat")):
            build_ids.append(scene_id)
        if release_candidate or finality_candidate in {"closure", "tail_hit"} or any(token in function for token in ("release", "drop", "resolve", "afterimage", "payoff")):
            release_ids.append(scene_id)
        if near_tail or any(token in function for token in ("tail", "afterimage", "outro", "resolution")):
            tail_ids.append(scene_id)

        if energy == "high" or delivery_mode in {"assertive", "pressurized"} or any(token in function for token in ("peak", "climax", "performance")):
            performance_ids.append(scene_id)
        if semantic_turn_candidate or finality_candidate == "hinge" or rhythmic_cut_reason == "transition" or any(token in function for token in ("transition", "turn", "reveal", "callback")):
            micro_transition_ids.append(scene_id)
        if stillness_candidate or delivery_mode in {"reflective", "intimate", "suspended", "observational", "final"} or energy == "low" or any(token in function for token in ("setup", "observe", "anchor", "breather", "release")):
            observational_ids.append(scene_id)

    dominant_energy = "medium"
    if len(high_ids) > max(len(low_ids), len(medium_ids)):
        dominant_energy = "high"
    elif len(low_ids) > max(len(high_ids), len(medium_ids)):
        dominant_energy = "low"

    return {
        "dramaturgy_source": "audio_primary",
        "audio_drives_dramaturgy": True,
        "content_type": str(content_type or "music_video"),
        "textual_directive_present": bool(_has_textual_directive(input_pkg)),
        "energy_profile": {
            "low": len(low_ids),
            "medium": len(medium_ids),
            "high": len(high_ids),
            "total_windows": len(windows),
        },
        "dominant_energy": dominant_energy,
        "energy_curve_summary": str(audio_map.get("global_arc_hint") or audio_map.get("analysis_mode") or "audio_arc_driven"),
        "peak_window_ids": high_ids[:6],
        "low_energy_window_ids": low_ids[:8],
        "medium_energy_window_ids": medium_ids[:8],
        "high_energy_window_ids": high_ids[:8],
        "build_window_ids": list(dict.fromkeys(build_ids))[:8],
        "release_window_ids": list(dict.fromkeys(release_ids))[:8],
        "tail_resolution_window_ids": list(dict.fromkeys(tail_ids))[:8],
        "performance_candidate_window_ids": list(dict.fromkeys(performance_ids))[:8],
        "micro_transition_candidate_window_ids": list(dict.fromkeys(micro_transition_ids))[:8],
        "observational_candidate_window_ids": list(dict.fromkeys(observational_ids))[:8],
        "suggested_arc": "ground -> build -> peak -> release -> afterimage",
    }


def _build_story_core_rules() -> dict[str, Any]:
    # Headwear rules must remain universal across future modes and subjects; do not overfit to baseball-cap-only scenarios.
    return {
        "audio_priority": {
            "primary_signals": [
                "rhythm_scene_timing_and_cut_structure_from_audio_map",
                "emotional_arc_from_audio_map",
            ],
            "secondary_signal": "lyrics_meaning_optional_only",
            "forbid_lyrics_only_story_derivation": True,
            "fallback_when_lyrics_weak": [
                "rhythm",
                "energy_pattern",
                "repetition_pattern",
                "emotional_contour",
                "user_concept",
            ],
        },
        "ref_usage": {
            "refs_are_cast_and_objects": True,
            "refs_are_world_anchors": True,
            "props_can_drive_dramaturgy": True,
            "role_ids_are_immutable": True,
            "forbid_character_role_id_swapping": True,
            "use_connected_refs_and_assigned_roles_as_truth": True,
            "forbid_rewriting_face_identity": True,
            "forbid_rewriting_key_prop_identity": True,
        },
        "appearance_policy": {
            "allow_controlled_styling_variation": True,
            "forbid_full_character_reinvention": True,
            "must_keep_face_identity": True,
            "must_keep_key_prop_identity": True,
            "must_keep_core_silhouette": True,
            "headwear_optional_not_mandatory": True,
            "forbid_default_headwear_takeoff_puton_actions": True,
            "forbid_scene_to_scene_full_wardrobe_replacement": True,
            "limit_active_object_complexity_per_scene": True,
        },
        "headwear_hair_compatibility": {
            "headwear_optional_not_mandatory": True,
            "headwear_is_look_component_not_default_action_object": True,
            "hair_must_be_physically_compatible_with_headwear": True,
            "hair_should_remain_natural_and_readable": True,
            "forbid_unnatural_hair_stuffing_under_headwear": True,
            "forbid_incompatible_top_volume_conflicts": True,
            "prefer_believable_arrangements_over_awkward_compression": True,
            "example_believable_arrangements": [
                "long loose hair",
                "long straight hair",
                "low ponytail",
                "tucked side hair when compatible",
            ],
            "examples_are_guidance_not_exhaustive_law": True,
        },
        "world_lock": {
            "respect_user_world_constraints": True,
            "examples": ["no neon", "no club", "realism", "local environment tone"],
            "allow_user_authorized_style_override_only": True,
            "forbid_wholesale_identity_replacement": True,
            "director_note_world_lock_is_hard_constraint": True,
            "forbid_neon_club_warehouse_drift_when_director_note_blocks_it": True,
            "audio_cannot_override_explicit_world_lock": True,
        },
        "core_stage_boundaries": {
            "forbid_route_selection": True,
            "forbid_i2v_ia2v_first_last_selection": True,
            "forbid_final_prompt_writing": True,
            "forbid_final_package_assembly": True,
        },
        "anti_literal_world_building": {
            "forbid_first_order_cliche_shorthand": True,
            "forbid_repetitive_noir_stereotype_language": True,
            "world_must_feel_specific_but_not_cartoon_literal": True,
            "prefer_second_order_visual_logic": [
                "rituals_and_social_codes",
                "distance_thresholds_and_witness_perspective",
                "ordinary_life_under_latent_pressure",
                "reactive_environment_details_and_aftermath",
            ],
            "atmosphere_is_world_logic_not_costume_shorthand": True,
        },
        "identity_inference_guard": {
            "if_character_ref_exists_do_not_invent_exact_styling_package": True,
            "forbid_invented_exact_wardrobe_accessories_headwear_jewelry_tattoos": True,
            "forbid_invented_facial_hair_specifics_without_grounding": True,
            "allow_only_ref_safe_presence_language": [
                "commanding",
                "restrained",
                "intimate",
                "watchful",
                "pressured",
                "reflective",
            ],
        },
        "segment_contrast_contract": {
            "adjacent_beats_must_change_min_1_frame_axis": ["scale_or_intimacy", "density", "motion_character"],
            "adjacent_beats_must_change_min_1_meaning_axis": ["narrative_function", "social_pressure", "hero_vs_world_ratio"],
            "forbid_adjacent_same_frame_plus_same_meaning": True,
        },
        "performance_vs_world_contract": {
            "performance_windows_can_foreground_vocal_owner": True,
            "world_cutaway_windows_must_not_recentre_hero_by_habit": True,
            "require_alternation_between_performance_world_observation_release": True,
            "if_character_1_lip_sync_only_i2v_should_stay_world_or_observational": True,
        },
        "audio_map_dramaturgy_contract": {
            "audio_map_is_timing_and_dramaturgy_hint_layer": True,
            "must_use_hints_for_contrast_progression_breath": True,
            "hints_to_use": [
                "local_energy_band",
                "delivery_mode",
                "semantic_weight",
                "semantic_turn_candidate",
                "release_candidate",
                "stillness_candidate",
                "finality_candidate",
            ],
            "release_and_stillness_windows_allow_sparse_afterimage_beats": True,
            "final_phrase_not_required_to_increase_density": True,
        },
    }


def _extract_story_user_concept(input_pkg: dict[str, Any]) -> str:
    for key in ("director_note", "note", "story_text", "text"):
        value = str(input_pkg.get(key) or "").strip()
        if value:
            return value[:1400]
    return ""


def _collect_story_core_refs_by_role(
    input_pkg: dict[str, Any],
    refs_inventory: dict[str, Any],
    connected_summary: dict[str, Any],
) -> tuple[dict[str, list[str]], list[str]]:
    normalized: dict[str, list[str]] = {}
    sources_used: set[str] = set()
    refs_by_role = _safe_dict(input_pkg.get("refs_by_role"))
    connected_refs_by_role = _safe_dict(connected_summary.get("refsByRole"))
    connected_refs_present = _safe_dict(connected_summary.get("refsPresentByRole"))
    connected_attached_present = _safe_dict(connected_summary.get("connectedRefsPresentByRole"))

    def _append(role: str, candidate: str, source: str) -> None:
        clean_role = str(role or "").strip()
        clean_url = str(candidate or "").strip()
        if not clean_role or not clean_url:
            return
        bucket = normalized.setdefault(clean_role, [])
        if clean_url not in bucket:
            bucket.append(clean_url)
            sources_used.add(source)

    for role, urls in refs_by_role.items():
        for value in _safe_list(urls):
            _append(str(role), str(value), "refs_by_role")
    for role, urls in connected_refs_by_role.items():
        for value in _safe_list(urls):
            _append(str(role), str(value), "connected_refs_by_role")
    for role, urls in connected_refs_present.items():
        for value in _safe_list(urls):
            _append(str(role), str(value), "refs_present_by_role")
    for role, urls in connected_attached_present.items():
        for value in _safe_list(urls):
            _append(str(role), str(value), "connected_refs_present_by_role")
    for role in ("character_1", "character_2", "character_3", "props", "location", "style", "animal", "group"):
        ref_key = _role_to_ref_key(role)
        for value in _extract_ref_urls(refs_inventory.get(ref_key)):
            _append(role, value, "refs_inventory")
        selected_refs = _safe_dict(input_pkg.get("selected_refs"))
        selected_value = selected_refs.get(role)
        if isinstance(selected_value, list):
            for value in _safe_list(selected_value):
                _append(role, str(value), "selected_refs")
        else:
            _append(role, str(selected_value or ""), "selected_refs")
        if bool(connected_refs_present.get(role)) and role not in normalized:
            normalized[role] = []
        if bool(connected_attached_present.get(role)) and role not in normalized:
            normalized[role] = []
    return normalized, sorted(sources_used)


def _build_story_core_compact_refs_manifest(
    *,
    refs_by_role: dict[str, list[str]],
    ref_attachment_summary: dict[str, Any],
    connected_summary: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    attached = _safe_dict(ref_attachment_summary)
    connected_present = _safe_dict(connected_summary.get("refsPresentByRole"))
    connected_attached_present = _safe_dict(connected_summary.get("connectedRefsPresentByRole"))
    connected_role_ids = [str(role).strip() for role in _safe_list(connected_summary.get("connectedRoleIds")) if str(role).strip()]
    ordered_roles = [
        "character_1", "character_2", "character_3", "props", "location", "style", "animal", "group",
    ]
    role_set: set[str] = set(connected_role_ids)
    role_set.update(str(role).strip() for role in refs_by_role.keys() if str(role).strip())
    for role in ordered_roles:
        if bool(connected_present.get(role)) or bool(connected_attached_present.get(role)):
            role_set.add(role)
    available_roles = sorted(role_set)
    label_map = {
        "character_1": "Character 1",
        "character_2": "Character 2",
        "character_3": "Character 3",
        "props": "Props",
        "location": "Location",
        "style": "Style",
        "animal": "Animal",
        "group": "Group",
    }
    attached_refs: dict[str, Any] = {}
    attached_roles: list[str] = []
    for role in available_roles:
        urls = _safe_list(refs_by_role.get(role))
        attachment_row = _safe_dict(attached.get(role))
        is_attached = bool(attachment_row.get("attached")) or bool(urls) or bool(connected_attached_present.get(role))
        if is_attached:
            attached_roles.append(role)
        attached_refs[role] = {
            "count": len(urls) or int(attachment_row.get("count") or 0) or (1 if bool(connected_attached_present.get(role)) else 0),
            "attached": is_attached,
            "label": label_map.get(role) or role.replace("_", " ").title(),
        }
    return {"available_roles": available_roles, "attached_refs": attached_refs}, attached_roles


def _extract_director_world_lock_summary(input_pkg: dict[str, Any], story_user_concept: str) -> str:
    user_text = " ".join(
        [
            str(input_pkg.get("director_note") or "").strip(),
            str(input_pkg.get("note") or "").strip(),
            str(input_pkg.get("story_text") or "").strip(),
            str(input_pkg.get("text") or "").strip(),
            str(story_user_concept or "").strip(),
        ]
    ).strip().lower()
    if not user_text:
        return ""
    markers = []
    for token in ("not club", "не клуб", "not neon", "не неон", "not warehouse", "не warehouse", "realistic"):
        if token in user_text:
            markers.append(token)
    return ", ".join(markers[:8])


def _build_story_core_input_context(
    *,
    input_pkg: dict[str, Any],
    audio_map: dict[str, Any],
    refs_inventory: dict[str, Any],
    prop_contracts: list[dict[str, Any]],
    ref_attachment_summary: dict[str, Any],
    grounding_level: str,
) -> dict[str, Any]:
    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    normalized_refs_by_role, refs_sources_used = _collect_story_core_refs_by_role(input_pkg, refs_inventory, connected_summary)
    compact_refs_manifest, attached_ref_roles = _build_story_core_compact_refs_manifest(
        refs_by_role=normalized_refs_by_role,
        ref_attachment_summary=ref_attachment_summary,
        connected_summary=connected_summary,
    )
    story_user_concept = _extract_story_user_concept(input_pkg)
    model_id = _resolve_active_video_model_id(input_pkg)
    i2v_profile = get_video_model_capability_profile(model_id, "i2v")
    audio_dramaturgy = _safe_dict(audio_map.get("audio_dramaturgy"))
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    core_segments, core_source = _coerce_core_audio_segments(audio_map)
    total_segments = len(core_segments)
    default_route_budget = _compute_route_budget_for_total(total_segments, creative_config)
    audio_summary = {
        "duration_sec": _to_float(audio_map.get("duration_sec"), 0.0),
        "analysis_mode": str(audio_map.get("analysis_mode") or ""),
        "global_arc_hint": str(audio_map.get("global_arc_hint") or ""),
        "energy_curve_summary": str(audio_dramaturgy.get("energy_curve_summary") or ""),
        "dominant_energy": str(audio_dramaturgy.get("dominant_energy") or ""),
        "window_counts": _safe_dict(audio_dramaturgy.get("window_counts")),
        "scene_candidate_window_count": len(_safe_list(audio_map.get("scene_candidate_windows"))),
        "segment_count": total_segments,
        "segments_source_of_truth": core_source,
        "segments_preview": [
            {
                "segment_id": str(item.get("segment_id") or ""),
                "t0": _to_float(item.get("t0"), 0.0),
                "t1": _to_float(item.get("t1"), 0.0),
                "intensity": _to_float(item.get("intensity"), 0.0),
                "is_lip_sync_candidate": bool(item.get("is_lip_sync_candidate")),
                "rhythmic_anchor": str(item.get("rhythmic_anchor") or "none"),
            }
            for item in core_segments[:16]
        ],
    }
    director_world_lock_summary = _extract_director_world_lock_summary(input_pkg, story_user_concept)
    connected_role_summary = {
        "connectedRoleIds": [str(role).strip() for role in _safe_list(connected_summary.get("connectedRoleIds")) if str(role).strip()],
        "refsPresentByRole": _safe_dict(connected_summary.get("refsPresentByRole")),
        "connectedRefsPresentByRole": _safe_dict(connected_summary.get("connectedRefsPresentByRole")),
    }
    compact_context = {
        "director_note": str(input_pkg.get("director_note") or "").strip(),
        "story_text": str(input_pkg.get("story_text") or "").strip(),
        "note": str(input_pkg.get("note") or "").strip(),
        "user_concept": story_user_concept,
        "audio_summary": audio_summary,
        "creative_config": creative_config,
        "route_mix_doctrine_seed": {
            "target_route_mix": default_route_budget,
            "target_route_ratios": {
                "ia2v": float(creative_config.get("lipsync_ratio") or 0.25),
                "i2v": float(creative_config.get("i2v_ratio") or 0.5),
                "first_last": float(creative_config.get("first_last_ratio") or 0.25),
            },
            "max_consecutive_lipsync": int(creative_config.get("max_consecutive_lipsync") or 2),
            "policy_note": "CORE writes doctrine only. SCENES assigns concrete route per segment.",
        },
        "core_audio_contract": {
            "canonical_source": "audio_map.segments[]",
            "canonical_key": "segment_id",
            "legacy_fields_not_canonical": ["scene_slots", "phrase_units", "scene_candidate_windows"],
            "required_1_to_1_output": "narrative_segments[] by segment_id",
        },
        "connected_role_summary": connected_role_summary,
        "compact_refs_manifest": compact_refs_manifest,
        "assigned_roles": _safe_dict(input_pkg.get("assigned_roles_override")),
        "story_core_prop_contracts": prop_contracts,
        "ownership_binding_inventory": _safe_list(input_pkg.get("ownership_binding_inventory")),
        "world_style_identity_constraints": {
            "director_world_lock_summary": director_world_lock_summary,
            "grounding_level": grounding_level,
        },
        "video_capability_canon": {
            "model_id": model_id,
            "capability_rules_source_version": get_capability_rules_source_version(),
            "story_core_planning_bounds": {
                "verified_safe": _safe_list(i2v_profile.get("verified_safe")),
                "experimental": _safe_list(i2v_profile.get("experimental")),
                "blocked": _safe_list(i2v_profile.get("blocked")),
                "policy": {
                    "verified_safe_can_drive_default_planning": True,
                    "experimental_is_opt_in_not_default": True,
                    "blocked_must_not_be_normalized": True,
                },
            },
        },
        "story_rules": _build_story_core_rules(),
    }
    return {
        **compact_context,
        "available_roles": _safe_list(compact_refs_manifest.get("available_roles")),
        "refs_by_role": normalized_refs_by_role,
        "story_core_attached_ref_roles": attached_ref_roles,
        "story_core_director_world_lock_summary": director_world_lock_summary,
        "story_core_compact_context_size_estimate": len(json.dumps(_compact_prompt_payload(compact_context), ensure_ascii=False)),
        "story_core_refs_sources_used": refs_sources_used,
    }


def _default_story_core_guidance(creative_config: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized_creative_config = _normalize_creative_config(creative_config)
    route_mix_doctrine, _ = _build_route_mix_doctrine_for_scenes(normalized_creative_config)
    return {
        "route_mix_doctrine_for_scenes": route_mix_doctrine,
        "world_progression_hints": [
            "single coherent world; world/location specifics must come from current text, refs, props, and already established context",
            "preserve identity, world, and style continuity across the full clip",
            "allow clip progression through performance intensity, framing variation, spatial variation inside one world family, emotional modulation, and camera intimacy",
            "do not introduce concrete setting lore when current input does not provide it",
        ],
        "viewer_contrast_rules": [
            "wide_framing_vs_close_intimacy",
            "held_stillness_vs_dynamic_motion",
            "environment_context_vs_subject_micro_expression",
            "measured_rhythm_vs_peak_intensity",
        ],
        "unexpected_realistic_beats": [
            "small environmental motion that supports realism",
            "brief framing reset that keeps subject/world continuity",
            "short pause or breath beat before renewed performance intensity",
            "camera distance variation without changing world family",
        ],
        "prop_guidance": {
            "continuity_object_role": "continuity/reference anchor",
            "carried_object_role": "owner-linked continuity/reference anchor when present",
            "binding_grammar": {
                "carried": "owner-bound continuity object; may affect visibility, handling, silhouette, and pose constraints when relevant",
                "held": "owner hand-occupied continuity object; visible handling possible without finger micro-choreography",
                "worn": "silhouette/look continuity anchor; not a default action driver",
                "pocketed": "owner-linked continuity object; optional visual emphasis",
                "nearby": "owner-adjacent continuity object; within reach when scene logic allows",
                "environment": "world-anchored scene element; not owner-locked prop",
            },
            "ownership_grammar": {
                "character_1": "primary-role owned object",
                "character_2": "support-role owned object",
                "character_3": "antagonist-role owned object",
                "shared": "multi-role shared object",
                "environment": "world/environment object",
            },
            "must_keep_same_object_identity_across_clip": True,
            "forbid_random_object_identity_changes": True,
            "object_is_functional_not_symbolic_decoration_by_default": True,
            "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present": True,
            "forbid_overused_hand_choreography_around_object": True,
            "forbid_magic_or_metaphor_prop_behavior": True,
        },
        "narrative_pressure_rules": [
            "primary narrative role remains the action spine unless current input explicitly reassigns it",
            "secondary performance role must not steal narrative spine unless current input explicitly asks for co-lead behavior",
            "do not invent extra characters unless grounded by current input, refs, or planner output",
            "do not impose ungrounded high-stakes narrative pressure unless current input explicitly supports it",
            "avoid repetitive scene function without meaningful variation",
            "meaningful variation may come from framing, emotional intensity, performance mode, spatial relation, camera distance, or local world detail",
        ],
        "world_richness_rules": [
            "grounded realism only; no spectacle-first escalation",
            "one coherent world logic across clip",
            "restrained motion scale; avoid complex hand/cloth gimmicks",
            "support performance roles may appear only when grounded by current input and refs while staying in the same world family",
            "do not inject hardcoded genre/location restrictions in fallback guidance",
        ],
    }


def _normalize_story_core_guidance(raw_guidance: Any, creative_config: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = _default_story_core_guidance(creative_config)
    row = _safe_dict(raw_guidance)
    prop_guidance = _safe_dict(row.get("prop_guidance"))
    fallback_prop_guidance = _safe_dict(fallback.get("prop_guidance"))
    return {
        "route_mix_doctrine_for_scenes": _safe_dict(row.get("route_mix_doctrine_for_scenes"))
        or _safe_dict(fallback.get("route_mix_doctrine_for_scenes")),
        "world_progression_hints": [str(item).strip() for item in _safe_list(row.get("world_progression_hints")) if str(item).strip()]
        or _safe_list(fallback.get("world_progression_hints")),
        "viewer_contrast_rules": [str(item).strip() for item in _safe_list(row.get("viewer_contrast_rules")) if str(item).strip()]
        or _safe_list(fallback.get("viewer_contrast_rules")),
        "unexpected_realistic_beats": [str(item).strip() for item in _safe_list(row.get("unexpected_realistic_beats")) if str(item).strip()]
        or _safe_list(fallback.get("unexpected_realistic_beats")),
        "prop_guidance": {
            "continuity_object_role": str(
                prop_guidance.get("continuity_object_role")
                or fallback_prop_guidance.get("continuity_object_role")
                or ""
            ).strip(),
            "carried_object_role": str(
                prop_guidance.get("carried_object_role")
                or fallback_prop_guidance.get("carried_object_role")
                or ""
            ).strip(),
            "binding_grammar": _safe_dict(prop_guidance.get("binding_grammar")) or _safe_dict(fallback_prop_guidance.get("binding_grammar")),
            "ownership_grammar": _safe_dict(prop_guidance.get("ownership_grammar")) or _safe_dict(fallback_prop_guidance.get("ownership_grammar")),
            "must_keep_same_object_identity_across_clip": bool(
                prop_guidance.get("must_keep_same_object_identity_across_clip")
                if "must_keep_same_object_identity_across_clip" in prop_guidance
                else fallback_prop_guidance.get("must_keep_same_object_identity_across_clip")
            ),
            "forbid_random_object_identity_changes": bool(
                prop_guidance.get("forbid_random_object_identity_changes")
                if "forbid_random_object_identity_changes" in prop_guidance
                else fallback_prop_guidance.get("forbid_random_object_identity_changes")
            ),
            "object_is_functional_not_symbolic_decoration_by_default": bool(
                prop_guidance.get("object_is_functional_not_symbolic_decoration_by_default")
                if "object_is_functional_not_symbolic_decoration_by_default" in prop_guidance
                else fallback_prop_guidance.get("object_is_functional_not_symbolic_decoration_by_default")
            ),
            "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present": bool(
                prop_guidance.get("carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present")
                if "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present" in prop_guidance
                else (
                    prop_guidance.get("carried_object_affects_posture_speed_balance_concealment_route_choice_body_tension_if_present")
                    if "carried_object_affects_posture_speed_balance_concealment_route_choice_body_tension_if_present" in prop_guidance
                    else (
                        fallback_prop_guidance.get(
                            "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present"
                        )
                        if "carried_object_affects_visibility_handling_silhouette_spatial_relation_pose_constraints_if_present"
                        in fallback_prop_guidance
                        else fallback_prop_guidance.get(
                            "carried_object_affects_posture_speed_balance_concealment_route_choice_body_tension_if_present"
                        )
                    )
                )
            ),
            "forbid_overused_hand_choreography_around_object": bool(
                prop_guidance.get("forbid_overused_hand_choreography_around_object")
                if "forbid_overused_hand_choreography_around_object" in prop_guidance
                else fallback_prop_guidance.get("forbid_overused_hand_choreography_around_object")
            ),
            "forbid_magic_or_metaphor_prop_behavior": bool(
                prop_guidance.get("forbid_magic_or_metaphor_prop_behavior")
                if "forbid_magic_or_metaphor_prop_behavior" in prop_guidance
                else fallback_prop_guidance.get("forbid_magic_or_metaphor_prop_behavior")
            ),
        },
        "narrative_pressure_rules": [str(item).strip() for item in _safe_list(row.get("narrative_pressure_rules")) if str(item).strip()]
        or _safe_list(fallback.get("narrative_pressure_rules")),
        "world_richness_rules": [str(item).strip() for item in _safe_list(row.get("world_richness_rules")) if str(item).strip()]
        or _safe_list(fallback.get("world_richness_rules")),
    }


def _build_core_validation_feedback(code: str, errors: list[str]) -> str:
    if code == CORE_ID_MISMATCH:
        details = "; ".join([str(item) for item in errors[:6]])
        return (
            "CORE_ID_MISMATCH: ID renaming is forbidden. Keep AUDIO segment_id values immutable and verbatim. "
            "Copy segment_id exactly from audio_map.segments[] in the same order. "
            "Do not rename/normalize/shorten/re-index/regenerate IDs. "
            "Do not convert seg_01 -> seg_0. Preserve zero padding exactly. "
            f"Mismatch details: {details}"
        )[:1400]
    if code == CORE_ROLE_BINDING_CONTRADICTION:
        return (
            "CORE_ROLE_BINDING_CONTRADICTION: character_1 is explicitly female/девушка. "
            "Do not describe character_1 as guy/man/male/парень/мужчина. "
            "character_2 is explicitly male/парень. "
            "Do not describe character_2 as girl/woman/female/девушка/женщина."
        )[:1400]
    if code == CORE_QUALITY_GATES_FAILED:
        details = "; ".join([str(item) for item in errors[:8]])
        return (
            "CORE_QUALITY_GATES_FAILED: improve semantic progression without changing identity/world/style locks, refs, genre, "
            "or established season/weather continuity. Increase adjacent frame+meaning contrast, rebalance hero/world dominance, "
            "avoid long literal fallback streaks, and restore breathing rhythm only when overload appears. "
            f"Gate details: {details}"
        )[:1400]
    details = "; ".join([str(item) for item in errors[:6]])
    return f"{code}: {details}"[:1400]


def _story_core_pressure_mode(row: dict[str, Any]) -> str:
    arc_role = str(row.get("arc_role") or "").strip().lower()
    text = f"{row.get('beat_purpose') or ''} {row.get('emotional_key') or ''}".lower()
    if arc_role == "climax" or any(token in text for token in ("panic", "rage", "chase", "impact", "crash", "violent", "urgent", "overload")):
        return "high"
    if arc_role in {"release", "afterglow"} or any(token in text for token in ("calm", "silence", "exhale", "quiet", "still", "gentle", "soft")):
        return "low"
    return "medium"


def _story_core_emotional_mode(row: dict[str, Any]) -> str:
    text = f"{row.get('beat_purpose') or ''} {row.get('emotional_key') or ''}".lower()
    if any(token in text for token in ("fear", "threat", "danger", "panic", "pressure", "anxious")):
        return "threat"
    if any(token in text for token in ("desire", "love", "intimate", "tender", "warmth", "longing")):
        return "intimate"
    if any(token in text for token in ("reflect", "memory", "regret", "echo", "distance", "hollow")):
        return "reflective"
    return "driving"


def _infer_story_core_dominance(row: dict[str, Any]) -> str:
    hero_world_mode = str(row.get("hero_world_mode") or "").strip().lower()
    if hero_world_mode == "hero_foreground":
        return "hero_first"
    if hero_world_mode == "world_foreground":
        return "world_first"
    if hero_world_mode == "balanced":
        return "balanced"
    text = f"{row.get('beat_purpose') or ''} {row.get('emotional_key') or ''}".lower()
    hero_score = sum(1 for token in ("hero", "decision", "assert", "lead", "choose", "command", "push") if token in text)
    world_score = sum(1 for token in ("crowd", "city", "weather", "system", "pressure", "response", "environment", "world") if token in text)
    if hero_score > world_score:
        return "hero_first"
    if world_score > hero_score:
        return "world_first"
    return "balanced"


def _score_story_core_association(row: dict[str, Any]) -> dict[str, Any]:
    text = f"{row.get('beat_purpose') or ''} {row.get('emotional_key') or ''}".lower()
    subtext_mode = str(row.get("subtext_mode") or "").strip().lower()
    association_target = str(row.get("association_target") or "").strip().lower()
    generic_env_tokens = (
        "alley", "street", "room", "warehouse", "corridor", "office", "bar", "club", "rooftop", "parking", "rain", "neon", "shadow",
    )
    threat_tokens = (
        "danger", "threat", "bad guys", "attack", "crime", "gun", "knife", "chase", "violence", "panic", "fear",
    )
    emotion_tokens = (
        "sad", "lonely", "alone", "angry", "afraid", "happy", "crying", "tears", "heartbroken", "desperate",
    )
    subtext_tokens = (
        "metaphor", "subtext", "indirect", "symbol", "counterpoint", "echo", "negative space", "off-screen", "implied", "second-order",
    )
    world_detail_tokens = (
        "ritual", "micro-detail", "texture", "environment reaction", "ambient", "incidental", "background action", "observer", "trace",
    )
    direct_mapping_markers = (
        "shows fear", "shows sadness", "to show", "illustrates", "literally", "because she is", "because he is",
    )

    generic_env_hits = sum(1 for token in generic_env_tokens if token in text)
    threat_hits = sum(1 for token in threat_tokens if token in text)
    emotion_hits = sum(1 for token in emotion_tokens if token in text)
    subtext_hits = sum(1 for token in subtext_tokens if token in text)
    world_detail_hits = sum(1 for token in world_detail_tokens if token in text)
    direct_mapping_hits = sum(1 for token in direct_mapping_markers if token in text)

    generic_cliche = generic_env_hits >= 2 and (threat_hits > 0 or emotion_hits > 0)
    direct_emotion_illustration = emotion_hits > 0 and (direct_mapping_hits > 0 or generic_env_hits > 0)
    direct_threat_shorthand = threat_hits >= 2 or (threat_hits > 0 and generic_env_hits > 0)
    structured_second_order = subtext_mode in {"second_order", "aftermath_trace", "witness_detail", "symbolic_environment"}
    structured_direct = subtext_mode == "direct"
    low_subtext = subtext_hits == 0 and world_detail_hits == 0 and not structured_second_order

    literal_score = 0
    if generic_cliche:
        literal_score += 2
    if direct_emotion_illustration:
        literal_score += 2
    if direct_threat_shorthand:
        literal_score += 2
    if low_subtext:
        literal_score += 1
    if direct_mapping_hits > 0:
        literal_score += 1

    depth_score = 0
    if subtext_hits > 0:
        depth_score += 2
    if world_detail_hits > 0:
        depth_score += 1
    if "contrast" in text or "counterpoint" in text:
        depth_score += 1

    if structured_second_order:
        depth_score += 2
    elif subtext_mode == "coded":
        depth_score += 1
    if association_target == "level_2_plus":
        depth_score += 1
    elif association_target == "level_1":
        literal_score += 0
    if structured_direct:
        literal_score += 1

    if depth_score >= 2:
        association_level = 2
    elif literal_score >= 5:
        association_level = 0
    else:
        association_level = 1

    reasons: list[str] = []
    if generic_cliche:
        reasons.append("generic_cliche_environment")
    if direct_emotion_illustration:
        reasons.append("direct_emotion_illustration")
    if direct_threat_shorthand:
        reasons.append("direct_threat_shorthand")
    if low_subtext:
        reasons.append("low_subtext")
    if depth_score >= 2:
        reasons.append("subtext_or_second_order_detail")
    if association_level == 1 and not reasons:
        reasons.append("direct_but_acceptable_setup")

    return {
        "association_level": association_level,
        "reason_tags": reasons,
        "literal_generic_cliche": generic_cliche,
        "direct_emotion_illustration": direct_emotion_illustration,
        "direct_threat_shorthand": direct_threat_shorthand,
        "direct_mapping_hits": direct_mapping_hits,
        "low_subtext": low_subtext,
    }


def _infer_frame_axes(row: dict[str, Any]) -> dict[str, str]:
    visual_scale = str(row.get("visual_scale") or "").strip().lower()
    visual_density = str(row.get("visual_density") or "").strip().lower()
    motion_profile = str(row.get("motion_profile") or "").strip().lower()
    if visual_scale in {"intimate", "medium", "wide"} and visual_density in {"sparse", "moderate", "dense"} and motion_profile in {"still", "controlled", "dynamic"}:
        return {"scale": visual_scale, "density": visual_density, "motion": motion_profile}
    text = f"{row.get('beat_purpose') or ''} {row.get('emotional_key') or ''}".lower()
    scale = "intimate" if any(token in text for token in ("intimate", "close", "private", "whisper")) else ("wide" if any(token in text for token in ("wide", "city", "crowd", "landscape")) else "medium")
    density = "dense" if any(token in text for token in ("crowd", "dense", "chaos", "packed", "flood")) else ("sparse" if any(token in text for token in ("empty", "quiet", "isolated", "vacant")) else "moderate")
    motion = "dynamic" if any(token in text for token in ("run", "rush", "chase", "impact", "surge", "spin")) else ("still" if any(token in text for token in ("still", "hold", "pause", "freeze", "quiet")) else "controlled")
    return {"scale": scale, "density": density, "motion": motion}


def _infer_meaning_axes(row: dict[str, Any]) -> dict[str, str]:
    beat_mode = str(row.get("beat_mode") or "").strip().lower()
    hero_world_mode = str(row.get("hero_world_mode") or "").strip().lower()
    narrative_function = str(row.get("arc_role") or "").strip().lower()
    if beat_mode in {"performance", "world_observation", "world_pressure", "aftermath", "threshold", "social_texture", "release", "transition"}:
        social_pressure = "high" if beat_mode in {"world_pressure", "threshold"} else ("low" if beat_mode in {"aftermath", "release"} else "medium")
    else:
        text = f"{row.get('beat_purpose') or ''} {row.get('emotional_key') or ''}".lower()
        social_pressure = "high" if any(token in text for token in ("judged", "crowd", "threat", "conflict", "pressure", "constraint")) else "low"
    if hero_world_mode in {"hero_foreground", "world_foreground", "balanced"}:
        hero_world_ratio = {
            "hero_foreground": "hero_first",
            "world_foreground": "world_first",
            "balanced": "balanced",
        }[hero_world_mode]
    else:
        hero_world_ratio = _infer_story_core_dominance(row)
    return {
        "narrative_function": narrative_function,
        "social_pressure": social_pressure,
        "hero_vs_world_ratio": hero_world_ratio,
    }


def _evaluate_story_core_quality_gates(
    narrative_segments: list[dict[str, Any]],
    *,
    content_type: str = "",
    director_mode: str = "",
    audio_map_quality_context: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    content_type_norm = str(content_type or "").strip().lower()
    director_mode_norm = str(director_mode or "").strip().lower()
    is_music_clip_mode = content_type_norm == "music_video" and director_mode_norm == "clip"
    diagnostics: dict[str, Any] = {
        "story_core_entropy_threshold_triggered": False,
        "story_core_flatline_span_start": "",
        "story_core_flatline_span_end": "",
        "story_core_flatline_reason": "",
        "story_core_flatline_repeated_story_functions": [],
        "story_core_flatline_repeated_pressure_mode": "",
        "story_core_flatline_retry_used": False,
        "story_core_two_axis_validation_passed": True,
        "story_core_axis_change_frame": [],
        "story_core_axis_change_meaning": [],
        "story_core_duplicate_adjacent_pairs": [],
        "story_core_semantic_duplicate_pairs": [],
        "story_core_visual_duplicate_pairs": [],
        "story_core_two_axis_fail_reason_by_pair": [],
        "story_core_two_axis_pair_status": [],
        "story_core_hero_world_balance_score": 1.0,
        "story_core_hero_first_streak": 0,
        "story_core_world_first_streak": 0,
        "story_core_reactive_world_rule_passed": True,
        "story_core_reactive_world_retry_used": False,
        "story_core_association_level_summary": {"level_0": 0, "level_1": 0, "level_2_plus": 0},
        "story_core_literal_fallback_count": 0,
        "story_core_literal_fallback_segments": [],
        "story_core_second_degree_segments": [],
        "story_core_association_scoring_reason_by_segment": [],
        "story_core_literal_generic_cliche_segments": [],
        "story_core_direct_emotion_illustration_segments": [],
        "story_core_low_subtext_segments": [],
        "story_core_direct_threat_shorthand_segments": [],
        "story_core_direct_mapping_segments": [],
        "story_core_anti_literal_retry_used": False,
        "story_core_visual_breath_triggered": False,
        "story_core_visual_breath_reason": "",
        "story_core_visual_breath_inserted_in_logic": False,
        "story_core_contrast_event_required": False,
        "story_core_contrast_event_present": False,
        "story_core_overload_spans": [],
        "story_core_local_breath_windows_checked": [],
        "story_core_local_breath_found": False,
        "story_core_local_breath_segment_ids": [],
        "story_core_visual_breath_fail_spans": [],
        "story_core_entropy_flatline_downgraded_to_warning": False,
        "story_core_entropy_flatline_downgrade_reason": "",
    }
    causes: list[str] = []
    if len(narrative_segments) < 2:
        return causes, diagnostics

    vectors: list[dict[str, Any]] = []
    for row in narrative_segments:
        association = _score_story_core_association(row)
        vectors.append(
            {
                "segment_id": str(row.get("segment_id") or "").strip(),
                "story_function": str(row.get("arc_role") or "").strip().lower(),
                "pressure_mode": _story_core_pressure_mode(row),
                "emotional_mode": _story_core_emotional_mode(row),
                "frame_axes": _infer_frame_axes(row),
                "meaning_axes": _infer_meaning_axes(row),
                "dominance": _infer_story_core_dominance(row),
                "association_level": int(association.get("association_level") or 1),
                "association_reason_tags": _safe_list(association.get("reason_tags")),
                "literal_generic_cliche": bool(association.get("literal_generic_cliche")),
                "direct_emotion_illustration": bool(association.get("direct_emotion_illustration")),
                "direct_threat_shorthand": bool(association.get("direct_threat_shorthand")),
                "direct_mapping_hits": int(association.get("direct_mapping_hits") or 0),
                "low_subtext": bool(association.get("low_subtext")),
            }
        )

    for idx in range(2, len(vectors)):
        chunk = vectors[idx - 2 : idx + 1]
        same_pressure = len({row["pressure_mode"] for row in chunk}) == 1
        same_story = len({row["story_function"] for row in chunk}) == 1
        same_emotion = len({row["emotional_mode"] for row in chunk}) == 1
        if same_pressure and (same_story or same_emotion):
            diagnostics["story_core_entropy_threshold_triggered"] = True
            diagnostics["story_core_flatline_span_start"] = chunk[0]["segment_id"]
            diagnostics["story_core_flatline_span_end"] = chunk[-1]["segment_id"]
            diagnostics["story_core_flatline_reason"] = "three_adjacent_segments_have_near_identical_dramatic_vector"
            diagnostics["story_core_flatline_repeated_story_functions"] = sorted({row["story_function"] for row in chunk})
            diagnostics["story_core_flatline_repeated_pressure_mode"] = chunk[0]["pressure_mode"]
            diagnostics["story_core_flatline_retry_used"] = True
            causes.append("entropy_flatline_detected")
            break

    for idx in range(1, len(vectors)):
        left = vectors[idx - 1]
        right = vectors[idx]
        pair_label = f"{left['segment_id']}->{right['segment_id']}"
        frame_changed_axes = [axis for axis in ("scale", "density", "motion") if left["frame_axes"][axis] != right["frame_axes"][axis]]
        meaning_changed_axes = [
            axis
            for axis in ("narrative_function", "social_pressure", "hero_vs_world_ratio")
            if left["meaning_axes"][axis] != right["meaning_axes"][axis]
        ]
        diagnostics["story_core_axis_change_frame"].append({"pair": pair_label, "axes": frame_changed_axes})
        diagnostics["story_core_axis_change_meaning"].append({"pair": pair_label, "axes": meaning_changed_axes})
        pair_status = "pass"
        fail_reason = ""
        if not frame_changed_axes and not meaning_changed_axes:
            diagnostics["story_core_duplicate_adjacent_pairs"].append(pair_label)
            pair_status = "hard_duplicate"
            fail_reason = "no_change_in_frame_or_meaning"
        elif frame_changed_axes and not meaning_changed_axes:
            diagnostics["story_core_semantic_duplicate_pairs"].append(pair_label)
            pair_status = "semantic_duplicate"
            fail_reason = "meaning_group_flat"
        elif meaning_changed_axes and not frame_changed_axes:
            diagnostics["story_core_visual_duplicate_pairs"].append(pair_label)
            pair_status = "visual_duplicate"
            fail_reason = "frame_group_flat"
        diagnostics["story_core_two_axis_pair_status"].append({"pair": pair_label, "status": pair_status})
        if fail_reason:
            diagnostics["story_core_two_axis_fail_reason_by_pair"].append({"pair": pair_label, "reason": fail_reason})
    has_hard_duplicates = bool(diagnostics["story_core_duplicate_adjacent_pairs"])
    has_semantic_duplicates = bool(diagnostics["story_core_semantic_duplicate_pairs"])
    has_visual_duplicates = bool(diagnostics["story_core_visual_duplicate_pairs"])
    diagnostics["story_core_two_axis_validation_passed"] = not has_hard_duplicates and not has_semantic_duplicates and not has_visual_duplicates
    if has_hard_duplicates:
        causes.append("two_axis_duplicate_adjacent_pairs")
    if has_semantic_duplicates:
        causes.append("two_axis_semantic_duplicates")
    if has_visual_duplicates:
        causes.append("two_axis_visual_duplicates")

    dominant_rows = [row["dominance"] for row in vectors]
    hero_count = sum(1 for row in dominant_rows if row == "hero_first")
    world_count = sum(1 for row in dominant_rows if row == "world_first")
    max_hero_streak = 0
    max_world_streak = 0
    run = 0
    prev = ""
    for current in dominant_rows:
        run = run + 1 if current == prev else 1
        prev = current
        if current == "hero_first":
            max_hero_streak = max(max_hero_streak, run)
        if current == "world_first":
            max_world_streak = max(max_world_streak, run)
    diagnostics["story_core_hero_first_streak"] = max_hero_streak
    diagnostics["story_core_world_first_streak"] = max_world_streak
    balance_score = 1.0 - (abs(hero_count - world_count) / max(1, len(dominant_rows)))
    streak_penalty = 0.15 * max(0, max(max_hero_streak, max_world_streak) - 2)
    diagnostics["story_core_hero_world_balance_score"] = round(max(0.0, balance_score - streak_penalty), 3)
    if max_hero_streak >= 3 or max_world_streak >= 3:
        diagnostics["story_core_reactive_world_rule_passed"] = False
        diagnostics["story_core_reactive_world_retry_used"] = True
        causes.append("reactive_world_streak_limit_exceeded")

    association_levels = [int(row["association_level"]) for row in vectors]
    level_0_segments = [vectors[i]["segment_id"] for i, level in enumerate(association_levels) if level <= 0]
    level_2_plus_segments = [vectors[i]["segment_id"] for i, level in enumerate(association_levels) if level >= 2]
    diagnostics["story_core_association_level_summary"] = {
        "level_0": len(level_0_segments),
        "level_1": sum(1 for level in association_levels if level == 1),
        "level_2_plus": len(level_2_plus_segments),
    }
    diagnostics["story_core_literal_fallback_count"] = len(level_0_segments)
    diagnostics["story_core_literal_fallback_segments"] = level_0_segments[:16]
    diagnostics["story_core_second_degree_segments"] = level_2_plus_segments[:16]
    diagnostics["story_core_association_scoring_reason_by_segment"] = [
        {"segment_id": row["segment_id"], "level": row["association_level"], "reasons": row["association_reason_tags"]}
        for row in vectors
    ][:32]
    diagnostics["story_core_literal_generic_cliche_segments"] = [
        row["segment_id"] for row in vectors if row["literal_generic_cliche"]
    ][:16]
    diagnostics["story_core_direct_emotion_illustration_segments"] = [
        row["segment_id"] for row in vectors if row["direct_emotion_illustration"]
    ][:16]
    diagnostics["story_core_direct_threat_shorthand_segments"] = [
        row["segment_id"] for row in vectors if row["direct_threat_shorthand"]
    ][:16]
    diagnostics["story_core_direct_mapping_segments"] = [
        row["segment_id"] for row in vectors if row["direct_mapping_hits"] > 0
    ][:16]
    diagnostics["story_core_low_subtext_segments"] = [row["segment_id"] for row in vectors if row["low_subtext"]][:16]
    min_second_degree_required = 2 if len(vectors) >= 5 else 1
    low_depth_profile = len(level_2_plus_segments) < min_second_degree_required
    high_literal_signal_segments = [
        row["segment_id"]
        for row in vectors
        if row["literal_generic_cliche"] or row["direct_emotion_illustration"] or row["direct_threat_shorthand"]
    ]
    high_literal_signal_count = len(high_literal_signal_segments)
    literal_signal_density_high = high_literal_signal_count > max(1, len(vectors) // 2)
    low_streak = 0
    max_low_streak = 0
    for level in association_levels:
        if level <= 1:
            low_streak += 1
            max_low_streak = max(max_low_streak, low_streak)
        else:
            low_streak = 0
    anti_literal_failed = False
    if low_depth_profile:
        if is_music_clip_mode:
            anti_literal_failed = (
                len(level_0_segments) > max(1, len(vectors) // 2)
                or (max_low_streak >= 4 and literal_signal_density_high)
                or high_literal_signal_count >= max(2, len(vectors) - 1)
            )
        else:
            anti_literal_failed = (
                len(level_0_segments) > 0
                or max_low_streak >= 3
                or literal_signal_density_high
            )
    if anti_literal_failed:
        causes.append("anti_literal_low_depth_streak")
    hard_literal_fallback = len(level_0_segments) > max(1, len(vectors) // 2)
    low_depth_streak_for_retry = max_low_streak >= (4 if is_music_clip_mode else 3)
    if hard_literal_fallback or low_depth_streak_for_retry or anti_literal_failed:
        diagnostics["story_core_anti_literal_retry_used"] = True
        if "anti_literal_low_depth_streak" not in causes:
            causes.append("anti_literal_low_depth_streak")
    if "anti_literal_low_depth_streak" in causes:
        diagnostics["story_core_anti_literal_retry_used"] = True

    overload_spans: list[dict[str, Any]] = []

    if diagnostics["story_core_entropy_threshold_triggered"]:
        overload_spans.append(
            {
                "start_segment_id": diagnostics["story_core_flatline_span_start"],
                "end_segment_id": diagnostics["story_core_flatline_span_end"],
                "reason": "entropy_threshold_triggered",
            }
        )

    for signal_name, predicate in (
        ("high_pressure_streak_3", lambda row: row["pressure_mode"] == "high"),
        ("high_density_streak_3", lambda row: row["frame_axes"]["density"] in {"high", "dense"}),
        ("hero_first_streak_3", lambda row: row["dominance"] == "hero_first"),
    ):
        run_start = -1
        run_len = 0
        for idx, row in enumerate(vectors):
            if predicate(row):
                if run_len == 0:
                    run_start = idx
                run_len += 1
            else:
                if run_len >= 3:
                    overload_spans.append(
                        {
                            "start_segment_id": vectors[run_start]["segment_id"],
                            "end_segment_id": vectors[idx - 1]["segment_id"],
                            "reason": signal_name,
                        }
                    )
                run_start = -1
                run_len = 0
        if run_len >= 3:
            overload_spans.append(
                {
                    "start_segment_id": vectors[run_start]["segment_id"],
                    "end_segment_id": vectors[len(vectors) - 1]["segment_id"],
                    "reason": signal_name,
                }
            )

    overload_detected = bool(overload_spans)
    diagnostics["story_core_overload_spans"] = overload_spans[:16]
    diagnostics["story_core_visual_breath_triggered"] = overload_detected
    diagnostics["story_core_contrast_event_required"] = overload_detected
    if overload_detected:
        diagnostics["story_core_visual_breath_reason"] = (
            "overload_detected_repeated_peak_pressure_density_or_dominance"
        )

    windows_checked: list[dict[str, Any]] = []
    local_breath_segment_ids: list[str] = []
    fail_spans: list[dict[str, Any]] = []
    for span in overload_spans:
        end_id = str(span.get("end_segment_id") or "")
        end_idx = next((idx for idx, row in enumerate(vectors) if row["segment_id"] == end_id), -1)
        if end_idx < 0:
            continue
        window_start = end_idx + 1
        window_end = min(len(vectors) - 1, end_idx + 2)
        candidate_ids = [vectors[idx]["segment_id"] for idx in range(window_start, window_end + 1)] if window_start <= window_end else []
        windows_checked.append(
            {
                "overload_end_segment_id": end_id,
                "window_segment_ids": candidate_ids,
            }
        )
        local_hit = False
        for idx in range(window_start, window_end + 1):
            row = vectors[idx]
            contrast_hit = (
                row["story_function"] in {"release", "afterglow"}
                or row["pressure_mode"] == "low"
                or row["frame_axes"]["density"] == "low"
                or row["dominance"] == "world_first"
                or row["emotional_mode"] == "intimate"
                or row["association_level"] >= 2
            )
            if contrast_hit:
                local_hit = True
                local_breath_segment_ids.append(row["segment_id"])
        if not local_hit:
            fail_spans.append(span)

    has_contrast_event = bool(local_breath_segment_ids)
    diagnostics["story_core_local_breath_windows_checked"] = windows_checked[:24]
    diagnostics["story_core_local_breath_segment_ids"] = sorted(set(local_breath_segment_ids))[:16]
    diagnostics["story_core_local_breath_found"] = has_contrast_event
    diagnostics["story_core_visual_breath_fail_spans"] = fail_spans[:16]
    diagnostics["story_core_contrast_event_present"] = has_contrast_event
    diagnostics["story_core_visual_breath_inserted_in_logic"] = has_contrast_event
    if overload_detected and fail_spans:
        causes.append("visual_breath_contrast_event_missing")

    if is_music_clip_mode and "entropy_flatline_detected" in causes:
        quality_ctx = _safe_dict(audio_map_quality_context)
        ids_preserved = bool(quality_ctx.get("segment_ids_preserved", True))
        coverage_ok = bool(quality_ctx.get("coverage_ok"))
        gap_sum_sec = _to_float(quality_ctx.get("gap_sum_sec"), 1.0)
        overlap_sum_sec = _to_float(quality_ctx.get("overlap_sum_sec"), 1.0)
        phrase_endings_ok = bool(quality_ctx.get("phrase_endings_ok", True))
        source_valid = bool(quality_ctx.get("audio_map_source_valid", True))
        has_structural_validity = (
            ids_preserved
            and coverage_ok
            and gap_sum_sec <= 1e-6
            and overlap_sum_sec <= 1e-6
            and phrase_endings_ok
            and source_valid
        )
        if has_structural_validity:
            causes = [code for code in causes if code != "entropy_flatline_detected"]
            diagnostics["story_core_entropy_flatline_downgraded_to_warning"] = True
            diagnostics["story_core_entropy_flatline_downgrade_reason"] = (
                "music_video_clip_phrase_regularization_with_valid_audio_map_structure"
            )

    return causes, diagnostics


def _normalize_gender_hint(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    female_tokens = (
        "female", "woman", "girl", "girlfriend", "wife", "lady", "she", "her",
        "девушка", "женщина", "жена", "она", "жен", "feminine",
    )
    male_tokens = (
        "male", "man", "guy", "boy", "boyfriend", "husband", "he", "him",
        "парень", "мужчина", "муж", "он", "masculine",
    )
    if any(t in token for t in female_tokens):
        return "female"
    if any(t in token for t in male_tokens):
        return "male"
    return ""


def _normalize_identity_label_hint(value: Any) -> str:
    token = str(value or "").strip().lower()
    if not token:
        return ""
    female_tokens = ("девушка", "женщина", "girl", "woman", "female", "lady", "feminine")
    male_tokens = ("парень", "мужчина", "guy", "man", "male", "masculine")
    if any(t in token for t in female_tokens):
        return "female"
    if any(t in token for t in male_tokens):
        return "male"
    return ""


def _extract_role_identity_expectations(input_pkg: dict[str, Any], assigned_roles: dict[str, Any]) -> dict[str, str]:
    expectations: dict[str, str] = {}
    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    nested_identity_sources: list[dict[str, Any]] = [
        _safe_dict(input_pkg.get("role_identity_mapping")),
        _safe_dict(input_pkg.get("character_identity_by_role")),
        _safe_dict(connected_summary.get("role_identity_mapping")),
        _safe_dict(connected_summary.get("character_identity_by_role")),
    ]
    for source in nested_identity_sources:
        for role in ("character_1", "character_2", "character_3"):
            if role in expectations:
                continue
            identity_row = _safe_dict(source.get(role))
            hint = _normalize_gender_hint(identity_row.get("gender_hint"))
            if not hint:
                hint = _normalize_identity_label_hint(identity_row.get("identity_label"))
            if hint:
                expectations[role] = hint
    candidate_sources: list[Any] = [
        assigned_roles,
        _safe_dict(input_pkg.get("roleTypeByRole")),
        _safe_dict(connected_summary.get("roleTypeByRole")),
        _safe_dict(connected_summary.get("assignedRoles")),
        _safe_dict(connected_summary.get("roleMapping")),
    ]
    for source in candidate_sources:
        row = _safe_dict(source)
        for role in ("character_1", "character_2", "character_3"):
            if role in expectations:
                continue
            hint = _normalize_gender_hint(row.get(role))
            if hint:
                expectations[role] = hint
    return expectations


def _extract_role_identity_mapping_payload(input_pkg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    sources: list[dict[str, Any]] = [
        _safe_dict(input_pkg.get("role_identity_mapping")),
        _safe_dict(connected_summary.get("role_identity_mapping")),
    ]
    out: dict[str, dict[str, Any]] = {}
    for source in sources:
        for role in ("character_1", "character_2", "character_3"):
            if role in out:
                continue
            row = _safe_dict(source.get(role))
            if not row:
                continue
            out[role] = {
                "identity_label": str(row.get("identity_label") or "").strip(),
                "gender_hint": str(row.get("gender_hint") or "").strip(),
            }
    return out


def _resolve_vocal_owner_role_by_gender(vocal_gender: str, role_gender_map: dict[str, str]) -> str:
    token = str(vocal_gender or "").strip().lower()
    if token not in {"female", "male"}:
        return "unknown"
    matches = [role for role in ("character_1", "character_2", "character_3") if role_gender_map.get(role) == token]
    return matches[0] if len(matches) == 1 else "unknown"


def _alias_pattern(alias: str) -> str:
    return rf"(?<![A-Za-zА-Яа-яЁё]){re.escape(str(alias or '').strip().lower())}(?![A-Za-zА-Яа-яЁё])"


def _role_has_opposite_gender_near_role(json_dump: str, role: str, expected_gender: str) -> tuple[bool, dict[str, str]]:
    opposite_aliases_by_expected = {
        "female": ("guy", "man", "male", "парень", "мужчина", "the guy", "the man"),
        "male": ("girl", "woman", "female", "девушка", "женщина", "the girl", "the woman"),
    }
    aliases = tuple(
        sorted(
            opposite_aliases_by_expected.get(str(expected_gender or "").strip().lower(), ()),
            key=lambda value: len(str(value or "").strip()),
            reverse=True,
        )
    )
    if not aliases:
        return False, {}
    escaped_role = re.escape(str(role or "").strip().lower())
    role_pattern = rf"(?<![a-z0-9_]){escaped_role}(?![a-z0-9_])"
    for alias in aliases:
        alias_pattern = _alias_pattern(str(alias or ""))
        patterns = (
            ("alias_with_role_parentheses", rf"{alias_pattern}\s*\(\s*{role_pattern}\s*\)"),
            ("role_with_alias_after_separator", rf"{role_pattern}\s*(?:is|=|:|-|—|–)\s*(?:an?\s+|the\s+)?{alias_pattern}"),
        )
        for pattern_name, pattern in patterns:
            hit = re.search(pattern, json_dump)
            if hit:
                excerpt_start = max(0, hit.start() - 48)
                excerpt_end = min(len(json_dump), hit.end() + 48)
                return True, {
                    "alias": str(alias),
                    "pattern": str(pattern_name),
                    "excerpt": json_dump[excerpt_start:excerpt_end],
                }
    return False, {}


def _detect_core_role_binding_contradictions(
    payload: dict[str, Any],
    role_identity_expectations: dict[str, str],
    debug_capture: dict[str, Any] | None = None,
) -> list[str]:
    if not role_identity_expectations:
        if debug_capture is not None:
            debug_capture["story_core_role_binding_contradiction_matches"] = []
        return []
    json_dump = json.dumps(payload, ensure_ascii=False).lower()
    contradictions: list[str] = []
    contradiction_matches: list[dict[str, str]] = []
    for role, expected_gender in role_identity_expectations.items():
        has_contradiction, match_meta = _role_has_opposite_gender_near_role(json_dump, role, expected_gender)
        if has_contradiction:
            alias = str(match_meta.get("alias") or "")
            contradictions.append(f"json_dump:{role}_expected_{expected_gender}_found_{alias}")
            contradiction_matches.append(
                {
                    "role": str(role),
                    "expected": str(expected_gender),
                    "alias": alias,
                    "pattern": str(match_meta.get("pattern") or ""),
                    "excerpt": str(match_meta.get("excerpt") or ""),
                }
            )
    if debug_capture is not None:
        debug_capture["story_core_role_binding_contradiction_matches"] = contradiction_matches[:8]
    return contradictions[:8]


def _collect_present_cast_roles(input_present_cast_roles: Any) -> set[str]:
    allowed = {"character_1", "character_2"}
    for row in _safe_list(input_present_cast_roles):
        token = str(row or "").strip().lower()
        if token.startswith("character_"):
            allowed.add(token)
    return allowed


def _detect_core_role_spawning_matches(
    payload: dict[str, Any],
    *,
    present_cast_roles: set[str],
    role_identity_expectations: dict[str, str],
) -> list[dict[str, str]]:
    forbidden_role_stage_fields = {"roster", "scene_casting", "role_plan", "assigned_cast", "cast_assignment"}
    role_id_pattern = re.compile(r"\bcharacter_(\d+)\b", flags=re.IGNORECASE)
    allowed_role_mentions = {
        "character_1",
        "character_2",
        "beat_primary_subject",
        "beat_secondary_subjects",
        "must_be_visible",
        "may_be_offscreen",
        "vocal_owner_role",
        "identity_doctrine",
        "narrative_backbone",
        "prompt_interface_contract",
    }
    if "character_3" in present_cast_roles:
        allowed_role_mentions.add("character_3")
    matches: list[dict[str, str]] = []

    def _append_match(path: str, reason: str, excerpt: str) -> None:
        if len(matches) >= 12:
            return
        matches.append({"path": path, "reason": reason, "excerpt": excerpt[:240]})

    def _walk(node: Any, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                key_str = str(key or "")
                key_l = key_str.strip().lower()
                child_path = f"{path}.{key_str}" if path else key_str
                if key_l in forbidden_role_stage_fields:
                    _append_match(
                        child_path,
                        "forbidden_role_stage_field",
                        f"{key_str}: {str(value)[:180]}",
                    )
                _walk(value, child_path)
            return
        if isinstance(node, list):
            for idx, row in enumerate(node):
                _walk(row, f"{path}[{idx}]")
            return
        if isinstance(node, str):
            text = str(node)
            text_l = text.lower()
            for field in forbidden_role_stage_fields:
                if re.search(rf"\b{re.escape(field)}\b", text_l):
                    _append_match(path, "forbidden_role_stage_field_mention", text)
                    break
            for hit in role_id_pattern.finditer(text_l):
                role_id = f"character_{hit.group(1)}".lower()
                if role_id in allowed_role_mentions:
                    continue
                if role_id in {"character_1", "character_2"}:
                    continue
                if role_id in present_cast_roles:
                    continue
                start = max(0, hit.start() - 48)
                end = min(len(text), hit.end() + 48)
                _append_match(path, "new_or_unconnected_role_id", text[start:end])

    _walk(payload, "$")
    contradiction_matches: list[dict[str, str]] = []
    json_dump = json.dumps(payload, ensure_ascii=False).lower()
    for role, expected_gender in role_identity_expectations.items():
        has_contradiction, match_meta = _role_has_opposite_gender_near_role(
            json_dump,
            role,
            expected_gender,
        )
        if has_contradiction:
            contradiction_matches.append(
                {
                    "path": "$",
                    "reason": f"identity_gender_conflict:{role}:expected_{expected_gender}",
                    "excerpt": str(match_meta.get("excerpt") or ""),
                }
            )
    return [*matches, *contradiction_matches][:12]


_STORY_CORE_TECHNICAL_LANGUAGE_PATTERNS = (
    r"\bcamera[_\s-]*intent\b",
    r"\bcamera[_\s-]*move(s|ment)?\b",
    r"\bcamera[_\s-]*tracks?\b",
    r"\bcamera[_\s-]*motion\b",
    r"\bshot[_\s-]*framing\b",
    r"\bclose[_\s-]?up\b",
    r"\bmedium[_\s-]*shot\b",
    r"\bwide[_\s-]*shot\b",
    r"\btracking[_\s-]*shot\b",
    r"\bdolly\b",
    r"\bzoom\b",
    r"\bpan(?:\s+(?:left|right|up|down))?\b",
    r"\btilt(?:\s+(?:up|down))?\b",
    r"\bsubject[_\s-]*motion\b",
    r"\bmotion[_\s-]*profile\b",
    r"\b(?:positive|negative)[_\s-]*prompt\b",
    r"\brenderer\b",
    r"\bdelivery\b",
    r"\bworkflow\b",
    r"\bmodel[_\s-]*id\b",
    r"\bframe[_\s-]*strategy\b",
    r"\bdelivery(?:[_\s-]*mode)?\b",
    r"\bvisual[_\s-]*profile\b",
    r"\bframe[_\s-]*axis\b",
    r"\bmeaning[_\s-]*axis\b",
    r"\bhero_world_mode\b",
    r"\bbeat_mode\b",
    r"\bassociation_target\b",
    r"\blevel[_\s-]*2[_\s-]*plus\b",
    r"\bsecond[_\s-]*order(?:\s+as\s+label)?\b",
    r"\b(?:validator|schema|contract|prompt|retry)\b",
    r"\b(?:field|enum)[_\s-]*(?:like|value|label|slot)\b",
    r"\b(?:foreground|background)\b[^\n]{0,24}\b(?:schema|contract|mode|slot|balance)\b",
)
_STORY_CORE_ROUTE_LEAKAGE_PATTERNS = (
    r"\b(?:segment|scene)\s*\d+[^\n]{0,40}\buses?\b[^\n]{0,24}\b(i2v|ia2v|first_last)\b",
    r"\bsegment[_\s-]*id[^a-z0-9]{0,8}(seg[_\s-]*\d+)[^\n]{0,40}\buses?\b[^\n]{0,24}\b(i2v|ia2v|first_last)\b",
    r"\broute\b[^\n]{0,24}\b(i2v|ia2v|first_last)\b",
)
_STORY_CORE_ROUTE_TOKENS = ("i2v", "ia2v", "first_last")

_STORY_CORE_STRUCTURED_SEGMENT_FIELDS = {
    "segment_id",
    "arc_role",
    "visual_scale",
    "visual_density",
    "motion_profile",
    "hero_world_mode",
    "beat_mode",
    "subtext_mode",
    "association_target",
}
_STORY_CORE_NARRATIVE_TOP_LEVEL_FIELDS = ("story_summary", "opening_anchor", "ending_callback_rule")
_STORY_CORE_NARRATIVE_GLOBAL_ARC_FIELDS = ("exposition", "climax", "resolution")
_STORY_CORE_NARRATIVE_IDENTITY_DOCTRINE_FIELDS = ("hero_anchor", "world_doctrine", "style_doctrine")
_STORY_CORE_NARRATIVE_SEGMENT_PRIORITY_FIELDS = ("beat_purpose", "emotional_key")
_STORY_CORE_LITERAL_LIST_MARKERS = (
    "to show",
    "show ",
    "showing",
    "показывать",
    "показать",
    "покажи",
    "показываем",
)
_STORY_CORE_DIRECT_MAPPING_MARKERS = (
    "shows fear",
    "shows sadness",
    "illustrates",
    "literally",
    "because she is",
    "because he is",
    "чтобы показать",
)


def _is_music_video_clip_mode(content_type: str, director_mode: str) -> bool:
    return str(content_type or "").strip().lower() == "music_video" and str(director_mode or "").strip().lower() == "clip"


def _extract_story_core_anchor_phrases(text: str) -> list[str]:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return []
    anchors: list[str] = []
    for pattern in (
        r"\bодесс\w*\b",
        r"\bodessa\b",
        r"\bport\b",
        r"\bharbor\b",
        r"\bsea\b",
        r"\bprimors\w*\b",
        r"\bпотемкин\w*\b",
        r"\bбульвар\w*\b",
        r"\bдвор\w*\b",
        r"\bлестниц\w*\b",
    ):
        for hit in re.findall(pattern, lowered, flags=re.IGNORECASE):
            token = str(hit or "").strip()
            if token and token not in anchors:
                anchors.append(token)
    return anchors[:6]


def _has_opening_world_anchor_signal(text: str) -> bool:
    lowered = str(text or "").strip().lower()
    if not lowered:
        return False
    return any(
        token in lowered
        for token in (
            "morning",
            "dawn",
            "quiet light",
            "black sea",
            "sea",
            "seagull",
            "gulls",
            "port",
            "harbor",
            "harbour",
            "quay",
            "shore",
            "embankment",
            "boulevard",
        )
    )


def _is_intentional_clip_subject_alternation(
    beat_subjects: list[str],
    *,
    primary_subject: str,
    content_type: str,
    director_mode: str,
) -> bool:
    if not _is_music_video_clip_mode(content_type, director_mode):
        return False
    sequence = [str(item or "").strip() for item in beat_subjects if str(item or "").strip()]
    if len(sequence) < 4:
        return False
    allowed = {primary_subject, "world"}
    if not set(sequence).issubset(allowed):
        return False
    if not ({primary_subject, "world"} <= set(sequence)):
        return False
    alternations = sum(1 for idx in range(1, len(sequence)) if sequence[idx] != sequence[idx - 1])
    return alternations >= max(2, math.floor((len(sequence) - 1) * 0.7))


def _transition_handoff_is_explicit(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    content_type: str,
    director_mode: str,
) -> bool:
    left_subject = str(left.get("beat_primary_subject") or "").strip()
    right_subject = str(right.get("beat_primary_subject") or "").strip()
    if not left_subject or not right_subject or left_subject == right_subject:
        return False
    if not _is_music_video_clip_mode(content_type, director_mode):
        return True
    merged_signal = " ".join(
        [
            str(left.get("beat_focus_hint") or ""),
            str(right.get("beat_focus_hint") or ""),
            str(left.get("group_reason") or ""),
            str(right.get("group_reason") or ""),
        ]
    ).lower()
    explicit_tokens = (
        "handoff",
        "passes",
        "pass to",
        "hands over",
        "takes from",
        "gives",
        "exchange",
        "ownership",
        "transfer",
        "enter",
        "exit",
        "reveals",
        "focus shifts to",
    )
    explicit_signal = any(token in merged_signal for token in explicit_tokens)
    reason_signal = str(right.get("story_function") or "") == "transition_turn" and str(left.get("story_function") or "") != str(
        right.get("story_function") or ""
    )
    return explicit_signal or reason_signal


def _compress_story_core_literal_segment_text(
    row: dict[str, Any],
    *,
    arc_role: str,
    anchor_hint: str,
    previous_function_phrase: str = "",
    include_anchor_tail: bool = False,
) -> tuple[dict[str, Any], bool]:
    rewritten = dict(row)
    beat_purpose = str(row.get("beat_purpose") or "").strip()
    emotional_key = str(row.get("emotional_key") or "").strip()
    merged_text = f"{beat_purpose} {emotional_key}".strip().lower()
    comma_count = merged_text.count(",")
    literal_marker_hit = any(marker in merged_text for marker in _STORY_CORE_LITERAL_LIST_MARKERS)
    direct_marker_hit = any(marker in merged_text for marker in _STORY_CORE_DIRECT_MAPPING_MARKERS)
    list_like_hit = comma_count >= 2
    should_rewrite = bool(beat_purpose) and (literal_marker_hit or direct_marker_hit or list_like_hit)
    if not should_rewrite:
        return rewritten, False

    function_map = {
        "setup": (
            "Frame the opening through observable place cues before committing to direct performance emphasis",
            "Establish world and intent through concrete context before explicit escalation begins",
        ),
        "build": (
            "Increase pressure by layering social observation, movement vectors, and tighter relational distance",
            "Escalate momentum through accumulating witness detail and compressing relational distance",
        ),
        "pivot": (
            "Reframe intent through a perspective shift so stakes change without literal explanation",
            "Turn the dramatic axis by redirecting consequence and viewpoint without explicit exposition",
        ),
        "climax": (
            "Drive peak force where performed intent meets external reaction and consequence",
            "Push maximum pressure as hero action collides with public consequence and unstable control",
        ),
        "release": (
            "Decompress through residue and recovery signals while continuity still holds",
            "Shift from impact to recovery while preserving continuity pressure in the same world frame",
        ),
        "afterglow": (
            "Close on a persistent trace where inner resolve and world rhythm align",
            "Land on lingering resonance where cost remains visible but emotional direction stabilizes",
        ),
    }
    emotional_map = {
        "setup": "measured curiosity with latent tension",
        "build": "mounting urgency carried by crowded bystanders, tightening street distance, and visible public attention",
        "pivot": "charged uncertainty with unstable balance of control",
        "climax": "high-voltage confrontation under social pressure",
        "release": "controlled exhale with reflective drag",
        "afterglow": "quiet resonance, intimate but still externally aware",
    }
    beat_mode_map = {
        "world_observation": "Prioritize environment causality and public texture over explicit lyric paraphrase",
        "performance": "Let body rhythm and vocal intent carry narrative progression in-frame",
        "transition": "Mark the turn with bridging evidence so the shift reads as motivated",
    }
    hero_world_map = {
        "hero_foreground": "Keep hero agency legible while world response remains consequential",
        "world_foreground": "Keep the world in command and let hero presence read through reactions and traces",
        "balanced": "Balance hero signal and environmental counterweight within the same beat",
    }
    subtext_map = {
        "second_order": "Show meaning through concrete environment cues: distance between people, blocked paths, doorways, and visible reactions in frame",
        "coded": "Use concrete visual behavior instead of slogans: exchanged glances, guarded posture, interrupted movement, and objects handled with caution",
        "direct": "Preserve clear intent while avoiding literal replay of source text",
    }
    role = str(arc_role or "").strip().lower()
    beat_mode = str(row.get("beat_mode") or "").strip().lower()
    hero_world_mode = str(row.get("hero_world_mode") or "").strip().lower()
    subtext_mode = str(row.get("subtext_mode") or "").strip().lower()
    mode_clause = beat_mode_map.get(beat_mode) or "Keep progression tied to concrete visual logic"
    foreground_clause = hero_world_map.get(hero_world_mode) or "Keep subject/world hierarchy coherent for this beat"
    subtext_clause = subtext_map.get(subtext_mode) or "Keep cues concrete: specific place details, physical textures, and visible behavior instead of abstract labels"
    choices = function_map.get(role) or function_map["build"]
    phrase_seed = f"{str(row.get('segment_id') or '').strip()}|{beat_mode}|{hero_world_mode}|{subtext_mode}"
    selected_idx = int(hashlib.sha1(phrase_seed.encode("utf-8")).hexdigest(), 16) % len(choices)
    selected_phrase = choices[selected_idx]
    if previous_function_phrase and selected_phrase == previous_function_phrase:
        selected_phrase = choices[(selected_idx + 1) % len(choices)]
    anchor_tail = f" Anchors stay grounded in {anchor_hint}." if (include_anchor_tail and anchor_hint) else ""
    rewritten["beat_purpose"] = f"{selected_phrase}. {mode_clause}. {foreground_clause}.{anchor_tail}".strip()
    rewritten["emotional_key"] = f"{emotional_map.get(role) or emotional_map['build']}; {subtext_clause.lower()}"
    return rewritten, True


def _apply_story_core_semantic_compression(
    payload: dict[str, Any],
    *,
    content_type: str,
    director_mode: str,
    user_concept: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not _is_music_video_clip_mode(content_type, director_mode):
        return payload, {"applied": False, "rewritten_segments": []}
    normalized = deepcopy(_safe_dict(payload))
    if not normalized:
        return normalized, {"applied": False, "rewritten_segments": []}
    anchor_tokens = _extract_story_core_anchor_phrases(user_concept)
    anchor_hint = ", ".join(anchor_tokens[:3])
    rewritten_segments: list[str] = []
    output_segments: list[dict[str, Any]] = []
    previous_function_phrase = ""
    last_anchor_tail_idx = -99
    for idx, row in enumerate(_safe_list(normalized.get("narrative_segments"))):
        segment = _safe_dict(row)
        segment_id = str(segment.get("segment_id") or "").strip()
        include_anchor_tail = bool(anchor_hint) and (idx == 0 or idx - last_anchor_tail_idx >= 4)
        rewritten_row, changed = _compress_story_core_literal_segment_text(
            segment,
            arc_role=str(segment.get("arc_role") or ""),
            anchor_hint=anchor_hint,
            previous_function_phrase=previous_function_phrase,
            include_anchor_tail=include_anchor_tail,
        )
        if changed and segment_id:
            rewritten_segments.append(segment_id)
            previous_function_phrase = str(rewritten_row.get("beat_purpose") or "").split(".", 1)[0].strip().lower()
            if "anchors stay grounded in " in str(rewritten_row.get("beat_purpose") or "").lower():
                last_anchor_tail_idx = idx
            if str(rewritten_row.get("subtext_mode") or "").strip().lower() == "direct":
                rewritten_row["subtext_mode"] = "coded"
            if str(rewritten_row.get("association_target") or "").strip().lower() == "level_1":
                rewritten_row["association_target"] = "level_2_plus"
        output_segments.append(rewritten_row)
    if output_segments:
        normalized["narrative_segments"] = output_segments
    return normalized, {"applied": bool(rewritten_segments), "rewritten_segments": rewritten_segments[:24]}


def _story_core_forbidden_zones_text(payload: dict[str, Any]) -> list[tuple[str, str]]:
    normalized_zones: list[tuple[str, str]] = []
    for field_name in _STORY_CORE_NARRATIVE_TOP_LEVEL_FIELDS:
        value = str(payload.get(field_name) or "").strip()
        if value:
            normalized_zones.append((field_name, value.lower()))
    global_arc = _safe_dict(payload.get("global_arc"))
    for field_name in _STORY_CORE_NARRATIVE_GLOBAL_ARC_FIELDS:
        value = str(global_arc.get(field_name) or "").strip()
        if value:
            normalized_zones.append((f"global_arc.{field_name}", value.lower()))
    identity_doctrine = _safe_dict(payload.get("identity_doctrine"))
    for field_name in _STORY_CORE_NARRATIVE_IDENTITY_DOCTRINE_FIELDS:
        value = str(identity_doctrine.get(field_name) or "").strip()
        if value:
            normalized_zones.append((f"identity_doctrine.{field_name}", value.lower()))
    for idx, segment in enumerate(_safe_list(payload.get("narrative_segments"))):
        row = _safe_dict(segment)
        for field_name in _STORY_CORE_NARRATIVE_SEGMENT_PRIORITY_FIELDS:
            value = str(row.get(field_name) or "").strip()
            if value:
                normalized_zones.append((f"narrative_segments[{idx}].{field_name}", value.lower()))
        for key, raw_value in row.items():
            key_name = str(key or "").strip()
            if not key_name or key_name in _STORY_CORE_STRUCTURED_SEGMENT_FIELDS or key_name in _STORY_CORE_NARRATIVE_SEGMENT_PRIORITY_FIELDS:
                continue
            if isinstance(raw_value, str) and raw_value.strip():
                normalized_zones.append((f"narrative_segments[{idx}].{key_name}", raw_value.strip().lower()))
    return normalized_zones


def _find_story_core_forbidden_technical_match(payload: dict[str, Any]) -> dict[str, Any]:
    normalized_zones = _story_core_forbidden_zones_text(payload)
    for pattern in _STORY_CORE_TECHNICAL_LANGUAGE_PATTERNS:
        compiled = re.compile(pattern)
        for zone_name, zone_text in normalized_zones:
            hit = compiled.search(zone_text)
            if hit:
                return {
                    "match_type": "technical_language_pattern",
                    "pattern": pattern,
                    "zone": zone_name,
                    "zone_text": zone_text,
                    "match_obj": hit,
                    "raw_term": hit.group(0) if hasattr(hit, "group") else "",
                }
    for token in _STORY_CORE_ROUTE_TOKENS:
        for zone_name, zone_text in normalized_zones:
            if token in zone_text:
                return {
                    "match_type": "route_token",
                    "pattern": token,
                    "zone": zone_name,
                    "zone_text": zone_text,
                    "match_obj": None,
                    "raw_term": token,
                }
    for pattern in _STORY_CORE_ROUTE_LEAKAGE_PATTERNS:
        compiled = re.compile(pattern)
        for zone_name, zone_text in normalized_zones:
            hit = compiled.search(zone_text)
            if hit:
                return {
                    "match_type": "route_leakage_pattern",
                    "pattern": pattern,
                    "zone": zone_name,
                    "zone_text": zone_text,
                    "match_obj": hit,
                    "raw_term": hit.group(0) if hasattr(hit, "group") else "",
                }
    return {}


def _sanitize_story_core_forbidden_technical_language(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str], bool]:
    sanitized = deepcopy(_safe_dict(payload))
    if not sanitized:
        return sanitized, [], False

    removed_terms: list[str] = []

    def _sanitize_text(value: str) -> str:
        out = str(value or "")
        for pattern in _STORY_CORE_TECHNICAL_LANGUAGE_PATTERNS:
            compiled = re.compile(pattern, flags=re.IGNORECASE)
            hits = compiled.findall(out)
            if hits:
                if isinstance(hits[0], tuple):
                    removed_terms.extend([str(item[0]) for item in hits if item and str(item[0]).strip()])
                else:
                    removed_terms.extend([str(item) for item in hits if str(item).strip()])
            out = compiled.sub(" ", out)
        for pattern in _STORY_CORE_ROUTE_LEAKAGE_PATTERNS:
            compiled = re.compile(pattern, flags=re.IGNORECASE)
            for hit in compiled.finditer(out):
                removed_terms.append(str(hit.group(0) or "").strip())
            out = compiled.sub(" ", out)
        for token in _STORY_CORE_ROUTE_TOKENS:
            token_compiled = re.compile(rf"\b{re.escape(token)}\b", flags=re.IGNORECASE)
            if token_compiled.search(out):
                removed_terms.append(token)
            out = token_compiled.sub(" ", out)
        out = re.sub(r"\s{2,}", " ", out).strip()
        return out

    def _sanitize_field(row: dict[str, Any], field_name: str) -> None:
        if field_name not in row:
            return
        if isinstance(row.get(field_name), str):
            row[field_name] = _sanitize_text(str(row.get(field_name) or ""))

    for field_name in _STORY_CORE_NARRATIVE_TOP_LEVEL_FIELDS:
        _sanitize_field(sanitized, field_name)

    global_arc = _safe_dict(sanitized.get("global_arc"))
    for field_name in _STORY_CORE_NARRATIVE_GLOBAL_ARC_FIELDS:
        _sanitize_field(global_arc, field_name)
    if global_arc:
        sanitized["global_arc"] = global_arc

    identity_doctrine = _safe_dict(sanitized.get("identity_doctrine"))
    for field_name in _STORY_CORE_NARRATIVE_IDENTITY_DOCTRINE_FIELDS:
        _sanitize_field(identity_doctrine, field_name)
    if identity_doctrine:
        sanitized["identity_doctrine"] = identity_doctrine

    narrative_segments: list[dict[str, Any]] = []
    for row in _safe_list(sanitized.get("narrative_segments")):
        segment = _safe_dict(row)
        if not segment:
            narrative_segments.append(segment)
            continue
        for field_name in _STORY_CORE_NARRATIVE_SEGMENT_PRIORITY_FIELDS:
            _sanitize_field(segment, field_name)
        for key_name, raw_value in list(segment.items()):
            field_name = str(key_name or "").strip()
            if (
                not field_name
                or field_name in _STORY_CORE_STRUCTURED_SEGMENT_FIELDS
                or field_name in _STORY_CORE_NARRATIVE_SEGMENT_PRIORITY_FIELDS
            ):
                continue
            if isinstance(raw_value, str):
                segment[field_name] = _sanitize_text(raw_value)
        narrative_segments.append(segment)
    if narrative_segments:
        sanitized["narrative_segments"] = narrative_segments
    deduped_terms = sorted({term.strip() for term in removed_terms if term and term.strip()})[:32]
    changed = json.dumps(_safe_dict(payload), ensure_ascii=False, sort_keys=True) != json.dumps(sanitized, ensure_ascii=False, sort_keys=True)
    return sanitized, deduped_terms, changed


def _validate_story_core_v11_payload(
    *,
    payload: dict[str, Any],
    audio_segments: list[dict[str, Any]],
    user_concept: str,
    role_identity_expectations: dict[str, str] | None = None,
    present_cast_roles: set[str] | None = None,
    debug_capture: dict[str, Any] | None = None,
    content_type: str = "",
    director_mode: str = "",
    audio_map_quality_context: dict[str, Any] | None = None,
) -> tuple[bool, str, list[str]]:
    # Role binding quick checks:
    # Should PASS:
    # - "the guy (character_2) while the girl (character_1)"
    # - "character_1 is the woman; character_2 is the man"
    # Should FAIL:
    # - "the guy (character_1)"
    # - "character_2 is the girl"
    errors: list[str] = []
    required_strings = ("core_version", "story_summary", "opening_anchor", "ending_callback_rule")
    for key in required_strings:
        if not str(payload.get(key) or "").strip():
            errors.append(f"missing_or_empty:{key}")
    if str(payload.get("core_version") or "").strip() != "1.1":
        errors.append("core_version_must_be_1.1")
    global_arc = _safe_dict(payload.get("global_arc"))
    identity_doctrine = _safe_dict(payload.get("identity_doctrine"))
    for key in ("exposition", "climax", "resolution"):
        if not str(global_arc.get(key) or "").strip():
            errors.append(f"missing_global_arc:{key}")
    for key in ("hero_anchor", "world_doctrine", "style_doctrine"):
        if not str(identity_doctrine.get(key) or "").strip():
            errors.append(f"missing_identity_doctrine:{key}")
    if errors:
        return False, CORE_SCHEMA_INVALID, errors

    narrative_segments = [row for row in _safe_list(payload.get("narrative_segments")) if isinstance(row, dict)]
    if not narrative_segments:
        return False, CORE_SCHEMA_INVALID, ["narrative_segments_missing_or_empty"]
    allowed_roles = {"setup", "build", "pivot", "climax", "release", "afterglow"}
    allowed_segment_fields = {
        "visual_scale": {"intimate", "medium", "wide"},
        "visual_density": {"sparse", "moderate", "dense"},
        "motion_profile": {"still", "controlled", "dynamic"},
        "hero_world_mode": {"hero_foreground", "world_foreground", "balanced"},
        "beat_mode": {"performance", "world_observation", "world_pressure", "aftermath", "threshold", "social_texture", "release", "transition"},
        "subtext_mode": {"direct", "coded", "second_order", "aftermath_trace", "witness_detail", "symbolic_environment"},
        "association_target": {"level_1", "level_2_plus"},
    }
    for idx, row in enumerate(narrative_segments, start=1):
        if not str(row.get("segment_id") or "").strip():
            errors.append(f"narrative_segments[{idx}] missing segment_id")
        if str(row.get("arc_role") or "").strip() not in allowed_roles:
            errors.append(f"narrative_segments[{idx}] invalid arc_role")
        if not str(row.get("beat_purpose") or "").strip():
            errors.append(f"narrative_segments[{idx}] missing beat_purpose")
        if not str(row.get("emotional_key") or "").strip():
            errors.append(f"narrative_segments[{idx}] missing emotional_key")
        for field_name, allowed_values in allowed_segment_fields.items():
            value = str(row.get(field_name) or "").strip().lower()
            if value not in allowed_values:
                errors.append(f"narrative_segments[{idx}] invalid {field_name}")
    if errors:
        return False, CORE_SCHEMA_INVALID, errors

    expected_ids = [str(row.get("segment_id") or "").strip() for row in audio_segments if str(row.get("segment_id") or "").strip()]
    actual_ids = [str(row.get("segment_id") or "").strip() for row in narrative_segments]
    if expected_ids != actual_ids:
        missing = [seg_id for seg_id in expected_ids if seg_id not in actual_ids]
        extra = [seg_id for seg_id in actual_ids if seg_id not in expected_ids]
        ordering_conflict = not missing and not extra
        mismatch_errors: list[str] = []
        if missing:
            mismatch_errors.append(f"missing_segment_ids:{missing[:8]}")
        if extra:
            mismatch_errors.append(f"extra_segment_ids:{extra[:8]}")
        if ordering_conflict:
            mismatch_errors.append("segment_order_conflict")
        expected_numeric = [re.sub(r"\D+", "", seg_id) for seg_id in expected_ids]
        actual_numeric = [re.sub(r"\D+", "", seg_id) for seg_id in actual_ids]
        if (
            len(expected_ids) == len(actual_ids)
            and expected_numeric == actual_numeric
            and expected_ids != actual_ids
        ):
            mismatch_errors.append("id_mismatch_kind:renamed_segment_ids")
        return False, CORE_ID_MISMATCH, mismatch_errors or ["segment_id_1_to_1_mismatch"]

    quality_retry_causes, quality_gate_diag = _evaluate_story_core_quality_gates(
        narrative_segments,
        content_type=content_type,
        director_mode=director_mode,
        audio_map_quality_context=audio_map_quality_context,
    )
    if debug_capture is not None:
        debug_capture.update(quality_gate_diag)
    if quality_retry_causes:
        return False, CORE_QUALITY_GATES_FAILED, quality_retry_causes

    drift_keys = {"t0", "t1", "scene_slots", "scene_candidate_windows", "phrase_units"}
    json_dump = json.dumps(payload, ensure_ascii=False).lower()
    if any(token in json_dump for token in drift_keys):
        return False, CORE_TIMING_DRIFT, ["core_payload_attempts_timing_or_legacy_grid_control"]

    normalized_zones = _story_core_forbidden_zones_text(payload)

    def _capture_technical_debug(*, match_type: str, pattern: str, zone_name: str, zone_text: str, match_obj: re.Match[str] | None = None) -> None:
        if debug_capture is None:
            return
        if match_obj:
            start = max(0, match_obj.start() - 60)
            end = min(len(zone_text), match_obj.end() + 60)
            excerpt = zone_text[start:end]
        else:
            excerpt = zone_text[:180]
        debug_capture["story_core_technical_spawn_match_type"] = match_type
        debug_capture["story_core_technical_spawn_match_pattern"] = pattern
        debug_capture["story_core_technical_spawn_match_zone"] = zone_name
        debug_capture["story_core_technical_spawn_match_excerpt"] = excerpt[:240]
        debug_capture["story_core_technical_spawn_match_term"] = (
            str(match_obj.group(0) or "").strip() if match_obj else str(pattern or "").strip()
        )[:120]

    technical_match = _find_story_core_forbidden_technical_match(payload)
    if technical_match:
        _capture_technical_debug(
            match_type=str(technical_match.get("match_type") or ""),
            pattern=str(technical_match.get("pattern") or ""),
            zone_name=str(technical_match.get("zone") or ""),
            zone_text=str(technical_match.get("zone_text") or ""),
            match_obj=technical_match.get("match_obj") if isinstance(technical_match.get("match_obj"), re.Match) else None,
        )
        error_key = (
            "core_payload_contains_technical_language_in_forbidden_zone"
            if str(technical_match.get("match_type") or "") == "technical_language_pattern"
            else "core_payload_contains_route_language_in_forbidden_zone"
        )
        return False, CORE_TECHNICAL_SPAWNING, [error_key]

    direct_route_assignment_patterns = (
        r"segment[_\s-]*id[^a-z0-9]{0,8}(seg[_\s-]*\d+)[^\n]{0,40}(->|=>|:|uses?|route)[^\n]{0,24}(i2v|ia2v|first_last)",
        r"scene\s*\d+[^\n]{0,40}(->|=>|:|uses?|route)[^\n]{0,24}(i2v|ia2v|first_last)",
    )
    if any(re.search(pattern, json_dump) for pattern in direct_route_assignment_patterns):
        return False, CORE_TECHNICAL_SPAWNING, ["core_payload_contains_direct_route_assignment"]

    spawning_matches = _detect_core_role_spawning_matches(
        payload,
        present_cast_roles=present_cast_roles or {"character_1", "character_2"},
        role_identity_expectations=role_identity_expectations or {},
    )
    if debug_capture is not None:
        debug_capture["story_core_role_spawning_matches"] = spawning_matches[:12]
    if spawning_matches:
        reasons = [str(row.get("reason") or "role_spawning_detected") for row in spawning_matches[:8]]
        return False, CORE_ROLE_SPAWNING, reasons or ["role_spawning_detected"]

    concept = str(user_concept or "").strip().lower()
    if concept:
        if any(token in concept for token in ("no neon", "не неон", "not neon")) and "neon" in json_dump:
            return False, CORE_IDENTITY_CONFLICT, ["concept_forbids_neon_but_core_introduced_neon"]
        if any(token in concept for token in ("no club", "не клуб", "not club")) and "club" in json_dump:
            return False, CORE_IDENTITY_CONFLICT, ["concept_forbids_club_but_core_introduced_club"]
    contradictions = _detect_core_role_binding_contradictions(
        payload,
        role_identity_expectations or {},
        debug_capture=debug_capture,
    )
    if contradictions:
        return False, CORE_ROLE_BINDING_CONTRADICTION, contradictions

    return True, "", []


def _normalize_story_core_segment_structured_fields(row: dict[str, Any]) -> dict[str, str]:
    normalized = {
        "visual_scale": str(row.get("visual_scale") or "").strip().lower(),
        "visual_density": str(row.get("visual_density") or "").strip().lower(),
        "motion_profile": str(row.get("motion_profile") or "").strip().lower(),
        "hero_world_mode": str(row.get("hero_world_mode") or "").strip().lower(),
        "beat_mode": str(row.get("beat_mode") or "").strip().lower(),
        "subtext_mode": str(row.get("subtext_mode") or "").strip().lower(),
        "association_target": str(row.get("association_target") or "").strip().lower(),
    }
    valid_values = {
        "visual_scale": {"intimate", "medium", "wide"},
        "visual_density": {"sparse", "moderate", "dense"},
        "motion_profile": {"still", "controlled", "dynamic"},
        "hero_world_mode": {"hero_foreground", "world_foreground", "balanced"},
        "beat_mode": {"performance", "world_observation", "world_pressure", "aftermath", "threshold", "social_texture", "release", "transition"},
        "subtext_mode": {"direct", "coded", "second_order", "aftermath_trace", "witness_detail", "symbolic_environment"},
        "association_target": {"level_1", "level_2_plus"},
    }
    text = f"{row.get('beat_purpose') or ''} {row.get('emotional_key') or ''}".lower()
    fallback = {
        "visual_scale": _infer_frame_axes(row)["scale"],
        "visual_density": _infer_frame_axes(row)["density"],
        "motion_profile": _infer_frame_axes(row)["motion"],
        "hero_world_mode": (
            "hero_foreground"
            if _infer_story_core_dominance(row) == "hero_first"
            else ("world_foreground" if _infer_story_core_dominance(row) == "world_first" else "balanced")
        ),
        "beat_mode": (
            "performance"
            if "performance" in text or "vocal" in text
            else ("world_pressure" if any(token in text for token in ("pressure", "threat", "constraint")) else "world_observation")
        ),
        "subtext_mode": "coded" if any(token in text for token in ("coded", "indirect", "implied")) else "direct",
        "association_target": "level_2_plus" if any(token in text for token in ("subtext", "symbol", "second-order", "witness", "aftermath")) else "level_1",
    }
    return {
        field: (normalized[field] if normalized[field] in valid_values[field] else fallback[field])
        for field in valid_values
    }


def _normalize_story_core_contract_payload(
    *,
    parsed: dict[str, Any],
    audio_segments: list[dict[str, Any]],
    creative_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_creative_config = _normalize_creative_config(creative_config)
    configured_route_mix_doctrine, _ = _build_route_mix_doctrine_for_scenes(normalized_creative_config)
    fallback_guidance = _default_story_core_guidance(normalized_creative_config)
    raw_guidance = _safe_dict(parsed.get("story_guidance"))
    route_mix_row = (
        _safe_dict(raw_guidance.get("route_mix_doctrine_for_scenes"))
        or _safe_dict(parsed.get("route_mix_doctrine_for_scenes"))
        or _safe_dict(fallback_guidance.get("route_mix_doctrine_for_scenes"))
    )
    route_mix_doctrine = {
        **configured_route_mix_doctrine,
        "core_scope_only": str(route_mix_row.get("core_scope_only") or configured_route_mix_doctrine.get("core_scope_only") or "").strip(),
        "lipsync_candidate_is_permission_not_obligation": bool(
            route_mix_row.get("lipsync_candidate_is_permission_not_obligation")
            if "lipsync_candidate_is_permission_not_obligation" in route_mix_row
            else configured_route_mix_doctrine.get("lipsync_candidate_is_permission_not_obligation")
        ),
        "avoid_long_consecutive_lipsync_streaks": bool(
            route_mix_row.get("avoid_long_consecutive_lipsync_streaks")
            if "avoid_long_consecutive_lipsync_streaks" in route_mix_row
            else configured_route_mix_doctrine.get("avoid_long_consecutive_lipsync_streaks")
        ),
        "prioritize_lipsync_for_strong_performance_windows": bool(
            route_mix_row.get("prioritize_lipsync_for_strong_performance_windows")
            if "prioritize_lipsync_for_strong_performance_windows" in route_mix_row
            else configured_route_mix_doctrine.get("prioritize_lipsync_for_strong_performance_windows")
        ),
    }
    parsed_segments = [row for row in _safe_list(parsed.get("narrative_segments")) if isinstance(row, dict)]
    parsed_by_segment_id = {
        str(_safe_dict(row).get("segment_id") or "").strip(): _safe_dict(row)
        for row in parsed_segments
        if str(_safe_dict(row).get("segment_id") or "").strip()
    }
    unresolved_rows = [row for row in parsed_segments if not str(_safe_dict(row).get("segment_id") or "").strip()]
    normalized_segments: list[dict[str, Any]] = []
    fallback_arc_roles = ("setup", "build", "pivot", "climax", "release", "afterglow")
    for idx, segment in enumerate(audio_segments):
        canonical_segment_id = str(_safe_dict(segment).get("segment_id") or "").strip()
        source_row = _safe_dict(parsed_by_segment_id.get(canonical_segment_id))
        if not source_row and idx < len(parsed_segments):
            source_row = _safe_dict(parsed_segments[idx])
        if not source_row and unresolved_rows:
            source_row = _safe_dict(unresolved_rows.pop(0))
        arc_fallback = fallback_arc_roles[min(idx, len(fallback_arc_roles) - 1)]
        phrase_hint = str(_safe_dict(segment).get("transcript_slice") or "").strip()
        normalized_row = {
            "segment_id": canonical_segment_id,
            "arc_role": str(source_row.get("arc_role") or arc_fallback).strip(),
            "beat_purpose": str(source_row.get("beat_purpose") or phrase_hint or f"progress_{idx + 1}").strip(),
            "emotional_key": str(source_row.get("emotional_key") or "musical progression").strip(),
        }
        normalized_row.update(_normalize_story_core_segment_structured_fields(source_row or normalized_row))
        normalized_segments.append(normalized_row)
    return {
        "core_version": "1.1",
        "story_summary": str(parsed.get("story_summary") or "").strip(),
        "opening_anchor": str(parsed.get("opening_anchor") or "").strip(),
        "ending_callback_rule": str(parsed.get("ending_callback_rule") or "").strip(),
        "global_arc": {
            "exposition": str(_safe_dict(parsed.get("global_arc")).get("exposition") or "").strip(),
            "climax": str(_safe_dict(parsed.get("global_arc")).get("climax") or "").strip(),
            "resolution": str(_safe_dict(parsed.get("global_arc")).get("resolution") or "").strip(),
        },
        "identity_doctrine": {
            "hero_anchor": _ref_safe_identity_text(
                str(_safe_dict(parsed.get("identity_doctrine")).get("hero_anchor") or "").strip(),
                "character_1",
            ),
            "world_doctrine": str(_safe_dict(parsed.get("identity_doctrine")).get("world_doctrine") or "").strip(),
            "style_doctrine": str(_safe_dict(parsed.get("identity_doctrine")).get("style_doctrine") or "").strip(),
        },
        "route_mix_doctrine_for_scenes": route_mix_doctrine,
        "narrative_segments": normalized_segments,
        "audio_segment_count": len(audio_segments),
    }


def _build_story_core_prompt(
    core_input_context: dict[str, Any],
    assigned_roles: dict[str, Any],
    story_core_mode: str,
    capability_bounds_text: str,
) -> str:
    compact_input = _compact_prompt_payload(core_input_context)
    compact_assigned_roles = _compact_prompt_payload(assigned_roles)
    world_constraint = str(
        _safe_dict(core_input_context.get("world_style_identity_constraints")).get("director_world_lock_summary")
        or ""
    ).strip()
    mode = "directed" if story_core_mode == "directed" else "creative"
    mode_instructions = (
        "MODE: DIRECTED\n"
        "- Preserve user concept as narrative meaning source.\n"
        "- Audio segments still define temporal rhythm and escalation pattern.\n"
    )
    if mode == "creative":
        mode_instructions = (
            "MODE: CREATIVE\n"
            "- Build compact meaning from audio segment progression, refs, and concept.\n"
            "- Keep one coherent world family and identity continuity.\n"
        )
    return (
        "You are STORY CORE v1.1 stage.\n"
        "Return STRICT JSON only. No markdown.\n"
        "AUDIO canonical source for CORE is audio_map.segments[] only.\n"
        "HARD CONTRACT: segment_id from AUDIO is immutable.\n"
        "Use the provided segment_id values exactly as given (verbatim copy from AUDIO).\n"
        "Do not rename, normalize, shorten, re-index, regenerate, or synthesize segment IDs.\n"
        "Do not convert to zero-based IDs or alter zero padding (seg_01 must remain seg_01; seg_0 is invalid).\n"
        "narrative_segments[] must preserve exact segment_id values and exact order from audio_map.segments[].\n"
        "Any new/generated/reformatted segment_id is invalid.\n"
        "HARD CONTRACT: Role IDs are immutable.\n"
        "Never infer, rename, or swap character_1/character_2/character_3 from wording semantics.\n"
        "Use connected refs, assigned roles, and user-provided role mapping as source of truth for every field.\n"
        "If mapping says character_1 is female and character_2 is male, preserve this exactly in story_summary, identity_doctrine, narrative_segments, and any role mentions.\n"
        "Never output contradictions like 'The guy (character_1)' when role mapping marks character_1 as female.\n"
        "CORE scope = doctrine + narrative meaning per segment.\n"
        "Forbidden at CORE: roles planning, scene rows, choreography, camera, motion, framing, route assignment, prompt writing, renderer/delivery fields.\n"
        "Do not output t0/t1 or timing edits; never merge/delete/reorder segments.\n"
        "You may include global route_mix_doctrine_for_scenes policy only (ratios/anti-streak/performance windows), never segment_id->route assignments.\n"
        "Use creative_config only as doctrine input.\n"
        "ANTI-LITERAL WORLD CONTRACT (hard): avoid first-order cliché shorthand. Do not map danger/crime/street myth to automatic stock noir bundles (e.g., repeated dark alleys/courtyards/ports/underworld phrasing).\n"
        "Build world via second-order social logic: rituals, coded behavior, distance, threshold spaces, witness perspective, ordinary life under pressure, and aftermath traces.\n"
        "World must feel specific through lived texture and human environment, not through repeated stereotype tags.\n"
        "SEMANTIC COMPRESSION RULE (hard): when user concept contains location/object lists, compress them into coherent world doctrine and progression logic; never output checklist-style 'show X, Y, Z'.\n"
        "Keep grounded anchors (city, hero, world identity, musical character), but rewrite literal directives into dramaturgical function.\n"
        "For narrative_segments, write beat purpose as scene function + relation between hero and world + progression pressure, not as direct paraphrase of lyrics or user note.\n"
        "ATMOSPHERE RULE: criminal/tense/street atmosphere means social pressure and risk logic in the world; it is NOT a request for costume shorthand.\n"
        "IDENTITY INVENTION GUARD (hard): if character refs exist, do not invent exact wardrobe/accessories/headwear/jewelry/tattoos/facial-hair packages unless explicitly grounded in refs or user text.\n"
        "Allowed identity language at CORE: narrative presence, social role energy, emotional mode, and scene function.\n"
        "PERFORMANCE vs WORLD SPLIT (hard): performance/vocal segments may foreground hero; world/cutaway segments must not re-center hero by habit and should prioritize reactive city/human environment beats.\n"
        "WORLD-BEAT DIVERSITY (hard): world/cutaway segments must diversify by dramaturgic function, not only by location noun; use observation, social_texture, pressure, threshold, witness/aftermath/release functions as needed.\n"
        "PERFORMANCE DIVERSITY (hard): performance segments cannot differ only by emotion wording; vary structured visual profile and hero/world balance across performance beats.\n"
        "TWO-AXIS ADJACENCY CONTRACT (hard): each adjacent segment pair must change at least one frame axis (scale/intimacy OR density OR motion character) and at least one meaning axis (narrative function OR social pressure OR hero_vs_world_ratio).\n"
        "Adjacent beats must not repeat the same frame logic plus the same narrative function.\n"
        "TWO OUTPUT LAYERS (hard):\n"
        "LAYER A = STRUCTURED MACHINE FIELDS ONLY IN JSON (visual_scale, visual_density, motion_profile, hero_world_mode, beat_mode, subtext_mode, association_target).\n"
        "LAYER B = HUMAN NARRATIVE TEXT (story_summary, opening_anchor, ending_callback_rule, global_arc prose, identity_doctrine prose, beat_purpose, emotional_key, optional narrative descriptions).\n"
        "Narrative text must read like natural creative director notes, not schema labels, validator logic, metadata, system instructions, or analysis output.\n"
        "Write narrative meaning, not field explanations. Describe what the beat feels/does in story terms, not which structured slot it belongs to.\n"
        "Never explain the plan using metadata language.\n"
        "FORBIDDEN VOCABULARY IN LAYER B (hard): do not mention delivery, delivery mode, structured, visual profile, frame axis, meaning axis, motion_profile, hero_world_mode, beat_mode, association_target, level_2_plus, second_order as label, validator, schema, contract, prompt, retry, or any field-like/enum-like wording.\n"
        "Human words are allowed when natural (e.g., dense, quiet, wide, movement), but never as technical labels.\n"
        "STRUCTURED FRAME CONTRACT (hard): every narrative segment must include visual_scale(intimate|medium|wide), visual_density(sparse|moderate|dense), motion_profile(still|controlled|dynamic).\n"
        "STRUCTURED MEANING CONTRACT (hard): every narrative segment must include hero_world_mode(hero_foreground|world_foreground|balanced) and beat_mode(performance|world_observation|world_pressure|aftermath|threshold|social_texture|release|transition).\n"
        "SUBTEXT CONTRACT (hard): every narrative segment must include subtext_mode(direct|coded|second_order|aftermath_trace|witness_detail|symbolic_environment) and association_target(level_1|level_2_plus).\n"
        "At least two segments (preferably non-performance world beats) must be second-order/subtext-aware with association_target=level_2_plus.\n"
        "AUDIO_MAP DRAMATURGY CONTRACT (hard): use audio_map not only for boundaries but for contrast opportunities, stillness/hold beats, semantic turns, release windows, and finality logic.\n"
        "Do not auto-convert assertive/high windows into repeated generic threat tableaux; release/stillness windows should permit breath/aftermath/sparse echo.\n"
        "NARRATIVE-ONLY SANITIZE SELF-CHECK BEFORE FINAL JSON (hard): scan all Layer B text fields, detect leaked technical vocabulary, and rewrite leaked fragments into natural story language while keeping Layer A structured values unchanged.\n"
        "If you cannot rewrite a leaked technical fragment cleanly, remove only the technical fragment and keep the beat meaning coherent.\n"
        "INTERNAL SELF-CHECK BEFORE FINAL JSON: for every adjacent pair verify frame-axis change, meaning-axis change, anti-cliché depth, no unsupported identity invention, clear beat type (performance/world/observation/pressure/release/aftermath), and no stereotype vocabulary loops. Rewrite failing pairs before output.\n"
        "Output must include narrative_segments[] with EXACT 1:1 mapping to provided segment_id list and same order.\n"
        "Each narrative_segments item requires: segment_id, arc_role(setup|build|pivot|climax|release|afterglow), beat_purpose, emotional_key, visual_scale, visual_density, motion_profile, hero_world_mode, beat_mode, subtext_mode, association_target.\n"
        "Top-level required fields: core_version, story_summary, opening_anchor, ending_callback_rule, global_arc, identity_doctrine, narrative_segments.\n"
        "global_arc fields: exposition, climax, resolution.\n"
        "identity_doctrine fields: hero_anchor, world_doctrine, style_doctrine.\n"
        "Keep doctrine concise, production-usable, and consistent with refs/user concept.\n"
        f"World constraint: {world_constraint or 'keep one coherent world family from user/director constraints'}\n"
        f"Video model capability bounds (must obey): {capability_bounds_text}\n"
        "HARD CONTRACT: respect explicit user world locks (e.g., no club/no neon) and avoid contradictions.\n"
        f"{mode_instructions}\n"
        f"story_core_mode={mode}\n\n"
        f"CORE_INPUT_CONTEXT:\n{json.dumps(compact_input, ensure_ascii=False)[:4200]}\n\n"
        f"ASSIGNED_ROLES:\n{json.dumps(compact_assigned_roles, ensure_ascii=False)[:1200]}\n\n"
    )


def _is_usable_story_core(story_core: dict[str, Any]) -> bool:
    if not isinstance(story_core, dict):
        return False
    return all(
        bool(str(story_core.get(key) or "").strip())
        for key in ("story_summary", "opening_anchor", "ending_callback_rule")
    )


def create_storyboard_package(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    req = _safe_dict(payload)
    metadata = _safe_dict(req.get("metadata"))
    director_controls = _safe_dict(req.get("director_controls"))
    if not director_controls:
        director_controls = _safe_dict(req.get("directorControls"))
    refs_inventory, ownership_binding_inventory = _normalize_refs_inventory(_safe_dict(req.get("context_refs")))
    source = _safe_dict(req.get("source"))
    source_mode = str(source.get("source_mode") or source.get("sourceMode") or "").strip().lower()
    source_text = str(source.get("source_value") or source.get("sourceValue") or "").strip() if source_mode == "text" else ""
    text_in = _safe_dict(refs_inventory.get("text_in"))
    connected_text = str(text_in.get("value") or "").strip()
    local_text = str(req.get("directorNote") or req.get("director_note") or req.get("note") or "").strip()
    primary_narrative_text = connected_text or local_text or source_text
    base_input = {
        "text": str(req.get("text") or "").strip() or primary_narrative_text,
        "story_text": str(req.get("storyText") or req.get("story_text") or "").strip() or primary_narrative_text,
        "note": str(req.get("note") or "").strip() or primary_narrative_text,
        "director_note": str(req.get("directorNote") or req.get("director_note") or "").strip() or primary_narrative_text,
        "source": _safe_dict(req.get("source")),
        "audio_url": str(req.get("audioUrl") or "").strip(),
        "audio_duration_sec": float(req.get("audioDurationSec") or 0.0),
        "transcript_text": str(req.get("transcriptText") or req.get("transcript") or "").strip(),
        "lyrics_text": str(req.get("lyricsText") or req.get("lyrics") or "").strip(),
        "spoken_text_hint": str(req.get("spokenTextHint") or "").strip(),
        "content_type": str(director_controls.get("contentType") or "music_video"),
        "director_mode": _resolve_director_mode(
            req.get("director_mode") or metadata.get("director_mode"),
            content_type=str(director_controls.get("contentType") or "music_video"),
        ),
        "format": str(director_controls.get("format") or req.get("format") or "9:16"),
        "creative_config": _normalize_creative_config(_extract_request_creative_config(req, metadata)),
        "connected_context_summary": _safe_dict(req.get("connected_context_summary")),
        "refs_by_role": _safe_dict(req.get("refsByRole")),
        "selected_refs": {
            "character_1": str(req.get("selectedCharacterRefUrl") or "").strip(),
            "style": str(req.get("selectedStyleRefUrl") or "").strip(),
            "location": str(req.get("selectedLocationRefUrl") or "").strip(),
            "props": [str(item).strip() for item in _safe_list(req.get("selectedPropsRefUrls")) if str(item).strip()],
        },
        "ownership_binding_inventory": ownership_binding_inventory,
    }
    normalized_input = _normalize_input_audio_source(base_input, refs_inventory)
    stages = {
        stage_id: {
            "status": "idle",
            "updated_at": "",
            "error": "",
            "run_count": 0,
        }
        for stage_id in STAGE_IDS
    }
    return {
        "package_version": "scenario_stage_pipeline_v1",
        "pipeline_mode": str(metadata.get("pipelineMode") or "scenario_stage_v1"),
        "created_at": _utc_iso(),
        "updated_at": _utc_iso(),
        "input": normalized_input,
        "refs_inventory": refs_inventory,
        "assigned_roles": _safe_dict(req.get("roleTypeByRole")),
        "story_core": {},
        "audio_map": {},
        "role_plan": {},
        "scene_plan": {"scenes": []},
        "scene_prompts": {"scenes": []},
        "final_video_prompt": {"delivery_version": FINAL_VIDEO_PROMPT_DELIVERY_VERSION, "segments": [], "scenes": []},
        "final_storyboard": {"final_storyboard_version": "1.1", "render_manifest": [], "scenes": []},
        "diagnostics": {
            "warnings": [],
            "events": [],
            "errors": [],
            "stale_reason": "",
            "last_action": "",
            "story_core_mode": "creative",
            "story_core_used_fallback": False,
            "story_core_character_ref_attached": False,
            "story_core_character_ref_source": "",
            "story_core_character_ref_error": "",
            "story_core_prop_contracts": [],
            "story_core_prop_confusion_guard_applied": False,
            "story_core_ref_attachment_summary": {},
            "story_core_grounding_level": "standard",
            "input_creative_config_active": _safe_dict(normalized_input.get("creative_config")),
        },
        "stage_statuses": stages,
        "contracts": {
            "entities": {
                "character": {"views": []},
                "location": {"views": []},
                "prop": {"views": []},
            },
            "scene": {
                "required": ["camera_angle", "shot_size", "subject_facing"],
            },
        },
    }
    _inject_route_strategy_diagnostics(package)
    return package


def pick_best_view_for_scene(entity: dict[str, Any] | None, scene_context: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate = _safe_dict(entity)
    views = _safe_list(candidate.get("views"))
    if not views:
        return {}
    # Hook for future ranking by scene_context.
    return views[0] if isinstance(views[0], dict) else {"id": str(views[0])}


def mark_stale_downstream(package: dict[str, Any], from_stage_id: str, reason: str = "") -> dict[str, Any]:
    pkg = deepcopy(_safe_dict(package))
    reason_norm = str(reason or "").strip().lower()
    queue = list(DOWNSTREAM_BY_STAGE.get(from_stage_id, []))
    visited: set[str] = set()
    statuses = _safe_dict(pkg.get("stage_statuses"))
    if reason_norm == "route_strategy_changed" and from_stage_id in STAGE_IDS:
        origin_state = _safe_dict(statuses.get(from_stage_id))
        origin_state["status"] = "stale"
        origin_state["updated_at"] = _utc_iso()
        statuses[from_stage_id] = origin_state
    while queue:
        stage_id = queue.pop(0)
        if stage_id in visited:
            continue
        visited.add(stage_id)
        stage_state = _safe_dict(statuses.get(stage_id))
        stage_state["status"] = "stale"
        stage_state["updated_at"] = _utc_iso()
        statuses[stage_id] = stage_state
        queue.extend(DOWNSTREAM_BY_STAGE.get(stage_id, []))
    pkg["stage_statuses"] = statuses
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    diagnostics["stale_reason"] = str(reason or "manual_input_changed")
    pkg["diagnostics"] = diagnostics
    _inject_route_strategy_diagnostics(pkg)
    pkg["updated_at"] = _utc_iso()
    return pkg


def _clear_stage_diagnostics(diagnostics: dict[str, Any], stage_id: str) -> dict[str, Any]:
    prefixes = STAGE_DIAGNOSTIC_PREFIXES.get(stage_id, ())
    if not prefixes:
        return diagnostics
    next_diagnostics = dict(diagnostics)
    for key in list(next_diagnostics.keys()):
        if any(str(key).startswith(prefix) for prefix in prefixes):
            next_diagnostics.pop(key, None)
    return next_diagnostics


def invalidate_downstream_stages(package: dict[str, Any], from_stage_id: str, reason: str = "") -> dict[str, Any]:
    return _clear_downstream_stage_outputs(package, from_stage_id, reason or f"rerun:{from_stage_id}")


def _set_stage_status(package: dict[str, Any], stage_id: str, status: str, *, error: str = "") -> None:
    statuses = _safe_dict(package.get("stage_statuses"))
    stage_state = _safe_dict(statuses.get(stage_id))
    stage_state["status"] = status
    stage_state["updated_at"] = _utc_iso()
    stage_state["error"] = str(error or "")
    if status in {"running", "done"}:
        for key in (
            "invalidated",
            "invalid",
            "dirty",
            "stale",
            "staleReason",
            "stale_reason",
            "reason",
            "statusReason",
            "invalidateReason",
            "invalidatedReason",
        ):
            stage_state.pop(key, None)
    if status in {"done", "error"}:
        stage_state["run_count"] = int(stage_state.get("run_count") or 0) + 1
    statuses[stage_id] = stage_state
    package["stage_statuses"] = statuses
    if status == "done":
        diagnostics = _safe_dict(package.get("diagnostics"))
        if str(diagnostics.get("stale_reason") or "").strip():
            diagnostics["stale_reason"] = ""
        package["diagnostics"] = diagnostics


def _append_diag_event(package: dict[str, Any], message: str, *, stage_id: str = "") -> None:
    diagnostics = _safe_dict(package.get("diagnostics"))
    events = _safe_list(diagnostics.get("events"))
    events.append({"at": _utc_iso(), "stage_id": stage_id, "message": message})
    diagnostics["events"] = events[-80:]
    package["diagnostics"] = diagnostics


def _estimate_json_size_bytes(value: Any) -> int:
    try:
        payload = json.dumps(value, ensure_ascii=False, default=str)
        return len(payload.encode("utf-8"))
    except Exception:
        return 0


def build_runtime_diagnostics_summary(
    package: dict[str, Any],
    *,
    current_stage: str = "",
    include_debug: bool = False,
) -> dict[str, Any]:
    pkg = _safe_dict(package)
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    warnings = _safe_list(diagnostics.get("warnings"))
    validation_error = _first_text(
        diagnostics.get(f"{current_stage}_validation_error") if current_stage else "",
        diagnostics.get("validation_error"),
    )
    used_model = _first_text(
        diagnostics.get(f"{current_stage}_used_model") if current_stage else "",
        diagnostics.get("scene_prompts_used_model"),
        diagnostics.get("scene_plan_used_model"),
        diagnostics.get("role_plan_used_model"),
        diagnostics.get("story_core_used_model"),
    )
    summary: dict[str, Any] = {
        "current_stage": str(current_stage or ""),
        "used_model": used_model,
        "warnings_count": len(warnings),
        "validation_error": str(validation_error or ""),
    }
    if include_debug:
        section_sizes = {
            "storyboardPackage": _estimate_json_size_bytes(pkg),
            "final_storyboard": _estimate_json_size_bytes(_safe_dict(pkg.get("final_storyboard"))),
            "story_core": _estimate_json_size_bytes(_safe_dict(pkg.get("story_core"))),
            "diagnostics": _estimate_json_size_bytes(diagnostics),
            "diagnostics.events": _estimate_json_size_bytes(_safe_list(diagnostics.get("events"))),
        }
        summary["size_counters"] = section_sizes
        summary["downstream_modes"] = {
            "role_plan_mode": str(_safe_dict(pkg.get("role_plan")).get("director_mode") or ""),
            "scene_plan_mode": str(_safe_dict(pkg.get("scene_plan")).get("director_mode") or ""),
            "scene_prompts_mode": str(_safe_dict(pkg.get("scene_prompts")).get("director_mode") or ""),
            "final_storyboard_mode": str(_safe_dict(pkg.get("final_storyboard")).get("director_mode") or ""),
        }
        summary["events"] = _safe_list(diagnostics.get("events"))
        summary["errors"] = _safe_list(diagnostics.get("errors"))
    return summary


def build_stage_payload_health_summary(package: dict[str, Any]) -> dict[str, Any]:
    pkg = _safe_dict(package)
    story_core = _safe_dict(pkg.get("story_core"))
    role_plan = _safe_dict(pkg.get("role_plan"))
    scene_plan = _safe_dict(pkg.get("scene_plan"))
    scene_prompts = _safe_dict(pkg.get("scene_prompts"))
    return {
        "has_story_core": _has_valid_story_core_payload(story_core),
        "has_role_plan": _has_valid_role_plan_payload(role_plan),
        "has_scene_plan": _has_valid_scene_plan_payload(scene_plan),
        "has_scene_prompts": _has_valid_scene_prompts_payload(scene_prompts),
        "story_core_segment_count": len(_safe_list(story_core.get("narrative_segments"))),
        "role_plan_roster_count": len(_safe_list(role_plan.get("roster"))),
        "scene_plan_segment_count": len(_safe_list(scene_plan.get("segments"))) or len(_safe_list(scene_plan.get("scenes"))),
        "scene_prompts_segment_count": len(_safe_list(scene_prompts.get("segments"))) or len(_safe_list(scene_prompts.get("scenes"))),
        "scene_prompts_prompts_version": str(scene_prompts.get("prompts_version") or "").strip(),
    }


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        if hasattr(value, "item") and callable(getattr(value, "item")):
            value = value.item()
        elif isinstance(value, (list, tuple)) and value:
            value = value[0]
        elif hasattr(value, "__len__") and hasattr(value, "__getitem__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]
        parsed = float(value)
        if math.isfinite(parsed):
            return round(parsed, 3)
    except Exception:
        pass
    return fallback


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _resolve_downstream_mode_metadata(package: dict[str, Any]) -> dict[str, str]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    story_core_v1 = _safe_dict(story_core.get("story_core_v1"))
    content_type = str(input_pkg.get("content_type") or "")
    director_mode = _resolve_director_mode(input_pkg.get("director_mode"), content_type=content_type)
    story_truth_source = str(story_core_v1.get("story_truth_source") or "").strip()
    audio_truth_scope = str(story_core_v1.get("audio_truth_scope") or "").strip()
    if not story_truth_source:
        story_truth_source = "note_refs_primary" if director_mode == "clip" else "mixed_inputs"
    if not audio_truth_scope:
        audio_truth_scope = "timing_plus_emotion" if director_mode == "clip" else "timing_structure"
    return {
        "director_mode": director_mode,
        "story_truth_source": story_truth_source,
        "audio_truth_scope": audio_truth_scope,
    }


def _attach_downstream_mode_metadata(stage_payload: Any, package: dict[str, Any]) -> dict[str, Any]:
    payload = _safe_dict(stage_payload)
    metadata = _resolve_downstream_mode_metadata(package)
    payload["director_mode"] = metadata["director_mode"]
    payload["story_truth_source"] = metadata["story_truth_source"]
    payload["audio_truth_scope"] = metadata["audio_truth_scope"]
    return payload


def _scene_prompts_upstream_signature(package: dict[str, Any]) -> str:
    snapshot = {
        "story_core": _safe_dict(package.get("story_core")),
        "audio_map": _safe_dict(package.get("audio_map")),
        "role_plan": _safe_dict(package.get("role_plan")),
        "scene_plan": _safe_dict(package.get("scene_plan")),
        "refs_inventory": _safe_dict(package.get("refs_inventory")),
    }
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _final_video_prompt_scene_prompts_signature(package: dict[str, Any]) -> str:
    payload = json.dumps(_safe_dict(package.get("scene_prompts")), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _final_video_prompt_character_1_context(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    connected = _safe_dict(input_pkg.get("connected_context_summary")) or _safe_dict(package.get("connected_context_summary"))
    role_map = _safe_dict(connected.get("role_identity_mapping"))
    char1 = _safe_dict(role_map.get("character_1"))
    refs_present = _safe_list(_safe_dict(connected.get("refsPresentByRole")).get("character_1"))
    connected_refs = _safe_list(_safe_dict(connected.get("connectedRefsPresentByRole")).get("character_1"))
    ref_character_1_inventory = _safe_dict(_safe_dict(package.get("refs_inventory")).get("ref_character_1"))
    character_views = _normalize_character_views(ref_character_1_inventory, "character_1")
    inventory_refs = _safe_list(ref_character_1_inventory.get("refs"))
    inventory_value = str(ref_character_1_inventory.get("value") or "").strip()
    all_refs = [
        str(v).strip()
        for v in [*refs_present, *connected_refs, *inventory_refs, inventory_value]
        if str(v).strip()
    ]
    all_refs = list(dict.fromkeys(all_refs))
    ref_signature = hashlib.sha256("|".join(sorted(all_refs)).encode("utf-8")).hexdigest() if all_refs else ""
    return {
        "gender_hint": str(char1.get("gender_hint") or "").strip().lower(),
        "identity_label": str(char1.get("identity_label") or "").strip(),
        "ref_signature": ref_signature,
        "connected_refs": sorted(all_refs),
        "character_views": character_views,
        "character_view_types": [key for key in _CHARACTER_VIEW_ORDER if key in character_views],
    }


def _final_video_prompt_route_map_signature(package: dict[str, Any]) -> tuple[str, list[str]]:
    scene_prompts = _safe_dict(package.get("scene_prompts"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    rows = _safe_list(scene_prompts.get("segments")) or _safe_list(scene_prompts.get("scenes"))
    if not rows:
        rows = _safe_list(scene_plan.get("segments")) or _safe_list(scene_plan.get("scenes"))
    route_pairs: list[str] = []
    segment_ids: list[str] = []
    for row in rows:
        item = _safe_dict(row)
        segment_id = str(item.get("segment_id") or item.get("scene_id") or "").strip()
        if not segment_id:
            continue
        route = str(item.get("route") or "").strip().lower()
        if route in {"f_l", "first-last"}:
            route = "first_last"
        if route in {"lip_sync", "lip_sync_music"}:
            route = "ia2v"
        if route not in {"i2v", "ia2v", "first_last"}:
            route = "i2v"
        segment_ids.append(segment_id)
        route_pairs.append(f"{segment_id}:{route}")
    return hashlib.sha1("|".join(route_pairs).encode("utf-8")).hexdigest(), segment_ids


def _final_video_prompt_upstream_signature(package: dict[str, Any]) -> str:
    snapshot = {
        "input": _safe_dict(package.get("input")),
        "audio_map": _safe_dict(package.get("audio_map")),
        "story_core": _safe_dict(package.get("story_core")),
        "role_plan": _safe_dict(package.get("role_plan")),
        "scene_plan": _safe_dict(package.get("scene_plan")),
        "scene_prompts": _safe_dict(package.get("scene_prompts")),
    }
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _collect_final_video_prompt_snapshot_meta(package: dict[str, Any]) -> dict[str, Any]:
    character_ctx = _final_video_prompt_character_1_context(package)
    route_sig, segment_ids = _final_video_prompt_route_map_signature(package)
    return {
        "upstream_signature": _final_video_prompt_upstream_signature(package),
        "scene_prompts_signature": _final_video_prompt_scene_prompts_signature(package),
        "route_map_signature": route_sig,
        "segment_ids": segment_ids,
        "character_1_gender_hint": str(character_ctx.get("gender_hint") or ""),
        "character_1_identity_label": str(character_ctx.get("identity_label") or ""),
        "character_1_ref_signature": str(character_ctx.get("ref_signature") or ""),
        "character_1_connected_refs": _safe_list(character_ctx.get("connected_refs")),
    }


def _snapshot_has_gender_identity_conflict(snapshot_payload: dict[str, Any], *, gender_hint: str) -> tuple[bool, list[str]]:
    if gender_hint == "male":
        terms = _FEMALE_CODED_TERMS
    elif gender_hint == "female":
        terms = _MALE_CODED_TERMS
    else:
        return False, []
    found_terms: list[str] = []
    for seg_raw in _safe_list(snapshot_payload.get("segments")):
        seg = _safe_dict(seg_raw)
        route_payload = _safe_dict(seg.get("route_payload"))
        fields = [
            str(route_payload.get("positive_prompt") or ""),
            str(route_payload.get("negative_prompt") or ""),
            str(route_payload.get("first_frame_prompt") or ""),
            str(route_payload.get("last_frame_prompt") or ""),
            str(route_payload.get("image_prompt") or ""),
            str(route_payload.get("video_prompt") or ""),
            str(seg.get("starts_from_previous_logic") or ""),
            str(seg.get("ends_with_state") or ""),
            str(seg.get("continuity_with_next") or ""),
            str(seg.get("image_prompt") or ""),
            str(seg.get("video_prompt") or ""),
        ]
        blob = " ".join(fields).lower()
        for term in terms:
            if re.search(rf"(?i)\b{re.escape(term)}\b", blob):
                found_terms.append(term)
    dedup_terms = list(dict.fromkeys(found_terms))
    return bool(dedup_terms), dedup_terms


def _normalize_route_contract(route_value: Any) -> dict[str, Any]:
    raw = str(route_value or "").strip().lower()
    if raw in {"f_l", "first-last"}:
        raw = "first_last"
    if raw in {"lip_sync", "lip_sync_music"}:
        raw = "ia2v"
    if raw not in {"i2v", "ia2v", "first_last"}:
        raw = "i2v"

    if raw == "ia2v":
        return {
            "route": "ia2v",
            "ltx_mode": "lip_sync_music",
            "render_mode": "lip_sync_music",
            "transition_type": "audio_performance",
            "lip_sync": True,
            "needs_two_frames": False,
        }
    if raw == "first_last":
        return {
            "route": "first_last",
            "ltx_mode": "f_l",
            "render_mode": "first_last",
            "transition_type": "micro_transition",
            "lip_sync": False,
            "needs_two_frames": True,
            "continuity_priority": "high",
            "environment_lock_required": True,
            "identity_lock_required": True,
            "wardrobe_lock_required": True,
            "two_frame_micro_transition": True,
        }
    return {
        "route": "i2v",
        "ltx_mode": "i2v",
        "render_mode": "image_to_video",
        "transition_type": "continuous",
        "lip_sync": False,
        "needs_two_frames": False,
    }


def _run_finalize_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    role_plan = _safe_dict(package.get("role_plan"))
    scene_plan = _safe_dict(package.get("scene_plan"))
    scene_prompts = _safe_dict(package.get("scene_prompts"))
    final_video_prompt = _safe_dict(package.get("final_video_prompt"))

    plan_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(scene_plan.get("segments"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    if not plan_by_id:
        plan_by_id = {
            str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
            for row in _safe_list(scene_plan.get("scenes"))
            if str(_safe_dict(row).get("scene_id") or "").strip()
        }

    final_segments = [
        _safe_dict(row)
        for row in _safe_list(final_video_prompt.get("segments"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    ]
    beat_rows = [_safe_dict(row) for row in _safe_list(_safe_dict(story_core.get("beat_map")).get("beats"))]
    beat_timing_by_segment: dict[str, tuple[float, float]] = {}
    for beat in beat_rows:
        slot_ids = _safe_list(beat.get("slot_ids"))
        slot_id = str(slot_ids[0] or "").strip() if slot_ids else ""
        if not slot_id:
            continue
        time_range = _safe_dict(beat.get("time_range"))
        t0_raw = time_range.get("t0")
        t1_raw = time_range.get("t1")
        try:
            beat_t0 = float(t0_raw)
            beat_t1 = float(t1_raw)
        except Exception:
            continue
        if not (math.isfinite(beat_t0) and math.isfinite(beat_t1)):
            continue
        if beat_t1 < beat_t0:
            beat_t1 = beat_t0
        beat_timing_by_segment.setdefault(slot_id, (beat_t0, beat_t1))
    prompts_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(scene_prompts.get("segments"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    if not prompts_by_id:
        prompts_by_id = {
            str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
            for row in _safe_list(scene_prompts.get("scenes"))
            if str(_safe_dict(row).get("scene_id") or "").strip()
        }
    role_casting_by_id = {
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
        for row in _safe_list(role_plan.get("scene_casting"))
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    }
    if not role_casting_by_id:
        role_casting_by_id = {
            str(_safe_dict(row).get("scene_id") or "").strip(): _safe_dict(row)
            for row in _safe_list(role_plan.get("scene_roles"))
            if str(_safe_dict(row).get("scene_id") or "").strip()
        }

    render_manifest: list[dict[str, Any]] = []
    compat_scenes: list[dict[str, Any]] = []
    project_audio_url = str(input_pkg.get("audio_url") or "").strip()
    final_refs_roles = ("character_1", "character_2", "character_3", "location", "style", "props")

    def _collect_final_refs_by_role() -> dict[str, list[str]]:
        normalized: dict[str, list[str]] = {}
        selected_refs = _safe_dict(input_pkg.get("selected_refs"))
        connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
        refs_contract = _safe_dict(package.get("refs_contract"))
        if not refs_contract:
            refs_contract = _safe_dict(_safe_dict(package.get("story_core")).get("refs_contract"))
        refs_contract_connected_summary = _safe_dict(refs_contract.get("connected_refs_summary"))
        refs_inventory = _safe_dict(package.get("refs_inventory"))

        def _append(role: str, candidate: str) -> None:
            clean_role = str(role or "").strip()
            clean_url = str(candidate or "").strip()
            if not clean_role or not clean_url:
                return
            bucket = normalized.setdefault(clean_role, [])
            if clean_url not in bucket:
                bucket.append(clean_url)

        def _append_from_map(raw_map: Any) -> None:
            role_map = _safe_dict(raw_map)
            for raw_role, raw_values in role_map.items():
                role = str(raw_role or "").strip()
                if not role:
                    continue
                for value in _safe_list(raw_values):
                    _append(role, str(value or ""))

        _append_from_map(input_pkg.get("refs_by_role"))
        _append_from_map(input_pkg.get("refsByRole"))
        _append_from_map(connected_summary.get("connectedRefsPresentByRole"))
        _append_from_map(connected_summary.get("refsPresentByRole"))
        _append_from_map(connected_summary.get("connected_refs_present_by_role"))
        _append_from_map(connected_summary.get("refs_present_by_role"))
        _append_from_map(refs_contract_connected_summary.get("connectedRefsPresentByRole"))
        _append_from_map(refs_contract_connected_summary.get("refsPresentByRole"))
        _append_from_map(refs_contract_connected_summary.get("connected_refs_present_by_role"))
        _append_from_map(refs_contract_connected_summary.get("refs_present_by_role"))

        for role in final_refs_roles:
            selected_value = selected_refs.get(role)
            if isinstance(selected_value, list):
                for value in _safe_list(selected_value):
                    _append(role, str(value or ""))
            else:
                _append(role, str(selected_value or ""))
            for value in _extract_ref_urls(refs_inventory.get(_role_to_ref_key(role))):
                _append(role, value)

        return normalized

    final_refs_by_role = _collect_final_refs_by_role()
    final_refs_summary_by_role = {role: len(final_refs_by_role.get(role) or []) for role in final_refs_roles}
    final_missing_character_ref_segments: list[str] = []
    final_segments_with_source_image_refs = 0
    connected_summary = _safe_dict(input_pkg.get("connected_context_summary"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))

    def _resolve_route_for_finalize_row(segment_row: dict[str, Any], plan_row_data: dict[str, Any], metadata_row: dict[str, Any]) -> str:
        route_candidates = [
            segment_row.get("route"),
            segment_row.get("video_generation_route"),
            segment_row.get("planned_video_generation_route"),
            segment_row.get("resolved_workflow_key"),
            plan_row_data.get("route"),
            plan_row_data.get("video_generation_route"),
            plan_row_data.get("planned_video_generation_route"),
            metadata_row.get("route_type"),
        ]
        for candidate in route_candidates:
            normalized = str(candidate or "").strip().lower()
            if not normalized:
                continue
            if normalized in {"first_last", "first-last", "f_l"}:
                return "first_last"
            if normalized in {"ia2v", "lip_sync", "lip_sync_music", "avatar_lipsync"}:
                return "ia2v"
            if normalized in {"i2v", "image_video", "image_to_video", "standard_video"}:
                return "i2v"
            return normalized
        return "i2v"

    def _build_linked_assets(
        segment_row: dict[str, Any],
        plan_row_data: dict[str, Any],
        prompts_row_data: dict[str, Any],
        role_row_data: dict[str, Any],
    ) -> dict[str, Any]:
        role_keys = _collect_scene_role_keys(segment_row, plan_row_data, prompts_row_data, role_row_data)
        character_refs: dict[str, list[str]] = {}
        for role in ("character_1", "character_2", "character_3"):
            if role in role_keys and (final_refs_by_role.get(role) or []):
                character_refs[role] = list(final_refs_by_role.get(role) or [])

        source_image_refs_candidates = [
            segment_row.get("source_image_refs"),
            plan_row_data.get("source_image_refs"),
            prompts_row_data.get("source_image_refs"),
            plan_row_data.get("image_refs"),
            prompts_row_data.get("image_refs"),
        ]
        source_image_refs: list[str] = []
        for candidate in source_image_refs_candidates:
            for item in _safe_list(candidate):
                value = str(item or "").strip()
                if value and value not in source_image_refs:
                    source_image_refs.append(value)

        for role in role_keys:
            for value in _safe_list(final_refs_by_role.get(role)):
                url = str(value or "").strip()
                if url and url not in source_image_refs:
                    source_image_refs.append(url)

        return {
            "audio_url": project_audio_url or None,
            "character_refs": character_refs,
            "refs": deepcopy(final_refs_by_role),
            "source_image_refs": source_image_refs,
            "start_frame_asset": segment_row.get("start_frame_asset"),
            "end_frame_asset": segment_row.get("end_frame_asset"),
        }

    def _collect_scene_role_keys(
        segment_row: dict[str, Any],
        plan_row_data: dict[str, Any],
        prompts_row_data: dict[str, Any],
        role_row_data: dict[str, Any],
    ) -> list[str]:
        role_keys: list[str] = []
        role_keys.extend(
            str(value).strip()
            for value in [
                role_row_data.get("primary_role"),
                role_row_data.get("visual_focus_role"),
                role_row_data.get("vocal_owner_role"),
                segment_row.get("primary_role"),
                segment_row.get("visual_focus_role"),
                plan_row_data.get("primary_role"),
                plan_row_data.get("visual_focus_role"),
                prompts_row_data.get("primary_role"),
                prompts_row_data.get("visual_focus_role"),
            ]
            if str(value or "").strip()
        )
        for role_list in (
            role_row_data.get("secondary_roles"),
            role_row_data.get("active_roles"),
            segment_row.get("active_roles"),
            segment_row.get("scene_roles"),
            plan_row_data.get("active_roles"),
            plan_row_data.get("scene_roles"),
            prompts_row_data.get("active_roles"),
            prompts_row_data.get("scene_roles"),
        ):
            role_keys.extend(str(item).strip() for item in _safe_list(role_list) if str(item).strip())
        role_keys = list(dict.fromkeys([role for role in role_keys if role]))
        present_cast_roles = [str(role or "").strip() for role in _safe_list(connected_summary.get("presentCastRoles")) if str(role or "").strip()]
        character_count_raw = connected_summary.get("characterCount")
        character_count = str(character_count_raw).strip() if character_count_raw is not None else ""
        has_character_1_ref = bool(_extract_ref_urls(refs_inventory.get("ref_character_1")))
        has_character_2_ref = bool(_extract_ref_urls(refs_inventory.get("ref_character_2")))
        has_character_3_ref = bool(_extract_ref_urls(refs_inventory.get("ref_character_3")))
        single_character_project = (
            character_count in {"1", "1.0"}
            or present_cast_roles == ["character_1"]
            or (has_character_1_ref and not has_character_2_ref and not has_character_3_ref)
        )
        if not role_keys and final_refs_by_role.get("character_1") and single_character_project:
            role_keys.append("character_1")
        return role_keys

    def _merge_scene_continuity_fields(
        segment_row: dict[str, Any],
        plan_row_data: dict[str, Any],
        prompts_row_data: dict[str, Any],
        role_row_data: dict[str, Any],
        *,
        route_name: str,
        role_keys: list[str],
    ) -> dict[str, Any]:
        def _first_non_empty_str(*values: Any) -> str:
            for value in values:
                clean = str(value or "").strip()
                if clean:
                    return clean
            return ""

        def _first_non_empty_list(*values: Any) -> list[Any]:
            for value in values:
                if isinstance(value, list) and value:
                    return list(value)
            return []

        def _first_non_empty_map(*values: Any) -> dict[str, Any]:
            for value in values:
                if isinstance(value, dict) and value:
                    return _safe_dict(value)
            return {}

        raw_primary_role = _first_non_empty_str(
            segment_row.get("primaryRole"),
            prompts_row_data.get("primaryRole"),
            plan_row_data.get("primaryRole"),
            role_row_data.get("primary_role"),
            plan_row_data.get("primary_role"),
        ).lower()
        primary_role = raw_primary_role if raw_primary_role in {"character_1", "character_2", "character_3", "group"} else raw_primary_role
        raw_hero_entity_id = _first_non_empty_str(
            segment_row.get("heroEntityId"),
            prompts_row_data.get("heroEntityId"),
            plan_row_data.get("heroEntityId"),
            primary_role if primary_role in {"character_1", "character_2", "character_3"} else "",
        ).lower()
        hero_entity_id = raw_hero_entity_id if raw_hero_entity_id in {"character_1", "character_2", "character_3"} else raw_hero_entity_id

        must_appear = [
            str(role or "").strip().lower()
            for role in _first_non_empty_list(
                segment_row.get("mustAppear"),
                prompts_row_data.get("mustAppear"),
                plan_row_data.get("mustAppear"),
            )
            if str(role or "").strip()
        ]
        refs_used = [
            str(role or "").strip().lower()
            for role in _first_non_empty_list(
                segment_row.get("refsUsed"),
                prompts_row_data.get("refsUsed"),
                plan_row_data.get("refsUsed"),
            )
            if str(role or "").strip()
        ]
        refs_by_role = _first_non_empty_map(
            segment_row.get("refsByRole"),
            prompts_row_data.get("refsByRole"),
            plan_row_data.get("refsByRole"),
        )
        previous_scene_image_url = _first_non_empty_str(
            segment_row.get("previousSceneImageUrl"),
            prompts_row_data.get("previousSceneImageUrl"),
            plan_row_data.get("previousSceneImageUrl"),
        )

        visual_focus_role = _first_non_empty_str(
            segment_row.get("visual_focus_role"),
            prompts_row_data.get("visual_focus_role"),
            plan_row_data.get("visual_focus_role"),
            role_row_data.get("visual_focus_role"),
        ).lower()
        speaker_role = _first_non_empty_str(
            segment_row.get("speaker_role"),
            prompts_row_data.get("speaker_role"),
            plan_row_data.get("speaker_role"),
            role_row_data.get("speaker_role"),
        ).lower()
        cast_roles = {"character_1", "character_2", "character_3", "group"}
        must_appear_cast = [
            str(role or "").strip().lower()
            for role in must_appear
            if str(role or "").strip().lower() in cast_roles
        ]
        refs_by_role_cast_present = any(_safe_list(_safe_dict(refs_by_role).get(role)) for role in ("character_1", "character_2", "character_3"))
        explicit_cast_focus_present = bool(
            primary_role in cast_roles
            or hero_entity_id in {"character_1", "character_2", "character_3"}
            or must_appear_cast
            or refs_by_role_cast_present
        )
        environment_only_cutaway = bool(
            route_name == "i2v"
            and visual_focus_role == "environment"
            and not speaker_role
            and not explicit_cast_focus_present
        )
        has_human_subject = bool(
            any(role in cast_roles for role in role_keys)
            or primary_role in cast_roles
            or hero_entity_id in {"character_1", "character_2", "character_3"}
            or must_appear
        ) and not environment_only_cutaway

        explicit_hero_ref_exists = bool(
            hero_entity_id
            and (
                _safe_list(_safe_dict(refs_by_role).get(hero_entity_id))
                or _safe_list(_safe_dict(final_refs_by_role).get(hero_entity_id))
            )
        )
        same_hero_continuation_scene = bool(
            route_name in {"i2v", "ia2v"}
            and has_human_subject
            and explicit_hero_ref_exists
            and (primary_role == hero_entity_id or not primary_role)
            and hero_entity_id in {"character_1", "character_2", "character_3"}
            and not environment_only_cutaway
        )
        continuity_repair_active = bool(
            same_hero_continuation_scene
            and (
                previous_scene_image_url
                or bool(segment_row.get("continuityFixApplied"))
                or bool(segment_row.get("continuity_fix_applied"))
            )
        )

        if hero_entity_id in {"character_1", "character_2", "character_3"}:
            if hero_entity_id not in must_appear and same_hero_continuation_scene:
                must_appear.append(hero_entity_id)
            if hero_entity_id not in refs_used and same_hero_continuation_scene and explicit_hero_ref_exists:
                refs_used.append(hero_entity_id)
            if hero_entity_id not in refs_by_role:
                refs_by_role = dict(refs_by_role)
                refs_by_role[hero_entity_id] = list(_safe_list(final_refs_by_role.get(hero_entity_id)))

        identity_lock_applied = bool(
            segment_row.get("identityLockApplied")
            or segment_row.get("identity_lock_applied")
            or (same_hero_continuation_scene and explicit_hero_ref_exists)
        )
        body_lock_applied = bool(
            segment_row.get("bodyLockApplied")
            or segment_row.get("body_lock_applied")
            or (route_name != "ia2v" and same_hero_continuation_scene and explicit_hero_ref_exists)
        )
        confirmed_hero_look_reference_used = bool(
            segment_row.get("confirmedHeroLookReferenceUsed")
            or (same_hero_continuation_scene and explicit_hero_ref_exists)
        )
        continuity_fix_applied = bool(
            segment_row.get("continuityFixApplied")
            or segment_row.get("continuity_fix_applied")
            or continuity_repair_active
        )

        return {
            "primaryRole": primary_role or None,
            "heroEntityId": hero_entity_id or None,
            "mustAppear": list(dict.fromkeys([role for role in must_appear if role])),
            "refsUsed": list(dict.fromkeys([role for role in refs_used if role])),
            "refsByRole": _safe_dict(refs_by_role),
            "previousSceneImageUrl": previous_scene_image_url or None,
            "identityLockApplied": identity_lock_applied,
            "bodyLockApplied": body_lock_applied,
            "confirmedHeroLookReferenceUsed": confirmed_hero_look_reference_used,
            "continuityFixApplied": continuity_fix_applied,
        }

    for idx, segment in enumerate(final_segments, start=1):
        segment_id = str(segment.get("segment_id") or segment.get("scene_id") or f"seg_{idx}").strip()
        scene_id = str(segment_id or segment.get("scene_id") or "").strip()
        plan_row = _safe_dict(plan_by_id.get(segment_id) or plan_by_id.get(scene_id))
        prompts_row = _safe_dict(prompts_by_id.get(segment_id) or prompts_by_id.get(scene_id))
        role_casting_row = _safe_dict(role_casting_by_id.get(segment_id) or role_casting_by_id.get(scene_id))

        route_payload = _safe_dict(segment.get("route_payload"))
        engine_hints = _safe_dict(segment.get("engine_hints"))
        video_metadata = _safe_dict(segment.get("video_metadata"))
        route = _resolve_route_for_finalize_row(segment, plan_row, video_metadata)
        role_keys = _collect_scene_role_keys(segment, plan_row, prompts_row, role_casting_row)
        continuity_fields = _merge_scene_continuity_fields(
            segment,
            plan_row,
            prompts_row,
            role_casting_row,
            route_name=route,
            role_keys=role_keys,
        )
        linked_assets = _build_linked_assets(segment, plan_row, prompts_row, role_casting_row)
        if _safe_list(linked_assets.get("source_image_refs")):
            final_segments_with_source_image_refs += 1

        t0 = _to_float(plan_row.get("t0"), 0.0)
        t1 = _to_float(plan_row.get("t1"), t0)
        if t1 < t0:
            t1 = t0
        duration_sec = _to_float(plan_row.get("duration_sec"), max(0.0, t1 - t0))

        beat_timing = beat_timing_by_segment.get(segment_id)
        if beat_timing is not None:
            t0, t1 = beat_timing
            duration_sec = max(0.0, t1 - t0)
        else:
            logger.warning("[scenario_stage_pipeline] finalize_stage missing beat timing for segment_id=%s", segment_id)

        segment["t0"] = t0
        segment["t1"] = t1
        segment["duration_sec"] = duration_sec

        final_image_prompt = str(
            route_payload.get("image_prompt")
            or prompts_row.get("photo_prompt")
            or route_payload.get("positive_prompt")
            or ""
        ).strip()
        final_video_prompt_text = str(
            route_payload.get("video_prompt")
            or route_payload.get("positive_prompt")
            or prompts_row.get("video_prompt")
            or ""
        ).strip()
        final_video_prompt_text = _strip_literal_quoted_dialogue(final_video_prompt_text)
        final_negative_prompt = str(route_payload.get("negative_prompt") or prompts_row.get("negative_prompt") or "").strip()
        if route == "ia2v":
            final_negative_prompt = IA2V_MINIMAL_NEGATIVE_PROMPT
        final_route_positive_prompt = _strip_literal_quoted_dialogue(
            str(route_payload.get("positive_prompt") or final_video_prompt_text or "").strip()
        )
        final_prompt_source = str(segment.get("prompt_source") or FINAL_VIDEO_PROMPT_STAGE_VERSION).strip() or FINAL_VIDEO_PROMPT_STAGE_VERSION
        final_payload = {
            "image_prompt": final_image_prompt,
            "video_prompt": final_video_prompt_text,
            "negative_prompt": final_negative_prompt,
            "negative_video_prompt": final_negative_prompt,
            "route": route,
            "primaryRole": continuity_fields.get("primaryRole"),
            "heroEntityId": continuity_fields.get("heroEntityId"),
            "mustAppear": list(_safe_list(continuity_fields.get("mustAppear"))),
            "refsUsed": list(_safe_list(continuity_fields.get("refsUsed"))),
            "refsByRole": _safe_dict(continuity_fields.get("refsByRole")),
            "previousSceneImageUrl": continuity_fields.get("previousSceneImageUrl"),
            "identityLockApplied": bool(continuity_fields.get("identityLockApplied")),
            "bodyLockApplied": bool(continuity_fields.get("bodyLockApplied")),
            "confirmedHeroLookReferenceUsed": bool(continuity_fields.get("confirmedHeroLookReferenceUsed")),
            "continuityFixApplied": bool(continuity_fields.get("continuityFixApplied")),
            "linked_assets": deepcopy(linked_assets),
            "audio_url": str(linked_assets.get("audio_url") or "").strip(),
            "source_image_refs": list(_safe_list(linked_assets.get("source_image_refs"))),
            "prompt_source": final_prompt_source,
        }

        manifest_row = {
            "segment_id": segment_id,
            "scene_id": scene_id,
            "timing": {
                "t0": t0,
                "t1": t1,
                "duration_sec": duration_sec,
            },
            "route": route,
            "route_payload": {
                "positive_prompt": final_route_positive_prompt,
                "negative_prompt": final_negative_prompt,
                "negative_video_prompt": final_negative_prompt,
                "image_prompt": final_image_prompt,
                "video_prompt": final_video_prompt_text,
                "first_frame_prompt": route_payload.get("first_frame_prompt"),
                "last_frame_prompt": route_payload.get("last_frame_prompt"),
            },
            "engine_hints": engine_hints,
            "video_metadata": video_metadata,
            "linked_assets": linked_assets,
            "audio_behavior_hints": str(segment.get("audio_behavior_hints") or "").strip(),
            "primaryRole": continuity_fields.get("primaryRole"),
            "heroEntityId": continuity_fields.get("heroEntityId"),
            "mustAppear": list(_safe_list(continuity_fields.get("mustAppear"))),
            "refsUsed": list(_safe_list(continuity_fields.get("refsUsed"))),
            "refsByRole": _safe_dict(continuity_fields.get("refsByRole")),
            "previousSceneImageUrl": continuity_fields.get("previousSceneImageUrl"),
            "identityLockApplied": bool(continuity_fields.get("identityLockApplied")),
            "bodyLockApplied": bool(continuity_fields.get("bodyLockApplied")),
            "confirmedHeroLookReferenceUsed": bool(continuity_fields.get("confirmedHeroLookReferenceUsed")),
            "continuityFixApplied": bool(continuity_fields.get("continuityFixApplied")),
            "prompt_source": final_prompt_source,
        }
        render_manifest.append(manifest_row)
        active_warning_roles = list(role_keys)
        active_warning_roles.extend(
            str(value).strip()
            for value in [
                role_casting_row.get("primary_role"),
                role_casting_row.get("visual_focus_role"),
                role_casting_row.get("vocal_owner_role"),
                segment.get("primary_role"),
                segment.get("visual_focus_role"),
                segment.get("vocal_owner_role"),
                plan_row.get("primary_role"),
                plan_row.get("visual_focus_role"),
                plan_row.get("vocal_owner_role"),
                prompts_row.get("primary_role"),
                prompts_row.get("visual_focus_role"),
                prompts_row.get("vocal_owner_role"),
            ]
            if str(value or "").strip()
        )
        active_warning_roles = list(dict.fromkeys([role for role in active_warning_roles if role]))
        if "character_1" in active_warning_roles and not _safe_list(_safe_dict(linked_assets.get("character_refs")).get("character_1")):
            final_missing_character_ref_segments.append(segment_id)

        compat_scenes.append(
            {
                "scene_id": scene_id,
                "segment_id": segment_id,
                "route": route,
                "final_payload": final_payload,
                "image_prompt": final_payload["image_prompt"],
                "video_prompt": final_payload["video_prompt"],
                "negative_video_prompt": final_payload["negative_prompt"],
                "first_frame_prompt": manifest_row["route_payload"].get("first_frame_prompt"),
                "last_frame_prompt": manifest_row["route_payload"].get("last_frame_prompt"),
                "video_metadata": video_metadata,
                "engine_hints": engine_hints,
                "linked_assets": linked_assets,
                "audio_behavior_hints": manifest_row["audio_behavior_hints"],
                "prompt_source": manifest_row["prompt_source"],
                "scene_plan": plan_row,
                "scene_prompt": prompts_row,
                "role_plan": role_casting_row,
                "t0": t0,
                "t1": t1,
                "duration_sec": duration_sec,
            }
        )

    project_metadata = {
        "content_type": str(input_pkg.get("content_type") or ""),
        "director_mode": str(input_pkg.get("director_mode") or ""),
        "format": str(input_pkg.get("format") or ""),
        "audio_url": str(input_pkg.get("audio_url") or "").strip(),
        "audio_duration_sec": _to_float(
            input_pkg.get("audio_duration_sec") if input_pkg.get("audio_duration_sec") is not None else audio_map.get("duration_sec"),
            _to_float(audio_map.get("duration_sec"), 0.0),
        ),
        "story_summary": str(story_core.get("story_summary") or "").strip(),
        "director_summary": str(story_core.get("director_summary") or "").strip(),
        "story_title": str(story_core.get("story_title") or input_pkg.get("title") or "").strip(),
    }

    integrity_input = {
        "project_metadata": project_metadata,
        "render_manifest": render_manifest,
    }
    integrity_hash = hashlib.sha256(
        json.dumps(integrity_input, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    final_storyboard = {
        "final_storyboard_version": "1.1",
        "created_for_signature": _current_scenario_input_signature(package),
        "project_metadata": project_metadata,
        "render_manifest": render_manifest,
        "integrity_hash": integrity_hash,
        "scenes": compat_scenes,
        "source_package_snapshot": {
            "input": input_pkg,
            "audio_map": audio_map,
            "story_core": story_core,
            "role_plan": role_plan,
            "scene_plan": scene_plan,
            "scene_prompts": scene_prompts,
            "final_video_prompt": final_video_prompt,
        },
        "meta": {
            "final_video_prompt_linked_refs_by_role": final_refs_summary_by_role,
            "final_video_prompt_segments_with_source_image_refs": final_segments_with_source_image_refs,
            "final_video_prompt_missing_character_ref_segments": final_missing_character_ref_segments,
        },
    }
    final_storyboard = _attach_downstream_mode_metadata(final_storyboard, package)
    package["final_storyboard"] = final_storyboard

    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["finalize_scene_count"] = len(compat_scenes)
    diagnostics["finalize_render_manifest_count"] = len(render_manifest)
    diagnostics["finalize_used_final_video_prompt_segments"] = bool(final_segments)
    diagnostics["finalize_integrity_hash"] = integrity_hash
    diagnostics["finalize_creative_rewrite_applied"] = False
    diagnostics["final_video_prompt_linked_refs_by_role"] = final_refs_summary_by_role
    diagnostics["final_video_prompt_segments_with_source_image_refs"] = final_segments_with_source_image_refs
    diagnostics["final_video_prompt_missing_character_ref_segments"] = final_missing_character_ref_segments
    package["diagnostics"] = diagnostics
    _append_diag_event(package, f"final_storyboard built manifest={len(render_manifest)}", stage_id="finalize")
    return package


def _run_story_core_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    creative_config = _normalize_creative_config(input_pkg.get("creative_config"))
    audio_map = _safe_dict(package.get("audio_map"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    assigned_roles = _safe_dict(package.get("assigned_roles"))
    story_core_mode = _detect_story_core_mode(input_pkg)
    model_id = _resolve_active_video_model_id(input_pkg)
    capability_profile = get_video_model_capability_profile(model_id, "i2v")
    fallback = _default_story_core(input_pkg)
    if not _is_usable_audio_map(audio_map):
        raise RuntimeError("story_core_requires_audio_map")
    fallback_route_mix_doctrine, route_mix_source = _build_route_mix_doctrine_for_scenes(creative_config)
    fallback["story_guidance"] = _default_story_core_guidance(creative_config)
    fallback_core_v1 = _build_story_core_v11(
        input_pkg=input_pkg,
        audio_map=audio_map,
        refs_inventory=refs_inventory,
        assigned_roles=assigned_roles,
        parsed_story_core=fallback,
        fallback_story_core=fallback,
    )
    fallback["story_core_v1"] = fallback_core_v1
    prop_contracts, prop_guard_applied = _normalize_story_core_prop_contracts(input_pkg, refs_inventory)
    ref_attachment_summary = {
        "character_1": {"attached": False, "error": "", "source": "", "url": ""},
        "props": {"attached": False, "error": "", "source": "", "url": "", "connected": bool(prop_contracts), "contracts_count": len(prop_contracts)},
        "location": {"attached": False, "error": "", "source": "", "url": ""},
        "style": {"attached": False, "error": "", "source": "", "url": ""},
    }
    grounding_level = "strict" if prop_contracts else "standard"
    diagnostics = _safe_dict(package.get("diagnostics"))
    previous_story_core_payload = deepcopy(_safe_dict(package.get("story_core")))
    package["story_core_stale"] = False
    diagnostics["story_core_mode"] = story_core_mode
    diagnostics["story_core_used_model"] = "gemini-3.1-pro-preview"
    diagnostics["story_core_character_ref_attached"] = False
    diagnostics["story_core_character_ref_source"] = ""
    diagnostics["story_core_character_ref_error"] = ""
    diagnostics["story_core_prop_contracts"] = prop_contracts
    diagnostics["story_core_prop_confusion_guard_applied"] = bool(prop_guard_applied)
    diagnostics["story_core_ref_attachment_summary"] = ref_attachment_summary
    diagnostics["story_core_grounding_level"] = grounding_level
    diagnostics["story_core_audio_informed"] = bool(audio_map)
    diagnostics["story_core_audio_dramaturgy_source"] = "audio_map" if audio_map else ""
    diagnostics["story_core_creative_config_active"] = creative_config
    diagnostics["story_core_route_mix_doctrine_source"] = route_mix_source
    diagnostics["story_core_route_mix_doctrine_applied"] = fallback_route_mix_doctrine
    diagnostics["story_core_route_mix_doctrine_ratios"] = _safe_dict(fallback_route_mix_doctrine.get("short_clip_default_target_ratios"))
    diagnostics["story_core_textual_directive_present"] = bool(_has_textual_directive(input_pkg))
    diagnostics["story_core_available_roles"] = []
    diagnostics["story_core_attached_ref_roles"] = []
    diagnostics["story_core_attached_ref_count"] = 0
    diagnostics["story_core_props_ref_attached"] = False
    diagnostics["story_core_location_ref_attached"] = False
    diagnostics["story_core_style_ref_attached"] = False
    diagnostics["story_core_payload_mode"] = "full"
    diagnostics["story_core_available_roles_resolved"] = []
    diagnostics["story_core_director_world_lock_summary"] = ""
    diagnostics["story_core_compact_context_size_estimate"] = 0
    diagnostics["story_core_refs_sources_used"] = []
    diagnostics["story_core_raw_response"] = ""
    diagnostics["story_core_normalized_payload"] = {}
    diagnostics["story_core_semantic_compression_applied"] = False
    diagnostics["story_core_semantic_compression_segments"] = []
    diagnostics["story_core_validation_errors"] = []
    diagnostics["story_core_role_identity_expectations"] = {}
    diagnostics["story_core_retry_used"] = False
    diagnostics["story_core_retry_feedback"] = ""
    diagnostics["story_core_last_error_code"] = ""
    diagnostics["story_core_configured_timeout_sec"] = get_scenario_stage_timeout("story_core")
    diagnostics["story_core_timeout_stage_policy_name"] = scenario_timeout_policy_name("story_core")
    diagnostics["story_core_timed_out"] = False
    diagnostics["story_core_timeout_retry_attempted"] = False
    diagnostics["story_core_response_was_empty_after_timeout"] = False
    diagnostics["story_core_hard_fail"] = False
    diagnostics["story_core_failed_payload_rejected"] = False
    diagnostics["story_core_previous_payload_was_cleared"] = False
    diagnostics["story_core_previous_stale_payload"] = {}
    diagnostics["story_core_id_mismatch_kind"] = ""
    diagnostics["story_core_audio_canonical_source"] = "audio_map.segments[]"
    diagnostics["story_core_legacy_fields_non_canonical"] = ["scene_slots", "phrase_units", "scene_candidate_windows"]
    diagnostics["story_core_audio_segments_source"] = ""
    diagnostics["story_core_technical_spawn_match_type"] = ""
    diagnostics["story_core_technical_spawn_match_pattern"] = ""
    diagnostics["story_core_technical_spawn_match_excerpt"] = ""
    diagnostics["story_core_technical_spawn_match_zone"] = ""
    diagnostics["story_core_technical_spawn_match_field"] = ""
    diagnostics["story_core_technical_spawn_match_term"] = ""
    diagnostics["story_core_technical_spawn_rule_name"] = ""
    diagnostics["story_core_technical_spawn_origin"] = ""
    diagnostics["story_core_technical_spawn_introduced_by"] = ""
    diagnostics["story_core_technical_spawn_detected_in"] = ""
    diagnostics["story_core_role_binding_contradiction_matches"] = []
    diagnostics["story_core_retry_sanitize_applied"] = False
    diagnostics["story_core_retry_removed_terms"] = []
    diagnostics["story_core_retry_prompt_mode"] = "default"
    diagnostics["story_core_entropy_threshold_triggered"] = False
    diagnostics["story_core_flatline_span_start"] = ""
    diagnostics["story_core_flatline_span_end"] = ""
    diagnostics["story_core_flatline_reason"] = ""
    diagnostics["story_core_flatline_repeated_story_functions"] = []
    diagnostics["story_core_flatline_repeated_pressure_mode"] = ""
    diagnostics["story_core_flatline_retry_used"] = False
    diagnostics["story_core_two_axis_validation_passed"] = True
    diagnostics["story_core_axis_change_frame"] = []
    diagnostics["story_core_axis_change_meaning"] = []
    diagnostics["story_core_duplicate_adjacent_pairs"] = []
    diagnostics["story_core_semantic_duplicate_pairs"] = []
    diagnostics["story_core_visual_duplicate_pairs"] = []
    diagnostics["story_core_two_axis_fail_reason_by_pair"] = []
    diagnostics["story_core_two_axis_pair_status"] = []
    diagnostics["story_core_hero_world_balance_score"] = 1.0
    diagnostics["story_core_hero_first_streak"] = 0
    diagnostics["story_core_world_first_streak"] = 0
    diagnostics["story_core_reactive_world_rule_passed"] = True
    diagnostics["story_core_reactive_world_retry_used"] = False
    diagnostics["story_core_association_level_summary"] = {"level_0": 0, "level_1": 0, "level_2_plus": 0}
    diagnostics["story_core_literal_fallback_count"] = 0
    diagnostics["story_core_literal_fallback_segments"] = []
    diagnostics["story_core_second_degree_segments"] = []
    diagnostics["story_core_association_scoring_reason_by_segment"] = []
    diagnostics["story_core_literal_generic_cliche_segments"] = []
    diagnostics["story_core_direct_emotion_illustration_segments"] = []
    diagnostics["story_core_low_subtext_segments"] = []
    diagnostics["story_core_anti_literal_retry_used"] = False
    diagnostics["story_core_visual_breath_triggered"] = False
    diagnostics["story_core_visual_breath_reason"] = ""
    diagnostics["story_core_visual_breath_inserted_in_logic"] = False
    diagnostics["story_core_contrast_event_required"] = False
    diagnostics["story_core_contrast_event_present"] = False
    diagnostics["story_core_overload_spans"] = []
    diagnostics["story_core_local_breath_windows_checked"] = []
    diagnostics["story_core_local_breath_found"] = False
    diagnostics["story_core_local_breath_segment_ids"] = []
    diagnostics["story_core_visual_breath_fail_spans"] = []
    diagnostics["story_core_second_attempt_changed_payload"] = False
    diagnostics["active_video_model_capability_profile"] = model_id
    diagnostics["active_route_capability_mode"] = "story_core_planning_bounds"
    diagnostics["story_core_capability_guard_applied"] = True
    diagnostics["scene_plan_capability_guard_applied"] = False
    diagnostics["prompt_capability_guard_applied"] = False
    diagnostics["capability_rules_source_version"] = get_capability_rules_source_version()
    package["diagnostics"] = diagnostics
    _append_diag_event(package, "story_core audio-informed build requested", stage_id="story_core")
    try:
        api_key = _resolve_stage_gemini_api_key(package, stage_id="story_core")
        inline_ref_parts, inline_ref_diag = _build_story_core_inline_ref_parts(
            input_pkg=input_pkg,
            refs_inventory=refs_inventory,
        )
        if bool(inline_ref_diag.get("story_core_character_ref_attached")):
            grounding_level = "strict"
        elif bool(inline_ref_diag.get("story_core_attached_ref_count")):
            grounding_level = "standard"
        else:
            grounding_level = "cautious"
        ref_attachment_summary = _safe_dict(inline_ref_diag.get("story_core_ref_attachment_summary"))
        props_summary = _safe_dict(ref_attachment_summary.get("props"))
        props_summary["connected"] = bool(prop_contracts)
        props_summary["contracts_count"] = len(prop_contracts)
        ref_attachment_summary["props"] = props_summary
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics.update(inline_ref_diag)
        diagnostics["story_core_ref_attachment_summary"] = ref_attachment_summary
        diagnostics["story_core_grounding_level"] = grounding_level
        package["diagnostics"] = diagnostics
        input_with_assignments = dict(input_pkg)
        input_with_assignments["assigned_roles_override"] = assigned_roles
        core_input_context = _build_story_core_input_context(
            input_pkg=input_with_assignments,
            audio_map=audio_map,
            refs_inventory=refs_inventory,
            prop_contracts=prop_contracts,
            ref_attachment_summary=ref_attachment_summary,
            grounding_level=grounding_level,
        )
        diagnostics["story_core_available_roles"] = _safe_list(core_input_context.get("available_roles"))
        diagnostics["story_core_available_roles_resolved"] = _safe_list(core_input_context.get("available_roles"))
        diagnostics["story_core_attached_ref_roles"] = _safe_list(core_input_context.get("story_core_attached_ref_roles"))
        diagnostics["story_core_director_world_lock_summary"] = str(core_input_context.get("story_core_director_world_lock_summary") or "")
        diagnostics["story_core_compact_context_size_estimate"] = int(core_input_context.get("story_core_compact_context_size_estimate") or 0)
        diagnostics["story_core_refs_sources_used"] = _safe_list(core_input_context.get("story_core_refs_sources_used"))
        diagnostics["story_core_payload_mode"] = "lean"
        diagnostics.update(
            build_capability_diagnostics_summary(
                model_id=model_id,
                route_type="story_core_planning_bounds",
                story_core_guard_applied=True,
                scene_plan_guard_applied=False,
                prompt_guard_applied=False,
            )
        )
        package["diagnostics"] = diagnostics
        capability_bounds_text = (
            f"model={model_id}; verified_safe={', '.join([str(v) for v in _safe_list(capability_profile.get('verified_safe'))])}; "
            f"experimental(opt-in)={', '.join([str(v) for v in _safe_list(capability_profile.get('experimental'))])}; "
            f"blocked={', '.join([str(v) for v in _safe_list(capability_profile.get('blocked'))])}"
        )[:1500]
        prompt = _build_story_core_prompt(
            core_input_context,
            assigned_roles,
            story_core_mode,
            capability_bounds_text,
        )
        core_segments, core_segments_source = _coerce_core_audio_segments(audio_map)
        user_concept = _extract_story_user_concept(input_pkg)
        role_identity_expectations = _extract_role_identity_expectations(input_pkg, assigned_roles)
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics["story_core_role_identity_expectations"] = role_identity_expectations
        package["diagnostics"] = diagnostics
        validation_feedback = ""
        retry_used = False
        retry_prompt_mode = "default"
        retry_sanitize_applied = False
        retry_removed_terms: list[str] = []
        retry_sanitized_seed: dict[str, Any] = {}
        first_attempt_normalized_fingerprint = ""
        last_error_code = CORE_SCHEMA_INVALID
        last_errors: list[str] = []
        configured_timeout = get_scenario_stage_timeout("story_core")
        for attempt in range(2):
            prompt_with_feedback = prompt
            if validation_feedback:
                prompt_with_feedback = f"{prompt_with_feedback}\nVALIDATION_FEEDBACK_FROM_PREVIOUS_ATTEMPT:\n{validation_feedback}\n"
            if retry_used and retry_prompt_mode == "narrative_only_de_technicalized_retry":
                prompt_with_feedback = (
                    f"{prompt_with_feedback}\nRETRY_MODE: narrative_only_de_technicalized_retry\n"
                    "The previous response leaked technical/backstage wording into narrative forbidden zones.\n"
                    "Return strictly human narrative language only.\n"
                    "Do NOT use camera/shot/framing/motion/profile/prompt/renderer/delivery/workflow/model/route terms.\n"
                    "Do NOT mention i2v/ia2v/first_last anywhere in CORE payload.\n"
                    "If a phrase sounds like production instruction, rewrite it as plain story meaning.\n"
                    "Keep exact segment_id mapping and order.\n"
                )
                if retry_removed_terms:
                    prompt_with_feedback = f"{prompt_with_feedback}BANNED_TERMS_FROM_PREVIOUS_ATTEMPT: {json.dumps(retry_removed_terms[:24], ensure_ascii=False)}\n"
                if retry_sanitized_seed:
                    prompt_with_feedback = (
                        f"{prompt_with_feedback}SANITIZED_REFERENCE_JSON_FROM_PREVIOUS_ATTEMPT (narrative-only baseline, keep meaning but improve prose):\n"
                        f"{json.dumps(retry_sanitized_seed, ensure_ascii=False)[:2600]}\n"
                    )
            parts: list[dict[str, Any]] = [{"text": prompt_with_feedback}, *inline_ref_parts]
            body = {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            }
            response = post_generate_content(
                api_key=api_key,
                model="gemini-3.1-pro-preview",
                body=body,
                timeout=configured_timeout,
            )
            if isinstance(response, dict) and response.get("__http_error__"):
                timeout_error = is_timeout_error(response.get("text"))
                last_error_code = "story_core_timeout" if timeout_error else CORE_SCHEMA_INVALID
                last_errors = [f"gemini_http_error:{response.get('status')}:{response.get('text')}"]
                if timeout_error:
                    diagnostics = _safe_dict(package.get("diagnostics"))
                    diagnostics["story_core_timed_out"] = True
                    diagnostics["story_core_response_was_empty_after_timeout"] = True
                    diagnostics["story_core_timeout_retry_attempted"] = attempt == 0
                    package["diagnostics"] = diagnostics
            else:
                raw_text = _extract_gemini_text(response)
                diagnostics = _safe_dict(package.get("diagnostics"))
                diagnostics["story_core_raw_response"] = raw_text
                parsed = _extract_json_obj(raw_text)
                resolved_director_mode = _resolve_director_mode(
                    input_pkg.get("director_mode"),
                    content_type=str(input_pkg.get("content_type") or "music_video"),
                )
                normalized_core = _normalize_story_core_contract_payload(
                    parsed=parsed,
                    audio_segments=core_segments,
                    creative_config=creative_config,
                )
                normalized_core, semantic_compression_diag = _apply_story_core_semantic_compression(
                    normalized_core,
                    content_type=str(input_pkg.get("content_type") or "music_video"),
                    director_mode=resolved_director_mode,
                    user_concept=user_concept,
                )
                sanitized_normalized_core, sanitize_removed_terms, sanitize_applied = _sanitize_story_core_forbidden_technical_language(
                    normalized_core
                )
                if sanitize_applied:
                    normalized_core = sanitized_normalized_core
                    retry_removed_terms = sorted({*retry_removed_terms, *sanitize_removed_terms})[:24]
                normalized_fingerprint = json.dumps(normalized_core, ensure_ascii=False, sort_keys=True)
                if attempt == 0:
                    first_attempt_normalized_fingerprint = normalized_fingerprint
                diagnostics["story_core_normalized_payload"] = normalized_core
                diagnostics["story_core_semantic_compression_applied"] = bool(semantic_compression_diag.get("applied"))
                diagnostics["story_core_semantic_compression_segments"] = _safe_list(
                    semantic_compression_diag.get("rewritten_segments")
                )
                diagnostics["story_core_narrative_sanitizer_applied"] = bool(sanitize_applied)
                diagnostics["story_core_narrative_sanitizer_removed_terms"] = sanitize_removed_terms[:24]
                technical_spawn_debug: dict[str, Any] = {}
                present_cast_roles = _collect_present_cast_roles(
                    _safe_dict(input_pkg.get("connected_context_summary")).get("presentCastRoles")
                )
                audio_map_diag = _safe_dict(_safe_dict(audio_map).get("diagnostics"))
                audio_map_source = str(
                    _safe_dict(audio_map).get("audio_map_source_of_truth")
                    or audio_map_diag.get("audio_map_source_of_truth")
                    or _safe_dict(package.get("diagnostics")).get("audio_map_source_of_truth")
                    or ""
                ).strip().lower()
                audio_map_quality_context = {
                    "segment_ids_preserved": bool(
                        [str(_safe_dict(seg).get("segment_id") or "").strip() for seg in core_segments if str(_safe_dict(seg).get("segment_id") or "").strip()]
                        == [
                            str(_safe_dict(seg).get("segment_id") or "").strip()
                            for seg in _safe_list(_safe_dict(audio_map).get("segments"))
                            if str(_safe_dict(seg).get("segment_id") or "").strip()
                        ]
                    ),
                    "coverage_ok": bool(audio_map_diag.get("coverage_ok")),
                    "gap_sum_sec": _to_float(audio_map_diag.get("gap_sum_sec"), _to_float(audio_map_diag.get("audio_map_gap_sum_sec"), 0.0)),
                    "overlap_sum_sec": _to_float(audio_map_diag.get("overlap_sum_sec"), _to_float(audio_map_diag.get("audio_map_overlap_sum_sec"), 0.0)),
                    "phrase_endings_ok": bool(
                        _safe_dict(audio_map).get("segmentation_validation", {}).get("prefer_phrase_endings")
                        if isinstance(_safe_dict(audio_map).get("segmentation_validation"), dict)
                        else True
                    ),
                    "audio_map_source_valid": audio_map_source in {"segments_v1_1", "audio_map.segments[]"},
                }
                ok, error_code, validation_errors = _validate_story_core_v11_payload(
                    payload=normalized_core,
                    audio_segments=core_segments,
                    user_concept=user_concept,
                    role_identity_expectations=role_identity_expectations,
                    present_cast_roles=present_cast_roles,
                    debug_capture=technical_spawn_debug,
                    content_type=str(input_pkg.get("content_type") or "music_video"),
                    director_mode=_resolve_director_mode(
                        input_pkg.get("director_mode"),
                        content_type=str(input_pkg.get("content_type") or "music_video"),
                    ),
                    audio_map_quality_context=audio_map_quality_context,
                )
                diagnostics["story_core_validation_errors"] = validation_errors
                diagnostics["story_core_retry_used"] = retry_used
                diagnostics["story_core_last_error_code"] = error_code if not ok else ""
                diagnostics["story_core_id_mismatch_kind"] = ""
                diagnostics["story_core_technical_spawn_match_type"] = str(technical_spawn_debug.get("story_core_technical_spawn_match_type") or "")
                diagnostics["story_core_technical_spawn_match_pattern"] = str(technical_spawn_debug.get("story_core_technical_spawn_match_pattern") or "")
                diagnostics["story_core_technical_spawn_match_excerpt"] = str(technical_spawn_debug.get("story_core_technical_spawn_match_excerpt") or "")
                diagnostics["story_core_technical_spawn_match_zone"] = str(technical_spawn_debug.get("story_core_technical_spawn_match_zone") or "")
                diagnostics["story_core_technical_spawn_match_field"] = str(technical_spawn_debug.get("story_core_technical_spawn_match_zone") or "")
                diagnostics["story_core_technical_spawn_match_term"] = str(technical_spawn_debug.get("story_core_technical_spawn_match_term") or "")
                diagnostics["story_core_technical_spawn_rule_name"] = str(technical_spawn_debug.get("story_core_technical_spawn_match_type") or "")
                diagnostics["story_core_role_binding_contradiction_matches"] = _safe_list(
                    technical_spawn_debug.get("story_core_role_binding_contradiction_matches")
                )
                diagnostics["story_core_role_spawning_matches"] = _safe_list(
                    technical_spawn_debug.get("story_core_role_spawning_matches")
                )
                diagnostics["story_core_entropy_threshold_triggered"] = bool(
                    technical_spawn_debug.get("story_core_entropy_threshold_triggered")
                )
                diagnostics["story_core_flatline_span_start"] = str(technical_spawn_debug.get("story_core_flatline_span_start") or "")
                diagnostics["story_core_flatline_span_end"] = str(technical_spawn_debug.get("story_core_flatline_span_end") or "")
                diagnostics["story_core_flatline_reason"] = str(technical_spawn_debug.get("story_core_flatline_reason") or "")
                diagnostics["story_core_flatline_repeated_story_functions"] = _safe_list(
                    technical_spawn_debug.get("story_core_flatline_repeated_story_functions")
                )
                diagnostics["story_core_flatline_repeated_pressure_mode"] = str(
                    technical_spawn_debug.get("story_core_flatline_repeated_pressure_mode") or ""
                )
                diagnostics["story_core_flatline_retry_used"] = bool(technical_spawn_debug.get("story_core_flatline_retry_used"))
                diagnostics["story_core_entropy_flatline_downgraded_to_warning"] = bool(
                    technical_spawn_debug.get("story_core_entropy_flatline_downgraded_to_warning")
                )
                diagnostics["story_core_entropy_flatline_downgrade_reason"] = str(
                    technical_spawn_debug.get("story_core_entropy_flatline_downgrade_reason") or ""
                )
                diagnostics["story_core_two_axis_validation_passed"] = bool(
                    technical_spawn_debug.get("story_core_two_axis_validation_passed", True)
                )
                diagnostics["story_core_axis_change_frame"] = _safe_list(technical_spawn_debug.get("story_core_axis_change_frame"))
                diagnostics["story_core_axis_change_meaning"] = _safe_list(technical_spawn_debug.get("story_core_axis_change_meaning"))
                diagnostics["story_core_duplicate_adjacent_pairs"] = _safe_list(
                    technical_spawn_debug.get("story_core_duplicate_adjacent_pairs")
                )
                diagnostics["story_core_semantic_duplicate_pairs"] = _safe_list(
                    technical_spawn_debug.get("story_core_semantic_duplicate_pairs")
                )
                diagnostics["story_core_visual_duplicate_pairs"] = _safe_list(
                    technical_spawn_debug.get("story_core_visual_duplicate_pairs")
                )
                diagnostics["story_core_two_axis_fail_reason_by_pair"] = _safe_list(
                    technical_spawn_debug.get("story_core_two_axis_fail_reason_by_pair")
                )
                diagnostics["story_core_two_axis_pair_status"] = _safe_list(
                    technical_spawn_debug.get("story_core_two_axis_pair_status")
                )
                diagnostics["story_core_hero_world_balance_score"] = float(
                    technical_spawn_debug.get("story_core_hero_world_balance_score") or 0.0
                )
                diagnostics["story_core_hero_first_streak"] = int(technical_spawn_debug.get("story_core_hero_first_streak") or 0)
                diagnostics["story_core_world_first_streak"] = int(technical_spawn_debug.get("story_core_world_first_streak") or 0)
                diagnostics["story_core_reactive_world_rule_passed"] = bool(
                    technical_spawn_debug.get("story_core_reactive_world_rule_passed", True)
                )
                diagnostics["story_core_reactive_world_retry_used"] = bool(
                    technical_spawn_debug.get("story_core_reactive_world_retry_used")
                )
                diagnostics["story_core_association_level_summary"] = _safe_dict(
                    technical_spawn_debug.get("story_core_association_level_summary")
                )
                diagnostics["story_core_literal_fallback_count"] = int(
                    technical_spawn_debug.get("story_core_literal_fallback_count") or 0
                )
                diagnostics["story_core_literal_fallback_segments"] = _safe_list(
                    technical_spawn_debug.get("story_core_literal_fallback_segments")
                )
                diagnostics["story_core_second_degree_segments"] = _safe_list(
                    technical_spawn_debug.get("story_core_second_degree_segments")
                )
                diagnostics["story_core_association_scoring_reason_by_segment"] = _safe_list(
                    technical_spawn_debug.get("story_core_association_scoring_reason_by_segment")
                )
                diagnostics["story_core_literal_generic_cliche_segments"] = _safe_list(
                    technical_spawn_debug.get("story_core_literal_generic_cliche_segments")
                )
                diagnostics["story_core_direct_emotion_illustration_segments"] = _safe_list(
                    technical_spawn_debug.get("story_core_direct_emotion_illustration_segments")
                )
                diagnostics["story_core_low_subtext_segments"] = _safe_list(
                    technical_spawn_debug.get("story_core_low_subtext_segments")
                )
                diagnostics["story_core_anti_literal_retry_used"] = bool(
                    technical_spawn_debug.get("story_core_anti_literal_retry_used")
                )
                diagnostics["story_core_visual_breath_triggered"] = bool(
                    technical_spawn_debug.get("story_core_visual_breath_triggered")
                )
                diagnostics["story_core_visual_breath_reason"] = str(
                    technical_spawn_debug.get("story_core_visual_breath_reason") or ""
                )
                diagnostics["story_core_visual_breath_inserted_in_logic"] = bool(
                    technical_spawn_debug.get("story_core_visual_breath_inserted_in_logic")
                )
                diagnostics["story_core_contrast_event_required"] = bool(
                    technical_spawn_debug.get("story_core_contrast_event_required")
                )
                diagnostics["story_core_contrast_event_present"] = bool(
                    technical_spawn_debug.get("story_core_contrast_event_present")
                )
                diagnostics["story_core_overload_spans"] = _safe_list(technical_spawn_debug.get("story_core_overload_spans"))
                diagnostics["story_core_local_breath_windows_checked"] = _safe_list(
                    technical_spawn_debug.get("story_core_local_breath_windows_checked")
                )
                diagnostics["story_core_local_breath_found"] = bool(
                    technical_spawn_debug.get("story_core_local_breath_found")
                )
                diagnostics["story_core_local_breath_segment_ids"] = _safe_list(
                    technical_spawn_debug.get("story_core_local_breath_segment_ids")
                )
                diagnostics["story_core_visual_breath_fail_spans"] = _safe_list(
                    technical_spawn_debug.get("story_core_visual_breath_fail_spans")
                )
                diagnostics["story_core_retry_prompt_mode"] = retry_prompt_mode
                diagnostics["story_core_retry_sanitize_applied"] = retry_sanitize_applied
                diagnostics["story_core_retry_removed_terms"] = retry_removed_terms[:24]
                diagnostics["story_core_second_attempt_changed_payload"] = bool(
                    retry_used and bool(first_attempt_normalized_fingerprint) and normalized_fingerprint != first_attempt_normalized_fingerprint
                )
                if not ok and error_code == CORE_ID_MISMATCH:
                    for validation_error in validation_errors:
                        if str(validation_error).startswith("id_mismatch_kind:"):
                            diagnostics["story_core_id_mismatch_kind"] = str(validation_error).split(":", 1)[1].strip()
                            break
                if not ok and error_code == CORE_TECHNICAL_SPAWNING:
                    raw_match = _find_story_core_forbidden_technical_match(_safe_dict(parsed))
                    norm_match = _find_story_core_forbidden_technical_match(normalized_core)
                    detected_in = "none"
                    spawn_origin = "unknown"
                    introduced_by = "unknown"
                    if raw_match and norm_match:
                        detected_in = "raw_and_normalized"
                        spawn_origin = "original_model_output"
                        introduced_by = "model_output"
                    elif raw_match:
                        detected_in = "raw"
                        spawn_origin = "original_model_output"
                        introduced_by = "model_output"
                    elif norm_match:
                        detected_in = "normalized_only"
                        spawn_origin = "after_normalization"
                        introduced_by = "backend_normalization_or_merge"
                    diagnostics["story_core_technical_spawn_origin"] = spawn_origin
                    diagnostics["story_core_technical_spawn_introduced_by"] = introduced_by
                    diagnostics["story_core_technical_spawn_detected_in"] = detected_in
                diagnostics["story_core_audio_canonical_source"] = "audio_map.segments[]"
                diagnostics["story_core_legacy_fields_non_canonical"] = ["scene_slots", "phrase_units", "scene_candidate_windows"]
                diagnostics["story_core_audio_segments_source"] = core_segments_source
                package["diagnostics"] = diagnostics
                if ok:
                    story_core = {
                        "core_version": "1.1",
                        "story_summary": str(normalized_core.get("story_summary") or "").strip(),
                        "opening_anchor": str(normalized_core.get("opening_anchor") or "").strip(),
                        "ending_callback_rule": str(normalized_core.get("ending_callback_rule") or "").strip(),
                        "global_arc": _safe_dict(normalized_core.get("global_arc")),
                        "identity_doctrine": _safe_dict(normalized_core.get("identity_doctrine")),
                        "identity_lock": {"rule": str(_safe_dict(normalized_core.get("identity_doctrine")).get("hero_anchor") or "")},
                        "world_lock": {"rule": str(_safe_dict(normalized_core.get("identity_doctrine")).get("world_doctrine") or "")},
                        "style_lock": {"rule": str(_safe_dict(normalized_core.get("identity_doctrine")).get("style_doctrine") or "")},
                        "story_guidance": {
                            **_default_story_core_guidance(creative_config),
                            "route_mix_doctrine_for_scenes": _safe_dict(normalized_core.get("route_mix_doctrine_for_scenes")),
                        },
                        "narrative_segments": _safe_list(normalized_core.get("narrative_segments")),
                    }
                    story_core_v1 = _build_story_core_v11(
                        input_pkg=input_pkg,
                        audio_map=audio_map,
                        refs_inventory=refs_inventory,
                        assigned_roles=assigned_roles,
                        parsed_story_core=story_core,
                        fallback_story_core=fallback,
                    )
                    visual_ref_applied, visual_ref_source = _collect_visual_ref_identity_diagnostics(
                        package,
                        input_pkg,
                        refs_inventory,
                    )
                    for role in ("character_1", "character_2", "character_3"):
                        if not visual_ref_applied.get(role):
                            continue
                        _apply_visual_ref_identity_lock_to_story_core(
                            story_core,
                            story_core_v1,
                            role,
                            apply_hero_anchor_lock=role == "character_1",
                        )
                    story_core["story_core_v1"] = story_core_v1
                    package["story_core"] = story_core
                    package["story_core_v1"] = story_core_v1
                    package["story_core_stale"] = False
                    diagnostics = _safe_dict(package.get("diagnostics"))
                    diagnostics["story_core_used_fallback"] = False
                    diagnostics["story_core_retry_used"] = retry_used
                    diagnostics["story_core_last_error_code"] = ""
                    diagnostics["story_core_route_mix_doctrine_source"] = route_mix_source
                    diagnostics["story_core_route_mix_doctrine_applied"] = _safe_dict(
                        normalized_core.get("route_mix_doctrine_for_scenes")
                    )
                    diagnostics["story_core_route_mix_doctrine_ratios"] = _safe_dict(
                        _safe_dict(normalized_core.get("route_mix_doctrine_for_scenes")).get("short_clip_default_target_ratios")
                    )
                    diagnostics["visual_ref_identity_lock_applied"] = visual_ref_applied
                    diagnostics["visual_ref_identity_lock_source"] = visual_ref_source
                    package["diagnostics"] = diagnostics
                    _append_diag_event(package, "story_core generated", stage_id="story_core")
                    return package
                last_error_code = error_code or CORE_SCHEMA_INVALID
                last_errors = validation_errors or ["story_core_validation_failed"]
                if attempt == 0 and last_error_code == CORE_TECHNICAL_SPAWNING:
                    retry_sanitized_seed, retry_removed_terms, retry_sanitize_applied = _sanitize_story_core_forbidden_technical_language(normalized_core)
                    retry_prompt_mode = "narrative_only_de_technicalized_retry" if retry_sanitize_applied else "default"

            validation_feedback = _build_core_validation_feedback(last_error_code, last_errors)
            diagnostics = _safe_dict(package.get("diagnostics"))
            diagnostics["story_core_retry_used"] = retry_used
            diagnostics["story_core_retry_feedback"] = validation_feedback
            diagnostics["story_core_retry_prompt_mode"] = retry_prompt_mode
            diagnostics["story_core_retry_sanitize_applied"] = retry_sanitize_applied
            diagnostics["story_core_retry_removed_terms"] = retry_removed_terms[:24]
            diagnostics["story_core_last_error_code"] = last_error_code
            diagnostics["story_core_validation_errors"] = last_errors
            package["diagnostics"] = diagnostics
            if attempt == 0:
                retry_used = True
                _append_diag_event(package, f"story_core strict validation failed, requesting one retry: {validation_feedback}", stage_id="story_core")
                continue
            break
        raise RuntimeError(f"{last_error_code}:{'; '.join(last_errors[:3])}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scenario_stage_pipeline] story_core failed")
        diagnostics = _safe_dict(package.get("diagnostics"))
        last_error_code = str(diagnostics.get("story_core_last_error_code") or "").strip()
        if not last_error_code:
            raw_error = str(exc or "").strip()
            if ":" in raw_error:
                last_error_code = raw_error.split(":", 1)[0].strip()
            else:
                last_error_code = raw_error
        if is_timeout_error(exc):
            diagnostics["story_core_timed_out"] = True
            diagnostics["story_core_last_error_code"] = "story_core_timeout"
            diagnostics["story_core_response_was_empty_after_timeout"] = not bool(
                str(diagnostics.get("story_core_raw_response") or "").strip()
            )
            last_error_code = "story_core_timeout"
        diagnostics["story_core_last_error_code"] = last_error_code
        diagnostics["story_core_failed_payload_rejected"] = True
        had_previous_payload = bool(previous_story_core_payload)
        diagnostics["story_core_previous_payload_was_cleared"] = had_previous_payload
        if had_previous_payload:
            diagnostics["story_core_previous_stale_payload"] = previous_story_core_payload
        package["story_core"] = {}
        package["story_core_v1"] = {}
        package["story_core_stale"] = True
        diagnostics["story_core_used_fallback"] = False
        diagnostics["story_core_hard_fail"] = True
        warnings = _safe_list(diagnostics.get("warnings"))
        warnings.append({"stage_id": "story_core", "message": f"hard_fail:{exc}"})
        diagnostics["warnings"] = warnings[-80:]
        package["diagnostics"] = diagnostics
        hard_fail_error_code = last_error_code or "story_core_hard_fail"
        raise RuntimeError(hard_fail_error_code) from exc


def _run_input_package_stage(package: dict[str, Any]) -> dict[str, Any]:
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    package["refs_inventory"] = refs_inventory
    input_pkg = _normalize_input_audio_source(_safe_dict(package.get("input")), refs_inventory)
    input_pkg["creative_config"] = _normalize_creative_config(input_pkg.get("creative_config"))
    package["input"] = input_pkg
    diagnostics = _safe_dict(package.get("diagnostics"))
    previous_signature = str(diagnostics.get("scenario_input_signature") or "")
    signatures = _compute_input_signatures(package)
    current_signature = str(signatures.get("scenario_input_signature") or "")
    if previous_signature and current_signature and previous_signature != current_signature:
        package = _clear_downstream_stage_outputs(package, "input_package", "input_signature_changed")
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics["input_signature_changed"] = True
        diagnostics["downstream_cleared_due_to_input_change"] = True
    else:
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics["input_signature_changed"] = False
        diagnostics["downstream_cleared_due_to_input_change"] = False
    diagnostics["input_creative_config_active"] = _safe_dict(input_pkg.get("creative_config"))
    diagnostics.update(signatures)
    package["diagnostics"] = diagnostics
    _append_diag_event(package, "input_package normalized", stage_id="input_package")
    return package


def _coerce_duration_sec(value: Any) -> float:
    try:
        if hasattr(value, "item") and callable(getattr(value, "item")):
            value = value.item()
        elif isinstance(value, (list, tuple)) and value:
            value = value[0]
        elif hasattr(value, "__len__") and hasattr(value, "__getitem__") and not isinstance(value, (str, bytes)):
            if len(value) == 1:
                value = value[0]
        duration = float(value)
    except Exception:
        duration = 0.0
    if duration < 0.0:
        return 0.0
    return duration


def _clamp_time(value: float, duration_sec: float) -> float:
    if duration_sec <= 0:
        return max(0.0, float(value))
    return max(0.0, min(float(value), duration_sec))


def _segment_count_for_duration(duration_sec: float) -> int:
    if duration_sec <= 30:
        return 3
    if duration_sec <= 70:
        return 4
    if duration_sec <= 130:
        return 5
    return 6


def _split_duration_evenly(duration_sec: float, segments: int) -> list[tuple[float, float]]:
    safe_segments = max(1, int(segments or 1))
    if duration_sec <= 0:
        return [(0.0, 0.0)]
    result: list[tuple[float, float]] = []
    for idx in range(safe_segments):
        t0 = (duration_sec * idx) / safe_segments
        t1 = (duration_sec * (idx + 1)) / safe_segments
        result.append((round(t0, 3), round(t1, 3)))
    return result


def _labels_for_segment_count(segment_count: int) -> list[str]:
    if segment_count <= 3:
        return ["intro", "turn", "release"]
    if segment_count == 4:
        return ["intro", "rise", "turn", "release"]
    if segment_count == 5:
        return ["intro", "rise", "turn", "release", "outro"]
    return ["intro", "rise", "turn", "release", "afterglow", "outro"]


def _energy_curve_for_count(segment_count: int) -> list[str]:
    if segment_count <= 3:
        return ["low", "high", "medium"]
    if segment_count == 4:
        return ["low", "medium", "high", "medium"]
    if segment_count == 5:
        return ["low", "medium", "high", "medium", "low"]
    return ["low", "medium", "high", "high", "medium", "low"]


def _mood_curve_from_story_core(story_core: dict[str, Any], segment_count: int) -> list[str]:
    arc = str(story_core.get("global_arc") or "").strip().lower()
    if "setup" in arc and "afterimage" in arc:
        base = ["anticipation", "momentum", "tension", "release", "afterimage"]
        if segment_count <= len(base):
            return base[:segment_count]
        return base + ["calm"] * (segment_count - len(base))
    if segment_count <= 3:
        return ["calm", "intense", "resolved"]
    if segment_count == 4:
        return ["calm", "building", "intense", "resolved"]
    if segment_count == 5:
        return ["calm", "building", "intense", "resolved", "afterglow"]
    return ["calm", "building", "intense", "urgent", "resolved", "afterglow"]


def _build_audio_map_from_duration(
    duration_sec: float,
    story_core: dict[str, Any],
    *,
    analysis_mode: str,
    content_type: str = "",
) -> dict[str, Any]:
    duration = _coerce_duration_sec(duration_sec)
    segment_count = _segment_count_for_duration(duration)
    windows = _split_duration_evenly(duration, segment_count)
    labels = _labels_for_segment_count(segment_count)
    energies = _energy_curve_for_count(segment_count)
    moods = _mood_curve_from_story_core(story_core, segment_count)

    sections: list[dict[str, Any]] = []
    for idx, (t0, t1) in enumerate(windows):
        sections.append(
            {
                "id": f"sec_{idx + 1}",
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "label": labels[idx] if idx < len(labels) else f"part_{idx + 1}",
                "energy": energies[idx] if idx < len(energies) else "medium",
                "mood": moods[idx] if idx < len(moods) else "neutral",
            }
        )

    # phrase endpoints: keep section boundaries; for music_video fallback do not synthesize pseudo-cadence grid.
    is_music_video = str(content_type or "").strip().lower() == "music_video"
    phrase_step = 4.0 if duration <= 90 else 6.0
    phrase_points: list[float] = []
    if duration > 0 and not is_music_video:
        cursor = phrase_step
        while cursor < max(0.0, duration - 0.001):
            phrase_points.append(round(cursor, 3))
            cursor += phrase_step
    for sec in sections:
        boundary = _clamp_time(float(sec.get("t1") or 0.0), duration)
        if 0.0 < boundary < duration:
            phrase_points.append(round(boundary, 3))
    phrase_endpoints = sorted(set(phrase_points))

    no_split_ranges: list[dict[str, Any]] = []
    if duration > 0:
        no_split_ranges.append({"t0": 0.0, "t1": round(min(1.5, duration), 3), "reason": "intro_protection"})
        tail_start = round(max(0.0, duration - 1.5), 3)
        if tail_start < duration:
            no_split_ranges.append({"t0": tail_start, "t1": round(duration, 3), "reason": "outro_tail_protection"})
    for sec in sections:
        t0 = float(sec.get("t0") or 0.0)
        t1 = float(sec.get("t1") or 0.0)
        if (t1 - t0) >= 5.0:
            pivot = round(t0 + (t1 - t0) * 0.5, 3)
            no_split_ranges.append(
                {"t0": round(max(t0, pivot - 0.35), 3), "t1": round(min(t1, pivot + 0.35), 3), "reason": "phrase_core_window"}
            )

    candidate_cut_points: list[float] = []
    for point in phrase_endpoints:
        if point <= 0.0 or point >= duration:
            continue
        inside_blocked = any(float(r.get("t0") or 0.0) <= point <= float(r.get("t1") or 0.0) for r in no_split_ranges)
        if not inside_blocked:
            candidate_cut_points.append(point)

    mood_progression = [
        {
            "t0": float(section.get("t0") or 0.0),
            "t1": float(section.get("t1") or 0.0),
            "mood": str(section.get("mood") or "neutral"),
            "energy": str(section.get("energy") or "medium"),
        }
        for section in sections
    ]
    energy_counts = {"low": 0, "medium": 0, "high": 0}
    for section in sections:
        energy = str(section.get("energy") or "medium").lower()
        if energy in energy_counts:
            energy_counts[energy] += 1
    dominant_energy = max(energy_counts.items(), key=lambda item: item[1])[0] if sections else "medium"

    lip_sync_ranges: list[dict[str, float]] = []
    content_hint = str(story_core.get("story_summary") or "").lower()
    if "speak" in content_hint or "sing" in content_hint or "vocal" in content_hint:
        for section in sections:
            if str(section.get("energy") or "").lower() in {"medium", "high"}:
                lip_sync_ranges.append(
                    {
                        "t0": round(float(section.get("t0") or 0.0), 3),
                        "t1": round(float(section.get("t1") or 0.0), 3),
                    }
                )

    arc_short = str(story_core.get("global_arc") or "").strip() or "unknown_arc"
    phrase_units: list[dict[str, Any]] = []
    prev_t = 0.0
    for idx, t1 in enumerate(phrase_endpoints, start=1):
        current_t1 = _clamp_time(float(t1), duration)
        if current_t1 <= prev_t:
            continue
        phrase_units.append(
            {
                "id": f"ph_{idx}",
                "t0": round(prev_t, 3),
                "t1": round(current_t1, 3),
                "duration_sec": round(current_t1 - prev_t, 3),
                "text": "",
                "word_count": 0,
                "semantic_weight": "medium",
            }
        )
        prev_t = current_t1
    if duration > prev_t:
        phrase_units.append(
            {
                "id": f"ph_{len(phrase_units) + 1}",
                "t0": round(prev_t, 3),
                "t1": round(duration, 3),
                "duration_sec": round(max(0.0, duration - prev_t), 3),
                "text": "",
                "word_count": 0,
                "semantic_weight": "medium",
            }
        )
    scene_windows = _build_scene_candidate_windows(phrase_units, duration) if phrase_units else []

    return {
        "duration_sec": round(duration, 3),
        "analysis_mode": analysis_mode,
        "sections": sections,
        "phrase_endpoints_sec": phrase_endpoints,
        "phrase_units": phrase_units,
        "scene_candidate_windows": scene_windows,
        "no_split_ranges": no_split_ranges,
        "candidate_cut_points_sec": sorted(set(candidate_cut_points)),
        "pacing_profile": {
            "segment_count": len(sections),
            "phrase_step_sec": None if is_music_video else phrase_step,
            "dominant_energy": dominant_energy,
        },
        "mood_progression": mood_progression,
        "audio_arc_summary": f"Audio map follows story_core arc '{arc_short}' with {len(sections)} timing sections.",
        "section_summary": [f"{sec.get('label')}:{sec.get('energy')}/{sec.get('mood')}" for sec in sections],
        "lip_sync_candidate_ranges": lip_sync_ranges,
    }


def _resolve_audio_analysis_path(audio_url: str) -> tuple[str, str | None]:
    raw = str(audio_url or "").strip()
    if raw:
        try:
            direct_path = Path(raw).expanduser().resolve()
            if direct_path.exists() and direct_path.is_file():
                return str(direct_path), None
        except Exception:
            pass

    local_path, _ = _resolve_local_audio_asset_path(audio_url=audio_url)
    if local_path:
        return local_path, None

    resolved = _resolve_reference_url(audio_url)
    if not resolved:
        return "", None

    parsed = urllib.parse.urlparse(resolved)
    suffix = Path(parsed.path or "").suffix or ".audio"
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    tmp_path = tmp.name
    tmp.close()
    req = urllib.request.Request(resolved, headers={"User-Agent": "photostudio-audio-map/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp, open(tmp_path, "wb") as fh:
            fh.write(resp.read())
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return "", None
    return tmp_path, tmp_path


def _collect_audio_resolution_candidates(
    audio_url: str,
    input_payload: dict[str, Any] | None = None,
    refs_inventory: dict[str, Any] | None = None,
) -> list[str]:
    input_pkg = _safe_dict(input_payload)
    refs = _safe_dict(refs_inventory)
    audio_in = _safe_dict(refs.get("audio_in"))
    audio_in_meta = _safe_dict(audio_in.get("meta"))
    source = _safe_dict(input_pkg.get("source"))
    connected = _safe_dict(input_pkg.get("connected_context_summary"))
    connected_audio = _safe_dict(connected.get("audio_in"))

    raw_candidates = [
        audio_url,
        source.get("source_value"),
        source.get("sourceValue"),
        input_pkg.get("source_value"),
        input_pkg.get("sourceValue"),
        audio_in.get("value"),
        audio_in_meta.get("url"),
        audio_in_meta.get("fileName"),
        connected.get("audio_url"),
        connected.get("audioUrl"),
        connected.get("source_value"),
        connected.get("sourceValue"),
        connected.get("fileName"),
        connected_audio.get("value"),
        _safe_dict(connected_audio.get("meta")).get("url"),
        _safe_dict(connected_audio.get("meta")).get("fileName"),
    ]
    seen: set[str] = set()
    values: list[str] = []
    for item in raw_candidates:
        value = str(item or "").strip()
        if value and value not in seen:
            seen.add(value)
            values.append(value)
    return values


def _extract_asset_rel_path_from_audio_candidate(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    source = parsed.path or raw
    decoded = urllib.parse.unquote(str(source).strip())
    normalized = decoded.replace("\\", "/").lstrip("/")
    marker = "static/assets/"
    lower = normalized.lower()
    idx = lower.find(marker)
    if idx >= 0:
        return normalized[idx + len(marker) :].strip("/")
    if normalized.lower().startswith("assets/"):
        return normalized[len("assets/") :].strip("/")
    return ""


def _resolve_local_audio_asset_path(
    *,
    audio_url: str,
    input_payload: dict[str, Any] | None = None,
    refs_inventory: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    assets_root = Path(ASSETS_DIR).resolve()
    candidates = _collect_audio_resolution_candidates(audio_url, input_payload, refs_inventory)
    basename_candidates: list[str] = []
    selected_rel_path = ""

    debug: dict[str, Any] = {
        "audio_url": str(audio_url or ""),
        "extracted_asset_relative_path": "",
        "final_local_path": "",
        "exists": False,
        "fallback_to_basename_lookup": False,
    }

    for candidate in candidates:
        rel_path = _extract_asset_rel_path_from_audio_candidate(candidate)
        parsed = urllib.parse.urlparse(candidate)
        basename = Path(urllib.parse.unquote(parsed.path or candidate)).name
        if basename and basename not in basename_candidates:
            basename_candidates.append(basename)
        if not rel_path:
            continue
        if not selected_rel_path:
            selected_rel_path = rel_path
        try:
            file_path = (assets_root / rel_path).resolve()
            if assets_root not in file_path.parents:
                continue
            if file_path.exists() and file_path.is_file():
                debug["extracted_asset_relative_path"] = rel_path
                debug["final_local_path"] = str(file_path)
                debug["exists"] = True
                return str(file_path), debug
        except Exception:
            continue

    debug["extracted_asset_relative_path"] = selected_rel_path
    debug["fallback_to_basename_lookup"] = bool(basename_candidates)
    for basename in basename_candidates:
        try:
            candidate_path = (assets_root / basename).resolve()
            if assets_root not in candidate_path.parents:
                continue
            if candidate_path.exists() and candidate_path.is_file():
                debug["final_local_path"] = str(candidate_path)
                debug["exists"] = True
                return str(candidate_path), debug
        except Exception:
            continue

    return "", debug


def _float_points(values: Any, duration_sec: float, *, min_t: float = 0.0, max_t: float | None = None) -> list[float]:
    max_bound = duration_sec if max_t is None else max_t
    points: list[float] = []
    for value in _safe_list(values):
        try:
            t = float(value)
        except Exception:
            continue
        if t < min_t or t > max_bound:
            continue
        points.append(round(t, 3))
    points.sort()
    dedup: list[float] = []
    for point in points:
        if not dedup or abs(point - dedup[-1]) >= 0.18:
            dedup.append(point)
    return dedup


def _extract_audio_transcript_text(input_pkg: dict[str, Any]) -> str:
    candidates = (
        input_pkg.get("transcript_text"),
        input_pkg.get("transcriptText"),
        input_pkg.get("transcript"),
        input_pkg.get("lyrics_text"),
        input_pkg.get("lyricsText"),
        input_pkg.get("lyrics"),
        input_pkg.get("spoken_text_hint"),
        input_pkg.get("spokenTextHint"),
    )
    for item in candidates:
        text = str(item or "").strip()
        if text:
            return text
    return ""


def _words_from_text(text: str) -> list[str]:
    return [token for token in re.findall(r"[^\s]+", str(text or "").strip()) if token]


def _is_instrumental_tail_marker_text(text: Any) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    normalized = re.sub(r"[\[\]\(\)\{\}_\-:;,.!?]", " ", raw.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    marker_tokens = set(normalized.split())
    instrumental_tokens = {
        "instrumental",
        "interlude",
        "outro",
        "tail",
        "residual",
        "residue",
        "ending",
        "fade",
        "music",
        "nonlexical",
        "non",
        "lexical",
        "vocal",
        "vocals",
        "no",
    }
    if not marker_tokens.issubset(instrumental_tokens):
        return False
    return bool(marker_tokens.intersection({"instrumental", "interlude", "outro", "tail", "residual", "residue", "fade"}))


def _estimate_word_count_from_text(text: Any) -> int:
    if _is_instrumental_tail_marker_text(text):
        return 0
    return len(_words_from_text(str(text or "")))


def _phrase_word_count(phrase: dict[str, Any]) -> int:
    direct_count = int(max(0, _to_float(phrase.get("word_count"), -1.0)))
    if direct_count > 0:
        return direct_count
    return _estimate_word_count_from_text(phrase.get("text"))


def _semantic_weight_for_phrase(text: str, word_count: int, *, is_last: bool) -> str:
    clean = str(text or "").strip()
    if is_last or any(mark in clean for mark in ("!", "?", "…", "...", "—")):
        return "high"
    if word_count >= 9 or "," in clean or ";" in clean or ":" in clean:
        return "medium"
    return "low"


def _build_phrase_units_from_alignment(
    analysis: dict[str, Any],
    duration_sec: float,
    transcript_text: str,
) -> list[dict[str, Any]]:
    return _build_phrase_units_from_approximate_alignment(analysis, duration_sec, transcript_text)


def _build_phrase_units_from_approximate_alignment(
    analysis: dict[str, Any],
    duration_sec: float,
    transcript_text: str,
) -> list[dict[str, Any]]:
    vocal_phrases = [item for item in _safe_list(analysis.get("vocalPhrases")) if isinstance(item, dict)]
    if not vocal_phrases:
        return []
    words = _words_from_text(transcript_text)
    if not words:
        return []

    ordered = sorted(vocal_phrases, key=lambda item: float(item.get("start") or 0.0))
    spans: list[tuple[float, float]] = []
    for phrase in ordered:
        t0 = _clamp_time(float(phrase.get("start") or 0.0), duration_sec)
        t1 = _clamp_time(float(phrase.get("end") or 0.0), duration_sec)
        if t1 - t0 < 0.2:
            continue
        spans.append((t0, t1))
    if not spans:
        return []

    durations = [max(0.2, t1 - t0) for t0, t1 in spans]
    total_duration = max(0.001, sum(durations))
    remaining = len(words)
    allocations: list[int] = []
    for idx, span_duration in enumerate(durations):
        if idx == len(durations) - 1:
            count = remaining
        else:
            ratio = span_duration / total_duration
            count = max(1, int(round(len(words) * ratio)))
            remaining_after = len(durations) - idx - 1
            count = min(count, max(1, remaining - remaining_after))
        allocations.append(count)
        remaining -= count

    phrase_units: list[dict[str, Any]] = []
    cursor = 0
    for idx, ((t0, t1), count) in enumerate(zip(spans, allocations, strict=False), start=1):
        phrase_words = words[cursor : cursor + count]
        cursor += count
        if not phrase_words:
            continue
        text = " ".join(phrase_words).strip()
        phrase_units.append(
            {
                "id": f"ph_{idx}",
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "text": text,
                "word_count": len(phrase_words),
                "can_cut_after": True,
                "semantic_weight": _semantic_weight_for_phrase(text, len(phrase_words), is_last=idx == len(spans)),
                "words": [],
                "word_timestamps": [],
            }
        )
    return phrase_units


def _extract_input_alignment_payload(input_pkg: dict[str, Any]) -> dict[str, Any]:
    alignment = _safe_dict(input_pkg.get("transcript_alignment"))
    if alignment:
        return alignment
    alignment = _safe_dict(input_pkg.get("transcriptAlignment"))
    if alignment:
        return alignment
    words = _safe_list(input_pkg.get("word_timestamps") or input_pkg.get("wordTimestamps"))
    phrases = _safe_list(input_pkg.get("phrase_timestamps") or input_pkg.get("phraseTimestamps"))
    if words:
        return {
            "transcript_text": str(input_pkg.get("transcript_text") or input_pkg.get("transcriptText") or "").strip(),
            "words": words,
            "phrases": phrases,
            "source": "input_payload_word_timestamps",
        }
    return {}


def _build_phrase_units_from_real_alignment(
    alignment: dict[str, Any],
    duration_sec: float,
) -> list[dict[str, Any]]:
    def _append_phrase_chunk(words_chunk: list[dict[str, Any]], preferred_text: str, is_last: bool) -> None:
        if not words_chunk:
            return
        resolved_t0 = float(words_chunk[0].get("t0") or 0.0)
        resolved_t1 = float(words_chunk[-1].get("t1") or resolved_t0)
        text = preferred_text or " ".join(str(word.get("text") or "") for word in words_chunk).strip()
        phrase_units.append(
            {
                "id": f"ph_{len(phrase_units) + 1}",
                "t0": round(resolved_t0, 3),
                "t1": round(resolved_t1, 3),
                "text": text,
                "word_count": len(words_chunk),
                "words": words_chunk,
                "word_timestamps": words_chunk,
                "can_cut_after": True,
                "semantic_weight": _semantic_weight_for_phrase(text, len(words_chunk), is_last=is_last),
            }
        )

    words = [
        item
        for item in _safe_list(alignment.get("words"))
        if isinstance(item, dict) and float(item.get("t1") or 0.0) > float(item.get("t0") or 0.0)
    ]
    if not words:
        return []

    phrases_raw = [
        item
        for item in _safe_list(alignment.get("phrases"))
        if isinstance(item, dict) and float(item.get("t1") or 0.0) > float(item.get("t0") or 0.0)
    ]
    if not phrases_raw:
        min_t0 = float(words[0].get("t0") or 0.0)
        max_t1 = float(words[-1].get("t1") or min_t0)
        phrases_raw = [{"text": str(alignment.get("transcript_text") or "").strip(), "t0": min_t0, "t1": max_t1}]

    phrase_units: list[dict[str, Any]] = []
    word_idx = 0
    for phrase_idx, phrase in enumerate(phrases_raw, start=1):
        t0 = _clamp_time(float(phrase.get("t0") or 0.0), duration_sec)
        t1 = _clamp_time(float(phrase.get("t1") or 0.0), duration_sec)
        if t1 - t0 < 0.12:
            continue
        phrase_words: list[dict[str, Any]] = []
        while word_idx < len(words):
            word = words[word_idx]
            w0 = float(word.get("t0") or 0.0)
            w1 = float(word.get("t1") or 0.0)
            if w1 <= t0:
                word_idx += 1
                continue
            if w0 >= t1:
                break
            phrase_words.append(
                {
                    "text": str(word.get("text") or "").strip(),
                    "t0": round(_clamp_time(w0, duration_sec), 3),
                    "t1": round(_clamp_time(w1, duration_sec), 3),
                }
            )
            word_idx += 1
        if not phrase_words:
            continue
        phrase_text = str(phrase.get("text") or "").strip() or " ".join(str(word.get("text") or "") for word in phrase_words).strip()
        phrase_span = float(phrase_words[-1].get("t1") or 0.0) - float(phrase_words[0].get("t0") or 0.0)
        if phrase_span <= 8.0 or len(phrase_words) <= 3:
            _append_phrase_chunk(phrase_words, phrase_text, is_last=phrase_idx == len(phrases_raw))
            continue
        # Aggressive split for oversized phrase: cut only at real word boundaries.
        chunk: list[dict[str, Any]] = []
        for w_idx, word in enumerate(phrase_words):
            chunk.append(word)
            chunk_span = float(chunk[-1].get("t1") or 0.0) - float(chunk[0].get("t0") or 0.0)
            is_last_word = w_idx == len(phrase_words) - 1
            if chunk_span >= 6.0 or (chunk_span >= 3.0 and is_last_word):
                _append_phrase_chunk(chunk, "", is_last=is_last_word and phrase_idx == len(phrases_raw))
                chunk = []
        if chunk:
            _append_phrase_chunk(chunk, "", is_last=phrase_idx == len(phrases_raw))

    return phrase_units


def _build_phrase_units_from_music_dynamics(
    duration_sec: float,
    analysis: dict[str, Any],
) -> list[dict[str, Any]]:
    duration = _coerce_duration_sec(duration_sec)
    if duration <= 0:
        return []

    pause_points = _float_points(analysis.get("pausePoints"), duration, min_t=0.2, max_t=max(0.0, duration - 0.2))
    phrase_points = _float_points(analysis.get("phraseBoundaries"), duration, min_t=0.2, max_t=max(0.0, duration - 0.2))
    section_edges = []
    for section in _safe_list(analysis.get("sections")):
        if not isinstance(section, dict):
            continue
        end_point = _coerce_duration_sec(section.get("end"))
        if 0.2 <= end_point <= max(0.0, duration - 0.2):
            section_edges.append(round(end_point, 3))
    section_points = _float_points(section_edges, duration, min_t=0.2, max_t=max(0.0, duration - 0.2))
    energy_peaks = _float_points(analysis.get("energyPeaks"), duration, min_t=0.2, max_t=max(0.0, duration - 0.2))

    score_by_point: dict[float, float] = {}
    for point in pause_points:
        score_by_point[point] = max(score_by_point.get(point, 0.0), 1.0)
    for point in phrase_points:
        score_by_point[point] = max(score_by_point.get(point, 0.0), 0.9)
    for point in section_points:
        score_by_point[point] = max(score_by_point.get(point, 0.0), 0.82)
    for point in energy_peaks:
        score_by_point[point] = max(score_by_point.get(point, 0.0), 0.66)

    boundaries: list[float] = [0.0]
    min_phrase = 2.0
    pref_min = 3.0
    pref_max = 6.0
    hard_max = 8.0
    prev_span = 0.0
    phrase_idx = 0
    candidates = sorted(score_by_point.keys())

    while boundaries[-1] < duration - 0.12:
        start = boundaries[-1]
        best_t = None
        best_score = -1e9
        for point in candidates:
            if point <= start + min_phrase:
                continue
            span = point - start
            if span > hard_max:
                break
            score = score_by_point.get(point, 0.0)
            if pref_min <= span <= pref_max:
                score += 0.75
            score -= abs(span - 4.4) * 0.22
            if prev_span > 0 and abs(span - prev_span) <= 0.12:
                score -= 0.65
            if abs(span - 4.0) <= 0.05:
                score -= 0.2
            if score > best_score:
                best_score = score
                best_t = point

        if best_t is None:
            remaining = duration - start
            if remaining <= hard_max:
                best_t = duration
            else:
                best_t = _clamp_time(start + pref_max, duration)

        if duration - best_t < 1.8:
            best_t = duration
        if best_t <= start + 0.15:
            break
        prev_span = max(0.0, best_t - start)
        boundaries.append(round(best_t, 3))
        phrase_idx += 1
        if boundaries[-1] >= duration - 0.001:
            boundaries[-1] = round(duration, 3)
            break

    if boundaries[-1] < duration:
        boundaries.append(round(duration, 3))

    phrase_units: list[dict[str, Any]] = []
    for idx in range(1, len(boundaries)):
        t0 = boundaries[idx - 1]
        t1 = boundaries[idx]
        span = t1 - t0
        if span <= 0.15:
            continue
        boundary_strength = score_by_point.get(round(t1, 3), 0.0)
        semantic_weight = "high" if boundary_strength >= 0.9 else "medium" if boundary_strength >= 0.7 else "low"
        phrase_units.append(
            {
                "id": f"ph_{len(phrase_units) + 1}",
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "duration_sec": round(span, 3),
                "text": "",
                "word_count": 0,
                "semantic_weight": semantic_weight,
            }
        )
    return phrase_units


def _build_scene_candidate_windows(
    phrase_units: list[dict[str, Any]],
    duration_sec: float,
    *,
    target_min: float = 3.0,
    target_max: float = 6.0,
    hard_max: float = 8.0,
) -> list[dict[str, Any]]:
    if not phrase_units:
        return []
    windows: list[dict[str, Any]] = []

    def _append_window(start_idx: int, end_idx: int) -> None:
        win_start = _clamp_time(float(phrase_units[start_idx].get("t0") or 0.0), duration_sec)
        win_end = _clamp_time(float(phrase_units[end_idx].get("t1") or win_start), duration_sec)
        windows.append(
            {
                "id": f"sc_{len(windows) + 1}",
                "t0": round(win_start, 3),
                "t1": round(win_end, 3),
                "duration_sec": round(max(0.0, win_end - win_start), 3),
                "phrase_ids": [str(phrase_units[k].get("id") or "") for k in range(start_idx, end_idx + 1)],
                "cut_after_phrase_id": str(phrase_units[end_idx].get("id") or ""),
            }
        )

    idx = 0
    prev_window_duration = 0.0
    while idx < len(phrase_units):
        start = float(phrase_units[idx].get("t0") or 0.0)
        best_j = idx
        best_score = -1e9
        for j in range(idx, len(phrase_units)):
            end = float(phrase_units[j].get("t1") or start)
            span = end - start
            if span <= 0:
                continue
            if span > hard_max and j > idx:
                break
            center_target = (target_min + target_max) * 0.5
            closeness = -abs(span - center_target)
            in_target_bonus = 1.2 if target_min <= span <= target_max else 0.0
            near_hard_cap_penalty = -0.8 if span > target_max else 0.0
            semantic_bonus = 0.35 if str(phrase_units[j].get("semantic_weight") or "") == "high" else 0.0
            equal_prev_penalty = -0.8 if prev_window_duration > 0 and abs(span - prev_window_duration) <= 0.1 else 0.0
            near_four_run_penalty = (
                -0.45 if prev_window_duration > 0 and abs(prev_window_duration - 4.0) <= 0.1 and abs(span - 4.0) <= 0.1 else 0.0
            )
            score = closeness + in_target_bonus + near_hard_cap_penalty + semantic_bonus + equal_prev_penalty + near_four_run_penalty
            if span >= target_min and score > best_score:
                best_score = score
                best_j = j

        if best_j < idx:
            best_j = idx
        _append_window(idx, best_j)
        if windows:
            prev_window_duration = float(windows[-1].get("duration_sec") or 0.0)
        idx = best_j + 1

    # Aggressive post-split if any window is over hard cap: split only on phrase boundaries (never mid-word).
    normalized: list[dict[str, Any]] = []
    for win in windows:
        span = float(win.get("duration_sec") or 0.0)
        if span <= hard_max:
            normalized.append(win)
            continue
        phrase_ids = [str(item) for item in _safe_list(win.get("phrase_ids")) if str(item)]
        if len(phrase_ids) <= 1:
            normalized.append(win)
            continue
        current: list[str] = []
        current_t0 = float(win.get("t0") or 0.0)
        for phrase_id in phrase_ids:
            phrase = next((item for item in phrase_units if str(item.get("id") or "") == phrase_id), None)
            if not phrase:
                continue
            proposed = current + [phrase_id]
            proposed_t1 = float(phrase.get("t1") or current_t0)
            proposed_span = proposed_t1 - current_t0
            if current and proposed_span > hard_max:
                end_phrase = next((item for item in phrase_units if str(item.get("id") or "") == current[-1]), None)
                end_t1 = float((end_phrase or {}).get("t1") or current_t0)
                normalized.append(
                    {
                        "id": f"sc_{len(normalized) + 1}",
                        "t0": round(current_t0, 3),
                        "t1": round(end_t1, 3),
                        "duration_sec": round(max(0.0, end_t1 - current_t0), 3),
                        "phrase_ids": current,
                        "cut_after_phrase_id": str(current[-1]),
                    }
                )
                current = [phrase_id]
                current_t0 = float(phrase.get("t0") or current_t0)
            else:
                current = proposed
        if current:
            end_phrase = next((item for item in phrase_units if str(item.get("id") or "") == current[-1]), None)
            end_t1 = float((end_phrase or {}).get("t1") or current_t0)
            normalized.append(
                {
                    "id": f"sc_{len(normalized) + 1}",
                    "t0": round(current_t0, 3),
                    "t1": round(end_t1, 3),
                    "duration_sec": round(max(0.0, end_t1 - current_t0), 3),
                    "phrase_ids": current,
                    "cut_after_phrase_id": str(current[-1]),
                }
            )
    def _repair_short_final_tail(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(candidates) < 2:
            return candidates
        repaired = [dict(item) for item in candidates]
        last = repaired[-1]
        prev = repaired[-2]
        last_span = float(last.get("duration_sec") or 0.0)
        min_tail_sec = min(2.0, max(0.0, target_min - 0.1))
        if last_span >= min_tail_sec and last_span >= max(0.0, target_min - 0.8):
            return repaired
        prev_t0 = float(prev.get("t0") or 0.0)
        last_t1 = float(last.get("t1") or prev_t0)
        if last_t1 <= prev_t0:
            return repaired
        merged_phrase_ids = [
            *[str(item) for item in _safe_list(prev.get("phrase_ids")) if str(item)],
            *[str(item) for item in _safe_list(last.get("phrase_ids")) if str(item)],
        ]
        prev["t1"] = round(last_t1, 3)
        prev["duration_sec"] = round(max(0.0, last_t1 - prev_t0), 3)
        prev["phrase_ids"] = list(dict.fromkeys(merged_phrase_ids))
        prev["cut_after_phrase_id"] = str(last.get("cut_after_phrase_id") or prev.get("cut_after_phrase_id") or "")
        repaired = repaired[:-1]
        for idx, row in enumerate(repaired, start=1):
            row["id"] = f"sc_{idx}"
        return repaired

    if normalized:
        return _repair_short_final_tail(normalized)
    return _repair_short_final_tail(windows)


def _to_word_rows(alignment: dict[str, Any], duration_sec: float) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    for item in _safe_list(alignment.get("words")):
        if not isinstance(item, dict):
            continue
        t0 = _clamp_time(_to_float(item.get("t0"), 0.0), duration_sec)
        t1 = _clamp_time(_to_float(item.get("t1"), t0), duration_sec)
        if t1 - t0 < 0.01:
            continue
        rows.append({"t0": round(t0, 3), "t1": round(t1, 3)})
    return rows


def _align_cut_to_word_boundary(cut_t: float, word_rows: list[dict[str, float]], duration_sec: float) -> float:
    cut = _clamp_time(cut_t, duration_sec)
    for row in word_rows:
        w0 = float(row.get("t0") or 0.0)
        w1 = float(row.get("t1") or w0)
        if w0 < cut < w1:
            if abs(cut - w0) <= abs(w1 - cut):
                return round(w0, 3)
            return round(w1, 3)
    return round(cut, 3)


def _repair_scene_slot_boundaries(
    raw_slots: list[dict[str, Any]],
    *,
    duration_sec: float,
    word_rows: list[dict[str, float]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    repaired: list[dict[str, Any]] = []
    stats = {
        "dropped_near_zero": 0,
        "boundary_repaired": 0,
        "word_boundary_repaired": 0,
        "orphan_tail_merged": 0,
    }
    prev_t1 = 0.0
    for row in raw_slots:
        t0 = _clamp_time(_to_float(row.get("t0"), prev_t1), duration_sec)
        t1 = _clamp_time(_to_float(row.get("t1"), t0), duration_sec)
        if repaired:
            t0 = round(prev_t1, 3)
            if abs(t0 - _to_float(row.get("t0"), t0)) >= 0.001:
                stats["boundary_repaired"] += 1
        if word_rows:
            aligned_t1 = _align_cut_to_word_boundary(t1, word_rows, duration_sec)
            if abs(aligned_t1 - t1) >= 0.001:
                stats["word_boundary_repaired"] += 1
            t1 = aligned_t1
        if t1 <= t0:
            stats["dropped_near_zero"] += 1
            continue
        span = t1 - t0
        if span < 0.1:
            stats["dropped_near_zero"] += 1
            continue
        fixed = dict(row)
        fixed["t0"] = round(t0, 3)
        fixed["t1"] = round(t1, 3)
        fixed["duration_sec"] = round(span, 3)
        repaired.append(fixed)
        prev_t1 = float(fixed["t1"])
    if len(repaired) >= 2:
        tail_span = float(repaired[-1].get("duration_sec") or 0.0)
        if tail_span < 0.5:
            repaired[-2]["t1"] = round(float(repaired[-1].get("t1") or repaired[-2].get("t1") or 0.0), 3)
            repaired[-2]["duration_sec"] = round(
                max(0.0, float(repaired[-2].get("t1") or 0.0) - float(repaired[-2].get("t0") or 0.0)), 3
            )
            repaired[-2]["phrase_ids"] = list(
                dict.fromkeys(
                    [str(item) for item in _safe_list(repaired[-2].get("phrase_ids")) if str(item)]
                    + [str(item) for item in _safe_list(repaired[-1].get("phrase_ids")) if str(item)]
                )
            )
            repaired.pop()
            stats["orphan_tail_merged"] += 1
    for idx, slot in enumerate(repaired, start=1):
        slot["id"] = f"slot_{idx}"
        slot["duration_sec"] = round(max(0.0, float(slot.get("t1") or 0.0) - float(slot.get("t0") or 0.0)), 3)
    return repaired, stats


def _build_scene_slots(
    *,
    audio_map: dict[str, Any],
    analysis: dict[str, Any],
    duration_sec: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    phrase_units = [item for item in _safe_list(audio_map.get("phrase_units")) if isinstance(item, dict)]
    if not phrase_units:
        return [], {"audio_map_scene_slots_repair_stats": {}}
    phrase_index = {str(item.get("id") or ""): item for item in phrase_units}
    ordered_phrases = sorted(phrase_units, key=lambda item: (_to_float(item.get("t0"), 0.0), _to_float(item.get("t1"), 0.0)))
    seed_rows = [item for item in _safe_list(audio_map.get("scene_candidate_windows")) if isinstance(item, dict)] or phrase_units

    def _slot_phrase_overlap(slot_t0: float, slot_t1: float, phrase: dict[str, Any]) -> float:
        phrase_t0 = _to_float(phrase.get("t0"), 0.0)
        phrase_t1 = _to_float(phrase.get("t1"), phrase_t0)
        return max(0.0, min(slot_t1, phrase_t1) - max(slot_t0, phrase_t0))

    def _intersected_phrase_rows(slot_t0: float, slot_t1: float) -> list[dict[str, Any]]:
        slot_span = max(0.001, slot_t1 - slot_t0)
        intersected: list[dict[str, Any]] = []
        for phrase in ordered_phrases:
            phrase_t0 = _to_float(phrase.get("t0"), 0.0)
            phrase_t1 = _to_float(phrase.get("t1"), phrase_t0)
            phrase_span = max(0.001, phrase_t1 - phrase_t0)
            overlap = _slot_phrase_overlap(slot_t0, slot_t1, phrase)
            if overlap <= 0.0:
                continue
            if overlap >= 0.1 or overlap / slot_span >= 0.18 or overlap / phrase_span >= 0.18:
                intersected.append(phrase)
        return intersected

    def _nearest_phrase_row(slot_t0: float, slot_t1: float) -> dict[str, Any] | None:
        if not ordered_phrases:
            return None
        center = (slot_t0 + slot_t1) / 2.0
        nearest: dict[str, Any] | None = None
        nearest_dist = float("inf")
        for phrase in ordered_phrases:
            phrase_t0 = _to_float(phrase.get("t0"), 0.0)
            phrase_t1 = _to_float(phrase.get("t1"), phrase_t0)
            phrase_center = (phrase_t0 + phrase_t1) / 2.0
            dist = abs(phrase_center - center)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = phrase
        return nearest

    raw_slots: list[dict[str, Any]] = []
    for seed in seed_rows:
        phrase_ids = [str(item) for item in _safe_list(seed.get("phrase_ids")) if str(item)]
        if not phrase_ids and str(seed.get("id") or "").startswith("ph_"):
            phrase_ids = [str(seed.get("id") or "")]
        resolved_phrases = [phrase_index[item] for item in phrase_ids if item in phrase_index]
        t0 = _to_float(seed.get("t0"), 0.0)
        t1 = _to_float(seed.get("t1"), t0)
        if resolved_phrases:
            t0 = _to_float(resolved_phrases[0].get("t0"), t0)
            t1 = _to_float(resolved_phrases[-1].get("t1"), t1)
        primary_text = ""
        for phrase in resolved_phrases:
            text = str(phrase.get("text") or "").strip()
            if text:
                primary_text = text[:280]
                break
        raw_slots.append(
            {
                "id": str(seed.get("id") or ""),
                "t0": round(_clamp_time(t0, duration_sec), 3),
                "t1": round(_clamp_time(t1, duration_sec), 3),
                "duration_sec": round(max(0.0, t1 - t0), 3),
                "phrase_ids": phrase_ids,
                "primary_phrase_text": primary_text,
            }
        )

    word_rows = _to_word_rows(_safe_dict(audio_map.get("transcript_alignment")), duration_sec)
    slots, repair_stats = _repair_scene_slot_boundaries(raw_slots, duration_sec=duration_sec, word_rows=word_rows)
    if not slots:
        return [], {"audio_map_scene_slots_repair_stats": repair_stats}
    def _hydrate_slot_phrase_data(slot: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], int, str, bool]:
        t0 = float(slot.get("t0") or 0.0)
        t1 = float(slot.get("t1") or t0)
        existing_ids = [str(item) for item in _safe_list(slot.get("phrase_ids")) if str(item)]
        slot_phrases_by_id = [phrase_index[item] for item in existing_ids if item in phrase_index]
        slot_phrase_rows = slot_phrases_by_id + _intersected_phrase_rows(t0, t1)
        if not slot_phrase_rows:
            nearest_phrase = _nearest_phrase_row(t0, t1)
            if nearest_phrase is not None:
                slot_phrase_rows = [nearest_phrase]

        deduped_phrases: list[dict[str, Any]] = []
        seen_phrase_ids: set[str] = set()
        for phrase in sorted(
            slot_phrase_rows,
            key=lambda item: (_to_float(item.get("t0"), 0.0), _to_float(item.get("t1"), 0.0), str(item.get("id") or "")),
        ):
            phrase_id = str(phrase.get("id") or "")
            if not phrase_id or phrase_id in seen_phrase_ids:
                continue
            deduped_phrases.append(phrase)
            seen_phrase_ids.add(phrase_id)

        slot_phrases = deduped_phrases
        phrase_ids = [str(item.get("id") or "") for item in slot_phrases if str(item.get("id") or "")]
        word_count = sum(_phrase_word_count(phrase) for phrase in slot_phrases)
        primary_phrase_text = ""
        for phrase in slot_phrases:
            text = str(phrase.get("text") or "").strip()
            if text:
                primary_phrase_text = text[:280]
                break
        is_instrumental_tail_slot = bool(slot_phrases) and all(
            _is_instrumental_tail_marker_text(str(phrase.get("text") or "")) for phrase in slot_phrases
        )
        return slot_phrases, phrase_ids, word_count, primary_phrase_text, is_instrumental_tail_slot

    for slot in slots:
        slot_phrases, phrase_ids, word_count, primary_phrase_text, is_instrumental_tail_slot = _hydrate_slot_phrase_data(slot)
        slot["_hydrated_phrases"] = slot_phrases
        slot["phrase_ids"] = phrase_ids
        slot["phrase_count"] = len(slot_phrases)
        slot["word_count"] = word_count
        slot["primary_phrase_text"] = primary_phrase_text
        slot["_is_instrumental_tail_slot"] = is_instrumental_tail_slot

    if len(slots) >= 2:
        last = slots[-1]
        prev = slots[-2]
        last_t0 = float(last.get("t0") or 0.0)
        last_t1 = float(last.get("t1") or last_t0)
        last_span = max(0.0, last_t1 - last_t0)
        marker_text_only = bool(last.get("_hydrated_phrases")) and bool(last.get("_is_instrumental_tail_slot"))
        tail_has_no_words = int(last.get("word_count") or 0) == 0
        if 0.0 < last_span <= 1.2 and marker_text_only and tail_has_no_words:
            prev_t0 = float(prev.get("t0") or 0.0)
            prev["t1"] = round(last_t1, 3)
            prev["duration_sec"] = round(max(0.0, last_t1 - prev_t0), 3)
            prev["phrase_ids"] = list(
                dict.fromkeys(
                    [str(item) for item in _safe_list(prev.get("phrase_ids")) if str(item)]
                    + [str(item) for item in _safe_list(last.get("phrase_ids")) if str(item)]
                )
            )
            slots.pop()
            repair_stats["orphan_tail_merged"] = int(repair_stats.get("orphan_tail_merged", 0)) + 1
            for slot_idx, row in enumerate(slots, start=1):
                row["id"] = f"slot_{slot_idx}"
            for slot in slots:
                slot_phrases, phrase_ids, word_count, primary_phrase_text, is_instrumental_tail_slot = _hydrate_slot_phrase_data(slot)
                slot["_hydrated_phrases"] = slot_phrases
                slot["phrase_ids"] = phrase_ids
                slot["phrase_count"] = len(slot_phrases)
                slot["word_count"] = word_count
                slot["primary_phrase_text"] = primary_phrase_text
                slot["_is_instrumental_tail_slot"] = is_instrumental_tail_slot

    beats = _float_points(analysis.get("beats"), duration_sec)
    energy_peaks = _float_points(analysis.get("energyPeaks"), duration_sec)
    vocal_phrases = [item for item in _safe_list(analysis.get("vocalPhrases")) if isinstance(item, dict)]
    alignment = _safe_dict(audio_map.get("transcript_alignment"))
    transcript_available = bool(str(alignment.get("transcript_text") or "").strip() or _safe_list(alignment.get("words")))
    diagnostics_notes: list[str] = []
    if not beats:
        diagnostics_notes.append("beat_alignment_score is approximate: beats unavailable")
    if not vocal_phrases:
        diagnostics_notes.append("vocal_ratio is approximate: vocal phrases unavailable")

    for idx, slot in enumerate(slots):
        t0 = float(slot.get("t0") or 0.0)
        t1 = float(slot.get("t1") or t0)
        span = max(0.001, t1 - t0)
        slot_phrases = [item for item in _safe_list(slot.get("_hydrated_phrases")) if isinstance(item, dict)]
        phrase_ids = [str(item) for item in _safe_list(slot.get("phrase_ids")) if str(item)]
        word_count = int(slot.get("word_count") or 0)
        words_per_sec = word_count / span if span > 0 else 0.0
        primary_phrase_text = str(slot.get("primary_phrase_text") or "")
        is_instrumental_tail_slot = bool(slot.get("_is_instrumental_tail_slot"))

        vocal_overlap = 0.0
        for vp in vocal_phrases:
            v0 = _clamp_time(_to_float(vp.get("start"), 0.0), duration_sec)
            v1 = _clamp_time(_to_float(vp.get("end"), v0), duration_sec)
            vocal_overlap += max(0.0, min(t1, v1) - max(t0, v0))
        if vocal_phrases:
            vocal_ratio = round(max(0.0, min(1.0, vocal_overlap / span)), 4)
        else:
            phrase_coverage = max(0.0, min(1.0, sum(_slot_phrase_overlap(t0, t1, phrase) for phrase in slot_phrases) / span))
            has_phrase_text = bool(primary_phrase_text)
            if is_instrumental_tail_slot:
                fallback_vocal_ratio = 0.03 + 0.07 * phrase_coverage
                vocal_ratio = round(max(0.0, min(0.12, fallback_vocal_ratio)), 4)
            elif transcript_available and (phrase_ids or has_phrase_text):
                fallback_vocal_ratio = 0.45 + 0.35 * phrase_coverage + 0.20 * min(1.0, words_per_sec / 3.2)
                vocal_ratio = round(max(0.0, min(0.95, fallback_vocal_ratio)), 4)
            elif has_phrase_text:
                fallback_vocal_ratio = 0.22 + 0.33 * phrase_coverage + 0.15 * min(1.0, words_per_sec / 3.2)
                vocal_ratio = round(max(0.0, min(0.7, fallback_vocal_ratio)), 4)
            else:
                vocal_ratio = 0.0
        phrase_density = len(slot_phrases) / span
        energy_score = round(max(0.0, min(1.0, 0.55 * vocal_ratio + 0.25 * min(1.0, words_per_sec / 3.0) + 0.2 * min(1.0, phrase_density))), 4)
        energy_variance = round(max(0.0, min(1.0, min(1.0, len(slot_phrases) / 3.0) * (0.4 + 0.6 * min(1.0, words_per_sec / 3.5)))), 4)

        slot_beats = [b for b in beats if t0 <= b <= t1]
        sync_points = sorted({round(item, 3) for item in ([b for b in slot_beats] + [p for p in energy_peaks if t0 <= p <= t1])})
        sync_points = sync_points[:8]
        rhythmic_intensity = round(max(0.0, min(1.0, len(sync_points) / max(1.0, span * 1.2))), 4)
        if beats:
            nearest_beat_dist = min(abs(t1 - beat) for beat in beats)
            beat_alignment_score = round(max(0.0, min(1.0, 1.0 - nearest_beat_dist / 0.25)), 4)
        else:
            beat_alignment_score = round(max(0.0, 1.0 - rhythmic_intensity * 0.5), 4)

        short_slot_warning = span < 2.8
        long_slot_warning = span > 8.0
        opening_risk = idx == 0 and (short_slot_warning or energy_score < 0.35 or vocal_ratio < 0.2)
        high_density_warning = words_per_sec > 3.2
        low_vocal_confidence = vocal_ratio > 0.2 and word_count == 0 and not word_rows
        beat_misalignment_warning = beat_alignment_score < 0.35 and len(slot_beats) >= 2
        orphan_tail_risk = idx == len(slots) - 1 and 0.5 <= span < 1.2

        merge_priority = "low"
        if short_slot_warning and (high_density_warning or idx == len(slots) - 1):
            merge_priority = "high"
        elif short_slot_warning or beat_misalignment_warning:
            merge_priority = "medium"
        hold_candidate = span >= 5.2 and energy_variance <= 0.42
        tail_ok = idx == len(slots) - 1 and span >= 1.8 and not orphan_tail_risk
        afterimage_candidate = idx == len(slots) - 1 and span >= 2.4 and energy_score <= 0.65

        slot["audio_features"] = {
            "vocal_ratio": vocal_ratio,
            "energy_score": energy_score,
            "energy_variance": energy_variance,
            "rhythmic_intensity": rhythmic_intensity,
            "sync_points": sync_points,
            "beat_alignment_score": beat_alignment_score,
        }
        slot["integrity_flags"] = {
            "short_slot_warning": short_slot_warning,
            "long_slot_warning": long_slot_warning,
            "opening_risk": opening_risk,
            "high_density_warning": high_density_warning,
            "low_vocal_confidence": low_vocal_confidence,
            "beat_misalignment_warning": beat_misalignment_warning,
            "orphan_tail_risk": orphan_tail_risk,
        }
        slot["hints"] = {
            "vocal_focus_potential": round(max(0.0, min(1.0, 0.75 * vocal_ratio + 0.25 * (1.0 - rhythmic_intensity))), 4),
            "motion_potential": round(max(0.0, min(1.0, 0.65 * rhythmic_intensity + 0.35 * energy_variance)), 4),
            "tail_ok": tail_ok,
            "afterimage_candidate": afterimage_candidate,
            "hold_candidate": hold_candidate,
            "dialogue_candidate": bool(vocal_ratio >= 0.55 and rhythmic_intensity <= 0.45),
            "merge_candidate_priority": merge_priority,
        }
        slot["phrase_ids"] = phrase_ids
        slot["phrase_count"] = len(slot_phrases)
        slot["word_count"] = word_count
        slot["primary_phrase_text"] = primary_phrase_text
        slot.pop("_hydrated_phrases", None)
        slot.pop("_is_instrumental_tail_slot", None)

    diagnostics_patch = {
        "audio_map_scene_slots_repair_stats": repair_stats,
        "audio_map_scene_slot_feature_notes": diagnostics_notes,
    }
    return slots, diagnostics_patch


def _max_equal_duration_streak(durations: list[float], *, tolerance: float = 0.08) -> int:
    if not durations:
        return 0
    best = 1
    current = 1
    for idx in range(1, len(durations)):
        if abs(durations[idx] - durations[idx - 1]) <= tolerance:
            current += 1
            best = max(best, current)
        else:
            current = 1
    return best


def _audio_map_grid_metrics(audio_map: dict[str, Any]) -> dict[str, Any]:
    phrase_durations = [
        float(item.get("duration_sec") or max(0.0, float(item.get("t1") or 0.0) - float(item.get("t0") or 0.0)))
        for item in _safe_list(audio_map.get("phrase_units"))
        if isinstance(item, dict)
    ]
    scene_durations = [
        float(item.get("duration_sec") or max(0.0, float(item.get("t1") or 0.0) - float(item.get("t0") or 0.0)))
        for item in _safe_list(audio_map.get("scene_candidate_windows"))
        if isinstance(item, dict)
    ]
    phrase_near_4 = [d for d in phrase_durations if abs(d - 4.0) <= 0.15]
    scene_near_4 = [d for d in scene_durations if abs(d - 4.0) <= 0.15]
    equal_phrase_pairs = sum(
        1 for idx in range(1, len(phrase_durations)) if abs(phrase_durations[idx] - phrase_durations[idx - 1]) <= 0.08
    )
    max_equal_phrase_streak = _max_equal_duration_streak(phrase_durations)
    max_equal_scene_streak = _max_equal_duration_streak(scene_durations)
    phrase_near_4_ratio = (len(phrase_near_4) / len(phrase_durations)) if phrase_durations else 0.0
    scene_near_4_ratio = (len(scene_near_4) / len(scene_durations)) if scene_durations else 0.0
    grid_like = bool(
        (len(phrase_durations) >= 4 and phrase_near_4_ratio >= 0.72 and max_equal_phrase_streak >= 3)
        or (len(scene_durations) >= 4 and scene_near_4_ratio >= 0.72 and max_equal_scene_streak >= 3)
    )
    return {
        "phrase_near_4_sec_count": len(phrase_near_4),
        "phrase_near_4_sec_ratio": round(phrase_near_4_ratio, 3),
        "equal_phrase_duration_adjacent_pairs": equal_phrase_pairs,
        "max_equal_phrase_duration_streak": max_equal_phrase_streak,
        "scene_near_4_sec_count": len(scene_near_4),
        "scene_near_4_sec_ratio": round(scene_near_4_ratio, 3),
        "max_equal_scene_duration_streak": max_equal_scene_streak,
        "audio_map_grid_like_segmentation": grid_like,
    }


def _validate_audio_map_soft(audio_map: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    duration = _coerce_duration_sec(audio_map.get("duration_sec"))
    if duration <= 0:
        return ["duration_missing"]

    phrase_units = [item for item in _safe_list(audio_map.get("phrase_units")) if isinstance(item, dict)]
    scene_windows = [item for item in _safe_list(audio_map.get("scene_candidate_windows")) if isinstance(item, dict)]
    if not phrase_units:
        flags.append("phrase_units_missing")
    if not scene_windows:
        flags.append("scene_candidate_windows_missing")

    prev_phrase_t1 = -1.0
    for idx, unit in enumerate(phrase_units):
        t0 = float(unit.get("t0") or 0.0)
        t1 = float(unit.get("t1") or 0.0)
        if t1 <= t0:
            flags.append(f"phrase_non_positive_span_{idx}")
        if prev_phrase_t1 >= 0 and t0 + 0.12 < prev_phrase_t1:
            flags.append(f"phrase_overlap_{idx}")
        prev_phrase_t1 = max(prev_phrase_t1, t1)
        if (t1 - t0) > 12.0:
            flags.append(f"phrase_too_long_{idx}")

    prev_scene_t1 = -1.0
    for idx, window in enumerate(scene_windows):
        t0 = float(window.get("t0") or 0.0)
        t1 = float(window.get("t1") or 0.0)
        if t1 <= t0:
            flags.append(f"scene_non_positive_span_{idx}")
        if prev_scene_t1 >= 0 and t0 + 0.2 < prev_scene_t1:
            flags.append(f"scene_overlap_{idx}")
        prev_scene_t1 = max(prev_scene_t1, t1)
        if (t1 - t0) > 12.0:
            flags.append(f"scene_too_long_{idx}")

    if scene_windows:
        scene_start = float(scene_windows[0].get("t0") or 0.0)
        scene_end = float(scene_windows[-1].get("t1") or 0.0)
        if scene_start > 1.5:
            flags.append("scene_start_gap_large")
        if duration - scene_end > 1.8:
            flags.append("scene_end_gap_large")
    return sorted(set(flags))


def _build_phrase_first_audio_map(
    duration_sec: float,
    story_core: dict[str, Any],
    analysis: dict[str, Any],
    transcript_text: str,
) -> dict[str, Any] | None:
    phrase_units = _build_phrase_units_from_approximate_alignment(analysis, duration_sec, transcript_text)
    if not phrase_units:
        return None
    base_map = _build_audio_map_from_dynamics(duration_sec, story_core, analysis, analysis_mode="approximate_phrase_grouping_v1")
    scene_windows = _build_scene_candidate_windows(phrase_units, duration_sec)
    phrase_boundaries = [
        round(float(item.get("t1") or 0.0), 3)
        for item in phrase_units
        if 0.0 < float(item.get("t1") or 0.0) < duration_sec
    ]
    base_map["analysis_mode"] = "approximate_phrase_grouping_v1"
    base_map["transcript_available"] = True
    base_map["phrase_units"] = phrase_units
    base_map["scene_candidate_windows"] = scene_windows
    base_map["phrase_endpoints_sec"] = sorted(set(phrase_boundaries))
    base_map["candidate_cut_points_sec"] = sorted(set(phrase_boundaries))
    base_map["cut_policy"] = {
        "min_scene_sec": 3,
        "target_scene_sec_min": 3,
        "target_scene_sec_max": 6,
        "hard_max_scene_sec": 8,
        "no_mid_word_cut": True,
        "prefer_phrase_endings": True,
        "prefer_semantic_boundaries": True,
    }
    base_map["audio_dynamics_summary"] = {
        **_safe_dict(base_map.get("audio_dynamics_summary")),
        "pause_points_count": len(_safe_list(analysis.get("pausePoints"))),
        "phrase_points_count": len(phrase_boundaries),
        "energy_peaks_count": len(_safe_list(analysis.get("energyPeaks"))),
        "detected_sections_count": len(_safe_list(analysis.get("sections"))),
    }
    return base_map


def _build_audio_map_from_real_alignment(
    duration_sec: float,
    story_core: dict[str, Any],
    analysis: dict[str, Any],
    alignment: dict[str, Any],
) -> dict[str, Any] | None:
    phrase_units = _build_phrase_units_from_real_alignment(alignment, duration_sec)
    if not phrase_units:
        return None
    base_map = _build_audio_map_from_dynamics(duration_sec, story_core, analysis, analysis_mode="transcript_alignment_v2")
    scene_windows = _build_scene_candidate_windows(phrase_units, duration_sec)
    phrase_boundaries = [round(float(item.get("t1") or 0.0), 3) for item in phrase_units if 0.0 < float(item.get("t1") or 0.0) < duration_sec]
    no_split_ranges = []
    for phrase in phrase_units:
        words = [word for word in _safe_list(phrase.get("words")) if isinstance(word, dict)]
        if len(words) >= 3:
            center_idx = len(words) // 2
            center_word = words[center_idx]
            no_split_ranges.append(
                {
                    "t0": round(_clamp_time(float(center_word.get("t0") or 0.0) - 0.12, duration_sec), 3),
                    "t1": round(_clamp_time(float(center_word.get("t1") or 0.0) + 0.12, duration_sec), 3),
                    "reason": f"phrase_core:{phrase.get('id')}",
                }
            )
    base_map["analysis_mode"] = "transcript_alignment_v2"
    base_map["transcript_available"] = bool(
        str(alignment.get("transcript_text") or "").strip() or _safe_list(alignment.get("words"))
    )
    base_map["transcript_text"] = str(alignment.get("transcript_text") or "").strip()
    base_map["transcript_alignment"] = {
        "transcript_text": str(alignment.get("transcript_text") or "").strip(),
        "words": _safe_list(alignment.get("words")),
        "phrases": _safe_list(alignment.get("phrases")),
        "source": str(alignment.get("source") or ""),
    }
    base_map["phrase_units"] = phrase_units
    base_map["scene_candidate_windows"] = scene_windows
    base_map["phrase_endpoints_sec"] = sorted(set(phrase_boundaries))
    base_map["candidate_cut_points_sec"] = sorted(set(phrase_boundaries))
    base_map["no_split_ranges"] = sorted(
        [item for item in _safe_list(base_map.get("no_split_ranges")) if isinstance(item, dict)] + no_split_ranges,
        key=lambda item: float(item.get("t0") or 0.0),
    )
    base_map["cut_policy"] = {
        "min_scene_sec": 3,
        "target_scene_sec_min": 3,
        "target_scene_sec_max": 6,
        "hard_max_scene_sec": 8,
        "max_scene_sec_forbidden": 8,
        "no_mid_word_cut": True,
        "prefer_phrase_endings": True,
        "prefer_semantic_boundaries": True,
        "requires_word_aligned_cut_points": True,
        "alignment_source": str(alignment.get("source") or "unknown"),
    }
    return base_map


def _add_scored_candidate(
    score_by_point: dict[float, float],
    point: float,
    score: float,
    duration_sec: float,
    *,
    edge_padding_sec: float = 0.65,
) -> None:
    t = _clamp_time(point, duration_sec)
    if not (edge_padding_sec <= t <= max(0.0, duration_sec - edge_padding_sec)):
        return
    key = round(t, 3)
    score_by_point[key] = max(score_by_point.get(key, 0.0), float(score))


def _choose_section_boundaries(
    duration_sec: float,
    segment_count: int,
    score_by_point: dict[float, float],
) -> list[float]:
    if duration_sec <= 0:
        return [0.0]
    target_boundaries = max(0, segment_count - 1)
    if target_boundaries <= 0:
        return [0.0, round(duration_sec, 3)]
    min_gap = max(2.4, min(6.0, duration_sec / (segment_count * 1.8)))
    sorted_candidates = sorted(score_by_point.items(), key=lambda item: item[0])
    boundaries = [0.0]
    for idx in range(1, target_boundaries + 1):
        ideal = duration_sec * idx / segment_count
        best: tuple[float, float] | None = None
        for point, base_score in sorted_candidates:
            if point <= boundaries[-1] + min_gap or point >= duration_sec - min_gap:
                continue
            distance_penalty = abs(point - ideal) / max(0.001, duration_sec)
            score = base_score - distance_penalty
            if best is None or score > best[1]:
                best = (point, score)
        if best is None:
            fallback = round(_clamp_time(ideal, duration_sec), 3)
            if fallback <= boundaries[-1] + min_gap:
                fallback = round(min(duration_sec - min_gap, boundaries[-1] + min_gap), 3)
            boundaries.append(fallback)
        else:
            boundaries.append(round(best[0], 3))
    cleaned = [0.0]
    for value in sorted(set(boundaries[1:])):
        if value - cleaned[-1] >= min_gap:
            cleaned.append(value)
    if cleaned[-1] < duration_sec - min_gap * 0.5:
        cleaned.append(round(duration_sec, 3))
    else:
        cleaned[-1] = round(duration_sec, 3)
    if len(cleaned) < 2:
        return [0.0, round(duration_sec, 3)]
    return cleaned


def _build_audio_map_from_dynamics(
    duration_sec: float,
    story_core: dict[str, Any],
    analysis: dict[str, Any],
    *,
    analysis_mode: str,
) -> dict[str, Any]:
    duration = _coerce_duration_sec(duration_sec)
    segment_count = _segment_count_for_duration(duration)
    labels = _labels_for_segment_count(segment_count)
    moods = _mood_curve_from_story_core(story_core, segment_count)

    score_by_point: dict[float, float] = {}
    sections_analysis = _safe_list(analysis.get("sections"))
    pause_points = _float_points(analysis.get("pausePoints"), duration, min_t=0.15, max_t=max(0.0, duration - 0.15))
    phrase_points = _float_points(analysis.get("phraseBoundaries"), duration, min_t=0.15, max_t=max(0.0, duration - 0.15))
    energy_peaks = _float_points(analysis.get("energyPeaks"), duration, min_t=0.15, max_t=max(0.0, duration - 0.15))
    downbeats = _float_points(analysis.get("downbeats"), duration, min_t=0.15, max_t=max(0.0, duration - 0.15))

    for section in sections_analysis:
        t0 = _coerce_duration_sec(section.get("start"))
        t1 = _coerce_duration_sec(section.get("end"))
        if t1 - t0 < 0.9:
            continue
        _add_scored_candidate(score_by_point, t0, 0.62, duration)
        _add_scored_candidate(score_by_point, t1, 0.98, duration)
    for point in pause_points:
        _add_scored_candidate(score_by_point, point, 1.0, duration)
    for point in phrase_points:
        _add_scored_candidate(score_by_point, point, 0.72, duration)
    for point in energy_peaks:
        _add_scored_candidate(score_by_point, point, 0.55, duration)
    for point in downbeats:
        _add_scored_candidate(score_by_point, point, 0.42, duration)

    boundaries = _choose_section_boundaries(duration, segment_count, score_by_point)
    if len(boundaries) < 2:
        raise ValueError("audio_dynamics_v2_boundaries_missing")

    sections: list[dict[str, Any]] = []
    section_energies: list[float] = []
    for idx in range(len(boundaries) - 1):
        t0 = round(boundaries[idx], 3)
        t1 = round(boundaries[idx + 1], 3)
        section_mid = (t0 + t1) * 0.5
        density_score = 0.0
        for point in energy_peaks:
            if t0 <= point <= t1:
                density_score += 1.0
        for point in pause_points:
            if t0 <= point <= t1:
                density_score -= 0.5
        density_score += 0.15 * math.sin(section_mid / max(duration, 1.0) * math.pi)
        section_energies.append(density_score)
        sections.append(
            {
                "id": f"sec_{idx + 1}",
                "t0": t0,
                "t1": t1,
                "label": labels[idx] if idx < len(labels) else f"part_{idx + 1}",
                "mood": moods[idx] if idx < len(moods) else "neutral",
            }
        )

    if section_energies:
        low_thr = float(sorted(section_energies)[max(0, int(len(section_energies) * 0.3) - 1)])
        high_thr = float(sorted(section_energies)[min(len(section_energies) - 1, int(len(section_energies) * 0.7))])
    else:
        low_thr = high_thr = 0.0
    for idx, section in enumerate(sections):
        energy_value = section_energies[idx] if idx < len(section_energies) else 0.0
        if energy_value <= low_thr:
            section["energy"] = "low"
        elif energy_value >= high_thr:
            section["energy"] = "high"
        else:
            section["energy"] = "medium"

    phrase_endpoints = sorted(
        set(
            [
                point
                for point in (pause_points + phrase_points + [round(float(s.get("t1") or 0.0), 3) for s in sections])
                if 0.0 < point < duration
            ]
        )
    )

    no_split_ranges: list[dict[str, Any]] = []
    if duration > 0:
        no_split_ranges.append({"t0": 0.0, "t1": round(min(1.2, duration), 3), "reason": "intro_protection"})
        tail_start = round(max(0.0, duration - 1.2), 3)
        if tail_start < duration:
            no_split_ranges.append({"t0": tail_start, "t1": round(duration, 3), "reason": "outro_tail_protection"})
    for peak in energy_peaks:
        no_split_ranges.append(
            {
                "t0": round(max(0.0, peak - 0.28), 3),
                "t1": round(min(duration, peak + 0.28), 3),
                "reason": "energy_core_protection",
            }
        )

    candidate_cut_points: list[float] = []
    for point in sorted(score_by_point.keys()):
        if point <= 0.0 or point >= duration:
            continue
        inside_blocked = any(float(r.get("t0") or 0.0) <= point <= float(r.get("t1") or 0.0) for r in no_split_ranges)
        if inside_blocked:
            continue
        if score_by_point.get(point, 0.0) >= 0.58:
            candidate_cut_points.append(point)

    mood_progression = [
        {
            "t0": float(section.get("t0") or 0.0),
            "t1": float(section.get("t1") or 0.0),
            "mood": str(section.get("mood") or "neutral"),
            "energy": str(section.get("energy") or "medium"),
        }
        for section in sections
    ]
    energy_counts = {"low": 0, "medium": 0, "high": 0}
    for section in sections:
        energy = str(section.get("energy") or "medium").lower()
        if energy in energy_counts:
            energy_counts[energy] += 1
    dominant_energy = max(energy_counts.items(), key=lambda item: item[1])[0] if sections else "medium"

    lip_sync_ranges: list[dict[str, float]] = []
    content_hint = str(story_core.get("story_summary") or "").lower()
    if "speak" in content_hint or "sing" in content_hint or "vocal" in content_hint:
        for section in sections:
            if str(section.get("energy") or "").lower() in {"medium", "high"}:
                lip_sync_ranges.append({"t0": round(float(section.get("t0") or 0.0), 3), "t1": round(float(section.get("t1") or 0.0), 3)})

    phrase_units = _build_phrase_units_from_music_dynamics(duration, analysis)
    if phrase_units:
        phrase_endpoints = sorted(
            {
                round(float(item.get("t1") or 0.0), 3)
                for item in phrase_units
                if 0.0 < float(item.get("t1") or 0.0) < duration
            }
        )
    scene_windows = _build_scene_candidate_windows(phrase_units, duration) if phrase_units else []

    arc_short = str(story_core.get("global_arc") or "").strip() or "unknown_arc"
    return {
        "duration_sec": round(duration, 3),
        "analysis_mode": analysis_mode,
        "sections": sections,
        "phrase_endpoints_sec": phrase_endpoints,
        "phrase_units": phrase_units,
        "scene_candidate_windows": scene_windows,
        "no_split_ranges": no_split_ranges,
        "candidate_cut_points_sec": sorted(set(candidate_cut_points)),
        "pacing_profile": {
            "segment_count": len(sections),
            "dominant_energy": dominant_energy,
            "candidate_density": round(len(score_by_point) / max(duration, 1.0), 3),
        },
        "mood_progression": mood_progression,
        "audio_arc_summary": f"Audio map follows story_core arc '{arc_short}' and aligns sections to detected dynamics/pause anchors.",
        "section_summary": [f"{sec.get('label')}:{sec.get('energy')}/{sec.get('mood')}" for sec in sections],
        "lip_sync_candidate_ranges": lip_sync_ranges,
        "audio_dynamics_summary": {
            "pause_points_count": len(pause_points),
            "phrase_points_count": len(phrase_points),
            "energy_peaks_count": len(energy_peaks),
            "detected_sections_count": len(sections_analysis),
        },
    }


def _is_usable_audio_map(audio_map: dict[str, Any]) -> bool:
    if not isinstance(audio_map, dict):
        return False
    if str(audio_map.get("audio_map_version") or "").strip() == "1.1":
        segments = _safe_list(audio_map.get("segments"))
        duration = _coerce_duration_sec(audio_map.get("duration_sec"))
        return duration > 0 and bool(segments)
    duration = _coerce_duration_sec(audio_map.get("duration_sec"))
    sections = _safe_list(audio_map.get("sections"))
    return duration > 0 and bool(sections)


def _audio_map_story_core_guard_fingerprint(audio_map: dict[str, Any]) -> str:
    node = _safe_dict(audio_map)

    def _norm_rows(rows: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for idx, row in enumerate(_safe_list(rows), start=1):
            if not isinstance(row, dict):
                continue
            normalized.append(
                {
                    "id": str(row.get("id") or row.get("scene_id") or row.get("slot_id") or idx),
                    "t0": round(_to_float(row.get("t0"), 0.0), 3),
                    "t1": round(_to_float(row.get("t1"), 0.0), 3),
                }
            )
        return normalized

    payload = {
        "duration_sec": round(_coerce_duration_sec(node.get("duration_sec")), 3),
        "phrase_units_count": len(_safe_list(node.get("phrase_units"))),
        "scene_candidate_windows_count": len(_safe_list(node.get("scene_candidate_windows"))),
        "scene_slots_count": len(_safe_list(node.get("scene_slots"))),
        "phrase_units": _norm_rows(node.get("phrase_units")),
        "scene_candidate_windows": _norm_rows(node.get("scene_candidate_windows")),
        "scene_slots": _norm_rows(node.get("scene_slots")),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_audio_rows_for_music_video(rows: list[dict[str, Any]], duration_sec: float) -> list[dict[str, Any]]:
    duration = _coerce_duration_sec(duration_sec)
    normalized: list[dict[str, Any]] = []
    cursor = 0.0
    for row in sorted(rows, key=lambda item: (_to_float(item.get("t0"), 0.0), _to_float(item.get("t1"), 0.0))):
        t0 = round(_clamp_time(_to_float(row.get("t0"), 0.0), duration), 3)
        t1 = round(_clamp_time(_to_float(row.get("t1"), t0), duration), 3)
        t0 = max(t0, round(cursor, 3))
        if t1 <= t0:
            continue
        fixed = dict(row)
        fixed["t0"] = t0
        fixed["t1"] = t1
        fixed["duration_sec"] = round(max(0.0, t1 - t0), 3)
        normalized.append(fixed)
        cursor = t1
    return normalized


def _finalize_music_video_model_led_audio_map(audio_map: dict[str, Any], duration_sec: float) -> tuple[dict[str, Any], str]:
    duration = _coerce_duration_sec(duration_sec)
    if duration <= 0:
        return audio_map, "duration_missing"

    phrase_units_raw = [item for item in _safe_list(audio_map.get("phrase_units")) if isinstance(item, dict)]
    scene_windows_raw = [item for item in _safe_list(audio_map.get("scene_candidate_windows")) if isinstance(item, dict)]
    if not phrase_units_raw and not scene_windows_raw:
        return audio_map, "segmentation_units_missing"

    phrase_units = _normalize_audio_rows_for_music_video(phrase_units_raw, duration)
    scene_windows = _normalize_audio_rows_for_music_video(scene_windows_raw, duration)
    if not phrase_units and not scene_windows:
        return audio_map, "segmentation_units_invalid_after_normalization"

    coverage_rows = scene_windows or phrase_units
    first_t0 = _to_float(coverage_rows[0].get("t0"), 0.0) if coverage_rows else 0.0
    last_t1 = _to_float(coverage_rows[-1].get("t1"), 0.0) if coverage_rows else 0.0
    covered_span = max(0.0, last_t1 - first_t0)
    min_required_coverage = max(1.0, duration * 0.35)
    if covered_span < min_required_coverage:
        return audio_map, "segmentation_coverage_too_low"

    sections = [item for item in _safe_list(audio_map.get("sections")) if isinstance(item, dict)]
    normalized_sections = _normalize_audio_rows_for_music_video(sections, duration)
    if not normalized_sections:
        source_rows = scene_windows or phrase_units
        normalized_sections = [
            {
                "id": f"sec_{idx + 1}",
                "t0": float(item.get("t0") or 0.0),
                "t1": float(item.get("t1") or 0.0),
                "label": str(item.get("scene_function") or item.get("label") or f"part_{idx + 1}"),
                "energy": str(item.get("energy") or "medium"),
                "mood": str(item.get("mood") or "neutral"),
            }
            for idx, item in enumerate(source_rows)
        ]

    finalized = dict(audio_map)
    finalized["duration_sec"] = round(duration, 3)
    finalized["overall_duration_sec"] = round(duration, 3)
    finalized["phrase_units"] = phrase_units
    finalized["scene_candidate_windows"] = scene_windows
    finalized["sections"] = normalized_sections
    finalized["phrase_endpoints_sec"] = sorted(
        {
            round(_to_float(item.get("t1"), 0.0), 3)
            for item in phrase_units
            if 0.0 < _to_float(item.get("t1"), 0.0) < duration
        }
    )
    finalized["candidate_cut_points_sec"] = sorted(
        {
            round(_to_float(value, -1.0), 3)
            for value in _safe_list(finalized.get("candidate_cut_points_sec"))
            if 0.0 < _to_float(value, -1.0) < duration
        }
    )
    return finalized, ""


def _build_audio_map_from_gemini_payload(payload: dict[str, Any], duration_sec: float, *, analysis_mode: str) -> dict[str, Any]:
    duration = _coerce_duration_sec(duration_sec)
    phrase_units = [item for item in _safe_list(payload.get("phrase_units")) if isinstance(item, dict)]
    scene_windows = [item for item in _safe_list(payload.get("scene_candidate_windows")) if isinstance(item, dict)]
    phrase_endpoints = sorted(
        {
            round(_to_float(value, -1.0), 3)
            for value in _safe_list(payload.get("phrase_endpoints_sec"))
            if 0.0 < _to_float(value, -1.0) < duration
        }
    )
    if not phrase_endpoints and phrase_units:
        phrase_endpoints = sorted(
            {
                round(float(item.get("t1") or 0.0), 3)
                for item in phrase_units
                if 0.0 < float(item.get("t1") or 0.0) < duration
            }
        )
    return {
        "duration_sec": round(duration, 3),
        "analysis_mode": analysis_mode,
        "transcript_available": bool(payload.get("transcript_available")),
        "track_type": str(payload.get("track_type") or "unknown"),
        "overall_duration_sec": float(payload.get("overall_duration_sec") or duration),
        "global_notes": _safe_dict(payload.get("global_notes")),
        "sections": [item for item in _safe_list(payload.get("sections")) if isinstance(item, dict)],
        "phrase_endpoints_sec": phrase_endpoints,
        "phrase_units": phrase_units,
        "scene_candidate_windows": scene_windows,
        "candidate_cut_points_sec": [value for value in _safe_list(payload.get("candidate_cut_points_sec"))],
        "no_split_ranges": [item for item in _safe_list(payload.get("no_split_ranges")) if isinstance(item, dict)],
        "lip_sync_candidate_ranges": [item for item in _safe_list(payload.get("lip_sync_candidate_ranges")) if isinstance(item, dict)],
        "audio_map_alignment_source": "gemini_semantic_segmentation",
    }


def _summarize_audio_map_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sections = [item for item in _safe_list(payload.get("sections")) if isinstance(item, dict)]
    phrase_units = [item for item in _safe_list(payload.get("phrase_units")) if isinstance(item, dict)]
    scene_windows = [item for item in _safe_list(payload.get("scene_candidate_windows")) if isinstance(item, dict)]
    all_rows = sections + phrase_units + scene_windows
    t0_values = [_to_float(item.get("t0"), -1.0) for item in all_rows]
    t1_values = [_to_float(item.get("t1"), -1.0) for item in all_rows]
    first_t0 = min((v for v in t0_values if v >= 0.0), default=None)
    last_t1 = max((v for v in t1_values if v >= 0.0), default=None)
    return {
        "sections_count": len(sections),
        "phrase_units_count": len(phrase_units),
        "scene_candidate_windows_count": len(scene_windows),
        "phrase_endpoints_count": len(_safe_list(payload.get("phrase_endpoints_sec"))),
        "first_t0": round(first_t0, 3) if first_t0 is not None else None,
        "last_t1": round(last_t1, 3) if last_t1 is not None else None,
        "overall_duration_sec": _to_float(payload.get("overall_duration_sec"), 0.0),
    }


def _summarize_audio_map_v11(payload: dict[str, Any]) -> dict[str, Any]:
    segments = [item for item in _safe_list(payload.get("segments")) if isinstance(item, dict)]
    first_t0 = _to_float(_safe_dict(segments[0]).get("t0"), -1.0) if segments else -1.0
    last_t1 = _to_float(_safe_dict(segments[-1]).get("t1"), -1.0) if segments else -1.0
    return {
        "audio_map_version": str(payload.get("audio_map_version") or ""),
        "audio_id": str(payload.get("audio_id") or "")[:120],
        "segments_count": len(segments),
        "first_t0": round(first_t0, 3) if first_t0 >= 0 else None,
        "last_t1": round(last_t1, 3) if last_t1 >= 0 else None,
        "no_split_ranges_count": len(_safe_list(payload.get("no_split_ranges"))),
    }


def _derive_audio_hint_profile(
    *,
    idx: int,
    segments: list[dict[str, Any]],
    t0: float,
    t1: float,
    intensity: float,
    text: str,
) -> dict[str, Any]:
    duration = max(0.001, t1 - t0)
    prev_seg = _safe_dict(segments[idx - 2]) if idx > 1 else {}
    next_seg = _safe_dict(segments[idx]) if idx < len(segments) else {}
    prev_intensity = _to_float(prev_seg.get("intensity"), intensity)
    next_intensity = _to_float(next_seg.get("intensity"), intensity)
    word_count = len([w for w in re.split(r"\s+", text) if w.strip()])
    words_per_sec = word_count / duration
    is_last = idx == len(segments)
    has_pause_like_tail = bool(re.search(r"(\.\.\.|[!?…]|\b(oh|ah|mm|hmm|yeah|okay)\b)\s*$", text.strip(), flags=re.IGNORECASE))
    reflective_markers = bool(re.search(r"\b(why|if|maybe|remember|think|wonder|seems|felt|feel)\b", text, flags=re.IGNORECASE))
    assertive_markers = bool(re.search(r"\b(now|never|must|will|can't|dont|don't|stop|go)\b", text, flags=re.IGNORECASE))
    observation_markers = bool(re.search(r"\b(there is|i see|you see|look|watch|when)\b", text, flags=re.IGNORECASE))

    if intensity <= 0.28:
        local_energy_band = "low"
    elif intensity >= 0.84 and intensity >= prev_intensity + 0.14 and intensity >= next_intensity - 0.03:
        local_energy_band = "surge"
    elif intensity <= prev_intensity - 0.14 and intensity <= next_intensity + 0.08:
        local_energy_band = "settle"
    elif intensity >= 0.67:
        local_energy_band = "high"
    else:
        local_energy_band = "medium"

    if idx == 1:
        energy_delta = "reset"
    else:
        delta = intensity - prev_intensity
        if delta >= 0.24:
            energy_delta = "spike"
        elif delta >= 0.08:
            energy_delta = "rise"
        elif delta <= -0.24:
            energy_delta = "release"
        elif delta <= -0.08:
            energy_delta = "soften"
        else:
            energy_delta = "hold"

    if is_last and (has_pause_like_tail or intensity < 0.62):
        delivery_mode = "final"
    elif words_per_sec >= 3.2 and intensity >= 0.68:
        delivery_mode = "pressurized"
    elif reflective_markers:
        delivery_mode = "reflective"
    elif assertive_markers and intensity >= 0.62:
        delivery_mode = "assertive"
    elif words_per_sec <= 1.35 and intensity <= 0.55:
        delivery_mode = "intimate"
    elif has_pause_like_tail and words_per_sec <= 2.0:
        delivery_mode = "suspended"
    elif observation_markers:
        delivery_mode = "observational"
    else:
        delivery_mode = "declarative"

    punctuation_weight = len(re.findall(r"[,:;!?…]", text))
    semantic_score = (0.45 if reflective_markers else 0.0) + (0.35 if assertive_markers else 0.0) + min(0.6, punctuation_weight * 0.1)
    semantic_score += min(0.5, max(0.0, words_per_sec - 2.0) * 0.25)
    if semantic_score >= 0.9:
        semantic_weight = "high"
    elif semantic_score >= 0.4:
        semantic_weight = "medium"
    else:
        semantic_weight = "low"

    semantic_turn_candidate = bool(
        idx > 1
        and (
            reflective_markers
            or bool(re.search(r"\b(but|yet|still|then|instead|suddenly|however)\b", text, flags=re.IGNORECASE))
            or abs(intensity - prev_intensity) >= 0.2
        )
    )
    release_candidate = bool(
        has_pause_like_tail
        or (energy_delta in {"release", "soften"} and words_per_sec <= 2.5)
        or (local_energy_band == "settle" and duration >= 3.6)
    )
    if is_last and has_pause_like_tail:
        finality_candidate = "tail_hit"
    elif is_last and (release_candidate or delivery_mode == "final"):
        finality_candidate = "closure"
    elif semantic_turn_candidate and (energy_delta in {"rise", "soften", "release", "spike"} or delivery_mode in {"reflective", "observational"}):
        finality_candidate = "hinge"
    elif energy_delta in {"rise", "spike"} and delivery_mode in {"assertive", "pressurized"} and not release_candidate:
        finality_candidate = "continuation"
    else:
        finality_candidate = "none"
    visual_density_hint = "dense" if (words_per_sec >= 3.2 or intensity >= 0.8) else ("sparse" if words_per_sec <= 1.4 and intensity <= 0.45 else "moderate")
    stillness_candidate = bool(
        (release_candidate and words_per_sec <= 2.8)
        or (delivery_mode in {"intimate", "suspended", "reflective", "final", "observational"} and words_per_sec <= 2.5)
        or (duration >= 4.2 and words_per_sec <= 1.7)
    )
    lyrical_density = "high" if words_per_sec >= 3.1 else ("low" if words_per_sec <= 1.45 else "medium")
    return {
        "word_count": word_count,
        "local_energy_band": local_energy_band,
        "energy_delta_vs_prev": energy_delta,
        "delivery_mode": delivery_mode,
        "semantic_weight": semantic_weight,
        "semantic_turn_candidate": semantic_turn_candidate,
        "release_candidate": release_candidate,
        "finality_candidate": finality_candidate,
        "visual_density_hint": visual_density_hint,
        "stillness_candidate": stillness_candidate,
        "lyrical_density": lyrical_density,
        "intensity_bucket": "high" if intensity >= 0.67 else ("medium" if intensity >= 0.33 else "low"),
    }


def _legacy_energy_from_local_band(local_energy_band: str, fallback: str) -> str:
    band = str(local_energy_band or "").strip().lower()
    if band == "low":
        return "low"
    if band in {"medium", "settle"}:
        return "medium"
    if band in {"high", "surge"}:
        return "high"
    return str(fallback or "medium").strip().lower() if str(fallback or "").strip().lower() in {"low", "medium", "high"} else "medium"


def _is_first_last_candidate_from_hints(
    *,
    idx: int,
    total: int,
    duration: float,
    local_energy_band: str,
    delivery_mode: str,
    finality_candidate: str,
    semantic_turn_candidate: bool,
) -> bool:
    if duration < 4.0:
        return False
    is_open = idx == 1 and local_energy_band in {"high", "surge"} and delivery_mode in {"declarative", "assertive", "pressurized"}
    is_end = idx == total and finality_candidate in {"closure", "tail_hit"}
    is_controlled_transition = semantic_turn_candidate and finality_candidate == "hinge" and local_energy_band in {"medium", "high", "settle"}
    return bool(is_open or is_end or is_controlled_transition)


SCENE_PACKAGING_POLICY = {
    "short_vocal_merge_min_sec": 2.5,
    "short_vocal_merge_max_sec": 4.2,
    "short_vocal_merged_max_sec": 7.5,
    "adjacent_vocal_max_gap_sec": 0.12,
    "long_world_split_min_sec": 5.8,
    "world_split_part_min_sec": 2.5,
    "world_split_part_target_max_sec": 4.0,
    "world_split_part_hard_max_sec": 4.4,
    "no_split_gap_candidate_min_sec": 0.18,
    "no_split_gap_candidate_midpoint_bias": 0.5,
    "instrumental_gap_min_detect_sec": 0.9,
    "instrumental_gap_scene_min_sec": 2.5,
    "instrumental_gap_multi_scene_min_sec": 5.5,
    "instrumental_gap_three_scene_min_sec": 8.5,
    "instrumental_scene_min_duration_sec": 2.8,
}


def _segment_duration(seg: dict[str, Any]) -> float:
    t0 = _to_float(seg.get("t0"), 0.0)
    t1 = _to_float(seg.get("t1"), t0)
    return round(max(0.0, _to_float(seg.get("duration_sec"), t1 - t0)), 3)


def _is_non_vocal_world_candidate(seg: dict[str, Any]) -> bool:
    text = str(seg.get("transcript_slice") or "").strip()
    if bool(seg.get("is_lip_sync_candidate")) and text:
        return False
    route_hints = _safe_dict(seg.get("route_hints"))
    lip_fit = str(route_hints.get("lip_sync_fit") or "").strip().lower()
    lyrical_density = str(seg.get("lyrical_density") or "").strip().lower()
    return bool((not text) or lip_fit in {"too_short", "too_long"} or lyrical_density in {"low", ""})


def _is_semantic_break_between(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_text = str(left.get("transcript_slice") or "").strip()
    right_text = str(right.get("transcript_slice") or "").strip()
    left_finality = str(left.get("finality_candidate") or "").strip().lower()
    right_turn = bool(right.get("semantic_turn_candidate"))
    right_release = bool(right.get("release_candidate"))
    if left_finality in {"hinge", "closure", "tail_hit"}:
        return True
    if right_turn or right_release:
        return True
    if re.search(r"[.!?…]\s*$", left_text) and re.search(
        r"^\s*(but|however|yet|then|instead|meanwhile|suddenly|now)\b",
        right_text,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def _is_inside_no_split(point_sec: float, no_split_ranges: list[dict[str, Any]], *, eps: float = 0.01) -> bool:
    for row in no_split_ranges:
        start = _to_float(_safe_dict(row).get("start"), _to_float(_safe_dict(row).get("t0"), -1.0))
        end = _to_float(_safe_dict(row).get("end"), _to_float(_safe_dict(row).get("t1"), -1.0))
        if start < 0 or end < 0:
            continue
        if (start - eps) <= point_sec <= (end + eps):
            return True
    return False


def _derive_gap_split_candidates_from_no_split_ranges(
    *,
    window_t0: float,
    window_t1: float,
    no_split_ranges: list[dict[str, Any]],
) -> list[float]:
    policy = SCENE_PACKAGING_POLICY
    min_gap = _to_float(policy.get("no_split_gap_candidate_min_sec"), 0.0)
    midpoint_bias = min(1.0, max(0.0, _to_float(policy.get("no_split_gap_candidate_midpoint_bias"), 0.5)))
    if window_t1 <= window_t0:
        return []
    clamped_ranges: list[tuple[float, float]] = []
    for row in no_split_ranges:
        start = _to_float(_safe_dict(row).get("start"), _to_float(_safe_dict(row).get("t0"), -1.0))
        end = _to_float(_safe_dict(row).get("end"), _to_float(_safe_dict(row).get("t1"), -1.0))
        if end <= start:
            continue
        clip_start = max(window_t0, start)
        clip_end = min(window_t1, end)
        if clip_end <= clip_start:
            continue
        clamped_ranges.append((round(clip_start, 3), round(clip_end, 3)))
    if len(clamped_ranges) < 2:
        return []
    clamped_ranges.sort(key=lambda item: (item[0], item[1]))
    merged_ranges: list[tuple[float, float]] = []
    for start, end in clamped_ranges:
        if not merged_ranges or start > merged_ranges[-1][1]:
            merged_ranges.append((start, end))
            continue
        prev_start, prev_end = merged_ranges[-1]
        merged_ranges[-1] = (prev_start, max(prev_end, end))
    if len(merged_ranges) < 2:
        return []
    gap_candidates: list[float] = []
    for idx in range(len(merged_ranges) - 1):
        left_end = merged_ranges[idx][1]
        right_start = merged_ranges[idx + 1][0]
        gap = round(right_start - left_end, 3)
        if gap < min_gap:
            continue
        candidate = left_end + gap * midpoint_bias
        if not (window_t0 + 0.01 < candidate < window_t1 - 0.01):
            continue
        gap_candidates.append(round(candidate, 3))
    return sorted(set(gap_candidates))


def _derive_instrumental_gap_windows(
    *,
    phrase_units: list[dict[str, Any]],
    timeline_t0: float,
    timeline_t1: float,
) -> list[dict[str, Any]]:
    policy = SCENE_PACKAGING_POLICY
    min_detect = _to_float(policy.get("instrumental_gap_min_detect_sec"), 0.0)
    if timeline_t1 <= timeline_t0:
        return []
    vocal_ranges: list[tuple[float, float]] = []
    for unit in phrase_units:
        row = _safe_dict(unit)
        text = str(row.get("text") or row.get("transcript_slice") or row.get("transcript") or "").strip()
        has_text = bool(text and not _is_instrumental_tail_marker_text(text))
        has_words = _phrase_word_count(row) > 0
        if not (has_text or has_words):
            continue
        t0 = _to_float(row.get("t0"), -1.0)
        t1 = _to_float(row.get("t1"), -1.0)
        if not (timeline_t0 <= t0 < t1 <= timeline_t1 + 0.01):
            continue
        vocal_ranges.append((max(timeline_t0, t0), min(timeline_t1, t1)))
    if not vocal_ranges:
        total = round(max(0.0, timeline_t1 - timeline_t0), 3)
        return [{"t0": round(timeline_t0, 3), "t1": round(timeline_t1, 3), "duration_sec": total, "position": "full_track"}] if total >= min_detect else []
    vocal_ranges.sort(key=lambda pair: (pair[0], pair[1]))
    merged: list[tuple[float, float]] = []
    for start, end in vocal_ranges:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        prev_start, prev_end = merged[-1]
        merged[-1] = (prev_start, max(prev_end, end))
    windows: list[dict[str, Any]] = []
    cursor = timeline_t0
    for idx, (start, end) in enumerate(merged):
        if start > cursor:
            duration = round(max(0.0, start - cursor), 3)
            if duration >= min_detect:
                position = "intro" if idx == 0 else "middle"
                windows.append({"t0": round(cursor, 3), "t1": round(start, 3), "duration_sec": duration, "position": position})
        cursor = max(cursor, end)
    if timeline_t1 > cursor:
        duration = round(max(0.0, timeline_t1 - cursor), 3)
        if duration >= min_detect:
            position = "outro" if merged else "full_track"
            windows.append({"t0": round(cursor, 3), "t1": round(timeline_t1, 3), "duration_sec": duration, "position": position})
    return windows


def _build_instrumental_block_internal_splits(
    *,
    t0: float,
    t1: float,
    target_scene_count: int,
    candidate_cut_points: list[float],
    no_split_ranges: list[dict[str, Any]],
) -> list[float]:
    policy = SCENE_PACKAGING_POLICY
    min_scene_duration = _to_float(policy.get("instrumental_scene_min_duration_sec"), 2.5)
    if target_scene_count <= 1:
        return []
    desired_split_count = target_scene_count - 1
    candidate_pool = {
        round(_to_float(point, -1.0), 3)
        for point in candidate_cut_points
        if (t0 + 0.01) < _to_float(point, -1.0) < (t1 - 0.01) and not _is_inside_no_split(_to_float(point, -1.0), no_split_ranges)
    }
    span = max(0.0, t1 - t0)
    for k in range(1, target_scene_count):
        ideal = t0 + span * (k / target_scene_count)
        if (t0 + 0.01) < ideal < (t1 - 0.01) and not _is_inside_no_split(ideal, no_split_ranges):
            candidate_pool.add(round(ideal, 3))
    sorted_pool = sorted(candidate_pool)
    if len(sorted_pool) < desired_split_count:
        return []
    best: tuple[float, list[float]] | None = None
    for combo in itertools.combinations(sorted_pool, desired_split_count):
        cuts = [t0, *combo, t1]
        chunks = [round(cuts[idx + 1] - cuts[idx], 3) for idx in range(len(cuts) - 1)]
        if any(chunk < min_scene_duration for chunk in chunks):
            continue
        ideal = span / target_scene_count if target_scene_count > 0 else span
        score = sum(abs(chunk - ideal) for chunk in chunks)
        if best is None or score < best[0]:
            best = (score, list(combo))
    return [] if best is None else [round(point, 3) for point in best[1]]


def _package_instrumental_gap_windows(
    segments: list[dict[str, Any]],
    *,
    phrase_units: list[dict[str, Any]],
    candidate_cut_points: list[float],
    no_split_ranges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    policy = SCENE_PACKAGING_POLICY
    scene_min = _to_float(policy.get("instrumental_gap_scene_min_sec"), 2.5)
    multi_min = _to_float(policy.get("instrumental_gap_multi_scene_min_sec"), 5.5)
    three_scene_min = _to_float(policy.get("instrumental_gap_three_scene_min_sec"), 8.5)
    timeline_t0 = _to_float(_safe_dict(segments[0]).get("t0"), 0.0) if segments else 0.0
    timeline_t1 = max((_to_float(_safe_dict(seg).get("t1"), 0.0) for seg in segments), default=timeline_t0)
    gap_windows = _derive_instrumental_gap_windows(phrase_units=phrase_units, timeline_t0=timeline_t0, timeline_t1=timeline_t1)
    if not gap_windows:
        return segments, {"detected_gap_count": 0, "scene_block_count": 0, "micro_gap_ignored_count": 0, "transition_buffer_count": 0, "split_count": 0}
    boundary_points: set[float] = set()
    packaged_blocks = 0
    transition_buffers = 0
    split_count = 0
    scene_targets: list[dict[str, Any]] = []
    for window in gap_windows:
        t0 = _to_float(window.get("t0"), 0.0)
        t1 = _to_float(window.get("t1"), t0)
        duration = max(0.0, t1 - t0)
        if duration < scene_min:
            transition_buffers += 1
            continue
        boundary_points.update({round(t0, 3), round(t1, 3)})
        target_scene_count = 1
        if duration >= three_scene_min:
            target_scene_count = 3
        elif duration >= multi_min:
            target_scene_count = 2
        internal_splits = _build_instrumental_block_internal_splits(
            t0=t0,
            t1=t1,
            target_scene_count=target_scene_count,
            candidate_cut_points=candidate_cut_points,
            no_split_ranges=no_split_ranges,
        )
        if target_scene_count > 1 and not internal_splits:
            fallback_target = max(1, target_scene_count - 1)
            internal_splits = _build_instrumental_block_internal_splits(
                t0=t0,
                t1=t1,
                target_scene_count=fallback_target,
                candidate_cut_points=candidate_cut_points,
                no_split_ranges=no_split_ranges,
            )
        boundary_points.update(internal_splits)
        split_count += len(internal_splits)
        packaged_blocks += 1
        scene_targets.append({"t0": round(t0, 3), "t1": round(t1, 3), "position": str(window.get("position") or "middle")})
    if not boundary_points:
        return segments, {
            "detected_gap_count": len(gap_windows),
            "scene_block_count": 0,
            "micro_gap_ignored_count": max(0, len(gap_windows) - transition_buffers),
            "transition_buffer_count": transition_buffers,
            "split_count": 0,
        }
    split_points = sorted(boundary_points)
    repartitioned: list[dict[str, Any]] = []
    for seg in segments:
        row = _safe_dict(seg)
        seg_t0 = _to_float(row.get("t0"), 0.0)
        seg_t1 = _to_float(row.get("t1"), seg_t0)
        internal_points = [point for point in split_points if seg_t0 + 0.01 < point < seg_t1 - 0.01]
        if not internal_points:
            repartitioned.append(row)
            continue
        cut_chain = [seg_t0, *internal_points, seg_t1]
        for idx in range(len(cut_chain) - 1):
            left, right = round(cut_chain[idx], 3), round(cut_chain[idx + 1], 3)
            if right - left <= 0.05:
                continue
            piece = _safe_dict(deepcopy(row))
            piece["t0"] = left
            piece["t1"] = right
            piece["duration_sec"] = round(max(0.0, right - left), 3)
            repartitioned.append(piece)
    for seg in repartitioned:
        seg_t0 = _to_float(seg.get("t0"), 0.0)
        seg_t1 = _to_float(seg.get("t1"), seg_t0)
        for block in scene_targets:
            block_t0 = _to_float(block.get("t0"), 0.0)
            block_t1 = _to_float(block.get("t1"), block_t0)
            if seg_t0 >= block_t0 - 0.01 and seg_t1 <= block_t1 + 0.01:
                seg["is_lip_sync_candidate"] = False
                seg["transcript_slice"] = ""
                seg["lyrical_density"] = "low"
                route_hints = _safe_dict(seg.get("route_hints"))
                route_hints["lip_sync_fit"] = "instrumental_gap"
                route_hints["preferred_routes"] = ["i2v", "world", "transition", "atmosphere"]
                seg["route_hints"] = route_hints
                seg["scene_packaging_unit"] = "instrumental_gap"
                seg["instrumental_gap_position"] = str(block.get("position") or "middle")
                break
    return repartitioned, {
        "detected_gap_count": len(gap_windows),
        "scene_block_count": packaged_blocks,
        "micro_gap_ignored_count": max(0, len(gap_windows) - packaged_blocks - transition_buffers),
        "transition_buffer_count": transition_buffers,
        "split_count": split_count,
    }


def _rename_segments_canon(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    prev_intensity = 0.0
    for idx, raw in enumerate(segments, start=1):
        seg = _safe_dict(deepcopy(raw))
        t0 = round(_to_float(seg.get("t0"), 0.0), 3)
        t1 = round(_to_float(seg.get("t1"), t0), 3)
        seg["segment_id"] = f"seg_{idx:02d}"
        seg["t0"] = t0
        seg["t1"] = t1
        seg["duration_sec"] = round(max(0.0, t1 - t0), 3)
        intensity = _to_float(seg.get("intensity"), prev_intensity)
        seg["energy_delta_vs_prev"] = "reset" if idx == 1 else str(seg.get("energy_delta_vs_prev") or ("rise" if intensity > prev_intensity else "hold")).strip().lower()
        prev_intensity = intensity
        out.append(seg)
    return out


def _merge_short_adjacent_vocal_windows(segments: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    policy = SCENE_PACKAGING_POLICY
    merged: list[dict[str, Any]] = []
    idx = 0
    merge_count = 0
    while idx < len(segments):
        current = _safe_dict(segments[idx])
        if idx + 1 >= len(segments):
            merged.append(current)
            break
        nxt = _safe_dict(segments[idx + 1])
        dur_a = _segment_duration(current)
        dur_b = _segment_duration(nxt)
        gap = max(0.0, round(_to_float(nxt.get("t0"), 0.0) - _to_float(current.get("t1"), 0.0), 3))
        combined_dur = round(max(0.0, _to_float(nxt.get("t1"), 0.0) - _to_float(current.get("t0"), 0.0)), 3)
        can_merge = bool(
            bool(current.get("is_lip_sync_candidate"))
            and bool(nxt.get("is_lip_sync_candidate"))
            and (policy["short_vocal_merge_min_sec"] <= dur_a <= policy["short_vocal_merge_max_sec"])
            and (policy["short_vocal_merge_min_sec"] <= dur_b <= policy["short_vocal_merge_max_sec"])
            and gap <= policy["adjacent_vocal_max_gap_sec"]
            and combined_dur <= policy["short_vocal_merged_max_sec"]
            and not _is_semantic_break_between(current, nxt)
        )
        if not can_merge:
            merged.append(current)
            idx += 1
            continue
        joined = _safe_dict(deepcopy(current))
        joined["t1"] = round(_to_float(nxt.get("t1"), _to_float(current.get("t1"), 0.0)), 3)
        joined["duration_sec"] = round(max(0.0, _to_float(joined.get("t1"), 0.0) - _to_float(joined.get("t0"), 0.0)), 3)
        a_text = str(current.get("transcript_slice") or "").strip()
        b_text = str(nxt.get("transcript_slice") or "").strip()
        joined["transcript_slice"] = f"{a_text} {b_text}".strip()
        joined["semantic_turn_candidate"] = False
        joined["release_candidate"] = bool(nxt.get("release_candidate"))
        joined["finality_candidate"] = str(nxt.get("finality_candidate") or current.get("finality_candidate") or "none")
        route_hints = _safe_dict(current.get("route_hints"))
        route_hints["lip_sync_fit"] = "good"
        joined["route_hints"] = route_hints
        merged.append(joined)
        merge_count += 1
        idx += 2
    return merged, merge_count


def _split_long_world_windows(
    segments: list[dict[str, Any]],
    *,
    phrase_units: list[dict[str, Any]],
    candidate_cut_points: list[float],
    no_split_ranges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    policy = SCENE_PACKAGING_POLICY
    split_count = 0
    out: list[dict[str, Any]] = []
    unit_cut_points = sorted(
        {
            round(_to_float(_safe_dict(unit).get("t1"), -1.0), 3)
            for unit in phrase_units
            if _to_float(_safe_dict(unit).get("t1"), -1.0) > 0.0
        }
    )
    generic_cut_points = sorted({round(_to_float(v, -1.0), 3) for v in candidate_cut_points if _to_float(v, -1.0) > 0.0})
    all_cut_points = sorted(set(unit_cut_points + generic_cut_points))
    for seg_raw in segments:
        seg = _safe_dict(seg_raw)
        if not _is_non_vocal_world_candidate(seg):
            out.append(seg)
            continue
        duration = _segment_duration(seg)
        if duration < policy["long_world_split_min_sec"]:
            out.append(seg)
            continue
        t0 = _to_float(seg.get("t0"), 0.0)
        t1 = _to_float(seg.get("t1"), t0)
        midpoint = t0 + (t1 - t0) / 2.0
        gap_cut_points = _derive_gap_split_candidates_from_no_split_ranges(
            window_t0=t0,
            window_t1=t1,
            no_split_ranges=no_split_ranges,
        )
        candidate_pool = sorted(set(all_cut_points + gap_cut_points))
        candidates: list[tuple[float, float, float]] = []
        for cut in candidate_pool:
            if not (t0 + 0.01 < cut < t1 - 0.01):
                continue
            if _is_inside_no_split(cut, no_split_ranges):
                continue
            left = round(cut - t0, 3)
            right = round(t1 - cut, 3)
            if left < policy["world_split_part_min_sec"] or right < policy["world_split_part_min_sec"]:
                continue
            if left > policy["world_split_part_hard_max_sec"] or right > policy["world_split_part_hard_max_sec"]:
                continue
            penalty = abs(left - right) + abs(cut - midpoint) * 0.2
            candidates.append((penalty, cut, abs(cut - midpoint)))
        if not candidates:
            out.append(seg)
            continue
        candidates.sort(key=lambda item: item[0])
        cut = candidates[0][1]
        left_seg = _safe_dict(deepcopy(seg))
        right_seg = _safe_dict(deepcopy(seg))
        left_seg["t1"] = round(cut, 3)
        left_seg["duration_sec"] = round(max(0.0, cut - t0), 3)
        right_seg["t0"] = round(cut, 3)
        right_seg["duration_sec"] = round(max(0.0, t1 - cut), 3)
        left_seg["first_last_candidate"] = False
        right_seg["first_last_candidate"] = bool(seg.get("first_last_candidate"))
        left_seg["release_candidate"] = False
        right_seg["semantic_turn_candidate"] = False
        out.extend([left_seg, right_seg])
        split_count += 1
    return out, split_count


def _apply_scene_packaging_policy_to_audio_segments(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    patched = _safe_dict(deepcopy(payload))
    segments = [_safe_dict(row) for row in _safe_list(patched.get("segments")) if isinstance(row, dict)]
    if not segments:
        return patched, {"enabled": True, "merge_count": 0, "split_count": 0, "segment_count_before": 0, "segment_count_after": 0}
    phrase_units = [_safe_dict(row) for row in _safe_list(patched.get("phrase_units")) if isinstance(row, dict)]
    candidate_cut_points = [float(v) for v in _safe_list(patched.get("candidate_cut_points_sec")) if isinstance(v, (int, float))]
    no_split_ranges = [_safe_dict(row) for row in _safe_list(patched.get("no_split_ranges")) if isinstance(row, dict)]
    merged, merge_count = _merge_short_adjacent_vocal_windows(segments)
    split, split_count = _split_long_world_windows(
        merged,
        phrase_units=phrase_units,
        candidate_cut_points=candidate_cut_points,
        no_split_ranges=no_split_ranges,
    )
    packaged, instrumental_diag = _package_instrumental_gap_windows(
        split,
        phrase_units=phrase_units,
        candidate_cut_points=candidate_cut_points,
        no_split_ranges=no_split_ranges,
    )
    patched["segments"] = _rename_segments_canon(packaged)
    diag = {
        "enabled": True,
        "policy": dict(SCENE_PACKAGING_POLICY),
        "merge_count": merge_count,
        "split_count": split_count,
        "instrumental_gap_detected_count": int(instrumental_diag.get("detected_gap_count") or 0),
        "instrumental_gap_scene_block_count": int(instrumental_diag.get("scene_block_count") or 0),
        "instrumental_gap_transition_buffer_count": int(instrumental_diag.get("transition_buffer_count") or 0),
        "instrumental_gap_micro_ignored_count": int(instrumental_diag.get("micro_gap_ignored_count") or 0),
        "instrumental_gap_internal_split_count": int(instrumental_diag.get("split_count") or 0),
        "segment_count_before": len(segments),
        "segment_count_after": len(_safe_list(patched.get("segments"))),
    }
    return patched, diag


def _build_legacy_compat_audio_payload_from_segments_v11(payload: dict[str, Any], *, duration_sec: float, analysis_mode: str) -> dict[str, Any]:
    """
    Build temporary legacy compatibility fields from AUDIO v1.1 segments.

    Canonical AUDIO source of truth is `segments[]` (Gemini-authored). Derived fields
    (`sections`, `phrase_units`, `scene_candidate_windows`, `candidate_cut_points_sec`)
    exist only as a deprecated compatibility bridge for downstream consumers that have
    not migrated to direct `segments[]` consumption yet.
    """
    segments = [item for item in _safe_list(payload.get("segments")) if isinstance(item, dict)]
    phrase_units: list[dict[str, Any]] = []
    scene_candidate_windows: list[dict[str, Any]] = []
    sections: list[dict[str, Any]] = []
    phrase_endpoints: list[float] = []
    energy_band_counts: dict[str, int] = {}
    delivery_mode_counts: dict[str, int] = {}
    semantic_weight_counts: dict[str, int] = {}
    finality_count = 0
    finality_distribution: dict[str, int] = {"none": 0, "continuation": 0, "hinge": 0, "closure": 0, "tail_hit": 0}
    release_count = 0
    stillness_count = 0
    semantic_turn_count = 0
    energy_delta_non_hold = 0
    for idx, seg in enumerate(segments, start=1):
        t0 = round(_to_float(seg.get("t0"), 0.0), 3)
        t1 = round(_to_float(seg.get("t1"), t0), 3)
        if t1 <= t0:
            continue
        text = str(seg.get("transcript_slice") or "").strip()
        intensity = _to_float(seg.get("intensity"), 0.0)
        rhythmic_anchor = str(seg.get("rhythmic_anchor") or "none").strip().lower() or "none"
        if rhythmic_anchor not in {"beat", "drop", "transition", "none"}:
            rhythmic_anchor = "none"
        inferred = _derive_audio_hint_profile(idx=idx, segments=segments, t0=t0, t1=t1, intensity=intensity, text=text)
        energy = inferred["intensity_bucket"]
        local_energy_band = str(seg.get("local_energy_band") or inferred["local_energy_band"]).strip().lower()
        energy_delta = str(seg.get("energy_delta_vs_prev") or inferred["energy_delta_vs_prev"]).strip().lower()
        if idx == 1:
            energy_delta = "reset"
        delivery_mode = str(seg.get("delivery_mode") or inferred["delivery_mode"]).strip().lower()
        semantic_weight = str(seg.get("semantic_weight") or inferred["semantic_weight"]).strip().lower()
        semantic_turn_candidate = bool(seg.get("semantic_turn_candidate")) if seg.get("semantic_turn_candidate") is not None else bool(inferred["semantic_turn_candidate"])
        release_candidate = bool(seg.get("release_candidate")) if seg.get("release_candidate") is not None else bool(inferred["release_candidate"])
        finality_candidate = str(seg.get("finality_candidate") or inferred["finality_candidate"]).strip().lower()
        visual_density_hint = str(seg.get("visual_density_hint") or inferred["visual_density_hint"]).strip().lower()
        stillness_candidate = bool(seg.get("stillness_candidate")) if seg.get("stillness_candidate") is not None else bool(inferred["stillness_candidate"])
        lyrical_density = str(seg.get("lyrical_density") or inferred["lyrical_density"]).strip().lower()
        duration = round(max(0.0, t1 - t0), 3)
        if finality_candidate not in {"none", "continuation", "hinge", "closure", "tail_hit"}:
            finality_candidate = "none"
        if idx != len(segments) and finality_candidate in {"closure", "tail_hit"}:
            finality_candidate = "none"
        if idx == len(segments) and finality_candidate == "hinge":
            finality_candidate = "closure" if release_candidate else "none"
        if stillness_candidate:
            stillness_candidate = bool(
                release_candidate
                or delivery_mode in {"reflective", "intimate", "suspended", "final", "observational"}
                or finality_candidate in {"closure", "tail_hit"}
            )
        if not stillness_candidate and release_candidate and delivery_mode in {"reflective", "intimate", "suspended", "final", "observational"}:
            stillness_candidate = True
        first_last_candidate = bool(seg.get("first_last_candidate"))
        derived_first_last = _is_first_last_candidate_from_hints(
            idx=idx,
            total=len(segments),
            duration=duration,
            local_energy_band=local_energy_band,
            delivery_mode=delivery_mode,
            finality_candidate=finality_candidate,
            semantic_turn_candidate=semantic_turn_candidate,
        )
        first_last_candidate = bool(first_last_candidate and derived_first_last) or bool(derived_first_last)
        energy = _legacy_energy_from_local_band(local_energy_band, energy)
        energy_band_counts[local_energy_band] = energy_band_counts.get(local_energy_band, 0) + 1
        delivery_mode_counts[delivery_mode] = delivery_mode_counts.get(delivery_mode, 0) + 1
        semantic_weight_counts[semantic_weight] = semantic_weight_counts.get(semantic_weight, 0) + 1
        semantic_turn_count += 1 if semantic_turn_candidate else 0
        release_count += 1 if release_candidate else 0
        stillness_count += 1 if stillness_candidate else 0
        finality_count += 1 if finality_candidate != "none" else 0
        finality_distribution[finality_candidate] = finality_distribution.get(finality_candidate, 0) + 1
        energy_delta_non_hold += 1 if energy_delta in {"rise", "soften", "release", "spike", "reset"} else 0
        seg["local_energy_band"] = local_energy_band
        seg["energy_delta_vs_prev"] = energy_delta
        seg["delivery_mode"] = delivery_mode
        seg["semantic_weight"] = semantic_weight
        seg["semantic_turn_candidate"] = semantic_turn_candidate
        seg["release_candidate"] = release_candidate
        seg["finality_candidate"] = finality_candidate
        seg["visual_density_hint"] = visual_density_hint
        seg["stillness_candidate"] = stillness_candidate
        seg["lyrical_density"] = lyrical_density
        seg["first_last_candidate"] = first_last_candidate
        phrase_units.append(
            {
                "id": f"ph_{idx}",
                "t0": t0,
                "t1": t1,
                "duration_sec": duration,
                "text": text,
                "word_count": int(inferred["word_count"]),
                "semantic_weight": semantic_weight,
                "delivery_mode": delivery_mode,
                "local_energy_band": local_energy_band,
                "energy_delta_vs_prev": energy_delta,
                "semantic_turn_candidate": semantic_turn_candidate,
                "release_candidate": release_candidate,
                "finality_candidate": finality_candidate,
                "visual_density_hint": visual_density_hint,
                "stillness_candidate": stillness_candidate,
                "lyrical_density": lyrical_density,
                "first_last_candidate": first_last_candidate,
            }
        )
        scene_candidate_windows.append(
            {
                "id": f"sc_{idx}",
                "t0": t0,
                "t1": t1,
                "duration_sec": duration,
                "phrase_text": text,
                "transcript_confidence": "high" if text else "low",
                "cut_reason": rhythmic_anchor,
                "energy": energy,
                "scene_function": rhythmic_anchor if rhythmic_anchor in {"beat", "drop", "transition"} else "bridge",
                "no_mid_word_cut": True,
                "local_energy_band": local_energy_band,
                "energy_delta_vs_prev": energy_delta,
                "delivery_mode": delivery_mode,
                "semantic_weight": semantic_weight,
                "semantic_turn_candidate": semantic_turn_candidate,
                "release_candidate": release_candidate,
                "finality_candidate": finality_candidate,
                "visual_density_hint": visual_density_hint,
                "stillness_candidate": stillness_candidate,
                "lyrical_density": lyrical_density,
                "first_last_candidate": first_last_candidate,
            }
        )
        mood = "tense" if delivery_mode in {"assertive", "pressurized"} else ("contemplative" if delivery_mode in {"reflective", "intimate", "suspended"} else "neutral")
        sections.append(
            {
                "id": f"sec_{idx}",
                "t0": t0,
                "t1": t1,
                "label": rhythmic_anchor or "segment",
                "energy": energy,
                "mood": mood,
                "local_energy_band": local_energy_band,
                "delivery_mode": delivery_mode,
                "semantic_weight": semantic_weight,
                "finality_candidate": finality_candidate,
                "release_candidate": release_candidate,
                "stillness_candidate": stillness_candidate,
                "first_last_candidate": first_last_candidate,
            }
        )
        if 0.0 < t1 < duration_sec:
            phrase_endpoints.append(round(t1, 3))

    no_split_ranges = []
    for row in _safe_list(payload.get("no_split_ranges")):
        item = _safe_dict(row)
        no_split_ranges.append(
            {
                "t0": round(_to_float(item.get("start"), 0.0), 3),
                "t1": round(_to_float(item.get("end"), 0.0), 3),
                "reason": "model_guardrail",
            }
        )
    total_segments = max(1, len(segments))
    local_energy_variation_score = round(min(1.0, (len(energy_band_counts) * 0.22) + (energy_delta_non_hold / total_segments) * 0.5), 4)
    contrast_summary = (
        f"energy_bands={sorted(energy_band_counts.keys())}; "
        f"delivery_modes={sorted(delivery_mode_counts.keys())}; "
        f"release={release_count}/{total_segments}; stillness={stillness_count}/{total_segments}; semantic_turns={semantic_turn_count}"
    )
    progression_summary = (
        f"non_hold_deltas={energy_delta_non_hold}/{total_segments}; "
        f"finality_non_none={finality_count}; "
        f"semantic_weight_high={semantic_weight_counts.get('high', 0)}"
    )
    diagnostics = _safe_dict(payload.get("diagnostics"))
    diagnostics["audio_map_local_energy_variation_score"] = local_energy_variation_score
    diagnostics["audio_map_delivery_mode_distribution"] = dict(sorted(delivery_mode_counts.items()))
    diagnostics["audio_map_semantic_weight_distribution"] = {
        "low": semantic_weight_counts.get("low", 0),
        "medium": semantic_weight_counts.get("medium", 0),
        "high": semantic_weight_counts.get("high", 0),
    }
    diagnostics["audio_map_semantic_turn_candidate_count"] = semantic_turn_count
    diagnostics["audio_map_release_candidate_count"] = release_count
    diagnostics["audio_map_stillness_candidate_count"] = stillness_count
    diagnostics["audio_map_finality_candidate_count"] = finality_count
    diagnostics["audio_map_finality_candidate_distribution"] = {
        "none": finality_distribution.get("none", 0),
        "continuation": finality_distribution.get("continuation", 0),
        "hinge": finality_distribution.get("hinge", 0),
        "closure": finality_distribution.get("closure", 0),
        "tail_hit": finality_distribution.get("tail_hit", 0),
    }
    first_last_count = sum(1 for seg in segments if bool(seg.get("first_last_candidate")))
    diagnostics["audio_map_first_last_candidate_count"] = first_last_count
    diagnostics["audio_map_overassigned_finality_warning"] = bool(
        len(segments) >= 3 and finality_count > max(1, int(round(len(segments) * 0.5)))
    )
    diagnostics["audio_map_overassigned_first_last_warning"] = bool(
        len(segments) >= 3 and first_last_count > max(1, int(round(len(segments) * 0.5)))
    )
    diagnostics["audio_map_flat_energy_warning"] = bool(len(energy_band_counts) <= 1 and energy_delta_non_hold <= 1)
    diagnostics["audio_map_flat_delivery_warning"] = bool(len(delivery_mode_counts) <= 1)
    diagnostics["audio_map_flat_semantic_weight_warning"] = bool(sum(1 for v in semantic_weight_counts.values() if v > 0) <= 1)
    diagnostics["audio_map_contrast_potential_summary"] = contrast_summary
    diagnostics["audio_map_progression_hint_summary"] = progression_summary

    return {
        "audio_map_version": "1.1",
        "audio_id": str(payload.get("audio_id") or ""),
        "vocal_profile": _safe_dict(payload.get("vocal_profile")),
        "vocal_gender": str(payload.get("vocal_gender") or "unknown").strip().lower() or "unknown",
        "vocal_owner_role": str(payload.get("vocal_owner_role") or "unknown").strip() or "unknown",
        "vocal_owner_confidence": _to_float(payload.get("vocal_owner_confidence"), 0.0),
        "duration_sec": round(duration_sec, 3),
        "analysis_mode": analysis_mode,
        "diagnostics": diagnostics,
        "segments": segments,
        "sections": sections,
        "phrase_endpoints_sec": sorted(set(phrase_endpoints)),
        "phrase_units": phrase_units,
        "scene_candidate_windows": scene_candidate_windows,
        "no_split_ranges": no_split_ranges,
        "candidate_cut_points_sec": sorted(set(phrase_endpoints)),
        "transcript_available": bool(_safe_dict(payload.get("diagnostics")).get("transcript_used")),
        "audio_map_alignment_source": "gemini_audio_map_v1_1",
    }


def _run_audio_map_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    story_core = _safe_dict(package.get("story_core"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    diagnostics = _safe_dict(package.get("diagnostics"))

    audio_url = str(input_pkg.get("audio_url") or "").strip()
    duration_sec = _coerce_duration_sec(input_pkg.get("audio_duration_sec"))
    content_type = str(input_pkg.get("content_type") or "").strip().lower() or "music_video"
    director_mode = _resolve_director_mode(input_pkg.get("director_mode"), content_type=content_type)
    story_core_mode = str(diagnostics.get("story_core_mode") or _detect_story_core_mode(input_pkg)).strip().lower() or "creative"

    diagnostics["audio_map_source_audio_url"] = audio_url
    diagnostics["audio_map_used_model"] = ""
    diagnostics["audio_map_analysis_mode"] = "audio_map_v1_1_strict"
    diagnostics["audio_map_used_fallback"] = False
    diagnostics["audio_map_phrase_mode"] = "audio_map_v1_1_strict"
    diagnostics["audio_map_segmentation_backend"] = "gemini"
    diagnostics["audio_map_segmentation_used_fallback"] = False
    diagnostics["audio_map_segmentation_validation_error"] = ""
    diagnostics["audio_map_segmentation_validation_errors"] = []
    diagnostics["audio_map_segmentation_prompt_version"] = ""
    diagnostics["audio_map_segmentation_error"] = ""
    diagnostics["audio_map_segmentation_error_detail"] = ""
    diagnostics["audio_map_segmentation_payload_summary"] = {}
    diagnostics["audio_map_segmentation_normalized_summary"] = {}
    diagnostics["audio_map_segmentation_retry_used"] = False
    diagnostics["audio_map_segmentation_retry_feedback"] = ""
    diagnostics["audio_map_segmentation_last_error_code"] = ""
    diagnostics["audio_map_segmentation_last_errors"] = []
    diagnostics["audio_map_video_ready_validation"] = False
    diagnostics["audio_map_short_segments_found"] = []
    diagnostics["audio_map_validation_error"] = ""
    diagnostics["audio_segmentation_source_mode"] = "none"
    diagnostics["audio_segmentation_local_path_found"] = False
    diagnostics["audio_segmentation_inline_attempted"] = False
    diagnostics["audio_segmentation_inline_bytes_size"] = 0
    diagnostics["audio_segmentation_url_used"] = ""
    diagnostics["audio_segmentation_transport_error"] = ""
    diagnostics["audio_map_stage_branch"] = ""
    diagnostics["audio_map_primary_fallback_reason"] = ""
    diagnostics["audio_map_dynamics_error"] = ""
    diagnostics["audio_map_primary_source"] = "gemini"
    diagnostics["audio_map_model_led_segmentation"] = True
    diagnostics["audio_map_validation_flags"] = []
    diagnostics["audio_map_source_of_truth"] = "segments_v1_1"
    diagnostics["audio_map_legacy_compat_bridge"] = True
    diagnostics["audio_map_legacy_scene_slots_derived"] = False
    diagnostics["audio_map_legacy_scene_slots_deprecated"] = True
    diagnostics["audio_map_gap_sum_sec"] = 0.0
    diagnostics["audio_map_overlap_sum_sec"] = 0.0
    diagnostics["phrase_near_4_sec_count"] = 0
    diagnostics["phrase_near_4_sec_ratio"] = 0.0
    diagnostics["equal_phrase_duration_adjacent_pairs"] = 0
    diagnostics["max_equal_phrase_duration_streak"] = 0
    diagnostics["scene_near_4_sec_count"] = 0
    diagnostics["scene_near_4_sec_ratio"] = 0.0
    diagnostics["max_equal_scene_duration_streak"] = 0
    diagnostics["audio_map_grid_like_segmentation"] = False
    diagnostics["audio_map_music_signal_mode"] = "none"
    diagnostics["audio_map_dynamics_available"] = False
    diagnostics["audio_map_vocal_gender"] = "unknown"
    diagnostics["audio_map_vocal_owner_role"] = "unknown"
    diagnostics["audio_map_vocal_owner_confidence"] = 0.0
    diagnostics["audio_map_voice_reason"] = ""
    diagnostics["transcript_available"] = False
    diagnostics["word_timestamp_count"] = 0
    diagnostics["phrase_unit_count"] = 0
    diagnostics["scene_candidate_count"] = 0
    diagnostics["gemini_api_key_source"] = "missing"
    diagnostics["gemini_api_key_valid"] = False
    diagnostics["gemini_api_key_error"] = "empty"
    package["diagnostics"] = diagnostics
    gemini_api_key = _resolve_stage_gemini_api_key(package, stage_id="audio_map")

    if duration_sec <= 0:
        raise RuntimeError("AUDIO_TIMING_VIOLATION:audio_duration_missing_or_invalid")

    transcript_text = _extract_audio_transcript_text(input_pkg)
    role_identity_mapping_payload = _extract_role_identity_mapping_payload(input_pkg)
    role_identity_expectations = _extract_role_identity_expectations(input_pkg, _safe_dict(input_pkg.get("assigned_roles")))
    raw_analysis: dict[str, Any] = {}
    analysis_path = ""
    cleanup_path: str | None = None
    if audio_url:
        local_audio_path, local_resolution_debug = _resolve_local_audio_asset_path(
            audio_url=audio_url,
            input_payload=input_pkg,
            refs_inventory=refs_inventory,
        )
        _append_diag_event(
            package,
            "audio_map local audio resolution "
            f"audio_url={str(local_resolution_debug.get('audio_url') or '')} "
            f"asset_rel={str(local_resolution_debug.get('extracted_asset_relative_path') or '')} "
            f"local_path={str(local_resolution_debug.get('final_local_path') or '')} "
            f"exists={bool(local_resolution_debug.get('exists'))} "
            f"basename_fallback={bool(local_resolution_debug.get('fallback_to_basename_lookup'))}",
            stage_id="audio_map",
        )
        analysis_path, cleanup_path = _resolve_audio_analysis_path(local_audio_path or audio_url)
        if analysis_path:
            try:
                raw_analysis = analyze_audio(analysis_path, debug=False)
            except Exception as analysis_exc:  # noqa: BLE001
                diagnostics["audio_map_dynamics_error"] = str(analysis_exc)
                raw_analysis = {}
    diagnostics["audio_map_stage_branch"] = "gemini_strict_v11"

    def _collect_short_segments(errors: list[str]) -> list[str]:
        return [
            str(item)
            for item in errors
            if "duration_sec must be >= 3.0" in str(item)
            or "too short for standalone video segment" in str(item)
            or "<2.8s without natural tail/reaction evidence" in str(item)
            or "AUDIO_MAP_INVALID_SHORT_SEGMENT" in str(item)
        ]

    def _validation_feedback(code: str, errors: list[str]) -> str:
        short_segments = _collect_short_segments(errors)
        if short_segments:
            parsed: list[str] = []
            for msg in short_segments[:6]:
                match = re.search(r"segment\[(\d+)\].*?:\s*([0-9]+(?:\.[0-9]+)?)\s*$", msg)
                if not match:
                    continue
                seg_num = int(match.group(1)) + 1
                seg_id = f"seg_{seg_num:02d}"
                parsed.append(f"{seg_id} duration={float(match.group(2)):.3f}")
            short_summary = "; ".join(parsed) if parsed else "; ".join(short_segments[:6])
            return (
                "Your previous audio_map violated video-ready segmentation. "
                f"These segments are too short as standalone video windows: {short_summary}. "
                "Do not pad or cut mid-word. Merge short phrase_units with adjacent phrase/pause/reaction into natural compact video windows. "
                "Keep i2v windows compact when possible. first_last_candidate requires >=4.0 sec."
            )[:1500]
        if code == "AUDIO_MAP_INVALID_FIRST_LAST_DURATION":
            detail = "; ".join([str(item) for item in errors[:6]])
            return (
                "Your previous audio_map violated first_last duration rules. "
                f"{detail}. first_last_candidate may be true only when duration_sec >= 4.0."
            )[:1500]
        if code == "AUDIO_MAP_INVALID_TIMELINE":
            detail = "; ".join([str(item) for item in errors[:6]])
            return (
                "Your previous audio_map violated timeline consistency (coverage/order/overlap/gap). "
                f"{detail}. Return contiguous full-coverage segments with no overlaps or gaps."
            )[:1500]
        detail = "; ".join([str(item) for item in errors[:6]])
        prefix = "Hard boundary violation. " if code == "AUDIO_PLOT_LEAKAGE" else ""
        return f"{prefix}{code}: {detail}"[:1500]

    strict_result = None
    last_error_code = "AUDIO_SCHEMA_INVALID"
    last_errors: list[str] = []
    validation_feedback = ""
    retry_used = False
    try:
        for attempt in range(2):
            gemini_result = build_gemini_audio_segmentation(
                api_key=gemini_api_key,
                audio_path=analysis_path,
                audio_url=audio_url,
                duration_sec=duration_sec,
                audio_id=str(input_pkg.get("job_id") or input_pkg.get("id") or "audio_source"),
                transcript_text=transcript_text,
                dynamics_summary=_safe_dict(raw_analysis.get("summary")),
                role_identity_mapping=role_identity_mapping_payload,
                validation_feedback=validation_feedback,
            )
            diagnostics["audio_map_segmentation_prompt_version"] = str(gemini_result.get("prompt_version") or "")
            diagnostics["audio_map_used_model"] = str(gemini_result.get("used_model") or diagnostics.get("audio_map_used_model") or "")
            transport_meta = _safe_dict(gemini_result.get("transport_meta"))
            diagnostics["audio_segmentation_source_mode"] = str(transport_meta.get("audio_segmentation_source_mode") or "none")
            diagnostics["audio_segmentation_local_path_found"] = bool(transport_meta.get("audio_segmentation_local_path_found"))
            diagnostics["audio_segmentation_inline_attempted"] = bool(transport_meta.get("audio_segmentation_inline_attempted"))
            diagnostics["audio_segmentation_inline_bytes_size"] = int(transport_meta.get("audio_segmentation_inline_bytes_size") or 0)
            diagnostics["audio_segmentation_url_used"] = str(transport_meta.get("audio_segmentation_url_used") or "")
            diagnostics["audio_segmentation_transport_error"] = str(transport_meta.get("audio_segmentation_transport_error") or "")
            diagnostics["audio_map_segmentation_raw_response"] = str(gemini_result.get("raw_text") or "")
            diagnostics["audio_map_segmentation_retry_used"] = retry_used

            if not bool(gemini_result.get("ok")):
                last_error_code = "AUDIO_SCHEMA_INVALID"
                last_errors = [str(gemini_result.get("error") or "gemini_generation_failed")]
                validation_feedback = _validation_feedback(last_error_code, last_errors)
                diagnostics["audio_map_segmentation_last_error_code"] = last_error_code
                diagnostics["audio_map_segmentation_last_errors"] = last_errors
                diagnostics["audio_map_segmentation_retry_feedback"] = validation_feedback
            else:
                gemini_payload = _safe_dict(gemini_result.get("payload"))
                strict_result = validate_audio_map_v11(gemini_payload, audio_duration_sec=duration_sec)
                diagnostics["audio_map_segmentation_normalized_summary"] = _summarize_audio_map_v11(strict_result.normalized or gemini_payload)
                diagnostics["audio_map_segmentation_validation_error"] = strict_result.error_code if not strict_result.ok else ""
                diagnostics["audio_map_segmentation_validation_errors"] = strict_result.errors
                diagnostics["audio_map_segmentation_last_error_code"] = strict_result.error_code if not strict_result.ok else ""
                diagnostics["audio_map_segmentation_last_errors"] = strict_result.errors if not strict_result.ok else []
                diagnostics["audio_map_short_segments_found"] = _collect_short_segments(strict_result.errors if not strict_result.ok else [])
                diagnostics["audio_map_video_ready_validation"] = bool(strict_result.ok)
                diagnostics["audio_map_validation_error"] = strict_result.error_code if not strict_result.ok else ""
                if strict_result.ok:
                    normalized_diag = _safe_dict(strict_result.normalized.get("diagnostics")) if isinstance(strict_result.normalized, dict) else {}
                    diagnostics["audio_map_gap_sum_sec"] = _to_float(normalized_diag.get("gap_sum_sec"), 0.0)
                    diagnostics["audio_map_overlap_sum_sec"] = _to_float(normalized_diag.get("overlap_sum_sec"), 0.0)
                    break
                last_error_code = strict_result.error_code
                last_errors = strict_result.errors
                validation_feedback = _validation_feedback(last_error_code, last_errors)
                diagnostics["audio_map_segmentation_retry_feedback"] = validation_feedback

            if attempt == 0:
                retry_used = True
                diagnostics["audio_map_segmentation_retry_used"] = True
                _append_diag_event(package, f"audio_map strict validation failed, requesting one retry: {validation_feedback}", stage_id="audio_map")
                continue
            break

        if not strict_result or not strict_result.ok:
            if last_error_code == "AUDIO_MAP_INVALID_SHORT_SEGMENT":
                diagnostics["audio_map_validation_error"] = "AUDIO_MAP_INVALID_SHORT_SEGMENT"
            raise RuntimeError(f"{last_error_code}:{'; '.join(last_errors[:3])}")

        packaged_audio_payload, packaging_diag = _apply_scene_packaging_policy_to_audio_segments(strict_result.normalized)
        packaged_validation = validate_audio_map_v11(packaged_audio_payload, audio_duration_sec=duration_sec)
        if packaged_validation.ok:
            strict_result = packaged_validation
        else:
            packaging_diag["fallback_reason"] = "packaging_validation_failed_keep_original_segments"
            packaging_diag["fallback_error_code"] = str(packaged_validation.error_code or "")
            packaging_diag["fallback_errors"] = _safe_list(packaged_validation.errors)

        diagnostics["audio_map_scene_packaging_policy"] = packaging_diag
        audio_map = _build_legacy_compat_audio_payload_from_segments_v11(
            strict_result.normalized,
            duration_sec=duration_sec,
            analysis_mode="audio_map_v1_1_strict",
        )
    finally:
        if cleanup_path:
            try:
                os.unlink(cleanup_path)
            except OSError:
                pass

    audio_map["content_type"] = content_type
    audio_map["director_mode"] = director_mode
    audio_map["semantic_source_type"] = _resolve_audio_semantic_source_type(input_pkg)
    audio_map["audio_truth_scope"] = "timing_plus_emotion" if director_mode == "clip" else "timing_structure"
    audio_map["story_core_mode"] = story_core_mode
    audio_map["story_core_arc_ref"] = str(story_core.get("global_arc") or "")
    audio_map["audio_dramaturgy"] = _build_audio_dramaturgy_summary(audio_map, input_pkg, content_type)
    vocal_profile = _safe_dict(audio_map.get("vocal_profile"))
    vocal_gender = str(vocal_profile.get("vocal_gender") or audio_map.get("vocal_gender") or "unknown").strip().lower()
    if vocal_gender not in {"female", "male", "mixed", "unknown"}:
        vocal_gender = "unknown"
    resolved_vocal_owner_role = _resolve_vocal_owner_role_by_gender(vocal_gender, role_identity_expectations)
    gemini_vocal_owner_role = str(vocal_profile.get("vocal_owner_role") or audio_map.get("vocal_owner_role") or "unknown").strip() or "unknown"
    vocal_owner_reason = str(vocal_profile.get("reason") or "").strip()
    vocal_owner_confidence = _to_float(vocal_profile.get("confidence"), _to_float(audio_map.get("vocal_owner_confidence"), 0.0))
    if resolved_vocal_owner_role == "unknown":
        vocal_owner_confidence = min(vocal_owner_confidence, 0.49)
    audio_map["vocal_profile"] = {
        "vocal_gender": vocal_gender,
        "vocal_owner_role": gemini_vocal_owner_role if gemini_vocal_owner_role in {"character_1", "character_2", "character_3", "unknown"} else "unknown",
        "confidence": round(max(0.0, min(1.0, vocal_owner_confidence)), 3),
        "reason": vocal_owner_reason,
    }
    audio_map["vocal_gender"] = vocal_gender
    audio_map["vocal_owner_role"] = resolved_vocal_owner_role
    audio_map["vocal_owner_confidence"] = round(max(0.0, min(1.0, vocal_owner_confidence)), 3)
    audio_map.setdefault(
        "transcript_available",
        bool(transcript_text and str(audio_map.get("analysis_mode") or "") in {"transcript_alignment_v2", "approximate_phrase_grouping_v1"}),
    )
    audio_map.setdefault("phrase_units", [])
    audio_map.setdefault("scene_candidate_windows", [])
    scene_slots, scene_slot_diag = _build_scene_slots(
        audio_map=audio_map,
        analysis=raw_analysis,
        duration_sec=duration_sec,
    )
    # Deprecated compatibility bridge: downstream should migrate to direct segments[] usage.
    for slot in scene_slots:
        if isinstance(slot, dict):
            slot.setdefault("legacy_derived", True)
            slot.setdefault("deprecated_bridge", True)
            slot.setdefault("canonical_source", "segments_v1_1")
    audio_map["scene_slots"] = scene_slots
    audio_map["audio_map_source_of_truth"] = "segments_v1_1"
    audio_map["audio_map_legacy_compat_bridge"] = True
    audio_map["audio_map_legacy_scene_slots_derived"] = True
    audio_map["audio_map_legacy_scene_slots_deprecated"] = True
    audio_map["audio_map_legacy_bridge_note"] = (
        "Derived fields (sections/phrase_units/scene_candidate_windows/scene_slots) are deprecated compatibility bridge; "
        "segments[] is canonical source of truth."
    )
    audio_map.setdefault("audio_map_alignment_source", "")
    audio_map.setdefault(
        "cut_policy",
        {
            "min_scene_sec": 3,
            "target_scene_sec_min": 3,
            "target_scene_sec_max": 6,
            "hard_max_scene_sec": 8,
            "no_mid_word_cut": True,
            "prefer_phrase_endings": True,
        },
    )
    grid_metrics = _audio_map_grid_metrics(audio_map)
    validation_flags = _validate_audio_map_soft(audio_map)
    audio_dynamics_summary = _safe_dict(audio_map.get("audio_dynamics_summary"))
    dynamics_available = bool(
        int(audio_dynamics_summary.get("pause_points_count") or 0)
        or int(audio_dynamics_summary.get("phrase_points_count") or 0)
        or int(audio_dynamics_summary.get("energy_peaks_count") or 0)
        or int(audio_dynamics_summary.get("detected_sections_count") or 0)
    )
    music_signal_mode = "dynamics+transcript" if dynamics_available else "transcript_only"
    audio_map.update(grid_metrics)
    audio_map["audio_map_primary_source"] = "gemini"
    audio_map["audio_map_model_led_segmentation"] = True
    audio_map["audio_map_validation_flags"] = validation_flags
    audio_map["audio_map_backend_repair_applied"] = False
    audio_map["audio_map_music_signal_mode"] = music_signal_mode
    audio_map["audio_map_dynamics_available"] = dynamics_available
    audio_map["audio_analyzer"] = {
        "beats": _safe_list(raw_analysis.get("beats")),
        "vocal_phrases": _safe_list(raw_analysis.get("vocalPhrases")),
        "segments": _safe_list(audio_map.get("segments")),
    }

    diagnostics["audio_map_analysis_mode"] = "audio_map_v1_1_strict"
    diagnostics["audio_map_used_fallback"] = False
    diagnostics["audio_map_primary_fallback_reason"] = ""
    diagnostics["audio_map_segmentation_backend"] = "gemini"
    diagnostics["audio_map_segmentation_used_fallback"] = False
    diagnostics["audio_map_segmentation_error"] = str(diagnostics.get("audio_map_segmentation_error") or "")
    diagnostics["audio_map_segmentation_error_detail"] = str(diagnostics.get("audio_map_segmentation_error_detail") or "")
    diagnostics["audio_map_validation_flags"] = validation_flags
    diagnostics["audio_map_primary_source"] = "gemini"
    diagnostics["audio_map_model_led_segmentation"] = True
    diagnostics["audio_map_backend_repair_applied"] = False
    diagnostics["audio_map_phrase_mode"] = str(audio_map.get("analysis_mode") or "audio_map_v1_1_strict")
    diagnostics["transcript_available"] = bool(audio_map.get("transcript_available"))
    diagnostics["word_timestamp_count"] = 0
    diagnostics["phrase_unit_count"] = len(_safe_list(audio_map.get("phrase_units")))
    diagnostics["scene_candidate_count"] = len(_safe_list(audio_map.get("scene_candidate_windows")))
    diagnostics["scene_slot_count"] = len(scene_slots)
    diagnostics["audio_map_source_of_truth"] = "segments_v1_1"
    diagnostics["audio_map_legacy_compat_bridge"] = True
    diagnostics["audio_map_legacy_scene_slots_derived"] = True
    diagnostics["audio_map_legacy_scene_slots_deprecated"] = True
    diagnostics["audio_map_legacy_bridge_note"] = str(audio_map.get("audio_map_legacy_bridge_note") or "")
    diagnostics["audio_analyzer_beats_count"] = len(_safe_list(_safe_dict(audio_map.get("audio_analyzer")).get("beats")))
    diagnostics["audio_analyzer_vocal_phrases_count"] = len(_safe_list(_safe_dict(audio_map.get("audio_analyzer")).get("vocal_phrases")))
    diagnostics["audio_analyzer_segments_count"] = len(_safe_list(_safe_dict(audio_map.get("audio_analyzer")).get("segments")))
    diagnostics.update(_safe_dict(scene_slot_diag))
    diagnostics["audio_map_alignment_source"] = str(audio_map.get("audio_map_alignment_source") or "gemini_audio_map_v1_1")
    diagnostics.update(grid_metrics)
    diagnostics["audio_map_music_signal_mode"] = music_signal_mode
    diagnostics["audio_map_dynamics_available"] = dynamics_available
    diagnostics["audio_map_dramaturgy_source"] = str(_safe_dict(audio_map.get("audio_dramaturgy")).get("dramaturgy_source") or "")
    diagnostics["audio_map_textual_directive_present"] = bool(_safe_dict(audio_map.get("audio_dramaturgy")).get("textual_directive_present"))
    diagnostics["audio_map_vocal_gender"] = str(audio_map.get("vocal_gender") or "unknown")
    diagnostics["audio_map_vocal_owner_role"] = str(audio_map.get("vocal_owner_role") or "unknown")
    diagnostics["audio_map_vocal_owner_confidence"] = _to_float(audio_map.get("vocal_owner_confidence"), 0.0)
    diagnostics["audio_map_voice_reason"] = str(_safe_dict(audio_map.get("vocal_profile")).get("reason") or "")
    package["diagnostics"] = diagnostics
    package["audio_map"] = audio_map
    _append_diag_event(package, "audio_map generated", stage_id="audio_map")
    return package


def _run_role_plan_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    content_type = str(input_pkg.get("content_type") or "").strip().lower()
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["role_plan_backend"] = "gemini"
    diagnostics["role_plan_prompt_version"] = "roles_v1_1"
    diagnostics["role_plan_error"] = ""
    diagnostics["role_plan_validation_error"] = ""
    diagnostics["validation_error"] = ""
    diagnostics["role_plan_used_fallback"] = False
    diagnostics["role_plan_roles_version"] = ""
    diagnostics["role_plan_roster_count"] = 0
    diagnostics["role_plan_scene_casting_count"] = 0
    diagnostics["role_plan_segment_coverage_ok"] = False
    diagnostics["role_plan_retry_count"] = 0
    diagnostics["role_plan_error_code"] = ""
    diagnostics["role_plan_skipped"] = False
    diagnostics["role_plan_skip_reason"] = ""
    diagnostics["role_plan_empty"] = False
    diagnostics["role_plan_snapshot_restored"] = False
    diagnostics["role_plan_failure_reason"] = ""
    diagnostics["role_plan_candidate_failed_but_snapshot_restored"] = False
    diagnostics["role_plan_last_failed_candidate_error"] = ""
    diagnostics["role_plan_configured_timeout_sec"] = get_scenario_stage_timeout("role_plan")
    diagnostics["role_plan_timeout_stage_policy_name"] = scenario_timeout_policy_name("role_plan")
    diagnostics["role_plan_timed_out"] = False
    diagnostics["role_plan_timeout_retry_attempted"] = False
    diagnostics["role_plan_response_was_empty_after_timeout"] = False
    diagnostics["role_plan_raw_model_response_preview"] = ""
    diagnostics["role_plan_normalized_preview"] = ""
    diagnostics["role_plan_technical_leak_trigger"] = ""
    diagnostics["role_plan_technical_leak_field"] = ""
    diagnostics["role_plan_technical_leak_token"] = ""
    diagnostics["role_plan_false_positive_technical_leak_allowed"] = False
    diagnostics["role_plan_allowed_technical_token"] = ""
    diagnostics["role_plan_allowed_technical_phrase"] = ""
    diagnostics["role_plan_dropped_non_canonical_fields"] = []
    diagnostics["role_plan_created_for_signature"] = ""
    diagnostics["role_plan_coverage_expected_segment_ids"] = []
    diagnostics["role_plan_coverage_seen_segment_ids"] = []
    diagnostics["role_plan_coverage_missing_segment_ids"] = []
    diagnostics["role_plan_coverage_extra_segment_ids"] = []
    diagnostics["role_plan_primary_mismatch_segments"] = []
    diagnostics["gemini_api_key_source"] = "missing"
    diagnostics["gemini_api_key_valid"] = False
    diagnostics["gemini_api_key_error"] = "empty"
    package["diagnostics"] = diagnostics
    previous_role_plan = _safe_dict(package.get("role_plan"))
    previous_role_plan_valid = _has_valid_role_plan_payload(previous_role_plan)

    if content_type and content_type not in {"music_video", "clip", "story"}:
        package["role_plan"] = _attach_downstream_mode_metadata({}, package)
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics["role_plan_error"] = f"unsupported_content_type:{content_type}"
        diagnostics["role_plan_used_fallback"] = False
        diagnostics["role_plan_skipped"] = True
        diagnostics["role_plan_skip_reason"] = f"unsupported_content_type:{content_type}"
        package["diagnostics"] = diagnostics
        _append_diag_event(package, f"role_plan skipped for content_type={content_type}", stage_id="role_plan")
        return package

    gemini_api_key = _resolve_stage_gemini_api_key(package, stage_id="role_plan")
    result = build_gemini_role_plan(
        api_key=gemini_api_key,
        package=package,
    )
    role_plan = _safe_dict(result.get("role_plan"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    visual_ref_applied, visual_ref_source = _collect_visual_ref_identity_diagnostics(package, input_pkg, refs_inventory)
    for role in ("character_1", "character_2", "character_3"):
        if visual_ref_applied.get(role):
            _apply_visual_ref_identity_lock_to_role_plan(role_plan, role)
    current_signature = _current_scenario_input_signature(package)
    if current_signature:
        role_plan["created_for_signature"] = current_signature
    package["role_plan"] = _attach_downstream_mode_metadata(role_plan, package)

    role_diag = _safe_dict(result.get("diagnostics"))
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["role_plan_backend"] = "gemini"
    diagnostics["role_plan_prompt_version"] = str(role_diag.get("prompt_version") or "roles_v1_1")
    diagnostics["role_plan_used_fallback"] = bool(result.get("used_fallback"))
    diagnostics["role_plan_roles_version"] = str(role_plan.get("roles_version") or role_diag.get("roles_version") or "")
    diagnostics["role_plan_roster_count"] = int(role_diag.get("roster_count") or len(_safe_list(role_plan.get("roster"))))
    diagnostics["role_plan_scene_casting_count"] = int(role_diag.get("scene_casting_count") or len(_safe_list(role_plan.get("scene_casting"))))
    diagnostics["role_plan_segment_coverage_ok"] = bool(role_diag.get("segment_coverage_ok"))
    diagnostics["role_plan_uses_audio_transcript_slice"] = bool(role_diag.get("uses_audio_transcript_slice"))
    diagnostics["role_plan_uses_core_arc_role"] = bool(role_diag.get("uses_core_arc_role"))
    diagnostics["role_plan_uses_core_beat_purpose"] = bool(role_diag.get("uses_core_beat_purpose"))
    diagnostics["role_plan_uses_core_emotional_key"] = bool(role_diag.get("uses_core_emotional_key"))
    diagnostics["role_plan_fell_back_to_legacy_audio_text_fields"] = bool(role_diag.get("fell_back_to_legacy_audio_text_fields"))
    diagnostics["role_plan_fell_back_to_legacy_core_fields"] = bool(role_diag.get("fell_back_to_legacy_core_fields"))
    diagnostics["role_plan_missing_core_meaning_rows"] = int(role_diag.get("missing_core_meaning_rows") or 0)
    diagnostics["role_plan_normalized_segment_rows_error"] = str(role_diag.get("normalized_segment_rows_error") or "")
    diagnostics["role_plan_normalized_segment_rows_missing_core_meaning"] = str(
        role_diag.get("normalized_segment_rows_missing_core_meaning") or ""
    )
    diagnostics["role_plan_retry_count"] = int(result.get("retry_count") or role_diag.get("retry_count") or 0)
    diagnostics["role_plan_error_code"] = str(result.get("error_code") or role_diag.get("error_code") or "")
    diagnostics["role_plan_error"] = str(result.get("error") or "")
    diagnostics["role_plan_validation_error"] = str(result.get("validation_error") or "")
    diagnostics["validation_error"] = str(result.get("validation_error") or "")
    diagnostics["role_plan_raw_model_response_preview"] = str(role_diag.get("raw_model_response_preview") or "")
    diagnostics["role_plan_normalized_preview"] = str(role_diag.get("normalized_role_plan_preview") or "")
    diagnostics["role_plan_technical_leak_trigger"] = str(role_diag.get("technical_leak_trigger") or "")
    diagnostics["role_plan_technical_leak_field"] = str(role_diag.get("technical_leak_field") or "")
    diagnostics["role_plan_technical_leak_token"] = str(role_diag.get("technical_leak_token") or "")
    diagnostics["role_plan_false_positive_technical_leak_allowed"] = bool(role_diag.get("false_positive_technical_leak_allowed"))
    diagnostics["role_plan_allowed_technical_token"] = str(role_diag.get("allowed_technical_token") or "")
    diagnostics["role_plan_allowed_technical_phrase"] = str(role_diag.get("allowed_technical_phrase") or "")
    diagnostics["role_plan_dropped_non_canonical_fields"] = _safe_list(role_diag.get("dropped_non_canonical_fields"))
    diagnostics["role_plan_coverage_expected_segment_ids"] = _safe_list(role_diag.get("coverage_expected_segment_ids"))
    diagnostics["role_plan_coverage_seen_segment_ids"] = _safe_list(role_diag.get("coverage_seen_segment_ids"))
    diagnostics["role_plan_coverage_missing_segment_ids"] = _safe_list(role_diag.get("coverage_missing_segment_ids"))
    diagnostics["role_plan_coverage_extra_segment_ids"] = _safe_list(role_diag.get("coverage_extra_segment_ids"))
    diagnostics["role_plan_primary_mismatch_segments"] = _safe_list(role_diag.get("role_plan_primary_mismatch_segments"))
    diagnostics["role_plan_created_for_signature"] = str(_safe_dict(package.get("role_plan")).get("created_for_signature") or "")
    diagnostics["role_plan_configured_timeout_sec"] = int(
        role_diag.get("configured_timeout_sec") or diagnostics.get("role_plan_configured_timeout_sec") or 0
    )
    diagnostics["role_plan_timeout_stage_policy_name"] = str(
        role_diag.get("timeout_stage_policy_name") or diagnostics.get("role_plan_timeout_stage_policy_name") or ""
    )
    diagnostics["role_plan_timed_out"] = bool(role_diag.get("timed_out"))
    diagnostics["role_plan_timeout_retry_attempted"] = bool(role_diag.get("timeout_retry_attempted"))
    diagnostics["role_plan_response_was_empty_after_timeout"] = bool(role_diag.get("response_was_empty_after_timeout"))
    diagnostics["role_plan_skipped"] = False
    diagnostics["role_plan_skip_reason"] = ""
    diagnostics["role_plan_empty"] = not _has_valid_role_plan_payload(role_plan)
    diagnostics["visual_ref_identity_lock_applied"] = visual_ref_applied
    diagnostics["visual_ref_identity_lock_source"] = visual_ref_source
    package["diagnostics"] = diagnostics

    role_plan_valid = _has_valid_role_plan_payload(role_plan)
    has_error_flags = bool(
        diagnostics.get("role_plan_error")
        or diagnostics.get("role_plan_validation_error")
        or diagnostics.get("role_plan_error_code")
    )
    has_counts = int(diagnostics.get("role_plan_roster_count") or 0) > 0 and int(diagnostics.get("role_plan_scene_casting_count") or 0) > 0
    has_coverage = bool(diagnostics.get("role_plan_segment_coverage_ok"))
    stage_success = bool(result.get("ok")) and role_plan_valid and not has_error_flags and has_counts and has_coverage

    if stage_success:
        diagnostics["role_plan_snapshot_restored"] = False
        diagnostics["role_plan_failure_reason"] = ""
        diagnostics["role_plan_candidate_failed_but_snapshot_restored"] = False
        diagnostics["role_plan_last_failed_candidate_error"] = ""
        package["diagnostics"] = diagnostics
        _append_diag_event(package, "role_plan generated", stage_id="role_plan")
        return package

    failure_reason = (
        str(diagnostics.get("role_plan_validation_error") or "")
        or str(diagnostics.get("role_plan_error_code") or "")
        or str(diagnostics.get("role_plan_error") or "")
        or "role_plan_invalid_empty_or_uncovered"
    )

    if previous_role_plan_valid:
        package["role_plan"] = _attach_downstream_mode_metadata(previous_role_plan, package)
        diagnostics["role_plan_snapshot_restored"] = True
        diagnostics["role_plan_candidate_failed_but_snapshot_restored"] = True
        diagnostics["role_plan_last_failed_candidate_error"] = str(failure_reason)
        diagnostics["role_plan_failure_reason"] = str(failure_reason)
        diagnostics["validation_error"] = ""
        diagnostics["role_plan_validation_error"] = ""
        diagnostics["role_plan_error"] = ""
        diagnostics["role_plan_error_code"] = ""
        diagnostics["role_plan_empty"] = False
        package["diagnostics"] = diagnostics
        _append_diag_event(package, "role_plan invalid: restored previous snapshot", stage_id="role_plan")
        _append_diag_event(
            package,
            "role_plan candidate failed but previous valid snapshot restored",
            stage_id="role_plan",
        )
        return package
    else:
        package["role_plan"] = _attach_downstream_mode_metadata({}, package)
        diagnostics["role_plan_snapshot_restored"] = False
        diagnostics["role_plan_candidate_failed_but_snapshot_restored"] = False
        diagnostics["role_plan_last_failed_candidate_error"] = str(failure_reason)
        _append_diag_event(package, "role_plan invalid: no previous snapshot", stage_id="role_plan")

    diagnostics["role_plan_failure_reason"] = failure_reason
    diagnostics["role_plan_empty"] = True
    package["diagnostics"] = diagnostics
    raise RuntimeError(failure_reason)


def _run_scene_plan_stage(package: dict[str, Any]) -> dict[str, Any]:
    previous_scene_plan = _safe_dict(package.get("scene_plan"))
    previous_scene_plan_valid = _has_valid_scene_plan_payload(previous_scene_plan)
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["scene_plan_backend"] = "gemini"
    diagnostics["scene_plan_prompt_version"] = SCENE_PLAN_PROMPT_VERSION
    diagnostics["scene_plan_used_model"] = ""
    diagnostics["scene_plan_used_fallback"] = False
    diagnostics["scene_plan_scene_count"] = 0
    diagnostics["scene_plan_route_counts"] = {"i2v": 0, "ia2v": 0, "first_last": 0}
    diagnostics["scene_plan_presence_modes"] = []
    diagnostics["scene_plan_route_flat"] = False
    diagnostics["scene_plan_watchability_fallback_count"] = 0
    diagnostics["scene_plan_world_summary_used"] = False
    diagnostics["scene_plan_window_count_source"] = 0
    diagnostics["scene_plan_window_count_model"] = 0
    diagnostics["scene_plan_window_count_normalized"] = 0
    diagnostics["scene_plan_repaired_to_audio_windows"] = False
    diagnostics["scene_plan_synthetic_rows_dropped"] = 0
    diagnostics["scene_plan_missing_rows_filled"] = 0
    diagnostics["scene_plan_has_adjacent_ia2v"] = False
    diagnostics["scene_plan_has_adjacent_first_last"] = False
    diagnostics["scene_plan_route_spacing_warning"] = False
    diagnostics["active_video_model_capability_profile"] = str(diagnostics.get("active_video_model_capability_profile") or "")
    diagnostics["active_route_capability_mode"] = str(diagnostics.get("active_route_capability_mode") or "")
    diagnostics["scene_plan_capability_guard_applied"] = False
    diagnostics["scene_plan_validation_error"] = ""
    diagnostics["scene_plan_error_code"] = ""
    diagnostics["scene_plan_validation_errors"] = []
    diagnostics["scene_plan_error_codes"] = []
    diagnostics["scene_plan_scenes_version"] = ""
    diagnostics["scene_plan_segment_count_expected"] = 0
    diagnostics["scene_plan_segment_count_actual"] = 0
    diagnostics["scene_plan_segment_coverage_ok"] = False
    diagnostics["scene_plan_uses_segment_id_canonical"] = False
    diagnostics["scene_plan_uses_legacy_scene_candidate_windows_bridge"] = False
    diagnostics["scene_plan_uses_legacy_compiled_contract_bridge"] = False
    diagnostics["scene_plan_role_source_precedence"] = []
    diagnostics["scene_plan_route_budget_retry_used"] = False
    diagnostics["scene_plan_route_budget_retry_already_attempted"] = False
    diagnostics["scene_plan_route_budget_second_retry_suppressed"] = False
    diagnostics["scene_plan_route_budget_feedback"] = ""
    diagnostics["scene_plan_post_validity_route_rebalance"] = {}
    diagnostics["scene_plan_route_budget_ok"] = True
    diagnostics["scene_plan_route_budget_target"] = {}
    diagnostics["scene_plan_route_budget_actual"] = {}
    diagnostics["scene_plan_soft_route_budget_shortfall_accepted"] = False
    diagnostics["scene_plan_soft_route_budget_target"] = {}
    diagnostics["scene_plan_soft_route_budget_actual"] = {}
    diagnostics["scene_plan_soft_route_budget_reason"] = ""
    diagnostics["scene_plan_max_consecutive_lipsync"] = 0
    diagnostics["scene_plan_longest_lipsync_streak"] = 0
    diagnostics["scene_plan_all_lipsync_mode"] = False
    diagnostics["scene_plan_lipsync_streak_guard_relaxed"] = False
    diagnostics["scene_plan_route_budget_validation_mode"] = "mixed"
    diagnostics["scene_plan_lipsync_streak_warning"] = ""
    diagnostics["scene_plan_route_budget_mismatch"] = False
    diagnostics["scene_plan_route_budget_mode"] = "creative_config_soft"
    diagnostics["scene_plan_first_last_forbidden"] = True
    diagnostics["scene_plan_first_last_missing_is_ok"] = True
    diagnostics["scene_plan_route_budget_old_first_last_requirement_suppressed"] = False
    diagnostics["scene_plan_hard_route_map_enabled"] = False
    diagnostics["scene_plan_hard_route_map_source"] = ""
    diagnostics["scene_plan_hard_route_map_by_segment"] = {}
    diagnostics["scene_plan_hard_route_map_target_counts"] = {"i2v": 0, "ia2v": 0, "first_last": 0}
    diagnostics["scene_plan_hard_route_map_actual_counts_after_normalize"] = {"i2v": 0, "ia2v": 0, "first_last": 0}
    diagnostics["scene_plan_enum_invalid_detected"] = False
    diagnostics["scene_plan_enum_invalid_count"] = 0
    diagnostics["scene_plan_enum_invalid_field"] = ""
    diagnostics["scene_plan_enum_invalid_value"] = ""
    diagnostics["scene_plan_enum_invalid_allowed_values"] = []
    diagnostics["scene_plan_enum_invalid_segment_id"] = ""
    diagnostics["scene_plan_enum_invalid_rows"] = []
    diagnostics["scene_plan_enum_repair_applied"] = False
    diagnostics["scene_plan_enum_repair_count"] = 0
    diagnostics["scene_plan_enum_repair_rows"] = []
    diagnostics["scene_plan_enum_unrepaired_count"] = 0
    diagnostics["scene_plan_enum_unrepaired_rows"] = []
    diagnostics["scene_plan_final_semantic_repairs"] = {"rows_updated": 0, "world_rows_repaired": 0, "primary_role_filled": 0}
    diagnostics["scene_plan_failed_candidate_preview"] = []
    diagnostics["scene_plan_failed_candidate_route_mix"] = {}
    diagnostics["scene_plan_failed_candidate_rows_count"] = 0
    diagnostics["scene_plan_failed_candidate_segment_ids"] = []
    diagnostics["scene_plan_failed_candidate_first_routes"] = []
    diagnostics["scene_plan_failed_candidate_validation_errors"] = []
    diagnostics["scenes_vocal_owner_role_used"] = "unknown"
    diagnostics["validation_error"] = ""
    diagnostics["scene_plan_error"] = ""
    diagnostics["scene_plan_empty"] = False
    diagnostics["scene_plan_snapshot_restored"] = False
    diagnostics["scene_plan_failure_reason"] = ""
    diagnostics["scene_plan_candidate_failed_but_snapshot_restored"] = False
    diagnostics["scene_plan_last_failed_candidate_error"] = ""
    diagnostics["scene_plan_created_for_signature"] = str(_safe_dict(previous_scene_plan).get("created_for_signature") or "")
    diagnostics["scene_plan_configured_timeout_sec"] = get_scenario_stage_timeout("scene_plan")
    diagnostics["scene_plan_timeout_stage_policy_name"] = scenario_timeout_policy_name("scene_plan")
    diagnostics["scene_plan_timed_out"] = False
    diagnostics["scene_plan_timeout_retry_attempted"] = False
    diagnostics["scene_plan_response_was_empty_after_timeout"] = False
    diagnostics["scene_plan_first_attempt_error"] = ""
    diagnostics["scene_plan_retry_reason"] = ""
    diagnostics["scene_plan_retry_prompt_mode"] = ""
    diagnostics["scene_plan_retry_timed_out"] = False
    diagnostics["scene_plan_retry_empty_response"] = False
    diagnostics["gemini_api_key_source"] = "missing"
    diagnostics["gemini_api_key_valid"] = False
    diagnostics["gemini_api_key_error"] = "empty"
    package["diagnostics"] = diagnostics
    gemini_api_key = _resolve_stage_gemini_api_key(package, stage_id="scene_plan")
    hard_fail_error = ""
    scene_plan_prompt_package = _build_scene_plan_prompt_package(package)
    scene_prompt_input = _safe_dict(scene_plan_prompt_package.get("input"))
    scene_prompt_creative_config = deepcopy(_safe_dict(scene_prompt_input.get("creative_config")))
    strict_preset_name = str(scene_prompt_creative_config.get("route_strategy_preset") or "").strip().lower()
    backend_hard_route_map: dict[str, str] = {}
    backend_hard_route_source = ""
    if strict_preset_name == "no_first_last_50_50_0":
        backend_hard_route_map = build_no_first_last_50_50_hard_route_map(package)
        if backend_hard_route_map:
            backend_hard_route_source = "backend_strict_preset_no_first_last_50_50_0"
            scene_prompt_creative_config["hard_route_assignments_by_segment"] = dict(backend_hard_route_map)
            scene_prompt_creative_config["route_assignments_by_segment"] = dict(backend_hard_route_map)
            scene_prompt_creative_config["routes_are_hard_locked"] = True
            scene_prompt_creative_config["route_assignment_source"] = backend_hard_route_source
            scene_prompt_input["creative_config"] = scene_prompt_creative_config
            scene_plan_prompt_package["input"] = scene_prompt_input
    backend_hard_route_target_counts = {
        "i2v": sum(1 for route in backend_hard_route_map.values() if route == "i2v"),
        "ia2v": sum(1 for route in backend_hard_route_map.values() if route == "ia2v"),
        "first_last": sum(1 for route in backend_hard_route_map.values() if route == "first_last"),
    }

    result = build_gemini_scene_plan(
        api_key=gemini_api_key,
        package=scene_plan_prompt_package,
    )
    initial_validation_error = str(result.get("validation_error") or "").strip()
    if initial_validation_error:
        diagnostics["scene_plan_first_attempt_error"] = initial_validation_error
        diagnostics["scene_plan_retry_reason"] = initial_validation_error
        retry_prompt_mode = "compact_route_budget_retry" if initial_validation_error == "route_budget_mismatch" else "default"
        diagnostics["scene_plan_retry_prompt_mode"] = retry_prompt_mode
        validation_error_code = str(result.get("error_code") or "").strip()
        validation_feedback = _build_scene_plan_retry_feedback(
            initial_validation_error,
            validation_error_code,
            _safe_dict(result.get("diagnostics")),
        )
        _append_diag_event(package, f"scene_plan validation failed, retrying once: {validation_feedback}", stage_id="scene_plan")
        retry_result = build_gemini_scene_plan(
            api_key=gemini_api_key,
            package=scene_plan_prompt_package,
            validation_feedback=validation_feedback,
            prompt_mode=retry_prompt_mode,
        )
        result = retry_result
        if str(result.get("validation_error") or "").strip():
            result["ok"] = False
            result["error"] = str(result.get("error") or result.get("validation_error") or "scene_plan_validation_failed")
            hard_fail_error = str(result.get("validation_error") or result.get("error") or "scene_plan_validation_failed")

    scene_plan = _safe_dict(result.get("scene_plan"))
    route_locks_by_segment: dict[str, str] = {}
    route_lock_source = ""
    route_assignment_source = ""
    route_semantic_mismatches: list[dict[str, Any]] = []
    semantic_retry_used = False
    user_hard_route_map = _extract_user_hard_route_map(_safe_dict(package.get("input")))
    effective_hard_route_map = dict(backend_hard_route_map or user_hard_route_map)
    if scene_plan:
        if backend_hard_route_map:
            preferred_route_locks, preferred_route_lock_source = _preferred_validated_route_locks(
                scene_plan,
                backend_hard_route_map,
            )
            route_locks_by_segment = dict(preferred_route_locks)
            route_lock_source = str(preferred_route_lock_source or "backend_strict_preset_no_first_last_50_50_0")
            route_assignment_source = "validated_route_map" if preferred_route_lock_source else "hard_route_map"
        else:
            route_locks_by_segment, route_lock_source = _resolve_scene_plan_route_locks(
                package=package,
                scene_plan=scene_plan,
                previous_scene_plan=previous_scene_plan,
            )
            route_assignment_source = str(route_lock_source or "")
        scene_plan, locked_route_counts = _apply_scene_plan_route_locks(
            scene_plan,
            route_locks_by_segment,
            overwrite_existing=False if route_lock_source in {"scene_plan_validated_routes", "scene_plan_row_routes"} else (True if backend_hard_route_map else (route_lock_source != "fallback_backend_route_fill")),
        )
        route_semantic_mismatches = _collect_scene_plan_route_semantic_mismatches(scene_plan)
        if route_semantic_mismatches and not effective_hard_route_map:
            semantic_retry_used = True
            targeted_feedback = (
                "Some routes do not match story beat types. Reassign routes semantically while respecting "
                "route_targets_per_block as much as possible. I2V=physical story action, IA2V=vocal emotional performance. "
                "Preserve all segment_ids and scene meanings."
            )
            _append_diag_event(package, "scene_plan semantic mismatch detected, retrying once", stage_id="scene_plan")
            semantic_retry_result = build_gemini_scene_plan(
                api_key=gemini_api_key,
                package=scene_plan_prompt_package,
                validation_feedback=targeted_feedback,
            )
            retry_scene_plan = _safe_dict(semantic_retry_result.get("scene_plan"))
            if retry_scene_plan:
                if backend_hard_route_map:
                    preferred_route_locks, preferred_route_lock_source = _preferred_validated_route_locks(
                        retry_scene_plan,
                        backend_hard_route_map,
                    )
                    route_locks_by_segment = dict(preferred_route_locks)
                    route_lock_source = str(preferred_route_lock_source or "backend_strict_preset_no_first_last_50_50_0")
                    route_assignment_source = "validated_route_map" if preferred_route_lock_source else "hard_route_map"
                else:
                    route_locks_by_segment, route_lock_source = _resolve_scene_plan_route_locks(
                        package=package,
                        scene_plan=retry_scene_plan,
                        previous_scene_plan=previous_scene_plan,
                    )
                    route_assignment_source = str(route_lock_source or route_assignment_source or "")
                retry_scene_plan, locked_route_counts = _apply_scene_plan_route_locks(
                    retry_scene_plan,
                    route_locks_by_segment,
                    overwrite_existing=False if route_lock_source in {"scene_plan_validated_routes", "scene_plan_row_routes"} else (True if backend_hard_route_map else (route_lock_source != "fallback_backend_route_fill")),
                )
                route_semantic_mismatches = _collect_scene_plan_route_semantic_mismatches(retry_scene_plan)
                semantic_retry_result["scene_plan"] = retry_scene_plan
                result = semantic_retry_result
                scene_plan = retry_scene_plan
            else:
                result = semantic_retry_result
                scene_plan = retry_scene_plan
        scene_plan, final_semantic_repairs = _repair_scene_plan_final_semantics(
            package=package,
            scene_plan=scene_plan,
        )
        scene_plan, post_validity_rebalance = _rebalance_scene_plan_routes_after_validity_repairs(
            package=package,
            scene_plan=scene_plan,
        )
        scene_plan, final_sync_meta = _sync_scene_plan_storyboard_mirror(scene_plan)
        diagnostics["scene_plan_final_semantic_repairs"] = dict(final_semantic_repairs)
        diagnostics["scene_plan_post_validity_route_rebalance"] = dict(post_validity_rebalance)
        diagnostics["scene_plan_final_sync"] = dict(final_sync_meta)
        result["scene_plan"] = scene_plan
    else:
        locked_route_counts = {"i2v": 0, "ia2v": 0, "first_last": 0}
    route_budget_ok = True
    route_budget_feedback = ""
    route_budget_meta: dict[str, Any] = {}
    route_budget_ok, route_budget_feedback, route_budget_meta = _validate_scene_plan_route_budget(
        package=package,
        scene_plan=scene_plan,
        diagnostics=diagnostics,
    )
    route_budget_retry_already_attempted = str(diagnostics.get("scene_plan_retry_prompt_mode") or "") == "compact_route_budget_retry"
    if not route_budget_ok:
        diagnostics["scene_plan_route_budget_retry_used"] = True
        diagnostics["scene_plan_route_budget_retry_already_attempted"] = bool(route_budget_retry_already_attempted)
        diagnostics["scene_plan_route_budget_feedback"] = route_budget_feedback
        if route_budget_retry_already_attempted:
            diagnostics["scene_plan_route_budget_second_retry_suppressed"] = True
            result["ok"] = False
            result["validation_error"] = "route_budget_mismatch"
            result["error"] = "scene_plan_route_budget_validation_failed"
            result["error_code"] = "SCENES_ROUTE_BUDGET_MISMATCH"
            diagnostics["scene_plan_failure_reason"] = "route_budget_mismatch_after_compact_retry"
            hard_fail_error = "route_budget_mismatch_after_compact_retry"
        else:
            diagnostics["scene_plan_route_budget_second_retry_suppressed"] = False
            diagnostics["scene_plan_first_attempt_error"] = diagnostics.get("scene_plan_first_attempt_error") or "route_budget_mismatch"
            diagnostics["scene_plan_retry_reason"] = "route_budget_mismatch"
            diagnostics["scene_plan_retry_prompt_mode"] = "compact_route_budget_retry"
            _append_diag_event(package, f"scene_plan route budget validation failed, retrying once: {route_budget_feedback}", stage_id="scene_plan")
            retry_result = build_gemini_scene_plan(
                api_key=gemini_api_key,
                package=scene_plan_prompt_package,
                validation_feedback=route_budget_feedback,
                prompt_mode="compact_route_budget_retry",
            )
            retry_scene_plan = _safe_dict(retry_result.get("scene_plan"))
            retry_locked_route_counts = {"i2v": 0, "ia2v": 0, "first_last": 0}
            if retry_scene_plan:
                if backend_hard_route_map:
                    preferred_route_locks, preferred_route_lock_source = _preferred_validated_route_locks(
                        retry_scene_plan,
                        backend_hard_route_map,
                    )
                    route_locks_by_segment = dict(preferred_route_locks)
                    route_lock_source = str(preferred_route_lock_source or "backend_strict_preset_no_first_last_50_50_0")
                    route_assignment_source = "validated_route_map" if preferred_route_lock_source else "hard_route_map"
                else:
                    route_locks_by_segment, route_lock_source = _resolve_scene_plan_route_locks(
                        package=package,
                        scene_plan=retry_scene_plan,
                        previous_scene_plan=previous_scene_plan,
                    )
                    route_assignment_source = str(route_lock_source or route_assignment_source or "")
                retry_scene_plan, retry_locked_route_counts = _apply_scene_plan_route_locks(
                    retry_scene_plan,
                    route_locks_by_segment,
                    overwrite_existing=False if route_lock_source in {"scene_plan_validated_routes", "scene_plan_row_routes"} else (True if backend_hard_route_map else (route_lock_source != "fallback_backend_route_fill")),
                )
                retry_scene_plan, retry_final_semantic_repairs = _repair_scene_plan_final_semantics(
                    package=package,
                    scene_plan=retry_scene_plan,
                )
                retry_scene_plan, retry_post_validity_rebalance = _rebalance_scene_plan_routes_after_validity_repairs(
                    package=package,
                    scene_plan=retry_scene_plan,
                )
                retry_scene_plan, retry_sync_meta = _sync_scene_plan_storyboard_mirror(retry_scene_plan)
                diagnostics["scene_plan_final_semantic_repairs"] = dict(retry_final_semantic_repairs)
                diagnostics["scene_plan_post_validity_route_rebalance"] = dict(retry_post_validity_rebalance)
                diagnostics["scene_plan_final_sync"] = dict(retry_sync_meta)
                retry_result["scene_plan"] = retry_scene_plan
            retry_ok, retry_feedback, retry_meta = _validate_scene_plan_route_budget(
                package=package,
                scene_plan=retry_scene_plan,
                diagnostics=diagnostics,
            )
            retry_diag = _safe_dict(retry_result.get("diagnostics"))
            diagnostics["scene_plan_retry_timed_out"] = bool(retry_diag.get("timed_out"))
            diagnostics["scene_plan_retry_empty_response"] = bool(
                retry_diag.get("response_was_empty_after_timeout")
                or (not _scene_plan_rows_for_validation(retry_scene_plan))
            )
            result = retry_result
            scene_plan = retry_scene_plan
            locked_route_counts = dict(retry_locked_route_counts)
            route_budget_ok = retry_ok
            route_budget_feedback = retry_feedback
            route_budget_meta = retry_meta
            timeout_empty_retry = bool(diagnostics.get("scene_plan_retry_timed_out") or diagnostics.get("scene_plan_retry_empty_response"))
            if timeout_empty_retry:
                result["ok"] = False
                result["validation_error"] = "scene_plan_timeout_empty_response"
                result["error"] = "scene_plan_timeout"
                result["error_code"] = "SCENES_TIMEOUT_EMPTY_RESPONSE"
                hard_fail_error = "scene_plan_timeout_empty_response"
            if (not route_budget_ok) and (not timeout_empty_retry):
                result["ok"] = False
                result["validation_error"] = "route_budget_mismatch"
                result["error"] = "scene_plan_route_budget_validation_failed"
                result["error_code"] = "SCENES_ROUTE_BUDGET_MISMATCH"
                hard_fail_error = "route_budget_mismatch"
    else:
        diagnostics["scene_plan_route_budget_retry_already_attempted"] = bool(route_budget_retry_already_attempted)
        diagnostics["scene_plan_route_budget_second_retry_suppressed"] = False

    scene_diag = _safe_dict(result.get("diagnostics"))
    final_route_counts = _scene_plan_route_counts(scene_plan)
    route_counts = _safe_dict(scene_diag.get("route_counts"))
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["scene_plan_backend"] = "gemini"
    diagnostics["scene_plan_prompt_version"] = str(scene_diag.get("prompt_version") or SCENE_PLAN_PROMPT_VERSION)
    diagnostics["scene_plan_used_model"] = str(scene_diag.get("used_model") or diagnostics.get("scene_plan_used_model") or "")
    diagnostics["scene_plan_used_fallback"] = bool(result.get("used_fallback"))
    diagnostics["scene_plan_scene_count"] = int(scene_diag.get("scene_count") or len(_safe_list(scene_plan.get("storyboard"))))
    diagnostics["scene_plan_route_counts"] = {
        "i2v": int(final_route_counts.get("i2v") or route_counts.get("i2v") or _safe_dict(scene_plan.get("route_mix_summary")).get("i2v") or 0),
        "ia2v": int(final_route_counts.get("ia2v") or route_counts.get("ia2v") or _safe_dict(scene_plan.get("route_mix_summary")).get("ia2v") or 0),
        "first_last": int(
            final_route_counts.get("first_last")
            or route_counts.get("first_last")
            or _safe_dict(scene_plan.get("route_mix_summary")).get("first_last")
            or 0
        ),
    }
    diagnostics["scene_plan_presence_modes"] = _safe_list(scene_diag.get("presence_modes"))
    diagnostics["scene_plan_route_flat"] = bool(scene_diag.get("route_flat"))
    diagnostics["scene_plan_watchability_fallback_count"] = int(scene_diag.get("watchability_fallback_count") or 0)
    diagnostics["scene_plan_world_summary_used"] = bool(scene_diag.get("world_summary_used"))
    diagnostics["scene_plan_window_count_source"] = int(scene_diag.get("window_count_source") or 0)
    diagnostics["scene_plan_window_count_model"] = int(scene_diag.get("window_count_model") or 0)
    diagnostics["scene_plan_window_count_normalized"] = int(scene_diag.get("window_count_normalized") or 0)
    diagnostics["scene_plan_repaired_to_audio_windows"] = bool(scene_diag.get("repaired_to_audio_windows"))
    diagnostics["scene_plan_synthetic_rows_dropped"] = int(scene_diag.get("synthetic_rows_dropped") or 0)
    diagnostics["scene_plan_missing_rows_filled"] = int(scene_diag.get("missing_rows_filled") or 0)
    diagnostics["scene_plan_has_adjacent_ia2v"] = bool(scene_diag.get("scene_plan_has_adjacent_ia2v"))
    diagnostics["scene_plan_has_adjacent_first_last"] = bool(scene_diag.get("scene_plan_has_adjacent_first_last"))
    diagnostics["scene_plan_route_spacing_warning"] = bool(scene_diag.get("scene_plan_route_spacing_warning"))
    diagnostics["active_video_model_capability_profile"] = str(
        scene_diag.get("active_video_model_capability_profile") or diagnostics.get("active_video_model_capability_profile") or ""
    )
    diagnostics["active_route_capability_mode"] = str(
        scene_diag.get("active_route_capability_mode") or diagnostics.get("active_route_capability_mode") or ""
    )
    diagnostics["scene_plan_capability_guard_applied"] = bool(scene_diag.get("scene_plan_capability_guard_applied"))
    diagnostics["capability_rules_source_version"] = str(
        scene_diag.get("capability_rules_source_version") or diagnostics.get("capability_rules_source_version") or ""
    )
    diagnostics["scene_plan_validation_error"] = str(result.get("validation_error") or "")
    diagnostics["scene_plan_error_code"] = str(result.get("error_code") or scene_diag.get("error_code") or "")
    diagnostics["scene_plan_validation_errors"] = _safe_list(scene_diag.get("scene_plan_validation_errors"))
    diagnostics["scene_plan_error_codes"] = _safe_list(scene_diag.get("scene_plan_error_codes"))
    diagnostics["scene_plan_scenes_version"] = str(_safe_dict(scene_plan).get("scenes_version") or scene_diag.get("scene_plan_scenes_version") or "")
    audio_segments = _safe_list(_safe_dict(package.get("audio_map")).get("segments"))
    expected_segment_ids = [
        str(_safe_dict(segment).get("segment_id") or "").strip()
        for segment in audio_segments
        if str(_safe_dict(segment).get("segment_id") or "").strip()
    ]
    final_rows = _scene_plan_rows_for_validation(scene_plan)
    actual_segment_ids = [
        str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
        for row in final_rows
        if str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
    ]
    final_segment_coverage_ok = bool(expected_segment_ids and expected_segment_ids == actual_segment_ids)
    diagnostics["scene_plan_segment_count_expected"] = len(expected_segment_ids)
    diagnostics["scene_plan_segment_count_actual"] = len(actual_segment_ids)
    diagnostics["scene_plan_segment_coverage_ok"] = final_segment_coverage_ok
    diagnostics["scene_plan_uses_segment_id_canonical"] = bool(scene_diag.get("uses_segment_id_canonical"))
    diagnostics["scene_plan_uses_legacy_scene_candidate_windows_bridge"] = bool(scene_diag.get("scene_candidate_windows_bridge"))
    diagnostics["scene_plan_uses_legacy_compiled_contract_bridge"] = bool(scene_diag.get("compiled_contract_bridge"))
    diagnostics["scene_plan_role_source_precedence"] = _safe_list(scene_diag.get("role_source_precedence"))
    diagnostics["scene_plan_configured_timeout_sec"] = int(
        scene_diag.get("configured_timeout_sec") or diagnostics.get("scene_plan_configured_timeout_sec") or 0
    )
    diagnostics["scene_plan_timeout_stage_policy_name"] = str(
        scene_diag.get("timeout_stage_policy_name") or diagnostics.get("scene_plan_timeout_stage_policy_name") or ""
    )
    diagnostics["scene_plan_timed_out"] = bool(scene_diag.get("timed_out"))
    diagnostics["scene_plan_timeout_retry_attempted"] = bool(scene_diag.get("timeout_retry_attempted"))
    diagnostics["scene_plan_response_was_empty_after_timeout"] = bool(scene_diag.get("response_was_empty_after_timeout"))
    diagnostics["scene_plan_route_budget_ok"] = bool(route_budget_ok)
    diagnostics["scene_plan_route_budget_target"] = _safe_dict(route_budget_meta.get("target_route_mix"))
    diagnostics["scene_plan_route_budget_actual"] = _safe_dict(route_budget_meta.get("actual_route_mix"))
    diagnostics["route_budget_original_targets"] = _safe_dict(
        route_budget_meta.get("route_budget_original_targets") or scene_diag.get("route_budget_original_targets")
    )
    diagnostics["route_budget_resolved_scene_count"] = int(
        route_budget_meta.get("route_budget_resolved_scene_count") or scene_diag.get("route_budget_resolved_scene_count") or 0
    )
    diagnostics["route_budget_resolved_targets"] = _safe_dict(
        route_budget_meta.get("route_budget_resolved_targets") or scene_diag.get("route_budget_resolved_targets")
    )
    diagnostics["route_budget_resolved_from"] = str(
        route_budget_meta.get("route_budget_resolved_from") or scene_diag.get("route_budget_resolved_from") or ""
    )
    diagnostics["route_budget_preset"] = str(route_budget_meta.get("route_budget_preset") or scene_diag.get("route_budget_preset") or "")
    diagnostics["scene_plan_max_consecutive_lipsync"] = int(route_budget_meta.get("max_consecutive_lipsync") or 0)
    diagnostics["scene_plan_longest_lipsync_streak"] = int(route_budget_meta.get("longest_lipsync_streak") or 0)
    diagnostics["scene_plan_all_lipsync_mode"] = bool(route_budget_meta.get("all_lipsync_mode"))
    diagnostics["scene_plan_lipsync_streak_guard_relaxed"] = bool(route_budget_meta.get("lipsync_streak_guard_relaxed"))
    diagnostics["scene_plan_route_budget_validation_mode"] = str(route_budget_meta.get("route_budget_validation_mode") or "mixed")
    diagnostics["scene_plan_route_budget_mode"] = str(route_budget_meta.get("route_budget_mode") or diagnostics.get("scene_plan_route_budget_mode") or "creative_config_soft")
    diagnostics["scene_plan_first_last_forbidden"] = bool(route_budget_meta.get("scene_plan_first_last_forbidden", True))
    diagnostics["scene_plan_first_last_missing_is_ok"] = bool(route_budget_meta.get("scene_plan_first_last_missing_is_ok", False))
    diagnostics["scene_plan_route_budget_old_first_last_requirement_suppressed"] = bool(route_budget_meta.get("scene_plan_route_budget_old_first_last_requirement_suppressed"))
    diagnostics["scene_plan_lipsync_streak_warning"] = str(route_budget_meta.get("lipsync_streak_warning") or "")
    diagnostics["scene_plan_route_budget_mismatch"] = not bool(route_budget_ok)
    diagnostics["scene_plan_enum_invalid_detected"] = bool(scene_diag.get("scene_plan_enum_invalid_detected"))
    diagnostics["scene_plan_enum_invalid_count"] = int(scene_diag.get("scene_plan_enum_invalid_count") or 0)
    diagnostics["scene_plan_enum_invalid_field"] = str(scene_diag.get("scene_plan_enum_invalid_field") or "")
    diagnostics["scene_plan_enum_invalid_value"] = str(scene_diag.get("scene_plan_enum_invalid_value") or "")
    diagnostics["scene_plan_enum_invalid_allowed_values"] = _safe_list(scene_diag.get("scene_plan_enum_invalid_allowed_values"))
    diagnostics["scene_plan_enum_invalid_segment_id"] = str(scene_diag.get("scene_plan_enum_invalid_segment_id") or "")
    diagnostics["scene_plan_enum_invalid_rows"] = _safe_list(scene_diag.get("scene_plan_enum_invalid_rows"))
    diagnostics["scene_plan_enum_repair_applied"] = bool(scene_diag.get("scene_plan_enum_repair_applied"))
    diagnostics["scene_plan_enum_repair_count"] = int(scene_diag.get("scene_plan_enum_repair_count") or 0)
    diagnostics["scene_plan_enum_repair_rows"] = _safe_list(scene_diag.get("scene_plan_enum_repair_rows"))
    diagnostics["scene_plan_enum_unrepaired_count"] = int(scene_diag.get("scene_plan_enum_unrepaired_count") or 0)
    diagnostics["scene_plan_enum_unrepaired_rows"] = _safe_list(scene_diag.get("scene_plan_enum_unrepaired_rows"))
    current_validated_route_map = _scene_plan_routes_by_segment(scene_plan)
    diagnostics["scene_plan_route_locks_by_segment"] = _safe_dict(
        _safe_dict(scene_plan.get("route_locks_by_segment")) or current_validated_route_map
    )
    diagnostics["scene_plan_requested_route_locks_by_segment"] = _safe_dict(route_locks_by_segment)
    diagnostics["scene_plan_route_lock_applied"] = bool(route_locks_by_segment)
    diagnostics["scene_plan_route_lock_source"] = str(route_lock_source or "")
    diagnostics["scene_plan_hard_route_map_enabled"] = bool(backend_hard_route_map)
    diagnostics["scene_plan_hard_route_map_source"] = str(backend_hard_route_source or "")
    diagnostics["scene_plan_hard_route_map_by_segment"] = dict(backend_hard_route_map)
    diagnostics["scene_plan_hard_route_map_target_counts"] = dict(backend_hard_route_target_counts)
    diagnostics["scene_plan_hard_route_map_actual_counts_after_normalize"] = dict(final_route_counts)
    diagnostics["scene_plan_route_assignment_source"] = str(
        "hard_route_map"
        if bool(backend_hard_route_map)
        else (
            "user_hard_route_map"
            if route_lock_source == "creative_config.route_assignments_by_segment"
            else (route_assignment_source or ("user_hard_route_map" if user_hard_route_map else ""))
        )
    )
    diagnostics["scene_plan_route_semantic_mismatches"] = list(route_semantic_mismatches)
    diagnostics["scene_plan_route_semantic_retry_used"] = bool(semantic_retry_used)
    diagnostics["scene_plan_route_budget_after_lock"] = dict(final_route_counts)
    diagnostics["scenes_vocal_owner_role_used"] = str(scene_diag.get("vocal_owner_role") or "unknown")
    if not route_budget_ok:
        diagnostics["scene_plan_validation_error"] = "route_budget_mismatch"
        diagnostics["scene_plan_error_code"] = "SCENES_ROUTE_BUDGET_MISMATCH"
        if "route_budget_mismatch" not in diagnostics["scene_plan_validation_errors"]:
            diagnostics["scene_plan_validation_errors"].append("route_budget_mismatch")
        if "SCENES_ROUTE_BUDGET_MISMATCH" not in diagnostics["scene_plan_error_codes"]:
            diagnostics["scene_plan_error_codes"].append("SCENES_ROUTE_BUDGET_MISMATCH")
    if int(diagnostics.get("scene_plan_enum_unrepaired_count") or 0) > 0:
        if "enum_invalid" not in diagnostics["scene_plan_validation_errors"]:
            diagnostics["scene_plan_validation_errors"].append("enum_invalid")
        if "SCENES_ENUM_INVALID" not in diagnostics["scene_plan_error_codes"]:
            diagnostics["scene_plan_error_codes"].append("SCENES_ENUM_INVALID")
    timeout_empty = bool(
        diagnostics.get("scene_plan_timed_out")
        or diagnostics.get("scene_plan_response_was_empty_after_timeout")
        or (
            diagnostics.get("scene_plan_segment_count_actual") == 0
            and (
                diagnostics.get("scene_plan_retry_timed_out")
                or diagnostics.get("scene_plan_retry_empty_response")
            )
        )
    )
    if timeout_empty and int(diagnostics.get("scene_plan_segment_count_actual") or 0) == 0:
        diagnostics["scene_plan_validation_error"] = "scene_plan_timeout_empty_response"
        diagnostics["scene_plan_error_code"] = "SCENES_TIMEOUT_EMPTY_RESPONSE"
        diagnostics["scene_plan_failure_reason"] = "scene_plan_timeout_empty_response"
    segment_coverage_ok = final_segment_coverage_ok
    enum_unrepaired_count = int(diagnostics.get("scene_plan_enum_unrepaired_count") or 0)
    scene_plan_empty = not bool(scene_plan and _safe_list(scene_plan.get("storyboard")))
    has_real_error = bool(timeout_empty or enum_unrepaired_count > 0 or scene_plan_empty or (not segment_coverage_ok))
    route_budget_validation_error = str(result.get("validation_error") or "").strip().lower() == "route_budget_mismatch"
    route_budget_diag_error = str(diagnostics.get("scene_plan_error") or "").strip().lower() == "route_budget_mismatch"
    route_budget_diag_failure = str(diagnostics.get("scene_plan_failure_reason") or "").strip().lower() == "route_budget_mismatch"
    route_budget_diag_last_failed = str(diagnostics.get("scene_plan_last_failed_candidate_error") or "").strip().lower() == "route_budget_mismatch"
    if route_budget_ok and route_budget_validation_error:
        result["validation_error"] = ""
        result["error"] = ""
        result["error_code"] = ""
    if route_budget_ok and (route_budget_diag_error or route_budget_diag_failure or route_budget_diag_last_failed):
        diagnostics["scene_plan_error"] = ""
        diagnostics["scene_plan_failure_reason"] = ""
        diagnostics["scene_plan_last_failed_candidate_error"] = ""
    post_validity_rebalance_diag = _safe_dict(diagnostics.get("scene_plan_post_validity_route_rebalance"))
    soft_targets_enabled = bool(_safe_dict(route_budget_meta.get("creative_config")).get("targets_are_soft"))
    no_valid_ia2v_replacement_candidate = bool(
        post_validity_rebalance_diag.get("attempted")
        and int(post_validity_rebalance_diag.get("upgraded") or 0) == 0
        and int(post_validity_rebalance_diag.get("missing_ia2v") or 0) > 0
    )
    only_route_budget_shortfall = bool(
        (not route_budget_ok)
        and (not has_real_error)
        and str(result.get("validation_error") or "").strip().lower() == "route_budget_mismatch"
    )
    soft_route_budget_shortfall_accepted = bool(
        soft_targets_enabled and segment_coverage_ok and only_route_budget_shortfall and no_valid_ia2v_replacement_candidate
    )
    diagnostics["scene_plan_soft_route_budget_shortfall_accepted"] = bool(soft_route_budget_shortfall_accepted)
    diagnostics["scene_plan_soft_route_budget_target"] = (
        _safe_dict(route_budget_meta.get("target_route_mix")) if soft_route_budget_shortfall_accepted else {}
    )
    diagnostics["scene_plan_soft_route_budget_actual"] = (
        _safe_dict(route_budget_meta.get("actual_route_mix")) if soft_route_budget_shortfall_accepted else {}
    )
    diagnostics["scene_plan_soft_route_budget_reason"] = (
        "no_valid_ia2v_replacement_candidate" if soft_route_budget_shortfall_accepted else ""
    )
    if soft_route_budget_shortfall_accepted:
        _append_diag_event(
            package,
            "scene_plan soft route budget shortfall accepted: no valid ia2v replacement candidate",
            stage_id="scene_plan",
        )
    route_budget_acceptance_ok = bool(route_budget_ok or soft_route_budget_shortfall_accepted)
    if route_budget_acceptance_ok and (not has_real_error):
        result["ok"] = True
        result["validation_error"] = ""
        result["error"] = ""
        result["error_code"] = ""
        diagnostics["scene_plan_validation_error"] = ""
        diagnostics["scene_plan_error_code"] = ""
        diagnostics["scene_plan_error"] = ""
        diagnostics["scene_plan_failure_reason"] = ""
        diagnostics["scene_plan_last_failed_candidate_error"] = ""
        diagnostics["scene_plan_validation_errors"] = [
            error for error in _safe_list(diagnostics.get("scene_plan_validation_errors"))
            if str(error or "").strip() != "route_budget_mismatch"
        ]
        diagnostics["scene_plan_error_codes"] = [
            code for code in _safe_list(diagnostics.get("scene_plan_error_codes"))
            if str(code or "").strip() != "SCENES_ROUTE_BUDGET_MISMATCH"
        ]
        # Clear stale pre-repair hard-fail markers once the post-repair candidate passes
        # all final acceptance gates (budget, coverage, schema/enum, timeout).
        hard_fail_error = ""
    diagnostics["validation_error"] = str(diagnostics.get("scene_plan_validation_error") or "")
    diagnostics["scene_plan_error"] = str(result.get("error") or "")
    if timeout_empty and int(diagnostics.get("scene_plan_segment_count_actual") or 0) == 0:
        diagnostics["scene_plan_error"] = "scene_plan_timeout"
    diagnostics["scene_plan_empty"] = scene_plan_empty
    if timeout_empty and diagnostics["scene_plan_empty"]:
        hard_fail_error = "scene_plan_timeout_empty_response"
    if not hard_fail_error:
        result_has_validation_error = bool(str(result.get("validation_error") or "").strip())
        if (not bool(result.get("ok"))) or result_has_validation_error:
            hard_fail_error = str(result.get("validation_error") or result.get("error") or "scene_plan_invalid")
    package["diagnostics"] = diagnostics
    if hard_fail_error:
        failed_rows = _scene_plan_rows_for_validation(scene_plan)
        failed_routes = [
            str(_safe_dict(row).get("route") or "").strip().lower()
            for row in failed_rows
        ]
        failed_segment_ids = [
            str(_safe_dict(row).get("segment_id") or _safe_dict(row).get("scene_id") or "").strip()
            for row in failed_rows
        ]
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics["scene_plan_failed_candidate_preview"] = failed_rows[:3]
        diagnostics["scene_plan_failed_candidate_route_mix"] = _safe_dict(route_budget_meta.get("actual_route_mix"))
        diagnostics["scene_plan_failed_candidate_rows_count"] = len(failed_rows)
        diagnostics["scene_plan_failed_candidate_segment_ids"] = [sid for sid in failed_segment_ids if sid]
        diagnostics["scene_plan_failed_candidate_first_routes"] = failed_routes[:5]
        diagnostics["scene_plan_failed_candidate_validation_errors"] = _safe_list(diagnostics.get("scene_plan_validation_errors"))
        package["diagnostics"] = diagnostics
        can_restore_snapshot = _scene_plan_snapshot_restore_is_safe(
            package=package,
            previous_scene_plan=previous_scene_plan,
            resolved_target_budget=_safe_dict(route_budget_meta.get("route_budget_resolved_targets")),
        )
        if previous_scene_plan_valid and can_restore_snapshot:
            package["scene_plan"] = _attach_downstream_mode_metadata(previous_scene_plan, package)
            diagnostics = _safe_dict(package.get("diagnostics"))
            diagnostics["scene_plan_snapshot_restored"] = True
            diagnostics["scene_plan_failure_reason"] = str(hard_fail_error)
            diagnostics["scene_plan_candidate_failed_but_snapshot_restored"] = True
            diagnostics["scene_plan_last_failed_candidate_error"] = str(hard_fail_error)
            diagnostics["validation_error"] = ""
            diagnostics["scene_plan_validation_error"] = ""
            diagnostics["scene_plan_error"] = ""
            diagnostics["scene_plan_error_code"] = ""
            package["diagnostics"] = diagnostics
            _append_diag_event(package, "scene_plan invalid: restored previous snapshot", stage_id="scene_plan")
            _append_diag_event(
                package,
                "scene_plan candidate failed but previous valid snapshot restored",
                stage_id="scene_plan",
            )
            return package
        else:
            diagnostics = _safe_dict(package.get("diagnostics"))
            diagnostics["scene_plan_snapshot_restored"] = False
            diagnostics["scene_plan_failure_reason"] = str(hard_fail_error)
            diagnostics["scene_plan_candidate_failed_but_snapshot_restored"] = False
            diagnostics["scene_plan_last_failed_candidate_error"] = str(hard_fail_error)
            package["diagnostics"] = diagnostics
            _append_diag_event(package, "scene_plan invalid: no previous snapshot", stage_id="scene_plan")
        _append_diag_event(package, f"scene_plan hard fail after retry: {hard_fail_error}", stage_id="scene_plan")
        raise RuntimeError(hard_fail_error)

    current_signature = _current_scenario_input_signature(package)
    current_route_strategy_signature = _route_strategy_signature_for_package(package)
    if current_signature:
        scene_plan["created_for_signature"] = current_signature
    scene_plan["created_for_signature"] = str(scene_plan.get("created_for_signature") or current_signature or "")
    scene_plan["route_strategy_signature"] = current_route_strategy_signature
    scene_plan["route_locks_by_segment"] = _safe_dict(scene_plan.get("route_locks_by_segment") or route_locks_by_segment)
    for stale_error_key in (
        "error",
        "validation_error",
        "validationError",
        "failure_reason",
        "failureReason",
        "retry_error",
        "retryError",
        "last_error",
        "lastError",
    ):
        scene_plan.pop(stale_error_key, None)
    package["scene_plan"] = _attach_downstream_mode_metadata(scene_plan, package)
    role_scene_sync = _sync_role_scene_route_semantics(package)
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["role_scene_route_sync_updated_count"] = int(_safe_dict(role_scene_sync).get("updated") or 0)
    diagnostics["role_scene_route_sync_ia2v_forced_visible_count"] = int(_safe_dict(role_scene_sync).get("ia2v_forced_visible") or 0)
    diagnostics["role_scene_route_sync_i2v_offscreen_repairs"] = int(_safe_dict(role_scene_sync).get("i2v_offscreen_repairs") or 0)
    diagnostics["scene_plan_created_for_signature"] = str(_safe_dict(package.get("scene_plan")).get("created_for_signature") or "")
    diagnostics["scene_plan_route_strategy_signature"] = current_route_strategy_signature
    diagnostics["scene_plan_created_for_signature"] = str(current_signature or diagnostics.get("scene_plan_created_for_signature") or "")
    diagnostics["scene_plan_snapshot_restored"] = False
    diagnostics["scene_plan_failure_reason"] = ""
    diagnostics["scene_plan_candidate_failed_but_snapshot_restored"] = False
    diagnostics["scene_plan_last_failed_candidate_error"] = ""
    package["diagnostics"] = diagnostics

    if scene_plan and _safe_list(scene_plan.get("storyboard")):
        _append_diag_event(package, "scene_plan generated", stage_id="scene_plan")
        if diagnostics.get("scene_plan_route_flat"):
            _append_diag_event(package, "scene_plan_route_flat warning", stage_id="scene_plan")
        if diagnostics.get("scene_plan_route_spacing_warning"):
            _append_diag_event(package, "scene_plan_route_spacing_warning", stage_id="scene_plan")
    else:
        _append_diag_event(package, "scene_plan empty", stage_id="scene_plan")
    return package




def _run_scene_prompts_stage(package: dict[str, Any]) -> dict[str, Any]:
    current_signature = _scene_prompts_upstream_signature(package)
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["scene_prompts_backend"] = "gemini"
    diagnostics["scene_prompts_prompt_version"] = SCENE_PROMPTS_PROMPT_VERSION
    diagnostics["scene_prompts_stage_source"] = "current_package"
    diagnostics["scene_prompts_rows_source_count"] = 0
    diagnostics["scene_prompts_rows_model_count"] = 0
    diagnostics["scene_prompts_rows_normalized_count"] = 0
    diagnostics["scene_prompts_repaired_from_current_package_count"] = 0
    diagnostics["scene_prompts_unrelated_rows_discarded_count"] = 0
    diagnostics["scene_prompts_used_fallback"] = False
    diagnostics["scene_prompts_scene_count"] = 0
    diagnostics["scene_prompts_missing_photo_count"] = 0
    diagnostics["scene_prompts_missing_video_count"] = 0
    diagnostics["scene_prompts_ia2v_audio_driven_count"] = 0
    diagnostics["scene_prompts_route_semantics_mismatch_count"] = 0
    diagnostics["scene_prompts_prompts_version"] = ""
    diagnostics["scene_prompts_segment_count_expected"] = 0
    diagnostics["scene_prompts_segment_count_actual"] = 0
    diagnostics["scene_prompts_segment_coverage_ok"] = False
    diagnostics["scene_prompts_uses_segment_id_canonical"] = False
    diagnostics["scene_prompts_uses_legacy_bridge"] = False
    diagnostics["scene_prompts_legacy_bridge_generated"] = False
    diagnostics["scene_prompts_legacy_bridge_present"] = False
    diagnostics["scene_prompts_legacy_bridge_mode"] = ""
    diagnostics["scene_prompts_canonical_source"] = ""
    diagnostics["scene_prompts_global_style_anchor_present"] = False
    diagnostics["scene_prompts_transition_required_count"] = 0
    diagnostics["scene_prompts_transition_present_count"] = 0
    diagnostics["scene_prompts_error_code"] = ""
    diagnostics["scene_prompts_raw_model_response_preview"] = ""
    diagnostics["scene_prompts_parsed_payload_preview"] = ""
    diagnostics["scene_prompts_sanitized_payload_preview"] = ""
    diagnostics["scene_prompts_normalized_scene_prompts_preview"] = ""
    diagnostics["scene_prompts_dropped_non_canonical_fields"] = []
    diagnostics["scene_prompts_expected_segment_ids"] = []
    diagnostics["scene_prompts_seen_segment_ids"] = []
    diagnostics["scene_prompts_missing_segment_ids"] = []
    diagnostics["scene_prompts_extra_segment_ids"] = []
    diagnostics["scene_prompts_snapshot_restored"] = False
    diagnostics["scene_prompts_failure_reason"] = ""
    diagnostics["scene_prompts_configured_timeout_sec"] = get_scenario_stage_timeout("scene_prompts")
    diagnostics["scene_prompts_timeout_stage_policy_name"] = scenario_timeout_policy_name("scene_prompts")
    diagnostics["scene_prompts_timed_out"] = False
    diagnostics["scene_prompts_timeout_retry_attempted"] = False
    diagnostics["scene_prompts_response_was_empty_after_timeout"] = False
    diagnostics["scene_prompts_empty_count"] = 0
    diagnostics["scene_prompts_empty_scene_ids"] = []
    diagnostics["scene_prompts_rebuilt_count"] = 0
    diagnostics["scene_prompts_rebuilt_scene_ids"] = []
    diagnostics["scene_prompts_valid_count"] = 0
    diagnostics["scene_prompts_legacy_bridge_used"] = False
    diagnostics["prompt_capability_guard_applied"] = False
    diagnostics["scene_prompts_validation_error"] = ""
    diagnostics["validation_error"] = ""
    diagnostics["scene_prompts_error"] = ""
    diagnostics["scene_prompts_empty"] = False
    diagnostics["scene_prompts_quality_pass_applied"] = False
    diagnostics["scene_prompts_quality_ia2v_variant_applied_count"] = 0
    diagnostics["scene_prompts_quality_world_i2v_conflict_fixed_count"] = 0
    diagnostics["scene_prompts_quality_video_prompt_deduped_count"] = 0
    diagnostics["scene_prompts_quality_world_anchor_strengthened"] = False
    previous_signature = str(diagnostics.get("scene_prompts_upstream_signature") or "")
    diagnostics["scene_prompts_upstream_changed"] = bool(previous_signature and previous_signature != current_signature)
    diagnostics["scene_prompts_upstream_signature"] = current_signature
    previous_scene_prompts = _safe_dict(package.get("scene_prompts"))
    previous_scene_prompts_valid = _has_valid_scene_prompts_payload(previous_scene_prompts)
    diagnostics["gemini_api_key_source"] = "missing"
    diagnostics["gemini_api_key_valid"] = False
    diagnostics["gemini_api_key_error"] = "empty"
    package["diagnostics"] = diagnostics
    package["scene_prompts"] = {"scenes": []}
    gemini_api_key = _resolve_stage_gemini_api_key(package, stage_id="scene_prompts")

    hard_fail_error = ""

    result = build_gemini_scene_prompts(
        api_key=gemini_api_key,
        package=package,
    )
    normalized_scene_prompts, normalized_validation_error, normalized_diag = _enforce_scene_prompts_identity_and_presence(
        package,
        _safe_dict(result.get("scene_prompts")),
    )
    result = _apply_scene_prompts_enforcement_result(
        result,
        normalized_scene_prompts,
        normalized_validation_error,
        normalized_diag,
    )
    result = _postprocess_scene_prompts_technical_tagging(result)
    initial_validation_error = str(result.get("validation_error") or "").strip()
    if initial_validation_error:
        initial_diag = _safe_dict(result.get("diagnostics"))
        if bool(initial_diag.get("scene_prompts_timed_out")):
            initial_diag["scene_prompts_timeout_retry_attempted"] = True
            result["diagnostics"] = initial_diag
        validation_error_code = str(result.get("error_code") or _safe_dict(result.get("diagnostics")).get("scene_prompts_error_code") or "").strip()
        validation_feedback = _build_scene_prompts_retry_feedback(initial_validation_error, validation_error_code)
        _append_diag_event(package, f"scene_prompts validation failed, retrying once: {validation_feedback}", stage_id="scene_prompts")
        retry_result = build_gemini_scene_prompts(
            api_key=gemini_api_key,
            package=package,
            validation_feedback=validation_feedback,
            compact_retry=True,
        )
        result = retry_result
        normalized_scene_prompts, normalized_validation_error, normalized_diag = _enforce_scene_prompts_identity_and_presence(
            package,
            _safe_dict(result.get("scene_prompts")),
        )
        result = _apply_scene_prompts_enforcement_result(
            result,
            normalized_scene_prompts,
            normalized_validation_error,
            normalized_diag,
        )
        result = _postprocess_scene_prompts_technical_tagging(result)
        if str(result.get("validation_error") or "").strip():
            retry_diag = _safe_dict(result.get("diagnostics"))
            timeout_still = bool(retry_diag.get("scene_prompts_timed_out"))
            empty_after_timeout = bool(retry_diag.get("scene_prompts_response_was_empty_after_timeout"))
            if timeout_still and empty_after_timeout:
                _append_diag_event(
                    package,
                    "scene_prompts timeout/empty after compact retry, rebuilding from current scene_plan",
                    stage_id="scene_prompts",
                )
                result = build_gemini_scene_prompts(
                    api_key=gemini_api_key,
                    package=package,
                    force_rebuild_from_scene_plan=True,
                )
                normalized_scene_prompts, normalized_validation_error, normalized_diag = _enforce_scene_prompts_identity_and_presence(
                    package,
                    _safe_dict(result.get("scene_prompts")),
                )
                result = _apply_scene_prompts_enforcement_result(
                    result,
                    normalized_scene_prompts,
                    normalized_validation_error,
                    normalized_diag,
                )
                result = _postprocess_scene_prompts_technical_tagging(result)
            if str(result.get("validation_error") or "").strip():
                result["ok"] = False
                result["error"] = str(result.get("error") or result.get("validation_error") or "scene_prompts_validation_failed")
                hard_fail_error = str(result.get("validation_error") or result.get("error") or "scene_prompts_validation_failed")

    quality_scene_prompts, quality_diag = _scene_prompts_quality_pass(package, _safe_dict(result.get("scene_prompts")))
    result["scene_prompts"] = quality_scene_prompts
    result_diag = _safe_dict(result.get("diagnostics"))
    result_diag.update(quality_diag)
    result["diagnostics"] = result_diag
    scene_prompts = _safe_dict(result.get("scene_prompts"))
    package["scene_prompts"] = _attach_downstream_mode_metadata(scene_prompts, package)

    prompts_diag = _safe_dict(result.get("diagnostics"))
    diagnostics = _safe_dict(package.get("diagnostics"))
    for key, value in prompts_diag.items():
        if str(key).startswith("scene_prompts_"):
            diagnostics[str(key)] = value

    stage_keys = (
        "prompt_version",
        "stage_source",
        "rows_source_count",
        "rows_model_count",
        "rows_normalized_count",
        "repaired_from_current_package_count",
        "unrelated_rows_discarded_count",
        "missing_photo_count",
        "missing_video_count",
        "ia2v_audio_driven_count",
        "active_video_model_capability_profile",
        "active_route_capability_mode",
        "capability_rules_source_version",
        "scene_prompts_runtime_marker",
    )
    for key in stage_keys:
        if key in prompts_diag:
            diagnostics[key] = prompts_diag.get(key)

    alias_map = {
        "prompt_version": "scene_prompts_prompt_version",
        "stage_source": "scene_prompts_stage_source",
        "rows_source_count": "scene_prompts_rows_source_count",
        "rows_model_count": "scene_prompts_rows_model_count",
        "rows_normalized_count": "scene_prompts_rows_normalized_count",
        "repaired_from_current_package_count": "scene_prompts_repaired_from_current_package_count",
        "unrelated_rows_discarded_count": "scene_prompts_unrelated_rows_discarded_count",
        "missing_photo_count": "scene_prompts_missing_photo_count",
        "missing_video_count": "scene_prompts_missing_video_count",
        "ia2v_audio_driven_count": "scene_prompts_ia2v_audio_driven_count",
    }
    for source_key, target_key in alias_map.items():
        if source_key in prompts_diag:
            diagnostics[target_key] = prompts_diag.get(source_key)

    diagnostics["scene_prompts_backend"] = str(
        prompts_diag.get("scene_prompts_backend") or diagnostics.get("scene_prompts_backend") or "gemini"
    )
    diagnostics["scene_prompts_used_fallback"] = bool(result.get("used_fallback"))
    diagnostics["scene_prompts_scene_count"] = int(prompts_diag.get("scene_count") or len(_safe_list(scene_prompts.get("segments"))))
    diagnostics["scene_prompts_prompts_version"] = str(
        prompts_diag.get("scene_prompts_prompts_version") or scene_prompts.get("prompts_version") or ""
    )
    diagnostics["scene_prompts_segment_count_expected"] = int(prompts_diag.get("scene_prompts_segment_count_expected") or 0)
    diagnostics["scene_prompts_segment_count_actual"] = int(
        prompts_diag.get("scene_prompts_segment_count_actual") or len(_safe_list(scene_prompts.get("segments")))
    )
    diagnostics["scene_prompts_segment_coverage_ok"] = bool(prompts_diag.get("scene_prompts_segment_coverage_ok"))
    diagnostics["scene_prompts_uses_segment_id_canonical"] = bool(prompts_diag.get("scene_prompts_uses_segment_id_canonical"))
    diagnostics["scene_prompts_uses_legacy_bridge"] = bool(prompts_diag.get("scene_prompts_uses_legacy_bridge"))
    diagnostics["scene_prompts_global_style_anchor_present"] = bool(
        prompts_diag.get("scene_prompts_global_style_anchor_present") or scene_prompts.get("global_style_anchor")
    )
    diagnostics["scene_prompts_transition_required_count"] = int(prompts_diag.get("scene_prompts_transition_required_count") or 0)
    diagnostics["scene_prompts_transition_present_count"] = int(prompts_diag.get("scene_prompts_transition_present_count") or 0)
    diagnostics["scene_prompts_error_code"] = str(prompts_diag.get("scene_prompts_error_code") or result.get("error_code") or "")
    diagnostics["scene_prompts_raw_model_response_preview"] = str(prompts_diag.get("scene_prompts_raw_model_response_preview") or "")
    diagnostics["scene_prompts_parsed_payload_preview"] = str(prompts_diag.get("scene_prompts_parsed_payload_preview") or "")
    diagnostics["scene_prompts_sanitized_payload_preview"] = str(prompts_diag.get("scene_prompts_sanitized_payload_preview") or "")
    diagnostics["scene_prompts_normalized_scene_prompts_preview"] = str(
        prompts_diag.get("scene_prompts_normalized_scene_prompts_preview") or ""
    )
    diagnostics["scene_prompts_dropped_non_canonical_fields"] = _safe_list(
        prompts_diag.get("scene_prompts_dropped_non_canonical_fields")
    )
    diagnostics["scene_prompts_expected_segment_ids"] = _safe_list(prompts_diag.get("scene_prompts_expected_segment_ids"))
    diagnostics["scene_prompts_seen_segment_ids"] = _safe_list(prompts_diag.get("scene_prompts_seen_segment_ids"))
    diagnostics["scene_prompts_missing_segment_ids"] = _safe_list(prompts_diag.get("scene_prompts_missing_segment_ids"))
    diagnostics["scene_prompts_extra_segment_ids"] = _safe_list(prompts_diag.get("scene_prompts_extra_segment_ids"))
    diagnostics["scene_prompts_failure_reason"] = str(prompts_diag.get("scene_prompts_failure_reason") or "")
    diagnostics["scene_prompts_configured_timeout_sec"] = int(
        prompts_diag.get("scene_prompts_configured_timeout_sec") or diagnostics.get("scene_prompts_configured_timeout_sec") or 0
    )
    diagnostics["scene_prompts_timeout_stage_policy_name"] = str(
        prompts_diag.get("scene_prompts_timeout_stage_policy_name") or diagnostics.get("scene_prompts_timeout_stage_policy_name") or ""
    )
    diagnostics["scene_prompts_timed_out"] = bool(prompts_diag.get("scene_prompts_timed_out"))
    diagnostics["scene_prompts_timeout_retry_attempted"] = bool(prompts_diag.get("scene_prompts_timeout_retry_attempted"))
    diagnostics["scene_prompts_response_was_empty_after_timeout"] = bool(
        prompts_diag.get("scene_prompts_response_was_empty_after_timeout")
    )
    diagnostics["scene_prompts_route_semantics_mismatch_count"] = int(
        prompts_diag.get("scene_prompts_route_semantics_mismatch_count")
        or diagnostics.get("scene_prompts_route_semantics_mismatch_count")
        or 0
    )
    diagnostics["active_video_model_capability_profile"] = str(
        prompts_diag.get("active_video_model_capability_profile") or diagnostics.get("active_video_model_capability_profile") or ""
    )
    diagnostics["active_route_capability_mode"] = str(
        prompts_diag.get("active_route_capability_mode") or diagnostics.get("active_route_capability_mode") or ""
    )
    diagnostics["prompt_capability_guard_applied"] = bool(prompts_diag.get("prompt_capability_guard_applied"))
    diagnostics["capability_rules_source_version"] = str(
        prompts_diag.get("capability_rules_source_version") or diagnostics.get("capability_rules_source_version") or ""
    )
    diagnostics["scene_prompts_validation_error"] = str(result.get("validation_error") or "")
    diagnostics["validation_error"] = str(diagnostics.get("scene_prompts_validation_error") or "")
    diagnostics["scene_prompts_error"] = str(result.get("error") or "")
    diagnostics["scene_prompts_empty"] = not bool(scene_prompts and _safe_list(scene_prompts.get("segments")))
    if _scene_prompts_result_has_no_blocking_errors(result):
        result["ok"] = True
        result["error"] = ""
        diagnostics["scene_prompts_error"] = ""
    if not hard_fail_error:
        result_has_validation_error = bool(str(result.get("validation_error") or "").strip())
        result_has_segments = bool(_safe_list(scene_prompts.get("segments")))
        result_prompts_version = str(scene_prompts.get("prompts_version") or "").strip()
        coverage_ok = bool(diagnostics.get("scene_prompts_segment_coverage_ok"))
        if (not bool(result.get("ok"))) or result_has_validation_error or (not result_has_segments) or (result_prompts_version != "1.1") or (not coverage_ok):
            hard_fail_error = str(result.get("validation_error") or result.get("error") or "scene_prompts_invalid")
            diagnostics["scene_prompts_failure_reason"] = diagnostics.get("scene_prompts_failure_reason") or hard_fail_error
    package["diagnostics"] = diagnostics

    if hard_fail_error:
        if previous_scene_prompts_valid:
            package["scene_prompts"] = previous_scene_prompts
            diagnostics = _safe_dict(package.get("diagnostics"))
            diagnostics["scene_prompts_snapshot_restored"] = True
            package["diagnostics"] = diagnostics
        _append_diag_event(package, f"scene_prompts hard fail after retry: {hard_fail_error}", stage_id="scene_prompts")
        raise RuntimeError(hard_fail_error)

    if scene_prompts and _safe_list(scene_prompts.get("segments")):
        _append_diag_event(package, "scene_prompts generated", stage_id="scene_prompts")
        if int(diagnostics.get("scene_prompts_route_semantics_mismatch_count") or 0) > 0:
            _append_diag_event(package, "scene_prompts_route_semantics_mismatch warning", stage_id="scene_prompts")
    else:
        _append_diag_event(package, "scene_prompts empty", stage_id="scene_prompts")
    return package

def _run_final_video_prompt_stage(package: dict[str, Any]) -> dict[str, Any]:
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["final_video_prompt_backend"] = "gemini"
    diagnostics["final_video_prompt_prompt_version"] = FINAL_VIDEO_PROMPT_STAGE_VERSION
    diagnostics["final_video_prompt_segment_count"] = 0
    diagnostics["final_video_prompt_error"] = ""
    diagnostics["final_video_prompt_snapshot_restored"] = False
    diagnostics["final_video_prompt_configured_timeout_sec"] = get_scenario_stage_timeout("final_video_prompt")
    diagnostics["final_video_prompt_timeout_stage_policy_name"] = scenario_timeout_policy_name("final_video_prompt")
    diagnostics["final_video_prompt_timed_out"] = False
    diagnostics["final_video_prompt_timeout_retry_attempted"] = False
    diagnostics["final_video_prompt_response_was_empty_after_timeout"] = False
    diagnostics["final_video_prompt_snapshot_restore_blocked_by_signature"] = False
    diagnostics["final_video_prompt_snapshot_restore_block_reason"] = ""
    diagnostics["final_video_prompt_identity_gender_conflict_detected"] = False
    diagnostics["final_video_prompt_identity_gender_conflict_terms_removed"] = []
    diagnostics["final_video_prompt_identity_gender_conflict_segments"] = []
    diagnostics["final_video_prompt_validation_checked_after_sanitizer"] = False
    diagnostics["final_video_prompt_stale_identity_remaining_segments"] = []
    diagnostics["gemini_api_key_source"] = "missing"
    diagnostics["gemini_api_key_valid"] = False
    diagnostics["gemini_api_key_error"] = "empty"
    package["diagnostics"] = diagnostics
    gemini_api_key = _resolve_stage_gemini_api_key(package, stage_id="final_video_prompt")

    previous_payload = _safe_dict(package.get("final_video_prompt"))
    current_signature = _current_scenario_input_signature(package)
    current_snapshot_meta = _collect_final_video_prompt_snapshot_meta(package)
    current_character_ctx = _final_video_prompt_character_1_context(package)

    def _apply_result_diagnostics(result_payload: dict[str, Any]) -> None:
        diag = _safe_dict(result_payload.get("diagnostics"))
        local_diagnostics = _safe_dict(package.get("diagnostics"))
        local_diagnostics["final_video_prompt_backend"] = str(diag.get("final_video_prompt_backend") or "gemini")
        local_diagnostics["final_video_prompt_prompt_version"] = str(
            diag.get("final_video_prompt_prompt_version") or FINAL_VIDEO_PROMPT_STAGE_VERSION
        )
        local_diagnostics["final_video_prompt_segment_count"] = int(diag.get("final_video_prompt_segment_count") or 0)
        local_diagnostics["final_video_prompt_used_fallback"] = bool(diag.get("final_video_prompt_used_fallback"))
        local_diagnostics["final_video_prompt_error"] = str(result_payload.get("error") or "")
        local_diagnostics["final_video_prompt_configured_timeout_sec"] = int(
            diag.get("final_video_prompt_configured_timeout_sec") or local_diagnostics.get("final_video_prompt_configured_timeout_sec") or 0
        )
        local_diagnostics["final_video_prompt_timeout_stage_policy_name"] = str(
            diag.get("final_video_prompt_timeout_stage_policy_name") or local_diagnostics.get("final_video_prompt_timeout_stage_policy_name") or ""
        )
        local_diagnostics["final_video_prompt_timed_out"] = bool(diag.get("final_video_prompt_timed_out"))
        local_diagnostics["final_video_prompt_timeout_retry_attempted"] = bool(diag.get("final_video_prompt_timeout_retry_attempted"))
        local_diagnostics["final_video_prompt_response_was_empty_after_timeout"] = bool(
            diag.get("final_video_prompt_response_was_empty_after_timeout")
        )
        local_diagnostics["final_video_prompt_identity_gender_conflict_detected"] = bool(
            diag.get("final_video_prompt_identity_gender_conflict_detected")
        )
        local_diagnostics["final_video_prompt_identity_gender_conflict_terms_removed"] = _safe_list(
            diag.get("final_video_prompt_identity_gender_conflict_terms_removed")
        )
        local_diagnostics["final_video_prompt_identity_gender_conflict_segments"] = _safe_list(
            diag.get("final_video_prompt_identity_gender_conflict_segments")
        )
        local_diagnostics["final_video_prompt_validation_checked_after_sanitizer"] = bool(
            diag.get("final_video_prompt_validation_checked_after_sanitizer")
        )
        local_diagnostics["final_video_prompt_stale_identity_remaining_segments"] = _safe_list(
            diag.get("final_video_prompt_stale_identity_remaining_segments")
        )
        local_diagnostics["current_character_1_gender_hint"] = str(current_character_ctx.get("gender_hint") or "")
        local_diagnostics["current_character_1_identity_label"] = str(current_character_ctx.get("identity_label") or "")
        local_diagnostics["current_character_1_ref_signature"] = str(current_character_ctx.get("ref_signature") or "")
        package["diagnostics"] = local_diagnostics

    result = generate_ltx_video_prompt_metadata(
        api_key=gemini_api_key,
        package=package,
    )
    _apply_result_diagnostics(result)

    final_video_prompt = _safe_dict(result.get("final_video_prompt"))
    if _safe_list(final_video_prompt.get("segments")):
        final_video_prompt["created_for_signature"] = current_signature
        final_video_prompt["snapshot_compatibility"] = current_snapshot_meta
        package["final_video_prompt"] = final_video_prompt
        diagnostics = _safe_dict(package.get("diagnostics"))
        diagnostics["final_video_prompt_snapshot_restored"] = False
        diagnostics["final_video_prompt_snapshot_restore_blocked_by_signature"] = False
        diagnostics["final_video_prompt_snapshot_restore_block_reason"] = ""
        package["diagnostics"] = diagnostics
        _append_diag_event(package, "final_video_prompt generated", stage_id="final_video_prompt")
        return package

    previous_signature = str(previous_payload.get("created_for_signature") or "").strip()
    previous_meta = _safe_dict(previous_payload.get("snapshot_compatibility"))
    stale_conflict, stale_terms = _snapshot_has_gender_identity_conflict(
        previous_payload, gender_hint=str(current_character_ctx.get("gender_hint") or "")
    )
    signature_compatible = bool(not current_signature or not previous_signature or previous_signature == current_signature)
    compatibility_checks = {
        "upstream_signature": str(previous_meta.get("upstream_signature") or "") == str(current_snapshot_meta.get("upstream_signature") or ""),
        "character_1_ref_signature": str(previous_meta.get("character_1_ref_signature") or "")
        == str(current_snapshot_meta.get("character_1_ref_signature") or ""),
        "character_1_gender_hint": str(previous_meta.get("character_1_gender_hint") or "")
        == str(current_snapshot_meta.get("character_1_gender_hint") or ""),
        "character_1_identity_label": str(previous_meta.get("character_1_identity_label") or "")
        == str(current_snapshot_meta.get("character_1_identity_label") or ""),
        "route_map_signature": str(previous_meta.get("route_map_signature") or "") == str(current_snapshot_meta.get("route_map_signature") or ""),
        "segment_ids": _safe_list(previous_meta.get("segment_ids")) == _safe_list(current_snapshot_meta.get("segment_ids")),
        "scene_prompts_signature": str(previous_meta.get("scene_prompts_signature") or "")
        == str(current_snapshot_meta.get("scene_prompts_signature") or ""),
    }
    missing_meta = not previous_meta
    incompatible_reasons = [k for k, ok_flag in compatibility_checks.items() if not ok_flag]
    restore_block_reason = ""
    if not signature_compatible:
        restore_block_reason = "created_for_signature_mismatch"
    elif stale_conflict:
        restore_block_reason = f"stale_identity_gender_conflict:{','.join(stale_terms)}"
    elif missing_meta:
        restore_block_reason = "missing_snapshot_compatibility_metadata"
    elif incompatible_reasons:
        restore_block_reason = f"incompatible_snapshot:{','.join(incompatible_reasons)}"
    can_restore_snapshot = bool(previous_payload) and signature_compatible and (not restore_block_reason)
    package["final_video_prompt"] = previous_payload if can_restore_snapshot else package.get("final_video_prompt", {})
    diagnostics = _safe_dict(package.get("diagnostics"))
    diagnostics["final_video_prompt_snapshot_restored"] = bool(can_restore_snapshot)
    diagnostics["final_video_prompt_snapshot_restore_blocked_by_signature"] = bool(previous_payload and not can_restore_snapshot)
    diagnostics["final_video_prompt_snapshot_restore_block_reason"] = restore_block_reason
    if stale_conflict:
        diagnostics["final_video_prompt_identity_gender_conflict_detected"] = True
    diagnostics["current_character_1_gender_hint"] = str(current_character_ctx.get("gender_hint") or "")
    diagnostics["current_character_1_identity_label"] = str(current_character_ctx.get("identity_label") or "")
    diagnostics["current_character_1_ref_signature"] = str(current_character_ctx.get("ref_signature") or "")
    package["diagnostics"] = diagnostics
    if previous_payload and not can_restore_snapshot:
        package["final_video_prompt"] = STAGE_SECTION_RESETTERS["final_video_prompt"]()
        retry_result = generate_ltx_video_prompt_metadata(
            api_key=gemini_api_key,
            package=package,
        )
        _apply_result_diagnostics(retry_result)
        retry_payload = _safe_dict(retry_result.get("final_video_prompt"))
        if _safe_list(retry_payload.get("segments")):
            retry_payload["created_for_signature"] = current_signature
            retry_payload["snapshot_compatibility"] = current_snapshot_meta
            package["final_video_prompt"] = retry_payload
            diagnostics = _safe_dict(package.get("diagnostics"))
            diagnostics["final_video_prompt_snapshot_restored"] = False
            package["diagnostics"] = diagnostics
            _append_diag_event(package, "final_video_prompt generated after blocked snapshot restore", stage_id="final_video_prompt")
            return package
        _append_diag_event(package, "final_video_prompt empty after blocked snapshot retry", stage_id="final_video_prompt")
        raise RuntimeError(str(retry_result.get("error") or "final_video_prompt_empty"))
    _append_diag_event(package, "final_video_prompt empty", stage_id="final_video_prompt")
    raise RuntimeError(str(result.get("error") or "final_video_prompt_empty"))


def run_stage(stage_id: str, package: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if stage_id not in STAGE_IDS:
        raise ValueError(f"unknown_stage:{stage_id}")
    pkg = deepcopy(_safe_dict(package)) if package else create_storyboard_package(payload)
    _set_stage_status(pkg, stage_id, "running")
    pkg["updated_at"] = _utc_iso()

    deps = STAGE_DEPENDENCIES.get(stage_id, [])
    if stage_id == "final_video_prompt":
        missing_reasons, payload_ok_by_stage, status_by_stage, false_positive_prevented = _collect_final_video_prompt_dependency_gate_state(
            pkg, deps
        )
        final_video_prompt_gate_accepted = bool(
            all(bool(payload_ok_by_stage.get(dep_stage)) for dep_stage in deps)
        )
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["final_video_prompt_dependency_gate_mode"] = "payload_validity"
        diagnostics["final_video_prompt_dependency_status_by_stage"] = status_by_stage
        diagnostics["final_video_prompt_dependency_payload_ok_by_stage"] = payload_ok_by_stage
        diagnostics["final_video_prompt_dependency_gate_false_positive_prevented"] = bool(false_positive_prevented)
        diagnostics["final_video_prompt_dependency_gate_recomputed_after_payload_overrides"] = True
        diagnostics["final_video_prompt_missing_dependency_keys"] = list(
            dep_stage for dep_stage in deps if not bool(payload_ok_by_stage.get(dep_stage))
        )
        diagnostics["final_video_prompt_dependency_gate_accepted"] = bool(final_video_prompt_gate_accepted)
        diagnostics["final_video_prompt_missing_upstream_reasons"] = list(missing_reasons)
        diagnostics["final_video_prompt_force_current_stage_execution"] = False
        pkg["diagnostics"] = diagnostics
    if stage_id == "finalize":
        (
            _,
            payload_ok_by_stage,
            status_by_stage,
            false_positive_prevented,
        ) = _collect_finalize_dependency_gate_state(pkg, deps)
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["finalize_dependency_gate_mode"] = "payload_validity"
        diagnostics["finalize_dependency_status_by_stage"] = status_by_stage
        diagnostics["finalize_dependency_payload_ok_by_stage"] = payload_ok_by_stage
        diagnostics["finalize_dependency_gate_false_positive_prevented"] = bool(false_positive_prevented)
        pkg["diagnostics"] = diagnostics
    missing = [dep for dep in deps if not _is_stage_dependency_satisfied(pkg, stage_id, dep)]
    if missing:
        if stage_id == "final_video_prompt":
            missing_reasons, _, _, _ = _collect_final_video_prompt_dependency_gate_state(pkg, deps)
            error_code = f"final_video_prompt_incomplete_dependencies:{','.join(missing_reasons)}"
        elif stage_id == "finalize":
            missing_reasons, _, _, _ = _collect_finalize_dependency_gate_state(pkg, deps)
            diagnostics = _safe_dict(pkg.get("diagnostics"))
            diagnostics["finalize_missing_upstream_reasons"] = missing_reasons
            pkg["diagnostics"] = diagnostics
            error_code = f"finalize_incomplete_dependencies:{','.join(missing_reasons)}"
        else:
            error_code = f"missing_dependencies:{','.join(missing)}"
        _set_stage_status(pkg, stage_id, "error", error=error_code)
        _safe_dict(pkg.get("diagnostics")).setdefault("errors", []).append(f"{stage_id}: {error_code} {missing}")
        return pkg

    try:
        if stage_id == "input_package":
            pkg = _run_input_package_stage(pkg)
        elif stage_id == "audio_map":
            pkg = _run_audio_map_stage(pkg)
        elif stage_id == "story_core":
            pkg = _run_story_core_stage(pkg)
            story_core_ok, story_core_error_code, story_core_errors = _validate_story_core_result(pkg.get("story_core"))
            if not story_core_ok:
                diagnostics = _safe_dict(pkg.get("diagnostics"))
                diagnostics["story_core_last_error_code"] = story_core_error_code
                diagnostics["story_core_validation_errors"] = story_core_errors
                diagnostics["story_core_failed_payload_rejected"] = True
                diagnostics["story_core_hard_fail"] = True
                diagnostics["story_core_result_invalid"] = True
                diagnostics["story_core_invalid_required_fields"] = story_core_errors
                pkg["diagnostics"] = diagnostics
                pkg["story_core"] = {}
                pkg["story_core_v1"] = {}
                pkg["story_core_stale"] = True
                raise RuntimeError(f"{story_core_error_code}:{'; '.join(story_core_errors[:6])}")
        elif stage_id == "role_plan":
            pkg = _run_role_plan_stage(pkg)
        elif stage_id == "scene_plan":
            pkg = _run_scene_plan_stage(pkg)
        elif stage_id == "scene_prompts":
            pkg = _run_scene_prompts_stage(pkg)
        elif stage_id == "final_video_prompt":
            pkg = _run_final_video_prompt_stage(pkg)
        elif stage_id == "finalize":
            pkg = _run_finalize_stage(pkg)
        _set_stage_status(pkg, stage_id, "done")
        if stage_id == "final_video_prompt":
            diagnostics = _safe_dict(pkg.get("diagnostics"))
            diagnostics["final_video_prompt_executed"] = True
            pkg["diagnostics"] = diagnostics
        if stage_id == "story_core":
            _clear_stale_stage_failure_diagnostics(pkg, stage_id)
    except Exception as exc:  # noqa: BLE001
        _set_stage_status(pkg, stage_id, "error", error=str(exc))
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        errors = _safe_list(diagnostics.get("errors"))
        errors.append(f"{stage_id}: {exc}")
        diagnostics["errors"] = errors[-80:]
        if stage_id == "final_video_prompt":
            diagnostics["final_video_prompt_executed"] = False
        pkg["diagnostics"] = diagnostics

    _inject_route_strategy_diagnostics(pkg)
    pkg["updated_at"] = _utc_iso()
    return pkg


def run_manual_stage(
    stage_id: str,
    package: dict[str, Any],
    payload: dict[str, Any] | None = None,
    *,
    return_executed_stage_ids: bool = False,
) -> dict[str, Any] | tuple[dict[str, Any], list[str]]:
    if stage_id not in STAGE_IDS:
        raise ValueError(f"unknown_stage:{stage_id}")
    pkg = deepcopy(_safe_dict(package)) if package else create_storyboard_package(payload)
    executed_stage_ids: list[str] = []
    if stage_id == "final_video_prompt":
        deps = STAGE_DEPENDENCIES.get(stage_id, [])
        incoming_payload_summary = build_stage_payload_health_summary(pkg)
        missing_reasons, payload_ok_by_stage, status_by_stage, false_positive_prevented = _collect_final_video_prompt_dependency_gate_state(
            pkg, deps
        )
        audio_map = _safe_dict(pkg.get("audio_map"))
        audio_segments = _safe_list(audio_map.get("segments"))
        scene_prompts = _safe_dict(pkg.get("scene_prompts"))
        scene_prompts_segments = _safe_list(scene_prompts.get("segments")) or _safe_list(scene_prompts.get("scenes"))
        audio_map_ok_for_final_video_prompt = _has_valid_audio_map_payload_for_downstream_video(pkg)
        scene_prompts_ok_for_final_video_prompt = _has_valid_scene_prompts_payload_for_final_video_prompt(pkg)
        payload_ok_by_stage["audio_map"] = bool(audio_map_ok_for_final_video_prompt)
        payload_ok_by_stage["scene_prompts"] = bool(scene_prompts_ok_for_final_video_prompt)
        missing_reasons = [
            _final_video_prompt_dependency_reason(pkg, dep_stage) or f"missing_{dep_stage}_payload"
            for dep_stage in deps
            if not bool(payload_ok_by_stage.get(dep_stage))
        ]
        final_video_prompt_payload_gate_accepted = bool(
            all(bool(payload_ok_by_stage.get(dep_stage)) for dep_stage in deps)
        )
        reusable_upstream = [dep_stage for dep_stage in deps if payload_ok_by_stage.get(dep_stage)]
        missing_upstream = [dep_stage for dep_stage in deps if not payload_ok_by_stage.get(dep_stage)]
        final_video_prompt_missing_dependency_keys = list(missing_upstream)
        force_requested_stage_execution = bool(final_video_prompt_payload_gate_accepted)
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["continuation_mode"] = "manual_final_video_prompt_isolated"
        diagnostics["final_video_prompt_incoming_payload_summary"] = incoming_payload_summary
        diagnostics["final_video_prompt_dependency_gate_mode"] = "payload_validity"
        diagnostics["final_video_prompt_dependency_status_by_stage"] = status_by_stage
        diagnostics["final_video_prompt_dependency_payload_ok_by_stage"] = payload_ok_by_stage
        diagnostics["final_video_prompt_dependency_gate_false_positive_prevented"] = bool(false_positive_prevented)
        diagnostics["final_video_prompt_dependency_gate_recomputed_after_payload_overrides"] = True
        diagnostics["final_video_prompt_dependency_audio_map_payload_valid"] = bool(audio_map_ok_for_final_video_prompt)
        diagnostics["final_video_prompt_dependency_audio_map_segment_count"] = len(audio_segments)
        diagnostics["final_video_prompt_dependency_audio_map_coverage_ok"] = bool(
            _safe_dict(audio_map.get("diagnostics")).get("coverage_ok")
        )
        diagnostics["final_video_prompt_dependency_scene_prompts_payload_valid"] = bool(scene_prompts_ok_for_final_video_prompt)
        diagnostics["final_video_prompt_dependency_scene_prompts_segment_count"] = len(scene_prompts_segments)
        diagnostics["final_video_prompt_missing_dependency_keys"] = final_video_prompt_missing_dependency_keys
        diagnostics["final_video_prompt_dependency_gate_accepted"] = bool(final_video_prompt_payload_gate_accepted)
        diagnostics["final_video_prompt_force_current_stage_execution"] = bool(force_requested_stage_execution)
        diagnostics["upstream_package_complete"] = not bool(missing_upstream)
        diagnostics["reused_upstream_stages"] = reusable_upstream
        diagnostics["regenerated_stages"] = [stage_id]
        if missing_upstream:
            diagnostics["final_video_prompt_missing_upstream"] = missing_upstream
            diagnostics["final_video_prompt_missing_upstream_reasons"] = missing_reasons
            diagnostics["final_video_prompt_missing_upstream_payload_summary"] = incoming_payload_summary
            if not audio_map_ok_for_final_video_prompt:
                requested_stage_not_executed_reason = "audio_map_payload_invalid_for_final_video_prompt"
            elif not scene_prompts_ok_for_final_video_prompt:
                requested_stage_not_executed_reason = "scene_prompts_payload_invalid_for_final_video_prompt"
            else:
                requested_stage_not_executed_reason = "missing_dependencies"
            diagnostics["requested_stage_not_executed_reason"] = requested_stage_not_executed_reason
            pkg["diagnostics"] = diagnostics
            error_code = f"final_video_prompt_incomplete_dependencies:{','.join(diagnostics['final_video_prompt_missing_upstream_reasons'])}"
            _set_stage_status(pkg, stage_id, "error", error=error_code)
            _append_diag_event(
                pkg,
                f"manual final_video_prompt dependency gate failed summary={incoming_payload_summary}",
                stage_id=stage_id,
            )
            _append_diag_event(pkg, error_code, stage_id=stage_id)
            return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg
        pkg["diagnostics"] = diagnostics
        if force_requested_stage_execution:
            pkg = invalidate_downstream_stages(pkg, stage_id, reason=f"manual_rerun:{stage_id}")
        pkg = run_stage(stage_id, pkg, payload)
        if str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get(stage_id)).get("status") or "").strip().lower() == "done":
            pkg = _restore_payload_valid_upstream_statuses_for_stage(pkg, stage_id, deps, payload_ok_by_stage)
        executed_stage_ids.append(stage_id)
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["final_video_prompt_executed"] = "final_video_prompt" in executed_stage_ids
        pkg["diagnostics"] = diagnostics
        return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg
    if stage_id == "finalize":
        # Guardrail: pressing FINAL must not retrigger upstream creative Gemini stages.
        # Finalize can run only from already prepared normalized outputs.
        deps = STAGE_DEPENDENCIES.get(stage_id, [])
        incoming_payload_summary = build_stage_payload_health_summary(pkg)
        missing_reasons, payload_ok_by_stage, status_by_stage, false_positive_prevented = _collect_finalize_dependency_gate_state(
            pkg, deps
        )
        finalize_payload_gate_accepted = bool(all(bool(payload_ok_by_stage.get(dep_stage)) for dep_stage in deps))
        reusable_upstream = [dep_stage for dep_stage in deps if payload_ok_by_stage.get(dep_stage)]
        missing_upstream = [dep_stage for dep_stage in deps if not payload_ok_by_stage.get(dep_stage)]
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["continuation_mode"] = "manual_finalize_assembler_only"
        diagnostics["finalize_incoming_payload_summary"] = incoming_payload_summary
        diagnostics["finalize_dependency_gate_mode"] = "payload_validity"
        diagnostics["finalize_dependency_status_by_stage"] = status_by_stage
        diagnostics["finalize_dependency_payload_ok_by_stage"] = payload_ok_by_stage
        diagnostics["finalize_dependency_gate_false_positive_prevented"] = bool(false_positive_prevented)
        diagnostics["finalize_dependency_gate_recomputed_after_payload_overrides"] = True
        diagnostics["finalize_dependency_gate_accepted"] = bool(finalize_payload_gate_accepted)
        diagnostics["finalize_missing_dependency_keys"] = list(missing_upstream)
        diagnostics["finalize_force_current_stage_execution"] = bool(finalize_payload_gate_accepted)
        diagnostics["upstream_package_complete"] = not bool(missing_upstream)
        diagnostics["reused_upstream_stages"] = reusable_upstream
        diagnostics["regenerated_stages"] = [stage_id]
        diagnostics["finalize_missing_upstream_reasons"] = missing_reasons
        if not finalize_payload_gate_accepted:
            diagnostics["finalize_missing_upstream"] = missing_upstream
            diagnostics["finalize_missing_upstream_payload_summary"] = incoming_payload_summary
            if not bool(payload_ok_by_stage.get("audio_map")):
                requested_stage_not_executed_reason = "audio_map_payload_invalid_for_finalize"
            elif not bool(payload_ok_by_stage.get("final_video_prompt")):
                requested_stage_not_executed_reason = "final_video_prompt_payload_invalid_for_finalize"
            else:
                requested_stage_not_executed_reason = "missing_dependencies"
            diagnostics["requested_stage_not_executed_reason"] = requested_stage_not_executed_reason
            pkg["diagnostics"] = diagnostics
            error_code = f"finalize_incomplete_dependencies:{','.join(missing_reasons)}"
            _set_stage_status(pkg, stage_id, "error", error=error_code)
            _append_diag_event(
                pkg,
                f"manual finalize dependency gate failed summary={incoming_payload_summary}",
                stage_id=stage_id,
            )
            _append_diag_event(pkg, error_code, stage_id=stage_id)
            return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg
        pkg["diagnostics"] = diagnostics
        if finalize_payload_gate_accepted:
            pkg = invalidate_downstream_stages(pkg, stage_id, reason=f"manual_rerun:{stage_id}")
        pkg = run_stage(stage_id, pkg, payload)
        if str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get(stage_id)).get("status") or "").strip().lower() == "done":
            pkg = _restore_payload_valid_upstream_statuses_for_stage(pkg, stage_id, deps, payload_ok_by_stage)
        executed_stage_ids.append(stage_id)
        return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg
    dep_sequence = resolve_stage_sequence([stage_id], include_dependencies=True)[:-1]
    scene_plan_payload_ok_by_stage: dict[str, bool] = {}
    scene_plan_payload_gate_accepted = False
    scene_prompts_payload_ok_by_stage: dict[str, bool] = {}
    scene_prompts_payload_gate_accepted = False
    force_requested_stage_execution = False
    if stage_id == "scene_plan":
        deps = list(dep_sequence)
        (
            scene_plan_payload_ok_by_stage,
            scene_plan_status_by_stage,
            scene_plan_false_positive_prevented,
        ) = _collect_scene_plan_dependency_gate_state(pkg, deps)
        scene_plan_payload_gate_accepted = _can_run_scene_plan_from_existing_payload(pkg, deps)
        if scene_plan_payload_gate_accepted:
            reusable_upstream = list(dep_sequence)
            missing_upstream = []
            continuation_mode = "reuse_existing_package"
        else:
            reusable_upstream = [
                dep_stage
                for dep_stage in dep_sequence
                if scene_plan_payload_ok_by_stage.get(dep_stage) is True
            ]
            missing_upstream = [
                dep_stage
                for dep_stage in dep_sequence
                if dep_stage not in reusable_upstream
            ]
            continuation_mode = "reuse_existing_package" if not missing_upstream else "recompute_missing_upstream"
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["scene_plan_dependency_gate_mode"] = "payload_validity"
        diagnostics["scene_plan_dependency_status_by_stage"] = scene_plan_status_by_stage
        diagnostics["scene_plan_dependency_payload_ok_by_stage"] = scene_plan_payload_ok_by_stage
        diagnostics["scene_plan_dependency_gate_false_positive_prevented"] = bool(scene_plan_false_positive_prevented)
        diagnostics["scene_plan_dependency_gate_accepted"] = bool(scene_plan_payload_gate_accepted)
        audio_map = _safe_dict(pkg.get("audio_map"))
        audio_segments = _safe_list(audio_map.get("segments"))
        scene_plan_audio_map_payload_valid = _has_valid_audio_map_payload(pkg)
        diagnostics["scene_plan_audio_map_payload_valid"] = bool(scene_plan_audio_map_payload_valid)
        diagnostics["scene_plan_audio_map_segment_count"] = len(audio_segments)
        diagnostics["scene_plan_audio_map_stage_error_ignored_due_to_valid_payload"] = bool(
            scene_plan_audio_map_payload_valid and _scene_plan_audio_map_stage_error_present(pkg)
        )
        diagnostics["scene_plan_reused_upstream_statuses_restored"] = False
        diagnostics["scene_plan_upstream_statuses_restored_before_run"] = False
        diagnostics["scene_plan_upstream_statuses_restored_before_run_stages"] = []
        diagnostics["scene_plan_missing_upstream_from_payload_gate"] = list(missing_upstream)
        diagnostics["scene_plan_reusable_upstream_from_payload_gate"] = list(reusable_upstream)
        pkg["diagnostics"] = diagnostics
    elif stage_id == "scene_prompts":
        deps = list(dep_sequence)
        (
            scene_prompts_payload_ok_by_stage,
            scene_prompts_status_by_stage,
            scene_prompts_false_positive_prevented,
        ) = _collect_scene_prompts_dependency_gate_state(pkg, deps)
        scene_prompts_payload_gate_accepted = _can_run_scene_prompts_from_existing_payload(pkg, deps)
        scene_plan_rows = _safe_list(
            _safe_dict(pkg.get("scene_plan")).get("storyboard")
            or _safe_dict(pkg.get("scene_plan")).get("scenes")
        )
        audio_map = _safe_dict(pkg.get("audio_map"))
        audio_segments = _safe_list(audio_map.get("segments"))
        audio_map_ok_for_prompts = _has_valid_audio_map_payload_for_scene_prompts(pkg)
        scene_plan_ok_for_prompts = _has_valid_scene_plan_payload_for_scene_prompts(pkg)
        audio_map_block_reason = "" if audio_map_ok_for_prompts else "audio_map_payload_invalid_for_scene_prompts"
        scene_plan_block_reason = "" if scene_plan_ok_for_prompts else "scene_plan_payload_invalid_for_scene_prompts"
        scene_prompts_payload_ok_by_stage["audio_map"] = bool(audio_map_ok_for_prompts)
        scene_prompts_payload_ok_by_stage["scene_plan"] = bool(scene_plan_ok_for_prompts)
        scene_prompts_payload_gate_accepted = bool(
            deps and all(bool(scene_prompts_payload_ok_by_stage.get(dep_stage)) for dep_stage in deps)
        )
        scene_prompts_missing_dependency_keys = [
            dep_stage for dep_stage in deps if not bool(scene_prompts_payload_ok_by_stage.get(dep_stage))
        ]
        if scene_prompts_payload_gate_accepted:
            reusable_upstream = []
            missing_upstream = []
            continuation_mode = "downstream_only_scene_prompts"
            dep_sequence = []
            force_requested_stage_execution = True
        else:
            reusable_upstream = [dep_stage for dep_stage in dep_sequence if _can_reuse_stage_output(pkg, dep_stage)]
            missing_upstream = [dep_stage for dep_stage in dep_sequence if dep_stage not in reusable_upstream]
            continuation_mode = "blocked_scene_plan_invalid"
            dep_sequence = []
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["scene_prompts_dependency_gate_mode"] = "payload_validity"
        diagnostics["scene_prompts_dependency_status_by_stage"] = scene_prompts_status_by_stage
        diagnostics["scene_prompts_dependency_payload_ok_by_stage"] = scene_prompts_payload_ok_by_stage
        diagnostics["scene_prompts_dependency_gate_recomputed_after_payload_overrides"] = True
        diagnostics["scene_prompts_dependency_gate_false_positive_prevented"] = bool(scene_prompts_false_positive_prevented)
        diagnostics["scene_prompts_dependency_gate_accepted"] = bool(scene_prompts_payload_gate_accepted)
        diagnostics["scene_prompts_requested"] = True
        diagnostics["scene_prompts_force_current_stage_execution"] = bool(force_requested_stage_execution)
        diagnostics["scene_prompts_dependency_audio_map_payload_valid"] = bool(audio_map_ok_for_prompts)
        diagnostics["scene_prompts_dependency_audio_map_segment_count"] = len(audio_segments)
        diagnostics["scene_prompts_dependency_audio_map_coverage_ok"] = bool(
            _safe_dict(audio_map.get("diagnostics")).get("coverage_ok")
        )
        diagnostics["scene_prompts_dependency_scene_plan_payload_valid"] = bool(scene_plan_ok_for_prompts)
        diagnostics["scene_prompts_dependency_scene_plan_row_count"] = len(scene_plan_rows)
        diagnostics["scene_prompts_dependency_gate_accepted"] = bool(scene_prompts_payload_gate_accepted)
        diagnostics["scene_prompts_missing_dependency_keys"] = list(scene_prompts_missing_dependency_keys)
        diagnostics["scene_prompts_skipped_reason"] = "" if scene_prompts_payload_gate_accepted else str(
            audio_map_block_reason or scene_plan_block_reason or "missing_dependencies"
        )
        diagnostics["scene_prompts_reused_upstream_statuses_restored"] = False
        diagnostics["scene_prompts_upstream_statuses_restored_before_run"] = False
        diagnostics["scene_prompts_upstream_statuses_restored_before_run_stages"] = []
        diagnostics["scene_prompts_downstream_only"] = True
        diagnostics["scene_prompts_used_existing_scene_plan"] = bool(scene_prompts_payload_gate_accepted)
        diagnostics["scene_prompts_blocked_auto_scene_plan_rebuild"] = True
        diagnostics["scene_prompts_downstream_only_invalidation"] = bool(scene_prompts_payload_gate_accepted)
        diagnostics["scene_prompts_scene_plan_gate_reason"] = str(scene_plan_block_reason or "")
        pkg["diagnostics"] = diagnostics
        if not scene_prompts_payload_gate_accepted:
            diagnostics = _safe_dict(pkg.get("diagnostics"))
            diagnostics["scene_prompts_error_code"] = "PROMPTS_BLOCKED_SCENE_PLAN_INVALID"
            diagnostics["scene_prompts_error_hint"] = "Run SCENES manually first"
            diagnostics["requested_stage_not_executed"] = True
            if not audio_map_ok_for_prompts:
                requested_stage_not_executed_reason = "audio_map_payload_invalid_for_scene_prompts"
            elif not scene_plan_ok_for_prompts:
                requested_stage_not_executed_reason = "scene_plan_payload_invalid_for_scene_prompts"
            else:
                requested_stage_not_executed_reason = "missing_dependencies"
            diagnostics["requested_stage_not_executed_reason"] = requested_stage_not_executed_reason
            pkg["diagnostics"] = diagnostics
            _set_stage_status(pkg, stage_id, "error", error="PROMPTS_BLOCKED_SCENE_PLAN_INVALID")
            _append_diag_event(pkg, "PROMPTS_BLOCKED_SCENE_PLAN_INVALID", stage_id=stage_id)
            _append_diag_event(pkg, "Run SCENES manually first", stage_id=stage_id)
            return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg
    else:
        reusable_upstream = [dep_stage for dep_stage in dep_sequence if _can_reuse_stage_output(pkg, dep_stage)]
        missing_upstream = [dep_stage for dep_stage in dep_sequence if dep_stage not in reusable_upstream]
        continuation_mode = "reuse_existing_package" if not missing_upstream else "recompute_missing_upstream"
    if missing_upstream:
        first_missing_idx = dep_sequence.index(missing_upstream[0])
        dep_sequence = dep_sequence[first_missing_idx:]
    else:
        dep_sequence = []
    if stage_id == "scene_plan" and scene_plan_payload_gate_accepted:
        dep_sequence = []
    preserve_audio_map_for_story_core = stage_id == "story_core" and _is_usable_audio_map(_safe_dict(pkg.get("audio_map")))
    if preserve_audio_map_for_story_core:
        # Manual CORE rerun must preserve upstream audio_map and skip audio_map dependency rebuild.
        dep_sequence = [dep_stage for dep_stage in dep_sequence if dep_stage != "audio_map"]
    preserved_audio_map_snapshot = deepcopy(_safe_dict(pkg.get("audio_map"))) if preserve_audio_map_for_story_core else {}
    guarded_fingerprint_before = (
        _audio_map_story_core_guard_fingerprint(preserved_audio_map_snapshot) if preserve_audio_map_for_story_core else ""
    )
    upstream_guard_snapshots: dict[str, dict[str, Any]] = {
        dep_stage: deepcopy(_safe_dict(pkg.get(_stage_output_field(dep_stage))))
        for dep_stage in reusable_upstream
    }
    diagnostics = _safe_dict(pkg.get("diagnostics"))
    diagnostics["continuation_mode"] = continuation_mode
    diagnostics["upstream_package_complete"] = not bool(missing_upstream)
    diagnostics["reused_upstream_stages"] = reusable_upstream
    diagnostics["regenerated_stages"] = list(dep_sequence) + [stage_id]
    if missing_upstream:
        diagnostics["recompute_missing_upstream_stages"] = missing_upstream
    if stage_id == "scene_prompts":
        diagnostics["scene_prompts_final_dep_sequence_before_run"] = list(dep_sequence)
        diagnostics["scene_prompts_expected_execution_stage"] = "scene_prompts"
        if force_requested_stage_execution:
            diagnostics["scene_prompts_current_stage_execution_path"] = "separate_stage_runner"
    pkg["diagnostics"] = diagnostics
    for dep_stage in dep_sequence:
        pkg = run_stage(dep_stage, pkg, payload)
        executed_stage_ids.append(dep_stage)
        if str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get(dep_stage)).get("status") or "") == "error":
            return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg
    scene_plan_preserved_upstream_statuses: dict[str, dict[str, Any]] = {}
    if stage_id == "scene_plan" and scene_plan_payload_gate_accepted:
        statuses_before_invalidate = _safe_dict(pkg.get("stage_statuses"))
        for upstream_stage in ("input_package", "audio_map", "story_core", "role_plan"):
            if not bool(scene_plan_payload_ok_by_stage.get(upstream_stage)):
                continue
            scene_plan_preserved_upstream_statuses[upstream_stage] = deepcopy(_safe_dict(statuses_before_invalidate.get(upstream_stage)))
    scene_prompts_preserved_upstream_statuses: dict[str, dict[str, Any]] = {}
    if stage_id == "scene_prompts" and scene_prompts_payload_gate_accepted:
        statuses_before_invalidate = _safe_dict(pkg.get("stage_statuses"))
        for upstream_stage in ("input_package", "audio_map", "story_core", "role_plan", "scene_plan"):
            if not bool(scene_prompts_payload_ok_by_stage.get(upstream_stage)):
                continue
            scene_prompts_preserved_upstream_statuses[upstream_stage] = deepcopy(_safe_dict(statuses_before_invalidate.get(upstream_stage)))
    pkg = invalidate_downstream_stages(pkg, stage_id, reason=f"manual_rerun:{stage_id}")
    if stage_id == "scene_plan" and scene_plan_payload_gate_accepted:
        statuses = _safe_dict(pkg.get("stage_statuses"))
        restored_before_run: list[str] = []
        for upstream_stage, preserved_state in scene_plan_preserved_upstream_statuses.items():
            stage_state = deepcopy(_safe_dict(preserved_state))
            stage_state["status"] = "done"
            stage_state["error"] = ""
            stage_state["updated_at"] = _utc_iso()
            for key in (
                "invalidated",
                "invalid",
                "dirty",
                "stale",
                "staleReason",
                "stale_reason",
                "reason",
                "statusReason",
                "invalidateReason",
                "invalidatedReason",
            ):
                stage_state.pop(key, None)
            statuses[upstream_stage] = stage_state
            restored_before_run.append(upstream_stage)
        pkg["stage_statuses"] = statuses
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["scene_plan_upstream_statuses_restored_before_run"] = bool(restored_before_run)
        diagnostics["scene_plan_upstream_statuses_restored_before_run_stages"] = restored_before_run
        diagnostics["scene_plan_reused_upstream_statuses_restored"] = bool(restored_before_run)
        pkg["diagnostics"] = diagnostics
    if stage_id == "scene_prompts" and scene_prompts_payload_gate_accepted:
        statuses = _safe_dict(pkg.get("stage_statuses"))
        restored_before_run: list[str] = []
        for upstream_stage, preserved_state in scene_prompts_preserved_upstream_statuses.items():
            stage_state = deepcopy(_safe_dict(preserved_state))
            stage_state["status"] = "done"
            stage_state["error"] = ""
            stage_state["updated_at"] = _utc_iso()
            for key in (
                "invalidated",
                "invalid",
                "dirty",
                "stale",
                "staleReason",
                "stale_reason",
                "reason",
                "statusReason",
                "invalidateReason",
                "invalidatedReason",
            ):
                stage_state.pop(key, None)
            statuses[upstream_stage] = stage_state
            restored_before_run.append(upstream_stage)
        pkg["stage_statuses"] = statuses
        restore_deps = resolve_stage_sequence([stage_id], include_dependencies=True)[:-1]
        pkg = _restore_payload_valid_upstream_statuses_for_stage(pkg, stage_id, restore_deps, scene_prompts_payload_ok_by_stage)
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["scene_prompts_upstream_statuses_restored_before_run"] = bool(restored_before_run)
        diagnostics["scene_prompts_upstream_statuses_restored_before_run_stages"] = restored_before_run
        diagnostics["scene_prompts_downstream_only_invalidation"] = True
        pkg["diagnostics"] = diagnostics
    if stage_id not in dep_sequence:
        pkg = run_stage(stage_id, pkg, payload)
        executed_stage_ids.append(stage_id)
    if stage_id == "scene_plan" and str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get(stage_id)).get("status") or "").strip().lower() == "done":
        statuses = _safe_dict(pkg.get("stage_statuses"))
        restored_stages: list[str] = []
        for upstream_stage in ("input_package", "audio_map", "story_core", "role_plan"):
            if not bool(scene_plan_payload_ok_by_stage.get(upstream_stage)):
                continue
            stage_state = _safe_dict(statuses.get(upstream_stage))
            stage_state["status"] = "done"
            stage_state["error"] = ""
            stage_state["updated_at"] = _utc_iso()
            statuses[upstream_stage] = stage_state
            restored_stages.append(upstream_stage)
        pkg["stage_statuses"] = statuses
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["scene_plan_reused_upstream_statuses_restored"] = bool(restored_stages)
        diagnostics["scene_plan_reused_upstream_statuses_restored_stages"] = restored_stages
        pkg["diagnostics"] = diagnostics
    if stage_id == "scene_prompts" and str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get(stage_id)).get("status") or "").strip().lower() == "done":
        deps = resolve_stage_sequence([stage_id], include_dependencies=True)[:-1]
        pkg = _restore_payload_valid_upstream_statuses_for_stage(pkg, stage_id, deps, scene_prompts_payload_ok_by_stage)
    if stage_id == "scene_prompts":
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        diagnostics["scene_prompts_executed"] = "scene_prompts" in executed_stage_ids
        pkg["diagnostics"] = diagnostics
    if reusable_upstream:
        statuses = _safe_dict(pkg.get("stage_statuses"))
        guard_restored: list[str] = []
        for upstream_stage in reusable_upstream:
            output_key = _stage_output_field(upstream_stage)
            before_snapshot = upstream_guard_snapshots.get(upstream_stage, {})
            after_snapshot = _safe_dict(pkg.get(output_key))
            if before_snapshot and after_snapshot != before_snapshot:
                pkg[output_key] = deepcopy(before_snapshot)
                stage_state = _safe_dict(statuses.get(upstream_stage))
                stage_state["status"] = "done"
                stage_state["error"] = ""
                stage_state["updated_at"] = _utc_iso()
                statuses[upstream_stage] = stage_state
                guard_restored.append(upstream_stage)
        pkg["stage_statuses"] = statuses
        if guard_restored:
            diagnostics = _safe_dict(pkg.get("diagnostics"))
            warnings = _safe_list(diagnostics.get("warnings"))
            warnings.append(
                {
                    "stage_id": stage_id,
                    "message": "manual_rerun_upstream_mutation_guard_triggered",
                    "restored_stages": guard_restored,
                }
            )
            diagnostics["warnings"] = warnings[-80:]
            diagnostics["manual_rerun_upstream_mutation_guard_triggered"] = True
            diagnostics["manual_rerun_upstream_restored_stages"] = guard_restored
            pkg["diagnostics"] = diagnostics
    if preserve_audio_map_for_story_core:
        guarded_audio_map_after = _safe_dict(pkg.get("audio_map"))
        guarded_fingerprint_after = _audio_map_story_core_guard_fingerprint(guarded_audio_map_after)
        audio_map_was_mutated = guarded_audio_map_after != preserved_audio_map_snapshot
        if audio_map_was_mutated:
            diagnostics = _safe_dict(pkg.get("diagnostics"))
            warnings = _safe_list(diagnostics.get("warnings"))
            warnings.append(
                {
                    "stage_id": "story_core",
                    "message": "story_core_audio_map_mutation_guard_triggered",
                }
            )
            diagnostics["warnings"] = warnings[-80:]
            diagnostics["story_core_audio_map_mutation_guard_triggered"] = True
            diagnostics["story_core_audio_map_mutation_guard_fingerprint_changed"] = bool(
                guarded_fingerprint_before and guarded_fingerprint_after != guarded_fingerprint_before
            )
            pkg["diagnostics"] = diagnostics
            _append_diag_event(pkg, "story_core audio_map mutation guard restored upstream audio_map", stage_id="story_core")
        # Always restore preserved upstream audio_map snapshot for manual story_core rerun.
        pkg["audio_map"] = preserved_audio_map_snapshot
    return (pkg, executed_stage_ids) if return_executed_stage_ids else pkg


def run_pipeline(stage_ids: list[str], package: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    pkg = deepcopy(_safe_dict(package)) if package else create_storyboard_package(payload)
    for stage_id in stage_ids:
        pkg = run_stage(stage_id, pkg, payload)
        if str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get(stage_id)).get("status") or "") == "error":
            break
    return pkg


def _resolve_stage_with_dependencies(stage_id: str, ordered: list[str], visited: set[str]) -> None:
    if stage_id in visited:
        return
    visited.add(stage_id)
    for dep in STAGE_DEPENDENCIES.get(stage_id, []):
        _resolve_stage_with_dependencies(dep, ordered, visited)
    if stage_id not in ordered:
        ordered.append(stage_id)


def resolve_stage_sequence(
    requested_stage_ids: list[str] | None = None,
    *,
    auto_mode: bool = False,
    include_dependencies: bool = False,
) -> list[str]:
    if auto_mode:
        return list(STAGE_IDS)
    stage_ids = [stage_id for stage_id in (requested_stage_ids or []) if stage_id in STAGE_IDS]
    if not stage_ids:
        stage_ids = ["story_core"]
    if not include_dependencies:
        return stage_ids
    ordered: list[str] = []
    visited: set[str] = set()
    for stage_id in stage_ids:
        _resolve_stage_with_dependencies(stage_id, ordered, visited)
    return ordered
