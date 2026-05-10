const MANUAL_BLOCK_STORYBOARD_SPLIT_TYPE = "manual_block_storyboard_pass_single_block";
const MANUAL_BLOCK_VIDEO_PROMPT_SPLIT_TYPE = "manual_block_video_prompt_pass_single_block";

const STORY_BIBLE_FIELDS = [
  "project_story_summary_ru",
  "project_core_theme_ru",
  "project_drama_arc_ru",
  "project_visual_bible_ru",
  "project_style_lock_ru",
  "project_world_lock_ru",
  "project_character_identity_lock_ru",
  "project_location_lock_ru",
  "project_time_progression_ru",
  "project_atmosphere_lock_ru",
  "project_camera_language_ru",
  "project_color_progression_ru",
  "project_continuity_rules_ru",
  "project_must_keep_same_ru",
  "project_allowed_variation_ru",
  "project_reference_prompt_en",
];

const IMMUTABLE_SCENE_FIELDS = [
  "scene_id",
  "start_sec",
  "end_sec",
  "speech_start_sec",
  "speech_end_sec",
  "source_phrase_ids",
  "story_block_id",
];

const BLOCK_OUTPUT_FIELDS = [
  "block_visual_bible_ru",
  "block_style_lock_ru",
  "block_location_lock_ru",
  "block_time_of_day_ru",
  "block_color_palette_ru",
  "block_camera_language_ru",
  "block_continuity_rules_ru",
  "block_storyboard_summary_ru",
  "block_reference_frame_prompt_en",
];

const SCENE_OUTPUT_FIELDS = [
  "scene_global_context_ru",
  "continuity_anchor_ru",
  "must_match_project_identity_ru",
  "must_match_block_style_ru",
  "storyboard_frame_role_ru",
  "source_image_prompt_en",
  "source_image_prompt_ru",
  "source_image_negative_prompt_en",
  "i2v_prompt_en",
  "i2v_negative_prompt_en",
  "composition_ru",
  "camera_angle_ru",
  "subject_lock_ru",
  "background_lock_ru",
  "continuity_from_previous_scene_ru",
  "must_keep_same_ru",
  "allowed_variation_ru",
];

const EMPTY_PROMPT_FIELDS = ["video_prompt", "negative_prompt", "sound_prompt"];

const SCENE_IMAGE_URL_FIELDS = [
  "image_url",
  "start_image_url",
  "end_image_url",
];

const VIDEO_OUTPUT_FIELDS = [
  "video_prompt",
  "negative_prompt",
  "sound_prompt",
  "audio_mode",
  "voice_mode",
  "voice_language",
  "speech_text",
  "voice_profile",
  "ambient_sound_prompt",
  "sound_mix_note_ru",
  "voice_preset_id",
  "voice_role",
  "voice_gender",
  "delivery_style",
  "negative_voice_traits",
];

const VIDEO_CONTEXT_FIELDS = [
  ...SCENE_IMAGE_URL_FIELDS,
  "source_image_prompt_en",
  "source_image_prompt_ru",
  "source_image_negative_prompt_en",
  "i2v_prompt_en",
  "i2v_negative_prompt_en",
  "original_text",
  "source_text_en",
  "adapted_text_en",
  "translated_text_ru",
  "meaning_hint_ru",
  "scene_goal_ru",
  "prompt_hint_ru",
  "photo_prompt_hint_ru",
  "scene_global_context_ru",
  "continuity_anchor_ru",
  "continuity_from_previous_scene_ru",
  "must_match_project_identity_ru",
  "must_match_block_style_ru",
  "must_keep_same_ru",
  "allowed_variation_ru",
  "storyboard_frame_role_ru",
  "composition_ru",
  "camera_angle_ru",
  "subject_lock_ru",
  "background_lock_ru",
  ...VIDEO_OUTPUT_FIELDS,
];


