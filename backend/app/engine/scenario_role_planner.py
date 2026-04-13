from __future__ import annotations

import json
import re
from typing import Any

from app.engine.gemini_rest import post_generate_content

ROLE_PLAN_PROMPT_VERSION = "role_plan_v2"

_BASE_ROLES = ["character_1", "character_2", "character_3", "location", "props", "style"]
ALLOWED_SCENE_ROLES = set(_BASE_ROLES)
_CHARACTER_ROLES = ["character_1", "character_2", "character_3"]
_WORLD_ROLES = ["location", "style"]
_PROP_ROLES = ["props"]
_SCENE_SUBJECT_PRIORITIES = {"hero", "hero_plus_world", "hero_plus_prop", "world_anchor"}
_PROP_ACTIVATION_MODES = {"anchor_worn", "visible_anchor", "silhouette_anchor", "not_emphasized"}
_WORLD_EMPHASIS_LEVELS = {"low", "medium", "high"}
_OWNERSHIP_ROLE_MAP = {
    "main": "character_1",
    "support": "character_2",
    "antagonist": "character_3",
    "shared": "shared",
    "world": "environment",
}
_BINDING_TYPES = {"carried", "worn", "held", "pocketed", "nearby", "environment"}

ALLOWED_SCENE_PRESENCE_MODES = {
    "solo_performance",
    "solo_observational",
    "environment_anchor",
    "transit",
    "private_release",
}

_COUNTRY_HINTS = {
    "iran": "Iran",
    "iranian": "Iran",
    "иран": "Iran",
    "tehran": "Iran",
    "usa": "USA",
    "united states": "USA",
    "america": "USA",
    "new york": "USA",
    "uk": "United Kingdom",
    "england": "United Kingdom",
    "london": "United Kingdom",
    "france": "France",
    "paris": "France",
    "japan": "Japan",
    "tokyo": "Japan",
}


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _normalize_ref_meta(meta: Any) -> dict[str, str]:
    row = _safe_dict(meta)
    ownership_role = str(row.get("ownershipRole") or row.get("ownership_role") or "auto").strip().lower() or "auto"
    ownership_mapped = str(row.get("ownershipRoleMapped") or row.get("ownership_role_mapped") or "").strip().lower()
    if ownership_mapped not in {"character_1", "character_2", "character_3", "shared", "environment"}:
        ownership_mapped = _OWNERSHIP_ROLE_MAP.get(ownership_role, "")
    binding_type = str(row.get("bindingType") or row.get("binding_type") or "nearby").strip().lower() or "nearby"
    if binding_type not in _BINDING_TYPES:
        binding_type = "nearby"
    return {
        "ownershipRole": ownership_role,
        "ownershipRoleMapped": ownership_mapped,
        "bindingType": binding_type,
    }


def _build_ref_binding_inventory(refs_inventory: dict[str, Any]) -> list[dict[str, str]]:
    inventory: list[dict[str, str]] = []
    for key, value in refs_inventory.items():
        row = _safe_dict(value)
        meta = _normalize_ref_meta(row.get("meta"))
        if not meta["ownershipRoleMapped"] and meta["bindingType"] == "nearby":
            continue
        role_name = _role_from_ref_key(key)
        label = str(row.get("source_label") or row.get("value") or key).strip()[:120]
        inventory.append(
            {
                "refRole": role_name or str(key),
                "label": label or str(key),
                "ownershipRole": meta["ownershipRole"],
                "ownershipRoleMapped": meta["ownershipRoleMapped"],
                "bindingType": meta["bindingType"],
            }
        )
    return inventory[:16]


def _is_world_bound_binding_row(row: dict[str, Any]) -> bool:
    ref_role = str(row.get("refRole") or "").strip().lower()
    owner = str(row.get("ownershipRoleMapped") or "").strip().lower()
    binding = str(row.get("bindingType") or "").strip().lower()
    if owner == "environment":
        return True
    if ref_role in {"world", "location", "props", "style"}:
        return True
    if ref_role in {"character_1", "character_2", "character_3"}:
        return False
    return binding == "environment"


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


def _has_world_anchor_signal(input_pkg: dict[str, Any], story_core: dict[str, Any]) -> bool:
    world_lock = _safe_dict(story_core.get("world_lock"))
    world_lock_blob = " ".join(
        [
            str(world_lock.get("setting") or ""),
            str(world_lock.get("rules") or ""),
            str(world_lock.get("setting_description") or ""),
            str(world_lock.get("socio_cultural_context") or ""),
            " ".join(str(item).strip() for item in _safe_list(world_lock.get("key_locations")) if str(item).strip()),
        ]
    ).strip()
    if world_lock_blob:
        return True
    text_blob = " ".join(
        str(part or "")
        for part in [
            input_pkg.get("note"),
            input_pkg.get("story_text"),
            input_pkg.get("director_note"),
            story_core.get("story_summary"),
            story_core.get("opening_anchor"),
        ]
    ).lower()
    anchor_hints = ("urban", "street", "city", "district", "tehran", "iran", "iranian", "neighborhood", "daylight")
    return any(hint in text_blob for hint in anchor_hints)


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


