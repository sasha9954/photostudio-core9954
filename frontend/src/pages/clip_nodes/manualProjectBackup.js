export const MANUAL_TIMING_ACTIVE_PROJECT_KEY = "manual_timing_active_project";
export const MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY = "manual_timing_active_project_id";
export const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY = "manual_clip_board_active_project";
export const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY = "manual_clip_board_active_project_id";
export const MANUAL_CLIP_BOARD_CANONICAL_PROJECT_KEY = "manual_clip_board_canonical_project";


const MANUAL_STORAGE_MAX_STRING_LENGTH = 200000;
const MANUAL_STORAGE_DANGEROUS_KEY_RE = /(raw|debug|blob|base64|binary|provider|response|payload|(^|[_-])file($|[_-]))/i;
const MANUAL_STORAGE_SCENE_ALLOWED_KEYS = new Set([
  "scene_id",
  "index",
  "start_sec",
  "end_sec",
  "duration_sec",
  "speech_start_sec",
  "speech_end_sec",
  "source_phrase_ids",
  "story_block_id",
  "story_block_title_ru",
  "story_block_position_ru",
  "translated_text_ru",
  "meaning_hint_ru",
  "scene_goal_ru",
  "scene_role_in_block_ru",
  "block_progress_ru",
  "photo_prompt_hint_ru",
  "prompt_hint_ru",
  "source_image_prompt_en",
  "source_image_prompt_ru",
  "i2v_prompt_en",
  "i2v_negative_prompt_en",
  "video_prompt",
  "negative_prompt",
  "sound_prompt",
  "image_url",
  "imageUrl",
  "image_preview_url",
  "video_url",
  "videoUrl",
  "audio_slice_url",
  "audio_slice_duration_sec",
  "status",
  "video_job_id",
  "video_error",
  "route",
  "renderMode",
  "lipSync",
  "format",
  "selected",
  "isSelected",
  "manual",
  "isManual",
  "manualSelected",
  "manualRoute",
  "manualPrompt",
]);
const MANUAL_STORAGE_TOP_LEVEL_DROP_KEYS = new Set([
  "rawScenarioResponse",
  "pendingRawResponse",
  "rawResponse",
  "debugRaw",
  "debugPayload",
  "promptDebugRaw",
  "largePayload",
]);

let lastManualClipBoardStorageError = null;

export function getLastManualClipBoardStorageError() {
  return lastManualClipBoardStorageError;
}

function rememberManualClipBoardStorageError(error = null) {
  lastManualClipBoardStorageError = error;
}

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
  const storageProject = sanitizeManualClipBoardProjectForStorage(safeProject);
  const serialized = JSON.stringify(storageProject);
  localStorage.setItem(getManualClipBoardCanonicalStorageKey(), serialized);
  return true;
}

function compactArray(value) {
  return Array.isArray(value) ? value : [];
}


export function getLocalStorageApproxBytes() {
  try {
    let total = 0;
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index) || "";
      const value = localStorage.getItem(key) || "";
      total += key.length + value.length;
    }
    return total * 2;
  } catch {
    return 0;
  }
}

function isQuotaExceededError(err) {
  return Boolean(
    err
    && (
      err.name === "QuotaExceededError"
      || err.name === "NS_ERROR_DOM_QUOTA_REACHED"
      || err.code === 22
      || err.code === 1014
    )
  );
}

function isDataUrlString(value = "") {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized.startsWith("data:image")
    || normalized.startsWith("data:video")
    || normalized.startsWith("data:audio")
    || normalized.startsWith("data:");
}

function isFileLikeManualStorageObject(value) {
  if (!value || typeof value !== "object") return false;
  const tag = Object.prototype.toString.call(value);
  if (tag === "[object Blob]" || tag === "[object File]") return true;
  if (typeof Blob !== "undefined" && value instanceof Blob) return true;
  if (typeof File !== "undefined" && value instanceof File) return true;
  return Boolean(
    typeof value.arrayBuffer === "function"
    && typeof value.stream === "function"
    && typeof value.size === "number"
    && typeof value.type === "string"
  );
}

