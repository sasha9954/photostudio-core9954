import { buildGlobalMusicPromptFromStructuredMusic } from "./comfyNarrativeDomain";

const normalizeText = (value) => String(value || "").trim();
const SCENARIO_STORYBOARD_TRACE = false;
const CLIP_TRACE_SCENARIO_FORMAT = false;
const CLIP_TRACE_SCENARIO_GLOBAL_MUSIC = false;
const DEFAULT_GLOBAL_VISUAL_LOCK = {
  captureStyle: "cinematic commercial realism",
  cameraLanguage: "controlled cinematic camera",
  lensFeel: "consistent medium focal cinematic lens compression",
  lightingStyle: "soft directional key, controlled contrast, realistic bounce light",
  colorGrade: "natural cinematic grade, balanced contrast, soft highlight rolloff",
  imageDensity: "high-end clean detailed natural texture",
  productionConsistency: "all scenes must feel captured by the same production setup",
  continuityRule: "all scenes must feel captured by the same production setup",
  forbiddenDrift: [
    "no drastic lighting changes",
    "no sudden palette shifts",
    "no image quality drops",
    "no style jumps",
    "no camera language changes",
    "no texture density drift",
  ],
};
const DEFAULT_GLOBAL_CAMERA_PROFILE = {
  lensProfile: "cinematic medium focal length, natural perspective, soft background separation",
  exposureProfile: "balanced cinematic exposure, protected highlights, readable shadows",
  dynamicRangeProfile: "wide dynamic range feel, soft highlight rolloff, no clipped look",
  sharpnessProfile: "clean but natural detail, no over-sharpened AI look",
  textureProfile: "natural skin/material texture, premium production clarity",
  motionProfile: "controlled cinematic movement, no random camera behavior",
  continuityProfile: "same capture system feel across all scenes",
  forbiddenCameraDrift: [
    "no abrupt focal length changes unless scene explicitly requires it",
    "no exposure jumps between scenes",
    "no contrast regime shifts",
    "no sharpness inconsistency",
    "no change in cinematic capture feel",
  ],
};
function normalizeStringScalar(value) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value).trim();
  return "";
}

const normalizeStringList = (value) => {
  if (Array.isArray(value)) {
    return Array.from(new Set(value.map((item) => normalizeStringScalar(item)).filter(Boolean)));
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? [trimmed] : [];
  }
  if (typeof value === "number" || typeof value === "boolean") return [String(value).trim()];
  return [];
};

