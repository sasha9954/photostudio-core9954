import { buildGlobalMusicPromptFromStructuredMusic } from "./comfyNarrativeDomain";

const normalizeText = (value) => String(value || "").trim();
const SCENARIO_STORYBOARD_TRACE = false;
const CLIP_TRACE_SCENARIO_FORMAT = false;
const CLIP_TRACE_SCENARIO_GLOBAL_MUSIC = false;
const SCENARIO_ROLE_TRACE_SCENE_ID = "TRACE_SCENE_2P_001";

function shouldTraceScenarioRoleScene(sceneId = "") {
  const needle = normalizeText(SCENARIO_ROLE_TRACE_SCENE_ID);
  if (!needle) return false;
  return normalizeText(sceneId) === needle;
}
export const SCENARIO_LTX_WORKFLOW_MAP = {
  i2v: "i2v",
  f_l: "f_l",
  continuation: "continuation",
  lip_sync: "lip_sync_music",
  lip_sync_music: "lip_sync_music",
  i2v_sound: "i2v_sound",
  f_l_sound: "f_l_sound",
};
const SCENARIO_LEGACY_WORKFLOW_ALIASES = {
  i2v_as: "i2v",
  f_l_as: "f_l",
  lip_sync_music: "lip_sync_music",
  lip_sync: "lip_sync_music",
};
const SCENARIO_LEGACY_WORKFLOW_FILENAME_TO_KEY = {
  "image-video.json": "i2v",
  "image-video-golos-zvuk.json": "i2v_sound",
  "imag-imag-video-bz.json": "f_l",
  "imag-imag-video-zvuk.json": "f_l_sound",
  "image-lipsink-video-music.json": "lip_sync_music",
};
export const SCENARIO_MODEL_KEY_TO_SPEC = {
  ltx23_dev_fp8: {
    key: "ltx23_dev_fp8",
    ckpt_name: "ltx-2.3-22b-dev-fp8.safetensors",
    compatible_workflow_keys: ["i2v", "f_l"],
  },
  ltx23_distilled_fp8: {
    key: "ltx23_distilled_fp8",
    ckpt_name: "ltx-2.3-22b-distilled-fp8.safetensors",
    compatible_workflow_keys: ["i2v", "f_l"],
  },
  ltx23_dev_fp16: {
    key: "ltx23_dev_fp16",
    ckpt_name: "ltx-2.3-22b-dev-fp16.safetensors",
    compatible_workflow_keys: ["i2v", "f_l"],
  },
  ltx23_distilled_fp16: {
    key: "ltx23_distilled_fp16",
    ckpt_name: "ltx-2.3-22b-distilled-fp16.safetensors",
    compatible_workflow_keys: ["i2v", "f_l"],
  },
  ltx23_13b_dev_fp8: {
    key: "ltx23_13b_dev_fp8",
    ckpt_name: "ltx-2.3-13b-dev-fp8.safetensors",
    compatible_workflow_keys: ["i2v", "f_l"],
  },
  ltx23_13b_distilled_fp8: {
    key: "ltx23_13b_distilled_fp8",
    ckpt_name: "ltx-2.3-13b-distilled-fp8.safetensors",
    compatible_workflow_keys: ["i2v", "f_l"],
  },
};
const SCENARIO_WORKFLOW_DEFAULT_MODEL_KEY = {
  i2v: "ltx23_dev_fp8",
  lip_sync_music: "ltx23_dev_fp8",
  i2v_sound: "ltx23_dev_fp8",
  f_l_sound: "ltx23_distilled_fp8",
  f_l: "ltx23_distilled_fp8",
};
const SCENARIO_MODEL_KEY_ALIASES = {
  "ltx-2.3": "ltx23_dev_fp8",
  "ltx23-dev-fp8": "ltx23_dev_fp8",
  "ltx23_dev_fp8": "ltx23_dev_fp8",
  "ltx-2.3-22b-dev-fp8.safetensors": "ltx23_dev_fp8",
  "ltx23-distilled-fp8": "ltx23_distilled_fp8",
  "ltx23_distilled_fp8": "ltx23_distilled_fp8",
  "ltx-2.3-22b-distilled-fp8.safetensors": "ltx23_distilled_fp8",
  "ltx23_dev_fp16": "ltx23_dev_fp16",
  "ltx23-dev-fp16": "ltx23_dev_fp16",
  "ltx-2.3-22b-dev-fp16.safetensors": "ltx23_dev_fp16",
  "ltx23_distilled_fp16": "ltx23_distilled_fp16",
  "ltx23-distilled-fp16": "ltx23_distilled_fp16",
  "ltx-2.3-22b-distilled-fp16.safetensors": "ltx23_distilled_fp16",
  "ltx23_13b_dev_fp8": "ltx23_13b_dev_fp8",
  "ltx23-13b-dev-fp8": "ltx23_13b_dev_fp8",
  "ltx-2.3-13b-dev-fp8.safetensors": "ltx23_13b_dev_fp8",
  "ltx23_13b_distilled_fp8": "ltx23_13b_distilled_fp8",
  "ltx23-13b-distilled-fp8": "ltx23_13b_distilled_fp8",
  "ltx-2.3-13b-distilled-fp8.safetensors": "ltx23_13b_distilled_fp8",
};
const DEFAULT_GLOBAL_VISUAL_LOCK = {
  captureStyle: "cinematic commercial realism",
  cameraLanguage: "controlled cinematic camera",
  lensFeel: "consistent medium focal cinematic lens compression",
  lightingStyle: "soft directional key, controlled contrast, realistic bounce light",
  colorGrade: "natural cinematic grade, balanced contrast, soft highlight rolloff",
  imageDensity: "high-end clean detailed natural texture",
  productionConsistency: "all scenes must feel captured by the same production setup",
  continuityRule: "all scenes must feel captured by the same production setup",
  forbiddenDrift: [
    "no drastic lighting changes",
    "no sudden palette shifts",
    "no image quality drops",
    "no style jumps",
    "no camera language changes",
    "no texture density drift",
  ],
};
const DEFAULT_GLOBAL_CAMERA_PROFILE = {
  lensProfile: "cinematic medium focal length, natural perspective, soft background separation",
  exposureProfile: "balanced cinematic exposure, protected highlights, readable shadows",
  dynamicRangeProfile: "wide dynamic range feel, soft highlight rolloff, no clipped look",
  sharpnessProfile: "clean but natural detail, no over-sharpened AI look",
  textureProfile: "natural skin/material texture, premium production clarity",
  motionProfile: "controlled cinematic movement, no random camera behavior",
  continuityProfile: "same capture system feel across all scenes",
  forbiddenCameraDrift: [
    "no abrupt focal length changes unless scene explicitly requires it",
    "no exposure jumps between scenes",
    "no contrast regime shifts",
    "no sharpness inconsistency",
    "no change in cinematic capture feel",
  ],
};
function normalizeStringScalar(value) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value).trim();
  return "";
}

const normalizeStringList = (value) => {
  if (Array.isArray(value)) {
    return Array.from(new Set(value.map((item) => normalizeStringScalar(item)).filter(Boolean)));
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? [trimmed] : [];
  }
  if (typeof value === "number" || typeof value === "boolean") return [String(value).trim()];
  return [];
};

