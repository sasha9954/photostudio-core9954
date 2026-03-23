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

export const NARRATIVE_RESULT_TABS = [
  { value: "scenario", labelRu: "Сценарий" },
  { value: "voice", labelRu: "Озвучка" },
  { value: "brain", labelRu: "Для мозга" },
  { value: "music", labelRu: "Музыка" },
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
    activeResultTab: "scenario",
    outputs: {
      scenario: "",
      voiceScript: "",
      brainPackage: null,
      bgMusicPrompt: "",
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

export function buildNarrativeOutputs(state = {}) {
  const resolvedSource = resolveNarrativeSource(state);
  const sourceMode = resolvedSource.mode || "AUDIO";
  const contentType = NARRATIVE_CONTENT_TYPE_OPTIONS.some((item) => item.value === state.contentType) ? state.contentType : "story";
  const narrativeMode = NARRATIVE_MODE_OPTIONS.some((item) => item.value === state.narrativeMode) ? state.narrativeMode : "cinematic_expand";
  const styleProfile = NARRATIVE_STYLE_OPTIONS.some((item) => item.value === state.styleProfile) ? state.styleProfile : "realistic";
  const connectedContext = summarizeNarrativeConnectedContext({ ...state, resolvedSource });

  const directorNote = normalizeText(state.directorNote) || "Без дополнительных правок";
  const sourcePayload = normalizeText(resolvedSource.value);

  if (!sourcePayload) {
    return {
      scenario: "",
      voiceScript: "",
      brainPackage: null,
      bgMusicPrompt: "",
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

  return {
    scenario,
    voiceScript,
    brainPackage,
    bgMusicPrompt,
  };
}
