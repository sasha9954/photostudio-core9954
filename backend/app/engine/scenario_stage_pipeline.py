from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from app.engine.gemini_rest import post_generate_content

logger = logging.getLogger(__name__)

STAGE_IDS = (
    "input_package",
    "story_core",
    "audio_map",
    "role_plan",
    "scene_plan",
    "scene_prompts",
    "finalize",
)

STAGE_DEPENDENCIES: dict[str, list[str]] = {
    "input_package": [],
    "story_core": ["input_package"],
    "audio_map": ["story_core"],
    "role_plan": ["story_core"],
    "scene_plan": ["audio_map", "role_plan"],
    "scene_prompts": ["scene_plan"],
    "finalize": ["scene_prompts"],
}

DOWNSTREAM_BY_STAGE: dict[str, list[str]] = {
    stage_id: [candidate for candidate, deps in STAGE_DEPENDENCIES.items() if stage_id in deps]
    for stage_id in STAGE_IDS
}


def _utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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
    source_note = str(input_pkg.get("note") or input_pkg.get("text") or "").strip()
    source_note = source_note[:800]
    return {
        "story_summary": source_note or "Music-driven visual story with continuity locks.",
        "opening_anchor": "Open with a stable hero/world establishing frame.",
        "ending_callback_rule": "Last beat should echo opening anchor with emotional change.",
        "global_arc": "setup→rise→turn→release→afterimage",
        "identity_lock": {"rule": "Keep hero identity stable across all scenes."},
        "world_lock": {"rule": "Keep world/location logic coherent without random jumps."},
        "style_lock": {"rule": "Keep one cinematic style language across the whole track."},
    }


def _build_story_core_prompt(input_pkg: dict[str, Any], refs_inventory: dict[str, Any]) -> str:
    return (
        "You are building STORY CORE for a stage-based music-video pipeline. Return strict JSON only.\n"
        "Do NOT output scenes/prompts/full storyboard.\n"
        "Required keys: story_summary, opening_anchor, ending_callback_rule, global_arc, identity_lock, world_lock, style_lock.\n"
        "Keep concise but production-usable.\n\n"
        f"INPUT:\n{json.dumps(input_pkg, ensure_ascii=False)[:3000]}\n\n"
        f"REFS_INVENTORY:\n{json.dumps(refs_inventory, ensure_ascii=False)[:3000]}\n"
    )


