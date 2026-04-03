from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ProjectMode(str, Enum):
    narration_first = "narration_first"
    music_first = "music_first"
    hybrid = "hybrid"


class InputMode(str, Enum):
    audio_first = "audio_first"
    text_to_audio_first = "text_to_audio_first"


class AudioSegmentType(str, Enum):
    narration = "narration"
    music = "music"
    music_vocal = "music_vocal"
    local_phrase = "local_phrase"
    sfx_pulse = "sfx_pulse"
    unknown = "unknown"


class NarrationMode(str, Enum):
    full = "full"
    duck = "duck"
    pause = "pause"


class RenderMode(str, Enum):
    i2v = "i2v"
    i2v_as = "i2v_as"
    f_l = "f_l"
    f_l_as = "f_l_as"
    continuation = "continuation"
    lip_sync = "lip_sync"


SCENE_INTENTS = (
    "setup",
    "pursuit",
    "confrontation",
    "threat",
    "escape",
    "support",
    "dialogue",
    "reveal",
    "observation",
    "transition",
)

INTENT_RENDER_PREFERENCES: dict[str, tuple[RenderMode, ...]] = {
    "setup": (RenderMode.i2v,),
    "observation": (RenderMode.i2v_as,),
    "pursuit": (RenderMode.i2v_as,),
    "confrontation": (RenderMode.f_l, RenderMode.i2v_as),
    "threat": (RenderMode.i2v_as,),
    "escape": (RenderMode.i2v_as,),
    "support": (RenderMode.i2v,),
    "dialogue": (RenderMode.lip_sync, RenderMode.i2v),
    "reveal": (RenderMode.f_l_as, RenderMode.i2v_as),
    "transition": (RenderMode.continuation, RenderMode.i2v),
}

INTENT_PHRASE_PATTERNS: list[tuple[str, tuple[str, ...], float]] = [
    ("escape", ("trying to escape", "tries to escape", "attempts to escape", "breaks free"), 0.87),
    ("pursuit", ("moves toward", "move toward", "closing distance", "closes in", "gives chase"), 0.86),
    ("confrontation", ("stands against", "stand against", "faces off", "faces ", "direct standoff"), 0.85),
    ("observation", ("observing silently", "watching silently", "keeps watching", "from a distance"), 0.84),
]

INTENT_KEYWORD_MAP: list[tuple[str, tuple[str, ...]]] = [
    ("pursuit", ("chase", "run", "follow", "track", "hunt")),
    ("confrontation", ("fight", "argue", "attack", "clash", "challenge")),
    ("threat", ("danger", "fear", "control", "menace", "intimidate", "threat")),
    ("escape", ("escape", "flee", "evade", "break out")),
    ("support", ("help", "comfort", "assist", "protect")),
    ("dialogue", ("talk", "say", "explain", "discuss", "converse")),
    ("reveal", ("discover", "realize", "uncover", "reveal")),
    ("observation", ("watch", "observe", "notice", "scan")),
]


class AudioLayerRef(BaseModel):
    kind: str
    url: str | None = None
    present: bool = False
    source: str | None = None
    label: str | None = None


class LipSyncPolicy(BaseModel):
    allowed: bool = False
    reason: str = "lip_sync_not_evaluated"
    requires_music_track: bool = True
    requires_vocal: bool = True
    requires_close_framing: bool = True
    requires_vocal_present: bool = True
    requires_musical_rhythm: bool = True
    requires_framing_support: bool = True


class PlannerValidation(BaseModel):
    valid: bool = True
    blocked: bool = False
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AudioPlanningContext(BaseModel):
    master_audio_url: str | None = None
    global_music_track_url: str | None = None
    text_source_present: bool = False
    master_narration_present: bool = False
    global_music_track_present: bool = False
    local_scene_audio_allowed: bool = True
    analysis_source_of_truth: str = "gemini"
    auxiliary_audio_analysis: str | None = "debug_only"
    lip_sync_policy: LipSyncPolicy = Field(default_factory=LipSyncPolicy)
    planning_blocked_reason: str | None = None


class ProjectPlanningInput(BaseModel):
    input_mode: InputMode = InputMode.audio_first
    project_mode: ProjectMode = ProjectMode.narration_first
    story_text: str | None = None
    master_audio_url: str | None = None
    global_music_track_url: str | None = None
    refs: dict[str, Any] = Field(default_factory=dict)
    world: dict[str, Any] = Field(default_factory=dict)
    style: dict[str, Any] = Field(default_factory=dict)
    props: dict[str, Any] = Field(default_factory=dict)
    planner_rules: dict[str, Any] = Field(default_factory=dict)
    planner_overrides: dict[str, Any] = Field(default_factory=dict)
    explicit_input_mode: bool = False


class LtxRenderTask(BaseModel):
    shot_id: str
    production_mode: RenderMode
    model: str = "ltx"
    use_audio_as_driver: bool = False
    audio_source_ref: str | None = None
    start_frame_source: str | None = None
    end_frame_source: str | None = None
    motion_bucket: int | None = None
    motion_interpretation: str | None = None
    constraints: dict[str, Any] = Field(default_factory=dict)
    debug: dict[str, Any] = Field(default_factory=dict)


