export const NARRATIVE_SOURCE_OPTIONS = [
  { value: "AUDIO", labelRu: "Аудио" },
  { value: "VIDEO_FILE", labelRu: "Видео файл" },
  { value: "VIDEO_LINK", labelRu: "Ссылка на видео" },
];

export const NARRATIVE_CONTENT_TYPE_OPTIONS = [
  { value: "story", labelRu: "История" },
  { value: "music_video", labelRu: "Клип" },
  { value: "ad", labelRu: "Реклама" },
  { value: "cartoon", labelRu: "Мультфильм" },
  { value: "teaser", labelRu: "Тизер" },
  { value: "series", labelRu: "Сериал" },
];

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
  { id: "ref_character_1", labelRu: "Character 1", mode: "CONTEXT", kind: "context", role: "character_1" },
  { id: "ref_character_2", labelRu: "Character 2", mode: "CONTEXT", kind: "context", role: "character_2" },
  { id: "ref_character_3", labelRu: "Character 3", mode: "CONTEXT", kind: "context", role: "character_3" },
  { id: "ref_props", labelRu: "Props", mode: "CONTEXT", kind: "context", role: "props" },
  { id: "ref_location", labelRu: "Location", mode: "CONTEXT", kind: "context", role: "location" },
  { id: "ref_style", labelRu: "Style", mode: "CONTEXT", kind: "context", role: "style" },
];

export const NARRATIVE_SOURCE_INPUT_HANDLES = NARRATIVE_INPUT_HANDLES.filter((item) => item.kind === "story_source");
export const NARRATIVE_CONTEXT_INPUT_HANDLES = NARRATIVE_INPUT_HANDLES.filter((item) => item.kind === "context");

const lookupLabel = (options, value, fallback) => options.find((option) => option.value === value)?.labelRu || fallback;

const normalizeText = (value) => String(value || "").trim();
const CLIP_TRACE_SCENARIO_GLOBAL_MUSIC_SYNTH = false;

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
    .map((item, index) => {
      if (index === 0) return "character_1";
      if (index === 1) return "character_2";
      if (index === 2) return "character_3";
      return item;
    });
}