const VOICE_PRESET_BANK = {
  narrator_male_documentary_en: {
    label_ru: "Мужской документальный диктор EN",
    voice_role: "narrator",
    voice_language: "en",
    voice_gender: "male",
    voice_profile: "calm male documentary narrator, warm low-mid voice, clear English pronunciation, natural breath, cinematic but restrained",
    delivery_style: "slow, clear, observant, intimate, serious but not dramatic",
    good_for: ["wildlife_story", "documentary", "history", "crime_story", "survival_story"],
    negative_voice_traits: "robotic voice, slurred words, distorted speech, exaggerated acting, cartoon voice, echo, harsh sibilance",
  },
  narrator_female_documentary_en: {
    label_ru: "Женский документальный диктор EN",
    voice_role: "narrator",
    voice_language: "en",
    voice_gender: "female",
    voice_profile: "calm female documentary narrator, warm natural voice, clear English pronunciation, soft authority, cinematic but restrained",
    delivery_style: "clear, warm, measured, thoughtful, emotionally controlled",
    good_for: ["wildlife_story", "human_story", "mystery", "emotional_story"],
    negative_voice_traits: "robotic voice, slurred words, distorted speech, overly theatrical acting, cartoon voice, echo",
  },
  narrator_male_documentary_ru: {
    label_ru: "Мужской документальный диктор RU",
    voice_role: "narrator",
    voice_language: "ru",
    voice_gender: "male",
    voice_profile: "спокойный мужской документальный диктор, тёплый низко-средний тембр, чёткое русское произношение, естественное дыхание, сдержанная кинематографичная подача",
    delivery_style: "медленно, ясно, наблюдательно, серьёзно, без театрального переигрывания",
    good_for: ["wildlife_story", "history", "crime_story", "survival_story"],
    negative_voice_traits: "роботизированный голос, смазанная речь, искажения, переигрывание, мультяшный голос, эхо, шипение",
  },
  narrator_female_documentary_ru: {
    label_ru: "Женский документальный диктор RU",
    voice_role: "narrator",
    voice_language: "ru",
    voice_gender: "female",
    voice_profile: "спокойный женский документальный диктор, тёплый натуральный голос, чёткое русское произношение, мягкая уверенность, сдержанная эмоциональность",
    delivery_style: "ясно, тепло, размеренно, вдумчиво, без театральности",
    good_for: ["wildlife_story", "human_story", "mystery", "emotional_story"],
    negative_voice_traits: "роботизированный голос, смазанная речь, искажения, переигрывание, мультяшный голос, эхо",
  },
  storyteller_male_cinematic_en: {
    label_ru: "Мужской кино-рассказчик EN",
    voice_role: "narrator",
    voice_language: "en",
    voice_gender: "male",
    voice_profile: "cinematic male storyteller, deep warm voice, dramatic but controlled, clear English pronunciation",
    delivery_style: "slow tension, meaningful pauses, emotional weight, not trailer-like",
    good_for: ["dramatic_story", "crime_story", "survival_story", "epic_intro"],
    negative_voice_traits: "overly epic trailer voice, shouting, robotic voice, distorted speech, slurred words",
  },
  whisper_male_en: {
    label_ru: "Мужской шёпот EN",
    voice_role: "narrator",
    voice_language: "en",
    voice_gender: "male",
    voice_profile: "quiet male whisper voice, close microphone feeling, clear whispered English, tense and intimate",
    delivery_style: "very quiet, slow, suspenseful, close, with short pauses",
    good_for: ["secret_observation", "horror_soft", "crime_story", "tension"],
    negative_voice_traits: "unclear whisper, noisy breath, distorted whisper, harsh sibilance, robotic voice",
  },
  whisper_female_en: {
    label_ru: "Женский шёпот EN",
    voice_role: "narrator",
    voice_language: "en",
    voice_gender: "female",
    voice_profile: "quiet female whisper voice, close microphone feeling, clear whispered English, intimate and tense",
    delivery_style: "soft, slow, secretive, suspenseful, with delicate pauses",
    good_for: ["secret_observation", "mystery", "emotional_story", "tension"],
    negative_voice_traits: "unclear whisper, noisy breath, distorted whisper, harsh sibilance, robotic voice",
  },
  character_short_phrase_en: {
    label_ru: "Короткая реплика персонажа EN",
    voice_role: "character",
    voice_language: "en",
    voice_gender: "auto",
    voice_profile: "natural character voice, believable acting, clear English pronunciation, close realistic sound",
    delivery_style: "short phrase, natural emotion, not theatrical, no long monologue",
    good_for: ["dialogue", "short_reaction", "story_scene"],
    negative_voice_traits: "robotic voice, overacting, slurred words, distorted speech, lip-sync mismatch",
  },
};

const PHOTO_STORYBOARD_CANON_RU = "ФОТО-РАСКАДРОВКА БЛОКА: не делать одинаковые establishing shots и не повторять одну композицию с разным светом. Один блок должен сохранять единый мир, стиль, время суток и атмосферу, но каждая сцена обязана показывать новую точку наблюдения, новый участок пространства или новый визуальный ракурс. Делать кадры так, будто камера подсматривает за живым миром изнутри: из травы, из-за куста, через ветви, с края низины, с уровня земли, из скрытой наблюдательной позиции. Разрешено выдумывать правдоподобные микролокации внутри общего мира, если они усиливают интерес и не ломают Story Bible. Каждый кадр должен иметь свою функцию: вход в мир, развитие, раскрытие, тревога, переход, кульминационная подготовка или мост к следующему блоку. Запрещено делать серию однотипных открыток, туристических панорам, дроновых видов или повтор одного и того же горизонта. Сохранять continuity: общий стиль, палитру, время, погоду, природную среду и эмоциональный тон блока.";
const PHOTO_STORYBOARD_CANON_EN = "BLOCK PHOTO STORYBOARD CANON: do not create repeated postcard-style establishing shots or the same composition with only different lighting. One block must keep the same world, style, time of day and atmosphere, but every scene must show a new observation point, a new micro-location, or a new visual angle. Make the frames feel as if the camera is secretly observing a living world from inside it: from tall grass, behind a bush, through branches, from the edge of a low basin, from ground level, or from a hidden documentary position. Plausible invented micro-locations are allowed if they strengthen the story and do not break the Story Bible. Every frame must have a clear function: entrance, development, reveal, tension, transition, setup, or bridge to the next block. Avoid generic tourist panoramas, drone-like views, repeated horizons, and wallpaper-like images. Preserve continuity: shared style, palette, time, weather, environment, and emotional tone.";