class PlannedShot(BaseModel):
    shot_id: str
    scene_id: str
    start_sec: float
    end_sec: float
    duration_sec: float
    shot_type: str | None = None
    framing: str | None = None
    render_mode: RenderMode
    render_reason: str
    audio_segment_type: AudioSegmentType
    has_vocal_rhythm: bool = False
    motion_interpretation: str
    motion_profile: dict[str, str] | None = None
    project_mode: ProjectMode
    audio_driver: str | None = None
    lipsync_policy: LipSyncPolicy
    start_frame_source: str | None = None
    parent_shot_id: str | None = None
    needs_two_frames: bool = False
    narration_mode: NarrationMode = NarrationMode.full
    validation_errors: list[str] = Field(default_factory=list)
    validation_warnings: list[str] = Field(default_factory=list)
    render_task: LtxRenderTask | None = None


class PlannedScene(BaseModel):
    scene_id: str
    scene_mode: str
    start_sec: float
    end_sec: float
    duration_sec: float
    summary: str
    intent: str | None = None
    narration_mode: NarrationMode = NarrationMode.full
    audio_segment_type: AudioSegmentType = AudioSegmentType.unknown
    continuation_from_prev: bool = False
    shots: list[PlannedShot] = Field(default_factory=list)


class AudioFirstPlannerOutput(BaseModel):
    input_mode: InputMode
    project_mode: ProjectMode
    planning_context: AudioPlanningContext
    validation: PlannerValidation
    scenes: list[PlannedScene] = Field(default_factory=list)
    render_tasks: list[LtxRenderTask] = Field(default_factory=list)
    debug: dict[str, Any] = Field(default_factory=dict)


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        num = float(value)
    except Exception:
        return default
    if num != num or num in (float("inf"), float("-inf")):
        return default
    return float(num)


def _normalize_input_mode(value: Any, *, has_audio: bool, has_text: bool) -> tuple[InputMode, bool]:
    raw = _clean_str(value).lower()
    if raw == InputMode.text_to_audio_first.value:
        return InputMode.text_to_audio_first, True
    if raw == InputMode.audio_first.value:
        return InputMode.audio_first, True
    if has_audio:
        return InputMode.audio_first, False
    if has_text:
        return InputMode.text_to_audio_first, False
    return InputMode.audio_first, False


def _normalize_project_mode(value: Any) -> ProjectMode:
    raw = _clean_str(value).lower()
    for mode in ProjectMode:
        if raw == mode.value:
            return mode
    return ProjectMode.narration_first


def _estimate_scene_intent(scene: dict[str, Any], scene_index: int) -> tuple[str, float]:
    scene_blob = " ".join(
        [
            _clean_str(scene.get("intent")),
            _clean_str(scene.get("sceneText")),
            _clean_str(scene.get("visualDescription")),
            _clean_str(scene.get("sceneMeaning")),
            _clean_str(scene.get("summary")),
            _clean_str(scene.get("title")),
            _clean_str(scene.get("sceneNarrativeStep")),
            _clean_str(scene.get("sceneGoal")),
        ]
    ).lower()
    for intent, phrases, confidence in INTENT_PHRASE_PATTERNS:
        if any(phrase in scene_blob for phrase in phrases):
            return intent, confidence
    for intent, keywords in INTENT_KEYWORD_MAP:
        if any(keyword in scene_blob for keyword in keywords):
            return intent, 0.82
    if scene_index == 0:
        return "setup", 0.58
    return "transition", 0.5


def _intent_phrase_and_keyword_matches(scene: dict[str, Any], intent: str) -> tuple[list[str], list[str]]:
    scene_blob = " ".join(
        [
            _clean_str(scene.get("intent")),
            _clean_str(scene.get("sceneText")),
            _clean_str(scene.get("visualDescription")),
            _clean_str(scene.get("sceneMeaning")),
            _clean_str(scene.get("summary")),
            _clean_str(scene.get("title")),
            _clean_str(scene.get("sceneNarrativeStep")),
            _clean_str(scene.get("sceneGoal")),
        ]
    ).lower()
    phrase_matches: list[str] = []
    keyword_matches: list[str] = []
    for mapped_intent, phrases, _ in INTENT_PHRASE_PATTERNS:
        if mapped_intent == intent:
            phrase_matches.extend([phrase for phrase in phrases if phrase in scene_blob])
    for mapped_intent, keywords in INTENT_KEYWORD_MAP:
        if mapped_intent == intent:
            keyword_matches.extend([keyword for keyword in keywords if keyword in scene_blob])
    return list(dict.fromkeys(phrase_matches)), list(dict.fromkeys(keyword_matches))


def _extract_intent_matches(scene: dict[str, Any], intent: str) -> list[str]:
    phrases, keywords = _intent_phrase_and_keyword_matches(scene, intent)
    return list(dict.fromkeys(phrases + keywords))