function normalizeObjectMap(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function isNonEmptyObject(value) {
  return !!(value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length > 0);
}

function isNonEmptyArray(value) {
  return Array.isArray(value) && value.length > 0;
}

function isNonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function isNonEmptyValue(value) {
  if (isNonEmptyObject(value) || isNonEmptyArray(value) || isNonEmptyString(value)) return true;
  return typeof value === "number" || typeof value === "boolean";
}

function mergeNestedMapValuePreferNonEmpty(primary, fallback) {
  if (Array.isArray(primary) || Array.isArray(fallback)) {
    const primaryList = Array.isArray(primary) ? primary.filter((item) => isNonEmptyValue(item)) : [];
    const fallbackList = Array.isArray(fallback) ? fallback.filter((item) => isNonEmptyValue(item)) : [];
    if (!primaryList.length) return fallbackList;
    if (!fallbackList.length) return primaryList;
    return Array.from(new Set([...primaryList, ...fallbackList]));
  }
  if (isNonEmptyObject(primary) || isNonEmptyObject(fallback)) {
    const primaryMap = normalizeObjectMap(primary);
    const fallbackMap = normalizeObjectMap(fallback);
    const keys = Array.from(new Set([...Object.keys(fallbackMap), ...Object.keys(primaryMap)]));
    return keys.reduce((acc, key) => {
      const merged = mergeNestedMapValuePreferNonEmpty(primaryMap[key], fallbackMap[key]);
      if (isNonEmptyValue(merged)) acc[key] = merged;
      return acc;
    }, {});
  }
  if (isNonEmptyValue(primary)) return primary;
  if (isNonEmptyValue(fallback)) return fallback;
  return undefined;
}

function mergeObjectMapsPreferNonEmpty(primary, fallback) {
  const primaryMap = normalizeObjectMap(primary);
  const fallbackMap = normalizeObjectMap(fallback);
  if (!Object.keys(primaryMap).length) return fallbackMap;
  if (!Object.keys(fallbackMap).length) return primaryMap;
  const keys = Array.from(new Set([...Object.keys(fallbackMap), ...Object.keys(primaryMap)]));
  return keys.reduce((acc, key) => {
    const merged = mergeNestedMapValuePreferNonEmpty(primaryMap[key], fallbackMap[key]);
    if (isNonEmptyValue(merged)) acc[key] = merged;
    return acc;
  }, {});
}

function mergeStringListsPreferNonEmpty(primary, fallback) {
  const primaryList = normalizeStringList(primary);
  const fallbackList = normalizeStringList(fallback);
  if (!primaryList.length) return fallbackList;
  if (!fallbackList.length) return primaryList;
  return Array.from(new Set([...fallbackList, ...primaryList]));
}

function mergeStructuredPreferNonEmpty(primary, fallback, emptyValue = {}) {
  if (Array.isArray(primary) && Array.isArray(fallback)) {
    return Array.from(new Set([...fallback, ...primary].filter((item) => isNonEmptyValue(item))));
  }
  if (isNonEmptyObject(primary) && isNonEmptyObject(fallback)) {
    return { ...fallback, ...primary };
  }
  if (isNonEmptyValue(primary)) return primary;
  if (isNonEmptyValue(fallback)) return fallback;
  return emptyValue;
}

function toNumber(value, fallback = 0) {
  const direct = Number(value);
  if (Number.isFinite(direct)) return direct;
  const match = String(value || "").match(/-?\d+(?:\.\d+)?/);
  if (match) {
    const parsed = Number(match[0]);
    if (Number.isFinite(parsed)) return parsed;
  }
  return fallback;
}

function firstFiniteNumber(valueMap = {}, keys = []) {
  for (const key of keys) {
    const value = Number(valueMap?.[key]);
    if (Number.isFinite(value)) return value;
  }
  return null;
}

export function resolveSceneDisplayTime(scene = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const timelineStart = firstFiniteNumber(source, ["timeStart", "time_start", "t0", "start", "startSec"]);
  const timelineEnd = firstFiniteNumber(source, ["timeEnd", "time_end", "t1", "end", "endSec"]);
  const hasTimeline = timelineStart != null || timelineEnd != null;

  if (hasTimeline) {
    const startSec = timelineStart != null ? timelineStart : 0;
    const endSec = timelineEnd != null ? timelineEnd : startSec;
    return {
      startSec,
      endSec: Math.max(startSec, endSec),
      source: "timeline",
    };
  }

  const audioSliceStartSec = firstFiniteNumber(source, ["audioSliceStartSec", "audio_slice_start_sec", "audioSliceT0"]);
  const audioSliceEndSec = firstFiniteNumber(source, ["audioSliceEndSec", "audio_slice_end_sec", "audioSliceT1"]);
  const startSec = audioSliceStartSec != null ? audioSliceStartSec : 0;
  const endSec = audioSliceEndSec != null ? audioSliceEndSec : startSec;
  return {
    startSec,
    endSec: Math.max(startSec, endSec),
    source: "audio_slice",
  };
}

function normalizePromptForCompare(value = "") {
  return String(value || "")
    .toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function tokenOverlapRatio(first = "", second = "") {
  const left = new Set(normalizePromptForCompare(first).split(" ").filter(Boolean));
  const right = new Set(normalizePromptForCompare(second).split(" ").filter(Boolean));
  if (!left.size || !right.size) return 0;
  const overlap = [...left].filter((token) => right.has(token)).length;
  return overlap / Math.max(left.size, right.size);
}

function hasAnyKeyword(value = "", keywords = []) {
  const text = normalizePromptForCompare(value);
  if (!text) return false;
  return keywords.some((keyword) => text.includes(String(keyword || "").toLowerCase()));
}

const META_BANNED_PHRASES = [
  "baseline composition",
  "pre-change state",
  "resolved changed state",
  "a→b",
  "a->b",
  "must be readable",
  "subject-position delta",
  "subject position delta",
  "visibly evolve",
  "new state",
  "represents",
  "this scene symbolizes",
  "the cycle begins anew",
  "dramatic purpose",
  "beat function",
  "progression",
  "transition family",
  "hero arc",
  "world arc",
  "scene purpose",
];

function sanitizeVisiblePromptText(value = "") {
  let text = normalizeText(value);
  if (!text) return "";
  META_BANNED_PHRASES.forEach((phrase) => {
    const escaped = phrase.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    text = text.replace(new RegExp(escaped, "ig"), " ");
  });
  text = text
    .replace(/\s*[-–—]{1,2}>\s*/g, " ")
    .replace(/\b[A-Za-zА-Яа-яЁё]\s*[→➜]\s*[A-Za-zА-Яа-яЁё]\b/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!text) return "";

  const sentences = text.split(/(?<=[.!?])\s+/).map((item) => item.trim()).filter(Boolean);
  if (sentences.length <= 1) return text;
  const unique = [];
  const seen = new Set();
  sentences.forEach((sentence) => {
    const key = normalizePromptForCompare(sentence);
    if (!key || seen.has(key)) return;
    seen.add(key);
    unique.push(sentence);
  });
  return unique.join(" ").trim();
}

function buildVisibleVideoPrompt(scene = {}, fallbackPrompt = "") {
  const source = scene && typeof scene === "object" ? scene : {};
  const identity = sanitizeVisiblePromptText(
    source.focalSubject
    || source.sceneAction
    || source.summaryEn
    || source.summaryRu
    || source.sceneGoalEn
    || source.sceneGoalRu
    || ""
  );
  const characterAction = sanitizeVisiblePromptText(source.characterAction || source.motion || source.sceneAction || "");
  const camera = sanitizeVisiblePromptText(source.cameraMotion || source.cameraIntent || source.cameraEn || "");
  const atmosphere = sanitizeVisiblePromptText(source.environmentMotion || source.environment || source.locationEn || "");
  const base = sanitizeVisiblePromptText(fallbackPrompt);
  const parts = [
    "Cinematic 9:16 vertical shot",
    identity,
    characterAction,
    camera,
    atmosphere,
  ].filter(Boolean);
  return sanitizeVisiblePromptText(parts.join(", ")) || base;
}

function translatePlannerMetaToVisiblePrompt(scene = {}, { mode = "image" } = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const visibleInputs = [
    source.frameDescription,
    source.visualDescription,
    source.visualPrompt,
    source.sceneAction,
    source.characterAction,
    source.cameraMotion,
    source.environmentMotion,
    source.sceneGoalEn,
    source.sceneGoalRu,
  ];
  const cleaned = visibleInputs.map((item) => sanitizeVisiblePromptText(item)).filter(Boolean).join(", ");
  if (mode === "video") {
    return buildVisibleVideoPrompt(source, cleaned);
  }
  return cleaned;
}

function ensureDistinctStartEndPrompts(scene = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const startRaw = sanitizeVisiblePromptText(source.startFramePromptEn || source.startFramePromptRu || "");
  const endRaw = sanitizeVisiblePromptText(source.endFramePromptEn || source.endFramePromptRu || "");
  const overlap = tokenOverlapRatio(startRaw, endRaw);
  const isLipSync = Boolean(source.lipSync || source.requiresAudioSensitiveVideo || source.renderMode === "lip_sync_music");
  if (startRaw && endRaw && overlap < 0.82) {
    return { startFramePrompt: startRaw, endFramePrompt: endRaw };
  }

  const identity = sanitizeVisiblePromptText(source.focalSubject || source.summaryEn || source.summaryRu || "same performer");
  const composition = sanitizeVisiblePromptText(source.cameraEn || source.cameraRu || "medium close shot");
  const atmosphere = sanitizeVisiblePromptText(source.locationEn || source.locationRu || source.environmentMotion || "club background with moving lights");
  const startAction = isLipSync
    ? "mouth just opening on first sung syllable, face fully readable"
    : "movement starting, body still compact";
  const endAction = isLipSync
    ? "final sung syllable, rotation peak reached, face remains readable"
    : "movement peak reached, transformed pose clearly visible";
  return {
    startFramePrompt: sanitizeVisiblePromptText(`${identity}, ${startAction}, ${composition}, ${atmosphere}`),
    endFramePrompt: sanitizeVisiblePromptText(`${identity}, ${endAction}, ${composition}, ${atmosphere}, intensified particles and light`),
  };
}

function buildTransitionPromptPatch(scene = {}, index = 0) {
  const source = scene && typeof scene === "object" ? scene : {};
  const transitionType = normalizeText(source.transitionType).toLowerCase();
  const sceneType = normalizeText(source.sceneType).toLowerCase();
  const imageStrategy = deriveScenarioImageStrategy(source);
  const isTransition = imageStrategy === "first_last"
    || transitionType.includes("state_shift")
    || transitionType.includes("transition")
    || sceneType.includes("transition")
    || sceneType.includes("state_shift");
  if (!isTransition) return null;

  const startPrompt = normalizeText(source.startFramePromptRu || source.startFramePromptEn || source.startFramePrompt);
  const endPrompt = normalizeText(source.endFramePromptRu || source.endFramePromptEn || source.endFramePrompt);
  const overlap = tokenOverlapRatio(startPrompt, endPrompt);
  const shouldStrengthen = !startPrompt || !endPrompt || overlap >= 0.82;
  if (!shouldStrengthen) return null;

  const sceneGoal = normalizeText(source.sceneGoalRu || source.sceneGoalEn || source.sceneGoal || source.summaryRu || source.summaryEn);
  const imagePrompt = normalizeText(source.imagePromptRu || source.imagePromptEn || source.imagePrompt || source.frameDescription);
  const videoPrompt = normalizeText(source.videoPromptRu || source.videoPromptEn || source.videoPrompt || source.transitionActionPrompt);
  const startBase = startPrompt || imagePrompt || sceneGoal || videoPrompt;
  const endBase = endPrompt || sceneGoal || videoPrompt || imagePrompt;
  if (!startBase && !endBase) return null;

  const distinct = ensureDistinctStartEndPrompts({
    ...source,
    startFramePromptRu: startBase,
    endFramePromptRu: endBase,
  });

  return {
    startFramePromptRu: distinct.startFramePrompt,
    endFramePromptRu: distinct.endFramePrompt,
    continuationFromPrevious: index > 0 ? true : Boolean(source.continuationFromPrevious ?? source.continuation_from_previous ?? source.continuation),
  };
}

function buildScenarioSceneContractWarnings(scene = {}) {
  const mustAppear = normalizeStringList(scene.mustAppear);
  const actors = normalizeStringList(scene.actors);
  const primaryRole = normalizeText(scene.primaryRole);
  const secondaryRoles = normalizeStringList(scene.secondaryRoles);
  const sceneActiveRoles = normalizeStringList(scene.sceneActiveRoles);
  const refsUsed = normalizeStringList(scene.refsUsed);
  const summaryText = [scene.summaryRu, scene.summaryEn].map((item) => normalizeText(item)).filter(Boolean).join(" ");
  const sceneGoalText = [scene.sceneGoalRu, scene.sceneGoalEn].map((item) => normalizeText(item)).filter(Boolean).join(" ");
  const imagePrompt = [scene.imagePromptRu, scene.imagePromptEn].map((item) => normalizeText(item)).filter(Boolean).join(" ");
  const videoPrompt = [scene.videoPromptRu, scene.videoPromptEn].map((item) => normalizeText(item)).filter(Boolean).join(" ");
  const lyricSliceText = normalizeText(scene.localPhrase || scene.lyricText);
  const warnings = [];

  const twoCharacterRequired = mustAppear.includes("character_1") && mustAppear.includes("character_2");
  const oneActorSignals = ["один", "solo", "alone", "одиноч", "single character", "single actor"];
  const twoActorSignals = ["вместе", "оба", "both", "together", "друг на", "reaction", "dialog", "диалог"];
  const sceneNarrative = [summaryText, sceneGoalText].filter(Boolean).join(" ");
  if (twoCharacterRequired) {
    const hasTwoActorLanguage = hasAnyKeyword(sceneNarrative, twoActorSignals);
    const hasOneActorLanguage = hasAnyKeyword(sceneNarrative, oneActorSignals);
    if (!hasTwoActorLanguage || hasOneActorLanguage) {
      warnings.push({ code: "two_character_summary_one_actor", level: "warning", label: "2-char contract vs 1-char summary" });
    }
  }

  if (tokenOverlapRatio(imagePrompt, videoPrompt) >= 0.82) {
    warnings.push({ code: "video_prompt_too_similar_to_image", level: "warning", label: "videoPrompt ~ imagePrompt" });
  }

  const intimateKeywords = ["whisper", "шеп", "тихо", "на ухо", "intimate", "нежно", "close confession"];
  const hasIntimateSpeech = hasAnyKeyword([lyricSliceText, summaryText, sceneGoalText].join(" "), intimateKeywords);
  if (hasIntimateSpeech && !(scene.lipSync || scene.requiresAudioSensitiveVideo)) {
    warnings.push({
      code: "intimate_phrase_without_lipsync_or_audio_sensitive",
      level: "warning",
      label: "intimate phrase without lipSync",
    });
  }

  const roleUniverse = new Set([primaryRole, ...secondaryRoles, ...sceneActiveRoles, ...refsUsed, ...actors].filter(Boolean));
  const mismatchedRoles = mustAppear.filter((role) => !roleUniverse.has(role));
  if (mismatchedRoles.length) {
    warnings.push({ code: "must_appear_mismatch", level: "warning", label: `mustAppear mismatch: ${mismatchedRoles.join(", ")}` });
  }
  return warnings;
}

function preferRuFrom(source = {}, fallback = "") {
  return normalizeText(
    source?.ru
    ?? source?.summary_ru
    ?? source?.story_summary_ru
    ?? source?.text_ru
    ?? source?.summary
    ?? fallback
  );
}

function normalizeDualField({ ru = "", en = "" } = {}) {
  const safeEn = normalizeText(en);
  const safeRu = normalizeText(ru) || safeEn;
  return { ru: safeRu, en: safeEn || safeRu };
}

function resolveFormatAlias(...candidates) {
  for (const candidate of candidates) {
    const normalized = normalizeText(candidate);
    if (!normalized) continue;
    if (normalized === "9:16" || normalized === "16:9" || normalized === "1:1") return normalized;
  }
  return "";
}

function normalizeScenarioWorkflowKeyCandidate(candidate) {
  const normalized = normalizeText(candidate).toLowerCase();
  if (!normalized) return "";
  const canonical = SCENARIO_LEGACY_WORKFLOW_ALIASES[normalized] || normalized;
  return SCENARIO_LTX_WORKFLOW_MAP[canonical] ? canonical : "";
}

function normalizeDurationFromScene(source = {}, fallback = 0) {
  const explicitDuration = toNumber(source.durationSec ?? source.duration, Number(fallback) || 0);
  const t0 = toNumber(source.t0 ?? source.time_start ?? source.timeStart, 0);
  const t1 = toNumber(source.t1 ?? source.time_end ?? source.timeEnd, t0 + explicitDuration);
  const fromRange = Math.max(0, t1 - t0);
  if (Number.isFinite(explicitDuration) && explicitDuration > 0) return Number(explicitDuration.toFixed(3));
  return Number(fromRange.toFixed(3));
}

export function detectScenarioAssetType({ url = "", mime = "", extension = "", preferFrame = false } = {}) {
  const normalizedUrl = normalizeText(url).toLowerCase();
  const normalizedMime = normalizeText(mime).toLowerCase();
  const normalizedExt = normalizeText(extension).toLowerCase().replace(/^\./, "");

  const videoExt = ["mp4", "webm", "mov", "m4v", "avi", "mkv"];
  const imageExt = ["png", "jpg", "jpeg", "webp", "bmp", "gif", "heic", "heif"];

  if (normalizedMime.startsWith("video/")) return "video";
  if (normalizedMime.startsWith("image/")) return preferFrame ? "frame" : "image";
  if (videoExt.some((ext) => normalizedUrl.endsWith(`.${ext}`) || normalizedExt === ext)) return "video";
  if (imageExt.some((ext) => normalizedUrl.endsWith(`.${ext}`) || normalizedExt === ext)) {
    return preferFrame ? "frame" : "image";
  }
  return "unknown";
}

export function deriveScenarioImageStrategy(scene = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const ltxModeRaw = normalizeText(source.ltxMode ?? source.ltx_mode).toLowerCase();
  const ltxMode = normalizeScenarioWorkflowKeyCandidate(ltxModeRaw) || ltxModeRaw;
  const requiresTwoFrames = Boolean(source.needsTwoFrames ?? source.needs_two_frames) || ["f_l"].includes(ltxMode);
  const requiresContinuation = Boolean(source.continuation ?? source.continuationFromPrevious ?? source.continuation_from_previous) || ltxMode === "continuation";
  if (requiresTwoFrames) return "first_last";
  if (requiresContinuation) return "continuation";
  return "single";
}

export function resolveScenarioExplicitWorkflowKey(scene = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const modelAssignments = source?.modelAssignments && typeof source.modelAssignments === "object" ? source.modelAssignments : {};
  const providerHints = source?.providerHints && typeof source.providerHints === "object" ? source.providerHints : {};
  const candidates = [
    source.resolvedWorkflowKey,
    source.videoWorkflowKey,
    source.workflowKey,
    source.workflow_key,
    source.videoWorkflow,
    source.workflow,
    modelAssignments.videoWorkflowKey,
    modelAssignments.workflowKey,
    modelAssignments.workflow,
    providerHints.videoWorkflowKey,
    providerHints.workflowKey,
    providerHints.workflow,
  ];
  for (const candidate of candidates) {
    const normalized = normalizeText(candidate).toLowerCase();
    if (!normalized) continue;
    const workflowKey = normalizeScenarioWorkflowKeyCandidate(normalized);
    if (workflowKey) return workflowKey;
    if (SCENARIO_LEGACY_WORKFLOW_FILENAME_TO_KEY[normalized]) return SCENARIO_LEGACY_WORKFLOW_FILENAME_TO_KEY[normalized];
  }
  return "";
}

export function resolveScenarioExplicitModelKey(scene = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const modelAssignments = source?.modelAssignments && typeof source.modelAssignments === "object" ? source.modelAssignments : {};
  const providerHints = source?.providerHints && typeof source.providerHints === "object" ? source.providerHints : {};
  const candidates = [
    source.resolvedModelKey,
    source.modelFileOverride,
    source.model_file_override,
    source.modelKey,
    source.model_key,
    source.videoModelKey,
    source.videoModel,
    source.model,
    modelAssignments.videoModelKey,
    modelAssignments.modelKey,
    modelAssignments.model,
    providerHints.videoModelKey,
    providerHints.modelKey,
    providerHints.model,
  ];
  for (const candidate of candidates) {
    const normalized = normalizeText(candidate).toLowerCase();
    if (!normalized) continue;
    if (SCENARIO_MODEL_KEY_ALIASES[normalized]) return SCENARIO_MODEL_KEY_ALIASES[normalized];
    const workflowKey = normalizeScenarioWorkflowKeyCandidate(normalized);
    if (workflowKey) return SCENARIO_WORKFLOW_DEFAULT_MODEL_KEY[workflowKey] || "";
    if (SCENARIO_LEGACY_WORKFLOW_FILENAME_TO_KEY[normalized]) {
      const workflowKey = SCENARIO_LEGACY_WORKFLOW_FILENAME_TO_KEY[normalized];
      return SCENARIO_WORKFLOW_DEFAULT_MODEL_KEY[workflowKey] || "";
    }
    return normalized;
  }
  return "";
}

export function resolveScenarioWorkflowKey(scene = {}) {
  const explicitWorkflow = resolveScenarioExplicitWorkflowKey(scene);
  if (explicitWorkflow) return explicitWorkflow;
  const source = scene && typeof scene === "object" ? scene : {};
  const continuationRequested = Boolean(
    source.continuation
    ?? source.continuationFromPrevious
    ?? source.continuation_from_previous
    ?? source.requiresContinuation
  );
  if (continuationRequested) return SCENARIO_LTX_WORKFLOW_MAP.continuation;
  const ltxMode = normalizeText(source.ltxMode ?? source.ltx_mode).toLowerCase();
  return normalizeScenarioWorkflowKeyCandidate(ltxMode) || SCENARIO_LTX_WORKFLOW_MAP.i2v;
}

function resolveScenarioRenderProvider(source = {}, scenarioPackage = null) {
  const providerHints = source?.providerHints && typeof source.providerHints === "object" ? source.providerHints : {};
  const packageHints = scenarioPackage?.providerHints && typeof scenarioPackage.providerHints === "object" ? scenarioPackage.providerHints : {};
  const providerCandidates = [
    source.sceneRenderProvider,
    source.provider,
    providerHints.provider,
    providerHints.videoProvider,
    packageHints.provider,
    packageHints.videoProvider,
  ];
  const hasScenarioLtxContract = Boolean(
    normalizeText(source.ltxMode ?? source.ltx_mode)
    || normalizeText(source.resolvedWorkflowKey)
    || source.continuation
    || source.continuationFromPrevious
    || source.continuation_from_previous
    || source.needsTwoFrames
    || source.needs_two_frames
    || source.requiresTwoFrames
    || source.requiresContinuation
    || ["lip_sync_music", "lip_sync"].includes(normalizeText(source.resolvedWorkflowKey || resolveScenarioExplicitWorkflowKey(source) || resolveScenarioWorkflowKey(source)).toLowerCase())
  );
  for (const candidate of providerCandidates) {
    const normalized = normalizeText(candidate).toLowerCase();
    if (hasScenarioLtxContract && normalized === "kie") continue;
    if (normalized) return normalized;
  }
  if (hasScenarioLtxContract) return "comfy_remote";
  return "";
}

function resolveScenarioGlobalMusicPrompt(storyboardOut = {}, directorOutput = {}) {
  const flatPrompt = normalizeText(
    storyboardOut?.globalMusicPrompt
    ?? storyboardOut?.music_prompt
    ?? storyboardOut?.bgMusicPrompt
    ?? directorOutput?.globalMusicPrompt
    ?? directorOutput?.bgMusicPrompt
    ?? directorOutput?.music?.globalMusicPrompt
    ?? directorOutput?.music_prompt
  );
  const structuredMusic = (
    (directorOutput?.music && typeof directorOutput.music === "object" ? directorOutput.music : null)
    || (storyboardOut?.music && typeof storyboardOut.music === "object" ? storyboardOut.music : null)
    || null
  );
  const shouldSkipStructuredFallback = Boolean(structuredMusic?.__isDerivedFallback);
  const synthesizedPrompt = (flatPrompt || shouldSkipStructuredFallback) ? "" : buildGlobalMusicPromptFromStructuredMusic(structuredMusic);
  if (CLIP_TRACE_SCENARIO_GLOBAL_MUSIC) {
    console.debug("[SCENARIO GLOBAL MUSIC SYNTH]", {
      hasFlatPrompt: !!flatPrompt,
      hasStructuredMusic: !!structuredMusic,
      mood: normalizeText(structuredMusic?.mood ?? structuredMusic?.musicMood ?? structuredMusic?.music_mood),
      style: normalizeText(structuredMusic?.style ?? structuredMusic?.musicStyle ?? structuredMusic?.music_style),
      hasPacingHints: !!normalizeText(structuredMusic?.pacingHints ?? structuredMusic?.pacing_hints ?? structuredMusic?.pacing),
      synthesizedPromptLength: synthesizedPrompt.length,
    });
  }
  return flatPrompt || synthesizedPrompt;
}

function resolveScenarioMusicPromptSource(storyboardOut = {}, directorOutput = {}) {
  const storyboardMusicPrompt = normalizeText(storyboardOut?.music_prompt);
  const storyboardGlobalMusicPrompt = normalizeText(storyboardOut?.globalMusicPrompt);
  const directorMusicPrompt = normalizeText(directorOutput?.music_prompt);
  const directorGlobalMusicPrompt = normalizeText(directorOutput?.globalMusicPrompt ?? directorOutput?.music?.globalMusicPrompt);
  const musicPromptRu = normalizeText(
    storyboardOut?.musicPromptRu
    ?? storyboardOut?.music_prompt_ru
    ?? directorOutput?.musicPromptRu
    ?? directorOutput?.music_prompt_ru
  );
  const musicPromptEn = normalizeText(
    storyboardOut?.musicPromptEn
    ?? storyboardOut?.music_prompt_en
    ?? directorOutput?.musicPromptEn
    ?? directorOutput?.music_prompt_en
  );

  const realMusicPrompt = (
    storyboardMusicPrompt
    || storyboardGlobalMusicPrompt
    || directorMusicPrompt
    || directorGlobalMusicPrompt
    || musicPromptRu
    || musicPromptEn
  );
  const fallbackMusicPrompt = resolveScenarioGlobalMusicPrompt(storyboardOut, directorOutput);
  if (realMusicPrompt) {
    return {
      kind: "real",
      text: realMusicPrompt,
      fallbackText: fallbackMusicPrompt && fallbackMusicPrompt !== realMusicPrompt ? fallbackMusicPrompt : "",
    };
  }
  if (fallbackMusicPrompt) {
    return {
      kind: "fallback",
      text: fallbackMusicPrompt,
      fallbackText: fallbackMusicPrompt,
    };
  }
  return { kind: "empty", text: "", fallbackText: "" };
}

const SCENARIO_CAST_ROLE_KEYS = ["character_1", "character_2", "character_3", "animal", "group"];
const SCENARIO_TWO_PERSON_INTERACTION_MARKERS = [
  "gaze",
  "shared glance",
  "look into eyes",
  "eye contact",
  "whisper",
  "hold hand",
  "holding hands",
  "embrace",
  "hug",
  "looks at",
  "looking at",
  "див",
  "смотр",
  "взгляд",
  "шепч",
  "обнима",
  "держ",
];

function normalizeScenarioRoleId(value = "") {
  const raw = normalizeText(value).toLowerCase();
  if (!raw) return "";
  if (SCENARIO_CAST_ROLE_KEYS.includes(raw)) return raw;
  if (raw === "character a" || raw === "charactera" || raw === "a") return "character_1";
  if (raw === "character b" || raw === "characterb" || raw === "b") return "character_2";
  const charMatch = raw.match(/^character[\s_-]*(\d+)$/);
  if (charMatch) {
    const idx = Number(charMatch[1]);
    if (Number.isFinite(idx) && idx >= 1 && idx <= 3) return `character_${idx}`;
  }
  return "";
}

function hasScenarioTwoPersonSemanticSignal(scene = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const blob = [
    source.summaryEn,
    source.summaryRu,
    source.imagePromptEn,
    source.imagePromptRu,
    source.videoPromptEn,
    source.videoPromptRu,
    source.sceneAction,
    source.focalSubject,
    source.sceneGoal,
    source.scene_goal,
    source.action,
  ].map((v) => normalizeText(v).toLowerCase()).filter(Boolean).join(" ");
  if (!blob) return false;
  return SCENARIO_TWO_PERSON_INTERACTION_MARKERS.some((marker) => blob.includes(marker));
}

function resolveScenarioSceneRoleContract(scene = {}, scenarioPackage = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const sceneRoleDynamics = normalizeText(source.sceneRoleDynamics ?? source.scene_role_dynamics).toLowerCase();
  const packageRefsByRole = normalizeObjectMap(scenarioPackage?.refsByRole);
  const packageMustAppearRoles = normalizeStringList(scenarioPackage?.mustAppearRoles).map((r) => normalizeScenarioRoleId(r) || normalizeText(r));
  const packageRefDirectives = normalizeObjectMap(scenarioPackage?.refDirectives);

  const refsByRole = normalizeObjectMap(source.refsByRole);
  const refsUsedByRoleInput = normalizeObjectMap(source.refsUsedByRole ?? source.refs_used_by_role);
  const refsUsedByRoleKeys = Object.keys(refsUsedByRoleInput).map((role) => normalizeScenarioRoleId(role) || normalizeText(role)).filter(Boolean);
  const participantsRaw = Array.isArray(source.actors) ? source.actors : [];
  const participantRoles = participantsRaw.map((actor) => normalizeScenarioRoleId(actor)).filter(Boolean);
  const primaryRole = normalizeScenarioRoleId(source.primaryRole) || normalizeScenarioRoleId(source.primary_role) || "";
  const secondaryRolesInput = normalizeStringList(source.secondaryRoles ?? source.secondary_roles).map((role) => normalizeScenarioRoleId(role) || role);
  const sceneActiveInput = normalizeStringList(source.sceneActiveRoles ?? source.scene_active_roles).map((role) => normalizeScenarioRoleId(role) || role);
  const refsUsedInput = normalizeStringList(source.refsUsed ?? source.refs_used).map((role) => normalizeScenarioRoleId(role) || role);
  const mustAppearInput = normalizeStringList(source.mustAppear ?? source.must_appear).map((role) => normalizeScenarioRoleId(role) || role);
  const supportInput = normalizeStringList(source.supportEntityIds ?? source.support_entity_ids).map((role) => normalizeScenarioRoleId(role) || role);
  const explicitCrowdNarrativeSignal = [
    source.sceneGoal,
    source.scene_goal,
    source.summary,
    source.summaryRu,
    source.summaryEn,
    source.frameDescription,
    source.actionInFrame,
    source.imagePromptRu,
    source.imagePromptEn,
    source.videoPromptRu,
    source.videoPromptEn,
    source.sceneAction,
    source.action,
  ].map((v) => normalizeText(v).toLowerCase()).filter(Boolean).join(" ");
  const crowdImportantKeywords = ["protest", "riot", "mob", "audience", "chorus", "crowd chant", "mass panic", "митинг", "толпа", "бунт", "хор", "массов"];
  const groupNarrativelyRequired = crowdImportantKeywords.some((keyword) => explicitCrowdNarrativeSignal.includes(keyword))
    || normalizeStringList(source.mustAppear ?? source.must_appear).map((role) => normalizeScenarioRoleId(role)).includes("group")
    || ["required", "hero"].includes(normalizeText(source?.refDirectives?.group).toLowerCase())
    || normalizeText(packageRefDirectives?.group).toLowerCase() === "required"
    || normalizeText(packageRefDirectives?.group).toLowerCase() === "hero";

  const availableCastRoles = SCENARIO_CAST_ROLE_KEYS.filter((role) => {
    const hasSceneRefs = Array.isArray(refsByRole?.[role]) && refsByRole[role].length > 0;
    const hasPackageRefs = Array.isArray(packageRefsByRole?.[role]) && packageRefsByRole[role].length > 0;
    return hasSceneRefs || hasPackageRefs;
  });
  const rolesRequiredByDirective = SCENARIO_CAST_ROLE_KEYS.filter((role) => {
    const directive = normalizeText(packageRefDirectives?.[role]).toLowerCase();
    return directive && directive !== "omit";
  });
  const castMustAppearRoles = packageMustAppearRoles.filter((role) => SCENARIO_CAST_ROLE_KEYS.includes(role));

  const explicitRoles = Array.from(new Set([
    primaryRole,
    ...secondaryRolesInput,
    ...sceneActiveInput,
    ...refsUsedInput,
    ...mustAppearInput,
    ...supportInput,
    ...refsUsedByRoleKeys,
    ...participantRoles,
  ].map((role) => normalizeScenarioRoleId(role) || normalizeText(role)).filter((role) => SCENARIO_CAST_ROLE_KEYS.includes(role))));
  const explicitRolesWithoutDefaultGroup = explicitRoles.filter((role) => (role !== "group" || groupNarrativelyRequired));
  const hasActiveHumanRoles = explicitRolesWithoutDefaultGroup.some((role) => ["character_1", "character_2", "character_3", "group"].includes(role));
  const isEnvironmentOnlyScene = sceneRoleDynamics === "environment" && participantRoles.length === 0 && !hasActiveHumanRoles;

  const hasSemanticTwoPerson = hasScenarioTwoPersonSemanticSignal(source);
  const hasTwoParticipants = participantRoles.length >= 2 || participantsRaw.length >= 2;
  const twoPersonFromGlobalContract = availableCastRoles.includes("character_1") && availableCastRoles.includes("character_2")
    && (hasSemanticTwoPerson || hasTwoParticipants || castMustAppearRoles.includes("character_2") || rolesRequiredByDirective.includes("character_2"));
  const forceTwoPerson = twoPersonFromGlobalContract && (explicitRoles.includes("character_1") || explicitRoles.length <= 1);

  const activeRoles = Array.from(new Set([
    ...explicitRolesWithoutDefaultGroup,
    ...(forceTwoPerson ? ["character_1", "character_2"] : []),
  ])).filter((role) => SCENARIO_CAST_ROLE_KEYS.includes(role));
  const filteredActiveRoles = isEnvironmentOnlyScene ? [] : activeRoles.filter((role) => (role !== "group" || groupNarrativelyRequired));

  const resolvedPrimary = isEnvironmentOnlyScene ? "" : (primaryRole || filteredActiveRoles[0] || availableCastRoles[0] || "");
  const resolvedSecondary = Array.from(new Set(filteredActiveRoles.filter((role) => role !== resolvedPrimary)));
  const resolvedActive = Array.from(new Set([resolvedPrimary, ...resolvedSecondary].filter(Boolean)));
  const resolvedMustAppear = Array.from(new Set([
    ...mustAppearInput.filter((role) => resolvedActive.includes(role)),
    ...(forceTwoPerson ? ["character_1", "character_2"] : []),
  ])).filter((role) => resolvedActive.includes(role));
  const finalMustAppear = resolvedMustAppear.length ? resolvedMustAppear : [...resolvedActive];
  const finalRefsUsed = Array.from(new Set([
    ...refsUsedInput.filter((role) => resolvedActive.includes(role)),
    ...resolvedActive,
  ]));
  const refsUsedByRole = resolvedActive.reduce((acc, role) => {
    const sourceRefs = Array.isArray(refsByRole?.[role]) ? refsByRole[role] : (Array.isArray(packageRefsByRole?.[role]) ? packageRefsByRole[role] : []);
    if (sourceRefs.length > 0) acc[role] = sourceRefs;
    return acc;
  }, {});
  let supportEntityIds = Array.from(new Set([
    ...supportInput.filter((role) => resolvedSecondary.includes(role)),
    ...resolvedSecondary,
  ]));
  if (!groupNarrativelyRequired) {
    supportEntityIds = supportEntityIds.filter((role) => role !== "group");
  }
  if (!groupNarrativelyRequired) {
    delete refsUsedByRole.group;
  }

  return {
    primaryRole: resolvedPrimary,
    secondaryRoles: resolvedSecondary,
    sceneActiveRoles: resolvedActive,
    refsUsed: !groupNarrativelyRequired ? finalRefsUsed.filter((role) => role !== "group") : finalRefsUsed,
    mustAppear: !groupNarrativelyRequired ? finalMustAppear.filter((role) => role !== "group") : finalMustAppear,
    mustNotAppear: isEnvironmentOnlyScene ? ["character_1", "character_2", "character_3", "group"] : (!groupNarrativelyRequired ? ["group"] : []),
    supportEntityIds,
    refsUsedByRole,
    debug: {
      participants: participantsRaw,
      hasSemanticTwoPerson,
      hasTwoParticipants,
      forceTwoPerson,
      availableCastRoles,
      groupNarrativelyRequired,
      isEnvironmentOnlyScene,
    },
  };
}

export function normalizeScenarioScene(scene = {}, index = 0, scenarioPackage = null) {
  const source = scene && typeof scene === "object" ? scene : {};
  const clipDecisionReason = normalizeText(source.clipDecisionReason ?? source.clip_decision_reason);
  const roleInfluenceReasonFromClip = ((clipDecisionReason.match(/roleInfluenceReason=([^;\.]+)/) || [null, ""])[1] || "").trim();
  const sceneRoleDynamicsFromClip = ((clipDecisionReason.match(/sceneRoleDynamics=([^;\.]+)/) || [null, ""])[1] || "").trim();
  const appearanceDriftRiskFromClip = ((clipDecisionReason.match(/appearanceDriftRisk=([^;\.]+)/) || [null, ""])[1] || "").trim();
  const t0 = toNumber(source.t0 ?? source.time_start ?? source.timeStart, index * 5);
  const durationRaw = toNumber(source.durationSec ?? source.duration, 5);
  const t1 = Math.max(t0, toNumber(source.t1 ?? source.time_end ?? source.timeEnd, t0 + durationRaw));
  const durationSec = Math.max(0, Number((t1 - t0).toFixed(3)));
  const explicitWorkflowKey = resolveScenarioExplicitWorkflowKey(source);
  const continuationRequested = Boolean(source.continuation ?? source.continuationFromPrevious ?? source.continuation_from_previous);
  const ltxModeFromSource = normalizeText(source.ltxMode ?? source.ltx_mode);
  const ltxMode = normalizeScenarioWorkflowKeyCandidate(ltxModeFromSource) || (ltxModeFromSource.toLowerCase() || (continuationRequested ? "continuation" : "i2v"));
  const ltxModeNormalized = ltxMode.toLowerCase();
  const renderMode = normalizeText(source.renderMode)
    || (["f_l"].includes(ltxMode) ? "first_last" : "image_to_video");
  const requiresTwoFrames = Boolean(source.needsTwoFrames ?? source.needs_two_frames) || ["f_l"].includes(ltxModeNormalized);
  const requiresContinuationRaw = continuationRequested || ltxModeNormalized === "continuation" || explicitWorkflowKey === "continuation";
  const requiresContinuation = false;
  if (requiresContinuationRaw) {
    console.warn("[SCENARIO UNSUPPORTED VIDEO MODE]", {
      sceneId: normalizeText(source.sceneId ?? source.scene_id ?? `S${index + 1}`),
      originalLtxMode: ltxModeNormalized,
      originalRenderMode: normalizeText(source.renderMode ?? source.render_mode),
      fallbackApplied: true,
      fallbackWorkflowKey: "i2v",
      reason: "continuation_execution_not_supported_in_backend",
    });
  }
  const imageStrategy = deriveScenarioImageStrategy(source);
  const resolvedWorkflowKeyRaw = explicitWorkflowKey || resolveScenarioWorkflowKey(source);
  const resolvedWorkflowKey = (requiresContinuationRaw && !requiresTwoFrames) ? "i2v" : resolvedWorkflowKeyRaw;
  const requiresAudioSensitiveVideo = ["lip_sync_music", "lip_sync"].includes(resolvedWorkflowKey) || Boolean(source.lipSync ?? source.lip_sync);
  const resolvedModelKey = resolveScenarioExplicitModelKey(source) || SCENARIO_WORKFLOW_DEFAULT_MODEL_KEY[resolvedWorkflowKey] || "";
  const sceneRenderProvider = resolveScenarioRenderProvider(source, scenarioPackage);
  const requestedDurationSec = normalizeDurationFromScene(source, durationSec);

  const summaryDual = normalizeDualField({
    ru: source.summaryRu ?? source.summary_ru ?? source.sceneGoalRu ?? source.scene_goal_ru ?? source.sceneGoal ?? source.scene_goal ?? source.action,
    en: source.summaryEn ?? source.summary_en ?? source.sceneGoalEn ?? source.scene_goal_en ?? source.scene_goal ?? source.sceneGoal ?? source.action,
  });
  const imageDual = normalizeDualField({
    ru: source.imagePromptRu ?? source.image_prompt_ru ?? source.imagePrompt ?? source.image_prompt,
    en: source.imagePromptEn ?? source.image_prompt_en ?? source.imagePrompt ?? source.image_prompt,
  });
  const videoDual = normalizeDualField({
    ru: source.videoPromptRu ?? source.video_prompt_ru ?? source.videoPrompt ?? source.video_prompt,
    en: source.videoPromptEn ?? source.video_prompt_en ?? source.videoPrompt ?? source.video_prompt,
  });
  const cameraDual = normalizeDualField({
    ru: source.cameraRu ?? source.camera_ru ?? source.cameraIdea ?? source.camera,
    en: source.cameraEn ?? source.camera_en ?? source.cameraIdea ?? source.camera,
  });
  const emotionDual = normalizeDualField({
    ru: source.emotionRu ?? source.emotion_ru ?? source.emotion,
    en: source.emotionEn ?? source.emotion_en ?? source.emotion,
  });
  const locationDual = normalizeDualField({
    ru: source.locationRu ?? source.location_ru ?? source.worldRu ?? source.world_ru ?? source.location,
    en: source.locationEn ?? source.location_en ?? source.worldEn ?? source.world_en ?? source.location,
  });
  const sceneGoalDual = normalizeDualField({
    ru: source.sceneGoalRu ?? source.scene_goal_ru ?? source.sceneGoal ?? source.scene_goal ?? source.shotPurposeRu,
    en: source.sceneGoalEn ?? source.scene_goal_en ?? source.sceneGoal ?? source.scene_goal ?? source.shotPurposeEn,
  });

  const forbiddenInsertionsRaw = source.forbiddenInsertions ?? source.forbidden_insertions;
  const forbiddenChangesRaw = source.forbiddenChanges ?? source.forbidden_changes;
  const forbiddenInsertions = normalizeStringList(forbiddenInsertionsRaw);
  const forbiddenChanges = normalizeStringList(forbiddenChangesRaw);
  const inheritedPackageFormat = resolveFormatAlias(
    scenarioPackage?.format,
    scenarioPackage?.aspectRatio,
    scenarioPackage?.aspect_ratio,
    scenarioPackage?.canvas
  );
  const sceneFormat = resolveFormatAlias(
    source?.format,
    source?.imageFormat,
    source?.image_format,
    source?.aspectRatio,
    source?.aspect_ratio,
    source?.canvas,
    inheritedPackageFormat
  );
  const normalizedScene = {
    sceneId: normalizeText(source.sceneId ?? source.scene_id) || `S${index + 1}`,
    t0,
    t1,
    durationSec,
    summaryRu: summaryDual.ru,
    summaryEn: summaryDual.en,
    sceneGoalRu: sceneGoalDual.ru,
    sceneGoalEn: sceneGoalDual.en,
    imagePromptRu: imageDual.ru,
    imagePromptEn: imageDual.en,
    videoPromptRu: videoDual.ru,
    videoPromptEn: videoDual.en,
    cameraRu: cameraDual.ru,
    cameraEn: cameraDual.en,
    emotionRu: emotionDual.ru,
    emotionEn: emotionDual.en,
    actors: Array.isArray(source.actors ?? source.participants) ? (source.actors ?? source.participants).filter(Boolean) : [],
    locationRu: locationDual.ru,
    locationEn: locationDual.en,
    renderMode,
    ltxMode: (requiresContinuationRaw && !requiresTwoFrames) ? "i2v" : ltxMode,
    ltxReason: normalizeText(source.ltxReason ?? source.ltx_reason ?? source.whyThisMode),
    needsTwoFrames: Boolean(source.needsTwoFrames ?? source.needs_two_frames ?? ["first_last"].includes(renderMode)),
    continuationFromPrevious: false,
    continuationSourceSceneId: normalizeText(source.continuationSourceSceneId ?? source.continuation_source_scene_id),
    continuationSourceAssetUrl: normalizeText(source.continuationSourceAssetUrl ?? source.continuation_source_asset_url),
    continuationSourceAssetType: normalizeText(source.continuationSourceAssetType ?? source.continuation_source_asset_type),
    imageStrategy: normalizeText(source.imageStrategy) || imageStrategy,
    requiresTwoFrames,
    requiresContinuation,
    requiresAudioSensitiveVideo,
    resolvedWorkflowKey,
    resolvedModelKey: resolvedModelKey || normalizeText(source.resolvedModelKey),
    sceneRenderProvider: sceneRenderProvider || normalizeText(source.sceneRenderProvider),
    workflowFileOverride: normalizeText(source.workflowFileOverride ?? source.workflow_file_override),
    modelFileOverride: normalizeText(source.modelFileOverride ?? source.model_file_override),
    startImageUrl: normalizeText(source.startImageUrl ?? source.start_image_url ?? source.startFrameImageUrl ?? source.start_frame_image_url),
    endImageUrl: normalizeText(source.endImageUrl ?? source.end_image_url ?? source.endFrameImageUrl ?? source.end_frame_image_url),
    audioSliceUrl: normalizeText(source.audioSliceUrl ?? source.audio_slice_url),
    requestedDurationSec,
    narrationMode: normalizeText(source.narrationMode ?? source.narration_mode) || "full",
    localPhrase: normalizeText(source.localPhrase ?? source.local_phrase),
    scenePhraseTexts: normalizeStringList(source.scenePhraseTexts ?? source.scene_phrase_texts ?? source.matchedPhraseTexts ?? source.matched_phrase_texts),
    scenePhraseCount: toNumber(source.scenePhraseCount ?? source.scene_phrase_count, 0),
    scenePhraseSource: normalizeText(source.scenePhraseSource ?? source.scene_phrase_source),
    lyricText: normalizeText(source.lyricText ?? source.lyric_text ?? source.lyricFragment ?? source.lyric_fragment),
    sfx: normalizeText(source.sfx),
    musicMixHint: normalizeText(source.musicMixHint ?? source.music_mix_hint) || "medium",
    speakerRole: normalizeText(source.speakerRole ?? source.speaker_role),
    audioSliceStartSec: toNumber(source.audioSliceStartSec ?? source.audio_slice_start_sec ?? source.time_start, t0),
    audioSliceEndSec: toNumber(source.audioSliceEndSec ?? source.audio_slice_end_sec ?? source.time_end, t1),
    audioSliceExpectedDurationSec: toNumber(source.audioSliceExpectedDurationSec ?? source.audio_slice_expected_duration_sec ?? durationSec, durationSec),
    startFramePromptRu: normalizeText(source.startFramePromptRu ?? source.start_frame_prompt_ru ?? source.startFramePrompt),
    startFramePromptEn: normalizeText(source.startFramePromptEn ?? source.start_frame_prompt_en ?? source.startFramePrompt),
    endFramePromptRu: normalizeText(source.endFramePromptRu ?? source.end_frame_prompt_ru ?? source.endFramePrompt),
    endFramePromptEn: normalizeText(source.endFramePromptEn ?? source.end_frame_prompt_en ?? source.endFramePrompt),
    imageUrl: normalizeText(source.imageUrl ?? source.image_url ?? source.previewUrl ?? source.preview_url),
    imageStatus: normalizeText(source.imageStatus ?? source.image_status),
    format: sceneFormat,
    imageFormat: sceneFormat,
    startFrameImageUrl: normalizeText(source.startFrameImageUrl ?? source.start_frame_image_url ?? source.startFramePreviewUrl ?? source.start_frame_preview_url),
    startFrameStatus: normalizeText(source.startFrameStatus ?? source.start_frame_status),
    endFrameImageUrl: normalizeText(source.endFrameImageUrl ?? source.end_frame_image_url ?? source.endFramePreviewUrl ?? source.end_frame_preview_url),
    endFrameStatus: normalizeText(source.endFrameStatus ?? source.end_frame_status),
    sceneType: source.sceneType ?? source.scene_type,
    refsByRole: source.refsByRole ?? source.refs_by_role,
    connectedRefsByRole: source.connectedRefsByRole ?? source.connected_refs_by_role,
    contextRefs: source.contextRefs ?? source.context_refs,
    connectedContextSummary: source.connectedContextSummary ?? source.connected_context_summary,
    primaryRole: source.primaryRole ?? source.primary_role,
    secondaryRoles: normalizeStringList(source.secondaryRoles ?? source.secondary_roles),
    sceneActiveRoles: normalizeStringList(source.sceneActiveRoles ?? source.scene_active_roles),
    refsUsed: normalizeStringList(source.refsUsed ?? source.refs_used),
    refDirectives: source.refDirectives ?? source.ref_directives ?? {},
    refsUsedByRole: normalizeObjectMap(source.refsUsedByRole ?? source.refs_used_by_role),
    focalSubject: source.focalSubject ?? source.focal_subject,
    sceneAction: source.sceneAction ?? source.scene_action,
    cameraIntent: source.cameraIntent ?? source.camera_intent,
    environmentMotion: source.environmentMotion ?? source.environment_motion,
    forbiddenInsertions,
    forbiddenChanges,
    lipSync: source.lipSync ?? source.lip_sync,
    lipSyncText: source.lipSyncText ?? source.lip_sync_text,
    performerPresentation: normalizeText(source.performerPresentation ?? source.performer_presentation),
    vocalPresentation: normalizeText(source.vocalPresentation ?? source.vocal_presentation),
    lipSyncVoiceCompatibility: normalizeText(source.lipSyncVoiceCompatibility ?? source.lip_sync_voice_compatibility),
    lipSyncVoiceCompatibilityReason: normalizeText(source.lipSyncVoiceCompatibilityReason ?? source.lip_sync_voice_compatibility_reason),
    clipDecisionReason,
    roleInfluenceApplied: Boolean(
      source.roleInfluenceApplied
      ?? source.role_influence_applied
      ?? (clipDecisionReason.includes("roleInfluenceApplied=true"))
    ),
    roleInfluenceReason: normalizeText(source.roleInfluenceReason ?? source.role_influence_reason) || roleInfluenceReasonFromClip,
    sceneRoleDynamics: normalizeText(source.sceneRoleDynamics ?? source.scene_role_dynamics) || sceneRoleDynamicsFromClip,
    multiCharacterIdentityLock: Boolean(
      source.multiCharacterIdentityLock
      ?? source.multi_character_identity_lock
      ?? (clipDecisionReason.includes("multiCharacterIdentityLock=true"))
    ),
    distinctCharacterSeparation: Boolean(
      source.distinctCharacterSeparation
      ?? source.distinct_character_separation
      ?? (clipDecisionReason.includes("distinctCharacterSeparation=true"))
    ),
    appearanceDriftRisk: normalizeText(source.appearanceDriftRisk ?? source.appearance_drift_risk) || appearanceDriftRiskFromClip,
    transitionType: source.transitionType ?? source.transition_type,
    shotType: source.shotType ?? source.shot_type,
    continuity: source.continuity,
    worldScaleContext: source.worldScaleContext ?? source.world_scale_context,
    entityScaleAnchors: source.entityScaleAnchors ?? source.entity_scale_anchors,
    environmentLock: source.environmentLock ?? source.environment_lock,
    styleLock: source.styleLock ?? source.style_lock,
    identityLock: source.identityLock ?? source.identity_lock,
    mustAppear: normalizeStringList(source.mustAppear ?? source.must_appear),
    mustNotAppear: normalizeStringList(source.mustNotAppear ?? source.must_not_appear),
    heroEntityId: normalizeText(source.heroEntityId ?? source.hero_entity_id),
    supportEntityIds: normalizeStringList(source.supportEntityIds ?? source.support_entity_ids),
    plannerDebug: source.plannerDebug ?? source.planner_debug,
    generationHints: source.generationHints ?? source.generation_hints,
    modelAssignments: source.modelAssignments ?? source.model_assignments,
    providerHints: source.providerHints ?? source.provider_hints,
    audioDurationSec: source.audioDurationSec ?? source.audio_duration_sec,
    sceneMeta: source.sceneMeta ?? source.scene_meta,
    debug: source.debug,
    meta: source.meta,
    globalVisualLock: scenarioPackage?.globalVisualLock || null,
    globalCameraProfile: scenarioPackage?.globalCameraProfile || null,
  };
  if (shouldTraceScenarioRoleScene(normalizedScene.sceneId)) {
    console.debug("[SCENARIO ROLE TRACE] normalizedScene.before_resolveScenarioSceneRoleContract", {
      sceneId: normalizedScene.sceneId,
      primaryRole: normalizedScene.primaryRole || "",
      secondaryRoles: normalizedScene.secondaryRoles || [],
      sceneActiveRoles: normalizedScene.sceneActiveRoles || [],
      refsUsed: normalizedScene.refsUsed || [],
      mustAppear: normalizedScene.mustAppear || [],
      refsUsedByRoleKeys: Object.keys(normalizeObjectMap(normalizedScene.refsUsedByRole)),
      actors: Array.isArray(normalizedScene.actors) ? normalizedScene.actors : [],
      refsByRoleKeys: Object.keys(normalizeObjectMap(normalizedScene.refsByRole)),
    });
  }
  const resolvedRoleContract = resolveScenarioSceneRoleContract(normalizedScene, scenarioPackage || {});
  if (shouldTraceScenarioRoleScene(normalizedScene.sceneId)) {
    console.debug("[SCENARIO ROLE TRACE] resolvedRoleContract.after_resolveScenarioSceneRoleContract", {
      sceneId: normalizedScene.sceneId,
      ...resolvedRoleContract,
      refsUsedByRoleKeys: Object.keys(normalizeObjectMap(resolvedRoleContract?.refsUsedByRole)),
      hasCharacter2InSceneActiveRoles: Array.isArray(resolvedRoleContract?.sceneActiveRoles) && resolvedRoleContract.sceneActiveRoles.includes("character_2"),
    });
  }
  normalizedScene.primaryRole = resolvedRoleContract.primaryRole;
  normalizedScene.secondaryRoles = resolvedRoleContract.secondaryRoles;
  normalizedScene.sceneActiveRoles = resolvedRoleContract.sceneActiveRoles;
  normalizedScene.refsUsed = resolvedRoleContract.refsUsed;
  normalizedScene.mustAppear = resolvedRoleContract.mustAppear;
  normalizedScene.mustNotAppear = resolvedRoleContract.mustNotAppear;
  normalizedScene.supportEntityIds = resolvedRoleContract.supportEntityIds;
  normalizedScene.heroEntityId = normalizedScene.heroEntityId || resolvedRoleContract.primaryRole || "";
  normalizedScene.refsUsedByRole = normalizeObjectMap(resolvedRoleContract.refsUsedByRole);

  if (SCENARIO_STORYBOARD_TRACE) {
    console.debug("[SCENARIO TRANSFER] normalized scene", {
      sceneId: normalizedScene.sceneId,
      renderMode: normalizedScene.renderMode,
      ltxMode: normalizedScene.ltxMode,
      sceneType: normalizedScene.sceneType,
      primaryRole: normalizedScene.primaryRole,
      secondaryRoles: Array.isArray(normalizedScene.secondaryRoles) ? normalizedScene.secondaryRoles : [],
      refsUsed: Array.isArray(normalizedScene.refsUsed) ? normalizedScene.refsUsed : [],
      lipSync: Boolean(normalizedScene.lipSync),
      audioSliceStartSec: normalizedScene.audioSliceStartSec,
      audioSliceEndSec: normalizedScene.audioSliceEndSec,
      hasContinuity: !!normalizedScene.continuity,
      hasIdentityLock: normalizedScene.identityLock !== undefined && normalizedScene.identityLock !== null,
      hasStyleLock: normalizedScene.styleLock !== undefined && normalizedScene.styleLock !== null,
      hasEnvironmentLock: normalizedScene.environmentLock !== undefined && normalizedScene.environmentLock !== null,
      hasMustAppear: Array.isArray(normalizedScene.mustAppear) ? normalizedScene.mustAppear.length > 0 : !!normalizedScene.mustAppear,
      hasMustNotAppear: Array.isArray(normalizedScene.mustNotAppear) ? normalizedScene.mustNotAppear.length > 0 : !!normalizedScene.mustNotAppear,
      hasModelAssignments: !!normalizedScene.modelAssignments,
      hasProviderHints: !!normalizedScene.providerHints,
    });
  }
  console.debug("[SCENARIO SCENE CONTRACT DEBUG]", {
    sceneId: normalizedScene.sceneId,
    participants: resolvedRoleContract.debug?.participants || normalizedScene.actors || [],
    primaryRole: normalizedScene.primaryRole || "",
    secondaryRoles: normalizedScene.secondaryRoles || [],
    sceneActiveRoles: normalizedScene.sceneActiveRoles || [],
    refsUsed: normalizedScene.refsUsed || [],
    mustAppear: normalizedScene.mustAppear || [],
    refsUsedByRoleKeys: Object.keys(normalizeObjectMap(normalizedScene.refsUsedByRole)),
    forceTwoPerson: Boolean(resolvedRoleContract.debug?.forceTwoPerson),
    hasSemanticTwoPerson: Boolean(resolvedRoleContract.debug?.hasSemanticTwoPerson),
  });
  const transitionPromptPatch = buildTransitionPromptPatch(normalizedScene, index);
  if (transitionPromptPatch) {
    normalizedScene.startFramePromptRu = String(transitionPromptPatch.startFramePromptRu || normalizedScene.startFramePromptRu || "").trim();
    normalizedScene.endFramePromptRu = String(transitionPromptPatch.endFramePromptRu || normalizedScene.endFramePromptRu || "").trim();
    normalizedScene.startFramePromptEn = normalizedScene.startFramePromptEn || normalizedScene.startFramePromptRu;
    normalizedScene.endFramePromptEn = normalizedScene.endFramePromptEn || normalizedScene.endFramePromptRu;
    if (index > 0) {
      normalizedScene.continuationFromPrevious = Boolean(transitionPromptPatch.continuationFromPrevious);
    }
  }
  normalizedScene.imagePromptRu = sanitizeVisiblePromptText(
    normalizedScene.imagePromptRu || translatePlannerMetaToVisiblePrompt(normalizedScene, { mode: "image" })
  );
  normalizedScene.imagePromptEn = sanitizeVisiblePromptText(normalizedScene.imagePromptEn || normalizedScene.imagePromptRu);
  normalizedScene.videoPromptRu = sanitizeVisiblePromptText(
    normalizedScene.videoPromptRu || translatePlannerMetaToVisiblePrompt(normalizedScene, { mode: "video" })
  );
  normalizedScene.videoPromptEn = sanitizeVisiblePromptText(normalizedScene.videoPromptEn || normalizedScene.videoPromptRu);
  const distinctPrompts = ensureDistinctStartEndPrompts(normalizedScene);
  normalizedScene.startFramePromptRu = sanitizeVisiblePromptText(normalizedScene.startFramePromptRu || distinctPrompts.startFramePrompt);
  normalizedScene.startFramePromptEn = sanitizeVisiblePromptText(normalizedScene.startFramePromptEn || normalizedScene.startFramePromptRu);
  normalizedScene.endFramePromptRu = sanitizeVisiblePromptText(normalizedScene.endFramePromptRu || distinctPrompts.endFramePrompt);
  normalizedScene.endFramePromptEn = sanitizeVisiblePromptText(normalizedScene.endFramePromptEn || normalizedScene.endFramePromptRu);
  normalizedScene.contractWarnings = buildScenarioSceneContractWarnings(normalizedScene);
  normalizedScene.contractWarningCodes = normalizedScene.contractWarnings.map((item) => item.code);
  return normalizedScene;
}

export function buildPromptSanitizationExamples() {
  return {
    first_last_transform_scene: {
      image_prompt: "dancer in a silver dress, medium shot, club lights and haze, face visible",
      video_prompt: "Cinematic 9:16 vertical shot, dancer in a silver dress, spin accelerates into a rose-like fabric vortex, camera pushes in with slight orbit, fabric fills lower half of frame, club strobes and haze pulses",
      start_frame_prompt: "dancer begins turn, dress still reads as a normal dress, face readable, medium close shot, club crowd and lights behind",
      end_frame_prompt: "spin at peak, dress expanded into a giant rose vortex filling lower half of frame, altered silhouette, brighter club flashes and drifting particles",
    },
    lip_sync_music_scene: {
      image_prompt: "female vocalist at center stage, close-up framing, warm key light, mic in hand, face clean and readable",
      video_prompt: "Cinematic 9:16 vertical shot, same vocalist, sings into mic with clear mouth articulation and continuous lip movement, slight handheld push-in, hair and sequins react to air movement, backlights sweep through haze",
      start_frame_prompt: "vocalist takes breath before first phrase, lips just parting, face fully readable, close shot, audience bokeh behind",
      end_frame_prompt: "vocal phrase ends on sustained note, chin lifted, mouth closing after final syllable, face still clear, rotating backlights and denser haze",
    },
    standard_i2v_performance_scene: {
      image_prompt: "guitarist on rooftop at dusk, three-quarter body framing, neon skyline in background",
      video_prompt: "Cinematic 9:16 vertical shot, guitarist steps forward while strumming, camera tracks left then settles, jacket fabric and guitar strap move in wind, distant traffic lights shimmer through light fog",
      start_frame_prompt: "guitarist steady before step, shoulders squared to camera, dusk skyline stable in background",
      end_frame_prompt: "guitarist finishes forward step with stronger stance, camera closer, wind lifts jacket edge, skyline lights brighter",
    },
    meta_language_removed_from: ["image_prompt", "video_prompt", "start_frame_prompt", "end_frame_prompt"],
  };
}

function buildGlobalVisualLock(storyboardOut = {}, directorOutput = {}) {
  const existingLock = storyboardOut?.globalVisualLock
    ?? storyboardOut?.global_visual_lock
    ?? directorOutput?.globalVisualLock
    ?? directorOutput?.global_visual_lock;
  const baseLock = existingLock && typeof existingLock === "object" ? existingLock : {};
  const styleLock = storyboardOut?.styleLock ?? storyboardOut?.style_lock ?? directorOutput?.styleLock ?? directorOutput?.style_lock;
  const environmentLock = storyboardOut?.environmentLock ?? storyboardOut?.environment_lock ?? directorOutput?.environmentLock ?? directorOutput?.environment_lock;
  const world = storyboardOut?.world ?? storyboardOut?.world_en ?? storyboardOut?.world_ru ?? directorOutput?.world ?? directorOutput?.worldEn ?? directorOutput?.worldRu;
  const generationHints = storyboardOut?.generationHints ?? storyboardOut?.generation_hints ?? directorOutput?.generationHints ?? directorOutput?.generation_hints;
  const providerHints = storyboardOut?.providerHints ?? storyboardOut?.provider_hints ?? directorOutput?.providerHints ?? directorOutput?.provider_hints;
  const modelAssignments = storyboardOut?.modelAssignments ?? storyboardOut?.model_assignments ?? directorOutput?.modelAssignments ?? directorOutput?.model_assignments;
  const meta = storyboardOut?.meta ?? directorOutput?.meta;
  const debug = storyboardOut?.debug ?? directorOutput?.debug;
  const hasAnySource = !!existingLock || !!styleLock || !!environmentLock || !!world || !!generationHints || !!providerHints || !!modelAssignments || !!meta || !!debug;
  const nextForbiddenDrift = normalizeStringList(baseLock?.forbiddenDrift).length
    ? Array.from(new Set([...DEFAULT_GLOBAL_VISUAL_LOCK.forbiddenDrift, ...normalizeStringList(baseLock?.forbiddenDrift)]))
    : DEFAULT_GLOBAL_VISUAL_LOCK.forbiddenDrift;
  return hasAnySource
    ? {
      ...DEFAULT_GLOBAL_VISUAL_LOCK,
      ...(styleLock && typeof styleLock === "object" ? { styleLock } : {}),
      ...(environmentLock && typeof environmentLock === "object" ? { environmentLock } : {}),
      ...(world ? { world } : {}),
      ...(generationHints ? { generationHints } : {}),
      ...(providerHints ? { providerHints } : {}),
      ...(modelAssignments ? { modelAssignments } : {}),
      ...(meta ? { meta } : {}),
      ...(debug ? { debug } : {}),
      ...baseLock,
      forbiddenDrift: nextForbiddenDrift,
    }
    : {
      ...DEFAULT_GLOBAL_VISUAL_LOCK,
      forbiddenDrift: [...DEFAULT_GLOBAL_VISUAL_LOCK.forbiddenDrift],
    };
}

function buildGlobalCameraProfile(storyboardOut = {}, directorOutput = {}) {
  const existingProfile = storyboardOut?.globalCameraProfile
    ?? storyboardOut?.global_camera_profile
    ?? directorOutput?.globalCameraProfile
    ?? directorOutput?.global_camera_profile;
  const baseProfile = existingProfile && typeof existingProfile === "object" ? existingProfile : {};
  const nextForbiddenCameraDrift = normalizeStringList(baseProfile?.forbiddenCameraDrift).length
    ? Array.from(new Set([...DEFAULT_GLOBAL_CAMERA_PROFILE.forbiddenCameraDrift, ...normalizeStringList(baseProfile?.forbiddenCameraDrift)]))
    : DEFAULT_GLOBAL_CAMERA_PROFILE.forbiddenCameraDrift;
  return {
    ...DEFAULT_GLOBAL_CAMERA_PROFILE,
    ...baseProfile,
    forbiddenCameraDrift: nextForbiddenCameraDrift,
  };
}

export function normalizeScenarioStoryboardPackage({ storyboardOut = null, directorOutput = null } = {}) {
  const globalVisualLock = buildGlobalVisualLock(storyboardOut || {}, directorOutput || {});
  const globalCameraProfile = buildGlobalCameraProfile(storyboardOut || {}, directorOutput || {});
  const format = resolveFormatAlias(
    storyboardOut?.format,
    storyboardOut?.aspectRatio,
    storyboardOut?.aspect_ratio,
    storyboardOut?.canvas,
    directorOutput?.format,
    directorOutput?.aspectRatio,
    directorOutput?.aspect_ratio,
    directorOutput?.canvas
  );
  const refsByRole = mergeObjectMapsPreferNonEmpty(
    storyboardOut?.refsByRole ?? storyboardOut?.refs_by_role,
    directorOutput?.refsByRole ?? directorOutput?.refs_by_role
  );
  const connectedRefsByRole = mergeObjectMapsPreferNonEmpty(
    storyboardOut?.connectedRefsByRole ?? storyboardOut?.connected_refs_by_role,
    directorOutput?.connectedRefsByRole ?? directorOutput?.connected_refs_by_role
  );
  const roleTypeByRole = mergeObjectMapsPreferNonEmpty(
    storyboardOut?.roleTypeByRole ?? storyboardOut?.role_type_by_role,
    directorOutput?.roleTypeByRole ?? directorOutput?.role_type_by_role
  );
  const connectedContextSummary = mergeStructuredPreferNonEmpty(
    storyboardOut?.connectedContextSummary ?? storyboardOut?.connected_context_summary,
    directorOutput?.connectedContextSummary ?? directorOutput?.connected_context_summary,
    ""
  );
  const heroParticipants = mergeStringListsPreferNonEmpty(
    storyboardOut?.heroParticipants ?? storyboardOut?.hero_participants,
    directorOutput?.heroParticipants ?? directorOutput?.hero_participants
  );
  const supportingParticipants = mergeStringListsPreferNonEmpty(
    storyboardOut?.supportingParticipants ?? storyboardOut?.supporting_participants,
    directorOutput?.supportingParticipants ?? directorOutput?.supporting_participants
  );
  const mustAppearRoles = mergeStringListsPreferNonEmpty(
    storyboardOut?.mustAppearRoles ?? storyboardOut?.must_appear_roles,
    directorOutput?.mustAppearRoles ?? directorOutput?.must_appear_roles
  );
  const contextRefs = mergeStructuredPreferNonEmpty(
    storyboardOut?.contextRefs ?? storyboardOut?.context_refs,
    directorOutput?.contextRefs ?? directorOutput?.context_refs,
    {}
  );
  const refDirectives = mergeObjectMapsPreferNonEmpty(
    storyboardOut?.refDirectives ?? storyboardOut?.ref_directives,
    directorOutput?.refDirectives ?? directorOutput?.ref_directives
  );

  const scenesRaw = Array.isArray(storyboardOut?.scenes)
    ? storyboardOut.scenes
    : Array.isArray(directorOutput?.scenes)
      ? directorOutput.scenes
      : [];
  const scenes = scenesRaw.map((scene, idx) => {
    const tracedSceneId = normalizeText(scene?.sceneId ?? scene?.scene_id) || `S${idx + 1}`;
    if (shouldTraceScenarioRoleScene(tracedSceneId)) {
      const plannerSceneRaw = Array.isArray(storyboardOut?.scenes) ? (storyboardOut.scenes[idx] || null) : null;
      const directorSceneRaw = Array.isArray(directorOutput?.scenes) ? (directorOutput.scenes[idx] || null) : null;
      console.debug("[SCENARIO ROLE TRACE] raw planner/director scene", {
        sceneId: tracedSceneId,
        plannerSceneRaw,
        directorSceneRaw,
      });
    }
    return normalizeScenarioScene(scene, idx, {
      globalVisualLock,
      globalCameraProfile,
      format,
      refsByRole,
      connectedRefsByRole,
      roleTypeByRole,
      connectedContextSummary,
      heroParticipants,
      supportingParticipants,
      mustAppearRoles,
      contextRefs,
      refDirectives,
    });
  });

  const storySummary = normalizeDualField({
    ru: storyboardOut?.story_summary_ru ?? directorOutput?.history?.summaryRu ?? preferRuFrom(directorOutput?.history, storyboardOut?.story_summary),
    en: storyboardOut?.story_summary_en ?? storyboardOut?.story_summary ?? directorOutput?.history?.summaryEn ?? directorOutput?.history?.summary,
  });
  const world = normalizeDualField({
    ru: storyboardOut?.world_ru ?? directorOutput?.history?.worldRu ?? scenes.find((scene) => !!scene.locationRu)?.locationRu,
    en: storyboardOut?.world_en ?? directorOutput?.history?.worldEn ?? scenes.find((scene) => !!scene.locationEn)?.locationEn,
  });
  const previewPrompt = normalizeDualField({
    ru: storyboardOut?.preview_prompt_ru ?? directorOutput?.history?.previewPromptRu ?? storySummary.ru,
    en: storyboardOut?.preview_prompt_en ?? directorOutput?.history?.previewPromptEn ?? storySummary.en,
  });
  const actors = Array.from(new Set(scenes.flatMap((scene) => (Array.isArray(scene.actors) ? scene.actors : [])).filter(Boolean)));
  const locations = Array.from(new Set(scenes.map((scene) => normalizeText(scene.locationEn || scene.locationRu)).filter(Boolean)));
  const globalMusicPrompt = resolveScenarioGlobalMusicPrompt(storyboardOut || {}, directorOutput || {});
  const musicPromptSource = resolveScenarioMusicPromptSource(storyboardOut || {}, directorOutput || {});
  const musicPromptRu = normalizeText(
    storyboardOut?.musicPromptRu
    ?? storyboardOut?.music_prompt_ru
    ?? directorOutput?.musicPromptRu
    ?? directorOutput?.music_prompt_ru
  );
  const musicPromptEn = normalizeText(
    storyboardOut?.musicPromptEn
    ?? storyboardOut?.music_prompt_en
    ?? directorOutput?.musicPromptEn
    ?? directorOutput?.music_prompt_en
  );

  const normalizedPackage = {
    scenes,
    format,
    storySummaryRu: storySummary.ru,
    storySummaryEn: storySummary.en,
    worldRu: world.ru,
    worldEn: world.en,
    previewPromptRu: previewPrompt.ru,
    previewPromptEn: previewPrompt.en,
    actors,
    locations,
    refsByRole,
    connectedRefsByRole,
    roleTypeByRole,
    connected_context_summary: connectedContextSummary,
    connectedContextSummary,
    heroParticipants,
    supportingParticipants,
    mustAppearRoles,
    context_refs: contextRefs,
    contextRefs,
    refDirectives,
    audioUrl: normalizeText(
      storyboardOut?.audioUrl
      ?? storyboardOut?.audio_url
      ?? directorOutput?.audioUrl
      ?? directorOutput?.audio_url
    ),
    audioDurationSec: toNumber(
      storyboardOut?.audioDurationSec
      ?? storyboardOut?.audio_duration_sec
      ?? directorOutput?.audioDurationSec
      ?? directorOutput?.audio_duration_sec,
      0
    ),
    globalMusicPrompt,
    musicPromptSourceKind: musicPromptSource.kind,
    musicPromptSourceText: musicPromptSource.text,
    fallbackMusicPrompt: musicPromptSource.fallbackText,
    musicPromptRu,
    musicPromptEn,
    bgMusicPrompt: normalizeText(storyboardOut?.bgMusicPrompt ?? directorOutput?.bgMusicPrompt) || globalMusicPrompt,
    musicStatus: normalizeText(storyboardOut?.musicStatus ?? storyboardOut?.music_status ?? directorOutput?.musicStatus ?? directorOutput?.music_status),
    musicUrl: normalizeText(storyboardOut?.musicUrl ?? storyboardOut?.music_url ?? directorOutput?.musicUrl ?? directorOutput?.music_url),
    plannerDebug: storyboardOut?.plannerDebug ?? storyboardOut?.planner_debug ?? directorOutput?.plannerDebug ?? directorOutput?.planner_debug,
    generationHints: storyboardOut?.generationHints ?? storyboardOut?.generation_hints ?? directorOutput?.generationHints ?? directorOutput?.generation_hints,
    globalVisualLock,
    globalCameraProfile,
    modelAssignments: storyboardOut?.modelAssignments ?? storyboardOut?.model_assignments ?? directorOutput?.modelAssignments ?? directorOutput?.model_assignments,
    providerHints: storyboardOut?.providerHints ?? storyboardOut?.provider_hints ?? directorOutput?.providerHints ?? directorOutput?.provider_hints,
    debug: storyboardOut?.debug ?? directorOutput?.debug,
    meta: storyboardOut?.meta ?? directorOutput?.meta,
  };
  if (CLIP_TRACE_SCENARIO_FORMAT) {
    console.debug("[SCENARIO FORMAT] normalized package", {
      format: normalizedPackage.format || "",
      scenesCount: Array.isArray(normalizedPackage.scenes) ? normalizedPackage.scenes.length : 0,
    });
  }
  if (CLIP_TRACE_SCENARIO_GLOBAL_MUSIC) {
    console.debug("[SCENARIO GLOBAL MUSIC]", {
      hasDirectorGlobalMusicPrompt: !!normalizeText(
        directorOutput?.globalMusicPrompt
        ?? directorOutput?.music?.globalMusicPrompt
        ?? directorOutput?.music_prompt
        ?? directorOutput?.bgMusicPrompt
      ),
      hasStoryboardOutMusicPrompt: !!normalizeText(
        storyboardOut?.globalMusicPrompt
        ?? storyboardOut?.music_prompt
        ?? storyboardOut?.bgMusicPrompt
      ),
      normalizedGlobalMusicPromptLength: globalMusicPrompt.length,
    });
  }
  console.debug("[SCENARIO STORYBOARD PACKAGE]", {
    status: "package normalized successfully",
    packageMergeStrategy: "safe_nested_map_merge",
    packageRefsByRoleKeys: Object.keys(normalizedPackage.refsByRole || {}),
    packageConnectedRefsByRoleKeys: Object.keys(normalizedPackage.connectedRefsByRole || {}),
    packageRefDirectivesKeys: Object.keys(normalizedPackage.refDirectives || {}),
    packageHeroParticipants: normalizedPackage.heroParticipants || [],
    packageSupportingParticipants: normalizedPackage.supportingParticipants || [],
    packageMustAppearRoles: normalizedPackage.mustAppearRoles || [],
    packageContextRefsType: Array.isArray(normalizedPackage.context_refs)
      ? "array"
      : isNonEmptyObject(normalizedPackage.context_refs)
        ? "object"
        : isNonEmptyString(normalizedPackage.context_refs)
          ? "string"
          : "empty",
    packageConnectedContextSummaryType: Array.isArray(normalizedPackage.connected_context_summary)
      ? "array"
      : isNonEmptyObject(normalizedPackage.connected_context_summary)
        ? "object"
        : isNonEmptyString(normalizedPackage.connected_context_summary)
          ? "string"
          : "empty",
    sceneRoleSnapshot: (normalizedPackage.scenes || []).map((scene, idx) => ({
      scene: idx + 1,
      primaryRole: normalizeText(scene?.primaryRole),
      secondaryRoles: Array.isArray(scene?.secondaryRoles) ? scene.secondaryRoles : [],
      sceneActiveRoles: Array.isArray(scene?.sceneActiveRoles) ? scene.sceneActiveRoles : [],
      refsUsed: Array.isArray(scene?.refsUsed) ? scene.refsUsed : [],
      mustAppear: Array.isArray(scene?.mustAppear) ? scene.mustAppear : [],
      refsUsedByRoleKeys: Object.keys(normalizeObjectMap(scene?.refsUsedByRole)),
    })),
  });
  return normalizedPackage;
}

export function buildScenarioHumanVisualAnchors(scene = {}) {
  const source = scene && typeof scene === "object" ? scene : {};
  const actorList = Array.isArray(source.actors) ? source.actors.map((item) => normalizeText(item)).filter(Boolean) : [];
  const summary = normalizeText(source.summaryEn || source.summaryRu || source.summary || source.sceneGoal || source.sceneType);
  const promptText = normalizeText(source.videoPromptEn || source.videoPromptRu || source.videoPrompt);
  const positionHints = [];
  const promptBlob = `${summary} ${promptText}`.toLowerCase();
  if (promptBlob.includes("left")) positionHints.push("left side of frame");
  if (promptBlob.includes("right")) positionHints.push("right side of frame");

  const roleCandidates = Array.from(new Set([
    ...normalizeStringList(source.sceneActiveRoles ?? source.scene_active_roles),
    ...normalizeStringList(source.refsUsed ?? source.refs_used),
    ...normalizeStringList(source.mustAppear ?? source.must_appear),
    ...normalizeStringList(source.secondaryRoles ?? source.secondary_roles),
    normalizeText(source.primaryRole ?? source.primary_role),
    ...Object.keys(normalizeObjectMap(source.refsByRole ?? source.refs_by_role)),
  ].filter(Boolean)));
  const canonicalRoles = ["character_1", "character_2", "character_3"].filter((role) => roleCandidates.includes(role));
  const primaryIds = canonicalRoles.length
    ? canonicalRoles
    : ["person_1", "person_2"].slice(0, Math.max(1, Math.min(2, actorList.length || 1)));

  const anchors = [];
  primaryIds.forEach((identityId, index) => {
    const refTarget = identityId.startsWith("character_")
      ? `same subject as ref node ${identityId}`
      : `same subject as source frame ${identityId}`;
    const positionHint = positionHints[index] ? `, ${positionHints[index]}` : "";
    anchors.push(`${identityId}: ${refTarget}${positionHint}, preserve exact face, hair, clothing`);
  });
  if (primaryIds.length >= 2) {
    anchors.push(`keep ${primaryIds[0]} and ${primaryIds[1]} in the same left/right arrangement from source frame`);
  }
  return anchors;
}

export function buildScenarioPreviewInput({ storyboardOut = null, directorOutput = null, format = "9:16", styleProfile = "" } = {}) {
  const pkg = normalizeScenarioStoryboardPackage({ storyboardOut, directorOutput });
  const resolvedFormat = resolveFormatAlias(format, pkg?.format) || "9:16";
  console.debug("[SCENARIO STORYBOARD PREVIEW INPUT]", {
    usesNormalizedPackageRefs: true,
    refsByRoleKeys: Object.keys(pkg?.refsByRole || {}),
  });
  return {
    storySummaryRu: pkg.storySummaryRu,
    storySummaryEn: pkg.storySummaryEn,
    worldRu: pkg.worldRu,
    worldEn: pkg.worldEn,
    previewPromptRu: pkg.previewPromptRu,
    previewPromptEn: pkg.previewPromptEn,
    styleProfile: normalizeText(styleProfile),
    actors: pkg.actors,
    locations: pkg.locations,
    refsByRole: pkg?.refsByRole && typeof pkg.refsByRole === "object" ? pkg.refsByRole : {},
    format: resolvedFormat,
  };
}
