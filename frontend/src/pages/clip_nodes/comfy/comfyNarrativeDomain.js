export const NARRATIVE_SOURCE_OPTIONS = [
  { value: "AUDIO", labelRu: "Аудио" },
  { value: "VIDEO_FILE", labelRu: "Видео файл" },
  { value: "VIDEO_LINK", labelRu: "Ссылка на видео" },
];

export const NARRATIVE_CONTENT_TYPE_REGISTRY = [
  {
    value: "story",
    labelRu: "История",
    isEnabled: true,
    policyKey: "story",
    modeFamily: "narrative",
    usesGlobalMusicPrompt: true,
    supportsLipSync: false,
    supportsAudioSlices: false,
    prefersPerformanceCloseup: false,
    defaultLtxStrategy: "balanced_story",
    summaryStyle: "story_arc",
    notesRu: "Базовый сюжетный режим с нейтральной музыкальной политикой.",
  },
  {
    value: "music_video",
    labelRu: "Клип",
    isEnabled: true,
    policyKey: "music_video",
    modeFamily: "performance",
    usesGlobalMusicPrompt: false,
    supportsLipSync: true,
    supportsAudioSlices: true,
    prefersPerformanceCloseup: true,
    defaultLtxStrategy: "i2v",
    summaryStyle: "beat_driven",
    notesRu: "Клип опирается на master audio и не требует synthetic global music prompt.",
  },
  {
    value: "ad",
    labelRu: "Реклама",
    isEnabled: true,
    policyKey: "ad",
    modeFamily: "commercial",
    usesGlobalMusicPrompt: true,
    supportsLipSync: false,
    supportsAudioSlices: false,
    prefersPerformanceCloseup: false,
    defaultLtxStrategy: "brand_focus",
    summaryStyle: "value_prop",
    notesRu: "Коммерческий режим с безопасной stub-политикой.",
  },
  {
    value: "cartoon",
    labelRu: "Мультфильм",
    isEnabled: false,
    policyKey: "cartoon",
    modeFamily: "stylized",
    usesGlobalMusicPrompt: true,
    supportsLipSync: true,
    supportsAudioSlices: false,
    prefersPerformanceCloseup: false,
    defaultLtxStrategy: "stylized_motion",
    summaryStyle: "expressive_arc",
    notesRu: "Анимационный режим с безопасной политикой для будущего апгрейда.",
  },
  {
    value: "teaser",
    labelRu: "Тизер",
    isEnabled: false,
    policyKey: "teaser",
    modeFamily: "promo",
    usesGlobalMusicPrompt: true,
    supportsLipSync: false,
    supportsAudioSlices: false,
    prefersPerformanceCloseup: false,
    defaultLtxStrategy: "hook_first",
    summaryStyle: "tease_hook",
    notesRu: "Короткий промо-режим с акцентом на крючок.",
  },
  {
    value: "series",
    labelRu: "Сериал",
    isEnabled: false,
    policyKey: "series",
    modeFamily: "episodic",
    usesGlobalMusicPrompt: true,
    supportsLipSync: false,
    supportsAudioSlices: false,
    prefersPerformanceCloseup: false,
    defaultLtxStrategy: "episodic_continuity",
    summaryStyle: "episode_arc",
    notesRu: "Эпизодический режим без жёсткой режиссёрской кастомизации.",
  },
  {
    value: "film",
    labelRu: "Фильм",
    isEnabled: false,
    policyKey: "film",
    modeFamily: "cinematic",
    usesGlobalMusicPrompt: true,
    supportsLipSync: false,
    supportsAudioSlices: false,
    prefersPerformanceCloseup: false,
    defaultLtxStrategy: "cinematic_long_arc",
    summaryStyle: "feature_arc",
    notesRu: "Кинорежим как каркас для последующей тонкой режиссуры.",
  },
  {
    value: "comics",
    labelRu: "Комикс",
    isEnabled: false,
    policyKey: "comics",
    modeFamily: "stylized",
    usesGlobalMusicPrompt: true,
    supportsLipSync: false,
    supportsAudioSlices: false,
    prefersPerformanceCloseup: false,
    defaultLtxStrategy: "panel_like",
    summaryStyle: "panel_story",
    notesRu: "Комикс-режим с безопасным policy scaffold.",
  },
  {
    value: "documentary",
    labelRu: "Документалка",
    isEnabled: false,
    policyKey: "documentary",
    modeFamily: "factual",
    usesGlobalMusicPrompt: true,
    supportsLipSync: false,
    supportsAudioSlices: true,
    prefersPerformanceCloseup: false,
    defaultLtxStrategy: "observational",
    summaryStyle: "fact_driven",
    notesRu: "Документальный режим для честного world-context повествования.",
  },
  {
    value: "trailer",
    labelRu: "Трейлер",
    isEnabled: false,
    policyKey: "trailer",
    modeFamily: "promo",
    usesGlobalMusicPrompt: false,
    supportsLipSync: false,
    supportsAudioSlices: true,
    prefersPerformanceCloseup: false,
    defaultLtxStrategy: "impact_cut",
    summaryStyle: "peak_moments",
    notesRu: "Трейлер не синтезирует global music prompt поверх master audio.",
  },
];

export const NARRATIVE_CONTENT_TYPE_OPTIONS = NARRATIVE_CONTENT_TYPE_REGISTRY.map(({ value, labelRu, isEnabled }) => ({
  value,
  labelRu,
  isEnabled: isEnabled !== false,
}));

export const NARRATIVE_MODE_OPTIONS = [
  { value: "strict_reference", labelRu: "Строго по референсу" },
  { value: "cinematic_expand", labelRu: "Расширить кинематографично" },
  { value: "rewrite_full", labelRu: "Полностью переписать" },
  { value: "visual_idea_only", labelRu: "Только визуальная идея" },
  { value: "pro_screenplay", labelRu: "Профессиональный сценарий" },
];

export const NARRATIVE_STYLE_OPTIONS = [
  { value: "realistic", labelRu: "Реалистичный" },
  { value: "dark_horror", labelRu: "Тёмный (хоррор)" },
  { value: "documentary", labelRu: "Документальный" },
  { value: "music_clip", labelRu: "Клип (музыкальный)" },
  { value: "premium_ad", labelRu: "Реклама (премиум)" },
  { value: "cartoon", labelRu: "Мультяшный" },
  { value: "thriller", labelRu: "Триллер" },
  { value: "noir", labelRu: "Нуар" },
];

export const NARRATIVE_FORMAT_OPTIONS = ["9:16", "16:9", "1:1"];

export const NARRATIVE_RESULT_TABS = [
  { value: "history", labelRu: "История" },
  { value: "scenes", labelRu: "Сцены" },
  { value: "video", labelRu: "Видео" },
  { value: "sound", labelRu: "Звук" },
  { value: "music", labelRu: "Музыка" },
  { value: "json", labelRu: "JSON" },
];

export const NARRATIVE_INPUT_HANDLES = [
  { id: "audio_in", labelRu: "Аудио", mode: "AUDIO", kind: "story_source" },
  { id: "video_file_in", labelRu: "Видео файл", mode: "VIDEO_FILE", kind: "story_source" },
  { id: "video_link_in", labelRu: "Ссылка на видео", mode: "VIDEO_LINK", kind: "story_source" },
  { id: "ref_character_1", labelRu: "Персонаж 1", mode: "CONTEXT", kind: "context", role: "character_1" },
  { id: "ref_character_2", labelRu: "Персонаж 2", mode: "CONTEXT", kind: "context", role: "character_2" },
  { id: "ref_character_3", labelRu: "Персонаж 3", mode: "CONTEXT", kind: "context", role: "character_3" },
  { id: "ref_props", labelRu: "Предметы", mode: "CONTEXT", kind: "context", role: "props" },
  { id: "ref_location", labelRu: "Локация", mode: "CONTEXT", kind: "context", role: "location" },
  { id: "ref_style", labelRu: "Стиль", mode: "CONTEXT", kind: "context", role: "style" },
];

export const NARRATIVE_SOURCE_INPUT_HANDLES = NARRATIVE_INPUT_HANDLES.filter((item) => item.kind === "story_source");
export const NARRATIVE_CONTEXT_INPUT_HANDLES = NARRATIVE_INPUT_HANDLES.filter((item) => item.kind === "context");

const lookupLabel = (options, value, fallback) => options.find((option) => option.value === value)?.labelRu || fallback;

const normalizeText = (value) => String(value || "").trim();
const CLIP_TRACE_SCENARIO_GLOBAL_MUSIC_SYNTH = false;
const SCENARIO_DIRECTOR_FIXTURE_TOGGLE_KEY = "ps:scenarioDirector:forceFixture";

const NARRATIVE_CONTENT_TYPE_POLICY_BY_VALUE = Object.fromEntries(
  NARRATIVE_CONTENT_TYPE_REGISTRY.map((item) => [item.value, item])
);

export function getNarrativeContentTypePolicy(contentType) {
  const normalized = normalizeText(contentType);
  return NARRATIVE_CONTENT_TYPE_POLICY_BY_VALUE[normalized] || NARRATIVE_CONTENT_TYPE_POLICY_BY_VALUE.story;
}

export function isNarrativeContentTypeEnabled(contentType) {
  return getNarrativeContentTypePolicy(contentType)?.isEnabled !== false;
}

export function getSafeNarrativeContentType(contentType, fallbackContentType = "story") {
  const normalized = normalizeText(contentType);
  if (isNarrativeContentTypeEnabled(normalized)) return normalized;
  if (isNarrativeContentTypeEnabled(fallbackContentType)) return fallbackContentType;
  const firstEnabled = NARRATIVE_CONTENT_TYPE_OPTIONS.find((item) => item.isEnabled !== false)?.value;
  return firstEnabled || "story";
}

function isScenarioDirectorFixtureForced() {
  if (typeof window === "undefined") return false;
  const queryRaw = normalizeText(new URLSearchParams(window.location.search).get("scenarioDirectorFixture"));
  if (["1", "true", "yes", "on"].includes(queryRaw.toLowerCase())) return true;
  if (["0", "false", "no", "off"].includes(queryRaw.toLowerCase())) return false;
  const storageRaw = normalizeText(window.localStorage?.getItem(SCENARIO_DIRECTOR_FIXTURE_TOGGLE_KEY));
  return ["1", "true", "yes", "on"].includes(storageRaw.toLowerCase());
}

function getConnectedInputRawSignal(input) {
  if (!input || typeof input !== "object") return "";
  return input.value
    || input.preview
    || input.url
    || input.assetUrl
    || input.fileName
    || input.sourceLabel
    || input?.meta?.label
    || input?.meta?.preview
    || "";
}

function getConnectedInputSignal(input) {
  return normalizeText(getConnectedInputRawSignal(input));
}

function isFirstLastLikeScene(scene = {}) {
  const renderMode = normalizeText(scene?.renderMode || scene?.render_mode).toLowerCase();
  const ltxMode = normalizeText(scene?.ltxMode || scene?.ltx_mode).toLowerCase();
  return renderMode === "first_last"
    || renderMode === "first_last_sound"
    || Boolean(scene?.needsTwoFrames ?? scene?.needs_two_frames)
    || ltxMode === "f_l"
    || ltxMode === "first_last";
}

function deriveFirstLastPrompts(scene = {}) {
  const explicitStart = normalizeText(scene?.startFramePromptRu || scene?.startFramePromptEn || scene?.startFramePrompt || scene?.start_frame_prompt);
  const explicitEnd = normalizeText(scene?.endFramePromptRu || scene?.endFramePromptEn || scene?.endFramePrompt || scene?.end_frame_prompt);
  if (explicitStart && explicitEnd) {
    return { start: explicitStart, end: explicitEnd, derived: false };
  }

  const sceneGoal = normalizeText(scene?.sceneGoal || scene?.scene_goal);
  const frameDescription = normalizeText(scene?.frameDescription || scene?.frame_description);
  const imagePrompt = normalizeText(scene?.imagePromptRu || scene?.imagePromptEn || scene?.imagePrompt || scene?.image_prompt);
  const videoPrompt = normalizeText(scene?.videoPromptRu || scene?.videoPromptEn || scene?.videoPrompt || scene?.video_prompt);
  const transitionType = normalizeText(scene?.transitionType || scene?.transition_type).replaceAll("_", " ") || "state shift";
  const transitionSemantics = videoPrompt || `First→last transition with ${transitionType}.`;

  const start = explicitStart || frameDescription || sceneGoal || imagePrompt || videoPrompt;
  let end = explicitEnd || sceneGoal || imagePrompt || frameDescription || videoPrompt;
  if (end) end = `${end}. Final changed state after transition: ${transitionSemantics}`;
  if (start && end && start === end) end = `${end}. Keep the final frame visually different from the start frame.`;
  return { start, end, derived: true };
}

function normalizeNarrativeSourceMode(mode) {
  const clean = String(mode || "").trim().toUpperCase();
  if (clean === "VIDEO_FILE") return "video_file";
  if (clean === "VIDEO_LINK") return "video_link";
  return "audio";
}


