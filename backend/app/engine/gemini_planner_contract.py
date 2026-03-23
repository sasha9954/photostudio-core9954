from __future__ import annotations

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.engine.audio_first_planner import (
    AudioFirstPlannerOutput,
    AudioPlanningContext,
    AudioSegmentType,
    InputMode,
    LipSyncPolicy,
    LtxRenderTask,
    NarrationMode,
    PlannedScene,
    PlannedShot,
    PlannerValidation,
    ProjectMode,
    ProjectPlanningInput,
    RenderMode,
    build_audio_planning_context,
    validate_project_input,
)


class GeminiPlanningStatus(str, Enum):
    ok = "ok"
    blocked = "blocked"
    invalid = "invalid"


class GeminiPlannerShotFrameSource(str, Enum):
    new = "new"
    previous_last_frame = "previous_last_frame"
    provided_frame = "provided_frame"


class GeminiPlannerAudioSegmentType(str, Enum):
    narration = "narration"
    music = "music"
    music_vocal = "music_vocal"
    local_phrase = "local_phrase"
    sfx_accent = "sfx_accent"
    music_bed = "music_bed"
    unknown = "unknown"


class GeminiPlannerLipSyncPolicy(BaseModel):
    allowed: bool = False
    reason: str = "lip_sync_not_evaluated"


class GeminiPlannerShot(BaseModel):
    model_config = ConfigDict(extra="allow")

    shot_id: str
    summary: str
    start_sec: float
    end_sec: float
    duration_sec: float
    shot_type: str | None = None
    framing: str | None = None
    render_mode: RenderMode
    render_reason: str
    motion_interpretation: str
    audio_segment_type: GeminiPlannerAudioSegmentType
    has_vocal_rhythm: bool = False
    start_frame_source: GeminiPlannerShotFrameSource | None = None
    parent_shot_id: str | None = None
    needs_two_frames: bool = False
    local_phrase_text: str = ""
    lipsync_policy: GeminiPlannerLipSyncPolicy

    @model_validator(mode="after")
    def validate_timing(self) -> "GeminiPlannerShot":
        if self.start_sec < 0 or self.end_sec < 0 or self.duration_sec < 0:
            raise ValueError("shot_timing_must_be_non_negative")
        if self.end_sec < self.start_sec:
            raise ValueError("shot_end_sec_must_be_greater_than_or_equal_to_start_sec")
        expected_duration = round(self.end_sec - self.start_sec, 3)
        if abs(expected_duration - round(self.duration_sec, 3)) > 0.05:
            raise ValueError("shot_duration_must_match_start_end")
        return self


class GeminiPlannerScene(BaseModel):
    model_config = ConfigDict(extra="allow")

    scene_id: str
    scene_mode: str
    summary: str
    start_sec: float
    end_sec: float
    duration_sec: float
    narration_mode: NarrationMode
    audio_segment_type: GeminiPlannerAudioSegmentType
    continuation_from_prev: bool = False
    shots: list[GeminiPlannerShot] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_timing(self) -> "GeminiPlannerScene":
        if self.start_sec < 0 or self.end_sec < 0 or self.duration_sec < 0:
            raise ValueError("scene_timing_must_be_non_negative")
        if self.end_sec < self.start_sec:
            raise ValueError("scene_end_sec_must_be_greater_than_or_equal_to_start_sec")
        expected_duration = round(self.end_sec - self.start_sec, 3)
        if abs(expected_duration - round(self.duration_sec, 3)) > 0.05:
            raise ValueError("scene_duration_must_match_start_end")
        return self


class GeminiPlannerOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    contract_version: str
    schema_version: str | None = None
    planner_contract_name: str | None = None
    planner_source: str | None = None
    project_mode: ProjectMode
    input_mode: InputMode
    planning_status: GeminiPlanningStatus
    planning_block_reason: str | None = None
    debug_summary: str | None = None
    scenes: list[GeminiPlannerScene]


class GeminiPlannerParseResult(BaseModel):
    ok: bool
    parsed: GeminiPlannerOutput | None = None
    raw_payload: dict[str, Any] | None = None
    raw_text: str | None = None
    parse_mode: str = "invalid"
    contract_schema_valid: bool = False
    contract_validation_errors: list[str] = Field(default_factory=list)
    contract_validation_warnings: list[str] = Field(default_factory=list)
    raw_gemini_contract_version: str | None = None
    raw_gemini_schema_version: str | None = None
    parser_notes: list[str] = Field(default_factory=list)
    parser_debug_summary: str | None = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GeminiPlannerValidationReport(BaseModel):
    valid: bool = True
    blocked: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class GeminiPlannerInputPackage(BaseModel):
    model_config = ConfigDict(extra="allow")

    contract_version: str = "gemini_audio_first_planner_v1"
    schema_version: str = "gemini_audio_first_planner_schema_v1"
    planner_contract_name: str = "audio_first_gemini_planner_contract"
    planner_source: str = "gemini"
    mode: str = "clip"
    planner_mode: str = "gemini_only"
    input_mode: InputMode
    project_mode: ProjectMode
    story_text: str | None = None
    master_audio_url: str | None = None
    transcript_text: str | None = None
    lyrics_text: str | None = None
    spoken_text_hint: str | None = None
    narrative_source: str | None = None
    timeline_source: str | None = None
    global_music_track_url: str | None = None
    refs_by_role: dict[str, list[dict[str, str]]] = Field(default_factory=dict)
    role_type_by_role: dict[str, str] = Field(default_factory=dict)
    role_mode: str = "auto"
    style_preset: str | None = None
    genre: str | None = None
    story_control_mode: str | None = None
    story_mission_summary: str | None = None
    planner_rules: dict[str, Any] = Field(default_factory=dict)
    planner_overrides: dict[str, Any] = Field(default_factory=dict)
    production_canon: dict[str, Any] = Field(default_factory=dict)
    story_context: dict[str, Any] = Field(default_factory=dict)
    world_lock: dict[str, Any] = Field(default_factory=dict)
    entity_locks: dict[str, Any] = Field(default_factory=dict)
    optional_audio_cues: dict[str, Any] = Field(default_factory=dict)


