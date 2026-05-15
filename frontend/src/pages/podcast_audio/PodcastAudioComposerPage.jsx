import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { getAccountScopedStorageKey } from "../clip_nodes/manualProjectBackup.js";
import { API_BASE } from "../../services/api.js";
import {
  MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
  normalizeManualTimingAudio,
  persistManualTimingProject,
  readManualTimingProjectForNode,
} from "../clip_nodes/manual_timing/manualTimingDomain.js";
import "./PodcastAudioComposerPage.css";

const BUILD_ID = "blocks-v44-timing-manifest-handoff";
const COMPOSER_STORAGE_VERSION = 43;
const RESTORABLE_STORAGE_VERSIONS = new Set([30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43]);
const ACTOR_AUDIO_DB_NAME = "podcast_audio_composer_assets_v1";
const ACTOR_AUDIO_DB_STORE = "audio_files";
const DEFAULT_MICRO_STEP_SEC = 0.5;
const MIN_BLOCK_SEC = 0.001;
const MAX_HISTORY_ITEMS = 50;
const TIMELINE_MIN_WIDTH_PX = 1200;
const TIMELINE_PIXELS_PER_SECOND = 18;
const TIMELINE_PIXELS_PER_BLOCK = 56;

const BLOCK_COLORS = [
  "var(--podcast-block-color-1)",
  "var(--podcast-block-color-2)",
  "var(--podcast-block-color-3)",
  "var(--podcast-block-color-4)",
  "var(--podcast-block-color-5)",
  "var(--podcast-block-color-6)",
];

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
  return (Array.isArray(actorAudios) ? actorAudios : []).map((actor) => ({
    ...stripTransientSourceUrl(actor),
    url: "",
    blocks: serializeBlocksForStorage(actor?.blocks),
    isPlaying: false,
  }));
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
    let url = "";
    try {
      const blob = await getActorAudioBlob(getActorAudioBlobKey(sourceNodeId, actorId));
      if (blob) url = URL.createObjectURL(blob);
    } catch {}
    const blocks = serializeBlocksForStorage(actor?.blocks).map((block) => ({
      ...block,
      source_audio_id: block.source_audio_id || actorId,
      source_url: url || undefined,
      source_name: block.source_name || actor.name || actor.filename || actorId,
      block_label: block.block_label || actor.label,
      color: block.color || actor.color,
    }));
    restored.push({
      ...actor,
      url,
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

function getItemSourceUrl(item = {}, mainAudio = {}, actorAudios = []) {
  if (!item || item.type === "silence" || item.source_audio_id === "silence") return "";
  const sourceId = String(item.source_audio_id || "main").trim() || "main";
  if (sourceId && sourceId !== "main") {
    const actorUrl = getAudioUrlForSourceId(sourceId, mainAudio, actorAudios);
    if (actorUrl) return actorUrl;
  }
  if (typeof item.source_url === "string" && item.source_url.trim()) return item.source_url.trim();
  return getAudioUrlForSourceId(sourceId, mainAudio, actorAudios);
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
        source_url: typeof block?.source_url === "string" && block.source_url.trim() ? block.source_url.trim() : undefined,
        source_name: typeof block?.source_name === "string" && block.source_name.trim() ? block.source_name.trim() : undefined,
        source_start_sec: type === "silence" ? 0 : start,
        source_end_sec: type === "silence" ? roundSeconds(end - start) : end,
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
      source_url: typeof baseBlock.source_url === "string" && baseBlock.source_url.trim() ? baseBlock.source_url.trim() : undefined,
      source_name: typeof baseBlock.source_name === "string" && baseBlock.source_name.trim() ? baseBlock.source_name.trim() : undefined,
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
  const viewportRef = useRef(null);
  const safeDuration = Math.max(0, normalizeNumber(totalDurationSec, 0));
  const timelineWidthPx = Math.ceil(Math.max(
    TIMELINE_MIN_WIDTH_PX,
    safeDuration * TIMELINE_PIXELS_PER_SECOND,
    blocks.length * TIMELINE_PIXELS_PER_BLOCK
  ));
  const playheadLeft = safeDuration > 0 ? `${Math.min(100, Math.max(0, (currentTimeSec / safeDuration) * 100))}%` : "0%";

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport || !safeDuration || timelineWidthPx <= viewport.clientWidth) return;

    const selectedIndex = blocks.findIndex((block) => block.id === selectedBlockId);
    const targetTimeSec = selectedIndex >= 0
      ? getBlockVirtualStart(blocks, selectedIndex) + (getBlockDuration(blocks[selectedIndex]) / 2)
      : currentTimeSec;
    const targetX = clampSeconds(targetTimeSec, 0, safeDuration) / safeDuration * timelineWidthPx;
    const padding = Math.min(96, Math.max(32, viewport.clientWidth * 0.12));
    const visibleStart = viewport.scrollLeft + padding;
    const visibleEnd = viewport.scrollLeft + viewport.clientWidth - padding;

    if (targetX < visibleStart) {
      viewport.scrollTo({ left: Math.max(0, targetX - padding), behavior: "smooth" });
    } else if (targetX > visibleEnd) {
      viewport.scrollTo({ left: Math.max(0, targetX - viewport.clientWidth + padding), behavior: "smooth" });
    }
  }, [blocks, currentTimeSec, safeDuration, selectedBlockId, timelineWidthPx]);

  return (
    <div className="podcastBlockTimelineWrap">
      <div className="podcastTimelineViewport" ref={viewportRef}>
        <div
          className="podcastTimelineInner"
          style={{ "--timeline-width": `${timelineWidthPx}px` }}
        >
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
  const silenceFrameRef = useRef(null);
  const phrasePreviewRef = useRef(null);
  const actorAudioInputRef = useRef(null);
  const activeActorPlaybackRef = useRef(null);
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
  const [guideJsonDialog, setGuideJsonDialog] = useState(null);
  const [phrasePreview, setPhrasePreview] = useState({ clipId: "", positionSec: 0, isPlaying: false });
  const [message, setMessage] = useState("");
  const [hasHydrated, setHasHydrated] = useState(false);
  const [finalAudioBusy, setFinalAudioBusy] = useState("");

  const totalDurationSec = useMemo(() => getTimelineDuration(blocks), [blocks]);
  const audioSignature = useMemo(() => getAudioSignature(audio, durationSec || audio.duration_sec), [audio, durationSec]);

  useEffect(() => {
    blocksRef.current = blocks;
  }, [blocks]);

  useEffect(() => {
    hydratedRef.current = hasHydrated;
  }, [hasHydrated]);

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
      if (silenceFrameRef.current) cancelAnimationFrame(silenceFrameRef.current);
    };
  }, []);

  useEffect(() => {
    const safeDuration = normalizeNumber(audio.duration_sec, 0);
    setDurationSec(safeDuration);
    setCurrentTimeSec(0);
    stopSilencePlayback();
    setIsPlaying(false);
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
        return actor ? { ...clip, source_url: actor.url || undefined, source_name: clip.source_name || actor.name || actor.filename } : clip;
      });
      const restoredBlocks = normalizeBlocks(saved.blocks, safeDuration, restoredSavedClips).map((block) => {
        const actor = restoredActorAudios.find((item) => item.id === block.source_audio_id);
        return actor ? { ...block, source_url: actor.url || undefined, source_name: block.source_name || actor.name || actor.filename } : block;
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
    setSelectedBlockId(blockId);
    seekOnTimeline(startSec, blocksRef.current);
    setBlockMenu({
      blockId,
      x: Number.isFinite(point.x) ? point.x : 220,
      y: Number.isFinite(point.y) ? point.y : 220,
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

  const replaceSelectedWithSavedClip = (clip) => {
    const snapshot = getSelectedBlockSnapshot();
    if (!snapshot || !clip) return;
    pushHistory("replace_with_saved_clip");
    const nextBlocks = [...blocks];
    nextBlocks[snapshot.index] = {
      ...snapshot.block,
      id: createId("block"),
      type: "phrase",
      block_kind: "phrase",
      source_audio_id: clip.source_audio_id || "main",
      source_url: getItemSourceUrl(clip, audio, actorAudios),
      source_name: clip.source_name || getAudioNameForSourceId(clip.source_audio_id || "main", audio, actorAudios),
      source_start_sec: roundSeconds(clip.source_start_sec),
      source_end_sec: roundSeconds(clip.source_end_sec),
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
    const snapshot = getSelectedBlockSnapshot();
    if (!snapshot || !clip) return;
    pushHistory("insert_saved_clip_after");
    const insertedBlock = {
      id: createId("block"),
      type: "phrase",
      block_kind: "phrase",
      source_audio_id: clip.source_audio_id || "main",
      source_url: getItemSourceUrl(clip, audio, actorAudios),
      source_name: clip.source_name || getAudioNameForSourceId(clip.source_audio_id || "main", audio, actorAudios),
      source_start_sec: roundSeconds(clip.source_start_sec),
      source_end_sec: roundSeconds(clip.source_end_sec),
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

  const stopSilencePlayback = () => {
    if (silenceFrameRef.current) {
      cancelAnimationFrame(silenceFrameRef.current);
      silenceFrameRef.current = null;
    }
  };

  const playSilenceBlock = (blockIndex, startOffsetSec = 0) => {
    const block = blocks[blockIndex];
    if (!block) return;
    const blockStartSec = getBlockVirtualStart(blocks, blockIndex);
    const duration = getBlockDuration(block);
    const startOffset = clampSeconds(startOffsetSec, 0, duration);
    const startedAt = performance.now();
    stopSilencePlayback();
    setCurrentTimeSec(roundSeconds(blockStartSec + startOffset));
    setIsPlaying(true);

    const tick = () => {
      const elapsed = startOffset + (performance.now() - startedAt) / 1000;
      const nextTime = roundSeconds(blockStartSec + Math.min(duration, elapsed));
      setCurrentTimeSec(nextTime);
      if (elapsed >= duration) {
        silenceFrameRef.current = null;
        setIsPlaying(false);
        return;
      }
      silenceFrameRef.current = requestAnimationFrame(tick);
    };

    silenceFrameRef.current = requestAnimationFrame(tick);
  };

  const insertSilenceAtCursor = () => {
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
      stopPhrasePreview({ pause: false });
      stopSilencePlayback();
      element.pause();
      setIsPlaying(false);
      return;
    }

    stopPhrasePreview({ pause: false });
    stopActorPlayback({ pause: false });

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
      currentBlockIndexRef.current = selectedIndex;
      setSelectedBlockId(block.id);
      setCurrentTimeSec(playVirtualTime);
      if (block.type === "silence") {
        element.pause();
        playSilenceBlock(selectedIndex, playOffset);
        return;
      }
      const ready = await prepareBlockAudio(block, roundSeconds(roundSeconds(block.source_start_sec) + playOffset));
      if (!ready) {
        setMessage("Не удалось открыть источник выбранного блока.");
        return;
      }
    } else if (currentTimeSec >= totalDurationSec - 0.05) {
      seekOnTimeline(0, blocks);
    }

    try {
      await element.play();
      setIsPlaying(true);
    } catch {
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

    if (!safeBlocks.length) return;

    const index = currentBlockIndexRef.current;
    const block = safeBlocks[index] || safeBlocks[0];
    const blockVirtualStart = getBlockVirtualStart(safeBlocks, index);
    const virtualTime = roundSeconds(blockVirtualStart + Math.max(0, sourceTime - roundSeconds(block.source_start_sec)));

    if (sourceTime >= roundSeconds(block.source_end_sec) - 0.025) {
      const selectedIndex = safeBlocks.findIndex((item) => item.id === selectedBlockId);
      if (selectedIndex === index) {
        setCurrentTimeSec(roundSeconds(blockVirtualStart + getBlockDuration(block)));
        setIsPlaying(false);
        element.pause();
        return;
      }

      const nextIndex = index + 1;
      if (safeBlocks[nextIndex]) {
        const nextBlock = safeBlocks[nextIndex];
        currentBlockIndexRef.current = nextIndex;
        element.currentTime = roundSeconds(nextBlock.source_start_sec);
        setCurrentTimeSec(getBlockVirtualStart(safeBlocks, nextIndex));
        return;
      }

      setCurrentTimeSec(getTimelineDuration(safeBlocks));
      setIsPlaying(false);
      element.pause();
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

  const deleteActorAudio = (actorId) => {
    const actor = actorAudios.find((item) => item.id === actorId);
    if (!actor) return;
    if (activeActorPlaybackRef.current?.actorId === actorId) stopActorPlayback({ pause: true });
    void deleteActorAudioBlob(getActorAudioBlobKey(sourceNodeId, actorId));
    setActorAudios((items) => items.filter((item) => item.id !== actorId));
    setMessage(`Дополнительное аудио “${actor.name}” удалено. Уже сохранённые фразы остаются в списке.`);
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

  const saveActorSelectedClip = (actorId) => {
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
    const countForActor = savedClips.filter((clip) => String(clip.source_audio_id || "") === actorId || String(clip.label || "").startsWith(actor.label)).length + 1;
    const label = `${compactBlockBadge(actor.label || actor.name || "АКТ") || "АКТ"}${countForActor}`;
    const clip = {
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
    setSavedClips((items) => [...items, clip]);
    setMessage(`Сохранена фраза актёра “${clip.label}” (${formatTimer(duration)}). Она появилась в общем списке “Аудио фразы”.`);
  };

  const splitCurrentBlock = () => {
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
    setSelectedBlockId(result.blocks[selectedIndex]?.id || selectedBlockId);

    // Как в Manual Timing: меняем конечную границу выбранного блока.
    // Прицел реза/currentTimeSec не двигаем. Он нужен только для кнопки “резать”.
    const arrow = direction < 0 ? "←" : "→";
    setMessage(`${arrow} Конец выбранного блока сдвинут на ${formatTimer(Math.abs(result.appliedDelta))}. Выбранный блок: ${formatTimer(result.selectedDurationAfter)}, правый остаток: ${formatTimer(result.rightDurationAfter)}.`);
  };

  const deleteSelectedBlock = () => {
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
    stopSilencePlayback();
    stopPhrasePreview({ pause: true });
    if (audioRef.current) audioRef.current.pause();
    setIsPlaying(false);
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

  const uploadFinalAudioBlob = async ({ blob, filename }) => {
    const form = new FormData();
    form.append("file", new File([blob], filename || "podcast_composer.wav", { type: "audio/wav" }));
    const response = await fetch(`${API_BASE}/api/assets/upload`, {
      method: "POST",
      credentials: "include",
      body: form,
    });
    const data = await response.json().catch(() => null);
    if (!response.ok) {
      throw new Error(data?.detail || data?.message || `upload_failed:${response.status}`);
    }
    return data || {};
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
    setMessage("Собираю финальное аудио и загружаю его в тайминг...");
    try {
      const result = await renderComposerAudioBlob();
      const uploaded = await uploadFinalAudioBlob(result);
      const finalUrl = String(uploaded.url || uploaded.assetUrl || uploaded.asset_url || uploaded.publicUrl || uploaded.path || "").trim();
      if (!finalUrl) throw new Error("backend не вернул URL собранного аудио");
      const finalDurationSec = roundSeconds(uploaded.duration_sec || uploaded.durationSec || result.durationSec);
      const finalAudio = {
        url: finalUrl,
        filename: uploaded.filename || uploaded.name || result.filename,
        duration_sec: finalDurationSec,
        duration_ms: Math.round(finalDurationSec * 1000),
      };
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
      const nextProject = {
        ...baseProject,
        nodeId: sourceNodeId,
        sourceNodeId,
        audio: finalAudio,
        audio_source: "podcast_audio_composer",
        project_mode: baseProject.project_mode || "podcast_dialogue",
        project_kind: baseProject.project_kind || "podcast",
        timing_status: "draft",
        markers: handoffMarkers,
        scenes: handoffScenes,
        audio_phrases: [],
        selectedSceneId: handoffScenes[0]?.scene_id || "",
        story_blocks: [handoffStoryBlock],
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
          audio: finalAudio,
          podcast_edit_manifest: editManifest,
        },
      });
    } catch (error) {
      setMessage(`Не удалось перейти в тайминг: ${error?.message || "ошибка сборки/загрузки"}.`);
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
            <button className="podcastComposerPlayButton" type="button" onClick={togglePlayback}>{isPlaying ? "■ Stop" : "▶ Play блок"}</button>
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
              className="podcastBlockActionMenu"
              style={{ "--menu-x": `${blockMenu.x}px`, "--menu-y": `${blockMenu.y}px` }}
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
            onEnded={() => setIsPlaying(false)}
            onLoadedMetadata={onLoadedMetadata}
            onPause={() => setIsPlaying(false)}
            onPlay={() => setIsPlaying(true)}
            onTimeUpdate={onTimeUpdate}
          />
        </section>
      )}
    </div>
  );
}