const PHOTO_OBSERVATION_MODES = [
  "clean_cinematic_documentary",
  "hidden_grass_observer",
  "long_lens_field_camera",
  "through_branches_spy_view",
  "ground_level_animal_path",
  "camera_trap_feeling",
  "optional_hud_overlay_for_preview_only",
];

const PHOTO_OVERLAY_POLICY_RU = "Для i2v/source images по умолчанию делать чистое изображение без текста, HUD, REC, ISO, батарейки, UI-рамок и графических меток. Стиль скрытого наблюдения передавать через композицию: длинный объектив, трава/ветки на переднем плане, дистанция, натуральное поведение животных, ощущение полевой камеры. HUD/camera interface можно использовать только как отдельный preview/монтажный слой или специальный экспериментальный стиль, но не как базовую картинку для LTX.";
const PHOTO_OVERLAY_POLICY_EN = "For i2v/source images, default to a clean image with no text, HUD, REC indicator, ISO labels, battery, UI frames, or graphic markers. Convey the hidden observation style through composition: long lens, grass/branches in the foreground, distance, natural animal behavior, field-camera feeling. HUD/camera interface may be used only as a separate preview/editing overlay or special experimental style, not as the base LTX source image.";

const CHATGPT_TASK = "BLOCK STORYBOARD PASS / РАСКАДРОВКА ОДНОГО БЛОКА. Используй общий Story Bible проекта и данные выбранного блока. Сделай visual bible блока и prompts для всех сцен этого блока. Не меняй scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids, story_block_id, количество сцен. video_prompt, negative_prompt, sound_prompt оставить пустыми. Не переписывай Story Pass поля: translated_text_ru, meaning_hint_ru, scene_goal_ru, photo_prompt_hint_ru, prompt_hint_ru, scene_role_in_block_ru, block_progress_ru. Используй их только как контекст. Заполняй только block storyboard fields и scene image/i2v prompt fields. Важно для фото-раскадровки: не повторяй одну и ту же композицию между сценами блока. Сохраняй общий мир и стиль, но меняй точку наблюдения, микролокацию, передний план, высоту камеры и драматическую функцию кадра. Делай кадры так, будто камера подсматривает за живым миром, а не снимает стандартные открытки. Если нужен стиль скрытого наблюдения, делай его через композицию и оптику, а не через текстовый HUD. Не добавляй UI/text overlays в source images unless explicitly requested.";


const LTX_VIDEO_PROMPT_CANON_RU = `
LTX VIDEO PROMPT CANON:
Каждый video_prompt должен начинаться с привязки к исходной картинке:
"Use the uploaded image as the exact first frame and visual anchor."

Всегда сохранять:
- ту же локацию
- тот же свет
- ту же геометрию фона
- тот же стиль
- тот же мир
- те же ключевые объекты/животных, если они есть

Каждая сцена должна иметь:
1) одну понятную функцию сцены: entrance / reveal / observation / tension / transition / setup / aftermath
2) только одно главное движение камеры: slow push-in / gentle pull-back / side tracking / low creeping forward move / subtle lateral reveal / controlled parallax
3) 2–4 слоя живой среды: grass sways, mist drifts, dust moves, clouds move slowly, light changes gradually, water ripples, foreground branches move slightly, distant birds/animals move subtly
4) ограниченную и реалистичную динамику: documentary, grounded, controlled, natural, no music-video chaos

Для i2v/i2v_sound не комбинировать больше одного camera move в одной сцене.
Не использовать одновременно lateral reveal + push-in + parallax, если сцена должна сохранять исходную картинку.
Если важно сохранить image identity/source photo identity, использовать: "almost static documentary shot with very small push-in" + motion only from environment layers.
Не просить new animals / herd / story event, если они не должны появиться из уже заданного кадра и Story Bible.

Запрещать:
dead static camera, orbit/spin, hard zoom, fast drone move, chaotic shake, impossible camera move, sudden scene change, identity/world/location drift, warped animals, melting trees/grass, flickering sky, text/HUD/UI overlays, combined multi-move camera instructions, unnecessary new animals/herd/story events.
`;

const LTX_CAMERA_MOVE_BANK = {
  verified_safe: [
    "slow push-in",
    "very slow push-in",
    "gentle pull-back",
    "side tracking with controlled parallax",
    "low creeping forward move",
    "subtle lateral reveal",
    "small handheld documentary drift",
    "controlled camera settle"
  ],
  experimental_use_carefully: [
    "subtle arc with clear parallax",
    "small reframing pan",
    "attention shift reveal"
  ],
  avoid_by_default: [
    "orbit",
    "spin",
    "fast drone",
    "hard zoom",
    "whip pan",
    "chaotic handheld",
    "large camera arc",
    "matrix-like slow motion"
  ]
};

