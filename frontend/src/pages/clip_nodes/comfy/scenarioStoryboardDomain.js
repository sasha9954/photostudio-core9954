const normalizeText = (value) => String(value || "").trim();

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

export function normalizeScenarioScene(scene = {}, index = 0) {
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

  return {
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
  };
}

export function normalizeScenarioStoryboardPackage({ storyboardOut = null, directorOutput = null } = {}) {
  const scenesRaw = Array.isArray(storyboardOut?.scenes)
    ? storyboardOut.scenes
    : Array.isArray(directorOutput?.scenes)
      ? directorOutput.scenes
      : [];
  const scenes = scenesRaw.map((scene, idx) => normalizeScenarioScene(scene, idx));

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

  return {
    scenes,
    storySummaryRu: storySummary.ru,
    storySummaryEn: storySummary.en,
    worldRu: world.ru,
    worldEn: world.en,
    previewPromptRu: previewPrompt.ru,
    previewPromptEn: previewPrompt.en,
    actors,
    locations,
  };
}

export function buildScenarioPreviewInput({ storyboardOut = null, directorOutput = null, format = "9:16", styleProfile = "" } = {}) {
  const pkg = normalizeScenarioStoryboardPackage({ storyboardOut, directorOutput });
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
    format: normalizeText(format) || "9:16",
  };
}
