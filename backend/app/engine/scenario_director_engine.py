import ast
import base64
import json
import logging
import mimetypes
import os
import re
import tempfile
import time
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
GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC = (1.5, 4.0, 8.0)
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

SCENARIO_CANONICAL_ROLES = (
    "character_1",
    "character_2",
    "character_3",
    "animal",
    "animal_1",
    "group",
    "group_faces",
    "location",
    "style",
    "props",
)
SCENARIO_CAST_ROLES = {"character_1", "character_2", "character_3", "animal", "animal_1", "group", "group_faces"}
SCENARIO_ROLE_ALIASES = {
    "character1": "character_1",
    "character2": "character_2",
    "character3": "character_3",
    "animal1": "animal_1",
    "group_face": "group_faces",
}
DUO_SCENE_HINTS = {"duo", "reunion", "shared-presence", "shared_presence", "two-shot", "two_shot", "joint", "together", "both"}
DUO_SCENE_EXTRA_HINTS = {
    "two shot",
    "shared",
    "meet",
    "meets",
    "meeting",
    "hug",
    "hugs",
    "walk together",
    "walking together",
}
GROUP_NARRATIVE_REQUIRED_HINTS = {
    "protest",
    "riot",
    "mob",
    "audience",
    "crowd chant",
    "mass panic",
    "chorus",
    "митинг",
    "бунт",
    "толпа",
    "массов",
    "хор",
}
WORLD_ANCHOR_ROLES = {"location", "style", "props", "animal", "animal_1", "group", "group_faces"}
SCENARIO_WORLD_ROLES = {"location", "style", "props"}
SCENARIO_CONTENT_TYPE_REGISTRY: dict[str, dict[str, Any]] = {
    "story": {
        "label": "История",
        "description": "Базовый сюжетный режим",
        "is_enabled": True,
        "mode_family": "narrative",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "balanced_story",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "story_arc",
        "pacing_profile": "balanced",
    },
    "music_video": {
        "label": "Клип",
        "description": "Музыкальный режим с опорой на master audio",
        "is_enabled": True,
        "mode_family": "performance",
        "uses_global_music_prompt": False,
        "supports_lip_sync": True,
        "supports_audio_slices": True,
        "default_ltx_strategy": "image-video",
        "prefers_close_face_for_lipsync": True,
        "clipWorkflowDefault": "image-video",
        "clipWorkflowLipSync": "image-lipsink-video-music",
        "clipWorkflowSound": "image-video-golos-zvuk",
        "clipWorkflowFirstLast": "imag-imag-video-bz",
        "clipWorkflowFirstLastSound": "imag-imag-video-zvuk",
        "narrative_priority": "beat_sync",
        "pacing_profile": "rhythmic",
    },
    "ad": {
        "label": "Реклама",
        "description": "Коммерческий режим",
        "is_enabled": True,
        "mode_family": "commercial",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "brand_focus",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "offer_clarity",
        "pacing_profile": "concise",
    },
    "cartoon": {
        "label": "Мультфильм",
        "description": "Стилизация и выразительная подача",
        "is_enabled": False,
        "mode_family": "stylized",
        "uses_global_music_prompt": True,
        "supports_lip_sync": True,
        "supports_audio_slices": False,
        "default_ltx_strategy": "stylized_motion",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "expressive_action",
        "pacing_profile": "playful",
    },
    "teaser": {
        "label": "Тизер",
        "description": "Короткий интригующий формат",
        "is_enabled": False,
        "mode_family": "promo",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "hook_first",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "intrigue",
        "pacing_profile": "punchy",
    },
    "series": {
        "label": "Сериал",
        "description": "Эпизодический режим",
        "is_enabled": False,
        "mode_family": "episodic",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "episodic_continuity",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "episode_progression",
        "pacing_profile": "steady",
    },
    "film": {
        "label": "Фильм",
        "description": "Полнометражная кинематографическая подача",
        "is_enabled": False,
        "mode_family": "cinematic",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "cinematic_long_arc",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "feature_arc",
        "pacing_profile": "cinematic",
    },
    "comics": {
        "label": "Комикс",
        "description": "Панельный/графический стиль",
        "is_enabled": False,
        "mode_family": "stylized",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": False,
        "default_ltx_strategy": "panel_like",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "panel_story",
        "pacing_profile": "framed",
    },
    "documentary": {
        "label": "Документалка",
        "description": "Фактическое повествование",
        "is_enabled": False,
        "mode_family": "factual",
        "uses_global_music_prompt": True,
        "supports_lip_sync": False,
        "supports_audio_slices": True,
        "default_ltx_strategy": "observational",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "fact_priority",
        "pacing_profile": "measured",
    },
    "trailer": {
        "label": "Трейлер",
        "description": "Пиковый промо-монтаж",
        "is_enabled": False,
        "mode_family": "promo",
        "uses_global_music_prompt": False,
        "supports_lip_sync": False,
        "supports_audio_slices": True,
        "default_ltx_strategy": "impact_cut",
        "prefers_close_face_for_lipsync": False,
        "narrative_priority": "peaks_only",
        "pacing_profile": "impact",
    },
}

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
PRESENTATION_MALE_HINTS = (
    "male vocal",
    "male voice",
    "male singer",
    "masculine vocal",
    "masculine voice",
    "man singing",
    "his voice",
    "he sings",
    "he is singing",
    "мужской вокал",
    "мужской голос",
    "поет мужчина",
    "поёт мужчина",
)
PRESENTATION_FEMALE_HINTS = (
    "female vocal",
    "female voice",
    "female singer",
    "feminine vocal",
    "feminine voice",
    "woman singing",
    "her voice",
    "she sings",
    "she is singing",
    "женский вокал",
    "женский голос",
    "поет женщина",
    "поёт женщина",
)
PRESENTATION_MIXED_HINTS = (
    "male and female",
    "female and male",
    "mixed vocal",
    "mixed voices",
    "duet vocal",
    "duet",
    "group vocal",
    "мужской и женский",
    "смешанный вокал",
    "дуэт",
)
NON_PERFORMER_ROLE_TYPE_HINTS = {"animal", "animal_1", "props", "location", "style", "group", "group_faces", "object", "background"}
MUSIC_VIDEO_SCENE_PURPOSES = {"hook", "build", "performance", "transition", "payoff", "ending_hold"}
MUSIC_VIDEO_PERFORMANCE_FRAMINGS = {"face_close", "close_performance", "medium_performance", "duet_frame"}
VOCAL_PRIORITY_KEYS = (
    "audioSemantics",
    "audio_semantics",
    "audioUnderstanding",
    "audio_understanding",
    "structuredPlannerDiagnostics",
    "structured_planner_diagnostics",
    "sceneAudioEvidence",
    "scene_audio_evidence",
    "plannerDebug",
    "planner_debug",
    "directorSummary",
    "director_summary",
    "storySummary",
    "story_summary",
    "transcriptHints",
    "transcript_hints",
    "lyrics",
    "lyricText",
    "lyric_text",
    "localPhrase",
    "local_phrase",
)
VOCAL_MALE_HINTS = (
    "male vocal",
    "male voice",
    "man singing",
    "man vocals",
    "masculine vocal",
    "masculine voice",
    "baritone",
    "tenor",
    "deep male vocal",
    "male singer",
)
VOCAL_FEMALE_HINTS = (
    "female vocal",
    "female voice",
    "woman singing",
    "woman vocals",
    "feminine vocal",
    "feminine voice",
    "alto female",
    "soprano",
    "female singer",
)
VOCAL_MIXED_HINTS = (
    "male and female vocals",
    "duet vocal",
    "mixed vocals",
    "two voices",
    "alternating male female",
)
PERFORMER_FEMALE_HINTS = ("girl", "woman", "female", "lady", "девушка", "женщина", "девочка")
PERFORMER_MALE_HINTS = ("man", "male", "boy", "guy", "мужчина", "парень", "мальчик")


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
    start_frame_prompt: str = ""
    end_frame_prompt: str = ""
    ltx_mode: str = "i2v"
    ltx_reason: str = ""
    start_frame_source: str = "new"
    needs_two_frames: bool = False
    continuation_from_previous: bool = False
    narration_mode: str = "full"
    local_phrase: str | None = None
    sfx: str = ""
    music_mix_hint: str = "off"
    render_mode: str = "image_video"
    resolved_workflow_key: str = "image-video"
    transition_type: str = "cut"
    shot_type: str = "medium"
    requested_duration_sec: float = 0.0
    scene_purpose: str = ""
    viewer_hook: str = ""
    performance_framing: str = ""
    lip_sync: bool = False
    lip_sync_text: str = ""
    send_audio_to_generator: bool = False
    audio_slice_start_sec: float = 0.0
    audio_slice_end_sec: float = 0.0
    audio_slice_expected_duration_sec: float = 0.0
    clip_decision_reason: str = ""
    role_influence_applied: bool = False
    role_influence_reason: str = ""
    scene_role_dynamics: str = ""
    multi_character_identity_lock: bool = False
    distinct_character_separation: bool = False
    duet_lock_enabled: bool = False
    duet_composition_mode: str = ""
    secondary_role_visibility_requirement: str = ""
    character2_drift_guard: str = ""
    duet_identity_contract: str = ""
    appearance_drift_risk: str = ""
    director_genre_intent: str = ""
    director_genre_reason: str = ""
    director_tone_bias: str = ""
    workflow_decision_reason: str = ""
    lip_sync_decision_reason: str = ""
    audio_slice_decision_reason: str = ""
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
        self.start_frame_prompt = str(self.start_frame_prompt or "").strip()
        self.end_frame_prompt = str(self.end_frame_prompt or "").strip()
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
        self.render_mode = str(self.render_mode or "image_video").strip() or "image_video"
        self.resolved_workflow_key = str(self.resolved_workflow_key or "image-video").strip() or "image-video"
        self.transition_type = str(self.transition_type or "cut").strip() or "cut"
        self.shot_type = str(self.shot_type or "").strip()
        self.requested_duration_sec = _safe_float(
            self.requested_duration_sec,
            max(0.0, _safe_float(self.duration, max(0.0, self.time_end - self.time_start))),
        )
        self.scene_purpose = str(self.scene_purpose or "").strip()
        self.viewer_hook = str(self.viewer_hook or "").strip()
        self.performance_framing = str(self.performance_framing or "").strip()
        self.lip_sync = _coerce_bool(self.lip_sync, self.ltx_mode == "lip_sync")
        self.lip_sync_text = str(self.lip_sync_text or "").strip()
        self.send_audio_to_generator = _coerce_bool(self.send_audio_to_generator, False)
        self.audio_slice_start_sec = _safe_float(self.audio_slice_start_sec, self.time_start)
        self.audio_slice_end_sec = _safe_float(self.audio_slice_end_sec, self.time_end)
        self.audio_slice_expected_duration_sec = _safe_float(
            self.audio_slice_expected_duration_sec,
            max(0.0, self.audio_slice_end_sec - self.audio_slice_start_sec),
        )
        self.clip_decision_reason = str(self.clip_decision_reason or "").strip()
        self.role_influence_applied = _coerce_bool(self.role_influence_applied, False)
        self.role_influence_reason = str(self.role_influence_reason or "").strip()
        self.scene_role_dynamics = str(self.scene_role_dynamics or "").strip()
        self.multi_character_identity_lock = _coerce_bool(self.multi_character_identity_lock, False)
        self.distinct_character_separation = _coerce_bool(self.distinct_character_separation, False)
        self.duet_lock_enabled = _coerce_bool(self.duet_lock_enabled, False)
        self.duet_composition_mode = str(self.duet_composition_mode or "").strip()
        self.secondary_role_visibility_requirement = str(self.secondary_role_visibility_requirement or "").strip()
        self.character2_drift_guard = str(self.character2_drift_guard or "").strip()
        self.duet_identity_contract = str(self.duet_identity_contract or "").strip()
        self.appearance_drift_risk = str(self.appearance_drift_risk or "").strip()
        self.director_genre_intent = str(self.director_genre_intent or "").strip()
        self.director_genre_reason = str(self.director_genre_reason or "").strip()
        self.director_tone_bias = str(self.director_tone_bias or "").strip()
        self.workflow_decision_reason = str(self.workflow_decision_reason or "").strip()
        self.lip_sync_decision_reason = str(self.lip_sync_decision_reason or "").strip()
        self.audio_slice_decision_reason = str(self.audio_slice_decision_reason or "").strip()
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
    story_frame_source: str = ""
    rhythm_source: str = ""
    story_frame_source_reason: str = ""
    rhythm_source_reason: str = ""
    did_audio_remain_primary: bool = False
    did_director_note_override_audio: bool = False
    why: str = ""

    @model_validator(mode="after")
    def _normalize(self) -> "ScenarioDirectorNarrativeStrategy":
        story_core_source = str(self.story_core_source or "mixed").strip().lower() or "mixed"
        self.story_core_source = story_core_source if story_core_source in {"audio", "source_of_truth", "director_note", "mixed", "fallback"} else "mixed"
        story_frame_source = str(self.story_frame_source or "").strip().lower()
        self.story_frame_source = story_frame_source if story_frame_source in {"source_of_truth", "director_note"} else ""
        rhythm_source = str(self.rhythm_source or "").strip().lower()
        self.rhythm_source = rhythm_source if rhythm_source in {"audio"} else ""
        self.story_frame_source_reason = str(self.story_frame_source_reason or "").strip()
        self.rhythm_source_reason = str(self.rhythm_source_reason or "").strip()
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
    no_text_fallback_mode: str = "off"
    authorial_interpretation_level: str = "balanced"
    audio_literalness_level: str = "balanced"

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
        fallback_mode = str(self.no_text_fallback_mode or "off").strip().lower() or "off"
        self.no_text_fallback_mode = fallback_mode if fallback_mode in {"off", "neutral_audio_literal"} else "off"
        self.authorial_interpretation_level = str(self.authorial_interpretation_level or "balanced").strip().lower() or "balanced"
        self.audio_literalness_level = str(self.audio_literalness_level or "balanced").strip().lower() or "balanced"
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


def _extract_gemini_finish_reason(resp: dict[str, Any]) -> str:
    candidates = resp.get("candidates") if isinstance(resp, dict) else None
    if not isinstance(candidates, list):
        return ""
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        finish = str(candidate.get("finishReason") or "").strip()
        if finish:
            return finish
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

    recovered = _recover_partial_json_candidate(candidate)
    if recovered:
        try:
            parsed = json.loads(recovered)
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


def _recover_partial_json_candidate(candidate: str) -> str | None:
    text = str(candidate or "").strip()
    if not text.startswith("{"):
        return None
    in_string = False
    escape = False
    closers: list[str] = []
    for char in text:
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
            continue
        if char == "{":
            closers.append("}")
        elif char == "[":
            closers.append("]")
        elif char in {"}", "]"} and closers:
            expected = closers[-1]
            if char == expected:
                closers.pop()
    repaired = text.rstrip()
    repaired = re.sub(r",\s*$", "", repaired)
    if in_string:
        repaired += '"'
    if closers:
        repaired += "".join(reversed(closers))
    return repaired


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
    normalized.setdefault("render_mode", normalized.get("renderMode"))
    normalized.setdefault("resolved_workflow_key", normalized.get("resolvedWorkflowKey"))
    normalized.setdefault("transition_type", normalized.get("transitionType"))
    normalized.setdefault("shot_type", normalized.get("shotType"))
    normalized.setdefault("requested_duration_sec", normalized.get("requestedDurationSec"))
    normalized.setdefault("scene_purpose", normalized.get("scenePurpose"))
    normalized.setdefault("viewer_hook", normalized.get("viewerHook"))
    normalized.setdefault("performance_framing", normalized.get("performanceFraming"))
    normalized.setdefault("lip_sync", normalized.get("lipSync"))
    normalized.setdefault("lip_sync_text", normalized.get("lipSyncText"))
    normalized.setdefault("send_audio_to_generator", normalized.get("sendAudioToGenerator"))
    normalized.setdefault("audio_slice_start_sec", normalized.get("audioSliceStartSec"))
    normalized.setdefault("audio_slice_end_sec", normalized.get("audioSliceEndSec"))
    normalized.setdefault("audio_slice_expected_duration_sec", normalized.get("audioSliceExpectedDurationSec"))
    normalized.setdefault("clip_decision_reason", normalized.get("clipDecisionReason"))
    normalized.setdefault("role_influence_applied", normalized.get("roleInfluenceApplied"))
    normalized.setdefault("role_influence_reason", normalized.get("roleInfluenceReason"))
    normalized.setdefault("scene_role_dynamics", normalized.get("sceneRoleDynamics"))
    normalized.setdefault("multi_character_identity_lock", normalized.get("multiCharacterIdentityLock"))
    normalized.setdefault("distinct_character_separation", normalized.get("distinctCharacterSeparation"))
    normalized.setdefault("duet_lock_enabled", normalized.get("duetLockEnabled"))
    normalized.setdefault("duet_composition_mode", normalized.get("duetCompositionMode"))
    normalized.setdefault("secondary_role_visibility_requirement", normalized.get("secondaryRoleVisibilityRequirement"))
    normalized.setdefault("character2_drift_guard", normalized.get("character2DriftGuard"))
    normalized.setdefault("duet_identity_contract", normalized.get("duetIdentityContract"))
    normalized.setdefault("appearance_drift_risk", normalized.get("appearanceDriftRisk"))
    normalized.setdefault("director_genre_intent", normalized.get("directorGenreIntent"))
    normalized.setdefault("director_genre_reason", normalized.get("directorGenreReason"))
    normalized.setdefault("director_tone_bias", normalized.get("directorToneBias"))
    normalized.setdefault("workflow_decision_reason", normalized.get("workflowDecisionReason"))
    normalized.setdefault("lip_sync_decision_reason", normalized.get("lipSyncDecisionReason"))
    normalized.setdefault("audio_slice_decision_reason", normalized.get("audioSliceDecisionReason"))

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
        narrative_strategy.setdefault("story_frame_source", narrative_strategy.get("storyFrameSource"))
        narrative_strategy.setdefault("rhythm_source", narrative_strategy.get("rhythmSource"))
        narrative_strategy.setdefault("story_frame_source_reason", narrative_strategy.get("storyFrameSourceReason"))
        narrative_strategy.setdefault("rhythm_source_reason", narrative_strategy.get("rhythmSourceReason"))
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
        diagnostics.setdefault("no_text_fallback_mode", diagnostics.get("noTextFallbackMode"))
        diagnostics.setdefault("authorial_interpretation_level", diagnostics.get("authorialInterpretationLevel"))
        diagnostics.setdefault("audio_literalness_level", diagnostics.get("audioLiteralnessLevel"))
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
            "storyFrameSource": str(narrative_strategy_raw.get("story_frame_source") or narrative_strategy_raw.get("storyFrameSource") or "").strip().lower(),
            "rhythmSource": str(narrative_strategy_raw.get("rhythm_source") or narrative_strategy_raw.get("rhythmSource") or "").strip().lower(),
            "storyFrameSourceReason": str(
                narrative_strategy_raw.get("story_frame_source_reason")
                or narrative_strategy_raw.get("storyFrameSourceReason")
                or ""
            ).strip(),
            "rhythmSourceReason": str(
                narrative_strategy_raw.get("rhythm_source_reason")
                or narrative_strategy_raw.get("rhythmSourceReason")
                or ""
            ).strip(),
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
            "story_core_source": str(narrative_strategy_raw.get("story_core_source") or narrative_strategy_raw.get("storyCoreSource") or "").strip().lower() or "mixed",
            "story_frame_source": str(narrative_strategy_raw.get("story_frame_source") or narrative_strategy_raw.get("storyFrameSource") or "").strip().lower(),
            "rhythm_source": str(narrative_strategy_raw.get("rhythm_source") or narrative_strategy_raw.get("rhythmSource") or "").strip().lower(),
            "story_frame_source_reason": str(
                narrative_strategy_raw.get("story_frame_source_reason")
                or narrative_strategy_raw.get("storyFrameSourceReason")
                or ""
            ).strip(),
            "rhythm_source_reason": str(
                narrative_strategy_raw.get("rhythm_source_reason")
                or narrative_strategy_raw.get("rhythmSourceReason")
                or ""
            ).strip(),
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
            "noTextFallbackMode": str(diagnostics_raw.get("no_text_fallback_mode") or diagnostics_raw.get("noTextFallbackMode") or "").strip().lower() or "off",
            "authorialInterpretationLevel": str(diagnostics_raw.get("authorial_interpretation_level") or diagnostics_raw.get("authorialInterpretationLevel") or "").strip().lower() or "balanced",
            "audioLiteralnessLevel": str(diagnostics_raw.get("audio_literalness_level") or diagnostics_raw.get("audioLiteralnessLevel") or "").strip().lower() or "balanced",
        },
    }