const I2V_SOUND_PROMPT_POLICY_RU = "Для i2v_sound финальный sound_prompt должен быть только позитивным описанием натуральной атмосферы. Не писать в sound_prompt слова narrator, voice, speech, spoken words, dialogue, human voice даже с отрицанием. Если нужно запретить голос — это должно быть отдельной технической настройкой/negative audio field, но не частью positive sound_prompt.";
const I2V_SOUND_PROMPT_POLICY_EN = "For i2v_sound, the final sound_prompt must be a positive natural ambience description only. Do not include the words narrator, voice, speech, spoken words, dialogue, or human voice inside the sound_prompt, even as negatives. If voice must be disabled, use a separate technical flag/negative audio field, not the positive sound_prompt.";

const LTX_ENVIRONMENTAL_MOTION_BANK = [
  "grass_sways",
  "mist_or_dust_drifts",
  "clouds_move_slowly",
  "light_changes_gradually",
  "water_or_puddles_ripple",
  "foreground_occluders_move_slightly",
  "distant_birds_move_subtly",
  "distant_animals_move_subtly",
  "camera_parallax_reveals_depth"
];

const LTX_NEGATIVE_PROMPT_GUIDANCE = {
  common: [
    "dead static image",
    "sudden scene change",
    "identity drift",
    "location drift",
    "world change",
    "warped animals",
    "melting trees",
    "melting grass",
    "flickering sky",
    "text overlay",
    "HUD",
    "REC overlay",
    "UI elements",
    "orbit camera",
    "spin camera",
    "hard zoom",
    "chaotic shake",
    "fast drone movement",
    "CGI look",
    "cartoon look",
    "surreal motion"
  ],
  note: "Не перегружать negative prompt. Выбирать только релевантные риски сцены, чтобы не заморозить движение."
};

const VIDEO_CHATGPT_TASK = "BLOCK VIDEO PROMPT PASS / VIDEO PROMPTS ОДНОГО БЛОКА. Используй общий Story Bible проекта, visual bible выбранного блока, раскадровочные image поля и данные только сцен этого блока. Не меняй scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids, story_block_id, количество сцен. Заполняй только video_prompt, negative_prompt, sound_prompt и audio/voice поля. Учитывай route каждой сцены: для i2v audio_mode должен быть none или ambience; для i2v_sound audio_mode должен быть ambience; для i2v_text audio_mode должен быть narration или speech, а speech_text должен брать текст из original_text или translated_text_ru в зависимости от voice_language; ia2v/lip-sync route пропускай дальше без изменения архитектуры. Для i2v_text обязательно используй voice_preset_bank и default_voice_config: speech_text должен звучать тем же выбранным голосом по проекту, если сцена явно не переопределяет voice_preset_id. sound_prompt должен включать точную фразу, voice_profile, delivery_style, background ambience, mix note and negative_voice_traits. Для i2v_sound не добавляй speech_text, narrator, human voice или spoken words — только натуральную атмосферу.";

function toStringId(value = "") {
  return String(value || "").trim();
}

function pickFields(source = {}, fields = []) {
  return fields.reduce((acc, field) => {
    acc[field] = source?.[field] ?? "";
    return acc;
  }, {});
}

function pickPresentFields(source = {}, fields = []) {
  return fields.reduce((acc, field) => {
    if (Object.prototype.hasOwnProperty.call(source || {}, field)) {
      acc[field] = source?.[field] ?? "";
    }
    return acc;
  }, {});
}

function normalizeSourcePhraseIdsForCompare(value = []) {
  return (Array.isArray(value) ? value : [])
    .map((id) => String(id || "").trim())
    .filter(Boolean);
}

function sameNumber(a, b) {
  return Math.abs(Number(a || 0) - Number(b || 0)) < 0.001;
}

function sameSourcePhraseIds(a = [], b = []) {
  return JSON.stringify(normalizeSourcePhraseIdsForCompare(a)) === JSON.stringify(normalizeSourcePhraseIdsForCompare(b));
}

function resolveManualBlockStoryboardSelection(project = {}, selectedSceneOrBlockId = "") {
  const selectedId = toStringId(selectedSceneOrBlockId || project?.selectedSceneId);
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const storyBlocks = Array.isArray(project?.story_blocks) ? project.story_blocks : [];
  const selectedScene = scenes.find((scene) => toStringId(scene?.scene_id) === selectedId) || null;
  const selectedBlockId = toStringId(selectedScene?.story_block_id || selectedId);
  const selectedBlock = storyBlocks.find((block, idx) => {
    const blockId = toStringId(block?.block_id || block?.id || block?.story_block_id || `block_${idx + 1}`);
    return blockId === selectedBlockId;
  }) || null;
  const targetBlockId = toStringId(selectedBlock?.block_id || selectedBlock?.id || selectedBlock?.story_block_id || selectedBlockId);
  const blockScenes = scenes.filter((scene) => toStringId(scene?.story_block_id) === targetBlockId);

  return { selectedScene, selectedBlock, targetBlockId, blockScenes };
}