export function stripLargeManualStorageValue(value, path = "") {
  if (value === undefined || typeof value === "function" || typeof value === "symbol") return undefined;
  if (value === null || typeof value === "number" || typeof value === "boolean") return value;
  if (typeof value === "string") {
    if (isDataUrlString(value)) return "";
    if (value.length > MANUAL_STORAGE_MAX_STRING_LENGTH) return "";
    return value;
  }
  if (isFileLikeManualStorageObject(value)) return undefined;
  if (Array.isArray(value)) {
    return value
      .map((item, index) => stripLargeManualStorageValue(item, `${path}[${index}]`))
      .filter((item) => item !== undefined);
  }
  if (typeof value !== "object") return undefined;

  const compact = {};
  Object.entries(value).forEach(([key, child]) => {
    const safeKey = String(key || "");
    if (MANUAL_STORAGE_DANGEROUS_KEY_RE.test(safeKey)) return;
    const stripped = stripLargeManualStorageValue(child, path ? `${path}.${safeKey}` : safeKey);
    if (stripped !== undefined) compact[safeKey] = stripped;
  });
  return compact;
}

function sanitizeManualClipBoardSceneForStorage(scene = {}, index = 0) {
  if (!scene || typeof scene !== "object") return scene;
  const compact = {};
  Object.entries(scene).forEach(([key, value]) => {
    const safeKey = String(key || "");
    if (MANUAL_STORAGE_DANGEROUS_KEY_RE.test(safeKey) && !MANUAL_STORAGE_SCENE_ALLOWED_KEYS.has(safeKey)) return;
    const stripped = stripLargeManualStorageValue(value, `scenes[${index}].${safeKey}`);
    if (stripped === undefined) return;
    if (MANUAL_STORAGE_SCENE_ALLOWED_KEYS.has(safeKey)) {
      compact[safeKey] = stripped;
      return;
    }
    if (stripped === null || typeof stripped === "string" || typeof stripped === "number" || typeof stripped === "boolean") {
      compact[safeKey] = stripped;
    }
  });
  return compact;
}

