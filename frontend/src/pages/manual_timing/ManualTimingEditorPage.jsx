import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { API_BASE } from "../../services/api";
import {
  buildManualProjectBackupJson,
  canUseLegacyManualProjectStorage,
  computeManualProjectInputSignature,
  getAccountScopedStorageKey,
  getManualClipBoardMaterialStats,
  logManualBoardMediaRefs,
  hasManualBoardMaterials,
  hasMeaningfulManualProject,
  readActiveManualClipBoardProject,
  readManualClipBoardProjectForNode,
  readLegacyManualClipBoardProject,
  readLegacyManualTimingProject,
  replaceManualClipBoardProjectForNode,
  writeManualClipBoardOpenState,
  unwrapManualProjectBackupJson,
} from "../clip_nodes/manualProjectBackup.js";
import "./ManualTimingEditorPage.css";
import {
  MANUAL_TIMING_ACTIVE_PROJECT_KEY,
  MANUAL_TIMING_AI_PASS_BY_TYPE,
  MANUAL_TIMING_AI_PASS_STAGES,
  MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE,
  MANUAL_TIMING_ENERGY,
  MANUAL_TIMING_MUSIC_CLIP_MODE,
  MANUAL_TIMING_PODCAST_DIALOGUE_MODE,
  MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
  MANUAL_TIMING_ROUTES,
  MANUAL_TIMING_SECTIONS,
  MANUAL_TIMING_STORY_PROJECT_KIND,
  MANUAL_TIMING_STORY_VOICEOVER_MODE,
  buildDraftStoryBlocksFromGapAwareScenes,
  buildGapAwareScenesFromAudioPhrases,
  buildManualTimingAiSplitRequestJson,
  buildManualTimingBlockStoryboardPassJson,
  buildManualTimingClipPassJson,
  buildManualTimingExportJson,
  buildManualTimingPodcastPassJson,
  buildManualTimingStoryBiblePassJson,
  buildManualTimingStoryPassJson,
  buildManualTimingSampleJson,
  completeManualTimingWorkflowStage,
  buildManualTimingScenesFromMarkers,
  buildManualTimingWarnings,
  deriveStoryBlockRangeFromScenes,
  formatTimingSec,
  getDefaultManualTimingNodeData,
  getManualTimingSceneDurationWarning,
  inferManualTimingCompletedStages,
  getManualTimingAudioSignature,
  getManualTimingPhrasesForScene,
  hydrateManualTimingScenesWithStoryBlocks,
  isManualTimingAiRequestJson,
  normalizeManualTimingAudio,
  normalizeManualTimingAudioPhrases,
  normalizeManualTimingMarkers,
  normalizeManualTimingWorkflow,
  normalizeManualTimingProjectFromJson,
  pickManualTimingProjectStoryBibleFields,
  normalizeManualTimingRoute,
  normalizeManualTimingSourcePhraseIds,
  readManualTimingProjectForNode,
  normalizeManualTimingStoryBlocks,
  persistManualTimingProject,
  repairManualTimingSourcePhraseIdsFromTiming,
  roundTimingSec,
  updateManualTimingSceneById,
  validateManualTimingBlockStoryboardPassImport,
  validateManualTimingClipPassImport,
  validateManualTimingPodcastPassImport,
  validateManualTimingPassResultActivation,
  validateManualTimingStoryBiblePassImport,
  validateManualTimingStoryPassImport,
  validateSceneCoverage,
} from "../clip_nodes/manual_timing/manualTimingDomain";

const SECTION_LABELS = {
  intro: "вступление",
  verse: "куплет",
  chorus: "припев",
  bridge: "бридж",
  instrumental: "проигрыш",
  outro: "финал",
};

const ROUTE_LABELS = {
  ia2v: "ia2v / lip-sync",
  i2v: "i2v / видео",
  i2v_sound: "i2v_sound / звук",
  i2v_text: "i2v_text / речь",
};

const ENERGY_LABELS = {
  soft: "мягко",
  mid: "средне",
  high: "сильно",
};

const STATUS_LABELS = {
  empty: "пусто",
  draft: "черновик",
  confirmed: "подтверждено",
};

const NUDGE_STEPS = [-0.2, -0.1, -0.05, -0.02, 0.02, 0.05, 0.1, 0.2];
const MANUAL_TIMING_NUDGE_DEFAULT_STEP_SEC = 0.5;
const MANUAL_TIMING_INSERT_SILENCE_SEC = 0.5;
const MANUAL_TIMING_MAX_SILENCE_SEC = 30;
const MANUAL_TIMING_TIMELINE_MIN_WIDTH_PX = 1800;
const MANUAL_TIMING_TIMELINE_PIXELS_PER_SECOND = 8;
const SHOW_MISSING_PHRASE_TOOLS = false;
const STORYBOARD_ROUTE = "/studio/storyboard";

const MANUAL_TIMING_AI_REQUEST_JSON_MESSAGE = "Это JSON-задание для AI, а не заполненный результат. Отправьте этот файл в ChatGPT/Gemini, затем вставьте ответ с manual_timing_pass_result.activation_phrase.";
const MANUAL_TIMING_RU_TTS_EMPTY_TEXT = "перевод пока не заполнен";

function speakManualTimingRuText(text = "") {
  const value = String(text || "").trim();
  if (!value || value === MANUAL_TIMING_RU_TTS_EMPTY_TEXT) return;

  try {
    window.speechSynthesis.cancel();
    const utterance = new SpeechSynthesisUtterance(value);
    utterance.lang = "ru-RU";
    utterance.rate = 0.92;
    utterance.pitch = 1;
    utterance.volume = 1;
    window.speechSynthesis.speak(utterance);
  } catch (error) {
    console.warn("[MANUAL TIMING RU_TTS_FAILED]", error);
  }
}

function stopManualTimingSpeech() {
  try {
    window.speechSynthesis.cancel();
  } catch {}
}

function formatManualBoardUpdatedAt(value) {
  if (!value) return "неизвестно";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ru-RU");
}


const MANUAL_TIMING_CURRENT_PROJECT_BACKUP_SOURCE = "manual_timing_editor_current_project";
const MANUAL_TIMING_CURRENT_PROJECT_BACKUP_EXTRA_KEYS = [
  "project_story_summary_ru",
  "project_visual_bible_ru",
  "project_continuity_rules_ru",
  "characters",
  "locations",
  "visual_style",
  "recurring_symbols",
  "do_not_change_rules",
];

const STORY_PASS_REQUIRED_SCENE_FIELDS = [
  "translated_text_ru",
  "meaning_hint_ru",
  "scene_goal_ru",
  "photo_prompt_hint_ru",
  "prompt_hint_ru",
  "scene_role_in_block_ru",
  "block_progress_ru",
];

const SEGMENT_COLORS = [
  "#37d6c2",
  "#6aa9ff",
  "#b88cff",
  "#7bd86a",
  "#ffb15c",
  "#ff7ab6",
  "#5ee0ff",
  "#d3e85a",
];

const STORY_BLOCK_COLORS = SEGMENT_COLORS;



const MANUAL_NEW_BOARD_IMAGE_FIELDS = [
  "image_url",
  "imageUrl",
  "image_preview_url",
  "imagePreviewUrl",
  "generated_image_url",
  "generatedImageUrl",
  "start_image_url",
  "startImageUrl",
  "start_image_preview_url",
  "startImagePreviewUrl",
  "end_image_url",
  "endImageUrl",
  "end_image_preview_url",
  "endImagePreviewUrl",
];

const MANUAL_NEW_BOARD_VIDEO_FIELDS = [
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
  "video_job_id",
  "videoJobId",
  "video_has_audio",
  "hasAudio",
  "videoHasAudio",
  "video_request_payload_preview",
  "videoRequestPayloadPreview",
];

const MANUAL_NEW_BOARD_MMAUDIO_FIELDS = [
  "mmaudio_video_url",
  "mmaudioVideoUrl",
  "mmaudio_raw_video_url",
  "mmaudioRawVideoUrl",
  "mmaudio_source_video_url",
  "mmaudioSourceVideoUrl",
  "original_video_before_mmaudio_url",
  "originalVideoBeforeMMAudioUrl",
  "mmaudio_job_id",
  "mmaudioJobId",
  "mmaudio_status",
  "mmaudio_error",
  "mmaudio_gain_status",
  "mmaudio_gain_error",
];

const MANUAL_NEW_BOARD_PROMPT_FIELDS = [
  "photo_prompt",
  "photoPrompt",
  "image_prompt",
  "imagePrompt",
  "video_prompt",
  "videoPrompt",
  "negative_prompt",
  "negativePrompt",
  "sound_prompt",
  "soundPrompt",
  "mmaudio_prompt",
  "mmaudioPrompt",
  "mmaudio_negative_prompt",
  "mmaudioNegativePrompt",
  "negative_audio_prompt",
  "negativeAudioPrompt",
  "speech_text",
  "speechText",
  "voice_profile",
  "voiceProfile",
  "delivery_style",
  "deliveryStyle",
  "ambient_sound_prompt",
  "ambientSoundPrompt",
  "source_image_prompt_en",
  "source_image_prompt_ru",
  "source_image_negative_prompt_en",
  "i2v_prompt_en",
  "i2v_negative_prompt_en",
  "composition_ru",
  "camera_angle_ru",
  "subject_lock_ru",
  "background_lock_ru",
  "scene_global_context_ru",
  "continuity_anchor_ru",
  "storyboard_frame_role_ru",
];

const MANUAL_NEW_BOARD_CLEARED_SCENE_FIELDS = [
  ...MANUAL_NEW_BOARD_IMAGE_FIELDS,
  ...MANUAL_NEW_BOARD_VIDEO_FIELDS,
  ...MANUAL_NEW_BOARD_MMAUDIO_FIELDS,
  ...MANUAL_NEW_BOARD_PROMPT_FIELDS,
];

const MANUAL_NEW_BOARD_TIMING_STORY_FIELDS = [
  "scene_id",
  "id",
  "index",
  "start_sec",
  "end_sec",
  "duration_sec",
  "speech_start_sec",
  "speech_end_sec",
  "route",
  "section",
  "energy",
  "contains_vocal",
  "source_phrase_ids",
  "story_block_id",
  "story_block_title_ru",
  "story_block_position_ru",
  "story_block_index",
  "story_block_order",
  "story_block_range_ru",
  "story_block_summary_ru",
  "song_block_id",
  "topic_block_id",
  "scene_type",
  "speaker_id",
  "speaker_name",
  "text",
  "source_text",
  "source_text_ru",
  "source_text_en",
  "translated_text_ru",
  "translation_ru",
  "meaning_hint_ru",
  "meaning_ru",
  "semantic_summary_ru",
  "scene_goal_ru",
  "scene_role_in_block_ru",
  "block_progress_ru",
  "photo_prompt_hint_ru",
  "prompt_hint_ru",
  "format",
  "aspect_ratio",
  "format_locked",
  "audio_slice_url",
  "audio_slice_duration_sec",
];

const MANUAL_NEW_BOARD_TIMING_STORY_FIELD_SET = new Set(MANUAL_NEW_BOARD_TIMING_STORY_FIELDS);

function hasManualNewBoardFieldValue(scene = {}, field = "") {
  if (!scene || !Object.prototype.hasOwnProperty.call(scene, field)) return false;
  const value = scene[field];
  if (value === null || value === undefined) return false;
  if (typeof value === "boolean") return value === true;
  if (typeof value === "number") return Number.isFinite(value) && value !== 0;
  if (typeof value === "string") return value.trim().length > 0;
  if (Array.isArray(value)) return value.length > 0;
  if (typeof value === "object") return Object.keys(value).length > 0;
  return Boolean(value);
}

function sceneHasAnyManualNewBoardField(scene = {}, fields = []) {
  return fields.some((field) => hasManualNewBoardFieldValue(scene, field));
}

function getManualNewBoardCleanSceneStats(scenes = []) {
  const safeScenes = Array.isArray(scenes) ? scenes : [];
  return {
    sceneCount: safeScenes.length,
    clearedImageCount: safeScenes.filter((scene) => sceneHasAnyManualNewBoardField(scene, MANUAL_NEW_BOARD_IMAGE_FIELDS)).length,
    clearedVideoCount: safeScenes.filter((scene) => sceneHasAnyManualNewBoardField(scene, MANUAL_NEW_BOARD_VIDEO_FIELDS)).length,
    clearedMMAudioCount: safeScenes.filter((scene) => sceneHasAnyManualNewBoardField(scene, MANUAL_NEW_BOARD_MMAUDIO_FIELDS)).length,
    clearedPromptCount: safeScenes.filter((scene) => sceneHasAnyManualNewBoardField(scene, MANUAL_NEW_BOARD_PROMPT_FIELDS)).length,
  };
}

function sanitizeManualTimingSceneForNewBoard(scene = {}, projectFormat = "9:16") {
  const sourceScene = scene && typeof scene === "object" ? scene : {};
  const cleanScene = {};
  Object.entries(sourceScene).forEach(([key, value]) => {
    if (MANUAL_NEW_BOARD_CLEARED_SCENE_FIELDS.includes(key)) return;
    if (MANUAL_NEW_BOARD_TIMING_STORY_FIELD_SET.has(key) || /(^|_)(text|translation|meaning|summary)($|_)/i.test(key)) {
      cleanScene[key] = value;
    }
  });

  const safeFormat = String(projectFormat || sourceScene.format || sourceScene.aspect_ratio || "9:16");
  cleanScene.scene_id = String(cleanScene.scene_id || sourceScene.scene_id || sourceScene.id || "");
  cleanScene.index = Number.isFinite(Number(cleanScene.index ?? sourceScene.index)) ? Number(cleanScene.index ?? sourceScene.index) : 0;
  cleanScene.start_sec = Number(cleanScene.start_sec ?? sourceScene.start_sec ?? 0) || 0;
  cleanScene.end_sec = Number(cleanScene.end_sec ?? sourceScene.end_sec ?? cleanScene.start_sec ?? 0) || 0;
  cleanScene.duration_sec = Number(cleanScene.duration_sec ?? sourceScene.duration_sec ?? Math.max(0, cleanScene.end_sec - cleanScene.start_sec)) || 0;
  cleanScene.route = String(cleanScene.route || sourceScene.route || "i2v");
  cleanScene.source_phrase_ids = normalizeManualTimingSourcePhraseIds(cleanScene.source_phrase_ids || sourceScene.source_phrase_ids || sourceScene.sourcePhraseIds);
  cleanScene.format = safeFormat;
  cleanScene.aspect_ratio = safeFormat;
  cleanScene.format_locked = true;
  cleanScene.status = "draft";
  cleanScene.image_upload_status = "";
  cleanScene.image_upload_error = "";
  cleanScene.video_error = "";
  cleanScene.error = "";

  MANUAL_NEW_BOARD_CLEARED_SCENE_FIELDS.forEach((field) => {
    cleanScene[field] = "";
  });
  cleanScene.video_has_audio = false;
  cleanScene.hasAudio = false;
  cleanScene.videoHasAudio = false;

  return cleanScene;
}


