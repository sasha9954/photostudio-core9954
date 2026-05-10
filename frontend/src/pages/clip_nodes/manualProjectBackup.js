export const MANUAL_TIMING_ACTIVE_PROJECT_KEY = "manual_timing_active_project";
export const MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY = "manual_timing_active_project_id";
export const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY = "manual_clip_board_active_project";
export const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY = "manual_clip_board_active_project_id";
export const MANUAL_CLIP_BOARD_CANONICAL_PROJECT_KEY = "manual_clip_board_canonical_project";

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

export function getManualClipBoardCanonicalStorageKey() {
  return getAccountScopedStorageKey(MANUAL_CLIP_BOARD_CANONICAL_PROJECT_KEY);
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

export function readCanonicalManualClipBoardProject() {
  return readManualProjectJsonStorage(getManualClipBoardCanonicalStorageKey());
}

export function writeCanonicalManualClipBoardProject(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const serialized = JSON.stringify(safeProject);
  localStorage.setItem(getManualClipBoardCanonicalStorageKey(), serialized);
  return true;
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
  const readyStatuses = new Set(["video_ready", "video_running", "video_queued"]);
  const hasText = (value) => String(value || "").trim().length > 0;
  const hasTruthyMaterial = (value) => value === true || hasText(value);
  const sceneHasImage = (scene = {}) => Boolean(
    hasText(scene?.image_url)
    || hasText(scene?.image_preview_url)
    || hasText(scene?.start_image_url)
    || hasText(scene?.end_image_url)
  );
  const sceneHasVideo = (scene = {}) => Boolean(hasText(scene?.video_url) || hasText(scene?.videoUrl));
  const sceneHasPrompt = (scene = {}) => Boolean(
    hasText(scene?.video_prompt)
    || hasText(scene?.negative_prompt)
    || hasText(scene?.sound_prompt)
    || hasText(scene?.negative_audio_prompt)
  );
  const sceneHasJob = (scene = {}) => hasText(scene?.video_job_id);
  const sceneHasReadyStatus = (scene = {}) => readyStatuses.has(String(scene?.status || "").trim());
  const sceneHasGeneratedAudio = (scene = {}) => {
    const hasCustomGain = Object.prototype.hasOwnProperty.call(scene || {}, "generated_audio_gain_db")
      && hasText(scene?.generated_audio_gain_db)
      && Number(scene?.generated_audio_gain_db) !== -6;
    return Boolean(
      hasText(scene?.generated_audio_policy)
      || hasTruthyMaterial(scene?.keep_generated_audio)
      || hasCustomGain
    );
  };
  const sceneHasPayloadPreview = (scene = {}) => hasText(scene?.video_request_payload_preview);
  const sceneHasVideoAudioFlag = (scene = {}) => scene?.video_has_audio === true;
  const customRouteCount = scenes.filter((scene) => {
    const route = String(scene?.route || "").trim().toLowerCase();
    return route && route !== "i2v";
  }).length;
  const imageCount = scenes.filter(sceneHasImage).length;
  const videoCount = scenes.filter(sceneHasVideo).length;
  const promptCount = scenes.filter(sceneHasPrompt).length;
  const jobCount = scenes.filter(sceneHasJob).length;
  const readyStatusCount = scenes.filter(sceneHasReadyStatus).length;
  const generatedAudioCount = scenes.filter(sceneHasGeneratedAudio).length;
  const payloadPreviewCount = scenes.filter(sceneHasPayloadPreview).length;
  const videoAudioFlagCount = scenes.filter(sceneHasVideoAudioFlag).length;
  const materialTotal = scenes.filter((scene) => (
    sceneHasImage(scene)
    || sceneHasVideo(scene)
    || sceneHasPrompt(scene)
    || sceneHasJob(scene)
    || sceneHasReadyStatus(scene)
    || sceneHasGeneratedAudio(scene)
    || sceneHasPayloadPreview(scene)
    || sceneHasVideoAudioFlag(scene)
    || (String(scene?.route || "").trim().toLowerCase() && String(scene?.route || "").trim().toLowerCase() !== "i2v")
  )).length;

  return {
    scenes: scenes.length,
    images: imageCount,
    imageCount,
    prompts: promptCount,
    promptCount,
    videos: videoCount,
    videoCount,
    videoJobs: jobCount,
    readyStatuses: readyStatusCount,
    generatedAudio: generatedAudioCount,
    payloadPreviews: payloadPreviewCount,
    videoHasAudio: videoAudioFlagCount,
    customRoutes: customRouteCount,
    materialTotal,
  };
}

export function shouldSkipManualBoardPersistToProtectMaterials(nextProject, existingProject, options = {}) {
  if (options?.forceReplace || options?.explicitReset || options?.allowMaterialLoss) return false;
  const nextStats = getManualClipBoardMaterialStats(nextProject);
  const existingStats = getManualClipBoardMaterialStats(existingProject);
  if (existingStats.materialTotal <= 0) return false;
  return nextStats.materialTotal < existingStats.materialTotal
    || nextStats.videoCount < existingStats.videoCount
    || nextStats.imageCount < existingStats.imageCount
    || nextStats.promptCount < existingStats.promptCount
    || nextStats.videoJobs < existingStats.videoJobs
    || nextStats.readyStatuses < existingStats.readyStatuses
    || nextStats.generatedAudio < existingStats.generatedAudio
    || nextStats.payloadPreviews < existingStats.payloadPreviews
    || nextStats.videoHasAudio < existingStats.videoHasAudio;
}

export function scoreManualClipBoardProject(project = {}) {
  const stats = getManualClipBoardMaterialStats(project);
  const updatedAt = Number(project?.updatedAt || project?.updated_at || 0);
  return {
    stats,
    updatedAt,
    score:
      stats.videoCount * 100000
      + stats.imageCount * 10000
      + stats.videoJobs * 6000
      + stats.readyStatuses * 4000
      + stats.promptCount * 1000
      + stats.generatedAudio * 700
      + stats.payloadPreviews * 600
      + stats.videoHasAudio * 550
      + stats.customRoutes * 500
      + stats.materialTotal * 100
      + stats.scenes
      + Math.min(updatedAt / 1000000000000, 10),
  };
}

export function pickBestManualClipBoardProject(candidates = []) {
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
      if (b.scoreData.stats.videoCount !== a.scoreData.stats.videoCount) {
        return b.scoreData.stats.videoCount - a.scoreData.stats.videoCount;
      }
      if (b.scoreData.stats.imageCount !== a.scoreData.stats.imageCount) {
        return b.scoreData.stats.imageCount - a.scoreData.stats.imageCount;
      }
      if (b.scoreData.stats.videoJobs !== a.scoreData.stats.videoJobs) {
        return b.scoreData.stats.videoJobs - a.scoreData.stats.videoJobs;
      }
      if (b.scoreData.stats.readyStatuses !== a.scoreData.stats.readyStatuses) {
        return b.scoreData.stats.readyStatuses - a.scoreData.stats.readyStatuses;
      }
      if (b.scoreData.stats.promptCount !== a.scoreData.stats.promptCount) {
        return b.scoreData.stats.promptCount - a.scoreData.stats.promptCount;
      }
      if (b.scoreData.stats.customRoutes !== a.scoreData.stats.customRoutes) {
        return b.scoreData.stats.customRoutes - a.scoreData.stats.customRoutes;
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

export function readManualClipBoardProjectForNode(nodeId = "") {
  const safeNodeId = String(nodeId || "").trim();
  if (!safeNodeId) return null;
  const candidates = [
    readManualProjectJsonStorage(getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(safeNodeId))),
  ];
  if (canUseLegacyManualProjectStorage()) {
    candidates.push(readManualProjectJsonStorage(getManualClipBoardProjectStorageKey(safeNodeId)));
  }
  return pickBestManualClipBoardProject(candidates);
}

export function readActiveManualClipBoardProject() {
  const candidates = [];
  const canonicalProject = readCanonicalManualClipBoardProject();
  candidates.push(canonicalProject);
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
  const canonicalStats = getManualClipBoardMaterialStats(canonicalProject);
  if (bestStats.materialTotal > canonicalStats.materialTotal || bestStats.customRoutes > canonicalStats.customRoutes) {
    try {
      writeCanonicalManualClipBoardProject({
        ...best,
        updatedAt: Date.now(),
        lastPersistReason: "repair_canonical_manual_clip_board",
      });
    } catch {}
  }

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
  const explicitReset = Boolean(options?.explicitReset);
  const reason = String(options?.reason || safeProject?.lastPersistReason || "");
  const nodeId = String(safeProject?.nodeId || safeProject?.sourceNodeId || "").trim();
  const nodeScopedExisting = nodeId ? readManualClipBoardProjectForNode(nodeId) : null;
  const activeExisting = readActiveManualClipBoardProject();
  const existing = pickBestManualClipBoardProject([nodeScopedExisting, activeExisting]);
  const existingStats = getManualClipBoardMaterialStats(existing);
  const nextStats = getManualClipBoardMaterialStats(safeProject);

  if (shouldSkipManualBoardPersistToProtectMaterials(safeProject, existing, options)) {
    console.warn("[MANUAL BOARD PERSIST PROTECT] skipped overwrite", {
      nodeId,
      existingStats,
      nextStats,
      reason: reason || "incoming_project_has_fewer_materials",
    });
    return false;
  }

  if (
    !forceReplace
    && !explicitReset
    && hasMeaningfulManualProject(existing)
    && existingStats.materialTotal > 0
    && nextStats.materialTotal === 0
    && nextStats.scenes >= existingStats.scenes * 0.8
  ) {
    console.warn("[MANUAL BOARD PERSIST PROTECT] skipped overwrite", {
      nodeId,
      existingStats,
      nextStats,
      reason: reason || "incoming_project_empty_material_snapshot",
    });
    return false;
  }

  try {
    writeCanonicalManualClipBoardProject(safeProject);
    const serialized = JSON.stringify(safeProject);
    localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY), serialized);
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
      target: "canonical+active+node",
      canonicalKey: getManualClipBoardCanonicalStorageKey(),
      reason,
      forceReplace,
      explicitReset,
      stats: getManualClipBoardMaterialStats(safeProject),
      selectedSceneId: safeProject.selectedSceneId,
      updatedAt: safeProject.updatedAt,
    });
    return true;
  } catch {
    return false;
  }
}

export function debugManualClipBoardStorageSnapshot() {
  const rows = [];
  try {
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (!key || !key.includes("manual_clip_board")) continue;
      const project = readManualProjectJsonStorage(key);
      rows.push({
        key,
        nodeId: project?.nodeId,
        selectedSceneId: project?.selectedSceneId,
        updatedAt: project?.updatedAt,
        reason: project?.lastPersistReason,
        stats: getManualClipBoardMaterialStats(project),
      });
    }
  } catch {}
  console.table(rows.map((row) => ({
    key: row.key,
    scenes: row.stats.scenes,
    images: row.stats.images,
    prompts: row.stats.prompts,
    videos: row.stats.videos,
    customRoutes: row.stats.customRoutes,
    materialTotal: row.stats.materialTotal,
    updatedAt: row.updatedAt,
    reason: row.reason,
  })));
  return rows;
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
