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
  extractComfyDebugFields,
  normalizeComfyScenePrompts,
  PROMPT_SYNC_STATUS,
} from "./clip_nodes/comfy/comfyBrainDomain";


// -------------------------
// typed ports + colors (for clear wiring)
// -------------------------
const PORT_COLORS = {
  audio: "#ff5f7d",
  text: "#8bb8ff",
  ref_character: "#34d5d7",
  ref_character_1: "#34d5d7",
  ref_character_2: "#00bcd4",
  ref_character_3: "#26c6da",
  ref_animal: "#ffb74d",
  ref_group: "#f06292",
  ref_location: "#b37bff",
  ref_style: "#ffc25b",
  ref_items: "#93dd6f",
  ref_props: "#93dd6f",
  plan: "#4dd8ff",
  comfy_plan: "#4dd8ff",
  comfy_storyboard: "#6aa8ff",
  comfy_video: "#7df9ff",
  brain_to_storyboard: "#4dd8ff",
  storyboard_to_assembly: "#6aa8ff",
  assembly: "#6aa8ff",
  brain: "#c480ff",
};

function portColor(key) {
  return PORT_COLORS[key] || "#8c8c8c";
}

function handleStyle(kind, extra = {}) {
  const color = portColor(kind);
  return {
    background: color,
    width: 12,
    height: 12,
    border: "2px solid rgba(255,255,255,0.42)",
    boxShadow: `0 0 0 1px rgba(0,0,0,0.55), 0 0 10px ${color}99`,
    ...extra,
  };
}

function isBrainInput(handleId) {
  return handleId === "audio" || handleId === "text" || handleId === "ref_character" || handleId === "ref_location" || handleId === "ref_style" || handleId === "ref_items";
}

function isComfyBrainInput(handleId) {
  return [
    "audio",
    "text",
    "ref_character_1",
    "ref_character_2",
    "ref_character_3",
    "ref_animal",
    "ref_group",
    "ref_location",
    "ref_style",
    "ref_props",
  ].includes(handleId);
}

