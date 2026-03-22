export const NARRATIVE_SOURCE_OPTIONS = [
  { value: "TEXT", labelRu: "Текст" },
  { value: "AUDIO", labelRu: "Аудио" },
  { value: "VIDEO_REF", labelRu: "Видео (референс)" },
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
  { id: "text_in", labelRu: "Текст", mode: "TEXT" },
  { id: "audio_in", labelRu: "Аудио", mode: "AUDIO" },
  { id: "video_ref_in", labelRu: "Видео (реф)", mode: "VIDEO_REF" },
];

const lookupLabel = (options, value, fallback) => options.find((option) => option.value === value)?.labelRu || fallback;

const normalizeText = (value) => String(value || "").trim();

const splitEntities = (text) => normalizeText(text)
  .split(/[,.!?:;\n]+/)
  .map((item) => item.trim())
  .filter(Boolean)
  .slice(0, 6);

export function getDefaultNarrativeNodeData() {
  return {
    sourceOrigin: "disconnected",
    contentType: "story",
    narrativeMode: "cinematic_expand",
    styleProfile: "realistic",
    directorNote: "",
    textInput: "",
    audioInput: "",
    videoUrlInput: "",
    connectedInputs: {
      text_in: null,
      audio_in: null,
      video_ref_in: null,
    },
    resolvedSource: {
      mode: null,
      origin: "disconnected",
      value: "",
      label: "Источник не подключён",
      sourceLabel: "Ожидается внешний источник",
      preview: "",
    },
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
  const connectedOption = NARRATIVE_INPUT_HANDLES.find((item) => normalizeText(connectedInputs?.[item.id]?.value));

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
  const connectedValue = normalizeText(connectedSource?.value);
  const modeLabel = lookupLabel(NARRATIVE_SOURCE_OPTIONS, connectedOption.mode, "Текст");

  return {
    mode: connectedOption.mode,
    origin: connectedValue ? "connected" : "disconnected",
    value: connectedValue,
    label: modeLabel,
    sourceLabel: normalizeText(connectedSource?.sourceLabel) || `Подключённый источник (${modeLabel.toLowerCase()})`,
    preview: normalizeText(connectedSource?.preview) || connectedValue,
  };
}

export function buildNarrativeOutputs(state = {}) {
  const resolvedSource = resolveNarrativeSource(state);
  const sourceMode = resolvedSource.mode || "TEXT";
  const contentType = NARRATIVE_CONTENT_TYPE_OPTIONS.some((item) => item.value === state.contentType) ? state.contentType : "story";
  const narrativeMode = NARRATIVE_MODE_OPTIONS.some((item) => item.value === state.narrativeMode) ? state.narrativeMode : "cinematic_expand";
  const styleProfile = NARRATIVE_STYLE_OPTIONS.some((item) => item.value === state.styleProfile) ? state.styleProfile : "realistic";

  const directorNote = normalizeText(state.directorNote) || "Без дополнительных правок";
  const textInput = normalizeText(state.textInput);
  const audioInput = normalizeText(state.audioInput) || "Аудио-референс будет добавлен позже.";
  const videoUrlInput = normalizeText(state.videoUrlInput) || "Ссылка на видео пока не указана.";

  const sourcePayload = normalizeText(resolvedSource.value)
    || (sourceMode === "TEXT"
      ? (textInput || "История пока не добавлена.")
      : sourceMode === "AUDIO"
        ? audioInput
        : videoUrlInput);

  const sourceLabel = lookupLabel(NARRATIVE_SOURCE_OPTIONS, sourceMode, "Текст");
  const contentTypeLabel = lookupLabel(NARRATIVE_CONTENT_TYPE_OPTIONS, contentType, "История");
  const narrativeModeLabel = lookupLabel(NARRATIVE_MODE_OPTIONS, narrativeMode, "Расширить кинематографично");
  const styleLabel = lookupLabel(NARRATIVE_STYLE_OPTIONS, styleProfile, "Реалистичный");
  const entities = splitEntities(sourceMode === "TEXT" ? sourcePayload : `${sourcePayload}. ${directorNote}`);
  const readableEntities = entities.length ? entities : ["Главный герой", "Ключевой объект", "Среда действия"];
  const sourceOriginLabel = resolvedSource.origin === "connected" ? "Подключённый источник" : "Ручной ввод";
  const sourcePreview = normalizeText(resolvedSource.preview) || sourcePayload;

  const shortDescription = `${contentTypeLabel} в стиле «${styleLabel}». Основа: ${sourceLabel.toLowerCase()}.`;
  const fullScenario = [
    `Кратко: ${shortDescription}`,
    `Режим обработки: ${narrativeModeLabel}.`,
    `Режиссёрская задача: ${directorNote}.`,
    `Источник сейчас: ${sourceOriginLabel}.`,
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
    `Стиль подачи: ${styleLabel}.`,
    `Главный источник: ${sourceLabel}.`,
    `Происхождение источника: ${sourceOriginLabel}.`,
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
    sourcePreview,
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