function buildCompactSceneForStoryboard(scene = {}) {
  return {
    scene_id: scene?.scene_id || "",
    index: scene?.index ?? "",
    start_sec: scene?.start_sec ?? 0,
    end_sec: scene?.end_sec ?? 0,
    speech_start_sec: scene?.speech_start_sec ?? scene?.start_sec ?? 0,
    speech_end_sec: scene?.speech_end_sec ?? scene?.end_sec ?? 0,
    duration_sec: scene?.duration_sec ?? Math.max(0, Number(scene?.end_sec || 0) - Number(scene?.start_sec || 0)),
    source_phrase_ids: normalizeSourcePhraseIdsForCompare(scene?.source_phrase_ids || scene?.sourcePhraseIds),
    story_block_id: scene?.story_block_id || "",
    route: scene?.route || "i2v",
    original_text: scene?.original_text || scene?.source_text_en || scene?.adapted_text_en || "",
    translated_text_ru: scene?.translated_text_ru || "",
    meaning_hint_ru: scene?.meaning_hint_ru || "",
    scene_goal_ru: scene?.scene_goal_ru || "",
    photo_prompt_hint_ru: scene?.photo_prompt_hint_ru || "",
    prompt_hint_ru: scene?.prompt_hint_ru || scene?.photo_prompt_hint_ru || "",
    scene_role_in_block_ru: scene?.scene_role_in_block_ru || "",
    block_progress_ru: scene?.block_progress_ru || "",
    visual_role_ru: scene?.visual_role_ru || "",
    performance_role_ru: scene?.performance_role_ru || "",
    image_url: scene?.image_url || scene?.start_image_url || "",
    start_image_url: scene?.start_image_url || scene?.image_url || "",
    end_image_url: scene?.end_image_url || "",
    video_prompt: "",
    negative_prompt: "",
    sound_prompt: "",
  };
}

export function buildManualBlockStoryboardContextJson(project = {}, selectedSceneOrBlockId = "") {
  const { selectedBlock, targetBlockId, blockScenes } = resolveManualBlockStoryboardSelection(project, selectedSceneOrBlockId);
  if (!targetBlockId || !selectedBlock) {
    throw new Error("manual_block_storyboard_target_block_not_found");
  }

  return {
    split_type: MANUAL_BLOCK_STORYBOARD_SPLIT_TYPE,
    chatgpt_task: CHATGPT_TASK,
    format: project?.format || "9:16",
    aspect_ratio: project?.format || "9:16",
    photo_storyboard_canon_ru: PHOTO_STORYBOARD_CANON_RU,
    photo_storyboard_canon_en: PHOTO_STORYBOARD_CANON_EN,
    photo_observation_modes: PHOTO_OBSERVATION_MODES,
    photo_overlay_policy_ru: PHOTO_OVERLAY_POLICY_RU,
    photo_overlay_policy_en: PHOTO_OVERLAY_POLICY_EN,
    project_story_bible: pickFields(project, STORY_BIBLE_FIELDS),
    target_block_id: targetBlockId,
    target_block: { ...(selectedBlock || {}), block_id: targetBlockId },
    scenes: blockScenes.map(buildCompactSceneForStoryboard),
    output_fields_to_fill: {
      target_block: BLOCK_OUTPUT_FIELDS,
      scenes: [...SCENE_OUTPUT_FIELDS, ...SCENE_IMAGE_URL_FIELDS],
      keep_empty: EMPTY_PROMPT_FIELDS,
    },
  };
}


function resolveSpeechTextForVideo(scene = {}, voiceLanguageOverride = "") {
  const voiceLanguage = String(voiceLanguageOverride || scene?.voice_language || "").trim().toLowerCase();
  if (voiceLanguage.startsWith("ru")) return String(scene?.translated_text_ru || scene?.original_text || "").trim();
  return String(scene?.original_text || scene?.source_text_en || scene?.adapted_text_en || scene?.translated_text_ru || "").trim();
}

function buildCompactSceneForVideoPrompt(scene = {}, project = {}) {
  const compact = {
    scene_id: scene?.scene_id || "",
    index: scene?.index ?? "",
    route: scene?.route || "i2v",
    start_sec: scene?.start_sec ?? 0,
    end_sec: scene?.end_sec ?? 0,
    speech_start_sec: scene?.speech_start_sec ?? scene?.start_sec ?? 0,
    speech_end_sec: scene?.speech_end_sec ?? scene?.end_sec ?? 0,
    duration_sec: scene?.duration_sec ?? Math.max(0, Number(scene?.end_sec || 0) - Number(scene?.start_sec || 0)),
    source_phrase_ids: normalizeSourcePhraseIdsForCompare(scene?.source_phrase_ids || scene?.sourcePhraseIds),
    story_block_id: scene?.story_block_id || "",
    ...pickFields(scene, VIDEO_CONTEXT_FIELDS),
  };

  const route = String(compact.route || "i2v").trim().toLowerCase();
  const projectVoicePresetId = project?.voice_preset_id || "narrator_male_documentary_en";
  const voicePresetId = compact.voice_preset_id || projectVoicePresetId;
  const voicePreset = VOICE_PRESET_BANK[voicePresetId] || VOICE_PRESET_BANK.narrator_male_documentary_en;

  compact.voice_preset_id = compact.voice_preset_id || project?.voice_preset_id || "";
  compact.voice_role = compact.voice_role || scene?.voice_role || "";
  compact.voice_gender = compact.voice_gender || scene?.voice_gender || "";
  compact.delivery_style = compact.delivery_style || scene?.delivery_style || "";
  compact.negative_voice_traits = compact.negative_voice_traits || scene?.negative_voice_traits || "";

  if (route === "i2v_text") {
    compact.voice_preset_id = compact.voice_preset_id || projectVoicePresetId;
    compact.voice_language = compact.voice_language || project?.voice_language || voicePreset?.voice_language || "en";
    compact.voice_role = compact.voice_role || voicePreset?.voice_role || "narrator";
    compact.voice_gender = compact.voice_gender || voicePreset?.voice_gender || "";
    compact.voice_profile = compact.voice_profile || voicePreset?.voice_profile || "";
    compact.delivery_style = compact.delivery_style || voicePreset?.delivery_style || "";
    compact.negative_voice_traits = compact.negative_voice_traits || voicePreset?.negative_voice_traits || "";
    compact.speech_text = compact.speech_text || resolveSpeechTextForVideo(scene, compact.voice_language);
    compact.ambient_sound_prompt = compact.ambient_sound_prompt || "quiet natural ambience under the voice, low volume, no music overpowering narration";
  } else if (route === "i2v_sound") {
    compact.audio_mode = compact.audio_mode || "ambience";
    compact.voice_mode = "none";
    compact.speech_text = "";
    compact.voice_preset_id = "";
    compact.voice_role = "none";
    compact.voice_gender = "";
    compact.voice_profile = "";
    compact.delivery_style = "";
    compact.negative_voice_traits = "";
  } else {
    compact.speech_text = compact.speech_text || "";
  }

  return compact;
}

