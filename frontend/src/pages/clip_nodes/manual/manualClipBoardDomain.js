export const MANUAL_CLIP_MODE = "manual_clip_board";
export const MANUAL_CLIP_STEPS = ["empty", "audio_loaded", "split_chat_ready", "scene_plan_ready"];
export const ROUTES = ["ia2v", "i2v", "i2v_sound", "first_last", "first_last_sound"];
export const PROJECT_KINDS = ["clip", "story"];
export const SPLIT_SOURCES = ["ai", "json"];

export const CHATGPT_STORY_SPLIT_TASK = {
  task_type: "audio_story_split_or_storyboard_pass",
  instruction_ru: "Это JSON для проекта PhotoStudio. Если scenes пустые или есть одна длинная сцена на всю длительность аудио — нужно сделать новую AI-разбивку: придумать story_blocks и scenes по смыслу аудио/идеи. Если scenes уже нарезаны пользователем — не менять scene_id, start_sec, end_sec и route, а только заполнить story_blocks и смысловые поля сцен.",
  rules_ru: [
    "Не заполнять video_prompt, negative_prompt, sound_prompt.",
    "Для voice-over историй по умолчанию использовать i2v.",
    "Для музыкального клипа ia2v использовать только там, где реально нужен lip-sync.",
    "Каждый story_block должен иметь title_ru, summary_ru, block_goal_ru, block_reveal_ru, block_emotion_ru, color, start_sec, end_sec, scene_ids.",
    "Каждая scene должна иметь original_text или adapted_text_en, translated_text_ru, meaning_hint_ru, story_block_id, story_block_title_ru, story_block_position_ru, scene_role_in_block_ru, block_progress_ru, scene_goal_ru, photo_prompt_hint_ru, prompt_hint_ru.",
    "Вернуть готовый JSON в том же формате."
  ],
  output_ru: "Верни только готовый JSON без лишнего текста."
};

export const STORY_PREP_TEMPLATE_META = {
  has_dynamic_template: true,
  template_type: "story_prep_template",
};

export function getDefaultManualClipNodeData() {
  return {
    mode: MANUAL_CLIP_MODE,
    title: "AI-разбивка клипа",
    ruLabel: "AI-разбивка клипа",
    step: "empty",
    format: "9:16",
    project_kind: "clip",
    split_source: "ai",
    json_input: "",
    json_error: "",
    last_split_source: "",
    split_audio_status: "idle",
    split_audio_error: "",
    split_audio_count: 0,
    ai_split_status: "idle",
    ai_split_error: "",
    manual_director_required: true,
    manual_director_chat: {
      messages: [],
      answers: {},
      questions: [],
      done: false,
      summary: "",
      contract: null,
      status: "idle",
      error: "",
    },
    audio: { url: "", filename: "", duration_sec: 0, duration_ms: 0 },
    split_chat: { user_request: "", ai_summary: "", raw_ai_json: null },
    story_blocks: [],
    split_settings: {
      target_scene_count: "auto",
      lipsync_ratio: "auto",
      route_preference: "mixed",
      cutting_style: "mixed_phrase",
      continuity_mode: "manual_last_frame_optional",
    },
    scenes: [],
    selectedSceneId: "",
  };
}

export function normalizeManualAudio(audio = null) {
  if (!audio || typeof audio !== "object") return { url: "", filename: "", duration_sec: 0, duration_ms: 0 };
  const url = String(audio.url || audio.value || audio.href || "").trim();
  const filename = String(audio.filename || audio.fileName || audio.name || audio.meta?.filename || "").trim();
  const durationSecRaw = Number(
    audio.duration_sec
    ?? audio.durationSec
    ?? audio.duration
    ?? audio.meta?.duration_sec
    ?? audio.meta?.duration
    ?? 0
  );
  const durationMsRaw = Number(
    audio.duration_ms
    ?? audio.durationMs
    ?? audio.meta?.duration_ms
    ?? 0
  );
  const duration_sec = Number.isFinite(durationSecRaw) ? Number(durationSecRaw.toFixed(3)) : 0;
  const duration_ms = Number.isFinite(durationMsRaw) && durationMsRaw > 0
    ? Math.round(durationMsRaw)
    : Math.round(duration_sec * 1000);
  return { url, filename, duration_sec, duration_ms };
}