def _build_audio_dramaturgy_context(audio_map: dict[str, Any]) -> dict[str, Any]:
    drama = _safe_dict(audio_map.get("audio_dramaturgy"))
    return {
        "dramaturgy_source": str(drama.get("dramaturgy_source") or "audio_primary"),
        "audio_drives_dramaturgy": bool(drama.get("audio_drives_dramaturgy") if "audio_drives_dramaturgy" in drama else True),
        "dominant_energy": str(drama.get("dominant_energy") or ""),
        "energy_curve_summary": str(drama.get("energy_curve_summary") or ""),
        "peak_window_ids": _safe_list(drama.get("peak_window_ids") or drama.get("high_energy_window_ids")),
        "build_window_ids": _safe_list(drama.get("build_window_ids")),
        "release_window_ids": _safe_list(drama.get("release_window_ids")),
        "tail_resolution_window_ids": _safe_list(drama.get("tail_resolution_window_ids")),
        "low_energy_window_ids": _safe_list(drama.get("low_energy_window_ids")),
        "medium_energy_window_ids": _safe_list(drama.get("medium_energy_window_ids")),
        "high_energy_window_ids": _safe_list(drama.get("high_energy_window_ids")),
        "textual_directive_present": bool(drama.get("textual_directive_present")),
    }


def _build_role_planning_context(package: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    assigned_roles = _safe_dict(package.get("assigned_roles"))
    story_core = _safe_dict(package.get("story_core"))
    audio_map = _safe_dict(package.get("audio_map"))
    binding_inventory = _build_ref_binding_inventory(refs_inventory)

    roles_inventory, character_roles_present, world_roles_present = _build_roles_inventory(input_pkg, refs_inventory, assigned_roles)
    if _has_world_anchor_signal(input_pkg, story_core) and "location" not in world_roles_present:
        world_roles_present = [*world_roles_present, "location"]
        roles_inventory["present_roles"] = list(dict.fromkeys([*(_safe_list(roles_inventory.get("present_roles"))), "location"]))
    scene_windows = _build_scene_windows(audio_map)
    audio_dramaturgy = _build_audio_dramaturgy_context(audio_map)

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
            "story_guidance": _safe_dict(story_core.get("story_guidance")),
        },
        "roles_inventory": roles_inventory,
        "ownership_binding_inventory": binding_inventory,
        "scene_windows": scene_windows,
        "audio_dramaturgy": audio_dramaturgy,
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
        "input_pkg": input_pkg,
        "story_core": story_core,
        "roles_inventory": roles_inventory,
        "ownership_binding_inventory": binding_inventory,
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
        "Use story_core.story_guidance as story-level constraints when available: world_progression_hints, viewer_contrast_rules, unexpected_realistic_beats, prop_guidance, narrative_pressure_rules.\n"
        "If only one character ref exists, keep strong single-hero continuity.\n"
        "location is a world anchor, not necessarily scene subject.\n"
        "props should appear only where useful.\n"
        "ownership_binding_inventory is a planning signal, not metadata-only.\n"
        "If ownershipRoleMapped=character_1 and bindingType in {carried, held}, keep props co-activated with character_1 in active scenes unless there is an explicit reason to suppress visual emphasis.\n"
        "bindingType semantics: carried=high continuity burden/conflict anchor; held=strong local hand-occupied co-activation; worn=silhouette/wardrobe continuity; pocketed=owner-linked but optional visual emphasis; nearby=owner-adjacent optional activation; environment=world-anchored not owner-locked.\n"
        "style is global style anchor, not a scene object.\n"
        "No scene prompts, no camera routes, no i2v/ia2v assignment.\n\n"
        "CLIP DRAMATURGY (MANDATORY):\n"
        "- In clip mode, scene presence mode is derived primarily from audio energy and phrase intensity.\n"
        "- Clip mode arc is visual/emotional progression, not default literal travel-story plotting.\n"
        "- Role distribution must serve musical dramaturgy, not arbitrary variety.\n"
        "- Low-energy windows: prefer solo_observational or private_release.\n"
        "- Medium-energy windows: prefer transit/solo_observational with occasional solo_performance.\n"
        "- High-energy windows: prefer solo_performance.\n"
        "- Release/tail windows: prefer private_release or restrained observational close.\n"
        "- Audio peaks naturally support performance emphasis.\n\n"
        "ROLE VARIETY (MANDATORY):\n"
        "- scene_presence_mode must be one of: solo_performance, solo_observational, environment_anchor, transit, private_release.\n"
        "- Even with one hero, vary scene_presence_mode across scenes when feasible.\n"
        "- Do NOT mark every scene as performer-centered.\n"
        "- performance_focus=true only for genuinely performer-driven scenes.\n"
        "- For clip pacing target about 2-4 performance_focus=true scenes when scene count allows; avoid flat distribution.\n\n"
        "WORLD CONTINUITY (MANDATORY):\n"
        "- Maintain one coherent world across all scenes.\n"
        "- If location/style refs exist, lock to them: world_anchor_mode=ref_locked.\n"
        "- location ref defines geographic/architectural/environmental anchor.\n"
        "- style ref defines visual/tonal/aesthetic anchor.\n"
        "- Both anchors must remain stable across scenes; variation only inside same world/style family.\n"
        "- If no location ref is provided, infer one coherent realistic world anchor from note/story/story_core and keep all scenes inside that same country/city/environment logic.\n"
        "- If user text is absent, avoid over-literal geographic progression and keep continuity tight.\n"
        "- Do not drift into arbitrary location-chain journey logic unless explicit textual directive demands geography progression.\n"
        "- Prefer world continuity + energy/intimacy progression over invented travel mechanics.\n"
        "- If refs imply one heroine + one location + one prop, keep same-world continuity and avoid unnecessary travel invention.\n"
        "- Allow only natural local progression when explicitly grounded; no random cross-country or cross-style jumps.\n"
        "- If no explicit time-of-day is given, choose a baseline in scene 1 and keep believable lighting progression.\n\n"
        "REALISM (MANDATORY):\n"
        "- Always stay realistic and grounded by default.\n"
        "- No fantasy, no surreal jumps, no cross-country drift, no random aesthetic teleportation.\n"
        "- Lighting continuity must be plausible; no unmotivated day/night/style teleports.\n\n"
        "Return EXACT contract keys:\n"
        "{\n"
        '  "plan_version": "role_plan_v2",\n'
        '  "mode": "clip",\n'
        '  "global_roles": {"primary_character_roles": [], "support_character_roles": [], "world_roles": [], "prop_roles": []},\n'
        '  "world_continuity": {"world_anchor_mode": "inferred", "country_or_region": "", "environment_family": "", "location_progression": [], "style_anchor": "", "realism_contract": "", "lighting_continuity": {"time_of_day_base": "", "allowed_progression": "", "color_temperature_band": "", "contrast_profile": "", "shadow_behavior": "", "practical_sources": [], "forbidden_shifts": []}, "continuity_rules": []},\n'
        '  "scene_roles": [{"scene_id": "sc_1", "t0": 0.0, "t1": 1.0, "primary_role": "character_1", "secondary_roles": [], "active_roles": ["character_1", "location", "style"], "inactive_roles": ["character_2", "character_3", "props"], "character_presence": "solo", "scene_presence_mode": "solo_observational", "performance_focus": false, "role_reason": ""}],\n'
        '  "role_arc_summary": "",\n'
        '  "continuity_notes": [""]\n'
        "}\n\n"
        f"ROLE_PLANNING_CONTEXT:\n{json.dumps(_compact_prompt_payload(context), ensure_ascii=False)}"
    )


