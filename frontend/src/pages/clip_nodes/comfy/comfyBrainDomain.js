const RENDER_PROFILE_OPTIONS = ["comfy image", "comfy text"];
export const AUDIO_STORY_MODE_OPTIONS = ["lyrics_music", "music_only", "music_plus_text", "speech_narrative"];

const REFERENCE_HANDLE_TO_ROLE = {
  ref_character_1: "character_1",
  ref_character_2: "character_2",
  ref_character_3: "character_3",
  ref_animal: "animal",
  ref_group: "group",
  ref_location: "location",
  ref_style: "style",
  ref_props: "props",
};

const ROLE_PRIORITY = ["character_1", "character_2", "character_3", "animal", "group", "location", "props", "style"];
const VISUAL_ANCHOR_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"];

const STORY_OVERRIDE_MARKERS = ["не по песне", "другой сюжет", "separate story", "different story", "not literal lyrics"];
const STORY_ENHANCEMENT_MARKERS = ["усили", "усилить", "enhance", "intensify", "emphasize", "make more cinematic"];
const CLIP_TRACE_COMFY_REFS = false;

const MODE_RULES = {
  clip: {
    modeIntent: "music-driven montage",
    modePromptBias: "rhythm and visual energy",
    modeSceneStrategy: "beats and transitions",
    modeContinuityBias: "motif continuity",
    planningMindset: "music video director",
    continuityStrictness: "soft",
    abstractionAllowance: "high",
    narrativeDiscipline: "medium",
    planningRules: [
      "Prioritize audiovisual impact over literal causality.",
      "Allow associative montage and short scene beats.",
      "Keep motif continuity even when causality is loose.",
    ],
  },
  kino: {
    modeIntent: "cinematic causality",
    modePromptBias: "dramatic logic",
    modeSceneStrategy: "narrative chain",
    modeContinuityBias: "strong continuity",
    planningMindset: "director",
    continuityStrictness: "strong",
    abstractionAllowance: "medium",
    narrativeDiscipline: "high",
    planningRules: [
      "Build explicit cause-and-effect between scenes.",
      "Avoid random clip sequencing; preserve narrative chain.",
      "Hold continuity of character, world and actions.",
    ],
  },
  reklama: {
    modeIntent: "commercial persuasion",
    modePromptBias: "product focus",
    modeSceneStrategy: "hook-value-payoff",
    modeContinuityBias: "brand consistency",
    planningMindset: "creative strategist",
    continuityStrictness: "strong",
    abstractionAllowance: "low",
    narrativeDiscipline: "high",
    planningRules: [
      "Each scene must carry a communication function.",
      "Do not lose product/message for long stretches.",
      "Follow hook → value → payoff progression.",
    ],
  },
  scenario: {
    modeIntent: "structured storyboard",
    modePromptBias: "clarity",
    modeSceneStrategy: "beat-by-beat",
    modeContinuityBias: "script continuity",
    planningMindset: "screenwriter",
    continuityStrictness: "very_strong",
    abstractionAllowance: "low",
    narrativeDiscipline: "very_high",
    planningRules: [
      "Follow strongest text/story discipline.",
      "Music is secondary to beat-by-beat script logic.",
      "Minimize montage abstraction and keep structural clarity.",
    ],
  },
};

const AUDIO_STORY_POLICIES = {
  lyrics_music: {
    policyLabel: "lyrics_and_music_joint_driver",
    lyricsUsage: "use",
    storySource: "lyrics+music",
    textRoleDefault: "optional enhancer",
    planningRules: [
      "Use lyrics meaning as narrative source when audio is present.",
      "Use music for rhythm, transitions and emotional contour.",
    ],
  },
  music_only: {
    policyLabel: "music_drives_story_lyrics_ignored",
    lyricsUsage: "ignore",
    storySource: "music",
    textRoleDefault: "guide when connected",
    planningRules: [
      "Ignore literal lyrics even if song contains words.",
      "Build story from rhythm, energy, references and selected mode.",
      "Without text node, do mood/energy progression instead of fake literal song plot.",
    ],
  },
  music_plus_text: {
    policyLabel: "text_semantics_with_music_timing",
    lyricsUsage: "ignore",
    storySource: "text+music",
    textRoleDefault: "primary",
    planningRules: [
      "Ignore lyrics semantics.",
      "Text node controls narrative direction.",
      "Music controls timing, pacing, emotion and montage intensity.",
    ],
  },
  speech_narrative: {
    policyLabel: "spoken_semantics_primary",
    lyricsUsage: "ignore",
    storySource: "spoken_semantics",
    textRoleDefault: "secondary support",
    planningRules: [
      "Spoken transcript/narration is the main semantic story source.",
      "Plan scenes beat-by-beat from transcriptText, spokenTextHint and audioSemanticSummary.",
      "Do not drift into generic music-video mood unrelated to the narrated topic.",
      "If there are no character refs and no explicit people in the narration/text, keep visuals environment/object/infrastructure-only.",
    ],
  },
};

export const PROMPT_SYNC_STATUS = {
  synced: "synced",
  needsSync: "needs_sync",
  syncing: "syncing",
  syncError: "sync_error",
};

function computePromptSync({ ru = "", en = "" } = {}) {
  const ruText = String(ru || "").trim();
  const enText = String(en || "").trim();
  if (ruText && enText) return PROMPT_SYNC_STATUS.synced;
  return PROMPT_SYNC_STATUS.needsSync;
}