def _compute_intent_confidence(scene: dict[str, Any], intent: str, base_confidence: float) -> float:
    phrase_matches, keyword_matches = _intent_phrase_and_keyword_matches(scene, intent)
    confidence = max(0.0, base_confidence)
    if phrase_matches:
        confidence += 0.05
    if len(keyword_matches) >= 2:
        confidence += 0.05
    return round(min(0.95, confidence), 2)


def _map_intent_to_render_mode(
    intent: str,
    audio_segment_type: AudioSegmentType,
    has_vocal_rhythm: bool,
) -> tuple[list[RenderMode], float]:
    normalized_intent = _clean_str(intent).lower()
    preferred_modes = list(INTENT_RENDER_PREFERENCES.get(normalized_intent, (RenderMode.i2v,)))
    if normalized_intent == "dialogue":
        preferred_modes = [RenderMode.lip_sync, RenderMode.i2v] if has_vocal_rhythm else [RenderMode.i2v]
    if audio_segment_type == AudioSegmentType.narration and RenderMode.lip_sync in preferred_modes:
        preferred_modes = [mode for mode in preferred_modes if mode != RenderMode.lip_sync] or [RenderMode.i2v]
    if audio_segment_type == AudioSegmentType.local_phrase and RenderMode.lip_sync in preferred_modes:
        preferred_modes = [mode for mode in preferred_modes if mode != RenderMode.lip_sync] or [RenderMode.i2v]
    priority = 0.64 if normalized_intent in INTENT_RENDER_PREFERENCES else 0.4
    if has_vocal_rhythm and normalized_intent == "dialogue":
        priority = max(priority, 0.88)
    if normalized_intent in {"confrontation", "reveal", "transition"}:
        priority = max(priority, 0.72)
    return preferred_modes, round(priority, 2)


def build_project_planning_input(payload: dict[str, Any]) -> ProjectPlanningInput:
    has_audio = bool(_clean_str(payload.get("masterAudioUrl") or payload.get("audioUrl")))
    has_text = bool(_clean_str(payload.get("storyText") or payload.get("text") or payload.get("lyricsText") or payload.get("transcriptText")))
    input_mode, explicit_input_mode = _normalize_input_mode(payload.get("inputMode"), has_audio=has_audio, has_text=has_text)
    project_mode = _normalize_project_mode(payload.get("projectMode"))
    story_text = _clean_str(payload.get("storyText") or payload.get("text") or payload.get("transcriptText") or payload.get("lyricsText")) or None
    master_audio_url = _clean_str(payload.get("masterAudioUrl") or payload.get("audioUrl")) or None
    global_music_track_url = _clean_str(
        payload.get("globalMusicTrackUrl")
        or payload.get("musicTrackUrl")
        or payload.get("userMusicTrackUrl")
        or payload.get("globalUserAudioTrackUrl")
        or payload.get("sunoUrl")
    ) or None
    refs = payload.get("refsByRole") if isinstance(payload.get("refsByRole"), dict) else {}
    return ProjectPlanningInput(
        input_mode=input_mode,
        project_mode=project_mode,
        story_text=story_text,
        master_audio_url=master_audio_url,
        global_music_track_url=global_music_track_url,
        refs=refs,
        world=payload.get("world") if isinstance(payload.get("world"), dict) else {},
        style=payload.get("style") if isinstance(payload.get("style"), dict) else {},
        props=payload.get("props") if isinstance(payload.get("props"), dict) else {},
        planner_rules=payload.get("plannerRules") if isinstance(payload.get("plannerRules"), dict) else {},
        planner_overrides=payload.get("plannerOverrides") if isinstance(payload.get("plannerOverrides"), dict) else {},
        explicit_input_mode=explicit_input_mode,
    )


def build_audio_planning_context(project_input: ProjectPlanningInput) -> AudioPlanningContext:
    blocked_reason = None
    if project_input.input_mode == InputMode.text_to_audio_first and not project_input.master_audio_url:
        blocked_reason = "planning_blocked_until_master_audio_exists"
    return AudioPlanningContext(
        master_audio_url=project_input.master_audio_url,
        global_music_track_url=project_input.global_music_track_url,
        text_source_present=bool(project_input.story_text),
        master_narration_present=bool(project_input.master_audio_url),
        global_music_track_present=bool(project_input.global_music_track_url),
        local_scene_audio_allowed=True,
        analysis_source_of_truth="gemini",
        auxiliary_audio_analysis="debug_fallback_only",
        lip_sync_policy=LipSyncPolicy(
            allowed=False,
            reason="lip_sync_allowed_only_for_music_vocal_rhythm_with_supported_framing",
            requires_music_track=True,
            requires_vocal=True,
            requires_close_framing=True,
        ),
        planning_blocked_reason=blocked_reason,
    )


def validate_project_input(project_input: ProjectPlanningInput, context: AudioPlanningContext) -> PlannerValidation:
    errors: list[str] = []
    warnings: list[str] = []
    blocked = False
    if project_input.input_mode == InputMode.text_to_audio_first and not project_input.master_audio_url:
        blocked = True
        errors.append("master_audio_required_for_text_to_audio_first")
        warnings.append("fake_timing_from_raw_text_disabled")
    if project_input.global_music_track_url and "suno" in project_input.global_music_track_url.lower():
        warnings.append("legacy_suno_url_mapped_to_global_music_track")
    return PlannerValidation(valid=len(errors) == 0, blocked=blocked, errors=errors, warnings=warnings)


