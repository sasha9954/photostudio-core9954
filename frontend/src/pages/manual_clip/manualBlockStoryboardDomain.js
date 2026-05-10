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

const PHOTO_STORYBOARD_CANON_RU = "ФОТО-РАСКАДРОВКА БЛОКА: не делать одинаковые establishing shots и не повторять одну композицию с разным светом. Один блок должен сохранять единый мир, стиль, время суток и атмосферу, но каждая сцена обязана показывать новую точку наблюдения, новый участок пространства или новый визуальный ракурс. Делать кадры так, будто камера подсматривает за живым миром изнутри: из травы, из-за куста, через ветви, с края низины, с уровня земли, из скрытой наблюдательной позиции. Разрешено выдумывать правдоподобные микролокации внутри общего мира, если они усиливают интерес и не ломают Story Bible. Каждый кадр должен иметь свою функцию: вход в мир, развитие, раскрытие, тревога, переход, кульминационная подготовка или мост к следующему блоку. Запрещено делать серию однотипных открыток, туристических панорам, дроновых видов или повтор одного и того же горизонта. Сохранять continuity: общий стиль, палитру, время, погоду, природную среду и эмоциональный тон блока.";
const PHOTO_STORYBOARD_CANON_EN = "BLOCK PHOTO STORYBOARD CANON: do not create repeated postcard-style establishing shots or the same composition with only different lighting. One block must keep the same world, style, time of day and atmosphere, but every scene must show a new observation point, a new micro-location, or a new visual angle. Make the frames feel as if the camera is secretly observing a living world from inside it: from tall grass, behind a bush, through branches, from the edge of a low basin, from ground level, or from a hidden documentary position. Plausible invented micro-locations are allowed if they strengthen the story and do not break the Story Bible. Every frame must have a clear function: entrance, development, reveal, tension, transition, setup, or bridge to the next block. Avoid generic tourist panoramas, drone-like views, repeated horizons, and wallpaper-like images. Preserve continuity: shared style, palette, time, weather, environment, and emotional tone.";

const CHATGPT_TASK = "BLOCK STORYBOARD PASS / РАСКАДРОВКА ОДНОГО БЛОКА. Используй общий Story Bible проекта и данные выбранного блока. Сделай visual bible блока и prompts для всех сцен этого блока. Не меняй scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids, story_block_id, количество сцен. video_prompt, negative_prompt, sound_prompt оставить пустыми. Не переписывай Story Pass поля: translated_text_ru, meaning_hint_ru, scene_goal_ru, photo_prompt_hint_ru, prompt_hint_ru, scene_role_in_block_ru, block_progress_ru. Используй их только как контекст. Заполняй только block storyboard fields и scene image/i2v prompt fields. Важно для фото-раскадровки: не повторяй одну и ту же композицию между сценами блока. Сохраняй общий мир и стиль, но меняй точку наблюдения, микролокацию, передний план, высоту камеры и драматическую функцию кадра. Делай кадры так, будто камера подсматривает за живым миром, а не снимает стандартные открытки.";

const VIDEO_CHATGPT_TASK = "BLOCK VIDEO PROMPT PASS / VIDEO PROMPTS ОДНОГО БЛОКА. Используй общий Story Bible проекта, visual bible выбранного блока, раскадровочные image поля и данные только сцен этого блока. Не меняй scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids, story_block_id, количество сцен. Заполняй только video_prompt, negative_prompt, sound_prompt и audio/voice поля. Учитывай route каждой сцены: для i2v audio_mode должен быть none или ambience; для i2v_sound audio_mode должен быть ambience; для i2v_text audio_mode должен быть narration или speech, а speech_text должен брать текст из original_text или translated_text_ru в зависимости от voice_language; ia2v/lip-sync route пропускай дальше без изменения архитектуры.";

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


function resolveSpeechTextForVideo(scene = {}) {
  const voiceLanguage = String(scene?.voice_language || "").trim().toLowerCase();
  if (voiceLanguage.startsWith("ru")) return String(scene?.translated_text_ru || scene?.original_text || "").trim();
  return String(scene?.original_text || scene?.source_text_en || scene?.adapted_text_en || scene?.translated_text_ru || "").trim();
}

function buildCompactSceneForVideoPrompt(scene = {}) {
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
  compact.speech_text = compact.speech_text || resolveSpeechTextForVideo(scene);
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
    route_rules: {
      i2v: { audio_mode: "none или ambience" },
      i2v_sound: { audio_mode: "ambience" },
      i2v_text: { audio_mode: "narration или speech", speech_text: "original_text или translated_text_ru в зависимости от voice_language" },
      ia2v: { note: "route передать дальше, архитектуру lip-sync не ломать" },
      first_last: { note: "использовать start_image_url и end_image_url" },
    },
    project_story_bible: pickFields(project, STORY_BIBLE_FIELDS),
    target_block_id: targetBlockId,
    target_block: { ...(selectedBlock || {}), block_id: targetBlockId },
    scenes: blockScenes.map(buildCompactSceneForVideoPrompt),
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