export function normalizeComfyScenePrompts(scene = {}) {
  const imagePromptRu = String(scene?.imagePromptRu || "").trim();
  const imagePromptEn = String(scene?.imagePromptEn || scene?.imagePrompt || "").trim();
  const videoPromptRu = String(scene?.videoPromptRu || "").trim();
  const videoPromptEn = String(scene?.videoPromptEn || scene?.videoPrompt || "").trim();
  const sceneGoal = String(scene?.sceneGoal || "").trim();
  const sceneText = String(scene?.sceneText || "").trim();
  const sceneMeaning = String(scene?.sceneMeaning || "").trim();
  const visualDescription = String(scene?.visualDescription || "").trim();
  const cameraPlan = String(scene?.cameraPlan || "").trim();
  const motionPlan = String(scene?.motionPlan || "").trim();
  const sfxPlan = String(scene?.sfxPlan || "").trim();
  const storyBeat = String(scene?.storyBeat || "").trim();
  const visualAction = String(scene?.visualAction || "").trim();
  const emotion = String(scene?.emotion || "").trim();
  const cameraIntent = String(scene?.cameraIntent || "").trim();
  const continuityNotes = String(scene?.continuityNotes || scene?.continuity || "").trim();
  const durationSecRaw = Number(scene?.durationSec);
  const durationSec = Number.isFinite(durationSecRaw) ? durationSecRaw : 4;
  const generationDurationSecRaw = Number(scene?.generationDurationSec);
  const generationDurationSec = Number.isFinite(generationDurationSecRaw)
    ? Math.max(1, Math.ceil(generationDurationSecRaw))
    : Math.max(1, Math.ceil(durationSec || 4));
  const plannedGenerator = String(scene?.plannedGenerator || "video").trim() || "video";
  const futureGeneratorHint = String(scene?.futureGeneratorHint || "video").trim() || "video";
  const imagePromptSyncStatus = [PROMPT_SYNC_STATUS.synced, PROMPT_SYNC_STATUS.needsSync, PROMPT_SYNC_STATUS.syncing, PROMPT_SYNC_STATUS.syncError].includes(scene?.imagePromptSyncStatus)
    ? scene.imagePromptSyncStatus
    : computePromptSync({ ru: imagePromptRu, en: imagePromptEn });
  const videoPromptSyncStatus = [PROMPT_SYNC_STATUS.synced, PROMPT_SYNC_STATUS.needsSync, PROMPT_SYNC_STATUS.syncing, PROMPT_SYNC_STATUS.syncError].includes(scene?.videoPromptSyncStatus)
    ? scene.videoPromptSyncStatus
    : computePromptSync({ ru: videoPromptRu, en: videoPromptEn });
  const refsUsed = Array.isArray(scene?.refsUsed)
    ? scene.refsUsed.map((role) => String(role || "").trim()).filter(Boolean)
    : [];
  const activeRefs = Array.isArray(scene?.activeRefs)
    ? scene.activeRefs.map((role) => String(role || "").trim()).filter(Boolean)
    : refsUsed;
  const primaryRole = String(scene?.primaryRole || "").trim();
  const fallbackHero = primaryRole || refsUsed[0] || "";
  const heroEntityId = String(scene?.heroEntityId || fallbackHero).trim();
  const mustAppear = Array.isArray(scene?.mustAppear)
    ? scene.mustAppear.map((role) => String(role || "").trim()).filter(Boolean)
    : (heroEntityId ? [heroEntityId] : refsUsed);
  const promptLanguageStatus = (scene?.promptLanguageStatus && typeof scene.promptLanguageStatus === "object")
    ? scene.promptLanguageStatus
    : {
        image: imagePromptRu && imagePromptEn ? "ru_en_present" : (imagePromptEn ? "ru_missing_en_fallback" : (imagePromptRu ? "en_missing_ru_only" : "missing_both")),
        video: videoPromptRu && videoPromptEn ? "ru_en_present" : (videoPromptEn ? "ru_missing_en_fallback" : (videoPromptRu ? "en_missing_ru_only" : "missing_both")),
      };
  const ruPromptMissing = (scene?.ruPromptMissing && typeof scene.ruPromptMissing === "object")
    ? scene.ruPromptMissing
    : { image: !imagePromptRu, video: !videoPromptRu };
  const enPromptPresent = (scene?.enPromptPresent && typeof scene.enPromptPresent === "object")
    ? scene.enPromptPresent
    : { image: Boolean(imagePromptEn), video: Boolean(videoPromptEn) };
  return {
    ...scene,
    imagePromptRu,
    imagePromptEn,
    videoPromptRu,
    videoPromptEn,
    sceneText,
    sceneMeaning,
    visualDescription,
    cameraPlan,
    motionPlan,
    sfxPlan,
    sceneGoal,
    storyBeat,
    visualAction,
    emotion,
    cameraIntent,
    continuityNotes,
    durationSec,
    generationDurationSec,
    plannedGenerator,
    futureGeneratorHint,
    imagePrompt: imagePromptEn,
    videoPrompt: videoPromptEn,
    promptLanguageStatus,
    ruPromptMissing,
    enPromptPresent,
    refsUsed,
    activeRefs,
    refUsageReason: String(scene?.refUsageReason || "").trim(),
    characterRoleLogic: Array.isArray(scene?.characterRoleLogic) ? scene.characterRoleLogic : [],
    primaryRole,
    heroEntityId,
    mustAppear,
    videoPanelOpen: Boolean(scene?.videoPanelOpen || String(scene?.videoUrl || "").trim()),
    imagePromptSyncStatus,
    videoPromptSyncStatus,
  };
}


export function normalizeRenderProfile(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return RENDER_PROFILE_OPTIONS.includes(normalized) ? normalized : "comfy image";
}

export function normalizeAudioStoryMode(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return AUDIO_STORY_MODE_OPTIONS.includes(normalized) ? normalized : "lyrics_music";
}

export function normalizeRoleList(input = []) {
  const unique = new Set();
  (Array.isArray(input) ? input : []).forEach((raw) => {
    const role = REFERENCE_HANDLE_TO_ROLE[raw] || String(raw || "").trim().toLowerCase();
    if (ROLE_PRIORITY.includes(role)) unique.add(role);
  });
  return ROLE_PRIORITY.filter((role) => unique.has(role));
}

export function deriveSceneRoles({ refsByRole = {} } = {}) {
  const cast = normalizeRoleList(Object.keys(refsByRole).filter((role) => Array.isArray(refsByRole[role]) && refsByRole[role].length > 0));
  const castSubjects = cast.filter((role) => ["character_1", "character_2", "character_3", "animal", "group"].includes(role));
  const primarySubject = castSubjects[0] || cast[0] || "character_1";
  const secondarySubjects = castSubjects.filter((role) => role !== primarySubject);
  return { primarySubject, secondarySubjects, cast };
}

export function canGenerateComfyImage(plannerInput = {}) {
  const refsByRole = plannerInput?.refsByRole || {};
  return VISUAL_ANCHOR_ROLES.some((role) => Array.isArray(refsByRole[role]) && refsByRole[role].length > 0);
}

export function inferPropAnchorLabel(refsByRole = {}) {
  const firstProp = (Array.isArray(refsByRole.props) ? refsByRole.props : [])[0];
  return String(firstProp?.name || "").trim() || "hero prop";
}

function normalizeStoryHeuristicText(value = "") {
  return String(value || "").trim().toLowerCase().replace(/\s+/g, " ");
}

function hasStoryMarker(input = "", markers = []) {
  if (!input) return false;
  return markers.some((marker) => input.includes(marker));
}

export function detectStoryControlMode({ meaningfulText = "", meaningfulAudio = "", refsByRole = {} } = {}) {
  const text = normalizeStoryHeuristicText(meaningfulText);
  const hasText = !!text;
  const hasAudio = !!String(meaningfulAudio || "").trim();
  const hasRefs = Object.values(refsByRole || {}).some((items) => Array.isArray(items) && items.length > 0);
  if (!hasText && !hasAudio && !hasRefs) return "insufficient_input";
  if (!hasText && !hasAudio && hasRefs) return "refs_mode_generated";
  if (hasText && !hasAudio) return "text_override";
  if (!hasText && hasAudio) return "audio_primary";
  if (hasStoryMarker(text, STORY_OVERRIDE_MARKERS)) return "text_override";
  if (hasStoryMarker(text, STORY_ENHANCEMENT_MARKERS)) return "audio_enhanced_by_text";
  return "hybrid_balanced";
}

