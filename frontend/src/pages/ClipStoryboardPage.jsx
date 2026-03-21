import React, { useEffect, useMemo, useRef, useState, useCallback } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  Handle,
  Position,
  addEdge,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./ClipStoryboardPage.css";

import { API_BASE, fetchJson } from "../services/api";
import { useAuth } from "../app/AuthContext";
import { useNavigate } from "react-router-dom";
import {
  ComfyBrainNode,
  ComfyStoryboardNode,
  ComfyVideoPreviewNode,
  RefCharacter2Node,
  RefCharacter3Node,
  RefAnimalNode,
  RefGroupNode,
} from "./clip_nodes/comfy";
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


// -------------------------
// typed ports + colors (for clear wiring)
// -------------------------
const PORT_COLORS = {
  audio: "#ff5f7d",
  text: "#6fa8ff",
  ref_character: "#16d4de",
  ref_character_1: "#16d4de",
  ref_character_2: "#00bdd6",
  ref_character_3: "#1fc8ff",
  ref_animal: "#ffb347",
  ref_group: "#ff5f97",
  ref_location: "#a56fff",
  ref_style: "#ffcb52",
  ref_items: "#8ddf48",
  ref_props: "#8ddf48",
  plan: "#38d4ff",
  comfy_plan: "#38d4ff",
  comfy_storyboard: "#5c95ff",
  comfy_video: "#5ef2ff",
  intro_context: "#ff9d5c",
  intro_frame: "#ffd25a",
  intro_to_assembly: "#ffb35c",
  brain_to_storyboard: "#38d4ff",
  storyboard_to_assembly: "#5c95ff",
  assembly: "#5c95ff",
  brain: "#bb72ff",
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

const CLIP_TRACE_PERSIST = false;
const CLIP_TRACE_VIDEO_POLLING = false;
const CLIP_TRACE_COMFY_REFS = false;
const CLIP_TRACE_BRAIN_REFRESH = false;
const CLIP_TRACE_GRAPH_HYDRATE = false;
const CLIP_TRACE_ASSEMBLY_SOURCE = false;

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

function isBrainInput(handleId) {
  return handleId === "audio" || handleId === "text" || handleId === "ref_character" || handleId === "ref_location" || handleId === "ref_style" || handleId === "ref_items";
}

function isComfyBrainInput(handleId) {
  return ["audio", "text", ...Object.keys(COMFY_BRAIN_REF_HANDLE_CONFIG)].includes(handleId);
}

const EDGE_STYLE_BY_KIND = {
  audio: { color: PORT_COLORS.audio, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
  text: { color: PORT_COLORS.text, strokeWidth: 2.4, opacity: 1, animatedDash: true, filter: "drop-shadow(0 0 2px currentColor)" },
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
  if (sourceType === "comfyBrain" && isComfyBrainInput(sourceHandle)) return sourceHandle;

  if (targetType === "introFrame") {
    if (targetHandle === "story_context") return "intro_context";
    if (targetHandle === "title_context") return "text";
  }

  if (sourceType === "comfyBrain" && sourceHandle === "comfy_plan" && targetType === "comfyStoryboard" && targetHandle === "comfy_plan") {
    return "comfy_plan";
  }

  if (sourceType === "comfyStoryboard" && sourceHandle === "comfy_scene_video_out" && targetType === "comfyVideoPreview" && targetHandle === "comfy_scene_video_out") {
    return "comfy_video";
  }

  if (sourceType === "comfyStoryboard" && sourceHandle === "comfy_scene_video_out" && targetType === "assemblyNode") {
    return "storyboard_to_assembly";
  }

  if (sourceType === "introFrame" && sourceHandle === "intro_frame_out" && targetType === "assemblyNode" && targetHandle === "assembly_intro") {
    return "intro_to_assembly";
  }

  if (sourceType === "brainNode" && sourceHandle === "plan" && targetType === "storyboardNode" && targetHandle === "plan_in") {
    return "plan";
  }

  if (sourceType === "storyboardNode" && sourceHandle === "plan_out" && targetType === "assemblyNode") {
    return "storyboard_to_assembly";
  }

  if (sourceType === "brainNode" && targetType === "assemblyNode") return "brain_to_assembly";
  if (existingKind && EDGE_STYLE_BY_KIND[existingKind]) return existingKind;
  if (targetType === "assemblyNode") return "assembly";
  return "default";
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
  return !!(
    scene.lipSync === true
    || scene.isLipSync === true
    || String(scene.renderMode || "").trim().toLowerCase() === "avatar_lipsync"
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

const SCENE_IMAGE_FORMATS = ["9:16", "1:1", "16:9"];
const DEFAULT_SCENE_IMAGE_FORMAT = "9:16";
const USE_COMFY_MOCK = false;
const INTRO_STYLE_PRESET_META = {
  cinematic: {
    value: "cinematic",
    label: "Cinematic",
    shortDescription: "Премиальный film-poster кадр с серьёзным драматичным светом.",
    uiHint: "Poster-grade",
    accent: "#f6d365",
    secondary: "#5ee7df",
    background: "radial-gradient(circle at 24% 18%, rgba(246,211,101,0.76), rgba(94,231,223,0.24) 34%, rgba(10,16,28,1) 80%)",
    promptRules: [
      "ощущение opening shot / movie poster, clean composition и premium serious mood",
      "драматичный направленный свет, выразительный контраст, аккуратная иерархия кадра",
      "thumbnail energy сдержанная и дорогая, без дешёвого clickbait",
    ],
    forbidden: ["cheap clickbait look", "fake UI / arrows / circles", "collage или мусорная перегрузка"],
  },
  youtube: {
    value: "youtube",
    label: "YouTube",
    shortDescription: "Читаемый thumbnail-first кадр с сильным focal point и ясной иерархией.",
    uiHint: "Readable thumbnail",
    accent: "#ffcf5c",
    secondary: "#ff6b3d",
    background: "radial-gradient(circle at 24% 20%, rgba(255,207,92,0.88), rgba(255,107,61,0.38) 35%, rgba(9,15,32,1) 78%)",
    promptRules: [
      "ясный главный объект, сильная subject hierarchy и читаемость в маленьком размере",
      "больше thumbnail energy и punch, но всё ещё clean / premium",
      "акцент на одном визуальном обещании кадра, без перегруза",
    ],
    forbidden: ["red arrows / fake UI / badges", "reaction-meme trash", "too many competing focal points"],
  },
  dark_neon: {
    value: "dark_neon",
    label: "Dark Neon",
    shortDescription: "Тёмная cinematic сцена с контролируемыми cyan/magenta/violet акцентами.",
    uiHint: "Cyber glow",
    accent: "#6ef2ff",
    secondary: "#b86dff",
    background: "radial-gradient(circle at 25% 18%, rgba(110,242,255,0.5), rgba(184,109,255,0.28) 36%, rgba(4,8,20,1) 80%)",
    promptRules: [
      "dark scene + controlled neon accents, cinematic cyber glow",
      "контрастный mood, аккуратный rim light и читаемый focal point",
      "палитра неоновая, но сдержанная и дорогая, не кислотный мусор",
    ],
    forbidden: ["acid rainbow overload", "unreadable overglow", "chaotic cheap cyberpunk clutter"],
  },
  thriller: {
    value: "thriller",
    label: "Thriller",
    shortDescription: "Напряжённый low-key кадр с тревогой, опасностью и мрачным драматизмом.",
    uiHint: "Suspense mood",
    accent: "#ff6b6b",
    secondary: "#ffd166",
    background: "radial-gradient(circle at 22% 18%, rgba(255,107,107,0.55), rgba(255,209,102,0.18) 34%, rgba(8,8,12,1) 80%)",
    promptRules: [
      "suspense-first composition, low-key lighting и тревожная атмосфера",
      "кадр про напряжение и ожидание, а не про gore или horror-camp",
      "тени глубже, свет выборочный, настроение кинематографично-мрачное",
    ],
    forbidden: ["cheap horror camp", "blood / gore overload", "slasher poster clichés"],
  },
  fashion: {
    value: "fashion",
    label: "Fashion",
    shortDescription: "Premium editorial cover с polished posing и luxury finish.",
    uiHint: "Editorial polish",
    accent: "#ffd7f0",
    secondary: "#8ae5ff",
    background: "radial-gradient(circle at 24% 20%, rgba(255,215,240,0.78), rgba(138,229,255,0.28) 36%, rgba(14,15,28,1) 80%)",
    promptRules: [
      "ощущение luxury editorial cover / campaign still",
      "чистая stylish композиция, уверенная posing, polished premium surface",
      "look дорогой и curated, без mass-market banner energy",
    ],
    forbidden: ["cheap ecommerce banner feel", "sales-ad clutter", "mass-market promo styling"],
  },
  glitch: {
    value: "glitch",
    label: "Glitch",
    shortDescription: "Контролируемая digital distortion aesthetic без потери главного focal point.",
    uiHint: "Controlled distortion",
    accent: "#8eff8c",
    secondary: "#ff6ef2",
    background: "radial-gradient(circle at 24% 20%, rgba(142,255,140,0.58), rgba(255,110,242,0.28) 32%, rgba(4,10,18,1) 78%)",
    promptRules: [
      "stylized disruption и digital texture как осознанный приём, не шум",
      "главный subject остаётся читаемым, композиция не разваливается",
      "модерновый synthetic mood, но без unreadable mess",
    ],
    forbidden: ["full-frame unreadable corruption", "broken-image sludge", "destroyed focal hierarchy"],
  },
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

function getSceneImageSize(format) {
  const normalized = normalizeSceneImageFormat(format);
  if (normalized === "1:1") return { width: 1024, height: 1024 };
  if (normalized === "16:9") return { width: 1344, height: 768 };
  return { width: 768, height: 1344 };
}

function normalizeIntroStylePreset(stylePreset) {
  return INTRO_STYLE_PRESETS.includes(stylePreset) ? stylePreset : "cinematic";
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

function getIntroStyleMeta(stylePreset = "cinematic") {
  const normalized = normalizeIntroStylePreset(stylePreset);
  return INTRO_STYLE_PRESET_META[normalized] || INTRO_STYLE_PRESET_META.cinematic;
}

function resolveSceneTransitionType(scene) {
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

function enforceSceneDiversityLocal(rawScenes) {
  const scenes = Array.isArray(rawScenes) ? rawScenes : [];
  return scenes.map((scene, idx) => {
    const prev = idx > 0 ? scenes[idx - 1] : null;
    const sceneRole = normalizeSceneRole(scene?.sceneRole, idx);
    const currentText = String(scene?.sceneText || scene?.visualDescription || scene?.why || "").trim();
    const prevText = String(prev?.sceneText || prev?.visualDescription || prev?.why || "").trim();
    const isNearDup = isNearDuplicateSceneText(currentText, prevText);
    const roleDirective = getRoleDiversityDirective(sceneRole);
    const baseFramePrompt = String(scene?.framePrompt || scene?.imagePrompt || scene?.prompt || "").trim();
    const baseVideoPrompt = String(scene?.videoPrompt || scene?.transitionActionPrompt || "").trim();

    const patch = {
      sceneRole,
      framePrompt: baseFramePrompt ? `${baseFramePrompt}\n${roleDirective}` : roleDirective,
      videoPrompt: baseVideoPrompt ? `${baseVideoPrompt}\n${roleDirective}` : roleDirective,
    };

    if (isNearDup && currentText) {
      patch.sceneText = `${currentText} (${sceneRole.replace("_", " ")} beat; add new visual meaning relative to previous shot).`;
    }

    return { ...scene, ...patch };
  });
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
      title: String(introFrame?.title || "").trim(),
      stylePreset: introFrame?.stylePreset || "cinematic",
      contextSummary: String(introFrame?.contextSummary || "").trim(),
      previewFormat: introFrame?.previewFormat,
    });
  }
  return imageUrl;
}

function hasIntroFramePreview(introFrame) {
  if (!introFrame) return false;
  const previewKind = getEffectiveIntroFramePreviewKind(introFrame);
  if (previewKind === INTRO_FRAME_PREVIEW_KINDS.GENERATED_LOCAL) {
    return !!String(introFrame?.title || "").trim() || !!String(introFrame?.generatedAt || "").trim();
  }
  return !!String(introFrame?.imageUrl || "").trim();
}

function buildIntroFrameComparablePayload(introFrame) {
  if (!introFrame || !hasIntroFramePreview(introFrame)) return null;
  const previewKind = getEffectiveIntroFramePreviewKind(introFrame);
  return {
    nodeId: String(introFrame?.nodeId || ""),
    title: String(introFrame?.title || "").trim(),
    autoTitle: !!introFrame?.autoTitle,
    stylePreset: normalizeIntroStylePreset(introFrame?.stylePreset || "cinematic"),
    durationSec: normalizeIntroDurationSec(introFrame?.durationSec),
    previewFormat: normalizeIntroFramePreviewFormat(introFrame?.previewFormat),
    previewKind,
    generatedAt: String(introFrame?.generatedAt || "").trim() || null,
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
  const storyboardNode = (Array.isArray(nodes) ? nodes : []).find((n) => n?.type === "storyboardNode") || null;
  return Array.isArray(storyboardNode?.data?.scenes) ? storyboardNode.data.scenes : [];
}

function normalizeComfyScenesForAssembly(scenes = []) {
  return normalizeSceneCollectionWithSceneId(scenes, "comfy_scene").map((scene, idx) => {
    const durationFromScene = normalizeDurationSec(scene?.durationSec);
    const requestedDurationSec = durationFromScene != null
      ? durationFromScene
      : getSceneRequestedDurationSec({
        requestedDurationSec: scene?.requestedDurationSec,
        t0: scene?.startSec,
        t1: scene?.endSec,
      });
    const normalizedFormat = normalizeSceneImageFormat(scene?.imageFormat || scene?.format || "9:16");
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
  return normalizeComfyScenesForAssembly(comfyStoryboardNode?.data?.mockScenes);
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
      return sourceType === "storyboardNode" || sourceType === "comfyStoryboard";
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
      title: String(introNode?.data?.title || "").trim(),
      autoTitle: !!introNode?.data?.autoTitle,
      stylePreset: normalizeIntroStylePreset(introNode?.data?.stylePreset || "cinematic"),
      durationSec: normalizeIntroDurationSec(introNode?.data?.durationSec),
      previewFormat: normalizeIntroFramePreviewFormat(introNode?.data?.previewFormat),
      imageUrl: String(introNode?.data?.imageUrl || "").trim(),
      previewKind: getEffectiveIntroFramePreviewKind(introNode?.data),
      generatedAt: String(introNode?.data?.generatedAt || "").trim(),
      status: String(introNode?.data?.status || "idle"),
      contextSummary: String(introNode?.data?.contextSummary || "").trim(),
      altTitles: Array.isArray(introNode?.data?.altTitles) ? introNode.data.altTitles : [],
    }
    : null;

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

  if (sourceNode?.type === "comfyStoryboard") {
    const scenes = normalizeComfyScenesForAssembly(sourceNode?.data?.mockScenes);
    return {
      assemblyNodeId: effectiveAssemblyNodeId,
      sourceNodeId: String(sourceNode?.id || ""),
      sourceNodeType: "comfyStoryboard",
      scenesSource: "comfyStoryboard",
      scenes,
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
    introSourceNodeId: String(introFrame?.nodeId || ""),
    introSourceNodeType: introFrame?.nodeType || "",
    introFrame,
  };
}

function extractGlobalAudioUrlFromNodes(nodes = []) {
  const audioNodeWithUrl = (Array.isArray(nodes) ? nodes : []).find((n) => n?.type === "audioNode" && n?.data?.audioUrl);
  return audioNodeWithUrl?.data?.audioUrl ? String(audioNodeWithUrl.data.audioUrl) : "";
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
  const previousEndImageUrl = String(previousScene?.endImageUrl || "").trim();
  const manualStartImageUrl = String(scene?.startImageUrl || "").trim();
  if (inheritPreviousEndAsStart && previousEndImageUrl) return "previous_end";
  if (manualStartImageUrl) return "manual";
  return "none";
}

function getEffectiveSceneStartImage(scene, previousScene) {
  if (resolveSceneTransitionType(scene) !== "continuous") return String(scene?.startImageUrl || "").trim();
  if (getSceneStartImageSource(scene, previousScene) === "previous_end") {
    return String(previousScene?.endImageUrl || "").trim();
  }
  return String(scene?.startImageUrl || "").trim();
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

async function uploadAsset(file) {
  const fd = new FormData();
  fd.append("file", file);

  const res = await fetch(`${API_BASE}/api/assets/upload`, {
    method: "POST",
    body: fd,
    credentials: "include",
  });
  if (!res.ok) {
    let txt = "";
    try {
      txt = await res.text();
    } catch {
      // ignore
    }
    throw new Error(`upload_failed:${res.status}:${txt || res.statusText}`);
  }
  return await res.json();
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

function splitIntroPreviewWord(word = "", maxChars = 12) {
  const clean = String(word || "").trim();
  if (!clean) return [];
  if (clean.length <= maxChars) return [clean];
  const parts = [];
  let rest = clean;
  while (rest.length > maxChars) {
    const take = Math.max(4, maxChars - 1);
    parts.push(`${rest.slice(0, take)}-`);
    rest = rest.slice(take);
  }
  if (rest) parts.push(rest);
  return parts;
}

function wrapIntroPreviewTitle({ ctx, text, maxWidth, maxLines }) {
  const rawWords = String(text || "").split(/\s+/).filter(Boolean);
  if (!rawWords.length) return [];
  const words = rawWords.flatMap((word) => splitIntroPreviewWord(word, 14));
  const lines = [];
  let current = "";
  for (let idx = 0; idx < words.length; idx += 1) {
    const candidate = current ? `${current} ${words[idx]}` : words[idx];
    if (current && ctx.measureText(candidate).width > maxWidth) {
      lines.push(current);
      current = words[idx];
      if (lines.length >= maxLines - 1) break;
      continue;
    }
    current = candidate;
  }
  if (current && lines.length < maxLines) lines.push(current);
  const renderedWordCount = lines.join(" ").replace(/…/g, "").split(/\s+/).filter(Boolean).length;
  if (renderedWordCount < words.length && lines.length) {
    lines[lines.length - 1] = `${lines[lines.length - 1].replace(/[.,!?;:\-–—\s]+$/g, "")}…`;
  }
  return lines.slice(0, maxLines);
}

function fitIntroPreviewTitleLayout({ title = "", previewFormatMeta }) {
  const safeTitle = truncateIntroText(title || "INTRO FRAME", previewFormatMeta.titleMaxChars) || "INTRO FRAME";
  if (typeof document === "undefined") {
    return {
      lines: [safeTitle],
      fontSize: previewFormatMeta.titleFontPx,
      lineHeight: previewFormatMeta.titleLineHeight,
      plateMaxWidth: previewFormatMeta.width * 0.78,
      platePaddingX: 18,
      platePaddingY: 16,
      maxPlateHeight: Math.round(previewFormatMeta.height * 0.3),
    };
  }

  const canvas = document.createElement("canvas");
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    return {
      lines: [safeTitle],
      fontSize: previewFormatMeta.titleFontPx,
      lineHeight: previewFormatMeta.titleLineHeight,
      plateMaxWidth: previewFormatMeta.width * 0.78,
      platePaddingX: 18,
      platePaddingY: 16,
      maxPlateHeight: Math.round(previewFormatMeta.height * 0.3),
    };
  }

  const isLandscape = previewFormatMeta.aspectRatio > 1.2;
  const isPortrait = previewFormatMeta.aspectRatio < 0.9;
  const maxLines = isPortrait ? 4 : 3;
  const widthRatio = isLandscape ? 0.62 : (isPortrait ? 0.84 : 0.78);
  const boxWidth = Math.round(previewFormatMeta.width * widthRatio);
  const maxPlateHeight = Math.round(previewFormatMeta.height * (isPortrait ? 0.34 : 0.26));
  const paddingX = isLandscape ? 18 : 16;
  const paddingY = isPortrait ? 16 : 14;
  const maxFont = previewFormatMeta.titleFontPx;
  const minFont = isPortrait ? 20 : 18;

  for (let fontSize = maxFont; fontSize >= minFont; fontSize -= 1) {
    const lineHeight = Math.round(fontSize * (isPortrait ? 1.05 : 1.08));
    ctx.font = `900 ${fontSize}px Arial`;
    const lines = wrapIntroPreviewTitle({
      ctx,
      text: safeTitle,
      maxWidth: boxWidth - paddingX * 2,
      maxLines,
    });
    if (!lines.length) continue;
    const longestLine = Math.max(...lines.map((line) => ctx.measureText(line).width));
    const contentHeight = lines.length * lineHeight;
    const plateHeight = contentHeight + paddingY * 2;
    if (longestLine <= (boxWidth - paddingX * 2) && plateHeight <= maxPlateHeight) {
      return {
        lines,
        fontSize,
        lineHeight,
        plateMaxWidth: boxWidth,
        platePaddingX: paddingX,
        platePaddingY: paddingY,
        maxPlateHeight,
      };
    }
  }

  const fallbackFontSize = minFont;
  ctx.font = `900 ${fallbackFontSize}px Arial`;
  return {
    lines: wrapIntroPreviewTitle({
      ctx,
      text: safeTitle,
      maxWidth: boxWidth - paddingX * 2,
      maxLines,
    }) || [safeTitle],
    fontSize: fallbackFontSize,
    lineHeight: Math.round(fallbackFontSize * (isPortrait ? 1.05 : 1.08)),
    plateMaxWidth: boxWidth,
    platePaddingX: paddingX,
    platePaddingY: paddingY,
    maxPlateHeight,
  };
}

function buildIntroFrameAutoTitle({ textValue = "", scenes = [] } = {}) {
  const text = truncateIntroText(textValue, 72);
  if (text) {
    const words = text.split(" ").filter(Boolean).slice(0, 6);
    return words.join(" ").toUpperCase();
  }
  const firstScene = Array.isArray(scenes) ? scenes.find((scene) => String(getSceneUiDescription(scene) || "").trim()) : null;
  const fallback = truncateIntroText(getSceneUiDescription(firstScene) || firstScene?.title || "", 56);
  return fallback ? fallback.toUpperCase() : "CINEMATIC INTRO";
}

function normalizeIntroConnectedRefsByRole(refsByRole = {}) {
  return Object.fromEntries(
    INTRO_COMFY_REF_ROLES.map((role) => {
      const items = Array.isArray(refsByRole?.[role]) ? refsByRole[role] : [];
      const urls = items
        .map((item) => (typeof item === "string" ? item : item?.url))
        .map((value) => String(value || "").trim())
        .filter(Boolean);
      return [role, [...new Set(urls)]];
    })
  );
}

function formatIntroRoleLabel(role) {
  return String(role || "")
    .replace(/^character_/, "character ")
    .replace(/_/g, " ")
    .trim();
}

function buildIntroRoleAwareCastSummary(refsByRole = {}) {
  const castRoles = INTRO_CAST_ROLES.filter((role) => (refsByRole?.[role] || []).length > 0);
  if (!castRoles.length) return "";
  const heroRoles = ["character_1", "character_2", "character_3"].filter((role) => (refsByRole?.[role] || []).length > 0);
  const supportRoles = castRoles.filter((role) => !heroRoles.includes(role));
  const summary = [];
  if (heroRoles.length) summary.push(`heroes: ${heroRoles.map(formatIntroRoleLabel).join(", ")}`);
  if (supportRoles.length) summary.push(`support: ${supportRoles.map(formatIntroRoleLabel).join(", ")}`);
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

function buildIntroStyleContext({ stylePreset = "", refsByRole = {} } = {}) {
  const normalizedStylePreset = normalizeIntroStylePreset(stylePreset || "cinematic");
  const hasStyleRefs = (refsByRole?.style || []).length > 0;
  return `${normalizedStylePreset}${hasStyleRefs ? " + explicit style reference anchors" : " baseline"}`;
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
  const storyEdges = incoming.filter((edge) => String(edge?.targetHandle || "") === "story_context");
  const titleEdge = [...incoming].reverse().find((edge) => String(edge?.targetHandle || "") === "title_context") || null;
  const storySources = storyEdges
    .map((edge) => nodesById.get(edge?.source) || null)
    .filter(Boolean);
  const titleSource = titleEdge ? (nodesById.get(titleEdge.source) || null) : null;
  const comfyNode = storySources.find((node) => node?.type === "comfyStoryboard") || null;
  const comfyBrainNode = storySources.find((node) => node?.type === "comfyBrain") || null;
  const storyboardNode = storySources.find((node) => node?.type === "storyboardNode") || null;
  const textNode = titleSource?.type === "textNode"
    ? titleSource
    : storySources.find((node) => node?.type === "textNode") || null;
  const comfyScenes = normalizeComfyScenesForAssembly(comfyNode?.data?.mockScenes);
  const storyboardScenes = Array.isArray(storyboardNode?.data?.scenes) ? storyboardNode.data.scenes : [];
  const scenes = comfyScenes.length ? comfyScenes : storyboardScenes;
  const sceneCount = scenes.length;
  const textValue = String(textNode?.data?.textValue || "").trim();
  const sourceLabels = storySources.map((node) => {
    if (node?.type === "comfyStoryboard") return "COMFY STORYBOARD";
    if (node?.type === "storyboardNode") return "STORYBOARD";
    if (node?.type === "brainNode") return "BRAIN";
    if (node?.type === "comfyBrain") return "COMFY BRAIN";
    if (node?.type === "refNode") return String(node?.data?.title || "REF");
    return String(node?.type || "NODE").toUpperCase();
  });
  const summaryParts = [];
  if (sceneCount) summaryParts.push(`${sceneCount} сцен`);
  if (textValue) summaryParts.push(`text: ${truncateIntroText(textValue, 42)}`);
  if (sourceLabels.length) summaryParts.push(`inputs: ${sourceLabels.join(", ")}`);
  const plannerInput = comfyNode?.data?.plannerMeta?.plannerInput || comfyBrainNode?.data?.plannerMeta?.plannerInput || {};
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
  const connectedRefsByRole = normalizeIntroConnectedRefsByRole(
    Object.fromEntries(
      INTRO_COMFY_REF_ROLES.map((role) => {
        const graphSourceNodeId = String(graphConnectedSourceNodeIdsByRole?.[role] || "").trim();
        if (graphSourceNodeId) {
          return [role, graphConnectedRefsByRole?.[role] || []];
        }
        if (!hasGraphConnectedRefs) {
          return [role, plannerConnectedRefsByRole?.[role] || []];
        }
        return [role, []];
      })
    )
  );
  const activeRefRoles = INTRO_COMFY_REF_ROLES.filter((role) => (connectedRefsByRole?.[role] || []).length > 0);
  const introActiveCastRoles = INTRO_CAST_ROLES.filter((role) => (connectedRefsByRole?.[role] || []).length > 0);
  const heroParticipants = ["character_1", "character_2", "character_3"].filter((role) => (connectedRefsByRole?.[role] || []).length > 0);
  const supportingParticipants = ["animal", "group"].filter((role) => (connectedRefsByRole?.[role] || []).length > 0);
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
  const introMustAppear = [...introActiveCastRoles];
  const introMustNotAppear = [];
  const worldContext = buildIntroWorldContext({
    refsByRole: connectedRefsByRole,
    sceneCount,
    storyContext: summaryParts.join(" • "),
  });
  const styleContext = buildIntroStyleContext({
    stylePreset: plannerInput?.stylePreset || comfyNode?.data?.stylePreset || comfyBrainNode?.data?.stylePreset || "",
    refsByRole: connectedRefsByRole,
  });
  const importantProps = buildIntroImportantProps(connectedRefsByRole);
  if (roleAwareCastSummary) summaryParts.push(`cast: ${roleAwareCastSummary}`);
  return {
    sourceNodeIds: storySources.map((node) => String(node?.id || "")).filter(Boolean),
    sourceNodeTypes: storySources.map((node) => String(node?.type || "")).filter(Boolean),
    refAnchorNodeId: String(graphRefPackage?.anchorNodeId || ""),
    refAnchorNodeType: String(graphRefPackage?.anchorNodeType || ""),
    titleContextNodeId: String(titleSource?.id || ""),
    titleText: textValue,
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
    storyBeats.length ? `Opening beats: ${storyBeats.join(" | ")}` : "",
  ].filter(Boolean).join(" • ");
}

function buildIntroFramePreviewDataUrl({ title = "", stylePreset = "cinematic", contextSummary = "", previewFormat = INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE } = {}) {
  const formatMeta = getIntroFramePreviewFormatMeta(previewFormat);
  const safeTitle = truncateIntroText(title || "INTRO FRAME", formatMeta.titleMaxChars) || "INTRO FRAME";
  const safeContext = truncateIntroText(contextSummary || "Story preview", formatMeta.contextMaxChars) || "Story preview";
  const meta = getIntroStyleMeta(stylePreset);
  const width = formatMeta.width;
  const height = formatMeta.height;
  const titleLayout = fitIntroPreviewTitleLayout({ title: safeTitle, previewFormatMeta: formatMeta });
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

      const drawWrapped = (text, x, y, maxWidth, lineHeight, maxLines, font, color) => {
        ctx.font = font;
        ctx.fillStyle = color;
        const words = String(text || "").split(/\s+/).filter(Boolean);
        let line = "";
        let linesDrawn = 0;
        for (let i = 0; i < words.length; i += 1) {
          const testLine = line ? `${line} ${words[i]}` : words[i];
          if (ctx.measureText(testLine).width > maxWidth && line) {
            ctx.fillText(line, x, y + linesDrawn * lineHeight);
            linesDrawn += 1;
            line = words[i];
            if (linesDrawn >= maxLines - 1) break;
          } else {
            line = testLine;
          }
        }
        if (linesDrawn < maxLines && line) {
          const remaining = linesDrawn >= maxLines - 1 ? truncateIntroText(line, 22) : line;
          ctx.fillText(remaining, x, y + linesDrawn * lineHeight);
        }
      };

      drawWrapped(
        titleLayout.lines.join(" "),
        formatMeta.paddingX,
        formatMeta.paddingTop + Math.max(68, Math.round(height * 0.12)),
        titleLayout.plateMaxWidth,
        titleLayout.lineHeight,
        titleLayout.lines.length,
        `900 ${titleLayout.fontSize}px Arial`,
        "#ffffff"
      );
      ctx.fillStyle = meta.accent;
      ctx.fillRect(formatMeta.paddingX, formatMeta.accentY, formatMeta.accentWidth, Math.max(4, Math.round(height * 0.011)));
      drawWrapped(
        safeContext,
        formatMeta.paddingX,
        formatMeta.contextY,
        width - formatMeta.paddingX * 2,
        formatMeta.contextLineHeight,
        formatMeta.contextMaxLines,
        `400 ${formatMeta.contextFontPx}px Arial`,
        "rgba(255,255,255,0.82)"
      );
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
      <foreignObject x="${formatMeta.paddingX}" y="${formatMeta.paddingTop + Math.max(28, Math.round(height * 0.06))}" width="${width - formatMeta.paddingX * 2}" height="${formatMeta.titleBoxHeight}">
        <div xmlns="http://www.w3.org/1999/xhtml" style="max-width:${titleLayout.plateMaxWidth}px;max-height:${titleLayout.maxPlateHeight}px;padding:${titleLayout.platePaddingY}px ${titleLayout.platePaddingX}px;box-sizing:border-box;font-family:Arial,Helvetica,sans-serif;font-size:${titleLayout.fontSize}px;line-height:${(titleLayout.lineHeight / titleLayout.fontSize).toFixed(2)};font-weight:900;color:white;text-transform:uppercase;letter-spacing:0.018em;overflow:hidden;display:grid;align-content:center;word-break:break-word;overflow-wrap:anywhere;background:linear-gradient(180deg, rgba(4,6,12,0.74) 0%, rgba(4,6,12,0.92) 100%);border:1px solid ${meta.accent}55;border-radius:18px;">
          ${titleLayout.lines.map((line) => `<div>${line}</div>`).join("")}
        </div>
      </foreignObject>
      <foreignObject x="${formatMeta.paddingX}" y="${formatMeta.contextY}" width="${width - formatMeta.paddingX * 2}" height="${formatMeta.contextBoxHeight}">
        <div xmlns="http://www.w3.org/1999/xhtml" style="font-family:Arial,Helvetica,sans-serif;font-size:${formatMeta.contextFontPx}px;line-height:${(formatMeta.contextLineHeight / formatMeta.contextFontPx).toFixed(2)};color:rgba(255,255,255,0.82);overflow:hidden;display:-webkit-box;-webkit-line-clamp:${formatMeta.contextMaxLines};-webkit-box-orient:vertical;word-break:break-word;">
          ${safeContext}
        </div>
      </foreignObject>
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
  return {
    ...normalized,
    refStatus,
    refShortLabel,
    refDetailsOpen: !!normalized?.refDetailsOpen,
    refHiddenProfile: normalized?.refHiddenProfile && typeof normalized.refHiddenProfile === "object" ? normalized.refHiddenProfile : null,
    refAnalysisError: refStatus === "error" ? String(normalized?.refAnalysisError || "").trim() : "",
    refAnalyzedAt: String(normalized?.refAnalyzedAt || "").trim(),
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
  const primaryRole = String(refs?.primaryRole || "").trim();
  if (primaryRole) normalized.primaryRole = primaryRole;
  if (Array.isArray(refs?.secondaryRoles)) {
    normalized.secondaryRoles = refs.secondaryRoles.map((role) => String(role || "").trim()).filter(Boolean);
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
  primaryRole = "",
  secondaryRoles = [],
  heroEntityId = "",
  supportEntityIds = [],
  mustAppear = [],
  mustNotAppear = [],
  environmentLock = null,
  styleLock = null,
  identityLock = null,
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

  const nodeSignals = {
    hasAudio: !!String(audioUrl || "").trim(),
    hasText: !!String(text || "").trim(),
    hasMode: !!String(mode || "").trim(),
    hasStylePreset: !!String(stylePreset || "").trim(),
    hasContinuity: !!String(continuity || "").trim(),
    hasPlannerMeta: !!(plannerMeta && typeof plannerMeta === 'object' && Object.keys(plannerMeta).length),
  };

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
    primaryRole: String(primaryRole || "").trim(),
    secondaryRoles: Array.isArray(secondaryRoles) ? secondaryRoles : undefined,
    heroEntityId: String(heroEntityId || "").trim(),
    supportEntityIds: Array.isArray(supportEntityIds) ? supportEntityIds : undefined,
    mustAppear: Array.isArray(mustAppear) ? mustAppear : undefined,
    mustNotAppear: Array.isArray(mustNotAppear) ? mustNotAppear : undefined,
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

function serializeNodesForStorage(nodes) {
  return nodes.map((n) => {
    const normalizedData = isComfyRefLikeNodeType(n.type)
      ? normalizeComfyRefNodeData(n.type, n.data || {}, n?.data?.kind || "")
      : (n.data || {});
    const data = stripFunctionsDeep(normalizedData) || {};
    if (n.type === "brainNode") {
      delete data.isParsing;
      delete data.activeParseToken;
    }
    if (n.type === "introFrame") {
      data.previewKind = getEffectiveIntroFramePreviewKind(data);
      if (data.previewKind === INTRO_FRAME_PREVIEW_KINDS.GENERATED_LOCAL) {
        delete data.imageUrl;
      }
      delete data.contextSummary;
      delete data.contextSceneCount;
      delete data.sourceNodeIds;
      delete data.sourceNodeTypes;
      delete data.titleContextNodeId;
      delete data.onField;
      delete data.onGenerate;
      delete data.onPickImage;
      delete data.onClearImage;
    }
    return {
      id: n.id,
      type: n.type,
      position: n.position,
      data,
    };
  });
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
  const maxFiles = kind === "ref_style" ? 1 : 5;
  const refsRaw = Array.isArray(data?.refs) ? data.refs : (data?.url ? [{ url: data.url, name: data?.name || "" }] : []);
  const refs = refsRaw
    .map((item) => ({ url: String(item?.url || "").trim(), name: String(item?.name || "").trim() }))
    .filter((item) => !!item.url);
  const canAddMore = refs.length < maxFiles;
  const refStatus = deriveRefNodeStatus(data);
  const isDraft = refStatus === "draft";
  const isError = refStatus === "error";
  const isReady = refStatus === "ready";
  const shortLabel = String(data?.refShortLabel || "").trim();
  const analysisError = String(data?.refAnalysisError || "").trim();
  const detailsOpen = !!data?.refDetailsOpen;
  const detailsLines = formatRefProfileDetails(data?.refHiddenProfile);
  const canToggleDetails = refStatus === "ready" && detailsLines.length > 0;

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
        className={`clipSB_nodeRef clipSB_nodeRef--${kind} ${isDraft ? "clipSB_nodeRefDraft" : ""} ${isError ? "clipSB_nodeRefError" : ""}`.trim()}
      >
        <div className="clipSB_refGrid" style={{ marginBottom: 10 }}>
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
        {isDraft ? <div className="clipSB_refWarningBadge">⚠ Нажмите «Добавить», чтобы подтвердить реф</div> : null}
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

        <div style={{ display: "flex", gap: 8 }}>
          <button className="clipSB_btn" onClick={openPicker} disabled={!canAddMore || !!data?.uploading || refStatus === "loading"}>
            {data?.uploading ? "Загрузка…" : "Загрузить фото"}
          </button>
          <button className="clipSB_btn" onClick={() => data?.onConfirmAdd?.(id)} disabled={!refs.length || !!data?.uploading || refStatus === "loading"}>
            {refStatus === "loading" ? "Анализ..." : "Добавить"}
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
            : "Загрузи до 5 фото, затем нажми «Добавить»"}
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
          <button className="clipSB_btn clipSB_btnSecondary" onClick={() => data?.onOpenScenario?.(id)} disabled={scenes.length === 0}>
            Сценарий
          </button>
          {isStale ? (
            <span className="clipSB_small" style={{ color: "#ffb86c", alignSelf: "center" }}>⚠ результат устарел</span>
          ) : null}
        </div>

        {scenes.length === 0 ? (
          <div className="clipSB_small">Пусто. Нажми «Разобрать» в BRAIN (бесплатно).</div>
        ) : (
          <div className="clipSB_planList">
            {scenes.map((s, idx) => (
              <div key={getScenarioSceneStableKey(s, idx)} className="clipSB_planRow">
                <div className="clipSB_planTime">{s.t0}s → {s.t1}s</div>
                <div className="clipSB_planText">{getSceneUiDescription(s)}</div>
              </div>
            ))}
          </div>
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
  const styleMeta = getIntroStyleMeta(data?.stylePreset || "cinematic");
  const previewFormat = normalizeIntroFramePreviewFormat(data?.previewFormat);
  const previewFormatMeta = getIntroFramePreviewFormatMeta(previewFormat);
  const durationSec = normalizeIntroDurationSec(data?.durationSec);
  const [durationDraft, setDurationDraft] = useState(
    () => String(durationSec).replace(".", ",")
  );

  useEffect(() => {
    setDurationDraft(String(durationSec).replace(".", ","));
  }, [durationSec]);

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
  const previewTitle = String(data?.title || "INTRO FRAME").trim() || "INTRO FRAME";
  const previewContext = String(data?.contextSummary || "Story preview").trim() || "Story preview";
  const previewContextShort = truncateIntroText(previewContext, 148);
  const contextClamp = previewFormat === INTRO_FRAME_PREVIEW_FORMATS.PORTRAIT ? 2 : 1;
  const statusLabel = status === "ready"
    ? "preview готов"
    : status === "generating" || status === "preview_generating"
      ? "собираем preview..."
      : "черновик";
  const selectedStylePreset = normalizeIntroStylePreset(data?.stylePreset || "cinematic");
  const selectedStyleMeta = getIntroStyleMeta(selectedStylePreset);
  const isGenerating = status === "generating" || status === "preview_generating";
  const debug = data?.debug && typeof data.debug === "object" ? data.debug : {};
  const activeCastRoles = Array.isArray(debug?.introActiveCastRoles) ? debug.introActiveCastRoles : [];
  const mustAppearRoles = Array.isArray(debug?.introMustAppear) ? debug.introMustAppear : [];
  const mustNotAppearRoles = Array.isArray(debug?.introMustNotAppear) ? debug.introMustNotAppear : [];
  const attachedReferenceParts = debug?.attachedReferenceParts && typeof debug.attachedReferenceParts === "object"
    ? debug.attachedReferenceParts
    : {};
  const attachedInlineRoles = Array.isArray(debug?.attachedInlineReferenceRoles) ? debug.attachedInlineReferenceRoles : [];
  const overlayDebug = debug?.overlay && typeof debug.overlay === "object" ? debug.overlay : {};
  const previewTitleLayout = useMemo(
    () => fitIntroPreviewTitleLayout({ title: previewTitle, previewFormatMeta }),
    [previewFormatMeta, previewTitle]
  );
  const detailRows = [
    activeCastRoles.length ? ["Active cast", activeCastRoles.join(", ")] : null,
    mustAppearRoles.length ? ["Must appear", mustAppearRoles.join(", ")] : null,
    mustNotAppearRoles.length ? ["Must not appear", mustNotAppearRoles.join(", ")] : null,
    attachedInlineRoles.length ? ["Inline refs", attachedInlineRoles.join(", ")] : null,
    Object.keys(attachedReferenceParts).length ? ["Attached parts", Object.entries(attachedReferenceParts).map(([role, count]) => `${role}:${count}`).join(" • ")] : null,
    overlayDebug?.titleRendered ? ["Overlay", `font ${overlayDebug?.fontSize || 0}px • ${Array.isArray(overlayDebug?.lines) ? overlayDebug.lines.length : 0} lines`] : null,
  ].filter(Boolean);

  return (
    <>
      <Handle type="target" position={Position.Left} id="story_context" className="clipSB_handle" style={handleStyle("intro_context", { top: 76 })} />
      <Handle type="target" position={Position.Left} id="title_context" className="clipSB_handle" style={handleStyle("text", { top: 140 })} />
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
                  <div className="clipSB_assemblyRow"><span>Режим</span><strong>{autoTitle ? "AUTO TITLE" : "MANUAL TITLE"}</strong></div>
                  <div className="clipSB_assemblyRow"><span>Style</span><strong>{styleMeta.label}</strong></div>
                  <div className="clipSB_assemblyRow"><span>Длительность</span><strong>{durationSec.toFixed(1)} сек</strong></div>
                  <div className="clipSB_assemblyRow"><span>Статус</span><strong>{statusLabel}</strong></div>
                </div>

                  <label>
                    <div className="clipSB_hint" style={{ marginBottom: 6 }}>Title</div>
                    <input
                      className="clipSB_input"
                      value={String(data?.title || "")}
                      onChange={(e) => data?.onField?.(id, "title", e.target.value)}
                      placeholder="Введите заголовок или используйте auto title"
                    />
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
                      <div className="clipSB_hint" style={{ marginBottom: 6 }}>Preview</div>
                      <select
                        className="clipSB_select"
                        value={previewFormat}
                        onChange={(e) => data?.onField?.(id, "previewFormat", e.target.value)}
                        style={{ minHeight: 42 }}
                      >
                        {INTRO_FRAME_PREVIEW_FORMAT_OPTIONS.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
                      </select>
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

                  <div
                    className="clipSB_introInfoBar clipSB_small"
                    title={previewContext}
                  >
                    Story context: {previewContextShort || "не подключён"}
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
                        Avoid: {selectedStyleMeta.forbidden.join(" • ")}
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
                  className="clipSB_introFramePreviewBox"
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
                        <div
                          style={{
                            alignSelf: "start",
                            width: "100%",
                            maxWidth: previewFormat === INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE ? "72%" : "100%",
                            maxHeight: `${previewTitleLayout.maxPlateHeight}px`,
                            padding: `${previewTitleLayout.platePaddingY}px ${previewTitleLayout.platePaddingX}px`,
                            borderRadius: 18,
                            background: "linear-gradient(180deg, rgba(4,6,12,0.74) 0%, rgba(4,6,12,0.92) 100%)",
                            border: `1px solid ${styleMeta.accent}55`,
                            boxShadow: `0 10px 30px ${styleMeta.accent}30, inset 0 1px 0 rgba(255,255,255,0.08)`,
                            overflow: "hidden",
                          }}
                        >
                          <div
                            style={{
                              fontSize: previewTitleLayout.fontSize,
                              lineHeight: `${previewTitleLayout.lineHeight}px`,
                              fontWeight: 900,
                              letterSpacing: "0.018em",
                              textTransform: "uppercase",
                              color: "#ffffff",
                              textShadow: "0 2px 0 rgba(0,0,0,0.32), 0 0 18px rgba(255,255,255,0.12)",
                              overflow: "hidden",
                              display: "grid",
                              gap: 2,
                              wordBreak: "break-word",
                              overflowWrap: "anywhere",
                            }}
                          >
                            {previewTitleLayout.lines.map((line, index) => (
                              <span key={`${line}-${index}`}>{line}</span>
                            ))}
                          </div>
                        </div>
                        <div style={{ width: previewFormat === INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE ? "72%" : "100%", height: 4, borderRadius: 999, background: `linear-gradient(90deg, ${styleMeta.accent} 0%, ${styleMeta.secondary} 100%)`, opacity: 0.95 }} />
                        <div
                          className="clipSB_small"
                          style={{
                            color: "rgba(245,247,255,0.84)",
                            overflow: "hidden",
                            display: "-webkit-box",
                            WebkitBoxOrient: "vertical",
                            WebkitLineClamp: contextClamp,
                            lineHeight: 1.4,
                            minWidth: 0,
                            wordBreak: "break-word",
                            maxWidth: previewFormat === INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE ? "76%" : "100%",
                          }}
                        >
                          {previewContext}
                        </div>
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

  return (
    <>
      <Handle type="target" position={Position.Left} id="assembly_in" className="clipSB_handle" style={handleStyle("assembly")} />
      <Handle type="target" position={Position.Left} id="assembly_intro" className="clipSB_handle" style={handleStyle("intro_to_assembly", { top: 132 })} />
      <NodeShell
        title="ASSEMBLY"
        onClose={() => data?.onRemoveNode?.(id)}
        icon={<span aria-hidden>🎬</span>}
        className="clipSB_nodeAssembly"
      >
        <div className="clipSB_assemblyStats">
          <div className="clipSB_assemblyRow"><span>Сцен готово</span><strong>{data?.readyScenes || 0}/{data?.totalScenes || 0}</strong></div>
          <div className="clipSB_assemblyRow"><span>Источник</span><strong>{data?.sourceLabel || "НЕ ПОДКЛЮЧЕНО"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Intro</span><strong>{data?.introLabel || "НЕТ INTRO"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Аудио</span><strong>{data?.hasAudio ? "подключено" : "не подключено"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Формат</span><strong>{data?.format || "9:16"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Длительность</span><strong>~{Math.round(Number(data?.durationSec || 0))} сек</strong></div>
        </div>

        <div className="clipSB_small" style={{ marginTop: 8 }}>
          Main source: <b>{data?.sourceLabel || "НЕ ПОДКЛЮЧЕНО"}</b> · Intro: <b>{data?.introLabel || "НЕТ INTRO"}</b>
        </div>
        {data?.debugSummary ? (
          <div className="clipSB_selectHint" style={{ marginTop: 6 }}>
            trace: {data.debugSummary}
          </div>
        ) : null}
        {result ? (
          <div className="clipSB_selectHint" style={{ marginTop: 6 }}>
            result: scenes={resultSceneCount}; intro={result?.introIncluded ? "yes" : "no"}; totalSegments={resultTotalSegments || resultSceneCount}; totalSteps={Number(data?.assemblyStageTotal || 0) || Number(result?.totalSteps || 0) || 0}; introDuration={Number(result?.introDurationSec || 0) || 0}
          </div>
        ) : null}

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
});
const [comfyEditor, setComfyEditor] = useState({
  open: false,
  nodeId: null,
  selected: 0,
});

// Open scenario overlay from node button (custom event)
useEffect(() => {
  const handler = (e) => {
    const nodeId = e?.detail?.nodeId || null;
    setScenarioEditor((s) => ({
      ...s,
      open: true,
      nodeId,
      selected: 0,
    }));
  };

  window.addEventListener("ps:clipOpenScenario", handler);
  return () => window.removeEventListener("ps:clipOpenScenario", handler);
}, []);

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
  const [edges, setEdges, onEdgesChange] = useEdgesState(defaultEdges);

  useEffect(() => {
    nodesCountRef.current = nodes.length;
  }, [nodes.length]);

const scenarioNode = useMemo(() => {
  if (scenarioEditor.nodeId) return nodes.find((n) => n.id === scenarioEditor.nodeId) || null;
  return nodes.find((n) => n.type === "storyboardNode") || null;
}, [nodes, scenarioEditor.nodeId]);

const scenarioBrainRefs = useMemo(() => {
  if (!scenarioNode?.id) return { character: [], location: [], style: [], props: [] };
  const incomingPlanEdge = [...edges]
    .reverse()
    .find((e) => e.target === scenarioNode.id && (e.targetHandle || "") === "plan_in");
  if (!incomingPlanEdge?.source) return { character: [], location: [], style: [], props: [] };
  const brainInput = collectBrainPlannerInput({ brainNodeId: incomingPlanEdge.source, nodesList: nodes, edgesList: edges });
  const brainNode = nodes.find((n) => n.id === incomingPlanEdge.source);
  const planRefs = brainNode?.data?.scenePlan?.refs || {};
  return {
    character: Array.isArray(planRefs.character) ? planRefs.character : brainInput.characterRefs,
    location: Array.isArray(planRefs.location) ? planRefs.location : brainInput.locationRefs,
    style: Array.isArray(planRefs.style) ? planRefs.style : brainInput.styleRefs,
    props: Array.isArray(planRefs.props) ? planRefs.props : brainInput.propsRefs,
    propAnchorLabel: planRefs.propAnchorLabel,
    sessionCharacterAnchor: planRefs.sessionCharacterAnchor,
    sessionLocationAnchor: planRefs.sessionLocationAnchor,
    sessionStyleAnchor: planRefs.sessionStyleAnchor,
    sessionBaseline: planRefs.sessionBaseline,
  };
}, [edges, nodes, scenarioNode?.id]);

const scenarioScenes = useMemo(() => {
  const arr = scenarioNode?.data?.scenes;
  return normalizeSceneCollectionWithSceneId(arr, "scene");
}, [scenarioNode]);

const recommendedNextSceneIndex = useMemo(() => {
  if (!Array.isArray(scenarioScenes) || scenarioScenes.length === 0) return -1;
  const currentIdx = Number.isFinite(scenarioEditor.selected) ? scenarioEditor.selected : -1;
  return scenarioScenes.findIndex((scene, idx) => idx > currentIdx && !String(scene?.videoUrl || "").trim());
}, [scenarioEditor.selected, scenarioScenes]);

const scenarioSelected = scenarioScenes[scenarioEditor.selected] || null;
const scenarioSelectedTransitionType = resolveSceneTransitionType(scenarioSelected);
const scenarioSelectedIsLipSync = isLipSyncScene(scenarioSelected);
const scenarioPreviousScene = scenarioEditor.selected > 0 ? scenarioScenes[scenarioEditor.selected - 1] : null;
const scenarioSelectedCanInheritPreviousEnd = scenarioSelectedTransitionType === "continuous"
  && !!scenarioPreviousScene
  && !!String(scenarioPreviousScene?.endImageUrl || "").trim();
const scenarioSelectedEffectiveStartImageUrl = getEffectiveSceneStartImage(scenarioSelected, scenarioPreviousScene);
const scenarioSelectedVideoSourceImageUrl = String(scenarioSelected?.videoSourceImageUrl || "").trim();
const scenarioSelectedVideoPanelActivated = !!scenarioSelected?.videoPanelActivated;
const scenarioSelectedStartImageSource = getSceneStartImageSource(scenarioSelected, scenarioPreviousScene);
const scenarioSelectedImageFormat = normalizeSceneImageFormat(scenarioSelected?.imageFormat);
const scenarioSelectedIndexLabel = Number.isFinite(scenarioEditor.selected) ? scenarioEditor.selected + 1 : 0;
const scenarioSelectedT0 = Number(scenarioSelected?.t0 ?? scenarioSelected?.start ?? 0);
const scenarioSelectedT1 = Number(scenarioSelected?.t1 ?? scenarioSelected?.end ?? 0);
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
const scenarioPreviousSceneImageSource = scenarioPreviousScene?.endImageUrl
  ? "endImageUrl"
  : scenarioPreviousScene?.imageUrl
    ? "imageUrl"
    : scenarioPreviousScene?.startImageUrl
      ? "startImageUrl"
      : "none";
const scenarioHasImageForVideo = scenarioSelectedTransitionType === "continuous"
  ? !!(scenarioSelectedEffectiveStartImageUrl || scenarioSelected?.endImageUrl || scenarioSelected?.imageUrl)
  : !!scenarioSelected?.imageUrl;
const scenarioCanShowAddToVideoButton = scenarioHasImageForVideo && !scenarioSelectedVideoPanelActivated;

const comfyNode = useMemo(() => {
  if (comfyEditor.nodeId) return nodes.find((n) => n.id === comfyEditor.nodeId && n.type === 'comfyStoryboard') || null;
  return nodes.find((n) => n.type === 'comfyStoryboard') || null;
}, [nodes, comfyEditor.nodeId]);

useEffect(() => {
  console.log("[COMFY DEBUG FRONT] comfyStoryboard plannerMeta plannerInput refsByRole", comfyNode?.data?.plannerMeta?.plannerInput?.refsByRole);
}, [comfyNode]);

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
const comfySelectedStartSec = Number(comfySelectedScene?.audioSliceStartSec ?? comfySelectedScene?.audioSliceT0 ?? comfySelectedScene?.startSec ?? comfySelectedScene?.start ?? comfySelectedScene?.t0 ?? 0);
const comfySelectedEndSec = Number(comfySelectedScene?.audioSliceEndSec ?? comfySelectedScene?.audioSliceT1 ?? comfySelectedScene?.endSec ?? comfySelectedScene?.end ?? comfySelectedScene?.t1 ?? comfySelectedStartSec);
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
  }, [scenarioEditor.selected, scenarioSelected?.sceneId]);

  useEffect(() => {
    if (!scenarioEditor.open) return;
    if (!scenarioScenes.length) return;
    const maxIdx = scenarioScenes.length - 1;
    if (scenarioEditor.selected < 0 || scenarioEditor.selected > maxIdx) {
      setScenarioEditor((prev) => ({ ...prev, selected: 0 }));
    }
  }, [scenarioEditor.open, scenarioEditor.selected, scenarioScenes.length]);

  useEffect(() => {
    if (!scenarioEditor.open) return;
    const node = scenarioItemRefs.current.get(scenarioEditor.selected);
    if (!node) return;
    try {
      node.scrollIntoView({ block: "nearest", behavior: "smooth" });
    } catch {
      node.scrollIntoView();
    }
  }, [scenarioEditor.open, scenarioEditor.selected]);

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

  const updateScenarioScene = useCallback((idx, patch) => {
    if (!scenarioNode?.id || idx < 0) return;
    setNodes((prev) => prev.map((n) => {
      if (n.id !== scenarioNode.id) return n;
      const scenes = Array.isArray(n?.data?.scenes) ? n.data.scenes : [];
      if (!scenes[idx]) return n;
      const nextScenes = scenes.map((s, i) => (i === idx ? { ...s, ...patch } : s));
      return { ...n, data: { ...n.data, scenes: nextScenes } };
    }));
  }, [scenarioNode?.id, setNodes]);

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
    scenarioVideoJobsBySceneRef.current.delete(key);
    persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
    stopScenarioVideoPolling(key);
    const terminalStatus = String(meta?.status || "").toLowerCase();
    if (terminalStatus) {
      console.info("[CLIP TRACE] active job cleared after terminal status", {
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
    const startMeta = {
      ...jobMeta,
      sceneId,
      startedAt: Number(jobMeta?.startedAt) || now,
      updatedAt: Number(jobMeta?.updatedAt) || now,
      status: String(jobMeta?.status || "queued").toLowerCase(),
    };
    const prevMeta = scenarioVideoJobsBySceneRef.current.get(sceneId);
    if (prevMeta?.jobId && String(prevMeta.jobId) !== String(startMeta.jobId)) {
      console.info("[CLIP TRACE] active job replaced", {
        scope: "scenario",
        sceneId,
        oldJobId: String(prevMeta.jobId || ""),
        newJobId: String(startMeta.jobId || ""),
      });
    }
    scenarioVideoJobsBySceneRef.current.set(sceneId, startMeta);
    persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
    updateScenarioScene(scenarioScenes.findIndex((x) => String(x?.sceneId || "") === sceneId), {
      videoJobId: startMeta.jobId,
      videoStatus: startMeta.status,
      videoError: "",
    });
    stopScenarioVideoPolling(sceneId);

    console.info("[CLIP VIDEO POLLING START]", {
      sceneId,
      jobId: String(startMeta.jobId || ""),
      provider: String(startMeta.provider || ""),
    });

    const scheduleScenarioPoll = (delayMs, reason) => {
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
      if (lastUpdatedAt > 0 && (Date.now() - lastUpdatedAt) > staleTimeoutMs) {
        updateScenarioScene(scenarioScenes.findIndex((x) => String(x?.sceneId || "") === sceneId), {
          videoStatus: "error",
          videoError: "video_job_stale_timeout",
        });
        clearActiveVideoJob(sceneId);
        return;
      }
      try {
        const activeMeta = scenarioVideoJobsBySceneRef.current.get(sceneId);
        if (!activeMeta?.jobId) return;
        const out = await fetchJson(`/api/clip/video/status/${encodeURIComponent(activeMeta.jobId)}`, { method: "GET" });
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

        const idx = scenarioScenes.findIndex((x) => String(x?.sceneId || "") === sceneId);
        const prevNotFoundCount = Number(activeMeta?.notFoundCount) || 0;
        if (!out?.ok && isScenarioVideoJobNotFound(out)) {
          const nextNotFoundCount = prevNotFoundCount + 1;
          const toleratedMeta = { ...nextMeta, notFoundCount: nextNotFoundCount, status: "running" };
          scenarioVideoJobsBySceneRef.current.set(sceneId, toleratedMeta);
          persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
          if (nextNotFoundCount < notFoundRetryLimit) {
            updateScenarioScene(idx, {
              videoStatus: "running",
              videoError: "",
              videoJobId: toleratedMeta.jobId,
            });
            scheduleScenarioPoll(2200, "status_not_found_retry");
            return;
          }
          updateScenarioScene(idx, {
            videoStatus: "not_found",
            videoError: String(out?.hint || out?.code || "video_job_not_found"),
            videoJobId: toleratedMeta.jobId,
          });
          clearActiveVideoJob(sceneId, { status: "not_found", jobId: toleratedMeta.jobId });
          return;
        }
        if (!out?.ok) throw new Error(out?.hint || out?.code || "video_status_failed");

        const settledMeta = { ...nextMeta, notFoundCount: 0 };
        scenarioVideoJobsBySceneRef.current.set(sceneId, settledMeta);
        persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));

        if (status === "not_found") {
          const nextNotFoundCount = prevNotFoundCount + 1;
          const toleratedMeta = { ...settledMeta, notFoundCount: nextNotFoundCount, status: "running" };
          scenarioVideoJobsBySceneRef.current.set(sceneId, toleratedMeta);
          persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
          if (nextNotFoundCount < notFoundRetryLimit) {
            updateScenarioScene(idx, {
              videoStatus: "running",
              videoJobId: toleratedMeta.jobId,
              videoError: "",
            });
            scheduleScenarioPoll(2200, "status_not_found_retry");
            return;
          }
          updateScenarioScene(idx, {
            videoStatus: "not_found",
            videoJobId: toleratedMeta.jobId,
            videoError: String(out?.error || out?.hint || "video_job_not_found"),
          });
          clearActiveVideoJob(sceneId, { status: "not_found", jobId: toleratedMeta.jobId });
          return;
        }

        updateScenarioScene(idx, {
          videoStatus: status,
          videoJobId: settledMeta.jobId,
          videoError: status === "error" || status === "stopped" ? String(out?.error || out?.hint || "video_job_failed") : "",
        });
        console.info("[CLIP VIDEO STATUS APPLIED]", {
          sceneId,
          jobId: String(settledMeta?.jobId || ""),
          status,
          videoUrl: String(out?.videoUrl || ""),
          error: status === "error" || status === "stopped" ? String(out?.error || out?.hint || "video_job_failed") : "",
        });
        console.info("[VIDEO STATUS APPLIED]", {
          scope: "scenario",
          sceneId,
          jobId: String(settledMeta?.jobId || ""),
          status,
          ok: !!out?.ok,
        });

        if (status === "done") {
          if (idx === -1) {
            console.warn("[CLIP WARN] scene idx not found for done status", {
              scope: "scenario",
              sceneId,
              jobId: String(settledMeta?.jobId || ""),
            });
          } else {
            updateScenarioScene(idx, {
              videoUrl: String(out?.videoUrl || ""),
              mode: String(out?.mode || ""),
              model: String(out?.model || ""),
              requestedDurationSec: normalizeDurationSec(out?.requestedDurationSec),
              providerDurationSec: normalizeDurationSec(out?.providerDurationSec),
              videoStatus: "done",
              videoError: "",
              videoJobId: settledMeta.jobId,
            });
            console.info("[CLIP TRACE] scene updated after done", {
              scope: "scenario",
              sceneId,
              idx,
              videoUrl: String(out?.videoUrl || ""),
              videoStatus: "done",
              videoJobId: String(settledMeta?.jobId || ""),
            });
          }
          clearActiveVideoJob(sceneId, { status: "done", jobId: settledMeta.jobId });
          return;
        }

        if (status === "error" || status === "stopped" || status === "not_found") {
          clearActiveVideoJob(sceneId, { status, jobId: settledMeta.jobId });
          return;
        }

        scheduleScenarioPoll(1800, "status_running");
      } catch (e) {
        const errMsg = String(e?.message || "").toLowerCase();
        const idx = scenarioScenes.findIndex((x) => String(x?.sceneId || "") === sceneId);
        if (errMsg.includes("job_id_not_found_or_expired")) {
          const activeMeta = scenarioVideoJobsBySceneRef.current.get(sceneId) || {};
          const nextNotFoundCount = (Number(activeMeta?.notFoundCount) || 0) + 1;
          const toleratedMeta = { ...activeMeta, notFoundCount: nextNotFoundCount, status: "running", updatedAt: Date.now() };
          scenarioVideoJobsBySceneRef.current.set(sceneId, toleratedMeta);
          persistActiveVideoJob(Object.fromEntries(scenarioVideoJobsBySceneRef.current.entries()));
          if (nextNotFoundCount < notFoundRetryLimit) {
            updateScenarioScene(idx, {
              videoStatus: "running",
              videoError: "",
              videoJobId: String(toleratedMeta?.jobId || ""),
            });
            scheduleScenarioPoll(2400, "exception_not_found_retry");
            return;
          }
          updateScenarioScene(idx, {
            videoStatus: "not_found",
            videoError: String(e?.message || e),
            videoJobId: String(toleratedMeta?.jobId || ""),
          });
          clearActiveVideoJob(sceneId, { status: "not_found", jobId: toleratedMeta.jobId });
          return;
        }
        updateScenarioScene(idx, {
          videoStatus: "running",
          videoError: "",
        });
        scheduleScenarioPoll(2400, "exception_retry");
      }
    };

    scheduleScenarioPoll(250, "initial_after_start");
  }, [clearActiveVideoJob, isScenarioVideoJobNotFound, persistActiveVideoJob, scenarioScenes, stopScenarioVideoPolling, updateScenarioScene]);

  useEffect(() => () => stopScenarioVideoPolling(), [stopScenarioVideoPolling]);

  useEffect(() => {
    const raw = safeGet(VIDEO_JOB_STORE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      const entries = parsed?.jobId
        ? [[String(parsed?.sceneId || ""), parsed]]
        : (parsed && typeof parsed === "object" ? Object.entries(parsed) : []);
      const staleTimeoutMs = 20 * 60 * 1000;
      console.info("[CLIP STORAGE] restore scenario video jobs", {
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
        const sceneVideoUrl = String(sceneNow?.videoUrl || "").trim();
        const sceneVideoJobId = String(sceneNow?.videoJobId || "").trim();
        const persistedJobId = String(meta?.jobId || "").trim();
        if (sceneVideoUrl || (sceneVideoJobId && sceneVideoJobId !== persistedJobId)) {
          console.info("[CLIP TRACE] stale persisted job ignored", {
            scope: "scenario",
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
        console.info("[CLIP TRACE] active job restored", {
          scope: "scenario",
          sceneId: normalizedSceneId,
          jobId: persistedJobId,
        });
        startScenarioVideoPolling({ ...meta, sceneId: normalizedSceneId });
      });
      persistActiveVideoJob(nextPersisted);
    } catch {
      safeDel(VIDEO_JOB_STORE_KEY);
    }
  }, [VIDEO_JOB_STORE_KEY, accountKey, persistActiveVideoJob, scenarioScenes, startScenarioVideoPolling, updateScenarioScene]);

  const updateComfyScene = useCallback((idx, patch) => {
    if (!comfyNode?.id || idx < 0) return;
    setNodes((prev) => prev.map((n) => {
      if (n.id !== comfyNode.id) return n;
      const scenes = Array.isArray(n?.data?.mockScenes) ? n.data.mockScenes : [];
      if (!scenes[idx]) return n;
      const nextScenes = scenes.map((scene, sceneIdx) => (sceneIdx === idx ? { ...scene, ...patch } : scene));
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
        const out = await fetchJson(`/api/clip/video/status/${encodeURIComponent(activeMeta.jobId)}`, { method: "GET" });
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
        fetchJson(`/api/clip/video/status/${encodeURIComponent(persistedJobId)}`, { method: "GET" })
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
      const plannerInput = {
        text: liveDerived?.meaningfulText || "",
        audioUrl: liveDerived?.meaningfulAudio || "",
        audioDurationSec: liveDerived?.meaningfulAudioDurationSec || null,
        refsByRole: liveDerived?.refsByRole || {},
        mode: liveDerived?.modeValue || String(deriveNode?.data?.mode || "clip"),
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

${contextPrompt}`.trim(),
          sceneText: contextPrompt,
          style: String(plannerInput?.stylePreset || comfyNode?.data?.stylePreset || 'realism'),
          width: 1024,
          height: 1792,
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
      updateComfyScene(comfySafeIndex, { imageUrl: String(out.imageUrl || '') });
    } catch (e) {
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
      const out = await fetchJson('/api/clip/video/start', {
        method: 'POST',
        body: {
          sceneId,
          imageUrl: String(comfySceneSnapshot.imageUrl || ''),
          videoPrompt: syncedVideoPrompt,
          transitionActionPrompt: contextPrompt,
          requestedDurationSec: Number(comfySceneSnapshot.generationDurationSec) || Math.ceil(Number(comfySceneSnapshot.durationSec) || 3),
          shotType: String(comfySceneSnapshot.sceneNarrativeStep || ''),
          sceneType: String(comfySceneSnapshot.sceneGoal || ''),
          format: '9:16',
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
          const immediateOut = await fetchJson(`/api/clip/video/status/${encodeURIComponent(startedMeta.jobId)}`, { method: "GET" });
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
          imageUrl: String(comfySelectedScene.imageUrl || ''),
          videoPrompt: syncedVideoPrompt,
          transitionActionPrompt: contextPrompt,
          requestedDurationSec: Number(comfySelectedScene.generationDurationSec) || Math.ceil(Number(comfySelectedScene.durationSec) || 3),
          shotType: String(comfySelectedScene.sceneNarrativeStep || ''),
          sceneType: String(comfySelectedScene.sceneGoal || ''),
          format: '9:16',
          provider: 'comfy_remote',
        },
      });
      if (!legacyOut?.ok || !legacyOut?.videoUrl) throw new Error(legacyOut?.hint || legacyOut?.code || 'video_generation_failed');
      updateComfyScene(comfySafeIndex, { videoUrl: String(legacyOut.videoUrl || ''), videoPanelOpen: true, videoStatus: 'done', videoError: '', videoJobId: '' });
    } catch (e) {
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
        },
      });
      if (!out?.ok || !out?.audioSliceUrl) throw new Error(out?.hint || out?.code || "audio_slice_failed");
      const outStartSec = Number(out?.startSec ?? out?.t0 ?? startSec);
      const outEndSec = Number(out?.endSec ?? out?.t1 ?? endSec);
      const durationSec = normalizeDurationSec(out?.durationSec ?? out?.audioSliceBackendDurationSec ?? out?.duration);
      const nextExpectedDuration = Math.max(0, outEndSec - outStartSec);
      updateComfyScene(comfySafeIndex, {
        audioSliceUrl: String(out.audioSliceUrl || ""),
        audioSliceStartSec: outStartSec,
        audioSliceEndSec: outEndSec,
        audioSliceDurationSec: durationSec ?? nextExpectedDuration,
        audioSliceStatus: "ready",
        audioSliceError: "",
        audioSliceT0: outStartSec,
        audioSliceT1: outEndSec,
        audioSliceExpectedDurationSec: nextExpectedDuration,
        audioSliceBackendDurationSec: durationSec,
        audioSliceActualDurationSec: null,
        audioSliceLoadError: "",
        speechSafeAdjusted: Boolean(out?.speechSafeAdjusted),
        speechSafeShiftMs: Number(out?.speechSafeShiftMs ?? 0),
        sliceMayCutSpeech: Boolean(out?.sliceMayCutSpeech),
      });
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

  const handleGenerateScenarioImage = useCallback(async (slot = "single") => {
    if (!scenarioSelected) return;

    const transitionType = resolveSceneTransitionType(scenarioSelected);
    const normalizedSlot = slot === "start" || slot === "end" ? slot : "single";
    if (transitionType === "continuous" && normalizedSlot === "start" && !!scenarioSelected?.inheritPreviousEndAsStart) {
      return;
    }
    const sceneId = String(scenarioSelected?.sceneId || "").trim();
    if (!sceneId) throw new Error("scene_id_required");
    const sceneText = String(scenarioSelected.sceneText || scenarioSelected.visualDescription || "").trim();
    const previousScene = scenarioEditor.selected > 0 ? scenarioScenes[scenarioEditor.selected - 1] : null;
    const previousSceneImageUrl = String(
      previousScene?.endImageUrl
      || previousScene?.imageUrl
      || previousScene?.startImageUrl
      || ""
    ).trim();
    const previousContinuityMemory = scenarioSelected.previousContinuityMemory
      || previousScene?.continuityMemory
      || null;
    const imageFormat = normalizeSceneImageFormat(scenarioSelected.imageFormat);
    const { width, height } = getSceneImageSize(imageFormat);

    let sceneDelta = "";
    if (transitionType === "continuous" && normalizedSlot === "start") {
      sceneDelta = String(scenarioSelected.startFramePrompt || scenarioSelected.imagePrompt || scenarioSelected.prompt || "").trim();
    } else if (transitionType === "continuous" && normalizedSlot === "end") {
      sceneDelta = String(scenarioSelected.endFramePrompt || scenarioSelected.imagePrompt || scenarioSelected.prompt || "").trim();
    } else {
      sceneDelta = getScenePrimaryFramePrompt(scenarioSelected);
    }

    if (!sceneDelta) {
      setScenarioImageError("Добавьте prompt для генерации кадра");
      return;
    }

    setScenarioImageLoading(true);
    setScenarioImageError("");
    try {
      const out = await fetchJson(`/api/clip/image`, {
        method: "POST",
        body: {
          sceneId,
          sceneDelta: `${sceneDelta}
Aspect ratio: ${imageFormat}`,
          sceneText,
          width,
          height,
          refs: normalizeClipImageRefsPayload({
            ...scenarioBrainRefs,
            previousContinuityMemory,
            previousSceneImageUrl,
          }),
        },
      });
      if (!out?.ok || !out?.imageUrl) throw new Error(out?.hint || out?.code || "image_generation_failed");

      const generatedImageUrl = String(out.imageUrl || "");
      if (transitionType === "continuous" && normalizedSlot === "start") {
        updateScenarioScene(scenarioEditor.selected, {
          startImageUrl: generatedImageUrl,
          imageFormat,
          videoUrl: "",
          videoStatus: "",
          videoError: "",
          videoJobId: "",
          videoSourceImageUrl: "",
          videoPanelActivated: false,
        });
      } else if (transitionType === "continuous" && normalizedSlot === "end") {
        updateScenarioScene(scenarioEditor.selected, {
          endImageUrl: generatedImageUrl,
          imageFormat,
          videoUrl: "",
          videoStatus: "",
          videoError: "",
          videoJobId: "",
          videoSourceImageUrl: "",
          videoPanelActivated: false,
        });
      } else {
        updateScenarioScene(scenarioEditor.selected, {
          imageUrl: generatedImageUrl,
          imageFormat,
          videoUrl: "",
          videoStatus: "",
          videoError: "",
          videoJobId: "",
          videoSourceImageUrl: "",
          videoPanelActivated: false,
        });
      }
      clearActiveVideoJob(sceneId);
      setScenarioVideoOpen(false);
      console.log("[StoryboardVideo] image_generated_reset_video_stage", {
        sceneId,
        slot: normalizedSlot,
      });
    } catch (e) {
      console.error(e);
      setScenarioImageError(String(e?.message || e));
    } finally {
      setScenarioImageLoading(false);
    }
  }, [clearActiveVideoJob, scenarioSelected, scenarioEditor.selected, scenarioScenes, scenarioBrainRefs, updateScenarioScene]);

  const handleClearScenarioImage = useCallback((slot = "single") => {
    setScenarioImageError("");
    const transitionType = resolveSceneTransitionType(scenarioSelected);
    const normalizedSlot = slot === "start" || slot === "end" ? slot : "single";
    if (transitionType === "continuous" && normalizedSlot === "start" && !!scenarioSelected?.inheritPreviousEndAsStart) {
      return;
    }
    if (transitionType === "continuous" && normalizedSlot === "start") {
      updateScenarioScene(scenarioEditor.selected, {
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
      updateScenarioScene(scenarioEditor.selected, {
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
    updateScenarioScene(scenarioEditor.selected, {
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
  }, [clearActiveVideoJob, scenarioEditor.selected, scenarioSelected, updateScenarioScene]);

  const handleScenarioTakeAudioByIndex = useCallback(async (idx) => {
    const scene = scenarioScenes[idx] || null;
    if (!scene) return;
    if (!globalAudioUrlRaw) {
      const msg = "Не найден общий audioUrl в Audio node";
      updateScenarioScene(idx, {
        audioSliceStatus: "error",
        audioSliceError: msg,
        audioSliceLoadError: msg,
      });
      if (idx === scenarioEditor.selected) setScenarioVideoError(msg);
      return;
    }

    const sceneId = String(scene?.sceneId || "").trim();
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
      const out = await fetchJson("/api/clip/audio/slice", {
        method: "POST",
        body: {
          sceneId,
          startSec,
          endSec,
          audioUrl: globalAudioUrlRaw,
          audioStoryMode: String(scenarioSelected?.audioStoryMode || ""),
        },
      });
      if (!out?.ok || !out?.audioSliceUrl) throw new Error(out?.hint || out?.code || "audio_slice_failed");
      const outStartSec = Number(out?.startSec ?? out?.t0 ?? startSec);
      const outEndSec = Number(out?.endSec ?? out?.t1 ?? endSec);
      const durationSec = normalizeDurationSec(out?.durationSec ?? out?.audioSliceBackendDurationSec ?? out?.duration);
      const expectedDuration = Math.max(0, outEndSec - outStartSec);
      updateScenarioScene(idx, {
        audioSliceUrl: String(out.audioSliceUrl || ""),
        audioSliceStartSec: outStartSec,
        audioSliceEndSec: outEndSec,
        audioSliceDurationSec: durationSec ?? expectedDuration,
        audioSliceStatus: "ready",
        audioSliceError: "",
        audioSliceT0: outStartSec,
        audioSliceT1: outEndSec,
        audioSliceExpectedDurationSec: expectedDuration,
        audioSliceBackendDurationSec: durationSec,
        audioSliceActualDurationSec: null,
        audioSliceLoadError: "",
        speechSafeAdjusted: Boolean(out?.speechSafeAdjusted),
        speechSafeShiftMs: Number(out?.speechSafeShiftMs ?? 0),
        sliceMayCutSpeech: Boolean(out?.sliceMayCutSpeech),
      });
    } catch (e) {
      console.error(e);
      const msg = String(e?.message || e || "audio_slice_failed");
      updateScenarioScene(idx, {
        audioSliceStatus: "error",
        audioSliceError: msg,
        audioSliceLoadError: msg,
      });
      if (idx === scenarioEditor.selected) setScenarioVideoError(msg);
    } finally {
      if (idx === scenarioEditor.selected) setScenarioAudioSliceLoading(false);
    }
  }, [globalAudioUrlRaw, scenarioEditor.selected, scenarioScenes, scenarioSelected?.audioStoryMode, updateScenarioScene]);

  const handleScenarioVideoTakeAudio = useCallback(async () => {
    if (!scenarioSelected) return;
    await handleScenarioTakeAudioByIndex(scenarioEditor.selected);
  }, [handleScenarioTakeAudioByIndex, scenarioEditor.selected, scenarioSelected]);

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
      setScenarioEditor((prev) => ({ ...prev, selected: nextIdx }));
      return;
    }

    setAssemblyInfo("Все сцены готовы. Можно собирать клип.");
  }, [scenarioScenes]);

  const handleScenarioGenerateVideo = useCallback(async () => {
    const transitionType = resolveSceneTransitionType(scenarioSelected);
    const frameImageUrl = String(scenarioSelected?.imageUrl || "").trim();
    const effectiveStartImageUrl = String(scenarioSelectedEffectiveStartImageUrl || "").trim();
    const endImageUrl = String(scenarioSelected?.endImageUrl || "").trim();
    const hasImageForVideo = transitionType === "continuous"
      ? !!(effectiveStartImageUrl || endImageUrl || frameImageUrl)
      : !!frameImageUrl;

    if (!hasImageForVideo) return;
    const effectiveLipSync = isLipSyncScene(scenarioSelected);
    const effectiveRenderMode = scenarioSelected?.renderMode || (effectiveLipSync ? "avatar_lipsync" : "standard_video");
    const effectiveVideoProvider = String(scenarioSelected?.sceneRenderProvider || "kie").trim().toLowerCase() === "comfy_remote" ? "comfy_remote" : "kie";

    if (effectiveLipSync && !scenarioSelected?.audioSliceUrl) {
      setScenarioVideoError("Для lipSync сначала возьмите аудио");
      return;
    }

    const sceneId = String(scenarioSelected?.sceneId || "").trim();
    if (!sceneId) throw new Error("scene_id_required");
    const t0 = Number(scenarioSelected.t0 ?? scenarioSelected.start ?? 0);
    const t1 = Number(scenarioSelected.t1 ?? scenarioSelected.end ?? 0);
    const dur = Math.max(0, t1 - t0);
    const requestedDurationSec = dur;

    const continuityBridgePrompt = transitionType === "continuous"
      ? buildContinuousContinuityBridge({ scene: scenarioSelected, previousScene: scenarioPreviousScene })
      : "";
    const sourceImageUrl = transitionType === "continuous"
      ? (effectiveStartImageUrl || endImageUrl || frameImageUrl || "")
      : (frameImageUrl || "");

    console.log("[StoryboardVideo] video_loading_on reason=generate_video", { sceneId });
    updateScenarioScene(scenarioEditor.selected, { videoStatus: "queued", videoError: "", videoJobId: "", videoPanelActivated: true });
    setScenarioVideoError("");
    console.log("[StoryboardVideo] generate", {
      sceneId,
      transitionType,
      provider: effectiveVideoProvider,
      sourceImageUrl,
    });
    try {
      const endpoint = "/api/clip/video/start";
      const transitionActionPrompt = [
        continuityBridgePrompt,
        getSceneTransitionPrompt(scenarioSelected),
      ].filter(Boolean).join("\n");
      const out = await fetchJson(endpoint, {
        method: "POST",
        body: {
          sceneId,
          imageUrl: sourceImageUrl,
          startImageUrl: effectiveStartImageUrl,
          endImageUrl,
          audioSliceUrl: scenarioSelected.audioSliceUrl || "",
          videoPrompt: scenarioSelected.videoPrompt || "",
          transitionActionPrompt,
          transitionType,
          requestedDurationSec,
          lipSync: effectiveLipSync,
          renderMode: effectiveRenderMode,
          shotType: scenarioSelected.shotType || "",
          sceneType: scenarioSelected.sceneType || "",
          format: normalizeSceneImageFormat(scenarioSelected.imageFormat),
          provider: effectiveVideoProvider,
        },
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
      }

      if (out?.ok && out?.jobId) {
        console.info("[CLIP TRACE] state update", {
          source: "handleScenarioGenerateVideo:updateScenarioScene:queued",
          sceneId,
          jobId: String(out.jobId || ""),
        });
        updateScenarioScene(scenarioEditor.selected, { videoJobId: String(out.jobId), videoStatus: "queued", videoError: "" });
        startScenarioVideoPolling({
          jobId: String(out.jobId),
          providerJobId: String(out.providerJobId || ""),
          provider: String(out?.provider || effectiveVideoProvider),
          sceneId,
          status: "queued",
        });
        return;
      }

      // Fallback for environments where async endpoints are not available yet.
      const legacyOut = await fetchJson("/api/clip/video", {
        method: "POST",
        body: {
          sceneId,
          imageUrl: sourceImageUrl,
          startImageUrl: effectiveStartImageUrl,
          endImageUrl,
          audioSliceUrl: scenarioSelected.audioSliceUrl || "",
          videoPrompt: scenarioSelected.videoPrompt || "",
          transitionActionPrompt,
          transitionType,
          requestedDurationSec,
          lipSync: effectiveLipSync,
          renderMode: effectiveRenderMode,
          shotType: scenarioSelected.shotType || "",
          sceneType: scenarioSelected.sceneType || "",
          format: normalizeSceneImageFormat(scenarioSelected.imageFormat),
          provider: effectiveVideoProvider,
        },
      });
      if (!legacyOut?.ok || !legacyOut?.videoUrl) throw new Error(legacyOut?.hint || legacyOut?.code || "video_generation_failed");
      updateScenarioScene(scenarioEditor.selected, {
        videoUrl: String(legacyOut.videoUrl || ""),
        mode: String(legacyOut.mode || ""),
        model: String(legacyOut.model || ""),
        requestedDurationSec: normalizeDurationSec(legacyOut.requestedDurationSec),
        providerDurationSec: normalizeDurationSec(legacyOut.providerDurationSec),
        videoStatus: "done",
        videoError: "",
        videoJobId: "",
      });
      openNextSceneWithoutVideo(scenarioEditor.selected);
    } catch (e) {
      console.error(e);
      setScenarioVideoError(String(e?.message || e));
      updateScenarioScene(scenarioEditor.selected, { videoStatus: "error", videoError: String(e?.message || e) });
    }
  }, [openNextSceneWithoutVideo, scenarioEditor.selected, scenarioPreviousScene, scenarioSelected, scenarioSelectedEffectiveStartImageUrl, startScenarioVideoPolling, updateScenarioScene]);

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
    const hasImage = transitionType === "continuous"
      ? !!(scenarioSelectedEffectiveStartImageUrl || scenarioSelected?.endImageUrl || scenarioSelected?.imageUrl)
      : !!scenarioSelected?.imageUrl;
    if (!hasImage) return;
    const videoSourceImageUrl = transitionType === "continuous"
      ? String(scenarioSelectedEffectiveStartImageUrl || scenarioSelected?.endImageUrl || scenarioSelected?.imageUrl || "")
      : String(scenarioSelected?.imageUrl || "");
    const effectiveVideoProvider = String(scenarioSelected?.sceneRenderProvider || "kie").trim().toLowerCase() === "comfy_remote" ? "comfy_remote" : "kie";

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
  }, [clearActiveVideoJob, scenarioEditor.selected, scenarioSelected, scenarioSelectedEffectiveStartImageUrl, updateScenarioScene]);

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
    const sceneFormat = assemblyScenesForPayload.find((scene) => String(scene?.imageFormat || scene?.format || "").trim())?.imageFormat
      || assemblyScenesForPayload.find((scene) => String(scene?.format || "").trim())?.format
      || "9:16";
    return buildAssemblyPayload({
      scenes: assemblyScenesForPayload,
      audioUrl: globalAudioUrlRaw,
      format: sceneFormat,
      intro: assemblyIntroFrame,
    });
  }, [assemblyIntroFrame, assemblyScenesForPayload, globalAudioUrlRaw]);

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
  const assemblyPollTimerRef = useRef(null);
  const assemblyPollingActiveRef = useRef(false);

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
                    audioUrl: audioUrl || null,
                    text: textValue || null,
                    mode,
                    scenarioKey,
                    shootKey,
                    styleKey,
                    freezeStyle,
                    wantLipSync,
                    refs: {
                      character: characterRefs,
                      location: locationRefs,
                      props: propsRefs,
                      style: styleRefs,
                    },
                    characterRefs,
                    locationRefs,
                    propsRefs,
                    styleRef,
                    audioType,
                  };

                  const res = await fetch(`${API_BASE}/api/clip/plan`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload),
                    signal: controller.signal,
                  });

                  const out = await res.json().catch(() => ({}));
                  if (!res.ok || !out?.ok) throw new Error(out?.detail || out?.hint || "clip_plan_failed");

                  if (parseTokenRef.current !== parseToken) return;
                  const latestInput = collectBrainPlannerInput({ brainNodeId: nodeId, nodesList: nodesRef.current, edgesList: edgesRef.current });
                  if (latestInput.signature !== plannerInputSignature) return;

                  const audioDuration = Number(out?.audioDuration || 30);
                  const scenesRaw = Array.isArray(out?.scenes) ? out.scenes : [];
                  const validation = out?.plannerDebug?.validation || {};
                  const diverseScenesRaw = enforceSceneDiversityLocal(scenesRaw);

                  const existingScenesById = normalizeSceneCollectionWithSceneId(((nodesRef.current || []).find((x) => x.id === nodeId)?.data?.scenes || []), "scene");
                  const scenes = diverseScenesRaw
                    .map((s, idx) => {
                      const t0 = Number(s.start ?? s.t0 ?? 0);
                      const t1 = Number(s.end ?? s.t1 ?? 0);
                      const prompt = String(s.imagePrompt || s.framePrompt || s.prompt || s.sceneText || `Scene ${idx + 1}`);
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
                        sceneRenderProvider: s.provider || "kie",
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
              onPickImage: async (nodeId, file) => {
                const pickedFiles = Array.isArray(file) ? file : (file ? [file] : []);
                if (!pickedFiles.length) return;
                setNodes((prev) => bindHandlers(prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: true } } : x))));
                try {
                  const targetNode = nodesRef.current.find((x) => x.id === nodeId);
                  const maxFiles = targetNode?.data?.kind === "ref_style" ? 1 : 5;
                  const prevRefs = normalizeRefData(targetNode?.data || {}, targetNode?.data?.kind || "").refs;
                  const room = Math.max(0, maxFiles - (maxFiles === 1 ? 0 : prevRefs.length));
                  const queue = (maxFiles === 1 ? pickedFiles.slice(0, 1) : pickedFiles.slice(0, room));

                  for (const oneFile of queue) {
                    try {
                      const out = await uploadAsset(oneFile);
                      const url = String(out?.url || "").trim();
                      if (!url) continue;
                      setNodes((prev) => prev.map((x) => {
                        if (x.id !== nodeId) return x;
                        const nextPrevRefs = normalizeRefData(x?.data || {}, x?.data?.kind || "").refs;
                        const nextMax = x?.data?.kind === "ref_style" ? 1 : 5;
                        const nextRefs = nextMax === 1
                          ? [{ url, name: out?.name || oneFile.name }]
                          : nextPrevRefs.concat({ url, name: out?.name || oneFile.name }).slice(0, nextMax);
                        return { ...x, data: { ...x.data, refs: nextRefs, refStatus: nextRefs.length ? "draft" : "empty", refShortLabel: "", refDetailsOpen: false, refHiddenProfile: null, refAnalysisError: "" } };
                      }));
                    } catch (err) {
                      console.error(err);
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
          return {
            ...base,
            data: {
              ...base.data,
              onOpenScenario: (nodeId) => {
                try {
                  window.dispatchEvent(new CustomEvent("ps:clipOpenScenario", { detail: { nodeId } }));
                } catch (e) {}
              },
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
              plannerMeta: {
                ...(base.data?.plannerMeta || {}),
                connectedRefsSummary,
                connectedRefsWarnings,
                referenceProfiles: hiddenReferenceProfiles,
              },
              onField: (nodeId, key, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, [key]: value } } : x))),
              onPlannerMode: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, plannerMode: value === "gemini_only" ? "gemini_only" : "legacy" } } : x))),
              onMode: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, mode: value } } : x))),
              onOutput: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, output: normalizeRenderProfile(value) } } : x))),
              onGenre: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, genre: normalizeComfyGenre(value) } } : x))),
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

                const payload = {
                  mode: freshDerived.modeValue,
                  plannerMode: String(freshDerived.plannerMode || "legacy"),
                  output: freshDerived.outputValue,
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
                  audioDurationSec: freshDerived.meaningfulAudioDurationSec,
                  refsByRole: freshDerived.refsByRole,
                  storyControlMode: freshDerived.storyControlMode,
                  storyMissionSummary: freshDerived.storyMissionSummary,
                  audioStoryMode: freshDerived.audioStoryMode,
                  timelineSource: freshDerived.timelineSource,
                  narrativeSource: freshDerived.narrativeSource,
                };
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
                      const plannerMeta = { plannerInput: payload, mode: payload.mode, plannerMode: payload.plannerMode, output: payload.output, stylePreset: payload.stylePreset, genre: payload.genre, narrativeSource: payload.narrativeSource, timelineSource: payload.timelineSource, storyControlMode: payload.storyControlMode, storyMissionSummary: payload.storyMissionSummary, audioStoryMode: payload.audioStoryMode, warnings: [...freshPresentation.critical, ...freshPresentation.warnings], summary: freshPresentation.brainSummary, sceneRoleModel: freshPresentation.sceneRoleModel, referenceSummary: freshPresentation.referenceSummary };
                      const scenes = buildMockComfyScenes(plannerMeta);
                      response = { ok: true, planMeta: plannerMeta, globalContinuity: scenes[0]?.plannerMeta?.globalContinuity || "", scenes, warnings: plannerMeta.warnings, errors: [], debug: {} };
                    } else {
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
                  const resetBrainScenes = resetVideoStateBySceneId(scenes, { panelField: "videoPanelOpen" });
                  const comfyStoryboardTargets = comfyStoryTargets.length
                    ? comfyStoryTargets
                    : (nodesRef.current || []).filter((nodeItem) => nodeItem?.type === 'comfyStoryboard').map((nodeItem) => nodeItem.id);
                  const plannerMeta = response?.planMeta || {};
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
                  const storyboardScenesWithResetState = resetVideoStateBySceneId(scenes, { panelField: "videoPanelOpen" }).map((scene) => ({ ...scene, videoJobId: String(scene?.videoJobId || ""), videoStatus: String(scene?.videoStatus || ""), videoError: String(scene?.videoError || "") }));

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
          return {
            ...base,
            data: {
              ...base.data,
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
          const hasManualTitle = !!String(base.data?.title || "").trim() && (!!base.data?.manualTitle || !base.data?.autoTitle);
          const resolvedTitle = hasManualTitle
            ? String(base.data?.title || "")
            : (base.data?.autoTitle
              ? buildIntroFrameAutoTitle({ textValue: introContext.titleText, scenes: introContext.scenes })
              : String(base.data?.title || ""));
          return {
            ...base,
            data: {
              ...base.data,
              title: resolvedTitle,
              contextSummary: introContext.summary,
              contextSceneCount: introContext.sceneCount,
              sourceNodeIds: introContext.sourceNodeIds,
              sourceNodeTypes: introContext.sourceNodeTypes,
              onField: (nodeId, key, value) => setNodes((prev) => prev.map((x) => {
                if (x.id !== nodeId) return x;
                const nextData = { ...x.data };
                if (key === "autoTitle") {
                  nextData.autoTitle = !!value;
                  if (!!value) {
                    const freshContext = collectIntroFrameContext({ nodeId, nodes: prev, edges: edgesRef.current || [] });
                    nextData.title = buildIntroFrameAutoTitle({ textValue: freshContext.titleText, scenes: freshContext.scenes });
                    nextData.manualTitle = false;
                    nextData.altTitles = [nextData.title].filter(Boolean);
                  }
                } else if (key === "stylePreset") {
                  nextData.stylePreset = normalizeIntroStylePreset(value);
                } else if (key === "previewFormat") {
                  nextData.previewFormat = normalizeIntroFramePreviewFormat(value);
                } else if (key === "durationSec") {
                  nextData.durationSec = normalizeIntroDurationSec(value);
                } else if (key === "title") {
                  nextData.title = String(value || "");
                  nextData.manualTitle = !!String(value || "").trim();
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
                ? { ...x, data: { ...x.data, imageUrl: "", previewKind: "", status: "idle", generatedAt: "", error: "", debug: {} } }
                : x))),
              onGenerate: async (nodeId) => {
                const currentNode = (nodesRef.current || []).find((nodeItem) => nodeItem.id === nodeId);
                const freshContext = collectIntroFrameContext({
                  nodeId,
                  nodes: nodesRef.current || [],
                  edges: edgesRef.current || [],
                });
                const manualTitle = String(currentNode?.data?.title || "").trim();
                const preserveManualTitle = !!manualTitle && (!!currentNode?.data?.manualTitle || !currentNode?.data?.autoTitle);
                const nextTitle = preserveManualTitle
                  ? manualTitle
                  : (currentNode?.data?.autoTitle
                    ? buildIntroFrameAutoTitle({ textValue: freshContext.titleText, scenes: freshContext.scenes })
                    : manualTitle || freshContext.autoTitle);
                const storyContext = buildIntroFrameStoryContextText(freshContext);
                const payload = {
                  title: nextTitle,
                  autoTitle: !!currentNode?.data?.autoTitle,
                  stylePreset: normalizeIntroStylePreset(currentNode?.data?.stylePreset || "cinematic"),
                  previewFormat: normalizeIntroFramePreviewFormat(currentNode?.data?.previewFormat),
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
                };

                setNodes((prev) => prev.map((x) => (x.id === nodeId
                  ? {
                    ...x,
                    data: {
                      ...x.data,
                      title: nextTitle,
                      manualTitle: preserveManualTitle,
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
                  console.log("[INTRO FRAME PAYLOAD] /api/clip/intro/generate", {
                    title: payload.title,
                    previewFormat: payload.previewFormat,
                    stylePreset: payload.stylePreset,
                    connectedRefsByRole: payload.connectedRefsByRole,
                    rawConnectedRefsByRoleCounts: Object.fromEntries(
                      INTRO_COMFY_REF_ROLES.map((role) => [role, Array.isArray(freshContext?.graphConnectedRefsByRole?.[role]) ? freshContext.graphConnectedRefsByRole[role].length : 0])
                    ),
                    plannerRefsByRoleCounts: Object.fromEntries(
                      INTRO_COMFY_REF_ROLES.map((role) => [role, Array.isArray(freshContext?.plannerConnectedRefsByRole?.[role]) ? freshContext.plannerConnectedRefsByRole[role].length : 0])
                    ),
                    connectedRefsByRoleCounts: Object.fromEntries(
                      INTRO_COMFY_REF_ROLES.map((role) => [role, Array.isArray(payload?.connectedRefsByRole?.[role]) ? payload.connectedRefsByRole[role].length : 0])
                    ),
                    graphConnectedSourceNodeIdsByRole: freshContext?.graphConnectedSourceNodeIdsByRole || {},
                    introMustAppear: payload.introMustAppear,
                    introMustNotAppear: payload.introMustNotAppear,
                    connectedGenderLocksByRole: payload.connectedGenderLocksByRole,
                    connectedSpeciesLocksByRole: payload.connectedSpeciesLocksByRole,
                  });
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
                        title: preserveManualTitle ? nextTitle : String(out?.title || nextTitle || ""),
                        imageUrl: String(out.imageUrl || ""),
                        previewKind: INTRO_FRAME_PREVIEW_KINDS.BACKEND_GENERATED,
                        status: "ready",
                        generatedAt: String(out.generatedAt || new Date().toISOString()),
                        altTitles: [preserveManualTitle ? nextTitle : "", String(out?.title || nextTitle || "")].filter(Boolean),
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
      accountKey,
      STORE_KEY,
    ]
  );

  const bindHandlersRef = useRef(bindHandlers);

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

          if (n.type === "introFrame") {
            data.title = String(data.title || "");
            data.autoTitle = !!data.autoTitle;
            data.stylePreset = normalizeIntroStylePreset(data.stylePreset || "cinematic");
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

          return {
            id: n.id,
            type: n.type,
            position: n.position,
            data,
          };
        });
      const cleanEdges = savedEdges
        .filter((e) => e && typeof e.id === "string" && e.source && e.target)
        .map((e) => {
          const sourceNode = cleanNodes.find((n) => n.id === e.source);
          const targetNode = cleanNodes.find((n) => n.id === e.target);
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
    } else if (type === "brain") {
      node = { id, type: "brainNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: "clip", scenarioKey: "clip", shootKey: "cinema", styleKey: "realism", freezeStyle: false, clipSec: 30 } };
    } else if (type === "ref_character") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — ПЕРСОНАЖ", icon: "🧍", kind: "ref_character", refs: [], uploading: false, refStatus: "empty" } };
    } else if (type === "ref_location") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — ЛОКАЦИЯ", icon: "📍", kind: "ref_location", refs: [], uploading: false, refStatus: "empty" } };
    } else if (type === "ref_style") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — СТИЛЬ", icon: "🎨", kind: "ref_style", refs: [], uploading: false, refStatus: "empty" } };
    } else if (type === "ref_items") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — ПРЕДМЕТЫ", icon: "📦", kind: "ref_items", refs: [], uploading: false, refStatus: "empty" } };
    } else if (type === "storyboard") {
      node = { id, type: "storyboardNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { scenes: [] } };
    } else if (type === "introFrame") {
      node = {
        id,
        type: "introFrame",
        position: { x: centerX + jitterX, y: centerY + jitterY },
        data: { title: "", autoTitle: true, manualTitle: false, stylePreset: "cinematic", durationSec: 2.5, previewFormat: INTRO_FRAME_PREVIEW_FORMATS.LANDSCAPE, imageUrl: "", previewKind: "", status: "idle", generatedAt: "", altTitles: [], error: "" },
      };
    } else if (type === "assembly") {
      node = { id, type: "assemblyNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: {} };
    } else if (type === "comfyBrain") {
      node = { id, type: "comfyBrain", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'clip', plannerMode: 'legacy', output: 'comfy image', genre: '', audioStoryMode: 'lyrics_music', styleKey: 'realism', freezeStyle: false, parseStatus: 'idle' } };
    } else if (type === "comfyStoryboard") {
      node = { id, type: "comfyStoryboard", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mockScenes: [], sceneCount: 0, mode: 'clip', parseStatus: 'idle' } };
    } else if (type === "comfyVideoPreview") {
      node = { id, type: "comfyVideoPreview", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { previewStatus: 'idle', previewUrl: '', workflowPreset: 'comfy-default', format: '9:16', duration: 0 } };
    } else if (type === "refCharacter2") {
      node = { id, type: "refCharacter2", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'ally', name: '', identityLock: false, priority: 'normal', notes: '', refs: [], uploading: false, refStatus: 'empty' } };
    } else if (type === "refCharacter3") {
      node = { id, type: "refCharacter3", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'ally', name: '', identityLock: false, priority: 'normal', notes: '', refs: [], uploading: false, refStatus: 'empty' } };
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
      brainNode: BrainNode,
      refNode: RefNode,
      storyboardNode: StoryboardPlanNode,
      introFrame: IntroFrameNode,
      assemblyNode: AssemblyNode,
      comfyBrain: ComfyBrainNode,
      comfyStoryboard: ComfyStoryboardNode,
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
        const src = nodes.find((n) => n.id === params.source);
        const dst = nodes.find((n) => n.id === params.target);
        if (!src || !dst) return eds;

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
          return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
        }

        if (dst.type === 'comfyBrain') {
          const h = params.targetHandle || '';
          const sourceHandle = params.sourceHandle || '';
          const refCfg = COMFY_BRAIN_REF_HANDLE_CONFIG[h];
          const ok =
            (h === 'audio' && src.type === 'audioNode' && sourceHandle === 'audio') ||
            (h === 'text' && src.type === 'textNode' && sourceHandle === 'text') ||
            (!!refCfg && src.type === refCfg.sourceType && sourceHandle === refCfg.sourceHandle);
          if (!ok) return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || '') === h));
          const presentation = getEdgePresentation({ sourceHandle, targetHandle: h, sourceType: src.type, targetType: dst.type });
          return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
        }

        if (src.type === "brainNode" && (params.sourceHandle || "") === "plan") {
          if (dst.type === "storyboardNode" && (params.targetHandle || "") === "plan_in") {
            const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || "") === "plan_in"));
            const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
            return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          }
          return eds;
        }

        if (src.type === 'comfyBrain' && (params.sourceHandle || '') === 'comfy_plan') {
          if (dst.type !== 'comfyStoryboard' || (params.targetHandle || '') !== 'comfy_plan') return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || '') === 'comfy_plan'));
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || '', targetHandle: params.targetHandle || '', sourceType: src.type, targetType: dst.type });
          return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
        }

        if (dst.type === "introFrame") {
          const targetHandle = params.targetHandle || "";
          const sourceHandle = params.sourceHandle || "";
          const allowStoryContext =
            targetHandle === "story_context"
            && (
              (src.type === "comfyStoryboard" && sourceHandle === "comfy_scene_video_out")
              || (src.type === "storyboardNode" && sourceHandle === "plan_out")
              || (src.type === "brainNode" && sourceHandle === "plan")
              || (src.type === "comfyBrain" && sourceHandle === "comfy_plan")
              || (src.type === "refNode" && ["ref_character", "ref_location", "ref_style", "ref_items"].includes(sourceHandle))
              || (src.type === "textNode" && sourceHandle === "text")
            );
          const allowTitleContext = targetHandle === "title_context" && src.type === "textNode" && sourceHandle === "text";
          if (!allowStoryContext && !allowTitleContext) return eds;
          const cleaned = allowTitleContext
            ? eds.filter((e) => !(e.target === dst.id && (e.targetHandle || "") === "title_context"))
            : eds;
          const presentation = getEdgePresentation({ sourceHandle, targetHandle, sourceType: src.type, targetType: dst.type });
          return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
        }

        if (src.type === 'comfyStoryboard' && (params.sourceHandle || '') === 'comfy_scene_video_out') {
          if (dst.type === 'assemblyNode' && (params.targetHandle || '') === 'assembly_in') {
            const cleaned = removeAssemblyIncomingSourceEdges(eds, dst.id, "assembly_in");
            const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || '', targetHandle: params.targetHandle || '', sourceType: src.type, targetType: dst.type });
            return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
          }
          if (dst.type !== 'comfyVideoPreview' || (params.targetHandle || '') !== 'comfy_scene_video_out') return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || '') === 'comfy_scene_video_out'));
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || '', targetHandle: params.targetHandle || '', sourceType: src.type, targetType: dst.type });
          return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
        }

        if (src.type === 'comfyStoryboard' || src.type === 'comfyBrain' || src.type === 'comfyVideoPreview' || dst.type === 'comfyStoryboard' || dst.type === 'comfyBrain' || dst.type === 'comfyVideoPreview') {
          return eds;
        }

        if (src.type === "introFrame" && (params.sourceHandle || "") === "intro_frame_out") {
          if (dst.type !== "assemblyNode" || (params.targetHandle || "") !== "assembly_intro") return eds;
          const cleaned = removeAssemblyIncomingSourceEdges(eds, dst.id, "assembly_intro");
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
          return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
        }

        if (src.type === "storyboardNode" && (params.sourceHandle || "") === "plan_out") {
          if (dst.type !== "assemblyNode" || (params.targetHandle || "") !== "assembly_in") return eds;
          const cleaned = removeAssemblyIncomingSourceEdges(eds, dst.id, "assembly_in");
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
          return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
        }

        const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || "", targetHandle: params.targetHandle || "", sourceType: src.type, targetType: dst.type });
        return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, eds);
      });
    },
    [setEdges, nodes]
  );

  const onEdgeClick = useCallback((evt, edge) => {
    evt?.stopPropagation?.();
    if (!edge?.id) return;
    setEdges((eds) => eds.filter((e) => e.id !== edge.id));
  }, [setEdges]);

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
                  <h4>TEXT</h4>
                  <p>TEXT может <b>override</b>, <b>guide</b> или <b>enhance</b> story в зависимости от MODE.</p>
                </section>
                <section className="clipSB_guideCard">
                  <h4>STYLE</h4>
                  <p>STYLE усиливает визуал и не должен ломать драматургию MODE.</p>
                </section>
                <section className="clipSB_guideCard">
                  <h4>REFS</h4>
                  <p>REFS фиксируют персонажей, мир и continuity между сценами.</p>
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
                        + (i === scenarioEditor.selected ? " isActive" : "")}
                      role="button"
                      tabIndex={0}
                      onClick={() => setScenarioEditor((x) => ({ ...x, selected: i }))}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          setScenarioEditor((x) => ({ ...x, selected: i }));
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
                            {scenarioSelectedEffectiveStartImageUrl ? (
                              <img
                                src={scenarioSelectedEffectiveStartImageUrl}
                                alt="start frame preview"
                                className="clipSB_scenarioPreview"
                                onClick={(e) => openLightbox(scenarioSelectedEffectiveStartImageUrl, e.currentTarget.getBoundingClientRect())}
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
                            {scenarioSelected.endImageUrl ? (
                              <img
                                src={scenarioSelected.endImageUrl}
                                alt="end frame preview"
                                className="clipSB_scenarioPreview"
                                onClick={(e) => openLightbox(scenarioSelected.endImageUrl, e.currentTarget.getBoundingClientRect())}
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
                            {scenarioSelected.imageUrl ? (
                              <img
                                src={scenarioSelected.imageUrl}
                                alt="scene preview"
                                className="clipSB_scenarioPreview"
                                onClick={(e) => openLightbox(scenarioSelected.imageUrl, e.currentTarget.getBoundingClientRect())}
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
                          <div className="clipSB_hint">Срез ещё не создан. Нажмите «Взять аудио».</div>
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
                              {scenarioSelected.endImageUrl ? (
                                <img
                                  src={scenarioSelected.endImageUrl}
                                  className="clipSB_videoFrameImg"
                                  alt="end frame"
                                  onClick={(e) => openLightbox(scenarioSelected.endImageUrl, e.currentTarget.getBoundingClientRect())}
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
                          <div className="clipSB_videoKv"><span>endImageUrl</span><span>{scenarioSelected.endImageUrl || "—"}</span></div>
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
                            disabled={scenarioVideoLoading || !(scenarioSelectedTransitionType === "continuous" ? (scenarioSelectedEffectiveStartImageUrl || scenarioSelected.endImageUrl || scenarioSelected.imageUrl) : scenarioSelected.imageUrl) || (scenarioSelectedIsLipSync && !scenarioSelected.audioSliceUrl)}
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
                              <div className="clipSB_comfyPreviewEmpty">Изображение сцены пока не создано</div>
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
                        <div className="clipSB_hint">Срез ещё не создан. Нажмите «Взять аудио».</div>
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
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("brain")}>🧠 Мозг</button>
              <div className="clipSB_drawerSep" />
              <div className="clipSB_drawerGroupTitle">ОБЫЧНЫЕ REFS</div>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("ref_character")}>🧍 REF — Персонаж</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("ref_location")}>📍 REF — Локация</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("ref_style")}>🎨 REF — Стиль</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("ref_items")}>📦 REF — Предметы</button>
              <div className="clipSB_drawerSep" />
              <div className="clipSB_drawerGroupTitle">COMFY FLOW</div>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("comfyBrain")}>🧠 COMFY BRAIN</button>
              <button className="clipSB_drawerItem" onClick={() => addNodeFromDrawer("comfyStoryboard")}>🧩 COMFY STORYBOARD</button>
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