def _normalize_role_list(values: Any, allowed_roles: set[str]) -> list[str]:
    return list(dict.fromkeys([role for role in [str(item).strip() for item in _safe_list(values)] if role in allowed_roles]))


def _extract_country_or_region(*parts: str) -> str:
    merged = " ".join(str(part or "") for part in parts).lower()
    for hint, country in _COUNTRY_HINTS.items():
        if re.search(rf"\b{re.escape(hint)}\b", merged):
            return country
    return ""


def _build_world_lock_text(story_core: dict[str, Any]) -> str:
    world_lock = _safe_dict(story_core.get("world_lock"))
    key_locations = [
        str(item).strip()
        for item in _safe_list(world_lock.get("key_locations"))
        if str(item).strip()
    ]
    parts = [
        world_lock.get("setting"),
        world_lock.get("rules"),
        world_lock.get("setting_description"),
        world_lock.get("socio_cultural_context"),
        world_lock.get("rule"),
        world_lock.get("summary"),
        " ".join(key_locations),
    ]
    return " ".join(str(part).strip() for part in parts if str(part).strip())


def _build_style_anchor_text(story_core: dict[str, Any]) -> str:
    style_lock = _safe_dict(story_core.get("style_lock"))
    visual_style_tags = [
        str(item).strip()
        for item in _safe_list(style_lock.get("visual_style_tags"))
        if str(item).strip()
    ]
    core_parts = [
        style_lock.get("visual_mood"),
        ", ".join(visual_style_tags),
        style_lock.get("mood_and_tone"),
        style_lock.get("summary"),
        style_lock.get("rule"),
    ]
    secondary_parts = [
        style_lock.get("audio_style"),
        style_lock.get("audio_mood"),
    ]
    negative_tags = [
        str(item).strip()
        for item in _safe_list(style_lock.get("negative_style_tags"))
        if str(item).strip()
    ]
    negative_prompts = str(style_lock.get("negative_prompts") or "").strip()
    parts: list[str] = [str(part).strip() for part in core_parts if str(part).strip()]
    parts.extend(str(part).strip() for part in secondary_parts if str(part).strip())
    if negative_tags:
        parts.append(f"avoid: {', '.join(negative_tags[:4])}")
    elif negative_prompts:
        parts.append(f"avoid: {negative_prompts[:120]}")
    return " | ".join(parts)