def _infer_audio_segment_type(scene: dict[str, Any], project_input: ProjectPlanningInput, context: AudioPlanningContext) -> AudioSegmentType:
    if _clean_str(scene.get("localScenePhrase") or scene.get("localSceneAudio") or scene.get("localPhraseText")):
        return AudioSegmentType.local_phrase
    audio_story_mode = _clean_str(scene.get("audioStoryMode") or project_input.planner_rules.get("audioStoryMode") or "").lower()
    audio_type = _clean_str(scene.get("audioType") or scene.get("sceneType") or "").lower()
    has_vocals = bool(scene.get("hasVocals") is True or scene.get("isLipSync") is True or scene.get("lipSync") is True)
    if audio_story_mode == "speech_narrative":
        return AudioSegmentType.narration
    if audio_type in {"narration", "voiceover", "spoken"}:
        return AudioSegmentType.narration
    if audio_type in {"instrumental", "music_only", "bg", "music"}:
        return AudioSegmentType.music
    if audio_type in {"song", "song_with_vocals", "vocals", "music_vocal"}:
        return AudioSegmentType.music_vocal
    if has_vocals and context.global_music_track_present:
        return AudioSegmentType.music_vocal
    if context.global_music_track_present:
        return AudioSegmentType.music
    if project_input.project_mode == ProjectMode.narration_first and context.master_narration_present:
        return AudioSegmentType.narration
    return AudioSegmentType.unknown


def _infer_narration_mode(scene: dict[str, Any], audio_segment_type: AudioSegmentType) -> NarrationMode:
    raw = _clean_str(scene.get("narrationMode")).lower()
    if raw in {"full", "duck", "pause"}:
        return NarrationMode(raw)
    if audio_segment_type == AudioSegmentType.music:
        return NarrationMode.duck
    if audio_segment_type == AudioSegmentType.local_phrase:
        return NarrationMode.pause
    return NarrationMode.full


def _shot_supports_lipsync(scene: dict[str, Any]) -> bool:
    shot_type = _clean_str(scene.get("shotType")).lower()
    camera_type = _clean_str(scene.get("cameraType")).lower()
    framing = f"{shot_type} {camera_type}"
    return any(token in framing for token in ["close", "medium", "portrait", "performance", "mouth"])


def _has_audio_accent(scene: dict[str, Any], audio_segment_type: AudioSegmentType) -> bool:
    hint_blob = " ".join(
        [
            _clean_str(scene.get("timingReason")),
            _clean_str(scene.get("cameraMotion")),
            _clean_str(scene.get("motionPlan")),
            _clean_str(scene.get("transitionType")),
        ]
    ).lower()
    if audio_segment_type == AudioSegmentType.local_phrase:
        return True
    return any(token in hint_blob for token in ["peak", "accent", "impact", "pulse", "downbeat", "beat", "hit", "energy"])


def _needs_controlled_transition(scene: dict[str, Any]) -> bool:
    transition_type = _clean_str(scene.get("transitionType")).lower()
    return transition_type in {"enter_transition", "match_cut", "perspective_shift", "continuous", "controlled_transition", "f_l", "f_l_as"}


def _is_continuation(scene: dict[str, Any]) -> bool:
    transition_type = _clean_str(scene.get("transitionType")).lower()
    return transition_type in {"continuation", "enter_transition"}


def _compute_lipsync_policy(
    scene: dict[str, Any],
    context: AudioPlanningContext,
    audio_segment_type: AudioSegmentType,
) -> tuple[LipSyncPolicy, bool]:
    has_vocal_present = bool(scene.get("hasVocals") is True or scene.get("isLipSync") is True or scene.get("lipSync") is True)
    suitable_framing = _shot_supports_lipsync(scene)
    has_vocal_rhythm = audio_segment_type == AudioSegmentType.music_vocal and has_vocal_present and context.global_music_track_present
    base_kwargs = {
        "requires_music_track": True,
        "requires_vocal": True,
        "requires_close_framing": True,
        "requires_vocal_present": True,
        "requires_musical_rhythm": True,
        "requires_framing_support": True,
    }
    if audio_segment_type == AudioSegmentType.narration:
        return LipSyncPolicy(allowed=False, reason="narration_segments_must_not_use_lip_sync", **base_kwargs), has_vocal_rhythm
    if audio_segment_type == AudioSegmentType.local_phrase:
        return LipSyncPolicy(allowed=False, reason="local_scene_phrase_does_not_enable_lip_sync_automatically", **base_kwargs), has_vocal_rhythm
    if not has_vocal_present:
        return LipSyncPolicy(allowed=False, reason="vocal_presence_required_for_lip_sync", **base_kwargs), has_vocal_rhythm
    if not context.global_music_track_present:
        return LipSyncPolicy(allowed=False, reason="global_music_track_required_for_lip_sync", **base_kwargs), has_vocal_rhythm
    if audio_segment_type != AudioSegmentType.music_vocal:
        return LipSyncPolicy(allowed=False, reason="lip_sync_only_allowed_for_music_vocal_segments", **base_kwargs), has_vocal_rhythm
    if not suitable_framing:
        return LipSyncPolicy(allowed=False, reason="shot_framing_not_suitable_for_lip_sync", **base_kwargs), has_vocal_rhythm
    return LipSyncPolicy(allowed=True, reason="music_vocal_rhythm_and_supported_framing_confirmed", **base_kwargs), True