class GeminiContractExecutionResult(BaseModel):
    canonical_output: AudioFirstPlannerOutput
    parsed_output: GeminiPlannerOutput | None = None
    validation_report: GeminiPlannerValidationReport = Field(default_factory=GeminiPlannerValidationReport)
    planner_input: GeminiPlannerInputPackage
    compatibility_scenes: list[dict[str, Any]] = Field(default_factory=list)


PRODUCTION_CANON_RULES = {
    "audio_first": {
        "enabled": True,
        "timing_source_of_truth": "master_audio",
        "text_without_master_audio_is_not_final_timing": True,
    },
    "input_mode_rules": {
        "text_to_audio_first_requires_master_audio": True,
        "blocked_reason": "planning_blocked_until_master_audio_exists",
    },
    "lip_sync": {
        "allowed_only_when": [
            "music_driven_scene",
            "vocal_present",
            "rhythmic_support_present",
            "framing_supports_lip_sync",
        ],
        "forbidden_when": [
            "narration_segment",
            "local_phrase_segment",
            "missing_vocal_rhythm",
            "lipsync_policy_disallows",
        ],
    },
    "local_phrases": {
        "allowed_in_ltx_scene_audio": True,
        "do_not_imply_lip_sync": True,
    },
    "motion_modes": {
        "i2v_as_and_f_l_as": "audio_sensitive_motion_not_speech_articulation",
    },
    "render_modes": [mode.value for mode in RenderMode],
    "narration_modes": [mode.value for mode in NarrationMode],
    "project_modes": [mode.value for mode in ProjectMode],
    "elevenlabs": {
        "allowed_usage": "full_master_narration_only",
        "forbidden_usage": "short_scene_local_phrases",
    },
    "music_layer": {
        "global_user_music_track": True,
        "no_suno_hard_dependency": True,
    },
}

GEMINI_PLANNER_CONTRACT_VERSION = "gemini_audio_first_planner_v1"
GEMINI_PLANNER_SCHEMA_VERSION = "gemini_audio_first_planner_schema_v1"
GEMINI_PLANNER_CONTRACT_NAME = "audio_first_gemini_planner_contract"


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _round_sec(value: Any) -> float:
    return round(float(value or 0.0), 3)


def _normalize_refs_by_role(value: Any) -> dict[str, list[dict[str, str]]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, list[dict[str, str]]] = {}
    for role, items in value.items():
        if not isinstance(items, list):
            continue
        cleaned_items: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = _clean_str(item.get("url"))
            if not url:
                continue
            cleaned_item = {"url": url, "name": _clean_str(item.get("name"))}
            role_type = _clean_str(item.get("roleType")).lower()
            if role_type:
                cleaned_item["roleType"] = role_type
            cleaned_items.append(cleaned_item)
        if cleaned_items:
            normalized[_clean_str(role)] = cleaned_items
    return normalized


def build_gemini_planner_input(
    normalized: dict[str, Any],
    project_input: ProjectPlanningInput,
    *,
    story_context: dict[str, Any] | None = None,
    world_lock: dict[str, Any] | None = None,
    entity_locks: dict[str, Any] | None = None,
    optional_audio_cues: dict[str, Any] | None = None,
) -> GeminiPlannerInputPackage:
    canon_rules = {
        **PRODUCTION_CANON_RULES,
        "planner_rules": project_input.planner_rules,
        "planner_overrides": project_input.planner_overrides,
    }
    return GeminiPlannerInputPackage(
        contract_version=GEMINI_PLANNER_CONTRACT_VERSION,
        schema_version=GEMINI_PLANNER_SCHEMA_VERSION,
        planner_contract_name=GEMINI_PLANNER_CONTRACT_NAME,
        mode=_clean_str(normalized.get("mode") or "clip") or "clip",
        planner_mode=_clean_str(normalized.get("plannerMode") or "gemini_only") or "gemini_only",
        input_mode=project_input.input_mode,
        project_mode=project_input.project_mode,
        story_text=project_input.story_text,
        master_audio_url=project_input.master_audio_url,
        transcript_text=_clean_str(normalized.get("transcriptText")) or None,
        lyrics_text=_clean_str(normalized.get("lyricsText")) or None,
        spoken_text_hint=_clean_str(normalized.get("spokenTextHint")) or None,
        narrative_source=_clean_str(normalized.get("narrativeSource")) or None,
        timeline_source=_clean_str(normalized.get("timelineSource")) or None,
        global_music_track_url=project_input.global_music_track_url,
        refs_by_role=_normalize_refs_by_role(normalized.get("refsByRole") or project_input.refs),
        role_type_by_role=normalized.get("roleTypeByRole") if isinstance(normalized.get("roleTypeByRole"), dict) else {},
        role_mode=_clean_str(normalized.get("roleMode") or "auto") or "auto",
        style_preset=_clean_str(normalized.get("stylePreset")) or None,
        genre=_clean_str(normalized.get("genre")) or None,
        story_control_mode=_clean_str(normalized.get("storyControlMode")) or None,
        story_mission_summary=_clean_str(normalized.get("storyMissionSummary")) or None,
        planner_rules=project_input.planner_rules,
        planner_overrides=project_input.planner_overrides,
        production_canon=canon_rules,
        story_context=story_context or {},
        world_lock=world_lock or {},
        entity_locks=entity_locks or {},
        optional_audio_cues=optional_audio_cues or {},
    )