def _extract_world_lock_key_locations(story_core: dict[str, Any]) -> list[str]:
    world_lock = _safe_dict(story_core.get("world_lock"))
    return [
        str(item).strip()
        for item in _safe_list(world_lock.get("key_locations"))
        if str(item).strip()
    ]


def _infer_time_of_day_base(*parts: str) -> str:
    merged = " ".join(str(part or "") for part in parts).lower()
    if "dusk" in merged or "evening" in merged:
        return "dusk"
    if "night" in merged:
        return "night"
    if "morning" in merged:
        return "morning"
    if "afternoon" in merged:
        return "afternoon"
    if "daylight" in merged or re.search(r"\bday\b", merged):
        return "day"
    return "late afternoon"


def _default_lighting_continuity(time_of_day_base: str) -> dict[str, Any]:
    base = str(time_of_day_base or "late_afternoon").strip().lower().replace(" ", "_")
    if base in {"afternoon", "late_afternoon"}:
        return {
            "time_of_day_base": "late_afternoon",
            "allowed_progression": "consistent_late_afternoon",
            "color_temperature_band": "4300_5200k",
            "contrast_profile": "soft_to_medium",
            "shadow_behavior": "long_soft_shadows",
            "practical_sources": ["window_light", "street_bounce"],
            "forbidden_shifts": ["neon", "club_color", "day_to_night", "unmotivated_weather_change"],
        }
    if base in {"night", "dusk", "evening"}:
        return {
            "time_of_day_base": "night",
            "allowed_progression": "consistent_night",
            "color_temperature_band": "2800_4300k",
            "contrast_profile": "medium_to_high",
            "shadow_behavior": "controlled_deep_shadows",
            "practical_sources": ["street_lamps", "window_practicals"],
            "forbidden_shifts": ["daylight_jump", "unmotivated_sunlight", "club_color"],
        }
    return {
        "time_of_day_base": base or "late_afternoon",
        "allowed_progression": "local_realistic_progression",
        "color_temperature_band": "neutral_natural",
        "contrast_profile": "soft_to_medium",
        "shadow_behavior": "consistent_natural_shadows",
        "practical_sources": ["ambient_natural", "local_practicals"],
        "forbidden_shifts": ["unmotivated_day_night_jump", "neon_style_teleport"],
    }


def _infer_environment_family(story_summary: str, country_or_region: str) -> str:
    story = str(story_summary or "").lower()
    if "urban" in story or "street" in story:
        return "grounded contemporary urban environment"
    if country_or_region:
        return f"grounded contemporary environment in {country_or_region}"
    return "grounded realistic world"


