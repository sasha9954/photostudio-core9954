import { fetchJson } from "../../services/api.js";

export const MANUAL_TIMING_ACTIVE_PROJECT_KEY = "manual_timing_active_project";
export const MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY = "manual_timing_active_project_id";
export const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY = "manual_clip_board_active_project";
export const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY = "manual_clip_board_active_project_id";
export const MANUAL_CLIP_BOARD_CANONICAL_PROJECT_KEY = "manual_clip_board_canonical_project";
export const MANUAL_CLIP_BOARD_OPEN_STATE_KEY = "photostudio_manual_director_open_state";
export const MANUAL_CLIP_BOARD_LAST_GOOD_SESSION_KEY = "photostudio_manual_director_last_good_board";
export const MANUAL_CLIP_BOARD_EMERGENCY_DOWNLOADED_ONCE_KEY = "manual_board_emergency_downloaded_once";

const MANUAL_STORAGE_MAX_STRING_LENGTH = 200000;
export const MANUAL_STORAGE_MAX_SAFE_LENGTH = 1200000;
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
  "negative_audio_prompt",
  "speech_text",
  "voice_profile",
  "voice_mode",
  "voice_language",
  "delivery_style",
  "image_url",
  "imageUrl",
  "image_preview_url",
  "imagePreviewUrl",
  "start_image_url",
  "startImageUrl",
  "end_image_url",
  "endImageUrl",
  "start_image_preview_url",
  "startImagePreviewUrl",
  "end_image_preview_url",
  "endImagePreviewUrl",
  "generated_image_url",
  "generatedImageUrl",
  "image_width",
  "imageWidth",
  "image_height",
  "imageHeight",
  "image_aspect_ratio",
  "imageAspectRatio",
  "image_aspect_label",
  "imageAspectLabel",
  "image_upload_status",
  "imageUploadStatus",
  "image_upload_error",
  "imageUploadError",
  "video_url",
  "videoUrl",
  "generated_video_url",
  "generatedVideoUrl",
  "final_video_url",
  "finalVideoUrl",
  "result_video_url",
  "resultVideoUrl",
  "video_asset_url",
  "videoAssetUrl",
  "video_preview_url",
  "videoPreviewUrl",
  "mmaudio_video_url",
  "mmaudioVideoUrl",
  "mmaudio_raw_video_url",
  "mmaudioRawVideoUrl",
  "mmaudio_source_video_url",
  "mmaudioSourceVideoUrl",
  "original_video_before_mmaudio_url",
  "originalVideoBeforeMMAudioUrl",
  "mmaudio_status",
  "mmaudioStatus",
  "mmaudio_job_id",
  "mmaudioJobId",
  "mmaudio_error",
  "mmaudioError",
  "mmaudio_gain_status",
  "mmaudioGainStatus",
  "mmaudio_prompt",
  "mmaudioPrompt",
  "mmaudio_negative_prompt",
  "mmaudioNegativePrompt",
  "mmaudio_gain_db",
  "generatedAudioPolicy",
  "generated_audio_policy",
  "generatedAudioGainDb",
  "generated_audio_gain_db",
  "video_has_audio",
  "videoHasAudio",
  "hasAudio",
  "deleted_media_revision",
  "deletedMediaRevision",
  "video_deleted_at",
  "videoDeletedAt",
  "photo_deleted_at",
  "photoDeletedAt",
  "audio_slice_url",
  "audio_slice_duration_sec",
  "status",
  "video_job_id",
  "video_error",
  "route",
  "renderMode",
  "lipSync",
  "format",
  "aspect_ratio",
  "format_locked",
  "image_width",
  "image_height",
  "image_aspect_ratio",
  "image_aspect_label",
  "selected",
  "isSelected",
  "manual",
  "isManual",
  "manualSelected",
  "manualRoute",
  "manualPrompt",
  "prompt",
  "image_prompt",
  "imagePrompt",
  "photo_prompt",
  "photoPrompt",
  "videoPrompt",
  "final_video_prompt",
  "finalVideoPrompt",
  "positive_prompt",
  "positivePrompt",
  "negativePrompt",
  "user_prompt",
  "userPrompt",
  "custom_prompt",
  "customPrompt",
  "selected_model",
  "selectedModel",
  "model",
  "model_id",
  "modelId",
  "image_model",
  "imageModel",
  "video_model",
  "videoModel",
  "generator_model",
  "generatorModel",
  "provider",
  "selectedImageModel",
  "selectedVideoModel",
  "selectedGenerator",
  "selectedProvider",
  "provider_model",
  "providerModel",
  "imageProvider",
  "videoProvider",
  "generationModel",
  "generation_model",
  "generation_settings",
  "generationSettings",
  "model_settings",
  "modelSettings",
]);