def _build_gemini_planner_output_contract_dict(planner_input: GeminiPlannerInputPackage) -> dict[str, Any]:
    return {
        "contract_version": planner_input.contract_version,
        "schema_version": planner_input.schema_version,
        "planner_contract_name": planner_input.planner_contract_name,
        "planner_source": planner_input.planner_source,
        "project_mode": "narration_first|music_first|hybrid",
        "input_mode": "audio_first|text_to_audio_first",
        "planning_status": "ok|blocked|invalid",
        "planning_block_reason": "string|null",
        "debug_summary": "string|null",
        "scenes": [
            {
                "scene_id": "scene_001",
                "scene_mode": "string",
                "summary": "string",
                "start_sec": 0.0,
                "end_sec": 4.2,
                "duration_sec": 4.2,
                "narration_mode": "full|duck|pause",
                "audio_segment_type": "narration|music|music_vocal|local_phrase|sfx_accent|music_bed|unknown",
                "continuation_from_prev": False,
                "shots": [
                    {
                        "shot_id": "scene_001__shot_001",
                        "summary": "string",
                        "start_sec": 0.0,
                        "end_sec": 2.1,
                        "duration_sec": 2.1,
                        "shot_type": "string",
                        "framing": "string",
                        "render_mode": "i2v|i2v_as|f_l|f_l_as|continuation|lip_sync",
                        "render_reason": "string",
                        "motion_interpretation": "string",
                        "audio_segment_type": "narration|music|music_vocal|local_phrase|sfx_accent|music_bed|unknown",
                        "has_vocal_rhythm": False,
                        "start_frame_source": "new|previous_last_frame|provided_frame|null",
                        "parent_shot_id": None,
                        "needs_two_frames": False,
                        "local_phrase_text": "",
                        "lipsync_policy": {"allowed": False, "reason": "string"},
                    }
                ],
            }
        ],
    }


def build_gemini_planner_system_rules(planner_input: GeminiPlannerInputPackage) -> str:
    return (
        "You are the central Audio-first Gemini planner for COMFY clip planning.\n"
        "Return exactly one JSON object and no markdown.\n"
        "Use the structured production canon in planner_input.production_canon as hard rules, not suggestions.\n"
        f"Planner source is '{planner_input.planner_source}'. Contract name is '{planner_input.planner_contract_name}'.\n"
        f"Contract version must be '{planner_input.contract_version}' and schema_version must be '{planner_input.schema_version}'.\n"
        "Do scene analysis, timing segmentation, render-mode selection, and shot planning yourself.\n"
        "Audio-first means master audio is the timing source of truth.\n"
        "If input_mode is text_to_audio_first and master_audio_url is missing, return planning_status='blocked' and no invented timing.\n"
        "Narration must not use lip_sync. Local phrase text must not imply lip_sync.\n"
        "lip_sync is only for music-driven vocal + rhythm scenes with framing that supports visible articulation.\n"
        "i2v_as and f_l_as are audio-sensitive motion, not speech articulation.\n"
        "Populate every timing field consistently and keep shot timing inside its scene timing.\n"
        "CHARACTER ROLE LOGIC:\n"
        "- planner_input.role_mode is either 'auto' or 'locked'. planner_input.role_type_by_role contains the active role mapping.\n"
        "- If role_mode='locked': hero is the main narrative subject, antagonist is the source of conflict/tension/opposition, and support assists/reacts/accompanies.\n"
        "- If role_mode='locked': build scenes around the hero perspective, introduce interaction between hero and antagonist, and maintain role consistency across scenes.\n"
        "- If role_mode='locked' and an antagonist exists, at least one scene must show conflict, tension, or opposition, and the antagonist must influence story progression.\n"
        "- If role_mode='auto': you may assign roles dynamically, but do not invent extreme conflict without justification.\n"
        "ROLE CONSISTENCY RULE:\n"
        "- Never swap roles across scenes.\n"
        "- Hero must remain hero in all scenes.\n"
        "- Antagonist must not become hero.\n"
        "- Support must not override hero as the main subject.\n"
        "SCENE ROLE DISTRIBUTION:\n"
        "- Hero should appear in the majority of scenes.\n"
        "- Antagonist should appear in key tension scenes.\n"
        "- Support should appear in context or interaction scenes."
    )


def build_gemini_planner_output_contract(planner_input: GeminiPlannerInputPackage) -> str:
    output_contract = _build_gemini_planner_output_contract_dict(planner_input)
    return (
        "Expected output contract.\n"
        "Return one top-level JSON object with these fields and enum domains.\n"
        f"{json.dumps(output_contract, ensure_ascii=False, indent=2)}"
    )


def build_gemini_planner_runtime_payload(planner_input: GeminiPlannerInputPackage) -> str:
    runtime_payload = {
        "planner_source": planner_input.planner_source,
        "planner_contract_name": planner_input.planner_contract_name,
        "contract_version": planner_input.contract_version,
        "schema_version": planner_input.schema_version,
        "planner_input": planner_input.model_dump(mode="json", exclude_none=True),
    }
    return json.dumps(runtime_payload, ensure_ascii=False, indent=2)


def build_gemini_planner_request_text(planner_input: GeminiPlannerInputPackage) -> str:
    system_rules = build_gemini_planner_system_rules(planner_input)
    output_contract = build_gemini_planner_output_contract(planner_input)
    runtime_payload = build_gemini_planner_runtime_payload(planner_input)
    return (
        "=== GEMINI PLANNER SYSTEM RULES ===\n"
        f"{system_rules}\n\n"
        "=== GEMINI PLANNER OUTPUT CONTRACT ===\n"
        f"{output_contract}\n\n"
        "=== GEMINI PLANNER RUNTIME PAYLOAD ===\n"
        f"{runtime_payload}"
    )