export function sanitizeManualClipBoardProjectForStorage(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const compact = {};

  Object.entries(safeProject).forEach(([key, value]) => {
    const safeKey = String(key || "");
    if (MANUAL_STORAGE_TOP_LEVEL_DROP_KEYS.has(safeKey)) return;
    if (safeKey === "payloadPreview") {
      if (typeof value === "string" && value.length <= 20000 && !isDataUrlString(value)) compact[safeKey] = value;
      return;
    }
    if (MANUAL_STORAGE_DANGEROUS_KEY_RE.test(safeKey)) return;

    if (safeKey === "scenes") {
      compact.scenes = Array.isArray(value)
        ? value.map((scene, index) => sanitizeManualClipBoardSceneForStorage(scene, index))
        : [];
      return;
    }

    if (["story_blocks", "audio_phrases", "markers", "song_blocks", "topic_blocks", "speakers"].includes(safeKey)) {
      compact[safeKey] = Array.isArray(value) ? stripLargeManualStorageValue(value, safeKey) : [];
      return;
    }

    const stripped = stripLargeManualStorageValue(value, safeKey);
    if (stripped !== undefined) compact[safeKey] = stripped;
  });

  return compact;
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
  const hasNumberMaterial = (value) => Number.isFinite(Number(value)) && Number(value) > 0;
  const hasTruthyMaterial = (value) => value === true || hasText(value);
  const sceneHasImage = (scene = {}) => Boolean(
    hasText(scene?.image_url)
    || hasText(scene?.image_preview_url)
    || hasText(scene?.start_image_url)
    || hasText(scene?.end_image_url)
    || hasText(scene?.start_image_preview_url)
    || hasText(scene?.end_image_preview_url)
  );
  const sceneHasVideo = (scene = {}) => Boolean(hasText(scene?.video_url) || hasText(scene?.videoUrl));
  const sceneHasVideoPrompt = (scene = {}) => Boolean(
    hasText(scene?.video_prompt)
    || hasText(scene?.negative_prompt)
    || hasText(scene?.sound_prompt)
    || hasText(scene?.negative_audio_prompt)
    || hasText(scene?.speech_text)
    || hasText(scene?.voice_profile)
    || hasText(scene?.delivery_style)
    || hasText(scene?.ambient_sound_prompt)
  );
  const sceneHasPhotoPrompt = (scene = {}) => Boolean(
    hasText(scene?.source_image_prompt_en)
    || hasText(scene?.source_image_prompt_ru)
    || hasText(scene?.source_image_negative_prompt_en)
    || hasText(scene?.i2v_prompt_en)
    || hasText(scene?.i2v_negative_prompt_en)
    || hasText(scene?.composition_ru)
    || hasText(scene?.camera_angle_ru)
    || hasText(scene?.subject_lock_ru)
    || hasText(scene?.background_lock_ru)
    || hasText(scene?.scene_global_context_ru)
    || hasText(scene?.continuity_anchor_ru)
    || hasText(scene?.storyboard_frame_role_ru)
  );
  const sceneHasPrompt = (scene = {}) => sceneHasVideoPrompt(scene) || sceneHasPhotoPrompt(scene);
  const sceneHasJob = (scene = {}) => hasText(scene?.video_job_id);
  const sceneHasReadyStatus = (scene = {}) => readyStatuses.has(String(scene?.status || "").trim());
  const sceneHasAudioSlice = (scene = {}) => Boolean(
    hasText(scene?.audio_slice_url)
    || scene?.audio_extracted === true
    || hasNumberMaterial(scene?.audio_slice_duration_sec)
  );
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
  const sceneHasPayloadPreview = (scene = {}) => hasTruthyMaterial(scene?.video_request_payload_preview);
  const sceneHasVideoAudioFlag = (scene = {}) => scene?.video_has_audio === true;
  const customRouteCount = scenes.filter((scene) => {
    const route = String(scene?.route || "").trim().toLowerCase();
    return route && route !== "i2v";
  }).length;
  const imageCount = scenes.filter(sceneHasImage).length;
  const videoCount = scenes.filter(sceneHasVideo).length;
  const videoPromptCount = scenes.filter(sceneHasVideoPrompt).length;
  const photoPromptCount = scenes.filter(sceneHasPhotoPrompt).length;
  const promptCount = scenes.filter(sceneHasPrompt).length;
  const audioSliceCount = scenes.filter(sceneHasAudioSlice).length;
  const jobCount = scenes.filter(sceneHasJob).length;
  const readyStatusCount = scenes.filter(sceneHasReadyStatus).length;
  const generatedAudioCount = scenes.filter(sceneHasGeneratedAudio).length;
  const payloadPreviewCount = scenes.filter(sceneHasPayloadPreview).length;
  const videoAudioFlagCount = scenes.filter(sceneHasVideoAudioFlag).length;
  const materialTotal = scenes.filter((scene) => (
    sceneHasImage(scene)
    || sceneHasVideo(scene)
    || sceneHasPrompt(scene)
    || sceneHasAudioSlice(scene)
    || sceneHasJob(scene)
    || sceneHasReadyStatus(scene)
    || sceneHasGeneratedAudio(scene)
    || sceneHasPayloadPreview(scene)
    || sceneHasVideoAudioFlag(scene)
  )).length;
  const materialScore =
    videoCount * 100000
    + imageCount * 10000
    + jobCount * 6000
    + readyStatusCount * 4000
    + promptCount * 1200
    + photoPromptCount * 900
    + videoPromptCount * 900
    + audioSliceCount * 800
    + generatedAudioCount * 700
    + payloadPreviewCount * 600
    + videoAudioFlagCount * 550
    + materialTotal * 100;

  return {
    scenes: scenes.length,
    images: imageCount,
    imageCount,
    prompts: promptCount,
    promptCount,
    photoPrompts: photoPromptCount,
    videoPrompts: videoPromptCount,
    videos: videoCount,
    videoCount,
    audioSlices: audioSliceCount,
    videoJobs: jobCount,
    readyStatuses: readyStatusCount,
    generatedAudio: generatedAudioCount,
    payloadPreviews: payloadPreviewCount,
    videoHasAudio: videoAudioFlagCount,
    customRoutes: customRouteCount,
    materialTotal,
    materialScore,
  };
}

