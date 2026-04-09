from __future__ import annotations

import json
from typing import Any

from app.engine.gemini_rest import post_generate_content

ROLE_PLAN_PROMPT_VERSION = "role_plan_v1"

_BASE_ROLES = ["character_1", "character_2", "character_3", "location", "props", "style"]
ALLOWED_SCENE_ROLES = set(_BASE_ROLES)
_CHARACTER_ROLES = ["character_1", "character_2", "character_3"]
_WORLD_ROLES = ["location", "style"]
_PROP_ROLES = ["props"]


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _round3(value: Any) -> float:
    try:
        return round(float(value), 3)
    except Exception:
        return 0.0


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


def _role_from_ref_key(key: str) -> str:
    clean = str(key or "").strip().lower()
    if clean.startswith("ref_"):
        clean = clean[4:]
    return clean


def _collect_refs_present_by_role(input_pkg: dict[str, Any], refs_inventory: dict[str, Any]) -> dict[str, list[str]]:
    summary = _safe_dict(input_pkg.get("connected_context_summary"))
    from_summary = _safe_dict(summary.get("connectedRefsPresentByRole"))
    if not from_summary:
        from_summary = _safe_dict(summary.get("refsPresentByRole"))
    out: dict[str, list[str]] = {}

    for role, raw in from_summary.items():
        role_name = str(role or "").strip()
        if not role_name or role_name not in ALLOWED_SCENE_ROLES:
            continue
        if isinstance(raw, list):
            values = [str(item).strip() for item in raw if str(item).strip()]
            if values:
                out[role_name] = values
        elif bool(raw):
            out[role_name] = ["present"]

    for key, value in refs_inventory.items():
        role_name = _role_from_ref_key(key)
        if role_name not in ALLOWED_SCENE_ROLES:
            continue
        row = _safe_dict(value)
        refs = [str(item).strip() for item in _safe_list(row.get("refs")) if str(item).strip()]
        if not refs:
            first_value = str(row.get("value") or row.get("preview") or "").strip()
            if first_value:
                refs = [first_value]
        if refs:
            existing = out.get(role_name) or []
            out[role_name] = list(dict.fromkeys([*existing, *refs]))[:8]
    return out