function toAudioDurationSec(value) {
  const numeric = Number(value);
  if (Number.isFinite(numeric) && numeric > 0) return Number(numeric.toFixed(3));
  return 0;
}

function resolveScenarioAudioContext(connectedInputs = {}, resolvedSource = {}) {
  const audioInput = connectedInputs?.audio_in && typeof connectedInputs.audio_in === "object" ? connectedInputs.audio_in : {};
  const meta = audioInput?.meta && typeof audioInput.meta === "object" ? audioInput.meta : {};
  const normalizedSourceMode = normalizeNarrativeSourceMode(resolvedSource?.mode);
  const hasAudioSource = normalizedSourceMode === "audio";
  const audioDurationSec = toAudioDurationSec(
    audioInput?.durationSec
      ?? audioInput?.audioDurationSec
      ?? meta?.durationSec
      ?? meta?.audioDurationSec
      ?? meta?.audio?.durationSec
      ?? 0
  );

  const detectedOrigin = normalizeText(
    audioInput?.origin
      || meta?.origin
      || meta?.sourceOrigin
      || (hasAudioSource ? "audio_node" : "")
  );
  const sourceOrigin = hasAudioSource ? "connected" : normalizeText(resolvedSource?.origin);

  const mimeType = normalizeText(audioInput?.mimeType || meta?.mimeType || meta?.audio?.mimeType);
  const fileName = normalizeText(audioInput?.fileName || meta?.fileName || meta?.audio?.fileName || audioInput?.preview);
  const url = normalizeText(audioInput?.url || meta?.url || meta?.audio?.url || resolvedSource?.value);

  return {
    hasAudioSource,
    audioDurationSec,
    sourceOrigin,
    audioOrigin: detectedOrigin || (hasAudioSource ? "audio_node" : ""),
    mimeType,
    fileName,
    url,
  };
}

function resolveAudioDurationFallback(state = {}) {
  const metadata = state?.metadata && typeof state.metadata === "object" ? state.metadata : {};
  const source = state?.source && typeof state.source === "object" ? state.source : {};
  const sourceMetadata = source?.metadata && typeof source.metadata === "object" ? source.metadata : {};
  const sourceAudioMeta = sourceMetadata?.audio && typeof sourceMetadata.audio === "object" ? sourceMetadata.audio : {};
  const metadataAudio = metadata?.audio && typeof metadata.audio === "object" ? metadata.audio : {};

  return toAudioDurationSec(
    state?.audioDurationSec
      ?? state?.audioContext?.audioDurationSec
      ?? source?.audioDurationSec
      ?? sourceMetadata?.audioDurationSec
      ?? sourceAudioMeta?.durationSec
      ?? metadata?.audioDurationSec
      ?? metadataAudio?.durationSec
      ?? 0
  );
}

function buildReferencePayload(input, fallbackLabel) {
  if (!input || typeof input !== "object") return null;
  const normalizedRefs = Array.isArray(input.refs)
    ? input.refs
      .map((item) => {
        if (typeof item === "string") {
          const url = normalizeText(item);
          return url ? { url, roleType: "" } : null;
        }
        const url = normalizeText(item?.url || item);
        if (!url) return null;
        const roleType = normalizeText(item?.roleType).toLowerCase();
        return { url, roleType };
      })
      .filter(Boolean)
    : [];
  const refs = normalizedRefs.map((item) => item.url).filter(Boolean);
  const roleType = normalizeText(input?.roleType || normalizedRefs.find((item) => !!item.roleType)?.roleType).toLowerCase();
  const value = normalizeText(input.value) || normalizeText(refs[0]) || "";
  if (!value && !refs.length && !normalizeText(input.preview)) return null;
  const meta = input?.meta && typeof input.meta === "object" ? { ...input.meta } : {};
  if (roleType) meta.roleType = roleType;
  return {
    label: fallbackLabel,
    source_label: normalizeText(input.sourceLabel) || fallbackLabel,
    preview: normalizeText(input.preview) || normalizeText(input.fileName) || value,
    value,
    refs,
    count: Math.max(Number(input.count) || 0, refs.length || (value ? 1 : 0)),
    meta,
  };
}

