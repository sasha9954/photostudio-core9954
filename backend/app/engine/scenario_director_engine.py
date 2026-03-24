import ast
import base64
import json
import logging
import mimetypes
import os
import re
import tempfile
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.core.config import settings
from app.core.static_paths import ASSETS_DIR, BACKEND_DIR
from app.engine.audio_analyzer import (
    analyze_audio,
    analyze_audio_semantics,
    analyze_audio_semantics_fallback,
    derive_audio_semantic_profile,
)
from app.engine.gemini_rest import post_generate_content

ALLOWED_SOURCE_MODES = {"audio", "video_file", "video_link"}
ALLOWED_LTX_MODES = {"i2v", "i2v_as", "f_l", "f_l_as", "continuation", "lip_sync"}
ALLOWED_NARRATION_MODES = {"full", "duck", "pause"}
ALLOWED_EXPLICIT_ROLE_TYPES = {"hero", "support", "antagonist", "auto"}
DEFAULT_TEXT_MODEL = (getattr(settings, "GEMINI_TEXT_MODEL", None) or "gemini-3.1-pro-preview").strip() or "gemini-3.1-pro-preview"
FALLBACK_TEXT_MODEL = (getattr(settings, "GEMINI_TEXT_MODEL_FALLBACK", None) or "gemini-2.5-flash").strip() or "gemini-2.5-flash"
LEGACY_START_FRAME_ALIASES = {
    "previous_last_frame": "previous_frame",
    "prev_last": "previous_frame",
    "last_frame": "previous_frame",
}
JSON_ONLY_RETRY_SUFFIX = (
    "\n\nRETRY OVERRIDE: Output ONLY one JSON object. No markdown. No commentary. No comments. "
    "No alternative versions. Keep the same backend contract and return flat scene fields. "
    "HARD CONTRACT: narration_mode must be present in every scene, must be a string, must never be null, and allowed values are full, duck, pause. "
    "If unsure use full."
)
MASTER_JSON_RETRY_SUFFIX = (
    "\n\nRETRY OVERRIDE: Return ONLY JSON. No markdown. No comments. "
    "MASTER MODE ONLY. DO NOT generate scenes. Keep fields short."
)
SCENES_JSON_RETRY_SUFFIX = (
    "\n\nRETRY OVERRIDE: Return ONLY JSON. No markdown. No comments. "
    "SCENES MODE ONLY. Keep short fields only."
)

logger = logging.getLogger(__name__)

WEAK_SCENE_PATTERNS = (
    "character walks",
    "walks with determination",
    "camera follows",
    "tense cinematic moment",
    "mysterious atmosphere",
    "cinematic scene",
    "dramatic shot",
    "the subject moves",
    "the character looks around",
    "moody lighting",
    "suspenseful moment",
)
GENERIC_SCENE_GOALS = {
    "",
    "scene",
    "moment",
    "transition",
    "build mood",
    "set tone",
    "cinematic moment",
    "dramatic moment",
}
ABSTRACT_AUDIO_ONLY_WORDS = {"mood", "tension", "emotion", "feeling", "vibe", "atmosphere", "energy", "tone"}
CONCRETE_AUDIO_HINT_WORDS = {
    "door",
    "tunnel",
    "bunker",
    "missile",
    "desert",
    "facility",
    "map",
    "rock",
    "entrance",
    "corridor",
    "weapon",
    "radar",
    "silo",
    "base",
    "launch",
    "operation",
    "checkpoint",
    "control",
    "shaft",
    "vault",
}
WORLD_AUDIO_KEYWORDS = {
    "iran",
    "bunker",
    "tunnel",
    "missile",
    "military",
    "desert",
    "facility",
    "silo",
    "underground",
    "base",
    "corridor",
    "blast",
    "door",
    "war",
    "operation",
    "launch",
}
WORLD_MISMATCH_LOCATION_KEYWORDS = {
    "cafe",
    "restaurant",
    "beach",
    "party",
    "nightclub",
    "club",
    "hotel",
    "apartment",
    "penthouse",
    "rooftop",
    "ballroom",
    "wedding",
    "classroom",
    "office",
    "mall",
    "amusement",
    "park",
}
HIGH_SEVERITY_RISKS = {"world_mismatch", "invalid_phrase_boundary", "invalid_pause_boundary", "invalid_energy_boundary"}
LOW_SEVERITY_RISKS = {"weak_audio_anchor", "low_scene_confidence", "abstract_audio_usage", "missing_audio_anchor_evidence"}
SCENE_PURPOSES = (
    "hook",
    "entry",
    "destabilization",
    "reveal",
    "escalation",
    "confrontation",
    "transition",
    "peak image",
    "emotional climax",
    "final image / ending hold",
)
TIMELINE_START_TOLERANCE_SEC = 0.35
TIMELINE_END_TOLERANCE_SEC = 0.75
TIMELINE_INTERNAL_GAP_WARN_SEC = 1.25
TIMELINE_TAIL_WARN_SEC = 1.0
TIMELINE_COVERAGE_RATIO_WARN = 0.95
MAX_INLINE_AUDIO_BYTES = 15 * 1024 * 1024