export function deriveStoryNarrativeRoles(storyControlMode = "insufficient_input") {
  if (storyControlMode === "text_override") return { textNarrativeRole: "story_mission_primary", audioNarrativeRole: "rhythm_emotion_support" };
  if (storyControlMode === "audio_primary") return { textNarrativeRole: "optional_intent_hint", audioNarrativeRole: "timeline_and_emotional_backbone" };
  if (storyControlMode === "audio_enhanced_by_text") return { textNarrativeRole: "dramatic_boost", audioNarrativeRole: "timeline_backbone" };
  if (storyControlMode === "refs_mode_generated") return { textNarrativeRole: "none", audioNarrativeRole: "none" };
  return { textNarrativeRole: "shared_story_driver", audioNarrativeRole: "shared_timeline_driver" };
}

export function buildStoryMissionSummary({ meaningfulText = "", storyControlMode = "insufficient_input", mode = "clip" } = {}) {
  const text = String(meaningfulText || "").trim();
  if (text) return text.slice(0, 220);
  if (storyControlMode === "audio_primary") return `Build ${mode} scenes from audio rhythm and emotional contour.`;
  if (storyControlMode === "refs_mode_generated") return `Build ${mode} scenes from references and mode semantics.`;
  return `Build ${mode} scenes with coherent narrative progression.`;
}

export function getModeSemantics(mode = "clip") {
  const key = String(mode || "clip").toLowerCase();
  const resolved = MODE_RULES[key] || MODE_RULES.clip;
  return { ...resolved, modeRules: resolved };
}

export function getStyleSemantics(stylePreset = "realism") {
  const key = String(stylePreset || "realism").toLowerCase();
  const map = {
    realism: "natural light and believable texture",
    film: "cinematic grading and dramatic light",
    neon: "high contrast neon accents",
    glossy: "clean premium commercial polish",
    soft: "gentle light and airy mood",
  };
  return {
    styleSummary: map[key] || map.realism,
    styleContinuity: `Keep ${key} style continuity across all scenes.`,
    styleRules: {
      scope: "visual_filter_only",
      planningRules: [
        "Style affects visual treatment, light, color and finish.",
        "Style must not override mode story structure.",
        "Keep style continuity unless freeze style is disabled by user flow.",
      ],
    },
  };
}

export function getAudioStoryPolicy(audioStoryMode = "lyrics_music", { hasText = false, hasAudio = false } = {}) {
  const modeKey = normalizeAudioStoryMode(audioStoryMode);
  const base = AUDIO_STORY_POLICIES[modeKey] || AUDIO_STORY_POLICIES.lyrics_music;
  const warnings = [];
  if (modeKey === "music_plus_text" && hasAudio && !hasText) {
    warnings.push("music_plus_text selected without text: lyrics are ignored, fallback to music-only progression.");
  }
  if (modeKey === "music_only" && hasAudio && !hasText) {
    warnings.push("music_only without text: planner should use mood/energy progression.");
  }
  if (modeKey === "speech_narrative" && hasAudio) {
    warnings.push("speech_narrative: spoken audio semantics should drive scene planning; text/refs are secondary support.");
  }
  return { ...base, warnings };
}

export function deriveTextInfluence({ mode = "clip", audioStoryMode = "lyrics_music", hasText = false, storyControlMode = "insufficient_input" } = {}) {
  if (!hasText) return "none";
  if (storyControlMode === "text_override") return "override";
  if (mode === "scenario") return "override";
  if (mode === "kino") return audioStoryMode === "music_only" ? "guide" : "override";
  if (mode === "reklama") return audioStoryMode === "music_only" ? "guide" : "override";
  if (audioStoryMode === "speech_narrative") return "secondary";
  if (mode === "clip") return audioStoryMode === "music_plus_text" ? "guide" : "enhancer";
  return "guide";
}

export function buildComfyGlobalContinuity({ plannerInput = {}, refsByRole = {}, sceneRoleModel = {} } = {}) {
  const mode = plannerInput.mode || "clip";
  const style = plannerInput.stylePreset || "realism";
  const cast = (sceneRoleModel.cast || []).join(", ") || "character_1";
  const world = ["location", "props", "style"].filter((role) => (refsByRole[role] || []).length > 0).join(", ") || "implicit world";
  return `Mode ${mode}. Style ${style}. Keep cast (${cast}) and world anchors (${world}) consistent scene-to-scene.`;
}

function getRoleLabelRu(role = "") {
  const map = {
    character_1: "главный герой",
    character_2: "второй герой",
    character_3: "третий герой",
    animal: "животное",
    group: "группа",
    location: "локация",
    props: "реквизит",
    style: "стиль",
  };
  return map[String(role || "").trim().toLowerCase()] || "герой";
}

function clampSceneDuration(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 4;
  return Math.max(3, Math.min(6, Math.round(num)));
}

function buildSceneDurations(count, { mode = "clip", audioDurationSec = null, hasAudio = false } = {}) {
  const modePatterns = {
    clip: [3, 4, 5, 3, 6, 4],
    kino: [4, 5, 4, 6, 5, 4],
    reklama: [3, 4, 4, 5, 4, 3],
    scenario: [4, 4, 5, 5, 4, 6],
  };
  const pattern = modePatterns[String(mode || "clip").toLowerCase()] || modePatterns.clip;
  const durations = Array.from({ length: count }).map((_, idx) => pattern[idx % pattern.length]);
  if (!hasAudio || !Number.isFinite(Number(audioDurationSec)) || Number(audioDurationSec) <= 0) {
    return durations.map(clampSceneDuration);
  }
  const targetAverage = Math.max(3, Math.min(6, Number(audioDurationSec) / Math.max(1, count)));
  return durations.map((duration, idx) => {
    const patterned = duration + (targetAverage - 4);
    const rhythmOffset = ((idx % 3) - 1) * 0.35;
    return clampSceneDuration(patterned + rhythmOffset);
  });
}