def parse_gemini_planner_output(raw_output: str | dict[str, Any] | GeminiPlannerOutput) -> GeminiPlannerParseResult:
    if isinstance(raw_output, GeminiPlannerOutput):
        payload = raw_output.model_dump(mode="json")
        return GeminiPlannerParseResult(
            ok=True,
            parsed=raw_output,
            raw_payload=payload,
            raw_text=raw_output.model_dump_json(indent=2),
            parse_mode="strict",
            contract_schema_valid=True,
            raw_gemini_contract_version=_clean_str(payload.get("contract_version")) or None,
            raw_gemini_schema_version=_clean_str(payload.get("schema_version")) or None,
            parser_debug_summary="strict_json:model_instance",
        )

    payload: dict[str, Any] | None = None
    parse_mode = "strict"
    parser_notes: list[str] = []
    raw_text: str | None = None
    if isinstance(raw_output, dict):
        payload = raw_output
    else:
        raw_text = str(raw_output or "").strip()
        if not raw_text:
            return GeminiPlannerParseResult(
                ok=False,
                raw_text=raw_text,
                parse_mode="invalid",
                errors=["gemini_contract_empty_output"],
                contract_validation_errors=["gemini_contract_empty_output"],
                parser_debug_summary="invalid:empty_output",
            )
        try:
            payload = json.loads(raw_text)
        except Exception:
            parse_mode = "rescued_json"
            start = raw_text.find("{")
            end = raw_text.rfind("}")
            if start >= 0 and end > start:
                try:
                    payload = json.loads(raw_text[start:end + 1])
                    parser_notes.append("parser_rescued_json_substring")
                except Exception:
                    payload = None
                    parser_notes.append("parser_rescue_substring_json_decode_failed")
            else:
                parser_notes.append("parser_rescue_brace_window_not_found")
    if not isinstance(payload, dict):
        return GeminiPlannerParseResult(
            ok=False,
            raw_text=raw_text,
            parse_mode="invalid",
            errors=["gemini_contract_invalid_json"],
            warnings=parser_notes,
            contract_validation_errors=["gemini_contract_invalid_json"],
            parser_notes=parser_notes,
            parser_debug_summary="invalid:json_parse_failed",
        )
    if isinstance(payload.get("errors"), list) and not any(key in payload for key in ["contract_version", "project_mode", "input_mode", "planning_status", "scenes"]):
        upstream_errors = [str(item).strip() for item in payload.get("errors") if str(item).strip()]
        return GeminiPlannerParseResult(
            ok=False,
            raw_payload=payload,
            raw_text=raw_text,
            parse_mode="invalid",
            errors=upstream_errors or ["gemini_contract_upstream_error"],
            warnings=parser_notes,
            contract_validation_errors=upstream_errors or ["gemini_contract_upstream_error"],
            raw_gemini_contract_version=_clean_str(payload.get("contract_version")) or None,
            raw_gemini_schema_version=_clean_str(payload.get("schema_version")) or None,
            parser_notes=parser_notes,
            parser_debug_summary="invalid:upstream_error_payload",
        )

    try:
        parsed = GeminiPlannerOutput.model_validate(payload)
    except ValidationError as exc:
        errors = []
        for err in exc.errors():
            path = ".".join(str(part) for part in err.get("loc") or [])
            msg = str(err.get("msg") or "validation error")
            errors.append(f"gemini_contract_schema_error:{path}:{msg}")
        return GeminiPlannerParseResult(
            ok=False,
            raw_payload=payload,
            raw_text=raw_text,
            parse_mode="invalid",
            errors=errors,
            warnings=parser_notes,
            contract_schema_valid=False,
            contract_validation_errors=errors,
            raw_gemini_contract_version=_clean_str(payload.get("contract_version")) or None,
            raw_gemini_schema_version=_clean_str(payload.get("schema_version")) or None,
            parser_notes=parser_notes,
            parser_debug_summary=f"invalid:schema_errors:{len(errors)}",
        )

    parser_debug_summary = "strict_json:contract_valid" if parse_mode == "strict" else "rescued_json:contract_valid"
    return GeminiPlannerParseResult(
        ok=True,
        parsed=parsed,
        raw_payload=payload,
        raw_text=raw_text,
        parse_mode=parse_mode,
        contract_schema_valid=True,
        contract_validation_warnings=parser_notes if parse_mode == "rescued_json" else [],
        raw_gemini_contract_version=_clean_str(payload.get("contract_version")) or None,
        raw_gemini_schema_version=_clean_str(payload.get("schema_version")) or None,
        parser_notes=parser_notes,
        warnings=parser_notes,
        parser_debug_summary=parser_debug_summary,
    )