export function shouldSkipManualBoardPersistToProtectMaterials(nextProject, existingProject, options = {}) {
  if (options?.forceReplace || options?.explicitReset || options?.allowMaterialLoss) return false;
  const nextStats = getManualClipBoardMaterialStats(nextProject);
  const existingStats = getManualClipBoardMaterialStats(existingProject);
  if (existingStats.materialTotal <= 0) return false;
  return nextStats.materialScore < existingStats.materialScore
    || nextStats.materialTotal < existingStats.materialTotal
    || nextStats.videoCount < existingStats.videoCount
    || nextStats.imageCount < existingStats.imageCount
    || nextStats.promptCount < existingStats.promptCount
    || nextStats.audioSlices < existingStats.audioSlices
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
      stats.materialScore
      + stats.customRoutes * 50
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
      if (b.scoreData.score !== a.scoreData.score) {
        return b.scoreData.score - a.scoreData.score;
      }
      if (b.scoreData.stats.materialScore !== a.scoreData.stats.materialScore) {
        return b.scoreData.stats.materialScore - a.scoreData.stats.materialScore;
      }
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
      if (b.scoreData.stats.audioSlices !== a.scoreData.stats.audioSlices) {
        return b.scoreData.stats.audioSlices - a.scoreData.stats.audioSlices;
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
  const storageProject = sanitizeManualClipBoardProjectForStorage(project);
  const serialized = JSON.stringify(storageProject);
  localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY), serialized);

  const nodeId = String(storageProject?.nodeId || "").trim();
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


export function getManualProjectOwnerId(project = {}) {
  return String(project?.sourceNodeId || project?.nodeId || "").trim();
}

export function manualProjectBelongsToNode(project = {}, nodeId = "") {
  const safeNodeId = String(nodeId || "").trim();
  if (!safeNodeId) return true;
  return getManualProjectOwnerId(project) === safeNodeId;
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

  const bestScore = scoreManualClipBoardProject(best);
  const canonicalScore = scoreManualClipBoardProject(canonicalProject);
  if (bestScore.score > canonicalScore.score) {
    try {
      writeCanonicalManualClipBoardProject({
        ...best,
        updatedAt: Date.now(),
        lastPersistReason: "repair_canonical_manual_clip_board",
      });
    } catch {}
  }

  const scopedActiveScore = scoreManualClipBoardProject(scopedActive);
  if (best !== scopedActive && bestScore.score > scopedActiveScore.score) {
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


export function readActiveManualClipBoardProjectForNode(nodeId = "") {
  const safeNodeId = String(nodeId || "").trim();
  const nodeProject = safeNodeId ? readManualClipBoardProjectForNode(safeNodeId) : null;
  const activeProject = readActiveManualClipBoardProject();
  const activeMatching = manualProjectBelongsToNode(activeProject, safeNodeId) ? activeProject : null;
  return pickBestManualClipBoardProject([nodeProject, activeMatching]);
}

export function clearManualClipBoardProjectForNode(nodeId = "", options = {}) {
  const safeNodeId = String(nodeId || "").trim();
  const clearActive = options?.clearActive !== false;
  const clearCanonical = options?.clearCanonical !== false;

  const matchesNode = (project = {}) => {
    if (!safeNodeId) return true;
    const projectNodeId = String(project?.nodeId || project?.sourceNodeId || "").trim();
    return !projectNodeId || projectNodeId === safeNodeId;
  };

  try {
    if (safeNodeId) {
      localStorage.removeItem(getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(safeNodeId)));
      if (canUseLegacyManualProjectStorage()) localStorage.removeItem(getManualClipBoardProjectStorageKey(safeNodeId));
    }

    if (clearActive) {
      const scopedActiveKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
      const scopedActiveIdKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY);
      const scopedActive = readManualProjectJsonStorage(scopedActiveKey);
      const scopedActiveId = String(localStorage.getItem(scopedActiveIdKey) || "").trim();
      if (matchesNode(scopedActive) || (safeNodeId && scopedActiveId === safeNodeId)) {
        localStorage.removeItem(scopedActiveKey);
        localStorage.removeItem(scopedActiveIdKey);
      }

      if (canUseLegacyManualProjectStorage()) {
        const legacyActive = readManualProjectJsonStorage(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
        const legacyActiveId = String(localStorage.getItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY) || "").trim();
        if (matchesNode(legacyActive) || (safeNodeId && legacyActiveId === safeNodeId)) {
          localStorage.removeItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
          localStorage.removeItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY);
        }
      }
    }

    if (clearCanonical) {
      const canonicalKey = getManualClipBoardCanonicalStorageKey();
      const canonicalProject = readManualProjectJsonStorage(canonicalKey);
      if (matchesNode(canonicalProject)) localStorage.removeItem(canonicalKey);
    }

    return true;
  } catch {
    return false;
  }
}