def _select_render_mode(
    scene: dict[str, Any],
    project_input: ProjectPlanningInput,
    audio_segment_type: AudioSegmentType,
    has_vocal_rhythm: bool,
    lipsync_policy: LipSyncPolicy,
) -> tuple[RenderMode, str, list[RenderMode], float, bool]:
    wants_lipsync = bool(scene.get("isLipSync") is True or scene.get("lipSync") is True)
    scene_intent_raw = _clean_str(scene.get("intent")).lower()
    estimated_intent, _ = _estimate_scene_intent(scene, -1)
    scene_intent = scene_intent_raw if scene_intent_raw in SCENE_INTENTS else estimated_intent
    intent_modes, intent_priority = _map_intent_to_render_mode(scene_intent, audio_segment_type, has_vocal_rhythm)
    selected_mode: RenderMode
    selected_reason: str
    if wants_lipsync and lipsync_policy.allowed and has_vocal_rhythm:
        selected_mode = RenderMode.lip_sync
        selected_reason = "music_vocal_scene_with_rhythm_and_supported_framing"
    elif _is_continuation(scene):
        selected_mode = RenderMode.continuation
        selected_reason = "continuation_of_previous_visual_flow"
    else:
        audio_accent = _has_audio_accent(scene, audio_segment_type)
        if _needs_controlled_transition(scene):
            if audio_accent:
                selected_mode = RenderMode.f_l_as
                selected_reason = "controlled_a_to_b_transition_aligned_to_audio_accent"
            else:
                selected_mode = RenderMode.f_l
                selected_reason = "controlled_a_to_b_transition_without_audio_accent"
        elif audio_accent:
            selected_mode = RenderMode.i2v_as
            selected_reason = "audio_sensitive_motion_without_speech_articulation"
        else:
            selected_mode = RenderMode.i2v
            selected_reason = "base_single_frame_animation_without_forced_articulation"
    override_applied = False
    if (
        intent_priority >= 0.8
        and selected_mode not in intent_modes
    ):
        for preferred_mode in intent_modes:
            if preferred_mode == RenderMode.lip_sync and (not lipsync_policy.allowed or not has_vocal_rhythm):
                continue
            selected_mode = preferred_mode
            override_applied = True
            break
    if selected_mode not in intent_modes:
        selected_reason = (
            f"{selected_reason}|intent_soft_mismatch:{scene_intent}"
            f"|preferred:{','.join(mode.value for mode in intent_modes)}"
        )
    else:
        selected_reason = f"{selected_reason}|intent_aligned:{scene_intent}"
    if override_applied:
        selected_reason = f"{selected_reason}|intent_override_applied"
    return selected_mode, selected_reason, intent_modes, intent_priority, override_applied


def _render_mode_to_workflow_family(render_mode: RenderMode) -> str:
    if render_mode == RenderMode.lip_sync:
        return "lip_sync_music"
    if render_mode in {RenderMode.f_l, RenderMode.f_l_as}:
        return "f_l"
    return "i2v"


def _map_intent_to_motion(intent: str) -> dict[str, str]:
    mapping: dict[str, dict[str, str]] = {
        "pursuit": {"motionStyle": "forward_movement_tracking", "cameraBehavior": "handheld_follow"},
        "confrontation": {"motionStyle": "locked_tension", "cameraBehavior": "slow_push_in"},
        "threat": {"motionStyle": "minimal_motion_high_tension", "cameraBehavior": "static_with_micro_shake"},
        "observation": {"motionStyle": "slow_drift", "cameraBehavior": "wide_static"},
        "escape": {"motionStyle": "fast_directional", "cameraBehavior": "shaky_tracking"},
    }
    return mapping.get(intent, {"motionStyle": "balanced_scene_motion", "cameraBehavior": "context_driven"})


def _validate_role_intent(scene: dict[str, Any], role: str, intent: str) -> tuple[str, list[str]]:
    clean_role = _clean_str(role).lower()
    clean_intent = _clean_str(intent).lower()
    if clean_role not in {"hero", "antagonist", "secondary"}:
        return "ok", []
    warnings: list[str] = []
    mismatch_rules = {
        ("hero", "threat"),
        ("antagonist", "support"),
    }
    suspicious_rules = {
        ("hero", "threat"),
        ("antagonist", "support"),
    }
    if (clean_role, clean_intent) in mismatch_rules:
        warnings.append("intent_role_mismatch")
    if (clean_role, clean_intent) in suspicious_rules:
        warnings.append("suspicious_role_intent_combination")
    return ("warn" if warnings else "ok"), warnings


