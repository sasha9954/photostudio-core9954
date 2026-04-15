from __future__ import annotations

import json
import re
from typing import Any

from app.engine.gemini_rest import post_generate_content

ROLE_PLAN_PROMPT_VERSION = "roles_v1_1"
ROLES_VERSION = "1.1"

ROLES_SCHEMA_INVALID = "ROLES_SCHEMA_INVALID"
ROLES_SEGMENT_ID_MISMATCH = "ROLES_SEGMENT_ID_MISMATCH"
ROLES_CASTING_GAP = "ROLES_CASTING_GAP"
ROLES_ENTITY_HALLUCINATION = "ROLES_ENTITY_HALLUCINATION"
ROLES_ACTION_LEAKING = "ROLES_ACTION_LEAKING"
ROLES_TECHNICAL_LEAKING = "ROLES_TECHNICAL_LEAKING"
ROLES_CREATIVE_ROUTE = "ROLES_CREATIVE_ROUTE"
ROLES_CONTINUITY_BREAK = "ROLES_CONTINUITY_BREAK"
ROLES_DOCTRINE_DUPLICATION = "ROLES_DOCTRINE_DUPLICATION"

ALLOWED_PRESENCE_MODES = {"physical", "voiceover", "shadow", "implied"}
ALLOWED_PRESENCE_WEIGHTS = {"anchor", "primary", "support", "background"}

_ACTION_LEAK_TOKENS = {"fight", "run", "chase", "jump", "shoot", "explosion"}
_TECH_LEAK_TOKENS = {"fps", "lens", "focal", "camera", "exposure", "iso", "render", "seed", "sampler"}
_ROUTE_LEAK_TOKENS = {"route", "i2v", "ia2v", "first_last", "camera_move", "camera motion"}


def _safe_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _normalize_text(value: Any, *, max_len: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())[:max_len]


def _build_segment_rows(audio_map: dict[str, Any], story_core: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], str, dict[str, Any]]:
    audio_segments = [_safe_dict(row) for row in _safe_list(audio_map.get("segments"))]
    core_segments = [_safe_dict(row) for row in _safe_list(story_core.get("narrative_segments"))]

    audio_ids = [str(row.get("segment_id") or "").strip() for row in audio_segments]
    core_ids = [str(row.get("segment_id") or "").strip() for row in core_segments]

    diagnostics: dict[str, Any] = {
        "uses_audio_transcript_slice": False,
        "uses_core_arc_role": False,
        "uses_core_beat_purpose": False,
        "uses_core_emotional_key": False,
        "fell_back_to_legacy_audio_text_fields": False,
        "fell_back_to_legacy_core_fields": False,
        "missing_core_meaning_rows": 0,
    }

    if not audio_ids or not core_ids:
        return [], [], ROLES_CASTING_GAP, diagnostics
    if any(not segment_id for segment_id in audio_ids) or any(not segment_id for segment_id in core_ids):
        return [], [], ROLES_SEGMENT_ID_MISMATCH, diagnostics
    if audio_ids != core_ids:
        return [], [], ROLES_SEGMENT_ID_MISMATCH, diagnostics

    segments: list[dict[str, Any]] = []
    for arow, crow in zip(audio_segments, core_segments, strict=False):
        transcript_slice_raw = arow.get("transcript_slice")
        if transcript_slice_raw not in (None, ""):
            diagnostics["uses_audio_transcript_slice"] = True
        elif arow.get("text") not in (None, "") or arow.get("transcript") not in (None, ""):
            diagnostics["fell_back_to_legacy_audio_text_fields"] = True

        arc_role_raw = crow.get("arc_role")
        beat_purpose_raw = crow.get("beat_purpose")
        emotional_key_raw = crow.get("emotional_key")

        if arc_role_raw not in (None, ""):
            diagnostics["uses_core_arc_role"] = True
        if beat_purpose_raw not in (None, ""):
            diagnostics["uses_core_beat_purpose"] = True
        if emotional_key_raw not in (None, ""):
            diagnostics["uses_core_emotional_key"] = True
        if (
            (arc_role_raw in (None, "") and (crow.get("segment_function") not in (None, "") or crow.get("narrative_function") not in (None, "")))
            or (beat_purpose_raw in (None, "") and (crow.get("segment_function") not in (None, "") or crow.get("narrative_function") not in (None, "")))
            or (emotional_key_raw in (None, "") and (crow.get("emotional_beat") not in (None, "") or crow.get("emotional_intent") not in (None, "")))
        ):
            diagnostics["fell_back_to_legacy_core_fields"] = True

        arc_role = _normalize_text(crow.get("arc_role") or crow.get("segment_function") or crow.get("narrative_function") or "", max_len=220)
        beat_purpose = _normalize_text(crow.get("beat_purpose") or crow.get("segment_function") or crow.get("narrative_function") or "", max_len=220)
        emotional_key = _normalize_text(crow.get("emotional_key") or crow.get("emotional_beat") or crow.get("emotional_intent") or "", max_len=220)
        if not (arc_role or beat_purpose or emotional_key):
            diagnostics["missing_core_meaning_rows"] = int(diagnostics.get("missing_core_meaning_rows") or 0) + 1

        segments.append(
            {
                "segment_id": str(arow.get("segment_id") or "").strip(),
                "t0": arow.get("t0"),
                "t1": arow.get("t1"),
                "duration_sec": arow.get("duration_sec"),
                "transcript_slice": _normalize_text(arow.get("transcript_slice") or arow.get("text") or arow.get("transcript") or "", max_len=320),
                "arc_role": arc_role,
                "beat_purpose": beat_purpose,
                "emotional_key": emotional_key,
            }
        )
    if diagnostics["missing_core_meaning_rows"] > 0:
        return [], [], ROLES_CASTING_GAP, diagnostics
    return segments, audio_ids, "", diagnostics


