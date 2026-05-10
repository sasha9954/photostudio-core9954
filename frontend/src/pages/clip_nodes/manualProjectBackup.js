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

export function canUseLegacyManualProjectStorage() {
  return getManualProjectAccountScopeId() === "guest";
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


export function getManualClipBoardMaterialStats(project = {}) {
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  return {
    scenes: scenes.length,
    images: scenes.filter((scene) => String(scene?.image_url || scene?.start_image_url || scene?.end_image_url || "").trim()).length,
    prompts: scenes.filter((scene) => String(scene?.video_prompt || scene?.negative_prompt || scene?.sound_prompt || "").trim()).length,
    videos: scenes.filter((scene) => String(scene?.video_url || scene?.videoUrl || "").trim()).length,
    materialTotal: scenes.filter((scene) => (
      String(scene?.image_url || scene?.start_image_url || scene?.end_image_url || "").trim()
      || String(scene?.video_prompt || scene?.negative_prompt || scene?.sound_prompt || "").trim()
      || String(scene?.video_url || scene?.videoUrl || "").trim()
    )).length,
  };
}

export function scoreManualClipBoardProject(project = {}) {
  const stats = getManualClipBoardMaterialStats(project);
  const updatedAt = Number(project?.updatedAt || project?.updated_at || 0);
  return {
    stats,
    updatedAt,
    score:
      stats.videos * 100000
      + stats.images * 10000
      + stats.prompts * 1000
      + stats.materialTotal * 100
      + stats.scenes
      + Math.min(updatedAt / 1000000000000, 10),
  };
}

function pickBestManualClipBoardProject(candidates = []) {
  const valid = candidates.filter(hasMeaningfulManualProject);
  if (!valid.length) return null;

  return valid
    .map((project) => ({
      project,
      scoreData: scoreManualClipBoardProject(project),
    }))
    .sort((a, b) => {
      if (b.scoreData.stats.materialTotal !== a.scoreData.stats.materialTotal) {
        return b.scoreData.stats.materialTotal - a.scoreData.stats.materialTotal;
      }
      if (b.scoreData.stats.videos !== a.scoreData.stats.videos) {
        return b.scoreData.stats.videos - a.scoreData.stats.videos;
      }
      if (b.scoreData.stats.images !== a.scoreData.stats.images) {
        return b.scoreData.stats.images - a.scoreData.stats.images;
      }
      if (b.scoreData.stats.prompts !== a.scoreData.stats.prompts) {
        return b.scoreData.stats.prompts - a.scoreData.stats.prompts;
      }
      return b.scoreData.updatedAt - a.scoreData.updatedAt;
    })[0].project;
}

export function hasMeaningfulManualProject(project = {}) {
  if (!project || typeof project !== "object") return false;
  return Boolean(
    String(project.project_mode || project.projectMode || "").trim()
    || String(project.project_kind || project.projectKind || "").trim()
    || String(project.audio?.url || project.audio_metadata?.url || "").trim()
    || compactArray(project.audio_phrases).length
    || compactArray(project.markers).length
    || compactArray(project.story_blocks).length
    || compactArray(project.scenes).length
  );
}

export function readLegacyManualTimingProject() {
  const legacyActive = readManualProjectJsonStorage(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
  if (hasMeaningfulManualProject(legacyActive)) return legacyActive;
  try {
    const legacyNodeId = String(localStorage.getItem(MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY) || "").trim();
    if (legacyNodeId) {
      const legacyNodeProject = readManualProjectJsonStorage(getManualTimingProjectStorageKey(legacyNodeId));
      if (hasMeaningfulManualProject(legacyNodeProject)) return legacyNodeProject;
    }
  } catch {}
  return null;
}

export function readLegacyManualClipBoardProject() {
  const legacyActive = readManualProjectJsonStorage(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
  if (hasMeaningfulManualProject(legacyActive)) return legacyActive;
  try {
    const legacyNodeId = String(localStorage.getItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY) || "").trim();
    if (legacyNodeId) {
      const legacyNodeProject = readManualProjectJsonStorage(getManualClipBoardProjectStorageKey(legacyNodeId));
      if (hasMeaningfulManualProject(legacyNodeProject)) return legacyNodeProject;
    }
  } catch {}
  return null;
}

export function readAnyLegacyManualProject() {
  return readLegacyManualTimingProject() || readLegacyManualClipBoardProject();
}

export function migrateLegacyManualProjectToCurrentAccount(project, { target = "timing" } = {}) {
  const safeProject = unwrapManualProjectBackupJson(project);
  if (!hasMeaningfulManualProject(safeProject)) return false;
  const isDirectorTarget = String(target || "").trim() === "director";
  const activeKey = isDirectorTarget ? MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY : MANUAL_TIMING_ACTIVE_PROJECT_KEY;
  const activeIdKey = isDirectorTarget ? MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY : MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY;
  const projectKeyFactory = isDirectorTarget ? getManualClipBoardProjectStorageKey : getManualTimingProjectStorageKey;

  try {
    const serialized = JSON.stringify(safeProject);
    localStorage.setItem(getAccountScopedStorageKey(activeKey), serialized);
    const nodeId = String(safeProject?.nodeId || "").trim();
    if (nodeId) {
      localStorage.setItem(getAccountScopedStorageKey(activeIdKey), nodeId);
      localStorage.setItem(getAccountScopedStorageKey(projectKeyFactory(nodeId)), serialized);
    }
    return true;
  } catch {
    return false;
  }
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
  if (canUseLegacyManualProjectStorage()) {
    const legacyActive = readManualProjectJsonStorage(activeKey);
    if (hasMeaningfulManualProject(legacyActive)) return legacyActive;
    try {
      const legacyNodeId = String(localStorage.getItem(activeIdKey) || "").trim();
      if (legacyNodeId) {
        const legacyNodeProject = readManualProjectJsonStorage(projectKeyFactory(legacyNodeId));
        if (hasMeaningfulManualProject(legacyNodeProject)) return legacyNodeProject;
      }
    } catch {}
  }
  return null;
}


function writeManualClipBoardProjectStorage(project = {}) {
  const serialized = JSON.stringify(project);
  localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY), serialized);

  const nodeId = String(project?.nodeId || "").trim();
  if (nodeId) {
    localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY), nodeId);
    localStorage.setItem(getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(nodeId)), serialized);
  }
}

