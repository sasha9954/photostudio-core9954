export const MANUAL_TIMING_MODE = "manual_timing";
export const MANUAL_TIMING_ACTIVE_PROJECT_KEY = "manual_timing_active_project";
export const MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY = "manual_timing_active_project_id";

export const MANUAL_TIMING_SECTIONS = ["intro", "verse", "chorus", "bridge", "instrumental", "outro"];
export const MANUAL_TIMING_ROUTES = ["ia2v", "i2v"];
export const MANUAL_TIMING_ENERGY = ["soft", "mid", "high"];

const MANUAL_TIMING_SECTION_LABELS_RU = {
  intro: "вступление",
  verse: "куплет",
  chorus: "припев",
  bridge: "бридж",
  instrumental: "проигрыш",
  outro: "финал",
};

function sectionLabelRu(section = "") {
  const key = String(section || "").trim().toLowerCase();
  return MANUAL_TIMING_SECTION_LABELS_RU[key] || key || "не указана";
}

export function getManualTimingProjectStorageKey(nodeId = "") {
  const safeId = String(nodeId || "default").trim() || "default";
  return `manual_timing_project:${safeId}`;
}

export function readManualTimingJsonStorage(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function readManualTimingProjectForNode(nodeId = "") {
  const safeId = String(nodeId || "").trim();
  const active = readManualTimingJsonStorage(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
  if (active && (!safeId || String(active?.nodeId || "") === safeId)) return active;
  const scoped = readManualTimingJsonStorage(getManualTimingProjectStorageKey(safeId));
  if (scoped && (!safeId || String(scoped?.nodeId || "") === safeId)) return scoped;
  return null;
}

export function persistManualTimingProject(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  try {
    const serialized = JSON.stringify(safeProject);
    localStorage.setItem(MANUAL_TIMING_ACTIVE_PROJECT_KEY, serialized);
    const nodeId = String(safeProject?.nodeId || "").trim();
    if (nodeId) {
      localStorage.setItem(MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY, nodeId);
      localStorage.setItem(getManualTimingProjectStorageKey(nodeId), serialized);
    }
  } catch {}
}

export function removeManualTimingProjectForNode(nodeId = "") {
  const safeId = String(nodeId || "").trim();
  try {
    if (safeId) localStorage.removeItem(getManualTimingProjectStorageKey(safeId));
    const active = readManualTimingJsonStorage(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
    if (!safeId || String(active?.nodeId || "") === safeId) {
      localStorage.removeItem(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
      localStorage.removeItem(MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY);
    }
  } catch {}
}

export function getDefaultManualTimingNodeData() {
  return {
    mode: MANUAL_TIMING_MODE,
    project_kind: "clip",
    format: "9:16",
    audio: {
      url: "",
      filename: "",
      duration_sec: 0,
      duration_ms: 0,
    },
    timing_status: "empty",
    markers: [],
    scenes: [],
    selectedSceneId: "",
    updatedAt: 0,
  };
}

export function normalizeManualTimingAudio(audio = null) {
  if (!audio || typeof audio !== "object") return { url: "", filename: "", duration_sec: 0, duration_ms: 0 };
  const url = String(audio.url || audio.value || audio.href || "").trim();
  const filename = String(audio.filename || audio.fileName || audio.name || audio.preview || audio.meta?.filename || "").trim();
  const durationSecRaw = Number(
    audio.duration_sec
    ?? audio.durationSec
    ?? audio.audioDurationSec
    ?? audio.duration
    ?? audio.meta?.duration_sec
    ?? audio.meta?.durationSec
    ?? audio.meta?.audioDurationSec
    ?? audio.meta?.duration
    ?? 0
  );
  const durationMsRaw = Number(audio.duration_ms ?? audio.durationMs ?? audio.meta?.duration_ms ?? 0);
  const duration_sec = Number.isFinite(durationSecRaw) ? Number(durationSecRaw.toFixed(3)) : 0;
  const duration_ms = Number.isFinite(durationMsRaw) && durationMsRaw > 0 ? Math.round(durationMsRaw) : Math.round(duration_sec * 1000);
  return { url, filename, duration_sec, duration_ms };
}

export function roundTimingSec(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return 0;
  return Number(n.toFixed(3));
}

export function formatTimingSec(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0:00.000";
  const minutes = Math.floor(n / 60);
  const seconds = Math.floor(n % 60);
  const millis = Math.round((n - Math.floor(n)) * 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

export function normalizeManualTimingSection(section = "") {
  const value = String(section || "").trim().toLowerCase();
  return MANUAL_TIMING_SECTIONS.includes(value) ? value : "verse";
}

export function normalizeManualTimingRoute(route = "") {
  const value = String(route || "").trim().toLowerCase();
  return MANUAL_TIMING_ROUTES.includes(value) ? value : "i2v";
}

export function normalizeManualTimingEnergy(energy = "") {
  const value = String(energy || "").trim().toLowerCase();
  return MANUAL_TIMING_ENERGY.includes(value) ? value : "mid";
}

export function getSectionDefaults(section = "") {
  const normalized = normalizeManualTimingSection(section);
  if (normalized === "intro" || normalized === "instrumental" || normalized === "outro") {
    return {
      section: normalized,
      route: "i2v",
      contains_vocal: false,
      contains_vocal_assumption: false,
      contains_instrumental_assumption: true,
    };
  }
  return {
    section: normalized,
    route: "ia2v",
    contains_vocal: true,
    contains_vocal_assumption: true,
    contains_instrumental_assumption: false,
  };
}

export function normalizeManualTimingMarkers(markers = [], durationSec = 0) {
  const duration = roundTimingSec(durationSec);
  const values = (Array.isArray(markers) ? markers : [])
    .map((value) => roundTimingSec(value))
    .filter((value) => Number.isFinite(value) && value >= 0 && (!duration || value <= duration));

  values.push(0);
  if (duration > 0) values.push(duration);

  const sorted = [...new Set(values.map((value) => value.toFixed(3)))]
    .map((value) => Number(value))
    .sort((a, b) => a - b);

  const deduped = [];
  for (const value of sorted) {
    const prev = deduped[deduped.length - 1];
    if (prev === undefined || Math.abs(value - prev) >= 0.001) deduped.push(value);
  }
  return deduped;
}

function buildScenePreserveMaps(existingScenes = []) {
  const byId = new Map();
  const byTimeline = new Map();
  (Array.isArray(existingScenes) ? existingScenes : []).forEach((scene) => {
    const id = String(scene?.scene_id || "").trim();
    if (id && !byId.has(id)) byId.set(id, scene);
    const key = `${roundTimingSec(scene?.start_sec).toFixed(3)}|${roundTimingSec(scene?.end_sec).toFixed(3)}`;
    if (key !== "0.000|0.000" && !byTimeline.has(key)) byTimeline.set(key, scene);
  });
  return { byId, byTimeline };
}

export function buildManualTimingScenesFromMarkers(markers = [], existingScenes = [], options = {}) {
  const duration = Number(options.durationSec || 0);
  const safeMarkers = normalizeManualTimingMarkers(markers, duration);
  const { byId, byTimeline } = buildScenePreserveMaps(existingScenes);
  const scenes = [];

  for (let i = 0; i < safeMarkers.length - 1; i += 1) {
    const start = roundTimingSec(safeMarkers[i]);
    const end = roundTimingSec(safeMarkers[i + 1]);
    if (!(end > start)) continue;
    const sceneId = `seg_${String(i + 1).padStart(2, "0")}`;
    const timelineKey = `${start.toFixed(3)}|${end.toFixed(3)}`;
    const old = byTimeline.get(timelineKey) || byId.get(sceneId) || {};
    const section = normalizeManualTimingSection(old.section || (i === 0 ? "intro" : "verse"));
    const defaults = getSectionDefaults(section);
    const route = normalizeManualTimingRoute(old.route || defaults.route);
    const containsVocal = typeof old.contains_vocal === "boolean"
      ? old.contains_vocal
      : Boolean(old.contains_vocal_assumption ?? defaults.contains_vocal);
    const containsInstrumental = typeof old.contains_instrumental === "boolean"
      ? old.contains_instrumental
      : Boolean(old.contains_instrumental_assumption ?? !containsVocal);

    scenes.push({
      scene_id: sceneId,
      index: i + 1,
      start_sec: start,
      end_sec: end,
      duration_sec: roundTimingSec(end - start),
      section,
      route,
      contains_vocal: containsVocal,
      contains_vocal_assumption: Boolean(old.contains_vocal_assumption ?? containsVocal),
      contains_instrumental_assumption: Boolean(old.contains_instrumental_assumption ?? containsInstrumental),
      use_sound_suggestion: Boolean(old.use_sound_suggestion || false),
      energy: normalizeManualTimingEnergy(old.energy || "mid"),
      quality: String(old.quality || "manual_draft"),
      boundary_reason: String(old.boundary_reason || "manual_marker"),
      transition_out: String(old.transition_out || "manual_cut"),
      story_time: String(old.story_time || ""),
      scene_type: String(old.scene_type || ""),
      drama_hint: String(old.drama_hint || ""),
      short_note: String(old.short_note || ""),
      scene_goal_ru: String(old.scene_goal_ru || ""),
      photo_prompt_hint_ru: String(old.photo_prompt_hint_ru || ""),
      prompt_hint_ru: String(old.prompt_hint_ru || old.photo_prompt_hint_ru || ""),
      story_position_ru: String(old.story_position_ru || old.story_time || ""),
      user_note_ru: String(old.user_note_ru || old.user_notes_ru || ""),
      video_prompt: String(old.video_prompt || ""),
      negative_prompt: String(old.negative_prompt || ""),
      sound_prompt: String(old.sound_prompt || ""),
    });
  }

  return scenes;
}

export function updateManualTimingSceneById(scenes = [], sceneId = "", patch = {}) {
  const safePatch = patch && typeof patch === "object" ? patch : {};
  return (Array.isArray(scenes) ? scenes : []).map((scene) => {
    if (String(scene?.scene_id || "") !== String(sceneId || "")) return scene;
    const next = { ...scene, ...safePatch };
    const sectionChanged = Object.prototype.hasOwnProperty.call(safePatch, "section");
    const routeChanged = Object.prototype.hasOwnProperty.call(safePatch, "route");
    const containsVocalChanged = Object.prototype.hasOwnProperty.call(safePatch, "contains_vocal");

    if (sectionChanged) {
      const defaults = getSectionDefaults(safePatch.section);
      next.section = defaults.section;
      if (!routeChanged) next.route = defaults.route;
      if (!containsVocalChanged) {
        next.contains_vocal = defaults.contains_vocal;
        next.contains_vocal_assumption = defaults.contains_vocal_assumption;
        next.contains_instrumental_assumption = defaults.contains_instrumental_assumption;
      }
    }

    if (containsVocalChanged) {
      next.contains_vocal = Boolean(safePatch.contains_vocal);
      next.contains_vocal_assumption = Boolean(safePatch.contains_vocal);
      next.contains_instrumental_assumption = !Boolean(safePatch.contains_vocal);
    }

    if (routeChanged) next.route = safePatch.route;
    next.section = normalizeManualTimingSection(next.section);
    next.route = normalizeManualTimingRoute(next.route);
    next.energy = normalizeManualTimingEnergy(next.energy);
    next.use_sound_suggestion = Boolean(next.use_sound_suggestion);
    return next;
  });
}



function toManualTimingBool(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "y", "on", "да"].includes(normalized)) return true;
    if (["false", "0", "no", "n", "off", "нет", ""].includes(normalized)) return false;
  }
  return Boolean(fallback);
}

function pickManualTimingText(scene = {}, keys = []) {
  for (const key of keys) {
    const value = scene?.[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value);
  }
  return "";
}

function normalizeManualTimingSceneForImport(scene = {}, idx = 0) {
  const rawSection = scene?.section || scene?.story_section || scene?.song_section || scene?.scene_section;
  const section = normalizeManualTimingSection(rawSection || (idx === 0 ? "intro" : "verse"));
  const defaults = getSectionDefaults(section);
  const route = normalizeManualTimingRoute(scene?.route || scene?.video_generation_route || scene?.renderMode || defaults.route);
  const containsVocal = toManualTimingBool(
    scene?.contains_vocal,
    toManualTimingBool(scene?.contains_vocal_assumption, defaults.contains_vocal)
  );
  const containsInstrumental = toManualTimingBool(
    scene?.contains_instrumental,
    toManualTimingBool(scene?.contains_instrumental_assumption, !containsVocal)
  );

  const start = roundTimingSec(scene?.start_sec ?? scene?.startSec ?? scene?.start ?? 0);
  const end = roundTimingSec(scene?.end_sec ?? scene?.endSec ?? scene?.end ?? start);

  return {
    scene_id: String(scene?.scene_id || scene?.sceneId || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene?.index || idx + 1),
    start_sec: start,
    end_sec: end,
    duration_sec: roundTimingSec(scene?.duration_sec ?? scene?.durationSec ?? (end - start)),
    section,
    route,
    contains_vocal: containsVocal,
    contains_vocal_assumption: toManualTimingBool(scene?.contains_vocal_assumption, containsVocal),
    contains_instrumental_assumption: toManualTimingBool(scene?.contains_instrumental_assumption, containsInstrumental),
    use_sound_suggestion: toManualTimingBool(scene?.use_sound_suggestion, false),
    energy: normalizeManualTimingEnergy(scene?.energy || "mid"),
    quality: String(scene?.quality || "manual_draft"),
    boundary_reason: String(scene?.boundary_reason || "json_import"),
    transition_out: String(scene?.transition_out || "manual_cut"),
    story_time: String(scene?.story_time || ""),
    scene_type: String(scene?.scene_type || ""),
    drama_hint: pickManualTimingText(scene, ["drama_hint", "dramaHint", "scene_drama_ru"]),
    short_note: pickManualTimingText(scene, ["short_note", "shortNote", "note", "summary_ru"]),
    scene_goal_ru: pickManualTimingText(scene, ["scene_goal_ru", "sceneGoalRu", "goal_ru", "goal"]),
    photo_prompt_hint_ru: pickManualTimingText(scene, ["photo_prompt_hint_ru", "photoPromptHintRu", "prompt_hint_ru", "visual_hint_ru"]),
    prompt_hint_ru: pickManualTimingText(scene, ["prompt_hint_ru", "photo_prompt_hint_ru", "promptHintRu", "visual_hint_ru"]),
    story_position_ru: pickManualTimingText(scene, ["story_position_ru", "story_time", "storyPositionRu"]),
    user_note_ru: pickManualTimingText(scene, ["user_note_ru", "user_notes_ru", "userNoteRu", "note_ru", "director_note_ru"]),
    video_prompt: "",
    negative_prompt: "",
    sound_prompt: "",
  };
}

export function buildManualTimingSampleJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const existingScenes = Array.isArray(safeProject.scenes) ? safeProject.scenes : [];
  const scenes = existingScenes.length ? buildManualTimingExportJson(safeProject).scenes : [
    {
      scene_id: "seg_01",
      index: 1,
      start_sec: 0,
      end_sec: 5,
      duration_sec: 5,
      section: "intro",
      route: "i2v",
      contains_vocal: false,
      contains_vocal_assumption: false,
      contains_instrumental_assumption: true,
      use_sound_suggestion: true,
      energy: "soft",
      quality: "manual_draft",
      boundary_reason: "json_import",
      transition_out: "manual_cut",
      story_time: "настоящее / прошлое / флешбэк",
      scene_type: "intro / performance / flashback / cutaway",
      drama_hint: "Коротко: что происходит драматургически.",
      short_note: "Короткая подпись для карточки сцены.",
      scene_goal_ru: "Зачем нужна сцена в сюжете.",
      photo_prompt_hint_ru: "Что учесть при создании фото.",
      prompt_hint_ru: "Что учесть в видео-промте.",
      story_position_ru: "Позиция в истории.",
      user_note_ru: "Твоя заметка: звук, фраза, визуал, что не забыть.",
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: ""
    }
  ];

  return {
    mode: "manual_clip_board",
    project_kind: String(safeProject.project_kind || "clip"),
    format: String(safeProject.format || "9:16"),
    split_type: existingScenes.length ? "manual_timing_export_for_chatgpt" : "manual_timing_template_for_chatgpt",
    audio_duration_sec: Number(audio.duration_sec || 0),
    global_hint: "Заполни/поправь scenes: тайминги start_sec/end_sec, section, route, contains_vocal, energy, подсказки и user_note_ru. Prompts оставь пустыми.",
    scenes,
  };
}

export function normalizeManualTimingProjectFromJson(raw = {}, baseProject = {}) {
  const safeRaw = raw && typeof raw === "object" ? raw : {};
  const safeBase = baseProject && typeof baseProject === "object" ? baseProject : {};
  const baseAudio = normalizeManualTimingAudio(safeBase.audio);
  const rawDuration = Number(safeRaw.audio_duration_sec ?? safeRaw.audioDurationSec ?? safeRaw.duration_sec ?? safeRaw.durationSec ?? safeRaw.audio?.duration_sec ?? 0);
  const durationSec = roundTimingSec(baseAudio.duration_sec || rawDuration || 0);
  const rawScenes = Array.isArray(safeRaw.scenes) ? safeRaw.scenes : [];
  const importedScenes = rawScenes
    .map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx))
    .filter((scene) => Number(scene.end_sec) > Number(scene.start_sec));

  const markerValues = [];
  importedScenes.forEach((scene) => {
    markerValues.push(scene.start_sec);
    markerValues.push(scene.end_sec);
  });
  if (durationSec > 0) {
    markerValues.push(0);
    markerValues.push(durationSec);
  }

  const markers = normalizeManualTimingMarkers(markerValues, durationSec || importedScenes[importedScenes.length - 1]?.end_sec || 0);
  const finalDuration = durationSec || markers[markers.length - 1] || 0;
  const scenes = markers.length >= 2
    ? buildManualTimingScenesFromMarkers(markers, importedScenes, { durationSec: finalDuration })
    : importedScenes;

  return {
    ...getDefaultManualTimingNodeData(),
    ...safeBase,
    project_kind: String(safeRaw.project_kind || safeRaw.projectKind || safeBase.project_kind || "clip"),
    format: String(safeRaw.format || safeBase.format || "9:16"),
    audio: {
      ...baseAudio,
      duration_sec: finalDuration || baseAudio.duration_sec || 0,
      duration_ms: Math.round((finalDuration || baseAudio.duration_sec || 0) * 1000),
    },
    timing_status: "draft",
    markers,
    scenes,
    selectedSceneId: scenes[0]?.scene_id || "",
    updatedAt: Date.now(),
  };
}

