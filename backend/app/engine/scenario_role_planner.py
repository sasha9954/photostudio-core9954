from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any

from app.engine.gemini_rest import post_generate_content
from app.engine.scenario_stage_timeout_policy import (
    get_scenario_stage_timeout,
    is_timeout_error,
    scenario_timeout_policy_name,
)

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
ROLE_PRIMARY_MISMATCH = "ROLE_PRIMARY_MISMATCH"

ALLOWED_PRESENCE_MODES = {"physical", "voiceover", "shadow", "implied"}
ALLOWED_PRESENCE_WEIGHTS = {"anchor", "primary", "support", "background"}

_ACTION_LEAK_TOKENS = {"fight", "run", "chase", "jump", "shoot", "explosion"}
_TECH_LEAK_TOKENS = {"fps", "lens", "camera", "exposure", "iso", "render", "seed", "sampler"}
_TECH_LEAK_STRICT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("focal length", re.compile(r"\bfocal\s+length\b", re.IGNORECASE)),
    ("focal distance", re.compile(r"\bfocal\s+distance\b", re.IGNORECASE)),
    ("lens focal", re.compile(r"\blens\s+focal\b", re.IGNORECASE)),
)
_FOCAL_ALLOWED_PHRASE_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bemotional\s+focal\s+point\b", re.IGNORECASE), "main emotional focus"),
    (re.compile(r"\bvisual\s+focal\s+point\b", re.IGNORECASE), "main visual focus"),
    (re.compile(r"\bfocal\s+point\s+of\s+the\s+narrative\b", re.IGNORECASE), "main focus of the narrative"),
    (re.compile(r"\bfocal\s+point\b", re.IGNORECASE), "main focus"),
)
_ROUTE_LEAK_TOKENS = {"route", "i2v", "ia2v", "first_last", "camera_move", "camera motion"}
_ROLE_PLAN_ALLOWED_KEYS = {"roles_version", "roster", "scene_casting"}
_ROSTER_ALLOWED_KEYS = {"entity_id", "role_name", "continuity_rules"}
_SCENE_CASTING_ALLOWED_KEYS = {
    "segment_id",
    "primary_role",
    "secondary_roles",
    "presence_mode",
    "presence_weight",
    "performance_focus",
    "continuity_notes",
}
_ROLE_PLAN_TECHNICAL_BANNED_TERMS: tuple[str, ...] = (
    "reference image",
    "visual reference",
    "connected character",
    "canonical source of truth",
    "refspresentbyrole",
    "connected_context_summary",
    "body proportions",
    "auxiliary only",
    "technical contract",
    "input package",
    "source of truth",
)
_ROLE_PLAN_TECHNICAL_REPLACEMENTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bconnected\s+character_1\s+image\s+reference\b", re.IGNORECASE), "the established heroine"),
    (re.compile(r"\breference\s+image\b", re.IGNORECASE), "same character"),
    (re.compile(r"\bvisual\s+reference\b", re.IGNORECASE), "same character look"),
    (re.compile(r"\bcanonical\s+source\s+of\s+truth\b", re.IGNORECASE), ""),
    (re.compile(r"\bsource\s+of\s+truth\b", re.IGNORECASE), ""),
    (re.compile(r"\bbody\s+proportions\b", re.IGNORECASE), "overall look"),
    (re.compile(r"\bauxiliary\s+only\b", re.IGNORECASE), ""),
    (re.compile(r"\brefspresentbyrole\b", re.IGNORECASE), ""),
    (re.compile(r"\bconnected_context_summary\b", re.IGNORECASE), ""),
    (re.compile(r"\btechnical\s+contract\b", re.IGNORECASE), ""),
    (re.compile(r"\binput\s+package\b", re.IGNORECASE), ""),
)
_TECH_IDENTITY_LEAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (term, re.compile(rf"\b{re.escape(term)}\b", re.IGNORECASE)) for term in _ROLE_PLAN_TECHNICAL_BANNED_TERMS
)


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