function pickSceneBlueprint(mode = "clip", idx = 0, count = 1) {
  const position = count <= 1 ? 0 : idx / Math.max(1, count - 1);
  const modeKey = String(mode || "clip").toLowerCase();
  const clipBlueprints = [
    { sceneGoal: "Мгновенно задать ритм и внимание", storyBeat: "История открывается сильным визуальным импульсом", visualAction: "Герой резко входит в кадр и сразу запускает движение сцены", emotion: "Импульс, предвкушение", cameraIntent: "Быстрый заход камеры, энергичный параллакс", continuityFocus: "Сохранить ключевой образ героя и стиль света", motionLevel: "high", dialogueLike: false },
    { sceneGoal: "Усилить драйв и смену импульса", storyBeat: "Герой проходит через первый эмоциональный подъём", visualAction: "Герой ускоряется, взаимодействует с пространством и меняет траекторию", emotion: "Азарт, напряжение", cameraIntent: "Ручная камера с коротким рывком и смещением", continuityFocus: "Сохранить ту же локацию-мотив и узнаваемую пластику движения", motionLevel: "high", dialogueLike: false },
    { sceneGoal: "Показать эмоциональный акцент", storyBeat: "История делает акцент на внутреннем состоянии героя", visualAction: "Герой на секунду задерживается, реагирует взглядом и снова двигается", emotion: "Эмоциональный всплеск, уязвимость", cameraIntent: "Средний план с мягким подлетом камеры", continuityFocus: "Сохранить костюм, настроение света и мотив предыдущей сцены", motionLevel: "medium", dialogueLike: true },
    { sceneGoal: "Подготовить следующий визуальный удар", storyBeat: "Сюжетный ритм собирается перед новым пиком", visualAction: "Пространство и герой входят в синхронное движение перед переходом", emotion: "Нарастание, ожидание", cameraIntent: "Камера тянет вперёд и готовит переход на следующий бит", continuityFocus: "Сохранить доминирующие цвета, героя и направление движения", motionLevel: "medium", dialogueLike: false },
    { sceneGoal: "Дать кульминационный всплеск", storyBeat: "История достигает самого яркого действия", visualAction: "Герой совершает самый выразительный жест или рывок в сцене", emotion: "Эйфория, максимальное напряжение", cameraIntent: "Широкий динамичный проход с ощущением кульминации", continuityFocus: "Сохранить кульминационный образ героя и окружение без разрыва", motionLevel: "high", dialogueLike: false },
    { sceneGoal: "Закрепить послевкусие и выход", storyBeat: "История завершает фразу и оставляет визуальный след", visualAction: "Герой замедляется, фиксирует итог момента и растворяется в финальном движении", emotion: "Освобождение, послевкусие", cameraIntent: "Плавный отъезд камеры с финальной фиксацией образа", continuityFocus: "Сохранить финальный образ, свет и тот же мир сцены", motionLevel: "low", dialogueLike: false },
  ];
  const kinoBlueprints = [
    { sceneGoal: "Завязать причину события", storyBeat: "Герой сталкивается с фактом, который запускает действие", visualAction: "Герой замечает важную деталь и принимает решение двигаться дальше", emotion: "Собранность, тревога", cameraIntent: "Наблюдающая камера с акцентом на причинную деталь", continuityFocus: "Сохранить ту же ситуацию, героя и предметный контекст", motionLevel: "medium", dialogueLike: false },
    { sceneGoal: "Показать развитие решения", storyBeat: "Решение героя приводит к следующему шагу", visualAction: "Герой действует целенаправленно и меняет состояние сцены", emotion: "Напряжение, контроль", cameraIntent: "Поступательное движение камеры вслед за действием", continuityFocus: "Сохранить направление действия и логику пространства", motionLevel: "medium", dialogueLike: false },
    { sceneGoal: "Зафиксировать реакцию и ставку", storyBeat: "История раскрывает, что поставлено на карту", visualAction: "Герой останавливается, считывает последствия и готовится к следующему шагу", emotion: "Сомнение, давление", cameraIntent: "Сдержанный средний план с фокусом на лице", continuityFocus: "Сохранить ту же одежду, локацию и причинную связь", motionLevel: "low", dialogueLike: true },
    { sceneGoal: "Довести конфликт до перелома", storyBeat: "Сюжет приходит к точке, где возврата уже нет", visualAction: "Герой совершает действие, после которого ситуация меняется необратимо", emotion: "Решимость, драматическое напряжение", cameraIntent: "Камера усиливает перелом через наезд и фиксацию жеста", continuityFocus: "Сохранить конфликтный объект и тот же драматический вектор", motionLevel: "high", dialogueLike: false },
    { sceneGoal: "Закрепить следствие", storyBeat: "История показывает результат принятого решения", visualAction: "Герой проживает итог и переводит историю к следующей точке", emotion: "Тяжесть, разрядка", cameraIntent: "Плавное сопровождение с ощущением последствия", continuityFocus: "Сохранить следы действия и неизменность мира", motionLevel: "low", dialogueLike: true },
  ];
  const reklamaBlueprints = [
    { sceneGoal: "Сразу захватить внимание", storyBeat: "Сцена мгновенно формулирует главный визуальный крючок", visualAction: "Герой или продукт появляются максимально выразительно и читаемо", emotion: "Интерес, импульс", cameraIntent: "Короткий яркий заход камеры на ключевой объект", continuityFocus: "Сохранить брендовые цвета, героя и предмет в центре внимания", motionLevel: "medium", dialogueLike: false },
    { sceneGoal: "Показать ключевую пользу", storyBeat: "История объясняет, зачем объект нужен зрителю", visualAction: "Герой наглядно использует объект и демонстрирует полезный результат", emotion: "Уверенность, удовольствие", cameraIntent: "Чистый демонстрационный ракурс с акцентом на действие", continuityFocus: "Сохранить продукт, детали интерфейса или формы без изменений", motionLevel: "medium", dialogueLike: false },
    { sceneGoal: "Укрепить доверие через эмоцию", storyBeat: "Сцена показывает человеческую реакцию на ценность", visualAction: "Герой реагирует на эффект продукта и делится эмоциональным откликом", emotion: "Облегчение, радость", cameraIntent: "Средний план с мягким приближением к эмоции", continuityFocus: "Сохранить образ героя, продукт и чистую визуальную подачу", motionLevel: "low", dialogueLike: true },
    { sceneGoal: "Подвести к запоминанию оффера", storyBeat: "История собирает визуальные аргументы в понятный итог", visualAction: "Герой завершает действие, а продукт остаётся главным визуальным акцентом", emotion: "Удовлетворение, уверенность", cameraIntent: "Плавный финальный наезд с премиальной подачей", continuityFocus: "Сохранить брендовый свет, упаковку и чистый фон", motionLevel: "low", dialogueLike: false },
  ];
  const scenarioBlueprints = [
    { sceneGoal: "Чётко ввести исходную ситуацию", storyBeat: "История устанавливает, кто где находится и что происходит", visualAction: "Герой входит в обозначенное пространство и начинает конкретное действие", emotion: "Собранность, ожидание", cameraIntent: "Ясный установочный план без визуального шума", continuityFocus: "Сохранить географию сцены, образ героя и реквизит", motionLevel: "low", dialogueLike: false },
    { sceneGoal: "Перевести сцену к следующему биту", storyBeat: "Герой выполняет шаг, который логично ведёт к продолжению", visualAction: "Герой взаимодействует с объектом или персонажем и меняет ситуацию", emotion: "Фокус, внутреннее давление", cameraIntent: "Функциональное сопровождение действия без лишней экспрессии", continuityFocus: "Сохранить позицию объектов и последовательность действий", motionLevel: "medium", dialogueLike: false },
    { sceneGoal: "Показать реакцию и уточнение смысла", storyBeat: "История фиксирует ответ героя на произошедшее", visualAction: "Герой останавливается, реагирует и формулирует следующий импульс действием", emotion: "Сдержанное напряжение, размышление", cameraIntent: "Средний план с читаемой мимикой и ясным фокусом", continuityFocus: "Сохранить ось сцены, костюм и предметные связи", motionLevel: "low", dialogueLike: true },
    { sceneGoal: "Подготовить точный переход вперёд", storyBeat: "Сюжет завершает текущий шаг и переводит историю дальше", visualAction: "Герой завершает действие и визуально открывает следующий эпизод", emotion: "Решимость, движение вперёд", cameraIntent: "Плавное доведение кадра до точки следующего перехода", continuityFocus: "Сохранить логическую последовательность и состояние пространства", motionLevel: "medium", dialogueLike: false },
  ];
  const blueprintMap = { clip: clipBlueprints, kino: kinoBlueprints, reklama: reklamaBlueprints, scenario: scenarioBlueprints };
  const blueprints = blueprintMap[modeKey] || clipBlueprints;
  const scaledIndex = Math.min(blueprints.length - 1, Math.max(0, Math.round(position * (blueprints.length - 1))));
  return blueprints[scaledIndex];
}

