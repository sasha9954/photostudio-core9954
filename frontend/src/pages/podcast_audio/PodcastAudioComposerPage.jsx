import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { getAccountScopedStorageKey } from "../clip_nodes/manualProjectBackup.js";
import { API_BASE } from "../../services/api.js";
import {
  MANUAL_TIMING_PODCAST_DIALOGUE_MODE,
  MANUAL_TIMING_PODCAST_DIALOGUE_PROJECT_KIND,
  MANUAL_TIMING_STORY_PROJECT_KIND,
  MANUAL_TIMING_STORY_VOICEOVER_MODE,
  MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
  normalizeManualTimingAudio,
  persistManualTimingProject,
  readManualTimingProjectForNode,
} from "../clip_nodes/manual_timing/manualTimingDomain.js";
import "./PodcastAudioComposerPage.css";

const BUILD_ID = "blocks-v46-separated-playback";
const COMPOSER_STORAGE_VERSION = 44;
const RESTORABLE_STORAGE_VERSIONS = new Set([30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44]);
const ACTOR_AUDIO_DB_NAME = "podcast_audio_composer_assets_v1";
const ACTOR_AUDIO_DB_STORE = "audio_files";
const DEFAULT_MICRO_STEP_SEC = 0.5;
const MIN_BLOCK_SEC = 0.001;
const MAX_HISTORY_ITEMS = 50;
const ASSET_UPLOAD_SOFT_LIMIT_BYTES = 60 * 1024 * 1024;
const PODCAST_AUDIO_HANDOFF_SOURCE = "podcast_audio_composer";
const BLOCK_COLORS = [
  "var(--podcast-block-color-1)",
  "var(--podcast-block-color-2)",
  "var(--podcast-block-color-3)",
  "var(--podcast-block-color-4)",
  "var(--podcast-block-color-5)",
  "var(--podcast-block-color-6)",
];


const BLOCK_ACTION_MENU_OFFSET = 12;
const BLOCK_ACTION_MENU_PADDING = 16;
const BLOCK_ACTION_MENU_FALLBACK_WIDTH = 460;
const BLOCK_ACTION_MENU_FALLBACK_HEIGHT = 560;

function getSafeBlockMenuPosition(clickX = 220, clickY = 220, menuSize = {}) {
  if (typeof window === "undefined") {
    return {
      x: clickX,
      y: clickY,
      menuWidth: BLOCK_ACTION_MENU_FALLBACK_WIDTH,
      menuHeight: BLOCK_ACTION_MENU_FALLBACK_HEIGHT,
      flippedX: false,
      shiftedY: false,
    };
  }

  const padding = BLOCK_ACTION_MENU_PADDING;
  const viewportWidth = window.innerWidth || 0;
  const viewportHeight = window.innerHeight || 0;
  const maxMenuWidth = Math.max(0, viewportWidth - (padding * 2));
  const maxMenuHeight = Math.max(0, viewportHeight - (padding * 2));
  const menuWidth = Math.min(
    maxMenuWidth || BLOCK_ACTION_MENU_FALLBACK_WIDTH,
    Number.isFinite(menuSize.width) && menuSize.width > 0 ? menuSize.width : BLOCK_ACTION_MENU_FALLBACK_WIDTH
  );
  const menuHeight = Math.min(
    maxMenuHeight || BLOCK_ACTION_MENU_FALLBACK_HEIGHT,
    Number.isFinite(menuSize.height) && menuSize.height > 0 ? menuSize.height : BLOCK_ACTION_MENU_FALLBACK_HEIGHT
  );

  let left = clickX + BLOCK_ACTION_MENU_OFFSET;
  let top = clickY + BLOCK_ACTION_MENU_OFFSET;
  let flippedX = false;
  let shiftedY = false;

  if (left + menuWidth > viewportWidth - padding) {
    left = clickX - menuWidth - BLOCK_ACTION_MENU_OFFSET;
    flippedX = true;
  }

  if (top + menuHeight > viewportHeight - padding) {
    top = Math.max(padding, viewportHeight - menuHeight - padding);
    shiftedY = true;
  }

  left = Math.max(padding, Math.min(left, viewportWidth - menuWidth - padding));
  top = Math.max(padding, Math.min(top, viewportHeight - menuHeight - padding));

  return {
    x: Math.round(left),
    y: Math.round(top),
    menuWidth: Math.round(menuWidth),
    menuHeight: Math.round(menuHeight),
    flippedX,
    shiftedY,
  };
}

const COLOR_SWATCHES = [
  "#60a5fa",
  "#f59e0b",
  "#22c55e",
  "#ec4899",
  "#a855f7",
  "#ef4444",
  "#14b8a6",
  "#eab308",
  "#94a3b8",
  "#ffffff",
];

const GUIDE_JSON_SAMPLE = {
  schema: "podcast_composer_guide_v1",
  purpose: "Монтажная карта для Podcast Audio Composer. Это НЕ ASR-разбор и НЕ поиск реальных голосов в текущем аудио. Используется только внутри композера как визуальная подсказка: где оставить диктора и где позже вставить актёрские фразы. Тишину в JSON не размечать — пользователь расставляет паузы вручную кнопкой 'тишина'. В Manual Timing экспортируется готовое собранное аудио вместе с podcast_edit_manifest, чтобы роли, тишина, вставки и source-map были видны в JSON после ASR.",
  rules: {
    do_not_treat_as_asr: true,
    do_not_search_actor_voices_in_main_audio: true,
    main_audio_is_narrator_base: true,
    actor_blocks_are_placeholders: true,
    actor_blocks_mean_future_insertion_slots: true,
    silence_is_manual_only: true,
    do_not_generate_silence_blocks_from_json: true,
    timing_is_approximate: true,
    user_can_fix_boundaries_with_nudge: true,
    export_to_timing: "audio_plus_podcast_edit_manifest",
    labels_on_blocks: "show 3-4 letters from role label",
    auto_fill_uncovered_tail_as_narrator: true,
    uncovered_tail_role: "narrator"
  },
  roles: [
    {
      id: "narrator",
      label: "ДИК",
      name: "Диктор",
      color: "#64748b",
      type: "main_voice",
      meaning: "Оставить исходный дикторский звук из основного аудио."
    },
    {
      id: "ded",
      label: "ДЕД",
      name: "Дед",
      color: "#d99a18",
      type: "actor_placeholder",
      meaning: "Это место позже заменить или заполнить фразой деда из дополнительного аудио."
    },
    {
      id: "tetka",
      label: "ТЁТ",
      name: "Тётка",
      color: "#ef4444",
      type: "actor_placeholder",
      meaning: "Это место позже заменить или заполнить фразой тётки из дополнительного аудио."
    },
    {
      id: "witness",
      label: "СВИД",
      name: "Свидетель",
      color: "#60a5fa",
      type: "actor_placeholder",
      meaning: "Это место позже заменить или заполнить фразой свидетеля."
    }
  ],
  blocks: [
    {
      role: "narrator",
      t0: 0,
      t1: 8.5,
      action: "keep_main_audio",
      note: "Диктор подводит к первой фразе деда."
    },
    {
      role: "ded",
      t0: 8.5,
      t1: 9.0,
      action: "replace_with_actor_phrase_later",
      phrase_hint: "Здесь в общем аудио звучит маркер 'говорит дед'. Позже заменить фразой деда.",
      note: "Актёрский placeholder. В текущем основном аудио голоса деда может не быть."
    },
    {
      role: "narrator",
      t0: 9.0,
      t1: 25.0,
      action: "keep_main_audio",
      note: "Диктор продолжает историю и подводит к тётке."
    },
    {
      role: "tetka",
      t0: 25.0,
      t1: 25.5,
      action: "replace_with_actor_phrase_later",
      phrase_hint: "Здесь в общем аудио звучит маркер 'говорит тётка'. Позже заменить фразой тётки.",
      note: "Актёрский placeholder для будущей вставки."
    },
    {
      role: "narrator",
      t0: 25.5,
      t1: 45.0,
      action: "keep_main_audio",
      note: "Диктор продолжает рассказ. Если аудио длиннее, Composer сам оставит хвост как ДИК."
    }
  ]
};

function getGuideJsonSampleText() {
  return JSON.stringify(GUIDE_JSON_SAMPLE, null, 2);
}

function normalizeGuideRoleKey(value = "") {
  return String(value || "")
    .trim()
    .toLowerCase()
    .replace(/[^0-9a-zа-яёіїєґ_-]+/gi, "_");
}

function buildGuideRoleMap(roles = []) {
  const map = new Map();
  (Array.isArray(roles) ? roles : []).forEach((role, index) => {
    const id = normalizeGuideRoleKey(role?.id || role?.role || role?.name || role?.label || `role_${index + 1}`);
    if (!id) return;
    map.set(id, {
      id,
      label: String(role?.label || role?.name || role?.id || id).trim() || id,
      color: String(role?.color || "").trim() || COLOR_SWATCHES[index % COLOR_SWATCHES.length],
      color_index: index,
      type: String(role?.type || role?.kind || "").trim(),
      name: String(role?.name || role?.label || role?.id || id).trim(),
      meaning: String(role?.meaning || role?.description || "").trim(),
    });
  });
  return map;
}

function getGuideRoleInfo(roleMap, roleValue = "", fallbackIndex = 0) {
  const roleKey = normalizeGuideRoleKey(roleValue || "narrator");
  if (roleMap.has(roleKey)) return roleMap.get(roleKey);
  if (["дик", "дикт", "диктор", "narrator", "voiceover", "voice_over"].includes(roleKey)) {
    return { id: "narrator", label: "ДИК", color: "#c43b3b", color_index: 5 };
  }
  const label = String(roleValue || `ГОЛ${fallbackIndex + 1}`).trim() || `ГОЛ${fallbackIndex + 1}`;
  return {
    id: roleKey || `role_${fallbackIndex + 1}`,
    label,
    color: COLOR_SWATCHES[(fallbackIndex + 1) % COLOR_SWATCHES.length],
    color_index: (fallbackIndex + 1) % COLOR_SWATCHES.length,
  };
}

function normalizeGuideBlockRow(row = {}, index = 0) {
  const t0 = row.t0 ?? row.start ?? row.start_sec ?? row.startSec ?? row.from;
  const t1 = row.t1 ?? row.end ?? row.end_sec ?? row.endSec ?? row.to;
  return {
    role: row.role || row.speaker || row.actor || row.label || row.name || "narrator",
    t0: normalizeNumber(t0, 0),
    t1: normalizeNumber(t1, 0),
    note: String(row.note || row.text || row.line || row.phrase || "").trim(),
    action: String(row.action || row.mode || row.intent || "").trim(),
    phrase_hint: String(row.phrase_hint || row.phraseHint || row.hint || "").trim(),
    index,
  };
}

function makeGuideAudioBlock(startSec, endSec, roleInfo, fallbackIndex = 0, row = {}) {
  const start = roundSeconds(startSec);
  const end = roundSeconds(endSec);
  if (end <= start) return null;
  const label = String(roleInfo?.label || "ДИК").trim() || "ДИК";
  const roleType = String(roleInfo?.type || "").trim().toLowerCase();
  const action = String(row?.action || "").trim().toLowerCase();
  const roleId = String(roleInfo?.id || "").trim().toLowerCase();
  const isSilence = roleType === "silence" || roleId === "silence" || action === "insert_silence" || action === "silence";
  if (isSilence) {
    return {
      ...createSilenceBlock(end - start, Number.isInteger(roleInfo?.color_index) ? roleInfo.color_index : fallbackIndex),
      color: String(roleInfo?.color || "").trim() || undefined,
      block_label: label || "ТИШ",
      guide_action: action || "insert_silence",
      guide_note: String(row?.note || "").trim(),
      guide_phrase_hint: String(row?.phrase_hint || "").trim(),
    };
  }
  return {
    id: createId("block"),
    type: "audio",
    source_audio_id: "main",
    source_start_sec: start,
    source_end_sec: end,
    color_index: Number.isInteger(roleInfo?.color_index) ? roleInfo.color_index : fallbackIndex,
    color: String(roleInfo?.color || "").trim() || undefined,
    block_label: label,
    guide_action: action,
    guide_note: String(row?.note || "").trim(),
    guide_phrase_hint: String(row?.phrase_hint || "").trim(),
  };
}

function buildBlocksFromGuideJson(guideValue, durationSec = 0) {
  const data = typeof guideValue === "string" ? JSON.parse(guideValue) : guideValue;
  const totalDuration = roundSeconds(durationSec);
  if (!data || typeof data !== "object" || totalDuration <= 0) return null;

  const roleMap = buildGuideRoleMap(data.roles || data.speakers || data.actors || []);
  const rows = data.blocks || data.guideBlocks || data.segments || data.items || [];
  const normalizedRows = (Array.isArray(rows) ? rows : [])
    .map((row, index) => normalizeGuideBlockRow(row, index))
    .filter((row) => Number.isFinite(row.t0) && Number.isFinite(row.t1) && row.t1 > row.t0)
    .sort((a, b) => a.t0 - b.t0 || a.t1 - b.t1);

  if (!normalizedRows.length) return null;

  const narratorRole = getGuideRoleInfo(roleMap, "narrator", 0);
  const nextBlocks = [];
  let cursor = 0;

  normalizedRows.forEach((row, index) => {
    const start = clampSeconds(row.t0, 0, totalDuration);
    const end = clampSeconds(row.t1, 0, totalDuration);
    if (end <= cursor + 0.0005) return;

    if (start > cursor + 0.0005) {
      const gapBlock = makeGuideAudioBlock(cursor, start, narratorRole, 0);
      if (gapBlock) nextBlocks.push(gapBlock);
      cursor = start;
    }

    const effectiveStart = Math.max(cursor, start);
    const roleInfo = getGuideRoleInfo(roleMap, row.role, index + 1);
    const guideBlock = makeGuideAudioBlock(effectiveStart, end, roleInfo, index + 1, row);
    if (guideBlock) nextBlocks.push(guideBlock);
    cursor = Math.max(cursor, end);
  });

  if (cursor < totalDuration - 0.0005) {
    const tailBlock = makeGuideAudioBlock(cursor, totalDuration, narratorRole, 0);
    if (tailBlock) nextBlocks.push(tailBlock);
  }

  return nextBlocks.length ? nextBlocks : null;
}

function getBlockRenderColor(block = {}) {
  if (typeof block?.color === "string" && block.color.trim()) return block.color.trim();
  const index = Number.isInteger(block?.color_index) ? block.color_index : 0;
  return BLOCK_COLORS[((index % BLOCK_COLORS.length) + BLOCK_COLORS.length) % BLOCK_COLORS.length];
}

function getExplicitBlockLabel(block = {}) {
  if (!block || typeof block !== "object") return "";
  return String(
    block.inserted_phrase_label
    || block.saved_clip_label
    || block.phrase_label
    || block.block_label
    || block.label
    || ""
  ).trim();
}

function isAutoNarratorLabel(label = "") {
  const compact = String(label || "").trim().toLowerCase();
  return ["дик", "дикт", "диктор"].includes(compact);
}

function hasPhraseIdentity(block = {}) {
  return Boolean(
    block?.type === "phrase"
    || block?.block_kind === "phrase"
    || block?.inserted_phrase_id
    || block?.saved_clip_id
    || block?.phrase_id
    || block?.inserted_phrase_label
    || block?.saved_clip_label
    || block?.phrase_label
  );
}

function getStoredBlockLabel(block = {}) {
  const explicit = getExplicitBlockLabel(block);
  if (!explicit) return "";
  const sourceId = block?.source_audio_id || "main";
  if (sourceId === "main" && !hasPhraseIdentity(block) && isAutoNarratorLabel(explicit)) return "";
  return explicit;
}

function getBlockLabelText(block = {}) {
  if (!block || typeof block !== "object") return "";
  const explicit = getStoredBlockLabel(block);
  if (explicit) return explicit;
  if (block.type === "silence") return "Тишина";
  if (hasPhraseIdentity(block)) return "Аудио-фраза";
  if ((block.source_audio_id || "main") === "main") return "Диктор";
  return String(block.source_audio_id || "Аудио").trim() || "Аудио";
}

function compactBlockBadge(label = "") {
  return String(label || "")
    .replace(/^[^0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ]+/, "")
    .replace(/[^0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ]+/g, "")
    .trim()
    .toUpperCase()
    .slice(0, 4);
}

function getBlockInitialText(block = {}) {
  if (!block || typeof block !== "object") return "";
  if (block.type === "silence") return "ТИШ";
  const explicit = getStoredBlockLabel(block);
  if (explicit) return compactBlockBadge(explicit) || "ФРАЗ";
  if ((block.source_audio_id || "main") === "main") return "ДИК";
  return compactBlockBadge(block.source_audio_id || "АУД") || "АУД";
}

function normalizeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function roundSeconds(value, digits = 3) {
  return Number(Math.max(0, normalizeNumber(value, 0)).toFixed(digits));
}

function roundSignedSeconds(value, digits = 3) {
  const number = normalizeNumber(value, 0);
  return Number(number.toFixed(digits));
}

function clampSeconds(value, min = 0, max = Number.POSITIVE_INFINITY) {
  const safeValue = normalizeNumber(value, min);
  return roundSeconds(Math.min(max, Math.max(min, safeValue)));
}

