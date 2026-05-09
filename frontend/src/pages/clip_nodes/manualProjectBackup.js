export const MANUAL_TIMING_ACTIVE_PROJECT_KEY = "manual_timing_active_project";
export const MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY = "manual_timing_active_project_id";
export const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY = "manual_clip_board_active_project";
export const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY = "manual_clip_board_active_project_id";

export function getManualProjectAccountScopeId() {
  try {
    const userId = String(localStorage.getItem("ps:lastUserId") || "").trim();
    if (userId) return `user_${encodeURIComponent(userId)}`;
    const email = String(localStorage.getItem("ps:lastEmail") || "").trim().toLowerCase();
    if (email) return `email_${encodeURIComponent(email)}`;
  } catch {}
  return "guest";
}

export function getAccountScopedStorageKey(baseKey = "") {
  return `${String(baseKey || "manual_project")}:account:${getManualProjectAccountScopeId()}`;
}

export function getManualTimingProjectStorageKey(nodeId = "") {
  const safeId = String(nodeId || "default").trim() || "default";
  return `manual_timing_project:${safeId}`;
}

export function getManualClipBoardProjectStorageKey(nodeId = "") {
  const safeId = String(nodeId || "default").trim() || "default";
  return `manual_clip_board_project:${safeId}`;
}

export function readManualProjectJsonStorage(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function compactArray(value) {
  return Array.isArray(value) ? value : [];
}

function normalizeAudioMetadata(project = {}) {
  const audio = project?.audio && typeof project.audio === "object" ? project.audio : {};
  const meta = project?.audio_metadata && typeof project.audio_metadata === "object" ? project.audio_metadata : {};
  return {
    ...meta,
    ...audio,
    url: String(audio.url || meta.url || ""),
    filename: String(audio.filename || meta.filename || ""),
    duration_sec: Number(audio.duration_sec ?? meta.duration_sec ?? project.audio_duration_sec ?? 0) || 0,
    duration_ms: Number(audio.duration_ms ?? meta.duration_ms ?? 0) || Math.round((Number(audio.duration_sec ?? meta.duration_sec ?? project.audio_duration_sec ?? 0) || 0) * 1000),
  };
}

export function buildManualProjectBackupJson(project = {}, { source = "manual_project" } = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audioMetadata = normalizeAudioMetadata(safeProject);
  const updatedAt = safeProject.updatedAt || safeProject.updated_at || Date.now();
  return {
    backup_type: "photostudio_manual_project_backup",
    backup_schema_version: 1,
    createdAt: new Date().toISOString(),
    source,
    account_scope: getManualProjectAccountScopeId(),
    nodeId: String(safeProject.nodeId || ""),
    project_mode: String(safeProject.project_mode || safeProject.projectMode || ""),
    project_kind: String(safeProject.project_kind || safeProject.projectKind || ""),
    format: String(safeProject.format || "9:16"),
    audio: audioMetadata,
    audio_metadata: audioMetadata,
    audio_duration_sec: Number(audioMetadata.duration_sec || 0),
    audio_mode: String(safeProject.audio_mode || ""),
    audio_phrases: compactArray(safeProject.audio_phrases),
    markers: compactArray(safeProject.markers),
    story_blocks: compactArray(safeProject.story_blocks),
    song_blocks: compactArray(safeProject.song_blocks),
    topic_blocks: compactArray(safeProject.topic_blocks),
    speakers: compactArray(safeProject.speakers),
    scenes: compactArray(safeProject.scenes),
    selectedSceneId: String(safeProject.selectedSceneId || ""),
    timing_status: String(safeProject.timing_status || ""),
    updatedAt,
  };
}

export function unwrapManualProjectBackupJson(raw = {}) {
  const safeRaw = raw && typeof raw === "object" ? raw : {};
  if (safeRaw.backup_type !== "photostudio_manual_project_backup") return safeRaw;
  const audio = normalizeAudioMetadata(safeRaw);
  return {
    ...safeRaw,
    audio,
    audio_metadata: audio,
    project_mode: String(safeRaw.project_mode || ""),
    project_kind: String(safeRaw.project_kind || ""),
    format: String(safeRaw.format || "9:16"),
    audio_duration_sec: Number(safeRaw.audio_duration_sec || audio.duration_sec || 0),
    audio_phrases: compactArray(safeRaw.audio_phrases),
    markers: compactArray(safeRaw.markers),
    story_blocks: compactArray(safeRaw.story_blocks),
    song_blocks: compactArray(safeRaw.song_blocks),
    topic_blocks: compactArray(safeRaw.topic_blocks),
    speakers: compactArray(safeRaw.speakers),
    scenes: compactArray(safeRaw.scenes),
    selectedSceneId: String(safeRaw.selectedSceneId || ""),
    updatedAt: safeRaw.updatedAt || Date.now(),
  };
}

export function hasMeaningfulManualProject(project = {}) {
  if (!project || typeof project !== "object") return false;
  return Boolean(
    String(project.project_mode || project.projectMode || "").trim()
    || String(project.project_kind || project.projectKind || "").trim()
    || String(project.audio?.url || project.audio_metadata?.url || "").trim()
    || compactArray(project.audio_phrases).length
    || compactArray(project.markers).length
    || compactArray(project.scenes).length
  );
}

function readActiveProjectPair(activeKey, activeIdKey, projectKeyFactory) {
  const scopedActiveKey = getAccountScopedStorageKey(activeKey);
  const scopedActiveIdKey = getAccountScopedStorageKey(activeIdKey);
  const scopedActive = readManualProjectJsonStorage(scopedActiveKey);
  if (hasMeaningfulManualProject(scopedActive)) return scopedActive;
  try {
    const scopedNodeId = String(localStorage.getItem(scopedActiveIdKey) || "").trim();
    if (scopedNodeId) {
      const scopedNodeProject = readManualProjectJsonStorage(getAccountScopedStorageKey(projectKeyFactory(scopedNodeId)));
      if (hasMeaningfulManualProject(scopedNodeProject)) return scopedNodeProject;
    }
  } catch {}
  const legacyActive = readManualProjectJsonStorage(activeKey);
  if (hasMeaningfulManualProject(legacyActive)) return legacyActive;
  try {
    const legacyNodeId = String(localStorage.getItem(activeIdKey) || "").trim();
    if (legacyNodeId) {
      const legacyNodeProject = readManualProjectJsonStorage(projectKeyFactory(legacyNodeId));
      if (hasMeaningfulManualProject(legacyNodeProject)) return legacyNodeProject;
    }
  } catch {}
  return null;
}

export function readAnyActiveManualProject() {
  return readActiveProjectPair(
    MANUAL_TIMING_ACTIVE_PROJECT_KEY,
    MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY,
    getManualTimingProjectStorageKey
  ) || readActiveProjectPair(
    MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY,
    MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY,
    getManualClipBoardProjectStorageKey
  );
}