const EDGE_STYLE_BY_KIND = {
  audio: { color: PORT_COLORS.audio, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  text: { color: PORT_COLORS.text, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_character: { color: PORT_COLORS.ref_character, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_character_1: { color: PORT_COLORS.ref_character_1, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_location: { color: PORT_COLORS.ref_location, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_style: { color: PORT_COLORS.ref_style, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_items: { color: PORT_COLORS.ref_items, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_props: { color: PORT_COLORS.ref_props, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_character_2: { color: PORT_COLORS.ref_character_2, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_character_3: { color: PORT_COLORS.ref_character_3, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_animal: { color: PORT_COLORS.ref_animal, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  ref_group: { color: PORT_COLORS.ref_group, strokeWidth: 2.1, opacity: 0.95, animatedDash: true },
  comfy_plan: { color: PORT_COLORS.comfy_plan, strokeWidth: 2.4, opacity: 0.98, animatedDash: true },
  comfy_storyboard: { color: PORT_COLORS.comfy_storyboard, strokeWidth: 2.4, opacity: 0.98, animatedDash: true },
  comfy_video: { color: PORT_COLORS.comfy_video, strokeWidth: 2.4, opacity: 0.98, animatedDash: true },
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
  default: { color: "#8c8c8c", strokeWidth: 2, opacity: 0.9, animatedDash: true },
};

function detectEdgeKind({ sourceHandle = "", targetHandle = "", sourceType = "", targetType = "", existingKind = "" }) {
  if (targetType === "brainNode" && isBrainInput(targetHandle)) return targetHandle;
  if (targetType === "comfyBrain" && isComfyBrainInput(targetHandle)) return targetHandle;

  if (sourceType === "comfyBrain" && sourceHandle === "comfy_plan" && targetType === "comfyStoryboard" && targetHandle === "comfy_plan") {
    return "comfy_plan";
  }

  if (sourceType === "comfyStoryboard" && sourceHandle === "comfy_scene_video_out" && targetType === "comfyVideoPreview" && targetHandle === "comfy_scene_video_out") {
    return "comfy_video";
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
  const explicitId = String(scene.id || scene.sceneId || "").trim();
  if (explicitId) return explicitId;

  const start = Number(scene.t0 ?? scene.start);
  const end = Number(scene.t1 ?? scene.end);
  if (Number.isFinite(start) && Number.isFinite(end)) {
    return `time-${start}-${end}`;
  }

  return `scene-${idx}`;
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

function resolveSceneTransitionType(scene) {
  const raw = String(scene?.transitionType || "single").toLowerCase();
  if (raw === "continuous" || raw === "single" || raw === "hard_cut") return raw;
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

function buildAssemblyPayload({ scenes = [], audioUrl = "", format = "9:16" }) {
  const normalizedFormat = normalizeSceneImageFormat(format);
  const safeAudioUrl = String(audioUrl || "").trim();
  const preparedScenes = (Array.isArray(scenes) ? scenes : [])
    .map((scene, idx) => {
      const videoUrl = String(scene?.videoUrl || "").trim();
      if (!videoUrl) return null;
      return {
        sceneId: String(scene?.id || `scene_${String(idx + 1).padStart(3, "0")}`),
        videoUrl,
        requestedDurationSec: getSceneRequestedDurationSec(scene),
        transitionType: resolveSceneTransitionType(scene),
        order: idx + 1,
      };
    })
    .filter(Boolean);

  return {
    audioUrl: safeAudioUrl,
    format: normalizedFormat,
    scenes: preparedScenes,
  };
}

function extractStoryboardScenesFromNodes(nodes = []) {
  const storyboardNode = (Array.isArray(nodes) ? nodes : []).find((n) => n?.type === "storyboardNode") || null;
  return Array.isArray(storyboardNode?.data?.scenes) ? storyboardNode.data.scenes : [];
}

function extractGlobalAudioUrlFromNodes(nodes = []) {
  const audioNodeWithUrl = (Array.isArray(nodes) ? nodes : []).find((n) => n?.type === "audioNode" && n?.data?.audioUrl);
  return audioNodeWithUrl?.data?.audioUrl ? String(audioNodeWithUrl.data.audioUrl) : "";
}

function buildAssemblyPayloadSignature(payload) {
  return JSON.stringify({
    audioUrl: payload?.audioUrl || "",
    format: payload?.format || "9:16",
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
    `Mode: ${mode}`,
    `Style preset: ${stylePreset}`,
    scene?.title ? `Scene title: ${scene.title}` : "",
    scene?.sceneGoal ? `Scene goal: ${scene.sceneGoal}` : "",
    scene?.sceneNarrativeStep ? `Narrative step: ${scene.sceneNarrativeStep}` : "",
    scene?.continuity ? `Continuity: ${scene.continuity}` : "",
    timing ? `Timing: ${timing}` : "",
    scene?.primaryRole ? `Primary role: ${scene.primaryRole}` : "",
    Array.isArray(scene?.secondaryRoles) && scene.secondaryRoles.length ? `Secondary roles: ${scene.secondaryRoles.join(", ")}` : "",
    scene?.refsUsed && typeof scene.refsUsed === "object" && Object.keys(scene.refsUsed).length ? `Refs used: ${JSON.stringify(scene.refsUsed)}` : "",
    isVideo && scene?.imageUrl ? `Source image: ${scene.imageUrl}` : "",
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
    const normalizedData = n.type === "refNode"
      ? normalizeRefData(n.data || {}, n?.data?.kind || "")
      : (n.data || {});
    const data = stripFunctionsDeep(normalizedData) || {};
    if (n.type === "brainNode") {
      delete data.isParsing;
      delete data.activeParseToken;
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
        className={`clipSB_nodeRef clipSB_nodeRef--${kind}`}
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

        <button className="clipSB_btn" onClick={openPicker} disabled={!canAddMore || !!data?.uploading}>
          {data?.uploading ? "Загрузка…" : "Загрузить фото"}
        </button>
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
            : "Загрузи до 5 фото, порядок учитывается"}
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

function AssemblyNode({ id, data }) {
  const isAssembling = !!data?.isAssembling;
  const canAssemble = !!data?.canAssemble;
  const status = data?.status || "empty";
  const result = data?.result || null;
  const finalVideoUrl = resolveAssetUrl(data?.result?.finalVideoUrl);
  const resultSceneCount = Number(result?.sceneCount || 0);
  const audioApplied = !!result?.audioApplied;
  const isStale = !!data?.isStale;

  return (
    <>
      <Handle type="target" position={Position.Left} id="assembly_in" className="clipSB_handle" style={handleStyle("assembly")} />
      <NodeShell
        title="ASSEMBLY"
        onClose={() => data?.onRemoveNode?.(id)}
        icon={<span aria-hidden>🎬</span>}
        className="clipSB_nodeAssembly"
      >
        <div className="clipSB_assemblyStats">
          <div className="clipSB_assemblyRow"><span>Сцен готово</span><strong>{data?.readyScenes || 0}/{data?.totalScenes || 0}</strong></div>
          <div className="clipSB_assemblyRow"><span>Аудио</span><strong>{data?.hasAudio ? "подключено" : "не подключено"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Формат</span><strong>{data?.format || "9:16"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Длительность</span><strong>~{Math.round(Number(data?.durationSec || 0))} сек</strong></div>
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

        {status === "empty" ? <div className="clipSB_assemblyNote">Нужна хотя бы одна готовая видео-сцена</div> : null}
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
              Сцен: {resultSceneCount > 0 ? resultSceneCount : Number(data?.readyScenes || 0)} • Аудио: {audioApplied ? "добавлено" : "нет"}
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

  const didHydrateRef = useRef(false);
  const isHydratingRef = useRef(true);
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
  const scenarioVideoPollTimerRef = useRef(null);
  const scenarioActiveVideoJobRef = useRef(null);
  const comfyVideoPollTimerRef = useRef(null);
  const comfyActiveVideoJobRef = useRef(null);
  const comfyPromptSyncTimersRef = useRef(new Map());
  const comfyPromptSyncInFlightRef = useRef(new Map());
  const [scenarioVideoFocusPulse, setScenarioVideoFocusPulse] = useState(false);

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
  return Array.isArray(arr) ? arr : [];
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
const scenarioSelectedStartImageSource = getSceneStartImageSource(scenarioSelected, scenarioPreviousScene);
const scenarioSelectedImageFormat = normalizeSceneImageFormat(scenarioSelected?.imageFormat);
const scenarioSelectedIndexLabel = Number.isFinite(scenarioEditor.selected) ? scenarioEditor.selected + 1 : 0;
const scenarioSelectedT0 = Number(scenarioSelected?.t0 ?? scenarioSelected?.start ?? 0);
const scenarioSelectedT1 = Number(scenarioSelected?.t1 ?? scenarioSelected?.end ?? 0);
const scenarioSelectedExpectedSliceSec = Number(
  scenarioSelected?.audioSliceExpectedDurationSec ?? Math.max(0, scenarioSelectedT1 - scenarioSelectedT0)
);
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

const comfyNode = useMemo(() => {
  if (comfyEditor.nodeId) return nodes.find((n) => n.id === comfyEditor.nodeId && n.type === 'comfyStoryboard') || null;
  return nodes.find((n) => n.type === 'comfyStoryboard') || null;
}, [nodes, comfyEditor.nodeId]);

useEffect(() => {
  console.log("[COMFY DEBUG FRONT] comfyStoryboard plannerMeta plannerInput refsByRole", comfyNode?.data?.plannerMeta?.plannerInput?.refsByRole);
}, [comfyNode]);

const comfyScenes = useMemo(() => {
  const arr = comfyNode?.data?.mockScenes;
  return Array.isArray(arr) ? arr.map((scene) => normalizeComfyScenePrompts(scene)) : [];
}, [comfyNode]);

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
  const [scenarioVideoLoading, setScenarioVideoLoading] = useState(false);
  const [scenarioVideoError, setScenarioVideoError] = useState("");
  const [scenarioVideoOpen, setScenarioVideoOpen] = useState(false);
  const [comfyImageLoading, setComfyImageLoading] = useState(false);
  const [comfyImageError, setComfyImageError] = useState("");
  const [comfyVideoLoading, setComfyVideoLoading] = useState(false);
  const [comfyVideoError, setComfyVideoError] = useState("");
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
const comfyActiveVideoJobSceneId = String(comfyActiveVideoJobRef.current?.sceneId || "").trim();
const comfyActiveVideoJobStatus = String(comfyActiveVideoJobRef.current?.status || "").toLowerCase();
const comfyHasActiveVideoJobForScene = Boolean(
  comfySelectedSceneId
  && comfyActiveVideoJobSceneId
  && comfySelectedSceneId === comfyActiveVideoJobSceneId
  && !["done", "error", "stopped", "not_found"].includes(comfyActiveVideoJobStatus)
);
const comfyShowVideoSection = Boolean(
  comfySelectedScene?.videoPanelOpen
  || String(comfySelectedScene?.videoUrl || "").trim()
  || comfyHasActiveVideoJobForScene
);

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
    setScenarioVideoError("");
    setScenarioVideoOpen(false);
  }, [scenarioEditor.selected]);

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

  const stopScenarioVideoPolling = useCallback(() => {
    if (scenarioVideoPollTimerRef.current) {
      clearTimeout(scenarioVideoPollTimerRef.current);
      scenarioVideoPollTimerRef.current = null;
    }
  }, []);

  const persistActiveVideoJob = useCallback((job) => {
    if (!job || !job.jobId) {
      safeDel(VIDEO_JOB_STORE_KEY);
      return;
    }
    safeSet(VIDEO_JOB_STORE_KEY, JSON.stringify(job));
  }, [VIDEO_JOB_STORE_KEY]);

  const clearActiveVideoJob = useCallback(() => {
    scenarioActiveVideoJobRef.current = null;
    safeDel(VIDEO_JOB_STORE_KEY);
    stopScenarioVideoPolling();
    setScenarioVideoLoading(false);
  }, [VIDEO_JOB_STORE_KEY, stopScenarioVideoPolling]);

  const startScenarioVideoPolling = useCallback((jobMeta) => {
    if (!jobMeta?.jobId) return;
    scenarioActiveVideoJobRef.current = { ...jobMeta };
    persistActiveVideoJob(scenarioActiveVideoJobRef.current);
    setScenarioVideoLoading(true);
    stopScenarioVideoPolling();

    const tick = async () => {
      try {
        const out = await fetchJson(`/api/clip/video/status/${encodeURIComponent(jobMeta.jobId)}`, { method: "GET" });
        if (!out?.ok) throw new Error(out?.hint || out?.code || "video_status_failed");
        const status = String(out?.status || "").toLowerCase();
        const nextMeta = {
          ...(scenarioActiveVideoJobRef.current || {}),
          jobId: jobMeta.jobId,
          providerJobId: String(out?.providerJobId || scenarioActiveVideoJobRef.current?.providerJobId || ""),
          sceneId: String(out?.sceneId || scenarioActiveVideoJobRef.current?.sceneId || ""),
          status,
        };
        scenarioActiveVideoJobRef.current = nextMeta;
        persistActiveVideoJob(nextMeta);

        if (status === "done") {
          const sceneId = String(nextMeta.sceneId || "");
          const idx = scenarioScenes.findIndex((x) => String(x?.id || "") === sceneId);
          if (idx >= 0) {
            updateScenarioScene(idx, {
              videoUrl: String(out?.videoUrl || ""),
              mode: String(out?.mode || ""),
              model: String(out?.model || ""),
              requestedDurationSec: normalizeDurationSec(out?.requestedDurationSec),
              providerDurationSec: normalizeDurationSec(out?.providerDurationSec),
            });
          }
          clearActiveVideoJob();
          return;
        }

        if (status === "error" || status === "stopped" || status === "not_found") {
          setScenarioVideoError(String(out?.error || out?.hint || "video_job_failed"));
          clearActiveVideoJob();
          return;
        }

        scenarioVideoPollTimerRef.current = setTimeout(tick, 1800);
      } catch (e) {
        console.error(e);
        scenarioVideoPollTimerRef.current = setTimeout(tick, 2400);
      }
    };

    scenarioVideoPollTimerRef.current = setTimeout(tick, 250);
  }, [clearActiveVideoJob, persistActiveVideoJob, scenarioScenes, stopScenarioVideoPolling, updateScenarioScene]);

  useEffect(() => () => stopScenarioVideoPolling(), [stopScenarioVideoPolling]);

  useEffect(() => {
    const raw = safeGet(VIDEO_JOB_STORE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      if (!parsed?.jobId) return;
      startScenarioVideoPolling(parsed);
    } catch {
      safeDel(VIDEO_JOB_STORE_KEY);
    }
  }, [VIDEO_JOB_STORE_KEY, startScenarioVideoPolling]);

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
    if (status === PROMPT_SYNC_STATUS.synced && enText) return enText;
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

  const stopComfyVideoPolling = useCallback(() => {
    if (comfyVideoPollTimerRef.current) {
      clearTimeout(comfyVideoPollTimerRef.current);
      comfyVideoPollTimerRef.current = null;
    }
  }, []);

  const persistActiveComfyVideoJob = useCallback((job) => {
    if (!job || !job.jobId) {
      safeDel(COMFY_VIDEO_JOB_STORE_KEY);
      return;
    }
    safeSet(COMFY_VIDEO_JOB_STORE_KEY, JSON.stringify(job));
  }, [COMFY_VIDEO_JOB_STORE_KEY]);

  const clearActiveComfyVideoJob = useCallback(() => {
    comfyActiveVideoJobRef.current = null;
    safeDel(COMFY_VIDEO_JOB_STORE_KEY);
    stopComfyVideoPolling();
    setComfyVideoLoading(false);
  }, [COMFY_VIDEO_JOB_STORE_KEY, stopComfyVideoPolling]);

  const startComfyVideoPolling = useCallback((jobMeta) => {
    if (!jobMeta?.jobId) return;
    comfyActiveVideoJobRef.current = { ...jobMeta };
    persistActiveComfyVideoJob(comfyActiveVideoJobRef.current);
    setComfyVideoLoading(true);
    stopComfyVideoPolling();

    const tick = async () => {
      try {
        const out = await fetchJson(`/api/clip/video/status/${encodeURIComponent(jobMeta.jobId)}`, { method: "GET" });
        if (!out?.ok) throw new Error(out?.hint || out?.code || "video_status_failed");
        const status = String(out?.status || "").toLowerCase();
        const nextMeta = {
          ...(comfyActiveVideoJobRef.current || {}),
          jobId: jobMeta.jobId,
          providerJobId: String(out?.providerJobId || comfyActiveVideoJobRef.current?.providerJobId || ""),
          sceneId: String(out?.sceneId || comfyActiveVideoJobRef.current?.sceneId || ""),
          status,
        };
        comfyActiveVideoJobRef.current = nextMeta;
        persistActiveComfyVideoJob(nextMeta);

        if (status === "done") {
          const sceneId = String(nextMeta.sceneId || "");
          const idx = comfyScenes.findIndex((x) => String(x?.sceneId || "") === sceneId);
          if (idx >= 0) {
            updateComfyScene(idx, { videoUrl: String(out?.videoUrl || ""), videoPanelOpen: true });
          }
          clearActiveComfyVideoJob();
          return;
        }

        if (status === "error" || status === "stopped" || status === "not_found") {
          setComfyVideoError(String(out?.error || out?.hint || "video_job_failed"));
          clearActiveComfyVideoJob();
          return;
        }

        comfyVideoPollTimerRef.current = setTimeout(tick, 1800);
      } catch (e) {
        console.error(e);
        comfyVideoPollTimerRef.current = setTimeout(tick, 2400);
      }
    };

    comfyVideoPollTimerRef.current = setTimeout(tick, 250);
  }, [clearActiveComfyVideoJob, comfyScenes, persistActiveComfyVideoJob, stopComfyVideoPolling, updateComfyScene]);

  useEffect(() => () => stopComfyVideoPolling(), [stopComfyVideoPolling]);

  useEffect(() => {
    const raw = safeGet(COMFY_VIDEO_JOB_STORE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      if (!parsed?.jobId) return;
      startComfyVideoPolling(parsed);
    } catch {
      safeDel(COMFY_VIDEO_JOB_STORE_KEY);
    }
  }, [COMFY_VIDEO_JOB_STORE_KEY, startComfyVideoPolling]);

  const handleComfyGenerateImage = useCallback(async () => {
    if (!comfySelectedScene) return;
    setComfyImageLoading(true);
    setComfyImageError('');
    try {
      const sceneId = String(comfySelectedScene.sceneId || `comfy-scene-${comfySafeIndex + 1}`);
      const contextPrompt = buildComfySceneContextPrompt({
        scene: comfySelectedScene,
        mode: comfyNode?.data?.mode || "clip",
        stylePreset: comfyNode?.data?.stylePreset || "realism",
      });
      const imagePrompt = await ensureComfyPromptSynced({ idx: comfySafeIndex, promptType: 'image' });
      const previousSceneImageUrl = String(
        comfyPreviousScene?.endImageUrl
        || comfyPreviousScene?.imageUrl
        || comfyPreviousScene?.startImageUrl
        || ""
      ).trim();
      const plannerInput = comfyNode?.data?.plannerMeta?.plannerInput || {};
      const refsByRoleForImage = plannerInput?.refsByRole || comfyRefsByRole;
      const refsPayloadForImage = {
        refsByRole: refsByRoleForImage,
        previousSceneImageUrl,
        previousContinuityMemory: comfySelectedScene?.continuity ? { continuity: comfySelectedScene.continuity } : null,
        propAnchorLabel: inferPropAnchorLabel(refsByRoleForImage),
        text: plannerInput?.text || comfyNode?.data?.text || "",
        audioUrl: plannerInput?.audioUrl || comfyNode?.data?.audioUrl || "",
        mode: plannerInput?.mode || comfyNode?.data?.mode || "",
        stylePreset: plannerInput?.stylePreset || comfyNode?.data?.stylePreset || "",
        sceneId,
        sceneGoal: plannerInput?.sceneGoal || comfySelectedScene?.sceneGoal || "",
        sceneNarrativeStep: plannerInput?.sceneNarrativeStep || comfySelectedScene?.sceneNarrativeStep || "",
        continuity: plannerInput?.continuity || comfySelectedScene?.continuity || "",
        plannerMeta: comfyNode?.data?.plannerMeta || null,
        refsUsed: Array.isArray(comfySelectedScene?.refsUsed) ? comfySelectedScene.refsUsed : [],
        refDirectives: comfySelectedScene?.refDirectives && typeof comfySelectedScene.refDirectives === 'object' ? comfySelectedScene.refDirectives : null,
        primaryRole: comfySelectedScene?.primaryRole || "",
        secondaryRoles: Array.isArray(comfySelectedScene?.secondaryRoles) ? comfySelectedScene.secondaryRoles : [],
        heroEntityId: comfySelectedScene?.heroEntityId || "",
        supportEntityIds: Array.isArray(comfySelectedScene?.supportEntityIds) ? comfySelectedScene.supportEntityIds : [],
        mustAppear: Array.isArray(comfySelectedScene?.mustAppear) ? comfySelectedScene.mustAppear : [],
        mustNotAppear: Array.isArray(comfySelectedScene?.mustNotAppear) ? comfySelectedScene.mustNotAppear : [],
        environmentLock: typeof comfySelectedScene?.environmentLock === 'boolean' ? comfySelectedScene.environmentLock : null,
        styleLock: typeof comfySelectedScene?.styleLock === 'boolean' ? comfySelectedScene.styleLock : null,
        identityLock: typeof comfySelectedScene?.identityLock === 'boolean' ? comfySelectedScene.identityLock : null,
      };
      const refsForImageRequest = buildComfySceneRefsPayload(refsPayloadForImage);

      console.log("[COMFY DEBUG FRONT] /clip/image plannerInput", plannerInput);
      console.log("[COMFY DEBUG FRONT] /clip/image plannerInput.refsByRole", plannerInput?.refsByRole);
      console.log("[COMFY DEBUG FRONT] /clip/image plannerInput.refsByRole counts", summarizeRefsByRole(plannerInput?.refsByRole));
      console.log("[COMFY DEBUG FRONT] /clip/image comfyRefsByRole", comfyRefsByRole);
      console.log("[COMFY DEBUG FRONT] /clip/image comfyRefsByRole counts", summarizeRefsByRole(comfyRefsByRole));
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
  }, [comfyNode?.data?.mode, comfyNode?.data?.stylePreset, comfyPreviousScene?.endImageUrl, comfyPreviousScene?.imageUrl, comfyPreviousScene?.startImageUrl, comfyRefsByRole, comfySafeIndex, comfySelectedScene, ensureComfyPromptSynced, updateComfyScene]);

  const handleComfyDeleteImage = useCallback(() => {
    setComfyImageError('');
    updateComfyScene(comfySafeIndex, { imageUrl: '', videoUrl: '', videoPanelOpen: false });
  }, [comfySafeIndex, updateComfyScene]);

  const handleComfyOpenVideoPanel = useCallback(() => {
    if (!comfySelectedScene) return;
    updateComfyScene(comfySafeIndex, { videoPanelOpen: true });
  }, [comfySafeIndex, comfySelectedScene, updateComfyScene]);

  const handleComfyGenerateVideo = useCallback(async () => {
    if (!comfySelectedScene?.imageUrl) return;
    updateComfyScene(comfySafeIndex, { videoPanelOpen: true });
    setComfyVideoLoading(true);
    setComfyVideoError('');
    try {
      const sceneId = String(comfySelectedScene.sceneId || `comfy-scene-${comfySafeIndex + 1}`);
      const contextPrompt = buildComfySceneContextPrompt({
        scene: comfySelectedScene,
        mode: comfyNode?.data?.mode || "clip",
        stylePreset: comfyNode?.data?.stylePreset || "realism",
        isVideo: true,
      });
      const syncedVideoPrompt = await ensureComfyPromptSynced({ idx: comfySafeIndex, promptType: 'video' });
      const out = await fetchJson('/api/clip/video/start', {
        method: 'POST',
        body: {
          sceneId,
          imageUrl: String(comfySelectedScene.imageUrl || ''),
          videoPrompt: syncedVideoPrompt,
          transitionActionPrompt: contextPrompt,
          requestedDurationSec: Number(comfySelectedScene.durationSec) || 3,
          shotType: String(comfySelectedScene.sceneNarrativeStep || ''),
          sceneType: String(comfySelectedScene.sceneGoal || ''),
          format: '9:16',
        },
      });

      if (out?.ok && out?.jobId) {
        startComfyVideoPolling({
          jobId: String(out.jobId),
          providerJobId: String(out.providerJobId || ''),
          sceneId,
          status: 'queued',
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
          requestedDurationSec: Number(comfySelectedScene.durationSec) || 3,
          shotType: String(comfySelectedScene.sceneNarrativeStep || ''),
          sceneType: String(comfySelectedScene.sceneGoal || ''),
          format: '9:16',
        },
      });
      if (!legacyOut?.ok || !legacyOut?.videoUrl) throw new Error(legacyOut?.hint || legacyOut?.code || 'video_generation_failed');
      updateComfyScene(comfySafeIndex, { videoUrl: String(legacyOut.videoUrl || ''), videoPanelOpen: true });
      setComfyVideoLoading(false);
    } catch (e) {
      console.error(e);
      setComfyVideoError(String(e?.message || e));
      setComfyVideoLoading(false);
    }
  }, [comfyNode?.data?.mode, comfyNode?.data?.stylePreset, comfySafeIndex, comfySelectedScene, ensureComfyPromptSynced, startComfyVideoPolling, updateComfyScene]);

  const handleComfyDeleteVideo = useCallback(() => {
    setComfyVideoError('');
    updateComfyScene(comfySafeIndex, { videoUrl: '' });
  }, [comfySafeIndex, updateComfyScene]);

  const handleComfyImagePromptChange = useCallback((value) => {
    const nextRu = String(value || '');
    updateComfyScene(comfySafeIndex, {
      imagePromptRu: nextRu,
      imagePromptSyncStatus: PROMPT_SYNC_STATUS.needsSync,
      imagePromptSyncError: '',
    });
    scheduleComfyPromptSync({ idx: comfySafeIndex, promptType: 'image', ruText: nextRu });
  }, [comfySafeIndex, scheduleComfyPromptSync, updateComfyScene]);

  const handleComfyVideoPromptChange = useCallback((value) => {
    const nextRu = String(value || '');
    updateComfyScene(comfySafeIndex, {
      videoPromptRu: nextRu,
      videoPromptSyncStatus: PROMPT_SYNC_STATUS.needsSync,
      videoPromptSyncError: '',
    });
    scheduleComfyPromptSync({ idx: comfySafeIndex, promptType: 'video', ruText: nextRu });
  }, [comfySafeIndex, scheduleComfyPromptSync, updateComfyScene]);

  const handleGenerateScenarioImage = useCallback(async (slot = "single") => {
    if (!scenarioSelected) return;

    const transitionType = resolveSceneTransitionType(scenarioSelected);
    const normalizedSlot = slot === "start" || slot === "end" ? slot : "single";
    if (transitionType === "continuous" && normalizedSlot === "start" && !!scenarioSelected?.inheritPreviousEndAsStart) {
      return;
    }
    const sceneId = String(scenarioSelected.id || `s${scenarioEditor.selected + 1}`);
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
        updateScenarioScene(scenarioEditor.selected, { startImageUrl: generatedImageUrl, imageFormat });
      } else if (transitionType === "continuous" && normalizedSlot === "end") {
        updateScenarioScene(scenarioEditor.selected, { endImageUrl: generatedImageUrl, imageFormat });
      } else {
        updateScenarioScene(scenarioEditor.selected, { imageUrl: generatedImageUrl, imageFormat });
      }
    } catch (e) {
      console.error(e);
      setScenarioImageError(String(e?.message || e));
    } finally {
      setScenarioImageLoading(false);
    }
  }, [scenarioSelected, scenarioEditor.selected, scenarioScenes, scenarioBrainRefs, updateScenarioScene]);

  const handleClearScenarioImage = useCallback((slot = "single") => {
    setScenarioImageError("");
    const transitionType = resolveSceneTransitionType(scenarioSelected);
    const normalizedSlot = slot === "start" || slot === "end" ? slot : "single";
    if (transitionType === "continuous" && normalizedSlot === "start" && !!scenarioSelected?.inheritPreviousEndAsStart) {
      return;
    }
    if (transitionType === "continuous" && normalizedSlot === "start") {
      updateScenarioScene(scenarioEditor.selected, { startImageUrl: "" });
      return;
    }
    if (transitionType === "continuous" && normalizedSlot === "end") {
      updateScenarioScene(scenarioEditor.selected, { endImageUrl: "" });
      return;
    }
    updateScenarioScene(scenarioEditor.selected, { imageUrl: "" });
  }, [scenarioEditor.selected, scenarioSelected, updateScenarioScene]);

  const handleScenarioVideoTakeAudio = useCallback(async () => {
    if (!scenarioSelected) return;
    if (!globalAudioUrlRaw) {
      setScenarioVideoError("Не найден общий audioUrl в Audio node");
      return;
    }
    const sceneId = String(scenarioSelected.id || `s${scenarioEditor.selected + 1}`);
    const t0 = Number(scenarioSelected.t0 ?? scenarioSelected.start ?? 0);
    const t1 = Number(scenarioSelected.t1 ?? scenarioSelected.end ?? 0);

    setScenarioVideoLoading(true);
    setScenarioVideoError("");
    try {
      const out = await fetchJson("/api/clip/audio-slice", {
        method: "POST",
        body: { sceneId, t0, t1, audioUrl: globalAudioUrlRaw },
      });
      if (!out?.ok || !out?.audioSliceUrl) throw new Error(out?.hint || out?.code || "audio_slice_failed");
      const outT0 = Number(out?.t0 ?? t0);
      const outT1 = Number(out?.t1 ?? t1);
      const expectedDuration = Math.max(0, outT1 - outT0);
      updateScenarioScene(scenarioEditor.selected, {
        audioSliceUrl: String(out.audioSliceUrl || ""),
        audioSliceT0: outT0,
        audioSliceT1: outT1,
        audioSliceExpectedDurationSec: expectedDuration,
        audioSliceBackendDurationSec: normalizeDurationSec(out?.audioSliceBackendDurationSec ?? out?.duration),
        audioSliceActualDurationSec: null,
        audioSliceLoadError: "",
      });
    } catch (e) {
      console.error(e);
      setScenarioVideoError(String(e?.message || e));
    } finally {
      setScenarioVideoLoading(false);
    }
  }, [globalAudioUrlRaw, scenarioEditor.selected, scenarioSelected, updateScenarioScene]);

  const handleScenarioSliceLoadedMetadata = useCallback((event) => {
    if (!scenarioSelected) return;
    const mediaEl = event?.currentTarget || event?.target || null;
    const duration = mediaEl && Number.isFinite(mediaEl.duration) ? Number(mediaEl.duration) : null;
    updateScenarioScene(scenarioEditor.selected, {
      audioSliceActualDurationSec: duration,
      audioSliceLoadError: "",
    });
  }, [scenarioEditor.selected, scenarioSelected, updateScenarioScene]);

  const handleScenarioSliceAudioError = useCallback(() => {
    if (!scenarioSelected) return;
    const msg = "Не удалось загрузить вырезанный mp3-срез. Проверьте URL и наличие файла.";
    updateScenarioScene(scenarioEditor.selected, {
      audioSliceActualDurationSec: null,
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

    if (effectiveLipSync && !scenarioSelected?.audioSliceUrl) {
      setScenarioVideoError("Для lipSync сначала возьмите аудио");
      return;
    }

    const sceneId = String(scenarioSelected.id || `s${scenarioEditor.selected + 1}`);
    const t0 = Number(scenarioSelected.t0 ?? scenarioSelected.start ?? 0);
    const t1 = Number(scenarioSelected.t1 ?? scenarioSelected.end ?? 0);
    const dur = Math.max(0, t1 - t0);
    const requestedDurationSec = dur;

    const continuityBridgePrompt = transitionType === "continuous"
      ? buildContinuousContinuityBridge({ scene: scenarioSelected, previousScene: scenarioPreviousScene })
      : "";

    setScenarioVideoLoading(true);
    setScenarioVideoError("");
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
          imageUrl: frameImageUrl || effectiveStartImageUrl || endImageUrl,
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
        },
      });

      if (out?.ok && out?.jobId) {
        startScenarioVideoPolling({
          jobId: String(out.jobId),
          providerJobId: String(out.providerJobId || ""),
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
          imageUrl: frameImageUrl || effectiveStartImageUrl || endImageUrl,
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
        },
      });
      if (!legacyOut?.ok || !legacyOut?.videoUrl) throw new Error(legacyOut?.hint || legacyOut?.code || "video_generation_failed");
      updateScenarioScene(scenarioEditor.selected, {
        videoUrl: String(legacyOut.videoUrl || ""),
        mode: String(legacyOut.mode || ""),
        model: String(legacyOut.model || ""),
        requestedDurationSec: normalizeDurationSec(legacyOut.requestedDurationSec),
        providerDurationSec: normalizeDurationSec(legacyOut.providerDurationSec),
      });
      openNextSceneWithoutVideo(scenarioEditor.selected);
      setScenarioVideoLoading(false);
    } catch (e) {
      console.error(e);
      setScenarioVideoError(String(e?.message || e));
      setScenarioVideoLoading(false);
    }
  }, [openNextSceneWithoutVideo, scenarioEditor.selected, scenarioPreviousScene, scenarioSelected, scenarioSelectedEffectiveStartImageUrl, startScenarioVideoPolling, updateScenarioScene]);

  const handleScenarioClearVideo = useCallback(() => {
    setScenarioVideoError("");
    updateScenarioScene(scenarioEditor.selected, { videoUrl: "" });
  }, [scenarioEditor.selected, updateScenarioScene]);

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
    setScenarioVideoFocusPulse(true);
    window.setTimeout(() => setScenarioVideoFocusPulse(false), 1200);
    scrollToVideoBlock();
  }, [scenarioSelected, scenarioSelectedEffectiveStartImageUrl]);

  const storyboardScenesForAssembly = useMemo(() => extractStoryboardScenesFromNodes(nodes), [nodes]);

  const assemblyPayload = useMemo(() => {
    const sceneFormat = storyboardScenesForAssembly.find((scene) => String(scene?.imageFormat || "").trim())?.imageFormat || "9:16";
    return buildAssemblyPayload({
      scenes: storyboardScenesForAssembly,
      audioUrl: globalAudioUrlRaw,
      format: sceneFormat,
    });
  }, [globalAudioUrlRaw, storyboardScenesForAssembly]);

  const assemblyPayloadSignature = useMemo(() => buildAssemblyPayloadSignature(assemblyPayload), [assemblyPayload]);

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

  const assemblyStatus = useMemo(() => {
    const hasVideoScenes = assemblyPayload.scenes.length > 0;
    if (isAssembling) return "building";
    if (assemblyBuildState === "done" && assemblyResult?.finalVideoUrl) return "done";
    if (assemblyBuildState === "error") return "error";
    if (!hasVideoScenes && !assemblyResult?.finalVideoUrl) return "empty";
    return "ready";
  }, [isAssembling, assemblyBuildState, assemblyPayload.scenes.length, assemblyResult?.finalVideoUrl]);

  useEffect(() => {
    const estimatedDurationSec = assemblyPayload.scenes.reduce(
      (sum, scene) => sum + (Number(scene.requestedDurationSec) || 0),
      0
    );
    setNodes((prev) => prev.map((n) => {
      if (n.type !== "assemblyNode") return n;
      return {
        ...n,
        data: {
          ...n.data,
          totalScenes: storyboardScenesForAssembly.length,
          readyScenes: assemblyPayload.scenes.length,
          hasAudio: !!assemblyPayload.audioUrl,
          format: assemblyPayload.format,
          durationSec: estimatedDurationSec,
          canAssemble: assemblyPayload.scenes.length > 0 && !isAssembling,
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
          onAssemble: handleAssemblyBuild,
          onStopAssemble: handleAssemblyStop,
        },
      };
    }));
  }, [
    assemblyPayload,
    storyboardScenesForAssembly.length,
    isAssembling,
    assemblyStatus,
    assemblyResult,
    assemblyError,
    assemblyInfo,
    assemblyJobId,
    assemblyProgressPercent,
    assemblyStage,
    assemblyStageLabel,
    assemblyStageCurrent,
    assemblyStageTotal,
    isAssemblyStale,
    handleAssemblyBuild,
    handleAssemblyStop,
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

    setNodes((prev) => {
      const next = prev.map((n) => {
        if (!invalidBrainIds.includes(n.id)) return n;
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

      const staleTargets = edgesNow.filter((e) => invalidBrainIds.includes(e.source)).map((e) => e.target);
      return next.map((n) => {
        if (!staleTargets.includes(n.id)) return n;
        if (n.type === "storyboardNode" || n.type === "resultsNode") {
          return { ...n, data: { ...n.data, isStale: true } };
        }
        if (n.type === "assemblyNode") {
          return { ...n, data: { ...n.data, isStale: true } };
        }
        return n;
      });
    });
    setIsAssemblyStale(true);
  }, [nodes, edges, setNodes]);


  const removeNode = useCallback((nodeId) => {
    setNodes((prev) => prev.filter((n) => n.id !== nodeId));
    setEdges((prev) => prev.filter((e) => e.source !== nodeId && e.target !== nodeId));
  }, [setNodes, setEdges]);

  
  // wire handlers into node.data (keeps render simple)
  const bindHandlers = useCallback(
    (ns) =>
      ns.map((n) => {
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

                setNodes((prev) => prev.map((x) => (x.id === nodeId ? {
                  ...x,
                  data: { ...x.data, isParsing: true, activeParseToken: parseToken, lastParseError: null },
                } : x)));

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

                  const scenes = diverseScenesRaw
                    .map((s, idx) => {
                      const t0 = Number(s.start ?? s.t0 ?? 0);
                      const t1 = Number(s.end ?? s.t1 ?? 0);
                      const prompt = String(s.imagePrompt || s.framePrompt || s.prompt || s.sceneText || `Scene ${idx + 1}`);
                      const transitionType = resolveSceneTransitionType(s);
                      return {
                        id: s.id || `s${String(idx + 1).padStart(2, "0")}`,
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
                        audioSliceLoadError: s.audioSliceLoadError || "",
                        videoUrl: s.videoUrl || "",
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
                        audioSliceStartSec: Number(s.audioSliceStartSec ?? t0),
                        audioSliceEndSec: Number(s.audioSliceEndSec ?? t1),
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

                  setNodes((prev) => {
                    const updated = prev.map((x) =>
                      x.id === nodeId
                        ? {
                            ...x,
                            data: {
                              ...x.data,
                              scenes,
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
                                scenes,
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

        if (n.type === "refNode") {
          return {
            ...base,
            data: {
              ...base.data,
              onPickImage: async (nodeId, file) => {
                const pickedFiles = Array.isArray(file) ? file : (file ? [file] : []);
                if (!pickedFiles.length) return;
                setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: true } } : x)));
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
                        return { ...x, data: { ...x.data, refs: nextRefs } };
                      }));
                    } catch (err) {
                      console.error(err);
                    }
                  }
                } finally {
                  setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: false } } : x)));
                }
              },
              onRemoveImage: (nodeId, idx) => {
                setNodes((prev) => prev.map((x) => {
                  if (x.id !== nodeId) return x;
                  const prevRefs = normalizeRefData(x?.data || {}, x?.data?.kind || "").refs;
                  return { ...x, data: { ...x.data, refs: prevRefs.filter((_, i) => i !== idx) } };
                }));
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
            if (derived.narrativeSource === 'none') critical.push('Недостаточно входных данных');
            if (meaningfulRefRoles.length > 0) warnings.push('Все подключённые ref-ноды участвуют в построении сцен');
            if (!canGenerateComfyImage({ refsByRole: derived.refsByRole }) && (derived.meaningfulText || derived.meaningfulAudio)) {
              warnings.push('Визуальные сцены будут синтезированы из текста, аудио и режима');
            }
            if (!!derived.meaningfulAudio && !derived.meaningfulText && meaningfulRefRoles.length === 0) warnings.push('Сюжет будет выведен из аудио');
            if (!derived.meaningfulAudio && !derived.meaningfulText && meaningfulRefRoles.length > 0) warnings.push('Сюжет будет выведен из референсов и режима');
            if (!!derived.meaningfulAudio && !derived.meaningfulText && meaningfulRefRoles.length > 0) warnings.push('Сюжет будет выведен из аудио и референсов');
            if (!derived.meaningfulAudio && derived.meaningfulText) warnings.push('Таймфреймы будут построены логически, без музыкального ритма');
            if (derived.storyControlMode === 'text_override' && derived.meaningfulAudio) warnings.push('TEXT задаёт сюжет; AUDIO используется для ритма/эмоционального контура');
            if (derived.storyControlMode === 'audio_enhanced_by_text') warnings.push('AUDIO даёт story backbone; TEXT усиливает драму и акценты');
            if (derived.storyControlMode === 'hybrid_balanced') warnings.push('Сюжет формируется совместно из AUDIO и TEXT');
            if (derived.audioStoryMode === 'lyrics_music' && derived.meaningfulAudio) warnings.push('Audio story mode: lyrics+music (можно использовать смысл слов песни).');
            if (derived.audioStoryMode === 'music_only' && derived.meaningfulAudio) warnings.push('Audio story mode: music_only (lyrics игнорируются, сюжет только из музыки/энергии).');
            if (derived.audioStoryMode === 'music_plus_text' && derived.meaningfulAudio) warnings.push('Audio story mode: music_plus_text (lyrics игнорируются, сюжет берётся из TEXT).');
            if (derived.audioStoryMode === 'music_plus_text' && derived.meaningfulAudio && !derived.meaningfulText) warnings.push('music_plus_text выбран, но TEXT пустой: lyrics будут проигнорированы, storyboard будет построен только по музыке/энергии');
            if (derived.outputValue === 'comfy text' && !String(derived.meaningfulText || '').trim()) warnings.push('Для comfy text желательно добавить richer text prompt');
            if (derived.modeValue === 'reklama' && !derived.meaningfulText) warnings.push('Для reklama желательно добавить рекламный тезис в TEXT');

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
              output: derived.outputValue,
              stylePreset: derived.stylePreset,
              freezeStyle: derived.freezeStyle,
              meaningfulText: derived.meaningfulText,
              meaningfulAudio: derived.meaningfulAudio,
              audioDurationSec: derived.meaningfulAudioDurationSec,
              refsByRole: derived.refsByRole,
              narrativeSource: derived.narrativeSource,
              timelineSource: derived.timelineSource,
              storyControlMode: derived.storyControlMode,
              storyMissionSummary: derived.storyMissionSummary,
              audioStoryMode: derived.audioStoryMode,
              textNarrativeRole: derived.narrativeRoles.textNarrativeRole,
              audioNarrativeRole: derived.narrativeRoles.audioNarrativeRole,
              modeIntent: derived.modeSemantics.modeIntent,
              modePromptBias: derived.modeSemantics.modePromptBias,
              modeSceneStrategy: derived.modeSemantics.modeSceneStrategy,
              modeContinuityBias: derived.modeSemantics.modeContinuityBias,
              planningMindset: derived.modeSemantics.planningMindset,
              styleSummary: derived.styleSemantics.styleSummary,
              styleContinuity: derived.styleSemantics.styleContinuity,
              primaryImageAnchor: Object.values(derived.refsByRole).flat().find((item) => item?.url) || null,
              warnings: [...critical, ...warnings],
              coverage: {
                hasText: !!derived.meaningfulText,
                hasAudio: !!derived.meaningfulAudio,
                hasVisualAnchors: canGenerateComfyImage({ refsByRole: derived.refsByRole }),
                roleCoverage,
              },
              anchors,
            };

            const brainSummary = {
              storySource: derived.narrativeSource,
              cast: castLabels,
              world: `location ${derived.refsByRole.location.length ? 'yes' : 'no'} • props ${derived.refsByRole.props.length ? 'yes' : 'no'} • scale ${anchors.worldScaleContext}`,
              style: `${derived.stylePreset}${derived.refsByRole.style.length ? ' + style ref' : ' only'} • ${derived.styleSemantics.styleSummary}`,
              worldCompact: `${derived.refsByRole.location.length ? 'location' : ''}${derived.refsByRole.location.length && derived.refsByRole.props.length ? ' + ' : ''}${derived.refsByRole.props.length ? 'props' : ''}` || 'none',
              styleCompact: `${derived.stylePreset}${derived.refsByRole.style.length ? ' + ref' : ''}`,
              sourceArbitration: `${derived.narrativeSource} • ${derived.storyControlMode}`,
              storyMissionSummary: derived.storyMissionSummary,
              audioStoryMode: derived.audioStoryMode,
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
            nodesNow: nodesRef.current || [],
            edgesNow: edgesRef.current || [],
            normalizeRefDataFn: normalizeRefData,
          });
          const presentation = buildComfyBrainPresentation(derived);

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
              onField: (nodeId, key, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, [key]: value } } : x))),
              onMode: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, mode: value } } : x))),
              onOutput: (nodeId, value) => setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, output: normalizeRenderProfile(value) } } : x))),
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

                const payload = {
                  mode: freshDerived.modeValue,
                  output: freshDerived.outputValue,
                  stylePreset: freshDerived.stylePreset,
                  freezeStyle: freshDerived.freezeStyle,
                  text: freshDerived.meaningfulText || "",
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

                setNodes((prev) => prev.map((x) => {
                  if (x.id === nodeId) {
                    return { ...x, data: { ...x.data, parseStatus: 'parsing' } };
                  }
                  if (comfyStoryTargets.includes(x.id) && x.type === 'comfyStoryboard') {
                    return { ...x, data: { ...x.data, parseStatus: 'updating' } };
                  }
                  return x;
                }));

                let response;
                try {
                  if (USE_COMFY_MOCK) {
                    const plannerMeta = { plannerInput: payload, mode: payload.mode, output: payload.output, stylePreset: payload.stylePreset, narrativeSource: payload.narrativeSource, timelineSource: payload.timelineSource, storyControlMode: payload.storyControlMode, storyMissionSummary: payload.storyMissionSummary, audioStoryMode: payload.audioStoryMode, warnings: [...freshPresentation.critical, ...freshPresentation.warnings], summary: freshPresentation.brainSummary, sceneRoleModel: freshPresentation.sceneRoleModel, referenceSummary: freshPresentation.referenceSummary };
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
                  setNodes((prev) => prev.map((x) => {
                    if (x.id === nodeId) return { ...x, data: { ...x.data, parseStatus: "error", brainCritical: [String(err?.message || err)], brainWarnings: [] } };
                    if (comfyStoryTargets.includes(x.id) && x.type === 'comfyStoryboard') return { ...x, data: { ...x.data, parseStatus: 'error' } };
                    return x;
                  }));
                  return;
                } finally {
                  comfyParseInFlightRef.current.delete(nodeId);
                }

                if (!response?.ok) {
                  setNodes((prev) => prev.map((x) => {
                    if (x.id === nodeId) return { ...x, data: { ...x.data, parseStatus: "error", brainCritical: Array.isArray(response?.errors) ? response.errors : ["COMFY parse failed"], brainWarnings: Array.isArray(response?.warnings) ? response.warnings : [] } };
                    if (comfyStoryTargets.includes(x.id) && x.type === 'comfyStoryboard') return { ...x, data: { ...x.data, parseStatus: 'error' } };
                    return x;
                  }));
                  return;
                }

                const scenes = Array.isArray(response?.scenes) ? response.scenes : [];
                const plannerMeta = response?.planMeta || {};
                const globalContinuity = response?.globalContinuity || "";
                const debugFields = response?.debug || extractComfyDebugFields({ plannerInput: payload, plannerMeta: { ...plannerMeta, globalContinuity } });
                const parsedAt = new Date().toLocaleTimeString();

                setNodes((prev) => prev.map((x) => {
                  if (x.id === nodeId) {
                    return {
                      ...x,
                      data: {
                        ...x.data,
                        parseStatus: 'ready',
                        parsedAt,
                        mockScenes: scenes,
                        lastPlannerMeta: { ...plannerMeta, globalContinuity, debugFields },
                        comfyDebug: debugFields,
                        brainWarnings: Array.isArray(response?.warnings) ? response.warnings : freshPresentation.warnings,
                        brainCritical: Array.isArray(response?.errors) ? response.errors : [],
                      },
                    };
                  }
                  return x;
                }));

                if (comfyStoryTargets.length) {
                  setNodes((prev) => prev.map((x) => (comfyStoryTargets.includes(x.id) && x.type === 'comfyStoryboard')
                    ? {
                        ...x,
                        data: {
                          ...x.data,
                          mockScenes: scenes,
                          sceneCount: scenes.length,
                          mode: freshDerived.modeValue,
                          output: freshDerived.outputValue,
                          stylePreset: freshDerived.stylePreset,
                          narrativeSource: freshDerived.narrativeSource,
                          timelineSource: freshDerived.timelineSource,
                          storyControlMode: freshDerived.storyControlMode,
                          storyMissionSummary: freshDerived.storyMissionSummary,
                          audioStoryMode: freshDerived.audioStoryMode,
                          textNarrativeRole: freshDerived.narrativeRoles.textNarrativeRole,
                          audioNarrativeRole: freshDerived.narrativeRoles.audioNarrativeRole,
                          warnings: Array.isArray(response?.warnings) ? response.warnings : [],
                          summary: freshPresentation.brainSummary,
                          refsByRoleSummary: freshPresentation.referenceSummary,
                          plannerMeta: { ...plannerMeta, globalContinuity },
                          debugFields,
                          pipelineFlow: debugFields.pipelineFlow,
                          parseStatus: 'ready',
                        },
                      }
                    : x));
                }
              },
            },
          };
        }


        if (n.type === "comfyStoryboard") {
          return {
            ...base,
            data: {
              ...base.data,
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

        if (n.type === "refCharacter2" || n.type === "refCharacter3" || n.type === "refAnimal" || n.type === "refGroup") {
          return {
            ...base,
            data: {
              ...base.data,
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
                    return { ...x, data: { ...x.data, refs: oldRefs.concat(uploadedRefs).slice(0, 5) } };
                  }));
                } finally {
                  setNodes((prev) => prev.map((x) => (x.id === nodeId ? { ...x, data: { ...x.data, uploading: false } } : x)));
                }
              },
              onRemoveImage: (nodeId, idx) => setNodes((prev) => prev.map((x) => {
                if (x.id !== nodeId) return x;
                const refs = Array.isArray(x?.data?.refs)
                  ? x.data.refs.filter((_, i) => i !== idx)
                  : [];
                return { ...x, data: { ...x.data, refs } };
              })),
            },
          };
        }

        if (n.type === "assemblyNode") {
          const estimatedDurationSec = assemblyPayload.scenes.reduce(
            (sum, scene) => sum + (Number(scene.requestedDurationSec) || 0),
            0
          );
          return {
            ...base,
            data: {
              ...base.data,
              totalScenes: storyboardScenesForAssembly.length,
              readyScenes: assemblyPayload.scenes.length,
              hasAudio: !!assemblyPayload.audioUrl,
              format: assemblyPayload.format,
              durationSec: estimatedDurationSec,
              canAssemble: assemblyPayload.scenes.length > 0 && !isAssembling,
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
              onAssemble: handleAssemblyBuild,
              onStopAssemble: handleAssemblyStop,
            },
          };
        }