export function persistManualClipBoardProject(project = {}, options = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const forceReplace = Boolean(options?.forceReplace);
  const explicitReset = Boolean(options?.explicitReset);
  const reason = String(options?.reason || safeProject?.lastPersistReason || "");
  const nodeId = String(safeProject?.nodeId || safeProject?.sourceNodeId || "").trim();
  const nodeScopedExisting = nodeId ? readManualClipBoardProjectForNode(nodeId) : null;
  const activeExistingRaw = readActiveManualClipBoardProject();
  const activeExistingRawOwner = getManualProjectOwnerId(activeExistingRaw);
  const activeWasIgnoredBecauseDifferentOwner = Boolean(
    nodeId
    && hasMeaningfulManualProject(activeExistingRaw)
    && !manualProjectBelongsToNode(activeExistingRaw, nodeId)
  );
  const activeExisting = nodeId && manualProjectBelongsToNode(activeExistingRaw, nodeId)
    ? activeExistingRaw
    : null;
  const existing = nodeId
    ? pickBestManualClipBoardProject([nodeScopedExisting, activeExisting])
    : pickBestManualClipBoardProject([nodeScopedExisting, activeExistingRaw]);
  const existingStats = getManualClipBoardMaterialStats(existing);
  const nextStats = getManualClipBoardMaterialStats(safeProject);

  if (shouldSkipManualBoardPersistToProtectMaterials(safeProject, existing, options)) {
    console.warn("[MANUAL BOARD PERSIST PROTECT] skipped overwrite", {
      nodeId,
      activeExistingRawOwner,
      activeWasIgnoredBecauseDifferentOwner,
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
      activeExistingRawOwner,
      activeWasIgnoredBecauseDifferentOwner,
      existingStats,
      nextStats,
      reason: reason || "incoming_project_empty_material_snapshot",
    });
    return false;
  }

  let serialized = "";
  const storageProject = sanitizeManualClipBoardProjectForStorage(safeProject);
  const storageStats = getManualClipBoardMaterialStats(storageProject);
  try {
    serialized = JSON.stringify(storageProject);
    writeCanonicalManualClipBoardProject(storageProject);
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
    rememberManualClipBoardStorageError(null);
    console.debug("[manual board persist]", {
      target: "canonical+active+node",
      canonicalKey: getManualClipBoardCanonicalStorageKey(),
      reason,
      forceReplace,
      explicitReset,
      nodeId,
      sourceNodeId: storageProject.sourceNodeId,
      incomingStats: nextStats,
      storageStats,
      storageSerializedKb: Math.round(serialized.length / 1024),
      selectedSceneId: storageProject.selectedSceneId,
      updatedAt: storageProject.updatedAt,
    });
    return true;
  } catch (err) {
    const errorInfo = {
      reason: isQuotaExceededError(err) ? "quota_exceeded" : "write_failed",
      errorName: err?.name,
      errorMessage: err?.message,
      errorCode: err?.code,
      serializedLength: serialized.length,
      serializedKb: Math.round(serialized.length / 1024),
      safeProjectScenesLength: Array.isArray(safeProject.scenes) ? safeProject.scenes.length : 0,
      storageScenesLength: Array.isArray(storageProject.scenes) ? storageProject.scenes.length : 0,
      incomingStats: nextStats,
      storageStats,
      localStorageApproxBytes: getLocalStorageApproxBytes(),
      nodeId,
    };
    rememberManualClipBoardStorageError(errorInfo);
    console.error("[manual board persist] write failed", errorInfo);
    return false;
  }
}


