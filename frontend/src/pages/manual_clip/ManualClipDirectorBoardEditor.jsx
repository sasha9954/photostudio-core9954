import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { API_BASE, fetchJson } from "../../services/api.js";
import { buildStoryPrepTemplateText, STORY_PREP_TEMPLATE_META } from "../clip_nodes/manual/manualClipBoardDomain";
import {
  applyManualBlockStoryboardImport,
  applyManualBlockVideoPromptImport,
  buildManualBlockStoryboardContextJson,
  buildManualBlockVideoPromptContextJson,
} from "./manualBlockStoryboardDomain.js";
import {
  buildEmergencyManualClipBoardProjectForStorage,
  MANUAL_CLIP_BOARD_EMERGENCY_DOWNLOADED_ONCE_KEY,
  buildManualProjectBackupJson,
  cleanupManualClipBoardStorageAggressive,
  computeManualProjectInputSignature,
  forceWriteManualClipBoardProjectForNode,
  getLastManualClipBoardStorageError,
  getManualBoardMediaDebugStats,
  getManualBoardSceneStateDebugStats,
  getManualBoardStrictProjectIdentity,
  getManualClipBoardMaterialStats,
  getManualClipBoardSnapshotSize,
  logManualBoardMediaRefs,
  loadManualClipBoardProjectDurable,
  readLastGoodManualClipBoardProject,
  readEmergencyManualClipBoardProjectForNode,
  readCanonicalManualClipBoardProject,
  rememberLastGoodManualClipBoardProject,
  getManualProjectOwnerId,
  hasMeaningfulManualProject,
  manualBoardStrictIdentityMatches,
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
const MANUAL_BOARD_EMERGENCY_DOWNLOAD_THROTTLE_MS = 10 * 60 * 1000;
const MANUAL_BOARD_EMERGENCY_DOWNLOAD_LAST_TS_KEY = "manual_board_emergency_download_last_ts";
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


const MANUAL_SCENE_IMAGE_MEDIA_FIELDS = [
  "image_url",
  "imageUrl",
  "start_image_url",
  "startImageUrl",
  "image_preview_url",
  "imagePreviewUrl",
  "generated_image_url",
  "generatedImageUrl",
];

const MANUAL_SCENE_VIDEO_MEDIA_FIELDS = [
  "video_url",
  "videoUrl",
  "result_video_url",
  "resultVideoUrl",
  "generated_video_url",
  "generatedVideoUrl",
  "final_video_url",
  "finalVideoUrl",
];

const MANUAL_SCENE_PROTECTED_MEDIA_FIELDS = [
  ...MANUAL_SCENE_IMAGE_MEDIA_FIELDS,
  ...MANUAL_SCENE_VIDEO_MEDIA_FIELDS,
];

function isManualPollLocalOnlyReason(reason = "") {
  return /^(video_poll_running|video_poll_queued|video_poll_retry|video_poll_unknown_status)$/.test(String(reason || ""));
}

function isManualExplicitMediaLossReason(reason = "") {
  return /delete.*(video|photo|image)|remove.*(video|photo|image)|clear.*(video|photo|image)|user_delete|explicit.*reset|reset|import/i.test(String(reason || ""));
}

function getManualBoardRevision(project = {}) {
  return Number(project?.revision || project?.projectRevision || 0) || 0;
}

function projectHasManualVideoJobRunning(project = {}) {
  return (Array.isArray(project?.scenes) ? project.scenes : []).some((scene = {}) => {
    const status = String(scene?.status || scene?.video_status || scene?.videoStatus || "").trim().toLowerCase();
    return status === "video_running" || status === "running" || status === "video_queued" || status === "queued";
  });
}

function mergeManualScenePreservingMedia(currentScene = {}, patch = {}) {
  const safePatch = patch && typeof patch === "object" ? patch : {};
  const merged = { ...(currentScene || {}), ...safePatch };
  MANUAL_SCENE_PROTECTED_MEDIA_FIELDS.forEach((field) => {
    if (!Object.prototype.hasOwnProperty.call(safePatch, field)) {
      merged[field] = currentScene?.[field];
    }
  });
  return withManualSceneMediaAliases(merged);
}

function mergeManualScenePatchPreservingPromptModel(existingScene = {}, patch = {}, options = {}) {
  const allowEmptyPromptModelOverwrite = options?.allowEmptyPromptModelOverwrite === true;

  const promptModelKeys = [
    "prompt",
    "image_prompt",
    "imagePrompt",
    "photo_prompt",
    "photoPrompt",
    "source_image_prompt_en",
    "source_image_prompt_ru",
    "video_prompt",
    "videoPrompt",
    "i2v_prompt_en",
    "final_video_prompt",
    "finalVideoPrompt",
    "positive_prompt",
    "positivePrompt",
    "negative_prompt",
    "negativePrompt",
    "i2v_negative_prompt_en",
    "sound_prompt",
    "negative_audio_prompt",
    "user_prompt",
    "userPrompt",
    "custom_prompt",
    "customPrompt",
    "speech_text",
    "voice_profile",
    "voice_mode",
    "voice_language",
    "delivery_style",
    "mmaudio_prompt",
    "mmaudioPrompt",
    "mmaudio_negative_prompt",
    "mmaudioNegativePrompt",

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
    "selectedImageModel",
    "selectedVideoModel",
    "selectedGenerator",
    "selectedProvider",
    "provider",
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
  ];

  const safePatch = patch && typeof patch === "object" ? patch : {};
  const merged = {
    ...(existingScene || {}),
    ...safePatch,
  };

  if (allowEmptyPromptModelOverwrite) {
    const patchHasVideoPrompt = Object.prototype.hasOwnProperty.call(safePatch, "video_prompt");
    const patchHasNegativePrompt = Object.prototype.hasOwnProperty.call(safePatch, "negative_prompt");
    if (patchHasVideoPrompt) {
      merged.videoPrompt = safePatch.video_prompt;
      merged.i2v_prompt_en = safePatch.video_prompt;
      merged.final_video_prompt = safePatch.video_prompt;
      merged.finalVideoPrompt = safePatch.video_prompt;
      merged.positive_prompt = safePatch.video_prompt;
      merged.positivePrompt = safePatch.video_prompt;
    }
    if (patchHasNegativePrompt) {
      merged.negativePrompt = safePatch.negative_prompt;
      merged.i2v_negative_prompt_en = safePatch.negative_prompt;
      merged.videoNegativePrompt = safePatch.negative_prompt;
    }
  }

  if (!allowEmptyPromptModelOverwrite) {
    promptModelKeys.forEach((key) => {
      const existingValue = existingScene?.[key];
      const patchHasKey = Object.prototype.hasOwnProperty.call(safePatch, key);
      const patchValue = safePatch?.[key];

      const patchIsEmpty =
        patchHasKey
        && (patchValue === "" || patchValue === null || patchValue === undefined);

      const existingHasValue =
        existingValue !== "" && existingValue !== null && existingValue !== undefined;

      if (patchIsEmpty && existingHasValue) {
        merged[key] = existingValue;
      }
    });
  }

  return merged;
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

function getManualBoardAudioInfo(project = {}) {
  return {
    url: String(project?.audio?.url || project?.audio_url || project?.audioUrl || "").trim(),
    name: String(project?.audio?.name || project?.audio?.filename || project?.audio_name || "").trim(),
    duration_sec: Number(project?.audio?.duration_sec || project?.audio_duration_sec || 0) || 0,
  };
}

function normalizeManualBoardProjectAudioCompat(project = {}) {
  const audioInfo = getManualBoardAudioInfo(project);
  if (!audioInfo.url) return project;
  return {
    ...(project || {}),
    audio: {
      ...((project || {}).audio || {}),
      url: audioInfo.url,
      name: audioInfo.name,
      filename: String(project?.audio?.filename || audioInfo.name || "").trim(),
      duration_sec: audioInfo.duration_sec,
    },
    audio_url: audioInfo.url,
    audioUrl: audioInfo.url,
    audio_name: audioInfo.name,
    audio_duration_sec: audioInfo.duration_sec,
  };
}

function logManualBoardHydratePick(source, project = {}, extra = {}) {
  console.info("[MANUAL BOARD HYDRATE PICK]", {
    source,
    owner: getManualProjectOwnerId(project),
    project_id: project?.project_id || project?.projectId || "",
    input_signature: project?.input_signature || project?.inputSignature || computeManualProjectInputSignature(project),
    audio: getManualBoardAudioInfo(project),
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

function getManualBoardReloadPickDiagnostics(project = {}) {
  const timeline = getManualProjectTimelineDiagnostics(project);
  const material = getManualClipBoardMaterialStats(project);
  return {
    ...timeline,
    imageCount: material.imageCount || 0,
    videoCount: material.videoCount || 0,
    promptCount: material.promptCount || 0,
    materialScore: material.materialScore || 0,
  };
}

function hasManualBoardReloadRestorePayload(project = {}) {
  const diagnostics = getManualBoardReloadPickDiagnostics(project);
  return Boolean(
    diagnostics.audioDurationSec > 0
    && diagnostics.storyBlocksCount > 0
    && diagnostics.scenesCount > 0
    && diagnostics.scenesWithTiming > 0
  );
}

function compareManualBoardReloadRestorePriority(a, b) {
  const aDiagnostics = getManualBoardReloadPickDiagnostics(a?.project);
  const bDiagnostics = getManualBoardReloadPickDiagnostics(b?.project);
  const aHasRestorePayload = hasManualBoardReloadRestorePayload(a?.project);
  const bHasRestorePayload = hasManualBoardReloadRestorePayload(b?.project);
  if (Number(bHasRestorePayload) !== Number(aHasRestorePayload)) return Number(bHasRestorePayload) - Number(aHasRestorePayload);
  if (bDiagnostics.audioDurationSec !== aDiagnostics.audioDurationSec) return bDiagnostics.audioDurationSec - aDiagnostics.audioDurationSec;
  if (bDiagnostics.storyBlocksCount !== aDiagnostics.storyBlocksCount) return bDiagnostics.storyBlocksCount - aDiagnostics.storyBlocksCount;
  if (bDiagnostics.scenesWithTiming !== aDiagnostics.scenesWithTiming) return bDiagnostics.scenesWithTiming - aDiagnostics.scenesWithTiming;
  if (bDiagnostics.scenesCount !== aDiagnostics.scenesCount) return bDiagnostics.scenesCount - aDiagnostics.scenesCount;
  return 0;
}

function logManualBoardReloadPickDebug(candidate = null, projectOverride = null) {
  const project = projectOverride || candidate?.project || {};
  const diagnostics = getManualBoardReloadPickDiagnostics(project);
  console.info("[MANUAL BOARD RELOAD PICK DEBUG]", {
    source: candidate?.source || "unknown",
    audioDurationSec: diagnostics.audioDurationSec,
    storyBlocksCount: diagnostics.storyBlocksCount,
    scenesCount: diagnostics.scenesCount,
    scenesWithTiming: diagnostics.scenesWithTiming,
    imageCount: diagnostics.imageCount,
    promptCount: diagnostics.promptCount,
    selectedSceneId: diagnostics.selectedSceneId,
  });
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
    reloadRestore: getManualBoardReloadPickDiagnostics(project),
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
      const reloadRestorePriority = compareManualBoardReloadRestorePriority(a, b);
      if (reloadRestorePriority !== 0) return reloadRestorePriority;
      if (b.scoreData.stats.imageCount !== a.scoreData.stats.imageCount) return b.scoreData.stats.imageCount - a.scoreData.stats.imageCount;
      if (b.scoreData.stats.videoCount !== a.scoreData.stats.videoCount) return b.scoreData.stats.videoCount - a.scoreData.stats.videoCount;
      if (b.scoreData.stats.promptCount !== a.scoreData.stats.promptCount) return b.scoreData.stats.promptCount - a.scoreData.stats.promptCount;
      if (b.scoreData.stats.readyStatuses !== a.scoreData.stats.readyStatuses) return b.scoreData.stats.readyStatuses - a.scoreData.stats.readyStatuses;
      if (b.scoreData.stats.materialScore !== a.scoreData.stats.materialScore) return b.scoreData.stats.materialScore - a.scoreData.stats.materialScore;
      if (b.scoreData.updatedAt !== a.scoreData.updatedAt) return b.scoreData.updatedAt - a.scoreData.updatedAt;
      if (Number(b.openStateMatch) !== Number(a.openStateMatch)) return Number(b.openStateMatch) - Number(a.openStateMatch);
      if (b.scoreData.revision !== a.scoreData.revision) return b.scoreData.revision - a.scoreData.revision;
      if (b.scoreData.deletionRevision !== a.scoreData.deletionRevision) return b.scoreData.deletionRevision - a.scoreData.deletionRevision;
      return b.scoreData.score - a.scoreData.score;
    });
  return ranked[0] || null;
}

function mergePickedManualBoardTimelineIfNeeded(picked = null, candidates = [], reason = "hydrate_candidate_merge") {
  const pickedProject = picked?.project;
  if (!hasMeaningfulManualProject(pickedProject) || manualProjectHasTimelineStructure(pickedProject)) return pickedProject;
  const timelineCandidate = (Array.isArray(candidates) ? candidates : [])
    .filter(({ project }) => hasMeaningfulManualProject(project) && manualProjectHasTimelineStructure(project))
    .sort((a, b) => getManualProjectTimelineDiagnostics(b.project).scenesWithTiming - getManualProjectTimelineDiagnostics(a.project).scenesWithTiming)[0];
  if (!timelineCandidate?.project) return pickedProject;
  const mergedProject = preserveProjectTimelineIfIncomingEmpty(pickedProject, timelineCandidate.project, pickedProject);
  console.warn("[MANUAL BOARD HYDRATE TIMELINE MERGE]", {
    reason,
    materialSource: picked?.source || "unknown",
    timelineSource: timelineCandidate.source || "unknown",
    materialTimeline: getManualProjectTimelineDiagnostics(pickedProject),
    timelineCandidate: getManualProjectTimelineDiagnostics(timelineCandidate.project),
    mergedTimeline: getManualProjectTimelineDiagnostics(mergedProject),
  });
  return mergedProject;
}

function readManualActiveProject(sourceNodeId = "", navigationProject = null, options = {}) {
  const safeSourceNodeId = String(sourceNodeId || "").trim();
  const durableProject = options?.durableProject || null;
  const durableTried = options?.durableTried === true;
  const durableBelongsToSource =
    hasMeaningfulManualProject(durableProject)
    && (!safeSourceNodeId || projectBelongsToSource(durableProject, safeSourceNodeId));
  const forceResetBoard = options?.forceResetBoard === true;
  const explicitNewProject = options?.explicitNewProject === true;
  const hasIncomingNavigationProject = hasMeaningfulManualProject(navigationProject);
  const durableMatchesIncoming = hasIncomingNavigationProject
    ? manualBoardStrictIdentityMatches(durableProject, navigationProject)
    : true;
  let navigationProjectForCandidates = navigationProject;
  let forcePreferDurableProject = false;

  if (explicitNewProject && hasIncomingNavigationProject && durableTried && durableBelongsToSource && !durableMatchesIncoming) {
    const durableIdentity = getManualBoardStrictProjectIdentity(durableProject);
    const incomingIdentity = getManualBoardStrictProjectIdentity(navigationProject);

    console.warn("[MANUAL BOARD DURABLE REJECTED FOR NEW PROJECT: IDENTITY MISMATCH]", {
      sourceNodeId: safeSourceNodeId,
      durableIdentity,
      incomingIdentity,
    });
    console.warn("[MANUAL BOARD NEW PROJECT CLEAN START]", {
      sourceNodeId: safeSourceNodeId,
      incomingIdentity,
      rejectedDurableIdentity: durableIdentity,
    });

    return navigationProject;
  }

  if (!forceResetBoard && durableTried && durableBelongsToSource && (!explicitNewProject || durableMatchesIncoming)) {
    const durableStats = getManualClipBoardMaterialStats(durableProject);
    const navStats = getManualClipBoardMaterialStats(navigationProject);

    console.warn("[MANUAL BOARD DURABLE FOUND: PREFER OVER NAVIGATION]", {
      sourceNodeId: safeSourceNodeId,
      explicitNewProject,
      durableIdentityMatchesIncoming: durableMatchesIncoming,
      durableStats,
      navStats,
      durableProjectId: durableProject?.project_id || durableProject?.projectId || "",
      navigationProjectId: navigationProject?.project_id || navigationProject?.projectId || "",
    });

    return mergePickedManualBoardTimelineIfNeeded(
      { source: "backend-durable", project: durableProject },
      [
        { source: "backend-durable", project: durableProject },
        { source: "navigation", project: navigationProject },
      ],
      "durable_found_prefer_over_navigation"
    );
  }
  if (explicitNewProject && hasMeaningfulManualProject(navigationProject)) {
    if (!durableTried) {
      console.warn("[MANUAL BOARD EXPLICIT NEW WAITING DURABLE]", {
        sourceNodeId: safeSourceNodeId,
      });
      return null;
    }

    const durableStats = getManualClipBoardMaterialStats(durableProject);
    const navStats = getManualClipBoardMaterialStats(navigationProject);
    const durableHasAnyMedia = Boolean(
      hasMeaningfulManualProject(durableProject)
      && (
        (durableStats?.scenesWithImage || 0) > 0
        || (durableStats?.scenesWithVideo || 0) > 0
        || (durableStats?.generatedImages || 0) > 0
        || (durableStats?.generatedVideos || 0) > 0
        || (durableStats?.imageCount || 0) > 0
        || (durableStats?.videoCount || 0) > 0
      )
    );
    const navHasNoMedia = (
      (navStats?.scenesWithImage || 0) <= 0
      && (navStats?.scenesWithVideo || 0) <= 0
      && (navStats?.generatedImages || 0) <= 0
      && (navStats?.generatedVideos || 0) <= 0
      && (navStats?.imageCount || 0) <= 0
      && (navStats?.videoCount || 0) <= 0
    );
    const durableHasMoreMedia = Boolean(
      hasMeaningfulManualProject(durableProject)
      && (
        (durableStats?.scenesWithImage || durableStats?.imageCount || durableStats?.images || 0) > (navStats?.scenesWithImage || navStats?.imageCount || navStats?.images || 0)
        || (durableStats?.scenesWithVideo || durableStats?.videoCount || durableStats?.videos || 0) > (navStats?.scenesWithVideo || navStats?.videoCount || navStats?.videos || 0)
        || (durableStats?.generatedVideos || durableStats?.videoCount || durableStats?.videos || 0) > (navStats?.generatedVideos || navStats?.videoCount || navStats?.videos || 0)
        || (durableStats?.generatedImages || durableStats?.imageCount || durableStats?.images || 0) > (navStats?.generatedImages || navStats?.imageCount || navStats?.images || 0)
      )
    );

    if (durableHasAnyMedia && navHasNoMedia) {
      console.warn("[MANUAL BOARD NAVIGATION PROJECT REJECTED: WOULD DROP MEDIA]", {
        sourceNodeId: safeSourceNodeId,
        navStats,
        durableStats,
      });

      navigationProjectForCandidates = null;
      forcePreferDurableProject = true;
    } else if (!durableHasMoreMedia) {
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
        stats: navStats,
      });
      return navigationProject;
    } else {
      console.warn("[MANUAL BOARD EXPLICIT NEW IGNORED: DURABLE HAS MORE MEDIA]", {
        sourceNodeId: safeSourceNodeId,
        navStats,
        durableStats,
      });
    }
  }
  const lastGoodProject = readLastGoodManualClipBoardProject();
  const emergencyProject = readEmergencyManualClipBoardProjectForNode(safeSourceNodeId);
  const nodeProject = readManualClipBoardProjectForNode(safeSourceNodeId);
  const activeProject = readActiveManualClipBoardProject();
  const canonicalProject = readCanonicalManualClipBoardProject();
  const openState = readManualClipBoardOpenState();
  const candidates = [
    { source: "backend-durable", project: durableProject },
    { source: "session-last-good", project: lastGoodProject },
    { source: "emergency", project: emergencyProject },
    { source: "navigation", project: navigationProjectForCandidates },
    { source: "node-scoped", project: nodeProject },
    { source: "active", project: activeProject },
    { source: "canonical", project: canonicalProject },
  ];

  if (safeSourceNodeId) {
    const ownerCandidates = candidates.filter(({ source, project: candidateProject }) => {
      if (!hasMeaningfulManualProject(candidateProject)) return false;
      const sourceMatches = projectBelongsToSource(candidateProject, safeSourceNodeId);
      if (!sourceMatches) logManualBoardSkipStale(source, candidateProject, { sourceNodeId: safeSourceNodeId }, "owner_mismatch");
      return sourceMatches;
    });

    if (forcePreferDurableProject && hasMeaningfulManualProject(durableProject)) {
      console.warn("[MANUAL BOARD PICK FORCED: BACKEND DURABLE PRESERVES MEDIA]", {
        sourceNodeId: safeSourceNodeId,
        durableStats: getManualClipBoardMaterialStats(durableProject),
      });

      return mergePickedManualBoardTimelineIfNeeded(
        { source: "backend-durable", project: durableProject },
        candidates,
        "forced_backend_durable_preserve_media"
      );
    }

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
      const pickedProject = mergePickedManualBoardTimelineIfNeeded(picked, ownerCandidates, "source_bound_hydrate");
      logManualBoardReloadPickDebug(picked, pickedProject);
      logManualBoardHydratePick(picked.source, pickedProject, { sourceNodeId: safeSourceNodeId });
      return pickedProject;
    }

    console.warn("[MANUAL BOARD HYDRATE] no project for sourceNodeId", {
      sourceNodeId: safeSourceNodeId,
      lastGoodProjectExists: hasMeaningfulManualProject(lastGoodProject),
      emergencyProjectExists: hasMeaningfulManualProject(emergencyProject),
      nodeProjectExists: hasMeaningfulManualProject(nodeProject),
      activeProjectExists: hasMeaningfulManualProject(activeProject),
      canonicalProjectExists: hasMeaningfulManualProject(canonicalProject),
      activeOwner: getManualProjectOwnerId(activeProject),
      navigationProjectExists: hasMeaningfulManualProject(navigationProject),
      navigationOwner: getManualProjectOwnerId(navigationProject),
    });
    return null;
  }

  const picked = pickNewestManualBoardCandidate(candidates, openState);
  const bestProject = picked?.project || navigationProject || activeProject || nodeProject;
  let source = picked?.source || "unknown";
  if (!picked?.source) {
    if (bestProject === navigationProject) source = "navigation";
    else if (bestProject === lastGoodProject) source = "session-last-good";
    else if (bestProject === emergencyProject) source = "emergency";
    else if (bestProject === activeProject) source = "active";
    else if (bestProject === canonicalProject) source = "canonical";
    else if (bestProject === nodeProject) source = "node-scoped";
  }
  const bestProjectWithTimeline = mergePickedManualBoardTimelineIfNeeded(picked || { source, project: bestProject }, candidates, "active_hydrate");
  if (bestProjectWithTimeline) {
    logManualBoardReloadPickDebug(picked || { source, project: bestProject }, bestProjectWithTimeline);
    logManualBoardHydratePick(source, bestProjectWithTimeline);
  }
  return bestProjectWithTimeline;
}