function toStoryboardNumericSec(value, fallback = 0) {
  const direct = Number(value);
  if (Number.isFinite(direct)) return direct;
  const match = String(value || "").match(/-?\d+(?:\.\d+)?/);
  if (match) {
    const parsed = Number(match[0]);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function participantsToActors(participants = []) {
  return (Array.isArray(participants) ? participants : [])
    .map((item) => normalizeText(item))
    .filter(Boolean)
    .map((item, index) => toCanonicalRoleId(item, index));
}

function needsTwoFramesForMode(ltxMode = "") {
  return ["f_l"].includes(String(ltxMode || "").trim());
}

function isContinuationMode(ltxMode = "") {
  return String(ltxMode || "").trim() === "continuation";
}

function getConnectedInputCount(input) {
  const safeCount = Number(input?.count || input?.meta?.count || 0);
  if (Number.isFinite(safeCount) && safeCount > 0) return safeCount;
  if (Array.isArray(input?.refs)) return input.refs.filter(Boolean).length;
  return getConnectedInputSignal(input) ? 1 : 0;
}

const splitEntities = (text) => normalizeText(text)
  .split(/[,.!?:;\n]+/)
  .map((item) => item.trim())
  .filter(Boolean)
  .slice(0, 6);

function normalizeMusicPromptPart(value) {
  if (Array.isArray(value)) return value.map((item) => normalizeMusicPromptPart(item)).filter(Boolean).join(", ");
  if (value && typeof value === "object") return "";
  return normalizeText(value);
}

export function buildGlobalMusicPromptFromStructuredMusic(music = null) {
  const source = music && typeof music === "object" ? music : null;
  if (!source) return "";

  const mood = normalizeMusicPromptPart(source?.mood ?? source?.musicMood ?? source?.music_mood);
  const style = normalizeMusicPromptPart(source?.style ?? source?.musicStyle ?? source?.music_style);
  const pacingHints = normalizeMusicPromptPart(source?.pacingHints ?? source?.pacing_hints ?? source?.pacing);
  const genre = normalizeMusicPromptPart(source?.genre);
  const energy = normalizeMusicPromptPart(source?.energy);
  const instrumentation = normalizeMusicPromptPart(source?.instrumentation ?? source?.instruments);

  const parts = [
    mood ? `Mood: ${mood}.` : "",
    style ? `Style: ${style}.` : "",
    pacingHints ? `Pacing: ${pacingHints}.` : "",
    genre ? `Genre: ${genre}.` : "",
    energy ? `Energy: ${energy}.` : "",
    instrumentation ? `Instrumentation: ${instrumentation}.` : "",
  ].filter(Boolean);
  return parts.join(" ").trim();
}

function resolveDirectorGlobalMusicPrompt(response = {}, storyboardOut = null, directorOutput = null, state = {}) {
  const controls = response?.director_controls && typeof response.director_controls === "object" ? response.director_controls : {};
  const requestedContentType = normalizeText(
    response?.contentType
    ?? controls?.contentType
    ?? directorOutput?.contentType
    ?? storyboardOut?.contentType
    ?? state?.contentType
    ?? "story"
  ) || "story";
  const contentType = getSafeNarrativeContentType(requestedContentType);
  const contentTypePolicy = getNarrativeContentTypePolicy(contentType);
  const hasMasterAudioSource = Boolean(
    state?.audioContext?.hasAudioSource
    || normalizeNarrativeSourceMode(state?.resolvedSource?.mode) === "audio"
    || normalizeNarrativeSourceMode(resolveNarrativeSource(state)?.mode) === "audio"
  );
  const flatPrompt = normalizeText(
    response?.globalMusicPrompt
    ?? response?.bgMusicPrompt
    ?? response?.music_prompt
    ?? storyboardOut?.globalMusicPrompt
    ?? storyboardOut?.music_prompt
    ?? storyboardOut?.bgMusicPrompt
    ?? directorOutput?.music?.globalMusicPrompt
    ?? directorOutput?.music?.music_prompt
    ?? directorOutput?.music_prompt
    ?? directorOutput?.globalMusicPrompt
  );
  const structuredMusic = (
    (response?.directorOutput?.music && typeof response.directorOutput.music === "object" ? response.directorOutput.music : null)
    || (directorOutput?.music && typeof directorOutput.music === "object" ? directorOutput.music : null)
    || (response?.music && typeof response.music === "object" ? response.music : null)
    || (storyboardOut?.music && typeof storyboardOut.music === "object" ? storyboardOut.music : null)
    || null
  );
  const shouldSkipStructuredFallback = Boolean(structuredMusic?.__isDerivedFallback);
  const shouldDisableFallbackByPolicy = !contentTypePolicy?.usesGlobalMusicPrompt
    || (contentType === "trailer" && hasMasterAudioSource);
  const synthesizedPrompt = (flatPrompt || shouldSkipStructuredFallback || shouldDisableFallbackByPolicy)
    ? ""
    : buildGlobalMusicPromptFromStructuredMusic(structuredMusic);
  const resolvedPrompt = flatPrompt || synthesizedPrompt;
  if (CLIP_TRACE_SCENARIO_GLOBAL_MUSIC_SYNTH) {
    console.debug("[SCENARIO GLOBAL MUSIC SYNTH]", {
      hasFlatPrompt: !!flatPrompt,
      hasStructuredMusic: !!structuredMusic,
      contentType,
      usesGlobalMusicPrompt: !!contentTypePolicy?.usesGlobalMusicPrompt,
      hasMasterAudioSource,
      mood: normalizeMusicPromptPart(structuredMusic?.mood ?? structuredMusic?.musicMood ?? structuredMusic?.music_mood),
      style: normalizeMusicPromptPart(structuredMusic?.style ?? structuredMusic?.musicStyle ?? structuredMusic?.music_style),
      hasPacingHints: !!normalizeMusicPromptPart(structuredMusic?.pacingHints ?? structuredMusic?.pacing_hints ?? structuredMusic?.pacing),
      synthesizedPromptLength: synthesizedPrompt.length,
    });
  }
  return resolvedPrompt;
}

const buildSceneWindow = (index, totalScenes) => {
  const safeIndex = Number(index) || 0;
  const safeTotal = Math.max(1, Number(totalScenes) || 1);
  const duration = safeIndex === safeTotal - 1 ? 6 : 5;
  const timeStart = safeIndex * 5;
  const timeEnd = timeStart + duration;
  return {
    durationSec: duration,
    timeStart,
    timeEnd,
    timeStartLabel: `${timeStart}s`,
    timeEndLabel: `${timeEnd}s`,
    durationLabel: `${duration}s`,
  };
};

export function summarizeNarrativeConnectedContext(state = {}) {
  const connectedInputs = state?.connectedInputs && typeof state.connectedInputs === "object" ? state.connectedInputs : {};
  const resolvedSource = resolveNarrativeSource(state);
  const connectedRefsByRole = {
    character_1: getConnectedInputCount(connectedInputs?.ref_character_1) > 0 ? [getConnectedInputSignal(connectedInputs?.ref_character_1)].filter(Boolean) : [],
    character_2: getConnectedInputCount(connectedInputs?.ref_character_2) > 0 ? [getConnectedInputSignal(connectedInputs?.ref_character_2)].filter(Boolean) : [],
    character_3: getConnectedInputCount(connectedInputs?.ref_character_3) > 0 ? [getConnectedInputSignal(connectedInputs?.ref_character_3)].filter(Boolean) : [],
    props: getConnectedInputCount(connectedInputs?.ref_props) > 0 ? [getConnectedInputSignal(connectedInputs?.ref_props)].filter(Boolean) : [],
    location: getConnectedInputCount(connectedInputs?.ref_location) > 0 ? [getConnectedInputSignal(connectedInputs?.ref_location)].filter(Boolean) : [],
    style: getConnectedInputCount(connectedInputs?.ref_style) > 0 ? [getConnectedInputSignal(connectedInputs?.ref_style)].filter(Boolean) : [],
  };
  const presentCastRoles = Object.entries(connectedRefsByRole)
    .filter(([role, refs]) => refs.length > 0 && !["props", "location", "style"].includes(role))
    .map(([role]) => role);
  const presentWorldRoles = Object.entries(connectedRefsByRole)
    .filter(([role, refs]) => refs.length > 0 && ["props", "location", "style"].includes(role))
    .map(([role]) => role);
  const sourceByHandle = {
    audio_in: getConnectedInputCount(connectedInputs?.audio_in) > 0,
    video_file_in: getConnectedInputCount(connectedInputs?.video_file_in) > 0,
    video_link_in: getConnectedInputCount(connectedInputs?.video_link_in) > 0,
  };

  return {
    activeSourceMode: resolvedSource?.mode || null,
    activeSourceLabel: resolvedSource?.label || "Источник не подключён",
    hasActiveSource: resolvedSource?.origin === "connected" && !!normalizeText(resolvedSource?.value),
    sourceByHandle,
    characterCount: presentCastRoles.length,
    hasProps: presentWorldRoles.includes("props"),
    hasLocation: presentWorldRoles.includes("location"),
    hasStyle: presentWorldRoles.includes("style"),
    connectedRoleIds: [
      ...(Array.isArray(presentCastRoles) ? presentCastRoles : []),
      ...(Array.isArray(presentWorldRoles) ? presentWorldRoles : []),
    ],
    presentCastRoles,
    presentWorldRoles,
    refsPresentByRole: connectedRefsByRole,
    connectedRefsPresentByRole: connectedRefsByRole,
  };
}

export function getDefaultNarrativeNodeData() {
  return {
    sourceOrigin: "disconnected",
    contentType: "story",
    narrativeMode: "cinematic_expand",
    styleProfile: "realistic",
    format: "9:16",
    directorNote: "",
    connectedInputs: {
      audio_in: null,
      video_file_in: null,
      video_link_in: null,
      ref_character_1: null,
      ref_character_2: null,
      ref_character_3: null,
      ref_props: null,
      ref_location: null,
      ref_style: null,
    },
    resolvedSource: {
      mode: null,
      origin: "disconnected",
      value: "",
      label: "Источник не подключён",
      sourceLabel: "Ожидается внешний источник",
      preview: "",
    },
    error: null,
    isGenerating: false,
    activeResultTab: "history",
    pendingOutputs: null,
    pendingGeneratedAt: "",
    confirmedAt: "",
    outputs: {
      storyboardOut: null,
      scenario: "",
      voiceScript: "",
      brainPackage: null,
      bgMusicPrompt: "",
      directorOutput: null,
    },
  };
}

export function resolveNarrativeSource(state = {}) {
  const connectedInputs = state?.connectedInputs && typeof state.connectedInputs === "object" ? state.connectedInputs : {};
  const connectedOption = NARRATIVE_SOURCE_INPUT_HANDLES.find((item) => getConnectedInputSignal(connectedInputs?.[item.id]));

  if (!connectedOption) {
    return {
      mode: null,
      origin: "disconnected",
      value: "",
      label: "Источник не подключён",
      sourceLabel: "Ожидается внешний источник",
      preview: "",
    };
  }

  const connectedSource = connectedInputs[connectedOption.id] || null;
  const connectedValue = getConnectedInputSignal(connectedSource);
  const modeLabel = lookupLabel(NARRATIVE_SOURCE_OPTIONS, connectedOption.mode, "Аудио");
  const connectedPreview = normalizeText(connectedSource?.preview)
    || normalizeText(connectedSource?.fileName)
    || connectedValue;
  const connectedSourceLabel = normalizeText(connectedSource?.sourceLabel)
    || normalizeText(connectedSource?.fileName)
    || `Подключённый источник (${modeLabel.toLowerCase()})`;

  return {
    mode: connectedOption.mode,
    origin: connectedValue ? "connected" : "disconnected",
    value: connectedValue,
    label: modeLabel,
    sourceLabel: connectedSourceLabel,
    preview: connectedPreview,
  };
}

export function buildScenarioDirectorRequestPayload(state = {}) {
  const resolvedSource = resolveNarrativeSource(state);
  const sourceValue = normalizeText(resolvedSource.value);
  if (!sourceValue) return null;

  const connectedInputs = state?.connectedInputs && typeof state.connectedInputs === "object" ? state.connectedInputs : {};
  const connectedContextSummary = summarizeNarrativeConnectedContext({ ...state, resolvedSource });
  const audioContextRaw = resolveScenarioAudioContext(connectedInputs, resolvedSource);
  const persistedAudioDurationSec = resolveAudioDurationFallback(state);
  const effectiveAudioDurationSec = toAudioDurationSec(audioContextRaw.audioDurationSec || persistedAudioDurationSec);
  const wasDurationResolved = effectiveAudioDurationSec > 0;
  const audioContext = {
    ...audioContextRaw,
    audioDurationSec: effectiveAudioDurationSec,
  };
  const safeContentType = getSafeNarrativeContentType(state?.contentType, "music_video");
  const isMusicVideo = safeContentType === "music_video";
  const preferAudioOverText = audioContext.hasAudioSource;
  const contextRefs = {
    character_1: buildReferencePayload(connectedInputs?.ref_character_1, "Character 1"),
    character_2: buildReferencePayload(connectedInputs?.ref_character_2, "Character 2"),
    character_3: buildReferencePayload(connectedInputs?.ref_character_3, "Character 3"),
    props: buildReferencePayload(connectedInputs?.ref_props, "Props"),
    location: buildReferencePayload(connectedInputs?.ref_location, "Location"),
    style: buildReferencePayload(connectedInputs?.ref_style, "Style"),
  };
  const roleTypeByRole = Object.fromEntries(
    Object.entries(contextRefs)
      .map(([role, value]) => [role, normalizeText(value?.meta?.roleType).toLowerCase()])
      .filter(([, roleType]) => !!roleType)
  );

  const format = NARRATIVE_FORMAT_OPTIONS.includes(String(state?.format || "").trim())
    ? String(state.format).trim()
    : "9:16";

  const payload = {
    mode: isMusicVideo ? "clip_pipeline" : "oneshot",
    directGeminiStoryboardMode: true,
    direct_gemini_storyboard_mode: true,
    source: {
      source_mode: normalizeNarrativeSourceMode(resolvedSource.mode),
      source_value: sourceValue,
      source_preview: normalizeText(resolvedSource.preview) || sourceValue,
      source_label: normalizeText(resolvedSource.sourceLabel) || normalizeText(resolvedSource.label) || "Source of truth",
      source_origin: audioContext.sourceOrigin || normalizeText(resolvedSource.origin) || "connected",
      audioDurationSec: audioContext.audioDurationSec,
      mimeType: audioContext.mimeType,
      fileName: audioContext.fileName,
      metadata: {
        origin: normalizeText(resolvedSource.origin) || "connected",
        label: normalizeText(resolvedSource.label),
        connectedHandle: Object.entries(connectedInputs).find(([, value]) => value && normalizeText(value.value) === sourceValue)?.[0] || "",
        activeSourceMode: connectedContextSummary.activeSourceMode || null,
        audio: {
          durationSec: audioContext.audioDurationSec,
          mimeType: audioContext.mimeType,
          fileName: audioContext.fileName,
          url: audioContext.url || sourceValue,
          origin: audioContext.audioOrigin || "audio_node",
        },
      },
    },
    context_refs: Object.fromEntries(Object.entries(contextRefs).filter(([, value]) => !!value)),
    source_origin: audioContext.sourceOrigin || normalizeText(resolvedSource.origin) || "connected",
    audioDurationSec: audioContext.audioDurationSec,
    director_controls: {
      contentType: safeContentType,
      format,
      preferAudioOverText,
    },
    connected_context_summary: connectedContextSummary,
    metadata: {
      sourcePreview: normalizeText(resolvedSource.preview) || sourceValue,
      sourceLabel: normalizeText(resolvedSource.sourceLabel) || normalizeText(resolvedSource.label),
      directGeminiStoryboardMode: true,
      direct_gemini_storyboard_mode: true,
      ...(isMusicVideo ? { pipelineMode: "clip_pipeline_v1", useClipStoryboardPipeline: true } : {}),
      fileOrLinkMeta: connectedInputs?.video_file_in?.meta || connectedInputs?.video_link_in?.meta || connectedInputs?.audio_in?.meta || {},
      roleTypeByRole,
      audio: {
        durationSec: audioContext.audioDurationSec,
        mimeType: audioContext.mimeType,
        fileName: audioContext.fileName,
        url: audioContext.url || sourceValue,
        origin: audioContext.audioOrigin || "audio_node",
      },
      format,
    },
  };

  const forceFixture = isScenarioDirectorFixtureForced();
  if (forceFixture) {
    payload.metadata.forceLocalDeterministicFixture = true;
    payload.metadata.fixtureDebugToggle = "frontend_query_or_localstorage";
    console.warn("[ScenarioDirector] DEBUG FIXTURE MODE enabled (forceLocalDeterministicFixture=true)");
  }

  if (audioContext.hasAudioSource) {
    console.debug("[ScenarioDirector] audio payload context", {
      sourceMode: normalizeNarrativeSourceMode(resolvedSource.mode),
      audioUrl: audioContext.url || sourceValue,
      source_origin: audioContext.sourceOrigin || normalizeText(resolvedSource.origin) || "connected",
      metadataAudioOrigin: audioContext.audioOrigin || "audio_node",
      audioDurationSec: audioContext.audioDurationSec,
      wasDurationResolved,
      persistedAudioDurationSec,
    });
  }

  return payload;
}

export function buildScenarioStageManualPayload({
  sourceState = {},
  targetState = {},
  stageId = "",
  autoRun = false,
  storyboardPackage = {},
  requestSource = "scenario_storyboard:manual_stage",
} = {}) {
  const basePayload = buildScenarioDirectorRequestPayload(sourceState) || {};
  const source = sourceState && typeof sourceState === "object" ? sourceState : {};
  const target = targetState && typeof targetState === "object" ? targetState : {};
  const directorOutput = target?.directorOutput && typeof target.directorOutput === "object" ? target.directorOutput : {};
  const existingStoryboardPackage = (
    storyboardPackage && typeof storyboardPackage === "object" && Object.keys(storyboardPackage).length
  ) ? storyboardPackage : (target?.storyboardPackage && typeof target.storyboardPackage === "object" ? target.storyboardPackage : {});

  return {
    ...basePayload,
    mode: "scenario_stage",
    pipelineMode: "scenario_stage_v1",
    stageId: normalizeText(stageId),
    autoRun: Boolean(autoRun),
    storyboardPackage: existingStoryboardPackage,
    text: normalizeText(source?.text || target?.text || basePayload?.text),
    storyText: normalizeText(source?.storyText || target?.storyText),
    note: normalizeText(source?.note || source?.storyText || target?.note || target?.storyText),
    directorNote: normalizeText(source?.directorNote || target?.directorNote),
    audioUrl: normalizeText(source?.audioUrl || source?.masterAudioUrl || target?.audioUrl || basePayload?.audioUrl),
    audioDurationSec: Number(source?.audioDurationSec || target?.audioDurationSec || basePayload?.audioDurationSec || 0) || 0,
    source: source?.resolvedSource && typeof source.resolvedSource === "object" ? source.resolvedSource : (basePayload?.source || {}),
    context_refs: source?.connectedInputs && typeof source.connectedInputs === "object" ? source.connectedInputs : (basePayload?.context_refs || {}),
    connected_context_summary: (
      source?.connected_context_summary && typeof source.connected_context_summary === "object"
    ) ? source.connected_context_summary : (basePayload?.connected_context_summary || {}),
    director_controls: {
      ...(basePayload?.director_controls && typeof basePayload.director_controls === "object" ? basePayload.director_controls : {}),
      ...(source?.director_controls && typeof source.director_controls === "object" ? source.director_controls : {}),
      contentType: normalizeText(source?.contentType || target?.scenarioMode || basePayload?.director_controls?.contentType) || "music_video",
      format: normalizeText(source?.format || target?.format || basePayload?.director_controls?.format) || "9:16",
    },
    contentType: normalizeText(source?.contentType || target?.scenarioMode || basePayload?.director_controls?.contentType) || "music_video",
    format: normalizeText(source?.format || target?.format || basePayload?.director_controls?.format) || "9:16",
    roleTypeByRole: source?.roleTypeByRole && typeof source.roleTypeByRole === "object" ? source.roleTypeByRole : (basePayload?.metadata?.roleTypeByRole || {}),
    refsByRole: source?.refsByRole && typeof source.refsByRole === "object" ? source.refsByRole : {},
    selectedCharacterRefUrl: normalizeText(source?.selectedCharacterRefUrl),
    selectedStyleRefUrl: normalizeText(source?.selectedStyleRefUrl),
    selectedLocationRefUrl: normalizeText(source?.selectedLocationRefUrl),
    selectedPropsRefUrls: Array.isArray(source?.selectedPropsRefUrls) ? source.selectedPropsRefUrls : [],
    master_output: source?.master_output && typeof source.master_output === "object" ? source.master_output : {},
    timeWindow: source?.timeWindow && typeof source.timeWindow === "object" ? source.timeWindow : {},
    options: source?.options && typeof source.options === "object" ? source.options : {},
    metadata: {
      ...(basePayload?.metadata && typeof basePayload.metadata === "object" ? basePayload.metadata : {}),
      ...(source?.metadata && typeof source.metadata === "object" ? source.metadata : {}),
      pipelineMode: "scenario_stage_v1",
      requestSource: normalizeText(requestSource) || "scenario_storyboard:manual_stage",
      contentType: normalizeText(source?.contentType || target?.scenarioMode) || "music_video",
      format: normalizeText(source?.format || target?.format) || "9:16",
    },
    scenario: target?.scenario || source?.scenario || "",
    scenarioPackage: target?.scenarioPackage && typeof target.scenarioPackage === "object" ? target.scenarioPackage : {},
    storyboardOut: target?.storyboardOut && typeof target.storyboardOut === "object" ? target.storyboardOut : {},
    directorOutput: directorOutput,
  };
}

function toCanonicalRoleId(value, fallbackIndex = -1) {
  const clean = normalizeText(value);
  const canonical = clean.toLowerCase().replace(/\s+/g, "_");
  if (/^character_[1-9]\d*$/i.test(canonical)) return canonical;
  if (["animal", "animal_1", "group", "group_faces", "props", "location", "style"].includes(canonical)) return canonical;
  if (/^character[1-9]\d*$/i.test(canonical)) return canonical.replace(/^character/i, "character_");
  if (Number.isInteger(fallbackIndex) && fallbackIndex >= 0 && fallbackIndex < 3) return `character_${fallbackIndex + 1}`;
  return canonical;
}

function toHumanizedCanonicalRole(roleId) {
  const canonical = normalizeText(roleId).toLowerCase();
  if (canonical === "character_1") return "Персонаж 1";
  if (canonical === "character_2") return "Персонаж 2";
  if (canonical === "character_3") return "Персонаж 3";
  if (canonical === "animal" || canonical === "animal_1") return "Животное";
  if (canonical === "group" || canonical === "group_faces") return "Группа / совместный кадр";
  if (canonical === "props") return "Предметы";
  if (canonical === "location") return "Локация";
  if (canonical === "style") return "Стиль";
  return canonical || "Персонаж";
}

export function mapStoryboardOutToDirectorOutput(storyboardOut = null, state = {}) {
  if (!storyboardOut || typeof storyboardOut !== "object") return null;
  const scenes = Array.isArray(storyboardOut.scenes) ? storyboardOut.scenes : [];
  const contextRefs = storyboardOut?.context_refs && typeof storyboardOut.context_refs === "object" ? storyboardOut.context_refs : {};
  const refsByRole = storyboardOut?.refsByRole && typeof storyboardOut.refsByRole === "object" ? storyboardOut.refsByRole : {};
  const connectedRefsByRole = storyboardOut?.connectedRefsByRole && typeof storyboardOut.connectedRefsByRole === "object" ? storyboardOut.connectedRefsByRole : {};
  const connectedSummary = storyboardOut?.connected_context_summary && typeof storyboardOut.connected_context_summary === "object" ? storyboardOut.connected_context_summary : {};
  const connectedSummaryRefs = connectedSummary?.refsByRole && typeof connectedSummary.refsByRole === "object" ? connectedSummary.refsByRole : {};
  const roleIds = [];
  const pushRole = (value) => {
    const normalized = toCanonicalRoleId(value);
    if (!normalized || roleIds.includes(normalized)) return;
    roleIds.push(normalized);
  };
  scenes.forEach((scene, index) => {
    (Array.isArray(scene.actors) ? scene.actors : []).forEach((actor, actorIndex) => pushRole(toCanonicalRoleId(actor, actorIndex)));
    ["primaryRole", "primary_role"].forEach((key) => pushRole(scene?.[key]));
    ["secondaryRoles", "secondary_roles", "sceneActiveRoles", "scene_active_roles", "mustAppear", "must_appear", "refsUsed", "refs_used"].forEach((key) => {
      const values = Array.isArray(scene?.[key]) ? scene[key] : [];
      values.forEach((value) => pushRole(value));
    });
    if (normalizeText(scene.location)) pushRole("location");
    if (Array.isArray(scene.props) && scene.props.some((item) => normalizeText(item))) pushRole("props");
  });
  [contextRefs, refsByRole, connectedRefsByRole, connectedSummaryRefs].forEach((source) => Object.keys(source || {}).forEach(pushRole));
  const roleLabels = Object.fromEntries(roleIds.map((role) => [role, toHumanizedCanonicalRole(role)]));
  const castRoles = roleIds.filter((role) => !["props", "location", "style"].includes(role));
  const worldRoles = roleIds.filter((role) => ["props", "location", "style"].includes(role));
  const buildRoleCopy = (role) => {
    if (role === "character_1") return "Главный герой / главный носитель действия";
    if (role === "character_2") return "Партнёр по сцене / вторичный акцент";
    if (role === "character_3") return "Поддерживающий персонаж или смысловой объект";
    if (["animal", "animal_1", "group", "group_faces"].includes(role)) return "Поддерживающий участник кадра";
    if (["props", "location", "style"].includes(role)) return "Мировой контекст / world anchor";
    return "Поддерживающая роль";
  };
  const history = {
    summary: normalizeText(storyboardOut.story_summary),
    fullScenario: normalizeText(storyboardOut.full_scenario),
    characterRoles: castRoles.map((role) => ({
      name: role,
      displayName: roleLabels[role],
      role: buildRoleCopy(role),
    })),
    toneStyleDirection: normalizeText(state.styleProfile) || "realistic",
    directorSummary: normalizeText(storyboardOut.director_summary),
    presentCastRoles: castRoles,
    presentWorldRoles: worldRoles,
    hasProps: worldRoles.includes("props"),
    hasLocation: worldRoles.includes("location"),
    hasStyle: worldRoles.includes("style"),
  };
  const globalMusicPrompt = normalizeText(
    storyboardOut?.globalMusicPrompt
    ?? storyboardOut?.music_prompt
    ?? storyboardOut?.bgMusicPrompt
  );
  const hasRealGlobalMusicPrompt = Boolean(globalMusicPrompt);
  const normalizedScenes = scenes.map((scene, index) => {
    const ltxMode = normalizeText(scene.ltx_mode) || "i2v";
    return {
      sceneId: normalizeText(scene.scene_id) || `S${index + 1}`,
      title: normalizeText(scene.scene_id) || `S${index + 1}`,
      timeStart: toStoryboardNumericSec(scene.time_start, index * 5),
      timeEnd: toStoryboardNumericSec(scene.time_end, (index + 1) * 5),
      duration: toStoryboardNumericSec(scene.duration, 5),
      participants: (Array.isArray(scene.actors) ? scene.actors : []).map((actor, actorIndex) => toCanonicalRoleId(actor, actorIndex)).filter(Boolean),
      location: normalizeText(scene.location),
      props: Array.isArray(scene.props) ? scene.props.map((item) => normalizeText(item)).filter(Boolean) : [],
      action: normalizeText(scene.action_in_frame),
      emotion: normalizeText(scene.emotion),
      sceneGoal: normalizeText(scene.scene_goal),
      frameDescription: normalizeText(scene.frame_description),
      actionInFrame: normalizeText(scene.action_in_frame),
      cameraIdea: normalizeText(scene.camera),
      imagePrompt: normalizeText(scene.image_prompt),
      videoPrompt: normalizeText(scene.video_prompt),
      videoNegativePrompt: normalizeText(scene.video_negative_prompt ?? scene.videoNegativePrompt),
      ltxMode,
      whyThisMode: normalizeText(scene.ltx_reason),
      renderMode: normalizeText(scene.render_mode ?? scene.renderMode) || "image_video",
      resolvedWorkflowKey: normalizeText(scene.resolved_workflow_key ?? scene.resolvedWorkflowKey) || "i2v",
      sourceRoute: normalizeText(scene.sourceRoute ?? scene.source_route),
      videoGenerationRoute: normalizeText(scene.video_generation_route ?? scene.videoGenerationRoute),
      plannedVideoGenerationRoute: normalizeText(scene.planned_video_generation_route ?? scene.plannedVideoGenerationRoute),
      resolvedWorkflowFile: normalizeText(scene.resolved_workflow_file ?? scene.resolvedWorkflowFile),
      audioSliceKind: normalizeText(scene.audio_slice_kind ?? scene.audioSliceKind),
      musicVocalLipSyncAllowed: Boolean(scene.music_vocal_lipsync_allowed ?? scene.musicVocalLipSyncAllowed),
      scenePurpose: normalizeText(scene.scene_purpose ?? scene.scenePurpose),
      viewerHook: normalizeText(scene.viewer_hook ?? scene.viewerHook),
      startFrameSource: normalizeText(scene.start_frame_source) || "new",
      needsTwoFrames: Boolean(scene.needs_two_frames),
      continuation: Boolean(scene.continuation_from_previous),
      transitionType: normalizeText(scene.transition_type ?? scene.transitionType) || "cut",
      shotType: normalizeText(scene.shot_type ?? scene.shotType) || "",
      requestedDurationSec: toStoryboardNumericSec(scene.requested_duration_sec ?? scene.requestedDurationSec, toStoryboardNumericSec(scene.duration, 5)),
      narrationMode: normalizeText(scene.narration_mode) || "full",
      localPhrase: scene.local_phrase ? normalizeText(scene.local_phrase) : null,
      sfx: normalizeText(scene.sfx),
      soundNotes: normalizeText(scene.sfx),
      pauseDuckSilenceNotes: "",
      musicMixHint: normalizeText(scene.music_mix_hint) || "off",
      lipSync: Boolean(scene.lip_sync ?? scene.lipSync),
      lipSyncText: normalizeText(scene.lip_sync_text ?? scene.lipSyncText),
      sendAudioToGenerator: Boolean(scene.send_audio_to_generator ?? scene.sendAudioToGenerator),
      audioSliceStartSec: toStoryboardNumericSec(scene.audio_slice_start_sec ?? scene.audioSliceStartSec, 0),
      audioSliceEndSec: toStoryboardNumericSec(scene.audio_slice_end_sec ?? scene.audioSliceEndSec, 0),
      audioSliceExpectedDurationSec: toStoryboardNumericSec(scene.audio_slice_expected_duration_sec ?? scene.audioSliceExpectedDurationSec, 0),
      performanceFraming: normalizeText(scene.performance_framing ?? scene.performanceFraming),
      clipDecisionReason: normalizeText(scene.clip_decision_reason ?? scene.clipDecisionReason),
      workflowDecisionReason: normalizeText(scene.workflow_decision_reason ?? scene.workflowDecisionReason),
      lipSyncDecisionReason: normalizeText(scene.lip_sync_decision_reason ?? scene.lipSyncDecisionReason),
      audioSliceDecisionReason: normalizeText(scene.audio_slice_decision_reason ?? scene.audioSliceDecisionReason),
    };
  });
  return {
    history,
    scenes: normalizedScenes,
    video: normalizedScenes.map((scene) => ({
      sceneId: scene.sceneId,
      frameDescription: scene.frameDescription,
      actionInFrame: scene.actionInFrame,
      cameraIdea: scene.cameraIdea,
      imagePrompt: scene.imagePrompt,
      videoPrompt: scene.videoPrompt,
      videoNegativePrompt: scene.videoNegativePrompt,
      ltxMode: scene.ltxMode,
      whyThisMode: scene.whyThisMode,
      renderMode: scene.renderMode,
      resolvedWorkflowKey: scene.resolvedWorkflowKey,
      startFrameSource: scene.startFrameSource,
      needsTwoFrames: scene.needsTwoFrames,
      continuation: scene.continuation,
      transitionType: scene.transitionType,
      shotType: scene.shotType,
      requestedDurationSec: scene.requestedDurationSec,
    })),
    sound: normalizedScenes.map((scene) => ({
      sceneId: scene.sceneId,
      narrationMode: scene.narrationMode,
      localPhrase: scene.localPhrase,
      sfx: scene.sfx,
      soundNotes: scene.soundNotes,
      pauseDuckSilenceNotes: scene.pauseDuckSilenceNotes,
      lipSync: scene.lipSync,
      lipSyncText: scene.lipSyncText,
      sendAudioToGenerator: scene.sendAudioToGenerator,
      audioSliceStartSec: scene.audioSliceStartSec,
      audioSliceEndSec: scene.audioSliceEndSec,
      audioSliceExpectedDurationSec: scene.audioSliceExpectedDurationSec,
    })),
    music: {
      globalMusicPrompt,
      mood: normalizeText(state.styleProfile) || "realistic",
      style: `${getSafeNarrativeContentType(state?.contentType)} / ${normalizeText(state.styleProfile) || "realistic"}`,
      pacingHints: "Use the approved storyboard_out pacing when Storyboard executes the scenes.",
      __isDerivedFallback: !hasRealGlobalMusicPrompt,
    },
    globalMusicPrompt,
    refsByRole,
    connectedRefsByRole,
    contentTypePolicy: getNarrativeContentTypePolicy(getSafeNarrativeContentType(state?.contentType)),
  };
}

function mapCompactDirectorResponseToStoryboardOut(compactResponse = {}) {
  const inputUnderstanding = compactResponse?.input_understanding && typeof compactResponse.input_understanding === "object"
    ? compactResponse.input_understanding
    : {};
  const storyboard = compactResponse?.storyboard && typeof compactResponse.storyboard === "object"
    ? compactResponse.storyboard
    : {};
  const compactScenes = Array.isArray(storyboard?.scenes) ? storyboard.scenes : [];
  if (!compactScenes.length) return null;
  const continuityLock = Boolean(inputUnderstanding?.same_character_across_all_scenes);
  const identityFields = continuityLock
    ? [
      "face_identity",
      "hair_identity",
      "garment_identity",
      "body_identity",
      "makeup_identity",
      "accessory_identity",
      "age_consistency",
      "color_identity",
    ]
    : [];
  const scenes = compactScenes.map((scene, index) => {
    const startSec = Number(scene?.start_time_sec ?? 0) || 0;
    const endSecRaw = Number(scene?.end_time_sec ?? startSec) || startSec;
    const endSec = endSecRaw < startSec ? startSec : endSecRaw;
    const routeRaw = normalizeText(scene?.route).toLowerCase();
    const resolvedWorkflowKey = ["lip_sync_music", "lip_sync"].includes(routeRaw)
      ? "lip_sync_music"
      : ["f_l", "first_last", "first-last"].includes(routeRaw)
        ? "f_l"
        : "i2v";
    const isLipSync = resolvedWorkflowKey === "lip_sync_music";
    const needsTwoFrames = resolvedWorkflowKey === "f_l";
    const description = normalizeText(scene?.description);
    const contentTags = Array.isArray(scene?.content_tags) ? scene.content_tags.map((tag) => normalizeText(tag)).filter(Boolean) : [];
    return {
      scene_id: normalizeText(scene?.scene_id) || `S${index + 1}`,
      time_start: startSec,
      time_end: endSec,
      duration: Math.max(0, endSec - startSec),
      requested_duration_sec: Math.max(0, endSec - startSec),
      scene_goal: description,
      frame_description: description || "Performance-led visual beat aligned to audio.",
      action_in_frame: description || "Performer follows the current music phrase.",
      camera: "medium shot, stable cinematic camera",
      what_from_audio_this_scene_uses: description,
      shot_type: contentTags[0] || "medium",
      performance_framing: contentTags.slice(0, 3).join(", "),
      local_phrase: description,
      render_mode: "image_video",
      resolved_workflow_key: resolvedWorkflowKey,
      video_generation_route: resolvedWorkflowKey,
      planned_video_generation_route: resolvedWorkflowKey,
      ltx_mode: isLipSync ? resolvedWorkflowKey : (needsTwoFrames ? "f_l" : "i2v"),
      lip_sync: isLipSync,
      send_audio_to_generator: isLipSync,
      music_vocal_lipsync_allowed: isLipSync,
      needs_two_frames: needsTwoFrames,
      audio_slice_start_sec: isLipSync ? startSec : 0,
      audio_slice_end_sec: isLipSync ? endSec : 0,
      audio_slice_expected_duration_sec: isLipSync ? Math.max(0, endSec - startSec) : 0,
      identity_lock_applied: continuityLock,
      identity_lock_fields_used: identityFields,
    };
  });
  const diagnostics = storyboard?.diagnostics && typeof storyboard.diagnostics === "object" ? { ...storyboard.diagnostics } : {};
  diagnostics.gemini_input_understanding = inputUnderstanding;
  return {
    story_summary: normalizeText(storyboard?.story_summary),
    full_scenario: normalizeText(storyboard?.full_scenario),
    voice_script: normalizeText(storyboard?.voice_script),
    director_summary: normalizeText(storyboard?.director_summary),
    audio_understanding: storyboard?.audio_understanding && typeof storyboard.audio_understanding === "object" ? storyboard.audio_understanding : {},
    narrative_strategy: storyboard?.narrative_strategy && typeof storyboard.narrative_strategy === "object" ? storyboard.narrative_strategy : {},
    diagnostics,
    scenes,
  };
}

export function normalizeScenarioDirectorApiResponse(response = {}, state = {}) {
  if (String(response?.pipeline || "").trim() === "scenario_stage_v1") {
    const storyboardPackage = response?.storyboardPackage && typeof response.storyboardPackage === "object" ? response.storyboardPackage : {};
    const storyCore = storyboardPackage?.story_core && typeof storyboardPackage.story_core === "object" ? storyboardPackage.story_core : {};
    const scenes = Array.isArray(storyboardPackage?.final_storyboard?.scenes)
      ? storyboardPackage.final_storyboard.scenes
      : (Array.isArray(storyboardPackage?.scene_plan?.scenes) ? storyboardPackage.scene_plan.scenes : []);
    const storyboardOut = {
      scenes,
      contentType: "music_video",
      format: normalizeText(state?.format) || "9:16",
      story_summary: normalizeText(storyCore?.story_summary),
      opening_anchor: normalizeText(storyCore?.opening_anchor),
      ending_callback_rule: normalizeText(storyCore?.ending_callback_rule),
    };
    return {
      storyboardOut,
      scenario: normalizeText(storyCore?.story_summary),
      voiceScript: "",
      scenarioPackage: storyboardPackage,
      brainPackage: null,
      bgMusicPrompt: "",
      globalMusicPrompt: "",
      directorOutput: {
        pipeline: "scenario_stage_v1",
        storyboardPackage,
        stageStatuses: storyboardPackage?.stage_statuses && typeof storyboardPackage.stage_statuses === "object" ? storyboardPackage.stage_statuses : {},
        diagnostics: storyboardPackage?.diagnostics && typeof storyboardPackage.diagnostics === "object" ? storyboardPackage.diagnostics : {},
        executedStages: Array.isArray(response?.executedStages) ? response.executedStages : [],
        scenes: storyboardOut.scenes,
      },
      raw: response,
    };
  }
  const clipPipelineUsed = String(response?.pipeline || "").trim() === "clip_chunked_v1";
  if (clipPipelineUsed) {
    const mergedStoryboard = response?.merged_storyboard && typeof response.merged_storyboard === "object"
      ? response.merged_storyboard
      : {};
    const mergedScenesRaw = Array.isArray(mergedStoryboard?.scenes) ? mergedStoryboard.scenes : [];
    const chunks = Array.isArray(response?.chunks) ? response.chunks : [];
    const chunkBySceneId = {};
    chunks.forEach((chunk) => {
      const chunkId = normalizeText(chunk?.chunk_id);
      const scenes = Array.isArray(chunk?.scenes) ? chunk.scenes : [];
      scenes.forEach((scene) => {
        const sceneId = normalizeText(scene?.scene_id);
        if (sceneId && chunkId) chunkBySceneId[sceneId] = chunkId;
      });
    });
    const normalizedScenes = mergedScenesRaw.map((scene, index) => {
      const sceneId = normalizeText(scene?.scene_id) || `S${index + 1}`;
      const route = normalizeText(scene?.route).toLowerCase();
      const firstFramePrompt = normalizeText(scene?.first_frame_prompt || scene?.firstFramePrompt);
      const lastFramePrompt = normalizeText(scene?.last_frame_prompt || scene?.lastFramePrompt);
      const transitionPrompt = normalizeText(scene?.transition_prompt || scene?.transitionPrompt);
      const framePrompt = normalizeText(scene?.frame_prompt || scene?.framePrompt || scene?.goal || "");
      return {
        scene_id: sceneId,
        time_start: Number(scene?.t0 ?? scene?.time_start ?? 0) || 0,
        time_end: Number(scene?.t1 ?? scene?.time_end ?? 0) || 0,
        duration: Math.max(0, (Number(scene?.t1 ?? scene?.time_end ?? 0) || 0) - (Number(scene?.t0 ?? scene?.time_start ?? 0) || 0)),
        scene_goal: normalizeText(scene?.goal),
        route,
        source_route: route,
        planned_video_generation_route: route,
        video_generation_route: route,
        resolved_workflow_key: route === "first_last" ? "f_l" : route,
        ltx_mode: route === "first_last" ? "f_l" : route,
        framePrompt,
        imagePrompt: framePrompt,
        frame_prompt: framePrompt,
        camera_prompt: normalizeText(scene?.camera_prompt || scene?.cameraPrompt),
        motion_prompt: normalizeText(scene?.motion_prompt || scene?.motionPrompt),
        startFramePrompt: route === "first_last" ? firstFramePrompt : "",
        endFramePrompt: route === "first_last" ? lastFramePrompt : "",
        transitionActionPrompt: route === "first_last" ? transitionPrompt : normalizeText(scene?.motion_prompt || scene?.motionPrompt),
        first_frame_prompt: firstFramePrompt,
        last_frame_prompt: lastFramePrompt,
        transition_prompt: transitionPrompt,
        chunkId: chunkBySceneId[sceneId] || "",
      };
    });
    const storyboardOut = {
      scenes: normalizedScenes,
      contentType: "music_video",
      format: normalizeText(response?.job?.format || state?.format) || "9:16",
    };
    const directorOutput = {
      scenes: normalizedScenes,
      contentType: "music_video",
      format: storyboardOut.format,
      clipPipeline: {
        pipeline: "clip_chunked_v1",
        whole_track_map: response?.whole_track_map || null,
        chunks,
        merged_storyboard: mergedStoryboard,
        repair: response?.repair || null,
        meta: response?.meta || {},
      },
      storyboardPackage: response?.storyboardPackage && typeof response.storyboardPackage === "object" ? response.storyboardPackage : null,
      stageStatuses: response?.storyboardPackage?.stage_statuses && typeof response.storyboardPackage.stage_statuses === "object" ? response.storyboardPackage.stage_statuses : {},
      diagnostics: response?.storyboardPackage?.diagnostics && typeof response.storyboardPackage.diagnostics === "object" ? response.storyboardPackage.diagnostics : {},
      executedStages: Array.isArray(response?.executedStages) ? response.executedStages : [],
    };
    console.debug("[CLIP PIPELINE NORMALIZE]", {
      pipelineModeSent: "clip_pipeline_v1",
      pipelineUsedReturned: response?.meta?.pipelineUsed || response?.pipeline,
      sceneCount: normalizedScenes.length,
      finalSceneEnd: response?.meta?.finalSceneEnd,
      audioDurationSec: response?.meta?.audioDurationSec,
    });
    return {
      storyboardOut,
      scenario: "",
      voiceScript: "",
      brainPackage: null,
      bgMusicPrompt: "",
      globalMusicPrompt: "",
      directorOutput,
      raw: response,
    };
  }

  const compactStoryboardOut = mapCompactDirectorResponseToStoryboardOut(response);
  const storyboardOut = response?.storyboardOut && typeof response.storyboardOut === "object"
    ? response.storyboardOut
    : response?.storyboard_out && typeof response.storyboard_out === "object"
      ? response.storyboard_out
      : compactStoryboardOut;
  const canonicalSceneContract = Array.isArray(response?.canonicalSceneContract)
    ? response.canonicalSceneContract
    : Array.isArray(response?.finalSceneContract)
      ? response.finalSceneContract
      : [];
  const directorOutputFromResponseRaw = response?.directorOutput && typeof response.directorOutput === "object"
    ? response.directorOutput
    : null;
  const directorOutputFromResponse = directorOutputFromResponseRaw
    ? {
      ...directorOutputFromResponseRaw,
      scenes: canonicalSceneContract.length
        ? canonicalSceneContract
        : (Array.isArray(directorOutputFromResponseRaw?.scenes) ? directorOutputFromResponseRaw.scenes : []),
    }
    : null;
  const packageRoleAwareKeys = [
    "refsByRole",
    "connectedRefsByRole",
    "roleTypeByRole",
    "heroParticipants",
    "supportingParticipants",
    "mustAppearRoles",
    "context_refs",
    "refDirectives",
  ];
  const sceneRoleAwareKeys = [
    "renderMode",
    "resolvedWorkflowKey",
    "lipSync",
    "lipSyncText",
    "sendAudioToGenerator",
    "audioSliceStartSec",
    "audioSliceEndSec",
    "audioSliceExpectedDurationSec",
    "scenePurpose",
    "viewerHook",
    "performanceFraming",
    "transitionType",
    "shotType",
    "requestedDurationSec",
    "primaryRole",
    "secondaryRoles",
    "sceneActiveRoles",
    "refsUsed",
    "mustAppear",
    "mustNotAppear",
    "heroEntityId",
    "supportEntityIds",
    "refDirectives",
  ];
  const hasAnyKey = (source, keys) => !!source && typeof source === "object" && keys.some((key) => key in source);
  const extractScenes = (source) => {
    if (!source || typeof source !== "object") return [];
    return Array.isArray(source.scenes) ? source.scenes : [];
  };
  const isNonEmptyObject = (value) => !!value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length > 0;
  const toUniqueTextList = (...sources) => {
    const seen = new Set();
    const result = [];
    sources.forEach((items) => {
      if (!Array.isArray(items)) return;
      items.forEach((item) => {
        const clean = normalizeText(item);
        if (!clean || seen.has(clean)) return;
        seen.add(clean);
        result.push(clean);
      });
    });
    return result;
  };
  const toUniqueGenericList = (...sources) => {
    const seen = new Set();
    const result = [];
    sources.forEach((items) => {
      if (!Array.isArray(items)) return;
      items.forEach((item) => {
        if (!item) return;
        const key = typeof item === "string" ? item : JSON.stringify(item);
        if (!key || seen.has(key)) return;
        seen.add(key);
        result.push(item);
      });
    });
    return result;
  };
  const firstNonEmptyText = (...values) => values.map((item) => normalizeText(item)).find(Boolean) || "";
  const mergeUniqueList = (existingList, nextList) => {
    const mergedList = toUniqueGenericList(existingList, nextList);
    return mergedList.length ? mergedList : Array.isArray(existingList) ? existingList : [];
  };
  const mergeMapField = (field) => {
    const merged = {};
    [response, storyboardOut, directorOutputFromResponse].forEach((source) => {
      const value = source?.[field];
      if (!value || typeof value !== "object" || Array.isArray(value)) return;
      Object.entries(value).forEach(([key, mapValue]) => {
        if (Array.isArray(mapValue)) {
          if (!mapValue.length) return;
          const existing = Array.isArray(merged[key]) ? merged[key] : [];
          merged[key] = mergeUniqueList(existing, mapValue);
          return;
        }
        if (isNonEmptyObject(mapValue)) {
          merged[key] = { ...(merged[key] && typeof merged[key] === "object" ? merged[key] : {}), ...mapValue };
          return;
        }
        if (normalizeText(mapValue)) merged[key] = mapValue;
      });
    });
    return merged;
  };
  const responseScenes = canonicalSceneContract.length ? [] : extractScenes(response);
  const storyboardScenes = canonicalSceneContract.length ? [] : extractScenes(storyboardOut);
  const directorScenes = extractScenes(directorOutputFromResponse);
  const hadDirectorOutput = !!directorOutputFromResponse;
  const hadStoryboardOut = !!storyboardOut;
  const hadResponseScenes = responseScenes.length > 0;
  const packageContractDetected = [
    response,
    storyboardOut,
    directorOutputFromResponse,
  ].some((item) => hasAnyKey(item, packageRoleAwareKeys));
  const sceneContractDetected = [
    ...responseScenes,
    ...storyboardScenes,
    ...directorScenes,
  ].some((scene) => hasAnyKey(scene, sceneRoleAwareKeys));
  const newContractDetected = packageContractDetected || sceneContractDetected;
  const hasUsableScenarioData = hadDirectorOutput
    || hadStoryboardOut
    || hadResponseScenes
    || packageContractDetected
    || directorScenes.length > 0
    || storyboardScenes.length > 0;
  if (!hasUsableScenarioData) return null;
  const stateFormat = NARRATIVE_FORMAT_OPTIONS.includes(String(state?.format || "").trim())
    ? String(state.format).trim()
    : "";
  const resolvedFormat = normalizeText(
    response?.format
    ?? response?.aspectRatio
    ?? response?.aspect_ratio
    ?? response?.canvas
    ?? storyboardOut?.format
    ?? storyboardOut?.aspectRatio
    ?? storyboardOut?.aspect_ratio
    ?? storyboardOut?.canvas
    ?? stateFormat
  ) || "9:16";
  const legacyFallbackOutput = !newContractDetected && storyboardOut ? mapStoryboardOutToDirectorOutput(storyboardOut, state) : null;
  const packageMergeSources = [
    hadDirectorOutput ? "directorOutput" : "",
    hadStoryboardOut ? "storyboardOut" : "",
    !!response && typeof response === "object" ? "response" : "",
  ].filter(Boolean);
  const mergedPackageRoleContract = {
    refsByRole: mergeMapField("refsByRole"),
    connectedRefsByRole: mergeMapField("connectedRefsByRole"),
    roleTypeByRole: mergeMapField("roleTypeByRole"),
    connected_context_summary: firstNonEmptyText(
      directorOutputFromResponse?.connected_context_summary,
      storyboardOut?.connected_context_summary,
      response?.connected_context_summary
    ),
    heroParticipants: toUniqueTextList(
      directorOutputFromResponse?.heroParticipants,
      storyboardOut?.heroParticipants,
      response?.heroParticipants
    ),
    supportingParticipants: toUniqueTextList(
      directorOutputFromResponse?.supportingParticipants,
      storyboardOut?.supportingParticipants,
      response?.supportingParticipants
    ),
    mustAppearRoles: toUniqueTextList(
      directorOutputFromResponse?.mustAppearRoles,
      storyboardOut?.mustAppearRoles,
      response?.mustAppearRoles
    ),
    context_refs: mergeMapField("context_refs"),
    refDirectives: mergeMapField("refDirectives"),
  };
  const maxSceneCount = Math.max(directorScenes.length, storyboardScenes.length, responseScenes.length);
  const roleAwareScenes = Array.from({ length: maxSceneCount }, (_, index) => {
    const directorScene = directorScenes[index] && typeof directorScenes[index] === "object" ? directorScenes[index] : null;
    const storyboardScene = storyboardScenes[index] && typeof storyboardScenes[index] === "object" ? storyboardScenes[index] : null;
    const responseScene = responseScenes[index] && typeof responseScenes[index] === "object" ? responseScenes[index] : null;
    if (!directorScene && !storyboardScene && !responseScene) return null;
    const resolvedWorkflowKey = firstNonEmptyText(
      directorScene?.resolvedWorkflowKey,
      storyboardScene?.resolvedWorkflowKey,
      storyboardScene?.resolved_workflow_key,
      responseScene?.resolvedWorkflowKey,
      responseScene?.resolved_workflow_key
    ) || "i2v";
    const videoGenerationRoute = firstNonEmptyText(
      directorScene?.videoGenerationRoute,
      storyboardScene?.videoGenerationRoute,
      storyboardScene?.video_generation_route,
      responseScene?.videoGenerationRoute,
      responseScene?.video_generation_route
    );
    const plannedVideoGenerationRoute = firstNonEmptyText(
      directorScene?.plannedVideoGenerationRoute,
      storyboardScene?.plannedVideoGenerationRoute,
      storyboardScene?.planned_video_generation_route,
      responseScene?.plannedVideoGenerationRoute,
      responseScene?.planned_video_generation_route
    );
    const sourceRoute = firstNonEmptyText(
      directorScene?.sourceRoute,
      storyboardScene?.sourceRoute,
      storyboardScene?.source_route,
      responseScene?.sourceRoute,
      responseScene?.source_route
    );
    const uiRouteValue = firstNonEmptyText(videoGenerationRoute, plannedVideoGenerationRoute, sourceRoute, resolvedWorkflowKey);
    const uiRouteSource = videoGenerationRoute
      ? "videoGenerationRoute"
      : plannedVideoGenerationRoute
        ? "plannedVideoGenerationRoute"
        : sourceRoute
          ? "sourceRoute"
          : "legacy";
    const routeNormalized = String(uiRouteValue || "").trim().toLowerCase();
    const lipSyncFromRoute = routeNormalized === "lip_sync_music";
    const lipSyncFromState = Boolean(
      directorScene?.lipSync
      ?? directorScene?.isLipSync
      ?? storyboardScene?.lipSync
      ?? storyboardScene?.isLipSync
      ?? storyboardScene?.lip_sync
      ?? responseScene?.lipSync
      ?? responseScene?.isLipSync
      ?? responseScene?.lip_sync
    );
    const uiLipSyncSource = lipSyncFromRoute ? "route" : (lipSyncFromState ? "state" : "legacy");
    return {
      ...(responseScene || {}),
      ...(storyboardScene || {}),
      ...(directorScene || {}),
      renderMode: firstNonEmptyText(directorScene?.renderMode, storyboardScene?.renderMode, storyboardScene?.render_mode, responseScene?.renderMode, responseScene?.render_mode) || "image_video",
      resolvedWorkflowKey,
      sourceRoute,
      videoGenerationRoute,
      plannedVideoGenerationRoute,
      uiRouteSource,
      uiRouteValue,
      resolvedWorkflowFile: firstNonEmptyText(
        directorScene?.resolvedWorkflowFile,
        storyboardScene?.resolvedWorkflowFile,
        storyboardScene?.resolved_workflow_file,
        responseScene?.resolvedWorkflowFile,
        responseScene?.resolved_workflow_file
      ),
      audioSliceKind: firstNonEmptyText(
        directorScene?.audioSliceKind,
        storyboardScene?.audioSliceKind,
        storyboardScene?.audio_slice_kind,
        responseScene?.audioSliceKind,
        responseScene?.audio_slice_kind
      ),
      musicVocalLipSyncAllowed: Boolean(
        directorScene?.musicVocalLipSyncAllowed
        ?? storyboardScene?.musicVocalLipSyncAllowed
        ?? storyboardScene?.music_vocal_lipsync_allowed
        ?? responseScene?.musicVocalLipSyncAllowed
        ?? responseScene?.music_vocal_lipsync_allowed
      ),
      lipSync: lipSyncFromRoute || lipSyncFromState,
      isLipSync: lipSyncFromRoute || lipSyncFromState,
      uiLipsyncSource: uiLipSyncSource,
      lipSyncText: firstNonEmptyText(directorScene?.lipSyncText, storyboardScene?.lipSyncText, storyboardScene?.lip_sync_text, responseScene?.lipSyncText, responseScene?.lip_sync_text),
      sendAudioToGenerator: Boolean(
        directorScene?.sendAudioToGenerator
        ?? storyboardScene?.sendAudioToGenerator
        ?? storyboardScene?.send_audio_to_generator
        ?? responseScene?.sendAudioToGenerator
        ?? responseScene?.send_audio_to_generator
      ),
      audioSliceStartSec: toStoryboardNumericSec(
        directorScene?.audioSliceStartSec
        ?? storyboardScene?.audioSliceStartSec
        ?? storyboardScene?.audio_slice_start_sec
        ?? responseScene?.audioSliceStartSec
        ?? responseScene?.audio_slice_start_sec,
        0
      ),
      audioSliceEndSec: toStoryboardNumericSec(
        directorScene?.audioSliceEndSec
        ?? storyboardScene?.audioSliceEndSec
        ?? storyboardScene?.audio_slice_end_sec
        ?? responseScene?.audioSliceEndSec
        ?? responseScene?.audio_slice_end_sec,
        0
      ),
      audioSliceExpectedDurationSec: toStoryboardNumericSec(
        directorScene?.audioSliceExpectedDurationSec
        ?? storyboardScene?.audioSliceExpectedDurationSec
        ?? storyboardScene?.audio_slice_expected_duration_sec
        ?? responseScene?.audioSliceExpectedDurationSec
        ?? responseScene?.audio_slice_expected_duration_sec,
        0
      ),
      scenePurpose: firstNonEmptyText(directorScene?.scenePurpose, storyboardScene?.scenePurpose, storyboardScene?.scene_purpose, responseScene?.scenePurpose, responseScene?.scene_purpose),
      viewerHook: firstNonEmptyText(directorScene?.viewerHook, storyboardScene?.viewerHook, storyboardScene?.viewer_hook, responseScene?.viewerHook, responseScene?.viewer_hook),
      performanceFraming: firstNonEmptyText(
        directorScene?.performanceFraming,
        storyboardScene?.performanceFraming,
        storyboardScene?.performance_framing,
        responseScene?.performanceFraming,
        responseScene?.performance_framing
      ),
      transitionType: firstNonEmptyText(directorScene?.transitionType, storyboardScene?.transitionType, storyboardScene?.transition_type, responseScene?.transitionType, responseScene?.transition_type) || "cut",
      shotType: firstNonEmptyText(directorScene?.shotType, storyboardScene?.shotType, storyboardScene?.shot_type, responseScene?.shotType, responseScene?.shot_type),
      requestedDurationSec: toStoryboardNumericSec(
        directorScene?.requestedDurationSec
        ?? storyboardScene?.requestedDurationSec
        ?? storyboardScene?.requested_duration_sec
        ?? responseScene?.requestedDurationSec
        ?? responseScene?.requested_duration_sec,
        0
      ),
      ...(isFirstLastLikeScene({ ...(responseScene || {}), ...(storyboardScene || {}), ...(directorScene || {}) })
        ? (() => {
          const mergedScene = { ...(responseScene || {}), ...(storyboardScene || {}), ...(directorScene || {}) };
          const prompts = deriveFirstLastPrompts(mergedScene);
          return {
            startFramePrompt: prompts.start || "",
            endFramePrompt: prompts.end || "",
            startFramePromptRu: prompts.start || "",
            startFramePromptEn: prompts.start || "",
            endFramePromptRu: prompts.end || "",
            endFramePromptEn: prompts.end || "",
          };
        })()
        : {}),
      primaryRole: firstNonEmptyText(directorScene?.primaryRole, storyboardScene?.primaryRole, responseScene?.primaryRole),
      secondaryRoles: toUniqueTextList(directorScene?.secondaryRoles, storyboardScene?.secondaryRoles, responseScene?.secondaryRoles),
      sceneActiveRoles: toUniqueTextList(directorScene?.sceneActiveRoles, storyboardScene?.sceneActiveRoles, responseScene?.sceneActiveRoles),
      refsUsed: toUniqueGenericList(directorScene?.refsUsed, storyboardScene?.refsUsed, responseScene?.refsUsed),
      mustAppear: toUniqueTextList(directorScene?.mustAppear, storyboardScene?.mustAppear, responseScene?.mustAppear),
      mustNotAppear: toUniqueTextList(directorScene?.mustNotAppear, storyboardScene?.mustNotAppear, responseScene?.mustNotAppear),
      heroEntityId: firstNonEmptyText(directorScene?.heroEntityId, storyboardScene?.heroEntityId, responseScene?.heroEntityId),
      supportEntityIds: toUniqueGenericList(directorScene?.supportEntityIds, storyboardScene?.supportEntityIds, responseScene?.supportEntityIds),
      refDirectives: {
        ...(responseScene?.refDirectives && typeof responseScene.refDirectives === "object" ? responseScene.refDirectives : {}),
        ...(storyboardScene?.refDirectives && typeof storyboardScene.refDirectives === "object" ? storyboardScene.refDirectives : {}),
        ...(directorScene?.refDirectives && typeof directorScene.refDirectives === "object" ? directorScene.refDirectives : {}),
      },
    };
  }).filter(Boolean);
  const legacyScenes = extractScenes(legacyFallbackOutput);
  const sceneMergeStrategy = roleAwareScenes.length
    ? (directorScenes.length && (storyboardScenes.length || responseScenes.length) ? "indexed_merge" : directorScenes.length ? "director_only" : storyboardScenes.length ? "storyboard_only" : "response_only")
    : legacyScenes.length
      ? "storyboard_only"
      : "response_only";
  const usedLegacyFallback = !!legacyFallbackOutput && (!directorOutputFromResponse || (!roleAwareScenes.length && legacyScenes.length > 0));
  const directorOutput = {
    ...(legacyFallbackOutput && typeof legacyFallbackOutput === "object" ? legacyFallbackOutput : {}),
    ...(directorOutputFromResponse && typeof directorOutputFromResponse === "object" ? directorOutputFromResponse : {}),
    refsByRole: mergedPackageRoleContract.refsByRole,
    connectedRefsByRole: mergedPackageRoleContract.connectedRefsByRole,
    roleTypeByRole: mergedPackageRoleContract.roleTypeByRole,
    connected_context_summary: mergedPackageRoleContract.connected_context_summary,
    heroParticipants: mergedPackageRoleContract.heroParticipants,
    supportingParticipants: mergedPackageRoleContract.supportingParticipants,
    mustAppearRoles: mergedPackageRoleContract.mustAppearRoles,
    context_refs: mergedPackageRoleContract.context_refs,
    refDirectives: mergedPackageRoleContract.refDirectives,
    scenes: roleAwareScenes.length ? roleAwareScenes : legacyScenes,
  };
  const globalMusicPrompt = resolveDirectorGlobalMusicPrompt(response, storyboardOut, directorOutput, state);
  const normalizedStoryboardOut = storyboardOut ? {
    ...storyboardOut,
    format: normalizeText(storyboardOut?.format) || resolvedFormat,
    aspectRatio: normalizeText(storyboardOut?.aspectRatio ?? storyboardOut?.aspect_ratio) || resolvedFormat,
    globalMusicPrompt: normalizeText(storyboardOut?.globalMusicPrompt) || globalMusicPrompt,
    music: {
      ...(storyboardOut?.music && typeof storyboardOut.music === "object" ? storyboardOut.music : {}),
      globalMusicPrompt: normalizeText(storyboardOut?.music?.globalMusicPrompt) || globalMusicPrompt,
    },
  } : null;
  console.debug("[SCENARIO NORMALIZE]", {
    hadDirectorOutput,
    hadStoryboardOut,
    hadResponseScenes,
    newContractDetected,
    usedLegacyFallback,
    packageMergeSources,
    sceneMergeStrategy,
    packageRefsByRoleKeys: Object.keys(directorOutput?.refsByRole || {}),
    packageConnectedRefsByRoleKeys: Object.keys(directorOutput?.connectedRefsByRole || {}),
    packageHeroParticipants: directorOutput?.heroParticipants || [],
    packageSupportingParticipants: directorOutput?.supportingParticipants || [],
    packageMustAppearRoles: directorOutput?.mustAppearRoles || [],
    sceneRoleSummary: (Array.isArray(directorOutput?.scenes) ? directorOutput.scenes : []).map((scene, index) => ({
      scene: index + 1,
      primaryRole: scene?.primaryRole || "",
      secondaryRoles: Array.isArray(scene?.secondaryRoles) ? scene.secondaryRoles : [],
      sceneActiveRoles: Array.isArray(scene?.sceneActiveRoles) ? scene.sceneActiveRoles : [],
      refsUsed: Array.isArray(scene?.refsUsed) ? scene.refsUsed : [],
      mustAppear: Array.isArray(scene?.mustAppear) ? scene.mustAppear : [],
    })),
  });
  return {
    storyboardOut: normalizedStoryboardOut,
    scenario: normalizeText(response?.scenario) || normalizeText(storyboardOut?.full_scenario),
    voiceScript: normalizeText(response?.voiceScript) || normalizeText(storyboardOut?.voice_script),
    brainPackage: response?.brainPackage && typeof response.brainPackage === "object" ? response.brainPackage : null,
    bgMusicPrompt: normalizeText(response?.bgMusicPrompt) || globalMusicPrompt || normalizeText(storyboardOut?.music_prompt),
    globalMusicPrompt,
    directorOutput: {
      ...(directorOutput && typeof directorOutput === "object" ? directorOutput : {}),
      format: normalizeText(directorOutput?.format) || resolvedFormat,
      globalMusicPrompt: normalizeText(directorOutput?.globalMusicPrompt) || globalMusicPrompt,
      music_prompt: normalizeText(directorOutput?.music_prompt) || globalMusicPrompt,
      music: {
        ...(directorOutput?.music && typeof directorOutput.music === "object" ? directorOutput.music : {}),
        globalMusicPrompt: normalizeText(directorOutput?.music?.globalMusicPrompt) || globalMusicPrompt,
      },
    },
  };
}

export function buildNarrativeOutputs(state = {}) {
  const resolvedSource = resolveNarrativeSource(state);
  const sourceMode = resolvedSource.mode || "AUDIO";
  const contentType = getSafeNarrativeContentType(
    NARRATIVE_CONTENT_TYPE_OPTIONS.some((item) => item.value === state.contentType) ? state.contentType : "story",
    "music_video"
  );
  const contentTypePolicy = getNarrativeContentTypePolicy(contentType);
  const narrativeMode = NARRATIVE_MODE_OPTIONS.some((item) => item.value === state.narrativeMode) ? state.narrativeMode : "cinematic_expand";
  const styleProfile = NARRATIVE_STYLE_OPTIONS.some((item) => item.value === state.styleProfile) ? state.styleProfile : "realistic";
  const format = NARRATIVE_FORMAT_OPTIONS.includes(String(state?.format || "").trim())
    ? String(state.format).trim()
    : "9:16";
  const connectedContext = summarizeNarrativeConnectedContext({ ...state, resolvedSource });

  const directorNote = normalizeText(state.directorNote) || "Без дополнительных правок";
  const sourcePayload = normalizeText(resolvedSource.value);

  if (!sourcePayload) {
    return {
      storyboardOut: null,
      scenario: "",
      voiceScript: "",
      brainPackage: null,
      bgMusicPrompt: "",
      directorOutput: null,
    };
  }

  const sourceLabel = lookupLabel(NARRATIVE_SOURCE_OPTIONS, sourceMode, "Аудио");
  const contentTypeLabel = lookupLabel(NARRATIVE_CONTENT_TYPE_OPTIONS, contentType, "История");
  const narrativeModeLabel = lookupLabel(NARRATIVE_MODE_OPTIONS, narrativeMode, "Расширить кинематографично");
  const styleLabel = lookupLabel(NARRATIVE_STYLE_OPTIONS, styleProfile, "Реалистичный");
  const entities = splitEntities(`${sourcePayload}. ${directorNote}`);
  const readableEntities = entities.length ? entities : ["Главный герой", "Ключевой объект", "Среда действия"];
  const sourceOriginLabel = resolvedSource.origin === "connected" ? "Подключённый источник" : "Источник не подключён";
  const connectedContextLabel = [
    `CAST roles: ${connectedContext.presentCastRoles.length} (${connectedContext.presentCastRoles.join(", ") || "нет"})`,
    `WORLD roles: ${connectedContext.presentWorldRoles.join(", ") || "нет"}`,
    `props: ${connectedContext.hasProps ? "да" : "нет"}`,
    `location: ${connectedContext.hasLocation ? "да" : "нет"}`,
    `style: ${connectedContext.hasStyle ? "да" : "нет"}`,
    `connected role ids: ${connectedContext.connectedRoleIds.join(", ") || "нет"}`,
  ].join(", ");

  const shortDescription = `${contentTypeLabel} в стиле «${styleLabel}». Основа: ${sourceLabel.toLowerCase()}.`;
  const fullScenario = [
    `Кратко: ${shortDescription}`,
    `Director controls: ${narrativeModeLabel}.`,
    `Режиссёрская задача: ${directorNote}.`,
    `Источник сейчас: ${sourceOriginLabel}.`,
    `Connected context: ${connectedContextLabel}.`,
    `Исходный материал: ${sourcePayload}`,
    "",
    "Рабочая драматургия:",
    "1. Завязка — быстро вводим мир, героя и эмоциональный тон.",
    "2. Развитие — усиливаем цель, конфликт или ожидание зрителя.",
    "3. Кульминация — даём самый сильный визуальный или драматический акцент.",
    "4. Финал — оставляем ясное послевкусие и направление для следующих сцен.",
  ].join("\n");

  const adaptationSummary = [
    `Адаптация под задачу: ${directorNote}.`,
    `Тип видео: ${contentTypeLabel}.`,
    `Стиль обработки: ${styleLabel}.`,
    `Активный source-of-truth: ${sourceLabel}.`,
    `Connected context: ${connectedContextLabel}.`,
  ].join("\n");

  const scenario = [
    shortDescription,
    "",
    "Полный сценарий:",
    fullScenario,
    "",
    "Результат адаптации:",
    adaptationSummary,
  ].join("\n");

  const voiceScript = [
    `Тон озвучки: ${styleLabel.toLowerCase()}, формат — ${contentTypeLabel.toLowerCase()}.`,
    "",
    "Текст диктора:",
    `«${shortDescription} ${directorNote !== "Без дополнительных правок" ? `Дополнительно: ${directorNote.toLowerCase()}.` : "Сохраняем ясный и цепляющий ритм."}»`,
    "",
    "Диалоги:",
    directorNote !== "Без дополнительных правок"
      ? `— Режиссёрская правка: ${directorNote}.`
      : "— Диалоги пока не заданы, акцент на авторской подаче диктора.",
  ].join("\n");

  const brainPackage = {
    contentType,
    contentTypeLabel,
    styleProfile,
    styleLabel,
    sourceMode,
    sourceOrigin: resolvedSource.origin,
    sourceLabel,
    sourcePreview: normalizeText(resolvedSource.preview) || sourcePayload,
    connectedContext,
    contentTypePolicy,
    entities: readableEntities,
    sceneLogic: [
      "Вход в мир истории и настрой атмосферы.",
      "Уточнение действия, цели или желания героя.",
      "Рост напряжения или визуального масштаба.",
      "Финальный акцент, который можно передать в раскадровку.",
    ],
    audioStrategy: `Фоновая музыка должна поддерживать стиль «${styleLabel}» без ударов, шагов и спецэффектов. Озвучка ведёт зрителя через основной конфликт и эмоциональные акценты.`,
    directorNote,
  };

  const bgMusicPrompt = contentTypePolicy.usesGlobalMusicPrompt
    ? [
      `Long background music for a ${contentTypeLabel.toLowerCase()} with ${styleLabel.toLowerCase()} mood.`,
      `Support the narrative arc from intro to climax to ending.`,
      `No footsteps, no hits, no sound effects, no stingers, only continuous cinematic background score.`,
      `Director note: ${directorNote}.`,
    ].join(" ")
    : "";

  const toneStyleDirection = `${styleLabel}. Режим director: ${narrativeModeLabel}. Видео должно сохранять единый тон и визуальную непрерывность от первого до финального кадра.`;
  const characterRoles = readableEntities.map((entity, index) => ({
    name: entity,
    role: index === 0
      ? "Главный герой / главный носитель действия"
      : index === 1
        ? "Партнёр по сцене / вторичный акцент"
        : "Поддерживающий персонаж или смысловой объект",
  }));

  const baseSceneBlueprints = [
    {
      title: "Интро / ввод в мир",
      location: connectedContext.hasLocation ? "Подключённая location reference" : "Основная локация истории",
      action: "Открываем историю сильным вводным образом и сразу фиксируем мир сцены.",
      emotion: "Ожидание, настрой, интрига",
      goal: "Дать зрителю понятный вход в мир, героя и общий визуальный тон.",
      cameraIdea: "Плавный establishing shot с мягким заходом камеры.",
      ltxMode: "i2v",
      whyThisMode: "Интро должно стартовать как production-ready intro shot с устойчивым первым кадром.",
      narrationMode: "intro_voiceover",
      localPhrase: "Это начало истории и эмоциональный вход для зрителя.",
      sfx: "Лёгкая атмосфера пространства, без перегруза.",
      soundNotes: "Оставить место для вступительного голоса и первого музыкального мотива.",
      pauseNotes: "Короткая пауза перед первым ключевым действием.",
    },
    {
      title: "Первое движение",
      location: connectedContext.hasLocation ? "Та же локация с уточнением масштаба" : "Развёрнутая среда действия",
      action: "Показываем, как герой входит в действие и начинает движение к цели.",
      emotion: "Фокус, импульс, вовлечение",
      goal: "Перевести сцену из экспозиции в реальное действие и удержать темп.",
      cameraIdea: "Средний план с движением в сторону действия и акцентом на реакцию героя.",
      ltxMode: "i2v",
      whyThisMode: "Нужен чистый production motion shot без кастомной терминологии.",
      narrationMode: "guided_progression",
      localPhrase: "Ставка растёт, и герой вынужден действовать точнее.",
      sfx: "Точечные движения среды, мягкие акценты действия.",
      soundNotes: "Саунд-дизайн подчёркивает ритм без доминирования над текстом.",
      pauseNotes: "Небольшие duck/silence окна под ключевые фразы диктора.",
    },
    {
      title: "Углубление масштаба",
      location: connectedContext.hasLocation ? "Глубокая часть подключённой локации" : "Более масштабная зона пространства",
      action: "Усиливаем глубину, падение масштаба и визуальную амплитуду сцены.",
      emotion: "Рост напряжения, расширение масштаба",
      goal: "Сделать сцену крупнее и драматически ощутимее перед следующим beat.",
      cameraIdea: "Выразительный push-in или движение через глубину пространства.",
      ltxMode: "f_l",
      whyThisMode: "Нужен production mode для глубины, масштаба и акцентного движения.",
      narrationMode: "depth_build",
      localPhrase: "Мир истории начинает давить сильнее и становится масштабнее.",
      sfx: "Более объёмная атмосфера и мягкий нарастающий акцент.",
      soundNotes: "Музыка и окружение расширяют сцену, но не ломают дикторскую читаемость.",
      pauseNotes: "Короткие окна под смену акцента и усиление кадра.",
    },
    {
      title: "Ответное действие",
      location: connectedContext.hasLocation ? "Функциональная зона конфликта" : "Центр действия",
      action: "Герой отвечает на вызов и начинает менять ход истории внутри кадра.",
      emotion: "Давление, решимость, концентрация",
      goal: "Показать активную фазу конфликта и усилить драматургию.",
      cameraIdea: "Динамичный проход камеры с читаемым фокусом на действии.",
      ltxMode: "i2v",
      whyThisMode: "Продолжаем основное движение сцены production-ready режимом i2v.",
      narrationMode: "conflict_push",
      localPhrase: "Теперь герой не наблюдает — он вмешивается в ход событий.",
      sfx: "Акценты действия и среды, собранные без лишнего шума.",
      soundNotes: "Озвучка должна уверенно вести зрителя через изменение ситуации.",
      pauseNotes: "Минимальные паузы, чтобы сохранить импульс.",
    },
    {
      title: "Реакция и эмоциональный отскок",
      location: connectedContext.hasLocation ? "Реакционный ракурс той же локации" : "Зона эмоционального отклика",
      action: "Фиксируем реакцию героя или мира на произошедший поворот.",
      emotion: "Шок, понимание, эмоциональный отклик",
      goal: "Дать зрителю эмоционально считать последствия и усилить вовлечение.",
      cameraIdea: "Реакционный акцент с устойчивым кадром и мягким движением.",
      ltxMode: "i2v",
      whyThisMode: "Реакционный shot должен быть чистым, читаемым и стабильно собранным.",
      narrationMode: "reaction_line",
      localPhrase: "Именно здесь зритель чувствует последствия предыдущего удара.",
      sfx: "Сдержанный хвост события и акцент на дыхании пространства.",
      soundNotes: "Даём место для эмоциональной фразы и удерживаем музыкальный нерв.",
      pauseNotes: "Возможен короткий duck под главную реакцию.",
    },
    {
      title: "Продолжение хода",
      location: connectedContext.hasLocation ? "Связующая часть подключённой локации" : "Переход между ключевыми зонами",
      action: "Сцена напрямую подхватывает предыдущее действие и продолжает его без разрыва.",
      emotion: "Непрерывность, напряжение, поступательное движение",
      goal: "Обеспечить мягкий handoff между сценами и сохранить непрерывность действия.",
      cameraIdea: "Продолжение предыдущего кадра с логичным смещением точки внимания.",
      ltxMode: "continuation",
      whyThisMode: "Этот beat должен чувствоваться как прямое продолжение предыдущего кадра.",
      narrationMode: "continuous_push",
      localPhrase: "История не останавливается и тянет зрителя дальше без паузы.",
      sfx: "Непрерывная среда, перетекающая из предыдущего момента.",
      soundNotes: "Не обнулять музыку — сохранить continuity между сценами.",
      pauseNotes: "Паузы минимальны, чтобы не ломать эффект продолжения.",
    },
    {
      title: "Финальный напор",
      location: connectedContext.hasLocation ? "Выход на финальную точку локации" : "Последний активный участок пространства",
      action: "Дожимаем сюжетный импульс и подводим сцену к финальному образу.",
      emotion: "Напряжение, решимость, подводка к финалу",
      goal: "Подготовить кульминационный образ или финальный переход.",
      cameraIdea: "Уверенное движение к финальной точке действия.",
      ltxMode: "i2v",
      whyThisMode: "Нужен ещё один production motion shot перед финальным закрытием.",
      narrationMode: "pre_closing_drive",
      localPhrase: "Всё сводится к последнему сильному визуальному жесту.",
      sfx: "Собранный импульс действия и плотная атмосфера.",
      soundNotes: "Музыка нарастает, но оставляет место под заключительную мысль.",
      pauseNotes: "Короткий micro-pause возможен перед финальным акцентом.",
    },
    {
      title: "Финальный выход",
      location: connectedContext.hasLocation ? "Финальный ракурс подключённой локации" : "Финальный образ пространства",
      action: "Замедляемся, фиксируем результат и оставляем послевкусие.",
      emotion: "Освобождение, завершённость, послевкусие",
      goal: "Закрыть драматическую фразу и подготовить материал для storyboard handoff.",
      cameraIdea: "Плавный отъезд или статичный финальный hold.",
      ltxMode: "f_l",
      whyThisMode: "Финал должен закрываться production-ready финальным режимом без legacy-названий.",
      narrationMode: "closing_voiceover",
      localPhrase: "История завершает фразу и оставляет ясный итог.",
      sfx: "Остаточная атмосфера и мягкий хвост пространства.",
      soundNotes: "Оставить место для финальной фразы и музыкального разрешения.",
      pauseNotes: "Финальная тишина или duck для последней мысли.",
    },
  ];

  const scenes = baseSceneBlueprints.map((sceneBlueprint, index) => {
    const timing = buildSceneWindow(index, baseSceneBlueprints.length);
    const participants = characterRoles.slice(0, Math.max(1, Math.min(characterRoles.length, index + 1))).map((item) => item.name);
    const props = connectedContext.hasProps ? ["Подключённые props/ref-объекты"] : ["Ключевой объект сцены"];
    const timeStartSec = toStoryboardNumericSec(timing.timeStart, index * 5);
    const timeEndSec = toStoryboardNumericSec(timing.timeEnd, timeStartSec + timing.durationSec);
    const startFrameSource = index === 0 ? "new" : "previous_end";
    const imagePrompt = [
      `${contentTypeLabel}, ${styleLabel.toLowerCase()} style.`,
      `Scene ${index + 1}: ${sceneBlueprint.title}.`,
      `Participants: ${participants.join(", ")}.`,
      `Location: ${sceneBlueprint.location}.`,
      `Action: ${sceneBlueprint.action}`,
      `Director note: ${directorNote}.`,
    ].join(" ");
    const videoPrompt = [
      `Animate scene ${index + 1} with ${sceneBlueprint.cameraIdea.toLowerCase()}.`,
      `Emotion: ${sceneBlueprint.emotion}.`,
      `Scene goal: ${sceneBlueprint.goal}.`,
      `Maintain continuity with ${styleLabel.toLowerCase()} visual language.`,
    ].join(" ");

    return {
      sceneId: `S${index + 1}`,
      title: sceneBlueprint.title,
      timeStart: timeStartSec,
      timeEnd: timeEndSec,
      duration: timing.durationSec,
      participants,
      location: sceneBlueprint.location,
      props,
      action: sceneBlueprint.action,
      emotion: sceneBlueprint.emotion,
      sceneGoal: sceneBlueprint.goal,
      frameDescription: `${sceneBlueprint.title}: ${sceneBlueprint.action}`,
      actionInFrame: sceneBlueprint.action,
      cameraIdea: sceneBlueprint.cameraIdea,
      imagePrompt,
      videoPrompt,
      ltxMode: sceneBlueprint.ltxMode,
      whyThisMode: sceneBlueprint.whyThisMode,
      startFrameSource,
      needsTwoFrames: needsTwoFramesForMode(sceneBlueprint.ltxMode),
      continuation: isContinuationMode(sceneBlueprint.ltxMode),
      narrationMode: sceneBlueprint.narrationMode,
      localPhrase: sceneBlueprint.localPhrase,
      sfx: sceneBlueprint.sfx,
      soundNotes: sceneBlueprint.soundNotes,
      pauseDuckSilenceNotes: sceneBlueprint.pauseNotes,
      musicMixHint: index === 2 ? "medium" : index >= 6 ? "low" : "off",
    };
  });

  const storyboardOut = {
    format,
    aspectRatio: format,
    story_summary: shortDescription,
    full_scenario: fullScenario,
    voice_script: voiceScript,
    music_prompt: bgMusicPrompt,
    director_summary: adaptationSummary,
    scenes: scenes.map((scene) => ({
      scene_id: scene.sceneId,
      time_start: scene.timeStart,
      time_end: scene.timeEnd,
      duration: scene.duration,
      actors: participantsToActors(scene.participants),
      location: scene.location,
      props: scene.props,
      emotion: scene.emotion,
      scene_goal: scene.sceneGoal,
      frame_description: scene.frameDescription,
      action_in_frame: scene.actionInFrame,
      camera: scene.cameraIdea,
      image_prompt: scene.imagePrompt,
      video_prompt: scene.videoPrompt,
      ltx_mode: scene.ltxMode,
      start_frame_source: scene.startFrameSource,
      needs_two_frames: scene.needsTwoFrames,
      continuation_from_previous: scene.continuation,
      narration_mode: scene.narrationMode,
      local_phrase: scene.localPhrase || null,
      sfx: scene.sfx,
      ltx_reason: scene.whyThisMode,
      music_mix_hint: scene.musicMixHint,
    })),
  };

  const directorOutput = {
    format,
    history: {
      summary: shortDescription,
      fullScenario,
      characterRoles,
      toneStyleDirection,
      directorSummary: adaptationSummary,
      presentCastRoles: connectedContext.presentCastRoles,
      presentWorldRoles: connectedContext.presentWorldRoles,
      refsPresentByRole: connectedContext.refsPresentByRole,
      connectedRefsPresentByRole: connectedContext.connectedRefsPresentByRole,
      hasProps: connectedContext.hasProps,
      hasLocation: connectedContext.hasLocation,
      hasStyle: connectedContext.hasStyle,
    },
    scenes,
    video: scenes.map((scene) => ({
      sceneId: scene.sceneId,
      frameDescription: scene.frameDescription,
      actionInFrame: scene.actionInFrame,
      cameraIdea: scene.cameraIdea,
      imagePrompt: scene.imagePrompt,
      videoPrompt: scene.videoPrompt,
      ltxMode: scene.ltxMode,
      whyThisMode: scene.whyThisMode,
      startFrameSource: scene.startFrameSource,
      needsTwoFrames: scene.needsTwoFrames,
      continuation: scene.continuation,
    })),
    sound: scenes.map((scene) => ({
      sceneId: scene.sceneId,
      narrationMode: scene.narrationMode,
      localPhrase: scene.localPhrase,
      sfx: scene.sfx,
      soundNotes: scene.soundNotes,
      pauseDuckSilenceNotes: scene.pauseDuckSilenceNotes,
    })),
    music: {
      globalMusicPrompt: bgMusicPrompt,
      mood: styleLabel,
      style: `${contentTypeLabel} / ${styleLabel}`,
      pacingHints: "Start restrained, grow through the middle scenes, peak at the climax, and resolve with a clean outro.",
    },
    contentTypePolicy,
  };

  return {
    storyboardOut,
    scenario,
    voiceScript,
    brainPackage,
    bgMusicPrompt,
    directorOutput,
  };
}