def _normalize_world_continuity(raw_world: Any, *, input_pkg: dict[str, Any], story_core: dict[str, Any], has_world_refs: bool) -> dict[str, Any]:
    row = _safe_dict(raw_world)
    world_lock_text = _build_world_lock_text(story_core)
    style_anchor_fallback = _build_style_anchor_text(story_core)
    key_locations_fallback = _extract_world_lock_key_locations(story_core)

    fallback_country = _extract_country_or_region(
        input_pkg.get("note"),
        input_pkg.get("story_text"),
        input_pkg.get("director_note"),
        story_core.get("story_summary"),
        world_lock_text,
    )
    inferred_time_of_day = _infer_time_of_day_base(
        input_pkg.get("note"),
        input_pkg.get("story_text"),
        input_pkg.get("director_note"),
        story_core.get("story_summary"),
        world_lock_text,
        style_anchor_fallback,
    )

    mode_raw = str(row.get("world_anchor_mode") or "").strip().lower()
    world_anchor_mode = "ref_locked" if has_world_refs else "inferred"
    if mode_raw in {"inferred", "ref_locked"}:
        world_anchor_mode = mode_raw

    lighting = _safe_dict(row.get("lighting_continuity"))
    default_lighting = _default_lighting_continuity(str(lighting.get("time_of_day_base") or inferred_time_of_day))
    lighting_normalized = {
        "time_of_day_base": str(lighting.get("time_of_day_base") or "").strip() or str(default_lighting.get("time_of_day_base") or ""),
        "allowed_progression": str(lighting.get("allowed_progression") or "").strip() or str(default_lighting.get("allowed_progression") or ""),
        "color_temperature_band": str(lighting.get("color_temperature_band") or "").strip() or str(default_lighting.get("color_temperature_band") or ""),
        "contrast_profile": str(lighting.get("contrast_profile") or "").strip() or str(default_lighting.get("contrast_profile") or ""),
        "shadow_behavior": str(lighting.get("shadow_behavior") or "").strip() or str(default_lighting.get("shadow_behavior") or ""),
        "practical_sources": [str(item).strip() for item in _safe_list(lighting.get("practical_sources")) if str(item).strip()] or _safe_list(default_lighting.get("practical_sources")),
        "forbidden_shifts": [str(item).strip() for item in _safe_list(lighting.get("forbidden_shifts")) if str(item).strip()] or _safe_list(default_lighting.get("forbidden_shifts")),
    }

    normalized = {
        "world_anchor_mode": world_anchor_mode,
        "country_or_region": str(row.get("country_or_region") or "").strip() or fallback_country,
        "environment_family": str(row.get("environment_family") or "").strip()
        or _infer_environment_family(story_core.get("story_summary") or "", fallback_country),
        "location_progression": [str(item).strip() for item in _safe_list(row.get("location_progression")) if str(item).strip()],
        "style_anchor": str(row.get("style_anchor") or "").strip() or style_anchor_fallback,
        "realism_contract": str(row.get("realism_contract") or "").strip()
        or "Always grounded and realistic continuity. No cross-country or cross-style drift.",
        "lighting_continuity": lighting_normalized,
        "continuity_rules": [str(item).strip() for item in _safe_list(row.get("continuity_rules")) if str(item).strip()],
    }

    if not normalized["style_anchor"]:
        normalized["style_anchor"] = "naturalistic observational realism, restrained tone"

    if not normalized["location_progression"]:
        if len(key_locations_fallback) >= 2:
            normalized["location_progression"] = key_locations_fallback[:6]
        else:
            normalized["location_progression"] = ["establishing street", "adjacent passage", "nearby interior"]

    if not normalized["continuity_rules"]:
        normalized["continuity_rules"] = [
            "Keep all scenes inside one coherent country/city/environment logic.",
            "Allow only local plausible movement between spaces.",
            "Keep style and realism stable unless explicit refs require otherwise.",
        ]
    return normalized


def _default_scene_presence_mode(scene_window: dict[str, Any], idx: int, total: int, has_hero: bool) -> str:
    if not has_hero:
        return "environment_anchor"
    energy = str(scene_window.get("energy") or "").strip().lower()
    scene_function = str(scene_window.get("scene_function") or "").strip().lower()
    phrase_text = str(scene_window.get("phrase_text") or "").strip().lower()
    performance_cues = ("performance", "perform", "sing", "dance", "chorus", "hook", "drop", "refrain")
    has_performance_cue = any(cue in scene_function or cue in phrase_text for cue in performance_cues)
    if "release" in scene_function or "afterimage" in scene_function:
        return "private_release"
    if energy == "high":
        if has_performance_cue:
            return "solo_performance"
        return "transit" if idx not in {0, total - 1} else "solo_observational"
    if energy == "low":
        return "solo_observational" if idx < total - 1 else "private_release"
    if energy == "medium":
        return "transit" if idx not in {0, total - 1} else "solo_observational"
    if total <= 1:
        return "solo_observational"
    if idx == total - 1 and total >= 4:
        return "private_release"
    pattern = ["solo_observational", "solo_performance", "transit", "environment_anchor"]
    return pattern[idx % len(pattern)]


def _infer_scene_subject_priority(*, scene_presence_mode: str, active_roles: list[str], primary_role: str | None) -> str:
    mode = str(scene_presence_mode or "").strip().lower()
    active = set(active_roles)
    if mode == "environment_anchor" and "location" in active:
        return "world_anchor"
    if primary_role and "location" in active:
        return "hero_plus_world"
    if primary_role and "props" in active:
        return "hero_plus_prop"
    if primary_role:
        return "hero"
    if "location" in active:
        return "world_anchor"
    return "hero"


def _infer_world_emphasis(scene_presence_mode: str, scene_subject_priority: str) -> str:
    mode = str(scene_presence_mode or "").strip().lower()
    if mode in {"environment_anchor", "transit"} or scene_subject_priority in {"hero_plus_world", "world_anchor"}:
        return "high"
    if mode in {"solo_observational", "private_release"}:
        return "medium"
    return "low"


def _infer_prop_activation_mode(*, props_active: bool, scene_subject_priority: str, scene_presence_mode: str) -> str:
    if not props_active:
        return "not_emphasized"
    if scene_subject_priority == "hero_plus_prop":
        return "visible_anchor"
    if scene_presence_mode == "private_release":
        return "silhouette_anchor"
    return "anchor_worn"


