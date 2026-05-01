export const MANUAL_CLIP_MODE = "manual_clip_board";
export const MANUAL_CLIP_STEPS = ["empty", "audio_loaded", "split_chat_ready", "scene_plan_ready", "director_board"];
export const ROUTES = ["ia2v", "i2v", "i2v_sound"];

export function getDefaultManualClipNodeData() {
  return {
    mode: MANUAL_CLIP_MODE,
    title: "AI-разбивка клипа",
    ruLabel: "AI-разбивка клипа",
    step: "empty",
    format: "9:16",
    audio: { url: "", filename: "", duration_sec: 0, duration_ms: 0 },
    split_chat: { user_request: "", ai_summary: "", raw_ai_json: null },
    split_settings: {
      target_scene_count: "auto",
      lipsync_ratio: "auto",
      route_preference: "mixed",
    },
    scenes: [],
    selectedSceneId: "",
  };
}

export function buildMockSplitJson(durationSec = 24) {
  const total = Math.max(12, Number(durationSec) || 24);
  const scenes = [
    { scene_id: "SEG_01", index: 1, start_sec: 0, end_sec: Math.min(3.65, total), route: "ia2v", energy: "soft", quality: "good", boundary_reason: "end_of_vocal_phrase", transition_out: "soft_cut_after_tail", drama_hint: "Вступление", short_note: "Задаём эмоциональный тон." },
    { scene_id: "SEG_02", index: 2, start_sec: Math.min(3.65, total), end_sec: Math.min(8.2, total), route: "i2v", energy: "mid", quality: "check", boundary_reason: "music_accent", transition_out: "cut_on_music_accent", drama_hint: "Первый конфликт", short_note: "Сюжетная перебивка." },
    { scene_id: "SEG_03", index: 3, start_sec: Math.min(8.2, total), end_sec: Math.min(12.8, total), route: "i2v_sound", energy: "high", quality: "good", boundary_reason: "phrase_tail_and_music_breath", transition_out: "hard_cut_on_phrase_end", drama_hint: "Эскалация", short_note: "Рост напряжения." },
  ].map((s) => ({ ...s, duration_sec: Math.max(0, Number((s.end_sec - s.start_sec).toFixed(3))), image_url: "", video_prompt: "", negative_prompt: "", sound_prompt: "", audio_slice_url: "", video_url: "", status: "draft", error: "" }));

  return { mode: MANUAL_CLIP_MODE, split_type: "phrase_based", audio_duration_sec: total, global_hint: "Фразовая разбивка с опорой на паузы и акценты.", scenes };
}

export function normalizeScene(scene, idx) {
  const start = Number(scene?.start_sec || 0);
  const end = Number(scene?.end_sec || start);
  const route = ROUTES.includes(scene?.route) ? scene.route : "ia2v";
  return {
    scene_id: String(scene?.scene_id || `SEG_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene?.index || idx + 1),
    start_sec: start,
    end_sec: end,
    duration_sec: Number((Math.max(0, end - start)).toFixed(3)),
    route,
    energy: String(scene?.energy || "mid"),
    quality: String(scene?.quality || "check"),
    boundary_reason: String(scene?.boundary_reason || "uncertain_boundary"),
    transition_out: String(scene?.transition_out || "soft_cut_after_tail"),
    drama_hint: String(scene?.drama_hint || ""),
    short_note: String(scene?.short_note || ""),
    image_url: String(scene?.image_url || ""),
    video_prompt: String(scene?.video_prompt || ""),
    negative_prompt: String(scene?.negative_prompt || ""),
    sound_prompt: String(scene?.sound_prompt || ""),
    audio_slice_url: String(scene?.audio_slice_url || ""),
    video_url: String(scene?.video_url || ""),
    status: String(scene?.status || "draft"),
    error: String(scene?.error || ""),
  };
}

export function buildMontageManifest(data = {}) {
  const scenes = (Array.isArray(data?.scenes) ? data.scenes : [])
    .filter((scene) => scene?.status === "video_ready" && scene?.video_url)
    .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0))
    .map((scene) => ({ scene_id: scene.scene_id, index: scene.index, start_sec: scene.start_sec, end_sec: scene.end_sec, duration_sec: scene.duration_sec, route: scene.route, video_url: scene.video_url }));

  return { source: MANUAL_CLIP_MODE, format: String(data?.format || "9:16"), audio_url: String(data?.audio?.url || ""), scenes };
}