def _collect_allowed_entity_registry(package: dict[str, Any]) -> dict[str, dict[str, Any]]:
    input_pkg = _safe_dict(package.get("input"))
    refs_inventory = _safe_dict(package.get("refs_inventory"))
    assigned_roles = _safe_dict(package.get("assigned_roles"))
    connected_context_summary = _safe_dict(input_pkg.get("connected_context_summary"))

    registry: dict[str, dict[str, Any]] = {}

    def _upsert(entity_id: str, source: str, role_name: str = "") -> None:
        clean_id = _normalize_text(entity_id, max_len=80)
        if not clean_id:
            return
        row = registry.setdefault(clean_id, {"entity_id": clean_id, "role_name": "", "sources": []})
        if role_name and not row.get("role_name"):
            row["role_name"] = role_name
        sources = _safe_list(row.get("sources"))
        if source not in sources:
            sources.append(source)
        row["sources"] = sources

    for role_name in _safe_dict(input_pkg.get("refs_by_role")).keys():
        _upsert(str(role_name), "input.refs_by_role", role_name=str(role_name))

    for ref_id, ref_payload in refs_inventory.items():
        entity_id = str(ref_id)
        if entity_id.startswith("ref_"):
            entity_id = entity_id[4:]
        label = str(_safe_dict(ref_payload).get("source_label") or "")
        _upsert(entity_id, "refs_inventory", role_name=label)

    for role_name in assigned_roles.keys():
        _upsert(str(role_name), "assigned_roles", role_name=str(role_name))

    connected_roles = _safe_dict(connected_context_summary.get("connectedRefsPresentByRole"))
    if not connected_roles:
        connected_roles = _safe_dict(connected_context_summary.get("refsPresentByRole"))
    for role_name in connected_roles.keys():
        _upsert(str(role_name), "connected_context_summary", role_name=str(role_name))

    return registry