function cleanupOldManualClipBoardStorageForQuota(currentNodeId = "") {
  const safeNodeId = String(currentNodeId || "").trim();
  const accountScopeId = getManualProjectAccountScopeId();
  const accountSuffix = `:account:${accountScopeId}`;
  const currentScopedNodeKey = safeNodeId
    ? getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(safeNodeId))
    : "";
  const currentLegacyNodeKey = safeNodeId ? getManualClipBoardProjectStorageKey(safeNodeId) : "";
  const activeKeys = new Set([
    getManualClipBoardCanonicalStorageKey(),
    getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY),
    getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY),
    currentScopedNodeKey,
  ].filter(Boolean));
  const legacyActiveProject = readManualProjectJsonStorage(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
  const legacyActiveOwner = getManualProjectOwnerId(legacyActiveProject);
  const legacyActiveId = (() => {
    try {
      return String(localStorage.getItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY) || "").trim();
    } catch {
      return "";
    }
  })();
  const keepLegacyActive = Boolean(safeNodeId && (legacyActiveOwner === safeNodeId || legacyActiveId === safeNodeId));
  const legacyActiveKeys = new Set([
    keepLegacyActive ? MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY : "",
    keepLegacyActive ? MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY : "",
    currentLegacyNodeKey,
  ].filter(Boolean));
  const removedKeys = [];

  const removeKey = (key) => {
    if (!key || activeKeys.has(key) || key === currentLegacyNodeKey) return;
    try {
      localStorage.removeItem(key);
      removedKeys.push(key);
    } catch {}
  };

  try {
    const keys = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key) keys.push(key);
    }

    keys
      .filter((key) => key.includes("manual_clip_board") && !key.includes(":account:"))
      .filter((key) => !legacyActiveKeys.has(key))
      .forEach(removeKey);

    keys
      .filter((key) => key.includes("manual_clip_board_project:") && key.includes(accountSuffix))
      .filter((key) => key !== currentScopedNodeKey)
      .sort((a, b) => {
        const aProject = readManualProjectJsonStorage(a);
        const bProject = readManualProjectJsonStorage(b);
        return Number(aProject?.updatedAt || aProject?.updated_at || 0) - Number(bProject?.updatedAt || bProject?.updated_at || 0);
      })
      .slice(0, 20)
      .forEach(removeKey);

    if (removedKeys.length) {
      console.warn("[manual board quota cleanup]", {
        currentNodeId: safeNodeId,
        accountScopeId,
        removedKeysCount: removedKeys.length,
        removedKeys,
        localStorageApproxBytes: getLocalStorageApproxBytes(),
      });
    }
  } catch (err) {
    console.warn("[manual board quota cleanup] failed", {
      currentNodeId: safeNodeId,
      accountScopeId,
      errorName: err?.name,
      errorMessage: err?.message,
    });
  }

  return removedKeys;
}