export function buildManualTimingWarnings(project = {}) {
  const audio = normalizeManualTimingAudio(project.audio);
  const scenes = Array.isArray(project.scenes) ? project.scenes : [];
  const warnings = [];
  const duration = Number(audio.duration_sec || 0);

  if (!scenes.length) warnings.push("Нет сегментов разметки.");
  if (scenes.length) {
    if (Math.abs(Number(scenes[0]?.start_sec || 0)) > 0.001) warnings.push("Первая сцена не начинается с 0.000 сек.");
    if (duration > 0 && Math.abs(Number(scenes[scenes.length - 1]?.end_sec || 0) - duration) > 0.05) warnings.push("Последняя сцена не заканчивается на длительности аудио.");
  }

  scenes.forEach((scene, idx) => {
    const start = Number(scene.start_sec || 0);
    const end = Number(scene.end_sec || 0);
    const dur = Number(scene.duration_sec || (end - start));
    if (idx > 0) {
      const prevEnd = Number(scenes[idx - 1]?.end_sec || 0);
      if (Math.abs(start - prevEnd) > 0.01) warnings.push(`${scene.scene_id}: есть разрыв или наложение с предыдущей сценой.`);
    }
    if (dur < 1.0) warnings.push(`${scene.scene_id}: длительность меньше 1 сек.`);
    if (dur > 9.0) warnings.push(`${scene.scene_id}: длительность больше 9 сек — проверь, не склеены ли разные фразы.`);
    if (scene.route === "ia2v" && !scene.contains_vocal) warnings.push(`${scene.scene_id}: ia2v стоит на участке без вокала.`);
    if (["intro", "instrumental"].includes(String(scene.section || "")) && scene.route === "ia2v") warnings.push(`${scene.scene_id}: секция “${sectionLabelRu(scene.section)}”, но выбран route=ia2v.`);
  });

  return warnings;
}