export function buildManualBlockVideoPromptContextJson(project = {}, selectedSceneOrBlockId = "") {
  const { selectedBlock, targetBlockId, blockScenes } = resolveManualBlockStoryboardSelection(project, selectedSceneOrBlockId);
  if (!targetBlockId || !selectedBlock) {
    throw new Error("manual_block_video_prompt_target_block_not_found");
  }

  return {
    split_type: MANUAL_BLOCK_VIDEO_PROMPT_SPLIT_TYPE,
    chatgpt_task: VIDEO_CHATGPT_TASK,
    format: project?.format || "9:16",
    aspect_ratio: project?.format || "9:16",
    ltx_video_prompt_canon_ru: LTX_VIDEO_PROMPT_CANON_RU,
    i2v_sound_prompt_policy_ru: I2V_SOUND_PROMPT_POLICY_RU,
    i2v_sound_prompt_policy_en: I2V_SOUND_PROMPT_POLICY_EN,
    ltx_camera_move_bank: LTX_CAMERA_MOVE_BANK,
    ltx_environmental_motion_bank: LTX_ENVIRONMENTAL_MOTION_BANK,
    ltx_negative_prompt_guidance: LTX_NEGATIVE_PROMPT_GUIDANCE,
    route_rules: {
      i2v: {
        audio_mode: "none или ambience",
        video_prompt: "Use uploaded image as exact first frame. Write exactly one clear camera move + 2–4 living environment layers + scene function. Do not combine multiple camera moves. For image identity preservation prefer almost static documentary shot with very small push-in. Do not write speech. Keep motion restrained and documentary.",
        sound_prompt: "Optional. Empty if no generated ambience is needed.",
      },
      i2v_sound: {
        audio_mode: "ambience",
        voice_mode: "none",
        speech_text: "",
        sound_prompt_required: true,
        sound_prompt_rule: "Only positive natural ambience description. Do not mention narrator, voice, spoken words, dialogue or speech inside the final sound_prompt. Use field tone, wind, grass, birds, insects, water/mud/puddles, dust, distant animal movement and subtle environmental sound.",
        video_prompt_rule: "Use uploaded image as exact first frame. Use exactly one restrained camera move; for image identity preservation prefer almost static documentary shot with very small push-in plus 2–4 environment motion layers. Do not combine lateral reveal, push-in and parallax in one scene. Do not request new animals, herds or story events unless required by the source image/story.",
      },
      i2v_text: {
        audio_mode: "narration_or_speech",
        sound_prompt_required: true,
        speech_text_required: true,
        voice_preset_required: true,
        speech_text_source: "If voice_language starts with ru, use translated_text_ru; otherwise use original_text/source_text_en.",
        voice_profile_rule: "Use voice_preset_bank. Keep same voice across project unless scene overrides voice_preset_id.",
        sound_prompt_rule: "Must include exact speech_text, voice_profile, delivery_style, background ambience, mix note and negative_voice_traits.",
        voice_over_rule: "If voice_role is narrator, do not force animals or people to lip-sync. This is off-screen voice-over.",
        character_speech_rule: "If voice_role is character, phrase should be short and natural. Use only when character is meant to speak.",
      },
      ia2v: { note: "route передать дальше, архитектуру lip-sync не ломать" },
      first_last: { note: "использовать start_image_url и end_image_url" },
    },
    voice_preset_bank: VOICE_PRESET_BANK,
    default_voice_config: {
      voice_preset_id: project?.voice_preset_id || "narrator_male_documentary_en",
      voice_language: project?.voice_language || "en",
      voice_role: "narrator",
      apply_to_i2v_text_by_default: true,
      keep_same_voice_across_project: true,
      note_ru: "Для i2v_text по умолчанию использовать один и тот же голос на весь проект, если сцена явно не переопределяет voice_preset_id.",
    },
    i2v_text_sound_prompt_template: {
      narrator: `Narrator voice-over says exactly: "{speech_text}".
Voice profile: {voice_profile}.
Delivery: {delivery_style}.
Background ambience: {ambient_sound_prompt}.
Mix: voice clear and close, ambience low under the voice, no music overpowering narration.
Avoid voice traits: {negative_voice_traits}.`,
      character: `Character says exactly: "{speech_text}".
Voice profile: {voice_profile}.
Delivery: {delivery_style}.
Background ambience: {ambient_sound_prompt}.
Mix: speech clear, natural and close, ambience low under the voice.
Avoid voice traits: {negative_voice_traits}.`,
    },
    i2v_sound_sound_prompt_template: "Natural scene ambience only: {ambient_sound_prompt}. Keep the sound realistic, subtle, documentary, low-volume and environmental. Use only field tone, wind, grass, birds, insects, water, dust, distant animal movement or other natural location sounds.",
    project_story_bible: pickFields(project, STORY_BIBLE_FIELDS),
    target_block_id: targetBlockId,
    target_block: { ...(selectedBlock || {}), block_id: targetBlockId },
    scenes: blockScenes.map((scene) => buildCompactSceneForVideoPrompt(scene, project)),
    output_fields_to_fill: {
      scenes: VIDEO_OUTPUT_FIELDS,
    },
  };
}