def _build_roles_prompt(context: dict[str, Any]) -> str:
    return (
        "You are ROLES stage (GEMINI-FIRST 7-layer canon) for scenario pipeline.\n"
        "Return STRICT JSON ONLY. No markdown. No comments.\n"
        "Canonical key is segment_id only.\n"
        "Read-only sources: story_core.narrative_segments + audio_map.segments + entity registry.\n"
        "Do not use scene_id as source of truth.\n"
        "Do not use scene_candidate_windows as source of truth.\n"
        "Forbidden leakage: action choreography, route planning, camera/motion directives, prompt-language authoring, technical/render details.\n"
        "Forbidden hallucination: NEVER introduce entities outside allowed_entity_registry.\n"
        "Output EXACT schema:"
        "{"
        '"roles_version":"1.1",'
        '"roster":[{"entity_id":"string","role_name":"string","continuity_rules":["string"]}],'
        '"scene_casting":[{"segment_id":"string","primary_role":"string","secondary_roles":["string"],"presence_mode":"physical|voiceover|shadow|implied","presence_weight":"anchor|primary|support|background","performance_focus":"string"}]'
        "}.\n"
        "Every segment_id must have exactly one scene_casting row.\n"
        f"ROLES_CONTEXT:\n{json.dumps(_compact_prompt_payload(context), ensure_ascii=False)}"
    )


def _extract_error_from_leakage(roles_payload: dict[str, Any]) -> str:
    serialized = json.dumps(roles_payload, ensure_ascii=False).lower()
    if any(token in serialized for token in _ROUTE_LEAK_TOKENS):
        return ROLES_CREATIVE_ROUTE
    if any(token in serialized for token in _TECH_LEAK_TOKENS):
        return ROLES_TECHNICAL_LEAKING
    if any(token in serialized for token in _ACTION_LEAK_TOKENS):
        return ROLES_ACTION_LEAKING
    return ""