export function buildManualClipSampleJson({ projectKind = "clip", durationSec = 56, format = "9:16" } = {}) {
  const kind = PROJECT_KINDS.includes(projectKind) ? projectKind : "clip";
  const audioDuration = Math.max(1, Number(durationSec) || 56);
  const globalHint = kind === "story"
    ? "Нарративная разбивка истории: делите по завершению фраз диктора, переходам сцен и эмоциональным паузам."
    : "Музыкальная фразовая разбивка клипа: ставьте границы на концах вокальных фраз и музыкальных акцентах.";

  const scenes = kind === "story"
    ? [
      { scene_id: "seg_01", index: 1, start_sec: 0.0, end_sec: 4.5, duration_sec: 4.5, route: "i2v", energy: "soft", quality: "good", boundary_reason: "narration_phrase_end", transition_out: "soft_cut_after_tail", drama_hint: "Экспозиция героя", short_note: "Короткое понимание сцены.", video_prompt: "", negative_prompt: "", sound_prompt: "" },
      { scene_id: "seg_02", index: 2, start_sec: 4.5, end_sec: 9.2, duration_sec: 4.7, route: "i2v_sound", energy: "mid", quality: "good", boundary_reason: "scene_transition", transition_out: "crossfade", drama_hint: "Переход к конфликту", short_note: "Короткое понимание сцены.", video_prompt: "", negative_prompt: "", sound_prompt: "" },
      { scene_id: "seg_03", index: 3, start_sec: 9.2, end_sec: 13.7, duration_sec: 4.5, route: "i2v", energy: "mid", quality: "check", boundary_reason: "emotional_pause", transition_out: "soft_cut_after_tail", drama_hint: "Эмоциональная остановка", short_note: "Короткое понимание сцены.", video_prompt: "", negative_prompt: "", sound_prompt: "" },
    ]
    : [
      { scene_id: "seg_01", index: 1, start_sec: 0.0, end_sec: 4.5, duration_sec: 4.5, route: "ia2v", energy: "soft", quality: "good", boundary_reason: "end_of_vocal_phrase", transition_out: "soft_cut_after_tail", drama_hint: "Вступление / герой настоящего", short_note: "Короткое понимание сцены.", video_prompt: "", negative_prompt: "", sound_prompt: "" },
      { scene_id: "seg_02", index: 2, start_sec: 4.5, end_sec: 8.9, duration_sec: 4.4, route: "i2v", energy: "mid", quality: "good", boundary_reason: "music_accent", transition_out: "cut_on_music_accent", drama_hint: "Развитие ритма", short_note: "Короткое понимание сцены.", video_prompt: "", negative_prompt: "", sound_prompt: "" },
      { scene_id: "seg_03", index: 3, start_sec: 8.9, end_sec: 13.2, duration_sec: 4.3, route: "i2v_sound", energy: "high", quality: "check", boundary_reason: "end_of_vocal_phrase", transition_out: "hard_cut_on_phrase_end", drama_hint: "Акцент припева", short_note: "Короткое понимание сцены.", video_prompt: "", negative_prompt: "", sound_prompt: "" },
    ];

  return {
    chatgpt_task: CHATGPT_STORY_SPLIT_TASK,
    prep_template_meta: STORY_PREP_TEMPLATE_META,
    mode: MANUAL_CLIP_MODE,
    project_kind: kind,
    format,
    split_type: "phrase_based",
    audio_duration_sec: Number(audioDuration.toFixed(3)),
    global_hint: globalHint,
    story_blocks: [],
    scenes,
  };
}

export function buildMockSplitJson({ projectKind = "clip", durationSec = 24, format = "9:16" } = {}) {
  return buildManualClipSampleJson({ projectKind, durationSec: Math.max(12, Number(durationSec) || 24), format });
}