def _build_reference_role_map(payload: dict[str, Any]) -> dict[str, str]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    role_map: dict[str, str] = {}
    for role, item in refs.items():
        if not isinstance(item, dict):
            continue
        normalized_role = _normalize_scenario_role(role)
        if not normalized_role:
            continue
        label = str(item.get("preview") or item.get("label") or item.get("source_label") or role).strip()
        role_map[normalized_role] = label or normalized_role
    return role_map


def _build_display_label_by_role(payload: dict[str, Any], known_roles: list[str]) -> dict[str, str]:
    role_labels = _build_reference_role_map(payload)
    return {
        role: role_labels.get(role, role)
        for role in known_roles
    }


def _scene_participants(scene: ScenarioDirectorScene, role_lookup: dict[str, str]) -> list[str]:
    participants: list[str] = []
    for actor in scene.actors:
        clean = _normalize_scenario_role(actor, role_lookup=role_lookup)
        if not clean:
            continue
        participants.append(clean)
    return participants


def _build_character_roles(payload: dict[str, Any], role_labels: dict[str, str], known_roles: list[str]) -> list[dict[str, str]]:
    ordered_roles = [role for role in known_roles if role in SCENARIO_CAST_ROLES]
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
        "animal": "Животное / поддерживающий участник кадра",
        "animal_1": "Животное / поддерживающий участник кадра",
        "group": "Группа / массовка",
        "group_faces": "Группа / массовка",
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


def _build_role_lookup_from_payload(payload: dict[str, Any]) -> dict[str, str]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    lookup: dict[str, str] = {}
    for role, item in refs.items():
        normalized_role = _normalize_scenario_role(role)
        if not normalized_role or not isinstance(item, dict):
            continue
        candidates = [
            item.get("preview"),
            item.get("label"),
            item.get("source_label"),
            role,
        ]
        for candidate in candidates:
            clean = str(candidate or "").strip().lower()
            if clean:
                lookup[clean] = normalized_role
    return lookup


def _normalize_scenario_role(role: Any, *, role_lookup: dict[str, str] | None = None) -> str:
    clean = str(role or "").strip().lower()
    if not clean:
        return ""
    if role_lookup and clean in role_lookup:
        return role_lookup[clean]
    match = re.fullmatch(r"character[\s_-]*(\d+)", clean)
    if match:
        return f"character_{match.group(1)}"
    return SCENARIO_ROLE_ALIASES.get(clean, clean)