function formatTimer(value) {
  const totalMs = Math.max(0, Math.round(normalizeNumber(value, 0) * 1000));
  const minutes = Math.floor(totalMs / 60000);
  const seconds = Math.floor((totalMs % 60000) / 1000);
  const milliseconds = totalMs % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

function createId(prefix = "id") {
  return `${prefix}_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function cloneJson(value) {
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return value;
  }
}

function getComposerStorageKey(sourceNodeId = "") {
  const safeNodeId = String(sourceNodeId || "default").trim() || "default";
  return getAccountScopedStorageKey(`podcast_audio_composer_v30:${safeNodeId}`);
}

function readComposerStorage(sourceNodeId = "") {
  try {
    const raw = localStorage.getItem(getComposerStorageKey(sourceNodeId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeComposerStorage(sourceNodeId = "", payload = {}) {
  try {
    localStorage.setItem(getComposerStorageKey(sourceNodeId), JSON.stringify(payload));
  } catch {}
}

function removeComposerStorage(sourceNodeId = "") {
  try {
    localStorage.removeItem(getComposerStorageKey(sourceNodeId));
  } catch {}
}

function isBlobUrl(value = "") {
  return String(value || "").trim().startsWith("blob:");
}


function isBackendStaticAssetUrl(url = "") {
  const raw = String(url || "").trim();
  if (!raw || !raw.includes("/static/assets/")) return false;

  let pathname = raw;
  try {
    pathname = new URL(raw, typeof window !== "undefined" ? window.location.href : "http://localhost").pathname;
  } catch {
    pathname = raw.split(/[?#]/, 1)[0];
  }

  const assetIndex = pathname.indexOf("/static/assets/");
  if (assetIndex < 0) return false;
  const assetPath = pathname.slice(assetIndex + "/static/assets/".length).trim();
  if (!assetPath || assetPath.includes("..")) return false;
  const cleanPath = assetPath.split(/[?#]/, 1)[0];
  return /\.(mp3|wav|m4a|aac|ogg|oga|webm|flac)$/i.test(cleanPath) || cleanPath.length > 0;
}

function extractBackendStaticAssetFilename(url = "", fallback = "podcast_audio.mp3") {
  const raw = String(url || "").trim();
  let pathname = raw;
  try {
    pathname = new URL(raw, typeof window !== "undefined" ? window.location.href : "http://localhost").pathname;
  } catch {
    pathname = raw.split(/[?#]/, 1)[0];
  }
  const filename = decodeURIComponent(String(pathname || "").split("/").filter(Boolean).pop() || "").trim();
  return filename || fallback;
}

function inferAudioMimeTypeFromFilename(filename = "") {
  const lower = String(filename || "").toLowerCase();
  if (lower.endsWith(".mp3")) return "audio/mpeg";
  if (lower.endsWith(".wav")) return "audio/wav";
  if (lower.endsWith(".m4a") || lower.endsWith(".aac")) return "audio/aac";
  if (lower.endsWith(".ogg") || lower.endsWith(".oga")) return "audio/ogg";
  if (lower.endsWith(".webm")) return "audio/webm";
  if (lower.endsWith(".flac")) return "audio/flac";
  return "audio/mpeg";
}

function stripTransientSourceUrl(row = {}) {
  if (!row || typeof row !== "object") return row;
  const copy = { ...row };
  if (isBlobUrl(copy.source_url)) delete copy.source_url;
  if (isBlobUrl(copy.url)) delete copy.url;
  copy.isPlaying = false;
  return copy;
}

function serializeBlocksForStorage(blocks = []) {
  return (Array.isArray(blocks) ? blocks : []).map(stripTransientSourceUrl);
}

function serializeSavedClipsForStorage(savedClips = []) {
  return (Array.isArray(savedClips) ? savedClips : []).map(stripTransientSourceUrl);
}

function serializeActorAudiosForStorage(actorAudios = []) {
  return (Array.isArray(actorAudios) ? actorAudios : []).map((actor) => {
    const cleanActor = stripTransientSourceUrl(actor);
    const serverUrl = getActorServerSourceUrl(cleanActor);
    return {
      ...cleanActor,
      url: serverUrl,
      asset_url: serverUrl || cleanActor.asset_url || cleanActor.assetUrl || "",
      server_url: serverUrl || cleanActor.server_url || "",
      publicUrl: serverUrl || cleanActor.publicUrl || "",
      blocks: serializeBlocksForStorage(actor?.blocks),
      isPlaying: false,
    };
  });
}

function getActorAudioBlobKey(sourceNodeId = "", actorId = "") {
  return `${getComposerStorageKey(sourceNodeId)}:actor:${String(actorId || "")}`;
}

function openComposerAssetDb() {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === "undefined") {
      reject(new Error("IndexedDB недоступен"));
      return;
    }
    const request = indexedDB.open(ACTOR_AUDIO_DB_NAME, 1);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains(ACTOR_AUDIO_DB_STORE)) db.createObjectStore(ACTOR_AUDIO_DB_STORE);
    };
    request.onerror = () => reject(request.error || new Error("Не удалось открыть IndexedDB"));
    request.onsuccess = () => resolve(request.result);
  });
}

async function putActorAudioBlob(key = "", blob) {
  if (!key || !blob) return;
  const db = await openComposerAssetDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(ACTOR_AUDIO_DB_STORE, "readwrite");
    tx.objectStore(ACTOR_AUDIO_DB_STORE).put(blob, key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error("Не удалось сохранить аудио актёра"));
  });
  db.close?.();
}

async function getActorAudioBlob(key = "") {
  if (!key) return null;
  const db = await openComposerAssetDb();
  const result = await new Promise((resolve, reject) => {
    const tx = db.transaction(ACTOR_AUDIO_DB_STORE, "readonly");
    const request = tx.objectStore(ACTOR_AUDIO_DB_STORE).get(key);
    request.onsuccess = () => resolve(request.result || null);
    request.onerror = () => reject(request.error || new Error("Не удалось прочитать аудио актёра"));
  });
  db.close?.();
  return result;
}

async function deleteActorAudioBlob(key = "") {
  if (!key) return;
  const db = await openComposerAssetDb();
  await new Promise((resolve, reject) => {
    const tx = db.transaction(ACTOR_AUDIO_DB_STORE, "readwrite");
    tx.objectStore(ACTOR_AUDIO_DB_STORE).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error || new Error("Не удалось удалить аудио актёра"));
  });
  db.close?.();
}

async function restoreActorAudiosFromStorage(sourceNodeId = "", actorAudios = []) {
  const rows = Array.isArray(actorAudios) ? actorAudios : [];
  const restored = [];
  for (const actor of rows) {
    const actorId = String(actor?.id || "");
    if (!actorId) continue;
    const serverUrl = getActorServerSourceUrl(actor);
    let browserUrl = serverUrl;
    try {
      const blob = await getActorAudioBlob(getActorAudioBlobKey(sourceNodeId, actorId));
      if (blob) browserUrl = URL.createObjectURL(blob);
    } catch {}
    const blocks = serializeBlocksForStorage(actor?.blocks).map((block) => ({
      ...block,
      source_audio_id: block.source_audio_id || actorId,
      source_url: browserUrl || undefined,
      asset_url: serverUrl || block.asset_url || block.assetUrl || undefined,
      server_url: serverUrl || block.server_url || undefined,
      source_name: block.source_name || actor.name || actor.filename || actorId,
      block_label: block.block_label || actor.label,
      color: block.color || actor.color,
    }));
    restored.push({
      ...actor,
      url: browserUrl,
      asset_url: serverUrl || actor.asset_url || actor.assetUrl || "",
      server_url: serverUrl || actor.server_url || "",
      publicUrl: serverUrl || actor.publicUrl || "",
      blocks,
      isPlaying: false,
      currentTimeSec: clampSeconds(actor?.currentTimeSec || 0, 0, getTimelineDuration(blocks) || actor?.duration_sec || 0),
    });
  }
  return restored;
}

function getAudioSignature(audio = {}, durationSec = 0) {
  return [String(audio?.url || ""), String(audio?.filename || ""), String(roundSeconds(durationSec || audio?.duration_sec || 0))].join("|");
}


function normalizeBrowserAudioUrl(url = "") {
  const raw = String(url || "").trim();
  if (!raw) return "";
  if (/^https?:\/\//i.test(raw) || raw.startsWith("blob:") || raw.startsWith("data:")) return raw;
  if (raw.startsWith("/")) return `${API_BASE}${raw}`;
  if (raw.startsWith("static/assets/")) return `${API_BASE}/${raw}`;
  try {
    return new URL(raw, window.location.href).href;
  } catch {
    return raw;
  }
}

function sanitizeAudioDownloadName(filename = "podcast-audio") {
  const base = String(filename || "podcast-audio")
    .replace(/\.[^.]+$/, "")
    .replace(/[^0-9A-Za-zА-Яа-яЁёІіЇїЄєҐґ_-]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 80);
  return `${base || "podcast_audio"}_composer.wav`;
}

function writeStringToDataView(view, offset, value) {
  for (let index = 0; index < value.length; index += 1) {
    view.setUint8(offset + index, value.charCodeAt(index));
  }
}

function encodeWavFromFloatChannels(channels = [], sampleRate = 44100) {
  const channelCount = Math.max(1, channels.length || 1);
  const frameCount = channels[0]?.length || 0;
  const bytesPerSample = 2;
  const blockAlign = channelCount * bytesPerSample;
  const dataSize = frameCount * blockAlign;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  writeStringToDataView(view, 0, "RIFF");
  view.setUint32(4, 36 + dataSize, true);
  writeStringToDataView(view, 8, "WAVE");
  writeStringToDataView(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, channelCount, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * blockAlign, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true);
  writeStringToDataView(view, 36, "data");
  view.setUint32(40, dataSize, true);

  let offset = 44;
  for (let frame = 0; frame < frameCount; frame += 1) {
    for (let channel = 0; channel < channelCount; channel += 1) {
      const sample = Math.max(-1, Math.min(1, channels[channel]?.[frame] || 0));
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
      offset += 2;
    }
  }
  return new Blob([buffer], { type: "audio/wav" });
}


function getAudioUrlForSourceId(sourceId = "", mainAudio = {}, actorAudios = []) {
  const safeId = String(sourceId || "main").trim() || "main";
  if (safeId === "main") return String(mainAudio?.url || "");
  const actor = (Array.isArray(actorAudios) ? actorAudios : []).find((item) => item.id === safeId);
  return String(actor?.url || "");
}

function getAudioNameForSourceId(sourceId = "", mainAudio = {}, actorAudios = []) {
  const safeId = String(sourceId || "main").trim() || "main";
  if (safeId === "main") return String(mainAudio?.filename || "Основное аудио");
  const actor = (Array.isArray(actorAudios) ? actorAudios : []).find((item) => item.id === safeId);
  return String(actor?.name || actor?.filename || safeId);
}

const SERVER_AUDIO_URL_KEYS = ["source_url", "url", "asset_url", "assetUrl", "server_url", "public_url", "publicUrl"];

function getFirstBackendStaticAssetUrl(row = {}, keys = SERVER_AUDIO_URL_KEYS) {
  if (!row || typeof row !== "object") return "";
  for (const key of keys) {
    const value = String(row?.[key] || "").trim();
    if (value && isBackendStaticAssetUrl(value)) return value;
  }
  return "";
}

function getActorServerSourceUrl(actor = {}) {
  return getFirstBackendStaticAssetUrl(actor, ["url", "asset_url", "assetUrl", "server_url", "public_url", "publicUrl", "source_url"]);
}

function getBlockSavedClipLookupValues(block = {}) {
  return [block?.saved_clip_id, block?.inserted_phrase_id, block?.phrase_id]
    .map((value) => String(value || "").trim())
    .filter(Boolean);
}

function findSavedClipForBlock(block = {}, savedClips = []) {
  const clips = Array.isArray(savedClips) ? savedClips : [];
  const ids = getBlockSavedClipLookupValues(block);
  for (const id of ids) {
    const match = clips.find((clip) => [clip?.id, clip?.saved_clip_id, clip?.inserted_phrase_id]
      .some((value) => String(value || "").trim() === id));
    if (match) return match;
  }

  const labels = [block?.saved_clip_label, block?.inserted_phrase_label, block?.phrase_label]
    .map((value) => String(value || "").trim())
    .filter(Boolean);
  for (const label of labels) {
    const match = clips.find((clip) => [clip?.label, clip?.saved_clip_label, clip?.inserted_phrase_label]
      .some((value) => String(value || "").trim() === label));
    if (match) return match;
  }
  return null;
}

function normalizePodcastSourceLabel(value = "") {
  return String(value || "")
    .toLowerCase()
    .replace(/\.[a-z0-9а-яёіїєґ]+$/i, "")
    .replace(/[\s_-]+/g, " ")
    .trim();
}

function getPodcastSourceIdentityValues(item = {}) {
  return [
    item?.label,
    item?.name,
    item?.filename,
    item?.source_name,
    item?.source_label,
    item?.block_label,
    item?.saved_clip_label,
    item?.inserted_phrase_label,
    item?.phrase_label,
  ]
    .map(normalizePodcastSourceLabel)
    .filter(Boolean);
}

function findActorAudioByLogicalLabel({ savedClip = null, block = null, actorAudios = [] } = {}) {
  const actors = Array.isArray(actorAudios) ? actorAudios : [];
  const wanted = new Set([...getPodcastSourceIdentityValues(savedClip || {}), ...getPodcastSourceIdentityValues(block || {})]);
  if (!wanted.size) return null;
  const exact = actors.find((actor) => getPodcastSourceIdentityValues(actor).some((value) => wanted.has(value)));
  if (exact) return exact;
  if (actors.length === 1) {
    const actorValues = getPodcastSourceIdentityValues(actors[0]);
    const matchesSingleActor = actorValues.some((actorValue) => [...wanted].some((wantedValue) => (
      actorValue.length >= 3 && wantedValue.length >= 3 && (actorValue.includes(wantedValue) || wantedValue.includes(actorValue))
    )));
    if (matchesSingleActor) return actors[0];
  }
  return null;
}

function getSavedClipServerSourceUrl(savedClip = {}) {
  return getFirstBackendStaticAssetUrl(savedClip, ["source_url", "asset_url", "assetUrl", "server_url", "public_url", "publicUrl", "url"]);
}

function buildPodcastSourceRegistry({ actorAudios = [], savedClips = [], blocks = [], legacySourceUrlByActorId = new Map() } = {}) {
  const actorById = new Map();
  const savedClipById = new Map();
  const sourceUrlByActorId = new Map();
  const sourceUrlBySavedClipId = new Map();
  const sourceUrlByInsertedPhraseId = new Map();
  const sourceUrlByLegacyActorId = new Map(legacySourceUrlByActorId instanceof Map ? legacySourceUrlByActorId : []);

  (Array.isArray(actorAudios) ? actorAudios : []).forEach((actor) => {
    const id = String(actor?.id || "").trim();
    if (!id) return;
    actorById.set(id, actor);
    const url = getActorServerSourceUrl(actor);
    if (url) sourceUrlByActorId.set(id, url);
  });

  (Array.isArray(savedClips) ? savedClips : []).forEach((clip) => {
    const ids = [clip?.id, clip?.saved_clip_id, clip?.inserted_phrase_id].map((value) => String(value || "").trim()).filter(Boolean);
    ids.forEach((id) => savedClipById.set(id, clip));
    const clipUrl = getSavedClipServerSourceUrl(clip);
    ids.forEach((id) => {
      if (clipUrl) {
        sourceUrlBySavedClipId.set(id, clipUrl);
        sourceUrlByInsertedPhraseId.set(id, clipUrl);
      }
    });
    const sourceId = String(clip?.source_audio_id || "").trim();
    if (sourceId && sourceId !== "main" && clipUrl && !sourceUrlByActorId.has(sourceId)) {
      sourceUrlByLegacyActorId.set(sourceId, clipUrl);
    }
  });

  (Array.isArray(blocks) ? blocks : []).forEach((block) => {
    const blockUrl = getFirstBackendStaticAssetUrl(block, ["source_url", "asset_url", "assetUrl", "server_url", "public_url", "publicUrl", "url"]);
    if (!blockUrl) return;
    getBlockSavedClipLookupValues(block).forEach((id) => sourceUrlByInsertedPhraseId.set(id, blockUrl));
    const sourceId = String(block?.source_audio_id || "").trim();
    if (sourceId && sourceId !== "main" && !sourceUrlByActorId.has(sourceId)) sourceUrlByLegacyActorId.set(sourceId, blockUrl);
  });

  return { actorById, savedClipById, sourceUrlByActorId, sourceUrlBySavedClipId, sourceUrlByInsertedPhraseId, sourceUrlByLegacyActorId };
}

function resolveServerRenderableBlockSource({ block = {}, mainAudio = {}, originalAudioUrl = "", registry = null, savedClips = [], actorById = new Map() } = {}) {
  const sourceId = String(block?.source_audio_id || "main").trim() || "main";
  if (block?.type === "silence" || sourceId === "silence") return { sourceUrl: "", resolvedVia: "silence", savedClip: null };

  const directBlockUrl = getFirstBackendStaticAssetUrl(block, ["source_url", "asset_url", "assetUrl", "server_url", "public_url", "publicUrl", "url"]);
  if (directBlockUrl) return { sourceUrl: directBlockUrl, resolvedVia: "block", savedClip: null };

  const savedClip = findSavedClipForBlock(block, savedClips);
  const savedIds = getBlockSavedClipLookupValues(block);
  for (const id of savedIds) {
    const insertedUrl = registry?.sourceUrlByInsertedPhraseId?.get(id) || registry?.sourceUrlBySavedClipId?.get(id);
    if (insertedUrl) return { sourceUrl: insertedUrl, resolvedVia: "inserted_phrase", savedClip };
  }

  if (savedClip) {
    const clipUrl = getSavedClipServerSourceUrl(savedClip);
    if (clipUrl) return { sourceUrl: clipUrl, resolvedVia: "saved_clip", savedClip };
    const clipSourceId = String(savedClip?.source_audio_id || "").trim();
    const clipActorUrl = clipSourceId ? (registry?.sourceUrlByActorId?.get(clipSourceId) || registry?.sourceUrlByLegacyActorId?.get(clipSourceId)) : "";
    if (clipActorUrl) return { sourceUrl: clipActorUrl, resolvedVia: "saved_clip_source_audio_id", savedClip };
  }

  const actorUrl = registry?.sourceUrlByActorId?.get(sourceId) || registry?.sourceUrlByLegacyActorId?.get(sourceId) || getActorServerSourceUrl(actorById.get(sourceId));
  if (sourceId !== "main" && actorUrl) return { sourceUrl: actorUrl, resolvedVia: registry?.sourceUrlByLegacyActorId?.has(sourceId) ? "legacy_actor" : "actor", savedClip };

  const fallbackOriginalUrl = String(originalAudioUrl || mainAudio?.url || "").trim();
  if (sourceId === "main" && isBackendStaticAssetUrl(fallbackOriginalUrl)) return { sourceUrl: fallbackOriginalUrl, resolvedVia: "main", savedClip };

  return { sourceUrl: "", resolvedVia: "missing", savedClip };
}

function resolveServerRenderableBlockSourceUrl({ block = {}, mainAudio = {}, originalAudioUrl = "", actorById = new Map(), savedClips = [] } = {}) {
  const sourceId = String(block?.source_audio_id || "main").trim() || "main";
  if (block?.type === "silence" || sourceId === "silence") return "";

  const explicitSourceUrl = String(block?.source_url || "").trim();
  if (isBackendStaticAssetUrl(explicitSourceUrl)) return explicitSourceUrl;

  const directAssetUrl = getFirstBackendStaticAssetUrl(block, ["asset_url", "assetUrl", "server_url", "public_url", "publicUrl", "url"]);
  if (directAssetUrl) return directAssetUrl;

  const savedClip = findSavedClipForBlock(block, savedClips);
  if (savedClip) {
    const clipUrl = getFirstBackendStaticAssetUrl(savedClip);
    if (clipUrl) return clipUrl;

    const clipSourceId = String(savedClip?.source_audio_id || "").trim();
    if (clipSourceId && clipSourceId !== "main") {
      const actorUrl = getActorServerSourceUrl(actorById.get(clipSourceId));
      if (actorUrl) return actorUrl;
    }
  }

  if (sourceId !== "main") {
    const actorUrl = getActorServerSourceUrl(actorById.get(sourceId));
    if (actorUrl) return actorUrl;
  }

  const fallbackOriginalUrl = String(originalAudioUrl || mainAudio?.url || "").trim();
  if (sourceId === "main" && isBackendStaticAssetUrl(fallbackOriginalUrl)) return fallbackOriginalUrl;

  return "";
}

function getItemSourceUrl(item = {}, mainAudio = {}, actorAudios = []) {
  if (!item || item.type === "silence" || item.source_audio_id === "silence") return "";
  const staticAssetUrl = getFirstBackendStaticAssetUrl(item, ["source_url", "asset_url", "assetUrl", "server_url", "public_url", "publicUrl", "url"]);
  if (staticAssetUrl) return staticAssetUrl;
  const sourceId = String(item.source_audio_id || "main").trim() || "main";
  if (sourceId && sourceId !== "main") {
    const actorUrl = getAudioUrlForSourceId(sourceId, mainAudio, actorAudios);
    if (actorUrl) return actorUrl;
  }
  if (typeof item.source_url === "string" && item.source_url.trim()) return item.source_url.trim();
  return getAudioUrlForSourceId(sourceId, mainAudio, actorAudios);
}

function isSilenceBlock(block = {}) {
  return Boolean(block?.type === "silence" || block?.source_kind === "silence" || block?.source_audio_id === "silence" || block?.is_silence);
}

function getInsertedAudioUrl(block = {}) {
  const keys = ["audio_url", "fragment_url", "asset_url", "assetUrl", "src", "source_url", "server_url", "public_url", "publicUrl", "url"];
  for (const key of keys) {
    const value = String(block?.[key] || "").trim();
    if (value) return value;
  }
  return "";
}

function isInsertedAudioBlock(block = {}) {
  if (!block || typeof block !== "object" || isSilenceBlock(block)) return false;
  const explicitKind = String(block?.source_kind || block?.block_type || block?.block_kind || "").trim().toLowerCase();
  if (["inserted_audio", "fragment_audio", "tts_audio", "saved_chunk", "phrase"].includes(explicitKind)) return true;
  if (block?.is_phrase || block?.type === "phrase" || hasPhraseIdentity(block)) return true;
  const sourceId = String(block?.source_audio_id || "main").trim() || "main";
  return sourceId !== "main" && Boolean(getInsertedAudioUrl(block));
}

function hasInsertedAudioUrl(block = {}) {
  return isInsertedAudioBlock(block) && Boolean(getInsertedAudioUrl(block));
}

function normalizeComposerPlaybackBlock(block = {}, index = 0, timelineStartSec = 0) {
  const timelineStart = roundSeconds(block?.timeline_start_sec ?? timelineStartSec);
  const durationSec = roundSeconds(block?.duration_sec || getBlockDuration(block));
  const timelineEnd = roundSeconds(block?.timeline_end_sec ?? (timelineStart + durationSec));
  const blockId = String(block?.id || block?.block_id || `block_${index}`);

  if (isSilenceBlock(block)) {
    return {
      type: "silence",
      blockId,
      block,
      index,
      timelineStartSec: timelineStart,
      timelineEndSec: timelineEnd,
      durationSec,
      sourceStartSec: 0,
      sourceEndSec: durationSec,
      audioUrl: "",
    };
  }

  if (isInsertedAudioBlock(block)) {
    const audioUrl = getInsertedAudioUrl(block);
    if (!audioUrl) console.warn("[PAC INSERTED_AUDIO_MISSING_URL]", block);
    return {
      type: "inserted_audio",
      blockId,
      block,
      index,
      timelineStartSec: timelineStart,
      timelineEndSec: timelineEnd,
      startSec: 0,
      endSec: durationSec,
      durationSec,
      sourceStartSec: 0,
      sourceEndSec: durationSec,
      audioUrl,
    };
  }

  const sourceStartSec = roundSeconds(block?.source_start_sec);
  const sourceEndSec = roundSeconds(block?.source_end_sec ?? (sourceStartSec + durationSec));
  return {
    type: "main_audio",
    blockId,
    block,
    index,
    timelineStartSec: timelineStart,
    timelineEndSec: timelineEnd,
    sourceStartSec,
    sourceEndSec,
    durationSec: durationSec || Math.max(0, sourceEndSec - sourceStartSec),
    audioUrl: "",
  };
}

function logComposerQueueBlock(item = {}) {
  console.info("[PAC QUEUE BLOCK]", {
    index: item.index,
    blockId: item.blockId,
    type: item.type,
    timelineStart: item.timelineStartSec,
    timelineEnd: item.timelineEndSec,
    sourceStart: item.sourceStartSec,
    sourceEnd: item.sourceEndSec,
    audioUrl: item.audioUrl,
    durationSec: item.durationSec,
  });
}

function inferActorLabelFromFilename(filename = "") {
  const base = String(filename || "").replace(/\.[^.]+$/, "").trim().toLowerCase();
  if (base.includes("дед") || base.includes("ded")) return "ДЕД";
  if (base.includes("тет") || base.includes("тёт") || base.includes("tet")) return "ТЁТ";
  if (base.includes("мам") || base.includes("mom") || base.includes("mama")) return "МАМА";
  if (base.includes("свид") || base.includes("witness")) return "СВИД";
  if (base.includes("акт") || base.includes("actor")) return "АКТ";
  return compactBlockBadge(base || "АКТ") || "АКТ";
}

function createExternalAudioBlock({ sourceId, sourceUrl, sourceName, label, color, startSec, endSec, colorIndex = 1 }) {
  return {
    id: createId("block"),
    type: "audio",
    source_audio_id: sourceId,
    source_url: sourceUrl,
    source_name: sourceName,
    source_start_sec: roundSeconds(startSec),
    source_end_sec: roundSeconds(endSec),
    color_index: colorIndex,
    color,
    block_label: label,
  };
}

function createMainBlock(startSec, endSec, colorIndex = 0) {
  return {
    id: createId("block"),
    type: "audio",
    source_audio_id: "main",
    source_start_sec: roundSeconds(startSec),
    source_end_sec: roundSeconds(endSec),
    color_index: colorIndex,
  };
}

function createSilenceBlock(durationSec = DEFAULT_MICRO_STEP_SEC, colorIndex = 4) {
  const safeDuration = clampSeconds(durationSec, 0.01, 30);
  return {
    id: createId("block"),
    type: "silence",
    source_audio_id: "silence",
    source_start_sec: 0,
    source_end_sec: safeDuration,
    color_index: colorIndex,
    block_label: "Тишина",
  };
}

function getBlockDuration(block = {}) {
  if (!block || typeof block !== "object") return 0;
  return Math.max(0, roundSeconds(block.source_end_sec) - roundSeconds(block.source_start_sec));
}

function createInitialBlocks(durationSec = 0) {
  const safeDuration = roundSeconds(durationSec);
  if (safeDuration <= 0) return [];
  return [createMainBlock(0, safeDuration, 0)];
}

function getTimelineDuration(blocks = []) {
  return roundSeconds((Array.isArray(blocks) ? blocks : []).reduce((sum, block) => sum + getBlockDuration(block), 0));
}

function normalizeBlocks(blocks = [], fallbackDurationSec = 0, savedClipsForMigration = []) {
  const normalized = (Array.isArray(blocks) ? blocks : [])
    .map((block, index) => {
      const start = roundSeconds(block?.source_start_sec);
      const end = roundSeconds(block?.source_end_sec);
      if (end <= start) return null;
      const savedClipMatch = (Array.isArray(savedClipsForMigration) ? savedClipsForMigration : [])
        .find((clip) => String(clip?.id || "") && String(clip?.id || "") === String(block?.inserted_phrase_id || block?.saved_clip_id || block?.phrase_id || ""));
      const migratedLabel = getStoredBlockLabel(block) || String(savedClipMatch?.label || "").trim();
      const phraseIdentity = hasPhraseIdentity(block) || Boolean(savedClipMatch);
      const type = block?.type === "silence" ? "silence" : (phraseIdentity ? "phrase" : (block?.type || "audio"));
      return {
        id: String(block?.id || `block_${index}`),
        type,
        block_kind: phraseIdentity ? "phrase" : undefined,
        source_audio_id: block?.source_audio_id || (type === "silence" ? "silence" : "main"),
        source_kind: type === "silence" ? "silence" : (phraseIdentity ? "inserted_audio" : (block?.source_kind || "main_audio")),
        block_type: type === "silence" ? "silence" : (phraseIdentity ? "inserted_audio" : (block?.block_type || "main_audio")),
        source_url: typeof block?.source_url === "string" && block.source_url.trim() ? block.source_url.trim() : undefined,
        audio_url: typeof block?.audio_url === "string" && block.audio_url.trim() ? block.audio_url.trim() : undefined,
        fragment_url: typeof block?.fragment_url === "string" && block.fragment_url.trim() ? block.fragment_url.trim() : undefined,
        asset_url: typeof block?.asset_url === "string" && block.asset_url.trim() ? block.asset_url.trim() : undefined,
        assetUrl: typeof block?.assetUrl === "string" && block.assetUrl.trim() ? block.assetUrl.trim() : undefined,
        server_url: typeof block?.server_url === "string" && block.server_url.trim() ? block.server_url.trim() : undefined,
        publicUrl: typeof block?.publicUrl === "string" && block.publicUrl.trim() ? block.publicUrl.trim() : undefined,
        src: typeof block?.src === "string" && block.src.trim() ? block.src.trim() : undefined,
        source_name: typeof block?.source_name === "string" && block.source_name.trim() ? block.source_name.trim() : undefined,
        duration_sec: phraseIdentity ? roundSeconds(block?.duration_sec || end - start) : undefined,
        source_start_sec: type === "silence" ? 0 : (phraseIdentity ? 0 : start),
        source_end_sec: type === "silence" ? roundSeconds(end - start) : (phraseIdentity ? roundSeconds(block?.duration_sec || end - start) : end),
        color_index: Number.isInteger(block?.color_index) ? block.color_index : index,
        color: typeof block?.color === "string" && block.color.trim() ? block.color.trim() : undefined,
        block_label: migratedLabel || undefined,
        phrase_label: phraseIdentity && migratedLabel ? migratedLabel : undefined,
        inserted_phrase_label: phraseIdentity && migratedLabel ? migratedLabel : undefined,
        saved_clip_label: phraseIdentity && migratedLabel ? migratedLabel : undefined,
        inserted_phrase_id: block?.inserted_phrase_id || block?.saved_clip_id || block?.phrase_id || savedClipMatch?.id || undefined,
        saved_clip_id: block?.saved_clip_id || block?.inserted_phrase_id || block?.phrase_id || savedClipMatch?.id || undefined,
      };
    })
    .filter(Boolean);
  return normalized.length ? normalized : createInitialBlocks(fallbackDurationSec);
}

function getBlockVirtualStart(blocks = [], targetIndex = 0) {
  let cursor = 0;
  for (let index = 0; index < targetIndex; index += 1) {
    cursor += getBlockDuration(blocks[index]);
  }
  return roundSeconds(cursor);
}

function getBlockVirtualEnd(blocks = [], targetIndex = 0) {
  return roundSeconds(getBlockVirtualStart(blocks, targetIndex) + getBlockDuration(blocks[targetIndex]));
}

function getSelectedBlockIndex(blocks = [], selectedBlockId = "") {
  return (Array.isArray(blocks) ? blocks : []).findIndex((block) => block.id === selectedBlockId);
}

function findTimelinePosition(blocks = [], virtualTimeSec = 0) {
  const safeBlocks = Array.isArray(blocks) ? blocks : [];
  const timelineDuration = getTimelineDuration(safeBlocks);
  if (!safeBlocks.length || timelineDuration <= 0) return null;

  const safeTime = clampSeconds(virtualTimeSec, 0, timelineDuration);
  let cursor = 0;
  for (let index = 0; index < safeBlocks.length; index += 1) {
    const block = safeBlocks[index];
    const duration = getBlockDuration(block);
    const start = cursor;
    const end = roundSeconds(cursor + duration);
    const isLast = index === safeBlocks.length - 1;
    if (safeTime < end || isLast) {
      const offset = clampSeconds(safeTime - start, 0, duration);
      return {
        index,
        block,
        block_start_sec: start,
        block_end_sec: end,
        offset_sec: offset,
        source_time_sec: roundSeconds(roundSeconds(block.source_start_sec) + offset),
      };
    }
    cursor = end;
  }
  return null;
}

function getBrokenPhraseRepairKey(item = {}) {
  return [item?.blockId, item?.savedClipId, item?.insertedPhraseId, item?.sourceAudioId]
    .map((value) => String(value || "").trim())
    .filter(Boolean)
    .join("::") || createId("broken_phrase");
}

function getBrokenPhraseLabel(item = {}) {
  return String(
    item?.label
    || item?.savedClipLabel
    || item?.insertedPhraseLabel
    || item?.savedClipId
    || item?.insertedPhraseId
    || item?.blockId
    || "Фраза"
  ).trim() || "Фраза";
}

function buildActorPhraseRepairOptions(actorAudios = []) {
  return (Array.isArray(actorAudios) ? actorAudios : []).flatMap((actor) => {
    const actorId = String(actor?.id || "").trim();
    if (!actorId) return [];
    return (Array.isArray(actor?.blocks) ? actor.blocks : []).map((block, index) => {
      const blockId = String(block?.id || "").trim();
      if (!blockId) return null;
      const duration = getBlockDuration(block);
      if (duration <= 0) return null;
      const actorLabel = String(actor?.label || actor?.name || actor?.filename || actorId).trim() || actorId;
      return {
        value: `${actorId}::${blockId}`,
        actorId,
        blockId,
        actor,
        block,
        label: `${actorLabel} · блок ${index + 1} · ${formatTimer(duration)}`,
        duration,
      };
    }).filter(Boolean);
  });
}

function splitBlockAtTime(blocks = [], timeSec = 0, selectedBlockId = null) {
  const position = findTimelinePosition(blocks, timeSec);
  if (!position) return null;
  const { index, block, offset_sec } = position;
  const blockDuration = getBlockDuration(block);
  if (offset_sec <= 0 || offset_sec >= blockDuration) return null;

  const splitSourceTime = roundSeconds(block.source_start_sec + offset_sec);
  const leftBlock = {
    ...block,
    id: createId("block"),
    source_end_sec: splitSourceTime,
    color_index: (block.color_index + 1) % BLOCK_COLORS.length,
  };
  const rightBlock = {
    ...block,
    id: createId("block"),
    source_start_sec: splitSourceTime,
  };
  const nextBlocks = [...blocks];
  nextBlocks.splice(index, 1, leftBlock, rightBlock);
  return {
    blocks: nextBlocks,
    selectedBlockId: leftBlock.id,
    selectedBlockStart: getBlockVirtualStart(nextBlocks, index),
  };
}

function getActiveBoundaryLeftIndex(blocks = [], selectedBlockId = "") {
  const selectedIndex = getSelectedBlockIndex(blocks, selectedBlockId);
  if (selectedIndex < 0) return -1;
  if (blocks[selectedIndex + 1]) return selectedIndex;
  if (selectedIndex > 0) return selectedIndex - 1;
  return -1;
}

function buildContiguousBlocksFromDurations(blocks = [], durations = []) {
  const nextBlocks = [];
  let cursor = 0;
  (Array.isArray(durations) ? durations : []).forEach((durationValue, index) => {
    const duration = roundSeconds(durationValue);
    if (duration <= 0.0005) return;
    const baseBlock = blocks[index] || {};
    const start = roundSeconds(cursor);
    const end = roundSeconds(cursor + duration);
    const storedLabel = getStoredBlockLabel(baseBlock);
    const isPhraseBlock = hasPhraseIdentity(baseBlock) || Boolean(storedLabel && baseBlock.type !== "silence");
    const type = baseBlock.type === "silence" ? "silence" : (isPhraseBlock ? "phrase" : (baseBlock.type || "audio"));
    const sourceStart = type === "silence"
      ? 0
      : isPhraseBlock
        ? roundSeconds(baseBlock.source_start_sec)
        : start;
    const sourceEnd = type === "silence" ? duration : roundSeconds(sourceStart + duration);
    nextBlocks.push({
      id: String(baseBlock.id || createId("block")),
      type,
      block_kind: isPhraseBlock ? "phrase" : undefined,
      source_audio_id: type === "silence" ? "silence" : (baseBlock.source_audio_id || "main"),
      source_kind: type === "silence" ? "silence" : (isPhraseBlock ? "inserted_audio" : (baseBlock.source_kind || "main_audio")),
      block_type: type === "silence" ? "silence" : (isPhraseBlock ? "inserted_audio" : (baseBlock.block_type || "main_audio")),
      source_url: typeof baseBlock.source_url === "string" && baseBlock.source_url.trim() ? baseBlock.source_url.trim() : undefined,
      audio_url: typeof baseBlock.audio_url === "string" && baseBlock.audio_url.trim() ? baseBlock.audio_url.trim() : undefined,
      fragment_url: typeof baseBlock.fragment_url === "string" && baseBlock.fragment_url.trim() ? baseBlock.fragment_url.trim() : undefined,
      asset_url: typeof baseBlock.asset_url === "string" && baseBlock.asset_url.trim() ? baseBlock.asset_url.trim() : undefined,
      assetUrl: typeof baseBlock.assetUrl === "string" && baseBlock.assetUrl.trim() ? baseBlock.assetUrl.trim() : undefined,
      server_url: typeof baseBlock.server_url === "string" && baseBlock.server_url.trim() ? baseBlock.server_url.trim() : undefined,
      publicUrl: typeof baseBlock.publicUrl === "string" && baseBlock.publicUrl.trim() ? baseBlock.publicUrl.trim() : undefined,
      src: typeof baseBlock.src === "string" && baseBlock.src.trim() ? baseBlock.src.trim() : undefined,
      source_name: typeof baseBlock.source_name === "string" && baseBlock.source_name.trim() ? baseBlock.source_name.trim() : undefined,
      duration_sec: isPhraseBlock ? duration : undefined,
      source_start_sec: sourceStart,
      source_end_sec: sourceEnd,
      color_index: Number.isInteger(baseBlock.color_index) ? baseBlock.color_index : index,
      color: typeof baseBlock.color === "string" && baseBlock.color.trim() ? baseBlock.color.trim() : undefined,
      block_label: storedLabel || undefined,
      phrase_label: isPhraseBlock && storedLabel ? storedLabel : undefined,
      inserted_phrase_label: isPhraseBlock && storedLabel ? storedLabel : undefined,
      saved_clip_label: isPhraseBlock && storedLabel ? storedLabel : undefined,
      inserted_phrase_id: baseBlock.inserted_phrase_id || baseBlock.saved_clip_id || baseBlock.phrase_id || undefined,
      saved_clip_id: baseBlock.saved_clip_id || baseBlock.inserted_phrase_id || baseBlock.phrase_id || undefined,
    });
    cursor = end;
  });
  return nextBlocks;
}

function resizeSelectedBlockEnd(blocks = [], selectedIndex = -1, deltaSec = 0, audioDurationSec = 0) {
  if (selectedIndex < 0 || selectedIndex >= blocks.length) return null;

  const safeBlocks = Array.isArray(blocks) ? blocks : [];
  const durations = safeBlocks.map((block) => getBlockDuration(block));
  const currentDuration = roundSeconds(durations[selectedIndex] || 0);
  const requestedDelta = roundSignedSeconds(Number(deltaSec || 0));
  const totalDuration = roundSeconds(audioDurationSec || getTimelineDuration(safeBlocks));

  if (safeBlocks[selectedIndex]?.type === "silence") {
    const requestedDuration = roundSeconds(currentDuration + requestedDelta);
    const nextDuration = clampSeconds(requestedDuration, 0.01, 30);
    const appliedDelta = roundSignedSeconds(nextDuration - currentDuration);
    if (Math.abs(appliedDelta) < 0.0005) {
      return {
        blocks: safeBlocks,
        selectedIndex,
        selectedEndSec: getBlockVirtualEnd(safeBlocks, selectedIndex),
        selectedDurationAfter: currentDuration,
        rightDurationAfter: durations[selectedIndex + 1] || 0,
        appliedDelta: 0,
        moved: false,
        hitStart: requestedDelta < 0,
        hitEnd: requestedDelta > 0,
      };
    }
    const nextBlocks = [...safeBlocks];
    nextBlocks[selectedIndex] = {
      ...nextBlocks[selectedIndex],
      source_start_sec: 0,
      source_end_sec: nextDuration,
    };
    return {
      blocks: nextBlocks,
      selectedIndex,
      selectedEndSec: getBlockVirtualEnd(nextBlocks, selectedIndex),
      selectedDurationAfter: nextDuration,
      rightDurationAfter: getBlockDuration(nextBlocks[selectedIndex + 1]),
      appliedDelta,
      moved: true,
      hitStart: false,
      hitEnd: false,
    };
  }

  if (currentDuration <= 0 || Math.abs(requestedDelta) < 0.0005) {
    return {
      blocks: safeBlocks,
      selectedIndex,
      selectedEndSec: getBlockVirtualEnd(safeBlocks, selectedIndex),
      selectedDurationAfter: currentDuration,
      rightDurationAfter: durations[selectedIndex + 1] || 0,
      appliedDelta: 0,
      moved: false,
      hitStart: false,
      hitEnd: false,
    };
  }

  const nextDurations = [...durations];
  const nextBlocksBase = [...safeBlocks];
  let appliedDelta = 0;

  if (requestedDelta < 0) {
    // ←: конец выбранного блока идёт влево.
    // Выбранный блок уменьшается, освободившаяся длительность сразу отдаётся правому остатку.
    const availableToGive = Math.max(0, currentDuration - MIN_BLOCK_SEC);
    const giveAmount = roundSeconds(Math.min(Math.abs(requestedDelta), availableToGive));
    if (giveAmount <= 0.0005) {
      return {
        blocks: safeBlocks,
        selectedIndex,
        selectedEndSec: getBlockVirtualEnd(safeBlocks, selectedIndex),
        selectedDurationAfter: currentDuration,
        rightDurationAfter: durations[selectedIndex + 1] || 0,
        appliedDelta: 0,
        moved: false,
        hitStart: true,
        hitEnd: false,
      };
    }

    nextDurations[selectedIndex] = roundSeconds(currentDuration - giveAmount);
    if (nextDurations[selectedIndex + 1] !== undefined) {
      nextDurations[selectedIndex + 1] = roundSeconds((nextDurations[selectedIndex + 1] || 0) + giveAmount);
    } else {
      nextDurations.splice(selectedIndex + 1, 0, giveAmount);
      nextBlocksBase.splice(selectedIndex + 1, 0, {
        id: createId("block"),
        type: "audio",
        source_audio_id: "main",
        source_start_sec: 0,
        source_end_sec: giveAmount,
        color_index: (safeBlocks[selectedIndex]?.color_index + 1) % BLOCK_COLORS.length,
      });
    }
    appliedDelta = roundSignedSeconds(-giveAmount);
  } else {
    // →: конец выбранного блока идёт вправо.
    // Выбранный блок растёт, забирая длительность у правых остатков.
    let need = requestedDelta;
    let cursorIndex = selectedIndex + 1;
    while (need > 0.0005 && cursorIndex < nextDurations.length) {
      const rightDuration = roundSeconds(nextDurations[cursorIndex] || 0);
      if (rightDuration <= 0.0005) {
        nextDurations.splice(cursorIndex, 1);
        nextBlocksBase.splice(cursorIndex, 1);
        continue;
      }
      const take = roundSeconds(Math.min(need, rightDuration));
      nextDurations[selectedIndex] = roundSeconds((nextDurations[selectedIndex] || 0) + take);
      nextDurations[cursorIndex] = roundSeconds(rightDuration - take);
      need = roundSignedSeconds(need - take);
      appliedDelta = roundSignedSeconds(appliedDelta + take);
      if (nextDurations[cursorIndex] <= 0.0005) {
        nextDurations.splice(cursorIndex, 1);
        nextBlocksBase.splice(cursorIndex, 1);
      } else {
        break;
      }
    }

    if (appliedDelta <= 0.0005) {
      return {
        blocks: safeBlocks,
        selectedIndex,
        selectedEndSec: getBlockVirtualEnd(safeBlocks, selectedIndex),
        selectedDurationAfter: currentDuration,
        rightDurationAfter: durations[selectedIndex + 1] || 0,
        appliedDelta: 0,
        moved: false,
        hitStart: false,
        hitEnd: true,
      };
    }
  }

  const nextBlocks = buildContiguousBlocksFromDurations(nextBlocksBase, nextDurations);
  const nextSelectedBlock = nextBlocks[selectedIndex] || null;
  const nextRightBlock = nextBlocks[selectedIndex + 1] || null;

  return {
    blocks: nextBlocks,
    selectedIndex,
    selectedEndSec: nextSelectedBlock ? getBlockVirtualEnd(nextBlocks, selectedIndex) : 0,
    selectedDurationAfter: nextSelectedBlock ? getBlockDuration(nextSelectedBlock) : 0,
    rightDurationAfter: nextRightBlock ? getBlockDuration(nextRightBlock) : 0,
    appliedDelta,
    moved: true,
    hitStart: false,
    hitEnd: false,
  };
}

function mergeWithPrevious(blocks = [], blockId = "") {
  const index = blocks.findIndex((block) => block.id === blockId);
  if (index <= 0) return null;
  const prev = blocks[index - 1];
  const current = blocks[index];
  if (prev.source_audio_id !== current.source_audio_id || roundSeconds(prev.source_end_sec) !== roundSeconds(current.source_start_sec)) {
    return null;
  }
  const merged = {
    ...prev,
    id: createId("block"),
    source_end_sec: current.source_end_sec,
  };
  const nextBlocks = [...blocks];
  nextBlocks.splice(index - 1, 2, merged);
  return {
    blocks: nextBlocks,
    selectedBlockId: merged.id,
    selectedBlockStart: getBlockVirtualStart(nextBlocks, index - 1),
  };
}

function mergeWithNext(blocks = [], blockId = "") {
  const index = blocks.findIndex((block) => block.id === blockId);
  if (index < 0 || index >= blocks.length - 1) return null;
  const current = blocks[index];
  const next = blocks[index + 1];
  const currentDuration = getBlockDuration(current);
  const nextDuration = getBlockDuration(next);
  if (currentDuration <= 0 || nextDuration <= 0) return null;

  const sameSilence = current.type === "silence" && next.type === "silence";
  const sameAudio = current.type !== "silence"
    && next.type !== "silence"
    && current.source_audio_id === next.source_audio_id
    && roundSeconds(current.source_end_sec) === roundSeconds(next.source_start_sec);

  if (!sameSilence && !sameAudio) return null;

  const merged = sameSilence
    ? {
        ...current,
        id: createId("block"),
        source_start_sec: 0,
        source_end_sec: roundSeconds(currentDuration + nextDuration),
      }
    : {
        ...current,
        id: createId("block"),
        source_end_sec: next.source_end_sec,
      };

  const nextBlocks = [...blocks];
  nextBlocks.splice(index, 2, merged);
  return {
    blocks: nextBlocks,
    selectedBlockId: merged.id,
    selectedBlockStart: getBlockVirtualStart(nextBlocks, index),
  };
}

function deleteBlockAndMark(blocks = [], blockId = "", deletionMarkers = []) {
  const index = blocks.findIndex((block) => block.id === blockId);
  if (index < 0) return null;
  const block = blocks[index];
  const start = getBlockVirtualStart(blocks, index);
  const removedDuration = getBlockDuration(block);
  if (removedDuration <= 0) return null;
  const nextBlocks = blocks.filter((item) => item.id !== blockId);
  const shiftedMarkers = (Array.isArray(deletionMarkers) ? deletionMarkers : []).map((marker) => {
    const markerTime = roundSeconds(marker?.at_sec);
    if (markerTime >= start + removedDuration) return { ...marker, at_sec: roundSeconds(markerTime - removedDuration) };
    if (markerTime >= start && markerTime < start + removedDuration) return { ...marker, at_sec: roundSeconds(start) };
    return marker;
  });
  shiftedMarkers.push({
    id: createId("delete"),
    at_sec: roundSeconds(start),
    removed_duration_sec: removedDuration,
  });
  return {
    blocks: nextBlocks,
    deletionMarkers: shiftedMarkers,
    nextTimeSec: clampSeconds(start, 0, getTimelineDuration(nextBlocks)),
    removedDuration,
  };
}

function BlockTimeline({
  blocks = [],
  selectedBlockId = "",
  currentTimeSec = 0,
  totalDurationSec = 0,
  deletionMarkers = [],
  onSeek,
  onSelectBlock,
  onBlockDoubleClick,
}) {
  const safeDuration = Math.max(0, normalizeNumber(totalDurationSec, 0));
  const playheadLeft = safeDuration > 0 ? `${Math.min(100, Math.max(0, (currentTimeSec / safeDuration) * 100))}%` : "0%";

  return (
    <div className="podcastBlockTimelineWrap">
      <div
        className="podcastBlockTimeline"
        role="button"
        tabIndex={0}
        aria-label="Блочная аудио-дорожка"
        onClick={(event) => {
          if (!safeDuration || !onSeek) return;
          const rect = event.currentTarget.getBoundingClientRect();
          const ratio = rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0;
          onSeek(clampSeconds(ratio * safeDuration, 0, safeDuration));
        }}
      >
        {blocks.map((block, index) => {
          const duration = getBlockDuration(block);
          const startSec = getBlockVirtualStart(blocks, index);
          const widthPercent = safeDuration > 0 ? `${(duration / safeDuration) * 100}%` : "0%";
          const leftPercent = safeDuration > 0 ? `${(startSec / safeDuration) * 100}%` : "0%";
          const isSelected = block.id === selectedBlockId;
          const color = getBlockRenderColor(block);
          const badgeText = getBlockInitialText(block);
          const labelText = getBlockLabelText(block);
          return (
            <div
              key={block.id}
              className={`podcastAudioBlock${isSelected ? " selected" : ""}${block.type === "silence" ? " silence" : ""}${(block.source_audio_id || "main") === "main" && block.type !== "silence" && !hasPhraseIdentity(block) && !getStoredBlockLabel(block) ? " narrator" : ""}${hasPhraseIdentity(block) ? " phrase" : ""}${badgeText ? " labeled" : ""}`}
              style={{ left: leftPercent, width: widthPercent, "--block-color": color }}
              onClick={(event) => {
                event.stopPropagation();
                onSelectBlock?.(block.id, startSec);
              }}
              onDoubleClick={(event) => {
                event.stopPropagation();
                onBlockDoubleClick?.(block.id, startSec, { x: event.clientX, y: event.clientY });
              }}
              title={`${labelText ? `${labelText} · ` : ""}${formatTimer(startSec)} → ${formatTimer(startSec + duration)} · двойной клик = меню`}
            >
              {badgeText ? <span className="podcastBlockBadge" title={labelText}>{badgeText}</span> : null}
            </div>
          );
        })}

        {deletionMarkers.map((marker) => {
          const left = safeDuration > 0 ? `${(roundSeconds(marker.at_sec) / safeDuration) * 100}%` : "0%";
          return <div key={marker.id} className="podcastDeleteCutMark" style={{ left }} title={`Удалено ${formatTimer(marker.removed_duration_sec)}`} />;
        })}

        <div className="podcastPlayhead" style={{ left: playheadLeft }}>
          <span>{formatTimer(currentTimeSec)}</span>
        </div>
      </div>
    </div>
  );
}

export default function PodcastAudioComposerPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const sourceNodeId = String(location.state?.sourceNodeId || searchParams.get("sourceNodeId") || "").trim();
  const stateAudio = normalizeManualTimingAudio(location.state?.audio);
  const storedManualTimingProject = useMemo(() => readManualTimingProjectForNode(sourceNodeId), [sourceNodeId]);
  const audio = useMemo(() => {
    if (stateAudio.url) return stateAudio;
    return normalizeManualTimingAudio(storedManualTimingProject?.audio);
  }, [stateAudio, storedManualTimingProject]);

  const audioRef = useRef(null);
  const fragmentAudioRef = useRef(null);
  const fragmentPlaybackRafRef = useRef(null);
  const silenceFrameRef = useRef(null);
  const phrasePreviewRef = useRef(null);
  const actorAudioInputRef = useRef(null);
  const blockMenuRef = useRef(null);
  const activeActorPlaybackRef = useRef(null);
  const activeMainPlaybackRef = useRef(null);
  const playbackGuardRafRef = useRef(null);
  const sequenceTransitionRef = useRef(false);
  const selectedBlockIdRef = useRef("");
  const blocksRef = useRef([]);
  const currentBlockIndexRef = useRef(0);
  const hydratedRef = useRef(false);

  const [durationSec, setDurationSec] = useState(() => normalizeNumber(audio.duration_sec, 0));
  const [currentTimeSec, setCurrentTimeSec] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [blocks, setBlocks] = useState([]);
  const [selectedBlockId, setSelectedBlockId] = useState("");
  const [deletionMarkers, setDeletionMarkers] = useState([]);
  const [microStepSec, setMicroStepSec] = useState(DEFAULT_MICRO_STEP_SEC);
  const [history, setHistory] = useState([]);
  const [savedClips, setSavedClips] = useState([]);
  const [actorAudios, setActorAudios] = useState([]);
  const [blockMenu, setBlockMenu] = useState(null);
  const [saveClipDialog, setSaveClipDialog] = useState(null);
  const [deleteActorDialog, setDeleteActorDialog] = useState(null);
  const [guideJsonDialog, setGuideJsonDialog] = useState(null);
  const [phrasePreview, setPhrasePreview] = useState({ clipId: "", positionSec: 0, isPlaying: false });
  const [message, setMessage] = useState("");
  const [brokenPhrases, setBrokenPhrases] = useState([]);
  const [repairSelections, setRepairSelections] = useState({});
  const [repairBusyKey, setRepairBusyKey] = useState("");
  const [hasHydrated, setHasHydrated] = useState(false);
  const [finalAudioBusy, setFinalAudioBusy] = useState("");

  const totalDurationSec = useMemo(() => getTimelineDuration(blocks), [blocks]);
  const audioSignature = useMemo(() => getAudioSignature(audio, durationSec || audio.duration_sec), [audio, durationSec]);

  useEffect(() => {
    blocksRef.current = blocks;
  }, [blocks]);

  useEffect(() => {
    selectedBlockIdRef.current = selectedBlockId;
  }, [selectedBlockId]);

  useEffect(() => {
    hydratedRef.current = hasHydrated;
  }, [hasHydrated]);

  const updateBlockMenuPosition = (menuElement = blockMenuRef.current) => {
    if (!blockMenu) return;
    const clickX = Number.isFinite(blockMenu.clickX) ? blockMenu.clickX : blockMenu.x;
    const clickY = Number.isFinite(blockMenu.clickY) ? blockMenu.clickY : blockMenu.y;
    const rect = menuElement?.getBoundingClientRect();
    const safePosition = getSafeBlockMenuPosition(clickX, clickY, {
      width: rect?.width,
      height: rect?.height,
    });
    const shouldUpdate =
      blockMenu.x !== safePosition.x ||
      blockMenu.y !== safePosition.y ||
      blockMenu.menuWidth !== safePosition.menuWidth ||
      blockMenu.menuHeight !== safePosition.menuHeight ||
      blockMenu.flippedX !== safePosition.flippedX ||
      blockMenu.shiftedY !== safePosition.shiftedY;

    console.log("[PODCAST BLOCK MENU POSITION]", {
      clickX,
      clickY,
      menuWidth: safePosition.menuWidth,
      menuHeight: safePosition.menuHeight,
      finalLeft: safePosition.x,
      finalTop: safePosition.y,
      flippedX: safePosition.flippedX,
      shiftedY: safePosition.shiftedY,
    });

    if (shouldUpdate) {
      setBlockMenu((menu) => menu ? {
        ...menu,
        x: safePosition.x,
        y: safePosition.y,
        menuWidth: safePosition.menuWidth,
        menuHeight: safePosition.menuHeight,
        flippedX: safePosition.flippedX,
        shiftedY: safePosition.shiftedY,
      } : menu);
    }
  };

  useLayoutEffect(() => {
    if (!blockMenu) return;
    updateBlockMenuPosition();
  }, [blockMenu?.blockId, blockMenu?.clickX, blockMenu?.clickY, savedClips.length]);

  useEffect(() => {
    if (!blockMenu) return undefined;
    const handleViewportChange = () => updateBlockMenuPosition();
    window.addEventListener("resize", handleViewportChange);
    window.addEventListener("scroll", handleViewportChange, true);
    return () => {
      window.removeEventListener("resize", handleViewportChange);
      window.removeEventListener("scroll", handleViewportChange, true);
    };
  }, [blockMenu]);

  useEffect(() => {
    if (!audioRef.current || !audio.url) return;
    const mainSrc = new URL(audio.url, window.location.href).href;
    if (audioRef.current.src !== mainSrc) {
      audioRef.current.src = audio.url;
      audioRef.current.load();
    }
  }, [audio.url]);


  useEffect(() => {
    return () => {
      stopMainPlayback();
    };
  }, []);

  useEffect(() => {
    const safeDuration = normalizeNumber(audio.duration_sec, 0);
    setDurationSec(safeDuration);
    setCurrentTimeSec(0);
    stopMainPlayback();
    setBlocks([]);
    setSelectedBlockId("");
    setDeletionMarkers([]);
    setMicroStepSec(DEFAULT_MICRO_STEP_SEC);
    setHistory([]);
    setSavedClips([]);
    setActorAudios([]);
    activeActorPlaybackRef.current = null;
    setBlockMenu(null);
    setSaveClipDialog(null);
    setGuideJsonDialog(null);
    setMessage("");
    setBrokenPhrases([]);
    setRepairSelections({});
    setRepairBusyKey("");
    setHasHydrated(false);
    hydratedRef.current = false;
    currentBlockIndexRef.current = 0;
  }, [audio.url, audio.filename, audio.duration_sec]);

  useEffect(() => {
    if (!hasHydrated || !audio.url) return;
    writeComposerStorage(sourceNodeId, {
      version: COMPOSER_STORAGE_VERSION,
      mainAudioSignature: audioSignature,
      blocks: serializeBlocksForStorage(blocks),
      selectedBlockId,
      deletionMarkers,
      savedClips: serializeSavedClipsForStorage(savedClips),
      actorAudios: serializeActorAudiosForStorage(actorAudios),
      microStepSec,
      updatedAt: Date.now(),
    });
  }, [audio.url, audioSignature, blocks, selectedBlockId, deletionMarkers, savedClips, actorAudios, microStepSec, hasHydrated, sourceNodeId]);
  useEffect(() => {
    if (!hasHydrated || !actorAudios.length) return;
    actorAudios.forEach((actor) => {
      if (!actor?.id || !isBlobUrl(actor.url)) return;
      void fetch(actor.url)
        .then((response) => response.blob())
        .then((blob) => putActorAudioBlob(getActorAudioBlobKey(sourceNodeId, actor.id), blob))
        .catch(() => {});
    });
  }, [actorAudios, hasHydrated, sourceNodeId]);


  const hydrateState = async (nextDurationSec) => {
    const safeDuration = roundSeconds(nextDurationSec);
    if (!audio.url || safeDuration <= 0) return;
    const saved = readComposerStorage(sourceNodeId);
    const signature = getAudioSignature(audio, safeDuration);
    const canRestore = RESTORABLE_STORAGE_VERSIONS.has(Number(saved?.version)) && saved?.mainAudioSignature === signature;
    if (canRestore) {
      const restoredActorAudios = await restoreActorAudiosFromStorage(sourceNodeId, saved.actorAudios || []);
      const restoredSavedClips = serializeSavedClipsForStorage(Array.isArray(saved.savedClips) ? saved.savedClips : []).map((clip) => {
        const actor = restoredActorAudios.find((item) => item.id === clip.source_audio_id);
        const actorServerUrl = getActorServerSourceUrl(actor || {});
        return actor ? { ...clip, source_url: actor.url || actorServerUrl || undefined, asset_url: actorServerUrl || clip.asset_url || clip.assetUrl || undefined, server_url: actorServerUrl || clip.server_url || undefined, source_name: clip.source_name || actor.name || actor.filename } : clip;
      });
      const restoredBlocks = normalizeBlocks(saved.blocks, safeDuration, restoredSavedClips).map((block) => {
        const actor = restoredActorAudios.find((item) => item.id === block.source_audio_id);
        const actorServerUrl = getActorServerSourceUrl(actor || {});
        return actor ? { ...block, source_url: actor.url || actorServerUrl || undefined, asset_url: actorServerUrl || block.asset_url || block.assetUrl || undefined, server_url: actorServerUrl || block.server_url || undefined, source_name: block.source_name || actor.name || actor.filename } : block;
      });
      setBlocks(restoredBlocks);
      setSelectedBlockId(String(saved.selectedBlockId || restoredBlocks[0]?.id || ""));
      setDeletionMarkers(Array.isArray(saved.deletionMarkers) ? saved.deletionMarkers : []);
      setSavedClips(restoredSavedClips);
      setActorAudios(restoredActorAudios);
      setMicroStepSec(clampSeconds(saved.microStepSec, 0.01, 30) || DEFAULT_MICRO_STEP_SEC);
      setMessage(restoredActorAudios.length ? "Монтаж восстановлен после перезагрузки, включая дополнительные аудио актёров." : "Монтаж восстановлен после перезагрузки.");
    } else {
      const initialBlocks = createInitialBlocks(safeDuration);
      setBlocks(initialBlocks);
      setSelectedBlockId(initialBlocks[0]?.id || "");
      setDeletionMarkers([]);
      setSavedClips([]);
      setActorAudios([]);
      setMicroStepSec(DEFAULT_MICRO_STEP_SEC);
      if (saved) removeComposerStorage(sourceNodeId);
    }
    setHasHydrated(true);
    hydratedRef.current = true;
  };

  useEffect(() => {
    const safeDuration = normalizeNumber(durationSec || audio.duration_sec, 0);
    if (!audio.url || safeDuration <= 0 || hydratedRef.current) return;
    void hydrateState(safeDuration);
  }, [audio.url, audio.duration_sec, durationSec]);

  const pushHistory = (label = "edit") => {
    const snapshot = {
      id: createId("history"),
      label,
      blocks: cloneJson(blocks),
      selectedBlockId,
      deletionMarkers: cloneJson(deletionMarkers),
      currentTimeSec,
    };
    setHistory((items) => [...items.slice(-(MAX_HISTORY_ITEMS - 1)), snapshot]);
  };

  const onBackToNode = () => {
    navigate("/studio/storyboard", {
      state: {
        focusManualTimingNodeId: sourceNodeId,
        manualBoardSkipOpenStateReason: "podcast_back_to_node",
        closeManualDirectorBoard: true,
        closeLegacyScenarioEditors: true,
      },
    });
  };

  const waitForAudioReady = (element) => new Promise((resolve) => {
    if (!element) return resolve(false);
    if (element.readyState >= 1) return resolve(true);
    const done = () => resolve(true);
    const fail = () => resolve(false);
    element.addEventListener("loadedmetadata", done, { once: true });
    element.addEventListener("error", fail, { once: true });
  });

  const prepareAudioElement = async (sourceUrl = "", sourceTimeSec = 0) => {
    const element = audioRef.current;
    const safeUrl = String(sourceUrl || "").trim();
    if (!element || !safeUrl) return false;
    const normalizedUrl = new URL(safeUrl, window.location.href).href;
    if (element.src !== normalizedUrl) {
      element.pause();
      element.src = safeUrl;
      element.load();
      await waitForAudioReady(element);
    }
    try {
      element.currentTime = roundSeconds(sourceTimeSec);
    } catch {}
    return true;
  };

  const prepareBlockAudio = async (block, sourceTimeSec = null) => {
    if (!block || block.type === "silence") return false;
    const sourceUrl = getItemSourceUrl(block, audio, actorAudios);
    const time = sourceTimeSec === null || sourceTimeSec === undefined ? block.source_start_sec : sourceTimeSec;
    return prepareAudioElement(sourceUrl, time);
  };

  function stopMainPlaybackGuard() {
    if (playbackGuardRafRef.current) {
      cancelAnimationFrame(playbackGuardRafRef.current);
      playbackGuardRafRef.current = null;
    }
  }

  function stopFragmentPlayback({ pause = true } = {}) {
    if (fragmentPlaybackRafRef.current) {
      cancelAnimationFrame(fragmentPlaybackRafRef.current);
      fragmentPlaybackRafRef.current = null;
    }
    const fragmentElement = fragmentAudioRef.current;
    if (pause && fragmentElement) {
      try {
        fragmentElement.pause();
        fragmentElement.src = "";
      } catch {}
    }
    fragmentAudioRef.current = null;
  }

  function playNextSequenceBlock(fromIndex, toIndex) {
    if (sequenceTransitionRef.current) {
      console.info("[PAC PLAY NEXT SKIPPED_DUPLICATE]", { fromIndex, toIndex });
      return;
    }

    sequenceTransitionRef.current = true;

    const nextBlock = blocksRef.current?.[toIndex];
    const nextStart = nextBlock ? getBlockVirtualStart(blocksRef.current, toIndex) : getTimelineDuration(blocksRef.current);
    const nextItem = nextBlock ? normalizeComposerPlaybackBlock(nextBlock, toIndex, nextStart) : null;
    console.info("[PAC PLAY NEXT]", {
      fromIndex,
      toIndex,
      nextType: nextItem?.type || "end",
    });

    window.setTimeout(() => {
      sequenceTransitionRef.current = false;
    }, 80);

    void playSequenceBlock(toIndex, 0);
  }

  function stopSelectedBlockPlaybackAtEnd(reason = "selected_block_end") {
    void reason;
    sequenceTransitionRef.current = false;
    const element = audioRef.current;
    const session = activeMainPlaybackRef.current;
    stopMainPlaybackGuard();
    stopFragmentPlayback();

    if (element) {
      try {
        if (session?.sourceEndSec != null && session?.type !== "inserted_audio") {
          element.currentTime = roundSeconds(session.sourceEndSec);
        }
        element.pause();
      } catch {}
    }

    if (session) {
      setCurrentTimeSec(roundSeconds(session.virtualStartSec + session.durationSec));
    }

    setIsPlaying(false);
    activeMainPlaybackRef.current = null;
  }

  function stopMainPlayback() {
    sequenceTransitionRef.current = false;
    const element = audioRef.current;
    stopMainPlaybackGuard();
    stopSilencePlayback();
    stopFragmentPlayback();
    activeMainPlaybackRef.current = null;
    if (element) {
      try {
        element.pause();
      } catch {}
    }
    setIsPlaying(false);
  }

  function startMainPlaybackGuard() {
    stopMainPlaybackGuard();

    const tick = () => {
      const element = audioRef.current;
      const session = activeMainPlaybackRef.current;
      if (!element || !session) {
        stopMainPlaybackGuard();
        return;
      }

      const sourceTime = roundSeconds(element.currentTime);
      const sourceEnd = roundSeconds(session.sourceEndSec);

      if (sourceTime >= sourceEnd - 0.015) {
        if (session.mode === "sequence") {
          playNextSequenceBlock(session.blockIndex, session.blockIndex + 1);
        } else {
          stopSelectedBlockPlaybackAtEnd("raf_guard_end");
        }
        return;
      }

      const position = clampSeconds(sourceTime - session.sourceStartSec, 0, session.durationSec);
      setCurrentTimeSec(roundSeconds(session.virtualStartSec + position));
      playbackGuardRafRef.current = requestAnimationFrame(tick);
    };

    playbackGuardRafRef.current = requestAnimationFrame(tick);
  }

  const stopActorPlayback = ({ pause = true } = {}) => {
    activeActorPlaybackRef.current = null;
    setActorAudios((items) => items.map((item) => item.isPlaying ? { ...item, isPlaying: false } : item));
    if (pause && audioRef.current) audioRef.current.pause();
  };

  const seekOnTimeline = (timeSec, blocksOverride = blocksRef.current) => {
    const safeBlocks = Array.isArray(blocksOverride) ? blocksOverride : [];
    const nextDuration = getTimelineDuration(safeBlocks);
    const nextTime = clampSeconds(timeSec, 0, nextDuration);
    const position = findTimelinePosition(safeBlocks, nextTime);
    setCurrentTimeSec(nextTime);
    if (position) {
      currentBlockIndexRef.current = position.index;
      if (audioRef.current && position.block?.type !== "silence") {
        prepareBlockAudio(position.block, position.source_time_sec);
      }
    }
  };

  const selectBlock = (blockId, startSec) => {
    setSelectedBlockId(blockId);
    seekOnTimeline(startSec, blocksRef.current);
    setMessage("Выбран блок. Жёлтая линия — только прицел разреза. Доводчик меняет конец выбранного блока: ← отдаёт часть правому соседу, → забирает часть у правого соседа.");
  };

  const openBlockMenu = (blockId, startSec, point = {}) => {
    const clickX = Number.isFinite(point.x) ? point.x : 220;
    const clickY = Number.isFinite(point.y) ? point.y : 220;
    const safePosition = getSafeBlockMenuPosition(clickX, clickY);
    setSelectedBlockId(blockId);
    seekOnTimeline(startSec, blocksRef.current);
    setBlockMenu({
      blockId,
      clickX,
      clickY,
      x: safePosition.x,
      y: safePosition.y,
      menuWidth: safePosition.menuWidth,
      menuHeight: safePosition.menuHeight,
      flippedX: safePosition.flippedX,
      shiftedY: safePosition.shiftedY,
    });
    setSaveClipDialog(null);
    setMessage("Открыто меню блока: удаление, сохранение, цвет блока и аудио-фразы.");
  };

  const closeBlockMenu = () => {
    setBlockMenu(null);
    setSaveClipDialog(null);
  };

  const getSelectedBlockSnapshot = () => {
    const index = getSelectedBlockIndex(blocks, selectedBlockId);
    if (index < 0) return null;
    const block = blocks[index];
    return {
      index,
      block,
      startSec: getBlockVirtualStart(blocks, index),
      durationSec: getBlockDuration(block),
    };
  };

  const openSaveDialog = () => {
    const snapshot = getSelectedBlockSnapshot();
    if (!snapshot) {
      setMessage("Сначала выбери блок для сохранения.");
      return;
    }
    setSaveClipDialog({
      label: `Фраза ${savedClips.length + 1}`,
    });
  };

  const saveSelectedClip = () => {
    const snapshot = getSelectedBlockSnapshot();
    if (!snapshot) return;
    const nextIndex = savedClips.length + 1;
    const label = String(saveClipDialog?.label || `Фраза ${nextIndex}`).trim() || `Фраза ${nextIndex}`;
    const block = snapshot.block;
    const clip = {
      id: createId("saved_clip"),
      label,
      type: block.type || "audio",
      source_audio_id: block.source_audio_id || "main",
      source_url: getItemSourceUrl(block, audio, actorAudios),
      source_name: block.source_name || getAudioNameForSourceId(block.source_audio_id || "main", audio, actorAudios),
      source_start_sec: roundSeconds(block.source_start_sec),
      source_end_sec: roundSeconds(block.source_end_sec),
      duration_sec: snapshot.durationSec,
      color_index: Number.isInteger(block.color_index) ? block.color_index : (savedClips.length % BLOCK_COLORS.length),
      color: typeof block.color === "string" && block.color.trim() ? block.color.trim() : undefined,
      source_label: getBlockLabelText(block),
      created_at: Date.now(),
    };
    setSavedClips((items) => [...items, clip]);
    setSaveClipDialog(null);
    setMessage(`Сохранена аудио-фраза “${label}” (${formatTimer(snapshot.durationSec)}).`);
  };

  const deleteSavedClip = (clipId) => {
    const safeClipId = String(clipId || "");
    if (!safeClipId) return;
    const clip = savedClips.find((item) => item.id === safeClipId);
    if (phrasePreviewRef.current?.clipId === safeClipId || phrasePreview.clipId === safeClipId) {
      stopPhrasePreview({ pause: true });
    }
    setSavedClips((items) => items.filter((item) => item.id !== safeClipId));
    setMessage(clip ? `Аудио-фраза “${clip.label}” удалена из списка.` : "Аудио-фраза удалена из списка.");
  };

  const replaceSelectedWithSilence = () => {
    stopMainPlayback();
    const snapshot = getSelectedBlockSnapshot();
    if (!snapshot) return;
    pushHistory("replace_with_silence");
    const nextBlocks = [...blocks];
    nextBlocks[snapshot.index] = {
      ...snapshot.block,
      id: createId("block"),
      type: "silence",
      source_audio_id: "silence",
      source_start_sec: 0,
      source_end_sec: snapshot.durationSec,
      color_index: 4,
      block_label: "Тишина",
    };
    setBlocks(nextBlocks);
    setSelectedBlockId(nextBlocks[snapshot.index].id);
    seekOnTimeline(snapshot.startSec, nextBlocks);
    setBlockMenu(null);
    setMessage(`Выбранный блок заменён тишиной ${formatTimer(snapshot.durationSec)}. Дальше её длину можно подгонять доводчиком.`);
  };

  const repairOptions = useMemo(() => buildActorPhraseRepairOptions(actorAudios), [actorAudios]);

  const removeBrokenPhraseFromPanel = (item = {}) => {
    const key = getBrokenPhraseRepairKey(item);
    setBrokenPhrases((rows) => rows.filter((row) => getBrokenPhraseRepairKey(row) !== key));
    setRepairSelections((items) => {
      const next = { ...(items || {}) };
      delete next[key];
      return next;
    });
  };

  const selectBrokenPhraseOnTimeline = (item = {}) => {
    const blockId = String(item?.blockId || "").trim();
    const index = blocks.findIndex((block) => String(block?.id || "").trim() === blockId);
    if (index < 0) {
      setMessage(`Не нашёл блок ${blockId || "фразы"} на таймлайне.`);
      return;
    }
    const startSec = getBlockVirtualStart(blocks, index);
    setSelectedBlockId(blocks[index].id);
    seekOnTimeline(startSec, blocks);
    setBlockMenu(null);
    setMessage(`Найдена сломанная фраза “${getBrokenPhraseLabel(item)}” на ${formatTimer(startSec)}.`);
  };

  const deleteBrokenPhraseInsertion = (item = {}) => {
    stopMainPlayback();
    const blockId = String(item?.blockId || "").trim();
    if (!blockId) return;
    const result = deleteBlockAndMark(blocks, blockId, deletionMarkers);
    if (!result) {
      setMessage(`Не удалось удалить вставку “${getBrokenPhraseLabel(item)}”.`);
      return;
    }
    pushHistory("delete_broken_phrase");
    setBlocks(result.blocks);
    setDeletionMarkers(result.deletionMarkers);
    setSelectedBlockId(result.blocks[0]?.id || "");
    seekOnTimeline(result.nextTimeSec, result.blocks);
    removeBrokenPhraseFromPanel(item);
    setMessage(`Удалена вставка сломанной фразы “${getBrokenPhraseLabel(item)}”.`);
  };

  const getSelectedRepairOptionValue = (item = {}) => {
    const key = getBrokenPhraseRepairKey(item);
    const explicit = String(repairSelections?.[key] || "").trim();
    if (explicit) return explicit;
    const selectedActor = (Array.isArray(actorAudios) ? actorAudios : []).find((actor) => String(actor?.selectedBlockId || "").trim());
    if (selectedActor?.id && selectedActor?.selectedBlockId) return `${selectedActor.id}::${selectedActor.selectedBlockId}`;
    return repairOptions[0]?.value || "";
  };

  const replaceBrokenPhraseWithActorBlock = async (item = {}) => {
    stopMainPlayback();
    const key = getBrokenPhraseRepairKey(item);
    const optionValue = getSelectedRepairOptionValue(item);
    const option = repairOptions.find((candidate) => candidate.value === optionValue);
    if (!option?.actor || !option?.block) {
      setMessage("Добавь аудио актёра, выбери в нём блок и затем замени сломанную фразу.");
      return;
    }

    const blockId = String(item?.blockId || "").trim();
    const blockIndex = blocks.findIndex((block) => String(block?.id || "").trim() === blockId);
    if (blockIndex < 0) {
      setMessage(`Не нашёл блок ${blockId || "фразы"} на таймлайне.`);
      return;
    }

    const targetBlock = blocks[blockIndex];
    const label = getBrokenPhraseLabel(item);
    const duration = getBlockDuration(option.block);
    if (duration <= 0) {
      setMessage("Выбранный блок актёрского аудио пустой.");
      return;
    }

    setRepairBusyKey(key);
    setMessage(`Сохраняю новую независимую фразу “${label}”...`);
    try {
      const baseClip = {
        id: String(item?.savedClipId || item?.insertedPhraseId || targetBlock?.saved_clip_id || targetBlock?.inserted_phrase_id || createId("saved_clip")).trim(),
        label,
        type: "audio",
        source_audio_id: option.actorId,
        source_url: option.actor.url,
        source_name: option.actor.name || option.actor.filename || option.actorId,
        source_start_sec: roundSeconds(option.block.source_start_sec),
        source_end_sec: roundSeconds(option.block.source_end_sec),
        duration_sec: duration,
        color_index: Number.isInteger(targetBlock?.color_index) ? targetBlock.color_index : option.block.color_index,
        color: targetBlock?.color || option.block.color || option.actor.color,
        source_label: option.actor.label || option.actor.name || option.actorId,
        repair_source_audio_id: String(item?.sourceAudioId || targetBlock?.source_audio_id || "").trim(),
        created_at: Date.now(),
      };
      const converted = await makeIndependentSavedClip({ clip: baseClip, actor: option.actor, block: option.block, label });
      const sourceUrl = converted.source_url || converted.asset_url || converted.assetUrl || converted.server_url || converted.publicUrl || "";
      const newDuration = roundSeconds(converted.duration_sec || duration);

      pushHistory("repair_broken_phrase");
      setSavedClips((clips) => {
        const ids = new Set([converted.id, item?.savedClipId, item?.insertedPhraseId].map((value) => String(value || "").trim()).filter(Boolean));
        let replaced = false;
        const next = (Array.isArray(clips) ? clips : []).map((clip) => {
          const clipIds = [clip?.id, clip?.saved_clip_id, clip?.inserted_phrase_id].map((value) => String(value || "").trim());
          if (!clipIds.some((id) => ids.has(id))) return clip;
          replaced = true;
          return { ...clip, ...converted };
        });
        return replaced ? next : [...next, converted];
      });

      const nextBlocks = blocks.map((block, index) => {
        if (index !== blockIndex) return block;
        return {
          ...block,
          type: "phrase",
          block_kind: "phrase",
          source_kind: "inserted_audio",
          block_type: "inserted_audio",
          source_audio_id: converted.id,
          source_url: sourceUrl,
          audio_url: sourceUrl,
          fragment_url: sourceUrl,
          asset_url: converted.asset_url || sourceUrl,
          assetUrl: converted.assetUrl || sourceUrl,
          server_url: converted.server_url || sourceUrl,
          publicUrl: converted.publicUrl || sourceUrl,
          src: sourceUrl,
          source_name: converted.source_name || converted.filename || label,
          source_start_sec: 0,
          source_end_sec: newDuration,
          duration_sec: newDuration,
          saved_clip_id: converted.id,
          inserted_phrase_id: converted.id,
          saved_clip_label: converted.label || label,
          inserted_phrase_label: converted.label || label,
          phrase_label: converted.label || label,
          block_label: converted.label || label,
          original_source_audio_id: String(item?.sourceAudioId || block?.source_audio_id || "").trim(),
        };
      });
      setBlocks(nextBlocks);
      setSelectedBlockId(nextBlocks[blockIndex]?.id || "");
      seekOnTimeline(getBlockVirtualStart(nextBlocks, blockIndex), nextBlocks);
      removeBrokenPhraseFromPanel(item);
      setMessage(`Фраза “${label}” заменена независимым asset ${formatTimer(newDuration)}.`);
    } catch (error) {
      console.error("[PODCAST BROKEN PHRASE REPAIR_FAILED]", error);
      setMessage(`Не удалось заменить фразу “${label}”: ${error?.message || error}`);
    } finally {
      setRepairBusyKey("");
    }
  };

  const replaceSelectedWithSavedClip = (clip) => {
    stopMainPlayback();
    const snapshot = getSelectedBlockSnapshot();
    if (!snapshot || !clip) return;
    const clipAudioUrl = getItemSourceUrl(clip, audio, actorAudios);
    if (!clipAudioUrl) {
      console.warn("[PAC INSERTED_AUDIO_MISSING_URL]", clip);
      setMessage("У вставки нет audio_url, блок не может проигрываться");
      return;
    }
    const clipDuration = roundSeconds(clip.duration_sec || Math.max(0, roundSeconds(clip.source_end_sec) - roundSeconds(clip.source_start_sec)));
    if (clipDuration <= 0) {
      setMessage("У вставки нулевая длительность, блок не может проигрываться");
      return;
    }
    pushHistory("replace_with_saved_clip");
    const nextBlocks = [...blocks];
    nextBlocks[snapshot.index] = {
      ...snapshot.block,
      id: createId("block"),
      type: "phrase",
      block_kind: "phrase",
      source_kind: "inserted_audio",
      block_type: "inserted_audio",
      source_audio_id: clip.id || clip.source_audio_id || "inserted_audio",
      source_url: clipAudioUrl,
      audio_url: clipAudioUrl,
      fragment_url: clipAudioUrl,
      asset_url: clip.asset_url || clipAudioUrl,
      assetUrl: clip.assetUrl || clipAudioUrl,
      server_url: clip.server_url || clipAudioUrl,
      publicUrl: clip.publicUrl || clipAudioUrl,
      src: clipAudioUrl,
      source_name: clip.source_name || clip.filename || getAudioNameForSourceId(clip.source_audio_id || "main", audio, actorAudios),
      source_start_sec: 0,
      source_end_sec: clipDuration,
      duration_sec: clipDuration,
      color_index: Number.isInteger(clip.color_index) ? clip.color_index : snapshot.block.color_index,
      color: typeof clip.color === "string" && clip.color.trim() ? clip.color.trim() : undefined,
      block_label: String(clip.label || "Фраза").trim() || "Фраза",
      phrase_label: String(clip.label || "Фраза").trim() || "Фраза",
      inserted_phrase_label: String(clip.label || "Фраза").trim() || "Фраза",
      saved_clip_label: String(clip.label || "Фраза").trim() || "Фраза",
      inserted_phrase_id: clip.id || createId("phrase"),
      saved_clip_id: clip.id || undefined,
    };
    setBlocks(nextBlocks);
    setSelectedBlockId(nextBlocks[snapshot.index].id);
    seekOnTimeline(snapshot.startSec, nextBlocks);
    setBlockMenu(null);
    setMessage(`Выбранный блок заменён сохранённым куском “${clip.label}”.`);
  };

  const insertSavedClipAfterSelected = (clip) => {
    stopMainPlayback();
    const snapshot = getSelectedBlockSnapshot();
    if (!snapshot || !clip) return;
    const clipAudioUrl = getItemSourceUrl(clip, audio, actorAudios);
    if (!clipAudioUrl) {
      console.warn("[PAC INSERTED_AUDIO_MISSING_URL]", clip);
      setMessage("У вставки нет audio_url, блок не может проигрываться");
      return;
    }
    const clipDuration = roundSeconds(clip.duration_sec || Math.max(0, roundSeconds(clip.source_end_sec) - roundSeconds(clip.source_start_sec)));
    if (clipDuration <= 0) {
      setMessage("У вставки нулевая длительность, блок не может проигрываться");
      return;
    }
    pushHistory("insert_saved_clip_after");
    const insertedBlock = {
      id: createId("block"),
      type: "phrase",
      block_kind: "phrase",
      source_kind: "inserted_audio",
      block_type: "inserted_audio",
      source_audio_id: clip.id || clip.source_audio_id || "inserted_audio",
      source_url: clipAudioUrl,
      audio_url: clipAudioUrl,
      fragment_url: clipAudioUrl,
      asset_url: clip.asset_url || clipAudioUrl,
      assetUrl: clip.assetUrl || clipAudioUrl,
      server_url: clip.server_url || clipAudioUrl,
      publicUrl: clip.publicUrl || clipAudioUrl,
      src: clipAudioUrl,
      source_name: clip.source_name || clip.filename || getAudioNameForSourceId(clip.source_audio_id || "main", audio, actorAudios),
      source_start_sec: 0,
      source_end_sec: clipDuration,
      duration_sec: clipDuration,
      color_index: Number.isInteger(clip.color_index) ? clip.color_index : (snapshot.index + 1),
      color: typeof clip.color === "string" && clip.color.trim() ? clip.color.trim() : undefined,
      block_label: String(clip.label || "Фраза").trim() || "Фраза",
      phrase_label: String(clip.label || "Фраза").trim() || "Фраза",
      inserted_phrase_label: String(clip.label || "Фраза").trim() || "Фраза",
      saved_clip_label: String(clip.label || "Фраза").trim() || "Фраза",
      inserted_phrase_id: clip.id || createId("phrase"),
      saved_clip_id: clip.id || undefined,
    };
    const nextBlocks = [...blocks];
    nextBlocks.splice(snapshot.index + 1, 0, insertedBlock);
    setBlocks(nextBlocks);
    setSelectedBlockId(insertedBlock.id);
    seekOnTimeline(roundSeconds(snapshot.startSec + snapshot.durationSec), nextBlocks);
    setBlockMenu(null);
    setMessage(`Кусок “${clip.label}” вставлен после выбранного блока.`);
  };

  const setSelectedBlockColor = (color) => {
    const safeColor = String(color || "").trim();
    if (!selectedBlockId || !safeColor) return;
    pushHistory("set_block_color");
    setBlocks((items) => items.map((block) => block.id === selectedBlockId ? { ...block, color: safeColor } : block));
    setMessage(`Цвет выбранного блока изменён.`);
  };

  const getPreviewPosition = (clip) => {
    if (!clip || phrasePreview.clipId !== clip.id) return 0;
    return clampSeconds(phrasePreview.positionSec, 0, clip.duration_sec || getBlockDuration(clip));
  };

  const stopPhrasePreview = ({ pause = true } = {}) => {
    phrasePreviewRef.current = null;
    setPhrasePreview({ clipId: "", positionSec: 0, isPlaying: false });
    if (pause && audioRef.current) audioRef.current.pause();
  };

  const playSavedClipPreview = async (clip) => {
    if (!clip) return;
    const duration = roundSeconds(clip.duration_sec || (roundSeconds(clip.source_end_sec) - roundSeconds(clip.source_start_sec)));
    if (duration <= 0) return;
    stopSilencePlayback();

    if (clip.type === "silence") {
      if (audioRef.current) audioRef.current.pause();
      phrasePreviewRef.current = null;
      setPhrasePreview({ clipId: clip.id, positionSec: 0, isPlaying: false });
      setMessage(`“${clip.label}” — это блок тишины ${formatTimer(duration)}.`);
      return;
    }

    const element = audioRef.current;
    if (!element) return;
    if (phrasePreviewRef.current?.clipId === clip.id && phrasePreview.isPlaying) {
      const pausedAt = clampSeconds(phrasePreview.positionSec, 0, duration);
      element.pause();
      phrasePreviewRef.current = null;
      setPhrasePreview({ clipId: clip.id, positionSec: pausedAt, isPlaying: false });
      return;
    }

    stopActorPlayback({ pause: false });
    const resumeOffset = phrasePreview.clipId === clip.id && phrasePreview.positionSec < duration - 0.025
      ? clampSeconds(phrasePreview.positionSec, 0, duration)
      : 0;
    const sourceUrl = getItemSourceUrl(clip, audio, actorAudios);
    const ready = await prepareAudioElement(sourceUrl, roundSeconds(roundSeconds(clip.source_start_sec) + resumeOffset));
    if (!ready) {
      setMessage(`Не удалось открыть источник аудио-фразы “${clip.label}”.`);
      return;
    }

    phrasePreviewRef.current = {
      clipId: clip.id,
      startSec: roundSeconds(clip.source_start_sec),
      endSec: roundSeconds(clip.source_end_sec),
      durationSec: duration,
    };
    setPhrasePreview({ clipId: clip.id, positionSec: resumeOffset, isPlaying: true });
    setIsPlaying(false);
    try {
      await element.play();
    } catch {
      stopPhrasePreview({ pause: false });
    }
  };

  const seekSavedClipPreview = (clip, offsetSec) => {
    if (!clip) return;
    const duration = roundSeconds(clip.duration_sec || (roundSeconds(clip.source_end_sec) - roundSeconds(clip.source_start_sec)));
    const offset = clampSeconds(offsetSec, 0, duration);
    setPhrasePreview({ clipId: clip.id, positionSec: offset, isPlaying: Boolean(phrasePreviewRef.current?.clipId === clip.id) });
    if (clip.type !== "silence" && audioRef.current) {
      prepareAudioElement(getItemSourceUrl(clip, audio, actorAudios), roundSeconds(roundSeconds(clip.source_start_sec) + offset));
    }
  };

  function stopSilencePlayback() {
    if (silenceFrameRef.current) {
      cancelAnimationFrame(silenceFrameRef.current);
      silenceFrameRef.current = null;
    }
  }

  const playSilenceBlock = (blockIndex, startOffsetSec = 0, mode = "selected_block") => {
    const safeBlocks = blocksRef.current;
    const block = safeBlocks[blockIndex];
    if (!block) return;
    const blockStartSec = getBlockVirtualStart(safeBlocks, blockIndex);
    const duration = getBlockDuration(block);
    const startOffset = clampSeconds(startOffsetSec, 0, duration);
    const startedAt = performance.now();
    stopSilencePlayback();
    activeMainPlaybackRef.current = {
      mode,
      blockId: block.id,
      blockIndex,
      sourceStartSec: roundSeconds(block.source_start_sec),
      sourceEndSec: roundSeconds(block.source_end_sec),
      virtualStartSec: blockStartSec,
      durationSec: duration,
    };
    setCurrentTimeSec(roundSeconds(blockStartSec + startOffset));
    setIsPlaying(true);

    const tick = () => {
      const session = activeMainPlaybackRef.current;
      if (!session || session.blockId !== block.id || session.mode !== mode) {
        silenceFrameRef.current = null;
        return;
      }
      const elapsed = startOffset + (performance.now() - startedAt) / 1000;
      const nextTime = roundSeconds(blockStartSec + Math.min(duration, elapsed));
      setCurrentTimeSec(nextTime);
      if (elapsed >= duration) {
        silenceFrameRef.current = null;
        if (mode === "sequence") {
          playNextSequenceBlock(blockIndex, blockIndex + 1);
        } else {
          activeMainPlaybackRef.current = null;
          setIsPlaying(false);
        }
        return;
      }
      silenceFrameRef.current = requestAnimationFrame(tick);
    };

    silenceFrameRef.current = requestAnimationFrame(tick);
  };

  const playFragmentAudioBlock = async (blockIndex, startOffsetSec = 0, mode = "selected_block") => {
    const safeBlocks = blocksRef.current;
    const block = safeBlocks[blockIndex];
    if (!block) return;
    const blockStartSec = getBlockVirtualStart(safeBlocks, blockIndex);
    const item = normalizeComposerPlaybackBlock(block, blockIndex, blockStartSec);
    logComposerQueueBlock(item);
    const duration = roundSeconds(item.durationSec || getBlockDuration(block));
    const startOffset = clampSeconds(startOffsetSec, 0, duration);

    if (!item.audioUrl) {
      console.warn("[PAC INSERTED_AUDIO_MISSING_URL]", block);
      setMessage("У вставки нет audio_url, блок не может проигрываться");
      if (mode === "sequence") {
        playNextSequenceBlock(blockIndex, blockIndex + 1);
      } else {
        activeMainPlaybackRef.current = null;
        setIsPlaying(false);
      }
      return;
    }

    if (audioRef.current) {
      try {
        audioRef.current.pause();
      } catch {}
    }
    stopMainPlaybackGuard();
    stopSilencePlayback();
    stopFragmentPlayback();

    const fragmentElement = new Audio(item.audioUrl);
    fragmentAudioRef.current = fragmentElement;
    activeMainPlaybackRef.current = {
      mode,
      type: "inserted_audio",
      blockId: item.blockId,
      blockIndex,
      sourceStartSec: 0,
      sourceEndSec: duration,
      virtualStartSec: blockStartSec,
      durationSec: duration,
    };
    currentBlockIndexRef.current = blockIndex;
    setSelectedBlockId(block.id);
    setCurrentTimeSec(roundSeconds(blockStartSec + startOffset));
    setIsPlaying(true);

    let startedAt = performance.now();
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      if (fragmentAudioRef.current !== fragmentElement) return;
      stopFragmentPlayback();
      setCurrentTimeSec(roundSeconds(blockStartSec + duration));
      if (mode === "sequence") {
        playNextSequenceBlock(blockIndex, blockIndex + 1);
      } else {
        activeMainPlaybackRef.current = null;
        setIsPlaying(false);
      }
    };

    const tick = () => {
      const session = activeMainPlaybackRef.current;
      if (!session || session.blockId !== item.blockId || session.mode !== mode || fragmentAudioRef.current !== fragmentElement) {
        fragmentPlaybackRafRef.current = null;
        return;
      }
      const elementTime = Number.isFinite(fragmentElement.currentTime) ? fragmentElement.currentTime : 0;
      const elapsed = Math.max(startOffset + (performance.now() - startedAt) / 1000, elementTime);
      setCurrentTimeSec(roundSeconds(blockStartSec + Math.min(duration, elapsed)));
      if (elapsed >= duration - 0.015) {
        finish();
        return;
      }
      fragmentPlaybackRafRef.current = requestAnimationFrame(tick);
    };

    fragmentElement.addEventListener("ended", finish, { once: true });
    fragmentElement.addEventListener("error", () => {
      console.warn("[PAC INSERTED_AUDIO_MISSING_URL]", block);
      setMessage("У вставки нет audio_url, блок не может проигрываться");
      if (mode === "sequence") playNextSequenceBlock(blockIndex, blockIndex + 1);
      else stopMainPlayback();
    }, { once: true });

    try {
      await waitForAudioReady(fragmentElement);
      try {
        fragmentElement.currentTime = startOffset;
      } catch {}
      startedAt = performance.now();
      await fragmentElement.play();
      fragmentPlaybackRafRef.current = requestAnimationFrame(tick);
    } catch {
      stopFragmentPlayback();
      setMessage("Не удалось открыть вставленный аудио-фрагмент.");
      if (mode === "sequence") playNextSequenceBlock(blockIndex, blockIndex + 1);
      else stopMainPlayback();
    }
  };

  async function playSequenceBlock(blockIndex, startOffsetSec = 0) {
    const safeBlocks = blocksRef.current;
    const element = audioRef.current;
    if (!safeBlocks.length || blockIndex >= safeBlocks.length) {
      stopMainPlayback();
      setCurrentTimeSec(getTimelineDuration(safeBlocks));
      return;
    }

    const safeIndex = Math.max(0, blockIndex);
    const block = safeBlocks[safeIndex];
    const blockStartSec = getBlockVirtualStart(safeBlocks, safeIndex);
    const item = normalizeComposerPlaybackBlock(block, safeIndex, blockStartSec);
    logComposerQueueBlock(item);
    const duration = roundSeconds(item.durationSec || getBlockDuration(block));
    const offset = clampSeconds(startOffsetSec, 0, duration);
    const sourceTime = roundSeconds(item.sourceStartSec + offset);
    currentBlockIndexRef.current = safeIndex;
    setSelectedBlockId(block.id);
    setCurrentTimeSec(roundSeconds(blockStartSec + offset));
    stopMainPlaybackGuard();
    stopSilencePlayback();
    stopFragmentPlayback();

    activeMainPlaybackRef.current = {
      mode: "sequence",
      type: item.type,
      blockId: item.blockId,
      blockIndex: safeIndex,
      sourceStartSec: item.sourceStartSec,
      sourceEndSec: item.sourceEndSec,
      virtualStartSec: blockStartSec,
      durationSec: duration,
    };

    if (item.type === "silence") {
      if (element) {
        try {
          element.pause();
        } catch {}
      }
      playSilenceBlock(safeIndex, offset, "sequence");
      return;
    }

    if (item.type === "inserted_audio") {
      await playFragmentAudioBlock(safeIndex, offset, "sequence");
      return;
    }

    if (!element) {
      stopMainPlayback();
      return;
    }

    const ready = await prepareAudioElement(audio.url || getItemSourceUrl(block, audio, actorAudios), sourceTime);
    if (!ready) {
      stopMainPlayback();
      setMessage("Не удалось открыть источник блока монтажа.");
      return;
    }

    try {
      await element.play();
      setIsPlaying(true);
      startMainPlaybackGuard();
    } catch {
      stopMainPlayback();
    }
  }

  const playFullMontageFrom = (timeSec = 0) => {
    const safeBlocks = blocksRef.current;
    if (!safeBlocks.length) return;
    const nextDuration = getTimelineDuration(safeBlocks);
    if (nextDuration <= 0) return;
    const startTime = clampSeconds(timeSec, 0, nextDuration);
    const position = findTimelinePosition(safeBlocks, startTime) || { index: 0, offset_sec: 0 };
    stopPhrasePreview({ pause: false });
    stopActorPlayback({ pause: false });
    stopMainPlayback();
    void playSequenceBlock(position.index, position.offset_sec || 0);
  };

  const insertSilenceAtCursor = () => {
    stopMainPlayback();
    if (!blocks.length) return;
    const silenceDuration = clampSeconds(DEFAULT_MICRO_STEP_SEC, 0.01, 30);
    const cursorTime = clampSeconds(currentTimeSec, 0, totalDurationSec || durationSec || audio.duration_sec);
    const silenceBlock = createSilenceBlock(silenceDuration, 4);
    const epsilon = 0.001;
    let nextBlocks = [];
    let insertIndex = 0;

    if (cursorTime <= epsilon) {
      nextBlocks = [silenceBlock, ...blocks];
      insertIndex = 0;
    } else if (cursorTime >= totalDurationSec - epsilon) {
      nextBlocks = [...blocks, silenceBlock];
      insertIndex = blocks.length;
    } else {
      const position = findTimelinePosition(blocks, cursorTime);
      if (!position) return;
      const { index, block, offset_sec } = position;
      const blockDuration = getBlockDuration(block);
      const blockStart = getBlockVirtualStart(blocks, index);
      const isAtStart = offset_sec <= epsilon;
      const isAtEnd = Math.abs(offset_sec - blockDuration) <= epsilon;

      if (isAtStart) {
        nextBlocks = [...blocks.slice(0, index), silenceBlock, ...blocks.slice(index)];
        insertIndex = index;
      } else if (isAtEnd) {
        nextBlocks = [...blocks.slice(0, index + 1), silenceBlock, ...blocks.slice(index + 1)];
        insertIndex = index + 1;
      } else {
        const splitPoint = roundSeconds(block.source_start_sec + offset_sec);
        const leftBlock = block.type === "silence"
          ? { ...block, id: createId("block"), source_start_sec: 0, source_end_sec: offset_sec }
          : { ...block, id: createId("block"), source_end_sec: splitPoint };
        const rightBlock = block.type === "silence"
          ? { ...block, id: createId("block"), source_start_sec: 0, source_end_sec: roundSeconds(blockDuration - offset_sec) }
          : { ...block, id: createId("block"), source_start_sec: splitPoint };
        nextBlocks = [...blocks.slice(0, index), leftBlock, silenceBlock, rightBlock, ...blocks.slice(index + 1)];
        insertIndex = index + 1;
      }
    }

    pushHistory("insert_silence_at_cursor");
    setBlocks(nextBlocks);
    setSelectedBlockId(silenceBlock.id);
    setBlockMenu(null);
    setSaveClipDialog(null);
    seekOnTimeline(cursorTime, nextBlocks);
    setMessage("Вставлена тишина 0.5 сек отдельным блоком по жёлтому прицелу. Основное аудио разрезано и сдвинуто вправо на длину тишины. Выбранный блок тишины можно менять доводчиком до 30 сек.");
  };

  const togglePlayback = async () => {
    const element = audioRef.current;
    if (!element || !blocks.length || totalDurationSec <= 0) return;

    if (isPlaying) {
      stopMainPlayback();
      return;
    }

    stopPhrasePreview({ pause: false });
    stopActorPlayback({ pause: false });
    stopMainPlayback();

    let selectedIndex = blocks.findIndex((block) => block.id === selectedBlockId);
    if (selectedIndex < 0) selectedIndex = 0;

    const block = blocks[selectedIndex];
    const blockStartSec = getBlockVirtualStart(blocks, selectedIndex);
    const blockDuration = getBlockDuration(block);
    const blockEndSec = roundSeconds(blockStartSec + blockDuration);
    const currentInsideSelected = currentTimeSec > blockStartSec + 0.025 && currentTimeSec < blockEndSec - 0.025;
    const playOffset = currentInsideSelected ? clampSeconds(currentTimeSec - blockStartSec, 0, blockDuration) : 0;
    const playVirtualTime = roundSeconds(blockStartSec + playOffset);

    if (block) {
      const item = normalizeComposerPlaybackBlock(block, selectedIndex, blockStartSec);
      logComposerQueueBlock(item);
      currentBlockIndexRef.current = selectedIndex;
      setSelectedBlockId(block.id);
      setCurrentTimeSec(playVirtualTime);
      if (item.type === "silence") {
        element.pause();
        playSilenceBlock(selectedIndex, playOffset, "selected_block");
        return;
      }
      if (item.type === "inserted_audio") {
        await playFragmentAudioBlock(selectedIndex, playOffset, "selected_block");
        return;
      }
      const ready = await prepareAudioElement(audio.url || getItemSourceUrl(block, audio, actorAudios), roundSeconds(item.sourceStartSec + playOffset));
      if (!ready) {
        setMessage("Не удалось открыть источник выбранного блока.");
        return;
      }
      activeMainPlaybackRef.current = {
        mode: "selected_block",
        type: item.type,
        blockId: item.blockId,
        blockIndex: selectedIndex,
        sourceStartSec: item.sourceStartSec,
        sourceEndSec: item.sourceEndSec,
        virtualStartSec: blockStartSec,
        durationSec: blockDuration,
      };
    } else if (currentTimeSec >= totalDurationSec - 0.05) {
      seekOnTimeline(0, blocks);
    }

    try {
      await element.play();
      setIsPlaying(true);
      startMainPlaybackGuard();
    } catch {
      stopMainPlaybackGuard();
      activeMainPlaybackRef.current = null;
      setIsPlaying(false);
    }
  };

  const onLoadedMetadata = (event) => {
    const currentSrc = String(event.currentTarget.src || "");
    const mainSrc = audio.url ? new URL(audio.url, window.location.href).href : "";
    if (mainSrc && currentSrc && currentSrc !== mainSrc && hasHydrated) return;
    const metadataDuration = normalizeNumber(event.currentTarget.duration, 0);
    if (metadataDuration > 0) {
      const safeDuration = roundSeconds(metadataDuration);
      setDurationSec(safeDuration);
      if (!hydratedRef.current) void hydrateState(safeDuration);
    }
  };

  const onTimeUpdate = (event) => {
    const safeBlocks = blocksRef.current;
    const element = event.currentTarget;
    const sourceTime = roundSeconds(element.currentTime);

    const actorPlayback = activeActorPlaybackRef.current;
    if (actorPlayback) {
      const position = clampSeconds(sourceTime - actorPlayback.sourceStartSec, 0, actorPlayback.durationSec);
      const nextCurrent = roundSeconds(actorPlayback.virtualStartSec + position);
      setActorAudios((items) => items.map((item) => item.id === actorPlayback.actorId ? { ...item, currentTimeSec: nextCurrent, isPlaying: true } : { ...item, isPlaying: false }));
      if (sourceTime >= roundSeconds(actorPlayback.sourceEndSec) - 0.025) {
        element.pause();
        activeActorPlaybackRef.current = null;
        setActorAudios((items) => items.map((item) => item.id === actorPlayback.actorId ? { ...item, currentTimeSec: roundSeconds(actorPlayback.virtualStartSec + actorPlayback.durationSec), isPlaying: false } : item));
      }
      return;
    }

    const preview = phrasePreviewRef.current;
    if (preview) {
      const position = clampSeconds(sourceTime - preview.startSec, 0, preview.durationSec);
      setPhrasePreview({ clipId: preview.clipId, positionSec: position, isPlaying: true });
      if (sourceTime >= roundSeconds(preview.endSec) - 0.025) {
        element.pause();
        phrasePreviewRef.current = null;
        setPhrasePreview({ clipId: preview.clipId, positionSec: preview.durationSec, isPlaying: false });
      }
      return;
    }

    const mainPlayback = activeMainPlaybackRef.current;
    if (mainPlayback?.mode === "selected_block") {
      const position = clampSeconds(sourceTime - mainPlayback.sourceStartSec, 0, mainPlayback.durationSec);
      setCurrentTimeSec(roundSeconds(mainPlayback.virtualStartSec + position));

      if (sourceTime >= roundSeconds(mainPlayback.sourceEndSec) - 0.025) {
        stopSelectedBlockPlaybackAtEnd("timeupdate_end");
      }
      return;
    }

    if (!safeBlocks.length) return;

    const index = currentBlockIndexRef.current;
    const block = safeBlocks[index] || safeBlocks[0];
    const blockVirtualStart = getBlockVirtualStart(safeBlocks, index);
    const virtualTime = roundSeconds(blockVirtualStart + Math.max(0, sourceTime - roundSeconds(block.source_start_sec)));

    if (sourceTime >= roundSeconds(block.source_end_sec) - 0.025) {
      setCurrentTimeSec(roundSeconds(blockVirtualStart + getBlockDuration(block)));
      if (mainPlayback?.mode === "sequence") {
        playNextSequenceBlock(index, index + 1);
      } else {
        element.pause();
        stopMainPlaybackGuard();
        activeMainPlaybackRef.current = null;
        setIsPlaying(false);
      }
      return;
    }

    setCurrentTimeSec(clampSeconds(virtualTime, 0, getTimelineDuration(safeBlocks)));
  };

  const applyActorAudioPatch = (actorId, patcher) => {
    setActorAudios((items) => items.map((item) => {
      if (item.id !== actorId) return item;
      return typeof patcher === "function" ? patcher(item) : { ...item, ...(patcher || {}) };
    }));
  };

  const addActorAudioFiles = (event) => {
    const files = Array.from(event.target.files || []);
    if (!files.length) return;
    files.forEach((file, fileIndex) => {
      const id = createId("actor_audio");
      const url = URL.createObjectURL(file);
      const label = inferActorLabelFromFilename(file.name);
      const color = COLOR_SWATCHES[(actorAudios.length + fileIndex + 1) % COLOR_SWATCHES.length];
      const sourceName = file.name || `actor_${fileIndex + 1}.mp3`;
      const baseActor = {
        id,
        url,
        name: sourceName,
        filename: sourceName,
        label,
        color,
        duration_sec: 0,
        blocks: [],
        selectedBlockId: "",
        currentTimeSec: 0,
        isPlaying: false,
        microStepSec: DEFAULT_MICRO_STEP_SEC,
      };
      void putActorAudioBlob(getActorAudioBlobKey(sourceNodeId, id), file).catch(() => {
        setMessage(`Аудио “${sourceName}” добавлено, но браузер не смог сохранить его для восстановления после F5.`);
      });
      setActorAudios((items) => [...items, baseActor]);

      const probe = new Audio(url);
      probe.preload = "metadata";
      probe.onloadedmetadata = () => {
        const duration = roundSeconds(probe.duration || 0);
        const initialBlock = duration > 0 ? createExternalAudioBlock({
          sourceId: id,
          sourceUrl: url,
          sourceName,
          label,
          color,
          startSec: 0,
          endSec: duration,
          colorIndex: fileIndex + 1,
        }) : null;
        setActorAudios((items) => items.map((item) => item.id === id ? {
          ...item,
          duration_sec: duration,
          blocks: initialBlock ? [initialBlock] : [],
          selectedBlockId: initialBlock?.id || "",
        } : item));
      };
      probe.onerror = () => setMessage(`Не удалось прочитать длительность аудио “${sourceName}”.`);
    });
    event.target.value = "";
    setMessage("Добавлено аудио актёра. В этом блоке можно только резать, слушать и сохранять фразы в общий список.");
  };

  const getActorPhraseDependencies = (actorId) => {
    const safeActorId = String(actorId || "").trim();
    if (!safeActorId) return [];
    const byKey = new Map();
    const remember = (phrase = {}, reason = "") => {
      const id = String(phrase.id || phrase.saved_clip_id || phrase.inserted_phrase_id || phrase.blockId || phrase.label || reason || "").trim();
      if (!id) return;
      const label = String(phrase.label || phrase.saved_clip_label || phrase.inserted_phrase_label || phrase.phrase_label || phrase.block_label || id).trim() || id;
      const existing = byKey.get(id) || { id, label, reasons: new Set(), savedClipId: String(phrase.savedClipId || phrase.id || phrase.saved_clip_id || "").trim(), blockId: String(phrase.blockId || "").trim() };
      if (reason) existing.reasons.add(reason);
      if (phrase.savedClipId || phrase.id || phrase.saved_clip_id) existing.savedClipId = String(phrase.savedClipId || phrase.id || phrase.saved_clip_id || "").trim();
      if (phrase.blockId) existing.blockId = String(phrase.blockId || "").trim();
      byKey.set(id, existing);
    };

    (Array.isArray(savedClips) ? savedClips : []).forEach((clip) => {
      if (String(clip?.source_audio_id || "").trim() === safeActorId) remember(clip, "savedClip.source_audio_id");
    });

    (Array.isArray(blocks) ? blocks : []).forEach((block) => {
      const blockSourceId = String(block?.source_audio_id || "").trim();
      const linkedClip = findSavedClipForBlock(block, savedClips);
      const linkedClipFromActor = linkedClip && String(linkedClip?.source_audio_id || "").trim() === safeActorId;
      if (blockSourceId === safeActorId) {
        remember({ ...block, id: block?.saved_clip_id || block?.inserted_phrase_id || block?.id, blockId: block?.id, savedClipId: block?.saved_clip_id }, "block.source_audio_id");
      }
      if (linkedClipFromActor) {
        remember({ ...linkedClip, blockId: block?.id, savedClipId: linkedClip?.id }, "block.saved_clip_id");
      }
    });

    return [...byKey.values()].map((item) => ({ ...item, reasons: [...item.reasons] }));
  };

  const deleteActorAudioNow = (actorId) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    if (!actor) return;
    if (activeActorPlaybackRef.current?.actorId === actorId) stopActorPlayback({ pause: true });
    void deleteActorAudioBlob(getActorAudioBlobKey(sourceNodeId, actorId));
    setActorAudios((items) => items.filter((item) => item.id !== actorId));
    setDeleteActorDialog(null);
    setMessage(`Дополнительное аудио “${actor.name}” удалено.`);
  };

  const deleteActorAudio = (actorId) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    if (!actor) return;
    const dependentPhrases = getActorPhraseDependencies(actorId);
    if (dependentPhrases.length) {
      console.warn("[PODCAST ACTOR DELETE BLOCKED_USED_BY_PHRASES]", {
        actorId,
        actorName: actor.name || actor.label || actorId,
        dependentPhraseCount: dependentPhrases.length,
        dependentPhrases,
      });
      setDeleteActorDialog({ actorId, actorName: actor.name || actor.label || actorId, dependentPhrases, busy: false });
      setMessage("Это аудио используется во вставленных фразах. Если удалить его, финальная сборка не сможет собрать подкаст.");
      return;
    }
    deleteActorAudioNow(actorId);
  };

  const selectActorBlock = (actorId, blockId, startSec) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    if (!actor) return;
    applyActorAudioPatch(actorId, { selectedBlockId: blockId, currentTimeSec: startSec });
    const block = actor.blocks.find((item) => item.id === blockId);
    if (block) prepareAudioElement(actor.url, block.source_start_sec);
  };

  const seekActorTimeline = (actorId, timeSec) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    if (!actor) return;
    const total = getTimelineDuration(actor.blocks) || actor.duration_sec;
    const nextTime = clampSeconds(timeSec, 0, total);
    const position = findTimelinePosition(actor.blocks, nextTime);
    applyActorAudioPatch(actorId, { currentTimeSec: nextTime });
    if (position) prepareAudioElement(actor.url, position.source_time_sec);
  };

  const toggleActorPlayback = async (actorId) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    const element = audioRef.current;
    if (!actor || !element || !actor.blocks.length) return;
    if (activeActorPlaybackRef.current?.actorId === actorId && actor.isPlaying) {
      stopActorPlayback({ pause: true });
      return;
    }
    stopPhrasePreview({ pause: false });
    stopSilencePlayback();
    setIsPlaying(false);

    const selectedIndex = Math.max(0, actor.blocks.findIndex((block) => block.id === actor.selectedBlockId));
    const block = actor.blocks[selectedIndex] || actor.blocks[0];
    const virtualStart = getBlockVirtualStart(actor.blocks, selectedIndex);
    const duration = getBlockDuration(block);
    const virtualEnd = roundSeconds(virtualStart + duration);
    const currentInsideSelected = actor.currentTimeSec > virtualStart + 0.025 && actor.currentTimeSec < virtualEnd - 0.025;
    const resumeOffset = currentInsideSelected ? clampSeconds(actor.currentTimeSec - virtualStart, 0, duration) : 0;
    const ready = await prepareAudioElement(actor.url, roundSeconds(roundSeconds(block.source_start_sec) + resumeOffset));
    if (!ready) return;
    activeActorPlaybackRef.current = {
      actorId,
      blockId: block.id,
      sourceStartSec: roundSeconds(block.source_start_sec),
      sourceEndSec: roundSeconds(block.source_end_sec),
      virtualStartSec: virtualStart,
      durationSec: duration,
    };
    setActorAudios((items) => items.map((item) => item.id === actorId ? { ...item, selectedBlockId: block.id, currentTimeSec: roundSeconds(virtualStart + resumeOffset), isPlaying: true } : { ...item, isPlaying: false }));
    try {
      await element.play();
    } catch {
      stopActorPlayback({ pause: false });
    }
  };

  const splitActorAudioBlock = (actorId) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    if (!actor || !actor.blocks.length) return;
    const splitTime = clampSeconds(actor.currentTimeSec, 0, getTimelineDuration(actor.blocks));
    const result = splitBlockAtTime(actor.blocks, splitTime, actor.selectedBlockId);
    if (!result) {
      setMessage("В аудио актёра нельзя резать в самом начале или конце блока. Поставь прицел внутрь блока.");
      return;
    }
    applyActorAudioPatch(actorId, {
      blocks: result.blocks,
      selectedBlockId: result.selectedBlockId,
      currentTimeSec: result.selectedBlockStart,
    });
    prepareAudioElement(actor.url, result.blocks.find((block) => block.id === result.selectedBlockId)?.source_start_sec || 0);
    setMessage(`Аудио актёра “${actor.label}” разрезано. Выбран левый блок.`);
  };

  const adjustActorAudioBlockEnd = (actorId, direction) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    if (!actor) return;
    const selectedIndex = getSelectedBlockIndex(actor.blocks, actor.selectedBlockId);
    if (selectedIndex < 0) return;
    const step = clampSeconds(actor.microStepSec || DEFAULT_MICRO_STEP_SEC, 0.001, 30);
    const result = resizeSelectedBlockEnd(actor.blocks, selectedIndex, direction * step, actor.duration_sec || getTimelineDuration(actor.blocks));
    if (!result?.moved) {
      setMessage("Доводчик аудио актёра упёрся в начало/конец доступного блока.");
      return;
    }
    applyActorAudioPatch(actorId, {
      blocks: result.blocks,
      selectedBlockId: result.blocks[selectedIndex]?.id || actor.selectedBlockId,
    });
    setMessage(`Подправлен конец выбранного блока актёра “${actor.label}”.`);
  };

  const extractPhraseToServerAsset = async ({ sourceAudioUrl, sourceStartSec, sourceEndSec, durationSec, label, sourceNodeId: nodeId }) => {
    const response = await fetch(`${API_BASE}/api/podcast-audio/extract-phrase-to-asset`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sourceAudioUrl,
        sourceStartSec: roundSeconds(sourceStartSec),
        sourceEndSec: roundSeconds(sourceEndSec),
        durationSec: roundSeconds(durationSec),
        label,
        sourceNodeId: nodeId || sourceNodeId,
      }),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok || data?.ok === false) {
      throw new Error(String(data?.message || data?.detail || data?.code || `extract_failed:${response.status}`));
    }
    const url = String(data?.url || data?.assetUrl || data?.asset_url || data?.server_url || data?.publicUrl || data?.path || "").trim();
    if (!url) throw new Error("backend не вернул URL сохранённой фразы");
    return {
      ...data,
      url,
      assetUrl: String(data?.assetUrl || data?.asset_url || url).trim() || url,
      asset_url: String(data?.asset_url || data?.assetUrl || url).trim() || url,
      server_url: String(data?.server_url || url).trim() || url,
      publicUrl: String(data?.publicUrl || data?.public_url || url).trim() || url,
      duration_sec: roundSeconds(data?.duration_sec || data?.durationSec || durationSec),
      source: "podcast_saved_phrase",
    };
  };

  const makeIndependentSavedClip = async ({ clip, actor, block, label }) => {
    const actorWithServerAsset = await uploadActorSourceAudioForServer(actor);
    const sourceAudioUrl = getActorServerSourceUrl(actorWithServerAsset);
    if (!sourceAudioUrl) throw new Error(`Не удалось сохранить исходное аудио актёра “${actor?.name || actor?.label || actor?.id}” на сервере.`);

    setActorAudios((items) => items.map((item) => item.id === actorWithServerAsset.id ? { ...item, ...actorWithServerAsset, isPlaying: false } : item));

    const duration = roundSeconds(clip?.duration_sec || getBlockDuration(block) || (roundSeconds(block?.source_end_sec) - roundSeconds(block?.source_start_sec)));
    const sourceStartSec = roundSeconds(clip?.source_start_sec ?? block?.source_start_sec);
    const sourceEndSec = roundSeconds(clip?.source_end_sec ?? block?.source_end_sec ?? (sourceStartSec + duration));
    const asset = await extractPhraseToServerAsset({
      sourceAudioUrl,
      sourceStartSec,
      sourceEndSec,
      durationSec: duration,
      label: label || clip?.label,
      sourceNodeId,
    });
    const clipId = String(clip?.id || createId("saved_clip"));
    const sourceUrl = asset.url;
    console.log("[PODCAST SAVED PHRASE ASSET_CREATED]", {
      savedClipId: clipId,
      label: label || clip?.label || clipId,
      sourceUrl,
      durationSec: asset.duration_sec || duration,
    });
    return {
      ...(clip || {}),
      id: clipId,
      label: label || clip?.label || "Фраза",
      type: "audio",
      source: "podcast_saved_phrase",
      original_source_audio_id: clip?.original_source_audio_id || actor?.id || clip?.source_audio_id || "",
      source_audio_id: clipId,
      source_url: sourceUrl,
      asset_url: asset.asset_url || sourceUrl,
      assetUrl: asset.assetUrl || sourceUrl,
      server_url: asset.server_url || sourceUrl,
      publicUrl: asset.publicUrl || sourceUrl,
      source_name: asset.filename || clip?.source_name || actor?.name || label || clipId,
      filename: asset.filename || clip?.filename || undefined,
      source_start_sec: 0,
      source_end_sec: roundSeconds(asset.duration_sec || duration),
      duration_sec: roundSeconds(asset.duration_sec || duration),
      mime_type: asset.mime_type || "audio/mpeg",
    };
  };

  const saveActorSelectedClip = async (actorId) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    if (!actor) return;
    const selectedIndex = getSelectedBlockIndex(actor.blocks, actor.selectedBlockId);
    if (selectedIndex < 0) {
      setMessage("Сначала выбери блок в аудио актёра.");
      return;
    }
    const block = actor.blocks[selectedIndex];
    const duration = getBlockDuration(block);
    if (duration <= 0) return;
    const countForActor = savedClips.filter((clip) => String(clip.original_source_audio_id || clip.source_audio_id || "") === actorId || String(clip.label || "").startsWith(actor.label)).length + 1;
    const label = `${compactBlockBadge(actor.label || actor.name || "АКТ") || "АКТ"}${countForActor}`;
    const baseClip = {
      id: createId("saved_clip"),
      label,
      type: "audio",
      source_audio_id: actorId,
      source_url: actor.url,
      source_name: actor.name,
      source_start_sec: roundSeconds(block.source_start_sec),
      source_end_sec: roundSeconds(block.source_end_sec),
      duration_sec: duration,
      color_index: Number.isInteger(block.color_index) ? block.color_index : 1,
      color: actor.color || block.color,
      source_label: actor.label,
      created_at: Date.now(),
    };
    try {
      setMessage(`Сохраняю фразу актёра “${label}” как независимый серверный файл...`);
      const clip = await makeIndependentSavedClip({ clip: baseClip, actor, block, label });
      setSavedClips((items) => [...items, clip]);
      setMessage(`Сохранена независимая фраза актёра “${clip.label}” (${formatTimer(clip.duration_sec)}). Теперь исходное аудио актёра можно удалить.`);
    } catch (error) {
      console.error("[PODCAST SAVED PHRASE ASSET_CREATE_FAILED]", error);
      setMessage(`Не удалось сохранить фразу актёра на сервере: ${error?.message || error}`);
    }
  };

  const saveActorDependentPhrasesAsIndependent = async (actorId) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    if (!actor) return;
    const dependencies = getActorPhraseDependencies(actorId);
    const dependencyIds = new Set(dependencies.flatMap((dep) => [dep.id, dep.savedClipId]).map((value) => String(value || "").trim()).filter(Boolean));
    const clipsToConvert = (Array.isArray(savedClips) ? savedClips : []).filter((clip) => (
      String(clip?.source_audio_id || "").trim() === String(actorId)
      || dependencyIds.has(String(clip?.id || "").trim())
      || dependencyIds.has(String(clip?.saved_clip_id || "").trim())
      || dependencyIds.has(String(clip?.inserted_phrase_id || "").trim())
    ));

    const directBlocksToConvert = (Array.isArray(blocks) ? blocks : []).filter((block) => (
      String(block?.source_audio_id || "").trim() === String(actorId)
      && !findSavedClipForBlock(block, clipsToConvert)
    ));

    if (!clipsToConvert.length && !directBlocksToConvert.length) {
      setDeleteActorDialog((dialog) => dialog ? { ...dialog, busy: false } : dialog);
      setMessage("Не нашёл сохранённые фразы для независимого сохранения.");
      return;
    }

    setDeleteActorDialog((dialog) => dialog ? { ...dialog, busy: true } : dialog);
    try {
      const convertedById = new Map();
      const convertedByBlockId = new Map();
      for (const clip of clipsToConvert) {
        const converted = await makeIndependentSavedClip({ clip, actor, block: clip, label: clip.label });
        convertedById.set(String(clip.id || ""), converted);
      }
      for (const block of directBlocksToConvert) {
        const label = String(block?.saved_clip_label || block?.inserted_phrase_label || block?.phrase_label || block?.block_label || actor.label || "Фраза").trim();
        const baseClip = {
          id: block?.saved_clip_id || block?.inserted_phrase_id || createId("saved_clip"),
          label,
          type: "audio",
          source_audio_id: actorId,
          source_url: actor.url,
          source_name: actor.name,
          source_start_sec: roundSeconds(block?.source_start_sec),
          source_end_sec: roundSeconds(block?.source_end_sec),
          duration_sec: getBlockDuration(block),
          color_index: block?.color_index,
          color: block?.color || actor.color,
          source_label: actor.label,
          created_at: Date.now(),
        };
        const converted = await makeIndependentSavedClip({ clip: baseClip, actor, block, label });
        convertedByBlockId.set(String(block?.id || ""), converted);
        convertedById.set(String(baseClip.id || ""), converted);
      }

      setSavedClips((items) => {
        const convertedDirect = [...convertedByBlockId.values()].filter((clip) => !(items || []).some((item) => String(item?.id || "") === String(clip?.id || "")));
        return [...items.map((clip) => convertedById.get(String(clip.id || "")) || clip), ...convertedDirect];
      });
      setBlocks((items) => items.map((block) => {
        const linkedClip = findSavedClipForBlock(block, clipsToConvert);
        const converted = (linkedClip ? convertedById.get(String(linkedClip.id || "")) : null) || convertedByBlockId.get(String(block?.id || ""));
        if (!converted && String(block?.source_audio_id || "").trim() !== String(actorId)) return block;
        const sourceUrl = converted?.source_url || converted?.asset_url || converted?.server_url || "";
        const duration = roundSeconds(converted?.duration_sec || getBlockDuration(block));
        return {
          ...block,
          type: converted ? "phrase" : block.type,
          block_kind: converted ? "phrase" : block.block_kind,
          source_kind: converted ? "inserted_audio" : block.source_kind,
          block_type: converted ? "inserted_audio" : block.block_type,
          source_audio_id: converted?.id || block.source_audio_id,
          source_url: sourceUrl || block.source_url,
          audio_url: sourceUrl || block.audio_url,
          fragment_url: sourceUrl || block.fragment_url,
          asset_url: sourceUrl || block.asset_url,
          assetUrl: sourceUrl || block.assetUrl,
          server_url: sourceUrl || block.server_url,
          publicUrl: sourceUrl || block.publicUrl,
          src: sourceUrl || block.src,
          source_start_sec: converted ? 0 : block.source_start_sec,
          source_end_sec: converted ? duration : block.source_end_sec,
          duration_sec: duration || block.duration_sec,
          saved_clip_id: converted?.id || block.saved_clip_id,
          inserted_phrase_id: converted?.id || block.inserted_phrase_id,
          saved_clip_label: converted?.label || block.saved_clip_label,
          inserted_phrase_label: converted?.label || block.inserted_phrase_label,
          phrase_label: converted?.label || block.phrase_label,
          block_label: converted?.label || block.block_label,
        };
      }));

      setDeleteActorDialog(null);
      setMessage(`Зависимые фразы сохранены как независимые файлы. Теперь можно удалить аудио “${actor.name}”.`);
    } catch (error) {
      console.error("[PODCAST DEPENDENT PHRASES ASSET_CREATE_FAILED]", error);
      setDeleteActorDialog((dialog) => dialog ? { ...dialog, busy: false } : dialog);
      setMessage(`Не удалось сохранить зависимые фразы: ${error?.message || error}`);
    }
  };

  const splitCurrentBlock = () => {
    stopMainPlayback();
    if (!blocks.length || totalDurationSec <= 0) return;

    // ВАЖНО: режем именно по зелёному бегунку/currentTimeSec.
    // В v16 здесь ошибочно резалось по правому краю выбранного блока,
    // поэтому первый полный блок не делился вообще.
    const splitTime = clampSeconds(currentTimeSec, 0, totalDurationSec);
    const result = splitBlockAtTime(blocks, splitTime, selectedBlockId);

    if (!result) {
      setMessage("Нельзя разрезать в самом начале или в самом конце блока. Передвинь жёлтый прицел внутрь блока и нажми “резать”.");
      return;
    }

    pushHistory("split_block");
    setBlocks(result.blocks);
    setSelectedBlockId(result.selectedBlockId);
    seekOnTimeline(result.selectedBlockStart, result.blocks);
    setMessage("Разрезано по жёлтому прицелу. Левая часть выбрана автоматически, плеер поставлен в начало выбранного блока.");
  };

  const adjustSelectedRightEdge = (direction) => {
    const selectedIndex = getSelectedBlockIndex(blocks, selectedBlockId);
    if (selectedIndex < 0) {
      setMessage("Сначала выбери блок.");
      return;
    }

    const step = clampSeconds(microStepSec, 0.001, 30);
    const result = resizeSelectedBlockEnd(blocks, selectedIndex, direction * step, durationSec || audio.duration_sec || totalDurationSec);

    if (!result || !result.blocks) {
      setMessage("Конец выбранного блока нельзя сдвинуть.");
      return;
    }

    if (!result.moved) {
      setMessage(direction < 0
        ? "Выбранный блок уже почти нулевой, дальше уменьшать нельзя."
        : "Справа больше нет остатка, выбранный блок уже дошёл до конца аудио."
      );
      return;
    }

    pushHistory("resize_selected_block_end");
    setBlocks(result.blocks);
    const nextSelectedBlockId = result.blocks[selectedIndex]?.id || selectedBlockId;
    setSelectedBlockId(nextSelectedBlockId);

    if (activeMainPlaybackRef.current?.blockId === selectedBlockId) {
      const updatedIndex = result.blocks.findIndex((block) => block.id === selectedBlockId);
      const updatedBlock = result.blocks[updatedIndex];
      if (updatedBlock) {
        const sourceEndSec = roundSeconds(updatedBlock.source_end_sec);
        const durationSec = getBlockDuration(updatedBlock);
        activeMainPlaybackRef.current = {
          ...activeMainPlaybackRef.current,
          blockIndex: updatedIndex,
          sourceEndSec,
          durationSec,
          virtualStartSec: getBlockVirtualStart(result.blocks, updatedIndex),
        };
        if (audioRef.current && roundSeconds(audioRef.current.currentTime) >= sourceEndSec - 0.015) {
          stopSelectedBlockPlaybackAtEnd("boundary_nudged_past_end");
        }
      }
    }

    // Как в Manual Timing: меняем конечную границу выбранного блока.
    // Прицел реза/currentTimeSec не двигаем. Он нужен только для кнопки “резать”.
    const arrow = direction < 0 ? "←" : "→";
    setMessage(`${arrow} Конец выбранного блока сдвинут на ${formatTimer(Math.abs(result.appliedDelta))}. Выбранный блок: ${formatTimer(result.selectedDurationAfter)}, правый остаток: ${formatTimer(result.rightDurationAfter)}.`);
  };

  const deleteSelectedBlock = () => {
    stopMainPlayback();
    if (!selectedBlockId) {
      setMessage("Сначала выбери блок.");
      return;
    }
    const result = deleteBlockAndMark(blocks, selectedBlockId, deletionMarkers);
    if (!result) return;
    pushHistory("delete_block");
    setBlocks(result.blocks);
    setDeletionMarkers(result.deletionMarkers);
    const nextSelected = result.blocks[0]?.id || "";
    setSelectedBlockId(nextSelected);
    setBlockMenu(null);
    setSaveClipDialog(null);
    seekOnTimeline(result.nextTimeSec, result.blocks);
    setMessage(`Блок удалён (${formatTimer(result.removedDuration)}). На дорожке остался маячок удаления. Вернуть можно кнопкой “Назад”.`);
  };

  const mergeSelectedWithNext = () => {
    if (!selectedBlockId) return;
    const result = mergeWithNext(blocks, selectedBlockId);
    if (!result) {
      setMessage("Склеить можно только со следующим соседним блоком, если это непрерывный кусок одного аудио или тишины.");
      return;
    }
    pushHistory("merge_next");
    setBlocks(result.blocks);
    setSelectedBlockId(result.selectedBlockId);
    seekOnTimeline(result.selectedBlockStart, result.blocks);
    setBlockMenu(null);
    setMessage("Выбранный блок склеен со следующим.");
  };

  const undoLastAction = () => {
    const last = history[history.length - 1];
    if (!last) {
      setMessage("Нет действия для отмены.");
      return;
    }
    const restoredBlocks = normalizeBlocks(last.blocks, durationSec || audio.duration_sec);
    setBlocks(restoredBlocks);
    setSelectedBlockId(last.selectedBlockId || restoredBlocks[0]?.id || "");
    setDeletionMarkers(Array.isArray(last.deletionMarkers) ? last.deletionMarkers : []);
    setHistory((items) => items.slice(0, -1));
    seekOnTimeline(last.currentTimeSec, restoredBlocks);
    setMessage("Последнее действие отменено.");
  };

  const clearAll = () => {
    const baseDuration = durationSec || audio.duration_sec;
    const initialBlocks = createInitialBlocks(baseDuration);
    stopSilencePlayback();
    stopPhrasePreview({ pause: false });
    if (audioRef.current) audioRef.current.pause();
    setIsPlaying(false);
    setBlocks(initialBlocks);
    setSelectedBlockId(initialBlocks[0]?.id || "");
    setDeletionMarkers([]);
    setSavedClips([]);
    actorAudios.forEach((actor) => void deleteActorAudioBlob(getActorAudioBlobKey(sourceNodeId, actor.id)));
    setActorAudios([]);
    activeActorPlaybackRef.current = null;
    setBlockMenu(null);
    setSaveClipDialog(null);
    setHistory([]);
    setCurrentTimeSec(0);
    if (audioRef.current) audioRef.current.currentTime = 0;
    removeComposerStorage(sourceNodeId);
    setMessage("Монтаж очищен. Дорожка снова одна сплошная от 0 до конца.");
  };


  const copyGuideJsonSample = async () => {
    const sample = getGuideJsonSampleText();
    try {
      await navigator.clipboard.writeText(sample);
      setMessage("Образец JSON скопирован. Он размечает только диктора и персонажей; тишину ставишь вручную.");
    } catch {
      setGuideJsonDialog({ value: sample });
      setMessage("Не удалось скопировать автоматически — открыл поле с образцом JSON.");
    }
  };

  const openGuideJsonDialog = () => {
    setGuideJsonDialog({ value: getGuideJsonSampleText() });
    setBlockMenu(null);
    setSaveClipDialog(null);
    setMessage("Вставь JSON-план: роли + примерные блоки. Он нужен только для удобной сборки внутри композера.");
  };

  const applyGuideJson = () => {
    const rawValue = String(guideJsonDialog?.value || "").trim();
    if (!rawValue) {
      setMessage("Поле JSON пустое.");
      return;
    }
    let nextBlocks = null;
    try {
      nextBlocks = buildBlocksFromGuideJson(rawValue, durationSec || audio.duration_sec || totalDurationSec);
    } catch (error) {
      setMessage(`JSON не применён: ${error?.message || "ошибка формата"}.`);
      return;
    }
    if (!nextBlocks?.length) {
      setMessage("JSON прочитан, но блоки не найдены. Нужен массив blocks/guideBlocks/segments с t0/t1 и role.");
      return;
    }
    pushHistory("apply_guide_json");
    stopPhrasePreview({ pause: true });
    stopMainPlayback();
    setBlocks(nextBlocks);
    setSelectedBlockId(nextBlocks[0]?.id || "");
    setDeletionMarkers([]);
    setGuideJsonDialog(null);
    seekOnTimeline(0, nextBlocks);
    setMessage(`JSON-план применён: создано ${nextBlocks.length} блоков-подсказок. Это только разметка композера, в Timing уйдёт потом готовое аудио без JSON.`);
  };



  const renderComposerAudioBlob = async () => {
    const safeBlocks = Array.isArray(blocks) && blocks.length ? blocks : createInitialBlocks(durationSec || audio.duration_sec || totalDurationSec);
    const renderDurationSec = getTimelineDuration(safeBlocks);
    if (!safeBlocks.length || renderDurationSec <= 0) {
      throw new Error("Нет блоков для сборки аудио.");
    }

    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) {
      throw new Error("Браузер не поддерживает Web Audio API.");
    }

    const audioContext = new AudioContextClass();
    const decodedCache = new Map();

    try {
      const decodeForUrl = async (rawUrl) => {
        const url = normalizeBrowserAudioUrl(rawUrl);
        if (!url) throw new Error("Пустой URL аудио-фрагмента.");
        if (decodedCache.has(url)) return decodedCache.get(url);
        const response = await fetch(url, { credentials: "include" });
        if (!response.ok) throw new Error(`Не удалось загрузить аудио-фрагмент: HTTP ${response.status}`);
        const arrayBuffer = await response.arrayBuffer();
        const decoded = await audioContext.decodeAudioData(arrayBuffer.slice(0));
        decodedCache.set(url, decoded);
        return decoded;
      };

      let outputSampleRate = 44100;
      for (const block of safeBlocks) {
        if (block?.type === "silence") continue;
        const sourceUrl = getItemSourceUrl(block, audio, actorAudios);
        if (!sourceUrl) continue;
        const decoded = await decodeForUrl(sourceUrl);
        if (decoded?.sampleRate) {
          outputSampleRate = decoded.sampleRate;
          break;
        }
      }

      const outputFrames = Math.max(1, Math.ceil(renderDurationSec * outputSampleRate));
      const channelCount = 2;
      const outChannels = Array.from({ length: channelCount }, () => new Float32Array(outputFrames));
      let outputCursor = 0;
      const fadeFrames = Math.max(1, Math.round(outputSampleRate * 0.008));

      for (const block of safeBlocks) {
        const blockDurationSec = getBlockDuration(block);
        const blockFrames = Math.max(1, Math.round(blockDurationSec * outputSampleRate));
        if (block?.type === "silence") {
          outputCursor += blockFrames;
          continue;
        }

        const sourceUrl = getItemSourceUrl(block, audio, actorAudios);
        if (!sourceUrl) {
          outputCursor += blockFrames;
          continue;
        }
        const decoded = await decodeForUrl(sourceUrl);
        const sourceStartFrame = Math.max(0, Math.round(roundSeconds(block.source_start_sec) * decoded.sampleRate));
        const sourceEndFrame = Math.min(decoded.length, Math.max(sourceStartFrame, Math.round(roundSeconds(block.source_end_sec) * decoded.sampleRate)));
        const availableSourceFrames = Math.max(0, sourceEndFrame - sourceStartFrame);
        const framesToCopy = Math.min(blockFrames, Math.round(availableSourceFrames * outputSampleRate / decoded.sampleRate), outputFrames - outputCursor);

        for (let channel = 0; channel < channelCount; channel += 1) {
          const sourceData = decoded.getChannelData(Math.min(channel, decoded.numberOfChannels - 1));
          const targetData = outChannels[channel];
          for (let frame = 0; frame < framesToCopy; frame += 1) {
            const sourceFrame = Math.min(decoded.length - 1, sourceStartFrame + Math.floor(frame * decoded.sampleRate / outputSampleRate));
            let gain = 1;
            if (frame < fadeFrames && outputCursor > 0) gain = Math.min(gain, frame / fadeFrames);
            if (frame > framesToCopy - fadeFrames) gain = Math.min(gain, Math.max(0, (framesToCopy - frame) / fadeFrames));
            targetData[outputCursor + frame] = (sourceData[sourceFrame] || 0) * gain;
          }
        }
        outputCursor += blockFrames;
      }

      const usedFrames = Math.min(outputFrames, Math.max(1, outputCursor));
      const trimmedChannels = outChannels.map((channel) => channel.slice(0, usedFrames));
      return {
        blob: encodeWavFromFloatChannels(trimmedChannels, outputSampleRate),
        durationSec: roundSeconds(usedFrames / outputSampleRate),
        filename: sanitizeAudioDownloadName(audio.filename || "podcast_audio"),
      };
    } finally {
      try { await audioContext.close(); } catch {}
    }
  };

  const uploadFinalAudioBlob = async ({ blob, filename, mimeType = "" }) => {
    const sizeBytes = Number(blob?.size || 0);
    if (sizeBytes > ASSET_UPLOAD_SOFT_LIMIT_BYTES) {
      throw new Error("Аудио ещё не сохранено на сервере / слишком большое для upload");
    }

    const form = new FormData();
    form.append("file", new File([blob], filename || "podcast_composer.wav", { type: mimeType || blob?.type || "audio/wav" }));
    const response = await fetch(`${API_BASE}/api/assets/upload`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = String(data?.detail || data?.message || `upload_failed:${response.status}`);
      if (response.status === 413 || detail === "file_too_large") {
        throw new Error("Аудио ещё не сохранено на сервере / слишком большое для upload");
      }
      throw new Error(detail);
    }
    return data || {};
  };

  const uploadActorSourceAudioForServer = async (actor = {}) => {
    const existingUrl = String(actor?.url || actor?.asset_url || actor?.assetUrl || actor?.public_url || actor?.publicUrl || "").trim();
    if (isBackendStaticAssetUrl(existingUrl)) return { ...actor, url: existingUrl, asset_url: existingUrl, server_url: existingUrl, publicUrl: existingUrl };

    const actorId = String(actor?.id || "").trim();
    let blob = null;
    try {
      blob = actorId ? await getActorAudioBlob(getActorAudioBlobKey(sourceNodeId, actorId)) : null;
    } catch {}
    if (!blob && existingUrl && existingUrl.startsWith("blob:")) {
      try {
        blob = await fetch(existingUrl).then((response) => response.ok ? response.blob() : null);
      } catch {}
    }
    if (!blob) return { ...actor };

    const filename = String(actor?.filename || actor?.name || `${actorId || "actor_audio"}.mp3`).trim();
    const uploaded = await uploadFinalAudioBlob({ blob, filename, mimeType: blob.type || inferAudioMimeTypeFromFilename(filename) });
    const assetUrl = String(uploaded.url || uploaded.assetUrl || uploaded.asset_url || uploaded.publicUrl || uploaded.path || "").trim();
    if (!assetUrl) return { ...actor };
    return {
      ...actor,
      url: assetUrl,
      asset_url: assetUrl,
      server_url: assetUrl,
      publicUrl: assetUrl,
      filename: String(uploaded.filename || uploaded.name || filename).trim(),
      duration_sec: roundSeconds(uploaded.duration_sec || uploaded.durationSec || actor?.duration_sec || actor?.durationSec || 0),
    };
  };

  const prepareServerRenderableAudioSources = async () => {
    const nextActorAudios = [];
    for (const actor of Array.isArray(actorAudios) ? actorAudios : []) {
      nextActorAudios.push(await uploadActorSourceAudioForServer(actor));
    }

    const legacySourceUrlByActorId = new Map();
    let nextSavedClips = (Array.isArray(savedClips) ? savedClips : []).map((clip) => ({ ...clip }));
    let nextBlocks = (Array.isArray(blocks) ? blocks : []).map((block) => ({ ...block }));

    const actorById = new Map(nextActorAudios.map((actor) => [String(actor?.id || "").trim(), actor]).filter(([id]) => Boolean(id)));
    nextActorAudios.forEach((actor) => {
      const actorId = String(actor?.id || "").trim();
      const actorUrl = getActorServerSourceUrl(actor);
      if (!actorId || !actorUrl) return;
      nextSavedClips = nextSavedClips.map((clip) => String(clip?.source_audio_id || "").trim() === actorId ? {
        ...clip,
        source_url: actorUrl,
        asset_url: actorUrl,
        server_url: actorUrl,
        publicUrl: actorUrl,
      } : clip);
      nextBlocks = nextBlocks.map((block) => String(block?.source_audio_id || "").trim() === actorId ? {
        ...block,
        source_url: actorUrl,
        asset_url: actorUrl,
        server_url: actorUrl,
        publicUrl: actorUrl,
      } : block);
    });

    const rememberRecoveredSource = ({ legacySourceAudioId = "", sourceUrl = "", recoveredFrom = "", savedClip = null, block = null } = {}) => {
      const legacyId = String(legacySourceAudioId || "").trim();
      if (!legacyId || !sourceUrl) return;
      legacySourceUrlByActorId.set(legacyId, sourceUrl);
      nextSavedClips = nextSavedClips.map((clip) => {
        const sameSource = String(clip?.source_audio_id || "").trim() === legacyId;
        const sameClip = savedClip && [clip?.id, clip?.saved_clip_id, clip?.inserted_phrase_id].some((value) => String(value || "").trim() && [savedClip?.id, savedClip?.saved_clip_id, savedClip?.inserted_phrase_id].some((clipValue) => String(clipValue || "").trim() === String(value || "").trim()));
        if (!sameSource && !sameClip) return clip;
        return { ...clip, source_url: sourceUrl, asset_url: sourceUrl, server_url: sourceUrl, publicUrl: sourceUrl };
      });
      nextBlocks = nextBlocks.map((item) => {
        const sameSource = String(item?.source_audio_id || "").trim() === legacyId;
        const sameBlock = block && String(item?.id || "").trim() === String(block?.id || "").trim();
        const sameSavedClip = savedClip && getBlockSavedClipLookupValues(item).some((id) => [savedClip?.id, savedClip?.saved_clip_id, savedClip?.inserted_phrase_id].some((value) => String(value || "").trim() === id));
        if (!sameSource && !sameBlock && !sameSavedClip) return item;
        return { ...item, source_url: sourceUrl, asset_url: sourceUrl, server_url: sourceUrl, publicUrl: sourceUrl };
      });
      console.log("[PODCAST LEGACY ACTOR SOURCE RECOVERED]", { legacySourceAudioId: legacyId, sourceUrl, recoveredFrom });
    };

    const uploadRecoveredActorBlob = async ({ blob, filename = "actor_audio.mp3" } = {}) => {
      if (!blob) return "";
      const uploaded = await uploadFinalAudioBlob({ blob, filename, mimeType: blob.type || inferAudioMimeTypeFromFilename(filename) });
      return String(uploaded.url || uploaded.assetUrl || uploaded.asset_url || uploaded.publicUrl || uploaded.path || "").trim();
    };

    let registry = buildPodcastSourceRegistry({ actorAudios: nextActorAudios, savedClips: nextSavedClips, blocks: nextBlocks, legacySourceUrlByActorId });
    const originalUrl = String(audio.url || location.state?.audio?.url || storedManualTimingProject?.audio?.url || "").trim();

    for (const block of nextBlocks) {
      const sourceId = String(block?.source_audio_id || "main").trim() || "main";
      const isSilence = block?.type === "silence" || sourceId === "silence";
      if (isSilence || sourceId === "main") continue;
      const current = resolveServerRenderableBlockSource({ block, mainAudio: audio, originalAudioUrl: originalUrl, registry, savedClips: nextSavedClips, actorById });
      if (current.sourceUrl) continue;

      const savedClip = current.savedClip || findSavedClipForBlock(block, nextSavedClips);
      const legacyIdCandidates = [savedClip?.source_audio_id, block?.source_audio_id]
        .map((value) => String(value || "").trim())
        .filter((value) => value && value !== "main" && !registry.sourceUrlByActorId.has(value));

      for (const legacySourceAudioId of legacyIdCandidates) {
        if (legacySourceUrlByActorId.has(legacySourceAudioId)) break;

        const savedClipUrl = getSavedClipServerSourceUrl(savedClip || {});
        if (savedClipUrl) {
          rememberRecoveredSource({ legacySourceAudioId, sourceUrl: savedClipUrl, recoveredFrom: "saved_clip", savedClip, block });
          break;
        }

        let blob = null;
        try { blob = await getActorAudioBlob(getActorAudioBlobKey(sourceNodeId, legacySourceAudioId)); } catch {}
        if (blob) {
          const filename = String(savedClip?.source_name || block?.source_name || savedClip?.label || block?.saved_clip_label || `${legacySourceAudioId}.mp3`).trim();
          const sourceUrl = await uploadRecoveredActorBlob({ blob, filename });
          if (sourceUrl) {
            rememberRecoveredSource({ legacySourceAudioId, sourceUrl, recoveredFrom: "indexeddb", savedClip, block });
            break;
          }
        }

        // Do not silently bind a broken legacy phrase to a newly added actor track by label.
        // The user must explicitly pick the replacement block so we can extract a new
        // independent saved phrase asset instead of rendering from the old actor source.
      }

      registry = buildPodcastSourceRegistry({ actorAudios: nextActorAudios, savedClips: nextSavedClips, blocks: nextBlocks, legacySourceUrlByActorId });
    }

    registry = buildPodcastSourceRegistry({ actorAudios: nextActorAudios, savedClips: nextSavedClips, blocks: nextBlocks, legacySourceUrlByActorId });
    nextBlocks = nextBlocks.map((block) => {
      const resolved = resolveServerRenderableBlockSource({
        block,
        mainAudio: audio,
        originalAudioUrl: originalUrl,
        registry,
        savedClips: nextSavedClips,
        actorById,
      });
      if (!resolved.sourceUrl || block?.type === "silence" || String(block?.source_audio_id || "main") === "silence") return { ...block };
      return { ...block, source_url: resolved.sourceUrl, asset_url: resolved.sourceUrl, server_url: resolved.sourceUrl, publicUrl: resolved.sourceUrl };
    });

    console.log("[PODCAST SOURCE REGISTRY]", {
      actorIds: [...registry.actorById.keys()],
      savedClipIds: [...registry.savedClipById.keys()],
      legacySourceIds: [...registry.sourceUrlByLegacyActorId.keys()],
      uploadedSourceUrlByActorId: Object.fromEntries(registry.sourceUrlByActorId),
    });

    const changedSavedClips = JSON.stringify(serializeSavedClipsForStorage(nextSavedClips)) !== JSON.stringify(serializeSavedClipsForStorage(savedClips));
    const changedBlocks = JSON.stringify(serializeBlocksForStorage(nextBlocks)) !== JSON.stringify(serializeBlocksForStorage(blocks));
    const changedActors = JSON.stringify(serializeActorAudiosForStorage(nextActorAudios)) !== JSON.stringify(serializeActorAudiosForStorage(actorAudios));
    if (changedSavedClips) setSavedClips(nextSavedClips);
    if (changedBlocks) setBlocks(nextBlocks);
    if (changedActors) setActorAudios(nextActorAudios);
    return { actorAudios: nextActorAudios, savedClips: nextSavedClips, blocks: nextBlocks, registry };
  };

  const renderComposerAudioToServerAsset = async ({ manifest, finalDurationSec }) => {
    const { actorAudios: serverActorAudios, savedClips: serverSavedClips, blocks: serverBlocks, registry: preparedRegistry } = await prepareServerRenderableAudioSources();
    const actorById = new Map(serverActorAudios.map((actor) => [String(actor?.id || ""), actor]));
    const sourceRegistry = preparedRegistry || buildPodcastSourceRegistry({ actorAudios: serverActorAudios, savedClips: serverSavedClips, blocks: serverBlocks });
    const originalUrl = String(audio.url || location.state?.audio?.url || storedManualTimingProject?.audio?.url || "").trim();
    const availableActorIds = serverActorAudios.map((actor) => String(actor?.id || "").trim()).filter(Boolean);
    const availableSavedClipIds = serverSavedClips.flatMap((clip) => [clip?.id, clip?.saved_clip_id, clip?.inserted_phrase_id])
      .map((value) => String(value || "").trim())
      .filter(Boolean);
    const availableLegacySourceIds = [...sourceRegistry.sourceUrlByLegacyActorId.keys()];
    const missingSources = [];
    const renderBlocks = (Array.isArray(serverBlocks) ? serverBlocks : []).map((block) => {
      const sourceId = String(block?.source_audio_id || "main").trim() || "main";
      const isSilence = block?.type === "silence" || sourceId === "silence";
      const resolved = isSilence ? { sourceUrl: "", resolvedVia: "silence", savedClip: null } : resolveServerRenderableBlockSource({
        block,
        mainAudio: audio,
        originalAudioUrl: originalUrl,
        registry: sourceRegistry,
        actorById,
        savedClips: serverSavedClips,
      });
      const sourceUrl = resolved.sourceUrl;
      const renderedBlock = {
        ...block,
        source_url: sourceUrl,
        duration_sec: getBlockDuration(block),
      };

      if (!isSilence) {
        const sourceMap = {
          blockId: String(block?.id || ""),
          type: String(block?.type || "audio"),
          sourceAudioId: sourceId,
          savedClipId: String(block?.saved_clip_id || ""),
          insertedPhraseId: String(block?.inserted_phrase_id || ""),
          resolvedSourceUrl: sourceUrl,
          resolvedVia: resolved.resolvedVia,
          sourceStartSec: roundSeconds(block?.source_start_sec),
          sourceEndSec: roundSeconds(block?.source_end_sec),
          durationSec: getBlockDuration(block),
        };
        console.log("[PODCAST RENDER BLOCK SOURCE MAP]", sourceMap);
        if (!sourceUrl) {
          const missing = {
            label: getBrokenPhraseLabel({ ...block, savedClipLabel: block?.saved_clip_label || resolved.savedClip?.label || "", insertedPhraseLabel: block?.inserted_phrase_label || "" }),
            blockId: sourceMap.blockId,
            sourceAudioId: sourceId,
            savedClipId: sourceMap.savedClipId,
            insertedPhraseId: sourceMap.insertedPhraseId,
            savedClipLabel: String(block?.saved_clip_label || resolved.savedClip?.label || ""),
            insertedPhraseLabel: String(block?.inserted_phrase_label || ""),
            availableActorIds,
            availableSavedClipIds,
            availableLegacySourceIds,
          };
          console.error("[PODCAST RENDER BLOCK SOURCE MISSING]", missing);
          missingSources.push(missing);
        }
      }

      return renderedBlock;
    });

    if (missingSources.length) {
      setBrokenPhrases(missingSources);
      const firstMissing = missingSources[0];
      const label = getBrokenPhraseLabel(firstMissing);
      throw new Error(`Найдены сломанные фразы: ${missingSources.length}. Открой панель “Broken phrases”, заново добавь аудио актёра и явно замени каждую фразу. Первая: ${label}. blockId=${firstMissing.blockId || ""} savedClipId=${firstMissing.savedClipId || ""}`);
    }

    setBrokenPhrases([]);

    console.log("[PODCAST TO TIMING RENDER_TO_ASSET_START]", {
      originalAudioUrl: String(audio.url || ""),
      totalDurationSec: finalDurationSec,
      blockCount: renderBlocks.length,
      hasEdits: hasComposerEdits(),
    });

    const response = await fetch(`${API_BASE}/api/podcast-audio/render-to-asset`, {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sourceNodeId,
        originalAudioUrl: String(audio.url || location.state?.audio?.url || storedManualTimingProject?.audio?.url || "").trim(),
        blocks: renderBlocks,
        actorAudios: serverActorAudios,
        savedClips: serverSavedClips,
        deletionMarkers: Array.isArray(deletionMarkers) ? deletionMarkers : [],
        finalDurationSec,
        podcastEditManifest: manifest,
      }),
    });
    const data = await response.json().catch(() => null);
    if (!response.ok || data?.ok === false) {
      const detail = String(data?.code || data?.detail || data?.message || `render_failed:${response.status}`);
      const sourceDetail = data?.blockId || data?.sourceUrl ? ` (${data?.blockId || "block"}: ${data?.sourceUrl || "source"})` : "";
      throw new Error(`${detail}${sourceDetail}`);
    }
    const finalAudioUrl = String(data?.url || data?.assetUrl || data?.asset_url || data?.publicUrl || data?.path || "").trim();
    if (!finalAudioUrl) throw new Error("backend не вернул URL собранного аудио");
    const filename = String(data?.filename || data?.name || extractBackendStaticAssetFilename(finalAudioUrl, "podcast_audio_composer.mp3")).trim();
    const durationSec = roundSeconds(data?.duration_sec || data?.durationSec || finalDurationSec);
    console.log("[PODCAST TO TIMING RENDER_TO_ASSET_DONE]", {
      finalAudioUrl,
      durationSec,
      filename,
    });
    return {
      url: finalAudioUrl,
      filename,
      duration_sec: durationSec,
      duration_ms: Math.round(durationSec * 1000),
      mime_type: String(data?.mime_type || data?.mime || inferAudioMimeTypeFromFilename(filename)).trim(),
      source: data?.source || PODCAST_AUDIO_HANDOFF_SOURCE,
    };
  };

  const getOriginalAudioDurationSec = () => roundSeconds(audio.duration_sec || durationSec);

  const hasComposerEdits = () => {
    const originalDurationSec = getOriginalAudioDurationSec();
    const editedDurationSec = roundSeconds(totalDurationSec || durationSec || audio.duration_sec);
    const hasInsertedPhrases = (Array.isArray(blocks) ? blocks : []).some((block) => {
      const sourceId = String(block?.source_audio_id || "main");
      return hasPhraseIdentity(block) || (sourceId && sourceId !== "main" && sourceId !== "silence");
    });
    const hasSilence = (Array.isArray(blocks) ? blocks : []).some((block) => block?.type === "silence" || block?.source_audio_id === "silence");
    const hasDeletions = Array.isArray(deletionMarkers) && deletionMarkers.length > 0;
    const hasExternalAudio = (Array.isArray(actorAudios) && actorAudios.length > 0) || (Array.isArray(savedClips) && savedClips.length > 0);
    const durationChanged = originalDurationSec > 0 && editedDurationSec > 0 && Math.abs(editedDurationSec - originalDurationSec) > 0.25;
    return Boolean(hasInsertedPhrases || hasSilence || hasDeletions || hasExternalAudio || durationChanged);
  };

  const getFinalAudioStateCandidates = () => [
    storedManualTimingProject?.podcast_edit_manifest?.final_audio,
    storedManualTimingProject?.composer_edit_manifest?.final_audio,
    location.state?.podcast_edit_manifest?.final_audio,
    location.state?.composer_edit_manifest?.final_audio,
    location.state?.finalAudio,
    location.state?.composedAudio,
    location.state?.renderedAudio,
    storedManualTimingProject?.finalAudio,
    storedManualTimingProject?.composedAudio,
    storedManualTimingProject?.renderedAudio,
  ];

  const normalizeStaticFinalAudioCandidate = (candidate, { requireComposerSource = false, requireDurationMatch = false } = {}) => {
    const url = String(candidate?.url || candidate?.assetUrl || candidate?.asset_url || candidate?.publicUrl || candidate?.path || "").trim();
    if (!isBackendStaticAssetUrl(url)) return null;

    const source = String(candidate?.source || candidate?.audio_source || "").trim();
    if (requireComposerSource && source !== PODCAST_AUDIO_HANDOFF_SOURCE) return null;

    const editedDurationSec = roundSeconds(totalDurationSec || durationSec || audio.duration_sec);
    const candidateDurationSec = roundSeconds(candidate?.duration_sec || candidate?.durationSec || candidate?.duration || editedDurationSec);
    const hasCandidateDuration = candidateDurationSec > 0;
    if (requireDurationMatch && (!hasCandidateDuration || Math.abs(candidateDurationSec - editedDurationSec) > 0.75)) return null;

    const filename = String(candidate?.filename || candidate?.fileName || candidate?.name || extractBackendStaticAssetFilename(url)).trim();
    const staticDurationSec = candidateDurationSec || editedDurationSec;
    return {
      url,
      filename: filename || extractBackendStaticAssetFilename(url),
      duration_sec: staticDurationSec,
      duration_ms: Math.round(staticDurationSec * 1000),
      mime_type: String(candidate?.mime_type || candidate?.mimeType || inferAudioMimeTypeFromFilename(filename || url)).trim(),
      source: PODCAST_AUDIO_HANDOFF_SOURCE,
    };
  };

  const resolvePersistedStaticFinalAudio = () => {
    const hasEdits = hasComposerEdits();
    for (const candidate of getFinalAudioStateCandidates()) {
      const finalAudio = normalizeStaticFinalAudioCandidate(candidate, { requireDurationMatch: hasEdits });
      if (finalAudio) return finalAudio;
    }

    const composerSourceCandidates = [
      storedManualTimingProject?.audio,
      audio,
      location.state?.audio,
    ];
    for (const candidate of composerSourceCandidates) {
      const finalAudio = normalizeStaticFinalAudioCandidate(candidate, { requireComposerSource: true, requireDurationMatch: true });
      if (finalAudio) return finalAudio;
    }

    if (!hasEdits) {
      for (const candidate of composerSourceCandidates) {
        const originalAudio = normalizeStaticFinalAudioCandidate(candidate);
        if (originalAudio) return originalAudio;
      }
    }

    return null;
  };


  const buildPodcastEditManifestForTiming = ({ finalAudio = null, finalDurationSec = 0 } = {}) => {
    const safeBlocks = Array.isArray(blocks) && blocks.length ? blocks : createInitialBlocks(durationSec || audio.duration_sec || totalDurationSec);
    const timelineDurationSec = roundSeconds(finalDurationSec || getTimelineDuration(safeBlocks));
    let cursor = 0;
    const manifestBlocks = safeBlocks.map((block, index) => {
      const duration = getBlockDuration(block);
      const start = roundSeconds(cursor);
      const end = roundSeconds(cursor + duration);
      cursor = end;
      const label = getBlockLabelText(block);
      const isSilence = block?.type === "silence" || block?.source_audio_id === "silence";
      const isPhrase = hasPhraseIdentity(block) || (block?.source_audio_id && block.source_audio_id !== "main" && block.source_audio_id !== "silence");
      const sourceId = isSilence ? "silence" : String(block?.source_audio_id || "main");
      return {
        block_id: String(block?.id || `block_${index + 1}`),
        index: index + 1,
        timeline_start_sec: start,
        timeline_end_sec: end,
        duration_sec: duration,
        type: isSilence ? "silence" : (isPhrase ? "phrase" : "audio"),
        source_kind: isSilence ? "silence" : (isPhrase ? "inserted_audio" : "main_audio"),
        is_silence: isSilence,
        is_phrase: isPhrase,
        role_label: label,
        speaker_label: isPhrase ? label : "",
        source_audio_id: sourceId,
        source_audio_name: isSilence ? "Тишина" : getAudioNameForSourceId(sourceId, audio, actorAudios),
        source_start_sec: isSilence ? null : roundSeconds(block?.source_start_sec),
        source_end_sec: isSilence ? null : roundSeconds(block?.source_end_sec),
        saved_clip_id: block?.saved_clip_id || block?.inserted_phrase_id || "",
        saved_clip_label: block?.saved_clip_label || block?.inserted_phrase_label || block?.phrase_label || "",
        label,
        badge: getBlockInitialText(block),
        color: getBlockRenderColor(block),
        note: String(block?.guide_note || block?.user_note || "").trim(),
        phrase_hint: String(block?.guide_phrase_hint || "").trim(),
      };
    });

    return {
      schema: "podcast_edit_manifest_v1",
      version: 1,
      source: "podcast_audio_composer",
      created_at: new Date().toISOString(),
      source_node_id: sourceNodeId,
      timeline_duration_sec: timelineDurationSec,
      final_audio: finalAudio ? {
        url: finalAudio.url || "",
        filename: finalAudio.filename || "",
        duration_sec: roundSeconds(finalAudio.duration_sec || timelineDurationSec),
        mime_type: finalAudio.mime_type || finalAudio.mimeType || "",
        source: finalAudio.source || PODCAST_AUDIO_HANDOFF_SOURCE,
      } : null,
      original_main_audio: {
        url: audio.url || "",
        filename: audio.filename || "",
        duration_sec: roundSeconds(audio.duration_sec || durationSec),
      },
      actor_audios: (Array.isArray(actorAudios) ? actorAudios : []).map((actor) => ({
        id: actor.id || "",
        label: actor.label || actor.name || actor.filename || "",
        name: actor.name || actor.filename || actor.id || "",
        duration_sec: roundSeconds(actor.duration_sec || actor.durationSec || 0),
      })),
      saved_clips: (Array.isArray(savedClips) ? savedClips : []).map((clip) => ({
        id: clip.id || "",
        label: clip.label || clip.saved_clip_label || "",
        source_audio_id: clip.source_audio_id || "main",
        source_start_sec: roundSeconds(clip.source_start_sec),
        source_end_sec: roundSeconds(clip.source_end_sec),
        duration_sec: getBlockDuration(clip),
      })),
      deletion_markers: (Array.isArray(deletionMarkers) ? deletionMarkers : []).map((marker) => ({
        id: marker.id || "",
        at_sec: roundSeconds(marker.at_sec),
        removed_duration_sec: roundSeconds(marker.removed_duration_sec),
      })),
      blocks: manifestBlocks,
      rules: {
        manual_timing_receives_final_audio: true,
        manifest_preserves_roles_silence_replacements: true,
        asr_should_run_on_final_audio: true,
        semantic_pass_should_keep_podcast_edit_manifest: true,
      },
    };
  };

  const buildManualTimingScenesFromPodcastManifest = (manifest, finalDurationSec = 0) => {
    const sourceBlocks = Array.isArray(manifest?.blocks) ? manifest.blocks : [];
    const safeDuration = roundSeconds(finalDurationSec || manifest?.timeline_duration_sec || 0);
    const scenesFromBlocks = sourceBlocks
      .map((block, index) => {
        const start = roundSeconds(block?.timeline_start_sec);
        const end = roundSeconds(block?.timeline_end_sec);
        if (!(end > start)) return null;
        const isSilence = Boolean(block?.is_silence || block?.source_kind === "silence" || block?.type === "silence");
        const isPhrase = Boolean(block?.is_phrase || block?.source_kind === "inserted_audio" || block?.type === "phrase");
        const label = String(block?.label || block?.role_label || block?.speaker_label || (isSilence ? "Тишина" : isPhrase ? "Фраза" : "Диктор")).trim();
        return {
          scene_id: `seg_${String(index + 1).padStart(2, "0")}`,
          index: index + 1,
          start_sec: start,
          end_sec: end,
          duration_sec: roundSeconds(end - start),
          source_kind: isSilence ? "silence" : "audio",
          source_start_sec: isSilence ? null : start,
          source_end_sec: isSilence ? null : end,
          is_silence: isSilence,
          composer_source_kind: String(block?.source_kind || ""),
          composer_source_audio_id: String(block?.source_audio_id || ""),
          composer_source_audio_name: String(block?.source_audio_name || ""),
          composer_source_start_sec: block?.source_start_sec ?? null,
          composer_source_end_sec: block?.source_end_sec ?? null,
          composer_block_id: String(block?.block_id || ""),
          composer_block_type: String(block?.type || ""),
          composer_block_label: label,
          composer_role_label: String(block?.role_label || label),
          composer_saved_clip_id: String(block?.saved_clip_id || ""),
          composer_saved_clip_label: String(block?.saved_clip_label || ""),
          section: isSilence ? "instrumental" : "intro",
          route: isSilence ? "i2v_sound" : "i2v",
          contains_vocal: false,
          contains_vocal_assumption: false,
          contains_instrumental_assumption: !isSilence,
          use_sound_suggestion: isSilence,
          energy: isSilence ? "soft" : "mid",
          quality: "podcast_composer_handoff",
          boundary_reason: "podcast_composer_manifest",
          transition_out: "manual_cut",
          scene_type: isSilence ? "manual_silence" : (isPhrase ? "podcast_inserted_phrase" : "podcast_narrator_audio"),
          original_text: isSilence ? "[тишина]" : label,
          translated_text_ru: isSilence ? "[тишина]" : label,
          meaning_hint_ru: isSilence ? "Вставленная пользователем тишина из Podcast Composer." : (isPhrase ? `Вставленная/сохранённая фраза: ${label}.` : "Фрагмент основного дикторского аудио."),
          scene_goal_ru: isSilence ? "Техническая пауза в собранном подкасте." : "Фрагмент собранного подкаст-аудио для дальнейшей ASR/нарезки.",
          user_note_ru: isSilence
            ? "Podcast Composer: вставленная тишина. При смысловой нарезке учитывать как паузу."
            : `Podcast Composer: ${label}. Source: ${block?.source_audio_name || block?.source_audio_id || "main"}.`,
          speaker_id: isPhrase ? String(block?.source_audio_id || block?.speaker_label || "") : "",
          speaker_name: isPhrase ? label : "",
          source_phrase_ids: [],
          story_block_id: MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id,
          story_block_title_ru: MANUAL_TIMING_UNKNOWN_STORY_BLOCK.title_ru,
          story_block_color: MANUAL_TIMING_UNKNOWN_STORY_BLOCK.color,
        };
      })
      .filter(Boolean);

    if (scenesFromBlocks.length) return scenesFromBlocks;
    return safeDuration > 0 ? [{
      scene_id: "seg_01",
      index: 1,
      start_sec: 0,
      end_sec: safeDuration,
      duration_sec: safeDuration,
      source_kind: "audio",
      source_start_sec: 0,
      source_end_sec: safeDuration,
      is_silence: false,
      section: "intro",
      route: "i2v",
      quality: "podcast_composer_handoff",
      boundary_reason: "podcast_composer_manifest",
      story_block_id: MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id,
      story_block_title_ru: MANUAL_TIMING_UNKNOWN_STORY_BLOCK.title_ru,
      story_block_color: MANUAL_TIMING_UNKNOWN_STORY_BLOCK.color,
    }] : [];
  };


  const downloadComposedAudio = async () => {
    if (finalAudioBusy) return;
    setFinalAudioBusy("download");
    setMessage("Собираю финальное аудио для скачивания...");
    try {
      const result = await renderComposerAudioBlob();
      const url = URL.createObjectURL(result.blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = result.filename;
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 30000);
      setMessage(`Готово: аудио собрано и скачивается (${formatTimer(result.durationSec)}).`);
    } catch (error) {
      setMessage(`Не удалось скачать аудио: ${error?.message || "ошибка сборки"}.`);
    } finally {
      setFinalAudioBusy("");
    }
  };

  const applyComposedAudioToTiming = async () => {
    if (finalAudioBusy) return;
    setFinalAudioBusy("timing");
    setMessage("Готовлю финальное аудио для тайминга...");
    try {
      const persistedStaticAudio = resolvePersistedStaticFinalAudio();
      const hasEdits = hasComposerEdits();
      const originalDurationSec = getOriginalAudioDurationSec();
      const editedTotalDurationSec = roundSeconds(totalDurationSec || durationSec || audio.duration_sec);
      const originalAudioUrl = String(audio.url || location.state?.audio?.url || storedManualTimingProject?.audio?.url || "").trim();
      const logBlockedWrongAudio = () => {
        console.log("[PODCAST TO TIMING BLOCKED_WRONG_AUDIO]", {
          reason: "would_pass_original_audio_instead_of_composed_audio",
          originalAudioUrl,
          totalDurationSec: editedTotalDurationSec,
          originalDurationSec,
          hasEdits,
        });
      };
      let result = null;
      let usedUpload = false;
      let finalAudio = null;

      if (persistedStaticAudio?.url) {
        finalAudio = persistedStaticAudio;
        console.log("[PODCAST TO TIMING UPLOAD SKIPPED_STATIC_ASSET]", {
          url: finalAudio.url,
          filename: finalAudio.filename,
        });
      } else if (hasEdits) {
        setMessage("Собираю финальное аудио на сервере и сохраняю asset...");
        const pendingManifest = buildPodcastEditManifestForTiming({ finalAudio: null, finalDurationSec: editedTotalDurationSec });
        finalAudio = await renderComposerAudioToServerAsset({ manifest: pendingManifest, finalDurationSec: editedTotalDurationSec });
      } else {
        setMessage("Собираю финальное аудио и загружаю его в тайминг...");
        result = await renderComposerAudioBlob();
        if (Number(result?.blob?.size || 0) > ASSET_UPLOAD_SOFT_LIMIT_BYTES) {
          throw new Error("Сначала нужно сохранить финальное аудио после монтажа на сервере");
        }
        const uploaded = await uploadFinalAudioBlob(result);
        const finalUrl = String(uploaded.url || uploaded.assetUrl || uploaded.asset_url || uploaded.publicUrl || uploaded.path || "").trim();
        if (!finalUrl) throw new Error("backend не вернул URL собранного аудио");
        const finalDurationSec = roundSeconds(uploaded.duration_sec || uploaded.durationSec || result.durationSec);
        const filename = String(uploaded.filename || uploaded.name || result.filename || extractBackendStaticAssetFilename(finalUrl, "podcast_composer.wav")).trim();
        finalAudio = {
          url: finalUrl,
          filename,
          duration_sec: finalDurationSec,
          duration_ms: Math.round(finalDurationSec * 1000),
          mime_type: String(uploaded.mime_type || uploaded.mimeType || result.blob?.type || inferAudioMimeTypeFromFilename(filename)).trim(),
          source: PODCAST_AUDIO_HANDOFF_SOURCE,
        };
        usedUpload = true;
      }

      const finalDurationSec = roundSeconds(finalAudio.duration_sec || totalDurationSec || durationSec || audio.duration_sec);
      finalAudio = {
        ...finalAudio,
        duration_sec: finalDurationSec,
        duration_ms: Math.round(finalDurationSec * 1000),
        mime_type: finalAudio.mime_type || inferAudioMimeTypeFromFilename(finalAudio.filename || finalAudio.url),
        source: PODCAST_AUDIO_HANDOFF_SOURCE,
      };
      if (hasEdits && originalAudioUrl && String(finalAudio.url || "").trim() === originalAudioUrl) {
        logBlockedWrongAudio();
        throw new Error("Manual Timing не должен получать исходное дикторское аудио вместо собранного");
      }

      console.log("[PODCAST TO TIMING AUDIO HANDOFF]", {
        sourceUrl: String(finalAudio.url || ""),
        isStaticAsset: isBackendStaticAssetUrl(finalAudio.url),
        usedUpload,
        filename: finalAudio.filename,
        durationSec: finalDurationSec,
        sizeBytes: Number(result?.blob?.size || 0),
      });

      const editManifest = buildPodcastEditManifestForTiming({ finalAudio, finalDurationSec });
      const handoffScenes = buildManualTimingScenesFromPodcastManifest(editManifest, finalDurationSec);
      const handoffMarkers = handoffScenes.length
        ? [...handoffScenes.map((scene) => roundSeconds(scene.start_sec)), roundSeconds(handoffScenes[handoffScenes.length - 1].end_sec)]
        : [0, finalDurationSec];
      const handoffStoryBlock = {
        ...MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
        scene_ids: handoffScenes.map((scene) => scene.scene_id),
        start_sec: 0,
        end_sec: finalDurationSec,
      };
      const baseProject = storedManualTimingProject && typeof storedManualTimingProject === "object" ? storedManualTimingProject : {};
      const baseProjectMode = String(baseProject.project_mode || "").trim();
      const baseProjectKind = String(baseProject.project_kind || "").trim();
      const isExplicitPodcastProject = baseProjectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE || baseProjectKind === MANUAL_TIMING_PODCAST_DIALOGUE_PROJECT_KIND;
      const nextProjectMode = baseProjectMode || (isExplicitPodcastProject ? MANUAL_TIMING_PODCAST_DIALOGUE_MODE : MANUAL_TIMING_STORY_VOICEOVER_MODE);
      const nextProjectKind = baseProjectKind || (nextProjectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE ? MANUAL_TIMING_PODCAST_DIALOGUE_PROJECT_KIND : MANUAL_TIMING_STORY_PROJECT_KIND);
      const usePodcastManifestTiming = nextProjectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE;
      const preservedMarkers = Array.isArray(baseProject.markers) && baseProject.markers.length ? baseProject.markers : handoffMarkers;
      const preservedScenes = Array.isArray(baseProject.scenes) && baseProject.scenes.length ? baseProject.scenes : handoffScenes;
      const preservedStoryBlocks = Array.isArray(baseProject.story_blocks) && baseProject.story_blocks.length ? baseProject.story_blocks : [handoffStoryBlock];
      const preservedAudioPhrases = Array.isArray(baseProject.audio_phrases) ? baseProject.audio_phrases : [];
      const nextProject = {
        ...baseProject,
        nodeId: sourceNodeId,
        sourceNodeId,
        audio: finalAudio,
        audio_source: "podcast_audio_composer",
        project_mode: nextProjectMode,
        project_kind: nextProjectKind,
        timing_status: "draft",
        markers: usePodcastManifestTiming ? handoffMarkers : preservedMarkers,
        scenes: usePodcastManifestTiming ? handoffScenes : preservedScenes,
        audio_phrases: usePodcastManifestTiming ? [] : preservedAudioPhrases,
        selectedSceneId: usePodcastManifestTiming ? (handoffScenes[0]?.scene_id || "") : (baseProject.selectedSceneId || preservedScenes[0]?.scene_id || ""),
        story_blocks: usePodcastManifestTiming ? [handoffStoryBlock] : preservedStoryBlocks,
        podcast_edit_manifest: editManifest,
        composer_edit_manifest: editManifest,
        edit_manifest_source: "podcast_audio_composer",
        composer_audio_applied_at: Date.now(),
        updatedAt: Date.now(),
      };
      persistManualTimingProject(nextProject);
      setMessage("Готовое аудио и podcast_edit_manifest загружены. Открываю Manual Timing...");
      navigate(`/studio/manual-timing-editor?sourceNodeId=${encodeURIComponent(sourceNodeId)}`, {
        state: {
          sourceNodeId,
          fromPodcastComposer: true,
          replaceAudio: true,
          project_mode: nextProjectMode,
          project_kind: nextProjectKind,
          audio: finalAudio,
          podcast_edit_manifest: editManifest,
        },
      });
    } catch (error) {
      const detail = String(error?.message || "ошибка сборки/загрузки");
      const safeDetail = detail.includes("слишком большое") || detail.includes("too_large") || detail.includes("file_too_large")
        ? "Сначала нужно сохранить финальное аудио после монтажа на сервере"
        : detail;
      setMessage(`Не удалось перейти в тайминг: ${safeDetail}.`);
    } finally {
      setFinalAudioBusy("");
    }
  };

  const selectedBlockDuration = useMemo(() => {
    const selected = blocks.find((block) => block.id === selectedBlockId);
    return selected ? getBlockDuration(selected) : 0;
  }, [blocks, selectedBlockId]);

  const selectedIndex = getSelectedBlockIndex(blocks, selectedBlockId);
  const selectedBoundaryAvailable = selectedIndex >= 0;
  const canMoveSelectedRightBoundary = selectedIndex >= 0;
  const selectedRightEdgeSec = selectedBoundaryAvailable ? getBlockVirtualEnd(blocks, selectedIndex) : 0;
  const activeMainMode = activeMainPlaybackRef.current?.mode || "";
  const isSequencePlaying = isPlaying && activeMainMode === "sequence";

  return (
    <div className="podcastComposerPage" data-build={BUILD_ID}>
      <header className="podcastComposerHeader">
        <div>
          <p className="podcastComposerEyebrow">Podcast Audio Composer · {BUILD_ID}</p>
          <h1>Подкаст / аудио</h1>
        </div>
        <div className="podcastComposerHeaderActions">
          <button type="button" onClick={clearAll} disabled={!audio.url}>Очистить всё</button>
          <button type="button" onClick={onBackToNode}>Назад к ноде</button>
        </div>
      </header>

      {!audio.url ? (
        <div className="podcastComposerMessage">Аудио не найдено. Вернитесь к ноде и загрузите аудио.</div>
      ) : (
        <section className="podcastComposerCard" aria-label="Прослушивание аудио">
          <div className="podcastComposerAudioMeta">
            <div>
              <span>Файл</span>
              <strong>{audio.filename || "audio"}</strong>
            </div>
            <div>
              <span>Длительность после монтажа</span>
              <strong>{formatTimer(totalDurationSec || durationSec || audio.duration_sec)}</strong>
            </div>
          </div>

          <BlockTimeline
            blocks={blocks}
            currentTimeSec={currentTimeSec}
            deletionMarkers={deletionMarkers}
            onSeek={seekOnTimeline}
            onSelectBlock={selectBlock}
            onBlockDoubleClick={openBlockMenu}
            selectedBlockId={selectedBlockId}
            totalDurationSec={totalDurationSec || durationSec || audio.duration_sec}
          />

          <div className="podcastComposerHint">Жёлтая линия = прицел разреза. “Тишина” вставляет отдельный блок 0.5 сек по прицелу и сдвигает основное аудио вправо. Доводчик ← / → меняет конец выбранного блока; для тишины лимит до 30 сек. Двойной клик по блоку открывает меню: удалить, сохранить, цвет блока, аудио-фразы. JSON-кнопки нужны только для монтажной карты внутри композера: актёрские блоки — места для будущих вставок, не найденные голоса. Тишину JSON не размечает — паузы ставятся вручную.</div>

          <div className="podcastComposerControls">
            <button className="podcastComposerPlayButton" type="button" onClick={togglePlayback} disabled={isSequencePlaying}>{isPlaying && activeMainMode !== "sequence" ? "■ стоп" : "▶ блок"}</button>
            <button className="podcastComposerPlayButton" type="button" onClick={() => isSequencePlaying ? stopMainPlayback() : playFullMontageFrom(0)}>{isSequencePlaying ? "■ стоп montage" : "▶ весь монтаж"}</button>
            <span className="podcastComposerTimer">{formatTimer(currentTimeSec)}</span>
            <div className="podcastJsonMiniActions" aria-label="JSON-план подсказок">
              <button type="button" title="Скопировать образец JSON" onClick={copyGuideJsonSample}>⧉ JSON</button>
              <button type="button" title="Вставить JSON-план" onClick={openGuideJsonDialog}>{"{}"} JSON</button>
            </div>
            <button className="podcastComposerCutButton" type="button" onClick={splitCurrentBlock}>резать</button>
            <button className="podcastComposerSilenceButton" type="button" onClick={insertSilenceAtCursor}>тишина</button>
            <div className="podcastCutControls" aria-label="Микро-доводчик правой границы">
              <button type="button" onClick={() => adjustSelectedRightEdge(-1)} disabled={!selectedBoundaryAvailable}>←</button>
              <label>
                <span>шаг</span>
                <input min="0.01" max="30" step="0.01" type="number" value={microStepSec} onChange={(event) => setMicroStepSec(clampSeconds(event.target.value, 0.01, 30))} />
              </label>
              <button type="button" onClick={() => adjustSelectedRightEdge(1)} disabled={!selectedBoundaryAvailable}>→</button>
            </div>
            <button type="button" onClick={mergeSelectedWithNext} disabled={!selectedBlockId}>Склеить со следующим</button>
            <button type="button" onClick={undoLastAction} disabled={!history.length}>↶ Назад</button>
          </div>



          <div className="podcastComposerFinalActions" aria-label="Финальный файл">
            <button className="podcastFinalDownloadButton" type="button" onClick={downloadComposedAudio} disabled={!!finalAudioBusy || !blocks.length}>
              {finalAudioBusy === "download" ? "Собираю..." : "⬇ скачать аудио"}
            </button>
            <button className="podcastFinalTimingButton" type="button" onClick={applyComposedAudioToTiming} disabled={!!finalAudioBusy || !blocks.length}>
              {finalAudioBusy === "timing" ? "Собираю..." : "→ перейти в тайминг"}
            </button>
          </div>

          <div className="podcastComposerStatusGrid">
            <span>Выбранный блок: <b>{selectedBlockDuration ? formatTimer(selectedBlockDuration) : "нет"}</b></span>
            <span>Конец блока: <b>{selectedBoundaryAvailable ? formatTimer(selectedRightEdgeSec) : "нет"}</b></span>
            <span>Блоков: <b>{blocks.length}</b></span>
            <span>Удалений: <b>{deletionMarkers.length}</b></span>
            <span>Сохранено: <b>{savedClips.length}</b></span>
          </div>

          {message ? <div className="podcastComposerInlineMessage">{message}</div> : null}

          {brokenPhrases.length ? (
            <section className="podcastBrokenPhrasesPanel" aria-label="Broken phrases">
              <div className="podcastBrokenPhrasesHeader">
                <div>
                  <p>Broken phrases</p>
                  <h2>Нужно восстановить сохранённые фразы</h2>
                </div>
                <button type="button" onClick={() => actorAudioInputRef.current?.click()}>＋ заново добавить аудио актёра</button>
              </div>
              <p className="podcastBrokenPhrasesNote">
                Эти блоки ссылаются на удалённое старое actor audio. Composer не будет подставлять исходное дикторское аудио и не удалит вставки автоматически: выбери блок в заново добавленном аудио актёра и создай новую независимую сохранённую фразу.
              </p>
              <div className="podcastBrokenPhrasesList">
                {brokenPhrases.map((phrase) => {
                  const key = getBrokenPhraseRepairKey(phrase);
                  const selectedOption = getSelectedRepairOptionValue(phrase);
                  const busy = repairBusyKey === key;
                  return (
                    <article className="podcastBrokenPhraseCard" key={key}>
                      <div className="podcastBrokenPhraseMeta">
                        <strong>{getBrokenPhraseLabel(phrase)}</strong>
                        <dl>
                          <div><dt>blockId</dt><dd>{phrase.blockId || "—"}</dd></div>
                          <div><dt>savedClipId</dt><dd>{phrase.savedClipId || phrase.insertedPhraseId || "—"}</dd></div>
                          <div><dt>old sourceAudioId</dt><dd>{phrase.sourceAudioId || "—"}</dd></div>
                        </dl>
                      </div>
                      <div className="podcastBrokenPhraseRepairControls">
                        <label>
                          новый блок актёра
                          <select
                            value={selectedOption}
                            onChange={(event) => setRepairSelections((items) => ({ ...(items || {}), [key]: event.target.value }))}
                            disabled={!repairOptions.length || busy}
                          >
                            {repairOptions.length ? repairOptions.map((option) => (
                              <option key={option.value} value={option.value}>{option.label}</option>
                            )) : <option value="">Добавь/нарежь аудио актёра</option>}
                          </select>
                        </label>
                        <div className="podcastBrokenPhraseActions">
                          <button type="button" onClick={() => selectBrokenPhraseOnTimeline(phrase)}>Найти на таймлайне</button>
                          <button className="podcastBrokenPhraseReplace" type="button" onClick={() => replaceBrokenPhraseWithActorBlock(phrase)} disabled={!repairOptions.length || busy}>
                            {busy ? "Сохраняю..." : "Заменить новой фразой"}
                          </button>
                          <button className="podcastBrokenPhraseDelete" type="button" onClick={() => deleteBrokenPhraseInsertion(phrase)} disabled={busy}>Удалить вставку</button>
                        </div>
                      </div>
                    </article>
                  );
                })}
              </div>
            </section>
          ) : null}

          {guideJsonDialog ? (
            <div className="podcastGuideJsonDialog" onClick={(event) => event.stopPropagation()}>
              <button className="podcastMenuClose" type="button" onClick={() => setGuideJsonDialog(null)}>×</button>
              <h3>JSON-план блоков</h3>
              <p>Вставь роли и примерные тайминги диктора/персонажей. Тишину JSON не размечает — паузы добавляются вручную.</p>
              <textarea
                value={guideJsonDialog.value}
                onChange={(event) => setGuideJsonDialog((dialog) => ({ ...(dialog || {}), value: event.target.value }))}
                spellCheck={false}
              />
              <div className="podcastGuideJsonActions">
                <button type="button" onClick={copyGuideJsonSample}>Скопировать образец</button>
                <button className="primary" type="button" onClick={applyGuideJson}>Применить JSON</button>
              </div>
            </div>
          ) : null}

          {blockMenu ? (
            <div
              ref={blockMenuRef}
              className="podcastBlockActionMenu"
              style={{
                "--menu-x": `${blockMenu.x}px`,
                "--menu-y": `${blockMenu.y}px`,
                "--menu-left": `${blockMenu.x}px`,
                "--menu-top": `${blockMenu.y}px`,
              }}
              onClick={(event) => event.stopPropagation()}
            >
              <button className="podcastMenuClose" type="button" onClick={closeBlockMenu}>×</button>
              <h3>Действия</h3>

              <div className="podcastMenuButtonGrid">
                <button className="podcastMenuDangerAction" type="button" onClick={deleteSelectedBlock}>Удалить</button>
                <button className="podcastMenuSaveAction" type="button" onClick={openSaveDialog}>Сохранить</button>
              </div>

              <div className="podcastMenuSectionTitle accent">Цвет блока</div>
              <div className="podcastColorPickerGrid">
                {COLOR_SWATCHES.map((color) => (
                  <button
                    key={color}
                    className="podcastColorSwatch"
                    style={{ "--swatch-color": color }}
                    type="button"
                    title={color}
                    onClick={() => setSelectedBlockColor(color)}
                  />
                ))}
                <label className="podcastCustomColorPicker">
                  свой
                  <input
                    type="color"
                    value={(blocks.find((block) => block.id === selectedBlockId)?.color || "#60a5fa")}
                    onChange={(event) => setSelectedBlockColor(event.target.value)}
                  />
                </label>
              </div>

              <div className="podcastMenuSectionTitle accent">Аудио фразы</div>
              {savedClips.length ? (
                <div className="podcastSavedClipList">
                  {savedClips.map((clip) => {
                    const duration = roundSeconds(clip.duration_sec || (roundSeconds(clip.source_end_sec) - roundSeconds(clip.source_start_sec)));
                    const previewPosition = getPreviewPosition(clip);
                    const isPreviewing = phrasePreview.clipId === clip.id && phrasePreview.isPlaying;
                    return (
                      <div className="podcastSavedClipRow" key={clip.id}>
                        <div className="podcastSavedClipInfo">
                          <span>{clip.label}</span>
                          <small>{formatTimer(duration)}</small>
                        </div>
                        <input
                          className="podcastSavedClipSlider"
                          min="0"
                          max={duration || 0}
                          step="0.01"
                          type="range"
                          value={previewPosition}
                          onChange={(event) => seekSavedClipPreview(clip, event.target.value)}
                        />
                        <div className="podcastSavedClipActions">
                          <button type="button" title="Прослушать фразу" onClick={() => playSavedClipPreview(clip)}>{isPreviewing ? "■" : "▶"}</button>
                          <button type="button" title="Заменить выбранный блок этой фразой" onClick={() => replaceSelectedWithSavedClip(clip)}>↔</button>
                          <button type="button" title="Вставить эту фразу после выбранного блока" onClick={() => insertSavedClipAfterSelected(clip)}>＋</button>
                          <button className="podcastSavedClipDelete" type="button" title="Удалить аудио-фразу из списка" onClick={() => deleteSavedClip(clip.id)}>🗑</button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="podcastMenuEmpty">пока нет сохранённых аудио-фраз</div>
              )}
            </div>
          ) : null}

          {saveClipDialog ? (
            <div className="podcastSaveDialog" style={{ "--menu-x": `${blockMenu?.x || 260}px`, "--menu-y": `${blockMenu?.y || 220}px` }} onClick={(event) => event.stopPropagation()}>
              <button className="podcastMenuClose" type="button" onClick={() => setSaveClipDialog(null)}>×</button>
              <h3>Сохранить аудио-фразу</h3>
              <input
                value={saveClipDialog.label}
                onChange={(event) => setSaveClipDialog((dialog) => ({ ...(dialog || {}), label: event.target.value }))}
                autoFocus
              />
              <button type="button" onClick={saveSelectedClip}>OK</button>
            </div>
          ) : null}

          {deleteActorDialog ? (
            <div className="podcastDeleteGuardDialog" role="dialog" aria-modal="true" onClick={(event) => event.stopPropagation()}>
              <button className="podcastMenuClose" type="button" onClick={() => setDeleteActorDialog(null)} disabled={deleteActorDialog.busy}>×</button>
              <h3>Аудио используется во фразах</h3>
              <p>Это аудио используется во вставленных фразах. Если удалить его, финальная сборка не сможет собрать подкаст.</p>
              <div className="podcastDeleteGuardList" aria-label="Зависимые фразы">
                {(deleteActorDialog.dependentPhrases || []).map((phrase) => (
                  <div className="podcastDeleteGuardPhrase" key={`${phrase.id}-${phrase.blockId || ""}`}>
                    <strong>{phrase.label || phrase.id}</strong>
                    <small>{[phrase.id, phrase.blockId ? `block: ${phrase.blockId}` : ""].filter(Boolean).join(" · ")}</small>
                  </div>
                ))}
              </div>
              <div className="podcastDeleteGuardActions">
                <button type="button" onClick={() => setDeleteActorDialog(null)} disabled={deleteActorDialog.busy}>Cancel</button>
                <button className="podcastMenuDangerAction" type="button" onClick={() => deleteActorAudioNow(deleteActorDialog.actorId)} disabled={deleteActorDialog.busy}>Delete anyway</button>
                <button className="podcastMenuSaveAction" type="button" onClick={() => saveActorDependentPhrasesAsIndependent(deleteActorDialog.actorId)} disabled={deleteActorDialog.busy}>
                  {deleteActorDialog.busy ? "Saving..." : "Save dependent phrases as independent assets first"}
                </button>
              </div>
            </div>
          ) : null}

          <section className="podcastActorAudioPanel" aria-label="Дополнительные аудио актёров">
            <div className="podcastActorAudioHeader">
              <div>
                <h2>Аудио актёров</h2>
                <p>Здесь только режем озвучки актёров и сохраняем фразы. Вставка/замена делается в основном аудио.</p>
              </div>
              <input
                ref={actorAudioInputRef}
                type="file"
                accept="audio/*"
                multiple
                hidden
                onChange={addActorAudioFiles}
              />
              <button type="button" onClick={() => actorAudioInputRef.current?.click()}>＋ аудио</button>
            </div>

            {actorAudios.length ? (
              <div className="podcastActorAudioList">
                {actorAudios.map((actor) => {
                  const actorDuration = getTimelineDuration(actor.blocks) || actor.duration_sec || 0;
                  const actorSelectedDuration = actor.selectedBlockId
                    ? getBlockDuration(actor.blocks.find((block) => block.id === actor.selectedBlockId))
                    : 0;
                  return (
                    <article className="podcastActorAudioCard" key={actor.id} style={{ "--actor-color": actor.color || "#d99a18" }}>
                      <div className="podcastActorAudioMeta">
                        <div>
                          <strong>{actor.name}</strong>
                          <span>{formatTimer(actorDuration)}</span>
                        </div>
                        <label>
                          метка
                          <input value={actor.label} onChange={(event) => applyActorAudioPatch(actor.id, { label: event.target.value })} />
                        </label>
                        <label className="podcastActorColorLabel">
                          цвет
                          <input type="color" value={actor.color || "#d99a18"} onChange={(event) => applyActorAudioPatch(actor.id, { color: event.target.value, blocks: actor.blocks.map((block) => ({ ...block, color: event.target.value, block_label: actor.label })) })} />
                        </label>
                        <button className="podcastActorDeleteButton" type="button" onClick={() => deleteActorAudio(actor.id)}>Удалить аудио</button>
                      </div>

                      <BlockTimeline
                        blocks={actor.blocks}
                        currentTimeSec={actor.currentTimeSec || 0}
                        deletionMarkers={[]}
                        onSeek={(timeSec) => seekActorTimeline(actor.id, timeSec)}
                        onSelectBlock={(blockId, startSec) => selectActorBlock(actor.id, blockId, startSec)}
                        selectedBlockId={actor.selectedBlockId}
                        totalDurationSec={actorDuration}
                      />

                      <div className="podcastActorAudioControls">
                        <button type="button" onClick={() => toggleActorPlayback(actor.id)}>{actor.isPlaying ? "■ Stop" : "▶ Play блок"}</button>
                        <span className="podcastComposerTimer">{formatTimer(actor.currentTimeSec || 0)}</span>
                        <button type="button" onClick={() => splitActorAudioBlock(actor.id)}>резать</button>
                        <div className="podcastCutControls compact" aria-label="Доводчик аудио актёра">
                          <button type="button" onClick={() => adjustActorAudioBlockEnd(actor.id, -1)} disabled={!actor.selectedBlockId}>←</button>
                          <label>
                            <span>шаг</span>
                            <input min="0.01" max="30" step="0.01" type="number" value={actor.microStepSec || DEFAULT_MICRO_STEP_SEC} onChange={(event) => applyActorAudioPatch(actor.id, { microStepSec: clampSeconds(event.target.value, 0.01, 30) })} />
                          </label>
                          <button type="button" onClick={() => adjustActorAudioBlockEnd(actor.id, 1)} disabled={!actor.selectedBlockId}>→</button>
                        </div>
                        <button className="podcastActorSavePhraseButton" type="button" onClick={() => saveActorSelectedClip(actor.id)} disabled={!actor.selectedBlockId}>Сохранить фразу</button>
                        <span className="podcastActorSelectedInfo">выбрано: {actorSelectedDuration ? formatTimer(actorSelectedDuration) : "нет"}</span>
                      </div>
                    </article>
                  );
                })}
              </div>
            ) : (
              <div className="podcastActorAudioEmpty">Добавь отдельные озвучки деда, тётки или других персонажей. Они будут источниками фраз для основного монтажа.</div>
            )}
          </section>

          <audio
            ref={audioRef}
            preload="metadata"
            onEnded={() => {
              if (activeMainPlaybackRef.current?.mode === "sequence") {
                playNextSequenceBlock(activeMainPlaybackRef.current?.blockIndex || 0, (activeMainPlaybackRef.current?.blockIndex || 0) + 1);
              } else {
                stopMainPlayback();
              }
            }}
            onLoadedMetadata={onLoadedMetadata}
            onPause={() => {
              if (activeMainPlaybackRef.current?.mode !== "sequence") {
                stopMainPlaybackGuard();
                activeMainPlaybackRef.current = null;
                setIsPlaying(false);
              }
            }}
            onPlay={() => setIsPlaying(true)}
            onTimeUpdate={onTimeUpdate}
          />
        </section>
      )}
    </div>
  );
}