def _build_contract_debug_info(
    planner_input: GeminiPlannerInputPackage,
    parsed_output: GeminiPlannerOutput | None,
    validation_errors: list[str],
    validation_warnings: list[str],
    parse_result: GeminiPlannerParseResult | None,
    *,
    raw_payload: dict[str, Any] | None = None,
    raw_debug_summary: str | None = None,
) -> dict[str, Any]:
    raw_contract_version = (
        parse_result.raw_gemini_contract_version
        if parse_result and parse_result.raw_gemini_contract_version
        else (parsed_output.contract_version if parsed_output else planner_input.contract_version)
    )
    raw_schema_version = (
        parse_result.raw_gemini_schema_version
        if parse_result and parse_result.raw_gemini_schema_version
        else (parsed_output.schema_version if parsed_output and parsed_output.schema_version else planner_input.schema_version)
    )
    raw_contract_name = (
        parsed_output.planner_contract_name
        if parsed_output and parsed_output.planner_contract_name
        else planner_input.planner_contract_name
    )
    raw_planner_source = (
        parsed_output.planner_source
        if parsed_output and parsed_output.planner_source
        else planner_input.planner_source
    )
    return {
        "planner_source": planner_input.planner_source,
        "plannerSource": planner_input.planner_source,
        "planner_contract_name": planner_input.planner_contract_name,
        "plannerContractName": planner_input.planner_contract_name,
        "contract_version": planner_input.contract_version,
        "contractVersion": planner_input.contract_version,
        "schema_version": planner_input.schema_version,
        "schemaVersion": planner_input.schema_version,
        "contract_parse_mode": parse_result.parse_mode if parse_result else "invalid",
        "parse_mode": parse_result.parse_mode if parse_result else "invalid",
        "parseMode": parse_result.parse_mode if parse_result else "invalid",
        "contract_schema_valid": parse_result.contract_schema_valid if parse_result else False,
        "contractSchemaValid": parse_result.contract_schema_valid if parse_result else False,
        "parser_notes": parse_result.parser_notes if parse_result else [],
        "parserNotes": parse_result.parser_notes if parse_result else [],
        "parser_debug_summary": parse_result.parser_debug_summary if parse_result else "invalid:missing_parse_result",
        "parserDebugSummary": parse_result.parser_debug_summary if parse_result else "invalid:missing_parse_result",
        "validation_errors": list(validation_errors),
        "validationErrors": list(validation_errors),
        "validation_warnings": list(validation_warnings),
        "validationWarnings": list(validation_warnings),
        "contract_validation_errors": parse_result.contract_validation_errors if parse_result else list(validation_errors),
        "contractValidationErrors": parse_result.contract_validation_errors if parse_result else list(validation_errors),
        "contract_validation_warnings": parse_result.contract_validation_warnings if parse_result else list(validation_warnings),
        "contractValidationWarnings": parse_result.contract_validation_warnings if parse_result else list(validation_warnings),
        "raw_gemini_contract_version": raw_contract_version,
        "rawGeminiContractVersion": raw_contract_version,
        "raw_gemini_schema_version": raw_schema_version,
        "rawGeminiSchemaVersion": raw_schema_version,
        "raw_gemini_contract_name": raw_contract_name,
        "rawGeminiContractName": raw_contract_name,
        "raw_gemini_planner_source": raw_planner_source,
        "rawGeminiPlannerSource": raw_planner_source,
        "raw_gemini_debug_summary": raw_debug_summary or (parsed_output.debug_summary if parsed_output else None),
        "rawGeminiDebugSummary": raw_debug_summary or (parsed_output.debug_summary if parsed_output else None),
        "raw_gemini_payload": raw_payload or (parsed_output.model_dump(mode="json") if parsed_output else {}),
        "rawGeminiPayload": raw_payload or (parsed_output.model_dump(mode="json") if parsed_output else {}),
        "canonical_source_of_truth": True,
        "canonicalSourceOfTruth": True,
        "returns_compatibility_projection": True,
        "returnsCompatibilityProjection": True,
        "canonical_is_projection": False,
        "canonicalIsProjection": False,
        "compatibility_projection": False,
        "compatibilityProjection": False,
    }


def _parse_validation_message(message: Any) -> dict[str, str]:
    raw = str(message or "").strip()
    if not raw:
        return {"level": "", "path": "", "message": ""}
    parts = raw.split(":")
    if len(parts) >= 3 and parts[0].startswith("gemini_contract_"):
        return {"level": parts[0], "path": parts[1], "message": ":".join(parts[2:]).strip()}
    if len(parts) >= 2:
        return {"level": "", "path": parts[0].strip(), "message": ":".join(parts[1:]).strip()}
    return {"level": "", "path": "", "message": raw}


def _path_matches_message(target_path: str, message_path: str) -> bool:
    normalized_target = _clean_str(target_path)
    normalized_message = _clean_str(message_path)
    if not normalized_target or not normalized_message:
        return False
    if normalized_message == normalized_target:
        return True
    if normalized_message.startswith(f"{normalized_target}.") or normalized_message.startswith(f"{normalized_target}:"):
        return True
    if normalized_target.startswith("scene_") and "__shot_" in normalized_message:
        scene_prefix, _, remainder = normalized_message.partition("__shot_")
        if scene_prefix == normalized_target and remainder:
            return True
    return False


def _convert_audio_segment_type(value: GeminiPlannerAudioSegmentType) -> AudioSegmentType:
    mapping = {
        GeminiPlannerAudioSegmentType.narration: AudioSegmentType.narration,
        GeminiPlannerAudioSegmentType.music: AudioSegmentType.music,
        GeminiPlannerAudioSegmentType.music_vocal: AudioSegmentType.music_vocal,
        GeminiPlannerAudioSegmentType.local_phrase: AudioSegmentType.local_phrase,
        GeminiPlannerAudioSegmentType.sfx_accent: AudioSegmentType.sfx_pulse,
        GeminiPlannerAudioSegmentType.music_bed: AudioSegmentType.music,
        GeminiPlannerAudioSegmentType.unknown: AudioSegmentType.unknown,
    }
    return mapping.get(value, AudioSegmentType.unknown)