export function buildManualTimingExportJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const scenes = (Array.isArray(safeProject.scenes) ? safeProject.scenes : []).map((scene, idx) => ({
    scene_id: String(scene?.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene?.index || idx + 1),
    start_sec: roundTimingSec(scene?.start_sec),
    end_sec: roundTimingSec(scene?.end_sec),
    duration_sec: roundTimingSec(scene?.duration_sec || (Number(scene?.end_sec || 0) - Number(scene?.start_sec || 0))),
    section: normalizeManualTimingSection(scene?.section),
    route: normalizeManualTimingRoute(scene?.route),
    contains_vocal: Boolean(scene?.contains_vocal),
    contains_vocal_assumption: Boolean(scene?.contains_vocal_assumption ?? scene?.contains_vocal),
    contains_instrumental_assumption: Boolean(scene?.contains_instrumental_assumption ?? !scene?.contains_vocal),
    use_sound_suggestion: Boolean(scene?.use_sound_suggestion),
    energy: normalizeManualTimingEnergy(scene?.energy),
    quality: String(scene?.quality || (safeProject.timing_status === "confirmed" ? "manual_confirmed" : "manual_draft")),
    boundary_reason: String(scene?.boundary_reason || "manual_marker"),
    transition_out: String(scene?.transition_out || "manual_cut"),
    story_time: String(scene?.story_time || ""),
    scene_type: String(scene?.scene_type || ""),
    drama_hint: String(scene?.drama_hint || ""),
    short_note: String(scene?.short_note || ""),
    scene_goal_ru: String(scene?.scene_goal_ru || ""),
    photo_prompt_hint_ru: String(scene?.photo_prompt_hint_ru || ""),
    prompt_hint_ru: String(scene?.prompt_hint_ru || scene?.photo_prompt_hint_ru || ""),
    story_position_ru: String(scene?.story_position_ru || scene?.story_time || ""),
    user_note_ru: String(scene?.user_note_ru || ""),
    video_prompt: "",
    negative_prompt: "",
    sound_prompt: "",
  }));

  return {
    mode: "manual_clip_board",
    project_kind: String(safeProject.project_kind || "clip"),
    format: String(safeProject.format || "9:16"),
    split_type: safeProject.timing_status === "confirmed" ? "manual_timing_confirmed" : "manual_timing_draft",
    audio_duration_sec: Number(audio.duration_sec || 0),
    global_hint: safeProject.timing_status === "confirmed" ? "Manual timing confirmed by user" : "Manual timing draft",
    scenes,
  };
}