def _build_roles_inventory(input_pkg: dict[str, Any], refs_inventory: dict[str, Any], assigned_roles: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    refs_present_by_role = _collect_refs_present_by_role(input_pkg, refs_inventory)
    present_roles = [role for role in _BASE_ROLES if refs_present_by_role.get(role)]

    refs_inventory_summary: list[dict[str, Any]] = []
    for role in present_roles:
        refs = refs_present_by_role.get(role) or []
        refs_inventory_summary.append({"role": role, "ref_count": len(refs), "preview": refs[:2]})

    character_roles_present = [role for role in _CHARACTER_ROLES if role in present_roles]
    world_roles_present = [role for role in _WORLD_ROLES if role in present_roles]

    return {
        "assigned_roles": _safe_dict(assigned_roles),
        "present_roles": present_roles,
        "refs_present_by_role": refs_present_by_role,
        "refs_inventory_summary": refs_inventory_summary,
    }, character_roles_present, world_roles_present


def _build_scene_windows(audio_map: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, row_raw in enumerate(_safe_list(audio_map.get("scene_candidate_windows")), start=1):
        row = _safe_dict(row_raw)
        t0 = _round3(row.get("t0"))
        t1 = _round3(row.get("t1"))
        if t1 <= t0:
            continue
        out.append(
            {
                "id": str(row.get("id") or f"sc_{idx}"),
                "t0": t0,
                "t1": t1,
                "duration_sec": _round3(row.get("duration_sec") or (t1 - t0)),
                "phrase_text": str(row.get("phrase_text") or "").strip(),
                "scene_function": str(row.get("scene_function") or "").strip(),
                "energy": str(row.get("energy") or "").strip(),
                "transcript_confidence": str(row.get("transcript_confidence") or "").strip(),
            }
        )
    return out


def _build_role_planning_context(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    assigned_roles = _safe_dict(package.get("assigned_roles"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))

    roles_inventory, character_roles_present, world_roles_present = _build_roles_inventory(input_pkg, refs_inventory, assigned_roles)
    scene_windows = _build_scene_windows(audio_map)

    compact_context = {
        "mode": "clip",
        "content_type": str(input_pkg.get("content_type") or ""),
        "format": str(input_pkg.get("format") or ""),
        "director_note": str(input_pkg.get("director_note") or input_pkg.get("note") or "")[:1200],
        "story_core": {
            "story_summary": str(story_core.get("story_summary") or "")[:1000],
            "opening_anchor": str(story_core.get("opening_anchor") or "")[:500],
            "ending_callback_rule": str(story_core.get("ending_callback_rule") or "")[:500],
            "global_arc": str(story_core.get("global_arc") or "")[:500],
            "identity_lock": _safe_dict(story_core.get("identity_lock")),
            "world_lock": _safe_dict(story_core.get("world_lock")),
            "style_lock": _safe_dict(story_core.get("style_lock")),
        },
        "roles_inventory": roles_inventory,
        "scene_windows": scene_windows,
        "sections": _safe_list(audio_map.get("sections")),
        "clip_role_policy": {
            "goal": "watchable music-video style scene casting",
            "single_hero_bias": True,
            "respect_world_refs": True,
            "respect_props_refs": True,
            "respect_style_refs": True,
            "do_not_force_all_roles_into_every_scene": True,
        },
    }

    aux = {
        "scene_windows": scene_windows,
        "present_roles": roles_inventory.get("present_roles") or [],
        "character_roles_present": character_roles_present,
        "world_roles_present": world_roles_present,
    }
    return compact_context, aux


def _build_prompt(context: dict[str, Any]) -> str:
    return (
        "You are ROLE PLAN stage for scenario pipeline.\n"
        "Return STRICT JSON only. No markdown.\n"
        "mode=clip only.\n"
        "Task: distribute role presence across already fixed scene windows.\n"
        "Do NOT force all roles into every scene.\n"
        "Use only roles that are present in roles_inventory.\n"
        "World refs (location/style/props) are globally visible, but scene-level should explicitly mark active/inactive.\n"
        "If only one character ref exists, keep strong single-hero continuity.\n"
        "location is a world anchor, not necessarily scene subject.\n"
        "props should appear only where useful.\n"
        "style is global style anchor, not a scene object.\n"
        "No scene prompts, no camera routes, no i2v/ia2v assignment.\n"
        "Keep choices watchable, emotionally coherent, clip-friendly.\n\n"
        "Return EXACT contract keys:\n"
        "{\n"
        '  "plan_version": "role_plan_v1",\n'
        '  "mode": "clip",\n'
        '  "global_roles": {"primary_character_roles": [], "support_character_roles": [], "world_roles": [], "prop_roles": []},\n'
        '  "scene_roles": [{"scene_id": "sc_1", "t0": 0.0, "t1": 1.0, "primary_role": "character_1", "secondary_roles": [], "active_roles": ["character_1", "location", "style"], "inactive_roles": ["character_2", "character_3", "props"], "character_presence": "solo", "performance_focus": true, "role_reason": ""}],\n'
        '  "role_arc_summary": "",\n'
        '  "continuity_notes": [""]\n'
        "}\n\n"
        f"ROLE_PLANNING_CONTEXT:\n{json.dumps(context, ensure_ascii=False)}"
    )


def _normalize_role_list(values: Any, allowed_roles: set[str]) -> list[str]:
    return list(dict.fromkeys([role for role in [str(item).strip() for item in _safe_list(values)] if role in allowed_roles]))


def _default_scene_role_row(scene_window: dict[str, Any], *, hero_role: str, world_anchors: list[str], all_roles: list[str]) -> dict[str, Any]:
    active_roles = [*([hero_role] if hero_role else []), *world_anchors]
    active_roles = list(dict.fromkeys([role for role in active_roles if role]))
    return {
        "scene_id": str(scene_window.get("id") or ""),
        "t0": _round3(scene_window.get("t0")),
        "t1": _round3(scene_window.get("t1")),
        "primary_role": hero_role or None,
        "secondary_roles": [],
        "active_roles": active_roles,
        "inactive_roles": [role for role in all_roles if role not in active_roles],
        "character_presence": "solo" if hero_role else "none",
        "performance_focus": bool(hero_role),
        "role_reason": "fallback_scene_completion",
    }


def _normalize_role_plan(
    raw_plan: dict[str, Any],
    *,
    scene_windows: list[dict[str, Any]],
    present_roles: list[str],
    character_roles_present: list[str],
    world_roles_present: list[str],
) -> tuple[dict[str, Any], bool, str]:
    allowed_roles = set(present_roles)
    known_scene_ids = {str(row.get("id") or ""): row for row in scene_windows}
    first_character_role = character_roles_present[0] if character_roles_present else ""

    raw_global = _safe_dict(raw_plan.get("global_roles"))
    global_roles = {
        "primary_character_roles": _normalize_role_list(raw_global.get("primary_character_roles"), set(character_roles_present)) or ([first_character_role] if first_character_role else []),
        "support_character_roles": _normalize_role_list(raw_global.get("support_character_roles"), set(character_roles_present)),
        "world_roles": _normalize_role_list(raw_global.get("world_roles"), set(world_roles_present)) or list(world_roles_present),
        "prop_roles": _normalize_role_list(raw_global.get("prop_roles"), set(_PROP_ROLES) & allowed_roles),
    }
    if global_roles["primary_character_roles"]:
        global_roles["support_character_roles"] = [
            role for role in global_roles["support_character_roles"] if role not in set(global_roles["primary_character_roles"])
        ]

    scene_roles_by_id: dict[str, dict[str, Any]] = {}
    for row_raw in _safe_list(raw_plan.get("scene_roles")):
        row = _safe_dict(row_raw)
        scene_id = str(row.get("scene_id") or "").strip()
        window = known_scene_ids.get(scene_id)
        if not window:
            continue

        primary_role_raw = str(row.get("primary_role") or "").strip()
        primary_role = primary_role_raw if primary_role_raw in set(character_roles_present) else None
        secondary_roles = [
            role for role in _normalize_role_list(row.get("secondary_roles"), allowed_roles)
            if role != primary_role
        ]

        active_roles = _normalize_role_list(row.get("active_roles"), allowed_roles)
        if primary_role and primary_role not in active_roles:
            active_roles.insert(0, primary_role)
        if not active_roles:
            active_roles = [*([first_character_role] if first_character_role else []), *world_roles_present]
            active_roles = [role for role in active_roles if role in allowed_roles]

        character_presence = str(row.get("character_presence") or "").strip().lower() or ("solo" if primary_role else "none")
        if character_presence not in {"solo", "duo", "ensemble", "none"}:
            character_presence = "solo" if primary_role else "none"

        scene_roles_by_id[scene_id] = {
            "scene_id": scene_id,
            "t0": _round3(window.get("t0")),
            "t1": _round3(window.get("t1")),
            "primary_role": primary_role,
            "secondary_roles": secondary_roles,
            "active_roles": active_roles,
            "inactive_roles": [role for role in present_roles if role not in active_roles],
            "character_presence": character_presence,
            "performance_focus": bool(row.get("performance_focus")) if primary_role else False,
            "role_reason": str(row.get("role_reason") or "").strip() or "scene_role_distribution",
        }

    used_fallback = False
    normalized_scene_roles: list[dict[str, Any]] = []
    for window in scene_windows:
        scene_id = str(window.get("id") or "")
        if scene_id in scene_roles_by_id:
            normalized_scene_roles.append(scene_roles_by_id[scene_id])
        else:
            used_fallback = True
            normalized_scene_roles.append(
                _default_scene_role_row(
                    window,
                    hero_role=first_character_role,
                    world_anchors=world_roles_present,
                    all_roles=present_roles,
                )
            )

    role_arc_summary = str(raw_plan.get("role_arc_summary") or "").strip() or "Clip role continuity with scene-aware casting."
    continuity_notes = [str(item).strip() for item in _safe_list(raw_plan.get("continuity_notes")) if str(item).strip()]
    if not continuity_notes:
        continuity_notes = ["Preserve role continuity while avoiding overloaded scenes."]

    plan = {
        "plan_version": ROLE_PLAN_PROMPT_VERSION,
        "mode": "clip",
        "global_roles": global_roles,
        "scene_roles": normalized_scene_roles,
        "role_arc_summary": role_arc_summary,
        "continuity_notes": continuity_notes,
    }
    validation_error = "" if normalized_scene_roles else "scene_roles_empty_after_normalization"
    return plan, used_fallback, validation_error


def build_gemini_role_plan(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    context, aux = _build_role_planning_context(package)
    scene_windows = _safe_list(aux.get("scene_windows"))
    present_roles = [str(role) for role in _safe_list(aux.get("present_roles")) if str(role).strip()]
    character_roles_present = [str(role) for role in _safe_list(aux.get("character_roles_present")) if str(role).strip()]
    world_roles_present = [str(role) for role in _safe_list(aux.get("world_roles_present")) if str(role).strip()]

    diagnostics = {
        "prompt_version": ROLE_PLAN_PROMPT_VERSION,
        "scene_count": len(scene_windows),
        "present_roles": present_roles,
        "character_roles_count": len(character_roles_present),
        "world_roles_count": len(world_roles_present),
    }

    if not scene_windows:
        plan, used_fallback, validation_error = _normalize_role_plan(
            {},
            scene_windows=scene_windows,
            present_roles=present_roles,
            character_roles_present=character_roles_present,
            world_roles_present=world_roles_present,
        )
        return {
            "ok": False,
            "role_plan": plan,
            "error": "scene_windows_missing",
            "validation_error": validation_error or "scene_windows_missing",
            "used_fallback": True,
            "diagnostics": diagnostics,
        }

    prompt = _build_prompt(context)
    try:
        response = post_generate_content(
            api_key=str(api_key or "").strip(),
            model="gemini-2.5-pro",
            body={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.2},
            },
            timeout=90,
        )
        if isinstance(response, dict) and response.get("__http_error__"):
            raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")

        parsed = _extract_json_obj(_extract_gemini_text(response))
        role_plan, used_fallback, validation_error = _normalize_role_plan(
            parsed,
            scene_windows=scene_windows,
            present_roles=present_roles,
            character_roles_present=character_roles_present,
            world_roles_present=world_roles_present,
        )
        return {
            "ok": bool(role_plan.get("scene_roles")),
            "role_plan": role_plan,
            "error": "" if role_plan.get("scene_roles") else "invalid_role_plan",
            "validation_error": validation_error,
            "used_fallback": used_fallback,
            "diagnostics": diagnostics,
        }
    except Exception as exc:  # noqa: BLE001
        role_plan, used_fallback, validation_error = _normalize_role_plan(
            {},
            scene_windows=scene_windows,
            present_roles=present_roles,
            character_roles_present=character_roles_present,
            world_roles_present=world_roles_present,
        )
        return {
            "ok": bool(role_plan.get("scene_roles")),
            "role_plan": role_plan,
            "error": str(exc),
            "validation_error": validation_error,
            "used_fallback": True,
            "diagnostics": diagnostics,
        }