function readScopedManualClipBoardProjectCandidates() {
  const candidates = [];
  const accountSuffix = `:account:${getManualProjectAccountScopeId()}`;

  try {
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (!key || !key.includes("manual_clip_board_project:") || !key.includes(accountSuffix)) continue;
      candidates.push(readManualProjectJsonStorage(key));
    }
  } catch {}

  return candidates;
}

export function readActiveManualClipBoardProject() {
  const candidates = [];
  const scopedActiveKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
  const scopedActiveIdKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY);
  const scopedActive = readManualProjectJsonStorage(scopedActiveKey);
  candidates.push(scopedActive);

  try {
    const scopedNodeId = String(localStorage.getItem(scopedActiveIdKey) || "").trim();
    if (scopedNodeId) {
      candidates.push(readManualProjectJsonStorage(getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(scopedNodeId))));
    }
  } catch {}

  if (canUseLegacyManualProjectStorage()) {
    candidates.push(readManualProjectJsonStorage(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY));
    try {
      const legacyNodeId = String(localStorage.getItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY) || "").trim();
      if (legacyNodeId) {
        candidates.push(readManualProjectJsonStorage(getManualClipBoardProjectStorageKey(legacyNodeId)));
      }
    } catch {}
  }

  candidates.push(...readScopedManualClipBoardProjectCandidates());

  const best = pickBestManualClipBoardProject(candidates);
  if (!best) return null;

  console.debug("[manual board read best]", {
    candidates: candidates.map((project) => ({
      nodeId: project?.nodeId,
      updatedAt: project?.updatedAt,
      stats: getManualClipBoardMaterialStats(project),
    })),
    picked: {
      nodeId: best?.nodeId,
      updatedAt: best?.updatedAt,
      stats: getManualClipBoardMaterialStats(best),
    },
  });

  const bestStats = getManualClipBoardMaterialStats(best);
  const scopedActiveStats = getManualClipBoardMaterialStats(scopedActive);
  if (best !== scopedActive && bestStats.materialTotal > scopedActiveStats.materialTotal) {
    try {
      writeManualClipBoardProjectStorage({
        ...best,
        updatedAt: Date.now(),
        lastPersistReason: "repair_active_manual_clip_board_pointer",
      });
    } catch {}
  }

  return best;
}

export function persistManualClipBoardProject(project = {}, options = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const forceReplace = Boolean(options?.forceReplace);
  const reason = String(options?.reason || safeProject?.lastPersistReason || "");
  const existing = readActiveManualClipBoardProject();
  const existingStats = getManualClipBoardMaterialStats(existing);
  const incomingStats = getManualClipBoardMaterialStats(safeProject);

  if (
    !forceReplace
    && hasMeaningfulManualProject(existing)
    && existingStats.materialTotal > 0
    && incomingStats.materialTotal === 0
    && incomingStats.scenes >= existingStats.scenes * 0.8
  ) {
    console.warn("[manual board persist blocked] refusing to overwrite rich board with empty snapshot", { reason, existingStats, incomingStats });
    return false;
  }

  try {
    const serialized = JSON.stringify(safeProject);
    localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY), serialized);
    const nodeId = String(safeProject?.nodeId || "").trim();
    if (nodeId) {
      localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY), nodeId);
      localStorage.setItem(getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(nodeId)), serialized);
    }
    if (canUseLegacyManualProjectStorage()) {
      localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY, serialized);
      if (nodeId) {
        localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY, nodeId);
        localStorage.setItem(getManualClipBoardProjectStorageKey(nodeId), serialized);
      }
    }
    console.debug("[manual board persist]", {
      reason,
      forceReplace,
      stats: getManualClipBoardMaterialStats(safeProject),
      updatedAt: safeProject.updatedAt,
    });
    return true;
  } catch {
    return false;
  }
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
