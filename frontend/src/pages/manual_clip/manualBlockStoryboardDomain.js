const MANUAL_BLOCK_STORYBOARD_SPLIT_TYPE = "manual_block_storyboard_pass_single_block";

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
  "visual_bible_ru",
  "block_visual_bible_ru",
  "block_camera_language_ru",
  "block_color_palette_ru",
  "block_atmosphere_ru",
  "block_continuity_notes_ru",
  "block_storyboard_notes_ru",
  "block_goal_ru",
  "block_reveal_ru",
  "block_emotion_ru",
];

const SCENE_OUTPUT_FIELDS = [
  "scene_goal_ru",
  "photo_prompt_hint_ru",
  "prompt_hint_ru",
  "scene_role_in_block_ru",
  "block_progress_ru",
  "visual_role_ru",
  "performance_role_ru",
];

const EMPTY_PROMPT_FIELDS = ["video_prompt", "negative_prompt", "sound_prompt"];

const CHATGPT_TASK = "BLOCK STORYBOARD PASS / РАСКАДРОВКА ОДНОГО БЛОКА. Используй общий Story Bible проекта и данные выбранного блока. Сделай visual bible блока и prompts для всех сцен этого блока. Не меняй scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids, story_block_id, количество сцен. video_prompt, negative_prompt, sound_prompt оставить пустыми. Заполни только block storyboard fields и scene prompt fields.";

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
    project_story_bible: pickFields(project, STORY_BIBLE_FIELDS),
    target_block_id: targetBlockId,
    target_block: { ...(selectedBlock || {}), block_id: targetBlockId },
    scenes: blockScenes.map(buildCompactSceneForStoryboard),
    output_fields_to_fill: {
      target_block: BLOCK_OUTPUT_FIELDS,
      scenes: SCENE_OUTPUT_FIELDS,
      keep_empty: EMPTY_PROMPT_FIELDS,
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

function validateIncomingSceneShape(originalScene = {}, incomingScene = {}, targetBlockId = "") {
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
  EMPTY_PROMPT_FIELDS.forEach((field) => {
    if (String(incomingScene?.[field] || "").trim()) {
      throw new Error(`${field}_must_be_empty:${originalScene?.scene_id}`);
    }
  });
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
    validateIncomingSceneShape(scene, incoming, targetBlockId);
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
    const scenePatch = pickPresentFields(incoming, SCENE_OUTPUT_FIELDS);
    return {
      ...scene,
      ...scenePatch,
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: "",
    };
  });

  return {
    ...project,
    story_blocks: nextStoryBlocks,
    scenes: nextScenes,
    updatedAt: Date.now(),
  };
}

export { MANUAL_BLOCK_STORYBOARD_SPLIT_TYPE, STORY_BIBLE_FIELDS, IMMUTABLE_SCENE_FIELDS };