def _motion_interpretation(render_mode: RenderMode, audio_segment_type: AudioSegmentType) -> str:
    if render_mode == RenderMode.lip_sync:
        return "music_vocal_performance_with_rhythm_locked_facial_delivery"
    if render_mode in {RenderMode.i2v_as, RenderMode.f_l_as}:
        return "audio_sensitive_motion_not_speech_articulation"
    if render_mode == RenderMode.continuation:
        return "last_frame_continuity_from_previous_shot"
    if render_mode in {RenderMode.f_l}:
        return "controlled_a_to_b_transition_motion"
    if audio_segment_type == AudioSegmentType.narration:
        return "reactive_body_language_without_mouth_articulation"
    return "ambient_or_character_motion_without_articulation"


def _audio_driver(audio_segment_type: AudioSegmentType, context: AudioPlanningContext) -> str | None:
    if audio_segment_type == AudioSegmentType.local_phrase:
        return "local_scene_phrase"
    if audio_segment_type == AudioSegmentType.music_vocal and context.global_music_track_present:
        return "global_music_track"
    if audio_segment_type == AudioSegmentType.music and context.global_music_track_present:
        return "global_music_track"
    if context.master_narration_present:
        return "master_narration"
    return None


def _build_render_task(
    shot: PlannedShot,
    scene: dict[str, Any],
) -> LtxRenderTask:
    return LtxRenderTask(
        shot_id=shot.shot_id,
        production_mode=shot.render_mode,
        use_audio_as_driver=shot.render_mode in {RenderMode.i2v_as, RenderMode.f_l_as, RenderMode.lip_sync},
        audio_source_ref=shot.audio_driver,
        start_frame_source=shot.start_frame_source,
        end_frame_source="generated_end_frame" if shot.needs_two_frames else None,
        motion_bucket=None,
        motion_interpretation=shot.motion_interpretation,
        constraints={
            "narration_mode": shot.narration_mode.value,
            "audio_segment_type": shot.audio_segment_type.value,
            "has_vocal_rhythm": shot.has_vocal_rhythm,
            "needs_two_frames": shot.needs_two_frames,
        },
        debug={
            "render_reason": shot.render_reason,
            "motion_interpretation": shot.motion_interpretation,
            "project_mode": shot.project_mode.value,
            "legacy_transition_type": _clean_str(scene.get("transitionType")) or None,
            "legacy_scene_type": _clean_str(scene.get("sceneType")) or None,
        },
    )


def validate_planned_shot(shot: PlannedShot, project_input: ProjectPlanningInput) -> PlannerValidation:
    errors = list(shot.validation_errors)
    warnings = list(shot.validation_warnings)
    if shot.render_mode == RenderMode.lip_sync and shot.lipsync_policy.allowed is not True:
        errors.append("lip_sync_policy_disallows_selected_render_mode")
    if shot.render_mode == RenderMode.lip_sync and shot.has_vocal_rhythm is not True:
        errors.append("lip_sync_requires_has_vocal_rhythm_true")
    if shot.audio_segment_type == AudioSegmentType.narration and shot.render_mode == RenderMode.lip_sync:
        errors.append("narration_segment_cannot_use_lip_sync")
    if shot.audio_segment_type == AudioSegmentType.local_phrase and shot.render_mode == RenderMode.lip_sync:
        errors.append("local_scene_phrase_cannot_auto_enable_lip_sync")
    if shot.render_mode in {RenderMode.i2v_as, RenderMode.f_l_as}:
        warnings.append("audio_sensitive_modes_are_motion_not_speech_articulation")
    if project_input.input_mode == InputMode.text_to_audio_first and not project_input.master_audio_url:
        errors.append("planning_blocked_until_master_audio_exists")
    if shot.render_mode == RenderMode.continuation:
        if not shot.parent_shot_id:
            warnings.append("continuation_missing_parent_shot_id")
        if not shot.start_frame_source:
            errors.append("continuation_requires_start_frame_source")
    if shot.render_mode in {RenderMode.f_l, RenderMode.f_l_as} and shot.needs_two_frames is not True:
        warnings.append("f_l_modes_should_enable_needs_two_frames")
    return PlannerValidation(valid=len(errors) == 0, errors=list(dict.fromkeys(errors)), warnings=list(dict.fromkeys(warnings)))


