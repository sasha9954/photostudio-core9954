import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { fetchJson } from "../../services/api.js";
import { buildStoryPrepTemplateText, STORY_PREP_TEMPLATE_META } from "../clip_nodes/manual/manualClipBoardDomain";
import {
  applyManualBlockStoryboardImport,
  applyManualBlockVideoPromptImport,
  buildManualBlockStoryboardContextJson,
  buildManualBlockVideoPromptContextJson,
} from "./manualBlockStoryboardDomain.js";
import {
  buildManualProjectBackupJson,
  computeManualProjectInputSignature,
  forceWriteManualClipBoardProjectForNode,
  getLastManualClipBoardStorageError,
  getManualClipBoardMaterialStats,
  logManualBoardMediaRefs,
  getManualProjectOwnerId,
  hasMeaningfulManualProject,
  persistManualClipBoardProject,
  readActiveManualClipBoardProject,
  readManualClipBoardProjectForNode,
  readLegacyManualClipBoardProject,
  readLegacyManualTimingProject,
  readManualClipBoardOpenState,
  scoreManualClipBoardProject,
  unwrapManualProjectBackupJson,
  writeManualClipBoardOpenState,
} from "../clip_nodes/manualProjectBackup.js";
import "./ManualClipDirectorPage.css";

const ROUTES = ["ia2v", "i2v", "i2v_sound", "i2v_text", "first_last", "first_last_sound"];
const I2V_SOUND_GAIN_DEFAULT_DB = -6;
const I2V_SOUND_GAIN_MIN_DB = -18;
const I2V_SOUND_GAIN_MAX_DB = 10;
const MANUAL_TIMING_STORY_VOICEOVER_MODE = "story_voiceover";
const MANUAL_TIMING_MUSIC_CLIP_MODE = "music_clip";
const MANUAL_TIMING_PODCAST_DIALOGUE_MODE = "podcast_dialogue";



const LTX_CLEAN_BASE_NEGATIVE_PROMPT = "";

function buildLtxCleanPositivePrompt(scene = {}, project = {}) {
  return String(scene.video_prompt || scene.videoPrompt || "").trim();
}

function buildLtxCleanNegativePrompt(scene = {}, project = {}) {
  return String(scene.negative_prompt || scene.negativePrompt || scene.videoNegativePrompt || "").trim();
}

function withLtxCleanFinalPrompts(scene = {}, project = {}) {
  const positive = buildLtxCleanPositivePrompt(scene, project);
  const negative = buildLtxCleanNegativePrompt(scene, project);
  return {
    positive_prompt: positive,
    negative_prompt: negative,
    finalPositivePrompt: positive,
    final_positive_prompt: positive,
    finalNegativePrompt: negative,
    final_negative_prompt: negative,
  };
}

const MANUAL_STALE_PROMPT_FIELD_CLEAR_PATCH = {
  positive_prompt: "",
  positivePrompt: "",
  finalPositivePrompt: "",
  final_positive_prompt: "",
  finalNegativePrompt: "",
  final_negative_prompt: "",
  ambience_hint: "",
  ambienceHint: "",
  ambient_sound_prompt: "",
  ambientSoundPrompt: "",
};

function clearManualStalePromptFields(extra = {}) {
  return {
    ...MANUAL_STALE_PROMPT_FIELD_CLEAR_PATCH,
    ...extra,
  };
}
function normalizeProjectAspectFormat(format) {
  const value = String(format || "").trim();
  return ["9:16", "16:9", "1:1"].includes(value) ? value : "";
}

function resolveLockedProjectFormat(project = {}) {
  return normalizeProjectAspectFormat(project?.format)
    || normalizeProjectAspectFormat(project?.aspect_ratio)
    || "9:16";
}

function resolveProjectAspectFormat(project = {}, scene = {}) {
  return resolveLockedProjectFormat(project)
    || normalizeProjectAspectFormat(scene?.format)
    || normalizeProjectAspectFormat(scene?.aspect_ratio)
    || "9:16";
}

function getAspectRatioNumber(format) {
  const safeFormat = normalizeProjectAspectFormat(format);
  if (safeFormat === "9:16") return 9 / 16;
  if (safeFormat === "16:9") return 16 / 9;
  if (safeFormat === "1:1") return 1;
  return null;
}

function getImageAspectRatioLabel(width, height) {
  const safeWidth = Number(width || 0);
  const safeHeight = Number(height || 0);
  if (!safeWidth || !safeHeight) return "unknown";
  const ratio = safeWidth / safeHeight;
  if (Math.abs(ratio - 9 / 16) < 0.12) return "9:16";
  if (Math.abs(ratio - 16 / 9) < 0.18) return "16:9";
  if (Math.abs(ratio - 1) < 0.12) return "1:1";
  return `${safeWidth}:${safeHeight}`;
}

function isImageAspectMismatch(width, height, expectedFormat) {
  const expected = getAspectRatioNumber(expectedFormat);
  if (!expected || !width || !height) return false;
  const actual = Number(width) / Number(height);
  const tolerance = 0.12;
  return Math.abs(actual - expected) / expected > tolerance;
}

function getStoredImageAspectLabel(scene = {}) {
  const storedLabel = String(scene?.image_aspect_label || "").trim();
  if (storedLabel) return storedLabel;
  if (scene?.image_width && scene?.image_height) return getImageAspectRatioLabel(scene.image_width, scene.image_height);
  return "unknown";
}

function getSceneImageAspectMeta(scene = {}) {
  const width = Number(scene?.image_width || 0);
  const height = Number(scene?.image_height || 0);
  const label = getStoredImageAspectLabel(scene);
  const ratio = Number(scene?.image_aspect_ratio || (width && height ? width / height : 0));
  return { width, height, label, ratio: Number.isFinite(ratio) ? ratio : 0 };
}

function isSceneImageAspectMismatch(scene = {}, expectedFormat) {
  const meta = getSceneImageAspectMeta(scene);
  if (meta.width && meta.height) return isImageAspectMismatch(meta.width, meta.height, expectedFormat);
  const expected = getAspectRatioNumber(expectedFormat);
  if (!expected || !meta.ratio) return false;
  const tolerance = 0.12;
  return Math.abs(meta.ratio - expected) / expected > tolerance;
}

function readImageFileDimensions(file) {
  return new Promise((resolve) => {
    if (!file || typeof Image === "undefined" || typeof URL === "undefined") {
      resolve({ width: 0, height: 0, objectUrl: "" });
      return;
    }
    const objectUrl = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => {
      resolve({
        width: Number(image.naturalWidth || image.width || 0),
        height: Number(image.naturalHeight || image.height || 0),
        objectUrl,
      });
    };
    image.onerror = () => resolve({ width: 0, height: 0, objectUrl });
    image.src = objectUrl;
  });
}

function projectBelongsToSource(project = {}, sourceNodeId = "") {
  const source = String(sourceNodeId || "").trim();
  if (!source) return true;
  return getManualProjectOwnerId(project) === source;
}

function logManualBoardHydratePick(source, project = {}, extra = {}) {
  console.info("[MANUAL BOARD HYDRATE PICK]", {
    source,
    owner: getManualProjectOwnerId(project),
    project_id: project?.project_id || project?.projectId || "",
    input_signature: project?.input_signature || project?.inputSignature || computeManualProjectInputSignature(project),
    stats: getManualClipBoardMaterialStats(project),
    ...extra,
  });
}

function logManualBoardSkipStale(source, project = {}, targetProject = {}, reason = "identity_mismatch") {
  console.warn("[MANUAL BOARD SKIP STALE]", {
    source,
    reason,
    owner: getManualProjectOwnerId(project),
    project_id: project?.project_id || project?.projectId || "",
    input_signature: project?.input_signature || project?.inputSignature || computeManualProjectInputSignature(project),
    targetOwner: getManualProjectOwnerId(targetProject),
    targetProjectId: targetProject?.project_id || targetProject?.projectId || "",
    targetInputSignature: targetProject?.input_signature || targetProject?.inputSignature || computeManualProjectInputSignature(targetProject),
    stats: getManualClipBoardMaterialStats(project),
  });
}

function manualBoardProjectId(project = {}) {
  return String(project?.project_id || project?.projectId || "").trim();
}

function manualBoardInputSignature(project = {}) {
  return String(project?.input_signature || project?.inputSignature || "").trim();
}

function manualBoardMatchesOpenState(project = {}, openState = {}) {
  const openProjectId = String(openState?.project_id || openState?.projectId || "").trim();
  const openInputSignature = String(openState?.input_signature || openState?.inputSignature || "").trim();
  if (!openProjectId && !openInputSignature) return false;
  const projectId = manualBoardProjectId(project);
  const inputSignature = manualBoardInputSignature(project);
  if (openProjectId && projectId !== openProjectId) return false;
  if (openInputSignature && inputSignature !== openInputSignature) return false;
  return true;
}

function buildManualBoardPickLogEntry(candidate = {}) {
  const project = candidate?.project || {};
  const scoreData = scoreManualClipBoardProject(project);
  return {
    source: candidate?.source || "unknown",
    project_id: manualBoardProjectId(project),
    input_signature: manualBoardInputSignature(project),
    revision: scoreData.revision,
    deletionRevision: scoreData.deletionRevision,
    updatedAt: scoreData.updatedAt,
    stats: scoreData.stats,
    selectedSceneId: String(project?.selectedSceneId || "").trim(),
    openStateMatch: Boolean(candidate?.openStateMatch),
  };
}

function pickNewestManualBoardCandidate(candidates = [], openState = {}) {
  const ranked = candidates
    .filter(({ project }) => hasMeaningfulManualProject(project))
    .map((candidate) => ({
      ...candidate,
      openStateMatch: manualBoardMatchesOpenState(candidate.project, openState),
      scoreData: scoreManualClipBoardProject(candidate.project),
    }))
    .sort((a, b) => {
      if (Number(b.openStateMatch) !== Number(a.openStateMatch)) return Number(b.openStateMatch) - Number(a.openStateMatch);
      if (b.scoreData.deletionRevision !== a.scoreData.deletionRevision) return b.scoreData.deletionRevision - a.scoreData.deletionRevision;
      if (b.scoreData.revision !== a.scoreData.revision) return b.scoreData.revision - a.scoreData.revision;
      if (b.scoreData.updatedAt !== a.scoreData.updatedAt) return b.scoreData.updatedAt - a.scoreData.updatedAt;
      if (b.scoreData.stats.materialScore !== a.scoreData.stats.materialScore) return b.scoreData.stats.materialScore - a.scoreData.stats.materialScore;
      if (b.scoreData.stats.materialTotal !== a.scoreData.stats.materialTotal) return b.scoreData.stats.materialTotal - a.scoreData.stats.materialTotal;
      return b.scoreData.score - a.scoreData.score;
    });
  return ranked[0] || null;
}

function readManualActiveProject(sourceNodeId = "", navigationProject = null, options = {}) {
  const safeSourceNodeId = String(sourceNodeId || "").trim();
  if (options?.explicitNewProject && hasMeaningfulManualProject(navigationProject)) {
    logManualBoardMediaRefs("[MANUAL BOARD MEDIA REFS NAVIGATION PROJECT]", navigationProject, { sourceNodeId: safeSourceNodeId });
    console.info("[MANUAL BOARD EMBEDDED PICK]", {
      sourceNodeId: safeSourceNodeId,
      ownerNodeId: getManualProjectOwnerId(navigationProject),
      picked: "navigationProject",
      project_id: manualBoardProjectId(navigationProject),
      input_signature: manualBoardInputSignature(navigationProject),
      audio_signature: String(navigationProject?.audio_signature || navigationProject?.audioSignature || "").trim(),
      audio: {
        url: String(navigationProject?.audio?.url || navigationProject?.audio_url || navigationProject?.audioUrl || "").trim(),
        name: String(navigationProject?.audio?.name || navigationProject?.audio?.filename || navigationProject?.audio_name || "").trim(),
        duration_sec: Number(navigationProject?.audio?.duration_sec || navigationProject?.audio_duration_sec || 0) || 0,
      },
      explicitNewProject: true,
      stats: getManualClipBoardMaterialStats(navigationProject),
    });
    return navigationProject;
  }
  const nodeProject = readManualClipBoardProjectForNode(safeSourceNodeId);
  const activeProject = readActiveManualClipBoardProject();
  const openState = readManualClipBoardOpenState();
  const candidates = [
    { source: "navigation", project: navigationProject },
    { source: "node-scoped", project: nodeProject },
    { source: "active", project: activeProject },
  ];

  if (safeSourceNodeId) {
    const ownerCandidates = candidates.filter(({ source, project: candidateProject }) => {
      if (!hasMeaningfulManualProject(candidateProject)) return false;
      const sourceMatches = projectBelongsToSource(candidateProject, safeSourceNodeId);
      if (!sourceMatches) logManualBoardSkipStale(source, candidateProject, { sourceNodeId: safeSourceNodeId }, "owner_mismatch");
      return sourceMatches;
    });
    const picked = pickNewestManualBoardCandidate(ownerCandidates, openState);
    if (picked?.project) {
      console.info("[MANUAL BOARD EMBEDDED PICK]", {
        sourceNodeId: safeSourceNodeId,
        picked: buildManualBoardPickLogEntry(picked),
        candidates: ownerCandidates.map((candidate) => buildManualBoardPickLogEntry({
          ...candidate,
          openStateMatch: manualBoardMatchesOpenState(candidate.project, openState),
        })),
      });
      logManualBoardHydratePick(picked.source, picked.project, { sourceNodeId: safeSourceNodeId });
      return picked.project;
    }

    console.warn("[MANUAL BOARD HYDRATE] no project for sourceNodeId", {
      sourceNodeId: safeSourceNodeId,
      nodeProjectExists: hasMeaningfulManualProject(nodeProject),
      activeProjectExists: hasMeaningfulManualProject(activeProject),
      activeOwner: getManualProjectOwnerId(activeProject),
      navigationProjectExists: hasMeaningfulManualProject(navigationProject),
      navigationOwner: getManualProjectOwnerId(navigationProject),
    });
    return null;
  }

  const picked = pickNewestManualBoardCandidate(candidates, openState);
  const bestProject = picked?.project || navigationProject || activeProject || nodeProject;
  const source = picked?.source || (bestProject === navigationProject
    ? "navigation"
    : (bestProject === activeProject ? "active" : (bestProject === nodeProject ? "node-scoped" : "unknown")));
  if (bestProject) logManualBoardHydratePick(source, bestProject);
  return bestProject;
}

function persistManualProject(nextProject = {}, options = {}) {
  if (!hasMeaningfulManualProject(nextProject)) return false;
  return persistManualClipBoardProject(nextProject, options);
}

function dispatchManualDirectorBoardUpdate(sourceNodeId = "", project = {}) {
  const safeSourceNodeId = String(sourceNodeId || project?.sourceNodeId || project?.nodeId || "").trim();
  if (!safeSourceNodeId || typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent("manual-director-board:update", {
    detail: { sourceNodeId: safeSourceNodeId, project },
  }));
}
const STATUS_VIDEO_READY = "video_ready";

const FIRST_LAST_ROUTES = new Set(["first_last", "first_last_sound"]);
const GENERATED_SOUND_ROUTES = new Set(["i2v_sound", "first_last_sound"]);

function isFirstLastRoute(route = "") {
  return FIRST_LAST_ROUTES.has(String(route || "").trim());
}

function isGeneratedSoundRoute(route = "") {
  return GENERATED_SOUND_ROUTES.has(String(route || "").trim());
}


function normalizeStoryBlock(block = {}, idx = 0) {
  const id = String(block?.block_id || block?.id || block?.story_block_id || `block_${idx + 1}`).trim();
  const blockGoalRu = String(
    block?.block_goal_ru ||
    block?.blockGoalRu ||
    block?.goal_ru ||
    block?.story_block_goal_ru ||
    ""
  );
  const blockRevealRu = String(
    block?.block_reveal_ru ||
    block?.blockRevealRu ||
    block?.reveal_ru ||
    block?.story_block_reveal_ru ||
    ""
  );
  const blockEmotionRu = String(
    block?.block_emotion_ru ||
    block?.blockEmotionRu ||
    block?.emotion_ru ||
    block?.story_block_emotion_ru ||
    ""
  );

  return {
    ...block,
    block_id: id,
    id: String(block?.id || id),
    title_ru: String(block?.title_ru || block?.title || block?.name || id),
    summary_ru: String(block?.summary_ru || block?.summary || ""),
    color: String(block?.color || block?.story_block_color || "#8aa4ff"),
    block_goal_ru: blockGoalRu,
    block_reveal_ru: blockRevealRu,
    block_emotion_ru: blockEmotionRu,
    goal_ru: blockGoalRu,
    reveal_ru: blockRevealRu,
    emotion_ru: blockEmotionRu,
    scene_ids: Array.isArray(block?.scene_ids || block?.sceneIds)
      ? (block?.scene_ids || block?.sceneIds).map((id) => String(id || "").trim()).filter(Boolean)
      : [],
    start_sec: Number(block?.start_sec ?? block?.startSec ?? 0) || 0,
    end_sec: Number(block?.end_sec ?? block?.endSec ?? 0) || 0,
  };
}

function buildStoryBlockLookup(storyBlocks = []) {
  const lookup = new Map();
  (Array.isArray(storyBlocks) ? storyBlocks : []).forEach((block, idx) => {
    const normalized = normalizeStoryBlock(block, idx);
    if (normalized.block_id) lookup.set(normalized.block_id, normalized);
    if (normalized.id) lookup.set(normalized.id, normalized);
  });
  return lookup;
}

function truncateText(value = "", max = 120) {
  const text = String(value || "").trim();
  if (text.length <= max) return text;
  return `${text.slice(0, Math.max(0, max - 1)).trim()}…`;
}

function isStoryVoiceoverProject(project = {}) {
  return String(project?.project_mode || project?.projectMode || "") === MANUAL_TIMING_STORY_VOICEOVER_MODE;
}

function normalizeSourcePhraseIds(value = []) {
  return (Array.isArray(value) ? value : [])
    .map((id) => String(id || "").trim())
    .filter(Boolean);
}

function getAudioPhrasesForScene(audioPhrases = [], scene = null) {
  if (!scene) return [];
  const sourceIds = normalizeSourcePhraseIds(scene.source_phrase_ids || scene.sourcePhraseIds);
  const phrases = Array.isArray(audioPhrases) ? audioPhrases : [];
  if (sourceIds.length) {
    const byId = new Map(phrases.map((phrase) => [String(phrase?.phrase_id || phrase?.id || ""), phrase]));
    return sourceIds.map((id) => byId.get(id)).filter(Boolean);
  }
  const sceneStart = Number(scene.start_sec || 0);
  const sceneEnd = Number(scene.end_sec || 0);
  return phrases.filter((phrase) => {
    const start = Number(phrase?.start_sec || 0);
    const end = Number(phrase?.end_sec || 0);
    return end > sceneStart + 0.001 && start < sceneEnd - 0.001;
  });
}

function formatDirectorSec(value = 0) {
  const num = Number(value || 0);
  return Number.isFinite(num) ? num.toFixed(2) : "0.00";
}

function isMeaningSceneVisible(scene = {}) {
  return Boolean(
    scene?.story_block_title_ru
    || scene?.story_block_position_ru
    || scene?.translated_text_ru
    || scene?.scene_goal_ru
    || scene?.prompt_hint_ru
    || scene?.photo_prompt_hint_ru
    || scene?.short_note
  );
}

function resolveI2vTextSceneText(scene = {}) {
  return String(
    scene?.lyrics_text
    || scene?.original_text
    || scene?.translated_text_ru
    || scene?.meaning_hint_ru
    || ""
  ).trim();
}

