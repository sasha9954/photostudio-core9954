export const MANUAL_CLIP_MODE = "manual_clip_board";
export const MANUAL_CLIP_STEPS = ["empty", "audio_loaded", "split_chat_ready", "scene_plan_ready"];
export const ROUTES = ["ia2v", "i2v", "i2v_sound", "first_last", "first_last_sound"];
export const PROJECT_KINDS = ["clip", "story"];
export const SPLIT_SOURCES = ["ai", "json"];

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
    mode: MANUAL_CLIP_MODE,
    project_kind: kind,
    format,
    split_type: "phrase_based",
    audio_duration_sec: Number(audioDuration.toFixed(3)),
    global_hint: globalHint,
    scenes,
  };
}

export function buildMockSplitJson({ projectKind = "clip", durationSec = 24, format = "9:16" } = {}) {
  return buildManualClipSampleJson({ projectKind, durationSec: Math.max(12, Number(durationSec) || 24), format });
}

export function parseManualSplitJson(rawText) {
  try {
    const parsed = JSON.parse(String(rawText || "").trim());
    const container = Array.isArray(parsed) ? { scenes: parsed } : (parsed?.data?.scenes ? { ...parsed, ...parsed.data } : parsed);
    const rawScenes = Array.isArray(container?.scenes) ? container.scenes : [];
    if (rawScenes.length === 0) return { ok: false, error: "JSON должен содержать непустой массив scenes." };

    const scenes = rawScenes.map((scene, idx) => normalizeScene(scene, idx));
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
      scenes,
    };

    return { ok: true, splitJson, scenes };
  } catch (error) {
    return { ok: false, error: `Ошибка JSON: ${error?.message || "неверный формат"}` };
  }
}

export function normalizeScene(scene, idx) {
  const start = Number(scene?.start_sec || 0);
  const end = Number(scene?.end_sec || start);
  const route = ROUTES.includes(scene?.route) ? scene.route : "ia2v";
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
    use_sound_suggestion: String(scene?.use_sound_suggestion || ""),
    contains_vocal_assumption: Boolean(scene?.contains_vocal_assumption),
    contains_instrumental_assumption: Boolean(scene?.contains_instrumental_assumption),
    contains_vocal: Boolean(scene?.contains_vocal ?? scene?.contains_vocal_assumption),
    contains_instrumental: Boolean(scene?.contains_instrumental ?? scene?.contains_instrumental_assumption),
    transition_out: String(scene?.transition_out || "soft_cut_after_tail"),
    story_time: String(scene?.story_time || ""),
    scene_type: String(scene?.scene_type || ""),
    drama_hint: String(scene?.drama_hint || ""),
    short_note: String(scene?.short_note || ""),
    scene_goal_ru: String(scene?.scene_goal_ru || ""),
    photo_prompt_hint_ru: String(scene?.photo_prompt_hint_ru || ""),
    prompt_hint_ru: String(scene?.prompt_hint_ru || scene?.photo_prompt_hint_ru || ""),
    story_position_ru: String(scene?.story_position_ru || scene?.story_time || ""),
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