function inferSceneEmotionTag(emotion = "") {
  const value = String(emotion || "").toLowerCase();
  if (value.includes("эйфор") || value.includes("радост") || value.includes("удоволь")) return "uplift";
  if (value.includes("трев") || value.includes("напряж") || value.includes("давление")) return "tension";
  if (value.includes("уязв") || value.includes("сомнен") || value.includes("размыш")) return "intimate";
  return "steady";
}

function pickFutureGeneratorHint({ mode = "clip", hasAudio = false, audioStoryMode = "lyrics_music", blueprint = {} } = {}) {
  if (mode === "clip" && hasAudio && audioStoryMode !== "music_only" && (blueprint.motionLevel === "high" || blueprint.emotion?.includes("Эйфория") || blueprint.emotion?.includes("Азарт"))) {
    return "singing_candidate";
  }
  if (blueprint.dialogueLike || inferSceneEmotionTag(blueprint.emotion) === "intimate") {
    return "speech_candidate";
  }
  if (blueprint.motionLevel === "high" || String(blueprint.visualAction || "").includes("меняет") || String(blueprint.visualAction || "").includes("рывок")) {
    return "transform_candidate";
  }
  return "video";
}

function buildContinuityNotes({ plannerInput = {}, plannerMeta = {}, primaryRole = "character_1", blueprint = {} } = {}) {
  const refsByRole = plannerInput?.refsByRole || {};
  const locationAnchor = Array.isArray(refsByRole.location) && refsByRole.location.length > 0 ? "та же локация" : "тот же тип пространства";
  const propAnchor = Array.isArray(refsByRole.props) && refsByRole.props.length > 0 ? "те же ключевые предметы" : "без смены ключевых объектов";
  const styleAnchor = plannerInput?.stylePreset ? `стиль ${plannerInput.stylePreset}` : "тот же визуальный стиль";
  const castAnchor = primaryRole ? `тот же ${getRoleLabelRu(primaryRole)}` : "тот же герой";
  return [castAnchor, locationAnchor, propAnchor, styleAnchor, blueprint.continuityFocus].filter(Boolean).join(", ");
}

function buildPromptPackage({ idx = 0, stylePreset = "realism", primaryRole = "character_1", plannerInput = {}, plannerMeta = {}, blueprint = {}, continuityNotes = "" } = {}) {
  const storyMission = String(plannerInput?.storyMissionSummary || plannerMeta?.storyMissionSummary || "").trim();
  const primaryRoleRu = getRoleLabelRu(primaryRole);
  const worldHint = Array.isArray(plannerInput?.refsByRole?.location) && plannerInput.refsByRole.location.length > 0 ? "anchored to the established location" : "in a consistent cinematic environment";
  const styleSummary = String(plannerMeta?.styleRules?.styleSummary || plannerInput?.styleSemantics?.styleSummary || "").trim();
  const imagePromptEn = [
    `Create the keyframe for scene ${idx + 1}.`,
    `Main subject: ${primaryRole}.`,
    `Show ${blueprint.visualAction ? blueprint.visualAction.toLowerCase() : "a decisive story moment"}.`,
    `Environment: ${worldHint}.`,
    `Mood: ${blueprint.emotion || "focused cinematic emotion"}.`,
    `Style: ${stylePreset}${styleSummary ? `, ${styleSummary}` : ""}.`,
    storyMission ? `Story context: ${storyMission}.` : "",
  ].filter(Boolean).join(" ");
  const videoPromptEn = [
    `Animate scene ${idx + 1} as a coherent story beat.`,
    `Action: ${blueprint.visualAction || "keep the hero moving through the scene"}.`,
    `Camera: ${blueprint.cameraIntent || "maintain motivated cinematic movement"}.`,
    `Emotional progression: start with ${blueprint.emotion || "focused emotion"} and push toward the next beat.`,
    continuityNotes ? `Continuity: ${continuityNotes}.` : "",
    `Keep the result ready for video generation.`,
  ].filter(Boolean).join(" ");
  const imagePromptRu = `Ключевой кадр ${idx + 1}: ${blueprint.visualAction || "решающий момент сцены"}. В кадре ${primaryRoleRu}, окружение без разрыва по стилю ${stylePreset}.`;
  const videoPromptRu = `Внутри сцены ${idx + 1}: ${blueprint.visualAction || "герой продолжает действие"}, камера — ${String(blueprint.cameraIntent || "мотивированное движение").toLowerCase()}, эмоция — ${String(blueprint.emotion || "сфокусированное напряжение").toLowerCase()}.`;
  return { imagePromptEn, videoPromptEn, imagePromptRu, videoPromptRu };
}