class ScenarioDirectorError(RuntimeError):
    def __init__(self, code: str, message: str, *, status_code: int = 400, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class ScenarioDirectorScene(BaseModel):
    scene_id: str
    time_start: float = 0.0
    time_end: float = 0.0
    duration: float = 0.0
    actors: list[str] = Field(default_factory=list)
    location: str = ""
    props: list[str] = Field(default_factory=list)
    emotion: str = ""
    scene_goal: str = ""
    frame_description: str = ""
    action_in_frame: str = ""
    camera: str = ""
    image_prompt: str = ""
    video_prompt: str = ""
    ltx_mode: str = "i2v"
    ltx_reason: str = ""
    start_frame_source: str = "new"
    needs_two_frames: bool = False
    continuation_from_previous: bool = False
    narration_mode: str = "full"
    local_phrase: str | None = None
    sfx: str = ""
    music_mix_hint: str = "off"
    what_from_audio_this_scene_uses: str = ""
    director_note_layer: str = ""
    boundary_reason: str = "fallback"
    audio_anchor_evidence: str = ""
    confidence: float = 0.5

    @field_validator("scene_id", mode="before")
    @classmethod
    def _validate_scene_id(cls, value: Any) -> str:
        clean = str(value or "").strip()
        return clean or "S1"

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorScene":
        self.time_start = _safe_float(self.time_start, 0.0)
        self.time_end = _safe_float(self.time_end, self.time_start)
        self.duration = _safe_float(self.duration, max(0.0, self.time_end - self.time_start))
        if self.duration <= 0 and self.time_end > self.time_start:
            self.duration = round(self.time_end - self.time_start, 3)
        if self.time_end <= self.time_start and self.duration > 0:
            self.time_end = round(self.time_start + self.duration, 3)
        self.actors = [str(item).strip() for item in (self.actors or []) if str(item).strip()]
        self.props = [str(item).strip() for item in (self.props or []) if str(item).strip()]
        self.location = str(self.location or "").strip()
        self.emotion = str(self.emotion or "").strip()
        self.scene_goal = str(self.scene_goal or "").strip()
        self.frame_description = str(self.frame_description or "").strip()
        self.action_in_frame = str(self.action_in_frame or "").strip()
        self.camera = str(self.camera or "").strip()
        self.image_prompt = str(self.image_prompt or "").strip()
        self.video_prompt = str(self.video_prompt or "").strip()
        self.narration_mode = str(self.narration_mode or "full").strip() or "full"
        self.start_frame_source = _normalize_start_frame_source(self.start_frame_source, continuation=self.continuation_from_previous)
        self.needs_two_frames = _coerce_bool(self.needs_two_frames, False)
        self.continuation_from_previous = _coerce_bool(self.continuation_from_previous, False)
        self.ltx_mode = _normalize_ltx_mode(
            self.ltx_mode,
            continuation=self.continuation_from_previous,
            needs_two_frames=self.needs_two_frames,
            narration_mode=self.narration_mode,
        )
        self.local_phrase = str(self.local_phrase).strip() if self.local_phrase is not None and str(self.local_phrase).strip() else None
        self.sfx = _stringify_sfx(self.sfx)
        self.music_mix_hint = str(self.music_mix_hint or "off").strip() or "off"
        self.what_from_audio_this_scene_uses = str(self.what_from_audio_this_scene_uses or "").strip()
        self.director_note_layer = str(self.director_note_layer or "").strip()
        boundary_reason = str(self.boundary_reason or "fallback").strip().lower() or "fallback"
        self.boundary_reason = boundary_reason if boundary_reason in {"phrase", "pause", "semantic", "energy", "fallback"} else "fallback"
        self.audio_anchor_evidence = str(self.audio_anchor_evidence or "").strip()
        self.confidence = _safe_float(self.confidence, 0.5)
        if self.confidence < 0:
            self.confidence = 0.0
        elif self.confidence > 1:
            self.confidence = 1.0
        self.ltx_reason = _normalize_ltx_reason(
            str(self.ltx_reason or "").strip(),
            self.ltx_mode,
            narration_mode=self.narration_mode,
        )
        return self


class ScenarioDirectorAudioUnderstanding(BaseModel):
    main_topic: str = ""
    world_context: str = ""
    implied_events: list[str] = Field(default_factory=list)
    emotional_tone_from_audio: str = ""
    confidence_audio_understood: float = 0.0
    what_from_audio_defines_world: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorAudioUnderstanding":
        self.main_topic = str(self.main_topic or "").strip()
        self.world_context = str(self.world_context or "").strip()
        self.implied_events = [str(item).strip() for item in (self.implied_events or []) if str(item).strip()]
        self.emotional_tone_from_audio = str(self.emotional_tone_from_audio or "").strip()
        self.confidence_audio_understood = _safe_float(self.confidence_audio_understood, 0.0)
        if self.confidence_audio_understood < 0:
            self.confidence_audio_understood = 0.0
        elif self.confidence_audio_understood > 1:
            self.confidence_audio_understood = 1.0
        self.what_from_audio_defines_world = str(self.what_from_audio_defines_world or "").strip()
        return self


class ScenarioDirectorConflictAnalysis(BaseModel):
    audio_vs_director_note_conflict: bool = False
    conflict_description: str = ""
    resolution_strategy: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorConflictAnalysis":
        self.audio_vs_director_note_conflict = _coerce_bool(self.audio_vs_director_note_conflict, False)
        self.conflict_description = str(self.conflict_description or "").strip()
        self.resolution_strategy = str(self.resolution_strategy or "").strip()
        return self


class ScenarioDirectorNarrativeStrategy(BaseModel):
    story_core_source: str = "mixed"
    did_audio_remain_primary: bool = False
    did_director_note_override_audio: bool = False
    why: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorNarrativeStrategy":
        story_core_source = str(self.story_core_source or "mixed").strip().lower() or "mixed"
        self.story_core_source = story_core_source if story_core_source in {"audio", "director_note", "mixed", "fallback"} else "mixed"
        self.did_audio_remain_primary = _coerce_bool(self.did_audio_remain_primary, False)
        self.did_director_note_override_audio = _coerce_bool(self.did_director_note_override_audio, False)
        self.why = str(self.why or "").strip()
        return self


class ScenarioDirectorStoryMeta(BaseModel):
    title: str = ""
    summary: str = ""
    how_director_note_was_integrated: str = ""
    how_romance_exists_inside_audio_world: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorStoryMeta":
        self.title = str(self.title or "").strip()
        self.summary = str(self.summary or "").strip()
        self.how_director_note_was_integrated = str(self.how_director_note_was_integrated or "").strip()
        self.how_romance_exists_inside_audio_world = str(self.how_romance_exists_inside_audio_world or "").strip()
        return self


class ScenarioDirectorDiagnostics(BaseModel):
    used_audio_as_content_source: bool = False
    used_audio_only_as_mood: bool = False
    did_fallback_from_audio_content_truth: bool = False
    biggest_risk: str = ""
    what_may_be_wrong: str = ""
    planner_mode: str = "text_fallback"
    how_director_note_was_integrated: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorDiagnostics":
        self.used_audio_as_content_source = _coerce_bool(self.used_audio_as_content_source, False)
        self.used_audio_only_as_mood = _coerce_bool(self.used_audio_only_as_mood, False)
        self.did_fallback_from_audio_content_truth = _coerce_bool(self.did_fallback_from_audio_content_truth, False)
        self.biggest_risk = str(self.biggest_risk or "").strip()
        self.what_may_be_wrong = str(self.what_may_be_wrong or "").strip()
        planner_mode = str(self.planner_mode or "text_fallback").strip().lower() or "text_fallback"
        self.planner_mode = planner_mode if planner_mode in {"full_audio_first", "partial_audio_first", "text_fallback"} else "text_fallback"
        self.how_director_note_was_integrated = str(self.how_director_note_was_integrated or "").strip()
        return self


class ScenarioDirectorStoryboardOut(BaseModel):
    story_summary: str = ""
    full_scenario: str = ""
    voice_script: str = ""
    music_prompt: str = ""
    director_summary: str = ""
    audio_understanding: ScenarioDirectorAudioUnderstanding = Field(default_factory=ScenarioDirectorAudioUnderstanding)
    conflict_analysis: ScenarioDirectorConflictAnalysis = Field(default_factory=ScenarioDirectorConflictAnalysis)
    narrative_strategy: ScenarioDirectorNarrativeStrategy = Field(default_factory=ScenarioDirectorNarrativeStrategy)
    story: ScenarioDirectorStoryMeta = Field(default_factory=ScenarioDirectorStoryMeta)
    diagnostics: ScenarioDirectorDiagnostics = Field(default_factory=ScenarioDirectorDiagnostics)
    scenes: list[ScenarioDirectorScene] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorStoryboardOut":
        self.story_summary = str(self.story_summary or "").strip()
        self.full_scenario = str(self.full_scenario or "").strip()
        self.voice_script = str(self.voice_script or "").strip()
        self.music_prompt = str(self.music_prompt or "").strip()
        self.director_summary = str(self.director_summary or "").strip()
        if not self.scenes:
            raise ValueError("director output must contain at least one scene")
        return self


def _safe_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        return fallback
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return fallback
    return round(parsed, 3)


def _coerce_bool(value: Any, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    if isinstance(value, (int, float)):
        return bool(value)
    clean = str(value).strip().lower()
    if clean in {"true", "1", "yes", "y", "on"}:
        return True
    if clean in {"false", "0", "no", "n", "off", "null", "none", ""}:
        return False
    return fallback


def _stringify_sfx(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(str(item).strip() for item in value if str(item).strip())
    return str(value or "").strip()


def _normalize_start_frame_source(value: Any, *, continuation: bool = False) -> str:
    clean = str(value or "").strip()
    if not clean and continuation:
        return "previous_frame"
    normalized = LEGACY_START_FRAME_ALIASES.get(clean, clean)
    if normalized in {"new", "first_frame", "previous_frame", "generated"}:
        return normalized
    if continuation:
        return "previous_frame"
    if normalized == "":
        return "new"
    return normalized


def _normalize_ltx_mode(value: Any, *, continuation: bool, needs_two_frames: bool, narration_mode: str) -> str:
    clean = str(value or "").strip()
    if clean in ALLOWED_LTX_MODES:
        if clean == "lip_sync" and not _is_music_vocal_mode(narration_mode):
            return "i2v_as"
        return clean
    if continuation:
        return "continuation"
    if needs_two_frames:
        return "f_l"
    return "i2v"


def _normalize_ltx_reason(reason: str, ltx_mode: str, *, narration_mode: str) -> str:
    if reason:
        if ltx_mode == "lip_sync" and not _is_music_vocal_mode(narration_mode):
            return f"{reason}; normalized from lip_sync because narration is not music-vocal driven"
        return reason
    defaults = {
        "i2v": "Static or atmospheric scene with clean single-frame animation.",
        "i2v_as": "Audio-sensitive motion without speech articulation.",
        "f_l": "A-to-B transition that requires two frames.",
        "f_l_as": "Audio-accented A-to-B transition that requires two frames.",
        "continuation": "Direct continuation of the previous shot.",
        "lip_sync": "Music-vocal rhythm shot with visible articulation support.",
    }
    return defaults.get(ltx_mode, "Production render mode selected by Scenario Director.")


def _is_music_vocal_mode(value: str) -> bool:
    lowered = str(value or "").strip().lower()
    return any(token in lowered for token in ("music", "vocal", "lyric", "sing", "chorus"))


def _extract_gemini_text(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp, dict) else None
    if isinstance(candidates, list):
        for candidate in candidates:
            content = candidate.get("content") if isinstance(candidate, dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if not isinstance(parts, list):
                continue
            texts = [str(part.get("text") or "") for part in parts if isinstance(part, dict) and str(part.get("text") or "").strip()]
            if texts:
                return "\n".join(texts).strip()
    return ""


def _extract_json_blob(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.strip().startswith("```")]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return text[start:end + 1]
    return text


def _extract_balanced_json_candidate(text: str) -> str | None:
    in_string = False
    escape = False
    depth = 0
    start_index: int | None = None
    for index, char in enumerate(text):
        if start_index is None:
            if char == "{":
                start_index = index
                depth = 1
            continue
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start_index:index + 1]
    return None


def _clean_dirty_json_blob(raw_text: str) -> str:
    candidate = _extract_json_blob(raw_text)
    candidate = re.sub(r"^```(?:json)?\s*", "", candidate.strip(), flags=re.IGNORECASE)
    candidate = re.sub(r"\s*```$", "", candidate, flags=re.IGNORECASE)
    candidate = _extract_balanced_json_candidate(candidate) or candidate
    candidate = candidate.strip().strip("`")
    return candidate


def _try_parse_dirty_json(text: str) -> dict[str, Any] | None:
    candidate = _clean_dirty_json_blob(text)
    if not candidate.startswith("{"):
        return None
    try:
        parsed = json.loads(candidate)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        pass

    pythonish = re.sub(r"\btrue\b", "True", candidate, flags=re.IGNORECASE)
    pythonish = re.sub(r"\bfalse\b", "False", pythonish, flags=re.IGNORECASE)
    pythonish = re.sub(r"\bnull\b", "None", pythonish, flags=re.IGNORECASE)
    try:
        parsed = ast.literal_eval(pythonish)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
    return _try_parse_dirty_json(raw_text)


def _normalize_legacy_scene_shape(scene: dict) -> dict:
    normalized = dict(scene or {})
    visual = normalized.pop("visual", None)
    audio = normalized.pop("audio", None)
    ltx = normalized.pop("ltx", None)

    applied = False
    if isinstance(visual, dict):
        normalized.setdefault("frame_description", visual.get("frame_description"))
        normalized.setdefault("action_in_frame", visual.get("action_in_frame"))
        normalized.setdefault("camera", visual.get("camera"))
        applied = True
    if isinstance(audio, dict):
        normalized.setdefault("narration_mode", audio.get("narration"))
        normalized.setdefault("local_phrase", audio.get("local_phrase"))
        normalized.setdefault("sfx", audio.get("sfx"))
        applied = True
    if isinstance(ltx, dict):
        normalized.setdefault("ltx_mode", ltx.get("mode"))
        normalized.setdefault("ltx_reason", ltx.get("reason"))
        normalized.setdefault("start_frame_source", ltx.get("start_frame_source"))
        normalized.setdefault("needs_two_frames", ltx.get("needs_two_frames"))
        applied = True

    normalized["start_frame_source"] = _normalize_start_frame_source(
        normalized.get("start_frame_source"),
        continuation=_coerce_bool(normalized.get("continuation_from_previous"), False),
    )
    normalized["needs_two_frames"] = _coerce_bool(normalized.get("needs_two_frames"), False)
    normalized["continuation_from_previous"] = _coerce_bool(
        normalized.get("continuation_from_previous") or normalized.get("start_frame_source") == "previous_frame",
        False,
    )
    if normalized["start_frame_source"] == "previous_frame":
        normalized["continuation_from_previous"] = True
    normalized["sfx"] = _stringify_sfx(normalized.get("sfx"))
    normalized.setdefault("what_from_audio_this_scene_uses", normalized.get("whatFromAudioThisSceneUses"))
    normalized.setdefault("director_note_layer", normalized.get("directorNoteLayer"))
    normalized.setdefault("boundary_reason", normalized.get("boundaryReason"))
    normalized.setdefault("audio_anchor_evidence", normalized.get("audioAnchorEvidence"))
    normalized.setdefault("confidence", normalized.get("confidence"))

    if applied:
        logger.debug(
            "[SCENARIO_DIRECTOR] legacy scene normalized scene_id=%s ltx_mode=%s start_frame=%s",
            str(normalized.get("scene_id") or "").strip() or "unknown",
            str(normalized.get("ltx_mode") or "").strip() or "auto",
            normalized.get("start_frame_source") or "new",
        )
    return normalized


def _normalize_scenario_director_scene_defaults(payload: dict[str, Any]) -> tuple[dict[str, Any], list[str], list[str]]:
    if not isinstance(payload, dict):
        return payload, [], []
    repaired = dict(payload)
    scenes = repaired.get("scenes")
    if not isinstance(scenes, list):
        return repaired, [], []
    normalized_fields: list[str] = []
    warnings: list[str] = []
    normalized_scenes: list[Any] = []
    for idx, raw_scene in enumerate(scenes):
        if not isinstance(raw_scene, dict):
            normalized_scenes.append(raw_scene)
            continue
        scene = dict(raw_scene)
        narration_mode = str(scene.get("narration_mode") or "").strip().lower()
        if narration_mode not in ALLOWED_NARRATION_MODES:
            scene["narration_mode"] = "full"
            normalized_fields.append(f"scenes[{idx}].narration_mode")
            warnings.append("scenario_director_normalized_narration_mode_default")
        normalized_scenes.append(scene)
    repaired["scenes"] = normalized_scenes
    return repaired, normalized_fields, list(dict.fromkeys(warnings))


def _repair_scenario_director_payload(payload: dict) -> dict:
    candidate = payload
    changed = False
    if isinstance(candidate.get("storyboard_out"), dict):
        candidate = candidate["storyboard_out"]
        changed = True
    elif isinstance(candidate.get("storyboardOut"), dict):
        candidate = candidate["storyboardOut"]
        changed = True
    elif isinstance(candidate.get("output"), dict) and isinstance(candidate["output"].get("scenes"), list):
        candidate = candidate["output"]
        changed = True

    repaired = dict(candidate)
    scenes = repaired.get("scenes")
    if isinstance(scenes, list):
        normalized_scenes = [_normalize_legacy_scene_shape(scene) if isinstance(scene, dict) else scene for scene in scenes]
        if normalized_scenes != scenes:
            changed = True
        repaired["scenes"] = normalized_scenes
        repaired, normalized_fields, _ = _normalize_scenario_director_scene_defaults(repaired)
        if normalized_fields:
            changed = True

    if not repaired.get("story_summary"):
        repaired["story_summary"] = repaired.get("summary") or repaired.get("storySummary") or ""
        changed = changed or bool(repaired["story_summary"])
    if not repaired.get("full_scenario"):
        repaired["full_scenario"] = repaired.get("scenario") or repaired.get("fullScenario") or repaired.get("story") or ""
        changed = changed or bool(repaired["full_scenario"])
    if not repaired.get("voice_script"):
        repaired["voice_script"] = repaired.get("voiceScript") or repaired.get("narration_script") or ""
        changed = changed or bool(repaired["voice_script"])
    if not repaired.get("music_prompt"):
        repaired["music_prompt"] = repaired.get("musicPrompt") or repaired.get("music_direction") or ""
        changed = changed or bool(repaired["music_prompt"])
    if not repaired.get("director_summary"):
        repaired["director_summary"] = repaired.get("directorSummary") or repaired.get("direction_summary") or ""
        changed = changed or bool(repaired["director_summary"])
    if not isinstance(repaired.get("audio_understanding"), dict):
        candidate_audio_understanding = repaired.get("audioUnderstanding")
        if isinstance(candidate_audio_understanding, dict):
            repaired["audio_understanding"] = candidate_audio_understanding
            changed = True
    if isinstance(repaired.get("audio_understanding"), dict):
        audio_understanding = dict(repaired.get("audio_understanding") or {})
        audio_understanding.setdefault("main_topic", audio_understanding.get("mainTopic"))
        audio_understanding.setdefault("world_context", audio_understanding.get("worldContext"))
        audio_understanding.setdefault("implied_events", audio_understanding.get("impliedEvents"))
        audio_understanding.setdefault("emotional_tone_from_audio", audio_understanding.get("emotionalToneFromAudio"))
        audio_understanding.setdefault("confidence_audio_understood", audio_understanding.get("confidenceAudioUnderstood"))
        audio_understanding.setdefault("what_from_audio_defines_world", audio_understanding.get("whatFromAudioDefinesWorld"))
        repaired["audio_understanding"] = audio_understanding
    if not isinstance(repaired.get("conflict_analysis"), dict):
        candidate_conflict = repaired.get("conflictAnalysis")
        if isinstance(candidate_conflict, dict):
            repaired["conflict_analysis"] = candidate_conflict
            changed = True
    if isinstance(repaired.get("conflict_analysis"), dict):
        conflict_analysis = dict(repaired.get("conflict_analysis") or {})
        conflict_analysis.setdefault("audio_vs_director_note_conflict", conflict_analysis.get("audioVsDirectorNoteConflict"))
        conflict_analysis.setdefault("conflict_description", conflict_analysis.get("conflictDescription"))
        conflict_analysis.setdefault("resolution_strategy", conflict_analysis.get("resolutionStrategy"))
        repaired["conflict_analysis"] = conflict_analysis
    if not isinstance(repaired.get("narrative_strategy"), dict):
        candidate_strategy = repaired.get("narrativeStrategy")
        if isinstance(candidate_strategy, dict):
            repaired["narrative_strategy"] = candidate_strategy
            changed = True
    if isinstance(repaired.get("narrative_strategy"), dict):
        narrative_strategy = dict(repaired.get("narrative_strategy") or {})
        narrative_strategy.setdefault("story_core_source", narrative_strategy.get("storyCoreSource"))
        narrative_strategy.setdefault("did_audio_remain_primary", narrative_strategy.get("didAudioRemainPrimary"))
        narrative_strategy.setdefault("did_director_note_override_audio", narrative_strategy.get("didDirectorNoteOverrideAudio"))
        repaired["narrative_strategy"] = narrative_strategy
    if not isinstance(repaired.get("story"), dict):
        candidate_story = repaired.get("story")
        if isinstance(candidate_story, dict):
            repaired["story"] = candidate_story
            changed = True
    if not isinstance(repaired.get("diagnostics"), dict):
        candidate_diagnostics = repaired.get("diagnostics")
        if isinstance(candidate_diagnostics, dict):
            repaired["diagnostics"] = candidate_diagnostics
            changed = True
    if isinstance(repaired.get("story"), dict):
        story = dict(repaired.get("story") or {})
        story.setdefault("how_director_note_was_integrated", story.get("howDirectorNoteWasIntegrated"))
        story.setdefault("how_romance_exists_inside_audio_world", story.get("howRomanceExistsInsideAudioWorld"))
        repaired["story"] = story
    if isinstance(repaired.get("diagnostics"), dict):
        diagnostics = dict(repaired.get("diagnostics") or {})
        diagnostics.setdefault("used_audio_as_content_source", diagnostics.get("usedAudioAsContentSource"))
        diagnostics.setdefault("used_audio_only_as_mood", diagnostics.get("usedAudioOnlyAsMood"))
        diagnostics.setdefault("did_fallback_from_audio_content_truth", diagnostics.get("didFallbackFromAudioContentTruth"))
        diagnostics.setdefault("biggest_risk", diagnostics.get("biggestRisk"))
        diagnostics.setdefault("what_may_be_wrong", diagnostics.get("whatMayBeWrong"))
        diagnostics.setdefault("planner_mode", diagnostics.get("plannerMode"))
        diagnostics.setdefault("how_director_note_was_integrated", diagnostics.get("howDirectorNoteWasIntegrated"))
        repaired["diagnostics"] = diagnostics

    if changed:
        logger.debug(
            "[SCENARIO_DIRECTOR] repair applied scenes=%s story_summary=%s",
            len(repaired.get("scenes") or []),
            bool(str(repaired.get("story_summary") or "").strip()),
        )
    return repaired


def _extract_structured_diagnostics(parsed_payload: dict[str, Any]) -> dict[str, Any]:
    audio_understanding_raw = parsed_payload.get("audio_understanding") if isinstance(parsed_payload.get("audio_understanding"), dict) else {}
    conflict_analysis_raw = parsed_payload.get("conflict_analysis") if isinstance(parsed_payload.get("conflict_analysis"), dict) else {}
    narrative_strategy_raw = parsed_payload.get("narrative_strategy") if isinstance(parsed_payload.get("narrative_strategy"), dict) else {}
    diagnostics_raw = parsed_payload.get("diagnostics") if isinstance(parsed_payload.get("diagnostics"), dict) else {}
    story_raw = parsed_payload.get("story") if isinstance(parsed_payload.get("story"), dict) else {}
    return {
        "audioUnderstanding": {
            "mainTopic": str(audio_understanding_raw.get("main_topic") or audio_understanding_raw.get("mainTopic") or "").strip(),
            "worldContext": str(audio_understanding_raw.get("world_context") or audio_understanding_raw.get("worldContext") or "").strip(),
            "impliedEvents": [
                str(item).strip()
                for item in (audio_understanding_raw.get("implied_events") or audio_understanding_raw.get("impliedEvents") or [])
                if str(item).strip()
            ],
            "emotionalToneFromAudio": str(audio_understanding_raw.get("emotional_tone_from_audio") or audio_understanding_raw.get("emotionalToneFromAudio") or "").strip(),
            "confidenceAudioUnderstood": _safe_float(
                audio_understanding_raw.get("confidence_audio_understood") if audio_understanding_raw.get("confidence_audio_understood") is not None else audio_understanding_raw.get("confidenceAudioUnderstood"),
                0.0,
            ),
            "whatFromAudioDefinesWorld": str(audio_understanding_raw.get("what_from_audio_defines_world") or audio_understanding_raw.get("whatFromAudioDefinesWorld") or "").strip(),
        },
        "conflictAnalysis": {
            "audioVsDirectorNoteConflict": _coerce_bool(
                conflict_analysis_raw.get("audio_vs_director_note_conflict")
                if conflict_analysis_raw.get("audio_vs_director_note_conflict") is not None
                else conflict_analysis_raw.get("audioVsDirectorNoteConflict"),
                False,
            ),
            "conflictDescription": str(conflict_analysis_raw.get("conflict_description") or conflict_analysis_raw.get("conflictDescription") or "").strip(),
            "resolutionStrategy": str(conflict_analysis_raw.get("resolution_strategy") or conflict_analysis_raw.get("resolutionStrategy") or "").strip(),
        },
        "narrativeStrategy": {
            "storyCoreSource": str(narrative_strategy_raw.get("story_core_source") or narrative_strategy_raw.get("storyCoreSource") or "").strip().lower() or "mixed",
            "didAudioRemainPrimary": _coerce_bool(
                narrative_strategy_raw.get("did_audio_remain_primary")
                if narrative_strategy_raw.get("did_audio_remain_primary") is not None
                else narrative_strategy_raw.get("didAudioRemainPrimary"),
                False,
            ),
            "didDirectorNoteOverrideAudio": _coerce_bool(
                narrative_strategy_raw.get("did_director_note_override_audio")
                if narrative_strategy_raw.get("did_director_note_override_audio") is not None
                else narrative_strategy_raw.get("didDirectorNoteOverrideAudio"),
                False,
            ),
            "why": str(narrative_strategy_raw.get("why") or "").strip(),
        },
        "story": {
            "title": str(story_raw.get("title") or "").strip(),
            "summary": str(story_raw.get("summary") or "").strip(),
            "howDirectorNoteWasIntegrated": str(story_raw.get("how_director_note_was_integrated") or story_raw.get("howDirectorNoteWasIntegrated") or "").strip(),
            "howRomanceExistsInsideAudioWorld": str(story_raw.get("how_romance_exists_inside_audio_world") or story_raw.get("howRomanceExistsInsideAudioWorld") or "").strip(),
        },
        "diagnostics": {
            "usedAudioAsContentSource": _coerce_bool(
                diagnostics_raw.get("used_audio_as_content_source")
                if diagnostics_raw.get("used_audio_as_content_source") is not None
                else diagnostics_raw.get("usedAudioAsContentSource"),
                False,
            ),
            "usedAudioOnlyAsMood": _coerce_bool(
                diagnostics_raw.get("used_audio_only_as_mood")
                if diagnostics_raw.get("used_audio_only_as_mood") is not None
                else diagnostics_raw.get("usedAudioOnlyAsMood"),
                False,
            ),
            "didFallbackFromAudioContentTruth": _coerce_bool(
                diagnostics_raw.get("did_fallback_from_audio_content_truth")
                if diagnostics_raw.get("did_fallback_from_audio_content_truth") is not None
                else diagnostics_raw.get("didFallbackFromAudioContentTruth"),
                False,
            ),
            "biggestRisk": str(diagnostics_raw.get("biggest_risk") or diagnostics_raw.get("biggestRisk") or "").strip(),
            "whatMayBeWrong": str(diagnostics_raw.get("what_may_be_wrong") or diagnostics_raw.get("whatMayBeWrong") or "").strip(),
            "plannerMode": str(diagnostics_raw.get("planner_mode") or diagnostics_raw.get("plannerMode") or "").strip().lower() or "text_fallback",
            "howDirectorNoteWasIntegrated": str(diagnostics_raw.get("how_director_note_was_integrated") or diagnostics_raw.get("howDirectorNoteWasIntegrated") or "").strip(),
        },
    }


def _build_reference_role_map(payload: dict[str, Any]) -> dict[str, str]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    role_map: dict[str, str] = {}
    for role, item in refs.items():
        if not isinstance(item, dict):
            continue
        label = str(item.get("preview") or item.get("label") or item.get("source_label") or role).strip()
        role_map[str(role).strip()] = label or str(role).strip()
    return role_map


def _scene_participants(scene: ScenarioDirectorScene, role_labels: dict[str, str]) -> list[str]:
    participants: list[str] = []
    for actor in scene.actors:
        clean = str(actor or "").strip()
        if not clean:
            continue
        participants.append(role_labels.get(clean, clean))
    return participants


def _build_character_roles(payload: dict[str, Any], role_labels: dict[str, str]) -> list[dict[str, str]]:
    ordered_roles = ["character_1", "character_2", "character_3"]
    effective_role_types, _, _ = _resolve_effective_role_type_by_role(payload)
    role_copy_by_type = {
        "hero": "Главный герой / главный носитель действия",
        "support": "Партнёр по сцене / поддерживающий акцент",
        "antagonist": "Антагонист / контр-сила конфликта",
    }
    default_role_copy = {
        "character_1": "Главный герой / главный носитель действия",
        "character_2": "Партнёр по сцене / вторичный акцент",
        "character_3": "Поддерживающий персонаж или смысловой объект",
    }
    out: list[dict[str, str]] = []
    for role in ordered_roles:
        label = role_labels.get(role)
        if not label:
            continue
        explicit_type = str(effective_role_types.get(role) or "").strip().lower()
        role_copy = role_copy_by_type.get(explicit_type) or default_role_copy.get(role, "Поддерживающая роль")
        out.append({"name": label, "role": role_copy})
    return out


def _resolve_audio_duration_info(payload: dict[str, Any]) -> tuple[float, str]:
    normalized = _normalize_audio_context(payload)
    return _safe_float(normalized.get("audioDurationSec"), 0.0), str(normalized.get("audioDurationSource") or "missing")


def _resolve_audio_asset_path(audio_url: str | None) -> str | None:
    clean = str(audio_url or "").strip()
    if not clean:
        return None
    parsed = urlparse(clean)
    filename = os.path.basename(parsed.path)
    if not filename:
        return None
    base = os.path.splitext(filename)[0]
    candidates = [filename, base, f"{base}.mp3", f"{base}.wav", f"{base}.ogg", f"{base}.m4a"]
    for name in candidates:
        path = os.path.join(str(ASSETS_DIR), name)
        if os.path.isfile(path):
            return path
    return None


def _resolve_audio_source_for_analysis(audio_url: str | None) -> dict[str, Any]:
    clean = str(audio_url or "").strip()
    if not clean:
        return {"ok": False, "mode": "missing", "path": None, "url": None, "normalized": "", "hint": "audio_url_missing", "reason": "audio_url_missing"}

    def _resolve_local_static_asset(path_value: str) -> str | None:
        path_clean = str(path_value or "").strip()
        if not path_clean:
            return None
        normalized_path = path_clean.split("?", 1)[0].split("#", 1)[0]
        normalized_path = normalized_path.lstrip("/")
        if not normalized_path.startswith("static/assets/"):
            return None
        relative_path = normalized_path[len("static/"):].strip("/")
        if not relative_path:
            return None
        candidate = (BACKEND_DIR / "static" / relative_path).resolve()
        try:
            candidate.relative_to((BACKEND_DIR / "static").resolve())
        except ValueError:
            return None
        if candidate.is_file():
            return str(candidate)
        return None

    parsed = urlparse(clean)
    if parsed.scheme in {"http", "https"}:
        local_static_path = _resolve_local_static_asset(parsed.path or "")
        if local_static_path:
            return {
                "ok": True,
                "mode": "local_file",
                "path": local_static_path,
                "url": None,
                "normalized": local_static_path,
                "hint": "audio_local_static_asset_from_http_url",
                "reason": "http_url_points_to_static_assets_local_file_exists",
            }
        return {
            "ok": True,
            "mode": "http",
            "path": None,
            "url": clean,
            "normalized": clean,
            "hint": "audio_url_absolute",
            "reason": "absolute_http_url_fallback",
        }

    if parsed.scheme:
        return {"ok": False, "mode": "invalid", "path": None, "url": None, "normalized": clean, "hint": "audio_url_not_absolute", "reason": "unsupported_url_scheme"}

    normalized = clean.lstrip("/")
    path_variants = [normalized]
    if normalized.startswith("static/"):
        path_variants.append(normalized[len("static/"):])
    if normalized.startswith("assets/"):
        path_variants.append(f"static/{normalized}")

    for variant in path_variants:
        variant_clean = variant.strip("/")
        if not variant_clean:
            continue
        candidate = (BACKEND_DIR / variant_clean).resolve()
        if candidate.is_file():
            return {
                "ok": True,
                "mode": "local_file",
                "path": str(candidate),
                "url": None,
                "normalized": str(candidate),
                "hint": "audio_local_file_resolved",
                "reason": "relative_path_resolved_to_backend_file",
            }

    asset_path = _resolve_audio_asset_path(clean)
    if asset_path:
        return {
            "ok": True,
            "mode": "local_file",
            "path": asset_path,
            "url": None,
            "normalized": asset_path,
            "hint": "audio_asset_resolved",
            "reason": "asset_filename_resolved",
        }

    public_base = (getattr(settings, "PUBLIC_BASE_URL", None) or "").strip()
    if public_base:
        normalized_url_path = clean if clean.startswith("/") else f"/{clean}"
        fallback_url = urljoin(public_base.rstrip("/") + "/", normalized_url_path.lstrip("/"))
        return {
            "ok": True,
            "mode": "http",
            "path": None,
            "url": fallback_url,
            "normalized": fallback_url,
            "hint": "audio_public_base_url_fallback",
            "reason": "public_base_url_fallback",
        }

    return {"ok": False, "mode": "missing", "path": None, "url": None, "normalized": clean, "hint": "audio_asset_not_found", "reason": "asset_not_found_locally_and_no_http_fallback"}


def _normalize_audio_context(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    source_audio_meta = source_metadata.get("audio") if isinstance(source_metadata.get("audio"), dict) else {}
    metadata_audio = metadata.get("audio") if isinstance(metadata.get("audio"), dict) else {}
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}

    source_mode = str(
        source.get("source_mode")
        or payload.get("source_mode")
        or payload.get("sourceMode")
        or metadata.get("sourceMode")
        or "audio"
    ).strip().lower()
    duration_candidates = [
        ("payload.audioDurationSec", payload.get("audioDurationSec")),
        ("source.audioDurationSec", source.get("audioDurationSec")),
        ("source.metadata.audioDurationSec", source_metadata.get("audioDurationSec")),
        ("metadata.audioDurationSec", metadata.get("audioDurationSec")),
        ("metadata.audio.durationSec", metadata_audio.get("durationSec")),
        ("source.metadata.audio.durationSec", source_audio_meta.get("durationSec")),
        ("source_metadata.audioDurationSec", (payload.get("source_metadata") or {}).get("audioDurationSec") if isinstance(payload.get("source_metadata"), dict) else None),
    ]
    audio_duration_sec = 0.0
    duration_source = "missing"
    for key, value in duration_candidates:
        parsed = _safe_float(value, 0.0)
        if parsed > 0:
            audio_duration_sec = parsed
            duration_source = key
            break

    source_origin_raw = str(
        payload.get("source_origin")
        or source.get("source_origin")
        or source.get("origin")
        or metadata_audio.get("origin")
        or payload.get("sourceOrigin")
        or ("connected" if source_mode == "audio" else "")
    ).strip()
    source_origin_lower = source_origin_raw.lower()
    source_origin = "connected" if source_origin_lower in {"connected", "audio_node", "audio_upload", "audio_generated"} else source_origin_raw
    audio_url = str(
        source.get("source_value")
        or source.get("value")
        or payload.get("source_value")
        or metadata_audio.get("url")
        or source_audio_meta.get("url")
        or ""
    ).strip()
    prefer_audio_over_text = _coerce_bool(controls.get("preferAudioOverText"), source_mode == "audio")
    timeline_source = str(controls.get("timelineSource") or ("audio" if source_mode == "audio" else "text")).strip().lower() or "text"
    segmentation_mode = str(controls.get("segmentationMode") or ("phrase-first" if source_mode == "audio" else "default")).strip().lower() or "default"
    has_audio = source_mode == "audio" and bool(audio_url)

    return {
        "hasAudio": has_audio,
        "audioUrl": audio_url or None,
        "audioDurationSec": audio_duration_sec,
        "audioDurationSource": duration_source,
        "sourceMode": source_mode.upper(),
        "sourceOrigin": source_origin or None,
        "sourceOriginRaw": source_origin_raw or None,
        "preferAudioOverText": prefer_audio_over_text,
        "timelineSource": timeline_source,
        "segmentationMode": segmentation_mode,
        "useAudioPhraseBoundaries": _coerce_bool(controls.get("useAudioPhraseBoundaries"), source_mode == "audio"),
    }


def _build_audio_analysis_fallback(duration_sec: float, hint: str, source: str = "none") -> dict[str, Any]:
    return {
        "ok": False,
        "audioDurationSec": _safe_float(duration_sec, 0.0),
        "phrases": [],
        "pauseWindows": [],
        "energyTransitions": [],
        "sections": [],
        "beats": [],
        "bars": [],
        "source": source,
        "hint": hint,
        "errors": [hint] if hint else [],
    }


def _analyze_audio_for_scenario_director(audio_context: dict[str, Any]) -> dict[str, Any]:
    audio_url = str(audio_context.get("audioUrl") or "").strip()
    payload_duration = _safe_float(audio_context.get("audioDurationSec"), 0.0)
    resolution = _resolve_audio_source_for_analysis(audio_url)
    if not resolution.get("ok"):
        return {
            **_build_audio_analysis_fallback(payload_duration, str(resolution.get("hint") or "audio_url_missing"), source="missing"),
            "audioUrlRaw": audio_url or None,
            "audioUrlNormalized": resolution.get("normalized"),
            "audioUrlResolutionMode": resolution.get("mode"),
            "audioResolvedPath": resolution.get("path"),
            "audioResolutionReason": resolution.get("reason"),
        }

    source = "local_file" if resolution.get("mode") == "local_file" else "http_download"
    temp_path: str | None = None
    errors: list[str] = []
    try:
        analysis = analyze_audio(str(resolution.get("path"))) if resolution.get("mode") == "local_file" and resolution.get("path") else None
        if analysis is None:
            fetch_url = str(resolution.get("url") or "")
            if not fetch_url:
                return {
                    **_build_audio_analysis_fallback(payload_duration, "audio_url_not_absolute", source=source),
                    "audioUrlRaw": audio_url or None,
                    "audioUrlNormalized": resolution.get("normalized"),
                    "audioUrlResolutionMode": "invalid",
                    "audioResolvedPath": resolution.get("path"),
                    "audioResolutionReason": resolution.get("reason"),
                }
            suffix = os.path.splitext(urlparse(fetch_url).path)[1] or ".audio"
            response = requests.get(fetch_url, timeout=30)
            response.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(response.content)
                temp_path = tmp.name
            analysis = analyze_audio(temp_path)
        semantic = derive_audio_semantic_profile(analysis)
        return {
            "ok": True,
            "audioDurationSec": _safe_float(analysis.get("duration"), payload_duration),
            "phrases": analysis.get("vocalPhrases") if isinstance(analysis.get("vocalPhrases"), list) else [],
            "pauseWindows": [
                {"start": _safe_float(item, 0.0), "end": _safe_float(item, 0.0)}
                for item in (analysis.get("pausePoints") or [])
            ],
            "energyTransitions": [{"timeSec": _safe_float(item, 0.0)} for item in (analysis.get("energyPeaks") or [])],
            "sections": analysis.get("sections") if isinstance(analysis.get("sections"), list) else [],
            "beats": analysis.get("beats") if isinstance(analysis.get("beats"), list) else [],
            "bars": analysis.get("bars") if isinstance(analysis.get("bars"), list) else [],
            "source": source,
            "hint": "analysis_ok",
            "errors": errors,
            "semantic": semantic,
            "audioUrlRaw": audio_url or None,
            "audioUrlNormalized": resolution.get("normalized"),
            "audioUrlResolutionMode": resolution.get("mode"),
            "audioResolvedPath": resolution.get("path"),
            "audioResolutionReason": resolution.get("reason"),
        }
    except Exception as exc:
        errors.append(f"audio_analysis_failed:{str(exc)[:180]}")
        return {
            **_build_audio_analysis_fallback(payload_duration, "audio_fetch_failed", source=source),
            "errors": errors,
            "audioUrlRaw": audio_url or None,
            "audioUrlNormalized": resolution.get("normalized"),
            "audioUrlResolutionMode": resolution.get("mode"),
            "audioResolvedPath": resolution.get("path"),
            "audioResolutionReason": resolution.get("reason"),
        }
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


def _analyze_audio_semantics_for_scenario_director(payload: dict[str, Any], audio_context: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    transcript_candidates = [
        metadata.get("audioTranscript"),
        metadata.get("transcript"),
        source_metadata.get("audioTranscript"),
        source_metadata.get("transcript"),
    ]
    transcript = ""
    for candidate in transcript_candidates:
        if isinstance(candidate, str) and candidate.strip():
            transcript = candidate.strip()
            break

    audio_url = str(audio_context.get("audioUrl") or "").strip() or None
    if not transcript:
        semantics = analyze_audio_semantics_fallback("", hint="no_asr_transcript")
    else:
        semantics = analyze_audio_semantics(audio_url, transcript_text=transcript)

    normalized = semantics if isinstance(semantics, dict) else {}
    return {
        "ok": _coerce_bool(normalized.get("ok"), bool(transcript)),
        "transcript": str(normalized.get("transcript") or transcript or "").strip(),
        "semanticSummary": str(normalized.get("semanticSummary") or "").strip(),
        "narrativeCore": str(normalized.get("narrativeCore") or "").strip(),
        "worldContext": str(normalized.get("worldContext") or "").strip(),
        "entities": [str(item).strip() for item in (normalized.get("entities") or []) if str(item).strip()],
        "impliedEvents": [str(item).strip() for item in (normalized.get("impliedEvents") or []) if str(item).strip()],
        "tone": str(normalized.get("tone") or "").strip(),
        "confidence": _safe_float(normalized.get("confidence"), 0.0),
        "hint": str(normalized.get("hint") or ("transcript_semantic_ok" if transcript else "no_asr_transcript")).strip(),
    }


def _build_audio_timeline_guidance(audio_analysis: dict[str, Any], audio_context: dict[str, Any]) -> dict[str, Any]:
    phrase_candidates = [
        {"timeSec": _safe_float((phrase or {}).get("end"), 0.0), "reason": "phrase_end", "weight": 10}
        for phrase in (audio_analysis.get("phrases") or [])
        if _safe_float((phrase or {}).get("end"), -1) > 0
    ]
    pause_candidates = [
        {"timeSec": _safe_float((pause or {}).get("start"), 0.0), "reason": "pause", "weight": 9}
        for pause in (audio_analysis.get("pauseWindows") or [])
        if _safe_float((pause or {}).get("start"), -1) > 0
    ]
    energy_candidates = [
        {"timeSec": _safe_float((transition or {}).get("timeSec"), 0.0), "reason": "energy", "weight": 5}
        for transition in (audio_analysis.get("energyTransitions") or [])
        if _safe_float((transition or {}).get("timeSec"), -1) > 0
    ]
    section_candidates: list[dict[str, Any]] = []
    for section in (audio_analysis.get("sections") or []):
        start = _safe_float((section or {}).get("start"), -1)
        end = _safe_float((section or {}).get("end"), -1)
        if start > 0:
            section_candidates.append({"timeSec": start, "reason": "section_start", "weight": 6})
        if end > 0:
            section_candidates.append({"timeSec": end, "reason": "section_end", "weight": 7})
    return {
        "timelineSource": "audio",
        "segmentationMode": "phrase-first",
        "sourceMode": audio_context.get("sourceMode"),
        "phraseCandidates": phrase_candidates,
        "pauseCandidates": pause_candidates,
        "energyCandidates": energy_candidates,
        "sectionCandidates": section_candidates,
        "hints": [
            "audio is source of timing truth",
            "align boundaries to phrase endings and pauses",
            "use section and energy transitions as secondary boundaries",
        ],
    }


def _build_phrase_first_segmentation_guidance(audio_analysis: dict[str, Any], audio_context: dict[str, Any]) -> dict[str, Any]:
    guidance = _build_audio_timeline_guidance(audio_analysis, audio_context)
    all_candidates = [
        *guidance.get("phraseCandidates", []),
        *guidance.get("pauseCandidates", []),
        *guidance.get("energyCandidates", []),
        *guidance.get("sectionCandidates", []),
    ]
    ordered = sorted(
        all_candidates,
        key=lambda item: (_safe_float(item.get("timeSec"), 0.0), -int(item.get("weight") or 0)),
    )
    dedup: list[dict[str, Any]] = []
    for item in ordered:
        t = _safe_float(item.get("timeSec"), -1)
        if t <= 0:
            continue
        if dedup and abs(_safe_float(dedup[-1].get("timeSec"), -99) - t) < 0.35:
            if int(item.get("weight") or 0) > int(dedup[-1].get("weight") or 0):
                dedup[-1] = item
            continue
        dedup.append(item)
    guidance["boundaryCandidates"] = dedup[:80]
    return guidance


def _resolve_effective_role_type_by_role(payload: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], bool]:
    explicit_map = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    effective: dict[str, str] = {}
    source_map: dict[str, str] = {}
    role_override_applied = False
    for role in ("character_1", "character_2", "character_3"):
        explicit = str(explicit_map.get(role) or "").strip().lower()
        if explicit in ALLOWED_EXPLICIT_ROLE_TYPES and explicit != "auto":
            effective[role] = explicit
            source_map[role] = "explicit"
            role_override_applied = True
            continue
        source_map[role] = "default"
    if not any(value == "hero" for value in effective.values()):
        effective.setdefault("character_1", "hero")
        source_map["character_1"] = source_map.get("character_1") if source_map.get("character_1") == "explicit" else "default"
    return effective, source_map, role_override_applied


def _is_audio_connected(payload: dict[str, Any]) -> bool:
    audio_context = _normalize_audio_context(payload)
    source_origin = str(audio_context.get("sourceOrigin") or "connected").strip().lower()
    return bool(audio_context.get("hasAudio") and source_origin in {"connected", "audio_node", "audio_upload", "audio_generated"})


def _estimate_text_overlap(text: str, anchor: str) -> float:
    base = re.findall(r"[a-zA-Zа-яА-Я0-9_]+", str(text or "").lower())
    ref = set(re.findall(r"[a-zA-Zа-яА-Я0-9_]+", str(anchor or "").lower()))
    if not base or not ref:
        return 0.0
    shared = sum(1 for token in base if token in ref)
    return round(shared / max(1, len(base)), 4)


def _build_director_output(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> dict[str, Any]:
    role_labels = _build_reference_role_map(payload)
    history = {
        "summary": storyboard_out.story_summary,
        "fullScenario": storyboard_out.full_scenario,
        "characterRoles": _build_character_roles(payload, role_labels),
        "toneStyleDirection": str(payload.get("director_controls", {}).get("styleProfile") or "").strip() or "Scenario Director tone guidance from Gemini.",
        "directorSummary": storyboard_out.director_summary,
    }
    scenes = []
    video = []
    sound = []
    for scene in storyboard_out.scenes:
        participants = _scene_participants(scene, role_labels)
        scene_item = {
            "sceneId": scene.scene_id,
            "title": scene.scene_id,
            "timeStart": scene.time_start,
            "timeEnd": scene.time_end,
            "duration": scene.duration,
            "participants": participants,
            "location": scene.location,
            "props": scene.props,
            "action": scene.action_in_frame,
            "emotion": scene.emotion,
            "sceneGoal": scene.scene_goal,
            "frameDescription": scene.frame_description,
            "actionInFrame": scene.action_in_frame,
            "cameraIdea": scene.camera,
            "imagePrompt": scene.image_prompt,
            "videoPrompt": scene.video_prompt,
            "ltxMode": scene.ltx_mode,
            "whyThisMode": scene.ltx_reason,
            "startFrameSource": scene.start_frame_source,
            "needsTwoFrames": scene.needs_two_frames,
            "continuation": scene.continuation_from_previous,
            "narrationMode": scene.narration_mode,
            "localPhrase": scene.local_phrase,
            "sfx": scene.sfx,
            "soundNotes": scene.sfx,
            "pauseDuckSilenceNotes": "",
            "musicMixHint": scene.music_mix_hint,
        }
        scenes.append(scene_item)
        video.append(
            {
                "sceneId": scene.scene_id,
                "frameDescription": scene.frame_description,
                "actionInFrame": scene.action_in_frame,
                "cameraIdea": scene.camera,
                "imagePrompt": scene.image_prompt,
                "videoPrompt": scene.video_prompt,
                "ltxMode": scene.ltx_mode,
                "whyThisMode": scene.ltx_reason,
                "startFrameSource": scene.start_frame_source,
                "needsTwoFrames": scene.needs_two_frames,
                "continuation": scene.continuation_from_previous,
            }
        )
        sound.append(
            {
                "sceneId": scene.scene_id,
                "narrationMode": scene.narration_mode,
                "localPhrase": scene.local_phrase,
                "sfx": scene.sfx,
                "soundNotes": scene.sfx,
                "pauseDuckSilenceNotes": "",
            }
        )
    music = {
        "globalMusicPrompt": storyboard_out.music_prompt,
        "mood": str(payload.get("director_controls", {}).get("styleProfile") or "").strip(),
        "style": f"{payload.get('director_controls', {}).get('contentType') or ''} / {payload.get('director_controls', {}).get('styleProfile') or ''}".strip(" /"),
        "pacingHints": "Use the Gemini scene pacing to build intro, escalation, climax, and resolution.",
    }
    return {
        "history": history,
        "scenes": scenes,
        "video": video,
        "sound": sound,
        "music": music,
    }


def _build_brain_package(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    role_labels = _build_reference_role_map(payload)
    entities = [label for _, label in sorted(role_labels.items())]
    return {
        "contentType": controls.get("contentType") or "story",
        "contentTypeLabel": controls.get("contentType") or "story",
        "styleProfile": controls.get("styleProfile") or "realistic",
        "styleLabel": controls.get("styleProfile") or "realistic",
        "sourceMode": str(source.get("source_mode") or "audio").upper(),
        "sourceOrigin": str(source.get("source_origin") or payload.get("sourceOrigin") or "connected"),
        "sourceLabel": source.get("source_mode") or "audio",
        "sourcePreview": source.get("source_preview") or source.get("source_value") or "",
        "connectedContext": summary,
        "entities": entities,
        "sceneLogic": [scene.scene_goal or scene.frame_description or scene.action_in_frame for scene in storyboard_out.scenes],
        "audioStrategy": storyboard_out.voice_script or storyboard_out.music_prompt,
        "directorNote": controls.get("directorNote") or "",
    }


def _estimate_narrative_bias(
    payload: dict[str, Any],
    storyboard_out: ScenarioDirectorStoryboardOut,
    *,
    audio_connected: bool,
    prefer_audio_over_text: bool,
) -> tuple[str, float, float, list[str]]:
    warnings: list[str] = []
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    director_note = str(controls.get("directorNote") or "").strip()
    text_hint_present = bool(director_note)
    aggregate_story = " ".join(
        [
            storyboard_out.story_summary,
            storyboard_out.full_scenario,
            storyboard_out.director_summary,
            " ".join(scene.scene_goal for scene in storyboard_out.scenes),
            " ".join(scene.action_in_frame for scene in storyboard_out.scenes),
        ]
    )
    text_overlap = _estimate_text_overlap(aggregate_story, director_note) if text_hint_present else 0.0
    audio_influence = 0.75 if audio_connected else 0.3
    if prefer_audio_over_text and audio_connected:
        audio_influence = 0.9
    text_influence = min(1.0, 0.2 + text_overlap) if text_hint_present else 0.0
    if audio_connected and prefer_audio_over_text and text_overlap >= 0.6:
        warnings.append("scenario_may_be_text_led_not_audio_led")
    if audio_influence - text_influence >= 0.2:
        return "audio", text_influence, audio_influence, warnings
    if text_influence - audio_influence >= 0.2:
        return "text", text_influence, audio_influence, warnings
    return "mixed", text_influence, audio_influence, warnings


def _apply_scene_count_limit(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if len(storyboard_out.scenes) > 20:
        storyboard_out.scenes = storyboard_out.scenes[:20]
    return storyboard_out


def _scene_text_bundle(scene: ScenarioDirectorScene) -> str:
    return " | ".join(
        part
        for part in [
            scene.scene_goal,
            scene.frame_description,
            scene.action_in_frame,
            scene.camera,
            scene.image_prompt,
            scene.video_prompt,
            scene.ltx_reason,
        ]
        if str(part or "").strip()
    ).strip()


def _count_scene_signal_hits(bundle: str, markers: tuple[str, ...]) -> int:
    return sum(1 for marker in markers if marker in bundle)


def _scene_specificity_score(scene: ScenarioDirectorScene) -> int:
    score = 0
    bundle = _scene_text_bundle(scene).lower()
    object_markers = (
        "alarm key", "hatch", "beam", "door", "window", "mirror", "monitor", "console", "switch", "helmet",
        "hand", "face", "dust", "light", "shadow", "blood", "machine", "siren", "corridor", "stair",
    )
    action_markers = (
        "freezes", "opens", "shuts", "turns", "presses", "reaches", "stares", "flinches", "steps", "grips",
        "pulls", "reveals", "cuts out", "pulses", "cracks", "ignites", "crosses", "locks", "unlocks",
    )
    camera_markers = (
        "close-up", "locked frontal", "wide", "overhead", "profile", "insert", "tracking", "dolly", "push-in",
        "static", "macro", "two-shot", "over-the-shoulder", "silhouette",
    )
    sensory_markers = (
        "red", "blue", "neon", "steam", "dust", "beam", "glow", "hum", "siren", "pulse", "echo", "flicker",
    )
    if len(bundle) >= 70:
        score += 1
    if len(scene.frame_description.split()) >= 4:
        score += 1
    if len(scene.action_in_frame.split()) >= 2:
        score += 1
    if len(scene.camera.split()) >= 2:
        score += 1
    if scene.location or scene.props:
        score += 1
    if _count_scene_signal_hits(bundle, object_markers) >= 1:
        score += 1
    if _count_scene_signal_hits(bundle, action_markers) >= 1:
        score += 1
    if _count_scene_signal_hits(bundle, camera_markers) >= 1:
        score += 1
    if _count_scene_signal_hits(bundle, sensory_markers) >= 1:
        score += 1
    return score


def _scene_weak_assessment(scene: ScenarioDirectorScene) -> tuple[bool, str]:
    bundle = _scene_text_bundle(scene).lower()
    specificity = _scene_specificity_score(scene)
    directing_fields = sum(
        1
        for field in (scene.frame_description, scene.action_in_frame, scene.camera)
        if str(field or "").strip()
    )
    if any(pattern in bundle for pattern in WEAK_SCENE_PATTERNS):
        return True, "generic"
    if directing_fields == 0:
        return True, "missing_directing"
    if specificity >= 4:
        return False, "short_but_specific" if len(bundle) < 80 else "specific"
    if len(bundle) < 28 and specificity < 2:
        return True, "too_thin"
    if directing_fields < 2 and specificity < 3:
        return True, "missing_directing"
    if len(bundle) < 55 and specificity < 3:
        return True, "vague"
    if specificity < 2:
        return True, "vague"
    return False, "specific"


def _is_scene_weak(scene: ScenarioDirectorScene) -> bool:
    weak, _ = _scene_weak_assessment(scene)
    return weak


def _infer_scene_purpose(scene: ScenarioDirectorScene) -> str:
    bundle = _scene_text_bundle(scene).lower()
    if scene.continuation_from_previous:
        return "transition"
    if any(token in bundle for token in ("final", "last image", "aftertaste", "hold", "lingers", "lingering")):
        return "final image / ending hold"
    if any(token in bundle for token in ("climax", "breaks", "collapse", "scream", "impact", "erupts", "peak")):
        return "emotional climax"
    if any(token in bundle for token in ("reveal", "door opens", "discovers", "unveils", "transformation", "new space")):
        return "reveal"
    if any(token in bundle for token in ("confronts", "faces", "standoff", "conflict")):
        return "confrontation"
    if any(token in bundle for token in ("intrudes", "wrong", "glitch", "alarm", "destabil")):
        return "destabilization"
    if any(token in bundle for token in ("arrives", "enters", "descends", "steps into")):
        return "entry"
    if any(token in bundle for token in ("wide tableau", "iconic image", "silhouette", "burning frame")):
        return "peak image"
    if any(token in bundle for token in ("escalates", "rushes", "tightens", "rises", "closing in")):
        return "escalation"
    return "hook" if scene.time_start <= 0.1 else "transition"


def _repair_missing_scene_goal(scene: ScenarioDirectorScene) -> ScenarioDirectorScene:
    goal = str(scene.scene_goal or "").strip().lower()
    if goal and goal not in GENERIC_SCENE_GOALS:
        return scene
    scene.scene_goal = _infer_scene_purpose(scene)
    return scene


def _filter_or_repair_weak_scenes(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if not storyboard_out.scenes:
        return storyboard_out
    kept: list[ScenarioDirectorScene] = []
    weak_count = 0
    allow_repair_only = len(storyboard_out.scenes) <= 3
    for scene in storyboard_out.scenes:
        scene = _repair_missing_scene_goal(scene)
        weak, reason = _scene_weak_assessment(scene)
        if not weak:
            if reason == "short_but_specific":
                logger.debug("[SCENARIO_DIRECTOR] weak scene kept short_but_specific scene_id=%s", scene.scene_id)
            kept.append(scene)
            continue
        weak_count += 1
        logger.debug("[SCENARIO_DIRECTOR] weak scene detected scene_id=%s reason=%s", scene.scene_id, reason)
        if allow_repair_only or len(storyboard_out.scenes) - weak_count < 1:
            if not scene.scene_goal or scene.scene_goal.lower() in GENERIC_SCENE_GOALS:
                scene.scene_goal = _infer_scene_purpose(scene)
            kept.append(scene)
            continue
        if _scene_specificity_score(scene) >= 4:
            kept.append(scene)
    if not kept:
        raise ScenarioDirectorError(
            "scenario_director_empty_after_filter",
            "Scenario Director filtered out all scenes after quality checks.",
            status_code=502,
        )
    if weak_count:
        logger.debug("[SCENARIO_DIRECTOR] weak scenes filtered count=%s", max(0, len(storyboard_out.scenes) - len(kept)))
    storyboard_out.scenes = kept
    return storyboard_out


def _normalize_scene_timeline(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    previous_end = 0.0
    for scene in storyboard_out.scenes:
        original_end = _safe_float(scene.time_end, scene.time_start)
        original_duration = _safe_float(scene.duration, max(0.0, original_end - scene.time_start))
        scene.time_start = max(_safe_float(scene.time_start, previous_end), previous_end)
        if original_end < scene.time_start:
            original_end = round(scene.time_start + max(0.0, original_duration), 3)
        scene.time_end = max(original_end, scene.time_start)
        scene.duration = round(max(0.0, scene.time_end - scene.time_start), 3)
        previous_end = scene.time_end
    return storyboard_out


def _limit_lip_sync_usage(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    lip_sync_seen = 0
    for scene in storyboard_out.scenes:
        if scene.ltx_mode != "lip_sync":
            continue
        lip_sync_seen += 1
        if lip_sync_seen <= 3:
            continue
        scene.ltx_mode = "i2v_as"
        original_reason = str(scene.ltx_reason or "").strip()
        replacement_reason = "Normalized from lip_sync to i2v_as because Scenario Director allows at most 3 lip_sync scenes per output."
        scene.ltx_reason = f"{original_reason}; {replacement_reason}" if original_reason else replacement_reason
    return storyboard_out


def _detect_expected_character_roles(payload: dict[str, Any]) -> list[str]:
    effective_role_types, source_by_role, _ = _resolve_effective_role_type_by_role(payload)
    explicit_roles = [
        role
        for role in ("character_1", "character_2", "character_3")
        if source_by_role.get(role) == "explicit" and effective_role_types.get(role) in {"hero", "support", "antagonist"}
    ]
    if explicit_roles:
        return explicit_roles[:2]
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    expected: list[str] = []
    for role in ("character_1", "character_2", "character_3"):
        item = refs.get(role)
        if not isinstance(item, dict):
            continue
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        connected = _coerce_bool(meta.get("connected"), False)
        ref_count = len(item.get("refs") or [])
        count = int(item.get("count") or 0)
        if connected or ref_count > 0 or count > 0:
            expected.append(role)
    if len(expected) >= 2:
        return expected[:2]
    implied = connected_summary.get("entities") if isinstance(connected_summary.get("entities"), list) else []
    for role in ("character_1", "character_2"):
        if role in expected:
            continue
        if any(role in str(entity or "") for entity in implied):
            expected.append(role)
    return expected[:2]


def _enforce_explicit_role_assignments(payload: dict[str, Any], storyboard_out: ScenarioDirectorStoryboardOut) -> tuple[ScenarioDirectorStoryboardOut, list[str]]:
    effective_role_types, source_by_role, _ = _resolve_effective_role_type_by_role(payload)
    explicit_roles = [
        role
        for role in ("character_1", "character_2", "character_3")
        if source_by_role.get(role) == "explicit" and effective_role_types.get(role) in {"hero", "support", "antagonist"}
    ]
    if not explicit_roles:
        return storyboard_out, []
    warnings: list[str] = []
    role_presence = {role: False for role in explicit_roles}
    for scene in storyboard_out.scenes:
        for role in explicit_roles:
            if role in scene.actors:
                role_presence[role] = True
    for role in explicit_roles:
        if role_presence.get(role):
            continue
        target_scene = storyboard_out.scenes[0] if storyboard_out.scenes else None
        if target_scene:
            target_scene.actors.append(role)
            role_presence[role] = True
            warnings.append(f"explicit_role_repaired:{role}")
    return storyboard_out, warnings


def _character_lock_candidate_score(scene: ScenarioDirectorScene, *, scene_index: int, total_scenes: int, companion_roles: set[str]) -> int:
    bundle = _scene_text_bundle(scene).lower()
    purpose = _infer_scene_purpose(scene)
    score = 0
    if purpose in {"reveal", "confrontation", "escalation", "destabilization"}:
        score += 3
    if any(token in bundle for token in ("reveal", "confront", "exchange", "reaction", "watches", "faces", "shared", "between", "answer", "responds", "alarm", "tension")):
        score += 2
    if any(actor in companion_roles for actor in scene.actors):
        score += 2
    if any(token in bundle for token in ("close-up", "isolated", "alone", "empty hallway", "environment only", "abstract", "held image", "lingers on")):
        score -= 3
    if scene_index == 0 and purpose == "hook":
        score -= 2
    if scene_index == total_scenes - 1 and purpose == "final image / ending hold":
        score -= 2
    if len(scene.actors) <= 1 and any(token in bundle for token in ("close-up", "macro", "portrait", "hand", "face")):
        score -= 2
    return score


def _enforce_character_lock(payload: dict[str, Any], storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    expected_roles = _detect_expected_character_roles(payload)
    if len(expected_roles) < 2 or not storyboard_out.scenes:
        return storyboard_out
    role_set = set(expected_roles)
    present_roles = {actor for scene in storyboard_out.scenes for actor in scene.actors if actor in role_set}
    missing_roles = [role for role in expected_roles if role not in present_roles]
    if not missing_roles:
        return storyboard_out
    total_scenes = len(storyboard_out.scenes)
    for role in missing_roles:
        companion_roles = role_set - {role}
        candidates: list[tuple[int, int]] = []
        for index, scene in enumerate(storyboard_out.scenes):
            score = _character_lock_candidate_score(scene, scene_index=index, total_scenes=total_scenes, companion_roles=companion_roles)
            if score >= 3:
                candidates.append((score, index))
        if not candidates:
            logger.debug("[SCENARIO_DIRECTOR] character lock skipped no_candidate role=%s", role)
            continue
        candidates.sort(key=lambda item: (-item[0], item[1]))
        repaired_indexes: list[str] = []
        max_repairs = 1 if total_scenes <= 4 else 2
        for _, index in candidates[:max_repairs]:
            scene = storyboard_out.scenes[index]
            if role not in scene.actors:
                scene.actors.append(role)
            if not scene.scene_goal or scene.scene_goal.lower() in GENERIC_SCENE_GOALS:
                purpose = _infer_scene_purpose(scene)
                if purpose in {"reveal", "confrontation", "escalation", "destabilization"}:
                    scene.scene_goal = purpose
                elif any(actor in companion_roles for actor in scene.actors):
                    scene.scene_goal = "interaction"
            repaired_indexes.append(scene.scene_id)
        if repaired_indexes:
            logger.debug("[SCENARIO_DIRECTOR] character lock repaired role=%s scenes=%s", role, ",".join(repaired_indexes))
        else:
            logger.debug("[SCENARIO_DIRECTOR] character lock skipped no_candidate role=%s", role)
    return storyboard_out


def _has_overly_uniform_timing(storyboard_out: ScenarioDirectorStoryboardOut) -> bool:
    if len(storyboard_out.scenes) < 3:
        return False
    durations = [max(0.1, _safe_float(scene.duration, scene.time_end - scene.time_start)) for scene in storyboard_out.scenes]
    spread = max(durations) - min(durations)
    average = sum(durations) / len(durations)
    max_deviation = max(abs(value - average) for value in durations)
    repeated_fives = sum(1 for value in durations if abs(value - 5.0) <= 0.15)
    half_second_buckets = {round(value * 2) / 2 for value in durations}
    return (spread <= 0.45 and max_deviation <= 0.3) or (len(half_second_buckets) <= 2 and spread <= 0.6) or (repeated_fives >= max(3, len(durations) - 1) and spread <= 0.8)


def _apply_timing_variation(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if not _has_overly_uniform_timing(storyboard_out):
        logger.debug("[SCENARIO_DIRECTOR] timing variation skipped already_good")
        return storyboard_out
    scene_count = len(storyboard_out.scenes)
    if scene_count < 2:
        logger.debug("[SCENARIO_DIRECTOR] timing variation skipped already_good")
        return storyboard_out
    durations = [max(1.2, _safe_float(scene.duration, scene.time_end - scene.time_start)) for scene in storyboard_out.scenes]
    deltas = [0.0] * scene_count
    purposes = [_infer_scene_purpose(scene) for scene in storyboard_out.scenes]
    if durations[0] >= 2.2 and purposes[0] not in {"emotional climax", "final image / ending hold"}:
        deltas[0] -= min(0.35, durations[0] - 1.8)
    if purposes[-1] in {"reveal", "emotional climax", "peak image", "final image / ending hold"} or durations[-1] <= max(durations[:-1]):
        deltas[-1] += 0.35
    if scene_count >= 4 and purposes[-2] in {"reveal", "emotional climax", "confrontation", "escalation"}:
        deltas[-2] += 0.2
    positive_total = round(sum(value for value in deltas if value > 0), 3)
    negative_total = round(-sum(value for value in deltas if value < 0), 3)
    remaining = round(positive_total - negative_total, 3)
    compensation_indexes = [
        index
        for index in range(1, max(1, scene_count - 1))
        if durations[index] > 1.8 and purposes[index] not in {"emotional climax", "final image / ending hold"}
    ]
    if remaining > 0 and compensation_indexes:
        per_scene = min(0.2, round(remaining / len(compensation_indexes), 3))
        for index in compensation_indexes:
            available = max(0.0, durations[index] - 1.4)
            reduction = min(per_scene, available)
            deltas[index] -= reduction
            remaining = round(remaining - reduction, 3)
            if remaining <= 0.02:
                break
    elif remaining < 0 and compensation_indexes:
        deltas[compensation_indexes[len(compensation_indexes) // 2]] += abs(remaining)
    adjusted = [round(max(1.2, duration + delta), 3) for duration, delta in zip(durations, deltas)]
    total_diff = round(sum(durations) - sum(adjusted), 3)
    if abs(total_diff) > 0.01:
        rebalance_index = compensation_indexes[0] if compensation_indexes else min(max(1, scene_count // 2), scene_count - 1)
        adjusted[rebalance_index] = round(max(1.2, adjusted[rebalance_index] + total_diff), 3)
    if max(abs(adjusted[index] - durations[index]) for index in range(scene_count)) < 0.15:
        logger.debug("[SCENARIO_DIRECTOR] timing variation skipped already_good")
        return storyboard_out
    cursor = 0.0
    for scene, duration in zip(storyboard_out.scenes, adjusted):
        scene.time_start = round(cursor, 3)
        scene.duration = duration
        scene.time_end = round(scene.time_start + scene.duration, 3)
        cursor = scene.time_end
    delta_total = round(sum(abs(adjusted[index] - durations[index]) for index in range(scene_count)), 3)
    logger.debug("[SCENARIO_DIRECTOR] timing variation applied delta_total=%s", delta_total)
    return storyboard_out


def _scene_has_transition_evidence(scene: ScenarioDirectorScene) -> bool:
    bundle = _scene_text_bundle(scene).lower()
    return any(
        token in bundle
        for token in (
            "door opens", "opens one inch", "entering a new", "new chamber", "new room", "crosses the threshold",
            "reveal", "reveals", "unveils", "transformation", "before", "after", "state shift", "activation",
            "activates", "ignites", "powers on", "hatch opens", "crosses into", "discovers",
        )
    )


def _scene_has_audio_reactive_evidence(scene: ScenarioDirectorScene) -> bool:
    bundle = _scene_text_bundle(scene).lower()
    return any(
        token in bundle
        for token in (
            "pulses", "pulse", "breathing machinery", "machinery", "hum", "audio-reactive", "lights flicker",
            "rhythmic", "throb", "alarm", "siren", "vibration", "subtle response",
        )
    )


def _recover_ltx_mode(scene: ScenarioDirectorScene) -> str:
    if _scene_has_transition_evidence(scene):
        return "f_l_as" if _scene_has_audio_reactive_evidence(scene) else "f_l"
    if _scene_has_audio_reactive_evidence(scene):
        return "i2v_as"
    return "i2v"


def _rebalance_ltx_modes(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    if not storyboard_out.scenes:
        return storyboard_out
    continuation_total = sum(
        1 for scene in storyboard_out.scenes if scene.ltx_mode == "continuation" or scene.continuation_from_previous or scene.start_frame_source == "previous_frame"
    )
    continuation_limit = max(2, len(storyboard_out.scenes) // 2)
    continuation_seen = 0
    for index, scene in enumerate(storyboard_out.scenes):
        original_mode = scene.ltx_mode
        target_mode = original_mode
        correction_reason = ""
        if original_mode == "lip_sync" and not _is_music_vocal_mode(scene.narration_mode):
            target_mode = "i2v_as"
            correction_reason = "visible articulation is unsupported by narration mode"
        elif original_mode == "continuation":
            continuation_seen += 1
            if index == 0:
                target_mode = _recover_ltx_mode(scene)
                correction_reason = "continuation cannot be the first scene"
            elif continuation_total > continuation_limit and continuation_seen > continuation_limit:
                target_mode = _recover_ltx_mode(scene)
                correction_reason = "continuation was overused"
        elif index == 0 and scene.start_frame_source == "previous_frame":
            target_mode = _recover_ltx_mode(scene)
            correction_reason = "first scene cannot inherit a previous frame"
        elif original_mode in {"f_l", "f_l_as"} and not scene.needs_two_frames and not _scene_has_transition_evidence(scene):
            target_mode = "i2v_as" if original_mode == "f_l_as" and _scene_has_audio_reactive_evidence(scene) else "i2v"
            correction_reason = "two-frame transition evidence is missing"
        elif original_mode == "i2v_as" and not _scene_has_audio_reactive_evidence(scene) and _scene_has_transition_evidence(scene) and scene.needs_two_frames:
            target_mode = "f_l_as"
            correction_reason = "transition evidence is stronger than ambient pulse"
        if target_mode != original_mode:
            scene.ltx_mode = target_mode
            scene.ltx_reason = _normalize_ltx_reason(f"Normalized to {target_mode}: {correction_reason}.", target_mode, narration_mode=scene.narration_mode)
            logger.debug("[SCENARIO_DIRECTOR] ltx normalize corrected scene_id=%s from=%s to=%s", scene.scene_id, original_mode, target_mode)
        else:
            scene.ltx_reason = _normalize_ltx_reason(scene.ltx_reason, scene.ltx_mode, narration_mode=scene.narration_mode)
            logger.debug("[SCENARIO_DIRECTOR] ltx normalize kept original scene_id=%s mode=%s", scene.scene_id, scene.ltx_mode)
        scene.needs_two_frames = scene.ltx_mode in {"f_l", "f_l_as"}
        if scene.ltx_mode == "continuation" and index > 0:
            scene.continuation_from_previous = True
            scene.start_frame_source = "previous_frame"
        elif scene.ltx_mode != "continuation":
            scene.continuation_from_previous = False
            if scene.start_frame_source == "previous_frame":
                scene.start_frame_source = "new"
    return storyboard_out


def _assert_storyboard_quality(storyboard_out: ScenarioDirectorStoryboardOut) -> None:
    scenes = storyboard_out.scenes or []
    if not scenes:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_empty_after_filter", "Scenario Director returned no usable scenes.", status_code=502)
    frame_count = sum(1 for scene in scenes if str(scene.frame_description or "").strip())
    action_count = sum(1 for scene in scenes if str(scene.action_in_frame or "").strip())
    camera_count = sum(1 for scene in scenes if str(scene.camera or "").strip())
    if frame_count == 0:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned scenes without frame_description.", status_code=502)
    if action_count == 0:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned scenes without action_in_frame.", status_code=502)
    if camera_count == 0:
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned scenes without camera direction.", status_code=502)
    strong_scene_count = 0
    generic_scene_count = 0
    directing_scene_count = 0
    for scene in scenes:
        weak, reason = _scene_weak_assessment(scene)
        directing_fields = sum(1 for field in (scene.frame_description, scene.action_in_frame, scene.camera) if str(field or "").strip())
        if directing_fields >= 2:
            directing_scene_count += 1
        if not weak or _scene_specificity_score(scene) >= 4:
            strong_scene_count += 1
        if reason == "generic":
            generic_scene_count += 1
    if directing_scene_count == 0 or strong_scene_count == 0 or generic_scene_count == len(scenes):
        logger.debug("[SCENARIO_DIRECTOR] quality assert failed")
        raise ScenarioDirectorError("scenario_director_low_quality", "Scenario Director returned only weak filler scenes.", status_code=502)


def _resolve_audio_duration_sec(payload: dict[str, Any]) -> float:
    duration, _ = _resolve_audio_duration_info(payload)
    return duration


def _validate_audio_timeline_coverage(scenes: list[ScenarioDirectorScene], audio_duration_sec: float, *, coverage_source: str = "fallback") -> dict[str, Any]:
    if audio_duration_sec <= 0:
        return {
            "audioDurationSec": 0.0,
            "expectedAudioDurationSec": 0.0,
            "actualCoveredDurationSec": 0.0,
            "coverageSource": "missing",
            "timelineStartSec": 0.0,
            "timelineEndSec": 0.0,
            "timelineCoverageSec": 0.0,
            "timelineCoverageRatio": None,
            "coverageRatio": None,
            "uncoveredTailSec": 0.0,
            "internalGapCount": 0,
            "timelineCoverageStatus": "ok",
            "warnings": [],
        }
    if not scenes:
        return {
            "audioDurationSec": audio_duration_sec,
            "expectedAudioDurationSec": audio_duration_sec,
            "actualCoveredDurationSec": 0.0,
            "coverageSource": coverage_source,
            "timelineStartSec": 0.0,
            "timelineEndSec": 0.0,
            "timelineCoverageSec": 0.0,
            "timelineCoverageRatio": 0.0,
            "coverageRatio": 0.0,
            "uncoveredTailSec": audio_duration_sec,
            "internalGapCount": 0,
            "timelineCoverageStatus": "invalid",
            "warnings": ["timeline_coverage_too_short", "timeline_does_not_reach_audio_end", "timeline_has_uncovered_audio_tail"],
        }
    sorted_scenes = sorted(scenes, key=lambda scene: (scene.time_start, scene.time_end))
    first_start = _safe_float(sorted_scenes[0].time_start, 0.0)
    last_end = _safe_float(sorted_scenes[-1].time_end, first_start)
    total_coverage = 0.0
    gap_count = 0
    warnings: list[str] = []
    cursor = 0.0
    for scene in sorted_scenes:
        start = _safe_float(scene.time_start, cursor)
        end = max(start, _safe_float(scene.time_end, start))
        gap = round(max(0.0, start - cursor), 3)
        if gap >= TIMELINE_INTERNAL_GAP_WARN_SEC:
            gap_count += 1
            warnings.append("timeline_has_large_internal_gap")
        total_coverage += max(0.0, end - start)
        cursor = max(cursor, end)
    uncovered_tail = round(max(0.0, audio_duration_sec - last_end), 3)
    coverage_ratio = round((total_coverage / audio_duration_sec) if audio_duration_sec > 0 else 0.0, 4)
    if first_start > TIMELINE_START_TOLERANCE_SEC:
        warnings.append("timeline_does_not_start_at_zero")
    if audio_duration_sec - last_end > TIMELINE_END_TOLERANCE_SEC:
        warnings.append("timeline_does_not_reach_audio_end")
    if uncovered_tail > TIMELINE_TAIL_WARN_SEC:
        warnings.append("timeline_has_uncovered_audio_tail")
    if coverage_ratio < TIMELINE_COVERAGE_RATIO_WARN:
        warnings.append("timeline_coverage_too_short")
    status = "ok"
    unique_warnings = list(dict.fromkeys(warnings))
    if any(code in unique_warnings for code in ("timeline_does_not_reach_audio_end", "timeline_has_uncovered_audio_tail", "timeline_coverage_too_short")):
        status = "invalid"
    elif unique_warnings:
        status = "warning"
    return {
        "audioDurationSec": round(audio_duration_sec, 3),
        "expectedAudioDurationSec": round(audio_duration_sec, 3),
        "actualCoveredDurationSec": round(total_coverage, 3),
        "coverageSource": coverage_source,
        "timelineStartSec": round(first_start, 3),
        "timelineEndSec": round(last_end, 3),
        "timelineCoverageSec": round(total_coverage, 3),
        "timelineCoverageRatio": coverage_ratio,
        "coverageRatio": coverage_ratio,
        "uncoveredTailSec": uncovered_tail,
        "internalGapCount": gap_count,
        "timelineCoverageStatus": status,
        "warnings": unique_warnings,
    }


def _harden_storyboard_out(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> ScenarioDirectorStoryboardOut:
    storyboard_out = _apply_scene_count_limit(storyboard_out)
    storyboard_out = _filter_or_repair_weak_scenes(storyboard_out)
    storyboard_out = _enforce_character_lock(payload, storyboard_out)
    storyboard_out, _ = _enforce_explicit_role_assignments(payload, storyboard_out)
    storyboard_out = _apply_timing_variation(storyboard_out)
    storyboard_out = _rebalance_ltx_modes(storyboard_out)
    storyboard_out = _normalize_scene_timeline(storyboard_out)
    storyboard_out = _limit_lip_sync_usage(storyboard_out)
    _assert_storyboard_quality(storyboard_out)
    return storyboard_out


def _validate_scene_audio_grounding(scene: ScenarioDirectorScene, audio_context: dict[str, Any]) -> list[str]:
    risks: list[str] = []
    usage_text = str(scene.what_from_audio_this_scene_uses or "").strip().lower()
    usage_words = {word for word in re.findall(r"[a-z]+", usage_text)}
    if usage_words and usage_words.issubset(ABSTRACT_AUDIO_ONLY_WORDS):
        risks.append("abstract_audio_usage")
    elif usage_words and usage_words.intersection(ABSTRACT_AUDIO_ONLY_WORDS):
        context = audio_context if isinstance(audio_context, dict) else {}
        dynamic_sources: list[str] = []
        audio_understanding = context.get("audioUnderstanding")
        if isinstance(audio_understanding, dict):
            dynamic_sources.append(str(audio_understanding.get("mainTopic") or ""))
            dynamic_sources.append(str(audio_understanding.get("worldContext") or ""))
            implied_events = audio_understanding.get("impliedEvents")
            if isinstance(implied_events, list):
                dynamic_sources.extend(str(item or "") for item in implied_events)
        dynamic_sources.extend(
            [
                str(context.get("mainTopic") or ""),
                str(context.get("worldContext") or ""),
            ]
        )
        implied_events = context.get("impliedEvents")
        if isinstance(implied_events, list):
            dynamic_sources.extend(str(item or "") for item in implied_events)

        dynamic_keywords = {
            token
            for token in re.findall(r"[a-z]+", " ".join(dynamic_sources).lower())
            if len(token) > 3 and token not in ABSTRACT_AUDIO_ONLY_WORDS
        }
        concrete_keyword_set = set(CONCRETE_AUDIO_HINT_WORDS).union(dynamic_keywords)
        has_concrete_anchor = bool(usage_words.intersection(concrete_keyword_set))
        if not has_concrete_anchor:
            risks.append("abstract_audio_usage")

    anchor_evidence = str(scene.audio_anchor_evidence or "").strip()
    if not anchor_evidence:
        risks.append("missing_audio_anchor_evidence")
    elif len(anchor_evidence) < 10:
        risks.append("weak_audio_anchor")

    context = audio_context if isinstance(audio_context, dict) else {}
    boundary_reason = str(scene.boundary_reason or "").strip().lower()
    if boundary_reason == "phrase" and not (context.get("phrases") or []):
        risks.append("invalid_phrase_boundary")
    if boundary_reason == "pause" and not (context.get("pauseWindows") or []):
        risks.append("invalid_pause_boundary")
    if boundary_reason == "energy" and not (context.get("energyTransitions") or []):
        risks.append("invalid_energy_boundary")

    if _safe_float(scene.confidence, 0.5) < 0.4:
        risks.append("low_scene_confidence")
    return list(dict.fromkeys(risks))


def _validate_world_consistency(scene: ScenarioDirectorScene, audio_understanding: dict[str, Any]) -> list[str]:
    audio_data = audio_understanding if isinstance(audio_understanding, dict) else {}
    world_context = str(audio_data.get("worldContext") or "").strip().lower()
    main_topic = str(audio_data.get("mainTopic") or "").strip().lower()
    implied_events_raw = audio_data.get("impliedEvents")
    implied_events_text = " ".join(str(item or "") for item in implied_events_raw) if isinstance(implied_events_raw, list) else ""
    audio_world_text = " ".join([world_context, main_topic, implied_events_text]).strip()
    scene_location = str(scene.location or "").strip().lower()
    if not audio_world_text or not scene_location:
        return []
    if any(token in audio_world_text for token in WORLD_AUDIO_KEYWORDS) and any(token in scene_location for token in WORLD_MISMATCH_LOCATION_KEYWORDS):
        return ["world_mismatch"]
    return []


def _validate_audio_first_integrity(
    storyboard_out: ScenarioDirectorStoryboardOut,
    structured_planner_diagnostics: dict[str, Any],
    audio_analysis: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = structured_planner_diagnostics if isinstance(structured_planner_diagnostics, dict) else {}
    narrative_strategy = diagnostics.get("narrativeStrategy") if isinstance(diagnostics.get("narrativeStrategy"), dict) else {}
    planner_diagnostics = diagnostics.get("diagnostics") if isinstance(diagnostics.get("diagnostics"), dict) else {}
    audio_understanding = diagnostics.get("audioUnderstanding") if isinstance(diagnostics.get("audioUnderstanding"), dict) else {}
    audio_context = audio_analysis if isinstance(audio_analysis, dict) else {}

    scene_risk_map: list[dict[str, Any]] = []
    scene_risk_total = 0.0
    for scene in storyboard_out.scenes:
        scene_risks = _validate_scene_audio_grounding(scene, audio_context)
        scene_risks.extend(_validate_world_consistency(scene, audio_understanding))
        scene_risks = list(dict.fromkeys(scene_risks))
        for risk in scene_risks:
            if risk in HIGH_SEVERITY_RISKS:
                scene_risk_total += 0.12
            elif risk in LOW_SEVERITY_RISKS:
                scene_risk_total += 0.06
            else:
                scene_risk_total += 0.08
        scene_risk_map.append({"sceneId": scene.scene_id, "risks": scene_risks})

    global_risks: list[str] = []
    if not _coerce_bool(narrative_strategy.get("didAudioRemainPrimary"), False):
        global_risks.append("didAudioRemainPrimary_false")
    if _coerce_bool(planner_diagnostics.get("usedAudioOnlyAsMood"), False):
        global_risks.append("usedAudioOnlyAsMood_true")
    if not _coerce_bool(planner_diagnostics.get("usedAudioAsContentSource"), False):
        global_risks.append("usedAudioAsContentSource_false")
    global_risks = list(dict.fromkeys(global_risks))

    score = 1.0 - scene_risk_total - (0.18 * len(global_risks))
    score = max(0.0, min(1.0, round(score, 3)))
    return {
        "sceneRiskMap": scene_risk_map,
        "globalRisks": global_risks,
        "score": score,
    }


def _build_request_text(
    payload: dict[str, Any],
    *,
    audio_context: dict[str, Any] | None = None,
    audio_analysis: dict[str, Any] | None = None,
    audio_guidance: dict[str, Any] | None = None,
    audio_semantics: dict[str, Any] | None = None,
    strict_json_retry: bool = False,
) -> str:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    director_controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    connected_context_summary = payload.get("connected_context_summary", {})
    metadata = payload.get("metadata", {})
    normalized_audio = audio_context if isinstance(audio_context, dict) else _normalize_audio_context(payload)
    runtime_analysis = audio_analysis if isinstance(audio_analysis, dict) else _build_audio_analysis_fallback(
        _safe_float(normalized_audio.get("audioDurationSec"), 0.0), "analysis_not_requested"
    )
    runtime_guidance = audio_guidance if isinstance(audio_guidance, dict) else {}
    runtime_semantics = audio_semantics if isinstance(audio_semantics, dict) else {}
    audio_duration_sec = _safe_float(
        runtime_analysis.get("audioDurationSec"),
        _safe_float(normalized_audio.get("audioDurationSec"), 0.0),
    )
    audio_duration_source = "analysis" if _safe_float(runtime_analysis.get("audioDurationSec"), 0.0) > 0 else str(normalized_audio.get("audioDurationSource") or "missing")
    source_mode = str(normalized_audio.get("sourceMode") or source.get("source_mode") or "").strip().lower()
    source_origin = str(normalized_audio.get("sourceOrigin") or source.get("source_origin") or payload.get("sourceOrigin") or "connected").strip().lower()
    audio_connected = bool(normalized_audio.get("hasAudio"))
    prefer_audio_over_text = _coerce_bool(normalized_audio.get("preferAudioOverText"), True)
    role_type_by_role = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    request_text = (
        "You are Scenario Director for PhotoStudio COMFY.\n"
        "Gemini is the planning brain. Do not delegate planning to heuristics.\n"
        "Return a single JSON object only. No markdown, no commentary.\n"
        "The storyboard_out must be production-usable for downstream Storyboard execution.\n"
        "SOURCE HIERARCHY (HARD, AUDIO MODE ONLY):\n"
        "1) AUDIO_CONTENT_TRUTH: defines story subject, world facts, implied events/context.\n"
        "2) AUDIO_TIMELINE_TRUTH: defines timing anchors (phrases, pauses, energy transitions, sections).\n"
        "3) DIRECTOR_NOTE_INTERPRETATION: emotional/relational lens only; never a content override.\n"
        "4) STYLE_TREATMENT: visual treatment only; does not define world facts.\n"
        "5) CHARACTER_REFS: who appears and role dynamics; does not replace audio world.\n"
        "CONFLICT POLICY (HARD):\n"
        "- If AUDIO meaning conflicts with DIRECTOR NOTE, preserve AUDIO meaning/world/events and reinterpret DIRECTOR NOTE inside that world.\n"
        "- Never replace a clear audio topic with generic romance or unrelated locations.\n"
        "- Never use audio as mood-only when audio already provides world/content facts.\n"
        "- If preferAudioOverText=true and audio/text conflict, audio MUST dominate.\n"
        "AUDIO CONTENT TRUTH RULE:\n"
        "- If audioSemantics.ok=true and audioSemantics.semanticSummary/worldContext/narrativeCore are present, they define the story subject, world facts, and implied events.\n"
        "- Director note may only reinterpret emotional/relationship dynamics inside that audio-defined world.\n"
        "- Director note must NOT replace audioSemantics topic/world.\n"
        "- audio timing signals define boundaries; audio semantics define meaning.\n"
        "FORBIDDEN:\n"
        "- director note as main subject when audio has stronger subject matter.\n"
        "- unrelated meet-cute/bar/date story when audio defines another world.\n"
        "- inventing unrelated world/location while clear audio world exists.\n"
        "TWO-STAGE OUTPUT LOGIC (SINGLE JSON):\n"
        "- First fill truth analysis blocks: audioUnderstanding -> conflictAnalysis -> narrativeStrategy.\n"
        "- Then produce story, scenes, diagnostics.\n"
        "- Every scene MUST prove audio usage with whatFromAudioThisSceneUses + audioAnchorEvidence + boundaryReason.\n"
        "MUST-USE SELF-CHECKS (REQUIRED IN OUTPUT):\n"
        "- narrativeStrategy.didAudioRemainPrimary\n"
        "- narrativeStrategy.didDirectorNoteOverrideAudio\n"
        "- audioUnderstanding.whatFromAudioDefinesWorld\n"
        "- story.howDirectorNoteWasIntegrated\n"
        "- diagnostics.usedAudioAsContentSource\n"
        "- diagnostics.usedAudioOnlyAsMood\n"
        "PLANNER MODES:\n"
        "- full_audio_first: audio meaning understood + usable timeline signals.\n"
        "- partial_audio_first: audio meaning partial but still primary world/content anchor.\n"
        "- text_fallback: only when audio truth is unavailable/unusable.\n"
        "- If audio world/topic is clear, director note must not capture story core even in partial mode.\n"
        "TEXT-ONLY DEGRADE:\n"
        "- If sourceMode is not AUDIO or audio unavailable, use normal text-led planning and set diagnostics/plannerMode accordingly.\n"
        "AUDIO-FIRST SEGMENTATION:\n"
        "- Do not build evenly spaced scenes when audio analysis exists.\n"
        "- Align boundaries to phrase endings first, pause windows second, then section/energy transitions.\n"
        "ANTI-FAKE AUDIO USAGE RULES:\n"
        "- whatFromAudioThisSceneUses MUST reference concrete elements (places, objects, events, actions).\n"
        "- Forbidden: vague words like 'mood', 'tension', 'feeling' without concrete audio-derived detail.\n"
        "- audioAnchorEvidence MUST reference either phrase meaning, pause, section, or a specific event described in audio.\n"
        "- If unsure, mark boundaryReason='fallback' and reduce confidence.\n"
        "ANTI-DRIFT LOCKS:\n"
        "- Preserve the exact count of core characters implied by the source and refs.\n"
        "- If two connected refs imply two women, keep two women unless the user explicitly changes that.\n"
        "- Do not collapse connected characters into generic operative/target/action archetypes.\n"
        "- Preserve relationship tension, emotional roles, gender presentation, and visual identity anchors from the refs.\n"
        "- Preserve implied genre: horror, claustrophobic tension, industrial dread, surreal unease, emotional darkness, intimacy, mystery.\n"
        "- Do not flatten unique tone into generic espionage thriller or safe corporate cinematic filler.\n"
        "- Preserve the environment identity from the source or refs: bunker, abyss, industrial shaft, abandoned corridor, concrete hall, strange facility, ritual room, flooded station, etc.\n"
        "SHORT-FORM DIRECTING RULES:\n"
        "- Think like a premium short-film director + trailer editor + music-video storyboard artist.\n"
        "- The first 1-3 seconds must hook immediately with a specific image, not vague mood text.\n"
        "- Every scene must contain a specific image idea, a physical action, camera intent, and dramatic purpose.\n"
        "- Every scene must either reveal, intensify, transform, or leave a memorable afterimage.\n"
        "- Prefer fewer strong scenes over many weak scenes.\n"
        "- Build escalation: hook -> entry/destabilization -> reveal/complication -> escalation -> peak image/emotional climax -> final image that stays in memory.\n"
        "- Avoid safe filler and generic thriller language; convert vague mood text into concrete story-specific visuals.\n"
        "- Preserve horror, intimacy, dread, surreal industrial tension, or other source-implied genre DNA.\n"
        "- Use the environment as an active dramatic force, not passive background.\n"
        "- Do not flatten unique story DNA into generic content.\n"
        "- If connected refs imply two key characters, build interplay, contrast, and relationship energy across the scenario.\n"
        "TIMING RULES:\n"
        "- Do not force evenly sliced 5-second blocks.\n"
        "- Typical useful scene duration is about 4-9 seconds, but hook, reveal, climax, and final hold may be shorter or longer when justified by the drama.\n"
        "- Let timing breathe and follow emotional rhythm.\n"
        "- For longer videos, vary rhythm like short / medium / medium / short / long / climax / final hold.\n"
        "LTX MODE RULES:\n"
        "- i2v: strong single-image motion.\n"
        "- i2v_as: audio-sensitive motion, environmental pulsing, breathing tension, subtle rhythm response, but no literal speech articulation.\n"
        "- f_l: controlled A-to-B reveal, door opening, object transformation, pose shift, environmental change, or two-state transition.\n"
        "- f_l_as: transition or reveal that also needs audio-driven hit timing.\n"
        "- continuation: preserve continuity from the previous scene's visual endpoint when that is the strongest choice. Do not overuse it.\n"
        "- lip_sync: only if visible vocal articulation is truly required and the narration/audio mode supports it.\n"
        "- Every scene must include a short concrete ltx_reason that explains the production intent.\n"
        "Hard constraints:\n"
        "- Use only real LTX modes: i2v, i2v_as, f_l, f_l_as, continuation, lip_sync.\n"
        "- Never use fake modes like intro_lock, hero_peak, motion_follow, ending_hold.\n"
        "- lip_sync is allowed only for music-driven vocal rhythm with visible articulation support.\n"
        "- Do not use lip_sync for ordinary narration or generic voice-over.\n"
        "- Scenario Director is the main planning node. Storyboard executes your storyboard_out and should not rethink the plan.\n"
        "- Build scenes from story meaning, source-of-truth, connected refs, and director controls.\n"
        "- Keep timing coherent and use floats in seconds.\n"
        '- If "audioDurationSec" is present and > 0, scene timeline MUST span the full audio duration from 0.0 to audioDurationSec.\n'
        "- If audioDurationSec > 0: first scene starts at 0.0, final scene reaches near audioDurationSec, and every major audio interval belongs to some scene.\n"
        "- No large uncovered audio tail at the end. No large silent timeline gap unless explicitly intended as a scene beat.\n"
        "- If story climax happens early but audio continues, add natural late-stage scenes (aftermath, reaction, realization, escape continuation, tension tail, unresolved closing image, outro suspense).\n"
        "- Around 60 seconds, 6 scenes can be acceptable only if they truly cover full audio; add scenes when timing is too compressed.\n"
        "- Every scene must include concise but useful video/audio planning fields.\n"
        "- Keep the backend-compatible flat scene fields only. Do not output nested visual/audio/ltx blocks in final JSON.\n"
        "CONTRACT HARD RULE FOR narration_mode:\n"
        '- Every scene MUST include "narration_mode" explicitly.\n'
        '- narration_mode MUST always be a string and MUST NEVER be null.\n'
        '- Allowed values: "full", "duck", "pause".\n'
        '- If unsure, use "full".\n'
        '- Never output null for narration_mode and never omit narration_mode.\n'
        "ROLE HARD RULE:\n"
        "- If roleTypeByRole explicitly marks a role as hero/support/antagonist, you MUST preserve it in story summary, scene construction, role summary, and dominant scene behavior.\n"
        "- Do not silently revert to default character_1 hero if explicit roleTypeByRole provides a hero/support/antagonist mapping.\n"
        "Output contract:\n"
        "{\n"
        '  "story_summary": "",\n'
        '  "full_scenario": "",\n'
        '  "voice_script": "",\n'
        '  "music_prompt": "",\n'
        '  "director_summary": "",\n'
        '  "audioUnderstanding": {\n'
        '    "mainTopic": "",\n'
        '    "worldContext": "",\n'
        '    "impliedEvents": [],\n'
        '    "emotionalToneFromAudio": "",\n'
        '    "confidenceAudioUnderstood": 0.0,\n'
        '    "whatFromAudioDefinesWorld": ""\n'
        "  },\n"
        '  "conflictAnalysis": {\n'
        '    "audioVsDirectorNoteConflict": false,\n'
        '    "conflictDescription": "",\n'
        '    "resolutionStrategy": ""\n'
        "  },\n"
        '  "narrativeStrategy": {\n'
        '    "storyCoreSource": "audio",\n'
        '    "didAudioRemainPrimary": true,\n'
        '    "didDirectorNoteOverrideAudio": false,\n'
        '    "why": ""\n'
        "  },\n"
        '  "story": {\n'
        '    "title": "",\n'
        '    "summary": "",\n'
        '    "howDirectorNoteWasIntegrated": "",\n'
        '    "howRomanceExistsInsideAudioWorld": ""\n'
        "  },\n"
        '  "scenes": [\n'
        "    {\n"
        '      "scene_id": "S1",\n'
        '      "time_start": 0.0,\n'
        '      "time_end": 6.0,\n'
        '      "duration": 6.0,\n'
        '      "actors": ["character_1"],\n'
        '      "location": "",\n'
        '      "props": [],\n'
        '      "emotion": "",\n'
        '      "scene_goal": "",\n'
        '      "frame_description": "",\n'
        '      "action_in_frame": "",\n'
        '      "camera": "",\n'
        '      "image_prompt": "",\n'
        '      "video_prompt": "",\n'
        '      "ltx_mode": "i2v",\n'
        '      "ltx_reason": "",\n'
        '      "start_frame_source": "new",\n'
        '      "needs_two_frames": false,\n'
        '      "continuation_from_previous": false,\n'
        '      "narration_mode": "full",\n'
        '      "local_phrase": null,\n'
        '      "sfx": "",\n'
        '      "music_mix_hint": "off",\n'
        '      "whatFromAudioThisSceneUses": "",\n'
        '      "directorNoteLayer": "",\n'
        '      "boundaryReason": "phrase",\n'
        '      "audioAnchorEvidence": "",\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ],\n"
        '  "diagnostics": {\n'
        '    "usedAudioAsContentSource": true,\n'
        '    "usedAudioOnlyAsMood": false,\n'
        '    "didFallbackFromAudioContentTruth": false,\n'
        '    "biggestRisk": "",\n'
        '    "whatMayBeWrong": "",\n'
        '    "plannerMode": "full_audio_first",\n'
        '    "howDirectorNoteWasIntegrated": ""\n'
        "  }\n"
        "}\n\n"
        f"Runtime payload:\n{json.dumps({'source': source, 'context_refs': context_refs, 'director_controls': director_controls, 'connected_context_summary': connected_context_summary, 'metadata': metadata, 'audioDurationSec': audio_duration_sec if audio_duration_sec > 0 else None, 'audioDurationSource': audio_duration_source, 'sourceMode': source_mode, 'sourceOrigin': source_origin, 'audioConnected': audio_connected, 'preferAudioOverText': prefer_audio_over_text, 'roleTypeByRole': role_type_by_role, 'audioContext': normalized_audio, 'audioAnalysis': {'ok': runtime_analysis.get('ok'), 'audioDurationSec': runtime_analysis.get('audioDurationSec'), 'phraseCount': len(runtime_analysis.get('phrases') or []), 'pauseCount': len(runtime_analysis.get('pauseWindows') or []), 'energyTransitionCount': len(runtime_analysis.get('energyTransitions') or []), 'sectionCount': len(runtime_analysis.get('sections') or [])}, 'audioSemantics': {'ok': runtime_semantics.get('ok'), 'transcript': str(runtime_semantics.get('transcript') or '')[:2000], 'semanticSummary': str(runtime_semantics.get('semanticSummary') or '')[:1200], 'narrativeCore': str(runtime_semantics.get('narrativeCore') or '')[:600], 'worldContext': str(runtime_semantics.get('worldContext') or '')[:600], 'entities': [str(item).strip() for item in (runtime_semantics.get('entities') or []) if str(item).strip()][:20], 'impliedEvents': [str(item).strip() for item in (runtime_semantics.get('impliedEvents') or []) if str(item).strip()][:20], 'tone': str(runtime_semantics.get('tone') or '')[:200], 'confidence': runtime_semantics.get('confidence'), 'hint': str(runtime_semantics.get('hint') or '')[:120]}, 'segmentationGuidance': runtime_guidance}, ensure_ascii=False, indent=2)}"
    )
    if strict_json_retry:
        request_text += JSON_ONLY_RETRY_SUFFIX
    return request_text


def _build_audio_coverage_refinement_prompt(payload: dict[str, Any], storyboard_out: ScenarioDirectorStoryboardOut, coverage: dict[str, Any]) -> str:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    director_controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    compact_scenes: list[dict[str, Any]] = []
    for scene in storyboard_out.scenes:
        compact_scenes.append(
            {
                "scene_id": scene.scene_id,
                "time_start": scene.time_start,
                "time_end": scene.time_end,
                "duration": scene.duration,
                "scene_goal": scene.scene_goal,
                "frame_description": scene.frame_description,
                "action_in_frame": scene.action_in_frame,
                "camera": scene.camera,
            }
        )
    return (
        "Timeline repair pass (not a rewrite). Return strict JSON object only with the same contract keys.\n"
        "Preserve current story direction and keep strong existing scenes.\n"
        "Repair timeline to fully cover audio. Extend or add scenes only where needed.\n"
        "Keep progression natural and cinematic.\n"
        "Final scene must reach audioDurationSec.\n"
        "If audioDurationSec is > 0: first scene must start at 0.0, no large uncovered tail, and no large internal uncovered gap.\n"
        "Prefer preserving story quality while fixing coverage boundaries.\n"
        f"Coverage diagnostics: {json.dumps(coverage, ensure_ascii=False)}\n"
        f"Runtime payload: {json.dumps({'source': source, 'context_refs': context_refs, 'director_controls': director_controls, 'metadata': metadata, 'audioDurationSec': coverage.get('audioDurationSec')}, ensure_ascii=False)}\n"
        f"Current storyboard snapshot: {json.dumps({'story_summary': storyboard_out.story_summary, 'full_scenario': storyboard_out.full_scenario, 'voice_script': storyboard_out.voice_script, 'music_prompt': storyboard_out.music_prompt, 'director_summary': storyboard_out.director_summary, 'scenes': compact_scenes}, ensure_ascii=False)}"
    )


def _send_director_request(api_key: str, body: dict[str, Any]) -> tuple[dict[str, Any] | None, str, list[str]]:
    attempted_models: list[str] = []
    response: dict[str, Any] | None = None
    model_used = DEFAULT_TEXT_MODEL
    for candidate_model in [DEFAULT_TEXT_MODEL, FALLBACK_TEXT_MODEL]:
        if candidate_model in attempted_models:
            continue
        attempted_models.append(candidate_model)
        response = post_generate_content(api_key, candidate_model, body, timeout=120)
        model_used = candidate_model
        if not isinstance(response, dict) or not response.get("__http_error__"):
            break
    return response, model_used, attempted_models


def _parse_storyboard_payload(raw_text: str) -> dict[str, Any]:
    logger.debug("[SCENARIO_DIRECTOR] raw response received chars=%s", len(str(raw_text or "")))
    extracted = _extract_json_object(raw_text)
    if extracted is None:
        raise ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for Scenario Director: could not extract JSON object.",
            status_code=502,
            details={"rawPreview": str(raw_text or "")[:1000]},
        )
    logger.debug("[SCENARIO_DIRECTOR] json extracted keys=%s", ",".join(list(extracted.keys())[:8]))
    repaired = _repair_scenario_director_payload(extracted)
    return repaired


def _build_audio_first_single_call_prompt(payload: dict[str, Any]) -> str:
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    director_note = str(controls.get("directorNote") or controls.get("director_note") or "").strip()
    audio_duration = _safe_float(
        payload.get("audioDurationSec") or payload.get("metadata", {}).get("audio", {}).get("durationSec"),
        0.0,
    )
    role_labels = _build_reference_role_map(payload)
    available_refs = ", ".join(role_labels.values())
    references_block = (
        f"Available character references: {available_refs}\n" if available_refs else "Available character references: none\n"
    )
    return (
        "You are Scenario Director. AUDIO is the primary source of truth.\n"
        "Do not invent story that contradicts spoken audio.\n"
        "Scene timing must follow speech phrases, pauses, and energy shifts.\n"
        "Every scene must be grounded in spoken content.\n"
        "Character references are identity anchors.\n"
        "Use provided character references when scenes imply people.\n"
        "Do not replace core characters with invented ones.\n"
        "Do not contradict provided references.\n"
        "DIRECTOR NOTE CONSTRAINTS:\n"
        "- The director note may influence tone, lighting, pacing, emotional intensity, and cinematic style.\n"
        "- The director note MUST NOT change or reinterpret the literal meaning of the audio.\n"
        "STRICT RULES:\n"
        "- Do NOT convert real entities into metaphors.\n"
        "- If the audio mentions military objects (missiles, bunkers, tunnels, doors, satellites, infrastructure), they MUST remain literal and physically present in the scenes.\n"
        "- Do NOT reinterpret threats, weapons, or infrastructure as emotions, relationships, or symbolic concepts.\n"
        "- Do NOT replace factual events with abstract or poetic meaning.\n"
        "ALLOWED:\n"
        "- You may introduce romantic, poetic, or emotional tone ONLY as visual mood, character behavior, lighting, framing, or atmosphere.\n"
        "- Emotional interpretation must exist INSIDE the literal world defined by the audio, not instead of it.\n"
        "GOOD EXAMPLE:\n"
        "- dark bunker remains a bunker, but lighting, camera, and character interaction can feel intimate or emotional\n"
        "BAD EXAMPLE:\n"
        "- bunker becomes a metaphor for love or emotional connection\n"
        "PRIORITY RULE:\n"
        "- If there is any conflict between audio content and director note, ALWAYS preserve the literal meaning of the audio.\n"
        "- The audio is the source of truth.\n"
        "- The director note is a stylistic modifier only.\n"
        "REAL TIMELINE REQUIREMENTS:\n"
        f"- The audio duration is {audio_duration} seconds.\n"
        "- ALL timestamps (t0, t1) MUST be expressed in REAL seconds of the audio.\n"
        "- DO NOT normalize time to a 0..1 scale.\n"
        "- DO NOT compress the timeline.\n"
        "- The full timeline of transcript and scenes MUST span the actual audio duration.\n"
        "- The last scene MUST end close to the full duration of the audio.\n"
        "- Each segment must correspond to real spoken timing in the audio.\n"
        "- Scene boundaries should align with:\n"
        "  - speech phrases\n"
        "  - pauses\n"
        "  - energy shifts\n"
        "BAD:\n"
        "- t0: 0.0 → t1: 1.0 for full audio\n"
        "GOOD:\n"
        "- t0: 0.0 → t1: 4.2 → t1: 9.8 → ... → ~60.0\n"
        "Return strict JSON only.\n"
        f"Director note: {director_note if director_note else 'empty'}\n"
        f"{references_block}"
        "Output JSON contract:\n"
        "{\n"
        '  "transcript": [\n'
        '    { "t0": 0.0, "t1": 0.0, "text": "" }\n'
        "  ],\n"
        '  "audioStructure": {\n'
        '    "pauses": [],\n'
        '    "energyPeaks": [],\n'
        '    "transitions": [],\n'
        '    "pacingType": "",\n'
        '    "rhythmDescription": ""\n'
        "  },\n"
        '  "semanticTimeline": [\n'
        "    {\n"
        '      "t0": 0.0,\n'
        '      "t1": 0.0,\n'
        '      "text": "",\n'
        '      "meaning": "",\n'
        '      "visualFocus": "",\n'
        '      "emotion": "",\n'
        '      "sceneType": "intro",\n'
        '      "transitionHint": ""\n'
        "    }\n"
        "  ],\n"
        '  "scenes": [\n'
        "    {\n"
        '      "sceneId": "S1",\n'
        '      "t0": 0.0,\n'
        '      "t1": 0.0,\n'
        '      "duration": 0.0,\n'
        '      "summary": "",\n'
        '      "visualPrompt": "",\n'
        '      "characters": [],\n'
        '      "environment": "",\n'
        '      "camera": "",\n'
        '      "motion": "",\n'
        '      "transitionIn": "",\n'
        '      "transitionOut": ""\n'
        "    }\n"
        "  ],\n"
        '  "globalStory": {\n'
        '    "overallNarrative": "",\n'
        '    "mainTopic": "",\n'
        '    "worldDescription": "",\n'
        '    "tone": ""\n'
        "  },\n"
        '  "debug": {\n'
        '    "audioUsage": "",\n'
        '    "alignment": "",\n'
        '    "boundaryLogic": "",\n'
        '    "signals": ""\n'
        "  }\n"
        "}"
    )


def _build_inline_audio_part(audio_context: dict[str, Any]) -> dict[str, Any]:
    audio_url = str(audio_context.get("audioUrl") or "").strip()
    resolution = _resolve_audio_source_for_analysis(audio_url)
    if not resolution.get("ok"):
        raise ScenarioDirectorError(
            "audio_source_unavailable",
            "Audio source is required for audio-first single-call mode.",
            status_code=400,
            details={"audioUrl": audio_url or None, "reason": resolution.get("reason"), "hint": resolution.get("hint")},
        )

    mime_type = (
        str(audio_context.get("audioMimeType") or "").strip()
        or str(audio_context.get("mimeType") or "").strip()
        or mimetypes.guess_type(str(audio_url or ""))[0]
        or "audio/mpeg"
    )
    raw_audio = b""
    if resolution.get("mode") == "local_file" and resolution.get("path"):
        with open(str(resolution.get("path")), "rb") as fp:
            raw_audio = fp.read()
    else:
        fetch_url = str(resolution.get("url") or "").strip()
        if not fetch_url:
            raise ScenarioDirectorError(
                "audio_source_unavailable",
                "Resolved audio source has no readable path or URL.",
                status_code=400,
                details={"audioUrl": audio_url or None, "reason": resolution.get("reason")},
            )
        response = requests.get(fetch_url, timeout=60)
        response.raise_for_status()
        raw_audio = response.content
        mime_type = response.headers.get("content-type", mime_type).split(";")[0].strip() or mime_type
    if not raw_audio:
        raise ScenarioDirectorError(
            "audio_source_unavailable",
            "Audio file is empty for audio-first single-call mode.",
            status_code=400,
            details={"audioUrl": audio_url or None},
        )
    raw_audio_bytes = len(raw_audio)
    logger.info(
        "[SCENARIO DIRECTOR] inline audio raw size bytes=%s max=%s",
        raw_audio_bytes,
        MAX_INLINE_AUDIO_BYTES,
    )
    if raw_audio_bytes > MAX_INLINE_AUDIO_BYTES:
        raise ScenarioDirectorError(
            "audio_too_large_for_inline",
            "Audio file is too large for inline Gemini payload.",
            status_code=413,
            details={
                "audioUrl": audio_url or None,
                "rawAudioBytes": raw_audio_bytes,
                "maxInlineAudioBytes": MAX_INLINE_AUDIO_BYTES,
            },
        )
    return {"inlineData": {"mimeType": mime_type, "data": base64.b64encode(raw_audio).decode("utf-8")}}


def _parse_audio_first_single_call_payload(raw_text: str) -> dict[str, Any]:
    extracted = _extract_json_object(raw_text)
    if extracted is None:
        raise ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for audio-first single-call mode.",
            status_code=502,
            details={"rawPreview": str(raw_text or "")[:1000]},
        )
    required = ("transcript", "audioStructure", "semanticTimeline", "scenes")
    missing = [key for key in required if key not in extracted]
    if missing:
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload missed required fields.",
            status_code=502,
            details={"missingFields": missing, "rawPreview": str(raw_text or "")[:1000]},
        )
    if not isinstance(extracted.get("transcript"), list):
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload has invalid transcript type.",
            status_code=502,
            details={"field": "transcript", "expectedType": "list", "actualType": type(extracted.get("transcript")).__name__},
        )
    if not isinstance(extracted.get("audioStructure"), dict):
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload has invalid audioStructure type.",
            status_code=502,
            details={"field": "audioStructure", "expectedType": "dict", "actualType": type(extracted.get("audioStructure")).__name__},
        )
    if not isinstance(extracted.get("semanticTimeline"), list):
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload has invalid semanticTimeline type.",
            status_code=502,
            details={
                "field": "semanticTimeline",
                "expectedType": "list",
                "actualType": type(extracted.get("semanticTimeline")).__name__,
            },
        )
    scenes = extracted.get("scenes")
    if not isinstance(scenes, list):
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload has invalid scenes type.",
            status_code=502,
            details={"field": "scenes", "expectedType": "list", "actualType": type(scenes).__name__},
        )
    if not scenes:
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini audio-first payload returned empty scenes.",
            status_code=502,
            details={"field": "scenes", "reason": "empty_list"},
        )
    return extracted


def _scale_audio_first_timeline_if_normalized(result: dict[str, Any], audio_duration_sec: float) -> dict[str, Any]:
    if audio_duration_sec <= 0:
        return result
    max_t1 = 0.0
    for section_key in ("transcript", "semanticTimeline", "scenes"):
        section = result.get(section_key)
        if not isinstance(section, list):
            continue
        for row in section:
            if not isinstance(row, dict):
                continue
            max_t1 = max(max_t1, _safe_float(row.get("t1"), 0.0))
    if max_t1 <= 0:
        return result
    if max_t1 >= audio_duration_sec * 0.3 and abs(max_t1 - 1.0) > 0.05:
        return result
    scale = audio_duration_sec / max_t1
    for section_key in ("transcript", "semanticTimeline", "scenes"):
        section = result.get(section_key)
        if not isinstance(section, list):
            continue
        for row in section:
            if not isinstance(row, dict):
                continue
            t0 = _safe_float(row.get("t0"), 0.0)
            t1 = _safe_float(row.get("t1"), t0)
            row["t0"] = t0 * scale
            row["t1"] = t1 * scale
            if section_key == "scenes":
                row["duration"] = _safe_float(row.get("duration"), max(0.0, t1 - t0)) * scale
    print("[SCENARIO DIRECTOR] timeline normalized → scaled to real duration")
    return result


def _map_single_call_to_storyboard_out(result: dict[str, Any]) -> dict[str, Any]:
    global_story = result.get("globalStory") if isinstance(result.get("globalStory"), dict) else {}
    debug = result.get("debug") if isinstance(result.get("debug"), dict) else {}
    transcript_rows = result.get("transcript") if isinstance(result.get("transcript"), list) else []
    semantic_timeline = result.get("semanticTimeline") if isinstance(result.get("semanticTimeline"), list) else []
    raw_scenes = result.get("scenes") if isinstance(result.get("scenes"), list) else []
    transcript_text_parts = [
        str(item.get("text") or "").strip()
        for item in transcript_rows
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    voice_script = " ".join(transcript_text_parts).strip()
    legacy_scenes: list[dict[str, Any]] = []
    for idx, scene in enumerate(raw_scenes, start=1):
        if not isinstance(scene, dict):
            continue
        scene_start = _safe_float(scene.get("t0"), 0.0)
        scene_end = _safe_float(scene.get("t1"), scene_start)
        scene_duration = _safe_float(scene.get("duration"), max(0.0, scene_end - scene_start))
        timeline_hit = semantic_timeline[idx - 1] if idx - 1 < len(semantic_timeline) and isinstance(semantic_timeline[idx - 1], dict) else {}
        legacy_scenes.append(
            {
                "scene_id": str(scene.get("sceneId") or f"S{idx}").strip() or f"S{idx}",
                "time_start": scene_start,
                "time_end": scene_end,
                "duration": scene_duration,
                "actors": [str(actor).strip() for actor in (scene.get("characters") or []) if str(actor).strip()],
                "location": str(scene.get("environment") or "").strip(),
                "props": [],
                "emotion": str(timeline_hit.get("emotion") or "").strip(),
                "scene_goal": str(scene.get("summary") or "").strip(),
                "frame_description": str(scene.get("summary") or "").strip(),
                "action_in_frame": str(scene.get("motion") or "").strip(),
                "camera": str(scene.get("camera") or "").strip(),
                "image_prompt": str(scene.get("visualPrompt") or "").strip(),
                "video_prompt": str(scene.get("visualPrompt") or "").strip(),
                "ltx_mode": "i2v_as",
                "ltx_reason": "Audio-first single-call default mapping.",
                "start_frame_source": "new",
                "needs_two_frames": False,
                "continuation_from_previous": idx > 1,
                "narration_mode": "full",
                "local_phrase": str(timeline_hit.get("text") or "").strip() or None,
                "sfx": "",
                "music_mix_hint": "off",
                "what_from_audio_this_scene_uses": str(timeline_hit.get("meaning") or scene.get("summary") or "").strip(),
                "director_note_layer": "",
                "boundary_reason": "phrase",
                "audio_anchor_evidence": str(timeline_hit.get("transitionHint") or "").strip(),
                "confidence": 0.9,
            }
        )
    director_summary = (
        str(debug.get("alignment") or "").strip()
        or str(global_story.get("overallNarrative") or "").strip()
        or "Audio-first single-call Gemini output."
    )
    global_narrative = str(global_story.get("overallNarrative") or "").strip()
    return {
        "story_summary": global_narrative,
        "full_scenario": global_narrative,
        "voice_script": voice_script,
        "music_prompt": "",
        "director_summary": director_summary,
        "audio_understanding": {
            "main_topic": str(global_story.get("mainTopic") or "").strip(),
            "world_context": str(global_story.get("worldDescription") or "").strip(),
            "implied_events": [],
            "emotional_tone_from_audio": str(global_story.get("tone") or "").strip(),
            "confidence_audio_understood": 0.9,
            "what_from_audio_defines_world": str(global_story.get("worldDescription") or "").strip(),
        },
        "conflict_analysis": {
            "audio_vs_director_note_conflict": False,
            "conflict_description": "",
            "resolution_strategy": "",
        },
        "narrative_strategy": {
            "story_core_source": "audio",
            "did_audio_remain_primary": True,
            "did_director_note_override_audio": False,
            "why": "Audio-first single-call output.",
        },
        "story": {
            "title": str(global_story.get("mainTopic") or "").strip(),
            "summary": str(global_story.get("overallNarrative") or "").strip(),
            "how_director_note_was_integrated": "",
            "how_romance_exists_inside_audio_world": "",
        },
        "diagnostics": {
            "used_audio_as_content_source": True,
            "used_audio_only_as_mood": False,
            "did_fallback_from_audio_content_truth": False,
            "biggest_risk": str(debug.get("boundaryLogic") or "").strip(),
            "what_may_be_wrong": str(debug.get("signals") or "").strip(),
            "planner_mode": "full_audio_first",
            "how_director_note_was_integrated": "",
        },
        "scenes": legacy_scenes,
    }


def _run_audio_first_single_call(payload: dict[str, Any], audio_context: dict[str, Any], api_key: str) -> dict[str, Any]:
    logger.info("[SCENARIO DIRECTOR] audio-first single-call mode")
    prompt = _build_audio_first_single_call_prompt(payload)
    logger.info("[SCENARIO DIRECTOR] sending inline audio to Gemini")
    inline_audio_part = _build_inline_audio_part(audio_context)
    body = {
        "systemInstruction": {
            "parts": [{"text": "Return strict JSON only."}],
        },
        "contents": [{"role": "user", "parts": [{"text": prompt}, inline_audio_part]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
        },
    }
    response, model_used, attempted_models = _send_director_request(api_key, body)
    if not isinstance(response, dict):
        raise ScenarioDirectorError("gemini_request_failed", "Gemini did not return a JSON object.", status_code=502)
    if response.get("__http_error__"):
        status_code = int(response.get("status") or 502)
        raise ScenarioDirectorError(
            "gemini_request_failed",
            f"Gemini request failed with HTTP {status_code}: {str(response.get('text') or '')[:400]}",
            status_code=502,
            details={"httpStatus": status_code},
        )
    raw_text = _extract_gemini_text(response)
    parsed_single = _parse_audio_first_single_call_payload(raw_text)
    audio_duration_sec = _safe_float(
        payload.get("audioDurationSec") or payload.get("metadata", {}).get("audio", {}).get("durationSec"),
        0.0,
    )
    parsed_single = _scale_audio_first_timeline_if_normalized(parsed_single, audio_duration_sec)
    logger.info("[SCENARIO DIRECTOR] received single-call json keys=%s", list(parsed_single.keys()))
    legacy_payload = _map_single_call_to_storyboard_out(parsed_single)
    logger.info("[SCENARIO DIRECTOR] mapped single-call result to legacy storyboardOut")
    storyboard_out = ScenarioDirectorStoryboardOut.model_validate(legacy_payload)
    storyboard_out = _harden_storyboard_out(storyboard_out, payload)
    director_output = _build_director_output(storyboard_out, payload)
    brain_package = _build_brain_package(storyboard_out, payload)
    return {
        "ok": True,
        "transcript": parsed_single.get("transcript") or [],
        "audioStructure": parsed_single.get("audioStructure") if isinstance(parsed_single.get("audioStructure"), dict) else {},
        "semanticTimeline": parsed_single.get("semanticTimeline") or [],
        "scenes": parsed_single.get("scenes") or [],
        "globalStory": parsed_single.get("globalStory") if isinstance(parsed_single.get("globalStory"), dict) else {},
        "debug": parsed_single.get("debug") if isinstance(parsed_single.get("debug"), dict) else {},
        "storyboardOut": storyboard_out.model_dump(mode="json"),
        "directorOutput": director_output,
        "scenario": storyboard_out.full_scenario,
        "voiceScript": storyboard_out.voice_script,
        "bgMusicPrompt": storyboard_out.music_prompt,
        "brainPackage": brain_package,
        "meta": {
            "plannerSource": "gemini",
            "modelUsed": model_used,
            "attemptedModels": attempted_models,
            "audioFirstSingleCall": True,
            "rawGeminiTextPreview": raw_text[:2000],
        },
    }


def run_scenario_director(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ScenarioDirectorError(
            "gemini_api_key_missing",
            "GEMINI_API_KEY is missing for Scenario Director generation.",
            status_code=503,
        )

    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_meta = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    source_audio_meta = source_meta.get("audio") if isinstance(source_meta.get("audio"), dict) else {}
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    metadata_audio = metadata.get("audio") if isinstance(metadata.get("audio"), dict) else {}
    audio_context = _normalize_audio_context(payload)
    if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" and _coerce_bool(audio_context.get("hasAudio"), False):
        return _run_audio_first_single_call(payload, audio_context, api_key)
    source_origin_raw = str(
        payload.get("source_origin")
        or source.get("source_origin")
        or source.get("origin")
        or metadata_audio.get("origin")
        or payload.get("sourceOrigin")
        or ""
    ).strip()
    source_origin_normalized = str(audio_context.get("sourceOrigin") or source_origin_raw or "connected").strip().lower()
    audio_url_raw = str(
        source.get("source_value")
        or source.get("value")
        or payload.get("source_value")
        or metadata_audio.get("url")
        or source_audio_meta.get("url")
        or ""
    ).strip()
    audio_url_normalized = str(audio_context.get("audioUrl") or audio_url_raw).strip()
    audio_analysis = _build_audio_analysis_fallback(_safe_float(audio_context.get("audioDurationSec"), 0.0), "analysis_skipped")
    audio_hints: list[str] = []
    audio_errors: list[str] = []
    audio_analysis_attempted = False
    if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" and audio_context.get("hasAudio"):
        audio_analysis_attempted = True
        audio_analysis = _analyze_audio_for_scenario_director(audio_context)
        if audio_analysis.get("ok"):
            audio_hints.append("audio_analysis_ok")
        else:
            audio_hints.append(str(audio_analysis.get("hint") or "audio_analysis_failed"))
            audio_errors.extend(audio_analysis.get("errors") or [])
    elif str(audio_context.get("sourceMode") or "").upper() == "AUDIO":
        audio_hints.append("audio_mode_without_audio_url")
    audio_semantics = _analyze_audio_semantics_for_scenario_director(payload, audio_context)

    can_use_phrase_first = bool(
        str(audio_context.get("sourceMode") or "").upper() == "AUDIO"
        and _coerce_bool(audio_context.get("preferAudioOverText"), True)
        and audio_analysis.get("ok")
        and (
            len(audio_analysis.get("phrases") or []) > 0
            or len(audio_analysis.get("pauseWindows") or []) > 0
            or len(audio_analysis.get("sections") or []) > 0
        )
    )
    audio_guidance = _build_phrase_first_segmentation_guidance(audio_analysis, audio_context) if can_use_phrase_first else {}
    request_text = _build_request_text(
        payload,
        audio_context=audio_context,
        audio_analysis=audio_analysis,
        audio_guidance=audio_guidance,
        audio_semantics=audio_semantics,
    )
    body = {
        "systemInstruction": {
            "parts": [
                {
                    "text": (
                        "You are the production Scenario Director for PhotoStudio COMFY. Return strict JSON only. "
                        "When sourceMode=AUDIO and sourceOrigin=connected, AUDIO content truth outranks director note/style and must define world/topic/events. "
                        "Director note is interpretation layer only and must never override clear audio meaning. "
                        "If audioDurationSec > 0, timeline must cover full audio from 0.0 to audioDurationSec. "
                        "Every scene must include narration_mode as non-null string (full|duck|pause) and audio usage evidence fields."
                    ),
                }
            ]
        },
        "contents": [{"role": "user", "parts": [{"text": request_text}]}],
        "generationConfig": {
            "temperature": 0.2,
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
        },
    }

    response, model_used, attempted_models = _send_director_request(api_key, body)

    if not isinstance(response, dict):
        raise ScenarioDirectorError("gemini_request_failed", "Gemini did not return a JSON object.", status_code=502)
    if response.get("__http_error__"):
        status_code = int(response.get("status") or 502)
        raise ScenarioDirectorError(
            "gemini_request_failed",
            f"Gemini request failed with HTTP {status_code}: {str(response.get('text') or '')[:400]}",
            status_code=502,
            details={"httpStatus": status_code},
        )

    raw_text = _extract_gemini_text(response)
    retried_for_json = False
    try:
        parsed_payload = _parse_storyboard_payload(raw_text)
    except ScenarioDirectorError as first_exc:
        retried_for_json = True
        retry_body = {
            **body,
            "contents": [{"role": "user", "parts": [{"text": _build_request_text(payload, audio_context=audio_context, audio_analysis=audio_analysis, audio_guidance=audio_guidance, audio_semantics=audio_semantics, strict_json_retry=True)}]}],
        }
        retry_response, retry_model_used, retry_attempts = _send_director_request(api_key, retry_body)
        attempted_models.extend(model for model in retry_attempts if model not in attempted_models)
        if not isinstance(retry_response, dict) or retry_response.get("__http_error__"):
            raise first_exc
        raw_text = _extract_gemini_text(retry_response)
        model_used = retry_model_used
        parsed_payload = _parse_storyboard_payload(raw_text)

    parsed_payload, normalized_contract_fields, normalization_warnings = _normalize_scenario_director_scene_defaults(parsed_payload)
    structured_planner_diagnostics = _extract_structured_diagnostics(parsed_payload)

    try:
        storyboard_out = ScenarioDirectorStoryboardOut.model_validate(parsed_payload)
        logger.debug("[SCENARIO_DIRECTOR] validation ok scenes=%s retry=%s", len(storyboard_out.scenes), retried_for_json)
    except ValidationError as exc:
        logger.debug("[SCENARIO_DIRECTOR] validation failed errors=%s", len(exc.errors()))
        raise ScenarioDirectorError(
            "gemini_contract_invalid",
            "Gemini Scenario Director response does not match the required contract.",
            status_code=502,
            details={"validationErrors": exc.errors(), "rawPreview": raw_text[:1000]},
        ) from exc

    storyboard_out = _harden_storyboard_out(storyboard_out, payload)
    storyboard_out, explicit_role_warnings = _enforce_explicit_role_assignments(payload, storyboard_out)
    audio_duration_from_analysis = _safe_float(audio_analysis.get("audioDurationSec"), 0.0)
    audio_duration_from_payload = _safe_float(audio_context.get("audioDurationSec"), 0.0)
    audio_duration_sec = audio_duration_from_analysis or audio_duration_from_payload
    audio_duration_source = "analysis" if audio_duration_from_analysis > 0 else ("payload" if audio_duration_from_payload > 0 else "missing")
    audio_connected = _is_audio_connected(payload)
    audio_connected_reason = (
        "audio_connected_via_legacy_origin" if audio_connected and source_origin_raw.lower() in {"audio_node", "audio_upload", "audio_generated"} else "audio_connected"
    ) if audio_connected else (
        "source_origin_not_connected"
        if str(audio_context.get("hasAudio"))
        else "audio_url_missing"
    )
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    prefer_audio_over_text = _coerce_bool(controls.get("preferAudioOverText"), _coerce_bool(audio_context.get("preferAudioOverText"), True))
    text_hint_present = bool(str(controls.get("directorNote") or "").strip())
    effective_role_type_by_role, role_assignment_source, role_override_applied = _resolve_effective_role_type_by_role(payload)
    coverage = _validate_audio_timeline_coverage(storyboard_out.scenes, audio_duration_sec, coverage_source=audio_duration_source if audio_duration_source in {"analysis", "payload"} else "fallback")
    coverage_warnings: list[str] = list(coverage.get("warnings") or [])
    audio_led_warnings: list[str] = []
    if audio_connected and audio_duration_sec <= 0:
        audio_led_warnings.append("audio_connected_but_duration_missing")
    narrative_bias_estimate, text_hint_influence, audio_influence, bias_warnings = _estimate_narrative_bias(
        payload,
        storyboard_out,
        audio_connected=audio_connected,
        prefer_audio_over_text=prefer_audio_over_text,
    )
    audio_led_warnings.extend(bias_warnings)
    timeline_refinement_attempted = False
    timeline_refinement_succeeded = False
    if audio_duration_sec > 0 and coverage.get("timelineCoverageStatus") == "invalid":
        timeline_refinement_attempted = True
        refinement_body = {
            **body,
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": _build_audio_coverage_refinement_prompt(payload, storyboard_out, coverage)}],
                }
            ],
        }
        refinement_response, refinement_model_used, refinement_attempted_models = _send_director_request(api_key, refinement_body)
        attempted_models.extend(model for model in refinement_attempted_models if model not in attempted_models)
        if isinstance(refinement_response, dict) and not refinement_response.get("__http_error__"):
            refinement_raw_text = _extract_gemini_text(refinement_response)
            try:
                refinement_parsed = _parse_storyboard_payload(refinement_raw_text)
                refinement_parsed, _, refinement_normalization_warnings = _normalize_scenario_director_scene_defaults(refinement_parsed)
                refined_storyboard = ScenarioDirectorStoryboardOut.model_validate(refinement_parsed)
                refined_storyboard = _harden_storyboard_out(refined_storyboard, payload)
                refined_coverage = _validate_audio_timeline_coverage(refined_storyboard.scenes, audio_duration_sec, coverage_source=audio_duration_source if audio_duration_source in {"analysis", "payload"} else "fallback")
                if refined_coverage.get("timelineCoverageStatus") == "ok":
                    storyboard_out = refined_storyboard
                    coverage = refined_coverage
                    coverage_warnings = list(dict.fromkeys([*coverage_warnings, *refinement_normalization_warnings, *list(refined_coverage.get("warnings") or [])]))
                    timeline_refinement_succeeded = True
                    model_used = refinement_model_used
            except (ScenarioDirectorError, ValidationError):
                coverage_warnings.append("timeline_refinement_contract_invalid")
        else:
            coverage_warnings.append("timeline_refinement_request_failed")
    if audio_duration_sec > 0 and coverage.get("timelineCoverageStatus") == "invalid":
        raise ScenarioDirectorError(
            "contract_invalid_for_timeline",
            "Scenario Director timeline does not fully cover audioDurationSec after refinement.",
            status_code=502,
            details=coverage,
        )

    phrase_signals_available = bool(
        len(audio_analysis.get("phrases") or []) > 0
        or len(audio_analysis.get("pauseWindows") or []) > 0
        or len(audio_analysis.get("sections") or []) > 0
        or len(audio_analysis.get("energyTransitions") or []) > 0
    )
    is_audio_mode = str(audio_context.get("sourceMode") or "").upper() == "AUDIO"
    full_audio_first = bool(is_audio_mode and audio_connected and audio_analysis.get("ok") and phrase_signals_available)
    partial_audio_first = bool(
        is_audio_mode
        and not full_audio_first
        and (audio_connected or audio_duration_sec > 0 or _coerce_bool(audio_context.get("preferAudioOverText"), True))
    )
    fallback_mode = "full_audio_first" if full_audio_first else ("partial_audio_first" if partial_audio_first else "text_fallback")
    if full_audio_first:
        fallback_reason = "audio_connected_with_usable_analysis_signals"
        audio_primary_driver_reason = "full_audio_phrase_pause_section_guidance"
    elif partial_audio_first:
        fallback_reason = "audio_mode_with_duration_or_partial_signals"
        audio_primary_driver_reason = "partial_audio_guidance_duration_aware"
    else:
        fallback_reason = "audio_unusable_or_non_audio_source"
        audio_primary_driver_reason = "text_fallback_only"
    planner_narrative_strategy = structured_planner_diagnostics.get("narrativeStrategy") if isinstance(structured_planner_diagnostics.get("narrativeStrategy"), dict) else {}
    planner_diagnostics = structured_planner_diagnostics.get("diagnostics") if isinstance(structured_planner_diagnostics.get("diagnostics"), dict) else {}
    planner_audio_understanding = structured_planner_diagnostics.get("audioUnderstanding") if isinstance(structured_planner_diagnostics.get("audioUnderstanding"), dict) else {}
    audio_semantics_ok = _coerce_bool(audio_semantics.get("ok"), False)
    audio_transcript_available = bool(str(audio_semantics.get("transcript") or "").strip())
    audio_semantic_summary = str(audio_semantics.get("semanticSummary") or "").strip()
    audio_world_context = str(audio_semantics.get("worldContext") or "").strip()
    audio_narrative_core = str(audio_semantics.get("narrativeCore") or "").strip()
    audio_entities = [str(item).strip() for item in (audio_semantics.get("entities") or []) if str(item).strip()]
    audio_implied_events = [str(item).strip() for item in (audio_semantics.get("impliedEvents") or []) if str(item).strip()]
    audio_semantic_summary_available = bool(audio_semantic_summary)
    audio_content_truth_available = bool(audio_semantic_summary or audio_world_context or audio_entities)
    planner_scene_evidence = []
    for scene in storyboard_out.scenes:
        planner_scene_evidence.append(
            {
                "sceneId": scene.scene_id,
                "whatFromAudioThisSceneUses": scene.what_from_audio_this_scene_uses,
                "directorNoteLayer": scene.director_note_layer,
                "boundaryReason": scene.boundary_reason,
                "audioAnchorEvidence": scene.audio_anchor_evidence,
                "confidence": scene.confidence,
            }
        )
    fake_audio_first_risks: list[str] = []
    audio_grounding_validation = {"sceneRiskMap": [], "globalRisks": [], "score": 1.0}
    if is_audio_mode and audio_connected:
        if not _coerce_bool(planner_narrative_strategy.get("didAudioRemainPrimary"), False):
            fake_audio_first_risks.append("didAudioRemainPrimary_false")
        if _coerce_bool(planner_narrative_strategy.get("didDirectorNoteOverrideAudio"), False):
            fake_audio_first_risks.append("didDirectorNoteOverrideAudio_true")
        if _coerce_bool(planner_diagnostics.get("usedAudioOnlyAsMood"), False):
            fake_audio_first_risks.append("usedAudioOnlyAsMood_true")
        if not _coerce_bool(planner_diagnostics.get("usedAudioAsContentSource"), False):
            fake_audio_first_risks.append("usedAudioAsContentSource_false")
        if audio_semantics_ok and audio_content_truth_available and not _coerce_bool(planner_diagnostics.get("usedAudioAsContentSource"), False):
            fake_audio_first_risks.append("audio_content_truth_ignored")
        if not str(planner_audio_understanding.get("whatFromAudioDefinesWorld") or "").strip():
            fake_audio_first_risks.append("whatFromAudioDefinesWorld_missing")
        if any(
            not str(item.get("whatFromAudioThisSceneUses") or "").strip() or not str(item.get("audioAnchorEvidence") or "").strip()
            for item in planner_scene_evidence
        ):
            fake_audio_first_risks.append("scene_level_audio_evidence_missing")
        audio_grounding_validation = _validate_audio_first_integrity(
            storyboard_out=storyboard_out,
            structured_planner_diagnostics=structured_planner_diagnostics,
            audio_analysis=audio_analysis if isinstance(audio_analysis, dict) else {},
        )
        scene_level_risks = [
            risk
            for row in (audio_grounding_validation.get("sceneRiskMap") or [])
            if isinstance(row, dict)
            for risk in (row.get("risks") or [])
            if str(risk).strip()
        ]
        fake_audio_first_risks.extend(audio_grounding_validation.get("globalRisks") or [])
        fake_audio_first_risks.extend(scene_level_risks)
        logger.debug(
            "[AUDIO_GROUNDING] score=%s scene_risks=%s global_risks=%s",
            audio_grounding_validation.get("score"),
            len(scene_level_risks),
            len(audio_grounding_validation.get("globalRisks") or []),
        )
    fake_audio_first_risks = list(dict.fromkeys(fake_audio_first_risks))
    fake_audio_first_suspected = bool(fake_audio_first_risks) or _safe_float(audio_grounding_validation.get("score"), 1.0) < 0.5

    director_output = _build_director_output(storyboard_out, payload)
    brain_package = _build_brain_package(storyboard_out, payload)
    return {
        "ok": True,
        "storyboardOut": storyboard_out.model_dump(mode="json"),
        "directorOutput": director_output,
        "scenario": storyboard_out.full_scenario,
        "voiceScript": storyboard_out.voice_script,
        "bgMusicPrompt": storyboard_out.music_prompt,
        "brainPackage": brain_package,
        "meta": {
            "plannerSource": "gemini",
            "modelUsed": model_used,
            "attemptedModels": attempted_models,
            "retriedForJson": retried_for_json,
            "rawGeminiTextPreview": raw_text[:2000],
            "contractNormalizationApplied": bool(normalized_contract_fields),
            "normalizedContractFields": normalized_contract_fields,
            "audioDurationSec": coverage.get("audioDurationSec"),
            "audioDurationSec_payload": audio_duration_from_payload,
            "audioDurationSec_analysis": audio_duration_from_analysis,
            "audioConnected": audio_connected,
            "audioDurationSource": audio_duration_source,
            "audioUsedAsPrimaryNarrativeDriver": bool(full_audio_first or partial_audio_first),
            "audioPrimaryDriverReason": audio_primary_driver_reason,
            "fallbackMode": fallback_mode,
            "fallbackReason": fallback_reason,
            "audioSourceMode": audio_context.get("sourceMode"),
            "sourceOrigin_raw": source_origin_raw or None,
            "sourceOrigin_normalized": source_origin_normalized or None,
            "audioConnectedReason": audio_connected_reason,
            "audioUrl_raw": audio_url_raw or None,
            "audioUrl_normalized": str(audio_analysis.get("audioUrlNormalized") or audio_url_normalized or "") or None,
            "audioUrlResolutionMode": str(audio_analysis.get("audioUrlResolutionMode") or ("missing" if not audio_url_normalized else "invalid")),
            "audioResolvedPath": str(audio_analysis.get("audioResolvedPath") or "") or None,
            "audioResolutionReason": str(audio_analysis.get("audioResolutionReason") or "") or None,
            "preferAudioOverText": prefer_audio_over_text,
            "timelineSource": audio_context.get("timelineSource"),
            "segmentationMode": audio_context.get("segmentationMode"),
            "audioAnalysisAttempted": audio_analysis_attempted,
            "audioAnalysisOk": bool(audio_analysis.get("ok")),
            "audioAnalysisReason": str(audio_analysis.get("hint") or ("analysis_not_attempted" if not audio_analysis_attempted else "analysis_failed")),
            "phraseCount": len(audio_analysis.get("phrases") or []),
            "pauseCount": len(audio_analysis.get("pauseWindows") or []),
            "sectionCount": len(audio_analysis.get("sections") or []),
            "energyTransitionCount": len(audio_analysis.get("energyTransitions") or []),
            "usedPhraseFirstSegmentation": can_use_phrase_first,
            "sceneBoundaryStrategy": "phrase_pause_energy_section" if can_use_phrase_first else ("audio_duration_fallback" if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" else "text_default"),
            "audioAnalysisSource": audio_analysis.get("source"),
            "audioAnalysisHint": audio_analysis.get("hint"),
            "audioSemanticsOk": audio_semantics_ok,
            "audioTranscriptAvailable": audio_transcript_available,
            "audioSemanticSummaryAvailable": audio_semantic_summary_available,
            "audioNarrativeCore": audio_narrative_core[:600],
            "audioWorldContext": audio_world_context[:600],
            "audioEntities": audio_entities[:20],
            "audioImpliedEvents": audio_implied_events[:20],
            "audioSemanticsConfidence": _safe_float(audio_semantics.get("confidence"), 0.0),
            "audioContentTruthAvailable": audio_content_truth_available,
            "structuredPlannerDiagnostics": structured_planner_diagnostics,
            "sceneAudioEvidence": planner_scene_evidence,
            "fakeAudioFirstRiskSignals": fake_audio_first_risks,
            "fakeAudioFirstSuspected": fake_audio_first_suspected,
            "audioGroundingValidation": audio_grounding_validation,
            "audioGroundingScore": audio_grounding_validation.get("score"),
            "textHintPresent": text_hint_present,
            "textHintInfluence": text_hint_influence,
            "audioInfluence": audio_influence,
            "narrativeBiasEstimate": narrative_bias_estimate,
            "effectiveRoleTypeByRole": effective_role_type_by_role,
            "roleAssignmentSource": role_assignment_source,
            "roleOverrideApplied": role_override_applied,
            "timelineStartSec": coverage.get("timelineStartSec"),
            "timelineEndSec": coverage.get("timelineEndSec"),
            "timelineCoverageSec": coverage.get("timelineCoverageSec"),
            "timelineCoverageRatio": coverage.get("timelineCoverageRatio"),
            "coverageRatio": coverage.get("coverageRatio"),
            "expectedAudioDurationSec": coverage.get("expectedAudioDurationSec"),
            "actualCoveredDurationSec": coverage.get("actualCoveredDurationSec"),
            "coverageSource": coverage.get("coverageSource"),
            "uncoveredTailSec": coverage.get("uncoveredTailSec"),
            "internalGapCount": coverage.get("internalGapCount"),
            "timelineCoverageStatus": coverage.get("timelineCoverageStatus"),
            "timelineCoverageWarnings": coverage.get("warnings") or [],
            "sceneBoundaryCandidatesSample": (audio_guidance.get("boundaryCandidates") or [])[:12] if isinstance(audio_guidance, dict) else [],
            "timelineRefinementAttempted": timeline_refinement_attempted,
            "timelineRefinementSucceeded": timeline_refinement_succeeded,
            "hints": list(dict.fromkeys([*audio_hints, *(audio_guidance.get("hints") or [])])) if isinstance(audio_guidance, dict) else audio_hints,
            "errors": audio_errors,
            "warnings": list(
                dict.fromkeys(
                    [
                        *normalization_warnings,
                        *coverage_warnings,
                        *audio_led_warnings,
                        *explicit_role_warnings,
                    ]
                )
            ),
        },
    }


def _build_master_request_text(payload: dict[str, Any], *, audio_context: dict[str, Any], audio_analysis: dict[str, Any], retry_level: int = 0) -> str:
    base = _build_request_text(payload, audio_context=audio_context, audio_analysis=audio_analysis, audio_guidance={})
    retry_hint = ""
    if retry_level >= 1:
        retry_hint += MASTER_JSON_RETRY_SUFFIX
    if retry_level >= 2:
        retry_hint += "\nRETRY 2 OVERRIDE: Reduce acts to 5. Minimal output."
    return (
        f"{base}\n\n"
        "MASTER MODE:\n"
        "- DO NOT generate scenes.\n"
        "- Return only world truth and story arc.\n"
        "- Keep summary <= 300 chars.\n"
        "- Keep acts count between 1 and 8.\n"
        "- Keep text compact.\n"
        "Return JSON contract:\n"
        "{\n"
        '  "audioUnderstanding": {},\n'
        '  "worldContext": "",\n'
        '  "narrativeStrategy": {\n'
        '    "didAudioRemainPrimary": true,\n'
        '    "didDirectorNoteOverrideAudio": false\n'
        "  },\n"
        '  "storyArc": {\n'
        '    "summary": "",\n'
        '    "acts": [\n'
        "      {\n"
        '        "id": "A1",\n'
        '        "approxStart": 0,\n'
        '        "approxEnd": 30,\n'
        '        "purpose": "",\n'
        '        "whatFromAudioDefinesThisAct": ""\n'
        "      }\n"
        "    ]\n"
        "  }\n"
        "}\n"
        f"{retry_hint}"
    )


def _short_text(value: Any, *, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _sanitize_master_output(parsed_payload: dict[str, Any]) -> dict[str, Any]:
    audio_understanding = parsed_payload.get("audioUnderstanding")
    if not isinstance(audio_understanding, dict):
        audio_understanding = {}
    world_context = _short_text(
        parsed_payload.get("worldContext")
        or (audio_understanding.get("worldContext") if isinstance(audio_understanding, dict) else "")
        or "",
        limit=300,
    )
    narrative_strategy = parsed_payload.get("narrativeStrategy") if isinstance(parsed_payload.get("narrativeStrategy"), dict) else {}
    story_arc = parsed_payload.get("storyArc") if isinstance(parsed_payload.get("storyArc"), dict) else {}
    acts_raw = story_arc.get("acts") if isinstance(story_arc.get("acts"), list) else []
    acts: list[dict[str, Any]] = []
    for idx, act in enumerate(acts_raw[:8], start=1):
        item = act if isinstance(act, dict) else {}
        acts.append(
            {
                "id": _short_text(item.get("id") or f"A{idx}", limit=12) or f"A{idx}",
                "approxStart": _safe_float(item.get("approxStart"), 0.0),
                "approxEnd": _safe_float(item.get("approxEnd"), 0.0),
                "purpose": _short_text(item.get("purpose"), limit=160),
                "whatFromAudioDefinesThisAct": _short_text(item.get("whatFromAudioDefinesThisAct"), limit=160),
            }
        )
    return {
        "audioUnderstanding": audio_understanding,
        "worldContext": world_context,
        "narrativeStrategy": {
            "didAudioRemainPrimary": _coerce_bool(narrative_strategy.get("didAudioRemainPrimary"), True),
            "didDirectorNoteOverrideAudio": _coerce_bool(narrative_strategy.get("didDirectorNoteOverrideAudio"), False),
        },
        "storyArc": {
            "summary": _short_text(story_arc.get("summary"), limit=300),
            "acts": acts,
        },
    }


def run_scenario_director_master(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ScenarioDirectorError("gemini_api_key_missing", "GEMINI_API_KEY is missing for Scenario Director generation.", status_code=503)

    audio_context = _normalize_audio_context(payload)
    audio_analysis = _build_audio_analysis_fallback(_safe_float(audio_context.get("audioDurationSec"), 0.0), "analysis_skipped")
    if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" and audio_context.get("hasAudio"):
        audio_analysis = _analyze_audio_for_scenario_director(audio_context)

    attempted_models: list[str] = []
    model_used = DEFAULT_TEXT_MODEL
    last_error: ScenarioDirectorError | None = None
    for retry_level in range(0, 3):
        body = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are the production Scenario Director for PhotoStudio COMFY. Return strict JSON only. "
                            "MASTER MODE: DO NOT generate scenes. Return only world truth and story arc."
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": _build_master_request_text(payload, audio_context=audio_context, audio_analysis=audio_analysis, retry_level=retry_level)}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 4096},
        }
        response, model_used, retry_models = _send_director_request(api_key, body)
        attempted_models.extend(model for model in retry_models if model not in attempted_models)
        if not isinstance(response, dict) or response.get("__http_error__"):
            last_error = ScenarioDirectorError("gemini_request_failed", "Gemini request failed in Scenario Director master mode.", status_code=502)
            continue
        raw_text = _extract_gemini_text(response)
        parsed_payload = _extract_json_object(raw_text)
        if isinstance(parsed_payload, dict):
            sanitized = _sanitize_master_output(parsed_payload)
            return {
                "ok": True,
                "mode": "master",
                "masterOutput": sanitized,
                "meta": {"plannerSource": "gemini", "modelUsed": model_used, "attemptedModels": attempted_models, "retryCount": retry_level},
            }
        last_error = ScenarioDirectorError("gemini_invalid_json", "Gemini returned invalid JSON for Scenario Director master mode.", status_code=502)

    raise last_error or ScenarioDirectorError("gemini_invalid_json", "Gemini returned invalid JSON for Scenario Director master mode.", status_code=502)


def _build_scenes_request_text(
    payload: dict[str, Any],
    *,
    master_output: dict[str, Any],
    start_sec: float,
    end_sec: float,
    expected_scenes: int,
    audio_analysis: dict[str, Any],
    retry_level: int = 0,
) -> str:
    retry_hint = ""
    if retry_level >= 1:
        retry_hint += SCENES_JSON_RETRY_SUFFIX + "\nRETRY 1 OVERRIDE: Return ONLY JSON. Short fields only."
    if retry_level >= 2:
        retry_hint += "\nRETRY 2 OVERRIDE: Reduce scenes to 5. Minimal output."
    compact_audio = {
        "audioDurationSec": _safe_float(audio_analysis.get("audioDurationSec"), 0.0),
        "phrases": (audio_analysis.get("phrases") or [])[:16],
        "pauseWindows": (audio_analysis.get("pauseWindows") or [])[:16],
        "energyTransitions": (audio_analysis.get("energyTransitions") or [])[:16],
    }
    return (
        "SCENES MODE:\n"
        "- DO NOT redefine story.\n"
        "- Use provided MASTER as immutable truth.\n"
        "- Do not rewrite worldContext.\n"
        "- Do not expand beyond given time window.\n"
        "- Keep scene count <= expectedScenes and <= 8.\n"
        "- Keep every string <= 160 chars.\n"
        "Return JSON contract:\n"
        "{\n"
        '  "scenes": [\n'
        "    {\n"
        '      "id": "S1",\n'
        '      "t0": 0,\n'
        '      "t1": 5,\n'
        '      "whatFromAudioThisSceneUses": "",\n'
        '      "audioAnchorEvidence": "",\n'
        '      "frame": "",\n'
        '      "action": "",\n'
        '      "confidence": 0.8\n'
        "    }\n"
        "  ],\n"
        '  "diagnostics": { "audioGroundingScore": 0.82 }\n'
        "}\n\n"
        f"Runtime: {json.dumps({'timeWindow': {'startSec': start_sec, 'endSec': end_sec}, 'expectedScenes': expected_scenes, 'masterOutput': master_output, 'audioAnalysis': compact_audio, 'metadata': payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}}, ensure_ascii=False)}"
        f"{retry_hint}"
    )


def _sanitize_scenes_output(parsed_payload: dict[str, Any], *, start_sec: float, end_sec: float, max_scenes: int) -> dict[str, Any]:
    scenes_raw = parsed_payload.get("scenes") if isinstance(parsed_payload.get("scenes"), list) else []
    scenes: list[dict[str, Any]] = []
    for idx, scene in enumerate(scenes_raw[:max_scenes], start=1):
        item = scene if isinstance(scene, dict) else {}
        t0 = max(start_sec, min(end_sec, _safe_float(item.get("t0"), start_sec)))
        t1 = max(t0, min(end_sec, _safe_float(item.get("t1"), t0)))
        scenes.append(
            {
                "id": _short_text(item.get("id") or f"S{idx}", limit=12) or f"S{idx}",
                "t0": round(t0, 3),
                "t1": round(t1, 3),
                "whatFromAudioThisSceneUses": _short_text(item.get("whatFromAudioThisSceneUses"), limit=160),
                "audioAnchorEvidence": _short_text(item.get("audioAnchorEvidence"), limit=160),
                "frame": _short_text(item.get("frame"), limit=160),
                "action": _short_text(item.get("action"), limit=160),
                "confidence": max(0.0, min(1.0, _safe_float(item.get("confidence"), 0.5))),
            }
        )
    diagnostics_raw = parsed_payload.get("diagnostics") if isinstance(parsed_payload.get("diagnostics"), dict) else {}
    return {
        "scenes": scenes,
        "diagnostics": {"audioGroundingScore": max(0.0, min(1.0, _safe_float(diagnostics_raw.get("audioGroundingScore"), 0.0)))},
    }


def run_scenario_director_scenes(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (getattr(settings, "GEMINI_API_KEY", None) or os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise ScenarioDirectorError("gemini_api_key_missing", "GEMINI_API_KEY is missing for Scenario Director generation.", status_code=503)

    master_output = payload.get("master_output") if isinstance(payload.get("master_output"), dict) else {}
    if not master_output:
        raise ScenarioDirectorError("master_output_missing", "master_output is required for Scenario Director scenes mode.", status_code=422)
    time_window = payload.get("timeWindow") if isinstance(payload.get("timeWindow"), dict) else {}
    start_sec = max(0.0, _safe_float(time_window.get("startSec"), 0.0))
    raw_end_sec = _safe_float(time_window.get("endSec"), start_sec + 60.0)
    end_sec = raw_end_sec if raw_end_sec >= start_sec else start_sec
    if (end_sec - start_sec) > 60.0:
        end_sec = round(start_sec + 60.0, 3)
    expected_scenes = int(_safe_float(payload.get("expectedScenes"), max(1, round((end_sec - start_sec) / 8.0))))
    expected_scenes = max(1, min(expected_scenes, 8))

    audio_context = _normalize_audio_context(payload)
    audio_analysis = _build_audio_analysis_fallback(_safe_float(audio_context.get("audioDurationSec"), 0.0), "analysis_skipped")
    if str(audio_context.get("sourceMode") or "").upper() == "AUDIO" and audio_context.get("hasAudio"):
        audio_analysis = _analyze_audio_for_scenario_director(audio_context)

    attempted_models: list[str] = []
    model_used = DEFAULT_TEXT_MODEL
    last_error: ScenarioDirectorError | None = None
    for retry_level in range(0, 3):
        body = {
            "systemInstruction": {
                "parts": [
                    {
                        "text": (
                            "You are the production Scenario Director for PhotoStudio COMFY. Return strict JSON only. "
                            "SCENES MODE: Do not redefine story. Use MASTER as immutable truth. Do not rewrite worldContext."
                        )
                    }
                ]
            },
            "contents": [{"role": "user", "parts": [{"text": _build_scenes_request_text(payload, master_output=master_output, start_sec=start_sec, end_sec=end_sec, expected_scenes=expected_scenes, audio_analysis=audio_analysis, retry_level=retry_level)}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 4096},
        }
        response, model_used, retry_models = _send_director_request(api_key, body)
        attempted_models.extend(model for model in retry_models if model not in attempted_models)
        if not isinstance(response, dict) or response.get("__http_error__"):
            last_error = ScenarioDirectorError("gemini_request_failed", "Gemini request failed in Scenario Director scenes mode.", status_code=502)
            continue
        raw_text = _extract_gemini_text(response)
        parsed_payload = _extract_json_object(raw_text)
        if isinstance(parsed_payload, dict):
            max_scenes = 5 if retry_level >= 2 else expected_scenes
            sanitized = _sanitize_scenes_output(parsed_payload, start_sec=start_sec, end_sec=end_sec, max_scenes=max_scenes)
            return {
                "ok": True,
                "mode": "scenes",
                "timeWindow": {"startSec": start_sec, "endSec": end_sec},
                **sanitized,
                "meta": {"plannerSource": "gemini", "modelUsed": model_used, "attemptedModels": attempted_models, "retryCount": retry_level, "expectedScenes": expected_scenes},
            }
        last_error = ScenarioDirectorError("gemini_invalid_json", "Gemini returned invalid JSON for Scenario Director scenes mode.", status_code=502)

    raise last_error or ScenarioDirectorError("gemini_invalid_json", "Gemini returned invalid JSON for Scenario Director scenes mode.", status_code=502)
