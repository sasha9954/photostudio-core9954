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


class AudioLayerRef(BaseModel):
    kind: str
    url: str | None = None
    present: bool = False
    source: str | None = None
    label: str | None = None


class LipSyncPolicy(BaseModel):
    allowed: bool = False
    reason: str = "lip_sync_not_evaluated"
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
    motion_bucket: str | None = None
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
    if has_vocals and (context.global_music_track_present or project_input.project_mode == ProjectMode.music_first or audio_type in {"song", "song_with_vocals", "vocals"}):
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
    project_input: ProjectPlanningInput,
    context: AudioPlanningContext,
    audio_segment_type: AudioSegmentType,
) -> tuple[LipSyncPolicy, bool]:
    has_vocal_present = bool(scene.get("hasVocals") is True or scene.get("isLipSync") is True or scene.get("lipSync") is True)
    suitable_framing = _shot_supports_lipsync(scene)
    has_vocal_rhythm = audio_segment_type == AudioSegmentType.music_vocal and has_vocal_present and context.global_music_track_present
    if audio_segment_type == AudioSegmentType.narration:
        return LipSyncPolicy(allowed=False, reason="narration_segments_must_not_use_lip_sync"), has_vocal_rhythm
    if audio_segment_type == AudioSegmentType.local_phrase:
        return LipSyncPolicy(allowed=False, reason="local_scene_phrase_does_not_enable_lip_sync_automatically"), has_vocal_rhythm
    if not has_vocal_present:
        return LipSyncPolicy(allowed=False, reason="vocal_presence_required_for_lip_sync"), has_vocal_rhythm
    if not context.global_music_track_present and project_input.project_mode != ProjectMode.music_first:
        return LipSyncPolicy(allowed=False, reason="musical_rhythmic_support_required_for_lip_sync"), has_vocal_rhythm
    if not suitable_framing:
        return LipSyncPolicy(allowed=False, reason="shot_framing_not_suitable_for_lip_sync"), has_vocal_rhythm
    if audio_segment_type != AudioSegmentType.music_vocal:
        return LipSyncPolicy(allowed=False, reason="lip_sync_only_allowed_for_music_driven_vocal_scenes"), has_vocal_rhythm
    return LipSyncPolicy(allowed=True, reason="music_vocal_rhythm_and_supported_framing_confirmed"), True


def _select_render_mode(
    scene: dict[str, Any],
    project_input: ProjectPlanningInput,
    audio_segment_type: AudioSegmentType,
    has_vocal_rhythm: bool,
    lipsync_policy: LipSyncPolicy,
) -> tuple[RenderMode, str]:
    wants_lipsync = bool(scene.get("isLipSync") is True or scene.get("lipSync") is True)
    if wants_lipsync and lipsync_policy.allowed and has_vocal_rhythm:
        return RenderMode.lip_sync, "music_vocal_scene_with_rhythm_and_supported_framing"
    if _is_continuation(scene):
        return RenderMode.continuation, "continuation_of_previous_visual_flow"
    audio_accent = _has_audio_accent(scene, audio_segment_type)
    if _needs_controlled_transition(scene):
        if audio_accent:
            return RenderMode.f_l_as, "controlled_a_to_b_transition_aligned_to_audio_accent"
        return RenderMode.f_l, "controlled_a_to_b_transition_without_audio_accent"
    if audio_accent:
        return RenderMode.i2v_as, "audio_sensitive_motion_without_speech_articulation"
    return RenderMode.i2v, "base_single_frame_animation_without_forced_articulation"


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
    if audio_segment_type == AudioSegmentType.music_vocal:
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
        motion_bucket=shot.motion_interpretation,
        constraints={
            "narration_mode": shot.narration_mode.value,
            "audio_segment_type": shot.audio_segment_type.value,
            "has_vocal_rhythm": shot.has_vocal_rhythm,
            "needs_two_frames": shot.needs_two_frames,
        },
        debug={
            "render_reason": shot.render_reason,
            "legacy_transition_type": _clean_str(scene.get("transitionType")) or None,
            "legacy_scene_type": _clean_str(scene.get("sceneType")) or None,
        },
    )


def validate_planned_shot(shot: PlannedShot, project_input: ProjectPlanningInput) -> PlannerValidation:
    errors = list(shot.validation_errors)
    warnings = list(shot.validation_warnings)
    if shot.render_mode == RenderMode.lip_sync and shot.has_vocal_rhythm is not True:
        errors.append("lip_sync_requires_has_vocal_rhythm_true")
    if shot.audio_segment_type == AudioSegmentType.narration and shot.render_mode == RenderMode.lip_sync:
        errors.append("narration_segment_cannot_use_lip_sync")
    if shot.audio_segment_type == AudioSegmentType.local_phrase and shot.render_mode == RenderMode.lip_sync:
        errors.append("local_scene_phrase_cannot_auto_enable_lip_sync")
    if shot.render_mode in {RenderMode.i2v_as, RenderMode.f_l_as}:
        warnings.append("audio_sensitive_modes_are_motion_not_speech_articulation")
    if project_input.project_mode == ProjectMode.narration_first and shot.audio_segment_type == AudioSegmentType.narration and shot.render_mode == RenderMode.lip_sync:
        errors.append("narration_first_projects_must_not_prefer_lip_sync_for_narration")
    if shot.render_mode == RenderMode.continuation:
        if not shot.parent_shot_id:
            errors.append("continuation_requires_parent_shot_id")
        if not shot.start_frame_source:
            errors.append("continuation_requires_start_frame_source")
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
        lipsync_policy, has_vocal_rhythm = _compute_lipsync_policy(scene, project_input, context, audio_segment_type)
        render_mode, render_reason = _select_render_mode(scene, project_input, audio_segment_type, has_vocal_rhythm, lipsync_policy)
        shot_id = f"{scene_id}__shot_001"
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
            narration_mode=narration_mode,
            audio_segment_type=audio_segment_type,
            continuation_from_prev=continuation,
            shots=[shot],
        )
        planned_scenes.append(planned_scene)
        validation.errors.extend(shot_validation.errors)
        validation.warnings.extend(shot_validation.warnings)
        previous_shot_id = shot_id

    validation.errors = list(dict.fromkeys(validation.errors))
    validation.warnings = list(dict.fromkeys(validation.warnings))
    validation.valid = len(validation.errors) == 0

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
        },
    )