function resolveManualVideoRoutePayload(scene = {}) {
  const route = String(scene.route || "i2v").trim();

  if (route === "ia2v") {
    return {
      resolvedWorkflowKey: "lip_sync_music",
      video_generation_route: "lip_sync_music",
      renderMode: "lip_sync_music",
      lipSync: true,
      requiresAudioSensitiveVideo: true,
      send_audio_to_generator: true,
      audioSliceUrl: String(scene.audio_slice_url || ""),
      keepGeneratedAudio: false,
      generatedAudioPolicy: "mute_generated_video_audio_use_master_track",
    };
  }

  if (route === "i2v_sound") {
    return {
      resolvedWorkflowKey: "ltx23_i2v_sound_clean",
      video_generation_route: "i2v_sound",
      renderMode: "i2v_sound",
      lipSync: false,
      requiresAudioSensitiveVideo: false,
      send_audio_to_generator: false,
      audioSliceUrl: "",
      keepGeneratedAudio: true,
      generatedAudioPolicy: "mix_generated_audio_under_master",
      generatedAudioGainDb: Number(scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
      // No separate sound prompt node exists in the clean Comfy workflow.
      // Sound/action must be written in the main video_prompt; bans go to negative_prompt.
      soundPrompt: "",
      sound_prompt: "",
      negativeAudioPrompt: "",
      negative_audio_prompt: "",
    };
  }

  if (route === "i2v_text") {
    const soundPrompt = String(scene.sound_prompt || "").trim();
    return {
      resolvedWorkflowKey: "ltx23_i2v_sound_clean",
      video_generation_route: "i2v_text",
      renderMode: "i2v_sound",
      lipSync: false,
      requiresAudioSensitiveVideo: false,
      send_audio_to_generator: false,
      audioSliceUrl: "",
      keepGeneratedAudio: true,
      generatedAudioPolicy: "mix_generated_audio_under_master",
      generatedAudioGainDb: Number(scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
      generated_speech_required: true,
      soundPrompt,
      sound_prompt: soundPrompt,
      negativeAudioPrompt: String(scene.negative_audio_prompt || ""),
      negative_audio_prompt: String(scene.negative_audio_prompt || ""),
    };
  }

  if (route === "first_last" || route === "first_last_sound") {
    const withSound = route === "first_last_sound";
    return {
      resolvedWorkflowKey: withSound ? "first_last_sound_clean" : "first_last",
      video_generation_route: route,
      renderMode: route,
      ltxMode: withSound ? "f_l_sound" : "f_l",
      transitionType: "continuous",
      imageStrategy: "first_last",
      requiresTwoFrames: true,
      lipSync: false,
      requiresAudioSensitiveVideo: false,
      send_audio_to_generator: false,
      audioSliceUrl: "",
      startImageUrl: String(scene.start_image_url || scene.image_url || ""),
      endImageUrl: String(scene.end_image_url || ""),
      keepGeneratedAudio: withSound,
      generatedAudioPolicy: withSound ? "mix_generated_audio_under_master" : "silent_video_use_master_track",
      generatedAudioGainDb: Number(scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
      // first_last_sound has a single positive prompt node in Comfy.
      // Keep sound inside video_prompt and bans inside negative_prompt to avoid hidden stale prompt text.
      soundPrompt: "",
      sound_prompt: "",
      negativeAudioPrompt: "",
      negative_audio_prompt: "",
    };
  }

  return {
    resolvedWorkflowKey: "standard_video",
    video_generation_route: "i2v",
    renderMode: "standard_video",
    lipSync: false,
    requiresAudioSensitiveVideo: false,
    send_audio_to_generator: false,
    audioSliceUrl: "",
    keepGeneratedAudio: false,
    generatedAudioPolicy: "silent_video_use_master_track",
  };
}

async function startManualSceneVideo(payload) {
  return fetchJson("/api/clip/video/start", { method: "POST", timeoutMs: 60000, body: payload });
}

async function getManualSceneVideoStatus(jobId) {
  return fetchJson(`/api/clip/video/status/${encodeURIComponent(jobId)}`, { method: "GET", timeoutMs: 60000 });
}

async function extractManualVideoLastFrame(payload) {
  return fetchJson("/api/clip/video/extract-last-frame", {
    method: "POST",
    timeoutMs: 120000,
    body: payload,
  });
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("file_read_failed"));
    reader.readAsDataURL(file);
  });
}

async function uploadManualSceneImage(file) {
  const dataUrl = await fileToDataUrl(file);
  const out = await fetchJson("/api/assets/fromDataUrl", {
    method: "POST",
    body: { dataUrl },
  });

  const url = String(out?.url || out?.asset_url || out?.public_url || "").trim();
  if (!url) throw new Error("image_upload_failed");

  return url;
}

function resolveVideoStartJobId(out = {}) {
  return String(out.jobId || out.job_id || out.id || "").trim();
}

function resolveManualStatusVideoUrl(out = {}) {
  return String(
    out?.videoUrl ||
    out?.video_url ||
    out?.url ||
    out?.finalVideoUrl ||
    out?.final_video_url ||
    out?.result?.videoUrl ||
    out?.result?.video_url ||
    ""
  ).trim();
}


const MANUAL_VIDEO_RESULT_FIELDS = [
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
];

function resolveManualSceneFinalVideoUrl(scene = {}) {
  for (const field of MANUAL_VIDEO_RESULT_FIELDS) {
    const value = String(scene?.[field] || "").trim();
    if (value && !value.startsWith("mock://")) return value;
  }
  return "";
}

const MMAUDIO_VIDEO_SOURCE_FIELDS = [
  "video_url",
  "videoUrl",
  "generatedVideoUrl",
  "generated_video_url",
  "finalVideoUrl",
  "final_video_url",
  "resultVideoUrl",
  "result_video_url",
  "videoAssetUrl",
  "video_asset_url",
  "videoPreviewUrl",
  "video_preview_url",
  "mmaudio_video_url",
];

function resolveMMAudioSourceVideoUrl(scene = {}) {
  for (const field of MMAUDIO_VIDEO_SOURCE_FIELDS) {
    const value = String(scene?.[field] || "").trim();
    if (value && !value.startsWith("mock://")) return value;
  }
  return "";
}

function resolveManualStatusVideoHasAudio(out = {}) {
  return Boolean(out?.videoHasAudio ?? out?.hasAudio ?? out?.video_has_audio ?? false);
}

function resolveManualSceneStatus(scene = {}) {
  if (scene.video_url) return STATUS_VIDEO_READY;
  if (isFirstLastRoute(scene.route)) {
    const hasStart = Boolean(scene.start_image_url || scene.image_url);
    const hasEnd = Boolean(scene.end_image_url);
    if (scene.video_prompt && hasStart && hasEnd) return "prompt_ready";
    if (hasStart || hasEnd) return "photo_loaded";
    if (scene.audio_slice_url) return "audio_ready";
    return "draft";
  }
  if (scene.video_prompt && scene.image_url) return "prompt_ready";
  if (scene.image_url) return "photo_loaded";
  if (scene.audio_slice_url) return "audio_ready";
  return "draft";
}

function getSceneStatusLabel(scene = {}) {
  const status = scene?.status;
  if (status === "video_queued") return "очередь";
  if (status === "video_running") return "генерация";
  if (status === "video_error") return "ошибка";
  if (status === STATUS_VIDEO_READY) return "готово";
  if (scene?.audio_extracted && !scene?.video_url) return "аудио";
  if (status === "photo_loaded") return "фото";
  if (status === "prompt_ready") return "промт";
  if (status === "audio_ready") return "аудио";
  return status || "draft";
}

function toBool(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const v = value.trim().toLowerCase();
    if (["true", "1", "yes", "y", "on"].includes(v)) return true;
    if (["false", "0", "no", "n", "off", ""].includes(v)) return false;
  }
  return fallback;
}

function shouldBlockManualOldDataUrlUpload(project = {}) {
  return ["manual_new_project_from_audio_split", "manual_new_project_from_audio_split_open_embedded"].includes(String(project?.lastPersistReason || ""));
}

function stripBlockedManualSceneDataUrls(scene = {}, project = {}, sceneIndex = 0) {
  if (!shouldBlockManualOldDataUrlUpload(project)) return scene || {};
  const nextScene = { ...(scene || {}) };
  [
    "image_url",
    "image_preview_url",
    "start_image_url",
    "start_image_preview_url",
    "end_image_url",
    "end_image_preview_url",
    "generated_image_url",
  ].forEach((key) => {
    const value = String(nextScene[key] || "").trim();
    if (/^(data:image|data:video|blob:)/i.test(value)) {
      console.info("[MANUAL BOARD BLOCK OLD DATAURL UPLOAD]", {
        sceneId: String(nextScene.scene_id || nextScene.id || `scene_${sceneIndex + 1}`),
        key,
        reason: project?.lastPersistReason || "explicit_new_project_hydrate",
      });
      nextScene[key] = "";
    }
  });
  return nextScene;
}