const MANUAL_STORAGE_SCENE_LIGHTWEIGHT_KEYS = new Set([
  "scene_id", "id", "index",
  "start_sec", "end_sec", "duration_sec", "speech_start_sec", "speech_end_sec", "timing",
  "story_block_id", "story_block_title_ru", "story_block_position_ru", "source_phrase_ids",
  "audio_slice_url", "audio_slice_duration_sec",
  "route", "renderMode", "lipSync", "format", "aspect_ratio",
  "source_image_prompt_en", "source_image_prompt_ru", "i2v_prompt_en", "i2v_negative_prompt_en",
  "prompt", "image_prompt", "imagePrompt", "photo_prompt", "photoPrompt",
  "video_prompt", "videoPrompt", "final_video_prompt", "finalVideoPrompt",
  "positive_prompt", "positivePrompt", "negative_prompt", "negativePrompt",
  "user_prompt", "userPrompt", "custom_prompt", "customPrompt",
  "selected_model", "selectedModel", "model", "model_id", "modelId",
  "image_model", "imageModel", "video_model", "videoModel",
  "generator_model", "generatorModel", "provider",
  "selectedImageModel", "selectedVideoModel", "selectedGenerator", "selectedProvider",
  "provider_model", "providerModel", "imageProvider", "videoProvider",
  "generationModel", "generation_model",
  "generation_settings", "generationSettings", "model_settings", "modelSettings",
  "sound_prompt", "negative_audio_prompt",
  "mmaudio_prompt", "mmaudioPrompt", "mmaudio_negative_prompt", "mmaudioNegativePrompt",
  "speech_text", "voice_profile", "voice_mode", "voice_language", "delivery_style",
  "image_url", "imageUrl", "start_image_url", "startImageUrl", "end_image_url", "endImageUrl",
  "video_url", "videoUrl", "result_video_url", "resultVideoUrl", "generated_video_url", "generatedVideoUrl",
  "final_video_url", "finalVideoUrl", "video_status", "videoStatus", "video_job_id", "videoJobId",
  "status", "updatedAt", "updated_at", "video_has_audio", "videoHasAudio", "hasAudio",
  "generated_audio_policy", "generatedAudioPolicy", "generated_audio_gain_db", "generatedAudioGainDb",
  "keep_generated_audio", "keepGeneratedAudio", "audio_slice_url", "audio_slice_duration_sec",
  "mmaudio_status", "mmaudio_job_id", "mmaudio_video_url", "mmaudioVideoUrl",
  "deleted_media_revision", "deletedMediaRevision", "video_deleted_at", "photo_deleted_at",
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


const MANUAL_BOARD_MEDIA_REF_KEY_RE = /(image|photo|preview|video|media|mmaudio|dataurl|data_url|asset|url)/i;
const MANUAL_BOARD_MEDIA_REF_VALUE_RE = /(data:image|data:video|blob:|\/static\/assets|\.(png|jpe?g|webp|gif|mp4|mov|webm)(?:[?#]|$))/i;
const MANUAL_BOARD_ALLOWED_AUDIO_PATH_RE = /(^|\.)(audio|audio_metadata)\.url$|(^|\.)audio_slice_url$/i;
const MANUAL_BOARD_AUDIO_VALUE_RE = /\.(mp3|wav|m4a|aac|ogg)(?:[?#]|$)/i;

function previewManualBoardMediaValue(value) {
  if (typeof value === "string") return value.length > 180 ? `${value.slice(0, 180)}…` : value;
  if (value === null || value === undefined) return "";
  try {
    const serialized = JSON.stringify(value);
    return serialized.length > 180 ? `${serialized.slice(0, 180)}…` : serialized;
  } catch {
    return String(value).slice(0, 180);
  }
}

function hasNonEmptyManualBoardMediaValue(value) {
  if (value === null || value === undefined) return false;
  if (typeof value === "string") return value.trim().length > 0;
  if (typeof value === "number") return Number.isFinite(value) && value !== 0;
  if (typeof value === "boolean") return value === true;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return Boolean(value);
}

function isAllowedManualBoardAudioMediaRef(path = "", key = "", value = "") {
  const safePath = String(path || "");
  const safeKey = String(key || "");
  const text = String(value || "").trim();
  if (!MANUAL_BOARD_ALLOWED_AUDIO_PATH_RE.test(safePath) && !/^(audio_slice_url|url)$/i.test(safeKey)) return false;
  if (/^(data:image|data:video|blob:)/i.test(text)) return false;
  if (MANUAL_BOARD_MEDIA_REF_VALUE_RE.test(text) && !MANUAL_BOARD_AUDIO_VALUE_RE.test(text)) return false;
  return true;
}

export function findManualBoardMediaRefs(project = {}, options = {}) {
  const maxDepth = Number(options?.maxDepth || 8) || 8;
  const refs = [];
  const seen = new WeakSet();
  const visit = (value, path = "project", depth = 0, key = "") => {
    if (depth > maxDepth) return;
    if (value && typeof value === "object") {
      if (seen.has(value)) return;
      seen.add(value);
      if (Array.isArray(value)) {
        value.forEach((item, index) => visit(item, `${path}[${index}]`, depth + 1, String(index)));
        return;
      }
      Object.entries(value).forEach(([childKey, childValue]) => {
        const childPath = path ? `${path}.${childKey}` : childKey;
        const keyMatches = MANUAL_BOARD_MEDIA_REF_KEY_RE.test(String(childKey || ""));
        if (keyMatches && hasNonEmptyManualBoardMediaValue(childValue)) {
          const childIsScalar = childValue === null || typeof childValue !== "object";
          const text = typeof childValue === "string" ? childValue.trim() : previewManualBoardMediaValue(childValue);
          const suspiciousString = typeof childValue === "string" && MANUAL_BOARD_MEDIA_REF_VALUE_RE.test(text);
          const includeAll = options?.includeAllMediaKeys === true;
          if ((childIsScalar || includeAll) && !isAllowedManualBoardAudioMediaRef(childPath, childKey, text) && (includeAll || suspiciousString || keyMatches)) {
            refs.push({
              path: childPath,
              key: childKey,
              valuePreview: previewManualBoardMediaValue(childValue),
            });
          }
        }
        visit(childValue, childPath, depth + 1, childKey);
      });
    }
  };
  visit(project, "project", 0, "project");
  return refs;
}

export function logManualBoardMediaRefs(label = "[MANUAL BOARD MEDIA REFS]", project = {}, extra = {}) {
  try {
    const refs = findManualBoardMediaRefs(project, extra?.findOptions || {});
    console.info(label, {
      sourceNodeId: String(extra?.sourceNodeId || project?.sourceNodeId || project?.nodeId || "").trim(),
      project_id: String(project?.project_id || project?.projectId || "").trim(),
      input_signature: String(project?.input_signature || project?.inputSignature || "").trim(),
      sceneCount: Array.isArray(project?.scenes) ? project.scenes.length : 0,
      refsCount: refs.length,
      refs: refs.slice(0, 20),
      ...extra,
    });
    return refs;
  } catch (error) {
    console.warn(`${label} failed`, { error: error?.message || String(error || "unknown") });
    return [];
  }
}


export function getManualBoardSceneStateDebugStats(project = {}) {
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];

  const sceneRows = scenes.map((scene) => ({
    scene_id: scene?.scene_id || scene?.id || "",

    prompt: scene?.prompt || "",
    image_prompt: scene?.image_prompt || "",
    imagePrompt: scene?.imagePrompt || "",
    photo_prompt: scene?.photo_prompt || "",
    photoPrompt: scene?.photoPrompt || "",
    source_image_prompt_en: scene?.source_image_prompt_en || "",
    source_image_prompt_ru: scene?.source_image_prompt_ru || "",
    video_prompt: scene?.video_prompt || "",
    i2v_prompt_en: scene?.i2v_prompt_en || "",
    videoPrompt: scene?.videoPrompt || "",
    final_video_prompt: scene?.final_video_prompt || "",
    finalVideoPrompt: scene?.finalVideoPrompt || "",
    positive_prompt: scene?.positive_prompt || "",
    positivePrompt: scene?.positivePrompt || "",
    negative_prompt: scene?.negative_prompt || "",
    i2v_negative_prompt_en: scene?.i2v_negative_prompt_en || "",
    negativePrompt: scene?.negativePrompt || "",
    user_prompt: scene?.user_prompt || "",
    userPrompt: scene?.userPrompt || "",
    custom_prompt: scene?.custom_prompt || "",
    customPrompt: scene?.customPrompt || "",
    sound_prompt: scene?.sound_prompt || "",
    negative_audio_prompt: scene?.negative_audio_prompt || "",
    speech_text: scene?.speech_text || "",
    voice_profile: scene?.voice_profile || "",
    voice_mode: scene?.voice_mode || "",
    voice_language: scene?.voice_language || "",
    delivery_style: scene?.delivery_style || "",
    mmaudio_prompt: scene?.mmaudio_prompt || "",
    mmaudioPrompt: scene?.mmaudioPrompt || "",
    mmaudio_negative_prompt: scene?.mmaudio_negative_prompt || "",
    mmaudioNegativePrompt: scene?.mmaudioNegativePrompt || "",

    selected_model: scene?.selected_model || "",
    selectedModel: scene?.selectedModel || "",
    model: scene?.model || "",
    model_id: scene?.model_id || "",
    modelId: scene?.modelId || "",
    image_model: scene?.image_model || "",
    imageModel: scene?.imageModel || "",
    video_model: scene?.video_model || "",
    videoModel: scene?.videoModel || "",
    generator_model: scene?.generator_model || "",
    generatorModel: scene?.generatorModel || "",
    selectedImageModel: scene?.selectedImageModel || "",
    selectedVideoModel: scene?.selectedVideoModel || "",
    selectedGenerator: scene?.selectedGenerator || "",
    selectedProvider: scene?.selectedProvider || "",
    provider: scene?.provider || "",
    provider_model: scene?.provider_model || "",
    providerModel: scene?.providerModel || "",
    imageProvider: scene?.imageProvider || "",
    videoProvider: scene?.videoProvider || "",
    generationModel: scene?.generationModel || "",
    generation_model: scene?.generation_model || "",
    route: scene?.route || "",
  }));

  const scenesWithPrompt = sceneRows.filter((row) => (
    row.prompt
    || row.image_prompt
    || row.imagePrompt
    || row.photo_prompt
    || row.photoPrompt
    || row.source_image_prompt_en
    || row.source_image_prompt_ru
    || row.video_prompt
    || row.i2v_prompt_en
    || row.videoPrompt
    || row.final_video_prompt
    || row.finalVideoPrompt
    || row.positive_prompt
    || row.positivePrompt
    || row.negative_prompt
    || row.negativePrompt
    || row.i2v_negative_prompt_en
    || row.sound_prompt
    || row.negative_audio_prompt
    || row.speech_text
    || row.voice_profile
    || row.voice_mode
    || row.voice_language
    || row.delivery_style
    || row.mmaudio_prompt
    || row.mmaudioPrompt
    || row.mmaudio_negative_prompt
    || row.mmaudioNegativePrompt
    || row.user_prompt
    || row.userPrompt
    || row.custom_prompt
    || row.customPrompt
  )).length;

  const scenesWithModel = sceneRows.filter((row) => (
    row.selected_model
    || row.selectedModel
    || row.model
    || row.model_id
    || row.modelId
    || row.image_model
    || row.imageModel
    || row.video_model
    || row.videoModel
    || row.generator_model
    || row.generatorModel
    || row.selectedImageModel
    || row.selectedVideoModel
    || row.selectedGenerator
    || row.selectedProvider
    || row.provider
    || row.provider_model
    || row.providerModel
    || row.imageProvider
    || row.videoProvider
    || row.generationModel
    || row.generation_model
  )).length;

  return {
    scenesCount: scenes.length,
    scenesWithPrompt,
    scenesWithModel,
    firstScene: sceneRows[0] || null,
    selectedScene: sceneRows.find((row) => row.scene_id === project?.selectedSceneId) || null,
    projectSelectedModel: {
      selected_model: project?.selected_model || "",
      selectedModel: project?.selectedModel || "",
      active_model: project?.active_model || "",
      activeModel: project?.activeModel || "",
      selectedImageModel: project?.selectedImageModel || "",
      selectedVideoModel: project?.selectedVideoModel || "",
      generationSettings: project?.generationSettings || null,
      modelSettings: project?.modelSettings || null,
    },
  };
}

export function getManualBoardMediaDebugStats(project = {}) {
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const sceneRows = scenes.map((scene) => ({
    scene_id: scene?.scene_id || scene?.id || "",
    image_url: scene?.image_url || "",
    imageUrl: scene?.imageUrl || "",
    generated_image_url: scene?.generated_image_url || "",
    generatedImageUrl: scene?.generatedImageUrl || "",
    start_image_url: scene?.start_image_url || "",
    startImageUrl: scene?.startImageUrl || "",
    displayImageUrl: scene?.displayImageUrl || "",
    video_url: scene?.video_url || "",
    videoUrl: scene?.videoUrl || "",
    generated_video_url: scene?.generated_video_url || "",
    generatedVideoUrl: scene?.generatedVideoUrl || "",
    final_video_url: scene?.final_video_url || "",
    finalVideoUrl: scene?.finalVideoUrl || "",
  }));

  const scenesWithImage = sceneRows.filter((row) => (
    row.image_url || row.imageUrl || row.generated_image_url || row.generatedImageUrl || row.start_image_url || row.startImageUrl || row.displayImageUrl
  )).length;

  const scenesWithVideo = sceneRows.filter((row) => (
    row.video_url || row.videoUrl || row.generated_video_url || row.generatedVideoUrl || row.final_video_url || row.finalVideoUrl
  )).length;

  return {
    scenesCount: scenes.length,
    scenesWithImage,
    scenesWithVideo,
    firstScene: sceneRows[0] || null,
    selectedScene: sceneRows.find((row) => row.scene_id === project?.selectedSceneId) || null,
  };
}

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

export async function saveManualClipBoardProjectDurable(project = {}, options = {}) {
  const safeProject = sanitizeManualClipBoardProjectForStorage(project && typeof project === "object" ? project : {});
  const nodeId = getManualProjectOwnerId(safeProject);
  if (!nodeId || !hasMeaningfulManualProject(safeProject)) return false;
  let existingDurableProject = null;
  try {
    existingDurableProject = await loadManualClipBoardProjectDurable(nodeId, {
      timeoutMs: 5000,
      silent: true,
    });
  } catch {}

  const incomingMediaStats = getManualBoardMediaDebugStats(safeProject);
  const existingMediaStats = getManualBoardMediaDebugStats(existingDurableProject);
  const incomingHasNoMedia =
    incomingMediaStats.scenesWithImage <= 0
    && incomingMediaStats.scenesWithVideo <= 0;
  const existingHasMedia =
    existingMediaStats.scenesWithImage > 0
    || existingMediaStats.scenesWithVideo > 0;
  const reason = String(options?.reason || safeProject?.lastPersistReason || "");
  const isInitialOpenSkeletonReason = reason === "manual_new_project_from_audio_split_open_embedded"
    || reason === "manual_new_project_from_audio_split";
  const sameStrictIdentityAsExisting = existingDurableProject
    ? manualBoardStrictIdentityMatches(safeProject, existingDurableProject)
    : true;

  if (existingHasMedia && incomingHasNoMedia && !sameStrictIdentityAsExisting) {
    console.info("[MANUAL BOARD NEW PROJECT ALLOWED TO OVERWRITE OLD DURABLE]", {
      nodeId,
      reason,
      incomingIdentity: getManualBoardStrictProjectIdentity(safeProject),
      existingIdentity: getManualBoardStrictProjectIdentity(existingDurableProject),
      incomingMediaStats,
      existingMediaStats,
    });
  }

  if (
    existingHasMedia
    && incomingHasNoMedia
    && sameStrictIdentityAsExisting
    && (!options?.allowEmptyDurableOverwrite || isInitialOpenSkeletonReason)
  ) {
    const incomingRefs = findManualBoardMediaRefs(safeProject, { maxDepth: 8 }).slice(0, 20);
    const existingRefs = findManualBoardMediaRefs(existingDurableProject, { maxDepth: 8 }).slice(0, 20);

    console.warn("[manual board durable save skipped: incoming would drop media]", {
      nodeId,
      reason,
      incomingMediaStats,
      existingMediaStats,
      incomingRefsCount: incomingRefs.length,
      incomingRefs,
      existingRefsCount: existingRefs.length,
      existingRefs,
    });
    return "skipped_preserve_media";
  }

  const durableProject = sanitizeManualClipBoardProjectForStorage({
    ...buildManualProjectBackupJson(safeProject, { source: "manual_board_backend_durable" }),
    source: String(safeProject.source || safeProject.ownerNodeType || "manual_board_backend_durable"),
    durable_source: String(options?.source || "manual_board_backend_durable"),
  });
  const mediaDebugStats = getManualBoardMediaDebugStats(safeProject);
  const sceneStateDebugStats = getManualBoardSceneStateDebugStats(safeProject);
  const mediaRefs = findManualBoardMediaRefs(safeProject, { maxDepth: 8 }).slice(0, 20);
  console.info("[manual board durable save payload media debug]", {
    nodeId,
    reason: reason || safeProject?.lastPersistReason || "",
    mediaDebugStats,
    sceneStateDebugStats,
    refsCount: mediaRefs.length,
    refs: mediaRefs,
  });
  const response = await fetchJson("/api/manual-board/save", {
    method: "POST",
    timeoutMs: Number(options?.timeoutMs || 12000),
    body: {
      accountKey: getManualProjectAccountScopeId(),
      nodeId,
      project: {
        ...durableProject,
        lastPersistReason: String(reason || durableProject.lastPersistReason || "manual_board_backend_durable_save"),
      },
    },
  });
  if (response?.ok) {
    console.info("[manual board durable save ok]", {
      nodeId,
      reason: reason || safeProject?.lastPersistReason || "manual_board_backend_durable_save",
      mediaDebugStats,
      sceneStateDebugStats,
      refsCount: mediaRefs.length,
      refs: mediaRefs,
    });
  }
  return Boolean(response?.ok);
}

export function queueManualClipBoardProjectDurableSave(project = {}, options = {}) {
  try {
    saveManualClipBoardProjectDurable(project, options).then((ok) => {
      if (ok === "skipped_preserve_media") {
        console.warn("[manual board durable persist] backend write skipped to preserve media", {
          nodeId: getManualProjectOwnerId(project),
          reason: options?.reason || project?.lastPersistReason || "manual_board_backend_durable_save",
          mediaDebugStats: getManualBoardMediaDebugStats(project),
          sceneStateDebugStats: getManualBoardSceneStateDebugStats(project),
        });
        return;
      }

      if (ok === true) {
        console.info("[manual board durable persist] backend write ok", {
          nodeId: getManualProjectOwnerId(project),
          reason: options?.reason || project?.lastPersistReason || "manual_board_backend_durable_save",
          stats: getManualClipBoardMaterialStats(project),
          mediaDebugStats: getManualBoardMediaDebugStats(project),
          sceneStateDebugStats: getManualBoardSceneStateDebugStats(project),
        });
      }
    }).catch((error) => {
      console.warn("[manual board durable persist] backend write failed; local fallback remains", {
        nodeId: getManualProjectOwnerId(project),
        reason: options?.reason || project?.lastPersistReason || "manual_board_backend_durable_save",
        errorName: error?.name,
        errorMessage: error?.message,
      });
    });
    return true;
  } catch {
    return false;
  }
}

let lastManualBoardDurableSaveSignature = "";
let pendingManualBoardDurableSaveSignature = "";

function buildManualBoardDurableSaveSignature(project = {}, reason = "") {
  return JSON.stringify({
    owner: getManualProjectOwnerId(project),
    project_id: project?.project_id || project?.projectId || "",
    updatedAt: project?.updatedAt || "",
    revision: project?.revision || 0,
    selectedSceneId: project?.selectedSceneId || "",
    reason,
    stats: getManualClipBoardMaterialStats(project),
    mediaStats: getManualBoardMediaDebugStats(project),
    sceneStateStats: getManualBoardSceneStateDebugStats(project),
  });
}

function queueManualBoardDurableSaveOnce(project = {}, options = {}) {
  const signature = buildManualBoardDurableSaveSignature(project, options?.reason || "");
  if (signature && (signature === pendingManualBoardDurableSaveSignature || signature === lastManualBoardDurableSaveSignature)) return false;
  pendingManualBoardDurableSaveSignature = signature;
  try {
    saveManualClipBoardProjectDurable(project, options).then((ok) => {
      if (ok === "skipped_preserve_media") {
        console.warn("[manual board durable persist] backend write skipped to preserve media", {
          nodeId: getManualProjectOwnerId(project),
          reason: options?.reason || project?.lastPersistReason || "manual_board_backend_durable_save",
          mediaDebugStats: getManualBoardMediaDebugStats(project),
          sceneStateDebugStats: getManualBoardSceneStateDebugStats(project),
        });
        return;
      }

      if (ok === true) {
        lastManualBoardDurableSaveSignature = signature;
        console.info("[manual board durable persist] backend write ok", {
          nodeId: getManualProjectOwnerId(project),
          reason: options?.reason || project?.lastPersistReason || "manual_board_backend_durable_save",
          stats: getManualClipBoardMaterialStats(project),
          mediaDebugStats: getManualBoardMediaDebugStats(project),
          sceneStateDebugStats: getManualBoardSceneStateDebugStats(project),
        });
      }
    }).catch((error) => {
      console.warn("[manual board durable persist] backend write failed; local fallback remains", {
        nodeId: getManualProjectOwnerId(project),
        reason: options?.reason || project?.lastPersistReason || "manual_board_backend_durable_save",
        errorName: error?.name,
        errorMessage: error?.message,
      });
    }).finally(() => {
      if (pendingManualBoardDurableSaveSignature === signature) {
        pendingManualBoardDurableSaveSignature = "";
      }
    });
    return true;
  } catch {
    if (pendingManualBoardDurableSaveSignature === signature) {
      pendingManualBoardDurableSaveSignature = "";
    }
    return false;
  }
}

export async function loadManualClipBoardProjectDurable(nodeId = {}, options = {}) {
  const safeNodeId = String(nodeId || "").trim();
  if (!safeNodeId) return null;
  const qs = new URLSearchParams({ accountKey: getManualProjectAccountScopeId() });
  const response = await fetchJson(`/api/manual-board/load/${encodeURIComponent(safeNodeId)}?${qs.toString()}`, {
    timeoutMs: Number(options?.timeoutMs || 12000),
  });
  if (response?.found === false) return null;
  const project = response?.project && typeof response.project === "object" ? unwrapManualProjectBackupJson(response.project) : null;
  if (!hasMeaningfulManualProject(project)) return null;
  if (!options?.silent) {
    console.info("[manual board durable hydrate] backend load ok", {
      nodeId: safeNodeId,
      found: response?.found,
      stats: getManualClipBoardMaterialStats(project),
    });
    const mediaRefs = findManualBoardMediaRefs(project, { maxDepth: 8 }).slice(0, 20);

    console.info("[manual board durable load media debug]", {
      nodeId: safeNodeId,
      mediaDebugStats: getManualBoardMediaDebugStats(project),
      sceneStateDebugStats: getManualBoardSceneStateDebugStats(project),
      refsCount: mediaRefs.length,
      refs: mediaRefs,
    });
  }
  return project;
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

function readManualProjectJsonSessionStorage(key) {
  try {
    const raw = sessionStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}


export function getManualClipBoardOpenStateStorageKey() {
  return getAccountScopedStorageKey(MANUAL_CLIP_BOARD_OPEN_STATE_KEY);
}

export function readManualClipBoardOpenState() {
  return readManualProjectJsonStorage(getManualClipBoardOpenStateStorageKey());
}

export function writeManualClipBoardOpenState(state = {}) {
  try {
    const payload = {
      ...(state || {}),
      isOpen: Boolean(state?.isOpen),
      sourceNodeId: String(state?.sourceNodeId || "").trim(),
      selectedSceneId: String(state?.selectedSceneId || "").trim(),
      project_id: String(state?.project_id || state?.projectId || "").trim(),
      input_signature: String(state?.input_signature || state?.inputSignature || "").trim(),
      audio_signature: String(state?.audio_signature || state?.audioSignature || state?.forceAudioSignature || "").trim(),
      forceProjectId: String(state?.forceProjectId || state?.project_id || state?.projectId || "").trim(),
      forceInputSignature: String(state?.forceInputSignature || state?.input_signature || state?.inputSignature || "").trim(),
      forceAudioSignature: String(state?.forceAudioSignature || state?.audio_signature || state?.audioSignature || "").trim(),
      manualBoardExplicitNewProject: Boolean(state?.manualBoardExplicitNewProject),
      routePath: String(state?.routePath || "").trim(),
      updatedAt: Number(state?.updatedAt || Date.now()),
    };
    localStorage.setItem(getManualClipBoardOpenStateStorageKey(), JSON.stringify(payload));
    return true;
  } catch {
    return false;
  }
}

export function clearManualClipBoardOpenState(extra = {}) {
  return writeManualClipBoardOpenState({ ...(extra || {}), isOpen: false, updatedAt: Date.now() });
}

export function readCanonicalManualClipBoardProject() {
  return readManualProjectJsonStorage(getManualClipBoardCanonicalStorageKey());
}

export function writeCanonicalManualClipBoardProject(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const fullStorageProject = sanitizeManualClipBoardProjectForStorage(safeProject);
  const fullSerialized = JSON.stringify(fullStorageProject);
  const useLightweightPersist = fullSerialized.length > MANUAL_STORAGE_MAX_SAFE_LENGTH;
  const storageMode = useLightweightPersist ? "lightweight" : "full";
  const storageProject = protectManualBoardTimingStorageSnapshot(
    useLightweightPersist ? buildLightweightManualClipBoardProjectForStorage(safeProject) : fullStorageProject,
    safeProject,
    storageMode,
  );
  logManualBoardTimingSaveDebug(storageProject, storageMode);
  const serialized = JSON.stringify(storageProject);
  localStorage.setItem(getManualClipBoardCanonicalStorageKey(), serialized);
  writeManualClipBoardStorageModeLog({ mode: useLightweightPersist ? "lightweight" : "full", serializedLength: serialized.length, emergencySaved: false, quotaCleanupTriggered: false, removedKeysCount: 0, reason: storageProject.lastPersistReason || "write_canonical_manual_clip_board_project", nodeId: storageProject.nodeId || storageProject.sourceNodeId || "" });
  return true;
}

function writeManualClipBoardStorageModeLog({
  mode = "full",
  serializedLength = 0,
  emergencySaved = false,
  quotaCleanupTriggered = false,
  removedKeysCount = 0,
  reason = "",
  nodeId = "",
} = {}) {
  try {
    console.info("[MANUAL BOARD STORAGE MODE]", {
      mode,
      serializedKb: Math.round(Number(serializedLength || 0) / 1024),
      emergencySaved: Boolean(emergencySaved),
      quotaCleanupTriggered: Boolean(quotaCleanupTriggered),
      removedKeysCount: Number(removedKeysCount || 0),
      reason: String(reason || ""),
      nodeId: String(nodeId || ""),
    });
  } catch {}
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

export function getManualClipBoardSnapshotSize(project = {}) {
  try {
    return JSON.stringify(sanitizeManualClipBoardProjectForStorage(project && typeof project === "object" ? project : {})).length;
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

function isBadPersistentUrlString(value = "") {
  const normalized = String(value || "").trim().toLowerCase();
  return normalized.startsWith("data:")
    || normalized.startsWith("blob:")
    || normalized.includes(";base64,")
    || /^data[a-z0-9_/-]*:/i.test(normalized);
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
    if (isBadPersistentUrlString(value)) return "";
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
    if (!MANUAL_STORAGE_SCENE_LIGHTWEIGHT_KEYS.has(safeKey)) return;
    if (MANUAL_STORAGE_DANGEROUS_KEY_RE.test(safeKey) && !MANUAL_STORAGE_SCENE_LIGHTWEIGHT_KEYS.has(safeKey)) return;
    const stripped = stripLargeManualStorageValue(value, `scenes[${index}].${safeKey}`);
    if (stripped === undefined) return;
    compact[safeKey] = stripped;
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
      if (typeof value === "string" && value.length <= 20000 && !isBadPersistentUrlString(value)) compact[safeKey] = value;
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
  const url = String(audio.url || meta.url || project.audio_url || project.audioUrl || "").trim();
  const filename = String(
    audio.filename
    || audio.name
    || meta.filename
    || meta.name
    || project.audio_name
    || project.audioName
    || ""
  ).trim();
  const durationSec = Number(audio.duration_sec ?? meta.duration_sec ?? project.audio_duration_sec ?? project.audioDurationSec ?? 0) || 0;
  return {
    ...meta,
    ...audio,
    url,
    name: String(audio.name || meta.name || filename || "").trim(),
    filename,
    duration_sec: durationSec,
    duration_ms: Number(audio.duration_ms ?? meta.duration_ms ?? project.audio_duration_ms ?? project.audioDurationMs ?? 0) || Math.round(durationSec * 1000),
  };
}


function stableManualStringHash(value = "") {
  const text = String(value || "");
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

export function computeManualProjectInputSignature(project = {}, options = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeAudioMetadata(safeProject);
  const storyBlocks = Array.isArray(safeProject.story_blocks) ? safeProject.story_blocks : [];
  const phrases = Array.isArray(safeProject.audio_phrases) ? safeProject.audio_phrases : [];
  const markers = Array.isArray(safeProject.markers) ? safeProject.markers : [];
  const scenes = Array.isArray(safeProject.scenes) ? safeProject.scenes : [];
  const audioParts = [audio.url, audio.filename, Number(audio.duration_sec || 0).toFixed(3)].join("|");
  const storyParts = JSON.stringify({
    blocks: storyBlocks.map((block, index) => ({
      id: block?.block_id || block?.id || block?.story_block_id || index,
      text: block?.text || block?.translated_text_ru || block?.summary_ru || block?.title || "",
    })),
    phrases: phrases.map((phrase, index) => ({ id: phrase?.id || index, text: phrase?.text || phrase?.phrase || phrase?.translated_text_ru || "" })),
    markers: markers.map((marker, index) => ({ id: marker?.id || index, start: marker?.start_sec, end: marker?.end_sec, text: marker?.text || "" })),
    scenes: scenes.map((scene, index) => ({ id: scene?.scene_id || scene?.id || index, start: scene?.start_sec, end: scene?.end_sec, text: scene?.translated_text_ru || scene?.scene_goal_ru || "" })),
  });
  if (options?.audioOnly) return `audio:${stableManualStringHash(audioParts)}`;
  if (options?.storyOnly) return `story:${stableManualStringHash(storyParts)}`;
  return `input:${stableManualStringHash(`${audioParts}::${storyParts}`)}`;
}

function getManualProjectProjectId(project = {}) {
  return String(project?.project_id || project?.projectId || "").trim();
}

export function getManualProjectInputSignature(project = {}) {
  return String(project?.input_signature || project?.inputSignature || computeManualProjectInputSignature(project)).trim();
}

export function getManualBoardStrictProjectIdentity(project = {}) {
  const audio = project?.audio || project?.audio_metadata || {};
  return {
    projectId: String(project?.project_id || project?.projectId || "").trim(),
    inputSignature: String(project?.input_signature || project?.inputSignature || "").trim(),
    audioSignature: String(project?.audio_signature || project?.audioSignature || "").trim(),
    audioUrl: String(audio?.url || project?.audio_url || project?.audioUrl || "").trim(),
    audioDurationSec: Number(audio?.duration_sec || project?.audio_duration_sec || 0) || 0,
    sourceNodeId: String(project?.sourceNodeId || project?.nodeId || project?.ownerNodeId || "").trim(),
  };
}

export function manualBoardStrictIdentityMatches(a = {}, b = {}) {
  const ia = getManualBoardStrictProjectIdentity(a);
  const ib = getManualBoardStrictProjectIdentity(b);

  if (ia.projectId && ib.projectId && ia.projectId !== ib.projectId) return false;
  if (ia.inputSignature && ib.inputSignature && ia.inputSignature !== ib.inputSignature) return false;
  if (ia.audioSignature && ib.audioSignature && ia.audioSignature !== ib.audioSignature) return false;
  if (ia.audioUrl && ib.audioUrl && ia.audioUrl !== ib.audioUrl) return false;

  if (ia.audioDurationSec && ib.audioDurationSec) {
    if (Math.abs(ia.audioDurationSec - ib.audioDurationSec) > 0.05) return false;
  }

  return true;
}

export function manualClipBoardProjectsShareIdentity(a = {}, b = {}) {
  if (!hasMeaningfulManualProject(a) || !hasMeaningfulManualProject(b)) return true;
  const ownerA = getManualProjectOwnerId(a);
  const ownerB = getManualProjectOwnerId(b);
  // Manual Timing's source/owner node is the durable identity anchor.
  // Project ids and input signatures may legitimately change after import,
  // backup restore, or rebuild and must not block material autosaves for the
  // same Manual Timing board.
  if (ownerA && ownerB) return ownerA === ownerB;
  const projectA = getManualProjectProjectId(a);
  const projectB = getManualProjectProjectId(b);
  if (projectA && projectB && projectA !== projectB) return false;
  const signatureA = getManualProjectInputSignature(a);
  const signatureB = getManualProjectInputSignature(b);
  if (signatureA && signatureB && signatureA !== signatureB) return false;
  return true;
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
    format: String(safeProject.format || safeProject.aspect_ratio || "9:16"),
    aspect_ratio: String(safeProject.aspect_ratio || safeProject.format || "9:16"),
    format_locked: Boolean(safeProject.format_locked),
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
    scenes: compactArray(safeProject.scenes).map((scene, index) => sanitizeManualClipBoardSceneForStorage(scene, index)),
    selectedSceneId: String(safeProject.selectedSceneId || ""),
    timing_status: String(safeProject.timing_status || ""),
    project_id: String(safeProject.project_id || safeProject.projectId || ""),
    projectId: String(safeProject.projectId || safeProject.project_id || ""),
    sourceNodeId: String(safeProject.sourceNodeId || safeProject.nodeId || ""),
    ownerNodeId: String(safeProject.ownerNodeId || safeProject.sourceNodeId || safeProject.nodeId || ""),
    input_signature: String(safeProject.input_signature || safeProject.inputSignature || computeManualProjectInputSignature(safeProject)),
    audio_signature: String(safeProject.audio_signature || safeProject.audioSignature || computeManualProjectInputSignature(safeProject, { audioOnly: true })),
    story_signature: String(safeProject.story_signature || safeProject.storySignature || computeManualProjectInputSignature(safeProject, { storyOnly: true })),
    revision: Number(safeProject.revision || 0) || 0,
    deletionRevision: Number(safeProject.deletionRevision || safeProject.deletion_revision || 0) || 0,
    deletion_revision: Number(safeProject.deletion_revision || safeProject.deletionRevision || 0) || 0,
    deleted_media_revision: Number(safeProject.deleted_media_revision || safeProject.deletedMediaRevision || 0) || 0,
    updatedAt,
  };
}

export function unwrapManualProjectBackupJson(raw = {}) {
  const safeRaw = raw && typeof raw === "object" ? raw : {};
  if (safeRaw.backup_type === "photostudio_manual_project_emergency_backup") {
    const emergencyProject = safeRaw.project && typeof safeRaw.project === "object" ? safeRaw.project : {};
    const emergencyScenes = Array.isArray(emergencyProject?.scenes) ? emergencyProject.scenes : [];
    return {
      project_mode: "manual_clip_board",
      project_kind: "clip",
      format: emergencyProject?.format || "16:9",
      aspect_ratio: emergencyProject?.format || emergencyProject?.aspect_ratio || "16:9",
      story_blocks: Array.isArray(emergencyProject?.story_blocks) ? emergencyProject.story_blocks : [],
      scenes: emergencyScenes,
      ...emergencyProject,
      selectedSceneId: emergencyProject?.selectedSceneId || emergencyScenes?.[0]?.scene_id || "",
    };
  }
  if (safeRaw.backup_type !== "photostudio_manual_project_backup") return safeRaw;
  const audio = normalizeAudioMetadata(safeRaw);
  return {
    ...safeRaw,
    audio,
    audio_metadata: audio,
    project_mode: String(safeRaw.project_mode || ""),
    project_kind: String(safeRaw.project_kind || ""),
    format: String(safeRaw.format || safeRaw.aspect_ratio || "9:16"),
    aspect_ratio: String(safeRaw.aspect_ratio || safeRaw.format || "9:16"),
    format_locked: Boolean(safeRaw.format_locked),
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
    || hasText(scene?.imageUrl)
    || hasText(scene?.image_preview_url)
    || hasText(scene?.imagePreviewUrl)
    || hasText(scene?.start_image_url)
    || hasText(scene?.startImageUrl)
    || hasText(scene?.end_image_url)
    || hasText(scene?.endImageUrl)
    || hasText(scene?.start_image_preview_url)
    || hasText(scene?.startImagePreviewUrl)
    || hasText(scene?.end_image_preview_url)
    || hasText(scene?.endImagePreviewUrl)
    || hasText(scene?.generated_image_url)
    || hasText(scene?.generatedImageUrl)
  );
  const sceneHasVideo = (scene = {}) => Boolean(
    hasText(scene?.video_url)
    || hasText(scene?.videoUrl)
    || hasText(scene?.generated_video_url)
    || hasText(scene?.generatedVideoUrl)
    || hasText(scene?.final_video_url)
    || hasText(scene?.finalVideoUrl)
    || hasText(scene?.result_video_url)
    || hasText(scene?.resultVideoUrl)
    || hasText(scene?.video_asset_url)
    || hasText(scene?.videoAssetUrl)
    || hasText(scene?.mmaudio_video_url)
    || hasText(scene?.mmaudioVideoUrl)
  );
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
  const sceneHasJob = (scene = {}) => hasText(scene?.video_job_id) || hasText(scene?.videoJobId) || hasText(scene?.mmaudio_job_id) || hasText(scene?.mmaudioJobId);
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
  const sceneHasVideoAudioFlag = (scene = {}) => scene?.video_has_audio === true || scene?.videoHasAudio === true || scene?.hasAudio === true;
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
  const mmaudioJobCount = scenes.filter((scene) => hasText(scene?.mmaudio_job_id) || hasText(scene?.mmaudioJobId)).length;
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
    mmaudioJobs: mmaudioJobCount,
    generatedAudio: generatedAudioCount,
    payloadPreviews: payloadPreviewCount,
    videoHasAudio: videoAudioFlagCount,
    customRoutes: customRouteCount,
    materialTotal,
    materialScore,
  };
}


export function hasManualBoardMaterials(project = {}) {
  const stats = getManualClipBoardMaterialStats(project);
  return Boolean(
    (Number(stats.images || stats.imageCount || 0) || 0) > 0
    || (Number(stats.videos || stats.videoCount || 0) || 0) > 0
    || (Number(stats.prompts || stats.promptCount || 0) || 0) > 0
    || (Number(stats.videoJobs || 0) || 0) > 0
    || (Number(stats.mmaudioJobs || 0) || 0) > 0
    || (Number(stats.materialTotal || 0) || 0) > 0
  );
}

const MANUAL_BOARD_REAL_USER_MATERIAL_UPDATE_REASONS = new Set([
  "selected_scene_user",
  "delete_scene_video_user",
  "delete_scene_photo_user",
  "delete_first_last_start_image_user",
  "delete_first_last_end_image_user",
  "manual_video_queued",
  "manual_video_done",
  "manual_video_error",
  "manual_mmaudio_queued",
  "manual_mmaudio_done",
  "manual_mmaudio_error",
  "manual_mmaudio_gain_done",
  "upload_scene_image",
  "update_scene",
  "manual_director_embedded_update",
]);

function logManualBoardIdentityDebug(nextProject = {}, existingProject = {}, decision = "allow") {
  try {
    const candidateNodeId = getManualProjectOwnerId(nextProject);
    const existingNodeId = getManualProjectOwnerId(existingProject);
    const candidateProjectId = getManualProjectProjectId(nextProject);
    const existingProjectId = getManualProjectProjectId(existingProject);
    const candidateInputSignature = getManualProjectInputSignature(nextProject);
    const existingInputSignature = getManualProjectInputSignature(existingProject);
    console.warn("[MANUAL BOARD IDENTITY DEBUG]", {
      candidateNodeId,
      existingNodeId,
      candidateProjectId,
      existingProjectId,
      candidateInputSignature,
      existingInputSignature,
      sameNode: Boolean(candidateNodeId && existingNodeId && candidateNodeId === existingNodeId),
      sameProjectId: Boolean(candidateProjectId && existingProjectId && candidateProjectId === existingProjectId),
      sameInputSignature: Boolean(candidateInputSignature && existingInputSignature && candidateInputSignature === existingInputSignature),
      decision,
    });
  } catch {}
}

function manualBoardStatsHaveMaterialGain(nextStats = {}, existingStats = {}) {
  return (nextStats.imageCount || 0) > (existingStats.imageCount || 0)
    || (nextStats.videoCount || 0) > (existingStats.videoCount || 0)
    || (nextStats.promptCount || 0) > (existingStats.promptCount || 0)
    || (nextStats.readyStatuses || 0) > (existingStats.readyStatuses || 0)
    || (nextStats.materialScore || 0) > (existingStats.materialScore || 0)
    || (nextStats.materialTotal || 0) > (existingStats.materialTotal || 0);
}

function logManualBoardPersistDecision(label, nextProject, existingProject, extra = {}) {
  try {
    const nextStats = getManualClipBoardMaterialStats(nextProject);
    const existingStats = getManualClipBoardMaterialStats(existingProject);
    console.info(label, {
      sourceNodeId: String(nextProject?.sourceNodeId || nextProject?.nodeId || existingProject?.sourceNodeId || existingProject?.nodeId || "").trim(),
      source: String(nextProject?.source || existingProject?.source || "manual_timing_node"),
      project_id: String(nextProject?.project_id || nextProject?.projectId || ""),
      input_signature: String(nextProject?.input_signature || nextProject?.inputSignature || ""),
      revision: Number(nextProject?.revision || 0) || 0,
      deletionRevision: Number(nextProject?.deletionRevision || nextProject?.deletion_revision || nextProject?.deleted_media_revision || 0) || 0,
      updatedAt: Number(nextProject?.updatedAt || nextProject?.updated_at || 0) || 0,
      stats: nextStats,
      selectedSceneId: String(nextProject?.selectedSceneId || "").trim(),
      existing: {
        project_id: String(existingProject?.project_id || existingProject?.projectId || ""),
        input_signature: String(existingProject?.input_signature || existingProject?.inputSignature || ""),
        revision: Number(existingProject?.revision || 0) || 0,
        deletionRevision: Number(existingProject?.deletionRevision || existingProject?.deletion_revision || existingProject?.deleted_media_revision || 0) || 0,
        updatedAt: Number(existingProject?.updatedAt || existingProject?.updated_at || 0) || 0,
        stats: existingStats,
        selectedSceneId: String(existingProject?.selectedSceneId || "").trim(),
      },
      ...extra,
    });
  } catch {}
}

export function shouldSkipManualBoardPersistToProtectMaterials(nextProject, existingProject, options = {}) {
  const reason = String(options?.reason || nextProject?.lastPersistReason || "").toLowerCase();
  const isIntentionalMaterialDelete = /delete.*(video|photo|image)|remove.*(video|photo|image)|clear.*(video|photo|image)|user_delete/.test(reason);
  const isRealUserMaterialUpdate = MANUAL_BOARD_REAL_USER_MATERIAL_UPDATE_REASONS.has(reason) || isIntentionalMaterialDelete;
  if (options?.forceReplace || options?.explicitReset || options?.allowMaterialLoss) return false;
  const nextStats = getManualClipBoardMaterialStats(nextProject);
  const existingStats = getManualClipBoardMaterialStats(existingProject);
  const sameIdentity = manualClipBoardProjectsShareIdentity(nextProject, existingProject);
  if (!sameIdentity && hasMeaningfulManualProject(existingProject)) {
    logManualBoardIdentityDebug(nextProject, existingProject, "reject_identity_mismatch");
    logManualBoardPersistDecision("[MANUAL BOARD PERSIST BLOCK OLDER]", nextProject, existingProject, { reason, blockReason: "identity_mismatch" });
    return true;
  }
  const nextRevision = Number(nextProject?.revision || 0) || 0;
  const existingRevision = Number(existingProject?.revision || 0) || 0;
  const nextDeletionRevision = Number(nextProject?.deletionRevision || nextProject?.deletion_revision || nextProject?.deleted_media_revision || 0) || 0;
  const existingDeletionRevision = Number(existingProject?.deletionRevision || existingProject?.deletion_revision || existingProject?.deleted_media_revision || 0) || 0;
  const nextUpdatedAt = Number(nextProject?.updatedAt || nextProject?.updated_at || 0) || 0;
  const existingUpdatedAt = Number(existingProject?.updatedAt || existingProject?.updated_at || 0) || 0;

  if (sameIdentity && (
    nextDeletionRevision > existingDeletionRevision
    || manualBoardStatsHaveMaterialGain(nextStats, existingStats)
    || (isRealUserMaterialUpdate && (nextRevision > existingRevision || nextUpdatedAt > existingUpdatedAt))
  )) {
    logManualBoardPersistDecision("[MANUAL BOARD PERSIST ALLOW NEWER]", nextProject, existingProject, { reason });
    return false;
  }

  if (existingDeletionRevision > nextDeletionRevision) {
    logManualBoardPersistDecision("[MANUAL BOARD PERSIST BLOCK OLDER]", nextProject, existingProject, { reason, blockReason: "older_deletion_revision" });
    return true;
  }
  if (existingRevision > nextRevision && existingUpdatedAt > nextUpdatedAt) {
    logManualBoardPersistDecision("[MANUAL BOARD PERSIST BLOCK OLDER]", nextProject, existingProject, { reason, blockReason: "older_revision_and_updated_at" });
    return true;
  }
  if (existingStats.materialTotal <= 0) return false;
  const losesMaterials = nextStats.materialScore < existingStats.materialScore
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
  if (losesMaterials) {
    logManualBoardPersistDecision("[MANUAL BOARD PERSIST BLOCK OLDER]", nextProject, existingProject, { reason, blockReason: "same_or_older_revision_material_loss" });
    return true;
  }
  return false;
}

function getManualClipBoardDeletionRevision(project = {}) {
  return Number(
    project?.deletionRevision
    || project?.deletion_revision
    || project?.deleted_media_revision
    || project?.deletedMediaRevision
    || 0,
  ) || 0;
}

function getManualClipBoardRevision(project = {}) {
  return Number(project?.revision || project?.project_revision || project?.projectRevision || 0) || 0;
}

export function scoreManualClipBoardProject(project = {}) {
  const stats = getManualClipBoardMaterialStats(project);
  const updatedAt = Number(project?.updatedAt || project?.updated_at || 0) || 0;
  const deletionRevision = getManualClipBoardDeletionRevision(project);
  const revision = getManualClipBoardRevision(project);
  return {
    stats,
    updatedAt,
    deletionRevision,
    revision,
    score:
      stats.materialScore
      + stats.mmaudioJobs * 5000
      + stats.customRoutes * 50
      + stats.scenes
      + Math.min(updatedAt / 1000000000000, 10),
  };
}

function logManualClipBoardNewerRevisionPick(winner, olderRicherCandidates = []) {
  if (!olderRicherCandidates.length) return;
  try {
    console.info("[MANUAL BOARD PICK NEWER REVISION]", {
      picked: {
        project_id: winner?.project?.project_id || winner?.project?.projectId || "",
        nodeId: winner?.project?.nodeId || winner?.project?.sourceNodeId || "",
        deletionRevision: winner?.scoreData?.deletionRevision,
        revision: winner?.scoreData?.revision,
        updatedAt: winner?.scoreData?.updatedAt,
        materialScore: winner?.scoreData?.stats?.materialScore,
        materialTotal: winner?.scoreData?.stats?.materialTotal,
      },
      skipped: olderRicherCandidates.map(({ project, scoreData }) => ({
        project_id: project?.project_id || project?.projectId || "",
        nodeId: project?.nodeId || project?.sourceNodeId || "",
        deletionRevision: scoreData?.deletionRevision,
        revision: scoreData?.revision,
        updatedAt: scoreData?.updatedAt,
        materialScore: scoreData?.stats?.materialScore,
        materialTotal: scoreData?.stats?.materialTotal,
      })),
    });
  } catch {}
}

function manualClipBoardProjectSort(a, b) {
  if (b.scoreData.stats.imageCount !== a.scoreData.stats.imageCount) {
    return b.scoreData.stats.imageCount - a.scoreData.stats.imageCount;
  }
  if (b.scoreData.stats.videoCount !== a.scoreData.stats.videoCount) {
    return b.scoreData.stats.videoCount - a.scoreData.stats.videoCount;
  }
  if (b.scoreData.stats.promptCount !== a.scoreData.stats.promptCount) {
    return b.scoreData.stats.promptCount - a.scoreData.stats.promptCount;
  }
  if (b.scoreData.stats.readyStatuses !== a.scoreData.stats.readyStatuses) {
    return b.scoreData.stats.readyStatuses - a.scoreData.stats.readyStatuses;
  }
  if (b.scoreData.stats.materialScore !== a.scoreData.stats.materialScore) {
    return b.scoreData.stats.materialScore - a.scoreData.stats.materialScore;
  }
  if (b.scoreData.updatedAt !== a.scoreData.updatedAt) {
    return b.scoreData.updatedAt - a.scoreData.updatedAt;
  }
  if (b.scoreData.stats.materialTotal !== a.scoreData.stats.materialTotal) {
    return b.scoreData.stats.materialTotal - a.scoreData.stats.materialTotal;
  }
  if (b.scoreData.revision !== a.scoreData.revision) {
    return b.scoreData.revision - a.scoreData.revision;
  }
  if (b.scoreData.deletionRevision !== a.scoreData.deletionRevision) {
    return b.scoreData.deletionRevision - a.scoreData.deletionRevision;
  }
  if (b.scoreData.stats.mmaudioJobs !== a.scoreData.stats.mmaudioJobs) {
    return b.scoreData.stats.mmaudioJobs - a.scoreData.stats.mmaudioJobs;
  }
  if (b.scoreData.stats.audioSlices !== a.scoreData.stats.audioSlices) {
    return b.scoreData.stats.audioSlices - a.scoreData.stats.audioSlices;
  }
  if (b.scoreData.stats.customRoutes !== a.scoreData.stats.customRoutes) {
    return b.scoreData.stats.customRoutes - a.scoreData.stats.customRoutes;
  }
  return b.scoreData.score - a.scoreData.score;
}

function manualClipBoardNewerRevisionBeatsRicherSnapshot(winner, candidate) {
  const winnerDeletion = winner?.scoreData?.deletionRevision || 0;
  const candidateDeletion = candidate?.scoreData?.deletionRevision || 0;
  const winnerRevision = winner?.scoreData?.revision || 0;
  const candidateRevision = candidate?.scoreData?.revision || 0;
  const winnerIsNewer = winnerDeletion > candidateDeletion
    || (winnerDeletion === candidateDeletion && winnerRevision > candidateRevision);
  if (!winnerIsNewer) return false;
  return (candidate?.scoreData?.stats?.materialScore || 0) > (winner?.scoreData?.stats?.materialScore || 0)
    || (candidate?.scoreData?.stats?.materialTotal || 0) > (winner?.scoreData?.stats?.materialTotal || 0);
}

export function pickBestManualClipBoardProject(candidates = []) {
  const valid = candidates.filter(hasMeaningfulManualProject);
  if (!valid.length) return null;

  const anchor = valid[0];
  const sameIdentity = valid.filter((project) => manualClipBoardProjectsShareIdentity(anchor, project));
  if (!sameIdentity.length) return null;

  const ranked = sameIdentity
    .map((project) => ({
      project,
      scoreData: scoreManualClipBoardProject(project),
    }))
    .sort(manualClipBoardProjectSort);
  const winner = ranked[0];
  logManualClipBoardNewerRevisionPick(
    winner,
    ranked.slice(1).filter((candidate) => manualClipBoardNewerRevisionBeatsRicherSnapshot(winner, candidate)),
  );
  return winner.project;
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


export function rememberLastGoodManualClipBoardProject(project = {}) {
  if (!hasMeaningfulManualProject(project)) return false;
  try {
    const existingLastGood = readLastGoodManualClipBoardProject();
    if (manualBoardHasReloadRestorePayload(existingLastGood) && manualBoardSnapshotLosesTimeline(existingLastGood, project)) {
      console.warn("[MANUAL BOARD SESSION SNAPSHOT PRESERVE] blocked timeline-empty overwrite", {
        existing: getManualBoardTimingSummary(existingLastGood),
        incoming: getManualBoardTimingSummary(project),
      });
      return false;
    }
    const storageProject = sanitizeManualClipBoardProjectForStorage(project);
    sessionStorage.setItem(MANUAL_CLIP_BOARD_LAST_GOOD_SESSION_KEY, JSON.stringify(storageProject));
    return true;
  } catch {
    return false;
  }
}

export function readLastGoodManualClipBoardProject() {
  try {
    const raw = sessionStorage.getItem(MANUAL_CLIP_BOARD_LAST_GOOD_SESSION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function readEmergencyManualClipBoardProjectForNode(nodeId = "") {
  const safeNodeId = String(nodeId || "").trim();
  const candidates = [];
  const addCandidate = (key = "") => {
    if (!key) return;
    candidates.push(readManualProjectJsonSessionStorage(key));
    candidates.push(readManualProjectJsonStorage(key));
  };
  addCandidate(getAccountScopedStorageKey(`manual_clip_board_emergency_snapshot:${safeNodeId || "default"}`));
  if (safeNodeId) addCandidate(getAccountScopedStorageKey(`manual_clip_board_emergency_snapshot:default`));
  return pickBestManualClipBoardProject(candidates);
}

function writeManualClipBoardProjectStorage(project = {}) {
  const fullStorageProject = sanitizeManualClipBoardProjectForStorage(project);
  const fullSerialized = JSON.stringify(fullStorageProject);
  const useLightweightPersist = fullSerialized.length > MANUAL_STORAGE_MAX_SAFE_LENGTH;
  const storageMode = useLightweightPersist ? "lightweight" : "full";
  const storageProject = protectManualBoardTimingStorageSnapshot(
    useLightweightPersist ? buildLightweightManualClipBoardProjectForStorage(project) : fullStorageProject,
    project,
    storageMode,
  );
  logManualBoardTimingSaveDebug(storageProject, storageMode);
  const serialized = JSON.stringify(storageProject);
  localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY), serialized);

  const nodeId = String(storageProject?.nodeId || storageProject?.sourceNodeId || "").trim();
  if (nodeId) {
    localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY), nodeId);
    localStorage.setItem(getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(nodeId)), serialized);
  }
  writeManualClipBoardStorageModeLog({ mode: useLightweightPersist ? "lightweight" : "full", serializedLength: serialized.length, emergencySaved: false, quotaCleanupTriggered: false, removedKeysCount: 0, reason: storageProject.lastPersistReason || "write_manual_clip_board_project_storage", nodeId });
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
  return String(project?.sourceNodeId || project?.ownerNodeId || project?.nodeId || "").trim();
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

  // Do not scan every node-scoped board as an active candidate: a richer old project
  // from another source can otherwise resurrect media into a new board.

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

export function replaceManualClipBoardProjectForNode(nodeId = "", newProject = {}, options = {}) {
  const safeNodeId = String(nodeId || newProject?.nodeId || newProject?.sourceNodeId || "").trim();
  const reason = String(options?.reason || newProject?.lastPersistReason || "manual_new_project_from_audio_split");
  if (!safeNodeId) {
    console.error("[MANUAL BOARD NEW PROJECT REPLACE] missing nodeId", {
      reason,
      projectOwner: getManualProjectOwnerId(newProject),
      stats: getManualClipBoardMaterialStats(newProject),
    });
    return false;
  }

  const firstSceneId = Array.isArray(newProject?.scenes)
    ? String(newProject.scenes[0]?.scene_id || newProject.scenes[0]?.id || "").trim()
    : "";
  const previousNodeProject = readManualClipBoardProjectForNode(safeNodeId);
  const previousRevision = getManualClipBoardRevision(previousNodeProject);
  const previousDeletionRevision = getManualClipBoardDeletionRevision(previousNodeProject);
  const nextRevision = Math.max(1, Number(newProject?.revision || 0) || 0, previousRevision + 1);
  const nextDeletionRevision = Math.max(
    Number(newProject?.deletionRevision || newProject?.deletion_revision || newProject?.deleted_media_revision || 0) || 0,
    previousDeletionRevision + 1,
    nextRevision,
  );
  const safeProject = {
    ...(newProject || {}),
    nodeId: safeNodeId,
    sourceNodeId: safeNodeId,
    selectedSceneId: firstSceneId,
    revision: nextRevision,
    project_revision: nextRevision,
    projectRevision: nextRevision,
    deletionRevision: nextDeletionRevision,
    deletion_revision: nextDeletionRevision,
    deleted_media_revision: nextDeletionRevision,
    deletedMediaRevision: nextDeletionRevision,
    updatedAt: Date.now(),
    lastPersistReason: reason,
  };
  const storageProject = sanitizeManualClipBoardProjectForStorage(safeProject);
  const serialized = JSON.stringify(storageProject);
  const canonicalKey = getManualClipBoardCanonicalStorageKey();
  const activeKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
  const activeIdKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY);
  const nodeScopedKey = getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(safeNodeId));

  const writeReplacementProject = () => {
    clearManualClipBoardProjectForNode(safeNodeId, { clearActive: true, clearCanonical: true });
    localStorage.setItem(canonicalKey, serialized);
    localStorage.setItem(activeKey, serialized);
    localStorage.setItem(activeIdKey, safeNodeId);
    localStorage.setItem(nodeScopedKey, serialized);

    if (canUseLegacyManualProjectStorage()) {
      localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY, serialized);
      localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY, safeNodeId);
      localStorage.setItem(getManualClipBoardProjectStorageKey(safeNodeId), serialized);
    }
  };

  try {
    writeReplacementProject();

    const openRoutePath = options?.routePath || "/studio/storyboard";
    writeManualClipBoardOpenState({
      isOpen: true,
      sourceNodeId: safeNodeId,
      selectedSceneId: storageProject.selectedSceneId || "",
      project_id: String(storageProject.project_id || storageProject.projectId || "").trim(),
      input_signature: String(storageProject.input_signature || storageProject.inputSignature || "").trim(),
      audio_signature: String(storageProject.audio_signature || storageProject.audioSignature || "").trim(),
      manualBoardExplicitNewProject: true,
      forceProjectId: String(storageProject.project_id || storageProject.projectId || "").trim(),
      forceInputSignature: String(storageProject.input_signature || storageProject.inputSignature || "").trim(),
      forceAudioSignature: String(storageProject.audio_signature || storageProject.audioSignature || "").trim(),
      routePath: openRoutePath,
      updatedAt: Date.now(),
    });
    if (openRoutePath === "/studio/storyboard") {
      console.info('[MANUAL BOARD CANONICAL ROUTE] route="/studio/storyboard"', { nodeId: safeNodeId, reason });
    }
    rememberManualClipBoardStorageError(null);
    console.info("[MANUAL BOARD NEW PROJECT REPLACE]", {
      nodeId: safeNodeId,
      reason,
      forceReplace: options?.forceReplace !== false,
      explicitReset: options?.explicitReset !== false,
      allowMaterialLoss: options?.allowMaterialLoss !== false,
      project_id: storageProject.project_id || storageProject.projectId || "",
      input_signature: storageProject.input_signature || storageProject.inputSignature || "",
      audio_signature: storageProject.audio_signature || storageProject.audioSignature || "",
      story_signature: storageProject.story_signature || storageProject.storySignature || "",
      revision: storageProject.revision || 0,
      deletionRevision: storageProject.deletionRevision || storageProject.deletion_revision || storageProject.deleted_media_revision || 0,
      selectedSceneId: storageProject.selectedSceneId || "",
      audio: {
        url: String(storageProject.audio?.url || storageProject.audio_url || storageProject.audioUrl || "").trim(),
        name: String(storageProject.audio?.name || storageProject.audio?.filename || storageProject.audio_name || "").trim(),
        duration_sec: Number(storageProject.audio?.duration_sec || storageProject.audio_duration_sec || 0) || 0,
      },
      stats: getManualClipBoardMaterialStats(storageProject),
    });
    return storageProject;
  } catch (err) {
    const quotaExceeded = isQuotaExceededError(err);
    let quotaCleanupRemovedKeys = [];
    if (quotaExceeded) {
      quotaCleanupRemovedKeys = cleanupManualClipBoardStorageAggressive({ currentNodeId: safeNodeId, activeProjectId: storageProject.project_id || storageProject.projectId || "" });
      try {
        writeReplacementProject();
        rememberManualClipBoardStorageError(null);
        console.warn("[MANUAL BOARD NEW PROJECT REPLACE] saved after quota cleanup", {
          nodeId: safeNodeId,
          reason,
          removedKeysCount: quotaCleanupRemovedKeys.length,
          serializedKb: Math.round(serialized.length / 1024),
        });
        return storageProject;
      } catch (retryErr) {
        err = retryErr;
      }
    }

    const errorInfo = {
      reason: quotaExceeded ? "quota_exceeded" : "replace_failed",
      errorName: err?.name,
      errorMessage: err?.message,
      errorCode: err?.code,
      serializedLength: serialized.length,
      serializedKb: Math.round(serialized.length / 1024),
      quotaCleanupRemovedKeysCount: quotaCleanupRemovedKeys.length,
      quotaCleanupRemovedKeys,
      nodeId: safeNodeId,
    };
    rememberManualClipBoardStorageError(errorInfo);
    console.warn("[MANUAL BOARD NEW PROJECT REPLACE] failed", errorInfo);
    return false;
  }
}

function firstManualString(...values) {
  for (const value of values) {
    const normalized = String(value || "").trim();
    if (normalized) return normalized;
  }
  return "";
}

function firstManualFiniteNumber(...values) {
  for (const value of values) {
    if (value === null || value === undefined || value === "") continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric;
  }
  return 0;
}

function getManualBoardSceneTimingValues(scene = {}) {
  const safeScene = scene && typeof scene === "object" ? scene : {};
  const safeTiming = safeScene.timing && typeof safeScene.timing === "object" ? safeScene.timing : {};
  const topTiming = {
    start_sec: firstManualFiniteNumber(safeScene.start_sec, safeScene.startSec),
    end_sec: firstManualFiniteNumber(safeScene.end_sec, safeScene.endSec),
    duration_sec: firstManualFiniteNumber(safeScene.duration_sec, safeScene.durationSec),
    speech_start_sec: firstManualFiniteNumber(safeScene.speech_start_sec, safeScene.speechStartSec),
    speech_end_sec: firstManualFiniteNumber(safeScene.speech_end_sec, safeScene.speechEndSec),
  };
  const nestedTiming = {
    start_sec: firstManualFiniteNumber(safeTiming.start_sec, safeTiming.startSec),
    end_sec: firstManualFiniteNumber(safeTiming.end_sec, safeTiming.endSec),
    duration_sec: firstManualFiniteNumber(safeTiming.duration_sec, safeTiming.durationSec),
    speech_start_sec: firstManualFiniteNumber(safeTiming.speech_start_sec, safeTiming.speechStartSec),
    speech_end_sec: firstManualFiniteNumber(safeTiming.speech_end_sec, safeTiming.speechEndSec),
  };
  const topHasRealTiming = topTiming.end_sec > 0 || topTiming.duration_sec > 0;
  const nestedHasRealTiming = nestedTiming.end_sec > 0 || nestedTiming.duration_sec > 0;
  const baseTiming = topHasRealTiming || !nestedHasRealTiming ? topTiming : nestedTiming;
  const duration_sec = baseTiming.duration_sec > 0
    ? baseTiming.duration_sec
    : Math.max(0, baseTiming.end_sec - baseTiming.start_sec);
  const end_sec = baseTiming.end_sec > 0
    ? baseTiming.end_sec
    : (duration_sec > 0 ? baseTiming.start_sec + duration_sec : 0);
  return {
    ...baseTiming,
    end_sec,
    duration_sec,
  };
}

function manualBoardSceneHasTiming(scene = {}) {
  const timing = getManualBoardSceneTimingValues(scene);
  return timing.end_sec > 0 || timing.duration_sec > 0;
}

function getManualBoardTimingSummary(project = {}) {
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const scenesWithTiming = scenes.filter(manualBoardSceneHasTiming).length;
  return {
    audioDurationSec: Number(project?.audio?.duration_sec ?? project?.audio_metadata?.duration_sec ?? project?.audio_duration_sec ?? project?.audioDurationSec ?? 0) || 0,
    markersCount: Array.isArray(project?.markers) ? project.markers.length : 0,
    storyBlocksCount: Array.isArray(project?.story_blocks) ? project.story_blocks.length : 0,
    scenesCount: scenes.length,
    scenesWithTiming,
    scenesWithZeroTiming: Math.max(0, scenes.length - scenesWithTiming),
    selectedSceneId: firstManualString(project?.selectedSceneId, project?.selected_scene_id),
  };
}

function logManualBoardTimingSaveDebug(project = {}, mode = "full") {
  try {
    console.info("[MANUAL BOARD TIMING SAVE DEBUG]", {
      mode,
      ...getManualBoardTimingSummary(project),
    });
  } catch {}
}

function manualBoardHasTimelineStructure(project = {}) {
  const summary = getManualBoardTimingSummary(project);
  return Boolean(summary.audioDurationSec > 0 || summary.markersCount > 0 || summary.storyBlocksCount > 0 || summary.scenesWithTiming > 0);
}

function manualBoardHasReloadRestorePayload(project = {}) {
  const summary = getManualBoardTimingSummary(project);
  return Boolean(
    summary.audioDurationSec > 0
    && summary.storyBlocksCount > 0
    && summary.scenesCount > 0
    && summary.scenesWithTiming > 0
  );
}

function manualBoardSnapshotLosesTimeline(referenceProject = {}, candidateProject = {}) {
  const reference = getManualBoardTimingSummary(referenceProject);
  const candidate = getManualBoardTimingSummary(candidateProject);
  return Boolean(
    (reference.audioDurationSec > 0 && candidate.audioDurationSec <= 0)
    || (reference.markersCount > 0 && candidate.markersCount <= 0)
    || (reference.storyBlocksCount > 0 && candidate.storyBlocksCount <= 0)
    || (reference.scenesWithTiming > 0 && candidate.scenesWithTiming < reference.scenesWithTiming)
  );
}

function mergeManualBoardTimelineIntoStorageProject(storageProject = {}, referenceProject = {}) {
  const safeStorage = storageProject && typeof storageProject === "object" ? storageProject : {};
  const safeReference = referenceProject && typeof referenceProject === "object" ? referenceProject : {};
  const referenceAudio = normalizeAudioMetadata(safeReference);
  const storageAudio = safeStorage.audio && typeof safeStorage.audio === "object" ? safeStorage.audio : {};
  const referenceScenes = Array.isArray(safeReference.scenes) ? safeReference.scenes : [];
  const referenceById = new Map(referenceScenes.map((scene, index) => [firstManualString(scene?.scene_id, scene?.id, index), scene]));
  const nextScenes = (Array.isArray(safeStorage.scenes) ? safeStorage.scenes : []).map((scene, index) => {
    const sceneId = firstManualString(scene?.scene_id, scene?.id, index);
    const referenceScene = referenceById.get(sceneId) || referenceScenes[index] || {};
    if (!manualBoardSceneHasTiming(referenceScene) || manualBoardSceneHasTiming(scene)) return scene;
    const timing = getManualBoardSceneTimingValues(referenceScene);
    return {
      ...(scene && typeof scene === "object" ? scene : {}),
      start_sec: timing.start_sec,
      end_sec: timing.end_sec,
      duration_sec: timing.duration_sec,
      speech_start_sec: timing.speech_start_sec,
      speech_end_sec: timing.speech_end_sec,
      timing: {
        ...((scene?.timing && typeof scene.timing === "object") ? scene.timing : {}),
        ...timing,
      },
      story_block_id: firstManualString(scene?.story_block_id, referenceScene?.story_block_id),
      story_block_title_ru: firstManualString(scene?.story_block_title_ru, referenceScene?.story_block_title_ru),
      source_phrase_ids: Array.isArray(scene?.source_phrase_ids) && scene.source_phrase_ids.length ? scene.source_phrase_ids : compactArray(referenceScene?.source_phrase_ids),
      audio_slice_url: firstManualString(scene?.audio_slice_url, referenceScene?.audio_slice_url),
      audio_slice_duration_sec: firstManualFiniteNumber(scene?.audio_slice_duration_sec, referenceScene?.audio_slice_duration_sec),
    };
  });
  return {
    ...safeStorage,
    audio: {
      ...storageAudio,
      url: firstManualString(storageAudio.url, referenceAudio.url),
      name: firstManualString(storageAudio.name, referenceAudio.name, referenceAudio.filename),
      duration_sec: firstManualFiniteNumber(storageAudio.duration_sec, referenceAudio.duration_sec),
    },
    audio_duration_sec: firstManualFiniteNumber(safeStorage.audio_duration_sec, referenceAudio.duration_sec, safeReference.audio_duration_sec),
    markers: Array.isArray(safeStorage.markers) && safeStorage.markers.length ? safeStorage.markers : compactArray(safeReference.markers),
    story_blocks: Array.isArray(safeStorage.story_blocks) && safeStorage.story_blocks.length ? safeStorage.story_blocks : compactArray(safeReference.story_blocks),
    scenes: nextScenes,
  };
}

function protectManualBoardTimingStorageSnapshot(storageProject = {}, referenceProject = {}, mode = "full") {
  if (!manualBoardHasTimelineStructure(referenceProject) || !manualBoardSnapshotLosesTimeline(referenceProject, storageProject)) return storageProject;
  const mergedProject = mergeManualBoardTimelineIntoStorageProject(storageProject, referenceProject);
  console.warn("[MANUAL BOARD REJECT TIMING REGRESSION]", {
    mode,
    before: getManualBoardTimingSummary(referenceProject),
    candidate: getManualBoardTimingSummary(storageProject),
    fallback: getManualBoardTimingSummary(mergedProject),
  });
  return mergedProject;
}

function buildEmergencyManualClipBoardSceneForStorage(scene = {}, fallbackIndex = 0) {
  const safeScene = scene && typeof scene === "object" ? scene : {};
  const timing = getManualBoardSceneTimingValues(safeScene);
  const prompts = {
    source_image_prompt_en: firstManualString(safeScene.source_image_prompt_en, safeScene.sourceImagePromptEn, safeScene.image_prompt, safeScene.imagePrompt),
    source_image_prompt_ru: firstManualString(safeScene.source_image_prompt_ru, safeScene.sourceImagePromptRu),
    i2v_prompt_en: firstManualString(safeScene.i2v_prompt_en, safeScene.i2vPromptEn, safeScene.video_prompt, safeScene.videoPrompt),
    i2v_negative_prompt_en: firstManualString(safeScene.i2v_negative_prompt_en, safeScene.i2vNegativePromptEn, safeScene.negative_prompt, safeScene.negativePrompt),
    video_prompt: firstManualString(safeScene.video_prompt, safeScene.videoPrompt, safeScene.i2v_prompt_en, safeScene.i2vPromptEn),
    negative_prompt: firstManualString(safeScene.negative_prompt, safeScene.negativePrompt, safeScene.i2v_negative_prompt_en, safeScene.i2vNegativePromptEn),
    sound_prompt: firstManualString(safeScene.sound_prompt, safeScene.soundPrompt),
  };
  const sourcePhraseIds = Array.isArray(safeScene.source_phrase_ids)
    ? safeScene.source_phrase_ids
    : (Array.isArray(safeScene.sourcePhraseIds) ? safeScene.sourcePhraseIds : []);
  return stripLargeManualStorageValue({
    scene_id: firstManualString(safeScene.scene_id, safeScene.id),
    index: firstManualFiniteNumber(safeScene.index, fallbackIndex),
    route: firstManualString(safeScene.route, safeScene.manualRoute, safeScene.renderMode),
    start_sec: timing.start_sec,
    end_sec: timing.end_sec,
    duration_sec: timing.duration_sec,
    speech_start_sec: timing.speech_start_sec,
    speech_end_sec: timing.speech_end_sec,
    timing,
    story_block_id: firstManualString(safeScene.story_block_id, safeScene.storyBlockId),
    story_block_title_ru: firstManualString(safeScene.story_block_title_ru, safeScene.storyBlockTitleRu),
    source_phrase_ids: sourcePhraseIds,
    audio_slice_url: firstManualString(safeScene.audio_slice_url, safeScene.audioSliceUrl),
    audio_slice_duration_sec: firstManualFiniteNumber(safeScene.audio_slice_duration_sec, safeScene.audioSliceDurationSec),
    prompts,
    video_prompt: prompts.video_prompt,
    negative_prompt: prompts.negative_prompt,
    image_url: firstManualString(safeScene.image_url, safeScene.imageUrl, safeScene.generated_image_url, safeScene.generatedImageUrl),
    start_image_url: firstManualString(safeScene.start_image_url, safeScene.startImageUrl, safeScene.image_url, safeScene.imageUrl),
    end_image_url: firstManualString(safeScene.end_image_url, safeScene.endImageUrl),
    video_url: firstManualString(safeScene.video_url, safeScene.videoUrl, safeScene.result_video_url, safeScene.resultVideoUrl, safeScene.generated_video_url, safeScene.generatedVideoUrl, safeScene.final_video_url, safeScene.finalVideoUrl),
    status: firstManualString(safeScene.status, safeScene.video_status, safeScene.videoStatus),
    video_status: firstManualString(safeScene.video_status, safeScene.videoStatus, safeScene.status),
    video_job_id: firstManualString(safeScene.video_job_id, safeScene.videoJobId),
  }, "emergency.scenes[]");
}

function buildLightweightManualClipBoardProjectForStorage(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const canonical = sanitizeManualClipBoardProjectForStorage(safeProject);
  const audioMetadata = normalizeAudioMetadata(safeProject);
  const selectedSceneId = firstManualString(
    safeProject.selectedSceneId,
    safeProject.selected_scene_id,
    canonical.selectedSceneId,
    safeProject.scenes?.[0]?.scene_id,
    safeProject.scenes?.[0]?.id,
  );
  return stripLargeManualStorageValue({
    ...canonical,
    project_mode: firstManualString(safeProject.project_mode, safeProject.projectMode, canonical.project_mode) || "manual_clip_board",
    project_kind: firstManualString(safeProject.project_kind, safeProject.projectKind, canonical.project_kind) || "clip",
    format: firstManualString(safeProject.format, safeProject.aspect_ratio, canonical.format) || "16:9",
    aspect_ratio: firstManualString(safeProject.aspect_ratio, safeProject.format, canonical.aspect_ratio) || "16:9",
    format_locked: Boolean(safeProject.format_locked ?? safeProject.formatLocked ?? canonical.format_locked ?? true),
    audio: {
      url: firstManualString(audioMetadata.url, canonical.audio?.url),
      name: firstManualString(audioMetadata.name, audioMetadata.filename, canonical.audio?.name),
      duration_sec: firstManualFiniteNumber(audioMetadata.duration_sec, canonical.audio?.duration_sec),
    },
    audio_duration_sec: firstManualFiniteNumber(safeProject.audio_duration_sec, safeProject.audioDurationSec, audioMetadata.duration_sec, canonical.audio_duration_sec),
    markers: compactArray(safeProject.markers),
    story_blocks: compactArray(safeProject.story_blocks),
    selectedSceneId,
    project_id: firstManualString(safeProject.project_id, safeProject.projectId, canonical.project_id),
    projectId: firstManualString(safeProject.projectId, safeProject.project_id, canonical.projectId),
    sourceNodeId: firstManualString(safeProject.sourceNodeId, safeProject.nodeId, canonical.sourceNodeId),
    ownerNodeId: firstManualString(safeProject.ownerNodeId, safeProject.sourceNodeId, safeProject.nodeId, canonical.ownerNodeId),
    input_signature: firstManualString(safeProject.input_signature, safeProject.inputSignature, canonical.input_signature),
    inputSignature: firstManualString(safeProject.inputSignature, safeProject.input_signature, canonical.inputSignature),
    audio_signature: firstManualString(safeProject.audio_signature, safeProject.audioSignature, canonical.audio_signature),
    story_signature: firstManualString(safeProject.story_signature, safeProject.storySignature, canonical.story_signature),
    scenes: (Array.isArray(safeProject.scenes) ? safeProject.scenes : []).map((scene, index) => buildEmergencyManualClipBoardSceneForStorage(scene, index)),
    revision: Number(safeProject.revision || canonical.revision || 0) || 0,
    deletionRevision: Number(safeProject.deletionRevision || safeProject.deletion_revision || canonical.deletionRevision || 0) || 0,
    deletion_revision: Number(safeProject.deletion_revision || safeProject.deletionRevision || canonical.deletion_revision || 0) || 0,
    deleted_media_revision: Number(safeProject.deleted_media_revision || safeProject.deletedMediaRevision || canonical.deleted_media_revision || 0) || 0,
    updatedAt: Date.now(),
    lastPersistReason: "lightweight_large_project_snapshot",
  }, "lightweight");
}

export function buildEmergencyManualClipBoardProjectForStorage(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audioMetadata = normalizeAudioMetadata(safeProject);
  return stripLargeManualStorageValue({
    project_id: firstManualString(safeProject.project_id, safeProject.projectId),
    projectId: firstManualString(safeProject.projectId, safeProject.project_id),
    nodeId: firstManualString(safeProject.nodeId, safeProject.sourceNodeId, safeProject.ownerNodeId),
    sourceNodeId: firstManualString(safeProject.sourceNodeId, safeProject.nodeId),
    ownerNodeId: firstManualString(safeProject.ownerNodeId, safeProject.sourceNodeId, safeProject.nodeId),
    project_mode: firstManualString(safeProject.project_mode, safeProject.projectMode) || "manual_clip_board",
    project_kind: firstManualString(safeProject.project_kind, safeProject.projectKind) || "clip",
    format: firstManualString(safeProject.format, safeProject.aspect_ratio) || "16:9",
    aspect_ratio: firstManualString(safeProject.aspect_ratio, safeProject.format) || "16:9",
    format_locked: Boolean(safeProject.format_locked ?? safeProject.formatLocked ?? true),
    audio: {
      url: firstManualString(audioMetadata.url),
      name: firstManualString(audioMetadata.name, audioMetadata.filename),
      duration_sec: firstManualFiniteNumber(audioMetadata.duration_sec),
    },
    audio_duration_sec: firstManualFiniteNumber(safeProject.audio_duration_sec, safeProject.audioDurationSec, audioMetadata.duration_sec),
    markers: compactArray(safeProject.markers),
    story_blocks: compactArray(safeProject.story_blocks),
    selectedSceneId: firstManualString(safeProject.selectedSceneId, safeProject.selected_scene_id, safeProject.scenes?.[0]?.scene_id, safeProject.scenes?.[0]?.id),
    input_signature: firstManualString(safeProject.input_signature, safeProject.inputSignature),
    audio_signature: firstManualString(safeProject.audio_signature, safeProject.audioSignature),
    story_signature: firstManualString(safeProject.story_signature, safeProject.storySignature),
    scenes: (Array.isArray(safeProject.scenes) ? safeProject.scenes : []).map((scene, index) => buildEmergencyManualClipBoardSceneForStorage(scene, index)),
    updatedAt: Date.now(),
    lastPersistReason: "emergency_ultra_light_snapshot",
  }, "emergency");
}

function writeEmergencyManualClipBoardSnapshot(project = {}, nodeId = "") {
  const safeNodeId = String(nodeId || project?.nodeId || project?.sourceNodeId || "").trim();
  const existingEmergencyProject = readEmergencyManualClipBoardProjectForNode(safeNodeId);
  if (manualBoardHasReloadRestorePayload(existingEmergencyProject) && manualBoardSnapshotLosesTimeline(existingEmergencyProject, project)) {
    console.warn("[MANUAL BOARD EMERGENCY SNAPSHOT PRESERVE] blocked timeline-empty overwrite", {
      nodeId: safeNodeId,
      existing: getManualBoardTimingSummary(existingEmergencyProject),
      incoming: getManualBoardTimingSummary(project),
    });
    return true;
  }
  const emergencyProject = protectManualBoardTimingStorageSnapshot(buildEmergencyManualClipBoardProjectForStorage({
    ...(project && typeof project === "object" ? project : {}),
    nodeId: safeNodeId || project?.nodeId || project?.sourceNodeId || "",
  }), project, "emergency");
  logManualBoardTimingSaveDebug(emergencyProject, "emergency");
  const serialized = JSON.stringify(emergencyProject);
  const emergencyKey = getAccountScopedStorageKey(`manual_clip_board_emergency_snapshot:${safeNodeId || "default"}`);
  const writes = [];
  const tryWriteSessionEmergency = () => {
    try {
      sessionStorage.setItem(emergencyKey, serialized);
      writes.push("sessionStorage");
      return true;
    } catch {
      return false;
    }
  };
  const tryWriteLocalEmergency = () => {
    try {
      localStorage.setItem(emergencyKey, serialized);
      writes.push("localStorage");
      return true;
    } catch {
      return false;
    }
  };
  const rememberEmergencySnapshot = (quotaCleanupTriggered = false, removedKeysCount = 0) => {
    try { rememberLastGoodManualClipBoardProject(emergencyProject); } catch {}
    writeManualClipBoardStorageModeLog({
      mode: "emergency",
      serializedLength: serialized.length,
      emergencySaved: true,
      quotaCleanupTriggered,
      removedKeysCount,
      nodeId: safeNodeId,
    });
    console.warn("[manual board emergency snapshot] wrote ultra-light fallback in browser", {
      nodeId: safeNodeId,
      storageTargets: writes,
      serializedKb: Math.round(serialized.length / 1024),
      stats: getManualClipBoardMaterialStats(emergencyProject),
    });
  };

  const sessionSaved = tryWriteSessionEmergency();
  const localSaved = tryWriteLocalEmergency();
  if (sessionSaved || localSaved) {
    rememberEmergencySnapshot(false, 0);
    return true;
  }

  const removedKeys = cleanupManualClipBoardStorageAggressive({ currentNodeId: safeNodeId, activeProjectId: emergencyProject.project_id });
  const retryLocalSaved = tryWriteLocalEmergency();
  const retrySessionSaved = tryWriteSessionEmergency();
  if (retryLocalSaved || retrySessionSaved) {
    rememberEmergencySnapshot(removedKeys.length > 0, removedKeys.length);
    return true;
  }

  writeManualClipBoardStorageModeLog({
    mode: "emergency",
    serializedLength: serialized.length,
    emergencySaved: false,
    quotaCleanupTriggered: removedKeys.length > 0,
    removedKeysCount: removedKeys.length,
    nodeId: safeNodeId,
  });
  console.warn("[manual board emergency snapshot] browser storage write failed; automatic JSON download suppressed", {
    nodeId: safeNodeId,
    removedKeysCount: removedKeys.length,
    serializedKb: Math.round(serialized.length / 1024),
  });
  return false;
}

export function persistManualClipBoardProject(project = {}, options = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const forceReplace = Boolean(options?.forceReplace);
  const explicitReset = Boolean(options?.explicitReset);
  const reason = String(options?.reason || safeProject?.lastPersistReason || "");
  const nodeId = getManualProjectOwnerId(safeProject);
  const nodeScopedExisting = nodeId ? readManualClipBoardProjectForNode(nodeId) : null;
  const activeExistingRaw = readActiveManualClipBoardProject();
  const activeExistingRawOwner = getManualProjectOwnerId(activeExistingRaw);
  const activeWasIgnoredBecauseDifferentOwner = Boolean(
    nodeId
    && hasMeaningfulManualProject(activeExistingRaw)
    && !manualProjectBelongsToNode(activeExistingRaw, nodeId)
  );
  const activeExisting = nodeId
    && manualProjectBelongsToNode(activeExistingRaw, nodeId)
    && manualClipBoardProjectsShareIdentity(safeProject, activeExistingRaw)
    ? activeExistingRaw
    : null;
  const existing = nodeId
    ? pickBestManualClipBoardProject([nodeScopedExisting, activeExisting])
    : pickBestManualClipBoardProject([nodeScopedExisting, activeExistingRaw]);
  const existingStats = getManualClipBoardMaterialStats(existing);
  const nextStats = getManualClipBoardMaterialStats(safeProject);

  const isIntentionalMaterialDelete = /delete.*(video|photo|image)|remove.*(video|photo|image)|clear.*(video|photo|image)|user_delete|explicit.*reset|reset|import/.test(reason.toLowerCase());

  if (hasMeaningfulManualProject(existing) && !manualClipBoardProjectsShareIdentity(safeProject, existing) && !forceReplace && !explicitReset) {
    logManualBoardIdentityDebug(safeProject, existing, "reject_identity_mismatch");
    console.warn("[MANUAL BOARD REJECT OLD SNAPSHOT]", {
      candidate: { revision: safeProject?.revision, updatedAt: safeProject?.updatedAt, stats: nextStats },
      current: { revision: existing?.revision, updatedAt: existing?.updatedAt, stats: existingStats },
      rejectReason: "identity_mismatch",
      nodeId,
      existingOwner: getManualProjectOwnerId(existing),
      incomingOwner: getManualProjectOwnerId(safeProject),
      existingSignature: getManualProjectInputSignature(existing),
      incomingSignature: getManualProjectInputSignature(safeProject),
      reason: reason || "manual_board_identity_mismatch",
    });
    return false;
  }

  if (
    hasMeaningfulManualProject(existing)
    && !forceReplace
    && !explicitReset
    && !options?.allowMaterialLoss
    && !isIntentionalMaterialDelete
    && (
      (nextStats.imageCount || nextStats.images || 0) < (existingStats.imageCount || existingStats.images || 0)
      || (nextStats.videoCount || nextStats.videos || 0) < (existingStats.videoCount || existingStats.videos || 0)
    )
  ) {
    console.warn("[MANUAL BOARD PERSIST BLOCKED MEDIA REGRESSION]", {
      reason: reason || "incoming_project_media_regression",
      currentScenesWithImage: existingStats.imageCount || existingStats.images || 0,
      nextScenesWithImage: nextStats.imageCount || nextStats.images || 0,
      currentScenesWithVideo: existingStats.videoCount || existingStats.videos || 0,
      nextScenesWithVideo: nextStats.videoCount || nextStats.videos || 0,
      project_id: safeProject.project_id || safeProject.projectId || "",
      selectedSceneId: safeProject.selectedSceneId || "",
    });
    return false;
  }

  if (shouldSkipManualBoardPersistToProtectMaterials(safeProject, existing, options)) {
    console.warn("[MANUAL BOARD PERSIST BLOCKED MEDIA REGRESSION]", {
      reason: reason || "incoming_project_has_fewer_materials",
      currentScenesWithImage: existingStats.imageCount || existingStats.images || 0,
      nextScenesWithImage: nextStats.imageCount || nextStats.images || 0,
      currentScenesWithVideo: existingStats.videoCount || existingStats.videos || 0,
      nextScenesWithVideo: nextStats.videoCount || nextStats.videos || 0,
      project_id: safeProject.project_id || safeProject.projectId || "",
      selectedSceneId: safeProject.selectedSceneId || "",
    });
    console.warn("[MANUAL BOARD REJECT OLD SNAPSHOT]", {
      candidate: { revision: safeProject?.revision, updatedAt: safeProject?.updatedAt, stats: nextStats },
      current: { revision: existing?.revision, updatedAt: existing?.updatedAt, stats: existingStats },
      rejectReason: "protect_materials",
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
    && !options?.allowMaterialLoss
    && !isIntentionalMaterialDelete
    && hasMeaningfulManualProject(existing)
    && existingStats.materialTotal > 0
    && nextStats.materialTotal === 0
    && nextStats.scenes >= existingStats.scenes * 0.8
  ) {
    console.warn("[MANUAL BOARD REJECT OLD SNAPSHOT]", {
      candidate: { revision: safeProject?.revision, updatedAt: safeProject?.updatedAt, stats: nextStats },
      current: { revision: existing?.revision, updatedAt: existing?.updatedAt, stats: existingStats },
      rejectReason: "protect_materials",
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
  const fullStorageProject = sanitizeManualClipBoardProjectForStorage(safeProject);
  const fullSerialized = JSON.stringify(fullStorageProject);
  const useLightweightPersist = fullSerialized.length > MANUAL_STORAGE_MAX_SAFE_LENGTH;
  const storageMode = useLightweightPersist ? "lightweight" : "full";
  const timingReferenceProject = manualBoardSnapshotLosesTimeline(existing, safeProject) ? existing : safeProject;
  let storageProject = protectManualBoardTimingStorageSnapshot(
    useLightweightPersist ? buildLightweightManualClipBoardProjectForStorage(safeProject) : fullStorageProject,
    timingReferenceProject,
    storageMode,
  );
  logManualBoardTimingSaveDebug(storageProject, storageMode);
  const storageStats = getManualClipBoardMaterialStats(storageProject);
  const durableCandidateProject = fullSerialized.length <= 24 * 1024 * 1024
    ? fullStorageProject
    : storageProject;
  queueManualBoardDurableSaveOnce(durableCandidateProject, {
    reason,
    source: "manual_board_before_localstorage_quota_safe",
  });
  const queueDurableSaveAfterLocalSuccess = (projectForDurable, sourceSuffix = "") => {
    queueManualBoardDurableSaveOnce(projectForDurable, {
      reason,
      source: sourceSuffix || (
        useLightweightPersist
          ? "manual_board_persist_after_local_success_full_backend"
          : "manual_board_persist_after_local_success"
      ),
    });
  };
  try {
    serialized = JSON.stringify(storageProject);
    localStorage.setItem(getManualClipBoardCanonicalStorageKey(), serialized);
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
    rememberLastGoodManualClipBoardProject(storageProject);
    queueDurableSaveAfterLocalSuccess(durableCandidateProject);
    writeManualClipBoardStorageModeLog({ mode: useLightweightPersist ? "lightweight" : "full", serializedLength: serialized.length, emergencySaved: false, quotaCleanupTriggered: false, removedKeysCount: 0, reason, nodeId });
    console.info("[MANUAL BOARD PERSIST WRITE]", {
      target: "canonical+active+node",
      canonicalKey: getManualClipBoardCanonicalStorageKey(),
      reason,
      forceReplace,
      explicitReset,
      nodeId,
      sourceNodeId: storageProject.sourceNodeId,
      project_id: storageProject.project_id || storageProject.projectId || "",
      input_signature: storageProject.input_signature || storageProject.inputSignature || "",
      revision: storageProject.revision || 0,
      deletionRevision: storageProject.deletionRevision || storageProject.deletion_revision || storageProject.deleted_media_revision || 0,
      stats: storageStats,
      embedded: Boolean(options?.embedded),
      incomingStats: nextStats,
      storageStats,
      storageSerializedKb: Math.round(serialized.length / 1024),
      selectedSceneId: storageProject.selectedSceneId,
      updatedAt: storageProject.updatedAt,
    });
    return true;
  } catch (err) {
    const quotaExceeded = isQuotaExceededError(err);
    let quotaCleanupRemovedKeys = [];

    if (quotaExceeded) {
      quotaCleanupRemovedKeys = cleanupManualClipBoardStorageAggressive({
        currentNodeId: nodeId,
        activeProjectId: safeProject.project_id || safeProject.projectId || "",
      });
      try {
        localStorage.setItem(getManualClipBoardCanonicalStorageKey(), serialized);
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
        rememberLastGoodManualClipBoardProject(storageProject);
        queueDurableSaveAfterLocalSuccess(durableCandidateProject, "manual_board_persist_after_quota_cleanup_full_backend");
        writeManualClipBoardStorageModeLog({ mode: useLightweightPersist ? "lightweight" : "full", serializedLength: serialized.length, emergencySaved: false, quotaCleanupTriggered: true, removedKeysCount: quotaCleanupRemovedKeys.length, reason, nodeId });
        console.warn("[MANUAL BOARD PERSIST WRITE] saved after quota cleanup", {
          reason,
          nodeId,
          removedKeysCount: quotaCleanupRemovedKeys.length,
          storageSerializedKb: Math.round(serialized.length / 1024),
          storageStats,
        });
        return true;
      } catch (retryErr) {
        err = retryErr;
      }
    }

    if (quotaExceeded && !useLightweightPersist) {
      try {
        const lightweightProject = protectManualBoardTimingStorageSnapshot(buildLightweightManualClipBoardProjectForStorage(safeProject), timingReferenceProject, "lightweight");
        logManualBoardTimingSaveDebug(lightweightProject, "lightweight");
        const lightweightSerialized = JSON.stringify(lightweightProject);
        localStorage.setItem(getManualClipBoardCanonicalStorageKey(), lightweightSerialized);
        localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY), lightweightSerialized);
        if (nodeId) {
          localStorage.setItem(getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY), nodeId);
          localStorage.setItem(getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(nodeId)), lightweightSerialized);
        }
        if (canUseLegacyManualProjectStorage()) {
          localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY, lightweightSerialized);
          if (nodeId) {
            localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY, nodeId);
            localStorage.setItem(getManualClipBoardProjectStorageKey(nodeId), lightweightSerialized);
          }
        }
        rememberManualClipBoardStorageError(null);
        rememberLastGoodManualClipBoardProject(lightweightProject);
        const lightweightDurableCandidate = fullSerialized.length <= 24 * 1024 * 1024
          ? fullStorageProject
          : lightweightProject;
        queueManualBoardDurableSaveOnce(lightweightDurableCandidate, {
          reason,
          source: fullSerialized.length <= 24 * 1024 * 1024
            ? "manual_board_persist_lightweight_local_full_backend"
            : "manual_board_persist_lightweight_backend",
        });
        writeManualClipBoardStorageModeLog({ mode: "lightweight", serializedLength: lightweightSerialized.length, emergencySaved: false, quotaCleanupTriggered: quotaCleanupRemovedKeys.length > 0, removedKeysCount: quotaCleanupRemovedKeys.length, reason, nodeId });
        console.warn("[MANUAL BOARD PERSIST WRITE] saved lightweight snapshot after quota cleanup", {
          reason,
          nodeId,
          removedKeysCount: quotaCleanupRemovedKeys.length,
          storageSerializedKb: Math.round(lightweightSerialized.length / 1024),
          storageStats: getManualClipBoardMaterialStats(lightweightProject),
        });
        return true;
      } catch (lightweightErr) {
        err = lightweightErr;
      }
    }

    const errorInfo = {
      reason: quotaExceeded ? "quota_exceeded" : "write_failed",
      errorName: err?.name,
      errorMessage: err?.message,
      errorStack: err?.stack,
      errorCode: err?.code,
      storageBackend: "localStorage",
      projectId: safeProject.project_id || safeProject.projectId || "",
      activeProjectId: (() => { try { return readActiveManualClipBoardProject()?.project_id || readActiveManualClipBoardProject()?.projectId || ""; } catch { return ""; } })(),
      snapshotSize: serialized.length,
      sceneCount: Array.isArray(safeProject.scenes) ? safeProject.scenes.length : 0,
      materialCount: nextStats.materialTotal || 0,
      serializedLength: serialized.length,
      serializedKb: Math.round(serialized.length / 1024),
      safeProjectScenesLength: Array.isArray(safeProject.scenes) ? safeProject.scenes.length : 0,
      storageScenesLength: Array.isArray(storageProject.scenes) ? storageProject.scenes.length : 0,
      incomingStats: nextStats,
      storageStats,
      localStorageApproxBytes: getLocalStorageApproxBytes(),
      quotaCleanupRemovedKeysCount: quotaCleanupRemovedKeys.length,
      quotaCleanupRemovedKeys,
      nodeId,
    };
    const emergencySaved = writeEmergencyManualClipBoardSnapshot(storageProject, nodeId);
    errorInfo.emergencySaved = emergencySaved;
    writeManualClipBoardStorageModeLog({ mode: emergencySaved ? "emergency" : (useLightweightPersist ? "lightweight" : "full"), serializedLength: serialized.length, emergencySaved, quotaCleanupTriggered: quotaCleanupRemovedKeys.length > 0, removedKeysCount: quotaCleanupRemovedKeys.length, reason, nodeId });
    rememberManualClipBoardStorageError(errorInfo);
    console.warn("[manual board persist] primary write failed; emergency fallback status recorded", errorInfo);
    return emergencySaved;
  }
}


function getManualClipBoardStorageProjectIdentityForKey(key = "") {
  const project = readManualProjectJsonStorage(key);
  return {
    project,
    nodeId: getManualProjectOwnerId(project),
    projectId: String(project?.project_id || project?.projectId || "").trim(),
    updatedAt: Number(project?.updatedAt || project?.updated_at || 0) || 0,
  };
}

export function cleanupManualClipBoardStorageAggressive(options = {}) {
  const safeNodeId = String(options?.currentNodeId || options?.nodeId || "").trim();
  const activeProjectId = String(options?.activeProjectId || options?.project_id || options?.projectId || "").trim();
  const accountScopeId = getManualProjectAccountScopeId();
  const currentScopedNodeKey = safeNodeId ? getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(safeNodeId)) : "";
  const currentLegacyNodeKey = safeNodeId ? getManualClipBoardProjectStorageKey(safeNodeId) : "";
  const activeIdKeys = new Set([
    getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY),
    MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY,
  ].filter(Boolean));
  const currentNodeKeys = new Set([
    currentScopedNodeKey,
    currentLegacyNodeKey,
  ].filter(Boolean));
  const removedKeys = [];
  const keptKeys = [];

  const shouldKeepKey = (key = "") => {
    if (!key || !key.includes("manual_clip_board")) return true;
    if (currentNodeKeys.has(key)) return true;
    if (activeIdKeys.has(key)) {
      try {
        return String(localStorage.getItem(key) || "").trim() === safeNodeId;
      } catch {
        return false;
      }
    }
    const { nodeId, projectId } = getManualClipBoardStorageProjectIdentityForKey(key);
    if (safeNodeId && nodeId === safeNodeId) return true;
    if (activeProjectId && projectId === activeProjectId) return true;
    return false;
  };

  try {
    const keys = [];
    for (let index = 0; index < localStorage.length; index += 1) {
      const key = localStorage.key(index);
      if (key) keys.push(key);
    }

    keys
      .filter((key) => key.includes("manual_clip_board"))
      .sort((a, b) => {
        const aInfo = getManualClipBoardStorageProjectIdentityForKey(a);
        const bInfo = getManualClipBoardStorageProjectIdentityForKey(b);
        return aInfo.updatedAt - bInfo.updatedAt;
      })
      .forEach((key) => {
        if (shouldKeepKey(key)) {
          keptKeys.push(key);
          return;
        }
        try {
          localStorage.removeItem(key);
          removedKeys.push(key);
        } catch {}
      });

    if (removedKeys.length) {
      console.warn("[manual board quota cleanup aggressive]", {
        currentNodeId: safeNodeId,
        activeProjectId,
        accountScopeId,
        removedKeysCount: removedKeys.length,
        keptKeysCount: keptKeys.length,
        removedKeys,
        localStorageApproxBytes: getLocalStorageApproxBytes(),
      });
    }
  } catch (err) {
    console.warn("[manual board quota cleanup aggressive] failed", {
      currentNodeId: safeNodeId,
      activeProjectId,
      accountScopeId,
      errorName: err?.name,
      errorMessage: err?.message,
    });
  }

  return removedKeys;
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


function removeManualClipBoardForceWriteTargetKeys({ nodeId = "", nodeScopedKey = "", activeKey = "", activeIdKey = "", canonicalKey = "" } = {}) {
  const removedKeys = [];
  const removeTargetKey = (key) => {
    if (!key) return;
    try {
      localStorage.removeItem(key);
      removedKeys.push(key);
    } catch {}
  };

  removeTargetKey(nodeScopedKey);
  removeTargetKey(activeKey);
  removeTargetKey(activeIdKey);
  removeTargetKey(canonicalKey);

  if (canUseLegacyManualProjectStorage()) {
    removeTargetKey(getManualClipBoardProjectStorageKey(nodeId));
    removeTargetKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
    removeTargetKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY);
  }

  return removedKeys;
}

export function forceWriteManualClipBoardProjectForNode(project = {}, options = {}) {
  const incomingProject = project && typeof project === "object" ? project : {};
  const nodeId = getManualProjectOwnerId(incomingProject);
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
  const existingProject = pickBestManualClipBoardProject([readManualClipBoardProjectForNode(nodeId), readActiveManualClipBoardProject()]);
  const timingReferenceProject = manualBoardSnapshotLosesTimeline(existingProject, safeProject) ? existingProject : safeProject;
  const fullStorageProject = sanitizeManualClipBoardProjectForStorage(safeProject);
  const fullSerialized = JSON.stringify(fullStorageProject);
  const useLightweightPersist = fullSerialized.length > MANUAL_STORAGE_MAX_SAFE_LENGTH;
  const storageMode = useLightweightPersist ? "lightweight" : "full";
  let storageProject = protectManualBoardTimingStorageSnapshot(
    useLightweightPersist ? buildLightweightManualClipBoardProjectForStorage(safeProject) : fullStorageProject,
    timingReferenceProject,
    storageMode,
  );
  const canonicalKey = getManualClipBoardCanonicalStorageKey();
  const activeKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY);
  const activeIdKey = getAccountScopedStorageKey(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY);
  const nodeScopedKey = getAccountScopedStorageKey(getManualClipBoardProjectStorageKey(nodeId));
  const accountScopeId = getManualProjectAccountScopeId();
  logManualBoardTimingSaveDebug(storageProject, storageMode);
  const stats = getManualClipBoardMaterialStats(safeProject);
  const storageStats = getManualClipBoardMaterialStats(storageProject);
  queueManualClipBoardProjectDurableSave(storageProject, { reason, source: "manual_board_force_write" });
  const localStorageApproxBytesBeforeWrite = getLocalStorageApproxBytes();
  let serialized = "";

  const removeForceWriteTargetKeys = () => removeManualClipBoardForceWriteTargetKeys({
    nodeId,
    nodeScopedKey,
    activeKey,
    activeIdKey,
    canonicalKey,
  });

  const writeAndVerify = () => {
    let nodeScopedWritten = false;
    const optionalWriteFailures = [];
    const setOptionalItem = (key, value, label) => {
      try {
        localStorage.setItem(key, value);
      } catch (err) {
        optionalWriteFailures.push({
          label,
          key,
          errorName: err?.name,
          errorMessage: err?.message,
          errorCode: err?.code,
        });
      }
    };

    localStorage.setItem(nodeScopedKey, serialized);
    nodeScopedWritten = true;

    setOptionalItem(activeIdKey, nodeId, "activeIdKey");
    setOptionalItem(activeKey, serialized, "activeKey");
    setOptionalItem(canonicalKey, serialized, "canonicalKey");

    if (canUseLegacyManualProjectStorage()) {
      setOptionalItem(getManualClipBoardProjectStorageKey(nodeId), serialized, "legacyNodeScopedKey");
      setOptionalItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY, serialized, "legacyActiveKey");
      setOptionalItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY, nodeId, "legacyActiveIdKey");
    }

    const readback = readManualClipBoardProjectForNode(nodeId);
    const readbackOwner = getManualProjectOwnerId(readback);
    const readbackExists = hasMeaningfulManualProject(readback);
    const wrote = Boolean(readbackExists && readbackOwner === nodeId);

    console.info("[manual board force write node-scoped]", {
      nodeId,
      reason,
      wrote,
      nodeScopedWritten,
      optionalWriteFailed: optionalWriteFailures.length > 0,
      optionalWriteFailuresCount: optionalWriteFailures.length,
      readbackExists,
      readbackOwner,
      serializedKb: Math.round(serialized.length / 1024),
      stats,
      storageStats,
      selectedSceneId: storageProject.selectedSceneId,
      storageMode: useLightweightPersist ? "lightweight" : "full",
    });
    writeManualClipBoardStorageModeLog({ mode: useLightweightPersist ? "lightweight" : "full", serializedLength: serialized.length, emergencySaved: false, quotaCleanupTriggered: false, removedKeysCount: 0, reason, nodeId });

    if (optionalWriteFailures.length) {
      console.warn("[manual board force write node-scoped] partial write", {
        nodeId,
        reason,
        wrote,
        nodeScopedWritten,
        readbackExists,
        readbackOwner,
        optionalWriteFailures,
      });
    }

    if (!wrote) {
      console.error("[manual board force write node-scoped] verify failed", {
        activeKey,
        activeIdKey,
        canonicalKey,
        nodeScopedKey,
        accountScopeId,
        nodeId,
        nodeScopedWritten,
        optionalWriteFailures,
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
    removeForceWriteTargetKeys();
    const wrote = writeAndVerify();
    if (wrote) {
      rememberManualClipBoardStorageError(null);
      rememberLastGoodManualClipBoardProject(storageProject);
    }
    return wrote;
  } catch (err) {
    let retryWrote = false;
    let removedKeys = [];
    const quotaExceeded = isQuotaExceededError(err);

    if (quotaExceeded && serialized) {
      removedKeys = cleanupManualClipBoardStorageAggressive({ currentNodeId: nodeId, activeProjectId: safeProject.project_id || safeProject.projectId || "" });
      removedKeys.push(...removeForceWriteTargetKeys());
      try {
        retryWrote = writeAndVerify();
      } catch (retryErr) {
        err = retryErr;
      }
    }

    if (retryWrote) {
      rememberManualClipBoardStorageError(null);
      rememberLastGoodManualClipBoardProject(storageProject);
      writeManualClipBoardStorageModeLog({ mode: useLightweightPersist ? "lightweight" : "full", serializedLength: serialized.length, emergencySaved: false, quotaCleanupTriggered: removedKeys.length > 0, removedKeysCount: removedKeys.length, reason, nodeId });
      return true;
    }

    if (quotaExceeded && !useLightweightPersist) {
      try {
        const lightweightProject = protectManualBoardTimingStorageSnapshot(buildLightweightManualClipBoardProjectForStorage(safeProject), timingReferenceProject, "lightweight");
        logManualBoardTimingSaveDebug(lightweightProject, "lightweight");
        const lightweightSerialized = JSON.stringify(lightweightProject);
        serialized = lightweightSerialized;
        removeForceWriteTargetKeys();
        localStorage.setItem(nodeScopedKey, lightweightSerialized);
        try { localStorage.setItem(activeIdKey, nodeId); } catch {}
        try { localStorage.setItem(activeKey, lightweightSerialized); } catch {}
        try { localStorage.setItem(canonicalKey, lightweightSerialized); } catch {}
        if (canUseLegacyManualProjectStorage()) {
          try { localStorage.setItem(getManualClipBoardProjectStorageKey(nodeId), lightweightSerialized); } catch {}
          try { localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY, lightweightSerialized); } catch {}
          try { localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY, nodeId); } catch {}
        }
        const readback = readManualClipBoardProjectForNode(nodeId);
        if (hasMeaningfulManualProject(readback) && getManualProjectOwnerId(readback) === nodeId) {
          rememberManualClipBoardStorageError(null);
          rememberLastGoodManualClipBoardProject(lightweightProject);
          writeManualClipBoardStorageModeLog({ mode: "lightweight", serializedLength: lightweightSerialized.length, emergencySaved: false, quotaCleanupTriggered: removedKeys.length > 0, removedKeysCount: removedKeys.length, reason, nodeId });
          console.warn("[manual board force write node-scoped] saved lightweight snapshot after quota cleanup", { nodeId, reason, removedKeysCount: removedKeys.length, serializedKb: Math.round(lightweightSerialized.length / 1024), stats: getManualClipBoardMaterialStats(lightweightProject) });
          return true;
        }
      } catch (lightweightErr) {
        err = lightweightErr;
      }
    }

    const errorInfo = {
      reason: quotaExceeded ? "quota_exceeded" : "write_failed",
      errorName: err?.name,
      errorMessage: err?.message,
      errorStack: err?.stack,
      errorCode: err?.code,
      storageBackend: "localStorage",
      projectId: safeProject.project_id || safeProject.projectId || "",
      activeProjectId: (() => { try { return readActiveManualClipBoardProject()?.project_id || readActiveManualClipBoardProject()?.projectId || ""; } catch { return ""; } })(),
      snapshotSize: serialized.length,
      sceneCount: Array.isArray(safeProject.scenes) ? safeProject.scenes.length : 0,
      materialCount: stats.materialTotal || 0,
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
    const emergencySaved = writeEmergencyManualClipBoardSnapshot(storageProject, nodeId);
    errorInfo.emergencySaved = emergencySaved;
    writeManualClipBoardStorageModeLog({ mode: emergencySaved ? "emergency" : (useLightweightPersist ? "lightweight" : "full"), serializedLength: serialized.length, emergencySaved, quotaCleanupTriggered: removedKeys.length > 0, removedKeysCount: removedKeys.length, reason, nodeId });
    rememberManualClipBoardStorageError(errorInfo);
    console.warn("[manual board force write node-scoped] primary write failed; emergency fallback status recorded", errorInfo);
    return emergencySaved;
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