export function buildComfyScenesFromPlanner({ plannerInput = {}, plannerMeta = {} } = {}) {
  const count = Number(plannerMeta?.summary?.sceneCount || 6);
  const sceneCount = Math.max(1, Math.min(12, count));
  const mode = String(plannerInput?.mode || plannerMeta?.mode || "clip").toLowerCase();
  const modeRules = plannerInput?.modeRules || plannerMeta?.modeRules || MODE_RULES[mode] || MODE_RULES.clip;
  const primaryRole = plannerMeta?.sceneRoleModel?.primarySubject || "character_1";
  const secondaryRoles = plannerMeta?.sceneRoleModel?.secondarySubjects || [];
  const audioDurationSec = Number(plannerInput?.audioDurationSec || plannerMeta?.audioDurationSec || 0);
  const hasAudio = audioDurationSec > 0 || !!String(plannerInput?.meaningfulAudio || "").trim();
  const durations = buildSceneDurations(sceneCount, { mode, audioDurationSec, hasAudio });
  let cursorSec = 0;
  return Array.from({ length: sceneCount }).map((_, idx) => {
    const blueprint = pickSceneBlueprint(mode, idx, sceneCount);
    const continuityNotes = buildContinuityNotes({ plannerInput, plannerMeta, primaryRole, blueprint });
    const { imagePromptEn, videoPromptEn, imagePromptRu, videoPromptRu } = buildPromptPackage({
      idx,
      stylePreset: plannerInput.stylePreset || plannerMeta.stylePreset || "realism",
      primaryRole,
      plannerInput,
      plannerMeta,
      blueprint,
      continuityNotes,
    });
    const durationSec = durations[idx];
    const generationDurationSec = Math.ceil(durationSec);
    const startSec = cursorSec;
    const endSec = startSec + durationSec;
    cursorSec = endSec;
    const futureGeneratorHint = pickFutureGeneratorHint({
      mode,
      hasAudio,
      audioStoryMode: plannerInput?.audioStoryMode || plannerMeta?.audioStoryMode || "lyrics_music",
      blueprint,
    });
    const planningRulesSummary = Array.isArray(modeRules?.planningRules) ? modeRules.planningRules.slice(0, 2).join(" | ") : "";
    return {
      sceneId: `comfy-scene-${idx + 1}`,
      title: `Scene ${idx + 1}`,
      startSec,
      endSec,
      durationSec,
      generationDurationSec,
      sceneNarrativeStep: `step_${idx + 1}`,
      sceneGoal: blueprint.sceneGoal,
      storyMission: plannerInput.storyMissionSummary || "",
      sceneOutputRule: "scene image first",
      primaryRole,
      secondaryRoles,
      continuity: plannerMeta?.globalContinuity || "",
      imagePrompt: imagePromptEn,
      videoPrompt: videoPromptEn,
      imagePromptRu,
      imagePromptEn,
      videoPromptRu,
      videoPromptEn,
      imagePromptSyncStatus: PROMPT_SYNC_STATUS.synced,
      videoPromptSyncStatus: PROMPT_SYNC_STATUS.synced,
      refsUsed: [],
      refDirectives: {},
      heroEntityId: primaryRole,
      supportEntityIds: secondaryRoles,
      mustAppear: [primaryRole],
      mustNotAppear: [],
      environmentLock: true,
      styleLock: true,
      identityLock: true,
      roleSelectionReason: "mock_default_role_selection",
      storyBeat: blueprint.storyBeat,
      visualAction: blueprint.visualAction,
      emotion: blueprint.emotion,
      mustShow: [primaryRole],
      mustKeep: ["identity continuity", "style continuity"],
      cameraIntent: blueprint.cameraIntent,
      continuityNotes,
      transitionLogic: plannerInput?.modeContinuityBias || modeRules?.modeContinuityBias || "coherent transition",
      renderPriority: idx === 0 ? "hook" : "narrative_consistency",
      speechRelation: plannerInput?.audioStoryMode === "music_plus_text"
        ? "text-led over music timing"
        : (plannerInput?.audioStoryMode === "speech_narrative" ? "spoken-semantics-led" : "music-led"),
      abstractionLevel: modeRules?.abstractionAllowance || "medium",
      narrativeDiscipline: modeRules?.narrativeDiscipline || "medium",
      planningRulesApplied: planningRulesSummary,
      plannedGenerator: "video",
      futureGeneratorHint,
      imageUrl: "",
      videoUrl: "",
      videoPanelOpen: false,
      plannerMeta,
    };
  });
}

export function buildMockComfyScenes(meta = {}) {
  const plannerInput = meta?.plannerInput || {};
  const plannerMeta = {
    mode: String(plannerInput?.mode || meta?.mode || "clip"),
    output: normalizeRenderProfile(plannerInput?.output || meta?.output || "comfy image"),
    stylePreset: String(plannerInput?.stylePreset || meta?.stylePreset || "realism"),
    narrativeSource: String(plannerInput?.narrativeSource || meta?.narrativeSource || "none"),
    timelineSource: String(plannerInput?.timelineSource || meta?.timelineSource || "logic"),
    warnings: Array.isArray(meta?.warnings) ? meta.warnings : [],
    summary: meta?.summary || {},
    sceneRoleModel: meta?.sceneRoleModel || deriveSceneRoles({ refsByRole: plannerInput?.refsByRole || {} }),
    referenceSummary: meta?.referenceSummary || {},
    storyControlMode: String(plannerInput?.storyControlMode || meta?.storyControlMode || "insufficient_input"),
    storyMissionSummary: String(plannerInput?.storyMissionSummary || meta?.storyMissionSummary || ""),
    textNarrativeRole: String(plannerInput?.textNarrativeRole || meta?.textNarrativeRole || ""),
    audioNarrativeRole: String(plannerInput?.audioNarrativeRole || meta?.audioNarrativeRole || ""),
    audioStoryMode: normalizeAudioStoryMode(plannerInput?.audioStoryMode || meta?.audioStoryMode || "lyrics_music"),
    modeRules: plannerInput?.modeRules || meta?.modeRules || MODE_RULES[String(plannerInput?.mode || meta?.mode || "clip").toLowerCase()] || MODE_RULES.clip,
    styleRules: plannerInput?.styleRules || meta?.styleRules || {},
    audioStoryPolicy: plannerInput?.audioStoryPolicy || meta?.audioStoryPolicy || AUDIO_STORY_POLICIES.lyrics_music,
    textInfluence: String(plannerInput?.textInfluence || meta?.textInfluence || "none"),
    sceneContractSchemaVersion: "comfy_scene_contract_v3",
  };
  plannerMeta.globalContinuity = buildComfyGlobalContinuity({ plannerInput, refsByRole: plannerInput?.refsByRole || {}, sceneRoleModel: plannerMeta.sceneRoleModel });
  return buildComfyScenesFromPlanner({ plannerInput, plannerMeta });
}

function hasSemanticHints(value) {
  if (Array.isArray(value)) return value.some((item) => !!String(item || "").trim());
  if (value && typeof value === "object") return Object.values(value).some((item) => !!String(item || "").trim());
  return !!String(value || "").trim();
}