export function buildManualBlockStoryboardBriefText(project = {}, selectedSceneOrBlockId = "") {
  const context = buildManualBlockStoryboardContextJson(project, selectedSceneOrBlockId);
  const bibleLines = STORY_BIBLE_FIELDS
    .map((field) => [field, context.project_story_bible[field]])
    .filter(([, value]) => String(value || "").trim())
    .map(([field, value]) => `${field}: ${value}`);
  const block = context.target_block || {};
  const blockLines = [
    `block_id: ${context.target_block_id}`,
    `title_ru: ${block.title_ru || block.title || block.name || ""}`,
    `summary_ru: ${block.summary_ru || block.summary || ""}`,
    `block_goal_ru: ${block.block_goal_ru || block.goal_ru || ""}`,
    `block_reveal_ru: ${block.block_reveal_ru || block.reveal_ru || ""}`,
    `block_emotion_ru: ${block.block_emotion_ru || block.emotion_ru || ""}`,
  ].filter((line) => !line.endsWith(": "));

  const sceneLines = context.scenes.map((scene, idx) => [
    `Scene ${idx + 1} / ${scene.scene_id}`,
    `timing: ${scene.start_sec} → ${scene.end_sec} sec; speech: ${scene.speech_start_sec} → ${scene.speech_end_sec} sec; source_phrase_ids: ${scene.source_phrase_ids.join(", ") || "—"}`,
    `original: ${scene.original_text || "—"}`,
    `translated: ${scene.translated_text_ru || "—"}`,
    `meaning: ${scene.meaning_hint_ru || "—"}`,
    `goal: ${scene.scene_goal_ru || "—"}`,
    `photo_prompt_hint: ${scene.photo_prompt_hint_ru || "—"}`,
    `prompt_hint: ${scene.prompt_hint_ru || "—"}`,
  ].join("\n")).join("\n\n");

  return [
    "BLOCK STORYBOARD BRIEF / РАСКАДРОВКА ОДНОГО БЛОКА",
    CHATGPT_TASK,
    "",
    "## Story Bible summary",
    bibleLines.join("\n") || "—",
    "",
    "## Target block summary",
    blockLines.join("\n") || "—",
    "",
    "## Scenes of this block",
    sceneLines || "—",
    "",
    "## Output fields to fill",
    JSON.stringify(context.output_fields_to_fill, null, 2),
  ].join("\n");
}

function validateIncomingSceneShape(originalScene = {}, incomingScene = {}, targetBlockId = "", options = {}) {
  if (toStringId(incomingScene?.scene_id) !== toStringId(originalScene?.scene_id)) {
    throw new Error(`scene_id_changed:${originalScene?.scene_id || "unknown"}`);
  }
  if (toStringId(incomingScene?.story_block_id) !== targetBlockId) {
    throw new Error(`story_block_id_changed:${originalScene?.scene_id}`);
  }
  ["start_sec", "end_sec", "speech_start_sec", "speech_end_sec"].forEach((field) => {
    if (!sameNumber(incomingScene?.[field], originalScene?.[field])) {
      throw new Error(`${field}_changed:${originalScene?.scene_id}`);
    }
  });
  if (!sameSourcePhraseIds(incomingScene?.source_phrase_ids, originalScene?.source_phrase_ids)) {
    throw new Error(`source_phrase_ids_changed:${originalScene?.scene_id}`);
  }
  if (options.requireEmptyPromptFields) {
    EMPTY_PROMPT_FIELDS.forEach((field) => {
      if (String(incomingScene?.[field] || "").trim()) {
        throw new Error(`${field}_must_be_empty:${originalScene?.scene_id}`);
      }
    });
  }
}