export function normalizeStoryBlock(block = {}, idx = 0) {
  const id = String(block?.block_id || block?.id || block?.story_block_id || `block_${idx + 1}`).trim();
  const blockGoalRu = String(
    block?.block_goal_ru ||
    block?.blockGoalRu ||
    block?.goal_ru ||
    block?.story_block_goal_ru ||
    ""
  );
  const blockRevealRu = String(
    block?.block_reveal_ru ||
    block?.blockRevealRu ||
    block?.reveal_ru ||
    block?.story_block_reveal_ru ||
    ""
  );
  const blockEmotionRu = String(
    block?.block_emotion_ru ||
    block?.blockEmotionRu ||
    block?.emotion_ru ||
    block?.story_block_emotion_ru ||
    ""
  );

  return {
    ...block,
    block_id: id,
    id: String(block?.id || id),
    title_ru: String(block?.title_ru || block?.title || block?.name || id),
    summary_ru: String(block?.summary_ru || block?.summary || ""),
    color: String(block?.color || block?.story_block_color || "#8aa4ff"),
    block_goal_ru: blockGoalRu,
    block_reveal_ru: blockRevealRu,
    block_emotion_ru: blockEmotionRu,
    goal_ru: blockGoalRu,
    reveal_ru: blockRevealRu,
    emotion_ru: blockEmotionRu,
    scene_ids: Array.isArray(block?.scene_ids || block?.sceneIds)
      ? (block?.scene_ids || block?.sceneIds).map((id) => String(id || "").trim()).filter(Boolean)
      : [],
    start_sec: Number(block?.start_sec ?? block?.startSec ?? 0) || 0,
    end_sec: Number(block?.end_sec ?? block?.endSec ?? 0) || 0,
  };
}

export function buildStoryBlockLookup(storyBlocks = []) {
  const lookup = new Map();
  (Array.isArray(storyBlocks) ? storyBlocks : []).forEach((block, idx) => {
    const normalized = normalizeStoryBlock(block, idx);
    if (normalized.block_id) lookup.set(normalized.block_id, normalized);
    if (normalized.id) lookup.set(normalized.id, normalized);
  });
  return lookup;
}

export function toBool(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const v = value.trim().toLowerCase();
    if (["true", "1", "yes", "y", "on"].includes(v)) return true;
    if (["false", "0", "no", "n", "off", ""].includes(v)) return false;
  }
  return fallback;
}

export function parseManualSplitJson(rawText) {
  try {
    const parsed = JSON.parse(String(rawText || "").trim());
    const container = Array.isArray(parsed) ? { scenes: parsed } : (parsed?.data?.scenes ? { ...parsed, ...parsed.data } : parsed);
    const rawScenes = Array.isArray(container?.scenes) ? container.scenes : [];
    if (rawScenes.length === 0) return { ok: false, error: "JSON должен содержать непустой массив scenes." };

    const story_blocks = Array.isArray(container?.story_blocks) ? container.story_blocks.map(normalizeStoryBlock) : [];
    const storyBlockLookup = buildStoryBlockLookup(story_blocks);
    const scenes = rawScenes.map((scene, idx) => normalizeScene(scene, idx, storyBlockLookup));
    for (const scene of scenes) {
      if (!scene.scene_id) return { ok: false, error: "У каждой сцены должен быть scene_id." };
      if (!Number.isFinite(scene.start_sec) || !Number.isFinite(scene.end_sec)) return { ok: false, error: `Сцена ${scene.scene_id}: start_sec/end_sec должны быть числами.` };
      if (scene.end_sec <= scene.start_sec) return { ok: false, error: `Сцена ${scene.scene_id}: end_sec должен быть больше start_sec.` };
    }

    const inferredDuration = scenes.length
      ? Number(scenes[scenes.length - 1]?.end_sec || 0)
      : 0;

    const project_kind = PROJECT_KINDS.includes(container?.project_kind) ? container.project_kind : "clip";
    const splitJson = {
      mode: MANUAL_CLIP_MODE,
      project_kind,
      format: String(container?.format || "9:16"),
      split_type: String(container?.split_type || "phrase_based"),
      audio_duration_sec: Number(container?.audio_duration_sec || inferredDuration || 0),
      global_hint: String(container?.global_hint || ""),
      story_blocks,
      scenes,
    };

    return { ok: true, splitJson, scenes };
  } catch (error) {
    return { ok: false, error: `Ошибка JSON: ${error?.message || "неверный формат"}` };
  }
}