export function normalizeStoryboardSourceValue(value, fallback = "none") {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "audio_primary") return "audio";
  if (normalized === "text_primary") return "text";
  if (normalized === "audio" || normalized === "text" || normalized === "none") return normalized;
  return fallback;
}

export function deriveComfyBrainState({ nodeId = "", nodeData = {}, nodesNow = [], edgesNow = [], normalizeRefDataFn } = {}) {
  const incoming = (edgesNow || []).filter((e) => e.target === nodeId);
  const pickConnectedNode = (handleId) => {
    const edge = [...incoming].reverse().find((e) => String(e.targetHandle || "") === handleId);
    return edge ? (nodesNow.find((x) => x.id === edge.source) || null) : null;
  };

  const comfyRefConfigByHandle = {
    ref_character_1: { nodeType: "refNode", kind: "ref_character" },
    ref_location: { nodeType: "refNode", kind: "ref_location" },
    ref_style: { nodeType: "refNode", kind: "ref_style" },
    ref_props: { nodeType: "refNode", kind: "ref_items" },
    ref_character_2: { nodeType: "refCharacter2" },
    ref_character_3: { nodeType: "refCharacter3" },
    ref_animal: { nodeType: "refAnimal" },
    ref_group: { nodeType: "refGroup" },
  };

  const normalizeNodeRefs = (sourceNode, cfg = {}) => {
    if (!sourceNode || sourceNode?.type !== cfg.nodeType) return [];
    if (cfg.kind && sourceNode?.data?.kind !== cfg.kind) return [];
    if (cfg.nodeType === "refNode" && typeof normalizeRefDataFn === "function") {
      return normalizeRefDataFn(sourceNode?.data || {}, cfg.kind || "").refs;
    }
    const refs = Array.isArray(sourceNode?.data?.refs) ? sourceNode.data.refs : [];
    return refs
      .map((item) => {
        if (typeof item === "string") return { url: String(item || "").trim(), name: "" };
        return { url: String(item?.url || "").trim(), name: String(item?.name || "").trim() };
      })
      .filter((item) => !!item.url);
  };
  const resolveRefStatus = (sourceNode, refs = []) => {
    const refsCount = Array.isArray(refs) ? refs.length : 0;
    const raw = String(sourceNode?.data?.refStatus || "").trim().toLowerCase();
    const shortLabel = String(sourceNode?.data?.refShortLabel || "").trim();
    const refAnalyzedAt = String(sourceNode?.data?.refAnalyzedAt || "").trim();
    const refAnalysisError = String(sourceNode?.data?.refAnalysisError || "").trim();
    const hasHiddenProfile = !!(sourceNode?.data?.refHiddenProfile && typeof sourceNode.data.refHiddenProfile === "object");
    const hasAnalysisSignals = !!shortLabel || hasHiddenProfile || !!refAnalyzedAt;
    if (refAnalysisError || raw === "error") {
      return { status: "error", rawRefStatus: raw, refsCount, shortLabel, hasHiddenProfile, hasAnalyzedAt: !!refAnalyzedAt, warningLabel: "" };
    }
    if (!refsCount) {
      return { status: "empty", rawRefStatus: raw, refsCount, shortLabel, hasHiddenProfile, hasAnalyzedAt: !!refAnalyzedAt, warningLabel: "" };
    }
    if (raw === "loading") {
      return { status: "loading", rawRefStatus: raw, refsCount, shortLabel, hasHiddenProfile, hasAnalyzedAt: !!refAnalyzedAt, warningLabel: "" };
    }
    const status = (raw === "ready" || hasAnalysisSignals) ? "ready" : "draft";
    return { status, rawRefStatus: raw, refsCount, shortLabel, hasHiddenProfile, hasAnalyzedAt: !!refAnalyzedAt, warningLabel: "" };
  };
  const refNodesByRole = {
    character_1: pickConnectedNode("ref_character_1"),
    character_2: pickConnectedNode("ref_character_2"),
    character_3: pickConnectedNode("ref_character_3"),
    animal: pickConnectedNode("ref_animal"),
    group: pickConnectedNode("ref_group"),
    location: pickConnectedNode("ref_location"),
    style: pickConnectedNode("ref_style"),
    props: pickConnectedNode("ref_props"),
  };
  const roleDraftMessages = {
    character_1: "добавьте персонажа",
    character_2: "добавьте персонажа",
    character_3: "добавьте персонажа",
    animal: "добавьте животное",
    group: "добавьте группу",
    props: "добавьте предмет",
    location: "добавьте локацию",
    style: "добавьте стиль",
  };
  const refConnectionStates = Object.fromEntries(Object.entries(refNodesByRole).map(([role, sourceNode]) => {
    const cfg = role === "character_1" ? comfyRefConfigByHandle.ref_character_1
      : role === "character_2" ? comfyRefConfigByHandle.ref_character_2
      : role === "character_3" ? comfyRefConfigByHandle.ref_character_3
      : role === "animal" ? comfyRefConfigByHandle.ref_animal
      : role === "group" ? comfyRefConfigByHandle.ref_group
      : role === "location" ? comfyRefConfigByHandle.ref_location
      : role === "style" ? comfyRefConfigByHandle.ref_style
      : comfyRefConfigByHandle.ref_props;
    const refs = normalizeNodeRefs(sourceNode, cfg);
    const resolved = resolveRefStatus(sourceNode, refs);
    const status = String(resolved?.status || "draft");
    const warningLabel = status === "ready" ? "" : (roleDraftMessages[role] || "добавьте реф");
    const shortLabel = String(resolved?.shortLabel || sourceNode?.data?.refShortLabel || "").trim();
    return [role, {
      connected: !!sourceNode,
      status,
      refs,
      shortLabel,
      warningLabel,
      rawRefStatus: String(resolved?.rawRefStatus || ""),
      refsCount: Number(resolved?.refsCount || 0),
      hasHiddenProfile: !!resolved?.hasHiddenProfile,
      hasAnalyzedAt: !!resolved?.hasAnalyzedAt,
      error: String(sourceNode?.data?.refAnalysisError || "").trim(),
      hiddenProfile: sourceNode?.data?.refHiddenProfile && typeof sourceNode.data.refHiddenProfile === "object" ? sourceNode.data.refHiddenProfile : null,
    }];
  }));
  const refsByRole = Object.fromEntries(Object.entries(refConnectionStates).map(([role, meta]) => [role, meta.status === "ready" ? meta.refs : []]));

  const audioNode = pickConnectedNode("audio");
  const textNode = pickConnectedNode("text");
  const modeValue = String(nodeData?.mode || "clip").toLowerCase();
  const plannerMode = String(nodeData?.plannerMode || "legacy").trim().toLowerCase() === "gemini_only" ? "gemini_only" : "legacy";
  const outputValue = normalizeRenderProfile(nodeData?.output || "comfy image");
  const audioStoryMode = normalizeAudioStoryMode(nodeData?.audioStoryMode || "lyrics_music");
  const stylePreset = String(nodeData?.styleKey || "realism").toLowerCase();
  const freezeStyle = !!nodeData?.freezeStyle;
  const meaningfulAudio = audioNode?.type === "audioNode" ? String(audioNode?.data?.audioUrl || "").trim() : "";
  const audioDurationRaw = Number(audioNode?.data?.audioDurationSec || 0);
  const meaningfulAudioDurationSec = Number.isFinite(audioDurationRaw) && audioDurationRaw > 0 ? audioDurationRaw : null;
  const meaningfulText = textNode?.type === "textNode" ? String(textNode?.data?.textValue || "").trim() : "";
  const lyricsText = audioNode?.type === "audioNode" ? String(audioNode?.data?.lyricsText || "").trim() : "";
  const transcriptText = audioNode?.type === "audioNode" ? String(audioNode?.data?.transcriptText || "").trim() : "";
  const spokenTextHint = audioNode?.type === "audioNode" ? String(audioNode?.data?.spokenTextHint || "").trim() : "";
  const audioSemanticSummary = audioNode?.type === "audioNode" ? String(audioNode?.data?.audioSemanticSummary || "").trim() : "";
  const audioSemanticHints = audioNode?.type === "audioNode" ? (audioNode?.data?.audioSemanticHints || "") : "";
  const hasAudio = !!meaningfulAudio;
  const hasText = !!meaningfulText;
  const hasRefs = Object.values(refsByRole || {}).some((items) => Array.isArray(items) && items.length > 0);
  const semanticSupportPresent = !!transcriptText || !!spokenTextHint || !!audioSemanticSummary || !!meaningfulText || hasSemanticHints(audioSemanticHints);
  const weakSemanticContext = audioStoryMode === "speech_narrative" && hasAudio && !semanticSupportPresent;
  const semanticContextReason = weakSemanticContext ? "audio present but no transcript/hints/text support" : "";
  const storyControlMode = hasAudio
    ? "audio_primary"
    : hasText
      ? "text_override"
      : detectStoryControlMode({ meaningfulText, meaningfulAudio, refsByRole });
  const narrativeRoles = deriveStoryNarrativeRoles(storyControlMode);
  const narrativeSource = hasAudio ? "audio" : hasText ? "text" : "none";
  const storySource = narrativeSource;
  const timelineSource = hasAudio
    ? (audioStoryMode === "speech_narrative" ? "spoken semantic flow" : "audio rhythm")
    : hasText
      ? "text semantic flow"
      : "none";
  const modeSemantics = getModeSemantics(modeValue);
  const styleSemantics = getStyleSemantics(stylePreset);
  const audioStoryPolicy = getAudioStoryPolicy(audioStoryMode, { hasText: !!meaningfulText, hasAudio: !!meaningfulAudio });
  const textInfluence = deriveTextInfluence({ mode: modeValue, audioStoryMode, hasText: !!meaningfulText, storyControlMode });
  const storyMissionSummary = hasAudio && audioStoryMode === "speech_narrative"
    ? "Build scenes from spoken meaning and semantic progression."
    : buildStoryMissionSummary({ meaningfulText, storyControlMode, mode: modeValue });

  if (CLIP_TRACE_COMFY_REFS) {
    const tracedRoles = ["character_2", "character_3"];
    const tracePayload = Object.fromEntries(tracedRoles.map((role) => {
      const meta = refConnectionStates?.[role] || {};
      return [role, {
        connected: !!meta.connected,
        rawRefStatus: String(meta.rawRefStatus || ""),
        derivedStatus: String(meta.status || ""),
        refsCount: Number(meta.refsCount || 0),
        shortLabel: String(meta.shortLabel || ""),
        hasHiddenProfile: !!meta.hasHiddenProfile,
        hasAnalyzedAt: !!meta.hasAnalyzedAt,
        warningLabel: String(meta.warningLabel || ""),
      }];
    }));
    console.info("[CLIP TRACE COMFY REFS] deriveComfyBrainState role diagnostics", tracePayload);
  }

  return {
    modeValue,
    plannerMode,
    outputValue,
    audioStoryMode,
    stylePreset,
    freezeStyle,
    meaningfulText,
    meaningfulAudio,
    meaningfulAudioDurationSec,
    lyricsText,
    transcriptText,
    spokenTextHint,
    audioSemanticSummary,
    audioSemanticHints,
    refsByRole,
    storyControlMode,
    narrativeRoles,
    narrativeSource,
    storySource,
    timelineSource,
    modeSemantics,
    styleSemantics,
    audioStoryPolicy,
    textInfluence,
    storyMissionSummary,
    weakSemanticContext,
    semanticContextReason,
    hasAudio,
    hasText,
    hasRefs,
    refConnectionStates,
  };
}

