import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  addEdge,
  applyEdgeChanges,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./ClipStoryboardPage.css";

import { API_BASE, fetchJson } from "../services/api";
import { getScenarioMusicApiConfig, requestScenarioBackgroundMusic } from "../services/scenarioMusicApi";
import { useAuth } from "../app/AuthContext";
import { useNavigate } from "react-router-dom";
import {
  BrainPackageTesterNode,
  ComfyBrainNode,
  ComfyNarrativeNode,
  ComfyStoryboardNode,
  ComfyVideoPreviewNode,
  MusicPromptTesterNode,
  RefAnimalNode,
  RefCharacter2Node,
  RefCharacter3Node,
  RefGroupNode,
  RefLiteNode,
  ScenarioStoryboardNode,
  ScenarioOutputTesterNode,
  VoiceOutputTesterNode,
} from "./clip_nodes/comfy";
import VideoRefNode from "./clip_nodes/VideoRefNode";
import {
  normalizeRenderProfile,
  normalizeAudioStoryMode,
  deriveSceneRoles,
  canGenerateComfyImage,
  inferPropAnchorLabel,
  buildComfyGlobalContinuity,
  buildMockComfyScenes,
  deriveComfyBrainState,
  normalizeComfyGenre,
  extractComfyDebugFields,
  normalizeStoryboardSourceValue,
  normalizeComfyScenePrompts,
  PROMPT_SYNC_STATUS,
} from "./clip_nodes/comfy/comfyBrainDomain";
import { formatRefProfileDetails } from "./clip_nodes/comfy/refProfileDetails";
import { buildScenarioDirectorRequestPayload, getDefaultNarrativeNodeData, normalizeScenarioDirectorApiResponse, resolveNarrativeSource } from "./clip_nodes/comfy/comfyNarrativeDomain";
import {
  buildScenarioHumanVisualAnchors,
  buildScenarioPreviewInput,
  detectScenarioAssetType,
  deriveScenarioImageStrategy,
  normalizeScenarioStoryboardPackage,
  resolveSceneDisplayTime,
  resolveScenarioExplicitModelKey,
  resolveScenarioExplicitWorkflowKey,
  resolveScenarioWorkflowKey,
} from "./clip_nodes/comfy/scenarioStoryboardDomain";
import ScenarioStoryboardEditor from "./clip_nodes/comfy/ScenarioStoryboardEditor";


// -------------------------
// typed ports + colors (for clear wiring)
// -------------------------
const PORT_COLORS = {
  audio: "var(--family-audio)",
  text: "var(--family-text)",
  link: "var(--family-link)",
  video_ref: "var(--family-video-ref)",
  text_in: "var(--family-text)",
  audio_in: "var(--family-audio)",
  video_file_in: "var(--family-video-ref)",
  video_link_in: "var(--family-link)",
  link_in: "var(--family-link)",
  video_ref_in: "var(--family-video-ref)",
  storyboard_out: "var(--family-narrative)",
  preview_out: "var(--family-narrative)",
  scenario_out: "var(--family-narrative)",
  scenario_storyboard_in: "var(--family-narrative)",
  scenario_storyboard_out: "var(--family-storyboard)",
  scenario_preview_in: "var(--family-narrative)",
  voice_script_out: "var(--family-audio)",
  brain_package_out: "var(--family-brain)",
  brain_package: "var(--family-brain)",
  bg_music_prompt_out: "var(--family-music)",
  ref_character: "var(--family-ref-character)",
  ref_character_1: "var(--family-ref-character)",
  ref_character_2: "var(--family-ref-character)",
  ref_character_3: "var(--family-ref-character)",
  ref_animal: "var(--family-ref-animal)",
  ref_group: "var(--family-ref-group)",
  ref_location: "var(--family-ref-location)",
  ref_style: "var(--family-ref-style)",
  ref_items: "var(--family-ref-items)",
  ref_props: "var(--family-ref-items)",
  plan: "var(--family-brain)",
  comfy_plan: "var(--family-brain)",
  comfy_storyboard: "var(--family-storyboard)",
  comfy_video: "var(--family-generation)",
  intro_context: "var(--family-text)",
  intro_frame: "var(--family-generation)",
  intro_to_assembly: "var(--family-generation)",
  brain_to_storyboard: "var(--family-brain)",
  storyboard_to_assembly: "var(--family-storyboard)",
  assembly: "var(--family-assembly)",
  brain: "var(--family-brain)",
};

const HANDLE_BASE_STYLE = {
  width: 12,
  height: 12,
  borderRadius: 999,
  border: "2px solid rgba(255,255,255,0.82)",
  opacity: 1,
};

const humanizeComfyErrorCode = (errorCode) => {
  const code = String(errorCode || "").trim();
  if (!code) return "";
  if (code === "no_story_source") return "Нет audio или text для построения storyboard";
  if (code === "gemini_model_not_supported") return "Gemini model is not supported for generateContent";
  if (code === "gemini_invalid_json") return "Gemini вернул невалидный JSON";
  if (code === "gemini_request_failed") return "Gemini request failed";
  if (code === "gemini_api_key_missing") return "GEMINI_API_KEY is missing";
  if (code.startsWith("gemini_http_error:")) {
    const httpStatus = code.split(":")[1] || "unknown";
    return `Gemini request failed with HTTP ${httpStatus}`;
  }
  return code;
};

const compactComfyErrorMessage = (response) => {
  const sanitizedError = String(response?.debug?.sanitizedError || "").trim();
  if (sanitizedError) return sanitizedError;
  const firstError = Array.isArray(response?.errors) ? response.errors.find(Boolean) : "";
  return humanizeComfyErrorCode(firstError) || "COMFY parse failed";
};

const normalizeStoryboardSourcesForUi = ({ narrativeSource, storySource } = {}) => {
  const normalizedNarrativeSource = normalizeStoryboardSourceValue(narrativeSource, "none");
  const normalizedStorySource = normalizeStoryboardSourceValue(storySource, normalizedNarrativeSource);
  return {
    narrativeSource: normalizedNarrativeSource,
    storySource: normalizedStorySource,
  };
};

const COMFY_BRAIN_REF_HANDLE_CONFIG = {
  ref_character_1: { sourceType: "refNode", sourceHandle: "ref_character" },
  ref_character_2: { sourceType: "refCharacter2", sourceHandle: "ref_character_2" },
  ref_character_3: { sourceType: "refCharacter3", sourceHandle: "ref_character_3" },
  ref_animal: { sourceType: "refAnimal", sourceHandle: "ref_animal" },
  ref_group: { sourceType: "refGroup", sourceHandle: "ref_group" },
  ref_location: { sourceType: "refNode", sourceHandle: "ref_location" },
  ref_style: { sourceType: "refNode", sourceHandle: "ref_style" },
  ref_props: { sourceType: "refNode", sourceHandle: "ref_items" },
};

const COMFY_STORYBOARD_MAIN_HANDLE = "comfy_scene_video_out";
const COMFY_STORYBOARD_INTRO_HANDLE = "comfy_storyboard_intro_out";
const INTRO_FRAME_STORY_HANDLE = "story_context";

const CLIP_TRACE_PERSIST = false;
const CLIP_TRACE_VIDEO_POLLING = false;
const CLIP_TRACE_COMFY_REFS = false;
const CLIP_TRACE_BRAIN_REFRESH = false;
const CLIP_TRACE_GRAPH_HYDRATE = false;
const CLIP_TRACE_ASSEMBLY_SOURCE = false;
const CLIP_TRACE_SCENARIO_TRANSFER = false;
const CLIP_TRACE_VISUAL_LOCK = false;
const CLIP_TRACE_SCENARIO_FORMAT = false;
const CLIP_TRACE_SCENARIO_GRAPH = false;
const CLIP_TRACE_INTRO_PREVIEW = false;
const CLIP_TRACE_SCENARIO_GLOBAL_MUSIC = false;
const CLIP_TRACE_SCENARIO_EDITOR_GENERATE = true;
const CLIP_TRACE_SCENARIO_IMAGE_PAYLOAD = false;
const CLIP_TRACE_SCENARIO_SCENE_ASSETS = false;
const CLIP_TRACE_SCENARIO_IMAGE_E2E = false;
const CLIP_TRACE_ROLE_CONTRACT_SCENE_ID = "TRACE_SCENE_2P_001";

function shouldTraceRoleContractScene(sceneId = "") {
  const needle = String(CLIP_TRACE_ROLE_CONTRACT_SCENE_ID || "").trim();
  if (!needle) return false;
  return String(sceneId || "").trim() === needle;
}
const SCENARIO_DIRECTOR_TIMEOUT_MS = 90_000;
const VIDEO_START_TIMEOUT_MS = 25_000;
const VIDEO_STATUS_TIMEOUT_MS = 15_000;
const GLOBAL_FORBIDDEN_CHANGES_GUARDS = [
  "no change in lighting style",
  "no change in color grading",
  "no change in capture quality",
  "no change in camera language",
  "no exposure drift",
  "no dynamic range drift",
  "no sharpness regime shift",
];

const normalizeScenarioWorkflowKeyForProduction = (value) => {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "i2v_as") return "i2v";
  if (normalized === "f_l_as") return "f_l";
  if (normalized === "lip_sync") return "lip_sync_music";
  return normalized;
};

const normalizeDirectRouteToWorkflowKey = (value) => {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return "";
  if (["first_last", "first-last", "f_l"].includes(normalized)) return "f_l";
  if (["lip_sync_music", "lip_sync"].includes(normalized)) return "lip_sync_music";
  if (["i2v", "image_video"].includes(normalized)) return "i2v";
  return "";
};

const resolveSceneDirectRouteSource = (scene = {}) => {
  const sourceRoute = normalizeDirectRouteToWorkflowKey(scene?.sourceRoute || scene?.source_route);
  if (sourceRoute) return { workflowKey: sourceRoute, source: "sourceRoute" };
  const videoRoute = normalizeDirectRouteToWorkflowKey(scene?.videoGenerationRoute || scene?.video_generation_route);
  if (videoRoute) return { workflowKey: videoRoute, source: "video_generation_route" };
  const plannedRoute = normalizeDirectRouteToWorkflowKey(scene?.plannedVideoGenerationRoute || scene?.planned_video_generation_route);
  if (plannedRoute) return { workflowKey: plannedRoute, source: "planned_video_generation_route" };
  return { workflowKey: "", source: "legacy" };
};

const GLOBAL_FORBIDDEN_INSERTIONS_GUARDS = [
  "do not introduce a different visual style",
  "do not introduce a different lens feel",
  "do not introduce a different production look",
];

function traceScenarioGraphConnect(eventType = "rejected", { sourceType = "", sourceHandle = "", targetType = "", targetHandle = "" } = {}) {
  if (!CLIP_TRACE_SCENARIO_GRAPH) return;
  console.debug(`[SCENARIO GRAPH STRICT] connect ${eventType}`, {
    sourceType: String(sourceType || ""),
    sourceHandle: String(sourceHandle || ""),
    targetType: String(targetType || ""),
    targetHandle: String(targetHandle || ""),
  });
}

function normalizeScenarioStringList(value) {
  const normalizeScalar = (item) => {
    if (typeof item === "string") return item.trim();
    if (typeof item === "number" || typeof item === "boolean") return String(item).trim();
    return "";
  };
  if (Array.isArray(value)) {
    return Array.from(new Set(value.map((item) => normalizeScalar(item)).filter(Boolean)));
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? [trimmed] : [];
  }
  if (typeof value === "number" || typeof value === "boolean") return [String(value).trim()];
  return [];
}

function mergeScenarioStringLists(...lists) {
  return Array.from(new Set(lists.flatMap((list) => normalizeScenarioStringList(list)).filter(Boolean)));
}

function stringifyScenarioLockValue(value) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value).trim();
  if (Array.isArray(value)) return normalizeScenarioStringList(value).join(", ");
  if (value && typeof value === "object") {
    return normalizeScenarioStringList(Object.values(value)).join(", ");
  }
  return "";
}

function buildScenarioVisualGlueText(scene = {}) {
  const globalVisualLock = scene?.globalVisualLock && typeof scene.globalVisualLock === "object" ? scene.globalVisualLock : {};
  const globalCameraProfile = scene?.globalCameraProfile && typeof scene.globalCameraProfile === "object" ? scene.globalCameraProfile : {};
  const environmentLockText = stringifyScenarioLockValue(scene?.environmentLock);
  const styleLockText = stringifyScenarioLockValue(scene?.styleLock);
  const identityLockText = stringifyScenarioLockValue(scene?.identityLock);
  const visualConsistencyLines = [
    globalVisualLock?.productionConsistency || "all scenes must feel captured by the same production setup",
    globalVisualLock?.cameraLanguage || "controlled cinematic camera language",
    globalVisualLock?.lightingStyle || "soft directional key, controlled contrast, realistic bounce light",
    globalVisualLock?.colorGrade || "natural cinematic grade, balanced contrast, soft highlight rolloff",
    globalVisualLock?.imageDensity || "high-end clean detailed natural texture",
    ...normalizeScenarioStringList(globalVisualLock?.forbiddenDrift).slice(0, 2),
    environmentLockText ? "maintain environment continuity" : "",
    styleLockText ? "preserve style continuity" : "",
    identityLockText ? "preserve identity continuity" : "",
  ].filter(Boolean).slice(0, 8);
  const cameraProfileLines = [
    globalCameraProfile?.lensProfile || globalVisualLock?.lensFeel || "cinematic medium focal lens feel",
    globalCameraProfile?.exposureProfile || "balanced exposure with protected highlights",
    globalCameraProfile?.dynamicRangeProfile || "wide dynamic range feel with soft highlight rolloff",
    globalCameraProfile?.sharpnessProfile || "natural premium detail without over-sharpening",
    globalCameraProfile?.textureProfile || "consistent premium texture fidelity across scenes",
    globalCameraProfile?.motionProfile ? `imply ${globalCameraProfile.motionProfile}` : "",
    ...normalizeScenarioStringList(globalCameraProfile?.forbiddenCameraDrift).slice(0, 2),
    globalCameraProfile?.continuityProfile || "same capture system feel across the entire sequence",
  ].filter(Boolean).slice(0, 8);
  return [
    "GLOBAL VISUAL CONSISTENCY:",
    ...visualConsistencyLines.map((line) => `- ${line}`),
    "",
    "HARD CAMERA PROFILE:",
    ...cameraProfileLines.map((line) => `- ${line}`),
  ].join("\n").trim();
}

function buildScenarioVideoVisualGlueText(scene = {}) {
  const globalVisualLock = scene?.globalVisualLock && typeof scene.globalVisualLock === "object" ? scene.globalVisualLock : {};
  const globalCameraProfile = scene?.globalCameraProfile && typeof scene.globalCameraProfile === "object" ? scene.globalCameraProfile : {};
  const videoConsistencyLines = [
    globalVisualLock?.cameraLanguage || "continuous camera language across scenes",
    globalVisualLock?.lightingStyle || "consistent lighting and atmosphere",
    globalVisualLock?.colorGrade || "consistent palette and tonal balance",
    globalVisualLock?.productionConsistency || "matching production quality and image density",
    ...normalizeScenarioStringList(globalVisualLock?.forbiddenDrift).slice(0, 2),
  ].filter(Boolean).slice(0, 6);
  const cameraProfileLines = [
    globalCameraProfile?.lensProfile || globalVisualLock?.lensFeel || "maintain consistent lens behavior",
    globalCameraProfile?.exposureProfile || "maintain stable exposure and contrast feel",
    globalCameraProfile?.dynamicRangeProfile || "preserve highlight rolloff and dynamic range feel",
    globalCameraProfile?.textureProfile || "keep natural premium detail level",
    globalCameraProfile?.motionProfile || "maintain a controlled cinematic motion language",
    ...normalizeScenarioStringList(globalCameraProfile?.forbiddenCameraDrift).slice(0, 3),
    globalCameraProfile?.continuityProfile || "keep the sequence visually unified",
  ].filter(Boolean).slice(0, 8);
  return [
    "GLOBAL VIDEO CONSISTENCY:",
    ...videoConsistencyLines.map((line) => `- ${line}`),
    "",
    "HARD CAMERA PROFILE:",
    ...cameraProfileLines.map((line) => `- ${line}`),
  ].join("\n").trim();
}

function hasScenarioContractValue(value) {
  if (Array.isArray(value)) return value.length > 0;
  if (value && typeof value === "object") return Object.keys(value).length > 0;
  if (typeof value === "string") return value.trim().length > 0;
  return value !== undefined && value !== null;
}

function buildScenarioSceneContractPayload(scene = {}) {
  const forbiddenInsertions = mergeScenarioStringLists(scene?.forbiddenInsertions, GLOBAL_FORBIDDEN_INSERTIONS_GUARDS);
  const forbiddenChanges = mergeScenarioStringLists(scene?.forbiddenChanges, GLOBAL_FORBIDDEN_CHANGES_GUARDS);
  const imageStrategy = String(scene?.imageStrategy || deriveScenarioImageStrategy(scene)).trim().toLowerCase() || "single";
  const explicitWorkflow = resolveScenarioExplicitWorkflowKey(scene);
  const directRouteInfo = resolveSceneDirectRouteSource(scene);
  const resolvedWorkflowKey = normalizeScenarioWorkflowKeyForProduction(
    directRouteInfo.workflowKey || scene?.resolvedWorkflowKey || explicitWorkflow || resolveScenarioWorkflowKey(scene)
  );
  const explicitModel = resolveScenarioExplicitModelKey(scene);
  const resolvedModelKey = String(scene?.resolvedModelKey || explicitModel).trim();
  const requestedDurationSec = Number(
    scene?.requestedDurationSec
      ?? scene?.durationSec
      ?? Math.max(0, Number(scene?.t1 ?? 0) - Number(scene?.t0 ?? 0))
  );
  const rawSceneIndex = Number(scene?.sceneIndex ?? scene?.scene_index ?? scene?.index);
  const resolvedSceneIndex = Number.isFinite(rawSceneIndex) && rawSceneIndex > 0
    ? Math.floor(rawSceneIndex)
    : null;
  const identityContractPassthrough = {
    taskMode: scene?.taskMode,
    task_mode: scene?.task_mode,
    sourceOutfitProfile: scene?.sourceOutfitProfile,
    targetOutfitProfile: scene?.targetOutfitProfile,
    effectiveOutfitProfile: scene?.effectiveOutfitProfile,
    outfitProfile: scene?.outfitProfile,
    sourceOutfitReplaced: scene?.sourceOutfitReplaced,
    outfitIdentitySource: scene?.outfitIdentitySource,
    confidenceScores: scene?.confidenceScores,
    heroAppearanceContract: scene?.heroAppearanceContract,
    worldContinuityContract: scene?.worldContinuityContract,
    locationContinuityContract: scene?.locationContinuityContract,
    styleContinuityContract: scene?.styleContinuityContract,
    previousStableImageAnchorApplied: scene?.previousStableImageAnchorApplied,
    previousStableImageAnchorAvailable: scene?.previousStableImageAnchorAvailable,
    previousStableImageAnchorUrlResolved: scene?.previousStableImageAnchorUrlResolved,
    previousStableImageAnchorUsed: scene?.previousStableImageAnchorUsed,
    previousStableImageAnchorReason: scene?.previousStableImageAnchorReason,
    audioEmotionDirection: scene?.audioEmotionDirection,
  };
  return {
    sceneId: scene?.sceneId || "",
    sceneIndex: resolvedSceneIndex,
    scene_index: resolvedSceneIndex,
    index: resolvedSceneIndex,
    sceneType: scene?.sceneType,
    primaryRole: scene?.primaryRole,
    secondaryRoles: scene?.secondaryRoles,
    sceneActiveRoles: scene?.sceneActiveRoles,
    refsUsed: scene?.refsUsed,
    refDirectives: scene?.refDirectives,
    focalSubject: scene?.focalSubject,
    sceneAction: scene?.sceneAction,
    cameraIntent: scene?.cameraIntent,
    environmentMotion: scene?.environmentMotion,
    forbiddenInsertions,
    forbiddenChanges,
    renderMode: scene?.renderMode,
    ltxMode: scene?.ltxMode,
    transitionType: scene?.transitionType,
    shotType: scene?.shotType,
    roleInfluenceApplied: scene?.roleInfluenceApplied,
    roleInfluenceReason: scene?.roleInfluenceReason,
    sceneRoleDynamics: scene?.sceneRoleDynamics,
    multiCharacterIdentityLock: scene?.multiCharacterIdentityLock,
    distinctCharacterSeparation: scene?.distinctCharacterSeparation,
    appearanceDriftRisk: scene?.appearanceDriftRisk,
    lipSync: scene?.lipSync,
    lipSyncText: scene?.lipSyncText,
    continuity: scene?.continuity,
    worldScaleContext: scene?.worldScaleContext,
    entityScaleAnchors: scene?.entityScaleAnchors,
    environmentLock: scene?.environmentLock,
    styleLock: scene?.styleLock,
    identityLock: scene?.identityLock,
    mustAppear: scene?.mustAppear,
    mustNotAppear: scene?.mustNotAppear,
    heroEntityId: scene?.heroEntityId,
    supportEntityIds: scene?.supportEntityIds,
    plannerDebug: scene?.plannerDebug,
    generationHints: scene?.generationHints,
    globalVisualLock: scene?.globalVisualLock,
    globalCameraProfile: scene?.globalCameraProfile,
    modelAssignments: scene?.modelAssignments,
    providerHints: scene?.providerHints,
    audioSliceStartSec: scene?.audioSliceStartSec,
    audioSliceEndSec: scene?.audioSliceEndSec,
    audioDurationSec: scene?.audioDurationSec,
    imageStrategy,
    requiresTwoFrames: Boolean(scene?.requiresTwoFrames ?? scene?.needsTwoFrames ?? imageStrategy === "first_last"),
    requiresContinuation: Boolean(scene?.requiresContinuation ?? scene?.continuationFromPrevious ?? scene?.continuation ?? imageStrategy === "continuation"),
    continuationFromPrevious: Boolean(scene?.continuationFromPrevious ?? scene?.continuation ?? imageStrategy === "continuation"),
    continuationSourceSceneId: String(scene?.continuationSourceSceneId || "").trim(),
    continuationSourceAssetUrl: String(scene?.continuationSourceAssetUrl || "").trim(),
    continuationSourceAssetType: String(scene?.continuationSourceAssetType || "").trim(),
    audioSliceKind: String(scene?.audioSliceKind || scene?.audio_slice_kind || "").trim().toLowerCase(),
    musicVocalLipSyncAllowed: Boolean(scene?.musicVocalLipSyncAllowed ?? scene?.music_vocal_lipsync_allowed),
    performerPresentation: String(scene?.performerPresentation ?? scene?.performer_presentation ?? "").trim(),
    vocalPresentation: String(scene?.vocalPresentation ?? scene?.vocal_presentation ?? "").trim(),
    lipSyncVoiceCompatibility: String(scene?.lipSyncVoiceCompatibility ?? scene?.lip_sync_voice_compatibility ?? "").trim(),
    lipSyncVoiceCompatibilityReason: String(scene?.lipSyncVoiceCompatibilityReason ?? scene?.lip_sync_voice_compatibility_reason ?? "").trim(),
    videoReady: Boolean(scene?.videoReady ?? scene?.video_ready ?? false),
    plannedVideoGenerationRoute: String(scene?.plannedVideoGenerationRoute || scene?.planned_video_generation_route || "").trim().toLowerCase(),
    videoGenerationRoute: String(scene?.videoGenerationRoute || scene?.video_generation_route || "").trim().toLowerCase(),
    sourceRoute: String(scene?.sourceRoute || scene?.source_route || "").trim().toLowerCase(),
    uiRouteSource: directRouteInfo.source,
    videoBlockReasonCode: String(scene?.videoBlockReasonCode || scene?.video_block_reason_code || "").trim(),
    videoBlockReasonMessage: String(scene?.videoBlockReasonMessage || scene?.video_block_reason_message || "").trim(),
    videoDowngradeReasonCode: String(scene?.videoDowngradeReasonCode || scene?.video_downgrade_reason_code || "").trim(),
    videoDowngradeReasonMessage: String(scene?.videoDowngradeReasonMessage || scene?.video_downgrade_reason_message || "").trim(),
    identityLockApplied: Boolean(scene?.identityLockApplied ?? scene?.identity_lock_applied),
    identityLockNotes: String(scene?.identityLockNotes || scene?.identity_lock_notes || "").trim(),
    identityLockFieldsUsed: Array.isArray(scene?.identityLockFieldsUsed ?? scene?.identity_lock_fields_used)
      ? (scene?.identityLockFieldsUsed ?? scene?.identity_lock_fields_used)
      : [],
    requiresAudioSensitiveVideo: resolvedWorkflowKey === "lip_sync_music" || Boolean(scene?.lipSync),
    resolvedWorkflowKey,
    resolvedModelKey,
    requestedDurationSec: Number.isFinite(requestedDurationSec) ? Math.max(0, requestedDurationSec) : undefined,
    ...identityContractPassthrough,
  };
}

function resolveContinuationSourceFromPreviousScene(previousScene = null) {
  const prev = previousScene && typeof previousScene === "object" ? previousScene : {};
  const candidates = [
    { url: prev?.endFrameImageUrl, type: "frame" },
    { url: prev?.endImageUrl, type: "image" },
    { url: prev?.imageUrl, type: "image" },
    { url: prev?.videoUrl, type: "video" },
  ];
  for (const candidate of candidates) {
    const assetUrl = String(candidate?.url || "").trim();
    if (!assetUrl) continue;
    const detectedType = detectScenarioAssetType({ url: assetUrl, preferFrame: candidate.type === "frame" });
    return {
      continuationSourceAssetUrl: assetUrl,
      continuationSourceAssetType: detectedType === "unknown" ? candidate.type : detectedType,
    };
  }
  return {
    continuationSourceAssetUrl: "",
    continuationSourceAssetType: "",
  };
}

function resolveScenarioSceneVideoProvider(scene = {}) {
  const directRouteInfo = resolveSceneDirectRouteSource(scene);
  const rawProvider = String(scene?.sceneRenderProvider || "").trim().toLowerCase();
  const resolvedWorkflowKey = normalizeScenarioWorkflowKeyForProduction(
    directRouteInfo.workflowKey
    || scene?.resolvedWorkflowKey
    || resolveScenarioExplicitWorkflowKey(scene)
    || resolveScenarioWorkflowKey(scene)
    || ""
  );
  const isLipSyncWorkflow = resolvedWorkflowKey === "lip_sync_music";
  const hasLtxContract = Boolean(
    String(scene?.ltxMode || "").trim()
    || String(scene?.resolvedWorkflowKey || "").trim()
    || scene?.continuation
    || scene?.continuationFromPrevious
    || scene?.needsTwoFrames
    || scene?.requiresTwoFrames
    || scene?.requiresContinuation
    || resolvedWorkflowKey === "lip_sync_music"
  );
  if (rawProvider) {
    if (isLipSyncWorkflow) return rawProvider;
    if (hasLtxContract && rawProvider === "kie") return "comfy_remote";
    return rawProvider;
  }
  if (isLipSyncWorkflow) return "kie";
  if (hasLtxContract) return "comfy_remote";
  return "kie";
}

function normalizeScenarioRoleName(value = "") {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) return "";
  const aliases = {
    char_1: "character_1",
    character1: "character_1",
    char_2: "character_2",
    character2: "character_2",
    char_3: "character_3",
    character3: "character_3",
    ref_props: "props",
    ref_items: "props",
    items: "props",
    item: "props",
    objects: "props",
    object: "props",
  };
  return aliases[raw] || raw;
}

const SCENARIO_IMAGE_ROLE_KEYS = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"];

function extractScenarioRefsByRoleFromSource(source = null) {
  if (!source || typeof source !== "object") return Object.fromEntries(SCENARIO_IMAGE_ROLE_KEYS.map((role) => [role, []]));
  const toUrlList = (items) => (Array.isArray(items)
    ? items
      .map((item) => {
        if (typeof item === "string") return item;
        if (item && typeof item === "object") return item?.url || item?.src || item?.imageUrl || "";
        return "";
      })
      .map((value) => String(value || "").trim())
      .filter(Boolean)
    : []);
  const pullRefsFromRoleValue = (roleValue) => {
    if (Array.isArray(roleValue)) return toUrlList(roleValue);
    if (roleValue && typeof roleValue === "object") {
      return toUrlList(
        roleValue.refs
        ?? roleValue.images
        ?? roleValue.urls
        ?? roleValue.items
        ?? roleValue.value
        ?? roleValue.list
        ?? []
      );
    }
    return [];
  };
  const roleMap = Object.fromEntries(SCENARIO_IMAGE_ROLE_KEYS.map((role) => [role, []]));
  Object.entries(source || {}).forEach(([rawRole, roleValue]) => {
    const role = normalizeScenarioRoleName(rawRole);
    if (!SCENARIO_IMAGE_ROLE_KEYS.includes(role)) return;
    roleMap[role] = [...new Set([...(roleMap[role] || []), ...pullRefsFromRoleValue(roleValue)])];
  });
  return roleMap;
}

function mergeScenarioRefsByRole(...sources) {
  const roleMap = Object.fromEntries(SCENARIO_IMAGE_ROLE_KEYS.map((role) => [role, []]));
  sources.forEach((source) => {
    const extracted = extractScenarioRefsByRoleFromSource(source);
    SCENARIO_IMAGE_ROLE_KEYS.forEach((role) => {
      roleMap[role] = [...new Set([...(roleMap[role] || []), ...((extracted || {})[role] || [])])];
    });
  });
  return roleMap;
}

function collectScenarioNarrativeRefs({ sourceNode = null } = {}) {
  const emptyResult = {
    character: [],
    location: [],
    style: [],
    props: [],
    refsByRole: Object.fromEntries(SCENARIO_IMAGE_ROLE_KEYS.map((role) => [role, []])),
    hasNarrativeRefsByRole: false,
    hasNarrativeContextRefs: false,
  };
  if (!sourceNode || sourceNode?.type !== "comfyNarrative") return emptyResult;

  const nodeData = sourceNode?.data && typeof sourceNode.data === "object" ? sourceNode.data : {};
  const outputs = nodeData?.outputs && typeof nodeData.outputs === "object" ? nodeData.outputs : {};
  const pendingOutputs = nodeData?.pendingOutputs && typeof nodeData.pendingOutputs === "object" ? nodeData.pendingOutputs : {};
  const storyboardOut = outputs?.storyboardOut && typeof outputs.storyboardOut === "object" ? outputs.storyboardOut : {};
  const pendingStoryboardOut = pendingOutputs?.storyboardOut && typeof pendingOutputs.storyboardOut === "object" ? pendingOutputs.storyboardOut : {};
  const directorOutput = outputs?.directorOutput && typeof outputs.directorOutput === "object" ? outputs.directorOutput : {};
  const pendingDirectorOutput = pendingOutputs?.directorOutput && typeof pendingOutputs.directorOutput === "object" ? pendingOutputs.directorOutput : {};

  const refsByRole = mergeScenarioRefsByRole(
    nodeData?.refsByRole,
    outputs?.refsByRole,
    pendingOutputs?.refsByRole,
    storyboardOut?.refsByRole,
    pendingStoryboardOut?.refsByRole,
    directorOutput?.refsByRole,
    pendingDirectorOutput?.refsByRole,
    nodeData?.context_refs,
    nodeData?.connected_context_summary?.context_refs,
    outputs?.context_refs,
    outputs?.connected_context_summary?.context_refs,
    pendingOutputs?.context_refs,
    pendingOutputs?.connected_context_summary?.context_refs,
    storyboardOut?.context_refs,
    storyboardOut?.connected_context_summary?.context_refs,
    pendingStoryboardOut?.context_refs,
    pendingStoryboardOut?.connected_context_summary?.context_refs,
    directorOutput?.context_refs,
    directorOutput?.connected_context_summary?.context_refs,
    pendingDirectorOutput?.context_refs,
    pendingDirectorOutput?.connected_context_summary?.context_refs,
  );

  const castRoles = ["character_1", "character_2", "character_3", "animal", "group"];
  const legacyCharacter = [...new Set(castRoles.flatMap((role) => refsByRole?.[role] || []))];
  const hasNarrativeRefsByRole = SCENARIO_IMAGE_ROLE_KEYS.some((role) => (refsByRole?.[role] || []).length > 0);
  const hasNarrativeContextRefs = Object.values(mergeScenarioRefsByRole(
    nodeData?.context_refs,
    nodeData?.connected_context_summary?.context_refs,
    outputs?.context_refs,
    outputs?.connected_context_summary?.context_refs,
    pendingOutputs?.context_refs,
    pendingOutputs?.connected_context_summary?.context_refs,
    storyboardOut?.context_refs,
    storyboardOut?.connected_context_summary?.context_refs,
    pendingStoryboardOut?.context_refs,
    pendingStoryboardOut?.connected_context_summary?.context_refs,
    directorOutput?.context_refs,
    directorOutput?.connected_context_summary?.context_refs,
    pendingDirectorOutput?.context_refs,
    pendingDirectorOutput?.connected_context_summary?.context_refs,
  )).some((urls) => Array.isArray(urls) && urls.length > 0);

  return {
    character: legacyCharacter,
    location: refsByRole?.location || [],
    style: refsByRole?.style || [],
    props: refsByRole?.props || [],
    refsByRole,
    hasNarrativeRefsByRole,
    hasNarrativeContextRefs,
  };
}

function buildScenarioRefsByRoleForImage({ scene = {}, scenarioBrainRefs = {}, scenarioPackage = {} } = {}) {
  const toUrlList = (items) => (Array.isArray(items)
    ? items.map((item) => String(typeof item === "string" ? item : item?.url || "").trim()).filter(Boolean)
    : []);
  const humanRoles = ["character_1", "character_2", "character_3"];
  const enforceHumanRoleIsolation = (roleMapInput = {}) => {
    const outMap = { ...roleMapInput };
    const sharedGroupRefs = new Set(toUrlList(outMap?.group));
    const claimedByUrl = new Map();
    humanRoles.forEach((role) => {
      const nextUrls = [];
      (outMap?.[role] || []).forEach((url) => {
        if (!url) return;
        if (sharedGroupRefs.has(url)) {
          nextUrls.push(url);
          return;
        }
        if (!claimedByUrl.has(url)) {
          claimedByUrl.set(url, role);
          nextUrls.push(url);
        }
      });
      outMap[role] = [...new Set(nextUrls)];
    });
    return outMap;
  };
  const roleMap = Object.fromEntries(SCENARIO_IMAGE_ROLE_KEYS.map((role) => [role, []]));
  const appendFromSource = (source = null) => {
    const extracted = extractScenarioRefsByRoleFromSource(source);
    SCENARIO_IMAGE_ROLE_KEYS.forEach((role) => {
      roleMap[role] = [...new Set([...(roleMap[role] || []), ...((extracted || {})[role] || [])])];
    });
  };

  appendFromSource(scenarioBrainRefs?.refsByRole);
  appendFromSource(scene?.refsByRole);
  appendFromSource(scene?.connectedRefsByRole);
  appendFromSource(scene?.contextRefs);
  appendFromSource(scene?.context_refs);
  appendFromSource(scene?.connectedContextSummary?.context_refs);
  appendFromSource(scene?.connected_context_summary?.context_refs);
  appendFromSource(scene?.sceneMeta?.connected_context_summary?.context_refs);
  appendFromSource(scene?.scene_meta?.connected_context_summary?.context_refs);
  appendFromSource(scenarioPackage?.refsByRole);
  appendFromSource(scenarioPackage?.connectedRefsByRole);
  appendFromSource(scenarioPackage?.cast?.refsByRole);
  appendFromSource(scenarioPackage?.history?.refsByRole);
  appendFromSource(scenarioPackage?.context_refs);
  appendFromSource(scenarioPackage?.connected_context_summary?.context_refs);

  roleMap.location = [...new Set([...(roleMap.location || []), ...toUrlList(scenarioBrainRefs?.location)])];
  roleMap.style = [...new Set([...(roleMap.style || []), ...toUrlList(scenarioBrainRefs?.style)])];
  roleMap.props = [...new Set([...(roleMap.props || []), ...toUrlList(scenarioBrainRefs?.props)])];
  return enforceHumanRoleIsolation(roleMap);
}

function buildScenarioRoleContractForImage({ scene = {}, refsByRole = {} } = {}) {
  const isGroupNarrativelyRequiredByScene = (sceneSource = {}) => {
    const mustAppearScene = normalizeRoleList(sceneSource?.mustAppear);
    if (mustAppearScene.includes("group")) return true;
    const directives = sceneSource?.refDirectives && typeof sceneSource.refDirectives === "object" ? sceneSource.refDirectives : {};
    const directive = String(directives?.group || "").trim().toLowerCase();
    if (["required", "hero"].includes(directive)) return true;
    const crowdSignal = ["protest", "riot", "mob", "audience", "chorus", "crowd chant", "mass panic", "митинг", "толпа", "бунт", "хор", "массов"]
      .some((marker) => `${String(sceneSource?.sceneGoal || "")} ${String(sceneSource?.summaryEn || "")} ${String(sceneSource?.summaryRu || "")} ${String(sceneSource?.frameDescription || "")} ${String(sceneSource?.actionInFrame || "")} ${String(sceneSource?.videoPromptEn || "")} ${String(sceneSource?.videoPromptRu || "")} ${String(sceneSource?.imagePromptEn || "")} ${String(sceneSource?.imagePromptRu || "")}`.toLowerCase().includes(marker));
    return crowdSignal;
  };
  const castRoles = ["character_1", "character_2", "character_3", "animal", "group"];
  const sceneId = String(scene?.sceneId || "").trim();
  const sceneRoleDynamics = String(scene?.sceneRoleDynamics || "").trim().toLowerCase();
  const orderedRoleWithRefs = ["character_1", "character_2", "character_3", "animal", "group"]
    .filter((role) => Array.isArray(refsByRole?.[role]) && refsByRole[role].length > 0);
  const roleWithRefs = [...new Set([...orderedRoleWithRefs, ...castRoles.filter((role) => Array.isArray(refsByRole?.[role]) && refsByRole[role].length > 0)])];
  const normalizeRoleList = (value) => {
    const list = Array.isArray(value) ? value : [];
    return [...new Set(list.map((role) => normalizeScenarioRoleName(role)).filter(Boolean))];
  };
  const primaryRole = normalizeScenarioRoleName(scene?.primaryRole || "");
  const secondaryRoles = normalizeRoleList(scene?.secondaryRoles);
  const sceneActiveRoles = normalizeRoleList(scene?.sceneActiveRoles);
  const refsUsed = Array.isArray(scene?.refsUsed)
    ? normalizeRoleList(scene.refsUsed)
    : (scene?.refsUsed && typeof scene.refsUsed === "object"
      ? normalizeRoleList(Object.keys(scene.refsUsed).filter((role) => !!scene.refsUsed[role]))
      : []);
  const refsUsedByRoleKeys = normalizeRoleList(Object.keys((scene?.refsUsedByRole && typeof scene.refsUsedByRole === "object") ? scene.refsUsedByRole : {}));
  const actorsRoleSignals = normalizeRoleList(Array.isArray(scene?.actors) ? scene.actors : []);
  const semanticTwoPersonSignal = ["gaze", "shared glance", "look into eyes", "eye contact", "whisper", "hold hand", "embrace", "hug", "looks at", "looking at", "див", "смотр", "взгляд", "шепч", "обнима", "держ"]
    .some((marker) => `${String(scene?.summaryEn || "")} ${String(scene?.summaryRu || "")} ${String(scene?.videoPromptEn || "")} ${String(scene?.videoPromptRu || "")} ${String(scene?.imagePromptEn || "")} ${String(scene?.imagePromptRu || "")}`.toLowerCase().includes(marker));
  const mustAppear = normalizeRoleList(scene?.mustAppear);
  const groupNarrativelyRequired = isGroupNarrativelyRequiredByScene(scene);
  const hasActiveHumanRoles = [...sceneActiveRoles, ...mustAppear].some((role) => ["character_1", "character_2", "character_3", "group"].includes(role));
  const isEnvironmentOnlyScene = sceneRoleDynamics === "environment" && actorsRoleSignals.length === 0 && !hasActiveHumanRoles;
  const availableTwoPersonRefs = roleWithRefs.includes("character_1") && roleWithRefs.includes("character_2");
  const shouldForceTwoPerson = availableTwoPersonRefs && (
    semanticTwoPersonSignal
    || actorsRoleSignals.length >= 2
    || refsUsedByRoleKeys.includes("character_2")
    || mustAppear.includes("character_2")
  );
  if (isEnvironmentOnlyScene) {
    const environmentOnlyContract = {
      primaryRole: "",
      secondaryRoles: [],
      sceneActiveRoles: [],
      refsUsed: [],
      mustAppear: [],
      mustNotAppear: ["character_1", "character_2", "character_3", "group"],
      environmentOnly: true,
    };
    if (shouldTraceRoleContractScene(sceneId)) {
      console.debug("[SCENARIO ROLE TRACE] buildScenarioRoleContractForImage.output.environment_only", environmentOnlyContract);
    }
    return environmentOnlyContract;
  }
  const hasExplicitContract = Boolean(primaryRole || secondaryRoles.length || sceneActiveRoles.length || mustAppear.length);
  const explicitActiveRoles = normalizeRoleList([
    primaryRole,
    ...secondaryRoles,
    ...sceneActiveRoles,
    ...refsUsed,
    ...mustAppear,
    ...refsUsedByRoleKeys,
  ]);
  const healedPrimary = primaryRole || explicitActiveRoles[0] || roleWithRefs[0] || "";
  const healedActive = shouldForceTwoPerson
    ? normalizeRoleList([...explicitActiveRoles, "character_1", "character_2"])
    : explicitActiveRoles;
  const healedActiveWithoutGroup = groupNarrativelyRequired ? healedActive : healedActive.filter((role) => role !== "group");
  const healedPrimarySafe = healedActiveWithoutGroup.includes(healedPrimary) ? healedPrimary : (healedActiveWithoutGroup[0] || "");
  const healedSecondary = healedActiveWithoutGroup.filter((role) => role !== healedPrimarySafe);
  const healedMustAppear = normalizeRoleList([
    ...mustAppear,
    ...(shouldForceTwoPerson ? ["character_1", "character_2"] : healedActiveWithoutGroup),
  ]).filter((role) => healedActiveWithoutGroup.includes(role));
  const healedRefsUsed = normalizeRoleList([...refsUsed, ...healedActiveWithoutGroup]).filter((role) => groupNarrativelyRequired || role !== "group");
  if (shouldTraceRoleContractScene(sceneId)) {
    console.debug("[SCENARIO ROLE TRACE] buildScenarioRoleContractForImage.input", {
      sceneId,
      incomingPrimaryRole: primaryRole,
      incomingSecondaryRoles: secondaryRoles,
      incomingSceneActiveRoles: sceneActiveRoles,
      incomingRefsUsed: refsUsed,
      incomingMustAppear: mustAppear,
      refsByRoleCounts: summarizeRefsByRole(refsByRole),
      hasExplicitContract,
      shouldForceTwoPerson,
      semanticTwoPersonSignal,
    });
  }
  if (hasExplicitContract) {
    const explicitContract = {
      primaryRole: healedPrimarySafe,
      secondaryRoles: healedSecondary,
      sceneActiveRoles: healedActiveWithoutGroup,
      refsUsed: healedRefsUsed,
      mustAppear: healedMustAppear.length ? healedMustAppear : healedActiveWithoutGroup,
      mustNotAppear: groupNarrativelyRequired ? [] : ["group"],
    };
    if (shouldTraceRoleContractScene(sceneId)) {
      console.debug("[SCENARIO ROLE TRACE] buildScenarioRoleContractForImage.output.explicit", explicitContract);
    }
    return explicitContract;
  }
  if (roleWithRefs.length >= 2) {
    const fallbackPool = groupNarrativelyRequired ? roleWithRefs : roleWithRefs.filter((role) => role !== "group");
    const fallbackPrimary = primaryRole || fallbackPool[0] || "";
    const fallbackSecondary = fallbackPool.filter((role) => role !== fallbackPrimary);
    const fallbackActive = [fallbackPrimary, ...fallbackSecondary];
    const fallbackContract = {
      primaryRole: fallbackPrimary,
      secondaryRoles: fallbackSecondary,
      sceneActiveRoles: fallbackActive,
      refsUsed: fallbackActive,
      mustAppear: fallbackActive,
      mustNotAppear: groupNarrativelyRequired ? [] : ["group"],
    };
    if (shouldTraceRoleContractScene(sceneId)) {
      console.debug("[SCENARIO ROLE TRACE] buildScenarioRoleContractForImage.output.fallback_multi", fallbackContract);
    }
    return fallbackContract;
  }
  if (roleWithRefs.length === 1) {
    const singleRole = groupNarrativelyRequired ? roleWithRefs[0] : (roleWithRefs[0] === "group" ? "" : roleWithRefs[0]);
    const fallbackSingle = {
      primaryRole: singleRole,
      secondaryRoles: [],
      sceneActiveRoles: singleRole ? [singleRole] : [],
      refsUsed: singleRole ? [singleRole] : [],
      mustAppear: singleRole ? [singleRole] : [],
      mustNotAppear: groupNarrativelyRequired ? [] : ["group"],
    };
    if (shouldTraceRoleContractScene(sceneId)) {
      console.debug("[SCENARIO ROLE TRACE] buildScenarioRoleContractForImage.output.fallback_single", fallbackSingle);
    }
    return fallbackSingle;
  }
  const emptyContract = { primaryRole: "", secondaryRoles: [], sceneActiveRoles: [], refsUsed: [], mustAppear: [] };
  if (shouldTraceRoleContractScene(sceneId)) {
    console.debug("[SCENARIO ROLE TRACE] buildScenarioRoleContractForImage.output.empty", emptyContract);
  }
  return emptyContract;
}

function buildScenarioTransferLogData(scene = {}, contractPayload = {}) {
  return {
    sceneId: String(scene?.sceneId || contractPayload?.sceneId || ""),
    renderMode: String(scene?.renderMode || contractPayload?.renderMode || ""),
    ltxMode: String(scene?.ltxMode || contractPayload?.ltxMode || ""),
    sceneType: String(scene?.sceneType || contractPayload?.sceneType || ""),
    primaryRole: String(scene?.primaryRole || contractPayload?.primaryRole || ""),
    secondaryRoles: Array.isArray(scene?.secondaryRoles ?? contractPayload?.secondaryRoles) ? (scene?.secondaryRoles ?? contractPayload?.secondaryRoles) : [],
    refsUsed: Array.isArray(scene?.refsUsed ?? contractPayload?.refsUsed) ? (scene?.refsUsed ?? contractPayload?.refsUsed) : [],
    roleInfluenceApplied: Boolean(scene?.roleInfluenceApplied ?? contractPayload?.roleInfluenceApplied),
    roleInfluenceReason: String(scene?.roleInfluenceReason || contractPayload?.roleInfluenceReason || ""),
    sceneRoleDynamics: String(scene?.sceneRoleDynamics || contractPayload?.sceneRoleDynamics || ""),
    multiCharacterIdentityLock: Boolean(scene?.multiCharacterIdentityLock ?? contractPayload?.multiCharacterIdentityLock),
    distinctCharacterSeparation: Boolean(scene?.distinctCharacterSeparation ?? contractPayload?.distinctCharacterSeparation),
    appearanceDriftRisk: String(scene?.appearanceDriftRisk || contractPayload?.appearanceDriftRisk || ""),
    lipSync: Boolean(scene?.lipSync ?? contractPayload?.lipSync),
    audioSliceStartSec: scene?.audioSliceStartSec ?? contractPayload?.audioSliceStartSec ?? null,
    audioSliceEndSec: scene?.audioSliceEndSec ?? contractPayload?.audioSliceEndSec ?? null,
    hasContinuity: hasScenarioContractValue(scene?.continuity ?? contractPayload?.continuity),
    hasIdentityLock: hasScenarioContractValue(scene?.identityLock ?? contractPayload?.identityLock),
    hasStyleLock: hasScenarioContractValue(scene?.styleLock ?? contractPayload?.styleLock),
    hasEnvironmentLock: hasScenarioContractValue(scene?.environmentLock ?? contractPayload?.environmentLock),
    hasMustAppear: hasScenarioContractValue(scene?.mustAppear ?? contractPayload?.mustAppear),
    hasMustNotAppear: hasScenarioContractValue(scene?.mustNotAppear ?? contractPayload?.mustNotAppear),
    hasModelAssignments: hasScenarioContractValue(scene?.modelAssignments ?? contractPayload?.modelAssignments),
    hasProviderHints: hasScenarioContractValue(scene?.providerHints ?? contractPayload?.providerHints),
  };
}

function isAbortLikeError(error) {
  return error?.name === "AbortError" || String(error?.message || "").trim() === "AbortError";
}

function buildScenarioDirectorTimeoutError() {
  const error = new Error(`Scenario Director request timed out after ${Math.round(SCENARIO_DIRECTOR_TIMEOUT_MS / 1000)} seconds.`);
  error.name = "TimeoutError";
  return error;
}

function portColor(key) {
  return PORT_COLORS[key] || "#8c8c8c";
}

function handleStyle(kind, extra = {}) {
  const color = portColor(kind);
  return {
    ...HANDLE_BASE_STYLE,
    background: color,
    boxShadow: `0 0 0 1px rgba(0,0,0,0.72), 0 0 0 2px ${color}26`,
    ...extra,
  };
}

function normalizeLinkUrl(value = "") {
  return String(value || "").trim();
}

function parseLinkUrl(value = "") {
  const normalized = normalizeLinkUrl(value);
  if (!normalized) {
    return { isValid: false, normalized: "", domain: "", preview: "", href: "" };
  }

  try {
    const url = new URL(normalized);
    const protocol = String(url.protocol || "").toLowerCase();
    if (!["http:", "https:"].includes(protocol)) {
      return { isValid: false, normalized, domain: "", preview: "", href: "" };
    }
    const domain = String(url.hostname || "").replace(/^www\./i, "");
    const path = `${url.pathname || ""}${url.search || ""}`.replace(/\/$/, "") || "/";
    const preview = `${domain}${path === "/" ? "" : path}`;
    return {
      isValid: true,
      normalized: url.toString(),
      domain,
      preview,
      href: url.toString(),
    };
  } catch {
    return { isValid: false, normalized, domain: "", preview: "", href: "" };
  }
}

function buildLinkNodePayload(value = "") {
  const parsed = parseLinkUrl(value);
  if (!parsed.isValid) return null;
  return {
    type: "link",
    value: parsed.normalized,
    preview: parsed.preview || parsed.domain || parsed.normalized,
    sourceLabel: "Ссылка",
    url: parsed.href,
    domain: parsed.domain,
    href: parsed.href,
    meta: {
      domain: parsed.domain,
      kind: "link",
    },
  };
}

function getLinkNodeSavedPayload(data = null) {
  if (!data || typeof data !== "object") return null;
  const candidate = data.savedPayload && typeof data.savedPayload === "object"
    ? data.savedPayload
    : (data.outputPayload && typeof data.outputPayload === "object" ? data.outputPayload : null);
  if (!candidate) return null;
  const candidateUrl = candidate.value || candidate.url || candidate.href || "";
  const rebuilt = buildLinkNodePayload(candidateUrl);
  return {
    ...(rebuilt || {}),
    ...candidate,
    value: candidate.value || candidate.url || candidate.href || rebuilt?.value || "",
    preview: candidate.preview || rebuilt?.preview || candidate.domain || candidate.value || candidate.url || candidate.href || "",
    sourceLabel: candidate.sourceLabel || rebuilt?.sourceLabel || "Ссылка",
    url: candidate.url || candidate.href || candidate.value || rebuilt?.url || "",
    href: candidate.href || candidate.url || candidate.value || rebuilt?.href || "",
    domain: candidate.domain || rebuilt?.domain || "",
    meta: {
      ...(rebuilt?.meta || {}),
      ...(candidate.meta && typeof candidate.meta === "object" ? candidate.meta : {}),
      domain: candidate.domain || rebuilt?.domain || "",
      kind: "link",
    },
  };
}

function getNarrativeSourceRefreshSignature({ sourceNode = null, targetHandle = "" } = {}) {
  if (!sourceNode) return "";
  if (targetHandle === "audio_in" && sourceNode.type === "audioNode") {
    return `audio:${String(sourceNode?.data?.audioUrl || "").trim()}|${String(sourceNode?.data?.audioName || "").trim()}`;
  }
  if (targetHandle === "video_link_in" && sourceNode.type === "linkNode") {
    const payload = getLinkNodeSavedPayload(sourceNode?.data) || buildLinkNodePayload(sourceNode?.data?.urlValue || sourceNode?.data?.draftUrl || "");
    return `link:${JSON.stringify({
      value: payload?.value || "",
      preview: payload?.preview || "",
      url: payload?.url || payload?.href || "",
      domain: payload?.domain || "",
      status: String(sourceNode?.data?.urlStatus || ""),
    })}`;
  }
  if (targetHandle === "video_file_in" && sourceNode.type === "videoRefNode") {
    const payload = getVideoRefNodeSavedPayload(sourceNode?.data);
    return `video_ref_local:${JSON.stringify({
      value: payload?.value || "",
      fileName: payload?.fileName || "",
      assetUrl: payload?.assetUrl || payload?.url || "",
      preview: payload?.preview || "",
      duration: payload?.meta?.duration || null,
      mime: payload?.meta?.mime || "",
      size: payload?.meta?.size || 0,
    })}`;
  }
  if (targetHandle === "video_file_in" && sourceNode.type === "comfyStoryboard") {
    const scenes = Array.isArray(sourceNode?.data?.mockScenes) ? sourceNode.data.mockScenes : [];
    return `video_ref:${JSON.stringify(scenes.map((scene) => ({
      sceneId: String(scene?.sceneId || ""),
      videoUrl: String(scene?.videoUrl || scene?.assetUrl || "").trim(),
      imageUrl: String(scene?.imageUrl || "").trim(),
    })))}`;
  }
  if (["ref_character_1", "ref_location", "ref_style", "ref_props"].includes(targetHandle) && sourceNode.type === "refNode") {
    const normalized = normalizeRefData(sourceNode?.data || {}, sourceNode?.data?.kind || "");
    return `ref_node:${targetHandle}:${JSON.stringify({
      kind: String(sourceNode?.data?.kind || ""),
      refs: normalized.refs,
      status: String(sourceNode?.data?.refStatus || ""),
    })}`;
  }
  if (["ref_character_2", "ref_character_3"].includes(targetHandle) && ["refCharacter2", "refCharacter3"].includes(sourceNode.type)) {
    return `ref_lite:${targetHandle}:${JSON.stringify({
      refs: Array.isArray(sourceNode?.data?.refs) ? sourceNode.data.refs : [],
      status: String(sourceNode?.data?.refStatus || ""),
      name: String(sourceNode?.data?.name || ""),
      notes: String(sourceNode?.data?.notes || ""),
    })}`;
  }
  return "";
}

function isBrainInput(handleId) {
  return handleId === "audio" || handleId === "text" || handleId === "ref_character" || handleId === "ref_location" || handleId === "ref_style" || handleId === "ref_items";
}

function isComfyBrainInput(handleId) {
  return ["brain_package", "audio", "text", ...Object.keys(COMFY_BRAIN_REF_HANDLE_CONFIG)].includes(handleId);
}

function isNarrativeInput(handleId) {
  return [
    "audio_in",
    "video_file_in",
    "video_link_in",
    "ref_character_1",
    "ref_character_2",
    "ref_character_3",
    "ref_props",
    "ref_location",
    "ref_style",
  ].includes(String(handleId || ""));
}

function isNarrativeSourceInput(handleId) {
  return ["audio_in", "video_file_in", "video_link_in"].includes(String(handleId || ""));
}

const NARRATIVE_TESTER_NODE_CONFIG = {
  scenarioOutputTesterNode: {
    title: "ТЕСТЕР СЦЕНАРИЯ",
    acceptHandle: "scenario_out",
    payloadKey: "scenario",
  },
  voiceOutputTesterNode: {
    title: "ТЕСТЕР ОЗВУЧКИ",
    acceptHandle: "voice_script_out",
    payloadKey: "voiceScript",
  },
  brainPackageTesterNode: {
    title: "ТЕСТЕР LEGACY PLANNER",
    acceptHandle: "brain_package_out",
    payloadKind: "brain",
    payloadKey: "brainPackage",
  },
  musicPromptTesterNode: {
    title: "ТЕСТЕР МУЗЫКИ",
    acceptHandle: "bg_music_prompt_out",
    payloadKey: "bgMusicPrompt",
  },
};

function getNarrativeTesterConfig(type = "") {
  return NARRATIVE_TESTER_NODE_CONFIG[String(type || "")] || null;
}

function isNarrativeTesterNodeType(type = "") {
  return !!getNarrativeTesterConfig(type);
}

function getNarrativeTesterIncomingEdge({ nodeId = "", acceptHandle = "", edges = [] } = {}) {
  if (!nodeId || !acceptHandle) return null;
  return [...(Array.isArray(edges) ? edges : [])]
    .reverse()
    .find((edge) => edge?.target === nodeId && String(edge?.targetHandle || "") === acceptHandle)
    || null;
}

function extractNarrativeTesterPayload({ testerType = "", sourceNode = null, sourceHandle = "" } = {}) {
  const config = getNarrativeTesterConfig(testerType);
  if (!config || !sourceNode || sourceNode.type !== "comfyNarrative") return null;
  if (String(sourceHandle || "") !== config.acceptHandle) return null;
  const outputs = sourceNode?.data?.outputs && typeof sourceNode.data.outputs === "object" ? sourceNode.data.outputs : {};

  if (config.payloadKey === "brainPackage" || String(sourceHandle || "") === "brain_package_out") {
    const brainPackage = outputs?.brainPackage;
    console.log("[BRAIN TESTER FLOW]", {
      step: "extractNarrativeTesterPayload:narrative.outputs.brainPackage",
      value: brainPackage,
      type: typeof brainPackage,
      isArray: Array.isArray(brainPackage),
      isObject: !!brainPackage && typeof brainPackage === "object" && !Array.isArray(brainPackage),
      testerType,
      sourceHandle,
      sourceNodeType: sourceNode?.type || null,
    });
    return brainPackage && typeof brainPackage === "object" && !Array.isArray(brainPackage) ? brainPackage : brainPackage || null;
  }

  const payload = outputs?.[config.payloadKey];
  const normalized = String(payload || "").trim();
  return normalized || null;
}

function extractNarrativeBrainPackageForComfyBrain({ sourceNode = null, sourceHandle = "" } = {}) {
  if (!sourceNode || sourceNode.type !== "comfyNarrative" || String(sourceHandle || "") !== "brain_package_out") return null;
  const outputs = sourceNode?.data?.outputs && typeof sourceNode.data.outputs === "object" ? sourceNode.data.outputs : {};
  const brainPackage = outputs?.brainPackage;
  return brainPackage && typeof brainPackage === "object" ? brainPackage : null;
}

function extractNarrativeStoryboardOut({ sourceNode = null, sourceHandle = "" } = {}) {
  if (!sourceNode || sourceNode.type !== "comfyNarrative" || String(sourceHandle || "") !== "storyboard_out") return null;
  const outputs = sourceNode?.data?.outputs && typeof sourceNode.data.outputs === "object" ? sourceNode.data.outputs : {};
  const pendingOutputs = sourceNode?.data?.pendingOutputs && typeof sourceNode.data.pendingOutputs === "object" ? sourceNode.data.pendingOutputs : {};
  const storyboardOut = pendingOutputs?.storyboardOut || outputs?.storyboardOut;
  return storyboardOut && typeof storyboardOut === "object" && !Array.isArray(storyboardOut) ? storyboardOut : null;
}

function toStoryboardTimeSec(value, fallback = 0) {
  const direct = Number(value);
  if (Number.isFinite(direct)) return direct;
  const match = String(value || "").match(/-?\d+(?:\.\d+)?/);
  if (match) {
    const parsed = Number(match[0]);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function normalizeStoryboardOutScene(scene, index) {
  const source = scene && typeof scene === "object" ? scene : {};
  const t0 = toStoryboardTimeSec(source.time_start, index * 5);
  const duration = Math.max(0, toStoryboardTimeSec(source.duration, 5));
  const t1 = Math.max(t0, toStoryboardTimeSec(source.time_end, t0 + duration));
  const ltxMode = String(source.ltx_mode || "").trim();
  const sceneId = String(source.scene_id || `S${index + 1}`);
  const displayIndexRaw = Number(source.display_index ?? source.displayIndex);
  const displayIndex = Number.isFinite(displayIndexRaw) && displayIndexRaw > 0 ? Math.floor(displayIndexRaw) : (index + 1);
  return {
    id: `storyboard-scene-${index + 1}`,
    sceneId,
    displayIndex,
    t0,
    t1,
    start: t0,
    end: t1,
    durationSec: Math.max(0, Number((t1 - t0).toFixed(3))),
    sceneText: String(source.scene_goal || source.frame_description || "").trim(),
    visualDescription: String(source.frame_description || "").trim(),
    location: String(source.location || "").trim(),
    props: Array.isArray(source.props) ? source.props : [],
    emotion: String(source.emotion || "").trim(),
    imagePrompt: String(source.image_prompt || "").trim(),
    framePrompt: String(source.image_prompt || "").trim(),
    videoPrompt: String(source.video_prompt || "").trim(),
    actionInFrame: String(source.action_in_frame || "").trim(),
    cameraIdea: String(source.camera || "").trim(),
    ltxMode,
    ltxReason: String(source.ltx_reason || "").trim(),
    startFrameSource: String(source.start_frame_source || "new").trim(),
    needsTwoFrames: !!source.needs_two_frames,
    continuationFromPrevious: !!source.continuation_from_previous,
    narrationMode: String(source.narration_mode || "").trim(),
    localPhrase: source.local_phrase == null ? null : String(source.local_phrase),
    sfx: String(source.sfx || "").trim(),
    musicMixHint: String(source.music_mix_hint || "medium").trim() || "medium",
    actors: Array.isArray(source.actors) ? source.actors : [],
    executorModel: ltxMode || "i2v",
    sceneGenerationStatus: "not_generated",
    generatedAssetUrl: "",
    generatedAudioUrl: "",
    montageReady: false,
  };
}

function normalizeStoryboardGenerationStatus(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (["not_generated", "generating", "done", "error"].includes(normalized)) return normalized;
  return "not_generated";
}

function buildStoryboardSceneGenerationMap(scenes = [], previousMap = {}) {
  const prev = previousMap && typeof previousMap === "object" ? previousMap : {};
  return (Array.isArray(scenes) ? scenes : []).reduce((acc, scene) => {
    const sceneKey = String(scene?.sceneId || scene?.id || "");
    if (!sceneKey) return acc;
    const prevValue = prev[sceneKey] && typeof prev[sceneKey] === "object" ? prev[sceneKey] : {};
    acc[sceneKey] = {
      status: normalizeStoryboardGenerationStatus(prevValue.status),
      imageStatus: String(prevValue.imageStatus || ""),
      imageError: String(prevValue.imageError || ""),
      startFrameStatus: String(prevValue.startFrameStatus || ""),
      startFrameError: String(prevValue.startFrameError || ""),
      endFrameStatus: String(prevValue.endFrameStatus || ""),
      endFrameError: String(prevValue.endFrameError || ""),
      videoStatus: String(prevValue.videoStatus || ""),
      videoError: String(prevValue.videoError || ""),
      videoJobId: String(prevValue.videoJobId || ""),
      updatedAt: String(prevValue.updatedAt || ""),
      error: String(prevValue.error || ""),
      model: String(prevValue.model || scene?.executorModel || scene?.ltxMode || "i2v"),
      imagePrompt: String(prevValue.imagePrompt || scene?.imagePrompt || ""),
      videoPrompt: String(prevValue.videoPrompt || scene?.videoPrompt || ""),
      generatedAssetUrl: String(prevValue.generatedAssetUrl || ""),
      generatedAudioUrl: String(prevValue.generatedAudioUrl || ""),
      audioSliceStatus: String(prevValue.audioSliceStatus || ""),
      audioSliceUrl: String(prevValue.audioSliceUrl || ""),
      audioSliceDurationSec: normalizeDurationSec(prevValue.audioSliceDurationSec),
      audioSliceError: String(prevValue.audioSliceError || ""),
      audioSliceLoadError: String(prevValue.audioSliceLoadError || ""),
      montageReady: prevValue.montageReady === true,
    };
    return acc;
  }, {});
}

function collectSceneIds(scenes = []) {
  return (Array.isArray(scenes) ? scenes : [])
    .map((scene, idx) => String(scene?.sceneId || scene?.id || `S${idx + 1}`))
    .filter(Boolean);
}

function getAssetFileName(value = "") {
  const normalized = String(value || "").trim();
  if (!normalized) return "";
  const withoutQuery = normalized.split(/[?#]/)[0] || "";
  const parts = withoutQuery.split("/").filter(Boolean);
  return parts.length ? decodeURIComponent(parts[parts.length - 1]) : "";
}

function buildVideoRefNodePayload({
  fileName = "",
  assetUrl = "",
  durationSec = null,
  mime = "",
  size = 0,
  posterUrl = "",
  width = 0,
  height = 0,
} = {}) {
  const safeFileName = String(fileName || "").trim();
  const safeAssetUrl = String(assetUrl || "").trim();
  const safeMime = String(mime || "").trim();
  const safePoster = String(posterUrl || "").trim();
  const safeSize = Number(size || 0);
  const safeDuration = Number(durationSec || 0);
  if (!safeFileName && !safeAssetUrl) return null;
  return {
    type: "video_ref",
    value: safeAssetUrl || safeFileName,
    preview: safeFileName || safeAssetUrl || "Видео (референс)",
    sourceLabel: "Видео (референс)",
    fileName: safeFileName,
    assetUrl: safeAssetUrl,
    url: safeAssetUrl,
    posterUrl: safePoster,
    meta: {
      kind: "video_ref",
      duration: Number.isFinite(safeDuration) && safeDuration > 0 ? safeDuration : null,
      mime: safeMime,
      size: Number.isFinite(safeSize) && safeSize > 0 ? safeSize : 0,
      width: Number(width || 0) || 0,
      height: Number(height || 0) || 0,
    },
  };
}

function getVideoRefNodeSavedPayload(data = null) {
  if (!data || typeof data !== "object") return null;
  const candidate = data.savedPayload && typeof data.savedPayload === "object"
    ? data.savedPayload
    : (data.outputPayload && typeof data.outputPayload === "object" ? data.outputPayload : null);
  const assetUrl = String(candidate?.assetUrl || candidate?.url || data?.assetUrl || data?.url || "").trim();
  const fileName = String(candidate?.fileName || data?.fileName || "").trim();
  const built = buildVideoRefNodePayload({
    fileName,
    assetUrl,
    durationSec: candidate?.meta?.duration ?? data?.durationSec ?? data?.meta?.duration ?? null,
    mime: candidate?.meta?.mime || data?.mime || data?.meta?.mime || "",
    size: candidate?.meta?.size ?? data?.size ?? data?.meta?.size ?? 0,
    posterUrl: candidate?.posterUrl || data?.posterUrl || data?.previewImage || "",
    width: candidate?.meta?.width ?? data?.width ?? data?.meta?.width ?? 0,
    height: candidate?.meta?.height ?? data?.height ?? data?.meta?.height ?? 0,
  });
  if (!built && !candidate) return null;
  return {
    ...(built || {}),
    ...(candidate || {}),
    value: candidate?.value || built?.value || assetUrl || fileName,
    preview: candidate?.preview || built?.preview || fileName || assetUrl,
    sourceLabel: candidate?.sourceLabel || built?.sourceLabel || "Видео (референс)",
    fileName: candidate?.fileName || built?.fileName || fileName,
    assetUrl: candidate?.assetUrl || built?.assetUrl || assetUrl,
    url: candidate?.url || built?.url || assetUrl,
    posterUrl: candidate?.posterUrl || built?.posterUrl || data?.posterUrl || data?.previewImage || "",
    meta: {
      ...(built?.meta || {}),
      ...(candidate?.meta && typeof candidate.meta === "object" ? candidate.meta : {}),
      kind: "video_ref",
      duration: candidate?.meta?.duration ?? built?.meta?.duration ?? data?.durationSec ?? data?.meta?.duration ?? null,
      mime: candidate?.meta?.mime || built?.meta?.mime || data?.mime || data?.meta?.mime || "",
      size: candidate?.meta?.size ?? built?.meta?.size ?? data?.size ?? data?.meta?.size ?? 0,
      width: candidate?.meta?.width ?? built?.meta?.width ?? data?.width ?? data?.meta?.width ?? 0,
      height: candidate?.meta?.height ?? built?.meta?.height ?? data?.height ?? data?.meta?.height ?? 0,
    },
  };
}


function removeNarrativeIncomingSourceEdges(edges = [], narrativeNodeId = "") {
  if (!narrativeNodeId) return Array.isArray(edges) ? edges : [];
  return (Array.isArray(edges) ? edges : []).filter((edge) => !(edge?.target === narrativeNodeId && isNarrativeSourceInput(edge?.targetHandle)));
}

function enforceSingleNarrativeSourceEdge(edges = []) {
  const list = Array.isArray(edges) ? edges : [];
  const latestByNode = new Map();

  [...list].forEach((edge, index) => {
    if (!edge?.target || !isNarrativeSourceInput(edge?.targetHandle)) return;
    latestByNode.set(edge.target, index);
  });

  if (!latestByNode.size) return list;

  let changed = false;
  const next = list.filter((edge, index) => {
    if (!edge?.target || !isNarrativeSourceInput(edge?.targetHandle)) return true;
    const keepIndex = latestByNode.get(edge.target);
    const keep = keepIndex === index;
    if (!keep) {
      changed = true;
      console.warn("[NARRATIVE EDGE FIX]", edge.target);
    }
    return keep;
  });

  return changed ? next : list;
}

const EDGE_STYLE_BY_KIND = {
  audio: { color: PORT_COLORS.audio, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  text: { color: PORT_COLORS.text, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  text_in: { color: PORT_COLORS.text_in, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  audio_in: { color: PORT_COLORS.audio_in, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  video_file_in: { color: PORT_COLORS.video_file_in, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  video_link_in: { color: PORT_COLORS.video_link_in, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  link: { color: PORT_COLORS.link, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  link_in: { color: PORT_COLORS.link_in, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  video_ref_in: { color: PORT_COLORS.video_ref_in, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  storyboard_out: { color: PORT_COLORS.storyboard_out, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  preview_out: { color: PORT_COLORS.preview_out, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  scenario_storyboard_in: { color: PORT_COLORS.scenario_storyboard_in, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  scenario_storyboard_out: { color: PORT_COLORS.scenario_storyboard_out, strokeWidth: 2.6, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  scenario_preview_in: { color: PORT_COLORS.scenario_preview_in, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  scenario_out: { color: PORT_COLORS.scenario_out, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  voice_script_out: { color: PORT_COLORS.voice_script_out, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  brain_package_out: { color: PORT_COLORS.brain_package_out, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  brain_package: { color: PORT_COLORS.brain_package, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  bg_music_prompt_out: { color: PORT_COLORS.bg_music_prompt_out, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_character: { color: PORT_COLORS.ref_character, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_character_1: { color: PORT_COLORS.ref_character_1, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_location: { color: PORT_COLORS.ref_location, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_style: { color: PORT_COLORS.ref_style, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_items: { color: PORT_COLORS.ref_items, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_props: { color: PORT_COLORS.ref_props, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_character_2: { color: PORT_COLORS.ref_character_2, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_character_3: { color: PORT_COLORS.ref_character_3, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_animal: { color: PORT_COLORS.ref_animal, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  ref_group: { color: PORT_COLORS.ref_group, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  comfy_plan: { color: PORT_COLORS.comfy_plan, strokeWidth: 2.7, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  comfy_storyboard: { color: PORT_COLORS.comfy_storyboard, strokeWidth: 2.7, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  comfy_video: { color: PORT_COLORS.comfy_video, strokeWidth: 2.7, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  intro_context: { color: PORT_COLORS.intro_context, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  intro_frame: { color: PORT_COLORS.intro_frame, strokeWidth: 2.5, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  intro_to_assembly: { color: PORT_COLORS.intro_to_assembly, strokeWidth: 2.6, opacity: 1, animatedDash: true },
  plan: {
    color: PORT_COLORS.plan,
    strokeWidth: 2.4,
    opacity: 0.98,
    animatedDash: true,
  },
  brain_to_storyboard: {
    color: PORT_COLORS.brain_to_storyboard,
    strokeWidth: 2.4,
    opacity: 0.98,
    animatedDash: true,
  },
  storyboard_to_assembly: {
    color: PORT_COLORS.storyboard_to_assembly,
    strokeWidth: 2.6,
    opacity: 1,
    animatedDash: true,
  },
  brain_to_assembly: {
    color: PORT_COLORS.brain,
    strokeWidth: 2.3,
    opacity: 0.95,
    animatedDash: true,
  },
  assembly: { color: PORT_COLORS.assembly, strokeWidth: 2.2, opacity: 0.95, animatedDash: true },
  default: { color: "#8c8c8c", strokeWidth: 2.2, opacity: 0.96, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
};

function detectEdgeKind({ sourceHandle = "", targetHandle = "", sourceType = "", targetType = "", existingKind = "" }) {
  if (targetType === "brainNode" && isBrainInput(targetHandle)) return targetHandle;
  if (targetType === "comfyBrain" && isComfyBrainInput(targetHandle)) return targetHandle;
  if (targetType === "comfyNarrative" && isNarrativeInput(targetHandle)) return targetHandle;
  if (sourceType === "comfyBrain" && isComfyBrainInput(sourceHandle)) return sourceHandle;
  if (sourceType === "comfyNarrative" && ["storyboard_out", "preview_out", "scenario_out", "voice_script_out", "brain_package_out", "bg_music_prompt_out"].includes(String(sourceHandle || ""))) return sourceHandle;

  if (targetType === "introFrame" && targetHandle === INTRO_FRAME_STORY_HANDLE) return "intro_context";

  if (sourceType === "comfyBrain" && sourceHandle === "comfy_plan" && targetType === "comfyStoryboard" && targetHandle === "comfy_plan") {
    return "comfy_plan";
  }

  if (sourceType === "comfyStoryboard" && sourceHandle === COMFY_STORYBOARD_MAIN_HANDLE && targetType === "comfyVideoPreview" && targetHandle === COMFY_STORYBOARD_MAIN_HANDLE) {
    return "comfy_video";
  }

  if (sourceType === "comfyStoryboard" && sourceHandle === COMFY_STORYBOARD_MAIN_HANDLE && targetType === "assemblyNode") {
    return "storyboard_to_assembly";
  }

  if (sourceType === "introFrame" && sourceHandle === "intro_frame_out" && targetType === "assemblyNode" && targetHandle === "assembly_intro") {
    return "intro_to_assembly";
  }

  if (sourceType === "brainNode" && sourceHandle === "plan" && targetType === "storyboardNode" && targetHandle === "plan_in") {
    return "plan";
  }

  if (sourceType === "comfyNarrative" && sourceHandle === "storyboard_out" && targetType === "storyboardNode" && targetHandle === "plan_in") {
    return "storyboard_out";
  }

  if (sourceType === "comfyNarrative" && sourceHandle === "storyboard_out" && targetType === "scenarioStoryboard" && targetHandle === "scenario_storyboard_in") {
    return "scenario_storyboard_in";
  }

  if (sourceType === "comfyNarrative" && sourceHandle === "preview_out" && targetType === "comfyVideoPreview" && targetHandle === "scenario_preview_in") {
    return "scenario_preview_in";
  }

  if (sourceType === "storyboardNode" && sourceHandle === "plan_out" && targetType === "assemblyNode") {
    return "storyboard_to_assembly";
  }

  if (sourceType === "scenarioStoryboard" && sourceHandle === "scenario_storyboard_out" && targetType === "assemblyNode") {
    return "storyboard_to_assembly";
  }

  if (sourceType === "brainNode" && targetType === "assemblyNode") return "brain_to_assembly";
  if (existingKind && EDGE_STYLE_BY_KIND[existingKind]) return existingKind;
  if (targetType === "assemblyNode") return "assembly";
  return "default";
}

function normalizeClipStoryboardEdgeHandles(edge = {}, nodesById = new Map()) {
  if (!edge || typeof edge !== "object") return edge;
  const sourceNode = nodesById.get(edge.source);
  const targetNode = nodesById.get(edge.target);
  const sourceType = String(sourceNode?.type || "");
  const targetType = String(targetNode?.type || "");
  const sourceHandle = String(edge?.sourceHandle || "");
  const targetHandle = String(edge?.targetHandle || "");

  if (targetType === "introFrame" && targetHandle === "title_context") return null;

  if (
    sourceType === "comfyStoryboard"
    && targetType === "introFrame"
    && targetHandle === INTRO_FRAME_STORY_HANDLE
    && sourceHandle === COMFY_STORYBOARD_MAIN_HANDLE
  ) {
    return {
      ...edge,
      sourceHandle: COMFY_STORYBOARD_INTRO_HANDLE,
    };
  }

  return edge;
}

function getEdgePresentation(input) {
  const kind = detectEdgeKind(input);
  const visual = EDGE_STYLE_BY_KIND[kind] || EDGE_STYLE_BY_KIND.default;
  return {
    kind,
    animated: false,
    className: `clipSB_edge clipSB_edge--${kind}`,
    style: {
      stroke: visual.color,
      color: visual.color,
      strokeWidth: visual.strokeWidth,
      strokeDasharray: visual.strokeDasharray || "6 6",
      opacity: visual.opacity ?? 0.9,
      filter: visual.filter || "drop-shadow(0 0 6px currentColor)",
      "--clip-edge-hover-filter": visual.hoverFilter || visual.filter || "drop-shadow(0 0 6px currentColor)",
      "--clip-edge-hover-width": `${(visual.strokeWidth + 0.4).toFixed(2)}`,
      "--clip-edge-dash-duration": visual.animatedDash ? "1.35s" : "0s",
      "--clip-edge-dash-distance": visual.animatedDash ? "-20" : "0",
    },
  };
}

const SCENARIO_OPTIONS = [
  { value: "clip", label: "клип" },
  { value: "kino", label: "кино" },
  { value: "reklama", label: "реклама" },
];

const MODE_DISPLAY_META = {
  clip: {
    labelRu: "Клип",
    descriptionRu: "Ритм, монтаж и музыкальная энергия с динамичными сценами.",
  },
  kino: {
    labelRu: "Кино",
    descriptionRu: "Драматургия, логика сцен и причинно-следственная подача.",
  },
  reklama: {
    labelRu: "Реклама",
    descriptionRu: "Хук, ценность и акцент на продукте или ключевой идее.",
  },
  scenario: {
    labelRu: "Сценарий",
    descriptionRu: "Структурная раскадровка с понятными сценами и narrative steps.",
  },
};

const STYLE_DISPLAY_META = {
  realism: {
    labelRu: "Реализм",
    descriptionRu: "Натуральный свет, правдоподобная физика и живое изображение.",
  },
  film: {
    labelRu: "Кино-стиль",
    descriptionRu: "Киношная цветокоррекция, драматичный свет и авторская подача.",
  },
  neon: {
    labelRu: "Неон",
    descriptionRu: "Контрастный свет, цветные акценты и стилизованная атмосфера.",
  },
  glossy: {
    labelRu: "Глянец",
    descriptionRu: "Премиальная подача, чистая картинка и коммерческий блеск.",
  },
  soft: {
    labelRu: "Мягкий",
    descriptionRu: "Нежный свет, спокойная атмосфера и воздушная картинка.",
  },
};

function getModeDisplayMeta(mode = "clip") {
  const key = String(mode || "clip").toLowerCase();
  return MODE_DISPLAY_META[key] || MODE_DISPLAY_META.clip;
}

function getStyleDisplayMeta(stylePreset = "realism") {
  const key = String(stylePreset || "realism").toLowerCase();
  return STYLE_DISPLAY_META[key] || STYLE_DISPLAY_META.realism;
}

function formatSceneTypeHistogram(histogram) {
  if (!histogram || typeof histogram !== "object") return "—";
  const entries = Object.entries(histogram)
    .filter(([k, v]) => String(k || "").trim() && Number(v) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]));
  if (!entries.length) return "—";
  return entries.map(([k, v]) => `${k}:${v}`).join(" · ");
}

// -------------------------
// helpers
// -------------------------

function getAccountKey(user) {
  // Canonical accountKey:
  // - if user.id exists => "u_<id>" (always prefix to avoid format jumps)
  // - else if email exists => lowercased email
  // - else => "guest"
  const uidRaw = user?.id != null ? String(user.id) : "";
  const emailRaw = user?.email ? String(user.email).toLowerCase() : "";

  let key = "guest";
  if (uidRaw) key = uidRaw.startsWith("u_") ? uidRaw : `u_${uidRaw}`;
  else if (emailRaw) key = emailRaw;

  // Remember last known identity for post-refresh stability
  try {
    if (uidRaw) localStorage.setItem("ps:lastUserId", key);
    if (emailRaw) localStorage.setItem("ps:lastEmail", emailRaw);
  } catch {
    // ignore
  }

  // Fallback: if we're guest but we have a remembered user id/email, use it.
  if (key === "guest") {
    try {
      const lastUserId = localStorage.getItem("ps:lastUserId") || "";
      const lastEmail = (localStorage.getItem("ps:lastEmail") || "").toLowerCase();
      if (lastUserId) return String(lastUserId);
      if (lastEmail) return String(lastEmail);
    } catch {
      // ignore
    }
  }

  return key;
}

function safeGet(key) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSet(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

function safeDel(key) {
  try {
    localStorage.removeItem(key);
  } catch {
    // ignore
  }
}

function areNodesMeaningfullyEqual(prevNodes, nextNodes) {
  if (prevNodes === nextNodes) return true;
  if (!Array.isArray(prevNodes) || !Array.isArray(nextNodes)) return false;
  if (prevNodes.length !== nextNodes.length) return false;
  for (let i = 0; i < prevNodes.length; i += 1) {
    const prevSnapshot = JSON.stringify(stripFunctionsDeep(prevNodes[i]));
    const nextSnapshot = JSON.stringify(stripFunctionsDeep(nextNodes[i]));
    if (prevSnapshot !== nextSnapshot) return false;
  }
  return true;
}

function fmtTime(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s)) return "—";
  const total = Math.max(0, Math.floor(s));
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${mm}:${String(ss).padStart(2, "0")}`;
}

function fmtTimeWithMs(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s)) return "—";
  const safe = Math.max(0, s);
  const mm = Math.floor(safe / 60);
  const ss = Math.floor(safe % 60);
  const ms = Math.floor((safe - Math.floor(safe)) * 1000);
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
}

function fmtSecAndMs(seconds) {
  if (seconds == null || seconds === "") return "—";
  const s = Number(seconds);
  if (!Number.isFinite(s)) return "—";
  const ms = Math.round(s * 1000);
  return `${s.toFixed(3)}s (${ms}ms)`;
}

function normalizeDurationSec(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function buildAudioSliceReadyPatch({
  url = "",
  startSec = null,
  endSec = null,
  durationSec = null,
  audioSliceKind = "",
  musicVocalLipSyncAllowed = null,
  requiresAudioSensitiveVideo = null,
  expectedDurationSec = null,
  backendDurationSec = null,
  speechSafeAdjusted = false,
  speechSafeShiftMs = 0,
  sliceMayCutSpeech = false,
} = {}) {
  const normalizedUrl = String(url || "").trim();
  const normalizedStart = Number.isFinite(Number(startSec)) ? Number(startSec) : null;
  const normalizedEnd = Number.isFinite(Number(endSec)) ? Number(endSec) : null;
  const normalizedExpected = Number.isFinite(Number(expectedDurationSec))
    ? Number(expectedDurationSec)
    : Math.max(0, Number(normalizedEnd ?? 0) - Number(normalizedStart ?? 0));
  const normalizedDuration = Number.isFinite(Number(durationSec)) ? Number(durationSec) : normalizedExpected;
  return {
    audioSliceUrl: normalizedUrl,
    audioSliceStatus: "ready",
    extractedAudioStatus: "ready",
    extractedAudioUrl: normalizedUrl,
    audioSliceStartSec: normalizedStart,
    audioSliceEndSec: normalizedEnd,
    audioSliceT0: normalizedStart,
    audioSliceT1: normalizedEnd,
    audioSliceDurationSec: normalizedDuration,
    extractedAudioDurationSec: normalizedDuration,
    audioSliceExpectedDurationSec: normalizedExpected,
    audioSliceBackendDurationSec: Number.isFinite(Number(backendDurationSec)) ? Number(backendDurationSec) : normalizedDuration,
    audioSliceKind: String(audioSliceKind || "").trim().toLowerCase(),
    musicVocalLipSyncAllowed: (
      musicVocalLipSyncAllowed == null
        ? undefined
        : Boolean(musicVocalLipSyncAllowed)
    ),
    requiresAudioSensitiveVideo: (
      requiresAudioSensitiveVideo == null
        ? undefined
        : Boolean(requiresAudioSensitiveVideo)
    ),
    audioSliceActualDurationSec: null,
    audioSliceError: "",
    audioSliceLoadError: "",
    speechSafeAdjusted: Boolean(speechSafeAdjusted),
    speechSafeShiftMs: Number(speechSafeShiftMs ?? 0),
    sliceMayCutSpeech: Boolean(sliceMayCutSpeech),
  };
}

function normalizeLipSyncSceneStatePatch(scene = {}, patch = {}) {
  const nextPatch = patch && typeof patch === "object" ? { ...patch } : {};
  const merged = { ...(scene && typeof scene === "object" ? scene : {}), ...nextPatch };
  const normalizedWorkflowKey = String(
    merged?.resolvedWorkflowKey
    || merged?.resolved_workflow_key
    || ""
  ).trim().toLowerCase();
  const normalizedRenderMode = String(merged?.renderMode || merged?.render_mode || "").trim().toLowerCase();
  const isLipSyncRoute = normalizedWorkflowKey === "lip_sync_music" || normalizedRenderMode === "lip_sync_music";
  const hasAudioSlice = Boolean(String(merged?.audioSliceUrl || "").trim());
  if (!isLipSyncRoute) return nextPatch;
  nextPatch.renderMode = "lip_sync_music";
  nextPatch.resolvedWorkflowKey = "lip_sync_music";
  if (String(merged?.ltxMode || "").trim().toLowerCase() === "i2v" || !String(merged?.ltxMode || "").trim()) {
    nextPatch.ltxMode = "lip_sync";
  }
  if (hasAudioSlice) {
    const normalizedSliceKind = String(merged?.audioSliceKind || merged?.audio_slice_kind || "").trim().toLowerCase();
    nextPatch.audioSliceKind = normalizedSliceKind || "music_vocal";
    nextPatch.musicVocalLipSyncAllowed = true;
    nextPatch.requiresAudioSensitiveVideo = true;
    nextPatch.send_audio_to_generator = true;
    nextPatch.external_audio_used = true;
  }
  return nextPatch;
}

function isVideoJobInProgress(status) {
  const normalized = String(status || "").toLowerCase();
  return normalized === "queued" || normalized === "running";
}

function getSceneUiDescription(scene) {
  if (!scene || typeof scene !== "object") return "";
  const candidates = [
    scene.sceneText,
    scene.visualDescription,
    scene.why,
    scene.reason,
    scene.lyricFragment,
    scene.timingReason,
  ];
  for (const candidate of candidates) {
    const value = String(candidate || "").trim();
    if (value) return value;
  }
  return "";
}

function getScenarioSceneStableKey(scene, idx) {
  if (!scene || typeof scene !== "object") return `scene-${idx}`;
  const explicitId = String(scene.sceneId || "").trim();
  if (explicitId) return explicitId;

  const start = Number(scene.t0 ?? scene.start);
  const end = Number(scene.t1 ?? scene.end);
  if (Number.isFinite(start) && Number.isFinite(end)) {
    return `time-${start}-${end}`;
  }

  return `scene-${idx}`;
}

function buildCanonicalSceneId(scene, idx, prefix = "scene") {
  const explicit = String(scene?.sceneId || "").trim();
  if (explicit) return explicit;
  const snakeCase = String(scene?.scene_id || "").trim();
  if (snakeCase) return snakeCase;
  const legacy = String(scene?.id || "").trim();
  if (legacy) return legacy;
  return `${prefix}_${String(idx + 1).padStart(3, "0")}`;
}

function normalizeSceneCollectionWithSceneId(scenes, prefix = "scene") {
  return (Array.isArray(scenes) ? scenes : []).map((scene, idx) => ({
    ...(scene && typeof scene === "object" ? scene : {}),
    sceneId: buildCanonicalSceneId(scene, idx, prefix),
  }));
}

function mergeVideoStateBySceneId(nextScenes, existingScenes, { panelField = "" } = {}) {
  const existingMap = new Map(
    normalizeSceneCollectionWithSceneId(existingScenes).map((scene) => [String(scene?.sceneId || ""), scene])
  );
  return normalizeSceneCollectionWithSceneId(nextScenes).map((scene) => {
    const sceneId = String(scene?.sceneId || "");
    const prev = existingMap.get(sceneId);
    if (!prev) return scene;
    const merged = { ...scene };
    for (const field of ["videoUrl", "videoStatus", "videoJobId", "videoError"]) {
      if (!String(merged?.[field] || "").trim() && String(prev?.[field] || "").trim()) {
        merged[field] = prev[field];
      }
    }
    for (const field of [
      "audioSliceUrl",
      "audioSliceStatus",
      "audioSliceError",
      "audioSliceLoadError",
    ]) {
      if (!String(merged?.[field] || "").trim() && String(prev?.[field] || "").trim()) {
        merged[field] = prev[field];
      }
    }
    for (const field of [
      "audioSliceStartSec",
      "audioSliceEndSec",
      "audioSliceDurationSec",
      "audioSliceT0",
      "audioSliceT1",
      "audioSliceExpectedDurationSec",
      "audioSliceBackendDurationSec",
      "audioSliceActualDurationSec",
      "speechSafeAdjusted",
      "speechSafeShiftMs",
      "sliceMayCutSpeech",
    ]) {
      if ((merged?.[field] == null || merged?.[field] === "") && prev?.[field] != null && prev?.[field] !== "") {
        merged[field] = prev[field];
      }
    }
    if (panelField && merged?.[panelField] == null && prev?.[panelField] != null) {
      merged[panelField] = prev[panelField];
    }
    return merged;
  });
}

function resetVideoStateBySceneId(nextScenes, { panelField = "" } = {}) {
  return normalizeSceneCollectionWithSceneId(nextScenes).map((scene) => {
    const resetScene = {
      ...scene,
      videoUrl: "",
      videoStatus: "",
      videoJobId: "",
      videoError: "",
    };
    if (panelField) resetScene[panelField] = false;
    return resetScene;
  });
}

function buildSceneSignature(scenes, prefix = "scene") {
  return normalizeSceneCollectionWithSceneId(scenes, prefix)
    .map((scene, idx) => {
      const sceneId = String(scene?.sceneId || "").trim() || `${prefix}_${idx + 1}`;
      const t0 = Number(scene?.t0 ?? scene?.start ?? 0);
      const t1 = Number(scene?.t1 ?? scene?.end ?? 0);
      const imagePrompt = String(scene?.imagePrompt || scene?.framePrompt || scene?.prompt || scene?.sceneText || "").trim();
      return [sceneId, Number.isFinite(t0) ? t0 : "", Number.isFinite(t1) ? t1 : "", imagePrompt].join("|");
    })
    .join("~~");
}

const SCENARIO_GENERATED_ASSET_FIELDS = [
  "imageUrl",
  "imageStatus",
  "imageError",
  "startImageUrl",
  "endImageUrl",
  "startFrameImageUrl",
  "endFrameImageUrl",
  "startFramePreviewUrl",
  "endFramePreviewUrl",
  "startFrameStatus",
  "startFrameError",
  "endFrameStatus",
  "endFrameError",
  "videoUrl",
  "videoStatus",
  "videoError",
  "videoJobId",
  "videoSourceImageUrl",
  "videoPanelActivated",
];

function stripScenarioGeneratedAssets(scene = {}) {
  const base = scene && typeof scene === "object" ? { ...scene } : {};
  SCENARIO_GENERATED_ASSET_FIELDS.forEach((key) => {
    if (Object.prototype.hasOwnProperty.call(base, key)) delete base[key];
  });
  return base;
}

function buildScenarioScenePackageSignature(scene = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const displayTime = resolveSceneDisplayTime(source);
  const activeConnectedCharacterRoles = Array.from(new Set(
    [
      ...(Array.isArray(source?.activeConnectedCharacterRoles) ? source.activeConnectedCharacterRoles : []),
      ...(Array.isArray(source?.connectedContextSummary?.active_connected_character_roles)
        ? source.connectedContextSummary.active_connected_character_roles
        : []),
    ].map((value) => String(value || "").trim()).filter(Boolean)
  )).sort();
  const connectedRefNodeIdsByRole = source?.connectedRefNodeIdsByRole && typeof source.connectedRefNodeIdsByRole === "object"
    ? source.connectedRefNodeIdsByRole
    : {};
  const connectedRoleTypesByRole = source?.connectedRoleTypesByRole && typeof source.connectedRoleTypesByRole === "object"
    ? source.connectedRoleTypesByRole
    : {};
  const signaturePayload = {
    time: [Number(displayTime.startSec || 0), Number(displayTime.endSec || 0)],
    t0: Number(source?.t0 ?? source?.timeStart ?? source?.time_start ?? displayTime.startSec ?? 0),
    t1: Number(source?.t1 ?? source?.timeEnd ?? source?.time_end ?? displayTime.endSec ?? 0),
    duration: Number(source?.duration ?? source?.durationSec ?? Math.max(0, Number(displayTime.endSec || 0) - Number(displayTime.startSec || 0))),
    summaryRu: String(source?.summaryRu || "").trim(),
    summaryEn: String(source?.summaryEn || "").trim(),
    sceneGoalRu: String(source?.sceneGoalRu || "").trim(),
    sceneGoalEn: String(source?.sceneGoalEn || "").trim(),
    localPhrase: String(source?.localPhrase || "").trim(),
    lyricText: String(source?.lyricText || "").trim(),
    imagePromptRu: String(source?.imagePromptRu || "").trim(),
    imagePromptEn: String(source?.imagePromptEn || "").trim(),
    videoPromptRu: String(source?.videoPromptRu || "").trim(),
    videoPromptEn: String(source?.videoPromptEn || "").trim(),
    actors: Array.isArray(source?.actors) ? source.actors : [],
    refsUsed: Array.isArray(source?.refsUsed) ? source.refsUsed : [],
    mustAppear: Array.isArray(source?.mustAppear) ? source.mustAppear : [],
    props: Array.isArray(source?.props) ? source.props : [],
    refsByRole: source?.refsByRole && typeof source.refsByRole === "object" ? source.refsByRole : {},
    refsUsedByRole: source?.refsUsedByRole && typeof source.refsUsedByRole === "object" ? source.refsUsedByRole : {},
    supportEntityIds: Array.isArray(source?.supportEntityIds) ? source.supportEntityIds : [],
    mustNotAppear: Array.isArray(source?.mustNotAppear) ? source.mustNotAppear : [],
    audioAnchorEvidence: String(source?.audioAnchorEvidence || "").trim(),
    clipDecisionReason: String(source?.clipDecisionReason || "").trim(),
    workflowDecisionReason: String(source?.workflowDecisionReason || "").trim(),
    sceneRoleDynamics: String(source?.sceneRoleDynamics || "").trim(),
    primaryRole: String(source?.primaryRole || "").trim(),
    secondaryRoles: Array.isArray(source?.secondaryRoles) ? source.secondaryRoles : [],
    sceneActiveRoles: Array.isArray(source?.sceneActiveRoles) ? source.sceneActiveRoles : [],
    resolvedWorkflowKey: String(source?.resolvedWorkflowKey || "").trim(),
    videoGenerationRoute: String(source?.videoGenerationRoute || source?.video_generation_route || "").trim(),
    plannedVideoGenerationRoute: String(source?.plannedVideoGenerationRoute || source?.planned_video_generation_route || "").trim(),
    videoBlockReasonCode: String(source?.videoBlockReasonCode || source?.video_block_reason_code || "").trim(),
    videoDowngradeReasonCode: String(source?.videoDowngradeReasonCode || source?.video_downgrade_reason_code || "").trim(),
    resolvedModelKey: String(source?.resolvedModelKey || "").trim(),
    renderMode: String(source?.renderMode || "").trim(),
    ltxMode: String(source?.ltxMode || "").trim(),
    activeConnectedCharacterRoles,
    hasConnectedCharacter2: activeConnectedCharacterRoles.includes("character_2"),
    connectedRefNodeIdsByRole,
    connectedRoleTypesByRole,
  };
  return JSON.stringify(signaturePayload);
}

function buildScenarioSceneStableSignature(scene = {}) {
  return buildScenarioScenePackageSignature(stripScenarioGeneratedAssets(scene));
}


function isShortMusicIntroPhraseRow(row = {}) {
  const text = String(row?.text || row?.phrase || "").trim().toLowerCase();
  const normalizedText = text.replace(/[^a-z0-9а-я]+/gi, " ").trim();
  if (!["music intro", "instrumental intro"].includes(normalizedText)) return false;
  const t0 = Number(row?.t0 ?? row?.startSec ?? row?.start ?? 0);
  const t1Raw = Number(row?.t1 ?? row?.endSec ?? row?.end ?? t0);
  const t1 = Number.isFinite(t1Raw) && t1Raw >= t0 ? t1Raw : t0;
  const durationSec = Math.max(0, t1 - t0);
  return Number.isFinite(t0) && t0 <= 0.05 && durationSec <= 1.0;
}

function normalizeTimelinePhraseRows(rows = []) {
  if (!Array.isArray(rows)) return [];
  return rows
    .map((row, idx) => {
      if (!row || typeof row !== "object") return null;
      const text = String(row?.text || row?.phrase || "").trim();
      const t0 = Number(row?.t0 ?? row?.startSec ?? row?.start ?? 0);
      const t1Raw = Number(row?.t1 ?? row?.endSec ?? row?.end ?? t0);
      const t1 = Number.isFinite(t1Raw) && t1Raw >= t0 ? t1Raw : t0;
      if (!Number.isFinite(t0) || !Number.isFinite(t1) || !text) return null;
      return {
        id: String(row?.id || row?.phraseId || row?.sceneId || `P${idx + 1}`),
        text,
        t0,
        t1,
        emotion: String(row?.emotion || "").trim(),
        meaning: String(row?.meaning || "").trim(),
        transitionHint: String(row?.transitionHint || "").trim(),
      };
    })
    .filter(Boolean)
    .filter((row) => !isShortMusicIntroPhraseRow(row));
}

function overlapDurationSec(a0 = 0, a1 = 0, b0 = 0, b1 = 0) {
  const start = Math.max(Number(a0) || 0, Number(b0) || 0);
  const end = Math.min(Number(a1) || 0, Number(b1) || 0);
  return Math.max(0, end - start);
}

function getScenePhrasesByTime(scene = {}, transcript = [], semanticTimeline = [], minOverlapSec = 0) {
  const displayTime = resolveSceneDisplayTime(scene);
  const sceneStart = Number(displayTime.startSec || 0);
  const sceneEnd = Number(displayTime.endSec || sceneStart);
  const matchByTime = (phrases = []) => phrases
    .map((phrase) => {
      const overlapSec = overlapDurationSec(sceneStart, sceneEnd, phrase?.t0, phrase?.t1);
      return overlapSec > minOverlapSec ? { ...phrase, overlapSec } : null;
    })
    .filter(Boolean)
    .sort((a, b) => (Number(b?.overlapSec || 0) - Number(a?.overlapSec || 0)));
  const matchedTranscriptPhrases = matchByTime(Array.isArray(transcript) ? transcript : []);
  const matchedSemanticPhrases = matchByTime(Array.isArray(semanticTimeline) ? semanticTimeline : []);
  const selected = matchedTranscriptPhrases.length ? matchedTranscriptPhrases : matchedSemanticPhrases;
  const uniqueTexts = Array.from(new Set(selected.map((item) => String(item?.text || "").trim()).filter(Boolean)));
  const primaryPhrase = uniqueTexts[0] || "";
  const combinedPhraseText = uniqueTexts.join(" · ");
  return {
    matchedTranscriptPhrases,
    matchedSemanticPhrases,
    primaryPhrase,
    combinedPhraseText,
    phraseCount: uniqueTexts.length,
    matchedPhraseTexts: uniqueTexts,
    sourceUsed: matchedTranscriptPhrases.length ? "transcript" : (matchedSemanticPhrases.length ? "semanticTimeline" : "none"),
    sceneStart,
    sceneEnd,
  };
}

function mapPhraseToSceneIdByTime(phrase = {}, scenes = []) {
  const phraseStart = Number(phrase?.t0 ?? phrase?.startSec ?? 0);
  const phraseEndRaw = Number(phrase?.t1 ?? phrase?.endSec ?? phraseStart);
  const phraseEnd = Number.isFinite(phraseEndRaw) && phraseEndRaw >= phraseStart ? phraseEndRaw : phraseStart;
  let best = { sceneId: "", overlapSec: 0 };
  (Array.isArray(scenes) ? scenes : []).forEach((scene, idx) => {
    const sceneId = String(scene?.sceneId || `S${idx + 1}`).trim();
    const displayTime = resolveSceneDisplayTime(scene);
    const overlapSec = overlapDurationSec(displayTime.startSec, displayTime.endSec, phraseStart, phraseEnd);
    if (overlapSec > best.overlapSec) best = { sceneId, overlapSec };
  });
  return best.sceneId;
}

function collectSceneVideoStateStats(scenes, prefix = "scene") {
  return normalizeSceneCollectionWithSceneId(scenes, prefix).reduce((acc, scene) => {
    if (String(scene?.videoUrl || "").trim()) acc.videoUrlCount += 1;
    if (String(scene?.videoStatus || "").trim()) acc.videoStatusCount += 1;
    if (String(scene?.videoJobId || "").trim()) acc.videoJobIdCount += 1;
    return acc;
  }, {
    totalScenes: Array.isArray(scenes) ? scenes.length : 0,
    videoUrlCount: 0,
    videoStatusCount: 0,
    videoJobIdCount: 0,
  });
}

function isLipSyncScene(scene) {
  if (!scene || typeof scene !== "object") return false;
  const ltxMode = String(scene.ltxMode || scene.ltx_mode || "").trim().toLowerCase();
  const renderMode = String(scene.renderMode || scene.render_mode || "").trim().toLowerCase();
  const resolvedWorkflowKey = normalizeScenarioWorkflowKeyForProduction(
    scene.resolvedWorkflowKey || scene.resolved_workflow_key || ""
  );
  return !!(
    scene.lipSync === true
    || scene.isLipSync === true
    || ltxMode === "lip_sync"
    || ltxMode === "lip_sync_music"
    || renderMode === "avatar_lipsync"
    || renderMode === "lip_sync_music"
    || resolvedWorkflowKey === "lip_sync_music"
  );
}

function stableRefsSignature(refs = []) {
  return refs
    .map((item) => String(item?.url || "").trim())
    .filter(Boolean)
    .join("|");
}

function buildPlannerInputSignature({ characterRefs = [], locationRefs = [], styleRefs = [], propsRefs = [], text = "", audioUrl = "", mode = "clip", settings = {} }) {
  return JSON.stringify({
    characterRefs: stableRefsSignature(characterRefs),
    locationRefs: stableRefsSignature(locationRefs),
    styleRefs: stableRefsSignature(styleRefs),
    propsRefs: stableRefsSignature(propsRefs),
    text: String(text || "").trim(),
    audioUrl: String(audioUrl || "").trim(),
    mode: String(mode || "clip"),
    settings: {
      scenarioKey: String(settings?.scenarioKey || "clip"),
      shootKey: String(settings?.shootKey || "cinema"),
      styleKey: String(settings?.styleKey || "realism"),
      freezeStyle: !!settings?.freezeStyle,
      wantLipSync: !!settings?.wantLipSync,
    },
  });
}

function collectBrainPlannerInput({ brainNodeId, nodesList, edgesList }) {
  const inEdges = (edgesList || []).filter((e) => e.target === brainNodeId);

  const pickSourceNode = (handleId) => {
    const edgeExplicit = [...inEdges].reverse().find((e) => (e.targetHandle || "") === handleId);
    if (edgeExplicit) return (nodesList || []).find((x) => x.id === edgeExplicit.source) || null;
    if (handleId === "audio") {
      const e0 = [...inEdges].reverse().find((e) => e.source === "audio");
      if (e0) return (nodesList || []).find((x) => x.id === "audio") || null;
    }
    if (handleId === "text") {
      const e0 = [...inEdges].reverse().find((e) => e.source === "text");
      if (e0) return (nodesList || []).find((x) => x.id === "text") || null;
    }
    return null;
  };

  const getRefList = (refNode, expectedKind, max = 5) => {
    if (refNode?.type !== "refNode" || refNode?.data?.kind !== expectedKind) return [];
    const refsRaw = Array.isArray(refNode?.data?.refs) ? refNode.data.refs : [];
    return refsRaw
      .map((item) => ({ url: String(item?.url || "").trim() }))
      .filter((item) => !!item.url)
      .slice(0, max);
  };

  const brainNode = (nodesList || []).find((x) => x.id === brainNodeId);
  const audioNode = pickSourceNode("audio");
  const textNode = pickSourceNode("text");
  const refCharNode = pickSourceNode("ref_character");
  const refLocNode = pickSourceNode("ref_location");
  const refStyleNode = pickSourceNode("ref_style");
  const refItemsNode = pickSourceNode("ref_items");

  const characterRefs = getRefList(refCharNode, "ref_character", 5);
  const locationRefs = getRefList(refLocNode, "ref_location", 5);
  const propsRefs = getRefList(refItemsNode, "ref_items", 5);
  const styleRefs = getRefList(refStyleNode, "ref_style", 1);
  const scenarioKey = SCENARIO_OPTIONS.some((option) => option.value === brainNode?.data?.scenarioKey)
    ? brainNode.data.scenarioKey
    : "clip";
  const shootKey = brainNode?.data?.shootKey || "cinema";
  const styleKey = brainNode?.data?.styleKey || "realism";
  const freezeStyle = !!brainNode?.data?.freezeStyle;
  const wantLipSync = !!brainNode?.data?.wantLipSync;
  const audioUrl = audioNode?.type === "audioNode" ? (audioNode.data?.audioUrl || "") : "";
  const textValue = textNode?.type === "textNode" ? String(textNode.data?.textValue || "") : "";
  const mode = (brainNode?.data?.mode || scenarioKey || "clip").toLowerCase();

  const signature = buildPlannerInputSignature({
    characterRefs,
    locationRefs,
    styleRefs,
    propsRefs,
    text: textValue,
    audioUrl,
    mode,
    settings: { scenarioKey, shootKey, styleKey, freezeStyle, wantLipSync },
  });

  return {
    signature,
    mode,
    textValue,
    audioUrl,
    characterRefs,
    locationRefs,
    propsRefs,
    styleRefs,
    styleRef: styleRefs[0] || null,
    scenarioKey,
    shootKey,
    styleKey,
    freezeStyle,
    wantLipSync,
  };
}

function resolveAssetUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw) || raw.startsWith("data:")) return raw;
  if (raw.startsWith("/static/assets/") || raw.startsWith("/assets/")) return `${API_BASE}${raw}`;
  if (raw.startsWith("static/assets/")) return `${API_BASE}/${raw}`;
  return raw;
}

function normalizeVideoSourceUrl(url) {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (raw.startsWith("data:") || /^https?:\/\//i.test(raw)) return raw;
  if (raw.startsWith("/static/") || raw.startsWith("static/")) return String(resolveAssetUrl(raw) || "").trim();
  return raw;
}

const SCENE_IMAGE_FORMATS = ["9:16", "1:1", "16:9"];
const DEFAULT_SCENE_IMAGE_FORMAT = "9:16";
const USE_COMFY_MOCK = false;
const INTRO_STYLE_PRESET_META = {
  "youtube_shock": {
    "value": "youtube_shock",
    "key": "youtube_shock",
    "label": "YouTube Shock",
    "shortDescription": "High-urgency thumbnail hook with bold contrast and one obvious hero moment.",
    "compositionBias": "subject-dominant hero framing with aggressive readability",
    "palette": "warm yellow, red-orange, charcoal",
    "mood": "urgent, explosive, clickable",
    "textTreatment": "bold compact headline support, high contrast, minimal words",
    "graphicAccentsPreference": "glow edges, impact streaks, restrained arrows only if needed",
    "overlays": "tight glow plates and punchy light sweeps",
    "promptFragment": "premium high-energy thumbnail with immediate stop-scroll impact, one dominant hero subject, bold contrast, and clean clickable urgency",
    "promptRules": [
      "immediate thumbnail readability and one dominant focal point",
      "high-energy premium hook without spammy clickbait",
      "subject stays clear while accents stay secondary"
    ],
    "negativeRules": [
      "no cheap meme trash",
      "no fake subscriber UI",
      "no overcrowded collage"
    ],
    "accent": "#ffcf5c",
    "secondary": "#ff6b3d",
    "uiHint": "Shock hook",
    "background": "radial-gradient(circle at 24% 20%, rgba(255,207,92,0.88), rgba(255,107,61,0.38) 35%, rgba(9,15,32,1) 78%)"
  },
  "reaction_result": {
    "value": "reaction_result",
    "key": "reaction_result",
    "label": "Reaction Result",
    "shortDescription": "Outcome-first composition pairing a readable reaction with the revealed result.",
    "compositionBias": "split emphasis between hero reaction and revealed outcome",
    "palette": "gold, coral, deep navy",
    "mood": "surprised, satisfying, payoff-driven",
    "textTreatment": "short payoff phrase, supportive not dominant",
    "graphicAccentsPreference": "light callout accents, comparison framing, controlled glow",
    "overlays": "soft result halos and subtle directional emphasis",
    "promptFragment": "premium thumbnail where the emotional reaction and the revealed outcome are instantly understandable and tightly linked",
    "promptRules": [
      "result is instantly understandable",
      "reaction and outcome stay tightly linked",
      "premium payoff energy without clutter"
    ],
    "negativeRules": [
      "no meme-face distortion",
      "no noisy badges",
      "no cluttered comparison spam"
    ],
    "accent": "#ffb86c",
    "secondary": "#ff6f91",
    "uiHint": "Payoff frame",
    "background": "radial-gradient(circle at 22% 18%, rgba(255,184,108,0.78), rgba(255,111,145,0.28) 35%, rgba(11,18,32,1) 80%)"
  },
  "breaking_alert": {
    "value": "breaking_alert",
    "key": "breaking_alert",
    "label": "Breaking Alert",
    "shortDescription": "News-flash urgency with crisp hierarchy and alert-style lighting accents.",
    "compositionBias": "headline-supporting alert composition with urgent subject focus",
    "palette": "red, amber, dark steel",
    "mood": "urgent, alarming, immediate",
    "textTreatment": "compact alert headline plate with strong contrast",
    "graphicAccentsPreference": "signal bars, alert glows, restrained warning lines",
    "overlays": "broadcast streaks and emergency-light bloom",
    "promptFragment": "premium breaking-alert thumbnail with immediate urgency, crisp hierarchy, signal-style lighting, and strong readability",
    "promptRules": [
      "premium breaking alert tone",
      "urgent but clean hierarchy",
      "thumbnail readability first"
    ],
    "negativeRules": [
      "no tabloid chaos",
      "no fake ticker spam",
      "no alert graphics hiding the subject"
    ],
    "accent": "#ff5a5f",
    "secondary": "#ffd166",
    "uiHint": "Alert mode",
    "background": "radial-gradient(circle at 24% 18%, rgba(255,90,95,0.74), rgba(255,209,102,0.24) 34%, rgba(10,12,22,1) 80%)"
  },
  "tutorial_clickable": {
    "value": "tutorial_clickable",
    "key": "tutorial_clickable",
    "label": "Tutorial Clickable",
    "shortDescription": "Clean instructional thumbnail built around clarity, guidance, and one teachable focal point.",
    "compositionBias": "demonstration-first layout with readable subject/object steps",
    "palette": "cyan, blue, white, graphite",
    "mood": "clear, confident, helpful",
    "textTreatment": "short informative text with clean spacing and no shouting",
    "graphicAccentsPreference": "simple pointers, guide frames, minimal progress cues",
    "overlays": "soft UI emphasis bars and controlled highlights",
    "promptFragment": "clean premium tutorial thumbnail with obvious instructional focus, one teachable focal point, and strong small-size readability",
    "promptRules": [
      "clarity and teachability first",
      "guidance accents stay minimal",
      "approachable premium look"
    ],
    "negativeRules": [
      "no arrow overload",
      "no fake software UI spam",
      "no confusing multi-step collage"
    ],
    "accent": "#5dd6ff",
    "secondary": "#7a8cff",
    "uiHint": "How-to clarity",
    "background": "radial-gradient(circle at 24% 20%, rgba(93,214,255,0.72), rgba(122,140,255,0.28) 35%, rgba(8,13,28,1) 78%)"
  },
  "big_object_focus": {
    "value": "big_object_focus",
    "key": "big_object_focus",
    "label": "Big Object Focus",
    "shortDescription": "Hero-object composition where scale, shape, and readability of the main item dominate.",
    "compositionBias": "large object hero framing with supportive text and accents",
    "palette": "electric blue, orange spark, dark slate",
    "mood": "impressive, punchy, object-centric",
    "textTreatment": "minimal bold support near edges, never over the object core",
    "graphicAccentsPreference": "scale cues, rim glow, impact flares",
    "overlays": "light bloom and object-edge emphasis",
    "promptFragment": "iconic oversized hero object thumbnail where the item feels large, clear, readable, and instantly legible in one glance",
    "promptRules": [
      "main object feels iconic and large",
      "lighting celebrates scale",
      "design stays secondary to the object"
    ],
    "negativeRules": [
      "no clutter weakening the object silhouette",
      "no decorative text covering the main item",
      "no busy collage"
    ],
    "accent": "#55c7ff",
    "secondary": "#ff9f43",
    "uiHint": "Object hero",
    "background": "radial-gradient(circle at 24% 18%, rgba(85,199,255,0.7), rgba(255,159,67,0.26) 35%, rgba(9,15,28,1) 78%)"
  },
  "cyber_neon": {
    "value": "cyber_neon",
    "key": "cyber_neon",
    "label": "Cyber Neon",
    "shortDescription": "Dark futuristic scene with premium cyan-magenta neon and clean subject separation.",
    "compositionBias": "moody cyber subject framing with illuminated edge contrast",
    "palette": "cyan, magenta, violet, ink black",
    "mood": "charged, futuristic, immersive",
    "textTreatment": "sleek luminous type support with restrained density",
    "graphicAccentsPreference": "neon rims, holographic lines, signal streaks",
    "overlays": "selective glow fog and cyber light trails",
    "promptFragment": "premium cyber-neon thumbnail with controlled glow, moody futuristic depth, clean subject separation, and preserved focal clarity",
    "promptRules": [
      "controlled neon accents",
      "subject readability survives the glow",
      "immersive but premium"
    ],
    "negativeRules": [
      "no acid rainbow overload",
      "no unreadable overglow",
      "no chaotic cheap cyber clutter"
    ],
    "accent": "#6ef2ff",
    "secondary": "#b86dff",
    "uiHint": "Cyber glow",
    "background": "radial-gradient(circle at 25% 18%, rgba(110,242,255,0.5), rgba(184,109,255,0.28) 36%, rgba(4,8,20,1) 80%)"
  },
  "ai_tech_explainer": {
    "value": "ai_tech_explainer",
    "key": "ai_tech_explainer",
    "label": "AI Tech Explainer",
    "shortDescription": "Smart editorial tech look blending innovation cues with clear explanatory hierarchy.",
    "compositionBias": "explanatory hero focus with tech-context support",
    "palette": "teal, icy blue, white, deep navy",
    "mood": "intelligent, innovative, trustworthy",
    "textTreatment": "clean modern text support, medium weight, no forced shouting",
    "graphicAccentsPreference": "data halos, schematic lines, subtle interface cues",
    "overlays": "soft grid light and analytical glow panels",
    "promptFragment": "premium AI-tech explainer thumbnail with smart clarity, modern innovation cues, and readable explanatory hierarchy",
    "promptRules": [
      "innovation + explanation at a glance",
      "tasteful tech signals only",
      "trustworthy modern polish"
    ],
    "negativeRules": [
      "no cheesy robot clichés",
      "no fake dashboards covering the core subject",
      "no overloaded charts"
    ],
    "accent": "#7cf7e6",
    "secondary": "#70a1ff",
    "uiHint": "Explainer",
    "background": "radial-gradient(circle at 24% 20%, rgba(124,247,230,0.58), rgba(112,161,255,0.28) 34%, rgba(7,12,24,1) 80%)"
  },
  "futuristic_ui": {
    "value": "futuristic_ui",
    "key": "futuristic_ui",
    "label": "Futuristic UI",
    "shortDescription": "Interface-inspired future aesthetic with strong geometry and premium HUD restraint.",
    "compositionBias": "style-forward geometry supporting a central hero focus",
    "palette": "cyan, indigo, silver, obsidian",
    "mood": "precise, advanced, sleek",
    "textTreatment": "thin-to-medium sci-fi supportive text, tightly controlled",
    "graphicAccentsPreference": "HUD frames, scans, circular guides, glass panels",
    "overlays": "clean holographic overlays and interface glints",
    "promptFragment": "sleek futuristic interface-inspired thumbnail with premium HUD restraint, geometric polish, and a strong central focal hierarchy",
    "promptRules": [
      "interface geometry as design language, not clutter",
      "strong central readability",
      "advanced premium feel"
    ],
    "negativeRules": [
      "no dense dashboard clutter",
      "no unreadable micro-elements",
      "no full-screen fake UI takeover"
    ],
    "accent": "#7af0ff",
    "secondary": "#8f7cff",
    "uiHint": "HUD polish",
    "background": "radial-gradient(circle at 24% 18%, rgba(122,240,255,0.54), rgba(143,124,255,0.24) 34%, rgba(6,10,22,1) 80%)"
  },
  "glitch_signal": {
    "value": "glitch_signal",
    "key": "glitch_signal",
    "label": "Glitch Signal",
    "shortDescription": "Controlled signal corruption aesthetic with deliberate disruption and readable focal anchor.",
    "compositionBias": "focal anchor first, glitch treatment second",
    "palette": "mint green, hot magenta, deep navy",
    "mood": "unstable, digital, tense",
    "textTreatment": "short crisp text support with occasional digital texture",
    "graphicAccentsPreference": "scanlines, signal tears, channel splits",
    "overlays": "selective glitch bands and digital breakup",
    "promptFragment": "controlled glitch-signal thumbnail where digital distortion feels intentional, premium, and never destroys the main focal anchor",
    "promptRules": [
      "intentional digital disruption",
      "hero remains readable",
      "sharp contemporary finish"
    ],
    "negativeRules": [
      "no full-frame corruption",
      "no broken-image sludge",
      "no unreadable visual mess"
    ],
    "accent": "#8eff8c",
    "secondary": "#ff6ef2",
    "uiHint": "Signal break",
    "background": "radial-gradient(circle at 24% 20%, rgba(142,255,140,0.58), rgba(255,110,242,0.28) 32%, rgba(4,10,18,1) 78%)"
  },
  "dark_system": {
    "value": "dark_system",
    "key": "dark_system",
    "label": "Dark System",
    "shortDescription": "Cold shadow-heavy system aesthetic with disciplined contrast and ominous structure.",
    "compositionBias": "low-key structure with disciplined focal isolation",
    "palette": "graphite, steel blue, muted red",
    "mood": "controlled, severe, ominous",
    "textTreatment": "stark minimal support with industrial contrast",
    "graphicAccentsPreference": "system bars, hard-edge glows, sparse diagnostics",
    "overlays": "shadow gradients and subtle machine-light strips",
    "promptFragment": "dark system-grade thumbnail with cold disciplined contrast, ominous atmosphere, and precise focal isolation",
    "promptRules": [
      "cold disciplined darkness",
      "focal isolation via structure",
      "ominous but premium"
    ],
    "negativeRules": [
      "no muddy darkness",
      "no chaotic cyber clutter",
      "no noisy red alerts everywhere"
    ],
    "accent": "#8da2c9",
    "secondary": "#ff6b6b",
    "uiHint": "System dark",
    "background": "radial-gradient(circle at 24% 18%, rgba(141,162,201,0.42), rgba(255,107,107,0.18) 34%, rgba(8,9,15,1) 80%)"
  },
  "cinematic_dark": {
    "value": "cinematic_dark",
    "key": "cinematic_dark",
    "label": "Cinematic Dark",
    "shortDescription": "Premium film-poster darkness with dramatic light shaping and story-driven weight.",
    "compositionBias": "cinematic subject hierarchy with dramatic negative space",
    "palette": "gold ember, teal shadow, black",
    "mood": "serious, expensive, dramatic",
    "textTreatment": "elegant bold title support, restrained and premium",
    "graphicAccentsPreference": "light shafts, haze, subtle lens bloom",
    "overlays": "film-grade vignettes and dramatic glow pockets",
    "promptFragment": "premium dark cinematic poster frame with dramatic motivated light, intentional hierarchy, and expensive story-driven mood",
    "promptRules": [
      "opening-film still / premium poster",
      "dramatic light and negative space",
      "story-driven expensive mood"
    ],
    "negativeRules": [
      "no cheap clickbait styling",
      "no fake UI, arrows, badges, or circles",
      "no tabloid clutter"
    ],
    "accent": "#f6d365",
    "secondary": "#5ee7df",
    "uiHint": "Poster-grade",
    "background": "radial-gradient(circle at 24% 18%, rgba(246,211,101,0.76), rgba(94,231,223,0.24) 34%, rgba(10,16,28,1) 80%)"
  },
  "mystery_horror": {
    "value": "mystery_horror",
    "key": "mystery_horror",
    "label": "Mystery Horror",
    "shortDescription": "Suspenseful eerie frame driven by unknown threat, darkness, and controlled dread.",
    "compositionBias": "threat-aware focal composition with obscured mystery zones",
    "palette": "crimson, sickly amber, midnight black",
    "mood": "eerie, tense, unsettling",
    "textTreatment": "minimal ominous support, never comedic or campy",
    "graphicAccentsPreference": "mist, scratches, selective warning glows",
    "overlays": "fog, shadow bloom, distressed light leaks",
    "promptFragment": "eerie mystery-horror thumbnail with controlled dread, unknown threat energy, atmospheric darkness, and readable fear focus",
    "promptRules": [
      "suspense and unknown threat",
      "selective highlights + shadow",
      "premium grounded horror"
    ],
    "negativeRules": [
      "no gore overload",
      "no comedy-horror exaggeration",
      "no slasher-poster clichés"
    ],
    "accent": "#ff6b6b",
    "secondary": "#ffd166",
    "uiHint": "Dread mood",
    "background": "radial-gradient(circle at 22% 18%, rgba(255,107,107,0.55), rgba(255,209,102,0.18) 34%, rgba(8,8,12,1) 80%)"
  },
  "epic_fantasy": {
    "value": "epic_fantasy",
    "key": "epic_fantasy",
    "label": "Epic Fantasy",
    "shortDescription": "Mythic adventure framing with grand scale, luminous atmosphere, and heroic focus.",
    "compositionBias": "heroic central subject with sweeping world support",
    "palette": "royal blue, gold, emerald, dusk purple",
    "mood": "mythic, aspirational, grand",
    "textTreatment": "ornate-but-readable support with premium fantasy restraint",
    "graphicAccentsPreference": "magic particles, aura rims, sweeping light arcs",
    "overlays": "atmospheric mist, enchanted glow, cinematic embers",
    "promptFragment": "epic fantasy thumbnail with mythic scale, luminous atmosphere, adventurous silhouette, and heroic readability",
    "promptRules": [
      "grand mythic world",
      "iconic adventurous silhouette",
      "luminous atmosphere without losing clarity"
    ],
    "negativeRules": [
      "no cheap game-ad clutter",
      "no muddy fantasy chaos",
      "no noisy spell overload"
    ],
    "accent": "#ffd166",
    "secondary": "#7bed9f",
    "uiHint": "Mythic hero",
    "background": "radial-gradient(circle at 24% 18%, rgba(255,209,102,0.72), rgba(123,237,159,0.22) 34%, rgba(10,14,32,1) 80%)"
  },
  "emotional_story": {
    "value": "emotional_story",
    "key": "emotional_story",
    "label": "Emotional Story",
    "shortDescription": "Human-centered thumbnail driven by feeling, intimacy, and sincere visual storytelling.",
    "compositionBias": "face-and-feeling dominant composition with soft support",
    "palette": "rose, warm amber, muted blue-gray",
    "mood": "empathetic, sincere, intimate",
    "textTreatment": "short emotional support phrase, gentle but clear",
    "graphicAccentsPreference": "soft flares, depth haze, subtle highlights",
    "overlays": "warm bloom and emotional atmosphere layers",
    "promptFragment": "emotionally resonant thumbnail with intimate storytelling, strong human readability, and sincere feeling-first composition",
    "promptRules": [
      "emotion first in faces and posture",
      "intimate not melodramatic",
      "softness supports clarity"
    ],
    "negativeRules": [
      "no soap-opera excess",
      "no manipulative clutter",
      "no text overload"
    ],
    "accent": "#ff9aa2",
    "secondary": "#ffd6a5",
    "uiHint": "Human feeling",
    "background": "radial-gradient(circle at 24% 20%, rgba(255,154,162,0.66), rgba(255,214,165,0.26) 36%, rgba(14,16,28,1) 80%)"
  },
  "minimal_premium": {
    "value": "minimal_premium",
    "key": "minimal_premium",
    "label": "Minimal Premium",
    "shortDescription": "Refined luxury thumbnail with restrained composition, space, and premium finish.",
    "compositionBias": "style-led minimal hierarchy with elegant negative space",
    "palette": "ivory, soft gold, charcoal, muted taupe",
    "mood": "quiet, refined, premium",
    "textTreatment": "short elegant support, small footprint, no aggressive styling",
    "graphicAccentsPreference": "micro glows, fine lines, subtle gradients",
    "overlays": "soft vignette and polished light wash",
    "promptFragment": "minimal premium thumbnail with refined restraint, elegant spacing, luxury polish, and strong curated hierarchy",
    "promptRules": [
      "restraint and elegant spacing",
      "minimal accents",
      "luxurious highly curated feel"
    ],
    "negativeRules": [
      "no loud clickbait graphics",
      "no overcrowded text",
      "no decorative noise"
    ],
    "accent": "#f4d7a1",
    "secondary": "#dfe7fd",
    "uiHint": "Minimal luxe",
    "background": "radial-gradient(circle at 24% 18%, rgba(244,215,161,0.62), rgba(223,231,253,0.24) 34%, rgba(18,16,18,1) 80%)"
  }
};

const INTRO_STYLE_PRESETS = Object.keys(INTRO_STYLE_PRESET_META);
const INTRO_COMFY_REF_ROLES = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"];
const INTRO_CAST_ROLES = ["character_1", "character_2", "character_3", "animal", "group"];

const PARSE_PROGRESS_PHRASES = [
  "Анализирую входы",
  "Собираю смысл сцены",
  "Проверяю AUDIO / TEXT / REF",
  "Строю логику клипа",
  "Ищу переходы между сценами",
  "Определяю ритм и структуру",
  "Подготавливаю storyboard",
];

function formatParseTimer(seconds) {
  const safe = Math.max(0, Math.floor(Number(seconds) || 0));
  const mm = Math.floor(safe / 60);
  const ss = safe % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function estimateSceneCount(durationSec) {
  const raw = Number(durationSec);
  const safeDuration = Number.isFinite(raw) && raw > 0 ? raw : 30;
  const estimated = Math.round(safeDuration / 4);
  return Math.max(4, Math.min(24, estimated));
}

function normalizeSceneImageFormat(format) {
  return SCENE_IMAGE_FORMATS.includes(format) ? format : DEFAULT_SCENE_IMAGE_FORMAT;
}

function resolvePreferredSceneFormat(...candidates) {
  for (const candidate of candidates) {
    if (!String(candidate || "").trim()) continue;
    return normalizeSceneImageFormat(candidate);
  }
  return DEFAULT_SCENE_IMAGE_FORMAT;
}

function getSceneImageSize(format) {
  const normalized = normalizeSceneImageFormat(format);
  if (normalized === "1:1") return { width: 1024, height: 1024 };
  if (normalized === "16:9") return { width: 1344, height: 768 };
  return { width: 768, height: 1344 };
}

function normalizeIntroStylePreset(stylePreset) {
  const normalized = String(stylePreset || "").trim().toLowerCase();
  return INTRO_STYLE_PRESETS.includes(normalized) ? normalized : "cinematic_dark";
}

function parseLocaleFloat(value) {
  if (typeof value === "number") return value;
  const normalized = String(value ?? "").trim().replace(",", ".");
  if (!normalized) return NaN;
  return Number(normalized);
}

function normalizeIntroDurationSec(value) {
  const raw = parseLocaleFloat(value);
  if (!Number.isFinite(raw)) return 2.5;
  return Math.max(0.5, Math.min(8, Math.round(raw * 10) / 10));
}

function getIntroStyleMeta(stylePreset = "cinematic_dark") {
  const normalized = normalizeIntroStylePreset(stylePreset);
  return INTRO_STYLE_PRESET_META[normalized] || INTRO_STYLE_PRESET_META.cinematic_dark;
}

function resolveIntroCompositionPlan({ refsByRole = {}, heroParticipants = [], supportingParticipants = [], importantProps = [] } = {}) {
  const castRoles = INTRO_CAST_ROLES.filter((role) => Array.isArray(refsByRole?.[role]) && refsByRole[role].length > 0);
  const hasPropRefs = Array.isArray(refsByRole?.props) && refsByRole.props.length > 0;
  const roleSignals = [...heroParticipants, ...supportingParticipants].filter((role, index, arr) => INTRO_CAST_ROLES.includes(role) && arr.indexOf(role) === index);
  const propSignals = (Array.isArray(importantProps) ? importantProps : []).map((item) => String(item || "").trim()).filter(Boolean);
  const hasSubjects = castRoles.length > 0 || hasPropRefs || roleSignals.length > 0 || propSignals.length > 0;
  const focusTargets = [...castRoles, ...roleSignals.filter((role) => !castRoles.includes(role)), ...(hasPropRefs ? ["props"] : [])];
  return {
    mode: hasSubjects ? "subject_led" : "style_led",
    label: hasSubjects ? "Subject-led composition" : "Style-led composition",
    focusTargets,
    weightSummary: hasSubjects ? "subject 50–60% • support 40–50%" : "style-led balance • single focal hierarchy",
    summary: hasSubjects
      ? "Subjects must stay dominant, readable, and never be overpowered by text or graphics."
      : "Preset mood, palette, atmosphere, and type treatment drive the frame when no strong subjects are present.",
  };
}

function resolveSceneTransitionType(scene) {
  const imageStrategy = String(scene?.imageStrategy || deriveScenarioImageStrategy(scene)).trim().toLowerCase();
  if (imageStrategy === "continuation" || imageStrategy === "first_last") return "continuous";
  const raw = String(scene?.transitionType || "single").toLowerCase();
  if (raw === "continuous" || raw === "continuation" || raw === "enter_transition") return "continuous";
  if (raw === "hard_cut" || raw === "justified_cut") return "hard_cut";
  if (raw === "single" || raw === "match_cut" || raw === "perspective_shift" || raw === "start") return "single";
  return "single";
}

function getSceneTypeBadge(type) {
  if (type === "continuous") return "CONTINUOUS";
  if (type === "hard_cut") return "HARD CUT";
  return "SINGLE";
}

function getScenePrimaryFramePrompt(scene) {
  return String(scene?.imagePromptRu || scene?.framePrompt || scene?.imagePrompt || scene?.prompt || "").trim();
}

function deriveFirstLastFramePrompts(scene = {}) {
  const explicitStart = String(scene?.startFramePromptRu || scene?.startFramePromptEn || scene?.startFramePrompt || "").trim();
  const explicitEnd = String(scene?.endFramePromptRu || scene?.endFramePromptEn || scene?.endFramePrompt || "").trim();
  if (explicitStart && explicitEnd) {
    return { start: explicitStart, end: explicitEnd, derived: false };
  }

  const sceneGoal = String(scene?.sceneGoal || "").trim();
  const frameDescription = String(scene?.frameDescription || "").trim();
  const imagePrompt = String(scene?.imagePromptRu || scene?.imagePromptEn || scene?.imagePrompt || "").trim();
  const videoPrompt = String(scene?.videoPromptRu || scene?.videoPromptEn || scene?.videoPrompt || "").trim();
  const transitionType = String(scene?.transitionType || "state shift").trim().replaceAll("_", " ");
  const transitionSemantics = videoPrompt || `First→last transition with ${transitionType}.`;

  const start = explicitStart || frameDescription || sceneGoal || imagePrompt || videoPrompt;
  let end = explicitEnd || sceneGoal || imagePrompt || frameDescription || videoPrompt;
  if (start) {
    end = end || start;
    end = `${end}. Final changed state after transition: ${transitionSemantics}. Enforce clear A→B composition delta and changed subject positions.`;
  }
  if (start && end && isNearDuplicateSceneText(start, end)) {
    end = `${end}. Do not repeat opening composition; make the visual change readable in a single still frame.`;
  }
  return { start, end, derived: true };
}

function getSceneFramePromptByStrategy(scene, slot = "single") {
  const strategy = String(scene?.imageStrategy || deriveScenarioImageStrategy(scene)).trim().toLowerCase();
  const derivedFirstLast = deriveFirstLastFramePrompts(scene || {});
  if (strategy === "first_last") {
    if (slot === "end") return String(scene?.endFramePromptRu || scene?.endFramePromptEn || scene?.endFramePrompt || derivedFirstLast.end || scene?.imagePromptRu || scene?.imagePrompt || "").trim();
    return String(scene?.startFramePromptRu || scene?.startFramePromptEn || scene?.startFramePrompt || derivedFirstLast.start || scene?.imagePromptRu || scene?.imagePrompt || "").trim();
  }
  if (strategy === "continuation" && slot === "start") {
    return String(scene?.startFramePromptRu || scene?.startFramePromptEn || scene?.startFramePrompt || derivedFirstLast.start || scene?.imagePromptRu || scene?.imagePrompt || "").trim();
  }
  return getScenePrimaryFramePrompt(scene);
}

function getSceneTransitionPrompt(scene) {
  return String(scene?.videoPromptRu || scene?.transitionActionPrompt || scene?.videoPrompt || "").trim();
}

const COMFY_SYNC_STATUS_LABELS = {
  [PROMPT_SYNC_STATUS.synced]: "synced",
  [PROMPT_SYNC_STATUS.syncing]: "syncing...",
  [PROMPT_SYNC_STATUS.needsSync]: "needs sync",
  [PROMPT_SYNC_STATUS.syncError]: "sync error",
};


const SCENE_ROLE_FLOW = ["establishing", "reveal", "escalation", "hidden_detail", "payoff", "ending"];

function normalizeSceneRole(role, idx = 0) {
  const raw = String(role || "").trim().toLowerCase();
  if (SCENE_ROLE_FLOW.includes(raw)) return raw;
  return SCENE_ROLE_FLOW[Math.max(0, idx) % SCENE_ROLE_FLOW.length];
}

function normalizeForDupCheck(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function isNearDuplicateSceneText(a, b) {
  const ta = normalizeForDupCheck(a);
  const tb = normalizeForDupCheck(b);
  if (!ta || !tb) return false;
  if (ta === tb) return true;
  const wa = new Set(ta.split(" ").filter(Boolean));
  const wb = new Set(tb.split(" ").filter(Boolean));
  if (!wa.size || !wb.size) return false;
  let common = 0;
  wa.forEach((w) => {
    if (wb.has(w)) common += 1;
  });
  const ratio = common / Math.max(wa.size, wb.size);
  return ratio >= 0.82;
}

function getRoleDiversityDirective(sceneRole) {
  const role = normalizeSceneRole(sceneRole);
  if (role === "establishing") return "Role: establishing. Show clear geography and baseline spatial relations of the same place.";
  if (role === "reveal") return "Role: reveal. Expose a new meaningful layer of the same scene without changing location geometry.";
  if (role === "escalation") return "Role: escalation. Increase tension with motion, framing or light shift, not by adding new major objects.";
  if (role === "hidden_detail") return "Role: hidden detail. Focus on subtle detail that was present but unnoticed in the same scene.";
  if (role === "payoff") return "Role: payoff. Deliver the strongest visual consequence of previous beats in the same world.";
  return "Role: ending. Resolve the moment with visual closure while preserving scene continuity.";
}

function buildContinuousContinuityBridge({ scene, previousScene }) {
  const roleDirective = getRoleDiversityDirective(scene?.sceneRole);
  const baseline = [
    "Continuous bridge between START and END of the SAME scene/time window.",
    "Treat start and end images as one uninterrupted environment.",
    "Do NOT introduce any new major objects, characters, vehicles, machines, buildings, or terrain features.",
    "Do NOT alter core landscape geometry, topology, architecture layout, or object count.",
    "No sudden appearing/disappearing subjects that are absent in both anchor frames.",
    "Allowed: smooth camera motion, subtle natural motion, atmosphere, dust, light shift, micro-physics.",
    roleDirective,
  ];
  const prevMemory = String(previousScene?.continuityMemory?.summary || scene?.previousContinuityMemory?.summary || "").trim();
  if (prevMemory) baseline.push(`Carry continuity memory: ${prevMemory}`);
  return baseline.join(" ");
}

function getSceneRequestedDurationSec(scene) {
  const explicit = normalizeDurationSec(scene?.requestedDurationSec);
  if (explicit != null) return explicit;
  const t0 = Number(scene?.t0 ?? scene?.start ?? 0);
  const t1 = Number(scene?.t1 ?? scene?.end ?? 0);
  const fallback = t1 - t0;
  return Number.isFinite(fallback) ? Math.max(0, fallback) : 0;
}

const INTRO_FRAME_PREVIEW_KINDS = {
  UPLOADED: "uploaded_image",
  BACKEND_GENERATED: "backend_generated",
  GENERATED_LOCAL: "generated_local",
};

const INTRO_FRAME_PREVIEW_FORMATS = {
  PORTRAIT: "9:16",
  SQUARE: "1:1",
  LANDSCAPE: "16:9",
};

const INTRO_FRAME_PREVIEW_FORMAT_OPTIONS = [
  { value: INTRO_FRAME_PREVIEW_FORMATS.PORTRAIT, label: "9:16" },
  { value: INTRO_FRAME_PREVIEW_FORMATS.SQUARE, label: "1:1" },
  { value: INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE, label: "16:9" },
];

const INTRO_TITLE_RECOMMENDED_CHARS = 40;
const INTRO_HOOK_TITLE_MAX_CHARS = 64;

function normalizeIntroFramePreviewKind(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === INTRO_FRAME_PREVIEW_KINDS.UPLOADED) return INTRO_FRAME_PREVIEW_KINDS.UPLOADED;
  if (normalized === INTRO_FRAME_PREVIEW_KINDS.BACKEND_GENERATED) return INTRO_FRAME_PREVIEW_KINDS.BACKEND_GENERATED;
  if (normalized === INTRO_FRAME_PREVIEW_KINDS.GENERATED_LOCAL) return INTRO_FRAME_PREVIEW_KINDS.GENERATED_LOCAL;
  return "";
}

function normalizeIntroFramePreviewFormat(value) {
  const normalized = String(value || "").trim();
  if (INTRO_FRAME_PREVIEW_FORMAT_OPTIONS.some((option) => option.value === normalized)) return normalized;
  return INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE;
}

function getIntroFramePreviewFormatMeta(value) {
  const format = normalizeIntroFramePreviewFormat(value);
  if (format === INTRO_FRAME_PREVIEW_FORMATS.PORTRAIT) {
    return {
      value: format,
      label: "9:16",
      aspectRatio: "9 / 16",
      width: 576,
      height: 1024,
      titleMaxChars: 52,
      titleMaxLines: 4,
      titleFontPx: 56,
      titleLineHeight: 60,
      contextMaxChars: 92,
      contextMaxLines: 2,
      contextFontPx: 26,
      contextLineHeight: 32,
      paddingX: 42,
      paddingTop: 48,
      accentY: 748,
      accentWidth: 492,
      contextY: 790,
      titleBoxHeight: 300,
      contextBoxHeight: 88,
      cardMinHeight: 320,
      surfacePadding: 18,
    };
  }
  if (format === INTRO_FRAME_PREVIEW_FORMATS.SQUARE) {
    return {
      value: format,
      label: "1:1",
      aspectRatio: "1 / 1",
      width: 768,
      height: 768,
      titleMaxChars: 56,
      titleMaxLines: 3,
      titleFontPx: 52,
      titleLineHeight: 56,
      contextMaxChars: 96,
      contextMaxLines: 2,
      contextFontPx: 24,
      contextLineHeight: 30,
      paddingX: 42,
      paddingTop: 42,
      accentY: 568,
      accentWidth: 684,
      contextY: 606,
      titleBoxHeight: 236,
      contextBoxHeight: 78,
      cardMinHeight: 260,
      surfacePadding: 18,
    };
  }
  return {
    value: INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE,
    label: "16:9",
    aspectRatio: "16 / 9",
    width: 640,
    height: 360,
    titleMaxChars: 64,
    titleMaxLines: 3,
    titleFontPx: 40,
    titleLineHeight: 44,
    contextMaxChars: 120,
    contextMaxLines: 2,
    contextFontPx: 14,
    contextLineHeight: 18,
    paddingX: 28,
    paddingTop: 28,
    accentY: 264,
    accentWidth: 584,
    contextY: 292,
    titleBoxHeight: 116,
    contextBoxHeight: 36,
    cardMinHeight: 180,
    surfacePadding: 14,
  };
}

function getEffectiveIntroFramePreviewKind(introFrame) {
  const previewKind = normalizeIntroFramePreviewKind(introFrame?.previewKind);
  if (previewKind) return previewKind;
  return String(introFrame?.imageUrl || "").trim() ? INTRO_FRAME_PREVIEW_KINDS.UPLOADED : "";
}

function resolveIntroFramePreviewUrl(introFrame) {
  if (!introFrame) return "";
  const previewKind = getEffectiveIntroFramePreviewKind(introFrame);
  const imageUrl = String(introFrame?.imageUrl || "").trim();
  if (previewKind === INTRO_FRAME_PREVIEW_KINDS.GENERATED_LOCAL) {
    return buildIntroFramePreviewDataUrl({
      stylePreset: introFrame?.stylePreset || "cinematic_dark",
      previewFormat: introFrame?.previewFormat,
    });
  }
  return imageUrl;
}

function hasIntroFramePreview(introFrame) {
  if (!introFrame) return false;
  const previewKind = getEffectiveIntroFramePreviewKind(introFrame);
  if (previewKind === INTRO_FRAME_PREVIEW_KINDS.GENERATED_LOCAL) {
    return !!String(introFrame?.userTitleRaw ?? introFrame?.title ?? "").trim()
      || !!String(introFrame?.derivedTitle || "").trim()
      || !!String(introFrame?.generatedAt || "").trim();
  }
  return !!String(introFrame?.imageUrl || "").trim();
}

function buildIntroFrameComparablePayload(introFrame) {
  if (!introFrame || !hasIntroFramePreview(introFrame)) return null;
  const previewKind = getEffectiveIntroFramePreviewKind(introFrame);
  const manualTitleRaw = String(introFrame?.userTitleRaw ?? introFrame?.title ?? "");
  return {
    nodeId: String(introFrame?.nodeId || ""),
    title: manualTitleRaw.trim(),
    manualTitleRaw,
    autoTitle: !!introFrame?.autoTitle,
    stylePreset: normalizeIntroStylePreset(introFrame?.stylePreset || "cinematic_dark"),
    durationSec: normalizeIntroDurationSec(introFrame?.durationSec),
    previewFormat: normalizeIntroFramePreviewFormat(introFrame?.previewFormat),
    previewKind,
    generatedAt: String(introFrame?.generatedAt || "").trim() || null,
    previewTitleUsed: String(introFrame?.previewTitleUsed || "").trim() || null,
    imageUrl: (
      previewKind === INTRO_FRAME_PREVIEW_KINDS.UPLOADED
      || previewKind === INTRO_FRAME_PREVIEW_KINDS.BACKEND_GENERATED
    ) ? String(introFrame?.imageUrl || "").trim() : "",
  };
}

function buildIntroFramePayload(introFrame) {
  const comparable = buildIntroFrameComparablePayload(introFrame);
  const imageUrl = resolveIntroFramePreviewUrl(introFrame);
  if (!comparable || !imageUrl) return null;
  return {
    ...comparable,
    imageUrl,
  };
}

function buildAssemblyPayload({ scenes = [], audioUrl = "", format = "9:16", intro = null }) {
  const normalizedFormat = normalizeSceneImageFormat(format);
  const safeAudioUrl = String(audioUrl || "").trim();
  const preparedScenes = (Array.isArray(scenes) ? scenes : [])
    .map((scene, idx) => {
      const videoUrl = String(scene?.videoUrl || "").trim();
      if (!videoUrl) return null;
      return {
        sceneId: buildCanonicalSceneId(scene, idx, "scene"),
        videoUrl,
        requestedDurationSec: getSceneRequestedDurationSec(scene),
        transitionType: resolveSceneTransitionType(scene),
        order: Number.isFinite(Number(scene?.order)) ? Number(scene.order) : idx + 1,
      };
    })
    .filter(Boolean);

  return {
    audioUrl: safeAudioUrl,
    format: normalizedFormat,
    scenes: preparedScenes,
    intro: buildIntroFramePayload(intro),
  };
}

function extractStoryboardScenesFromNodes(nodes = []) {
  const scenarioStoryboardNode = (Array.isArray(nodes) ? nodes : []).find((n) => n?.type === "scenarioStoryboard") || null;
  if (Array.isArray(scenarioStoryboardNode?.data?.scenes)) return scenarioStoryboardNode.data.scenes;
  const storyboardNode = (Array.isArray(nodes) ? nodes : []).find((n) => n?.type === "storyboardNode") || null;
  return Array.isArray(storyboardNode?.data?.scenes) ? storyboardNode.data.scenes : [];
}

function normalizeComfyScenesForAssembly(scenes = [], fallbackFormat = DEFAULT_SCENE_IMAGE_FORMAT) {
  return normalizeSceneCollectionWithSceneId(scenes, "comfy_scene").map((scene, idx) => {
    const durationFromScene = normalizeDurationSec(scene?.durationSec);
    const requestedDurationSec = durationFromScene != null
      ? durationFromScene
      : getSceneRequestedDurationSec({
        requestedDurationSec: scene?.requestedDurationSec,
        t0: scene?.startSec,
        t1: scene?.endSec,
      });
    const normalizedFormat = resolvePreferredSceneFormat(scene?.imageFormat, scene?.format, fallbackFormat);
    return {
      ...scene,
      sceneId: buildCanonicalSceneId(scene, idx, "comfy_scene"),
      requestedDurationSec,
      order: Number.isFinite(Number(scene?.order)) ? Number(scene.order) : idx + 1,
      transitionType: resolveSceneTransitionType(scene),
      imageFormat: normalizedFormat,
      format: normalizedFormat,
    };
  });
}

function getAssemblySourceLabel(scenesSource = "none") {
  if (scenesSource === "comfyStoryboard") return "COMFY STORYBOARD";
  if (scenesSource === "scenarioStoryboard") return "SCENARIO STORYBOARD";
  if (scenesSource === "storyboard") return "STORYBOARD";
  return "НЕ ПОДКЛЮЧЕНО";
}

function getAssemblyIntroLabel(introSourceType = "none") {
  if (introSourceType === "introFrame") return "INTRO FRAME";
  return "НЕТ INTRO";
}

function removeAssemblyIncomingSourceEdges(edges = [], assemblyNodeId = "", targetHandle = "assembly_in") {
  const normalizedAssemblyNodeId = String(assemblyNodeId || "").trim();
  const normalizedTargetHandle = String(targetHandle || "").trim() || "assembly_in";
  return (Array.isArray(edges) ? edges : []).filter((edge) => {
    if (String(edge?.target || "") !== normalizedAssemblyNodeId) return true;
    return String(edge?.targetHandle || "") !== normalizedTargetHandle;
  });
}

function extractComfyScenesFromNodes(nodes = []) {
  const comfyStoryboardNode = (Array.isArray(nodes) ? nodes : []).find((n) => n?.type === "comfyStoryboard") || null;
  const fallbackFormat = resolvePreferredSceneFormat(
    comfyStoryboardNode?.data?.format,
    comfyStoryboardNode?.data?.plannerMeta?.format,
    comfyStoryboardNode?.data?.plannerMeta?.plannerInput?.format
  );
  return normalizeComfyScenesForAssembly(comfyStoryboardNode?.data?.mockScenes, fallbackFormat);
}

function resolveAssemblySource({ nodes = [], edges = [], assemblyNodeId = "" } = {}) {
  const nodesList = Array.isArray(nodes) ? nodes : [];
  const edgesList = Array.isArray(edges) ? edges : [];
  const assemblyNode = String(assemblyNodeId || "").trim()
    ? (nodesList.find((node) => node?.id === assemblyNodeId && node?.type === "assemblyNode") || null)
    : (nodesList.find((node) => node?.type === "assemblyNode") || null);
  const effectiveAssemblyNodeId = String(assemblyNode?.id || assemblyNodeId || "").trim();
  const nodesById = new Map(nodesList.map((node) => [node?.id, node]));
  const incomingSourceEdges = effectiveAssemblyNodeId
    ? edgesList.filter((edge) => {
      if (edge?.target !== effectiveAssemblyNodeId) return false;
      if (String(edge?.targetHandle || "") !== "assembly_in") return false;
      const sourceType = String(nodesById.get(edge?.source)?.type || "");
      return sourceType === "storyboardNode" || sourceType === "scenarioStoryboard" || sourceType === "comfyStoryboard";
    })
    : [];
  const incomingEdge = incomingSourceEdges.length ? incomingSourceEdges[incomingSourceEdges.length - 1] : null;
  const sourceNode = incomingEdge ? (nodesById.get(incomingEdge.source) || null) : null;
  const incomingIntroEdges = effectiveAssemblyNodeId
    ? edgesList.filter((edge) => {
      if (edge?.target !== effectiveAssemblyNodeId) return false;
      if (String(edge?.targetHandle || "") !== "assembly_intro") return false;
      return String(nodesById.get(edge?.source)?.type || "") === "introFrame";
    })
    : [];
  const introEdge = incomingIntroEdges.length ? incomingIntroEdges[incomingIntroEdges.length - 1] : null;
  const introNode = introEdge ? (nodesById.get(introEdge.source) || null) : null;
  const introFrame = introNode?.type === "introFrame"
    ? {
      nodeId: String(introNode?.id || ""),
      nodeType: "introFrame",
      title: String(introNode?.data?.userTitleRaw ?? introNode?.data?.title ?? "").trim(),
      manualTitleRaw: String(introNode?.data?.userTitleRaw ?? introNode?.data?.title ?? ""),
      autoTitle: !!introNode?.data?.autoTitle,
      stylePreset: normalizeIntroStylePreset(introNode?.data?.stylePreset || "cinematic_dark"),
      durationSec: normalizeIntroDurationSec(introNode?.data?.durationSec),
      previewFormat: normalizeIntroFramePreviewFormat(introNode?.data?.previewFormat),
      imageUrl: String(introNode?.data?.imageUrl || "").trim(),
      previewKind: getEffectiveIntroFramePreviewKind(introNode?.data),
      generatedAt: String(introNode?.data?.generatedAt || "").trim(),
      previewTitleUsed: String(introNode?.data?.previewTitleUsed || "").trim(),
      status: String(introNode?.data?.status || "idle"),
      contextSummary: String(introNode?.data?.contextSummary || "").trim(),
      altTitles: Array.isArray(introNode?.data?.altTitles) ? introNode.data.altTitles : [],
    }
    : null;
  if (CLIP_TRACE_SCENARIO_GRAPH) {
    const sourceType = String(sourceNode?.type || "");
    console.debug("[SCENARIO GRAPH] assembly sources", {
      hasIntroFrame: !!introFrame,
      hasScenarioStoryboard: sourceType === "scenarioStoryboard",
      introHandle: introEdge ? String(introEdge?.targetHandle || "") : "",
      storyboardHandle: incomingEdge ? String(incomingEdge?.targetHandle || "") : "",
    });
  }

  if (sourceNode?.type === "storyboardNode") {
    const scenes = Array.isArray(sourceNode?.data?.scenes) ? sourceNode.data.scenes : [];
    return {
      assemblyNodeId: effectiveAssemblyNodeId,
      sourceNodeId: String(sourceNode?.id || ""),
      sourceNodeType: "storyboardNode",
      scenesSource: "storyboard",
      scenes,
      introSourceNodeId: String(introFrame?.nodeId || ""),
      introSourceNodeType: introFrame?.nodeType || "",
      introFrame,
    };
  }

  if (sourceNode?.type === "scenarioStoryboard") {
    const scenes = Array.isArray(sourceNode?.data?.scenes) ? sourceNode.data.scenes : [];
    return {
      assemblyNodeId: effectiveAssemblyNodeId,
      sourceNodeId: String(sourceNode?.id || ""),
      sourceNodeType: "scenarioStoryboard",
      scenesSource: "scenarioStoryboard",
      scenes,
      introSourceNodeId: String(introFrame?.nodeId || ""),
      introSourceNodeType: introFrame?.nodeType || "",
      introFrame,
    };
  }

  if (sourceNode?.type === "comfyStoryboard") {
    const sourceFormat = resolvePreferredSceneFormat(
      sourceNode?.data?.format,
      sourceNode?.data?.plannerMeta?.format,
      sourceNode?.data?.plannerMeta?.plannerInput?.format
    );
    const scenes = normalizeComfyScenesForAssembly(sourceNode?.data?.mockScenes, sourceFormat);
    return {
      assemblyNodeId: effectiveAssemblyNodeId,
      sourceNodeId: String(sourceNode?.id || ""),
      sourceNodeType: "comfyStoryboard",
      scenesSource: "comfyStoryboard",
      scenes,
      scenarioFormat: sourceFormat,
      introSourceNodeId: String(introFrame?.nodeId || ""),
      introSourceNodeType: introFrame?.nodeType || "",
      introFrame,
    };
  }

  return {
    assemblyNodeId: effectiveAssemblyNodeId,
    sourceNodeId: "",
    sourceNodeType: "",
    scenesSource: "none",
    scenes: [],
    scenarioFormat: DEFAULT_SCENE_IMAGE_FORMAT,
    introSourceNodeId: String(introFrame?.nodeId || ""),
    introSourceNodeType: introFrame?.nodeType || "",
    introFrame,
  };
}

function extractGlobalAudioUrlFromNodes(nodes = []) {
  const audioNodeWithUrl = (Array.isArray(nodes) ? nodes : []).find((n) => n?.type === "audioNode" && n?.data?.audioUrl);
  return audioNodeWithUrl?.data?.audioUrl ? String(audioNodeWithUrl.data.audioUrl) : "";
}

function resolveScenarioAudioSourceUrlFromNode(node = null, fallbackNodes = []) {
  const sourceNode = node && typeof node === "object" ? node : {};
  const audioData = sourceNode?.data?.audioData && typeof sourceNode.data.audioData === "object" ? sourceNode.data.audioData : {};
  return String(
    audioData?.audioUrl
    || sourceNode?.data?.audioUrl
    || sourceNode?.data?.masterAudioUrl
    || extractGlobalAudioUrlFromNodes(fallbackNodes)
    || ""
  ).trim();
}

function resolveScenarioMasterAudioFromGraph({ scenarioNode = null, nodes = [], edges = [] } = {}) {
  const safeScenarioNode = scenarioNode && typeof scenarioNode === "object" ? scenarioNode : null;
  const scenarioAudioData = safeScenarioNode?.data?.audioData && typeof safeScenarioNode.data.audioData === "object"
    ? safeScenarioNode.data.audioData
    : {};
  const scenarioNodeAudioDataUrl = String(scenarioAudioData?.audioUrl || "").trim();
  if (scenarioNodeAudioDataUrl) {
    return { source: "scenario_node_audioData", url: scenarioNodeAudioDataUrl, connectedSourceAudioUrl: "", globalAudioUrl: extractGlobalAudioUrlFromNodes(nodes) };
  }
  const scenarioNodeAudioUrl = String(safeScenarioNode?.data?.audioUrl || "").trim();
  if (scenarioNodeAudioUrl) {
    return { source: "scenario_node_audioUrl", url: scenarioNodeAudioUrl, connectedSourceAudioUrl: "", globalAudioUrl: extractGlobalAudioUrlFromNodes(nodes) };
  }
  const scenarioNodeMasterAudioUrl = String(safeScenarioNode?.data?.masterAudioUrl || "").trim();
  if (scenarioNodeMasterAudioUrl) {
    return { source: "scenario_node_masterAudioUrl", url: scenarioNodeMasterAudioUrl, connectedSourceAudioUrl: "", globalAudioUrl: extractGlobalAudioUrlFromNodes(nodes) };
  }
  const nodesById = new Map((Array.isArray(nodes) ? nodes : []).map((nodeItem) => [nodeItem.id, nodeItem]));
  const incomingEdge = [...(Array.isArray(edges) ? edges : [])]
    .reverse()
    .find((edge) => edge?.target === safeScenarioNode?.id && String(edge?.targetHandle || "") === "scenario_storyboard_in") || null;
  const connectedSourceNode = incomingEdge?.source ? (nodesById.get(incomingEdge.source) || null) : null;
  const connectedSourceAudioUrl = String(
    connectedSourceNode?.data?.audioData?.audioUrl
    || connectedSourceNode?.data?.audioUrl
    || connectedSourceNode?.data?.masterAudioUrl
    || ""
  ).trim();
  if (connectedSourceAudioUrl) {
    return {
      source: "connected_source_node",
      url: connectedSourceAudioUrl,
      connectedSourceAudioUrl,
      globalAudioUrl: extractGlobalAudioUrlFromNodes(nodes),
    };
  }
  const globalAudioUrl = String(extractGlobalAudioUrlFromNodes(nodes) || "").trim();
  if (globalAudioUrl) {
    return { source: "global_audio_node", url: globalAudioUrl, connectedSourceAudioUrl: "", globalAudioUrl };
  }
  return { source: "missing", url: "", connectedSourceAudioUrl: "", globalAudioUrl: "" };
}

function extractGlobalAudioDurationFromNodes(nodes = []) {
  const audioNodeWithDuration = (Array.isArray(nodes) ? nodes : []).find((n) => n?.type === "audioNode" && Number(n?.data?.audioDurationSec) > 0);
  const durationSec = Number(audioNodeWithDuration?.data?.audioDurationSec || 0);
  return Number.isFinite(durationSec) && durationSec > 0 ? durationSec : 0;
}

function buildAssemblyPayloadSignature(payload, options = {}) {
  const introComparable = buildIntroFrameComparablePayload(payload?.intro);
  return JSON.stringify({
    scenesSource: String(options?.scenesSource || "none"),
    sourceNodeId: String(options?.sourceNodeId || ""),
    assemblyNodeId: String(options?.assemblyNodeId || ""),
    introSourceNodeId: String(options?.introSourceNodeId || ""),
    introSourceNodeType: String(options?.introSourceNodeType || ""),
    audioUrl: payload?.audioUrl || "",
    format: payload?.format || "9:16",
    intro: introComparable
      ? {
        nodeId: introComparable.nodeId,
        previewKind: introComparable.previewKind || "",
        imageUrl: introComparable.imageUrl,
        durationSec: introComparable.durationSec,
        title: introComparable.title,
        stylePreset: introComparable.stylePreset,
        previewFormat: introComparable.previewFormat,
        generatedAt: introComparable.generatedAt,
      }
      : null,
    scenes: Array.isArray(payload?.scenes)
      ? payload.scenes.map((s) => ({
        sceneId: s.sceneId,
        videoUrl: s.videoUrl,
        requestedDurationSec: s.requestedDurationSec,
        transitionType: s.transitionType,
        order: s.order,
      }))
      : [],
  });
}

function getSceneStartImageSource(scene, previousScene) {
  if (resolveSceneTransitionType(scene) !== "continuous") return "none";
  const inheritPreviousEndAsStart = !!scene?.inheritPreviousEndAsStart;
  const previousEndImageUrl = String(previousScene?.endImageUrl || previousScene?.endFrameImageUrl || "").trim();
  const manualStartImageUrl = String(scene?.startImageUrl || scene?.startFrameImageUrl || scene?.startFramePreviewUrl || "").trim();
  if (inheritPreviousEndAsStart && previousEndImageUrl) return "previous_end";
  if (manualStartImageUrl) return "manual";
  return "none";
}

function resolveSceneFrameUrls(scene, previousScene = null) {
  const sourceScene = scene && typeof scene === "object" ? scene : {};
  const sourcePreviousScene = previousScene && typeof previousScene === "object" ? previousScene : {};
  const previousEndImageUrl = String(sourcePreviousScene?.endImageUrl || sourcePreviousScene?.endFrameImageUrl || "").trim();
  const startImageUrl = String(sourceScene?.startImageUrl || sourceScene?.startFrameImageUrl || sourceScene?.startFramePreviewUrl || "").trim();
  const fallbackImageUrl = String(sourceScene?.imageUrl || "").trim();
  const endImageUrl = String(sourceScene?.endImageUrl || sourceScene?.endFrameImageUrl || sourceScene?.endFramePreviewUrl || "").trim();
  const startSource = getSceneStartImageSource(sourceScene, sourcePreviousScene);
  const effectiveStartImageUrl = startSource === "previous_end"
    ? previousEndImageUrl
    : (startImageUrl || fallbackImageUrl);
  return {
    effectiveStartImageUrl,
    endImageUrl,
    fallbackImageUrl,
    sourceOfTruthKeys: {
      start: startSource === "previous_end"
        ? ["previousScene.endImageUrl", "previousScene.endFrameImageUrl"]
        : ["scene.startImageUrl", "scene.startFrameImageUrl", "scene.startFramePreviewUrl", "scene.imageUrl"],
      end: ["scene.endImageUrl", "scene.endFrameImageUrl", "scene.endFramePreviewUrl"],
    },
  };
}

function resolveScenarioScenePreviewSources(scene, previousScene = null) {
  const transitionType = resolveSceneTransitionType(scene);
  const imageStrategy = String(scene?.imageStrategy || deriveScenarioImageStrategy(scene)).trim().toLowerCase() || "single";
  const frameUrls = resolveSceneFrameUrls(scene, previousScene);
  const resolvedStartPreviewSrc = String(resolveAssetUrl(frameUrls.effectiveStartImageUrl || frameUrls.fallbackImageUrl || "") || "").trim();
  const resolvedEndPreviewSrc = String(resolveAssetUrl(frameUrls.endImageUrl || "") || "").trim();
  const resolvedSinglePreviewSrc = String(resolveAssetUrl(scene?.imageUrl || frameUrls.fallbackImageUrl || "") || "").trim();
  const resolvedPreviewSrc = transitionType === "continuous"
    ? (resolvedStartPreviewSrc || resolvedEndPreviewSrc || resolvedSinglePreviewSrc)
    : (resolvedSinglePreviewSrc || resolvedStartPreviewSrc || resolvedEndPreviewSrc);
  return {
    imageStrategy,
    transitionType,
    resolvedPreviewSrc,
    resolvedStartPreviewSrc,
    resolvedEndPreviewSrc,
  };
}

function getEffectiveSceneStartImage(scene, previousScene) {
  if (resolveSceneTransitionType(scene) !== "continuous") {
    return String(scene?.startImageUrl || scene?.startFrameImageUrl || scene?.startFramePreviewUrl || scene?.imageUrl || "").trim();
  }
  return resolveSceneFrameUrls(scene, previousScene).effectiveStartImageUrl;
}

function getSceneVideoPoster(scene, previousScene = null) {
  const transitionType = resolveSceneTransitionType(scene);
  if (transitionType === "continuous") {
    return String(getEffectiveSceneStartImage(scene, previousScene) || scene?.endImageUrl || scene?.imageUrl || "").trim();
  }
  return String(scene?.imageUrl || "").trim();
}

function getSceneListThumb(scene, previousScene = null) {
  const transitionType = resolveSceneTransitionType(scene);
  if (transitionType === "continuous") {
    return String(getEffectiveSceneStartImage(scene, previousScene) || scene?.endImageUrl || scene?.imageUrl || "").trim();
  }
  return String(scene?.imageUrl || "").trim();
}

function isUploadableFile(file) {
  if (!file || typeof file !== "object") return false;
  const hasFileBits = typeof file.name === "string" && typeof file.size === "number";
  const hasBlobBits = typeof file.arrayBuffer === "function" || typeof file.stream === "function";
  return hasFileBits && hasBlobBits;
}

function getUploadDebugPayload(file) {
  return {
    file,
    name: String(file?.name || ""),
    type: String(file?.type || ""),
    size: Number(file?.size || 0),
    lastModified: Number(file?.lastModified || 0) || null,
  };
}

function buildUploadGuardKey(file) {
  const debug = getUploadDebugPayload(file);
  return `${debug.name}::${debug.type}::${debug.size}::${debug.lastModified || 0}`;
}

function extractUploadErrorDetail(error) {
  const raw = String(error?.message || error || "").trim();
  const match = raw.match(/^upload_failed:(\d+):(.*)$/);
  if (!match) return raw || "upload_failed";
  const [, statusCode, detailRaw] = match;
  const detail = String(detailRaw || "").trim();
  if (!detail) return `upload_failed:${statusCode}`;
  if (detail.startsWith("unsupported_ext:")) {
    const ext = detail.slice("unsupported_ext:".length) || "unknown";
    return `Формат ${ext} не поддерживается.`;
  }
  if (detail.startsWith("invalid_mime:")) {
    const mime = detail.slice("invalid_mime:".length) || "unknown";
    return `Сервер отклонил MIME type: ${mime}.`;
  }
  return detail;
}

function deriveRefStatusAfterUploadError(data = {}, refs = []) {
  const safeRefs = Array.isArray(refs) ? refs.filter((item) => !!String(item?.url || "").trim()) : [];
  if (!safeRefs.length) return "empty";
  return deriveRefNodeStatus({
    ...(data || {}),
    refs: safeRefs,
    refStatus: "",
    uploading: false,
  });
}

async function uploadAsset(file, options = {}) {
  const debugTag = String(options?.debugTag || "").trim();
  const debugPayload = getUploadDebugPayload(file);
  if (debugTag) {
    console.log(`[${debugTag} upload request]`, debugPayload);
  }
  if (!isUploadableFile(file)) {
    const invalidResponse = {
      ok: false,
      detail: "invalid_file_payload",
      debugTag,
      ...debugPayload,
    };
    if (debugTag) {
      console.log(`[${debugTag} upload response]`, invalidResponse);
    }
    throw new Error(`upload_invalid_file:${debugTag || "unknown"}`);
  }

  const fd = new FormData();
  fd.append("file", file);

  const res = await fetch(`${API_BASE}/api/assets/upload`, {
    method: "POST",
    body: fd,
    credentials: "include",
  });
  const resClone = res.clone();

  let responseJson = null;
  let responseText = "";
  try {
    responseJson = await res.json();
  } catch {
    try {
      responseText = await resClone.text();
    } catch {
      // ignore
    }
  }

  if (debugTag) {
    console.log(`[${debugTag} upload response]`, responseJson || { ok: res.ok, status: res.status, text: responseText || res.statusText });
  }

  if (!res.ok) {
    const detail = responseJson?.detail || responseText || res.statusText;
    throw new Error(`upload_failed:${res.status}:${detail}`);
  }
  return responseJson || {};
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("file_read_failed"));
    reader.readAsDataURL(file);
  });
}

function truncateIntroText(value, maxLength = 84) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (text.length <= maxLength) return text;
  return `${text.slice(0, Math.max(0, maxLength - 1)).trim()}…`;
}

function buildIntroFrameAutoTitle({ textValue = "", scenes = [] } = {}) {
  const text = truncateIntroText(textValue, 72);
  if (text) {
    const words = text.split(" ").filter(Boolean).slice(0, 6);
    return words.join(" ");
  }
  const firstScene = Array.isArray(scenes) ? scenes.find((scene) => String(getSceneUiDescription(scene) || "").trim()) : null;
  const fallback = truncateIntroText(getSceneUiDescription(firstScene) || firstScene?.title || "", 56);
  return fallback || "Cinematic Intro";
}

const INTRO_REF_ROLE_ALIASES = {
  character_1: ["character_1", "character1", "ref_character_1", "ref_character"],
  character_2: ["character_2", "character2", "ref_character_2"],
  character_3: ["character_3", "character3", "ref_character_3"],
  animal: ["animal", "animals", "ref_animal"],
  group: ["group", "groups", "crowd", "ref_group"],
  props: ["props", "ref_props", "ref_items", "items", "objects", "item", "object"],
  location: ["location", "locations", "ref_location"],
  style: ["style", "styles", "ref_style"],
};

function getIntroRefsForRole(refsByRole = {}, role = "") {
  const aliases = INTRO_REF_ROLE_ALIASES?.[role] || [role];
  const collected = [];
  for (const key of aliases) {
    const items = Array.isArray(refsByRole?.[key]) ? refsByRole[key] : [];
    for (const item of items) {
      const url = String(typeof item === "string" ? item : item?.url || "").trim();
      if (url && !collected.includes(url)) collected.push(url);
    }
  }
  return collected;
}

function normalizeIntroConnectedRefsByRole(refsByRole = {}) {
  return Object.fromEntries(
    INTRO_COMFY_REF_ROLES.map((role) => {
      const urls = getIntroRefsForRole(refsByRole, role);
      return [role, urls];
    })
  );
}

function normalizeIntroCastRole(value = "") {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return "";
  const direct = normalized.replace(/[\s-]+/g, "_");
  const map = {
    character_1: "character_1",
    character1: "character_1",
    hero: "character_1",
    lead: "character_1",
    protagonist: "character_1",
    main_character: "character_1",
    character_2: "character_2",
    character2: "character_2",
    secondary: "character_2",
    deuteragonist: "character_2",
    character_3: "character_3",
    character3: "character_3",
    tertiary: "character_3",
    animal: "animal",
    pet: "animal",
    dog: "animal",
    group: "group",
    crowd: "group",
  };
  return map[direct] || "";
}

function normalizeIntroParticipantRoleList(...sources) {
  const out = [];
  for (const source of sources) {
    const items = Array.isArray(source) ? source : [];
    for (const item of items) {
      const rawRole = typeof item === "string"
        ? item
        : (item?.role || item?.id || item?.entityId || item?.entity_id || item?.slot || item?.name || "");
      const role = normalizeIntroCastRole(rawRole);
      if (role && !out.includes(role)) out.push(role);
    }
  }
  return out;
}

function extractIntroScenarioRefsByRole(scenarioPackage = null) {
  if (!scenarioPackage || typeof scenarioPackage !== "object") return normalizeIntroConnectedRefsByRole({});
  const candidates = [
    scenarioPackage?.refsByRole,
    scenarioPackage?.connectedRefsByRole,
    scenarioPackage?.cast?.refsByRole,
    scenarioPackage?.castRefsByRole,
    scenarioPackage?.history?.refsByRole,
  ];
  const mergedScenarioRefs = candidates.reduce((acc, value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return acc;
    for (const [role, refs] of Object.entries(value)) {
      if (!Array.isArray(refs) || refs.length === 0) continue;
      acc[role] = [...(Array.isArray(acc?.[role]) ? acc[role] : []), ...refs];
    }
    return acc;
  }, {});
  return normalizeIntroConnectedRefsByRole(mergedScenarioRefs);
}

function buildIntroScenarioRefsByRole({ directorRefsByRole = {}, graphRefsByRole = {}, plannerRefsByRole = {} } = {}) {
  const normalizedDirector = normalizeIntroConnectedRefsByRole(directorRefsByRole);
  const normalizedGraph = normalizeIntroConnectedRefsByRole(graphRefsByRole);
  const normalizedPlanner = normalizeIntroConnectedRefsByRole(plannerRefsByRole);
  return normalizeIntroConnectedRefsByRole(
    Object.fromEntries(
      INTRO_COMFY_REF_ROLES.map((role) => {
        const directorUrls = normalizedDirector?.[role] || [];
        const graphUrls = normalizedGraph?.[role] || [];
        const plannerUrls = normalizedPlanner?.[role] || [];
        return [role, [...directorUrls, ...graphUrls, ...plannerUrls]];
      })
    )
  );
}

function formatIntroRoleLabel(role) {
  const canonical = String(role || "").trim().toLowerCase();
  if (canonical === "character_1") return "Персонаж 1";
  if (canonical === "character_2") return "Персонаж 2";
  if (canonical === "character_3") return "Персонаж 3";
  if (canonical === "animal" || canonical === "animal_1") return "Животное";
  if (canonical === "group" || canonical === "group_faces") return "Группа / совместный кадр";
  if (canonical === "props") return "Предметы";
  if (canonical === "location") return "Локация";
  if (canonical === "style") return "Стиль";
  return canonical;
}

function buildIntroRoleAwareCastSummary(refsByRole = {}) {
  const castRoles = INTRO_CAST_ROLES.filter((role) => (refsByRole?.[role] || []).length > 0);
  if (!castRoles.length) return "";
  const heroRoles = ["character_1", "character_2", "character_3"].filter((role) => (refsByRole?.[role] || []).length > 0);
  const supportRoles = castRoles.filter((role) => !heroRoles.includes(role));
  const summary = [];
  if (heroRoles.length) summary.push(`Герои: ${heroRoles.map(formatIntroRoleLabel).join(", ")}`);
  if (supportRoles.length) summary.push(`Поддержка: ${supportRoles.map(formatIntroRoleLabel).join(", ")}`);
  return summary.join(" • ");
}

function buildIntroImportantProps(refsByRole = {}) {
  const propsCount = Array.isArray(refsByRole?.props) ? refsByRole.props.length : 0;
  if (!propsCount) return [];
  const propLabel = inferPropAnchorLabel(refsByRole);
  return [propLabel || `${propsCount} connected prop reference${propsCount > 1 ? "s" : ""}`];
}

function buildIntroWorldContext({ refsByRole = {}, sceneCount = 0, storyContext = "" } = {}) {
  const parts = [];
  if ((refsByRole?.location || []).length > 0) parts.push("location anchor connected");
  if ((refsByRole?.group || []).length > 0) parts.push("group dynamics available");
  if ((refsByRole?.animal || []).length > 0) parts.push("animal presence matters");
  if ((refsByRole?.props || []).length > 0) parts.push("props should help sell the world");
  if (sceneCount > 0) parts.push(`opening story distilled from ${sceneCount} storyboard scenes`);
  if (!parts.length && storyContext) parts.push(truncateIntroText(storyContext, 120));
  return parts.join(" • ");
}

function buildIntroStyleContext({ stylePreset = "", refsByRole = {}, heroParticipants = [], supportingParticipants = [], importantProps = [] } = {}) {
  const normalizedStylePreset = normalizeIntroStylePreset(stylePreset || "cinematic_dark");
  const hasStyleRefs = (refsByRole?.style || []).length > 0;
  const compositionPlan = resolveIntroCompositionPlan({ refsByRole, heroParticipants, supportingParticipants, importantProps });
  return `${normalizedStylePreset}${hasStyleRefs ? " + explicit style reference anchors" : " baseline"} • ${compositionPlan.label} • ${compositionPlan.weightSummary}`;
}

function normalizeIntroRoleProfileMap(...sources) {
  const out = {};
  for (const source of sources) {
    if (!source || typeof source !== "object") continue;
    for (const role of INTRO_COMFY_REF_ROLES) {
      const profile = source?.[role];
      if (profile && typeof profile === "object" && !out[role]) out[role] = profile;
    }
  }
  return out;
}

function normalizeIntroGenderPresentation(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (!normalized) return "";
  if (["female", "woman", "girl", "feminine"].some((token) => normalized.includes(token))) return "female";
  if (["male", "man", "boy", "masculine"].some((token) => normalized.includes(token))) return "male";
  return "";
}

function inferIntroGenderLockFromProfile(profile) {
  if (!profile || typeof profile !== "object") return "";
  return normalizeIntroGenderPresentation(
    profile?.visualProfile?.genderPresentation
    || profile?.genderPresentation
    || profile?.visualProfile?.gender
    || profile?.gender
  );
}

function inferIntroSpeciesLockFromProfile(profile) {
  if (!profile || typeof profile !== "object") return "";
  const speciesValue = profile?.visualProfile?.speciesLock
    || profile?.visualProfile?.species
    || profile?.speciesLock
    || profile?.species
    || profile?.animalType;
  return String(speciesValue || "").trim().toLowerCase();
}

const INTRO_REF_HANDLE_BY_ROLE = {
  character_1: "ref_character_1",
  character_2: "ref_character_2",
  character_3: "ref_character_3",
  animal: "ref_animal",
  group: "ref_group",
  location: "ref_location",
  style: "ref_style",
  props: "ref_props",
};

function getLatestIncomingNodeForHandle({ targetNodeId = "", targetHandle = "", nodesById = new Map(), edges = [] } = {}) {
  if (!targetNodeId || !targetHandle) return null;
  const edge = [...(Array.isArray(edges) ? edges : [])]
    .reverse()
    .find((item) => item?.target === targetNodeId && String(item?.targetHandle || "") === String(targetHandle || ""));
  return edge ? (nodesById.get(edge?.source) || null) : null;
}

function getLatestIncomingEdgeForHandle({ targetNodeId = "", targetHandle = "", edges = [] } = {}) {
  if (!targetNodeId || !targetHandle) return null;
  return [...(Array.isArray(edges) ? edges : [])]
    .reverse()
    .find((item) => item?.target === targetNodeId && String(item?.targetHandle || "") === String(targetHandle || ""))
    || null;
}

function extractNarrativeConnectedValue({ sourceNode = null, sourceHandle = "", targetHandle = "" } = {}) {
  if (!sourceNode) return null;

  if (targetHandle === "audio_in" && sourceNode.type === "audioNode" && sourceHandle === "audio") {
    const audioUrl = String(sourceNode?.data?.audioUrl || "").trim();
    if (!audioUrl) return null;
    const audioName = String(sourceNode?.data?.audioName || "").trim();
    const rawDuration = Number(sourceNode?.data?.audioDurationSec);
    const audioDurationSec = Number.isFinite(rawDuration) && rawDuration > 0 ? Number(rawDuration.toFixed(3)) : null;
    return {
      value: audioUrl,
      preview: audioName || audioUrl,
      sourceLabel: "Внешний аудио-источник",
      url: audioUrl,
      fileName: audioName || "",
      audioDurationSec,
      durationSec: audioDurationSec,
      mimeType: String(sourceNode?.data?.audioMime || "").trim(),
      meta: {
        kind: "audio_node",
        url: audioUrl,
        fileName: audioName || "",
        mimeType: String(sourceNode?.data?.audioMime || "").trim(),
        audioDurationSec,
        durationSec: audioDurationSec,
        origin: "audio_node",
      },
    };
  }

  if (targetHandle === "video_link_in" && sourceNode.type === "linkNode" && sourceHandle === "link") {
    const savedPayload = getLinkNodeSavedPayload(sourceNode?.data);
    const payload = savedPayload || buildLinkNodePayload(sourceNode?.data?.urlValue || sourceNode?.data?.draftUrl || "");
    console.log("[LINK->NARRATIVE payload]", { sourceNode, extracted: payload, edge: { sourceHandle, targetHandle } });
    if (!payload) return null;
    return {
      value: payload.value || payload.url || payload.href || payload.preview || "",
      preview: payload.preview || payload.domain || payload.value || payload.url || "",
      sourceLabel: payload.sourceLabel || "Ссылка",
      url: payload.url || payload.href || payload.value || "",
      assetUrl: payload.href || payload.url || payload.value || "",
      fileName: payload.domain || getAssetFileName(payload.value || payload.href || ""),
      type: payload.type,
      domain: payload.domain || "",
      meta: {
        href: payload.href || payload.url || payload.value || "",
        domain: payload.domain || "",
        kind: payload?.meta?.kind || "link",
      },
    };
  }

  if (targetHandle === "video_file_in" && sourceNode.type === "videoRefNode" && sourceHandle === "video_ref") {
    const payload = getVideoRefNodeSavedPayload(sourceNode?.data);
    if (!payload) return null;
    return {
      value: payload.value || payload.assetUrl || payload.url || payload.fileName || "",
      preview: payload.preview || payload.fileName || payload.assetUrl || "",
      sourceLabel: payload.sourceLabel || "Видео (референс)",
      url: payload.url || payload.assetUrl || "",
      assetUrl: payload.assetUrl || payload.url || "",
      fileName: payload.fileName || getAssetFileName(payload.assetUrl || payload.url || payload.value || ""),
      posterUrl: payload.posterUrl || "",
      type: payload.type || "video_ref",
      meta: {
        ...(payload.meta && typeof payload.meta === "object" ? payload.meta : {}),
        kind: "video_ref",
      },
    };
  }

  if (targetHandle === "video_file_in" && sourceNode.type === "comfyStoryboard" && sourceHandle === COMFY_STORYBOARD_MAIN_HANDLE) {
    const scenes = Array.isArray(sourceNode?.data?.mockScenes) ? sourceNode.data.mockScenes : [];
    const videoUrls = scenes
      .map((scene) => String(scene?.videoUrl || scene?.assetUrl || "").trim())
      .filter(Boolean)
      .slice(0, 5);
    const firstVideoUrl = videoUrls[0] || "";
    const readyVideoCount = videoUrls.length;
    const fallbackPreview = readyVideoCount
      ? `${readyVideoCount} видео из COMFY STORYBOARD`
      : `${scenes.length || 0} сцен в COMFY STORYBOARD`;
    console.log("[VIDEO_REF->NARRATIVE payload]", {
      sourceNode,
      extracted: {
        videoUrls,
        readyVideoCount,
        fallbackPreview,
      },
      edge: { sourceHandle, targetHandle },
    });
    if (!firstVideoUrl && !scenes.length) return null;
    return {
      value: firstVideoUrl || fallbackPreview,
      preview: firstVideoUrl || fallbackPreview,
      sourceLabel: "Внешний видео-референс",
      url: firstVideoUrl,
      assetUrl: firstVideoUrl,
      fileName: getAssetFileName(firstVideoUrl),
      meta: {
        sceneCount: scenes.length || 0,
        readyVideoCount,
      },
    };
  }

  if (targetHandle === "ref_character_1" && sourceNode.type === "refNode" && sourceHandle === "ref_character") {
    const normalized = normalizeRefData(sourceNode?.data || {}, "ref_character");
    if (!normalized.refs.length) return null;
    const roleType = normalizeCharacterRoleType(sourceNode?.data?.roleType);
    return {
      value: normalized.refs[0]?.url || "",
      preview: normalized.refs[0]?.name || `Character 1 • ${normalized.refs.length} refs`,
      sourceLabel: "Character 1",
      refs: normalized.refs.map((item) => ({ url: item.url, roleType })),
      count: normalized.refs.length,
      meta: { kind: "ref_character", count: normalized.refs.length, roleType },
    };
  }

  if (targetHandle === "ref_character_2" && sourceNode.type === "refCharacter2" && sourceHandle === "ref_character_2") {
    const refs = (Array.isArray(sourceNode?.data?.refs) ? sourceNode.data.refs : [])
      .map((item) => String(item?.url || "").trim())
      .filter(Boolean)
      .slice(0, 5);
    if (!refs.length) return null;
    const roleType = normalizeCharacterRoleType(sourceNode?.data?.roleType);
    return {
      value: refs[0] || "",
      preview: String(sourceNode?.data?.name || "").trim() || `Character 2 • ${refs.length} refs`,
      sourceLabel: "Character 2",
      refs: refs.map((url) => ({ url, roleType })),
      count: refs.length,
      meta: { kind: "ref_character_2", count: refs.length, roleType },
    };
  }

  if (targetHandle === "ref_character_3" && sourceNode.type === "refCharacter3" && sourceHandle === "ref_character_3") {
    const refs = (Array.isArray(sourceNode?.data?.refs) ? sourceNode.data.refs : [])
      .map((item) => String(item?.url || "").trim())
      .filter(Boolean)
      .slice(0, 5);
    if (!refs.length) return null;
    const roleType = normalizeCharacterRoleType(sourceNode?.data?.roleType);
    return {
      value: refs[0] || "",
      preview: String(sourceNode?.data?.name || "").trim() || `Character 3 • ${refs.length} refs`,
      sourceLabel: "Character 3",
      refs: refs.map((url) => ({ url, roleType })),
      count: refs.length,
      meta: { kind: "ref_character_3", count: refs.length, roleType },
    };
  }

  if (targetHandle === "ref_animal" && sourceNode.type === "refAnimal" && sourceHandle === "ref_animal") {
    const refs = (Array.isArray(sourceNode?.data?.refs) ? sourceNode.data.refs : [])
      .map((item) => String(item?.url || "").trim())
      .filter(Boolean)
      .slice(0, 5);
    if (!refs.length) return null;
    return {
      value: refs[0] || "",
      preview: String(sourceNode?.data?.name || "").trim() || `Animal • ${refs.length} refs`,
      sourceLabel: "Animal",
      refs,
      count: refs.length,
      meta: { kind: "ref_animal", count: refs.length },
    };
  }

  if (targetHandle === "ref_group" && sourceNode.type === "refGroup" && sourceHandle === "ref_group") {
    const refs = (Array.isArray(sourceNode?.data?.refs) ? sourceNode.data.refs : [])
      .map((item) => String(item?.url || "").trim())
      .filter(Boolean)
      .slice(0, 5);
    if (!refs.length) return null;
    return {
      value: refs[0] || "",
      preview: String(sourceNode?.data?.name || "").trim() || `Group • ${refs.length} refs`,
      sourceLabel: "Group",
      refs,
      count: refs.length,
      meta: { kind: "ref_group", count: refs.length },
    };
  }

  if (targetHandle === "ref_props" && sourceNode.type === "refNode" && sourceHandle === "ref_items") {
    const normalized = normalizeRefData(sourceNode?.data || {}, "ref_items");
    if (!normalized.refs.length) return null;
    return {
      value: normalized.refs[0]?.url || "",
      preview: normalized.refs[0]?.name || `Props • ${normalized.refs.length} refs`,
      sourceLabel: "Props",
      refs: normalized.refs.map((item) => item.url),
      count: normalized.refs.length,
      meta: { kind: "ref_props", count: normalized.refs.length },
    };
  }

  if (targetHandle === "ref_location" && sourceNode.type === "refNode" && sourceHandle === "ref_location") {
    const normalized = normalizeRefData(sourceNode?.data || {}, "ref_location");
    if (!normalized.refs.length) return null;
    return {
      value: normalized.refs[0]?.url || "",
      preview: normalized.refs[0]?.name || "Location connected",
      sourceLabel: "Location",
      refs: normalized.refs.map((item) => item.url),
      count: normalized.refs.length,
      meta: { kind: "ref_location", count: normalized.refs.length },
    };
  }

  if (targetHandle === "ref_style" && sourceNode.type === "refNode" && sourceHandle === "ref_style") {
    const normalized = normalizeRefData(sourceNode?.data || {}, "ref_style");
    if (!normalized.refs.length) return null;
    return {
      value: normalized.refs[0]?.url || "",
      preview: normalized.refs[0]?.name || "Style connected",
      sourceLabel: "Style",
      refs: normalized.refs.map((item) => item.url),
      count: normalized.refs.length,
      meta: { kind: "ref_style", count: normalized.refs.length },
    };
  }

  return null;
}

function getNarrativeConnectedInputsSnapshot({ node = null, nodesById = new Map(), edges = [] } = {}) {
  if (!node?.id) {
    return {
      audio_in: null,
      video_file_in: null,
      video_link_in: null,
      ref_character_1: null,
      ref_character_2: null,
      ref_character_3: null,
      ref_animal: null,
      ref_group: null,
      ref_props: null,
      ref_location: null,
      ref_style: null,
    };
  }

  return ["audio_in", "video_file_in", "video_link_in", "ref_character_1", "ref_character_2", "ref_character_3", "ref_animal", "ref_group", "ref_props", "ref_location", "ref_style"].reduce((acc, handleId) => {
    const edge = getLatestIncomingEdgeForHandle({ targetNodeId: node.id, targetHandle: handleId, edges });
    const sourceNode = edge ? (nodesById.get(edge.source) || null) : null;
    const extracted = extractNarrativeConnectedValue({
      sourceNode,
      sourceHandle: String(edge?.sourceHandle || ""),
      targetHandle: handleId,
    });
    acc[handleId] = extracted
      ? {
          handleId,
          sourceNodeId: edge?.source || "",
          sourceHandle: String(edge?.sourceHandle || ""),
          ...extracted,
        }
      : null;
    return acc;
  }, {});
}

function buildNarrativeConnectedContextFingerprint(connectedInputs = {}) {
  const entries = Object.entries(connectedInputs && typeof connectedInputs === "object" ? connectedInputs : {})
    .map(([handleId, value]) => {
      const refs = Array.isArray(value?.refs)
        ? value.refs.map((ref) => String(ref?.url || ref || "").trim()).filter(Boolean).sort()
        : [];
      const roleType = String(value?.meta?.roleType || "").trim().toLowerCase();
      return {
        handleId: String(handleId || ""),
        sourceNodeId: String(value?.sourceNodeId || ""),
        sourceHandle: String(value?.sourceHandle || ""),
        roleType,
        refs,
      };
    })
    .sort((a, b) => String(a.handleId).localeCompare(String(b.handleId)));
  return JSON.stringify(entries);
}

function extractIntroRefUrlsFromNode(node = null, role = "") {
  if (!node || typeof node !== "object" || !role) return [];
  if (node?.type === "refNode") {
    const kind = String(node?.data?.kind || "");
    const expectedKindByRole = {
      character_1: "ref_character",
      location: "ref_location",
      style: "ref_style",
      props: "ref_items",
    };
    if (expectedKindByRole?.[role] && kind !== expectedKindByRole[role]) return [];
    return normalizeRefData(node?.data || {}, kind).refs
      .map((item) => String(item?.url || "").trim())
      .filter(Boolean);
  }
  return (Array.isArray(node?.data?.refs) ? node.data.refs : [])
    .map((item) => (typeof item === "string" ? item : item?.url))
    .map((value) => String(value || "").trim())
    .filter(Boolean);
}

function collectIntroConnectedRefPackage({ comfyNode = null, comfyBrainNode = null, nodesById = new Map(), edges = [] } = {}) {
  const resolveAnchorNode = () => {
    if (comfyBrainNode?.id) return comfyBrainNode;
    if (comfyNode?.id) {
      const linkedBrainNode = getLatestIncomingNodeForHandle({
        targetNodeId: comfyNode.id,
        targetHandle: "comfy_plan",
        nodesById,
        edges,
      });
      if (linkedBrainNode?.type === "comfyBrain") return linkedBrainNode;
      return comfyNode;
    }
    return null;
  };

  const anchorNode = resolveAnchorNode();
  const connectedSourceNodeIdsByRole = {};
  const connectedRefsByRole = Object.fromEntries(
    INTRO_COMFY_REF_ROLES.map((role) => {
      const sourceNode = getLatestIncomingNodeForHandle({
        targetNodeId: String(anchorNode?.id || ""),
        targetHandle: INTRO_REF_HANDLE_BY_ROLE?.[role] || "",
        nodesById,
        edges,
      });
      connectedSourceNodeIdsByRole[role] = String(sourceNode?.id || "");
      return [role, [...new Set(extractIntroRefUrlsFromNode(sourceNode, role))]];
    })
  );
  const roleProfiles = Object.fromEntries(
    INTRO_COMFY_REF_ROLES
      .map((role) => {
        const sourceNode = getLatestIncomingNodeForHandle({
          targetNodeId: String(anchorNode?.id || ""),
          targetHandle: INTRO_REF_HANDLE_BY_ROLE?.[role] || "",
          nodesById,
          edges,
        });
        const profile = sourceNode?.data?.refHiddenProfile;
        return [role, profile && typeof profile === "object" ? profile : null];
      })
      .filter(([, profile]) => !!profile)
  );

  return {
    anchorNodeId: String(anchorNode?.id || ""),
    anchorNodeType: String(anchorNode?.type || ""),
    connectedRefsByRole: normalizeIntroConnectedRefsByRole(connectedRefsByRole),
    connectedSourceNodeIdsByRole,
    roleProfiles,
  };
}

function collectIntroFrameContext({ nodeId = "", nodes = [], edges = [] } = {}) {
  const nodesById = new Map((Array.isArray(nodes) ? nodes : []).map((node) => [node?.id, node]));
  const incoming = (Array.isArray(edges) ? edges : []).filter((edge) => edge?.target === nodeId);
  const storyEdges = incoming.filter((edge) => String(edge?.targetHandle || "") === INTRO_FRAME_STORY_HANDLE);
  const narrativeEdge = [...storyEdges]
    .reverse()
    .find((edge) => {
      const sourceNode = nodesById.get(edge?.source) || null;
      return sourceNode?.type === "comfyNarrative" && String(edge?.sourceHandle || "") === "preview_out";
    }) || null;
  const narrativeNode = narrativeEdge?.source ? (nodesById.get(narrativeEdge.source) || null) : null;
  const hasValidScenarioSource = !!(narrativeNode?.type === "comfyNarrative" && String(narrativeEdge?.sourceHandle || "") === "preview_out");
  const narrativeDirectorOutput = narrativeNode?.type === "comfyNarrative"
    ? (narrativeNode?.data?.outputs?.directorOutput || narrativeNode?.data?.pendingOutputs?.directorOutput || null)
    : null;
  const narrativeStoryboardOut = extractNarrativeStoryboardOut({
    sourceNode: narrativeNode,
    sourceHandle: String(narrativeEdge?.sourceHandle || ""),
  });
  const storySources = narrativeNode ? [narrativeNode] : [];
  const comfyNode = null;
  const comfyBrainNode = null;
  const textNode = null;
  const narrativeScenes = Array.isArray(narrativeStoryboardOut?.scenes)
    ? narrativeStoryboardOut.scenes.map((scene, idx) => normalizeStoryboardOutScene(scene, idx))
    : [];
  const scenes = narrativeScenes;
  const sceneCount = scenes.length;
  const scenarioPackage = narrativeDirectorOutput && typeof narrativeDirectorOutput === "object"
    ? narrativeDirectorOutput
    : null;
  const textValue = "";
  const sourceLabels = storySources.map((node) => {
    if (node?.type === "comfyNarrative") return "SCENARIO DIRECTOR";
    return String(node?.type || "NODE").toUpperCase();
  });
  const summaryParts = [];
  if (sceneCount) summaryParts.push(`${sceneCount} сцен`);
  if (textValue) summaryParts.push(`text: ${truncateIntroText(textValue, 42)}`);
  if (sourceLabels.length) summaryParts.push(`inputs: ${sourceLabels.join(", ")}`);
  const plannerInput = {};
  const directorRefsByRole = extractIntroScenarioRefsByRole(scenarioPackage);
  const narrativeInputsSnapshot = getNarrativeConnectedInputsSnapshot({
    node: narrativeNode,
    nodesById,
    edges,
  });
  const narrativeGraphRefsByRole = normalizeIntroConnectedRefsByRole({
    character_1: narrativeInputsSnapshot?.ref_character_1?.refs || [],
    character_2: narrativeInputsSnapshot?.ref_character_2?.refs || [],
    character_3: narrativeInputsSnapshot?.ref_character_3?.refs || [],
    animal: narrativeInputsSnapshot?.ref_animal?.refs || [],
    group: narrativeInputsSnapshot?.ref_group?.refs || [],
    props: narrativeInputsSnapshot?.ref_props?.refs || [],
    location: narrativeInputsSnapshot?.ref_location?.refs || [],
    style: narrativeInputsSnapshot?.ref_style?.refs || [],
  });
  const scenarioFormatCandidates = [
    scenarioPackage?.format,
    scenarioPackage?.aspectRatio,
    scenarioPackage?.aspect_ratio,
    scenarioPackage?.canvas,
    scenes.find((scene) => String(scene?.format || "").trim())?.format,
    scenes.find((scene) => String(scene?.imageFormat || "").trim())?.imageFormat,
    comfyBrainNode?.data?.format,
    comfyNode?.data?.format,
    plannerInput?.format,
    comfyNode?.data?.plannerMeta?.format,
    comfyBrainNode?.data?.plannerMeta?.format,
  ].filter((value) => String(value || "").trim());
  const scenarioFormat = scenarioFormatCandidates.length
    ? resolvePreferredSceneFormat(...scenarioFormatCandidates)
    : "";
  const storySummary = String(
    scenarioPackage?.storySummaryRu
    || scenarioPackage?.storySummaryEn
    || scenarioPackage?.story_summary_ru
    || scenarioPackage?.story_summary_en
    || scenarioPackage?.story_summary
    || ""
  ).trim();
  const previewPrompt = String(
    scenarioPackage?.previewPromptRu
    || scenarioPackage?.previewPromptEn
    || scenarioPackage?.preview_prompt_ru
    || scenarioPackage?.preview_prompt_en
    || scenarioPackage?.preview_prompt
    || ""
  ).trim();
  const world = String(
    scenarioPackage?.worldRu
    || scenarioPackage?.worldEn
    || scenarioPackage?.world_ru
    || scenarioPackage?.world_en
    || scenarioPackage?.world
    || ""
  ).trim();
  const roles = Array.isArray(scenarioPackage?.actors)
    ? scenarioPackage.actors.filter(Boolean).map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  const toneStyleDirection = String(
    scenarioPackage?.toneStyleDirection
    || scenarioPackage?.tone_style_direction
    || scenarioPackage?.history?.toneStyleDirection
    || ""
  ).trim();
  if (CLIP_TRACE_SCENARIO_GRAPH) {
    console.debug("[SCENARIO GRAPH STRICT] intro source", {
      sourceNodeType: String(narrativeNode?.type || ""),
      sourceHandle: String(narrativeEdge?.sourceHandle || ""),
      validScenarioSource: hasValidScenarioSource,
    });
  }
  const plannerConnectedRefsByRole = normalizeIntroConnectedRefsByRole(plannerInput?.refsByRole || {});
  const graphRefPackage = collectIntroConnectedRefPackage({
    comfyNode,
    comfyBrainNode,
    nodesById,
    edges,
  });
  const graphConnectedRefsByRole = normalizeIntroConnectedRefsByRole(graphRefPackage?.connectedRefsByRole || {});
  const graphConnectedSourceNodeIdsByRole = graphRefPackage?.connectedSourceNodeIdsByRole || {};
  const graphConnectedRoles = INTRO_COMFY_REF_ROLES.filter((role) => !!String(graphConnectedSourceNodeIdsByRole?.[role] || "").trim());
  const hasGraphConnectedRefs = graphConnectedRoles.some((role) => (graphConnectedRefsByRole?.[role] || []).length > 0);
  const mergedGraphRefsByRole = normalizeIntroConnectedRefsByRole(
    Object.fromEntries(
      INTRO_COMFY_REF_ROLES.map((role) => [
        role,
        [
          ...(graphConnectedRefsByRole?.[role] || []),
          ...(narrativeGraphRefsByRole?.[role] || []),
        ],
      ])
    )
  );
  const connectedRefsByRole = buildIntroScenarioRefsByRole({
    directorRefsByRole,
    graphRefsByRole: mergedGraphRefsByRole,
    plannerRefsByRole: hasGraphConnectedRefs ? {} : plannerConnectedRefsByRole,
  });
  const activeRefRoles = INTRO_COMFY_REF_ROLES.filter((role) => (connectedRefsByRole?.[role] || []).length > 0);
  const introActiveCastRoles = INTRO_CAST_ROLES.filter((role) => (connectedRefsByRole?.[role] || []).length > 0);
  const directorHeroParticipants = normalizeIntroParticipantRoleList(
    scenarioPackage?.heroParticipants,
    scenarioPackage?.primaryParticipants,
    scenarioPackage?.cast?.heroParticipants,
    scenarioPackage?.history?.heroParticipants,
  );
  const directorSupportingParticipants = normalizeIntroParticipantRoleList(
    scenarioPackage?.supportingParticipants,
    scenarioPackage?.cast?.supportingParticipants,
    scenarioPackage?.history?.supportingParticipants,
  );
  const refsHeroParticipants = ["character_1", "character_2", "character_3"].filter((role) => (connectedRefsByRole?.[role] || []).length > 0);
  const refsSupportingParticipants = ["animal", "group"].filter((role) => (connectedRefsByRole?.[role] || []).length > 0);
  const heroParticipants = normalizeIntroParticipantRoleList(directorHeroParticipants, refsHeroParticipants);
  const supportingParticipants = normalizeIntroParticipantRoleList(
    directorSupportingParticipants,
    refsSupportingParticipants,
    introActiveCastRoles.filter((role) => !heroParticipants.includes(role) && !["character_1", "character_2", "character_3"].includes(role)),
  );
  const roleAwareCastSummary = buildIntroRoleAwareCastSummary(connectedRefsByRole);
  const directRoleProfiles = Object.fromEntries(
    (Array.isArray(nodes) ? nodes : [])
      .map((node) => [resolveRefRoleForNode(node), node?.data?.refHiddenProfile])
      .filter(([role, profile]) => !!role && profile && typeof profile === "object")
  );
  const roleProfiles = normalizeIntroRoleProfileMap(
    plannerInput?.referenceProfiles,
    comfyNode?.data?.hiddenReferenceProfiles,
    comfyNode?.data?.plannerMeta?.referenceProfiles,
    comfyBrainNode?.data?.hiddenReferenceProfiles,
    comfyBrainNode?.data?.plannerMeta?.referenceProfiles,
    graphRefPackage?.roleProfiles,
    directRoleProfiles,
  );
  const connectedGenderLocksByRole = Object.fromEntries(
    introActiveCastRoles
      .map((role) => [role, inferIntroGenderLockFromProfile(roleProfiles?.[role])])
      .filter(([, value]) => !!value)
  );
  const connectedSpeciesLocksByRole = Object.fromEntries(
    introActiveCastRoles
      .map((role) => [role, inferIntroSpeciesLockFromProfile(roleProfiles?.[role])])
      .filter(([, value]) => !!value)
  );
  const directorIntroMustAppear = normalizeIntroParticipantRoleList(
    scenarioPackage?.introMustAppear,
    scenarioPackage?.cast?.introMustAppear,
    scenarioPackage?.history?.introMustAppear,
    scenarioPackage?.mustAppearRoles,
  );
  const introMustAppear = normalizeIntroParticipantRoleList(
    directorIntroMustAppear,
    heroParticipants,
    introActiveCastRoles,
  );
  const introMustNotAppear = [];
  const worldContext = buildIntroWorldContext({
    refsByRole: connectedRefsByRole,
    sceneCount,
    storyContext: summaryParts.join(" • "),
  });
  const importantProps = buildIntroImportantProps(connectedRefsByRole);
  const styleContext = buildIntroStyleContext({
    stylePreset: plannerInput?.stylePreset || comfyNode?.data?.stylePreset || comfyBrainNode?.data?.stylePreset || "",
    refsByRole: connectedRefsByRole,
    heroParticipants,
    supportingParticipants,
    importantProps,
  });
  if (CLIP_TRACE_INTRO_PREVIEW) {
    console.debug("[INTRO PREVIEW REFS]", {
      countsByRole: Object.fromEntries(
        INTRO_COMFY_REF_ROLES.map((role) => [role, Array.isArray(connectedRefsByRole?.[role]) ? connectedRefsByRole[role].length : 0])
      ),
      heroParticipants,
      supportingParticipants,
      importantProps,
      introMustAppear,
    });
  }
  if (roleAwareCastSummary) summaryParts.push(`cast: ${roleAwareCastSummary}`);
  if (CLIP_TRACE_SCENARIO_FORMAT) {
    console.debug("[INTRO PREVIEW SOURCE]", {
      sourceNodeType: String(narrativeNode?.type || ""),
      hasDirectorOutput: !!scenarioPackage,
      hasStoryboardOut: !!narrativeStoryboardOut,
      connectedRefCountsByRole: Object.fromEntries(INTRO_COMFY_REF_ROLES.map((role) => [role, (connectedRefsByRole?.[role] || []).length])),
      heroParticipants,
      supportingParticipants,
      introMustAppear,
      hasStorySummary: !!storySummary,
      hasPreviewPrompt: !!previewPrompt,
      hasWorld: !!world,
      hasRoles: roles.length > 0,
      hasToneStyleDirection: !!toneStyleDirection,
    });
  }
  return {
    sourceNodeIds: storySources.map((node) => String(node?.id || "")).filter(Boolean),
    sourceNodeTypes: storySources.map((node) => String(node?.type || "")).filter(Boolean),
    refAnchorNodeId: String(graphRefPackage?.anchorNodeId || ""),
    refAnchorNodeType: String(graphRefPackage?.anchorNodeType || ""),
    titleContextNodeId: String(textNode?.id || ""),
    titleText: String(textValue || "").trim(),
    scenes,
    sceneCount,
    summary: summaryParts.join(" • ") || "не подключён",
    autoTitle: buildIntroFrameAutoTitle({ textValue, scenes }),
    connectedRefsByRole,
    graphConnectedRefsByRole,
    plannerConnectedRefsByRole,
    graphConnectedSourceNodeIdsByRole,
    roleAwareCastSummary,
    heroParticipants,
    supportingParticipants,
    importantProps,
    worldContext,
    styleContext,
    storySummary,
    previewPrompt,
    world,
    roles,
    toneStyleDirection,
    scenarioFormat,
    activeRefRoles,
    introActiveCastRoles,
    introMustAppear,
    introMustNotAppear,
    connectedGenderLocksByRole,
    connectedSpeciesLocksByRole,
  };
}

function buildIntroFrameStoryContextText(context = {}) {
  const scenes = Array.isArray(context?.scenes) ? context.scenes : [];
  const storyBeats = scenes
    .slice(0, 6)
    .map((scene, idx) => {
      const beat = truncateIntroText(getSceneUiDescription(scene) || scene?.title || scene?.prompt || "", 120);
      if (!beat) return "";
      const t0 = Number(scene?.t0 ?? scene?.start);
      const t1 = Number(scene?.t1 ?? scene?.end);
      const timing = Number.isFinite(t0) && Number.isFinite(t1) ? ` (${t0}s→${t1}s)` : "";
      return `${idx + 1}. ${beat}${timing}`;
    })
    .filter(Boolean);

  return [
    String(context?.summary || "").trim(),
    context?.storySummary ? `Story summary: ${truncateIntroText(context.storySummary, 240)}` : "",
    context?.previewPrompt ? `Preview intent: ${truncateIntroText(context.previewPrompt, 240)}` : "",
    context?.world ? `World: ${truncateIntroText(context.world, 220)}` : "",
    Array.isArray(context?.roles) && context.roles.length ? `Scenario roles: ${context.roles.slice(0, 8).join(", ")}` : "",
    context?.toneStyleDirection ? `Tone/style: ${truncateIntroText(context.toneStyleDirection, 220)}` : "",
    Array.isArray(context?.introMustAppear) && context.introMustAppear.length ? `Cast contract must appear: ${context.introMustAppear.join(", ")}` : "",
    Array.isArray(context?.heroParticipants) && context.heroParticipants.length ? `Hero participants: ${context.heroParticipants.join(", ")}` : "",
    storyBeats.length ? `Opening beats: ${storyBeats.join(" | ")}` : "",
  ].filter(Boolean).join(" • ");
}

function buildIntroFramePreviewDataUrl({ stylePreset = "cinematic_dark", previewFormat = INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE } = {}) {
  const formatMeta = getIntroFramePreviewFormatMeta(previewFormat);
  const meta = getIntroStyleMeta(stylePreset);
  const width = formatMeta.width;
  const height = formatMeta.height;
  if (typeof document !== "undefined") {
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (ctx) {
      const gradient = ctx.createLinearGradient(0, 0, canvas.width, canvas.height);
      gradient.addColorStop(0, "#080d19");
      gradient.addColorStop(0.52, meta.secondary);
      gradient.addColorStop(1, "#020409");
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, canvas.width, canvas.height);

      ctx.fillStyle = `${meta.accent}55`;
      ctx.beginPath();
      ctx.arc(canvas.width * 0.18, canvas.height * 0.18, Math.min(canvas.width, canvas.height) * 0.22, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = `${meta.secondary}44`;
      ctx.beginPath();
      ctx.arc(canvas.width * 0.82, canvas.height * 0.2, Math.min(canvas.width, canvas.height) * 0.28, 0, Math.PI * 2);
      ctx.fill();

      ctx.fillStyle = meta.accent;
      ctx.font = `700 ${Math.max(18, Math.round(width * 0.036))}px Arial`;
      ctx.fillText(meta.label.toUpperCase(), formatMeta.paddingX, formatMeta.paddingTop);
      ctx.fillStyle = `${meta.accent}cc`;
      ctx.fillRect(formatMeta.paddingX, formatMeta.accentY, formatMeta.accentWidth, Math.max(4, Math.round(height * 0.011)));
      ctx.font = `500 ${Math.max(12, Math.round(width * 0.018))}px Arial`;
      ctx.fillStyle = "rgba(255,255,255,0.72)";
      ctx.fillText("AVA STUDIO", formatMeta.paddingX, height - Math.max(18, Math.round(height * 0.06)));
      return canvas.toDataURL("image/jpeg", 0.72);
    }
  }
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}">
      <defs>
        <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stop-color="#080d19"/>
          <stop offset="55%" stop-color="${meta.secondary}"/>
          <stop offset="100%" stop-color="#020409"/>
        </linearGradient>
        <linearGradient id="accent" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0%" stop-color="${meta.accent}"/>
          <stop offset="100%" stop-color="${meta.secondary}"/>
        </linearGradient>
      </defs>
      <rect width="${width}" height="${height}" fill="url(#bg)"/>
      <circle cx="${Math.round(width * 0.18)}" cy="${Math.round(height * 0.18)}" r="${Math.round(Math.min(width, height) * 0.22)}" fill="${meta.accent}" opacity="0.22"/>
      <circle cx="${Math.round(width * 0.82)}" cy="${Math.round(height * 0.2)}" r="${Math.round(Math.min(width, height) * 0.28)}" fill="${meta.secondary}" opacity="0.18"/>
      <rect x="${formatMeta.paddingX}" y="${formatMeta.accentY}" width="${formatMeta.accentWidth}" height="${Math.max(4, Math.round(height * 0.011))}" rx="2" fill="url(#accent)" opacity="0.92"/>
      <text x="${formatMeta.paddingX}" y="${formatMeta.paddingTop}" fill="${meta.accent}" font-size="${Math.max(18, Math.round(width * 0.036))}" font-family="Arial, Helvetica, sans-serif" font-weight="700" letter-spacing="4">${meta.label.toUpperCase()}</text>
      <text x="${formatMeta.paddingX}" y="${height - Math.max(18, Math.round(height * 0.06))}" fill="rgba(255,255,255,0.72)" font-size="${Math.max(12, Math.round(width * 0.018))}" font-family="Arial, Helvetica, sans-serif" font-weight="500" letter-spacing="1.5">AVA STUDIO</text>
    </svg>
  `.trim();
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`;
}

const REF_NODE_ROLE_BY_TYPE = {
  refNode: {
    ref_character: "character_1",
    ref_location: "location",
    ref_style: "style",
    ref_items: "props",
  },
  refCharacter2: "character_2",
  refCharacter3: "character_3",
  refAnimal: "animal",
  refGroup: "group",
};

const REF_ROLE_PENDING_LABELS = {
  character_1: "добавьте персонажа",
  character_2: "добавьте персонажа",
  character_3: "добавьте персонажа",
  animal: "добавьте животное",
  group: "добавьте группу",
  props: "добавьте предмет",
  location: "добавьте локацию",
  style: "добавьте стиль",
};

const REF_STATUS_LABELS = {
  empty: "пусто",
  draft: "черновик",
  loading: "анализ...",
  ready: "готово",
  error: "ошибка",
};
const CHARACTER_ROLE_TYPES = new Set(["auto", "hero", "antagonist", "support"]);
const CHARACTER_ROLE_TYPE_OPTIONS = [
  { value: "auto", label: "Авто" },
  { value: "hero", label: "Главный" },
  { value: "antagonist", label: "Антагонист" },
  { value: "support", label: "Поддержка" },
];

function normalizeCharacterRoleType(value) {
  const clean = String(value || "").trim().toLowerCase();
  return CHARACTER_ROLE_TYPES.has(clean) ? clean : "auto";
}

function resolveRefRoleForNode(node = {}) {
  if (!node || typeof node !== "object") return "";
  if (node.type === "refNode") {
    return REF_NODE_ROLE_BY_TYPE.refNode?.[String(node?.data?.kind || "")] || "";
  }
  return REF_NODE_ROLE_BY_TYPE?.[node.type] || "";
}

function deriveRefNodeStatus(data = {}) {
  const refsCount = Array.isArray(data?.refs) ? data.refs.filter((item) => !!String(item?.url || "").trim()).length : 0;
  const rawStatus = String(data?.refStatus || "").trim().toLowerCase();
  const refShortLabel = String(data?.refShortLabel || "").trim();
  const refAnalyzedAt = String(data?.refAnalyzedAt || "").trim();
  const refAnalysisError = String(data?.refAnalysisError || "").trim();
  const hasHiddenProfile = !!(data?.refHiddenProfile && typeof data.refHiddenProfile === "object");
  const hasAnalysisSignals = !!refShortLabel || hasHiddenProfile || !!refAnalyzedAt;
  if (!refsCount) return "empty";
  if (rawStatus === "loading") return "loading";
  if (refAnalysisError || rawStatus === "error") return "error";
  if (hasAnalysisSignals) return "ready";
  if (["empty", "draft", "loading", "ready", "error"].includes(rawStatus)) return rawStatus;
  return "draft";
}

function normalizeRefNodeData(data = {}, kindHint = "") {
  const normalized = normalizeRefData(data, kindHint);
  const refs = Array.isArray(normalized?.refs) ? normalized.refs : [];
  const refStatus = deriveRefNodeStatus({ ...normalized, refs });
  const refShortLabel = String(normalized?.refShortLabel || "").trim();
  const kind = String(normalized?.kind || kindHint || "");
  return {
    ...normalized,
    roleType: kind === "ref_character" ? normalizeCharacterRoleType(normalized?.roleType) : "",
    refStatus,
    refShortLabel,
    refDetailsOpen: !!normalized?.refDetailsOpen,
    refHiddenProfile: normalized?.refHiddenProfile && typeof normalized.refHiddenProfile === "object" ? normalized.refHiddenProfile : null,
    refAnalysisError: refStatus === "error" ? String(normalized?.refAnalysisError || "").trim() : "",
    refAnalyzedAt: String(normalized?.refAnalyzedAt || "").trim(),
    uploadSoftError: String(normalized?.uploadSoftError || "").trim(),
  };
}

function isComfyRefLikeNodeType(nodeType = "") {
  return ["refNode", "refCharacter2", "refCharacter3", "refAnimal", "refGroup"].includes(String(nodeType || ""));
}

function normalizeComfyRefNodeData(nodeType = "", data = {}, kindHint = "") {
  if (nodeType === "refNode") {
    return normalizeRefNodeData(data, kindHint);
  }
  const refs = Array.isArray(data?.refs)
    ? data.refs
      .map((item) => ({
        url: String(item?.url || "").trim(),
        name: String(item?.name || "").trim(),
        type: String(item?.type || "").trim(),
      }))
      .filter((item) => !!item.url)
      .slice(0, 5)
    : [];
  const normalized = {
    ...data,
    refs,
    roleType: (nodeType === "refCharacter2" || nodeType === "refCharacter3") ? normalizeCharacterRoleType(data?.roleType) : "",
    refStatus: deriveRefNodeStatus({ ...(data || {}), refs }),
    refShortLabel: String(data?.refShortLabel || "").trim(),
    refDetailsOpen: !!data?.refDetailsOpen,
    refHiddenProfile: data?.refHiddenProfile && typeof data.refHiddenProfile === "object" ? data.refHiddenProfile : null,
    refAnalysisError: String(data?.refAnalysisError || "").trim(),
    refAnalyzedAt: String(data?.refAnalyzedAt || "").trim(),
  };
  return normalized;
}

function normalizeRefData(data, kindHint = "") {
  const kind = String(data?.kind || kindHint || "");
  const maxFiles = kind === "ref_style" ? 1 : 5;
  const refsRaw = Array.isArray(data?.refs)
    ? data.refs
    : (data?.url ? [{ url: data.url, name: data?.name || "" }] : []);
  const refs = refsRaw
    .map((item) => ({
      url: String(item?.url || "").trim(),
      name: String(item?.name || "").trim(),
    }))
    .filter((item) => !!item.url)
    .slice(0, maxFiles);

  return {
    ...data,
    refs,
  };
}

function normalizeClipImageRefsPayload(refs = {}) {
  const toUrlList = (items) => (Array.isArray(items)
    ? items
      .map((item) => (typeof item === "string" ? item : item?.url))
      .map((url) => String(url || "").trim())
      .filter(Boolean)
    : []);

  const normalized = {
    character: toUrlList(refs?.character),
    location: toUrlList(refs?.location),
    style: toUrlList(refs?.style),
    props: toUrlList(refs?.props),
  };

  const propAnchorLabel = String(refs?.propAnchorLabel || "").trim();
  if (propAnchorLabel) normalized.propAnchorLabel = propAnchorLabel;

  const sessionCharacterAnchor = String(refs?.sessionCharacterAnchor || "").trim();
  if (sessionCharacterAnchor) normalized.sessionCharacterAnchor = sessionCharacterAnchor;

  const sessionLocationAnchor = String(refs?.sessionLocationAnchor || "").trim();
  if (sessionLocationAnchor) normalized.sessionLocationAnchor = sessionLocationAnchor;

  const sessionStyleAnchor = String(refs?.sessionStyleAnchor || "").trim();
  if (sessionStyleAnchor) normalized.sessionStyleAnchor = sessionStyleAnchor;

  if (refs?.sessionBaseline && typeof refs.sessionBaseline === "object") {
    normalized.sessionBaseline = refs.sessionBaseline;
  }

  if (refs?.previousContinuityMemory && typeof refs.previousContinuityMemory === "object") {
    normalized.previousContinuityMemory = refs.previousContinuityMemory;
  }

  const previousSceneImageUrl = String(refs?.previousSceneImageUrl || "").trim();
  if (previousSceneImageUrl) normalized.previousSceneImageUrl = previousSceneImageUrl;

  if (refs?.refsByRole && typeof refs.refsByRole === "object") {
    normalized.refsByRole = refs.refsByRole;
  }
  if (refs?.connectedInputs && typeof refs.connectedInputs === "object") {
    normalized.connectedInputs = refs.connectedInputs;
  }
  for (const key of ["text", "audioUrl", "mode", "stylePreset", "sceneId", "sceneGoal", "sceneNarrativeStep", "continuity"]) {
    const value = String(refs?.[key] || "").trim();
    if (value) normalized[key] = value;
  }
  if (refs?.plannerMeta && typeof refs.plannerMeta === "object") {
    normalized.plannerMeta = refs.plannerMeta;
  }

  if (Array.isArray(refs?.refsUsed)) {
    normalized.refsUsed = refs.refsUsed.map((role) => String(role || "").trim()).filter(Boolean);
  } else if (refs?.refsUsed && typeof refs.refsUsed === "object") {
    normalized.refsUsed = refs.refsUsed;
  }
  if (refs?.refDirectives && typeof refs.refDirectives === "object") {
    normalized.refDirectives = refs.refDirectives;
  }
  if (refs?.refsUsedByRole && typeof refs.refsUsedByRole === "object") {
    normalized.refsUsedByRole = refs.refsUsedByRole;
  }
  const primaryRole = String(refs?.primaryRole || "").trim();
  if (primaryRole) normalized.primaryRole = primaryRole;
  if (Array.isArray(refs?.secondaryRoles)) {
    normalized.secondaryRoles = refs.secondaryRoles.map((role) => String(role || "").trim()).filter(Boolean);
  }
  if (Array.isArray(refs?.sceneActiveRoles)) {
    normalized.sceneActiveRoles = refs.sceneActiveRoles.map((role) => String(role || "").trim()).filter(Boolean);
  }
  const heroEntityId = String(refs?.heroEntityId || "").trim();
  if (heroEntityId) normalized.heroEntityId = heroEntityId;
  if (Array.isArray(refs?.supportEntityIds)) {
    normalized.supportEntityIds = refs.supportEntityIds.map((role) => String(role || "").trim()).filter(Boolean);
  }
  if (Array.isArray(refs?.mustAppear)) {
    normalized.mustAppear = refs.mustAppear.map((role) => String(role || "").trim()).filter(Boolean);
  }
  if (Array.isArray(refs?.mustNotAppear)) {
    normalized.mustNotAppear = refs.mustNotAppear.map((role) => String(role || "").trim()).filter(Boolean);
  }
  if (Array.isArray(refs?.participants)) {
    normalized.participants = refs.participants.map((name) => String(name || "").trim()).filter(Boolean);
  }
  if (typeof refs?.environmentLock === "boolean") normalized.environmentLock = refs.environmentLock;
  if (typeof refs?.styleLock === "boolean") normalized.styleLock = refs.styleLock;
  if (typeof refs?.identityLock === "boolean") normalized.identityLock = refs.identityLock;

  return normalized;
}


function buildComfySceneRefsPayload({
  refsByRole = {},
  previousSceneImageUrl = "",
  previousContinuityMemory = null,
  propAnchorLabel = "",
  text = "",
  audioUrl = "",
  mode = "",
  stylePreset = "",
  sceneId = "",
  sceneGoal = "",
  sceneNarrativeStep = "",
  continuity = "",
  plannerMeta = null,
  refsUsed = [],
  refDirectives = null,
  refsUsedByRole = null,
  primaryRole = "",
  secondaryRoles = [],
  sceneActiveRoles = [],
  heroEntityId = "",
  supportEntityIds = [],
  mustAppear = [],
  mustNotAppear = [],
  participants = [],
  environmentLock = null,
  styleLock = null,
  identityLock = null,
  sceneRoleDynamics = "",
  imagePrompt = "",
  videoPrompt = "",
} = {}) {
  const normalizeRoleUrls = (items) => (Array.isArray(items)
    ? items
      .map((item) => (typeof item === "string" ? item : item?.url))
      .map((url) => String(url || "").trim())
      .filter(Boolean)
    : []);

  const pickUrls = (roles = []) => roles
    .flatMap((role) => normalizeRoleUrls(refsByRole?.[role]));

  const normalizedRefsByRole = Object.fromEntries(
    ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"]
      .map((role) => [role, pickUrls([role])])
  );
  const activeRolesNormalized = Array.isArray(sceneActiveRoles)
    ? sceneActiveRoles.map((role) => normalizeScenarioRoleName(role)).filter(Boolean)
    : [];
  const mustAppearNormalized = Array.isArray(mustAppear)
    ? mustAppear.map((role) => normalizeScenarioRoleName(role)).filter(Boolean)
    : [];
  const participantsNormalized = Array.isArray(participants)
    ? participants.map((role) => normalizeScenarioRoleName(role)).filter(Boolean)
    : [];
  const isEnvironmentOnlyScene = String(sceneRoleDynamics || "").trim().toLowerCase() === "environment"
    && participantsNormalized.length === 0
    && ![...activeRolesNormalized, ...mustAppearNormalized].some((role) => ["character_1", "character_2", "character_3", "group"].includes(role));
  const normalizedMustNotAppear = Array.isArray(mustNotAppear) ? [...mustNotAppear] : [];
  const groupNarrativelyRequired = (() => {
    if (Array.isArray(mustAppearNormalized) && mustAppearNormalized.includes("group")) return true;
    const directive = String((refDirectives && typeof refDirectives === "object" ? refDirectives.group : "") || "").trim().toLowerCase();
    if (["required", "hero"].includes(directive)) return true;
    const crowdSignal = ["protest", "riot", "mob", "audience", "chorus", "crowd chant", "mass panic", "митинг", "толпа", "бунт", "хор", "массов"]
      .some((marker) => `${String(sceneGoal || "")} ${String(sceneNarrativeStep || "")} ${String(text || "")} ${String(imagePrompt || "")} ${String(videoPrompt || "")}`.toLowerCase().includes(marker));
    return crowdSignal;
  })();
  const hasDuet = activeRolesNormalized.includes("character_1") && activeRolesNormalized.includes("character_2");
  if (hasDuet && !groupNarrativelyRequired) {
    normalizedRefsByRole.group = [];
  }
  if (!groupNarrativelyRequired && !normalizedMustNotAppear.includes("group")) {
    normalizedMustNotAppear.push("group");
  }
  if (isEnvironmentOnlyScene) {
    ["character_1", "character_2", "character_3", "group"].forEach((role) => {
      normalizedRefsByRole[role] = [];
      if (!normalizedMustNotAppear.includes(role)) normalizedMustNotAppear.push(role);
    });
  }

  const nodeSignals = {
    hasAudio: !!String(audioUrl || "").trim(),
    hasText: !!String(text || "").trim(),
    hasMode: !!String(mode || "").trim(),
    hasStylePreset: !!String(stylePreset || "").trim(),
    hasContinuity: !!String(continuity || "").trim(),
    hasPlannerMeta: !!(plannerMeta && typeof plannerMeta === 'object' && Object.keys(plannerMeta).length),
  };
  const normalizedPrimaryRole = String(primaryRole || "").trim();
  const normalizedHeroEntityId = String(heroEntityId || "").trim() || normalizedPrimaryRole;

  return normalizeClipImageRefsPayload({
    character: pickUrls(["character_1", "character_2", "character_3", "animal", "group"]),
    location: pickUrls(["location"]),
    style: pickUrls(["style"]),
    props: pickUrls(["props"]),
    previousSceneImageUrl,
    previousContinuityMemory,
    propAnchorLabel: String(propAnchorLabel || "").trim() || undefined,
    refsByRole: normalizedRefsByRole,
    connectedInputs: {
      refsByRole: Object.fromEntries(Object.entries(normalizedRefsByRole).map(([role, urls]) => [role, (urls || []).length > 0])),
      ...nodeSignals,
    },
    text: String(text || "").trim(),
    audioUrl: String(audioUrl || "").trim(),
    mode: String(mode || "").trim(),
    stylePreset: String(stylePreset || "").trim(),
    sceneId: String(sceneId || "").trim(),
    sceneGoal: String(sceneGoal || "").trim(),
    sceneNarrativeStep: String(sceneNarrativeStep || "").trim(),
    continuity: String(continuity || "").trim(),
    plannerMeta: plannerMeta && typeof plannerMeta === 'object' ? plannerMeta : undefined,
    refsUsed: Array.isArray(refsUsed) ? refsUsed : (refsUsed && typeof refsUsed === 'object' ? refsUsed : undefined),
    refDirectives: refDirectives && typeof refDirectives === 'object' ? refDirectives : undefined,
    refsUsedByRole: refsUsedByRole && typeof refsUsedByRole === 'object'
      ? Object.fromEntries(Object.entries(refsUsedByRole).filter(([role]) => groupNarrativelyRequired || role !== "group"))
      : undefined,
    primaryRole: normalizedPrimaryRole,
    secondaryRoles: Array.isArray(secondaryRoles) ? secondaryRoles : undefined,
    sceneActiveRoles: Array.isArray(sceneActiveRoles) ? sceneActiveRoles : undefined,
    heroEntityId: normalizedHeroEntityId,
    supportEntityIds: Array.isArray(supportEntityIds) ? supportEntityIds.filter((role) => groupNarrativelyRequired || normalizeScenarioRoleName(role) !== "group") : undefined,
    mustAppear: Array.isArray(mustAppear) ? mustAppear.filter((role) => groupNarrativelyRequired || normalizeScenarioRoleName(role) !== "group") : undefined,
    mustNotAppear: normalizedMustNotAppear.length ? normalizedMustNotAppear : undefined,
    participants: Array.isArray(participants) ? participants : undefined,
    environmentLock: typeof environmentLock === 'boolean' ? environmentLock : undefined,
    styleLock: typeof styleLock === 'boolean' ? styleLock : undefined,
    identityLock: typeof identityLock === 'boolean' ? identityLock : undefined,
  });
}

function summarizeRefsByRole(refsByRole) {
  const roles = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"];
  const summary = Object.fromEntries(
    roles.map((role) => [role, Array.isArray(refsByRole?.[role]) ? refsByRole[role].length : 0])
  );
  const activeRoles = roles.filter((role) => summary[role] > 0);
  return { ...summary, activeRoles };
}

function hasNonEmptyRefsByRole(refsByRole = {}) {
  return SCENARIO_IMAGE_ROLE_KEYS.some((role) => {
    const items = refsByRole?.[role];
    if (!Array.isArray(items)) return false;
    return items.some((item) => String(typeof item === "string" ? item : item?.url || "").trim());
  });
}

function getNonEmptyRefRoleKeys(refsByRole = {}) {
  return SCENARIO_IMAGE_ROLE_KEYS.filter((role) => {
    const items = refsByRole?.[role];
    if (!Array.isArray(items)) return false;
    return items.some((item) => String(typeof item === "string" ? item : item?.url || "").trim());
  });
}

function hasNonEmptyRoleList(value) {
  return Array.isArray(value) && value.some((item) => String(item || "").trim());
}

function hasNonEmptyObjectKeys(value) {
  return !!(value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length > 0);
}

function looksLikeTechnicalAssetRef(value) {
  const raw = String(value || "").trim().toLowerCase();
  if (!raw) return false;
  return (
    raw.startsWith("http://")
    || raw.startsWith("https://")
    || raw.startsWith("blob:")
    || raw.startsWith("file:")
    || raw.startsWith("data:")
    || raw.startsWith("/static/")
    || raw.startsWith("/api/")
    || raw.startsWith("/assets/")
  );
}

const COMFY_IMAGE_NO_TEXT_RULE = [
  "Generate a clean scene image with zero added text.",
  "No captions, no labels, no subtitles, no watermarks, no typography, no UI overlays.",
  "Do not render scene numbers, titles, debug text, story_action, or side annotations.",
  "Only include signage or readable text when the scene explicitly requires a real in-world sign.",
].join(" ");

const COMFY_IMAGE_DESIGNED_TEXT_MARKERS = [
  "{title}",
  "title text",
  "stylized title",
  "movie title",
  "poster title",
  "thumbnail title",
  "logo text",
  "wordmark",
  "typography",
  "font",
  "lettering",
  "caption text",
];

function looksLikeSceneMetaLabel(value) {
  const raw = String(value || "").trim();
  if (!raw) return false;
  const normalized = raw.toLowerCase();
  return (
    /^scene\s*\d+$/i.test(raw)
    || /^сцена\s*\d+$/i.test(raw)
    || /^step[_\s-]*\d+$/i.test(raw)
    || normalized === "story_action"
    || normalized.startsWith("scene title:")
    || normalized.startsWith("narrative step:")
    || normalized.startsWith("scene goal:")
    || normalized.startsWith("mode:")
    || normalized.startsWith("style preset:")
    || normalized.startsWith("timing:")
    || normalized.startsWith("primary role:")
    || normalized.startsWith("secondary roles:")
    || normalized.startsWith("refs used:")
    || normalized.startsWith("source image:")
  );
}

function sanitizeComfyVisualPromptText(value) {
  if (!String(value || "").trim()) return "";
  return String(value || "")
    .split(/\n+/)
    .map((line) => String(line || "").trim())
    .filter((line) => line && !looksLikeSceneMetaLabel(line))
    .join("\n")
    .trim();
}

function appendComfyImageNoTextRule(value) {
  const trimmed = String(value || "").trim();
  if (!trimmed) return COMFY_IMAGE_NO_TEXT_RULE;
  const normalized = trimmed.toLowerCase();
  if (COMFY_IMAGE_DESIGNED_TEXT_MARKERS.some((marker) => normalized.includes(marker))) {
    return trimmed;
  }
  if (
    normalized.includes("no captions")
    || normalized.includes("no text")
    || normalized.includes("no typography")
    || normalized.includes("no ui overlays")
  ) {
    return trimmed;
  }
  return `${trimmed}\n\n${COMFY_IMAGE_NO_TEXT_RULE}`.trim();
}

function selectComfyImagePrompt({
  syncedImagePrompt = "",
  sceneContract = null,
  liveDerived = null,
  plannerSceneSnapshot = null,
} = {}) {
  const fallbackPromptFromContract = [
    sanitizeComfyVisualPromptText(sceneContract?.sceneGoal),
    sanitizeComfyVisualPromptText(sceneContract?.continuity),
    sceneContract?.refDirectives ? `Reference directives: ${JSON.stringify(sceneContract.refDirectives)}` : "",
  ].filter(Boolean).join("\n");
  const semanticAudioCue = String(liveDerived?.meaningfulAudio || "").trim();
  const safeAudioCue = semanticAudioCue && !looksLikeTechnicalAssetRef(semanticAudioCue) ? semanticAudioCue : "";
  const fallbackPromptFromLive = [
    sanitizeComfyVisualPromptText(liveDerived?.meaningfulText),
    safeAudioCue ? `Audio cue: ${safeAudioCue}` : "",
  ].filter(Boolean).join("\n");
  const fallbackPromptFromPlannerSnapshot = sanitizeComfyVisualPromptText(
    plannerSceneSnapshot?.imagePromptEn
    || plannerSceneSnapshot?.imagePrompt
    || plannerSceneSnapshot?.framePrompt
    || plannerSceneSnapshot?.prompt
    || ""
  );
  const promptCandidates = [
    { source: "synced_scene_image_prompt", value: sanitizeComfyVisualPromptText(syncedImagePrompt) },
    { source: "scene_contract", value: fallbackPromptFromContract },
    { source: "live_brain_state", value: fallbackPromptFromLive },
    { source: "planner_snapshot_fallback", value: fallbackPromptFromPlannerSnapshot },
  ];
  const selectedPromptCandidate = promptCandidates.find((item) => String(item?.value || "").trim()) || { source: "none", value: "" };
  return {
    imagePrompt: appendComfyImageNoTextRule(selectedPromptCandidate?.value || ""),
    promptSource: String(selectedPromptCandidate?.source || "none"),
    promptCandidates,
    audioCueDroppedAsTechnicalRef: Boolean(semanticAudioCue && !safeAudioCue),
  };
}

function pickReadyLiveRefsByRoleForScene({ liveDerived = null, scene = null, includeDebug = false } = {}) {
  const roles = ["character_1", "character_2", "character_3", "animal", "group", "location", "style", "props"];
  const castRoles = new Set(["character_1", "character_2", "character_3", "animal", "group"]);
  const worldAnchorRoles = new Set(["location", "style"]);
  const activeCandidates = new Set([
    ...(Array.isArray(scene?.refsUsed) ? scene.refsUsed : []),
    ...(Array.isArray(scene?.mustAppear) ? scene.mustAppear : []),
    ...(Array.isArray(scene?.secondaryRoles) ? scene.secondaryRoles : []),
    ...(Array.isArray(scene?.supportEntityIds) ? scene.supportEntityIds : []),
    String(scene?.heroEntityId || "").trim(),
    String(scene?.primaryRole || "").trim(),
  ].map((role) => String(role || "").trim()).filter(Boolean));

  const rawRefsUsed = Array.isArray(scene?.refsUsed)
    ? scene.refsUsed
    : (scene?.refsUsed && typeof scene?.refsUsed === "object" ? Object.keys(scene.refsUsed) : []);
  const refsUsedSet = new Set(rawRefsUsed.map((role) => String(role || "").trim()).filter(Boolean));
  const refDirectives = scene?.refDirectives && typeof scene.refDirectives === "object" ? scene.refDirectives : {};
  const hasPropAnchorSignal = Boolean(
    scene?.propAnchorLabel
    || refsUsedSet.has("props")
    || (refDirectives?.props && typeof refDirectives.props === "object" && Object.keys(refDirectives.props).length)
    || String(scene?.sceneGoal || "").toLowerCase().includes("prop")
    || String(scene?.continuity || "").toLowerCase().includes("prop")
  );

  const refConnectionStates = liveDerived?.refConnectionStates && typeof liveDerived.refConnectionStates === "object"
    ? liveDerived.refConnectionStates
    : {};

  const decisionByRole = {};
  const refsByRole = Object.fromEntries(roles.map((role) => {
    const roleState = refConnectionStates?.[role] || {};
    const connected = !!roleState?.connected;
    const status = String(roleState?.status || "").trim().toLowerCase();
    const roleRefs = Array.isArray(roleState?.refs) ? roleState.refs : [];
    const urls = roleRefs
      .map((item) => String(item?.url || "").trim())
      .filter(Boolean);
    const uniqueUrls = [...new Set(urls)];
    const inSceneCast = activeCandidates.size === 0 || activeCandidates.has(role);
    const hasUrls = uniqueUrls.length > 0;

    let allowedByRolePolicy = false;
    let attachedByPolicy = "none";
    let reason = "filtered_out_not_in_scene_cast";
    if (castRoles.has(role)) {
      allowedByRolePolicy = inSceneCast;
      attachedByPolicy = "cast";
      reason = inSceneCast ? "ok" : "filtered_out_not_in_scene_cast";
    } else if (worldAnchorRoles.has(role)) {
      allowedByRolePolicy = true;
      attachedByPolicy = "world_anchor";
      reason = "ok_world_anchor";
    } else if (role === "props") {
      const propsInScene = inSceneCast || refsUsedSet.has("props");
      allowedByRolePolicy = propsInScene || hasPropAnchorSignal;
      attachedByPolicy = allowedByRolePolicy ? "prop_anchor" : "none";
      reason = allowedByRolePolicy ? "ok_props_anchor" : "filtered_out_not_in_scene_cast";
    }

    const canAttachVisual = connected && status === "ready" && hasUrls && allowedByRolePolicy;
    if (!canAttachVisual && hasUrls && worldAnchorRoles.has(role) && (!connected || status !== "ready")) {
      reason = "filtered_out_world_anchor_disabled";
    }
    if (!hasUrls) reason = "no_urls";

    decisionByRole[role] = {
      connected,
      status,
      urlCount: uniqueUrls.length,
      inSceneCast,
      allowedByRolePolicy,
      attachedByPolicy,
      canAttachVisual,
      reason,
    };
    return [role, canAttachVisual ? uniqueUrls : []];
  }));

  if (includeDebug) {
    return {
      refsByRole,
      debug: {
        sceneId: String(scene?.sceneId || "").trim() || null,
        activeCandidates: [...activeCandidates],
        sceneCastRoles: [...activeCandidates].filter((role) => castRoles.has(role)),
        worldAnchorRoles: ["location", "style"],
        allowedRolesForImage: Object.entries(decisionByRole)
          .filter(([, state]) => !!state?.allowedByRolePolicy)
          .map(([role]) => role),
        attachedByPolicy: Object.fromEntries(
          Object.entries(decisionByRole).map(([role, state]) => [role, state?.attachedByPolicy || "none"])
        ),
        filteredReasonsByRole: Object.fromEntries(
          Object.entries(decisionByRole).map(([role, state]) => [role, state?.reason || "unknown"])
        ),
        propsAnchorSignals: {
          refsUsed: refsUsedSet.has("props"),
          hasPropAnchorSignal,
          directives: !!(refDirectives?.props && typeof refDirectives.props === "object" && Object.keys(refDirectives.props).length),
        },
        decisionByRole,
      },
    };
  }

  return refsByRole;
}

function collectComfyRefDerivationSnapshot({ nodeId = "", nodesNow = [], edgesNow = [] } = {}) {
  const trackedHandles = [
    "ref_character_1",
    "ref_character_2",
    "ref_character_3",
    "ref_animal",
    "ref_group",
    "ref_location",
    "ref_style",
    "ref_props",
  ];
  const incoming = (edgesNow || []).filter((edge) => edge.target === nodeId);
  const incomingByHandle = Object.fromEntries(
    trackedHandles.map((handleId) => {
      const matched = [...incoming].reverse().find((edge) => String(edge.targetHandle || "") === handleId) || null;
      return [handleId, matched];
    })
  );

  const connectedSources = Object.fromEntries(
    Object.entries(incomingByHandle).map(([handleId, edge]) => {
      const sourceNode = edge ? (nodesNow || []).find((nodeItem) => nodeItem.id === edge.source) || null : null;
      const rawRefs = Array.isArray(sourceNode?.data?.refs) ? sourceNode.data.refs : [];
      return [
        handleId,
        {
          connected: !!edge,
          edgeId: edge?.id || null,
          sourceId: sourceNode?.id || null,
          sourceType: sourceNode?.type || null,
          sourceKind: sourceNode?.data?.kind || null,
          rawRefsCount: rawRefs.length,
          validRawUrls: rawRefs.filter((item) => !!String(item?.url || "").trim()).length,
        },
      ];
    })
  );

  return {
    nodeId,
    incomingTotal: incoming.length,
    incomingTracked: Object.values(incomingByHandle).filter(Boolean).length,
    incomingHandles: Object.fromEntries(
      Object.entries(incomingByHandle).map(([handleId, edge]) => [handleId, edge ? edge.source : null])
    ),
    connectedSources,
  };
}

function buildComfySceneContextPrompt({ scene = {}, mode = "clip", stylePreset = "realism", isVideo = false } = {}) {
  const timing = Number.isFinite(Number(scene?.startSec)) && Number.isFinite(Number(scene?.endSec))
    ? `${Number(scene.startSec).toFixed(1)}-${Number(scene.endSec).toFixed(1)}s`
    : Number.isFinite(Number(scene?.durationSec))
      ? `${Number(scene.durationSec).toFixed(1)}s`
      : "";
  const parts = [
    `Cinematic ${String(mode || "clip").trim()} scene in ${String(stylePreset || "realism").trim()} style.`,
    sanitizeComfyVisualPromptText(scene?.sceneGoal),
    sanitizeComfyVisualPromptText(scene?.continuity),
    timing ? `Keep the action readable within ${timing}.` : "",
    scene?.primaryRole ? `Primary subject focus: ${scene.primaryRole}.` : "",
    Array.isArray(scene?.secondaryRoles) && scene.secondaryRoles.length ? `Supporting subjects available: ${scene.secondaryRoles.join(", ")}.` : "",
    isVideo && scene?.imageUrl ? `Animate from the provided source image while preserving the same clean composition and world continuity.` : "",
    !isVideo ? COMFY_IMAGE_NO_TEXT_RULE : "",
  ].filter(Boolean);
  return parts.join("\n");
}

function stripFunctionsDeep(value) {
  if (typeof value === "function") return undefined;
  if (Array.isArray(value)) {
    return value
      .map((item) => stripFunctionsDeep(item))
      .filter((item) => item !== undefined);
  }
  if (value && typeof value === "object") {
    const next = {};
    for (const [key, nested] of Object.entries(value)) {
      const cleaned = stripFunctionsDeep(nested);
      if (cleaned !== undefined) next[key] = cleaned;
    }
    return next;
  }
  return value;
}

function sanitizeNarrativeTesterNodeData(nodeType, rawData = {}) {
  const data = rawData && typeof rawData === "object" ? { ...rawData } : {};
  const testerType = String(data.testerType || nodeType || "").trim() || String(nodeType || "").trim();
  return testerType ? { testerType } : {};
}

function serializeNodesForStorage(nodes) {
  const safeNodes = Array.isArray(nodes) ? nodes : [];
  console.info("[CLIP SERIALIZE] start", { nodesCount: safeNodes.length });
  const serializedNodes = safeNodes.reduce((acc, n, index) => {
    if (!n || typeof n !== "object") {
      console.warn("[CLIP SERIALIZE] skip invalid node", {
        reason: "node_not_object",
        index,
        node: n,
      });
      return acc;
    }

    const nodeId = String(n.id || "").trim();
    const nodeType = String(n.type || "").trim();
    if (!nodeId || !nodeType) {
      console.warn("[CLIP SERIALIZE] skip invalid node", {
        reason: "missing_id_or_type",
        index,
        nodeId,
        nodeType,
      });
      return acc;
    }

    const normalizedData = isComfyRefLikeNodeType(nodeType)
      ? normalizeComfyRefNodeData(nodeType, n.data || {}, n?.data?.kind || "")
      : (n.data && typeof n.data === "object" ? n.data : {});
    let data = stripFunctionsDeep(normalizedData);
    if (!data || typeof data !== "object" || Array.isArray(data)) {
      console.warn("[CLIP SERIALIZE] skip invalid node", {
        reason: "invalid_node_data",
        nodeId,
        nodeType,
        dataType: typeof data,
        isArray: Array.isArray(data),
      });
      data = {};
    }

    const serialNode = {
      id: nodeId,
      type: nodeType,
      position: n.position && typeof n.position === "object"
        ? {
          x: Number.isFinite(Number(n.position.x)) ? Number(n.position.x) : 0,
          y: Number.isFinite(Number(n.position.y)) ? Number(n.position.y) : 0,
        }
        : { x: 0, y: 0 },
      data,
    };

    if (isNarrativeTesterNodeType(nodeType)) {
      serialNode.data = sanitizeNarrativeTesterNodeData(nodeType, data);
      acc.push(serialNode);
      return acc;
    }
    if (nodeType === "brainNode") {
      delete serialNode.data.isParsing;
      delete serialNode.data.activeParseToken;
    }
    if (nodeType === "introFrame") {
      serialNode.data.previewKind = getEffectiveIntroFramePreviewKind(serialNode.data);
      if (serialNode.data.previewKind === INTRO_FRAME_PREVIEW_KINDS.GENERATED_LOCAL) {
        delete serialNode.data.imageUrl;
      }
      delete serialNode.data.contextSummary;
      delete serialNode.data.contextSceneCount;
      delete serialNode.data.sourceNodeIds;
      delete serialNode.data.sourceNodeTypes;
      delete serialNode.data.titleContextNodeId;
      delete serialNode.data.onField;
      delete serialNode.data.onGenerate;
      delete serialNode.data.onPickImage;
      delete serialNode.data.onClearImage;
    }

    acc.push(serialNode);
    return acc;
  }, []);
  console.info(`[CLIP SERIALIZE] normalized nodes count=${serializedNodes.length}`);
  return serializedNodes;
}

function notify(detail) {
  try {
    window.dispatchEvent(new CustomEvent("ps:notify", { detail }));
  } catch {
    // ignore
  }
}


function formatContinuityForDisplay(value) {
  if (value === null || value === undefined) return "";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  const text = String(value || "").trim();
  if (!text) return "";
  if ((text.startsWith("{") && text.endsWith("}")) || (text.startsWith("[") && text.endsWith("]"))) {
    try {
      return JSON.stringify(JSON.parse(text), null, 2);
    } catch {
      return text.replace(/,\s*/g, ",\n").replace(/;\s*/g, ";\n");
    }
  }
  return text.replace(/,\s*/g, ",\n").replace(/;\s*/g, ";\n");
}

function ComfyCollapsibleSection({ title, defaultOpen = false, children, className = "", contentClassName = "" }) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <section className={["clipSB_comfySection", "clipSB_comfyAccordion", isOpen ? "isOpen" : "", className].filter(Boolean).join(" ")}>
      <button
        type="button"
        className="clipSB_comfyAccordionHeader"
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
      >
        <span className="clipSB_comfyBlockTitle" style={{ marginBottom: 0 }}>{title}</span>
        <span className="clipSB_comfyAccordionIcon" aria-hidden>{isOpen ? "−" : "+"}</span>
      </button>
      <div className={["clipSB_comfyAccordionBody", isOpen ? "isOpen" : "", contentClassName].filter(Boolean).join(" ")}>
        <div className="clipSB_comfyAccordionInner">{children}</div>
      </div>
    </section>
  );
}

async function getAudioDurationSec(url) {
  // Browser-side duration probe (metadata only)
  return await new Promise((resolve) => {
    const safeUrl = String(url || "").trim();
    if (!safeUrl) {
      resolve(null);
      return;
    }
    try {
      const a = new Audio();
      a.preload = "metadata";
      a.onloadedmetadata = () => {
        const duration = a && Number.isFinite(a.duration) ? a.duration : null;
        resolve(duration);
      };
      a.onerror = () => resolve(null);
      a.src = safeUrl;
    } catch {
      resolve(null);
    }
  });
}


// -------------------------
// node UIs
// -------------------------

function NodeShell({ title, icon, children, className = "", onClose }) {
  return (
    <div className={`clipSB_node ${className}`}>
      <div className="clipSB_nodeHeader">
        <div className="clipSB_nodeIcon">{icon}</div>
        <div className="clipSB_nodeTitle">{title}</div>
        {onClose ? (
          <button className="clipSB_nodeClose" onClick={onClose} title="Удалить ноду">×</button>
        ) : null}
      </div>
      <div className="clipSB_nodeBody">{children}</div>
    </div>
  );
}

function AudioNode({ id, data }) {
  const inputRef = useRef(null);

  const onPick = () => inputRef.current?.click();
  const onFile = (e) => {
    const f = e.target.files?.[0];
    if (!f) return;
    data?.onUpload?.(id, f);
    // allow picking same file again
    e.target.value = "";
  };

  return (
    <>
      <Handle type="source" position={Position.Right} id="audio" className="clipSB_handle" style={handleStyle("audio")} />
      <NodeShell title="AUDIO" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎧</span>} className="clipSB_nodeAudio">
          {data?.uploading ? (
            <div className="clipSB_btn clipSB_btnMuted">Загрузка…</div>
          ) : data?.audioName ? (
            <div className="clipSB_fileRow">
              <div className="clipSB_fileName" title={data.audioName}>
                Файл: {data.audioName}
              </div>
              <button className="clipSB_x" onClick={() => data?.onClear?.(id)} title="Удалить">
                ×
              </button>
            </div>
          ) : (
            <button className="clipSB_btn" onClick={onPick}>
              Загрузить файл
            </button>
          )}
          <input
            ref={inputRef}
            type="file"
            accept="audio/*,.mp3,.wav,.ogg,.m4a"
            style={{ display: "none" }}
            onChange={onFile}
          />
        <div className="clipSB_hint" style={{ marginTop: 10 }}>
          mp3 / wav / ogg{data?.audioDurationSec ? ` • ${Math.round(data.audioDurationSec)}s` : ""}
        </div>
      </NodeShell>
    </>
  );
}

function TextNode({ id, data }) {
  return (
    <>
      <Handle type="source" position={Position.Right} id="text" className="clipSB_handle" style={handleStyle("text")} />
      <NodeShell title="TEXT" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>📄</span>} className="clipSB_nodeText">
        <div className="clipSB_textWrap">
          <textarea
            className="clipSB_textarea"
            placeholder="Текст для истории / слова песни / идея сюжета…"
            value={data?.textValue || ""}
            onChange={(e) => data?.onChange?.(id, e.target.value)}
          />
          <button className="clipSB_clear" title="Очистить" onClick={() => data?.onChange?.(id, "")}
          >
            ⟲
          </button>
        </div>
        <div className="clipSB_hint" style={{ marginTop: 10 }}>
          выход: текст → BRAIN
        </div>
      </NodeShell>
    </>
  );
}

function LinkNode({ id, data }) {
  const draftUrl = String(data?.draftUrl ?? data?.urlValue ?? "");
  const savedPayload = getLinkNodeSavedPayload(data) || buildLinkNodePayload(data?.urlValue || "");
  const liveParsed = parseLinkUrl(draftUrl);
  const hasSavedValue = !!savedPayload?.value;
  const isDraftDirty = normalizeLinkUrl(draftUrl) !== normalizeLinkUrl(data?.urlValue || "");
  const status = data?.urlStatus || (hasSavedValue ? "ready" : "empty");
  const isInvalid = status === "invalid";
  const statusTitle = isInvalid
    ? "Некорректная ссылка"
    : hasSavedValue
      ? "Ссылка готова"
      : "Вставьте ссылку";
  const statusBody = isInvalid
    ? (data?.urlError || "Используйте полный http/https URL.")
    : hasSavedValue
      ? "Ссылка добавлена и готова к передаче дальше."
      : "Добавьте web-источник и нажмите «Применить», чтобы активировать output.";

  return (
    <>
      <Handle type="source" position={Position.Right} id="link" className="clipSB_handle" style={handleStyle("link")} />
      <NodeShell title="ССЫЛКА" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🔗</span>} className="clipSB_nodeLink">
        <div className="clipSB_linkField">
          <input
            className={`clipSB_input clipSB_linkInput ${isInvalid ? "isInvalid" : ""}`.trim()}
            type="url"
            inputMode="url"
            placeholder="https://example.com/article"
            value={draftUrl}
            onChange={(e) => data?.onDraftChange?.(id, e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                data?.onApplyUrl?.(id);
              }
            }}
          />
          <button
            type="button"
            className="clipSB_btn clipSB_linkApply"
            onClick={() => data?.onApplyUrl?.(id)}
            disabled={!draftUrl.trim()}
          >
            Применить URL
          </button>
        </div>

        <div className={`clipSB_linkStatus ${hasSavedValue ? "isReady" : ""} ${isInvalid ? "isInvalid" : ""}`.trim()}>
          <div className="clipSB_linkStatusEyebrow">WEB SOURCE</div>
          <div className="clipSB_linkStatusTitle">{statusTitle}</div>
          <div className="clipSB_linkStatusBody">{statusBody}</div>

          {hasSavedValue ? (
            <div className="clipSB_linkMetaList">
              <div className="clipSB_linkMetaRow">
                <span>Домен</span>
                <strong title={savedPayload?.domain || savedPayload?.value}>{savedPayload?.domain || "—"}</strong>
              </div>
              <div className="clipSB_linkMetaRow">
                <span>Preview</span>
                <strong title={savedPayload?.value}>{savedPayload?.preview || savedPayload?.value}</strong>
              </div>
            </div>
          ) : null}

          {!hasSavedValue && !isInvalid && liveParsed?.isValid ? (
            <div className="clipSB_linkDraftPreview" title={liveParsed.preview || liveParsed.normalized}>
              Будет сохранено: {liveParsed.preview || liveParsed.normalized}
            </div>
          ) : null}
        </div>

        <div className="clipSB_small">
          Внешний URL-источник для narrative / brain. После применения нода отдаёт payload типа link.
          {isDraftDirty && !isInvalid ? " Есть несохранённые изменения." : ""}
        </div>
      </NodeShell>
    </>
  );
}

function BrainNode({ id, data }) {
  const mode = data?.mode || "clip";
  const scenarioKey = SCENARIO_OPTIONS.some((option) => option.value === data?.scenarioKey)
    ? data.scenarioKey
    : "clip";
  const shoot = data?.shootKey || "cinema";
  const style = data?.styleKey || "realism";
  const freezeStyle = !!data?.freezeStyle;

  // clip length mode: auto from AUDIO duration or manual value
  const clipMode = data?.clipMode || "auto"; // "auto" | "manual"
  const manualSec = Math.max(5, Math.min(3600, Number(data?.clipSec || 30)));
  const audioSecRaw = Number(data?.audioDurationSec || 0);
  const audioSec = Number.isFinite(audioSecRaw) && audioSecRaw > 0 ? audioSecRaw : 0;
  const clipSec = clipMode === "auto" ? (audioSec > 0 ? Math.round(audioSec) : 30) : manualSec;
  const isParsing = !!data?.isParsing;
  const wasParsingRef = useRef(false);
  const [progressPhraseIndex, setProgressPhraseIndex] = useState(0);
  const [parseElapsedSeconds, setParseElapsedSeconds] = useState(0);
  const [progressDots, setProgressDots] = useState(1);
  const [estimatedSceneCount, setEstimatedSceneCount] = useState(() => estimateSceneCount(clipSec));

  useEffect(() => {
    if (isParsing && !wasParsingRef.current) {
      setProgressPhraseIndex(0);
      setParseElapsedSeconds(0);
      setProgressDots(1);
      setEstimatedSceneCount(estimateSceneCount(clipSec));
      wasParsingRef.current = true;
      return;
    }
    if (!isParsing && wasParsingRef.current) {
      setProgressPhraseIndex(0);
      setParseElapsedSeconds(0);
      setProgressDots(0);
      wasParsingRef.current = false;
    }
  }, [clipSec, isParsing]);

  useEffect(() => {
    if (!isParsing) return undefined;
    const phraseIntervalId = window.setInterval(() => {
      setProgressPhraseIndex((prev) => (prev + 1) % PARSE_PROGRESS_PHRASES.length);
    }, 2500);
    return () => window.clearInterval(phraseIntervalId);
  }, [isParsing]);

  useEffect(() => {
    if (!isParsing) return undefined;
    const timerIntervalId = window.setInterval(() => {
      setParseElapsedSeconds((prev) => prev + 1);
    }, 1000);
    return () => window.clearInterval(timerIntervalId);
  }, [isParsing]);

  useEffect(() => {
    if (!isParsing) return undefined;
    const dotsIntervalId = window.setInterval(() => {
      setProgressDots((prev) => (prev % 3) + 1);
    }, 500);
    return () => window.clearInterval(dotsIntervalId);
  }, [isParsing]);

  const progressPhrase = PARSE_PROGRESS_PHRASES[progressPhraseIndex] || PARSE_PROGRESS_PHRASES[0];
  const progressSuffix = ".".repeat(progressDots);

  return (
    <>
      {/* typed inputs */}
      <div style={{ position: "absolute", top: 110, left: 18, fontSize: 11, opacity: 0.75, pointerEvents: "none" }}>AUDIO</div>
      <div style={{ position: "absolute", top: 150, left: 18, fontSize: 11, opacity: 0.75, pointerEvents: "none" }}>TEXT</div>
      <div style={{ position: "absolute", top: 190, left: 18, fontSize: 11, opacity: 0.75, pointerEvents: "none" }}>REF Персонаж</div>
      <div style={{ position: "absolute", top: 230, left: 18, fontSize: 11, opacity: 0.75, pointerEvents: "none" }}>REF Локация</div>
      <div style={{ position: "absolute", top: 270, left: 18, fontSize: 11, opacity: 0.75, pointerEvents: "none" }}>REF Стиль</div>
      <div style={{ position: "absolute", top: 310, left: 18, fontSize: 11, opacity: 0.75, pointerEvents: "none" }}>REF Предметы</div>

      {/* typed output */}
      <div style={{ position: "absolute", top: 350, right: 18, fontSize: 11, opacity: 0.75, pointerEvents: "none" }}>PLAN</div>

      {/* explicit ports (позже удобно валидировать связи) */}
      <Handle type="target" position={Position.Left} id="audio" className="clipSB_handle" style={handleStyle("audio", { top: 116 })} />
      <Handle type="target" position={Position.Left} id="text" className="clipSB_handle" style={handleStyle("text", { top: 156 })} />
      <Handle type="target" position={Position.Left} id="ref_character" className="clipSB_handle" style={handleStyle("ref_character", { top: 196 })} />
      <Handle type="target" position={Position.Left} id="ref_location" className="clipSB_handle" style={handleStyle("ref_location", { top: 236 })} />
      <Handle type="target" position={Position.Left} id="ref_style" className="clipSB_handle" style={handleStyle("ref_style", { top: 276 })} />
      <Handle type="target" position={Position.Left} id="ref_items" className="clipSB_handle" style={handleStyle("ref_items", { top: 316 })} />
      <Handle type="source" position={Position.Right} id="plan" className="clipSB_handle" style={handleStyle("plan", { top: 216 })} />

      <NodeShell
        title="BRAIN"
        onClose={() => data?.onRemoveNode?.(id)}
        icon={<span aria-hidden>🧠</span>}
        className="clipSB_nodeBrain"
      >
        <div className="clipSB_small" style={{ marginBottom: 10 }}>
          Настройки планирования сцен (пока без генерации).
        </div>

        <div style={{ display: "grid", gap: 10 }}>
          {mode !== "scenario" ? (
            <div>
              <div className="clipSB_hint" style={{ marginBottom: 6 }}>
                Сценарий
              </div>
              <select
                className="clipSB_select clipSB_selectScenario"
                value={scenarioKey}
                onChange={(e) => data?.onScenario?.(id, e.target.value)}
              >
                {SCENARIO_OPTIONS.map((option) => (
                  <option key={option.value} value={option.value}>{option.label}</option>
                ))}
              </select>
            </div>
          ) : null}

          <div>
            <div className="clipSB_hint" style={{ marginBottom: 6 }}>
              Съёмка
            </div>
            <select
              className="clipSB_select"
              value={shoot}
              onChange={(e) => data?.onShoot?.(id, e.target.value)}
            >
              <option value="cinema">кино</option>
              <option value="clip">клиповая</option>
              <option value="doc">док/реализм</option>
              <option value="pov">POV</option>
              <option value="static">статичная камера</option>
            </select>
          </div>

          <div>
            <div className="clipSB_hint" style={{ marginBottom: 6 }}>
              Стиль
            </div>
            <select
              className="clipSB_select"
              value={style}
              onChange={(e) => data?.onStyle?.(id, e.target.value)}
            >
              <option value="realism">реализм</option>
              <option value="neon">неон/кибер</option>
              <option value="film">плёнка/грейн</option>
              <option value="glossy">глянец/реклама</option>
              <option value="soft">мягкий свет</option>
            </select>
          </div>

          <label
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "10px 12px",
              borderRadius: 12,
              background: "rgba(255,255,255,0.05)",
              boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.08)",
              color: "rgba(255,255,255,0.86)",
              userSelect: "none",
            }}
          >
            <input
              type="checkbox"
              checked={freezeStyle}
              onChange={(e) => data?.onFreezeStyle?.(id, e.target.checked)}
              style={{ width: 16, height: 16 }}
            />
            <span>Freeze Style — фиксировать стиль на все сцены</span>
          </label>
        </div>

        <div style={{ marginTop: 10 }}>
          <label
            className="clipSB_check"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "10px 12px",
              borderRadius: 12,
              background: "rgba(255,255,255,0.04)",
              boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.08)",
              color: "rgba(255,255,255,0.86)",
              userSelect: "none",
            }}
          >
            <input
              type="checkbox"
              checked={!!data?.wantLipSync}
              onChange={(e) => data?.onWantLipSync?.(id, e.target.checked)}
              style={{ width: 16, height: 16 }}
            />
            <span>LipSync — добавить performance-вставки с фразами вокала</span>
          </label>
        </div>

        <div className="clipSB_small" style={{ marginTop: 10 }}>
          Сейчас работаем только в режиме «клип»: с LipSync — больше фраз/performance, без LipSync — монтаж по биту и энергии.
        </div>

        <div style={{ marginTop: 10 }}>
          <div className="clipSB_hint" style={{ marginBottom: 6 }}>
            Длина клипа
          </div>

          <select
            className="clipSB_select"
            value={clipMode}
            onChange={(e) => data?.onClipMode?.(id, e.target.value)}
          >
            <option value="auto">авто — по AUDIO</option>
            <option value="manual">вручную</option>
          </select>

          {clipMode === "manual" ? (
            <input
              className="clipSB_input"
              type="number"
              min={5}
              max={3600}
              step={1}
              value={clipSec}
              onChange={(e) => data?.onClipSec?.(id, e.target.value)}
              style={{ marginTop: 8 }}
            />
          ) : (
            <div className="clipSB_small" style={{ marginTop: 8, opacity: 0.9 }}>
              Берём длительность из подключённой AUDIO-ноды (если есть). Если AUDIO не подключена — используем 30 сек.
            </div>
          )}
        </div>

        {isParsing ? (
          <button className="clipSB_btn clipSB_btnMuted" onClick={() => data?.onStopParse?.(id)} style={{ marginTop: 10 }}>
            Остановить
          </button>
        ) : (
          <button className="clipSB_btn" onClick={() => data?.onParse?.(id)} style={{ marginTop: 10 }}>
            Разобрать (бесплатно)
          </button>
        )}

        {isParsing ? (
          <div
            style={{
              marginTop: 8,
              padding: "10px 12px",
              borderRadius: 12,
              background: "rgba(255,255,255,0.04)",
              boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.08)",
            }}
          >
            <div style={{ color: "rgba(255,255,255,0.92)", fontSize: 13, lineHeight: 1.35 }}>
              {progressPhrase}{progressSuffix}
            </div>
            <div className="clipSB_small" style={{ marginTop: 6, opacity: 0.85 }}>
              Время: {formatParseTimer(parseElapsedSeconds)}
            </div>
            <div className="clipSB_small" style={{ marginTop: 2, opacity: 0.85 }}>
              Примерно сцен: {estimatedSceneCount}
            </div>
          </div>
        ) : data?.lastParseError ? (
          <div className="clipSB_small" style={{ marginTop: 8, color: "#ff7875" }}>Ошибка: {String(data.lastParseError)}</div>
        ) : null}
        {(!isParsing && !data?.lastParseError && data?.scenePlan?.engine) ? (
          <div className="clipSB_small" style={{ marginTop: 8, opacity: 0.95 }}>
            <div>
              engine: <b>{String(data.scenePlan.engine)}</b>
              {data.scenePlan.modelUsed ? <> · model: <span style={{ opacity: 0.9 }}>{String(data.scenePlan.modelUsed)}</span></> : null}
              {typeof data.scenePlan.sceneCount === "number" ? <> · scenes: <b>{String(data.scenePlan.sceneCount)}</b></> : null}
            </div>
            {Array.isArray(data.scenePlan.warnings) && data.scenePlan.warnings.length ? (
              <div style={{ marginTop: 4, color: "#ffb86c" }}>warnings: {data.scenePlan.warnings.join(", ")}</div>
            ) : null}
            {data.scenePlan.rejectedReason ? (
              <div style={{ marginTop: 4, color: "#ff7875" }}>rejectedReason: {String(data.scenePlan.rejectedReason)}</div>
            ) : null}
            <div style={{ marginTop: 4 }}>
              repairRetryUsed: <b>{data.scenePlan.repairRetryUsed ? "true" : "false"}</b>
              {data.scenePlan.audioHint ? <> · audio.hint: <span style={{ opacity: 0.9 }}>{String(data.scenePlan.audioHint)}</span></> : null}
            </div>
            {data.scenePlan.hint ? (
              <div style={{ marginTop: 4, color: "#ffb86c" }}>hint: {String(data.scenePlan.hint).slice(0, 180)}</div>
            ) : null}
          </div>
        ) : null}

        <div className="clipSB_hint" style={{ marginTop: 10 }}>
          входы: AUDIO/TEXT/REF • выход: PLAN → Storyboard
        </div>
      </NodeShell>
    </>
  );
}

function RefNode({ id, data }) {
  const inputRef = useRef(null);
  const title = data?.title || "REFERENCE";
  const icon = data?.icon || "📷";
  const kind = data?.kind || "ref";
  if (kind === "ref_character") {
    return (
      <RefLiteNode
        id={id}
        data={data}
        title={title}
        className="clipSB_nodeRef clipSB_nodeRef--ref_character"
        handleId="ref_character"
        showRoleSelector
      />
    );
  }
  const maxFiles = kind === "ref_style" ? 1 : 5;
  const refsRaw = Array.isArray(data?.refs) ? data.refs : (data?.url ? [{ url: data.url, name: data?.name || "" }] : []);
  const refs = refsRaw
    .map((item) => ({ url: String(item?.url || "").trim(), name: String(item?.name || "").trim() }))
    .filter((item) => !!item.url);
  const canAddMore = refs.length < maxFiles;
  const refStatus = deriveRefNodeStatus(data);
  const isError = refStatus === "error";
  const isReady = refStatus === "ready";
  const shortLabel = String(data?.refShortLabel || "").trim();
  const analysisError = String(data?.refAnalysisError || "").trim();
  const uploadSoftError = String(data?.uploadSoftError || "").trim();
  const detailsOpen = !!data?.refDetailsOpen;
  const detailsLines = formatRefProfileDetails(data?.refHiddenProfile);
  const canToggleDetails = refStatus === "ready" && detailsLines.length > 0;
  const showRoleSelector = kind === "ref_character";
  const roleType = normalizeCharacterRoleType(data?.roleType);

  const openPicker = () => {
    if (!canAddMore) return;
    inputRef.current?.click();
  };

  const onInputChange = async (e) => {
    const files = Array.from(e.target.files || []);
    if (!files.length) return;
    await data?.onPickImage?.(id, files);
    e.target.value = "";
  };

  return (
    <>
      <Handle type="source" position={Position.Right} id={kind} className="clipSB_handle" style={handleStyle(kind)} />
      <NodeShell
        title={title}
        onClose={() => data?.onRemoveNode?.(id)}
        icon={<span aria-hidden>{icon}</span>}
        className={`clipSB_nodeRef clipSB_nodeRef--${kind} ${refStatus === "draft" ? "clipSB_nodeRefDraft" : ""} ${isError ? "clipSB_nodeRefError" : ""}`.trim()}
      >
        <div className="clipSB_refGrid" style={{ marginBottom: 10 }}>
          {uploadSoftError ? <div className="clipSB_refWarningBadge">⚠ {uploadSoftError}</div> : null}
          {refs.map((item, idx) => (
            <div className="clipSB_refThumb" key={`${item.url}-${idx}`}>
              <img src={resolveAssetUrl(item.url)} alt={`${title} ${idx + 1}`} className="clipSB_refThumbImg" />
              <button
                className="clipSB_refThumbRemove"
                title="Удалить изображение"
                onClick={() => data?.onRemoveImage?.(id, idx)}
              >
                ×
              </button>
            </div>
          ))}
          {canAddMore ? (
            <button className="clipSB_refAddTile" onClick={openPicker} title="Добавить изображение">
              +
            </button>
          ) : null}
        </div>

        <div className="clipSB_fileRow" style={{ marginBottom: 10 }}>
          <div className="clipSB_fileName" title={refs.map((x) => x.name || x.url).join(", ")}>
            {refs.length ? `${refs.length}/${maxFiles} изображ.` : "нет изображения"}
          </div>
        </div>

        <div className="clipSB_small" style={{ marginBottom: 8 }}>статус: {REF_STATUS_LABELS[refStatus] || refStatus}</div>
        {isError ? <div className="clipSB_refErrorBadge">⚠ {analysisError || "Не удалось проанализировать реф"}</div> : null}
        {isReady && shortLabel ? <div className="clipSB_refReadyBadge">label: {shortLabel}</div> : null}
        {canToggleDetails ? (
          <button className="clipSB_refToggleDetails" onClick={() => data?.onToggleDetails?.(id)}>
            {detailsOpen ? "Скрыть описание" : "Показать описание"}
          </button>
        ) : null}
        {canToggleDetails && detailsOpen ? (
          <div className="clipSB_refDetailsBox">
            {detailsLines.map((line, idx) => <div key={`${id}-details-${idx}`} className="clipSB_refDetailsLine">{line}</div>)}
          </div>
        ) : null}
        {showRoleSelector ? (
          <div style={{ marginBottom: 10 }}>
            <div className="clipSB_small" style={{ marginBottom: 4 }}>Тип роли:</div>
            <select
              className="clipSB_select"
              value={roleType}
              onChange={(event) => data?.onField?.(id, "roleType", normalizeCharacterRoleType(event?.target?.value))}
              disabled={refStatus === "loading"}
            >
              {CHARACTER_ROLE_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </div>
        ) : null}

        <div style={{ display: "flex", gap: 8 }}>
          <button className="clipSB_btn" onClick={openPicker} disabled={!canAddMore || !!data?.uploading || refStatus === "loading"}>
            {data?.uploading ? "Загрузка…" : "Загрузить фото"}
          </button>
        </div>
        <input
          ref={inputRef}
          type="file"
          accept="image/*"
          multiple={kind !== "ref_style"}
          style={{ display: "none" }}
          onChange={onInputChange}
        />

        {!canAddMore ? (
          <div className="clipSB_small" style={{ marginTop: 8 }}>
            Достигнут лимит ({maxFiles})
          </div>
        ) : null}

        <div className="clipSB_hint" style={{ marginTop: 10 }}>
          {kind === "ref_style"
            ? "Глобальный стиль (1 фото)"
            : "Загрузи до 5 фото"}
        </div>

        <div className="clipSB_hint" style={{ marginTop: 8 }}>
          подключай к BRAIN (персонаж/локация/стиль/предметы)
        </div>
      </NodeShell>
    </>
  );
}

function StoryboardPlanNode({ id, data }) {
  const scenes = data?.scenes || [];
  const isStale = !!data?.isStale;
  const storyboardOut = data?.storyboardOut && typeof data.storyboardOut === "object" ? data.storyboardOut : null;
  const voiceScript = String(storyboardOut?.voice_script || "").trim();
  const musicPrompt = String(storyboardOut?.globalMusicPrompt || storyboardOut?.music_prompt || "").trim();
  const storySummary = String(storyboardOut?.story_summary || "").trim();
  const directorSummary = String(storyboardOut?.director_summary || "").trim();
  const sceneGeneration = data?.sceneGeneration && typeof data.sceneGeneration === "object" ? data.sceneGeneration : {};
  const enrichedScenes = scenes.map((scene) => {
    const runtime = sceneGeneration[String(scene?.sceneId || scene?.id || "")] || {};
    return {
      ...scene,
      executorModel: String(runtime.model || scene.executorModel || scene.ltxMode || "i2v"),
      imagePrompt: String(runtime.imagePrompt || scene.imagePrompt || ""),
      videoPrompt: String(runtime.videoPrompt || scene.videoPrompt || ""),
      sceneGenerationStatus: normalizeStoryboardGenerationStatus(runtime.status || scene.sceneGenerationStatus),
      sceneGenerationError: String(runtime.error || ""),
      generatedAssetUrl: String(runtime.generatedAssetUrl || scene.generatedAssetUrl || ""),
      generatedAudioUrl: String(runtime.generatedAudioUrl || scene.generatedAudioUrl || ""),
      montageReady: runtime.montageReady === true || scene.montageReady === true,
    };
  });

  const generationStatusLabels = {
    not_generated: "not_generated",
    generating: "generating",
    done: "done",
    error: "error",
  };

  return (
    <>
      <Handle type="target" position={Position.Left} id="plan_in" className="clipSB_handle" style={handleStyle("plan")} />
      <Handle type="source" position={Position.Right} id="plan_out" className="clipSB_handle" style={handleStyle("storyboard_to_assembly")} />
      <NodeShell
        title="STORYBOARD"
        onClose={() => data?.onRemoveNode?.(id)}
        icon={<span aria-hidden>🎞️</span>}
        className="clipSB_nodeStoryboard"
      >
        <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
          <button className="clipSB_btn clipSB_btnSecondary" onClick={() => data?.onOpenScenario?.(id)} disabled={enrichedScenes.length === 0}>
            Сценарий
          </button>
          {isStale ? (
            <span className="clipSB_small" style={{ color: "#ffb86c", alignSelf: "center" }}>⚠ результат устарел</span>
          ) : null}
        </div>

        {enrichedScenes.length === 0 ? (
          <div className="clipSB_small">Пусто. Подключи storyboard_out из Scenario Director.</div>
        ) : (
          <>
            <div className="clipSB_storyboardOverview">
              {storySummary ? (
                <div className="clipSB_storyboardSummaryCard">
                  <div className="clipSB_storyboardBlockTitle">Story summary</div>
                  <div className="clipSB_small">{storySummary}</div>
                </div>
              ) : null}
              {directorSummary ? (
                <div className="clipSB_storyboardSummaryCard">
                  <div className="clipSB_storyboardBlockTitle">Director summary</div>
                  <div className="clipSB_small">{directorSummary}</div>
                </div>
              ) : null}
              {voiceScript ? (
                <div className="clipSB_storyboardSummaryCard">
                  <div className="clipSB_storyboardBlockTitle">Voice script</div>
                  <div className="clipSB_hint">{voiceScript}</div>
                </div>
              ) : null}
            </div>

            <div className="clipSB_storyboardMusicCard">
              <div className="clipSB_storyboardMusicHeader">
                <div>
                  <div className="clipSB_storyboardBlockTitle">Music</div>
                  <div className="clipSB_small">Global music prompt для фоновой музыки и дальнейшего монтажа.</div>
                </div>
                <button className="clipSB_btn clipSB_btnSecondary" type="button" disabled>
                  Сгенерировать музыку
                </button>
              </div>
              <div className="clipSB_storyboardPromptBox">{musicPrompt || "Music prompt появится после получения storyboard_out."}</div>
            </div>

            <div className="clipSB_storyboardSceneList">
              {enrichedScenes.map((s, idx) => (
                <article key={getScenarioSceneStableKey(s, idx)} className="clipSB_storyboardSceneCard">
                  <div className="clipSB_storyboardSceneHeader">
                    <div>
                      <div className="clipSB_storyboardSceneId">{`Scene ${s.displayIndex || (idx + 1)}`}</div>
                      <div className="clipSB_storyboardSceneTime">
                        {s.t0}s → {s.t1}s • {Number(s.durationSec || Math.max(0, (s.t1 || 0) - (s.t0 || 0))).toFixed(1)}s
                      </div>
                    </div>
                    <span className={`clipSB_storyboardSceneStatus clipSB_storyboardSceneStatus--${s.sceneGenerationStatus}`.trim()}>
                      {generationStatusLabels[s.sceneGenerationStatus] || "not_generated"}
                    </span>
                  </div>

                  <div className="clipSB_storyboardSceneGrid">
                    <div className="clipSB_storyboardKv"><span>actors</span><strong>{Array.isArray(s.actors) && s.actors.length ? s.actors.join(", ") : "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>location</span><strong>{s.location || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>props</span><strong>{Array.isArray(s.props) && s.props.length ? s.props.join(", ") : "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>emotion</span><strong>{s.emotion || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>scene_goal</span><strong>{s.sceneText || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>frame_description</span><strong>{s.visualDescription || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>action_in_frame</span><strong>{s.actionInFrame || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>camera</span><strong>{s.cameraIdea || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>ltx_mode</span><strong>{s.ltxMode || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>ltx_reason</span><strong>{s.ltxReason || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>start_frame_source</span><strong>{s.startFrameSource || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>needs_two_frames</span><strong>{s.needsTwoFrames ? "true" : "false"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>continuation_from_previous</span><strong>{s.continuationFromPrevious ? "true" : "false"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>narration_mode</span><strong>{s.narrationMode || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>local_phrase</span><strong>{s.localPhrase || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>sfx</span><strong>{s.sfx || "—"}</strong></div>
                    <div className="clipSB_storyboardKv"><span>music_mix_hint</span><strong>{s.musicMixHint || "medium"}</strong></div>
                  </div>

                  <div className="clipSB_storyboardExecutor">
                    <div className="clipSB_storyboardExecutorRow">
                      <label className="clipSB_narrativeField">
                        <div className="clipSB_brainLabel">Model / ltx_mode</div>
                        <select
                          className="clipSB_select clipSB_storyboardSelect"
                          value={s.executorModel}
                          onChange={(event) => data?.onStoryboardSceneGenerationUpdate?.(id, s.sceneId, { model: event.target.value })}
                        >
                          {["i2v", "f_l", "continuation"].map((option) => (
                            <option key={option} value={option}>{option}</option>
                          ))}
                        </select>
                      </label>

                      <div className="clipSB_storyboardExecutorActions">
                        <button
                          className="clipSB_btn"
                          type="button"
                          onClick={() => data?.onStoryboardSceneGenerate?.(id, s.sceneId)}
                          disabled={s.sceneGenerationStatus === "generating"}
                        >
                          {s.sceneGenerationStatus === "generating" ? "Генерация…" : "Сгенерировать сцену"}
                        </button>
                        <button
                          className="clipSB_btn clipSB_btnSecondary"
                          type="button"
                          onClick={() => data?.onStoryboardSceneGenerationUpdate?.(id, s.sceneId, { montageReady: !s.montageReady })}
                        >
                          {s.montageReady ? "Готово к монтажу" : "Подготовить к монтажу"}
                        </button>
                      </div>
                    </div>

                    <div className="clipSB_storyboardPromptGrid">
                      <div className="clipSB_storyboardPromptCard">
                        <div className="clipSB_storyboardBlockTitle">Image prompt preview</div>
                        <div className="clipSB_storyboardPromptBox">{s.imagePrompt || "—"}</div>
                      </div>
                      <div className="clipSB_storyboardPromptCard">
                        <div className="clipSB_storyboardBlockTitle">Video prompt preview</div>
                        <div className="clipSB_storyboardPromptBox">{s.videoPrompt || "—"}</div>
                      </div>
                    </div>

                    {s.sceneGenerationError ? (
                      <div className="clipSB_storyboardError">Ошибка генерации: {s.sceneGenerationError}</div>
                    ) : null}
                  </div>
                </article>
              ))}
            </div>
          </>
        )}
      </NodeShell>
    </>
  );
}

function IntroFrameNode({ id, data }) {
  const fileInputRef = useRef(null);
  const [isCompactLayout, setIsCompactLayout] = useState(() => {
    if (typeof window === "undefined") return false;
    return window.innerWidth < 1280;
  });

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const media = window.matchMedia("(max-width: 1279px)");
    const syncLayout = (event) => setIsCompactLayout(event.matches);

    setIsCompactLayout(media.matches);
    if (typeof media.addEventListener === "function") {
      media.addEventListener("change", syncLayout);
      return () => media.removeEventListener("change", syncLayout);
    }

    media.addListener(syncLayout);
    return () => media.removeListener(syncLayout);
  }, []);

  const previewKind = normalizeIntroFramePreviewKind(data?.previewKind);
  const previewUrl = useMemo(
    () => resolveAssetUrl(resolveIntroFramePreviewUrl(data)),
    [data]
  );
  const hasBackendGeneratedAsset = previewKind === INTRO_FRAME_PREVIEW_KINDS.BACKEND_GENERATED && !!previewUrl;
  const autoTitle = !!data?.autoTitle;
  const styleMeta = getIntroStyleMeta(data?.stylePreset || "cinematic_dark");
  const previewFormat = normalizeIntroFramePreviewFormat(data?.scenarioFormat || data?.previewFormat);
  const previewFormatMeta = getIntroFramePreviewFormatMeta(previewFormat);
  const durationSec = normalizeIntroDurationSec(data?.durationSec);
  const [durationDraft, setDurationDraft] = useState(
    () => String(durationSec).replace(".", ",")
  );
  const [zoomedPreview, setZoomedPreview] = useState(null);

  useEffect(() => {
    setDurationDraft(String(durationSec).replace(".", ","));
  }, [durationSec]);

  useEffect(() => {
    const handler = (event) => {
      if (event.key === "Escape") setZoomedPreview(null);
    };

    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const commitDurationDraft = useCallback(() => {
    const parsed = parseLocaleFloat(durationDraft);

    if (!Number.isFinite(parsed)) {
      setDurationDraft(String(durationSec).replace(".", ","));
      return;
    }

    const normalized = normalizeIntroDurationSec(parsed);

    setDurationDraft(String(normalized).replace(".", ","));
    data?.onField?.(id, "durationSec", normalized);
  }, [data, durationDraft, durationSec, id]);
  const status = String(data?.status || "idle");
  const errorMessage = String(data?.error || "").trim();
  const rawTitle = String(data?.userTitleRaw ?? data?.title ?? "");
  const derivedTitle = String(data?.derivedTitle || "").trim();
  const generatedHookTitle = String(data?.generatedHookTitle || "").trim();
  const previewTitle = rawTitle.trim() || generatedHookTitle || derivedTitle || "Intro frame";
  const previewContext = String(data?.contextSummary || "Story preview").trim() || "Story preview";
  const titleLength = rawTitle.length;
  const hasRecommendedOverflow = titleLength > INTRO_TITLE_RECOMMENDED_CHARS;
  const statusLabel = status === "ready"
    ? "preview готов"
    : status === "generating" || status === "preview_generating"
      ? "собираем preview..."
      : "черновик";
  const selectedStylePreset = normalizeIntroStylePreset(data?.stylePreset || "cinematic_dark");
  const selectedStyleMeta = getIntroStyleMeta(selectedStylePreset);
  const selectedNegativeRules = Array.isArray(selectedStyleMeta?.negativeRules) && selectedStyleMeta.negativeRules.length
    ? selectedStyleMeta.negativeRules
    : (Array.isArray(selectedStyleMeta?.forbidden) ? selectedStyleMeta.forbidden : []);
  const isGenerating = status === "generating" || status === "preview_generating";
  const debug = data?.debug && typeof data.debug === "object" ? data.debug : {};
  const compositionMode = String(debug?.compositionMode || "").trim();
  const compositionFocusTargets = Array.isArray(debug?.compositionFocusTargets) ? debug.compositionFocusTargets : [];
  const compositionWeights = debug?.compositionWeights && typeof debug.compositionWeights === "object" ? debug.compositionWeights : {};
  const activeCastRoles = Array.isArray(debug?.introActiveCastRoles) ? debug.introActiveCastRoles : [];
  const mustAppearRoles = Array.isArray(debug?.introMustAppear) ? debug.introMustAppear : [];
  const mustNotAppearRoles = Array.isArray(debug?.introMustNotAppear) ? debug.introMustNotAppear : [];
  const attachedReferenceParts = debug?.attachedReferenceParts && typeof debug.attachedReferenceParts === "object"
    ? debug.attachedReferenceParts
    : {};
  const attachedInlineRoles = Array.isArray(debug?.attachedInlineReferenceRoles) ? debug.attachedInlineReferenceRoles : [];
  const overlayDebug = debug?.overlay && typeof debug.overlay === "object" ? debug.overlay : {};
  const detailRows = [
    ["Style key", selectedStylePreset],
    rawTitle.trim() ? ["User title", rawTitle] : null,
    derivedTitle ? ["Auto title", derivedTitle] : null,
    generatedHookTitle ? ["Hook title for generation", generatedHookTitle] : null,
    ["Title guide", `Рекомендуется до ${INTRO_TITLE_RECOMMENDED_CHARS} символов • служебный hook до ${INTRO_HOOK_TITLE_MAX_CHARS} символов`],
    previewContext && previewContext !== "Story preview" ? ["Story context", previewContext] : null,
    compositionMode ? ["Composition", compositionMode] : null,
    compositionFocusTargets.length ? ["Focus targets", compositionFocusTargets.join(", ")] : null,
    (compositionWeights?.subject || compositionWeights?.support)
      ? ["Weights", [compositionWeights?.subject, compositionWeights?.support].filter(Boolean).join(" • ")]
      : null,
    activeCastRoles.length ? ["Active cast", activeCastRoles.join(", ")] : null,
    mustAppearRoles.length ? ["Must appear", mustAppearRoles.join(", ")] : null,
    mustNotAppearRoles.length ? ["Must not appear", mustNotAppearRoles.join(", ")] : null,
    attachedInlineRoles.length ? ["Inline refs", attachedInlineRoles.join(", ")] : null,
    Object.keys(attachedReferenceParts).length ? ["Attached parts", Object.entries(attachedReferenceParts).map(([role, count]) => `${role}:${count}`).join(" • ")] : null,
    overlayDebug?.titleRendered ? ["Overlay", `font ${overlayDebug?.fontSize || 0}px • ${Array.isArray(overlayDebug?.lines) ? overlayDebug.lines.length : 0} lines`] : null,
  ].filter(Boolean);

  return (
    <>
      <Handle type="target" position={Position.Left} id={INTRO_FRAME_STORY_HANDLE} className="clipSB_handle" style={handleStyle("intro_context", { top: 76 })} />
      <Handle type="source" position={Position.Right} id="intro_frame_out" className="clipSB_handle" style={handleStyle("intro_frame", { top: 108 })} />
      <NodeShell
        title="INTRO FRAME"
        onClose={() => data?.onRemoveNode?.(id)}
        icon={<span aria-hidden>🖼️</span>}
        className="clipSB_nodeAssembly clipSB_nodeIntroFrame"
      >
        <div style={{ width: isCompactLayout ? "min(100vw - 96px, 100%)" : 840, maxWidth: "100%" }}>
          <div className="clipSB_introFrameBody">
            <div className="clipSB_introFrameGrid">
              <div className="clipSB_introFrameControls">
                <div className="clipSB_assemblyStats" style={{ marginBottom: 0 }}>
                  <div className="clipSB_assemblyRow"><span>Style</span><strong>{styleMeta.label}</strong></div>
                  <div className="clipSB_assemblyRow"><span>Длительность</span><strong>{durationSec.toFixed(1)} сек</strong></div>
                  <div className="clipSB_assemblyRow"><span>Статус</span><strong>{statusLabel}</strong></div>
                  <div className="clipSB_assemblyRow"><span>Preview</span><strong>{previewFormatMeta.label}</strong></div>
                </div>

                  <label className="clipSB_introTitleField">
                    <div className="clipSB_introTitleLabelRow">
                      <div className="clipSB_hint">Title</div>
                      <div className={`clipSB_introTitleCounter clipSB_small${hasRecommendedOverflow ? " clipSB_introTitleCounterWarning" : ""}`}>
                        {titleLength} / {INTRO_TITLE_RECOMMENDED_CHARS}
                      </div>
                    </div>
                    <input
                      className="clipSB_input"
                      value={rawTitle}
                      onChange={(e) => data?.onField?.(id, "title", e.target.value)}
                      placeholder="Введите заголовок или используйте auto title"
                    />
                    <div className={`clipSB_introTitleHint clipSB_small${hasRecommendedOverflow ? " clipSB_introTitleHintWarning" : ""}`}>
                      Рекомендуется до {INTRO_TITLE_RECOMMENDED_CHARS} символов. Лучше 2–5 слов.
                    </div>
                    <div className="clipSB_introTitleHint clipSB_small">
                      Этот ввод — основной источник заголовка для Intro Frame preview/generation.
                    </div>
                    <div className="clipSB_introTitleHint clipSB_small">
                      Служебный hook для генерации сокращается предсказуемо до {INTRO_HOOK_TITLE_MAX_CHARS} символов и не перезаписывает ваш текст.
                    </div>
                    {derivedTitle ? (
                      <div className="clipSB_introDerivedTitle clipSB_small">
                        <strong>{rawTitle.trim() ? "Auto title:" : "Заголовок для генерации:"}</strong> {derivedTitle}
                      </div>
                    ) : null}
                    {generatedHookTitle && generatedHookTitle !== derivedTitle && generatedHookTitle !== rawTitle.trim() ? (
                      <div className="clipSB_introDerivedTitle clipSB_small">
                        <strong>Hook title:</strong> {generatedHookTitle}
                      </div>
                    ) : null}
                  </label>

                  <label style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <input
                      type="checkbox"
                      checked={autoTitle}
                      onChange={(e) => data?.onField?.(id, "autoTitle", e.target.checked)}
                    />
                    <span className="clipSB_small">Auto title по сюжетному контексту</span>
                  </label>

                  <label>
                    <div className="clipSB_hint" style={{ marginBottom: 6 }}>Style preset</div>
                    <select
                      className="clipSB_select"
                      value={selectedStylePreset}
                      onChange={(e) => data?.onField?.(id, "stylePreset", e.target.value)}
                      style={{ minHeight: 42 }}
                    >
                      {INTRO_STYLE_PRESETS.map((item) => {
                        const meta = getIntroStyleMeta(item);
                        return <option key={item} value={item}>{meta.label} — {meta.uiHint}</option>;
                      })}
                    </select>
                  </label>

                  <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) 92px", gap: 10, alignItems: "end" }}>
                    <label>
                      <div className="clipSB_hint" style={{ marginBottom: 6 }}>Format</div>
                      <div className="clipSB_input clipSB_inputReadonly" style={{ minHeight: 42, display: "flex", alignItems: "center" }}>
                        {previewFormatMeta.label}
                        {data?.scenarioFormat ? <span className="clipSB_small" style={{ marginLeft: 8, opacity: 0.72 }}>from COMFY BRAIN</span> : null}
                      </div>
                    </label>
                    <label>
                      <div className="clipSB_hint" style={{ marginBottom: 6 }}>Sec</div>
                      <input
                        className="clipSB_input"
                        type="text"
                        inputMode="decimal"
                        value={durationDraft}
                        onChange={(e) => {
                          const next = String(e.target.value || "").replace(/[^\d,.\-]/g, "");
                          setDurationDraft(next);
                        }}
                        onBlur={commitDurationDraft}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") {
                            e.preventDefault();
                            e.currentTarget.blur();
                          }
                        }}
                        onWheel={(e) => e.currentTarget.blur()}
                        placeholder="0,5 – 8,0"
                      />
                    </label>
                  </div>

                  {errorMessage ? (
                    <div className="clipSB_introError clipSB_small">
                      Ошибка генерации: {errorMessage}
                    </div>
                  ) : null}

                  <details className="clipSB_introDetails" style={{ borderColor: `${selectedStyleMeta.accent}30` }}>
                    <summary style={{ cursor: "pointer", listStyle: "none", padding: "10px 12px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, fontSize: 12, fontWeight: 700, color: "#f5f7ff" }}>
                      <span>Details / Prompt info</span>
                      <span className="clipSB_small" style={{ color: selectedStyleMeta.accent }}>{selectedStyleMeta.uiHint}</span>
                    </summary>
                    <div style={{ display: "grid", gap: 10, padding: "0 12px 12px" }}>
                      <div className="clipSB_small" style={{ color: "rgba(245,247,255,0.78)", lineHeight: 1.45 }}>
                        {selectedStyleMeta.shortDescription}
                      </div>
                      <div className="clipSB_small" style={{ color: "rgba(207,216,255,0.74)", lineHeight: 1.4 }}>
                        Rules: {selectedStyleMeta.promptRules.join(" • ")}
                      </div>
                      <div className="clipSB_small" style={{ color: "rgba(255,188,188,0.72)", lineHeight: 1.35 }}>
                        Avoid: {selectedNegativeRules.join(" • ")}
                      </div>
                      <div className="clipSB_small" style={{ color: "#9fb0ff", lineHeight: 1.4 }}>
                        Generate вызывает backend Gemini generation и сохраняет preview как asset URL.
                      </div>
                      {hasBackendGeneratedAsset ? (
                        <div className="clipSB_small" style={{ color: "#b8c4ff", lineHeight: 1.4 }}>
                          Backend-branded asset показан как есть, без локального fake text overlay.
                        </div>
                      ) : null}
                      {detailRows.length ? (
                        <div style={{ display: "grid", gap: 6 }}>
                          {detailRows.map(([label, value]) => (
                            <div key={label} className="clipSB_small" style={{ color: "#dce4ff", lineHeight: 1.4 }}>
                              <strong style={{ color: "#ffffff" }}>{label}:</strong> {value}
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </details>
                </div>

              <div className="clipSB_introFramePreviewCol">
                <div
                  className={`clipSB_introFramePreviewBox${previewUrl ? " clipSB_previewCard" : ""}`}
                  onClick={previewUrl ? () => setZoomedPreview(previewUrl) : undefined}
                  style={{
                    '--intro-style-accent': `${styleMeta.accent}`,
                    '--intro-style-secondary': `${styleMeta.secondary}`,
                    '--intro-style-background': styleMeta.background,
                    minHeight: Math.max(previewFormatMeta.cardMinHeight, isCompactLayout ? 300 : 340),
                    aspectRatio: previewFormatMeta.aspectRatio,
                  }}
                >
                {hasBackendGeneratedAsset ? (
                  <img
                    src={previewUrl}
                    alt={previewTitle}
                    style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                  />
                ) : previewUrl ? (
                  <>
                    <img
                      src={previewUrl}
                      alt={previewTitle}
                      style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover", display: "block" }}
                    />
                    <div
                      style={{
                        position: "absolute",
                        inset: 0,
                        background: isGenerating ? "linear-gradient(180deg, rgba(5,8,16,0.12) 0%, rgba(5,8,16,0.08) 100%)" : "transparent",
                      }}
                    />
                  </>
                ) : (
                  <>
                    <div
                      style={{
                        position: "absolute",
                        inset: 0,
                        background: `radial-gradient(circle at 18% 18%, ${styleMeta.accent}38 0%, transparent 34%), radial-gradient(circle at 84% 20%, ${styleMeta.secondary}32 0%, transparent 38%), linear-gradient(145deg, #080d19 0%, ${styleMeta.secondary} 55%, #020409 100%)`,
                      }}
                    />

                    <div
                      style={{
                        position: "relative",
                        zIndex: 1,
                        minHeight: "100%",
                        width: "100%",
                        padding: Math.max(previewFormatMeta.surfacePadding, 18),
                        display: "flex",
                        flexDirection: "column",
                        justifyContent: "space-between",
                        gap: 14,
                        overflow: "hidden",
                        background: "linear-gradient(180deg, rgba(5,8,16,0.08) 0%, rgba(5,8,16,0.03) 28%, rgba(5,8,16,0.66) 100%)",
                      }}
                    >
                      <div
                        className="clipSB_small"
                        style={{
                          color: styleMeta.accent,
                          letterSpacing: "0.16em",
                          textTransform: "uppercase",
                          fontWeight: 800,
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        ava-studio
                      </div>

                      <div style={{ marginTop: "auto", display: "grid", gap: 10, minWidth: 0, overflow: "hidden" }}>
                        <div style={{ width: previewFormat === INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE ? "72%" : "100%", height: 4, borderRadius: 999, background: `linear-gradient(90deg, ${styleMeta.accent} 0%, ${styleMeta.secondary} 100%)`, opacity: 0.95 }} />
                        <div className="clipSB_small" style={{ color: "rgba(232,236,255,0.76)", letterSpacing: "0.08em", textTransform: "uppercase" }}>
                          ava-studio product 2026
                        </div>
                      </div>
                    </div>
                  </>
                )}
                </div>

                <div className="clipSB_introFrameActions">
                  <button className="clipSB_btn" onClick={() => data?.onGenerate?.(id)} disabled={isGenerating}>
                    {isGenerating ? "Генерация..." : (previewUrl ? "Перегенерировать" : "Сгенерировать")}
                  </button>
                  <button className="clipSB_btn clipSB_btnSecondary" onClick={() => fileInputRef.current?.click()}>
                    Загрузить
                  </button>
                  {previewUrl ? (
                    <button className="clipSB_btn clipSB_btnSecondary" onClick={() => data?.onClearImage?.(id)}>
                      Очистить
                    </button>
                  ) : null}
                </div>
              </div>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/webp"
              style={{ display: "none" }}
              onChange={(e) => {
                const file = e.target.files?.[0];
                if (file) data?.onPickImage?.(id, file);
                e.target.value = "";
              }}
            />
          </div>
        </div>
      </NodeShell>
      {zoomedPreview ? (
        <div
          className="clipSB_previewZoomOverlay"
          onClick={() => setZoomedPreview(null)}
        >
          <img
            src={zoomedPreview}
            alt={`${previewTitle} preview`}
            className="clipSB_previewZoomImage"
          />
        </div>
      ) : null}
    </>
  );
}

function AssemblyNode({ id, data }) {
  const isAssembling = !!data?.isAssembling;
  const canAssemble = !!data?.canAssemble;
  const status = data?.status || "empty";
  const result = data?.result || null;
  const finalVideoUrl = resolveAssetUrl(data?.result?.finalVideoUrl);
  const resultSceneCount = Number(result?.sceneCount || 0);
  const resultTotalSegments = Number(result?.totalSegments || 0);
  const audioApplied = !!result?.audioApplied;
  const isStale = !!data?.isStale;
  const statusLabel = status === "done"
    ? "готово"
    : status === "building"
      ? "сборка"
      : status === "ready"
        ? "готово к сборке"
        : status === "error"
          ? "ошибка"
          : "ожидание";

  return (
    <>
      <Handle type="target" position={Position.Left} id="assembly_in" className="clipSB_handle" style={handleStyle("storyboard_to_assembly")} />
      <Handle type="target" position={Position.Left} id="assembly_intro" className="clipSB_handle" style={handleStyle("intro_to_assembly", { top: 132 })} />
      <NodeShell
        title="ASSEMBLY"
        onClose={() => data?.onRemoveNode?.(id)}
        icon={<span aria-hidden>🎬</span>}
        className="clipSB_nodeAssembly"
      >
        <div className="clipSB_assemblyStats">
          <div className="clipSB_assemblyRow"><span>Source</span><strong className="clipSB_assemblyValue">{data?.sourceLabel || "НЕ ПОДКЛЮЧЕНО"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Intro</span><strong className="clipSB_assemblyValue">{data?.introLabel || "НЕТ INTRO"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Audio</span><strong className="clipSB_assemblyValue">{data?.hasAudio ? "подключено" : "не подключено"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Формат</span><strong>{data?.format || "9:16"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Длительность</span><strong>~{Math.round(Number(data?.durationSec || 0))} сек</strong></div>
          <div className="clipSB_assemblyRow"><span>Статус</span><strong className="clipSB_assemblyValue">{statusLabel}</strong></div>
        </div>

        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <button className={`clipSB_btn ${!canAssemble ? "clipSB_btnMuted" : ""}`} onClick={data?.onAssemble} disabled={!canAssemble}>
            {isAssembling ? "⚙ Собираем клип..." : "Собрать клип"}
          </button>
          {isStale ? (
            <span className="clipSB_small" style={{ color: "#ffb86c", alignSelf: "center" }}>⚠ результат устарел</span>
          ) : null}
          {isAssembling ? (
            <button className="clipSB_btn clipSB_btnSecondary" onClick={data?.onStopAssemble}>
              ⏹ stop
            </button>
          ) : null}
        </div>

        {isAssembling ? (
          <div className="clipSB_assemblyProgress">
            <div className="clipSB_assemblyProgressTitle">⚙ Собираем клип...</div>
            <div className="clipSB_assemblyProgressSub">{data?.assemblyStageLabel || "Подготавливаем итоговый ролик"}</div>
            {Number(data?.assemblyStageCurrent || 0) > 0 && Number(data?.assemblyStageTotal || 0) > 0 ? (
              <div className="clipSB_assemblyProgressSub">{Number(data?.assemblyStageCurrent || 0)} из {Number(data?.assemblyStageTotal || 0)}</div>
            ) : null}
            {data?.assemblyStage ? (
              <div className="clipSB_assemblyProgressSub">Этап: {data.assemblyStage}</div>
            ) : null}
            <div className="clipSB_assemblyProgressTrack">
              <div
                className="clipSB_assemblyProgressBar"
                style={{
                  width: `${Math.min(
                    100,
                    Number(data?.isAssembling ? (Number(data?.progressPercent || 0) <= 0 ? 6 : Number(data?.progressPercent || 0)) : (data?.progressPercent || 0))
                  )}%`,
                }}
              />
            </div>
          </div>
        ) : null}

        {status === "empty" ? <div className="clipSB_assemblyNote">Подключи COMFY STORYBOARD / STORYBOARD к main input Assembly.</div> : null}
        {data?.infoMessage ? <div className="clipSB_assemblyNote">{data.infoMessage}</div> : null}
        {status === "error" && data?.errorMessage ? (
          <div className="clipSB_assemblyErrorBlock">
            <div className="clipSB_assemblyErrorTitle">Ошибка сборки</div>
            <div className="clipSB_assemblyErrorText">{data.errorMessage}</div>
          </div>
        ) : null}

        {finalVideoUrl ? (
          <div className="clipSB_assemblyResult">
            <div className="clipSB_assemblyDoneTitle">✅ Клип готов</div>
            <div className="clipSB_assemblyDoneMeta">
              Сцен: {resultSceneCount > 0 ? resultSceneCount : Number(data?.readyScenes || 0)} • Intro: {result?.introIncluded ? "добавлен" : "нет"} • Аудио: {audioApplied ? "добавлено" : "нет"}
            </div>
            <video className="clipSB_videoPlayer" controls playsInline preload="metadata" src={finalVideoUrl} style={{ marginTop: 8 }} />
            <div className="clipSB_assemblyActions">
              <a className="clipSB_btn clipSB_btnLink" href={finalVideoUrl} target="_blank" rel="noreferrer">Открыть</a>
              <a className="clipSB_btn clipSB_btnLink" href={finalVideoUrl} download>Скачать mp4</a>
            </div>
          </div>
        ) : null}

        {(data?.debugSummary || result) ? (
          <details className="clipSB_assemblyDetails">
            <summary>Details</summary>
            <div className="clipSB_assemblyDetailsBody">
              {data?.debugSummary ? (
                <div className="clipSB_small clipSB_assemblyDetailsText">{data.debugSummary}</div>
              ) : null}
              {result ? (
                <div className="clipSB_small clipSB_assemblyDetailsText">
                  scenes={resultSceneCount}; intro={result?.introIncluded ? "yes" : "no"}; totalSegments={resultTotalSegments || resultSceneCount}; totalSteps={Number(data?.assemblyStageTotal || 0) || Number(result?.totalSteps || 0) || 0}; introDuration={Number(result?.introDurationSec || 0) || 0}
                </div>
              ) : null}
            </div>
          </details>
        ) : null}
      </NodeShell>
    </>
  );
}

// -------------------------
// page
// -------------------------

export default function ClipStoryboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const accountKey = useMemo(() => getAccountKey(user) || "guest", [user]);
  const STORE_KEY = useMemo(() => `ps:clipStoryboard:v1:${accountKey}`, [accountKey]);
  const VIDEO_JOB_STORE_KEY = useMemo(() => `ps:clipStoryboard:videoJob:v1:${accountKey}`, [accountKey]);
  const COMFY_VIDEO_JOB_STORE_KEY = useMemo(() => `ps:clipStoryboard:comfyVideoJob:v1:${accountKey}`, [accountKey]);

  const storageVersionRef = useRef(0);
  const plannerSignatureRef = useRef("");
  const storyboardSignatureRef = useRef("");
  const comfyStoryboardSignatureRef = useRef("");

  const didHydrateRef = useRef(false);
  const isHydratingRef = useRef(true);
  const hydrateInFlightRef = useRef(false);
  const nodesCountRef = useRef(0);
  const parseTokenRef = useRef(0);
  const parseControllerRef = useRef(null);
  const parseTimeoutRef = useRef(null);
  const activeParseNodeRef = useRef(null);
  const comfyParseSeqRef = useRef(0);
  const comfyParseInFlightRef = useRef(new Set());
  const scenarioItemRefs = useRef(new Map());
  const scenarioVideoSectionRef = useRef(null);
  const scenarioVideoCardRef = useRef(null);
  const scenarioVideoScrollTimerRef = useRef(null);
  const scenarioVideoScrollRafRef = useRef(0);
  const scenarioVideoPollTimersRef = useRef(new Map());
  const scenarioVideoJobsBySceneRef = useRef(new Map());
  const scenarioActivePollingJobIdsRef = useRef(new Set());
  const restoredScenarioVideoJobsRef = useRef(new Set());
  const comfyVideoPollTimersRef = useRef(new Map());
  const comfyVideoJobsBySceneRef = useRef(new Map());
  const comfyPromptSyncTimersRef = useRef(new Map());
  const comfyPromptSyncInFlightRef = useRef(new Map());
  const renderTraceSnapshotRef = useRef(null);
  const renderTraceLastLogRef = useRef({ ts: 0, signature: "" });
  const bindHandlersTraceRef = useRef({ ts: 0, changed: null });
  const [scenarioVideoFocusPulse, setScenarioVideoFocusPulse] = useState(false);

  const buildComfyStoryboardPatch = useCallback(({
    node,
    nodesNow = [],
    edgesNow = [],
    reason = "unknown",
  }) => {
    const currentData = node?.data && typeof node.data === "object" ? node.data : {};
    const rawStatus = String(currentData.parseStatus || "idle").trim().toLowerCase() || "idle";
    const mockScenes = normalizeSceneCollectionWithSceneId(currentData.mockScenes, "comfy_scene");
    const connectedPlanEdges = (Array.isArray(edgesNow) ? edgesNow : []).filter((edge) => edge?.target === node?.id && String(edge?.targetHandle || "") === "comfy_plan");
    const connectedSourceIds = connectedPlanEdges.map((edge) => String(edge?.source || "")).filter(Boolean);
    const connectedSourceNodes = connectedSourceIds
      .map((sourceId) => (Array.isArray(nodesNow) ? nodesNow : []).find((candidate) => candidate?.id === sourceId && candidate?.type === "comfyBrain") || null)
      .filter(Boolean);
    const activeRequestId = String(currentData.activeRequestId || "").trim();
    const activeRequestSourceNodeId = String(currentData.activeRequestSourceNodeId || "").trim();
    const activeSourceNode = connectedSourceNodes.find((sourceNode) => {
      const sourceNodeId = String(sourceNode?.id || "").trim();
      if (activeRequestSourceNodeId && sourceNodeId !== activeRequestSourceNodeId) return false;
      const sourceStatus = String(sourceNode?.data?.parseStatus || "").trim().toLowerCase();
      return comfyParseInFlightRef.current.has(sourceNodeId) || sourceStatus === "parsing";
    }) || null;
    const hasActiveRequest = !!activeRequestId && !!activeSourceNode;
    const sceneCount = Math.max(mockScenes.length, Number(currentData.sceneCount || 0));
    const hasScenes = sceneCount > 0;
    const isStale = currentData.isStale === true;

    let nextStatus = ["idle", "updating", "ready", "error", "stale"].includes(rawStatus) ? rawStatus : "idle";
    let statusReason = "keep_existing_status";

    if (hasActiveRequest) {
      nextStatus = "updating";
      statusReason = "active_request_in_progress";
    } else if (rawStatus === "updating") {
      nextStatus = isStale ? "stale" : (hasScenes ? "ready" : "idle");
      statusReason = "reset_orphaned_updating_status";
    } else if (isStale && rawStatus !== "error") {
      nextStatus = "stale";
      statusReason = "stale_without_active_request";
    } else if (hasScenes && rawStatus === "idle") {
      nextStatus = "ready";
      statusReason = "promote_idle_with_existing_scenes";
    } else if (!hasScenes && rawStatus === "ready") {
      nextStatus = "idle";
      statusReason = "demote_ready_without_scenes";
    }

    const patch = {
      ...currentData,
      mockScenes,
      sceneCount,
      parseStatus: nextStatus,
      isUpdating: hasActiveRequest,
      isBusy: hasActiveRequest,
      isGenerating: hasActiveRequest,
      hasActiveRequest,
      activeRequestId: hasActiveRequest ? activeRequestId : "",
      activeRequestSourceNodeId: hasActiveRequest ? activeRequestSourceNodeId : "",
      activeRequestStartedAt: hasActiveRequest ? String(currentData.activeRequestStartedAt || "") : "",
    };

    console.debug("[COMFY STORYBOARD] patch build", {
      nodeId: String(node?.id || ""),
      reason,
      rawStatus,
      nextStatus,
      statusReason,
      hasActiveRequest,
      activeRequestId,
      activeRequestSourceNodeId,
      connectedSourceIds,
      connectedSourceStates: connectedSourceNodes.map((sourceNode) => ({
        nodeId: String(sourceNode?.id || ""),
        parseStatus: String(sourceNode?.data?.parseStatus || ""),
        inFlight: comfyParseInFlightRef.current.has(String(sourceNode?.id || "").trim()),
      })),
      fields: {
        status: String(currentData.status || ""),
        parseStatus: String(currentData.parseStatus || ""),
        isUpdating: currentData.isUpdating ?? null,
        isGenerating: currentData.isGenerating ?? null,
        isBusy: currentData.isBusy ?? null,
        isStale: currentData.isStale ?? null,
        isDirty: currentData.isDirty ?? null,
        dirty: currentData.dirty ?? null,
        stale: currentData.stale ?? null,
      },
      sceneCount,
      hasScenes,
      plannerMetaPresent: !!currentData.plannerMeta,
    });

    if (rawStatus === "updating" && !hasActiveRequest) {
      console.warn("[COMFY STORYBOARD] orphaned updating state reset", {
        nodeId: String(node?.id || ""),
        reason,
        fallbackStatus: nextStatus,
        activeRequestId,
        activeRequestSourceNodeId,
        connectedSourceIds,
      });
    }

    return patch;
  }, []);

  const summarizeComfyPayload = useCallback((payload) => {
    const refs = payload?.refsByRole && typeof payload.refsByRole === "object" ? payload.refsByRole : {};
    const refsCounts = Object.fromEntries(
      Object.entries(refs).map(([role, items]) => [role, Array.isArray(items) ? items.length : 0])
    );
    return {
      mode: payload?.mode || "",
      output: payload?.output || "",
      stylePreset: payload?.stylePreset || "",
      audioStoryMode: payload?.audioStoryMode || "lyrics_music",
      hasText: !!String(payload?.text || "").trim(),
      hasAudio: !!String(payload?.audioUrl || "").trim(),
      audioDurationSec: Number(payload?.audioDurationSec || 0) > 0 ? Number(payload.audioDurationSec) : null,
      refsCounts,
    };
  }, []);

  const summarizeComfyResponse = useCallback((response) => {
    const scenes = Array.isArray(response?.scenes) ? response.scenes : [];
    const firstScene = scenes[0] || null;
    const planMeta = response?.planMeta && typeof response.planMeta === "object"
      ? response.planMeta
      : {};
    return {
      ok: !!response?.ok,
      mode: planMeta.mode || null,
      output: planMeta.output || null,
      stylePreset: planMeta.stylePreset || null,
      audioStoryMode: planMeta.audioStoryMode || null,
      storyControlMode: planMeta.storyControlMode || null,
      audioDurationSec: Number(planMeta?.audioDurationSec || 0) > 0 ? Number(planMeta.audioDurationSec) : null,
      timelineDurationSec: Number(planMeta?.timelineDurationSec || 0) > 0 ? Number(planMeta.timelineDurationSec) : null,
      sceneDurationTotalSec: Number(planMeta?.sceneDurationTotalSec || 0) > 0 ? Number(planMeta.sceneDurationTotalSec) : null,
      scenesCount: scenes.length,
      warnings: Array.isArray(response?.warnings) ? response.warnings.length : 0,
      errors: Array.isArray(response?.errors) ? response.errors.length : 0,
      firstScene: firstScene
        ? { sceneId: firstScene.sceneId || null, title: firstScene.title || null }
        : null,
    };
  }, []);

  const [lastSavedAt, setLastSavedAt] = useState(0);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [brainGuideOpen, setBrainGuideOpen] = useState(false);
  const brainGuideRef = useRef(null);

  
const [scenarioEditor, setScenarioEditor] = useState({
  open: false,
  nodeId: null,
  selected: 0,
  selectedSceneId: "",
});
const [activeScenarioStoryboardId, setActiveScenarioStoryboardId] = useState(null);
const [isScenarioStoryboardOpen, setIsScenarioStoryboardOpen] = useState(false);
const [comfyEditor, setComfyEditor] = useState({
  open: false,
  nodeId: null,
  selected: 0,
});

useEffect(() => {
  if (!brainGuideOpen) return;
  const onPointerDown = (event) => {
    if (brainGuideRef.current?.contains(event.target)) return;
    setBrainGuideOpen(false);
  };
  window.addEventListener("mousedown", onPointerDown);
  window.addEventListener("touchstart", onPointerDown, { passive: true });
  return () => {
    window.removeEventListener("mousedown", onPointerDown);
    window.removeEventListener("touchstart", onPointerDown);
  };
}, [brainGuideOpen]);

useEffect(() => {
  const handler = (e) => {
    setComfyEditor({
      open: true,
      nodeId: e?.detail?.nodeId || null,
      selected: 0,
    });
  };
  window.addEventListener('ps:clipOpenComfyStoryboard', handler);
  return () => window.removeEventListener('ps:clipOpenComfyStoryboard', handler);
}, []);

// Fullscreen canvas on this page (hide global sidebar)
  useEffect(() => {
    document.body.classList.add("ps_clip_fullscreen");
    return () => document.body.classList.remove("ps_clip_fullscreen");
  }, []);

  const defaultNodes = useMemo(
    () => [
      {
        id: "audio",
        type: "audioNode",
        position: { x: 120, y: 120 },
        data: { audioUrl: "", audioName: "", uploading: false, audioDurationSec: null },
      },
      {
        id: "text",
        type: "textNode",
        position: { x: 120, y: 300 },
        data: { textValue: "" },
      },
      {
        id: "brain",
        type: "brainNode",
        position: { x: 520, y: 200 },
        data: { mode: "clip", scenarioKey: "clip" },
      },
      {
        id: "assembly",
        type: "assemblyNode",
        position: { x: 860, y: 200 },
        data: {},
      },
    ],
    []
  );

  const defaultEdges = useMemo(
    () => [
      {
        id: "e-audio-brain",
        source: "audio",
        sourceHandle: "audio",
        target: "brain",
        targetHandle: "audio",
        ...getEdgePresentation({ sourceType: "audioNode", sourceHandle: "audio", targetType: "brainNode", targetHandle: "audio" }),
        data: { kind: "audio" },
      },
      {
        id: "e-text-brain",
        source: "text",
        sourceHandle: "text",
        target: "brain",
        targetHandle: "text",
        ...getEdgePresentation({ sourceType: "textNode", sourceHandle: "text", targetType: "brainNode", targetHandle: "text" }),
        data: { kind: "text" },
      },
      {
        id: "e-brain-assembly",
        source: "brain",
        target: "assembly",
        ...getEdgePresentation({ sourceType: "brainNode", targetType: "assemblyNode" }),
        data: { kind: "brain_to_assembly" },
      },
    ],
    []
  );

const [nodes, setNodes, onNodesChange] = useNodesState(defaultNodes);
const [edges, setEdges] = useEdgesState(defaultEdges);
const onOpenScenarioStoryboard = useCallback((nodeId) => {
  setActiveScenarioStoryboardId(nodeId || null);
  setIsScenarioStoryboardOpen(true);
}, []);

  useEffect(() => {
    nodesCountRef.current = nodes.length;
  }, [nodes.length]);

const scenarioNode = useMemo(() => {
  if (scenarioEditor.nodeId) return nodes.find((n) => n.id === scenarioEditor.nodeId) || null;
  return nodes.find((n) => n.type === "storyboardNode") || null;
}, [nodes, scenarioEditor.nodeId]);

const activeScenarioStoryboardNode = useMemo(() => {
  if (activeScenarioStoryboardId) return nodes.find((n) => n.id === activeScenarioStoryboardId && n.type === "scenarioStoryboard") || null;
  return nodes.find((n) => n.type === "scenarioStoryboard") || null;
}, [activeScenarioStoryboardId, nodes]);
const activeScenarioAudioData = activeScenarioStoryboardNode?.data?.audioData && typeof activeScenarioStoryboardNode.data.audioData === "object"
  ? activeScenarioStoryboardNode.data.audioData
  : {};
const activeScenarioMasterAudioResolution = useMemo(
  () => resolveScenarioMasterAudioFromGraph({ scenarioNode: activeScenarioStoryboardNode, nodes, edges }),
  [activeScenarioStoryboardNode, nodes, edges]
);
const activeScenarioMasterAudioUrl = String(activeScenarioMasterAudioResolution?.url || "").trim();
const activeScenarioMusicUrl = String(
  activeScenarioAudioData?.musicUrl
  || activeScenarioStoryboardNode?.data?.musicUrl
  || ""
).trim();

const scenarioFlowSourceNode = useMemo(() => {
  if (activeScenarioStoryboardNode?.id) return activeScenarioStoryboardNode;
  return scenarioNode;
}, [activeScenarioStoryboardNode, scenarioNode]);

const scenarioBrainRefs = useMemo(() => {
  if (!scenarioFlowSourceNode?.id) return { character: [], location: [], style: [], props: [], refsByRole: {} };
  const SCENARIO_PLAN_INPUT_HANDLES = new Set(["plan_in", "scenario_storyboard_in"]);
  const incomingPlanEdge = [...edges]
    .reverse()
    .find((e) => e.target === scenarioFlowSourceNode.id && SCENARIO_PLAN_INPUT_HANDLES.has((e.targetHandle || "")));
  if (!incomingPlanEdge?.source) return { character: [], location: [], style: [], props: [], refsByRole: {} };
  const sourceNode = nodes.find((n) => n.id === incomingPlanEdge.source) || null;
  const sourceNodeType = String(sourceNode?.type || "").trim();
  const sourceMode = sourceNodeType === "comfyNarrative" ? "narrative" : "brain";
  const planRefs = sourceNode?.data?.scenePlan?.refs || {};
  const plannerRefsByRole = sourceNode?.data?.plannerMeta?.plannerInput?.refsByRole;
  const plannerRefsByRoleMap = plannerRefsByRole && typeof plannerRefsByRole === "object"
    ? extractScenarioRefsByRoleFromSource(plannerRefsByRole)
    : Object.fromEntries(SCENARIO_IMAGE_ROLE_KEYS.map((role) => [role, []]));
  const hasPlannerRefsByRole = SCENARIO_IMAGE_ROLE_KEYS.some((role) => (plannerRefsByRoleMap?.[role] || []).length > 0);

  const scenePlanRefsByRoleMap = extractScenarioRefsByRoleFromSource({
    character_1: planRefs?.character,
    location: planRefs?.location,
    style: planRefs?.style,
    props: planRefs?.props,
  });
  const hasScenePlanRefs = SCENARIO_IMAGE_ROLE_KEYS.some((role) => (scenePlanRefsByRoleMap?.[role] || []).length > 0);

  const narrativeRefs = collectScenarioNarrativeRefs({ sourceNode });
  const refsByRole = sourceMode === "narrative"
    ? mergeScenarioRefsByRole(narrativeRefs?.refsByRole, plannerRefsByRoleMap)
    : plannerRefsByRoleMap;
  const hasNarrativeRefsByRole = !!narrativeRefs?.hasNarrativeRefsByRole;
  const hasNarrativeContextRefs = !!narrativeRefs?.hasNarrativeContextRefs;

  const brainInput = sourceMode === "brain"
    ? collectBrainPlannerInput({ brainNodeId: incomingPlanEdge.source, nodesList: nodes, edgesList: edges })
    : null;

  const result = {
    character: sourceMode === "narrative"
      ? (narrativeRefs?.character || [])
      : (Array.isArray(planRefs.character) ? planRefs.character : (brainInput?.characterRefs || [])),
    location: sourceMode === "narrative"
      ? (narrativeRefs?.location || [])
      : (Array.isArray(planRefs.location) ? planRefs.location : (brainInput?.locationRefs || [])),
    style: sourceMode === "narrative"
      ? (narrativeRefs?.style || [])
      : (Array.isArray(planRefs.style) ? planRefs.style : (brainInput?.styleRefs || [])),
    props: sourceMode === "narrative"
      ? (narrativeRefs?.props || [])
      : (Array.isArray(planRefs.props) ? planRefs.props : (brainInput?.propsRefs || [])),
    propAnchorLabel: planRefs.propAnchorLabel,
    sessionCharacterAnchor: planRefs.sessionCharacterAnchor,
    sessionLocationAnchor: planRefs.sessionLocationAnchor,
    sessionStyleAnchor: planRefs.sessionStyleAnchor,
    sessionBaseline: planRefs.sessionBaseline,
    refsByRole,
  };

  if (CLIP_TRACE_SCENARIO_IMAGE_PAYLOAD) {
    console.debug("[SCENARIO BRAIN REFS]", {
      sourceNodeId: String(sourceNode?.id || ""),
      sourceNodeType,
      sourceHandle: String(incomingPlanEdge?.sourceHandle || ""),
      sourceMode,
      hasScenePlanRefs,
      hasPlannerRefsByRole,
      hasNarrativeRefsByRole,
      hasNarrativeContextRefs,
      refsCountsByRole: summarizeRefsByRole(result?.refsByRole || {}),
    });
  }

  return result;
}, [edges, nodes, scenarioFlowSourceNode?.id]);

const scenarioScenes = useMemo(() => {
  const arr = scenarioFlowSourceNode?.data?.scenes;
  return normalizeSceneCollectionWithSceneId(arr, "scene");
}, [scenarioFlowSourceNode]);

useEffect(() => {
  if (!CLIP_TRACE_SCENARIO_EDITOR_GENERATE) return;
  const activeScenes = Array.isArray(activeScenarioStoryboardNode?.data?.scenes) ? activeScenarioStoryboardNode.data.scenes : [];
  const legacyScenes = Array.isArray(scenarioNode?.data?.scenes) ? scenarioNode.data.scenes : [];
  console.debug("[SCENARIO SOURCE]", {
    editorNodeId: String(scenarioEditor?.nodeId || ""),
    activeScenarioStoryboardNodeId: String(activeScenarioStoryboardNode?.id || ""),
    legacyScenarioNodeId: String(scenarioNode?.id || ""),
    sourceNodeIdUsedByFlow: String(scenarioFlowSourceNode?.id || ""),
    scenesCountFromActiveScenarioStoryboard: activeScenes.length,
    scenesCountFromLegacyStoryboard: legacyScenes.length,
    selectedSceneId: String(scenarioEditor?.selectedSceneId || (scenarioScenes[scenarioEditor.selected] || {}).sceneId || ""),
  });
}, [
  activeScenarioStoryboardNode,
  scenarioEditor?.nodeId,
  scenarioEditor.selected,
  scenarioEditor?.selectedSceneId,
  scenarioFlowSourceNode?.id,
  scenarioNode,
  scenarioScenes,
]);

const scenarioSelectedIndex = useMemo(() => {
  if (!Array.isArray(scenarioScenes) || scenarioScenes.length === 0) return -1;
  const selectedSceneId = String(scenarioEditor?.selectedSceneId || "").trim();
  if (selectedSceneId) {
    const idxById = scenarioScenes.findIndex((scene) => String(scene?.sceneId || "").trim() === selectedSceneId);
    if (idxById >= 0) return idxById;
  }
  const selectedIdx = Number.isFinite(scenarioEditor?.selected) ? Number(scenarioEditor.selected) : 0;
  return Math.min(Math.max(selectedIdx, 0), scenarioScenes.length - 1);
}, [scenarioEditor?.selected, scenarioEditor?.selectedSceneId, scenarioScenes]);

const recommendedNextSceneIndex = useMemo(() => {
  if (!Array.isArray(scenarioScenes) || scenarioScenes.length === 0) return -1;
  const currentIdx = Number.isFinite(scenarioSelectedIndex) ? scenarioSelectedIndex : -1;
  return scenarioScenes.findIndex((scene, idx) => idx > currentIdx && !String(scene?.videoUrl || "").trim());
}, [scenarioScenes, scenarioSelectedIndex]);

const scenarioSelected = scenarioSelectedIndex >= 0 ? (scenarioScenes[scenarioSelectedIndex] || null) : null;
const scenarioSelectedImageStrategy = String(scenarioSelected?.imageStrategy || deriveScenarioImageStrategy(scenarioSelected)).trim().toLowerCase() || "single";
const scenarioSelectedTransitionType = resolveSceneTransitionType(scenarioSelected);
const scenarioSelectedIsLipSync = isLipSyncScene(scenarioSelected);
const scenarioPreviousScene = scenarioSelectedIndex > 0 ? scenarioScenes[scenarioSelectedIndex - 1] : null;
const scenarioSelectedFrameUrls = resolveSceneFrameUrls(scenarioSelected, scenarioPreviousScene);
const scenarioSelectedPreviewSources = resolveScenarioScenePreviewSources(scenarioSelected, scenarioPreviousScene);
const scenarioSelectedResolvedPreviewSrc = scenarioSelectedPreviewSources.resolvedPreviewSrc;
const scenarioSelectedResolvedStartPreviewSrc = scenarioSelectedPreviewSources.resolvedStartPreviewSrc;
const scenarioSelectedResolvedEndPreviewSrc = scenarioSelectedPreviewSources.resolvedEndPreviewSrc;
const scenarioSelectedCanInheritPreviousEnd = scenarioSelectedTransitionType === "continuous"
  && !!scenarioPreviousScene
  && !!String(scenarioPreviousScene?.endImageUrl || scenarioPreviousScene?.endFrameImageUrl || "").trim();
const scenarioSelectedEffectiveStartImageUrl = getEffectiveSceneStartImage(scenarioSelected, scenarioPreviousScene);
const scenarioSelectedEndImageUrl = String(scenarioSelectedFrameUrls.endImageUrl || "").trim();
const scenarioSelectedVideoSourceImageUrl = String(scenarioSelected?.videoSourceImageUrl || "").trim();
const scenarioSelectedVideoPanelActivated = !!scenarioSelected?.videoPanelActivated;
const scenarioSelectedStartImageSource = getSceneStartImageSource(scenarioSelected, scenarioPreviousScene);
const scenarioSelectedImageFormat = resolvePreferredSceneFormat(scenarioSelected?.format, scenarioSelected?.imageFormat);
const scenarioSelectedIndexLabel = Number.isFinite(scenarioSelectedIndex) && scenarioSelectedIndex >= 0 ? scenarioSelectedIndex + 1 : 0;
const scenarioSelectedDisplayTime = resolveSceneDisplayTime(scenarioSelected);
const scenarioSelectedT0 = Number(scenarioSelectedDisplayTime.startSec ?? 0);
const scenarioSelectedT1 = Number(scenarioSelectedDisplayTime.endSec ?? 0);
const scenarioSelectedExpectedSliceSec = Number(
  scenarioSelected?.audioSliceDurationSec
    ?? scenarioSelected?.audioSliceExpectedDurationSec
    ?? Math.max(0, scenarioSelectedT1 - scenarioSelectedT0)
);
const scenarioSelectedAudioSliceStatus = String(scenarioSelected?.audioSliceStatus || "").trim();
const scenarioSelectedAudioSliceError = String(
  scenarioSelected?.audioSliceError || scenarioSelected?.audioSliceLoadError || ""
).trim();
const globalAudioUrlRaw = useMemo(() => extractGlobalAudioUrlFromNodes(nodes), [nodes]);
const globalAudioUrlResolved = useMemo(() => resolveAssetUrl(globalAudioUrlRaw), [globalAudioUrlRaw]);
const scenarioSelectedAudioSliceUrl = useMemo(() => resolveAssetUrl(scenarioSelected?.audioSliceUrl), [scenarioSelected?.audioSliceUrl]);
useEffect(() => {
  if (!CLIP_TRACE_SCENARIO_SCENE_ASSETS) return;
  console.debug("[SCENARIO SCENE ASSETS]", {
    selectedSceneId: String(scenarioSelected?.sceneId || ""),
    imageUrl: String(scenarioSelected?.imageUrl || "").trim(),
    startFrameImageUrl: String(scenarioSelected?.startImageUrl || scenarioSelected?.startFrameImageUrl || "").trim(),
    endFrameImageUrl: String(scenarioSelected?.endImageUrl || scenarioSelected?.endFrameImageUrl || "").trim(),
    videoUrl: String(scenarioSelected?.videoUrl || "").trim(),
    imageStatus: String(scenarioSelected?.imageStatus || "").trim(),
    videoStatus: String(scenarioSelected?.videoStatus || "").trim(),
  });
}, [scenarioSelected]);
useEffect(() => {
  if (!CLIP_TRACE_SCENARIO_SCENE_ASSETS) return;
  console.debug("[SCENARIO PREVIEW SRC FINAL]", {
    sceneId: String(scenarioSelected?.sceneId || ""),
    single: resolveAssetUrl(scenarioSelected?.imageUrl),
    start: resolveAssetUrl(scenarioSelectedEffectiveStartImageUrl),
    end: resolveAssetUrl(scenarioSelectedEndImageUrl),
  });
}, [scenarioSelected?.sceneId, scenarioSelected?.imageUrl, scenarioSelectedEffectiveStartImageUrl, scenarioSelectedEndImageUrl]);
const scenarioPreviousSceneImageSource = scenarioPreviousScene?.endImageUrl
  ? "endImageUrl"
  : scenarioPreviousScene?.endFrameImageUrl
    ? "endFrameImageUrl"
  : scenarioPreviousScene?.imageUrl
    ? "imageUrl"
    : scenarioPreviousScene?.startImageUrl
      ? "startImageUrl"
      : "none";
const scenarioHasImageForVideo = scenarioSelectedImageStrategy === "first_last"
  ? !!(scenarioSelectedFrameUrls.effectiveStartImageUrl || scenarioSelectedFrameUrls.fallbackImageUrl) && !!scenarioSelectedFrameUrls.endImageUrl
  : scenarioSelectedImageStrategy === "continuation"
    ? !!(scenarioSelectedFrameUrls.effectiveStartImageUrl || scenarioSelectedFrameUrls.fallbackImageUrl)
    : !!scenarioSelectedFrameUrls.fallbackImageUrl;
const scenarioCanShowAddToVideoButton = scenarioHasImageForVideo && !scenarioSelectedVideoPanelActivated;

const comfyNode = useMemo(() => {
  if (comfyEditor.nodeId) return nodes.find((n) => n.id === comfyEditor.nodeId && n.type === 'comfyStoryboard') || null;
  return nodes.find((n) => n.type === 'comfyStoryboard') || null;
}, [nodes, comfyEditor.nodeId]);

const comfyScenes = useMemo(() => {
  const arr = normalizeSceneCollectionWithSceneId(comfyNode?.data?.mockScenes, "comfy_scene");
  return arr.map((scene) => normalizeComfyScenePrompts({
    ...scene,
    videoJobId: String(scene?.videoJobId || ''),
    videoStatus: String(scene?.videoStatus || ''),
    videoError: String(scene?.videoError || ''),
  }));
}, [comfyNode]);

useEffect(() => {
  const latestBrainNode = nodes.find((node) => node.type === "brainNode") || null;
  plannerSignatureRef.current = String(latestBrainNode?.data?.plannerInputSignature || "");
  storyboardSignatureRef.current = buildSceneSignature(scenarioScenes, "scene");
  comfyStoryboardSignatureRef.current = buildSceneSignature(comfyScenes, "comfy_scene");
}, [comfyScenes, nodes, scenarioScenes]);

const shouldInvalidateClipStoryboardStorage = useCallback((payload) => {
  const savedPlannerSignature = String(payload?.plannerInputSignature || "");
  const savedScenarioSignature = String(payload?.storyboardSceneSignature || "");
  const savedComfySignature = String(payload?.comfySceneSignature || "");
  const runtimePlannerSignature = String(plannerSignatureRef.current || "");
  const runtimeScenarioSignature = String(storyboardSignatureRef.current || "");
  const runtimeComfySignature = String(comfyStoryboardSignatureRef.current || "");

  if (runtimePlannerSignature && savedPlannerSignature && runtimePlannerSignature !== savedPlannerSignature) return true;
  if (runtimeScenarioSignature && savedScenarioSignature && runtimeScenarioSignature !== savedScenarioSignature) return true;
  if (runtimeComfySignature && savedComfySignature && runtimeComfySignature !== savedComfySignature) return true;
  return false;
}, []);

const isClipHydrationBlocked = useCallback(() => {
  const nodesNow = nodesRef.current || [];
  const hasBrainParse = nodesNow.some((node) => node?.type === "brainNode" && !!node?.data?.isParsing);
  const hasComfyParse = nodesNow.some((node) => {
    if (node?.type !== "comfyBrain") return false;
    const status = String(node?.data?.parseStatus || "");
    return status === "loading" || status === "parsing";
  });
  const hasScenarioVideoGeneration = scenarioScenes.some((scene) => isVideoJobInProgress(scene?.videoStatus));
  const hasComfyVideoGeneration = comfyScenes.some((scene) => isVideoJobInProgress(scene?.videoStatus));
  const hasPendingParse = !!activeParseNodeRef.current || !!parseControllerRef.current || comfyParseInFlightRef.current.size > 0;
  return hasPendingParse || hasBrainParse || hasComfyParse || hasScenarioVideoGeneration || hasComfyVideoGeneration;
}, [comfyScenes, scenarioScenes]);

const clearClipStoryboardStorageForCurrentAccount = useCallback((reason = "") => {
  console.info("[CLIP STORAGE] clear account storage", {
    reason,
    accountKey,
    STORE_KEY,
    VIDEO_JOB_STORE_KEY,
    COMFY_VIDEO_JOB_STORE_KEY,
  });
  safeDel(STORE_KEY);
  safeDel(VIDEO_JOB_STORE_KEY);
  safeDel(COMFY_VIDEO_JOB_STORE_KEY);
  storageVersionRef.current += 1;
}, [COMFY_VIDEO_JOB_STORE_KEY, STORE_KEY, VIDEO_JOB_STORE_KEY, accountKey]);

const comfySelectedIndex = Number.isFinite(comfyEditor.selected) ? comfyEditor.selected : 0;
const comfySafeIndex = comfySelectedIndex < 0 ? 0 : Math.min(comfySelectedIndex, Math.max(0, comfyScenes.length - 1));
const comfySelectedScene = comfyScenes[comfySafeIndex] || null;
const comfyModeMeta = getModeDisplayMeta(comfyNode?.data?.mode || "clip");
const comfyStyleMeta = getStyleDisplayMeta(comfyNode?.data?.stylePreset || "realism");
const comfyRefsByRole = (comfyNode?.data?.plannerMeta?.plannerInput?.refsByRole && typeof comfyNode?.data?.plannerMeta?.plannerInput?.refsByRole === "object")
  ? comfyNode.data.plannerMeta.plannerInput.refsByRole
  : {};
const comfyPreviousScene = comfySafeIndex > 0 ? (comfyScenes[comfySafeIndex - 1] || null) : null;
  const [scenarioImageLoading, setScenarioImageLoading] = useState(false);
  const [scenarioImageError, setScenarioImageError] = useState("");

  const [scenarioAudioSliceLoading, setScenarioAudioSliceLoading] = useState(false);
  const [scenarioVideoError, setScenarioVideoError] = useState("");
  const [scenarioVideoOpen, setScenarioVideoOpen] = useState(false);
  const [comfyImageLoading, setComfyImageLoading] = useState(false);
  const [comfyImageError, setComfyImageError] = useState("");
  const [comfyAudioSliceLoading, setComfyAudioSliceLoading] = useState(false);

  const [comfyPromptSyncError, setComfyPromptSyncError] = useState("");
  const [assemblyBuildState, setAssemblyBuildState] = useState("idle");
  const [assemblyError, setAssemblyError] = useState("");
  const [assemblyInfo, setAssemblyInfo] = useState("");
  const [assemblyResult, setAssemblyResult] = useState(null);
  const [isAssemblyStale, setIsAssemblyStale] = useState(false);
  const [isAssembling, setIsAssembling] = useState(false);
  const [assemblyJobId, setAssemblyJobId] = useState("");
  const [assemblyProgressPercent, setAssemblyProgressPercent] = useState(0);
  const [assemblyStage, setAssemblyStage] = useState("");
  const [assemblyStageLabel, setAssemblyStageLabel] = useState("");
  const [assemblyStageCurrent, setAssemblyStageCurrent] = useState(0);
  const [assemblyStageTotal, setAssemblyStageTotal] = useState(0);
  const [lightboxUrl, setLightboxUrl] = useState("");
  const [lightboxAnchorRect, setLightboxAnchorRect] = useState(null);
  const [lightboxActive, setLightboxActive] = useState(false);
  const lightboxCloseTimerRef = useRef(null);

const comfySelectedSceneId = String(comfySelectedScene?.sceneId || "").trim();
const comfySelectedDisplayTime = resolveSceneDisplayTime(comfySelectedScene);
const comfySelectedStartSec = Number(comfySelectedDisplayTime.startSec ?? 0);
const comfySelectedEndSec = Number(comfySelectedDisplayTime.endSec ?? comfySelectedStartSec);
const comfySelectedExpectedSliceSec = Number(
  comfySelectedScene?.audioSliceDurationSec
    ?? comfySelectedScene?.audioSliceExpectedDurationSec
    ?? Math.max(0, comfySelectedEndSec - comfySelectedStartSec)
);
const comfySelectedAudioSliceStatus = String(comfySelectedScene?.audioSliceStatus || "").trim();
const comfySelectedAudioSliceError = String(
  comfySelectedScene?.audioSliceError || comfySelectedScene?.audioSliceLoadError || ""
).trim();
const comfySelectedAudioSliceUrl = useMemo(() => resolveAssetUrl(comfySelectedScene?.audioSliceUrl), [comfySelectedScene?.audioSliceUrl]);
const scenarioVideoLoading = isVideoJobInProgress(scenarioSelected?.videoStatus);
const comfyVideoLoading = isVideoJobInProgress(comfySelectedScene?.videoStatus);
const comfyHasActiveVideoJobForScene = comfyVideoLoading;
const scenarioHasVideoUrl = Boolean(String(scenarioSelected?.videoUrl || "").trim());
const comfyHasVideoUrl = Boolean(String(comfySelectedScene?.videoUrl || "").trim());
const scenarioShowingGeneratingOverlay = scenarioVideoLoading && !scenarioHasVideoUrl;
const comfyShowingGeneratingOverlay = comfyHasActiveVideoJobForScene && Boolean(comfySelectedScene?.imageUrl) && !comfyHasVideoUrl;
const comfyShowVideoSection = Boolean(
  comfySelectedScene?.videoPanelOpen
  || comfyHasVideoUrl
  || comfyHasActiveVideoJobForScene
);

  useEffect(() => {
    if (!scenarioSelected) return;
    console.info("[CLIP TRACE] preview render state", {
      scope: "scenario",
      sceneId: String(scenarioSelected?.sceneId || ""),
      hasVideoUrl: scenarioHasVideoUrl,
      videoStatus: String(scenarioSelected?.videoStatus || ""),
      videoJobId: String(scenarioSelected?.videoJobId || ""),
      showingGeneratingOverlay: scenarioShowingGeneratingOverlay,
    });
  }, [scenarioHasVideoUrl, scenarioSelected, scenarioShowingGeneratingOverlay]);

  useEffect(() => {
    if (!comfySelectedScene) return;
    console.info("[CLIP TRACE] preview render state", {
      scope: "comfy",
      sceneId: String(comfySelectedScene?.sceneId || ""),
      hasVideoUrl: comfyHasVideoUrl,
      videoStatus: String(comfySelectedScene?.videoStatus || ""),
      videoJobId: String(comfySelectedScene?.videoJobId || ""),
      showingGeneratingOverlay: comfyShowingGeneratingOverlay,
    });
  }, [comfyHasVideoUrl, comfySelectedScene, comfyShowingGeneratingOverlay]);

  const openLightbox = useCallback((url, sourceRect = null) => {
    if (lightboxCloseTimerRef.current) {
      clearTimeout(lightboxCloseTimerRef.current);
      lightboxCloseTimerRef.current = null;
    }
    setLightboxAnchorRect(sourceRect);
    setLightboxActive(false);
    setLightboxUrl(url);
  }, []);

  const closeLightbox = useCallback(() => {
    if (!lightboxUrl) return;
    setLightboxActive(false);
    if (lightboxCloseTimerRef.current) {
      clearTimeout(lightboxCloseTimerRef.current);
    }
    lightboxCloseTimerRef.current = setTimeout(() => {
      setLightboxUrl("");
      setLightboxAnchorRect(null);
      lightboxCloseTimerRef.current = null;
    }, 320);
  }, [lightboxUrl]);

  const handleComfyPreviewOpenLightbox = useCallback((url, event) => {
    const rect = event?.currentTarget?.getBoundingClientRect?.() || null;
    openLightbox(url, rect);
  }, [openLightbox]);

  const lightboxImageStyle = useMemo(() => {
    if (!lightboxAnchorRect || lightboxActive || typeof window === "undefined") return undefined;
    const viewportWidth = window.innerWidth || 1;
    const viewportHeight = window.innerHeight || 1;
    const centerX = lightboxAnchorRect.left + (lightboxAnchorRect.width / 2);
    const centerY = lightboxAnchorRect.top + (lightboxAnchorRect.height / 2);
    const translateX = centerX - (viewportWidth / 2);
    const translateY = centerY - (viewportHeight / 2);
    const scaleX = Math.min(1, Math.max(lightboxAnchorRect.width / (viewportWidth * 0.92), 0.08));
    const scaleY = Math.min(1, Math.max(lightboxAnchorRect.height / (viewportHeight * 0.88), 0.08));
    const scale = Math.min(scaleX, scaleY);
    return {
      transform: `translate(${translateX}px, ${translateY}px) scale(${scale})`,
      transformOrigin: "center center",
    };
  }, [lightboxActive, lightboxAnchorRect]);

  useEffect(() => {
    if (!lightboxUrl) return;
    const rafId = window.requestAnimationFrame(() => setLightboxActive(true));
    const onKeyDown = (e) => {
      if (e.key === "Escape") closeLightbox();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.cancelAnimationFrame(rafId);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [closeLightbox, lightboxUrl]);

  useEffect(() => () => {
    if (lightboxCloseTimerRef.current) {
      clearTimeout(lightboxCloseTimerRef.current);
      lightboxCloseTimerRef.current = null;
    }
  }, []);

  useEffect(() => {
    setScenarioVideoOpen(false);
    setScenarioAudioSliceLoading(false);
  }, [scenarioSelected?.sceneId, scenarioSelectedIndex]);

  useEffect(() => {
    if (!scenarioEditor.open) return;
    if (!scenarioScenes.length) return;
    if (scenarioSelectedIndex < 0) {
      const fallbackSceneId = String(scenarioScenes?.[0]?.sceneId || "").trim();
      setScenarioEditor((prev) => ({ ...prev, selected: 0, selectedSceneId: fallbackSceneId }));
    }
  }, [scenarioEditor.open, scenarioScenes, scenarioSelectedIndex]);

  useEffect(() => {
    const selectedSceneId = String(scenarioSelected?.sceneId || "").trim();
    if (!selectedSceneId) return;
    if (scenarioEditor.selected === scenarioSelectedIndex && scenarioEditor.selectedSceneId === selectedSceneId) return;
    setScenarioEditor((prev) => {
      if (prev.selected === scenarioSelectedIndex && prev.selectedSceneId === selectedSceneId) return prev;
      return { ...prev, selected: scenarioSelectedIndex, selectedSceneId };
    });
  }, [scenarioEditor.selected, scenarioEditor.selectedSceneId, scenarioSelected?.sceneId, scenarioSelectedIndex]);

  useEffect(() => {
    if (!scenarioEditor.open) return;
    const node = scenarioItemRefs.current.get(scenarioSelectedIndex);
    if (!node) return;
    try {
      node.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch {
      node.scrollIntoView();
    }
  }, [scenarioEditor.open, scenarioSelectedIndex]);

  useEffect(() => () => {
    if (scenarioVideoScrollRafRef.current) {
      cancelAnimationFrame(scenarioVideoScrollRafRef.current);
      scenarioVideoScrollRafRef.current = 0;
    }
    if (scenarioVideoScrollTimerRef.current) {
      clearTimeout(scenarioVideoScrollTimerRef.current);
      scenarioVideoScrollTimerRef.current = null;
    }
  }, []);

  const updateScenarioScene = useCallback((sceneRef, patch, options = {}) => {
    const explicitTargetNodeId = String(options?.nodeId || "").trim();
    const targetNodeId = explicitTargetNodeId || String(scenarioFlowSourceNode?.id || "").trim();
    if (!targetNodeId) return;
    setNodes((prev) => prev.map((n) => {
      if (n.id !== targetNodeId) return n;
      const scenes = Array.isArray(n?.data?.scenes) ? n.data.scenes : [];
      const resolvedIdx = typeof sceneRef === "string"
        ? scenes.findIndex((scene) => String(scene?.sceneId || "") === String(sceneRef || ""))
        : sceneRef;
      if (!Number.isInteger(resolvedIdx) || resolvedIdx < 0 || !scenes[resolvedIdx]) return n;
      const sceneAtIdx = scenes[resolvedIdx] || {};
      if (CLIP_TRACE_SCENARIO_IMAGE_E2E) {
        console.debug("[SCENARIO SCENE PATCH TARGET RESOLVED]", {
          targetNodeId,
          sceneRef,
          resolvedIdx,
          resolvedSceneId: String(sceneAtIdx?.sceneId || ""),
          patchKeys: Object.keys(patch || {}),
        });
        console.debug("[SCENARIO SCENE PATCH APPLIED]", {
          targetNodeId,
          sceneRef,
          resolvedIdx,
          actualSceneIdAtIdx: String(sceneAtIdx?.sceneId || ""),
          patchKeys: Object.keys(patch || {}),
        });
      }
      const normalizedPatch = normalizeLipSyncSceneStatePatch(sceneAtIdx, patch);
      const nextScenes = scenes.map((s, i) => (i === resolvedIdx ? { ...s, ...normalizedPatch } : s));
      return { ...n, data: { ...n.data, scenes: nextScenes } };
    }));
  }, [scenarioFlowSourceNode?.id, setNodes]);

  const updateScenarioSceneGenerationRuntime = useCallback((sceneIdRaw, patch = {}, options = {}) => {
    const sceneId = String(sceneIdRaw || "").trim();
    const targetNodeId = String(options?.nodeId || scenarioFlowSourceNode?.id || "").trim();
    if (!targetNodeId || !sceneId || !patch || typeof patch !== "object") return;
    if (CLIP_TRACE_SCENARIO_IMAGE_E2E) {
      console.debug("[SCENARIO RUNTIME PATCH TARGET]", {
        targetNodeId,
        sceneId,
        patchKeys: Object.keys(patch || {}),
        hasRuntimePatch: true,
      });
    }
    setNodes((prev) => prev.map((n) => {
      if (n.id !== targetNodeId) return n;
      const currentMap = n?.data?.sceneGeneration && typeof n.data.sceneGeneration === "object" ? n.data.sceneGeneration : {};
      const currentRuntime = currentMap[sceneId] && typeof currentMap[sceneId] === "object" ? currentMap[sceneId] : {};
      return {
        ...n,
        data: {
          ...n.data,
          sceneGeneration: {
            ...currentMap,
            [sceneId]: {
              ...currentRuntime,
              ...patch,
              updatedAt: new Date().toISOString(),
            },
          },
        },
      };
    }));
  }, [scenarioFlowSourceNode?.id, setNodes]);

  const stopScenarioVideoPolling = useCallback((sceneId = "") => {
    const key = String(sceneId || "").trim();
    if (!key) {
      scenarioVideoPollTimersRef.current.forEach((timerId) => clearTimeout(timerId));
      scenarioVideoPollTimersRef.current.clear();
      return;
    }
    const timerId = scenarioVideoPollTimersRef.current.get(key);
    if (timerId) {
      clearTimeout(timerId);
      scenarioVideoPollTimersRef.current.delete(key);
    }
  }, []);

  const persistActiveVideoJob = useCallback((jobsByScene) => {
    const entries = Object.entries(jobsByScene || {}).filter(([, value]) => value?.jobId);
    if (!entries.length) {
      safeDel(VIDEO_JOB_STORE_KEY);
      return;
    }
    safeSet(VIDEO_JOB_STORE_KEY, JSON.stringify(Object.fromEntries(entries)));
  }, [VIDEO_JOB_STORE_KEY]);

  const clearActiveVideoJob = useCallback((sceneId = "", meta = null) => {
    const key = String(sceneId || "").trim();
    if (!key) return;
    const activeMeta = scenarioVideoJobsBySceneRef.current.get(key) || null;
    const activeJobId = String(meta?.jobId || activeMeta?.jobId || "").trim();
    if (activeJobId) scenarioActivePollingJobIdsRef.current.delete(`${key}:${activeJobId}`);
    scenarioVideoJobsBySceneRef.current.delete(key);
    persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
    stopScenarioVideoPolling(key);
    const terminalStatus = String(meta?.status || "").toLowerCase();
    if (terminalStatus) {
      console.info("[SCENARIO VIDEO ACTIVE JOB CLEARED]", {
        scope: "scenario",
        sceneId: key,
        jobId: String(meta?.jobId || activeMeta?.jobId || ""),
        status: terminalStatus,
      });
    }
  }, [persistActiveVideoJob, stopScenarioVideoPolling]);

  const isScenarioVideoJobNotFound = useCallback((payload) => {
    const status = String(payload?.status || "").toLowerCase();
    const code = String(payload?.code || "").toLowerCase();
    const hint = String(payload?.hint || "").toLowerCase();
    return status === "not_found"
      || code === "video_job_not_found"
      || code.includes("job_id_not_found_or_expired")
      || hint.includes("job_id_not_found_or_expired");
  }, []);

  const startScenarioVideoPolling = useCallback((jobMeta) => {
    if (!jobMeta?.jobId || !jobMeta?.sceneId) return;
    const sceneId = String(jobMeta.sceneId || "").trim();
    if (!sceneId) return;
    const now = Date.now();
    const staleTimeoutMs = 20 * 60 * 1000;
    const notFoundRetryLimit = 3;
    const extractScenarioVideoError = (payload = {}) => {
      const code = String(payload?.code || "").trim();
      const hint = String(payload?.hint || "").trim();
      const error = String(payload?.error || "").trim();
      const message = String(payload?.message || "").trim();
      return error || hint || message || code || "video_job_failed";
    };
    const applyScenarioVideoPatch = (patch = {}, context = {}) => {
      const normalizedPatch = patch && typeof patch === "object" ? patch : {};
      const didPatch = Object.keys(normalizedPatch).length > 0;
      if (didPatch) updateScenarioScene(sceneId, normalizedPatch);
      console.info("[SCENARIO VIDEO ERROR PROPAGATION]", {
        sceneId,
        jobId: String(context?.jobId || ""),
        status: String(context?.status || ""),
        code: String(context?.code || ""),
        hint: String(context?.hint || ""),
        message: String(context?.message || ""),
        runtimeUpdated: didPatch,
        patchKeys: Object.keys(normalizedPatch),
      });
      return didPatch;
    };
    const sceneSnapshot = scenarioScenes.find((scene) => String(scene?.sceneId || "") === sceneId) || null;
    const runtimeStoryboardRevision = String(
      scenarioFlowSourceNode?.data?.storyboardRevision
      || jobMeta?.storyboardRevision
      || ""
    ).trim();
    const runtimeStoryboardSignature = String(
      scenarioFlowSourceNode?.data?.storyboardSignature
      || buildSceneSignature(scenarioScenes, "scene")
      || jobMeta?.storyboardSignature
      || ""
    ).trim();
    const runtimeSceneSignature = String(
      buildScenarioScenePackageSignature(sceneSnapshot || {})
      || jobMeta?.sceneSignature
      || ""
    ).trim();
    const startMeta = {
      ...jobMeta,
      sceneId,
      provider: String(jobMeta?.provider || sceneSnapshot?.sceneRenderProvider || "comfy_remote").trim() || "comfy_remote",
      workflowKey: normalizeScenarioWorkflowKeyForProduction(
        String(jobMeta?.workflowKey || sceneSnapshot?.resolvedWorkflowKey || resolveScenarioWorkflowKey(sceneSnapshot || {}) || "").trim()
      ),
      modelKey: String(jobMeta?.modelKey || sceneSnapshot?.resolvedModelKey || resolveScenarioExplicitModelKey(sceneSnapshot || {}) || "").trim(),
      audioSensitive: Boolean(jobMeta?.audioSensitive ?? normalizeScenarioWorkflowKeyForProduction(sceneSnapshot?.resolvedWorkflowKey || resolveScenarioWorkflowKey(sceneSnapshot || {})) === "lip_sync_music"),
      continuation: Boolean(jobMeta?.continuation ?? sceneSnapshot?.requiresContinuation ?? sceneSnapshot?.continuationFromPrevious),
      continuationSourceSceneId: String(jobMeta?.continuationSourceSceneId || sceneSnapshot?.continuationSourceSceneId || "").trim(),
      continuationSourceAssetType: String(jobMeta?.continuationSourceAssetType || sceneSnapshot?.continuationSourceAssetType || "").trim(),
      renderMode: String(jobMeta?.renderMode || sceneSnapshot?.renderMode || "").trim(),
      storyboardRevision: runtimeStoryboardRevision,
      storyboardSignature: runtimeStoryboardSignature,
      sceneSignature: runtimeSceneSignature,
      startedAt: Number(jobMeta?.startedAt) || now,
      updatedAt: Number(jobMeta?.updatedAt) || now,
      status: String(jobMeta?.status || "queued").toLowerCase(),
    };
    const pollingKey = `${sceneId}:${String(startMeta.jobId || "")}`;
    if (scenarioActivePollingJobIdsRef.current.has(pollingKey)) {
      console.info("[SCENARIO VIDEO RESTORE SKIPPED DUPLICATE]", {
        reason: "polling_already_active",
        sceneId,
        jobId: String(startMeta.jobId || ""),
      });
      return;
    }
    const prevMeta = scenarioVideoJobsBySceneRef.current.get(sceneId);
    const existingTimerId = scenarioVideoPollTimersRef.current.get(sceneId);
    if (prevMeta?.jobId && String(prevMeta.jobId) === String(startMeta.jobId) && existingTimerId) {
      console.info("[SCENARIO VIDEO RESTORE SKIPPED DUPLICATE]", {
        reason: "same_scene_job_with_timer",
        sceneId,
        jobId: String(startMeta.jobId || ""),
      });
      scenarioActivePollingJobIdsRef.current.add(pollingKey);
      return;
    }
    if (prevMeta?.jobId && String(prevMeta.jobId) !== String(startMeta.jobId)) {
      scenarioActivePollingJobIdsRef.current.delete(`${sceneId}:${String(prevMeta.jobId || "")}`);
      console.info("[CLIP TRACE] active job replaced", {
        scope: "scenario",
        sceneId,
        oldJobId: String(prevMeta.jobId || ""),
        newJobId: String(startMeta.jobId || ""),
      });
    }
    scenarioActivePollingJobIdsRef.current.add(pollingKey);
    scenarioVideoJobsBySceneRef.current.set(sceneId, startMeta);
    persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
    applyScenarioVideoPatch({
      videoJobId: startMeta.jobId,
      videoStatus: startMeta.status,
      videoError: "",
    }, { jobId: String(startMeta.jobId || ""), status: String(startMeta.status || "queued"), message: "polling_started" });
    stopScenarioVideoPolling(sceneId);

    console.info("[CLIP VIDEO POLLING START]", {
      sceneId,
      jobId: String(startMeta.jobId || ""),
      provider: String(startMeta.provider || ""),
      workflowKey: String(startMeta.workflowKey || ""),
      modelKey: String(startMeta.modelKey || ""),
      audioSensitive: Boolean(startMeta.audioSensitive),
      continuation: Boolean(startMeta.continuation),
      continuationSourceSceneId: String(startMeta.continuationSourceSceneId || ""),
      continuationSourceAssetType: String(startMeta.continuationSourceAssetType || ""),
      providerJobId: String(startMeta.providerJobId || ""),
      renderMode: String(startMeta.renderMode || ""),
    });

    const scheduleScenarioPoll = (delayMs, reason) => {
      console.info("[SCENARIO VIDEO POLL]", {
        action: "schedule",
        reason,
        sceneId,
        jobId: String(startMeta.jobId || ""),
        delayMs,
      });
      console.info("[VIDEO POLLING START]", {
        scope: "scenario",
        reason,
        sceneId,
        jobId: String(startMeta.jobId || ""),
        delayMs,
      });
      scenarioVideoPollTimersRef.current.set(sceneId, setTimeout(tick, delayMs));
    };

    const tick = async () => {
      const currentMeta = scenarioVideoJobsBySceneRef.current.get(sceneId) || {};
      console.info("[CLIP VIDEO POLLING TICK]", {
        sceneId,
        jobId: String(currentMeta?.jobId || ""),
      });
      console.info("[VIDEO POLLING TICK]", {
        scope: "scenario",
        sceneId,
        jobId: String(currentMeta?.jobId || ""),
        status: String(currentMeta?.status || ""),
      });
      const lastUpdatedAt = Number(currentMeta?.updatedAt) || Number(currentMeta?.startedAt) || 0;
      const liveScene = scenarioScenes.find((scene) => String(scene?.sceneId || "") === sceneId) || null;
      const liveStoryboardRevision = String(scenarioFlowSourceNode?.data?.storyboardRevision || "").trim();
      const liveStoryboardSignature = String(
        scenarioFlowSourceNode?.data?.storyboardSignature
        || buildSceneSignature(scenarioScenes, "scene")
        || ""
      ).trim();
      const liveSceneSignature = String(buildScenarioScenePackageSignature(liveScene || {}) || "").trim();
      const bindingMismatchByRun = (
        !!String(currentMeta?.storyboardRevision || "").trim()
        && !!liveStoryboardRevision
        && String(currentMeta?.storyboardRevision || "").trim() !== liveStoryboardRevision
      ) || (
        !!String(currentMeta?.storyboardSignature || "").trim()
        && !!liveStoryboardSignature
        && String(currentMeta?.storyboardSignature || "").trim() !== liveStoryboardSignature
      );
      const bindingMismatchByScene = !!String(currentMeta?.sceneSignature || "").trim()
        && !!liveSceneSignature
        && String(currentMeta?.sceneSignature || "").trim() !== liveSceneSignature;
      if (bindingMismatchByRun || bindingMismatchByScene) {
        console.info("[SCENARIO VIDEO POLL STALE IGNORED]", {
          sceneId,
          jobId: String(currentMeta?.jobId || ""),
          reasonIgnored: bindingMismatchByRun ? "storyboard_run_binding_mismatch" : "scene_signature_mismatch",
          persistedStoryboardRevision: String(currentMeta?.storyboardRevision || ""),
          runtimeStoryboardRevision: liveStoryboardRevision,
          persistedStoryboardSignature: String(currentMeta?.storyboardSignature || ""),
          runtimeStoryboardSignature: liveStoryboardSignature,
          persistedSceneSignature: String(currentMeta?.sceneSignature || ""),
          runtimeSceneSignature: liveSceneSignature,
        });
        clearActiveVideoJob(sceneId, { status: "stale_binding", jobId: String(currentMeta?.jobId || "") });
        return;
      }
      if (lastUpdatedAt > 0 && (Date.now() - lastUpdatedAt) > staleTimeoutMs) {
        applyScenarioVideoPatch({
          videoStatus: "error",
          videoError: "video_job_stale_timeout",
        }, { jobId: String(currentMeta?.jobId || ""), status: "error", code: "video_job_stale_timeout", message: "polling_stale_timeout" });
        clearActiveVideoJob(sceneId);
        return;
      }
      try {
        const activeMeta = scenarioVideoJobsBySceneRef.current.get(sceneId);
        if (!activeMeta?.jobId) return;
        console.info("[SCENARIO VIDEO POLL REQUEST]", {
          sceneId,
          jobId: String(activeMeta.jobId || ""),
          endpoint: `/api/clip/video/status/${encodeURIComponent(activeMeta.jobId)}`,
        });
        const out = await fetchJson(`/api/clip/video/status/${encodeURIComponent(activeMeta.jobId)}`, {
          method: "GET",
          timeoutMs: VIDEO_STATUS_TIMEOUT_MS,
        });
        console.info("[SCENARIO VIDEO POLL RESPONSE]", {
          sceneId,
          jobId: String(activeMeta.jobId || ""),
          ok: Boolean(out?.ok),
          status: String(out?.status || ""),
          hasVideoUrl: Boolean(String(out?.videoUrl || "").trim()),
          error: String(out?.error || out?.hint || out?.code || ""),
        });
        console.info("[SCENARIO VIDEO POLL]", {
          action: "response",
          sceneId,
          jobId: String(activeMeta.jobId || ""),
          ok: Boolean(out?.ok),
          status: String(out?.status || ""),
          code: String(out?.code || ""),
          hint: String(out?.hint || ""),
          message: String(out?.error || out?.message || ""),
        });
        const responseJobId = String(out?.jobId || activeMeta?.jobId || "").trim();
        const latestMeta = scenarioVideoJobsBySceneRef.current.get(sceneId) || {};
        const latestJobId = String(latestMeta?.jobId || "").trim();
        if (!latestJobId || !responseJobId || latestJobId !== responseJobId || String(activeMeta?.jobId || "").trim() !== responseJobId) {
          console.info("[SCENARIO VIDEO POLL STALE IGNORED]", {
            sceneId,
            jobId: String(activeMeta?.jobId || ""),
            responseJobId,
            latestJobId,
            providerJobId: String(out?.providerJobId || latestMeta?.providerJobId || ""),
            workflowKey: String(latestMeta?.workflowKey || ""),
            renderMode: String(latestMeta?.renderMode || ""),
            reasonIgnored: !latestJobId ? "missing_active_binding" : (latestJobId !== responseJobId ? "scene_job_binding_mismatch" : "request_job_changed_during_poll"),
          });
          return;
        }
        const status = String(out?.status || "").toLowerCase() || "running";
        const nextMeta = {
          ...activeMeta,
          providerJobId: String(out?.providerJobId || activeMeta?.providerJobId || ""),
          sceneId,
          status,
          updatedAt: Date.now(),
        };
        scenarioVideoJobsBySceneRef.current.set(sceneId, nextMeta);
        persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));

        const prevNotFoundCount = Number(activeMeta?.notFoundCount) || 0;
        if (!out?.ok && isScenarioVideoJobNotFound(out)) {
          const nextNotFoundCount = prevNotFoundCount + 1;
          const toleratedMeta = { ...nextMeta, notFoundCount: nextNotFoundCount, status: "running" };
          scenarioVideoJobsBySceneRef.current.set(sceneId, toleratedMeta);
          persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
          if (nextNotFoundCount < notFoundRetryLimit) {
            applyScenarioVideoPatch({
              videoStatus: "running",
              videoError: "",
              videoJobId: toleratedMeta.jobId,
            }, { jobId: String(toleratedMeta?.jobId || ""), status: "running", code: String(out?.code || ""), hint: String(out?.hint || ""), message: "status_not_found_retry" });
            scheduleScenarioPoll(2200, "status_not_found_retry");
            return;
          }
          applyScenarioVideoPatch({
            videoStatus: "not_found",
            videoError: String(out?.hint || out?.code || "video_job_not_found"),
            videoJobId: toleratedMeta.jobId,
            videoPanelActivated: false,
          }, { jobId: String(toleratedMeta?.jobId || ""), status: "not_found", code: String(out?.code || ""), hint: String(out?.hint || ""), message: String(out?.error || out?.message || "video_job_not_found") });
          console.info("[SCENARIO VIDEO UI RESET]", { sceneId, status: "not_found", videoPanelActivatedAfterApply: false });
          clearActiveVideoJob(sceneId, { status: "not_found", jobId: toleratedMeta.jobId });
          return;
        }
        if (!out?.ok) throw new Error(extractScenarioVideoError(out));

        const settledMeta = { ...nextMeta, notFoundCount: 0 };
        scenarioVideoJobsBySceneRef.current.set(sceneId, settledMeta);
        persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));

        if (status === "not_found") {
          const nextNotFoundCount = prevNotFoundCount + 1;
          const toleratedMeta = { ...settledMeta, notFoundCount: nextNotFoundCount, status: "running" };
          scenarioVideoJobsBySceneRef.current.set(sceneId, toleratedMeta);
          persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
          if (nextNotFoundCount < notFoundRetryLimit) {
            applyScenarioVideoPatch({
              videoStatus: "running",
              videoJobId: toleratedMeta.jobId,
              videoError: "",
            }, { jobId: String(toleratedMeta?.jobId || ""), status: "running", message: "status_not_found_retry" });
            scheduleScenarioPoll(2200, "status_not_found_retry");
            return;
          }
          applyScenarioVideoPatch({
            videoStatus: "not_found",
            videoJobId: toleratedMeta.jobId,
            videoError: String(out?.error || out?.hint || "video_job_not_found"),
            videoPanelActivated: false,
          }, { jobId: String(toleratedMeta?.jobId || ""), status: "not_found", code: String(out?.code || ""), hint: String(out?.hint || ""), message: String(out?.error || out?.message || "video_job_not_found") });
          console.info("[SCENARIO VIDEO UI RESET]", { sceneId, status: "not_found", videoPanelActivatedAfterApply: false });
          clearActiveVideoJob(sceneId, { status: "not_found", jobId: toleratedMeta.jobId });
          return;
        }

        applyScenarioVideoPatch({
          videoStatus: status,
          videoJobId: settledMeta.jobId,
          videoError: status === "error" || status === "stopped" ? extractScenarioVideoError(out) : "",
        }, {
          jobId: String(settledMeta?.jobId || ""),
          status,
          code: String(out?.code || ""),
          hint: String(out?.hint || ""),
          message: status === "error" || status === "stopped" ? extractScenarioVideoError(out) : "status_update",
        });
        console.info("[SCENARIO VIDEO STATUS APPLIED]", {
          sceneId,
          jobId: String(settledMeta?.jobId || ""),
          providerJobId: String(settledMeta?.providerJobId || ""),
          workflowKey: String(settledMeta?.workflowKey || ""),
          renderMode: String(settledMeta?.renderMode || ""),
          status,
          videoUrl: String(out?.videoUrl || ""),
          error: status === "error" || status === "stopped" ? String(out?.error || out?.hint || "video_job_failed") : "",
        });
        console.info("[SCENARIO VIDEO RESULT]", {
          sceneId,
          ok: status === "done",
          videoUrl: String(out?.videoUrl || ""),
          status,
          provider: String(out?.provider || settledMeta?.provider || ""),
        });
        console.info("[VIDEO STATUS APPLIED]", {
          scope: "scenario",
          sceneId,
          jobId: String(settledMeta?.jobId || ""),
          status,
          ok: !!out?.ok,
        });

        if (status === "done") {
          applyScenarioVideoPatch({
            videoUrl: String(out?.videoUrl || ""),
            mode: String(out?.mode || ""),
            model: String(out?.model || ""),
            requestedDurationSec: normalizeDurationSec(out?.requestedDurationSec),
            providerDurationSec: normalizeDurationSec(out?.providerDurationSec),
            videoRequestedPromptPreview: String(out?.requestedPromptPreview || out?.debug?.requestedPromptPreview || ""),
            videoEffectivePromptPreview: String(out?.effectivePromptPreview || out?.debug?.effectivePromptPreview || ""),
            videoEffectivePromptLength: Number(out?.effectivePromptLength || out?.debug?.effectivePromptLength || 0) || 0,
            videoPromptPatchedNodeIds: Array.isArray(out?.promptPatchedNodeIds)
              ? out.promptPatchedNodeIds
              : (Array.isArray(out?.debug?.promptPatchedNodeIds) ? out.debug.promptPatchedNodeIds : []),
            videoStatus: "done",
            videoError: "",
            videoJobId: settledMeta.jobId,
            videoPanelActivated: false,
          }, { jobId: String(settledMeta?.jobId || ""), status: "done", message: "video_ready" });
          console.info("[SCENARIO VIDEO UI RESET]", { sceneId, status: "done", videoPanelActivatedAfterApply: false });
          clearActiveVideoJob(sceneId, { status: "done", jobId: settledMeta.jobId });
          return;
        }

        if (status === "error" || status === "stopped" || status === "not_found") {
          console.error("[SCENARIO VIDEO ERROR PROPAGATION]", {
            sceneId,
            jobId: String(settledMeta?.jobId || ""),
            status,
            code: String(out?.code || ""),
            hint: String(out?.hint || ""),
            message: extractScenarioVideoError(out),
          });
          applyScenarioVideoPatch({
            videoPanelActivated: false,
            videoStatus: "error",
            videoError: extractScenarioVideoError(out),
            videoJobId: settledMeta.jobId,
          }, {
            jobId: String(settledMeta?.jobId || ""),
            status,
            code: String(out?.code || ""),
            hint: String(out?.hint || ""),
            message: extractScenarioVideoError(out),
          });
          console.info("[SCENARIO VIDEO UI RESET]", { sceneId, status, videoPanelActivatedAfterApply: false });
          clearActiveVideoJob(sceneId, { status, jobId: settledMeta.jobId });
          return;
        }

        scheduleScenarioPoll(1800, "status_running");
      } catch (e) {
        const errMsg = String(e?.message || "").toLowerCase();
        if (errMsg.includes("job_id_not_found_or_expired")) {
          const activeMeta = scenarioVideoJobsBySceneRef.current.get(sceneId) || {};
          const nextNotFoundCount = (Number(activeMeta?.notFoundCount) || 0) + 1;
          const toleratedMeta = { ...activeMeta, notFoundCount: nextNotFoundCount, status: "running", updatedAt: Date.now() };
          scenarioVideoJobsBySceneRef.current.set(sceneId, toleratedMeta);
          persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
          if (nextNotFoundCount < notFoundRetryLimit) {
            applyScenarioVideoPatch({
              videoStatus: "running",
              videoError: "",
              videoJobId: String(toleratedMeta?.jobId || ""),
            }, { jobId: String(toleratedMeta?.jobId || ""), status: "running", message: "exception_not_found_retry" });
            scheduleScenarioPoll(2400, "exception_not_found_retry");
            return;
          }
          applyScenarioVideoPatch({
            videoStatus: "not_found",
            videoError: String(e?.message || e),
            videoJobId: String(toleratedMeta?.jobId || ""),
            videoPanelActivated: false,
          }, { jobId: String(toleratedMeta?.jobId || ""), status: "not_found", code: "video_job_not_found", message: String(e?.message || e) });
          console.info("[SCENARIO VIDEO UI RESET]", { sceneId, status: "not_found", videoPanelActivatedAfterApply: false });
          clearActiveVideoJob(sceneId, { status: "not_found", jobId: toleratedMeta.jobId });
          return;
        }
        const propagatedError = String(e?.message || e || "video_poll_failed");
        console.error("[SCENARIO VIDEO ERROR PROPAGATION]", {
          sceneId,
          jobId: String((scenarioVideoJobsBySceneRef.current.get(sceneId) || {})?.jobId || ""),
          status: "error",
          message: propagatedError,
        });
        applyScenarioVideoPatch({
          videoStatus: "error",
          videoError: propagatedError,
          videoPanelActivated: false,
        }, { jobId: String((scenarioVideoJobsBySceneRef.current.get(sceneId) || {})?.jobId || ""), status: "error", message: propagatedError });
        clearActiveVideoJob(sceneId, { status: "error", jobId: String((scenarioVideoJobsBySceneRef.current.get(sceneId) || {})?.jobId || "") });
      }
    };

    scheduleScenarioPoll(250, "initial_after_start");
  }, [clearActiveVideoJob, isScenarioVideoJobNotFound, persistActiveVideoJob, scenarioFlowSourceNode?.data?.storyboardRevision, scenarioFlowSourceNode?.data?.storyboardSignature, scenarioScenes, stopScenarioVideoPolling, updateScenarioScene]);

  useEffect(() => () => {
    scenarioActivePollingJobIdsRef.current.clear();
    stopScenarioVideoPolling();
  }, [stopScenarioVideoPolling]);

  useEffect(() => {
    const restoreToken = `${accountKey}:${VIDEO_JOB_STORE_KEY}`;
    if (restoredScenarioVideoJobsRef.current.has(restoreToken)) return;
    const raw = safeGet(VIDEO_JOB_STORE_KEY);
    if (!raw) {
      restoredScenarioVideoJobsRef.current.add(restoreToken);
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      const entries = parsed?.jobId
        ? [[String(parsed?.sceneId || ""), parsed]]
        : (parsed && typeof parsed === "object" ? Object.entries(parsed) : []);
      const staleTimeoutMs = 20 * 60 * 1000;
      console.info("[SCENARIO VIDEO RESTORE]", {
        accountKey,
        VIDEO_JOB_STORE_KEY,
        restoredSceneIds: entries.map(([sceneId]) => String(sceneId || "")).filter(Boolean),
        restoredJobs: entries.length,
      });
      const nextPersisted = {};
      entries.forEach(([sceneId, meta]) => {
        if (!meta?.jobId) return;
        const normalizedSceneId = String(sceneId || "");
        const idx = scenarioScenes.findIndex((x) => String(x?.sceneId || "") === normalizedSceneId);
        const sceneNow = idx >= 0 ? scenarioScenes[idx] : null;
        const runtimeStoryboardRevision = String(scenarioFlowSourceNode?.data?.storyboardRevision || "").trim();
        const runtimeStoryboardSignature = String(
          scenarioFlowSourceNode?.data?.storyboardSignature
          || buildSceneSignature(scenarioScenes, "scene")
          || ""
        ).trim();
        const runtimeSceneSignature = String(buildScenarioScenePackageSignature(sceneNow || {}) || "").trim();
        const persistedStoryboardRevision = String(meta?.storyboardRevision || "").trim();
        const persistedStoryboardSignature = String(meta?.storyboardSignature || "").trim();
        const persistedSceneSignature = String(meta?.sceneSignature || "").trim();
        const mismatchByRun = (
          persistedStoryboardRevision
          && runtimeStoryboardRevision
          && persistedStoryboardRevision !== runtimeStoryboardRevision
        ) || (
          persistedStoryboardSignature
          && runtimeStoryboardSignature
          && persistedStoryboardSignature !== runtimeStoryboardSignature
        );
        const mismatchByScene = persistedSceneSignature && runtimeSceneSignature && persistedSceneSignature !== runtimeSceneSignature;
        if (mismatchByRun || mismatchByScene) {
          console.info("[SCENARIO VIDEO RESTORE SKIPPED DUPLICATE]", {
            reason: mismatchByRun ? "storyboard_run_binding_mismatch" : "scene_signature_mismatch",
            sceneId: normalizedSceneId,
            jobId: String(meta?.jobId || ""),
          });
          return;
        }
        const sceneVideoUrl = String(sceneNow?.videoUrl || "").trim();
        const sceneVideoJobId = String(sceneNow?.videoJobId || "").trim();
        const sceneVideoStatus = String(sceneNow?.videoStatus || "").toLowerCase();
        const persistedJobId = String(meta?.jobId || "").trim();
        const persistedStatus = String(meta?.status || "").toLowerCase();
        const isSceneTerminal = sceneVideoStatus === "done" || sceneVideoStatus === "error" || sceneVideoStatus === "stopped" || sceneVideoStatus === "not_found";
        const isPersistedTerminal = persistedStatus === "done" || persistedStatus === "error" || persistedStatus === "stopped" || persistedStatus === "not_found";
        const activeKey = `${normalizedSceneId}:${persistedJobId}`;
        const existingMeta = scenarioVideoJobsBySceneRef.current.get(normalizedSceneId);
        if (
          scenarioActivePollingJobIdsRef.current.has(activeKey)
          || (existingMeta?.jobId && String(existingMeta.jobId) === persistedJobId)
        ) {
          console.info("[SCENARIO VIDEO RESTORE SKIPPED DUPLICATE]", {
            reason: "already_attached",
            sceneId: normalizedSceneId,
            jobId: persistedJobId,
          });
          nextPersisted[normalizedSceneId] = existingMeta || meta;
          return;
        }
        if (isPersistedTerminal || isSceneTerminal || sceneVideoUrl || (sceneVideoJobId && sceneVideoJobId !== persistedJobId)) {
          console.info("[SCENARIO VIDEO RESTORE SKIPPED DUPLICATE]", {
            reason: isPersistedTerminal ? "persisted_terminal" : (isSceneTerminal ? "scene_terminal" : "stale_snapshot"),
            sceneId: normalizedSceneId,
            jobId: persistedJobId,
          });
          return;
        }
        const lastUpdatedAt = Number(meta?.updatedAt) || Number(meta?.startedAt) || 0;
        if (lastUpdatedAt > 0 && (Date.now() - lastUpdatedAt) > staleTimeoutMs) {
          updateScenarioScene(idx, { videoStatus: "error", videoError: "video_job_stale_timeout" });
          return;
        }
        nextPersisted[normalizedSceneId] = meta;
        console.info("[SCENARIO VIDEO RESTORE]", {
          event: "active_job_restored",
          scope: "scenario",
          sceneId: normalizedSceneId,
          jobId: persistedJobId,
        });
        startScenarioVideoPolling({
          ...meta,
          sceneId: normalizedSceneId,
          storyboardRevision: runtimeStoryboardRevision,
          storyboardSignature: runtimeStoryboardSignature,
          sceneSignature: runtimeSceneSignature,
      workflowKey: normalizeScenarioWorkflowKeyForProduction(
        String(meta?.workflowKey || sceneNow?.resolvedWorkflowKey || resolveScenarioWorkflowKey(sceneNow || {}) || "")
      ),
          modelKey: String(meta?.modelKey || sceneNow?.resolvedModelKey || resolveScenarioExplicitModelKey(sceneNow || {}) || ""),
          provider: String(meta?.provider || sceneNow?.sceneRenderProvider || "comfy_remote"),
          audioSensitive: Boolean(meta?.audioSensitive ?? normalizeScenarioWorkflowKeyForProduction(sceneNow?.resolvedWorkflowKey || resolveScenarioWorkflowKey(sceneNow || {})) === "lip_sync_music"),
          continuation: Boolean(meta?.continuation ?? sceneNow?.requiresContinuation ?? sceneNow?.continuationFromPrevious),
          continuationSourceSceneId: String(meta?.continuationSourceSceneId || sceneNow?.continuationSourceSceneId || ""),
          continuationSourceAssetType: String(meta?.continuationSourceAssetType || sceneNow?.continuationSourceAssetType || ""),
          renderMode: String(meta?.renderMode || sceneNow?.renderMode || ""),
        });
      });
      persistActiveVideoJob(nextPersisted);
    } catch {
      safeDel(VIDEO_JOB_STORE_KEY);
    } finally {
      restoredScenarioVideoJobsRef.current.add(restoreToken);
    }
  }, [VIDEO_JOB_STORE_KEY, accountKey, persistActiveVideoJob, scenarioFlowSourceNode?.data?.storyboardRevision, scenarioFlowSourceNode?.data?.storyboardSignature, scenarioScenes, startScenarioVideoPolling, updateScenarioScene]);

  const updateComfyScene = useCallback((idx, patch) => {
    if (!comfyNode?.id || idx < 0) return;
    setNodes((prev) => prev.map((n) => {
      if (n.id !== comfyNode.id) return n;
      const scenes = Array.isArray(n?.data?.mockScenes) ? n.data.mockScenes : [];
      if (!scenes[idx]) return n;
      const sceneAtIdx = scenes[idx] || {};
      const normalizedPatch = normalizeLipSyncSceneStatePatch(sceneAtIdx, patch);
      const nextScenes = scenes.map((scene, sceneIdx) => (sceneIdx === idx ? { ...scene, ...normalizedPatch } : scene));
      return { ...n, data: { ...n.data, mockScenes: nextScenes } };
    }));
  }, [comfyNode?.id, setNodes]);

  const getComfySceneSnapshot = useCallback((idx) => {
    if (!comfyNode?.id || idx < 0) return null;
    const nodeNow = (nodesRef.current || []).find((n) => n.id === comfyNode.id);
    const scenesNow = Array.isArray(nodeNow?.data?.mockScenes) ? nodeNow.data.mockScenes : [];
    const sceneNow = scenesNow[idx];
    return sceneNow ? normalizeComfyScenePrompts(sceneNow) : null;
  }, [comfyNode?.id]);

  const syncComfyPrompt = useCallback(async ({ idx, promptType, force = false, ruTextOverride = null } = {}) => {
    if (idx < 0) return null;
    const isImage = promptType === "image";
    const ruField = isImage ? "imagePromptRu" : "videoPromptRu";
    const enField = isImage ? "imagePromptEn" : "videoPromptEn";
    const statusField = isImage ? "imagePromptSyncStatus" : "videoPromptSyncStatus";
    const errorField = isImage ? "imagePromptSyncError" : "videoPromptSyncError";
    const promptField = isImage ? "imagePrompt" : "videoPrompt";
    const snapshot = getComfySceneSnapshot(idx);
    if (!snapshot) return null;
    const ruText = String((ruTextOverride ?? snapshot?.[ruField]) || "").trim();
    const currentStatus = String(snapshot?.[statusField] || "");
    const currentEnText = String(snapshot?.[enField] || "").trim();

    if (!ruText) {
      if (currentEnText) return currentEnText;
      const emptyError = isImage ? "Пустой imagePromptRu" : "Пустой videoPromptRu";
      updateComfyScene(idx, { [statusField]: PROMPT_SYNC_STATUS.syncError, [errorField]: emptyError });
      setComfyPromptSyncError(emptyError);
      throw new Error(emptyError);
    }

    if (!force && currentStatus === PROMPT_SYNC_STATUS.synced && currentEnText) {
      return currentEnText;
    }

    const syncKey = `${idx}:${promptType}`;
    if (comfyPromptSyncInFlightRef.current.has(syncKey)) {
      return comfyPromptSyncInFlightRef.current.get(syncKey);
    }

    const syncPromise = (async () => {
      updateComfyScene(idx, { [statusField]: PROMPT_SYNC_STATUS.syncing, [errorField]: "" });
      setComfyPromptSyncError("");
      try {
        const out = await fetchJson('/api/clip/comfy/prompt-sync', {
          method: 'POST',
          body: {
            sourceText: ruText,
            sourceLang: 'ru',
            targetLang: 'en',
            promptType,
            stylePreset: String(comfyNode?.data?.stylePreset || 'realism'),
            mode: String(comfyNode?.data?.mode || 'clip'),
            sceneContext: {
              sceneId: String(snapshot?.sceneId || ''),
              title: String(snapshot?.title || ''),
              continuity: String(snapshot?.continuity || ''),
              sceneNarrativeStep: String(snapshot?.sceneNarrativeStep || ''),
              sceneGoal: String(snapshot?.sceneGoal || ''),
            },
          },
        });
        const translated = String(out?.normalizedPrompt || out?.translatedPrompt || "").trim();
        if (!out?.ok || !translated) throw new Error(out?.error || out?.hint || 'prompt_sync_failed');
        updateComfyScene(idx, {
          [enField]: translated,
          [promptField]: translated,
          [statusField]: PROMPT_SYNC_STATUS.synced,
          [errorField]: "",
          enPromptPresent: { ...(snapshot?.enPromptPresent || {}), [isImage ? 'image' : 'video']: Boolean(translated) },
          promptLanguageStatus: { ...(snapshot?.promptLanguageStatus || {}), [isImage ? 'image' : 'video']: String((ruText || '').trim()) ? 'ru_en_present' : (translated ? 'ru_missing_en_fallback' : 'missing_both') },
          ruPromptMissing: { ...(snapshot?.ruPromptMissing || {}), [isImage ? 'image' : 'video']: !String((ruText || '').trim()) },
        });
        return translated;
      } catch (e) {
        const message = String(e?.message || e);
        updateComfyScene(idx, { [statusField]: PROMPT_SYNC_STATUS.syncError, [errorField]: message });
        setComfyPromptSyncError(message);
        throw e;
      } finally {
        comfyPromptSyncInFlightRef.current.delete(syncKey);
      }
    })();

    comfyPromptSyncInFlightRef.current.set(syncKey, syncPromise);
    return await syncPromise;
  }, [comfyNode?.data?.mode, comfyNode?.data?.stylePreset, getComfySceneSnapshot, updateComfyScene]);

  const ensureComfyPromptSynced = useCallback(async ({ idx, promptType } = {}) => {
    if (idx < 0) return "";
    const isImage = promptType === "image";
    const statusField = isImage ? "imagePromptSyncStatus" : "videoPromptSyncStatus";
    const enField = isImage ? "imagePromptEn" : "videoPromptEn";
    const snapshot = getComfySceneSnapshot(idx);
    if (!snapshot) return "";
    const status = String(snapshot?.[statusField] || "");
    const enText = String(snapshot?.[enField] || "").trim();
    if (status === PROMPT_SYNC_STATUS.syncing) {
      const key = `${idx}:${promptType}`;
      const inflight = comfyPromptSyncInFlightRef.current.get(key);
      if (inflight) return await inflight;
      throw new Error("Синхронизация prompt уже выполняется");
    }
    if (enText) return enText;
    return await syncComfyPrompt({ idx, promptType, force: true });
  }, [getComfySceneSnapshot, syncComfyPrompt]);

  const scheduleComfyPromptSync = useCallback(({ idx, promptType, ruText }) => {
    if (idx < 0) return;
    const key = `${idx}:${promptType}`;
    const existingTimer = comfyPromptSyncTimersRef.current.get(key);
    if (existingTimer) clearTimeout(existingTimer);
    const timerId = setTimeout(() => {
      comfyPromptSyncTimersRef.current.delete(key);
      syncComfyPrompt({ idx, promptType, force: true, ruTextOverride: ruText }).catch((e) => {
        console.error(e);
      });
    }, 650);
    comfyPromptSyncTimersRef.current.set(key, timerId);
  }, [syncComfyPrompt]);

  useEffect(() => () => {
    comfyPromptSyncTimersRef.current.forEach((timerId) => clearTimeout(timerId));
    comfyPromptSyncTimersRef.current.clear();
    comfyPromptSyncInFlightRef.current.clear();
  }, []);

  const stopComfyVideoPolling = useCallback((sceneId = "") => {
    const key = String(sceneId || "").trim();
    if (!key) {
      comfyVideoPollTimersRef.current.forEach((timerId) => clearTimeout(timerId));
      comfyVideoPollTimersRef.current.clear();
      return;
    }
    const timerId = comfyVideoPollTimersRef.current.get(key);
    if (timerId) {
      clearTimeout(timerId);
      comfyVideoPollTimersRef.current.delete(key);
      console.info("[CLIP TRACE] polling stopped", {
        scope: "comfy",
        sceneId: key,
        reason: "timer_cleared",
      });
    }
  }, []);

  const persistActiveComfyVideoJob = useCallback((jobsByScene) => {
    const entries = Object.entries(jobsByScene || {}).filter(([, value]) => value?.jobId);
    if (!entries.length) {
      safeDel(COMFY_VIDEO_JOB_STORE_KEY);
      return;
    }
    safeSet(COMFY_VIDEO_JOB_STORE_KEY, JSON.stringify(Object.fromEntries(entries)));
  }, [COMFY_VIDEO_JOB_STORE_KEY]);

  const comfyScenesRef = useRef(comfyScenes);
  useEffect(() => {
    comfyScenesRef.current = comfyScenes;
  }, [comfyScenes]);
  const activePollingJobsRef = useRef(new Set());
  const restoredComfyVideoJobsRef = useRef(new Set());
  const comfyRestoreRetryTimersRef = useRef(new Map());
  const comfyRestoreRetryCountsRef = useRef(new Map());
  const [comfyRestoreRevision, setComfyRestoreRevision] = useState(0);

  const scheduleComfyRestoreRetry = useCallback((sceneId = "", reason = "scene_missing") => {
    const normalizedSceneId = String(sceneId || "").trim();
    if (!normalizedSceneId) return;
    if (comfyRestoreRetryTimersRef.current.has(normalizedSceneId)) return;
    const attempt = (Number(comfyRestoreRetryCountsRef.current.get(normalizedSceneId)) || 0) + 1;
    comfyRestoreRetryCountsRef.current.set(normalizedSceneId, attempt);
    if (attempt > 8) {
      console.warn("[VIDEO RESTORE] dropped stale job reason=restore_retry_limit", {
        sceneId: normalizedSceneId,
        reason,
        attempt,
      });
      return;
    }
    const delayMs = Math.min(4000, 500 * attempt);
    console.info("[VIDEO RESTORE] retry scheduled", {
      sceneId: normalizedSceneId,
      reason,
      attempt,
      delayMs,
    });
    const timerId = setTimeout(() => {
      comfyRestoreRetryTimersRef.current.delete(normalizedSceneId);
      setComfyRestoreRevision((value) => value + 1);
    }, delayMs);
    comfyRestoreRetryTimersRef.current.set(normalizedSceneId, timerId);
  }, []);

  const clearComfyRestoreRetry = useCallback((sceneId = "") => {
    const normalizedSceneId = String(sceneId || "").trim();
    if (!normalizedSceneId) return;
    const timerId = comfyRestoreRetryTimersRef.current.get(normalizedSceneId);
    if (timerId) clearTimeout(timerId);
    comfyRestoreRetryTimersRef.current.delete(normalizedSceneId);
    comfyRestoreRetryCountsRef.current.delete(normalizedSceneId);
  }, []);

  const clearActiveComfyVideoJob = useCallback((sceneId = "", meta = null) => {
    const key = String(sceneId || "").trim();
    if (!key) return;
    const activeMeta = comfyVideoJobsBySceneRef.current.get(key) || null;
    const activeJobId = String(meta?.jobId || activeMeta?.jobId || "").trim();
    if (activeJobId) {
      activePollingJobsRef.current.delete(`${key}:${activeJobId}`);
    }
    comfyVideoJobsBySceneRef.current.delete(key);
    clearComfyRestoreRetry(key);
    persistActiveComfyVideoJob(Object.fromEntries(comfyVideoJobsBySceneRef.current.entries()));
    stopComfyVideoPolling(key);
    const terminalStatus = String(meta?.status || "").toLowerCase();
    if (terminalStatus) {
      console.info("[CLIP TRACE] active job cleared after terminal status", {
        scope: "comfy",
        sceneId: key,
        jobId: String(meta?.jobId || activeMeta?.jobId || ""),
        status: terminalStatus,
      });
      return;
    }
    if (activeMeta?.jobId) {
      console.warn("[CLIP TRACE] active comfy job cleared unexpectedly", {
        sceneId: key,
        jobId: String(meta?.jobId || activeMeta?.jobId || ""),
      });
    }
  }, [clearComfyRestoreRetry, persistActiveComfyVideoJob, stopComfyVideoPolling]);

  const resetComfyVideoJobsState = useCallback(() => {
    comfyVideoJobsBySceneRef.current.forEach((meta, sceneId) => {
      if (!meta?.jobId) return;
      console.warn("[CLIP TRACE] active comfy job cleared unexpectedly", {
        sceneId: String(sceneId || ""),
        jobId: String(meta?.jobId || ""),
      });
    });
    comfyVideoJobsBySceneRef.current.clear();
    activePollingJobsRef.current.clear();
    comfyRestoreRetryTimersRef.current.forEach((timerId) => clearTimeout(timerId));
    comfyRestoreRetryTimersRef.current.clear();
    comfyRestoreRetryCountsRef.current.clear();
    stopComfyVideoPolling();
    safeDel(COMFY_VIDEO_JOB_STORE_KEY);
  }, [COMFY_VIDEO_JOB_STORE_KEY, stopComfyVideoPolling]);


  const isComfyVideoJobNotFound = useCallback((payload) => {
    const status = String(payload?.status || "").toLowerCase();
    const code = String(payload?.code || "").toLowerCase();
    const hint = String(payload?.hint || "").toLowerCase();
    return status === "not_found"
      || code === "video_job_not_found"
      || code.includes("job_id_not_found_or_expired")
      || hint.includes("job_id_not_found_or_expired");
  }, []);

  const findComfySceneIndexById = useCallback((sceneId = "") => {
    const normalizedSceneId = String(sceneId || "").trim();
    if (!normalizedSceneId) return -1;
    return comfyScenesRef.current.findIndex((x) => String(x?.sceneId || "") === normalizedSceneId);
  }, []);

  const updateComfySceneById = useCallback((sceneId = "", patch = null) => {
    const idx = findComfySceneIndexById(sceneId);
    if (idx < 0 || !patch || typeof patch !== "object") return -1;
    updateComfyScene(idx, patch);
    return idx;
  }, [findComfySceneIndexById, updateComfyScene]);

  const buildComfyVideoJobMeta = useCallback((jobMeta = {}, sceneSnapshot = null) => {
    const sceneId = String(jobMeta?.sceneId || sceneSnapshot?.sceneId || "").trim();
    const imageUrl = String(jobMeta?.imageUrl || sceneSnapshot?.imageUrl || "").trim();
    const startImageUrl = String(jobMeta?.startImageUrl || sceneSnapshot?.startImageUrl || imageUrl || "").trim();
    const endImageUrl = String(jobMeta?.endImageUrl || sceneSnapshot?.endImageUrl || "").trim();
    const videoUrl = String(jobMeta?.videoUrl || sceneSnapshot?.videoUrl || "").trim();
    const now = Date.now();
    return {
      ...jobMeta,
      accountKey,
      sceneId,
      nodeId: String(jobMeta?.nodeId || comfyNode?.id || "").trim(),
      provider: String(jobMeta?.provider || "comfy_remote").trim() || "comfy_remote",
      providerJobId: String(jobMeta?.providerJobId || "").trim(),
      jobId: String(jobMeta?.jobId || "").trim(),
      status: String(jobMeta?.status || "queued").toLowerCase() || "queued",
      imageUrl,
      sceneImageUrl: imageUrl,
      sourceImageUrl: String(jobMeta?.sourceImageUrl || startImageUrl || endImageUrl || imageUrl || "").trim(),
      startImageUrl,
      endImageUrl,
      videoUrl,
      updatedAt: Number(jobMeta?.updatedAt) || now,
      startedAt: Number(jobMeta?.startedAt) || now,
      requestedDurationSec: Number(jobMeta?.requestedDurationSec || sceneSnapshot?.generationDurationSec || sceneSnapshot?.requestedDurationSec || sceneSnapshot?.durationSec) || null,
    };
  }, [accountKey, comfyNode?.id]);


  const startComfyVideoPolling = useCallback((jobMeta) => {
    if (!jobMeta?.jobId || !jobMeta?.sceneId) return;
    const sceneId = String(jobMeta.sceneId || "").trim();
    if (!sceneId) return;
    const startMeta = buildComfyVideoJobMeta({ ...jobMeta, sceneId, status: String(jobMeta?.status || "queued").toLowerCase() }, comfyScenesRef.current[findComfySceneIndexById(sceneId)] || null);
    const pollingKey = `${sceneId}:${String(startMeta.jobId || "")}`;
    if (CLIP_TRACE_VIDEO_POLLING) {
      console.info("[CLIP TRACE] polling start", {
        sceneId,
        jobId: String(startMeta?.jobId || ""),
        status: String(startMeta?.status || ""),
      });
    }
    if (activePollingJobsRef.current.has(pollingKey)) {
      if (CLIP_TRACE_VIDEO_POLLING) {
        console.info("[CLIP TRACE] polling start skipped already active", {
          scope: "comfy",
          sceneId,
          jobId: String(startMeta.jobId || ""),
        });
      }
      return;
    }
    const prevMeta = comfyVideoJobsBySceneRef.current.get(sceneId);
    const existingTimerId = comfyVideoPollTimersRef.current.get(sceneId);
    if (prevMeta?.jobId && String(prevMeta.jobId) === String(startMeta.jobId) && existingTimerId) {
      if (CLIP_TRACE_VIDEO_POLLING) {
        console.info("[CLIP TRACE] polling start skipped same active job", {
          scope: "comfy",
          sceneId,
          jobId: String(startMeta.jobId || ""),
        });
      }
      return;
    }
    if (prevMeta?.jobId && String(prevMeta.jobId) !== String(startMeta.jobId)) {
      activePollingJobsRef.current.delete(`${sceneId}:${String(prevMeta.jobId || "")}`);
      console.info("[CLIP TRACE] active job replaced", {
        scope: "comfy",
        sceneId,
        oldJobId: String(prevMeta.jobId || ""),
        newJobId: String(startMeta.jobId || ""),
      });
    }
    activePollingJobsRef.current.add(pollingKey);
    comfyVideoJobsBySceneRef.current.set(sceneId, startMeta);
    persistActiveComfyVideoJob(Object.fromEntries(comfyVideoJobsBySceneRef.current.entries()));
    clearComfyRestoreRetry(sceneId);
    updateComfySceneById(sceneId, { videoJobId: startMeta.jobId, videoStatus: startMeta.status, videoError: "" });
    stopComfyVideoPolling(sceneId);
    if (CLIP_TRACE_VIDEO_POLLING) {
      console.info("[CLIP TRACE] polling reset before start", { sceneId });
    }

    console.info("[CLIP VIDEO POLLING START]", {
      sceneId,
      jobId: String(startMeta.jobId || ""),
      provider: String(startMeta.provider || ""),
      workflowKey: String(startMeta.workflowKey || ""),
      modelKey: String(startMeta.modelKey || ""),
    });

    const scheduleComfyPoll = (delayMs, reason) => {
      if (CLIP_TRACE_VIDEO_POLLING) {
        console.info("[VIDEO POLLING START]", {
          scope: "comfy",
          reason,
          sceneId,
          jobId: String(startMeta.jobId || ""),
          delayMs,
        });
      }
      const timerId = setTimeout(tick, delayMs);
      comfyVideoPollTimersRef.current.set(sceneId, timerId);
      if (CLIP_TRACE_VIDEO_POLLING) {
        console.info("[CLIP TRACE] polling timer scheduled", {
          scope: "comfy",
          sceneId,
          jobId: String(startMeta.jobId || ""),
          delayMs,
          reason,
        });
      }
    };

    const tick = async () => {
      try {
        const activeMeta = comfyVideoJobsBySceneRef.current.get(sceneId);
        if (!activeMeta?.jobId) return;
        if (CLIP_TRACE_VIDEO_POLLING) {
          console.info("[CLIP VIDEO POLLING TICK]", {
            sceneId,
            jobId: String(activeMeta?.jobId || ""),
          });
          console.info("[VIDEO POLLING TICK]", {
            scope: "comfy",
            sceneId,
            jobId: String(activeMeta?.jobId || ""),
            status: String(activeMeta?.status || ""),
          });
        }
        const out = await fetchJson(`/api/clip/video/status/${encodeURIComponent(activeMeta.jobId)}`, {
          method: "GET",
          timeoutMs: VIDEO_STATUS_TIMEOUT_MS,
        });
        const activeMetaNow = comfyVideoJobsBySceneRef.current.get(sceneId);
        if (!activeMetaNow?.jobId || String(activeMetaNow.jobId) !== String(activeMeta.jobId)) {
          return;
        }
        const status = String(out?.status || "").toLowerCase() || "running";
        const nextMeta = buildComfyVideoJobMeta({
          ...activeMeta,
          providerJobId: String(out?.providerJobId || activeMeta?.providerJobId || ""),
          sceneId,
          status,
          videoUrl: String(out?.videoUrl || activeMeta?.videoUrl || ""),
          updatedAt: Date.now(),
        }, comfyScenesRef.current[findComfySceneIndexById(sceneId)] || null);
        comfyVideoJobsBySceneRef.current.set(sceneId, nextMeta);
        persistActiveComfyVideoJob(Object.fromEntries(comfyVideoJobsBySceneRef.current.entries()));

        const idx = findComfySceneIndexById(sceneId);
        if (idx >= 0) {
          updateComfySceneById(sceneId, {
            videoStatus: status,
            videoError: status === "error" || status === "stopped" || status === "not_found"
              ? String(out?.error || out?.hint || "video_job_failed")
              : "",
            videoJobId: nextMeta.jobId,
          });
          if (CLIP_TRACE_VIDEO_POLLING) {
            console.info("[VIDEO STATUS APPLIED]", {
              scope: "comfy",
              sceneId,
              jobId: String(nextMeta?.jobId || ""),
              status,
              ok: !!out?.ok,
            });
            console.info("[CLIP VIDEO STATUS APPLIED]", {
              sceneId,
              jobId: String(nextMeta?.jobId || ""),
              status,
              videoUrl: String(out?.videoUrl || ""),
              error: status === "error" || status === "stopped" || status === "not_found"
                ? String(out?.error || out?.hint || "video_job_failed")
                : "",
            });
          }
        }

        const prevNotFoundCount = Number(activeMeta?.notFoundCount) || 0;
        const notFoundRetryLimit = 3;
        if (!out?.ok && isComfyVideoJobNotFound(out)) {
          const nextNotFoundCount = prevNotFoundCount + 1;
          const toleratedMeta = { ...nextMeta, notFoundCount: nextNotFoundCount, status: "running", updatedAt: Date.now() };
          comfyVideoJobsBySceneRef.current.set(sceneId, toleratedMeta);
          persistActiveComfyVideoJob(Object.fromEntries(comfyVideoJobsBySceneRef.current.entries()));
          if (nextNotFoundCount < notFoundRetryLimit) {
            if (idx >= 0) updateComfySceneById(sceneId, { videoStatus: "running", videoError: "", videoJobId: String(toleratedMeta?.jobId || "") });
            scheduleComfyPoll(2200, "status_not_found_retry");
            return;
          }
          if (idx >= 0) updateComfySceneById(sceneId, { videoStatus: "not_found", videoError: String(out?.hint || out?.code || "video_job_not_found"), videoJobId: String(toleratedMeta?.jobId || "") });
          clearActiveComfyVideoJob(sceneId, { status: "not_found", jobId: toleratedMeta.jobId });
          return;
        }
        if (!out?.ok) throw new Error(out?.hint || out?.code || "video_status_failed");

        if (status === "done") {
          const doneVideoUrl = String(out?.videoUrl || "").trim();
          if (!doneVideoUrl) {
            scheduleComfyPoll(1800, "status_done_without_video");
            return;
          }
          if (idx === -1) {
            console.warn("[CLIP WARN] scene idx not found for done status", {
              scope: "comfy",
              sceneId,
              jobId: String(nextMeta?.jobId || ""),
            });
          } else {
            const currentScene = comfyScenesRef.current[idx] || null;
            const hasSameDoneState =
              String(currentScene?.videoStatus || "").toLowerCase() === "done"
              && String(currentScene?.videoUrl || "").trim() === doneVideoUrl
              && String(currentScene?.videoJobId || "") === String(nextMeta.jobId || "")
              && currentScene?.videoPanelOpen !== false;
            if (!hasSameDoneState) {
              updateComfySceneById(sceneId, {
                videoUrl: doneVideoUrl,
                videoPanelOpen: true,
                videoRequestedPromptPreview: String(out?.requestedPromptPreview || out?.debug?.requestedPromptPreview || ""),
                videoEffectivePromptPreview: String(out?.effectivePromptPreview || out?.debug?.effectivePromptPreview || ""),
                videoEffectivePromptLength: Number(out?.effectivePromptLength || out?.debug?.effectivePromptLength || 0) || 0,
                videoPromptPatchedNodeIds: Array.isArray(out?.promptPatchedNodeIds)
                  ? out.promptPatchedNodeIds
                  : (Array.isArray(out?.debug?.promptPatchedNodeIds) ? out.debug.promptPatchedNodeIds : []),
                videoStatus: "done",
                videoError: "",
                videoJobId: null,
              });
              if (CLIP_TRACE_VIDEO_POLLING) {
                console.info("[CLIP TRACE] video applied to scene", {
                  scope: "comfy",
                  sceneId,
                  idx,
                  videoUrl: doneVideoUrl,
                  videoStatus: "done",
                  videoJobId: "",
                });
              }
            }
          }
          activePollingJobsRef.current.delete(`${sceneId}:${String(nextMeta.jobId || "")}`);
          if (CLIP_TRACE_VIDEO_POLLING) {
            console.info("[CLIP TRACE] polling done", {
              scope: "comfy",
              sceneId,
              jobId: String(nextMeta?.jobId || ""),
            });
          }
          clearActiveComfyVideoJob(sceneId, { status: "done", jobId: nextMeta.jobId });
          return;
        }

        if (status === "error" || status === "stopped" || status === "not_found") {
          activePollingJobsRef.current.delete(`${sceneId}:${String(nextMeta.jobId || "")}`);
          clearActiveComfyVideoJob(sceneId, { status, jobId: nextMeta.jobId });
          return;
        }

        scheduleComfyPoll(1800, "status_running");
      } catch (e) {
        const idx = findComfySceneIndexById(sceneId);
        const errMsg = String(e?.message || e || "").toLowerCase();
        if (errMsg.includes("job_id_not_found_or_expired")) {
          const activeMetaNow = comfyVideoJobsBySceneRef.current.get(sceneId) || {};
          const nextNotFoundCount = (Number(activeMetaNow?.notFoundCount) || 0) + 1;
          const notFoundRetryLimit = 3;
          const toleratedMeta = { ...activeMetaNow, notFoundCount: nextNotFoundCount, status: "running", updatedAt: Date.now() };
          comfyVideoJobsBySceneRef.current.set(sceneId, toleratedMeta);
          persistActiveComfyVideoJob(Object.fromEntries(comfyVideoJobsBySceneRef.current.entries()));
          if (nextNotFoundCount < notFoundRetryLimit) {
            if (idx >= 0) updateComfySceneById(sceneId, { videoStatus: "running", videoError: "", videoJobId: String(toleratedMeta?.jobId || "") });
            scheduleComfyPoll(2400, "exception_not_found_retry");
            return;
          }
          if (idx >= 0) updateComfySceneById(sceneId, { videoStatus: "not_found", videoError: String(e?.message || e), videoJobId: String(toleratedMeta?.jobId || "") });
          clearActiveComfyVideoJob(sceneId, { status: "not_found", jobId: toleratedMeta.jobId });
          return;
        }
        if (idx >= 0) {
          updateComfySceneById(sceneId, { videoStatus: "running", videoError: "" });
        }
        scheduleComfyPoll(2400, "exception_retry");
      }
    };

    scheduleComfyPoll(250, "initial_after_start");
    if (CLIP_TRACE_VIDEO_POLLING) {
      console.info("[CLIP TRACE] initial comfy poll scheduled", {
        sceneId,
        jobId: String(startMeta.jobId || ""),
      });
    }
  }, [buildComfyVideoJobMeta, clearActiveComfyVideoJob, clearComfyRestoreRetry, findComfySceneIndexById, isComfyVideoJobNotFound, persistActiveComfyVideoJob, stopComfyVideoPolling, updateComfySceneById]);

  useEffect(() => () => stopComfyVideoPolling(), [stopComfyVideoPolling]);

  useEffect(() => () => {
    comfyRestoreRetryTimersRef.current.forEach((timerId) => clearTimeout(timerId));
    comfyRestoreRetryTimersRef.current.clear();
    comfyRestoreRetryCountsRef.current.clear();
  }, []);

  useEffect(() => {
    const restoreToken = `${accountKey}:${COMFY_VIDEO_JOB_STORE_KEY}`;
    if (!didHydrateRef.current || isHydratingRef.current) {
      console.info("[VIDEO RESTORE] start skipped", {
        reason: "graph_not_hydrated",
        accountKey,
        comfySceneCount: comfyScenes.length,
      });
      return;
    }
    if (restoredComfyVideoJobsRef.current.has(restoreToken)) return;
    const raw = safeGet(COMFY_VIDEO_JOB_STORE_KEY);
    if (!raw) {
      restoredComfyVideoJobsRef.current.add(restoreToken);
      return;
    }
    try {
      const parsed = JSON.parse(raw);
      const entries = parsed?.jobId
        ? [[String(parsed?.sceneId || ""), parsed]]
        : (parsed && typeof parsed === "object" ? Object.entries(parsed) : []);
      let hasPendingHydrationRestore = false;
      console.info("[VIDEO RESTORE] start", {
        accountKey,
        comfySceneCount: comfyScenes.length,
        storedJobs: entries.length,
      });
      console.info("[VIDEO RESTORE] hydrated scenes ids", comfyScenes.map((scene) => String(scene?.sceneId || "")).filter(Boolean));
      const nextPersisted = {};
      entries.forEach(([sceneId, meta]) => {
        if (!meta?.jobId) return;
        const normalizedSceneId = String(sceneId || "").trim();
        if (!normalizedSceneId) return;
        const persistedJobId = String(meta?.jobId || "").trim();
        console.info("[VIDEO RESTORE] restored job", {
          jobId: persistedJobId,
          sceneId: normalizedSceneId,
          status: String(meta?.status || ""),
        });
        const idx = findComfySceneIndexById(normalizedSceneId);
        if (idx < 0) {
          hasPendingHydrationRestore = true;
          nextPersisted[normalizedSceneId] = buildComfyVideoJobMeta({
            ...meta,
            sceneId: normalizedSceneId,
            status: String(meta?.status || "running").toLowerCase() || "running",
          });
          console.warn("[VIDEO RESTORE] scene missing", {
            sceneId: normalizedSceneId,
            jobId: persistedJobId,
          });
          scheduleComfyRestoreRetry(normalizedSceneId, "scene_missing_after_reload");
          return;
        }
        clearComfyRestoreRetry(normalizedSceneId);
        console.info("[VIDEO RESTORE] scene found", {
          sceneId: normalizedSceneId,
          jobId: persistedJobId,
          idx,
        });
        const sceneNow = comfyScenesRef.current[idx] || null;
        const sceneVideoUrl = String(sceneNow?.videoUrl || "").trim();
        const sceneVideoJobId = String(sceneNow?.videoJobId || "").trim();
        if (sceneVideoUrl) {
          console.info("[VIDEO RESTORE] dropped stale job reason=scene_has_video_url", {
            sceneId: normalizedSceneId,
            jobId: persistedJobId,
          });
          return;
        }
        if (sceneVideoJobId && sceneVideoJobId !== persistedJobId) {
          console.info("[VIDEO RESTORE] dropped stale job reason=scene_has_different_video_job", {
            sceneId: normalizedSceneId,
            jobId: persistedJobId,
            sceneVideoJobId,
          });
          return;
        }
        const normalizedMeta = buildComfyVideoJobMeta({
          ...meta,
          sceneId: normalizedSceneId,
          status: String(meta?.status || "running").toLowerCase() || "running",
        }, sceneNow);
        comfyVideoJobsBySceneRef.current.set(normalizedSceneId, normalizedMeta);
        nextPersisted[normalizedSceneId] = normalizedMeta;
        updateComfySceneById(normalizedSceneId, {
          videoJobId: persistedJobId,
          videoStatus: normalizedMeta.status,
          videoError: "",
        });
        fetchJson(`/api/clip/video/status/${encodeURIComponent(persistedJobId)}`, {
          method: "GET",
          timeoutMs: VIDEO_STATUS_TIMEOUT_MS,
        })
          .then((out) => {
            const status = String(out?.status || "").toLowerCase();
            if (status === "done") {
              const doneVideoUrl = String(out?.videoUrl || "").trim();
              if (!doneVideoUrl) {
                startComfyVideoPolling(buildComfyVideoJobMeta({ ...normalizedMeta, status: "running" }, comfyScenesRef.current[findComfySceneIndexById(normalizedSceneId)] || null));
                return;
              }
              console.info("[VIDEO RESTORE] apply final result sceneId=" + normalizedSceneId, {
                jobId: persistedJobId,
                videoUrl: doneVideoUrl,
              });
              updateComfySceneById(normalizedSceneId, {
                videoUrl: doneVideoUrl,
                videoPanelOpen: true,
                videoRequestedPromptPreview: String(out?.requestedPromptPreview || out?.debug?.requestedPromptPreview || ""),
                videoEffectivePromptPreview: String(out?.effectivePromptPreview || out?.debug?.effectivePromptPreview || ""),
                videoEffectivePromptLength: Number(out?.effectivePromptLength || out?.debug?.effectivePromptLength || 0) || 0,
                videoPromptPatchedNodeIds: Array.isArray(out?.promptPatchedNodeIds)
                  ? out.promptPatchedNodeIds
                  : (Array.isArray(out?.debug?.promptPatchedNodeIds) ? out.debug.promptPatchedNodeIds : []),
                videoStatus: "done",
                videoJobId: null,
                videoError: "",
              });
              activePollingJobsRef.current.delete(`${normalizedSceneId}:${persistedJobId}`);
              clearActiveComfyVideoJob(normalizedSceneId, { status: "done", jobId: persistedJobId });
              return;
            }
            if (status === "running" || status === "queued" || status === "pending") {
              console.info("[VIDEO RESTORE] polling resumed", {
                sceneId: normalizedSceneId,
                jobId: persistedJobId,
                status: status || "running",
              });
              startComfyVideoPolling(buildComfyVideoJobMeta({ ...normalizedMeta, status: status || "running" }, comfyScenesRef.current[findComfySceneIndexById(normalizedSceneId)] || null));
              return;
            }
            if (status === "error" || status === "stopped" || status === "not_found") {
              updateComfySceneById(normalizedSceneId, {
                videoStatus: status,
                videoError: String(out?.error || out?.hint || "video_job_failed"),
                videoJobId: persistedJobId,
              });
              clearActiveComfyVideoJob(normalizedSceneId, { status, jobId: persistedJobId });
              return;
            }
            console.info("[VIDEO RESTORE] polling resumed", {
              sceneId: normalizedSceneId,
              jobId: persistedJobId,
              status: "running_fallback",
            });
            startComfyVideoPolling(buildComfyVideoJobMeta({ ...normalizedMeta, status: "running" }, comfyScenesRef.current[findComfySceneIndexById(normalizedSceneId)] || null));
          })
          .catch((error) => {
            console.info("[VIDEO RESTORE] polling resumed", {
              sceneId: normalizedSceneId,
              jobId: persistedJobId,
              status: normalizedMeta.status || "running",
              reason: String(error?.message || error || "status_check_failed"),
            });
            startComfyVideoPolling(buildComfyVideoJobMeta({ ...normalizedMeta, status: normalizedMeta.status || "running" }, comfyScenesRef.current[findComfySceneIndexById(normalizedSceneId)] || null));
          });
      });
      persistActiveComfyVideoJob(nextPersisted);
      if (!hasPendingHydrationRestore) {
        restoredComfyVideoJobsRef.current.add(restoreToken);
      }
    } catch {
      safeDel(COMFY_VIDEO_JOB_STORE_KEY);
      restoredComfyVideoJobsRef.current.add(restoreToken);
    }
  }, [COMFY_VIDEO_JOB_STORE_KEY, accountKey, buildComfyVideoJobMeta, clearActiveComfyVideoJob, clearComfyRestoreRetry, comfyRestoreRevision, comfyScenes, didHydrateRef, findComfySceneIndexById, persistActiveComfyVideoJob, scheduleComfyRestoreRetry, startComfyVideoPolling, updateComfySceneById]);

  const handleComfyGenerateImage = useCallback(async () => {
    if (!comfySelectedScene) return;
    setComfyImageLoading(true);
    setComfyImageError('');
    try {
      const sceneId = String(comfySelectedScene?.sceneId || "").trim();
      if (!sceneId) throw new Error("scene_id_required");
      const comfyCurrentSceneById = comfyScenes.find((scene) => String(scene?.sceneId || '').trim() === sceneId) || null;
      const comfySceneForImageContract = comfyCurrentSceneById || comfySelectedScene;
      const contextPrompt = buildComfySceneContextPrompt({
        scene: comfySceneForImageContract,
        mode: comfyNode?.data?.mode || "clip",
        stylePreset: comfyNode?.data?.stylePreset || "realism",
      });
      const syncedImagePrompt = await ensureComfyPromptSynced({ idx: comfySafeIndex, promptType: 'image' });
      const previousSceneImageUrl = String(
        comfyPreviousScene?.endImageUrl
        || comfyPreviousScene?.imageUrl
        || comfyPreviousScene?.startImageUrl
        || ""
      ).trim();
      const nodesNow = nodesRef.current || [];
      const edgesNow = edgesRef.current || [];
      const linkedBrainEdge = edgesNow.find((edge) => {
        if (!edge || edge.target !== comfyNode?.id) return false;
        if (edge.sourceHandle !== "comfy_plan" || edge.targetHandle !== "comfy_plan") return false;
        const sourceNode = nodesNow.find((node) => node.id === edge.source);
        return sourceNode?.type === "comfyBrain";
      });
      const linkedComfyBrainNode = linkedBrainEdge
        ? (nodesNow.find((node) => node.id === linkedBrainEdge.source && node.type === "comfyBrain") || null)
        : null;
      if (!linkedComfyBrainNode) {
        console.warn("[COMFY DEBUG FRONT] /clip/image comfyBrain not found for storyboard, fallback derive through comfyStoryboard", {
          storyboardNodeId: comfyNode?.id || null,
          fallback: "comfyStoryboard",
        });
      }
      const deriveNode = linkedComfyBrainNode || comfyNode;
      console.log("[COMFY DEBUG FRONT] /clip/image derive source node", {
        deriveNodeType: deriveNode?.type || null,
        deriveNodeId: deriveNode?.id || null,
        storyboardNodeId: comfyNode?.id || null,
        foundBrainNodeId: linkedComfyBrainNode?.id || null,
      });
      const liveDerived = deriveComfyBrainState({
        nodeId: deriveNode?.id,
        nodeData: deriveNode?.data || {},
        nodesNow,
        edgesNow,
        normalizeRefDataFn: normalizeRefData,
      });
      const comfyScenarioFormat = resolvePreferredSceneFormat(
        comfySceneForImageContract?.imageFormat,
        comfySceneForImageContract?.format,
        linkedComfyBrainNode?.data?.format,
        comfyNode?.data?.format,
        comfyNode?.data?.plannerMeta?.format,
        comfyNode?.data?.plannerMeta?.plannerInput?.format
      );
      const { width, height } = getSceneImageSize(comfyScenarioFormat);
      const plannerInput = {
        text: liveDerived?.meaningfulText || "",
        audioUrl: liveDerived?.meaningfulAudio || "",
        audioDurationSec: liveDerived?.meaningfulAudioDurationSec || null,
        refsByRole: liveDerived?.refsByRole || {},
        mode: liveDerived?.modeValue || String(deriveNode?.data?.mode || "clip"),
        format: comfyScenarioFormat,
        stylePreset: liveDerived?.stylePreset || String(deriveNode?.data?.styleKey || "realism"),
      };
      const readyLiveRefsSelection = pickReadyLiveRefsByRoleForScene({
        liveDerived,
        scene: comfySceneForImageContract,
        includeDebug: true,
      });
      const readyLiveRefsByRole = readyLiveRefsSelection?.refsByRole || {};
      const readyLiveRefsDebug = readyLiveRefsSelection?.debug || {};
      const plannerSceneSnapshot = comfyNode?.data?.plannerMeta?.mockScenes?.[comfySafeIndex] || null;
      const promptSelection = selectComfyImagePrompt({
        syncedImagePrompt,
        sceneContract: comfySceneForImageContract,
        liveDerived,
        plannerSceneSnapshot,
      });
      const imagePrompt = promptSelection.imagePrompt;
      const promptSource = promptSelection.promptSource;
      const refsByRoleForImage = readyLiveRefsByRole;
      const refsPayloadForImage = {
        refsByRole: refsByRoleForImage,
        previousSceneImageUrl,
        previousContinuityMemory: comfySceneForImageContract?.continuity ? { continuity: comfySceneForImageContract.continuity } : null,
        propAnchorLabel: inferPropAnchorLabel(refsByRoleForImage),
        text: plannerInput?.text || comfyNode?.data?.text || "",
        audioUrl: plannerInput?.audioUrl || comfyNode?.data?.audioUrl || "",
        mode: plannerInput?.mode || comfyNode?.data?.mode || "",
        stylePreset: plannerInput?.stylePreset || comfyNode?.data?.stylePreset || "",
        sceneId,
        sceneGoal: comfySceneForImageContract?.sceneGoal || plannerInput?.sceneGoal || "",
        sceneNarrativeStep: comfySceneForImageContract?.sceneNarrativeStep || plannerInput?.sceneNarrativeStep || "",
        continuity: comfySceneForImageContract?.continuity || plannerInput?.continuity || "",
        plannerMeta: comfyNode?.data?.plannerMeta || null,
        refsUsed: Array.isArray(comfySceneForImageContract?.refsUsed) ? comfySceneForImageContract.refsUsed : [],
        refDirectives: comfySceneForImageContract?.refDirectives && typeof comfySceneForImageContract.refDirectives === 'object' ? comfySceneForImageContract.refDirectives : null,
        primaryRole: comfySceneForImageContract?.primaryRole || "",
        secondaryRoles: Array.isArray(comfySceneForImageContract?.secondaryRoles) ? comfySceneForImageContract.secondaryRoles : [],
        heroEntityId: comfySceneForImageContract?.heroEntityId || "",
        supportEntityIds: Array.isArray(comfySceneForImageContract?.supportEntityIds) ? comfySceneForImageContract.supportEntityIds : [],
        mustAppear: Array.isArray(comfySceneForImageContract?.mustAppear) ? comfySceneForImageContract.mustAppear : [],
        mustNotAppear: Array.isArray(comfySceneForImageContract?.mustNotAppear) ? comfySceneForImageContract.mustNotAppear : [],
        environmentLock: typeof comfySceneForImageContract?.environmentLock === 'boolean' ? comfySceneForImageContract.environmentLock : null,
        styleLock: typeof comfySceneForImageContract?.styleLock === 'boolean' ? comfySceneForImageContract.styleLock : null,
        identityLock: typeof comfySceneForImageContract?.identityLock === 'boolean' ? comfySceneForImageContract.identityLock : null,
        sceneRoleDynamics: comfySceneForImageContract?.sceneRoleDynamics || "",
        imagePrompt,
        videoPrompt: comfySceneForImageContract?.videoPromptEn || comfySceneForImageContract?.videoPromptRu || "",
        promptSource,
      };
      const refsForImageRequest = buildComfySceneRefsPayload(refsPayloadForImage);

      console.log("[COMFY DEBUG FRONT] /clip/image plannerInput", plannerInput);
      console.log("[COMFY DEBUG FRONT] /clip/image plannerInput.refsByRole", plannerInput?.refsByRole);
      console.log("[COMFY DEBUG FRONT] /clip/image plannerInput.refsByRole counts", summarizeRefsByRole(plannerInput?.refsByRole));
      console.log("[COMFY DEBUG FRONT] /clip/image readyLiveRefsByRole", readyLiveRefsByRole);
      console.log("[COMFY DEBUG FRONT] /clip/image readyLiveRefsByRole counts", summarizeRefsByRole(readyLiveRefsByRole));
      console.log("[COMFY DEBUG FRONT] /clip/image readyLiveRefs selection debug", readyLiveRefsDebug);
      console.log("[COMFY DEBUG FRONT] /clip/image comfyRefsByRole", comfyRefsByRole);
      console.log("[COMFY DEBUG FRONT] /clip/image comfyRefsByRole counts", summarizeRefsByRole(comfyRefsByRole));
      console.log("[COMFY DEBUG FRONT] /clip/image selected scene vs current scene", {
        sceneId,
        selectedHeroEntityId: comfySelectedScene?.heroEntityId || null,
        currentHeroEntityId: comfyCurrentSceneById?.heroEntityId || null,
        selectedRefsUsed: Array.isArray(comfySelectedScene?.refsUsed) ? comfySelectedScene.refsUsed : [],
        currentRefsUsed: Array.isArray(comfyCurrentSceneById?.refsUsed) ? comfyCurrentSceneById.refsUsed : [],
        selectedPrimaryRole: comfySelectedScene?.primaryRole || "",
        currentPrimaryRole: comfyCurrentSceneById?.primaryRole || "",
        selectedSceneGoal: comfySelectedScene?.sceneGoal || "",
        currentSceneGoal: comfyCurrentSceneById?.sceneGoal || "",
      });
      console.log("[COMFY DEBUG FRONT] /clip/image refs payload for buildComfySceneRefsPayload", refsPayloadForImage);
      console.log("[COMFY DEBUG FRONT] /clip/image refs payload refsByRole counts", summarizeRefsByRole(refsPayloadForImage?.refsByRole));
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsByRoleForImage.character_1 count", Array.isArray(refsByRoleForImage?.character_1) ? refsByRoleForImage.character_1.length : 0);
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsByRoleForImage.character_1 urls", Array.isArray(refsByRoleForImage?.character_1) ? refsByRoleForImage.character_1 : []);
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsPayloadForImage.refsByRole.character_1 count", Array.isArray(refsPayloadForImage?.refsByRole?.character_1) ? refsPayloadForImage.refsByRole.character_1.length : 0);
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsPayloadForImage.refsByRole.character_1 urls", Array.isArray(refsPayloadForImage?.refsByRole?.character_1) ? refsPayloadForImage.refsByRole.character_1 : []);
      console.log("[COMFY DEBUG FRONT] /clip/image final refs before request", refsForImageRequest);
      console.log("[COMFY DEBUG FRONT] /clip/image final refsByRole counts before request", summarizeRefsByRole(refsForImageRequest?.refsByRole));
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsForImageRequest.refsByRole.character_1 count", Array.isArray(refsForImageRequest?.refsByRole?.character_1) ? refsForImageRequest.refsByRole.character_1.length : 0);
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsForImageRequest.refsByRole.character_1 urls", Array.isArray(refsForImageRequest?.refsByRole?.character_1) ? refsForImageRequest.refsByRole.character_1 : []);
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsForImageRequest.character count", Array.isArray(refsForImageRequest?.character) ? refsForImageRequest.character.length : 0);
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsForImageRequest.character urls", Array.isArray(refsForImageRequest?.character) ? refsForImageRequest.character : []);
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsForImageRequest.heroEntityId", refsForImageRequest?.heroEntityId || null);
      console.log("[COMFY DEBUG FRONT] /clip/image stage refsForImageRequest.refsUsed", refsForImageRequest?.refsUsed || []);
      console.log("[COMFY DEBUG FRONT] /clip/image final role contract", {
        heroEntityId: refsForImageRequest?.heroEntityId || null,
        refsUsed: refsForImageRequest?.refsUsed || [],
        primaryRole: refsForImageRequest?.primaryRole || null,
        secondaryRoles: refsForImageRequest?.secondaryRoles || [],
        supportEntityIds: refsForImageRequest?.supportEntityIds || [],
        mustAppear: refsForImageRequest?.mustAppear || [],
        mustNotAppear: refsForImageRequest?.mustNotAppear || [],
        environmentLock: refsForImageRequest?.environmentLock,
        styleLock: refsForImageRequest?.styleLock,
        identityLock: refsForImageRequest?.identityLock,
      });
      console.log("[COMFY DEBUG FRONT] /clip/image final refsByRole urls", Object.fromEntries(
        Object.entries(refsForImageRequest?.refsByRole || {}).map(([role, urls]) => [role, Array.isArray(urls) ? urls : []])
      ));
      console.log("[COMFY DEBUG FRONT] /clip/image prompt selection", {
        sceneId,
        promptSource,
        imagePromptPreview: imagePrompt.slice(0, 240),
        selectedSceneId: String(comfySelectedScene?.sceneId || ""),
        currentSceneId: String(comfyCurrentSceneById?.sceneId || ""),
        audioCueDroppedAsTechnicalRef: promptSelection.audioCueDroppedAsTechnicalRef,
        promptCandidates: promptSelection.promptCandidates.map((item) => ({ source: item.source, preview: String(item.value || "").slice(0, 120) })),
      });

      const out = await fetchJson('/api/clip/image', {
        method: 'POST',
        body: {
          sceneId,
          prompt: imagePrompt,
          sceneDelta: `${imagePrompt}

${contextPrompt}

Aspect ratio: ${comfyScenarioFormat}`.trim(),
          sceneText: contextPrompt,
          style: String(plannerInput?.stylePreset || comfyNode?.data?.stylePreset || 'realism'),
          width,
          height,
          sceneContract: comfySceneForImageContract && typeof comfySceneForImageContract === 'object' ? comfySceneForImageContract : null,
          refs: refsForImageRequest,
          promptDebug: {
            sceneId,
            promptSource,
            promptPreview: imagePrompt.slice(0, 400),
            sceneDeltaPreview: `${imagePrompt}\n\n${contextPrompt}`.trim().slice(0, 400),
            sceneTextPreview: String(contextPrompt || "").slice(0, 400),
            audioCueDroppedAsTechnicalRef: promptSelection.audioCueDroppedAsTechnicalRef,
          },
        },
      });
      console.log("[COMFY DEBUG FRONT] /clip/image final request body refs", refsForImageRequest);
      if (!out?.ok || !out?.imageUrl) throw new Error(out?.hint || out?.code || 'image_generation_failed');
      updateComfyScene(comfySafeIndex, { imageUrl: String(out.imageUrl || ''), imageFormat: comfyScenarioFormat, format: comfyScenarioFormat });
    } catch (e) {
      traceScenarioVideo("[SCENARIO VIDEO TRACE ERROR] start_flow_exception", {
        sceneId,
        selectedSceneId: requestedSceneId,
        nodeId: targetNodeId,
        workflowKey: "",
        lipSyncRoute: false,
        hasImageUrl: false,
        hasAudioSliceUrl: false,
        videoStatus: "error",
        branch: String(e?.message || e || ""),
      });
      console.error(e);
      setComfyImageError(String(e?.message || e));
    } finally {
      setComfyImageLoading(false);
    }
  }, [comfyNode?.data, comfyNode?.id, comfyNode?.data?.mode, comfyNode?.data?.stylePreset, comfyPreviousScene?.endImageUrl, comfyPreviousScene?.imageUrl, comfyPreviousScene?.startImageUrl, comfyRefsByRole, comfySafeIndex, comfyScenes, comfySelectedScene, ensureComfyPromptSynced, normalizeRefData, updateComfyScene]);

  const handleComfyDeleteImage = useCallback(() => {
    const selectedSceneId = String(comfySelectedScene?.sceneId || '').trim();
    if (selectedSceneId) {
      clearActiveComfyVideoJob(selectedSceneId);
    }
    setComfyImageError('');
    updateComfyScene(comfySafeIndex, { imageUrl: '', videoUrl: '', videoPanelOpen: false, videoStatus: '', videoError: '', videoJobId: '' });
  }, [clearActiveComfyVideoJob, comfySafeIndex, comfySelectedScene?.sceneId, updateComfyScene]);

  const handleComfyOpenVideoPanel = useCallback(() => {
    if (!comfySelectedScene) return;
    updateComfyScene(comfySafeIndex, { videoPanelOpen: true });
  }, [comfySafeIndex, comfySelectedScene, updateComfyScene]);

  const handleComfyGenerateVideo = useCallback(async () => {
    if (!comfySelectedScene?.imageUrl) return;
    const sceneId = String(comfySelectedScene?.sceneId || "").trim();
    if (sceneId) {
      updateComfySceneById(sceneId, { videoPanelOpen: true, videoStatus: 'queued', videoError: '' });
    }
    try {
      if (!sceneId) throw new Error("scene_id_required");
      const comfySceneSnapshot = comfyScenesRef.current[findComfySceneIndexById(sceneId)] || comfySelectedScene;
      const comfyScenarioFormat = resolvePreferredSceneFormat(
        comfySceneSnapshot?.imageFormat,
        comfySceneSnapshot?.format,
        comfyNode?.data?.format,
        comfyNode?.data?.plannerMeta?.format,
        comfyNode?.data?.plannerMeta?.plannerInput?.format
      );
      const contextPrompt = buildComfySceneContextPrompt({
        scene: comfySceneSnapshot,
        mode: comfyNode?.data?.mode || "clip",
        stylePreset: comfyNode?.data?.stylePreset || "realism",
        isVideo: true,
      });
      const syncedVideoPrompt = await ensureComfyPromptSynced({ idx: comfySafeIndex, promptType: 'video' });
      console.log('[COMFY VIDEO START]', {
        sceneId,
        provider: 'comfy_remote',
        imageUrl: String(comfySelectedScene.imageUrl || ''),
        hasExistingVideo: Boolean(String(comfySelectedScene.videoUrl || '').trim()),
        hasActiveSceneJob: comfyHasActiveVideoJobForScene,
      });
      const rawVideoSourceUrls = {
        imageUrl: String(comfySceneSnapshot.imageUrl || "").trim(),
        startImageUrl: String(comfySceneSnapshot.startImageUrl || comfySceneSnapshot.startFrameImageUrl || "").trim(),
        endImageUrl: String(comfySceneSnapshot.endImageUrl || comfySceneSnapshot.endFrameImageUrl || "").trim(),
        audioSliceUrl: String(comfySceneSnapshot.audioSliceUrl || "").trim(),
        continuationSourceAssetUrl: String(comfySceneSnapshot.continuationSourceAssetUrl || "").trim(),
      };
      const comfyRouteWorkflow = normalizeDirectRouteToWorkflowKey(
        comfySceneSnapshot?.resolvedWorkflowKey
        || comfySceneSnapshot?.resolved_workflow_key
        || comfySceneSnapshot?.videoGenerationRoute
        || comfySceneSnapshot?.video_generation_route
        || comfySceneSnapshot?.plannedVideoGenerationRoute
        || comfySceneSnapshot?.planned_video_generation_route
      ) || "i2v";
      const comfyLipSync = comfyRouteWorkflow === "lip_sync_music";
      const comfySendAudioToGenerator = Boolean(comfySceneSnapshot?.sendAudioToGenerator ?? comfySceneSnapshot?.send_audio_to_generator ?? comfyLipSync);
      if (comfyLipSync && comfySendAudioToGenerator && !String(rawVideoSourceUrls.audioSliceUrl || "").trim()) {
        throw new Error("audioSliceUrl_required_for_lip_sync_workflow");
      }
      const normalizedVideoSourceUrls = {
        imageUrl: normalizeVideoSourceUrl(rawVideoSourceUrls.imageUrl),
        startImageUrl: normalizeVideoSourceUrl(rawVideoSourceUrls.startImageUrl),
        endImageUrl: normalizeVideoSourceUrl(rawVideoSourceUrls.endImageUrl),
        audioSliceUrl: normalizeVideoSourceUrl(rawVideoSourceUrls.audioSliceUrl),
        continuationSourceAssetUrl: normalizeVideoSourceUrl(rawVideoSourceUrls.continuationSourceAssetUrl),
      };
      console.debug("[SCENARIO VIDEO URL NORMALIZE]", {
        sceneId,
        provider: "comfy_remote",
        renderMode: "standard_video",
        original: rawVideoSourceUrls,
        normalized: normalizedVideoSourceUrls,
      });
      const out = await fetchJson('/api/clip/video/start', {
        method: 'POST',
        timeoutMs: VIDEO_START_TIMEOUT_MS,
        body: {
          sceneId,
          imageUrl: normalizedVideoSourceUrls.imageUrl,
          startImageUrl: normalizedVideoSourceUrls.startImageUrl,
          endImageUrl: normalizedVideoSourceUrls.endImageUrl,
          audioSliceUrl: comfyLipSync && comfySendAudioToGenerator ? normalizedVideoSourceUrls.audioSliceUrl : "",
          continuationSourceAssetUrl: normalizedVideoSourceUrls.continuationSourceAssetUrl,
          videoPrompt: syncedVideoPrompt,
          transitionActionPrompt: contextPrompt,
          requestedDurationSec: Number(comfySceneSnapshot.generationDurationSec) || Math.ceil(Number(comfySceneSnapshot.durationSec) || 3),
          resolvedWorkflowKey: comfyRouteWorkflow,
          video_generation_route: comfyRouteWorkflow,
          lipSync: comfyLipSync,
          send_audio_to_generator: comfyLipSync && comfySendAudioToGenerator,
          audio_slice_start_sec: Number(comfySceneSnapshot?.audioSliceStartSec ?? comfySceneSnapshot?.audio_slice_start_sec ?? 0),
          audio_slice_end_sec: Number(comfySceneSnapshot?.audioSliceEndSec ?? comfySceneSnapshot?.audio_slice_end_sec ?? 0),
          audio_slice_expected_duration_sec: Number(comfySceneSnapshot?.audioSliceExpectedDurationSec ?? comfySceneSnapshot?.audio_slice_expected_duration_sec ?? 0),
          shotType: String(comfySceneSnapshot.sceneNarrativeStep || ''),
          sceneType: String(comfySceneSnapshot.sceneGoal || ''),
          format: comfyScenarioFormat,
          provider: 'comfy_remote',
        },
      });
      console.info("[VIDEO START RESPONSE]", {
        scope: "comfy",
        sceneId,
        ok: !!out?.ok,
        jobId: String(out?.jobId || ""),
        providerJobId: String(out?.providerJobId || ""),
      });
      console.info("[CLIP VIDEO START RESPONSE]", {
        ok: !!out?.ok,
        jobId: String(out?.jobId || ""),
        provider: String(out?.provider || "comfy_remote"),
        mode: String(out?.mode || ""),
        sceneId,
        status: String(out?.status || ""),
        raw: out,
      });
      if (!out?.jobId) {
        console.warn("[CLIP WARN] video start returned without jobId", {
          sceneId,
          provider: "comfy_remote",
          raw: out,
        });
      }

      if (out?.jobId) {
        if (!out?.ok) {
          console.warn("[CLIP WARN] start response has jobId but ok=false, forcing polling start", {
            sceneId,
            jobId: String(out?.jobId || ""),
            raw: out,
          });
        }
        const startedMeta = buildComfyVideoJobMeta({
          jobId: String(out.jobId),
          providerJobId: String(out.providerJobId || ''),
          provider: String(out?.provider || "comfy_remote"),
          sceneId,
          status: 'queued',
        }, comfySceneSnapshot);
        comfyVideoJobsBySceneRef.current.set(sceneId, startedMeta);
        persistActiveComfyVideoJob(Object.fromEntries(comfyVideoJobsBySceneRef.current.entries()));
        updateComfySceneById(sceneId, { videoJobId: startedMeta.jobId, videoStatus: 'queued', videoError: '' });

        let shouldStartPolling = true;
        try {
          const immediateOut = await fetchJson(`/api/clip/video/status/${encodeURIComponent(startedMeta.jobId)}`, {
            method: "GET",
            timeoutMs: VIDEO_STATUS_TIMEOUT_MS,
          });
          const immediateStatus = String(immediateOut?.status || "").toLowerCase() || "running";
          if (immediateStatus === "done" && String(immediateOut?.videoUrl || "").trim()) {
            const immediateVideoUrl = String(immediateOut.videoUrl || '').trim();
            const currentSceneIdx = findComfySceneIndexById(sceneId);
            const currentScene = currentSceneIdx >= 0 ? (comfyScenesRef.current[currentSceneIdx] || null) : null;
            const hasSameDoneState =
              String(currentScene?.videoStatus || "").toLowerCase() === "done"
              && String(currentScene?.videoUrl || "").trim() === immediateVideoUrl
              && String(currentScene?.videoJobId || "") === String(startedMeta.jobId || "")
              && currentScene?.videoPanelOpen !== false;
            if (!hasSameDoneState) {
              updateComfySceneById(sceneId, {
                videoUrl: immediateVideoUrl,
                videoPanelOpen: true,
                videoRequestedPromptPreview: String(immediateOut?.requestedPromptPreview || immediateOut?.debug?.requestedPromptPreview || ""),
                videoEffectivePromptPreview: String(immediateOut?.effectivePromptPreview || immediateOut?.debug?.effectivePromptPreview || ""),
                videoEffectivePromptLength: Number(immediateOut?.effectivePromptLength || immediateOut?.debug?.effectivePromptLength || 0) || 0,
                videoPromptPatchedNodeIds: Array.isArray(immediateOut?.promptPatchedNodeIds)
                  ? immediateOut.promptPatchedNodeIds
                  : (Array.isArray(immediateOut?.debug?.promptPatchedNodeIds) ? immediateOut.debug.promptPatchedNodeIds : []),
                videoStatus: 'done',
                videoError: '',
                videoJobId: startedMeta.jobId,
              });
            }
            clearActiveComfyVideoJob(sceneId, { status: "done", jobId: startedMeta.jobId });
            shouldStartPolling = false;
          } else if (immediateStatus === "error" || immediateStatus === "stopped" || immediateStatus === "not_found") {
            updateComfySceneById(sceneId, {
              videoStatus: immediateStatus,
              videoError: String(immediateOut?.error || immediateOut?.hint || "video_job_failed"),
              videoJobId: startedMeta.jobId,
            });
            clearActiveComfyVideoJob(sceneId, { status: immediateStatus, jobId: startedMeta.jobId });
            shouldStartPolling = false;
          } else {
            updateComfySceneById(sceneId, {
              videoStatus: immediateStatus,
              videoError: '',
              videoJobId: startedMeta.jobId,
            });
          }
        } catch (immediateStatusError) {
          console.warn("[CLIP WARN] immediate comfy video status check failed, fallback to polling", {
            sceneId,
            jobId: startedMeta.jobId,
            error: String(immediateStatusError?.message || immediateStatusError || ""),
          });
        }

        if (!shouldStartPolling) return;

        if (CLIP_TRACE_VIDEO_POLLING) {
          console.info("[CLIP TRACE] state update", {
            source: "handleComfyGenerateVideo:updateComfyScene:queued",
            sceneId,
            jobId: String(out.jobId || ""),
          });
          console.info("[CLIP TRACE] about to start comfy polling", {
            sceneId,
            jobId: String(out.jobId || ""),
            out,
          });
        }
        startComfyVideoPolling({
          ...startedMeta,
        });
        return;
      }

      const legacyOut = await fetchJson('/api/clip/video', {
        method: 'POST',
        body: {
          sceneId,
          imageUrl: normalizedVideoSourceUrls.imageUrl,
          startImageUrl: normalizedVideoSourceUrls.startImageUrl,
          endImageUrl: normalizedVideoSourceUrls.endImageUrl,
          audioSliceUrl: normalizedVideoSourceUrls.audioSliceUrl,
          continuationSourceAssetUrl: normalizedVideoSourceUrls.continuationSourceAssetUrl,
          videoPrompt: syncedVideoPrompt,
          transitionActionPrompt: contextPrompt,
          requestedDurationSec: Number(comfySelectedScene.generationDurationSec) || Math.ceil(Number(comfySelectedScene.durationSec) || 3),
          shotType: String(comfySelectedScene.sceneNarrativeStep || ''),
          sceneType: String(comfySelectedScene.sceneGoal || ''),
          format: comfyScenarioFormat,
          provider: 'comfy_remote',
        },
      });
      if (!legacyOut?.ok || !legacyOut?.videoUrl) throw new Error(legacyOut?.hint || legacyOut?.code || 'video_generation_failed');
      updateComfyScene(comfySafeIndex, { videoUrl: String(legacyOut.videoUrl || ''), videoPanelOpen: true, videoStatus: 'done', videoError: '', videoJobId: '' });
    } catch (e) {
      traceScenarioVideo("[SCENARIO VIDEO TRACE ERROR] start_flow_exception", {
        sceneId,
        selectedSceneId: requestedSceneId,
        nodeId: targetNodeId,
        workflowKey: "",
        lipSyncRoute: false,
        hasImageUrl: false,
        hasAudioSliceUrl: false,
        videoStatus: "error",
        branch: String(e?.message || e || ""),
      });
      console.error(e);
      updateComfyScene(comfySafeIndex, { videoStatus: 'error', videoError: String(e?.message || e) });
    }
  }, [buildComfyVideoJobMeta, clearActiveComfyVideoJob, comfyHasActiveVideoJobForScene, comfyNode?.data?.mode, comfyNode?.data?.stylePreset, comfySafeIndex, comfySelectedScene, ensureComfyPromptSynced, findComfySceneIndexById, persistActiveComfyVideoJob, startComfyVideoPolling, updateComfySceneById]);

  const handleComfyDeleteVideo = useCallback(() => {
    const selectedSceneId = String(comfySelectedScene?.sceneId || '').trim();
    if (selectedSceneId) {
      clearActiveComfyVideoJob(selectedSceneId);
    }
    updateComfyScene(comfySafeIndex, { videoUrl: '', videoStatus: '', videoError: '', videoJobId: '' });
  }, [clearActiveComfyVideoJob, comfySafeIndex, comfySelectedScene?.sceneId, updateComfyScene]);

  const handleComfyTakeAudio = useCallback(async () => {
    if (!comfySelectedScene) return;
    if (!globalAudioUrlRaw) {
      const msg = "Не найден общий audioUrl в Audio node";
      updateComfyScene(comfySafeIndex, {
        audioSliceStatus: "error",
        audioSliceError: msg,
        audioSliceLoadError: msg,
      });
      return;
    }

    const sceneId = String(comfySelectedScene?.sceneId || "").trim();
    if (!sceneId) throw new Error("scene_id_required");
    const startSec = Number(comfySelectedScene?.startSec ?? comfySelectedScene?.start ?? comfySelectedScene?.t0 ?? 0);
    const endSec = Number(comfySelectedScene?.endSec ?? comfySelectedScene?.end ?? comfySelectedScene?.t1 ?? startSec);
    const expectedDuration = Math.max(0, endSec - startSec);

    setComfyAudioSliceLoading(true);
    updateComfyScene(comfySafeIndex, {
      audioSliceStatus: "loading",
      audioSliceError: "",
      audioSliceLoadError: "",
      audioSliceStartSec: startSec,
      audioSliceEndSec: endSec,
      audioSliceT0: startSec,
      audioSliceT1: endSec,
      audioSliceDurationSec: expectedDuration,
      audioSliceExpectedDurationSec: expectedDuration,
    });

    try {
      const out = await fetchJson("/api/clip/audio/slice", {
        method: "POST",
        body: {
          sceneId,
          startSec,
          endSec,
          audioUrl: globalAudioUrlRaw,
          audioStoryMode: String(comfyNode?.data?.plannerMeta?.audioStoryMode || comfyNode?.data?.audioStoryMode || ""),
          lipSync: Boolean(comfySelectedScene?.lipSync ?? comfySelectedScene?.lip_sync),
          renderMode: String(comfySelectedScene?.renderMode || comfySelectedScene?.render_mode || ""),
          ltxMode: String(comfySelectedScene?.ltxMode || comfySelectedScene?.ltx_mode || ""),
          resolvedWorkflowKey: String(comfySelectedScene?.resolvedWorkflowKey || comfySelectedScene?.resolved_workflow_key || ""),
          requiresAudioSensitiveVideo: Boolean(comfySelectedScene?.requiresAudioSensitiveVideo ?? comfySelectedScene?.requires_audio_sensitive_video),
        },
      });
      if (!out?.ok || !out?.audioSliceUrl) throw new Error(out?.hint || out?.code || "audio_slice_failed");
      const outStartSec = Number(out?.startSec ?? out?.t0 ?? startSec);
      const outEndSec = Number(out?.endSec ?? out?.t1 ?? endSec);
      const durationSec = normalizeDurationSec(out?.durationSec ?? out?.audioSliceBackendDurationSec ?? out?.duration);
      const nextExpectedDuration = Math.max(0, outEndSec - outStartSec);
      updateComfyScene(
        comfySafeIndex,
        buildAudioSliceReadyPatch({
          url: String(out.audioSliceUrl || ""),
          startSec: outStartSec,
          endSec: outEndSec,
          durationSec: durationSec ?? nextExpectedDuration,
          expectedDurationSec: nextExpectedDuration,
          backendDurationSec: durationSec,
          speechSafeAdjusted: out?.speechSafeAdjusted,
          speechSafeShiftMs: out?.speechSafeShiftMs,
          sliceMayCutSpeech: out?.sliceMayCutSpeech,
        })
      );
    } catch (e) {
      console.error(e);
      const msg = String(e?.message || e || "audio_slice_failed");
      updateComfyScene(comfySafeIndex, {
        audioSliceStatus: "error",
        audioSliceError: msg,
        audioSliceLoadError: msg,
      });
    } finally {
      setComfyAudioSliceLoading(false);
    }
  }, [comfySafeIndex, comfySelectedScene, globalAudioUrlRaw, updateComfyScene]);

  const handleComfySliceLoadedMetadata = useCallback((event) => {
    if (!comfySelectedScene) return;
    const mediaEl = event?.currentTarget || event?.target || null;
    const duration = mediaEl && Number.isFinite(mediaEl.duration) ? Number(mediaEl.duration) : null;
    updateComfyScene(comfySafeIndex, {
      audioSliceActualDurationSec: duration,
      audioSliceStatus: "ready",
      audioSliceError: "",
      audioSliceLoadError: "",
    });
  }, [comfySafeIndex, comfySelectedScene, updateComfyScene]);

  const handleComfySliceAudioError = useCallback(() => {
    if (!comfySelectedScene) return;
    const msg = "Не удалось загрузить вырезанный mp3-срез. Проверьте URL и наличие файла.";
    updateComfyScene(comfySafeIndex, {
      audioSliceActualDurationSec: null,
      audioSliceStatus: "error",
      audioSliceError: msg,
      audioSliceLoadError: msg,
    });
  }, [comfySafeIndex, comfySelectedScene, updateComfyScene]);

  const handleComfyImagePromptChange = useCallback((value) => {
    const nextValue = String(value || '');
    const hasRu = !!String(comfySelectedScene?.imagePromptRu || '').trim();
    const hasEn = !!String(comfySelectedScene?.imagePromptEn || '').trim();
    const sourceEn = String(comfySelectedScene?.imagePromptEnSource || '').trim();
    if (!hasRu && hasEn && comfySelectedScene?.imagePromptEditorLang === 'en_fallback') {
      const preservedEn = String(comfySelectedScene?.imagePromptEn || '');
      updateComfyScene(comfySafeIndex, {
        imagePromptRu: nextValue,
        imagePromptEn: preservedEn,
        imagePrompt: preservedEn,
        imagePromptEnSource: sourceEn || preservedEn,
        imagePromptEditorValue: nextValue,
        imagePromptEditorLang: 'ru',
        imagePromptSyncStatus: PROMPT_SYNC_STATUS.needsSync,
        imagePromptSyncError: '',
        enPromptPresent: { ...(comfySelectedScene?.enPromptPresent || {}), image: Boolean(preservedEn.trim()) },
        ruPromptMissing: { ...(comfySelectedScene?.ruPromptMissing || {}), image: !nextValue.trim() },
        promptLanguageStatus: { ...(comfySelectedScene?.promptLanguageStatus || {}), image: nextValue.trim() ? 'ru_en_present' : 'ru_missing_en_fallback' },
      });
      scheduleComfyPromptSync({ idx: comfySafeIndex, promptType: 'image', ruText: nextValue });
      return;
    }
    updateComfyScene(comfySafeIndex, {
      imagePromptRu: nextValue,
      imagePromptEditorValue: nextValue,
      imagePromptEditorLang: nextValue.trim() ? 'ru' : (hasEn ? 'en_fallback' : 'missing'),
      imagePromptSyncStatus: PROMPT_SYNC_STATUS.needsSync,
      imagePromptSyncError: '',
      ruPromptMissing: { ...(comfySelectedScene?.ruPromptMissing || {}), image: !nextValue.trim() },
      promptLanguageStatus: { ...(comfySelectedScene?.promptLanguageStatus || {}), image: nextValue.trim() ? (String(comfySelectedScene?.imagePromptEn || '').trim() ? 'ru_en_present' : 'en_missing_ru_only') : (String(comfySelectedScene?.imagePromptEn || '').trim() ? 'ru_missing_en_fallback' : 'missing_both') },
    });
    scheduleComfyPromptSync({ idx: comfySafeIndex, promptType: 'image', ruText: nextValue });
  }, [comfySafeIndex, comfySelectedScene, scheduleComfyPromptSync, updateComfyScene]);

  const handleComfyVideoPromptChange = useCallback((value) => {
    const nextValue = String(value || '');
    const hasRu = !!String(comfySelectedScene?.videoPromptRu || '').trim();
    const hasEn = !!String(comfySelectedScene?.videoPromptEn || '').trim();
    const sourceEn = String(comfySelectedScene?.videoPromptEnSource || '').trim();
    if (!hasRu && hasEn && comfySelectedScene?.videoPromptEditorLang === 'en_fallback') {
      const preservedEn = String(comfySelectedScene?.videoPromptEn || '');
      updateComfyScene(comfySafeIndex, {
        videoPromptRu: nextValue,
        videoPromptEn: preservedEn,
        videoPrompt: preservedEn,
        videoPromptEnSource: sourceEn || preservedEn,
        videoPromptEditorValue: nextValue,
        videoPromptEditorLang: 'ru',
        videoPromptSyncStatus: PROMPT_SYNC_STATUS.needsSync,
        videoPromptSyncError: '',
        enPromptPresent: { ...(comfySelectedScene?.enPromptPresent || {}), video: Boolean(preservedEn.trim()) },
        ruPromptMissing: { ...(comfySelectedScene?.ruPromptMissing || {}), video: !nextValue.trim() },
        promptLanguageStatus: { ...(comfySelectedScene?.promptLanguageStatus || {}), video: nextValue.trim() ? 'ru_en_present' : 'ru_missing_en_fallback' },
      });
      scheduleComfyPromptSync({ idx: comfySafeIndex, promptType: 'video', ruText: nextValue });
      return;
    }
    updateComfyScene(comfySafeIndex, {
      videoPromptRu: nextValue,
      videoPromptEditorValue: nextValue,
      videoPromptEditorLang: nextValue.trim() ? 'ru' : (hasEn ? 'en_fallback' : 'missing'),
      videoPromptSyncStatus: PROMPT_SYNC_STATUS.needsSync,
      videoPromptSyncError: '',
      ruPromptMissing: { ...(comfySelectedScene?.ruPromptMissing || {}), video: !nextValue.trim() },
      promptLanguageStatus: { ...(comfySelectedScene?.promptLanguageStatus || {}), video: nextValue.trim() ? (String(comfySelectedScene?.videoPromptEn || '').trim() ? 'ru_en_present' : 'en_missing_ru_only') : (String(comfySelectedScene?.videoPromptEn || '').trim() ? 'ru_missing_en_fallback' : 'missing_both') },
    });
    scheduleComfyPromptSync({ idx: comfySafeIndex, promptType: 'video', ruText: nextValue });
  }, [comfySafeIndex, comfySelectedScene, scheduleComfyPromptSync, updateComfyScene]);

  const resolveScenarioSceneIndex = useCallback((sceneId = "", scenesInput = null) => {
    const normalizedTarget = String(sceneId || "").trim();
    if (!normalizedTarget) return -1;
    const sourceScenes = Array.isArray(scenesInput) ? scenesInput : scenarioScenes;
    const loweredTarget = normalizedTarget.toLowerCase();
    return sourceScenes.findIndex((sceneItem, idx) => {
      const candidates = [
        sceneItem?.sceneId,
        sceneItem?.scene_id,
        sceneItem?.id,
        `S${idx + 1}`,
      ].map((value) => String(value || "").trim()).filter(Boolean);
      return candidates.includes(normalizedTarget) || candidates.some((value) => value.toLowerCase() === loweredTarget);
    });
  }, [scenarioScenes]);

  const resolveScenarioLiveBinding = useCallback((sceneIdRaw = "", options = {}) => {
    const sceneId = String(sceneIdRaw || "").trim();
    if (!sceneId) return null;
    const targetNodeId = String(options?.nodeId || scenarioFlowSourceNode?.id || "").trim();
    const activeNode = (nodesRef.current || []).find((nodeItem) => nodeItem?.id === targetNodeId) || scenarioFlowSourceNode || null;
    const rawScenes = Array.isArray(activeNode?.data?.scenes) ? activeNode.data.scenes : scenarioScenes;
    const normalizedScenes = normalizeSceneCollectionWithSceneId(rawScenes, "scene");
    const sceneIndex = resolveScenarioSceneIndex(sceneId, normalizedScenes);
    const scene = sceneIndex >= 0 ? normalizedScenes[sceneIndex] : null;
    const storyboardRevision = String(activeNode?.data?.storyboardRevision || "").trim();
    const storyboardSignature = String(
      activeNode?.data?.storyboardSignature
      || buildSceneSignature(normalizedScenes, "scene")
      || ""
    ).trim();
    const sceneSignature = String(buildScenarioScenePackageSignature(scene || {}) || "").trim();
    return { sceneIndex, scene, storyboardRevision, storyboardSignature, sceneSignature };
  }, [resolveScenarioSceneIndex, scenarioFlowSourceNode, scenarioScenes]);

  const handleGenerateScenarioImage = useCallback(async (slot = "single", options = {}) => {
    const targetNodeId = String(scenarioEditor?.nodeId || scenarioFlowSourceNode?.id || "").trim();
    const targetNode = (nodesRef.current || []).find((nodeItem) => nodeItem?.id === targetNodeId) || scenarioFlowSourceNode || null;
    const targetRawScenes = Array.isArray(targetNode?.data?.scenes)
      ? targetNode.data.scenes
      : (Array.isArray(options?.rawScenes) ? options.rawScenes : []);
    const targetScenes = normalizeSceneCollectionWithSceneId(targetRawScenes, "scene");
    const requestedSceneIndex = Number.isInteger(options?.sceneIndex) ? options.sceneIndex : scenarioEditor.selected;
    const requestedSceneId = String(options?.sceneId || options?.selectedSceneId || scenarioEditor?.selectedSceneId || scenarioSelected?.sceneId || "").trim();
    const resolvedSceneIndex = requestedSceneId ? resolveScenarioSceneIndex(requestedSceneId, targetScenes) : -1;
    const targetSceneIndex = resolvedSceneIndex >= 0
      ? resolvedSceneIndex
      : (requestedSceneIndex >= 0 ? requestedSceneIndex : -1);
    const targetScene = targetScenes[targetSceneIndex] || null;
    const requestSceneId = String(targetScene?.sceneId || requestedSceneId || "").trim();
    if (!targetNodeId || !targetNode) {
      console.error("[SCENARIO EDITOR IMAGE EARLY RETURN] missing_target_node", {
        targetNodeId,
        hasTargetNode: !!targetNode,
        requestedSceneId,
        requestedSceneIndex,
      });
      setScenarioImageError("Не найден source node для Scenario image generate.");
      notify({ type: "error", title: "Scenario node missing", message: "Откройте storyboard node и попробуйте снова." });
      return;
    }
    if (CLIP_TRACE_SCENARIO_EDITOR_GENERATE) {
      console.warn("[SCENARIO EDITOR IMAGE CLICK]", {
        clicked: true,
        "options.sceneIndex": Number.isInteger(options?.sceneIndex) ? options.sceneIndex : null,
        "options.sceneId": requestedSceneId || null,
        "scenarioEditor.selected": scenarioEditor.selected,
        "scenarioScenes.length": targetScenes.length,
        activeTab: String(options?.activeTab || options?.selectedTab || "unknown"),
        selectedTab: String(options?.selectedTab || options?.activeTab || "unknown"),
        requestedSceneIndex,
        resolvedSceneIndex,
        targetSceneIndex,
        targetSceneFound: !!targetScene,
      });
    }
    if (CLIP_TRACE_SCENARIO_EDITOR_GENERATE) {
      console.debug("[SCENARIO GENERATE ROUTE]", {
        actionType: "image",
        editorNodeId: String(scenarioEditor?.nodeId || ""),
        sourceNodeIdUsedByHandler: String(scenarioFlowSourceNode?.id || ""),
        selectedSceneId: String(targetScene?.sceneId || ""),
        resolvedSceneFound: !!targetScene,
      });
    }
    if (!targetScene) {
      const lookupMap = targetScenes.map((sceneItem, idx) => ({
        idx,
        sceneId: String(sceneItem?.sceneId || ""),
        scene_id: String(sceneItem?.scene_id || ""),
        id: String(sceneItem?.id || ""),
      }));
      const rawSceneIds = targetRawScenes.map((sceneItem, idx) => String(
        sceneItem?.sceneId || sceneItem?.scene_id || sceneItem?.id || `S${idx + 1}` || ""
      ).trim()).filter(Boolean);
      console.error("[SCENARIO EDITOR IMAGE EARLY RETURN] no_target_scene", {
        selectedSceneId: requestedSceneId,
        selectedSceneIndex: requestedSceneIndex,
        targetSceneIndex,
        requestedSceneIndex,
        requestedSceneId,
        resolvedSceneIndex,
        normalizedSceneIds: lookupMap.map((item) => item.sceneId || item.scene_id || item.id),
        rawStoryboardSceneIds: rawSceneIds,
        normalizedScenesLength: targetScenes.length,
        rawScenesLength: targetRawScenes.length,
        sceneCount: targetScenes.length,
        lookupMap,
        targetNodeId,
      });
      setScenarioImageError("Не удалось определить сцену для генерации изображения.");
      notify({ type: "error", title: "Scene not selected", message: "Выберите сцену и повторите генерацию изображения." });
      return;
    }
    if (requestedSceneId && requestSceneId && requestedSceneId !== requestSceneId) {
      console.warn("[SCENARIO EDITOR IMAGE REQUEST REMAPPED]", {
        requestedSceneId,
        requestSceneId,
        requestedSceneIndex,
        targetSceneIndex,
        targetNodeId,
      });
    }

    const imageStrategy = String(targetScene?.imageStrategy || deriveScenarioImageStrategy(targetScene)).trim().toLowerCase() || "single";
    const requestedSlot = slot === "start" || slot === "end" ? slot : "single";
    const normalizedSlot = imageStrategy === "first_last"
      ? (requestedSlot === "end" ? "end" : "start")
      : (imageStrategy === "continuation" ? (requestedSlot === "end" ? "end" : "start") : "single");
    console.debug("[SCENARIO IMAGE] generation_mode_resolved", {
      sceneId: String(targetScene?.sceneId || ""),
      imageStrategy,
      requestedSlot,
      normalizedSlot,
    });
    if ((imageStrategy === "continuation" || imageStrategy === "first_last") && normalizedSlot === "start" && !!targetScene?.inheritPreviousEndAsStart) {
      console.error("[SCENARIO EDITOR IMAGE EARLY RETURN] blocked_by_inherit_previous_end_as_start", {
        sceneId: String(targetScene?.sceneId || ""),
        targetSceneIndex,
        imageStrategy,
        normalizedSlot,
      });
      return;
    }
    const sceneId = requestSceneId;
    if (!sceneId) throw new Error("scene_id_required");
    const requestStoryboardRevision = String(targetNode?.data?.storyboardRevision || "").trim();
    const requestStoryboardSignature = String(
      targetNode?.data?.storyboardSignature
      || buildSceneSignature(targetScenes, "scene")
      || ""
    ).trim();
    const requestSceneSignature = String(buildScenarioScenePackageSignature(targetScene || {}) || "").trim();
    const requestSceneStableSignature = String(buildScenarioSceneStableSignature(targetScene || {}) || "").trim();
    const shouldTraceSelectedScene = shouldTraceRoleContractScene(sceneId);
    const sceneText = String(targetScene.sceneText || targetScene.visualDescription || "").trim();
    const previousScene = targetSceneIndex > 0 ? targetScenes[targetSceneIndex - 1] : null;
    const previousSceneImageUrl = String(
      previousScene?.endImageUrl
      || previousScene?.imageUrl
      || previousScene?.startImageUrl
      || ""
    ).trim();
    const previousContinuityMemory = targetScene.previousContinuityMemory
      || previousScene?.continuityMemory
      || null;
    const imageFormat = resolvePreferredSceneFormat(
      targetScene?.format,
      targetScene?.imageFormat
    );
    const { width, height } = getSceneImageSize(imageFormat);

    const sceneDelta = getSceneFramePromptByStrategy(targetScene, normalizedSlot);
    const continuityContractLines = [
      "FIRST_LAST CONTINUITY CONTRACT:",
      "- same characters and same identity",
      "- same clothing, accessories, hairstyle",
      "- same environment/location",
      "- same time, weather, and light family",
      "- same framing class / lens family / scale",
      "- no outfit redesign, no hairstyle redesign, no location swap, no extra people",
      "- change only pose / distance / emotion / body orientation / interaction intensity / scene state",
    ];
    const firstLastContinuityClause = continuityContractLines.join("\n");

    if (!sceneDelta) {
      console.error("[SCENARIO EDITOR IMAGE EARLY RETURN] missing_scene_delta", {
        sceneId,
        targetSceneIndex,
        imageStrategy,
        normalizedSlot,
      });
      setScenarioImageError("Добавьте prompt для генерации кадра");
      return;
    }
    const visualGlueText = buildScenarioVisualGlueText(targetScene);
    const applyFirstLastContinuityContract = imageStrategy === "first_last" && normalizedSlot === "end";
    const finalSceneDelta = `${visualGlueText}\n\n${sceneDelta}${applyFirstLastContinuityContract ? `\n\n${firstLastContinuityClause}` : ""}`.trim();

    setScenarioImageLoading(true);
    setScenarioImageError("");
    try {
      const scenarioContractPayload = buildScenarioSceneContractPayload(targetScene);
      const scenarioContractPayloadSanitized = {
        ...scenarioContractPayload,
      };
      const scenarioPackageForImage = targetNode?.data?.scenarioPackage && typeof targetNode.data.scenarioPackage === "object"
        ? targetNode.data.scenarioPackage
        : {};
      const refsByRoleForImage = buildScenarioRefsByRoleForImage({
        scene: targetScene,
        scenarioBrainRefs,
        scenarioPackage: scenarioPackageForImage,
      });
      if (CLIP_TRACE_SCENARIO_IMAGE_PAYLOAD) {
        const sceneRefs = extractScenarioRefsByRoleFromSource(targetScene?.refsByRole);
        const scenarioPackageRefs = extractScenarioRefsByRoleFromSource(scenarioPackageForImage?.refsByRole);
        const contextRefs = extractScenarioRefsByRoleFromSource(
          targetScene?.connected_context_summary?.context_refs
          || targetScene?.connectedContextSummary?.context_refs
          || targetScene?.context_refs
          || targetScene?.contextRefs
          || scenarioPackageForImage?.connected_context_summary?.context_refs
          || scenarioPackageForImage?.context_refs
          || {}
        );
        const contextRefCountsByRole = summarizeRefsByRole(contextRefs);
        const hasContextRefs = SCENARIO_IMAGE_ROLE_KEYS.some((role) => Number(contextRefCountsByRole?.[role] || 0) > 0);
        console.debug("[SCENARIO IMAGE SOURCE]", {
          sceneId,
          hasSceneRefsByRole: SCENARIO_IMAGE_ROLE_KEYS.some((role) => Array.isArray(sceneRefs?.[role]) && sceneRefs[role].length > 0),
          hasScenarioPackageRefsByRole: SCENARIO_IMAGE_ROLE_KEYS.some((role) => Array.isArray(scenarioPackageRefs?.[role]) && scenarioPackageRefs[role].length > 0),
          hasContextRefs,
          contextRefCountsByRole,
        });
      }
      const derivedRoleContract = buildScenarioRoleContractForImage({
        scene: targetScene,
        refsByRole: refsByRoleForImage,
      });
      if (shouldTraceSelectedScene) {
        console.debug("[SCENARIO ROLE TRACE] targetScene.before_buildScenarioRoleContractForImage", {
          sceneId,
          primaryRole: targetScene?.primaryRole || "",
          secondaryRoles: Array.isArray(targetScene?.secondaryRoles) ? targetScene.secondaryRoles : [],
          sceneActiveRoles: Array.isArray(targetScene?.sceneActiveRoles) ? targetScene.sceneActiveRoles : [],
          refsUsed: Array.isArray(targetScene?.refsUsed) ? targetScene.refsUsed : [],
          mustAppear: Array.isArray(targetScene?.mustAppear) ? targetScene.mustAppear : [],
          refsUsedByRoleKeys: Object.keys((targetScene?.refsUsedByRole && typeof targetScene.refsUsedByRole === "object") ? targetScene.refsUsedByRole : {}),
          refsByRoleCounts: summarizeRefsByRole(refsByRoleForImage),
        });
        console.debug("[SCENARIO ROLE TRACE] resolvedRoleContract.after_buildScenarioRoleContractForImage", {
          sceneId,
          ...derivedRoleContract,
          hasCharacter2InSceneActiveRoles: Array.isArray(derivedRoleContract?.sceneActiveRoles) && derivedRoleContract.sceneActiveRoles.includes("character_2"),
        });
      }
      const currentSceneForEndReference = applyFirstLastContinuityContract
        ? (resolveScenarioLiveBinding(sceneId, { nodeId: targetNodeId })?.scene || targetScene)
        : targetScene;
      const directCurrentSceneStartImageUrl = applyFirstLastContinuityContract
        ? String(
          currentSceneForEndReference?.startImageUrl
          || currentSceneForEndReference?.startFrameImageUrl
          || ""
        ).trim()
        : "";
      const effectiveCurrentSceneStartImageUrl = applyFirstLastContinuityContract
        ? String(getEffectiveSceneStartImage(currentSceneForEndReference, null) || "").trim()
        : "";
      const currentSceneStartImageUrl = applyFirstLastContinuityContract
        ? (directCurrentSceneStartImageUrl || effectiveCurrentSceneStartImageUrl)
        : "";
      const firstFrameReferenceUrlForEnd = currentSceneStartImageUrl;
      console.debug("[SCENARIO FIRST_LAST] end_reference_resolved", {
        sceneId,
        imageStrategy,
        normalizedSlot,
        hasCurrentSceneStartImage: Boolean(currentSceneStartImageUrl),
        directCurrentSceneStartImageUrl,
        effectiveCurrentSceneStartImageUrl,
        firstFrameReferenceUrl: firstFrameReferenceUrlForEnd,
      });
      const refsForImageRequest = normalizeClipImageRefsPayload({
        ...scenarioBrainRefs,
        refsByRole: refsByRoleForImage,
        refsUsed: derivedRoleContract.refsUsed,
        refsUsedByRole: targetScene?.refsUsedByRole,
        primaryRole: derivedRoleContract.primaryRole,
        secondaryRoles: derivedRoleContract.secondaryRoles,
        sceneActiveRoles: derivedRoleContract.sceneActiveRoles,
        mustAppear: Array.isArray(targetScene?.mustAppear) && targetScene.mustAppear.length
          ? targetScene.mustAppear
          : derivedRoleContract.mustAppear,
        mustNotAppear: Array.isArray(derivedRoleContract?.mustNotAppear) ? derivedRoleContract.mustNotAppear : [],
        participants: Array.isArray(targetScene?.actors) ? targetScene.actors : [],
        previousContinuityMemory,
        previousSceneImageUrl,
        firstFrameReferenceUrl: firstFrameReferenceUrlForEnd,
        currentSceneStartImageUrl,
      });
      if (applyFirstLastContinuityContract) {
        console.info("[FIRST_LAST END REQUEST SOURCE]", {
          sceneId,
          slot: normalizedSlot,
          startImageUrl: String(currentSceneForEndReference?.startImageUrl || "").trim(),
          startFrameImageUrl: String(currentSceneForEndReference?.startFrameImageUrl || "").trim(),
          fallbackImageUrl: String(currentSceneForEndReference?.imageUrl || "").trim(),
          firstFrameReferenceUrlForEnd,
          currentSceneStartImageUrl,
          willAttachFirstFrameReference: Boolean(firstFrameReferenceUrlForEnd),
        });
      }
      if (applyFirstLastContinuityContract) {
        console.info("[FIRST_LAST CONSISTENCY CONTRACT]", {
          sceneId,
          hasStartFrame: Boolean(firstFrameReferenceUrlForEnd),
          hasEndFrame: Boolean(String(targetScene?.endImageUrl || targetScene?.endFrameImageUrl || "").trim()),
          startFrameUsedAsReferenceForEnd: Boolean(firstFrameReferenceUrlForEnd),
          sameWardrobeLock: true,
          sameLocationLock: true,
          sameIdentityLock: true,
          sameCameraFamilyLock: true,
        });
      }
      const refsByRoleCounts = summarizeRefsByRole(refsForImageRequest?.refsByRole || {});
      const sourceRefsCandidates = [
        { label: "scene.refsByRole", value: targetScene?.refsByRole },
        { label: "scene.connectedRefsByRole", value: targetScene?.connectedRefsByRole },
        { label: "scene.context_refs", value: targetScene?.context_refs },
        { label: "scene.contextRefs", value: targetScene?.contextRefs },
        { label: "scene.connected_context_summary.context_refs", value: targetScene?.connected_context_summary?.context_refs },
        { label: "scene.connectedContextSummary.context_refs", value: targetScene?.connectedContextSummary?.context_refs },
        { label: "scene.connected_context_summary.contextRefs", value: targetScene?.connected_context_summary?.contextRefs },
        { label: "scene.connectedContextSummary.contextRefs", value: targetScene?.connectedContextSummary?.contextRefs },
        { label: "package.refsByRole", value: scenarioPackageForImage?.refsByRole },
        { label: "package.connectedRefsByRole", value: scenarioPackageForImage?.connectedRefsByRole },
        { label: "package.context_refs", value: scenarioPackageForImage?.context_refs },
        { label: "package.contextRefs", value: scenarioPackageForImage?.contextRefs },
        { label: "package.connected_context_summary.context_refs", value: scenarioPackageForImage?.connected_context_summary?.context_refs },
        { label: "package.connectedContextSummary.context_refs", value: scenarioPackageForImage?.connectedContextSummary?.context_refs },
        { label: "package.connected_context_summary.contextRefs", value: scenarioPackageForImage?.connected_context_summary?.contextRefs },
        { label: "package.connectedContextSummary.contextRefs", value: scenarioPackageForImage?.connectedContextSummary?.contextRefs },
      ];
      const sourceRefsEvidenceLabels = [];
      const sourceRefsByRole = sourceRefsCandidates.reduce((acc, candidate) => {
        const extracted = extractScenarioRefsByRoleFromSource(candidate?.value);
        if (hasNonEmptyRefsByRole(extracted)) {
          sourceRefsEvidenceLabels.push(candidate.label);
        }
        return mergeScenarioRefsByRole(acc, extracted);
      }, {});
      const sourceRefsKeys = getNonEmptyRefRoleKeys(sourceRefsByRole);
      const finalRefsKeys = getNonEmptyRefRoleKeys(refsForImageRequest?.refsByRole || {});
      const hadRoleAwareContract = [
        targetScene?.primaryRole,
        ...(Array.isArray(targetScene?.secondaryRoles) ? targetScene.secondaryRoles : []),
        ...(Array.isArray(targetScene?.sceneActiveRoles) ? targetScene.sceneActiveRoles : []),
        ...(Array.isArray(targetScene?.refsUsed) ? targetScene.refsUsed : []),
        ...(Array.isArray(targetScene?.mustAppear) ? targetScene.mustAppear : []),
        ...(Array.isArray(scenarioPackageForImage?.heroParticipants) ? scenarioPackageForImage.heroParticipants : []),
        ...(Array.isArray(scenarioPackageForImage?.supportingParticipants) ? scenarioPackageForImage.supportingParticipants : []),
        ...(Array.isArray(scenarioPackageForImage?.mustAppearRoles) ? scenarioPackageForImage.mustAppearRoles : []),
      ].some((value) => String(value || "").trim());
      const scenarioBrainRefsByRole = extractScenarioRefsByRoleFromSource(scenarioBrainRefs?.refsByRole);
      const scenarioBrainHasRefsByRole = hasNonEmptyRefsByRole(scenarioBrainRefsByRole);
      if (scenarioBrainHasRefsByRole) sourceRefsEvidenceLabels.push("scenarioBrain.refsByRole");
      const hadSourceRefs = sourceRefsKeys.length > 0 || scenarioBrainHasRefsByRole;
      const finalHasRefs = hasNonEmptyRefsByRole(refsForImageRequest?.refsByRole || {});
      const guardTriggered = hadSourceRefs && !finalHasRefs;
      const sourceRefEvidence = sourceRefsEvidenceLabels.length ? Array.from(new Set(sourceRefsEvidenceLabels)).join("|") : "none";
      const scenarioGuardDebug = {
        sceneIndex: targetSceneIndex,
        sceneId,
        hadSourceRefs,
        hadRoleAwareContract,
        sourceRefsKeys,
        finalRefsKeys,
        sourceRefEvidence,
        primaryRole: refsForImageRequest?.primaryRole || derivedRoleContract?.primaryRole || "",
        secondaryRoles: refsForImageRequest?.secondaryRoles || derivedRoleContract?.secondaryRoles || [],
        sceneActiveRoles: refsForImageRequest?.sceneActiveRoles || derivedRoleContract?.sceneActiveRoles || [],
        refsUsed: refsForImageRequest?.refsUsed || derivedRoleContract?.refsUsed || [],
        mustAppear: refsForImageRequest?.mustAppear || derivedRoleContract?.mustAppear || [],
        guardTriggered,
      };
      console.debug("[SCENARIO IMAGE GUARD]", scenarioGuardDebug);
      if (guardTriggered) {
        console.warn("[SCENARIO IMAGE ROLE-AWARE DEGRADATION]", {
          sceneId,
          reason: "source_refs_present_but_refs_for_image_request_empty",
          refsByRoleCounts: summarizeRefsByRole(sourceRefsByRole),
          sceneActiveRoles: refsForImageRequest?.sceneActiveRoles || derivedRoleContract?.sceneActiveRoles || [],
          primaryRole: refsForImageRequest?.primaryRole || derivedRoleContract?.primaryRole || "",
          mustAppear: refsForImageRequest?.mustAppear || derivedRoleContract?.mustAppear || [],
        });
        console.error("[SCENARIO EDITOR IMAGE EARLY RETURN] guard_triggered", scenarioGuardDebug);
        const guardError = new Error("scenario_refs_lost_before_clip_image");
        guardError.code = "scenario_refs_lost_before_clip_image";
        guardError.details = {
          ...scenarioGuardDebug,
          reason: "source refs existed in scenario inputs but final image request refsByRole is empty",
        };
        console.error("[SCENARIO IMAGE GUARD] code=scenario_refs_lost_before_clip_image", guardError.details);
        throw guardError;
      }
      if (CLIP_TRACE_SCENARIO_IMAGE_PAYLOAD || CLIP_TRACE_SCENARIO_EDITOR_GENERATE) {
        console.debug("[SCENARIO IMAGE REQUEST SUMMARY]", {
          nodeId: targetNodeId,
          selectedIndex: scenarioEditor.selected,
          selectedSceneId: String(scenarioEditor?.selectedSceneId || "").trim(),
          targetSceneIndex,
          requestSceneId: sceneId,
          scenarioSelectedSceneId: String(scenarioSelected?.sceneId || "").trim(),
          scenarioSelectedIndexResolved: scenarioSelectedIndex,
          refsByRoleCounts,
          refsUsed: refsForImageRequest?.refsUsed || [],
          sceneActiveRoles: refsForImageRequest?.sceneActiveRoles || [],
          primaryRole: refsForImageRequest?.primaryRole || "",
          secondaryRoles: refsForImageRequest?.secondaryRoles || [],
          mustAppear: refsForImageRequest?.mustAppear || [],
          mustNotAppear: refsForImageRequest?.mustNotAppear || [],
          hasLegacyCharacterRefs: Array.isArray(refsForImageRequest?.character) && refsForImageRequest.character.length > 0,
          hasRoleAwareRefs: Object.values(refsByRoleCounts || {}).some((count) => Number(count || 0) > 0),
        });
      }
      if (CLIP_TRACE_SCENARIO_IMAGE_PAYLOAD) {
        const refsSummary = summarizeRefsByRole(refsForImageRequest?.refsByRole || {});
        console.debug("[SCENARIO IMAGE PAYLOAD]", {
          sceneId,
          refsByRole: refsSummary,
          primaryRole: refsForImageRequest?.primaryRole || "",
          secondaryRoles: refsForImageRequest?.secondaryRoles || [],
          sceneActiveRoles: refsForImageRequest?.sceneActiveRoles || [],
          refsUsed: refsForImageRequest?.refsUsed || [],
          mustAppear: refsForImageRequest?.mustAppear || [],
        });
      }
      const promptSourceSummary = String(
        scenarioContractPayload?.promptSource
        || refsForImageRequest?.promptSource
        || targetScene?.promptSource
        || "unknown"
      ).trim();
      console.debug("[SCENARIO IMAGE REQUEST CONTEXT]", {
        nodeId: targetNodeId,
        activeNodeId: String(scenarioFlowSourceNode?.id || ""),
        sceneId,
        sceneIndex: targetSceneIndex,
        storyboardRevision: requestStoryboardRevision,
        storyboardSignature: requestStoryboardSignature,
        sceneSignature: requestSceneSignature,
        slot: normalizedSlot,
        selectedTab: String(options?.selectedTab || options?.activeTab || ""),
        promptSourceSummary,
        hasSceneGoal: !!String(targetScene?.sceneGoalRu || targetScene?.sceneGoalEn || targetScene?.sceneGoal || "").trim(),
        hasPrompt: !!String(finalSceneDelta || "").trim(),
        hasRefs: hasNonEmptyRefsByRole(refsForImageRequest?.refsByRole || {}),
      });
      if (shouldTraceSelectedScene) {
        console.debug("[SCENARIO ROLE TRACE] refs_payload.before_api_clip_image", {
          sceneId,
          primaryRole: refsForImageRequest?.primaryRole || "",
          secondaryRoles: refsForImageRequest?.secondaryRoles || [],
          sceneActiveRoles: refsForImageRequest?.sceneActiveRoles || [],
          refsUsed: refsForImageRequest?.refsUsed || [],
          mustAppear: refsForImageRequest?.mustAppear || [],
          refsByRoleCounts: summarizeRefsByRole(refsForImageRequest?.refsByRole || {}),
          hasCharacter2InRefsPayload: Array.isArray(refsForImageRequest?.sceneActiveRoles) && refsForImageRequest.sceneActiveRoles.includes("character_2"),
          hasCharacter2Refs: Number(summarizeRefsByRole(refsForImageRequest?.refsByRole || {})?.character_2 || 0) > 0,
        });
      }
      console.debug("[SCENE IMAGE STRATEGY]", {
        sceneId,
        ltxMode: String(targetScene?.ltxMode || ""),
        needsTwoFrames: Boolean(targetScene?.needsTwoFrames),
        continuation: Boolean(targetScene?.continuationFromPrevious ?? targetScene?.continuation),
        imageStrategy,
        hasImagePrompt: !!getScenePrimaryFramePrompt(targetScene),
        hasStartFramePrompt: !!String(targetScene?.startFramePromptRu || targetScene?.startFramePrompt || deriveFirstLastFramePrompts(targetScene || {}).start || "").trim(),
        hasEndFramePrompt: !!String(targetScene?.endFramePromptRu || targetScene?.endFramePrompt || deriveFirstLastFramePrompts(targetScene || {}).end || "").trim(),
      });
      if (CLIP_TRACE_VISUAL_LOCK) {
        console.debug("[SCENARIO VISUAL LOCK] image prompt", {
          sceneId,
          renderMode: String(targetScene?.renderMode || ""),
          ltxMode: String(targetScene?.ltxMode || ""),
          hasGlobalVisualLock: hasScenarioContractValue(targetScene?.globalVisualLock),
          hasGlobalCameraProfile: hasScenarioContractValue(targetScene?.globalCameraProfile),
          finalPromptLength: finalSceneDelta.length,
          hasEnvironmentLock: hasScenarioContractValue(targetScene?.environmentLock),
          hasStyleLock: hasScenarioContractValue(targetScene?.styleLock),
          hasIdentityLock: hasScenarioContractValue(targetScene?.identityLock),
        });
      }
      if (CLIP_TRACE_SCENARIO_TRANSFER) {
        console.debug("[SCENARIO TRANSFER] before /api/clip/image", buildScenarioTransferLogData(targetScene, scenarioContractPayload));
      }
      const heroAppearanceContractPayload = scenarioContractPayload?.heroAppearanceContract;
      const outfitProfilePayload = scenarioContractPayload?.effectiveOutfitProfile || scenarioContractPayload?.outfitProfile || scenarioContractPayload?.sourceOutfitProfile;
      console.debug("[SCENARIO IMAGE CONTRACT DEBUG] before /api/clip/image", {
        sceneId,
        hasHeroAppearanceContract: hasScenarioContractValue(heroAppearanceContractPayload),
        hasSourceOutfitProfile: hasScenarioContractValue(scenarioContractPayload?.sourceOutfitProfile),
        hasEffectiveOutfitProfile: hasScenarioContractValue(scenarioContractPayload?.effectiveOutfitProfile),
        hasConfidenceScores: hasScenarioContractValue(scenarioContractPayload?.confidenceScores),
        heroAppearanceContractKeys: heroAppearanceContractPayload && typeof heroAppearanceContractPayload === "object" ? Object.keys(heroAppearanceContractPayload) : [],
        outfitProfileKeys: outfitProfilePayload && typeof outfitProfilePayload === "object" ? Object.keys(outfitProfilePayload) : [],
        hasLocationContinuityContract: hasScenarioContractValue(scenarioContractPayload?.locationContinuityContract),
      });
      if (shouldTraceSelectedScene) {
        const finalTargetSceneForImage = {
          primaryRole: scenarioContractPayload?.primaryRole || refsForImageRequest?.primaryRole || "",
          secondaryRoles: (Array.isArray(scenarioContractPayload?.secondaryRoles) && scenarioContractPayload.secondaryRoles.length)
            ? scenarioContractPayload.secondaryRoles
            : (refsForImageRequest?.secondaryRoles || []),
          sceneActiveRoles: (Array.isArray(scenarioContractPayload?.sceneActiveRoles) && scenarioContractPayload.sceneActiveRoles.length)
            ? scenarioContractPayload.sceneActiveRoles
            : (refsForImageRequest?.sceneActiveRoles || []),
          refsUsed: (Array.isArray(scenarioContractPayload?.refsUsed) && scenarioContractPayload.refsUsed.length)
            ? scenarioContractPayload.refsUsed
            : (refsForImageRequest?.refsUsed || []),
          mustAppear: (Array.isArray(scenarioContractPayload?.mustAppear) && scenarioContractPayload.mustAppear.length)
            ? scenarioContractPayload.mustAppear
            : (refsForImageRequest?.mustAppear || []),
        };
        console.debug("[SCENARIO ROLE TRACE] targetScene.final_before_api_clip_image", {
          sceneId,
          ...finalTargetSceneForImage,
          hasCharacter2InSceneActiveRoles: Array.isArray(finalTargetSceneForImage.sceneActiveRoles) && finalTargetSceneForImage.sceneActiveRoles.includes("character_2"),
        });
      }
      const out = await fetchJson(`/api/clip/image`, {
        method: "POST",
        body: {
          sceneId,
          sceneDelta: `${finalSceneDelta}
Aspect ratio: ${imageFormat}`,
          sceneText,
          width,
          height,
          storyboardRevision: requestStoryboardRevision,
          storyboardSignature: requestStoryboardSignature,
          sceneSignature: requestSceneSignature,
          slot: normalizedSlot,
          sceneContract: scenarioContractPayloadSanitized,
          ...scenarioContractPayload,
          primaryRole: scenarioContractPayload?.primaryRole || refsForImageRequest?.primaryRole || "",
          secondaryRoles: (Array.isArray(scenarioContractPayload?.secondaryRoles) && scenarioContractPayload.secondaryRoles.length)
            ? scenarioContractPayload.secondaryRoles
            : (refsForImageRequest?.secondaryRoles || []),
          sceneActiveRoles: (Array.isArray(scenarioContractPayload?.sceneActiveRoles) && scenarioContractPayload.sceneActiveRoles.length)
            ? scenarioContractPayload.sceneActiveRoles
            : (refsForImageRequest?.sceneActiveRoles || []),
          refsUsed: (Array.isArray(scenarioContractPayload?.refsUsed) && scenarioContractPayload.refsUsed.length)
            ? scenarioContractPayload.refsUsed
            : (refsForImageRequest?.refsUsed || []),
          mustAppear: (Array.isArray(scenarioContractPayload?.mustAppear) && scenarioContractPayload.mustAppear.length)
            ? scenarioContractPayload.mustAppear
            : (refsForImageRequest?.mustAppear || []),
          refs: refsForImageRequest,
        },
      });
      if (CLIP_TRACE_SCENARIO_IMAGE_E2E) {
        console.debug("[SCENARIO IMAGE RESPONSE RECEIVED]", {
          requestedSceneId: sceneId,
          responseOk: Boolean(out?.ok),
          responseSceneId: String(out?.sceneId || "").trim(),
          hasImageUrl: !!String(out?.imageUrl || "").trim(),
          resultStatus: String(out?.resultStatus || "").trim(),
          degraded: Boolean(out?.degraded || String(out?.engine || "").trim().toLowerCase() === "mock"),
          slot: normalizedSlot,
        });
      }
      if (!out?.ok || !out?.imageUrl) {
        const responseError = new Error(String(out?.hint || out?.message || out?.code || "image_generation_failed"));
        responseError.code = String(out?.code || "").trim();
        responseError.hint = String(out?.hint || "").trim();
        responseError.status = Number(out?.status) || null;
        responseError.payload = out;
        throw responseError;
      }

      const generatedImageUrl = String(out?.imageUrl || "");
      const imageDegraded = Boolean(
        out?.degraded
        || String(out?.resultStatus || "").trim().toLowerCase() === "degraded"
        || String(out?.engine || "").trim().toLowerCase() === "mock"
      );
      const imageDegradeReason = String(out?.degradeReason || out?.hint || "").trim();
      const responseSceneId = String(out?.sceneId || "").trim();
      const liveBinding = resolveScenarioLiveBinding(sceneId, { nodeId: targetNodeId });
      const liveSceneStableSignature = String(buildScenarioSceneStableSignature(liveBinding?.scene || {}) || "").trim();
      const reasonIgnored = [];
      if (responseSceneId && responseSceneId !== sceneId) reasonIgnored.push("response_scene_mismatch");
      if (!generatedImageUrl) reasonIgnored.push("image_url_missing");
      if (!liveBinding || !liveBinding.scene) reasonIgnored.push("scene_missing_in_live_storyboard");
      if (liveBinding?.storyboardRevision && requestStoryboardRevision && liveBinding.storyboardRevision !== requestStoryboardRevision) {
        reasonIgnored.push("storyboard_revision_mismatch");
      }
      if (liveBinding?.storyboardSignature && requestStoryboardSignature && liveBinding.storyboardSignature !== requestStoryboardSignature) {
        reasonIgnored.push("storyboard_signature_mismatch");
      }
      const sceneSignatureMismatch = !!(liveBinding?.sceneSignature && requestSceneSignature && liveBinding.sceneSignature !== requestSceneSignature);
      if (sceneSignatureMismatch && requestSceneStableSignature && liveSceneStableSignature && requestSceneStableSignature !== liveSceneStableSignature) {
        reasonIgnored.push("scene_signature_mismatch");
      }
      const applyAccepted = reasonIgnored.length === 0;
      if (CLIP_TRACE_SCENARIO_IMAGE_E2E) {
        console.debug("[SCENARIO IMAGE RESPONSE APPLY]", {
          requestedSceneId: sceneId,
          responseSceneId: responseSceneId || sceneId,
          applyAccepted,
          reasonIgnored: reasonIgnored.join("|") || "",
          requestStoryboardRevision,
          requestStoryboardSignature,
          requestSceneSignature,
          requestSceneStableSignature,
          liveSceneStableSignature,
          "liveBinding?.sceneIndex": liveBinding?.sceneIndex,
          "liveBinding?.scene?.sceneId": String(liveBinding?.scene?.sceneId || "").trim(),
          "liveBinding?.storyboardRevision": String(liveBinding?.storyboardRevision || "").trim(),
          "liveBinding?.storyboardSignature": String(liveBinding?.storyboardSignature || "").trim(),
          "liveBinding?.sceneSignature": String(liveBinding?.sceneSignature || "").trim(),
          imageUrl: generatedImageUrl,
          slot: normalizedSlot,
        });
        console.debug("[SCENARIO LIVE BINDING TARGET]", {
          targetNodeId,
          scenarioFlowSourceNodeId: String(scenarioFlowSourceNode?.id || "").trim(),
          requestSceneId: sceneId,
          liveBindingSceneId: String(liveBinding?.scene?.sceneId || "").trim(),
          liveBindingSceneIndex: Number.isInteger(liveBinding?.sceneIndex) ? liveBinding.sceneIndex : null,
          liveBindingStoryboardRevision: String(liveBinding?.storyboardRevision || "").trim(),
          liveBindingStoryboardSignature: String(liveBinding?.storyboardSignature || "").trim(),
          liveBindingSceneSignature: String(liveBinding?.sceneSignature || "").trim(),
        });
        if (targetNodeId !== String(scenarioFlowSourceNode?.id || "").trim()) {
          console.warn("[SCENARIO LIVE BINDING TARGET MISMATCH]", {
            targetNodeId,
            scenarioFlowSourceNodeId: String(scenarioFlowSourceNode?.id || "").trim(),
            requestSceneId: sceneId,
            liveBindingSceneId: String(liveBinding?.scene?.sceneId || "").trim(),
          });
        }
      }
      if (!applyAccepted) {
        const runtimeResetPatch = normalizedSlot === "start"
          ? { startFrameStatus: "idle", startFrameError: "" }
          : normalizedSlot === "end"
            ? { endFrameStatus: "idle", endFrameError: "" }
            : { imageStatus: "idle", imageError: "" };
        updateScenarioSceneGenerationRuntime(sceneId, runtimeResetPatch, { nodeId: targetNodeId });
        if (CLIP_TRACE_SCENARIO_IMAGE_E2E) {
          const selectedPreviewSources = resolveScenarioScenePreviewSources(scenarioSelected, scenarioPreviousScene);
          console.debug("[SCENARIO IMAGE E2E TRACE]", {
            requestedSceneId: sceneId,
            responseOk: Boolean(out?.ok),
            applyAccepted,
            patchedNodeId: targetNodeId,
            patchedSceneId: sceneId,
            patchedImageUrl: "",
            selectedNodeId: String(scenarioFlowSourceNode?.id || ""),
            selectedSceneId: String(scenarioSelected?.sceneId || ""),
            selectedSceneImageStrategy: selectedPreviewSources.imageStrategy,
            selectedSceneTransitionType: selectedPreviewSources.transitionType,
            resolvedPreviewSrc: selectedPreviewSources.resolvedPreviewSrc,
            resolvedStartPreviewSrc: selectedPreviewSources.resolvedStartPreviewSrc,
            resolvedEndPreviewSrc: selectedPreviewSources.resolvedEndPreviewSrc,
            assetsPreservedAfterRebind: false,
            finalVisible: Boolean(selectedPreviewSources.resolvedPreviewSrc),
          });
        }
        return;
      }
      let runtimeImagePatch = {};
      if ((imageStrategy === "continuation" || imageStrategy === "first_last") && normalizedSlot === "start") {
        updateScenarioScene(sceneId, {
          startImageUrl: generatedImageUrl,
          startFrameImageUrl: generatedImageUrl,
          imageDegraded,
          imageDegradeReason,
          imageHint: String(out?.hint || "").trim(),
          imageFormat,
          videoUrl: "",
          videoStatus: "",
          videoError: "",
          videoJobId: "",
          videoSourceImageUrl: "",
          videoPanelActivated: false,
        }, { nodeId: targetNodeId });
        runtimeImagePatch = {
          startFrameStatus: imageDegraded ? "degraded" : "done",
          startFrameError: imageDegraded ? imageDegradeReason : "",
          imageDegraded,
        };
      } else if ((imageStrategy === "continuation" || imageStrategy === "first_last") && normalizedSlot === "end") {
        updateScenarioScene(sceneId, {
          endImageUrl: generatedImageUrl,
          endFrameImageUrl: generatedImageUrl,
          imageDegraded,
          imageDegradeReason,
          imageHint: String(out?.hint || "").trim(),
          imageFormat,
          videoUrl: "",
          videoStatus: "",
          videoError: "",
          videoJobId: "",
          videoSourceImageUrl: "",
          videoPanelActivated: false,
        }, { nodeId: targetNodeId });
        runtimeImagePatch = {
          endFrameStatus: imageDegraded ? "degraded" : "done",
          endFrameError: imageDegraded ? imageDegradeReason : "",
          imageDegraded,
        };
      } else {
        updateScenarioScene(sceneId, {
          imageUrl: generatedImageUrl,
          imageStatus: imageDegraded ? "degraded" : "done",
          imageDegraded,
          imageDegradeReason,
          imageHint: String(out?.hint || "").trim(),
          imageFormat,
          videoUrl: "",
          videoStatus: "",
          videoError: "",
          videoJobId: "",
          videoSourceImageUrl: "",
          videoPanelActivated: false,
        }, { nodeId: targetNodeId });
        runtimeImagePatch = {
          imageStatus: imageDegraded ? "degraded" : "done",
          imageError: imageDegraded ? imageDegradeReason : "",
          imageDegraded,
        };
      }
      updateScenarioSceneGenerationRuntime(sceneId, runtimeImagePatch, { nodeId: targetNodeId });
      if (CLIP_TRACE_SCENARIO_IMAGE_E2E) {
        console.debug("[SCENARIO IMAGE SCENE PATCHED]", {
          sceneId,
          slot: normalizedSlot,
          targetNodeId,
          imageUrl: generatedImageUrl,
          patchApplied: true,
        });
        console.debug("[SCENARIO IMAGE STATUS SYNC]", {
          sceneId,
          slot: normalizedSlot,
          status: "done",
          hasImageUrl: !!generatedImageUrl,
          error: "",
        });
      }
      clearActiveVideoJob(sceneId);
      setScenarioVideoOpen(false);
      console.log("[StoryboardVideo] image_generated_reset_video_stage", {
        sceneId,
        slot: normalizedSlot,
      });
      window.setTimeout(() => {
        const nodeNow = (nodesRef.current || []).find((nodeItem) => nodeItem?.id === targetNodeId) || null;
        const scenesNow = Array.isArray(nodeNow?.data?.scenes) ? nodeNow.data.scenes : [];
        const selectedSceneIdNow = String(scenarioEditor?.selectedSceneId || scenarioSelected?.sceneId || "").trim();
        const patchedSceneNow = scenesNow.find((sceneItem) => String(sceneItem?.sceneId || "").trim() === sceneId) || null;
        const selectedSceneNow = scenesNow.find((sceneItem) => String(sceneItem?.sceneId || "").trim() === selectedSceneIdNow) || null;
        const patchedImageUrl = String(
          normalizedSlot === "start"
            ? (patchedSceneNow?.startImageUrl || "")
            : normalizedSlot === "end"
              ? (patchedSceneNow?.endImageUrl || "")
              : (patchedSceneNow?.imageUrl || "")
        ).trim();
        const selectedSceneIndexNow = scenesNow.findIndex((sceneItem) => String(sceneItem?.sceneId || "").trim() === selectedSceneIdNow);
        const selectedPreviousSceneNow = selectedSceneIndexNow > 0 ? scenesNow[selectedSceneIndexNow - 1] : null;
        const selectedPreviewSources = resolveScenarioScenePreviewSources(selectedSceneNow, selectedPreviousSceneNow);
        const assetsPreservedAfterRebind = !!patchedImageUrl;
        const patchedScenePreviewSources = resolveScenarioScenePreviewSources(patchedSceneNow, selectedPreviousSceneNow);
        const resolvedPreviewSrcEditor = selectedPreviewSources.resolvedPreviewSrc;
        const resolvedPreviewSrcMain = patchedScenePreviewSources.resolvedPreviewSrc || selectedPreviewSources.resolvedPreviewSrc;
        if (CLIP_TRACE_SCENARIO_IMAGE_E2E || CLIP_TRACE_SCENARIO_EDITOR_GENERATE) {
          console.debug("[SCENARIO IMAGE FINAL VISIBILITY]", {
            nodeId: targetNodeId,
            requestSceneId: sceneId,
            selectedSceneId: selectedSceneIdNow,
            patchedSceneId: String(patchedSceneNow?.sceneId || sceneId),
            imageUrlStoredInPatchedScene: patchedImageUrl,
            selectedSceneImageUrl: String(selectedSceneNow?.imageUrl || "").trim(),
            resolvedPreviewSrcMain,
            resolvedPreviewSrcEditor,
            mainPreviewVisible: Boolean(resolvedPreviewSrcMain),
            editorPreviewVisible: Boolean(resolvedPreviewSrcEditor),
          });
        }
        if (CLIP_TRACE_SCENARIO_IMAGE_E2E) {
          console.debug("[SCENARIO IMAGE E2E TRACE]", {
            requestedSceneId: sceneId,
            responseOk: Boolean(out?.ok),
            applyAccepted,
            patchedNodeId: targetNodeId,
            patchedSceneId: String(patchedSceneNow?.sceneId || sceneId),
            patchedImageUrl,
            selectedNodeId: String(scenarioFlowSourceNode?.id || ""),
            selectedSceneId: selectedSceneIdNow,
            selectedSceneImageStrategy: selectedPreviewSources.imageStrategy,
            selectedSceneTransitionType: selectedPreviewSources.transitionType,
            resolvedPreviewSrc: selectedPreviewSources.resolvedPreviewSrc,
            resolvedStartPreviewSrc: selectedPreviewSources.resolvedStartPreviewSrc,
            resolvedEndPreviewSrc: selectedPreviewSources.resolvedEndPreviewSrc,
            assetsPreservedAfterRebind,
            finalVisible: Boolean(selectedPreviewSources.resolvedPreviewSrc),
          });
        }
      }, 0);
      if (imageStrategy === "first_last" && requestedSlot === "single") {
        await handleGenerateScenarioImage("end", { sceneIndex: targetSceneIndex, sceneId });
      }
    } catch (e) {
      console.error(e);
      const errorHint = String(e?.hint || e?.payload?.hint || "").trim();
      const errorCode = String(e?.code || e?.payload?.code || "").trim();
      const errorStatus = Number(e?.status ?? e?.payload?.status);
      const baseMessage = String(e?.message || e?.payload?.detail || e?.payload?.error || e || "").trim();
      const imageErrorMessage = errorHint
        || [baseMessage, errorCode && !baseMessage.includes(errorCode) ? `(${errorCode})` : ""].filter(Boolean).join(" ")
        || (Number.isFinite(errorStatus) ? `HTTP ${errorStatus}` : "Image generation failed");
      const runtimeErrorPatch = normalizedSlot === "start"
        ? { startFrameStatus: "error", startFrameError: imageErrorMessage }
        : normalizedSlot === "end"
          ? { endFrameStatus: "error", endFrameError: imageErrorMessage }
          : { imageStatus: "error", imageError: imageErrorMessage };
      updateScenarioSceneGenerationRuntime(sceneId, runtimeErrorPatch, { nodeId: targetNodeId });
      if (CLIP_TRACE_SCENARIO_IMAGE_E2E) {
        console.debug("[SCENARIO IMAGE STATUS SYNC]", {
          sceneId,
          slot: normalizedSlot,
          status: "error",
          hasImageUrl: false,
          error: imageErrorMessage,
        });
      }
      setScenarioImageError(imageErrorMessage);
    } finally {
      setScenarioImageLoading(false);
    }
  }, [clearActiveVideoJob, notify, resolveScenarioLiveBinding, resolveScenarioSceneIndex, scenarioEditor?.nodeId, scenarioEditor.selected, scenarioFlowSourceNode?.id, scenarioFlowSourceNode?.data?.scenarioPackage, scenarioFlowSourceNode?.data?.storyboardRevision, scenarioFlowSourceNode?.data?.storyboardSignature, scenarioScenes, scenarioSelected?.sceneId, scenarioBrainRefs, updateScenarioScene, updateScenarioSceneGenerationRuntime]);

  const handleClearScenarioImage = useCallback((slot = "single") => {
    setScenarioImageError("");
    const transitionType = resolveSceneTransitionType(scenarioSelected);
    const normalizedSlot = slot === "start" || slot === "end" ? slot : "single";
    if (transitionType === "continuous" && normalizedSlot === "start" && !!scenarioSelected?.inheritPreviousEndAsStart) {
      return;
    }
    if (transitionType === "continuous" && normalizedSlot === "start") {
      updateScenarioScene(scenarioSelectedIndex, {
        startImageUrl: "",
        videoUrl: "",
        videoStatus: "",
        videoError: "",
        videoJobId: "",
        videoSourceImageUrl: "",
        videoPanelActivated: false,
      });
      const sceneId = String(scenarioSelected?.sceneId || "").trim();
    if (!sceneId) throw new Error("scene_id_required");
      clearActiveVideoJob(sceneId);
      setScenarioVideoOpen(false);
      return;
    }
    if (transitionType === "continuous" && normalizedSlot === "end") {
      updateScenarioScene(scenarioSelectedIndex, {
        endImageUrl: "",
        videoUrl: "",
        videoStatus: "",
        videoError: "",
        videoJobId: "",
        videoSourceImageUrl: "",
        videoPanelActivated: false,
      });
      const sceneId = String(scenarioSelected?.sceneId || "").trim();
    if (!sceneId) throw new Error("scene_id_required");
      clearActiveVideoJob(sceneId);
      setScenarioVideoOpen(false);
      return;
    }
    const sceneId = String(scenarioSelected?.sceneId || "").trim();
    if (!sceneId) throw new Error("scene_id_required");
    updateScenarioScene(scenarioSelectedIndex, {
      imageUrl: "",
      videoUrl: "",
      videoSourceImageUrl: "",
      videoPanelActivated: false,
      videoStatus: "",
      videoError: "",
      videoJobId: "",
    });
    clearActiveVideoJob(sceneId);
    setScenarioVideoOpen(false);
  }, [clearActiveVideoJob, scenarioSelected, scenarioSelectedIndex, updateScenarioScene]);

  const handleScenarioTakeAudioByIndex = useCallback(async (idx, options = {}) => {
    const scene = scenarioScenes[idx] || null;
    if (!scene) return null;
    const explicitAudioUrl = String(options?.audioUrl || "").trim();
    const explicitSourceNode = options?.sourceNode || null;
    const explicitSourceNodeAudioUrl = explicitSourceNode
      ? String(resolveScenarioAudioSourceUrlFromNode(explicitSourceNode, nodesRef.current || []) || "").trim()
      : "";
    const scenarioFlowAudioUrl = String(resolveScenarioAudioSourceUrlFromNode(scenarioFlowSourceNode, nodesRef.current || []) || "").trim();
    const globalAudioUrl = String(extractGlobalAudioUrlFromNodes(nodesRef.current || []) || "").trim();
    const scenarioAudioUrl = explicitAudioUrl || explicitSourceNodeAudioUrl || scenarioFlowAudioUrl || globalAudioUrl;
    const sourceKind = explicitAudioUrl
      ? "explicit_audio_url"
      : explicitSourceNodeAudioUrl
        ? "explicit_source_node"
        : scenarioFlowAudioUrl
          ? "scenario_flow_source_node"
          : globalAudioUrl
            ? "global_audio_node"
            : "missing";
    const sceneId = String(scene?.sceneId || "").trim();
    console.debug("[SCENARIO AUDIO SOURCE RESOLVED]", {
      sceneId,
      source: sourceKind,
      resolvedAudioUrl: scenarioAudioUrl,
      scenarioNodeId: String(scenarioFlowSourceNode?.id || ""),
      overrideNodeId: String(explicitSourceNode?.id || ""),
    });
    if (!scenarioAudioUrl) {
      const msg = "Не найден актуальный audioUrl для Scenario";
      updateScenarioScene(idx, {
        audioSliceStatus: "error",
        audioSliceError: msg,
        audioSliceLoadError: msg,
      });
      if (idx === scenarioEditor.selected) setScenarioVideoError(msg);
      return null;
    }

    if (!sceneId) throw new Error("scene_id_required");
    const startSec = Number(scene.t0 ?? scene.start ?? 0);
    const endSec = Number(scene.t1 ?? scene.end ?? 0);

    console.log("[StoryboardVideo] audio_loading_on reason=take_audio", { sceneId, startSec, endSec });
    if (idx === scenarioEditor.selected) {
      setScenarioAudioSliceLoading(true);
      setScenarioVideoError("");
    }
    updateScenarioScene(idx, {
      audioSliceStatus: "loading",
      audioSliceError: "",
      audioSliceLoadError: "",
      audioSliceStartSec: startSec,
      audioSliceEndSec: endSec,
      audioSliceT0: startSec,
      audioSliceT1: endSec,
      audioSliceDurationSec: Math.max(0, endSec - startSec),
      audioSliceExpectedDurationSec: Math.max(0, endSec - startSec),
    });

    try {
      console.debug("[SCENARIO AUDIO SLICE REQUEST]", {
        endpoint: "/api/clip/audio/slice",
        sceneId,
        audioUrl: scenarioAudioUrl,
        startSec,
        endSec,
        expectedDurationSec: Math.max(0, endSec - startSec),
      });
      const out = await fetchJson("/api/clip/audio/slice", {
        method: "POST",
        body: {
          sceneId,
          startSec,
          endSec,
          audioUrl: scenarioAudioUrl,
          audioStoryMode: String(scenarioSelected?.audioStoryMode || ""),
          lipSync: Boolean(scene?.lipSync ?? scene?.lip_sync),
          renderMode: String(scene?.renderMode || scene?.render_mode || ""),
          ltxMode: String(scene?.ltxMode || scene?.ltx_mode || ""),
          resolvedWorkflowKey: String(scene?.resolvedWorkflowKey || scene?.resolved_workflow_key || ""),
          requiresAudioSensitiveVideo: Boolean(scene?.requiresAudioSensitiveVideo ?? scene?.requires_audio_sensitive_video),
        },
      });
      const resolvedSliceUrl = String(out?.audioSliceUrl || out?.sliceUrl || "").trim();
      if (!out?.ok || !resolvedSliceUrl) throw new Error(out?.hint || out?.code || "audio_slice_failed");
      const outStartSec = Number(out?.startSec ?? out?.t0 ?? startSec);
      const outEndSec = Number(out?.endSec ?? out?.t1 ?? endSec);
      const durationSec = normalizeDurationSec(out?.durationSec ?? out?.audioSliceBackendDurationSec ?? out?.duration);
      const expectedDuration = Math.max(0, outEndSec - outStartSec);
      const resolvedAudioSliceKind = String(out?.audioSliceKind || out?.audio_slice_kind || "").trim().toLowerCase();
      const resolvedMusicVocalLipSyncAllowed = out?.musicVocalLipSyncAllowed ?? out?.music_vocal_lipsync_allowed;
      const resolvedRequiresAudioSensitiveVideo = out?.requiresAudioSensitiveVideo ?? out?.requires_audio_sensitive_video;
      console.debug("[SCENARIO AUDIO SLICE RESPONSE]", {
        ok: Boolean(out?.ok),
        sceneId,
        sliceUrl: resolvedSliceUrl,
        usedAudioUrl: String(out?.audioUrl || scenarioAudioUrl || ""),
        actualDurationSec: durationSec,
        audioSliceKind: resolvedAudioSliceKind || "none",
        musicVocalLipSyncAllowed: resolvedMusicVocalLipSyncAllowed == null ? null : Boolean(resolvedMusicVocalLipSyncAllowed),
        renderMode: String(scene?.renderMode || "").trim().toLowerCase(),
        ltxMode: String(scene?.ltxMode || "").trim().toLowerCase(),
        error: "",
      });
      updateScenarioScene(
        idx,
        buildAudioSliceReadyPatch({
          url: resolvedSliceUrl,
          startSec: outStartSec,
          endSec: outEndSec,
          durationSec: durationSec ?? expectedDuration,
          expectedDurationSec: expectedDuration,
          backendDurationSec: durationSec,
          audioSliceKind: resolvedAudioSliceKind,
          musicVocalLipSyncAllowed: resolvedMusicVocalLipSyncAllowed,
          requiresAudioSensitiveVideo: resolvedRequiresAudioSensitiveVideo,
          speechSafeAdjusted: out?.speechSafeAdjusted,
          speechSafeShiftMs: out?.speechSafeShiftMs,
          sliceMayCutSpeech: out?.sliceMayCutSpeech,
        })
      );
      return {
        audioSliceUrl: resolvedSliceUrl,
        sliceUrl: resolvedSliceUrl,
        audioSliceDurationSec: durationSec ?? expectedDuration,
        audioSliceStartSec: outStartSec,
        audioSliceEndSec: outEndSec,
        audioSliceStatus: "ready",
        audioSliceKind: resolvedAudioSliceKind,
        musicVocalLipSyncAllowed: resolvedMusicVocalLipSyncAllowed == null ? undefined : Boolean(resolvedMusicVocalLipSyncAllowed),
        requiresAudioSensitiveVideo: resolvedRequiresAudioSensitiveVideo == null ? undefined : Boolean(resolvedRequiresAudioSensitiveVideo),
      };
    } catch (e) {
      console.error(e);
      const msg = String(e?.message || e || "audio_slice_failed");
      console.debug("[SCENARIO AUDIO SLICE RESPONSE]", {
        ok: false,
        sceneId,
        sliceUrl: "",
        usedAudioUrl: scenarioAudioUrl,
        actualDurationSec: null,
        error: msg,
      });
      updateScenarioScene(idx, {
        audioSliceStatus: "error",
        audioSliceError: msg,
        audioSliceLoadError: msg,
      });
      if (idx === scenarioEditor.selected) setScenarioVideoError(msg);
      return null;
    } finally {
      if (idx === scenarioEditor.selected) setScenarioAudioSliceLoading(false);
    }
  }, [scenarioEditor.selected, scenarioFlowSourceNode, scenarioScenes, scenarioSelected?.audioStoryMode, updateScenarioScene]);

  const handleScenarioVideoTakeAudio = useCallback(async () => {
    if (!scenarioSelected) return;
    await handleScenarioTakeAudioByIndex(scenarioEditor.selected);
  }, [handleScenarioTakeAudioByIndex, scenarioEditor.selected, scenarioSelected]);

  const handleScenarioEditorExtractSceneAudio = useCallback(async (nodeId, sceneId) => {
    const normalizedSceneId = String(sceneId || "").trim();
    if (!normalizedSceneId) throw new Error("scene_id_required");
    const sourceNode = (nodesRef.current || []).find((nodeItem) => nodeItem?.id === nodeId && nodeItem?.type === "scenarioStoryboard") || null;
    const rawScenes = Array.isArray(sourceNode?.data?.scenes) ? sourceNode.data.scenes : [];
    const normalizedScenes = normalizeSceneCollectionWithSceneId(rawScenes, "scene");
    const sceneIndex = resolveScenarioSceneIndex(normalizedSceneId, normalizedScenes);
    if (sceneIndex < 0) throw new Error(`scene_not_found:${normalizedSceneId}`);
    const selectedScene = normalizedScenes[sceneIndex] || {};
    const selectedAudioUrl = resolveScenarioAudioSourceUrlFromNode(sourceNode, nodesRef.current || []);
    const audioNodeUrl = extractGlobalAudioUrlFromNodes(nodesRef.current || []);
    console.debug("[SCENARIO PHRASE PREVIEW CLICK]", {
      sceneId: normalizedSceneId,
      phraseText: String(selectedScene?.localPhrase || selectedScene?.sceneText || "").trim(),
      sceneAudioSliceStart: Number(selectedScene?.audioSliceStartSec ?? selectedScene?.t0 ?? selectedScene?.start ?? 0),
      sceneAudioSliceEnd: Number(selectedScene?.audioSliceEndSec ?? selectedScene?.t1 ?? selectedScene?.end ?? 0),
      selectedAudioUrl,
      audioNodeUrl,
      scenarioNodeId: String(nodeId || ""),
      sourceNodeId: String(sourceNode?.id || ""),
    });
    const directResult = await handleScenarioTakeAudioByIndex(sceneIndex, {
      audioUrl: selectedAudioUrl,
      sourceNode,
    });
    const normalizedDirectUrl = String(directResult?.audioSliceUrl || directResult?.sliceUrl || "").trim();
    if (normalizedDirectUrl) {
      return {
        audioSliceUrl: normalizedDirectUrl,
        audioSliceDurationSec: normalizeDurationSec(directResult?.audioSliceDurationSec),
        audioSliceStartSec: Number(directResult?.audioSliceStartSec),
        audioSliceEndSec: Number(directResult?.audioSliceEndSec),
        audioSliceStatus: String(directResult?.audioSliceStatus || "").trim(),
        audioSliceKind: String(directResult?.audioSliceKind || directResult?.audio_slice_kind || "").trim().toLowerCase(),
        musicVocalLipSyncAllowed: directResult?.musicVocalLipSyncAllowed ?? directResult?.music_vocal_lipsync_allowed,
        requiresAudioSensitiveVideo: directResult?.requiresAudioSensitiveVideo ?? directResult?.requires_audio_sensitive_video,
      };
    }

    const refreshedNode = (nodesRef.current || []).find((nodeItem) => nodeItem?.id === nodeId && nodeItem?.type === "scenarioStoryboard") || null;
    const refreshedScenes = normalizeSceneCollectionWithSceneId(Array.isArray(refreshedNode?.data?.scenes) ? refreshedNode.data.scenes : [], "scene");
    const refreshedScene = refreshedScenes[sceneIndex] || null;
    return {
      audioSliceUrl: String(refreshedScene?.audioSliceUrl || "").trim(),
      audioSliceDurationSec: normalizeDurationSec(refreshedScene?.audioSliceDurationSec ?? refreshedScene?.audioSliceActualDurationSec ?? refreshedScene?.audioSliceExpectedDurationSec),
      audioSliceStartSec: Number(refreshedScene?.audioSliceStartSec ?? refreshedScene?.audioSliceT0),
      audioSliceEndSec: Number(refreshedScene?.audioSliceEndSec ?? refreshedScene?.audioSliceT1),
      audioSliceStatus: String(refreshedScene?.audioSliceStatus || "").trim(),
      audioSliceKind: String(refreshedScene?.audioSliceKind || refreshedScene?.audio_slice_kind || "").trim().toLowerCase(),
      musicVocalLipSyncAllowed: refreshedScene?.musicVocalLipSyncAllowed ?? refreshedScene?.music_vocal_lipsync_allowed,
      requiresAudioSensitiveVideo: refreshedScene?.requiresAudioSensitiveVideo ?? refreshedScene?.requires_audio_sensitive_video,
    };
  }, [handleScenarioTakeAudioByIndex, resolveScenarioSceneIndex]);

  const handleScenarioSliceLoadedMetadata = useCallback((event) => {
    if (!scenarioSelected) return;
    const mediaEl = event?.currentTarget || event?.target || null;
    const duration = mediaEl && Number.isFinite(mediaEl.duration) ? Number(mediaEl.duration) : null;
    updateScenarioScene(scenarioEditor.selected, {
      audioSliceActualDurationSec: duration,
      audioSliceStatus: "ready",
      audioSliceError: "",
      audioSliceLoadError: "",
    });
  }, [scenarioEditor.selected, scenarioSelected, updateScenarioScene]);

  const handleScenarioSliceAudioError = useCallback(() => {
    if (!scenarioSelected) return;
    const msg = "Не удалось загрузить вырезанный mp3-срез. Проверьте URL и наличие файла.";
    updateScenarioScene(scenarioEditor.selected, {
      audioSliceActualDurationSec: null,
      audioSliceStatus: "error",
      audioSliceError: msg,
      audioSliceLoadError: msg,
    });
    setScenarioVideoError(msg);
  }, [scenarioEditor.selected, scenarioSelected, updateScenarioScene]);

  const openNextSceneWithoutVideo = useCallback((currentIdx) => {
    const safeIdx = Number(currentIdx);
    if (!Number.isFinite(safeIdx) || safeIdx < 0) return;
    const startFrom = safeIdx + 1;
    if (!Array.isArray(scenarioScenes) || startFrom >= scenarioScenes.length) {
      setAssemblyInfo("Все сцены готовы. Можно собирать клип.");
      return;
    }

    const nextIdx = scenarioScenes.findIndex((scene, idx) => idx >= startFrom && !String(scene?.videoUrl || "").trim());
    if (nextIdx >= 0) {
      setScenarioEditor((prev) => ({ ...prev, selected: nextIdx, selectedSceneId: String((scenarioScenes[nextIdx] || {}).sceneId || "").trim() }));
      return;
    }

    setAssemblyInfo("Все сцены готовы. Можно собирать клип.");
  }, [scenarioScenes]);

  const handleScenarioGenerateVideo = useCallback(async (options = {}) => {
    const traceScenarioVideo = (label, payload = {}) => {
      console.info(label, {
        sceneId: String(payload?.sceneId || "").trim(),
        selectedSceneId: String(payload?.selectedSceneId || "").trim(),
        nodeId: String(payload?.nodeId || "").trim(),
        workflowKey: String(payload?.workflowKey || "").trim(),
        lipSyncRoute: Boolean(payload?.lipSyncRoute),
        hasImageUrl: Boolean(payload?.hasImageUrl),
        hasAudioSliceUrl: Boolean(payload?.hasAudioSliceUrl),
        videoStatus: String(payload?.videoStatus || "").trim(),
        branch: String(payload?.branch || "").trim(),
      });
    };
    traceScenarioVideo("[SCENARIO VIDEO TRACE 4] handleScenarioGenerateVideo_enter", {
      selectedSceneId: String(options?.sceneId || options?.selectedSceneId || scenarioEditor?.selectedSceneId || "").trim(),
      nodeId: String(options?.nodeId || scenarioEditor?.nodeId || scenarioFlowSourceNode?.id || "").trim(),
      branch: "enter",
    });
    console.info("[SCENARIO VIDEO FLOW]", {
      stage: "enter",
      options,
      selectedIndex: scenarioEditor.selected,
      selectedSceneId: String(scenarioEditor?.selectedSceneId || scenarioSelected?.sceneId || "").trim(),
    });
    const logScenarioVideoBlocked = (stage, reason, extra = {}) => {
      console.warn("[SCENARIO VIDEO BLOCKED]", {
        stage,
        sceneId: String(extra?.sceneId || "").trim(),
        reason: String(reason || "").trim(),
        workflow: String(extra?.workflow || "").trim(),
        renderMode: String(extra?.renderMode || "").trim(),
        hasImageUrl: Boolean(extra?.hasImageUrl),
        hasStartImageUrl: Boolean(extra?.hasStartImageUrl),
        hasEndImageUrl: Boolean(extra?.hasEndImageUrl),
        hasAudioSliceUrl: Boolean(extra?.hasAudioSliceUrl),
        selectedIndex: Number.isInteger(extra?.selectedIndex) ? extra.selectedIndex : null,
      });
    };
    const logScenarioDirectGenerateBlock = ({ route = "", url = "", reason = "", sceneId: blockedSceneId = "" } = {}) => {
      console.warn("[SCENARIO DIRECT GENERATE BLOCK]", {
        route: String(route || "").trim(),
        url: String(url || "").trim(),
        reason: String(reason || "").trim(),
        sceneId: String(blockedSceneId || "").trim(),
      });
    };
    const targetNodeId = String(scenarioEditor?.nodeId || scenarioFlowSourceNode?.id || "").trim();
    const targetNode = (nodesRef.current || []).find((nodeItem) => nodeItem?.id === targetNodeId) || scenarioFlowSourceNode || null;
    const targetRawScenes = Array.isArray(targetNode?.data?.scenes) ? targetNode.data.scenes : scenarioScenes;
    const targetScenes = normalizeSceneCollectionWithSceneId(targetRawScenes, "scene");
    const requestedSceneIndex = Number.isInteger(options?.sceneIndex) ? options.sceneIndex : scenarioEditor.selected;
    const requestedSceneId = String(options?.sceneId || options?.selectedSceneId || scenarioEditor?.selectedSceneId || scenarioSelected?.sceneId || "").trim();
    const resolvedSceneIndex = requestedSceneId ? resolveScenarioSceneIndex(requestedSceneId, targetScenes) : -1;
    const targetSceneIndex = resolvedSceneIndex >= 0
      ? resolvedSceneIndex
      : (requestedSceneIndex >= 0 ? requestedSceneIndex : -1);
    const targetScene = targetScenes[targetSceneIndex] || null;
    const sceneId = String(targetScene?.sceneId || requestedSceneId || "").trim();
    console.info("[SCENARIO VIDEO FLOW]", {
      stage: "scene_resolved",
      targetNodeId,
      hasTargetNode: !!targetNode,
      requestedSceneIndex,
      requestedSceneId,
      resolvedSceneIndex,
      targetSceneIndex,
      sceneId,
      scenesLength: targetScenes.length,
      selectedIndex: scenarioEditor.selected,
    });
    traceScenarioVideo("[SCENARIO VIDEO TRACE 5] scene_resolved", {
      sceneId,
      selectedSceneId: requestedSceneId,
      nodeId: targetNodeId,
      workflowKey: String(targetScene?.resolvedWorkflowKey || targetScene?.ltxMode || ""),
      hasImageUrl: Boolean(targetScene?.imageUrl),
      hasAudioSliceUrl: Boolean(targetScene?.audioSliceUrl),
      videoStatus: String(targetScene?.videoStatus || ""),
      branch: targetScene ? "scene_found" : "scene_missing",
    });
    if (CLIP_TRACE_SCENARIO_EDITOR_GENERATE) {
      console.debug("[SCENARIO GENERATE ROUTE]", {
        actionType: "video",
        editorNodeId: String(scenarioEditor?.nodeId || ""),
        sourceNodeIdUsedByHandler: String(scenarioFlowSourceNode?.id || ""),
        requestedSceneIndex,
        requestedSceneId,
        resolvedSceneIndex,
        targetSceneIndex,
        selectedSceneId: String(targetScene?.sceneId || ""),
        resolvedSceneFound: !!targetScene,
        selectedTab: String(options?.selectedTab || options?.activeTab || ""),
      });
    }
    if (!targetScene) {
      logScenarioVideoBlocked("resolve_target_scene", "missing_target_scene", {
        sceneId,
        workflow: "",
        renderMode: "",
        hasImageUrl: false,
        hasStartImageUrl: false,
        hasEndImageUrl: false,
        hasAudioSliceUrl: false,
        selectedIndex: targetSceneIndex,
      });
      return;
    }
    const targetPreviousScene = targetSceneIndex > 0 ? targetScenes[targetSceneIndex - 1] : null;
    const { effectiveStartImageUrl, endImageUrl, fallbackImageUrl, sourceOfTruthKeys } = resolveSceneFrameUrls(targetScene, targetPreviousScene);
    console.info("[SCENARIO VIDEO FLOW]", {
      stage: "source_urls",
      sceneId,
      fallbackImageUrl: String(fallbackImageUrl || "").trim(),
      effectiveStartImageUrl: String(effectiveStartImageUrl || "").trim(),
      endImageUrl: String(endImageUrl || "").trim(),
    });
    const targetEffectiveStartImageUrl = effectiveStartImageUrl;
    const imageStrategy = String(targetScene?.imageStrategy || deriveScenarioImageStrategy(targetScene)).trim().toLowerCase() || "single";
    const explicitWorkflow = resolveScenarioExplicitWorkflowKey(targetScene);
    const resolvedWorkflowKey = normalizeScenarioWorkflowKeyForProduction(
      explicitWorkflow
      || targetScene?.resolvedWorkflowKey
      || resolveScenarioWorkflowKey(targetScene)
    );
    const resolvedWorkflowKeyLower = String(resolvedWorkflowKey || "").trim().toLowerCase();
    const ltxModeNormalized = String(targetScene?.ltxMode || targetScene?.ltx_mode || "").trim().toLowerCase();
    const explicitRequiresTwoFrames = targetScene?.requiresTwoFrames ?? targetScene?.needsTwoFrames;
    const requiresTwoFrames = Boolean(
      explicitRequiresTwoFrames
      ?? (
        imageStrategy === "first_last"
        || resolvedWorkflowKeyLower === "f_l"
        || resolvedWorkflowKeyLower === "imag-imag-video-bz"
        || ltxModeNormalized === "f_l"
        || ltxModeNormalized === "first_last"
      )
    );
    const explicitRequiresContinuation = targetScene?.requiresContinuation ?? targetScene?.continuationFromPrevious;
    const requiresContinuation = Boolean(
      explicitRequiresContinuation
      ?? (
        imageStrategy === "continuation"
        || ltxModeNormalized === "continuation"
      )
    ) && !requiresTwoFrames;
    const fallbackContinuationToI2V = requiresContinuation && !requiresTwoFrames;
    const effectiveRequiresContinuation = fallbackContinuationToI2V ? false : requiresContinuation;
    const effectiveWorkflowKey = fallbackContinuationToI2V ? "i2v" : resolvedWorkflowKey;
    const effectiveContentType = String(
      scenarioFlowSourceNode?.data?.contentType
      || targetScene?.contentType
      || ""
    ).trim().toLowerCase();
    if (fallbackContinuationToI2V) {
      console.warn("[SCENARIO UNSUPPORTED VIDEO MODE]", {
        sceneId: String(targetScene?.sceneId || ""),
        originalLtxMode: String(targetScene?.ltxMode || ""),
        originalRenderMode: String(targetScene?.renderMode || ""),
        fallbackApplied: true,
        fallbackWorkflowKey: "i2v",
        reason: "continuation_execution_not_supported_in_backend",
      });
    }
    const transitionType = requiresTwoFrames
      ? "first_last"
      : (effectiveRequiresContinuation ? "continuous" : "single");
    const frameImageUrl = String(fallbackImageUrl || "").trim();
    const resolvedFirstFrameUrl = String(targetEffectiveStartImageUrl || "").trim();
    const resolvedLastFrameUrl = String(endImageUrl || "").trim();
    const hasImageForVideo = requiresTwoFrames
      ? !!resolvedFirstFrameUrl && !!resolvedLastFrameUrl
      : (effectiveRequiresContinuation
        ? !!(resolvedFirstFrameUrl || frameImageUrl)
        : !!frameImageUrl);
    if (!hasImageForVideo) {
      logScenarioVideoBlocked("validate_images", "missing_required_frame_assets", {
        sceneId,
        workflow: effectiveWorkflowKey,
        renderMode: String(targetScene?.renderMode || ""),
        hasImageUrl: Boolean(frameImageUrl),
        hasStartImageUrl: Boolean(resolvedFirstFrameUrl),
        hasEndImageUrl: Boolean(resolvedLastFrameUrl),
        hasAudioSliceUrl: Boolean(targetScene?.audioSliceUrl),
        selectedIndex: targetSceneIndex,
      });
      setScenarioVideoError("Для этой сцены не хватает source-кадров для video flow (см. [SCENARIO VIDEO REQUEST SUMMARY]).");
      if (targetSceneIndex >= 0) {
        updateScenarioScene(targetSceneIndex, { videoStatus: "error", videoError: "missing_required_frame_assets", videoPanelActivated: false });
      }
    }

    if (!hasImageForVideo) return;
    traceScenarioVideo("[SCENARIO VIDEO TRACE 6] image_validation_passed", {
      sceneId,
      selectedSceneId: requestedSceneId,
      nodeId: targetNodeId,
      workflowKey: String(effectiveWorkflowKey || ""),
      lipSyncRoute: effectiveWorkflowKey === "lip_sync_music",
      hasImageUrl: hasImageForVideo,
      hasAudioSliceUrl: Boolean(targetScene?.audioSliceUrl),
      videoStatus: String(targetScene?.videoStatus || ""),
      branch: "image_validation_passed",
    });
    const effectiveLipSync = isLipSyncScene(targetScene);
    const effectiveRenderMode = targetScene?.renderMode || (effectiveLipSync ? "avatar_lipsync" : "standard_video");
    console.info("[SCENARIO VIDEO FLOW]", {
      stage: "effective_render_mode",
      sceneId,
      effectiveRenderMode,
      effectiveLipSync,
    });
    const effectiveVideoProvider = resolveScenarioSceneVideoProvider(targetScene);

    const audioSliceUrlOverride = String(options?.audioSliceUrlOverride || "").trim();
    const audioSliceStartSecOverride = Number(options?.audioSliceStartSecOverride);
    const audioSliceEndSecOverride = Number(options?.audioSliceEndSecOverride);
    let attachedAudioSliceUrl = String(audioSliceUrlOverride || targetScene?.audioSliceUrl || "").trim();
    let effectiveAudioSliceKind = String(targetScene?.audioSliceKind || targetScene?.audio_slice_kind || "").trim().toLowerCase();
    let effectiveMusicVocalLipSyncAllowed = targetScene?.musicVocalLipSyncAllowed ?? targetScene?.music_vocal_lipsync_allowed;
    const lipSyncRoute = effectiveWorkflowKey === "lip_sync_music";
    const hasAudioSliceUrl = Boolean(attachedAudioSliceUrl);
    const staleSliceMetadataDetected = Boolean(
      lipSyncRoute
      && hasAudioSliceUrl
      && (!effectiveAudioSliceKind || effectiveMusicVocalLipSyncAllowed == null)
    );
    let reextractTriggered = false;
    traceScenarioVideo("[SCENARIO VIDEO TRACE 7] workflow_resolved", {
      sceneId,
      selectedSceneId: requestedSceneId,
      nodeId: targetNodeId,
      workflowKey: String(effectiveWorkflowKey || ""),
      lipSyncRoute,
      hasImageUrl: hasImageForVideo,
      hasAudioSliceUrl: Boolean(attachedAudioSliceUrl),
      videoStatus: String(targetScene?.videoStatus || ""),
      branch: "workflow_resolved",
    });
    console.info("[SCENARIO VIDEO CLICK]", {
      sceneId,
      resolvedWorkflowKey: effectiveWorkflowKey,
      lipSyncRoute,
      existingAudioSliceUrl: attachedAudioSliceUrl,
      willExtractSlice: lipSyncRoute && !attachedAudioSliceUrl,
      willStartVideo: true,
    });
    if (lipSyncRoute) {
      console.info("[SCENARIO LIP SYNC SLICE METADATA]", {
        sceneId,
        hasAudioSliceUrl,
        audioSliceKind: effectiveAudioSliceKind || "",
        musicVocalLipSyncAllowed: (
          effectiveMusicVocalLipSyncAllowed == null
            ? null
            : Boolean(effectiveMusicVocalLipSyncAllowed)
        ),
        staleSliceMetadataDetected,
        reextractTriggered,
      });
    }
    if (lipSyncRoute) {
      traceScenarioVideo("[SCENARIO VIDEO TRACE 8] before_auto_slice", {
        sceneId,
        selectedSceneId: requestedSceneId,
        nodeId: targetNodeId,
        workflowKey: String(effectiveWorkflowKey || ""),
        lipSyncRoute,
        hasImageUrl: hasImageForVideo,
        hasAudioSliceUrl: Boolean(attachedAudioSliceUrl),
        videoStatus: String(targetScene?.videoStatus || ""),
        branch: attachedAudioSliceUrl ? "auto_slice_skip_existing" : "auto_slice_required",
      });
      console.info("[SCENARIO LIP SYNC CHAIN]", {
        sceneId,
        stage: "before_auto_slice",
        needsSliceExtraction: !attachedAudioSliceUrl,
        audioSliceUrl: attachedAudioSliceUrl,
      });
    }
    if (lipSyncRoute && (!attachedAudioSliceUrl || staleSliceMetadataDetected)) {
      reextractTriggered = true;
      try {
        const extracted = await handleScenarioEditorExtractSceneAudio(targetNodeId, sceneId);
        attachedAudioSliceUrl = String(extracted?.audioSliceUrl || extracted?.sliceUrl || "").trim();
        effectiveAudioSliceKind = String(
          extracted?.audioSliceKind
          || extracted?.audio_slice_kind
          || effectiveAudioSliceKind
          || ""
        ).trim().toLowerCase();
        effectiveMusicVocalLipSyncAllowed = (
          extracted?.musicVocalLipSyncAllowed
          ?? extracted?.music_vocal_lipsync_allowed
          ?? effectiveMusicVocalLipSyncAllowed
        );
        traceScenarioVideo("[SCENARIO VIDEO TRACE 9] after_auto_slice", {
          sceneId,
          selectedSceneId: requestedSceneId,
          nodeId: targetNodeId,
          workflowKey: String(effectiveWorkflowKey || ""),
          lipSyncRoute,
          hasImageUrl: hasImageForVideo,
          hasAudioSliceUrl: Boolean(attachedAudioSliceUrl),
          videoStatus: String(targetScene?.videoStatus || ""),
          branch: attachedAudioSliceUrl ? "auto_slice_success" : "auto_slice_empty",
        });
        console.info("[SCENARIO LIP SYNC CHAIN]", {
          sceneId,
          sliceExtracted: Boolean(attachedAudioSliceUrl),
          resolvedAudioSliceUrl: attachedAudioSliceUrl,
          continuingToVideoStart: Boolean(attachedAudioSliceUrl),
        });
      } catch (error) {
        traceScenarioVideo("[SCENARIO VIDEO TRACE ERROR] auto_slice_failed", {
          sceneId,
          selectedSceneId: requestedSceneId,
          nodeId: targetNodeId,
          workflowKey: String(effectiveWorkflowKey || ""),
          lipSyncRoute,
          hasImageUrl: hasImageForVideo,
          hasAudioSliceUrl: false,
          videoStatus: String(targetScene?.videoStatus || ""),
          branch: String(error?.message || "auto_slice_failed"),
        });
        console.warn("[SCENARIO VIDEO FLOW] auto audio slice extraction failed", {
          sceneId,
          reason: String(error?.message || error || "audio_slice_auto_extract_failed"),
        });
      }
      console.info("[SCENARIO LIP SYNC SLICE METADATA]", {
        sceneId,
        hasAudioSliceUrl: Boolean(attachedAudioSliceUrl),
        audioSliceKind: effectiveAudioSliceKind || "",
        musicVocalLipSyncAllowed: (
          effectiveMusicVocalLipSyncAllowed == null
            ? null
            : Boolean(effectiveMusicVocalLipSyncAllowed)
        ),
        staleSliceMetadataDetected,
        reextractTriggered,
      });
    }
    if (effectiveContentType === "music_video" && ["i2v_sound", "f_l_sound"].includes(effectiveWorkflowKey)) {
      logScenarioVideoBlocked("validate_mode", "sound_workflow_blocked_for_clip", {
        sceneId,
        workflow: effectiveWorkflowKey,
        contentType: effectiveContentType,
      });
      setScenarioVideoError("Sound dialogue workflows отключены для music_video по умолчанию.");
      if (targetSceneIndex >= 0) {
        updateScenarioScene(targetSceneIndex, { videoStatus: "error", videoError: "sound_workflow_blocked_for_clip", videoPanelActivated: false });
      }
      return;
    }
    const musicVocalLipSyncAllowed = Boolean(effectiveMusicVocalLipSyncAllowed);
    const audioSliceKind = String(effectiveAudioSliceKind || "").trim().toLowerCase();
    if (effectiveWorkflowKey === "lip_sync_music" && !attachedAudioSliceUrl) {
      logScenarioVideoBlocked("validate_audio", "lip_sync_audio_missing", {
        sceneId,
        workflow: effectiveWorkflowKey,
        renderMode: String(effectiveRenderMode || ""),
        hasImageUrl: Boolean(frameImageUrl),
        hasStartImageUrl: Boolean(resolvedFirstFrameUrl),
        hasEndImageUrl: Boolean(resolvedLastFrameUrl),
        hasAudioSliceUrl: Boolean(attachedAudioSliceUrl),
        selectedIndex: targetSceneIndex,
      });
      setScenarioVideoError("Для lipSync не удалось автоматически подготовить audioSlice. Проверьте исходное аудио и попробуйте снова.");
      if (targetSceneIndex >= 0) {
        updateScenarioScene(targetSceneIndex, { videoStatus: "error", videoError: "lip_sync_audio_missing", videoPanelActivated: false });
      }
      return;
    }
    if (effectiveWorkflowKey === "lip_sync_music" && (!musicVocalLipSyncAllowed || audioSliceKind !== "music_vocal")) {
      logScenarioVideoBlocked("validate_audio", "lip_sync_music_vocal_flag_missing", {
        sceneId,
        workflow: effectiveWorkflowKey,
        musicVocalLipSyncAllowed,
        audioSliceKind: audioSliceKind || "none",
      });
      setScenarioVideoError("Для lipSync нужен slice с music+vocal compatibility.");
      if (targetSceneIndex >= 0) {
        updateScenarioScene(targetSceneIndex, { videoStatus: "error", videoError: "lip_sync_music_vocal_flag_missing", videoPanelActivated: false });
      }
      return;
    }

    console.info("[SCENARIO VIDEO FLOW]", {
      stage: "effective_workflow_key",
      sceneId,
      effectiveWorkflowKey,
      requiresTwoFrames,
      effectiveRequiresContinuation,
    });
    if (!sceneId) throw new Error("scene_id_required");
    const activeStoryboardRevision = String(scenarioFlowSourceNode?.data?.storyboardRevision || "").trim();
    const activeStoryboardSignature = String(
      scenarioFlowSourceNode?.data?.storyboardSignature
      || buildSceneSignature(scenarioScenes, "scene")
      || ""
    ).trim();
    const activeSceneSignature = String(buildScenarioScenePackageSignature(targetScene || {}) || "").trim();
    const explicitModel = resolveScenarioExplicitModelKey(targetScene);
    const workflowDefaultModelMap = {
      i2v: "ltx23_dev_fp8",
      lip_sync_music: "ltx23_dev_fp8",
      i2v_sound: "ltx23_dev_fp8",
      f_l: "ltx23_distilled_fp8",
      f_l_sound: "ltx23_distilled_fp8",
    };
    const resolvedModelKey = String(
      explicitModel
      || targetScene?.resolvedModelKey
      || workflowDefaultModelMap[String(effectiveWorkflowKey || "").trim().toLowerCase()]
      || ""
    ).trim();
    const requestedDurationSec = Number(
      targetScene?.requestedDurationSec
      ?? targetScene?.durationSec
      ?? Math.max(0, Number(targetScene.t1 ?? targetScene.end ?? 0) - Number(targetScene.t0 ?? targetScene.start ?? 0))
    ) || 0;
    const requiresAudioSensitiveVideo = effectiveWorkflowKey === "lip_sync_music" || Boolean(effectiveLipSync);
    const shouldAttachAudioSlice = Boolean(attachedAudioSliceUrl) && (requiresAudioSensitiveVideo || Boolean(effectiveLipSync));
    const continuationSourceSceneId = effectiveRequiresContinuation
      ? String(targetPreviousScene?.sceneId || "").trim()
      : "";
    const continuationSourceSelection = effectiveRequiresContinuation
      ? resolveContinuationSourceFromPreviousScene(targetPreviousScene)
      : { continuationSourceAssetUrl: "", continuationSourceAssetType: "" };
    const continuationSourceAssetUrl = String(continuationSourceSelection.continuationSourceAssetUrl || "").trim();
    const continuationSourceAssetType = String(continuationSourceSelection.continuationSourceAssetType || "").trim();
    const normalizedContinuationSourceAssetUrl = normalizeVideoSourceUrl(continuationSourceAssetUrl);
    const continuationEnabled = Boolean(
      effectiveRequiresContinuation
      && String(normalizedContinuationSourceAssetUrl || "").trim()
    );
    const continuityBridgePrompt = transitionType === "continuous"
      ? buildContinuousContinuityBridge({ scene: targetScene, previousScene: targetPreviousScene })
      : "";
    const originalVideoPrompt = getSceneTransitionPrompt(targetScene);
    const sceneHumanVisualAnchors = buildScenarioHumanVisualAnchors(targetScene);
    const humanAnchorBlock = sceneHumanVisualAnchors.length
      ? [
        "SCENE-SPECIFIC HUMAN VISUAL ANCHORS (SOURCE FRAME):",
        ...sceneHumanVisualAnchors.map((line) => `- ${line}`),
      ].join("\n")
      : "";
    const videoVisualGlueText = buildScenarioVideoVisualGlueText(targetScene);
    const finalVideoPrompt = [videoVisualGlueText, humanAnchorBlock, originalVideoPrompt].filter(Boolean).join("\n\n").trim();
    const sourceImageUrl = requiresTwoFrames
      ? (resolvedFirstFrameUrl || "")
      : (continuationEnabled
        ? (resolvedFirstFrameUrl || resolvedLastFrameUrl || frameImageUrl || "")
        : (frameImageUrl || ""));
    const rawScenarioVideoSourceUrls = {
      imageUrl: String(sourceImageUrl || "").trim(),
      startImageUrl: String(resolvedFirstFrameUrl || "").trim(),
      endImageUrl: String(resolvedLastFrameUrl || "").trim(),
      audioSliceUrl: String(shouldAttachAudioSlice ? attachedAudioSliceUrl : "").trim(),
      continuationSourceAssetUrl: String(normalizedContinuationSourceAssetUrl || "").trim(),
    };
    const normalizedScenarioVideoSourceUrls = {
      imageUrl: normalizeVideoSourceUrl(rawScenarioVideoSourceUrls.imageUrl),
      startImageUrl: normalizeVideoSourceUrl(rawScenarioVideoSourceUrls.startImageUrl),
      endImageUrl: normalizeVideoSourceUrl(rawScenarioVideoSourceUrls.endImageUrl),
      audioSliceUrl: normalizeVideoSourceUrl(rawScenarioVideoSourceUrls.audioSliceUrl),
      continuationSourceAssetUrl: normalizeVideoSourceUrl(rawScenarioVideoSourceUrls.continuationSourceAssetUrl),
    };
    const safeContinuationSourceSceneId = continuationEnabled ? continuationSourceSceneId : "";
    const safeContinuationSourceAssetUrl = continuationEnabled ? normalizedScenarioVideoSourceUrls.continuationSourceAssetUrl : "";
    const safeContinuationSourceAssetType = continuationEnabled ? continuationSourceAssetType : "";
    updateScenarioScene(targetSceneIndex, {
      continuationFromPrevious: continuationEnabled,
      continuationSourceSceneId: safeContinuationSourceSceneId,
      continuationSourceAssetUrl: safeContinuationSourceAssetUrl,
      continuationSourceAssetType: safeContinuationSourceAssetType,
    });
    console.info("[SCENARIO VIDEO FLOW]", {
      stage: "normalized_urls",
      sceneId,
      rawScenarioVideoSourceUrls,
      normalizedScenarioVideoSourceUrls,
    });
    console.debug("[SCENARIO VIDEO URL NORMALIZE]", {
      sceneId,
      provider: String(effectiveVideoProvider || ""),
      renderMode: String(effectiveRenderMode || ""),
      original: rawScenarioVideoSourceUrls,
      normalized: normalizedScenarioVideoSourceUrls,
    });
    const sourceImageStrategy = requiresTwoFrames
      ? "first_last_frames"
      : (continuationEnabled ? "continuation_previous_frame" : "single_image");
    const videoRequestSummary = {
      nodeId: String(scenarioFlowSourceNode?.id || scenarioEditor?.nodeId || ""),
      sceneId,
      ltxMode: String(targetScene?.ltxMode || ""),
      renderMode: String(effectiveRenderMode || ""),
      resolvedWorkflowKey: effectiveWorkflowKey,
      transitionType,
      requestedDurationSec,
      hasImageUrl: Boolean(frameImageUrl),
      hasStartImageUrl: Boolean(resolvedFirstFrameUrl),
      hasEndImageUrl: Boolean(resolvedLastFrameUrl),
      hasPreviousFrame: Boolean(continuationSourceAssetUrl),
      sourceImageStrategy,
      continuationFallbackApplied: fallbackContinuationToI2V,
    };
    console.info("[SCENARIO VIDEO REQUEST SUMMARY]", videoRequestSummary);
    if (requiresTwoFrames) {
      console.info("[SCENARIO FIRST_LAST VIDEO PAYLOAD]", {
        sceneId,
        workflow: effectiveWorkflowKey,
        firstFrameUrl: resolvedFirstFrameUrl,
        lastFrameUrl: resolvedLastFrameUrl,
        firstFramePresent: Boolean(resolvedFirstFrameUrl),
        lastFramePresent: Boolean(resolvedLastFrameUrl),
        secondFramePatchApplied: Boolean(resolvedFirstFrameUrl && resolvedLastFrameUrl),
        provider: effectiveVideoProvider,
      });
    }

    console.log("[StoryboardVideo] video_loading_on reason=generate_video", { sceneId });
    updateScenarioScene(targetSceneIndex, { videoUrl: "", videoStatus: "queued", videoError: "", videoJobId: "", videoPanelActivated: true });
    setScenarioVideoError("");
    console.log("[StoryboardVideo] generate", {
      sceneId,
      transitionType,
      provider: effectiveVideoProvider,
      sourceImageUrl,
    });
    console.debug("[SCENE VIDEO ROUTE]", {
      sceneId,
      sceneIndex: targetSceneIndex,
      storyboardRevision: activeStoryboardRevision,
      storyboardSignature: activeStoryboardSignature,
      sceneSignature: activeSceneSignature,
      ltxMode: String(targetScene?.ltxMode || ""),
      provider: effectiveVideoProvider,
      imageStrategy,
      explicitWorkflow,
      resolvedWorkflowKey: effectiveWorkflowKey,
      explicitModel,
      resolvedModelKey,
      requiresAudioSensitiveVideo,
      requestedDurationSec,
      startImagePresent: Boolean(effectiveStartImageUrl),
      endImagePresent: Boolean(resolvedLastFrameUrl),
      audioSlicePresent: Boolean(targetScene?.audioSliceUrl),
    });
    console.debug("[SCENARIO LTX SCENE DEBUG]", (Array.isArray(scenarioScenes) ? scenarioScenes : []).map((scene, idx) => ({
      sceneId: String(scene?.sceneId || ""),
      sceneIndex: idx,
      provider: resolveScenarioSceneVideoProvider(scene),
      ltxMode: String(scene?.ltxMode || ""),
      resolvedWorkflowKey: normalizeScenarioWorkflowKeyForProduction(
        String(scene?.resolvedWorkflowKey || resolveScenarioWorkflowKey(scene) || "")
      ),
      resolvedModelKey: String(scene?.resolvedModelKey || resolveScenarioExplicitModelKey(scene) || ""),
      startImagePresent: Boolean(scene?.startImageUrl || scene?.startFrameImageUrl),
      endImagePresent: Boolean(scene?.endImageUrl || scene?.endFrameImageUrl),
      audioSlicePresent: Boolean(scene?.audioSliceUrl),
    })));
    try {
      const endpoint = "/api/clip/video/start";
      console.debug("[SCENARIO VIDEO SEND ROUTE]", {
        route: endpoint,
        sceneId,
        selectedTab: String(options?.selectedTab || options?.activeTab || ""),
      });
      const transitionActionPrompt = [
        continuityBridgePrompt,
        getSceneTransitionPrompt(targetScene),
      ].filter(Boolean).join("\n");
      const scenarioContractPayload = buildScenarioSceneContractPayload(targetScene);
      const scenarioContractPayloadSanitized = {
        ...scenarioContractPayload,
        requiresContinuation: continuationEnabled,
        continuationFromPrevious: continuationEnabled,
        continuationSourceSceneId: safeContinuationSourceSceneId,
        continuationSourceAssetUrl: safeContinuationSourceAssetUrl,
        continuationSourceAssetType: safeContinuationSourceAssetType,
      };
      if (CLIP_TRACE_VISUAL_LOCK) {
        console.debug("[SCENARIO VISUAL LOCK] video prompt", {
          sceneId,
          renderMode: String(effectiveRenderMode || ""),
          ltxMode: String(targetScene?.ltxMode || ""),
          transitionType: String(transitionType || ""),
          lipSync: Boolean(effectiveLipSync),
          hasGlobalVisualLock: hasScenarioContractValue(targetScene?.globalVisualLock),
          hasGlobalCameraProfile: hasScenarioContractValue(targetScene?.globalCameraProfile),
          finalVideoPromptLength: finalVideoPrompt.length,
        });
      }
      if (CLIP_TRACE_SCENARIO_TRANSFER) {
        console.debug("[SCENARIO TRANSFER] before /api/clip/video/start", buildScenarioTransferLogData(targetScene, scenarioContractPayload));
      }
      const safeAudioSliceStartSec = Number.isFinite(audioSliceStartSecOverride)
        ? audioSliceStartSecOverride
        : Number(targetScene?.audioSliceStartSec ?? targetScene?.audio_slice_start_sec ?? targetScene?.t0 ?? targetScene?.start ?? 0);
      const safeAudioSliceEndSec = Number.isFinite(audioSliceEndSecOverride)
        ? audioSliceEndSecOverride
        : Number(targetScene?.audioSliceEndSec ?? targetScene?.audio_slice_end_sec ?? targetScene?.t1 ?? targetScene?.end ?? safeAudioSliceStartSec);
      const safeAudioSliceExpectedDurationSec = Number(
        targetScene?.audioSliceExpectedDurationSec
        ?? targetScene?.audio_slice_expected_duration_sec
        ?? Math.max(0, safeAudioSliceEndSec - safeAudioSliceStartSec)
      );
      traceScenarioVideo("[SCENARIO VIDEO TRACE 10] before_payload_build", {
        sceneId,
        selectedSceneId: requestedSceneId,
        nodeId: targetNodeId,
        workflowKey: String(effectiveWorkflowKey || ""),
        lipSyncRoute,
        hasImageUrl: hasImageForVideo,
        hasAudioSliceUrl: Boolean(attachedAudioSliceUrl),
        videoStatus: String(targetScene?.videoStatus || ""),
        branch: "before_payload_build",
      });
      const videoStartPayload = {
        sceneId,
        imageUrl: normalizedScenarioVideoSourceUrls.imageUrl,
        startImageUrl: normalizedScenarioVideoSourceUrls.startImageUrl,
        endImageUrl: normalizedScenarioVideoSourceUrls.endImageUrl,
        audioSliceUrl: normalizedScenarioVideoSourceUrls.audioSliceUrl,
        external_audio_used: shouldAttachAudioSlice,
        external_audio_reason: shouldAttachAudioSlice ? "lip_sync_scene" : "not_attached",
        videoPrompt: finalVideoPrompt,
        sceneHumanVisualAnchors,
        transitionActionPrompt,
        transitionType,
        requestedDurationSec,
        lipSync: lipSyncRoute ? true : effectiveLipSync,
        renderMode: effectiveRenderMode,
        ltxMode: lipSyncRoute
          ? "lip_sync"
          : String(targetScene?.ltxMode || ""),
        imageStrategy,
        resolvedWorkflowKey: effectiveWorkflowKey,
        video_generation_route: effectiveWorkflowKey,
        send_audio_to_generator: lipSyncRoute ? shouldAttachAudioSlice : false,
        audio_slice_start_sec: safeAudioSliceStartSec,
        audio_slice_end_sec: safeAudioSliceEndSec,
        audio_slice_expected_duration_sec: safeAudioSliceExpectedDurationSec,
        resolvedModelKey,
        workflowFileOverride: String(targetScene?.workflowFileOverride || ""),
        modelFileOverride: String(targetScene?.modelFileOverride || ""),
        requiresTwoFrames,
        requiresContinuation: continuationEnabled,
        requiresAudioSensitiveVideo,
        continuationFromPrevious: continuationEnabled,
        continuationSourceSceneId: safeContinuationSourceSceneId,
        continuationSourceAssetUrl: safeContinuationSourceAssetUrl,
        continuationSourceAssetType: safeContinuationSourceAssetType,
        shotType: targetScene.shotType || "",
        sceneType: targetScene.sceneType || "",
        format: resolvePreferredSceneFormat(
          targetScene?.format,
          targetScene?.imageFormat
        ),
        provider: effectiveVideoProvider,
        sceneRenderProvider: effectiveVideoProvider,
        sceneContract: scenarioContractPayloadSanitized,
        ...scenarioContractPayloadSanitized,
      };
      traceScenarioVideo("[SCENARIO VIDEO TRACE 11] after_payload_build", {
        sceneId,
        selectedSceneId: requestedSceneId,
        nodeId: targetNodeId,
        workflowKey: String(videoStartPayload?.resolvedWorkflowKey || ""),
        lipSyncRoute,
        hasImageUrl: Boolean(videoStartPayload?.imageUrl || videoStartPayload?.startImageUrl),
        hasAudioSliceUrl: Boolean(videoStartPayload?.audioSliceUrl),
        videoStatus: String(targetScene?.videoStatus || ""),
        branch: "after_payload_build",
      });
      console.info("[SCENARIO VIDEO FLOW]", {
        stage: "video_payload_built",
        sceneId,
        endpoint,
        payloadWorkflow: String(videoStartPayload?.resolvedWorkflowKey || ""),
        payloadRenderMode: String(videoStartPayload?.renderMode || ""),
      });
      console.debug("[SCENARIO VIDEO START PAYLOAD]", {
        endpoint,
        sceneId,
        effectiveVideoPromptLength: finalVideoPrompt.length,
        renderMode: String(effectiveRenderMode || ""),
        resolvedWorkflowKey: effectiveWorkflowKey,
        firstFrameUrl: resolvedFirstFrameUrl,
        lastFrameUrl: resolvedLastFrameUrl,
        sourceOfTruthKeys,
        payload: videoStartPayload,
      });
      console.info("[SCENARIO VIDEO START REQUEST]", {
        endpoint,
        method: "POST",
        sceneId,
        effectiveVideoPromptLength: finalVideoPrompt.length,
      });
      console.info("[SCENARIO VIDEO FLOW]", {
        stage: "before_fetch_start",
        sceneId,
        endpoint,
      });
      console.info("[SCENARIO VIDEO START INFO]", {
        stage: "before_start_post",
        sceneId,
        endpoint,
        timeoutMs: VIDEO_START_TIMEOUT_MS,
      });
      if (lipSyncRoute) {
        console.info("[SCENARIO LIP SYNC START]", {
          sceneId,
          route: effectiveWorkflowKey,
          hasImageUrl: Boolean(videoStartPayload?.imageUrl),
          hasAudioSliceUrl: Boolean(videoStartPayload?.audioSliceUrl),
          audioSliceUrl: String(videoStartPayload?.audioSliceUrl || ""),
          willCallVideoStart: true,
        });
      }
      traceScenarioVideo("[SCENARIO VIDEO TRACE 12] before_video_start_fetch", {
        sceneId,
        selectedSceneId: requestedSceneId,
        nodeId: targetNodeId,
        workflowKey: String(videoStartPayload?.resolvedWorkflowKey || ""),
        lipSyncRoute,
        hasImageUrl: Boolean(videoStartPayload?.imageUrl || videoStartPayload?.startImageUrl),
        hasAudioSliceUrl: Boolean(videoStartPayload?.audioSliceUrl),
        videoStatus: String(targetScene?.videoStatus || ""),
        branch: endpoint,
      });
      const out = await fetchJson(endpoint, {
        method: "POST",
        timeoutMs: VIDEO_START_TIMEOUT_MS,
        body: videoStartPayload,
      });
      console.info("[SCENARIO VIDEO START RESPONSE]", {
        endpoint,
        sceneId,
        ok: Boolean(out?.ok),
        jobId: String(out?.jobId || ""),
        status: String(out?.status || ""),
        code: String(out?.code || ""),
        hint: String(out?.hint || ""),
      });
      traceScenarioVideo("[SCENARIO VIDEO TRACE 13] after_video_start_fetch", {
        sceneId,
        selectedSceneId: requestedSceneId,
        nodeId: targetNodeId,
        workflowKey: String(videoStartPayload?.resolvedWorkflowKey || ""),
        lipSyncRoute,
        hasImageUrl: Boolean(videoStartPayload?.imageUrl || videoStartPayload?.startImageUrl),
        hasAudioSliceUrl: Boolean(videoStartPayload?.audioSliceUrl),
        videoStatus: String(out?.status || targetScene?.videoStatus || ""),
        branch: out?.ok ? "video_start_ok" : "video_start_not_ok",
      });
      if (lipSyncRoute) {
        console.info("[SCENARIO LIP SYNC START RESPONSE]", {
          sceneId,
          ok: Boolean(out?.ok),
          jobId: String(out?.jobId || ""),
          code: String(out?.code || ""),
          hint: String(out?.hint || ""),
        });
      }
      console.info("[SCENARIO VIDEO START INFO]", {
        stage: "after_start_post",
        sceneId,
        endpoint,
        ok: Boolean(out?.ok),
        jobId: String(out?.jobId || ""),
        status: String(out?.status || ""),
        code: String(out?.code || ""),
      });
      console.info("[SCENARIO VIDEO FLOW]", {
        stage: "after_fetch_start",
        sceneId,
        endpoint,
        ok: Boolean(out?.ok),
        jobId: String(out?.jobId || ""),
      });
      console.info("[SCENARIO VIDEO JOB]", {
        stage: "start_response",
        sceneId,
        jobId: String(out?.jobId || ""),
        ok: Boolean(out?.ok),
        status: String(out?.status || ""),
        code: String(out?.code || ""),
        hint: String(out?.hint || ""),
        message: String(out?.error || out?.message || ""),
      });
      console.info("[SCENARIO VIDEO STARTED]", {
        sceneId,
        route: endpoint,
        workflow: effectiveWorkflowKey,
        mode: String(effectiveRenderMode || ""),
        jobId: String(out?.jobId || ""),
      });
      console.info("[SCENARIO VIDEO START RESULT]", {
        endpoint,
        sceneId,
        status: String(out?.status || ""),
        response: out,
        effectiveVideoPromptLength: finalVideoPrompt.length,
      });
      console.info("[VIDEO START RESPONSE]", {
        scope: "scenario",
        sceneId,
        ok: !!out?.ok,
        jobId: String(out?.jobId || ""),
        providerJobId: String(out?.providerJobId || ""),
      });
      console.info("[CLIP VIDEO START RESPONSE]", {
        ok: !!out?.ok,
        jobId: String(out?.jobId || ""),
        provider: String(out?.provider || effectiveVideoProvider),
        mode: String(out?.mode || effectiveRenderMode),
        sceneId,
        status: String(out?.status || ""),
        raw: out,
      });
      if (!out?.jobId) {
        console.warn("[CLIP WARN] video start returned without jobId", {
          sceneId,
          provider: effectiveVideoProvider,
          raw: out,
        });
        logScenarioDirectGenerateBlock({
          route: "/api/clip/video",
          url: `${API_BASE}/api/clip/video`,
          reason: "legacy_fallback_disabled_missing_job_id_from_video_start",
          sceneId,
        });
        throw new Error(
          String(out?.hint || out?.code || "video_start_missing_job_id")
          + " (legacy direct generate fallback disabled)"
        );
      }

      if (out?.jobId) {
        if (!out?.ok) {
          console.warn("[SCENARIO VIDEO JOB]", {
            stage: "start_response_has_job_but_not_ok",
            sceneId,
            jobId: String(out?.jobId || ""),
            code: String(out?.code || ""),
            hint: String(out?.hint || ""),
            message: String(out?.error || out?.message || ""),
          });
        }
        console.info("[SCENARIO VIDEO JOB]", {
          stage: "queued",
          sceneId,
          jobId: String(out.jobId || ""),
          providerJobId: String(out.providerJobId || ""),
          workflowKey: String(effectiveWorkflowKey || ""),
        });
        console.info("[CLIP TRACE] state update", {
          source: "handleScenarioGenerateVideo:updateScenarioScene:queued",
          sceneId,
          jobId: String(out.jobId || ""),
        });
        const startedMeta = {
          jobId: String(out.jobId),
          providerJobId: String(out.providerJobId || ""),
          provider: String(out?.provider || effectiveVideoProvider),
          sceneId,
          storyboardRevision: activeStoryboardRevision,
          storyboardSignature: activeStoryboardSignature,
          sceneSignature: activeSceneSignature,
          workflowKey: effectiveWorkflowKey,
          modelKey: resolvedModelKey,
          audioSensitive: requiresAudioSensitiveVideo,
          continuation: effectiveRequiresContinuation,
          continuationSourceSceneId,
          continuationSourceAssetType,
          renderMode: effectiveRenderMode,
          status: "queued",
        };
        updateScenarioScene(targetSceneIndex, { videoJobId: startedMeta.jobId, videoStatus: "queued", videoError: "" });
        let shouldStartPolling = true;
        try {
          console.info("[SCENARIO VIDEO STATUS INFO]", {
            stage: "before_immediate_status_get",
            sceneId,
            jobId: startedMeta.jobId,
            timeoutMs: VIDEO_STATUS_TIMEOUT_MS,
          });
          console.info("[SCENARIO VIDEO FLOW]", {
            stage: "before_immediate_status_get",
            sceneId,
            jobId: startedMeta.jobId,
          });
          const immediateOut = await fetchJson(`/api/clip/video/status/${encodeURIComponent(startedMeta.jobId)}`, {
            method: "GET",
            timeoutMs: VIDEO_STATUS_TIMEOUT_MS,
          });
          const immediateStatus = String(immediateOut?.status || "").toLowerCase() || "running";
          console.info("[SCENARIO VIDEO STATUS INFO]", {
            stage: "after_immediate_status_get",
            sceneId,
            jobId: startedMeta.jobId,
            ok: Boolean(immediateOut?.ok),
            status: immediateStatus,
            hasVideoUrl: Boolean(String(immediateOut?.videoUrl || "").trim()),
            error: String(immediateOut?.error || immediateOut?.hint || immediateOut?.code || ""),
          });
          console.info("[SCENARIO VIDEO FLOW]", {
            stage: "after_immediate_status_get",
            sceneId,
            jobId: startedMeta.jobId,
            status: immediateStatus,
          });
          if (immediateStatus === "done" && String(immediateOut?.videoUrl || "").trim()) {
            updateScenarioScene(targetSceneIndex, {
              videoUrl: String(immediateOut.videoUrl || ""),
              mode: String(immediateOut.mode || ""),
              model: String(immediateOut.model || ""),
              requestedDurationSec: normalizeDurationSec(immediateOut.requestedDurationSec),
              providerDurationSec: normalizeDurationSec(immediateOut.providerDurationSec),
              videoStatus: "done",
              videoError: "",
              videoJobId: startedMeta.jobId,
              videoPanelActivated: false,
            });
            clearActiveVideoJob(sceneId, { status: "done", jobId: startedMeta.jobId });
            console.info("[SCENARIO VIDEO UI RESET]", { sceneId, status: "done", videoPanelActivatedAfterApply: false });
            openNextSceneWithoutVideo(targetSceneIndex);
            shouldStartPolling = false;
          } else if (immediateStatus === "error" || immediateStatus === "stopped" || immediateStatus === "not_found") {
            updateScenarioScene(targetSceneIndex, {
              videoStatus: immediateStatus,
              videoError: String(immediateOut?.error || immediateOut?.hint || "video_job_failed"),
              videoJobId: startedMeta.jobId,
              videoPanelActivated: false,
            });
            clearActiveVideoJob(sceneId, { status: immediateStatus, jobId: startedMeta.jobId });
            console.info("[SCENARIO VIDEO UI RESET]", { sceneId, status: immediateStatus, videoPanelActivatedAfterApply: false });
            shouldStartPolling = false;
          } else {
            updateScenarioScene(targetSceneIndex, {
              videoStatus: immediateStatus,
              videoError: "",
              videoJobId: startedMeta.jobId,
            });
          }
        } catch (immediateStatusError) {
          const message = String(immediateStatusError?.message || immediateStatusError || "");
          const statusLogLevel = message.toLowerCase().includes("timeout") ? "timeout" : "status";
          console.warn("[SCENARIO VIDEO STATUS INFO]", {
            stage: "immediate_status_error",
            sceneId,
            jobId: startedMeta.jobId,
            type: statusLogLevel,
            error: message,
          });
        }
        if (!shouldStartPolling) return;
        console.info("[SCENARIO VIDEO POLLING INFO]", {
          stage: "start_polling_transition",
          sceneId,
          jobId: startedMeta.jobId,
        });
        console.info("[SCENARIO VIDEO FLOW]", {
          stage: "before_start_polling",
          sceneId,
          jobId: startedMeta.jobId,
        });
        startScenarioVideoPolling({
          ...startedMeta,
        });
        return;
      }

    } catch (e) {
      console.error(e);
      const errorMessage = String(e?.message || e || "");
      const errorType = errorMessage.toLowerCase().includes("timeout") ? "timeout" : "start";
      console.warn("[SCENARIO VIDEO START INFO]", {
        stage: "start_or_status_error",
        sceneId,
        type: errorType,
        error: errorMessage,
      });
      setScenarioVideoError(String(e?.message || e));
      updateScenarioScene(targetSceneIndex, { videoStatus: "error", videoError: String(e?.message || e), videoPanelActivated: false });
      console.info("[SCENARIO VIDEO UI RESET]", { sceneId, status: "error", videoPanelActivatedAfterApply: false });
    }
  }, [clearActiveVideoJob, handleScenarioEditorExtractSceneAudio, openNextSceneWithoutVideo, resolveScenarioSceneIndex, scenarioEditor?.nodeId, scenarioEditor?.selectedSceneId, scenarioEditor.selected, scenarioFlowSourceNode?.id, scenarioScenes, scenarioSelected?.sceneId, startScenarioVideoPolling, updateScenarioScene]);

  const handleScenarioClearVideo = useCallback(() => {
    setScenarioVideoError("");
    const sceneId = String(scenarioSelected?.sceneId || "").trim();
    if (!sceneId) throw new Error("scene_id_required");
    clearActiveVideoJob(sceneId);
    updateScenarioScene(scenarioEditor.selected, { videoUrl: "", videoStatus: "", videoError: "", videoJobId: "" });
  }, [clearActiveVideoJob, scenarioEditor.selected, scenarioSelected?.sceneId, updateScenarioScene]);

  const handleScenarioAddToVideo = useCallback(() => {
    const scrollToVideoBlock = (attemptsLeft = 3) => {
      const node = scenarioVideoCardRef.current || scenarioVideoSectionRef.current;
      if (node) {
        try {
          node.scrollIntoView({ block: "center", behavior: "smooth" });
        } catch {
          node.scrollIntoView();
        }
        return;
      }
      if (attemptsLeft <= 0) return;
      scenarioVideoScrollRafRef.current = requestAnimationFrame(() => {
        scenarioVideoScrollTimerRef.current = window.setTimeout(() => {
          scrollToVideoBlock(attemptsLeft - 1);
        }, 0);
      });
    };

    const transitionType = resolveSceneTransitionType(scenarioSelected);
    const selectedFrameUrls = resolveSceneFrameUrls(scenarioSelected, scenarioPreviousScene);
    const hasImage = transitionType === "continuous"
      ? !!(selectedFrameUrls.effectiveStartImageUrl || selectedFrameUrls.endImageUrl || selectedFrameUrls.fallbackImageUrl)
      : !!selectedFrameUrls.fallbackImageUrl;
    if (!hasImage) return;
    const videoSourceImageUrl = transitionType === "continuous"
      ? String(selectedFrameUrls.effectiveStartImageUrl || selectedFrameUrls.endImageUrl || selectedFrameUrls.fallbackImageUrl || "")
      : String(selectedFrameUrls.fallbackImageUrl || "");
    const effectiveVideoProvider = resolveScenarioSceneVideoProvider(scenarioSelected);

    const sceneId = String(scenarioSelected?.sceneId || "").trim();
    if (!sceneId) throw new Error("scene_id_required");
    console.log("[StoryboardVideo] add_to_video activate_panel", {
      sceneId,
      transitionType,
      provider: effectiveVideoProvider,
      sourceImageUrl: videoSourceImageUrl,
    });

    updateScenarioScene(scenarioEditor.selected, {
      videoSourceImageUrl,
      videoPanelActivated: true,
    });

    if (scenarioVideoScrollRafRef.current) {
      cancelAnimationFrame(scenarioVideoScrollRafRef.current);
      scenarioVideoScrollRafRef.current = 0;
    }
    if (scenarioVideoScrollTimerRef.current) {
      clearTimeout(scenarioVideoScrollTimerRef.current);
      scenarioVideoScrollTimerRef.current = null;
    }

    setScenarioVideoOpen(true);
    setScenarioVideoError("");
    if (!String(scenarioSelected?.videoUrl || "").trim()) {
      clearActiveVideoJob(sceneId);
    }
    setScenarioVideoFocusPulse(true);
    window.setTimeout(() => setScenarioVideoFocusPulse(false), 1200);
    scrollToVideoBlock();
  }, [clearActiveVideoJob, scenarioEditor.selected, scenarioPreviousScene, scenarioSelected, updateScenarioScene]);

  const assemblySource = useMemo(() => resolveAssemblySource({ nodes, edges }), [nodes, edges]);
  const assemblyScenesSource = assemblySource.scenesSource;
  const assemblySourceLabel = useMemo(() => getAssemblySourceLabel(assemblyScenesSource), [assemblyScenesSource]);
  const assemblyIntroLabel = useMemo(() => getAssemblyIntroLabel(assemblySource.introSourceNodeType), [assemblySource.introSourceNodeType]);
  const assemblyScenesForPayload = assemblySource.scenes;
  const assemblyIntroFrame = assemblySource.introFrame;
  const assemblyHasIntro = hasIntroFramePreview(assemblyIntroFrame);
  const assemblyReadySceneCount = useMemo(
    () => assemblyScenesForPayload.filter((scene) => String(scene?.videoUrl || "").trim()).length,
    [assemblyScenesForPayload]
  );
  const assemblyDurationEstimateSec = useMemo(
    () => assemblyScenesForPayload.reduce((sum, scene) => sum + (Number(getSceneRequestedDurationSec(scene)) || 0), 0) + (assemblyHasIntro ? Number(assemblyIntroFrame?.durationSec || 0) : 0),
    [assemblyHasIntro, assemblyIntroFrame?.durationSec, assemblyScenesForPayload]
  );

  const assemblyPayload = useMemo(() => {
    const sceneFormat = resolvePreferredSceneFormat(
      assemblySource.scenarioFormat,
      assemblyScenesForPayload.find((scene) => String(scene?.imageFormat || scene?.format || "").trim())?.imageFormat,
      assemblyScenesForPayload.find((scene) => String(scene?.format || "").trim())?.format
    );
    return buildAssemblyPayload({
      scenes: assemblyScenesForPayload,
      audioUrl: globalAudioUrlRaw,
      format: sceneFormat,
      intro: assemblyIntroFrame,
    });
  }, [assemblyIntroFrame, assemblyScenesForPayload, assemblySource.scenarioFormat, globalAudioUrlRaw]);

  const assemblySourceNodeId = String(assemblySource.sourceNodeId || "");
  const assemblySourceNodeType = String(assemblySource.sourceNodeType || "");
  const assemblyNodeId = String(assemblySource.assemblyNodeId || "");
  const assemblyIntroSourceNodeId = String(assemblySource.introSourceNodeId || "");
  const assemblyIntroSourceNodeType = String(assemblySource.introSourceNodeType || "");
  const assemblyIntroDurationSec = Number(assemblyIntroFrame?.durationSec || 0);
  const assemblyIntroTitle = String(assemblyIntroFrame?.title || "");
  const assemblyHasAudio = !!assemblyPayload.audioUrl;
  const assemblyFormat = String(assemblyPayload.format || "9:16");
  const assemblyCanAssemble = assemblyPayload.scenes.length > 0 && !isAssembling;
  const assemblyDebugSummary = useMemo(
    () => `main=${assemblySourceLabel}; intro=${assemblyIntroLabel}; sourceNode=${assemblySourceNodeId || "none"}; introNode=${assemblyIntroSourceNodeId || "none"}; scenes=${assemblyScenesForPayload.length}; introImage=${assemblyHasIntro ? "yes" : "no"}; introDur=${assemblyIntroDurationSec || 0}; audio=${assemblyHasAudio ? "yes" : "no"}`,
    [
      assemblyHasAudio,
      assemblyHasIntro,
      assemblyIntroDurationSec,
      assemblyIntroLabel,
      assemblyIntroSourceNodeId,
      assemblyScenesForPayload.length,
      assemblySourceLabel,
      assemblySourceNodeId,
    ]
  );

  const assemblyPayloadSignature = useMemo(
    () => buildAssemblyPayloadSignature(assemblyPayload, {
      assemblyNodeId,
      sourceNodeId: assemblySourceNodeId,
      sourceNodeType: assemblySourceNodeType,
      scenesSource: assemblyScenesSource,
      introSourceNodeId: assemblyIntroSourceNodeId,
      introSourceNodeType: assemblyIntroSourceNodeType,
    }),
    [
      assemblyPayload,
      assemblyNodeId,
      assemblySourceNodeId,
      assemblySourceNodeType,
      assemblyScenesSource,
      assemblyIntroSourceNodeId,
      assemblyIntroSourceNodeType,
    ]
  );

  useEffect(() => {
    if (!CLIP_TRACE_ASSEMBLY_SOURCE) return;
    console.debug("[CLIP TRACE] assembly source", {
      assemblyNodeId,
      sourceNodeId: assemblySourceNodeId,
      sourceNodeType: assemblySourceNodeType,
      introSourceNodeId: assemblyIntroSourceNodeId,
      introSourceNodeType: assemblyIntroSourceNodeType,
      scenesSource: assemblyScenesSource,
      scenesCount: assemblyScenesForPayload.length,
      readyScenesCount: assemblyReadySceneCount,
      introConnected: assemblyIntroSourceNodeType === "introFrame",
      introImagePresent: assemblyHasIntro,
      introDuration: assemblyIntroDurationSec,
      sceneIds: assemblyScenesForPayload.map((scene) => String(scene?.sceneId || "")).filter(Boolean),
      requestedDurations: assemblyScenesForPayload.map((scene) => getSceneRequestedDurationSec(scene)),
      hasAudio: assemblyHasAudio,
      format: assemblyFormat,
      signatureSource: `${assemblyScenesSource}:${assemblySourceNodeId || "none"}:intro:${assemblyIntroSourceNodeId || "none"}`,
    });
  }, [
    assemblyFormat,
    assemblyHasAudio,
    assemblyHasIntro,
    assemblyIntroDurationSec,
    assemblyIntroSourceNodeId,
    assemblyIntroSourceNodeType,
    assemblyNodeId,
    assemblyReadySceneCount,
    assemblyScenesForPayload,
    assemblyScenesSource,
    assemblySourceNodeId,
    assemblySourceNodeType,
  ]);

  useEffect(() => {
    const prev = renderTraceSnapshotRef.current;
    const changed = [];
    if (!prev || prev.nodesRef !== nodes) changed.push("nodes");
    if (!prev || prev.edgesRef !== edges) changed.push("edges");
    if (!prev || prev.assemblyResult !== assemblyResult) changed.push("assemblyResult");
    if (!prev || prev.assemblyBuildState !== assemblyBuildState) changed.push("assemblyBuildState");
    if (!prev || prev.assemblyPayloadSignature !== assemblyPayloadSignature) changed.push("assemblyPayloadSignature");
    if (!prev || prev.lastSavedAt !== lastSavedAt) changed.push("lastSavedAt");
    if (!prev || prev.isAssemblyStale !== isAssemblyStale) changed.push("isAssemblyStale");
    if (!prev || prev.scenarioScenesRef !== scenarioScenes) changed.push("scenarioScenes");
    if (!prev || prev.comfyScenesRef !== comfyScenes) changed.push("comfyScenes");

    renderTraceSnapshotRef.current = {
      nodesRef: nodes,
      edgesRef: edges,
      assemblyResult,
      assemblyBuildState,
      assemblyPayloadSignature,
      lastSavedAt,
      isAssemblyStale,
      scenarioScenesRef: scenarioScenes,
      comfyScenesRef: comfyScenes,
    };

    if (!changed.length) return;

    const now = Date.now();
    const signature = changed.join("|");
    const last = renderTraceLastLogRef.current;
    if ((now - last.ts) >= 500 || last.signature !== signature) {
      console.debug("[CLIP TRACE] render causes", { changed });
      renderTraceLastLogRef.current = { ts: now, signature };
    }
  });

  const lastAssemblyPayloadSignatureRef = useRef("");
  const assemblyAbortControllerRef = useRef(null);
  const narrativeAbortControllersRef = useRef(new Map());
  const narrativeGenerateInFlightRef = useRef(new Map());
  const assemblyPollTimerRef = useRef(null);
  const assemblyPollingActiveRef = useRef(false);

  useEffect(() => () => {
    narrativeAbortControllersRef.current.forEach((controller) => controller?.abort());
    narrativeAbortControllersRef.current.clear();
    narrativeGenerateInFlightRef.current.clear();
  }, []);

  useEffect(() => {
    if (!assemblyPayloadSignature) return;

    if (!lastAssemblyPayloadSignatureRef.current) {
      lastAssemblyPayloadSignatureRef.current = assemblyPayloadSignature;
      return;
    }

    if (lastAssemblyPayloadSignatureRef.current === assemblyPayloadSignature) return;

    lastAssemblyPayloadSignatureRef.current = assemblyPayloadSignature;

    if (isAssembling) return;

    setAssemblyError("");
    setAssemblyInfo("");
    setAssemblyBuildState("idle");
    setIsAssemblyStale(true);
    setAssemblyJobId("");
    setAssemblyProgressPercent(0);
    setAssemblyStage("");
    setAssemblyStageLabel("");
    setAssemblyStageCurrent(0);
    setAssemblyStageTotal(0);
  }, [assemblyPayloadSignature, isAssembling]);

  const stopAssemblyPolling = useCallback(() => {
    assemblyPollingActiveRef.current = false;
    if (assemblyPollTimerRef.current) {
      clearTimeout(assemblyPollTimerRef.current);
      assemblyPollTimerRef.current = null;
    }
  }, []);

  const startAssemblyPolling = useCallback((jobId) => {
    if (!jobId) return;
    stopAssemblyPolling();
    assemblyPollingActiveRef.current = true;
    const statusUrl = API_BASE
      ? `${API_BASE}/api/clip/assemble/status/${encodeURIComponent(jobId)}`
      : `/api/clip/assemble/status/${encodeURIComponent(jobId)}`;

    const tick = async () => {
      try {
        const res = await fetch(statusUrl, { credentials: "include" });
        let out = null;
        try {
          out = await res.json();
        } catch {
          out = null;
        }
        if (!res.ok) throw new Error(String(out?.detail || out?.message || out?.hint || `HTTP ${res.status}`));
        if (!assemblyPollingActiveRef.current) return;

        const status = String(out?.status || "").toLowerCase();
        setAssemblyProgressPercent(Number(out?.progressPercent || 0));
        setAssemblyStage(String(out?.stage || ""));
        setAssemblyStageLabel(String(out?.label || ""));
        setAssemblyStageCurrent(Number(out?.current || 0));
        setAssemblyStageTotal(Number(out?.total || 0));

        if (status === "done") {
          stopAssemblyPolling();
          const finalVideoUrl = String(out?.finalVideoUrl || "").trim();
          if (!finalVideoUrl) throw new Error("Сборка завершена, но finalVideoUrl не получен");
          setAssemblyResult({
            finalVideoUrl,
            audioApplied: !!out?.audioApplied,
            sceneCount: Number(out?.sceneCount || assemblyPayload.scenes.length || 0),
            introIncluded: !!out?.introIncluded,
            totalSegments: Number(out?.totalSegments || 0),
            totalSteps: Number(out?.totalSteps || out?.total || 0),
            introDurationSec: Number(out?.introDurationSec || 0),
          });
          setAssemblyBuildState("done");
          setAssemblyInfo("");
          setIsAssemblyStale(false);
          setAssemblyJobId("");
          setIsAssembling(false);
          setNodes((prev) => [...prev]);
          return;
        }

        if (status === "error") {
          stopAssemblyPolling();
          setAssemblyBuildState("error");
          setAssemblyError(String(out?.error || "Ошибка сборки"));
          setAssemblyJobId("");
          setIsAssembling(false);
          return;
        }

        if (status === "stopped") {
          stopAssemblyPolling();
          setAssemblyBuildState("idle");
          setAssemblyInfo("Сборка остановлена");
          setAssemblyJobId("");
          setIsAssembling(false);
          return;
        }
      } catch (e) {
        if (!assemblyPollingActiveRef.current) return;
        stopAssemblyPolling();
        setAssemblyBuildState("error");
        setAssemblyError(String(e?.message || e || "Ошибка запроса статуса"));
        setAssemblyJobId("");
        setIsAssembling(false);
        return;
      }

      if (!assemblyPollingActiveRef.current) return;
      assemblyPollTimerRef.current = setTimeout(tick, 900);
    };

    tick();
  }, [assemblyPayload.scenes.length, setNodes, stopAssemblyPolling]);

  useEffect(() => {
    return () => stopAssemblyPolling();
  }, [stopAssemblyPolling]);

  const handleAssemblyBuild = useCallback(async () => {
    if (isAssembling) return;
    if (!assemblyPayload.scenes.length) return;

    const abortController = new AbortController();
    assemblyAbortControllerRef.current = abortController;
    setAssemblyError("");
    setAssemblyInfo("");
    setAssemblyResult(null);
    setIsAssemblyStale(false);
    setAssemblyProgressPercent(0);
    setAssemblyStage("");
    setAssemblyStageLabel("");
    setAssemblyStageCurrent(0);
    setAssemblyStageTotal(0);
    setAssemblyJobId("");

    setIsAssembling(true);
    setAssemblyBuildState("building");

    try {
      const assembleUrl = API_BASE ? `${API_BASE}/api/clip/assemble` : "/api/clip/assemble";
      const res = await fetch(assembleUrl, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(assemblyPayload),
        signal: abortController.signal,
      });
      let out = null;
      try {
        out = await res.json();
      } catch {
        out = null;
      }
      if (!res.ok) throw new Error(String(out?.detail || out?.message || out?.error || `HTTP ${res.status}`));
      const jobId = String(out?.jobId || "").trim();
      if (!jobId) throw new Error("Сборка запущена, но jobId не получен");

      setAssemblyJobId(jobId);
      startAssemblyPolling(jobId);
    } catch (e) {
      if (e?.name === "AbortError") {
        setAssemblyBuildState("idle");
        setAssemblyInfo("Сборка остановлена");
        return;
      }
      setAssemblyBuildState("error");
      setAssemblyResult(null);
      setAssemblyError(String(e?.message || e));
      setIsAssembling(false);
    } finally {
      if (assemblyAbortControllerRef.current === abortController) {
        assemblyAbortControllerRef.current = null;
      }
    }
  }, [assemblyPayload, isAssembling, startAssemblyPolling]);

  const handleAssemblyStop = useCallback(() => {
    assemblyAbortControllerRef.current?.abort();
    stopAssemblyPolling();
    if (assemblyJobId) {
      const stopUrl = API_BASE
        ? `${API_BASE}/api/clip/assemble/stop/${encodeURIComponent(assemblyJobId)}`
        : `/api/clip/assemble/stop/${encodeURIComponent(assemblyJobId)}`;
      fetch(stopUrl, { method: "POST", credentials: "include" }).catch(() => {});
    }
    setIsAssembling(false);
    setAssemblyBuildState("idle");
    setAssemblyInfo("Сборка остановлена");
    setAssemblyJobId("");
  }, [assemblyJobId, stopAssemblyPolling]);

  const handleAssemblyBuildRef = useRef(handleAssemblyBuild);
  const handleAssemblyStopRef = useRef(handleAssemblyStop);

  useEffect(() => {
    const changed = handleAssemblyBuildRef.current !== handleAssemblyBuild;
    if (changed) {
      console.debug("[CLIP TRACE] assembly build callback refreshed", {
        changed,
        payloadSignature: assemblyPayloadSignature,
      });
    }
    handleAssemblyBuildRef.current = handleAssemblyBuild;
  }, [assemblyPayloadSignature, handleAssemblyBuild]);

  useEffect(() => {
    const changed = handleAssemblyStopRef.current !== handleAssemblyStop;
    if (changed) {
      console.debug("[CLIP TRACE] assembly stop callback refreshed", {
        changed,
        jobId: assemblyJobId,
      });
    }
    handleAssemblyStopRef.current = handleAssemblyStop;
  }, [assemblyJobId, handleAssemblyStop]);

  const stableHandleAssemblyBuild = useCallback((...args) => {
    return handleAssemblyBuildRef.current?.(...args);
  }, []);

  const stableHandleAssemblyStop = useCallback((...args) => {
    return handleAssemblyStopRef.current?.(...args);
  }, []);

  const assemblyStatus = useMemo(() => {
    const hasVideoScenes = assemblyPayload.scenes.length > 0;
    if (isAssembling) return "building";
    if (assemblyBuildState === "done" && assemblyResult?.finalVideoUrl) return "done";
    if (assemblyBuildState === "error") return "error";
    if (!hasVideoScenes && !assemblyResult?.finalVideoUrl) return "empty";
    return "ready";
  }, [isAssembling, assemblyBuildState, assemblyPayload.scenes.length, assemblyResult?.finalVideoUrl]);

  const assemblyNodeDataPatch = useMemo(() => ({
    totalScenes: assemblyScenesForPayload.length,
    readyScenes: assemblyReadySceneCount,
    hasAudio: assemblyHasAudio,
    format: assemblyFormat,
    durationSec: assemblyDurationEstimateSec,
    scenesSource: assemblyScenesSource,
    sourceLabel: assemblySourceLabel,
    introLabel: assemblyIntroLabel,
    hasIntro: assemblyHasIntro,
    introDurationSec: assemblyIntroDurationSec,
    introTitle: assemblyIntroTitle,
    debugSummary: assemblyDebugSummary,
    canAssemble: assemblyCanAssemble,
    isAssembling,
    status: assemblyStatus,
    result: assemblyResult,
    errorMessage: assemblyError,
    infoMessage: assemblyInfo,
    assemblyJobId,
    progressPercent: assemblyProgressPercent,
    assemblyStage,
    assemblyStageLabel,
    assemblyStageCurrent,
    assemblyStageTotal,
    isStale: isAssemblyStale,
    onAssemble: stableHandleAssemblyBuild,
    onStopAssemble: stableHandleAssemblyStop,
  }), [
    assemblyCanAssemble,
    assemblyDebugSummary,
    assemblyDurationEstimateSec,
    assemblyError,
    assemblyFormat,
    assemblyHasAudio,
    assemblyHasIntro,
    assemblyInfo,
    assemblyIntroDurationSec,
    assemblyIntroLabel,
    assemblyIntroTitle,
    assemblyJobId,
    assemblyProgressPercent,
    assemblyReadySceneCount,
    assemblyResult,
    assemblyScenesForPayload.length,
    assemblyScenesSource,
    assemblySourceLabel,
    assemblyStage,
    assemblyStageCurrent,
    assemblyStageLabel,
    assemblyStageTotal,
    assemblyStatus,
    isAssembling,
    isAssemblyStale,
    stableHandleAssemblyBuild,
    stableHandleAssemblyStop,
  ]);

  const assemblyNodePatchDepsSnapshotRef = useRef(null);

  useEffect(() => {
    const depsSnapshot = {
      assemblyNodeDataPatch,
      assemblyResult,
      assemblyBuildState,
      assemblyPayloadSignature,
      isAssemblyStale,
      isAssembling,
    };
    const prevDepsSnapshot = assemblyNodePatchDepsSnapshotRef.current;
    const changedDeps = [];
    if (!prevDepsSnapshot || prevDepsSnapshot.assemblyNodeDataPatch !== depsSnapshot.assemblyNodeDataPatch) changedDeps.push("assemblyNodeDataPatch");
    if (!prevDepsSnapshot || prevDepsSnapshot.assemblyResult !== depsSnapshot.assemblyResult) changedDeps.push("assemblyResult");
    if (!prevDepsSnapshot || prevDepsSnapshot.assemblyBuildState !== depsSnapshot.assemblyBuildState) changedDeps.push("assemblyBuildState");
    if (!prevDepsSnapshot || prevDepsSnapshot.assemblyPayloadSignature !== depsSnapshot.assemblyPayloadSignature) changedDeps.push("assemblyPayloadSignature");
    if (!prevDepsSnapshot || prevDepsSnapshot.isAssemblyStale !== depsSnapshot.isAssemblyStale) changedDeps.push("isAssemblyStale");
    if (!prevDepsSnapshot || prevDepsSnapshot.isAssembling !== depsSnapshot.isAssembling) changedDeps.push("isAssembling");
    assemblyNodePatchDepsSnapshotRef.current = depsSnapshot;

    console.debug("[CLIP TRACE] assembly node sync effect enter", {
      changedDeps,
      payloadSignature: assemblyPayloadSignature,
      buildState: assemblyBuildState,
      isAssemblyStale,
      isAssembling,
    });

    setNodes((prev) => {
      let didChange = false;
      const next = prev.map((n) => {
        if (n.type !== "assemblyNode") return n;
        const hasChanges = Object.entries(assemblyNodeDataPatch).some(([key, value]) => !Object.is(n?.data?.[key], value));
        if (!hasChanges) return n;
        console.debug("[CLIP TRACE] assembly node sync setNodes", {
          nodeId: n.id,
          changedKeys: Object.entries(assemblyNodeDataPatch)
            .filter(([key, value]) => !Object.is(n?.data?.[key], value))
            .map(([key]) => key),
          payloadSignature: assemblyPayloadSignature,
        });
        didChange = true;
        return {
          ...n,
          data: {
            ...n.data,
            ...assemblyNodeDataPatch,
          },
        };
      });
      if (!didChange) {
        console.debug("[CLIP TRACE] assembly node sync setNodes skipped", {
          reason: "no_patch_changes",
          payloadSignature: assemblyPayloadSignature,
        });
      }
      return didChange ? next : prev;
    });
  }, [
    assemblyBuildState,
    assemblyNodeDataPatch,
    assemblyPayloadSignature,
    assemblyResult,
    isAssemblyStale,
    isAssembling,
    setNodes,
  ]);

  const nodesRef = useRef([]);
  const edgesRef = useRef([]);
  const refUploadGuardRef = useRef(new Map());

  useEffect(() => {
    nodesRef.current = nodes || [];
  }, [nodes]);

  useEffect(() => {
    edgesRef.current = edges || [];
  }, [edges]);

  useEffect(() => {
    const nodesNow = nodesRef.current || [];
    const edgesNow = edgesRef.current || [];
    const invalidBrainIds = nodesNow
      .filter((n) => n.type === "brainNode")
      .filter((brainNode) => {
        if (brainNode?.data?.isParsing) return false;
        const hasPlanData = !!brainNode?.data?.scenePlan || !!brainNode?.data?.lastPlanMeta || (Array.isArray(brainNode?.data?.scenes) && brainNode.data.scenes.length > 0);
        if (!hasPlanData) return false;
        const currentSig = collectBrainPlannerInput({ brainNodeId: brainNode.id, nodesList: nodesNow, edgesList: edgesNow }).signature;
        const plannedSig = String(brainNode?.data?.plannerInputSignature || "");
        return !!plannedSig && plannedSig !== currentSig;
      })
      .map((x) => x.id);

    if (!invalidBrainIds.length) return;

    const invalidBrainIdSet = new Set(invalidBrainIds);
    const staleTargetSet = new Set(
      edgesNow
        .filter((e) => invalidBrainIdSet.has(e.source))
        .map((e) => e.target),
    );

    const hasNodeChanges = nodesNow.some((n) => {
      if (invalidBrainIdSet.has(n.id)) {
        return n?.data?.scenePlan !== null
          || n?.data?.lastPlanMeta !== null
          || n?.data?.plannerInputSignature !== null
          || n?.data?.plannerState !== "stale";
      }

      if (!staleTargetSet.has(n.id)) return false;

      if (n.type === "storyboardNode" || n.type === "resultsNode" || n.type === "assemblyNode") {
        return n?.data?.isStale !== true;
      }

      return false;
    });

    setNodes((prev) => {
      let changed = false;
      const next = prev.map((n) => {
        if (!invalidBrainIdSet.has(n.id)) return n;

        const brainNeedsUpdate = n?.data?.scenePlan !== null
          || n?.data?.lastPlanMeta !== null
          || n?.data?.plannerInputSignature !== null
          || n?.data?.plannerState !== "stale";

        if (!brainNeedsUpdate) return n;

        changed = true;
        return {
          ...n,
          data: {
            ...n.data,
            scenePlan: null,
            lastPlanMeta: null,
            plannerInputSignature: null,
            plannerState: "stale",
          },
        };
      });

      const withStaleTargets = next.map((n) => {
        if (!staleTargetSet.has(n.id)) return n;
        if (n.type === "storyboardNode" || n.type === "resultsNode") {
          if (n?.data?.isStale === true) return n;
          changed = true;
          return { ...n, data: { ...n.data, isStale: true } };
        }
        if (n.type === "assemblyNode") {
          if (n?.data?.isStale === true) return n;
          changed = true;
          return { ...n, data: { ...n.data, isStale: true } };
        }
        return n;
      });

      if (!changed) return prev;
      return withStaleTargets;
    });

    if (hasNodeChanges || !isAssemblyStale) {
      setIsAssemblyStale(true);
    }
  }, [nodes, edges, isAssemblyStale, setNodes]);


  const removeNode = useCallback((nodeId) => {
    setNodes((prev) => prev.filter((n) => n.id !== nodeId));
    setEdges((prev) => prev.filter((e) => e.source !== nodeId && e.target !== nodeId));
  }, [setNodes, setEdges]);

  
  // wire handlers into node.data (keeps render simple)
  const bindHandlers = useCallback(
    (ns, options = {}) => {
      const effectiveNodes = Array.isArray(options?.nodesNow) ? options.nodesNow : ns;
      const effectiveEdges = Array.isArray(options?.edgesNow) ? options.edgesNow : (edgesRef.current || []);
      const traceReason = String(options?.traceReason || "").trim() || "default";
      console.log("[CLIP TRACE] bindHandlers executed", {
        nodesCount: Array.isArray(ns) ? ns.length : "unknown",
        edgesCount: Array.isArray(effectiveEdges) ? effectiveEdges.length : "unknown",
        reason: traceReason,
        timestamp: Date.now()
      });
      if (CLIP_TRACE_BRAIN_REFRESH) {
        console.info("[CLIP TRACE BRAIN REFRESH] bindHandlers context", {
          reason: traceReason,
          nodesCount: Array.isArray(effectiveNodes) ? effectiveNodes.length : 0,
          edgesCount: Array.isArray(effectiveEdges) ? effectiveEdges.length : 0,
        });
      }

      return ns.map((n) => {
        const base = { ...n, data: { ...n.data, onRemoveNode: (nodeId) => removeNode(nodeId) } };
        if (n.type === "audioNode") {
          return {
            ...base,
            data: {
              ...base.data,
              onUpload: async (nodeId, file) => {
                // optimistic ui
                setNodes((prev) =>
                  prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: true } } : x))
                );
                try {
                  const out = await uploadAsset(file);
                  const dur = out?.url ? await getAudioDurationSec(out.url) : null;
                  setNodes((prev) =>
                    prev.map((x) =>
                      x.id === nodeId
                        ? {
                            ...x,
                            data: {
                              ...x.data,
                              uploading: false,
                              audioUrl: out?.url || "",
                              audioName: out?.name || file.name,
                              audioDurationSec: (out?.durationSec ?? dur ?? x.data?.audioDurationSec ?? null),
                            },
                          }
                        : x
                    )
                  );
                } catch (e) {
                  setNodes((prev) =>
                    prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: false } } : x))
                  );
                  alert(
                    "Не удалось загрузить аудио на сервер. Файл выбран, но не сохранён. Проверь backend /api/assets/upload."
                  );
                  console.error(e);
                }
              },
              onClear: (nodeId) => {
                setNodes((prev) =>
                  prev.map((x) =>
                    x.id === nodeId
                      ? { ...x, data: { ...x.data, audioUrl: "", audioName: "", audioDurationSec: null, uploading: false } }
                      : x
                  )
                );
              },
            },
          };
        }
        if (n.type === "textNode") {
          return {
            ...base,
            data: {
              ...base.data,
              onChange: (nodeId, value) => {
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, textValue: value } } : x)));
              },
            },
          };
        }
        if (n.type === "linkNode") {
          return {
            ...base,
            data: {
              ...base.data,
              onDraftChange: (nodeId, value) => {
                setNodes((prev) => prev.map((x) => {
                  if (x.id !== nodeId) return x;
                  return {
                    ...x,
                    data: {
                      ...x.data,
                      draftUrl: value,
                      urlStatus: normalizeLinkUrl(value)
                        ? (x.data?.urlStatus === "invalid" ? "draft" : (x.data?.urlStatus || "draft"))
                        : "empty",
                      urlError: "",
                    },
                  };
                }));
              },
              onApplyUrl: (nodeId) => {
                setNodes((prev) => prev.map((x) => {
                  if (x.id !== nodeId) return x;
                  const draftUrl = normalizeLinkUrl(x?.data?.draftUrl ?? x?.data?.urlValue ?? "");
                  if (!draftUrl) {
                    return {
                      ...x,
                      data: {
                        ...x.data,
                        draftUrl: "",
                        urlValue: "",
                        urlStatus: "empty",
                        urlError: "",
                        savedPayload: null,
                        outputPayload: null,
                      },
                    };
                  }
                  const payload = buildLinkNodePayload(draftUrl);
                  if (!payload) {
                    return {
                      ...x,
                      data: {
                        ...x.data,
                        draftUrl,
                        urlStatus: "invalid",
                        urlError: "Некорректная ссылка. Используйте полный http/https URL.",
                        savedPayload: null,
                        outputPayload: null,
                      },
                    };
                  }
                  return {
                    ...x,
                    data: {
                      ...x.data,
                      draftUrl: payload.value,
                      urlValue: payload.value,
                      urlStatus: "ready",
                      urlError: "",
                      savedPayload: payload,
                      outputPayload: payload,
                    },
                  };
                }));
              },
            },
          };
        }
        if (n.type === "brainNode") {
          return {
            ...base,
            data: {
              ...base.data,
              onMode: (nodeId, value) => {
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, mode: value } } : x)));
              },
              onScenario: (nodeId, value) => {
                const nextValue = SCENARIO_OPTIONS.some((option) => option.value === value) ? value : "clip";
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, scenarioKey: nextValue, mode: nextValue } } : x)));
              },
              onShoot: (nodeId, value) => {
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, shootKey: value } } : x)));
              },
              onStyle: (nodeId, value) => {
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, styleKey: value } } : x)));
              },
              onFreezeStyle: (nodeId, checked) => {
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, freezeStyle: !!checked } } : x)));
              },
              onWantLipSync: (nodeId, checked) => {
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, wantLipSync: !!checked } } : x)));
              },
                            onClipMode: (nodeId, mode) => {
                const m = mode === "manual" ? "manual" : "auto";
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, clipMode: m } } : x)));
              },
onClipSec: (nodeId, value) => {
                const num = Number(value);
                const safe = Number.isFinite(num) ? Math.max(5, Math.min(3600, Math.floor(num))) : 30;
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, clipSec: safe } } : x)));
              },
              onStopParse: (nodeId) => {
                if (parseTimeoutRef.current) {
                  clearTimeout(parseTimeoutRef.current);
                  parseTimeoutRef.current = null;
                }
                if (parseControllerRef.current) {
                  parseControllerRef.current.abort();
                  parseControllerRef.current = null;
                }
                activeParseNodeRef.current = null;
                setNodes((prev) => prev.map((x) => (x.id === nodeId
                  ? { ...x, data: { ...x.data, isParsing: false, activeParseToken: null, lastParseError: null } }
                  : x)));
              },
              onParse: async (nodeId) => {
                const brainCurrent = nodesRef.current.find((x) => x.id === nodeId);
                if (brainCurrent?.data?.isParsing) return;

                if (parseTimeoutRef.current) {
                  clearTimeout(parseTimeoutRef.current);
                  parseTimeoutRef.current = null;
                }
                if (parseControllerRef.current) {
                  parseControllerRef.current.abort();
                  parseControllerRef.current = null;
                }

                const parseToken = parseTokenRef.current + 1;
                parseTokenRef.current = parseToken;
                const controller = new AbortController();
                parseControllerRef.current = controller;
                activeParseNodeRef.current = nodeId;
                let timeoutTriggered = false;

                const timeoutId = setTimeout(() => {
                  if (parseTokenRef.current !== parseToken) return;
                  timeoutTriggered = true;
                  controller.abort();
                  parseControllerRef.current = null;
                  parseTimeoutRef.current = null;
                  activeParseNodeRef.current = null;
                  setNodes((prev) => prev.map((x) => (x.id === nodeId
                    ? { ...x, data: { ...x.data, isParsing: false, activeParseToken: null, lastParseError: null } }
                    : x)));
                  notify({ type: "warning", message: "Разбор занял слишком много времени" });
                }, 95000);
                parseTimeoutRef.current = timeoutId;

                console.info("[CLIP STORAGE] scenario parse start", { nodeId, parseToken, accountKey, STORE_KEY });
                clearClipStoryboardStorageForCurrentAccount("scenario_parse_start");
                stopScenarioVideoPolling();
                scenarioVideoJobsBySceneRef.current.clear();

                setNodes((prev) => prev.map((x) => {
                  if (x.id === nodeId) {
                    return {
                      ...x,
                      data: {
                        ...x.data,
                        isParsing: true,
                        activeParseToken: parseToken,
                        lastParseError: null,
                        parsedAt: new Date().toISOString(),
                      },
                    };
                  }
                  if (x.type === "storyboardNode") {
                    return {
                      ...x,
                      data: {
                        ...x.data,
                        scenes: resetVideoStateBySceneId(normalizeSceneCollectionWithSceneId(x?.data?.scenes, "scene"), { panelField: "videoPanelActivated" }),
                      },
                    };
                  }
                  return x;
                }));

                try {
                  const planInput = collectBrainPlannerInput({ brainNodeId: nodeId, nodesList: nodesRef.current, edgesList: edgesRef.current });
                  const {
                    signature: plannerInputSignature,
                    mode,
                    textValue,
                    audioUrl,
                    characterRefs,
                    locationRefs,
                    propsRefs,
                    styleRefs,
                    styleRef,
                    scenarioKey,
                    shootKey,
                    styleKey,
                    freezeStyle,
                    wantLipSync,
                  } = planInput;

                  const audioType = wantLipSync ? "song" : "bg";

                  const payload = {
                    mode: "oneshot",
                    directGeminiStoryboardMode: true,
                    direct_gemini_storyboard_mode: true,
                    audioUrl: audioUrl || "",
                    text: textValue || "",
                    refsByRole: {
                      character_1: Array.isArray(characterRefs) ? characterRefs : [],
                      location: Array.isArray(locationRefs) ? locationRefs : [],
                      props: Array.isArray(propsRefs) ? propsRefs : [],
                      style: Array.isArray(styleRefs) ? styleRefs : [],
                    },
                    selectedCharacterRefUrl: Array.isArray(characterRefs) ? (characterRefs[0] || "") : "",
                    selectedStyleRefUrl: styleRef || (Array.isArray(styleRefs) ? (styleRefs[0] || "") : ""),
                    selectedLocationRefUrl: Array.isArray(locationRefs) ? (locationRefs[0] || "") : "",
                    selectedPropsRefUrls: Array.isArray(propsRefs) ? propsRefs : [],
                    options: {
                      mode,
                      scenarioKey,
                      shootKey,
                      styleKey,
                      freezeStyle,
                      wantLipSync,
                      audioType,
                    },
                  };

                  const out = await fetchJson("/api/clip/comfy/scenario-director/generate", {
                    method: "POST",
                    body: payload,
                    signal: controller.signal,
                  });
                  if (!out?.ok) throw new Error(out?.detail || out?.hint || "scenario_director_failed");
                  const normalizedDirector = normalizeScenarioDirectorApiResponse(out, {});

                  if (parseTokenRef.current !== parseToken) return;
                  const latestInput = collectBrainPlannerInput({ brainNodeId: nodeId, nodesList: nodesRef.current, edgesList: edgesRef.current });
                  if (latestInput.signature !== plannerInputSignature) return;

                  const audioDuration = Number(
                    normalizedDirector?.storyboardOut?.scenes?.[normalizedDirector?.storyboardOut?.scenes?.length - 1]?.time_end
                    || normalizedDirector?.directorOutput?.scenes?.[normalizedDirector?.directorOutput?.scenes?.length - 1]?.t1
                    || out?.audioDuration
                    || 30
                  );
                  const scenesRaw = Array.isArray(normalizedDirector?.storyboardOut?.scenes)
                    ? normalizedDirector.storyboardOut.scenes
                    : [];
                  const validation = out?.plannerDebug?.validation || {};

                  const existingScenesById = normalizeSceneCollectionWithSceneId(((nodesRef.current || []).find((x) => x.id === nodeId)?.data?.scenes || []), "scene");
                  const scenes = scenesRaw
                    .map((s, idx) => {
                      const t0 = Number(s.start ?? s.t0 ?? s.time_start ?? 0);
                      const t1 = Number(s.end ?? s.t1 ?? s.time_end ?? 0);
                      const prompt = String(s.imagePrompt || s.image_prompt || s.framePrompt || s.prompt || s.sceneText || s.scene_goal || `Scene ${idx + 1}`);
                      const transitionType = resolveSceneTransitionType(s);
                      const sceneId = buildCanonicalSceneId(s, idx, "scene");
                      return {
                        id: s.id || `s${String(idx + 1).padStart(2, "0")}`,
                        sceneId,
                        sceneRole: normalizeSceneRole(s.sceneRole, idx),
                        start: t0,
                        end: t1,
                        t0,
                        t1,
                        prompt,
                        transitionType,
                        imageStrategy: s.imageStrategy || deriveScenarioImageStrategy(s),
                        sceneText: s.sceneText || "",
                        imagePrompt: s.imagePrompt || "",
                        framePrompt: s.framePrompt || s.imagePrompt || s.prompt || "",
                        startFramePrompt: s.startFramePrompt || "",
                        endFramePrompt: s.endFramePrompt || "",
                        transitionActionPrompt: s.transitionActionPrompt || s.videoPrompt || "",
                        videoPrompt: s.videoPrompt || "",
                        imageUrl: s.imageUrl || "",
                        startImageUrl: s.startImageUrl || "",
                        endImageUrl: s.endImageUrl || "",
                        inheritPreviousEndAsStart: !!s.inheritPreviousEndAsStart,
                        startFrameSource: s.startFrameSource === "previous_end" ? "previous_end" : "manual",
                        ltxMode: s.ltxMode || s.ltx_mode || "",
                        needsTwoFrames: Boolean(s.needsTwoFrames ?? s.needs_two_frames),
                        requiresTwoFrames: Boolean(s.requiresTwoFrames ?? s.needsTwoFrames ?? s.needs_two_frames),
                        continuation: Boolean(s.continuation),
                        continuationFromPrevious: Boolean(s.continuationFromPrevious ?? s.continuation_from_previous),
                        requiresContinuation: Boolean(
                          s.requiresContinuation
                          ?? s.continuationFromPrevious
                          ?? s.continuation
                          ?? s.continuation_from_previous
                        ),
                        resolvedWorkflowKey: s.resolvedWorkflowKey
                          || resolveScenarioExplicitWorkflowKey(s)
                          || resolveScenarioWorkflowKey(s),
                        resolvedModelKey: s.resolvedModelKey || resolveScenarioExplicitModelKey(s) || "",
                        workflowFileOverride: s.workflowFileOverride || s.workflow_file_override || "",
                        modelFileOverride: s.modelFileOverride || s.model_file_override || "",
                        imageFormat: normalizeSceneImageFormat(s.imageFormat),
                        audioSliceUrl: s.audioSliceUrl || "",
                        audioSliceStartSec: Number(s.audioSliceStartSec ?? s.audioSliceT0 ?? t0),
                        audioSliceEndSec: Number(s.audioSliceEndSec ?? s.audioSliceT1 ?? t1),
                        audioSliceDurationSec: normalizeDurationSec(
                          s.audioSliceDurationSec ?? s.audioSliceBackendDurationSec ?? s.audioSliceExpectedDurationSec
                        ),
                        audioSliceStatus: String(s.audioSliceStatus || (s.audioSliceUrl ? "ready" : "")),
                        audioSliceError: s.audioSliceError || s.audioSliceLoadError || "",
                        audioSliceT0: Number(s.audioSliceStartSec ?? s.audioSliceT0 ?? t0),
                        audioSliceT1: Number(s.audioSliceEndSec ?? s.audioSliceT1 ?? t1),
                        audioSliceExpectedDurationSec: Number(
                          s.audioSliceExpectedDurationSec ??
                            Math.max(
                              0,
                              Number(s.audioSliceEndSec ?? s.audioSliceT1 ?? t1) -
                                Number(s.audioSliceStartSec ?? s.audioSliceT0 ?? t0)
                            )
                        ),
                        audioSliceBackendDurationSec: normalizeDurationSec(s.audioSliceBackendDurationSec),
                        audioSliceActualDurationSec: normalizeDurationSec(s.audioSliceActualDurationSec),
                        speechSafeAdjusted: Boolean(s.speechSafeAdjusted),
                        speechSafeShiftMs: Number(s.speechSafeShiftMs ?? 0),
                        sliceMayCutSpeech: Boolean(s.sliceMayCutSpeech),
                        audioSliceLoadError: s.audioSliceLoadError || s.audioSliceError || "",
                        videoUrl: s.videoUrl || "",
                        videoSourceImageUrl: s.videoSourceImageUrl || "",
                        videoPanelActivated: !!s.videoPanelActivated,
                        videoJobId: String(s.videoJobId || ""),
                        videoStatus: String(s.videoStatus || ""),
                        videoError: String(s.videoError || ""),
                        why: s.why || "",
                        audioType: s.audioType || "mixed",
                        sceneType: s.sceneType || "visual_rhythm",
                        hasVocals: !!s.hasVocals,
                        isLipSync: !!(s.isLipSync ?? s.lipSync),
                        lipSync: !!(s.lipSync ?? s.isLipSync),
                        renderMode: s.renderMode || (s.isLipSync || s.lipSync ? "avatar_lipsync" : "standard_video"),
                        sceneRenderProvider: resolveScenarioSceneVideoProvider(s),
                        sceneRenderModel: s.model || "",
                        requestedDurationSec: normalizeDurationSec(s.requestedDurationSec ?? Math.max(0, t1 - t0)),
                        mouthVisible: s.mouthVisible ?? null,
                        lyricFragment: s.lyricFragment || "",
                        timingReason: s.timingReason || s.why || "",
                        beatAnchor: s.beatAnchor || "",
                        performanceType: s.performanceType || "cinematic_visual",
                        shotType: s.shotType || "",
                        continuityMemory: s.continuityMemory && typeof s.continuityMemory === "object" ? s.continuityMemory : null,
                        previousContinuityMemory: s.previousContinuityMemory && typeof s.previousContinuityMemory === "object" ? s.previousContinuityMemory : null,
                      };
                    })
                    .filter((s) => Number.isFinite(s.t0) && Number.isFinite(s.t1) && s.t1 > s.t0);

                  const mergedScenes = mergeVideoStateBySceneId(scenes, existingScenesById, { panelField: "videoPanelActivated" });

                  setNodes((prev) => {
                    const updated = prev.map((x) =>
                      x.id === nodeId
                        ? {
                            ...x,
                            data: {
                              ...x.data,
                              scenes: mergedScenes,
                              isParsing: false,
                              activeParseToken: parseToken,
                              lastParseError: null,
                              lastPlanMeta: {
                                engine: out.engine || "gemini",
                                modelUsed: out.modelUsed || null,
                                fallbackUsed: !!out.fallbackUsed,
                                hint: out.hint || null,
                                error: out.error || null,
                                sceneCount: Number(validation.sceneCount ?? scenes.length),
                                warnings: Array.isArray(validation.warnings) ? validation.warnings : [],
                                rejectedReason: validation.rejectedReason || null,
                                repairRetryUsed: !!validation.repairRetryUsed,
                                audioHint: out?.plannerDebug?.audio?.hint || null,
                                plannerInputSignature,
                              },
                              scenePlan: {
                                engine: out.engine || "gemini",
                                modelUsed: out.modelUsed || null,
                                hint: out.hint || null,
                                audioDuration,
                                scenes: mergedScenes,
                                sceneCount: Number(validation.sceneCount ?? scenes.length),
                                warnings: Array.isArray(validation.warnings) ? validation.warnings : [],
                                rejectedReason: validation.rejectedReason || null,
                                repairRetryUsed: !!validation.repairRetryUsed,
                                audioHint: out?.plannerDebug?.audio?.hint || null,
                                refs: {
                                  character: characterRefs,
                                  location: locationRefs,
                                  props: propsRefs,
                                  style: styleRefs,
                                  propAnchorLabel: String(out?.propAnchor?.label || "").trim() || undefined,
                                  sessionCharacterAnchor: String(out?.sessionWorldAnchors?.character || "").trim() || undefined,
                                  sessionLocationAnchor: String(out?.sessionWorldAnchors?.location || "").trim() || undefined,
                                  sessionStyleAnchor: String(out?.sessionWorldAnchors?.style || "").trim() || undefined,
                                  sessionBaseline: out?.sessionBaseline && typeof out.sessionBaseline === "object"
                                    ? out.sessionBaseline
                                    : undefined,
                                },
                                settings: { scenarioKey, shootKey, styleKey, freezeStyle },
                              },
                              plannerInputSignature,
                              plannerState: "fresh",
                            },
                          }
                        : x
                    );

                    const targets = (edgesRef.current || []).filter((e) => e.source === nodeId).map((e) => e.target);
                    return updated.map((x) =>
                      targets.includes(x.id) && (x.type === "storyboardNode" || x.type === "resultsNode")
                        ? { ...x, data: { ...x.data, scenes, isStale: false } }
                        : x
                    );
                  });
                  setIsAssemblyStale(true);
                } catch (err) {
                  if (parseTokenRef.current !== parseToken) return;
                  if (err?.name === "AbortError") {
                    if (timeoutTriggered) return;
                    setNodes((prev) => prev.map((x) => (x.id === nodeId
                      ? { ...x, data: { ...x.data, isParsing: false, activeParseToken: null, lastParseError: null } }
                      : x)));
                    return;
                  }
                  console.error(err);
                  notify({ type: "error", message: "Ошибка разбора сцены" });
                  setNodes((prev) => prev.map((x) => (x.id === nodeId
                    ? { ...x, data: { ...x.data, isParsing: false, activeParseToken: parseToken, lastParseError: String(err?.message || err) } }
                    : x)));
                } finally {
                  clearTimeout(timeoutId);
                  if (parseTimeoutRef.current === timeoutId) {
                    parseTimeoutRef.current = null;
                  }
                  if (parseControllerRef.current === controller) {
                    parseControllerRef.current = null;
                  }
                  if (activeParseNodeRef.current === nodeId) {
                    activeParseNodeRef.current = null;
                  }
                }
              },
            },
          };
        }

        if (isComfyRefLikeNodeType(n.type)) {
          const normalizedRefData = normalizeComfyRefNodeData(n.type, base.data || {}, base?.data?.kind || "");
          return {
            ...base,
            data: {
              ...normalizedRefData,
              onField: (nodeId, key, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, [key]: key === "roleType" ? normalizeCharacterRoleType(value) : value } } : x))),
              onPickImage: async (nodeId, file) => {
                const pickedFiles = Array.isArray(file) ? file : (file ? [file] : []);
                if (!pickedFiles.length) return;
                setNodes((prev) => bindHandlers(prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: true, uploadSoftError: "" } } : x))));
                try {
                  const targetNode = nodesRef.current.find((x) => x.id === nodeId);
                  const normalizedTarget = normalizeRefData(targetNode?.data || {}, targetNode?.data?.kind || "");
                  const maxFiles = targetNode?.data?.kind === "ref_style" ? 1 : 5;
                  const prevRefs = normalizedTarget.refs;
                  const room = Math.max(0, maxFiles - (maxFiles === 1 ? 0 : prevRefs.length));
                  const queue = (maxFiles === 1 ? pickedFiles.slice(0, 1) : pickedFiles.slice(0, room));
                  const nodeKind = String(targetNode?.data?.kind || "").trim();
                  const debugTag = nodeKind === "ref_location" ? "LOCATION" : "REF";
                  const seenUploadKeys = new Set();
                  const guardBucket = refUploadGuardRef.current.get(nodeId) || new Map();
                  refUploadGuardRef.current.set(nodeId, guardBucket);
                  const guardNow = Date.now();

                  for (const oneFile of queue) {
                    const uploadKey = buildUploadGuardKey(oneFile);
                    if (!isUploadableFile(oneFile)) {
                      console.warn(`[${debugTag} upload skipped] invalid file payload`, {
                        nodeId,
                        kind: nodeKind,
                        uploadKey,
                        file: oneFile,
                      });
                      setNodes((prev) => prev.map((x) => (x.id === nodeId
                        ? { ...x, data: { ...x.data, uploadSoftError: "Лишний upload пропущен: в повторный шаг пришёл не File-объект." } }
                        : x)));
                      continue;
                    }
                    if (seenUploadKeys.has(uploadKey)) {
                      console.warn(`[${debugTag} upload skipped] duplicate file in same pick`, {
                        nodeId,
                        kind: nodeKind,
                        uploadKey,
                        file: getUploadDebugPayload(oneFile),
                      });
                      continue;
                    }
                    const guardMeta = guardBucket.get(uploadKey);
                    if (guardMeta && (guardMeta.status === "uploading" || (guardNow - Number(guardMeta.ts || 0)) < 5000)) {
                      console.warn(`[${debugTag} upload skipped] duplicate rapid re-upload`, {
                        nodeId,
                        kind: nodeKind,
                        uploadKey,
                        guardMeta,
                      });
                      continue;
                    }
                    seenUploadKeys.add(uploadKey);
                    guardBucket.set(uploadKey, { status: "uploading", ts: guardNow });
                    try {
                      const out = await uploadAsset(oneFile, { debugTag });
                      const url = String(out?.url || out?.assetUrl || "").trim();
                      if (!url) continue;
                      guardBucket.set(uploadKey, { status: "done", ts: Date.now(), url });
                      setNodes((prev) => prev.map((x) => {
                        if (x.id !== nodeId) return x;
                        const nextPrevRefs = normalizeRefData(x?.data || {}, x?.data?.kind || "").refs;
                        const nextMax = x?.data?.kind === "ref_style" ? 1 : 5;
                        const nextRefs = nextMax === 1
                          ? [{ url, name: out?.name || oneFile.name }]
                          : nextPrevRefs.concat({ url, name: out?.name || oneFile.name }).slice(0, nextMax);
                        return { ...x, data: { ...x.data, refs: nextRefs, refStatus: nextRefs.length ? "draft" : "empty", refShortLabel: "", refDetailsOpen: false, refHiddenProfile: null, refAnalysisError: "", uploadSoftError: "" } };
                      }));
                    } catch (err) {
                      console.error(err);
                      const responseDetail = extractUploadErrorDetail(err);
                      console.warn(`[${debugTag} upload error detail]`, {
                        nodeId,
                        kind: nodeKind,
                        uploadKey,
                        responseDetail,
                        file: getUploadDebugPayload(oneFile),
                      });
                      guardBucket.set(uploadKey, { status: "failed", ts: Date.now(), error: String(err?.message || err) });
                      setNodes((prev) => prev.map((x) => {
                        if (x.id !== nodeId) return x;
                        const existingRefs = normalizeRefData(x?.data || {}, x?.data?.kind || "").refs;
                        const fallbackStatus = deriveRefStatusAfterUploadError(x?.data || {}, existingRefs);
                        const softErrorMessage = existingRefs.length
                          ? `Upload завершился ошибкой, но уже загруженные изображения сохранены. ${responseDetail}`.trim()
                          : `Не удалось загрузить изображение. ${responseDetail}`.trim();
                        return {
                          ...x,
                          data: {
                            ...x.data,
                            refs: existingRefs,
                            refStatus: fallbackStatus,
                            uploadSoftError: softErrorMessage,
                          },
                        };
                      }));
                    }
                  }
                  for (const [guardKey, meta] of guardBucket.entries()) {
                    if ((Date.now() - Number(meta?.ts || 0)) > 30000) {
                      guardBucket.delete(guardKey);
                    }
                  }
                } finally {
                  setNodes((prev) => bindHandlers(prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: false } } : x))));
                }
              },
              onConfirmAdd: async (nodeId) => {
                const node = (nodesRef.current || []).find((x) => x.id === nodeId);
                const refs = normalizeRefData(node?.data || {}, node?.data?.kind || "").refs;
                const role = resolveRefRoleForNode(node);
                if (!refs.length || !role) return;
                setNodes((prev) => {
                  const nextNodes = prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, refStatus: "loading", refAnalysisError: "" } } : x));
                  return bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: "analyze-ref-node:loading" });
                });
                try {
                  const response = await fetchJson(`/api/clip/comfy/analyze-ref-node`, { method: "POST", body: { role, refs } });
                  const analyzedAt = new Date().toISOString();
                  setNodes((prev) => {
                    const nextNodes = prev.map((x) => {
                    if (x.id !== nodeId) return x;
                    const mergedData = normalizeComfyRefNodeData(x.type, {
                      ...x.data,
                      refs,
                      refStatus: "ready",
                      refShortLabel: String(response?.shortLabel || "").trim(),
                      refDetailsOpen: false,
                      refHiddenProfile: response?.profile && typeof response.profile === "object" ? response.profile : null,
                      refAnalysisError: "",
                      refAnalyzedAt: analyzedAt,
                    }, x?.data?.kind || "");
                    if (CLIP_TRACE_COMFY_REFS) {
                      console.info("[CLIP TRACE COMFY REFS] analyze-ref-node applied", {
                        nodeId,
                        role,
                        refStatus: mergedData.refStatus,
                        refsCount: Array.isArray(mergedData?.refs) ? mergedData.refs.length : 0,
                        refShortLabel: mergedData.refShortLabel,
                        hasHiddenProfile: !!mergedData.refHiddenProfile,
                        refAnalyzedAt: mergedData.refAnalyzedAt,
                        refAnalysisError: mergedData.refAnalysisError,
                      });
                    }
                    if (CLIP_TRACE_BRAIN_REFRESH) {
                      console.info("[CLIP TRACE BRAIN REFRESH] analyze-ref-node apply", {
                        nodeId,
                        role,
                        refStatus: mergedData.refStatus,
                        refShortLabel: mergedData.refShortLabel,
                        refAnalyzedAt: mergedData.refAnalyzedAt,
                        refsCount: Array.isArray(mergedData?.refs) ? mergedData.refs.length : 0,
                        hasHiddenProfile: !!mergedData.refHiddenProfile,
                      });
                    }
                    return { ...x, data: mergedData };
                  });
                    return bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: "analyze-ref-node:applied" });
                  });
                } catch (err) {
                  console.error(err);
                  setNodes((prev) => {
                    const nextNodes = prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, refStatus: "error", refAnalysisError: String(err?.message || err || "analyze_failed") } } : x));
                    return bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: "analyze-ref-node:error" });
                  });
                }
              },
              onRemoveImage: (nodeId, idx) => {
                setNodes((prev) => bindHandlers(prev.map((x) => {
                  if (x.id !== nodeId) return x;
                  const prevRefs = normalizeRefData(x?.data || {}, x?.data?.kind || "").refs;
                  const nextRefs = prevRefs.filter((_, i) => i !== idx);
                  return { ...x, data: { ...x.data, refs: nextRefs, refStatus: nextRefs.length ? "draft" : "empty", refShortLabel: "", refDetailsOpen: false, refHiddenProfile: null, refAnalysisError: "" } };
                })));
              },
              onToggleDetails: (nodeId) => {
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, refDetailsOpen: !x?.data?.refDetailsOpen } } : x)));
              },
            },
          };
        }
        
        if (n.type === "storyboardNode") {
          const nodesById = new Map((Array.isArray(effectiveNodes) ? effectiveNodes : []).map((nodeItem) => [nodeItem.id, nodeItem]));
          const incomingPlanEdge = [...effectiveEdges]
            .reverse()
            .find((edge) => edge?.target === n.id && String(edge?.targetHandle || "") === "plan_in") || null;
          const sourceNode = incomingPlanEdge?.source ? (nodesById.get(incomingPlanEdge.source) || null) : null;
          const storyboardOut = extractNarrativeStoryboardOut({
            sourceNode,
            sourceHandle: String(incomingPlanEdge?.sourceHandle || ""),
          });
          const storyboardOutValue = sourceNode?.type === "comfyNarrative" ? (storyboardOut || null) : null;
          const storyboardScenes = Array.isArray(storyboardOut?.scenes)
            ? storyboardOut.scenes.map((scene, idx) => normalizeStoryboardOutScene(scene, idx))
            : null;
          const normalizedScenes = storyboardScenes || base.data?.scenes || [];
          const sceneGeneration = buildStoryboardSceneGenerationMap(normalizedScenes, base.data?.sceneGeneration);
          return {
            ...base,
            data: {
              ...base.data,
              storyboardOut: storyboardOutValue,
              scenes: normalizedScenes,
              sceneGeneration,
              onOpenScenario: (nodeId) => {
                try {
                  window.dispatchEvent(new CustomEvent("ps:clipOpenScenario", { detail: { nodeId } }));
                } catch (e) {}
              },
              onStoryboardSceneGenerationUpdate: (nodeId, sceneId, patch = {}) => {
                setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                  if (nodeItem.id !== nodeId || nodeItem.type !== "storyboardNode") return nodeItem;
                  const currentMap = nodeItem?.data?.sceneGeneration && typeof nodeItem.data.sceneGeneration === "object"
                    ? nodeItem.data.sceneGeneration
                    : {};
                  const current = currentMap[sceneId] && typeof currentMap[sceneId] === "object" ? currentMap[sceneId] : {};
                  return {
                    ...nodeItem,
                    data: {
                      ...nodeItem.data,
                      sceneGeneration: {
                        ...currentMap,
                        [sceneId]: {
                          ...current,
                          ...patch,
                          status: patch.status ? normalizeStoryboardGenerationStatus(patch.status) : normalizeStoryboardGenerationStatus(current.status),
                          updatedAt: new Date().toISOString(),
                        },
                      },
                    },
                  };
                })));
              },
              onStoryboardSceneGenerate: (nodeId, sceneId) => {
                setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                  if (nodeItem.id !== nodeId || nodeItem.type !== "storyboardNode") return nodeItem;
                  const currentMap = nodeItem?.data?.sceneGeneration && typeof nodeItem.data.sceneGeneration === "object"
                    ? nodeItem.data.sceneGeneration
                    : {};
                  const current = currentMap[sceneId] && typeof currentMap[sceneId] === "object" ? currentMap[sceneId] : {};
                  return {
                    ...nodeItem,
                    data: {
                      ...nodeItem.data,
                      sceneGeneration: {
                        ...currentMap,
                        [sceneId]: {
                          ...current,
                          status: "generating",
                          error: "",
                          updatedAt: new Date().toISOString(),
                        },
                      },
                    },
                  };
                })));

                window.setTimeout(() => {
                  setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                    if (nodeItem.id !== nodeId || nodeItem.type !== "storyboardNode") return nodeItem;
                    const currentMap = nodeItem?.data?.sceneGeneration && typeof nodeItem.data.sceneGeneration === "object"
                      ? nodeItem.data.sceneGeneration
                      : {};
                    const current = currentMap[sceneId] && typeof currentMap[sceneId] === "object" ? currentMap[sceneId] : {};
                    return {
                      ...nodeItem,
                      data: {
                        ...nodeItem.data,
                        sceneGeneration: {
                          ...currentMap,
                          [sceneId]: {
                            ...current,
                            status: "done",
                            montageReady: current.montageReady === true,
                            updatedAt: new Date().toISOString(),
                          },
                        },
                      },
                    };
                  })));
                }, 1200);
              },
            },
          };
        }

        if (n.type === "scenarioStoryboard") {
          const nodesById = new Map((Array.isArray(effectiveNodes) ? effectiveNodes : []).map((nodeItem) => [nodeItem.id, nodeItem]));
          const incomingEdge = [...effectiveEdges]
            .reverse()
            .find((edge) => edge?.target === n.id && String(edge?.targetHandle || "") === "scenario_storyboard_in") || null;
          const sourceNode = incomingEdge?.source ? (nodesById.get(incomingEdge.source) || null) : null;
          const sourceHandle = String(incomingEdge?.sourceHandle || "");
          const validScenarioSource = sourceNode?.type === "comfyNarrative" && sourceHandle === "storyboard_out";
          const sourceIsGenerating = validScenarioSource && sourceNode?.data?.isGenerating === true;
          const hasPendingScenarioOutputs = validScenarioSource && !!sourceNode?.data?.pendingOutputs;
          const storyboardOut = validScenarioSource ? extractNarrativeStoryboardOut({ sourceNode, sourceHandle }) : null;
          const directorOutput = validScenarioSource
            ? (sourceNode?.data?.pendingOutputs?.directorOutput || sourceNode?.data?.outputs?.directorOutput || null)
            : null;
          const rawScenarioResponse = validScenarioSource ? (sourceNode?.data?.pendingRawResponse || null) : null;
          const sourceScenarioRevision = validScenarioSource
            ? String(sourceNode?.data?.pendingScenarioRevision || sourceNode?.data?.scenarioRevision || "")
            : "";
          const sourceConnectedContextFingerprint = validScenarioSource
            ? String(sourceNode?.data?.connectedContextFingerprint || "")
            : "";
          const previousConnectedContextFingerprint = String(base?.data?.connectedContextFingerprint || "");
          const connectedContextChanged = !!sourceConnectedContextFingerprint
            && !!previousConnectedContextFingerprint
            && sourceConnectedContextFingerprint !== previousConnectedContextFingerprint;
          const sourceScenarioContextStale = Boolean(sourceNode?.data?.scenarioContextStale);
          const normalizedPackage = normalizeScenarioStoryboardPackage({ storyboardOut, directorOutput });
          if (CLIP_TRACE_SCENARIO_GRAPH) {
            console.debug("[SCENARIO GRAPH STRICT] storyboard source", {
              sourceNodeType: String(sourceNode?.type || ""),
              sourceHandle,
              validScenarioSource: !!validScenarioSource,
            });
          }
          if (CLIP_TRACE_VISUAL_LOCK) {
            console.debug("[SCENARIO VISUAL LOCK] package", {
              hasGlobalVisualLock: hasScenarioContractValue(normalizedPackage?.globalVisualLock),
              hasGlobalCameraProfile: hasScenarioContractValue(normalizedPackage?.globalCameraProfile),
            });
          }
          if (CLIP_TRACE_SCENARIO_TRANSFER) {
            const sampleScene = Array.isArray(normalizedPackage?.scenes) ? normalizedPackage.scenes[0] : null;
            console.debug("[SCENARIO TRANSFER] normalizeScenarioScene sample", buildScenarioTransferLogData(sampleScene || {}, sampleScene || {}));
          }
          const previousScenes = Array.isArray(base?.data?.scenes) ? base.data.scenes : [];
          const resetBeforeRequest = sourceIsGenerating && (!hasPendingScenarioOutputs || Boolean(base?.data?.scenarioResetInFlight));
          const resetBecauseConnectedContextChanged = !sourceIsGenerating && (sourceScenarioContextStale || connectedContextChanged);
          const normalizedScenes = Array.isArray(normalizedPackage?.scenes) && normalizedPackage.scenes.length
            ? normalizedPackage.scenes
            : ((resetBeforeRequest || resetBecauseConnectedContextChanged) ? [] : previousScenes);
          const previousRevision = String(base?.data?.storyboardRevision || "");
          const nextRevision = sourceScenarioRevision || previousRevision;
          const revisionChanged = previousRevision !== nextRevision;
          const previousStoryboardSignature = String(
            base?.data?.storyboardSignature
            || buildSceneSignature(previousScenes, "scene")
            || ""
          );
          const nextStoryboardSignature = String(buildSceneSignature(normalizedScenes, "scene") || "");
          const storyboardSignatureChanged = !!nextStoryboardSignature && previousStoryboardSignature !== nextStoryboardSignature;
          const storyboardRunChanged = revisionChanged;
          const previousBySceneId = new Map(
            previousScenes.map((sceneItem, idx) => [String(sceneItem?.sceneId || `S${idx + 1}`), sceneItem])
          );
          const previousSceneSignatureById = new Map(
            previousScenes.map((sceneItem, idx) => {
              const sceneId = String(sceneItem?.sceneId || `S${idx + 1}`);
              return [sceneId, buildScenarioScenePackageSignature(sceneItem)];
            })
          );
          const timelineTranscriptPhrases = normalizeTimelinePhraseRows(storyboardOut?.transcript);
          const timelineSemanticPhrases = normalizeTimelinePhraseRows(storyboardOut?.semanticTimeline);
          const baseScenes = normalizedScenes.map((sceneItem, idx) => {
            const sceneId = String(sceneItem?.sceneId || `S${idx + 1}`);
            const cleanedScene = stripScenarioGeneratedAssets(sceneItem);
            const persistedScene = previousBySceneId.get(sceneId);
            const previousSceneSignature = String(previousSceneSignatureById.get(sceneId) || "");
            const nextSceneSignature = buildScenarioScenePackageSignature(cleanedScene);
            const semanticChanged = !previousSceneSignature || previousSceneSignature !== nextSceneSignature;
            const shouldPreserveAssets = !!persistedScene && !storyboardRunChanged && !semanticChanged;
            if (CLIP_TRACE_SCENARIO_SCENE_ASSETS) {
              console.debug("[SCENARIO REBIND ASSET DECISION]", {
                nodeId: String(n?.id || ""),
                storyboardRunChanged,
                revisionChanged,
                storyboardSignatureChanged,
                previousSceneSignature,
                nextSceneSignature,
                sceneId,
                preservedAssets: shouldPreserveAssets,
              });
            }
            if (!shouldPreserveAssets) return cleanedScene;
            const persistedAssets = {};
            SCENARIO_GENERATED_ASSET_FIELDS.forEach((key) => {
              if (Object.prototype.hasOwnProperty.call(persistedScene, key)) {
                persistedAssets[key] = persistedScene[key];
              }
            });
            return { ...cleanedScene, ...persistedAssets };
          });
          const scenes = baseScenes.map((sceneItem, idx) => {
            const sceneId = String(sceneItem?.sceneId || `S${idx + 1}`).trim() || `S${idx + 1}`;
            const phraseMatch = getScenePhrasesByTime(sceneItem, timelineTranscriptPhrases, timelineSemanticPhrases, 0);
            const nextScene = {
              ...sceneItem,
              localPhrase: phraseMatch.primaryPhrase || String(sceneItem?.localPhrase || "").trim(),
              scenePhraseTexts: phraseMatch.matchedPhraseTexts,
              matchedPhraseTexts: phraseMatch.matchedPhraseTexts,
              scenePhraseCount: Number(phraseMatch.phraseCount || 0),
              scenePhraseSource: phraseMatch.sourceUsed,
            };
            console.debug("[SCENARIO SCENE PHRASES]", {
              sceneId,
              t0: phraseMatch.sceneStart,
              t1: phraseMatch.sceneEnd,
              matchedPhraseCount: phraseMatch.phraseCount,
              matchedPhraseTexts: phraseMatch.matchedPhraseTexts,
              primaryPhrase: phraseMatch.primaryPhrase,
              sourceUsed: phraseMatch.sourceUsed,
            });
            return nextScene;
          });
          const uiStateUpdated = storyboardRunChanged || scenes.length !== previousScenes.length;
          if (storyboardRunChanged) {
            scenarioActivePollingJobIdsRef.current.clear();
            scenarioVideoJobsBySceneRef.current.clear();
            stopScenarioVideoPolling();
            safeDel(VIDEO_JOB_STORE_KEY);
          }
          console.debug("[SCENARIO APPLY RESPONSE]", {
            generateSuccess: validScenarioSource && !!sourceScenarioRevision,
            targetNodeId: String(n?.id || ""),
            hadStoryboardOut: !!storyboardOut,
            hadDirectorOutput: !!directorOutput,
            revisionChanged,
            storyboardSignatureChanged,
            storyboardRunChanged,
            normalizedScenesCount: scenes.length,
            previousRevision,
            nextRevision,
            previousStoryboardSignature,
            nextStoryboardSignature,
            uiStateUpdated,
          });
          console.debug("[SCENARIO SCENE ASSET SYNC]", {
            revisionChanged,
            storyboardSignatureChanged,
            storyboardRunChanged,
            preservedAssetsByDefault: !storyboardRunChanged,
            scenesCount: scenes.length,
            clearedAssetFieldsOnNewRun: storyboardRunChanged,
          });
          const previousSceneIds = collectSceneIds(previousScenes);
          const nextSceneIds = collectSceneIds(scenes);
          const previousGenerationMap = base?.data?.sceneGeneration && typeof base.data.sceneGeneration === "object"
            ? base.data.sceneGeneration
            : {};
          const generationSeedMap = storyboardRunChanged ? {} : previousGenerationMap;
          const sceneGeneration = buildStoryboardSceneGenerationMap(scenes, generationSeedMap);
          const nextSceneIdSet = new Set(nextSceneIds);
          const staleSceneGenerationKeys = Object.keys(previousGenerationMap).filter((sceneId) => !nextSceneIdSet.has(String(sceneId || "")));
          console.debug("[SCENARIO STORYBOARD REVISION SYNC]", {
            nodeId: String(n?.id || ""),
            previousStoryboardRevision: previousRevision,
            nextStoryboardRevision: nextRevision,
            previousSceneIds,
            nextSceneIds,
            clearedStaleSceneGenerationKeys: staleSceneGenerationKeys,
            resetSceneGenerationFromRevisionChange: storyboardRunChanged,
            recalculatedSummary: {
              sceneCount: scenes.length,
              photoCount: scenes.filter((sceneItem, idx) => {
                const sceneId = String(sceneItem?.sceneId || `S${idx + 1}`);
                const runtime = sceneGeneration[sceneId] && typeof sceneGeneration[sceneId] === "object" ? sceneGeneration[sceneId] : {};
                return (
                  !!String(sceneItem?.imageUrl || sceneItem?.startImageUrl || sceneItem?.endImageUrl || "").trim()
                  || String(runtime?.imageStatus || "").trim().toLowerCase() === "done"
                  || String(runtime?.startFrameStatus || "").trim().toLowerCase() === "done"
                  || String(runtime?.endFrameStatus || "").trim().toLowerCase() === "done"
                );
              }).length,
              videoCount: scenes.filter((sceneItem, idx) => {
                const sceneId = String(sceneItem?.sceneId || `S${idx + 1}`);
                const runtime = sceneGeneration[sceneId] && typeof sceneGeneration[sceneId] === "object" ? sceneGeneration[sceneId] : {};
                return !!String(sceneItem?.videoUrl || "").trim() || String(runtime?.videoStatus || "").trim().toLowerCase() === "done";
              }).length,
              status: scenes.length > 0 ? "ready" : "idle",
            },
          });
          const currentAudioData = base?.data?.audioData && typeof base.data.audioData === "object" ? base.data.audioData : {};
          const connectedAudioUrl = String(
            sourceNode?.data?.audioUrl
            || sourceNode?.data?.masterAudioUrl
            || sourceNode?.data?.plannerMeta?.plannerInput?.audioUrl
            || extractGlobalAudioUrlFromNodes(nodesRef.current || [])
            || ""
          ).trim();
          const connectedAudioDurationSec = Number(
            sourceNode?.data?.audioDurationSec
            || sourceNode?.data?.plannerMeta?.audioDurationSec
            || sourceNode?.data?.plannerMeta?.plannerInput?.audioDurationSec
            || extractGlobalAudioDurationFromNodes(nodesRef.current || [])
            || 0
          ) || 0;
          const packageMusicPromptSourceKindRaw = String(normalizedPackage?.musicPromptSourceKind || "").trim().toLowerCase();
          const packageMusicPromptSourceText = String(normalizedPackage?.musicPromptSourceText || "").trim();
          const packageFallbackMusicPrompt = String(normalizedPackage?.fallbackMusicPrompt || "").trim();
          const packageResolvedMusicPromptSourceKind = packageMusicPromptSourceKindRaw === "real" && packageMusicPromptSourceText
            ? "real"
            : packageFallbackMusicPrompt
              ? "fallback"
              : "empty";
          const packageResolvedMusicPromptSourceText = packageResolvedMusicPromptSourceKind === "real"
            ? packageMusicPromptSourceText
            : packageResolvedMusicPromptSourceKind === "fallback"
              ? packageFallbackMusicPrompt
              : "";
          const sourcePhrases = timelineTranscriptPhrases.length ? timelineTranscriptPhrases : timelineSemanticPhrases;
          const phraseBreakdown = sourcePhrases
            .filter((phrase) => !isShortMusicIntroPhraseRow(phrase))
            .map((phrase, idx) => ({
            sceneId: mapPhraseToSceneIdByTime(phrase, scenes),
            startSec: Number(phrase?.t0 ?? 0),
            endSec: Number(phrase?.t1 ?? phrase?.t0 ?? 0),
            text: String(phrase?.text || "").trim(),
            energy: String(phrase?.emotion || "").trim(),
            context: String(phrase?.meaning || phrase?.transitionHint || "").trim(),
          }));
          console.debug("[SCENARIO PHRASE MAP]", {
            phraseSourceUsed: timelineTranscriptPhrases.length ? "transcript" : (timelineSemanticPhrases.length ? "semanticTimeline" : "none"),
            phraseCount: phraseBreakdown.length,
            sceneCount: scenes.length,
            phrases: phraseBreakdown.map((item, idx) => ({
              idx,
              sceneId: item.sceneId,
              t0: item.startSec,
              t1: item.endSec,
              text: item.text,
            })),
          });
          const audioData = {
            ...currentAudioData,
            audioUrl: String(normalizedPackage?.audioUrl || connectedAudioUrl || currentAudioData.audioUrl || "").trim(),
            durationSec: Number(
              currentAudioData.durationSec
              ?? normalizedPackage?.audioDurationSec
              ?? connectedAudioDurationSec
              ?? 0
            ) || 0,
            phrases: phraseBreakdown,
            packageGlobalMusicPrompt: String(normalizedPackage?.musicPromptSourceText || normalizedPackage?.globalMusicPrompt || "").trim(),
            globalMusicPrompt: String(
              revisionChanged
                ? (packageResolvedMusicPromptSourceKind === "real" ? packageResolvedMusicPromptSourceText : "")
                : (
                  currentAudioData.globalMusicPrompt
                  || (packageResolvedMusicPromptSourceKind === "real" ? packageResolvedMusicPromptSourceText : "")
                  || ""
                )
            ).trim(),
            musicPromptSourceKind: String(
              revisionChanged
                ? packageResolvedMusicPromptSourceKind
                : (currentAudioData.musicPromptSourceKind || normalizedPackage?.musicPromptSourceKind || "empty")
            ).trim().toLowerCase(),
            musicPromptSourceText: String(
              revisionChanged
                ? packageResolvedMusicPromptSourceText
                : (
                  currentAudioData.musicPromptSourceText
                  || (packageResolvedMusicPromptSourceKind === "real" ? normalizedPackage?.musicPromptSourceText : "")
                  || (packageResolvedMusicPromptSourceKind === "fallback" ? packageFallbackMusicPrompt : "")
                  || ""
                )
            ).trim(),
            fallbackMusicPrompt: String(
              revisionChanged
                ? packageFallbackMusicPrompt
                : (currentAudioData.fallbackMusicPrompt || normalizedPackage?.fallbackMusicPrompt || "")
            ).trim(),
            musicPromptRu: String(
              revisionChanged
                ? (normalizedPackage?.musicPromptRu || "")
                : (currentAudioData.musicPromptRu || normalizedPackage?.musicPromptRu || "")
            ).trim(),
            musicPromptEn: String(
              revisionChanged
                ? (normalizedPackage?.musicPromptEn || "")
                : (currentAudioData.musicPromptEn || normalizedPackage?.musicPromptEn || "")
            ).trim(),
            musicStatus: String(
              revisionChanged
                ? (normalizedPackage?.musicStatus || "idle")
                : (currentAudioData.musicStatus || normalizedPackage?.musicStatus || "idle")
            ),
            musicUrl: String(
              revisionChanged
                ? (normalizedPackage?.musicUrl || "")
                : (currentAudioData.musicUrl || normalizedPackage?.musicUrl || "")
            ).trim(),
            musicError: String(
              revisionChanged
                ? (normalizedPackage?.musicError || "")
                : (currentAudioData.musicError || normalizedPackage?.musicError || "")
            ).trim(),
            musicTaskId: String(
              revisionChanged
                ? (normalizedPackage?.musicTaskId || "")
                : (currentAudioData.musicTaskId || normalizedPackage?.musicTaskId || "")
            ).trim(),
            musicPreviewUrl: String(
              revisionChanged
                ? (normalizedPackage?.musicPreviewUrl || "")
                : (currentAudioData.musicPreviewUrl || normalizedPackage?.musicPreviewUrl || "")
            ).trim(),
            musicFileName: String(
              revisionChanged
                ? (normalizedPackage?.musicFileName || "")
                : (currentAudioData.musicFileName || normalizedPackage?.musicFileName || "")
            ).trim(),
            musicSource: String(
              revisionChanged
                ? (normalizedPackage?.musicSource || "")
                : (currentAudioData.musicSource || normalizedPackage?.musicSource || "")
            ).trim(),
            musicDuration: Number(
              revisionChanged
                ? (normalizedPackage?.musicDuration ?? 0)
                : (currentAudioData.musicDuration ?? normalizedPackage?.musicDuration ?? 0)
            ) || 0,
          };
          const timelineDurationFromScenes = Number(
            scenes.reduce((maxValue, sceneItem) => {
              const displayTime = resolveSceneDisplayTime(sceneItem);
              return Math.max(maxValue, Number(displayTime.endSec || 0));
            }, 0)
          ) || 0;
          console.debug("[SCENARIO AUDIO LENGTH CHECK]", {
            revisionChanged,
            storyboardSignatureChanged,
            scenesCount: scenes.length,
            timelineDurationFromScenes,
            packageAudioDurationSec: Number(normalizedPackage?.audioDurationSec || 0) || 0,
            connectedAudioDurationSec,
            selectedDurationSec: Number(audioData?.durationSec || 0) || 0,
            hasConnectedAudioUrl: !!connectedAudioUrl,
            connectedAudioUrlPreview: connectedAudioUrl ? connectedAudioUrl.slice(0, 160) : "",
            packageAudioUrlPreview: String(normalizedPackage?.audioUrl || "").slice(0, 160),
          });
          const storyboardMusicPrompt = String(storyboardOut?.music_prompt || "").trim();
          const packageGlobalMusicPrompt = String(normalizedPackage?.globalMusicPrompt || "").trim();
          const fallbackMusicPrompt = String(normalizedPackage?.fallbackMusicPrompt || "").trim();
          const selectedMusicPromptSource = String(audioData?.musicPromptSourceKind || "empty").trim().toLowerCase() || "empty";
          const selectedMusicPromptLength = (
            selectedMusicPromptSource === "real"
              ? String(audioData?.globalMusicPrompt || "").trim().length
            : selectedMusicPromptSource === "fallback"
                ? String(audioData?.musicPromptSourceText || "").trim().length
                : 0
          );
          console.debug("[SCENARIO MUSIC PROMPT PAYLOAD]", {
            revisionChanged,
            storyboardMusicPromptLength: storyboardMusicPrompt.length,
            packageGlobalMusicPromptLength: packageGlobalMusicPrompt.length,
            fallbackMusicPromptLength: fallbackMusicPrompt.length,
            selectedMusicPromptKind: selectedMusicPromptSource,
            selectedMusicPromptLength,
            selectedMusicPromptPreview: String(
              selectedMusicPromptSource === "real"
                ? (audioData?.globalMusicPrompt || "")
                : selectedMusicPromptSource === "fallback"
                  ? (audioData?.musicPromptSourceText || "")
                  : ""
            ).slice(0, 120),
            willSendMusicGenerate: selectedMusicPromptSource === "real" && selectedMusicPromptLength > 0,
          });
          const musicPromptSource = revisionChanged
            ? "package"
            : (String(currentAudioData.globalMusicPrompt || currentAudioData.musicPromptRu || currentAudioData.musicPromptEn || "").trim()
              ? "currentAudioData"
              : "fallback");
          const usedCurrentAudioDataTextFallback = revisionChanged
            ? false
            : (
              !String(normalizedPackage?.musicPromptSourceText || "").trim()
              && !!String(currentAudioData.globalMusicPrompt || currentAudioData.musicPromptRu || currentAudioData.musicPromptEn || "").trim()
            );
          console.debug("[SCENARIO MUSIC TEXT SYNC]", {
            revisionChanged,
            musicPromptSourceKind: String(audioData?.musicPromptSourceKind || "empty").trim().toLowerCase() || "empty",
            musicPromptSourceTextLength: String(audioData?.musicPromptSourceText || "").trim().length,
            globalMusicPromptLength: String(audioData?.globalMusicPrompt || "").trim().length,
            musicPromptRuLength: String(audioData?.musicPromptRu || "").trim().length,
            musicPromptEnLength: String(audioData?.musicPromptEn || "").trim().length,
            usedCurrentAudioDataTextFallback,
          });
          const currentHasMusicResult = !!String(
            currentAudioData.musicUrl
            || currentAudioData.musicStatus
            || currentAudioData.musicError
            || currentAudioData.musicTaskId
            || currentAudioData.musicPreviewUrl
            || currentAudioData.musicFileName
            || currentAudioData.musicSource
          ).trim();
          const packageHasMusicResult = !!String(
            normalizedPackage?.musicUrl
            || normalizedPackage?.musicStatus
            || normalizedPackage?.musicError
            || normalizedPackage?.musicTaskId
            || normalizedPackage?.musicPreviewUrl
            || normalizedPackage?.musicFileName
            || normalizedPackage?.musicSource
          ).trim();
          const musicResultSource = revisionChanged
            ? (packageHasMusicResult ? "package" : "cleared")
            : (currentHasMusicResult ? "currentAudioData" : (packageHasMusicResult ? "package" : "cleared"));
          console.debug("[SCENARIO MUSIC SYNC]", {
            revisionChanged,
            musicSource: musicPromptSource,
            globalMusicPromptLength: String(audioData?.globalMusicPrompt || "").trim().length,
            musicPromptRuLength: String(audioData?.musicPromptRu || "").trim().length,
            musicPromptEnLength: String(audioData?.musicPromptEn || "").trim().length,
          });
          console.debug("[SCENARIO MUSIC RESULT SYNC]", {
            revisionChanged,
            musicPromptSource,
            musicResultSource,
            musicStatus: String(audioData?.musicStatus || "idle"),
            hasMusicUrl: !!String(audioData?.musicUrl || "").trim(),
          });
          if (CLIP_TRACE_SCENARIO_GLOBAL_MUSIC) {
            console.debug("[SCENARIO STORYBOARD MUSIC]", {
              packageHasGlobalMusicPrompt: !!String(normalizedPackage?.globalMusicPrompt || "").trim(),
              audioDataHasGlobalMusicPrompt: !!String(audioData?.globalMusicPrompt || "").trim(),
              musicPromptRuLength: String(audioData?.musicPromptRu || "").trim().length,
              musicPromptEnLength: String(audioData?.musicPromptEn || "").trim().length,
              globalMusicPromptLength: String(audioData?.globalMusicPrompt || "").trim().length,
            });
          }
          return {
            ...base,
            data: {
              ...base.data,
              scenes,
              format: resolvePreferredSceneFormat(
                normalizedPackage?.format,
                scenes.find((scene) => String(scene?.format || "").trim())?.format,
                scenes.find((scene) => String(scene?.imageFormat || "").trim())?.imageFormat,
                base.data?.format
              ),
              sceneGeneration,
              scenarioPackage: normalizedPackage,
              rawScenarioResponse,
              storyboardOut,
              directorOutput,
              scenarioMode: String(
                sourceNode?.data?.contentType
                || directorOutput?.contentType
                || storyboardOut?.contentType
                || normalizedPackage?.contentType
                || base?.data?.scenarioMode
                || "story"
              ).trim(),
              scenarioResetInFlight: resetBeforeRequest && scenes.length === 0,
              connectedContextFingerprint: sourceConnectedContextFingerprint || previousConnectedContextFingerprint,
              storyboardRevision: nextRevision,
              storyboardSignature: nextStoryboardSignature,
              scenesCount: scenes.length,
              status: resetBeforeRequest ? "generating" : (scenes.length > 0 ? "ready" : "idle"),
              audioData,
              onOpenScenarioStoryboard: onOpenScenarioStoryboard,
              onScenarioSceneUpdate: (nodeId, sceneId, patch = {}) => {
                if (CLIP_TRACE_SCENARIO_SCENE_ASSETS) {
                  console.debug("[SCENARIO SCENE PATCH]", {
                    nodeId: String(nodeId || ""),
                    sceneId: String(sceneId || ""),
                    patchedKeys: Object.keys(patch || {}),
                  });
                }
                setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                  if (nodeItem.id !== nodeId || nodeItem.type !== "scenarioStoryboard") return nodeItem;
                  const nextScenes = (Array.isArray(nodeItem?.data?.scenes) ? nodeItem.data.scenes : []).map((sceneItem) => (
                    String(sceneItem?.sceneId || "") === String(sceneId || "")
                      ? { ...sceneItem, ...patch }
                      : sceneItem
                  ));
                  const currentMap = nodeItem?.data?.sceneGeneration && typeof nodeItem.data.sceneGeneration === "object" ? nodeItem.data.sceneGeneration : {};
                  const currentRuntime = currentMap[sceneId] && typeof currentMap[sceneId] === "object" ? currentMap[sceneId] : {};
                  const runtimePatch = {};
                  if (Object.prototype.hasOwnProperty.call(patch, "imageUrl")) runtimePatch.imageStatus = String(patch.imageUrl || "").trim() ? "done" : "";
                  if (Object.prototype.hasOwnProperty.call(patch, "startImageUrl")) runtimePatch.startFrameStatus = String(patch.startImageUrl || "").trim() ? "done" : "";
                  if (Object.prototype.hasOwnProperty.call(patch, "endImageUrl")) runtimePatch.endFrameStatus = String(patch.endImageUrl || "").trim() ? "done" : "";
                  if (Object.prototype.hasOwnProperty.call(patch, "videoUrl")) runtimePatch.videoStatus = String(patch.videoUrl || "").trim() ? "done" : "";
                  if (Object.prototype.hasOwnProperty.call(patch, "videoStatus")) runtimePatch.videoStatus = String(patch.videoStatus || "");
                  if (Object.prototype.hasOwnProperty.call(patch, "videoJobId")) runtimePatch.videoJobId = String(patch.videoJobId || "");
                  if (Object.prototype.hasOwnProperty.call(patch, "videoError")) runtimePatch.videoError = String(patch.videoError || "");
                  if (Object.prototype.hasOwnProperty.call(patch, "audioSliceStatus")) runtimePatch.audioSliceStatus = String(patch.audioSliceStatus || "");
                  if (Object.prototype.hasOwnProperty.call(patch, "audioSliceUrl")) runtimePatch.audioSliceUrl = String(patch.audioSliceUrl || "");
                  if (Object.prototype.hasOwnProperty.call(patch, "audioSliceDurationSec")) runtimePatch.audioSliceDurationSec = normalizeDurationSec(patch.audioSliceDurationSec);
                  if (Object.prototype.hasOwnProperty.call(patch, "audioSliceError")) runtimePatch.audioSliceError = String(patch.audioSliceError || "");
                  if (Object.prototype.hasOwnProperty.call(patch, "audioSliceLoadError")) runtimePatch.audioSliceLoadError = String(patch.audioSliceLoadError || "");
                  if (!Object.keys(runtimePatch).length) return { ...nodeItem, data: { ...nodeItem.data, scenes: nextScenes } };
                  return {
                    ...nodeItem,
                    data: {
                      ...nodeItem.data,
                      scenes: nextScenes,
                      sceneGeneration: {
                        ...currentMap,
                        [sceneId]: {
                          ...currentRuntime,
                          ...runtimePatch,
                          updatedAt: new Date().toISOString(),
                        },
                      },
                    },
                  };
                })));
              },
              onScenarioSceneGenerate: (nodeId, sceneId, assetType = "scene", meta = {}) => {
                const normalizedAction = String(assetType || "scene").trim().toLowerCase();
                const normalizedSceneId = String(sceneId || "").trim();
                console.info("[SCENARIO VIDEO TRACE 3] page_onGenerateScene_enter", {
                  sceneId: normalizedSceneId,
                  selectedSceneId: String(scenarioEditor?.selectedSceneId || ""),
                  nodeId: String(nodeId || ""),
                  workflowKey: String(meta?.resolvedWorkflowKey || ""),
                  lipSyncRoute: String(meta?.resolvedWorkflowKey || "").trim().toLowerCase() === "lip_sync_music",
                  hasImageUrl: Boolean(meta?.hasImageUrl),
                  hasAudioSliceUrl: Boolean(meta?.hasAudioSliceUrl),
                  videoStatus: String(meta?.videoStatus || ""),
                  branch: normalizedAction || "scene",
                });
                const hasExplicitRequestedSceneId = !!normalizedSceneId;
                const sourceNode = (nodesRef.current || []).find((nodeItem) => nodeItem?.id === nodeId && nodeItem?.type === "scenarioStoryboard") || null;
                const rawScenes = Array.isArray(sourceNode?.data?.scenes) ? sourceNode.data.scenes : [];
                const normalizedScenes = normalizeSceneCollectionWithSceneId(rawScenes, "scene");
                const sceneIndexById = normalizedSceneId ? resolveScenarioSceneIndex(normalizedSceneId, normalizedScenes) : -1;
                const fallbackSceneIndex = !hasExplicitRequestedSceneId && normalizedScenes.length > 0 ? 0 : -1;
                const sceneIndex = sceneIndexById >= 0 ? sceneIndexById : fallbackSceneIndex;
                const targetScene = sceneIndex >= 0 ? normalizedScenes[sceneIndex] : null;
                const resolvedSceneId = String(targetScene?.sceneId || "").trim();
                const imageStrategy = String(targetScene?.imageStrategy || deriveScenarioImageStrategy(targetScene)).trim().toLowerCase() || "single";
                if (CLIP_TRACE_SCENARIO_EDITOR_GENERATE) {
                  const routedHandler = normalizedAction === "image"
                    ? "handleGenerateScenarioImage(single)"
                    : normalizedAction === "start_frame"
                      ? "handleGenerateScenarioImage(start)"
                      : normalizedAction === "end_frame"
                        ? "handleGenerateScenarioImage(end)"
                        : "handleScenarioGenerateVideo";
                  console.debug("[SCENARIO EDITOR GENERATE]", {
                    sceneId: normalizedSceneId,
                    actionType: normalizedAction || "scene",
                    imageStrategy,
                    routedHandler,
                    selectedTab: String(meta?.selectedTab || meta?.activeTab || ""),
                  });
                }
                if (hasExplicitRequestedSceneId && sceneIndexById < 0) {
                  const normalizedSceneIds = normalizedScenes.map((sceneItem, idx) => String(
                    sceneItem?.sceneId || sceneItem?.scene_id || sceneItem?.id || `S${idx + 1}` || ""
                  ).trim()).filter(Boolean);
                  const rawStoryboardSceneIds = rawScenes.map((sceneItem, idx) => String(
                    sceneItem?.sceneId || sceneItem?.scene_id || sceneItem?.id || `S${idx + 1}` || ""
                  ).trim()).filter(Boolean);
                  console.error("[SCENARIO EDITOR GENERATE EARLY RETURN] requested_scene_not_found", {
                    requestedSceneId: normalizedSceneId,
                    availableNormalizedSceneIds: normalizedSceneIds,
                    rawStoryboardSceneIds,
                    normalizedScenesLength: normalizedScenes.length,
                    rawScenesLength: rawScenes.length,
                    actionType: normalizedAction || "scene",
                  });
                  if (normalizedScenes.length > 0) {
                    const nextSafeSelectedIndex = Number.isFinite(scenarioEditor?.selected)
                      ? Math.min(Math.max(Number(scenarioEditor.selected), 0), normalizedScenes.length - 1)
                      : 0;
                    setScenarioEditor((prev) => ({ ...prev, selected: nextSafeSelectedIndex, selectedSceneId: String((normalizedScenes[nextSafeSelectedIndex] || {}).sceneId || "").trim() }));
                  }
                  setScenarioImageError("Выбранная сцена не найдена. Обновите выбор сцены и повторите генерацию.");
                  notify({
                    type: "error",
                    title: "Scene mismatch",
                    message: `Сцена ${normalizedSceneId} не найдена. Выберите актуальную сцену и повторите.`,
                  });
                  return;
                }
                if (sceneIndex < 0 || !targetScene) {
                  const lookupMap = normalizedScenes.map((sceneItem, idx) => ({
                    idx,
                    sceneId: String(sceneItem?.sceneId || ""),
                    scene_id: String(sceneItem?.scene_id || ""),
                    id: String(sceneItem?.id || ""),
                  }));
                  console.error("[SCENARIO EDITOR IMAGE EARLY RETURN] no_target_scene", {
                    selectedSceneId: normalizedSceneId,
                    selectedSceneIndex: sceneIndexById,
                    sceneIndex,
                    normalizedSceneIds: lookupMap.map((item) => item.sceneId || item.scene_id || item.id),
                    rawStoryboardSceneIds: rawScenes.map((sceneItem, idx) => String(
                      sceneItem?.sceneId || sceneItem?.scene_id || sceneItem?.id || `S${idx + 1}` || ""
                    ).trim()).filter(Boolean),
                    normalizedScenesLength: normalizedScenes.length,
                    rawScenesLength: rawScenes.length,
                    sceneCount: normalizedScenes.length,
                    actionType: normalizedAction || "scene",
                    lookupMap,
                  });
                  return;
                }
                if (!hasExplicitRequestedSceneId && sceneIndexById < 0 && fallbackSceneIndex === 0 && normalizedScenes[0]) {
                  console.warn("[SCENARIO EDITOR] safe default to first scene (no requested sceneId)", {
                    fallbackSceneId: String(normalizedScenes[0]?.sceneId || ""),
                    sceneCount: normalizedScenes.length,
                  });
                }
                setScenarioEditor((prev) => ({
                  ...prev,
                  selected: sceneIndex >= 0 ? sceneIndex : prev.selected,
                  selectedSceneId: resolvedSceneId || prev.selectedSceneId || "",
                }));

                setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                  if (nodeItem.id !== nodeId || nodeItem.type !== "scenarioStoryboard") return nodeItem;
                  const currentMap = nodeItem?.data?.sceneGeneration && typeof nodeItem.data.sceneGeneration === "object" ? nodeItem.data.sceneGeneration : {};
                  const currentRuntime = currentMap[resolvedSceneId] && typeof currentMap[resolvedSceneId] === "object" ? currentMap[resolvedSceneId] : {};
                  const runtimePatch = {
                    image: { imageStatus: "loading", imageError: "" },
                    start_frame: { startFrameStatus: "loading", startFrameError: "" },
                    end_frame: { endFrameStatus: "loading", endFrameError: "" },
                    video: { videoStatus: "loading", videoError: "" },
                    scene: { videoStatus: "loading", videoError: "" },
                  };
                  return {
                    ...nodeItem,
                    data: {
                      ...nodeItem.data,
                      sceneGeneration: {
                        ...currentMap,
                        [resolvedSceneId]: {
                          ...currentRuntime,
                          ...(runtimePatch[normalizedAction] || {}),
                          updatedAt: new Date().toISOString(),
                        },
                      },
                    },
                  };
                })));

                if (normalizedAction === "image") {
                  handleGenerateScenarioImage("single", { sceneIndex, sceneId: resolvedSceneId, normalizedScenes, rawScenes, ...meta });
                  return;
                }
                if (normalizedAction === "start_frame") {
                  handleGenerateScenarioImage("start", { sceneIndex, sceneId: resolvedSceneId, normalizedScenes, rawScenes, ...meta });
                  return;
                }
                if (normalizedAction === "end_frame") {
                  handleGenerateScenarioImage("end", { sceneIndex, sceneId: resolvedSceneId, normalizedScenes, rawScenes, ...meta });
                  return;
                }
                handleScenarioGenerateVideo({ sceneIndex, sceneId: resolvedSceneId, ...meta });
              },
              onScenarioMusicUpdate: (nodeId, patch = {}) => {
                setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                  if (nodeItem.id !== nodeId || nodeItem.type !== "scenarioStoryboard") return nodeItem;
                  const audioDataNow = nodeItem?.data?.audioData && typeof nodeItem.data.audioData === "object" ? nodeItem.data.audioData : {};
                  return {
                    ...nodeItem,
                    data: {
                      ...nodeItem.data,
                      audioData: {
                        ...audioDataNow,
                        ...patch,
                      },
                    },
                  };
                })));
              },
              onScenarioMusicGenerate: async (nodeId) => {
                const targetNode = (nodesRef.current || []).find((nodeItem) => nodeItem.id === nodeId && nodeItem.type === "scenarioStoryboard");
                const audioDataNow = targetNode?.data?.audioData && typeof targetNode.data.audioData === "object" ? targetNode.data.audioData : {};
                const emptyMusicResultPatch = {
                  musicUrl: "",
                  musicTaskId: "",
                  musicPreviewUrl: "",
                  musicFileName: "",
                  musicSource: "",
                  musicDuration: 0,
                };
                const selectedMusicPromptKind = String(audioDataNow?.musicPromptSourceKind || "empty").trim().toLowerCase() || "empty";
                const selectedMusicPromptText = String(
                  selectedMusicPromptKind === "real"
                    ? (audioDataNow?.globalMusicPrompt || audioDataNow?.musicPromptSourceText || audioDataNow?.musicPromptRu || audioDataNow?.musicPromptEn || "")
                    : selectedMusicPromptKind === "fallback"
                      ? (audioDataNow?.musicPromptSourceText || audioDataNow?.fallbackMusicPrompt || "")
                      : ""
                ).trim();
                const willSendMusicGenerate = selectedMusicPromptKind === "real" && !!selectedMusicPromptText;
                console.debug("[SCENARIO MUSIC PROMPT PAYLOAD]", {
                  revisionChanged: false,
                  storyboardMusicPromptLength: String(targetNode?.data?.storyboardOut?.music_prompt || "").trim().length,
                  packageGlobalMusicPromptLength: String(targetNode?.data?.scenarioPackage?.globalMusicPrompt || "").trim().length,
                  fallbackMusicPromptLength: String(audioDataNow?.fallbackMusicPrompt || "").trim().length,
                  selectedMusicPromptKind,
                  selectedMusicPromptLength: selectedMusicPromptText.length,
                  selectedMusicPromptPreview: selectedMusicPromptText.slice(0, 120),
                  willSendMusicGenerate,
                });
                if (!willSendMusicGenerate) {
                  const reason = selectedMusicPromptKind === "fallback"
                    ? "fallback_only"
                    : selectedMusicPromptKind === "empty"
                      ? "empty_prompt"
                      : "no_real_music_prompt";
                  console.debug("[SCENARIO MUSIC PROMPT BLOCKED]", { reason });
                  notify({
                    type: "warning",
                    title: "Music prompt unavailable",
                    message: reason === "fallback_only"
                      ? "Сценарный music prompt отсутствует: доступен только fallback mood/style/pacing."
                      : "Сценарный music prompt не предоставлен.",
                  });
                  setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                    if (nodeItem.id !== nodeId || nodeItem.type !== "scenarioStoryboard") return nodeItem;
                    const audioDataCurrent = nodeItem?.data?.audioData && typeof nodeItem.data.audioData === "object" ? nodeItem.data.audioData : {};
                    return {
                      ...nodeItem,
                      data: {
                        ...nodeItem.data,
                        audioData: {
                          ...audioDataCurrent,
                          musicStatus: "error",
                          musicError: reason,
                        },
                      },
                    };
                  })));
                  return;
                }
                const musicApiConfig = getScenarioMusicApiConfig();
                console.debug("[SCENARIO MUSIC API]", {
                  configured: musicApiConfig.configured,
                  endpoint: musicApiConfig.endpoint,
                  hasApiKey: musicApiConfig.hasApiKey,
                  willSend: willSendMusicGenerate && musicApiConfig.configured,
                  promptKind: selectedMusicPromptKind,
                  promptLength: selectedMusicPromptText.length,
                  status: musicApiConfig.configured ? "ready" : "error",
                });
                if (!musicApiConfig.configured) {
                  notify({
                    type: "warning",
                    title: "Music API not configured",
                    message: "Добавьте VITE_SCENARIO_MUSIC_API_URL и VITE_SCENARIO_MUSIC_API_KEY.",
                  });
                  setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                    if (nodeItem.id !== nodeId || nodeItem.type !== "scenarioStoryboard") return nodeItem;
                    const audioDataCurrent = nodeItem?.data?.audioData && typeof nodeItem.data.audioData === "object" ? nodeItem.data.audioData : {};
                    return {
                      ...nodeItem,
                      data: {
                        ...nodeItem.data,
                        audioData: {
                          ...audioDataCurrent,
                          ...emptyMusicResultPatch,
                          musicStatus: "error",
                          musicError: "music_api_not_configured",
                        },
                      },
                    };
                  })));
                  console.debug("[SCENARIO MUSIC RESULT SYNC]", {
                    status: "error",
                    resultFieldsCleared: true,
                    hasMusicUrl: false,
                    hasMusicTaskId: false,
                    hasMusicPreviewUrl: false,
                    musicError: "music_api_not_configured",
                  });
                  return;
                }
                const durationCandidates = [
                  { source: "durationSec", value: audioDataNow?.durationSec },
                  { source: "audioDurationSec", value: audioDataNow?.audioDurationSec },
                  { source: "nodeData", value: targetNode?.data?.audioDurationSec },
                  { source: "nodeData", value: targetNode?.data?.audioData?.durationSec },
                  { source: "nodeData", value: targetNode?.data?.audioData?.audioDurationSec },
                ];
                const pickedDuration = durationCandidates.find((candidate) => Number(candidate?.value) > 0);
                const resolvedDurationSec = pickedDuration ? Number(pickedDuration.value) : undefined;
                const durationSource = pickedDuration?.source || "none";
                const musicPayload = {
                  nodeId,
                  prompt: selectedMusicPromptText,
                  promptKind: selectedMusicPromptKind,
                  durationSec: resolvedDurationSec,
                };
                console.debug("[SCENARIO MUSIC PAYLOAD]", {
                  durationSec: resolvedDurationSec,
                  durationSource,
                  configured: musicApiConfig.configured,
                  willSend: willSendMusicGenerate && musicApiConfig.configured,
                  promptKind: selectedMusicPromptKind,
                });
                setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                  if (nodeItem.id !== nodeId || nodeItem.type !== "scenarioStoryboard") return nodeItem;
                  const audioDataCurrent = nodeItem?.data?.audioData && typeof nodeItem.data.audioData === "object" ? nodeItem.data.audioData : {};
                  return {
                    ...nodeItem,
                    data: {
                      ...nodeItem.data,
                      audioData: {
                        ...audioDataCurrent,
                        ...emptyMusicResultPatch,
                        musicStatus: "loading",
                        musicError: "",
                      },
                    },
                  };
                })));
                console.debug("[SCENARIO MUSIC RESULT SYNC]", {
                  status: "loading",
                  resultFieldsCleared: true,
                  hasMusicUrl: false,
                  hasMusicTaskId: false,
                  hasMusicPreviewUrl: false,
                  musicError: "",
                });
                try {
                  const musicResponse = await requestScenarioBackgroundMusic(musicPayload, { config: musicApiConfig });
                  const musicUrl = String(
                    musicResponse?.musicUrl
                    || musicResponse?.url
                    || musicResponse?.audioUrl
                    || ""
                  ).trim();
                  if (!musicUrl) throw new Error("music_url_missing");
                  const musicSource = String(musicResponse?.musicSource || musicResponse?.source || "api").trim() || "api";
                  const musicTaskId = String(musicResponse?.musicTaskId || musicResponse?.taskId || "").trim();
                  const musicPreviewUrl = String(musicResponse?.musicPreviewUrl || musicResponse?.previewUrl || "").trim();
                  const musicFileName = String(musicResponse?.musicFileName || musicResponse?.fileName || "").trim();
                  console.debug("[SCENARIO MUSIC API]", {
                    configured: musicApiConfig.configured,
                    endpoint: musicApiConfig.endpoint,
                    hasApiKey: musicApiConfig.hasApiKey,
                    willSend: true,
                    promptKind: selectedMusicPromptKind,
                    promptLength: selectedMusicPromptText.length,
                    status: "done",
                  });
                  setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                    if (nodeItem.id !== nodeId || nodeItem.type !== "scenarioStoryboard") return nodeItem;
                    const audioDataCurrent = nodeItem?.data?.audioData && typeof nodeItem.data.audioData === "object" ? nodeItem.data.audioData : {};
                    return {
                      ...nodeItem,
                      data: {
                        ...nodeItem.data,
                        audioData: {
                          ...audioDataCurrent,
                          musicStatus: "done",
                          musicError: "",
                          musicUrl,
                          musicSource,
                          musicTaskId,
                          musicPreviewUrl,
                          musicFileName,
                        },
                      },
                    };
                  })));
                  console.debug("[SCENARIO MUSIC RESULT SYNC]", {
                    status: "done",
                    resultFieldsCleared: false,
                    hasMusicUrl: !!musicUrl,
                    hasMusicTaskId: !!musicTaskId,
                    hasMusicPreviewUrl: !!musicPreviewUrl,
                    musicError: "",
                  });
                } catch (musicError) {
                  const message = String(musicError?.message || musicError || "music_generate_failed");
                  console.debug("[SCENARIO MUSIC API]", {
                    configured: musicApiConfig.configured,
                    endpoint: musicApiConfig.endpoint,
                    hasApiKey: musicApiConfig.hasApiKey,
                    willSend: true,
                    promptKind: selectedMusicPromptKind,
                    promptLength: selectedMusicPromptText.length,
                    status: "error",
                  });
                  setNodes((prev) => bindHandlers(prev.map((nodeItem) => {
                    if (nodeItem.id !== nodeId || nodeItem.type !== "scenarioStoryboard") return nodeItem;
                    const audioDataCurrent = nodeItem?.data?.audioData && typeof nodeItem.data.audioData === "object" ? nodeItem.data.audioData : {};
                    return {
                      ...nodeItem,
                      data: {
                        ...nodeItem.data,
                        audioData: {
                          ...audioDataCurrent,
                          ...emptyMusicResultPatch,
                          musicStatus: "error",
                          musicError: message,
                        },
                      },
                    };
                  })));
                  console.debug("[SCENARIO MUSIC RESULT SYNC]", {
                    status: "error",
                    resultFieldsCleared: true,
                    hasMusicUrl: false,
                    hasMusicTaskId: false,
                    hasMusicPreviewUrl: false,
                    musicError: message,
                  });
                }
              },
            },
          };
        }

        if (n.type === "comfyNarrative") {
          const nodesById = new Map((Array.isArray(effectiveNodes) ? effectiveNodes : []).map((nodeItem) => [nodeItem.id, nodeItem]));
          const connectedInputs = getNarrativeConnectedInputsSnapshot({
            node: n,
            nodesById,
            edges: effectiveEdges,
          });
          const resolvedSource = resolveNarrativeSource({
            ...(base.data || {}),
            connectedInputs,
          });
          const previousContextFingerprint = String(base?.data?.connectedContextFingerprint || "");
          const connectedContextFingerprint = buildNarrativeConnectedContextFingerprint(connectedInputs);
          const connectedContextChanged = previousContextFingerprint !== connectedContextFingerprint;
          const connectedCharacterRolesNow = ["ref_character_1", "ref_character_2", "ref_character_3"]
            .filter((handleId) => (Array.isArray(connectedInputs?.[handleId]?.refs) ? connectedInputs[handleId].refs.length > 0 : false))
            .map((handleId) => handleId.replace("ref_", ""));
          const connectedCharacterRolesPrev = ["ref_character_1", "ref_character_2", "ref_character_3"]
            .filter((handleId) => (Array.isArray(base?.data?.connectedInputs?.[handleId]?.refs) ? base.data.connectedInputs[handleId].refs.length > 0 : false))
            .map((handleId) => handleId.replace("ref_", ""));
          const rolePresenceChanged = JSON.stringify(connectedCharacterRolesNow) !== JSON.stringify(connectedCharacterRolesPrev);
          const character2Disconnected = connectedCharacterRolesPrev.includes("character_2") && !connectedCharacterRolesNow.includes("character_2");
          const shouldInvalidateScenario = connectedContextChanged && (rolePresenceChanged || character2Disconnected);
          const buildNarrativeGenerationState = ({ narrativeNodeId, nodesNow, edgesNow, traceReason = "narrative:generate" }) => {
            const safeNodes = Array.isArray(nodesNow) ? nodesNow : [];
            const safeEdges = Array.isArray(edgesNow) ? edgesNow : [];
            let requestPayload = null;
            const nextNodes = safeNodes.map((x) => {
              if (x.id !== narrativeNodeId) return x;
              const narrativeConnectedInputs = getNarrativeConnectedInputsSnapshot({
                node: x,
                nodesById: new Map(safeNodes.map((nodeItem) => [nodeItem.id, nodeItem])),
                edges: safeEdges,
              });
              const nextData = {
                ...x.data,
                connectedInputs: narrativeConnectedInputs,
              };
              const nextResolvedSource = resolveNarrativeSource(nextData);
              if (nextResolvedSource?.origin !== "connected" || !String(nextResolvedSource?.value || "").trim()) {
                return {
                  ...x,
                  data: {
                    ...nextData,
                    sourceOrigin: nextResolvedSource.origin,
                    resolvedSource: nextResolvedSource,
                    error: "NO_SOURCE",
                    isGenerating: false,
                    pendingOutputs: null,
                    pendingGeneratedAt: "",
                  },
                };
              }
              requestPayload = buildScenarioDirectorRequestPayload({
                ...nextData,
                sourceOrigin: nextResolvedSource.origin,
                resolvedSource: nextResolvedSource,
              });
              return {
                ...x,
                data: {
                  ...nextData,
                  sourceOrigin: nextResolvedSource.origin,
                  resolvedSource: nextResolvedSource,
                  error: null,
                  isGenerating: true,
                  activeResultTab: "history",
                  pendingOutputs: null,
                  pendingGeneratedAt: "",
                },
              };
            });
            const reboundNodes = bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: safeEdges, traceReason });
            const reboundNodesById = new Map(reboundNodes.map((nodeItem) => [nodeItem.id, nodeItem]));
            const plannerBrainNodeId = safeEdges
              .filter((edgeItem) => edgeItem.source === narrativeNodeId && String(edgeItem.sourceHandle || "") === "brain_package_out")
              .map((edgeItem) => reboundNodesById.get(edgeItem.target))
              .find((targetNode) => targetNode?.type === "comfyBrain" && String(targetNode?.id || "").trim())?.id || "";
            return { reboundNodes, plannerBrainNodeId, requestPayload };
          };
          const confirmNarrativeOutputs = ({ narrativeNodeId, nodesNow, edgesNow, traceReason = "narrative:confirm" }) => {
            const safeNodes = Array.isArray(nodesNow) ? nodesNow : [];
            const safeEdges = Array.isArray(edgesNow) ? edgesNow : [];
            const confirmedAt = new Date().toISOString();
            const nextNodes = safeNodes.map((x) => {
              if (x.id !== narrativeNodeId) return x;
              const pendingOutputs = x?.data?.pendingOutputs || null;
              if (!pendingOutputs) return x;
              return {
                ...x,
                data: {
                  ...x.data,
                  outputs: pendingOutputs,
                  pendingOutputs: null,
                  confirmedAt,
                  error: null,
                },
              };
            });
            const reboundNodes = bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: safeEdges, traceReason });
            const reboundNodesById = new Map(reboundNodes.map((nodeItem) => [nodeItem.id, nodeItem]));
            const plannerBrainNodeId = safeEdges
              .filter((edgeItem) => edgeItem.source === narrativeNodeId && String(edgeItem.sourceHandle || "") === "brain_package_out")
              .map((edgeItem) => reboundNodesById.get(edgeItem.target))
              .find((targetNode) => targetNode?.type === "comfyBrain" && String(targetNode?.id || "").trim())?.id || "";
            return { reboundNodes, plannerBrainNodeId };
          };
          return {
            ...base,
            data: {
              ...getDefaultNarrativeNodeData(),
              ...base.data,
              connectedInputs,
              connectedContextFingerprint,
              scenarioContextStale: shouldInvalidateScenario,
              scenarioContextStaleReason: shouldInvalidateScenario
                ? (character2Disconnected ? "character_2_disconnected" : "connected_role_context_changed")
                : "",
              ...(shouldInvalidateScenario ? {
                outputs: {
                  storyboardOut: null,
                  scenario: "",
                  voiceScript: "",
                  brainPackage: null,
                  bgMusicPrompt: "",
                  directorOutput: null,
                },
                pendingOutputs: null,
                pendingRawResponse: null,
                pendingStoryboardOut: null,
                pendingDirectorOutput: null,
                pendingGeneratedAt: "",
                pendingScenarioRevision: "",
                scenarioRevision: "",
              } : {}),
              sourceOrigin: resolvedSource.origin,
              resolvedSource,
              onFieldChange: (nodeId, patch) => {
                setNodes((prev) => {
                  const nextNodes = prev.map((x) => {
                    if (x.id !== nodeId) return x;
                    const nextPatch = { ...(patch || {}) };
                    const narrativeConnectedInputs = getNarrativeConnectedInputsSnapshot({
                      node: x,
                      nodesById: new Map(prev.map((nodeItem) => [nodeItem.id, nodeItem])),
                      edges: edgesRef.current || [],
                    });
                    const nextResolvedSource = resolveNarrativeSource({
                      ...(x.data || {}),
                      connectedInputs: narrativeConnectedInputs,
                    });
                    return { ...x, data: { ...x.data, ...nextPatch } };
                  });
                  return bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: "narrative:field-change" });
                });
              },
              onGenerate: (nodeId) => {
                setNodes((prev) => buildNarrativeGenerationState({
                  narrativeNodeId: nodeId,
                  nodesNow: prev,
                  edgesNow: edgesRef.current || [],
                  traceReason: "narrative:generate",
                }).reboundNodes);
              },
              onGenerateScenario: async (nodeId) => {
                const currentEdges = edgesRef.current || [];
                const currentNodes = nodesRef.current || [];
                const scenarioStoryboardTargetIds = currentEdges
                  .filter((edgeItem) => (
                    edgeItem.source === nodeId
                    && String(edgeItem.sourceHandle || "") === "storyboard_out"
                    && String(edgeItem.targetHandle || "") === "scenario_storyboard_in"
                  ))
                  .map((edgeItem) => String(edgeItem.target || "").trim())
                  .filter(Boolean);
                const activeNode = currentNodes.find((nodeItem) => nodeItem.id === nodeId);
                const requestKey = `scenario-generate:${String(nodeId || "")}`;
                const wasInFlight = narrativeGenerateInFlightRef.current.get(requestKey) === true;
                const activeNodeIsGenerating = activeNode?.data?.isGenerating === true;
                const liveController = narrativeAbortControllersRef.current.get(nodeId);
                const hasLiveController = !!liveController && liveController?.signal?.aborted !== true;
                const staleGeneratingState = activeNodeIsGenerating && !wasInFlight && !hasLiveController;
                let staleRecovered = false;
                const syncNarrativeGenerateState = ({
                  finalIsGenerating = false,
                  reason = "",
                  staleRecoveredFlag = false,
                } = {}) => {
                  setNodes((prev) => {
                    const nextNodes = prev.map((x) => {
                      if (x.id !== nodeId) return x;
                      const nextStatus = String(x?.data?.status || "").trim().toLowerCase();
                      const shouldResetStatus = finalIsGenerating === false && (nextStatus === "generating" || nextStatus === "pending" || nextStatus === "loading");
                      return {
                        ...x,
                        data: {
                          ...x.data,
                          isGenerating: finalIsGenerating,
                          ...(shouldResetStatus ? { status: "idle" } : {}),
                        },
                      };
                    });
                    const rebound = bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: reason || "narrative:scenario-generate:state-sync" });
                    nodesRef.current = rebound;
                    return rebound;
                  });
                  console.debug("[SCENARIO GENERATE STATE]", {
                    nodeId: String(nodeId || ""),
                    requestKey,
                    wasInFlightRef: wasInFlight,
                    activeNodeIsGenerating,
                    staleRecovered: staleRecoveredFlag,
                    finalIsGenerating,
                  });
                };
                if (staleGeneratingState) {
                  staleRecovered = true;
                  syncNarrativeGenerateState({
                    finalIsGenerating: false,
                    reason: "narrative:scenario-generate:stale-recovery",
                    staleRecoveredFlag: true,
                  });
                  console.debug("[SCENARIO GENERATE STALE RESET]", {
                    nodeId: String(nodeId || ""),
                    requestKey,
                    wasInFlightRef: wasInFlight,
                    activeNodeIsGenerating,
                    staleRecovered: true,
                    finalIsGenerating: false,
                  });
                }
                if (wasInFlight) {
                  console.debug("[SCENARIO GENERATE REQUEST]", {
                    requestKey,
                    inFlight: true,
                    retryAllowed: false,
                    attempt: 0,
                    httpStatus: null,
                    duplicateBlocked: true,
                  });
                  return;
                }
                if (activeNodeIsGenerating && !staleGeneratingState) {
                  console.debug("[SCENARIO GENERATE REQUEST]", {
                    requestKey,
                    inFlight: true,
                    retryAllowed: false,
                    attempt: 0,
                    httpStatus: null,
                    duplicateBlocked: true,
                  });
                  return;
                }
                narrativeGenerateInFlightRef.current.set(requestKey, true);
                console.debug("[SCENARIO GENERATE REQUEST]", {
                  requestKey,
                  inFlight: false,
                  retryAllowed: false,
                  attempt: 1,
                  httpStatus: null,
                  duplicateBlocked: false,
                });

                narrativeAbortControllersRef.current.get(nodeId)?.abort();
                const controller = new AbortController();
                narrativeAbortControllersRef.current.set(nodeId, controller);

                let preRequestNodes = currentNodes;
                if (scenarioStoryboardTargetIds.length > 0) {
                  const targetIdSet = new Set(scenarioStoryboardTargetIds);
                  const resetAppliedNodes = currentNodes.map((nodeItem) => {
                    if (!targetIdSet.has(String(nodeItem?.id || "")) || nodeItem?.type !== "scenarioStoryboard") return nodeItem;
                    const scenesBeforeReset = Array.isArray(nodeItem?.data?.scenes) ? nodeItem.data.scenes.length : 0;
                    console.debug("[SCENARIO RESET]", {
                      nodeId: String(nodeItem?.id || ""),
                      scenesBeforeReset,
                      resetBeforeRequest: true,
                    });
                    return {
                      ...nodeItem,
                      data: {
                        ...nodeItem.data,
                        scenes: [],
                        sceneGeneration: {},
                        storyboardOut: null,
                        directorOutput: null,
                        scenarioPackage: null,
                        rawScenarioResponse: null,
                        pendingOutputs: null,
                        pendingRawResponse: null,
                        pendingStoryboardOut: null,
                        pendingDirectorOutput: null,
                        pendingGeneratedAt: "",
                        pendingScenarioRevision: "",
                        activeSceneId: "",
                        selectedSceneId: "",
                        scenesCount: 0,
                        status: "generating",
                        scenarioResetInFlight: true,
                        audioData: {
                          ...(nodeItem?.data?.audioData && typeof nodeItem.data.audioData === "object" ? nodeItem.data.audioData : {}),
                          phrases: [],
                          musicStatus: "loading",
                          musicUrl: "",
                          musicError: "",
                          musicTaskId: "",
                          musicPreviewUrl: "",
                          musicFileName: "",
                          musicSource: "",
                        },
                      },
                    };
                  });
                  preRequestNodes = bindHandlers(resetAppliedNodes, { nodesNow: resetAppliedNodes, edgesNow: currentEdges, traceReason: "scenario:reset-before-request" });
                  nodesRef.current = preRequestNodes;
                  setNodes(preRequestNodes);
                }

                const { reboundNodes, requestPayload } = buildNarrativeGenerationState({
                  narrativeNodeId: nodeId,
                  nodesNow: preRequestNodes,
                  edgesNow: currentEdges,
                  traceReason: "narrative:generate:pending-confirm",
                });
                nodesRef.current = reboundNodes;
                setNodes(reboundNodes);

                if (!requestPayload) {
                  narrativeAbortControllersRef.current.delete(nodeId);
                  syncNarrativeGenerateState({
                    finalIsGenerating: false,
                    reason: "narrative:scenario-generate:missing-request-payload",
                    staleRecoveredFlag: staleRecovered,
                  });
                  notify({ type: "warning", title: "Source required", message: "Подключите один active source-of-truth перед генерацией сценария." });
                  return;
                }
                if (CLIP_TRACE_SCENARIO_FORMAT) {
                  console.debug("[SCENARIO FORMAT] director request", {
                    nodeId,
                    format: requestPayload?.director_controls?.format || "",
                  });
                }

                try {
                  const response = await new Promise((resolve, reject) => {
                    const timeoutId = window.setTimeout(() => {
                      controller.abort();
                      reject(buildScenarioDirectorTimeoutError());
                    }, SCENARIO_DIRECTOR_TIMEOUT_MS);
                    controller.signal.addEventListener("abort", () => {
                      window.clearTimeout(timeoutId);
                    }, { once: true });

                    Promise.resolve(fetchJson('/api/clip/comfy/scenario-director/generate', {
                      method: 'POST',
                      body: requestPayload,
                      signal: controller.signal,
                    }))
                      .then((result) => {
                        window.clearTimeout(timeoutId);
                        console.debug("[SCENARIO GENERATE REQUEST]", {
                          requestKey,
                          inFlight: true,
                          retryAllowed: false,
                          attempt: 1,
                          httpStatus: 200,
                          duplicateBlocked: false,
                        });
                        resolve(result);
                      })
                      .catch((requestError) => {
                        window.clearTimeout(timeoutId);
                        const httpStatus = Number(requestError?.status || requestError?.httpStatus || requestError?.response?.status || 0) || null;
                        console.debug("[SCENARIO GENERATE REQUEST]", {
                          requestKey,
                          inFlight: true,
                          retryAllowed: false,
                          attempt: 1,
                          httpStatus,
                          duplicateBlocked: false,
                        });
                        reject(requestError);
                      });
                  });

                  const refreshedNode = (nodesRef.current || []).find((nodeItem) => nodeItem.id === nodeId);
                  const normalizedOutputs = normalizeScenarioDirectorApiResponse(response, refreshedNode?.data || {});
                  if (!normalizedOutputs?.storyboardOut || !normalizedOutputs?.directorOutput) {
                    throw new Error('Scenario Director backend returned an incomplete contract.');
                  }
                  const nextRevision = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
                  setNodes((prev) => {
                    const nextNodes = prev.map((x) => {
                      if (x.id !== nodeId) return x;
                      return {
                        ...x,
                        data: {
                          ...x.data,
                          error: null,
                          isGenerating: false,
                          activeResultTab: 'history',
                          pendingOutputs: normalizedOutputs,
                          pendingRawResponse: response && typeof response === "object" ? response : null,
                          pendingStoryboardOut: normalizedOutputs?.storyboardOut || null,
                          pendingDirectorOutput: normalizedOutputs?.directorOutput || null,
                          pendingGeneratedAt: new Date().toISOString(),
                          pendingScenarioRevision: nextRevision,
                        },
                      };
                    });
                    const rebound = bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: 'narrative:generate:backend-success' });
                    nodesRef.current = rebound;
                    return rebound;
                  });
                } catch (error) {
                  const aborted = isAbortLikeError(error);
                  const message = String(error?.message || 'Scenario Director request failed').trim();
                  setNodes((prev) => {
                    const nextNodes = prev.map((x) => {
                      if (x.id !== nodeId) return x;
                      return {
                        ...x,
                        data: {
                          ...x.data,
                          error: aborted ? null : message,
                          isGenerating: false,
                        },
                      };
                    });
                    const rebound = bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: 'narrative:generate:backend-error' });
                    nodesRef.current = rebound;
                    return rebound;
                  });
                  if (!aborted) {
                    notify({ type: 'error', title: 'Scenario Director error', message });
                  }
                } finally {
                  if (narrativeAbortControllersRef.current.get(nodeId) === controller) {
                    narrativeAbortControllersRef.current.delete(nodeId);
                  }
                  narrativeGenerateInFlightRef.current.delete(requestKey);
                  const latestNode = (nodesRef.current || []).find((nodeItem) => nodeItem.id === nodeId);
                  const finalIsGenerating = latestNode?.data?.isGenerating === true;
                  console.debug("[SCENARIO GENERATE STATE]", {
                    nodeId: String(nodeId || ""),
                    requestKey,
                    wasInFlightRef: wasInFlight,
                    activeNodeIsGenerating,
                    staleRecovered,
                    finalIsGenerating,
                  });
                }
              },
              onConfirmScenario: async (nodeId) => {
                const currentEdges = edgesRef.current || [];
                const currentNodes = nodesRef.current || [];
                const { reboundNodes } = confirmNarrativeOutputs({
                  narrativeNodeId: nodeId,
                  nodesNow: currentNodes,
                  edgesNow: currentEdges,
                  traceReason: "narrative:confirm",
                });
                nodesRef.current = reboundNodes;
                setNodes(reboundNodes);
              },
              onSendToStoryboard: async (nodeId) => {
                const currentEdges = edgesRef.current || [];
                const currentNodes = nodesRef.current || [];
                const { reboundNodes, plannerBrainNodeId } = confirmNarrativeOutputs({
                  narrativeNodeId: nodeId,
                  nodesNow: currentNodes,
                  edgesNow: currentEdges,
                  traceReason: "narrative:confirm-and-storyboard",
                });
                nodesRef.current = reboundNodes;
                setNodes(reboundNodes);
                const hasStoryboardConsumer = currentEdges.some((edgeItem) =>
                  edgeItem.source === nodeId
                  && String(edgeItem.sourceHandle || "") === "storyboard_out"
                  && reboundNodes.some((nodeItem) => nodeItem.id === edgeItem.target && (nodeItem.type === "storyboardNode" || nodeItem.type === "scenarioStoryboard"))
                );
                if (hasStoryboardConsumer) {
                  notify({ type: "success", title: "Storyboard ready", message: "storyboard_out подтверждён и уже передан в Storyboard / Scenario Storyboard node." });
                  return;
                }
                if (!plannerBrainNodeId) {
                  notify({ type: "warning", title: "Connect Storyboard", message: "Результат подтверждён, но storyboard_out пока не подключён к Storyboard node." });
                  return;
                }
                const plannerNode = reboundNodes.find((nodeItem) => nodeItem.id === plannerBrainNodeId && nodeItem.type === "comfyBrain");
                await plannerNode?.data?.onParse?.(plannerBrainNodeId);
              },
            },
          };
        }

        if (isNarrativeTesterNodeType(n.type)) {
          const testerConfig = getNarrativeTesterConfig(n.type);
          const nodesById = new Map((Array.isArray(effectiveNodes) ? effectiveNodes : []).map((nodeItem) => [nodeItem.id, nodeItem]));
          const incomingEdge = getNarrativeTesterIncomingEdge({
            nodeId: n.id,
            acceptHandle: testerConfig?.acceptHandle || "",
            edges: effectiveEdges,
          });
          const sourceNode = incomingEdge ? (nodesById.get(incomingEdge.source) || null) : null;
          const payload = extractNarrativeTesterPayload({
            testerType: n.type,
            sourceNode,
            sourceHandle: String(incomingEdge?.sourceHandle || ""),
          });
          const hasPayload = payload != null && (!(typeof payload === "string") || !!String(payload || "").trim());
          if (n.type === "brainPackageTesterNode") {
            console.log("[BRAIN TESTER FLOW]", {
              step: "bindHandlers:node.data.payload",
              value: payload,
              type: typeof payload,
              isArray: Array.isArray(payload),
              isObject: !!payload && typeof payload === "object" && !Array.isArray(payload),
              testerNodeId: n.id,
              incomingEdge,
              sourceNodeType: sourceNode?.type || null,
              sourceNodeId: sourceNode?.id || null,
              hasPayload,
              testerType: n.type,
            });
          }
          return {
            ...base,
            data: {
              ...base.data,
              testerType: n.type,
              testerConfig,
              acceptedHandle: testerConfig?.acceptHandle || "",
              isConnected: !!incomingEdge && sourceNode?.type === "comfyNarrative" && String(incomingEdge?.sourceHandle || "") === String(testerConfig?.acceptHandle || ""),
              hasPayload,
              payload,
            },
          };
        }

        if (n.type === "comfyBrain") {
          const buildComfyBrainPresentation = (derived) => {
            const pushUnique = (target, message) => {
              const clean = String(message || '').trim();
              if (clean && !target.includes(clean)) target.push(clean);
            };
            const meaningfulRefRoles = Object.entries(derived.refsByRole).filter(([, refs]) => refs.length > 0).map(([role]) => role);
            const sceneRoleModel = deriveSceneRoles({ refsByRole: derived.refsByRole });
            const castLabels = sceneRoleModel.cast.length ? sceneRoleModel.cast.join(' + ') : 'none connected';

            const anchors = {
              sessionCharacterAnchor: sceneRoleModel.cast.length ? `identity lock: ${sceneRoleModel.cast.join(', ')}` : '',
              sessionLocationAnchor: derived.refsByRole.location.length ? 'location anchor from location refs' : '',
              sessionStyleAnchor: derived.refsByRole.style.length ? 'style anchor from style ref' : `${derived.stylePreset} baseline`,
              worldScaleContext: derived.refsByRole.animal.length ? 'animal_scale' : 'human_world',
              entityScaleAnchors: derived.refsByRole.animal.length ? { character: 1, animal: 1 } : { character: 1 },
              propAnchorLabel: inferPropAnchorLabel(derived.refsByRole),
            };

            const warnings = [];
            const critical = [];
            const refConnectionStates = derived?.refConnectionStates && typeof derived.refConnectionStates === 'object' ? derived.refConnectionStates : {};
            const pendingRefRoles = Object.entries(refConnectionStates)
              .filter(([, meta]) => meta?.connected && String(meta?.status || '') === 'draft')
              .map(([role, meta]) => `${role} — ${String(meta?.warningLabel || 'добавьте реф')}`);
            const erroredRefRoles = Object.entries(refConnectionStates)
              .filter(([, meta]) => meta?.connected && String(meta?.status || '') === 'error')
              .map(([role, meta]) => `${role} — ${String(meta?.error || 'ошибка анализа')}`);
            if (derived.narrativeSource === 'none') pushUnique(critical, 'Нет audio или text для построения storyboard');
            if (pendingRefRoles.length) pushUnique(warnings, `Есть незавершённые рефы: ${pendingRefRoles.join('; ')}`);
            if (erroredRefRoles.length) pushUnique(warnings, `Есть ref-ноды с ошибкой: ${erroredRefRoles.join('; ')}`);
            if (!canGenerateComfyImage({ refsByRole: derived.refsByRole }) && (derived.meaningfulText || derived.meaningfulAudio)) {
              pushUnique(warnings, 'Визуальные сцены будут синтезированы из текста, аудио и режима');
            }
            if (!!derived.meaningfulAudio && !derived.meaningfulText && meaningfulRefRoles.length === 0) pushUnique(warnings, 'Сюжет будет выведен из аудио');
            if (!derived.meaningfulAudio && !derived.meaningfulText && meaningfulRefRoles.length > 0) pushUnique(warnings, 'Референсы ограничивают визуальный мир, но без audio/text storyboard не будет собран.');
            if (!!derived.meaningfulAudio && !derived.meaningfulText && meaningfulRefRoles.length > 0) pushUnique(warnings, 'Сюжет будет выведен из аудио и референсов');
            if (!derived.meaningfulAudio && derived.meaningfulText) pushUnique(warnings, 'Таймфреймы будут построены логически, без музыкального ритма');
            if (derived.storyControlMode === 'text_override' && derived.meaningfulAudio) pushUnique(warnings, 'TEXT задаёт сюжет; AUDIO используется для ритма/эмоционального контура');
            if (derived.storyControlMode === 'audio_enhanced_by_text') pushUnique(warnings, 'AUDIO даёт story backbone; TEXT усиливает драму и акценты');
            if (derived.storyControlMode === 'hybrid_balanced') pushUnique(warnings, 'Сюжет формируется совместно из AUDIO и TEXT');
            if (derived.audioStoryMode === 'lyrics_music' && derived.meaningfulAudio) pushUnique(warnings, 'Audio story mode: lyrics+music (lyrics и музыка вместе управляют сюжетом).');
            if (derived.audioStoryMode === 'music_only' && derived.meaningfulAudio) pushUnique(warnings, 'Audio story mode: music_only (lyrics игнорируются; сюжет строится по ритму/энергии/refs/mode).');
            if (derived.audioStoryMode === 'music_plus_text' && derived.meaningfulAudio) pushUnique(warnings, 'Audio story mode: music_plus_text (lyrics игнорируются; TEXT задаёт narrative direction).');
            if (derived.audioStoryMode === 'speech_narrative' && derived.meaningfulAudio) pushUnique(warnings, 'Audio story mode: speech_narrative (spoken audio meaning is primary; transcript / spoken hints / semantic summary must drive the storyboard scene by scene).');
            if (derived.weakSemanticContext) pushUnique(warnings, 'Weak semantic context: добавьте transcript, spoken hints или text support.');
            if (Array.isArray(derived.audioStoryPolicy?.warnings)) derived.audioStoryPolicy.warnings.forEach((msg) => pushUnique(warnings, msg));
            if (derived.modeValue === 'scenario' && !derived.meaningfulText) pushUnique(warnings, 'Scenario mode без TEXT: добавьте текст для beat-by-beat дисциплины.');
            if (derived.outputValue === 'comfy text' && !String(derived.meaningfulText || '').trim()) pushUnique(warnings, 'Для comfy text желательно добавить richer text prompt');
            if (derived.modeValue === 'reklama' && !derived.meaningfulText) pushUnique(warnings, 'Для reklama желательно добавить рекламный тезис в TEXT');

            const roleCoverage = {
              castRoles: sceneRoleModel.cast,
              worldAnchors: ['location', 'style', 'props'].filter((role) => derived.refsByRole[role]?.length > 0),
              roleCount: meaningfulRefRoles.length,
            };

            const referenceSummary = {
              byRole: Object.fromEntries(Object.entries(derived.refsByRole).map(([role, refs]) => [role, refs.length])),
              text: meaningfulRefRoles.length ? `refs by role: ${meaningfulRefRoles.join(', ')}` : 'refs by role: none',
            };

            const plannerInput = {
              mode: derived.modeValue,
              plannerMode: String(derived.plannerMode || "legacy"),
              output: derived.outputValue,
              stylePreset: derived.stylePreset,
              genre: derived.genreValue,
              freezeStyle: derived.freezeStyle,
              meaningfulText: derived.meaningfulText,
              meaningfulAudio: derived.meaningfulAudio,
              audioDurationSec: derived.meaningfulAudioDurationSec,
              refsByRole: derived.refsByRole,
              narrativeSource: derived.narrativeSource,
              storySource: derived.storySource,
              timelineSource: derived.timelineSource,
              storyControlMode: derived.storyControlMode,
              storyMissionSummary: derived.storyMissionSummary,
              audioStoryMode: derived.audioStoryMode,
              lyricsText: derived.lyricsText,
              transcriptText: derived.transcriptText,
              spokenTextHint: derived.spokenTextHint,
              audioSemanticHints: derived.audioSemanticHints,
              audioSemanticSummary: derived.audioSemanticSummary,
              weakSemanticContext: derived.weakSemanticContext,
              semanticContextReason: derived.semanticContextReason,
              audioStoryPolicy: derived.audioStoryPolicy,
              textInfluence: derived.textInfluence,
              textNarrativeRole: derived.narrativeRoles.textNarrativeRole,
              audioNarrativeRole: derived.narrativeRoles.audioNarrativeRole,
              modeIntent: derived.modeSemantics.modeIntent,
              modePromptBias: derived.modeSemantics.modePromptBias,
              modeSceneStrategy: derived.modeSemantics.modeSceneStrategy,
              modeContinuityBias: derived.modeSemantics.modeContinuityBias,
              planningMindset: derived.modeSemantics.planningMindset,
              modeRules: derived.modeSemantics.modeRules,
              styleSummary: derived.styleSemantics.styleSummary,
              styleContinuity: derived.styleSemantics.styleContinuity,
              styleRules: derived.styleSemantics.styleRules,
              primaryImageAnchor: Object.values(derived.refsByRole).flat().find((item) => item?.url) || null,
              warnings: [...critical, ...warnings],
              coverage: {
                hasText: !!derived.meaningfulText,
                hasAudio: !!derived.meaningfulAudio,
                hasRefs: !!meaningfulRefRoles.length,
                hasVisualAnchors: canGenerateComfyImage({ refsByRole: derived.refsByRole }),
                roleCoverage,
              },
              anchors,
            };

            const brainSummary = {
              plannerMode: derived.plannerMode || 'legacy',
              storySource: derived.storySource || derived.narrativeSource,
              cast: castLabels,
              world: `location ${derived.refsByRole.location.length ? 'yes' : 'no'} • props ${derived.refsByRole.props.length ? 'yes' : 'no'} • scale ${anchors.worldScaleContext}`,
              style: `${derived.stylePreset}${derived.refsByRole.style.length ? ' + style ref' : ' only'} • ${derived.styleSemantics.styleSummary}`,
              genre: derived.genreValue || '—',
              worldCompact: `${derived.refsByRole.location.length ? 'location' : ''}${derived.refsByRole.location.length && derived.refsByRole.props.length ? ' + ' : ''}${derived.refsByRole.props.length ? 'props' : ''}` || 'none',
              styleCompact: `${derived.stylePreset}${derived.refsByRole.style.length ? ' + ref' : ''}`,
              sourceArbitration: `${derived.narrativeSource} • ${derived.storyControlMode}`,
              storyMissionSummary: derived.storyMissionSummary,
              audioStoryMode: derived.audioStoryMode,
              textInfluence: derived.textInfluence,
              outputMode: derived.outputValue,
              modeBias: derived.modeSemantics.modePromptBias,
              planningStyle: derived.modeSemantics.planningMindset,
              sceneStrategy: derived.modeSemantics.modeSceneStrategy,
              styleBias: derived.styleSemantics.styleSummary,
              pipelineFlow: 'brain → per-scene prompts/rules → scene image → scene video',
            };

            return {
              meaningfulRefRoles,
              sceneRoleModel,
              referenceSummary,
              warnings,
              critical,
              plannerInput,
              brainSummary,
            };
          };

          const derived = deriveComfyBrainState({
            nodeId: n.id,
            nodeData: base.data,
            nodesNow: effectiveNodes,
            edgesNow: effectiveEdges,
            normalizeRefDataFn: normalizeRefData,
          });
          const presentation = buildComfyBrainPresentation(derived);
          const roleOrder = ["character_1", "character_2", "character_3", "animal", "group", "props", "location", "style"];
          const refConnectionStates = derived?.refConnectionStates && typeof derived.refConnectionStates === "object" ? derived.refConnectionStates : {};
          const connectedRefsSummary = roleOrder
            .filter((role) => refConnectionStates?.[role]?.connected)
            .map((role) => {
              const meta = refConnectionStates?.[role] || {};
              if (meta.status === "ready") return { role, label: String(meta.shortLabel || "персонаж") };
              return { role, label: String(meta.warningLabel || "добавьте реф"), status: meta.status };
            });
          const connectedRefsWarnings = roleOrder
            .filter((role) => refConnectionStates?.[role]?.connected && ["draft", "error", "loading"].includes(String(refConnectionStates?.[role]?.status || "")))
            .map((role) => ({
              role,
              status: refConnectionStates?.[role]?.status || "draft",
              message: refConnectionStates?.[role]?.status === "error"
                ? (refConnectionStates?.[role]?.error || "ошибка анализа")
                : (refConnectionStates?.[role]?.warningLabel || "добавьте реф"),
            }));
          const narrativeBrainPackageEdge = [...effectiveEdges]
            .reverse()
            .find((edge) => edge?.target === n.id && String(edge?.targetHandle || "") === "brain_package") || null;
          const narrativeBrainSourceNode = narrativeBrainPackageEdge ? (effectiveNodes.find((nodeItem) => nodeItem.id === narrativeBrainPackageEdge.source) || null) : null;
          const narrativeBrainPackage = extractNarrativeBrainPackageForComfyBrain({
            sourceNode: narrativeBrainSourceNode,
            sourceHandle: String(narrativeBrainPackageEdge?.sourceHandle || ""),
          });

          const hiddenReferenceProfiles = Object.fromEntries(
            roleOrder
              .filter((role) => refConnectionStates?.[role]?.status === "ready" && refConnectionStates?.[role]?.hiddenProfile)
              .map((role) => [role, refConnectionStates[role].hiddenProfile])
          );

          if (CLIP_TRACE_COMFY_REFS) {
            console.info("[CLIP TRACE COMFY REFS] derived connected refs", {
              nodeId: n.id,
              connectedRefsSummary,
              connectedRefsWarnings,
              statuses: Object.fromEntries(roleOrder.map((role) => [role, {
                connected: !!refConnectionStates?.[role]?.connected,
                rawRefStatus: String(refConnectionStates?.[role]?.rawRefStatus || ""),
                status: String(refConnectionStates?.[role]?.status || ""),
                refsCount: Array.isArray(refConnectionStates?.[role]?.refs) ? refConnectionStates[role].refs.length : 0,
                shortLabel: String(refConnectionStates?.[role]?.shortLabel || ""),
                hasHiddenProfile: !!refConnectionStates?.[role]?.hasHiddenProfile,
                hasAnalyzedAt: !!refConnectionStates?.[role]?.hasAnalyzedAt,
                warningLabel: String(refConnectionStates?.[role]?.warningLabel || ""),
              }])),
            });
          }
          if (CLIP_TRACE_BRAIN_REFRESH) {
            console.info("[CLIP TRACE BRAIN REFRESH] comfyBrain derive", {
              nodeId: n.id,
              reason: traceReason,
              connectedRefsSummary,
              connectedRefsWarnings,
              refConnectionStates: {
                character_2: refConnectionStates?.character_2 || null,
                character_3: refConnectionStates?.character_3 || null,
              },
            });
          }

          return {
            ...base,
            data: {
              ...base.data,
              output: derived.outputValue,
              format: resolvePreferredSceneFormat(base.data?.format),
              connectedRefsCount: presentation.meaningfulRefRoles.length,
              connectedCastCount: presentation.sceneRoleModel.cast.length,
              hasAudio: !!derived.meaningfulAudio,
              hasText: !!derived.meaningfulText,
              brainSummary: presentation.brainSummary,
              brainWarnings: presentation.warnings,
              brainCritical: presentation.critical,
              plannerInput: presentation.plannerInput,
              sceneRoleModel: presentation.sceneRoleModel,
              referenceSummary: presentation.referenceSummary,
              refsConnectStatus: "ready",
              connectedRefsSummary,
              connectedRefsWarnings,
              hiddenReferenceProfiles,
              narrativeBrainPackageConnected: !!narrativeBrainPackageEdge && narrativeBrainSourceNode?.type === "comfyNarrative" && String(narrativeBrainPackageEdge?.sourceHandle || "") === "brain_package_out",
              narrativeBrainPackageSourceNodeId: narrativeBrainSourceNode?.id || "",
              narrativeBrainPackage,
              plannerMeta: {
                ...(base.data?.plannerMeta || {}),
                format: resolvePreferredSceneFormat(base.data?.plannerMeta?.format, base.data?.format),
                connectedRefsSummary,
                connectedRefsWarnings,
                referenceProfiles: hiddenReferenceProfiles,
              },
              onField: (nodeId, key, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, [key]: value } } : x))),
              onPlannerMode: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, plannerMode: value === "gemini_only" ? "gemini_only" : "legacy" } } : x))),
              onMode: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, mode: value } } : x))),
              onOutput: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, output: normalizeRenderProfile(value) } } : x))),
              onGenre: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, genre: normalizeComfyGenre(value) } } : x))),
              onFormat: (nodeId, value) => setNodes((prev) => prev.map((x) => {
                if (x.id === nodeId) {
                  return { ...x, data: { ...x.data, format: normalizeSceneImageFormat(value) } };
                }
                return x;
              })),
              onAudioStoryMode: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, audioStoryMode: normalizeAudioStoryMode(value) } } : x))),
              onStyle: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, styleKey: value } } : x))),
              onFreezeStyle: (nodeId, checked) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, freezeStyle: !!checked } } : x))),
              onParse: async (nodeId) => {
                if (comfyParseInFlightRef.current.has(nodeId)) {
                  console.info("[COMFY PARSE] skip duplicate run node=%s", nodeId);
                  return;
                }
                const parseId = comfyParseSeqRef.current + 1;
                comfyParseSeqRef.current = parseId;
                comfyParseInFlightRef.current.add(nodeId);
                try {
                  clearClipStoryboardStorageForCurrentAccount("comfy_parse_start");
                  const startedAt = new Date().toISOString();
                  const activeNode = (nodesRef.current || []).find((nodeItem) => nodeItem.id === nodeId);
                  const preDeriveSnapshot = collectComfyRefDerivationSnapshot({
                    nodeId,
                    nodesNow: nodesRef.current || [],
                    edgesNow: edgesRef.current || [],
                  });
                  console.log("[COMFY DEBUG FRONT] pre-derive snapshot", preDeriveSnapshot);
                  const freshDerived = deriveComfyBrainState({
                    nodeId,
                    nodeData: activeNode?.data || base.data,
                    nodesNow: nodesRef.current || [],
                    edgesNow: edgesRef.current || [],
                    normalizeRefDataFn: normalizeRefData,
                  });
                  const postDeriveSummary = summarizeRefsByRole(freshDerived?.refsByRole);
                  const droppedHandles = Object.entries(preDeriveSnapshot?.connectedSources || {})
                    .filter(([handleId, sourceMeta]) => sourceMeta?.connected && (postDeriveSummary?.[String(handleId || "").replace("ref_", "")] || 0) === 0)
                    .map(([handleId]) => handleId);
                  console.log("[COMFY DEBUG FRONT] post-derive refs summary", postDeriveSummary);
                  if (droppedHandles.length) {
                    console.warn("[COMFY DEBUG FRONT] derive dropped connected refs", {
                      nodeId,
                      droppedHandles,
                      connectedSources: preDeriveSnapshot?.connectedSources,
                    });
                  }
                  const freshPresentation = buildComfyBrainPresentation(freshDerived);

                  if (freshDerived.modeValue === "clip" && !freshDerived.meaningfulAudio) {
                    notify({ type: "warning", title: "AUDIO required", message: "Для режима CLIP сначала подключите AUDIO." });
                    return;
                  }

                console.info("[CLIP PLANNER] build payload start", {
                  nodeId,
                  mode: freshDerived.modeValue,
                  plannerMode: String(freshDerived.plannerMode || "legacy"),
                  hasAudio: !!freshDerived.meaningfulAudio,
                  hasText: !!freshDerived.meaningfulText,
                });

                const plannerMode = String(freshDerived.plannerMode || "").trim().toLowerCase() === "gemini_only" ? "gemini_only" : "legacy";
                const inputMode = freshDerived.meaningfulAudio
                  ? "audio_first"
                  : (freshDerived.meaningfulText ? "text_to_audio_first" : "");
                const rawPayload = {
                  mode: freshDerived.modeValue,
                  plannerMode,
                  directGeminiStoryboardMode: plannerMode === "gemini_only",
                  direct_gemini_storyboard_mode: plannerMode === "gemini_only",
                  inputMode,
                  projectMode: "narration_first",
                  output: freshDerived.outputValue,
                  format: resolvePreferredSceneFormat(activeNode?.data?.format),
                  stylePreset: freshDerived.stylePreset,
                  genre: freshDerived.genreValue,
                  freezeStyle: freshDerived.freezeStyle,
                  text: freshDerived.meaningfulText || "",
                  lyricsText: String(freshDerived.lyricsText || "").trim(),
                  transcriptText: String(freshDerived.transcriptText || "").trim(),
                  spokenTextHint: String(freshDerived.spokenTextHint || "").trim(),
                  audioSemanticHints: freshDerived.audioSemanticHints || "",
                  audioSemanticSummary: String(freshDerived.audioSemanticSummary || "").trim(),
                  audioUrl: freshDerived.meaningfulAudio || "",
                  masterAudioUrl: freshDerived.meaningfulAudio || "",
                  audioDurationSec: freshDerived.meaningfulAudioDurationSec,
                  refsByRole: freshDerived.refsByRole,
                  storyControlMode: freshDerived.storyControlMode,
                  storyMissionSummary: freshDerived.storyMissionSummary,
                  audioStoryMode: freshDerived.audioStoryMode,
                  timelineSource: freshDerived.timelineSource,
                  narrativeSource: freshDerived.narrativeSource,
                };
                const payload = Object.fromEntries(Object.entries(rawPayload).filter(([key, value]) => {
                  if (value === undefined || value === null) return false;
                  if (typeof value === "string") {
                    const trimmed = value.trim();
                    if (!trimmed && !["text", "audioUrl", "masterAudioUrl"].includes(key)) return false;
                    return true;
                  }
                  return true;
                }));
                const hasPlannerPayload = !!payload && typeof payload === "object" && !Array.isArray(payload);
                const hasPlannerSource = !!String(payload?.audioUrl || payload?.masterAudioUrl || payload?.text || payload?.lyricsText || payload?.transcriptText || "").trim();
                const hasValidPlannerMode = String(payload?.plannerMode || "").trim() === "gemini_only";
                const hasValidInputMode = ["audio_first", "text_to_audio_first"].includes(String(payload?.inputMode || "").trim());
                if (!hasPlannerPayload || !hasValidPlannerMode || !hasValidInputMode || !String(payload?.output || "").trim() || !String(payload?.projectMode || "").trim() || !hasPlannerSource) {
                  console.warn("[CLIP PLANNER] payload invalid, request aborted", {
                    nodeId,
                    hasPlannerPayload,
                    hasPlannerSource,
                    hasValidPlannerMode,
                    hasValidInputMode,
                    plannerMode: payload?.plannerMode || "",
                    inputMode: payload?.inputMode || "",
                    projectMode: payload?.projectMode || "",
                    masterAudioUrl: payload?.masterAudioUrl || "",
                    textLength: String(payload?.text || payload?.lyricsText || payload?.transcriptText || "").trim().length,
                  });
                  notify({ type: "warning", title: "Planner payload invalid", message: "Planner payload не отправлен: включите Gemini planner и проверьте AUDIO/TEXT источники." });
                  setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, parseStatus: "error", brainCritical: ["Planner payload invalid"], brainWarnings: [] } } : x)));
                  return;
                }
                console.log("[CLIP PLANNER] inputMode=", payload.inputMode);
                console.log("[CLIP PLANNER] projectMode=", payload.projectMode);
                console.log("[COMFY DEBUG FRONT] derived refsByRole", freshDerived?.refsByRole);
                console.log("[COMFY DEBUG FRONT] derived refs counts", summarizeRefsByRole(freshDerived?.refsByRole));
                console.log("[COMFY DEBUG FRONT] derived refs active roles", summarizeRefsByRole(freshDerived?.refsByRole)?.activeRoles || []);
                console.log("[COMFY DEBUG FRONT] payload refsByRole before /clip/comfy/plan", payload?.refsByRole);
                console.log("[COMFY DEBUG FRONT] payload refs counts before /clip/comfy/plan", summarizeRefsByRole(payload?.refsByRole));
                console.log("[COMFY DEBUG FRONT] payload essentials before /clip/comfy/plan", {
                  text: payload?.text || "",
                  audioUrl: payload?.audioUrl || "",
                  mode: payload?.mode || "",
                  stylePreset: payload?.stylePreset || "",
                });
                console.log(`[COMFY PARSE #${parseId}] payload`, {
                  nodeId,
                  startedAt,
                  ...summarizeComfyPayload(payload),
                });

                const comfyStoryTargets = (edgesRef.current || []).filter((e) => e.source === nodeId && e.sourceHandle === 'comfy_plan').map((e) => e.target);
                const activeStoryboardRequestId = `comfy-parse-${parseId}`;

                console.info("[COMFY STORYBOARD] request start patch", {
                  nodeId,
                  parseId,
                  activeStoryboardRequestId,
                  comfyStoryTargets,
                  hasRealActiveRequest: true,
                });

                setNodes((prev) => prev.map((x) => {
                  if (x.id === nodeId) {
                    return { ...x, data: { ...x.data, parseStatus: 'parsing' } };
                  }
                  if (comfyStoryTargets.includes(x.id) && x.type === 'comfyStoryboard') {
                    return {
                      ...x,
                      data: {
                        ...x.data,
                        parseStatus: 'updating',
                        isUpdating: true,
                        isGenerating: true,
                        isBusy: true,
                        hasActiveRequest: true,
                        activeRequestId: activeStoryboardRequestId,
                        activeRequestSourceNodeId: nodeId,
                        activeRequestStartedAt: startedAt,
                        isStale: true,
                        staleReason: 'updating_with_previous_result',
                        errorMessage: '',
                        lastKnownFreshAt: String(x?.data?.lastSuccessfulParseAt || x?.data?.lastKnownFreshAt || ''),
                      },
                    };
                  }
                  return x;
                }));

                  let response;
                  try {
                    if (USE_COMFY_MOCK) {
                      const plannerMeta = { plannerInput: payload, mode: payload.mode, plannerMode: payload.plannerMode, output: payload.output, format: payload.format, stylePreset: payload.stylePreset, genre: payload.genre, narrativeSource: payload.narrativeSource, timelineSource: payload.timelineSource, storyControlMode: payload.storyControlMode, storyMissionSummary: payload.storyMissionSummary, audioStoryMode: payload.audioStoryMode, warnings: [...freshPresentation.critical, ...freshPresentation.warnings], summary: freshPresentation.brainSummary, sceneRoleModel: freshPresentation.sceneRoleModel, referenceSummary: freshPresentation.referenceSummary };
                      const scenes = buildMockComfyScenes(plannerMeta);
                      response = { ok: true, planMeta: plannerMeta, globalContinuity: scenes[0]?.plannerMeta?.globalContinuity || "", scenes, warnings: plannerMeta.warnings, errors: [], debug: {} };
                    } else {
                      console.info("[CLIP PLANNER] send payload", {
                        nodeId,
                        plannerMode: payload.plannerMode,
                        inputMode: payload.inputMode,
                        projectMode: payload.projectMode,
                        masterAudioUrl: payload.masterAudioUrl,
                        narrativeSource: payload.narrativeSource,
                        timelineSource: payload.timelineSource,
                        audioStoryMode: payload.audioStoryMode,
                      });
                      console.log("[SEND TO BACKEND] payload", payload);
                      response = await fetchJson(`/api/clip/comfy/plan`, { method: "POST", body: payload });
                    }
                    console.log("[COMFY DEBUG FRONT] /clip/comfy/plan response plannerInput refsByRole", response?.planMeta?.plannerInput?.refsByRole);
                    console.log("[COMFY DEBUG FRONT] /clip/comfy/plan response plannerInput refs counts", summarizeRefsByRole(response?.planMeta?.plannerInput?.refsByRole));
                    console.log("[COMFY DEBUG FRONT] /clip/comfy/plan full planMeta", response?.planMeta);
                    console.log("[COMFY DEBUG FRONT] /clip/comfy/plan scenes count", Array.isArray(response?.scenes) ? response.scenes.length : 0);
                    console.log(`[COMFY PARSE #${parseId}] response`, summarizeComfyResponse(response));
                  } catch (err) {
                    console.error(`[COMFY PARSE #${parseId}] error`, err);
                    console.warn("[COMFY STORYBOARD] request failed", {
                      nodeId,
                      parseId,
                      comfyStoryTargets,
                      hasRealActiveRequest: false,
                      error: String(err?.message || err || "unknown_error"),
                    });
                    setNodes((prev) => prev.map((x) => {
                      if (x.id === nodeId) return { ...x, data: { ...x.data, parseStatus: "error", brainCritical: [String(err?.message || err)], brainWarnings: [] } };
                      if (comfyStoryTargets.includes(x.id) && x.type === 'comfyStoryboard') return { ...x, data: { ...x.data, parseStatus: 'error', isUpdating: false, isGenerating: false, isBusy: false, hasActiveRequest: false, activeRequestId: '', activeRequestSourceNodeId: '', activeRequestStartedAt: '', isStale: true, staleReason: 'parse_error_previous_result_retained', errorMessage: String(err?.message || err || 'unknown_error') } };
                      return x;
                    }));
                    return;
                  }

                  if (!response?.ok) {
                    const compactErrorMessage = compactComfyErrorMessage(response);
                    const normalizedSources = normalizeStoryboardSourcesForUi({
                      narrativeSource: response?.planMeta?.narrativeSource || response?.debug?.narrativeSource || freshDerived.narrativeSource,
                      storySource: response?.planMeta?.storySource || response?.debug?.storySource || freshDerived.storySource,
                    });
                    const debugFields = {
                      ...extractComfyDebugFields({ plannerInput: payload, plannerMeta: response?.planMeta || {} }),
                      ...(response?.debug && typeof response.debug === 'object' ? response.debug : {}),
                      ...normalizedSources,
                    };
                    console.warn("[COMFY STORYBOARD] request returned non-ok response", {
                      nodeId,
                      parseId,
                      comfyStoryTargets,
                      hasRealActiveRequest: false,
                      responseOk: !!response?.ok,
                      errors: Array.isArray(response?.errors) ? response.errors : [],
                    });
                    setNodes((prev) => prev.map((x) => {
                      if (x.id === nodeId) return { ...x, data: { ...x.data, parseStatus: "error", brainCritical: [compactErrorMessage], brainWarnings: Array.isArray(response?.warnings) ? response.warnings : [], comfyDebug: debugFields } };
                      if (comfyStoryTargets.includes(x.id) && x.type === 'comfyStoryboard') return { ...x, data: { ...x.data, parseStatus: 'error', isUpdating: false, isGenerating: false, isBusy: false, hasActiveRequest: false, activeRequestId: '', activeRequestSourceNodeId: '', activeRequestStartedAt: '', isStale: true, staleReason: 'parse_error_previous_result_retained', errorMessage: compactErrorMessage, warnings: Array.isArray(response?.warnings) ? response.warnings : (Array.isArray(x?.data?.warnings) ? x.data.warnings : []), narrativeSource: normalizedSources.narrativeSource, plannerMeta: { ...(x?.data?.plannerMeta || {}), ...(response?.planMeta || {}), ...normalizedSources }, debugFields, comfyDebug: debugFields } };
                      return x;
                    }));
                    return;
                  }

                  resetComfyVideoJobsState();
                  const scenes = normalizeSceneCollectionWithSceneId(Array.isArray(response?.scenes) ? response.scenes : [], "comfy_scene");
                  const plannerMeta = response?.planMeta || {};
                  const resolvedPlannerFormat = resolvePreferredSceneFormat(plannerMeta?.format, payload?.format, activeNode?.data?.format);
                  const resetBrainScenes = resetVideoStateBySceneId(scenes, { panelField: "videoPanelOpen" }).map((scene) => ({
                    ...scene,
                    imageFormat: resolvePreferredSceneFormat(scene?.imageFormat, scene?.format, resolvedPlannerFormat),
                    format: resolvePreferredSceneFormat(scene?.format, scene?.imageFormat, resolvedPlannerFormat),
                  }));
                  const comfyStoryboardTargets = comfyStoryTargets.length
                    ? comfyStoryTargets
                    : (nodesRef.current || []).filter((nodeItem) => nodeItem?.type === 'comfyStoryboard').map((nodeItem) => nodeItem.id);
                  const globalContinuity = response?.globalContinuity || "";
                  const normalizedSources = normalizeStoryboardSourcesForUi({
                    narrativeSource: plannerMeta?.narrativeSource || response?.debug?.narrativeSource || freshDerived.narrativeSource,
                    storySource: plannerMeta?.storySource || response?.debug?.storySource || freshDerived.storySource,
                  });
                  const debugFields = {
                    ...extractComfyDebugFields({ plannerInput: payload, plannerMeta: { ...plannerMeta, globalContinuity } }),
                    ...(response?.debug && typeof response.debug === 'object' ? response.debug : {}),
                    ...normalizedSources,
                  };
                  const parsedAt = new Date().toLocaleTimeString();
                  const parsedAtIso = new Date().toISOString();
                  const storyboardScenesWithResetState = resetVideoStateBySceneId(scenes, { panelField: "videoPanelOpen" }).map((scene) => {
                    const sceneFormat = resolvePreferredSceneFormat(scene?.imageFormat, scene?.format, resolvedPlannerFormat);
                    return {
                      ...scene,
                      imageFormat: sceneFormat,
                      format: sceneFormat,
                      videoJobId: String(scene?.videoJobId || ""),
                      videoStatus: String(scene?.videoStatus || ""),
                      videoError: String(scene?.videoError || ""),
                    };
                  });

                  console.log("[COMFY PLAN RESPONSE APPLIED]", {
                    nodeId,
                    scenesLength: scenes.length,
                    comfyStoryTargets,
                    comfyStoryboardTargets,
                  });
                  console.info("[COMFY STORYBOARD] request success patch", {
                    nodeId,
                    parseId,
                    comfyStoryboardTargets,
                    hasRealActiveRequest: false,
                    scenesLength: scenes.length,
                  });

                  setNodes((prev) => {
                    const nextNodes = prev.map((x) => {
                      if (x.id === nodeId) {
                        return {
                          ...x,
                          data: {
                            ...x.data,
                            parseStatus: 'ready',
                            parsedAt,
                            plannerMode: String(plannerMeta?.plannerMode || payload?.plannerMode || freshDerived.plannerMode || "legacy"),
                            format: resolvePreferredSceneFormat(plannerMeta?.format, payload?.format, x?.data?.format),
                            mockScenes: resetBrainScenes.map((scene) => ({ ...scene, videoJobId: String(scene?.videoJobId || ""), videoStatus: String(scene?.videoStatus || ""), videoError: String(scene?.videoError || "") })),
                            lastPlannerMeta: { ...plannerMeta, globalContinuity, debugFields },
                            comfyDebug: debugFields,
                            brainWarnings: Array.isArray(response?.warnings) ? response.warnings : freshPresentation.warnings,
                            brainCritical: Array.isArray(response?.errors) ? response.errors : [],
                          },
                        };
                      }
                      if (comfyStoryboardTargets.includes(x.id) && x.type === 'comfyStoryboard') {
                        return {
                          ...x,
                          data: {
                            ...x.data,
                            mockScenes: storyboardScenesWithResetState,
                            sceneCount: scenes.length,
                            mode: freshDerived.modeValue,
                            plannerMode: String(plannerMeta?.plannerMode || payload?.plannerMode || freshDerived.plannerMode || "legacy"),
                            output: freshDerived.outputValue,
                            format: resolvePreferredSceneFormat(plannerMeta?.format, payload?.format, x?.data?.format),
                            stylePreset: freshDerived.stylePreset,
                            narrativeSource: normalizedSources.narrativeSource,
                            timelineSource: freshDerived.timelineSource,
                            storyControlMode: freshDerived.storyControlMode,
                            storyMissionSummary: freshDerived.storyMissionSummary,
                            audioStoryMode: freshDerived.audioStoryMode,
                            textNarrativeRole: freshDerived.narrativeRoles.textNarrativeRole,
                            audioNarrativeRole: freshDerived.narrativeRoles.audioNarrativeRole,
                            warnings: Array.isArray(response?.warnings) ? response.warnings : [],
                            summary: freshPresentation.brainSummary,
                            refsByRoleSummary: freshPresentation.referenceSummary,
                            plannerMeta: { ...plannerMeta, globalContinuity, ...normalizedSources },
                            debugFields,
                            comfyDebug: debugFields,
                            pipelineFlow: debugFields.pipelineFlow,
                            parseStatus: 'ready',
                            isUpdating: false,
                            isGenerating: false,
                            isBusy: false,
                            hasActiveRequest: false,
                            activeRequestId: '',
                            activeRequestSourceNodeId: '',
                            activeRequestStartedAt: '',
                            isStale: false,
                            staleReason: '',
                            errorMessage: '',
                            lastSuccessfulParseAt: parsedAtIso,
                            lastKnownFreshAt: parsedAtIso,
                          },
                        };
                      }
                      return x;
                    });

                    const appliedStoryboardNode = nextNodes.find((nodeItem) => comfyStoryboardTargets.includes(nodeItem.id) && nodeItem.type === 'comfyStoryboard');
                    const appliedScenesLength = Array.isArray(appliedStoryboardNode?.data?.mockScenes) ? appliedStoryboardNode.data.mockScenes.length : 0;
                    console.log("[COMFY PLAN SETNODES DONE]", {
                      nodeId,
                      scenesLength: scenes.length,
                      appliedScenesLength,
                      comfyStoryTargets,
                      comfyStoryboardTargets,
                    });
                    return nextNodes;
                  });
                } finally {
                  comfyParseInFlightRef.current.delete(nodeId);
                }
              },
            },
          };
        }


        if (n.type === "comfyStoryboard") {
          const comfyStoryboardPatch = buildComfyStoryboardPatch({
            node: base,
            nodesNow: effectiveNodes,
            edgesNow: effectiveEdges,
            reason: `bindHandlers:${traceReason}`,
          });
          return {
            ...base,
            data: {
              ...comfyStoryboardPatch,
              onOpenComfy: (nodeId) => {
                try { window.dispatchEvent(new CustomEvent('ps:clipOpenComfyStoryboard', { detail: { nodeId } })); } catch (e) {}
              },
            },
          };
        }

        if (n.type === "comfyVideoPreview") {
          const nodesById = new Map((Array.isArray(effectiveNodes) ? effectiveNodes : []).map((nodeItem) => [nodeItem.id, nodeItem]));
          const incomingScenarioPreviewEdge = [...effectiveEdges]
            .reverse()
            .find((edge) => edge?.target === n.id && String(edge?.targetHandle || "") === "scenario_preview_in") || null;
          const scenarioSourceNode = incomingScenarioPreviewEdge?.source ? (nodesById.get(incomingScenarioPreviewEdge.source) || null) : null;
          const scenarioStoryboardOut = extractNarrativeStoryboardOut({
            sourceNode: scenarioSourceNode,
            sourceHandle: String(incomingScenarioPreviewEdge?.sourceHandle || ""),
          });
          const scenarioDirectorOutput = scenarioSourceNode?.type === "comfyNarrative"
            ? (scenarioSourceNode?.data?.outputs?.directorOutput || scenarioSourceNode?.data?.pendingOutputs?.directorOutput || null)
            : null;
          const scenarioPreview = buildScenarioPreviewInput({
            storyboardOut: scenarioStoryboardOut,
            directorOutput: scenarioDirectorOutput,
            styleProfile: scenarioSourceNode?.data?.styleProfile || "",
            format: scenarioSourceNode?.data?.format || base?.data?.format || "9:16",
          });
          return {
            ...base,
            data: {
              ...base.data,
              scenarioPreview: incomingScenarioPreviewEdge ? scenarioPreview : null,
              onField: (nodeId, key, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, [key]: value } } : x))),
            },
          };
        }

        if (n.type === "introFrame") {
          const introContext = collectIntroFrameContext({
            nodeId: n.id,
            nodes: effectiveNodes,
            edges: effectiveEdges,
          });
          const userTitleRaw = String(base.data?.userTitleRaw ?? base.data?.title ?? "");
          const derivedTitle = buildIntroFrameAutoTitle({ textValue: introContext.titleText, scenes: introContext.scenes });
          return {
            ...base,
            data: {
              ...base.data,
              scenarioFormat: introContext.scenarioFormat,
              previewFormat: introContext.scenarioFormat || base.data?.previewFormat,
              title: userTitleRaw,
              userTitleRaw,
              derivedTitle,
              contextSummary: introContext.summary,
              contextSceneCount: introContext.sceneCount,
              sourceNodeIds: introContext.sourceNodeIds,
              sourceNodeTypes: introContext.sourceNodeTypes,
              onField: (nodeId, key, value) => setNodes((prev) => prev.map((x) => {
                if (x.id !== nodeId) return x;
                const nextData = { ...x.data };
                if (key === "autoTitle") {
                  nextData.autoTitle = !!value;
                  const freshContext = collectIntroFrameContext({ nodeId, nodes: prev, edges: edgesRef.current || [] });
                  nextData.derivedTitle = buildIntroFrameAutoTitle({ textValue: freshContext.titleText, scenes: freshContext.scenes });
                  nextData.generatedHookTitle = "";
                  nextData.previewTitleUsed = "";
                  if (!value && !String(nextData.userTitleRaw ?? nextData.title ?? "").trim()) {
                    nextData.manualTitle = false;
                  }
                } else if (key === "stylePreset") {
                  nextData.stylePreset = normalizeIntroStylePreset(value);
                } else if (key === "previewFormat") {
                  nextData.previewFormat = normalizeIntroFramePreviewFormat(value);
                } else if (key === "durationSec") {
                  nextData.durationSec = normalizeIntroDurationSec(value);
                } else if (key === "title") {
                  nextData.title = String(value || "");
                  nextData.userTitleRaw = String(value || "");
                  nextData.manualTitle = !!String(value || "").trim();
                  nextData.generatedHookTitle = "";
                  nextData.previewTitleUsed = "";
                  if (nextData.manualTitle) nextData.autoTitle = false;
                } else {
                  nextData[key] = value;
                }
                return { ...x, data: nextData };
              })),
              onPickImage: async (nodeId, file) => {
                if (!file) return;
                try {
                  const dataUrl = await readFileAsDataUrl(file);
                  setNodes((prev) => prev.map((x) => (x.id === nodeId
                    ? {
                      ...x,
                      data: {
                        ...x.data,
                        imageUrl: dataUrl,
                        previewKind: INTRO_FRAME_PREVIEW_KINDS.UPLOADED,
                        status: "ready",
                        generatedAt: new Date().toISOString(),
                        error: "",
                        debug: {},
                      },
                    }
                    : x)));
                } catch (err) {
                  console.error(err);
                }
              },
              onClearImage: (nodeId) => setNodes((prev) => prev.map((x) => (x.id === nodeId
                ? { ...x, data: { ...x.data, imageUrl: "", previewKind: "", status: "idle", generatedAt: "", error: "", debug: {}, generatedHookTitle: "", previewTitleUsed: "" } }
                : x))),
              onGenerate: async (nodeId) => {
                const currentNode = (nodesRef.current || []).find((nodeItem) => nodeItem.id === nodeId);
                const freshContext = collectIntroFrameContext({
                  nodeId,
                  nodes: nodesRef.current || [],
                  edges: edgesRef.current || [],
                });
                const manualTitleRaw = String(currentNode?.data?.userTitleRaw ?? currentNode?.data?.title ?? "");
                const manualTitle = manualTitleRaw.trim();
                const currentDerivedTitle = String(currentNode?.data?.derivedTitle || "").trim()
                  || buildIntroFrameAutoTitle({ textValue: freshContext.titleText, scenes: freshContext.scenes });
                const preserveManualTitle = !!manualTitle && (!!currentNode?.data?.manualTitle || !currentNode?.data?.autoTitle);
                const nextTitle = preserveManualTitle
                  ? manualTitle
                  : (currentNode?.data?.autoTitle
                    ? currentDerivedTitle
                    : manualTitle || freshContext.autoTitle);
                const storyContext = buildIntroFrameStoryContextText(freshContext);
                const payload = {
                  title: nextTitle,
                  manualTitleRaw,
                  autoTitle: !!currentNode?.data?.autoTitle,
                  stylePreset: normalizeIntroStylePreset(currentNode?.data?.stylePreset || "cinematic_dark"),
                  previewFormat: normalizeIntroFramePreviewFormat(currentNode?.data?.scenarioFormat || currentNode?.data?.previewFormat),
                  durationSec: normalizeIntroDurationSec(currentNode?.data?.durationSec),
                  storyContext,
                  titleContext: String(freshContext.titleText || "").trim(),
                  sceneCount: Number(freshContext.sceneCount || 0),
                  sourceNodeTypes: Array.isArray(freshContext.sourceNodeTypes) ? freshContext.sourceNodeTypes : [],
                  connectedRefsByRole: freshContext.connectedRefsByRole || {},
                  roleAwareCastSummary: String(freshContext.roleAwareCastSummary || "").trim(),
                  heroParticipants: Array.isArray(freshContext.heroParticipants) ? freshContext.heroParticipants : [],
                  supportingParticipants: Array.isArray(freshContext.supportingParticipants) ? freshContext.supportingParticipants : [],
                  importantProps: Array.isArray(freshContext.importantProps) ? freshContext.importantProps : [],
                  worldContext: String(freshContext.worldContext || "").trim(),
                  styleContext: String(freshContext.styleContext || "").trim(),
                  introMustAppear: Array.isArray(freshContext.introMustAppear) ? freshContext.introMustAppear : [],
                  introMustNotAppear: Array.isArray(freshContext.introMustNotAppear) ? freshContext.introMustNotAppear : [],
                  connectedGenderLocksByRole: freshContext.connectedGenderLocksByRole || {},
                  connectedSpeciesLocksByRole: freshContext.connectedSpeciesLocksByRole || {},
                  storySummary: String(freshContext.storySummary || "").trim(),
                  previewPrompt: String(freshContext.previewPrompt || "").trim(),
                  world: String(freshContext.world || "").trim(),
                  roles: Array.isArray(freshContext.roles) ? freshContext.roles : [],
                  toneStyleDirection: String(freshContext.toneStyleDirection || "").trim(),
                };
                if (CLIP_TRACE_SCENARIO_FORMAT) {
                  console.debug("[INTRO PREVIEW PAYLOAD]", {
                    sourceNodeType: Array.isArray(freshContext.sourceNodeTypes) ? (freshContext.sourceNodeTypes[0] || "") : "",
                    previewFormat: payload.previewFormat,
                    connectedRefCountsByRole: Object.fromEntries(
                      INTRO_COMFY_REF_ROLES.map((role) => [role, Array.isArray(payload?.connectedRefsByRole?.[role]) ? payload.connectedRefsByRole[role].length : 0])
                    ),
                    heroParticipants: payload.heroParticipants,
                    supportingParticipants: payload.supportingParticipants,
                    introMustAppear: payload.introMustAppear,
                    hasStorySummary: !!payload.storySummary,
                    hasPreviewPrompt: !!payload.previewPrompt,
                    hasWorld: !!payload.world,
                    hasRoles: Array.isArray(payload.roles) && payload.roles.length > 0,
                    hasToneStyleDirection: !!payload.toneStyleDirection,
                  });
                }

                setNodes((prev) => prev.map((x) => (x.id === nodeId
                  ? {
                    ...x,
                    data: {
                      ...x.data,
                      title: manualTitleRaw,
                      userTitleRaw: manualTitleRaw,
                      derivedTitle: currentDerivedTitle,
                      manualTitle: preserveManualTitle,
                      previewTitleUsed: payload.title,
                      contextSummary: freshContext.summary,
                      contextSceneCount: freshContext.sceneCount,
                      sourceNodeIds: freshContext.sourceNodeIds,
                      sourceNodeTypes: freshContext.sourceNodeTypes,
                      status: "generating",
                      error: "",
                    },
                  }
                  : x)));

                try {
                  if (CLIP_TRACE_INTRO_PREVIEW) {
                    console.debug("[INTRO FRAME PAYLOAD] /api/clip/intro/generate", {
                      title: payload.title,
                      previewFormat: payload.previewFormat,
                      stylePreset: payload.stylePreset,
                      connectedRefsByRoleCounts: Object.fromEntries(
                        INTRO_COMFY_REF_ROLES.map((role) => [role, Array.isArray(payload?.connectedRefsByRole?.[role]) ? payload.connectedRefsByRole[role].length : 0])
                      ),
                      heroParticipants: payload.heroParticipants,
                      supportingParticipants: payload.supportingParticipants,
                      importantProps: payload.importantProps,
                      introMustAppear: payload.introMustAppear,
                    });
                  }
                  const out = await fetchJson("/api/clip/intro/generate", {
                    method: "POST",
                    body: payload,
                  });
                  if (!out?.ok || !out?.imageUrl) throw new Error(out?.hint || out?.code || "intro_generation_failed");
                  setNodes((prev) => prev.map((x) => (x.id === nodeId
                    ? {
                      ...x,
                      data: {
                        ...x.data,
                        title: manualTitleRaw,
                        userTitleRaw: manualTitleRaw,
                        derivedTitle: currentDerivedTitle,
                        previewTitleUsed: String(out?.debug?.previewTitleUsed || payload.title || ""),
                        generatedHookTitle: String(out?.title || nextTitle || ""),
                        imageUrl: String(out.imageUrl || ""),
                        previewKind: INTRO_FRAME_PREVIEW_KINDS.BACKEND_GENERATED,
                        status: "ready",
                        generatedAt: String(out.generatedAt || new Date().toISOString()),
                        altTitles: [manualTitle, currentDerivedTitle, String(out?.title || nextTitle || "")].filter(Boolean),
                        error: "",
                        debug: out?.debug && typeof out.debug === "object" ? out.debug : {},
                      },
                    }
                    : x)));
                } catch (err) {
                  console.error(err);
                  const message = String(err?.message || err || "intro_generation_failed");
                  setNodes((prev) => prev.map((x) => (x.id === nodeId
                    ? {
                      ...x,
                      data: {
                        ...x.data,
                        status: x.data?.imageUrl ? "ready" : "idle",
                        error: message,
                      },
                    }
                    : x)));
                  notify({ type: "error", message: `INTRO FRAME: ${message}` });
                }
              },
            },
          };
        }

        if (n.type === "refCharacter2" || n.type === "refCharacter3" || n.type === "refAnimal" || n.type === "refGroup") {
          return {
            ...base,
            data: {
              ...base.data,
              refStatus: deriveRefNodeStatus(base.data || {}),
              onField: (nodeId, key, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, [key]: value } } : x))),
              onOpenLightbox: (url) => openLightbox(resolveAssetUrl(url)),
              onPickImage: async (nodeId, filesRaw) => {
                const files = Array.isArray(filesRaw) ? filesRaw : (filesRaw ? [filesRaw] : []);
                if (!files.length) return;
                const allowTypes = new Set(["image/png", "image/jpeg", "image/jpg", "image/webp"]);
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: true } } : x)));
                try {
                  const targetNode = nodesRef.current.find((x) => x.id === nodeId);
                  const prevRefs = Array.isArray(targetNode?.data?.refs)
                    ? targetNode.data.refs
                      .map((item) => ({
                        url: String(item?.url || "").trim(),
                        name: String(item?.name || "").trim(),
                        type: String(item?.type || "").trim(),
                      }))
                      .filter((item) => !!item.url)
                      .slice(0, 5)
                    : [];
                  const room = Math.max(0, 5 - prevRefs.length);
                  const queue = files.slice(0, room);
                  const uploadedRefs = [];
                  for (const oneFile of queue) {
                    if (!allowTypes.has(String(oneFile?.type || "").toLowerCase())) continue;
                    try {
                      const dataUrl = await readFileAsDataUrl(oneFile);
                      if (!dataUrl) continue;
                      uploadedRefs.push({
                        url: dataUrl,
                        name: oneFile.name || "ref",
                        type: oneFile.type || "image/jpeg",
                        kind: "local",
                      });
                    } catch (err) {
                      console.error(err);
                    }
                  }
                  if (!uploadedRefs.length) return;
                  setNodes((prev) => prev.map((x) => {
                    if (x.id !== nodeId) return x;
                    const oldRefs = Array.isArray(x?.data?.refs)
                      ? x.data.refs
                        .map((item) => ({
                          url: String(item?.url || "").trim(),
                          name: String(item?.name || "").trim(),
                          type: String(item?.type || "").trim(),
                        }))
                        .filter((item) => !!item.url)
                        .slice(0, 5)
                      : [];
                    const refs = oldRefs.concat(uploadedRefs).slice(0, 5);
                    return { ...x, data: { ...x.data, refs, refStatus: refs.length ? "draft" : "empty", refShortLabel: "", refHiddenProfile: null, refAnalysisError: "" } };
                  }));
                } finally {
                  setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: false } } : x)));
                }
              },
              onConfirmAdd: async (nodeId) => {
                const node = (nodesRef.current || []).find((x) => x.id === nodeId);
                const refs = Array.isArray(node?.data?.refs) ? node.data.refs.filter((item) => !!String(item?.url || "").trim()).slice(0, 5) : [];
                const role = resolveRefRoleForNode(node);
                if (!refs.length || !role) return;
                setNodes((prev) => {
                  const nextNodes = prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, refStatus: "loading", refAnalysisError: "" } } : x));
                  return bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: "analyze-ref-node:loading" });
                });
                try {
                  const response = await fetchJson(`/api/clip/comfy/analyze-ref-node`, { method: "POST", body: { role, refs } });
                  const analyzedAt = new Date().toISOString();
                  setNodes((prev) => {
                    const nextNodes = prev.map((x) => {
                    if (x.id !== nodeId) return x;
                    const mergedData = normalizeComfyRefNodeData(x.type, {
                      ...x.data,
                      refs,
                      refStatus: "ready",
                      refShortLabel: String(response?.shortLabel || "").trim(),
                      refHiddenProfile: response?.profile && typeof response.profile === "object" ? response.profile : null,
                      refAnalysisError: "",
                      refAnalyzedAt: analyzedAt,
                    }, x?.data?.kind || "");
                    if (CLIP_TRACE_COMFY_REFS) {
                      console.info("[CLIP TRACE COMFY REFS] analyze-ref-node applied", {
                        nodeId,
                        role,
                        refStatus: mergedData.refStatus,
                        refsCount: Array.isArray(mergedData?.refs) ? mergedData.refs.length : 0,
                        refShortLabel: mergedData.refShortLabel,
                        hasHiddenProfile: !!mergedData.refHiddenProfile,
                        refAnalyzedAt: mergedData.refAnalyzedAt,
                        refAnalysisError: mergedData.refAnalysisError,
                      });
                    }
                    if (CLIP_TRACE_BRAIN_REFRESH) {
                      console.info("[CLIP TRACE BRAIN REFRESH] analyze-ref-node apply", {
                        nodeId,
                        role,
                        refStatus: mergedData.refStatus,
                        refShortLabel: mergedData.refShortLabel,
                        refAnalyzedAt: mergedData.refAnalyzedAt,
                        refsCount: Array.isArray(mergedData?.refs) ? mergedData.refs.length : 0,
                        hasHiddenProfile: !!mergedData.refHiddenProfile,
                      });
                    }
                    return { ...x, data: mergedData };
                  });
                    return bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: "analyze-ref-node:applied" });
                  });
                } catch (err) {
                  console.error(err);
                  setNodes((prev) => {
                    const nextNodes = prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, refStatus: "error", refAnalysisError: String(err?.message || err || "analyze_failed") } } : x));
                    return bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: "analyze-ref-node:error" });
                  });
                }
              },
              onRemoveImage: (nodeId, idx) => setNodes((prev) => prev.map((x) => {
                if (x.id !== nodeId) return x;
                const refs = Array.isArray(x?.data?.refs)
                  ? x.data.refs.filter((_, i) => i !== idx)
                  : [];
                return { ...x, data: { ...x.data, refs, refStatus: refs.length ? "draft" : "empty", refShortLabel: "", refHiddenProfile: null, refAnalysisError: "" } };
              })),
            },
          };
        }

        if (n.type === "assemblyNode") {
          return {
            ...base,
            data: {
              ...base.data,
              ...assemblyNodeDataPatch,
            },
          };
        }
return base;
      });
    },
    [
      setNodes,
      removeNode,
      edges,
      assemblyNodeDataPatch,
      buildComfyStoryboardPatch,
      clearClipStoryboardStorageForCurrentAccount,
      stopScenarioVideoPolling,
      onOpenScenarioStoryboard,
      accountKey,
      STORE_KEY,
    ]
  );

  const bindHandlersRef = useRef(bindHandlers);
  const narrativeSourceRefreshSignatureRef = useRef("");

  const refreshNodeBindingsForEdges = useCallback((nextEdges, traceReason = "edges:sync") => {
    const safeEdges = Array.isArray(nextEdges) ? nextEdges : [];
    edgesRef.current = safeEdges;
    setNodes((prev) => {
      const reboundNodes = bindHandlersRef.current(prev, {
        nodesNow: prev,
        edgesNow: safeEdges,
        traceReason,
      });
      nodesRef.current = reboundNodes;
      return reboundNodes;
    });
  }, [setNodes]);

  const onEdgesChange = useCallback((changes) => {
    setEdges((currentEdges) => {
      const nextEdges = enforceSingleNarrativeSourceEdge(applyEdgeChanges(changes, currentEdges));
      refreshNodeBindingsForEdges(nextEdges, "edges:change");
      return nextEdges;
    });
  }, [refreshNodeBindingsForEdges, setEdges]);

  useEffect(() => {
    const changed = bindHandlersRef.current !== bindHandlers;
    const now = Date.now();
    const last = bindHandlersTraceRef.current;
    if ((now - last.ts) >= 500 || last.changed !== changed) {
      console.debug(`[CLIP TRACE] bindHandlers ref check changed=${changed}`);
      bindHandlersTraceRef.current = { ts: now, changed };
    }
    bindHandlersRef.current = bindHandlers;
  }, [bindHandlers]);

  useEffect(() => {
    if (!didHydrateRef.current) return;
    refreshNodeBindingsForEdges(edgesRef.current || [], "edges:refresh-node-bindings");
  }, [edges, refreshNodeBindingsForEdges]);

  useEffect(() => {
    if (!didHydrateRef.current) return;
    const nodesById = new Map((Array.isArray(nodes) ? nodes : []).map((nodeItem) => [nodeItem.id, nodeItem]));
    const nextSignature = (Array.isArray(edges) ? edges : [])
      .filter((edge) => {
        if (!edge?.target || !isNarrativeInput(edge?.targetHandle)) return false;
        const sourceNode = nodesById.get(edge.source) || null;
        return !!sourceNode;
      })
      .map((edge) => {
        const sourceNode = nodesById.get(edge.source) || null;
        return [
          String(edge.target || ""),
          String(edge.targetHandle || ""),
          String(edge.source || ""),
          String(edge.sourceHandle || ""),
          getNarrativeSourceRefreshSignature({
            sourceNode,
            targetHandle: String(edge.targetHandle || ""),
          }),
        ].join("|");
      })
      .join("::");
    if (nextSignature === narrativeSourceRefreshSignatureRef.current) return;
    narrativeSourceRefreshSignatureRef.current = nextSignature;
    refreshNodeBindingsForEdges(edgesRef.current || [], "narrative:source-payload-change");
  }, [edges, nodes, refreshNodeBindingsForEdges]);

  // TEMP DIAGNOSTIC: disabled bindHandlers reconciliation effect to verify reload node disappearance loop.
  // useEffect(() => {
  //   setNodes((prev) => {
  //     const edgesCount = Array.isArray(edges) ? edges.length : 0;
  //     const nodesBefore = Array.isArray(prev) ? prev.length : 0;
  //     console.info(`[CLIP TRACE] bindHandlers effect run edges=${edgesCount} nodesBefore=${nodesBefore}`);
  //     const next = bindHandlers(prev);
  //     if (areNodesMeaningfullyEqual(prev, next)) {
  //       return prev;
  //     }
  //     return next;
  //   });
  // }, [edges, bindHandlers, setNodes]);

const hydrate = useCallback((source = "unknown") => {
    if (hydrateInFlightRef.current) {
      console.warn(`[CLIP WARN] hydrate re-entry blocked source=${source}`);
      return;
    }
    hydrateInFlightRef.current = true;

    // IMPORTANT: we always have an accountKey (fallback to "guest"), so persistence works even before auth init.
    console.info("[CLIP TRACE] hydrate start source=" + source, {
      accountKey,
      STORE_KEY,
      VIDEO_JOB_STORE_KEY,
      COMFY_VIDEO_JOB_STORE_KEY,
      storageVersion: storageVersionRef.current,
      nodesBefore: nodesCountRef.current,
    });

    if (isClipHydrationBlocked()) {
      console.info("[CLIP STORAGE] hydrate skipped due to active parse/generation", {
        accountKey,
        hasActiveParseNode: !!activeParseNodeRef.current,
        hasParseController: !!parseControllerRef.current,
        comfyParseInFlightCount: comfyParseInFlightRef.current.size,
      });
      // Keep persist effects active when hydrate is intentionally skipped.
      didHydrateRef.current = true;
      hydrateInFlightRef.current = false;
      return;
    }

    didHydrateRef.current = false;
    isHydratingRef.current = true;

    // Try current key first; if empty, try a few compatible legacy keys (to survive format changes)
    let raw = safeGet(STORE_KEY);
    if (!raw) {
      const candidates = [];
      const add = (k) => {
        if (k && !candidates.includes(k)) candidates.push(k);
      };

      add(STORE_KEY);

      // legacy: without/with u_ prefix
      if (accountKey.startsWith("u_")) add(`ps:clipStoryboard:v1:${accountKey.slice(2)}`);
      else add(`ps:clipStoryboard:v1:u_${accountKey}`);

      // legacy: email key
      const emailRaw = user?.email ? String(user.email).toLowerCase() : "";
      if (emailRaw) add(`ps:clipStoryboard:v1:${emailRaw}`);

      // guest key (if user worked before login)
      add("ps:clipStoryboard:v1:guest");

      for (const k of candidates) {
        const v = safeGet(k);
        if (v) {
          raw = v;
          // migrate into current key so next loads are stable
          if (k !== STORE_KEY) safeSet(STORE_KEY, v);
          break;
        }
      }
    }
    if (!raw) {
      console.info(`[CLIP TRACE] hydrate apply nodesBefore=${nodesCountRef.current} nodesAfter=${defaultNodes.length} edgesAfter=${defaultEdges.length}`);
      setNodes(bindHandlers(defaultNodes, { nodesNow: defaultNodes, edgesNow: defaultEdges, traceReason: "hydrate:defaults" }));
      setEdges(defaultEdges);
      if (CLIP_TRACE_BRAIN_REFRESH) {
        console.info("[CLIP TRACE BRAIN REFRESH] hydrate applied defaults graph", {
          nodesCount: defaultNodes.length,
          edgesCount: defaultEdges.length,
          rederiveScheduled: true,
        });
      }
      setAssemblyResult(null);
      setAssemblyBuildState("idle");
      setIsAssemblyStale(false);
      setAssemblyInfo("");
      setAssemblyError("");
      setAssemblyJobId("");
      setAssemblyProgressPercent(0);
      setAssemblyStage("");
      setAssemblyStageLabel("");
      setAssemblyStageCurrent(0);
      setAssemblyStageTotal(0);
      setIsAssembling(false);
      lastAssemblyPayloadSignatureRef.current = "";
      // mark hydrated on next tick to avoid wiping storage with default state
      setTimeout(() => {
        didHydrateRef.current = true;
        isHydratingRef.current = false;
        hydrateInFlightRef.current = false;
      }, 0);
      return;
    }

    try {
      const parsed = JSON.parse(raw);
      const savedNodes = Array.isArray(parsed?.nodes) ? parsed.nodes : null;
      const savedEdges = Array.isArray(parsed?.edges) ? parsed.edges : null;
      const savedAssemblyResult = parsed?.assemblyResult && typeof parsed.assemblyResult === "object" ? parsed.assemblyResult : null;
      const savedAssemblyBuildState = ["idle", "done"].includes(parsed?.assemblyBuildState)
        ? parsed.assemblyBuildState
        : "idle";
      const savedAssemblyPayloadSignature = String(parsed?.assemblyPayloadSignature || "");
      const savedPlannerSignature = String(parsed?.plannerInputSignature || "");
      const savedStoryboardSignature = String(parsed?.storyboardSceneSignature || "");
      const savedComfySignature = String(parsed?.comfySceneSignature || "");

      if (!savedNodes || !savedEdges) throw new Error("bad_format");
      if (shouldInvalidateClipStoryboardStorage(parsed)) {
        console.warn("[CLIP STORAGE] hydrated payload invalidated", {
          accountKey,
          STORE_KEY,
          savedPlannerSignature,
          runtimePlannerSignature: plannerSignatureRef.current,
          savedStoryboardSignature,
          runtimeStoryboardSignature: storyboardSignatureRef.current,
          savedComfySignature,
          runtimeComfySignature: comfyStoryboardSignatureRef.current,
        });
        throw new Error("stale_payload");
      }

      // sanitize
      const cleanNodes = savedNodes
        .filter((n) => n && typeof n.id === "string" && typeof n.type === "string" && n.position)
        .map((n) => {
          const data = { ...(n.data || {}) };

          if (n.type === "brainNode") {
            const mode = ["clip", "kino", "reklama", "scenario"].includes(data.mode) ? data.mode : "clip";
            const scenarioKey = SCENARIO_OPTIONS.some((option) => option.value === data.scenarioKey)
              ? data.scenarioKey
              : "clip";
            data.mode = mode;
            data.scenarioKey = scenarioKey;
            data.isParsing = false;
            delete data.activeParseToken;
          }

          if (n.type === "comfyBrain") {
            data.audioStoryMode = normalizeAudioStoryMode(data.audioStoryMode || "lyrics_music");
            data.parseStatus = ["idle", "parsing", "ready", "error"].includes(String(data.parseStatus || "")) ? data.parseStatus : "idle";
          }

          if (n.type === "comfyStoryboard") {
            data.parseStatus = ["idle", "updating", "ready", "error", "stale"].includes(String(data.parseStatus || "")) ? data.parseStatus : "idle";
            data.mockScenes = normalizeSceneCollectionWithSceneId(data.mockScenes, "comfy_scene");
            data.sceneCount = Math.max(data.mockScenes.length, Number(data.sceneCount || 0));
            if (data.parseStatus === "updating") {
              const fallbackStatus = data.isStale === true
                ? "stale"
                : (data.sceneCount > 0 ? "ready" : "idle");
              console.warn("[COMFY STORYBOARD] hydrate reset non-active updating status", {
                nodeId: String(n.id || ""),
                fallbackStatus,
                activeRequestId: String(data.activeRequestId || ""),
                activeRequestSourceNodeId: String(data.activeRequestSourceNodeId || ""),
                sceneCount: data.sceneCount,
              });
              data.parseStatus = fallbackStatus;
            }
            data.activeRequestId = "";
            data.activeRequestSourceNodeId = "";
            data.activeRequestStartedAt = "";
            data.hasActiveRequest = false;
            data.isUpdating = false;
            data.isGenerating = false;
            data.isBusy = false;
          }

          if (n.type === "storyboardNode") {
            data.scenes = normalizeSceneCollectionWithSceneId(data.scenes, "scene");
          }

          if (n.type === "scenarioStoryboard") {
            data.scenes = normalizeSceneCollectionWithSceneId(data.scenes, "scene");
            data.sceneGeneration = data.sceneGeneration && typeof data.sceneGeneration === "object" ? data.sceneGeneration : {};
          }

          if (n.type === "introFrame") {
            data.title = String(data.title || "");
            data.userTitleRaw = String(data.userTitleRaw ?? data.title ?? "");
            data.derivedTitle = String(data.derivedTitle || "");
            data.generatedHookTitle = String(data.generatedHookTitle || "");
            data.previewTitleUsed = String(data.previewTitleUsed || "");
            data.autoTitle = !!data.autoTitle;
            data.manualTitle = !!String(data.userTitleRaw || "").trim() && (!!data.manualTitle || !data.autoTitle);
            data.stylePreset = normalizeIntroStylePreset(data.stylePreset || "cinematic_dark");
            data.durationSec = normalizeIntroDurationSec(data.durationSec);
            data.previewFormat = normalizeIntroFramePreviewFormat(data.previewFormat);
            data.imageUrl = String(data.imageUrl || "");
            data.error = String(data.error || "");
            data.previewKind = getEffectiveIntroFramePreviewKind(data);
            if (data.previewKind === INTRO_FRAME_PREVIEW_KINDS.GENERATED_LOCAL) {
              data.imageUrl = "";
            }
            data.status = ["idle", "generating", "ready", "error"].includes(String(data.status || "")) ? String(data.status) : "idle";
            data.generatedAt = String(data.generatedAt || "");
            data.altTitles = Array.isArray(data.altTitles) ? data.altTitles.slice(0, 5).map((item) => String(item || "").trim()).filter(Boolean) : [];
          }

          if (n.type === "audioNode") {
            delete data.audioType;
            data.uploading = false;
            const normalizedAudioDuration = Number(data.audioDurationSec || 0);
            data.audioDurationSec = normalizedAudioDuration > 0 ? normalizedAudioDuration : null;
          }

          if (n.type === "linkNode") {
            const normalizedSavedUrl = normalizeLinkUrl(data.urlValue || data.value || "");
            const normalizedDraftUrl = normalizeLinkUrl(data.draftUrl ?? normalizedSavedUrl);
            const payload = buildLinkNodePayload(normalizedSavedUrl);
            data.draftUrl = normalizedDraftUrl;
            data.urlValue = payload?.value || "";
            data.urlStatus = payload
              ? "ready"
              : (normalizedDraftUrl ? (String(data.urlStatus || "") === "invalid" ? "invalid" : "draft") : "empty");
            data.urlError = data.urlStatus === "invalid"
              ? String(data.urlError || "Некорректная ссылка. Используйте полный http/https URL.")
              : "";
            data.savedPayload = payload;
            data.outputPayload = payload;
            delete data.value;
          }

          if (n.type === "videoRefNode") {
            const payload = getVideoRefNodeSavedPayload(data);
            data.fileName = String(payload?.fileName || data.fileName || "");
            data.assetUrl = String(payload?.assetUrl || data.assetUrl || data.url || "");
            data.url = String(payload?.url || data.url || data.assetUrl || "");
            data.durationSec = payload?.meta?.duration ?? data.durationSec ?? null;
            data.mime = String(payload?.meta?.mime || data.mime || "");
            data.size = payload?.meta?.size ?? data.size ?? 0;
            data.posterUrl = String(payload?.posterUrl || data.posterUrl || data.previewImage || "");
            data.previewImage = String(data.posterUrl || data.previewImage || "");
            data.width = payload?.meta?.width ?? data.width ?? 0;
            data.height = payload?.meta?.height ?? data.height ?? 0;
            data.uploadError = String(data.uploadError || "");
            data.uploading = false;
            data.savedPayload = payload;
            data.outputPayload = payload;
          }

          if (n.type === "refNode") {
            const normalized = normalizeRefNodeData(data, data?.kind || "");
            normalized.uploading = false;
            delete normalized.url;
            delete normalized.name;
            return {
              id: n.id,
              type: n.type,
              position: n.position,
              data: normalized,
            };
          }

          if (["refCharacter2", "refCharacter3", "refAnimal", "refGroup"].includes(n.type)) {
            const normalized = normalizeComfyRefNodeData(n.type, data, data?.kind || "");
            return {
              id: n.id,
              type: n.type,
              position: n.position,
              data: {
                ...normalized,
                uploading: false,
              },
            };
          }

          if (n.type === "brainPackageTesterNode") {
            console.log("[BRAIN TESTER FLOW]", {
              step: "hydrate:raw_node.data.payload",
              value: data?.payload,
              type: typeof data?.payload,
              isArray: Array.isArray(data?.payload),
              isObject: !!data?.payload && typeof data?.payload === "object" && !Array.isArray(data?.payload),
              testerPayloadType: typeof data?.testerPayload,
              testerPayloadValue: data?.testerPayload,
              nodeId: n.id,
            });
          }

          if (isNarrativeTesterNodeType(n.type)) {
            return {
              id: n.id,
              type: n.type,
              position: n.position,
              data: sanitizeNarrativeTesterNodeData(n.type, data),
            };
          }

          return {
            id: n.id,
            type: n.type,
            position: n.position,
            data,
          };
        });
      const cleanNodesById = new Map(cleanNodes.map((nodeItem) => [nodeItem.id, nodeItem]));
      const cleanEdges = savedEdges
        .filter((e) => e && typeof e.id === "string" && e.source && e.target)
        .map((e) => normalizeClipStoryboardEdgeHandles(e, cleanNodesById))
        .filter(Boolean)
        .map((e) => {
          const sourceNode = cleanNodesById.get(e.source);
          const targetNode = cleanNodesById.get(e.target);
          const presentation = getEdgePresentation({
            sourceHandle: e.sourceHandle || "",
            targetHandle: e.targetHandle || "",
            sourceType: sourceNode?.type || "",
            targetType: targetNode?.type || "",
            existingKind: e?.data?.kind || "",
          });
          return {
            id: e.id,
            source: e.source,
            sourceHandle: e.sourceHandle || null,
            target: e.target,
            targetHandle: e.targetHandle || null,
            className: presentation.className,
            style: presentation.style,
            animated: presentation.animated,
            data: { ...(e.data || {}), kind: presentation.kind },
          };
        });

      const hydratedNodes = cleanNodes.length ? cleanNodes : defaultNodes;
      const hydratedEdges = cleanEdges.length ? cleanEdges : defaultEdges;
      if (CLIP_TRACE_GRAPH_HYDRATE || CLIP_TRACE_COMFY_REFS) {
        const tracedHydratedNodes = hydratedNodes
          .filter((nodeItem) => ["refNode", "refCharacter2", "refCharacter3"].includes(String(nodeItem?.type || "")))
          .map((nodeItem) => ({
            id: nodeItem.id,
            type: nodeItem.type,
            kind: String(nodeItem?.data?.kind || ""),
            refStatus: String(nodeItem?.data?.refStatus || ""),
            refsCount: Array.isArray(nodeItem?.data?.refs) ? nodeItem.data.refs.length : 0,
            refShortLabel: String(nodeItem?.data?.refShortLabel || ""),
            hasHiddenProfile: !!nodeItem?.data?.refHiddenProfile,
            refAnalyzedAt: String(nodeItem?.data?.refAnalyzedAt || ""),
            refAnalysisError: String(nodeItem?.data?.refAnalysisError || ""),
          }));
        const tracedHydratedEdges = hydratedEdges
          .filter((edgeItem) => String(edgeItem?.targetHandle || "").startsWith("ref_"))
          .map((edgeItem) => ({
            id: edgeItem.id,
            source: edgeItem.source,
            target: edgeItem.target,
            sourceHandle: edgeItem.sourceHandle || null,
            targetHandle: edgeItem.targetHandle || null,
          }));
        console.info("[CLIP TRACE GRAPH HYDRATE] hydrated refs and edges", {
          tracedHydratedNodes,
          tracedHydratedEdges,
        });
      }
      console.log("[CLIP HYDRATE] nodes count", hydratedNodes.length);
      console.log("[CLIP HYDRATE] node types", hydratedNodes.map((nodeItem) => nodeItem.type));
      if (hydratedNodes.length === 0) {
        console.warn("[CLIP HYDRATE] no nodes restored from storage");
      }
      hydratedNodes.forEach((nodeItem) => {
        if (!nodeTypes[nodeItem?.type]) {
          console.warn("[CLIP NODE TYPE MISSING]", nodeItem?.type);
        }
      });
      const hydratedAssemblySource = resolveAssemblySource({ nodes: hydratedNodes, edges: hydratedEdges });
      const hydratedScenes = hydratedAssemblySource.scenes;
      const hydratedComfyScenes = extractComfyScenesFromNodes(hydratedNodes);
      const hydratedAudioUrl = extractGlobalAudioUrlFromNodes(hydratedNodes);
      const hydratedFormat = hydratedScenes.find((scene) => String(scene?.imageFormat || scene?.format || "").trim())?.imageFormat
        || hydratedScenes.find((scene) => String(scene?.format || "").trim())?.format
        || "9:16";
      const hydratedPayload = buildAssemblyPayload({
        scenes: hydratedScenes,
        audioUrl: hydratedAudioUrl,
        format: hydratedFormat,
        intro: hydratedAssemblySource.introFrame,
      });
      const hydratedSignature = buildAssemblyPayloadSignature(hydratedPayload, hydratedAssemblySource);
      const assemblySourceSceneStats = collectSceneVideoStateStats(
        hydratedScenes,
        hydratedAssemblySource.scenesSource === "comfyStoryboard" ? "comfy_scene" : "scene"
      );
      const storyboardStats = collectSceneVideoStateStats(extractStoryboardScenesFromNodes(hydratedNodes), "scene");
      const comfyStats = collectSceneVideoStateStats(hydratedComfyScenes, "comfy_scene");
      console.info("[CLIP STORAGE] hydrate payload accepted", {
        accountKey,
        STORE_KEY,
        scenesSource: hydratedAssemblySource.scenesSource,
        assemblySourceNodeId: hydratedAssemblySource.sourceNodeId,
        assemblySourceNodeType: hydratedAssemblySource.sourceNodeType,
        introSourceNodeId: hydratedAssemblySource.introSourceNodeId,
        introSourceNodeType: hydratedAssemblySource.introSourceNodeType,
        assemblySourceSceneStats,
        storyboardStats,
        comfyStats,
        hydratedSignatureSource: `${hydratedAssemblySource.scenesSource}:${hydratedAssemblySource.sourceNodeId || "none"}`,
      });

      console.info(`[CLIP TRACE] hydrate apply nodesBefore=${nodesCountRef.current} nodesAfter=${hydratedNodes.length} edgesAfter=${hydratedEdges.length}`);
      setNodes(bindHandlers(hydratedNodes, { nodesNow: hydratedNodes, edgesNow: hydratedEdges, traceReason: "hydrate:storage" }));
      setEdges(hydratedEdges);
      if (CLIP_TRACE_BRAIN_REFRESH) {
        console.info("[CLIP TRACE BRAIN REFRESH] hydrate applied storage graph", {
          nodesCount: hydratedNodes.length,
          edgesCount: hydratedEdges.length,
          rederiveScheduled: true,
        });
      }
      console.log("[CLIP TRACE] hydrate completed", {
        nodesAfterHydrate: Array.isArray(hydratedNodes) ? hydratedNodes.length : "unknown",
        edgesAfterHydrate: Array.isArray(hydratedEdges) ? hydratedEdges.length : "unknown",
        timestamp: Date.now()
      });

      if (
        savedAssemblyResult?.finalVideoUrl
        && savedAssemblyBuildState === "done"
        && savedAssemblyPayloadSignature
        && savedAssemblyPayloadSignature === hydratedSignature
      ) {
        setAssemblyResult({
          finalVideoUrl: String(savedAssemblyResult.finalVideoUrl || ""),
          sceneCount: Number(savedAssemblyResult.sceneCount || 0),
          audioApplied: !!savedAssemblyResult.audioApplied,
          introIncluded: !!savedAssemblyResult.introIncluded,
          totalSegments: Number(savedAssemblyResult.totalSegments || 0),
          totalSteps: Number(savedAssemblyResult.totalSteps || 0),
          introDurationSec: Number(savedAssemblyResult.introDurationSec || 0),
        });
        setAssemblyBuildState("done");
        setIsAssemblyStale(false);
      } else {
        setAssemblyResult(null);
        setAssemblyBuildState("idle");
        setIsAssemblyStale(false);
      }
      setAssemblyInfo("");
      setAssemblyError("");
      setAssemblyJobId("");
      setAssemblyProgressPercent(0);
      setAssemblyStage("");
      setAssemblyStageLabel("");
      setAssemblyStageCurrent(0);
      setAssemblyStageTotal(0);
      setIsAssembling(false);
      lastAssemblyPayloadSignatureRef.current = hydratedSignature;
    } catch (err) {
      console.warn("[CLIP STORAGE] hydrate failed, fallback to defaults", {
        accountKey,
        STORE_KEY,
        error: String(err?.message || err || "hydrate_failed"),
      });
      if (String(err?.message || "") === "stale_payload") {
        clearClipStoryboardStorageForCurrentAccount("hydrate_stale_payload");
      }
      console.info(`[CLIP TRACE] hydrate apply nodesBefore=${nodesCountRef.current} nodesAfter=${defaultNodes.length} edgesAfter=${defaultEdges.length}`);
      setNodes(bindHandlers(defaultNodes, { nodesNow: defaultNodes, edgesNow: defaultEdges, traceReason: "hydrate:defaults" }));
      setEdges(defaultEdges);
      if (CLIP_TRACE_BRAIN_REFRESH) {
        console.info("[CLIP TRACE BRAIN REFRESH] hydrate fallback to defaults", {
          nodesCount: defaultNodes.length,
          edgesCount: defaultEdges.length,
          rederiveScheduled: true,
        });
      }
      setAssemblyResult(null);
      setAssemblyBuildState("idle");
      setIsAssemblyStale(false);
      setAssemblyInfo("");
      setAssemblyError("");
      setAssemblyJobId("");
      setAssemblyProgressPercent(0);
      setAssemblyStage("");
      setAssemblyStageLabel("");
      setAssemblyStageCurrent(0);
      setAssemblyStageTotal(0);
      setIsAssembling(false);
      lastAssemblyPayloadSignatureRef.current = "";
    } finally {
      // mark hydrated on next tick so persist effect can't overwrite storage
      setTimeout(() => {
        didHydrateRef.current = true;
        isHydratingRef.current = false;
        hydrateInFlightRef.current = false;
      }, 0);
    }
  }, [
    STORE_KEY,
    VIDEO_JOB_STORE_KEY,
    COMFY_VIDEO_JOB_STORE_KEY,
    setNodes,
    setEdges,
    defaultNodes,
    defaultEdges,
    accountKey,
    user,
    shouldInvalidateClipStoryboardStorage,
    isClipHydrationBlocked,
    clearClipStoryboardStorageForCurrentAccount,
  ]);

  const hydrateRef = useRef(hydrate);

  useEffect(() => {
    hydrateRef.current = hydrate;
    console.info("[CLIP TRACE] hydrate ref updated");
  }, [hydrate]);

  const addNodeFromDrawer = useCallback((type) => {
    const id = `${type}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
    const centerX = 360;
    const centerY = 220;
    const jitterX = Math.round((Math.random() - 0.5) * 120);
    const jitterY = Math.round((Math.random() - 0.5) * 120);

    let node;
    if (type === "audio") {
      node = { id, type: "audioNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { audioUrl: "", audioName: "", uploading: false, audioDurationSec: null } };
    } else if (type === "text") {
      node = { id, type: "textNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { textValue: "" } };
    } else if (type === "link") {
      node = { id, type: "linkNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { draftUrl: "", urlValue: "", urlStatus: "empty", urlError: "", savedPayload: null, outputPayload: null } };
    } else if (type === "videoRef") {
      node = { id, type: "videoRefNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { fileName: "", assetUrl: "", url: "", durationSec: null, mime: "", size: 0, posterUrl: "", previewImage: "", width: 0, height: 0, uploading: false, uploadError: "", savedPayload: null, outputPayload: null } };
    } else if (type === "brain") {
      node = { id, type: "brainNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: "clip", scenarioKey: "clip", shootKey: "cinema", styleKey: "realism", freezeStyle: false, clipSec: 30 } };
    } else if (type === "ref_character") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — ПЕРСОНАЖ", icon: "🧍", kind: "ref_character", refs: [], roleType: "auto", uploading: false, refStatus: "empty" } };
    } else if (type === "ref_location") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — ЛОКАЦИЯ", icon: "📍", kind: "ref_location", refs: [], uploading: false, refStatus: "empty" } };
    } else if (type === "ref_style") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — СТИЛЬ", icon: "🎨", kind: "ref_style", refs: [], uploading: false, refStatus: "empty" } };
    } else if (type === "ref_items") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — ПРЕДМЕТЫ", icon: "📦", kind: "ref_items", refs: [], uploading: false, refStatus: "empty" } };
    } else if (type === "storyboard") {
      node = { id, type: "storyboardNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { scenes: [], sceneGeneration: {} } };
    } else if (type === "introFrame") {
      node = {
        id,
        type: "introFrame",
        position: { x: centerX + jitterX, y: centerY + jitterY },
        data: { title: "", userTitleRaw: "", derivedTitle: "", generatedHookTitle: "", previewTitleUsed: "", autoTitle: true, manualTitle: false, stylePreset: "cinematic_dark", durationSec: 2.5, previewFormat: INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE, imageUrl: "", previewKind: "", status: "idle", generatedAt: "", altTitles: [], error: "" },
      };
    } else if (type === "assembly") {
      node = { id, type: "assemblyNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: {} };
    } else if (type === "comfyNarrative") {
      node = { id, type: "comfyNarrative", position: { x: centerX + jitterX, y: centerY + jitterY }, data: getDefaultNarrativeNodeData() };
    } else if (type === "scenarioOutputTester") {
      node = { id, type: "scenarioOutputTesterNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { testerType: "scenarioOutputTesterNode" } };
    } else if (type === "voiceOutputTester") {
      node = { id, type: "voiceOutputTesterNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { testerType: "voiceOutputTesterNode" } };
    } else if (type === "brainPackageTester") {
      node = { id, type: "brainPackageTesterNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { testerType: "brainPackageTesterNode" } };
    } else if (type === "musicPromptTester") {
      node = { id, type: "musicPromptTesterNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { testerType: "musicPromptTesterNode" } };
    } else if (type === "comfyBrain") {
      node = { id, type: "comfyBrain", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'clip', plannerMode: 'legacy', output: 'comfy image', format: DEFAULT_SCENE_IMAGE_FORMAT, genre: '', audioStoryMode: 'lyrics_music', styleKey: 'realism', freezeStyle: false, parseStatus: 'idle' } };
    } else if (type === "comfyStoryboard") {
      node = { id, type: "comfyStoryboard", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mockScenes: [], sceneCount: 0, mode: 'clip', parseStatus: 'idle' } };
    } else if (type === "scenarioStoryboard") {
      node = { id, type: "scenarioStoryboard", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { scenes: [], sceneGeneration: {} } };
    } else if (type === "comfyVideoPreview") {
      node = { id, type: "comfyVideoPreview", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { previewStatus: 'idle', previewUrl: '', workflowPreset: 'comfy-default', format: '9:16', duration: 0 } };
    } else if (type === "refCharacter2") {
      node = { id, type: "refCharacter2", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'ally', name: '', identityLock: false, priority: 'normal', notes: '', refs: [], roleType: "auto", uploading: false, refStatus: 'empty' } };
    } else if (type === "refCharacter3") {
      node = { id, type: "refCharacter3", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'ally', name: '', identityLock: false, priority: 'normal', notes: '', refs: [], roleType: "auto", uploading: false, refStatus: 'empty' } };
    } else if (type === "refAnimal") {
      node = { id, type: "refAnimal", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'single animal', speciesHint: '', scaleLock: false, behavior: 'neutral', notes: '', refs: [], uploading: false, refStatus: 'empty' } };
    } else if (type === "refGroup") {
      node = { id, type: "refGroup", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'crowd', density: 'medium', formation: '', outfitConsistency: 'varied outfit', notes: '', refs: [], uploading: false, refStatus: 'empty' } };
    } else {
      return;
    }

    setNodes((prev) => {
      const nextNodes = prev.concat(node);
      return bindHandlers(nextNodes, { nodesNow: nextNodes, edgesNow: edgesRef.current || [], traceReason: "node:add" });
    });
    setDrawerOpen(false);
  }, [setNodes, bindHandlers]);


  // hydrate on account change
  useEffect(() => {
    hydrateRef.current("effect:account-change");
  }, [accountKey]);

  useEffect(() => () => {
    if (parseTimeoutRef.current) {
      clearTimeout(parseTimeoutRef.current);
      parseTimeoutRef.current = null;
    }
    if (parseControllerRef.current) {
      parseControllerRef.current.abort();
      parseControllerRef.current = null;
    }
    activeParseNodeRef.current = null;
  }, []);

  // re-hydrate when session changes (logout/login without full reload)
  useEffect(() => {
    const onSessionChanged = () => {
      if (isClipHydrationBlocked()) {
        console.info("[CLIP STORAGE] skip sessionChanged hydrate due to active parse/generation", { accountKey });
        return;
      }
      // wait a tick so AuthContext can update ps:lastUserId/ps:lastEmail
      setTimeout(() => {
        hydrateRef.current("event:sessionChanged");
      }, 0);
    };
    window.addEventListener("ps:sessionChanged", onSessionChanged);
    return () => window.removeEventListener("ps:sessionChanged", onSessionChanged);
  }, [accountKey, isClipHydrationBlocked]);

  useEffect(() => {
    console.info("[CLIP STORAGE] active account scope", {
      accountKey,
      STORE_KEY,
      VIDEO_JOB_STORE_KEY,
      COMFY_VIDEO_JOB_STORE_KEY,
    });
  }, [COMFY_VIDEO_JOB_STORE_KEY, STORE_KEY, VIDEO_JOB_STORE_KEY, accountKey]);


  const lastPersistedPayloadRef = useRef("");
  const persistDepsSnapshotRef = useRef(null);

  // persist
  useEffect(() => {
    const depsSnapshot = {
      nodesRef: nodes,
      edgesRef: edges,
      STORE_KEY,
      accountKey,
      assemblyResultRef: assemblyResult,
      assemblyBuildState,
      assemblyPayloadSignature,
      scenarioScenesRef: scenarioScenes,
      comfyScenesRef: comfyScenes,
    };
    const prevDepsSnapshot = persistDepsSnapshotRef.current;
    if (prevDepsSnapshot) {
      const changedDeps = [];
      if (prevDepsSnapshot.nodesRef !== depsSnapshot.nodesRef) changedDeps.push("nodes");
      if (prevDepsSnapshot.edgesRef !== depsSnapshot.edgesRef) changedDeps.push("edges");
      if (prevDepsSnapshot.STORE_KEY !== depsSnapshot.STORE_KEY) changedDeps.push("STORE_KEY");
      if (prevDepsSnapshot.accountKey !== depsSnapshot.accountKey) changedDeps.push("accountKey");
      if (prevDepsSnapshot.assemblyResultRef !== depsSnapshot.assemblyResultRef) changedDeps.push("assemblyResult");
      if (prevDepsSnapshot.assemblyBuildState !== depsSnapshot.assemblyBuildState) changedDeps.push("assemblyBuildState");
      if (prevDepsSnapshot.assemblyPayloadSignature !== depsSnapshot.assemblyPayloadSignature) changedDeps.push("assemblyPayloadSignature");
      if (prevDepsSnapshot.scenarioScenesRef !== depsSnapshot.scenarioScenesRef) changedDeps.push("scenarioScenes");
      if (prevDepsSnapshot.comfyScenesRef !== depsSnapshot.comfyScenesRef) changedDeps.push("comfyScenes");
      if (CLIP_TRACE_PERSIST) {
        console.info("[CLIP TRACE] persist deps changed", {
          changedDeps,
          changedDepsCount: changedDeps.length,
        });
      }
    } else {
      if (CLIP_TRACE_PERSIST) {
        console.info("[CLIP TRACE] persist deps changed", {
          changedDeps: ["initial_run"],
          changedDepsCount: 1,
        });
      }
    }
    persistDepsSnapshotRef.current = depsSnapshot;

    if (CLIP_TRACE_PERSIST) {
      console.log("[CLIP TRACE] persist effect triggered", {
        nodesCount: Array.isArray(nodes) ? nodes.length : "unknown",
        edgesCount: Array.isArray(edges) ? edges.length : "unknown",
        timestamp: Date.now()
      });
    }

    if (!didHydrateRef.current) {
      if (CLIP_TRACE_PERSIST) {
        console.info("[CLIP TRACE] persist skipped reason=not_hydrated");
      }
      return;
    }
    if (isHydratingRef.current) {
      if (CLIP_TRACE_PERSIST) {
        console.info("[CLIP TRACE] persist skipped reason=hydrate_in_progress");
      }
      return;
    }

    // strip handlers from data
    const serialNodes = serializeNodesForStorage(nodes);
    const serialEdges = edges.map((e) => ({ id: e.id, source: e.source, sourceHandle: e.sourceHandle || null, target: e.target, targetHandle: e.targetHandle || null }));
    if (CLIP_TRACE_COMFY_REFS) {
      const tracedNodes = serialNodes
        .filter((nodeItem) => ["refNode", "refCharacter2", "refCharacter3"].includes(String(nodeItem?.type || "")))
        .map((nodeItem) => ({
          id: nodeItem.id,
          type: nodeItem.type,
          kind: String(nodeItem?.data?.kind || ""),
          refStatus: String(nodeItem?.data?.refStatus || ""),
          refsCount: Array.isArray(nodeItem?.data?.refs) ? nodeItem.data.refs.length : 0,
          refShortLabel: String(nodeItem?.data?.refShortLabel || ""),
          hasHiddenProfile: !!nodeItem?.data?.refHiddenProfile,
          refAnalyzedAt: String(nodeItem?.data?.refAnalyzedAt || ""),
          refAnalysisError: String(nodeItem?.data?.refAnalysisError || ""),
        }));
      const tracedEdges = serialEdges
        .filter((edgeItem) => String(edgeItem?.targetHandle || "").startsWith("ref_"))
        .map((edgeItem) => ({
          id: edgeItem.id,
          source: edgeItem.source,
          target: edgeItem.target,
          sourceHandle: edgeItem.sourceHandle || null,
          targetHandle: edgeItem.targetHandle || null,
        }));
      console.info("[CLIP TRACE COMFY REFS] persist snapshot", { tracedNodes, tracedEdges });
    }

    const storyboardSceneSignature = buildSceneSignature(scenarioScenes, "scene");
    const comfySceneSignature = buildSceneSignature(comfyScenes, "comfy_scene");
    const plannerInputSignature = String(plannerSignatureRef.current || "");
    const payloadComparable = {
      nodes: serialNodes,
      edges: serialEdges,
      plannerInputSignature,
      storyboardSceneSignature,
      comfySceneSignature,
      accountKey,
      assemblyResult: assemblyResult?.finalVideoUrl
        ? {
          finalVideoUrl: String(assemblyResult.finalVideoUrl || ""),
          sceneCount: Number(assemblyResult.sceneCount || 0),
          audioApplied: !!assemblyResult.audioApplied,
          introIncluded: !!assemblyResult.introIncluded,
          totalSegments: Number(assemblyResult.totalSegments || 0),
          totalSteps: Number(assemblyResult.totalSteps || 0),
          introDurationSec: Number(assemblyResult.introDurationSec || 0),
        }
        : null,
      assemblyBuildState: assemblyBuildState === "done" ? "done" : "idle",
      assemblyPayloadSignature,
    };
    const comparablePayloadString = JSON.stringify(payloadComparable);
    const isSamePayload = comparablePayloadString === lastPersistedPayloadRef.current;
    if (CLIP_TRACE_PERSIST) {
      console.info("[CLIP TRACE] persist payload compare", {
        isSamePayload,
        payloadLength: comparablePayloadString.length,
        previousPayloadLength: lastPersistedPayloadRef.current.length,
      });
    }
    if (isSamePayload) {
      if (CLIP_TRACE_PERSIST) {
        console.info("[CLIP TRACE] persist skipped same payload");
      }
      return;
    }

    if (CLIP_TRACE_PERSIST) {
      console.info(`[CLIP TRACE] persist write nodes=${serialNodes.length} edges=${serialEdges.length}`);
    }
    if (serialNodes.length === 0) {
      console.warn("[CLIP WARN] persist attempted with empty nodes");
    }
    if (serialEdges.length === 0) {
      console.warn("[CLIP WARN] persist attempted with empty edges");
    }
    const ok = safeSet(STORE_KEY, JSON.stringify({
      ...payloadComparable,
      persistedAt: new Date().toISOString(),
    }));
    if (ok) {
      lastPersistedPayloadRef.current = comparablePayloadString;
      console.info("[CLIP STORAGE] persist state", {
        accountKey,
        STORE_KEY,
        plannerInputSignature,
        storyboardSceneStats: collectSceneVideoStateStats(scenarioScenes, "scene"),
        comfySceneStats: collectSceneVideoStateStats(comfyScenes, "comfy_scene"),
      });
      setLastSavedAt(Date.now());
    }
  }, [
    nodes,
    edges,
    STORE_KEY,
    accountKey,
    assemblyResult,
    assemblyBuildState,
    assemblyPayloadSignature,
    scenarioScenes,
    comfyScenes,
  ]);

  // extra safety: flush to storage on page unload (helps when navigating away quickly)
  useEffect(() => {
    const onBeforeUnload = () => {
      if (!didHydrateRef.current) return;
      if (isHydratingRef.current) return;

      const serialNodes = serializeNodesForStorage(nodes);
      const serialEdges = edges.map((e) => ({ id: e.id, source: e.source, sourceHandle: e.sourceHandle || null, target: e.target, targetHandle: e.targetHandle || null }));
      const ok = safeSet(STORE_KEY, JSON.stringify({
      nodes: serialNodes,
      edges: serialEdges,
      plannerInputSignature: String(plannerSignatureRef.current || ""),
      storyboardSceneSignature: buildSceneSignature(scenarioScenes, "scene"),
      comfySceneSignature: buildSceneSignature(comfyScenes, "comfy_scene"),
      persistedAt: new Date().toISOString(),
      accountKey,
      assemblyResult: assemblyResult?.finalVideoUrl
        ? {
          finalVideoUrl: String(assemblyResult.finalVideoUrl || ""),
          sceneCount: Number(assemblyResult.sceneCount || 0),
          audioApplied: !!assemblyResult.audioApplied,
          introIncluded: !!assemblyResult.introIncluded,
          totalSegments: Number(assemblyResult.totalSegments || 0),
          totalSteps: Number(assemblyResult.totalSteps || 0),
          introDurationSec: Number(assemblyResult.introDurationSec || 0),
        }
        : null,
      assemblyBuildState: assemblyBuildState === "done" ? "done" : "idle",
      assemblyPayloadSignature,
    }));
      if (ok) setLastSavedAt(Date.now());
    };

    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [
    nodes,
    edges,
    STORE_KEY,
    accountKey,
    assemblyResult,
    assemblyBuildState,
    assemblyPayloadSignature,
    scenarioScenes,
    comfyScenes,
  ]);


  const nodeTypes = useMemo(
    () => ({
      audioNode: AudioNode,
      textNode: TextNode,
      linkNode: LinkNode,
      videoRefNode: VideoRefNode,
      brainNode: BrainNode,
      refNode: RefNode,
      storyboardNode: StoryboardPlanNode,
      introFrame: IntroFrameNode,
      assemblyNode: AssemblyNode,
      comfyNarrative: ComfyNarrativeNode,
      scenarioOutputTesterNode: ScenarioOutputTesterNode,
      voiceOutputTesterNode: VoiceOutputTesterNode,
      brainPackageTesterNode: BrainPackageTesterNode,
      musicPromptTesterNode: MusicPromptTesterNode,
      comfyBrain: ComfyBrainNode,
      comfyStoryboard: ComfyStoryboardNode,
      scenarioStoryboard: ScenarioStoryboardNode,
      comfyVideoPreview: ComfyVideoPreviewNode,
      refCharacter2: RefCharacter2Node,
      refCharacter3: RefCharacter3Node,
      refAnimal: RefAnimalNode,
      refGroup: RefGroupNode,
    }),
    []
  );

  const onConnect = useCallback(
    (params) => {
      setEdges((eds) => {
        const nodesNow = nodesRef.current || [];
        const src = nodesNow.find((n) => n.id === params.source);
        const dst = nodesNow.find((n) => n.id === params.target);
        if (!src || !dst) return eds;
        let nextEdges = eds;

        if (dst.type === "brainNode") {
          const h = params.targetHandle || "";
          const ok =
            (h === "audio" && src.type === "audioNode" && (params.sourceHandle || "") === "audio") ||
            (h === "text" && src.type === "textNode" && (params.sourceHandle || "") === "text") ||
            (h === "ref_character" && src.type === "refNode" && (params.sourceHandle || "") === "ref_character") ||
            (h === "ref_location" && src.type === "refNode" && (params.sourceHandle || "") === "ref_location") ||
            (h === "ref_style" && src.type === "refNode" && (params.sourceHandle || "") === "ref_style") ||
            (h === "ref_items" && src.type === "refNode" && (params.sourceHandle || "") === "ref_items");
          if (!ok) return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || "") === h));
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: h, sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          refreshNodeBindingsForEdges(nextEdges, "edges:connect");
          return nextEdges;
        }

        if (dst.type === 'comfyBrain') {
          const h = params.targetHandle || '';
          const sourceHandle = params.sourceHandle || '';
          const refCfg = COMFY_BRAIN_REF_HANDLE_CONFIG[h];
          const ok =
            (h === 'brain_package' && src.type === 'comfyNarrative' && sourceHandle === 'brain_package_out') ||
            (h === 'audio' && src.type === 'audioNode' && sourceHandle === 'audio') ||
            (h === 'text' && src.type === 'textNode' && sourceHandle === 'text') ||
            (!!refCfg && src.type === refCfg.sourceType && sourceHandle === refCfg.sourceHandle);
          if (!ok) return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || '') === h));
          const presentation = getEdgePresentation({ sourceHandle, targetHandle: h, sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          refreshNodeBindingsForEdges(nextEdges, h === 'brain_package' ? "edges:connect:narrative-brain-package" : "edges:connect");
          return nextEdges;
        }

        if (dst.type === "comfyNarrative") {
          const h = params.targetHandle || "";
          const sourceHandle = params.sourceHandle || "";
          const ok =
            (h === "audio_in" && src.type === "audioNode" && sourceHandle === "audio") ||
            (h === "video_link_in" && src.type === "linkNode" && sourceHandle === "link") ||
            (h === "video_file_in" && src.type === "videoRefNode" && sourceHandle === "video_ref") ||
            (h === "video_file_in" && src.type === "comfyStoryboard" && sourceHandle === COMFY_STORYBOARD_MAIN_HANDLE) ||
            (h === "ref_character_1" && src.type === "refNode" && sourceHandle === "ref_character") ||
            (h === "ref_character_2" && src.type === "refCharacter2" && sourceHandle === "ref_character_2") ||
            (h === "ref_character_3" && src.type === "refCharacter3" && sourceHandle === "ref_character_3") ||
            (h === "ref_props" && src.type === "refNode" && sourceHandle === "ref_items") ||
            (h === "ref_location" && src.type === "refNode" && sourceHandle === "ref_location") ||
            (h === "ref_style" && src.type === "refNode" && sourceHandle === "ref_style");
          if (!ok) return eds;
          const cleaned = isNarrativeSourceInput(h)
            ? removeNarrativeIncomingSourceEdges(eds, dst.id)
            : eds.filter((e) => !(e.target === dst.id && String(e.targetHandle || "") === h));
          const presentation = getEdgePresentation({ sourceHandle, targetHandle: h, sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          refreshNodeBindingsForEdges(nextEdges, "edges:connect:narrative");
          return nextEdges;
        }

        if (isNarrativeTesterNodeType(dst.type)) {
          const targetHandle = params.targetHandle || "";
          const sourceHandle = params.sourceHandle || "";
          const testerConfig = getNarrativeTesterConfig(dst.type);
          const ok = src.type === "comfyNarrative"
            && sourceHandle === String(testerConfig?.acceptHandle || "")
            && targetHandle === String(testerConfig?.acceptHandle || "");
          if (!ok) return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && String(e.targetHandle || "") === targetHandle));
          const presentation = getEdgePresentation({ sourceHandle, targetHandle, sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          refreshNodeBindingsForEdges(nextEdges, "edges:connect:narrative-tester");
          return nextEdges;
        }

        if (src.type === "brainNode" && (params.sourceHandle || "") === "plan") {
          if (dst.type === "storyboardNode" && (params.targetHandle || "") === "plan_in") {
            const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || "") === "plan_in"));
            const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
            nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
            refreshNodeBindingsForEdges(nextEdges, "edges:connect");
            return nextEdges;
          }
          return eds;
        }

        if (src.type === "comfyNarrative" && (params.sourceHandle || "") === "storyboard_out") {
          const targetHandle = params.targetHandle || "";
          const isLegacyStoryboardRoute = dst.type === "storyboardNode" && targetHandle === "plan_in";
          const isScenarioStoryboardRoute = dst.type === "scenarioStoryboard" && targetHandle === "scenario_storyboard_in";
          if (!isLegacyStoryboardRoute && !isScenarioStoryboardRoute) {
            traceScenarioGraphConnect("rejected", {
              sourceType: src.type,
              sourceHandle: params.sourceHandle || "",
              targetType: dst.type,
              targetHandle,
            });
            return eds;
          }
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || "") === targetHandle));
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          if (isScenarioStoryboardRoute) {
            traceScenarioGraphConnect("accepted", {
              sourceType: src.type,
              sourceHandle: params.sourceHandle || "",
              targetType: dst.type,
              targetHandle,
            });
          } else if (CLIP_TRACE_SCENARIO_GRAPH) {
            console.debug("[SCENARIO GRAPH] legacy storyboard route accepted", {
              sourceType: src.type,
              sourceHandle: params.sourceHandle || "",
              targetType: dst.type,
              targetHandle,
            });
          }
          refreshNodeBindingsForEdges(nextEdges, isScenarioStoryboardRoute ? "edges:connect:scenario-storyboard" : "edges:connect:narrative-storyboard");
          return nextEdges;
        }

        if (src.type === "comfyNarrative" && (params.sourceHandle || "") === "preview_out") {
          if (dst.type === "introFrame" && (params.targetHandle || "") === INTRO_FRAME_STORY_HANDLE) {
            const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || "") === INTRO_FRAME_STORY_HANDLE));
            const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
            nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
            traceScenarioGraphConnect("accepted", {
              sourceType: src.type,
              sourceHandle: params.sourceHandle || "",
              targetType: dst.type,
              targetHandle: params.targetHandle || "",
            });
            refreshNodeBindingsForEdges(nextEdges, "edges:connect:scenario-intro");
            return nextEdges;
          }
          if (dst.type !== "comfyVideoPreview" || (params.targetHandle || "") !== "scenario_preview_in") {
            traceScenarioGraphConnect("rejected", {
              sourceType: src.type,
              sourceHandle: params.sourceHandle || "",
              targetType: dst.type,
              targetHandle: params.targetHandle || "",
            });
            return eds;
          }
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || "") === "scenario_preview_in"));
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          if (CLIP_TRACE_SCENARIO_GRAPH) {
            console.debug("[SCENARIO GRAPH] legacy preview route accepted", {
              sourceType: src.type,
              sourceHandle: params.sourceHandle || "",
              targetType: dst.type,
              targetHandle: params.targetHandle || "",
            });
          }
          refreshNodeBindingsForEdges(nextEdges, "edges:connect:scenario-preview");
          return nextEdges;
        }

        if (src.type === 'comfyBrain' && (params.sourceHandle || '') === 'comfy_plan') {
          if (dst.type !== 'comfyStoryboard' || (params.targetHandle || '') !== 'comfy_plan') return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || '') === 'comfy_plan'));
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || '', targetHandle: params.targetHandle || '', sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          refreshNodeBindingsForEdges(nextEdges, "edges:connect");
          return nextEdges;
        }

        if (dst.type === "introFrame") {
          const targetHandle = params.targetHandle || "";
          const sourceHandle = params.sourceHandle || "";
          const allowStoryContextScenario =
            targetHandle === INTRO_FRAME_STORY_HANDLE
            && src.type === "comfyNarrative"
            && sourceHandle === "preview_out";
          if (!allowStoryContextScenario) {
            traceScenarioGraphConnect("rejected", {
              sourceType: src.type,
              sourceHandle,
              targetType: dst.type,
              targetHandle,
            });
            return eds;
          }
          const presentation = getEdgePresentation({ sourceHandle, targetHandle, sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, eds);
          traceScenarioGraphConnect("accepted", {
            sourceType: src.type,
            sourceHandle,
            targetType: dst.type,
            targetHandle,
          });
          refreshNodeBindingsForEdges(nextEdges, "edges:connect");
          return nextEdges;
        }

        if (dst.type === "scenarioStoryboard") {
          traceScenarioGraphConnect("rejected", {
            sourceType: src.type,
            sourceHandle: params.sourceHandle || "",
            targetType: dst.type,
            targetHandle: params.targetHandle || "",
          });
          return eds;
        }

        if (src.type === 'comfyStoryboard' && (params.sourceHandle || '') === COMFY_STORYBOARD_MAIN_HANDLE) {
          if (dst.type === 'assemblyNode' && (params.targetHandle || '') === 'assembly_in') {
            const cleaned = removeAssemblyIncomingSourceEdges(eds, dst.id, "assembly_in");
            const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || '', targetHandle: params.targetHandle || '', sourceType: src.type, targetType: dst.type });
            nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
            refreshNodeBindingsForEdges(nextEdges, "edges:connect");
            return nextEdges;
          }
          if (dst.type !== 'comfyVideoPreview' || (params.targetHandle || '') !== COMFY_STORYBOARD_MAIN_HANDLE) return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || '') === COMFY_STORYBOARD_MAIN_HANDLE));
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || '', targetHandle: params.targetHandle || '', sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          refreshNodeBindingsForEdges(nextEdges, "edges:connect");
          return nextEdges;
        }

        if (src.type === 'comfyStoryboard' || src.type === 'comfyBrain' || src.type === 'comfyVideoPreview' || dst.type === 'comfyStoryboard' || dst.type === 'comfyBrain' || dst.type === 'comfyVideoPreview') {
          return eds;
        }

        if (src.type === "introFrame" && (params.sourceHandle || "") === "intro_frame_out") {
          if (dst.type !== "assemblyNode" || (params.targetHandle || "") !== "assembly_intro") {
            traceScenarioGraphConnect("rejected", {
              sourceType: src.type,
              sourceHandle: params.sourceHandle || "",
              targetType: dst.type,
              targetHandle: params.targetHandle || "",
            });
            return eds;
          }
          const cleaned = removeAssemblyIncomingSourceEdges(eds, dst.id, "assembly_intro");
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          traceScenarioGraphConnect("accepted", {
            sourceType: src.type,
            sourceHandle: params.sourceHandle || "",
            targetType: dst.type,
            targetHandle: params.targetHandle || "",
          });
          refreshNodeBindingsForEdges(nextEdges, "edges:connect");
          return nextEdges;
        }

        if (src.type === "storyboardNode" && (params.sourceHandle || "") === "plan_out") {
          if (dst.type !== "assemblyNode" || (params.targetHandle || "") !== "assembly_in") return eds;
          const cleaned = removeAssemblyIncomingSourceEdges(eds, dst.id, "assembly_in");
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          refreshNodeBindingsForEdges(nextEdges, "edges:connect");
          return nextEdges;
        }

        if (src.type === "scenarioStoryboard" && (params.sourceHandle || "") === "scenario_storyboard_out") {
          if (dst.type !== "assemblyNode" || (params.targetHandle || "") !== "assembly_in") {
            traceScenarioGraphConnect("rejected", {
              sourceType: src.type,
              sourceHandle: params.sourceHandle || "",
              targetType: dst.type,
              targetHandle: params.targetHandle || "",
            });
            return eds;
          }
          const cleaned = removeAssemblyIncomingSourceEdges(eds, dst.id, "assembly_in");
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
          nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          traceScenarioGraphConnect("accepted", {
            sourceType: src.type,
            sourceHandle: params.sourceHandle || "",
            targetType: dst.type,
            targetHandle: params.targetHandle || "",
          });
          refreshNodeBindingsForEdges(nextEdges, "edges:connect:scenario-storyboard-assembly");
          return nextEdges;
        }

        const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
        nextEdges = addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, eds);
        refreshNodeBindingsForEdges(nextEdges, "edges:connect");
        return nextEdges;
      });
    },
    [refreshNodeBindingsForEdges, setEdges]
  );

  const onEdgeClick = useCallback((evt, edge) => {
    evt?.stopPropagation?.();
    if (!edge?.id) return;
    setEdges((eds) => {
      const nextEdges = eds.filter((e) => e.id !== edge.id);
      refreshNodeBindingsForEdges(nextEdges, "edges:remove");
      return nextEdges;
    });
  }, [refreshNodeBindingsForEdges, setEdges]);

  useEffect(() => {
    const cleaned = enforceSingleNarrativeSourceEdge(edges);
    if (cleaned !== edges) {
      refreshNodeBindingsForEdges(cleaned, "edges:enforce-single-source");
      setEdges(cleaned);
    }
  }, [edges, refreshNodeBindingsForEdges, setEdges]);

  useEffect(() => {
    const onKey = (e) => {
      if (e.key === "Escape") {
        setDrawerOpen(false);
        setBrainGuideOpen(false);
      }
      if (e.key === "Tab") {
        e.preventDefault();
        setDrawerOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const stats = useMemo(
    () => ({
      nodes: nodes.length,
      edges: edges.length,
    }),
    [nodes.length, edges.length]
  );

  return (
    <div className="clipSB_root">
      <div className="clipSB_hud">
        <button className="clipSB_hudBtn" onClick={() => setDrawerOpen(true)}>Ноды</button>
        <button className="clipSB_hudBtn clipSB_hudBtnSecondary" onClick={() => navigate("/studio")}>К студиям</button>
        <div className="clipSB_guideWrap" ref={brainGuideRef}>
          <button
            className={"clipSB_hudBtn clipSB_hudBtnGuide" + (brainGuideOpen ? " isActive" : "")}
            onClick={() => setBrainGuideOpen((v) => !v)}
            aria-haspopup="dialog"
            aria-expanded={brainGuideOpen}
          >
            ℹ Guide
          </button>
          {brainGuideOpen ? (
            <div className="clipSB_guidePopover" role="dialog" aria-label="COMFY BRAIN quick guide">
              <div className="clipSB_guideTitle">COMFY BRAIN • quick guide</div>
              <div className="clipSB_guideGrid">
                <section className="clipSB_guideCard">
                  <h4>MODE</h4>
                  <ul>
                    <li><b>Clip</b> — музыкальный монтаж и ассоциативность.</li>
                    <li><b>Kino</b> — причинная кинодрама и логика сцен.</li>
                    <li><b>Reklama</b> — коммуникация, продукт и message.</li>
                    <li><b>Scenario</b> — строгий storyboard по смыслу.</li>
                  </ul>
                </section>
                <section className="clipSB_guideCard">
                  <h4>AUDIO STORY MODE</h4>
                  <ul>
                    <li><b>lyrics + music</b> — историю вместе ведут lyrics и музыка.</li>
                    <li><b>music only</b> — lyrics игнорируются, работает ритм и энергия.</li>
                    <li><b>music + text</b> — lyrics игнорируются, narrative direction даёт TEXT.</li>
                  </ul>
                </section>
                <section className="clipSB_guideCard">
                  <h4>FORMAT / CANVAS</h4>
                  <ul>
                    <li><b>9:16</b> — mobile / TikTok / Reels.</li>
                    <li><b>16:9</b> — YouTube / cinematic.</li>
                    <li><b>1:1</b> — universal / product / ads.</li>
                  </ul>
                  <p>FORMAT задаёт композицию сцены, кадрирование героя, место для текста и ритм монтажа.</p>
                  <p><b>Важно:</b> выбирай FORMAT до генерации — он влияет на весь сценарий.</p>
                </section>
                <section className="clipSB_guideCard">
                  <h4>FOCUS</h4>
                  <ul>
                    <li><b>character</b> — face, reaction, emotion.</li>
                    <li><b>object</b> — product / item / prop.</li>
                    <li><b>environment</b> — world, atmosphere, location.</li>
                  </ul>
                  <p>Главный субъект должен забирать примерно 50–60% внимания зрителя. Остальное только поддерживает его.</p>
                </section>
                <section className="clipSB_guideCard">
                  <h4>TEXT</h4>
                  <p>TEXT controls meaning:</p>
                  <ul>
                    <li><b>override</b> → напрямую задаёт историю.</li>
                    <li><b>guide</b> → направляет логику сцен.</li>
                    <li><b>enhance</b> → усиливает основную идею.</li>
                  </ul>
                </section>
                <section className="clipSB_guideCard">
                  <h4>STYLE</h4>
                  <p>STYLE усиливает визуал и не должен ломать драматургию MODE.</p>
                  <p>STYLE — это не сама история, а усиление атмосферы и подачи.</p>
                </section>
                <section className="clipSB_guideCard">
                  <h4>HOOK / PREVIEW</h4>
                  <p>Сильный preview обычно строится так:</p>
                  <ul>
                    <li><b>1</b> — headline / intrigue.</li>
                    <li><b>2</b> — emotion / face / action.</li>
                    <li><b>3</b> — contrast / light / situation.</li>
                  </ul>
                  <p>Держи hook в 2–5 словах, не перекрывай лица, оставляй information gap и повод кликнуть.</p>
                </section>
                <section className="clipSB_guideCard">
                  <h4>FLOW</h4>
                  <p><b>COMFY BRAIN → STORYBOARD → INTRO FRAME → ASSEMBLY</b></p>
                  <ul>
                    <li><b>Brain</b> — sets the rules: mode, format, style.</li>
                    <li><b>Storyboard</b> — splits the story into scenes.</li>
                    <li><b>Intro Frame</b> — builds preview / first frame.</li>
                    <li><b>Assembly</b> — collects the final video.</li>
                  </ul>
                </section>
                <section className="clipSB_guideCard">
                  <h4>REFS</h4>
                  <p>REFS фиксируют персонажей, мир и continuity между сценами.</p>
                </section>
                <section className="clipSB_guideCard">
                  <h4>WHAT NOT TO DO</h4>
                  <ul>
                    <li>Не перегружай сцену слишком большим количеством объектов.</li>
                    <li>Не смешивай слишком много стилей.</li>
                    <li>Не используй длинные title / hook.</li>
                    <li>Не меняй FORMAT внутри одного сценария.</li>
                    <li>Не меняй REFS между сценами без причины.</li>
                  </ul>
                </section>
                <section className="clipSB_guideCard clipSB_guideCardCombo">
                  <h4>QUICK RECIPES</h4>
                  <ul>
                    <li><b>YouTube / clickable</b> — <code>mode: clip • format: 16:9 • focus: character • text: short hook • style: high contrast</code></li>
                    <li><b>Cinematic</b> — <code>mode: kino • format: 16:9 • focus: environment + character • style: film</code></li>
                    <li><b>TikTok / vertical</b> — <code>mode: clip • format: 9:16 • focus: emotion • text: minimal • style: dynamic / clear</code></li>
                  </ul>
                </section>
                <section className="clipSB_guideCard clipSB_guideCardCombo">
                  <h4>Как комбинировать</h4>
                  <ul>
                    <li><code>scenario + music_plus_text + strong TEXT</code></li>
                    <li><code>kino + lyrics_music + refs</code></li>
                    <li><code>reklama + message in TEXT</code></li>
                    <li><code>clip + music_only + strong visual refs</code></li>
                    <li><code>scenario + speech_narrative + transcript + infrastructure refs</code></li>
                  </ul>
                </section>
              </div>
            </div>
          ) : null}
        </div>
      </div>

      
      {scenarioEditor.open ? (
        <div className="clipSB_scenarioOverlay" onClick={() => {
          setScenarioEditor((s) => ({ ...s, open: false }));
          closeLightbox();
        }}>
          <div className="clipSB_scenarioPanel" onClick={(e) => e.stopPropagation()}>
            <div className="clipSB_scenarioHeader">
              <div className="clipSB_scenarioTitle">SCENARIO</div>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <div className="clipSB_scenarioMeta">
                  {scenarioScenes.length} сцен
                </div>
                <button className="clipSB_iconBtn" onClick={() => {
                  setScenarioEditor((s) => ({ ...s, open: false }));
                  closeLightbox();
                }} aria-label="Закрыть">
                  ✕
                </button>
              </div>
            </div>

            <div className="clipSB_scenarioBody">
              <div className="clipSB_scenarioList">
                {scenarioScenes.map((s, i) => {
                  const previousScene = i > 0 ? scenarioScenes[i - 1] : null;
                  const sceneThumb = getSceneListThumb(s, previousScene);
                  const sceneAudioSliceUrl = resolveAssetUrl(s.audioSliceUrl);
                  const sceneAudioSliceStatus = String(s.audioSliceStatus || (s.audioSliceUrl ? "ready" : "")).trim();
                  const sceneAudioSliceError = String(s.audioSliceError || s.audioSliceLoadError || "").trim();
                  return (
                    <div
                      key={getScenarioSceneStableKey(s, i)}
                      ref={(node) => {
                        if (node) scenarioItemRefs.current.set(i, node);
                        else scenarioItemRefs.current.delete(i);
                      }}
                      className={"clipSB_scenarioItem"
                        + (i === scenarioSelectedIndex ? " isActive" : "")}
                      role="button"
                      tabIndex={0}
                      onClick={() => setScenarioEditor((x) => ({ ...x, selected: i, selectedSceneId: String(s?.sceneId || "").trim() }))}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setScenarioEditor((x) => ({ ...x, selected: i, selectedSceneId: String(s?.sceneId || "").trim() }));
                        }
                      }}
                    >
                      <div className="clipSB_scenarioItemInner">
                        {sceneThumb ? (
                          <img
                            src={sceneThumb}
                            alt="scene"
                            className="clipSB_scenarioThumb"
                            onClick={(e) => {
                              e.preventDefault();
                              e.stopPropagation();
                              openLightbox(sceneThumb, e.currentTarget.getBoundingClientRect());
                            }}
                          />
                        ) : (
                          <div className="clipSB_scenarioThumb clipSB_scenarioThumbPlaceholder" />
                        )}
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div className="clipSB_scenarioItemTop">
                            <div className="clipSB_scenarioItemTime">
                              {fmtTime(s.start)} → {fmtTime(s.end)}
                            </div>
                            <div className="clipSB_scenarioTags">
                              <div className="clipSB_tag">{getSceneTypeBadge(resolveSceneTransitionType(s))}</div>
                              {sceneThumb ? <div className="clipSB_tag">IMG</div> : null}
                              {s.videoUrl ? <div className="clipSB_tag clipSB_tagDone">VIDEO ✓</div> : null}
                              {sceneAudioSliceUrl ? <div className="clipSB_tag clipSB_tagOk">AUDIO ✓</div> : null}
                              {i === recommendedNextSceneIndex ? <div className="clipSB_tag clipSB_tagNext">NEXT</div> : null}
                              {isLipSyncScene(s) ? <div className="clipSB_tag">LS</div> : null}
                            </div>
                          </div>
                          <div className="clipSB_scenarioItemText">{getSceneUiDescription(s).slice(0, 90)}</div>
                          <div className="clipSB_scenarioItemActions">
                            <button
                              type="button"
                              className="clipSB_btn clipSB_btnSecondary clipSB_scenarioItemActionBtn"
                              onClick={(e) => {
                                e.preventDefault();
                                e.stopPropagation();
                                void handleScenarioTakeAudioByIndex(i);
                              }}
                              disabled={!globalAudioUrlRaw || sceneAudioSliceStatus === "loading"}
                            >
                              {sceneAudioSliceStatus === "loading" ? "Делаю..." : (sceneAudioSliceUrl ? "Обновить аудио" : "Взять аудио")}
                            </button>
                          </div>
                          {sceneAudioSliceUrl ? (
                            <audio
                              key={`scene-card-audio-${String(s.sceneId || i)}-${String(sceneAudioSliceUrl || "")}`}
                              className="clipSB_audioPlayer clipSB_scenarioCardAudio"
                              controls
                              preload="metadata"
                              src={sceneAudioSliceUrl}
                              onClick={(e) => e.stopPropagation()}
                            />
                          ) : null}
                          {sceneAudioSliceStatus === "loading" ? <div className="clipSB_hint">Извлекаю аудио по timeline сцены…</div> : null}
                          {sceneAudioSliceError ? <div className="clipSB_hint" style={{ color: "#ff8a8a" }}>{sceneAudioSliceError}</div> : null}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              <div className="clipSB_scenarioEdit">
                {scenarioSelected ? (
                  <>
                    <div className="clipSB_scenarioEditRow">
                      <div className="clipSB_hint">Тайм</div>
                      <div className="clipSB_kv">
                        <span>{fmtTime(scenarioSelected.start)}</span>
                        <span>—</span>
                        <span>{fmtTime(scenarioSelected.end)}</span>
                        <span style={{ opacity: 0.7 }}>({Math.max(0, (scenarioSelected.end || 0) - (scenarioSelected.start || 0)).toFixed(1)}s)</span>
                      </div>
                    </div>

                    <div className="clipSB_scenarioEditRow">
                      <label className="clipSB_check" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                        <input
                          type="checkbox"
                          checked={scenarioSelectedIsLipSync}
                          onChange={(e) => updateScenarioScene(scenarioEditor.selected, {
                            lipSync: !!e.target.checked,
                            isLipSync: !!e.target.checked,
                            renderMode: e.target.checked ? "avatar_lipsync" : "standard_video",
                          })}
                        />
                        <span>Этот кадр под липсинк (рот/лицо видно)</span>
                      </label>
                    </div>

                    {scenarioSelectedIsLipSync ? (
                      <div className="clipSB_scenarioEditRow">
                        <div className="clipSB_hint">Текст для губ (коротко)</div>
                        <input
                          className="clipSB_input"
                          value={scenarioSelected.lipSyncText || ""}
                          onChange={(e) => updateScenarioScene(scenarioEditor.selected, { lipSyncText: e.target.value })}
                          placeholder="фраза/строка припева…"
                        />
                      </div>
                    ) : null}

                    <div className="clipSB_scenarioEditRow">
                      <div className="clipSB_hint">Описание сцены</div>
                      <textarea
                        className="clipSB_textarea"
                        rows={6}
                        value={scenarioSelected.sceneText || ""}
                        onChange={(e) => updateScenarioScene(scenarioEditor.selected, { sceneText: e.target.value })}
                      />
                    </div>

                    {scenarioSelectedTransitionType === "continuous" ? (
                      <>
                        <div className="clipSB_scenarioEditRow">
                          <div className="clipSB_hint">Prompt (Start Frame)</div>
                          <textarea
                            className="clipSB_textarea"
                            rows={4}
                            value={scenarioSelected.startFramePrompt || ""}
                            onChange={(e) => updateScenarioScene(scenarioEditor.selected, { startFramePrompt: e.target.value })}
                          />
                        </div>

                        <div className="clipSB_scenarioEditRow">
                          <div className="clipSB_hint">Prompt (End Frame)</div>
                          <textarea
                            className="clipSB_textarea"
                            rows={4}
                            value={scenarioSelected.endFramePrompt || ""}
                            onChange={(e) => updateScenarioScene(scenarioEditor.selected, { endFramePrompt: e.target.value })}
                          />
                        </div>

                        <div className="clipSB_scenarioEditRow">
                          <div className="clipSB_hint">Prompt (Transition Video)</div>
                          <textarea
                            className="clipSB_textarea"
                            rows={4}
                            value={scenarioSelected.transitionActionPrompt ?? scenarioSelected.videoPrompt ?? ""}
                            onChange={(e) => updateScenarioScene(scenarioEditor.selected, { transitionActionPrompt: e.target.value, videoPrompt: e.target.value })}
                          />
                        </div>

                        <div className="clipSB_scenarioEditRow">
                          <div className="clipSB_hint" style={{ marginBottom: 6 }}>Изображения сцены</div>
                          <div className="clipSB_aspectPills" style={{ marginBottom: 8 }}>
                            {SCENE_IMAGE_FORMATS.map((format) => (
                              <button
                                key={format}
                                type="button"
                                className={`clipSB_aspectPill${scenarioSelectedImageFormat === format ? " isActive" : ""}`}
                                onClick={() => updateScenarioScene(scenarioEditor.selected, { imageFormat: format })}
                              >
                                {format}
                              </button>
                            ))}
                          </div>

                          <div className="clipSB_scenarioEditRow" style={{ marginBottom: 8 }}>
                            <label className="clipSB_check" style={{ display: "flex", gap: 10, alignItems: "center" }}>
                              <input
                                type="checkbox"
                                checked={!!scenarioSelected.inheritPreviousEndAsStart}
                                onChange={(e) => updateScenarioScene(scenarioEditor.selected, {
                                  inheritPreviousEndAsStart: !!e.target.checked,
                                  startFrameSource: e.target.checked ? "previous_end" : "manual",
                                })}
                                disabled={!scenarioSelectedCanInheritPreviousEnd}
                              />
                              <span>Взять END предыдущей сцены как START этой</span>
                            </label>
                          </div>

                          <div className="clipSB_hint" style={{ marginBottom: 6 }}>START FRAME IMAGE</div>
                          <div className="clipSB_hint" style={{ marginBottom: 6 }}>
                            Источник: {scenarioSelectedStartImageSource === "previous_end" ? "предыдущий END" : scenarioSelectedStartImageSource === "manual" ? "manual" : "none"}
                          </div>
                          <div className="clipSB_scenarioPreviewWrap">
                            {scenarioSelectedResolvedStartPreviewSrc ? (
                              <img
                                src={scenarioSelectedResolvedStartPreviewSrc}
                                alt="start frame preview"
                                className="clipSB_scenarioPreview"
                                onClick={(e) => openLightbox(scenarioSelectedResolvedStartPreviewSrc, e.currentTarget.getBoundingClientRect())}
                              />
                            ) : (
                              <div className="clipSB_scenarioPreview clipSB_scenarioPreviewPlaceholder">Start preview отсутствует</div>
                            )}
                          </div>
                          <div style={{ display: "flex", gap: 8, marginTop: 8, marginBottom: 10 }}>
                            <button
                              className="clipSB_btn clipSB_btnSecondary"
                              onClick={() => handleGenerateScenarioImage("start")}
                              disabled={scenarioImageLoading || !!scenarioSelected.inheritPreviousEndAsStart}
                            >
                              {scenarioImageLoading ? "Генерация..." : "Сгенерировать start"}
                            </button>
                            <button
                              className="clipSB_btn clipSB_btnSecondary"
                              onClick={() => handleClearScenarioImage("start")}
                              disabled={!!scenarioSelected.inheritPreviousEndAsStart}
                            >
                              Очистить start
                            </button>
                          </div>
                          {scenarioSelected.inheritPreviousEndAsStart ? (
                            <div className="clipSB_hint" style={{ marginBottom: 10 }}>START берётся из предыдущей сцены</div>
                          ) : null}

                          <div className="clipSB_hint" style={{ marginBottom: 6 }}>END FRAME IMAGE</div>
                          <div className="clipSB_scenarioPreviewWrap">
                            {scenarioSelectedResolvedEndPreviewSrc ? (
                              <img
                                src={scenarioSelectedResolvedEndPreviewSrc}
                                alt="end frame preview"
                                className="clipSB_scenarioPreview"
                                onClick={(e) => openLightbox(scenarioSelectedResolvedEndPreviewSrc, e.currentTarget.getBoundingClientRect())}
                              />
                            ) : (
                              <div className="clipSB_scenarioPreview clipSB_scenarioPreviewPlaceholder">End preview отсутствует</div>
                            )}
                          </div>
                          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                            <button className="clipSB_btn clipSB_btnSecondary" onClick={() => handleGenerateScenarioImage("end")} disabled={scenarioImageLoading}>
                              {scenarioImageLoading ? "Генерация..." : "Сгенерировать end"}
                            </button>
                            <button className="clipSB_btn clipSB_btnSecondary" onClick={() => handleClearScenarioImage("end")}>Очистить end</button>
                            {scenarioCanShowAddToVideoButton ? (
                              <button className="clipSB_btn clipSB_btnSecondary" onClick={handleScenarioAddToVideo}>Добавить в Видео</button>
                            ) : null}
                          </div>
                          {scenarioImageError ? <div className="clipSB_hint" style={{ color: "#ff8a8a", marginTop: 6 }}>{scenarioImageError}</div> : null}
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="clipSB_scenarioEditRow">
                          <div className="clipSB_hint" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                            <span>Prompt (Frame)</span>
                            {scenarioSelectedTransitionType === "hard_cut" ? <span className="clipSB_tag">Новый блок</span> : null}
                          </div>
                          <textarea
                            className="clipSB_textarea"
                            rows={5}
                            value={scenarioSelected.framePrompt ?? scenarioSelected.imagePrompt ?? ""}
                            onChange={(e) => updateScenarioScene(scenarioEditor.selected, { framePrompt: e.target.value, imagePrompt: e.target.value })}
                          />
                        </div>

                        <div className="clipSB_scenarioEditRow">
                          <div className="clipSB_hint" style={{ marginBottom: 6 }}>Изображение сцены</div>
                          <div className="clipSB_aspectPills" style={{ marginBottom: 8 }}>
                            {SCENE_IMAGE_FORMATS.map((format) => (
                              <button
                                key={format}
                                type="button"
                                className={`clipSB_aspectPill${scenarioSelectedImageFormat === format ? " isActive" : ""}`}
                                onClick={() => updateScenarioScene(scenarioEditor.selected, { imageFormat: format })}
                              >
                                {format}
                              </button>
                            ))}
                          </div>
                          <div className="clipSB_scenarioPreviewWrap">
                            {scenarioSelectedResolvedPreviewSrc ? (
                              <img
                                src={scenarioSelectedResolvedPreviewSrc}
                                alt="scene preview"
                                className="clipSB_scenarioPreview"
                                onClick={(e) => openLightbox(scenarioSelectedResolvedPreviewSrc, e.currentTarget.getBoundingClientRect())}
                              />
                            ) : (
                              <div className="clipSB_scenarioPreview clipSB_scenarioPreviewPlaceholder">Превью отсутствует</div>
                            )}
                          </div>
                          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
                            <button className="clipSB_btn clipSB_btnSecondary" onClick={() => handleGenerateScenarioImage("single")} disabled={scenarioImageLoading}>
                              {scenarioImageLoading ? "Генерация..." : "Сгенерировать изображение"}
                            </button>
                            <button className="clipSB_btn clipSB_btnSecondary" onClick={() => handleClearScenarioImage("single")}>Очистить изображение</button>
                            {scenarioCanShowAddToVideoButton ? (
                              <button className="clipSB_btn clipSB_btnSecondary" onClick={handleScenarioAddToVideo}>Добавить в Видео</button>
                            ) : null}
                          </div>
                          {scenarioImageError ? <div className="clipSB_hint" style={{ color: "#ff8a8a", marginTop: 6 }}>{scenarioImageError}</div> : null}
                        </div>

                        <div className="clipSB_scenarioEditRow">
                          <div className="clipSB_hint">Prompt (Video)</div>
                          <textarea
                            className="clipSB_textarea"
                            rows={4}
                            value={scenarioSelected.videoPrompt || ""}
                            onChange={(e) => updateScenarioScene(scenarioEditor.selected, { videoPrompt: e.target.value })}
                          />
                        </div>
                      </>
                    )}

                    <div className="clipSB_scenarioEditRow">
                      <div className="clipSB_hint">Scene decision debug</div>
                      <div className="clipSB_kv" style={{ display: "grid", gridTemplateColumns: "160px 1fr", rowGap: 6, columnGap: 8, alignItems: "start" }}>
                        <span>audioType</span><span>{String(scenarioSelected.audioType || "") || "—"}</span>
                        <span>sceneType</span><span>{String(scenarioSelected.sceneType || "") || "—"}</span>
                        <span>transitionType</span><span>{String(scenarioSelected.transitionType || scenarioSelectedTransitionType || "") || "—"}</span>
                        <span>cameraType</span><span>{String(scenarioSelected.cameraType || "") || "—"}</span>
                        <span>cameraMovement</span><span>{String(scenarioSelected.cameraMovement || "") || "—"}</span>
                        <span>cameraPosition</span><span>{String(scenarioSelected.cameraPosition || "") || "—"}</span>
                        <span>visualMode</span><span>{String(scenarioSelected.visualMode || "") || "—"}</span>
                        <span>humanAnchorType</span><span>{String(scenarioSelected.humanAnchorType || "") || "—"}</span>
                        <span>hasVocals</span><span>{String(!!scenarioSelected.hasVocals)}</span>
                        <span>isLipSync</span><span>{String(!!scenarioSelected.isLipSync || !!scenarioSelected.lipSync)}</span>
                        <span>lyricFragment</span><span>{String(scenarioSelected.lyricFragment || "") || "—"}</span>
                        <span>timingReason</span><span>{String(scenarioSelected.timingReason || "") || "—"}</span>
                        <span>beatAnchor</span><span>{String(scenarioSelected.beatAnchor || "") || "—"}</span>
                        <span>performanceType</span><span>{String(scenarioSelected.performanceType || "") || "—"}</span>
                        <span>shotType</span><span>{String(scenarioSelected.shotType || "") || "—"}</span>
                        <span>roleInfluenceApplied</span><span>{String(!!scenarioSelected.roleInfluenceApplied)}</span>
                        <span>roleInfluenceReason</span><span>{String(scenarioSelected.roleInfluenceReason || "") || "—"}</span>
                        <span>sceneRoleDynamics</span><span>{String(scenarioSelected.sceneRoleDynamics || "") || "—"}</span>
                        <span>multiCharacterIdentityLock</span><span>{String(!!scenarioSelected.multiCharacterIdentityLock)}</span>
                        <span>distinctCharacterSeparation</span><span>{String(!!scenarioSelected.distinctCharacterSeparation)}</span>
                        <span>appearanceDriftRisk</span><span>{String(scenarioSelected.appearanceDriftRisk || "") || "—"}</span>
                        <span>previousSceneImageSource</span><span>{scenarioPreviousSceneImageSource}</span>
                        <span>inheritPreviousEndAsStart</span><span>{String(!!scenarioSelected.inheritPreviousEndAsStart)}</span>
                        <span>startFrameSource</span><span>{scenarioSelectedStartImageSource}</span>
                      </div>
                    </div>

                    <div className="clipSB_scenarioEditRow clipSB_audioDebugBlock">
                      <div className="clipSB_audioDebugTitle">AUDIO SLICE DEBUG</div>
                      <div className="clipSB_audioDebugSceneLabel">
                        Scene {scenarioSelectedIndexLabel} · {fmtTimeWithMs(scenarioSelectedT0)} → {fmtTimeWithMs(scenarioSelectedT1)}
                      </div>

                      <div className="clipSB_audioDebugSection">
                        <div className="clipSB_hint" style={{ marginBottom: 6 }}>Полный трек (original audioUrl)</div>
                        {globalAudioUrlResolved ? (
                          <audio
                            key={`full-track-${globalAudioUrlResolved}`}
                            className="clipSB_audioPlayer"
                            controls
                            preload="metadata"
                            src={globalAudioUrlResolved}
                          />
                        ) : (
                          <div className="clipSB_hint" style={{ color: "#ffb4b4" }}>Полный трек не найден: загрузите аудио в Audio node.</div>
                        )}
                        <div className="clipSB_audioDebugUrl">{globalAudioUrlResolved || "—"}</div>
                      </div>

                      <div className="clipSB_audioDebugSection">
                        <div className="clipSB_hint" style={{ marginBottom: 6 }}>Срез по сцене (audioSliceUrl)</div>
                        {scenarioSelectedAudioSliceUrl ? (
                          <audio
                            key={`slice-${String(scenarioSelected.sceneId || scenarioEditor.selected)}-${String(scenarioSelectedAudioSliceUrl || "")}`}
                            className="clipSB_audioPlayer"
                            controls
                            preload="metadata"
                            src={scenarioSelectedAudioSliceUrl}
                            onLoadedMetadata={handleScenarioSliceLoadedMetadata}
                            onError={handleScenarioSliceAudioError}
                          />
                        ) : (
                          <div className="clipSB_hint">
                            {scenarioSelectedIsLipSync
                              ? "Для lipSync audioSlice подготовится автоматически при «Сделать видео». Если авто-извлечение не удастся, появится ошибка."
                              : "Срез ещё не создан. Нажмите «Взять аудио»."}
                          </div>
                        )}
                        <div className="clipSB_audioDebugUrl">{scenarioSelectedAudioSliceUrl || "—"}</div>
                      </div>

                      <div className="clipSB_audioDebugGrid">
                        <div className="clipSB_videoKv"><span>t0</span><span>{fmtSecAndMs(Number(scenarioSelected.audioSliceT0 ?? scenarioSelectedT0))}</span></div>
                        <div className="clipSB_videoKv"><span>t1</span><span>{fmtSecAndMs(Number(scenarioSelected.audioSliceT1 ?? scenarioSelectedT1))}</span></div>
                        <div className="clipSB_videoKv"><span>expected duration</span><span>{fmtSecAndMs(scenarioSelectedExpectedSliceSec)}</span></div>
                        <div className="clipSB_videoKv"><span>backend duration</span><span>{fmtSecAndMs(scenarioSelected.audioSliceBackendDurationSec)}</span></div>
                        <div className="clipSB_videoKv"><span>actual duration</span><span>{fmtSecAndMs(scenarioSelected.audioSliceActualDurationSec)}</span></div>
                      </div>

                      {scenarioSelectedAudioSliceError ? (
                        <div className="clipSB_hint" style={{ color: "#ff8a8a", marginTop: 6 }}>{scenarioSelectedAudioSliceError}</div>
                      ) : null}
                    </div>

                    {scenarioSelectedVideoPanelActivated ? (
                      <div ref={scenarioVideoCardRef} className={`clipSB_scenarioEditRow clipSB_videoBlock${(scenarioVideoFocusPulse || scenarioVideoOpen) ? " clipSB_videoBlockPulse" : ""}`}>
                        <div className="clipSB_hint" style={{ marginBottom: 8 }}>Видео сцены</div>
                        {scenarioSelectedTransitionType === "continuous" ? (
                          <div className="clipSB_videoPipelineRow">
                            <div className="clipSB_videoFrameSmall">
                              <div className="clipSB_hint">START</div>
                              {scenarioSelectedEffectiveStartImageUrl ? (
                                <img
                                  src={scenarioSelectedEffectiveStartImageUrl}
                                  className="clipSB_videoFrameImg"
                                  alt="start frame"
                                  onClick={(e) => openLightbox(scenarioSelectedEffectiveStartImageUrl, e.currentTarget.getBoundingClientRect())}
                                />
                              ) : (
                                <div className="clipSB_videoFramePlaceholder">START</div>
                              )}
                            </div>

                            <div className="clipSB_videoArrow">→</div>

                            <div className="clipSB_videoFrameSmall">
                              <div className="clipSB_hint">END</div>
                              {scenarioSelectedEndImageUrl ? (
                                <img
                                  src={scenarioSelectedEndImageUrl}
                                  className="clipSB_videoFrameImg"
                                  alt="end frame"
                                  onClick={(e) => openLightbox(scenarioSelectedEndImageUrl, e.currentTarget.getBoundingClientRect())}
                                />
                              ) : (
                                <div className="clipSB_videoFramePlaceholder">END</div>
                              )}
                            </div>

                            <div className="clipSB_videoArrow">→</div>

                            <div className="clipSB_videoResultBox">
                              <div className="clipSB_videoPreviewWrap">
                                {scenarioSelected.videoUrl ? (
                                  <video
                                    key={String(scenarioSelected.sceneId || scenarioEditor.selected) + ":" + String(scenarioSelected.videoUrl || "")}
                                    className="clipSB_videoPlayer"
                                    controls
                                    playsInline
                                    preload="metadata"
                                    src={scenarioSelected.videoUrl}
                                    poster={getSceneVideoPoster(scenarioSelected, scenarioPreviousScene)}
                                  />
                                ) : scenarioSelectedVideoSourceImageUrl ? (
                                  <img
                                    className="clipSB_videoPoster"
                                    src={scenarioSelectedVideoSourceImageUrl}
                                    alt="video preview"
                                  />
                                ) : (
                                  <div className="clipSB_videoFramePlaceholder">RESULT</div>
                                )}

                                {scenarioShowingGeneratingOverlay ? (
                                  <div className="clipSB_videoOverlay">
                                    <span className="clipSB_videoLoadingPulse">Генерация видео...</span>
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          </div>
                        ) : (
                          <div className="clipSB_videoPreviewWrap">
                            {scenarioSelected.videoUrl ? (
                              <video
                                key={String(scenarioSelected.sceneId || scenarioEditor.selected) + ":" + String(scenarioSelected.videoUrl || "")}
                                className="clipSB_videoPlayer"
                                controls
                                playsInline
                                preload="metadata"
                                src={scenarioSelected.videoUrl}
                                poster={getSceneVideoPoster(scenarioSelected, scenarioPreviousScene)}
                              />
                            ) : scenarioSelectedVideoSourceImageUrl ? (
                              <img className="clipSB_videoPoster" src={scenarioSelectedVideoSourceImageUrl} alt="poster" />
                            ) : (
                              <div className="clipSB_videoFramePlaceholder">PREVIEW</div>
                            )}

                            {scenarioShowingGeneratingOverlay ? (
                              <div className="clipSB_videoOverlay">
                                <span className="clipSB_videoLoadingPulse">Генерация видео...</span>
                              </div>
                            ) : null}
                          </div>
                        )}
                        <details className="clipSB_videoDetails">
                          <summary>Детали</summary>
                          <div className="clipSB_videoKv"><span>imageUrl</span><span>{scenarioSelected.imageUrl || "—"}</span></div>
                          <div className="clipSB_videoKv"><span>startImageUrl</span><span>{scenarioSelectedEffectiveStartImageUrl || "—"}</span></div>
                          <div className="clipSB_videoKv"><span>endImageUrl</span><span>{scenarioSelectedEndImageUrl || "—"}</span></div>
                          <div className="clipSB_videoKv"><span>videoSourceImageUrl</span><span>{scenarioSelected.videoSourceImageUrl || "—"}</span></div>
                          <div className="clipSB_videoKv"><span>transitionActionPrompt</span><span>{scenarioSelected.transitionActionPrompt || scenarioSelected.videoPrompt || "—"}</span></div>
                          <div className="clipSB_videoKv"><span>audioSliceUrl</span><span>{scenarioSelected.audioSliceUrl || "—"}</span></div>
                          <div className="clipSB_videoKv"><span>videoUrl</span><span>{scenarioSelected.videoUrl || "—"}</span></div>
                        </details>
                        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                          <button className="clipSB_btn clipSB_btnSecondary" onClick={handleScenarioVideoTakeAudio} disabled={scenarioAudioSliceLoading || scenarioSelectedAudioSliceStatus === "loading" || !globalAudioUrlRaw}>
                            {scenarioAudioSliceLoading ? "Делаю..." : (scenarioSelected?.audioSliceUrl ? "Обновить аудио" : "Взять аудио")}
                          </button>
                          <button
                            className="clipSB_btn clipSB_btnSecondary"
                            onClick={handleScenarioGenerateVideo}
                            disabled={scenarioVideoLoading || !scenarioHasImageForVideo}
                            title={scenarioSelectedIsLipSync ? "Для lipSync audioSlice будет автоматически подготовлен перед генерацией видео." : ""}
                          >
                            {scenarioVideoLoading ? "Делаю..." : "Сделать видео"}
                          </button>
                          <button className="clipSB_btn clipSB_btnSecondary" onClick={handleScenarioClearVideo} disabled={scenarioVideoLoading}>
                            Удалить видео
                          </button>
                        </div>
                        {(scenarioSelected?.videoError || scenarioVideoError) ? <div className="clipSB_hint" style={{ color: "#ff8a8a", marginTop: 6 }}>{scenarioSelected?.videoError || scenarioVideoError}</div> : null}
                      </div>
                    ) : null}
                  </>
                ) : (
                  <div className="clipSB_empty">Нет выбранной сцены</div>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      <ScenarioStoryboardEditor
        open={isScenarioStoryboardOpen}
        nodeId={activeScenarioStoryboardNode?.id || null}
        storyboardRevision={activeScenarioStoryboardNode?.data?.storyboardRevision || ""}
        storyboardSignature={activeScenarioStoryboardNode?.data?.storyboardSignature || ""}
        scenes={activeScenarioStoryboardNode?.data?.scenes || []}
        sceneGeneration={activeScenarioStoryboardNode?.data?.sceneGeneration || {}}
        audioData={activeScenarioAudioData}
        scenarioMode={activeScenarioStoryboardNode?.data?.scenarioMode || ""}
        masterAudioUrl={activeScenarioMasterAudioUrl}
        scenarioNodeAudioUrl={activeScenarioStoryboardNode?.data?.audioUrl || ""}
        scenarioNodeMasterAudioUrl={activeScenarioStoryboardNode?.data?.masterAudioUrl || ""}
        connectedSourceAudioUrl={activeScenarioMasterAudioResolution?.connectedSourceAudioUrl || ""}
        globalAudioUrl={activeScenarioMasterAudioResolution?.globalAudioUrl || ""}
        musicUrl={activeScenarioMusicUrl}
        onClose={() => setIsScenarioStoryboardOpen(false)}
        onUpdateScene={activeScenarioStoryboardNode?.data?.onScenarioSceneUpdate}
        onGenerateScene={activeScenarioStoryboardNode?.data?.onScenarioSceneGenerate}
        onUpdateMusic={activeScenarioStoryboardNode?.data?.onScenarioMusicUpdate}
        onGenerateMusic={activeScenarioStoryboardNode?.data?.onScenarioMusicGenerate}
        onExtractSceneAudio={handleScenarioEditorExtractSceneAudio}
      />

      {comfyEditor.open ? (
        <div className="clipSB_scenarioOverlay" onClick={() => setComfyEditor((state) => ({ ...state, open: false }))}>
          <div className="clipSB_scenarioPanel clipSB_comfyPanel" onClick={(e) => e.stopPropagation()}>
            <div className="clipSB_scenarioHeader">
              <div>
                <div className="clipSB_scenarioTitle">COMFY STORYBOARD</div>
                <div className="clipSB_scenarioMeta">{comfyModeMeta.labelRu} • {comfyStyleMeta.labelRu} • сцен: {comfyScenes.length}</div>
              </div>
              <button className="clipSB_iconBtn" onClick={() => setComfyEditor((state) => ({ ...state, open: false }))}>×</button>
            </div>

            <div className="clipSB_scenarioBody clipSB_comfyBody">
              <div className="clipSB_scenarioList clipSB_comfySceneList clipSB_comfySidebar">
                {!comfyScenes.length ? (
                  <div className="clipSB_empty">Нет сцен. Нажми «Разобрать» в COMFY BRAIN.</div>
                ) : comfyScenes.map((scene, index) => {
                  const isActive = comfySafeIndex === index;
                  const hasImage = !!String(scene?.imageUrl || '').trim();
                  const hasVideo = !!String(scene?.videoUrl || '').trim();
                  const hasAudioSlice = !!String(scene?.audioSliceUrl || '').trim();
                  const start = Number(scene?.startSec);
                  const end = Number(scene?.endSec);
                  const dur = Number(scene?.durationSec);
                  const timing = Number.isFinite(start) && Number.isFinite(end)
                    ? `${start.toFixed(1)}–${end.toFixed(1)}s`
                    : Number.isFinite(dur)
                      ? `${dur.toFixed(1)}s`
                      : '—';
                  return (
                    <button
                      key={scene.sceneId || `comfy-${index}`}
                      className={`clipSB_scenarioItem ${isActive ? 'isActive' : ''}`}
                      onClick={() => setComfyEditor((state) => ({ ...state, selected: index }))}
                    >
                      <div className="clipSB_scenarioItemTop">
                        <div className="clipSB_comfySceneId">{scene.sceneId || `scene ${index + 1}`}</div>
                        <div className="clipSB_comfyReadyIcons">
                          {hasImage ? <span className="clipSB_tag clipSB_tagOk">image-ready</span> : null}
                          {hasAudioSlice ? <span className="clipSB_tag clipSB_tagOk">audio-ready</span> : null}
                          {!hasVideo && String(scene?.videoStatus || '').trim() === 'queued' ? <span className="clipSB_tag">video-queued</span> : null}
                          {!hasVideo && String(scene?.videoStatus || '').trim() === 'running' ? <span className="clipSB_tag">video-running</span> : null}
                          {hasVideo || String(scene?.videoStatus || '').trim() === 'done' ? <span className="clipSB_tag clipSB_tagOk">video-ready</span> : null}
                          {(String(scene?.videoStatus || '').trim() === 'error' || String(scene?.videoStatus || '').trim() === 'not_found') ? <span className="clipSB_tag clipSB_tagWarn">video-error</span> : null}
                        </div>
                      </div>
                      <div className="clipSB_comfySceneTitle">{scene.title || `Сцена ${index + 1}`}</div>
                      <div className="clipSB_comfySceneTiming">{timing}</div>
                    </button>
                  );
                })}
              </div>

              <div className="clipSB_scenarioEdit clipSB_comfyEditor clipSB_comfyWorkspace">
                {!comfySelectedScene ? (
                  <div className="clipSB_empty">Выбери сцену слева.</div>
                ) : (
                  <>
                    <div className="clipSB_comfySection clipSB_comfyTopSection">
                      <div className="clipSB_comfyHero">
                        <div className="clipSB_comfyHeroPrimary">
                          <div className="clipSB_comfyHeroEyebrow">{comfySelectedScene.sceneId || `scene ${comfySafeIndex + 1}`}</div>
                          <div className="clipSB_comfyHeroTitle">{comfySelectedScene.title || '—'}</div>
                          <div className="clipSB_comfyHeroMeta">
                            <span>{Number.isFinite(Number(comfySelectedScene.startSec)) && Number.isFinite(Number(comfySelectedScene.endSec)) ? `${Number(comfySelectedScene.startSec).toFixed(1)}–${Number(comfySelectedScene.endSec).toFixed(1)}s` : `${Number(comfySelectedScene.durationSec || 0).toFixed(1)}s`}</span>
                            {String(comfySelectedScene.videoStatus || '').trim() ? <span>• status: {String(comfySelectedScene.videoStatus || '').trim()}</span> : null}
                          </div>
                        </div>
                        <div className="clipSB_comfyHeroBadges">
                          {String(comfySelectedScene.imageUrl || '').trim() ? <span className="clipSB_tag clipSB_tagOk">image-ready</span> : null}
                          {String(comfySelectedScene.audioSliceUrl || '').trim() ? <span className="clipSB_tag clipSB_tagOk">audio-ready</span> : null}
                          {!String(comfySelectedScene.videoUrl || '').trim() && String(comfySelectedScene.videoStatus || '').trim() === 'queued' ? <span className="clipSB_tag">video-queued</span> : null}
                          {!String(comfySelectedScene.videoUrl || '').trim() && String(comfySelectedScene.videoStatus || '').trim() === 'running' ? <span className="clipSB_tag">video-running</span> : null}
                          {String(comfySelectedScene.videoUrl || '').trim() || String(comfySelectedScene.videoStatus || '').trim() === 'done' ? <span className="clipSB_tag clipSB_tagOk">video-ready</span> : null}
                          {(String(comfySelectedScene.videoStatus || '').trim() === 'error' || String(comfySelectedScene.videoStatus || '').trim() === 'not_found') ? <span className="clipSB_tag clipSB_tagWarn">video-error</span> : null}
                        </div>
                      </div>
                    </div>

                    <ComfyCollapsibleSection title="Scene Info">
                      <div className="clipSB_comfyInfoGrid">
                        <div className="clipSB_comfyKv"><span>Planner</span><strong>{String(comfyNode?.data?.plannerMode || comfyNode?.data?.plannerMeta?.plannerMode || 'legacy')}</strong></div>
                        <div className="clipSB_comfyKv"><span>Тип сцены</span><strong>{comfySelectedScene.sceneType || '—'}</strong></div>
                        <div className="clipSB_comfyKv"><span>Рендер-модель</span><strong>{comfySelectedScene.futureRenderModel || '—'}</strong></div>
                        <div className="clipSB_comfyKv"><span>Anchor</span><strong>{comfySelectedScene.anchorType || '—'}</strong></div>
                        <div className="clipSB_comfyKv"><span>Primary role</span><strong>{comfySelectedScene.primaryRole || '—'}</strong></div>
                        <div className="clipSB_comfyKv clipSB_comfyKvWide"><span>Refs used</span><strong>{Array.isArray(comfySelectedScene.refsUsed) && comfySelectedScene.refsUsed.length ? comfySelectedScene.refsUsed.join(', ') : '—'}</strong></div>
                        <div className="clipSB_comfyKv clipSB_comfyKvWide"><span>Active refs</span><strong>{Array.isArray(comfySelectedScene.activeRefs) && comfySelectedScene.activeRefs.length ? comfySelectedScene.activeRefs.join(', ') : '—'}</strong></div>
                        <div className="clipSB_comfyKv clipSB_comfyKvWide"><span>Цель</span><strong>{comfySelectedScene.sceneGoal || comfySelectedScene.sceneNarrativeStep || '—'}</strong></div>
                      </div>
                      <div style={{ marginTop: 12 }}>
                        <div className="clipSB_comfyBlockTitle">Континуити</div>
                        <pre className="clipSB_comfyContinuity">{formatContinuityForDisplay(comfySelectedScene.continuity || comfyNode?.data?.plannerMeta?.globalContinuity || '—')}</pre>
                      </div>
                    </ComfyCollapsibleSection>

                    <ComfyCollapsibleSection title="World Bible">
                      <div className="clipSB_comfyBlockTitle">WORLD BIBLE</div>
                      <div className="clipSB_small">storyMode: {String(comfyNode?.data?.plannerMeta?.worldBible?.storyMode || comfyNode?.data?.plannerMeta?.audioStoryMode || '—')}</div>
                      <div className="clipSB_small">plannerMode: {String(comfyNode?.data?.plannerMeta?.plannerMode || comfyNode?.data?.plannerMode || 'legacy')}</div>
                      <div className="clipSB_small">visualStyle: {String(comfyNode?.data?.plannerMeta?.worldBible?.visualStyle || comfyNode?.data?.stylePreset || '—')}</div>
                      <div className="clipSB_small">lensFamily: {String(comfyNode?.data?.plannerMeta?.worldBible?.lensFamily || '—')}</div>
                      <div className="clipSB_small">lightingLogic: {String(comfyNode?.data?.plannerMeta?.worldBible?.lightingLogic || '—')}</div>
                      <div className="clipSB_small">productionFeel: {String(comfyNode?.data?.plannerMeta?.worldBible?.productionFeel || '—')}</div>
                      <div className="clipSB_small">colorWorld: {String(comfyNode?.data?.plannerMeta?.worldBible?.colorWorld || '—')}</div>
                      <div className="clipSB_small">cameraLanguage: {String(comfyNode?.data?.plannerMeta?.worldBible?.cameraLanguage || '—')}</div>
                      <div className="clipSB_small">emotionalArc: {String(comfyNode?.data?.plannerMeta?.worldBible?.emotionalArc || '—')}</div>
                      <div className="clipSB_small">storySource: {String(comfyNode?.data?.plannerMeta?.storySource || comfyNode?.data?.comfyDebug?.analysis?.storySource || '—')}</div>
                      <div className="clipSB_small">textSource: {String(comfyNode?.data?.plannerMeta?.textSource || comfyNode?.data?.comfyDebug?.analysis?.textSource || '—')}</div>
                      <div className="clipSB_small">exactLyricsAvailable: {String(Boolean(comfyNode?.data?.plannerMeta?.exactLyricsAvailable ?? comfyNode?.data?.comfyDebug?.analysis?.exactLyricsAvailable))}</div>
                      <div className="clipSB_small">transcriptAvailable: {String(Boolean(comfyNode?.data?.plannerMeta?.transcriptAvailable ?? comfyNode?.data?.comfyDebug?.analysis?.transcriptAvailable))}</div>
                      <div className="clipSB_small">usedSemanticFallback: {String(Boolean(comfyNode?.data?.plannerMeta?.usedSemanticFallback ?? comfyNode?.data?.comfyDebug?.analysis?.usedSemanticFallback))}</div>
                      <div className="clipSB_small">semanticHintCount: {String(comfyNode?.data?.plannerMeta?.semanticHintCount ?? comfyNode?.data?.comfyDebug?.analysis?.semanticHintCount ?? '—')}</div>
                      <div className="clipSB_small">audioSemanticSummary: {String(comfyNode?.data?.comfyDebug?.analysis?.audioSemanticSummary || '—')}</div>
                      <div className="clipSB_small">closeupSceneCount: {String(comfyNode?.data?.plannerMeta?.closeupSceneCount ?? comfyNode?.data?.comfyDebug?.analysis?.closeupSceneCount ?? '—')}</div>
                      <div className="clipSB_small">sceneTypeHistogram: {formatSceneTypeHistogram(comfyNode?.data?.plannerMeta?.sceneTypeHistogram || comfyNode?.data?.comfyDebug?.analysis?.sceneTypeHistogram)}</div>
                      <div className="clipSB_small">worldLock.locationType: {String(comfyNode?.data?.plannerMeta?.worldLock?.locationType || comfyNode?.data?.comfyDebug?.worldLock?.locationType || '—')}</div>
                      <div className="clipSB_small">worldLock.lighting: {String(comfyNode?.data?.plannerMeta?.worldLock?.lighting || comfyNode?.data?.comfyDebug?.worldLock?.lighting || '—')}</div>
                    </ComfyCollapsibleSection>

                    <div className="clipSB_comfySection">
                      <div className="clipSB_comfyBlockTitle">IMAGE · {comfyModeMeta.labelRu} / {comfyStyleMeta.labelRu}</div>
                      <div className="clipSB_comfySplitGrid">
                        <div className="clipSB_comfySplitCol">
                          <div className="clipSB_hint">Image prompt · {COMFY_SYNC_STATUS_LABELS[comfySelectedScene.imagePromptSyncStatus] || '—'}</div>
                          {comfySelectedScene?.imagePromptEditorLang === 'en_fallback' ? (
                            <div className="clipSB_small clipSB_promptFallbackNote" style={{ marginBottom: 6 }}>EN fallback loaded into editor.</div>
                          ) : null}
                          <textarea
                            className="clipSB_textarea clipSB_comfyTextarea"
                            value={String(comfySelectedScene.imagePromptEditorValue || '')}
                            onChange={(e) => handleComfyImagePromptChange(e.target.value)}
                            placeholder="Сгенерированный prompt сцены появится здесь автоматически"
                          />
                          {String(comfySelectedScene.imagePromptEnSource || '').trim() ? (
                            <div className="clipSB_small" style={{ marginTop: 6 }}>
                              {`Original EN fallback source: ${String(comfySelectedScene.imagePromptEnSource || '—')}`}
                            </div>
                          ) : null}
                          <div className="clipSB_small" style={{ marginTop: 6 }}>
                            {comfySelectedScene?.imagePromptEditorLang === 'en_fallback'
                              ? `EN source (editable): ${String(comfySelectedScene.imagePromptEn || '—')}`
                              : `EN (model): ${String(comfySelectedScene.imagePromptEn || '—')}`}
                          </div>
                          <div className="clipSB_small">promptLanguageStatus.image: {String(comfySelectedScene?.promptLanguageStatus?.image || '—')}</div>
                          {(String(comfySelectedScene.imagePromptSyncError || '').trim()) ? (
                            <div className="clipSB_hint" style={{ marginTop: 6, color: '#ff8a8a' }}>{String(comfySelectedScene.imagePromptSyncError || '')}</div>
                          ) : null}
                          {comfySelectedScene.imagePromptSyncStatus === PROMPT_SYNC_STATUS.syncError ? (
                            <button className="clipSB_btn clipSB_btnSecondary" style={{ marginTop: 8 }} onClick={async () => { try { await syncComfyPrompt({ idx: comfySafeIndex, promptType: 'image', force: true }); } catch (e) { console.error(e); } }}>Retry sync image</button>
                          ) : null}
                        </div>
                        <div className="clipSB_comfySplitCol">
                          <div className="clipSB_hint">Preview изображения</div>
                          <div className="clipSB_comfyPreviewBox">
                            {comfySelectedScene.imageUrl ? (
                              <img
                                className="clipSB_comfyPreviewImg"
                                src={resolveAssetUrl(comfySelectedScene.imageUrl)}
                                alt={comfySelectedScene.title || 'scene'}
                                onClick={(e) => handleComfyPreviewOpenLightbox(resolveAssetUrl(comfySelectedScene.imageUrl), e)}
                              />
                            ) : (
                              <div className="clipSB_comfyPreviewEmpty clipSB_comfyPreviewEmptyNoFill">Изображение сцены пока не создано</div>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="clipSB_comfyActions">
                        <button className="clipSB_btn" onClick={handleComfyGenerateImage} disabled={comfyImageLoading || comfySelectedScene.imagePromptSyncStatus === PROMPT_SYNC_STATUS.syncing}>{comfyImageLoading ? 'Создаю...' : (comfySelectedScene.imagePromptSyncStatus === PROMPT_SYNC_STATUS.syncing ? 'Синхронизация...' : 'Создать изображение')}</button>
                        <button className="clipSB_btn clipSB_btnSecondary" onClick={handleComfyDeleteImage} disabled={comfyImageLoading}>Удалить изображение</button>
                      </div>
                      {comfyImageError ? <div className="clipSB_hint" style={{ color: '#ff8a8a' }}>{comfyImageError}</div> : null}
                      {(String(comfySelectedScene.imagePromptSyncError || '').trim() || comfyPromptSyncError) ? <div className="clipSB_hint" style={{ color: '#ff8a8a' }}>sync: {String(comfySelectedScene.imagePromptSyncError || comfyPromptSyncError || '')}</div> : null}
                    </div>

                    <ComfyCollapsibleSection title="Audio / Timing">
                      <div className="clipSB_comfyBlockTitle">AUDIO SLICE</div>
                      <div className="clipSB_hint" style={{ marginBottom: 8 }}>Используется общий audioUrl и текущий тайминг сцены.</div>
                      <div className="clipSB_comfyActions">
                        <button
                          className="clipSB_btn clipSB_btnSecondary"
                          onClick={handleComfyTakeAudio}
                          disabled={comfyAudioSliceLoading || comfySelectedAudioSliceStatus === 'loading' || !globalAudioUrlRaw}
                        >
                          {comfyAudioSliceLoading || comfySelectedAudioSliceStatus === 'loading' ? 'Делаю...' : (comfySelectedScene?.audioSliceUrl ? 'Обновить аудио' : 'Взять аудио')}
                        </button>
                      </div>
                      {comfySelectedAudioSliceUrl ? (
                        <audio
                          key={`comfy-slice-${String(comfySelectedScene.sceneId || comfySafeIndex)}-${String(comfySelectedAudioSliceUrl || '')}`}
                          className="clipSB_audioPlayer"
                          controls
                          preload="metadata"
                          src={comfySelectedAudioSliceUrl}
                          onLoadedMetadata={handleComfySliceLoadedMetadata}
                          onError={handleComfySliceAudioError}
                        />
                      ) : (
                        <div className="clipSB_hint">
                          {(String(comfySelectedScene?.resolvedWorkflowKey || "").trim() === "lip_sync_music" || Boolean(comfySelectedScene?.lipSync))
                            ? "Для lipSync audioSlice подготовится автоматически при запуске генерации видео. Если авто-извлечение не удастся, появится ошибка."
                            : "Срез ещё не создан. Нажмите «Взять аудио»."}
                        </div>
                      )}
                      <div className="clipSB_audioDebugGrid" style={{ marginTop: 8 }}>
                        <div className="clipSB_videoKv"><span>t0</span><span>{fmtSecAndMs(Number(comfySelectedScene.audioSliceT0 ?? comfySelectedStartSec))}</span></div>
                        <div className="clipSB_videoKv"><span>t1</span><span>{fmtSecAndMs(Number(comfySelectedScene.audioSliceT1 ?? comfySelectedEndSec))}</span></div>
                        <div className="clipSB_videoKv"><span>expected duration</span><span>{fmtSecAndMs(comfySelectedExpectedSliceSec)}</span></div>
                        <div className="clipSB_videoKv"><span>backend duration</span><span>{fmtSecAndMs(comfySelectedScene.audioSliceBackendDurationSec)}</span></div>
                        <div className="clipSB_videoKv"><span>actual duration</span><span>{fmtSecAndMs(comfySelectedScene.audioSliceActualDurationSec)}</span></div>
                        <div className="clipSB_videoKv"><span>speechSafeAdjusted</span><span>{String(Boolean(comfySelectedScene.speechSafeAdjusted))}</span></div>
                        <div className="clipSB_videoKv"><span>speechSafeShiftMs</span><span>{String(comfySelectedScene.speechSafeShiftMs ?? 0)}</span></div>
                        <div className="clipSB_videoKv"><span>sliceMayCutSpeech</span><span>{String(Boolean(comfySelectedScene.sliceMayCutSpeech))}</span></div>
                      </div>
                      {comfySelectedAudioSliceStatus === 'loading' ? <div className="clipSB_hint">Извлекаю аудио по timeline сцены…</div> : null}
                      {comfySelectedAudioSliceError ? <div className="clipSB_hint" style={{ color: '#ff8a8a' }}>{comfySelectedAudioSliceError}</div> : null}
                    </ComfyCollapsibleSection>

                    {(comfySelectedScene.imageUrl && !comfyShowVideoSection) ? (
                      <div className="clipSB_comfySection">
                        <button className="clipSB_btn" onClick={handleComfyOpenVideoPanel}>Открыть видео-блок</button>
                      </div>
                    ) : null}

                    {comfyShowVideoSection ? (
                      <div className="clipSB_videoBlock">
                        <div className="clipSB_comfyBlockTitle">VIDEO · {comfyModeMeta.labelRu} / {comfyStyleMeta.labelRu}</div>
                        <div className="clipSB_comfySplitGrid">
                        <div className="clipSB_comfySplitCol">
                            <div className="clipSB_hint">Video prompt · {COMFY_SYNC_STATUS_LABELS[comfySelectedScene.videoPromptSyncStatus] || '—'}</div>
                            {comfySelectedScene?.videoPromptEditorLang === 'en_fallback' ? (
                              <div className="clipSB_small clipSB_promptFallbackNote" style={{ marginBottom: 6 }}>EN fallback loaded into editor.</div>
                            ) : null}
                            <textarea
                              className="clipSB_textarea clipSB_comfyTextarea"
                              value={String(comfySelectedScene.videoPromptEditorValue || '')}
                              onChange={(e) => handleComfyVideoPromptChange(e.target.value)}
                              placeholder="Сгенерированный video prompt сцены появится здесь автоматически"
                              disabled={!comfySelectedScene.imageUrl}
                            />
                            {String(comfySelectedScene.videoPromptEnSource || '').trim() ? (
                              <div className="clipSB_small" style={{ marginTop: 6 }}>
                                {`Original EN fallback source: ${String(comfySelectedScene.videoPromptEnSource || '—')}`}
                              </div>
                            ) : null}
                            <div className="clipSB_small" style={{ marginTop: 6 }}>
                              {comfySelectedScene?.videoPromptEditorLang === 'en_fallback'
                                ? `EN source (editable): ${String(comfySelectedScene.videoPromptEn || '—')}`
                                : `EN (model): ${String(comfySelectedScene.videoPromptEn || '—')}`}
                            </div>
                            <div className="clipSB_small">promptLanguageStatus.video: {String(comfySelectedScene?.promptLanguageStatus?.video || '—')}</div>
                            {(String(comfySelectedScene.videoPromptSyncError || '').trim()) ? (
                              <div className="clipSB_hint" style={{ marginTop: 6, color: '#ff8a8a' }}>{String(comfySelectedScene.videoPromptSyncError || '')}</div>
                            ) : null}
                            {comfySelectedScene.videoPromptSyncStatus === PROMPT_SYNC_STATUS.syncError ? (
                              <button className="clipSB_btn clipSB_btnSecondary" style={{ marginTop: 8 }} onClick={async () => { try { await syncComfyPrompt({ idx: comfySafeIndex, promptType: 'video', force: true }); } catch (e) { console.error(e); } }}>Retry sync video</button>
                            ) : null}
                            <div className="clipSB_comfyActions">
                              <button
                                className="clipSB_btn"
                                onClick={handleComfyGenerateVideo}
                                disabled={
                                  !comfySelectedScene.imageUrl
                                  || comfyVideoLoading
                                  || comfyHasActiveVideoJobForScene
                                  || Boolean(String(comfySelectedScene.videoUrl || '').trim())
                                  || comfySelectedScene.videoPromptSyncStatus === PROMPT_SYNC_STATUS.syncing
                                }
                              >
                                {(comfyVideoLoading || comfyHasActiveVideoJobForScene) ? 'Делаю...' : (comfySelectedScene.videoPromptSyncStatus === PROMPT_SYNC_STATUS.syncing ? 'Синхронизация...' : 'Сделать видео')}
                              </button>
                              <button className="clipSB_btn clipSB_btnSecondary" onClick={handleComfyDeleteVideo} disabled={comfyVideoLoading}>Удалить видео</button>
                            </div>
                          </div>
                          <div className="clipSB_comfySplitCol">
                            <div className="clipSB_hint">Video preview / status</div>
                            <div className="clipSB_videoPreviewWrap">
                              {comfySelectedScene.videoUrl ? (
                                <video className="clipSB_videoPlayer" src={resolveAssetUrl(comfySelectedScene.videoUrl)} controls />
                              ) : comfySelectedScene.imageUrl ? (
                                <img
                                  className="clipSB_videoPoster"
                                  src={resolveAssetUrl(comfySelectedScene.imageUrl)}
                                  alt={comfySelectedScene.title || 'video source preview'}
                                />
                              ) : (
                                <div className="clipSB_comfyPreviewEmpty">Сначала создайте изображение для этой сцены</div>
                              )}

                              {comfyShowingGeneratingOverlay ? (
                                <div className="clipSB_videoOverlay">
                                  <span className="clipSB_videoLoadingPulse">Генерация видео...</span>
                                </div>
                              ) : null}
                            </div>
                          </div>
                        </div>
                        {String(comfySelectedScene?.videoError || '').trim() ? <div className="clipSB_hint" style={{ color: '#ff8a8a' }}>{String(comfySelectedScene?.videoError || '')}</div> : null}
                      </div>
                    ) : null}

                    <ComfyCollapsibleSection title="Debug">
                      <div className="clipSB_small">pipeline: {(Array.isArray(comfyNode?.data?.pipelineFlow) ? comfyNode.data.pipelineFlow.join(' → ') : 'brain → scene image → scene video')}</div>
                      <div className="clipSB_small">plannerMode: {String(comfyNode?.data?.plannerMode || comfyNode?.data?.plannerMeta?.plannerMode || 'legacy')}</div>
                      <div className="clipSB_small">режим: {comfyModeMeta.labelRu} • стиль: {comfyStyleMeta.labelRu}</div>
                      <div className="clipSB_small">narrative source: {normalizeStoryboardSourceValue(comfyNode?.data?.narrativeSource || comfyNode?.data?.plannerMeta?.narrativeSource || comfyNode?.data?.comfyDebug?.narrativeSource, 'none')}</div>
                      <div className="clipSB_small">storySource: {normalizeStoryboardSourceValue(comfyNode?.data?.plannerMeta?.storySource || comfyNode?.data?.comfyDebug?.storySource, 'none')}</div>
                      <div className="clipSB_small">requestedModel: {String(comfyNode?.data?.comfyDebug?.requestedModel || '—')}</div>
                      <div className="clipSB_small">fallback: {String(comfyNode?.data?.comfyDebug?.fallbackFrom || '—')} → {String(comfyNode?.data?.comfyDebug?.fallbackTo || comfyNode?.data?.comfyDebug?.effectiveModel || '—')}</div>
                      <div className="clipSB_small">effectiveModel: {String(comfyNode?.data?.comfyDebug?.effectiveModel || '—')}</div>
                      <div className="clipSB_small">sanitizedError: {String(comfyNode?.data?.comfyDebug?.sanitizedError || comfyNode?.data?.errorMessage || '—')}</div>
                      <div className="clipSB_small">parseFailedReason: {String(comfyNode?.data?.comfyDebug?.parseFailedReason || comfyNode?.data?.errorMessage || '—')}</div>
                      <div className="clipSB_small">weakSemanticContext: {String(Boolean(comfyNode?.data?.comfyDebug?.weakSemanticContext ?? comfyNode?.data?.plannerMeta?.weakSemanticContext))}</div>
                      <div className="clipSB_small">semanticContextReason: {String(comfyNode?.data?.comfyDebug?.semanticContextReason || comfyNode?.data?.plannerMeta?.semanticContextReason || '—')}</div>
                      <div className="clipSB_small">hasAudio / hasText / hasRefs: {String(Boolean(comfyNode?.data?.comfyDebug?.hasAudio))} / {String(Boolean(comfyNode?.data?.comfyDebug?.hasText))} / {String(Boolean(comfyNode?.data?.comfyDebug?.hasRefs))}</div>
                      <div className="clipSB_small">audioPartAttached: {String(Boolean(comfyNode?.data?.comfyDebug?.audioPartAttached))}</div>
                      <div className="clipSB_small">imagePartsAttachedCount: {String(comfyNode?.data?.comfyDebug?.imagePartsAttachedCount ?? '—')}</div>
                      <div className="clipSB_small">audioDurationSec: {comfyNode?.data?.plannerMeta?.audioDurationSec ?? '—'}</div>
                      <div className="clipSB_small">timelineDurationSec: {comfyNode?.data?.plannerMeta?.timelineDurationSec ?? '—'}</div>
                      <div className="clipSB_small">sceneDurationTotalSec: {comfyNode?.data?.plannerMeta?.sceneDurationTotalSec ?? '—'}</div>
                      <div className="clipSB_small">preview.sourceSceneId: {String(comfyNode?.data?.plannerMeta?.preview?.sourceSceneId || comfyNode?.data?.comfyDebug?.preview?.sourceSceneId || '—')}</div>
                      <div className="clipSB_small">preview.activeRefs: {Array.isArray(comfyNode?.data?.plannerMeta?.preview?.activeRefs || comfyNode?.data?.comfyDebug?.preview?.activeRefs) ? (comfyNode?.data?.plannerMeta?.preview?.activeRefs || comfyNode?.data?.comfyDebug?.preview?.activeRefs).join(', ') : '—'}</div>
                      <div className="clipSB_small">warnings: {(Array.isArray(comfyNode?.data?.warnings) ? comfyNode.data.warnings.join(' | ') : '') || 'none'}</div>
                    </ComfyCollapsibleSection>
                  </>
                )}
              </div>
            </div>
          </div>
        </div>
      ) : null}

      {lightboxUrl ? (
        <div className={`clipSB_lightbox${lightboxActive ? ' isActive' : ''}`} onClick={closeLightbox}>
          <img
            className={`clipSB_lightboxImg${lightboxActive ? ' isActive' : ''}`}
            src={lightboxUrl}
            alt="Full preview"
            onClick={(e) => e.stopPropagation()}
            style={lightboxImageStyle}
          />
        </div>
      ) : null}

{drawerOpen ? (
        <div className="clipSB_drawerOverlay" onClick={() => setDrawerOpen(false)}>
          <div className="clipSB_drawer" onClick={(e) => e.stopPropagation()}>
            <div className="clipSB_drawerHeader">
              <div className="clipSB_drawerTitle">Ноды</div>
              <button className="clipSB_drawerClose" onClick={() => setDrawerOpen(false)} title="Закрыть">×</button>
            </div>
            <div className="clipSB_drawerList">
              <div className="clipSB_drawerGroupTitle">БАЗОВЫЕ НОДЫ</div>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("audio")}>🎧 Аудио</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("text")}>📄 Текст</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("link")}>🔗 Ссылка</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("videoRef")}>🎬 Видео</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("brain")}>🧠 Мозг</button>
              <div className="clipSB_drawerSep" />
              <div className="clipSB_drawerGroupTitle">ОБЫЧНЫЕ REFS</div>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("ref_character")}>🧍 REF — Персонаж</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("ref_location")}>📍 REF — Локация</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("ref_style")}>🎨 REF — Стиль</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("ref_items")}>📦 REF — Предметы</button>
              <div className="clipSB_drawerSep" />
              <div className="clipSB_drawerGroupTitle">COMFY FLOW</div>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("comfyNarrative")}>📚 Сценарий</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("comfyBrain")}>🧠 COMFY BRAIN</button>
              <div className="clipSB_drawerSep" />
              <div className="clipSB_drawerGroupTitle">DEBUG / TEST / SERVICE</div>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("scenarioOutputTester")}>🧪 ТЕСТЕР СЦЕНАРИЯ</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("voiceOutputTester")}>📡 ТЕСТЕР ОЗВУЧКИ</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("brainPackageTester")}>🔬 ТЕСТЕР LEGACY PLANNER</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("musicPromptTester")}>⚡ ТЕСТЕР МУЗЫКИ</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("comfyStoryboard")}>🧩 COMFY STORYBOARD</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("scenarioStoryboard")}>🎞️ SCENARIO STORYBOARD</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("comfyVideoPreview")}>🎬 COMFY VIDEO PREVIEW</button>
              <div className="clipSB_drawerSep" />
              <div className="clipSB_drawerGroupTitle">COMFY CAST / REFS</div>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("refCharacter2")}>👤 CHARACTER 2</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("refCharacter3")}>👤 CHARACTER 3</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("refAnimal")}>🐾 ANIMAL</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("refGroup")}>👥 GROUP / COLLECTIVE</button>
              <div className="clipSB_drawerSep" />
              <div className="clipSB_drawerGroupTitle">СЦЕНЫ / СБОРКА</div>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("storyboard")}>🎞️ Storyboard</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("introFrame")}>🖼️ Intro Frame</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("assembly")}>🎬 Сборка</button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="clipSB_canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onEdgeClick={onEdgeClick}
          defaultEdgeOptions={{ style: { strokeDasharray: "6 6" }, interactionWidth: 30 }}
          connectionLineStyle={{ strokeDasharray: "6 6" }}
          connectionRadius={28}
          connectionDragThreshold={2}
          fitView
          minZoom={0.2}
          maxZoom={2}
        >
          <Background gap={24} size={1} color="rgba(255,255,255,0.07)" />
          <Controls />
        </ReactFlow>
      </div>
    </div>
  );
}