const MANUAL_NEW_BOARD_ROOT_DROP_KEY_RE = /(selectedscene|selected_scene|image|photo|preview|video|media|mmaudio|generated|asset|dataurl|data_url)/i;
const MANUAL_NEW_BOARD_FORBIDDEN_MEDIA_VALUE_RE = /^(data:image|data:video|blob:)|\/static\/assets|\.(png|jpe?g|webp|gif|mp4|mov|webm)(?:[?#]|$)/i;

function isManualNewBoardAllowedAudioPath(path = "", key = "", value = "") {
  const safePath = String(path || "");
  const safeKey = String(key || "");
  const text = String(value || "").trim();
  if (/^(data:image|data:video|blob:)/i.test(text)) return false;
  if (safePath === "project.audio.url" || safePath === "project.audio_metadata.url") return true;
  if (/\.audio_phrases\[\d+\]/.test(safePath)) return true;
  if (safeKey === "audio_slice_url" && /\.(mp3|wav|m4a|aac|ogg)(?:[?#]|$)/i.test(text)) return true;
  return false;
}

function deepSanitizeManualNewBoardValue(value, path = "project", depth = 0) {
  if (value === null || value === undefined) return value;
  if (typeof value === "string") {
    return MANUAL_NEW_BOARD_FORBIDDEN_MEDIA_VALUE_RE.test(value) ? "" : value;
  }
  if (typeof value !== "object") return value;
  if (depth > 8) return undefined;
  if (Array.isArray(value)) {
    return value
      .map((item, index) => deepSanitizeManualNewBoardValue(item, `${path}[${index}]`, depth + 1))
      .filter((item) => item !== undefined);
  }
  const clean = {};
  Object.entries(value).forEach(([key, child]) => {
    const childPath = `${path}.${key}`;
    const keyMatchesDrop = MANUAL_NEW_BOARD_ROOT_DROP_KEY_RE.test(String(key || ""));
    if (keyMatchesDrop && !isManualNewBoardAllowedAudioPath(childPath, key, child)) return;
    const sanitized = deepSanitizeManualNewBoardValue(child, childPath, depth + 1);
    if (sanitized === undefined) return;
    if (typeof sanitized === "string" && keyMatchesDrop && sanitized.trim()) return;
    clean[key] = sanitized;
  });
  return clean;
}

function sanitizeManualNewBoardStoryBlock(block = {}, index = 0) {
  return deepSanitizeManualNewBoardValue(block && typeof block === "object" ? block : {}, `project.story_blocks[${index}]`, 0) || {};
}

function sanitizeManualNewBoardProject(projectSnapshot = {}) {
  const sourceProject = projectSnapshot && typeof projectSnapshot === "object" ? projectSnapshot : {};
  const projectFormat = String(sourceProject.format || sourceProject.aspect_ratio || "9:16");
  const cleanRoot = deepSanitizeManualNewBoardValue(sourceProject, "project", 0) || {};
  const cleanScenes = Array.isArray(sourceProject.scenes)
    ? sourceProject.scenes.map((scene) => sanitizeManualTimingSceneForNewBoard(scene, projectFormat))
    : [];
  const cleanStoryBlocks = Array.isArray(sourceProject.story_blocks)
    ? sourceProject.story_blocks.map((block, index) => sanitizeManualNewBoardStoryBlock(block, index))
    : [];
  return {
    ...cleanRoot,
    format: projectFormat,
    aspect_ratio: projectFormat,
    format_locked: true,
    story_blocks: cleanStoryBlocks,
    scenes: cleanScenes,
    selectedScene: null,
    selectedSceneId: cleanScenes[0]?.scene_id || "",
  };
}

function readActiveProject() {
  try {
    const raw = localStorage.getItem(getAccountScopedStorageKey(MANUAL_TIMING_ACTIVE_PROJECT_KEY))
      || (canUseLegacyManualProjectStorage() ? localStorage.getItem(MANUAL_TIMING_ACTIVE_PROJECT_KEY) : null);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function getEmptyManualTimingAudio() {
  return { url: "", filename: "", name: "", duration_sec: 0, duration_ms: 0 };
}

function normalizeManualTimingProjectAudioForHandoff(project = {}, stateAudio = null) {
  const candidates = [
    stateAudio,
    project?.audio,
    project?.uploadedAudio,
    project?.uploaded_audio,
    project?.sourceAudio,
    project?.source_audio,
    project?.sourceNodeAudio,
    project?.source_node_audio,
    {
      url: project?.audio_url || project?.audioUrl || project?.source_audio_url || project?.sourceAudioUrl,
      filename: project?.audio_name || project?.audioName || project?.audio_filename || project?.audioFilename,
      name: project?.audio_name || project?.audioName || project?.audio_filename || project?.audioFilename,
      duration_sec: project?.audio_duration_sec || project?.audioDurationSec || project?.duration_sec || project?.durationSec,
      duration_ms: project?.audio_duration_ms || project?.audioDurationMs,
    },
  ];

  for (const candidate of candidates) {
    const normalized = normalizeManualTimingAudio(candidate);
    if (!normalized.url) continue;
    const name = String(
      candidate?.name
      || candidate?.filename
      || candidate?.fileName
      || candidate?.original_filename
      || candidate?.originalFilename
      || normalized.filename
      || ""
    ).trim();
    return {
      url: normalized.url,
      name,
      filename: normalized.filename || name,
      duration_sec: Number(normalized.duration_sec || 0) || 0,
      duration_ms: Number(normalized.duration_ms || 0) || Math.round((Number(normalized.duration_sec || 0) || 0) * 1000),
    };
  }

  return getEmptyManualTimingAudio();
}

function applyManualTimingProjectAudioCompat(projectSnapshot = {}, handoffAudio = getEmptyManualTimingAudio()) {
  const audio = normalizeManualTimingProjectAudioForHandoff(projectSnapshot, handoffAudio);
  const audioName = String(audio.name || audio.filename || "").trim();
  return {
    ...(projectSnapshot || {}),
    audio: {
      ...audio,
      name: audioName,
      filename: audio.filename || audioName,
    },
    audio_metadata: {
      ...((projectSnapshot || {}).audio_metadata || {}),
      ...audio,
      name: audioName,
      filename: audio.filename || audioName,
    },
    audio_url: audio.url,
    audioUrl: audio.url,
    audio_name: audioName,
    audio_duration_sec: Number(audio.duration_sec || 0) || 0,
  };
}

async function uploadManualTimingAudioAsset(file) {
  const fd = new FormData();
  fd.append("file", file);

  const res = await fetch(`${API_BASE}/api/assets/upload`, {
    method: "POST",
    body: fd,
    credentials: "include",
  });
  if (!res.ok) {
    let message = `upload_failed:${res.status}`;
    try {
      const data = await res.json();
      message = data?.detail || data?.message || message;
    } catch {
      try {
        message = await res.text() || message;
      } catch {
        // keep default upload message
      }
    }
    throw new Error(message);
  }
  return await res.json();
}

function readAudioFileMetadata(file) {
  return new Promise((resolve) => {
    if (!file) {
      resolve({ duration_sec: 0, duration_ms: 0 });
      return;
    }

    const url = URL.createObjectURL(file);
    const audioEl = new Audio();
    let settled = false;

    const finish = (durationSec = 0) => {
      if (settled) return;
      settled = true;
      const safeDurationSec = Number.isFinite(Number(durationSec)) && Number(durationSec) > 0
        ? Number(Number(durationSec).toFixed(3))
        : 0;
      try {
        audioEl.removeAttribute("src");
        audioEl.load();
      } catch {
        // ignore metadata cleanup errors
      }
      URL.revokeObjectURL(url);
      resolve({
        duration_sec: safeDurationSec,
        duration_ms: safeDurationSec > 0 ? Math.round(safeDurationSec * 1000) : 0,
      });
    };

    audioEl.preload = "metadata";
    audioEl.onloadedmetadata = () => finish(audioEl.duration || 0);
    audioEl.onerror = () => finish(0);
    audioEl.src = url;
  });
}

function buildManualTimingProjectForAudioChange(baseProject = {}, nextAudio = getEmptyManualTimingAudio(), audioSource = "") {
  const safeAudio = normalizeManualTimingAudio(nextAudio);
  const durationSec = Number(safeAudio.duration_sec || 0);
  const hasAudio = Boolean(safeAudio.url);
  const markers = hasAudio && durationSec > 0 ? [0, durationSec] : [];
  const storyBlocks = [MANUAL_TIMING_UNKNOWN_STORY_BLOCK];
  const scenes = markers.length
    ? hydrateManualTimingScenesWithStoryBlocks(buildManualTimingScenesFromMarkers(markers, [], { durationSec }), storyBlocks)
    : [];
  return {
    ...getDefaultManualTimingNodeData(),
    nodeId: String(baseProject?.nodeId || ""),
    sourceNodeId: String(baseProject?.sourceNodeId || baseProject?.nodeId || ""),
    project_mode: String(baseProject?.project_mode || ""),
    project_kind: String(baseProject?.project_kind || ""),
    format: String(baseProject?.format || baseProject?.aspect_ratio || "9:16"),
    aspect_ratio: String(baseProject?.aspect_ratio || baseProject?.format || "9:16"),
    format_locked: false,
    audio: safeAudio,
    audio_source: audioSource,
    markers,
    story_blocks: storyBlocks,
    audio_phrases: [],
    audio_words: [],
    asr_phrase_map: null,
    scenes,
    selectedSceneId: scenes[0]?.scene_id || "",
    timing_status: hasAudio ? "draft" : "empty",
  };
}

function normalizeStoredManualTimingProject(raw = null, ownerNodeId = "") {
  const project = { ...getDefaultManualTimingNodeData(), ...(raw || {}) };
  const audio = normalizeManualTimingProjectAudioForHandoff(project);
  const duration = Number(audio.duration_sec || 0);
  const markers = duration > 0
    ? normalizeManualTimingMarkers(project.markers?.length ? project.markers : [0, duration], duration)
    : [];
  const story_blocks = normalizeManualTimingStoryBlocks(project.story_blocks);
  const audio_phrases = normalizeManualTimingAudioPhrases(project.audio_phrases);
  const rawScenes = markers.length >= 2
    ? buildManualTimingScenesFromMarkers(markers, project.scenes || [], { durationSec: duration })
    : (Array.isArray(project.scenes) ? project.scenes : []);
  const scenes = hydrateManualTimingScenesWithStoryBlocks(rawScenes, story_blocks);
  const safeOwnerNodeId = String(ownerNodeId || project.sourceNodeId || project.nodeId || "").trim();
  return {
    ...project,
    ...(safeOwnerNodeId ? { nodeId: safeOwnerNodeId, sourceNodeId: safeOwnerNodeId } : {}),
    project_mode: project.project_mode || "",
    project_kind: project.project_kind || "",
    format: String(project.format || project.aspect_ratio || "9:16"),
    aspect_ratio: String(project.aspect_ratio || project.format || "9:16"),
    format_locked: Boolean(project.format_locked),
    audio,
    markers,
    story_blocks,
    audio_phrases,
    scenes,
    selectedSceneId: project.selectedSceneId || scenes[0]?.scene_id || "",
    timing_status: project.timing_status || (scenes.length ? "draft" : "empty"),
    manual_scene_edits: Boolean(project.manual_scene_edits ?? project.manualSceneEdits),
    manualSceneEdits: Boolean(project.manualSceneEdits ?? project.manual_scene_edits),
    lastManualEditReason: String(project.lastManualEditReason || project.last_manual_edit_reason || ""),
  };
}

function buildInitialProject() {
  return normalizeStoredManualTimingProject(readActiveProject());
}

function getManualTimingOwnerNodeId(project = {}) {
  const explicit = String(project?.sourceNodeId || project?.nodeId || "").trim();
  if (explicit) return explicit;
  const mode = String(project?.project_mode || project?.projectMode || "story_voiceover").trim() || "story_voiceover";
  return `manual_timing_standalone_${mode}`;
}

function getManualProjectOwnerId(project = {}) {
  return String(project?.sourceNodeId || project?.nodeId || "").trim();
}

function getManualTimingBoardForOwner(ownerNodeId = "") {
  const safeOwnerNodeId = String(ownerNodeId || "").trim();
  const nodeBoard = readManualClipBoardProjectForNode(safeOwnerNodeId);
  if (hasMeaningfulManualProject(nodeBoard)) return nodeBoard;
  const activeBoard = readActiveManualClipBoardProject();
  return getManualProjectOwnerId(activeBoard) === safeOwnerNodeId ? activeBoard : null;
}

function getManualTimingRouteSourceNodeId(location = {}, project = {}) {
  const stateSourceNodeId = String(location?.state?.sourceNodeId || "").trim();
  const querySourceNodeId = (() => {
    try {
      return String(new URLSearchParams(location?.search || "").get("sourceNodeId") || "").trim();
    } catch {
      return "";
    }
  })();
  const projectSourceNodeId = String(project?.sourceNodeId || project?.nodeId || "").trim();
  return stateSourceNodeId || querySourceNodeId || projectSourceNodeId;
}

function resolveManualTimingOwnerNode(location = {}, project = {}) {
  const routeSourceNodeId = getManualTimingRouteSourceNodeId(location, project);
  const projectSourceNodeId = String(project?.sourceNodeId || project?.nodeId || "").trim();
  const fallbackOwnerNodeId = getManualTimingOwnerNodeId(project);
  const finalOwnerNodeId = routeSourceNodeId || projectSourceNodeId || fallbackOwnerNodeId;
  return {
    routeSourceNodeId,
    projectSourceNodeId,
    fallbackOwnerNodeId,
    finalOwnerNodeId,
  };
}

function getManualBoardIdentityParts(project = {}) {
  return {
    projectId: String(project?.project_id || project?.projectId || "").trim(),
    inputSignature: String(project?.input_signature || project?.inputSignature || "").trim(),
    audioSignature: String(project?.audio_signature || project?.audioSignature || "").trim(),
    storySignature: String(project?.story_signature || project?.storySignature || "").trim(),
  };
}

function manualBoardIdentityChanged(oldBoard = {}, newBoard = {}) {
  if (!hasMeaningfulManualProject(oldBoard) || !hasMeaningfulManualProject(newBoard)) return false;
  const oldIdentity = getManualBoardIdentityParts(oldBoard);
  const newIdentity = getManualBoardIdentityParts(newBoard);
  return ["projectId", "inputSignature", "audioSignature", "storySignature"].some((key) => (
    oldIdentity[key] && newIdentity[key] && oldIdentity[key] !== newIdentity[key]
  ));
}

function parseManualTimingAudioSignature(signature = "") {
  try {
    return JSON.parse(String(signature || ""));
  } catch {
    return {};
  }
}

function manualTimingAudioSignaturesDiffer(a = "", b = "") {
  const left = String(a || "").trim();
  const right = String(b || "").trim();
  if (!left || !right) return false;
  return left !== right;
}

function getManualTimingAudioNameForDiagnostics(projectOrAudio = {}) {
  const parsed = parseManualTimingAudioSignature(getManualTimingAudioSignature(projectOrAudio));
  return String(parsed.audio_name || "").trim();
}

function getManualTimingAudioDurationForDiagnostics(projectOrAudio = {}) {
  const parsed = parseManualTimingAudioSignature(getManualTimingAudioSignature(projectOrAudio));
  return Number(parsed.audio_duration_sec || 0) || 0;
}

function getIncomingManualTimingProjectFromLocation(location = {}, fallbackProject = {}, ownerNodeId = "") {
  const navState = location?.state && typeof location.state === "object" ? location.state : {};
  const rawProject = navState.project || navState.manualTimingProject || navState.timingProject || {};
  const incomingAudio = normalizeManualTimingProjectAudioForHandoff(rawProject, navState.audio || navState.audio_metadata || navState.audioMetadata);
  const hasIncomingAudio = Boolean(incomingAudio.url) && (
    navState.fromPodcastComposer === true
    || navState.replaceAudio === true
    || Boolean(navState.audio)
    || Boolean(navState.audio_metadata)
    || Boolean(navState.audioMetadata)
    || Boolean(rawProject?.audio)
  );
  if (!hasIncomingAudio) return null;
  const safeOwnerNodeId = String(ownerNodeId || navState.sourceNodeId || rawProject.sourceNodeId || rawProject.nodeId || fallbackProject.sourceNodeId || fallbackProject.nodeId || "").trim();
  const rawProjectMode = String(rawProject.project_mode || rawProject.projectMode || "").trim();
  const navProjectMode = String(navState.project_mode || navState.projectMode || "").trim();
  const fallbackProjectMode = String(fallbackProject.project_mode || fallbackProject.projectMode || "").trim();
  const rawProjectKind = String(rawProject.project_kind || rawProject.projectKind || "").trim();
  const navProjectKind = String(navState.project_kind || navState.projectKind || "").trim();
  const fallbackProjectKind = String(fallbackProject.project_kind || fallbackProject.projectKind || "").trim();
  const explicitProjectMode = rawProjectMode || navProjectMode || fallbackProjectMode;
  const explicitProjectKind = rawProjectKind || navProjectKind || fallbackProjectKind;
  const isExplicitPodcast = (
    explicitProjectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE
    || explicitProjectKind === "podcast"
    || navState.forcePodcastMode === true
  );
  const projectMode = explicitProjectMode || (isExplicitPodcast ? MANUAL_TIMING_PODCAST_DIALOGUE_MODE : MANUAL_TIMING_STORY_VOICEOVER_MODE);
  const projectKind = explicitProjectKind || (projectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE ? "podcast" : MANUAL_TIMING_STORY_PROJECT_KIND);
  console.info("[MANUAL TIMING PROJECT_MODE_RESOLVED]", {
    fromPodcastComposer: navState.fromPodcastComposer === true,
    rawProjectMode,
    navProjectMode,
    fallbackProjectMode,
    resolvedProjectMode: projectMode,
    resolvedProjectKind: projectKind,
    isExplicitPodcast,
  });
  const format = String(rawProject.format || rawProject.aspect_ratio || navState.format || navState.aspect_ratio || fallbackProject.format || fallbackProject.aspect_ratio || "9:16").trim();
  const baseProject = {
    ...fallbackProject,
    ...rawProject,
    nodeId: safeOwnerNodeId,
    sourceNodeId: safeOwnerNodeId,
    project_mode: projectMode,
    project_kind: projectKind,
    format,
    aspect_ratio: String(rawProject.aspect_ratio || rawProject.format || navState.aspect_ratio || navState.format || fallbackProject.aspect_ratio || fallbackProject.format || format || "9:16"),
  };
  const nextProject = buildManualTimingProjectForAudioChange(baseProject, incomingAudio, navState.fromPodcastComposer ? "podcast_audio_composer" : "incoming_audio");
  const shouldPreserveStoryTiming = navState.fromPodcastComposer === true && projectMode !== MANUAL_TIMING_PODCAST_DIALOGUE_MODE;
  if (shouldPreserveStoryTiming) {
    nextProject.markers = Array.isArray(baseProject.markers) ? baseProject.markers : nextProject.markers;
    nextProject.scenes = Array.isArray(baseProject.scenes) ? baseProject.scenes : nextProject.scenes;
    nextProject.story_blocks = Array.isArray(baseProject.story_blocks) ? baseProject.story_blocks : nextProject.story_blocks;
    nextProject.audio_phrases = Array.isArray(baseProject.audio_phrases) ? baseProject.audio_phrases : nextProject.audio_phrases;
    nextProject.selectedSceneId = baseProject.selectedSceneId || nextProject.selectedSceneId;
  }
  const incomingSignature = getManualTimingAudioSignature(nextProject);
  const projectId = String(rawProject.project_id || rawProject.projectId || "").trim()
    || `manual_timing_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
  return {
    ...nextProject,
    audio_metadata: {
      ...(rawProject.audio_metadata || rawProject.audioMetadata || {}),
      ...incomingAudio,
      name: incomingAudio.name || incomingAudio.filename || "",
      filename: incomingAudio.filename || incomingAudio.name || "",
    },
    audio_duration_sec: Number(incomingAudio.duration_sec || 0) || 0,
    project_id: projectId,
    projectId,
    input_signature: String(rawProject.input_signature || rawProject.inputSignature || incomingSignature),
    inputSignature: String(rawProject.inputSignature || rawProject.input_signature || incomingSignature),
    audio_signature: incomingSignature,
    audioSignature: incomingSignature,
    podcast_edit_manifest: navState.podcast_edit_manifest || rawProject.podcast_edit_manifest || rawProject.composer_edit_manifest || null,
    composer_edit_manifest: navState.composer_edit_manifest || navState.podcast_edit_manifest || rawProject.composer_edit_manifest || rawProject.podcast_edit_manifest || null,
  };
}

function getManualTimingBoardForOwnerMatchingProject(ownerNodeId = "", referenceProject = null, { logStale = false } = {}) {
  const board = getManualTimingBoardForOwner(ownerNodeId);
  if (!hasMeaningfulManualProject(board) || !referenceProject) return board;
  const referenceSignature = getManualTimingAudioSignature(referenceProject);
  const boardSignature = getManualTimingAudioSignature(board);
  if (!manualTimingAudioSignaturesDiffer(referenceSignature, boardSignature)) return board;
  if (logStale) {
    console.info("[MANUAL TIMING STALE_ACTIVE_BOARD_IGNORED]", {
      reason: "audio_signature_changed",
      incomingAudioDurationSec: getManualTimingAudioDurationForDiagnostics(referenceProject),
      storedAudioDurationSec: getManualTimingAudioDurationForDiagnostics(board),
      incomingAudioName: getManualTimingAudioNameForDiagnostics(referenceProject),
      storedAudioName: getManualTimingAudioNameForDiagnostics(board),
    });
  }
  return null;
}

function validateManualTimingBackupMatchesCurrentProject(exportProject = {}, currentProject = {}) {
  const exportPayload = buildManualProjectBackupJson(exportProject, { source: "manual_timing_editor_validation" });
  const currentSignature = parseManualTimingAudioSignature(getManualTimingAudioSignature(currentProject));
  const exportSignature = parseManualTimingAudioSignature(getManualTimingAudioSignature(exportPayload));
  const durationMatches = Math.abs(Number(currentSignature.audio_duration_sec || 0) - Number(exportSignature.audio_duration_sec || 0)) <= 0.001;
  const currentAudioRef = String(currentSignature.audio_url || currentSignature.audio_name || "").trim();
  const exportAudioRef = String(exportSignature.audio_url || exportSignature.audio_name || "").trim();
  const audioMatches = !currentAudioRef || !exportAudioRef || currentAudioRef === exportAudioRef;
  return {
    ok: durationMatches && audioMatches,
    payload: exportPayload,
    currentAudioDurationSec: Number(currentSignature.audio_duration_sec || 0) || 0,
    exportAudioDurationSec: Number(exportSignature.audio_duration_sec || 0) || 0,
    currentAudioName: String(currentSignature.audio_name || currentSignature.audio_url || ""),
    exportAudioName: String(exportSignature.audio_name || exportSignature.audio_url || ""),
  };
}

function getManualTimingCurrentProjectAudioDuration(project = {}) {
  const audio = normalizeManualTimingAudio(project?.audio);
  return Number(project?.audio_duration_sec || audio.duration_sec || project?.audioDurationSec || 0) || 0;
}

function getManualTimingCurrentProjectAudioName(project = {}) {
  const audio = normalizeManualTimingAudio(project?.audio);
  const metadata = project?.audio_metadata || project?.audioMetadata || {};
  return String(
    audio.name
    || audio.filename
    || metadata.name
    || metadata.filename
    || project?.audio_name
    || project?.audioName
    || project?.audio_filename
    || project?.audioFilename
    || "manual_timing"
  ).trim() || "manual_timing";
}

function sanitizeManualTimingBackupFilenamePart(value = "manual_timing", maxLength = 48) {
  const safe = String(value || "manual_timing")
    .replace(/\.[a-z0-9]{2,5}$/i, "")
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, " ")
    .replace(/\s+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^[_ .-]+|[_ .-]+$/g, "")
    .slice(0, maxLength)
    .replace(/^[_ .-]+|[_ .-]+$/g, "");
  return safe || "manual_timing";
}

function sanitizeManualTimingPassFilenamePart(value = "project", maxLength = 48) {
  const safe = String(value || "project")
    .replace(/\.[a-z0-9]{2,5}$/i, "")
    .replace(/[^a-z0-9а-яё]+/gi, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, maxLength)
    .replace(/^_+|_+$/g, "")
    .toLowerCase();
  return safe || "project";
}

function formatManualTimingBackupTimestamp(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}_${pad(date.getHours())}${pad(date.getMinutes())}`;
}

function getManualTimingDownloadPassType(passType = "") {
  if (passType === "music_clip") return "clip_pass";
  if (passType === "podcast_dialogue") return "podcast_pass";
  return sanitizeManualTimingPassFilenamePart(passType || "pass", 48);
}

function buildManualTimingPassDownloadFilename(passType = "", project = {}) {
  const safePassType = getManualTimingDownloadPassType(passType);
  const audioName = sanitizeManualTimingPassFilenamePart(getManualTimingCurrentProjectAudioName(project), 48);
  const durationSec = Math.max(0, Math.round(getManualTimingCurrentProjectAudioDuration(project)));
  return `manual_timing_${safePassType}_${audioName}_${durationSec}s_${formatManualTimingBackupTimestamp(new Date())}.json`;
}

function buildManualTimingCurrentProjectBackupFilename(project = {}, createdAt = new Date()) {
  const audioName = sanitizeManualTimingBackupFilenamePart(getManualTimingCurrentProjectAudioName(project));
  const durationSec = Math.max(0, Math.round(getManualTimingCurrentProjectAudioDuration(project)));
  return `manual_timing_backup_${audioName}_${durationSec}s_${formatManualTimingBackupTimestamp(createdAt)}.json`;
}

function buildManualTimingCurrentProjectBackupPayload(project = {}, createdAt = new Date()) {
  const payload = {
    backup_type: MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE,
    backup_schema_version: 1,
    createdAt: createdAt.toISOString(),
    source: MANUAL_TIMING_CURRENT_PROJECT_BACKUP_SOURCE,
    project_mode: project?.project_mode,
    project_kind: project?.project_kind,
    story_pass_mode: project?.story_pass_mode,
    split_type: project?.split_type,
    format: project?.format,
    aspect_ratio: project?.aspect_ratio,
    format_locked: project?.format_locked,
    timing_status: project?.timing_status,
    manual_timing_workflow: normalizeManualTimingWorkflow(project?.manual_timing_workflow),
    ...pickManualTimingProjectStoryBibleFields(project),
    audio: project?.audio,
    audio_metadata: project?.audio_metadata,
    audio_duration_sec: project?.audio_duration_sec ?? getManualTimingCurrentProjectAudioDuration(project),
    audio_phrases: project?.audio_phrases || [],
    markers: project?.markers || [],
    scenes: project?.scenes || [],
    story_blocks: project?.story_blocks || [],
    song_blocks: project?.song_blocks || [],
    topic_blocks: project?.topic_blocks || [],
    speakers: project?.speakers || [],
    manual_scene_edits: project?.manual_scene_edits,
    manualSceneEdits: project?.manualSceneEdits,
    lastManualEditReason: project?.lastManualEditReason,
    selectedSceneId: project?.selectedSceneId,
    project_id: project?.project_id,
    projectId: project?.projectId,
    input_signature: project?.input_signature,
    inputSignature: project?.inputSignature,
    audio_signature: project?.audio_signature,
    audioSignature: project?.audioSignature,
    nodeId: project?.nodeId,
    sourceNodeId: project?.sourceNodeId,
    updatedAt: project?.updatedAt,
  };

  MANUAL_TIMING_CURRENT_PROJECT_BACKUP_EXTRA_KEYS.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(project || {}, key)) payload[key] = project[key];
  });

  return payload;
}

function validateManualTimingCurrentProjectBackupPayload(payload = {}, currentProject = {}, currentState = {}) {
  const currentAudioDurationSec = Number(currentState.audioDurationSec ?? getManualTimingCurrentProjectAudioDuration(currentProject)) || 0;
  const payloadAudioDurationSec = Number(payload?.audio_duration_sec || 0) || 0;
  const currentSceneCount = Number(currentState.sceneCount ?? (Array.isArray(currentProject?.scenes) ? currentProject.scenes.length : 0)) || 0;
  const payloadSceneCount = Array.isArray(payload?.scenes) ? payload.scenes.length : 0;
  const currentMarkerCount = Number(currentState.markerCount ?? (Array.isArray(currentProject?.markers) ? currentProject.markers.length : 0)) || 0;
  const payloadMarkerCount = Array.isArray(payload?.markers) ? payload.markers.length : 0;
  const currentStoryBlockCount = Number(currentState.storyBlockCount ?? normalizeManualTimingStoryBlocks(currentProject?.story_blocks).length) || 0;
  const payloadStoryBlockCount = Array.isArray(payload?.story_blocks) ? payload.story_blocks.length : 0;

  let reason = "";
  if (Math.abs(payloadAudioDurationSec - currentAudioDurationSec) > 0.01) reason = "audio_duration_mismatch";
  else if (payloadSceneCount !== currentSceneCount) reason = "scene_count_mismatch";
  else if (payloadMarkerCount !== currentMarkerCount) reason = "marker_count_mismatch";
  else if (!Array.isArray(payload?.story_blocks)) reason = "story_blocks_missing";

  return {
    ok: !reason,
    reason,
    payloadAudioDurationSec,
    currentAudioDurationSec,
    payloadSceneCount,
    currentSceneCount,
    payloadMarkerCount,
    currentMarkerCount,
    payloadStoryBlockCount,
    currentStoryBlockCount,
  };
}

function downloadManualTimingJsonFile(payload = {}, filename = "manual_timing_ai_pass.json") {
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function downloadManualBoardBackupJson(project = {}, filename = "manual_clip_board_backup.json") {
  const blob = new Blob([JSON.stringify(buildManualProjectBackupJson(project, { source: "manual_timing_editor_new_project_replace" }), null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function dispatchManualTimingDirectorBoardUpdate(project = {}, explicitSourceNodeId = "") {
  const sourceNodeId = String(explicitSourceNodeId || getManualTimingOwnerNodeId(project) || "").trim();
  if (!sourceNodeId || typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("manual-director-board:update", {
    detail: { sourceNodeId, project: { ...project, nodeId: sourceNodeId, sourceNodeId } },
  }));
}

function clampTime(value, duration) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(Number(duration || 0), n));
}


function clampManualTimingStep(value, fallback = MANUAL_TIMING_NUDGE_DEFAULT_STEP_SEC) {
  const number = Number(String(value ?? "").replace(/,/g, "."));
  if (!Number.isFinite(number)) return fallback;
  return roundTimingSec(Math.max(0.01, Math.min(30, Math.abs(number))));
}

function normalizeMarkersForExactDuration(rawMarkers = [], durationSec = 0) {
  const duration = Math.max(0, Number(durationSec || 0));
  if (!(duration > 0)) return [];
  const values = (Array.isArray(rawMarkers) ? rawMarkers : [])
    .map((marker) => roundTimingSec(clampTime(marker, duration)))
    .filter((marker) => Number.isFinite(Number(marker)))
    .sort((a, b) => a - b);
  const unique = [];
  values.forEach((marker) => {
    if (!unique.length || Math.abs(marker - unique[unique.length - 1]) > 0.001) unique.push(marker);
  });
  if (!unique.length || unique[0] > 0.001) unique.unshift(0);
  else unique[0] = 0;
  if (Math.abs(unique[unique.length - 1] - duration) > 0.001) unique.push(roundTimingSec(duration));
  else unique[unique.length - 1] = roundTimingSec(duration);
  return unique;
}

function isManualTimingSilenceScene(scene = {}) {
  return Boolean(scene?.is_silence || scene?.is_virtual_silence || scene?.scene_type === "manual_silence" || scene?.source_kind === "silence");
}

function decorateManualTimingSilenceScene(scene = {}) {
  const start = roundTimingSec(scene?.start_sec);
  const end = roundTimingSec(scene?.end_sec);
  const duration = roundTimingSec(Math.max(0.01, end - start));
  return {
    ...scene,
    scene_type: "manual_silence",
    source_kind: "silence",
    is_silence: true,
    is_virtual_silence: true,
    duration_sec: duration,
    speech_start_sec: start,
    speech_end_sec: end,
    pre_silence_sec: 0,
    post_silence_sec: 0,
    section: "instrumental",
    route: "i2v_sound",
    contains_vocal: false,
    contains_vocal_assumption: false,
    contains_instrumental_assumption: false,
    use_sound_suggestion: true,
    energy: "soft",
    original_text: "[тишина]",
    translated_text_ru: "[тишина]",
    meaning_hint_ru: "Пауза / вставленная тишина.",
    scene_goal_ru: "Техническая пауза тишины.",
    user_note_ru: "Вставленная тишина. Можно выбрать блок и менять его длительность микродоводчиком.",
  };
}


function materializeManualTimingSceneSourceMap(scene = {}) {
  if (isManualTimingSilenceScene(scene)) return decorateManualTimingSilenceScene(scene);

  const start = roundTimingSec(Number(scene.start_sec || 0));
  const end = roundTimingSec(Number(scene.end_sec || start));
  const sourceStart = Number.isFinite(Number(scene.source_start_sec))
    ? roundTimingSec(Number(scene.source_start_sec))
    : start;
  const sourceEnd = Number.isFinite(Number(scene.source_end_sec))
    ? roundTimingSec(Number(scene.source_end_sec))
    : roundTimingSec(sourceStart + Math.max(0, end - start));

  return {
    ...scene,
    source_kind: scene.source_kind || "audio",
    source_start_sec: sourceStart,
    source_end_sec: sourceEnd,
  };
}

function getManualTimingSceneSourceStartSec(scene = {}) {
  if (isManualTimingSilenceScene(scene)) return null;
  const explicit = Number(scene.source_start_sec ?? scene.sourceStartSec);
  if (Number.isFinite(explicit)) return roundTimingSec(explicit);
  return roundTimingSec(scene.start_sec);
}

function getManualTimingSceneSourceEndSec(scene = {}) {
  if (isManualTimingSilenceScene(scene)) return null;
  const explicit = Number(scene.source_end_sec ?? scene.sourceEndSec);
  if (Number.isFinite(explicit)) return roundTimingSec(explicit);
  const sourceStart = getManualTimingSceneSourceStartSec(scene);
  return roundTimingSec(Number(sourceStart || 0) + Math.max(0, Number(scene.end_sec || 0) - Number(scene.start_sec || 0)));
}

function buildManualTimingSilenceBlocksFromScenes(scenes = []) {
  return (Array.isArray(scenes) ? scenes : [])
    .filter((scene) => isManualTimingSilenceScene(scene))
    .map((scene, index) => ({
      id: String(scene.silence_block_id || `silence_${index + 1}_${roundTimingSec(scene.start_sec).toFixed(3)}`),
      start_sec: roundTimingSec(scene.start_sec),
      end_sec: roundTimingSec(scene.end_sec),
      duration_sec: roundTimingSec(Math.max(0.01, Number(scene.end_sec || 0) - Number(scene.start_sec || 0))),
    }));
}


function retimeManualTimingScene(scene = {}, startSec = 0, endSec = 0, extraPatch = {}) {
  const start = roundTimingSec(startSec);
  const end = roundTimingSec(Math.max(start + 0.01, Number(endSec || 0)));
  const oldStart = roundTimingSec(scene?.start_sec);
  const oldEnd = roundTimingSec(scene?.end_sec);
  const safeSpeechStart = Number.isFinite(Number(scene?.speech_start_sec)) ? roundTimingSec(scene.speech_start_sec) : oldStart;
  const safeSpeechEnd = Number.isFinite(Number(scene?.speech_end_sec)) ? roundTimingSec(scene.speech_end_sec) : oldEnd;
  const speechStart = roundTimingSec(Math.max(start, Math.min(end, safeSpeechStart)));
  const speechEnd = roundTimingSec(Math.max(speechStart, Math.min(end, safeSpeechEnd)));
  return {
    ...scene,
    ...extraPatch,
    start_sec: start,
    end_sec: end,
    duration_sec: roundTimingSec(end - start),
    speech_start_sec: speechStart,
    speech_end_sec: speechEnd,
    pre_silence_sec: roundTimingSec(Math.max(0, speechStart - start)),
    post_silence_sec: roundTimingSec(Math.max(0, end - speechEnd)),
  };
}

function shiftManualTimingScene(scene = {}, deltaSec = 0) {
  const delta = roundTimingSec(deltaSec);
  const start = roundTimingSec(Number(scene?.start_sec || 0) + delta);
  const end = roundTimingSec(Number(scene?.end_sec || 0) + delta);
  const speechStart = Number.isFinite(Number(scene?.speech_start_sec)) ? roundTimingSec(Number(scene.speech_start_sec) + delta) : start;
  const speechEnd = Number.isFinite(Number(scene?.speech_end_sec)) ? roundTimingSec(Number(scene.speech_end_sec) + delta) : end;
  return {
    ...scene,
    start_sec: start,
    end_sec: end,
    duration_sec: roundTimingSec(Math.max(0.01, end - start)),
    speech_start_sec: speechStart,
    speech_end_sec: speechEnd,
    pre_silence_sec: roundTimingSec(Math.max(0, speechStart - start)),
    post_silence_sec: roundTimingSec(Math.max(0, end - speechEnd)),
  };
}

function reindexManualTimingTimelineScenes(scenes = []) {
  return (Array.isArray(scenes) ? scenes : [])
    .filter((scene) => Number(scene?.end_sec || 0) > Number(scene?.start_sec || 0) + 0.001)
    .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0))
    .map((scene, index) => ({
      ...scene,
      scene_id: `seg_${String(index + 1).padStart(2, "0")}`,
      index: index + 1,
      duration_sec: roundTimingSec(Math.max(0.01, Number(scene.end_sec || 0) - Number(scene.start_sec || 0))),
    }));
}

function buildManualTimingMarkersFromScenesList(scenes = [], durationSec = 0) {
  const values = [0, roundTimingSec(durationSec)];
  (Array.isArray(scenes) ? scenes : []).forEach((scene) => {
    values.push(roundTimingSec(scene?.start_sec));
    values.push(roundTimingSec(scene?.end_sec));
  });
  return normalizeMarkersForExactDuration(values, durationSec);
}


function rebuildManualTimingScenesWithVirtualSilence(rawScenes = [], originalDurationSec = 0) {
  const sourceDuration = roundTimingSec(Math.max(0, Number(originalDurationSec || 0)));
  const safeScenes = (Array.isArray(rawScenes) ? rawScenes : [])
    .filter((scene) => Number(scene?.end_sec || 0) > Number(scene?.start_sec || 0) + 0.001)
    .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0));
  if (!safeScenes.length) return { scenes: [], durationSec: sourceDuration, changed: false };

  const silenceTotal = safeScenes.reduce((sum, scene) => (
    isManualTimingSilenceScene(scene) ? sum + Math.max(0, Number(scene.end_sec || 0) - Number(scene.start_sec || 0)) : sum
  ), 0);
  const hasSilence = silenceTotal > 0.001;
  if (!hasSilence) {
    const maxEnd = safeScenes.reduce((max, scene) => Math.max(max, Number(scene?.end_sec || 0)), 0);
    return { scenes: reindexManualTimingTimelineScenes(safeScenes), durationSec: roundTimingSec(Math.max(sourceDuration, maxEnd)), changed: false };
  }

  const explicitSourceCount = safeScenes.filter((scene) => (
    !isManualTimingSilenceScene(scene)
    && Number.isFinite(Number(scene?.source_start_sec))
    && Number.isFinite(Number(scene?.source_end_sec))
  )).length;
  const audioSceneIndexes = safeScenes
    .map((scene, index) => (isManualTimingSilenceScene(scene) ? -1 : index))
    .filter((index) => index >= 0);
  const lastAudioIndex = audioSceneIndexes[audioSceneIndexes.length - 1];

  let timelineCursor = 0;
  let sourceCursor = 0;
  let changed = false;
  const rebuilt = safeScenes.map((scene, index) => {
    const oldStart = roundTimingSec(scene.start_sec);
    const oldEnd = roundTimingSec(scene.end_sec);
    const oldDuration = roundTimingSec(Math.max(0.01, oldEnd - oldStart));

    if (isManualTimingSilenceScene(scene)) {
      const start = roundTimingSec(timelineCursor);
      const end = roundTimingSec(start + oldDuration);
      timelineCursor = end;
      if (Math.abs(start - oldStart) > 0.001 || Math.abs(end - oldEnd) > 0.001) changed = true;
      return decorateManualTimingSilenceScene(retimeManualTimingScene(scene, start, end, {
        source_kind: "silence",
        source_start_sec: null,
        source_end_sec: null,
      }));
    }

    const explicitSourceStart = Number(scene.source_start_sec);
    const explicitSourceEnd = Number(scene.source_end_sec);
    let sourceStart = Number.isFinite(explicitSourceStart) ? roundTimingSec(explicitSourceStart) : roundTimingSec(sourceCursor);
    let sourceEnd = Number.isFinite(explicitSourceEnd) ? roundTimingSec(explicitSourceEnd) : null;

    if (sourceEnd === null) {
      const remainingSource = roundTimingSec(Math.max(0.01, sourceDuration - sourceStart));
      const shouldAbsorbLegacyGap = explicitSourceCount === 0 && index === lastAudioIndex && sourceDuration > 0;
      const sourceDurationForScene = shouldAbsorbLegacyGap ? remainingSource : Math.min(oldDuration, remainingSource);
      sourceEnd = roundTimingSec(sourceStart + Math.max(0.01, sourceDurationForScene));
    }

    const start = roundTimingSec(timelineCursor);
    const end = roundTimingSec(start + Math.max(0.01, sourceEnd - sourceStart));
    timelineCursor = end;
    sourceCursor = sourceEnd;
    if (
      Math.abs(start - oldStart) > 0.001
      || Math.abs(end - oldEnd) > 0.001
      || !Number.isFinite(Number(scene.source_start_sec))
      || !Number.isFinite(Number(scene.source_end_sec))
    ) changed = true;

    return retimeManualTimingScene(scene, start, end, {
      source_kind: "audio",
      source_start_sec: sourceStart,
      source_end_sec: sourceEnd,
      is_silence: false,
      is_virtual_silence: false,
    });
  });

  return {
    scenes: reindexManualTimingTimelineScenes(rebuilt),
    durationSec: roundTimingSec(Math.max(timelineCursor, sourceDuration + silenceTotal)),
    changed,
  };
}

function repairManualTimingSilenceTimelineProject(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const safeAudio = safeProject.audio && typeof safeProject.audio === "object" ? safeProject.audio : {};
  const rawScenes = Array.isArray(safeProject.scenes) ? safeProject.scenes : [];
  if (!rawScenes.some((scene) => isManualTimingSilenceScene(scene))) return null;

  const currentDuration = roundTimingSec(Number(safeAudio.duration_sec || safeAudio.durationSec || 0));
  const sourceDuration = roundTimingSec(Number(
    safeAudio.source_duration_sec
    || safeAudio.original_duration_sec
    || safeAudio.sourceDurationSec
    || safeAudio.originalDurationSec
    || currentDuration
    || 0
  ));
  if (!(sourceDuration > 0)) return null;

  const rebuilt = rebuildManualTimingScenesWithVirtualSilence(rawScenes, sourceDuration);
  if (!rebuilt.scenes.length) return null;
  const nextDuration = roundTimingSec(Math.max(rebuilt.durationSec, currentDuration));
  const currentMaxEnd = rawScenes.reduce((max, scene) => Math.max(max, Number(scene?.end_sec || 0)), 0);
  const needsRepair = rebuilt.changed
    || Math.abs(nextDuration - currentDuration) > 0.001
    || Math.abs(nextDuration - currentMaxEnd) > 0.001;
  if (!needsRepair) return null;

  return {
    ...safeProject,
    audio: {
      ...safeAudio,
      source_duration_sec: sourceDuration,
      original_duration_sec: sourceDuration,
      timeline_duration_sec: nextDuration,
      duration_sec: nextDuration,
      duration_ms: Math.round(nextDuration * 1000),
    },
    markers: buildManualTimingMarkersFromScenesList(rebuilt.scenes, nextDuration),
    scenes: rebuilt.scenes,
    virtual_silence_blocks: buildManualTimingSilenceBlocksFromScenes(rebuilt.scenes),
    timing_status: "draft",
  };
}

function getSceneIdForIndex(index) {
  return `seg_${String(Number(index || 0) + 1).padStart(2, "0")}`;
}

function getLastInternalMarker(markers = []) {
  const safe = Array.isArray(markers) ? markers : [];
  return Number(safe.length >= 2 ? safe[safe.length - 2] : 0) || 0;
}

function parseTimingInput(value = "") {
  const raw = String(value || "").trim().replace(/,/g, ".");
  if (!raw) return null;

  if (raw.includes(":")) {
    const parts = raw.split(":").map((part) => part.trim()).filter((part) => part !== "");
    if (!parts.length || parts.length > 3) return null;
    const numbers = parts.map((part) => Number(part));
    if (numbers.some((num) => !Number.isFinite(num) || num < 0)) return null;
    if (numbers.length === 3) return numbers[0] * 3600 + numbers[1] * 60 + numbers[2];
    if (numbers.length === 2) return numbers[0] * 60 + numbers[1];
    return numbers[0];
  }

  const seconds = Number(raw);
  return Number.isFinite(seconds) && seconds >= 0 ? seconds : null;
}

function padTimingPart(value, size) {
  const raw = String(value ?? "").replace(/\D/g, "");
  if (!raw) return "0".repeat(size);
  return raw.slice(-size).padStart(size, "0");
}

function getTimingPartsFromSec(value = 0) {
  const totalMs = Math.max(0, Math.round(Number(value || 0) * 1000));
  const totalSec = Math.floor(totalMs / 1000);
  const ms = totalMs % 1000;
  const min = Math.floor(totalSec / 60);
  const sec = totalSec % 60;
  return {
    min: String(min),
    sec: String(sec).padStart(2, "0"),
    ms: String(ms).padStart(3, "0"),
  };
}

function getSecFromTimingParts(parts = {}) {
  const min = Number(String(parts.min ?? "0").replace(/\D/g, "") || 0);
  const sec = Number(String(parts.sec ?? "0").replace(/\D/g, "") || 0);
  const ms = Number(String(parts.ms ?? "0").replace(/\D/g, "") || 0);
  if (![min, sec, ms].every(Number.isFinite)) return null;
  return min * 60 + sec + ms / 1000;
}

function getDurationWarningClassName(durationWarning = null) {
  if (!durationWarning) return "";
  if (durationWarning.severity === "danger") return "isDanger";
  if (durationWarning.severity === "warning") return "isWarning";
  return "isSoft";
}

function isInstrumentalScene(scene = {}) {
  return String(scene?.section || "").toLowerCase() === "instrumental" || (!scene?.contains_vocal && scene?.contains_instrumental_assumption);
}

function isStoryVoiceoverProject(project = {}) {
  return String(project?.project_mode || project?.projectMode || "") === MANUAL_TIMING_STORY_VOICEOVER_MODE
    || String(project?.project_kind || project?.projectKind || "") === MANUAL_TIMING_STORY_PROJECT_KIND;
}

function getManualTimingModeConfig(project = {}) {
  const mode = String(project?.project_mode || project?.projectMode || "").trim();
  if (mode === MANUAL_TIMING_STORY_VOICEOVER_MODE) {
    return {
      mode,
      className: "mode-story_voiceover",
      title: "Тайминг · История / Voice-over",
      badge: "История",
      subtitle: "ASR → gap-aware scenes → Story Pass → Story Bible Pass → Block Storyboard Pass",
      hint: "Озвученная история: ASR режет речь, сцены покрывают паузы, Story Pass заполняет смысловые блоки, Story Bible фиксирует единый мир и стиль.",
    };
  }
  if (mode === MANUAL_TIMING_MUSIC_CLIP_MODE) {
    return {
      mode,
      className: "mode-music_clip",
      title: "Тайминг · Клип / Music video",
      badge: "Клип",
      subtitle: "ASR → song structure → Clip Pass",
      hint: "Музыкальный клип: ASR режет фразы, Clip Pass определяет куплет/припев/проигрыш и назначает ia2v/i2v/i2v_sound.",
    };
  }
  if (mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) {
    return {
      mode,
      className: "mode-podcast_dialogue",
      title: "Тайминг · Подкаст / Dialogue",
      badge: "Подкаст",
      subtitle: "ASR → speakers/topics → Podcast Pass",
      hint: "Подкаст/история с репликами: ASR режет фразы, Podcast Pass определяет спикеров, темы, B-roll и сцены с произношением текста.",
    };
  }
  return {
    mode: "",
    className: "mode-unselected",
    title: "Тайминг · режим не выбран",
    badge: "Не выбран",
    subtitle: "выберите тип проекта в ноде",
    hint: "Режим проекта не выбран. Вернитесь в ноду и выберите тип проекта.",
  };
}


function getManualTimingRouteOptions(mode = "") {
  if (mode === MANUAL_TIMING_STORY_VOICEOVER_MODE) return ["i2v"];
  if (mode === MANUAL_TIMING_MUSIC_CLIP_MODE) return ["ia2v", "i2v", "i2v_sound"];
  if (mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) return ["i2v", "i2v_sound", "i2v_text"];
  return MANUAL_TIMING_ROUTES;
}

function getMergedManualTimingRoute(currentRoute = "", nextRoute = "") {
  const current = normalizeManualTimingRoute(currentRoute);
  const next = normalizeManualTimingRoute(nextRoute);
  const routeStrength = {
    i2v: 1,
    i2v_sound: 2,
    i2v_text: 3,
    ia2v: 4,
  };
  if (!String(currentRoute || "").trim() && String(nextRoute || "").trim()) return next;
  return (routeStrength[next] || 0) > (routeStrength[current] || 0) ? next : current;
}

function mergeManualTimingSourcePhraseIds(...phraseIdLists) {
  const merged = [];
  const seen = new Set();
  phraseIdLists.forEach((phraseIds) => {
    normalizeManualTimingSourcePhraseIds(phraseIds).forEach((phraseId) => {
      const key = String(phraseId || "").trim();
      if (!key || seen.has(key)) return;
      seen.add(key);
      merged.push(key);
    });
  });
  return merged;
}

function mergeManualTimingTextValue(primaryValue = "", nextValue = "") {
  const primary = String(primaryValue || "").trim();
  const next = String(nextValue || "").trim();
  if (!primary) return next;
  if (!next || primary === next || primary.includes(next)) return primary;
  return `${primary}
${next}`;
}

function rebuildManualTimingStoryBlocksForScenes(storyBlocks = [], nextScenes = []) {
  const sceneIds = new Set((Array.isArray(nextScenes) ? nextScenes : [])
    .map((scene) => String(scene?.scene_id || "").trim())
    .filter(Boolean));
  const unknownBlockId = String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || "");

  return normalizeManualTimingStoryBlocks(storyBlocks).map((block) => {
    const filteredBlock = {
      ...block,
      scene_ids: (Array.isArray(block?.scene_ids) ? block.scene_ids : [])
        .map((sceneId) => String(sceneId || "").trim())
        .filter((sceneId) => sceneId && sceneIds.has(sceneId)),
    };
    const derived = deriveStoryBlockRangeFromScenes(filteredBlock, nextScenes);
    if (derived) {
      return {
        ...filteredBlock,
        scene_ids: derived.scene_ids,
        start_sec: derived.start_sec,
        end_sec: derived.end_sec,
      };
    }
    return {
      ...filteredBlock,
      scene_ids: [],
      start_sec: 0,
      end_sec: 0,
    };
  }).filter((block) => {
    const blockIdValue = String(block?.block_id || "").trim();
    if (blockIdValue === unknownBlockId) return true;
    return (Array.isArray(block?.scene_ids) ? block.scene_ids : []).length > 0;
  });
}

function getManualTimingWorkflowLabels(mode = "") {
  if (mode === MANUAL_TIMING_MUSIC_CLIP_MODE) {
    return {
      phraseMap: "Создать Audio/Lyrics Phrase Map",
      buildScenes: "Собрать clip scenes из ASR",
      pass: "Clip Pass",
      copyPass: "Скопировать JSON для Clip Pass",
      applyPass: "Применить Clip Pass JSON",
      insertPass: "Вставить Clip Pass JSON",
      panelTitle: "Clip Pass JSON",
      panelHint: "Вставь JSON после Clip Pass: он заполняет song_blocks и смысловые поля клипа, но не video_prompt/negative_prompt/sound_prompt.",
      placeholder: "Вставь сюда Clip Pass JSON: scenes/song_blocks с сохранёнными таймингами и заполненными смысловыми полями...",
    };
  }
  if (mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) {
    return {
      phraseMap: "Создать Podcast Phrase Map",
      buildScenes: "Собрать podcast scenes из ASR",
      pass: "Podcast Pass",
      copyPass: "Скопировать JSON для Podcast Pass",
      applyPass: "Применить Podcast Pass JSON",
      insertPass: "Вставить Podcast Pass JSON",
      panelTitle: "Podcast Pass JSON",
      panelHint: "Вставь JSON после Podcast Pass: он заполняет speakers/topic_blocks, B-roll и тексты для generated voice, но не финальные prompts.",
      placeholder: "Вставь сюда Podcast Pass JSON: scenes/speakers/topic_blocks с сохранёнными таймингами и заполненными смысловыми полями...",
    };
  }
  return {
    phraseMap: "Создать Audio Phrase Map",
    buildScenes: "Собрать story scenes из ASR",
    pass: "Смысловая нарезка",
    copyPass: "Скопировать смысловую нарезку",
    applyPass: "Применить смысловую нарезку",
    insertPass: "Вставить смысловую нарезку",
    panelTitle: "AI JSON workflow",
    panelHint: "Вставь JSON нужного этапа: смысловая нарезка, библия истории или блочная раскадровка. video_prompt / negative_prompt / sound_prompt остаются пустыми до отдельного video-pass.",
    placeholder: "Вставь сюда JSON нужного этапа Manual Timing с manual_timing_pass.pass_type...",
  };
}

const MANUAL_STORY_BIBLE_PROJECT_KEYS = [
  "project_story_summary_ru",
  "project_core_theme_ru",
  "project_drama_arc_ru",
  "project_visual_bible_ru",
  "project_style_lock_ru",
  "project_world_lock_ru",
  "project_character_identity_lock_ru",
  "project_location_lock_ru",
  "project_time_progression_ru",
  "project_atmosphere_lock_ru",
  "project_camera_language_ru",
  "project_color_progression_ru",
  "project_continuity_rules_ru",
  "project_must_keep_same_ru",
  "project_allowed_variation_ru",
  "project_reference_prompt_en",
];

function getManualTimingPayloadPassType(rawObject = {}) {
  if (String(rawObject?.backup_type || rawObject?.backupType || "") === MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE) return "";
  const object = unwrapManualProjectBackupJson(rawObject);
  return String(object?.manual_timing_pass?.pass_type || object?.manualTimingPass?.passType || "").trim();
}

function getManualTimingJsonPassType(rawObject = {}) {
  if (String(rawObject?.backup_type || rawObject?.backupType || "") === MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE) return "timing_backup";
  const object = unwrapManualProjectBackupJson(rawObject);
  const payloadPassType = getManualTimingPayloadPassType(object);
  if (payloadPassType) return payloadPassType;
  const splitType = String(object?.split_type || object?.splitType || "").trim();
  if (splitType === "manual_story_bible_pass") return "story_bible";
  if (splitType === "manual_block_storyboard_pass") return "block_storyboard";
  if (splitType === "manual_timing_draft" || splitType === "semantic_story_cut_pass" || splitType === "manual_story_pass") return "semantic_story_cut";
  const task = typeof object?.chatgpt_task === "string" ? object.chatgpt_task : JSON.stringify(object?.chatgpt_task || "");
  if (task.includes("GLOBAL STORY BIBLE")) return "story_bible";
  if (task.includes("SEMANTIC STORY CUT PASS") || task.includes("СМЫСЛОВАЯ НАРЕЗКА ИСТОРИИ")) return "semantic_story_cut";
  return splitType || "unknown";
}

function getManualTimingPassNameRu(passType = "") {
  return MANUAL_TIMING_AI_PASS_BY_TYPE[passType]?.pass_name_ru || String(passType || "неизвестный этап");
}

function getManualTimingStageStatus(workflow = {}, passType = "", scenes = []) {
  const completedStages = Array.isArray(workflow?.completed_stages) ? workflow.completed_stages : [];
  if (completedStages.includes(passType)) return "применён";
  if (passType === "semantic_story_cut") return scenes.length ? "готов к AI" : "заблокирован";
  const stage = MANUAL_TIMING_AI_PASS_BY_TYPE[passType];
  const ready = stage?.requires?.every((required) => completedStages.includes(required));
  return ready ? "готов к AI" : "заблокирован";
}

function isManualTimingStageAvailable(workflow = {}, passType = "", scenes = []) {
  return getManualTimingStageStatus(workflow, passType, scenes) !== "заблокирован";
}

function getManualTimingStageStatusClass(status = "") {
  if (status === "применён") return "manualTimingAiStageDone";
  if (status === "готов к AI") return "manualTimingAiStageReady";
  return "manualTimingAiStageLocked";
}

function getCompactWarningItems(project = {}, warnings = []) {
  const safeWarnings = Array.isArray(warnings) ? warnings : [];
  const scenes = Array.isArray(project.scenes) ? project.scenes : [];
  const audioPhrases = normalizeManualTimingAudioPhrases(project.audio_phrases);
  const items = [];
  const hasStoryScenes = scenes.some((scene) => normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds).length);
  const hasImportedStoryPass = scenes.some(sceneHasStoryPassFields);
  if (hasStoryScenes && !hasImportedStoryPass) items.push("Semantic Story Cut ещё не заполнен");
  if (safeWarnings.some((warning) => /длин|больше 9|long/i.test(String(warning || "")))) items.push("Есть длинные сцены");
  if (!audioPhrases.length) items.push("Нет audio_phrases");
  if (!items.length && safeWarnings.length) items.push(`Есть предупреждения: ${safeWarnings.length}`);
  if (!items.length) items.push("Проверка: предупреждений нет");
  return [...new Set(items)];
}

function sceneHasCreatedMaterials(scene = {}) {
  return ["image_url", "image_preview_url", "video_url", "video_prompt", "negative_prompt", "sound_prompt", "video_job_id", "audio_slice_url"].some((key) => String(scene?.[key] || "").trim())
    || String(scene?.status || "").trim().toLowerCase() === "video_ready";
}

function isSingleFullLengthDraftScene(scenes = [], audioDurationSec = 0) {
  if (!Array.isArray(scenes) || scenes.length !== 1) return false;
  const scene = scenes[0] || {};
  const duration = Number(audioDurationSec || 0);
  const startsAtZero = Math.abs(Number(scene.start_sec || 0)) < 0.02;
  const endsAtDuration = !(duration > 0) || Math.abs(Number(scene.end_sec || 0) - duration) < 0.05;
  return String(scene.scene_id || "") === "seg_01" && startsAtZero && endsAtDuration;
}

function sceneHasStoryPassFields(scene = {}) {
  return STORY_PASS_REQUIRED_SCENE_FIELDS.some((key) => String(scene?.[key] || "").trim());
}

function sceneHasCompleteStoryPassFields(scene = {}) {
  return STORY_PASS_REQUIRED_SCENE_FIELDS.every((key) => String(scene?.[key] || "").trim());
}


function sceneHasCompleteClipPassFields(scene = {}) {
  return ["route", "song_block_id", "scene_goal_ru", "photo_prompt_hint_ru", "prompt_hint_ru"].every((key) => String(scene?.[key] || "").trim());
}

function sceneHasCompletePodcastPassFields(scene = {}) {
  return ["topic_block_id", "scene_type", "scene_goal_ru", "photo_prompt_hint_ru", "prompt_hint_ru"]
    .every((key) => String(scene?.[key] || "").trim());
}

function hasNonEmptyArray(value = []) {
  return Array.isArray(value) && value.length > 0;
}

function hasRealStoryBlocks(storyBlocks = []) {
  const unknownId = String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || "block_unknown");
  return (Array.isArray(storyBlocks) ? storyBlocks : []).some((block) => {
    const blockId = String(block?.block_id || block?.id || "").trim();
    return blockId && blockId !== unknownId;
  });
}