def _normalize_roster(raw_roster: Any, allowed_registry: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row_raw in _safe_list(raw_roster):
        row = _safe_dict(row_raw)
        entity_id = _normalize_text(row.get("entity_id"), max_len=80)
        if not entity_id:
            continue
        out.append(
            {
                "entity_id": entity_id,
                "role_name": _normalize_text(row.get("role_name") or _safe_dict(allowed_registry.get(entity_id)).get("role_name") or entity_id, max_len=120),
                "continuity_rules": [
                    _normalize_text(item, max_len=180)
                    for item in _safe_list(row.get("continuity_rules"))
                    if _normalize_text(item, max_len=180)
                ][:8],
            }
        )
    return list({row["entity_id"]: row for row in out}.values())


def _normalize_scene_casting(raw_scene_casting: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row_raw in _safe_list(raw_scene_casting):
        row = _safe_dict(row_raw)
        secondary_roles = list(
            dict.fromkeys(
                [
                    _normalize_text(item, max_len=80)
                    for item in _safe_list(row.get("secondary_roles"))
                    if _normalize_text(item, max_len=80)
                ]
            )
        )
        out.append(
            {
                "segment_id": _normalize_text(row.get("segment_id"), max_len=80),
                "primary_role": _normalize_text(row.get("primary_role"), max_len=80),
                "secondary_roles": secondary_roles,
                "presence_mode": _normalize_text(row.get("presence_mode"), max_len=40).lower(),
                "presence_weight": _normalize_text(row.get("presence_weight"), max_len=40).lower(),
                "performance_focus": _normalize_text(row.get("performance_focus"), max_len=220),
            }
        )
    return out


def _validate_roles_payload(
    *,
    roles_payload: dict[str, Any],
    expected_segment_ids: list[str],
    allowed_registry: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    roster = _normalize_roster(roles_payload.get("roster"), allowed_registry)
    scene_casting = _normalize_scene_casting(roles_payload.get("scene_casting"))

    if not roster or not scene_casting:
        return {}, ROLES_SCHEMA_INVALID

    roster_ids = {str(row.get("entity_id") or "").strip() for row in roster if str(row.get("entity_id") or "").strip()}
    if not roster_ids:
        return {}, ROLES_SCHEMA_INVALID

    if not roster_ids.issubset(set(allowed_registry.keys())):
        return {}, ROLES_ENTITY_HALLUCINATION

    seen_segment_ids: list[str] = []
    for row in scene_casting:
        segment_id = str(row.get("segment_id") or "").strip()
        if not segment_id:
            return {}, ROLES_SCHEMA_INVALID
        seen_segment_ids.append(segment_id)
        if str(row.get("primary_role") or "").strip() not in roster_ids:
            return {}, ROLES_ENTITY_HALLUCINATION
        for secondary in _safe_list(row.get("secondary_roles")):
            if str(secondary or "").strip() not in roster_ids:
                return {}, ROLES_ENTITY_HALLUCINATION
        if str(row.get("presence_mode") or "") not in ALLOWED_PRESENCE_MODES:
            return {}, ROLES_SCHEMA_INVALID
        if str(row.get("presence_weight") or "") not in ALLOWED_PRESENCE_WEIGHTS:
            return {}, ROLES_SCHEMA_INVALID

    if len(set(seen_segment_ids)) != len(seen_segment_ids):
        return {}, ROLES_DOCTRINE_DUPLICATION

    expected_set = set(expected_segment_ids)
    seen_set = set(seen_segment_ids)
    if seen_set != expected_set:
        missing = expected_set - seen_set
        extra = seen_set - expected_set
        if missing:
            return {}, ROLES_CASTING_GAP
        if extra:
            return {}, ROLES_SEGMENT_ID_MISMATCH
        return {}, ROLES_SEGMENT_ID_MISMATCH

    if seen_segment_ids != expected_segment_ids:
        return {}, ROLES_CONTINUITY_BREAK

    normalized = {
        "roles_version": ROLES_VERSION,
        "roster": roster,
        "scene_casting": scene_casting,
    }
    leak_error = _extract_error_from_leakage(normalized)
    if leak_error:
        return {}, leak_error
    return normalized, ""


def _build_role_plan_legacy_bridge_from_roles_v11(
    *,
    roles_payload: dict[str, Any],
) -> dict[str, Any]:
    roster = [_safe_dict(row) for row in _safe_list(roles_payload.get("roster"))]
    scene_casting = [_safe_dict(row) for row in _safe_list(roles_payload.get("scene_casting"))]
    roster_ids = [str(row.get("entity_id") or "").strip() for row in roster if str(row.get("entity_id") or "").strip()]

    scene_roles: list[dict[str, Any]] = []
    for row in scene_casting:
        primary = str(row.get("primary_role") or "").strip()
        secondary = [str(item).strip() for item in _safe_list(row.get("secondary_roles")) if str(item).strip()]
        active_roles = list(dict.fromkeys([primary, *secondary]))
        inactive_roles = [entity_id for entity_id in roster_ids if entity_id and entity_id not in active_roles]
        scene_roles.append(
            {
                "scene_id": str(row.get("segment_id") or "").strip(),
                "segment_id": str(row.get("segment_id") or "").strip(),
                "primary_role": primary,
                "secondary_roles": secondary,
                "active_roles": active_roles,
                "inactive_roles": inactive_roles,
                "scene_presence_mode": str(row.get("presence_mode") or "").strip(),
                "presence_weight": str(row.get("presence_weight") or "").strip(),
                "performance_focus": str(row.get("performance_focus") or "").strip(),
            }
        )

    return {
        "legacy_bridge_generated": True,
        "legacy_bridge_source": "roles_v1_1_scene_casting",
        "deprecated": True,
        "scene_roles": scene_roles,
    }


def build_gemini_role_plan(*, api_key: str, package: dict[str, Any]) -> dict[str, Any]:
    input_pkg = _safe_dict(package.get("input"))
    audio_map = _safe_dict(package.get("audio_map"))
    story_core = _safe_dict(package.get("story_core"))

    segment_rows, expected_segment_ids, segment_error, segment_row_diagnostics = _build_segment_rows(audio_map, story_core)
    allowed_registry = _collect_allowed_entity_registry(package)

    diagnostics = {
        "prompt_version": ROLE_PLAN_PROMPT_VERSION,
        "roles_version": ROLES_VERSION,
        "roster_count": 0,
        "scene_casting_count": 0,
        "segment_coverage_ok": False,
        "retry_count": 0,
        "error_code": "",
        "uses_audio_transcript_slice": False,
        "uses_core_arc_role": False,
        "uses_core_beat_purpose": False,
        "uses_core_emotional_key": False,
        "fell_back_to_legacy_audio_text_fields": False,
        "fell_back_to_legacy_core_fields": False,
    }

    diagnostics.update(segment_row_diagnostics)

    if segment_error:
        diagnostics["error_code"] = segment_error
        return {
            "ok": False,
            "role_plan": {},
            "error": segment_error,
            "error_code": segment_error,
            "validation_error": segment_error,
            "used_fallback": False,
            "retry_count": 0,
            "diagnostics": diagnostics,
        }

    if not allowed_registry:
        diagnostics["error_code"] = ROLES_ENTITY_HALLUCINATION
        return {
            "ok": False,
            "role_plan": {},
            "error": ROLES_ENTITY_HALLUCINATION,
            "error_code": ROLES_ENTITY_HALLUCINATION,
            "validation_error": ROLES_ENTITY_HALLUCINATION,
            "used_fallback": False,
            "retry_count": 0,
            "diagnostics": diagnostics,
        }

    context = {
        "mode": "clip",
        "content_type": str(input_pkg.get("content_type") or ""),
        "story_core": {
            "story_summary": _normalize_text(story_core.get("story_summary"), max_len=1000),
            "narrative_segments": [
                {
                    "segment_id": str(_safe_dict(row).get("segment_id") or "").strip(),
                    "arc_role": _normalize_text(_safe_dict(row).get("arc_role") or _safe_dict(row).get("segment_function") or _safe_dict(row).get("narrative_function") or "", max_len=220),
                    "beat_purpose": _normalize_text(_safe_dict(row).get("beat_purpose") or _safe_dict(row).get("segment_function") or _safe_dict(row).get("narrative_function") or "", max_len=220),
                    "emotional_key": _normalize_text(_safe_dict(row).get("emotional_key") or _safe_dict(row).get("emotional_beat") or _safe_dict(row).get("emotional_intent") or "", max_len=220),
                }
                for row in _safe_list(story_core.get("narrative_segments"))
            ],
        },
        "audio_map": {
            "segments": segment_rows,
        },
        "allowed_entity_registry": list(allowed_registry.values()),
        "entity_registry_sources": ["input.refs_by_role", "refs_inventory", "assigned_roles", "connected_context_summary"],
    }

    prompt = _build_roles_prompt(context)
    attempts = 2
    last_error = ROLES_SCHEMA_INVALID

    for attempt in range(attempts):
        diagnostics["retry_count"] = attempt
        try:
            response = post_generate_content(
                api_key=str(api_key or "").strip(),
                model="gemini-2.5-pro",
                body={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"responseMimeType": "application/json", "temperature": 0.1},
                },
                timeout=90,
            )
            if isinstance(response, dict) and response.get("__http_error__"):
                raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")

            parsed = _extract_json_obj(_extract_gemini_text(response))
            normalized, validation_error = _validate_roles_payload(
                roles_payload=parsed,
                expected_segment_ids=expected_segment_ids,
                allowed_registry=allowed_registry,
            )
            if validation_error:
                last_error = validation_error
                if attempt < attempts - 1:
                    prompt = (
                        f"{prompt}\n\nPREVIOUS_VALIDATION_ERROR={validation_error}. "
                        "You must fix exactly this and return valid schema JSON only."
                    )
                    continue
                break

            bridge = _build_role_plan_legacy_bridge_from_roles_v11(roles_payload=normalized)
            role_plan = {**normalized, **bridge}
            diagnostics.update(
                {
                    "roster_count": len(_safe_list(normalized.get("roster"))),
                    "scene_casting_count": len(_safe_list(normalized.get("scene_casting"))),
                    "segment_coverage_ok": True,
                    "error_code": "",
                }
            )
            return {
                "ok": True,
                "role_plan": role_plan,
                "error": "",
                "error_code": "",
                "validation_error": "",
                "used_fallback": False,
                "retry_count": attempt,
                "diagnostics": diagnostics,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or ROLES_SCHEMA_INVALID
            if attempt < attempts - 1:
                continue

    diagnostics["error_code"] = last_error
    return {
        "ok": False,
        "role_plan": {},
        "error": last_error,
        "error_code": last_error,
        "validation_error": last_error,
        "used_fallback": False,
        "retry_count": attempts - 1,
        "diagnostics": diagnostics,
    }