function toNumber(value, fallback = 0) {
  const direct = Number(value);
  if (Number.isFinite(direct)) return direct;
  const match = String(value || "").match(/-?\d+(?:\.\d+)?/);
  if (match) {
    const parsed = Number(match[0]);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function preferRuFrom(source = {}, fallback = "") {
  return normalizeText(
    source?.ru
    ?? source?.summary_ru
    ?? source?.story_summary_ru
    ?? source?.text_ru
    ?? source?.summary
    ?? fallback
  );
}

function normalizeDualField({ ru = "", en = "" } = {}) {
  const safeEn = normalizeText(en);
  const safeRu = normalizeText(ru) || safeEn;
  return { ru: safeRu, en: safeEn || safeRu };
}

function resolveFormatAlias(...candidates) {
  for (const candidate of candidates) {
    const normalized = normalizeText(candidate);
    if (!normalized) continue;
    if (normalized === "9:16" || normalized === "16:9" || normalized === "1:1") return normalized;
  }
  return "";
}

function resolveScenarioGlobalMusicPrompt(storyboardOut = {}, directorOutput = {}) {
  const flatPrompt = normalizeText(
    storyboardOut?.globalMusicPrompt
    ?? storyboardOut?.music_prompt
    ?? storyboardOut?.bgMusicPrompt
    ?? directorOutput?.globalMusicPrompt
    ?? directorOutput?.bgMusicPrompt
    ?? directorOutput?.music?.globalMusicPrompt
    ?? directorOutput?.music_prompt
  );
  const structuredMusic = (
    (directorOutput?.music && typeof directorOutput.music === "object" ? directorOutput.music : null)
    || (storyboardOut?.music && typeof storyboardOut.music === "object" ? storyboardOut.music : null)
    || null
  );
  const synthesizedPrompt = flatPrompt ? "" : buildGlobalMusicPromptFromStructuredMusic(structuredMusic);
  if (CLIP_TRACE_SCENARIO_GLOBAL_MUSIC) {
    console.debug("[SCENARIO GLOBAL MUSIC SYNTH]", {
      hasFlatPrompt: !!flatPrompt,
      hasStructuredMusic: !!structuredMusic,
      mood: normalizeText(structuredMusic?.mood ?? structuredMusic?.musicMood ?? structuredMusic?.music_mood),
      style: normalizeText(structuredMusic?.style ?? structuredMusic?.musicStyle ?? structuredMusic?.music_style),
      hasPacingHints: !!normalizeText(structuredMusic?.pacingHints ?? structuredMusic?.pacing_hints ?? structuredMusic?.pacing),
      synthesizedPromptLength: synthesizedPrompt.length,
    });
  }
  return flatPrompt || synthesizedPrompt;
}

export function normalizeScenarioScene(scene = {}, index = 0, scenarioPackage = null) {
  const source = scene && typeof scene === "object" ? scene : {};
  const t0 = toNumber(source.t0 ?? source.time_start ?? source.timeStart, index * 5);
  const durationRaw = toNumber(source.durationSec ?? source.duration, 5);
  const t1 = Math.max(t0, toNumber(source.t1 ?? source.time_end ?? source.timeEnd, t0 + durationRaw));
  const durationSec = Math.max(0, Number((t1 - t0).toFixed(3)));
  const ltxMode = normalizeText(source.ltxMode ?? source.ltx_mode) || "i2v_as";
  const renderMode = normalizeText(source.renderMode)
    || (["f_l", "f_l_as"].includes(ltxMode) ? "first_last" : "image_to_video");

  const summaryDual = normalizeDualField({
    ru: source.summaryRu ?? source.summary_ru ?? source.sceneGoalRu ?? source.scene_goal_ru ?? source.sceneGoal ?? source.scene_goal ?? source.action,
    en: source.summaryEn ?? source.summary_en ?? source.sceneGoalEn ?? source.scene_goal_en ?? source.scene_goal ?? source.sceneGoal ?? source.action,
  });
  const imageDual = normalizeDualField({
    ru: source.imagePromptRu ?? source.image_prompt_ru ?? source.imagePrompt ?? source.image_prompt,
    en: source.imagePromptEn ?? source.image_prompt_en ?? source.imagePrompt ?? source.image_prompt,
  });
  const videoDual = normalizeDualField({
    ru: source.videoPromptRu ?? source.video_prompt_ru ?? source.videoPrompt ?? source.video_prompt,
    en: source.videoPromptEn ?? source.video_prompt_en ?? source.videoPrompt ?? source.video_prompt,
  });
  const cameraDual = normalizeDualField({
    ru: source.cameraRu ?? source.camera_ru ?? source.cameraIdea ?? source.camera,
    en: source.cameraEn ?? source.camera_en ?? source.cameraIdea ?? source.camera,
  });
  const emotionDual = normalizeDualField({
    ru: source.emotionRu ?? source.emotion_ru ?? source.emotion,
    en: source.emotionEn ?? source.emotion_en ?? source.emotion,
  });
  const locationDual = normalizeDualField({
    ru: source.locationRu ?? source.location_ru ?? source.worldRu ?? source.world_ru ?? source.location,
    en: source.locationEn ?? source.location_en ?? source.worldEn ?? source.world_en ?? source.location,
  });

  const forbiddenInsertionsRaw = source.forbiddenInsertions ?? source.forbidden_insertions;
  const forbiddenChangesRaw = source.forbiddenChanges ?? source.forbidden_changes;
  const forbiddenInsertions = normalizeStringList(forbiddenInsertionsRaw);
  const forbiddenChanges = normalizeStringList(forbiddenChangesRaw);
  const inheritedPackageFormat = resolveFormatAlias(
    scenarioPackage?.format,
    scenarioPackage?.aspectRatio,
    scenarioPackage?.aspect_ratio,
    scenarioPackage?.canvas
  );
  const sceneFormat = resolveFormatAlias(
    source?.format,
    source?.imageFormat,
    source?.image_format,
    source?.aspectRatio,
    source?.aspect_ratio,
    source?.canvas,
    inheritedPackageFormat
  );
  const normalizedScene = {
    sceneId: normalizeText(source.sceneId ?? source.scene_id) || `S${index + 1}`,
    t0,
    t1,
    durationSec,
    summaryRu: summaryDual.ru,
    summaryEn: summaryDual.en,
    imagePromptRu: imageDual.ru,
    imagePromptEn: imageDual.en,
    videoPromptRu: videoDual.ru,
    videoPromptEn: videoDual.en,
    cameraRu: cameraDual.ru,
    cameraEn: cameraDual.en,
    emotionRu: emotionDual.ru,
    emotionEn: emotionDual.en,
    actors: Array.isArray(source.actors ?? source.participants) ? (source.actors ?? source.participants).filter(Boolean) : [],
    locationRu: locationDual.ru,
    locationEn: locationDual.en,
    renderMode,
    ltxMode,
    ltxReason: normalizeText(source.ltxReason ?? source.ltx_reason ?? source.whyThisMode),
    needsTwoFrames: Boolean(source.needsTwoFrames ?? source.needs_two_frames ?? ["first_last"].includes(renderMode)),
    continuationFromPrevious: Boolean(source.continuationFromPrevious ?? source.continuation_from_previous ?? source.continuation),
    narrationMode: normalizeText(source.narrationMode ?? source.narration_mode) || "full",
    localPhrase: normalizeText(source.localPhrase ?? source.local_phrase),
    sfx: normalizeText(source.sfx),
    musicMixHint: normalizeText(source.musicMixHint ?? source.music_mix_hint) || "medium",
    speakerRole: normalizeText(source.speakerRole ?? source.speaker_role),
    audioSliceStartSec: toNumber(source.audioSliceStartSec ?? source.audio_slice_start_sec ?? source.time_start, t0),
    audioSliceEndSec: toNumber(source.audioSliceEndSec ?? source.audio_slice_end_sec ?? source.time_end, t1),
    audioSliceExpectedDurationSec: toNumber(source.audioSliceExpectedDurationSec ?? source.audio_slice_expected_duration_sec ?? durationSec, durationSec),
    startFramePromptRu: normalizeText(source.startFramePromptRu ?? source.start_frame_prompt_ru ?? source.startFramePrompt),
    startFramePromptEn: normalizeText(source.startFramePromptEn ?? source.start_frame_prompt_en ?? source.startFramePrompt),
    endFramePromptRu: normalizeText(source.endFramePromptRu ?? source.end_frame_prompt_ru ?? source.endFramePrompt),
    endFramePromptEn: normalizeText(source.endFramePromptEn ?? source.end_frame_prompt_en ?? source.endFramePrompt),
    imageUrl: normalizeText(source.imageUrl ?? source.image_url ?? source.previewUrl ?? source.preview_url),
    imageStatus: normalizeText(source.imageStatus ?? source.image_status),
    format: sceneFormat,
    imageFormat: sceneFormat,
    startFrameImageUrl: normalizeText(source.startFrameImageUrl ?? source.start_frame_image_url ?? source.startFramePreviewUrl ?? source.start_frame_preview_url),
    startFrameStatus: normalizeText(source.startFrameStatus ?? source.start_frame_status),
    endFrameImageUrl: normalizeText(source.endFrameImageUrl ?? source.end_frame_image_url ?? source.endFramePreviewUrl ?? source.end_frame_preview_url),
    endFrameStatus: normalizeText(source.endFrameStatus ?? source.end_frame_status),
    sceneType: source.sceneType ?? source.scene_type,
    primaryRole: source.primaryRole ?? source.primary_role,
    secondaryRoles: source.secondaryRoles ?? source.secondary_roles,
    refsUsed: source.refsUsed ?? source.refs_used,
    refDirectives: source.refDirectives ?? source.ref_directives,
    focalSubject: source.focalSubject ?? source.focal_subject,
    sceneAction: source.sceneAction ?? source.scene_action,
    cameraIntent: source.cameraIntent ?? source.camera_intent,
    environmentMotion: source.environmentMotion ?? source.environment_motion,
    forbiddenInsertions,
    forbiddenChanges,
    lipSync: source.lipSync ?? source.lip_sync,
    lipSyncText: source.lipSyncText ?? source.lip_sync_text,
    transitionType: source.transitionType ?? source.transition_type,
    shotType: source.shotType ?? source.shot_type,
    continuity: source.continuity,
    worldScaleContext: source.worldScaleContext ?? source.world_scale_context,
    entityScaleAnchors: source.entityScaleAnchors ?? source.entity_scale_anchors,
    environmentLock: source.environmentLock ?? source.environment_lock,
    styleLock: source.styleLock ?? source.style_lock,
    identityLock: source.identityLock ?? source.identity_lock,
    mustAppear: source.mustAppear ?? source.must_appear,
    mustNotAppear: source.mustNotAppear ?? source.must_not_appear,
    heroEntityId: source.heroEntityId ?? source.hero_entity_id,
    supportEntityIds: source.supportEntityIds ?? source.support_entity_ids,
    plannerDebug: source.plannerDebug ?? source.planner_debug,
    generationHints: source.generationHints ?? source.generation_hints,
    modelAssignments: source.modelAssignments ?? source.model_assignments,
    providerHints: source.providerHints ?? source.provider_hints,
    audioDurationSec: source.audioDurationSec ?? source.audio_duration_sec,
    sceneMeta: source.sceneMeta ?? source.scene_meta,
    debug: source.debug,
    meta: source.meta,
    globalVisualLock: scenarioPackage?.globalVisualLock || null,
    globalCameraProfile: scenarioPackage?.globalCameraProfile || null,
  };
  if (SCENARIO_STORYBOARD_TRACE) {
    console.debug("[SCENARIO TRANSFER] normalized scene", {
      sceneId: normalizedScene.sceneId,
      renderMode: normalizedScene.renderMode,
      ltxMode: normalizedScene.ltxMode,
      sceneType: normalizedScene.sceneType,
      primaryRole: normalizedScene.primaryRole,
      secondaryRoles: Array.isArray(normalizedScene.secondaryRoles) ? normalizedScene.secondaryRoles : [],
      refsUsed: Array.isArray(normalizedScene.refsUsed) ? normalizedScene.refsUsed : [],
      lipSync: Boolean(normalizedScene.lipSync),
      audioSliceStartSec: normalizedScene.audioSliceStartSec,
      audioSliceEndSec: normalizedScene.audioSliceEndSec,
      hasContinuity: !!normalizedScene.continuity,
      hasIdentityLock: normalizedScene.identityLock !== undefined && normalizedScene.identityLock !== null,
      hasStyleLock: normalizedScene.styleLock !== undefined && normalizedScene.styleLock !== null,
      hasEnvironmentLock: normalizedScene.environmentLock !== undefined && normalizedScene.environmentLock !== null,
      hasMustAppear: Array.isArray(normalizedScene.mustAppear) ? normalizedScene.mustAppear.length > 0 : !!normalizedScene.mustAppear,
      hasMustNotAppear: Array.isArray(normalizedScene.mustNotAppear) ? normalizedScene.mustNotAppear.length > 0 : !!normalizedScene.mustNotAppear,
      hasModelAssignments: !!normalizedScene.modelAssignments,
      hasProviderHints: !!normalizedScene.providerHints,
    });
  }
  return normalizedScene;
}

function buildGlobalVisualLock(storyboardOut = {}, directorOutput = {}) {
  const existingLock = storyboardOut?.globalVisualLock
    ?? storyboardOut?.global_visual_lock
    ?? directorOutput?.globalVisualLock
    ?? directorOutput?.global_visual_lock;
  const baseLock = existingLock && typeof existingLock === "object" ? existingLock : {};
  const styleLock = storyboardOut?.styleLock ?? storyboardOut?.style_lock ?? directorOutput?.styleLock ?? directorOutput?.style_lock;
  const environmentLock = storyboardOut?.environmentLock ?? storyboardOut?.environment_lock ?? directorOutput?.environmentLock ?? directorOutput?.environment_lock;
  const world = storyboardOut?.world ?? storyboardOut?.world_en ?? storyboardOut?.world_ru ?? directorOutput?.world ?? directorOutput?.worldEn ?? directorOutput?.worldRu;
  const generationHints = storyboardOut?.generationHints ?? storyboardOut?.generation_hints ?? directorOutput?.generationHints ?? directorOutput?.generation_hints;
  const providerHints = storyboardOut?.providerHints ?? storyboardOut?.provider_hints ?? directorOutput?.providerHints ?? directorOutput?.provider_hints;
  const modelAssignments = storyboardOut?.modelAssignments ?? storyboardOut?.model_assignments ?? directorOutput?.modelAssignments ?? directorOutput?.model_assignments;
  const meta = storyboardOut?.meta ?? directorOutput?.meta;
  const debug = storyboardOut?.debug ?? directorOutput?.debug;
  const hasAnySource = !!existingLock || !!styleLock || !!environmentLock || !!world || !!generationHints || !!providerHints || !!modelAssignments || !!meta || !!debug;
  const nextForbiddenDrift = normalizeStringList(baseLock?.forbiddenDrift).length
    ? Array.from(new Set([...DEFAULT_GLOBAL_VISUAL_LOCK.forbiddenDrift, ...normalizeStringList(baseLock?.forbiddenDrift)]))
    : DEFAULT_GLOBAL_VISUAL_LOCK.forbiddenDrift;
  return hasAnySource
    ? {
      ...DEFAULT_GLOBAL_VISUAL_LOCK,
      ...(styleLock && typeof styleLock === "object" ? { styleLock } : {}),
      ...(environmentLock && typeof environmentLock === "object" ? { environmentLock } : {}),
      ...(world ? { world } : {}),
      ...(generationHints ? { generationHints } : {}),
      ...(providerHints ? { providerHints } : {}),
      ...(modelAssignments ? { modelAssignments } : {}),
      ...(meta ? { meta } : {}),
      ...(debug ? { debug } : {}),
      ...baseLock,
      forbiddenDrift: nextForbiddenDrift,
    }
    : {
      ...DEFAULT_GLOBAL_VISUAL_LOCK,
      forbiddenDrift: [...DEFAULT_GLOBAL_VISUAL_LOCK.forbiddenDrift],
    };
}

function buildGlobalCameraProfile(storyboardOut = {}, directorOutput = {}) {
  const existingProfile = storyboardOut?.globalCameraProfile
    ?? storyboardOut?.global_camera_profile
    ?? directorOutput?.globalCameraProfile
    ?? directorOutput?.global_camera_profile;
  const baseProfile = existingProfile && typeof existingProfile === "object" ? existingProfile : {};
  const nextForbiddenCameraDrift = normalizeStringList(baseProfile?.forbiddenCameraDrift).length
    ? Array.from(new Set([...DEFAULT_GLOBAL_CAMERA_PROFILE.forbiddenCameraDrift, ...normalizeStringList(baseProfile?.forbiddenCameraDrift)]))
    : DEFAULT_GLOBAL_CAMERA_PROFILE.forbiddenCameraDrift;
  return {
    ...DEFAULT_GLOBAL_CAMERA_PROFILE,
    ...baseProfile,
    forbiddenCameraDrift: nextForbiddenCameraDrift,
  };
}

export function normalizeScenarioStoryboardPackage({ storyboardOut = null, directorOutput = null } = {}) {
  const globalVisualLock = buildGlobalVisualLock(storyboardOut || {}, directorOutput || {});
  const globalCameraProfile = buildGlobalCameraProfile(storyboardOut || {}, directorOutput || {});
  const format = resolveFormatAlias(
    storyboardOut?.format,
    storyboardOut?.aspectRatio,
    storyboardOut?.aspect_ratio,
    storyboardOut?.canvas,
    directorOutput?.format,
    directorOutput?.aspectRatio,
    directorOutput?.aspect_ratio,
    directorOutput?.canvas
  );
  const scenesRaw = Array.isArray(storyboardOut?.scenes)
    ? storyboardOut.scenes
    : Array.isArray(directorOutput?.scenes)
      ? directorOutput.scenes
      : [];
  const scenes = scenesRaw.map((scene, idx) => normalizeScenarioScene(scene, idx, { globalVisualLock, globalCameraProfile, format }));

  const storySummary = normalizeDualField({
    ru: storyboardOut?.story_summary_ru ?? directorOutput?.history?.summaryRu ?? preferRuFrom(directorOutput?.history, storyboardOut?.story_summary),
    en: storyboardOut?.story_summary_en ?? storyboardOut?.story_summary ?? directorOutput?.history?.summaryEn ?? directorOutput?.history?.summary,
  });
  const world = normalizeDualField({
    ru: storyboardOut?.world_ru ?? directorOutput?.history?.worldRu ?? scenes.find((scene) => !!scene.locationRu)?.locationRu,
    en: storyboardOut?.world_en ?? directorOutput?.history?.worldEn ?? scenes.find((scene) => !!scene.locationEn)?.locationEn,
  });
  const previewPrompt = normalizeDualField({
    ru: storyboardOut?.preview_prompt_ru ?? directorOutput?.history?.previewPromptRu ?? storySummary.ru,
    en: storyboardOut?.preview_prompt_en ?? directorOutput?.history?.previewPromptEn ?? storySummary.en,
  });
  const actors = Array.from(new Set(scenes.flatMap((scene) => (Array.isArray(scene.actors) ? scene.actors : [])).filter(Boolean)));
  const locations = Array.from(new Set(scenes.map((scene) => normalizeText(scene.locationEn || scene.locationRu)).filter(Boolean)));
  const globalMusicPrompt = resolveScenarioGlobalMusicPrompt(storyboardOut || {}, directorOutput || {});
  const musicPromptRu = normalizeText(
    storyboardOut?.musicPromptRu
    ?? storyboardOut?.music_prompt_ru
    ?? directorOutput?.musicPromptRu
    ?? directorOutput?.music_prompt_ru
  );
  const musicPromptEn = normalizeText(
    storyboardOut?.musicPromptEn
    ?? storyboardOut?.music_prompt_en
    ?? directorOutput?.musicPromptEn
    ?? directorOutput?.music_prompt_en
  );

  const normalizedPackage = {
    scenes,
    format,
    storySummaryRu: storySummary.ru,
    storySummaryEn: storySummary.en,
    worldRu: world.ru,
    worldEn: world.en,
    previewPromptRu: previewPrompt.ru,
    previewPromptEn: previewPrompt.en,
    actors,
    locations,
    audioUrl: normalizeText(
      storyboardOut?.audioUrl
      ?? storyboardOut?.audio_url
      ?? directorOutput?.audioUrl
      ?? directorOutput?.audio_url
    ),
    audioDurationSec: toNumber(
      storyboardOut?.audioDurationSec
      ?? storyboardOut?.audio_duration_sec
      ?? directorOutput?.audioDurationSec
      ?? directorOutput?.audio_duration_sec,
      0
    ),
    globalMusicPrompt,
    musicPromptRu,
    musicPromptEn,
    bgMusicPrompt: normalizeText(storyboardOut?.bgMusicPrompt ?? directorOutput?.bgMusicPrompt) || globalMusicPrompt,
    musicStatus: normalizeText(storyboardOut?.musicStatus ?? storyboardOut?.music_status ?? directorOutput?.musicStatus ?? directorOutput?.music_status),
    musicUrl: normalizeText(storyboardOut?.musicUrl ?? storyboardOut?.music_url ?? directorOutput?.musicUrl ?? directorOutput?.music_url),
    plannerDebug: storyboardOut?.plannerDebug ?? storyboardOut?.planner_debug ?? directorOutput?.plannerDebug ?? directorOutput?.planner_debug,
    generationHints: storyboardOut?.generationHints ?? storyboardOut?.generation_hints ?? directorOutput?.generationHints ?? directorOutput?.generation_hints,
    globalVisualLock,
    globalCameraProfile,
    modelAssignments: storyboardOut?.modelAssignments ?? storyboardOut?.model_assignments ?? directorOutput?.modelAssignments ?? directorOutput?.model_assignments,
    providerHints: storyboardOut?.providerHints ?? storyboardOut?.provider_hints ?? directorOutput?.providerHints ?? directorOutput?.provider_hints,
    debug: storyboardOut?.debug ?? directorOutput?.debug,
    meta: storyboardOut?.meta ?? directorOutput?.meta,
  };
  if (CLIP_TRACE_SCENARIO_FORMAT) {
    console.debug("[SCENARIO FORMAT] normalized package", {
      format: normalizedPackage.format || "",
      scenesCount: Array.isArray(normalizedPackage.scenes) ? normalizedPackage.scenes.length : 0,
    });
  }
  if (CLIP_TRACE_SCENARIO_GLOBAL_MUSIC) {
    console.debug("[SCENARIO GLOBAL MUSIC]", {
      hasDirectorGlobalMusicPrompt: !!normalizeText(
        directorOutput?.globalMusicPrompt
        ?? directorOutput?.music?.globalMusicPrompt
        ?? directorOutput?.music_prompt
        ?? directorOutput?.bgMusicPrompt
      ),
      hasStoryboardOutMusicPrompt: !!normalizeText(
        storyboardOut?.globalMusicPrompt
        ?? storyboardOut?.music_prompt
        ?? storyboardOut?.bgMusicPrompt
      ),
      normalizedGlobalMusicPromptLength: globalMusicPrompt.length,
    });
  }
  return normalizedPackage;
}

export function buildScenarioPreviewInput({ storyboardOut = null, directorOutput = null, format = "9:16", styleProfile = "" } = {}) {
  const pkg = normalizeScenarioStoryboardPackage({ storyboardOut, directorOutput });
  const resolvedFormat = resolveFormatAlias(format, pkg?.format) || "9:16";
  return {
    storySummaryRu: pkg.storySummaryRu,
    storySummaryEn: pkg.storySummaryEn,
    worldRu: pkg.worldRu,
    worldEn: pkg.worldEn,
    previewPromptRu: pkg.previewPromptRu,
    previewPromptEn: pkg.previewPromptEn,
    styleProfile: normalizeText(styleProfile),
    actors: pkg.actors,
    locations: pkg.locations,
    refsByRole: directorOutput?.refsByRole && typeof directorOutput.refsByRole === "object" ? directorOutput.refsByRole : {},
    format: resolvedFormat,
  };
}