def _normalize_content_type(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return clean if clean in SCENARIO_CONTENT_TYPE_REGISTRY else "story"


def _resolve_requested_content_type(payload: dict[str, Any]) -> str:
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    return _normalize_content_type(controls.get("contentType") or payload.get("contentType"))


def _is_content_type_enabled(content_type: Any) -> bool:
    normalized = _normalize_content_type(content_type)
    policy = SCENARIO_CONTENT_TYPE_REGISTRY.get(normalized) or SCENARIO_CONTENT_TYPE_REGISTRY["story"]
    return bool(policy.get("is_enabled", True))


def _get_safe_content_type(content_type: Any, fallback_content_type: str = "story") -> str:
    normalized = _normalize_content_type(content_type)
    if _is_content_type_enabled(normalized):
        return normalized
    fallback_normalized = _normalize_content_type(fallback_content_type)
    if _is_content_type_enabled(fallback_normalized):
        return fallback_normalized
    for key, policy in SCENARIO_CONTENT_TYPE_REGISTRY.items():
        if bool(policy.get("is_enabled", True)):
            return key
    return "story"


def _get_content_type_policy(payload: dict[str, Any]) -> dict[str, Any]:
    requested = _resolve_requested_content_type(payload)
    effective = _get_safe_content_type(requested, "story")
    base = SCENARIO_CONTENT_TYPE_REGISTRY.get(effective) or SCENARIO_CONTENT_TYPE_REGISTRY["story"]
    return {
        "value": effective,
        **base,
        "requestedValue": requested,
        "requestedEnabled": _is_content_type_enabled(requested),
        "fallbackApplied": requested != effective,
    }


def _resolve_effective_global_music_prompt(payload: dict[str, Any], raw_music_prompt: str) -> str:
    clean_prompt = str(raw_music_prompt or "").strip()
    policy = _get_content_type_policy(payload)
    if policy.get("uses_global_music_prompt", True):
        return clean_prompt
    return clean_prompt if clean_prompt else ""


def _collect_known_roles(payload: dict[str, Any], scenes: list[ScenarioDirectorScene]) -> list[str]:
    role_lookup = _build_role_lookup_from_payload(payload)
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    refs_by_role_raw = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    connected_refs = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    connected_summary_refs = connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {}
    raw_role_types = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    effective_role_types, _, _ = _resolve_effective_role_type_by_role(payload)
    ordered: list[str] = []

    def _push(role: Any) -> None:
        normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    for role in refs.keys():
        _push(role)
    for role in refs_by_role_raw.keys():
        _push(role)
    for role in connected_refs.keys():
        _push(role)
    for role in connected_summary_refs.keys():
        _push(role)
    for role in raw_role_types.keys():
        _push(role)
    for role in effective_role_types.keys():
        _push(role)
    for scene in scenes:
        for actor in scene.actors:
            _push(actor)
        if str(scene.location or "").strip():
            _push("location")
        if any(str(prop or "").strip() for prop in (scene.props or [])):
            _push("props")
    for role in SCENARIO_CANONICAL_ROLES:
        if role in ordered:
            continue
        if role in refs or role in refs_by_role_raw or role in connected_refs or role in connected_summary_refs:
            _push(role)
    return ordered


def _collect_refs_by_role(payload: dict[str, Any], known_roles: list[str]) -> tuple[dict[str, list[str]], dict[str, bool]]:
    role_lookup = _build_role_lookup_from_payload(payload)
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    top_level_refs = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    connected_refs = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    connected_summary_refs = connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {}
    out: dict[str, list[str]] = {role: [] for role in known_roles}

    def _extend(role: Any, refs_value: Any) -> None:
        normalized_role = _normalize_scenario_role(role, role_lookup=role_lookup)
        if not normalized_role or normalized_role not in out or not isinstance(refs_value, list):
            return
        clean_refs = [str(ref).strip() for ref in refs_value if str(ref).strip()]
        if clean_refs:
            out[normalized_role].extend(clean_refs)

    for role, refs_value in top_level_refs.items():
        _extend(role, refs_value)
    for role, item in refs.items():
        if isinstance(item, dict):
            _extend(role, item.get("refs"))
    for role, refs_value in connected_refs.items():
        _extend(role, refs_value)
    for role, refs_value in connected_summary_refs.items():
        _extend(role, refs_value)
    merged = {role: list(dict.fromkeys(items)) for role, items in out.items() if items}
    source_flags = {
        "hasTopLevelRefsByRole": bool(top_level_refs),
        "hasContextRefs": bool(refs),
        "hasConnectedRefsByRole": bool(connected_refs or connected_summary_refs),
    }
    return merged, source_flags


def _collect_connected_refs_by_role(payload: dict[str, Any], known_roles: list[str]) -> dict[str, list[str]]:
    role_lookup = _build_role_lookup_from_payload(payload)
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    connected_raw = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    summary_refs = connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {}
    out: dict[str, list[str]] = {role: [] for role in known_roles}
    for source_map in (connected_raw, summary_refs):
        for role, refs in source_map.items():
            normalized_role = _normalize_scenario_role(role, role_lookup=role_lookup)
            if not normalized_role or normalized_role not in out:
                continue
            if isinstance(refs, list):
                out[normalized_role].extend([str(ref).strip() for ref in refs if str(ref).strip()])
    return {role: list(dict.fromkeys(items)) for role, items in out.items() if items}


def _has_connected_ref_for_role(payload: dict[str, Any], role: str) -> bool:
    normalized_role = _normalize_scenario_role(role)
    if not normalized_role:
        return False
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    item = refs.get(normalized_role) if isinstance(refs.get(normalized_role), dict) else {}
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    if _coerce_bool(meta.get("connected"), False):
        return True
    if isinstance(item.get("refs"), list) and any(str(ref).strip() for ref in item.get("refs") or []):
        return True
    try:
        if int(item.get("count") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    for source_map in (
        payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {},
        connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {},
    ):
        refs_list = source_map.get(normalized_role)
        if isinstance(refs_list, list) and any(str(ref).strip() for ref in refs_list):
            return True
    return False


def _extract_scene_actor_roles(
    scene: ScenarioDirectorScene,
    raw_scene: dict[str, Any],
    *,
    role_lookup: dict[str, str],
    known_roles: list[str],
    hero_participants: list[str],
    supporting_participants: list[str],
) -> list[str]:
    actor_roles: list[str] = []
    scene_signal_parts: list[str] = []

    def _collect_signal(value: Any) -> None:
        text = str(value or "").strip().lower()
        if text:
            scene_signal_parts.append(text)

    def _push(role: Any) -> None:
        normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
        if normalized and normalized in known_roles and normalized in SCENARIO_CAST_ROLES and normalized not in actor_roles:
            actor_roles.append(normalized)

    for key in ("primaryRole", "primary_role"):
        _collect_signal(raw_scene.get(key))
        _push(raw_scene.get(key))
    for key in ("secondaryRoles", "secondary_roles", "sceneActiveRoles", "scene_active_roles", "refsUsed", "refs_used"):
        for role in (raw_scene.get(key) or []):
            _push(role)
    for key in ("sceneType", "scene_type", "shotType", "shot_type", "title", "description", "prompt", "action", "beat", "imagePrompt", "videoPrompt"):
        _collect_signal(raw_scene.get(key))
    _collect_signal(scene.scene_goal)
    _collect_signal(scene.frame_description)
    _collect_signal(scene.action_in_frame)
    _collect_signal(scene.image_prompt)
    _collect_signal(scene.video_prompt)
    for actor in (scene.actors or []):
        _push(actor)
    for role in (raw_scene.get("participants") or []):
        _push(role)
    for key in ("mustAppear", "must_appear"):
        for role in (raw_scene.get(key) or []):
            _push(role)
    ref_directives = raw_scene.get("refDirectives") if isinstance(raw_scene.get("refDirectives"), dict) else {}
    for role, directive in ref_directives.items():
        if str(directive or "").strip().lower() in {"hero", "required"}:
            _push(role)
        _collect_signal(directive)
    if not actor_roles:
        for role in [*hero_participants, *supporting_participants]:
            _push(role)
    scene_signal = " ".join(scene_signal_parts)
    group_is_explicit = (
        "group" in {
            _normalize_scenario_role(role, role_lookup=role_lookup)
            for role in (raw_scene.get("mustAppear") or raw_scene.get("must_appear") or [])
        }
        or str(ref_directives.get("group") or "").strip().lower() in {"hero", "required"}
        or any(hint in scene_signal for hint in GROUP_NARRATIVE_REQUIRED_HINTS)
    )
    if "group" in actor_roles and not group_is_explicit:
        actor_roles = [role for role in actor_roles if role != "group"]
    return actor_roles


def _scene_has_shared_hint(scene: ScenarioDirectorScene, raw_scene: dict[str, Any]) -> bool:
    signal_parts: list[str] = []
    for key in (
        "sceneType",
        "scene_type",
        "shotType",
        "shot_type",
        "title",
        "description",
        "beat",
        "action",
        "imagePrompt",
        "image_prompt",
        "videoPrompt",
        "video_prompt",
    ):
        signal_parts.append(str(raw_scene.get(key) or "").strip().lower())
    signal_parts.extend(
        [
            str(scene.scene_goal or "").strip().lower(),
            str(scene.frame_description or "").strip().lower(),
            str(scene.action_in_frame or "").strip().lower(),
            str(scene.image_prompt or "").strip().lower(),
            str(scene.video_prompt or "").strip().lower(),
        ]
    )
    for list_key in ("participants", "mustAppear", "must_appear", "secondaryRoles", "secondary_roles", "refsUsed", "refs_used"):
        values = raw_scene.get(list_key)
        if isinstance(values, list):
            signal_parts.append(" ".join(str(value or "").strip().lower() for value in values))
    scene_signal = " ".join(part for part in signal_parts if part)
    if any(hint in scene_signal for hint in DUO_SCENE_HINTS):
        return True
    return any(hint in scene_signal for hint in DUO_SCENE_EXTRA_HINTS)


def _extract_scene_world_anchor_roles(raw_scene: dict[str, Any], actor_roles: list[str], *, role_lookup: dict[str, str]) -> list[str]:
    anchor_roles: list[str] = []

    def _push(role: Any) -> None:
        normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
        if (
            normalized
            and normalized in WORLD_ANCHOR_ROLES
            and normalized not in actor_roles
            and normalized not in anchor_roles
        ):
            anchor_roles.append(normalized)

    for key in ("sceneActiveRoles", "scene_active_roles", "refsUsed", "refs_used", "refRoles", "activeRoles"):
        for role in (raw_scene.get(key) or []):
            _push(role)
    ref_directives = raw_scene.get("refDirectives") if isinstance(raw_scene.get("refDirectives"), dict) else {}
    for role, directive in ref_directives.items():
        if str(directive or "").strip():
            _push(role)
    must_appear = raw_scene.get("mustAppear") if raw_scene.get("mustAppear") is not None else raw_scene.get("must_appear")
    if isinstance(must_appear, list):
        for role in must_appear:
            normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
            if normalized in {"location", "style", "animal", "animal_1", "group", "group_faces"}:
                _push(normalized)
            if normalized == "props":
                _push(normalized)
    return anchor_roles


def _resolve_scene_must_appear(
    scene: ScenarioDirectorScene,
    raw_scene: dict[str, Any],
    *,
    role_lookup: dict[str, str],
    actor_roles: list[str],
    primary_role: str,
    hero_participants: list[str],
) -> list[str]:
    def _normalize_actor_roles(values: Any) -> list[str]:
        out: list[str] = []
        if not isinstance(values, list):
            return out
        for role in values:
            normalized = _normalize_scenario_role(role, role_lookup=role_lookup)
            if normalized and normalized in SCENARIO_CAST_ROLES and normalized in actor_roles and normalized not in out:
                out.append(normalized)
        return out

    explicit = _normalize_actor_roles(raw_scene.get("mustAppear") if raw_scene.get("mustAppear") is not None else raw_scene.get("must_appear"))
    if "group" in explicit:
        scene_signal = " ".join(
            [
                str(raw_scene.get("sceneType") or raw_scene.get("scene_type") or "").strip().lower(),
                str(raw_scene.get("shotType") or raw_scene.get("shot_type") or "").strip().lower(),
                str(raw_scene.get("description") or "").strip().lower(),
                str(scene.scene_goal or "").strip().lower(),
                str(scene.action_in_frame or "").strip().lower(),
            ]
        )
        group_required = any(hint in scene_signal for hint in GROUP_NARRATIVE_REQUIRED_HINTS)
        if not group_required:
            explicit = [role for role in explicit if role != "group"]
    if explicit:
        return explicit
    explicit_primary = _normalize_scenario_role(
        raw_scene.get("primaryRole") if raw_scene.get("primaryRole") is not None else raw_scene.get("primary_role"),
        role_lookup=role_lookup,
    )
    explicit_secondary = _normalize_actor_roles(raw_scene.get("secondaryRoles") if raw_scene.get("secondaryRoles") is not None else raw_scene.get("secondary_roles"))
    if explicit_primary and explicit_primary in actor_roles and explicit_primary in SCENARIO_CAST_ROLES and explicit_secondary:
        return list(dict.fromkeys([explicit_primary, *explicit_secondary]))

    scene_signal = " ".join(
        [
            str(raw_scene.get("sceneType") or raw_scene.get("scene_type") or "").strip().lower(),
            str(raw_scene.get("shotType") or raw_scene.get("shot_type") or "").strip().lower(),
            str(raw_scene.get("title") or "").strip().lower(),
            str(raw_scene.get("description") or "").strip().lower(),
            str(raw_scene.get("prompt") or "").strip().lower(),
            str(raw_scene.get("beat") or "").strip().lower(),
            str(raw_scene.get("action") or "").strip().lower(),
            str(raw_scene.get("imagePrompt") or raw_scene.get("image_prompt") or scene.image_prompt or "").strip().lower(),
            str(raw_scene.get("videoPrompt") or raw_scene.get("video_prompt") or scene.video_prompt or "").strip().lower(),
            str(scene.scene_goal or "").strip().lower(),
            str(scene.frame_description or "").strip().lower(),
            str(scene.action_in_frame or "").strip().lower(),
        ]
    )
    if any(hint in scene_signal for hint in DUO_SCENE_HINTS):
        duo_candidates = [role for role in hero_participants if role in actor_roles]
        for role in actor_roles:
            if role not in duo_candidates:
                duo_candidates.append(role)
        if len(duo_candidates) >= 2:
            return duo_candidates[:2]

    if primary_role and primary_role in SCENARIO_CAST_ROLES:
        return [primary_role]
    if actor_roles:
        return [actor_roles[0]]
    return []


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


def _append_decision_flag(reason: str, flag: str, value: Any = True) -> str:
    base = str(reason or "").strip()
    token = f"{flag}={str(value).lower() if isinstance(value, bool) else value}"
    if token in base:
        return base
    return f"{base}; {token}".strip("; ").strip()


def _is_group_narratively_required(
    *,
    scene: ScenarioDirectorScene,
    raw_scene: dict[str, Any],
    ref_directives: dict[str, Any] | None = None,
    must_appear_roles: list[str] | None = None,
) -> bool:
    directives = ref_directives if isinstance(ref_directives, dict) else {}
    must_appear = [str(role or "").strip().lower() for role in (must_appear_roles or []) if str(role or "").strip()]
    if "group" in must_appear:
        return True
    if str(directives.get("group") or "").strip().lower() in {"hero", "required"}:
        return True
    scene_signal = " ".join(
        [
            str(raw_scene.get("sceneType") or raw_scene.get("scene_type") or "").strip().lower(),
            str(raw_scene.get("shotType") or raw_scene.get("shot_type") or "").strip().lower(),
            str(raw_scene.get("summary") or raw_scene.get("description") or "").strip().lower(),
            str(raw_scene.get("goal") or raw_scene.get("sceneGoal") or raw_scene.get("scene_goal") or "").strip().lower(),
            str(raw_scene.get("prompt") or raw_scene.get("imagePrompt") or raw_scene.get("videoPrompt") or "").strip().lower(),
            str(raw_scene.get("action") or raw_scene.get("beat") or "").strip().lower(),
            str(scene.scene_goal or "").strip().lower(),
            str(scene.frame_description or "").strip().lower(),
            str(scene.action_in_frame or "").strip().lower(),
            str(scene.image_prompt or "").strip().lower(),
            str(scene.video_prompt or "").strip().lower(),
        ]
    )
    return any(hint in scene_signal for hint in GROUP_NARRATIVE_REQUIRED_HINTS)


def _build_director_output(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> dict[str, Any]:
    content_type_policy = _get_content_type_policy(payload)
    is_music_video = str(content_type_policy.get("value") or "").strip().lower() == "music_video"
    prefer_explicit_duo_roles = (
        is_music_video
        and _has_connected_ref_for_role(payload, "character_1")
        and _has_connected_ref_for_role(payload, "character_2")
    )
    role_lookup = _build_role_lookup_from_payload(payload)
    known_roles = _collect_known_roles(payload, storyboard_out.scenes)
    display_label_by_role = _build_display_label_by_role(payload, known_roles)
    refs_by_role, refs_merge_flags = _collect_refs_by_role(payload, known_roles)
    connected_refs_by_role = _collect_connected_refs_by_role(payload, known_roles)
    effective_role_types, role_type_source, _ = _resolve_effective_role_type_by_role(payload)
    role_type_by_role: dict[str, str] = {}
    raw_role_types = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    for role in known_roles:
        normalized = _normalize_scenario_role(role)
        raw_type = str(raw_role_types.get(role) or raw_role_types.get(normalized) or "").strip().lower()
        if raw_type in ALLOWED_EXPLICIT_ROLE_TYPES:
            role_type_by_role[normalized] = raw_type
    for role, role_type in effective_role_types.items():
        normalized = _normalize_scenario_role(role)
        if normalized:
            role_type_by_role[normalized] = str(role_type or "").strip().lower() or "auto"
    for role in known_roles:
        normalized = _normalize_scenario_role(role)
        if normalized and normalized not in role_type_by_role:
            role_type_by_role[normalized] = "auto"

    connected_context_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    role_presence = {
        role for role in known_roles
        if len(refs_by_role.get(role) or []) > 0 or len(connected_refs_by_role.get(role) or []) > 0
    }
    for scene in storyboard_out.scenes:
        for actor in scene.actors:
            normalized_actor = _normalize_scenario_role(actor, role_lookup=role_lookup)
            if normalized_actor:
                role_presence.add(normalized_actor)
        if str(scene.location or "").strip():
            role_presence.add("location")
        if any(str(item or "").strip() for item in (scene.props or [])):
            role_presence.add("props")
    present_cast_roles = [role for role in known_roles if role in SCENARIO_CAST_ROLES and role in role_presence]
    present_world_roles = [role for role in known_roles if role in SCENARIO_WORLD_ROLES and role in role_presence]
    hero_participants = [role for role in known_roles if role in SCENARIO_CAST_ROLES and role_type_by_role.get(role) == "hero" and role in role_presence]
    supporting_participants = [
        role for role in known_roles
        if role in SCENARIO_CAST_ROLES and role in role_presence and role_type_by_role.get(role) in {"support", "antagonist"}
    ]
    if not hero_participants and "character_1" in role_presence:
        hero_participants = ["character_1"]
    must_appear_roles = [role for role in known_roles if role in SCENARIO_CAST_ROLES and role in role_presence]
    ref_directives = {
        role: ("hero" if role in hero_participants else ("required" if role in must_appear_roles or role in {"location", "props"} else "optional"))
        for role in known_roles
        if role in refs_by_role or role in connected_refs_by_role
    }

    history = {
        "summary": storyboard_out.story_summary,
        "fullScenario": storyboard_out.full_scenario,
        "characterRoles": _build_character_roles(payload, display_label_by_role, known_roles),
        "toneStyleDirection": str(payload.get("director_controls", {}).get("styleProfile") or "").strip() or "Scenario Director tone guidance from Gemini.",
        "directorSummary": storyboard_out.director_summary,
        "presentCastRoles": present_cast_roles,
        "presentWorldRoles": present_world_roles,
        "refsPresentByRole": refs_by_role,
        "connectedRefsPresentByRole": connected_refs_by_role,
        "hasProps": "props" in present_world_roles,
        "hasLocation": "location" in present_world_roles,
        "hasStyle": "style" in present_world_roles,
    }
    scenes = []
    video = []
    sound = []
    payload_scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    for scene in storyboard_out.scenes:
        scene_index = len(scenes)
        raw_scene = payload_scenes[scene_index] if scene_index < len(payload_scenes) and isinstance(payload_scenes[scene_index], dict) else {}
        scene_ref_directives = raw_scene.get("refDirectives") if isinstance(raw_scene.get("refDirectives"), dict) else {}
        participants = _scene_participants(scene, role_lookup)
        actor_roles = _extract_scene_actor_roles(
            scene,
            raw_scene,
            role_lookup=role_lookup,
            known_roles=known_roles,
            hero_participants=hero_participants,
            supporting_participants=supporting_participants,
        )
        actor_roles_before_rescue = list(actor_roles)
        shared_scene_hint = _scene_has_shared_hint(scene, raw_scene)
        if actor_roles and shared_scene_hint and len(actor_roles) < 2:
            rescue_pool = [*hero_participants, *supporting_participants]
            for role in rescue_pool:
                normalized = _normalize_scenario_role(role)
                if normalized and normalized in SCENARIO_CAST_ROLES and normalized not in actor_roles:
                    actor_roles.append(normalized)
                if len(actor_roles) >= 2:
                    break
        if prefer_explicit_duo_roles and shared_scene_hint:
            actor_roles = [role for role in actor_roles if role not in {"group", "group_faces"}]
            for explicit_role in ("character_1", "character_2"):
                if explicit_role not in actor_roles:
                    actor_roles.append(explicit_role)
        if prefer_explicit_duo_roles and shared_scene_hint:
            participants = [role for role in participants if role not in {"group", "group_faces"}]
            for explicit_role in ("character_1", "character_2"):
                if explicit_role in actor_roles and explicit_role not in participants:
                    participants.append(explicit_role)
        raw_anchor_roles = _extract_scene_world_anchor_roles(raw_scene, actor_roles, role_lookup=role_lookup)
        scene_anchor_roles: list[str] = list(raw_anchor_roles)
        if str(scene.location or "").strip() and (refs_by_role.get("location") or connected_refs_by_role.get("location")):
            scene_anchor_roles.append("location")
        if scene.props and (refs_by_role.get("props") or connected_refs_by_role.get("props")):
            scene_anchor_roles.append("props")
        if (
            (refs_by_role.get("style") or connected_refs_by_role.get("style"))
            and any(str(value or "").strip() for value in [scene.frame_description, scene.action_in_frame, scene.image_prompt, scene.video_prompt, scene.camera])
        ):
            scene_anchor_roles.append("style")
        scene_anchor_roles = list(dict.fromkeys(scene_anchor_roles))
        scene_active_roles = list(dict.fromkeys([*actor_roles, *scene_anchor_roles]))
        refs_used_roles: list[str] = []
        refs_used_map: dict[str, list[str]] = {}
        for role in dict.fromkeys([*scene_active_roles, *scene_anchor_roles]):
            role_refs = list(dict.fromkeys([*(refs_by_role.get(role) or []), *(connected_refs_by_role.get(role) or [])]))
            if not role_refs:
                continue
            refs_used_roles.append(role)
            refs_used_map[role] = role_refs
        explicit_primary_role = _normalize_scenario_role(
            raw_scene.get("primaryRole") if raw_scene.get("primaryRole") is not None else raw_scene.get("primary_role"),
            role_lookup=role_lookup,
        )
        primary_role = explicit_primary_role if explicit_primary_role in actor_roles else next(
            (role for role in actor_roles if role_type_by_role.get(role) == "hero"),
            actor_roles[0] if actor_roles else "",
        )
        secondary_roles = [role for role in actor_roles if role != primary_role]
        must_appear = _resolve_scene_must_appear(
            scene,
            raw_scene,
            role_lookup=role_lookup,
            actor_roles=actor_roles,
            primary_role=primary_role,
            hero_participants=hero_participants,
        )
        group_narratively_required = _is_group_narratively_required(
            scene=scene,
            raw_scene=raw_scene,
            ref_directives=scene_ref_directives,
            must_appear_roles=must_appear,
        )
        if prefer_explicit_duo_roles and shared_scene_hint:
            must_appear = [role for role in must_appear if role not in {"group", "group_faces"}]
            for explicit_role in ("character_1", "character_2"):
                if explicit_role in actor_roles and explicit_role not in must_appear:
                    must_appear.append(explicit_role)
        scene_role_dynamics = str(scene.scene_role_dynamics or "").strip().lower()
        active_character_roles = [role for role in actor_roles if role.startswith("character_")]
        is_environment_only_scene = bool(scene_role_dynamics == "environment" and not actor_roles and not active_character_roles)
        if "character_1" in actor_roles and "character_2" in actor_roles and "group" in actor_roles:
            actor_roles = [role for role in actor_roles if role != "group"]
            scene_active_roles = [role for role in scene_active_roles if role != "group"]
            refs_used_roles = [role for role in refs_used_roles if role != "group"]
            refs_used_map.pop("group", None)
            must_appear = [role for role in must_appear if role != "group"]
        if not group_narratively_required:
            actor_roles = [role for role in actor_roles if role != "group"]
            participants = [role for role in participants if role != "group"]
            scene_active_roles = [role for role in scene_active_roles if role != "group"]
            refs_used_roles = [role for role in refs_used_roles if role != "group"]
            refs_used_map.pop("group", None)
            must_appear = [role for role in must_appear if role != "group"]
        if is_environment_only_scene:
            scene_active_roles = [role for role in scene_active_roles if role in {"location", "style", "props"}]
            refs_used_roles = [role for role in refs_used_roles if role in {"location", "style", "props"}]
            refs_used_map = {role: refs for role, refs in refs_used_map.items() if role in {"location", "style", "props"}}
            must_appear = []
        primary_role = primary_role if primary_role in actor_roles else (actor_roles[0] if actor_roles else "")
        secondary_roles = [role for role in actor_roles if role != primary_role]
        support_entity_ids = [role for role in secondary_roles if role != "group" or group_narratively_required]
        start_frame_prompt = ""
        end_frame_prompt = ""
        if _scene_requires_explicit_first_last_prompts(scene):
            start_frame_prompt, end_frame_prompt = _derive_first_last_frame_prompts(scene, raw_scene)
            scene.start_frame_prompt = start_frame_prompt
            scene.end_frame_prompt = end_frame_prompt

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
            "startFramePrompt": start_frame_prompt,
            "endFramePrompt": end_frame_prompt,
            "startFramePromptRu": start_frame_prompt,
            "startFramePromptEn": start_frame_prompt,
            "endFramePromptRu": end_frame_prompt,
            "endFramePromptEn": end_frame_prompt,
            "ltxMode": scene.ltx_mode,
            "whyThisMode": scene.ltx_reason,
            "renderMode": scene.render_mode,
            "resolvedWorkflowKey": scene.resolved_workflow_key,
            "scenePurpose": scene.scene_purpose,
            "viewerHook": scene.viewer_hook,
            "startFrameSource": scene.start_frame_source,
            "needsTwoFrames": scene.needs_two_frames,
            "continuation": scene.continuation_from_previous,
            "transitionType": scene.transition_type,
            "shotType": scene.shot_type,
            "requestedDurationSec": scene.requested_duration_sec,
            "narrationMode": scene.narration_mode,
            "localPhrase": scene.local_phrase,
            "sfx": scene.sfx,
            "soundNotes": scene.sfx,
            "pauseDuckSilenceNotes": "",
            "musicMixHint": scene.music_mix_hint,
            "lipSync": scene.lip_sync,
            "lipSyncText": scene.lip_sync_text,
            "sendAudioToGenerator": scene.send_audio_to_generator,
            "audioSliceStartSec": scene.audio_slice_start_sec,
            "audioSliceEndSec": scene.audio_slice_end_sec,
            "audioSliceExpectedDurationSec": scene.audio_slice_expected_duration_sec,
            "performanceFraming": scene.performance_framing,
            "clipDecisionReason": _append_decision_flag(scene.clip_decision_reason, "groupNarrativelyRequired", group_narratively_required),
            "roleInfluenceApplied": scene.role_influence_applied or ("roleInfluenceApplied=true" in str(scene.clip_decision_reason or "")),
            "roleInfluenceReason": scene.role_influence_reason
            or (
                (re.search(r"roleInfluenceReason=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"roleInfluenceReason=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "sceneRoleDynamics": scene.scene_role_dynamics
            or (
                (re.search(r"sceneRoleDynamics=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"sceneRoleDynamics=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "multiCharacterIdentityLock": scene.multi_character_identity_lock or ("multiCharacterIdentityLock=true" in str(scene.clip_decision_reason or "")),
            "distinctCharacterSeparation": scene.distinct_character_separation or ("distinctCharacterSeparation=true" in str(scene.clip_decision_reason or "")),
            "duetLockEnabled": scene.duet_lock_enabled or ("duetLockEnabled=true" in str(scene.clip_decision_reason or "")),
            "duetCompositionMode": scene.duet_composition_mode
            or (
                (re.search(r"duetCompositionMode=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"duetCompositionMode=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "secondaryRoleVisibilityRequirement": scene.secondary_role_visibility_requirement
            or (
                (re.search(r"secondaryRoleVisibilityRequirement=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"secondaryRoleVisibilityRequirement=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "character2DriftGuard": scene.character2_drift_guard
            or (
                (re.search(r"character2DriftGuard=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"character2DriftGuard=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "duetIdentityContract": scene.duet_identity_contract,
            "appearanceDriftRisk": scene.appearance_drift_risk
            or (
                (re.search(r"appearanceDriftRisk=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"appearanceDriftRisk=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "directorGenreIntent": scene.director_genre_intent
            or (
                (re.search(r"directorGenreIntent=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"directorGenreIntent=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "directorGenreReason": scene.director_genre_reason,
            "directorToneBias": scene.director_tone_bias
            or (
                (re.search(r"directorToneBias=([^;\\.]+)", str(scene.clip_decision_reason or "")) or [None, ""])[1]
                if re.search(r"directorToneBias=([^;\\.]+)", str(scene.clip_decision_reason or ""))
                else ""
            ),
            "workflowDecisionReason": _append_decision_flag(scene.workflow_decision_reason, "groupNarrativelyRequired", group_narratively_required),
            "lipSyncDecisionReason": scene.lip_sync_decision_reason,
            "audioSliceDecisionReason": scene.audio_slice_decision_reason,
            "primaryRole": primary_role,
            "secondaryRoles": secondary_roles,
            "sceneActiveRoles": scene_active_roles,
            "refsUsed": refs_used_roles,
            "refsUsedByRole": refs_used_map,
            "mustAppear": must_appear,
            "mustNotAppear": ["character_1", "character_2", "character_3", "group"] if is_environment_only_scene else [],
            "heroEntityId": primary_role if primary_role else "",
            "supportEntityIds": support_entity_ids,
            "refDirectives": {role: ref_directives.get(role, "optional") for role in refs_used_roles},
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
                "startFramePrompt": start_frame_prompt,
                "endFramePrompt": end_frame_prompt,
                "startFramePromptRu": start_frame_prompt,
                "startFramePromptEn": start_frame_prompt,
                "endFramePromptRu": end_frame_prompt,
                "endFramePromptEn": end_frame_prompt,
                "ltxMode": scene.ltx_mode,
                "whyThisMode": scene.ltx_reason,
                "renderMode": scene.render_mode,
                "resolvedWorkflowKey": scene.resolved_workflow_key,
                "startFrameSource": scene.start_frame_source,
                "needsTwoFrames": scene.needs_two_frames,
                "continuation": scene.continuation_from_previous,
                "transitionType": scene.transition_type,
                "shotType": scene.shot_type,
                "requestedDurationSec": scene.requested_duration_sec,
                "sceneActiveRoles": scene_active_roles,
                "duetLockEnabled": scene.duet_lock_enabled,
                "duetIdentityContract": scene.duet_identity_contract,
                "directorGenreIntent": scene.director_genre_intent,
                "sceneContract": {
                    "activeRoles": scene_active_roles,
                    "duetLockEnabled": scene.duet_lock_enabled,
                    "duetIdentityContract": scene.duet_identity_contract,
                    "directorGenreIntent": scene.director_genre_intent,
                },
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
                "lipSync": scene.lip_sync,
                "lipSyncText": scene.lip_sync_text,
                "sendAudioToGenerator": scene.send_audio_to_generator,
                "audioSliceStartSec": scene.audio_slice_start_sec,
                "audioSliceEndSec": scene.audio_slice_end_sec,
                "audioSliceExpectedDurationSec": scene.audio_slice_expected_duration_sec,
            }
        )
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s primaryRole=%s", scene.scene_id, primary_role)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s secondaryRoles=%s", scene.scene_id, secondary_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s actorRoles(before)=%s", scene.scene_id, actor_roles_before_rescue)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s actorRoles(after rescue)=%s", scene.scene_id, actor_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s sharedSceneHint=%s", scene.scene_id, shared_scene_hint)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s anchorRoles(raw)=%s", scene.scene_id, raw_anchor_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s anchorRoles(final)=%s", scene.scene_id, scene_anchor_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s sceneActiveRoles=%s", scene.scene_id, scene_active_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s refsUsed=%s", scene.scene_id, refs_used_roles)
        logger.debug("[SCENARIO DIRECTOR OUTPUT] scene %s mustAppear=%s", scene.scene_id, must_appear)
    effective_global_music_prompt = storyboard_out.music_prompt if content_type_policy.get("uses_global_music_prompt", True) else ""
    music = {
        "globalMusicPrompt": effective_global_music_prompt,
        "mood": str(payload.get("director_controls", {}).get("styleProfile") or "").strip(),
        "style": f"{payload.get('director_controls', {}).get('contentType') or ''} / {payload.get('director_controls', {}).get('styleProfile') or ''}".strip(" /"),
        "pacingHints": "Use the Gemini scene pacing to build intro, escalation, climax, and resolution.",
    }
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package knownRoles=%s", known_roles)
    logger.debug(
        "[SCENARIO DIRECTOR OUTPUT] package refs merge sources top=%s context=%s connected=%s",
        refs_merge_flags.get("hasTopLevelRefsByRole"),
        refs_merge_flags.get("hasContextRefs"),
        refs_merge_flags.get("hasConnectedRefsByRole"),
    )
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package refsByRole keys=%s", sorted(refs_by_role.keys()))
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package connectedRefsByRole keys=%s", sorted(connected_refs_by_role.keys()))
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package roleTypeByRole=%s", role_type_by_role)
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package heroParticipants=%s", hero_participants)
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package supportingParticipants=%s", supporting_participants)
    logger.debug("[SCENARIO DIRECTOR OUTPUT] package mustAppearRoles=%s", must_appear_roles)
    logger.debug(
        "[SCENARIO DIRECTOR OUTPUT] package worldRefs location=%s style=%s props=%s animal_group=%s",
        bool(refs_by_role.get("location") or connected_refs_by_role.get("location")),
        bool(refs_by_role.get("style") or connected_refs_by_role.get("style")),
        bool(refs_by_role.get("props") or connected_refs_by_role.get("props")),
        bool(
            refs_by_role.get("animal")
            or connected_refs_by_role.get("animal")
            or refs_by_role.get("animal_1")
            or connected_refs_by_role.get("animal_1")
            or refs_by_role.get("group")
            or connected_refs_by_role.get("group")
            or refs_by_role.get("group_faces")
            or connected_refs_by_role.get("group_faces")
        ),
    )
    return {
        "history": history,
        "scenes": scenes,
        "video": video,
        "sound": sound,
        "music": music,
        "knownRoles": known_roles,
        "refsByRole": refs_by_role,
        "connectedRefsByRole": connected_refs_by_role,
        "roleTypeByRole": role_type_by_role,
        "connected_context_summary": connected_context_summary,
        "presentCastRoles": present_cast_roles,
        "presentWorldRoles": present_world_roles,
        "refsPresentByRole": refs_by_role,
        "connectedRefsPresentByRole": connected_refs_by_role,
        "hasLocation": "location" in present_world_roles,
        "hasProps": "props" in present_world_roles,
        "hasStyle": "style" in present_world_roles,
        "heroParticipants": hero_participants,
        "supportingParticipants": supporting_participants,
        "mustAppearRoles": must_appear_roles,
        "context_refs": context_refs,
        "displayLabelByRole": display_label_by_role,
        "refDirectives": ref_directives,
        "contentTypePolicy": content_type_policy,
        "debugRoleContract": {
            "knownRoles": known_roles,
            "roleTypeByRole": role_type_by_role,
            "roleTypeSourceByRole": role_type_source,
            "refsByRoleKeys": sorted(refs_by_role.keys()),
            "connectedRefsByRoleKeys": sorted(connected_refs_by_role.keys()),
            "refsMergeSourceFlags": refs_merge_flags,
            "hasLocationRef": bool(refs_by_role.get("location") or connected_refs_by_role.get("location")),
            "hasStyleRef": bool(refs_by_role.get("style") or connected_refs_by_role.get("style")),
            "hasPropsRef": bool(refs_by_role.get("props") or connected_refs_by_role.get("props")),
            "hasAnimalGroupRef": bool(
                refs_by_role.get("animal")
                or connected_refs_by_role.get("animal")
                or refs_by_role.get("animal_1")
                or connected_refs_by_role.get("animal_1")
                or refs_by_role.get("group")
                or connected_refs_by_role.get("group")
                or refs_by_role.get("group_faces")
                or connected_refs_by_role.get("group_faces")
            ),
            "presentCastRoles": present_cast_roles,
            "presentWorldRoles": present_world_roles,
            "contentTypePolicy": content_type_policy,
        },
    }


def _build_brain_package(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> dict[str, Any]:
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    known_roles = _collect_known_roles(payload, storyboard_out.scenes)
    entities = list(known_roles)
    content_type_policy = _get_content_type_policy(payload)
    content_type = content_type_policy.get("value") or "story"
    global_music_prompt = _resolve_effective_global_music_prompt(payload, storyboard_out.music_prompt)
    return {
        "contentType": content_type,
        "contentTypeLabel": content_type_policy.get("label") or content_type,
        "styleProfile": controls.get("styleProfile") or "realistic",
        "styleLabel": controls.get("styleProfile") or "realistic",
        "sourceMode": str(source.get("source_mode") or "audio").upper(),
        "sourceOrigin": str(source.get("source_origin") or payload.get("sourceOrigin") or "connected"),
        "sourceLabel": source.get("source_mode") or "audio",
        "sourcePreview": source.get("source_preview") or source.get("source_value") or "",
        "connectedContext": summary,
        "entities": entities,
        "sceneLogic": [scene.scene_goal or scene.frame_description or scene.action_in_frame for scene in storyboard_out.scenes],
        "audioStrategy": storyboard_out.voice_script or global_music_prompt,
        "directorNote": controls.get("directorNote") or "",
        "contentTypePolicy": content_type_policy,
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


def _limit_lip_sync_usage(
    storyboard_out: ScenarioDirectorStoryboardOut,
    *,
    content_type_policy: dict[str, Any] | None = None,
) -> ScenarioDirectorStoryboardOut:
    content_policy = content_type_policy or {}
    is_music_video = str(content_policy.get("value") or "").strip().lower() == "music_video"
    lip_sync_seen = 0
    for scene in storyboard_out.scenes:
        if scene.ltx_mode != "lip_sync":
            continue
        lip_sync_seen += 1
        if lip_sync_seen <= 3:
            continue
        has_sound_cue = bool(str(scene.sfx or "").strip() or str(scene.local_phrase or "").strip())
        use_sound_workflow = has_sound_cue and not is_music_video
        scene.render_mode = "image_video_sound" if has_sound_cue else "image_video"
        if is_music_video:
            scene.render_mode = "image_video"
        scene.resolved_workflow_key = (
            str(content_policy.get("clipWorkflowSound") or "image-video-golos-zvuk")
            if use_sound_workflow
            else str(content_policy.get("clipWorkflowDefault") or "image-video")
        )
        scene.ltx_mode = "i2v_as" if use_sound_workflow else "i2v"
        scene.lip_sync = False
        scene.send_audio_to_generator = False
        scene.lip_sync_text = ""
        scene.audio_slice_start_sec = 0.0
        scene.audio_slice_end_sec = 0.0
        scene.audio_slice_expected_duration_sec = 0.0
        scene.audio_slice_decision_reason = "Audio slice disabled after lip-sync limit downgrade."
        replacement_reason = (
            "Lip-sync quota reached; downgraded to sound-aware image-video workflow."
            if use_sound_workflow
            else (
                "Lip-sync quota reached; downgraded to base image-video workflow with sound workflow auto-disabled for music_video."
                if has_sound_cue and is_music_video
                else "Lip-sync quota reached; downgraded to base image-video workflow."
            )
        )
        scene.workflow_decision_reason = replacement_reason
        scene.lip_sync_decision_reason = "Lip-sync disabled because Scenario Director allows at most 3 lip_sync scenes per output."
        scene.ltx_reason = _normalize_ltx_reason(replacement_reason, scene.ltx_mode, narration_mode=scene.narration_mode)
    return storyboard_out


def _infer_music_video_shot_type(scene: ScenarioDirectorScene) -> str:
    bundle = " ".join([scene.camera, scene.frame_description, scene.action_in_frame]).lower()
    if any(token in bundle for token in ("two-shot", "two shot", "duet", "shared frame", "both in frame", "side by side")):
        return "duet_shared"
    if any(token in bundle for token in ("extreme close", "ecu", "macro", "insert", "detail")):
        return "detail_insert"
    if any(token in bundle for token in ("close-up", "close up", "portrait", "face", "frontal", "tight")):
        return "close_up"
    if any(token in bundle for token in ("medium close", "medium shot", "waist-up", "waist up")):
        return "medium"
    if any(token in bundle for token in ("wide", "establishing", "drone", "aerial", "long shot", "vast")):
        return "wide"
    return "medium"


def _infer_music_video_scene_purpose(
    index: int,
    total: int,
    scene: ScenarioDirectorScene,
    *,
    transition_candidate: bool = False,
    performance_framing: str = "",
    performer_presentation: str = "unknown",
) -> str:
    continuation = _coerce_bool(scene.continuation_from_previous, False) or scene.ltx_mode == "continuation"
    render_mode = str(scene.render_mode or "").strip().lower()
    framing = str(performance_framing or "").strip().lower()
    has_human_cast = _scene_has_human_performer(scene) or performer_presentation in {"male", "female", "mixed"}
    if index == 0:
        return "hook"
    if index == 1 and framing in MUSIC_VIDEO_PERFORMANCE_FRAMINGS and has_human_cast:
        return "performance"
    if scene.needs_two_frames or render_mode in {"first_last", "first_last_sound"}:
        return "transition"
    if transition_candidate or continuation:
        return "transition"
    if index == max(0, total - 2):
        return "payoff"
    if index == total - 1:
        return "ending_hold"
    if framing in MUSIC_VIDEO_PERFORMANCE_FRAMINGS and has_human_cast:
        return "performance"
    return "build"


def _scene_has_human_performer(scene: ScenarioDirectorScene) -> bool:
    return any(actor.startswith("character_") or actor in {"group_faces", "group"} for actor in (scene.actors or []))


def _scene_has_lip_sync_signal(scene: ScenarioDirectorScene) -> bool:
    text = " ".join(
        [
            scene.local_phrase or "",
            scene.what_from_audio_this_scene_uses or "",
            scene.scene_goal or "",
            scene.action_in_frame or "",
            scene.frame_description or "",
        ]
    ).lower()
    return any(token in text for token in ("lyric", "vocal", "chorus", "sing", "verse", "hook line", "line"))


def _infer_presentation_from_texts(texts: list[str]) -> str:
    lowered = " ".join(str(item or "").strip().lower() for item in texts if str(item or "").strip())
    if not lowered:
        return "unknown"
    has_mixed = any(token in lowered for token in PRESENTATION_MIXED_HINTS)
    has_male = any(token in lowered for token in PRESENTATION_MALE_HINTS) or bool(
        re.search(r"\b(male|man|boy|his|him)\b", lowered)
    )
    has_female = any(token in lowered for token in PRESENTATION_FEMALE_HINTS) or bool(
        re.search(r"\b(female|woman|girl|her|she)\b", lowered)
    )
    if has_mixed or (has_male and has_female):
        return "mixed"
    if has_male:
        return "male"
    if has_female:
        return "female"
    return "unknown"


def _find_raw_scene_payload(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> dict[str, Any]:
    payload_scenes = payload.get("scenes") if isinstance(payload.get("scenes"), list) else []
    scene_id = str(scene.scene_id or "").strip()
    for item in payload_scenes:
        if not isinstance(item, dict):
            continue
        item_scene_id = str(item.get("sceneId") or item.get("scene_id") or "").strip()
        if item_scene_id and scene_id and item_scene_id == scene_id:
            return item
    return {}


def _collect_text_fragments(value: Any) -> list[str]:
    out: list[str] = []
    if isinstance(value, str):
        clean = value.strip()
        if clean:
            out.append(clean)
        return out
    if isinstance(value, dict):
        for nested in value.values():
            out.extend(_collect_text_fragments(nested))
        return out
    if isinstance(value, list):
        for nested in value:
            out.extend(_collect_text_fragments(nested))
    return out


def _normalize_lookup_text(value: str) -> str:
    lowered = str(value or "").lower()
    lowered = re.sub(r"[_\-/]+", " ", lowered)
    lowered = re.sub(r"[^0-9a-zа-яё\s]+", " ", lowered, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", lowered).strip()


def _extract_vocal_signals(texts: list[str]) -> tuple[bool, bool]:
    normalized = _normalize_lookup_text(" ".join(str(item or "") for item in texts if str(item or "").strip()))
    if not normalized:
        return False, False
    has_mixed = any(hint in normalized for hint in VOCAL_MIXED_HINTS)
    has_male = has_mixed or any(hint in normalized for hint in VOCAL_MALE_HINTS)
    has_female = has_mixed or any(hint in normalized for hint in VOCAL_FEMALE_HINTS)
    return has_male, has_female


def _infer_vocal_presentation(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> str:
    audio_context = _normalize_audio_context(payload)
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_meta = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    raw_scene = _find_raw_scene_payload(scene, payload)
    prioritized_sources: list[list[str]] = []
    for key in VOCAL_PRIORITY_KEYS:
        bucket: list[str] = []
        bucket.extend(_collect_text_fragments(payload.get(key)))
        bucket.extend(_collect_text_fragments(metadata.get(key)))
        bucket.extend(_collect_text_fragments(source_meta.get(key)))
        bucket.extend(_collect_text_fragments(raw_scene.get(key)))
        if bucket:
            prioritized_sources.append(bucket)
    prioritized_sources.append(_collect_text_fragments(payload.get("transcriptHints") or payload.get("transcript_hints")))
    prioritized_sources.append([scene.local_phrase or "", scene.what_from_audio_this_scene_uses or "", scene.scene_goal or ""])
    prioritized_sources.append(_collect_text_fragments(raw_scene.get("lyricText") or raw_scene.get("lyrics")))
    prioritized_sources.append([str(metadata.get("transcript") or ""), str(source_meta.get("transcript") or "")])
    prioritized_sources.append(_collect_text_fragments({"metadata": metadata, "source_meta": source_meta, "raw_scene": raw_scene}))
    prioritized_sources.append(_collect_text_fragments(audio_context))

    for bucket in prioritized_sources:
        has_male, has_female = _extract_vocal_signals(bucket)
        if has_male and has_female:
            return "mixed"
        if has_male:
            return "male"
        if has_female:
            return "female"
    return "unknown"


def _role_is_human_performer(role: str) -> bool:
    return role.startswith("character_") or role in {"group", "group_faces"}


def _infer_role_presentation(role: str, payload: dict[str, Any], *, raw_scene: dict[str, Any] | None = None) -> str:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    display_labels = payload.get("displayLabelByRole") if isinstance(payload.get("displayLabelByRole"), dict) else {}
    role_type_by_role = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    refs_used_by_role = raw_scene.get("refsUsedByRole") if isinstance(raw_scene, dict) and isinstance(raw_scene.get("refsUsedByRole"), dict) else {}
    role_ref = refs.get(role) if isinstance(refs.get(role), dict) else {}
    text_sources = [str(display_labels.get(role) or ""), str(role_type_by_role.get(role) or ""), str(role or "")]
    text_sources.extend(_collect_text_fragments(role_ref))
    text_sources.extend(_collect_text_fragments(refs_used_by_role.get(role)))
    return _infer_presentation_from_texts(text_sources)


def _infer_presentations_from_texts(texts: list[str], *, male_hints: tuple[str, ...], female_hints: tuple[str, ...]) -> str:
    normalized = _normalize_lookup_text(" ".join(str(item or "") for item in texts if str(item or "").strip()))
    if not normalized:
        return "unknown"
    has_male = any(hint in normalized for hint in male_hints)
    has_female = any(hint in normalized for hint in female_hints)
    if has_male and has_female:
        return "mixed"
    if has_male:
        return "male"
    if has_female:
        return "female"
    return "unknown"


def _collect_role_hint_texts(role: str, payload: dict[str, Any], raw_scene: dict[str, Any]) -> list[str]:
    refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    display_labels = payload.get("displayLabelByRole") if isinstance(payload.get("displayLabelByRole"), dict) else {}
    role_type_by_role = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    connected_refs_by_role = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    scene_refs_by_role = raw_scene.get("refsByRole") if isinstance(raw_scene.get("refsByRole"), dict) else {}
    scene_refs_used_by_role = raw_scene.get("refsUsedByRole") if isinstance(raw_scene.get("refsUsedByRole"), dict) else {}
    scene_connected_by_role = raw_scene.get("connectedRefsByRole") if isinstance(raw_scene.get("connectedRefsByRole"), dict) else {}
    role_metadata_by_role = payload.get("roleMetadataByRole") if isinstance(payload.get("roleMetadataByRole"), dict) else {}
    scene_role_metadata = raw_scene.get("roleMetadataByRole") if isinstance(raw_scene.get("roleMetadataByRole"), dict) else {}
    profile_by_role = payload.get("profileByRole") if isinstance(payload.get("profileByRole"), dict) else {}
    identity_by_role = payload.get("identityByRole") if isinstance(payload.get("identityByRole"), dict) else {}
    texts = [str(display_labels.get(role) or ""), str(role_type_by_role.get(role) or ""), str(role or "")]
    texts.extend(_collect_text_fragments(refs.get(role)))
    texts.extend(_collect_text_fragments(refs_by_role.get(role)))
    texts.extend(_collect_text_fragments(connected_refs_by_role.get(role)))
    texts.extend(_collect_text_fragments(scene_refs_by_role.get(role)))
    texts.extend(_collect_text_fragments(scene_refs_used_by_role.get(role)))
    texts.extend(_collect_text_fragments(scene_connected_by_role.get(role)))
    texts.extend(_collect_text_fragments(role_metadata_by_role.get(role)))
    texts.extend(_collect_text_fragments(scene_role_metadata.get(role)))
    texts.extend(_collect_text_fragments(profile_by_role.get(role)))
    texts.extend(_collect_text_fragments(identity_by_role.get(role)))
    return texts


def _infer_scene_performer_presentation(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> str:
    raw_scene = _find_raw_scene_payload(scene, payload)
    role_type_by_role = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    role_candidates: list[str] = []
    for role in scene.actors or []:
        normalized = str(role or "").strip()
        if normalized and normalized not in role_candidates:
            role_candidates.append(normalized)
    for key in ("primaryRole", "primary_role"):
        normalized = str(raw_scene.get(key) or "").strip()
        if normalized and normalized not in role_candidates:
            role_candidates.append(normalized)
    for list_key in ("secondaryRoles", "secondary_roles", "sceneActiveRoles", "scene_active_roles", "participants", "refsUsed", "refs_used"):
        for role in (raw_scene.get(list_key) or []):
            normalized = str(role or "").strip()
            if normalized and normalized not in role_candidates:
                role_candidates.append(normalized)
    if "group" in role_candidates and any(role in role_candidates for role in ("character_1", "character_2")):
        role_candidates = [role for role in role_candidates if role != "group"]
    active_roles: list[str] = []
    for role in role_candidates:
        role_type = str(role_type_by_role.get(role) or "").strip().lower()
        if role_type and role_type in NON_PERFORMER_ROLE_TYPE_HINTS:
            continue
        if _role_is_human_performer(role):
            active_roles.append(role)
    active_roles = list(dict.fromkeys(active_roles))
    if not active_roles:
        return "unknown"
    preferred_character_roles = [role for role in ("character_1", "character_2") if role in active_roles]
    role_set = preferred_character_roles if preferred_character_roles else active_roles
    role_presentations: list[str] = []
    for role in role_set:
        label_based = _infer_presentations_from_texts(
            _collect_role_hint_texts(role, payload, raw_scene),
            male_hints=PERFORMER_MALE_HINTS,
            female_hints=PERFORMER_FEMALE_HINTS,
        )
        fallback = _infer_role_presentation(role, payload, raw_scene=raw_scene)
        chosen = label_based if label_based != "unknown" else fallback
        if chosen in {"male", "female", "mixed"}:
            role_presentations.append(chosen)
    if not role_presentations:
        return "unknown"
    normalized = set(role_presentations)
    if "mixed" in normalized or len(normalized) > 1:
        return "mixed"
    return next(iter(normalized))


def _role_has_explicit_refs(payload: dict[str, Any], role: str) -> bool:
    normalized_role = _normalize_scenario_role(role)
    if not normalized_role:
        return False
    refs_by_role = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    connected_refs_by_role = payload.get("connectedRefsByRole") if isinstance(payload.get("connectedRefsByRole"), dict) else {}
    context_refs = payload.get("context_refs") if isinstance(payload.get("context_refs"), dict) else {}
    connected_summary = payload.get("connected_context_summary") if isinstance(payload.get("connected_context_summary"), dict) else {}
    connected_summary_refs = connected_summary.get("refsByRole") if isinstance(connected_summary.get("refsByRole"), dict) else {}
    for source_map in (refs_by_role, connected_refs_by_role, connected_summary_refs):
        refs = source_map.get(normalized_role)
        if isinstance(refs, list) and any(str(item or "").strip() for item in refs):
            return True
    context_item = context_refs.get(normalized_role) if isinstance(context_refs.get(normalized_role), dict) else {}
    if isinstance(context_item.get("refs"), list) and any(str(item or "").strip() for item in context_item.get("refs")):
        return True
    try:
        if int(context_item.get("count") or 0) > 0:
            return True
    except (TypeError, ValueError):
        pass
    meta = context_item.get("meta") if isinstance(context_item.get("meta"), dict) else {}
    return _coerce_bool(meta.get("connected"), False)


def _build_music_video_cast_identity_lock(payload: dict[str, Any]) -> dict[str, Any]:
    locked_role_presentations: dict[str, str] = {}
    for role in ("character_1", "character_2", "character_3"):
        if not _role_has_explicit_refs(payload, role):
            continue
        texts = _collect_role_hint_texts(role, payload, {})
        presentation = _infer_presentations_from_texts(
            texts,
            male_hints=PERFORMER_MALE_HINTS,
            female_hints=PERFORMER_FEMALE_HINTS,
        )
        if presentation in {"male", "female", "mixed"}:
            locked_role_presentations[role] = presentation
    locked_roles = list(locked_role_presentations.keys())
    unique_presentations = {value for value in locked_role_presentations.values() if value in {"male", "female", "mixed"}}
    cast_identity_locked = bool(locked_roles)
    return {
        "enabled": cast_identity_locked,
        "lockedRoles": locked_roles,
        "lockedRolePresentationByRole": locked_role_presentations,
        "lockReason": (
            "explicit_character_refs_with_identity_hints"
            if cast_identity_locked
            else "no_explicit_character_refs_with_identity_hints"
        ),
        "globalPresentation": ("mixed" if "mixed" in unique_presentations or len(unique_presentations) > 1 else next(iter(unique_presentations), "unknown")),
    }


def _sanitize_cast_identity_text(text: str, *, preserve_presentation: str) -> tuple[str, bool]:
    cleaned = str(text or "")
    if not cleaned:
        return "", False
    if preserve_presentation == "female":
        patterns = (
            (r"\bman\b", "person"),
            (r"\bmen\b", "people"),
            (r"\bmale\b", "person"),
            (r"\bboy\b", "person"),
            (r"\bguy\b", "person"),
            (r"\bмужчина\b", "человек"),
            (r"\bпарень\b", "человек"),
            (r"\bмальчик\b", "человек"),
        )
    elif preserve_presentation == "male":
        patterns = (
            (r"\bwoman\b", "person"),
            (r"\bwomen\b", "people"),
            (r"\bfemale\b", "person"),
            (r"\bgirl\b", "person"),
            (r"\blady\b", "person"),
            (r"\bженщина\b", "человек"),
            (r"\bдевушка\b", "человек"),
            (r"\bдевочка\b", "человек"),
        )
    else:
        return cleaned, False
    changed = False
    for pattern, replacement in patterns:
        updated = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
        if updated != cleaned:
            changed = True
            cleaned = updated
    return cleaned, changed


def _enforce_music_video_cast_identity_lock(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> tuple[ScenarioDirectorStoryboardOut, dict[str, Any]]:
    lock = _build_music_video_cast_identity_lock(payload)
    if not lock.get("enabled"):
        return storyboard_out, lock
    locked_by_role = lock.get("lockedRolePresentationByRole") if isinstance(lock.get("lockedRolePresentationByRole"), dict) else {}

    def _scene_target_presentation(scene: ScenarioDirectorScene) -> str:
        actor_presentations = [locked_by_role.get(role) for role in (scene.actors or []) if locked_by_role.get(role) in {"female", "male", "mixed"}]
        if not actor_presentations:
            actor_presentations = [value for value in locked_by_role.values() if value in {"female", "male", "mixed"}]
        normalized = set(actor_presentations)
        if not normalized:
            return "unknown"
        if "mixed" in normalized or len(normalized) > 1:
            return "mixed"
        return next(iter(normalized))

    total_rewrites = 0
    top_level_fields = ("story_summary", "director_summary", "full_scenario")
    global_presentation = str(lock.get("globalPresentation") or "unknown")
    for field_name in top_level_fields:
        value = str(getattr(storyboard_out, field_name, "") or "")
        rewritten, changed = _sanitize_cast_identity_text(value, preserve_presentation=global_presentation)
        if changed:
            setattr(storyboard_out, field_name, rewritten)
            total_rewrites += 1
    for scene in storyboard_out.scenes:
        target_presentation = _scene_target_presentation(scene)
        for field_name in ("frame_description", "action_in_frame", "image_prompt", "video_prompt", "scene_goal"):
            value = str(getattr(scene, field_name, "") or "")
            rewritten, changed = _sanitize_cast_identity_text(value, preserve_presentation=target_presentation)
            if changed:
                setattr(scene, field_name, rewritten)
                total_rewrites += 1
    lock["textRewritesApplied"] = total_rewrites
    return storyboard_out, lock


def _infer_performance_framing(
    scene: ScenarioDirectorScene,
    *,
    shot_type: str,
    performer_presentation: str,
    transition_candidate: bool,
) -> str:
    shot = str(shot_type or scene.shot_type or "").strip().lower()
    if transition_candidate:
        return "non_performance"
    if shot in {"duet_shared"}:
        return "duet_frame"
    has_human_focus = performer_presentation in {"male", "female", "mixed"} or _scene_has_human_performer(scene)
    if shot in {"close_up"}:
        return "face_close" if has_human_focus else "close_performance"
    if shot in {"medium_close", "medium"}:
        return "medium_performance" if has_human_focus else "non_performance"
    if shot in {"wide", "establishing"}:
        return "wide_performance" if has_human_focus else "non_performance"
    return "non_performance" if not has_human_focus else "medium_performance"


def _build_music_video_viewer_hook(scene: ScenarioDirectorScene, purpose: str, shot_type: str) -> str:
    action = str(scene.action_in_frame or scene.frame_description or scene.scene_goal or "").strip()
    action_tail = f" {action}" if action else ""
    duet_present = "character_1" in (scene.actors or []) and "character_2" in (scene.actors or [])
    if purpose == "hook" and shot_type == "wide":
        return (
            f"Open on a bold silhouette against scale so the clip starts with instant tension and visual authority.{action_tail}"
        ).strip()
    if purpose == "hook":
        return f"Start with an unmistakable first image that feels like the song already in motion.{action_tail}".strip()
    if purpose == "performance":
        if duet_present or shot_type == "duet_shared":
            return (
                f"Hold attention on the emotional connection between two performers inside one shared frame beat.{action_tail}"
            ).strip()
        return f"Lock the viewer on face, breath, and emotional micro-movement so the performance feels personal.{action_tail}".strip()
    if purpose == "transition":
        return f"Create a visible edit pivot where the state changes and the viewer wants the next cut immediately.{action_tail}".strip()
    if purpose == "payoff":
        return f"Deliver the emotional peak with a stronger image beat that releases built tension.{action_tail}".strip()
    if purpose == "ending_hold":
        return f"Leave a lingering final image that stays after the music resolves into distance.{action_tail}".strip()
    shot_label = str(shot_type or "").replace("_", " ").strip() or "scene"
    return f"Keep the clip breathing through contrast in scale, distance, and motion within this {shot_label} beat.{action_tail}".strip()


def _build_music_video_image_prompt(scene: ScenarioDirectorScene) -> str:
    actors = ", ".join(str(actor).replace("_", " ") for actor in (scene.actors or [])[:2] if str(actor).strip())
    lead = str(scene.frame_description or scene.scene_goal or "").strip()
    location = str(scene.location or "").strip()
    emotion = str(scene.emotion or "").strip()
    visual_anchor = ", ".join(str(prop).strip() for prop in (scene.props or [])[:2] if str(prop).strip())
    parts: list[str] = []
    if lead:
        parts.append(lead)
    if actors:
        parts.append(f"Primary subjects: {actors}.")
    if location:
        parts.append(f"Location: {location}.")
    if emotion:
        parts.append(f"Mood/light: {emotion}.")
    if visual_anchor:
        parts.append(f"Visual anchors: {visual_anchor}.")
    parts.append("Compose as a hero keyframe with clear silhouette, depth layering, and strong frame balance.")
    return " ".join(parts).strip()


def _build_music_video_video_prompt(scene: ScenarioDirectorScene) -> str:
    action = str(scene.action_in_frame or scene.scene_goal or "").strip()
    camera = str(scene.camera or "").strip()
    purpose = str(scene.scene_purpose or "").replace("_", " ").strip()
    performance = str(scene.performance_framing or "").replace("_", " ").strip()
    transition_type = str(scene.transition_type or "").replace("_", " ").strip()
    motion_parts: list[str] = []
    if action:
        motion_parts.append(f"Performance motion: {action}.")
    if camera:
        motion_parts.append(f"Camera movement: {camera}.")
    else:
        motion_parts.append("Camera movement: maintain rhythmic push/pull and subtle reframing on beats.")
    if performance:
        motion_parts.append(f"Framing behavior: {performance}.")
    if purpose:
        motion_parts.append(f"Scene intent in time: {purpose}.")
    if scene.render_mode in {"first_last", "first_last_sound"} or scene.resolved_workflow_key == "imag-imag-video-bz":
        frame_state = str(scene.frame_description or scene.scene_goal or "").strip()
        motion_parts.append(
            f"First→last transition: evolve from the opening state '{frame_state}' to a clearly shifted final state with a readable emotional change."
        )
    elif transition_type in {"continuation", "state shift", "state_shift"}:
        motion_parts.append("Transition feel: use this shot as a visual bridge into the next beat, not an isolated loop.")
    else:
        motion_parts.append("Temporal feel: preserve beat-synced micro-movements in fabric, hair, gaze, and body weight.")
    motion_parts.append(f"Transition cue: {transition_type or 'cut'}, paced to music accents.")
    return " ".join(motion_parts).strip()


def _scene_requires_explicit_first_last_prompts(scene: ScenarioDirectorScene) -> bool:
    render_mode = str(scene.render_mode or "").strip().lower()
    ltx_mode = str(scene.ltx_mode or "").strip().lower()
    return bool(scene.needs_two_frames) or render_mode in {"first_last", "first_last_sound"} or ltx_mode in {"f_l", "first_last"}


def _derive_first_last_frame_prompts(scene: ScenarioDirectorScene, raw_scene: dict[str, Any] | None = None) -> tuple[str, str]:
    raw_scene = raw_scene if isinstance(raw_scene, dict) else {}
    explicit_start = str(
        raw_scene.get("startFramePrompt")
        or raw_scene.get("start_frame_prompt")
        or scene.start_frame_prompt
        or ""
    ).strip()
    explicit_end = str(
        raw_scene.get("endFramePrompt")
        or raw_scene.get("end_frame_prompt")
        or scene.end_frame_prompt
        or ""
    ).strip()
    if explicit_start and explicit_end:
        return explicit_start, explicit_end

    base_opening = str(scene.frame_description or scene.scene_goal or scene.image_prompt or scene.video_prompt or "").strip()
    base_closing = str(scene.scene_goal or scene.action_in_frame or scene.video_prompt or scene.image_prompt or base_opening).strip()
    transition_prompt = str(scene.video_prompt or "").strip()
    transition_hint = str(scene.transition_type or "").strip().replace("_", " ") or "state shift"
    transition_semantics = transition_prompt if transition_prompt else f"Transition semantics: {transition_hint}."

    if not explicit_start:
        explicit_start = base_opening
    if not explicit_end:
        explicit_end = (
            f"{base_closing}. Final state after transition: {transition_semantics}".strip()
            if base_closing
            else f"Final visual state after transition: {transition_semantics}".strip()
        )

    if explicit_start and explicit_end and explicit_start == explicit_end:
        explicit_end = f"{explicit_end} Keep it visually changed relative to the opening frame.".strip()
    return explicit_start, explicit_end


def _enhance_music_video_transition_language(scene: ScenarioDirectorScene) -> None:
    transition_kind = str(scene.transition_type or "").strip().lower()
    render_mode = str(scene.render_mode or "").strip().lower()
    purpose = str(scene.scene_purpose or "").strip().lower()
    is_transition_scene = transition_kind in {"state_shift", "continuation"} or render_mode in {"first_last", "first_last_sound"} or purpose == "transition"
    if not is_transition_scene:
        return
    if "visual bridge" not in scene.viewer_hook.lower():
        scene.viewer_hook = f"{scene.viewer_hook} Use this beat as a visual bridge into a new state.".strip()
    if "edit pivot" not in scene.video_prompt.lower():
        scene.video_prompt = f"{scene.video_prompt} Treat the motion as an edit pivot with clear before/after energy."
    if "state change" not in scene.clip_decision_reason.lower():
        scene.clip_decision_reason = f"{scene.clip_decision_reason} state_change_bridge=true;"


def _build_music_video_clip_decision_reason(
    scene: ScenarioDirectorScene,
    *,
    shot_type: str,
    presence_type: str,
    vocal_presentation: str,
    performer_presentation: str,
    lip_sync_compatible: bool,
    lip_sync_compatibility_reason: str,
    forced_transition_scene: bool,
    auto_sound_workflow_enabled: bool,
) -> str:
    emphasis: list[str] = []
    if scene.scene_purpose == "hook":
        emphasis.append("hook")
    elif scene.scene_purpose == "payoff":
        emphasis.append("payoff")
    elif scene.scene_purpose == "ending_hold":
        emphasis.append("ending")
    if forced_transition_scene:
        emphasis.append("forced_first_last")
    emphasis_token = ",".join(emphasis) if emphasis else "none"
    transition_token = scene.transition_type
    if forced_transition_scene and transition_token != "state_shift":
        transition_token = f"{transition_token}(forced)"
    return (
        f"purpose={scene.scene_purpose}; render={scene.render_mode}; workflow={scene.resolved_workflow_key}; "
        f"shot={shot_type}; presence={presence_type}; framing={scene.performance_framing}; "
        f"transition={transition_token}; twoFrames={'true' if scene.needs_two_frames else 'false'}; "
        f"lipSync={'true' if scene.lip_sync else 'false'}; vocal={vocal_presentation}; performer={performer_presentation}; "
        f"compatibility={'true' if lip_sync_compatible else 'false'}({lip_sync_compatibility_reason}); "
        f"soundWorkflowAutoDisabled={'true' if not auto_sound_workflow_enabled else 'false'}; emphasis={emphasis_token}."
    )


def _infer_music_video_presence_type(
    scene: ScenarioDirectorScene,
    *,
    payload: dict[str, Any] | None = None,
    raw_scene: dict[str, Any] | None = None,
) -> str:
    active_roles = _collect_scene_active_character_roles(scene, payload=payload, raw_scene=raw_scene)
    if "character_1" in active_roles and "character_2" in active_roles:
        return "duet"
    if active_roles:
        return "solo"
    return "environment"


def _infer_music_video_style_tone(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> str:
    texts = [
        _scene_text_bundle(scene),
        str(payload.get("source", {}).get("text") or "") if isinstance(payload.get("source"), dict) else "",
        str(payload.get("director_note") or payload.get("directorNote") or ""),
    ]
    normalized = _normalize_lookup_text(" ".join(texts))
    if any(token in normalized for token in ("thriller", "detective", "chase", "pursuit", "stalker", "surveillance", "suspense", "noir", "триллер", "детектив", "преслед", "слежк", "напряж")):
        return "thriller_detective"
    if any(token in normalized for token in ("romance", "romantic", "tender", "love", "couple", "роман", "любов", "нежн")):
        return "romantic"
    return "neutral"


def _resolve_director_genre_intent(payload: dict[str, Any], scene: ScenarioDirectorScene | None = None) -> dict[str, str]:
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    director_note = str(controls.get("directorNote") or controls.get("director_note") or "").strip()
    scene_bundle = _scene_text_bundle(scene) if scene is not None else ""
    normalized = _normalize_lookup_text(f"{director_note} {scene_bundle}")
    if any(token in normalized for token in ("horror", "страш", "жут", "ужас", "dread", "terror")):
        return {
            "directorGenreIntent": "horror_dread",
            "directorGenreReason": "director_note_horror_dread_tokens_detected",
            "directorToneBias": "fear_pressure_unease",
        }
    if any(token in normalized for token in ("social drama", "социальн", "tragic", "траг", "injustice", "бедност", "потер", "утрат")):
        return {
            "directorGenreIntent": "tragic_social_drama",
            "directorGenreReason": "social_conflict_or_tragic_tokens_detected",
            "directorToneBias": "human_cost_social_weight",
        }
    return {
        "directorGenreIntent": "neutral_drama",
        "directorGenreReason": "no_explicit_horror_or_tragic_social_markers",
        "directorToneBias": "observational_emotional_realism",
    }


def _collect_scene_active_character_roles(
    scene: ScenarioDirectorScene,
    *,
    payload: dict[str, Any] | None = None,
    raw_scene: dict[str, Any] | None = None,
) -> list[str]:
    source_payload = payload if isinstance(payload, dict) else {}
    scene_payload = raw_scene if isinstance(raw_scene, dict) else _find_raw_scene_payload(scene, source_payload)

    def _normalized_character_role(role_value: Any) -> str:
        role = _normalize_scenario_role(role_value)
        return role if role in {"character_1", "character_2", "character_3"} else ""

    ordered: list[str] = []

    def _push_role(role_value: Any) -> None:
        normalized = _normalized_character_role(role_value)
        if normalized and normalized not in ordered:
            ordered.append(normalized)

    def _push_list(values: Any) -> None:
        if not isinstance(values, list):
            return
        for role_value in values:
            _push_role(role_value)

    # priority: primary -> secondary/sceneActive -> explicit scene contracts -> actors/refs
    for key in ("primaryRole", "primary_role"):
        _push_role(scene_payload.get(key))

    for key in ("secondaryRoles", "secondary_roles", "sceneActiveRoles", "scene_active_roles"):
        _push_list(scene_payload.get(key))

    for key in ("mustAppear", "must_appear", "participants"):
        _push_list(scene_payload.get(key))

    for key in ("refsUsed", "refs_used"):
        value = scene_payload.get(key)
        if isinstance(value, dict):
            for role_key, role_value in value.items():
                if _coerce_bool(role_value, False) or role_value:
                    _push_role(role_key)
        else:
            _push_list(value)

    for map_key in ("refsUsedByRole", "refs_used_by_role", "refDirectives", "ref_directives"):
        map_value = scene_payload.get(map_key)
        if isinstance(map_value, dict):
            for role_key, role_value in map_value.items():
                if role_value:
                    _push_role(role_key)

    _push_list(scene.actors or [])

    return ordered


def _apply_music_video_role_influence(
    scene: ScenarioDirectorScene,
    *,
    payload: dict[str, Any],
    index: int,
    total: int,
    shot_type: str,
    scene_purpose: str,
    presence_type: str,
    performance_framing: str,
    transition_candidate: bool,
) -> dict[str, Any]:
    role_types, _, _ = _resolve_effective_role_type_by_role(payload)
    raw_scene = _find_raw_scene_payload(scene, payload)
    active_roles = _collect_scene_active_character_roles(scene, payload=payload, raw_scene=raw_scene)
    if not active_roles:
        return {"applied": False, "reason": "no_active_character_roles", "sceneRoleDynamics": "environment"}
    heroes = [role for role in active_roles if role_types.get(role) == "hero"]
    antagonists = [role for role in active_roles if role_types.get(role) == "antagonist"]
    supports = [role for role in active_roles if role_types.get(role) == "support"]
    tone = _infer_music_video_style_tone(scene, payload)
    reason: list[str] = []
    dynamics: list[str] = []
    final = {
        "shot_type": shot_type,
        "scene_purpose": scene_purpose,
        "presence_type": presence_type,
        "performance_framing": performance_framing,
        "transition_emphasis": "",
    }

    if len(heroes) >= 2:
        dynamics.append("shared_hero_duet")
        reason.append("two_heroes_shared_center")
        if not transition_candidate and scene_purpose in {"build", "performance"}:
            final["scene_purpose"] = "performance"
        if final["presence_type"] in {"duet", "solo"}:
            final["presence_type"] = "shared_duet"
        if final["shot_type"] not in {"duet_shared", "close_up"}:
            final["shot_type"] = "duet_shared"
        if final["performance_framing"] not in {"duet_frame", "face_close"}:
            final["performance_framing"] = "duet_frame"

    if heroes:
        dynamics.append("hero_anchor")
        if not transition_candidate and scene_purpose in {"build", "performance", "payoff", "ending_hold"}:
            if index in {1, max(0, total - 2), total - 1}:
                reason.append("hero_emotional_anchor_priority")
                if index == total - 1:
                    final["scene_purpose"] = "ending_hold"
                elif index == max(0, total - 2):
                    final["scene_purpose"] = "payoff"
                else:
                    final["scene_purpose"] = "performance"
                if final["shot_type"] in {"wide", "detail_insert"}:
                    final["shot_type"] = "close_up" if index != max(0, total - 2) else "medium"
                if final["performance_framing"] in {"non_performance", "wide_performance"}:
                    final["performance_framing"] = "face_close"

    if antagonists:
        dynamics.append("asymmetric_counter_presence")
        reason.append("antagonist_counter_presence")
        if final["presence_type"] == "duet":
            final["presence_type"] = "counter_presence"
        if tone == "thriller_detective":
            reason.append("thriller_detective_tension_weight")
            final["transition_emphasis"] = "watcher_pursuit_block"
            if final["shot_type"] in {"duet_shared", "close_up"}:
                final["shot_type"] = "medium" if not transition_candidate else final["shot_type"]
            if final["performance_framing"] == "duet_frame":
                final["performance_framing"] = "asymmetric_duet"
            if final["scene_purpose"] == "performance" and heroes and len(heroes) == 1:
                final["scene_purpose"] = "build"
        elif tone == "romantic":
            reason.append("romantic_soft_antagonist_not_neutral")
            if final["presence_type"] in {"duet", "shared_duet"}:
                final["presence_type"] = "counter_presence_soft"
        else:
            if final["performance_framing"] == "duet_frame":
                final["performance_framing"] = "asymmetric_duet"

    if supports and heroes:
        dynamics.append("support_secondary_presence")
        reason.append("support_kept_secondary_to_hero")
        if final["presence_type"] == "duet" and not antagonists:
            final["presence_type"] = "hero_with_support"
        if final["scene_purpose"] in {"payoff", "ending_hold"} and len(heroes) == 1:
            final["performance_framing"] = "face_close"

    if "character_2" in active_roles and "character_1" in active_roles:
        dynamics.append("duet_pair_protected")
    applied = bool(reason)
    return {
        "applied": applied,
        "reason": ";".join(reason) if reason else "role_defaults",
        "sceneRoleDynamics": ",".join(dynamics) if dynamics else "neutral",
        **final,
    }


def _extract_role_identity_markers(
    role: str,
    payload: dict[str, Any],
    *,
    raw_scene: dict[str, Any] | None = None,
) -> dict[str, Any]:
    scene_payload = raw_scene if isinstance(raw_scene, dict) else {}
    texts = _collect_role_hint_texts(role, payload, scene_payload)
    texts.extend(_collect_text_fragments(scene_payload.get("identityHintsByRole")))
    texts.extend(_collect_text_fragments(scene_payload.get("identity_hints_by_role")))
    texts.extend(_collect_text_fragments(scene_payload.get("sceneIdentityHints")))
    texts.extend(_collect_text_fragments(scene_payload.get("scene_identity_hints")))
    normalized = _normalize_lookup_text(" ".join(texts))

    face_terms = [term for term in ("oval face", "angular", "jawline", "high cheekbone", "круглое лицо", "скулы", "челюст") if term in normalized][:3]
    hair_face_terms = [term for term in ("blonde", "brunette", "redhead", "short hair", "long hair", "curly", "beard", "mustache", "усы", "борода", "кудр", "коротк", "длинн", "светл", "темн") if term in normalized][:4]
    body_terms = [term for term in ("slim", "athletic", "curvy", "fuller", "heavy", "lean", "broad", "tall", "petite", "стройн", "полн", "крупн", "худощ", "атлет", "высок", "миниат") if term in normalized][:4]
    outfit_terms = [term for term in ("dress", "jacket", "coat", "hoodie", "armor", "uniform", "suit", "плать", "куртк", "пальто", "форма", "костюм") if term in normalized][:4]
    accessory_terms = [term for term in ("glasses", "earring", "necklace", "hat", "ring", "очк", "серьг", "кулон", "шляп", "кольц") if term in normalized][:3]
    age_gender_terms = [term for term in ("male", "female", "man", "woman", "young", "older", "муж", "жен", "девуш", "парень", "возраст") if term in normalized][:3]

    display_labels = payload.get("displayLabelByRole") if isinstance(payload.get("displayLabelByRole"), dict) else {}
    label = str(display_labels.get(role) or role).strip()
    return {
        "label": label,
        "face": face_terms,
        "hair_face": hair_face_terms,
        "body": body_terms,
        "outfit": outfit_terms,
        "accessories": accessory_terms,
        "age_gender": age_gender_terms,
    }


def _build_multi_character_identity_lock(scene: ScenarioDirectorScene, payload: dict[str, Any]) -> dict[str, Any]:
    raw_scene = _find_raw_scene_payload(scene, payload)
    active_roles = _collect_scene_active_character_roles(scene, payload=payload, raw_scene=raw_scene)
    if len(active_roles) < 2:
        return {
            "enabled": False,
            "distinctCharacterSeparation": False,
            "identityLockByRole": {},
            "appearanceDriftRisk": "low_single_character",
            "duetLockEnabled": False,
            "duetCompositionMode": "single_focus",
            "secondaryRoleVisibilityRequirement": "none",
            "character2DriftGuard": "not_required",
            "duetIdentityContract": "",
            "contract": "",
        }
    lock_by_role = {role: _extract_role_identity_markers(role, payload, raw_scene=raw_scene) for role in active_roles}
    is_strict_duet = "character_1" in active_roles and "character_2" in active_roles
    identity_marker_count = 0
    for role in active_roles:
        markers = lock_by_role.get(role) or {}
        identity_marker_count += sum(len(markers.get(bucket) or []) for bucket in ("face", "hair_face", "body", "outfit", "accessories", "age_gender"))
    identity_marker_strength = min(1.0, identity_marker_count / max(4.0, len(active_roles) * 6.0))
    composition_strength = 0.9 if str(scene.shot_type or "") in {"duet_shared", "medium"} else 0.6
    if str(scene.performance_framing or "") in {"duet_frame", "asymmetric_duet"}:
        composition_strength = max(composition_strength, 0.95)
    must_appear = raw_scene.get("mustAppear") or raw_scene.get("must_appear") or raw_scene.get("participants") or []
    presence_contract_strength = 1.0 if len([r for r in must_appear if _normalize_scenario_role(r) in active_roles]) >= 2 else 0.55
    duet_lock_strength = 1.0 if is_strict_duet else 0.7
    active_role_count = len(active_roles)
    drift_score = (
        0.25 * min(1.0, active_role_count / 3.0)
        + 0.75 * (1.0 - (0.35 * duet_lock_strength + 0.25 * identity_marker_strength + 0.2 * composition_strength + 0.2 * presence_contract_strength))
    )
    if drift_score >= 0.7:
        risk = "high_character2_drift" if "character_2" in active_roles and duet_lock_strength < 0.75 else "high_multi_character_drift"
    elif drift_score >= 0.52:
        risk = "elevated_character2_drift" if "character_2" in active_roles else "elevated_multi_character_drift"
    elif drift_score >= 0.35:
        risk = "medium_multi_character_drift"
    else:
        risk = "low_locked_duet" if is_strict_duet else "low_multi_character_drift"
    clauses: list[str] = [
        "Distinct character separation is mandatory: keep every character visually unique in face, body silhouette, and outfit identity.",
        "Do not merge faces, do not average body types, do not transfer hairstyle/facial structure, and do not swap outfits between characters.",
        "Both active characters must stay on-screen when scene contract indicates duet/shared-presence/two-character framing.",
    ]
    for idx, role in enumerate(active_roles, start=1):
        markers = lock_by_role.get(role) or {}
        identity_bits = [f"slot {idx}={role} ({markers.get('label') or role})"]
        if markers.get("face"):
            identity_bits.append(f"face:{'/'.join(markers.get('face') or [])}")
        if markers.get("body"):
            identity_bits.append(f"body:{'/'.join(markers.get('body') or [])}")
        if markers.get("hair_face"):
            identity_bits.append(f"face-hair:{'/'.join(markers.get('hair_face') or [])}")
        if markers.get("outfit"):
            identity_bits.append(f"outfit:{'/'.join(markers.get('outfit') or [])}")
        if markers.get("accessories"):
            identity_bits.append(f"accessories:{'/'.join(markers.get('accessories') or [])}")
        if markers.get("age_gender"):
            identity_bits.append(f"age-gender:{'/'.join(markers.get('age_gender') or [])}")
        clauses.append(f"Preserve {'; '.join(identity_bits)}.")
    if "character_1" in active_roles and "character_2" in active_roles:
        clauses.append("character_2 must remain visibly distinct from character_1 and must never become a softened copy.")
    duet_contract = ""
    if is_strict_duet:
        duet_contract = (
            "DUET IDENTITY CONTRACT: keep character_1 and character_2 as two different human identities; "
            "never merge or average faces/bodies/clothing; preserve distinct face architecture, hair identity, silhouette, body build, and outfit silhouette; "
            "keep both active characters legible in one coherent frame; avoid twinization, face averaging, and clothing convergence."
        )
        clauses.append(duet_contract)
    return {
        "enabled": True,
        "distinctCharacterSeparation": True,
        "identityLockByRole": lock_by_role,
        "appearanceDriftRisk": risk,
        "duetLockEnabled": is_strict_duet,
        "duetCompositionMode": "hard_duet_split" if is_strict_duet else "multi_character_separation",
        "secondaryRoleVisibilityRequirement": "both_roles_legible_same_frame" if is_strict_duet else "secondary_roles_must_be_readable",
        "character2DriftGuard": (
            "strict_character2_not_softened_copy_of_character1"
            if is_strict_duet
            else ("soft_guard_for_character2" if "character_2" in active_roles else "not_required")
        ),
        "duetIdentityContract": duet_contract,
        "driftRiskInputs": {
            "activeRoleCount": active_role_count,
            "duetLockStrength": round(duet_lock_strength, 3),
            "identityMarkerStrength": round(identity_marker_strength, 3),
            "compositionStrength": round(composition_strength, 3),
            "presenceContractStrength": round(presence_contract_strength, 3),
            "driftScore": round(drift_score, 3),
        },
        "contract": " ".join(clauses),
    }


def _select_forced_music_video_transition_index(
    scenes: list[ScenarioDirectorScene],
    *,
    payload: dict[str, Any] | None = None,
) -> int | None:
    if len(scenes) < 5:
        return None
    candidate_indices = [idx for idx in range(1, len(scenes) - 1)]
    if not candidate_indices:
        return None
    scored: list[tuple[int, int]] = []
    for idx in candidate_indices:
        scene = scenes[idx]
        shot_type = _infer_music_video_shot_type(scene)
        raw_scene = _find_raw_scene_payload(scene, payload if isinstance(payload, dict) else {})
        presence = _infer_music_video_presence_type(scene, payload=payload, raw_scene=raw_scene)
        text_bundle = _scene_text_bundle(scene).lower()
        score = 0
        if shot_type == "duet_shared" or presence == "duet":
            score += 5
        if any(token in text_bundle for token in ("meet", "meeting", "touch", "take hand", "turn to each other", "walk together", "shared horizon")):
            score += 4
        if any(token in text_bundle for token in ("road", "highway", "path", "leave", "depart", "horizon", "into distance")):
            score += 3
        if "close" in shot_type or shot_type == "detail_insert":
            score += 1
        if idx >= len(scenes) // 2:
            score += 1
        scored.append((score, idx))
    scored.sort(key=lambda item: (-item[0], item[1]))
    best_score, best_idx = scored[0]
    return best_idx if best_score > 0 else candidate_indices[0]


def _is_lipsync_voice_compatible(vocal_presentation: str, performer_presentation: str) -> tuple[bool, str]:
    vocal = str(vocal_presentation or "unknown").strip().lower() or "unknown"
    performer = str(performer_presentation or "unknown").strip().lower() or "unknown"
    if vocal == "unknown":
        return False, "unknown_vocal_presentation"
    if performer == "unknown":
        return False, "unknown_performer_presentation"
    if vocal == "male" and performer == "female":
        return False, "male_vocal_female_performer_conflict"
    if vocal == "female" and performer == "male":
        return False, "female_vocal_male_performer_conflict"
    if vocal == "mixed" or performer == "mixed":
        return False, "mixed_presentation_not_supported"
    if vocal == performer:
        return True, "compatible"
    return False, "incompatible_presentation"


def _extract_lip_sync_text(scene: ScenarioDirectorScene) -> str:
    if str(scene.local_phrase or "").strip():
        return str(scene.local_phrase or "").strip()
    fallback = str(scene.what_from_audio_this_scene_uses or scene.scene_goal or "").strip()
    if not fallback:
        return ""
    words = fallback.split()
    return " ".join(words[:8]).strip()


def _apply_music_video_mode_policy(
    storyboard_out: ScenarioDirectorStoryboardOut,
    *,
    content_type_policy: dict[str, Any],
    payload: dict[str, Any],
    audio_duration_sec: float | None = None,
) -> ScenarioDirectorStoryboardOut:
    scenes = storyboard_out.scenes or []
    if not scenes:
        return storyboard_out
    has_existing_first_last = any(
        str(scene.render_mode or "").strip().lower() in {"first_last", "first_last_sound"} or _coerce_bool(scene.needs_two_frames, False)
        for scene in scenes
    )
    forced_first_last_index: int | None = None
    if len(scenes) >= 5 and not has_existing_first_last:
        forced_first_last_index = _select_forced_music_video_transition_index(scenes, payload=payload)
    max_lip_sync = max(1, min(3, len(scenes) // 2 if len(scenes) <= 6 else 3))
    lip_sync_used = 0
    prev_lip_sync = False
    prev_two_frames = False
    prev_shot_type = ""
    for index, scene in enumerate(scenes):
        shot_type = _infer_music_video_shot_type(scene)
        raw_scene = _find_raw_scene_payload(scene, payload)
        presence_type = _infer_music_video_presence_type(scene, payload=payload, raw_scene=raw_scene)
        if index == 0 and shot_type not in {"wide"}:
            shot_type = "wide"
        elif index == 1 and shot_type in {"wide"}:
            shot_type = "close_up" if _scene_has_human_performer(scene) else "medium"
        elif index > 1 and shot_type == prev_shot_type:
            if shot_type == "wide":
                shot_type = "close_up" if _scene_has_human_performer(scene) else "medium"
            elif shot_type in {"close_up", "detail_insert"}:
                shot_type = "wide" if index % 2 == 0 else "medium"
            else:
                shot_type = "duet_shared" if presence_type == "duet" else ("close_up" if index % 2 == 0 else "wide")
        scene.shot_type = shot_type
        duration = max(0.0, _safe_float(scene.duration, scene.time_end - scene.time_start))
        if duration > 0:
            scene.requested_duration_sec = duration
        else:
            scene.requested_duration_sec = _safe_float(scene.requested_duration_sec, 0.0)

        auto_sound_workflow_enabled = False
        has_sound_cue = bool(str(scene.sfx or "").strip() or str(scene.local_phrase or "").strip())
        transition_candidate = _scene_has_transition_evidence(scene) and index > 0 and not prev_two_frames
        forced_transition_scene = forced_first_last_index is not None and index == forced_first_last_index
        if forced_transition_scene:
            transition_candidate = True
        vocal_presentation = _infer_vocal_presentation(scene, payload)
        performer_presentation = _infer_scene_performer_presentation(scene, payload)
        inferred_framing = _infer_performance_framing(
            scene,
            shot_type=shot_type,
            performer_presentation=performer_presentation,
            transition_candidate=transition_candidate,
        )
        performance_framing = str(scene.performance_framing or "").strip() or inferred_framing
        scene.scene_purpose = _infer_music_video_scene_purpose(
            index,
            len(scenes),
            scene,
            transition_candidate=transition_candidate,
            performance_framing=performance_framing,
            performer_presentation=performer_presentation,
        )
        role_influence = _apply_music_video_role_influence(
            scene,
            payload=payload,
            index=index,
            total=len(scenes),
            shot_type=shot_type,
            scene_purpose=scene.scene_purpose,
            presence_type=presence_type,
            performance_framing=performance_framing,
            transition_candidate=transition_candidate,
        )
        shot_type = str(role_influence.get("shot_type") or shot_type)
        presence_type = str(role_influence.get("presence_type") or presence_type)
        performance_framing = str(role_influence.get("performance_framing") or performance_framing)
        scene.scene_purpose = str(role_influence.get("scene_purpose") or scene.scene_purpose)
        # Persist role-influenced composition back into final scene state.
        scene.shot_type = shot_type
        scene.performance_framing = performance_framing
        close_capable = shot_type not in {"wide"} and performance_framing not in {"non_performance", "wide_performance"}
        lip_sync_base_candidate = (
            _scene_has_human_performer(scene)
            and close_capable
            and _scene_has_lip_sync_signal(scene)
            and not transition_candidate
            and not prev_lip_sync
            and lip_sync_used < max_lip_sync
        )
        lip_sync_compatible, lip_sync_compatibility_reason = _is_lipsync_voice_compatible(vocal_presentation, performer_presentation)
        lip_sync_candidate = lip_sync_base_candidate and lip_sync_compatible

        render_mode = "image_video"
        resolved_workflow = str(content_type_policy.get("clipWorkflowDefault") or "image-video")
        ltx_mode = "i2v"
        needs_two_frames = False
        continuation = _coerce_bool(scene.continuation_from_previous, False) or scene.ltx_mode == "continuation"
        send_audio_to_generator = False
        lip_sync = False
        transition_type = "cut" if index > 0 else "cold_open"
        workflow_reason = "Default clip workflow for standard image-to-video scene."
        lip_sync_reason = "Not a lip-sync scene."
        audio_slice_reason = "Audio slice is not required."

        if lip_sync_candidate and not forced_transition_scene:
            render_mode = "lip_sync_music"
            resolved_workflow = str(content_type_policy.get("clipWorkflowLipSync") or "image-lipsink-video-music")
            ltx_mode = "lip_sync"
            lip_sync = True
            send_audio_to_generator = True
            performance_framing = "face_close" if performance_framing == "" else performance_framing
            scene_start = _safe_float(scene.time_start, 0.0)
            scene_end = max(scene_start, _safe_float(scene.time_end, scene_start))
            audio_cap = _safe_float(audio_duration_sec, 0.0) if audio_duration_sec is not None else 0.0
            slice_upper_bound = scene_end
            if audio_cap > 0:
                slice_upper_bound = max(scene_start, min(scene_end, audio_cap))
            start_sec = scene_start
            end_sec = min(scene_start + 5.0, slice_upper_bound)
            if end_sec < start_sec:
                end_sec = start_sec
            scene.audio_slice_start_sec = start_sec
            scene.audio_slice_end_sec = end_sec
            scene.audio_slice_expected_duration_sec = round(max(0.0, end_sec - start_sec), 3)
            scene.lip_sync_text = _extract_lip_sync_text(scene)
            workflow_reason = "Lip-sync workflow selected for close human vocal articulation."
            lip_sync_reason = f"Local vocal phrase + human close framing detected; compatibility={lip_sync_compatibility_reason}."
            audio_slice_reason = "Slice clamped to scene vocal window (max ~5s) and timeline bounds."
            lip_sync_used += 1
        elif transition_candidate:
            needs_two_frames = True
            transition_type = "state_shift"
            if has_sound_cue and auto_sound_workflow_enabled:
                render_mode = "first_last_sound"
                resolved_workflow = str(content_type_policy.get("clipWorkflowFirstLastSound") or "imag-imag-video-zvuk")
                ltx_mode = "f_l_as"
                workflow_reason = "First-last + sound workflow for controlled transition with sound cue."
            else:
                render_mode = "first_last"
                resolved_workflow = str(content_type_policy.get("clipWorkflowFirstLast") or "imag-imag-video-bz")
                ltx_mode = "f_l"
                workflow_reason = (
                    "First-last workflow for controlled visual state transition; sound workflow auto-disabled in music_video."
                    if has_sound_cue and not auto_sound_workflow_enabled
                    else "First-last workflow for controlled visual state transition."
                )
        elif has_sound_cue and auto_sound_workflow_enabled:
            render_mode = "image_video_sound"
            resolved_workflow = str(content_type_policy.get("clipWorkflowSound") or "image-video-golos-zvuk")
            ltx_mode = "i2v_as"
            workflow_reason = "Sound-aware workflow selected for SFX/short phrase support."
        elif has_sound_cue and not auto_sound_workflow_enabled:
            workflow_reason = "Sound cue detected but auto sound workflow is disabled for music_video; using base image-video."

        if lip_sync_base_candidate and not lip_sync_compatible:
            lip_sync_reason = f"Lip-sync candidate rejected by compatibility gate: {lip_sync_compatibility_reason}."

        if continuation and not needs_two_frames and not lip_sync:
            ltx_mode = "continuation"
            scene.start_frame_source = "previous_frame"
            transition_type = "continuation"
        elif not continuation and index > 0 and not needs_two_frames and not lip_sync and (index % 4 == 0):
            continuation = True
            ltx_mode = "continuation"
            scene.start_frame_source = "previous_frame"
            transition_type = "continuation"
        elif needs_two_frames and scene.start_frame_source == "previous_frame":
            scene.start_frame_source = "first_frame"
        elif scene.start_frame_source == "previous_frame" and not continuation:
            scene.start_frame_source = "new"

        scene.needs_two_frames = needs_two_frames
        scene.continuation_from_previous = continuation
        scene.render_mode = render_mode
        scene.resolved_workflow_key = resolved_workflow
        scene.ltx_mode = ltx_mode
        previous_reason = str(scene.ltx_reason or "").strip()
        final_reason = workflow_reason
        if previous_reason and previous_reason != workflow_reason:
            final_reason = f"{workflow_reason} Context: {previous_reason}"
        scene.ltx_reason = _normalize_ltx_reason(final_reason, ltx_mode, narration_mode=scene.narration_mode)
        scene.lip_sync = lip_sync
        scene.send_audio_to_generator = send_audio_to_generator
        scene.performance_framing = performance_framing
        scene.transition_type = transition_type if not str(scene.transition_type or "").strip() or scene.transition_type == "cut" else scene.transition_type
        if forced_transition_scene:
            scene.scene_purpose = "transition"
        identity_lock = _build_multi_character_identity_lock(scene, payload)
        genre_intent = _resolve_director_genre_intent(payload, scene)
        scene.image_prompt = _build_music_video_image_prompt(scene)
        scene.video_prompt = _build_music_video_video_prompt(scene)
        identity_contract = str(identity_lock.get("contract") or "").strip()
        if identity_contract:
            scene.image_prompt = f"{scene.image_prompt} {identity_contract}".strip()
            scene.video_prompt = f"{scene.video_prompt} {identity_contract}".strip()
        # Final derived debug layer (must reflect final scene state, not intermediate steps).
        final_shot_type = str(scene.shot_type or shot_type).strip() or shot_type
        final_presence_type = str(role_influence.get("presence_type") or _infer_music_video_presence_type(scene, payload=payload, raw_scene=raw_scene))
        scene.viewer_hook = _build_music_video_viewer_hook(scene, scene.scene_purpose, final_shot_type)
        if _coerce_bool(role_influence.get("applied"), False):
            role_reason = str(role_influence.get("reason") or "").strip()
            scene.viewer_hook = f"{scene.viewer_hook} Role dynamics: {str(role_influence.get('sceneRoleDynamics') or 'active')}."
            if role_reason:
                scene.viewer_hook = f"{scene.viewer_hook} Dramaturgic reason: {role_reason}."
        scene.clip_decision_reason = _build_music_video_clip_decision_reason(
            scene,
            shot_type=final_shot_type,
            presence_type=final_presence_type,
            vocal_presentation=vocal_presentation,
            performer_presentation=performer_presentation,
            lip_sync_compatible=lip_sync_compatible,
            lip_sync_compatibility_reason=lip_sync_compatibility_reason,
            forced_transition_scene=forced_transition_scene,
            auto_sound_workflow_enabled=auto_sound_workflow_enabled,
        )
        scene.clip_decision_reason = (
            f"{scene.clip_decision_reason} roleInfluenceApplied={'true' if _coerce_bool(role_influence.get('applied'), False) else 'false'}"
            f"; roleInfluenceReason={str(role_influence.get('reason') or 'none')}"
            f"; sceneRoleDynamics={str(role_influence.get('sceneRoleDynamics') or 'neutral')}"
            f"; multiCharacterIdentityLock={'true' if _coerce_bool(identity_lock.get('enabled'), False) else 'false'}"
            f"; distinctCharacterSeparation={'true' if _coerce_bool(identity_lock.get('distinctCharacterSeparation'), False) else 'false'}"
            f"; duetLockEnabled={'true' if _coerce_bool(identity_lock.get('duetLockEnabled'), False) else 'false'}"
            f"; duetCompositionMode={str(identity_lock.get('duetCompositionMode') or 'none')}"
            f"; secondaryRoleVisibilityRequirement={str(identity_lock.get('secondaryRoleVisibilityRequirement') or 'none')}"
            f"; character2DriftGuard={str(identity_lock.get('character2DriftGuard') or 'none')}"
            f"; directorGenreIntent={str(genre_intent.get('directorGenreIntent') or 'neutral_drama')}"
            f"; directorToneBias={str(genre_intent.get('directorToneBias') or 'observational_emotional_realism')}"
            f"; appearanceDriftRisk={str(identity_lock.get('appearanceDriftRisk') or 'none')}."
        )
        scene.role_influence_applied = _coerce_bool(role_influence.get("applied"), False)
        scene.role_influence_reason = str(role_influence.get("reason") or "none")
        scene.scene_role_dynamics = str(role_influence.get("sceneRoleDynamics") or "neutral")
        scene.multi_character_identity_lock = _coerce_bool(identity_lock.get("enabled"), False)
        scene.distinct_character_separation = _coerce_bool(identity_lock.get("distinctCharacterSeparation"), False)
        scene.duet_lock_enabled = _coerce_bool(identity_lock.get("duetLockEnabled"), False)
        scene.duet_composition_mode = str(identity_lock.get("duetCompositionMode") or "")
        scene.secondary_role_visibility_requirement = str(identity_lock.get("secondaryRoleVisibilityRequirement") or "")
        scene.character2_drift_guard = str(identity_lock.get("character2DriftGuard") or "")
        scene.duet_identity_contract = str(identity_lock.get("duetIdentityContract") or "")
        scene.appearance_drift_risk = str(identity_lock.get("appearanceDriftRisk") or "none")
        scene.director_genre_intent = str(genre_intent.get("directorGenreIntent") or "neutral_drama")
        scene.director_genre_reason = str(genre_intent.get("directorGenreReason") or "fallback")
        scene.director_tone_bias = str(genre_intent.get("directorToneBias") or "observational_emotional_realism")
        _enhance_music_video_transition_language(scene)
        scene.workflow_decision_reason = workflow_reason
        scene.lip_sync_decision_reason = lip_sync_reason
        scene.audio_slice_decision_reason = audio_slice_reason

        if not lip_sync:
            scene.lip_sync_text = ""
            scene.audio_slice_start_sec = 0.0
            scene.audio_slice_end_sec = 0.0
            scene.audio_slice_expected_duration_sec = 0.0

        prev_lip_sync = lip_sync
        prev_two_frames = needs_two_frames
        prev_shot_type = str(scene.shot_type or shot_type)
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


def _enforce_clip_phrase_and_duration_splits(storyboard_out: ScenarioDirectorStoryboardOut) -> ScenarioDirectorStoryboardOut:
    scenes = storyboard_out.scenes or []
    if not scenes:
        return storyboard_out
    next_scenes: list[ScenarioDirectorScene] = []
    for scene in scenes:
        duration = max(0.0, _safe_float(scene.duration, _safe_float(scene.time_end, 0.0) - _safe_float(scene.time_start, 0.0)))
        phrase_blob = str(scene.local_phrase or "").strip()
        phrase_chunks = [chunk.strip() for chunk in re.split(r"(?:\s*[/|]\s*|\n+|(?<=[\.\!\?;])\s+)", phrase_blob) if chunk.strip()]
        merged_phrase_risk = len(phrase_chunks) >= 2
        should_split = merged_phrase_risk or duration > 5.5
        if not should_split:
            next_scenes.append(scene)
            continue
        start = _safe_float(scene.time_start, 0.0)
        end = max(start, _safe_float(scene.time_end, start))
        midpoint = round((start + end) / 2.0, 3)
        split_at = max(start + 1.5, min(end - 1.5, midpoint))
        if split_at <= start or split_at >= end:
            next_scenes.append(scene)
            continue

        base = scene.model_dump(mode="python")
        left_data = {**base, "scene_id": f"{scene.scene_id}_A", "time_start": start, "time_end": split_at, "duration": round(max(0.0, split_at - start), 3)}
        right_data = {**base, "scene_id": f"{scene.scene_id}_B", "time_start": split_at, "time_end": end, "duration": round(max(0.0, end - split_at), 3)}
        if phrase_chunks:
            pivot = max(1, len(phrase_chunks) // 2)
            left_data["local_phrase"] = " ".join(phrase_chunks[:pivot]).strip() or left_data.get("local_phrase")
            right_data["local_phrase"] = " ".join(phrase_chunks[pivot:]).strip() or right_data.get("local_phrase")
        reason_value = "phrase_boundary" if merged_phrase_risk else "duration_overflow"
        left_data["boundary_reason"] = "phrase" if merged_phrase_risk else (left_data.get("boundary_reason") or "fallback")
        right_data["boundary_reason"] = "phrase" if merged_phrase_risk else (right_data.get("boundary_reason") or "fallback")
        for chunk_data in (left_data, right_data):
            chunk_data["clip_decision_reason"] = _append_decision_flag(chunk_data.get("clip_decision_reason"), "mergedPhraseRisk", merged_phrase_risk)
            chunk_data["clip_decision_reason"] = _append_decision_flag(chunk_data.get("clip_decision_reason"), "splitByPhraseBoundary", merged_phrase_risk)
            chunk_data["clip_decision_reason"] = _append_decision_flag(chunk_data.get("clip_decision_reason"), "lyricalMergeRejected", merged_phrase_risk)
            chunk_data["clip_decision_reason"] = _append_decision_flag(chunk_data.get("clip_decision_reason"), "autoSplitReason", reason_value)
            chunk_data["workflow_decision_reason"] = _append_decision_flag(chunk_data.get("workflow_decision_reason"), "autoSplitReason", reason_value)
        next_scenes.append(ScenarioDirectorScene.model_validate(left_data))
        next_scenes.append(ScenarioDirectorScene.model_validate(right_data))
    storyboard_out.scenes = next_scenes
    return storyboard_out


def _harden_storyboard_out(storyboard_out: ScenarioDirectorStoryboardOut, payload: dict[str, Any]) -> ScenarioDirectorStoryboardOut:
    content_type_policy = _get_content_type_policy(payload)
    audio_duration_sec = _resolve_audio_duration_sec(payload)
    storyboard_out = _apply_scene_count_limit(storyboard_out)
    storyboard_out = _filter_or_repair_weak_scenes(storyboard_out)
    storyboard_out = _enforce_character_lock(payload, storyboard_out)
    storyboard_out, _ = _enforce_explicit_role_assignments(payload, storyboard_out)
    storyboard_out = _apply_timing_variation(storyboard_out)
    storyboard_out = _rebalance_ltx_modes(storyboard_out)
    storyboard_out = _normalize_scene_timeline(storyboard_out)
    if content_type_policy.get("value") == "music_video":
        storyboard_out = _enforce_clip_phrase_and_duration_splits(storyboard_out)
        storyboard_out = _normalize_scene_timeline(storyboard_out)
        storyboard_out = _apply_music_video_mode_policy(
            storyboard_out,
            content_type_policy=content_type_policy,
            payload=payload,
            audio_duration_sec=audio_duration_sec,
        )
        storyboard_out.music_prompt = ""
    storyboard_out = _limit_lip_sync_usage(storyboard_out, content_type_policy=content_type_policy if content_type_policy.get("value") == "music_video" else None)
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
    content_type_policy = _get_content_type_policy(payload)
    is_music_video_mode = str(content_type_policy.get("value") or "").strip().lower() == "music_video"
    director_note_text = str(director_controls.get("directorNote") or director_controls.get("director_note") or "").strip()
    no_text_fallback_mode = "neutral_audio_literal" if not director_note_text else "off"
    authorial_interpretation_level = "low" if no_text_fallback_mode == "neutral_audio_literal" else "medium"
    audio_literalness_level = "high" if no_text_fallback_mode == "neutral_audio_literal" else "balanced"
    global_genre_intent = _resolve_director_genre_intent(payload, None)
    story_core_source = "director_note" if is_music_video_mode and director_note_text else "source_of_truth"
    story_frame_source = "director_note" if is_music_video_mode and director_note_text else "source_of_truth"
    rhythm_source = "audio" if is_music_video_mode else ""
    story_frame_source_reason = "director_note_present" if is_music_video_mode and director_note_text else "director_note_empty_use_source_truth"
    rhythm_source_reason = "audio_drives_pacing_and_transitions" if is_music_video_mode else ""
    cast_identity_lock = _build_music_video_cast_identity_lock(payload) if is_music_video_mode else {"enabled": False}
    story_core_reason = (
        "music_video_with_director_note_story_frame_plus_audio_rhythm_driver"
        if story_core_source == "director_note"
        else "music_video_without_director_note_or_non_music_mode_defaults_to_source_truth"
    )
    raw_role_type_by_role = payload.get("roleTypeByRole") if isinstance(payload.get("roleTypeByRole"), dict) else {}
    effective_role_type_by_role, _, _ = _resolve_effective_role_type_by_role(payload)
    role_type_by_role: dict[str, str] = {}
    for role, role_type in raw_role_type_by_role.items():
        normalized_role = _normalize_scenario_role(role)
        clean_type = str(role_type or "").strip().lower()
        if normalized_role and clean_type in ALLOWED_EXPLICIT_ROLE_TYPES:
            role_type_by_role[normalized_role] = clean_type
    for role, role_type in effective_role_type_by_role.items():
        normalized_role = _normalize_scenario_role(role)
        if normalized_role:
            role_type_by_role[normalized_role] = str(role_type or "").strip().lower() or "auto"
    mode_source_policy = (
        (
            "MODE POLICY (music_video):\n"
            f"- storyCoreSource={story_core_source}.\n"
            f"- storyFrameSource={story_frame_source}.\n"
            f"- rhythmSource={rhythm_source}.\n"
            "- If storyCoreSource=director_note: use director note as story frame (setting/concept/arc), "
            "while audio remains mandatory for rhythm, emotion, scene timing, energy progression, and transition timing.\n"
            "- If storyCoreSource=source_of_truth: derive story frame from source/audio semantics and transcript.\n"
            "- Refs remain identity/world anchors: character refs define cast; location/style/props refs define world and must not be replaced by text.\n"
            "- CAST IDENTITY LOCK: if explicit character refs already imply role identity/presentation, director note must not rewrite gender presentation or pair composition.\n"
        )
        if is_music_video_mode
        else (
            "MODE POLICY (non-music_video):\n"
            "- contentType defines interpretation policy (story/ad/etc); refs remain cast/world anchors.\n"
        )
    )
    source_hierarchy_policy = (
        (
            "SOURCE HIERARCHY (music_video, DIRECTOR-NOTE STORY CORE):\n"
            "1) STORY_FRAME_TRUTH: director note defines concept/setting/scenario bias and narrative frame.\n"
            "2) AUDIO_TIMELINE_TRUTH: audio defines rhythm, emotion, scene timing, energy progression, and transition timing.\n"
            "3) CHARACTER_REFS: character refs are cast anchors; location/style/props refs are world anchors.\n"
            "4) STYLE_TREATMENT: visual treatment enriches, but does not replace explicit refs.\n"
            "CONFLICT POLICY (music_video, DIRECTOR-NOTE STORY CORE):\n"
            "- Do NOT force audio semantics to replace director note story topic when storyCoreSource=director_note.\n"
            "- Audio MUST still drive pacing, rhythmic accents, emotional contour, and cut timing.\n"
            "- Director note must NOT replace explicit refs (cast/world anchors).\n"
            "- Never use audio as mood-only; use audio timing and emotional dynamics in every scene.\n"
            "AUDIO + STORY COUPLING RULE:\n"
            "- Keep narrative frame from director note while proving concrete audio usage via boundaries and timing evidence.\n"
            "- audio timing signals define boundaries; audio dynamics define progression.\n"
            "FORBIDDEN:\n"
            "- Replacing explicit refs with text-invented cast/world.\n"
            "- Ignoring audio timing or transition cues.\n"
            "- Treating director note as permission to detach scenes from the track rhythm.\n"
        )
        if is_music_video_mode and story_core_source == "director_note"
        else (
            "SOURCE HIERARCHY (music_video, AUDIO STORY CORE):\n"
            "1) AUDIO_CONTENT_TRUTH: source/audio semantics define story subject, world facts, implied events/context.\n"
            "2) AUDIO_TIMELINE_TRUTH: audio defines timing anchors (phrases, pauses, energy transitions, sections).\n"
            "3) CHARACTER_REFS: character refs are cast anchors; location/style/props refs are world anchors.\n"
            "4) DIRECTOR_NOTE_INTERPRETATION: enriches framing/tone but must not replace explicit refs.\n"
            "CONFLICT POLICY (music_video, AUDIO STORY CORE):\n"
            "- If audio semantics and director note conflict, keep narrative core from source/audio semantics.\n"
            "- Director note can tune cinematic treatment and emotional layer inside that source-defined frame.\n"
            "- Never replace a clear audio/source topic with unrelated narrative.\n"
            "- If preferAudioOverText=true and audio/text conflict, audio MUST dominate.\n"
            "AUDIO CONTENT TRUTH RULE:\n"
            "- If audioSemantics.ok=true and audioSemantics.semanticSummary/worldContext/narrativeCore are present, they define story subject, world facts, and implied events.\n"
            "- audio timing signals define boundaries; audio semantics define meaning.\n"
            "FORBIDDEN:\n"
            "- unrelated meet-cute/bar/date story when audio/source defines another world.\n"
            "- inventing unrelated world/location while clear audio/source world exists.\n"
            "- replacing explicit refs with text-invented cast/world.\n"
        )
        if is_music_video_mode
        else (
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
        )
    )
    planner_modes_policy = (
        (
            "PLANNER MODES:\n"
            "- full_audio_first: audio meaning understood + usable timeline signals.\n"
            "- partial_audio_first: audio meaning partial while timing remains primary.\n"
            "- text_fallback: only when audio truth is unavailable/unusable.\n"
            "- For music_video with storyCoreSource=director_note, director note sets story frame; audio remains mandatory for rhythm/emotion/pacing.\n"
        )
        if is_music_video_mode and story_core_source == "director_note"
        else (
            "PLANNER MODES:\n"
            "- full_audio_first: audio meaning understood + usable timeline signals.\n"
            "- partial_audio_first: audio meaning partial but still primary world/content anchor.\n"
            "- text_fallback: only when audio truth is unavailable/unusable.\n"
            "- For music_video with storyCoreSource=source_of_truth, audio semantics may define narrative core/topic/world.\n"
        )
        if is_music_video_mode
        else (
            "PLANNER MODES:\n"
            "- full_audio_first: audio meaning understood + usable timeline signals.\n"
            "- partial_audio_first: audio meaning partial but still primary world/content anchor.\n"
            "- text_fallback: only when audio truth is unavailable/unusable.\n"
            "- If audio world/topic is clear, director note must not capture story core even in partial mode.\n"
        )
    )
    request_text = (
        "You are Scenario Director for PhotoStudio COMFY.\n"
        "Gemini is the planning brain. Do not delegate planning to heuristics.\n"
        "Return a single JSON object only. No markdown, no commentary.\n"
        "The storyboard_out must be production-usable for downstream Storyboard execution.\n"
        f"{mode_source_policy}"
        f"{source_hierarchy_policy}"
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
        "- scenes[*].directorGenreIntent / directorGenreReason / directorToneBias\n"
        "- diagnostics.noTextFallbackMode / diagnostics.authorialInterpretationLevel / diagnostics.audioLiteralnessLevel\n"
        "GENRE INTENT RULE:\n"
        f"- inferred directorGenreIntent={global_genre_intent.get('directorGenreIntent')} (reason={global_genre_intent.get('directorGenreReason')}, toneBias={global_genre_intent.get('directorToneBias')}).\n"
        "- Distinguish horror_dread vs tragic_social_drama vs neutral_drama explicitly.\n"
        "- If director note contains horror/страшная/жуткая/ужасная/dread/terror markers, keep horror_dread intent; do not flatten to social drama.\n"
        "NO-TEXT FALLBACK POLICY:\n"
        f"- noTextFallbackMode={no_text_fallback_mode}; authorialInterpretationLevel={authorial_interpretation_level}; audioLiteralnessLevel={audio_literalness_level}.\n"
        "- With empty director note, stay neutral and audio-literal: prioritize observable action/emotion over philosophical reinterpretation.\n"
        f"{planner_modes_policy}"
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
        "- If castIdentityLocked=true in runtime payload, NEVER rewrite locked role presentation or pairing type.\n"
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
        "- For music_video / clip mode: keep phrase-first cadence dense; typical useful scene duration is about 2.0-5.5 seconds.\n"
        "- Do not merge adjacent lyrical phrases into one scene unless they are semantically identical and continuity demands a single beat.\n"
        "- If neighboring phrases express different meaning, keep separate scenes.\n"
        "- Let timing breathe and follow emotional rhythm.\n"
        "- For longer videos, vary rhythm like short / short / medium / short / medium / peak / final hold.\n"
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
        f'    "storyCoreSource": "{story_core_source}",\n'
        f'    "storyFrameSource": "{story_frame_source if is_music_video_mode else ""}",\n'
        f'    "rhythmSource": "{rhythm_source if is_music_video_mode else ""}",\n'
        f'    "storyFrameSourceReason": "{story_frame_source_reason if is_music_video_mode else ""}",\n'
        f'    "rhythmSourceReason": "{rhythm_source_reason if is_music_video_mode else ""}",\n'
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
        f"Runtime payload:\n{json.dumps({'source': source, 'context_refs': context_refs, 'director_controls': director_controls, 'connected_context_summary': connected_context_summary, 'metadata': metadata, 'audioDurationSec': audio_duration_sec if audio_duration_sec > 0 else None, 'audioDurationSource': audio_duration_source, 'sourceMode': source_mode, 'sourceOrigin': source_origin, 'audioConnected': audio_connected, 'preferAudioOverText': prefer_audio_over_text, 'contentType': content_type_policy.get('value'), 'storyCoreSource': story_core_source, 'storyCoreSourceReason': story_core_reason, 'storyFrameSource': story_frame_source if is_music_video_mode else None, 'storyFrameSourceReason': story_frame_source_reason if is_music_video_mode else None, 'rhythmSource': rhythm_source if is_music_video_mode else None, 'rhythmSourceReason': rhythm_source_reason if is_music_video_mode else None, 'castIdentityLocked': _coerce_bool(cast_identity_lock.get('enabled'), False) if is_music_video_mode else None, 'castIdentityLockReason': str(cast_identity_lock.get('lockReason') or '') if is_music_video_mode else None, 'lockedRolePresentationByRole': cast_identity_lock.get('lockedRolePresentationByRole') if is_music_video_mode else {}, 'roleTypeByRole': role_type_by_role, 'audioContext': normalized_audio, 'audioAnalysis': {'ok': runtime_analysis.get('ok'), 'audioDurationSec': runtime_analysis.get('audioDurationSec'), 'phraseCount': len(runtime_analysis.get('phrases') or []), 'pauseCount': len(runtime_analysis.get('pauseWindows') or []), 'energyTransitionCount': len(runtime_analysis.get('energyTransitions') or []), 'sectionCount': len(runtime_analysis.get('sections') or [])}, 'audioSemantics': {'ok': runtime_semantics.get('ok'), 'transcript': str(runtime_semantics.get('transcript') or '')[:2000], 'semanticSummary': str(runtime_semantics.get('semanticSummary') or '')[:1200], 'narrativeCore': str(runtime_semantics.get('narrativeCore') or '')[:600], 'worldContext': str(runtime_semantics.get('worldContext') or '')[:600], 'entities': [str(item).strip() for item in (runtime_semantics.get('entities') or []) if str(item).strip()][:20], 'impliedEvents': [str(item).strip() for item in (runtime_semantics.get('impliedEvents') or []) if str(item).strip()][:20], 'tone': str(runtime_semantics.get('tone') or '')[:200], 'confidence': runtime_semantics.get('confidence'), 'hint': str(runtime_semantics.get('hint') or '')[:120]}, 'segmentationGuidance': runtime_guidance}, ensure_ascii=False, indent=2)}"
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
    def _is_temp_unavailable(response_payload: dict[str, Any] | None) -> bool:
        if not isinstance(response_payload, dict) or not response_payload.get("__http_error__"):
            return False
        status = int(response_payload.get("status") or 0)
        text_l = str(response_payload.get("text") or "").lower()
        return status == 503 or "unavailable" in text_l or "high demand" in text_l

    attempted_models: list[str] = []
    response: dict[str, Any] | None = None
    model_used = DEFAULT_TEXT_MODEL
    for candidate_model in [DEFAULT_TEXT_MODEL, FALLBACK_TEXT_MODEL]:
        if candidate_model in attempted_models:
            continue
        attempted_models.append(candidate_model)
        for retry_idx in range(0, len(GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC) + 1):
            response = post_generate_content(api_key, candidate_model, body, timeout=120)
            if not _is_temp_unavailable(response):
                break
            if retry_idx >= len(GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC):
                break
            backoff_sec = GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC[retry_idx]
            logger.warning(
                "[SCENARIO DIRECTOR] Gemini temporary unavailable model=%s retry=%s backoff=%.1fs",
                candidate_model,
                retry_idx + 1,
                backoff_sec,
            )
            time.sleep(backoff_sec)
        model_used = candidate_model
        if not isinstance(response, dict) or not response.get("__http_error__"):
            break
    return response, model_used, attempted_models


def _build_scenario_director_http_error(response: dict[str, Any], *, fallback_code: str, fallback_message: str) -> ScenarioDirectorError:
    status_code = int(response.get("status") or 502)
    error_text = str(response.get("text") or "")
    error_text_l = error_text.lower()
    is_temp_unavailable = status_code == 503 or "unavailable" in error_text_l or "high demand" in error_text_l
    if is_temp_unavailable:
        return ScenarioDirectorError(
            "gemini_temporarily_unavailable",
            "Модель Gemini сейчас перегружена, попробуйте ещё раз через несколько секунд.",
            status_code=503,
            details={
                "httpStatus": status_code,
                "retryable": True,
                "retryAfterSec": GEMINI_TEMP_UNAVAILABLE_RETRY_BACKOFFS_SEC[-1],
            },
        )
    return ScenarioDirectorError(
        fallback_code,
        f"{fallback_message} with HTTP {status_code}: {error_text[:400]}",
        status_code=502,
        details={"httpStatus": status_code},
    )


def _parse_storyboard_payload(raw_text: str, *, parse_stage: str = "initial", finish_reason: str = "") -> dict[str, Any]:
    logger.debug(
        "[SCENARIO_DIRECTOR] raw response received chars=%s parse_stage=%s finish_reason=%s",
        len(str(raw_text or "")),
        parse_stage,
        finish_reason or "unknown",
    )
    extracted = _extract_json_object(raw_text)
    if extracted is None:
        raise ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for Scenario Director: could not extract JSON object.",
            status_code=502,
            details={
                "rawPreview": str(raw_text or "")[:4000],
                "rawLength": len(str(raw_text or "")),
                "finishReason": finish_reason or "",
                "parseStage": parse_stage,
            },
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
    available_refs = ", ".join(
        f"{role} ({label})" if label and label != role else role
        for role, label in sorted(role_labels.items())
    )
    references_block = (
        f"Available character references: {available_refs}\n" if available_refs else "Available character references: none\n"
    )
    return (
        "You are Scenario Director. AUDIO is the primary source of truth.\n"
        "Do not invent story that contradicts spoken audio.\n"
        "Scene timing must follow speech phrases, pauses, and energy shifts.\n"
        "Every scene must be grounded in spoken content.\n"
        "Character references are identity anchors.\n"
        "IMPORTANT: use ONLY canonical role ids in planning fields (character_1, character_2, character_3, animal, group, location, style, props).\n"
        "Never put filenames or display labels into actors/participants/roles.\n"
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
        "- Do not merge two adjacent lyrical phrases into one scene unless they express the same semantic beat.\n"
        "- Prefer short clip-friendly scene durations (about 2-5.5 sec) when phrase timing allows.\n"
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


def _parse_audio_first_single_call_payload(raw_text: str, *, parse_stage: str = "initial", finish_reason: str = "") -> dict[str, Any]:
    extracted = _extract_json_object(raw_text)
    if extracted is None:
        raise ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for audio-first single-call mode.",
            status_code=502,
            details={
                "rawPreview": str(raw_text or "")[:4000],
                "rawLength": len(str(raw_text or "")),
                "finishReason": finish_reason or "",
                "parseStage": parse_stage,
            },
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
                "video_prompt": str(scene.get("motion") or "").strip() or "Beat-synced camera and subject motion evolving through the scene.",
                "ltx_mode": "i2v",
                "ltx_reason": "Audio-first single-call mapped to base clip image-video mode.",
                "render_mode": "image_video",
                "resolved_workflow_key": "image-video",
                "start_frame_source": "new",
                "needs_two_frames": False,
                "continuation_from_previous": idx > 1,
                "narration_mode": "full",
                "local_phrase": str(timeline_hit.get("text") or "").strip() or None,
                "sfx": "",
                "music_mix_hint": "off",
                "scene_purpose": "hook" if idx == 1 else "build",
                "viewer_hook": "Immediate rhythmic visual anchor." if idx == 1 else "Beat-matched progression.",
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
            "story_frame_source": "source_of_truth",
            "rhythm_source": "audio",
            "story_frame_source_reason": "single_call_audio_first_no_director_note",
            "rhythm_source_reason": "single_call_audio_timeline_drives_pacing",
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
        raise _build_scenario_director_http_error(
            response,
            fallback_code="gemini_request_failed",
            fallback_message="Gemini request failed",
        )
    raw_text = _extract_gemini_text(response)
    finish_reason = _extract_gemini_finish_reason(response)
    logger.info(
        "[SCENARIO DIRECTOR] audio-first raw response chars=%s finish_reason=%s",
        len(str(raw_text or "")),
        finish_reason or "unknown",
    )
    parsed_single = _parse_audio_first_single_call_payload(raw_text, parse_stage="audio_first_initial", finish_reason=finish_reason)
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
    content_type_policy = _get_content_type_policy(payload)
    effective_global_music_prompt = _resolve_effective_global_music_prompt(payload, storyboard_out.music_prompt)
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
        "bgMusicPrompt": effective_global_music_prompt,
        "brainPackage": brain_package,
        "meta": {
            "plannerSource": "gemini",
            "modelUsed": model_used,
            "attemptedModels": attempted_models,
            "audioFirstSingleCall": True,
            "rawGeminiTextPreview": raw_text[:2000],
            "requestedContentType": content_type_policy.get("requestedValue"),
            "effectiveContentType": content_type_policy.get("value"),
            "requestedContentTypeEnabled": content_type_policy.get("requestedEnabled"),
            "contentTypeFallbackApplied": content_type_policy.get("fallbackApplied"),
            "contentTypePolicy": content_type_policy,
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
        raise _build_scenario_director_http_error(
            response,
            fallback_code="gemini_request_failed",
            fallback_message="Gemini request failed",
        )

    raw_text = _extract_gemini_text(response)
    finish_reason = _extract_gemini_finish_reason(response)
    logger.info(
        "[SCENARIO DIRECTOR] raw response chars=%s finish_reason=%s model=%s",
        len(str(raw_text or "")),
        finish_reason or "unknown",
        model_used,
    )
    retried_for_json = False
    try:
        parsed_payload = _parse_storyboard_payload(raw_text, parse_stage="initial", finish_reason=finish_reason)
    except ScenarioDirectorError as first_exc:
        retried_for_json = True
        retry_body = {
            **body,
            "contents": [{"role": "user", "parts": [{"text": _build_request_text(payload, audio_context=audio_context, audio_analysis=audio_analysis, audio_guidance=audio_guidance, audio_semantics=audio_semantics, strict_json_retry=True)}]}],
        }
        retry_response, retry_model_used, retry_attempts = _send_director_request(api_key, retry_body)
        attempted_models.extend(model for model in retry_attempts if model not in attempted_models)
        if not isinstance(retry_response, dict):
            raise first_exc
        if retry_response.get("__http_error__"):
            raise _build_scenario_director_http_error(
                retry_response,
                fallback_code="gemini_request_failed",
                fallback_message="Gemini strict JSON retry failed",
            )
        raw_text = _extract_gemini_text(retry_response)
        retry_finish_reason = _extract_gemini_finish_reason(retry_response)
        logger.info(
            "[SCENARIO DIRECTOR] retry raw response chars=%s finish_reason=%s model=%s",
            len(str(raw_text or "")),
            retry_finish_reason or "unknown",
            retry_model_used,
        )
        model_used = retry_model_used
        parsed_payload = _parse_storyboard_payload(raw_text, parse_stage="strict_json_retry", finish_reason=retry_finish_reason)

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
    content_type_policy = _get_content_type_policy(payload)
    is_music_video_mode = str(content_type_policy.get("value") or "").strip().lower() == "music_video"
    story_core_source = "director_note" if is_music_video_mode and text_hint_present else "source_of_truth"
    story_frame_source = "director_note" if is_music_video_mode and text_hint_present else "source_of_truth"
    rhythm_source = "audio" if is_music_video_mode else ""
    story_frame_source_reason = "director_note_present" if is_music_video_mode and text_hint_present else "director_note_empty_use_source_truth"
    rhythm_source_reason = "audio_drives_pacing_and_transitions" if is_music_video_mode else ""
    story_core_source_reason = (
        "music_video_director_note_sets_story_frame_audio_drives_rhythm_emotion_timing"
        if story_core_source == "director_note"
        else "story_frame_from_source_truth_audio_semantics_refs_anchor_cast_and_world"
    )
    cast_identity_lock_info = {"enabled": False, "lockReason": "not_music_video", "textRewritesApplied": 0}
    if is_music_video_mode:
        storyboard_out, cast_identity_lock_info = _enforce_music_video_cast_identity_lock(storyboard_out, payload)
        storyboard_out.narrative_strategy.story_core_source = story_core_source
        storyboard_out.narrative_strategy.story_frame_source = story_frame_source
        storyboard_out.narrative_strategy.rhythm_source = rhythm_source
        storyboard_out.narrative_strategy.story_frame_source_reason = story_frame_source_reason
        storyboard_out.narrative_strategy.rhythm_source_reason = rhythm_source_reason
        if not str(storyboard_out.narrative_strategy.why or "").strip():
            storyboard_out.narrative_strategy.why = story_core_source_reason
        planner_strategy = structured_planner_diagnostics.get("narrativeStrategy") if isinstance(structured_planner_diagnostics.get("narrativeStrategy"), dict) else {}
        planner_strategy["storyCoreSource"] = story_core_source
        planner_strategy["storyFrameSource"] = story_frame_source
        planner_strategy["rhythmSource"] = rhythm_source
        planner_strategy["storyFrameSourceReason"] = story_frame_source_reason
        planner_strategy["rhythmSourceReason"] = rhythm_source_reason
        planner_strategy["story_core_source"] = story_core_source
        planner_strategy["story_frame_source"] = story_frame_source
        planner_strategy["rhythm_source"] = rhythm_source
        planner_strategy["story_frame_source_reason"] = story_frame_source_reason
        planner_strategy["rhythm_source_reason"] = rhythm_source_reason
        structured_planner_diagnostics["narrativeStrategy"] = planner_strategy
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
                refinement_parsed = _parse_storyboard_payload(
                    refinement_raw_text,
                    parse_stage="timeline_refinement",
                    finish_reason=_extract_gemini_finish_reason(refinement_response),
                )
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
    controls = payload.get("director_controls") if isinstance(payload.get("director_controls"), dict) else {}
    director_note_text = str(controls.get("directorNote") or controls.get("director_note") or "").strip()
    no_text_fallback_mode = "neutral_audio_literal" if not director_note_text else "off"
    authorial_interpretation_level = "low" if no_text_fallback_mode == "neutral_audio_literal" else "medium"
    audio_literalness_level = "high" if no_text_fallback_mode == "neutral_audio_literal" else "balanced"
    storyboard_out.diagnostics.no_text_fallback_mode = no_text_fallback_mode
    storyboard_out.diagnostics.authorial_interpretation_level = authorial_interpretation_level
    storyboard_out.diagnostics.audio_literalness_level = audio_literalness_level
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
    effective_global_music_prompt = _resolve_effective_global_music_prompt(payload, storyboard_out.music_prompt)
    role_influence_applied_scenes = 0
    identity_lock_applied_scenes = 0
    for scene in (storyboard_out.scenes or []):
        reason = str(scene.clip_decision_reason or "")
        if scene.role_influence_applied or "roleInfluenceApplied=true" in reason:
            role_influence_applied_scenes += 1
        if scene.multi_character_identity_lock or "multiCharacterIdentityLock=true" in reason:
            identity_lock_applied_scenes += 1
    return {
        "ok": True,
        "storyboardOut": storyboard_out.model_dump(mode="json"),
        "directorOutput": director_output,
        "scenario": storyboard_out.full_scenario,
        "voiceScript": storyboard_out.voice_script,
        "bgMusicPrompt": effective_global_music_prompt,
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
            "noTextFallbackMode": no_text_fallback_mode,
            "authorialInterpretationLevel": authorial_interpretation_level,
            "audioLiteralnessLevel": audio_literalness_level,
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
            "requestedContentType": content_type_policy.get("requestedValue"),
            "effectiveContentType": content_type_policy.get("value"),
            "requestedContentTypeEnabled": content_type_policy.get("requestedEnabled"),
            "contentTypeFallbackApplied": content_type_policy.get("fallbackApplied"),
            "contentTypePolicy": content_type_policy,
            "textHintPresent": text_hint_present,
            "storyCoreSource": story_core_source,
            "storyCoreSourceReason": story_core_source_reason,
            "storyFrameSource": story_frame_source if is_music_video_mode else None,
            "rhythmSource": rhythm_source if is_music_video_mode else None,
            "storyFrameSourceReason": story_frame_source_reason if is_music_video_mode else None,
            "rhythmSourceReason": rhythm_source_reason if is_music_video_mode else None,
            "story_core_source": story_core_source,
            "story_core_source_reason": story_core_source_reason,
            "story_frame_source": story_frame_source if is_music_video_mode else "",
            "rhythm_source": rhythm_source if is_music_video_mode else "",
            "story_frame_source_reason": story_frame_source_reason if is_music_video_mode else "",
            "rhythm_source_reason": rhythm_source_reason if is_music_video_mode else "",
            "castIdentityLocked": _coerce_bool(cast_identity_lock_info.get("enabled"), False) if is_music_video_mode else False,
            "castIdentityLockReason": cast_identity_lock_info.get("lockReason") if is_music_video_mode else "not_music_video",
            "castIdentityLockRewritesApplied": cast_identity_lock_info.get("textRewritesApplied") if is_music_video_mode else 0,
            "lockedRolePresentationByRole": cast_identity_lock_info.get("lockedRolePresentationByRole") if is_music_video_mode else {},
            "textHintInfluence": text_hint_influence,
            "audioInfluence": audio_influence,
            "narrativeBiasEstimate": narrative_bias_estimate,
            "effectiveRoleTypeByRole": effective_role_type_by_role,
            "roleAssignmentSource": role_assignment_source,
            "roleOverrideApplied": role_override_applied,
            "roleInfluenceAppliedScenes": role_influence_applied_scenes if is_music_video_mode else 0,
            "multiCharacterIdentityLockScenes": identity_lock_applied_scenes if is_music_video_mode else 0,
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
            if isinstance(response, dict) and response.get("__http_error__"):
                last_error = _build_scenario_director_http_error(
                    response,
                    fallback_code="gemini_request_failed",
                    fallback_message="Gemini request failed in Scenario Director master mode",
                )
                if last_error.code == "gemini_temporarily_unavailable":
                    break
            else:
                last_error = ScenarioDirectorError("gemini_request_failed", "Gemini request failed in Scenario Director master mode.", status_code=502)
            continue
        raw_text = _extract_gemini_text(response)
        finish_reason = _extract_gemini_finish_reason(response)
        logger.info(
            "[SCENARIO DIRECTOR][master] raw response chars=%s finish_reason=%s retry=%s",
            len(str(raw_text or "")),
            finish_reason or "unknown",
            retry_level,
        )
        parsed_payload = _extract_json_object(raw_text)
        if isinstance(parsed_payload, dict):
            sanitized = _sanitize_master_output(parsed_payload)
            return {
                "ok": True,
                "mode": "master",
                "masterOutput": sanitized,
                "meta": {"plannerSource": "gemini", "modelUsed": model_used, "attemptedModels": attempted_models, "retryCount": retry_level},
            }
        last_error = ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for Scenario Director master mode.",
            status_code=502,
            details={"rawPreview": str(raw_text or "")[:4000], "rawLength": len(str(raw_text or "")), "finishReason": finish_reason or "", "parseStage": f"master_retry_{retry_level}"},
        )

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
        retry_hint += "\nRETRY 2 OVERRIDE: Keep compact fields, but preserve phrase boundaries; do not collapse adjacent distinct phrases."
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
        "- Keep scene count <= expectedScenes and <= 12.\n"
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
    expected_scenes = int(_safe_float(payload.get("expectedScenes"), max(1, round((end_sec - start_sec) / 4.0))))
    expected_scenes = max(1, min(expected_scenes, 12))

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
            if isinstance(response, dict) and response.get("__http_error__"):
                last_error = _build_scenario_director_http_error(
                    response,
                    fallback_code="gemini_request_failed",
                    fallback_message="Gemini request failed in Scenario Director scenes mode",
                )
                if last_error.code == "gemini_temporarily_unavailable":
                    break
            else:
                last_error = ScenarioDirectorError("gemini_request_failed", "Gemini request failed in Scenario Director scenes mode.", status_code=502)
            continue
        raw_text = _extract_gemini_text(response)
        finish_reason = _extract_gemini_finish_reason(response)
        logger.info(
            "[SCENARIO DIRECTOR][scenes] raw response chars=%s finish_reason=%s retry=%s",
            len(str(raw_text or "")),
            finish_reason or "unknown",
            retry_level,
        )
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
        last_error = ScenarioDirectorError(
            "gemini_invalid_json",
            "Gemini returned invalid JSON for Scenario Director scenes mode.",
            status_code=502,
            details={"rawPreview": str(raw_text or "")[:4000], "rawLength": len(str(raw_text or "")), "finishReason": finish_reason or "", "parseStage": f"scenes_retry_{retry_level}"},
        )

    raise last_error or ScenarioDirectorError("gemini_invalid_json", "Gemini returned invalid JSON for Scenario Director scenes mode.", status_code=502)