export function normalizeScene(scene, idx, storyBlockLookup = null) {
  const start = Number(scene?.start_sec || 0);
  const end = Number(scene?.end_sec || start);
  const route = ROUTES.includes(scene?.route) ? scene.route : "ia2v";
  const blockId = String(scene?.story_block_id || "").trim();
  const block = blockId && storyBlockLookup?.get ? storyBlockLookup.get(blockId) : null;
  return {
    scene_id: String(scene?.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene?.index || idx + 1),
    start_sec: start,
    end_sec: end,
    duration_sec: Number((Math.max(0, end - start)).toFixed(3)),
    route,
    energy: String(scene?.energy || "mid"),
    quality: String(scene?.quality || "check"),
    boundary_reason: String(scene?.boundary_reason || "uncertain_boundary"),
    boundary_confidence: String(scene?.boundary_confidence || ""),
    boundary_warning: String(scene?.boundary_warning || ""),
    use_sound_suggestion: toBool(scene?.use_sound_suggestion),
    contains_vocal_assumption: toBool(scene?.contains_vocal_assumption),
    contains_instrumental_assumption: toBool(scene?.contains_instrumental_assumption),
    contains_vocal: toBool(scene?.contains_vocal, toBool(scene?.contains_vocal_assumption)),
    contains_instrumental: toBool(scene?.contains_instrumental, toBool(scene?.contains_instrumental_assumption)),
    transition_out: String(scene?.transition_out || "soft_cut_after_tail"),
    story_time: String(scene?.story_time || ""),
    scene_type: String(scene?.scene_type || ""),
    drama_hint: String(scene?.drama_hint || ""),
    short_note: String(scene?.short_note || ""),
    scene_goal_ru: String(scene?.scene_goal_ru || ""),
    photo_prompt_hint_ru: String(scene?.photo_prompt_hint_ru || ""),
    prompt_hint_ru: String(scene?.prompt_hint_ru || scene?.photo_prompt_hint_ru || ""),
    user_note_ru: String(scene?.user_note_ru || scene?.user_notes_ru || ""),
    story_position_ru: String(scene?.story_position_ru || scene?.story_time || ""),
    story_block_id: blockId,
    story_block_title_ru: String(scene?.story_block_title_ru || block?.title_ru || ""),
    story_block_color: String(scene?.story_block_color || block?.color || ""),
    story_block_position_ru: String(scene?.story_block_position_ru || ""),
    story_block_goal_ru: String(scene?.story_block_goal_ru || block?.block_goal_ru || block?.goal_ru || ""),
    story_block_reveal_ru: String(scene?.story_block_reveal_ru || block?.block_reveal_ru || block?.reveal_ru || ""),
    story_block_emotion_ru: String(scene?.story_block_emotion_ru || block?.block_emotion_ru || block?.emotion_ru || ""),
    original_text: String(scene?.original_text || ""),
    translated_text_ru: String(scene?.translated_text_ru || ""),
    meaning_hint_ru: String(scene?.meaning_hint_ru || ""),
    source_text_en: String(scene?.source_text_en || ""),
    adapted_text_en: String(scene?.adapted_text_en || ""),
    scene_role_in_block_ru: String(scene?.scene_role_in_block_ru || ""),
    block_progress_ru: String(scene?.block_progress_ru || ""),
    image_url: String(scene?.image_url || scene?.start_image_url || ""),
    start_image_url: String(scene?.start_image_url || scene?.image_url || ""),
    end_image_url: String(scene?.end_image_url || ""),
    image_preview_url: String(scene?.image_preview_url || scene?.start_image_preview_url || ""),
    start_image_preview_url: String(scene?.start_image_preview_url || scene?.image_preview_url || ""),
    end_image_preview_url: String(scene?.end_image_preview_url || ""),
    image_upload_status: String(scene?.image_upload_status || ""),
    image_upload_error: String(scene?.image_upload_error || ""),
    video_prompt: String(scene?.video_prompt || ""),
    negative_prompt: String(scene?.negative_prompt || ""),
    sound_prompt: String(scene?.sound_prompt || ""),
    audio_slice_url: String(scene?.audio_slice_url || ""),
    audio_slice_duration_sec: Number(scene?.audio_slice_duration_sec || 0),
    video_url: String(scene?.video_url || ""),
    status: String(scene?.status || "draft"),
    error: String(scene?.error || ""),
    audio_extracted: Boolean(scene?.audio_extracted),
    video_job_id: String(scene?.video_job_id || ""),
    video_error: String(scene?.video_error || ""),
    video_has_audio: Boolean(scene?.video_has_audio),
    generated_audio_policy: String(scene?.generated_audio_policy || ""),
    generated_audio_gain_db: Number(scene?.generated_audio_gain_db ?? -16),
    keep_generated_audio: Boolean(scene?.keep_generated_audio),
    video_request_payload_preview: scene?.video_request_payload_preview || null,
  };
}