async function sliceStoryVoiceoverAudioForScenes(projectSnapshot = {}) {
  const scenes = Array.isArray(projectSnapshot?.scenes) ? projectSnapshot.scenes : [];
  const res = await fetch(`${API_BASE}/api/manual-clip/slice-audio`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      audio_url: projectSnapshot?.audio?.url,
      audio_filename: projectSnapshot?.audio?.filename,
      project_kind: "story",
      format: projectSnapshot?.format || "9:16",
      scenes: scenes.map((scene) => ({
        scene_id: scene.scene_id,
        start_sec: scene.start_sec,
        end_sec: scene.end_sec,
      })),
    }),
  });
  const data = await res.json().catch(() => null);
  if (!res.ok || data?.ok === false) {
    throw new Error(String(data?.detail || data?.message || `HTTP ${res.status}`));
  }
  return Array.isArray(data?.scenes) ? data.scenes : [];
}

function getStoryVoiceoverStatus(project = {}, audioPhrases = [], scenes = [], audioDurationSec = 0) {
  if (!audioPhrases.length) return "Шаг 1: создайте Audio Phrase Map";
  if (!scenes.length || isSingleFullLengthDraftScene(scenes, audioDurationSec)) return "Шаг 2: соберите story scenes из ASR";
  const hasStorySourceIds = scenes.some((scene) => Array.isArray(scene?.source_phrase_ids) && scene.source_phrase_ids.length);
  const hasStoryPass = scenes.some(sceneHasStoryPassFields);
  if (hasStorySourceIds && !hasStoryPass) return "Шаг 3: скопируйте JSON для Story Pass";
  return "Шаг 4: проверьте блоки и подтвердите";
}

function getReadableTimingStatus(project = {}, audioPhrases = [], scenes = [], audioDurationSec = 0) {
  if (isStoryVoiceoverProject(project)) return getStoryVoiceoverStatus(project, audioPhrases, scenes, audioDurationSec);
  return STATUS_LABELS[project.timing_status] || String(project.timing_status || "пусто");
}

function getCompactWarningsSummary(compactItems = []) {
  const items = Array.isArray(compactItems) ? compactItems : [];
  return items.join(" · ");
}

function getTimingSceneIdForAudioPhrase(phrase = null, scenes = []) {
  if (!phrase) return "";
  const start = Number(phrase?.start_sec || 0);
  const end = Number(phrase?.end_sec || 0);
  if (!(end > start)) return "";

  const containingScene = (Array.isArray(scenes) ? scenes : []).find((scene) => {
    const sceneStart = Number(scene?.start_sec || 0);
    const sceneEnd = Number(scene?.end_sec || 0);
    return start >= sceneStart - 0.001 && end <= sceneEnd + 0.001;
  });

  return String(containingScene?.scene_id || "");
}

function isUnresolvedAudioPhrase(phrase = {}) {
  return String(phrase?.status || "") === "needs_transcription"
    || String(phrase?.assignment_status || "") === "unassigned";
}

function hasTimingDraftValue(value) {
  return value !== null && value !== undefined && Number.isFinite(Number(value));
}

function getSceneStoryText(scene = {}) {
  const originalRaw = String(scene?.original_text || scene?.adapted_text_en || scene?.source_text_en || "").trim();
  const ruRaw = String(scene?.translated_text_ru || "").trim();
  const meaningRaw = String(scene?.meaning_hint_ru || "").trim();
  const blockGoalRaw = String(scene?.story_block_goal_ru || scene?.block_goal_ru || "").trim();
  const blockRevealRaw = String(scene?.story_block_reveal_ru || scene?.block_reveal_ru || "").trim();
  const blockEmotionRaw = String(scene?.story_block_emotion_ru || scene?.block_emotion_ru || "").trim();
  const sceneRoleRaw = String(scene?.scene_role_in_block_ru || "").trim();
  const blockProgressRaw = String(scene?.block_progress_ru || "").trim();
  const hasAnyStoryText = Boolean(originalRaw || ruRaw || meaningRaw);
  const instrumental = isInstrumentalScene(scene) && !hasAnyStoryText;
  return {
    blockTitle: String(scene?.story_block_title_ru || "Без блока"),
    blockColor: String(scene?.story_block_color || "#64748B"),
    position: String(scene?.story_block_position_ru || "—"),
    blockGoal: blockGoalRaw || "—",
    blockReveal: blockRevealRaw || "—",
    blockEmotion: blockEmotionRaw || "—",
    sceneRole: sceneRoleRaw || "—",
    blockProgress: blockProgressRaw || "—",
    original: originalRaw || "—",
    ru: ruRaw || "—",
    meaning: meaningRaw || (instrumental ? "Инструментальная / сюжетная сцена." : "—"),
  };
}

function buildAsrVerificationScenes(audioPhrases = []) {
  return normalizeManualTimingAudioPhrases(audioPhrases).map((phrase, idx) => {
    const sceneId = `asr_${String(idx + 1).padStart(3, "0")}`;
    return {
      scene_id: sceneId,
      index: idx + 1,
      start_sec: phrase.start_sec,
      end_sec: phrase.end_sec,
      duration_sec: roundTimingSec(phrase.end_sec - phrase.start_sec),
      section: "verse",
      route: "i2v",
      contains_vocal: false,
      contains_vocal_assumption: false,
      contains_instrumental_assumption: true,
      use_sound_suggestion: false,
      energy: "mid",
      quality: "asr_phrase_map_preview",
      boundary_reason: "asr_phrase_boundary",
      transition_out: "asr_phrase_cut",
      story_time: "",
      scene_type: "asr_phrase_preview",
      drama_hint: "",
      short_note: "ASR phrase map preview — не финальный storyboard",
      scene_goal_ru: "",
      photo_prompt_hint_ru: "",
      prompt_hint_ru: "",
      story_position_ru: "",
      user_note_ru: "ASR phrase map: временная сцена для проверки тайминга фразы, не финальный storyboard.",
      source_phrase_ids: [phrase.phrase_id],
      story_block_id: MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id,
      story_block_title_ru: MANUAL_TIMING_UNKNOWN_STORY_BLOCK.title_ru,
      story_block_color: MANUAL_TIMING_UNKNOWN_STORY_BLOCK.color,
      story_block_position_ru: "",
      scene_role_in_block_ru: "",
      block_progress_ru: "",
      original_text: phrase.text_en || "",
      translated_text_ru: phrase.text_ru || "",
      meaning_hint_ru: phrase.meaning_ru || "",
      source_text_en: phrase.text_en || "",
      adapted_text_en: "",
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: "",
    };
  });
}

function getAsrPhraseStyle(phrase, durationSec) {
  const start = clampTime(phrase.start_sec, durationSec);
  const end = clampTime(phrase.end_sec, durationSec);
  const width = durationSec > 0 ? ((end - start) / durationSec) * 100 : 0;
  return {
    left: `${durationSec > 0 ? Math.max(0, Math.min(100, (start / durationSec) * 100)) : 0}%`,
    width: `${Math.max(0.35, Math.min(100, width))}%`,
  };
}

function getScenePhraseAlignmentWarnings(scene = null, scenePhrases = [], allAudioPhrases = [], allScenes = [], audioDurationSec = 0) {
  if (!scene || isManualTimingSilenceScene(scene)) return [];
  const warnings = [];
  const sceneStart = Number(scene.start_sec || 0);
  const sceneEnd = Number(scene.end_sec || 0);
  const sceneDuration = Number(scene.duration_sec || (sceneEnd - sceneStart));
  const speechStart = Number(scene.speech_start_sec ?? sceneStart);
  const speechEnd = Number(scene.speech_end_sec ?? sceneEnd);
  const preSilence = Number(scene.pre_silence_sec ?? Math.max(0, speechStart - sceneStart));
  const postSilence = Number(scene.post_silence_sec ?? Math.max(0, sceneEnd - speechEnd));
  const sorted = [...(Array.isArray(scenePhrases) ? scenePhrases : [])].sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0));
  const sourceIds = Array.isArray(scene.source_phrase_ids) ? scene.source_phrase_ids.map((id) => String(id || "")).filter(Boolean) : [];
  const coverage = validateSceneCoverage(allScenes, audioDurationSec);

  if (!coverage.ok) warnings.push(...coverage.errors);
  if (!sourceIds.length) warnings.push(`${scene.scene_id}: source_phrase_ids пустые — scene не связана с ASR-фразами.`);
  if (sceneDuration < 2) warnings.push(`${scene.scene_id}: scene слишком короткая для монтажной сцены (${sceneDuration.toFixed(2)} сек).`);
  if (sceneDuration > 10) warnings.push(`${scene.scene_id}: scene слишком длинная (${sceneDuration.toFixed(2)} сек).`);
  if (preSilence > 1.25) warnings.push(`${scene.scene_id}: большая pre-silence ${preSilence.toFixed(2)} сек.`);
  if (postSilence > 1.25) warnings.push(`${scene.scene_id}: большая post-silence ${postSilence.toFixed(2)} сек.`);

  const sceneSourceStart = Number(getManualTimingSceneSourceStartSec(scene));
  const sceneSourceEnd = Number(getManualTimingSceneSourceEndSec(scene));
  const phraseIdsInsideScene = normalizeManualTimingAudioPhrases(allAudioPhrases)
    .filter((phrase) => Number(phrase.start_sec || 0) < sceneSourceEnd - 0.001 && Number(phrase.end_sec || 0) > sceneSourceStart + 0.001)
    .map((phrase) => String(phrase.phrase_id || ""))
    .filter(Boolean);
  const missingInsideSource = phraseIdsInsideScene.filter((phraseId) => !sourceIds.includes(phraseId));
  if (missingInsideSource.length) warnings.push(`${scene.scene_id}: внутри scene есть ASR phrase, но её нет в source_phrase_ids: ${missingInsideSource.join(", ")}.`);

  if (sourceIds.length && sorted.length) {
    const actualIds = sorted.map((phrase) => String(phrase.phrase_id || "")).filter(Boolean);
    const sameIds = sourceIds.length === actualIds.length && sourceIds.every((id, idx) => id === actualIds[idx]);
    if (!sameIds) warnings.push(`source_phrase_ids не совпадает с фразами внутри диапазона: ${sourceIds.join(", ") || "—"} vs ${actualIds.join(", ") || "—"}.`);
    const sourceFirst = sorted.find((phrase) => String(phrase.phrase_id || "") === sourceIds[0]);
    const sourceLast = sorted.find((phrase) => String(phrase.phrase_id || "") === sourceIds[sourceIds.length - 1]);
    if (sourceFirst && Math.abs(speechStart - Number(sourceFirst.start_sec || 0)) > 0.08) warnings.push(`speech_start_sec не равен start первой source_phrase (${formatTimingSec(sourceFirst.start_sec)}).`);
    if (sourceLast && Math.abs(speechEnd - Number(sourceLast.end_sec || 0)) > 0.08) warnings.push(`speech_end_sec не равен end последней source_phrase (${formatTimingSec(sourceLast.end_sec)}).`);
  }
  return [...new Set(warnings)];
}


function cloneManualTimingProjectForHistory(project) {
  try {
    return JSON.parse(JSON.stringify(project || {}));
  } catch {
    return { ...(project || {}) };
  }
}

function createManualTimingHistorySnapshot(project, currentTimeSec = 0, label = "edit") {
  return {
    id: `history_${Date.now()}_${Math.random().toString(16).slice(2)}`,
    label,
    currentTimeSec: roundTimingSec(Number(currentTimeSec || 0)),
    project: cloneManualTimingProjectForHistory(project),
  };
}


function pickManualTimingAudioPhraseOriginalText(phrase = {}) {
  return String(
    phrase?.text_original
    || phrase?.textOriginal
    || phrase?.text
    || phrase?.text_en
    || phrase?.textEn
    || phrase?.text_de
    || phrase?.textDe
    || phrase?.text_fr
    || phrase?.textFr
    || phrase?.original_text
    || phrase?.originalText
    || ""
  ).trim();
}

function pickManualTimingAudioPhraseRuText(phrase = {}) {
  return String(phrase?.text_ru || phrase?.textRu || phrase?.translation_ru || phrase?.translationRu || phrase?.meaning_ru || phrase?.meaningRu || "").trim();
}

function isManualTimingPhrasePartialInScene(phrase = {}, scene = null) {
  if (!scene || isManualTimingSilenceScene(scene)) return false;
  const sceneStart = Number(getManualTimingSceneSourceStartSec(scene));
  const sceneEnd = Number(getManualTimingSceneSourceEndSec(scene));
  const phraseStart = Number(phrase?.start_sec || 0);
  const phraseEnd = Number(phrase?.end_sec || 0);
  if (!(sceneEnd > sceneStart) || !(phraseEnd > phraseStart)) return false;
  return phraseStart < sceneStart - 0.001 || phraseEnd > sceneEnd + 0.001;
}

function getManualTimingPhrasesForSceneInspector(audioPhrases = [], scene = null) {
  if (!scene || !Array.isArray(audioPhrases) || isManualTimingSilenceScene(scene)) return [];
  const sceneStart = Number(getManualTimingSceneSourceStartSec(scene));
  const sceneEnd = Number(getManualTimingSceneSourceEndSec(scene));
  if (!(sceneEnd > sceneStart)) return [];

  return audioPhrases.reduce((inspectorPhrases, phrase) => {
    const phraseStart = Number(phrase?.start_sec || 0);
    const phraseEnd = Number(phrase?.end_sec || 0);
    const phraseDuration = phraseEnd - phraseStart;
    if (!(phraseDuration > 0)) return inspectorPhrases;

    const overlapStart = Math.max(sceneStart, phraseStart);
    const overlapEnd = Math.min(sceneEnd, phraseEnd);
    const overlapSec = overlapEnd - overlapStart;
    const overlapRatio = overlapSec > 0 ? overlapSec / phraseDuration : 0;
    const isFullyInside = phraseStart >= sceneStart - 0.001 && phraseEnd <= sceneEnd + 0.001;
    const isMeaningfulOverlap = overlapSec >= 0.35 || overlapRatio >= 0.25;

    if (isFullyInside || isMeaningfulOverlap) {
      inspectorPhrases.push({
        ...phrase,
        overlapSec,
        overlapRatio,
        isPartial: !isFullyInside,
      });
    }

    return inspectorPhrases;
  }, []);
}

function getManualTimingPhraseGroupCoverage(phrase = {}, groupScenes = []) {
  const phraseStart = Number(phrase?.start_sec || 0);
  const phraseEnd = Number(phrase?.end_sec || 0);
  const phraseDuration = phraseEnd - phraseStart;
  if (!(phraseDuration > 0) || !Array.isArray(groupScenes) || !groupScenes.length) {
    return { overlapSec: 0, overlapRatio: 0, isFullyInside: false };
  }

  const overlaps = groupScenes
    .map((scene) => {
      if (isManualTimingSilenceScene(scene)) return null;
      const sceneStart = Number(getManualTimingSceneSourceStartSec(scene));
      const sceneEnd = Number(getManualTimingSceneSourceEndSec(scene));
      const overlapStart = Math.max(sceneStart, phraseStart);
      const overlapEnd = Math.min(sceneEnd, phraseEnd);
      return overlapEnd > overlapStart ? [overlapStart, overlapEnd] : null;
    })
    .filter(Boolean)
    .sort((a, b) => a[0] - b[0]);

  const merged = [];
  overlaps.forEach(([start, end]) => {
    const last = merged[merged.length - 1];
    if (!last || start > last[1] + 0.001) {
      merged.push([start, end]);
      return;
    }
    last[1] = Math.max(last[1], end);
  });

  const overlapSec = Math.min(phraseDuration, merged.reduce((total, [start, end]) => total + Math.max(0, end - start), 0));
  const overlapRatio = overlapSec > 0 ? overlapSec / phraseDuration : 0;
  const isFullyInside = overlapSec >= phraseDuration - 0.001;
  return { overlapSec, overlapRatio, isFullyInside };
}

function getManualTimingPhrasesForSceneGroupInspector(audioPhrases = [], groupScenes = []) {
  if (!Array.isArray(groupScenes) || !groupScenes.length || !Array.isArray(audioPhrases)) return [];

  const phraseByKey = new Map();
  const upsertPhrase = (phrase) => {
    const phraseId = String(phrase?.phrase_id || "").trim();
    const phraseStart = Number(phrase?.start_sec || 0);
    const phraseEnd = Number(phrase?.end_sec || 0);
    const key = phraseId || `${phraseStart}:${phraseEnd}:${pickManualTimingAudioPhraseOriginalText(phrase)}`;
    if (!key) return;

    const coverage = getManualTimingPhraseGroupCoverage(phrase, groupScenes);
    const existing = phraseByKey.get(key);
    phraseByKey.set(key, {
      ...(existing || phrase),
      ...phrase,
      overlapSec: coverage.overlapSec,
      overlapRatio: coverage.overlapRatio,
      isPartial: !coverage.isFullyInside,
    });
  };

  groupScenes.forEach((scene) => {
    getManualTimingPhrasesForSceneInspector(audioPhrases, scene).forEach(upsertPhrase);
  });

  audioPhrases.forEach((phrase) => {
    const coverage = getManualTimingPhraseGroupCoverage(phrase, groupScenes);
    const isMeaningfulGroupOverlap = coverage.overlapSec >= 0.35 || coverage.overlapRatio >= 0.25;
    if (coverage.isFullyInside || isMeaningfulGroupOverlap) upsertPhrase(phrase);
  });

  return Array.from(phraseByKey.values())
    .sort((a, b) => Number(a?.start_sec || 0) - Number(b?.start_sec || 0));
}

function buildManualTimingAsrTranslationPassJson(project = {}) {
  const audioPhrases = normalizeManualTimingAudioPhrases(project?.audio_phrases).map((phrase) => ({
    phrase_id: phrase.phrase_id,
    start_sec: phrase.start_sec,
    end_sec: phrase.end_sec,
    text_original: pickManualTimingAudioPhraseOriginalText(phrase),
    text_ru: pickManualTimingAudioPhraseRuText(phrase),
  }));

  return {
    split_type: "manual_timing_asr_translation_pass",
    pass_type: "asr_translation",
    instruction_ru: "Заполни только audio_phrases[].text_ru русским переводом исходной ASR-фразы. Не меняй phrase_id, start_sec, end_sec и порядок фраз.",
    audio_duration_sec: Number(project?.audio?.duration_sec || project?.audio_duration_sec || 0),
    audio_phrases: audioPhrases,
  };
}

function pickManualTimingAudioPhraseImportId(phrase = {}) {
  return String(phrase?.phrase_id || phrase?.phraseId || phrase?.id || "").trim();
}

function mergeManualTimingAsrTranslationPhrases(currentPhrases = [], importedPhrases = []) {
  const normalizedCurrent = normalizeManualTimingAudioPhrases(currentPhrases);
  const importedById = new Map();
  if (Array.isArray(importedPhrases)) {
    importedPhrases.forEach((phrase) => {
      if (!phrase || typeof phrase !== "object") return;
      const phraseId = pickManualTimingAudioPhraseImportId(phrase);
      const importedRu = pickManualTimingAudioPhraseRuText(phrase);
      if (!phraseId || !importedRu) return;
      importedById.set(phraseId, importedRu);
    });
  }
  let updatedCount = 0;
  const nextPhrases = normalizedCurrent.map((phrase) => {
    const importedRu = importedById.get(String(phrase.phrase_id || "").trim());
    if (!importedRu) return phrase;
    const currentRu = pickManualTimingAudioPhraseRuText(phrase);
    if (currentRu === importedRu) return phrase;
    updatedCount += 1;
    return { ...phrase, text_ru: importedRu, translation_ru: importedRu };
  });
  return { audio_phrases: nextPhrases, updatedCount };
}