export function applyManualBlockStoryboardImport(project = {}, rawPayload = {}) {
  const payload = rawPayload?.payload && typeof rawPayload.payload === "object" ? rawPayload.payload : rawPayload;
  if (payload?.split_type !== MANUAL_BLOCK_STORYBOARD_SPLIT_TYPE) return null;

  const targetBlockId = toStringId(payload?.target_block_id || payload?.target_block?.block_id || payload?.story_block?.block_id || payload?.block_id);
  if (!targetBlockId) throw new Error("manual_block_storyboard_import_missing_target_block_id");

  const currentBlockScenes = (Array.isArray(project?.scenes) ? project.scenes : []).filter((scene) => toStringId(scene?.story_block_id) === targetBlockId);
  const incomingScenes = Array.isArray(payload?.scenes) ? payload.scenes : [];
  if (incomingScenes.length !== currentBlockScenes.length) {
    throw new Error(`manual_block_storyboard_scene_count_changed:${incomingScenes.length}/${currentBlockScenes.length}`);
  }

  const incomingById = new Map(incomingScenes.map((scene) => [toStringId(scene?.scene_id), scene]));
  currentBlockScenes.forEach((scene) => {
    const incoming = incomingById.get(toStringId(scene?.scene_id));
    if (!incoming) throw new Error(`manual_block_storyboard_missing_scene:${scene?.scene_id}`);
    validateIncomingSceneShape(scene, incoming, targetBlockId, {
      requireEmptyPromptFields: true,
    });
  });

  const incomingBlock = payload?.target_block || payload?.story_block || {};
  const nextStoryBlocks = (Array.isArray(project?.story_blocks) ? project.story_blocks : []).map((block, idx) => {
    const blockId = toStringId(block?.block_id || block?.id || block?.story_block_id || `block_${idx + 1}`);
    if (blockId !== targetBlockId) return block;
    const blockPatch = pickPresentFields(incomingBlock, BLOCK_OUTPUT_FIELDS);
    return { ...block, ...blockPatch, block_id: blockId };
  });

  const nextScenes = (Array.isArray(project?.scenes) ? project.scenes : []).map((scene) => {
    if (toStringId(scene?.story_block_id) !== targetBlockId) return scene;
    const incoming = incomingById.get(toStringId(scene?.scene_id)) || {};
    const scenePatch = pickPresentFields(incoming, [...SCENE_OUTPUT_FIELDS, ...SCENE_IMAGE_URL_FIELDS]);
    return {
      ...scene,
      ...scenePatch,
    };
  });

  return {
    ...project,
    story_blocks: nextStoryBlocks,
    scenes: nextScenes,
    updatedAt: Date.now(),
  };
}

export function applyManualBlockVideoPromptImport(project = {}, rawPayload = {}) {
  const payload = rawPayload?.payload && typeof rawPayload.payload === "object" ? rawPayload.payload : rawPayload;
  if (payload?.split_type !== MANUAL_BLOCK_VIDEO_PROMPT_SPLIT_TYPE) return null;

  const targetBlockId = toStringId(payload?.target_block_id || payload?.target_block?.block_id || payload?.story_block?.block_id || payload?.block_id);
  if (!targetBlockId) throw new Error("manual_block_video_prompt_import_missing_target_block_id");

  const currentBlockScenes = (Array.isArray(project?.scenes) ? project.scenes : []).filter((scene) => toStringId(scene?.story_block_id) === targetBlockId);
  const incomingScenes = Array.isArray(payload?.scenes) ? payload.scenes : [];
  if (incomingScenes.length !== currentBlockScenes.length) {
    throw new Error(`manual_block_video_prompt_scene_count_changed:${incomingScenes.length}/${currentBlockScenes.length}`);
  }

  const incomingById = new Map(incomingScenes.map((scene) => [toStringId(scene?.scene_id), scene]));
  currentBlockScenes.forEach((scene) => {
    const incoming = incomingById.get(toStringId(scene?.scene_id));
    if (!incoming) throw new Error(`manual_block_video_prompt_missing_scene:${scene?.scene_id}`);
    validateIncomingSceneShape(scene, incoming, targetBlockId, {
      requireEmptyPromptFields: false,
    });
  });

  const nextScenes = (Array.isArray(project?.scenes) ? project.scenes : []).map((scene) => {
    if (toStringId(scene?.story_block_id) !== targetBlockId) return scene;
    const incoming = incomingById.get(toStringId(scene?.scene_id)) || {};
    const scenePatch = pickPresentFields(incoming, VIDEO_OUTPUT_FIELDS);
    const nextScene = { ...scene, ...scenePatch };
    return {
      ...nextScene,
      status: nextScene.video_prompt ? "prompt_ready" : scene.status,
    };
  });

  return {
    ...project,
    scenes: nextScenes,
    updatedAt: Date.now(),
  };
}

export { MANUAL_BLOCK_STORYBOARD_SPLIT_TYPE, MANUAL_BLOCK_VIDEO_PROMPT_SPLIT_TYPE, STORY_BIBLE_FIELDS, IMMUTABLE_SCENE_FIELDS };