export function extractComfyDebugFields({ plannerInput = {}, plannerMeta = {} } = {}) {
  const normalizedNarrativeSource = normalizeStoryboardSourceValue(
    plannerInput.narrativeSource || plannerMeta.narrativeSource,
    "none",
  );
  const normalizedStorySource = normalizeStoryboardSourceValue(
    plannerInput.storySource || plannerMeta.storySource || normalizedNarrativeSource,
    normalizedNarrativeSource,
  );
  return {
    mode: plannerInput.mode,
    plannerMode: plannerInput.plannerMode || plannerMeta.plannerMode || "legacy",
    output: plannerInput.output,
    stylePreset: plannerInput.stylePreset,
    storyControlMode: plannerInput.storyControlMode,
    narrativeSource: normalizedNarrativeSource,
    storySource: normalizedStorySource,
    timelineSource: plannerInput.timelineSource,
    audioStoryMode: plannerInput.audioStoryMode,
    weakSemanticContext: !!plannerInput.weakSemanticContext,
    semanticContextReason: plannerInput.semanticContextReason || "",
    hasAudio: !!plannerInput.meaningfulAudio,
    hasText: !!plannerInput.meaningfulText,
    hasRefs: !!plannerInput?.coverage?.hasRefs,
    textInfluence: plannerInput.textInfluence,
    modeRules: plannerInput.modeRules,
    styleRules: plannerInput.styleRules,
    audioStoryPolicy: plannerInput.audioStoryPolicy,
    warnings: plannerMeta.warnings || [],
    globalContinuity: plannerMeta.globalContinuity || "",
    worldLock: plannerMeta.worldLock || {},
    entityLocks: plannerMeta.entityLocks || {},
    preview: plannerMeta.preview || {},
    primaryRole: plannerMeta?.sceneRoleModel?.primarySubject || "character_1",
    secondaryRoles: plannerMeta?.sceneRoleModel?.secondarySubjects || [],
    pipelineFlow: "brain → per-scene prompts/rules → scene image → scene video",
  };
}