def _default_scene_role_row(
    scene_window: dict[str, Any],
    *,
    hero_role: str,
    world_anchors: list[str],
    all_roles: list[str],
    idx: int,
    total_scenes: int,
) -> dict[str, Any]:
    active_roles = [*([hero_role] if hero_role else []), *world_anchors]
    active_roles = list(dict.fromkeys([role for role in active_roles if role]))
    if "props" in all_roles and hero_role and idx in {0, total_scenes - 1}:
        active_roles.append("props")
        active_roles = list(dict.fromkeys(active_roles))
    scene_presence_mode = _default_scene_presence_mode(scene_window, idx, total_scenes, bool(hero_role))
    scene_subject_priority = _infer_scene_subject_priority(
        scene_presence_mode=scene_presence_mode,
        active_roles=active_roles,
        primary_role=hero_role or None,
    )
    world_emphasis = _infer_world_emphasis(scene_presence_mode, scene_subject_priority)
    prop_activation_mode = _infer_prop_activation_mode(
        props_active="props" in active_roles,
        scene_subject_priority=scene_subject_priority,
        scene_presence_mode=scene_presence_mode,
    )
    return {
        "scene_id": str(scene_window.get("id") or ""),
        "t0": _round3(scene_window.get("t0")),
        "t1": _round3(scene_window.get("t1")),
        "primary_role": hero_role or None,
        "secondary_roles": [],
        "active_roles": active_roles,
        "inactive_roles": [role for role in all_roles if role not in active_roles],
        "character_presence": "solo" if hero_role else "none",
        "scene_presence_mode": scene_presence_mode,
        "performance_focus": bool(hero_role and scene_presence_mode == "solo_performance"),
        "scene_subject_priority": scene_subject_priority,
        "prop_activation_mode": prop_activation_mode,
        "world_emphasis": world_emphasis,
        "role_reason": "fallback_scene_completion",
    }