def validate_gemini_planner_output(
    planner_input: GeminiPlannerInputPackage,
    parsed_output: GeminiPlannerOutput | None,
) -> GeminiPlannerValidationReport:
    project_input = ProjectPlanningInput(
        input_mode=planner_input.input_mode,
        project_mode=planner_input.project_mode,
        story_text=planner_input.story_text,
        master_audio_url=planner_input.master_audio_url,
        global_music_track_url=planner_input.global_music_track_url,
        refs=planner_input.refs_by_role,
        planner_rules=planner_input.planner_rules,
        planner_overrides=planner_input.planner_overrides,
    )
    base_context = build_audio_planning_context(project_input)
    base_validation = validate_project_input(project_input, base_context)
    errors = list(base_validation.errors)
    warnings = list(base_validation.warnings)
    blocked = base_validation.blocked

    if parsed_output is None:
        errors.append("gemini_contract_missing_parsed_output")
        return GeminiPlannerValidationReport(valid=False, blocked=blocked, errors=list(dict.fromkeys(errors)), warnings=list(dict.fromkeys(warnings)))

    if parsed_output.project_mode != planner_input.project_mode:
        warnings.append("gemini_project_mode_mismatch_with_request")
    if parsed_output.input_mode != planner_input.input_mode:
        warnings.append("gemini_input_mode_mismatch_with_request")
    if parsed_output.planning_status == GeminiPlanningStatus.ok and not parsed_output.scenes:
        errors.append("ok_planning_status_requires_non_empty_scenes")

    if planner_input.input_mode == InputMode.text_to_audio_first and not planner_input.master_audio_url:
        blocked = True
        if parsed_output.planning_status != GeminiPlanningStatus.blocked:
            errors.append("text_to_audio_first_without_master_audio_must_be_blocked")

    if parsed_output.planning_status == GeminiPlanningStatus.blocked and not _clean_str(parsed_output.planning_block_reason):
        errors.append("blocked_planning_status_requires_planning_block_reason")
    if parsed_output.planning_status == GeminiPlanningStatus.invalid and parsed_output.scenes:
        warnings.append("invalid_planning_status_returned_with_scenes")

    previous_shot_id: str | None = None
    for scene_idx, scene in enumerate(parsed_output.scenes):
        scene_label = scene.scene_id or f"scene_{scene_idx + 1:03d}"
        if not scene.shots and parsed_output.planning_status == GeminiPlanningStatus.ok:
            errors.append(f"{scene_label}:scene_requires_at_least_one_shot")
        if abs(round(scene.end_sec - scene.start_sec, 3) - round(scene.duration_sec, 3)) > 0.05:
            errors.append(f"{scene_label}:scene_duration_inconsistent")
        if scene.audio_segment_type == GeminiPlannerAudioSegmentType.narration:
            for shot in scene.shots:
                if shot.render_mode == RenderMode.lip_sync:
                    errors.append(f"{scene_label}:{shot.shot_id}:narration_cannot_use_lip_sync")

        for shot_idx, shot in enumerate(scene.shots):
            shot_label = shot.shot_id or f"{scene_label}__shot_{shot_idx + 1:03d}"
            if shot.start_sec < scene.start_sec - 0.05 or shot.end_sec > scene.end_sec + 0.05:
                errors.append(f"{shot_label}:shot_timing_outside_scene_window")
            if abs(round(shot.end_sec - shot.start_sec, 3) - round(shot.duration_sec, 3)) > 0.05:
                errors.append(f"{shot_label}:shot_duration_inconsistent")
            if shot.duration_sec <= 0:
                errors.append(f"{shot_label}:shot_duration_must_be_positive")
            if shot.render_mode == RenderMode.lip_sync and shot.has_vocal_rhythm is not True:
                errors.append(f"{shot_label}:lip_sync_requires_has_vocal_rhythm_true")
            if shot.render_mode == RenderMode.lip_sync and shot.audio_segment_type == GeminiPlannerAudioSegmentType.narration:
                errors.append(f"{shot_label}:narration_segment_cannot_use_lip_sync")
            if shot.local_phrase_text and shot.render_mode == RenderMode.lip_sync:
                errors.append(f"{shot_label}:local_phrase_does_not_imply_lip_sync")
            if shot.local_phrase_text and shot.lipsync_policy.allowed is True:
                errors.append(f"{shot_label}:local_phrase_does_not_allow_lipsync_policy")
            if (
                planner_input.project_mode == ProjectMode.narration_first
                and shot.audio_segment_type == GeminiPlannerAudioSegmentType.music_vocal
                and not planner_input.global_music_track_url
                and scene.audio_segment_type != GeminiPlannerAudioSegmentType.music_vocal
            ):
                warnings.append(f"{shot_label}:music_vocal_without_music_context_in_narration_first")
            if (
                shot.render_mode == RenderMode.lip_sync
                and _clean_str(shot.framing).lower() in {"wide", "long", "long_shot", "wide_shot", "distant", "extreme_wide"}
            ):
                warnings.append(f"{shot_label}:lip_sync_framing_may_be_too_wide")
            if (
                scene.audio_segment_type == GeminiPlannerAudioSegmentType.narration
                and shot.audio_segment_type == GeminiPlannerAudioSegmentType.music_vocal
            ):
                warnings.append(f"{shot_label}:shot_audio_segment_conflicts_with_scene_audio_segment")
            if shot.render_mode == RenderMode.continuation:
                if not shot.parent_shot_id:
                    warnings.append(f"{shot_label}:continuation_missing_parent_shot_id")
                if shot.start_frame_source is None:
                    warnings.append(f"{shot_label}:continuation_missing_start_frame_source")
                if shot.start_frame_source not in {
                    GeminiPlannerShotFrameSource.previous_last_frame,
                    GeminiPlannerShotFrameSource.provided_frame,
                }:
                    warnings.append(f"{shot_label}:continuation_start_frame_source_should_reference_previous_or_provided_frame")
            if shot.render_mode in {RenderMode.f_l, RenderMode.f_l_as} and shot.needs_two_frames is not True:
                warnings.append(f"{shot_label}:f_l_modes_should_enable_needs_two_frames")
            if shot.render_mode == RenderMode.lip_sync and shot.lipsync_policy.allowed is not True:
                errors.append(f"{shot_label}:lipsync_policy_disallows_selected_render_mode")
            if scene.audio_segment_type == GeminiPlannerAudioSegmentType.narration and shot.render_mode == RenderMode.lip_sync:
                errors.append(f"{shot_label}:narration_scene_should_not_use_lip_sync")
            if shot.render_mode == RenderMode.continuation and shot.start_frame_source == GeminiPlannerShotFrameSource.new:
                warnings.append(f"{shot_label}:continuation_start_frame_source_should_reference_previous_frame")
            previous_shot_id = shot.shot_id or previous_shot_id

    unique_errors = list(dict.fromkeys(errors))
    unique_warnings = list(dict.fromkeys(warnings))
    return GeminiPlannerValidationReport(
        valid=len(unique_errors) == 0,
        blocked=blocked,
        errors=unique_errors,
        warnings=unique_warnings,
    )