def _strip_code_fences(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", raw, count=1)
        raw = re.sub(r"\s*```$", "", raw, count=1)
    return raw.strip()


def _extract_strict_json_payload(text: str) -> dict[str, Any]:
    payload = _extract_json_obj(_strip_code_fences(text))
    if payload:
        return payload
    raw = _strip_code_fences(text)
    decoder = json.JSONDecoder()
    for idx, char in enumerate(raw):
        if char != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(raw[idx:])
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
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
        "normalized_segment_rows_error": "",
        "normalized_segment_rows_missing_core_meaning": "",
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
        diagnostics["normalized_segment_rows_error"] = "missing_core_meaning_rows"
        diagnostics["normalized_segment_rows_missing_core_meaning"] = "mapped_to_roles_casting_gap"
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
        "ROLES output must be story-facing only.\n"
        "Do not copy technical identity/reference/source wording from CORE.\n"
        "Use technical identity locks only internally, never in output text.\n"
        "Do not output terms: reference image, visual reference, connected character, canonical source of truth, refsPresentByRole, connected_context_summary, body proportions, auxiliary only, technical contract, input package, source of truth.\n"
        "Write continuity in plain story language (example: 'The same woman remains the central character throughout the clip.').\n"
        "Output EXACT schema:"
        "{"
        '"roles_version":"1.1",'
        '"roster":[{"entity_id":"string","role_name":"string","continuity_rules":["string"]}],'
        '"scene_casting":[{"segment_id":"string","primary_role":"string","secondary_roles":["string"],"presence_mode":"physical|voiceover|shadow|implied","presence_weight":"anchor|primary|support|background","performance_focus":"string","continuity_notes":"string"}]'
        "}.\n"
        "Every segment_id must have exactly one scene_casting row.\n"
        f"ROLES_CONTEXT:\n{json.dumps(_compact_prompt_payload(context), ensure_ascii=False)}"
    )


def sanitize_role_plan_technical_leaks(text: Any) -> str:
    normalized = str(text or "")
    if not normalized:
        return ""
    sanitized = normalized
    for pattern, replacement in _ROLE_PLAN_TECHNICAL_REPLACEMENTS:
        sanitized = pattern.sub(replacement, sanitized)
    sanitized = re.sub(r"\s{2,}", " ", sanitized).strip(" ,;:-")
    return re.sub(r"\s+", " ", sanitized).strip()