def _normalize_role_plan(
    raw_plan: dict[str, Any],
    *,
    scene_windows: list[dict[str, Any]],
    present_roles: list[str],
    character_roles_present: list[str],
    world_roles_present: list[str],
    input_pkg: dict[str, Any],
    story_core: dict[str, Any],
    ownership_binding_inventory: list[dict[str, str]] | None = None,
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

    has_world_refs = "location" in world_roles_present or "style" in world_roles_present
    world_continuity = _normalize_world_continuity(
        raw_plan.get("world_continuity"),
        input_pkg=input_pkg,
        story_core=story_core,
        has_world_refs=has_world_refs,
    )

    scene_roles_by_id: dict[str, dict[str, Any]] = {}
    binding_rows = _safe_list(ownership_binding_inventory)
    carried_owner_props = {
        str(item.get("ownershipRoleMapped") or "").strip().lower()
        for item in binding_rows
        if str(item.get("bindingType") or "").strip().lower() == "carried"
    }
    held_owner_props = {
        str(item.get("ownershipRoleMapped") or "").strip().lower()
        for item in binding_rows
        if str(item.get("bindingType") or "").strip().lower() == "held"
    }
    moderate_owner_props = {
        str(item.get("ownershipRoleMapped") or "").strip().lower()
        for item in binding_rows
        if str(item.get("bindingType") or "").strip().lower() in {"worn", "pocketed", "nearby"}
    }
    has_world_environment_binding = any(
        _is_world_bound_binding_row(_safe_dict(item))
        and str(item.get("bindingType") or "").strip().lower() == "environment"
        for item in binding_rows
    )
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

        character_presence = str(row.get("character_presence") or "").strip().lower() or ("solo" if primary_role else "none")
        if character_presence not in {"solo", "duo", "ensemble", "none"}:
            character_presence = "solo" if primary_role else "none"

        scene_presence_mode = str(row.get("scene_presence_mode") or "").strip().lower()
        if scene_presence_mode not in ALLOWED_SCENE_PRESENCE_MODES:
            scene_presence_mode = _default_scene_presence_mode(window, len(scene_roles_by_id), len(scene_windows), bool(primary_role))

        active_roles = _normalize_role_list(row.get("active_roles"), allowed_roles)
        if primary_role and primary_role not in active_roles:
            active_roles.insert(0, primary_role)
        has_world_anchor = "location" in world_roles_present
        if has_world_anchor and "location" not in active_roles and scene_presence_mode in {"transit", "environment_anchor"}:
            active_roles.append("location")
        if "props" in allowed_roles and "props" in active_roles and scene_presence_mode == "environment_anchor":
            active_roles = [role for role in active_roles if role != "props"]
        if not active_roles:
            active_roles = [*([first_character_role] if first_character_role else []), *world_roles_present]
            active_roles = [role for role in active_roles if role in allowed_roles]
        if "props" in allowed_roles and primary_role:
            scene_function_blob = " ".join(
                [
                    str(window.get("scene_function") or "").strip().lower(),
                    str(window.get("phrase_text") or "").strip().lower(),
                    str(row.get("role_reason") or "").strip().lower(),
                ]
            )
            carried_owner_scene = primary_role in carried_owner_props and (
                scene_presence_mode in {"solo_performance", "solo_observational", "transit", "private_release"}
                or any(tag in scene_function_blob for tag in {"pressure", "evasion", "conceal", "escape", "release"})
            )
            held_owner_scene = primary_role in held_owner_props and (
                scene_presence_mode in {"solo_performance", "solo_observational", "transit", "private_release"}
                or any(tag in scene_function_blob for tag in {"pressure", "evasion", "conceal", "escape", "release"})
            )
            if carried_owner_scene or held_owner_scene:
                if "props" not in active_roles:
                    active_roles.append("props")
            elif primary_role in moderate_owner_props and scene_presence_mode in {"solo_performance", "solo_observational"} and "props" not in active_roles:
                active_roles.append("props")
        carried_owner_scene_for_env_guard = bool(
            primary_role
            and primary_role in carried_owner_props
            and scene_presence_mode in {"environment_anchor", "transit", "private_release"}
            and "props" in active_roles
        )
        held_owner_scene_for_env_guard = bool(
            primary_role
            and primary_role in held_owner_props
            and scene_presence_mode in {"transit", "private_release"}
            and "props" in active_roles
        )
        if (
            has_world_environment_binding
            and scene_presence_mode in {"environment_anchor", "transit"}
            and "props" in active_roles
            and not carried_owner_scene_for_env_guard
            and not held_owner_scene_for_env_guard
        ):
            active_roles = [role for role in active_roles if role != "props"]

        performance_focus = bool(row.get("performance_focus")) if primary_role else False
        if scene_presence_mode in {"environment_anchor", "transit", "solo_observational"}:
            performance_focus = False
        if scene_presence_mode == "solo_performance":
            scene_function_blob = " ".join(
                [
                    str(window.get("scene_function") or "").lower(),
                    str(window.get("phrase_text") or "").lower(),
                    str(row.get("role_reason") or "").lower(),
                ]
            )
            performance_focus = any(tag in scene_function_blob for tag in {"perform", "sing", "dance", "chorus", "stage"})

        scene_subject_priority_raw = str(row.get("scene_subject_priority") or "").strip().lower()
        scene_subject_priority = scene_subject_priority_raw if scene_subject_priority_raw in _SCENE_SUBJECT_PRIORITIES else _infer_scene_subject_priority(
            scene_presence_mode=scene_presence_mode,
            active_roles=active_roles,
            primary_role=primary_role,
        )

        prop_activation_mode_raw = str(row.get("prop_activation_mode") or "").strip().lower()
        prop_activation_mode = prop_activation_mode_raw if prop_activation_mode_raw in _PROP_ACTIVATION_MODES else _infer_prop_activation_mode(
            props_active="props" in active_roles,
            scene_subject_priority=scene_subject_priority,
            scene_presence_mode=scene_presence_mode,
        )
        if "props" in active_roles and primary_role in carried_owner_props:
            prop_activation_mode = "visible_anchor"
        elif "props" in active_roles and primary_role in held_owner_props and prop_activation_mode == "not_emphasized":
            prop_activation_mode = "visible_anchor" if scene_presence_mode in {"transit", "private_release"} else "anchor_worn"
        elif "props" in active_roles and primary_role in moderate_owner_props and prop_activation_mode == "not_emphasized":
            prop_activation_mode = "anchor_worn"
        elif has_world_environment_binding and scene_presence_mode in {"environment_anchor", "transit"}:
            prop_activation_mode = "not_emphasized"

        world_emphasis_raw = str(row.get("world_emphasis") or "").strip().lower()
        world_emphasis = world_emphasis_raw if world_emphasis_raw in _WORLD_EMPHASIS_LEVELS else _infer_world_emphasis(
            scene_presence_mode=scene_presence_mode,
            scene_subject_priority=scene_subject_priority,
        )

        scene_roles_by_id[scene_id] = {
            "scene_id": scene_id,
            "t0": _round3(window.get("t0")),
            "t1": _round3(window.get("t1")),
            "primary_role": primary_role,
            "secondary_roles": secondary_roles,
            "active_roles": active_roles,
            "inactive_roles": [role for role in present_roles if role not in active_roles],
            "character_presence": character_presence,
            "scene_presence_mode": scene_presence_mode,
            "performance_focus": performance_focus,
            "scene_subject_priority": scene_subject_priority,
            "prop_activation_mode": prop_activation_mode,
            "world_emphasis": world_emphasis,
            "role_reason": str(row.get("role_reason") or "").strip() or "scene_role_distribution",
        }

    used_fallback = False
    normalized_scene_roles: list[dict[str, Any]] = []
    total_scenes = len(scene_windows)
    for idx, window in enumerate(scene_windows):
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
                    idx=idx,
                    total_scenes=total_scenes,
                )
            )

    role_arc_summary = str(raw_plan.get("role_arc_summary") or "").strip() or "Single-hero arc moves between internal pressure and urban world contact, then resolves into restrained release."
    continuity_notes = [str(item).strip() for item in _safe_list(raw_plan.get("continuity_notes")) if str(item).strip()]
    if not continuity_notes:
        continuity_notes = [
            "Single hero continuity: character_1 remains the only narrative subject across all windows.",
            "Prop continuity: keep the current continuity object/wearable identity stable when present.",
            "World continuity: one coherent realistic urban location anchor is preserved across scenes.",
            "Lighting/realism lock: grounded daylight progression; avoid neon/club/warehouse drift.",
        ]

    plan = {
        "plan_version": ROLE_PLAN_PROMPT_VERSION,
        "mode": "clip",
        "global_roles": global_roles,
        "world_continuity": world_continuity,
        "scene_roles": normalized_scene_roles,
        "role_arc_summary": role_arc_summary,
        "continuity_notes": continuity_notes,
    }
    validation_error = "" if normalized_scene_roles else "scene_roles_empty_after_normalization"
    return plan, used_fallback, validation_error