export function forceWriteManualClipBoardProjectForNode(project = {}, options = {}) {
  const incomingProject = project && typeof project === "object" ? project : {};
  const nodeId = String(incomingProject.nodeId || incomingProject.sourceNodeId || "").trim();
  const reason = String(
    options?.reason
    || incomingProject.lastPersistReason
    || "force_write_manual_clip_board_for_node"
  );

  if (!nodeId) {
    console.error("[manual board force write node-scoped] missing nodeId", {
      reason,
      projectOwner: getManualProjectOwnerId(incomingProject),
      stats: getManualClipBoardMaterialStats(incomingProject),
    });
    return false;
  }

  const safeProject = {
    ...incomingProject,
    nodeId,
    sourceNodeId: nodeId,
    updatedAt: Date.now(),
    lastPersistReason: reason,
  };
  const storageProject = sanitizeManualClipBoardProjectForStorage(safeProject);
  const canonicalKey = getManualClipBoardCanonicalStorageKey();
  const activeKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
  const activeIdKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY);
  const nodeScopedKey = getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(nodeId));
  const accountScopeId = getManualProjectAccountScopeId();
  const stats = getManualClipBoardMaterialStats(safeProject);
  const storageStats = getManualClipBoardMaterialStats(storageProject);
  const localStorageApproxBytesBeforeWrite = getLocalStorageApproxBytes();
  let serialized = "";

  const writeAndVerify = () => {
    localStorage.setItem(canonicalKey, serialized);
    localStorage.setItem(activeKey, serialized);
    localStorage.setItem(activeIdKey, nodeId);
    localStorage.setItem(nodeScopedKey, serialized);

    if (canUseLegacyManualProjectStorage()) {
      localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY, serialized);
      localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY, nodeId);
      localStorage.setItem(getManualClipBoardProjectStorageKey(nodeId), serialized);
    }

    const readback = readManualClipBoardProjectForNode(nodeId);
    const readbackOwner = getManualProjectOwnerId(readback);
    const readbackExists = hasMeaningfulManualProject(readback);
    const wrote = Boolean(readbackExists && readbackOwner === nodeId);

    console.info("[manual board force write node-scoped]", {
      nodeId,
      reason,
      wrote,
      readbackExists,
      readbackOwner,
      serializedKb: Math.round(serialized.length / 1024),
      stats,
      storageStats,
      selectedSceneId: storageProject.selectedSceneId,
    });

    if (!wrote) {
      console.error("[manual board force write node-scoped] verify failed", {
        activeKey,
        activeIdKey,
        canonicalKey,
        nodeScopedKey,
        accountScopeId,
        nodeId,
        readbackRawLength: String(localStorage.getItem(nodeScopedKey) || "").length,
        readbackOwner,
        readbackExists,
        serializedLength: serialized.length,
        serializedKb: Math.round(serialized.length / 1024),
        safeProjectScenesLength: Array.isArray(safeProject.scenes) ? safeProject.scenes.length : 0,
        storageScenesLength: Array.isArray(storageProject.scenes) ? storageProject.scenes.length : 0,
        stats,
        storageStats,
      });
    }

    return wrote;
  };

  try {
    serialized = JSON.stringify(storageProject);
    const wrote = writeAndVerify();
    if (wrote) rememberManualClipBoardStorageError(null);
    return wrote;
  } catch (err) {
    let retryWrote = false;
    let removedKeys = [];
    const quotaExceeded = isQuotaExceededError(err);

    if (quotaExceeded && serialized) {
      removedKeys = cleanupOldManualClipBoardStorageForQuota(nodeId);
      try {
        retryWrote = writeAndVerify();
      } catch (retryErr) {
        err = retryErr;
      }
    }

    if (retryWrote) {
      rememberManualClipBoardStorageError(null);
      return true;
    }

    const errorInfo = {
      reason: quotaExceeded ? "quota_exceeded" : "write_failed",
      errorName: err?.name,
      errorMessage: err?.message,
      errorCode: err?.code,
      serializedLength: serialized.length,
      serializedKb: Math.round(serialized.length / 1024),
      safeProjectScenesLength: Array.isArray(safeProject.scenes) ? safeProject.scenes.length : 0,
      storageScenesLength: Array.isArray(storageProject.scenes) ? storageProject.scenes.length : 0,
      stats,
      storageStats,
      localStorageApproxBytes: localStorageApproxBytesBeforeWrite,
      localStorageApproxBytesAfterCleanup: getLocalStorageApproxBytes(),
      failedKey: nodeScopedKey,
      activeKey,
      activeIdKey,
      canonicalKey,
      nodeScopedKey,
      accountScopeId,
      nodeId,
      quotaCleanupRemovedKeysCount: removedKeys.length,
      quotaCleanupRemovedKeys: removedKeys,
      readbackRawLength: (() => {
        try {
          return String(localStorage.getItem(nodeScopedKey) || "").length;
        } catch {
          return 0;
        }
      })(),
    };
    rememberManualClipBoardStorageError(errorInfo);
    console.error("[manual board force write node-scoped] write failed", errorInfo);
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