def _sanitize_role_plan_payload_fields(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    out = deepcopy(_safe_dict(payload))
    changed = False

    for roster_row_raw in _safe_list(out.get("roster")):
        roster_row = _safe_dict(roster_row_raw)
        continuity_rules = []
        for item in _safe_list(roster_row.get("continuity_rules")):
            original = str(item or "")
            rewritten = sanitize_role_plan_technical_leaks(original)
            if rewritten != original:
                changed = True
            if rewritten:
                continuity_rules.append(rewritten)
        roster_row["continuity_rules"] = continuity_rules
        visual_constraints = _safe_list(roster_row.get("visual_constraints"))
        if visual_constraints:
            sanitized_constraints = []
            for item in visual_constraints:
                original = str(item or "")
                rewritten = sanitize_role_plan_technical_leaks(original)
                if rewritten != original:
                    changed = True
                if rewritten:
                    sanitized_constraints.append(rewritten)
            roster_row["visual_constraints"] = sanitized_constraints

    for casting_row_raw in _safe_list(out.get("scene_casting")):
        casting_row = _safe_dict(casting_row_raw)
        original_reason = str(casting_row.get("reason") or "")
        if original_reason:
            rewritten_reason = sanitize_role_plan_technical_leaks(original_reason)
            if rewritten_reason != original_reason:
                changed = True
            casting_row["reason"] = rewritten_reason

    for scene_role_raw in _safe_list(out.get("scene_roles")):
        scene_role = _safe_dict(scene_role_raw)
        original_reason = str(scene_role.get("reason") or "")
        rewritten_reason = sanitize_role_plan_technical_leaks(original_reason)
        if rewritten_reason != original_reason:
            changed = True
        scene_role["reason"] = rewritten_reason
        continuity_notes = _safe_list(scene_role.get("continuity_notes"))
        if continuity_notes:
            rewritten_notes = []
            for note in continuity_notes:
                original = str(note or "")
                rewritten = sanitize_role_plan_technical_leaks(original)
                if rewritten != original:
                    changed = True
                if rewritten:
                    rewritten_notes.append(rewritten)
            scene_role["continuity_notes"] = rewritten_notes

    role_arc_summary = out.get("role_arc_summary")
    if isinstance(role_arc_summary, str):
        rewritten_summary = sanitize_role_plan_technical_leaks(role_arc_summary)
        if rewritten_summary != role_arc_summary:
            changed = True
        out["role_arc_summary"] = rewritten_summary

    return out, changed


def _extract_error_from_leakage(roles_payload: dict[str, Any]) -> str:
    serialized = json.dumps(roles_payload, ensure_ascii=False).lower()
    if any(token in serialized for token in _ROUTE_LEAK_TOKENS):
        return ROLES_CREATIVE_ROUTE
    if any(pattern.search(serialized) for _, pattern in _TECH_IDENTITY_LEAK_PATTERNS):
        return ROLES_TECHNICAL_LEAKING
    if any(pattern.search(serialized) for _, pattern in _TECH_LEAK_STRICT_PATTERNS):
        return ROLES_TECHNICAL_LEAKING
    if any(token in serialized for token in _TECH_LEAK_TOKENS):
        return ROLES_TECHNICAL_LEAKING
    if any(token in serialized for token in _ACTION_LEAK_TOKENS):
        return ROLES_ACTION_LEAKING
    return ""


def _extract_leakage_details(roles_payload: dict[str, Any]) -> tuple[str, str, str]:
    def _walk(node: Any, path: str) -> tuple[str, str, str]:
        if isinstance(node, dict):
            for key, value in node.items():
                next_path = f"{path}.{key}" if path else str(key)
                code, leak_path, token = _walk(value, next_path)
                if code:
                    return code, leak_path, token
        elif isinstance(node, list):
            for idx, value in enumerate(node):
                next_path = f"{path}[{idx}]"
                code, leak_path, token = _walk(value, next_path)
                if code:
                    return code, leak_path, token
        elif isinstance(node, str):
            lowered = node.lower()
            for token in _ROUTE_LEAK_TOKENS:
                if token in lowered:
                    return ROLES_CREATIVE_ROUTE, path, token
            for token, pattern in _TECH_LEAK_STRICT_PATTERNS:
                if pattern.search(lowered):
                    return ROLES_TECHNICAL_LEAKING, path, token
            for token, pattern in _TECH_IDENTITY_LEAK_PATTERNS:
                if pattern.search(lowered):
                    return ROLES_TECHNICAL_LEAKING, path, token
            for token in _TECH_LEAK_TOKENS:
                if token in lowered:
                    return ROLES_TECHNICAL_LEAKING, path, token
            for token in _ACTION_LEAK_TOKENS:
                if token in lowered:
                    return ROLES_ACTION_LEAKING, path, token
        return "", "", ""

    return _walk(roles_payload, "")


def _cleanup_roles_payload(raw_payload: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    payload = _safe_dict(raw_payload)
    dropped_fields: list[str] = []
    cleaned: dict[str, Any] = {
        "roles_version": _normalize_text(payload.get("roles_version"), max_len=20) or ROLES_VERSION,
        "roster": [],
        "scene_casting": [],
    }

    for key in payload.keys():
        if str(key) not in _ROLE_PLAN_ALLOWED_KEYS:
            dropped_fields.append(str(key))

    for row_raw in _safe_list(payload.get("roster")):
        row = _safe_dict(row_raw)
        for key in row.keys():
            if str(key) not in _ROSTER_ALLOWED_KEYS:
                dropped_fields.append(f"roster.{key}")
        cleaned["roster"].append(
            {
                "entity_id": row.get("entity_id"),
                "role_name": row.get("role_name"),
                "continuity_rules": row.get("continuity_rules"),
            }
        )

    for row_raw in _safe_list(payload.get("scene_casting")):
        row = _safe_dict(row_raw)
        for key in row.keys():
            if str(key) not in _SCENE_CASTING_ALLOWED_KEYS:
                dropped_fields.append(f"scene_casting.{key}")
        cleaned["scene_casting"].append(
            {
                "segment_id": row.get("segment_id"),
                "primary_role": row.get("primary_role"),
                "secondary_roles": row.get("secondary_roles"),
                "presence_mode": row.get("presence_mode"),
                "presence_weight": row.get("presence_weight"),
                "performance_focus": row.get("performance_focus"),
                "continuity_notes": row.get("continuity_notes"),
            }
        )
    return cleaned, sorted(list(dict.fromkeys(dropped_fields)))


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
                "continuity_notes": _normalize_text(row.get("continuity_notes"), max_len=220),
            }
        )
    return out