def _presence_diagnostics(scene_roles: list[dict[str, Any]], world_continuity: dict[str, Any]) -> dict[str, Any]:
    presence_modes = [
        str(_safe_dict(row).get("scene_presence_mode") or "").strip()
        for row in scene_roles
        if str(_safe_dict(row).get("scene_presence_mode") or "").strip()
    ]
    performance_values = [bool(_safe_dict(row).get("performance_focus")) for row in scene_roles]
    unique_presence = sorted(set(presence_modes))
    unique_performance = sorted(set(performance_values))
    subject_priorities = sorted(
        {
            str(_safe_dict(row).get("scene_subject_priority") or "").strip()
            for row in scene_roles
            if str(_safe_dict(row).get("scene_subject_priority") or "").strip()
        }
    )
    world_emphasis_levels = sorted(
        {
            str(_safe_dict(row).get("world_emphasis") or "").strip()
            for row in scene_roles
            if str(_safe_dict(row).get("world_emphasis") or "").strip()
        }
    )
    return {
        "role_plan_world_anchor_mode": str(world_continuity.get("world_anchor_mode") or ""),
        "role_plan_country_or_region": str(world_continuity.get("country_or_region") or ""),
        "role_plan_presence_modes": unique_presence,
        "role_plan_presence_flat": bool(scene_roles) and len(unique_presence) <= 1,
        "role_plan_performance_focus_flat": bool(scene_roles) and len(unique_performance) <= 1,
        "role_plan_subject_priorities": subject_priorities,
        "role_plan_world_emphasis_levels": world_emphasis_levels,
    }


def build_gemini_role_plan(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    context, aux = _build_role_planning_context(package)
    scene_windows = _safe_list(aux.get("scene_windows"))
    present_roles = [str(role) for role in _safe_list(aux.get("present_roles")) if str(role).strip()]
    character_roles_present = [str(role) for role in _safe_list(aux.get("character_roles_present")) if str(role).strip()]
    world_roles_present = [str(role) for role in _safe_list(aux.get("world_roles_present")) if str(role).strip()]
    input_pkg = _safe_dict(aux.get("input_pkg"))
    story_core = _safe_dict(aux.get("story_core"))
    ownership_binding_inventory = _safe_list(aux.get("ownership_binding_inventory"))

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
            input_pkg=input_pkg,
            story_core=story_core,
            ownership_binding_inventory=ownership_binding_inventory,
        )
        diagnostics.update(_presence_diagnostics(_safe_list(plan.get("scene_roles")), _safe_dict(plan.get("world_continuity"))))
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
            input_pkg=input_pkg,
            story_core=story_core,
            ownership_binding_inventory=ownership_binding_inventory,
        )
        diagnostics.update(_presence_diagnostics(_safe_list(role_plan.get("scene_roles")), _safe_dict(role_plan.get("world_continuity"))))
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
            input_pkg=input_pkg,
            story_core=story_core,
            ownership_binding_inventory=ownership_binding_inventory,
        )
        diagnostics.update(_presence_diagnostics(_safe_list(role_plan.get("scene_roles")), _safe_dict(role_plan.get("world_continuity"))))
        return {
            "ok": bool(role_plan.get("scene_roles")),
            "role_plan": role_plan,
            "error": str(exc),
            "validation_error": validation_error,
            "used_fallback": True,
            "diagnostics": diagnostics,
        }