export function buildManualAudioSlicePayload({ audio, splitJson }) {
  // TODO: Later this payload will be sent to backend to create audio_slice_url for each scene.
  const safeAudio = normalizeManualAudio(audio);
  const sourceScenes = Array.isArray(splitJson?.scenes) ? splitJson.scenes : [];
  return {
    source: MANUAL_CLIP_MODE,
    audio_url: safeAudio.url,
    audio_filename: safeAudio.filename,
    project_kind: splitJson?.project_kind || "clip",
    format: splitJson?.format || "9:16",
    scenes: sourceScenes.map((scene, idx) => ({
      scene_id: String(scene?.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`),
      start_sec: Number(scene?.start_sec || 0),
      end_sec: Number(scene?.end_sec || 0),
    })),
  };
}

export function buildMontageManifest(data = {}) {
  const scenes = (Array.isArray(data?.scenes) ? data.scenes : [])
    .filter((scene) => scene?.status === "video_ready" && scene?.video_url)
    .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0))
    .map((scene, idx) => ({
      scene_id: scene.scene_id,
      sceneId: scene.scene_id,
      index: scene.index,
      order: Number(scene.index || idx + 1),
      start_sec: scene.start_sec,
      end_sec: scene.end_sec,
      startSec: scene.start_sec,
      endSec: scene.end_sec,
      duration_sec: scene.duration_sec,
      requestedDurationSec: Number(scene.duration_sec || Math.max(0, Number(scene.end_sec || 0) - Number(scene.start_sec || 0)) || 0),
      route: scene.route,
      mode: scene.route,
      video_url: scene.video_url,
      videoUrl: scene.video_url,
      video_has_audio: Boolean(scene.video_has_audio),
      videoHasAudio: Boolean(scene.video_has_audio),
      keep_generated_audio: Boolean(scene.keep_generated_audio),
      keepGeneratedAudio: Boolean(scene.keep_generated_audio),
      generated_audio_policy: scene.generated_audio_policy || "",
      generatedAudioPolicy: scene.generated_audio_policy || "",
      generated_audio_gain_db: Number(scene.generated_audio_gain_db ?? -16),
      generatedAudioGainDb: Number(scene.generated_audio_gain_db ?? -16),
      sound_prompt: scene.sound_prompt || "",
      soundPrompt: scene.sound_prompt || "",
    }));

  return {
    source: MANUAL_CLIP_MODE,
    sourceKind: MANUAL_CLIP_MODE,
    projectKind: String(data?.project_kind || "clip"),
    format: String(data?.format || "9:16"),
    audio_url: String(data?.audio?.url || ""),
    audioUrl: String(data?.audio?.url || ""),
    audio: data?.audio || null,
    scenes,
  };
}

function formatStoryPrepSeconds(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number.toFixed(2) : "0.00";
}

function compactStoryPrepText(...values) {
  return values.map((value) => String(value || "").trim()).find(Boolean) || "—";
}

function slugStoryPrepProjectName(value = "") {
  return String(value || "project")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9а-яё_-]+/gi, "_")
    .replace(/^_+|_+$/g, "") || "project";
}

function sceneFolderName(idx = 0) {
  return `seg_${String(idx + 1).padStart(2, "0")}`;
}

function inferSceneMaterials(scene = {}) {
  const route = String(scene?.route || "").trim();
  const text = [
    scene?.translated_text_ru,
    scene?.meaning_hint_ru,
    scene?.scene_goal_ru,
    scene?.photo_prompt_hint_ru,
    scene?.prompt_hint_ru,
    scene?.short_note,
    scene?.drama_hint,
  ].map((item) => String(item || "").trim()).filter(Boolean).join(" ");
  const needsLipSync = route === "ia2v";
  return {
    sourcePhotos: compactStoryPrepText(scene?.photo_prompt_hint_ru, scene?.prompt_hint_ru, "Подобрать стартовое изображение под смысл сцены."),
    generatedPhotos: compactStoryPrepText(scene?.scene_goal_ru, scene?.meaning_hint_ru, "При необходимости создать стартовый кадр для i2v."),
    character: needsLipSync ? "Да: нужен персонаж/лицо для lip-sync и эмоции." : (text ? "Проверить по фразе и смыслу сцены." : "По необходимости."),
    location: text ? "Определить локацию из фразы/meaning_hint/prompt_hint." : "По необходимости.",
    props: text ? "Выписать предметы/props из фразы и подсказок." : "По необходимости.",
    styleReference: "Нужен общий style reference проекта; для важных сцен добавить отдельные refs.",
  };
}

function collectStoryPrepBlocks(project = {}, scenes = []) {
  const rawBlocks = Array.isArray(project?.story_blocks) ? project.story_blocks : [];
  const blocks = rawBlocks.length
    ? rawBlocks.map((block, idx) => normalizeStoryBlock(block, idx))
    : [{
      block_id: "block_unassigned",
      id: "block_unassigned",
      title_ru: "Без блока",
      summary_ru: "Старый JSON без story_blocks — сцены перечислены плоским списком.",
      block_goal_ru: "Сгруппировать сцены вручную при подготовке материалов.",
      block_reveal_ru: "—",
      block_emotion_ru: "—",
      color: "#64748B",
      scene_ids: scenes.map((scene, idx) => String(scene?.scene_id || sceneFolderName(idx))),
      start_sec: Number(scenes[0]?.start_sec || 0),
      end_sec: Number(scenes[scenes.length - 1]?.end_sec || 0),
    }];

  const sceneById = new Map(scenes.map((scene, idx) => [String(scene?.scene_id || sceneFolderName(idx)), { scene, idx }]));
  const used = new Set();
  const grouped = blocks.map((block, blockIdx) => {
    const blockId = String(block.block_id || block.id || `block_${blockIdx + 1}`);
    let blockScenes = [];
    if (Array.isArray(block.scene_ids) && block.scene_ids.length) {
      blockScenes = block.scene_ids.map((sceneId) => sceneById.get(String(sceneId))).filter(Boolean);
    }
    if (!blockScenes.length) {
      blockScenes = scenes
        .map((scene, idx) => ({ scene, idx }))
        .filter(({ scene }) => String(scene?.story_block_id || "") === blockId);
    }
    blockScenes.forEach(({ scene, idx }) => used.add(String(scene?.scene_id || sceneFolderName(idx))));
    return { block, blockScenes };
  });

  const unassigned = scenes
    .map((scene, idx) => ({ scene, idx }))
    .filter(({ scene, idx }) => !used.has(String(scene?.scene_id || sceneFolderName(idx))));
  if (unassigned.length) {
    grouped.push({
      block: normalizeStoryBlock({
        block_id: "block_unassigned",
        title_ru: "Без блока",
        summary_ru: "Сцены без story_block_id или без связи с существующими story_blocks.",
        color: "#64748B",
        scene_ids: unassigned.map(({ scene, idx }) => String(scene?.scene_id || sceneFolderName(idx))),
        start_sec: Number(unassigned[0]?.scene?.start_sec || 0),
        end_sec: Number(unassigned[unassigned.length - 1]?.scene?.end_sec || 0),
      }, grouped.length),
      blockScenes: unassigned,
    });
  }
  return grouped;
}

export function buildStoryPrepTemplateText(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const scenes = Array.isArray(safeProject.scenes) ? safeProject.scenes : [];
  const audio = normalizeManualAudio(safeProject.audio);
  const title = compactStoryPrepText(safeProject.name, safeProject.title, safeProject.ruLabel, safeProject.project_name, "PhotoStudio project");
  const idea = compactStoryPrepText(
    safeProject.idea_ru,
    safeProject.idea,
    safeProject.global_hint,
    safeProject.story_request_ru,
    safeProject.split_chat?.user_request,
    safeProject.split_chat?.ai_summary
  );
  const durationSec = Number(audio.duration_sec || safeProject.audio_duration_sec || 0);
  const groupedBlocks = collectStoryPrepBlocks(safeProject, scenes);
  const projectFolder = slugStoryPrepProjectName(title);
  const lines = [];

  lines.push("# ШАБЛОН ПОДГОТОВКИ СЮЖЕТА");
  lines.push("");
  lines.push("## 1. ОБЩАЯ ИНФОРМАЦИЯ");
  lines.push(`- Название проекта: ${title}`);
  lines.push(`- Тип проекта: ${compactStoryPrepText(safeProject.project_kind, safeProject.projectKind)}`);
  lines.push(`- Формат: ${compactStoryPrepText(safeProject.format)}`);
  lines.push(`- Аудио: ${compactStoryPrepText(audio.filename, safeProject.audio_filename, "не указано")}`);
  lines.push(`- Длительность аудио: ${formatStoryPrepSeconds(durationSec)} сек`);
  lines.push(`- Общее количество блоков: ${groupedBlocks.length}`);
  lines.push(`- Общее количество сцен: ${scenes.length}`);
  lines.push(`- Краткая идея / запрос: ${idea}`);
  lines.push("");

  lines.push("## 2. СПИСОК БЛОКОВ");
  groupedBlocks.forEach(({ block, blockScenes }, blockIdx) => {
    const start = block.start_sec || blockScenes[0]?.scene?.start_sec || 0;
    const end = block.end_sec || blockScenes[blockScenes.length - 1]?.scene?.end_sec || start;
    lines.push(`### Блок ${blockIdx + 1}: ${compactStoryPrepText(block.title_ru, block.block_id)}`);
    lines.push(`- Время: ${formatStoryPrepSeconds(start)} – ${formatStoryPrepSeconds(end)} сек`);
    lines.push(`- Сцен в блоке: ${blockScenes.length}`);
    lines.push(`- Summary RU: ${compactStoryPrepText(block.summary_ru)}`);
    lines.push(`- Block goal RU: ${compactStoryPrepText(block.block_goal_ru, block.goal_ru)}`);
    lines.push(`- Block reveal RU: ${compactStoryPrepText(block.block_reveal_ru, block.reveal_ru)}`);
    lines.push(`- Block emotion RU: ${compactStoryPrepText(block.block_emotion_ru, block.emotion_ru)}`);
    lines.push(`- Материалы блока: refs/blocks/block_${String(blockIdx + 1).padStart(2, "0")}/, сцены: ${blockScenes.map(({ idx }) => `scenes/${sceneFolderName(idx)}/`).join(", ") || "—"}`);
    lines.push("");
  });

  lines.push("## 3. СЦЕНЫ ВНУТРИ КАЖДОГО БЛОКА");
  groupedBlocks.forEach(({ block, blockScenes }, blockIdx) => {
    lines.push(`### Блок ${blockIdx + 1}: ${compactStoryPrepText(block.title_ru, block.block_id)}`);
    if (!blockScenes.length) {
      lines.push("- В этом блоке пока нет сцен.");
      lines.push("");
      return;
    }
    blockScenes.forEach(({ scene, idx }, localIdx) => {
      const sceneId = String(scene?.scene_id || sceneFolderName(idx));
      const phraseRu = compactStoryPrepText(scene?.translated_text_ru, scene?.short_note, scene?.drama_hint, scene?.scene_goal_ru);
      const materials = inferSceneMaterials(scene);
      lines.push(`#### Сцена ${idx + 1} (${sceneId})`);
      lines.push(`- Номер внутри блока: ${localIdx + 1} из ${blockScenes.length}`);
      lines.push(`- Route: ${compactStoryPrepText(scene?.route, "i2v")}`);
      lines.push(`- Тайминг: ${formatStoryPrepSeconds(scene?.start_sec)} – ${formatStoryPrepSeconds(scene?.end_sec)} сек`);
      lines.push(`- Полная фраза RU (translated_text_ru): ${phraseRu}`);
      lines.push(`- Original/adapted/source EN: ${compactStoryPrepText(scene?.original_text, scene?.adapted_text_en, scene?.source_text_en)}`);
      lines.push(`- Meaning hint RU: ${compactStoryPrepText(scene?.meaning_hint_ru)}`);
      lines.push(`- Story block id/title: ${compactStoryPrepText(scene?.story_block_id, block.block_id)} / ${compactStoryPrepText(scene?.story_block_title_ru, block.title_ru)}`);
      lines.push(`- Story block position RU: ${compactStoryPrepText(scene?.story_block_position_ru)}`);
      lines.push(`- Scene role in block RU: ${compactStoryPrepText(scene?.scene_role_in_block_ru)}`);
      lines.push(`- Block progress RU: ${compactStoryPrepText(scene?.block_progress_ru)}`);
      lines.push(`- Scene goal RU: ${compactStoryPrepText(scene?.scene_goal_ru)}`);
      lines.push(`- Photo prompt hint RU: ${compactStoryPrepText(scene?.photo_prompt_hint_ru)}`);
      lines.push(`- Prompt hint RU: ${compactStoryPrepText(scene?.prompt_hint_ru)}`);
      lines.push("- Что подготовить для сцены:");
      lines.push(`  - Какие фото достать: ${materials.sourcePhotos}`);
      lines.push(`  - Какие фото создать: ${materials.generatedPhotos}`);
      lines.push(`  - Нужен ли персонаж: ${materials.character}`);
      lines.push(`  - Нужна ли локация: ${materials.location}`);
      lines.push(`  - Нужны ли props: ${materials.props}`);
      lines.push(`  - Нужен ли style reference: ${materials.styleReference}`);
      lines.push(`- Suggested folder: scenes/${sceneFolderName(idx)}/`);
      lines.push("- Suggested files:");
      lines.push("  - source_image.png");
      lines.push("  - refs.txt");
      lines.push("  - notes.txt");
      lines.push("");
    });
  });

  lines.push("## 4. СПИСОК НУЖНЫХ МАТЕРИАЛОВ");
  lines.push("### ПЕРСОНАЖИ");
  lines.push("- Выписать постоянных героев, лица, эмоции, возраст, одежду, особенности, фото для ia2v/lip-sync.");
  lines.push("### ЛОКАЦИИ");
  lines.push("- Для каждого блока и сцены собрать/создать окружение, время суток, погоду, географию, интерьер/экстерьер.");
  lines.push("### ПРЕДМЕТЫ / PROPS");
  lines.push("- Отдельно перечислить предметы из фраз, смысловых подсказок и prompt_hint_ru.");
  lines.push("### СТИЛЬ / АТМОСФЕРА");
  lines.push("- Общий visual style, цвет, свет, оптика, настроение, референсы по кадру.");
  lines.push("### ДОПОЛНИТЕЛЬНЫЕ РЕФЕРЕНСЫ");
  lines.push("- Карты, эпоха, костюмы, животные, транспорт, реквизит, moodboard.");
  lines.push("");

  lines.push("## 5. ПАПКИ ПРОЕКТА");
  lines.push(`${projectFolder}/`);
  lines.push("  audio/");
  lines.push("  json/");
  lines.push("  refs/");
  lines.push("  scenes/");
  scenes.forEach((scene, idx) => {
    lines.push(`    ${sceneFolderName(idx)}/`);
  });
  if (!scenes.length) lines.push("    seg_01/");
  lines.push("");

  const hasAudio = Boolean(audio.url || audio.filename || durationSec > 0);
  const hasBlocks = groupedBlocks.some(({ block }) => String(block.block_id || "") !== "block_unassigned") || Array.isArray(safeProject.story_blocks) && safeProject.story_blocks.length > 0;
  const hasTimings = scenes.length > 0 && scenes.every((scene) => Number(scene?.end_sec) > Number(scene?.start_sec));
  const hasRu = scenes.length > 0 && scenes.every((scene) => String(scene?.translated_text_ru || scene?.short_note || scene?.drama_hint || scene?.scene_goal_ru || "").trim());

  lines.push("## 6. ЧЕКЛИСТ");
  lines.push(`- [${hasAudio ? "x" : " "}] Есть аудио`);
  lines.push(`- [${scenes.length ? "x" : " "}] Есть JSON / scenes`);
  lines.push(`- [${hasBlocks ? "x" : " "}] Есть story_blocks`);
  lines.push(`- [${hasTimings ? "x" : " "}] Есть тайминги для каждой сцены`);
  lines.push(`- [${hasRu ? "x" : " "}] Есть фраза RU / fallback-смысл для каждой сцены`);
  lines.push("- [ ] Есть понимание, какие фото нужны");
  lines.push("- [ ] Есть стартовые изображения для i2v");
  lines.push("- [ ] Есть фото лица / эмоции для ia2v");
  lines.push("- [ ] Есть refs по локации и props");

  return lines.join("\n");
}
