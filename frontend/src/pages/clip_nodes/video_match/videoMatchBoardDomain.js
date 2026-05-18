export const VIDEO_MATCH_BOARD_ACTIVE_PROJECT_KEY = "VIDEO_MATCH_BOARD_ACTIVE_PROJECT_KEY";
export const VIDEO_MATCH_BOARD_ACTIVE_PROJECT_ID_KEY = "VIDEO_MATCH_BOARD_ACTIVE_PROJECT_ID_KEY";
export const VIDEO_MATCH_BOARD_SCHEMA_V1 = "video_match_board_v1";

export function getVideoMatchBoardNodeStorageKey(nodeId = "") {
  return `video_match_board:node:${String(nodeId || "default").trim() || "default"}`;
}

export function getVideoMatchBoardLastGoodStorageKey(nodeId = "") {
  return `video_match_board:last_good:${String(nodeId || "default").trim() || "default"}`;
}

export function getVideoMatchBoardEmergencyStorageKey(nodeId = "") {
  return `video_match_board:emergency:${String(nodeId || "default").trim() || "default"}`;
}

export function safeReadVideoMatchJson(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function safeWriteJson(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}

export function normalizeVideoMatchTimingContext(raw = {}) {
  const source = raw && typeof raw === "object" ? raw : {};
  const audio = source.audio && typeof source.audio === "object" ? source.audio : {};
  const sourceAudioUrl = String(source.sourceAudioUrl || source.audioUrl || audio.url || "").trim();
  const audioDurationRaw = Number(source.audioDurationSec ?? source.audio_duration_sec ?? audio.duration_sec ?? audio.durationSec ?? 0);
  const timingScenes = Array.isArray(source.timingScenes)
    ? source.timingScenes
    : (Array.isArray(source.scenes) ? source.scenes : []);
  const segments = Array.isArray(source.segments)
    ? source.segments
    : (Array.isArray(source.audioSegments) ? source.audioSegments : []);
  return {
    sourceAudioUrl,
    audioDurationSec: Number.isFinite(audioDurationRaw) && audioDurationRaw > 0 ? Number(audioDurationRaw.toFixed(3)) : 0,
    timingScenes,
    segments,
    podcastEditManifest: source.podcastEditManifest || source.podcast_edit_manifest || null,
    composerEditManifest: source.composerEditManifest || source.composer_edit_manifest || null,
    sourceNodeId: String(source.sourceNodeId || source.nodeId || "").trim(),
    updatedAt: source.updatedAt || source.updated_at || Date.now(),
  };
}

export function buildVideoMatchTimingContextFromManualTimingNodeData(data = {}, nodeId = "") {
  const source = data && typeof data === "object" ? data : {};
  const audio = source.audio && typeof source.audio === "object" ? source.audio : {};
  const directorBoard = source.director_board && typeof source.director_board === "object" ? source.director_board : {};
  return normalizeVideoMatchTimingContext({
    sourceAudioUrl: audio.url || source.sourceAudioUrl || "",
    audioDurationSec: audio.duration_sec ?? audio.durationSec ?? source.audioDurationSec ?? 0,
    timingScenes: Array.isArray(source.scenes) ? source.scenes : [],
    segments: Array.isArray(source.segments) ? source.segments : (Array.isArray(source.markers) ? source.markers : []),
    podcastEditManifest: source.podcastEditManifest || source.podcast_edit_manifest || directorBoard.podcastEditManifest || directorBoard.podcast_edit_manifest || null,
    composerEditManifest: source.composerEditManifest || source.composer_edit_manifest || directorBoard.composerEditManifest || directorBoard.composer_edit_manifest || null,
    sourceNodeId: nodeId,
    updatedAt: source.updatedAt || Date.now(),
  });
}

export function getDefaultVideoMatchBoardProject(nodeId = "", extra = {}) {
  const safeNodeId = String(nodeId || "default").trim() || "default";
  const now = Date.now();
  return {
    projectId: `video_match_board_${safeNodeId}`,
    schema: VIDEO_MATCH_BOARD_SCHEMA_V1,
    nodeId: safeNodeId,
    sourceNodeId: safeNodeId,
    sourceVideo: { filename: "", duration_sec: 0 },
    sourceVideoUrl: "",
    timingContext: normalizeVideoMatchTimingContext(extra.timingContext || {}),
    videoBlocks: [],
    selectedBlockId: "",
    jsonInput: "",
    jsonError: "",
    createdAt: now,
    updatedAt: now,
    ...extra,
  };
}

export function normalizeVideoBlock(match = {}, sourceVideoUrl = "") {
  const id = String(match.id || `match_${Date.now()}`).trim();
  return {
    id,
    audioSceneId: String(match.audio_scene_id || match.audioSceneId || "").trim(),
    targetStartSec: Number(match.target_t0 ?? match.targetStartSec ?? 0) || 0,
    targetEndSec: Number(match.target_t1 ?? match.targetEndSec ?? 0) || 0,
    sourceVideoStartSec: Number(match.video_t0 ?? match.sourceVideoStartSec ?? 0) || 0,
    sourceVideoEndSec: Number(match.video_t1 ?? match.sourceVideoEndSec ?? 0) || 0,
    sourceVideoUrl: String(match.sourceVideoUrl || sourceVideoUrl || "").trim(),
    matchReason: String(match.match_reason || match.matchReason || "").trim(),
    confidence: Number.isFinite(Number(match.confidence)) ? Number(match.confidence) : null,
  };
}

export function parseVideoMatchBoardJson(jsonText = "", sourceVideoUrl = "") {
  let parsed;
  try {
    parsed = JSON.parse(String(jsonText || ""));
  } catch (error) {
    return { ok: false, error: `Невалидный JSON: ${String(error?.message || error)}` };
  }
  if (!parsed || typeof parsed !== "object") return { ok: false, error: "JSON должен быть объектом." };
  if (parsed.schema !== VIDEO_MATCH_BOARD_SCHEMA_V1) return { ok: false, error: `schema должен быть ${VIDEO_MATCH_BOARD_SCHEMA_V1}.` };
  if (!Array.isArray(parsed.matches)) return { ok: false, error: "matches должен быть массивом." };
  const blocks = parsed.matches.map((match, index) => normalizeVideoBlock({ id: match?.id || `match_${String(index + 1).padStart(3, "0")}`, ...(match || {}) }, sourceVideoUrl));
  return {
    ok: true,
    sourceVideo: parsed.source_video && typeof parsed.source_video === "object" ? parsed.source_video : {},
    videoBlocks: blocks,
  };
}

export function readVideoMatchBoardProjectForNode(nodeId = "") {
  const safeNodeId = String(nodeId || "").trim();
  const nodeProject = safeReadVideoMatchJson(getVideoMatchBoardNodeStorageKey(safeNodeId));
  if (nodeProject) return nodeProject;
  const activeProject = safeReadVideoMatchJson(VIDEO_MATCH_BOARD_ACTIVE_PROJECT_KEY);
  if (activeProject && (!safeNodeId || String(activeProject.nodeId || "") === safeNodeId)) return activeProject;
  return null;
}

export function persistVideoMatchBoardProject(project = {}, options = {}) {
  const safeProject = {
    ...getDefaultVideoMatchBoardProject(project?.nodeId || project?.sourceNodeId || "default"),
    ...(project && typeof project === "object" ? project : {}),
    updatedAt: Date.now(),
  };
  const nodeId = String(safeProject.nodeId || safeProject.sourceNodeId || "default").trim() || "default";
  safeProject.nodeId = nodeId;
  safeProject.sourceNodeId = String(safeProject.sourceNodeId || nodeId);
  safeProject.timingContext = normalizeVideoMatchTimingContext(safeProject.timingContext || {});
  safeProject.videoBlocks = Array.isArray(safeProject.videoBlocks) ? safeProject.videoBlocks.map((block) => normalizeVideoBlock(block, safeProject.sourceVideoUrl)) : [];
  safeWriteJson(getVideoMatchBoardNodeStorageKey(nodeId), safeProject);
  safeWriteJson(VIDEO_MATCH_BOARD_ACTIVE_PROJECT_KEY, safeProject);
  try { localStorage.setItem(VIDEO_MATCH_BOARD_ACTIVE_PROJECT_ID_KEY, String(safeProject.projectId || nodeId)); } catch {}
  if (options?.lastGood !== false) safeWriteJson(getVideoMatchBoardLastGoodStorageKey(nodeId), safeProject);
  if (options?.emergency) safeWriteJson(getVideoMatchBoardEmergencyStorageKey(nodeId), safeProject);
  return safeProject;
}