def _build_render_task(shot: PlannedShot) -> LtxRenderTask:
    return LtxRenderTask(
        shot_id=shot.shot_id,
        production_mode=shot.render_mode,
        model="ltx",
        use_audio_as_driver=shot.render_mode in {RenderMode.i2v_as, RenderMode.f_l_as, RenderMode.lip_sync},
        audio_source_ref=shot.audio_driver,
        start_frame_source=shot.start_frame_source,
        end_frame_source="generated_end_frame" if shot.needs_two_frames else None,
        motion_interpretation=shot.motion_interpretation,
        constraints={
            "narration_mode": shot.narration_mode.value,
            "audio_segment_type": shot.audio_segment_type.value,
            "has_vocal_rhythm": shot.has_vocal_rhythm,
            "needs_two_frames": shot.needs_two_frames,
        },
        debug={
            "planner_source": "gemini",
            "render_reason": shot.render_reason,
            "motion_interpretation": shot.motion_interpretation,
            "audio_segment_type": shot.audio_segment_type.value,
            "lipsync_policy_reason": shot.lipsync_policy.reason,
            "summary": shot.shot_id,
        },
    )


def _resolve_audio_driver(audio_segment_type: AudioSegmentType, context: AudioPlanningContext) -> str | None:
    if audio_segment_type == AudioSegmentType.local_phrase:
        return "local_scene_phrase"
    if audio_segment_type in {AudioSegmentType.music, AudioSegmentType.music_vocal} and context.global_music_track_present:
        return "global_music_track"
    if context.master_narration_present:
        return "master_narration"
    return None


def _build_compatibility_scene(scene: PlannedScene) -> dict[str, Any]:
    first_shot = scene.shots[0] if scene.shots else None
    render_task = first_shot.render_task if first_shot else None
    return {
        "sceneId": scene.scene_id,
        "title": scene.summary or scene.scene_id,
        "sceneText": scene.summary,
        "sceneMeaning": scene.summary,
        "visualDescription": scene.summary,
        "startSec": scene.start_sec,
        "endSec": scene.end_sec,
        "durationSec": scene.duration_sec,
        "sceneMode": scene.scene_mode,
        "narrationMode": scene.narration_mode.value,
        "audioSegmentType": scene.audio_segment_type.value,
        "continuationFromPrev": scene.continuation_from_prev,
        "cameraType": first_shot.framing if first_shot else "",
        "shotType": first_shot.shot_type if first_shot else "",
        "renderMode": first_shot.render_mode.value if first_shot else "i2v",
        "renderReason": first_shot.render_reason if first_shot else "",
        "motionInterpretation": first_shot.motion_interpretation if first_shot else "",
        "hasVocalRhythm": bool(first_shot.has_vocal_rhythm) if first_shot else False,
        "localPhraseText": "",
        "startFrameSource": first_shot.start_frame_source if first_shot else None,
        "parentShotId": first_shot.parent_shot_id if first_shot else None,
        "needsTwoFrames": bool(first_shot.needs_two_frames) if first_shot else False,
        "lipsyncPolicy": first_shot.lipsync_policy.model_dump(mode="json") if first_shot else {"allowed": False, "reason": "missing_shot"},
        "validationErrors": list(first_shot.validation_errors) if first_shot else [],
        "validationWarnings": list(first_shot.validation_warnings) if first_shot else [],
        "renderTask": render_task.model_dump(mode="json") if render_task else None,
        "shots": [
            {
                "shotId": shot.shot_id,
                "summary": shot.render_reason,
                "startSec": shot.start_sec,
                "endSec": shot.end_sec,
                "durationSec": shot.duration_sec,
                "shotType": shot.shot_type,
                "framing": shot.framing,
                "renderMode": shot.render_mode.value,
                "renderReason": shot.render_reason,
                "motionInterpretation": shot.motion_interpretation,
                "audioSegmentType": shot.audio_segment_type.value,
                "hasVocalRhythm": shot.has_vocal_rhythm,
                "startFrameSource": shot.start_frame_source,
                "parentShotId": shot.parent_shot_id,
                "needsTwoFrames": shot.needs_two_frames,
                "narrationMode": shot.narration_mode.value,
                "validationErrors": shot.validation_errors,
                "validationWarnings": shot.validation_warnings,
                "lipsyncPolicy": shot.lipsync_policy.model_dump(mode="json"),
            }
            for shot in scene.shots
        ],
    }


def _collect_messages_for_path(messages: list[str], path: str) -> list[str]:
    matches: list[str] = []
    for message in messages:
        parsed_message = _parse_validation_message(message)
        if _path_matches_message(path, parsed_message.get("path") or ""):
            matches.append(str(message))
    return matches