def create_storyboard_package(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    req = _safe_dict(payload)
    metadata = _safe_dict(req.get("metadata"))
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
        "input": {
            "text": str(req.get("text") or "").strip(),
            "note": str(req.get("note") or req.get("storyText") or "").strip(),
            "source": _safe_dict(req.get("source")),
            "audio_url": str(req.get("audioUrl") or "").strip(),
            "audio_duration_sec": float(req.get("audioDurationSec") or 0.0),
            "content_type": str(_safe_dict(req.get("director_controls")).get("contentType") or "music_video"),
        },
        "refs_inventory": _safe_dict(req.get("context_refs")),
        "assigned_roles": _safe_dict(req.get("roleTypeByRole")),
        "story_core": {},
        "audio_map": {},
        "role_plan": {},
        "scene_plan": {"scenes": []},
        "scene_prompts": {"scenes": []},
        "final_storyboard": {"scenes": []},
        "diagnostics": {
            "warnings": [],
            "events": [],
            "errors": [],
            "stale_reason": "",
            "last_action": "",
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


def pick_best_view_for_scene(entity: dict[str, Any] | None, scene_context: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate = _safe_dict(entity)
    views = _safe_list(candidate.get("views"))
    if not views:
        return {}
    # Hook for future ranking by scene_context.
    return views[0] if isinstance(views[0], dict) else {"id": str(views[0])}


def mark_stale_downstream(package: dict[str, Any], from_stage_id: str, reason: str = "") -> dict[str, Any]:
    pkg = deepcopy(_safe_dict(package))
    queue = list(DOWNSTREAM_BY_STAGE.get(from_stage_id, []))
    visited: set[str] = set()
    statuses = _safe_dict(pkg.get("stage_statuses"))
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
    pkg["updated_at"] = _utc_iso()
    return pkg


def _set_stage_status(package: dict[str, Any], stage_id: str, status: str, *, error: str = "") -> None:
    statuses = _safe_dict(package.get("stage_statuses"))
    stage_state = _safe_dict(statuses.get(stage_id))
    stage_state["status"] = status
    stage_state["updated_at"] = _utc_iso()
    stage_state["error"] = str(error or "")
    if status in {"done", "error"}:
        stage_state["run_count"] = int(stage_state.get("run_count") or 0) + 1
    statuses[stage_id] = stage_state
    package["stage_statuses"] = statuses


def _append_diag_event(package: dict[str, Any], message: str, *, stage_id: str = "") -> None:
    diagnostics = _safe_dict(package.get("diagnostics"))
    events = _safe_list(diagnostics.get("events"))
    events.append({"at": _utc_iso(), "stage_id": stage_id, "message": message})
    diagnostics["events"] = events[-80:]
    package["diagnostics"] = diagnostics


def _run_story_core_stage(package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    fallback = _default_story_core(input_pkg)
    prompt = _build_story_core_prompt(input_pkg, refs_inventory)
    try:
        response = post_generate_content(
            {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            model="gemini-2.5-pro",
            timeout=90,
        )
        parsed = _extract_json_obj(_extract_gemini_text(response))
        story_core = {
            "story_summary": str(parsed.get("story_summary") or fallback["story_summary"]).strip(),
            "opening_anchor": str(parsed.get("opening_anchor") or fallback["opening_anchor"]).strip(),
            "ending_callback_rule": str(parsed.get("ending_callback_rule") or fallback["ending_callback_rule"]).strip(),
            "global_arc": parsed.get("global_arc") or fallback["global_arc"],
            "identity_lock": _safe_dict(parsed.get("identity_lock")) or fallback["identity_lock"],
            "world_lock": _safe_dict(parsed.get("world_lock")) or fallback["world_lock"],
            "style_lock": _safe_dict(parsed.get("style_lock")) or fallback["style_lock"],
        }
        package["story_core"] = story_core
        _append_diag_event(package, "story_core generated", stage_id="story_core")
        return package
    except Exception as exc:  # noqa: BLE001
        logger.exception("[scenario_stage_pipeline] story_core failed")
        package["story_core"] = fallback
        _append_diag_event(package, f"story_core fallback used: {exc}", stage_id="story_core")
        return package


def _run_input_package_stage(package: dict[str, Any]) -> dict[str, Any]:
    package["input"] = _safe_dict(package.get("input"))
    package["refs_inventory"] = _safe_dict(package.get("refs_inventory"))
    _append_diag_event(package, "input_package normalized", stage_id="input_package")
    return package


def run_stage(stage_id: str, package: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if stage_id not in STAGE_IDS:
        raise ValueError(f"unknown_stage:{stage_id}")
    pkg = deepcopy(_safe_dict(package)) if package else create_storyboard_package(payload)
    _set_stage_status(pkg, stage_id, "running")
    pkg["updated_at"] = _utc_iso()

    deps = STAGE_DEPENDENCIES.get(stage_id, [])
    statuses = _safe_dict(pkg.get("stage_statuses"))
    missing = [dep for dep in deps if str(_safe_dict(statuses.get(dep)).get("status") or "") not in {"done"}]
    if missing:
        _set_stage_status(pkg, stage_id, "error", error=f"missing_dependencies:{','.join(missing)}")
        _safe_dict(pkg.get("diagnostics")).setdefault("errors", []).append(f"{stage_id}: missing dependencies {missing}")
        return pkg

    try:
        if stage_id == "input_package":
            pkg = _run_input_package_stage(pkg)
        elif stage_id == "story_core":
            pkg = _run_story_core_stage(pkg)
        elif stage_id == "audio_map":
            pkg["audio_map"] = pkg.get("audio_map") or {"status": "placeholder"}
        elif stage_id == "role_plan":
            pkg["role_plan"] = pkg.get("role_plan") or {"status": "placeholder"}
        elif stage_id == "scene_plan":
            pkg["scene_plan"] = pkg.get("scene_plan") or {"scenes": []}
        elif stage_id == "scene_prompts":
            pkg["scene_prompts"] = pkg.get("scene_prompts") or {"scenes": []}
        elif stage_id == "finalize":
            pkg["final_storyboard"] = pkg.get("final_storyboard") or {"scenes": []}
        _set_stage_status(pkg, stage_id, "done")
    except Exception as exc:  # noqa: BLE001
        _set_stage_status(pkg, stage_id, "error", error=str(exc))
        diagnostics = _safe_dict(pkg.get("diagnostics"))
        errors = _safe_list(diagnostics.get("errors"))
        errors.append(f"{stage_id}: {exc}")
        diagnostics["errors"] = errors[-80:]
        pkg["diagnostics"] = diagnostics

    pkg["updated_at"] = _utc_iso()
    return pkg


def run_pipeline(stage_ids: list[str], package: dict[str, Any], payload: dict[str, Any] | None = None) -> dict[str, Any]:
    pkg = deepcopy(_safe_dict(package)) if package else create_storyboard_package(payload)
    for stage_id in stage_ids:
        pkg = run_stage(stage_id, pkg, payload)
        if str(_safe_dict(_safe_dict(pkg.get("stage_statuses")).get(stage_id)).get("status") or "") == "error":
            break
    return pkg


def resolve_stage_sequence(requested_stage_ids: list[str] | None = None, *, auto_mode: bool = False) -> list[str]:
    if auto_mode:
        return list(STAGE_IDS)
    stage_ids = [stage_id for stage_id in (requested_stage_ids or []) if stage_id in STAGE_IDS]
    return stage_ids or ["input_package", "story_core"]