function needsTwoFramesForMode(ltxMode = "") {
  return ["f_l", "f_l_as"].includes(String(ltxMode || "").trim());
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

function resolveDirectorGlobalMusicPrompt(response = {}, storyboardOut = null, directorOutput = null) {
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
  const synthesizedPrompt = flatPrompt ? "" : buildGlobalMusicPromptFromStructuredMusic(structuredMusic);
  const resolvedPrompt = flatPrompt || synthesizedPrompt;
  if (CLIP_TRACE_SCENARIO_GLOBAL_MUSIC_SYNTH) {
    console.debug("[SCENARIO GLOBAL MUSIC SYNTH]", {
      hasFlatPrompt: !!flatPrompt,
      hasStructuredMusic: !!structuredMusic,
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
  const characterHandles = ["ref_character_1", "ref_character_2", "ref_character_3"];
  const characterCount = characterHandles.reduce((total, handleId) => total + (getConnectedInputCount(connectedInputs?.[handleId]) > 0 ? 1 : 0), 0);
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
    characterCount,
    hasProps: getConnectedInputCount(connectedInputs?.ref_props) > 0,
    hasLocation: getConnectedInputCount(connectedInputs?.ref_location) > 0,
    hasStyle: getConnectedInputCount(connectedInputs?.ref_style) > 0,
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
  const preferAudioOverText = audioContext.hasAudioSource;
  const segmentationMode = audioContext.hasAudioSource ? "phrase-first" : "default";
  const timelineSource = audioContext.hasAudioSource ? "audio" : "text";
  const useAudioPhraseBoundaries = audioContext.hasAudioSource;
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
      contentType: normalizeText(state.contentType) || "story",
      narrativeMode: normalizeText(state.narrativeMode) || "cinematic_expand",
      styleProfile: normalizeText(state.styleProfile) || "realistic",
      format,
      directorNote: normalizeText(state.directorNote),
      preferAudioOverText,
      segmentationMode,
      timelineSource,
      useAudioPhraseBoundaries,
    },
    connected_context_summary: connectedContextSummary,
    metadata: {
      sourcePreview: normalizeText(resolvedSource.preview) || sourceValue,
      sourceLabel: normalizeText(resolvedSource.sourceLabel) || normalizeText(resolvedSource.label),
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

  if (audioContext.hasAudioSource) {
    console.debug("[ScenarioDirector] audio payload context", {
      sourceMode: normalizeNarrativeSourceMode(resolvedSource.mode),
      audioUrl: audioContext.url || sourceValue,
      source_origin: audioContext.sourceOrigin || normalizeText(resolvedSource.origin) || "connected",
      metadataAudioOrigin: audioContext.audioOrigin || "audio_node",
      audioDurationSec: audioContext.audioDurationSec,
      wasDurationResolved,
      persistedAudioDurationSec,
      timelineSource,
      segmentationMode,
    });
  }

  return payload;
}

function formatActorLabel(actor, roleLabels) {
  const clean = normalizeText(actor);
  return roleLabels[clean] || clean;
}

export function mapStoryboardOutToDirectorOutput(storyboardOut = null, state = {}) {
  if (!storyboardOut || typeof storyboardOut !== "object") return null;
  const scenes = Array.isArray(storyboardOut.scenes) ? storyboardOut.scenes : [];
  const connectedInputs = state?.connectedInputs && typeof state.connectedInputs === "object" ? state.connectedInputs : {};
  const roleLabels = {
    character_1: normalizeText(connectedInputs?.ref_character_1?.preview) || "Character 1",
    character_2: normalizeText(connectedInputs?.ref_character_2?.preview) || "Character 2",
    character_3: normalizeText(connectedInputs?.ref_character_3?.preview) || "Character 3",
  };
  const history = {
    summary: normalizeText(storyboardOut.story_summary),
    fullScenario: normalizeText(storyboardOut.full_scenario),
    characterRoles: Object.entries(roleLabels)
      .filter(([, label]) => !!normalizeText(label))
      .map(([role, label], index) => ({
        name: label,
        role: index === 0
          ? "Главный герой / главный носитель действия"
          : index === 1
            ? "Партнёр по сцене / вторичный акцент"
            : role,
      })),
    toneStyleDirection: normalizeText(state.styleProfile) || "realistic",
    directorSummary: normalizeText(storyboardOut.director_summary),
  };
  const globalMusicPrompt = normalizeText(
    storyboardOut?.globalMusicPrompt
    ?? storyboardOut?.music_prompt
    ?? storyboardOut?.bgMusicPrompt
  );
  const normalizedScenes = scenes.map((scene, index) => {
    const ltxMode = normalizeText(scene.ltx_mode) || "i2v";
    return {
      sceneId: normalizeText(scene.scene_id) || `S${index + 1}`,
      title: normalizeText(scene.scene_id) || `S${index + 1}`,
      timeStart: toStoryboardNumericSec(scene.time_start, index * 5),
      timeEnd: toStoryboardNumericSec(scene.time_end, (index + 1) * 5),
      duration: toStoryboardNumericSec(scene.duration, 5),
      participants: (Array.isArray(scene.actors) ? scene.actors : []).map((actor) => formatActorLabel(actor, roleLabels)),
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
      ltxMode,
      whyThisMode: normalizeText(scene.ltx_reason),
      startFrameSource: normalizeText(scene.start_frame_source) || "new",
      needsTwoFrames: Boolean(scene.needs_two_frames),
      continuation: Boolean(scene.continuation_from_previous),
      narrationMode: normalizeText(scene.narration_mode) || "full",
      localPhrase: scene.local_phrase ? normalizeText(scene.local_phrase) : null,
      sfx: normalizeText(scene.sfx),
      soundNotes: normalizeText(scene.sfx),
      pauseDuckSilenceNotes: "",
      musicMixHint: normalizeText(scene.music_mix_hint) || "off",
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
      ltxMode: scene.ltxMode,
      whyThisMode: scene.whyThisMode,
      startFrameSource: scene.startFrameSource,
      needsTwoFrames: scene.needsTwoFrames,
      continuation: scene.continuation,
    })),
    sound: normalizedScenes.map((scene) => ({
      sceneId: scene.sceneId,
      narrationMode: scene.narrationMode,
      localPhrase: scene.localPhrase,
      sfx: scene.sfx,
      soundNotes: scene.soundNotes,
      pauseDuckSilenceNotes: scene.pauseDuckSilenceNotes,
    })),
    music: {
      globalMusicPrompt,
      mood: normalizeText(state.styleProfile) || "realistic",
      style: `${normalizeText(state.contentType) || "story"} / ${normalizeText(state.styleProfile) || "realistic"}`,
      pacingHints: "Use the approved storyboard_out pacing when Storyboard executes the scenes.",
    },
    globalMusicPrompt,
  };
}

export function normalizeScenarioDirectorApiResponse(response = {}, state = {}) {
  const storyboardOut = response?.storyboardOut && typeof response.storyboardOut === "object"
    ? response.storyboardOut
    : response?.storyboard_out && typeof response.storyboard_out === "object"
      ? response.storyboard_out
      : null;
  if (!storyboardOut) return null;
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
  const directorOutput = response?.directorOutput && typeof response.directorOutput === "object"
    ? response.directorOutput
    : mapStoryboardOutToDirectorOutput(storyboardOut, state);
  const globalMusicPrompt = resolveDirectorGlobalMusicPrompt(response, storyboardOut, directorOutput);
  const normalizedStoryboardOut = {
    ...storyboardOut,
    format: normalizeText(storyboardOut?.format) || resolvedFormat,
    aspectRatio: normalizeText(storyboardOut?.aspectRatio ?? storyboardOut?.aspect_ratio) || resolvedFormat,
    globalMusicPrompt: normalizeText(storyboardOut?.globalMusicPrompt) || globalMusicPrompt,
    music: {
      ...(storyboardOut?.music && typeof storyboardOut.music === "object" ? storyboardOut.music : {}),
      globalMusicPrompt: normalizeText(storyboardOut?.music?.globalMusicPrompt) || globalMusicPrompt,
    },
  };
  return {
    storyboardOut: normalizedStoryboardOut,
    scenario: normalizeText(response?.scenario) || normalizeText(storyboardOut.full_scenario),
    voiceScript: normalizeText(response?.voiceScript) || normalizeText(storyboardOut.voice_script),
    brainPackage: response?.brainPackage && typeof response.brainPackage === "object" ? response.brainPackage : null,
    bgMusicPrompt: normalizeText(response?.bgMusicPrompt) || globalMusicPrompt || normalizeText(storyboardOut.music_prompt),
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
  const contentType = NARRATIVE_CONTENT_TYPE_OPTIONS.some((item) => item.value === state.contentType) ? state.contentType : "story";
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
    `Персонажей подключено: ${connectedContext.characterCount}`,
    `props: ${connectedContext.hasProps ? "да" : "нет"}`,
    `location: ${connectedContext.hasLocation ? "да" : "нет"}`,
    `style: ${connectedContext.hasStyle ? "да" : "нет"}`,
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

  const bgMusicPrompt = [
    `Long background music for a ${contentTypeLabel.toLowerCase()} with ${styleLabel.toLowerCase()} mood.`,
    `Support the narrative arc from intro to climax to ending.`,
    `No footsteps, no hits, no sound effects, no stingers, only continuous cinematic background score.`,
    `Director note: ${directorNote}.`,
  ].join(" ");

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
      ltxMode: "i2v_as",
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
      ltxMode: "f_l_as",
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
      ltxMode: "i2v_as",
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