function persistManualProject(nextProject = {}, options = {}) {
  if (!hasMeaningfulManualProject(nextProject)) return false;
  return persistManualClipBoardProject(nextProject, options);
}

function dispatchManualDirectorBoardUpdate(sourceNodeId = "", project = {}) {
  const reason = String(project?.lastPersistReason || "").trim();
  if (isManualPollLocalOnlyReason(reason)) return;
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

function getManualSceneSelectedModel(scene = {}) {
  const safeScene = scene && typeof scene === "object" ? scene : {};
  return String(
    safeScene.selectedModel
    || safeScene.selected_model
    || safeScene.generationModel
    || safeScene.generation_model
    || safeScene.providerModel
    || safeScene.provider_model
    || safeScene.modelId
    || safeScene.model_id
    || safeScene.model
    || ""
  ).trim();
}

function getManualSceneSelectedProvider(scene = {}) {
  const safeScene = scene && typeof scene === "object" ? scene : {};
  return String(
    safeScene.selectedProvider
    || safeScene.provider
    || safeScene.imageProvider
    || safeScene.videoProvider
    || ""
  ).trim();
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

async function resumeManualSceneVideoJob(jobId) {
  return fetchJson(`/api/clip/video/resume/${encodeURIComponent(jobId)}`, { method: "POST", timeoutMs: 60000 });
}

function isManualVideoInterruptedError(scene = {}) {
  const message = String(scene?.video_error || scene?.videoError || scene?.error || "").toLowerCase();
  return Boolean(
    scene?.video_job_id
    && String(scene?.status || "").toLowerCase() === "video_error"
    && (message.includes("backend_restarted") || message.includes("interrupted") || message.includes("прерван"))
  );
}

function resolveManualSceneVideoStatusEndpoint(jobId, statusEndpoint = "") {
  const rawEndpoint = String(statusEndpoint || "").trim();
  if (rawEndpoint) {
    if (rawEndpoint.startsWith("/")) return rawEndpoint;
    try {
      const parsed = new URL(rawEndpoint);
      const apiBase = new URL(API_BASE);
      if (parsed.origin === apiBase.origin) return `${parsed.pathname}${parsed.search || ""}`;
    } catch {
      // Ignore malformed statusEndpoint values and fall back to the canonical job status route.
    }
  }
  return `/api/clip/video/status/${encodeURIComponent(jobId)}`;
}

async function getManualSceneVideoStatus(jobId, statusEndpoint = "") {
  const endpoint = resolveManualSceneVideoStatusEndpoint(jobId, statusEndpoint);
  return fetchJson(endpoint, { method: "GET", timeoutMs: 60000 });
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

function readManualNestedValue(source = {}, path = "") {
  return String(path || "")
    .split(".")
    .filter(Boolean)
    .reduce((value, key) => (value && typeof value === "object" ? value[key] : undefined), source);
}

const MANUAL_STATUS_VIDEO_URL_PATHS = [
  "video_url",
  "videoUrl",
  "final_video_url",
  "finalVideoUrl",
  "result_video_url",
  "resultVideoUrl",
  "generated_video_url",
  "generatedVideoUrl",
  "output_url",
  "outputUrl",
  "asset_url",
  "assetUrl",
  "result.video_url",
  "result.videoUrl",
  "result.final_video_url",
  "result.finalVideoUrl",
  "result.result_video_url",
  "result.generated_video_url",
  "data.video_url",
  "data.final_video_url",
  "url",
];

function resolveManualVideoResultUrl(response = {}) {
  return String(
    response?.videoUrl
    || response?.video_url
    || response?.resultVideoUrl
    || response?.result_video_url
    || response?.generatedVideoUrl
    || response?.generated_video_url
    || response?.finalVideoUrl
    || response?.final_video_url
    || response?.url
    || response?.output_url
    || response?.outputUrl
    || response?.result?.videoUrl
    || response?.result?.video_url
    || response?.result?.url
    || ""
  ).trim();
}

function resolveManualStatusVideoUrl(out = {}) {
  const directResultUrl = resolveManualVideoResultUrl(out);
  if (directResultUrl) return directResultUrl;
  for (const path of MANUAL_STATUS_VIDEO_URL_PATHS) {
    const value = String(readManualNestedValue(out, path) || "").trim();
    if (value) return value;
  }
  return "";
}

function resolveManualVideoJobStatus(out = {}) {
  return String(out?.status || out?.state || out?.result?.status || out?.result?.state || out?.data?.status || out?.data?.state || "").trim().toLowerCase();
}

function collectManualResponseKeys(out = {}, prefix = "", depth = 0) {
  if (!out || typeof out !== "object" || depth > 1) return [];
  return Object.keys(out).flatMap((key) => {
    const path = prefix ? `${prefix}.${key}` : key;
    const value = out[key];
    if (value && typeof value === "object" && !Array.isArray(value)) {
      return [path, ...collectManualResponseKeys(value, path, depth + 1)];
    }
    return [path];
  });
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
  "output_url",
  "outputUrl",
  "asset_url",
  "assetUrl",
];

const MANUAL_VIDEO_CLEAR_FIELD_VALUES = {
  video_url: "",
  videoUrl: "",
  result_video_url: "",
  resultVideoUrl: "",
  generated_video_url: "",
  generatedVideoUrl: "",
  final_video_url: "",
  finalVideoUrl: "",
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
  video_job_id: "",
  videoJobId: "",
  video_status_endpoint: "",
  videoStatusEndpoint: "",
  video_status: "",
  videoStatus: "",
  video_error: "",
  videoError: "",
  video_has_audio: false,
  videoHasAudio: false,
  hasAudio: false,
  video_request_payload_preview: null,
  videoRequestPayloadPreview: null,
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
  generated_audio_policy: "",
  generatedAudioPolicy: "",
  generated_audio_gain_db: I2V_SOUND_GAIN_DEFAULT_DB,
  generatedAudioGainDb: I2V_SOUND_GAIN_DEFAULT_DB,
  keep_generated_audio: false,
  keepGeneratedAudio: false,
};

const MANUAL_IMAGE_CLEAR_FIELD_VALUES = {
  image_url: "",
  imageUrl: "",
  image_preview_url: "",
  imagePreviewUrl: "",
  start_image_url: "",
  startImageUrl: "",
  start_image_preview_url: "",
  startImagePreviewUrl: "",
  end_image_url: "",
  endImageUrl: "",
  end_image_preview_url: "",
  endImagePreviewUrl: "",
  generated_image_url: "",
  generatedImageUrl: "",
  image_upload_status: "",
  imageUploadStatus: "",
  image_upload_error: "",
  imageUploadError: "",
  image_width: 0,
  image_height: 0,
  image_aspect_ratio: 0,
  image_aspect_label: "",
};

function hasExplicitEmptyManualMediaPair(scene = {}, canonicalField = "", aliasField = "") {
  return Object.prototype.hasOwnProperty.call(scene || {}, canonicalField)
    && Object.prototype.hasOwnProperty.call(scene || {}, aliasField)
    && String(scene?.[canonicalField] || "").trim() === ""
    && String(scene?.[aliasField] || "").trim() === "";
}

function clearManualSceneVideoMediaPatch(extra = {}) {
  const now = Date.now();
  return {
    ...MANUAL_VIDEO_CLEAR_FIELD_VALUES,
    video_deleted_at: now,
    videoDeletedAt: now,
    deleted_media_revision: now,
    deletedMediaRevision: now,
    error: "",
    ...extra,
  };
}

function clearManualSceneImageMediaPatch(extra = {}) {
  const now = Date.now();
  return {
    ...MANUAL_IMAGE_CLEAR_FIELD_VALUES,
    photo_deleted_at: now,
    photoDeletedAt: now,
    deleted_media_revision: now,
    deletedMediaRevision: now,
    ...extra,
  };
}

function resolveManualSceneFinalVideoUrl(scene = {}) {
  if (hasExplicitEmptyManualMediaPair(scene, "video_url", "videoUrl")) return "";
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


function resolveManualSceneImageUrl(scene = {}) {
  if (hasExplicitEmptyManualMediaPair(scene, "image_url", "imageUrl")) return "";
  return String(
    scene?.image_url
    || scene?.imageUrl
    || scene?.start_image_url
    || scene?.startImageUrl
    || scene?.generated_image_url
    || scene?.generatedImageUrl
    || ""
  ).trim();
}

function resolveManualSceneImagePreviewUrl(scene = {}) {
  return String(
    scene?.image_preview_url
    || scene?.imagePreviewUrl
    || scene?.start_image_preview_url
    || scene?.startImagePreviewUrl
    || resolveManualSceneImageUrl(scene)
    || ""
  ).trim();
}

function withManualSceneMediaAliases(scene = {}) {
  const imageUrl = resolveManualSceneImageUrl(scene);
  const imagePreviewUrl = resolveManualSceneImagePreviewUrl(scene);
  const startImageUrl = String(scene?.start_image_url || scene?.startImageUrl || imageUrl || "").trim();
  const startImagePreviewUrl = String(scene?.start_image_preview_url || scene?.startImagePreviewUrl || imagePreviewUrl || "").trim();
  const endImageUrl = String(scene?.end_image_url || scene?.endImageUrl || "").trim();
  const endImagePreviewUrl = String(scene?.end_image_preview_url || scene?.endImagePreviewUrl || endImageUrl || "").trim();
  const generatedImageUrl = String(scene?.generated_image_url || scene?.generatedImageUrl || imageUrl || "").trim();
  const videoUrl = resolveManualSceneFinalVideoUrl(scene);
  const hasExplicitVideoClear = hasExplicitEmptyManualMediaPair(scene, "video_url", "videoUrl");
  const videoJobId = hasExplicitVideoClear ? "" : String(scene?.video_job_id || scene?.videoJobId || "").trim();
  const videoRequestPayloadPreview = hasExplicitVideoClear ? null : (scene?.video_request_payload_preview ?? scene?.videoRequestPayloadPreview ?? null);
  return {
    ...(scene || {}),
    image_url: imageUrl,
    imageUrl,
    image_preview_url: imagePreviewUrl,
    imagePreviewUrl: imagePreviewUrl,
    start_image_url: startImageUrl,
    startImageUrl,
    start_image_preview_url: startImagePreviewUrl,
    startImagePreviewUrl,
    end_image_url: endImageUrl,
    endImageUrl,
    end_image_preview_url: endImagePreviewUrl,
    endImagePreviewUrl,
    generated_image_url: generatedImageUrl,
    generatedImageUrl: generatedImageUrl,
    image_upload_status: String(scene?.image_upload_status || scene?.imageUploadStatus || ""),
    imageUploadStatus: String(scene?.imageUploadStatus || scene?.image_upload_status || ""),
    image_upload_error: String(scene?.image_upload_error || scene?.imageUploadError || ""),
    imageUploadError: String(scene?.imageUploadError || scene?.image_upload_error || ""),
    video_url: videoUrl,
    videoUrl,
    result_video_url: hasExplicitVideoClear ? "" : String(scene?.result_video_url || scene?.resultVideoUrl || videoUrl || "").trim(),
    resultVideoUrl: hasExplicitVideoClear ? "" : String(scene?.resultVideoUrl || scene?.result_video_url || videoUrl || "").trim(),
    generated_video_url: hasExplicitVideoClear ? "" : String(scene?.generated_video_url || scene?.generatedVideoUrl || videoUrl || "").trim(),
    generatedVideoUrl: hasExplicitVideoClear ? "" : String(scene?.generatedVideoUrl || scene?.generated_video_url || videoUrl || "").trim(),
    final_video_url: hasExplicitVideoClear ? "" : String(scene?.final_video_url || scene?.finalVideoUrl || videoUrl || "").trim(),
    finalVideoUrl: hasExplicitVideoClear ? "" : String(scene?.finalVideoUrl || scene?.final_video_url || videoUrl || "").trim(),
    video_job_id: videoJobId,
    videoJobId: videoJobId,
    video_has_audio: Boolean(scene?.video_has_audio ?? scene?.videoHasAudio ?? scene?.hasAudio ?? false),
    videoHasAudio: Boolean(scene?.videoHasAudio ?? scene?.video_has_audio ?? scene?.hasAudio ?? false),
    hasAudio: Boolean(scene?.hasAudio ?? scene?.video_has_audio ?? scene?.videoHasAudio ?? false),
    video_request_payload_preview: videoRequestPayloadPreview,
    videoRequestPayloadPreview: videoRequestPayloadPreview,
  };
}

function getManualBoardMediaDiagnostics(project = {}, selectedSceneId = "") {
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const safeSelectedSceneId = String(selectedSceneId || project?.selectedSceneId || "").trim();
  const selectedScene = scenes.find((scene) => String(scene?.scene_id || scene?.id || "") === safeSelectedSceneId) || scenes[0] || {};
  return {
    selectedSceneId: String(selectedScene?.scene_id || safeSelectedSceneId || ""),
    selectedSceneImageUrl: resolveManualSceneImageUrl(selectedScene),
    selectedScenePreviewUrl: resolveManualSceneImagePreviewUrl(selectedScene),
    selectedSceneVideoUrl: resolveManualSceneFinalVideoUrl(selectedScene),
    scenesWithImage: scenes.filter((scene) => resolveManualSceneImageUrl(scene) || resolveManualSceneImagePreviewUrl(scene)).length,
    scenesWithVideo: scenes.filter((scene) => resolveManualSceneFinalVideoUrl(scene)).length,
  };
}


function buildManualBoardAutosaveSignature(project = {}) {
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const audioInfo = getManualBoardAudioInfo(project);
  return JSON.stringify({
    project_id: manualBoardProjectId(project),
    projectId: String(project?.projectId || "").trim(),
    input_signature: manualBoardInputSignature(project),
    inputSignature: String(project?.inputSignature || "").trim(),
    audio_signature: String(project?.audio_signature || project?.audioSignature || "").trim(),
    sourceNodeId: String(project?.sourceNodeId || project?.ownerNodeId || project?.nodeId || "").trim(),
    selectedSceneId: String(project?.selectedSceneId || "").trim(),
    revision: Number(project?.revision || 0) || 0,
    updatedAt: Number(project?.updatedAt || 0) || 0,
    route: String(project?.route || project?.render_route || "").trim(),
    notes: String(project?.notes || project?.user_note_ru || project?.director_notes || ""),
    audio: {
      ...audioInfo,
      signature: String(project?.audio_signature || project?.audioSignature || "").trim(),
      phrases: Array.isArray(project?.audio_phrases) ? project.audio_phrases.length : 0,
    },
    scenes: scenes.map((scene = {}) => ({
      scene_id: String(scene?.scene_id || scene?.id || "").trim(),
      route: String(scene?.route || "").trim(),
      status: String(scene?.status || "").trim(),
      prompt: String(scene?.video_prompt || scene?.videoPrompt || ""),
      negative: String(scene?.negative_prompt || scene?.negativePrompt || scene?.videoNegativePrompt || ""),
      sound: String(scene?.sound_prompt || scene?.soundPrompt || ""),
      note: String(scene?.user_note_ru || scene?.note || scene?.notes || scene?.short_note || ""),
      image: resolveManualSceneImageUrl(scene),
      imagePreview: resolveManualSceneImagePreviewUrl(scene),
      startImage: String(scene?.start_image_url || scene?.startImageUrl || "").trim(),
      endImage: String(scene?.end_image_url || scene?.endImageUrl || "").trim(),
      video: resolveManualSceneFinalVideoUrl(scene),
      videoJobId: String(scene?.video_job_id || scene?.videoJobId || "").trim(),
      videoError: String(scene?.video_error || scene?.error || ""),
      videoHasAudio: Boolean(scene?.video_has_audio || scene?.videoHasAudio || scene?.hasAudio),
      audioSlice: String(scene?.audio_slice_url || scene?.audioSliceUrl || "").trim(),
      audioStart: Number(scene?.audio_slice_start_sec ?? scene?.audioSliceStartSec ?? scene?.start_sec ?? 0) || 0,
      audioEnd: Number(scene?.audio_slice_end_sec ?? scene?.audioSliceEndSec ?? scene?.end_sec ?? 0) || 0,
      mmaudioStatus: String(scene?.mmaudio_status || scene?.mmaudioStatus || "").trim(),
      mmaudioUrl: String(scene?.mmaudio_url || scene?.mmaudioUrl || scene?.mmaudio_video_url || "").trim(),
    })),
  });
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


function preserveManualScenePromptAndModelFields(normalized = {}, rawScene = {}) {
  const promptModelKeys = [
    "prompt",
    "image_prompt",
    "imagePrompt",
    "photo_prompt",
    "photoPrompt",
    "source_image_prompt_en",
    "source_image_prompt_ru",
    "video_prompt",
    "videoPrompt",
    "i2v_prompt_en",
    "final_video_prompt",
    "finalVideoPrompt",
    "positive_prompt",
    "positivePrompt",
    "negative_prompt",
    "negativePrompt",
    "i2v_negative_prompt_en",
    "sound_prompt",
    "negative_audio_prompt",
    "user_prompt",
    "userPrompt",
    "custom_prompt",
    "customPrompt",
    "speech_text",
    "voice_profile",
    "voice_mode",
    "voice_language",
    "delivery_style",
    "mmaudio_prompt",
    "mmaudioPrompt",
    "mmaudio_negative_prompt",
    "mmaudioNegativePrompt",
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
    "selectedImageModel",
    "selectedVideoModel",
    "selectedGenerator",
    "selectedProvider",
    "provider",
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
  ];

  const out = { ...normalized };
  promptModelKeys.forEach((key) => {
    if ((out[key] === undefined || out[key] === null || out[key] === "") && rawScene[key] !== undefined && rawScene[key] !== null) {
      out[key] = rawScene[key];
    }
  });
  return out;
}

function normalizeScene(scene = {}, idx = 0, storyBlockLookup = null, project = {}) {
  const rawScene = scene && typeof scene === "object" ? scene : {};
  const cleanInputScene = stripBlockedManualSceneDataUrls(rawScene, project, idx);
  const start = Number(cleanInputScene.start_sec || 0);
  const end = Number(cleanInputScene.end_sec || start);
  const blockId = String(cleanInputScene.story_block_id || "").trim();
  const block = blockId && storyBlockLookup?.get ? storyBlockLookup.get(blockId) : null;
  const normalizedScene = {
    ...cleanInputScene,
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
    image_aspect_ratio: Number(cleanInputScene.image_aspect_ratio || cleanInputScene.imageAspectRatio || 0),
    image_aspect_label: String(cleanInputScene.image_aspect_label || cleanInputScene.imageAspectLabel || ""),
    image_url: String(cleanInputScene.image_url || cleanInputScene.imageUrl || cleanInputScene.start_image_url || cleanInputScene.startImageUrl || ""),
    imageUrl: String(cleanInputScene.imageUrl || cleanInputScene.image_url || cleanInputScene.startImageUrl || cleanInputScene.start_image_url || ""),
    start_image_url: String(cleanInputScene.start_image_url || cleanInputScene.startImageUrl || cleanInputScene.image_url || cleanInputScene.imageUrl || ""),
    startImageUrl: String(cleanInputScene.startImageUrl || cleanInputScene.start_image_url || cleanInputScene.imageUrl || cleanInputScene.image_url || ""),
    end_image_url: String(cleanInputScene.end_image_url || cleanInputScene.endImageUrl || ""),
    endImageUrl: String(cleanInputScene.endImageUrl || cleanInputScene.end_image_url || ""),
    image_preview_url: String(cleanInputScene.image_preview_url || cleanInputScene.imagePreviewUrl || cleanInputScene.start_image_preview_url || cleanInputScene.startImagePreviewUrl || ""),
    imagePreviewUrl: String(cleanInputScene.imagePreviewUrl || cleanInputScene.image_preview_url || cleanInputScene.startImagePreviewUrl || cleanInputScene.start_image_preview_url || ""),
    start_image_preview_url: String(cleanInputScene.start_image_preview_url || cleanInputScene.startImagePreviewUrl || cleanInputScene.image_preview_url || cleanInputScene.imagePreviewUrl || ""),
    startImagePreviewUrl: String(cleanInputScene.startImagePreviewUrl || cleanInputScene.start_image_preview_url || cleanInputScene.imagePreviewUrl || cleanInputScene.image_preview_url || ""),
    end_image_preview_url: String(cleanInputScene.end_image_preview_url || cleanInputScene.endImagePreviewUrl || ""),
    endImagePreviewUrl: String(cleanInputScene.endImagePreviewUrl || cleanInputScene.end_image_preview_url || ""),
    generated_image_url: String(cleanInputScene.generated_image_url || cleanInputScene.generatedImageUrl || cleanInputScene.image_url || cleanInputScene.imageUrl || ""),
    generatedImageUrl: String(cleanInputScene.generatedImageUrl || cleanInputScene.generated_image_url || cleanInputScene.imageUrl || cleanInputScene.image_url || ""),
    image_upload_status: String(cleanInputScene.image_upload_status || cleanInputScene.imageUploadStatus || ""),
    imageUploadStatus: String(cleanInputScene.imageUploadStatus || cleanInputScene.image_upload_status || ""),
    image_upload_error: String(cleanInputScene.image_upload_error || cleanInputScene.imageUploadError || ""),
    imageUploadError: String(cleanInputScene.imageUploadError || cleanInputScene.image_upload_error || ""),
    video_url: String(cleanInputScene.video_url || cleanInputScene.videoUrl || cleanInputScene.result_video_url || cleanInputScene.resultVideoUrl || cleanInputScene.generated_video_url || cleanInputScene.generatedVideoUrl || ""),
    videoUrl: String(cleanInputScene.videoUrl || cleanInputScene.video_url || cleanInputScene.resultVideoUrl || cleanInputScene.result_video_url || cleanInputScene.generatedVideoUrl || cleanInputScene.generated_video_url || ""),
    result_video_url: String(cleanInputScene.result_video_url || cleanInputScene.resultVideoUrl || cleanInputScene.video_url || cleanInputScene.videoUrl || ""),
    resultVideoUrl: String(cleanInputScene.resultVideoUrl || cleanInputScene.result_video_url || cleanInputScene.videoUrl || cleanInputScene.video_url || ""),
    generated_video_url: String(cleanInputScene.generated_video_url || cleanInputScene.generatedVideoUrl || cleanInputScene.video_url || cleanInputScene.videoUrl || ""),
    generatedVideoUrl: String(cleanInputScene.generatedVideoUrl || cleanInputScene.generated_video_url || cleanInputScene.videoUrl || cleanInputScene.video_url || ""),
    audio_slice_url: String(cleanInputScene.audio_slice_url || ""),
    audio_slice_duration_sec: Number(cleanInputScene.audio_slice_duration_sec || 0),
    status: String(cleanInputScene.status || "draft"),
    error: String(cleanInputScene.error || ""),
    audio_extracted: Boolean(cleanInputScene.audio_extracted),
    video_job_id: String(cleanInputScene.video_job_id || cleanInputScene.videoJobId || ""),
    videoJobId: String(cleanInputScene.videoJobId || cleanInputScene.video_job_id || ""),
    video_error: String(cleanInputScene.video_error || cleanInputScene.videoError || ""),
    videoError: String(cleanInputScene.videoError || cleanInputScene.video_error || ""),
    video_has_audio: Boolean(cleanInputScene.video_has_audio ?? cleanInputScene.videoHasAudio ?? cleanInputScene.hasAudio),
    videoHasAudio: Boolean(cleanInputScene.videoHasAudio ?? cleanInputScene.video_has_audio ?? cleanInputScene.hasAudio),
    hasAudio: Boolean(cleanInputScene.hasAudio ?? cleanInputScene.video_has_audio ?? cleanInputScene.videoHasAudio),
    generated_audio_policy: String(cleanInputScene.generated_audio_policy || ""),
    generated_audio_gain_db: Number(cleanInputScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
    keep_generated_audio: Boolean(cleanInputScene.keep_generated_audio),
    video_request_payload_preview: cleanInputScene.video_request_payload_preview || cleanInputScene.videoRequestPayloadPreview || null,
    videoRequestPayloadPreview: cleanInputScene.videoRequestPayloadPreview || cleanInputScene.video_request_payload_preview || null,
  };
  return preserveManualScenePromptAndModelFields(withManualSceneMediaAliases(normalizedScene), cleanInputScene);
}

const IMPORT_EMPTY_PROTECTED_SCENE_FIELDS = [
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
  "image_height",
  "image_aspect_ratio",
  "image_aspect_label",
  "image_upload_status",
  "imageUploadStatus",
  "image_upload_error",
  "imageUploadError",
  "video_url",
  "videoUrl",
  "result_video_url",
  "resultVideoUrl",
  "generated_video_url",
  "generatedVideoUrl",
  "final_video_url",
  "finalVideoUrl",
  "output_video_url",
  "outputVideoUrl",
  "video_status",
  "videoStatus",
  "video_job_id",
  "videoJobId",
  "video_has_audio",
  "videoHasAudio",
  "hasAudio",
  "status",
  "audio_slice_url",
  "audio_slice_duration_sec",
  "audio_extracted",
  "video_error",
  "generated_audio_policy",
  "generatedAudioPolicy",
  "generated_audio_gain_db",
  "generatedAudioGainDb",
  "keep_generated_audio",
  "keepGeneratedAudio",
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

function firstImportedSceneNumber(...values) {
  for (const value of values) {
    if (value === null || value === undefined || value === "") continue;
    const numeric = Number(value);
    if (Number.isFinite(numeric)) return numeric;
  }
  return 0;
}

function getImportedSceneTimingValues(scene = {}) {
  const safeScene = scene && typeof scene === "object" ? scene : {};
  const safeTiming = safeScene.timing && typeof safeScene.timing === "object" ? safeScene.timing : {};
  const topTiming = {
    start_sec: firstImportedSceneNumber(safeScene.start_sec, safeScene.startSec),
    end_sec: firstImportedSceneNumber(safeScene.end_sec, safeScene.endSec),
    duration_sec: firstImportedSceneNumber(safeScene.duration_sec, safeScene.durationSec),
    speech_start_sec: firstImportedSceneNumber(safeScene.speech_start_sec, safeScene.speechStartSec),
    speech_end_sec: firstImportedSceneNumber(safeScene.speech_end_sec, safeScene.speechEndSec),
  };
  const nestedTiming = {
    start_sec: firstImportedSceneNumber(safeTiming.start_sec, safeTiming.startSec),
    end_sec: firstImportedSceneNumber(safeTiming.end_sec, safeTiming.endSec),
    duration_sec: firstImportedSceneNumber(safeTiming.duration_sec, safeTiming.durationSec),
    speech_start_sec: firstImportedSceneNumber(safeTiming.speech_start_sec, safeTiming.speechStartSec),
    speech_end_sec: firstImportedSceneNumber(safeTiming.speech_end_sec, safeTiming.speechEndSec),
  };
  const topHasTiming = topTiming.end_sec > 0 || topTiming.duration_sec > 0;
  const nestedHasTiming = nestedTiming.end_sec > 0 || nestedTiming.duration_sec > 0;
  const timing = topHasTiming || !nestedHasTiming ? topTiming : nestedTiming;
  return {
    ...timing,
    duration_sec: timing.duration_sec > 0 ? timing.duration_sec : Math.max(0, timing.end_sec - timing.start_sec),
  };
}

function importedSceneHasRealTiming(scene = {}) {
  const timing = getImportedSceneTimingValues(scene);
  return timing.end_sec > 0 || timing.duration_sec > 0;
}

const MANUAL_PROJECT_TIMELINE_FIELDS = [
  "audio",
  "audio_metadata",
  "audio_duration_sec",
  "markers",
  "story_blocks",
  "audio_phrases",
  "speakers",
  "topic_blocks",
  "song_blocks",
  "timing_status",
];

const MANUAL_SCENE_TIMELINE_FIELDS = [
  "start_sec",
  "end_sec",
  "duration_sec",
  "speech_start_sec",
  "speech_end_sec",
  "story_block_id",
  "story_block_title_ru",
  "story_block_position_ru",
  "source_phrase_ids",
  "audio_slice_url",
  "audio_slice_duration_sec",
  "translated_text_ru",
  "meaning_hint_ru",
  "scene_goal_ru",
  "scene_role_in_block_ru",
  "block_progress_ru",
];

function getManualProjectTimelineDiagnostics(project = {}) {
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const scenesWithTiming = scenes.filter((scene) => importedSceneHasRealTiming(scene)).length;
  return {
    project_id: project?.project_id || project?.projectId || "",
    audioDurationSec: Number(project?.audio_duration_sec || project?.audio?.duration_sec || project?.audioMetadata?.duration_sec || project?.audio_metadata?.duration_sec || 0) || 0,
    markersCount: Array.isArray(project?.markers) ? project.markers.length : 0,
    storyBlocksCount: Array.isArray(project?.story_blocks) ? project.story_blocks.length : 0,
    audioPhrasesCount: Array.isArray(project?.audio_phrases) ? project.audio_phrases.length : 0,
    speakersCount: Array.isArray(project?.speakers) ? project.speakers.length : 0,
    topicBlocksCount: Array.isArray(project?.topic_blocks) ? project.topic_blocks.length : 0,
    songBlocksCount: Array.isArray(project?.song_blocks) ? project.song_blocks.length : 0,
    scenesCount: scenes.length,
    scenesWithTiming,
    scenesWithZeroTiming: Math.max(0, scenes.length - scenesWithTiming),
    selectedSceneId: String(project?.selectedSceneId || "").trim(),
  };
}

function manualProjectHasTimelineStructure(project = {}) {
  const diagnostics = getManualProjectTimelineDiagnostics(project);
  return Boolean(
    diagnostics.audioDurationSec > 0
    || diagnostics.markersCount > 0
    || diagnostics.storyBlocksCount > 0
    || diagnostics.audioPhrasesCount > 0
    || diagnostics.speakersCount > 0
    || diagnostics.topicBlocksCount > 0
    || diagnostics.songBlocksCount > 0
    || diagnostics.scenesWithTiming > 0
    || String(project?.timing_status || "").trim()
  );
}

function manualProjectIncomingTimelineIsEmpty(project = {}) {
  return !manualProjectHasTimelineStructure(project);
}

function shouldPreserveTimelineValue(currentValue, incomingValue) {
  if (Array.isArray(currentValue)) return currentValue.length > 0 && (!Array.isArray(incomingValue) || incomingValue.length === 0);
  if (currentValue && typeof currentValue === "object") {
    if (!incomingValue || typeof incomingValue !== "object") return Object.keys(currentValue).length > 0;
    const currentMeaningful = Object.values(currentValue).some((value) => (typeof value === "number" ? value > 0 : String(value ?? "").trim() !== ""));
    const incomingMeaningful = Object.values(incomingValue).some((value) => (typeof value === "number" ? value > 0 : String(value ?? "").trim() !== ""));
    return currentMeaningful && !incomingMeaningful;
  }
  if (typeof currentValue === "number") return currentValue > 0 && Number(incomingValue || 0) <= 0;
  return String(currentValue ?? "").trim() !== "" && String(incomingValue ?? "").trim() === "";
}

function preserveSceneTimelineFieldsIfIncomingEmpty(nextScene = {}, currentScene = {}, incomingScene = {}) {
  if (!importedSceneHasRealTiming(currentScene) || importedSceneHasRealTiming(incomingScene)) return nextScene;
  const preservedScene = { ...(nextScene || {}) };
  MANUAL_SCENE_TIMELINE_FIELDS.forEach((field) => {
    if (shouldPreserveTimelineValue(currentScene?.[field], incomingScene?.[field])) {
      preservedScene[field] = currentScene[field];
    }
  });
  const timing = getImportedSceneTimingValues(currentScene);
  preservedScene.start_sec = timing.start_sec;
  preservedScene.end_sec = timing.end_sec;
  preservedScene.duration_sec = timing.duration_sec;
  preservedScene.speech_start_sec = timing.speech_start_sec;
  preservedScene.speech_end_sec = timing.speech_end_sec;
  preservedScene.timing = {
    ...((nextScene?.timing && typeof nextScene.timing === "object") ? nextScene.timing : {}),
    ...timing,
  };
  return preservedScene;
}

function preserveProjectTimelineIfIncomingEmpty(nextProject = {}, currentProject = {}, incomingProject = {}) {
  if (!manualProjectHasTimelineStructure(currentProject) || !manualProjectIncomingTimelineIsEmpty(incomingProject)) return nextProject || {};
  const preservedProject = { ...(nextProject || {}) };
  MANUAL_PROJECT_TIMELINE_FIELDS.forEach((field) => {
    if (shouldPreserveTimelineValue(currentProject?.[field], incomingProject?.[field])) {
      preservedProject[field] = currentProject[field];
    }
  });

  const currentScenes = Array.isArray(currentProject?.scenes) ? currentProject.scenes : [];
  const nextScenes = Array.isArray(preservedProject?.scenes) ? preservedProject.scenes : [];
  const incomingScenes = Array.isArray(incomingProject?.scenes) ? incomingProject.scenes : [];
  const currentById = new Map(currentScenes.map((scene, idx) => [String(scene?.scene_id || scene?.id || `seg_${String(idx + 1).padStart(2, "0")}`).trim(), scene]));
  const incomingById = new Map(incomingScenes.map((scene, idx) => [String(scene?.scene_id || scene?.id || `seg_${String(idx + 1).padStart(2, "0")}`).trim(), scene]));
  preservedProject.scenes = nextScenes.map((scene, idx) => {
    const sceneId = String(scene?.scene_id || scene?.id || `seg_${String(idx + 1).padStart(2, "0")}`).trim();
    return preserveSceneTimelineFieldsIfIncomingEmpty(scene, currentById.get(sceneId) || currentScenes[idx] || {}, incomingById.get(sceneId) || incomingScenes[idx] || {});
  });
  return preservedProject;
}

function logManualBoardRuntimeTimelineDebug(reason = "manual_director_persist", project = {}, source = "before_persist") {
  console.info("[MANUAL BOARD RUNTIME TIMELINE DEBUG]", {
    reason,
    ...getManualProjectTimelineDiagnostics(project),
    source,
  });
}

function logManualBoardRuntimeTimelineRegression(reason = "manual_director_persist", currentProject = {}, nextProject = {}) {
  console.warn("[MANUAL BOARD RUNTIME TIMELINE REGRESSION]", {
    reason,
    currentTimeline: getManualProjectTimelineDiagnostics(currentProject),
    nextTimeline: getManualProjectTimelineDiagnostics(nextProject),
    action: "merged_current_timeline_into_next",
  });
}

function preserveCurrentSceneTimelineIfIncomingIsEmpty(nextScene = {}, currentScene = {}, incomingScene = {}) {
  return preserveSceneTimelineFieldsIfIncomingEmpty(nextScene, currentScene, incomingScene);
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

    return preserveCurrentSceneTimelineIfIncomingIsEmpty(nextScene, currentScene, incomingScene);
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
  const autosaveTimerRef = useRef(null);
  const lastAutosaveSignatureRef = useRef("");
  const lastPersistedProjectRef = useRef(null);
  const manualBoardDirtyRef = useRef(false);
  const lastGoodBoardRef = useRef(null);
  const emergencyBackupDownloadedRef = useRef(false);
  const lastEmergencyAutoDownloadAtRef = useRef(0);
  const quickListenAudioRef = useRef(null);
  const quickListenRafRef = useRef(null);
  const playbackRangeRef = useRef({ startSec: 0, endSec: null });
  const didHydrateRef = useRef(false);
  const consumedExplicitNewProjectRef = useRef(false);
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
  const [autosaveStatus, setAutosaveStatus] = useState("Сохранено");
  const [autosaveError, setAutosaveError] = useState("");
  const [showEmergencyBackupButton, setShowEmergencyBackupButton] = useState(false);
  const [durableBoardLoad, setDurableBoardLoad] = useState({ nodeId: "", loading: false, project: null, tried: false });

  const isManualTimingProjectSource = (candidateProject = projectRef.current || project || {}) => {
    const ownerNodeType = String(candidateProject?.ownerNodeType || location.state?.ownerNodeType || "").trim().toLowerCase();
    const source = String(candidateProject?.source || location.state?.source || "").trim().toLowerCase();
    return ownerNodeType === "manualtiming" || source === "manual_timing_node";
  };

  const isCriticalEmergencyDownloadReason = (reason = "") => (
    /^(before_unload|route_leave|manual_force_save_button_failed|video_done_force_persist_failed)$/.test(String(reason || ""))
  );

  const canAutoDownloadEmergencyBackup = (reason = "") => {
    if (!isCriticalEmergencyDownloadReason(reason)) return false;
    try {
      if (sessionStorage.getItem(MANUAL_CLIP_BOARD_EMERGENCY_DOWNLOADED_ONCE_KEY) === "true") return false;
    } catch {}
    const now = Date.now();
    const storedLastDownloadAt = (() => {
      try { return Number(sessionStorage.getItem(MANUAL_BOARD_EMERGENCY_DOWNLOAD_LAST_TS_KEY) || 0) || 0; } catch { return 0; }
    })();
    const lastDownloadAt = Math.max(lastEmergencyAutoDownloadAtRef.current || 0, storedLastDownloadAt);
    return !lastDownloadAt || now - lastDownloadAt >= MANUAL_BOARD_EMERGENCY_DOWNLOAD_THROTTLE_MS;
  };

  const markEmergencyBackupAutoDownloaded = () => {
    const now = Date.now();
    emergencyBackupDownloadedRef.current = true;
    lastEmergencyAutoDownloadAtRef.current = now;
    try {
      sessionStorage.setItem(MANUAL_CLIP_BOARD_EMERGENCY_DOWNLOADED_ONCE_KEY, "true");
      sessionStorage.setItem(MANUAL_BOARD_EMERGENCY_DOWNLOAD_LAST_TS_KEY, String(now));
    } catch {}
  };

  const maybeAutoDownloadEmergencyBackup = (reason = "") => {
    if (!canAutoDownloadEmergencyBackup(reason)) return false;
    const downloaded = downloadEmergencyBoardBackup(reason);
    if (downloaded !== false) markEmergencyBackupAutoDownloaded();
    return downloaded !== false;
  };

  const getManualBoardForceState = (candidateProject = projectRef.current || project || {}) => ({
    manualBoardForceProjectId: String(candidateProject?.project_id || candidateProject?.projectId || "").trim(),
    manualBoardForceInputSignature: String(candidateProject?.input_signature || candidateProject?.inputSignature || "").trim(),
    manualBoardForceAudioSignature: String(candidateProject?.audio_signature || candidateProject?.audioSignature || "").trim(),
  });

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
        ? candidateProject.scenes.map((scene) => withManualSceneMediaAliases({ ...scene, format: projectFormat, aspect_ratio: projectFormat }))
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
    let safeProject = normalizeDirectorProjectOwner(candidateProject);
    const persistReason = options?.reason || safeProject.lastPersistReason || "manual_director_persist";
    if (isManualPollLocalOnlyReason(persistReason)) return false;
    const timelineBaseline = projectRef.current || lastGoodBoardRef.current || lastPersistedProjectRef.current || readManualClipBoardProjectForNode(ownerNodeId);
    const beforeTimeline = getManualProjectTimelineDiagnostics(safeProject);
    safeProject = preserveProjectTimelineIfIncomingEmpty(safeProject, timelineBaseline, candidateProject);
    if (beforeTimeline.scenesWithTiming === 0 && getManualProjectTimelineDiagnostics(timelineBaseline).scenesWithTiming > 0) {
      logManualBoardRuntimeTimelineRegression(persistReason, timelineBaseline, safeProject);
    }
    logManualBoardRuntimeTimelineDebug(persistReason, safeProject, "before_persist");
    const persistOptions = { ...(options || {}), reason: persistReason, embedded };

    if (embedded && typeof onProjectChange === "function") {
      const persisted = persistManualProject(safeProject, persistOptions);
      if (!persisted) return false;
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

    if (persisted && readbackOk) dispatchManualDirectorBoardUpdate(ownerNodeId, safeProject);
    return Boolean(persisted && readbackOk);
  };

  useEffect(() => {
    const safeSourceNodeId = String(sourceNodeIdFromRoute || "").trim();
    if (!safeSourceNodeId) {
      setDurableBoardLoad({ nodeId: "", loading: false, project: null, tried: true });
      return;
    }
    let cancelled = false;
    setDurableBoardLoad((prev) => ({
      nodeId: safeSourceNodeId,
      loading: prev.nodeId === safeSourceNodeId && prev.tried ? false : true,
      project: prev.nodeId === safeSourceNodeId ? prev.project : null,
      tried: prev.nodeId === safeSourceNodeId ? prev.tried : false,
    }));
    loadManualClipBoardProjectDurable(safeSourceNodeId)
      .then((durableProject) => {
        if (cancelled) return;
        setDurableBoardLoad({ nodeId: safeSourceNodeId, loading: false, project: durableProject, tried: true });
      })
      .catch((error) => {
        if (cancelled) return;
        console.warn("[manual board durable hydrate] backend load failed; falling back to browser storage", {
          sourceNodeId: safeSourceNodeId,
          errorName: error?.name,
          errorMessage: error?.message,
        });
        setDurableBoardLoad({ nodeId: safeSourceNodeId, loading: false, project: null, tried: true });
      });
    return () => { cancelled = true; };
  }, [sourceNodeIdFromRoute]);

  useEffect(() => {
    const navigationProject = location.state?.director_board || location.state?.project || null;
    const openState = readManualClipBoardOpenState();
    const rawExplicitNewProject = Boolean(
      manualBoardExplicitNewProject
      || location.state?.manualBoardExplicitNewProject === true
      || openState?.manualBoardExplicitNewProject === true
      || ["manual_new_project_from_audio_split", "manual_new_project_from_audio_split_open_embedded"].includes(String((embeddedProject || navigationProject)?.lastPersistReason || ""))
    );
    const explicitNewProject = rawExplicitNewProject && consumedExplicitNewProjectRef.current !== true;
    const hasFreshNavigationProject = hasMeaningfulManualProject(navigationProject || embeddedProject);
    const freshNavigationCandidate = embeddedProject || navigationProject;
    const shouldBypassDurableForFreshNewProject = Boolean(
      explicitNewProject
      && hasFreshNavigationProject
      && String(freshNavigationCandidate?.lastPersistReason || "").includes("manual_new_project_from_audio_split")
    );
    if (sourceNodeIdFromRoute && durableBoardLoad.nodeId !== sourceNodeIdFromRoute) return;
    if (sourceNodeIdFromRoute && durableBoardLoad.loading) return;
    const durableProject = durableBoardLoad.nodeId === sourceNodeIdFromRoute ? durableBoardLoad.project : null;
    const hydrationNavigationProject = embedded ? embeddedProject : navigationProject;
    const forceResetBoard = Boolean(
      location.state?.forceResetBoard === true
      || location.state?.manualBoardForceReset === true
      || openState?.forceResetBoard === true
    );
    const currentProject = projectRef.current;
    const alreadyHydrated = didHydrateRef.current === true;
    const currentHasMeaningfulProject = hasMeaningfulManualProject(currentProject);

    const currentIdentity = currentHasMeaningfulProject
      ? getManualBoardStrictProjectIdentity(currentProject)
      : null;


    const isSourceNodeChange = Boolean(
      currentIdentity?.sourceNodeId
      && sourceNodeIdFromRoute
      && currentIdentity.sourceNodeId !== sourceNodeIdFromRoute
    );

    if (
      alreadyHydrated
      && currentHasMeaningfulProject
      && !explicitNewProject
      && !forceResetBoard
      && !isSourceNodeChange
    ) {
      console.warn("[MANUAL BOARD HYDRATE SKIP: CURRENT EDITING STATE PROTECTED]", {
        sourceNodeId: sourceNodeIdFromRoute,
        reason: "already_hydrated_same_source",
      });
      return;
    }

    const navStats = getManualClipBoardMaterialStats(hydrationNavigationProject);
    const durableStats = getManualClipBoardMaterialStats(durableProject);
    const hydrateOptions = {
      explicitNewProject,
      durableProject,
      durableTried: durableBoardLoad.tried,
      forceResetBoard,
    };
    const parsedProject = embedded
      ? readManualActiveProject(sourceNodeIdFromRoute, embeddedProject, hydrateOptions)
      : readManualActiveProject(sourceNodeIdFromRoute, navigationProject, hydrateOptions);
    const pickedStats = getManualClipBoardMaterialStats(parsedProject);
    console.info("[MANUAL BOARD HYDRATE SOURCE FINAL]", {
      sourceNodeId: sourceNodeIdFromRoute,
      explicitNewProject,
      forceResetBoard,
      shouldBypassDurableForFreshNewProject,
      durableTried: durableBoardLoad.tried,
      durableFound: hasMeaningfulManualProject(durableProject),
      durableProjectId: durableProject?.project_id || durableProject?.projectId || "",
      navigationProjectId: hydrationNavigationProject?.project_id || hydrationNavigationProject?.projectId || "",
      durableStats,
      navStats,
      pickedOwner: getManualProjectOwnerId(parsedProject),
      pickedProjectId: parsedProject?.project_id || parsedProject?.projectId || "",
      pickedStats,
    });
    if (!hasMeaningfulManualProject(parsedProject)) {
      setProject(null);
      setSelectedSceneId("");
      didHydrateRef.current = true;
      return;
    }
    try {
      const parsed = unwrapManualProjectBackupJson(parsedProject);
      const clearedExplicitOpenState = explicitNewProject
        ? {
          ...(openState || {}),
          manualBoardExplicitNewProject: false,
          forceProjectId: "",
          forceInputSignature: "",
          forceAudioSignature: "",
          updatedAt: Date.now(),
        }
        : openState;
      if (explicitNewProject) {
        writeManualClipBoardOpenState(clearedExplicitOpenState);
      }
      const forcedProjectId = String(location.state?.manualBoardForceProjectId || location.state?.forceProjectId || clearedExplicitOpenState?.forceProjectId || "").trim();
      const forcedInputSignature = String(location.state?.manualBoardForceInputSignature || location.state?.forceInputSignature || clearedExplicitOpenState?.forceInputSignature || "").trim();
      const forcedAudioSignature = String(location.state?.manualBoardForceAudioSignature || location.state?.forceAudioSignature || clearedExplicitOpenState?.forceAudioSignature || "").trim();
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
      const currentSelectedSceneId = String(selectedSceneIdRef.current || "").trim();
      const openStateSelectedSceneId = String(openState?.selectedSceneId || "").trim();
      const selectedSceneIdForHydrate = didHydrateRef.current === true
        ? (
          scenes.some((scene) => scene.scene_id === currentSelectedSceneId)
            ? currentSelectedSceneId
            : scenes.some((scene) => scene.scene_id === openStateSelectedSceneId)
              ? openStateSelectedSceneId
              : scenes.some((scene) => scene.scene_id === parsedSelectedSceneId)
                ? parsedSelectedSceneId
                : String(scenes[0]?.scene_id || "")
        )
        : (
          scenes.some((scene) => scene.scene_id === parsedSelectedSceneId)
            ? parsedSelectedSceneId
            : scenes.some((scene) => scene.scene_id === openStateSelectedSceneId)
              ? openStateSelectedSceneId
              : String(scenes[0]?.scene_id || "")
        );
      console.info("[MANUAL BOARD HYDRATE RAW SCENE MEDIA DEBUG]", {
        selectedSceneId: selectedSceneIdForHydrate,
        rawMediaDebugStats: getManualBoardMediaDebugStats(parsed),
        normalizedMediaDebugStats: getManualBoardMediaDebugStats({ ...parsed, scenes }),
      });
      console.info("[MANUAL BOARD HYDRATE RAW SCENE STATE DEBUG]", {
        selectedSceneId: selectedSceneIdForHydrate,
        rawSceneStateDebugStats: getManualBoardSceneStateDebugStats(parsed),
        normalizedSceneStateDebugStats: getManualBoardSceneStateDebugStats({ ...parsed, scenes }),
      });
      let hydratedProject = normalizeDirectorProjectOwner({
        ...normalizeManualBoardProjectAudioCompat(parsed),
        ...(forcedProjectId ? { project_id: forcedProjectId, projectId: forcedProjectId } : {}),
        ...(forcedInputSignature ? { input_signature: forcedInputSignature, inputSignature: forcedInputSignature } : {}),
        ...(forcedAudioSignature ? { audio_signature: forcedAudioSignature, audioSignature: forcedAudioSignature } : {}),
        format: projectFormat,
        aspect_ratio: normalizeProjectAspectFormat(parsed?.aspect_ratio) || projectFormat,
        story_blocks: storyBlocks,
        scenes,
        selectedSceneId: selectedSceneIdForHydrate,
      });
      const currentHydratedProject = projectRef.current;
      hydratedProject = preserveProjectTimelineIfIncomingEmpty(hydratedProject, currentHydratedProject || readLastGoodManualClipBoardProject() || lastGoodBoardRef.current, parsed);
      const incomingReason = String(hydratedProject?.lastPersistReason || parsed?.lastPersistReason || "").trim();
      const currentDiagnostics = getManualBoardMediaDiagnostics(currentHydratedProject || {}, currentHydratedProject?.selectedSceneId || selectedSceneIdRef.current || "");
      const incomingDiagnostics = getManualBoardMediaDiagnostics(hydratedProject, selectedSceneIdForHydrate);
      const incomingRevision = getManualBoardRevision(hydratedProject);
      const currentRevision = getManualBoardRevision(currentHydratedProject || {});
      const hasCurrentProject = hasMeaningfulManualProject(currentHydratedProject);
      const currentPromptStats = getManualBoardSceneStateDebugStats(currentHydratedProject || {});
      const incomingPromptStats = getManualBoardSceneStateDebugStats(hydratedProject || {});
      const shouldSkipPromptRegressionHydrate = Boolean(
        !explicitNewProject
        && !forceResetBoard
        && hasCurrentProject
        && currentPromptStats.scenesWithPrompt > incomingPromptStats.scenesWithPrompt
      );
      if (shouldSkipPromptRegressionHydrate) {
        console.warn("[MANUAL BOARD HYDRATE SKIP PROMPT REGRESSION]", {
          incomingReason,
          currentScenesWithPrompt: currentPromptStats.scenesWithPrompt,
          incomingScenesWithPrompt: incomingPromptStats.scenesWithPrompt,
        });
        return;
      }
      const shouldSkipHydrate = Boolean(
        !explicitNewProject
        && hasCurrentProject
        && (
          incomingReason === "video_poll_running"
          || incomingReason === "video_poll_queued"
          || incomingRevision <= currentRevision
          || (
            !isManualExplicitMediaLossReason(incomingReason)
            && (
              incomingDiagnostics.scenesWithImage < currentDiagnostics.scenesWithImage
              || incomingDiagnostics.scenesWithVideo < currentDiagnostics.scenesWithVideo
            )
          )
          || (projectHasManualVideoJobRunning(currentHydratedProject) && incomingRevision <= currentRevision + 1)
        )
      );
      if (shouldSkipHydrate) {
        console.warn("[MANUAL BOARD HYDRATE SKIP STALE_OR_REGRESSIVE]", {
          incomingRevision,
          currentRevision,
          incomingScenesWithImage: incomingDiagnostics.scenesWithImage,
          currentScenesWithImage: currentDiagnostics.scenesWithImage,
          incomingScenesWithVideo: incomingDiagnostics.scenesWithVideo,
          currentScenesWithVideo: currentDiagnostics.scenesWithVideo,
          reason: incomingReason || "hydrate_stale_or_regressive",
        });
        return;
      }
      writeManualClipBoardOpenState({
        ...(openState || {}),
        manualBoardExplicitNewProject: false,
        forceResetBoard: false,
        selectedSceneId: selectedSceneIdForHydrate,
        updatedAt: Date.now(),
      });
      if (explicitNewProject) {
        consumedExplicitNewProjectRef.current = true;
      }
      const resolvedAudioUrl = String(hydratedProject.audio?.url || hydratedProject.audio_url || hydratedProject.audioUrl || "").trim();
      console.info("[MANUAL BOARD AUDIO RESOLVE]", {
        projectId: hydratedProject.project_id || hydratedProject.projectId || "",
        audioObjectUrl: hydratedProject?.audio?.url || "",
        audio_url: hydratedProject?.audio_url || "",
        audioUrl: hydratedProject?.audioUrl || "",
        resolvedAudioUrl,
        audioDurationSec: Number(hydratedProject?.audio?.duration_sec || hydratedProject?.audio_duration_sec || hydratedProject?.audioDurationSec || 0) || 0,
        sourceNodeId: hydratedProject?.sourceNodeId || hydratedProject?.nodeId || "",
        boardMode: hydratedProject?.board_mode,
        quickBoard: hydratedProject?.quick_board,
      });
      const hydrateMediaDiagnostics = getManualBoardMediaDiagnostics(hydratedProject, selectedSceneIdForHydrate);
      console.info("[MANUAL BOARD HYDRATE MEDIA FIELDS]", {
        selectedSceneId: hydrateMediaDiagnostics.selectedSceneId,
        scenesWithImage: hydrateMediaDiagnostics.scenesWithImage,
        scenesWithVideo: hydrateMediaDiagnostics.scenesWithVideo,
      });
      projectRef.current = hydratedProject;
      selectedSceneIdRef.current = selectedSceneIdForHydrate;
      lastAutosaveSignatureRef.current = buildManualBoardAutosaveSignature(hydratedProject);
      lastPersistedProjectRef.current = hydratedProject;
      setAutosaveStatus("Сохранено");
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
        lastAutosaveSignatureRef.current = buildManualBoardAutosaveSignature(projectToPersist);
        lastPersistedProjectRef.current = projectToPersist;
        setAutosaveStatus("Сохранено");
        setProject(projectToPersist);
        const ownerNodeId = sourceNodeIdFromRoute || hydratedProject.sourceNodeId || hydratedProject.nodeId;
        logManualBoardRuntimeTimelineDebug(reason, projectToPersist, "before_persist");
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
          audio: getManualBoardAudioInfo(projectToPersist),
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
  }, [embedded, embeddedProject, sourceNodeIdFromRoute, location.state, durableBoardLoad]);

  useEffect(() => {
    projectRef.current = project;
  }, [project]);

  useEffect(() => {
    selectedSceneIdRef.current = selectedSceneId;
  }, [selectedSceneId]);

  const flushManualBoardAutosave = (reason = "manual_board_autosave", options = {}) => {
    const { updateStatus = true, skipSignatureCheck = false, allowMaterialLoss = false } = options || {};
    if (autosaveTimerRef.current) {
      window.clearTimeout(autosaveTimerRef.current);
      autosaveTimerRef.current = null;
    }
    if (!didHydrateRef.current) return false;
    const currentProject = projectRef.current;
    if (!hasMeaningfulManualProject(currentProject)) return false;
    const signature = buildManualBoardAutosaveSignature(currentProject);
    if (!skipSignatureCheck && signature === lastAutosaveSignatureRef.current) {
      if (updateStatus) setAutosaveStatus("Сохранено");
      return true;
    }

    let safeProject = normalizeDirectorProjectOwner({
      ...currentProject,
      selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || currentProject.scenes?.[0]?.scene_id || "",
      updatedAt: Date.now(),
      lastPersistReason: reason,
    });
    const persistedBaseline = lastGoodBoardRef.current || lastPersistedProjectRef.current || readManualClipBoardProjectForNode(getProjectOwnerNodeId(safeProject));
    const beforeTimeline = getManualProjectTimelineDiagnostics(safeProject);
    safeProject = preserveProjectTimelineIfIncomingEmpty(safeProject, persistedBaseline || currentProject, safeProject);
    const baselineTimeline = getManualProjectTimelineDiagnostics(persistedBaseline || currentProject);
    if (beforeTimeline.scenesWithTiming === 0 && baselineTimeline.scenesWithTiming > 0) {
      logManualBoardRuntimeTimelineRegression(reason, persistedBaseline || currentProject, safeProject);
    }
    logManualBoardRuntimeTimelineDebug(reason, safeProject, "before_persist");
    const currentDiagnostics = getManualBoardMediaDiagnostics(persistedBaseline || {}, persistedBaseline?.selectedSceneId || selectedSceneIdRef.current || "");
    const nextDiagnostics = getManualBoardMediaDiagnostics(safeProject, safeProject.selectedSceneId);
    if (
      hasMeaningfulManualProject(persistedBaseline)
      && !allowMaterialLoss
      && !isManualExplicitMediaLossReason(reason)
      && (
        nextDiagnostics.scenesWithImage < currentDiagnostics.scenesWithImage
        || nextDiagnostics.scenesWithVideo < currentDiagnostics.scenesWithVideo
      )
    ) {
      console.warn("[MANUAL BOARD PERSIST BLOCKED MEDIA REGRESSION]", {
        reason,
        currentScenesWithImage: currentDiagnostics.scenesWithImage,
        nextScenesWithImage: nextDiagnostics.scenesWithImage,
        currentScenesWithVideo: currentDiagnostics.scenesWithVideo,
        nextScenesWithVideo: nextDiagnostics.scenesWithVideo,
        project_id: safeProject.project_id || safeProject.projectId || "",
        selectedSceneId: safeProject.selectedSceneId || selectedSceneIdRef.current || "",
      });
      if (updateStatus) setAutosaveStatus("Сохранено");
      return false;
    }

    projectRef.current = safeProject;
    selectedSceneIdRef.current = safeProject.selectedSceneId;
    if (updateStatus) {
      setAutosaveStatus("Сохраняем…");
      setAutosaveError("");
    }

    try {
      const saved = persistAndBroadcastDirectorProject(safeProject, { reason });
      if (!saved) throw new Error(getLastManualClipBoardStorageError()?.reason || "autosave_verify_failed");
      const storageErrorAfterSave = getLastManualClipBoardStorageError();
      lastAutosaveSignatureRef.current = buildManualBoardAutosaveSignature(safeProject);
      lastPersistedProjectRef.current = safeProject;
      manualBoardDirtyRef.current = false;
      rememberManualBoardLastGood(safeProject);
      if (updateStatus) {
        setProject(safeProject);
        if (storageErrorAfterSave?.emergencySaved === true) {
          const isQuotaEmergency = storageErrorAfterSave?.reason === "quota_exceeded";
          setAutosaveStatus(isQuotaEmergency ? "Autosave переполнен — скачайте backup вручную" : "Сохранён аварийный backup в браузере");
          setAutosaveError(String(storageErrorAfterSave?.errorMessage || storageErrorAfterSave?.reason || "emergency_saved"));
          setShowEmergencyBackupButton(isQuotaEmergency);
        } else {
          setAutosaveStatus("Сохранено");
          setShowEmergencyBackupButton(false);
        }
      }
      const diagnostics = getManualBoardMediaDiagnostics(safeProject, safeProject.selectedSceneId);
      console.info("[MANUAL BOARD AUTOSAVE DONE]", {
        reason,
        project_id: safeProject.project_id || safeProject.projectId || "",
        selectedSceneId: safeProject.selectedSceneId || "",
        scenesWithImage: diagnostics.scenesWithImage,
        scenesWithVideo: diagnostics.scenesWithVideo,
      });
      return true;
    } catch (error) {
      const storageError = getLastManualClipBoardStorageError();
      const errorMessage = String(storageError?.errorMessage || storageError?.reason || error?.message || error || "autosave_failed");
      if (updateStatus) {
        if (storageError?.emergencySaved === true) {
          const isQuotaEmergency = storageError?.reason === "quota_exceeded";
          setAutosaveStatus(isQuotaEmergency ? "Autosave переполнен — скачайте backup вручную" : "Сохранён аварийный backup в браузере");
          setAutosaveError(errorMessage);
          setShowEmergencyBackupButton(isQuotaEmergency);
        } else {
          const isQuotaError = storageError?.reason === "quota_exceeded";
          setAutosaveStatus(isQuotaError ? "Autosave переполнен — скачайте backup вручную" : "Ошибка autosave");
          setAutosaveError(errorMessage);
          setShowEmergencyBackupButton(true);
          maybeAutoDownloadEmergencyBackup(reason);
        }
      }
      console.error("[MANUAL BOARD AUTOSAVE FAILED]", {
        reason,
        errorName: error?.name || storageError?.errorName || "",
        errorMessage,
        errorStack: error?.stack || storageError?.errorStack || "",
        storageBackend: storageError?.storageBackend || "localStorage",
        projectId: safeProject.project_id || safeProject.projectId || "",
        activeProjectId: storageError?.activeProjectId || "",
        snapshotSize: storageError?.snapshotSize || getManualClipBoardSnapshotSize(safeProject),
        sceneCount: Array.isArray(safeProject.scenes) ? safeProject.scenes.length : 0,
        materialCount: getManualClipBoardMaterialStats(safeProject).materialTotal || 0,
        storageError,
      });
      return false;
    }
  };

  const scheduleManualBoardAutosave = (reason = "manual_board_autosave") => {
    if (isManualPollLocalOnlyReason(reason)) return;
    if (!didHydrateRef.current) return;
    const currentProject = projectRef.current;
    if (!hasMeaningfulManualProject(currentProject)) return;
    const signature = buildManualBoardAutosaveSignature(currentProject);
    if (signature === lastAutosaveSignatureRef.current) {
      setAutosaveStatus("Сохранено");
      return;
    }
    if (autosaveTimerRef.current) window.clearTimeout(autosaveTimerRef.current);
    manualBoardDirtyRef.current = true;
    setAutosaveStatus("Есть несохранённые изменения");
    setAutosaveError("");
    console.info("[MANUAL BOARD AUTOSAVE SCHEDULED]", {
      reason,
      project_id: currentProject.project_id || currentProject.projectId || "",
      selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || "",
    });
    autosaveTimerRef.current = window.setTimeout(() => {
      autosaveTimerRef.current = null;
      flushManualBoardAutosave(reason);
    }, 850);
  };

  const persistProject = (nextProject) => {
    const nextFormat = resolveProjectAspectFormat(nextProject || {}, (nextProject || {})?.scenes?.[0] || {});
    const currentProject = projectRef.current || project || lastGoodBoardRef.current || lastPersistedProjectRef.current || {};
    let safeProject = normalizeDirectorProjectOwner({
      ...(nextProject || {}),
      format: nextFormat,
      aspect_ratio: nextFormat,
      selectedSceneId: String(nextProject?.selectedSceneId || selectedSceneIdRef.current || ""),
      updatedAt: Date.now(),
    });
    const beforeTimeline = getManualProjectTimelineDiagnostics(safeProject);
    safeProject = preserveProjectTimelineIfIncomingEmpty(safeProject, currentProject, nextProject);
    if (beforeTimeline.scenesWithTiming === 0 && getManualProjectTimelineDiagnostics(currentProject).scenesWithTiming > 0) {
      logManualBoardRuntimeTimelineRegression(safeProject.lastPersistReason || "manual_director_persist_project", currentProject, safeProject);
    }
    projectRef.current = safeProject;
    selectedSceneIdRef.current = safeProject.selectedSceneId;
    setProject(safeProject);
    if (!didHydrateRef.current || !hasMeaningfulManualProject(safeProject)) return;
    scheduleManualBoardAutosave(safeProject.lastPersistReason || "manual_director_persist_project");
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
    let safeProject = normalizeDirectorProjectOwner({
      ...currentProject,
      selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || "",
      updatedAt: Date.now(),
      lastPersistReason: "manual_force_save_button",
    });
    logManualBoardRuntimeTimelineDebug("manual_force_save_button", safeProject, "before_persist");
    projectRef.current = safeProject;
    setProject(safeProject);
    console.info("[MANUAL BOARD SAVE MEDIA FIELDS]", getManualBoardMediaDiagnostics(safeProject, safeProject.selectedSceneId));
    if (autosaveTimerRef.current) {
      window.clearTimeout(autosaveTimerRef.current);
      autosaveTimerRef.current = null;
    }
    setAutosaveStatus("Сохраняем…");
    setAutosaveError("");
    const wrote = forceWriteManualClipBoardProjectForNode(safeProject, { reason: "manual_force_save_button" });
    const storageError = getLastManualClipBoardStorageError();
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
    if (readbackOk) {
      lastAutosaveSignatureRef.current = buildManualBoardAutosaveSignature(safeProject);
      lastPersistedProjectRef.current = safeProject;
      manualBoardDirtyRef.current = false;
      setAutosaveStatus("Сохранено");
      setAutosaveError("");
      setShowEmergencyBackupButton(false);
      rememberManualBoardLastGood(safeProject);
    } else {
      const isQuotaError = storageError?.reason === "quota_exceeded";
      setAutosaveStatus(isQuotaError ? "Autosave переполнен — скачайте backup вручную" : "Ошибка autosave");
      setAutosaveError(String(storageError?.errorMessage || storageError?.reason || "manual_force_save_failed"));
      setShowEmergencyBackupButton(true);
      maybeAutoDownloadEmergencyBackup("manual_force_save_button_failed");
    }
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
    lastAutosaveSignatureRef.current = buildManualBoardAutosaveSignature(safeProject);
    manualBoardDirtyRef.current = false;
    setAutosaveStatus("Сохранено");
    setProject(safeProject);
    return true;
  };

  useEffect(() => {
    const persistBeforeLeave = () => {
      if (manualBoardDirtyRef.current !== true) return;
      const currentProject = projectRef.current;
      if (!hasMeaningfulManualProject(currentProject)) return;
      const safeProject = normalizeDirectorProjectOwner({
        ...currentProject,
        selectedSceneId: selectedSceneIdRef.current || currentProject.selectedSceneId || currentProject.scenes?.[0]?.scene_id || "",
        updatedAt: Date.now(),
        lastPersistReason: "pagehide_dirty_draft",
      });
      const wrote = forceWriteManualClipBoardProjectForNode(safeProject, { reason: "pagehide_dirty_draft" });
      if (wrote) {
        projectRef.current = safeProject;
        selectedSceneIdRef.current = safeProject.selectedSceneId;
        lastAutosaveSignatureRef.current = buildManualBoardAutosaveSignature(safeProject);
        lastPersistedProjectRef.current = safeProject;
        manualBoardDirtyRef.current = false;
        rememberManualBoardLastGood(safeProject);
      }
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
      const status = String(scene?.status || "").trim().toLowerCase();

      if (!sceneId || !jobId) return;
      const key = `${sceneId}:${jobId}`;
      if (resolveManualSceneFinalVideoUrl(scene)) return;
      if (!["video_running", "video_queued"].includes(status)) return;
      if (terminalVideoJobsRef.current.has(key)) return;
      if (resumedVideoJobsRef.current.has(key)) return;

      resumedVideoJobsRef.current.add(key);
      console.info("[MANUAL BOARD VIDEO RESUME POLL]", {
        sceneId,
        jobId,
        status,
        statusEndpoint: String(scene?.video_status_endpoint || scene?.videoStatusEndpoint || ""),
      });
      pollManualSceneVideo(sceneId, jobId, 0, scene?.video_status_endpoint || scene?.videoStatusEndpoint || "");
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

  const rememberManualBoardLastGood = (candidateProject = projectRef.current || project || {}) => {
    if (!hasMeaningfulManualProject(candidateProject)) return false;
    lastGoodBoardRef.current = candidateProject;
    return rememberLastGoodManualClipBoardProject(candidateProject);
  };

  function downloadEmergencyBoardBackup(source = "manual_director_board_emergency") {
    const currentProject = projectRef.current || project || {};
    const ownerNodeId = String(getProjectOwnerNodeId(currentProject) || sourceNodeIdFromRoute || "").trim();
    const storedEmergencyProject = readEmergencyManualClipBoardProjectForNode(ownerNodeId);
    const fallbackProject = hasMeaningfulManualProject(storedEmergencyProject)
      ? storedEmergencyProject
      : (hasMeaningfulManualProject(currentProject) ? currentProject : (lastGoodBoardRef.current || readLastGoodManualClipBoardProject() || {}));
    const currentSelectedSceneId = selectedSceneIdRef.current || fallbackProject.selectedSceneId || selectedSceneId || "";
    if (!hasMeaningfulManualProject(fallbackProject)) {
      setBackupStatus("Нет аварийного backup для скачивания");
      return false;
    }
    downloadJsonPayload(
      {
        backup_type: "photostudio_manual_project_emergency_backup",
        backup_schema_version: 1,
        createdAt: new Date().toISOString(),
        source,
        project: buildEmergencyManualClipBoardProjectForStorage({ ...fallbackProject, selectedSceneId: currentSelectedSceneId }),
      },
      `manual_director_emergency_backup_${Date.now()}.json`,
    );
    setBackupStatus("Аварийный backup скачан");
    window.setTimeout(() => setBackupStatus(""), 1800);
    return true;
  }

  const onCleanupOldBrowserBackups = () => {
    const currentProject = projectRef.current || project || {};
    const ownerNodeId = String(getProjectOwnerNodeId(currentProject) || "").trim();
    const removedKeys = cleanupManualClipBoardStorageAggressive({
      currentNodeId: ownerNodeId,
      activeProjectId: currentProject.project_id || currentProject.projectId || "",
    });
    setBackupStatus(removedKeys.length
      ? `Очищено старых backup: ${removedKeys.length}`
      : "Старых backup для очистки не найдено");
    window.setTimeout(() => setBackupStatus(""), 2200);
  };

  const onDownloadProjectBackup = () => {
    const currentProject = projectRef.current || project || {};
    const currentSelectedSceneId = selectedSceneIdRef.current || currentProject.selectedSceneId || selectedSceneId || "";
    downloadJsonPayload(buildManualProjectBackupJson({ ...currentProject, selectedSceneId: currentSelectedSceneId }, { source: "manual_director_board" }));
  };

  const restoreManualProjectObject = (rawProject, successPrefix = "Backup восстановлен") => {
    const parsed = unwrapManualProjectBackupJson(rawProject);
    const storyBlocks = Array.isArray(parsed?.story_blocks) ? parsed.story_blocks.map(normalizeStoryBlock) : [];
    const storyBlockLookup = buildStoryBlockLookup(storyBlocks);
    const importedScenes = Array.isArray(parsed?.scenes) ? parsed.scenes : [];
    const mergedScenes = mergeImportedScenesPreservingMaterials(projectRef.current?.scenes || project?.scenes || [], importedScenes);
    const scenes = mergedScenes.map((scene, idx) => normalizeScene(scene, idx, storyBlockLookup));
    const currentProject = projectRef.current || project || {};
    let nextProject = {
      ...currentProject,
      ...parsed,
      story_blocks: storyBlocks,
      scenes,
      selectedSceneId: String(parsed?.selectedSceneId || scenes[0]?.scene_id || ""),
      updatedAt: Date.now(),
    };
    nextProject = preserveProjectTimelineIfIncomingEmpty(nextProject, currentProject, parsed);
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

  useEffect(() => {
    const scene = selectedScene;
    if (!scene) return;

    setMMAudioPrompt(String(scene.mmaudio_prompt || scene.mmaudioPrompt || scene.sound_prompt || ""));
    setMMAudioNegativePrompt(String(scene.mmaudio_negative_prompt || scene.mmaudioNegativePrompt || scene.negative_audio_prompt || ""));
  }, [project?.project_id, project?.updatedAt, selectedSceneId]);

  const currentBlock = useMemo(() => {
    const blockId = String(selectedScene?.story_block_id || "").trim();
    if (!blockId) return null;
    return storyBlocks.find((block) => String(block?.block_id || "") === blockId || String(block?.id || "") === blockId) || null;
  }, [selectedScene?.story_block_id, storyBlocks]);
  useEffect(() => {
    if (!selectedScene) return;
    const storyBlockId = String(selectedScene?.story_block_id || selectedScene?.storyBlockId || "").trim();
    console.info("[MANUAL BOARD SCENE BLOCK COLOR DEBUG]", {
      sceneId: String(selectedScene?.scene_id || selectedScene?.id || "").trim(),
      storyBlockId,
      sceneStoryBlockColor: String(selectedScene?.story_block_color || selectedScene?.storyBlockColor || "").trim(),
      sceneBlockColor: String(selectedScene?.block_color || selectedScene?.blockColor || "").trim(),
      sceneColor: String(selectedScene?.color || "").trim(),
      projectStoryBlocksCount: Array.isArray(project?.story_blocks) ? project.story_blocks.length : 0,
    });
  }, [project?.story_blocks, selectedScene]);
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
    console.info("[MANUAL BOARD SCENE AUDIO FALLBACK]", {
      sceneId: String(selectedScene?.scene_id || selectedScene?.id || "").trim(),
      hasAudioSliceUrl: Boolean(String(selectedScene?.audio_slice_url || selectedScene?.audioSliceUrl || "").trim()),
      hasMasterAudio: Boolean(audioUrl),
      startSec: Number(selectedScene?.start_sec || 0) || 0,
      endSec: Number(selectedScene?.end_sec || 0) || 0,
      mode: "master_audio_range",
    });
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
    const sceneExists = Array.isArray(baseProject.scenes)
      && baseProject.scenes.some((scene) => String(scene.scene_id || "") === safeSceneId);

    if (!safeSceneId || !sceneExists) return;

    selectedSceneIdRef.current = safeSceneId;
    setSelectedSceneId(safeSceneId);

    projectRef.current = {
      ...baseProject,
      selectedSceneId: safeSceneId,
    };

    try {
      writeManualClipBoardOpenState({
        ...(readManualClipBoardOpenState() || {}),
        isOpen: true,
        sourceNodeId: getProjectOwnerNodeId(baseProject),
        selectedSceneId: safeSceneId,
        updatedAt: Date.now(),
        reason: "selected_scene_ui_only",
      });
    } catch {}

    console.info("[MANUAL BOARD SELECT SCENE UI ONLY]", {
      sceneId: safeSceneId,
      sourceNodeId: getProjectOwnerNodeId(baseProject),
    });
  };

  useEffect(() => {
    setIsUserNoteEditorOpen(false);
  }, [selectedSceneId]);

  const updateScene = (sceneId, patchOrFactory, options = {}) => {
    const baseProject = projectRef.current || project || {};
    const prevScenes = Array.isArray(baseProject?.scenes) ? baseProject.scenes : [];
    const nextScenes = prevScenes.map((scene) => {
      if (scene.scene_id !== sceneId) return scene;
      const existingScene = scene || {};
      const patch = typeof patchOrFactory === "function" ? patchOrFactory(existingScene) : patchOrFactory;
      console.info("[MANUAL BOARD UPDATE_SCENE PATCH DEBUG]", {
        sceneId,
        reason: "update_scene",
        beforeSceneState: getManualBoardSceneStateDebugStats({ ...projectRef.current, scenes: [existingScene] }),
        patchSceneState: getManualBoardSceneStateDebugStats({ ...projectRef.current, scenes: [patch] }),
        patchKeys: Object.keys(patch || {}),
      });
      const persistReason = options?.reason || "update_scene";
      const isDeletionUpdate = /delete.*(video|photo|image)|remove.*(video|photo|image)|clear.*(video|photo|image)|user_delete/i.test(persistReason);
      const promptSafePatch = mergeManualScenePatchPreservingPromptModel(existingScene, patch || {}, {
        allowEmptyPromptModelOverwrite: options?.allowEmptyPromptModelOverwrite === true,
      });
      const nextScene = (options?.allowMediaLoss === true || isDeletionUpdate)
        ? withManualSceneMediaAliases({ ...(existingScene || {}), ...promptSafePatch })
        : mergeManualScenePreservingMedia(existingScene, promptSafePatch);
      console.info("[MANUAL BOARD UPDATE_SCENE MERGED DEBUG]", {
        sceneId,
        mergedSceneState: getManualBoardSceneStateDebugStats({ ...projectRef.current, scenes: [nextScene] }),
      });
      return nextScene;
    });
    const persistReason = options?.reason || "update_scene";
    const isDeletionUpdate = /delete.*(video|photo|image)|remove.*(video|photo|image)|clear.*(video|photo|image)|user_delete/i.test(persistReason);
    const now = Date.now();
    const refSelectedSceneId = String(selectedSceneIdRef.current || "").trim();
    const baseSelectedSceneId = String(baseProject.selectedSceneId || "").trim();
    const nextSelectedSceneId = prevScenes.some((scene) => scene.scene_id === refSelectedSceneId)
      ? refSelectedSceneId
      : (prevScenes.some((scene) => scene.scene_id === baseSelectedSceneId) ? baseSelectedSceneId : String(prevScenes[0]?.scene_id || ""));
    if (!isManualPollLocalOnlyReason(persistReason) && /job|poll|video_done|video_queued|video_running|mmaudio/i.test(persistReason) && nextSelectedSceneId !== sceneId) {
      console.info("[MANUAL BOARD BLOCK AUTO SELECT FROM JOB]", { reason: persistReason, sceneId, selectedSceneId: nextSelectedSceneId });
    }
    let nextProject = normalizeDirectorProjectOwner({
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
    nextProject = preserveProjectTimelineIfIncomingEmpty(nextProject, lastGoodBoardRef.current || lastPersistedProjectRef.current || baseProject, nextProject);

    projectRef.current = nextProject;
    selectedSceneIdRef.current = nextProject.selectedSceneId;
    setProject(nextProject);

    if (options?.autosave === false) {
      manualBoardDirtyRef.current = true;
      setAutosaveStatus("Есть несохранённые изменения");
      setAutosaveError("");
      return;
    }

    if (didHydrateRef.current && hasMeaningfulManualProject(nextProject) && !isManualPollLocalOnlyReason(persistReason)) {
      if (options?.forcePersist) {
        const saved = flushManualBoardAutosave(persistReason, {
          skipSignatureCheck: true,
          allowMaterialLoss: options?.allowMediaLoss === true || options?.allowMaterialLoss === true,
        });
        if (!saved) {
          const storageError = getLastManualClipBoardStorageError();
          const isQuotaError = storageError?.reason === "quota_exceeded";
          setAutosaveStatus(isQuotaError ? "Autosave переполнен — скачайте backup вручную" : "Ошибка autosave");
          setShowEmergencyBackupButton(true);
          if (/video_done/i.test(persistReason)) maybeAutoDownloadEmergencyBackup("video_done_force_persist_failed");
        }
      } else {
        scheduleManualBoardAutosave(persistReason);
      }
      const savedScene = nextScenes.find((s) => s.scene_id === sceneId);
      console.debug("[manual director updateScene autosave scheduled]", {
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
      ...clearManualSceneVideoMediaPatch(),
    };
    return clearManualSceneVideoMediaPatch({
      status: resolveManualSceneStatus(sceneWithoutVideo),
    });
  };

  const onDeleteSceneVideo = (scene) => {
    if (!scene?.scene_id) return;
    updateScene(
      scene.scene_id,
      (currentScene = {}) => clearVideoPatch(currentScene),
      {
        reason: "user_delete_video",
        allowMediaLoss: true,
        allowMaterialLoss: true,
        allowEmptyPromptModelOverwrite: true,
        autosave: false,
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
    imageUploadStatus: "",
    image_upload_error: "",
    imageUploadError: "",
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
    imageUploadStatus: "",
    image_upload_error: "",
    imageUploadError: "",
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
      reason: "user_delete_photo",
      allowMediaLoss: true,
      allowMaterialLoss: true,
      allowEmptyPromptModelOverwrite: true,
      autosave: false,
      explicitReset: true,
    });
  };

  const onDeleteFirstLastEndImage = (scene) => {
    if (!scene?.scene_id) return;
    if (scene.video_url && !window.confirm("Удаление последнего кадра также удалит видео этой сцены. Продолжить?")) return;
    updateScene(scene.scene_id, (currentScene = {}) => clearEndFramePatch(currentScene), {
      reason: "user_delete_photo",
      allowMediaLoss: true,
      allowMaterialLoss: true,
      allowEmptyPromptModelOverwrite: true,
      autosave: false,
      explicitReset: true,
    });
  };

  const onDeleteScenePhoto = (scene) => {
    if (!scene?.scene_id) return;
    if (scene.video_url && !window.confirm("Удаление фото также удалит видео этой сцены. Продолжить?")) return;
    updateScene(scene.scene_id, (currentScene = {}) => ({
      ...clearVideoPatch(currentScene),
      ...clearManualSceneImageMediaPatch(),
      ...clearManualStalePromptFields(),
      status: "draft",
    }), {
      reason: "user_delete_photo",
      allowMediaLoss: true,
      allowMaterialLoss: true,
      allowEmptyPromptModelOverwrite: true,
      autosave: false,
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
      ...(shouldClearVideoAfterUpload ? clearVideoPatch(selectedUploadScene) : {}),
      ...(isEndSlot
        ? { end_image_preview_url: previewUrl, endImagePreviewUrl: previewUrl }
        : { image_preview_url: previewUrl, imagePreviewUrl: previewUrl, start_image_preview_url: previewUrl, startImagePreviewUrl: previewUrl }),
      image_upload_status: "uploading",
      imageUploadStatus: "uploading",
      image_upload_error: "",
      imageUploadError: "",
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
            ? { end_image_url: imageUrl, endImageUrl: imageUrl, end_image_preview_url: imageUrl, endImagePreviewUrl: imageUrl }
            : { image_url: imageUrl, imageUrl, start_image_url: imageUrl, startImageUrl: imageUrl, image_preview_url: imageUrl, imagePreviewUrl: imageUrl, start_image_preview_url: imageUrl, startImagePreviewUrl: imageUrl, generated_image_url: imageUrl, generatedImageUrl: imageUrl }),
        };
        return {
          ...(isEndSlot
            ? { end_image_url: imageUrl, endImageUrl: imageUrl, end_image_preview_url: imageUrl, endImagePreviewUrl: imageUrl }
            : { image_url: imageUrl, imageUrl, start_image_url: imageUrl, startImageUrl: imageUrl, image_preview_url: imageUrl, imagePreviewUrl: imageUrl, start_image_preview_url: imageUrl, startImagePreviewUrl: imageUrl, generated_image_url: imageUrl, generatedImageUrl: imageUrl }),
          ...aspectFields,
          ...clearManualStalePromptFields(),
          ...(shouldClearVideoAfterUpload ? clearVideoPatch(nextScene) : {}),
          image_upload_status: "done",
          imageUploadStatus: "done",
          image_upload_error: "",
          imageUploadError: "",
          status: resolveManualSceneStatus({ ...nextScene, ...(shouldClearVideoAfterUpload ? { video_url: "", videoUrl: "" } : {}) }),
          error: "",
        };
      }, { reason: "photo_upload_done", forcePersist: true, allowMediaLoss: shouldClearVideoAfterUpload });
    } catch (err) {
      updateScene(sceneId, {
        image_upload_status: "error",
        imageUploadStatus: "error",
        image_upload_error: String(err?.message || "image_upload_failed"),
        imageUploadError: String(err?.message || "image_upload_failed"),
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
          imageUrl,
          start_image_url: imageUrl,
          startImageUrl: imageUrl,
          start_image_preview_url: imageUrl,
          startImagePreviewUrl: imageUrl,
          image_preview_url: imageUrl,
          imagePreviewUrl: imageUrl,
          generated_image_url: imageUrl,
          generatedImageUrl: imageUrl,
          image_upload_status: "done",
          imageUploadStatus: "done",
          image_upload_error: "",
          imageUploadError: "",
          error: "",
          status: resolveManualSceneStatus(nextScene),
        };
      }, { reason: "photo_upload_done", forcePersist: true });
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
        result_video_url: resultVideoUrl,
        resultVideoUrl: resultVideoUrl,
        generated_video_url: resultVideoUrl,
        generatedVideoUrl: resultVideoUrl,
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
      }, { reason: "mmaudio_done", forcePersist: true });
    } catch (err) {
      updateScene(sceneId, {
        mmaudio_gain_status: "error",
        mmaudio_gain_error: String(err?.message || "mmaudio_gain_remix_failed"),
      }, { reason: "manual_mmaudio_gain_error" });
    }
  };

  async function pollManualSceneVideo(sceneId, jobId, attempt = 0, statusEndpoint = "") {
    const maxAttempts = 180;
    const delayMs = 5000;
    const safeSceneId = String(sceneId || "").trim();
    const safeJobId = String(jobId || "").trim();
    const pollKey = `${safeSceneId}:${safeJobId}`;
    console.info("[MANUAL BOARD VIDEO POLL START]", {
      sceneId: safeSceneId,
      jobId: safeJobId,
      attempt,
    });
    const findSceneByJob = () => {
      const scenes = Array.isArray(projectRef.current?.scenes) ? projectRef.current.scenes : [];
      return scenes.find((scene) => safeJobId && String(scene?.video_job_id || scene?.videoJobId || "").trim() === safeJobId)
        || scenes.find((scene) => String(scene?.scene_id || scene?.id || "").trim() === safeSceneId);
    };
    const logPollEarlyReturn = (reason, sceneForLog = findSceneByJob()) => {
      console.warn("[MANUAL BOARD VIDEO POLL EARLY_RETURN]", {
        sceneId: safeSceneId,
        jobId: safeJobId,
        reason,
        currentJobId: String(sceneForLog?.video_job_id || sceneForLog?.videoJobId || "").trim(),
        currentStatus: String(sceneForLog?.status || "").toLowerCase(),
        alreadyHasVideo: resolveManualSceneFinalVideoUrl(sceneForLog),
        terminalRef: terminalVideoJobsRef.current.has(pollKey),
      });
    };
    if (!safeJobId) {
      logPollEarlyReturn("missing_job_id");
      return;
    }
    const currentSceneBeforePoll = findSceneByJob();
    const currentJobId = String(currentSceneBeforePoll?.video_job_id || currentSceneBeforePoll?.videoJobId || "").trim();
    const currentStatus = String(currentSceneBeforePoll?.status || "").toLowerCase();
    const alreadyHasVideo = resolveManualSceneFinalVideoUrl(currentSceneBeforePoll);
    if (alreadyHasVideo) {
      logPollEarlyReturn("scene_already_has_video", currentSceneBeforePoll);
      return;
    }
    if (terminalVideoJobsRef.current.has(pollKey) && !alreadyHasVideo) {
      terminalVideoJobsRef.current.delete(pollKey);
    }
    if (attempt > 0 && currentSceneBeforePoll && currentJobId && currentJobId !== safeJobId) {
      logPollEarlyReturn("scene_job_id_changed", currentSceneBeforePoll);
      return;
    }
    const endpoint = resolveManualSceneVideoStatusEndpoint(safeJobId, statusEndpoint || currentSceneBeforePoll?.video_status_endpoint || currentSceneBeforePoll?.videoStatusEndpoint);
    console.info("[MANUAL BOARD VIDEO STATUS REQUEST]", {
      sceneId: safeSceneId,
      jobId: safeJobId,
      endpoint,
    });
    try {
      const statusOut = await getManualSceneVideoStatus(safeJobId, endpoint);
      const rawStatus = String(statusOut?.status || statusOut?.state || statusOut?.result?.status || statusOut?.result?.state || statusOut?.data?.status || statusOut?.data?.state || "");
      const status = resolveManualVideoJobStatus(statusOut);
      const doneVideoUrl = resolveManualVideoResultUrl(statusOut) || resolveManualStatusVideoUrl(statusOut);
      const responseKeys = collectManualResponseKeys(statusOut);
      console.info("[MANUAL BOARD VIDEO POLL RESPONSE]", {
        sceneId,
        status: statusOut?.status,
        jobId: statusOut?.jobId || statusOut?.job_id,
        videoUrl: statusOut?.videoUrl || statusOut?.video_url || statusOut?.resultVideoUrl || statusOut?.result_video_url || doneVideoUrl || "",
        rawKeys: statusOut && typeof statusOut === "object" ? Object.keys(statusOut) : [],
      });
      console.info("[MANUAL BOARD VIDEO POLL STATUS]", {
        sceneId,
        jobId,
        rawStatus,
        resolvedStatus: status,
        resolvedVideoUrl: doneVideoUrl,
        responseKeys,
      });
      const isDoneStatus = ["done", "ready", "success", "completed"].includes(status);

      if (isDoneStatus && doneVideoUrl) {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        const targetScene = findSceneByJob();
        const targetSceneId = String(targetScene?.scene_id || sceneId || "").trim();
        if (!targetSceneId) {
          console.warn("[MANUAL BOARD VIDEO RESULT APPLY FAILED]", {
            sceneId,
            jobId,
            reason: "scene_not_found",
            rawResponse: statusOut,
          });
          return;
        }
        const videoHasAudio = resolveManualStatusVideoHasAudio(statusOut);

        updateScene(targetSceneId, (currentScene = {}) => ({
          status: "video_ready",
          video_status: "done",
          videoStatus: "done",
          video_url: doneVideoUrl,
          videoUrl: doneVideoUrl,
          result_video_url: doneVideoUrl,
          resultVideoUrl: doneVideoUrl,
          generated_video_url: doneVideoUrl,
          generatedVideoUrl: doneVideoUrl,
          output_video_url: doneVideoUrl,
          outputVideoUrl: doneVideoUrl,
          final_video_url: doneVideoUrl,
          finalVideoUrl: doneVideoUrl,
          video_job_id: safeJobId,
          videoJobId: safeJobId,
          video_status_endpoint: endpoint,
          videoStatusEndpoint: endpoint,
          video_error: "",
          videoError: "",
          error: "",
          updated_at: Date.now(),
          updatedAt: Date.now(),
          video_has_audio: videoHasAudio,
          videoHasAudio: videoHasAudio,
          hasAudio: videoHasAudio,
          keep_generated_audio: Boolean(statusOut?.keepGeneratedAudio ?? statusOut?.keep_generated_audio ?? currentScene?.keep_generated_audio ?? false),
          keepGeneratedAudio: Boolean(statusOut?.keepGeneratedAudio ?? statusOut?.keep_generated_audio ?? currentScene?.keepGeneratedAudio ?? currentScene?.keep_generated_audio ?? false),
          generated_audio_policy: String(statusOut?.generatedAudioPolicy ?? statusOut?.generated_audio_policy ?? currentScene?.generated_audio_policy ?? ""),
          generatedAudioPolicy: String(statusOut?.generatedAudioPolicy ?? statusOut?.generated_audio_policy ?? currentScene?.generatedAudioPolicy ?? currentScene?.generated_audio_policy ?? ""),
          generated_audio_gain_db: Number(statusOut?.generatedAudioGainDb ?? statusOut?.generated_audio_gain_db ?? currentScene?.generated_audio_gain_db ?? -16),
          generatedAudioGainDb: Number(statusOut?.generatedAudioGainDb ?? statusOut?.generated_audio_gain_db ?? currentScene?.generatedAudioGainDb ?? currentScene?.generated_audio_gain_db ?? -16),
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
          videoRequestPayloadPreview: {
            ...(currentScene?.videoRequestPayloadPreview || currentScene?.video_request_payload_preview || {}),
            status: status || "done",
            videoUrl: doneVideoUrl,
            videoHasAudio,
          },
        }), { reason: "video_done", forcePersist: true });
        terminalVideoJobsRef.current.add(pollKey);
        flushManualBoardAutosave("video_done", { skipSignatureCheck: true });
        console.info("[MANUAL BOARD VIDEO URL SAVED TO SCENE]", {
          sceneId: targetSceneId,
          videoUrl: doneVideoUrl,
        });
        console.info("[MANUAL BOARD VIDEO RESULT APPLY]", {
          sceneId: targetSceneId,
          jobId,
          videoUrl: doneVideoUrl,
          status: "video_ready",
          sceneUpdated: true,
        });
        return;
      }

      if (isDoneStatus && !doneVideoUrl) {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        console.warn("[MANUAL BOARD VIDEO DONE WITHOUT URL]", {
          sceneId,
          response: statusOut,
        });
        console.warn("[MANUAL BOARD VIDEO RESULT APPLY FAILED]", {
          sceneId,
          jobId,
          reason: "terminal_status_without_video_url",
          rawResponse: statusOut,
        });
        updateScene(sceneId, {
          status: "video_error",
          video_job_id: jobId,
          videoJobId: jobId,
          video_error: "video_done_without_url",
          videoError: "video_done_without_url",
          error: "Видео сгенерировано, но backend не вернул videoUrl",
        }, { reason: "video_done_without_url" });
        return;
      }

      if (status === "interrupted") {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        terminalVideoJobsRef.current.add(pollKey);
        const interruptedMessage = "Генерация прервана после рестарта backend. Можно перезапустить job. backend_restarted_while_job_running interrupted";
        updateScene(sceneId, {
          status: "video_error",
          video_job_id: safeJobId,
          videoJobId: safeJobId,
          video_status_endpoint: endpoint,
          videoStatusEndpoint: endpoint,
          video_error: interruptedMessage,
          videoError: interruptedMessage,
          error: interruptedMessage,
        }, { reason: "video_poll_interrupted" });
        flushManualBoardAutosave("video_poll_interrupted", { skipSignatureCheck: true });
        return;
      }

      if (status === "error_aspect_ratio") {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        terminalVideoJobsRef.current.add(pollKey);
        const aspectError = "lip_sync вернул vertical, ожидался 16:9";
        updateScene(sceneId, {
          status: "error_aspect_ratio",
          video_status: "error_aspect_ratio",
          videoStatus: "error_aspect_ratio",
          video_job_id: jobId,
          videoJobId: jobId,
          video_status_endpoint: endpoint,
          videoStatusEndpoint: endpoint,
          video_error: aspectError,
          videoError: aspectError,
          error: aspectError,
        }, { reason: "video_poll_error_aspect_ratio" });
        flushManualBoardAutosave("video_poll_error_aspect_ratio", { skipSignatureCheck: true });
        return;
      }

      if (status === "error" || status === "stopped" || status === "not_found") {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        terminalVideoJobsRef.current.add(pollKey);
        const terminalError = status === "not_found"
          ? "job не найден в памяти/backend disk. Возможно backend был перезапущен до durable save."
          : String(statusOut?.error || statusOut?.hint || "video_job_failed");
        updateScene(sceneId, {
          status: "video_error",
          video_job_id: jobId,
          videoJobId: jobId,
          video_status_endpoint: endpoint,
          videoStatusEndpoint: endpoint,
          video_error: terminalError,
          videoError: terminalError,
          error: terminalError,
        }, { reason: "video_poll_terminal_error" });
        return;
      }
      if (status === "queued" || status === "running") {
        console.info("[MANUAL BOARD VIDEO POLL LOCAL STATUS ONLY]", {
          sceneId,
          jobId: safeJobId,
          status,
        });
        updateScene(sceneId, {
          status: status === "queued" ? "video_queued" : "video_running",
          video_status: status,
          videoStatus: status,
          video_job_id: safeJobId,
          videoJobId: safeJobId,
          video_status_endpoint: endpoint,
          videoStatusEndpoint: endpoint,
          video_error: "",
          videoError: "",
          error: "",
          lastPollAt: Date.now(),
        }, { reason: status === "queued" ? "video_poll_queued" : "video_poll_running", autosave: false });
        setTimeout(() => pollManualSceneVideo(sceneId, safeJobId, attempt + 1, endpoint), delayMs);
        return;
      }
      if (attempt >= maxAttempts) {
        videoPollErrorCountRef.current.delete(`${sceneId}:${jobId}`);
        terminalVideoJobsRef.current.add(pollKey);
        updateScene(sceneId, { status: "video_error", video_job_id: jobId, videoJobId: jobId, video_error: "video_poll_timeout", videoError: "video_poll_timeout", error: "video_poll_timeout" }, { reason: "video_poll_timeout" });
        return;
      }
      console.info("[MANUAL BOARD VIDEO POLL LOCAL STATUS ONLY]", { sceneId, jobId: safeJobId, status });
      updateScene(sceneId, { status: "video_running", video_status: "running", videoStatus: "running", video_job_id: safeJobId, videoJobId: safeJobId, video_status_endpoint: endpoint, videoStatusEndpoint: endpoint, video_error: "", videoError: "", lastPollAt: Date.now() }, { reason: "video_poll_unknown_status", autosave: false });
      setTimeout(() => pollManualSceneVideo(sceneId, safeJobId, attempt + 1, endpoint), delayMs);
    } catch (err) {
      const currentScene = (Array.isArray(projectRef.current?.scenes) ? projectRef.current.scenes : []).find((scene) => scene.scene_id === sceneId);
      const currentStatus = String(currentScene?.status || "").toLowerCase();

      if (currentStatus === "video_queued" || currentStatus === "video_running") {
        const key = `${sceneId}:${jobId}`;
        const count = (videoPollErrorCountRef.current.get(key) || 0) + 1;
        videoPollErrorCountRef.current.set(key, count);

        if (count < 6) {
          console.info("[MANUAL BOARD VIDEO POLL LOCAL STATUS ONLY]", { sceneId, jobId: safeJobId, status: "running" });
          updateScene(sceneId, {
            status: "video_running",
            video_status: "running",
            videoStatus: "running",
            video_job_id: safeJobId,
            videoJobId: safeJobId,
            video_status_endpoint: endpoint,
            videoStatusEndpoint: endpoint,
            video_error: `status polling retry ${count}/6`,
            videoError: `status polling retry ${count}/6`,
            error: "",
            lastPollAt: Date.now(),
          }, { reason: "video_poll_retry", autosave: false });
          setTimeout(() => pollManualSceneVideo(sceneId, safeJobId, attempt + 1, endpoint), 10000);
          return;
        }

        videoPollErrorCountRef.current.delete(key);
      }

      terminalVideoJobsRef.current.add(pollKey);
      updateScene(sceneId, { status: "video_error", video_job_id: jobId, videoJobId: jobId, video_error: String(err?.message || "video_poll_failed"), videoError: String(err?.message || "video_poll_failed"), error: String(err?.message || "video_poll_failed") }, { reason: "video_poll_failed" });
    }
  }

  const onExtractSceneAudioSlice = async (scene) => {
    const sceneId = String(scene?.scene_id || "").trim();
    if (!sceneId) return;
    const currentProject = projectRef.current || project || {};
    const sourceAudioUrl = String(
      currentProject?.audio?.url
      || currentProject?.audio_url
      || currentProject?.audioUrl
      || audioUrl
      || ""
    ).trim();
    if (!sourceAudioUrl) {
      updateScene(sceneId, {
        error: "В доске нет основного аудио. Вернитесь в тайминг и создайте быструю доску заново.",
      });
      return;
    }
    if (sourceAudioUrl.startsWith("blob:")) {
      updateScene(sceneId, {
        error: "Аудио проекта ещё не сохранено на сервер. Для lip-sync нужен server audio asset.",
      });
      return;
    }
    const startSec = Number(scene?.start_sec);
    const endSec = Number(scene?.end_sec);
    const explicitDuration = Number(scene?.duration_sec);
    const durationSec = Number.isFinite(explicitDuration) && explicitDuration > 0
      ? explicitDuration
      : (Number.isFinite(startSec) && Number.isFinite(endSec) && endSec > startSec ? endSec - startSec : 0);
    if (!Number.isFinite(startSec) || !Number.isFinite(endSec) || endSec <= startSec || durationSec <= 0) {
      updateScene(sceneId, { error: "Не удалось определить диапазон сцены для нарезки аудио." });
      return;
    }
    const endpoint = `${API_BASE}/api/podcast-audio/extract-phrase-to-asset`;
    const sourceNodeId = String(
      currentProject?.nodeId
      || currentProject?.sourceNodeId
      || currentProject?.ownerNodeId
      || scene?.sourceNodeId
      || scene?.ownerNodeId
      || "manual_board"
    ).trim() || "manual_board";
    console.info("[MANUAL BOARD AUDIO SLICE EXTRACT START]", {
      sceneId,
      sourceNodeId,
      sourceAudioUrl,
      startSec,
      endSec,
      durationSec,
      endpoint,
    });
    try {
      const response = await fetch(endpoint, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sourceAudioUrl,
          sourceStartSec: startSec,
          sourceEndSec: endSec,
          durationSec,
          label: sceneId,
          sourceNodeId,
        }),
      });
      const out = await response.json().catch(() => null);
      if (!response.ok || out?.ok === false) {
        throw new Error(String(out?.detail || out?.message || `HTTP ${response.status}`));
      }
      const audioSliceUrl = String(out?.url || out?.assetUrl || out?.asset_url || out?.publicUrl || out?.public_url || "").trim();
      const rawDuration = Number(out?.duration_sec ?? out?.durationSec ?? durationSec);
      const audioSliceDurationSec = Number.isFinite(rawDuration) && rawDuration > 0 ? rawDuration : durationSec;
      if (!audioSliceUrl) throw new Error(String(out?.detail || out?.hint || "audio_slice_url_empty"));
      const nextScene = {
        ...scene,
        audio_slice_url: audioSliceUrl,
        audioSliceUrl: audioSliceUrl,
        audio_slice_duration_sec: audioSliceDurationSec,
        audioSliceDurationSec: audioSliceDurationSec,
        audio_extracted: true,
        audio_source_mode: "audio_slice_asset",
        error: "",
      };
      updateScene(sceneId, {
        audio_slice_url: audioSliceUrl,
        audioSliceUrl: audioSliceUrl,
        audio_slice_duration_sec: audioSliceDurationSec,
        audioSliceDurationSec: audioSliceDurationSec,
        audio_extracted: true,
        audio_source_mode: "audio_slice_asset",
        error: "",
        status: resolveManualSceneStatus(nextScene),
      });
      console.info("[MANUAL BOARD AUDIO SLICE EXTRACT DONE]", { sceneId, audioSliceUrl, audioSliceDurationSec });
    } catch (err) {
      const message = String(err?.message || "audio_slice_extract_failed");
      updateScene(sceneId, { error: message });
      console.error("[MANUAL BOARD AUDIO SLICE EXTRACT ERROR]", { sceneId, error: message });
    }
  };

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

    if (scene.route === "ia2v" && !String(scene.audio_slice_url || scene.audioSliceUrl || "").trim()) {
      updateScene(scene.scene_id, { error: "Для lip-sync сначала нажмите ‘Изъять аудио’.", status: scene.status || "draft" });
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
      ...clearManualSceneVideoMediaPatch(),
      status: "video_queued",
      video_error: "",
      videoError: "",
      error: "",
    }, { reason: "video_queued_clear_old_media", explicitReset: true });
    try {
      const expectedDimensions = expectedFormat === "16:9"
        ? { width: 1280, height: 720 }
        : (expectedFormat === "1:1" ? { width: 1024, height: 1024 } : { width: 720, height: 1280 });
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
        provider: "comfy_remote",
        ...routePayload,
        format: expectedFormat,
        aspect_ratio: expectedFormat,
        aspectRatio: expectedFormat,
        projectFormat: expectedFormat,
        project_format: expectedFormat,
        projectAspectRatio: expectedFormat,
        project_aspect_ratio: expectedFormat,
        width: expectedDimensions.width,
        height: expectedDimensions.height,
        manualClip: true,
        manual_clip: true,
        source: "manual_clip_board",
        project_kind: project?.project_kind || "clip",
        keepGeneratedAudio: Boolean(routePayload.keepGeneratedAudio),
        generatedAudioPolicy: routePayload.generatedAudioPolicy,
        generatedAudioGainDb: Number(routePayload.generatedAudioGainDb ?? scene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
      };
      console.info("[MANUAL BOARD VIDEO START]", {
        sceneId: scene?.scene_id,
        route: scene?.route,
        imageUrl: scene?.image_url || scene?.imageUrl || scene?.image_preview_url || "",
        videoPrompt: scene?.video_prompt || scene?.videoPrompt || "",
        negativePrompt: scene?.negative_prompt || scene?.negativePrompt || "",
      });
      const out = await startManualSceneVideo(payload);
      const jobId = resolveVideoStartJobId(out);
      const statusEndpoint = resolveManualSceneVideoStatusEndpoint(jobId, out?.statusEndpoint || out?.status_endpoint || "");
      const queueStatus = String(out?.queueStatus || out?.queue_status || out?.status || "").toLowerCase();
      console.info("[MANUAL BOARD VIDEO START RESPONSE]", {
        sceneId: scene.scene_id,
        response: out,
        jobId,
        statusEndpoint,
        queueStatus,
      });
      if (out?.ok === false || !jobId) throw new Error(String(out?.detail || out?.error || "video_start_failed"));
      updateScene(scene.scene_id, {
        status: queueStatus === "queued" ? "video_queued" : "video_running",
        video_job_id: jobId,
        videoJobId: jobId,
        video_status_endpoint: statusEndpoint,
        videoStatusEndpoint: statusEndpoint,
        video_error: "",
        videoError: "",
        error: "",
        keep_generated_audio: Boolean(payload.keepGeneratedAudio),
        keepGeneratedAudio: Boolean(payload.keepGeneratedAudio),
        generated_audio_policy: String(payload.generatedAudioPolicy || ""),
        generatedAudioPolicy: String(payload.generatedAudioPolicy || ""),
        generated_audio_gain_db: Number(payload.generatedAudioGainDb ?? I2V_SOUND_GAIN_DEFAULT_DB),
        generatedAudioGainDb: Number(payload.generatedAudioGainDb ?? I2V_SOUND_GAIN_DEFAULT_DB),
        video_request_payload_preview: {
          sceneId: scene.scene_id,
          route: scene.route,
          resolvedWorkflowKey: payload.resolvedWorkflowKey,
          renderMode: payload.renderMode,
          aspectRatio: payload.aspect_ratio,
          width: payload.width,
          height: payload.height,
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
          statusEndpoint,
          soundPromptPreview: String(out?.payloadSoundPromptPreview || payload.soundPrompt || "").slice(0, 180),
          negativeAudioPromptPreview: String(out?.payloadNegativeAudioPromptPreview || payload.negativeAudioPrompt || payload.negative_audio_prompt || "").slice(0, 180),
          sceneTextPreview: String(sceneTextPreview || "").slice(0, 180),
          narratorTextPreview: String(payload.narratorText || "").slice(0, 180),
          speakerTextPreview: String(payload.speakerText || "").slice(0, 180),
        },
        videoRequestPayloadPreview: {
          sceneId: scene.scene_id,
          route: scene.route,
          resolvedWorkflowKey: payload.resolvedWorkflowKey,
          renderMode: payload.renderMode,
          aspectRatio: payload.aspect_ratio,
          width: payload.width,
          height: payload.height,
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
          statusEndpoint,
          soundPromptPreview: String(out?.payloadSoundPromptPreview || payload.soundPrompt || "").slice(0, 180),
          negativeAudioPromptPreview: String(out?.payloadNegativeAudioPromptPreview || payload.negativeAudioPrompt || payload.negative_audio_prompt || "").slice(0, 180),
          sceneTextPreview: String(sceneTextPreview || "").slice(0, 180),
          narratorTextPreview: String(payload.narratorText || "").slice(0, 180),
          speakerTextPreview: String(payload.speakerText || "").slice(0, 180),
        },
      });
      flushManualBoardAutosave("video_job_started", { skipSignatureCheck: true });
      if (runningKey) videoStartInFlightRef.current.delete(runningKey);
      const resumedKey = `${scene.scene_id}:${jobId}`;
      resumedVideoJobsRef.current.add(resumedKey);
      pollManualSceneVideo(scene.scene_id, jobId, 0, statusEndpoint);
    } catch (err) {
      if (runningKey) videoStartInFlightRef.current.delete(runningKey);
      updateScene(scene.scene_id, { status: "video_error", video_error: String(err?.message || "video_start_failed"), error: String(err?.message || "video_start_failed") });
    }
  };

  const onResumeVideoJob = async (scene) => {
    const sceneId = String(scene?.scene_id || scene?.id || "").trim();
    const jobId = String(scene?.video_job_id || scene?.videoJobId || "").trim();
    if (!sceneId || !jobId) return;
    try {
      const out = await resumeManualSceneVideoJob(jobId);
      if (out?.ok === false) throw new Error(String(out?.hint || out?.error || out?.code || "video_job_resume_failed"));
      const nextJobId = resolveVideoStartJobId(out) || jobId;
      const statusEndpoint = resolveManualSceneVideoStatusEndpoint(nextJobId, out?.statusEndpoint || out?.status_endpoint || scene?.video_status_endpoint || scene?.videoStatusEndpoint || "");
      const pollKey = `${sceneId}:${nextJobId}`;
      terminalVideoJobsRef.current.delete(pollKey);
      videoPollErrorCountRef.current.delete(pollKey);
      updateScene(sceneId, {
        status: "video_queued",
        video_status: "queued",
        videoStatus: "queued",
        video_job_id: nextJobId,
        videoJobId: nextJobId,
        video_status_endpoint: statusEndpoint,
        videoStatusEndpoint: statusEndpoint,
        video_error: "",
        videoError: "",
        error: "",
      }, { reason: "video_job_resumed" });
      flushManualBoardAutosave("video_job_resumed", { skipSignatureCheck: true });
      pollManualSceneVideo(sceneId, nextJobId, 0, statusEndpoint);
    } catch (err) {
      const message = String(err?.message || "video_job_resume_failed");
      updateScene(sceneId, {
        status: "video_error",
        video_job_id: jobId,
        videoJobId: jobId,
        video_error: message,
        videoError: message,
        error: message,
      }, { reason: "video_job_resume_failed" });
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
    console.info("[MANUAL BOARD DISPLAY VIDEO FIELD CHECK]", {
      sceneId: selectedScene?.scene_id,
      video_url: selectedScene?.video_url,
      videoUrl: selectedScene?.videoUrl,
      generated_video_url: selectedScene?.generated_video_url,
      generatedVideoUrl: selectedScene?.generatedVideoUrl,
      result_video_url: selectedScene?.result_video_url,
      resultVideoUrl: selectedScene?.resultVideoUrl,
      final_video_url: selectedScene?.final_video_url,
      finalVideoUrl: selectedScene?.finalVideoUrl,
    });
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

  const handleBackToManualTiming = () => {
    const currentProject = { ...(projectRef.current || project || {}), selectedSceneId };
    const ownerNodeId = getProjectOwnerNodeId(currentProject);
    const forceState = getManualBoardForceState(currentProject);
    const saved = safePersistCurrentProject("back_to_manual_timing");
    writeManualClipBoardOpenState({
      isOpen: true,
      sourceNodeId: ownerNodeId,
      selectedSceneId: String(currentProject.selectedSceneId || currentProject.scenes?.[0]?.scene_id || "").trim(),
      project_id: forceState.manualBoardForceProjectId,
      input_signature: forceState.manualBoardForceInputSignature,
      audio_signature: forceState.manualBoardForceAudioSignature,
      forceProjectId: forceState.manualBoardForceProjectId,
      forceInputSignature: forceState.manualBoardForceInputSignature,
      forceAudioSignature: forceState.manualBoardForceAudioSignature,
      routePath: "/studio/storyboard",
      reason: "back_to_manual_timing",
      updatedAt: Date.now(),
    });
    const returnManualTimingProject = currentProject?.source_manual_timing_return_project || null;
    const returnManualTimingProjectId = String(currentProject?.source_manual_timing_project_id || currentProject?.sourceManualTimingProjectId || "").trim();
    const returnManualTimingInputSignature = String(currentProject?.source_manual_timing_input_signature || currentProject?.sourceManualTimingInputSignature || "").trim();
    const returnManualTimingAudioSignature = String(currentProject?.source_manual_timing_audio_signature || currentProject?.sourceManualTimingAudioSignature || "").trim();
    const returnManualTimingSelectedSceneId = String(currentProject?.source_manual_timing_selected_scene_id || currentProject?.sourceManualTimingSelectedSceneId || "").trim();
    console.info("[MANUAL BOARD BACK RETURN TIMING SNAPSHOT]", {
      ownerNodeId,
      hasReturnManualTimingProject: Boolean(returnManualTimingProject),
      returnProjectId: returnManualTimingProjectId,
      returnInputSignature: returnManualTimingInputSignature,
      returnAudioSignature: returnManualTimingAudioSignature,
    });
    console.info("[MANUAL BOARD BACK TO TIMING]", {
      sourceNodeId: ownerNodeId,
      ownerNodeId,
      saved,
      ...forceState,
    });
    navigate("/studio/manual-timing", {
      state: {
        sourceNodeId: ownerNodeId,
        ownerNodeId,
        returnFromStoryboard: true,
        openManualTimingNode: true,
        focusManualTimingNodeId: ownerNodeId,
        returnManualTimingProject,
        returnManualTimingProjectId,
        returnManualTimingInputSignature,
        returnManualTimingAudioSignature,
        returnManualTimingSelectedSceneId,
        ...forceState,
        forceProjectId: forceState.manualBoardForceProjectId,
        forceInputSignature: forceState.manualBoardForceInputSignature,
        forceAudioSignature: forceState.manualBoardForceAudioSignature,
      },
    });
  };

  const handleBackToManualTimingNode = () => {
    const currentProject = { ...(projectRef.current || project || {}), selectedSceneId };
    const ownerNodeId = getProjectOwnerNodeId(currentProject);
    const forceState = getManualBoardForceState(currentProject);
    safePersistCurrentProject("back_to_manual_timing_node");
    writeManualClipBoardOpenState({
      isOpen: false,
      sourceNodeId: ownerNodeId,
      selectedSceneId: String(currentProject.selectedSceneId || currentProject.scenes?.[0]?.scene_id || "").trim(),
      project_id: forceState.manualBoardForceProjectId,
      input_signature: forceState.manualBoardForceInputSignature,
      audio_signature: forceState.manualBoardForceAudioSignature,
      forceProjectId: forceState.manualBoardForceProjectId,
      forceInputSignature: forceState.manualBoardForceInputSignature,
      forceAudioSignature: forceState.manualBoardForceAudioSignature,
      routePath: "/studio/storyboard",
      reason: "back_to_manual_timing_node",
      updatedAt: Date.now(),
    });
    navigate("/studio/storyboard", {
      state: {
        focusManualTimingNodeId: ownerNodeId,
        sourceNodeId: ownerNodeId,
        ownerNodeId,
        closeManualDirectorBoard: true,
        closeLegacyScenarioEditors: true,
        manualBoardSkipOpenStateReason: "back_to_manual_timing_node",
        ...forceState,
      },
    });
    if (embedded && typeof onClose === "function") onClose();
  };

  const handleLegacyBackToAiSplit = () => {
    safePersistCurrentProject(embedded ? "close_embedded_director_board" : "back_to_ai_split");
    if (embedded && typeof onClose === "function") {
      onClose();
      return;
    }
    navigate("/studio/storyboard");
  };

  if (!project) return <div className="manualDirectorPage"><div className="manualDirectorEmpty"><h2>Проект режиссёрской доски не найден</h2><p>Сначала откройте AI-разбивку и нажмите «Перейти в режиссёрскую доску» или восстановите backup JSON.</p><div className="manualDirectorEmptyActions"><button className="clipSB_btn" onClick={() => (typeof onClose === "function" ? onClose() : navigate("/studio/storyboard"))}>Вернуться в студию</button><label className="clipSB_btn manualUploadBtn">Импорт backup / storyboard JSON<input type="file" accept=".json,application/json" hidden onChange={onImportProjectBackupFile} /></label><button className="clipSB_btn clipSB_btnSecondary" onClick={onRestoreLegacyManualProject}>Восстановить старый проект</button></div>{backupStatus ? <span className="manualDirectorBackupStatus">{backupStatus}</span> : null}</div></div>;

  return <div className="manualDirectorPage">
    <div className="manualDirectorTopbar">
      <button
        className="clipSB_btn"
        onClick={isManualTimingProjectSource(project) ? handleBackToManualTiming : handleLegacyBackToAiSplit}
      >
        {isManualTimingProjectSource(project) ? "← Назад в тайминг" : "Назад к AI-разбивке"}
      </button>
      {isManualTimingProjectSource(project) ? <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={handleBackToManualTimingNode}>← К ноде</button> : null}
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
      <span className={`manualDirectorAutosaveStatus ${autosaveStatus === "Ошибка autosave" || autosaveStatus.startsWith("Autosave переполнен") ? "isError" : ""}`} title={autosaveError || undefined} aria-live="polite">{autosaveStatus}</span>
      {autosaveStatus === "Ошибка autosave" ? <button className="clipSB_btn" onClick={onCleanupOldBrowserBackups}>Очистить старые backup в браузере</button> : null}
      {showEmergencyBackupButton ? <button className="clipSB_btn clipSB_btnDanger" onClick={() => downloadEmergencyBoardBackup("manual_director_board_emergency_button")}>Скачать аварийный backup</button> : null}
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
          <select value={selectedScene.route || "i2v"} onChange={(e) => {
            const route = e.target.value;
            console.info("[MANUAL BOARD ROUTE USER EDIT]", {
              sceneId: selectedScene.scene_id,
              from: selectedScene.route,
              to: route,
              autosave: false,
            });
            updateScene(selectedScene.scene_id, (currentScene = {}) => {
              const generatedAudioEnabled = route === "i2v_sound" || route === "i2v_text" || route === "first_last_sound";

              const routePatch = {
                route,
                keep_generated_audio: generatedAudioEnabled,
                keepGeneratedAudio: generatedAudioEnabled,
                generated_audio_policy: generatedAudioEnabled ? "mix_generated_audio_under_master" : "silent_video_use_master_track",
                generatedAudioPolicy: generatedAudioEnabled ? "mix_generated_audio_under_master" : "silent_video_use_master_track",
                generated_audio_gain_db: Number(currentScene.generated_audio_gain_db ?? currentScene.generatedAudioGainDb ?? I2V_SOUND_GAIN_DEFAULT_DB),
                generatedAudioGainDb: Number(currentScene.generatedAudioGainDb ?? currentScene.generated_audio_gain_db ?? I2V_SOUND_GAIN_DEFAULT_DB),
                start_image_url: isFirstLastRoute(route) ? String(currentScene.start_image_url || currentScene.image_url || "") : currentScene.start_image_url,
                image_url: isFirstLastRoute(route) ? String(currentScene.start_image_url || currentScene.image_url || "") : currentScene.image_url,
              };

              return {
                ...routePatch,
                route_changed_after_video: Boolean(route !== currentScene.route && resolveManualSceneFinalVideoUrl(currentScene)),
                routeChangedAfterVideo: Boolean(route !== currentScene.route && resolveManualSceneFinalVideoUrl(currentScene)),
              };
            }, {
              reason: "route_user_edit",
              autosave: false,
              allowEmptyPromptModelOverwrite: true,
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

        <label className="manualPromptBlock">Video / motion prompt<textarea value={selectedScene.video_prompt || ""} onChange={(e) => {
          const value = e.target.value;
          const nextScene = { ...selectedScene, video_prompt: value };
          updateScene(
            selectedScene.scene_id,
            { video_prompt: value, status: resolveManualSceneStatus(nextScene) },
            {
              reason: "prompt_user_edit",
              allowEmptyPromptModelOverwrite: true,
              autosave: false,
            }
          );
        }} /></label>
        <label className="manualNegativePromptBlock">Negative Prompt<textarea value={selectedScene.negative_prompt || ""} onChange={(e) => {
          const value = e.target.value;
          const nextScene = { ...selectedScene, negative_prompt: value };
          updateScene(
            selectedScene.scene_id,
            { negative_prompt: value, status: resolveManualSceneStatus(nextScene) },
            {
              reason: "prompt_user_edit",
              allowEmptyPromptModelOverwrite: true,
              autosave: false,
            }
          );
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
          {selectedScene.route === "ia2v" ? <button className="clipSB_btn" onClick={() => onExtractSceneAudioSlice(selectedScene)}>{selectedScene.audio_slice_url ? "Переизъять аудио" : "Изъять аудио"}</button> : null}
          {selectedScene.route === "ia2v" && selectedScene.audio_slice_url ? <span className="manualAudioReady">Аудио сцены готово</span> : null}
          {selectedScene.route === "ia2v" && selectedScene.audio_extracted ? <span className="manualAudioExtracted">Аудио изъято · готово к ia2v</span> : null}
          <button className="clipSB_btn" disabled={selectedImageAspectMismatch || ["video_queued", "video_running"].includes(String(selectedScene.status || "").toLowerCase())} onClick={() => onCreateVideo(selectedScene)}>
            {String(selectedScene.status || "").toLowerCase() === "video_queued" ? "В очереди" : String(selectedScene.status || "").toLowerCase() === "video_running" ? "Генерация идёт" : "Создать видео"}
          </button>
          <button className="clipSB_btn" disabled={selectedSceneIndex <= 0 || !!selectedMMAudioSourceVideoUrl} onClick={() => onUsePreviousLastFrame(selectedScene)}>Взять последний кадр предыдущей</button>
          <button className="clipSB_btn" disabled={!selectedMMAudioSourceVideoUrl} onClick={() => onDeleteSceneVideo(selectedScene)}>{selectedMMAudioSourceVideoUrl ? "Удалить видео" : "Видео нет"}</button>
        </div>
        {selectedScene.error ? <div className="manualError">{selectedScene.error}</div> : null}
        {selectedScene.route === "ia2v" && !selectedScene.audio_slice_url && audioUrl && selectedScene.audio_source_mode === "master_audio_range"
          ? <div className="manualVideoInfo">Аудио slice ещё не создан, предпрослушка идёт из основного аудио.</div>
          : null}
        {isManualVideoInterruptedError(selectedScene) ? <div className="manualVideoInfo">job прерван после рестарта backend — можно возобновить</div> : null}
        {isManualVideoInterruptedError(selectedScene) ? <button type="button" className="clipSB_btn" onClick={() => onResumeVideoJob(selectedScene)}>Возобновить job</button> : null}
        {(["video_queued", "video_running", "video_error"].includes(selectedScene.status)) ? <div className="manualVideoDebug">job: {selectedScene.video_job_id || "—"} · route: {selectedScene.route} · workflow: {selectedScene.video_request_payload_preview?.resolvedWorkflowKey || "—"} · audioSlice: {selectedScene.video_request_payload_preview?.hasAudioSliceUrl ? "yes" : "no"} · keepAudio: {selectedScene.video_request_payload_preview?.keepGeneratedAudio ? "yes" : "no"} · gain: {selectedScene.video_request_payload_preview?.generatedAudioGainDb ?? selectedScene.generated_audio_gain_db ?? "—"} dB{isManualVideoInterruptedError(selectedScene) ? " · job прерван после рестарта backend — можно возобновить" : ""}</div> : null}
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