return base;
      }),
    [
      setNodes,
      removeNode,
      edges,
      storyboardScenesForAssembly.length,
      assemblyPayload,
      isAssembling,
      assemblyStatus,
      assemblyResult,
      assemblyError,
      assemblyInfo,
      assemblyJobId,
      assemblyProgressPercent,
      assemblyStage,
      assemblyStageLabel,
      assemblyStageCurrent,
      assemblyStageTotal,
      handleAssemblyBuild,
      handleAssemblyStop,
    ]
  );

const hydrate = useCallback(() => {
    // IMPORTANT: we always have an accountKey (fallback to "guest"), so persistence works even before auth init.
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
      setNodes(bindHandlers(defaultNodes));
      setEdges(defaultEdges);
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

      if (!savedNodes || !savedEdges) throw new Error("bad_format");

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
            data.parseStatus = ["idle", "updating", "ready", "error"].includes(String(data.parseStatus || "")) ? data.parseStatus : "idle";
          }

          if (n.type === "audioNode") {
            delete data.audioType;
            data.uploading = false;
            const normalizedAudioDuration = Number(data.audioDurationSec || 0);
            data.audioDurationSec = normalizedAudioDuration > 0 ? normalizedAudioDuration : null;
          }

          if (n.type === "refNode") {
            const normalized = normalizeRefData(data, data?.kind || "");
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
      const hydratedScenes = extractStoryboardScenesFromNodes(hydratedNodes);
      const hydratedAudioUrl = extractGlobalAudioUrlFromNodes(hydratedNodes);
      const hydratedFormat = hydratedScenes.find((scene) => String(scene?.imageFormat || "").trim())?.imageFormat || "9:16";
      const hydratedPayload = buildAssemblyPayload({
        scenes: hydratedScenes,
        audioUrl: hydratedAudioUrl,
        format: hydratedFormat,
      });
      const hydratedSignature = buildAssemblyPayloadSignature(hydratedPayload);

      setNodes(bindHandlers(hydratedNodes));
      setEdges(hydratedEdges);

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
    } catch {
      setNodes(bindHandlers(defaultNodes));
      setEdges(defaultEdges);
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
      }, 0);
    }
  }, [STORE_KEY, setNodes, setEdges, defaultNodes, defaultEdges, accountKey, user]);

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
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — ПЕРСОНАЖ", icon: "🧍", kind: "ref_character", refs: [], uploading: false } };
    } else if (type === "ref_location") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — ЛОКАЦИЯ", icon: "📍", kind: "ref_location", refs: [], uploading: false } };
    } else if (type === "ref_style") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — СТИЛЬ", icon: "🎨", kind: "ref_style", refs: [], uploading: false } };
    } else if (type === "ref_items") {
      node = { id, type: "refNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { title: "REF — ПРЕДМЕТЫ", icon: "📦", kind: "ref_items", refs: [], uploading: false } };
    } else if (type === "storyboard") {
      node = { id, type: "storyboardNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { scenes: [] } };
    } else if (type === "assembly") {
      node = { id, type: "assemblyNode", position: { x: centerX + jitterX, y: centerY + jitterY }, data: {} };
    } else if (type === "comfyBrain") {
      node = { id, type: "comfyBrain", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'clip', output: 'comfy image', audioStoryMode: 'lyrics_music', styleKey: 'realism', freezeStyle: false, parseStatus: 'idle' } };
    } else if (type === "comfyStoryboard") {
      node = { id, type: "comfyStoryboard", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mockScenes: [], sceneCount: 0, mode: 'clip', parseStatus: 'idle' } };
    } else if (type === "comfyVideoPreview") {
      node = { id, type: "comfyVideoPreview", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { previewStatus: 'idle', previewUrl: '', workflowPreset: 'comfy-default', format: '9:16', duration: 0 } };
    } else if (type === "refCharacter2") {
      node = { id, type: "refCharacter2", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'ally', name: '', identityLock: false, priority: 'normal', notes: '', refs: [], uploading: false } };
    } else if (type === "refCharacter3") {
      node = { id, type: "refCharacter3", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'ally', name: '', identityLock: false, priority: 'normal', notes: '', refs: [], uploading: false } };
    } else if (type === "refAnimal") {
      node = { id, type: "refAnimal", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'single animal', speciesHint: '', scaleLock: false, behavior: 'neutral', notes: '', refs: [], uploading: false } };
    } else if (type === "refGroup") {
      node = { id, type: "refGroup", position: { x: centerX + jitterX, y: centerY + jitterY }, data: { mode: 'crowd', density: 'medium', formation: '', outfitConsistency: 'varied outfit', notes: '', refs: [], uploading: false } };
    } else {
      return;
    }

    setNodes((prev) => bindHandlers(prev.concat(node)));
    setDrawerOpen(false);
  }, [setNodes, bindHandlers]);


  // hydrate on account change
  useEffect(() => {
    hydrate();
  }, [hydrate]);

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
      // wait a tick so AuthContext can update ps:lastUserId/ps:lastEmail
      setTimeout(() => {
        hydrate();
      }, 0);
    };
    window.addEventListener("ps:sessionChanged", onSessionChanged);
    return () => window.removeEventListener("ps:sessionChanged", onSessionChanged);
  }, [hydrate]);




  // persist
  useEffect(() => {
    if (!didHydrateRef.current) return;
    if (isHydratingRef.current) return;

    // strip handlers from data
    const serialNodes = serializeNodesForStorage(nodes);
    const serialEdges = edges.map((e) => ({ id: e.id, source: e.source, sourceHandle: e.sourceHandle || null, target: e.target, targetHandle: e.targetHandle || null }));

    const ok = safeSet(STORE_KEY, JSON.stringify({
      nodes: serialNodes,
      edges: serialEdges,
      assemblyResult: assemblyResult?.finalVideoUrl
        ? {
          finalVideoUrl: String(assemblyResult.finalVideoUrl || ""),
          sceneCount: Number(assemblyResult.sceneCount || 0),
          audioApplied: !!assemblyResult.audioApplied,
        }
        : null,
      assemblyBuildState: assemblyBuildState === "done" ? "done" : "idle",
      assemblyPayloadSignature,
    }));
    if (ok) setLastSavedAt(Date.now());
  }, [nodes, edges, STORE_KEY, accountKey, assemblyResult, assemblyBuildState, assemblyPayloadSignature]);

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
      assemblyResult: assemblyResult?.finalVideoUrl
        ? {
          finalVideoUrl: String(assemblyResult.finalVideoUrl || ""),
          sceneCount: Number(assemblyResult.sceneCount || 0),
          audioApplied: !!assemblyResult.audioApplied,
        }
        : null,
      assemblyBuildState: assemblyBuildState === "done" ? "done" : "idle",
      assemblyPayloadSignature,
    }));
      if (ok) setLastSavedAt(Date.now());
    };

    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [nodes, edges, STORE_KEY, accountKey, assemblyResult, assemblyBuildState, assemblyPayloadSignature]);


  const nodeTypes = useMemo(
    () => ({
      audioNode: AudioNode,
      textNode: TextNode,
      brainNode: BrainNode,
      refNode: RefNode,
      storyboardNode: StoryboardPlanNode,
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
          const ok =
            (h === 'audio' && src.type === 'audioNode' && (params.sourceHandle || '') === 'audio') ||
            (h === 'text' && src.type === 'textNode' && (params.sourceHandle || '') === 'text') ||
            (h === 'ref_character_1' && src.type === 'refNode' && (params.sourceHandle || '') === 'ref_character') ||
            (h === 'ref_character_2' && src.type === 'refCharacter2' && (params.sourceHandle || '') === 'ref_character_2') ||
            (h === 'ref_character_3' && src.type === 'refCharacter3' && (params.sourceHandle || '') === 'ref_character_3') ||
            (h === 'ref_animal' && src.type === 'refAnimal' && (params.sourceHandle || '') === 'ref_animal') ||
            (h === 'ref_group' && src.type === 'refGroup' && (params.sourceHandle || '') === 'ref_group') ||
            (h === 'ref_location' && src.type === 'refNode' && (params.sourceHandle || '') === 'ref_location') ||
            (h === 'ref_style' && src.type === 'refNode' && (params.sourceHandle || '') === 'ref_style') ||
            (h === 'ref_props' && src.type === 'refNode' && (params.sourceHandle || '') === 'ref_items');
          if (!ok) return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || '') === h));
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || '', targetHandle: h, sourceType: src.type, targetType: dst.type });
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

        if (src.type === 'comfyStoryboard' && (params.sourceHandle || '') === 'comfy_scene_video_out') {
          if (dst.type !== 'comfyVideoPreview' || (params.targetHandle || '') !== 'comfy_scene_video_out') return eds;
          const cleaned = eds.filter((e) => !(e.target === dst.id && (e.targetHandle || '') === 'comfy_scene_video_out'));
          const presentation = getEdgePresentation({ sourceHandle: params.sourceHandle || '', targetHandle: params.targetHandle || '', sourceType: src.type, targetType: dst.type });
          return addEdge({ ...params, className: presentation.className, animated: presentation.animated, style: presentation.style, data: { kind: presentation.kind } }, cleaned);
        }

        if (src.type === 'comfyStoryboard' || src.type === 'comfyBrain' || src.type === 'comfyVideoPreview' || dst.type === 'comfyStoryboard' || dst.type === 'comfyBrain' || dst.type === 'comfyVideoPreview') {
          return eds;
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
      if (e.key === "Escape") setDrawerOpen(false);
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
                  return (
                    <button
                    key={getScenarioSceneStableKey(s, i)}
                    ref={(node) => {
                      if (node) scenarioItemRefs.current.set(i, node);
                      else scenarioItemRefs.current.delete(i);
                    }}
                    className={"clipSB_scenarioItem"
                      + (i === scenarioEditor.selected ? " isActive" : "")}
                    onClick={() => setScenarioEditor((x) => ({ ...x, selected: i }))}
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
                            {i === recommendedNextSceneIndex ? <div className="clipSB_tag clipSB_tagNext">NEXT</div> : null}
                            {isLipSyncScene(s) ? <div className="clipSB_tag">LS</div> : null}
                          </div>
                        </div>
                        <div className="clipSB_scenarioItemText">{getSceneUiDescription(s).slice(0, 90)}</div>
                      </div>
                    </div>
                    </button>
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
                            {(scenarioSelectedEffectiveStartImageUrl || scenarioSelected.endImageUrl || scenarioSelected.imageUrl) ? (
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
                            {scenarioSelected.imageUrl ? (
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
                        <span>transitionType</span><span>{scenarioSelectedTransitionType}</span>
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
                            key={`slice-${String(scenarioSelected.id || scenarioEditor.selected)}-${String(scenarioSelectedAudioSliceUrl || "")}`}
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

                      {scenarioSelected.audioSliceLoadError ? (
                        <div className="clipSB_hint" style={{ color: "#ff8a8a", marginTop: 6 }}>{scenarioSelected.audioSliceLoadError}</div>
                      ) : null}
                    </div>

                    {scenarioVideoOpen ? (
                      <div ref={scenarioVideoCardRef} className={`clipSB_scenarioEditRow clipSB_videoBlock${scenarioVideoFocusPulse ? " clipSB_videoBlockPulse" : ""}`}>
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
                                    key={String(scenarioSelected.id || scenarioEditor.selected) + ":" + String(scenarioSelected.videoUrl || "")}
                                    className="clipSB_videoPlayer"
                                    controls
                                    playsInline
                                    preload="metadata"
                                    src={scenarioSelected.videoUrl}
                                    poster={getSceneVideoPoster(scenarioSelected, scenarioPreviousScene)}
                                  />
                                ) : getSceneVideoPoster(scenarioSelected, scenarioPreviousScene) ? (
                                  <img
                                    className="clipSB_videoPoster"
                                    src={getSceneVideoPoster(scenarioSelected, scenarioPreviousScene)}
                                    alt="video preview"
                                  />
                                ) : (
                                  <div className="clipSB_videoFramePlaceholder">RESULT</div>
                                )}

                                {scenarioVideoLoading ? (
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
                                key={String(scenarioSelected.id || scenarioEditor.selected) + ":" + String(scenarioSelected.videoUrl || "")}
                                className="clipSB_videoPlayer"
                                controls
                                playsInline
                                preload="metadata"
                                src={scenarioSelected.videoUrl}
                                poster={getSceneVideoPoster(scenarioSelected, scenarioPreviousScene)}
                              />
                            ) : getSceneVideoPoster(scenarioSelected, scenarioPreviousScene) ? (
                              <img className="clipSB_videoPoster" src={getSceneVideoPoster(scenarioSelected, scenarioPreviousScene)} alt="poster" />
                            ) : null}

                            {scenarioVideoLoading ? (
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
                          <div className="clipSB_videoKv"><span>transitionActionPrompt</span><span>{scenarioSelected.transitionActionPrompt || scenarioSelected.videoPrompt || "—"}</span></div>
                          <div className="clipSB_videoKv"><span>audioSliceUrl</span><span>{scenarioSelected.audioSliceUrl || "—"}</span></div>
                          <div className="clipSB_videoKv"><span>videoUrl</span><span>{scenarioSelected.videoUrl || "—"}</span></div>
                        </details>
                        <div style={{ display: "flex", gap: 8, marginTop: 8, flexWrap: "wrap" }}>
                          <button className="clipSB_btn clipSB_btnSecondary" onClick={handleScenarioVideoTakeAudio} disabled={scenarioVideoLoading || !globalAudioUrlRaw}>
                            Взять аудио
                          </button>
                          <button
                            className="clipSB_btn clipSB_btnSecondary"
                            onClick={handleScenarioGenerateVideo}
                            disabled={scenarioVideoLoading || !(scenarioSelectedTransitionType === "continuous" ? (scenarioSelectedEffectiveStartImageUrl || scenarioSelected.endImageUrl || scenarioSelected.imageUrl) : scenarioSelected.imageUrl) || (scenarioSelectedIsLipSync && !scenarioSelected.audioSliceUrl)}
                          >
                            Сгенерировать видео
                          </button>
                          <button className="clipSB_btn clipSB_btnSecondary" onClick={handleScenarioClearVideo} disabled={scenarioVideoLoading}>
                            Очистить видео
                          </button>
                        </div>
                        {scenarioVideoError ? <div className="clipSB_hint" style={{ color: "#ff8a8a", marginTop: 6 }}>{scenarioVideoError}</div> : null}
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
                          {hasVideo ? <span className="clipSB_tag clipSB_tagOk">video-ready</span> : null}
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
                      <div className="clipSB_comfyInfoGrid">
                        <div className="clipSB_comfyKv"><span>Сцена</span><strong>{comfySelectedScene.title || '—'}</strong></div>
                        <div className="clipSB_comfyKv"><span>Время</span><strong>{Number.isFinite(Number(comfySelectedScene.startSec)) && Number.isFinite(Number(comfySelectedScene.endSec)) ? `${Number(comfySelectedScene.startSec).toFixed(1)}–${Number(comfySelectedScene.endSec).toFixed(1)}s` : `${Number(comfySelectedScene.durationSec || 0).toFixed(1)}s`}</strong></div>
                        <div className="clipSB_comfyKv clipSB_comfyKvWide"><span>Цель</span><strong>{comfySelectedScene.sceneGoal || comfySelectedScene.sceneNarrativeStep || '—'}</strong></div>
                      </div>
                    </div>

                    <div className="clipSB_comfySection">
                      <div className="clipSB_comfyBlockTitle">Континуити</div>
                      <pre className="clipSB_comfyContinuity">{formatContinuityForDisplay(comfySelectedScene.continuity || comfyNode?.data?.plannerMeta?.globalContinuity || '—')}</pre>
                    </div>

                    <div className="clipSB_comfySection">
                      <div className="clipSB_comfyBlockTitle">IMAGE · {comfyModeMeta.labelRu} / {comfyStyleMeta.labelRu}</div>
                      <div className="clipSB_comfySplitGrid">
                        <div className="clipSB_comfySplitCol">
                          <div className="clipSB_hint">Image prompt (RU) · {COMFY_SYNC_STATUS_LABELS[comfySelectedScene.imagePromptSyncStatus] || '—'}</div>
                          <textarea
                            className="clipSB_textarea clipSB_comfyTextarea"
                            value={String(comfySelectedScene.imagePromptRu || '')}
                            onChange={(e) => handleComfyImagePromptChange(e.target.value)}
                            placeholder="Опиши визуал сцены для генерации изображения"
                          />
                          <div className="clipSB_small" style={{ marginTop: 6 }}>EN (model): {String(comfySelectedScene.imagePromptEn || '—')}</div>
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
                            <div className="clipSB_hint">Video prompt (RU) · {COMFY_SYNC_STATUS_LABELS[comfySelectedScene.videoPromptSyncStatus] || '—'}</div>
                            <textarea
                              className="clipSB_textarea clipSB_comfyTextarea"
                              value={String(comfySelectedScene.videoPromptRu || '')}
                              onChange={(e) => handleComfyVideoPromptChange(e.target.value)}
                              placeholder="Опиши действие камеры и движение в кадре"
                              disabled={!comfySelectedScene.imageUrl}
                            />
                            <div className="clipSB_small" style={{ marginTop: 6 }}>EN (model): {String(comfySelectedScene.videoPromptEn || '—')}</div>
                            {(String(comfySelectedScene.videoPromptSyncError || '').trim()) ? (
                              <div className="clipSB_hint" style={{ marginTop: 6, color: '#ff8a8a' }}>{String(comfySelectedScene.videoPromptSyncError || '')}</div>
                            ) : null}
                            {comfySelectedScene.videoPromptSyncStatus === PROMPT_SYNC_STATUS.syncError ? (
                              <button className="clipSB_btn clipSB_btnSecondary" style={{ marginTop: 8 }} onClick={async () => { try { await syncComfyPrompt({ idx: comfySafeIndex, promptType: 'video', force: true }); } catch (e) { console.error(e); } }}>Retry sync video</button>
                            ) : null}
                            <div className="clipSB_comfyActions">
                              <button className="clipSB_btn" onClick={handleComfyGenerateVideo} disabled={!comfySelectedScene.imageUrl || comfyVideoLoading || comfySelectedScene.videoPromptSyncStatus === PROMPT_SYNC_STATUS.syncing}>{comfyVideoLoading ? 'Делаю...' : (comfySelectedScene.videoPromptSyncStatus === PROMPT_SYNC_STATUS.syncing ? 'Синхронизация...' : 'Сделать видео')}</button>
                              <button className="clipSB_btn clipSB_btnSecondary" onClick={handleComfyDeleteVideo} disabled={comfyVideoLoading}>Удалить видео</button>
                            </div>
                          </div>
                          <div className="clipSB_comfySplitCol">
                            <div className="clipSB_hint">Video preview / status</div>
                            <div className="clipSB_comfyPreviewBox">
                              {comfySelectedScene.videoUrl ? (
                                <video className="clipSB_scenarioPreview" src={resolveAssetUrl(comfySelectedScene.videoUrl)} controls />
                              ) : (comfyVideoLoading || comfyHasActiveVideoJobForScene) ? (
                                <div className="clipSB_comfyPreviewEmpty">Генерация видео…</div>
                              ) : !comfySelectedScene.imageUrl ? (
                                <div className="clipSB_comfyPreviewEmpty">Сначала создайте изображение для этой сцены</div>
                              ) : (
                                <div className="clipSB_comfyPreviewEmpty">Видео ещё не создано</div>
                              )}
                            </div>
                          </div>
                        </div>
                        {comfyVideoError ? <div className="clipSB_hint" style={{ color: '#ff8a8a' }}>{comfyVideoError}</div> : null}
                      </div>
                    ) : null}

                    <details className="clipSB_scenarioEditRow" style={{ marginTop: 8 }}>
                      <summary className="clipSB_hint" style={{ cursor: 'pointer' }}>DEBUG</summary>
                      <div className="clipSB_small" style={{ marginTop: 8 }}>pipeline: {(Array.isArray(comfyNode?.data?.pipelineFlow) ? comfyNode.data.pipelineFlow.join(' → ') : 'brain → scene image → scene video')}</div>
                      <div className="clipSB_small">режим: {comfyModeMeta.labelRu} • стиль: {comfyStyleMeta.labelRu}</div>
                      <div className="clipSB_small">narrative source: {comfyNode?.data?.narrativeSource || 'none'}</div>
                      <div className="clipSB_small">audioDurationSec: {comfyNode?.data?.plannerMeta?.audioDurationSec ?? '—'}</div>
                      <div className="clipSB_small">timelineDurationSec: {comfyNode?.data?.plannerMeta?.timelineDurationSec ?? '—'}</div>
                      <div className="clipSB_small">sceneDurationTotalSec: {comfyNode?.data?.plannerMeta?.sceneDurationTotalSec ?? '—'}</div>
                      <div className="clipSB_small">warnings: {(Array.isArray(comfyNode?.data?.warnings) ? comfyNode.data.warnings.join(' | ') : '') || 'none'}</div>
                    </details>
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