function normalizeScene(scene = {}, idx = 0, storyBlockLookup = null, project = {}) {
  const cleanInputScene = stripBlockedManualSceneDataUrls(scene, project, idx);
  const start = Number(cleanInputScene.start_sec || 0);
  const end = Number(cleanInputScene.end_sec || start);
  const blockId = String(cleanInputScene.story_block_id || "").trim();
  const block = blockId && storyBlockLookup?.get ? storyBlockLookup.get(blockId) : null;
  return {
    scene_id: String(cleanInputScene.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(cleanInputScene.index || idx + 1),
    route: ROUTES.includes(cleanInputScene.route) ? cleanInputScene.route : "i2v",
    start_sec: start,
    end_sec: end,
    speech_start_sec: Number(cleanInputScene.speech_start_sec ?? cleanInputScene.speechStartSec ?? start) || start,
    speech_end_sec: Number(cleanInputScene.speech_end_sec ?? cleanInputScene.speechEndSec ?? end) || end,
    duration_sec: Number((Math.max(0, end - start)).toFixed(3)),
    use_sound_suggestion: toBool(cleanInputScene.use_sound_suggestion),
    contains_vocal_assumption: toBool(cleanInputScene.contains_vocal_assumption),
    contains_instrumental_assumption: toBool(cleanInputScene.contains_instrumental_assumption),
    contains_vocal: toBool(cleanInputScene.contains_vocal, toBool(cleanInputScene.contains_vocal_assumption)),
    contains_instrumental: toBool(cleanInputScene.contains_instrumental, toBool(cleanInputScene.contains_instrumental_assumption)),
    story_time: String(cleanInputScene.story_time || ""),
    scene_type: String(cleanInputScene.scene_type || ""),
    drama_hint: String(cleanInputScene.drama_hint || ""),
    short_note: String(cleanInputScene.short_note || ""),
    scene_goal_ru: String(cleanInputScene.scene_goal_ru || ""),
    photo_prompt_hint_ru: String(cleanInputScene.photo_prompt_hint_ru || ""),
    prompt_hint_ru: String(cleanInputScene.prompt_hint_ru || cleanInputScene.photo_prompt_hint_ru || ""),
    user_note_ru: String(cleanInputScene.user_note_ru || cleanInputScene.user_notes_ru || ""),
    story_position_ru: String(cleanInputScene.story_position_ru || cleanInputScene.story_time || ""),
    story_block_id: blockId,
    story_block_title_ru: String(cleanInputScene.story_block_title_ru || block?.title_ru || ""),
    story_block_color: String(cleanInputScene.story_block_color || block?.color || ""),
    story_block_position_ru: String(cleanInputScene.story_block_position_ru || ""),
    story_block_goal_ru: String(cleanInputScene.story_block_goal_ru || block?.block_goal_ru || block?.goal_ru || ""),
    story_block_reveal_ru: String(cleanInputScene.story_block_reveal_ru || block?.block_reveal_ru || block?.reveal_ru || ""),
    story_block_emotion_ru: String(cleanInputScene.story_block_emotion_ru || block?.block_emotion_ru || block?.emotion_ru || ""),
    original_text: String(cleanInputScene.original_text || ""),
    translated_text_ru: String(cleanInputScene.translated_text_ru || ""),
    meaning_hint_ru: String(cleanInputScene.meaning_hint_ru || ""),
    source_text_en: String(cleanInputScene.source_text_en || ""),
    adapted_text_en: String(cleanInputScene.adapted_text_en || ""),
    scene_role_in_block_ru: String(cleanInputScene.scene_role_in_block_ru || ""),
    block_progress_ru: String(cleanInputScene.block_progress_ru || ""),
    scene_global_context_ru: String(cleanInputScene.scene_global_context_ru || ""),
    continuity_anchor_ru: String(cleanInputScene.continuity_anchor_ru || ""),
    must_match_project_identity_ru: String(cleanInputScene.must_match_project_identity_ru || ""),
    must_match_block_style_ru: String(cleanInputScene.must_match_block_style_ru || ""),
    storyboard_frame_role_ru: String(cleanInputScene.storyboard_frame_role_ru || ""),
    source_image_prompt_en: String(cleanInputScene.source_image_prompt_en || ""),
    source_image_prompt_ru: String(cleanInputScene.source_image_prompt_ru || ""),
    source_image_negative_prompt_en: String(cleanInputScene.source_image_negative_prompt_en || ""),
    i2v_prompt_en: String(cleanInputScene.i2v_prompt_en || ""),
    i2v_negative_prompt_en: String(cleanInputScene.i2v_negative_prompt_en || ""),
    composition_ru: String(cleanInputScene.composition_ru || ""),
    camera_angle_ru: String(cleanInputScene.camera_angle_ru || ""),
    subject_lock_ru: String(cleanInputScene.subject_lock_ru || ""),
    background_lock_ru: String(cleanInputScene.background_lock_ru || ""),
    continuity_from_previous_scene_ru: String(cleanInputScene.continuity_from_previous_scene_ru || ""),
    must_keep_same_ru: String(cleanInputScene.must_keep_same_ru || ""),
    allowed_variation_ru: String(cleanInputScene.allowed_variation_ru || ""),
    source_phrase_ids: normalizeSourcePhraseIds(cleanInputScene.source_phrase_ids || cleanInputScene.sourcePhraseIds),
    video_prompt: String(cleanInputScene.video_prompt || ""),
    positive_prompt: String(cleanInputScene.positive_prompt || ""),
    negative_prompt: String(cleanInputScene.negative_prompt || ""),
    sound_prompt: String(cleanInputScene.sound_prompt || ""),
    negative_audio_prompt: String(cleanInputScene.negative_audio_prompt || ""),
    audio_mode: String(cleanInputScene.audio_mode || ""),
    voice_mode: String(cleanInputScene.voice_mode || ""),
    voice_preset_id: String(cleanInputScene.voice_preset_id || ""),
    voice_language: String(cleanInputScene.voice_language || ""),
    voice_role: String(cleanInputScene.voice_role || ""),
    voice_gender: String(cleanInputScene.voice_gender || ""),
    speech_text: String(cleanInputScene.speech_text || ""),
    voice_profile: String(cleanInputScene.voice_profile || ""),
    delivery_style: String(cleanInputScene.delivery_style || ""),
    ambient_sound_prompt: String(cleanInputScene.ambient_sound_prompt || ""),
    sound_mix_note_ru: String(cleanInputScene.sound_mix_note_ru || ""),
    song_block_id: String(cleanInputScene.song_block_id || ""),
    song_block_type: String(cleanInputScene.song_block_type || ""),
    song_block_title_ru: String(cleanInputScene.song_block_title_ru || ""),
    lyrics_text: String(cleanInputScene.lyrics_text || ""),
    lip_sync_required: Boolean(cleanInputScene.lip_sync_required),
    vocal_owner_role: String(cleanInputScene.vocal_owner_role || ""),
    visual_role_ru: String(cleanInputScene.visual_role_ru || ""),
    performance_role_ru: String(cleanInputScene.performance_role_ru || ""),
    speaker_id: String(cleanInputScene.speaker_id || ""),
    speaker_name: String(cleanInputScene.speaker_name || ""),
    topic_block_id: String(cleanInputScene.topic_block_id || ""),
    topic_block_title_ru: String(cleanInputScene.topic_block_title_ru || ""),
    narrator_text_en: String(cleanInputScene.narrator_text_en || ""),
    narrator_text_ru: String(cleanInputScene.narrator_text_ru || ""),
    speaker_text_en: String(cleanInputScene.speaker_text_en || ""),
    speaker_text_ru: String(cleanInputScene.speaker_text_ru || ""),
    generated_speech_required: Boolean(cleanInputScene.generated_speech_required),
    voice_profile_id: String(cleanInputScene.voice_profile_id || ""),
    narrator_voice_profile_en: String(cleanInputScene.narrator_voice_profile_en || ""),
    negative_voice_traits: String(cleanInputScene.negative_voice_traits || ""),
    broll_hint_ru: String(cleanInputScene.broll_hint_ru || ""),
    format: normalizeProjectAspectFormat(cleanInputScene.format || cleanInputScene.aspect_ratio),
    aspect_ratio: normalizeProjectAspectFormat(cleanInputScene.aspect_ratio || cleanInputScene.format),
    image_width: Number(cleanInputScene.image_width || 0),
    image_height: Number(cleanInputScene.image_height || 0),
    image_aspect_ratio: Number(cleanInputScene.image_aspect_ratio || 0),
    image_aspect_label: String(cleanInputScene.image_aspect_label || ""),
    image_url: String(cleanInputScene.image_url || cleanInputScene.start_image_url || ""),
    start_image_url: String(cleanInputScene.start_image_url || cleanInputScene.image_url || ""),
    end_image_url: String(cleanInputScene.end_image_url || ""),
    image_preview_url: String(cleanInputScene.image_preview_url || cleanInputScene.start_image_preview_url || ""),
    start_image_preview_url: String(cleanInputScene.start_image_preview_url || cleanInputScene.image_preview_url || ""),
    end_image_preview_url: String(cleanInputScene.end_image_preview_url || ""),
    image_upload_status: String(cleanInputScene.image_upload_status || ""),
    image_upload_error: String(cleanInputScene.image_upload_error || ""),
    video_url: String(cleanInputScene.video_url || cleanInputScene.videoUrl || ""),
    audio_slice_url: String(cleanInputScene.audio_slice_url || ""),
    audio_slice_duration_sec: Number(cleanInputScene.audio_slice_duration_sec || 0),
    status: String(cleanInputScene.status || "draft"),
    error: String(cleanInputScene.error || ""),
    audio_extracted: Boolean(cleanInputScene.audio_extracted),
    video_job_id: String(cleanInputScene.video_job_id || ""),
    video_error: String(cleanInputScene.video_error || ""),
    video_has_audio: Boolean(cleanInputScene.video_has_audio),
    generated_audio_policy: String(cleanInputScene.generated_audio_policy || ""),
    generated_audio_gain_db: Number(cleanInputScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
    keep_generated_audio: Boolean(cleanInputScene.keep_generated_audio),
    video_request_payload_preview: cleanInputScene.video_request_payload_preview || null,
  };
}

const IMPORT_EMPTY_PROTECTED_SCENE_FIELDS = [
  "image_url",
  "image_preview_url",
  "start_image_url",
  "end_image_url",
  "start_image_preview_url",
  "end_image_preview_url",
  "video_url",
  "videoUrl",
  "video_job_id",
  "video_has_audio",
  "status",
  "audio_slice_url",
  "audio_slice_duration_sec",
  "audio_extracted",
  "video_error",
  "video_request_payload_preview",
  "generated_audio_policy",
  "generated_audio_gain_db",
  "keep_generated_audio",
  "video_prompt",
  "positive_prompt",
  "negative_prompt",
  "sound_prompt",
  "negative_audio_prompt",
  "speech_text",
  "voice_profile",
  "delivery_style",
  "ambient_sound_prompt",
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

function isEmptyImportedMaterialValue(value) {
  if (value === false || value === null || value === undefined) return true;
  return String(value).trim() === "";
}

function mergeImportedScenesPreservingMaterials(currentScenes = [], importedScenes = []) {
  const currentById = new Map((Array.isArray(currentScenes) ? currentScenes : []).map((scene) => [String(scene?.scene_id || scene?.id || "").trim(), scene]));
  return (Array.isArray(importedScenes) ? importedScenes : []).map((incomingScene) => {
    const sceneId = String(incomingScene?.scene_id || incomingScene?.id || "").trim();
    const currentScene = currentById.get(sceneId);
    if (!currentScene) return incomingScene;
    const nextScene = { ...(currentScene || {}), ...(incomingScene || {}) };
    IMPORT_EMPTY_PROTECTED_SCENE_FIELDS.forEach((field) => {
      const incomingHasField = Object.prototype.hasOwnProperty.call(incomingScene || {}, field);
      if (!incomingHasField) return;
      const incomingIsEmpty = isEmptyImportedMaterialValue(incomingScene?.[field]);
      const currentHasValue = currentScene?.[field] === true || String(currentScene?.[field] ?? "").trim() !== "";
      if (incomingIsEmpty && currentHasValue) nextScene[field] = currentScene[field];
    });

    const currentStatus = String(currentScene?.status || "").trim();
    const incomingStatus = String(incomingScene?.status || "").trim();
    const protectedCurrentStatuses = new Set(["video_ready", "video_running", "video_queued"]);
    const downgradeIncomingStatuses = new Set(["", "draft", "prompt_ready"]);
    if (protectedCurrentStatuses.has(currentStatus) && downgradeIncomingStatuses.has(incomingStatus)) {
      nextScene.status = currentScene.status;
    }

    if (currentScene?.video_has_audio === true && incomingScene?.video_has_audio !== true) {
      nextScene.video_has_audio = true;
    }

    return nextScene;
  });
}

function buildStoryPositionFallback(scene = {}, idx = 0, total = 0) {
  if (idx === 0) return "начало";
  if (idx === total - 1) return "финал";
  if (scene?.route === "ia2v") return "настоящее / lip-sync";
  return "развитие / визуальная сцена";
}

export default function ManualClipDirectorBoardEditor({
  project: embeddedProject = null,
  sourceNodeId = "",
  onProjectChange,
  onClose,
  onMMAudioGenerate,
  onMMAudioGainRemix,
  embedded = false,
  manualBoardExplicitNewProject = false,
} = {}) {
  const navigate = useNavigate();
  const location = useLocation();
  const sourceNodeIdFromRoute = useMemo(() => (
    embedded
      ? String(sourceNodeId || "")
      : String(location.state?.sourceNodeId || location.state?.ownerNodeId || new URLSearchParams(location.search).get("sourceNodeId") || "")
  ), [embedded, sourceNodeId, location.search, location.state]);
  const videoStartInFlightRef = useRef(new Set());
  const videoPollErrorCountRef = useRef(new Map());
  const resumedVideoJobsRef = useRef(new Set());
  const terminalVideoJobsRef = useRef(new Set());
  const quickListenAudioRef = useRef(null);
  const quickListenRafRef = useRef(null);
  const playbackRangeRef = useRef({ startSec: 0, endSec: null });
  const didHydrateRef = useRef(false);
  const didWarnMissingSourceNodeIdRef = useRef(false);
  const projectRef = useRef(null);
  const selectedSceneIdRef = useRef("");
  const aspectWarningResolverRef = useRef(null);
  const [project, setProject] = useState(null);
  const [selectedSceneId, setSelectedSceneId] = useState("");
  const [isUserNoteEditorOpen, setIsUserNoteEditorOpen] = useState(false);
  const [storyPrepTemplateText, setStoryPrepTemplateText] = useState("");
  const [isStoryPrepExpanded, setIsStoryPrepExpanded] = useState(false);
  const [backupStatus, setBackupStatus] = useState("");
  const [blockCopyStatus, setBlockCopyStatus] = useState("");
  const [playbackMode, setPlaybackMode] = useState("");
  const [isAudioPlaying, setIsAudioPlaying] = useState(false);
  const [playbackRange, setPlaybackRange] = useState({ startSec: 0, endSec: null });
  const [uploadAspectWarning, setUploadAspectWarning] = useState(null);
  const [mmaudioModal, setMMAudioModal] = useState(null);
  const [mmaudioPrompt, setMMAudioPrompt] = useState("");
  const [mmaudioNegativePrompt, setMMAudioNegativePrompt] = useState("");
  const [mmaudioGainDraftDb, setMMAudioGainDraftDb] = useState(-6);
  const [mmaudioModalError, setMMAudioModalError] = useState("");

  const getProjectOwnerNodeId = (candidateProject = {}) => String(
    sourceNodeIdFromRoute
    || candidateProject?.sourceNodeId
    || candidateProject?.nodeId
    || ""
  ).trim();

  const warnMissingSourceNodeId = () => {
    if (didWarnMissingSourceNodeIdRef.current) return;
    didWarnMissingSourceNodeIdRef.current = true;
    console.warn("[manual director] missing sourceNodeId, node-bound board sync disabled");
  };

  const logStorageVerify = (ownerNodeId = getProjectOwnerNodeId(projectRef.current)) => {
    const safeSourceNodeId = String(ownerNodeId || "").trim();
    if (!safeSourceNodeId) return;
    const nodeProject = readManualClipBoardProjectForNode(safeSourceNodeId);
    const activeProject = readActiveManualClipBoardProject();
    console.info("[MANUAL BOARD STORAGE VERIFY]", {
      sourceNodeId: safeSourceNodeId,
      nodeProjectExists: hasMeaningfulManualProject(nodeProject),
      nodeProjectOwner: getManualProjectOwnerId(nodeProject),
      activeProjectOwner: getManualProjectOwnerId(activeProject),
      nodeProjectStats: getManualClipBoardMaterialStats(nodeProject),
      activeProjectStats: getManualClipBoardMaterialStats(activeProject),
      route: typeof window !== "undefined" ? window.location.href : "",
    });
  };

  const normalizeDirectorProjectOwner = (candidateProject = {}) => {
    const ownerNodeId = getProjectOwnerNodeId(candidateProject);
    if (!ownerNodeId) return candidateProject || {};
    const projectFormat = resolveLockedProjectFormat(candidateProject);
    const inputSignature = String(candidateProject?.input_signature || candidateProject?.inputSignature || computeManualProjectInputSignature(candidateProject)).trim();
    const projectId = String(candidateProject?.project_id || candidateProject?.projectId || (inputSignature ? `manual_${ownerNodeId}_${inputSignature}` : `manual_${ownerNodeId}`)).trim();
    return {
      ...(candidateProject || {}),
      format: projectFormat,
      aspect_ratio: projectFormat,
      format_locked: true,
      scenes: Array.isArray(candidateProject?.scenes)
        ? candidateProject.scenes.map((scene) => ({ ...scene, format: projectFormat, aspect_ratio: projectFormat }))
        : [],
      source: candidateProject?.source || "manual_timing_node",
      ownerNodeType: candidateProject?.ownerNodeType || "manualTiming",
      nodeId: ownerNodeId,
      sourceNodeId: ownerNodeId,
      ownerNodeId,
      project_id: projectId,
      projectId,
      input_signature: inputSignature,
      inputSignature,
      audio_signature: String(candidateProject?.audio_signature || candidateProject?.audioSignature || computeManualProjectInputSignature(candidateProject, { audioOnly: true })).trim(),
      story_signature: String(candidateProject?.story_signature || candidateProject?.storySignature || computeManualProjectInputSignature(candidateProject, { storyOnly: true })).trim(),
    };
  };

  const persistAndBroadcastDirectorProject = (candidateProject = {}, options = {}) => {
    const ownerNodeId = getProjectOwnerNodeId(candidateProject);
    if (!ownerNodeId) {
      warnMissingSourceNodeId();
      return false;
    }
    const safeProject = normalizeDirectorProjectOwner(candidateProject);
    const persistReason = options?.reason || safeProject.lastPersistReason || "manual_director_persist";
    const persistOptions = { ...(options || {}), reason: persistReason, embedded };

    if (embedded && typeof onProjectChange === "function") {
      const persisted = persistManualProject(safeProject, persistOptions);
      dispatchManualDirectorBoardUpdate(ownerNodeId, safeProject);
      writeManualClipBoardOpenState({
        isOpen: true,
        sourceNodeId: ownerNodeId,
        selectedSceneId: safeProject.selectedSceneId || selectedSceneIdRef.current || "",
        manualBoardExplicitNewProject: ["manual_new_project_from_audio_split", "manual_new_project_from_audio_split_open_embedded"].includes(String(persistReason || "")),
        project_id: safeProject.project_id || safeProject.projectId || "",
        input_signature: safeProject.input_signature || safeProject.inputSignature || "",
        routePath: "/studio/storyboard",
        updatedAt: Date.now(),
      });
      console.info("[MANUAL BOARD PERSIST WRITE]", {
        reason: persistReason,
        sourceNodeId: ownerNodeId,
        project_id: safeProject.project_id || safeProject.projectId || "",
        input_signature: safeProject.input_signature || safeProject.inputSignature || "",
        revision: safeProject.revision || 0,
        deletionRevision: safeProject.deletionRevision || safeProject.deletion_revision || safeProject.deleted_media_revision || 0,
        updatedAt: safeProject.updatedAt,
        stats: getManualClipBoardMaterialStats(safeProject),
        embedded: true,
        directLocalPersisted: persisted,
      });
      onProjectChange(safeProject, persistReason, persistOptions);
      return true;
    }

    let persisted = persistManualProject(safeProject, persistOptions);
    let readback = ownerNodeId ? readManualClipBoardProjectForNode(ownerNodeId) : null;
    let readbackOk = hasMeaningfulManualProject(readback)
      && getManualProjectOwnerId(readback) === ownerNodeId;
    const stats = getManualClipBoardMaterialStats(safeProject);
    const canForceWriteCurrentBoard = Boolean(
      hasMeaningfulManualProject(safeProject)
      && ownerNodeId
      && Array.isArray(safeProject.scenes)
      && safeProject.scenes.length > 0
    );

    if ((!persisted || !readbackOk) && canForceWriteCurrentBoard) {
      persisted = forceWriteManualClipBoardProjectForNode(safeProject, {
        reason: `${options?.reason || "manual_director_persist"}_force_write_after_failed_verify`,
      });
      readback = ownerNodeId ? readManualClipBoardProjectForNode(ownerNodeId) : null;
      readbackOk = hasMeaningfulManualProject(readback)
        && getManualProjectOwnerId(readback) === ownerNodeId;
    }

    if (!readbackOk) {
      console.warn("[MANUAL BOARD PERSIST VERIFY] readback failed", {
        sourceNodeId: ownerNodeId,
        persisted,
        readbackOk,
        readbackOwner: getManualProjectOwnerId(readback),
        stats,
        readbackStats: getManualClipBoardMaterialStats(readback),
        reason: options?.reason || safeProject.lastPersistReason || "manual_director_persist",
      });
    }

    dispatchManualDirectorBoardUpdate(ownerNodeId, safeProject);
    return Boolean(persisted && readbackOk);
  };

  useEffect(() => {
    const navigationProject = location.state?.director_board || location.state?.project || null;
    const openState = readManualClipBoardOpenState();
    const explicitNewProject = Boolean(
      manualBoardExplicitNewProject
      || location.state?.manualBoardExplicitNewProject === true
      || openState?.manualBoardExplicitNewProject === true
      || ["manual_new_project_from_audio_split", "manual_new_project_from_audio_split_open_embedded"].includes(String((embeddedProject || navigationProject)?.lastPersistReason || ""))
    );
    const parsedProject = embedded
      ? readManualActiveProject(sourceNodeIdFromRoute, embeddedProject, { explicitNewProject })
      : readManualActiveProject(sourceNodeIdFromRoute, navigationProject, { explicitNewProject });
    if (!hasMeaningfulManualProject(parsedProject)) {
      setProject(null);
      setSelectedSceneId("");
      didHydrateRef.current = true;
      return;
    }
    try {
      const parsed = unwrapManualProjectBackupJson(parsedProject);
      const forcedProjectId = String(location.state?.manualBoardForceProjectId || location.state?.forceProjectId || openState?.forceProjectId || "").trim();
      const forcedInputSignature = String(location.state?.manualBoardForceInputSignature || location.state?.forceInputSignature || openState?.forceInputSignature || "").trim();
      const forcedAudioSignature = String(location.state?.manualBoardForceAudioSignature || location.state?.forceAudioSignature || openState?.forceAudioSignature || "").trim();
      const projectFormat = resolveProjectAspectFormat(parsed);
      const storyBlocks = Array.isArray(parsed?.story_blocks) ? parsed.story_blocks.map(normalizeStoryBlock) : [];
      const storyBlockLookup = buildStoryBlockLookup(storyBlocks);
      const scenes = Array.isArray(parsed?.scenes) ? parsed.scenes.map((scene, idx) => {
        const normalizedScene = normalizeScene(scene, idx, storyBlockLookup, parsed);
        return {
          ...normalizedScene,
          format: normalizeProjectAspectFormat(normalizedScene.format) || projectFormat,
          aspect_ratio: normalizeProjectAspectFormat(normalizedScene.aspect_ratio) || normalizeProjectAspectFormat(normalizedScene.format) || projectFormat,
        };
      }) : [];
      const parsedSelectedSceneId = String(parsed?.selectedSceneId || "").trim();
      const selectedSceneIdForHydrate = scenes.some((scene) => scene.scene_id === parsedSelectedSceneId) ? parsedSelectedSceneId : String(scenes[0]?.scene_id || "");
      const hydratedProject = normalizeDirectorProjectOwner({
        ...parsed,
        ...(forcedProjectId ? { project_id: forcedProjectId, projectId: forcedProjectId } : {}),
        ...(forcedInputSignature ? { input_signature: forcedInputSignature, inputSignature: forcedInputSignature } : {}),
        ...(forcedAudioSignature ? { audio_signature: forcedAudioSignature, audioSignature: forcedAudioSignature } : {}),
        format: projectFormat,
        aspect_ratio: normalizeProjectAspectFormat(parsed?.aspect_ratio) || projectFormat,
        story_blocks: storyBlocks,
        scenes,
        selectedSceneId: selectedSceneIdForHydrate,
      });
      projectRef.current = hydratedProject;
      selectedSceneIdRef.current = selectedSceneIdForHydrate;
      setProject(hydratedProject);
      setSelectedSceneId(selectedSceneIdForHydrate);

      if (!embedded && hasMeaningfulManualProject(hydratedProject)) {
        const reason = explicitNewProject && navigationProject ? "hydrate_explicit_new_navigation_project" : (navigationProject ? "hydrate_from_navigation_project" : "hydrate_source_bound_project");
        const projectToPersist = {
          ...hydratedProject,
          selectedSceneId: selectedSceneIdForHydrate,
          updatedAt: Date.now(),
          lastPersistReason: reason,
        };
        projectRef.current = projectToPersist;
        setProject(projectToPersist);
        const ownerNodeId = sourceNodeIdFromRoute || hydratedProject.sourceNodeId || hydratedProject.nodeId;
        let persisted = persistManualProject(projectToPersist, {
          reason,
          forceReplace: Boolean(navigationProject || explicitNewProject),
          explicitReset: Boolean(explicitNewProject),
          allowMaterialLoss: Boolean(explicitNewProject),
        });
        let readback = ownerNodeId ? readManualClipBoardProjectForNode(ownerNodeId) : null;
        let readbackOk = hasMeaningfulManualProject(readback)
          && getManualProjectOwnerId(readback) === ownerNodeId;

        if ((navigationProject || reason === "hydrate_from_navigation_project") && (!persisted || !readbackOk)) {
          persisted = forceWriteManualClipBoardProjectForNode(projectToPersist, {
            reason: "force_write_after_navigation_hydrate_failed_verify",
          });
          readback = ownerNodeId ? readManualClipBoardProjectForNode(ownerNodeId) : null;
          readbackOk = hasMeaningfulManualProject(readback)
            && getManualProjectOwnerId(readback) === ownerNodeId;
        }

        dispatchManualDirectorBoardUpdate(ownerNodeId, projectToPersist);
        console.info("[MANUAL BOARD HYDRATE] persisted navigation project for reload", {
          sourceNodeId: ownerNodeId,
          ownerNodeId,
          projectSource: explicitNewProject && navigationProject ? "navigationProject" : (navigationProject ? "navigation" : "canonical/node-scoped/active"),
          explicitNewProject,
          project_id: projectToPersist.project_id || projectToPersist.projectId || "",
          input_signature: projectToPersist.input_signature || projectToPersist.inputSignature || "",
          audio_signature: projectToPersist.audio_signature || projectToPersist.audioSignature || "",
          audio: {
            url: String(projectToPersist.audio?.url || projectToPersist.audio_url || projectToPersist.audioUrl || "").trim(),
            name: String(projectToPersist.audio?.name || projectToPersist.audio?.filename || projectToPersist.audio_name || "").trim(),
            duration_sec: Number(projectToPersist.audio?.duration_sec || projectToPersist.audio_duration_sec || 0) || 0,
          },
          persisted,
          readbackOk,
          readbackOwner: getManualProjectOwnerId(readback),
          stats: getManualClipBoardMaterialStats(projectToPersist),
          readbackStats: getManualClipBoardMaterialStats(readback),
          selectedSceneId: projectToPersist.selectedSceneId,
        });
        logStorageVerify(ownerNodeId);
      }
    } catch {
      setProject(null);
    } finally {
      didHydrateRef.current = true;
    }
  }, [embedded, embeddedProject, sourceNodeIdFromRoute, location.state]);

  useEffect(() => {
    projectRef.current = project;
  }, [project]);

  useEffect(() => {
    selectedSceneIdRef.current = selectedSceneId;
  }, [selectedSceneId]);

  const persistProject = (nextProject) => {
    const nextFormat = resolveProjectAspectFormat(nextProject || {}, (nextProject || {})?.scenes?.[0] || {});
    const safeProject = normalizeDirectorProjectOwner({
      ...(nextProject || {}),
      format: nextFormat,
      aspect_ratio: nextFormat,
      selectedSceneId: String(nextProject?.selectedSceneId || selectedSceneIdRef.current || ""),
      updatedAt: Date.now(),
    });
    projectRef.current = safeProject;
    selectedSceneIdRef.current = safeProject.selectedSceneId;
    setProject(safeProject);
    if (!didHydrateRef.current || !hasMeaningfulManualProject(safeProject)) return;
    persistAndBroadcastDirectorProject(safeProject, { reason: safeProject.lastPersistReason || "manual_director_persist_project" });
  };

  const onForceSaveDirectorBoard = () => {
    const currentProject = projectRef.current || project;
    if (!hasMeaningfulManualProject(currentProject)) {
      setBackupStatus("Нет проекта для сохранения");
      return;
    }
    const ownerNodeId = getProjectOwnerNodeId(currentProject);
    if (!ownerNodeId) {
      warnMissingSourceNodeId();
      setBackupStatus("Ошибка сохранения доски");
      return;
    }
    const safeProject = normalizeDirectorProjectOwner({
      ...currentProject,
      selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || "",
      updatedAt: Date.now(),
      lastPersistReason: "manual_force_save_button",
    });
    projectRef.current = safeProject;
    setProject(safeProject);
    const wrote = forceWriteManualClipBoardProjectForNode(safeProject, { reason: "manual_force_save_button" });
    const storageError = wrote ? null : getLastManualClipBoardStorageError();
    if (!wrote) {
      console.error("[MANUAL BOARD SAVE BUTTON] force write failed", {
        sourceNodeId: ownerNodeId,
        storageError,
      });
    }
    const readback = readManualClipBoardProjectForNode(ownerNodeId);
    const readbackOk = Boolean(
      wrote
      && hasMeaningfulManualProject(readback)
      && getManualProjectOwnerId(readback) === ownerNodeId
    );
    dispatchManualDirectorBoardUpdate(ownerNodeId, safeProject);
    logStorageVerify(ownerNodeId);
    setBackupStatus(readbackOk
      ? "Доска сохранена"
      : (storageError?.reason === "quota_exceeded"
        ? "Ошибка сохранения: localStorage переполнен"
        : "Ошибка сохранения: смотри console write failed"));
    window.setTimeout(() => setBackupStatus(""), 1800);
  };

  const safePersistCurrentProject = (reason = "manual_director_safe_persist") => {
    const currentProject = projectRef.current || project;
    if (!hasMeaningfulManualProject(currentProject)) return false;

    const safeProject = {
      ...currentProject,
      selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || currentProject.scenes?.[0]?.scene_id || "",
      updatedAt: Date.now(),
      lastPersistReason: reason,
    };

    projectRef.current = safeProject;
    selectedSceneIdRef.current = safeProject.selectedSceneId;
    persistAndBroadcastDirectorProject(safeProject, { reason });
    setProject(safeProject);
    return true;
  };

  useEffect(() => {
    const persistBeforeLeave = () => {
      const currentProject = projectRef.current;
      if (!hasMeaningfulManualProject(currentProject)) return;
      persistAndBroadcastDirectorProject({
        ...currentProject,
        selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || "",
        updatedAt: Date.now(),
        lastPersistReason: "pagehide_or_unmount",
      }, { reason: "pagehide_or_unmount" });
    };

    window.addEventListener("pagehide", persistBeforeLeave);
    window.addEventListener("beforeunload", persistBeforeLeave);

    return () => {
      persistBeforeLeave();
      window.removeEventListener("pagehide", persistBeforeLeave);
      window.removeEventListener("beforeunload", persistBeforeLeave);
    };
  }, []);

  const storyBlocks = Array.isArray(project?.story_blocks) ? project.story_blocks : [];
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const audioPhrases = Array.isArray(project?.audio_phrases) ? project.audio_phrases : [];
  const audio = project?.audio && typeof project.audio === "object" ? project.audio : {};
  const audioUrl = String(audio?.url || project?.audio_url || project?.audioUrl || "").trim();
  const isStoryVoiceover = isStoryVoiceoverProject(project);
  const storyPrepProject = useMemo(() => ({
    ...(project || {}),
    prep_template_meta: STORY_PREP_TEMPLATE_META,
    story_blocks: storyBlocks,
    scenes,
  }), [project, storyBlocks, scenes]);

  useEffect(() => {
    if (!project || !Array.isArray(scenes) || !scenes.length) return;

    scenes.forEach((scene) => {
      const jobId = String(scene?.video_job_id || scene?.videoJobId || "").trim();
      const sceneId = String(scene?.scene_id || "").trim();
      const status = String(scene?.status || "").trim();

      if (!sceneId || !jobId) return;
      const key = `${sceneId}:${jobId}`;
      if (resolveManualSceneFinalVideoUrl(scene)) return;
      if (!["video_running", "video_queued"].includes(status)) return;
      if (terminalVideoJobsRef.current.has(key)) return;
      if (resumedVideoJobsRef.current.has(key)) return;

      resumedVideoJobsRef.current.add(key);
      pollManualSceneVideo(sceneId, jobId, 0);
    });
  }, [project?.updatedAt, scenes]);

  useEffect(() => {
    if (!project) return;
    setStoryPrepTemplateText(buildStoryPrepTemplateText(storyPrepProject));
  }, [project, storyPrepProject]);

  const refreshStoryPrepTemplate = () => {
    setStoryPrepTemplateText(buildStoryPrepTemplateText(storyPrepProject));
  };

  const onCopyStoryPrepTemplate = async () => {
    const text = storyPrepTemplateText || buildStoryPrepTemplateText(storyPrepProject);
    await navigator.clipboard?.writeText(text);
  };

  const onDownloadStoryPrepTemplate = () => {
    const text = storyPrepTemplateText || buildStoryPrepTemplateText(storyPrepProject);
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "story_prep_template.txt";
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };

  const downloadJsonPayload = (payload, filename = "manual_project_backup.json") => {
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
      setBackupStatus("Backup проекта скачан");
      window.setTimeout(() => setBackupStatus(""), 1800);
    } catch {
      setBackupStatus("Не удалось скачать backup проекта");
    }
  };

  const onDownloadProjectBackup = () => {
    downloadJsonPayload(buildManualProjectBackupJson({ ...(project || {}), selectedSceneId }, { source: "manual_director_board" }));
  };

  const restoreManualProjectObject = (rawProject, successPrefix = "Backup восстановлен") => {
    const parsed = unwrapManualProjectBackupJson(rawProject);
    const storyBlocks = Array.isArray(parsed?.story_blocks) ? parsed.story_blocks.map(normalizeStoryBlock) : [];
    const storyBlockLookup = buildStoryBlockLookup(storyBlocks);
    const importedScenes = Array.isArray(parsed?.scenes) ? parsed.scenes : [];
    const mergedScenes = mergeImportedScenesPreservingMaterials(projectRef.current?.scenes || project?.scenes || [], importedScenes);
    const scenes = mergedScenes.map((scene, idx) => normalizeScene(scene, idx, storyBlockLookup));
    const nextProject = {
      ...(projectRef.current || project || {}),
      ...parsed,
      story_blocks: storyBlocks,
      scenes,
      selectedSceneId: String(parsed?.selectedSceneId || scenes[0]?.scene_id || ""),
      updatedAt: Date.now(),
    };
    persistProject(nextProject);
    console.debug("[manual director import video/photo]", {
      stats: getManualClipBoardMaterialStats(nextProject),
      selectedSceneId: nextProject.selectedSceneId,
    });
    setSelectedSceneId(nextProject.selectedSceneId);
    setBackupStatus(`${successPrefix}: сцен ${scenes.length}`);
    window.setTimeout(() => setBackupStatus(""), 2200);
  };

  const onRestoreLegacyManualProject = () => {
    const legacyProject = readLegacyManualClipBoardProject() || readLegacyManualTimingProject();
    if (!hasMeaningfulManualProject(legacyProject)) {
      setBackupStatus("Старый проект не найден");
      return;
    }
    try {
      restoreManualProjectObject(legacyProject, "Старый проект восстановлен");
    } catch (error) {
      setBackupStatus(`Ошибка восстановления: ${error?.message || "неверный формат"}`);
    }
  };

  const onImportProjectBackupFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      const blockStoryboardProject = applyManualBlockStoryboardImport(project || {}, parsed);
      if (blockStoryboardProject) {
        persistProject(blockStoryboardProject);
        console.debug("[manual director import video/photo]", {
          stats: getManualClipBoardMaterialStats(blockStoryboardProject),
          selectedSceneId: blockStoryboardProject.selectedSceneId,
        });
        setBackupStatus("Раскадровка блока импортирована");
        window.setTimeout(() => setBackupStatus(""), 2200);
        return;
      }
      const blockVideoPromptProject = applyManualBlockVideoPromptImport(project || {}, parsed);
      if (blockVideoPromptProject) {
        persistProject(blockVideoPromptProject);
        console.debug("[manual director import video/photo]", {
          stats: getManualClipBoardMaterialStats(blockVideoPromptProject),
          selectedSceneId: blockVideoPromptProject.selectedSceneId,
        });
        setBackupStatus("Видео-промты блока импортированы");
        window.setTimeout(() => setBackupStatus(""), 2200);
        return;
      }
      restoreManualProjectObject(parsed, "Backup восстановлен");
    } catch (error) {
      setBackupStatus(`Ошибка JSON: ${error?.message || "неверный формат"}`);
    }
  };

  const selectedScene = useMemo(() => scenes.find((s) => s.scene_id === selectedSceneId) || scenes[0] || null, [scenes, selectedSceneId]);
  const selectedMMAudioGainDb = useMemo(() => {
    const raw = selectedScene?.mmaudio_gain_db ?? selectedScene?.generatedAudioGainDb ?? selectedScene?.generated_audio_gain_db;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? Math.max(-24, Math.min(6, parsed)) : -6;
  }, [selectedScene?.mmaudio_gain_db, selectedScene?.generatedAudioGainDb, selectedScene?.generated_audio_gain_db]);
  const selectedHasMMAudioResult = Boolean(
    selectedScene
    && (
      String(selectedScene.mmaudio_status || "").toLowerCase() === "done"
      || String(selectedScene.mmaudio_video_url || selectedScene.mmaudioVideoUrl || "").trim()
      || String(selectedScene.generatedAudioPolicy || selectedScene.generated_audio_policy || "").trim() === "mmaudio_generated_audio"
    )
  );

  useEffect(() => {
    setMMAudioGainDraftDb(selectedMMAudioGainDb);
  }, [selectedScene?.scene_id, selectedMMAudioGainDb]);

  const currentBlock = useMemo(() => {
    const blockId = String(selectedScene?.story_block_id || "").trim();
    if (!blockId) return null;
    return storyBlocks.find((block) => String(block?.block_id || "") === blockId || String(block?.id || "") === blockId) || null;
  }, [selectedScene?.story_block_id, storyBlocks]);
  const selectedSceneIndex = useMemo(() => scenes.findIndex((s) => s.scene_id === selectedScene?.scene_id), [scenes, selectedScene]);
  const storyPositionText = selectedScene
    ? (selectedScene.story_position_ru || buildStoryPositionFallback(selectedScene, selectedSceneIndex >= 0 ? selectedSceneIndex : 0, scenes.length))
    : "";
  const dramaturgyText = selectedScene ? (selectedScene.drama_hint || selectedScene.short_note || "—") : "—";
  const sceneGoalText = selectedScene ? (selectedScene.scene_goal_ru || selectedScene.drama_hint || selectedScene.short_note || "—") : "—";
  const promptHintText = selectedScene
    ? (selectedScene.prompt_hint_ru || "Напишите prompt вручную под выбранное изображение.")
    : "Напишите prompt вручную под выбранное изображение.";
  const blockSceneCounts = useMemo(() => {
    const counts = new Map();
    scenes.forEach((scene) => {
      const blockId = String(scene?.story_block_id || "").trim();
      if (!blockId) return;
      counts.set(blockId, (counts.get(blockId) || 0) + 1);
    });
    return counts;
  }, [scenes]);
  const visibleStoryBlocks = useMemo(() => storyBlocks.filter((block, idx) => {
    const blockId = String(block?.block_id || block?.id || `block_${idx + 1}`).trim();
    return !(blockId === "block_unknown" && !blockSceneCounts.get(blockId));
  }), [storyBlocks, blockSceneCounts]);
  const storyBlockNumberById = useMemo(() => {
    const numbers = new Map();
    visibleStoryBlocks.forEach((block, idx) => {
      const blockId = String(block?.block_id || block?.id || `block_${idx + 1}`).trim();
      if (blockId) numbers.set(blockId, idx + 1);
    });
    return numbers;
  }, [visibleStoryBlocks]);
  const selectedBlockSceneIndex = useMemo(() => {
    if (!selectedScene?.story_block_id) return 0;
    return scenes.filter((scene) => scene.story_block_id === selectedScene.story_block_id).findIndex((scene) => scene.scene_id === selectedScene.scene_id) + 1;
  }, [scenes, selectedScene]);
  const selectedBlockSceneCount = selectedScene?.story_block_id ? (blockSceneCounts.get(selectedScene.story_block_id) || 0) : 0;
  const selectedBlockNumber = selectedScene?.story_block_id ? storyBlockNumberById.get(selectedScene.story_block_id) : null;
  const selectedSceneActionText = selectedScene ? (selectedScene.scene_goal_ru || selectedScene.prompt_hint_ru || selectedScene.photo_prompt_hint_ru || "") : "";
  const selectedSceneRuText = selectedScene ? (selectedScene.translated_text_ru || selectedScene.short_note || selectedScene.drama_hint || "") : "";
  const selectedSceneOriginalText = selectedScene ? (selectedScene.original_text || selectedScene.adapted_text_en || selectedScene.source_text_en || "") : "";
  const projectMode = String(project?.project_mode || project?.projectMode || "");
  const isMusicClipProject = projectMode === MANUAL_TIMING_MUSIC_CLIP_MODE;
  const isPodcastDialogueProject = projectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE;
  const selectedSceneMeaningDetails = selectedScene ? [
    ["scene_id", selectedScene.scene_id],
    ...(isMusicClipProject ? [
      ["song_block", selectedScene.song_block_title_ru || selectedScene.song_block_id],
      ["song_block_type", selectedScene.song_block_type],
      ["lyrics_text", selectedScene.lyrics_text],
      ["route", selectedScene.route],
      ["lip_sync_required", selectedScene.lip_sync_required ? "yes" : "no"],
      ["vocal_owner_role", selectedScene.vocal_owner_role],
    ] : []),
    ...(isPodcastDialogueProject ? [
      ["speaker", selectedScene.speaker_name || selectedScene.speaker_id],
      ["topic_block", selectedScene.topic_block_title_ru || selectedScene.topic_block_id],
      ["scene_type", selectedScene.scene_type],
      ["narrator_text_en", selectedScene.narrator_text_en],
      ["narrator_text_ru", selectedScene.narrator_text_ru],
      ["speaker_text_en", selectedScene.speaker_text_en],
      ["speaker_text_ru", selectedScene.speaker_text_ru],
    ] : []),
    ["Original", selectedSceneOriginalText],
    ["Meaning", selectedScene.meaning_hint_ru],
    ["Цель сцены", selectedScene.scene_goal_ru],
    ["Подсказка для фото", selectedScene.photo_prompt_hint_ru],
    ["Подсказка для оживления", selectedScene.prompt_hint_ru],
    ["Цель блока", selectedScene.story_block_goal_ru],
    ["Раскрытие блока", selectedScene.story_block_reveal_ru],
    ["Эмоция блока", selectedScene.story_block_emotion_ru],
    ["Роль сцены в блоке", selectedScene.scene_role_in_block_ru],
    ["Прогресс блока", selectedScene.block_progress_ru],
  ].filter(([, value]) => String(value || "").trim()) : [];
  const selectedSceneAudioPhrases = useMemo(() => getAudioPhrasesForScene(audioPhrases, selectedScene), [audioPhrases, selectedScene]);

  const selectedBlockContextId = selectedScene?.story_block_id || selectedScene?.scene_id || "";

  const flashBlockCopyStatus = (message) => {
    setBlockCopyStatus(message);
    window.setTimeout(() => setBlockCopyStatus(""), 1800);
  };

  const buildCurrentManualBlockProject = () => ({ ...(project || {}), story_blocks: storyBlocks, scenes, selectedSceneId });

  const getQuickListenDurationSec = () => {
    const audioEl = quickListenAudioRef.current;
    const mediaDuration = Number(audioEl?.duration || 0);
    const projectDuration = Number(audio?.duration_sec || project?.audio_duration_sec || 0);
    if (Number.isFinite(mediaDuration) && mediaDuration > 0) return mediaDuration;
    if (Number.isFinite(projectDuration) && projectDuration > 0) return projectDuration;
    return 0;
  };

  const clampQuickListenSec = (value) => {
    const num = Number(value || 0);
    const safeValue = Number.isFinite(num) ? Math.max(0, num) : 0;
    const duration = getQuickListenDurationSec();
    return duration > 0 ? Math.min(duration, safeValue) : safeValue;
  };

  const stopQuickListenRaf = () => {
    if (quickListenRafRef.current) {
      window.cancelAnimationFrame(quickListenRafRef.current);
      quickListenRafRef.current = null;
    }
  };

  const resetQuickListenState = () => {
    stopQuickListenRaf();
    setIsAudioPlaying(false);
    setPlaybackMode("");
    playbackRangeRef.current = { startSec: 0, endSec: null };
    setPlaybackRange({ startSec: 0, endSec: null });
  };

  const stopOrPauseCurrentPlayback = () => {
    const audioEl = quickListenAudioRef.current;
    if (audioEl) {
      try {
        audioEl.pause();
      } catch {}
    }
    resetQuickListenState();
  };

  const stopAtQuickListenRangeEndIfNeeded = () => {
    const audioEl = quickListenAudioRef.current;
    if (!audioEl) return false;
    const endSec = Number(playbackRangeRef.current?.endSec);
    if (!Number.isFinite(endSec)) return false;
    if (Number(audioEl.currentTime || 0) < endSec - 0.012) return false;

    try {
      audioEl.pause();
      audioEl.currentTime = clampQuickListenSec(endSec);
    } catch {}
    resetQuickListenState();
    return true;
  };

  const startQuickListenRaf = () => {
    stopQuickListenRaf();
    const tick = () => {
      const audioEl = quickListenAudioRef.current;
      if (!audioEl || audioEl.paused) {
        setIsAudioPlaying(false);
        quickListenRafRef.current = null;
        return;
      }
      if (stopAtQuickListenRangeEndIfNeeded()) {
        quickListenRafRef.current = null;
        return;
      }
      quickListenRafRef.current = window.requestAnimationFrame(tick);
    };
    quickListenRafRef.current = window.requestAnimationFrame(tick);
  };

  const playQuickListenRange = (mode, startValue = 0, endValue = null) => {
    const audioEl = quickListenAudioRef.current;
    if (!audioUrl) {
      setBackupStatus("В текущей доске нет аудио для прослушивания");
      window.setTimeout(() => setBackupStatus(""), 2200);
      console.warn("[MANUAL BOARD AUDIO] quick listen blocked: current board has no audio", {
        sourceNodeId: getProjectOwnerNodeId(projectRef.current || project),
        project_id: project?.project_id || project?.projectId || "",
      });
      return;
    }
    if (!audioEl) return;

    if (isAudioPlaying && playbackMode === mode) {
      stopOrPauseCurrentPlayback();
      return;
    }

    const startSec = clampQuickListenSec(startValue);
    const rawEndSec = Number(endValue);
    const endSec = Number.isFinite(rawEndSec) ? clampQuickListenSec(rawEndSec) : null;
    if (endSec !== null && endSec <= startSec + 0.02) return;

    stopQuickListenRaf();
    try {
      audioEl.pause();
      audioEl.currentTime = startSec;
    } catch {}

    setPlaybackMode(mode);
    playbackRangeRef.current = { startSec, endSec };
    setPlaybackRange({ startSec, endSec });
    setIsAudioPlaying(false);

    window.setTimeout(() => {
      const activeAudio = quickListenAudioRef.current;
      if (!activeAudio) return;
      try {
        activeAudio.currentTime = startSec;
      } catch {}
      activeAudio.play().then(() => {
        setPlaybackMode(mode);
        playbackRangeRef.current = { startSec, endSec };
        setPlaybackRange({ startSec, endSec });
        setIsAudioPlaying(true);
        startQuickListenRaf();
      }).catch(() => {
        setIsAudioPlaying(false);
      });
    }, 30);
  };

  const playSceneRange = () => {
    if (!selectedScene) return;
    playQuickListenRange("scene", Number(selectedScene.start_sec || 0), Number(selectedScene.end_sec || 0));
  };

  const playBlockRange = () => {
    if (!currentBlock) return;
    playQuickListenRange("block", Number(currentBlock.start_sec || 0), Number(currentBlock.end_sec || 0));
  };

  const playFullAudio = () => {
    playQuickListenRange("full", 0, null);
  };

  const onQuickListenTimeUpdate = () => {
    const audioEl = quickListenAudioRef.current;
    if (!audioEl) return;
    if (audioEl.paused) {
      setIsAudioPlaying(false);
      stopQuickListenRaf();
      return;
    }
    if (!stopAtQuickListenRangeEndIfNeeded()) setIsAudioPlaying(true);
  };

  useEffect(() => () => stopQuickListenRaf(), []);

  useEffect(() => {
    stopOrPauseCurrentPlayback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioUrl]);

  useEffect(() => {
    stopOrPauseCurrentPlayback();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedSceneId]);

  const onCopyBlockStoryboardJson = async () => {
    try {
      const payload = buildManualBlockStoryboardContextJson(
        buildCurrentManualBlockProject(),
        selectedBlockContextId,
      );
      await navigator.clipboard?.writeText(JSON.stringify(payload, null, 2));
      flashBlockCopyStatus(`JSON фото блока скопирован: ${payload.scenes.length} сцен`);
    } catch (error) {
      flashBlockCopyStatus(`Не удалось скопировать JSON фото: ${error?.message || "ошибка"}`);
    }
  };

  const onCopyBlockVideoPromptJson = async () => {
    try {
      const payload = buildManualBlockVideoPromptContextJson(
        buildCurrentManualBlockProject(),
        selectedBlockContextId,
      );
      await navigator.clipboard?.writeText(JSON.stringify(payload, null, 2));
      flashBlockCopyStatus(`JSON видео блока скопирован: ${payload.scenes.length} сцен`);
    } catch (error) {
      flashBlockCopyStatus(`Не удалось скопировать JSON видео: ${error?.message || "ошибка"}`);
    }
  };


  const assertImportedBlockMatchesSelection = (payload = {}) => {
    const importedBlockId = String(
      payload?.target_block_id
      || payload?.target_block?.block_id
      || payload?.story_block?.block_id
      || payload?.block_id
      || "",
    ).trim();
    const selectedBlockId = String(selectedBlockContextId || "").trim();
    if (importedBlockId && selectedBlockId && importedBlockId !== selectedBlockId) {
      throw new Error(`import_block_mismatch:${importedBlockId}/${selectedBlockId}`);
    }
  };

  const onImportBlockStoryboardFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      assertImportedBlockMatchesSelection(parsed?.payload && typeof parsed.payload === "object" ? parsed.payload : parsed);
      const nextProject = applyManualBlockStoryboardImport(buildCurrentManualBlockProject(), parsed);
      if (!nextProject) throw new Error("manual_block_storyboard_split_type_expected");
      persistProject(nextProject);
      console.debug("[manual director import video/photo]", {
        stats: getManualClipBoardMaterialStats(nextProject),
        selectedSceneId: nextProject.selectedSceneId,
      });
      flashBlockCopyStatus("Раскадровка/фото блока импортированы");
    } catch (error) {
      flashBlockCopyStatus(`Не удалось импортировать фото JSON: ${error?.message || "ошибка"}`);
    }
  };

  const onImportBlockVideoPromptFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const parsed = JSON.parse(await file.text());
      assertImportedBlockMatchesSelection(parsed?.payload && typeof parsed.payload === "object" ? parsed.payload : parsed);
      const nextProject = applyManualBlockVideoPromptImport(buildCurrentManualBlockProject(), parsed);
      if (!nextProject) throw new Error("manual_block_video_prompt_split_type_expected");
      persistProject(nextProject);
      console.debug("[manual director import video/photo]", {
        stats: getManualClipBoardMaterialStats(nextProject),
        selectedSceneId: nextProject.selectedSceneId,
      });
      flashBlockCopyStatus("Видео-промты блока импортированы");
    } catch (error) {
      flashBlockCopyStatus(`Не удалось импортировать видео JSON: ${error?.message || "ошибка"}`);
    }
  };

  const userNoteItems = useMemo(() => {
    const raw = String(selectedScene?.user_note_ru || "");
    if (!raw.trim()) return [];
    return raw
      .split(/\n|;/g)
      .map((item) => item.trim())
      .filter(Boolean);
  }, [selectedScene?.user_note_ru]);

  const selectSceneByUser = (sceneId) => {
    const safeSceneId = String(sceneId || "").trim();
    const baseProject = projectRef.current || project || {};
    const sceneExists = Array.isArray(baseProject.scenes) && baseProject.scenes.some((scene) => scene.scene_id === safeSceneId);
    if (!safeSceneId || !sceneExists) return;
    selectedSceneIdRef.current = safeSceneId;
    setSelectedSceneId(safeSceneId);
    const nextProject = normalizeDirectorProjectOwner({
      ...baseProject,
      selectedSceneId: safeSceneId,
      updatedAt: Date.now(),
      revision: (Number(baseProject.revision || 0) || 0) + 1,
      lastPersistReason: "selected_scene_user",
    });
    projectRef.current = nextProject;
    setProject(nextProject);
    if (didHydrateRef.current && hasMeaningfulManualProject(nextProject)) {
      persistAndBroadcastDirectorProject(nextProject, { reason: "selected_scene_user" });
    }
  };

  useEffect(() => {
    setIsUserNoteEditorOpen(false);
  }, [selectedSceneId]);

  const updateScene = (sceneId, patchOrFactory, options = {}) => {
    const baseProject = projectRef.current || project || {};
    const prevScenes = Array.isArray(baseProject?.scenes) ? baseProject.scenes : [];
    const nextScenes = prevScenes.map((scene) => {
      if (scene.scene_id !== sceneId) return scene;
      const patch = typeof patchOrFactory === "function" ? patchOrFactory(scene) : patchOrFactory;
      return { ...scene, ...(patch || {}) };
    });
    const persistReason = options?.reason || "update_scene";
    const isDeletionUpdate = /delete.*(video|photo|image)|remove.*(video|photo|image)|clear.*(video|photo|image)|user_delete/i.test(persistReason);
    const now = Date.now();
    const refSelectedSceneId = String(selectedSceneIdRef.current || "").trim();
    const baseSelectedSceneId = String(baseProject.selectedSceneId || "").trim();
    const nextSelectedSceneId = prevScenes.some((scene) => scene.scene_id === refSelectedSceneId)
      ? refSelectedSceneId
      : (prevScenes.some((scene) => scene.scene_id === baseSelectedSceneId) ? baseSelectedSceneId : String(prevScenes[0]?.scene_id || ""));
    if (/job|poll|video_done|video_queued|video_running|mmaudio/i.test(persistReason) && nextSelectedSceneId !== sceneId) {
      console.info("[MANUAL BOARD BLOCK AUTO SELECT FROM JOB]", { reason: persistReason, sceneId, selectedSceneId: nextSelectedSceneId });
    }
    const nextProject = normalizeDirectorProjectOwner({
      ...baseProject,
      scenes: nextScenes,
      selectedSceneId: nextSelectedSceneId,
      updatedAt: now,
      revision: (Number(baseProject.revision || 0) || 0) + 1,
      deletionRevision: isDeletionUpdate ? Math.max(Number(baseProject.deletionRevision || baseProject.deletion_revision || 0) || 0, now) : (Number(baseProject.deletionRevision || baseProject.deletion_revision || 0) || 0),
      deletion_revision: isDeletionUpdate ? Math.max(Number(baseProject.deletion_revision || baseProject.deletionRevision || 0) || 0, now) : (Number(baseProject.deletion_revision || baseProject.deletionRevision || 0) || 0),
      deleted_media_revision: isDeletionUpdate ? Math.max(Number(baseProject.deleted_media_revision || baseProject.deletedMediaRevision || 0) || 0, now) : (Number(baseProject.deleted_media_revision || baseProject.deletedMediaRevision || 0) || 0),
      deletedMediaRevision: isDeletionUpdate ? Math.max(Number(baseProject.deletedMediaRevision || baseProject.deleted_media_revision || 0) || 0, now) : (Number(baseProject.deletedMediaRevision || baseProject.deleted_media_revision || 0) || 0),
      lastPersistReason: persistReason,
    });

    projectRef.current = nextProject;
    selectedSceneIdRef.current = nextProject.selectedSceneId;
    setProject(nextProject);

    if (didHydrateRef.current && hasMeaningfulManualProject(nextProject)) {
      persistAndBroadcastDirectorProject(nextProject, {
        ...(options || {}),
        reason: persistReason,
      });
      const savedScene = nextScenes.find((s) => s.scene_id === sceneId);
      console.debug("[manual director updateScene saved]", {
        sceneId,
        stats: getManualClipBoardMaterialStats(nextProject),
        route: savedScene?.route,
        image: savedScene?.image_url,
        prompt: Boolean(savedScene?.video_prompt),
        video: savedScene?.video_url,
        reason: persistReason,
      });
    }
  };

  const requestUploadAspectDecision = (warning) => new Promise((resolve) => {
    aspectWarningResolverRef.current = resolve;
    setUploadAspectWarning(warning);
  });

  const resolveUploadAspectDecision = (decision) => {
    const resolver = aspectWarningResolverRef.current;
    aspectWarningResolverRef.current = null;
    setUploadAspectWarning(null);
    if (typeof resolver === "function") resolver(decision);
  };

  const clearVideoPatch = (scene = {}) => {
    const sceneWithoutVideo = {
      ...scene,
      video_url: "",
      videoUrl: "",
      generated_video_url: "",
      generatedVideoUrl: "",
      final_video_url: "",
      finalVideoUrl: "",
      result_video_url: "",
      resultVideoUrl: "",
      video_asset_url: "",
      videoAssetUrl: "",
      video_preview_url: "",
      videoPreviewUrl: "",
      mmaudio_video_url: "",
      mmaudioVideoUrl: "",
      mmaudio_raw_video_url: "",
      mmaudioRawVideoUrl: "",
      mmaudio_source_video_url: "",
      mmaudioSourceVideoUrl: "",
      original_video_before_mmaudio_url: "",
      originalVideoBeforeMMAudioUrl: "",
      mmaudio_status: "",
      mmaudioStatus: "",
      mmaudio_job_id: "",
      mmaudioJobId: "",
      mmaudio_error: "",
      mmaudioError: "",
      mmaudio_gain_status: "",
      mmaudioGainStatus: "",
      mmaudio_gain_error: "",
      mmaudioGainError: "",
      mmaudio_prompt: "",
      mmaudioPrompt: "",
      mmaudio_negative_prompt: "",
      mmaudioNegativePrompt: "",
      video_job_id: "",
      videoJobId: "",
      video_status: "",
      videoStatus: "",
      video_error: "",
      videoError: "",
      video_has_audio: false,
      videoHasAudio: false,
      hasAudio: false,
      generated_audio_policy: "",
      generatedAudioPolicy: "",
      generated_audio_gain_db: I2V_SOUND_GAIN_DEFAULT_DB,
      generatedAudioGainDb: I2V_SOUND_GAIN_DEFAULT_DB,
      keep_generated_audio: false,
      keepGeneratedAudio: false,
      video_request_payload_preview: null,
      videoRequestPayloadPreview: null,
      video_deleted_at: Date.now(),
      videoDeletedAt: Date.now(),
      deleted_media_revision: Date.now(),
      deletedMediaRevision: Date.now(),
      error: "",
    };
    return {
      video_url: "",
      videoUrl: "",
      generated_video_url: "",
      generatedVideoUrl: "",
      final_video_url: "",
      finalVideoUrl: "",
      result_video_url: "",
      resultVideoUrl: "",
      video_asset_url: "",
      videoAssetUrl: "",
      video_preview_url: "",
      videoPreviewUrl: "",
      mmaudio_video_url: "",
      mmaudioVideoUrl: "",
      mmaudio_raw_video_url: "",
      mmaudioRawVideoUrl: "",
      mmaudio_source_video_url: "",
      mmaudioSourceVideoUrl: "",
      original_video_before_mmaudio_url: "",
      originalVideoBeforeMMAudioUrl: "",
      mmaudio_status: "",
      mmaudioStatus: "",
      mmaudio_job_id: "",
      mmaudioJobId: "",
      mmaudio_error: "",
      mmaudioError: "",
      mmaudio_gain_status: "",
      mmaudioGainStatus: "",
      mmaudio_gain_error: "",
      mmaudioGainError: "",
      mmaudio_prompt: "",
      mmaudioPrompt: "",
      mmaudio_negative_prompt: "",
      mmaudioNegativePrompt: "",
      video_job_id: "",
      videoJobId: "",
      video_status: "",
      videoStatus: "",
      video_error: "",
      videoError: "",
      video_has_audio: false,
      videoHasAudio: false,
      hasAudio: false,
      generated_audio_policy: "",
      generatedAudioPolicy: "",
      generated_audio_gain_db: I2V_SOUND_GAIN_DEFAULT_DB,
      generatedAudioGainDb: I2V_SOUND_GAIN_DEFAULT_DB,
      keep_generated_audio: false,
      keepGeneratedAudio: false,
      video_request_payload_preview: null,
      videoRequestPayloadPreview: null,
      video_deleted_at: Date.now(),
      videoDeletedAt: Date.now(),
      deleted_media_revision: Date.now(),
      deletedMediaRevision: Date.now(),
      error: "",
      status: resolveManualSceneStatus(sceneWithoutVideo),
    };
  };

  const onDeleteSceneVideo = (scene) => {
    if (!scene?.scene_id) return;
    updateScene(
      scene.scene_id,
      (currentScene = {}) => clearVideoPatch(currentScene),
      {
        reason: "delete_scene_video_user",
        allowMaterialLoss: true,
        explicitReset: true,
      }
    );
  };

  const clearStartFramePatch = (scene = {}) => ({
    ...clearVideoPatch(scene),
    image_url: "",
    imageUrl: "",
    start_image_url: "",
    startImageUrl: "",
    image_preview_url: "",
    imagePreviewUrl: "",
    start_image_preview_url: "",
    startImagePreviewUrl: "",
    generated_image_url: "",
    generatedImageUrl: "",
    image_width: 0,
    image_height: 0,
    image_aspect_ratio: 0,
    image_aspect_label: "",
    image_upload_status: "",
    image_upload_error: "",
    ...clearManualStalePromptFields(),
    photo_deleted_at: Date.now(),
    photoDeletedAt: Date.now(),
    deleted_media_revision: Date.now(),
    deletedMediaRevision: Date.now(),
    status: "draft",
  });

  const clearEndFramePatch = (scene = {}) => ({
    ...clearVideoPatch(scene),
    end_image_url: "",
    endImageUrl: "",
    end_image_preview_url: "",
    endImagePreviewUrl: "",
    image_upload_status: "",
    image_upload_error: "",
    photo_deleted_at: Date.now(),
    photoDeletedAt: Date.now(),
    deleted_media_revision: Date.now(),
    deletedMediaRevision: Date.now(),
    status: "draft",
  });

  const onDeleteFirstLastStartImage = (scene) => {
    if (!scene?.scene_id) return;
    if (scene.video_url && !window.confirm("Удаление первого кадра также удалит видео этой сцены. Продолжить?")) return;
    updateScene(scene.scene_id, (currentScene = {}) => clearStartFramePatch(currentScene), {
      reason: "delete_first_last_start_image_user",
      allowMaterialLoss: true,
      explicitReset: true,
    });
  };

  const onDeleteFirstLastEndImage = (scene) => {
    if (!scene?.scene_id) return;
    if (scene.video_url && !window.confirm("Удаление последнего кадра также удалит видео этой сцены. Продолжить?")) return;
    updateScene(scene.scene_id, (currentScene = {}) => clearEndFramePatch(currentScene), {
      reason: "delete_first_last_end_image_user",
      allowMaterialLoss: true,
      explicitReset: true,
    });
  };

  const onDeleteScenePhoto = (scene) => {
    if (!scene?.scene_id) return;
    if (scene.video_url && !window.confirm("Удаление фото также удалит видео этой сцены. Продолжить?")) return;
    updateScene(scene.scene_id, (currentScene = {}) => ({
      ...clearStartFramePatch(currentScene),
      end_image_url: "",
      endImageUrl: "",
      end_image_preview_url: "",
      endImagePreviewUrl: "",
    }), {
      reason: "delete_scene_photo_user",
      allowMaterialLoss: true,
      explicitReset: true,
    });
  };

  const onUploadImage = async (sceneId, file, slot = "main") => {
    if (!file) return;
    const selectedUploadScene = scenes.find((scene) => scene.scene_id === sceneId) || {};
    const imageMeta = await readImageFileDimensions(file);
    const previewUrl = imageMeta.objectUrl || URL.createObjectURL(file);
    const imageAspectLabel = getImageAspectRatioLabel(imageMeta.width, imageMeta.height);
    const expectedFormat = resolveProjectAspectFormat(projectRef.current || project, selectedUploadScene);
    const mismatch = isImageAspectMismatch(imageMeta.width, imageMeta.height, expectedFormat);

    if (mismatch) {
      await requestUploadAspectDecision({
        sceneId,
        expectedFormat,
        actualFormatLabel: imageAspectLabel,
        width: imageMeta.width,
        height: imageMeta.height,
      });
      try { URL.revokeObjectURL(previewUrl); } catch {}
      return;
    }

    const isEndSlot = slot === "end";
    const shouldClearVideoAfterUpload = Boolean(selectedUploadScene.video_url);
    if (shouldClearVideoAfterUpload && !window.confirm("Замена фото удалит текущее видео сцены. Продолжить?")) {
      try { URL.revokeObjectURL(previewUrl); } catch {}
      return;
    }

    updateScene(sceneId, {
      ...(isEndSlot ? { end_image_preview_url: previewUrl } : { image_preview_url: previewUrl, start_image_preview_url: previewUrl }),
      image_upload_status: "uploading",
      image_upload_error: "",
    });

    try {
      const imageUrl = await uploadManualSceneImage(file);

      updateScene(sceneId, (currentScene = {}) => {
        const aspectFields = {
          image_width: Number(imageMeta.width || 0),
          image_height: Number(imageMeta.height || 0),
          image_aspect_ratio: imageMeta.width && imageMeta.height ? Number((imageMeta.width / imageMeta.height).toFixed(6)) : 0,
          image_aspect_label: imageAspectLabel,
        };
        const nextScene = {
          ...currentScene,
          ...aspectFields,
          ...(isEndSlot
            ? { end_image_url: imageUrl, end_image_preview_url: previewUrl }
            : { image_url: imageUrl, start_image_url: imageUrl, image_preview_url: previewUrl, start_image_preview_url: previewUrl }),
        };
        return {
          ...(isEndSlot
            ? { end_image_url: imageUrl, end_image_preview_url: previewUrl }
            : { image_url: imageUrl, start_image_url: imageUrl, image_preview_url: previewUrl, start_image_preview_url: previewUrl }),
          ...aspectFields,
          ...clearManualStalePromptFields(),
          ...(shouldClearVideoAfterUpload ? clearVideoPatch(nextScene) : {}),
          image_upload_status: "done",
          image_upload_error: "",
          status: resolveManualSceneStatus({ ...nextScene, ...(shouldClearVideoAfterUpload ? { video_url: "", videoUrl: "" } : {}) }),
          error: "",
        };
      });
    } catch (err) {
      updateScene(sceneId, {
        image_upload_status: "error",
        image_upload_error: String(err?.message || "image_upload_failed"),
        error: String(err?.message || "image_upload_failed"),
      });
    }
  };

  const onUsePreviousLastFrame = async (scene) => {
    if (!scene) return;
    const idx = scenes.findIndex((item) => item.scene_id === scene.scene_id);
    const previousScene = idx > 0 ? scenes[idx - 1] : null;
    const previousVideoUrl = String(previousScene?.video_url || "").trim();

    if (!previousScene || !previousVideoUrl) {
      updateScene(scene.scene_id, {
        error: "У предыдущей сцены нет готового видео для извлечения последнего кадра.",
      });
      return;
    }

    if (scene.video_url) {
      updateScene(scene.scene_id, {
        error: "Сначала удалите видео текущей сцены, потом можно заменить стартовое фото.",
      });
      return;
    }

    updateScene(scene.scene_id, {
      image_upload_status: "extracting_last_frame",
      image_upload_error: "",
      error: "",
    });

    try {
      const out = await extractManualVideoLastFrame({
        sceneId: scene.scene_id,
        sourceSceneId: previousScene.scene_id,
        videoUrl: previousVideoUrl,
        frameOffsetSec: 0.08,
      });
      const imageUrl = String(out?.imageUrl || out?.image_url || out?.url || "").trim();
      if (out?.ok === false || !imageUrl) {
        throw new Error(String(out?.detail || out?.hint || "last_frame_extract_failed"));
      }

      updateScene(scene.scene_id, (currentScene = {}) => {
        const nextScene = { ...currentScene, image_url: imageUrl, start_image_url: imageUrl };
        return {
          image_url: imageUrl,
          start_image_url: imageUrl,
          start_image_preview_url: "",
          image_preview_url: "",
          image_upload_status: "done",
          image_upload_error: "",
          error: "",
          status: resolveManualSceneStatus(nextScene),
        };
      });
    } catch (err) {
      updateScene(scene.scene_id, {
        image_upload_status: "error",
        image_upload_error: String(err?.message || "last_frame_extract_failed"),
        error: String(err?.message || "last_frame_extract_failed"),
      });
    }
  };

  const openMMAudioModal = (scene = {}) => {
    const sourceVideoUrl = resolveMMAudioSourceVideoUrl(scene);
    if (!sourceVideoUrl) {
      updateScene(scene.scene_id, { mmaudio_status: "error", mmaudio_error: "Сначала сгенерируй или загрузи видео" });
      return;
    }
    setMMAudioModal({ sceneId: scene.scene_id, sourceVideoUrl });
    setMMAudioPrompt(String(scene.mmaudio_prompt || ""));
    setMMAudioNegativePrompt(String(scene.mmaudio_negative_prompt || "music, speech, human voice, singing, distorted audio"));
    setMMAudioModalError("");
  };

  const submitMMAudioModal = async () => {
    const sceneId = String(mmaudioModal?.sceneId || "").trim();
    const currentScene = (projectRef.current?.scenes || []).find((scene) => String(scene?.scene_id || scene?.id || "") === sceneId) || selectedScene || {};
    const sourceVideoUrl = resolveMMAudioSourceVideoUrl(currentScene);
    const soundPrompt = String(mmaudioPrompt || "").trim();
    const negativeAudioPrompt = String(mmaudioNegativePrompt || "").trim();
    const generatedAudioGainDb = -6;
    if (!sourceVideoUrl) {
      setMMAudioModalError("Сначала сгенерируй или загрузи видео");
      return;
    }
    if (!soundPrompt) {
      setMMAudioModalError("Заполни Sound prompt");
      return;
    }
    updateScene(sceneId, {
      mmaudio_status: "queued",
      mmaudio_error: "",
      mmaudio_source_video_url: sourceVideoUrl,
      original_video_before_mmaudio_url: currentScene.original_video_before_mmaudio_url || sourceVideoUrl,
      originalVideoBeforeMMAudioUrl: currentScene.originalVideoBeforeMMAudioUrl || currentScene.original_video_before_mmaudio_url || sourceVideoUrl,
      generatedAudioGainDb: generatedAudioGainDb,
      generated_audio_gain_db: generatedAudioGainDb,
      mmaudio_prompt: soundPrompt,
      mmaudio_negative_prompt: negativeAudioPrompt,
      mmaudio_gain_db: generatedAudioGainDb,
    });
    setMMAudioModal(null);
    try {
      if (typeof onMMAudioGenerate === "function") {
        await onMMAudioGenerate({ sceneId, sourceVideoUrl, soundPrompt, negativeAudioPrompt, generatedAudioGainDb });
      } else {
        throw new Error("mmaudio_parent_handler_missing");
      }
    } catch (err) {
      updateScene(sceneId, { mmaudio_status: "error", mmaudio_error: String(err?.message || "mmaudio_start_failed") });
    }
  };

  const applyMMAudioGain = async () => {
    const sceneId = String(selectedScene?.scene_id || selectedScene?.id || "").trim();
    if (!sceneId) return;
    const gainDb = Math.max(-24, Math.min(6, Number(mmaudioGainDraftDb)));
    const sourceVideoUrl = String(
      selectedScene?.original_video_before_mmaudio_url
      || selectedScene?.originalVideoBeforeMMAudioUrl
      || selectedScene?.mmaudio_source_video_url
      || selectedScene?.mmaudioSourceVideoUrl
      || selectedScene?.video_url
      || selectedScene?.videoUrl
      || ""
    ).trim();
    const mmaudioVideoUrl = String(
      selectedScene?.mmaudio_raw_video_url
      || selectedScene?.mmaudioRawVideoUrl
      || selectedScene?.mmaudio_video_url
      || selectedScene?.mmaudioVideoUrl
      || selectedScene?.video_url
      || selectedScene?.videoUrl
      || ""
    ).trim();
    if (!sourceVideoUrl && !mmaudioVideoUrl) {
      updateScene(sceneId, { mmaudio_gain_status: "error", mmaudio_gain_error: "Нет видео для изменения громкости" }, { reason: "manual_mmaudio_gain_missing_video" });
      return;
    }
    updateScene(sceneId, { mmaudio_gain_status: "running", mmaudio_gain_error: "" }, { reason: "manual_mmaudio_gain_running" });
    try {
      const out = typeof onMMAudioGainRemix === "function"
        ? await onMMAudioGainRemix({ sceneId, sourceVideoUrl, mmaudioVideoUrl, generatedAudioGainDb: gainDb })
        : await fetchJson("/api/clip/video/mmaudio/remix-gain", {
          method: "POST",
          timeoutMs: 120000,
          body: { sceneId, sourceVideoUrl, mmaudioVideoUrl, generatedAudioGainDb: gainDb },
        });
      const resultVideoUrl = String(out?.videoUrl || out?.video_url || out?.url || "").trim();
      if (out?.ok === false || !resultVideoUrl) throw new Error(String(out?.error || out?.hint || out?.code || "mmaudio_gain_remix_failed"));
      updateScene(sceneId, {
        video_url: resultVideoUrl,
        videoUrl: resultVideoUrl,
        mmaudio_video_url: resultVideoUrl,
        mmaudioVideoUrl: resultVideoUrl,
        mmaudio_raw_video_url: selectedScene?.mmaudio_raw_video_url || selectedScene?.mmaudioRawVideoUrl || mmaudioVideoUrl,
        mmaudioRawVideoUrl: selectedScene?.mmaudioRawVideoUrl || selectedScene?.mmaudio_raw_video_url || mmaudioVideoUrl,
        original_video_before_mmaudio_url: selectedScene?.original_video_before_mmaudio_url || selectedScene?.originalVideoBeforeMMAudioUrl || sourceVideoUrl,
        originalVideoBeforeMMAudioUrl: selectedScene?.originalVideoBeforeMMAudioUrl || selectedScene?.original_video_before_mmaudio_url || sourceVideoUrl,
        video_has_audio: true,
        videoHasAudio: true,
        hasAudio: true,
        generatedAudioPolicy: "mmaudio_generated_audio",
        generated_audio_policy: "mmaudio_generated_audio",
        generatedAudioGainDb: gainDb,
        generated_audio_gain_db: gainDb,
        mmaudio_gain_db: gainDb,
        mmaudio_gain_status: "done",
        mmaudio_gain_error: "",
        mmaudio_status: "done",
        mmaudio_error: "",
        status: "video_ready",
      }, { reason: "manual_mmaudio_gain_done" });
    } catch (err) {
      updateScene(sceneId, {
        mmaudio_gain_status: "error",
        mmaudio_gain_error: String(err?.message || "mmaudio_gain_remix_failed"),
      }, { reason: "manual_mmaudio_gain_error" });
    }
  };

  async function pollManualSceneVideo(sceneId, jobId, attempt = 0) {
    const maxAttempts = 180;
    const delayMs = 5000;
    const pollKey = `${sceneId}:${jobId}`;
    const currentSceneBeforePoll = (Array.isArray(projectRef.current?.scenes) ? projectRef.current.scenes : []).find((scene) => scene.scene_id === sceneId);
    const currentJobId = String(currentSceneBeforePoll?.video_job_id || currentSceneBeforePoll?.videoJobId || "").trim();
    const currentStatus = String(currentSceneBeforePoll?.status || "").toLowerCase();
    if (!currentSceneBeforePoll || currentJobId !== String(jobId || "").trim() || !["video_queued", "video_running"].includes(currentStatus) || resolveManualSceneFinalVideoUrl(currentSceneBeforePoll) || terminalVideoJobsRef.current.has(pollKey)) {
      return;
    }
    try {
      const statusOut = await getManualSceneVideoStatus(jobId);
      const status = String(statusOut?.status || "").toLowerCase();
      const doneVideoUrl = resolveManualStatusVideoUrl(statusOut);
      const isDoneStatus = ["done", "ready", "success", "completed"].includes(status);

      if (isDoneStatus && doneVideoUrl) {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        terminalVideoJobsRef.current.add(pollKey);
        const videoHasAudio = resolveManualStatusVideoHasAudio(statusOut);

        updateScene(sceneId, (currentScene = {}) => ({
          status: "video_ready",
          video_url: doneVideoUrl,
          video_job_id: jobId,
          video_error: "",
          error: "",
          video_has_audio: videoHasAudio,
          keep_generated_audio: Boolean(statusOut?.keepGeneratedAudio ?? statusOut?.keep_generated_audio ?? currentScene?.keep_generated_audio ?? false),
          generated_audio_policy: String(statusOut?.generatedAudioPolicy ?? statusOut?.generated_audio_policy ?? currentScene?.generated_audio_policy ?? ""),
          generated_audio_gain_db: Number(statusOut?.generatedAudioGainDb ?? statusOut?.generated_audio_gain_db ?? currentScene?.generated_audio_gain_db ?? -16),
          video_request_payload_preview: {
            ...(currentScene?.video_request_payload_preview || {}),
            status: status || "done",
            resolvedWorkflowKey: String(statusOut?.workflowKey || statusOut?.resolvedWorkflowKey || currentScene?.video_request_payload_preview?.resolvedWorkflowKey || ""),
            mode: String(statusOut?.mode || currentScene?.video_request_payload_preview?.mode || ""),
            videoUrl: doneVideoUrl,
            videoHasAudio,
            keepGeneratedAudio: Boolean(statusOut?.keepGeneratedAudio ?? statusOut?.keep_generated_audio ?? currentScene?.keep_generated_audio ?? false),
            generatedAudioPolicy: String(statusOut?.generatedAudioPolicy ?? statusOut?.generated_audio_policy ?? currentScene?.generated_audio_policy ?? ""),
            generatedAudioGainDb: Number(statusOut?.generatedAudioGainDb ?? statusOut?.generated_audio_gain_db ?? currentScene?.generated_audio_gain_db ?? -16),
            soundPromptPreview: String(statusOut?.soundPromptPreview || currentScene?.video_request_payload_preview?.soundPromptPreview || "").slice(0, 180),
          },
        }));
        return;
      }

      if (isDoneStatus && !doneVideoUrl) {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        terminalVideoJobsRef.current.add(pollKey);
        updateScene(sceneId, {
          status: "video_error",
          video_job_id: jobId,
          video_error: "video_done_without_url",
          error: "Видео сгенерировано, но backend не вернул videoUrl",
        });
        return;
      }

      if (status === "error" || status === "stopped" || status === "not_found") {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        terminalVideoJobsRef.current.add(pollKey);
        updateScene(sceneId, { status: "video_error", video_job_id: jobId, video_error: String(statusOut?.error || statusOut?.hint || "video_job_failed"), error: String(statusOut?.error || statusOut?.hint || "video_job_failed") });
        return;
      }
      if (status === "queued" || status === "running") {
        updateScene(sceneId, { status: status === "queued" ? "video_queued" : "video_running", video_job_id: jobId, video_error: "", error: "" });
        setTimeout(() => pollManualSceneVideo(sceneId, jobId, attempt + 1), delayMs);
        return;
      }
      if (attempt >= maxAttempts) {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        terminalVideoJobsRef.current.add(pollKey);
        updateScene(sceneId, { status: "video_error", video_job_id: jobId, video_error: "video_poll_timeout", error: "video_poll_timeout" });
        return;
      }
      updateScene(sceneId, { status: "video_running", video_job_id: jobId, video_error: "" });
      setTimeout(() => pollManualSceneVideo(sceneId, jobId, attempt + 1), delayMs);
    } catch (err) {
      const currentScene = (Array.isArray(projectRef.current?.scenes) ? projectRef.current.scenes : []).find((scene) => scene.scene_id === sceneId);
      const currentStatus = String(currentScene?.status || "").toLowerCase();

      if (currentStatus === "video_queued" || currentStatus === "video_running") {
        const key = `${sceneId}:${jobId}`;
        const count = (videoPollErrorCountRef.current.get(key) || 0) + 1;
        videoPollErrorCountRef.current.set(key, count);

        if (count < 6) {
          updateScene(sceneId, {
            status: "video_running",
            video_job_id: jobId,
            video_error: `status polling retry ${count}/6`,
            error: "",
          });
          setTimeout(() => pollManualSceneVideo(sceneId, jobId, attempt + 1), 10000);
          return;
        }

        videoPollErrorCountRef.current.delete(key);
      }

      terminalVideoJobsRef.current.add(pollKey);
      updateScene(sceneId, { status: "video_error", video_job_id: jobId, video_error: String(err?.message || "video_poll_failed"), error: String(err?.message || "video_poll_failed") });
    }
  }

  const onCreateVideo = async (scene) => {
    const runningStatus = String(scene?.status || "").toLowerCase();
    const runningKey = String(scene?.scene_id || "").trim();
    if (runningStatus === "video_queued" || runningStatus === "video_running" || (runningKey && videoStartInFlightRef.current.has(runningKey))) {
      updateScene(scene.scene_id, {
        error: "Эта сцена уже отправлена на генерацию. Дождитесь результата или ошибки.",
        status: runningStatus || "video_running",
      });
      return;
    }
    const firstLast = isFirstLastRoute(scene.route);
    const safeImageUrl = String(firstLast ? (scene.start_image_url || scene.image_url || "") : (scene.image_url || "")).trim();
    const safeStartImageUrl = String(scene.start_image_url || scene.image_url || "").trim();
    const safeEndImageUrl = String(scene.end_image_url || "").trim();

    if (!scene.video_prompt.trim()) {
      updateScene(scene.scene_id, { error: "Добавьте video_prompt", status: "draft" });
      return;
    }
    if (firstLast) {
      if (!safeStartImageUrl || !safeEndImageUrl) {
        updateScene(scene.scene_id, { error: "Для first/last нужны первый и последний кадр", status: scene.status || "draft" });
        return;
      }
      if (safeStartImageUrl.startsWith("blob:") || safeEndImageUrl.startsWith("blob:")) {
        updateScene(scene.scene_id, {
          error: "Первый/последний кадр ещё не сохранён на сервер. Загрузите фото заново или дождитесь сохранения.",
          status: scene.status || "draft",
        });
        return;
      }
    } else if (!safeImageUrl || safeImageUrl.startsWith("blob:")) {
      updateScene(scene.scene_id, {
        error: "Фото ещё не сохранено на сервер. Загрузите фото заново или дождитесь сохранения.",
        status: scene.status || "draft",
      });
      return;
    }

    if (scene.route === "ia2v" && (!scene.audio_slice_url || !scene.audio_extracted)) {
      updateScene(scene.scene_id, { error: "Для ia2v сначала нажмите «Изъять аудио»", status: scene.status || "draft" });
      return;
    }

    if (scene.route === "i2v_text" && !String(scene.sound_prompt || "").trim()) {
      updateScene(scene.scene_id, { error: "Для i2v_text заполните поле «Промт речи и звука»: кто говорит, точная фраза, стиль голоса и фон.", status: scene.status || "draft" });
      return;
    }

    const expectedFormat = resolveLockedProjectFormat(projectRef.current || project);
    if (isSceneImageAspectMismatch(scene, expectedFormat)) {
      updateScene(scene.scene_id, {
        error: `Фото сцены не совпадает с форматом проекта ${expectedFormat}. Замените фото перед генерацией видео.`,
        status: scene.status || "draft",
      });
      return;
    }

    if (runningKey) videoStartInFlightRef.current.add(runningKey);
    const routePayload = resolveManualVideoRoutePayload(scene);
    const sceneTextPreview = scene.route === "i2v_text" ? resolveI2vTextSceneText(scene) : "";
    const requestedDurationSec = Number(scene.duration_sec || scene.audio_slice_duration_sec || 5);
    updateScene(scene.scene_id, {
      status: "video_queued",
      video_error: "",
      error: "",
      video_job_id: "",
      video_url: "",
      video_has_audio: false,
      video_request_payload_preview: null,
    });
    try {
      const payload = {
        sceneId: scene.scene_id,
        imageUrl: safeImageUrl,
        startImageUrl: firstLast ? safeStartImageUrl : undefined,
        endImageUrl: firstLast ? safeEndImageUrl : undefined,
        videoPrompt: scene.video_prompt,
        videoNegativePrompt: scene.negative_prompt || "",
        video_negative_prompt: scene.negative_prompt || "",
        soundPrompt: String(routePayload.soundPrompt ?? scene.sound_prompt ?? ""),
        sound_prompt: String(routePayload.sound_prompt ?? scene.sound_prompt ?? ""),
        negativeAudioPrompt: String(routePayload.negativeAudioPrompt ?? scene.negative_audio_prompt ?? ""),
        negative_audio_prompt: String(routePayload.negative_audio_prompt ?? scene.negative_audio_prompt ?? ""),
        positivePrompt: "",
        positive_prompt: "",
        negativePrompt: String(scene.negative_prompt || ""),
        negative_prompt: String(scene.negative_prompt || ""),
        finalPositivePrompt: "",
        final_positive_prompt: "",
        finalNegativePrompt: "",
        final_negative_prompt: "",
        speechText: String(scene.speech_text || ""),
        speech_text: String(scene.speech_text || ""),
        voiceProfile: String(scene.voice_profile || scene.narrator_voice_profile_en || ""),
        voice_profile: String(scene.voice_profile || scene.narrator_voice_profile_en || ""),
        voiceMode: String(scene.voice_mode || (scene.route === "i2v_text" ? "voiceover" : "none")),
        voice_mode: String(scene.voice_mode || (scene.route === "i2v_text" ? "voiceover" : "none")),
        voiceLanguage: String(scene.voice_language || ""),
        voice_language: String(scene.voice_language || ""),
        deliveryStyle: String(scene.delivery_style || ""),
        delivery_style: String(scene.delivery_style || ""),
        ambienceHint: "",
        ambience_hint: "",
        ambientSoundPrompt: "",
        ambient_sound_prompt: "",
        requestedDurationSec,
        targetDurationSec: requestedDurationSec,
        sceneStartSec: Number(scene.start_sec || 0),
        sceneEndSec: Number(scene.end_sec || 0),
        sceneDurationSec: Number(scene.duration_sec || requestedDurationSec),
        format: resolveLockedProjectFormat(projectRef.current || project),
        provider: "comfy_remote",
        ...routePayload,
        manualClip: true,
        manual_clip: true,
        source: "manual_clip_board",
        project_kind: project?.project_kind || "clip",
        keepGeneratedAudio: Boolean(routePayload.keepGeneratedAudio),
        generatedAudioPolicy: routePayload.generatedAudioPolicy,
        generatedAudioGainDb: Number(routePayload.generatedAudioGainDb ?? scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
      };
      const out = await startManualSceneVideo(payload);
      const jobId = resolveVideoStartJobId(out);
      if (out?.ok === false || !jobId) throw new Error(String(out?.detail || out?.error || "video_start_failed"));
      updateScene(scene.scene_id, {
        status: String(out?.status || "").toLowerCase() === "queued" ? "video_queued" : "video_running",
        video_job_id: jobId,
        video_error: "",
        error: "",
        keep_generated_audio: Boolean(payload.keepGeneratedAudio),
        generated_audio_policy: String(payload.generatedAudioPolicy || ""),
        generated_audio_gain_db: Number(payload.generatedAudioGainDb ?? I2V_SOUND_GAIN_DEFAULT_DB),
        video_request_payload_preview: {
          sceneId: scene.scene_id,
          route: scene.route,
          resolvedWorkflowKey: payload.resolvedWorkflowKey,
          renderMode: payload.renderMode,
          lipSync: payload.lipSync,
          hasAudioSliceUrl: Boolean(payload.audioSliceUrl),
          hasStartImageUrl: Boolean(payload.startImageUrl),
          hasEndImageUrl: Boolean(payload.endImageUrl),
          keepGeneratedAudio: Boolean(payload.keepGeneratedAudio),
          generatedAudioPolicy: payload.generatedAudioPolicy,
          generatedAudioGainDb: Number(payload.generatedAudioGainDb ?? I2V_SOUND_GAIN_DEFAULT_DB),
          jobId,
          requestSignature: String(out?.requestSignature || ""),
          deduplicated: Boolean(out?.deduplicated),
          queueStatus: String(out?.queueStatus || out?.status || ""),
          soundPromptPreview: String(out?.payloadSoundPromptPreview || payload.soundPrompt || "").slice(0, 180),
          negativeAudioPromptPreview: String(out?.payloadNegativeAudioPromptPreview || payload.negativeAudioPrompt || payload.negative_audio_prompt || "").slice(0, 180),
          sceneTextPreview: String(sceneTextPreview || "").slice(0, 180),
          narratorTextPreview: String(payload.narratorText || "").slice(0, 180),
          speakerTextPreview: String(payload.speakerText || "").slice(0, 180),
        },
      });
      if (runningKey) videoStartInFlightRef.current.delete(runningKey);
      const resumedKey = `${scene.scene_id}:${jobId}`;
      resumedVideoJobsRef.current.add(resumedKey);
      pollManualSceneVideo(scene.scene_id, jobId, 0);
    } catch (err) {
      if (runningKey) videoStartInFlightRef.current.delete(runningKey);
      updateScene(scene.scene_id, { status: "video_error", video_error: String(err?.message || "video_start_failed"), error: String(err?.message || "video_start_failed") });
    }
  };

  const selectedMMAudioSourceVideoUrl = selectedScene ? resolveMMAudioSourceVideoUrl(selectedScene) : "";

  const selectedDisplayMedia = useMemo(() => {
    if (!selectedScene) return { url: "", field: "none", type: "empty" };
    const candidates = [
      { field: "mmaudio_video_url", type: "video", url: selectedScene.mmaudio_video_url || selectedScene.mmaudioVideoUrl },
      { field: "video_url", type: "video", url: selectedScene.video_url || selectedScene.videoUrl },
      { field: "generated_video_url", type: "video", url: selectedScene.generated_video_url || selectedScene.generatedVideoUrl },
      { field: "final_video_url", type: "video", url: selectedScene.final_video_url || selectedScene.finalVideoUrl },
      { field: "result_video_url", type: "video", url: selectedScene.result_video_url || selectedScene.resultVideoUrl },
      { field: "image_preview_url", type: "image", url: selectedScene.image_preview_url },
      { field: "image_url", type: "image", url: selectedScene.image_url },
      { field: "generated_image_url", type: "image", url: selectedScene.generated_image_url || selectedScene.generatedImageUrl },
      { field: "start_image_url", type: "image", url: selectedScene.start_image_url || selectedScene.startImageUrl },
    ];
    const winner = candidates.find((candidate) => String(candidate.url || "").trim()) || { field: "none", type: "empty", url: "" };
    return { ...winner, url: String(winner.url || "").trim() };
  }, [selectedScene]);

  useEffect(() => {
    if (!selectedScene) return;
    console.info("[MANUAL BOARD DISPLAY MEDIA SOURCE]", {
      sceneId: String(selectedScene.scene_id || selectedScene.id || ""),
      displayImageUrl: selectedDisplayMedia.type === "image" ? selectedDisplayMedia.url : "",
      displayMediaUrl: selectedDisplayMedia.url,
      field: selectedDisplayMedia.field,
      type: selectedDisplayMedia.type,
    });
  }, [selectedScene?.scene_id, selectedDisplayMedia.url, selectedDisplayMedia.field, selectedDisplayMedia.type]);

  const selectedProjectFormat = selectedScene ? resolveProjectAspectFormat(projectRef.current || project, selectedScene) : resolveProjectAspectFormat(projectRef.current || project);
  const selectedImageAspectMeta = selectedScene ? getSceneImageAspectMeta(selectedScene) : { width: 0, height: 0, label: "unknown", ratio: 0 };
  const selectedImageAspectMismatch = Boolean(selectedScene && isSceneImageAspectMismatch(selectedScene, selectedProjectFormat));

  if (!project) return <div className="manualDirectorPage"><div className="manualDirectorEmpty"><h2>Проект режиссёрской доски не найден</h2><p>Сначала откройте AI-разбивку и нажмите «Перейти в режиссёрскую доску» или восстановите backup JSON.</p><div className="manualDirectorEmptyActions"><button className="clipSB_btn" onClick={() => (typeof onClose === "function" ? onClose() : navigate("/studio/storyboard"))}>Вернуться в студию</button><label className="clipSB_btn manualUploadBtn">Импорт backup / storyboard JSON<input type="file" accept=".json,application/json" hidden onChange={onImportProjectBackupFile} /></label><button className="clipSB_btn clipSB_btnSecondary" onClick={onRestoreLegacyManualProject}>Восстановить старый проект</button></div>{backupStatus ? <span className="manualDirectorBackupStatus">{backupStatus}</span> : null}</div></div>;

  return <div className="manualDirectorPage">
    <div className="manualDirectorTopbar">
      <button
        className="clipSB_btn"
        onClick={() => {
          safePersistCurrentProject(embedded ? "close_embedded_director_board" : "back_to_ai_split");
          if (embedded && typeof onClose === "function") {
            onClose();
            return;
          }
          navigate("/studio/storyboard");
        }}
      >
        Назад к AI-разбивке
      </button>
      <button className="clipSB_btn" onClick={() => {
        const currentProject = { ...(projectRef.current || project || {}), selectedSceneId };
        const ownerNodeId = getProjectOwnerNodeId(currentProject);
        const forceProjectId = String(currentProject.project_id || currentProject.projectId || "").trim();
        const forceInputSignature = String(currentProject.input_signature || currentProject.inputSignature || "").trim();
        const forceAudioSignature = String(currentProject.audio_signature || currentProject.audioSignature || "").trim();
        persistAndBroadcastDirectorProject(currentProject, { reason: "open_manual_audio_preview", forceReplace: true });
        writeManualClipBoardOpenState({
          isOpen: true,
          sourceNodeId: ownerNodeId,
          selectedSceneId: String(currentProject.selectedSceneId || currentProject.scenes?.[0]?.scene_id || "").trim(),
          project_id: forceProjectId,
          input_signature: forceInputSignature,
          audio_signature: forceAudioSignature,
          manualBoardExplicitNewProject: true,
          forceProjectId,
          forceInputSignature,
          forceAudioSignature,
          routePath: "/studio/storyboard",
          updatedAt: Date.now(),
        });
        console.info("[MANUAL BOARD AUDIO PREVIEW OPEN]", {
          sourceNodeId: ownerNodeId,
          ownerNodeId,
          project_id: forceProjectId,
          input_signature: forceInputSignature,
          audio_signature: forceAudioSignature,
          audio: {
            url: String(currentProject.audio?.url || currentProject.audio_url || currentProject.audioUrl || "").trim(),
            name: String(currentProject.audio?.name || currentProject.audio?.filename || currentProject.audio_name || "").trim(),
            duration_sec: Number(currentProject.audio?.duration_sec || currentProject.audio_duration_sec || 0) || 0,
          },
          explicitNewProject: true,
        });
        navigate("/studio/manual-clip-audio-preview", {
          state: {
            manualBoardExplicitNewProject: true,
            forceProjectId,
            forceInputSignature,
            forceAudioSignature,
            sourceNodeId: ownerNodeId,
            ownerNodeId,
            navigationProject: currentProject,
            director_board: currentProject,
            project: currentProject,
          },
        });
      }}>Прослушать сцены</button>
      <button className="clipSB_btn clipSB_btnPrimary" onClick={onForceSaveDirectorBoard}>Сохранить доску</button>
      <button className="clipSB_btn clipSB_btnPrimary" onClick={onDownloadProjectBackup}>Скачать backup проекта</button>
      <label className="clipSB_btn manualUploadBtn">Импорт backup / storyboard JSON<input type="file" accept=".json,application/json" hidden onChange={onImportProjectBackupFile} /></label>
      {backupStatus ? <span className="manualDirectorBackupStatus">{backupStatus}</span> : null}
    </div>
    {uploadAspectWarning ? <div className="manualAspectModalBackdrop" role="presentation">
      <div className="manualAspectModal" role="dialog" aria-modal="true" aria-labelledby="manual-aspect-warning-title">
        <h3 id="manual-aspect-warning-title">Проверка формата фото</h3>
        <p>Формат проекта: {uploadAspectWarning.expectedFormat}. Фото: {uploadAspectWarning.actualFormatLabel}. Загрузка заблокирована, чтобы не сломать проект. Загрузите изображение в формате {uploadAspectWarning.expectedFormat}.</p>
        <div className="manualAspectModalMeta">Image: {uploadAspectWarning.actualFormatLabel} / {uploadAspectWarning.width || "?"}x{uploadAspectWarning.height || "?"}</div>
        <div className="manualAspectModalActions">
          <button type="button" className="clipSB_btn clipSB_btnPrimary" onClick={() => resolveUploadAspectDecision("cancel")}>Понятно</button>
          <button type="button" className="clipSB_btn" onClick={() => resolveUploadAspectDecision("cancel")}>Отменить загрузку</button>
        </div>
      </div>
    </div> : null}
    {visibleStoryBlocks.length ? <div className="storyboardBlockStrip">
      {visibleStoryBlocks.map((block, idx) => {
        const blockId = String(block.block_id || block.id || `block_${idx + 1}`);
        const firstScene = scenes.find((scene) => scene.story_block_id === blockId);
        const isActive = selectedScene?.story_block_id === blockId;
        return <button
          key={`story-block-${blockId}-${idx}`}
          type="button"
          className={`storyboardBlockChip ${isActive ? "storyboardBlockChipActive" : ""}`}
          style={{ "--storyboard-block-color": block.color || "#8aa4ff" }}
          onClick={() => firstScene ? selectSceneByUser(firstScene.scene_id) : undefined}
          disabled={!firstScene}
        >
          {block.title_ru || blockId} · {blockSceneCounts.get(blockId) || 0}
        </button>;
      })}
    </div> : null}

    <section className="storyPrepTemplatePanel manualDirectorStoryPrepPanel">
      <div className="storyPrepTemplateHeader">
        <div className="storyPrepTemplateTitle">
          <h3>Шаблон подготовки сюжета</h3>
          <small>Живой production-чеклист: блоки, сцены, полные фразы, тайминги и материалы.</small>
        </div>
        <div className="storyPrepTemplateActions">
          <button
            type="button"
            className="clipSB_btn storyPrepTemplateExpandBtn"
            onClick={() => setIsStoryPrepExpanded((v) => !v)}
            aria-pressed={isStoryPrepExpanded}
            title={isStoryPrepExpanded ? "Свернуть preview" : "Развернуть preview"}
          >
            <span aria-hidden="true">{isStoryPrepExpanded ? "⤡" : "⛶"}</span>
            <span>{isStoryPrepExpanded ? "Свернуть" : "Развернуть"}</span>
          </button>
          <button className="clipSB_btn" onClick={refreshStoryPrepTemplate}>Обновить шаблон</button>
          <button className="clipSB_btn" onClick={onCopyStoryPrepTemplate}>Скопировать шаблон</button>
          <button className="clipSB_btn" onClick={onDownloadStoryPrepTemplate}>Скачать .txt</button>
        </div>
      </div>
      {isStoryPrepExpanded ? (
        <textarea className="storyPrepTemplatePreview" value={storyPrepTemplateText} onChange={(e) => setStoryPrepTemplateText(e.target.value)} spellCheck={false} />
      ) : null}
    </section>

    <div className="manualDirectorGrid">
      <aside className="manualDirectorScenes">
        {scenes.map((scene, idx) => {
          const sceneBlockNumber = scene.story_block_id ? storyBlockNumberById.get(scene.story_block_id) : null;
          const sceneBlockTitle = String(scene.story_block_title_ru || "").trim();
          const sceneRuPreview = truncateText(
            scene.translated_text_ru || scene.short_note || scene.drama_hint || scene.scene_goal_ru || "—",
            104
          );
          return <button
            key={scene.scene_id}
            className={`manualDirectorSceneItem ${selectedScene?.scene_id === scene.scene_id ? "active" : ""} ${scene.status === STATUS_VIDEO_READY ? "ready" : ""}`}
            style={scene.story_block_color ? { "--storyboard-block-color": scene.story_block_color } : undefined}
            onClick={() => selectSceneByUser(scene.scene_id)}
          >
            <strong>{idx + 1} сцена</strong><span>{scene.route}</span><span>{Number(scene.start_sec).toFixed(2)}–{Number(scene.end_sec).toFixed(2)} c</span><span className={`manualStatusBadge ${scene.status === STATUS_VIDEO_READY ? "ready" : scene.status === "video_error" ? "error" : (scene.status === "video_running" || scene.status === "video_queued") ? "running" : ""}`}>{getSceneStatusLabel(scene)}</span>
            {(sceneBlockNumber || sceneBlockTitle) ? <span className="manualStoryBlockBadges">
              {sceneBlockNumber ? <span className="manualStoryBlockBadge manualStoryBlockNumberBadge">Блок {sceneBlockNumber}</span> : null}
              {sceneBlockTitle ? <span className="manualStoryBlockBadge">{sceneBlockTitle}</span> : null}
            </span> : null}
            <small className="manualSceneRuPreview">{sceneRuPreview}</small>
          </button>;
        })}
      </aside>

      {selectedScene ? <section className="manualDirectorCenter">
        <div className="storyboardSceneBlockHeader">
          <div className="storyboardSceneBlockTitleRow">
            <h2>Сцена {selectedScene.index}</h2>
            <div className="manualBlockWorkflowActions" aria-label="Workflow JSON блока">
              <button
                type="button"
                className="clipSB_btn manualBlockPhotoBtn"
                title="Скопировать JSON блока для генерации фото / раскадровки"
                onClick={onCopyBlockStoryboardJson}
              >🖼 Фото JSON</button>
              <button
                type="button"
                className="clipSB_btn manualBlockVideoBtn"
                title="Скопировать JSON блока для video prompts"
                onClick={onCopyBlockVideoPromptJson}
              >🎬 Видео JSON</button>
              <label
                className="clipSB_btn manualUploadBtn manualBlockPhotoBtn manualBlockImportBtn"
                title="Импортировать JSON раскадровки блока"
              >📥 Фото<input type="file" accept=".json,application/json" hidden onChange={onImportBlockStoryboardFile} /></label>
              <label
                className="clipSB_btn manualUploadBtn manualBlockVideoBtn manualBlockImportBtn"
                title="Импортировать JSON видео-промтов блока"
              >📥 Видео<input type="file" accept=".json,application/json" hidden onChange={onImportBlockVideoPromptFile} /></label>
            </div>
          </div>
          {selectedScene.story_block_title_ru ? <div className="storyboardSceneBlockBadge" style={{ "--storyboard-block-color": selectedScene.story_block_color || "#8aa4ff" }}>
            {selectedBlockNumber ? <span>Блок {selectedBlockNumber}</span> : null}
            <strong>Блок: {selectedScene.story_block_title_ru}</strong>
          </div> : null}
          {blockCopyStatus ? <span className="manualBlockCopyStatus">{blockCopyStatus}</span> : null}
        </div>
        <div className="manualDirectorQuickListenBar" aria-label="Быстрое прослушивание аудио" title={`Диапазон прослушивания: ${formatDirectorSec(playbackRange.startSec)} → ${playbackRange.endSec === null ? "конец" : formatDirectorSec(playbackRange.endSec)} c`}>
          {!audioUrl ? <div className="manualAudioPending">В текущей доске нет аудио для быстрого прослушивания.</div> : null}
          {audioUrl ? <audio
            ref={quickListenAudioRef}
            className="manualDirectorQuickListenAudio"
            src={audioUrl}
            preload="metadata"
            onTimeUpdate={onQuickListenTimeUpdate}
            onPlay={() => { setIsAudioPlaying(true); startQuickListenRaf(); }}
            onPause={() => { setIsAudioPlaying(false); stopQuickListenRaf(); }}
            onEnded={resetQuickListenState}
          /> : null}
          <button
            type="button"
            className="clipSB_btn manualDirectorQuickListenScene"
            onClick={playSceneRange}
            disabled={!audioUrl || !selectedScene}
          >{isAudioPlaying && playbackMode === "scene" ? "⏸ Сцена" : "▶ Сцена"}</button>
          <button
            type="button"
            className="clipSB_btn manualDirectorQuickListenBlock"
            onClick={playBlockRange}
            disabled={!audioUrl || !currentBlock}
          >{isAudioPlaying && playbackMode === "block" ? "⏸ Блок" : "🟢 Блок"}</button>
          <button
            type="button"
            className="clipSB_btn manualDirectorQuickListenFull"
            onClick={playFullAudio}
            disabled={!audioUrl}
          >{isAudioPlaying && playbackMode === "full" ? "⏸ Всё аудио" : "🎵 Всё аудио"}</button>
        </div>

        <label>Route
          <select value={selectedScene.route} onChange={(e) => {
            const route = e.target.value;
            updateScene(selectedScene.scene_id, {
              route,
              keep_generated_audio: (route === "i2v_sound" || route === "i2v_text" || route === "first_last_sound") ? true : false,
              generated_audio_policy: (route === "i2v_sound" || route === "i2v_text" || route === "first_last_sound") ? "mix_generated_audio_under_master" : "",
              generated_audio_gain_db: (route === "i2v_sound" || route === "i2v_text" || route === "first_last_sound") ? Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB) : Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
              start_image_url: isFirstLastRoute(route) ? String(selectedScene.start_image_url || selectedScene.image_url || "") : selectedScene.start_image_url,
              image_url: isFirstLastRoute(route) ? String(selectedScene.start_image_url || selectedScene.image_url || "") : selectedScene.image_url,
            });
          }}>{ROUTES.map((route) => <option key={route} value={route}>{route}</option>)}</select>
        </label>

        <div className="manualTimingReadonly">
          <div>scene_id: {selectedScene.scene_id}</div>
          <div>Тайминг сцены: {formatDirectorSec(selectedScene.start_sec)} → {formatDirectorSec(selectedScene.end_sec)} c</div>
          <div>Длительность: {formatDirectorSec(selectedScene.duration_sec)} c</div>
          {selectedScene.source_phrase_ids?.length ? <div>source_phrase_ids: {selectedScene.source_phrase_ids.join(", ")}</div> : null}
        </div>

        {isStoryVoiceover ? <section className="manualDirectorStoryPassPanel">
          <div className="manualDirectorStoryPassHeader">
            <strong>Story Pass сцены</strong>
            {selectedScene.story_block_title_ru ? <span style={{ "--storyboard-block-color": selectedScene.story_block_color || "#8aa4ff" }}>{selectedBlockNumber ? `Блок ${selectedBlockNumber}: ` : ""}{selectedScene.story_block_title_ru}</span> : null}
          </div>
          <div className="manualDirectorStoryPassCompactHint">
            {selectedSceneRuText || selectedSceneActionText || "Подробный Story Pass доступен в раскрывающемся блоке ниже и через кнопки копирования JSON/текста блока."}
          </div>
          <details className="manualDirectorStoryPassDetails">
            <summary>Показать подробные поля Story Pass сцены</summary>
            <div className="manualDirectorStoryPassGrid">
              <span>translated_text_ru</span><strong>{selectedScene.translated_text_ru || "—"}</strong>
              <span>original_text</span><strong>{selectedSceneOriginalText || "—"}</strong>
              <span>meaning_hint_ru</span><strong>{selectedScene.meaning_hint_ru || "—"}</strong>
              <span>scene_goal_ru</span><strong>{selectedScene.scene_goal_ru || "—"}</strong>
              <span>photo_prompt_hint_ru</span><strong>{selectedScene.photo_prompt_hint_ru || "—"}</strong>
              <span>prompt_hint_ru</span><strong>{selectedScene.prompt_hint_ru || "—"}</strong>
              <span>scene_role_in_block_ru</span><strong>{selectedScene.scene_role_in_block_ru || "—"}</strong>
              <span>block_progress_ru</span><strong>{selectedScene.block_progress_ru || "—"}</strong>
            </div>
          </details>
          <details className="manualDirectorAsrDetails">
            <summary>ASR-фразы сцены ({selectedSceneAudioPhrases.length})</summary>
            {selectedSceneAudioPhrases.length ? <div className="manualDirectorAsrList">
              {selectedSceneAudioPhrases.map((phrase, idx) => <div key={`${selectedScene.scene_id}-asr-${phrase.phrase_id || idx}`}>
                <b>{phrase.phrase_id || idx + 1}</b> · {formatDirectorSec(phrase.start_sec)}→{formatDirectorSec(phrase.end_sec)} · {phrase.text_ru || phrase.text_en || phrase.text || "—"}
              </div>)}
            </div> : <div className="manualDirectorAsrList">ASR-фразы не найдены для source_phrase_ids этой сцены.</div>}
          </details>
        </section> : null}

        {!isStoryVoiceover && isMeaningSceneVisible(selectedScene) ? <div className="storyboardSceneMeaningCompact">
          <div className="storyboardSceneMeaningHeader">
            <strong>Смысл сцены</strong>
            {selectedScene.story_block_title_ru ? <span style={{ "--storyboard-block-color": selectedScene.story_block_color || "#8aa4ff" }}>{selectedBlockNumber ? `Блок ${selectedBlockNumber}: ` : ""}{selectedScene.story_block_title_ru}</span> : null}
            {selectedBlockSceneCount ? <em>сцена {selectedBlockSceneIndex || 1} из {selectedBlockSceneCount}</em> : null}
          </div>
          <div className="storyboardSceneMeaningBody">
            {isMusicClipProject && (selectedScene.song_block_title_ru || selectedScene.song_block_id) ? <p><b>Song block:</b> {selectedScene.song_block_title_ru || selectedScene.song_block_id} {selectedScene.song_block_type ? `(${selectedScene.song_block_type})` : ""}</p> : null}
            {isMusicClipProject && selectedScene.lyrics_text ? <p><b>Lyrics:</b> {selectedScene.lyrics_text}</p> : null}
            {isMusicClipProject ? <p><b>Route:</b> {selectedScene.route} · lip-sync: {selectedScene.lip_sync_required ? "yes" : "no"}{selectedScene.vocal_owner_role ? ` · ${selectedScene.vocal_owner_role}` : ""}</p> : null}
            {isPodcastDialogueProject && (selectedScene.speaker_name || selectedScene.speaker_id) ? <p><b>Speaker:</b> {selectedScene.speaker_name || selectedScene.speaker_id}</p> : null}
            {isPodcastDialogueProject && (selectedScene.topic_block_title_ru || selectedScene.topic_block_id) ? <p><b>Topic:</b> {selectedScene.topic_block_title_ru || selectedScene.topic_block_id}</p> : null}
            {isPodcastDialogueProject && selectedScene.scene_type ? <p><b>Scene type:</b> {selectedScene.scene_type}</p> : null}
            {isPodcastDialogueProject && (selectedScene.narrator_text_en || selectedScene.speaker_text_en) ? <p><b>Text EN:</b> {selectedScene.narrator_text_en || selectedScene.speaker_text_en}</p> : null}
            {selectedScene.story_block_title_ru ? <p><b>Блок:</b> {selectedScene.story_block_title_ru}</p> : null}
            {(selectedScene.story_block_position_ru || storyPositionText) ? <p><b>Позиция:</b> {selectedScene.story_block_position_ru || storyPositionText}</p> : null}
            {selectedSceneRuText ? <p><b>Фраза RU:</b> {selectedSceneRuText}</p> : null}
            {selectedSceneActionText ? <p><b>Что делать:</b> {selectedSceneActionText}</p> : null}
          </div>
          {selectedSceneMeaningDetails.length ? <details className="storyboardSceneMeaningDetails">
            <summary>Подробнее</summary>
            <div className="storyboardSceneMeaningGrid">
              {selectedSceneMeaningDetails.map(([label, value]) => <React.Fragment key={`${selectedScene.scene_id}-${label}`}>
                <span>{label}</span>
                <strong>{value}</strong>
              </React.Fragment>)}
            </div>
          </details> : null}
        </div> : null}

        <div className="manualSceneGuidance">
          <strong>Подсказка сцены</strong>
          <div>Позиция: {storyPositionText}</div>
          <div>Драматургия: {dramaturgyText}</div>
          <div>Смысл: {sceneGoalText}</div>
          <div>Что учесть в prompt: {promptHintText}</div>
          {userNoteItems.length ? <div className="manualUserNotesList">
            <div>Заметки пользователя:</div>
            {userNoteItems.map((item, idx) => <div key={`${selectedScene.scene_id}-user-note-${idx}`}>• {item}</div>)}
          </div> : null}
        </div>
        <div className="manualUserNoteEditor">
          <button className="clipSB_btn manualUserNoteToggle" onClick={() => setIsUserNoteEditorOpen((prev) => !prev)}>
            {isUserNoteEditorOpen ? "Скрыть заметку" : "+ заметка"}
          </button>
          {isUserNoteEditorOpen ? <textarea
            value={String(selectedScene.user_note_ru || "")}
            placeholder="Своя заметка к сцене: звук, фраза, визуал, что не забыть..."
            onChange={(e) => updateScene(selectedScene.scene_id, { user_note_ru: e.target.value })}
          /> : null}
        </div>

        <label className="manualPromptBlock">Video / motion prompt<textarea value={selectedScene.video_prompt} onChange={(e) => {
          const nextScene = { ...selectedScene, video_prompt: e.target.value };
          updateScene(selectedScene.scene_id, { video_prompt: e.target.value, status: resolveManualSceneStatus(nextScene) });
        }} /></label>
        <label className="manualNegativePromptBlock">Negative Prompt<textarea value={selectedScene.negative_prompt} onChange={(e) => {
          const nextScene = { ...selectedScene, negative_prompt: e.target.value };
          updateScene(selectedScene.scene_id, { negative_prompt: e.target.value, status: resolveManualSceneStatus(nextScene) });
        }} /></label>
        {selectedScene.route === "i2v_text" ? <section className="manualSoundBox">
          <strong>Текст сцены</strong>
          <div className="manualSceneTextDisplay" aria-readonly="true">{resolveI2vTextSceneText(selectedScene) || "—"}</div>
          <strong>Промт речи и звука</strong>
          <label className="manualPromptBlock">
            <textarea
              value={selectedScene.sound_prompt}
              placeholder={'Narrator voice-over says exactly: "..."\nCalm documentary male narrator, clear pronunciation.\nQuiet room tone, subtle ambience, no music overpowering the voice.\nAvoid robotic voice, slurred words, distorted speech.'}
              onChange={(e) => updateScene(selectedScene.scene_id, { sound_prompt: e.target.value, generated_speech_required: true })}
            />
          </label>
          <div className="manualRouteHint">В этом поле одной инструкцией укажите: кто говорит, точную фразу, стиль голоса и фоновые звуки. Текст сцены выше показан только как подсказка — его можно скопировать в prompt.</div>
        </section> : null}
        {isGeneratedSoundRoute(selectedScene.route) ? <section className="manualSoundBox">
          <strong>Громкость сценического звука</strong>
          <div className="manualRouteHint">Действия и звуки описывайте в основном Video / motion prompt. Запреты для звука и картинки пишите в общий Negative Prompt. Отдельного Sound prompt / Helper negative audio prompt в clean Comfy workflow нет.</div>
          <label className="manualGainControl">Громкость в монтаже после нормализации: {Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB).toFixed(0)} dB
            <input type="range" min={I2V_SOUND_GAIN_MIN_DB} max={I2V_SOUND_GAIN_MAX_DB} step="1" value={Number(selectedScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB)} onChange={(e) => {
              updateScene(selectedScene.scene_id, { generated_audio_gain_db: Number(e.target.value), keep_generated_audio: true, generated_audio_policy: "mix_generated_audio_under_master" });
            }} />
          </label>
          <div className="manualVideoInfo">Backend нормализует сгенерированный звук и применяет этот слайдер: -12 dB тихий фон, -6 dB слышимый эффект, 0 dB громко, +4/+6 dB сильный эффект, +10 dB максимум для теста.</div>
        </section> : null}

        <div className="manualDirectorButtons">
          {selectedScene.route === "ia2v" ? <button className="clipSB_btn" onClick={() => {
            if (!selectedScene.audio_slice_url) {
              updateScene(selectedScene.scene_id, { error: "Аудио сцены ещё не нарезано" });
              return;
            }
            const nextScene = { ...selectedScene, audio_extracted: true };
            updateScene(selectedScene.scene_id, {
              audio_extracted: true,
              error: "",
              status: resolveManualSceneStatus(nextScene),
            });
          }}>{selectedScene.audio_extracted ? "Переизъять аудио" : "Изъять аудио"}</button> : null}
          {selectedScene.route === "ia2v" && selectedScene.audio_slice_url ? <span className="manualAudioReady">Аудио сцены готово</span> : null}
          {selectedScene.route === "ia2v" && selectedScene.audio_extracted ? <span className="manualAudioExtracted">Аудио изъято · готово к ia2v</span> : null}
          <button className="clipSB_btn" disabled={selectedImageAspectMismatch || ["video_queued", "video_running"].includes(String(selectedScene.status || "").toLowerCase())} onClick={() => onCreateVideo(selectedScene)}>
            {String(selectedScene.status || "").toLowerCase() === "video_queued" ? "В очереди" : String(selectedScene.status || "").toLowerCase() === "video_running" ? "Генерация идёт" : "Создать видео"}
          </button>
          <button className="clipSB_btn" disabled={selectedSceneIndex <= 0 || !!selectedMMAudioSourceVideoUrl} onClick={() => onUsePreviousLastFrame(selectedScene)}>Взять последний кадр предыдущей</button>
          <button className="clipSB_btn" disabled={!selectedMMAudioSourceVideoUrl} onClick={() => onDeleteSceneVideo(selectedScene)}>{selectedMMAudioSourceVideoUrl ? "Удалить видео" : "Видео нет"}</button>
        </div>
        {selectedScene.error ? <div className="manualError">{selectedScene.error}</div> : null}
        {(["video_queued", "video_running", "video_error"].includes(selectedScene.status)) ? <div className="manualVideoDebug">job: {selectedScene.video_job_id || "—"} · route: {selectedScene.route} · workflow: {selectedScene.video_request_payload_preview?.resolvedWorkflowKey || "—"} · audioSlice: {selectedScene.video_request_payload_preview?.hasAudioSliceUrl ? "yes" : "no"} · keepAudio: {selectedScene.video_request_payload_preview?.keepGeneratedAudio ? "yes" : "no"} · gain: {selectedScene.video_request_payload_preview?.generatedAudioGainDb ?? selectedScene.generated_audio_gain_db ?? "—"} dB</div> : null}
      </section> : null}

      {selectedScene ? <section className="manualDirectorMedia"><h3>Media preview</h3>
        <div className="manualAspectPreviewPanel">
          <span>Project format: <strong>{selectedProjectFormat || "unknown"}</strong></span>
          <span>Image: <strong>{selectedImageAspectMeta.label || "unknown"}</strong>{selectedImageAspectMeta.width && selectedImageAspectMeta.height ? ` / ${selectedImageAspectMeta.width}x${selectedImageAspectMeta.height}` : " / unknown"}</span>
          {selectedImageAspectMismatch ? <span className="manualAspectMismatchBadge">Формат фото не совпадает с проектом</span> : null}
        </div>
        {isFirstLastRoute(selectedScene.route) && !selectedMMAudioSourceVideoUrl ? (
          <div className="manualFirstLastPanel">
            <div className="manualFirstLastSlot">
              <label className="clipSB_btn manualUploadBtn">Первый кадр<input type="file" accept="image/*" hidden onChange={(e) => onUploadImage(selectedScene.scene_id, e.target.files?.[0], "start")} /></label>
              <div className="manualMediaWindow manualMediaWindowSmall">{selectedScene.start_image_preview_url ? <img src={selectedScene.start_image_preview_url} alt="First frame preview" /> : selectedScene.start_image_url ? <img src={selectedScene.start_image_url} alt="First frame preview" /> : selectedScene.image_preview_url ? <img src={selectedScene.image_preview_url} alt="First frame preview" /> : selectedScene.image_url ? <img src={selectedScene.image_url} alt="First frame preview" /> : <div>Нет первого кадра</div>}</div>
            </div>
            <div className="manualFirstLastSlot">
              <label className="clipSB_btn manualUploadBtn">Последний кадр<input type="file" accept="image/*" hidden onChange={(e) => onUploadImage(selectedScene.scene_id, e.target.files?.[0], "end")} /></label>
              <div className="manualMediaWindow manualMediaWindowSmall">{selectedScene.end_image_preview_url ? <img src={selectedScene.end_image_preview_url} alt="Last frame preview" /> : selectedScene.end_image_url ? <img src={selectedScene.end_image_url} alt="Last frame preview" /> : <div>Нет последнего кадра</div>}</div>
            </div>
          </div>
        ) : (
          <>
            <div className="manualRepairActions">
              <label className="clipSB_btn manualUploadBtn">Заменить фото<input type="file" accept="image/*" hidden onChange={(e) => onUploadImage(selectedScene.scene_id, e.target.files?.[0], "main")} /></label>
              <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={() => onDeleteScenePhoto(selectedScene)} disabled={!selectedScene.image_url && !selectedScene.image_preview_url}>Удалить фото</button>
              <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={() => onDeleteSceneVideo(selectedScene)} disabled={!selectedMMAudioSourceVideoUrl}>Удалить видео</button>
              <button type="button" className="manualMMAButton" title={selectedMMAudioSourceVideoUrl ? "Дозвучить видео через MMAudio" : "Сначала сгенерируй или загрузи видео"} disabled={!selectedMMAudioSourceVideoUrl || ["queued", "running"].includes(String(selectedScene.mmaudio_status || "").toLowerCase())} onClick={() => openMMAudioModal(selectedScene)}>🪄 MMA</button>
            </div>
            <div className="manualMediaWindow">{selectedDisplayMedia.type === "video" ? <video key={selectedDisplayMedia.url} controls preload="metadata" src={selectedDisplayMedia.url} /> : selectedDisplayMedia.type === "image" ? <img src={selectedDisplayMedia.url} alt="Scene preview" /> : <div>Нет image/video preview</div>}</div>
          </>
        )}
        {isFirstLastRoute(selectedScene.route) && !selectedMMAudioSourceVideoUrl ? <div className="manualRepairActions">
          <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={() => onDeleteFirstLastStartImage(selectedScene)} disabled={!selectedScene.image_url && !selectedScene.image_preview_url && !selectedScene.start_image_url && !selectedScene.start_image_preview_url}>Удалить первый кадр</button>
          <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={() => onDeleteFirstLastEndImage(selectedScene)} disabled={!selectedScene.end_image_url && !selectedScene.end_image_preview_url}>Удалить последний кадр</button>
          <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={() => onDeleteSceneVideo(selectedScene)} disabled={!selectedMMAudioSourceVideoUrl}>Удалить видео</button>
          <button type="button" className="manualMMAButton" title={selectedMMAudioSourceVideoUrl ? "Дозвучить видео через MMAudio" : "Сначала сгенерируй или загрузи видео"} disabled={!selectedMMAudioSourceVideoUrl || ["queued", "running"].includes(String(selectedScene.mmaudio_status || "").toLowerCase())} onClick={() => openMMAudioModal(selectedScene)}>🪄 MMA</button>
        </div> : null}
        {selectedMMAudioSourceVideoUrl ? <a className="manualVideoLink" href={selectedMMAudioSourceVideoUrl} target="_blank" rel="noreferrer">Открыть видео напрямую</a> : null}
        {selectedHasMMAudioResult ? <div className="manualMMAGainBox manualMMAGainBoxInline">
          <div className="manualMMAGainHeader"><div><strong>Громкость MMAAudio</strong><span>Меняет громкость готовой озвучки без новой генерации.</span></div><strong>{Number(mmaudioGainDraftDb)} dB</strong></div>
          <input className="manualMMAGainSlider" type="range" min="-24" max="6" step="1" value={mmaudioGainDraftDb} onChange={(e) => setMMAudioGainDraftDb(Number(e.target.value))} />
          <div className="manualMMAGainFooter">
            <div className="manualMMAPresets">
              <button type="button" onClick={() => setMMAudioGainDraftDb(-12)}>Тихо -12</button>
              <button type="button" onClick={() => setMMAudioGainDraftDb(-6)}>Норм -6</button>
              <button type="button" onClick={() => setMMAudioGainDraftDb(0)}>Громко 0</button>
            </div>
            <button type="button" className="manualMMAButton" disabled={selectedScene.mmaudio_gain_status === "running"} onClick={applyMMAudioGain}>{selectedScene.mmaudio_gain_status === "running" ? "Применяем..." : "Применить громкость"}</button>
          </div>
          {selectedScene.mmaudio_gain_status === "error" ? <div className="manualError">Громкость MMAudio: {selectedScene.mmaudio_gain_error || "Ошибка применения"}</div> : null}
        </div> : null}
        {selectedScene.mmaudio_status === "queued" || selectedScene.mmaudio_status === "running" ? <div className="manualVideoInfo">🪄 MMAudio: {selectedScene.mmaudio_status === "queued" ? "в очереди" : "генерация звука"}</div> : null}
        {selectedScene.mmaudio_status === "error" ? <div className="manualError">MMAudio: {selectedScene.mmaudio_error || "Ошибка дозвучки"}</div> : null}
        {selectedScene.image_upload_status === "uploading" ? <div className="manualVideoInfo">Фото сохраняется на сервер...</div> : null}
        {selectedScene.image_upload_status === "extracting_last_frame" ? <div className="manualVideoInfo">Извлекаем последний кадр предыдущей сцены...</div> : null}
        {selectedScene.image_upload_status === "error" ? <div className="manualError">{selectedScene.image_upload_error || "Ошибка загрузки фото"}</div> : null}
        {isGeneratedSoundRoute(selectedScene.route) && selectedScene.video_url ? <div className="manualVideoInfo">Видео содержит сценический звук. В монтаже он будет подмешан фоном под основную музыку с выбранной громкостью.</div> : null}
        {selectedScene.route === "ia2v" ? <div className="manualVideoInfo">Lip-sync сцена: в финальном монтаже используем основной аудиотрек, звук видео можно игнорировать.</div> : null}
        {isFirstLastRoute(selectedScene.route) ? <div className="manualVideoInfo">First/last инструмент: загрузите первый и последний кадр. После генерации будет показано одно видео; после удаления видео снова появятся два окна кадров.</div> : <div className="manualVideoInfo">Для продолжения сцены можно взять последний кадр предыдущего готового видео как стартовое фото текущей сцены.</div>}
      </section> : null}

      {mmaudioModal ? <div className="manualMMAModalBackdrop" role="dialog" aria-modal="true">
        <div className="manualMMAModal">
          <h3>Дозвучить видео</h3>
          <label className="manualMMAField">
            <span>Sound prompt</span>
            <textarea value={mmaudioPrompt} onChange={(e) => setMMAudioPrompt(e.target.value)} placeholder="Realistic ocean surf sound, waves, wind, raw field recording. No music, no voice." />
          </label>
          <label className="manualMMAField">
            <span>Negative audio prompt</span>
            <textarea value={mmaudioNegativePrompt} onChange={(e) => setMMAudioNegativePrompt(e.target.value)} placeholder="music, voice, speech, singing, distorted audio" />
          </label>
          {mmaudioModalError ? <div className="manualError">{mmaudioModalError}</div> : null}
          <div className="manualMMAActions">
            <button type="button" className="clipSB_btn clipSB_btnSecondary" onClick={() => setMMAudioModal(null)}>Отмена</button>
            <button type="button" className="manualMMAButton" onClick={submitMMAudioModal}>🪄 Сгенерировать</button>
          </div>
        </div>
      </div> : null}

    </div>
  </div>;
}