export default function ManualTimingEditorPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const initialProjectRef = useRef(null);
  if (!initialProjectRef.current) {
    const storedInitialProject = buildInitialProject();
    const initialOwner = resolveManualTimingOwnerNode(location, storedInitialProject).finalOwnerNodeId;
    const incomingInitialProject = getIncomingManualTimingProjectFromLocation(location, storedInitialProject, initialOwner);
    const incomingInitialSignature = incomingInitialProject ? getManualTimingAudioSignature(incomingInitialProject) : "";
    const storedInitialSignature = storedInitialProject ? getManualTimingAudioSignature(storedInitialProject) : "";
    initialProjectRef.current = incomingInitialProject && (!storedInitialProject || manualTimingAudioSignaturesDiffer(incomingInitialSignature, storedInitialSignature))
      ? incomingInitialProject
      : storedInitialProject;
  }
  const audioRef = useRef(null);
  const timelineViewportRef = useRef(null);
  const timelineRef = useRef(null);
  const playUntilRef = useRef(null);
  const rafRef = useRef(null);
  const silenceRafRef = useRef(null);
  const [project, setProject] = useState(() => initialProjectRef.current);
  const [currentTime, setCurrentTime] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  const [jsonImportText, setJsonImportText] = useState("");
  const [isJsonImportOpen, setIsJsonImportOpen] = useState(false);
  const [quickEditSceneId, setQuickEditSceneId] = useState("");
  const [quickEditDraft, setQuickEditDraft] = useState(null);
  const [jumpTimeParts, setJumpTimeParts] = useState(() => ({ min: "0", sec: "00", ms: "000" }));
  const [missingPhraseDraft, setMissingPhraseDraft] = useState({ start_sec: null, end_sec: null });
  const [selectedMissingPhraseId, setSelectedMissingPhraseId] = useState("");
  const [asrStatus, setAsrStatus] = useState("");
  const [audioUploadStatus, setAudioUploadStatus] = useState("");
  const [handoffStatus, setHandoffStatus] = useState("");
  const [trackNudgeStepSec, setTrackNudgeStepSec] = useState(MANUAL_TIMING_NUDGE_DEFAULT_STEP_SEC);
  const [timelineViewportWidth, setTimelineViewportWidth] = useState(0);
  const [timelineScrollLeft, setTimelineScrollLeft] = useState(0);
  const [undoStack, setUndoStack] = useState([]);
  const [redoStack, setRedoStack] = useState([]);
  const [activeBoardProject, setActiveBoardProject] = useState(() => {
    const { finalOwnerNodeId } = resolveManualTimingOwnerNode(location, initialProjectRef.current);
    return getManualTimingBoardForOwnerMatchingProject(finalOwnerNodeId, initialProjectRef.current);
  });
  const [newBoardConfirm, setNewBoardConfirm] = useState(null);
  const [groupSelectedSceneIds, setGroupSelectedSceneIds] = useState([]);
  const [storyBlockDialog, setStoryBlockDialog] = useState({
    isOpen: false,
    selectedSceneIds: [],
    defaultTitle: "",
    title: "",
    color: "",
    hasExistingStoryBlock: false,
    confirmMoveExisting: false,
  });
  const newBoardConfirmResolverRef = useRef(null);
  const currentTimeRef = useRef(0);
  const silenceRepairSignatureRef = useRef("");
  const storyboardReturnHydrateKeyRef = useRef("");
  const isPlayingRef = useRef(false);
  const durationSecRef = useRef(0);
  const playStartGuardRef = useRef(null);
  const manualTimingPlaybackModeRef = useRef("");
  const [manualTimingPlaybackMode, setManualTimingPlaybackMode] = useState("");

  const audio = normalizeManualTimingAudio(project.audio);
  const durationSec = Number(audio.duration_sec || 0);
  const markers = useMemo(() => normalizeManualTimingMarkers(project.markers, durationSec), [project.markers, durationSec]);
  const storyBlocks = useMemo(() => normalizeManualTimingStoryBlocks(project.story_blocks), [project.story_blocks]);
  const audioPhrases = useMemo(() => normalizeManualTimingAudioPhrases(project.audio_phrases), [project.audio_phrases]);
  const scenes = Array.isArray(project.scenes) ? project.scenes : [];
  const activeBoardScenes = Array.isArray(activeBoardProject?.scenes) ? activeBoardProject.scenes : [];
  const activeBoardBlocks = Array.isArray(activeBoardProject?.story_blocks) ? activeBoardProject.story_blocks : [];
  const hasActiveBoardProject = hasMeaningfulManualProject(activeBoardProject);
  const projectFormat = String(project.format || project.aspect_ratio || "9:16");
  const hasSceneMaterials = scenes.some(sceneHasCreatedMaterials);
  const manualSceneEdits = Boolean(project.manualSceneEdits ?? project.manual_scene_edits);
  const currentCutTime = roundTimingSec(currentTime);
  const canCutAtCurrentTime = Boolean(audio.url)
    && durationSec > 0
    && currentCutTime > 0.001
    && currentCutTime < durationSec - 0.001
    && !markers.some((marker) => Math.abs(Number(marker) - currentCutTime) < 0.15);
  const isFormatLocked = Boolean(project.format_locked || hasActiveBoardProject || project.timing_status === "confirmed" || hasSceneMaterials || hasRealStoryBlocks(storyBlocks));
  const isStoryVoiceover = isStoryVoiceoverProject(project);
  const modeConfig = getManualTimingModeConfig(project);
  const isProjectModeSelected = Boolean(modeConfig.mode);
  const mainActionsDisabled = !isProjectModeSelected;
  const manualTimingOwner = useMemo(() => resolveManualTimingOwnerNode(location, project), [
    location?.search,
    location?.state,
    project?.sourceNodeId,
    project?.nodeId,
    project?.project_mode,
    project?.projectMode,
  ]);
  const { routeSourceNodeId, projectSourceNodeId, fallbackOwnerNodeId, finalOwnerNodeId } = manualTimingOwner;
  const isTimingAudioUploading = audioUploadStatus === "uploading";
  const workflowLabels = getManualTimingWorkflowLabels(modeConfig.mode);
  const routeOptions = getManualTimingRouteOptions(modeConfig.mode);
  const isMusicClip = modeConfig.mode === MANUAL_TIMING_MUSIC_CLIP_MODE;
  const isPodcastDialogue = modeConfig.mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE;
  const canRecoverPodcastProjectToStory = isPodcastDialogue && scenes.length > 0 && Boolean(audio.url || durationSec > 0);
  const hasWorkflowCompletedStages = Array.isArray(project?.manual_timing_workflow?.completed_stages) && project.manual_timing_workflow.completed_stages.length > 0;
  const inferredCompletedStages = useMemo(
    () => hasWorkflowCompletedStages ? [] : inferManualTimingCompletedStages(project),
    [
      hasWorkflowCompletedStages,
      project?.manual_timing_workflow,
      project?.scenes,
      project?.story_blocks,
      project?.project_story_summary_ru,
      project?.project_visual_bible_ru,
      project?.project_continuity_rules_ru,
      project?.project_style_lock_ru,
      project?.project_world_lock_ru,
    ]
  );
  const manualTimingWorkflow = normalizeManualTimingWorkflow(project.manual_timing_workflow, inferredCompletedStages);
  const manualTimingStageStatuses = MANUAL_TIMING_AI_PASS_STAGES.reduce((acc, stage) => {
    acc[stage.pass_type] = getManualTimingStageStatus(manualTimingWorkflow, stage.pass_type, scenes);
    return acc;
  }, {});
  const semanticStoryCutReady = isManualTimingStageAvailable(manualTimingWorkflow, "semantic_story_cut", scenes);
  const storyBiblePassReady = isManualTimingStageAvailable(manualTimingWorkflow, "story_bible", scenes);
  const blockStoryboardPassReady = isManualTimingStageAvailable(manualTimingWorkflow, "block_storyboard", scenes);
  const storyBibleButtonTitle = storyBiblePassReady ? "Скопировать JSON для библии истории" : "Сначала примените этап: Смысловая нарезка";
  const blockStoryboardButtonTitle = blockStoryboardPassReady ? "Скопировать JSON для блочной раскадровки" : "Сначала примените этап: Библия истории";
  const passReadyForDirector = project.timing_status === "confirmed"
    && scenes.length > 0
    && (
      (manualTimingWorkflow.completed_stages.includes("block_storyboard") && hasRealStoryBlocks(storyBlocks) && scenes.every(sceneHasCompleteStoryPassFields))
      || (isMusicClip && hasNonEmptyArray(project.song_blocks) && scenes.every(sceneHasCompleteClipPassFields))
      || (isPodcastDialogue && hasNonEmptyArray(project.speakers) && hasNonEmptyArray(project.topic_blocks) && scenes.every(sceneHasCompletePodcastPassFields))
    );
  const storyPassReadyForDirector = passReadyForDirector;
  const openDirectorBoardTitle = passReadyForDirector ? "Открыть режиссёрскую доску" : `Сначала примените ${workflowLabels.pass} JSON и подтвердите тайминг`;
  const selectedSceneText = useMemo(() => getSceneStoryText(scenes.find((scene) => scene.scene_id === project.selectedSceneId) || scenes[0] || null), [scenes, project.selectedSceneId]);
  const selectedScene = useMemo(
    () => scenes.find((scene) => scene.scene_id === project.selectedSceneId) || scenes[0] || null,
    [scenes, project.selectedSceneId]
  );
  const selectedSceneIndex = useMemo(
    () => scenes.findIndex((scene) => scene.scene_id === selectedScene?.scene_id),
    [scenes, selectedScene?.scene_id]
  );
  const quickEditScene = useMemo(
    () => scenes.find((scene) => scene.scene_id === quickEditSceneId) || null,
    [scenes, quickEditSceneId]
  );
  const groupSelectedSceneIdSet = useMemo(
    () => new Set(groupSelectedSceneIds.map((sceneId) => String(sceneId || "")).filter(Boolean)),
    [groupSelectedSceneIds]
  );
  const selectedGroupScenes = useMemo(
    () => scenes
      .filter((scene) => groupSelectedSceneIdSet.has(String(scene.scene_id || "")))
      .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0)),
    [scenes, groupSelectedSceneIdSet]
  );
  const isGroupPhraseInspectorMode = selectedGroupScenes.length > 0;
  const warnings = useMemo(() => buildManualTimingWarnings(project), [project]);
  const compactWarningItems = useMemo(() => getCompactWarningItems(project, warnings), [project, warnings]);
  const readableTimingStatus = getReadableTimingStatus(project, audioPhrases, scenes, durationSec);
  const warningsSummary = getCompactWarningsSummary(compactWarningItems);
  const lastCutSec = getLastInternalMarker(markers);
  const candidateDurationSec = Math.max(0, Number(currentTime || 0) - Number(lastCutSec || 0));
  const selectedSceneStartSec = selectedScene ? Number(selectedScene.start_sec || 0) : 0;
  const selectedSceneEndSec = selectedScene ? Number(selectedScene.end_sec || 0) : 0;
  const selectedSceneDurationSec = selectedScene
    ? Number(selectedScene.duration_sec || (selectedSceneEndSec - selectedSceneStartSec))
    : 0;
  const selectedSceneSpeechStartSec = selectedScene ? Number(selectedScene.speech_start_sec ?? selectedSceneStartSec) : 0;
  const selectedSceneSpeechEndSec = selectedScene ? Number(selectedScene.speech_end_sec ?? selectedSceneEndSec) : 0;
  const selectedScenePreSilenceSec = selectedScene ? Number(selectedScene.pre_silence_sec ?? Math.max(0, selectedSceneSpeechStartSec - selectedSceneStartSec)) : 0;
  const selectedScenePostSilenceSec = selectedScene ? Number(selectedScene.post_silence_sec ?? Math.max(0, selectedSceneEndSec - selectedSceneSpeechEndSec)) : 0;
  const selectedSceneSourcePhraseIds = selectedScene && Array.isArray(selectedScene.source_phrase_ids) ? selectedScene.source_phrase_ids : [];
  const selectedSceneDurationWarning = selectedScene
    ? getManualTimingSceneDurationWarning(selectedScene)
    : null;
  const selectedSceneAudioPhrases = useMemo(
    () => getManualTimingPhrasesForScene(audioPhrases, selectedScene),
    [audioPhrases, selectedScene]
  );
  const selectedSceneInspectorPhrases = useMemo(
    () => getManualTimingPhrasesForSceneInspector(audioPhrases, selectedScene),
    [audioPhrases, selectedScene]
  );
  const selectedGroupInspectorPhrases = useMemo(
    () => getManualTimingPhrasesForSceneGroupInspector(audioPhrases, selectedGroupScenes),
    [audioPhrases, selectedGroupScenes]
  );
  const activeInspectorPhrases = isGroupPhraseInspectorMode
    ? selectedGroupInspectorPhrases
    : selectedSceneInspectorPhrases;
  const selectedScenePhraseInspectorRows = useMemo(
    () => activeInspectorPhrases.map((phrase) => {
      const ruText = pickManualTimingAudioPhraseRuText(phrase);
      return {
        phrase,
        phraseId: String(phrase?.phrase_id || "").trim(),
        timingLabel: `${formatTimingSec(phrase.start_sec)} → ${formatTimingSec(phrase.end_sec)}`,
        originalText: pickManualTimingAudioPhraseOriginalText(phrase) || "—",
        ruText: ruText || MANUAL_TIMING_RU_TTS_EMPTY_TEXT,
        hasRuText: Boolean(ruText && ruText !== MANUAL_TIMING_RU_TTS_EMPTY_TEXT),
        isPartial: phrase?.isPartial ?? isManualTimingPhrasePartialInScene(phrase, selectedScene),
      };
    }),
    [activeInspectorPhrases, selectedScene]
  );
  const selectedSceneFullRuText = useMemo(
    () => selectedScenePhraseInspectorRows
      .map((row) => row.ruText)
      .filter((text) => text && text !== MANUAL_TIMING_RU_TTS_EMPTY_TEXT)
      .join(" "),
    [selectedScenePhraseInspectorRows]
  );
  const selectedSceneHasRuText = Boolean(selectedSceneFullRuText);
  const selectedSceneHasPartialPhrase = selectedScenePhraseInspectorRows.some((row) => row.isPartial);
  const selectedGroupStartSec = selectedGroupScenes.length ? Math.min(...selectedGroupScenes.map((scene) => Number(scene?.start_sec || 0))) : 0;
  const selectedGroupEndSec = selectedGroupScenes.length ? Math.max(...selectedGroupScenes.map((scene) => Number(scene?.end_sec || 0))) : 0;
  const selectedGroupFirstSceneId = selectedGroupScenes[0]?.scene_id || "—";
  const selectedGroupLastSceneId = selectedGroupScenes[selectedGroupScenes.length - 1]?.scene_id || "—";
  const selectedScenePhraseWarnings = useMemo(
    () => getScenePhraseAlignmentWarnings(selectedScene, selectedSceneAudioPhrases, audioPhrases, scenes, durationSec),
    [selectedScene, selectedSceneAudioPhrases, audioPhrases, scenes, durationSec]
  );
  const asrPhraseMarkers = useMemo(() => {
    if (!(durationSec > 0)) return [];
    return audioPhrases.map((phrase) => ({ ...phrase, style: getAsrPhraseStyle(phrase, durationSec) }));
  }, [audioPhrases, durationSec]);
  const selectedMissingPhrase = useMemo(
    () => audioPhrases.find((phrase) => String(phrase.phrase_id || "") === String(selectedMissingPhraseId || "")) || null,
    [audioPhrases, selectedMissingPhraseId]
  );
  const visibleAudioPhrases = useMemo(() => {
    if (!selectedMissingPhrase) return selectedSceneAudioPhrases;
    const hasSelected = selectedSceneAudioPhrases.some((phrase) => String(phrase.phrase_id || "") === String(selectedMissingPhrase.phrase_id || ""));
    return hasSelected ? selectedSceneAudioPhrases : [selectedMissingPhrase, ...selectedSceneAudioPhrases];
  }, [selectedMissingPhrase, selectedSceneAudioPhrases]);
  const selectedBoundarySec = selectedSceneEndSec;
  const canMergeSelectedWithNext = selectedSceneIndex >= 0 && selectedSceneIndex < scenes.length - 1;
  const selectedBoundaryIsInternal = canMergeSelectedWithNext;
  const selectedSceneIsSilence = isManualTimingSilenceScene(selectedScene);
  const canUseTrackNudge = Boolean(selectedScene && (selectedSceneIsSilence || selectedBoundaryIsInternal));
  const timelineWidthPx = Math.ceil(Math.max(
    timelineViewportWidth || 0,
    MANUAL_TIMING_TIMELINE_MIN_WIDTH_PX,
    (durationSec || 0) * MANUAL_TIMING_TIMELINE_PIXELS_PER_SECOND
  ));
  const timelineInnerStyle = useMemo(() => ({
    "--manual-timing-timeline-width": `${timelineWidthPx}px`,
  }), [timelineWidthPx]);
  const playheadPercent = durationSec > 0 ? Math.max(0, Math.min(100, (Number(currentTime || 0) / durationSec) * 100)) : 0;
  const lastCutPercent = durationSec > 0 ? Math.max(0, Math.min(100, (Number(lastCutSec || 0) / durationSec) * 100)) : 0;
  const candidateWidthPercent = durationSec > 0 ? Math.max(0, Math.min(100 - lastCutPercent, ((Number(currentTime || 0) - Number(lastCutSec || 0)) / durationSec) * 100)) : 0;
  const openTailSceneId = project.timing_status === "confirmed" ? "" : scenes[scenes.length - 1]?.scene_id || "";
  const candidateDurationLabel = candidateDurationSec > 0.001 ? formatTimingSec(candidateDurationSec) : "—";
  const storyBlockSummaries = useMemo(() => storyBlocks.map((block) => {
    const derived = deriveStoryBlockRangeFromScenes(block, scenes);
    const sceneCount = Number(derived?.scene_count || 0);
    return {
      ...block,
      ...(derived || { scene_ids: [], start_sec: 0, end_sec: 0 }),
      sceneCount,
    };
  }).filter((block) => String(block.block_id || "") !== MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id), [storyBlocks, scenes]);
  const missingPhraseTimelineMarkers = useMemo(() => {
    if (!(durationSec > 0)) return [];
    return audioPhrases
      .filter(isUnresolvedAudioPhrase)
      .map((phrase) => {
        const start = clampTime(phrase.start_sec, durationSec);
        const end = clampTime(phrase.end_sec, durationSec);
        if (!(end > start)) return null;
        return {
          ...phrase,
          start_sec: start,
          end_sec: end,
          left: `${Math.max(0, Math.min(100, (start / durationSec) * 100))}%`,
          width: `${Math.max(0.35, Math.min(100, ((end - start) / durationSec) * 100))}%`,
          timing_scene_id: getTimingSceneIdForAudioPhrase(phrase, scenes),
        };
      })
      .filter(Boolean);
  }, [audioPhrases, durationSec, scenes]);

  const timelineBlockRanges = useMemo(() => {
    if (!(durationSec > 0) || !storyBlocks.length) return [];
    const unknownBlockId = String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || "");

    return storyBlocks.map((block) => {
      const blockId = String(block?.block_id || "");
      const isUnknownBlock = blockId === unknownBlockId;
      const derived = deriveStoryBlockRangeFromScenes(block, scenes);

      if (isUnknownBlock) return null;
      if (!derived) return null;

      const safeStart = clampTime(derived.start_sec, durationSec);
      const safeEnd = clampTime(derived.end_sec, durationSec);
      if (!(safeEnd > safeStart)) return null;

      return {
        ...derived,
        block_id: blockId,
        title: String(block?.title_ru || blockId || "Story block"),
        start_sec: safeStart,
        end_sec: safeEnd,
        sceneCount: Number(derived.scene_count || 0),
      };
    }).filter(Boolean);
  }, [durationSec, storyBlocks, scenes]);


  useEffect(() => {
    const viewport = timelineViewportRef.current;
    if (!viewport) return undefined;

    const updateViewportWidth = () => {
      setTimelineViewportWidth(Math.round(viewport.getBoundingClientRect().width || 0));
    };

    updateViewportWidth();

    if (typeof ResizeObserver === "undefined") {
      window.addEventListener("resize", updateViewportWidth);
      return () => window.removeEventListener("resize", updateViewportWidth);
    }

    const observer = new ResizeObserver(updateViewportWidth);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    console.info("[MANUAL TIMING TIMELINE LAYOUT]", {
      audioDurationSec: durationSec,
      timelineWidthPx,
      viewportWidth: timelineViewportWidth,
      pixelsPerSecond: MANUAL_TIMING_TIMELINE_PIXELS_PER_SECOND,
      sceneCount: scenes.length,
      scrollLeft: timelineScrollLeft,
    });
  }, [durationSec, timelineWidthPx, timelineViewportWidth, scenes.length, timelineScrollLeft]);

  useEffect(() => {
    currentTimeRef.current = Number(currentTime || 0);
  }, [currentTime]);

  useEffect(() => {
    durationSecRef.current = Number(durationSec || 0);
  }, [durationSec]);

  useEffect(() => {
    console.info("[MANUAL TIMING OWNER RESOLVED]", {
      routeSourceNodeId,
      projectSourceNodeId,
      fallbackOwnerNodeId,
      finalOwnerNodeId,
    });
  }, [routeSourceNodeId, projectSourceNodeId, fallbackOwnerNodeId, finalOwnerNodeId]);

  useEffect(() => {
    const navState = location?.state || {};
    const requestedOwnerNodeId = String(
      navState.sourceNodeId
      || navState.ownerNodeId
      || navState.focusManualTimingNodeId
      || ""
    ).trim();
    const shouldHydrateReturn = Boolean(
      navState.returnFromStoryboard === true
      || navState.openManualTimingNode === true
      || navState.fromPodcastComposer === true
      || navState.replaceAudio === true
      || requestedOwnerNodeId
    );
    const ownerNodeId = requestedOwnerNodeId || finalOwnerNodeId;
    const hydrateKey = `${location?.key || "manual-timing"}:${ownerNodeId}`;
    if (!shouldHydrateReturn || !ownerNodeId || storyboardReturnHydrateKeyRef.current === hydrateKey) return;

    const storedProject = readManualTimingProjectForNode(ownerNodeId);
    const incomingProject = getIncomingManualTimingProjectFromLocation(location, storedProject || project, ownerNodeId);
    const incomingSignature = incomingProject ? getManualTimingAudioSignature(incomingProject) : "";
    const storedSignature = storedProject ? getManualTimingAudioSignature(storedProject) : "";
    const changed = manualTimingAudioSignaturesDiffer(incomingSignature, storedSignature);

    if (incomingProject) {
      console.info("[MANUAL TIMING INCOMING_AUDIO_SIGNATURE]", {
        incomingSignature,
        storedSignature,
        changed,
      });
    }

    storyboardReturnHydrateKeyRef.current = hydrateKey;

    if (incomingProject && (changed || !storedProject)) {
      if (changed && storedProject) {
        console.info("[MANUAL TIMING STALE_ACTIVE_BOARD_IGNORED]", {
          reason: "audio_signature_changed",
          incomingAudioDurationSec: getManualTimingAudioDurationForDiagnostics(incomingProject),
          storedAudioDurationSec: getManualTimingAudioDurationForDiagnostics(storedProject),
          incomingAudioName: getManualTimingAudioNameForDiagnostics(incomingProject),
          storedAudioName: getManualTimingAudioNameForDiagnostics(storedProject),
        });
      }
      setProject(incomingProject);
      persistManualTimingProject(incomingProject);
      setActiveBoardProject(getManualTimingBoardForOwnerMatchingProject(ownerNodeId, incomingProject, { logStale: true }));
      setAudioTime(0, { pause: true, clearBound: true });
      setAsrStatus("");
      setHandoffStatus("");
      return;
    }

    if (!storedProject) return;

    const restoredProject = normalizeStoredManualTimingProject(storedProject, ownerNodeId);
    setProject(restoredProject);
    persistManualTimingProject(restoredProject);
    setActiveBoardProject(getManualTimingBoardForOwnerMatchingProject(ownerNodeId, restoredProject, { logStale: true }));
    console.info("[MANUAL TIMING RETURN FROM STORYBOARD]", {
      sourceNodeId: ownerNodeId,
      returnFromStoryboard: navState.returnFromStoryboard === true,
      manualBoardForceProjectId: String(navState.manualBoardForceProjectId || navState.forceProjectId || "").trim(),
      manualBoardForceInputSignature: String(navState.manualBoardForceInputSignature || navState.forceInputSignature || "").trim(),
      manualBoardForceAudioSignature: String(navState.manualBoardForceAudioSignature || navState.forceAudioSignature || "").trim(),
    });
  }, [location?.key, location?.state, finalOwnerNodeId]);

  useEffect(() => {
    setActiveBoardProject(getManualTimingBoardForOwnerMatchingProject(finalOwnerNodeId, project, { logStale: true }));
  }, [finalOwnerNodeId, project]);

  useEffect(() => {
    setGroupSelectedSceneIds((selectedIds) => {
      if (!selectedIds.length) return selectedIds;
      const sceneIds = new Set(scenes.map((scene) => String(scene?.scene_id || "")).filter(Boolean));
      const nextIds = selectedIds.filter((sceneId) => sceneIds.has(String(sceneId || "")));
      return nextIds.length === selectedIds.length ? selectedIds : nextIds;
    });
  }, [scenes]);

  const persist = (nextProject) => {
    const ownerNodeId = String(routeSourceNodeId || nextProject?.sourceNodeId || nextProject?.nodeId || getManualTimingOwnerNodeId(nextProject) || finalOwnerNodeId || "").trim();
    const safeProject = {
      ...nextProject,
      nodeId: ownerNodeId,
      sourceNodeId: ownerNodeId,
      project_mode: nextProject?.project_mode || "",
      project_kind: nextProject?.project_kind || "",
      updatedAt: Date.now(),
    };
    setProject(safeProject);
    persistManualTimingProject(safeProject);
    return safeProject;
  };

  const persistRestoredProject = (snapshotProject) => {
    const ownerNodeId = String(routeSourceNodeId || snapshotProject?.sourceNodeId || snapshotProject?.nodeId || getManualTimingOwnerNodeId(snapshotProject) || finalOwnerNodeId || "").trim();
    const safeProject = {
      ...(snapshotProject || {}),
      nodeId: ownerNodeId,
      sourceNodeId: ownerNodeId,
      updatedAt: Date.now(),
    };
    setProject(safeProject);
    persistManualTimingProject(safeProject);
    return safeProject;
  };

  const rememberManualTimingAction = (label = "действие") => {
    setUndoStack((items) => [
      ...items.slice(-39),
      createManualTimingHistorySnapshot(project, currentTimeRef.current ?? currentTime, label),
    ]);
    setRedoStack([]);
  };

  const restoreManualTimingHistorySnapshot = (snapshot, message) => {
    if (!snapshot?.project) return;
    const restoredProject = persistRestoredProject(snapshot.project);
    const restoredTime = roundTimingSec(Number(snapshot.currentTimeSec || 0));
    durationSecRef.current = Number(restoredProject?.audio?.duration_sec || 0);
    setAudioTime(restoredTime, { pause: true, clearBound: true });
    setCopyStatus(message);
    window.setTimeout(() => setCopyStatus(""), 1800);
  };

  const undoLastManualTimingAction = () => {
    stopManualTimingPlayback();
    const last = undoStack[undoStack.length - 1];
    if (!last) {
      setCopyStatus("Нет действия для отмены");
      window.setTimeout(() => setCopyStatus(""), 1400);
      return;
    }
    setUndoStack((items) => items.slice(0, -1));
    setRedoStack((items) => [
      ...items.slice(-39),
      createManualTimingHistorySnapshot(project, currentTimeRef.current ?? currentTime, "redo"),
    ]);
    restoreManualTimingHistorySnapshot(last, "Отменено последнее действие");
  };

  const redoLastManualTimingAction = () => {
    stopManualTimingPlayback();
    const next = redoStack[redoStack.length - 1];
    if (!next) {
      setCopyStatus("Нет действия для повтора");
      window.setTimeout(() => setCopyStatus(""), 1400);
      return;
    }
    setRedoStack((items) => items.slice(0, -1));
    setUndoStack((items) => [
      ...items.slice(-39),
      createManualTimingHistorySnapshot(project, currentTimeRef.current ?? currentTime, "undo"),
    ]);
    restoreManualTimingHistorySnapshot(next, "Действие возвращено");
  };


  useEffect(() => {
    const rawScenes = Array.isArray(project?.scenes) ? project.scenes : [];
    const currentSignature = JSON.stringify({
      audioDuration: roundTimingSec(Number(project?.audio?.duration_sec || 0)),
      scenes: rawScenes.map((scene) => ({
        id: scene?.scene_id,
        start: roundTimingSec(scene?.start_sec),
        end: roundTimingSec(scene?.end_sec),
        sourceKind: scene?.source_kind,
        sourceStart: Number.isFinite(Number(scene?.source_start_sec)) ? roundTimingSec(scene?.source_start_sec) : null,
        sourceEnd: Number.isFinite(Number(scene?.source_end_sec)) ? roundTimingSec(scene?.source_end_sec) : null,
        isSilence: Boolean(scene?.is_silence || scene?.scene_type === "manual_silence"),
      })),
    });

    if (silenceRepairSignatureRef.current === currentSignature) return;

    const repairedProject = repairManualTimingSilenceTimelineProject(project);
    if (!repairedProject) {
      silenceRepairSignatureRef.current = currentSignature;
      return;
    }

    const repairedScenes = Array.isArray(repairedProject?.scenes) ? repairedProject.scenes : [];
    silenceRepairSignatureRef.current = JSON.stringify({
      audioDuration: roundTimingSec(Number(repairedProject?.audio?.duration_sec || 0)),
      scenes: repairedScenes.map((scene) => ({
        id: scene?.scene_id,
        start: roundTimingSec(scene?.start_sec),
        end: roundTimingSec(scene?.end_sec),
        sourceKind: scene?.source_kind,
        sourceStart: Number.isFinite(Number(scene?.source_start_sec)) ? roundTimingSec(scene?.source_start_sec) : null,
        sourceEnd: Number.isFinite(Number(scene?.source_end_sec)) ? roundTimingSec(scene?.source_end_sec) : null,
        isSilence: Boolean(scene?.is_silence || scene?.scene_type === "manual_silence"),
      })),
    });

    persist(repairedProject);
    durationSecRef.current = Number(repairedProject.audio?.duration_sec || 0);
    setCopyStatus("Таймлайн тишины пересобран: длительность аудио расширена, source-поля восстановлены.");
    window.setTimeout(() => setCopyStatus(""), 2200);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project?.scenes, project?.audio?.duration_sec]);

  const setPlayingState = (value) => {
    const next = Boolean(value);
    isPlayingRef.current = next;
    setIsPlaying(next);
  };

  const setDisplayTime = (timeValue) => {
    const activeDuration = durationSecRef.current || durationSec;
    const time = roundTimingSec(clampTime(timeValue, activeDuration));
    currentTimeRef.current = time;
    setCurrentTime(time);
    return time;
  };

  const stopRafLoop = () => {
    if (rafRef.current) {
      window.cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
  };

  const findSceneIndexForTimelineTime = (timeValue = 0) => {
    if (!scenes.length) return -1;
    const time = roundTimingSec(clampTime(timeValue, durationSecRef.current || durationSec));
    const epsilon = 0.001;
    const index = scenes.findIndex((scene, idx) => {
      const start = roundTimingSec(scene.start_sec);
      const end = roundTimingSec(scene.end_sec);
      const isLast = idx === scenes.length - 1;
      return time >= start - epsilon && (time < end - epsilon || (isLast && time <= end + epsilon));
    });
    return index;
  };

  const getSceneSourceStartSec = (scene = {}) => {
    if (isManualTimingSilenceScene(scene)) return null;
    const explicit = Number(scene.source_start_sec ?? scene.sourceStartSec);
    if (Number.isFinite(explicit)) return roundTimingSec(explicit);
    return roundTimingSec(scene.start_sec);
  };

  const getSceneSourceEndSec = (scene = {}) => {
    if (isManualTimingSilenceScene(scene)) return null;
    const explicit = Number(scene.source_end_sec ?? scene.sourceEndSec);
    if (Number.isFinite(explicit)) return roundTimingSec(explicit);
    const sourceStart = getSceneSourceStartSec(scene);
    return roundTimingSec(Number(sourceStart || 0) + Math.max(0, Number(scene.end_sec || 0) - Number(scene.start_sec || 0)));
  };

  const getSourceTimeForTimelineTime = (timeValue = 0) => {
    const index = findSceneIndexForTimelineTime(timeValue);
    const scene = index >= 0 ? scenes[index] : null;
    if (!scene || isManualTimingSilenceScene(scene)) return null;
    const timelineStart = roundTimingSec(scene.start_sec);
    const timelineEnd = roundTimingSec(scene.end_sec);
    const sourceStart = getSceneSourceStartSec(scene);
    const sourceEnd = getSceneSourceEndSec(scene);
    const offset = roundTimingSec(clampTime(timeValue, timelineEnd) - timelineStart);
    return roundTimingSec(Math.max(sourceStart, Math.min(sourceEnd, sourceStart + offset)));
  };

  const setAudioElementTime = (timeValue) => {
    const audioEl = audioRef.current;
    if (!audioEl) return;
    const sourceTime = getSourceTimeForTimelineTime(timeValue);
    if (sourceTime === null) return;
    try {
      audioEl.currentTime = sourceTime;
    } catch {}
  };

  const syncCurrentTimeFromAudio = ({ force = false } = {}) => {
    const audioEl = audioRef.current;
    if (!audioEl) return currentTimeRef.current;

    // Главное правило: когда audio на паузе, DOM-события pause/seeked/timeupdate
    // не имеют права двигать UI-курсор. Ручной переход уже сам обновляет курсор.
    if (!force && audioEl.paused) return currentTimeRef.current;

    const sceneIndex = findSceneIndexForTimelineTime(currentTimeRef.current);
    const scene = sceneIndex >= 0 ? scenes[sceneIndex] : null;
    if (!scene || isManualTimingSilenceScene(scene)) return currentTimeRef.current;

    const sourceStart = getSceneSourceStartSec(scene);
    const sourceEnd = getSceneSourceEndSec(scene);
    const sourceTime = roundTimingSec(Math.max(sourceStart, Math.min(sourceEnd, Number(audioEl.currentTime || 0))));
    const nextTime = roundTimingSec(Number(scene.start_sec || 0) + Math.max(0, sourceTime - sourceStart));
    const guard = playStartGuardRef.current;

    // После ручного seek браузер иногда на первые тики отдаёт 0.000.
    // Не принимаем этот краткий ложный ноль, но и не заставляем audio.currentTime
    // в цикле, чтобы плеер не прилипал к старту.
    if (!force && guard && Date.now() < guard.until && nextTime < Number(guard.start || 0) - 0.12) {
      return currentTimeRef.current;
    }

    if (guard && Date.now() >= guard.until) {
      playStartGuardRef.current = null;
    }

    return setDisplayTime(nextTime);
  };

  const stopAtBoundedEndIfNeeded = (timeValue) => {
    const audioEl = audioRef.current;
    const endSec = Number(playUntilRef.current);
    if (!audioEl || !Number.isFinite(endSec)) return false;
    const activeDuration = durationSecRef.current || durationSec;
    const safeEnd = roundTimingSec(clampTime(endSec, activeDuration));
    const nextTime = Number.isFinite(Number(timeValue)) ? Number(timeValue) : Number(audioEl.currentTime || currentTimeRef.current || 0);
    const isFullTimeline = manualTimingPlaybackModeRef.current === "full_timeline";
    const endTolerance = isFullTimeline ? 0.025 : 0.012;
    if (nextTime < safeEnd - endTolerance) return false;

    stopManualTimingPlayback();
    setAudioElementTime(safeEnd);
    setDisplayTime(safeEnd);
    return true;
  };

  const startRafLoop = () => {
    stopRafLoop();
    const tick = () => {
      const audioEl = audioRef.current;
      if (!audioEl || audioEl.paused) {
        setPlayingState(false);
        rafRef.current = null;
        return;
      }
      const nextTime = syncCurrentTimeFromAudio();
      if (stopAtBoundedEndIfNeeded(nextTime)) {
        rafRef.current = null;
        return;
      }
      const sceneIndex = findSceneIndexForTimelineTime(nextTime);
      const scene = sceneIndex >= 0 ? scenes[sceneIndex] : null;
      if (scene && !isManualTimingSilenceScene(scene)) {
        const sourceEnd = getSceneSourceEndSec(scene);
        if (Number(audioEl.currentTime || 0) >= sourceEnd - 0.018) {
          const nextTimelineTime = roundTimingSec(scene.end_sec);
          if (stopAtBoundedEndIfNeeded(nextTimelineTime)) {
            rafRef.current = null;
            return;
          }
          rafRef.current = null;
          continuePlaybackFromTimeline(nextTimelineTime, playUntilRef.current);
          return;
        }
      }
      rafRef.current = window.requestAnimationFrame(tick);
    };
    rafRef.current = window.requestAnimationFrame(tick);
  };

  const setAudioTime = (timeValue, { pause = false, clearBound = false } = {}) => {
    const activeDuration = durationSecRef.current || durationSec;
    const time = roundTimingSec(clampTime(timeValue, activeDuration));
    const audioEl = audioRef.current;

    if (clearBound) playUntilRef.current = null;
    playStartGuardRef.current = { start: time, until: Date.now() + 700 };

    if (audioEl) {
      if (pause) {
        audioEl.pause();
        manualTimingPlaybackModeRef.current = "";
        setManualTimingPlaybackMode("");
        setPlayingState(false);
        stopRafLoop();
        stopSilencePlayback();
      }
      setAudioElementTime(time);
    }

    setDisplayTime(time);
    return time;
  };

  const stopSilencePlayback = () => {
    if (silenceRafRef.current) {
      window.cancelAnimationFrame(silenceRafRef.current);
      silenceRafRef.current = null;
    }
  };

  function stopManualTimingPlayback() {
    const audioEl = audioRef.current;
    playUntilRef.current = null;
    playStartGuardRef.current = null;
    manualTimingPlaybackModeRef.current = "";
    setManualTimingPlaybackMode("");
    setPlayingState(false);
    stopRafLoop();
    stopSilencePlayback();

    if (audioEl) {
      try {
        audioEl.pause();
      } catch {}
    }
  }

  const continuePlaybackFromTimeline = (timelineTimeValue, endValue = null) => {
    const audioEl = audioRef.current;
    if (!audioEl) return;

    const activeDuration = durationSecRef.current || durationSec;
    const timelineTime = roundTimingSec(clampTime(timelineTimeValue, activeDuration));
    const boundedEnd = Number(endValue);
    const end = Number.isFinite(boundedEnd) ? roundTimingSec(clampTime(boundedEnd, activeDuration)) : null;
    if (end !== null && timelineTime >= end - 0.012) {
      playUntilRef.current = null;
      setPlayingState(false);
      setDisplayTime(end);
      return;
    }

    const sceneIndex = findSceneIndexForTimelineTime(timelineTime);
    const scene = sceneIndex >= 0 ? scenes[sceneIndex] : null;
    if (!scene) return;

    if (isManualTimingSilenceScene(scene)) {
      const silenceStart = timelineTime;
      const silenceEnd = Math.min(Number(scene.end_sec || 0), end ?? activeDuration);
      const startedAt = performance.now();
      try { audioEl.pause(); } catch {}
      stopRafLoop();
      stopSilencePlayback();
      setDisplayTime(silenceStart);
      setPlayingState(true);

      const tick = () => {
        const elapsed = (performance.now() - startedAt) / 1000;
        const nextTime = roundTimingSec(Math.min(silenceEnd, silenceStart + elapsed));
        setDisplayTime(nextTime);
        if (nextTime >= silenceEnd - 0.012) {
          silenceRafRef.current = null;
          if (end !== null && nextTime >= end - 0.012) {
            stopManualTimingPlayback();
            setDisplayTime(end);
            return;
          }
          continuePlaybackFromTimeline(silenceEnd, end);
          return;
        }
        silenceRafRef.current = window.requestAnimationFrame(tick);
      };
      silenceRafRef.current = window.requestAnimationFrame(tick);
      return;
    }

    stopSilencePlayback();
    playStartGuardRef.current = { start: timelineTime, until: Date.now() + 700 };
    setAudioElementTime(timelineTime);
    setDisplayTime(timelineTime);
    window.setTimeout(() => {
      const activeAudio = audioRef.current;
      if (!activeAudio) return;
      playStartGuardRef.current = { start: timelineTime, until: Date.now() + 700 };
      setAudioElementTime(timelineTime);
      setDisplayTime(timelineTime);
      activeAudio.play().then(() => {
        setPlayingState(true);
        startRafLoop();
      }).catch(() => {
        setPlayingState(false);
      });
    }, 30);
  };

  const playRange = (startValue, endValue = null, playbackMode = "cursor") => {
    const audioEl = audioRef.current;
    if (!audioEl) return;

    const activeDuration = durationSecRef.current || durationSec;
    const start = roundTimingSec(clampTime(startValue, activeDuration));
    const boundedEnd = Number(endValue);
    const end = Number.isFinite(boundedEnd) ? roundTimingSec(clampTime(boundedEnd, activeDuration)) : null;

    if (end !== null && end <= start + 0.02) return;

    stopManualTimingPlayback();
    manualTimingPlaybackModeRef.current = playbackMode;
    setManualTimingPlaybackMode(playbackMode);
    playUntilRef.current = end;

    try {
      audioEl.pause();
    } catch {}
    setPlayingState(false);
    setDisplayTime(start);
    continuePlaybackFromTimeline(start, end);
  };

  function playFullManualTiming() {
    const audioEl = audioRef.current;
    if (!audioEl || !audio.url) return;

    const startSec = 0;
    const endSec = durationSec || audio.duration_sec || audioEl.duration || 0;
    if (!(endSec > 0)) return;

    stopManualTimingPlayback();

    manualTimingPlaybackModeRef.current = "full_timeline";
    setManualTimingPlaybackMode("full_timeline");
    playUntilRef.current = endSec;

    try {
      audioEl.currentTime = startSec;
    } catch {}

    setCurrentTime(startSec);
    setDisplayTime(startSec);

    try {
      continuePlaybackFromTimeline(startSec, endSec);
    } catch (error) {
      console.warn("[MANUAL TIMING PLAY_FULL_FAILED]", error);
      stopManualTimingPlayback();
    }
  }

  const buildHydratedScenesForDuration = (nextMarkers, existingScenes = scenes, nextDurationSec = durationSec, silenceRanges = [], options = {}) => {
    const safeMarkers = normalizeManualTimingMarkers(nextMarkers, nextDurationSec);
    const nextRawScenes = buildManualTimingScenesFromMarkers(safeMarkers, existingScenes, {
      durationSec: nextDurationSec,
      allowIdFallback: Boolean(options.allowIdFallback),
    });
    const hydratedScenes = hydrateManualTimingScenesWithStoryBlocks(nextRawScenes, project.story_blocks);
    const safeExistingScenes = Array.isArray(existingScenes) ? existingScenes : [];
    const safeSilenceRanges = (Array.isArray(silenceRanges) ? silenceRanges : [])
      .map((range) => ({
        start_sec: roundTimingSec(range?.start_sec),
        end_sec: roundTimingSec(range?.end_sec),
      }))
      .filter((range) => range.end_sec > range.start_sec);

    return hydratedScenes.map((scene) => {
      const oldById = safeExistingScenes.find((item) => String(item?.scene_id || "") === String(scene?.scene_id || ""));
      const oldByExactRange = safeExistingScenes.find((item) => Math.abs(roundTimingSec(item?.start_sec) - roundTimingSec(scene.start_sec)) < 0.001
        && Math.abs(roundTimingSec(item?.end_sec) - roundTimingSec(scene.end_sec)) < 0.001);
      const shouldKeepSilence = isManualTimingSilenceScene(oldById) || isManualTimingSilenceScene(oldByExactRange)
        || safeSilenceRanges.some((range) => Math.abs(roundTimingSec(scene.start_sec) - range.start_sec) < 0.001
          && Math.abs(roundTimingSec(scene.end_sec) - range.end_sec) < 0.001);
      return shouldKeepSilence ? decorateManualTimingSilenceScene(scene) : scene;
    });
  };

  const rebuildFromMarkers = (nextMarkers, existingScenes = scenes, extraPatch = {}, options = {}) => {
    const safeMarkers = normalizeManualTimingMarkers(nextMarkers, durationSec);
    const nextRawScenes = buildManualTimingScenesFromMarkers(safeMarkers, existingScenes, {
      durationSec,
      allowIdFallback: Boolean(options.allowIdFallback),
    });
    const hydratedScenes = hydrateManualTimingScenesWithStoryBlocks(nextRawScenes, project.story_blocks);
    const nextScenes = options.allowIdFallback && hydratedScenes.length === (Array.isArray(existingScenes) ? existingScenes.length : 0)
      ? hydratedScenes.map((scene) => {
        const oldScene = (Array.isArray(existingScenes) ? existingScenes : []).find((item) => String(item?.scene_id || "") === String(scene?.scene_id || ""));
        if (!oldScene) return scene;
        return {
          ...scene,
          story_time: String(oldScene.story_time || ""),
          drama_hint: String(oldScene.drama_hint || ""),
          short_note: String(oldScene.short_note || ""),
          scene_goal_ru: String(oldScene.scene_goal_ru || ""),
          photo_prompt_hint_ru: String(oldScene.photo_prompt_hint_ru || ""),
          prompt_hint_ru: String(oldScene.prompt_hint_ru || oldScene.photo_prompt_hint_ru || ""),
          story_position_ru: String(oldScene.story_position_ru || oldScene.story_time || ""),
          user_note_ru: String(oldScene.user_note_ru || oldScene.user_notes_ru || ""),
          source_phrase_ids: Array.isArray(oldScene.source_phrase_ids) ? oldScene.source_phrase_ids : [],
          story_block_id: String(oldScene.story_block_id || scene.story_block_id || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id),
          story_block_title_ru: String(oldScene.story_block_title_ru || scene.story_block_title_ru || ""),
          story_block_color: String(oldScene.story_block_color || scene.story_block_color || ""),
          story_block_position_ru: String(oldScene.story_block_position_ru || scene.story_block_position_ru || ""),
          scene_role_in_block_ru: String(oldScene.scene_role_in_block_ru || ""),
          block_progress_ru: String(oldScene.block_progress_ru || ""),
          scene_global_context_ru: String(oldScene.scene_global_context_ru || ""),
          continuity_anchor_ru: String(oldScene.continuity_anchor_ru || ""),
          must_match_project_identity_ru: String(oldScene.must_match_project_identity_ru || ""),
          must_match_block_style_ru: String(oldScene.must_match_block_style_ru || ""),
          original_text: String(oldScene.original_text || ""),
          translated_text_ru: String(oldScene.translated_text_ru || ""),
          meaning_hint_ru: String(oldScene.meaning_hint_ru || ""),
          source_text_en: String(oldScene.source_text_en || ""),
          adapted_text_en: String(oldScene.adapted_text_en || ""),
        };
      })
      : hydratedScenes;
    const selectedSceneId = extraPatch.selectedSceneId || project.selectedSceneId || nextScenes[0]?.scene_id || "";
    return persist({
      ...project,
      ...extraPatch,
      markers: safeMarkers,
      story_blocks: normalizeManualTimingStoryBlocks(project.story_blocks),
      scenes: nextScenes,
      selectedSceneId,
      format: projectFormat,
      aspect_ratio: projectFormat,
      format_locked: Boolean(extraPatch.format_locked ?? project.format_locked),
      timing_status: extraPatch.timing_status || (nextScenes.length ? "draft" : project.timing_status || "draft"),
    });
  };

  useEffect(() => () => {
    stopRafLoop();
    stopSilencePlayback();
  }, []);

  useEffect(() => {
    if (!selectedScene && scenes[0]) {
      persist({ ...project, selectedSceneId: scenes[0].scene_id });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedScene?.scene_id, scenes.length]);

  useEffect(() => {
    if (selectedMissingPhraseId && !selectedMissingPhrase) setSelectedMissingPhraseId("");
  }, [selectedMissingPhraseId, selectedMissingPhrase]);

  const onTimeUpdate = () => {
    const audioEl = audioRef.current;
    if (!audioEl) return;

    // Не принимаем timeupdate от audio, когда он на паузе: именно это
    // сбрасывало курсор назад после ручного перехода и pause.
    if (audioEl.paused) {
      setPlayingState(false);
      return;
    }

    const nextTime = syncCurrentTimeFromAudio();
    if (stopAtBoundedEndIfNeeded(nextTime)) return;
    setPlayingState(true);
  };

  const getSelectedSceneBounds = () => {
    if (!selectedScene) return null;
    const start = roundTimingSec(clampTime(Number(selectedScene.start_sec || 0), durationSecRef.current || durationSec));
    const end = roundTimingSec(clampTime(Number(selectedScene.end_sec || 0), durationSecRef.current || durationSec));
    if (!(end > start + 0.02)) return null;
    return { start, end };
  };

  const selectSceneAndSeekStart = (scene, { pause = true } = {}) => {
    if (!scene) return;
    playUntilRef.current = null;
    persist({ ...project, selectedSceneId: scene.scene_id });
    setAudioTime(Number(scene.start_sec || 0), { pause, clearBound: true });
  };

  const onPlayPause = () => {
    const audioEl = audioRef.current;
    if (!audioEl) return;

    if (!audioEl.paused || isPlayingRef.current) {
      const rawTime = Number(audioEl.currentTime);
      const trustedTime = audioEl.paused
        ? Number(currentTimeRef.current || 0)
        : (Number.isFinite(rawTime) && rawTime > 0.03
          ? rawTime
          : Number(currentTimeRef.current || 0));
      const pausedAt = roundTimingSec(clampTime(trustedTime, durationSecRef.current || durationSec));
      stopManualTimingPlayback();
      setAudioElementTime(pausedAt);
      setDisplayTime(pausedAt);
      return;
    }

    const bounds = getSelectedSceneBounds();
    if (bounds) {
      const cursor = Number(currentTimeRef.current || 0);
      const isInsideSelected = cursor >= bounds.start - 0.035 && cursor < bounds.end - 0.035;
      const startFrom = isInsideSelected ? cursor : bounds.start;
      playRange(startFrom, bounds.end, "selected_scene");
      return;
    }

    playRange(currentTimeRef.current || 0, durationSec, "cursor");
  };

  const onStartOver = () => {
    playUntilRef.current = null;
    setAudioTime(0, { pause: true, clearBound: true });
  };

  const onPlayFromLastCut = () => {
    playRange(lastCutSec, durationSec, "cursor");
  };

  const onPlayAroundCursor = () => {
    const center = Number(currentTimeRef.current || currentTime || 0);
    const start = roundTimingSec(Math.max(0, center - 1));
    const end = roundTimingSec(Math.min(durationSecRef.current || durationSec, center + 1));
    playRange(start, end, "cursor");
  };

  const onJumpToTime = () => {
    const parsed = getSecFromTimingParts(jumpTimeParts);
    if (parsed === null) {
      setCopyStatus("Введите минуты, секунды и миллисекунды");
      window.setTimeout(() => setCopyStatus(""), 1800);
      return;
    }
    const nextTime = setAudioTime(parsed, { pause: true, clearBound: true });
    setJumpTimeParts(getTimingPartsFromSec(nextTime));
  };

  const onJumpKeyDown = (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      onJumpToTime();
    }
  };

  const updateJumpPart = (key, value) => {
    const maxLen = key === "ms" ? 3 : (key === "sec" ? 2 : 4);
    const clean = String(value || "").replace(/\D/g, "").slice(0, maxLen);
    setJumpTimeParts((prev) => ({ ...prev, [key]: clean }));
  };

  const normalizeJumpPartOnBlur = (key) => {
    setJumpTimeParts((prev) => {
      if (key === "min") return { ...prev, min: String(Number(prev.min || 0)) };
      if (key === "sec") return { ...prev, sec: padTimingPart(prev.sec, 2) };
      return { ...prev, ms: padTimingPart(prev.ms, 3) };
    });
  };

  const useCurrentTimeForJump = () => {
    setJumpTimeParts(getTimingPartsFromSec(currentTimeRef.current || currentTime));
  };

  const shiftMarkersFromBoundary = (safeMarkers = [], markerIndex = 0, nextTimeValue = 0) => {
    const lastIndex = safeMarkers.length - 1;
    if (markerIndex <= 0 || markerIndex >= lastIndex) return safeMarkers;

    const currentMarker = Number(safeMarkers[markerIndex] || 0);
    const requestedNext = roundTimingSec(Number(nextTimeValue || 0));
    const prevMarker = Number(safeMarkers[markerIndex - 1] || 0);
    const lastInternalMarker = Number(safeMarkers[lastIndex - 1] || currentMarker);

    const minDelta = (prevMarker + 0.25) - currentMarker;
    const maxDelta = (durationSec - 0.25) - lastInternalMarker;
    const requestedDelta = requestedNext - currentMarker;
    if ((requestedDelta > 0 && maxDelta <= 0) || (requestedDelta < 0 && minDelta >= 0) || maxDelta < minDelta) return safeMarkers;
    const delta = roundTimingSec(Math.min(maxDelta, Math.max(minDelta, requestedDelta)));
    if (Math.abs(delta) < 0.001) return safeMarkers;

    return safeMarkers.map((marker, idx) => {
      if (idx >= markerIndex && idx < lastIndex) return roundTimingSec(Number(marker) + delta);
      return marker;
    });
  };

  const addMarkerAt = (timeValue) => {
    if (!(durationSec > 0)) return;
    const time = roundTimingSec(clampTime(timeValue, durationSec));
    if (time <= 0.001 || time >= durationSec - 0.001) {
      setCopyStatus("Разрез нельзя поставить в самом начале или конце аудио");
      window.setTimeout(() => setCopyStatus(""), 1600);
      return;
    }
    const safeMarkers = normalizeManualTimingMarkers(project.markers, durationSec);
    const tooClose = safeMarkers.some((marker) => Math.abs(Number(marker) - time) < 0.15);
    if (tooClose) {
      setCopyStatus("Слишком близко к существующему разрезу");
      window.setTimeout(() => setCopyStatus(""), 1600);
      return;
    }

    // Если ставим разрез внутри уже размеченного участка, просто добавляем новую границу.
    // Следующие сцены не удаляются: они остаются на своих местах.
    rememberManualTimingAction("разрез");
    const nextMarkers = [...safeMarkers, time];
    const normalized = normalizeManualTimingMarkers(nextMarkers, durationSec);
    const boundaryIndex = normalized.findIndex((marker) => Math.abs(Number(marker) - time) < 0.001);
    const selectedSceneId = boundaryIndex > 0 ? getSceneIdForIndex(boundaryIndex - 1) : project.selectedSceneId;
    const nextProject = rebuildFromMarkers(normalized, scenes, {
      selectedSceneId,
      timing_status: "draft",
      manual_scene_edits: true,
      manualSceneEdits: true,
      lastManualEditReason: "cut_scene",
    });
    console.info("[MANUAL TIMING MANUAL CUT APPLIED]", {
      timeSec: time,
      selectedSceneId,
      sceneCountBefore: scenes.length,
      sceneCountAfter: Array.isArray(nextProject?.scenes) ? nextProject.scenes.length : scenes.length + 1,
      manualSceneEdits: true,
    });
    setAudioTime(time, { pause: true, clearBound: true });
  };

  const onAddMarker = () => {
    stopManualTimingPlayback();
    addMarkerAt(currentTimeRef.current ?? currentTime);
  };

  const mergeSelectedSceneWithNext = () => {
    stopManualTimingPlayback();
    if (!canMergeSelectedWithNext) return;

    const current = scenes[selectedSceneIndex];
    const next = scenes[selectedSceneIndex + 1];
    if (!current || !next) return;

    const currentSceneId = String(current.scene_id || "").trim();
    const nextSceneId = String(next.scene_id || "").trim();
    const startSec = roundTimingSec(Number(current.start_sec || 0));
    const endSec = roundTimingSec(Number(next.end_sec || current.end_sec || startSec));
    const duration = roundTimingSec(Math.max(0, endSec - startSec));
    const mergedSourcePhraseIds = mergeManualTimingSourcePhraseIds(current.source_phrase_ids, next.source_phrase_ids);
    const sameStoryBlock = String(current.story_block_id || "").trim()
      && String(current.story_block_id || "").trim() === String(next.story_block_id || "").trim();

    const mergedScene = {
      ...current,
      scene_id: currentSceneId || nextSceneId || getSceneIdForIndex(selectedSceneIndex),
      start_sec: startSec,
      end_sec: endSec,
      duration_sec: duration,
      speech_start_sec: roundTimingSec(Math.min(
        Number(current.speech_start_sec ?? current.start_sec ?? startSec),
        Number(next.speech_start_sec ?? next.start_sec ?? endSec)
      )),
      speech_end_sec: roundTimingSec(Math.max(
        Number(current.speech_end_sec ?? current.end_sec ?? startSec),
        Number(next.speech_end_sec ?? next.end_sec ?? endSec)
      )),
      post_silence_sec: Number(next.post_silence_sec ?? current.post_silence_sec ?? 0),
      source_phrase_ids: mergedSourcePhraseIds,
      route: getMergedManualTimingRoute(current.route, next.route),
      text: mergeManualTimingTextValue(current.text, next.text),
      text_ru: mergeManualTimingTextValue(current.text_ru, next.text_ru),
      source_text: mergeManualTimingTextValue(current.source_text, next.source_text),
      source_text_ru: mergeManualTimingTextValue(current.source_text_ru, next.source_text_ru),
      source_text_en: mergeManualTimingTextValue(current.source_text_en, next.source_text_en),
      original_text: mergeManualTimingTextValue(current.original_text, next.original_text),
      translated_text_ru: mergeManualTimingTextValue(current.translated_text_ru, next.translated_text_ru),
      notes: mergeManualTimingTextValue(current.notes, next.notes),
      note_ru: mergeManualTimingTextValue(current.note_ru, next.note_ru),
      short_note: mergeManualTimingTextValue(current.short_note, next.short_note),
      story_block_id: String(current.story_block_id || "").trim() || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id,
      story_block_title_ru: String(current.story_block_title_ru || ""),
      story_block_color: String(current.story_block_color || ""),
    };

    if (sameStoryBlock) {
      mergedScene.story_block_id = String(current.story_block_id || "").trim();
      mergedScene.story_block_title_ru = String(current.story_block_title_ru || next.story_block_title_ru || "");
      mergedScene.story_block_color = String(current.story_block_color || next.story_block_color || "");
    }

    const nextScenes = [
      ...scenes.slice(0, selectedSceneIndex),
      mergedScene,
      ...scenes.slice(selectedSceneIndex + 2),
    ];
    const nextMarkers = normalizeManualTimingMarkers(
      nextScenes.flatMap((scene) => [scene.start_sec, scene.end_sec]),
      durationSec
    );
    const nextStoryBlocks = rebuildManualTimingStoryBlocksForScenes(project.story_blocks, nextScenes);

    rememberManualTimingAction("соединить сцены");
    persist({
      ...project,
      scenes: nextScenes,
      markers: nextMarkers,
      story_blocks: nextStoryBlocks,
      selectedSceneId: mergedScene.scene_id,
      timing_status: "draft",
      manual_scene_edits: true,
      manualSceneEdits: true,
      lastManualEditReason: "merge_scene",
    });

    console.info("[MANUAL TIMING SCENES_MERGED]", {
      selectedSceneId: currentSceneId,
      nextSceneId,
      startSec,
      endSec,
      sceneCountBefore: scenes.length,
      sceneCountAfter: nextScenes.length,
      manualSceneEdits: true,
    });
    setAudioTime(startSec, { pause: true, clearBound: true });
  };

  const getNextMissingPhraseId = (phrases = []) => {
    const used = new Set((Array.isArray(phrases) ? phrases : []).map((phrase) => String(phrase?.phrase_id || "")));
    let idx = 1;
    while (used.has(`manual_missing_${String(idx).padStart(3, "0")}`)) idx += 1;
    return `manual_missing_${String(idx).padStart(3, "0")}`;
  };

  const setMissingPhraseDraftBoundary = (key) => {
    const activeDuration = durationSecRef.current || durationSec;
    if (!(activeDuration > 0)) return;
    const time = roundTimingSec(clampTime(currentTimeRef.current ?? currentTime, activeDuration));
    setMissingPhraseDraft((prev) => ({ ...(prev || {}), [key]: time }));
  };

  const resetMissingPhraseDraft = () => {
    setMissingPhraseDraft({ start_sec: null, end_sec: null });
  };

  const onAddMissingPhrase = () => {
    const activeDuration = durationSecRef.current || durationSec;
    if (!(activeDuration > 0)) return;
    if (!hasTimingDraftValue(missingPhraseDraft.start_sec)) {
      setCopyStatus("Сначала нажмите “Начало из курсора” для пропущенной фразы");
      window.setTimeout(() => setCopyStatus(""), 2200);
      return;
    }
    if (!hasTimingDraftValue(missingPhraseDraft.end_sec)) {
      setCopyStatus("Поставьте конец пропущенной фразы: кнопка “Конец из курсора”");
      window.setTimeout(() => setCopyStatus(""), 2200);
      return;
    }

    const draftStart = Number(missingPhraseDraft.start_sec);
    const draftEnd = Number(missingPhraseDraft.end_sec);

    const start = roundTimingSec(clampTime(Math.min(draftStart, draftEnd), activeDuration));
    const end = roundTimingSec(clampTime(Math.max(draftStart, draftEnd), activeDuration));
    if (!(end > start + 0.02)) {
      setCopyStatus("Диапазон пропущенной фразы слишком короткий");
      window.setTimeout(() => setCopyStatus(""), 1800);
      return;
    }

    const nextPhrase = {
      phrase_id: getNextMissingPhraseId(audioPhrases),
      start_sec: start,
      end_sec: end,
      text_en: "",
      text_ru: "",
      meaning_ru: "",
      status: "needs_transcription",
      assignment_status: "unassigned",
      note_ru: "Пропущенная фраза, нужно распознать",
    };

    persist({
      ...project,
      audio_phrases: normalizeManualTimingAudioPhrases([...audioPhrases, nextPhrase]),
      timing_status: project.timing_status === "empty" ? "draft" : project.timing_status,
    });
    setSelectedMissingPhraseId(nextPhrase.phrase_id);
    resetMissingPhraseDraft();
    setCopyStatus(`Добавлена пропущенная фраза ${nextPhrase.phrase_id}: ${nextPhrase.start_sec.toFixed(2)}–${nextPhrase.end_sec.toFixed(2)}`);
    window.setTimeout(() => setCopyStatus(""), 2000);
  };

  const updateAudioPhraseById = (phraseId, patch = {}) => {
    const phrases = normalizeManualTimingAudioPhrases(project.audio_phrases);
    const fallbackDuration = Math.max(durationSecRef.current || 0, durationSec || 0, ...phrases.map((phrase) => Number(phrase.end_sec || 0)));
    const activeDuration = fallbackDuration > 0 ? fallbackDuration : 0;
    const nextPhrases = phrases.map((phrase) => {
      if (String(phrase.phrase_id || "") !== String(phraseId || "")) return phrase;
      const nextStart = Object.prototype.hasOwnProperty.call(patch, "start_sec")
        ? roundTimingSec(clampTime(patch.start_sec, activeDuration))
        : phrase.start_sec;
      const nextEnd = Object.prototype.hasOwnProperty.call(patch, "end_sec")
        ? roundTimingSec(clampTime(patch.end_sec, activeDuration))
        : phrase.end_sec;
      return normalizeManualTimingAudioPhrases([{ ...phrase, ...patch, start_sec: Math.min(nextStart, nextEnd), end_sec: Math.max(nextStart, nextEnd) }])[0] || phrase;
    });

    persist({
      ...project,
      audio_phrases: nextPhrases,
    });
    setSelectedMissingPhraseId(String(phraseId || ""));
  };

  const updateAudioPhraseBoundaryFromCursor = (phraseId, key) => {
    const activeDuration = durationSecRef.current || durationSec;
    if (!(activeDuration > 0)) return;
    const time = roundTimingSec(clampTime(currentTimeRef.current ?? currentTime, activeDuration));
    updateAudioPhraseById(phraseId, { [key]: time });
  };

  const nudgeAudioPhraseBoundary = (phraseId, key, delta) => {
    const phrase = audioPhrases.find((item) => String(item.phrase_id || "") === String(phraseId || ""));
    if (!phrase) return;
    updateAudioPhraseById(phraseId, { [key]: roundTimingSec(Number(phrase[key] || 0) + Number(delta || 0)) });
  };

  const selectMissingPhrase = (phrase) => {
    if (!phrase) return;
    setSelectedMissingPhraseId(String(phrase.phrase_id || ""));
    setAudioTime(phrase.start_sec, { pause: true, clearBound: true });
  };

  const onDeleteLastMissingPhrase = () => {
    const phrases = normalizeManualTimingAudioPhrases(project.audio_phrases);
    if (!phrases.length) return;
    const deletedPhraseId = phrases[phrases.length - 1]?.phrase_id || "";
    persist({
      ...project,
      audio_phrases: phrases.slice(0, -1),
    });
    if (String(selectedMissingPhraseId || "") === String(deletedPhraseId || "")) setSelectedMissingPhraseId("");
  };

  const onDeleteMissingPhrase = (phraseId) => {
    const phrases = normalizeManualTimingAudioPhrases(project.audio_phrases);
    const nextPhrases = phrases.filter((phrase) => String(phrase.phrase_id || "") !== String(phraseId || ""));
    if (nextPhrases.length === phrases.length) return;
    persist({
      ...project,
      audio_phrases: nextPhrases,
    });
    if (String(selectedMissingPhraseId || "") === String(phraseId || "")) setSelectedMissingPhraseId("");
  };

  const onDeleteLastCut = () => {
    stopManualTimingPlayback();
    if (markers.length <= 2) return;
    const nextMarkers = markers.filter((_, idx) => idx !== markers.length - 2);
    const selectedSceneId = getSceneIdForIndex(Math.max(0, nextMarkers.length - 3));
    rebuildFromMarkers(nextMarkers, scenes, { selectedSceneId, timing_status: "draft" });
    setAudioTime(getLastInternalMarker(nextMarkers), { pause: true, clearBound: true });
  };

  const nudgeSelectedSilenceDuration = (delta) => {
    stopManualTimingPlayback();
    if (!selectedScene || !selectedSceneIsSilence || selectedSceneIndex < 0) {
      setCopyStatus("Выберите блок тишины");
      window.setTimeout(() => setCopyStatus(""), 1600);
      return;
    }

    const activeDuration = durationSecRef.current || durationSec;
    if (!(activeDuration > 0)) return;

    const safeDelta = roundTimingSec(Number(delta || 0));
    const start = roundTimingSec(Number(selectedScene.start_sec || 0));
    const end = roundTimingSec(Number(selectedScene.end_sec || 0));
    const currentDurationValue = roundTimingSec(Math.max(0.01, end - start));
    const nextSilenceDuration = roundTimingSec(Math.max(0.01, Math.min(MANUAL_TIMING_MAX_SILENCE_SEC, currentDurationValue + safeDelta)));
    const appliedDelta = roundTimingSec(nextSilenceDuration - currentDurationValue);

    if (Math.abs(appliedDelta) < 0.001) {
      setCopyStatus(safeDelta < 0 ? "Тишина уже почти нулевая" : "Максимум тишины 30 сек");
      window.setTimeout(() => setCopyStatus(""), 1600);
      return;
    }

    const nextEnd = roundTimingSec(start + nextSilenceDuration);
    const nextDuration = roundTimingSec(Math.max(0.01, activeDuration + appliedDelta));
    rememberManualTimingAction("доводчик тишины");
    const nextScenes = reindexManualTimingTimelineScenes(scenes.map((scene, index) => {
      if (index < selectedSceneIndex) return scene;
      if (index === selectedSceneIndex) {
        return decorateManualTimingSilenceScene(retimeManualTimingScene(scene, start, nextEnd));
      }
      return shiftManualTimingScene(scene, appliedDelta);
    }));
    const nextSelectedScene = nextScenes[selectedSceneIndex] || nextScenes.find((scene) => isManualTimingSilenceScene(scene)) || nextScenes[0];
    const nextMarkers = buildManualTimingMarkersFromScenesList(nextScenes, nextDuration);

    persist({
      ...project,
      audio: {
        ...audio,
        source_duration_sec: Number(audio.source_duration_sec || audio.original_duration_sec || audio.duration_sec || activeDuration),
        original_duration_sec: Number(audio.original_duration_sec || audio.source_duration_sec || audio.duration_sec || activeDuration),
        timeline_duration_sec: nextDuration,
        duration_sec: nextDuration,
        duration_ms: Math.round(nextDuration * 1000),
      },
      markers: nextMarkers,
      story_blocks: normalizeManualTimingStoryBlocks(project.story_blocks),
      audio_phrases: project.audio_phrases,
      virtual_silence_blocks: buildManualTimingSilenceBlocksFromScenes(nextScenes),
      scenes: nextScenes,
      selectedSceneId: nextSelectedScene?.scene_id || selectedScene.scene_id,
      timing_status: "draft",
    });
    setAudioTime(start, { pause: true, clearBound: true });
    setCopyStatus(`Тишина: ${formatTimingSec(nextSilenceDuration)} (${appliedDelta > 0 ? "+" : ""}${appliedDelta.toFixed(3)} сек). Правая часть таймлайна пересчитана.`);
    window.setTimeout(() => setCopyStatus(""), 1800);
  };

  const nudgeSelectedBoundary = (delta) => {
    stopManualTimingPlayback();
    if (selectedSceneIsSilence) {
      nudgeSelectedSilenceDuration(delta);
      return;
    }
    if (!selectedScene || !selectedBoundaryIsInternal) {
      setCopyStatus("Выберите сцену с внутренней конечной границей");
      window.setTimeout(() => setCopyStatus(""), 1600);
      return;
    }
    const markerIndex = selectedSceneIndex + 1;
    const prevMarker = Number(markers[markerIndex - 1] || 0);
    const currentMarker = Number(markers[markerIndex] || 0);
    const minSceneDuration = selectedSceneIsSilence ? 0.01 : 0.25;
    const minTime = prevMarker + minSceneDuration;
    const maxTime = selectedSceneIsSilence
      ? Math.min(prevMarker + MANUAL_TIMING_MAX_SILENCE_SEC, durationSec - 0.01)
      : durationSec - 0.25;
    const nextTime = roundTimingSec(Math.max(minTime, Math.min(maxTime, currentMarker + Number(delta || 0))));
    if (Math.abs(nextTime - currentMarker) < 0.001) return;

    const nextMarkers = shiftMarkersFromBoundary(markers, markerIndex, nextTime);
    const actualTime = Number(nextMarkers[markerIndex] || currentMarker);
    rememberManualTimingAction("микродоводчик");
    rebuildFromMarkers(nextMarkers, scenes, { selectedSceneId: selectedScene.scene_id, timing_status: "draft" }, { allowIdFallback: true });
    setAudioTime(actualTime, { pause: true, clearBound: true });
  };

  const insertSilenceAtTimelineTime = (insertTimeSec, silenceDurationSec = MANUAL_TIMING_INSERT_SILENCE_SEC) => {
    const activeDuration = durationSecRef.current || durationSec;
    if (!(activeDuration > 0)) return;

    const silenceDuration = roundTimingSec(Math.max(0.01, Number(silenceDurationSec || MANUAL_TIMING_INSERT_SILENCE_SEC)));
    const cursor = roundTimingSec(clampTime(Number(insertTimeSec || 0), activeDuration));

    stopManualTimingPlayback();
    const nextDuration = roundTimingSec(activeDuration + silenceDuration);
    const safeScenes = Array.isArray(scenes) && scenes.length
      ? scenes
      : hydrateManualTimingScenesWithStoryBlocks(
        buildManualTimingScenesFromMarkers(project.markers?.length ? project.markers : [0, activeDuration], [], { durationSec: activeDuration }),
        project.story_blocks
      );
    const sourceMappedScenes = safeScenes.map(materializeManualTimingSceneSourceMap);
    const epsilon = 0.001;
    const sourceIndex = sourceMappedScenes.findIndex((scene, index) => {
      const start = roundTimingSec(scene.start_sec);
      const end = roundTimingSec(scene.end_sec);
      const isLast = index === sourceMappedScenes.length - 1;
      return cursor >= start - epsilon && (cursor < end - epsilon || (isLast && cursor <= end + epsilon));
    });
    const insertAtIndex = sourceIndex >= 0 ? sourceIndex : sourceMappedScenes.length - 1;
    const nextSceneDrafts = [];
    let selectedSilenceOrderIndex = 0;

    sourceMappedScenes.forEach((scene, index) => {
      if (index < insertAtIndex) {
        nextSceneDrafts.push(scene);
        return;
      }

      if (index > insertAtIndex) {
        nextSceneDrafts.push(shiftManualTimingScene(scene, silenceDuration));
        return;
      }

      const sceneStart = roundTimingSec(scene.start_sec);
      const sceneEnd = roundTimingSec(scene.end_sec);
      const sceneAfterEnd = roundTimingSec(sceneEnd + silenceDuration);
      const sourceStart = Number.isFinite(Number(scene.source_start_sec)) ? roundTimingSec(scene.source_start_sec) : sceneStart;
      const sourceEnd = Number.isFinite(Number(scene.source_end_sec)) ? roundTimingSec(scene.source_end_sec) : roundTimingSec(sourceStart + Math.max(0, sceneEnd - sceneStart));
      const leftSourceEnd = roundTimingSec(sourceStart + Math.max(0, cursor - sceneStart));

      if (cursor > sceneStart + epsilon) {
        nextSceneDrafts.push(retimeManualTimingScene(scene, sceneStart, cursor, {
          source_kind: "audio",
          source_start_sec: sourceStart,
          source_end_sec: leftSourceEnd,
          user_note_ru: String(scene.user_note_ru || scene.user_notes_ru || "") || "Часть сцены до вставленной тишины.",
        }));
      }

      const silenceScene = decorateManualTimingSilenceScene({
        ...scene,
        scene_id: `silence_${Date.now()}`,
        index: nextSceneDrafts.length + 1,
        start_sec: cursor,
        end_sec: roundTimingSec(cursor + silenceDuration),
        source_start_sec: 0,
        source_end_sec: silenceDuration,
        silence_block_id: `silence_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      });
      selectedSilenceOrderIndex = nextSceneDrafts.length;
      nextSceneDrafts.push(silenceScene);

      if (cursor < sceneEnd - epsilon) {
        const shiftedRight = shiftManualTimingScene(scene, silenceDuration);
        nextSceneDrafts.push(retimeManualTimingScene(shiftedRight, roundTimingSec(cursor + silenceDuration), sceneAfterEnd, {
          source_kind: "audio",
          source_start_sec: leftSourceEnd,
          source_end_sec: sourceEnd,
          user_note_ru: String(scene.user_note_ru || scene.user_notes_ru || "") || "Часть сцены после вставленной тишины.",
        }));
      }
    });

    if (!nextSceneDrafts.some((scene) => isManualTimingSilenceScene(scene))) return;

    const nextScenes = reindexManualTimingTimelineScenes(nextSceneDrafts);
    const nextSelectedScene = nextScenes[selectedSilenceOrderIndex] || nextScenes.find((scene) => isManualTimingSilenceScene(scene));
    const nextMarkers = normalizeManualTimingMarkers(
      [
        0,
        ...nextScenes.map((scene) => Number(scene.end_sec || 0)),
      ],
      nextDuration
    );

    rememberManualTimingAction("вставка тишины");
    persist({
      ...project,
      audio: {
        ...audio,
        source_duration_sec: Number(audio.source_duration_sec || audio.original_duration_sec || audio.duration_sec || activeDuration),
        original_duration_sec: Number(audio.original_duration_sec || audio.source_duration_sec || audio.duration_sec || activeDuration),
        timeline_duration_sec: nextDuration,
        duration_sec: nextDuration,
        duration_ms: Math.round(nextDuration * 1000),
      },
      markers: nextMarkers,
      story_blocks: normalizeManualTimingStoryBlocks(project.story_blocks),
      audio_phrases: project.audio_phrases,
      virtual_silence_blocks: buildManualTimingSilenceBlocksFromScenes(nextScenes),
      scenes: nextScenes,
      selectedSceneId: nextSelectedScene?.scene_id || nextScenes[0]?.scene_id || "",
      timing_status: "draft",
    });
    setAudioTime(cursor, { pause: true, clearBound: true });
    setCopyStatus(`Вставлена тишина ${formatTimingSec(silenceDuration)} в ${formatTimingSec(cursor)}. Аудио разрезано, правая часть сдвинута.`);
    window.setTimeout(() => setCopyStatus(""), 2200);
  };

  const insertSilenceAtCursor = () => {
    const activeDuration = durationSecRef.current || durationSec;
    if (!(activeDuration > 0)) return;

    const audioEl = audioRef.current;
    const liveAudioTime = Number(audioEl?.currentTime);
    const rawCursor = Number.isFinite(liveAudioTime) && liveAudioTime > 0
      ? liveAudioTime
      : Number(currentTimeRef.current ?? currentTime ?? 0);
    const cursor = roundTimingSec(clampTime(rawCursor, activeDuration));

    insertSilenceAtTimelineTime(cursor);
  };

  const insertSilenceBeforeSelectedScene = () => {
    if (!selectedScene) {
      insertSilenceAtCursor();
      return;
    }

    const insertTime = roundTimingSec(clampTime(Number(selectedScene.start_sec || 0), durationSecRef.current || durationSec));
    insertSilenceAtTimelineTime(insertTime, 1);
  };

  const playSegment = (scene) => {
    if (!scene) return;
    const startSec = roundTimingSec(clampTime(Number(scene.start_sec || 0), durationSecRef.current || durationSec));
    const endSec = roundTimingSec(clampTime(Number(scene.end_sec || 0), durationSecRef.current || durationSec));
    if (!(endSec > startSec + 0.02)) return;

    persist({ ...project, selectedSceneId: scene.scene_id });
    playRange(startSec, endSec, "selected_scene");
  };

  const splitSegmentAtCurrentTime = (scene) => {
    if (!scene) return;
    const time = roundTimingSec(currentTimeRef.current ?? currentTime);
    if (time <= Number(scene.start_sec || 0) + 0.15 || time >= Number(scene.end_sec || 0) - 0.15) return;
    addMarkerAt(time);
  };

  const mergeWithNext = (scene) => {
    if (!scene) return;
    const end = roundTimingSec(scene.end_sec);
    const safeMarkers = normalizeManualTimingMarkers(project.markers, durationSec);
    const boundaryIndex = safeMarkers.findIndex((marker) => Math.abs(Number(marker) - end) < 0.001);
    if (boundaryIndex <= 0 || boundaryIndex >= safeMarkers.length - 1) return;
    // Удаляем только выбранную границу, остальные последующие разрезы остаются.
    const nextMarkers = safeMarkers.filter((marker) => Math.abs(Number(marker) - end) > 0.001);
    rebuildFromMarkers(nextMarkers, scenes, { selectedSceneId: scene.scene_id, timing_status: "draft" });
  };

  const deleteCutAfterScene = (scene) => {
    if (!scene) return;
    mergeWithNext(scene);
  };

  const updateScene = (sceneId, patch) => {
    const safePatch = isStoryVoiceover
      ? { ...patch, route: "i2v", contains_vocal: false, contains_vocal_assumption: false, contains_instrumental_assumption: true }
      : patch;
    const nextScenes = updateManualTimingSceneById(scenes, sceneId, safePatch);
    persist({ ...project, scenes: nextScenes, selectedSceneId: sceneId, timing_status: "draft" });
  };

  const openQuickEdit = (scene) => {
    if (!scene?.scene_id) return;
    setQuickEditSceneId(scene.scene_id);
    setQuickEditDraft({
      section: scene.section || "verse",
      route: scene.route || "i2v",
      contains_vocal: Boolean(scene.contains_vocal),
      use_sound_suggestion: Boolean(scene.use_sound_suggestion),
      energy: scene.energy || "mid",
      user_note_ru: String(scene.user_note_ru || ""),
    });
    persist({ ...project, selectedSceneId: scene.scene_id });
  };

  const closeQuickEdit = () => {
    setQuickEditDraft(null);
    setQuickEditSceneId("");
  };

  const applyQuickEdit = () => {
    if (!quickEditSceneId || !quickEditDraft) return;
    const nextScenes = updateManualTimingSceneById(scenes, quickEditSceneId, {
      section: quickEditDraft.section || "verse",
      route: quickEditDraft.route || "i2v",
      contains_vocal: Boolean(quickEditDraft.contains_vocal),
      use_sound_suggestion: Boolean(quickEditDraft.use_sound_suggestion),
      energy: quickEditDraft.energy || "mid",
      user_note_ru: String(quickEditDraft.user_note_ru || ""),
    });
    persist({
      ...project,
      scenes: nextScenes,
      selectedSceneId: quickEditSceneId,
      timing_status: "draft",
    });
    closeQuickEdit();
  };

  useEffect(() => {
    if (!quickEditSceneId || !quickEditDraft) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        closeQuickEdit();
        return;
      }
      if (event.key === "Enter" && !event.shiftKey) {
        const tagName = String(event.target?.tagName || "").toLowerCase();
        if (tagName === "textarea") return;
        event.preventDefault();
        applyQuickEdit();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quickEditSceneId, quickEditDraft]);

  const onCreateAudioPhraseMap = async () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    if (!audio.url) return;
    if (String(audio.url || "").startsWith("blob:")) {
      setAsrStatus("Ошибка ASR: backend не может читать blob URL. Нужно использовать backend/static asset URL или отправить файл через multipart.");
      return;
    }
    setAsrStatus("ASR: распознаю слова и собираю phrase map…");
    try {
      const res = await fetch(`${API_BASE}/api/manual-timing/audio-phrases`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          audio_url: audio.url,
          language: "en",
          split_mode: "pause_based",
          min_pause_sec: 0.45,
          max_phrase_sec: 8.0,
          min_phrase_sec: 1.2,
        }),
      });
      const data = await res.json().catch(() => null);
      if (!res.ok || !data?.ok) throw new Error(data?.detail || data?.message || `HTTP ${res.status}`);
      const nextAudioPhrases = normalizeManualTimingAudioPhrases((data.audio_phrases || []).map((phrase) => ({ ...phrase, source: "asr" })));
      const exactDuration = Number(data.audio_duration_sec || 0);
      const hasExactDuration = exactDuration > 0;
      const nextAudio = hasExactDuration ? {
        ...audio,
        duration_sec: roundTimingSec(exactDuration),
        duration_ms: Math.round(exactDuration * 1000),
      } : audio;
      persist({
        ...project,
        audio: nextAudio,
        audio_words: Array.isArray(data.words) ? data.words : [],
        audio_phrases: nextAudioPhrases,
        asr_phrase_map: {
          status: "ready",
          source: "faster-whisper",
          generatedAt: Date.now(),
          split_settings: data.split_settings || {},
          asr: data.asr || {},
        },
      });
      setAsrStatus(`ASR phrase map готов: ${nextAudioPhrases.length} фраз, ${Array.isArray(data.words) ? data.words.length : 0} слов. Это проверочная карта, не финальный storyboard.`);
      window.setTimeout(() => setAsrStatus(""), 5000);
    } catch (error) {
      setAsrStatus(`Ошибка ASR: ${error?.message || error}`);
    }
  };

  const onOpenAsrVerificationScenes = () => {
    if (!audioPhrases.length) return;
    const confirmed = window.confirm("Это заменит текущие сцены на ASR-preview. Продолжить?");
    if (!confirmed) return;
    const rawAsrScenes = buildAsrVerificationScenes(audioPhrases);
    const asrPreviewBlock = {
      ...MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
      block_id: "block_asr_phrase_map",
      title_ru: "ASR Phrase Map",
      summary_ru: "Проверочная карта фраз по word timestamps.",
      block_goal_ru: "Проверить точные границы произнесённых фраз.",
      block_reveal_ru: "После проверки фразы будут сгруппированы в смысловые сцены.",
      block_emotion_ru: "техническая проверка",
      color: "#64748B",
      scene_ids: rawAsrScenes.map((scene) => scene.scene_id),
      start_sec: rawAsrScenes[0]?.start_sec || 0,
      end_sec: rawAsrScenes[rawAsrScenes.length - 1]?.end_sec || audio.duration_sec || 0,
    };
    const asrScenes = hydrateManualTimingScenesWithStoryBlocks(
      rawAsrScenes.map((scene) => ({
        ...scene,
        story_block_id: asrPreviewBlock.block_id,
        story_block_title_ru: asrPreviewBlock.title_ru,
        story_block_color: asrPreviewBlock.color,
      })),
      [asrPreviewBlock]
    );
    persist({
      ...project,
      scenes: asrScenes,
      story_blocks: [asrPreviewBlock],
      selectedSceneId: asrScenes[0]?.scene_id || project.selectedSceneId || "",
      timing_status: "draft",
    });
    setAsrStatus("ASR-preview сцены открыты для проверки. Это временная замена storyboard.");
    window.setTimeout(() => setAsrStatus(""), 5000);
  };

  const onBuildStoryScenesFromAsr = () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    if (!audioPhrases.length) return;
    const nextMode = modeConfig.mode || project.project_mode;
    const nextKind =
      nextMode === MANUAL_TIMING_MUSIC_CLIP_MODE ? "clip" :
      nextMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE ? "podcast" :
      MANUAL_TIMING_STORY_PROJECT_KIND;
    if (manualSceneEdits) {
      console.info("[MANUAL TIMING REBUILD_BLOCKED_BY_MANUAL_EDITS]", {
        sceneCount: scenes.length,
        manualSceneEdits,
        source: "build_scenes",
      });
      const confirmedManualOverwrite = window.confirm("У тебя есть ручные разрезы сцен. Повторная сборка перезапишет их. Продолжить?");
      if (!confirmedManualOverwrite) return;
    }
    const hasCreatedMaterials = scenes.some(sceneHasCreatedMaterials);
    if (hasCreatedMaterials) {
      const confirmed = window.confirm("В сценах уже есть созданные материалы. Пересборка scenes может отвязать их. Продолжить?");
      if (!confirmed) return;
    }
    const audioDurationSec = Number(audio.duration_sec || durationSec || Math.max(...audioPhrases.map((phrase) => Number(phrase.end_sec || 0)), 0));
    const nextScenes = buildGapAwareScenesFromAudioPhrases(audioPhrases, {
      audioDurationSec,
      targetSceneDurationSec: { min: 4, preferred: 6, max: 9 },
      maxSceneDurationSec: 10,
      minSceneDurationSec: 2,
      projectKind: nextKind,
      route: "i2v",
    });
    const coverage = validateSceneCoverage(nextScenes, audioDurationSec);
    const draftBlocks = buildDraftStoryBlocksFromGapAwareScenes(nextScenes);
    const hydratedScenes = hydrateManualTimingScenesWithStoryBlocks(nextScenes, draftBlocks);
    const nextMarkers = normalizeManualTimingMarkers(
      hydratedScenes.flatMap((scene) => [scene.start_sec, scene.end_sec]),
      audioDurationSec
    );
    const sceneCountBeforeRebuild = scenes.length;
    const nextProject = persist({
      ...project,
      project_mode: nextMode,
      project_kind: nextKind,
      audio: {
        ...audio,
        duration_sec: roundTimingSec(audioDurationSec),
        duration_ms: Math.round(audioDurationSec * 1000),
      },
      markers: nextMarkers,
      scenes: hydratedScenes,
      story_blocks: draftBlocks,
      selectedSceneId: hydratedScenes[0]?.scene_id || project.selectedSceneId || "",
      timing_status: "draft",
      manual_scene_edits: false,
      manualSceneEdits: false,
      lastManualEditReason: "",
    });
    if (manualSceneEdits) {
      console.info("[MANUAL TIMING REBUILD_CONFIRMED_OVERWRITE_MANUAL_EDITS]", {
        sceneCountBefore: sceneCountBeforeRebuild,
        sceneCountAfter: Array.isArray(nextProject?.scenes) ? nextProject.scenes.length : hydratedScenes.length,
      });
    }
    const statusTail = coverage.ok ? "Покрытие audio_duration_sec проверено: без дыр и overlap." : coverage.errors.join(" ");
    setAsrStatus(`${workflowLabels.buildScenes}: собрано ${hydratedScenes.length} сцен, ${draftBlocks.length} черновых story_blocks. ${statusTail}`);
    window.setTimeout(() => setAsrStatus(""), 7000);
  };

  const onConfirmTiming = () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const nextScenes = scenes.map((scene) => ({ ...scene, quality: "manual_confirmed", format: projectFormat, aspect_ratio: projectFormat }));
    persist({ ...project, format: projectFormat, aspect_ratio: projectFormat, format_locked: true, scenes: nextScenes, timing_status: "confirmed" });
  };


  const requestNewBoardReplaceConfirmation = ({ existingBoard, newBoard }) => new Promise((resolve) => {
    const identityChanged = manualBoardIdentityChanged(existingBoard, newBoard);
    if (!hasMeaningfulManualProject(existingBoard) || !hasManualBoardMaterials(existingBoard)) {
      resolve("create");
      return;
    }
    console.info("[MANUAL BOARD NEW PROJECT CONFIRM REQUIRED]", {
      sourceNodeId: finalOwnerNodeId,
      oldStats: getManualClipBoardMaterialStats(existingBoard),
      newStats: getManualClipBoardMaterialStats(newBoard),
      identityChanged,
    });
    newBoardConfirmResolverRef.current = resolve;
    setNewBoardConfirm({
      existingBoard,
      newBoard,
      oldIdentity: getManualBoardIdentityParts(existingBoard),
      newIdentity: getManualBoardIdentityParts(newBoard),
      identityChanged,
    });
  });

  const resolveNewBoardReplaceConfirmation = (choice) => {
    const resolver = newBoardConfirmResolverRef.current;
    newBoardConfirmResolverRef.current = null;
    setNewBoardConfirm(null);
    if (typeof resolver === "function") resolver(choice);
  };

  const buildDirectorProjectSnapshot = () => {
    const sourceNodeId = finalOwnerNodeId;
    const projectFormat = String(project.format || project.aspect_ratio || "9:16");
    const sceneCleanStats = getManualNewBoardCleanSceneStats(scenes);
    const cleanScenes = scenes.map((scene) => sanitizeManualTimingSceneForNewBoard(scene, projectFormat));
    console.info("[MANUAL BOARD NEW PROJECT CLEAN SCENES]", sceneCleanStats);
    const handoffAudio = normalizeManualTimingProjectAudioForHandoff(project, audio);
    return applyManualTimingProjectAudioCompat({
      ...project,
      project_mode: modeConfig.mode || project.project_mode || MANUAL_TIMING_STORY_VOICEOVER_MODE,
      project_kind: project.project_kind || (modeConfig.mode === MANUAL_TIMING_MUSIC_CLIP_MODE ? "clip" : (modeConfig.mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE ? "podcast" : MANUAL_TIMING_STORY_PROJECT_KIND)),
      source: "manual_timing_node",
      ownerNodeType: "manualTiming",
      nodeId: sourceNodeId,
      sourceNodeId,
      step: `${workflowLabels.pass.toLowerCase().replace(/\s+/g, "_")}_ready`,
      format: projectFormat,
      aspect_ratio: projectFormat,
      format_locked: true,
      audio,
      audio_phrases: audioPhrases,
      story_blocks: storyBlocks,
      scenes: cleanScenes,
      selectedSceneId: cleanScenes[0]?.scene_id || selectedScene?.scene_id || scenes[0]?.scene_id || "",
      timing_status: project.timing_status || "confirmed",
    }, handoffAudio);
  };


  const onBackToNode = () => {
    const ownerNodeId = finalOwnerNodeId;
    console.info("[MANUAL TIMING BACK TO NODE]", { sourceNodeId: ownerNodeId });
    console.info("[MANUAL BOARD SKIP OPEN STATE]", { reason: "back_to_node", sourceNodeId: ownerNodeId });
    writeManualClipBoardOpenState({
      isOpen: false,
      sourceNodeId: ownerNodeId,
      routePath: STORYBOARD_ROUTE,
      reason: "back_to_node",
      updatedAt: Date.now(),
    });
    navigate(STORYBOARD_ROUTE, {
      state: {
        focusManualTimingNodeId: ownerNodeId,
        manualBoardSkipOpenStateReason: "back_to_node",
        closeManualDirectorBoard: true,
        closeLegacyScenarioEditors: true,
      },
    });
  };

  const onOpenDirectorBoard = () => {
    const ownerNodeId = finalOwnerNodeId;
    const existingBoard = getManualTimingBoardForOwner(ownerNodeId);
    if (hasMeaningfulManualProject(existingBoard)) {
      const safeBoard = {
        ...existingBoard,
        nodeId: ownerNodeId,
        sourceNodeId: ownerNodeId,
      };
      setActiveBoardProject(safeBoard);
      const forceProjectId = String(safeBoard?.project_id || safeBoard?.projectId || "").trim();
      const forceInputSignature = String(safeBoard?.input_signature || safeBoard?.inputSignature || "").trim();
      const forceAudioSignature = String(safeBoard?.audio_signature || safeBoard?.audioSignature || "").trim();
      writeManualClipBoardOpenState({
        isOpen: true,
        sourceNodeId: ownerNodeId,
        selectedSceneId: String(safeBoard?.selectedSceneId || safeBoard?.scenes?.[0]?.scene_id || "").trim(),
        project_id: forceProjectId,
        input_signature: forceInputSignature,
        audio_signature: forceAudioSignature,
        forceProjectId,
        forceInputSignature,
        forceAudioSignature,
        routePath: STORYBOARD_ROUTE,
        updatedAt: Date.now(),
      });
      navigate(STORYBOARD_ROUTE, {
        state: {
          openManualDirectorBoard: true,
          closeLegacyScenarioEditors: true,
          sourceNodeId: ownerNodeId,
          ownerNodeId,
          manualBoardForceProjectId: forceProjectId,
          manualBoardForceInputSignature: forceInputSignature,
          manualBoardForceAudioSignature: forceAudioSignature,
          forceProjectId,
          forceInputSignature,
          forceAudioSignature,
          director_board: safeBoard,
          project: safeBoard,
        },
      });
      return;
    }
    setCopyStatus("Для текущего тайминга доска не найдена. Нажмите ‘Создать новую доску из тайминга’.");
    window.setTimeout(() => setCopyStatus(""), 4200);
  };

  const onCreateNewDirectorBoardFromTiming = async () => {
    if (!storyPassReadyForDirector) {
      setCopyStatus(`Сначала примените ${workflowLabels.pass} JSON`);
      return;
    }
    if (handoffStatus) return;

    const ownerNodeId = finalOwnerNodeId;
    const existingBoard = getManualTimingBoardForOwner(ownerNodeId);
    if (hasMeaningfulManualProject(existingBoard)) setActiveBoardProject(existingBoard);

    let projectSnapshot = applyManualTimingProjectAudioCompat({
      ...buildDirectorProjectSnapshot(),
      nodeId: ownerNodeId,
      sourceNodeId: ownerNodeId,
      ownerNodeType: "manualTiming",
      source: "manual_timing_node",
      selectedSceneId: scenes[0]?.scene_id || "",
      updatedAt: Date.now(),
      lastPersistReason: "manual_new_project_from_audio_split",
    }, normalizeManualTimingProjectAudioForHandoff(project, audio));
    const inputSignature = computeManualProjectInputSignature(projectSnapshot);
    const audioSignature = computeManualProjectInputSignature(projectSnapshot, { audioOnly: true });
    const storySignature = computeManualProjectInputSignature(projectSnapshot, { storyOnly: true });
    const projectId = `manual_${ownerNodeId}_${inputSignature}_${Date.now()}`;
    projectSnapshot = {
      ...projectSnapshot,
      project_id: projectId,
      projectId,
      input_signature: inputSignature,
      inputSignature,
      audio_signature: audioSignature,
      audioSignature,
      story_signature: storySignature,
      storySignature,
    };

    logManualBoardMediaRefs("[MANUAL BOARD MEDIA REFS BEFORE CLEAN]", projectSnapshot, { sourceNodeId: ownerNodeId });
    console.info("[MANUAL BOARD NEW PROJECT REQUEST]", {
      sourceNodeId: ownerNodeId,
      oldIdentity: getManualBoardIdentityParts(existingBoard),
      newIdentity: getManualBoardIdentityParts(projectSnapshot),
      oldHasMaterials: hasManualBoardMaterials(existingBoard),
      identityChanged: manualBoardIdentityChanged(existingBoard, projectSnapshot),
    });
    console.info("[MANUAL BOARD NEW PROJECT TARGET OWNER]", {
      sourceNodeId: ownerNodeId,
      existingBoardStats: getManualClipBoardMaterialStats(existingBoard),
      newProjectStats: getManualClipBoardMaterialStats(projectSnapshot),
    });

    const replaceChoice = await requestNewBoardReplaceConfirmation({ existingBoard, newBoard: projectSnapshot });
    if (replaceChoice === "cancel") {
      console.info("[MANUAL BOARD NEW PROJECT CANCELLED]", { sourceNodeId: ownerNodeId });
      setCopyStatus("Создание новой доски отменено — старая доска сохранена.");
      window.setTimeout(() => setCopyStatus(""), 3200);
      return;
    }
    if (replaceChoice === "backup") {
      console.info("[MANUAL BOARD NEW PROJECT CONFIRM BACKUP]", { sourceNodeId: ownerNodeId });
      downloadManualBoardBackupJson(existingBoard);
    }
    let handoffWarning = "";
    const needsAudioSlice = Boolean(projectSnapshot?.audio?.url)
      && !(Array.isArray(projectSnapshot.scenes) && projectSnapshot.scenes.every((scene) => String(scene?.audio_slice_url || "").trim()));

    if (needsAudioSlice) {
      setHandoffStatus("Нарезаю аудио сцен…");
      try {
        const slicedScenes = await sliceStoryVoiceoverAudioForScenes(projectSnapshot);
        const slicedById = new Map(slicedScenes.map((scene) => [String(scene?.scene_id || ""), scene]));
        projectSnapshot = {
          ...projectSnapshot,
          scenes: projectSnapshot.scenes.map((scene) => {
            const slicedScene = slicedById.get(String(scene?.scene_id || ""));
            if (!slicedScene) return scene;
            return {
              ...scene,
              audio_slice_url: String(slicedScene.audio_slice_url || scene.audio_slice_url || ""),
              audio_slice_duration_sec: Number(slicedScene.audio_slice_duration_sec || scene.audio_slice_duration_sec || 0),
            };
          }),
        };
        if (!projectSnapshot.scenes.every((scene) => String(scene?.audio_slice_url || "").trim())) {
          throw new Error("backend_returned_partial_audio_slices");
        }
      } catch (error) {
        handoffWarning = `Проект передан, но аудио сцен не нарезано: ${String(error?.message || "audio_slice_failed")}`;
        projectSnapshot = { ...projectSnapshot, handoff_warning: handoffWarning };
        setCopyStatus(handoffWarning);
        window.setTimeout(() => setCopyStatus(""), 5000);
      }
    }

    projectSnapshot = sanitizeManualNewBoardProject(applyManualTimingProjectAudioCompat({
      ...projectSnapshot,
      nodeId: ownerNodeId,
      sourceNodeId: ownerNodeId,
      ownerNodeType: "manualTiming",
      source: "manual_timing_node",
      updatedAt: Date.now(),
      lastPersistReason: "manual_new_project_from_audio_split",
    }, normalizeManualTimingProjectAudioForHandoff(project, audio)));
    projectSnapshot = applyManualTimingProjectAudioCompat({
      ...projectSnapshot,
      nodeId: ownerNodeId,
      sourceNodeId: ownerNodeId,
      ownerNodeType: "manualTiming",
      source: "manual_timing_node",
      selectedSceneId: projectSnapshot.scenes?.[0]?.scene_id || "",
      selectedScene: null,
      updatedAt: Date.now(),
      lastPersistReason: "manual_new_project_from_audio_split",
    }, normalizeManualTimingProjectAudioForHandoff(project, audio));
    logManualBoardMediaRefs("[MANUAL BOARD MEDIA REFS AFTER CLEAN]", projectSnapshot, { sourceNodeId: ownerNodeId });

    const replacedProject = replaceManualClipBoardProjectForNode(ownerNodeId, projectSnapshot, {
      forceReplace: true,
      explicitReset: true,
      allowMaterialLoss: true,
      reason: "manual_new_project_from_audio_split",
      routePath: STORYBOARD_ROUTE,
    }) || projectSnapshot;
    const replacedProjectId = String(replacedProject?.project_id || replacedProject?.projectId || "").trim();
    const replacedInputSignature = String(replacedProject?.input_signature || replacedProject?.inputSignature || "").trim();
    const replacedAudioSignature = String(replacedProject?.audio_signature || replacedProject?.audioSignature || "").trim();
    writeManualClipBoardOpenState({
      isOpen: true,
      sourceNodeId: ownerNodeId,
      selectedSceneId: String(replacedProject?.selectedSceneId || replacedProject?.scenes?.[0]?.scene_id || "").trim(),
      project_id: replacedProjectId,
      input_signature: replacedInputSignature,
      manualBoardExplicitNewProject: true,
      forceProjectId: replacedProjectId,
      forceInputSignature: replacedInputSignature,
      forceAudioSignature: replacedAudioSignature,
      audio_signature: replacedAudioSignature,
      routePath: STORYBOARD_ROUTE,
      updatedAt: Date.now(),
    });
    console.info('[MANUAL BOARD CANONICAL ROUTE] route="/studio/storyboard"', { sourceNodeId: ownerNodeId });
    dispatchManualTimingDirectorBoardUpdate(replacedProject, ownerNodeId);
    persistManualTimingProject(replacedProject);
    setActiveBoardProject(replacedProject);
    if (!handoffWarning) setCopyStatus("Проект передан в режиссёрскую доску");
    setHandoffStatus("");
    console.info("[MANUAL TIMING BOARD AUDIO HANDOFF]", {
      sourceNodeId: ownerNodeId,
      project_id: replacedProjectId,
      audio: {
        url: String(replacedProject?.audio?.url || replacedProject?.audio_url || replacedProject?.audioUrl || "").trim(),
        name: String(replacedProject?.audio?.name || replacedProject?.audio?.filename || replacedProject?.audio_name || "").trim(),
        duration_sec: Number(replacedProject?.audio?.duration_sec || replacedProject?.audio_duration_sec || 0) || 0,
      },
      audio_url: String(replacedProject?.audio_url || replacedProject?.audioUrl || replacedProject?.audio?.url || "").trim(),
      audioSignature: replacedAudioSignature,
    });
    navigate(STORYBOARD_ROUTE, {
      state: {
        openManualDirectorBoard: true,
        manualBoardExplicitNewProject: true,
        manualBoardForceProjectId: replacedProjectId,
        manualBoardForceInputSignature: replacedInputSignature,
        manualBoardForceAudioSignature: replacedAudioSignature,
        forceProjectId: replacedProjectId,
        forceInputSignature: replacedInputSignature,
        forceAudioSignature: replacedAudioSignature,
        sourceNodeId: ownerNodeId,
        ownerNodeId,
        director_board: replacedProject,
        project: replacedProject,
        closeLegacyScenarioEditors: true,
      },
    });
  };

  const onCopyTimingJson = async () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const payload = buildManualTimingExportJson(project);
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      setCopyStatus("JSON таймингов скопирован");
      window.setTimeout(() => setCopyStatus(""), 1600);
    } catch {
      setCopyStatus("Не удалось скопировать JSON");
    }
  };

  const downloadJsonPayload = (payload, filename = "manual_timing.json") => {
    try {
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setCopyStatus("JSON скачан");
      window.setTimeout(() => setCopyStatus(""), 1600);
    } catch {
      setCopyStatus("Не удалось скачать JSON");
    }
  };

  const refreshSceneSourcePhraseIds = () => {
    if (!scenes.length || !audioPhrases.length) {
      setCopyStatus("Нет сцен или audio_phrases для обновления");
      window.setTimeout(() => setCopyStatus(""), 1800);
      return;
    }
    const repaired = repairManualTimingSourcePhraseIdsFromTiming(scenes, audioPhrases);
    if (!repaired.repaired) {
      setCopyStatus("Фразы уже соответствуют границам сцен");
      window.setTimeout(() => setCopyStatus(""), 2200);
      return;
    }
    rememberManualTimingAction("обновить source_phrase_ids");
    persist({ ...project, scenes: repaired.scenes, timing_status: "draft" });
    setCopyStatus(`Фразы обновлены: ${repaired.repairedEmptyCount + repaired.replacedWrongCount} сцен`);
    window.setTimeout(() => setCopyStatus(""), 2200);
  };

  const onDownloadAsrTranslationJson = () => {
    downloadJsonPayload(buildManualTimingAsrTranslationPassJson(project), "manual_timing_asr_translation_pass.json");
  };

  const applyAsrTranslationJson = (rawObject = {}) => {
    const importedPhrases = Array.isArray(rawObject)
      ? rawObject
      : (Array.isArray(rawObject?.audio_phrases) ? rawObject.audio_phrases : []);
    if (!importedPhrases.length) {
      setCopyStatus("В JSON нет audio_phrases для импорта перевода");
      window.setTimeout(() => setCopyStatus(""), 2200);
      return;
    }
    const merged = mergeManualTimingAsrTranslationPhrases(project.audio_phrases, importedPhrases);
    if (!merged.updatedCount) {
      setCopyStatus("Новых text_ru для audio_phrases не найдено");
      window.setTimeout(() => setCopyStatus(""), 2200);
      return;
    }
    rememberManualTimingAction("импорт ASR text_ru");
    persist({ ...project, audio_phrases: merged.audio_phrases, timing_status: "draft" });
    setCopyStatus(`ASR-перевод импортирован: обновлено ${merged.updatedCount} фраз`);
    window.setTimeout(() => setCopyStatus(""), 2400);
  };

  const onImportAsrTranslationJsonFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const text = await file.text();
      applyAsrTranslationJson(JSON.parse(text));
    } catch (error) {
      setCopyStatus(`Ошибка ASR Translation JSON: ${error?.message || "неверный формат"}`);
      window.setTimeout(() => setCopyStatus(""), 2600);
    }
  };

  const onDownloadTimingJson = () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    downloadJsonPayload(buildManualTimingExportJson(project), "manual_timing_export.json");
  };

  const onDownloadCurrentTimingBackup = () => {
    const createdAt = new Date();
    const payload = buildManualTimingCurrentProjectBackupPayload(project, createdAt);
    const audioName = getManualTimingCurrentProjectAudioName(project);
    const validation = validateManualTimingCurrentProjectBackupPayload(payload, project, {
      audioDurationSec: durationSec,
      sceneCount: scenes.length,
      markerCount: markers.length,
      storyBlockCount: storyBlocks.length,
    });

    if (!validation.ok) {
      console.info("[MANUAL TIMING CURRENT_TIMING_BACKUP_BLOCKED]", {
        reason: validation.reason,
        payloadAudioDurationSec: validation.payloadAudioDurationSec,
        currentAudioDurationSec: validation.currentAudioDurationSec,
        payloadSceneCount: validation.payloadSceneCount,
        currentSceneCount: validation.currentSceneCount,
        payloadMarkerCount: validation.payloadMarkerCount,
        currentMarkerCount: validation.currentMarkerCount,
        payloadStoryBlockCount: validation.payloadStoryBlockCount,
        currentStoryBlockCount: validation.currentStoryBlockCount,
      });
      setCopyStatus("Бэкап остановлен: экспорт не совпадает с текущим таймингом.");
      window.setTimeout(() => setCopyStatus(""), 5200);
      return;
    }

    console.info("[MANUAL TIMING CURRENT_TIMING_BACKUP_DOWNLOADED]", {
      audioDurationSec: validation.currentAudioDurationSec,
      audioName,
      sceneCount: validation.currentSceneCount,
      markerCount: validation.currentMarkerCount,
      storyBlockCount: validation.currentStoryBlockCount,
      projectMode: project?.project_mode,
      projectKind: project?.project_kind,
      source: "current_project",
    });
    downloadJsonPayload(payload, buildManualTimingCurrentProjectBackupFilename(project, createdAt));
  };

  const onDownloadProjectBackup = () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const validation = validateManualTimingBackupMatchesCurrentProject(project, project);
    if (!validation.ok) {
      console.info("[MANUAL TIMING BACKUP_BLOCKED_STALE_PROJECT]", {
        currentAudioDurationSec: validation.currentAudioDurationSec,
        exportAudioDurationSec: validation.exportAudioDurationSec,
        currentAudioName: validation.currentAudioName,
        exportAudioName: validation.exportAudioName,
      });
      setCopyStatus("Бэкап остановлен: текущий проект не совпадает с активной доской. Обновите проект или скачайте текущий JSON.");
      window.setTimeout(() => setCopyStatus(""), 5200);
      return;
    }
    console.info("[MANUAL TIMING CURRENT_PROJECT_BACKUP_VALIDATED]", {
      audioDurationSec: validation.currentAudioDurationSec,
      audioName: validation.currentAudioName,
      sceneCount: scenes.length,
      storyBlockCount: storyBlocks.length,
      markerCount: markers.length,
    });
    downloadJsonPayload(buildManualProjectBackupJson(project, { source: "manual_timing_editor" }), "manual_project_backup.json");
  };

  const onReturnToActiveBoard = () => {
    const ownerNodeId = finalOwnerNodeId;
    const existingBoard = getManualTimingBoardForOwner(ownerNodeId);
    if (hasMeaningfulManualProject(existingBoard)) {
      const safeBoard = {
        ...existingBoard,
        nodeId: ownerNodeId,
        sourceNodeId: ownerNodeId,
      };
      setActiveBoardProject(safeBoard);
      writeManualClipBoardOpenState({
        isOpen: true,
        sourceNodeId: ownerNodeId,
        selectedSceneId: String(safeBoard?.selectedSceneId || safeBoard?.scenes?.[0]?.scene_id || "").trim(),
        project_id: String(safeBoard?.project_id || safeBoard?.projectId || "").trim(),
        input_signature: String(safeBoard?.input_signature || safeBoard?.inputSignature || "").trim(),
        routePath: STORYBOARD_ROUTE,
        updatedAt: Date.now(),
      });
      navigate(STORYBOARD_ROUTE, {
        state: { openManualDirectorBoard: true, closeLegacyScenarioEditors: true, sourceNodeId: ownerNodeId, director_board: safeBoard, project: safeBoard },
      });
      return;
    }
    setCopyStatus("Для текущего тайминга доска не найдена. Нажмите ‘Создать новую доску из тайминга’.");
    window.setTimeout(() => setCopyStatus(""), 4200);
  };

  const onDownloadActiveBoardBackup = () => {
    const existingBoard = getManualTimingBoardForOwner(finalOwnerNodeId);
    if (!hasMeaningfulManualProject(existingBoard)) {
      setCopyStatus("Для текущего тайминга доска не найдена. Нажмите ‘Создать новую доску из тайминга’.");
      window.setTimeout(() => setCopyStatus(""), 4200);
      return;
    }
    const validation = validateManualTimingBackupMatchesCurrentProject(existingBoard, project);
    if (!validation.ok) {
      console.info("[MANUAL TIMING BACKUP_BLOCKED_STALE_PROJECT]", {
        currentAudioDurationSec: validation.currentAudioDurationSec,
        exportAudioDurationSec: validation.exportAudioDurationSec,
        currentAudioName: validation.currentAudioName,
        exportAudioName: validation.exportAudioName,
      });
      setActiveBoardProject(null);
      setCopyStatus("Бэкап остановлен: текущий проект не совпадает с активной доской. Обновите проект или скачайте текущий JSON.");
      window.setTimeout(() => setCopyStatus(""), 5200);
      return;
    }
    setActiveBoardProject(existingBoard);
    console.info("[MANUAL TIMING CURRENT_PROJECT_BACKUP_VALIDATED]", {
      audioDurationSec: validation.currentAudioDurationSec,
      audioName: validation.currentAudioName,
      sceneCount: scenes.length,
      storyBlockCount: storyBlocks.length,
      markerCount: markers.length,
    });
    downloadJsonPayload(buildManualProjectBackupJson(project, { source: "manual_timing_editor_active_board_current_project" }), "manual_project_backup.json");
  };

  const onStartNewAnalysisWithConfirm = () => {
    const confirmed = window.confirm("Начать новый разбор? Это очистит текущее аудио, ASR-фразы и сцены тайминга. Режиссёрская доска не удаляется — скачайте backup отдельно, если нужно.");
    if (!confirmed) return;
    const nextProject = buildManualTimingProjectForAudioChange(project, getEmptyManualTimingAudio(), "none");
    persist(nextProject);
    setAsrStatus("");
    setHandoffStatus("");
    setAudioTime(0, { pause: true, clearBound: true });
    setCopyStatus("Новый разбор начат. Загрузите новое аудио.");
    window.setTimeout(() => setCopyStatus(""), 3200);
  };

  const onDownloadSampleJson = () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    downloadJsonPayload(buildManualTimingSampleJson(project), "manual_timing_sample_for_chatgpt.json");
  };

  const onDownloadAiSplitRequestJson = () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    downloadJsonPayload(buildManualTimingAiSplitRequestJson(project), "manual_timing_ai_split_request.json");
  };

  const onCopyAiSplitRequestJson = async () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const payload = buildManualTimingAiSplitRequestJson(project);
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      setCopyStatus("JSON для AI-разбивки скопирован");
      window.setTimeout(() => setCopyStatus(""), 1600);
    } catch {
      setCopyStatus("Не удалось скопировать JSON для AI-разбивки");
    }
  };

  const onRecoverPodcastProjectToStory = () => {
    if (!canRecoverPodcastProjectToStory) return;
    const previousProjectMode = String(project?.project_mode || project?.projectMode || "").trim();
    const previousProjectKind = String(project?.project_kind || project?.projectKind || "").trim();
    const nextProject = persist({
      ...project,
      project_mode: MANUAL_TIMING_STORY_VOICEOVER_MODE,
      projectMode: MANUAL_TIMING_STORY_VOICEOVER_MODE,
      project_kind: MANUAL_TIMING_STORY_PROJECT_KIND,
      projectKind: MANUAL_TIMING_STORY_PROJECT_KIND,
      story_pass_mode: "semantic_story_cut",
      storyPassMode: "semantic_story_cut",
      split_type: "semantic_story_cut_pass",
      splitType: "semantic_story_cut_pass",
      lastPersistReason: "manual_timing_recovered_to_story_mode",
    });

    console.info("[MANUAL TIMING PROJECT_MODE_RECOVERED_TO_STORY]", {
      previousProjectMode,
      previousProjectKind,
      nextProjectMode: nextProject.project_mode,
      nextProjectKind: nextProject.project_kind,
      sceneCount: scenes.length,
      markerCount: markers.length,
      storyBlockCount: storyBlocks.length,
      audioDurationSec: durationSec,
    });
    setCopyStatus("Режим восстановлен: История. Тайминги, сцены, маркеры и story_blocks сохранены.");
    window.setTimeout(() => setCopyStatus(""), 3200);
  };

  const logManualTimingPassExport = (payload = {}, passType = "", filename = "") => {
    const passMeta = MANUAL_TIMING_AI_PASS_BY_TYPE[passType] || {};
    console.info("[MANUAL TIMING PASS_EXPORT_BUILT]", {
      passType,
      filename,
      passNameRu: passMeta.pass_name_ru || payload?.manual_timing_pass?.pass_name_ru || "",
      sceneCount: Array.isArray(payload?.scenes) ? payload.scenes.length : 0,
      storyBlockCount: Array.isArray(payload?.story_blocks) ? payload.story_blocks.length : 0,
      audioPhraseCount: Array.isArray(payload?.audio_phrases) ? payload.audio_phrases.length : 0,
      completedStages: payload?.manual_timing_workflow?.completed_stages || [],
      lockedStages: payload?.manual_timing_workflow?.locked_stages || [],
    });
  };

  const getManualTimingPassCopySuccessStatus = (passType = "", passMeta = {}) => {
    if (passType === "music_clip") return "JSON для Clip Pass скопирован и скачан";
    if (passType === "podcast_dialogue") return "JSON для Podcast Pass скопирован и скачан";
    return `JSON для этапа “${passMeta.pass_name_ru || passType}” скопирован и скачан`;
  };

  const copyManualTimingPassJson = async (passType = "semantic_story_cut", buildPayload) => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const passMeta = MANUAL_TIMING_AI_PASS_BY_TYPE[passType] || {};
    const payload = buildPayload(project);
    const filename = buildManualTimingPassDownloadFilename(passType, project);
    const jsonText = JSON.stringify(payload, null, 2);
    logManualTimingPassExport(payload, passType, filename);

    const clipboardPromise = typeof navigator !== "undefined" && navigator.clipboard?.writeText
      ? navigator.clipboard.writeText(jsonText).then(() => true).catch(() => false)
      : Promise.resolve(false);

    let downloaded = false;
    try {
      downloadManualTimingJsonFile(payload, filename);
      downloaded = true;
      console.info("[MANUAL TIMING PASS_JSON_DOWNLOADED]", {
        passType,
        filename,
        sceneCount: Array.isArray(payload?.scenes) ? payload.scenes.length : 0,
        storyBlockCount: Array.isArray(payload?.story_blocks) ? payload.story_blocks.length : 0,
        audioPhraseCount: Array.isArray(payload?.audio_phrases) ? payload.audio_phrases.length : 0,
      });
    } catch (error) {
      console.warn("[MANUAL TIMING PASS_JSON_DOWNLOAD_FAILED]", { passType, filename, error });
    }

    const clipboardOk = await clipboardPromise;
    if (clipboardOk && downloaded) {
      setCopyStatus(getManualTimingPassCopySuccessStatus(passType, passMeta));
      window.setTimeout(() => setCopyStatus(""), 2200);
      return;
    }
    if (!clipboardOk && downloaded) {
      setCopyStatus("Clipboard недоступен, но JSON скачан файлом");
      window.setTimeout(() => setCopyStatus(""), 2600);
      return;
    }
    if (clipboardOk && !downloaded) {
      setCopyStatus("JSON скопирован, но не удалось скачать файл");
      window.setTimeout(() => setCopyStatus(""), 2600);
      return;
    }
    setCopyStatus("Не удалось скопировать или скачать JSON");
  };

  const buildManualTimingModePassJson = (sourceProject = {}) => {
    const mode = String(sourceProject?.project_mode || sourceProject?.projectMode || "");
    if (mode === MANUAL_TIMING_MUSIC_CLIP_MODE) return buildManualTimingClipPassJson(sourceProject);
    if (mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) return buildManualTimingPodcastPassJson(sourceProject);
    return buildManualTimingStoryPassJson(sourceProject);
  };

  const onCopyModePassJson = async () => {
    const mode = String(project?.project_mode || project?.projectMode || "");
    if (mode === MANUAL_TIMING_MUSIC_CLIP_MODE) return copyManualTimingPassJson("music_clip", buildManualTimingModePassJson);
    if (mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) return copyManualTimingPassJson("podcast_dialogue", buildManualTimingModePassJson);
    return copyManualTimingPassJson("semantic_story_cut", buildManualTimingModePassJson);
  };

  const onCopyStoryBiblePassJson = async () => copyManualTimingPassJson("story_bible", buildManualTimingStoryBiblePassJson);

  const onCopyBlockStoryboardPassJson = async () => copyManualTimingPassJson("block_storyboard", buildManualTimingBlockStoryboardPassJson);


  const parseJsonImportText = () => JSON.parse(jsonImportText || "{}");

  const logManualTimingPassApplyBlocked = (reason = "", clickedPassType = "", payloadPassType = "", requiredStages = []) => {
    console.warn("[MANUAL TIMING PASS_APPLY_BLOCKED]", {
      reason,
      clickedPassType,
      payloadPassType,
      requiredStages,
      completedStages: manualTimingWorkflow.completed_stages,
    });
  };

  const blockManualTimingPassApply = (message = "", reason = "", clickedPassType = "", payloadPassType = "", requiredStages = []) => {
    logManualTimingPassApplyBlocked(reason, clickedPassType, payloadPassType, requiredStages);
    setCopyStatus(message);
    window.setTimeout(() => setCopyStatus(""), 3200);
  };

  const blockManualTimingAiRequestJsonApply = (rawObject = {}, clickedPassType = "") => {
    const importedObject = unwrapManualProjectBackupJson(rawObject);
    if (!isManualTimingAiRequestJson(importedObject)) return false;
    blockManualTimingPassApply(
      MANUAL_TIMING_AI_REQUEST_JSON_MESSAGE,
      "ai_request_json_pasted_as_result",
      clickedPassType,
      getManualTimingJsonPassType(importedObject),
      MANUAL_TIMING_AI_PASS_BY_TYPE[clickedPassType]?.requires || []
    );
    return true;
  };

  const getManualTimingActivationErrorMessage = (importedObject = {}, clickedPassType = "") => {
    const stage = MANUAL_TIMING_AI_PASS_BY_TYPE[clickedPassType];
    const result = importedObject?.manual_timing_pass_result || importedObject?.manualTimingPassResult || {};
    const activationPhrase = String(result?.activation_phrase || result?.activationPhrase || "").trim();
    if (!activationPhrase) return `Этап не активирован: отсутствует activation_phrase ${stage?.activation_phrase || ""}`.trim();
    return "Этап не активирован: неверный activation_phrase";
  };

  const validateManualTimingActivationOrBlock = (importedObject = {}, clickedPassType = "", payloadPassType = "") => {
    const activationValidation = validateManualTimingPassResultActivation(importedObject, clickedPassType);
    if (activationValidation.ok) return true;
    blockManualTimingPassApply(
      getManualTimingActivationErrorMessage(importedObject, clickedPassType),
      "activation_phrase_invalid",
      clickedPassType,
      payloadPassType || getManualTimingJsonPassType(importedObject),
      MANUAL_TIMING_AI_PASS_BY_TYPE[clickedPassType]?.requires || []
    );
    return false;
  };

  const validateManualTimingClickedPass = (rawObject = {}, clickedPassType = "") => {
    const backupType = String(rawObject?.backup_type || rawObject?.backupType || "");
    if (backupType === MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE) {
      return { ok: true, importedObject: rawObject, payloadPassType: "timing_backup", isCurrentTimingBackup: true };
    }
    if (backupType === "photostudio_manual_project_backup") {
      return { ok: true, importedObject: unwrapManualProjectBackupJson(rawObject), payloadPassType: "project_backup", isProjectBackup: true };
    }
    const importedObject = unwrapManualProjectBackupJson(rawObject);
    const explicitPassType = getManualTimingPayloadPassType(importedObject);
    if (!explicitPassType) {
      console.warn("[MANUAL TIMING PASS_APPLY_LEGACY_PAYLOAD] manual_timing_pass.pass_type missing; falling back to legacy validator.", {
        clickedPassType,
        inferredPassType: getManualTimingJsonPassType(importedObject),
      });
    }
    const payloadPassType = explicitPassType || getManualTimingJsonPassType(importedObject);
    if (payloadPassType !== clickedPassType) {
      blockManualTimingPassApply(
        `Этот JSON относится к этапу: ${getManualTimingPassNameRu(payloadPassType)}. Выберите правильную кнопку применения.`,
        "pass_type_mismatch",
        clickedPassType,
        payloadPassType,
        MANUAL_TIMING_AI_PASS_BY_TYPE[clickedPassType]?.requires || []
      );
      return { ok: false, importedObject, payloadPassType };
    }

    const requiredStages = MANUAL_TIMING_AI_PASS_BY_TYPE[clickedPassType]?.requires || [];
    const missingRequiredStage = requiredStages.find((stage) => !manualTimingWorkflow.completed_stages.includes(stage));
    if (missingRequiredStage) {
      blockManualTimingPassApply(
        `Сначала примените этап: ${getManualTimingPassNameRu(missingRequiredStage)}.`,
        "required_stage_missing",
        clickedPassType,
        payloadPassType,
        requiredStages
      );
      return { ok: false, importedObject, payloadPassType };
    }
    return { ok: true, importedObject, payloadPassType };
  };

  const buildProjectWithCompletedManualTimingStage = (baseProject = {}, passType = "") => ({
    ...baseProject,
    manual_timing_workflow: completeManualTimingWorkflowStage(baseProject, passType),
  });

  const logManualTimingPassApplied = (nextProject = {}, passType = "") => {
    console.info("[MANUAL TIMING PASS_APPLIED]", {
      passType,
      nextStage: nextProject?.manual_timing_workflow?.current_stage || "",
      completedStages: nextProject?.manual_timing_workflow?.completed_stages || [],
      sceneCount: Array.isArray(nextProject?.scenes) ? nextProject.scenes.length : 0,
      storyBlockCount: Array.isArray(nextProject?.story_blocks) ? nextProject.story_blocks.length : 0,
    });
  };

  const applyImportedStoryBibleJson = (rawObject) => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const passValidation = validateManualTimingClickedPass(rawObject, "story_bible");
    if (!passValidation.ok) return;
    const importedObject = passValidation.importedObject;
    const validation = validateManualTimingStoryBiblePassImport(importedObject, project);
    if (!validation?.ok) {
      const errors = Array.isArray(validation?.errors) ? validation.errors : [];
      blockManualTimingPassApply(`Библия истории отклонена: ${errors.slice(0, 3).join(" ") || "формат не прошёл проверку"}`, "validator_failed", "story_bible", passValidation.payloadPassType, MANUAL_TIMING_AI_PASS_BY_TYPE.story_bible.requires);
      return;
    }
    if (!validateManualTimingActivationOrBlock(importedObject, "story_bible", passValidation.payloadPassType)) return;
    const nextProject = { ...project };
    MANUAL_STORY_BIBLE_PROJECT_KEYS.forEach((key) => {
      if (Object.prototype.hasOwnProperty.call(importedObject, key)) {
        nextProject[key] = importedObject[key];
      }
    });
    nextProject.story_bible_status = "applied";
    nextProject.story_bible_applied_at = Date.now();
    nextProject.story_bible_split_type = String(importedObject?.split_type || importedObject?.splitType || "manual_story_bible_pass").trim();
    const completedProject = buildProjectWithCompletedManualTimingStage(nextProject, "story_bible");
    const savedProject = persist(completedProject);
    logManualTimingPassApplied(savedProject, "story_bible");
    setJsonImportText(JSON.stringify(buildManualTimingExportJson(savedProject), null, 2));
    setCopyStatus("Библия истории применена: обновлены только project_* поля, сцены и тайминги не тронуты");
    window.setTimeout(() => setCopyStatus(""), 2400);
  };

  const onApplyStoryCutJson = () => {
    try {
      const raw = parseJsonImportText();
      if (String(raw?.backup_type || raw?.backupType || "") === MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE) {
        applyImportedTimingJson(raw, "");
        return;
      }
      if (blockManualTimingAiRequestJsonApply(raw, "semantic_story_cut")) return;
      const passValidation = validateManualTimingClickedPass(raw, "semantic_story_cut");
      if (!passValidation.ok) return;
      applyImportedTimingJson(raw, "semantic_story_cut");
    } catch (error) {
      setCopyStatus(`Ошибка JSON: ${error?.message || "неверный формат"}`);
    }
  };

  const onApplyStoryBibleJson = () => {
    try {
      const raw = parseJsonImportText();
      if ([MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE, "photostudio_manual_project_backup"].includes(String(raw?.backup_type || raw?.backupType || ""))) {
        applyImportedTimingJson(raw, "");
        return;
      }
      if (blockManualTimingAiRequestJsonApply(raw, "story_bible")) return;
      applyImportedStoryBibleJson(raw);
    } catch (error) {
      setCopyStatus(`Ошибка JSON: ${error?.message || "неверный формат"}`);
    }
  };

  const onApplyBlockStoryboardJson = () => {
    try {
      const raw = parseJsonImportText();
      if (String(raw?.backup_type || raw?.backupType || "") === MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE) {
        applyImportedTimingJson(raw, "");
        return;
      }
      if (blockManualTimingAiRequestJsonApply(raw, "block_storyboard")) return;
      const passValidation = validateManualTimingClickedPass(raw, "block_storyboard");
      if (!passValidation.ok) return;
      applyImportedTimingJson(raw, "block_storyboard");
    } catch (error) {
      setCopyStatus(`Ошибка JSON: ${error?.message || "неверный формат"}`);
    }
  };

  const applyImportedTimingJson = (rawObject, clickedPassType = "") => {
    const backupType = String(rawObject?.backup_type || rawObject?.backupType || "");
    const isCurrentTimingBackupImport = backupType === MANUAL_TIMING_CURRENT_PROJECT_BACKUP_TYPE;
    const isBackupImport = backupType === "photostudio_manual_project_backup" || isCurrentTimingBackupImport;
    const stageToComplete = isBackupImport ? "" : clickedPassType;
    if (!isBackupImport && mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const importedObject = isCurrentTimingBackupImport ? rawObject : unwrapManualProjectBackupJson(rawObject);
    if (!isBackupImport && blockManualTimingAiRequestJsonApply(importedObject, clickedPassType)) return;
    if (!isBackupImport) {
      let validations = [];
      if (clickedPassType === "semantic_story_cut") {
        validations = [validateManualTimingStoryPassImport(importedObject, project)];
      } else if (clickedPassType === "block_storyboard") {
        validations = [validateManualTimingBlockStoryboardPassImport(importedObject, project)];
      } else {
        const mode = String(importedObject.project_mode || project.project_mode || project.projectMode || "");
        if (mode === MANUAL_TIMING_MUSIC_CLIP_MODE) {
          validations = [validateManualTimingClipPassImport(importedObject, project)];
        } else if (mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) {
          validations = [validateManualTimingPodcastPassImport(importedObject, project)];
        } else if (mode === MANUAL_TIMING_STORY_VOICEOVER_MODE) {
          const splitType = String(importedObject.split_type || importedObject.splitType || "");
          if (splitType === "manual_story_bible_pass") {
            validations = [validateManualTimingStoryBiblePassImport(importedObject, project)];
          } else if (splitType === "manual_block_storyboard_pass") {
            validations = [validateManualTimingBlockStoryboardPassImport(importedObject, project)];
          } else {
            validations = [validateManualTimingStoryPassImport(importedObject, project)];
          }
        } else {
          validations = [
            validateManualTimingStoryBiblePassImport(importedObject, project),
            validateManualTimingBlockStoryboardPassImport(importedObject, project),
            validateManualTimingStoryPassImport(importedObject, project),
            validateManualTimingClipPassImport(importedObject, project),
            validateManualTimingPodcastPassImport(importedObject, project),
          ];
        }
      }
      const passedValidation = validations.find((item) => item.ok);
      if (!passedValidation) {
        const validationErrors = validations.flatMap((item) => Array.isArray(item?.errors) ? item.errors : []);
        if (clickedPassType) {
          blockManualTimingPassApply(`${getManualTimingPassNameRu(clickedPassType)} отклонена: ${validationErrors.slice(0, 3).join(" ") || "формат не прошёл проверку"}`, "validator_failed", clickedPassType, getManualTimingJsonPassType(importedObject), MANUAL_TIMING_AI_PASS_BY_TYPE[clickedPassType]?.requires || []);
        } else {
          setCopyStatus(`${workflowLabels.pass} отклонён: ${validationErrors.slice(0, 3).join(" ") || "формат не прошёл проверку"}`);
        }
        return;
      }
      if (clickedPassType && !validateManualTimingActivationOrBlock(importedObject, clickedPassType, getManualTimingJsonPassType(importedObject))) return;
    }
    const importedProject = normalizeManualTimingProjectFromJson(importedObject, project);
    const nextProject = stageToComplete ? buildProjectWithCompletedManualTimingStage(importedProject, stageToComplete) : importedProject;
    persist(nextProject);
    if (stageToComplete) logManualTimingPassApplied(nextProject, stageToComplete);
    if (isCurrentTimingBackupImport) {
      console.info("[MANUAL TIMING BACKUP_RESTORED]", {
        sceneCount: Array.isArray(nextProject?.scenes) ? nextProject.scenes.length : 0,
        audioPhraseCount: Array.isArray(nextProject?.audio_phrases) ? nextProject.audio_phrases.length : 0,
        completedStages: nextProject?.manual_timing_workflow?.completed_stages || [],
      });
    }
    setJsonImportText(JSON.stringify(buildManualTimingExportJson(nextProject), null, 2));
    setCopyStatus(isCurrentTimingBackupImport
      ? `Бэкап тайминга восстановлен: сцен ${nextProject.scenes?.length || 0}, ASR-фраз ${nextProject.audio_phrases?.length || 0}`
      : (stageToComplete ? `Этап “${getManualTimingPassNameRu(stageToComplete)}” применён` : `JSON загружен: сцен ${nextProject.scenes?.length || 0}`));
    window.setTimeout(() => setCopyStatus(""), isCurrentTimingBackupImport ? 2600 : 1800);
    setAudioTime(0, { pause: true, clearBound: true });
  };

  const applyImportedTimingJsonFromMode = () => {
    try {
      const raw = parseJsonImportText();
      applyImportedTimingJson(raw, "");
    } catch (error) {
      setCopyStatus(`Ошибка JSON: ${error?.message || "неверный формат"}`);
    }
  };

  const onImportTimingJson = onApplyStoryCutJson;

  const onImportJsonFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const text = await file.text();
      const raw = JSON.parse(text);
      const passType = getManualTimingJsonPassType(raw);
      const passLabel = MANUAL_TIMING_AI_PASS_BY_TYPE[passType]?.pass_name_ru || (passType === "unknown" ? "неизвестный JSON" : passType);
      setJsonImportText(text);
      setIsJsonImportOpen(true);
      setCopyStatus(`JSON-файл вставлен в поле: ${passLabel}. Нажмите нужную кнопку применения.`);
    } catch (error) {
      setCopyStatus(`Ошибка файла JSON: ${error?.message || "неверный формат"}`);
    }
  };


  const onReplaceTimingAudio = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!isProjectModeSelected) {
      setCopyStatus("Сначала выберите режим проекта");
      return;
    }
    const confirmed = scenes.length || audio.url || audioPhrases.length
      ? window.confirm("Заменить аудио? Текущие ASR-фразы, разметка сцен и Story Pass будут очищены. Активная доска не удаляется — её можно отдельно скачать backup.")
      : true;
    if (!confirmed) return;

    setAudioUploadStatus("uploading");
    setCopyStatus("Загружаю новое аудио…");
    try {
      const [metadata, uploadedAsset] = await Promise.all([
        readAudioFileMetadata(file),
        uploadManualTimingAudioAsset(file),
      ]);
      const uploadedDurationSec = Number(uploadedAsset?.durationSec || uploadedAsset?.duration_sec || 0);
      const duration = uploadedDurationSec > 0
        ? Number(uploadedDurationSec.toFixed(3))
        : Number(metadata.duration_sec || 0);
      const uploadedAssetUrl = String(
        uploadedAsset?.url
        || uploadedAsset?.assetUrl
        || uploadedAsset?.asset_url
        || uploadedAsset?.publicUrl
        || uploadedAsset?.public_url
        || uploadedAsset?.path
        || ""
      ).trim();
      const uploadedAssetFilename = String(
        uploadedAsset?.name
        || uploadedAsset?.filename
        || uploadedAsset?.fileName
        || file.name
        || ""
      ).trim();
      if (!uploadedAssetUrl) throw new Error("asset_url_missing");

      const nextAudio = {
        url: uploadedAssetUrl,
        filename: uploadedAssetFilename,
        duration_sec: Number.isFinite(duration) ? duration : 0,
        duration_ms: Number.isFinite(duration) && duration > 0 ? Math.round(duration * 1000) : Number(metadata.duration_ms || 0),
      };
      const nextProject = buildManualTimingProjectForAudioChange(project, nextAudio, "manual_upload");
      persist(nextProject);
      setAsrStatus("");
      setHandoffStatus("");
      setAudioTime(0, { pause: true, clearBound: true });
      setCopyStatus(`Аудио заменено: ${uploadedAssetFilename || file.name}. Старые ASR/сцены очищены.`);
      window.setTimeout(() => setCopyStatus(""), 3200);
    } catch (error) {
      setCopyStatus(`Не удалось загрузить аудио: ${error?.message || "upload_failed"}`);
    } finally {
      setAudioUploadStatus("");
    }
  };

  const onDeleteTimingAudio = () => {
    if (!audio.url && !scenes.length && !audioPhrases.length) return;
    const confirmed = window.confirm("Удалить текущее аудио и очистить ASR-фразы/сцены тайминга? Активная режиссёрская доска не удаляется.");
    if (!confirmed) return;
    const nextProject = buildManualTimingProjectForAudioChange(project, getEmptyManualTimingAudio(), "none");
    persist(nextProject);
    setAsrStatus("");
    setHandoffStatus("");
    setAudioTime(0, { pause: true, clearBound: true });
    setCopyStatus("Аудио удалено. Тайминг очищен.");
    window.setTimeout(() => setCopyStatus(""), 2200);
  };

  const onRestoreLegacyManualProject = () => {
    const legacyProject = readLegacyManualTimingProject() || readLegacyManualClipBoardProject();
    if (!hasMeaningfulManualProject(legacyProject)) {
      setCopyStatus("Старый проект не найден");
      return;
    }
    const importedObject = unwrapManualProjectBackupJson(legacyProject);
    const nextProject = normalizeManualTimingProjectFromJson(importedObject, project);
    persist(nextProject);
    setJsonImportText(JSON.stringify(buildManualTimingExportJson(nextProject), null, 2));
    setCopyStatus(`Старый проект восстановлен: сцен ${nextProject.scenes?.length || 0}`);
    window.setTimeout(() => setCopyStatus(""), 2200);
    setAudioTime(0, { pause: true, clearBound: true });
  };

  const onReset = () => {
    stopManualTimingPlayback();
    const nextMarkers = durationSec > 0 ? [0, durationSec] : [];
    const nextStoryBlocks = [MANUAL_TIMING_UNKNOWN_STORY_BLOCK];
    const nextScenes = nextMarkers.length ? hydrateManualTimingScenesWithStoryBlocks(buildManualTimingScenesFromMarkers(nextMarkers, [], { durationSec }), nextStoryBlocks) : [];
    rememberManualTimingAction("сброс");
    persist({
      ...project,
      markers: nextMarkers,
      story_blocks: nextStoryBlocks,
      audio_phrases: [],
      scenes: nextScenes,
      selectedSceneId: nextScenes[0]?.scene_id || "",
      timing_status: durationSec > 0 ? "draft" : "empty",
    });
    setAudioTime(0, { pause: true, clearBound: true });
  };

  const onAudioLoadedMetadata = () => {
    const audioEl = audioRef.current;
    if (!audioEl) return;
    if (Array.isArray(project.virtual_silence_blocks) && project.virtual_silence_blocks.length) return;
    const nextDuration = Number(audioEl.duration || 0);
    if (!(nextDuration > 0)) return;
    const currentDuration = Number(project?.audio?.duration_sec || 0);
    if (Array.isArray(project.virtual_silence_blocks) && project.virtual_silence_blocks.length) return;
    if (currentDuration > nextDuration + 0.05) return;
    if (Math.abs(nextDuration - currentDuration) < 0.05) return;
    const nextAudio = {
      ...audio,
      duration_sec: Number(nextDuration.toFixed(3)),
      duration_ms: Math.round(nextDuration * 1000),
    };
    const nextMarkers = normalizeManualTimingMarkers(project.markers?.length ? project.markers : [0, nextAudio.duration_sec], nextAudio.duration_sec);
    const nextScenes = hydrateManualTimingScenesWithStoryBlocks(buildManualTimingScenesFromMarkers(nextMarkers, project.scenes, { durationSec: nextAudio.duration_sec }), project.story_blocks);
    persist({
      ...project,
      audio: nextAudio,
      markers: nextMarkers,
      story_blocks: normalizeManualTimingStoryBlocks(project.story_blocks),
      audio_phrases: normalizeManualTimingAudioPhrases(project.audio_phrases),
      scenes: nextScenes,
      selectedSceneId: project.selectedSceneId || nextScenes[0]?.scene_id || "",
      timing_status: project.timing_status === "empty" ? "draft" : project.timing_status,
    });
  };

  const getTimelineTimeFromEvent = (event) => {
    const el = timelineRef.current;
    if (!el || !(durationSec > 0)) return 0;
    const rect = el.getBoundingClientRect();
    const ratio = Math.max(0, Math.min(1, (event.clientX - rect.left) / Math.max(1, rect.width)));
    return roundTimingSec(ratio * durationSec);
  };

  const onTimelineSeek = (event) => {
    const time = getTimelineTimeFromEvent(event);
    setAudioTime(time, { clearBound: true });
  };

  const onTimelineViewportScroll = (event) => {
    setTimelineScrollLeft(Math.round(event.currentTarget.scrollLeft || 0));
  };

  const toggleStoryBlockGroupSceneSelection = (scene) => {
    const sceneId = String(scene?.scene_id || "").trim();
    if (!sceneId) return;

    setGroupSelectedSceneIds((selectedIds) => {
      const exists = selectedIds.some((selectedId) => String(selectedId || "") === sceneId);
      const nextIds = exists
        ? selectedIds.filter((selectedId) => String(selectedId || "") !== sceneId)
        : [...selectedIds, sceneId];
      console.info("[MANUAL TIMING STORY BLOCK GROUP_SELECTION_TOGGLE]", {
        sceneId,
        selectedSceneIds: nextIds,
      });
      return nextIds;
    });
  };

  const clearStoryBlockGroupSelection = () => {
    setGroupSelectedSceneIds([]);
  };

  const onTimelineSegmentClick = (event, scene) => {
    event.stopPropagation();
    if (event.ctrlKey || event.metaKey) {
      toggleStoryBlockGroupSceneSelection(scene);
      return;
    }
    selectSceneAndSeekStart(scene, { pause: true });
  };

  const getManualStoryBlocksWithScenes = (blocks = []) => {
    const unknownBlockId = String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || "");
    return normalizeManualTimingStoryBlocks(blocks).filter((block) => {
      const blockId = String(block?.block_id || "").trim();
      if (!blockId || blockId === unknownBlockId) return false;
      return (Array.isArray(block?.scene_ids) ? block.scene_ids : [])
        .map((sceneId) => String(sceneId || "").trim())
        .filter(Boolean)
        .length > 0;
    });
  };

  const getNextManualStoryBlockNumber = (blocks = []) => getManualStoryBlocksWithScenes(blocks).length + 1;

  const getNextManualStoryBlockId = (blocks = []) => {
    const normalizedBlocks = normalizeManualTimingStoryBlocks(blocks);
    const usedIds = new Set(normalizedBlocks.map((block) => String(block?.block_id || "")).filter(Boolean));
    const blockNumber = getNextManualStoryBlockNumber(normalizedBlocks);
    const baseBlockId = `manual_story_block_${String(blockNumber).padStart(2, "0")}`;
    if (!usedIds.has(baseBlockId)) return { blockId: baseBlockId, blockNumber };

    let suffix = 2;
    let blockId = `${baseBlockId}_${suffix}`;
    while (usedIds.has(blockId)) {
      suffix += 1;
      blockId = `${baseBlockId}_${suffix}`;
    }
    return { blockId, blockNumber };
  };

  const getNextManualStoryBlockColor = (blocks = []) => {
    const realBlockCount = getManualStoryBlocksWithScenes(blocks).length;
    return STORY_BLOCK_COLORS[realBlockCount % STORY_BLOCK_COLORS.length] || "#37d6c2";
  };

  const selectedGroupScenesInTimelineOrder = () => {
    if (!groupSelectedSceneIds.length) return [];
    const selectedIds = new Set(groupSelectedSceneIds.map((sceneId) => String(sceneId || "")).filter(Boolean));
    return scenes.filter((scene) => selectedIds.has(String(scene?.scene_id || "")));
  };

  const areScenesAdjacentInTimeline = (selectedScenes = []) => {
    if (selectedScenes.length <= 1) return true;
    const selectedIds = new Set(selectedScenes.map((scene) => String(scene?.scene_id || "")));
    const indexes = scenes
      .map((scene, index) => selectedIds.has(String(scene?.scene_id || "")) ? index : -1)
      .filter((index) => index >= 0);
    if (indexes.length !== selectedScenes.length) return false;
    for (let idx = 1; idx < indexes.length; idx += 1) {
      if (indexes[idx] !== indexes[idx - 1] + 1) return false;
    }
    return true;
  };

  const rejectManualStoryBlockGrouping = (reason, message = "") => {
    console.info("[MANUAL TIMING STORY BLOCK_GROUP_REJECTED]", {
      reason,
      selectedSceneIds: groupSelectedSceneIds,
    });
    if (message) {
      setCopyStatus(message);
      window.setTimeout(() => setCopyStatus(""), 2200);
    }
  };

  const resetStoryBlockDialog = () => {
    setStoryBlockDialog({
      isOpen: false,
      selectedSceneIds: [],
      defaultTitle: "",
      title: "",
      color: "",
      hasExistingStoryBlock: false,
      confirmMoveExisting: false,
    });
  };

  const clearManualStoryBlocks = () => {
    stopManualTimingPlayback();
    const confirmed = window.confirm("Очистить старые смысловые блоки? Сцены и разрезы останутся.");
    if (!confirmed) return;

    const oldBlockCount = normalizeManualTimingStoryBlocks(project.story_blocks)
      .filter((block) => String(block?.block_id || "") !== String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || ""))
      .length;
    const nextStoryBlocks = [MANUAL_TIMING_UNKNOWN_STORY_BLOCK];
    const nextScenes = scenes.map((scene) => {
      const { story_block_id, story_block_title_ru, story_block_color, ...restScene } = scene || {};
      return restScene;
    });

    rememberManualTimingAction("очистить story blocks");
    persist({
      ...project,
      story_blocks: nextStoryBlocks,
      scenes: nextScenes,
      selectedSceneId: project.selectedSceneId || nextScenes[0]?.scene_id || "",
    });
    resetStoryBlockDialog();
    console.info("[MANUAL TIMING STORY BLOCKS_CLEARED]", {
      sceneCount: nextScenes.length,
      oldBlockCount,
      newBlockCount: nextStoryBlocks.length,
      markersPreserved: true,
    });
    setCopyStatus("Смысловые блоки очищены. Сцены и разрезы сохранены.");
    window.setTimeout(() => setCopyStatus(""), 2400);
  };

  const createManualStoryBlockFromSelection = () => {
    const selectedScenes = selectedGroupScenesInTimelineOrder();
    const selectedSceneIds = selectedScenes.map((scene) => String(scene?.scene_id || "")).filter(Boolean);

    if (!selectedSceneIds.length) {
      rejectManualStoryBlockGrouping("empty_selection", "Выберите сцены через Ctrl+Click.");
      return;
    }

    if (!areScenesAdjacentInTimeline(selectedScenes)) {
      rejectManualStoryBlockGrouping("non_adjacent_selection", "Можно объединять только соседние сцены.");
      return;
    }

    const unknownBlockId = String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || "");
    const hasExistingStoryBlock = selectedScenes.some((scene) => {
      const storyBlockId = String(scene?.story_block_id || "").trim();
      return storyBlockId && storyBlockId !== unknownBlockId;
    });
    const { blockNumber } = getNextManualStoryBlockId(project.story_blocks);
    const defaultTitle = `Смысловой блок ${blockNumber}`;
    const color = getNextManualStoryBlockColor(project.story_blocks);
    setStoryBlockDialog({
      isOpen: true,
      selectedSceneIds,
      defaultTitle,
      title: defaultTitle,
      color,
      hasExistingStoryBlock,
      confirmMoveExisting: !hasExistingStoryBlock,
    });
    console.info("[MANUAL TIMING STORY BLOCK_DIALOG_OPEN]", {
      selectedSceneIds,
      defaultTitle,
      color,
    });
  };

  const submitManualStoryBlockDialog = () => {
    if (!storyBlockDialog.isOpen) return;
    const selectedSceneIdSet = new Set((Array.isArray(storyBlockDialog.selectedSceneIds) ? storyBlockDialog.selectedSceneIds : [])
      .map((sceneId) => String(sceneId || "").trim())
      .filter(Boolean));
    const selectedScenes = scenes.filter((scene) => selectedSceneIdSet.has(String(scene?.scene_id || "")));
    const selectedSceneIds = selectedScenes.map((scene) => String(scene?.scene_id || "")).filter(Boolean);

    if (!selectedSceneIds.length) {
      rejectManualStoryBlockGrouping("dialog_empty_selection", "Выбранные сцены больше не найдены.");
      resetStoryBlockDialog();
      return;
    }
    if (!areScenesAdjacentInTimeline(selectedScenes)) {
      rejectManualStoryBlockGrouping("dialog_non_adjacent_selection", "Можно объединять только соседние сцены.");
      resetStoryBlockDialog();
      return;
    }
    if (storyBlockDialog.hasExistingStoryBlock && !storyBlockDialog.confirmMoveExisting) {
      rejectManualStoryBlockGrouping("move_existing_not_confirmed", "Подтвердите перенос сцен в новый смысловой блок.");
      return;
    }

    const { blockId } = getNextManualStoryBlockId(project.story_blocks);
    const title = String(storyBlockDialog.title || "").trim() || storyBlockDialog.defaultTitle;
    const color = String(storyBlockDialog.color || "").trim() || getNextManualStoryBlockColor(project.story_blocks);
    const startSec = roundTimingSec(Math.min(...selectedScenes.map((scene) => Number(scene?.start_sec || 0))));
    const endSec = roundTimingSec(Math.max(...selectedScenes.map((scene) => Number(scene?.end_sec || 0))));
    const selectedIdSet = new Set(selectedSceneIds);
    const unknownBlockId = String(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || "");
    const normalizedBlocks = normalizeManualTimingStoryBlocks(project.story_blocks);
    const newBlock = {
      block_id: blockId,
      title_ru: title,
      scene_ids: selectedSceneIds,
      start_sec: startSec,
      end_sec: endSec,
      color,
    };
    const nextStoryBlocks = [
      ...normalizedBlocks.map((block) => ({
        ...block,
        scene_ids: (Array.isArray(block?.scene_ids) ? block.scene_ids : [])
          .map((sceneId) => String(sceneId || "").trim())
          .filter((sceneId) => sceneId && !selectedIdSet.has(sceneId)),
      })),
      newBlock,
    ];
    const nextScenes = scenes.map((scene) => selectedIdSet.has(String(scene?.scene_id || ""))
      ? {
        ...scene,
        story_block_id: blockId,
        story_block_title_ru: title,
        story_block_color: color,
      }
      : scene);
    const normalizedNextStoryBlocks = normalizeManualTimingStoryBlocks(nextStoryBlocks).map((block) => {
      const derived = deriveStoryBlockRangeFromScenes(block, nextScenes);
      if (derived) {
        return {
          ...block,
          scene_ids: derived.scene_ids,
          start_sec: derived.start_sec,
          end_sec: derived.end_sec,
        };
      }
      return {
        ...block,
        scene_ids: [],
        start_sec: 0,
        end_sec: 0,
      };
    }).filter((block) => {
      const blockIdValue = String(block?.block_id || "").trim();
      if (blockIdValue === unknownBlockId) return true;
      return (Array.isArray(block?.scene_ids) ? block.scene_ids : []).length > 0;
    });

    rememberManualTimingAction("смысловой блок");
    persist({
      ...project,
      story_blocks: normalizedNextStoryBlocks,
      scenes: nextScenes,
      selectedSceneId: selectedSceneIds[0] || project.selectedSceneId || "",
    });
    console.info("[MANUAL TIMING STORY BLOCK_GROUP_CREATED]", {
      blockId,
      title,
      sceneIds: selectedSceneIds,
      startSec,
      endSec,
      color,
    });
    setCopyStatus(`Создан смысловой блок: ${title}`);
    window.setTimeout(() => setCopyStatus(""), 2200);
    setGroupSelectedSceneIds([]);
    setSelectedMissingPhraseId("");
    resetStoryBlockDialog();
  };

  const onStoryBlockClick = (block) => {
    const firstSceneId = Array.isArray(block?.scene_ids) ? block.scene_ids.find(Boolean) : "";
    const scene = scenes.find((item) => item.scene_id === firstSceneId) || scenes.find((item) => item.story_block_id === block?.block_id);
    if (scene) selectSceneAndSeekStart(scene, { pause: true });
  };

  const getSegmentStyle = (scene, idx) => {
    const left = durationSec > 0 ? (Number(scene.start_sec || 0) / durationSec) * 100 : 0;
    const width = durationSec > 0 ? ((Number(scene.end_sec || 0) - Number(scene.start_sec || 0)) / durationSec) * 100 : 0;
    const color = SEGMENT_COLORS[idx % SEGMENT_COLORS.length];
    return {
      left: `${left}%`,
      width: `${Math.max(0.25, width)}%`,
      "--segment-color": color,
    };
  };

  const getStoryBlockRangeStyle = (blockRange) => {
    const left = durationSec > 0 ? (Number(blockRange.start_sec || 0) / durationSec) * 100 : 0;
    const width = durationSec > 0 ? ((Number(blockRange.end_sec || 0) - Number(blockRange.start_sec || 0)) / durationSec) * 100 : 0;
    return {
      left: `${Math.max(0, Math.min(100, left))}%`,
      width: `${Math.max(0.5, Math.min(100, width))}%`,
      "--story-block-color": blockRange.color || "#64748B",
    };
  };

  const markerPercents = useMemo(() => {
    if (!(durationSec > 0)) return [];
    return normalizeManualTimingMarkers(project.markers, durationSec).map((marker) => ({
      value: marker,
      left: `${Math.max(0, Math.min(100, (Number(marker) / durationSec) * 100))}%`,
    }));
  }, [project.markers, durationSec]);

  const legacyManualProject = useMemo(
    () => readLegacyManualTimingProject() || readLegacyManualClipBoardProject(),
    []
  );
  const showLegacyRestore = hasMeaningfulManualProject(legacyManualProject)
    && (!hasMeaningfulManualProject(project) || !isProjectModeSelected);

  return (
    <>
    <div className={`manualTimingPage pageCard ${modeConfig.className}`} data-build="manual-timing-stable-v18" data-story-bible-build="manual-timing-story-bible-v21" data-json-help-build="manual-timing-json-help-v22">
      <div className="manualTimingModeHeader">
        <div className="manualTimingModeTitleBlock">
          <h1 className="pageTitle">{modeConfig.title}</h1>
          <div className="manualTimingModeSubtitle">{modeConfig.subtitle}</div>
        </div>
        <div className="manualTimingModeHeaderActions">
          <button className="clipSB_btn clipSB_btnSecondary manualTimingBackButton" onClick={onBackToNode}>← Назад к ноде</button>
          <span className="manualTimingModeBadge">{modeConfig.badge}</span>
        </div>
      </div>
      <div className="manualTimingModeHint">{modeConfig.hint}</div>
      {!isProjectModeSelected ? <div className="manualTimingModeMissing">Режим проекта не выбран. Вернитесь в ноду и выберите тип проекта.</div> : null}
      {canRecoverPodcastProjectToStory ? <div className="manualTimingCompactActions manualTimingRecoveryActions">
        <button className="clipSB_btn clipSB_btnPrimary" type="button" onClick={onRecoverPodcastProjectToStory}>Вернуть режим: История</button>
        <span className="manualTimingWorkflowStatus">Без сброса аудио, сцен, маркеров и story_blocks.</span>
      </div> : null}
      {hasActiveBoardProject ? <div className="manualTimingActiveBoardWarning">
        <div>
          <b>🎬 Активная доска</b>
          <span>Есть сохранённая доска. Можно скачать backup или начать новый разбор.</span>
          <span>Сцен: {activeBoardScenes.length} · Блоков: {activeBoardBlocks.length} · Обновлено: {formatManualBoardUpdatedAt(activeBoardProject.updatedAt || activeBoardProject.updated_at)}</span>
        </div>
        <div className="manualTimingActiveBoardActions">
          <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={onDownloadActiveBoardBackup}>Скачать backup</button>
          <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={onStartNewAnalysisWithConfirm}>Начать новый разбор</button>
        </div>
      </div> : null}
      {showLegacyRestore ? <div className="manualTimingLegacyRestore">
        <div>
          <b>Найден старый проект в браузере</b>
          <span>Он не подхватывается автоматически для текущего аккаунта. Восстановите его вручную, если это ваш проект.</span>
        </div>
        <button className="clipSB_btn clipSB_btnPrimary" onClick={onRestoreLegacyManualProject}>Восстановить в текущий аккаунт</button>
      </div> : null}
      <div className="manualTimingMetaGrid">
        <div><b>Файл:</b> {audio.filename || "аудио не выбрано"}</div>
        <div><b>Длительность:</b> {formatTimingSec(durationSec)}</div>
        <div><b>Курсор:</b> {formatTimingSec(currentTime)}</div>
        <div><b>Сцен:</b> {scenes.length}</div>
        <div><b>Статус:</b> {readableTimingStatus}</div>
        <label className="manualTimingFormatControl"><b>Формат:</b>
          <select value={projectFormat} disabled={isFormatLocked} onChange={(e) => persist({ ...project, format: e.target.value, aspect_ratio: e.target.value, format_locked: false })}>
            <option value="9:16">9:16</option>
            <option value="16:9">16:9</option>
            <option value="1:1">1:1</option>
          </select>
        </label>
      </div>
      {isFormatLocked ? <div className="manualTimingFormatLockHint">Формат зафиксирован после Story Pass, подтверждения тайминга, создания доски или материалов. Чтобы изменить формат, начните новый проект.</div> : null}
      <div className="manualTimingCompactActions manualTimingAudioReplaceActions">
        <label className={`clipSB_btn clipSB_btnSecondary ${isTimingAudioUploading ? "isDisabled" : ""}`}>
          {isTimingAudioUploading ? "Загрузка аудио…" : (audio.url ? "Заменить аудио" : "Загрузить аудио")}
          <input type="file" accept="audio/*" onChange={onReplaceTimingAudio} disabled={isTimingAudioUploading || mainActionsDisabled} hidden />
        </label>
        <button className="clipSB_btn clipSB_btnDanger" type="button" onClick={onDeleteTimingAudio} disabled={isTimingAudioUploading || mainActionsDisabled || (!audio.url && !scenes.length && !audioPhrases.length)}>Удалить аудио</button>
        <span className="manualTimingWorkflowStatus">Замена аудио очистит старый разбор.</span>
      </div>

      <section className="manualTimingTransport">
        {audio.url ? <audio
          ref={audioRef}
          className="manualTimingAudioEngine"
          src={audio.url}
          onLoadedMetadata={onAudioLoadedMetadata}
          onTimeUpdate={onTimeUpdate}
          onSeeked={() => {
            const audioEl = audioRef.current;
            if (audioEl && !audioEl.paused) syncCurrentTimeFromAudio();
          }}
          onPlay={() => { setPlayingState(true); startRafLoop(); }}
          onPause={() => { setPlayingState(false); stopRafLoop(); }}
          onEnded={() => {
            const endTime = durationSecRef.current || durationSec;
            stopManualTimingPlayback();
            setDisplayTime(endTime);
          }}
        /> : <div className="manualTimingNoAudio">Аудио не выбрано. Подключите AUDIO-ноду или загрузите аудио в ноде “Тайминг песни”.</div>}

        <div className="manualTimingPlayerShell">
          <div className="manualTimingPlayerHeader">
            <div><b>Главная шкала разметки</b></div>
            <div>верхняя полоса — story blocks · нижняя — сцены · линия — текущее место · двойной клик по сцене — быстрая правка</div>
          </div>
          <div className={`manualTimingSelectedPhraseInspector ${isGroupPhraseInspectorMode ? "isGroupMode" : ""} ${selectedSceneHasPartialPhrase ? "hasPartialPhrase" : ""}`}>
            <div className="manualTimingSelectedPhraseMeta">
              {isGroupPhraseInspectorMode ? <>
                <span>Выбранный блок</span>
                <strong>{selectedGroupScenes.length} сцен</strong>
                <span>{selectedGroupFirstSceneId} → {selectedGroupLastSceneId} · {formatTimingSec(selectedGroupStartSec)} → {formatTimingSec(selectedGroupEndSec)}</span>
              </> : <>
                <span>scene_id</span>
                <strong>{selectedScene?.scene_id || "—"}</strong>
                <span>{selectedScene ? `${formatTimingSec(selectedSceneStartSec)} → ${formatTimingSec(selectedSceneEndSec)}` : "тайминг —"}</span>
              </>}
              {selectedSceneHasPartialPhrase ? <b className="manualTimingPartialBadge">частично</b> : null}
            </div>
            <div className="manualTimingSelectedPhraseRows">
              {isGroupPhraseInspectorMode || selectedScene ? (selectedScenePhraseInspectorRows.length ? selectedScenePhraseInspectorRows.map((row) => <div
                key={`selected-phrase-inspector-${row.phraseId || row.timingLabel}`}
                className={`manualTimingSelectedPhraseRow ${row.isPartial ? "isPartial" : ""}`}
              >
                <div className="manualTimingSelectedPhraseOriginal">
                  <span>{row.phraseId || "audio_phrase"} · {row.timingLabel}</span>
                  <p>{row.originalText}</p>
                </div>
                <div className="manualTimingSelectedPhraseRu">
                  <div className="manualTimingSelectedPhraseRuHeader">
                    <span>RU</span>
                    <button
                      className="clipSB_btn clipSB_btnSecondary manualTimingSelectedPhraseSpeakBtn"
                      type="button"
                      onClick={() => speakManualTimingRuText(row.ruText)}
                      disabled={!row.hasRuText}
                    >
                      ▶ RU
                    </button>
                  </div>
                  <p>{row.ruText}</p>
                </div>
              </div>) : <div className="manualTimingSelectedPhraseEmpty">{isGroupPhraseInspectorMode ? "В выбранном блоке нет ASR-фраз." : "В выбранной сцене нет ASR-фразы."}</div>) : <div className="manualTimingSelectedPhraseEmpty">Выбери сцену, чтобы увидеть ASR-фразу и перевод.</div>}
            </div>
            <div className="manualTimingSelectedPhraseActions">
              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => speakManualTimingRuText(selectedSceneFullRuText)} disabled={!selectedSceneHasRuText}>▶ весь перевод</button>
              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={stopManualTimingSpeech}>■ стоп</button>
              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={refreshSceneSourcePhraseIds} disabled={!scenes.length || !audioPhrases.length}>Обновить фразы</button>
              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={onDownloadAsrTranslationJson} disabled={!audioPhrases.length}>Скачать JSON для перевода ASR</button>
              <label className={`clipSB_btn clipSB_btnSecondary manualTimingFileBtn ${!audioPhrases.length ? "isDisabled" : ""}`}>
                Импорт ASR text_ru
                <input type="file" accept="application/json,.json,text/plain" onChange={onImportAsrTranslationJsonFile} disabled={!audioPhrases.length} />
              </label>
            </div>
          </div>
          <div
            className="manualTimingTimelineViewport"
            ref={timelineViewportRef}
            onScroll={onTimelineViewportScroll}
          >
            <div
              className="manualTimingTimelineInner"
              style={timelineInnerStyle}
            >
              <div
                className="manualTimingPlayerTrack"
                ref={timelineRef}
                onClick={onTimelineSeek}
                role="button"
                tabIndex={0}
                title="Кликни по шкале, чтобы перейти к этому месту"
              >
                <div className="manualTimingUncutTrack" />
                {timelineBlockRanges.length ? <div className="manualTimingBlockTrack" aria-label="смысловые блоки на шкале">
                  {timelineBlockRanges.map((blockRange) => <button
                    key={`player-block-${blockRange.block_id}`}
                    type="button"
                    className="manualTimingBlockRange"
                    style={getStoryBlockRangeStyle(blockRange)}
                    onClick={(event) => { event.stopPropagation(); onStoryBlockClick(blockRange); }}
                    title={`${blockRange.title}: ${formatTimingSec(blockRange.start_sec)} – ${formatTimingSec(blockRange.end_sec)}`}
                  >
                    <span className="manualTimingBlockRangeLabel">{blockRange.title}</span>
                  </button>)}
                </div> : null}
                {asrPhraseMarkers.length ? <div className="manualTimingPhraseTrack" aria-label="ASR phrase markers">
                  {asrPhraseMarkers.map((phrase) => <button
                    key={`phrase-marker-${phrase.phrase_id}`}
                    type="button"
                    className="manualTimingPhraseMarker"
                    style={phrase.style}
                    onClick={(event) => { event.stopPropagation(); setSelectedMissingPhraseId(phrase.phrase_id); playRange(phrase.start_sec, phrase.end_sec); }}
                    title={`${phrase.phrase_id}: ${formatTimingSec(phrase.start_sec)} – ${formatTimingSec(phrase.end_sec)} · ${pickManualTimingAudioPhraseOriginalText(phrase) || "ASR phrase"}`}
                  />)}
                </div> : null}
                {scenes.map((scene, idx) => {
                  const isOpenTail = scene.scene_id === openTailSceneId;
                  const isActive = selectedScene?.scene_id === scene.scene_id;
                  const isGroupSelected = groupSelectedSceneIdSet.has(String(scene.scene_id || ""));
                  const durationWarning = getManualTimingSceneDurationWarning(scene);
                  const isSilence = isManualTimingSilenceScene(scene);
                  return <button
                    key={`player-${scene.scene_id}`}
                    className={`manualTimingPlayerSegment ${isOpenTail ? "isOpenTail" : "isCut"} ${isSilence ? "isSilence" : ""} ${isActive ? "isActive" : ""} ${isGroupSelected ? "isGroupSelected" : ""}`}
                    style={getSegmentStyle(scene, idx)}
                    onClick={(event) => onTimelineSegmentClick(event, scene)}
                    onDoubleClick={(event) => { event.stopPropagation(); openQuickEdit(scene); }}
                    title={`${scene.scene_id}: ${formatTimingSec(scene.start_sec)} – ${formatTimingSec(scene.end_sec)}${isSilence ? " · тишина" : ""}${durationWarning ? ` · ${durationWarning.text}` : ""}. Ctrl+Click — выбрать для смыслового блока. Двойной клик — быстрая правка`}
                  >
                    <span>
                      {isSilence ? "тишина" : scene.scene_id}
                      {isActive ? <small>{formatTimingSec(Math.max(0, Number(scene.end_sec || 0) - Number(scene.start_sec || 0)))}</small> : null}
                    </span>
                  </button>;
                })}
                {candidateWidthPercent > 0.1 ? <div
                  className="manualTimingCandidateRange"
                  style={{ left: `${lastCutPercent}%`, width: `${candidateWidthPercent}%` }}
                  title={`Следующий отрезок: ${formatTimingSec(lastCutSec)} – ${formatTimingSec(currentTime)}`}
                /> : null}
                {markerPercents.map((marker) => <div key={"player-marker-" + marker.value} className="manualTimingPlayerMarker" style={{ left: marker.left }} title={formatTimingSec(marker.value)} />)}
                <div className="manualTimingLastCutLine" style={{ left: `${lastCutPercent}%` }} title={`Старт следующего отрезка: ${formatTimingSec(lastCutSec)}`} />
                <div className="manualTimingPlayhead" style={{ left: `${playheadPercent}%` }}>
                  <span>{formatTimingSec(currentTime)}</span>
                </div>
              </div>
            </div>
          </div>
          <div className="manualTimingPlayerLegend">
            <span><i className="legendBlock" /> story blocks</span>
            <span><i className="legendCut" /> сцены</span>
            <span><i className="legendTail" /> ещё не отрезано</span>
            <span><i className="legendCandidate" /> следующий отрезок</span>
            {audioPhrases.length ? <span><i className="legendAsrPhrase" /> ASR phrase map</span> : null}
            {SHOW_MISSING_PHRASE_TOOLS && audioPhrases.length ? <span><i className="legendMissingPhrase" /> пропущенная фраза</span> : null}
          </div>
          <div className="manualTimingTrackToolbox">
            <div className="manualTimingPlayStack">
              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingMiniPlayButton"
                onClick={onPlayPause}
                disabled={!audio.url}
                title={isPlaying ? "Пауза" : "Слушать выбранную сцену / с текущего места"}
              >
                {isPlaying && manualTimingPlaybackMode !== "full_timeline" ? "⏸" : "▶"}
              </button>

              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingFullPlaybackButton"
                type="button"
                onClick={() => {
                  if (manualTimingPlaybackMode === "full_timeline") {
                    stopManualTimingPlayback();
                  } else {
                    playFullManualTiming();
                  }
                }}
                disabled={!audio.url}
                title="Прослушать весь тайминг с начала"
              >
                {manualTimingPlaybackMode === "full_timeline" ? "■ всё" : "▶ всё"}
              </button>
            </div>

            <div className="manualTimingTrackNudgeBox" aria-label="Микро-доводчик конца выбранной сцены">
              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingNudgeArrow"
                disabled={!canUseTrackNudge}
                onClick={() => nudgeSelectedBoundary(-clampManualTimingStep(trackNudgeStepSec))}
                title="Уменьшить выбранный отрезок"
              >←</button>
              <label className="manualTimingStepControl">
                <span>шаг</span>
                <input
                  type="number"
                  min="0.01"
                  max="30"
                  step="0.01"
                  value={trackNudgeStepSec}
                  onChange={(event) => setTrackNudgeStepSec(clampManualTimingStep(event.target.value))}
                />
              </label>
              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingNudgeArrow"
                disabled={!canUseTrackNudge}
                onClick={() => nudgeSelectedBoundary(clampManualTimingStep(trackNudgeStepSec))}
                title="Увеличить выбранный отрезок"
              >→</button>
              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingCutButton"
                onClick={onAddMarker}
                disabled={!canCutAtCurrentTime}
                title="Разрезать сцену по текущей жёлтой линии"
              >✂ Разрезать</button>
              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingMergeButton"
                type="button"
                onClick={mergeSelectedSceneWithNext}
                disabled={!canMergeSelectedWithNext}
                title="Соединить выбранную сцену со следующей соседней сценой"
              >🔗 Соединить</button>
              <div className="manualTimingStoryGroupControls" aria-label="Ручная группировка смыслового блока">
                <button
                  className="clipSB_btn clipSB_btnSecondary manualTimingStoryGroupButton"
                  type="button"
                  onClick={createManualStoryBlockFromSelection}
                  disabled={!groupSelectedSceneIds.length}
                  title="Создать смысловой блок из выбранных Ctrl+Click соседних сцен"
                >＋ Смысловой блок</button>
                <button
                  className="clipSB_btn clipSB_btnSecondary manualTimingStoryGroupClearButton"
                  type="button"
                  onClick={clearStoryBlockGroupSelection}
                  disabled={!groupSelectedSceneIds.length}
                  title="Снять выбор сцен для смыслового блока"
                >Снять выбор</button>
                <button
                  className="clipSB_btn clipSB_btnSecondary manualTimingStoryGroupResetButton"
                  type="button"
                  onClick={clearManualStoryBlocks}
                  disabled={!scenes.length && !storyBlocks.length}
                  title="Очистить только story_blocks: сцены, разрезы и маркеры останутся"
                >Очистить блоки</button>
                <span className="manualTimingStoryGroupCount">Выбрано сцен: {groupSelectedSceneIds.length}</span>
              </div>
              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingSilenceButton"
                type="button"
                onClick={insertSilenceAtCursor}
                disabled={!audio.url || !(durationSec > 0)}
                title="Вставить 0.5 сек тишины по текущему курсору / после выбранного места"
              >тишина после</button>
              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingSilenceButton"
                type="button"
                onClick={insertSilenceBeforeSelectedScene}
                disabled={!audio.url || !(durationSec > 0)}
                title="Вставить 1 сек тишины перед выбранной сценой"
              >тишина до</button>
              <button
                className="clipSB_btn clipSB_btnDanger manualTimingResetMiniButton"
                onClick={onReset}
                disabled={mainActionsDisabled}
                title="Сбросить текущий тайминг и разбор"
              >сброс</button>
              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingUndoButton"
                onClick={undoLastManualTimingAction}
                disabled={!undoStack.length}
                title="Отменить последнее действие"
              >↶ назад</button>
              <button
                className="clipSB_btn clipSB_btnSecondary manualTimingUndoButton"
                onClick={redoLastManualTimingAction}
                disabled={!redoStack.length}
                title="Вернуть отменённое действие"
              >↷ вернуть</button>
            </div>

            {storyBlockDialog.isOpen ? <div className="manualTimingStoryBlockDialog" role="dialog" aria-modal="false" aria-label="Создание смыслового блока">
              <div className="manualTimingStoryBlockDialogHeader">
                <strong>Новый смысловой блок</strong>
                <span>{storyBlockDialog.selectedSceneIds.length} сцен</span>
              </div>
              {storyBlockDialog.hasExistingStoryBlock ? <div className="manualTimingStoryBlockDialogWarning">
                <p>Эти сцены уже входят в другой смысловой блок. Перенести их в новый блок?</p>
                <label>
                  <input
                    type="checkbox"
                    checked={storyBlockDialog.confirmMoveExisting}
                    onChange={(event) => setStoryBlockDialog((dialog) => ({ ...dialog, confirmMoveExisting: event.target.checked }))}
                  />
                  <span>Да, перенести выбранные сцены</span>
                </label>
              </div> : null}
              <label className="manualTimingStoryBlockDialogField">
                <span>Название</span>
                <input
                  type="text"
                  value={storyBlockDialog.title}
                  placeholder={storyBlockDialog.defaultTitle}
                  onChange={(event) => setStoryBlockDialog((dialog) => ({ ...dialog, title: event.target.value }))}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") submitManualStoryBlockDialog();
                    if (event.key === "Escape") resetStoryBlockDialog();
                  }}
                  autoFocus
                />
              </label>
              <div className="manualTimingStoryBlockDialogColor">
                <span style={{ background: storyBlockDialog.color || "#37d6c2" }} />
                <b>Автоцвет блока</b>
                <code>{storyBlockDialog.color || "#37d6c2"}</code>
              </div>
              <div className="manualTimingStoryBlockDialogActions">
                <button
                  className="clipSB_btn clipSB_btnSecondary"
                  type="button"
                  onClick={resetStoryBlockDialog}
                >Отмена</button>
                <button
                  className="clipSB_btn clipSB_btnPrimary"
                  type="button"
                  onClick={submitManualStoryBlockDialog}
                  disabled={storyBlockDialog.hasExistingStoryBlock && !storyBlockDialog.confirmMoveExisting}
                >Создать блок</button>
              </div>
            </div> : null}

        <details className="manualTimingJsonPanel manualTimingJsonPanelCompact manualTimingJsonPanelTrack">
          <summary className="manualTimingJsonSummary">
            <strong>JSON / экспорт</strong>
            <span>{workflowLabels.panelTitle}</span>
          </summary>
          <div className="manualTimingJsonHeader">
            <strong>{workflowLabels.panelTitle}</strong>
            <span>{workflowLabels.panelHint}</span>
            {canRecoverPodcastProjectToStory ? <button className="clipSB_btn clipSB_btnPrimary" type="button" onClick={onRecoverPodcastProjectToStory}>Вернуть режим: История</button> : null}
          </div>
          <details className="manualTimingJsonHelpBox manualTimingAiHelpBox">
            <summary>Как пользоваться AI JSON workflow</summary>
            <div className="manualTimingAiHelpIntro">
              Этот workflow нужен, чтобы поэтапно подготовить историю для режиссёрской доски. Каждый этап делается отдельно: сначала смысл сцен, потом общий паспорт истории, потом блочная раскадровка.
            </div>
            <div className="manualTimingJsonHelpGrid manualTimingAiHelpGrid">
              <div className="manualTimingJsonHelpStep">
                <b>1 · Audio Phrase Map</b>
                <span>Сначала загрузи аудио и запусти ASR. Это создаёт audio_phrases — техническую карту речи с таймкодами.</span>
              </div>
              <div className="manualTimingJsonHelpStep">
                <b>2 · Собрать сцены</b>
                <span>Собери черновые сцены из ASR. После этого у тебя появляется база для AI-этапов.</span>
              </div>
              <div className="manualTimingJsonHelpStep">
                <b>3 · Смысловая нарезка</b>
                <span>Нажми “Скопировать смысловую нарезку”, отправь JSON в ChatGPT/Gemini, затем вставь Semantic Story Cut JSON и нажми “Применить смысловую нарезку”. Этап заполняет смысл сцен, story_blocks, translated_text_ru, meaning_hint_ru, scene_goal_ru, photo_prompt_hint_ru, prompt_hint_ru и другие смысловые поля. video_prompt / negative_prompt / sound_prompt здесь ещё не заполняются.</span>
              </div>
              <div className="manualTimingJsonHelpStep">
                <b>4 · Библия истории</b>
                <span>После смысловой нарезки станет доступна “Библия истории”. Скопируй JSON, отправь в ChatGPT/Gemini, затем вставь ответ и нажми “Применить библию истории”. Этап обновляет только project_* поля: project_story_summary_ru, visual/style/world/continuity lock и общий паспорт истории. Сцены, тайминги и ASR-фразы он не меняет.</span>
              </div>
              <div className="manualTimingJsonHelpStep">
                <b>5 · Блочная раскадровка</b>
                <span>После библии истории станет доступна “Блочная раскадровка”. Скопируй JSON, отправь в ChatGPT/Gemini, затем вставь ответ и нажми “Применить блочную раскадровку”. Этап готовит block_visual_bible_ru, block_storyboard_summary_ru, storyboard_frame_role_ru, source_image_prompt_en/ru, i2v_prompt_en, composition_ru, camera_angle_ru, subject/background lock и другие storyboard-поля.</span>
              </div>
              <div className="manualTimingJsonHelpStep">
                <b>6 · Подтвердить тайминг</b>
                <span>Когда нужные этапы применены и всё выглядит правильно — подтверди тайминг.</span>
              </div>
              <div className="manualTimingJsonHelpStep">
                <b>7 · Открыть режиссёрскую доску</b>
                <span>После блочной раскадровки и подтверждения тайминга можно переходить в Director Board / Storyboard.</span>
              </div>
            </div>
            <div className="manualTimingJsonHelpNote manualTimingAiHelpNote">
              <b>Activation phrase:</b> Каждый AI-ответ должен вернуть activation_phrase. Без него следующий этап не откроется:<br />
              - SEMANTIC_STORY_CUT_DONE открывает Библию истории<br />
              - STORY_BIBLE_DONE открывает Блочную раскадровку<br />
              - BLOCK_STORYBOARD_DONE открывает Director Board
            </div>
            <div className="manualTimingJsonHelpNote manualTimingAiHelpNote">
              <b>Важно:</b> Current timing backup — это не AI-pass, а резервная копия для восстановления проекта. AI JSON этапы применяются только через соответствующие кнопки этапов. Если вставлен JSON не того этапа, нужно нажимать правильную кнопку применения.
            </div>
          </details>
          <div className="manualTimingAiWorkflow">
            <div className="manualTimingJsonActions manualTimingJsonActionsV21 manualTimingAiWorkflowTopActions">
              <button className="clipSB_btn clipSB_btnSecondary" onClick={() => setIsJsonImportOpen((value) => !value)} disabled={!isProjectModeSelected}>
                {isJsonImportOpen ? "Скрыть поле JSON" : "Вставить / показать JSON"}
              </button>
              <label className={`clipSB_btn clipSB_btnSecondary manualTimingFileBtn ${!isProjectModeSelected ? "isDisabled" : ""}`}>
                Импорт файла JSON
                <input type="file" accept="application/json,.json,text/plain" onChange={onImportJsonFile} disabled={!isProjectModeSelected} />
              </label>
              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={onDownloadCurrentTimingBackup}>Скачать бэкап тайминга</button>
              {!isStoryVoiceover ? <div className="manualTimingJsonPassGroup">
                <span>{workflowLabels.panelTitle}</span>
                <button className="clipSB_btn clipSB_btnPrimary" onClick={onCopyModePassJson} disabled={mainActionsDisabled}>{workflowLabels.copyPass}</button>
                <button className="clipSB_btn clipSB_btnPrimary" onClick={applyImportedTimingJsonFromMode} disabled={mainActionsDisabled || !jsonImportText.trim()}>{workflowLabels.applyPass}</button>
              </div> : null}
            </div>
            {isStoryVoiceover ? <div className="manualTimingAiStages">
              <div className={`manualTimingAiStageCard ${getManualTimingStageStatusClass(manualTimingStageStatuses.semantic_story_cut)}`}>
                <div className="manualTimingAiStageHeader">
                  <span className="manualTimingAiStageTitle">{MANUAL_TIMING_AI_PASS_BY_TYPE.semantic_story_cut.stage_label_ru}</span>
                  <span className="manualTimingAiStageStatus">{manualTimingStageStatuses.semantic_story_cut}</span>
                </div>
                <div className="manualTimingAiStageActions">
                  <button className="clipSB_btn clipSB_btnPrimary manualTimingAiStageButton" onClick={onCopyModePassJson} disabled={mainActionsDisabled || !semanticStoryCutReady}>{MANUAL_TIMING_AI_PASS_BY_TYPE.semantic_story_cut.copy_label_ru}</button>
                  <button className="clipSB_btn clipSB_btnPrimary manualTimingAiStageButton" onClick={onApplyStoryCutJson} disabled={mainActionsDisabled || !jsonImportText.trim() || !semanticStoryCutReady}>{MANUAL_TIMING_AI_PASS_BY_TYPE.semantic_story_cut.apply_label_ru}</button>
                </div>
              </div>
              <div className={`manualTimingAiStageCard ${getManualTimingStageStatusClass(manualTimingStageStatuses.story_bible)}`}>
                <div className="manualTimingAiStageHeader">
                  <span className="manualTimingAiStageTitle">{MANUAL_TIMING_AI_PASS_BY_TYPE.story_bible.stage_label_ru}</span>
                  <span className="manualTimingAiStageStatus">{manualTimingStageStatuses.story_bible}</span>
                </div>
                <div className="manualTimingAiStageActions">
                  <button className="clipSB_btn clipSB_btnPrimary manualTimingAiStageButton" onClick={onCopyStoryBiblePassJson} disabled={mainActionsDisabled || !storyBiblePassReady} title={storyBibleButtonTitle}>{MANUAL_TIMING_AI_PASS_BY_TYPE.story_bible.copy_label_ru}</button>
                  <button className="clipSB_btn clipSB_btnPrimary manualTimingAiStageButton" onClick={onApplyStoryBibleJson} disabled={mainActionsDisabled || !jsonImportText.trim() || !storyBiblePassReady} title={storyBibleButtonTitle}>{MANUAL_TIMING_AI_PASS_BY_TYPE.story_bible.apply_label_ru}</button>
                </div>
              </div>
              <div className={`manualTimingAiStageCard ${getManualTimingStageStatusClass(manualTimingStageStatuses.block_storyboard)}`}>
                <div className="manualTimingAiStageHeader">
                  <span className="manualTimingAiStageTitle">{MANUAL_TIMING_AI_PASS_BY_TYPE.block_storyboard.stage_label_ru}</span>
                  <span className="manualTimingAiStageStatus">{manualTimingStageStatuses.block_storyboard}</span>
                </div>
                <div className="manualTimingAiStageActions">
                  <button className="clipSB_btn clipSB_btnPrimary manualTimingAiStageButton" onClick={onCopyBlockStoryboardPassJson} disabled={mainActionsDisabled || !blockStoryboardPassReady} title={blockStoryboardButtonTitle}>{MANUAL_TIMING_AI_PASS_BY_TYPE.block_storyboard.copy_label_ru}</button>
                  <button className="clipSB_btn clipSB_btnPrimary manualTimingAiStageButton" onClick={onApplyBlockStoryboardJson} disabled={mainActionsDisabled || !jsonImportText.trim() || !blockStoryboardPassReady} title={blockStoryboardButtonTitle}>{MANUAL_TIMING_AI_PASS_BY_TYPE.block_storyboard.apply_label_ru}</button>
                </div>
              </div>
            </div> : null}
          </div>
          {isJsonImportOpen ? <textarea
            className="manualTimingJsonTextarea"
            value={jsonImportText}
            placeholder={workflowLabels.placeholder}
            onChange={(e) => setJsonImportText(e.target.value)}
          /> : null}
        </details>

          </div>
        </div>

        <div className={`manualTimingWarningsCompact ${warnings.length ? "hasWarnings" : ""}`}>
          <span>{warningsSummary}</span>
          {warnings.length ? <details className="manualTimingWarningsDetails">
            <summary>Показать подробную проверку</summary>
            <div className="manualTimingWarningsList">
              {warnings.map((warning, idx) => <div key={`${warning}-${idx}`}>• {warning}</div>)}
            </div>
          </details> : null}
        </div>

        <div className="manualTimingWorkflowPanel" aria-label={`Основной workflow ${workflowLabels.pass}`}>
          <div className="manualTimingWorkflowActions manualTimingWorkflowActionsPrimary">
            <button className="clipSB_btn clipSB_btnPrimary" onClick={onCreateAudioPhraseMap} disabled={mainActionsDisabled || !audio.url || String(asrStatus || "").startsWith("ASR: распознаю")}>1 · Audio Phrase Map</button>
            <button
              className="clipSB_btn clipSB_btnPrimary"
              onClick={onBuildStoryScenesFromAsr}
              disabled={mainActionsDisabled || !audioPhrases.length}
              title="Пересобрать сцены из текущего JSON/ASR. Может перезаписать ручные разрезы."
            >2 · Собрать сцены</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onConfirmTiming} disabled={mainActionsDisabled || !scenes.length}>3 · Подтвердить</button>
            <button
              className="clipSB_btn clipSB_btnSecondary"
              onClick={hasActiveBoardProject ? onOpenDirectorBoard : onCreateNewDirectorBoardFromTiming}
              disabled={mainActionsDisabled || (!hasActiveBoardProject && !storyPassReadyForDirector) || Boolean(handoffStatus)}
              title={hasActiveBoardProject ? "Открыть сохранённую режиссёрскую доску" : openDirectorBoardTitle}
            >{handoffStatus || (hasActiveBoardProject ? "Открыть доску" : "Создать доску")}</button>
            {hasActiveBoardProject ? <button className="clipSB_btn clipSB_btnSecondary" onClick={onCreateNewDirectorBoardFromTiming} disabled={mainActionsDisabled || !storyPassReadyForDirector || Boolean(handoffStatus)} title="Создать новую доску из текущего тайминга">Создать новую доску</button> : null}
          </div>
          <div className="manualTimingWorkflowStatusLine">
            <span>{storyPassReadyForDirector ? "Статус: можно создать режиссёрскую доску" : `Следующий шаг: применить ${workflowLabels.pass} JSON и подтвердить тайминг`}</span>
          </div>
        </div>

        {asrStatus ? <div className="manualTimingAsrStatus">{asrStatus}</div> : null}
        {audioPhrases.length ? <div className="manualTimingAsrNotice">ASR phrase map: audio_phrases покрывают только речь по word timestamps. Для storyboard используй “Собрать story scenes из ASR”: сцены станут gap-aware, покроют всю audio_duration_sec, а ChatGPT/Gemini затем заполнит перевод, story_blocks и смысловые поля без video_prompt/negative_prompt/sound_prompt.</div> : null}

        {/* Старый расширенный блок убран: основные действия перенесены к плееру и JSON-панели. */}

        <details className="manualTimingSceneDetailsDrawer">
          <summary className="manualTimingSceneDetailsSummary">
            <span>Детали выбранной сцены</span>
            <b>{selectedScene ? `${selectedScene.scene_id} · ${formatTimingSec(selectedSceneStartSec)} → ${formatTimingSec(selectedSceneEndSec)} · ${formatTimingSec(selectedSceneDurationSec)}` : "сцена не выбрана"}</b>
            <em>раскрыть</em>
          </summary>
          <div className="manualTimingSceneDetailsBody">
        {selectedScene ? <div className="manualTimingSceneTextPanel">
          <div className="manualTimingSceneTextHeader">
            <strong>Текст и смысл сцены</strong>
            <span className="manualTimingBlockBadge" style={{ "--story-block-color": selectedSceneText.blockColor }}>Story block: {selectedSceneText.blockTitle}</span>
          </div>
          <div className="manualTimingSceneTextHint">Текст сцены ниже — источник смысла. Тайминг должен заканчиваться на последнем слове этой фразы, без захода на следующую.</div>
          <div className="manualTimingSceneTextGrid">
            <div><span>Position</span><strong>{selectedSceneText.position || "—"}</strong></div>
            <div><span>Original</span><p>{selectedSceneText.original}</p></div>
            <div><span>RU</span><p>{selectedSceneText.ru}</p></div>
            <div><span>Meaning</span><p>{selectedSceneText.meaning}</p></div>
          </div>
          <div className="manualTimingBlockMeaningPanel">
            <div className="manualTimingBlockMeaningTitle">Блок</div>
            <div className="manualTimingBlockMeaningGrid">
              <div><span>Цель блока</span><p>{selectedSceneText.blockGoal}</p></div>
              <div><span>Раскрытие блока</span><p>{selectedSceneText.blockReveal}</p></div>
              <div><span>Эмоция блока</span><p>{selectedSceneText.blockEmotion}</p></div>
            </div>
          </div>
          <div className="manualTimingBlockMeaningPanel">
            <div className="manualTimingBlockMeaningTitle">Сцена внутри блока</div>
            <div className="manualTimingBlockMeaningGrid">
              <div><span>Роль сцены в блоке</span><p>{selectedSceneText.sceneRole}</p></div>
              <div><span>Прогресс блока</span><p>{selectedSceneText.blockProgress}</p></div>
            </div>
          </div>
          {audioPhrases.length ? <details className="manualTimingAudioPhrasesPanel">
            <summary className="manualTimingAudioPhrasesHeader">
              <strong>ASR-фразы выбранной сцены</strong>
              <span>{selectedSceneAudioPhrases.length ? `${selectedSceneAudioPhrases.length} в диапазоне сцены` : "нет фраз в диапазоне"}{selectedMissingPhrase && !selectedSceneAudioPhrases.some((phrase) => String(phrase.phrase_id || "") === String(selectedMissingPhrase.phrase_id || "")) ? " · показана выбранная метка" : ""}</span>
            </summary>
            <div className="manualTimingAudioPhrasesHint">Техническая ASR-карта: основной storyboard после Story Pass читается из текста и смысла сцены выше.</div>
            {selectedScenePhraseWarnings.length ? <div className="manualTimingPhraseWarnings">{selectedScenePhraseWarnings.map((warning, idx) => <div key={`phrase-warning-${idx}`}>⚠ {warning}</div>)}</div> : null}
            {visibleAudioPhrases.length ? <div className="manualTimingAudioPhrasesList">
              {visibleAudioPhrases.map((phrase) => {
                const isNeedsTranscription = String(phrase.status || "") === "needs_transcription";
                const timingSceneId = getTimingSceneIdForAudioPhrase(phrase, scenes);
                const isSelectedPhrase = String(selectedMissingPhraseId || "") === String(phrase.phrase_id || "");
                return <div key={phrase.phrase_id} className={`manualTimingAudioPhraseCard ${isNeedsTranscription ? "needsTranscription" : ""} ${isSelectedPhrase ? "isSelected" : ""}`}>
                  <div className="manualTimingAudioPhraseTop">
                    <strong>{phrase.phrase_id}</strong>
                    <span>{phrase.start_sec.toFixed(2)} – {phrase.end_sec.toFixed(2)} сек</span>
                    <span>{phrase.status || "—"}</span>
                    <span>{phrase.assignment_status || "—"}</span>
                    <span>confidence {Number(phrase.confidence || 0).toFixed(2)}</span>
                  </div>
                  {isNeedsTranscription ? <div className="manualTimingAudioPhraseWarning">⚠ Нужно распознать и перевести</div> : null}
                  <div className="manualTimingAudioPhraseTimingHint">
                    По таймингу сейчас внутри: <b>{timingSceneId || "—"}</b>. Это подсказка, не привязка.
                  </div>
                  <div className="manualTimingAudioPhraseTextGrid">
                    <div><span>original</span><p>{pickManualTimingAudioPhraseOriginalText(phrase) || "—"}</p></div>
                    <div><span>text_ru</span><p>{pickManualTimingAudioPhraseRuText(phrase) || "перевод пока не заполнен"}</p></div>
                    <div><span>note_ru</span><p>{String(phrase.note_ru || "").trim() || "—"}</p></div>
                  </div>
                  <div className="manualTimingAudioPhraseActions">
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => { setSelectedMissingPhraseId(phrase.phrase_id); playRange(phrase.start_sec, phrase.end_sec); }} disabled={!audio.url}>▶ фраза</button>
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => updateAudioPhraseBoundaryFromCursor(phrase.phrase_id, "start_sec")} disabled={!audio.url || !(durationSec > 0)}>Начало = курсор</button>
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => updateAudioPhraseBoundaryFromCursor(phrase.phrase_id, "end_sec")} disabled={!audio.url || !(durationSec > 0)}>Конец = курсор</button>
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => nudgeAudioPhraseBoundary(phrase.phrase_id, "start_sec", -0.05)}>start -0.05</button>
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => nudgeAudioPhraseBoundary(phrase.phrase_id, "start_sec", 0.05)}>start +0.05</button>
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => nudgeAudioPhraseBoundary(phrase.phrase_id, "end_sec", -0.05)}>end -0.05</button>
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => nudgeAudioPhraseBoundary(phrase.phrase_id, "end_sec", 0.05)}>end +0.05</button>
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onDeleteMissingPhrase(phrase.phrase_id)}>Удалить эту фразу</button>
                    <span className="manualTimingAudioPhraseAssignmentHint">Назначение решит ChatGPT после экспорта JSON</span>
                  </div>
                </div>;
              })}
            </div> : <div className="manualTimingAudioPhrasesEmpty">В диапазоне выбранной сцены нет audio_phrases.</div>}
          </details> : null}
        </div> : null}
            {!selectedScene ? <div className="manualTimingSceneDetailsEmpty">Выбери сцену на шкале, чтобы посмотреть текст, смысл, ASR-фразы и технические данные.</div> : null}
        <div className="manualTimingWorkInfo">
          <div className="manualTimingSelectedSceneSummary">
            {selectedScene ? <>
              <b>Выбрано:</b> {selectedScene.scene_id} · {formatTimingSec(selectedSceneStartSec)} → {formatTimingSec(selectedSceneEndSec)} · длина {formatTimingSec(selectedSceneDurationSec)}
            </> : <>
              <b>Выбрано:</b> —
            </>}
          </div>
          <div className="manualTimingStatusGrid">
            <div className="manualTimingStatusItem"><span>Последний разрез</span><strong className="manualTimingStatusValue">{formatTimingSec(lastCutSec)}</strong></div>
            <div className="manualTimingStatusItem"><span>Текущий курсор</span><strong className="manualTimingStatusValue">{formatTimingSec(currentTime)}</strong></div>
            <div className="manualTimingStatusItem"><span>Следующий отрезок</span><strong className="manualTimingStatusValue">{candidateDurationLabel}</strong></div>
            <div className="manualTimingStatusItem"><span>Выбранная сцена</span><strong className="manualTimingStatusValue">{selectedScene?.scene_id || "—"}</strong></div>
            <div className="manualTimingStatusItem"><span>Начало выбранной сцены</span><strong className="manualTimingStatusValue">{selectedScene ? formatTimingSec(selectedSceneStartSec) : "—"}</strong></div>
            <div className="manualTimingStatusItem"><span>Конец выбранной сцены</span><strong className="manualTimingStatusValue">{selectedScene ? formatTimingSec(selectedBoundarySec) : "—"}</strong></div>
            <div className="manualTimingStatusItem isPrimary"><span>Длина выбранной сцены</span><strong className="manualTimingStatusValue">{selectedScene ? formatTimingSec(selectedSceneDurationSec) : "—"}</strong>{selectedSceneDurationWarning ? <span className={`manualTimingDurationBadge ${getDurationWarningClassName(selectedSceneDurationWarning)}`}>⚠ {selectedSceneDurationWarning.label}</span> : null}</div>
            <div className="manualTimingStatusItem"><span>Речь внутри сцены</span><strong className="manualTimingStatusValue">{selectedScene ? `${formatTimingSec(selectedSceneSpeechStartSec)} → ${formatTimingSec(selectedSceneSpeechEndSec)}` : "—"}</strong></div>
            <div className="manualTimingStatusItem"><span>Паузы pre / post</span><strong className="manualTimingStatusValue">{selectedScene ? `${formatTimingSec(selectedScenePreSilenceSec)} / ${formatTimingSec(selectedScenePostSilenceSec)}` : "—"}</strong></div>
            <div className="manualTimingStatusItem"><span>source_phrase_ids</span><strong className="manualTimingStatusValue">{selectedSceneSourcePhraseIds.length ? selectedSceneSourcePhraseIds.join(", ") : "—"}</strong></div>
          </div>
        </div>
          </div>
        </details>

        {copyStatus ? <div className="manualTimingCopyStatus">{copyStatus}</div> : null}
      </section>

      {storyBlockSummaries.length ? <section className="manualTimingStoryBlocks" aria-label="смысловые блоки">
        {storyBlockSummaries.map((block) => <button
          key={block.block_id}
          type="button"
          className="manualTimingStoryBlockChip"
          style={{ "--story-block-color": block.color }}
          onClick={() => onStoryBlockClick(block)}
          title={block.summary_ru || block.title_ru}
        >
          <span>{block.title_ru || block.block_id}</span>
          <b>{block.sceneCount} сцен{block.sceneCount === 1 ? "а" : ""}</b>
        </button>)}
      </section> : null}

      {/* Нижняя дублирующая шкала удалена: основной плеер сверху остаётся единственной рабочей шкалой. */}
      <section className="manualTimingRows">
        {scenes.map((scene, idx) => {
          const isSelected = selectedScene?.scene_id === scene.scene_id;
          const canMerge = idx < scenes.length - 1;
          const durationWarning = getManualTimingSceneDurationWarning(scene);
          const rowStory = getSceneStoryText(scene);
          return <div key={scene.scene_id} className={`manualTimingRow ${isSelected ? "isSelected" : ""}`} style={{ "--story-block-color": rowStory.blockColor }} onClick={() => selectSceneAndSeekStart(scene, { pause: true })}>
            <div className="manualTimingRowMain">
              <strong>{scene.scene_id}</strong>
              <span className="manualTimingBlockBadge">{rowStory.blockTitle}</span>
              <span>{formatTimingSec(scene.start_sec)} – {formatTimingSec(scene.end_sec)}</span>
              <span>длина: {formatTimingSec(scene.duration_sec)}</span>
              {durationWarning ? <span className={`manualTimingDurationBadge ${getDurationWarningClassName(durationWarning)}`}>⚠ {durationWarning.label}</span> : null}
            </div>
            <div className="manualTimingRowControls" onClick={(e) => e.stopPropagation()}>
              <label>Секция<select value={scene.section || "verse"} onChange={(e) => updateScene(scene.scene_id, { section: e.target.value })}>{MANUAL_TIMING_SECTIONS.map((item) => <option key={item} value={item}>{SECTION_LABELS[item] || item}</option>)}</select></label>
              <label>Маршрут<select value={scene.route || "i2v"} onChange={(e) => updateScene(scene.scene_id, { route: e.target.value })}>{routeOptions.map((item) => <option key={item} value={item}>{ROUTE_LABELS[item] || item}</option>)}</select></label>
              <label>Энергия<select value={scene.energy || "mid"} onChange={(e) => updateScene(scene.scene_id, { energy: e.target.value })}>{MANUAL_TIMING_ENERGY.map((item) => <option key={item} value={item}>{ENERGY_LABELS[item] || item}</option>)}</select></label>
              {isStoryVoiceover ? <span className="manualTimingStoryRouteHint">ASR-речь, contains_vocal=false</span> : <label className="manualTimingCheck"><input type="checkbox" checked={Boolean(scene.contains_vocal)} onChange={(e) => updateScene(scene.scene_id, { contains_vocal: e.target.checked })} /> вокал</label>}
              <label className="manualTimingCheck"><input type="checkbox" checked={Boolean(scene.use_sound_suggestion)} onChange={(e) => updateScene(scene.scene_id, { use_sound_suggestion: e.target.checked })} /> звук потом</label>
            </div>
            <textarea
              className="manualTimingNote"
              onClick={(e) => e.stopPropagation()}
              value={String(scene.user_note_ru || "")}
              placeholder="Заметка к отрезку: звук, фраза, визуал, что не забыть..."
              onChange={(e) => updateScene(scene.scene_id, { user_note_ru: e.target.value })}
            />
            <div className="manualTimingRowActions" onClick={(e) => e.stopPropagation()}>
              <button className="clipSB_btn" onClick={(e) => { e.stopPropagation(); playSegment(scene); }}>▶ проиграть сцену</button>
              <button className="clipSB_btn clipSB_btnSecondary" onClick={(e) => { e.stopPropagation(); splitSegmentAtCurrentTime(scene); }}>разрез здесь</button>
              <button className="clipSB_btn clipSB_btnSecondary" disabled={!canMerge} onClick={(e) => { e.stopPropagation(); mergeWithNext(scene); }}>склеить со след.</button>
              <button className="clipSB_btn clipSB_btnDanger" disabled={!canMerge} onClick={(e) => { e.stopPropagation(); deleteCutAfterScene(scene); }}>удалить границу</button>
            </div>
          </div>;
        })}
      </section>

      <p className="manualTimingPage_hint">Размечай песню по слуху: вступление, куплет, припев, проигрыш. Пиши заметки к отрезкам — они потом отобразятся в “Подсказка сцены” в режиссёрской доске.</p>
    </div>
    {newBoardConfirm ? <div className="manualTimingNewBoardOverlay" role="presentation" onClick={() => resolveNewBoardReplaceConfirmation("cancel")}>
      <div className="manualTimingNewBoardDialog" role="dialog" aria-modal="true" aria-labelledby="manualTimingNewBoardTitle" onClick={(event) => event.stopPropagation()}>
        <h3 id="manualTimingNewBoardTitle">В доске уже есть старый проект</h3>
        <p>Сохранить backup перед созданием нового проекта?</p>
        <div className="manualTimingNewBoardMeta">
          <span>Старый: {newBoardConfirm.oldIdentity?.projectId || newBoardConfirm.oldIdentity?.inputSignature || "—"}</span>
          <span>Новый: {newBoardConfirm.newIdentity?.projectId || newBoardConfirm.newIdentity?.inputSignature || "—"}</span>
        </div>
        <div className="manualTimingNewBoardActions">
          <button className="clipSB_btn clipSB_btnPrimary" type="button" onClick={() => resolveNewBoardReplaceConfirmation("backup")}>Скачать backup и создать новый</button>
          <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => resolveNewBoardReplaceConfirmation("create")}>Создать новый без backup</button>
          <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => resolveNewBoardReplaceConfirmation("cancel")}>Отмена</button>
        </div>
      </div>
    </div> : null}
    {quickEditSceneId && quickEditDraft ? <div className="manualTimingQuickEditOverlay" onClick={closeQuickEdit} role="presentation">
      <div className="manualTimingQuickEditModal" onClick={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="Быстрая правка сцены">
        <div className="manualTimingQuickEditHeader">
          <div>
            <h3>Быстрая правка сцены</h3>
            <div className="manualTimingQuickEditMeta">
              <strong>{quickEditSceneId}</strong>
              {quickEditScene ? <span>{formatTimingSec(quickEditScene.start_sec)} → {formatTimingSec(quickEditScene.end_sec)} · {formatTimingSec(quickEditScene.duration_sec)}</span> : null}
              {quickEditScene ? (() => {
                const durationWarning = getManualTimingSceneDurationWarning({ ...quickEditScene, ...quickEditDraft });
                return durationWarning ? <div className={`manualTimingQuickEditWarning ${getDurationWarningClassName(durationWarning)}`}>⚠ <b>{durationWarning.label}</b>: {durationWarning.text}</div> : null;
              })() : null}
            </div>
          </div>
          <button className="manualTimingQuickEditClose" type="button" onClick={closeQuickEdit} title="Закрыть">×</button>
        </div>

        <div className="manualTimingQuickEditGrid">
          <label className="manualTimingQuickEditField">Секция
            <select value={quickEditDraft.section || "verse"} onChange={(e) => setQuickEditDraft((prev) => ({ ...(prev || {}), section: e.target.value }))}>
              {MANUAL_TIMING_SECTIONS.map((item) => <option key={item} value={item}>{SECTION_LABELS[item] || item}</option>)}
            </select>
          </label>

          <label className="manualTimingQuickEditField">Маршрут
            <select value={quickEditDraft.route || "i2v"} onChange={(e) => setQuickEditDraft((prev) => ({ ...(prev || {}), route: e.target.value }))}>
              {routeOptions.map((item) => <option key={item} value={item}>{ROUTE_LABELS[item] || item}</option>)}
            </select>
          </label>

          <label className="manualTimingQuickEditField">Энергия
            <select value={quickEditDraft.energy || "mid"} onChange={(e) => setQuickEditDraft((prev) => ({ ...(prev || {}), energy: e.target.value }))}>
              {MANUAL_TIMING_ENERGY.map((item) => <option key={item} value={item}>{ENERGY_LABELS[item] || item}</option>)}
            </select>
          </label>

          <label className="manualTimingQuickEditCheck"><input type="checkbox" checked={Boolean(quickEditDraft.contains_vocal)} onChange={(e) => setQuickEditDraft((prev) => ({ ...(prev || {}), contains_vocal: e.target.checked }))} /> Есть вокал / lip-sync</label>
          <label className="manualTimingQuickEditCheck"><input type="checkbox" checked={Boolean(quickEditDraft.use_sound_suggestion)} onChange={(e) => setQuickEditDraft((prev) => ({ ...(prev || {}), use_sound_suggestion: e.target.checked }))} /> Сгенерировать звук для i2v</label>
        </div>

        <label className="manualTimingQuickEditField">Заметка к сцене
          <textarea
            className="manualTimingQuickEditTextarea"
            value={String(quickEditDraft.user_note_ru || "")}
            placeholder="Например: завязка, важная реплика, смена эмоции, пауза, звук за кадром..."
            onChange={(e) => setQuickEditDraft((prev) => ({ ...(prev || {}), user_note_ru: e.target.value }))}
          />
        </label>

        <div className="manualTimingQuickEditHint">Enter — сохранить, Esc — закрыть. В заметке Enter оставляет перенос строки.</div>
        <div className="manualTimingQuickEditActions">
          <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={closeQuickEdit}>Отмена</button>
          <button className="clipSB_btn" type="button" onClick={applyQuickEdit}>OK</button>
        </div>
      </div>
    </div> : null}
    </>
  );
}