def build_audio_first_planner_output(project_input: ProjectPlanningInput, planner_result: dict[str, Any]) -> AudioFirstPlannerOutput:
    context = build_audio_planning_context(project_input)
    validation = validate_project_input(project_input, context)
    if validation.blocked:
        return AudioFirstPlannerOutput(
            input_mode=project_input.input_mode,
            project_mode=project_input.project_mode,
            planning_context=context,
            validation=validation,
            scenes=[],
            render_tasks=[],
            debug={
                "status": "blocked",
                "reason": context.planning_blocked_reason,
                "legacyPlannerPreserved": True,
            },
        )

    raw_scenes = planner_result.get("scenes") if isinstance(planner_result.get("scenes"), list) else []
    planned_scenes: list[PlannedScene] = []
    render_tasks: list[LtxRenderTask] = []
    scene_intent_by_scene: list[dict[str, str]] = []
    scene_intent_confidence: list[dict[str, Any]] = []
    scene_intent_diagnostics: list[dict[str, Any]] = []
    scene_intent_warnings: list[str] = []
    transition_intent_count = 0
    conflict_intent_count = 0
    previous_shot_id: str | None = None

    for idx, scene in enumerate(raw_scenes):
        if not isinstance(scene, dict):
            continue
        scene_id = _clean_str(scene.get("sceneId") or scene.get("id") or f"scene_{idx + 1:03d}") or f"scene_{idx + 1:03d}"
        start_sec = _to_float(scene.get("startSec") if "startSec" in scene else scene.get("start"))
        end_sec = _to_float(scene.get("endSec") if "endSec" in scene else scene.get("end"))
        duration_sec = max(0.0, _to_float(scene.get("durationSec"), end_sec - start_sec) or (end_sec - start_sec))
        audio_segment_type = _infer_audio_segment_type(scene, project_input, context)
        narration_mode = _infer_narration_mode(scene, audio_segment_type)
        lipsync_policy, has_vocal_rhythm = _compute_lipsync_policy(scene, context, audio_segment_type)
        render_mode, render_reason, suggested_modes, intent_priority, override_applied = _select_render_mode(
            scene,
            project_input,
            audio_segment_type,
            has_vocal_rhythm,
            lipsync_policy,
        )
        shot_id = f"{scene_id}__shot_001"
        scene_intent_raw = _clean_str(scene.get("intent")).lower()
        estimated_intent, estimated_confidence = _estimate_scene_intent(scene, idx)
        scene_intent = scene_intent_raw if scene_intent_raw in SCENE_INTENTS else estimated_intent
        motion_profile = _map_intent_to_motion(scene_intent)
        dominant_role_type = _clean_str(
            scene.get("dominantRoleType")
            or scene.get("primaryRoleType")
            or scene.get("roleType")
            or scene.get("role")
        ).lower()
        role_intent_validation, role_intent_warnings = _validate_role_intent(scene, dominant_role_type, scene_intent)
        if scene_intent == "transition":
            transition_intent_count += 1
        if scene_intent in {"confrontation", "threat"}:
            conflict_intent_count += 1
        continuation = render_mode == RenderMode.continuation
        start_frame_source = "previous_shot_last_frame" if continuation else "scene_keyframe"
        shot = PlannedShot(
            shot_id=shot_id,
            scene_id=scene_id,
            start_sec=round(start_sec, 3),
            end_sec=round(end_sec, 3),
            duration_sec=round(duration_sec, 3),
            shot_type=_clean_str(scene.get("shotType") or scene.get("cameraType")) or None,
            framing=_clean_str(scene.get("cameraType") or scene.get("shotType")) or None,
            render_mode=render_mode,
            render_reason=render_reason,
            audio_segment_type=audio_segment_type,
            has_vocal_rhythm=has_vocal_rhythm,
            motion_interpretation=_motion_interpretation(render_mode, audio_segment_type),
            motion_profile=motion_profile,
            project_mode=project_input.project_mode,
            audio_driver=_audio_driver(audio_segment_type, context),
            lipsync_policy=lipsync_policy,
            start_frame_source=start_frame_source,
            parent_shot_id=previous_shot_id if continuation else None,
            needs_two_frames=render_mode in {RenderMode.f_l, RenderMode.f_l_as},
            narration_mode=narration_mode,
        )
        shot_validation = validate_planned_shot(shot, project_input)
        shot.validation_errors = shot_validation.errors
        shot.validation_warnings = shot_validation.warnings
        shot.render_task = _build_render_task(shot, scene)
        render_tasks.append(shot.render_task)
        planned_scene = PlannedScene(
            scene_id=scene_id,
            scene_mode=_clean_str(scene.get("sceneMode") or scene.get("sceneType") or project_input.project_mode.value) or project_input.project_mode.value,
            start_sec=round(start_sec, 3),
            end_sec=round(end_sec, 3),
            duration_sec=round(duration_sec, 3),
            summary=_clean_str(scene.get("sceneText") or scene.get("visualDescription") or scene.get("sceneMeaning") or scene.get("title") or scene_id),
            intent=scene_intent,
            narration_mode=narration_mode,
            audio_segment_type=audio_segment_type,
            continuation_from_prev=continuation,
            shots=[shot],
        )
        planned_scenes.append(planned_scene)
        confidence_score = _compute_intent_confidence(
            scene,
            scene_intent,
            0.95 if scene_intent_raw in SCENE_INTENTS else estimated_confidence,
        )
        scene_intent_by_scene.append({"sceneId": scene_id, "intent": scene_intent})
        scene_intent_confidence.append({
            "sceneId": scene_id,
            "intent": scene_intent,
            "confidence": confidence_score,
            "source": "planner" if scene_intent_raw in SCENE_INTENTS else "estimated",
        })
        matched_keywords = _extract_intent_matches(scene, scene_intent)
        scene_intent_diagnostics.append(
            {
                "sceneId": scene_id,
                "intent": scene_intent,
                "confidence": confidence_score,
                "source": "planner" if scene_intent_raw in SCENE_INTENTS else "estimated",
                "matchedKeywords": matched_keywords,
                "suggestedRenderModes": [mode.value for mode in suggested_modes],
                "intentRenderPriority": intent_priority,
                "selectedRenderMode": render_mode.value,
                "selectedWorkflowFamily": _render_mode_to_workflow_family(render_mode),
                "audio_slice_kind": "music_vocal" if audio_segment_type == AudioSegmentType.music_vocal else ("voice_only" if audio_segment_type in {AudioSegmentType.local_phrase, AudioSegmentType.narration} else "none"),
                "music_vocal_lipsync_allowed": bool(lipsync_policy.allowed and has_vocal_rhythm),
                "sound_dialogue_allowed": bool(audio_segment_type in {AudioSegmentType.local_phrase, AudioSegmentType.narration}),
                "downgrade_reason": "" if render_mode == RenderMode.lip_sync else str(lipsync_policy.reason or "not_lipsync_or_policy_blocked"),
                "overrideApplied": override_applied,
                "roleIntentValidation": role_intent_validation,
                "motionProfile": motion_profile,
            }
        )
        if render_mode not in suggested_modes:
            scene_intent_warnings.append("intent_render_mode_mismatch_soft")
        scene_intent_warnings.extend(role_intent_warnings)
        validation.errors.extend(shot_validation.errors)
        validation.warnings.extend(shot_validation.warnings)
        previous_shot_id = shot_id

    refinement_hint: str | None = None
    if raw_scenes and (transition_intent_count / len(raw_scenes)) > 0.5:
        scene_intent_warnings.append("scene_intent_transition_overuse")
    if raw_scenes and conflict_intent_count == 0:
        scene_intent_warnings.append("scene_intent_conflict_missing")
    if any(item in {"scene_intent_transition_overuse", "scene_intent_conflict_missing"} for item in scene_intent_warnings):
        scene_intent_warnings.append("scenes lack clear narrative intent or progression")
    if any(item in {"scene_intent_transition_overuse", "scene_intent_conflict_missing"} for item in scene_intent_warnings):
        refinement_hint = "story lacks progression, missing tension or conflict"

    validation.errors = list(dict.fromkeys(validation.errors))
    validation.warnings.extend(scene_intent_warnings)
    validation.warnings = list(dict.fromkeys(validation.warnings))
    validation.valid = len(validation.errors) == 0
    durations = [float(scene.duration_sec or 0.0) for scene in planned_scenes]
    duration_span = (max(durations) - min(durations)) if durations else 0.0
    phrase_loop_prevented = len({
        str(scene.summary or "").strip().lower()
        for scene in planned_scenes
        if str(scene.summary or "").strip()
    }) < len([scene for scene in planned_scenes if str(scene.summary or "").strip()])
    clip_formula_rebalance_applied = bool(len(durations) >= 4 and duration_span > 2.0)
    arc_progression = ["entry", "build", "peak", "release_afterimage"] if len(planned_scenes) >= 4 else ["entry", "build", "release_afterimage"]

    return AudioFirstPlannerOutput(
        input_mode=project_input.input_mode,
        project_mode=project_input.project_mode,
        planning_context=context,
        validation=validation,
        scenes=planned_scenes,
        render_tasks=render_tasks,
        debug={
            "legacySceneCount": len(raw_scenes),
            "analysisSourceOfTruth": context.analysis_source_of_truth,
            "auxiliaryAudioAnalysis": context.auxiliary_audio_analysis,
            "globalMusicTrackGeneralized": bool(project_input.global_music_track_url),
            "sceneIntentByScene": scene_intent_by_scene,
            "sceneIntentConfidence": scene_intent_confidence,
            "sceneIntentWarnings": list(dict.fromkeys(scene_intent_warnings)),
            "sceneIntentDiagnostics": scene_intent_diagnostics,
            "selected_workflow_family": [item.get("selectedWorkflowFamily") for item in scene_intent_diagnostics],
            "audio_slice_kind": [item.get("audio_slice_kind") for item in scene_intent_diagnostics],
            "music_vocal_lipsync_allowed": [item.get("music_vocal_lipsync_allowed") for item in scene_intent_diagnostics],
            "sound_dialogue_allowed": [item.get("sound_dialogue_allowed") for item in scene_intent_diagnostics],
            "downgrade_reason": [item.get("downgrade_reason") for item in scene_intent_diagnostics],
            "no_text_clip_policy": "visual_arc_over_phrase_loop" if not _clean_str(project_input.story_text) else "off",
            "no_text_clip_policy_applied": bool(not _clean_str(project_input.story_text)),
            "phrase_loop_prevented": bool(phrase_loop_prevented),
            "clip_formula_rebalance_applied": bool(clip_formula_rebalance_applied),
            "phrase_boundary_priority_order": [
                "end_of_vocal_phrase",
                "clear_energy_change",
                "emotional_turn",
                "arrangement_shift",
                "beat_accent_group_ending",
                "micro_action_completion",
            ],
            "target_arc_progression": arc_progression,
            "scene_merge_or_reuse_reason": "",
            "refinementHint": refinement_hint,
        },
    )