def _rewrite_allowed_focal_phrases(roles_payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    payload = deepcopy(_safe_dict(roles_payload))
    changed = False
    roster_rows = _safe_list(payload.get("roster"))
    scene_casting_rows = _safe_list(payload.get("scene_casting"))

    for roster_row in roster_rows:
        row = _safe_dict(roster_row)
        normalized_rules: list[str] = []
        for item in _safe_list(row.get("continuity_rules")):
            text = str(item or "")
            rewritten = text
            for pattern, replacement in _FOCAL_ALLOWED_PHRASE_REWRITES:
                rewritten = pattern.sub(replacement, rewritten)
            if rewritten != text:
                changed = True
            normalized_rules.append(rewritten)
        row["continuity_rules"] = normalized_rules

    for casting_row in scene_casting_rows:
        row = _safe_dict(casting_row)
        for field in ("continuity_notes", "performance_focus"):
            text = str(row.get(field) or "")
            rewritten = text
            for pattern, replacement in _FOCAL_ALLOWED_PHRASE_REWRITES:
                rewritten = pattern.sub(replacement, rewritten)
            if rewritten != text:
                changed = True
            row[field] = rewritten
    return payload, changed


def _build_core_subject_map(story_core: dict[str, Any]) -> dict[str, dict[str, Any]]:
    story_core_v1 = _safe_dict(story_core.get("story_core_v1"))
    if not story_core_v1 and str(story_core.get("schema_version") or "").startswith("core_v1"):
        story_core_v1 = story_core
    beat_map = _safe_dict(story_core_v1.get("beat_map"))
    beats = [_safe_dict(row) for row in _safe_list(beat_map.get("beats"))]
    out: dict[str, dict[str, Any]] = {}
    for beat in beats:
        segment_id = _normalize_text(beat.get("source_segment_id"), max_len=80)
        if not segment_id:
            continue
        primary_role = _normalize_text(beat.get("beat_primary_subject"), max_len=80)
        secondary_roles = list(
            dict.fromkeys(
                [
                    _normalize_text(item, max_len=80)
                    for item in _safe_list(beat.get("beat_secondary_subjects"))
                    if _normalize_text(item, max_len=80)
                ]
            )
        )
        out[segment_id] = {"primary_role": primary_role, "secondary_roles": secondary_roles}
    return out


def _ensure_core_entities_in_roster(
    *,
    roles_payload: dict[str, Any],
    core_subject_map: dict[str, dict[str, Any]],
    allowed_registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    roster = _normalize_roster(roles_payload.get("roster"), allowed_registry)
    roster_by_id = {str(row.get("entity_id") or "").strip(): row for row in roster}
    for core_row in core_subject_map.values():
        role_ids = [str(core_row.get("primary_role") or "").strip()] + [
            str(item or "").strip() for item in _safe_list(core_row.get("secondary_roles"))
        ]
        for role_id in role_ids:
            if not role_id or role_id in roster_by_id:
                continue
            allowed_row = _safe_dict(allowed_registry.get(role_id))
            roster_by_id[role_id] = {
                "entity_id": role_id,
                "role_name": _normalize_text(allowed_row.get("role_name") or role_id, max_len=120),
                "continuity_rules": [],
            }
    return {
        "roles_version": _normalize_text(roles_payload.get("roles_version"), max_len=20) or ROLES_VERSION,
        "roster": list(roster_by_id.values()),
        "scene_casting": _safe_list(roles_payload.get("scene_casting")),
    }


def _normalize_scene_casting_from_core(
    *,
    scene_casting: list[dict[str, Any]],
    core_subject_map: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    normalized_rows: list[dict[str, Any]] = []
    mismatches: list[dict[str, str]] = []
    for row in scene_casting:
        clean_row = dict(_safe_dict(row))
        segment_id = str(clean_row.get("segment_id") or "").strip()
        core_row = _safe_dict(core_subject_map.get(segment_id))
        if not segment_id or not core_row:
            normalized_rows.append(clean_row)
            continue
        expected_primary = str(core_row.get("primary_role") or "").strip()
        got_primary = str(clean_row.get("primary_role") or "").strip()
        if expected_primary and got_primary and got_primary != expected_primary:
            mismatches.append({"segment_id": segment_id, "expected": expected_primary, "got": got_primary})
        if expected_primary:
            clean_row["primary_role"] = expected_primary
        clean_row["secondary_roles"] = list(_safe_list(core_row.get("secondary_roles")))
        normalized_rows.append(clean_row)
    return normalized_rows, mismatches


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
    core_subject_map = _build_core_subject_map(story_core)

    diagnostics = {
        "prompt_version": ROLE_PLAN_PROMPT_VERSION,
        "roles_version": ROLES_VERSION,
        "roster_count": 0,
        "scene_casting_count": 0,
        "segment_coverage_ok": False,
        "retry_count": 0,
        "error_code": "",
        "raw_model_response_preview": "",
        "parsed_payload_preview": "",
        "sanitized_payload_preview": "",
        "normalized_role_plan_preview": "",
        "technical_leak_trigger": "",
        "technical_leak_field": "",
        "technical_leak_token": "",
        "false_positive_technical_leak_allowed": False,
        "allowed_technical_token": "",
        "allowed_technical_phrase": "",
        "dropped_non_canonical_fields": [],
        "coverage_expected_segment_ids": expected_segment_ids,
        "coverage_seen_segment_ids": [],
        "coverage_missing_segment_ids": expected_segment_ids,
        "coverage_extra_segment_ids": [],
        "uses_audio_transcript_slice": False,
        "uses_core_arc_role": False,
        "uses_core_beat_purpose": False,
        "uses_core_emotional_key": False,
        "fell_back_to_legacy_audio_text_fields": False,
        "fell_back_to_legacy_core_fields": False,
        "configured_timeout_sec": get_scenario_stage_timeout("role_plan"),
        "timeout_stage_policy_name": scenario_timeout_policy_name("role_plan"),
        "timed_out": False,
        "timeout_retry_attempted": False,
        "response_was_empty_after_timeout": False,
        "role_plan_primary_mismatch_segments": [],
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
            "identity_doctrine": _normalize_text(story_core.get("identity_doctrine"), max_len=1200),
            "identity_lock": _safe_dict(story_core.get("identity_lock")),
            "world_lock": _safe_dict(story_core.get("world_lock")),
            "style_lock": _safe_dict(story_core.get("style_lock")),
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
        "assigned_roles": _safe_dict(package.get("assigned_roles")),
        "connected_refs_summary": _safe_dict(_safe_dict(input_pkg.get("connected_context_summary"))),
        "entity_registry_sources": ["input.refs_by_role", "refs_inventory", "assigned_roles", "connected_context_summary"],
    }

    prompt = _build_roles_prompt(context)
    attempts = 2
    last_error = ROLES_SCHEMA_INVALID
    configured_timeout = get_scenario_stage_timeout("role_plan")

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
                timeout=configured_timeout,
            )
            if isinstance(response, dict) and response.get("__http_error__"):
                raise RuntimeError(f"gemini_http_error:{response.get('status')}:{response.get('text')}")

            raw_model_text = _extract_gemini_text(response)
            diagnostics["raw_model_response_preview"] = _normalize_text(raw_model_text, max_len=500)
            parsed = _extract_strict_json_payload(raw_model_text)
            diagnostics["parsed_payload_preview"] = _normalize_text(json.dumps(parsed, ensure_ascii=False), max_len=500)
            sanitized, dropped_fields = _cleanup_roles_payload(parsed)
            sanitized = _ensure_core_entities_in_roster(
                roles_payload=sanitized,
                core_subject_map=core_subject_map,
                allowed_registry=allowed_registry,
            )
            sanitized, technical_leaks_sanitized = _sanitize_role_plan_payload_fields(sanitized)
            sanitized, focal_rewrites_applied = _rewrite_allowed_focal_phrases(sanitized)
            if focal_rewrites_applied:
                diagnostics["false_positive_technical_leak_allowed"] = True
                diagnostics["allowed_technical_token"] = "focal"
                diagnostics["allowed_technical_phrase"] = "focal point"
            if technical_leaks_sanitized:
                diagnostics["false_positive_technical_leak_allowed"] = True
                diagnostics["allowed_technical_token"] = "identity_reference_wording"
                diagnostics["allowed_technical_phrase"] = "story_facing_sanitized"
            diagnostics["dropped_non_canonical_fields"] = dropped_fields[:60]
            normalized_scene_casting, primary_mismatches = _normalize_scene_casting_from_core(
                scene_casting=[_safe_dict(row) for row in _safe_list(sanitized.get("scene_casting"))],
                core_subject_map=core_subject_map,
            )
            sanitized["scene_casting"] = normalized_scene_casting
            diagnostics["role_plan_primary_mismatch_segments"] = primary_mismatches[:40]
            diagnostics["sanitized_payload_preview"] = _normalize_text(json.dumps(sanitized, ensure_ascii=False), max_len=500)
            seen_segment_ids = [
                str(_safe_dict(row).get("segment_id") or "").strip()
                for row in _safe_list(sanitized.get("scene_casting"))
                if str(_safe_dict(row).get("segment_id") or "").strip()
            ]
            expected_set = set(expected_segment_ids)
            seen_set = set(seen_segment_ids)
            diagnostics["coverage_seen_segment_ids"] = seen_segment_ids
            diagnostics["coverage_missing_segment_ids"] = sorted(list(expected_set - seen_set))
            diagnostics["coverage_extra_segment_ids"] = sorted(list(seen_set - expected_set))
            if primary_mismatches and attempt < attempts - 1:
                last_error = ROLE_PRIMARY_MISMATCH
                diagnostics["error_code"] = ROLE_PRIMARY_MISMATCH
                prompt = (
                    f"{prompt}\n\nPREVIOUS_VALIDATION_ERROR={ROLE_PRIMARY_MISMATCH}. "
                    "Do not change primary_role/secondary_roles from CORE beat_map. "
                    "Only refine presence_mode, presence_weight, performance_focus, continuity_notes."
                )
                continue
            normalized, validation_error = _validate_roles_payload(
                roles_payload=sanitized,
                expected_segment_ids=expected_segment_ids,
                allowed_registry=allowed_registry,
            )
            if validation_error:
                last_error = validation_error
                diagnostics["error_code"] = validation_error
                if validation_error in {ROLES_TECHNICAL_LEAKING, ROLES_CREATIVE_ROUTE, ROLES_ACTION_LEAKING}:
                    leak_code, leak_path, leak_token = _extract_leakage_details(sanitized)
                    diagnostics["technical_leak_trigger"] = leak_code or validation_error
                    diagnostics["technical_leak_field"] = leak_path
                    diagnostics["technical_leak_token"] = leak_token
                if attempt < attempts - 1:
                    if validation_error == ROLES_TECHNICAL_LEAKING:
                        prompt = (
                            f"{prompt}\n\nPREVIOUS_VALIDATION_ERROR={validation_error}. "
                            "Your previous output leaked technical reference/source wording. "
                            "Rewrite the same role_plan in story-facing language only, preserve all segment_ids exactly, "
                            "and avoid reference/source/API/package terms."
                        )
                    else:
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
                    "coverage_seen_segment_ids": seen_segment_ids,
                    "coverage_missing_segment_ids": sorted(list(expected_set - seen_set)),
                    "coverage_extra_segment_ids": sorted(list(seen_set - expected_set)),
                    "normalized_role_plan_preview": _normalize_text(json.dumps(normalized, ensure_ascii=False), max_len=500),
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
            if is_timeout_error(last_error):
                diagnostics["timed_out"] = True
                diagnostics["error_code"] = "role_plan_timeout"
                diagnostics["response_was_empty_after_timeout"] = not bool(
                    str(diagnostics.get("raw_model_response_preview") or "").strip()
                )
                diagnostics["timeout_retry_attempted"] = attempt < attempts - 1
                last_error = "role_plan_timeout"
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
