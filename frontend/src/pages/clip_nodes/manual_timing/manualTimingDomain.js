export function getDefaultManualTimingNodeData() {
  return {
    mode: "manual_timing",
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
  const filename = String(audio.filename || audio.fileName || audio.name || audio.meta?.filename || "").trim();
  const durationSecRaw = Number(audio.duration_sec ?? audio.durationSec ?? audio.duration ?? audio.meta?.duration_sec ?? audio.meta?.duration ?? 0);
  const durationMsRaw = Number(audio.duration_ms ?? audio.durationMs ?? audio.meta?.duration_ms ?? 0);
  const duration_sec = Number.isFinite(durationSecRaw) ? Number(durationSecRaw.toFixed(3)) : 0;
  const duration_ms = Number.isFinite(durationMsRaw) && durationMsRaw > 0 ? Math.round(durationMsRaw) : Math.round(duration_sec * 1000);
  return { url, filename, duration_sec, duration_ms };
}

export function buildManualTimingExportJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  return {
    mode: "manual_clip_board",
    project_kind: String(safeProject.project_kind || "clip"),
    format: String(safeProject.format || "9:16"),
    split_type: "manual_timing_draft",
    audio_duration_sec: Number(audio.duration_sec || 0),
    global_hint: "Manual timing draft",
    scenes: Array.isArray(safeProject.scenes) ? safeProject.scenes : [],
  };
}