def map_gemini_plan_to_canonical_audio_first_output(
    planner_input: GeminiPlannerInputPackage,
    parsed_output: GeminiPlannerOutput | None,
    validation_report: GeminiPlannerValidationReport,
    *,
    raw_payload: dict[str, Any] | None = None,
    raw_debug_summary: str | None = None,
    parse_result: GeminiPlannerParseResult | None = None,
) -> GeminiContractExecutionResult:
    project_input = ProjectPlanningInput(
        input_mode=planner_input.input_mode,
        project_mode=planner_input.project_mode,
        story_text=planner_input.story_text,
        master_audio_url=planner_input.master_audio_url,
        global_music_track_url=planner_input.global_music_track_url,
        refs=planner_input.refs_by_role,
        planner_rules=planner_input.planner_rules,
        planner_overrides=planner_input.planner_overrides,
    )
    context = build_audio_planning_context(project_input)
    validation = PlannerValidation(
        valid=validation_report.valid,
        blocked=validation_report.blocked,
        errors=list(validation_report.errors),
        warnings=list(validation_report.warnings),
    )

    if parsed_output is None or validation.blocked or parsed_output.planning_status in {GeminiPlanningStatus.blocked, GeminiPlanningStatus.invalid}:
        blocked_reason = (
            (parsed_output.planning_block_reason if parsed_output else None)
            or context.planning_blocked_reason
            or (validation.errors[0] if validation.errors else None)
        )
        if blocked_reason:
            context.planning_blocked_reason = blocked_reason
        canonical = AudioFirstPlannerOutput(
            input_mode=project_input.input_mode,
            project_mode=project_input.project_mode,
            planning_context=context,
            validation=validation,
            scenes=[],
            render_tasks=[],
            debug={
                **_build_contract_debug_info(
                    planner_input,
                    parsed_output,
                    validation.errors,
                    validation.warnings,
                    parse_result,
                    raw_payload=raw_payload,
                    raw_debug_summary=raw_debug_summary,
                ),
                "planning_status": parsed_output.planning_status.value if parsed_output else GeminiPlanningStatus.invalid.value,
                "planner_validation_errors": validation.errors,
                "planner_validation_warnings": validation.warnings,
            },
        )
        return GeminiContractExecutionResult(
            canonical_output=canonical,
            parsed_output=parsed_output,
            validation_report=validation_report,
            planner_input=planner_input,
            compatibility_scenes=[],
        )

    planned_scenes: list[PlannedScene] = []
    render_tasks: list[LtxRenderTask] = []
    compatibility_scenes: list[dict[str, Any]] = []

    for scene in parsed_output.scenes:
        planned_shots: list[PlannedShot] = []
        for shot in scene.shots:
            audio_segment_type = _convert_audio_segment_type(shot.audio_segment_type)
            lipsync_policy = LipSyncPolicy(
                allowed=shot.lipsync_policy.allowed,
                reason=shot.lipsync_policy.reason,
            )
            planned_shot = PlannedShot(
                shot_id=shot.shot_id,
                scene_id=scene.scene_id,
                start_sec=_round_sec(shot.start_sec),
                end_sec=_round_sec(shot.end_sec),
                duration_sec=_round_sec(shot.duration_sec),
                shot_type=shot.shot_type,
                framing=shot.framing,
                render_mode=shot.render_mode,
                render_reason=shot.render_reason,
                audio_segment_type=audio_segment_type,
                has_vocal_rhythm=shot.has_vocal_rhythm,
                motion_interpretation=shot.motion_interpretation,
                project_mode=project_input.project_mode,
                audio_driver=_resolve_audio_driver(audio_segment_type, context),
                lipsync_policy=lipsync_policy,
                start_frame_source=shot.start_frame_source.value if shot.start_frame_source else None,
                parent_shot_id=shot.parent_shot_id,
                needs_two_frames=shot.needs_two_frames,
                narration_mode=scene.narration_mode,
                validation_errors=_collect_messages_for_path(validation_report.errors, shot.shot_id),
                validation_warnings=_collect_messages_for_path(validation_report.warnings, shot.shot_id),
            )
            planned_shot.render_task = _build_render_task(planned_shot)
            render_tasks.append(planned_shot.render_task)
            planned_shots.append(planned_shot)

        planned_scene = PlannedScene(
            scene_id=scene.scene_id,
            scene_mode=scene.scene_mode or project_input.project_mode.value,
            start_sec=_round_sec(scene.start_sec),
            end_sec=_round_sec(scene.end_sec),
            duration_sec=_round_sec(scene.duration_sec),
            summary=scene.summary or scene.scene_id,
            narration_mode=scene.narration_mode,
            audio_segment_type=_convert_audio_segment_type(scene.audio_segment_type),
            continuation_from_prev=scene.continuation_from_prev,
            shots=planned_shots,
        )
        planned_scenes.append(planned_scene)
        compatibility_scenes.append(_build_compatibility_scene(planned_scene))

    canonical = AudioFirstPlannerOutput(
        input_mode=project_input.input_mode,
        project_mode=project_input.project_mode,
        planning_context=context,
        validation=validation,
        scenes=planned_scenes,
        render_tasks=render_tasks,
        debug={
            **_build_contract_debug_info(
                planner_input,
                parsed_output,
                validation.errors,
                validation.warnings,
                parse_result,
                raw_payload=raw_payload,
                raw_debug_summary=raw_debug_summary,
            ),
            "planning_status": parsed_output.planning_status.value,
            "project_mode": parsed_output.project_mode.value,
            "input_mode": parsed_output.input_mode.value,
            "planner_validation_errors": validation.errors,
            "planner_validation_warnings": validation.warnings,
            "planner_input": planner_input.model_dump(mode="json", exclude_none=True),
        },
    )
    return GeminiContractExecutionResult(
        canonical_output=canonical,
        parsed_output=parsed_output,
        validation_report=validation_report,
        planner_input=planner_input,
        compatibility_scenes=compatibility_scenes,
    )
