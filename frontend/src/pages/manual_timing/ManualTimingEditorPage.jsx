import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { API_BASE } from "../../services/api";
import { buildManualProjectBackupJson, getAccountScopedStorageKey, unwrapManualProjectBackupJson } from "../clip_nodes/manualProjectBackup.js";
import "./ManualTimingEditorPage.css";
import {
  MANUAL_TIMING_ACTIVE_PROJECT_KEY,
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
  buildManualTimingClipPassJson,
  buildManualTimingExportJson,
  buildManualTimingPodcastPassJson,
  buildManualTimingStoryPassJson,
  buildManualTimingSampleJson,
  buildManualTimingScenesFromMarkers,
  buildManualTimingWarnings,
  deriveStoryBlockRangeFromScenes,
  formatTimingSec,
  getDefaultManualTimingNodeData,
  getManualTimingSceneDurationWarning,
  getManualTimingPhrasesForScene,
  hydrateManualTimingScenesWithStoryBlocks,
  normalizeManualTimingAudio,
  normalizeManualTimingAudioPhrases,
  normalizeManualTimingMarkers,
  normalizeManualTimingProjectFromJson,
  normalizeManualTimingSourcePhraseIds,
  normalizeManualTimingStoryBlocks,
  persistManualTimingProject,
  roundTimingSec,
  updateManualTimingSceneById,
  validateManualTimingClipPassImport,
  validateManualTimingPodcastPassImport,
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
const SHOW_MISSING_PHRASE_TOOLS = false;
const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY = "manual_clip_board_active_project";
const MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY = "manual_clip_board_active_project_id";
const MANUAL_CLIP_BOARD_ROUTE = "/studio/manual-clip-board";

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

function readActiveProject() {
  try {
    const raw = localStorage.getItem(getAccountScopedStorageKey(MANUAL_TIMING_ACTIVE_PROJECT_KEY))
      || localStorage.getItem(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function buildInitialProject() {
  const raw = readActiveProject();
  const project = { ...getDefaultManualTimingNodeData(), ...(raw || {}) };
  const audio = normalizeManualTimingAudio(project.audio);
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
  return {
    ...project,
    project_mode: project.project_mode || "",
    project_kind: project.project_kind || "",
    audio,
    markers,
    story_blocks,
    audio_phrases,
    scenes,
    selectedSceneId: project.selectedSceneId || scenes[0]?.scene_id || "",
    timing_status: project.timing_status || (scenes.length ? "draft" : "empty"),
  };
}

function clampTime(value, duration) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(Number(duration || 0), n));
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
      subtitle: "ASR → gap-aware scenes → Story Pass",
      hint: "Озвученная история: ASR режет речь, сцены покрывают паузы, Story Pass заполняет смысловые блоки.",
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
    pass: "Story Pass",
    copyPass: "Скопировать JSON для Story Pass",
    applyPass: "Применить Story Pass JSON",
    insertPass: "Вставить Story Pass JSON",
    panelTitle: "Story Pass JSON",
    panelHint: "Вставь JSON после Story Pass: он может заполнить только перевод, смысловые блоки и подсказки, без video_prompt/negative_prompt/sound_prompt.",
    placeholder: "Вставь сюда Story Pass JSON: scenes/story_blocks с сохранёнными таймингами и заполненными смысловыми полями...",
  };
}

function buildManualTimingModePassJson(project = {}) {
  const mode = String(project?.project_mode || project?.projectMode || "");
  if (mode === MANUAL_TIMING_MUSIC_CLIP_MODE) return buildManualTimingClipPassJson(project);
  if (mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) return buildManualTimingPodcastPassJson(project);
  return buildManualTimingStoryPassJson(project);
}

function getCompactWarningItems(project = {}, warnings = []) {
  const safeWarnings = Array.isArray(warnings) ? warnings : [];
  const scenes = Array.isArray(project.scenes) ? project.scenes : [];
  const audioPhrases = normalizeManualTimingAudioPhrases(project.audio_phrases);
  const items = [];
  const hasStoryScenes = scenes.some((scene) => normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds).length);
  const hasImportedStoryPass = scenes.some(sceneHasStoryPassFields);
  if (hasStoryScenes && !hasImportedStoryPass) items.push("Story Pass ещё не заполнен");
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
  const hasRequired = ["topic_block_id", "scene_type", "scene_goal_ru", "photo_prompt_hint_ru", "prompt_hint_ru"].every((key) => String(scene?.[key] || "").trim());
  if (!hasRequired) return false;
  if (String(scene?.route || "") !== "i2v_text") return true;
  return Boolean(String(scene?.narrator_text_en || scene?.narrator_text_ru || scene?.speaker_text_en || scene?.speaker_text_ru || "").trim());
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

function buildManualClipBoardStorageKey(nodeId = "") {
  const safeId = String(nodeId || "default").trim() || "default";
  return `manual_clip_board_project:${safeId}`;
}

function persistManualClipBoardProject(projectSnapshot = {}) {
  try {
    const serialized = JSON.stringify(projectSnapshot);
    localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_KEY, serialized);
    const nodeId = String(projectSnapshot?.nodeId || "").trim();
    if (nodeId) {
      localStorage.setItem(MANUAL_CLIP_BOARD_ACTIVE_PROJECT_ID_KEY, nodeId);
      localStorage.setItem(buildManualClipBoardStorageKey(nodeId), serialized);
    }
  } catch {}
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
  if (!scene) return [];
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

  const phraseIdsInsideScene = normalizeManualTimingAudioPhrases(allAudioPhrases)
    .filter((phrase) => Number(phrase.start_sec || 0) < sceneEnd - 0.001 && Number(phrase.end_sec || 0) > sceneStart + 0.001)
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

export default function ManualTimingEditorPage() {
  const navigate = useNavigate();
  const audioRef = useRef(null);
  const timelineRef = useRef(null);
  const playUntilRef = useRef(null);
  const rafRef = useRef(null);
  const [project, setProject] = useState(() => buildInitialProject());
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
  const [handoffStatus, setHandoffStatus] = useState("");
  const currentTimeRef = useRef(0);
  const isPlayingRef = useRef(false);
  const durationSecRef = useRef(0);
  const playStartGuardRef = useRef(null);

  const audio = normalizeManualTimingAudio(project.audio);
  const durationSec = Number(audio.duration_sec || 0);
  const markers = useMemo(() => normalizeManualTimingMarkers(project.markers, durationSec), [project.markers, durationSec]);
  const storyBlocks = useMemo(() => normalizeManualTimingStoryBlocks(project.story_blocks), [project.story_blocks]);
  const audioPhrases = useMemo(() => normalizeManualTimingAudioPhrases(project.audio_phrases), [project.audio_phrases]);
  const scenes = Array.isArray(project.scenes) ? project.scenes : [];
  const isStoryVoiceover = isStoryVoiceoverProject(project);
  const modeConfig = getManualTimingModeConfig(project);
  const isProjectModeSelected = Boolean(modeConfig.mode);
  const mainActionsDisabled = !isProjectModeSelected;
  const workflowLabels = getManualTimingWorkflowLabels(modeConfig.mode);
  const routeOptions = getManualTimingRouteOptions(modeConfig.mode);
  const isMusicClip = modeConfig.mode === MANUAL_TIMING_MUSIC_CLIP_MODE;
  const isPodcastDialogue = modeConfig.mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE;
  const passReadyForDirector = project.timing_status === "confirmed"
    && scenes.length > 0
    && (
      (isStoryVoiceover && hasRealStoryBlocks(storyBlocks) && scenes.every(sceneHasCompleteStoryPassFields))
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
  const selectedBoundaryIsInternal = selectedSceneIndex >= 0 && selectedSceneIndex < scenes.length - 1;
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
    currentTimeRef.current = Number(currentTime || 0);
  }, [currentTime]);

  useEffect(() => {
    durationSecRef.current = Number(durationSec || 0);
  }, [durationSec]);

  const persist = (nextProject) => {
    const safeProject = {
      ...nextProject,
      project_mode: nextProject?.project_mode || "",
      project_kind: nextProject?.project_kind || "",
      updatedAt: Date.now(),
    };
    setProject(safeProject);
    persistManualTimingProject(safeProject);
    return safeProject;
  };

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

  const setAudioElementTime = (timeValue) => {
    const audioEl = audioRef.current;
    if (!audioEl) return;
    const activeDuration = durationSecRef.current || durationSec;
    const time = roundTimingSec(clampTime(timeValue, activeDuration));
    try {
      audioEl.currentTime = time;
    } catch {}
  };

  const syncCurrentTimeFromAudio = ({ force = false } = {}) => {
    const audioEl = audioRef.current;
    if (!audioEl) return currentTimeRef.current;

    // Главное правило: когда audio на паузе, DOM-события pause/seeked/timeupdate
    // не имеют права двигать UI-курсор. Ручной переход уже сам обновляет курсор.
    if (!force && audioEl.paused) return currentTimeRef.current;

    const activeDuration = durationSecRef.current || durationSec;
    const nextTime = roundTimingSec(clampTime(Number(audioEl.currentTime || 0), activeDuration));
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
    const safeEnd = roundTimingSec(clampTime(endSec, durationSecRef.current || durationSec));
    const nextTime = Number.isFinite(Number(timeValue)) ? Number(timeValue) : Number(audioEl.currentTime || currentTimeRef.current || 0);
    if (nextTime < safeEnd - 0.012) return false;

    playUntilRef.current = null;
    playStartGuardRef.current = null;
    audioEl.pause();
    setAudioElementTime(safeEnd);
    setPlayingState(false);
    setDisplayTime(safeEnd);
    stopRafLoop();
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
        setPlayingState(false);
        stopRafLoop();
      }
      setAudioElementTime(time);
    }

    setDisplayTime(time);
    return time;
  };

  const playRange = (startValue, endValue = null) => {
    const audioEl = audioRef.current;
    if (!audioEl) return;

    const activeDuration = durationSecRef.current || durationSec;
    const start = roundTimingSec(clampTime(startValue, activeDuration));
    const boundedEnd = Number(endValue);
    const end = Number.isFinite(boundedEnd) ? roundTimingSec(clampTime(boundedEnd, activeDuration)) : null;

    if (end !== null && end <= start + 0.02) return;

    stopRafLoop();
    playUntilRef.current = end;
    playStartGuardRef.current = { start, until: Date.now() + 700 };

    try {
      audioEl.pause();
    } catch {}
    setPlayingState(false);
    setAudioElementTime(start);
    setDisplayTime(start);

    window.setTimeout(() => {
      const activeAudio = audioRef.current;
      if (!activeAudio) return;
      playUntilRef.current = end;
      playStartGuardRef.current = { start, until: Date.now() + 700 };
      setAudioElementTime(start);
      setDisplayTime(start);
      activeAudio.play().then(() => {
        setPlayingState(true);
        startRafLoop();
      }).catch(() => {
        setPlayingState(false);
      });
    }, 30);
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
      timing_status: extraPatch.timing_status || (nextScenes.length ? "draft" : project.timing_status || "draft"),
    });
  };

  useEffect(() => () => stopRafLoop(), []);

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
      playUntilRef.current = null;
      const rawTime = Number(audioEl.currentTime);
      const trustedTime = Number.isFinite(rawTime) && rawTime > 0.03
        ? rawTime
        : Number(currentTimeRef.current || 0);
      const pausedAt = roundTimingSec(clampTime(trustedTime, durationSecRef.current || durationSec));
      audioEl.pause();
      setAudioElementTime(pausedAt);
      setDisplayTime(pausedAt);
      setPlayingState(false);
      stopRafLoop();
      return;
    }

    const bounds = getSelectedSceneBounds();
    if (bounds) {
      const cursor = Number(currentTimeRef.current || 0);
      const isInsideSelected = cursor >= bounds.start - 0.035 && cursor < bounds.end - 0.035;
      const startFrom = isInsideSelected ? cursor : bounds.start;
      playRange(startFrom, bounds.end);
      return;
    }

    playRange(currentTimeRef.current || 0, durationSec);
  };

  const onStartOver = () => {
    playUntilRef.current = null;
    setAudioTime(0, { pause: true, clearBound: true });
  };

  const onPlayFromLastCut = () => {
    playRange(lastCutSec, durationSec);
  };

  const onPlayAroundCursor = () => {
    const center = Number(currentTimeRef.current || currentTime || 0);
    const start = roundTimingSec(Math.max(0, center - 1));
    const end = roundTimingSec(Math.min(durationSecRef.current || durationSec, center + 1));
    playRange(start, end);
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
    const nextMarkers = [...safeMarkers, time];
    const normalized = normalizeManualTimingMarkers(nextMarkers, durationSec);
    const boundaryIndex = normalized.findIndex((marker) => Math.abs(Number(marker) - time) < 0.001);
    const selectedSceneId = boundaryIndex > 0 ? getSceneIdForIndex(boundaryIndex - 1) : project.selectedSceneId;
    rebuildFromMarkers(normalized, scenes, { selectedSceneId, timing_status: "draft" });
    setAudioTime(time, { pause: true, clearBound: true });
  };

  const onAddMarker = () => addMarkerAt(currentTimeRef.current ?? currentTime);

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
    if (markers.length <= 2) return;
    const nextMarkers = markers.filter((_, idx) => idx !== markers.length - 2);
    const selectedSceneId = getSceneIdForIndex(Math.max(0, nextMarkers.length - 3));
    rebuildFromMarkers(nextMarkers, scenes, { selectedSceneId, timing_status: "draft" });
    setAudioTime(getLastInternalMarker(nextMarkers), { pause: true, clearBound: true });
  };

  const nudgeSelectedBoundary = (delta) => {
    if (!selectedScene || !selectedBoundaryIsInternal) {
      setCopyStatus("Выберите сцену с внутренней конечной границей");
      window.setTimeout(() => setCopyStatus(""), 1600);
      return;
    }
    const markerIndex = selectedSceneIndex + 1;
    const prevMarker = Number(markers[markerIndex - 1] || 0);
    const currentMarker = Number(markers[markerIndex] || 0);
    const minTime = prevMarker + 0.25;
    const maxTime = durationSec - 0.25;
    const nextTime = roundTimingSec(clampTime(currentMarker + Number(delta || 0), maxTime));
    if (nextTime <= minTime || nextTime >= durationSec - 0.001) return;

    const nextMarkers = shiftMarkersFromBoundary(markers, markerIndex, nextTime);
    const actualTime = Number(nextMarkers[markerIndex] || currentMarker);
    rebuildFromMarkers(nextMarkers, scenes, { selectedSceneId: selectedScene.scene_id, timing_status: "draft" }, { allowIdFallback: true });
    setAudioTime(actualTime, { pause: true, clearBound: true });
  };

  const playSegment = (scene) => {
    if (!scene) return;
    const startSec = roundTimingSec(clampTime(Number(scene.start_sec || 0), durationSecRef.current || durationSec));
    const endSec = roundTimingSec(clampTime(Number(scene.end_sec || 0), durationSecRef.current || durationSec));
    if (!(endSec > startSec + 0.02)) return;

    persist({ ...project, selectedSceneId: scene.scene_id });
    playRange(startSec, endSec);
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
    persist({
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
    });
    const statusTail = coverage.ok ? "Покрытие audio_duration_sec проверено: без дыр и overlap." : coverage.errors.join(" ");
    setAsrStatus(`${workflowLabels.buildScenes}: собрано ${hydratedScenes.length} сцен, ${draftBlocks.length} черновых story_blocks. ${statusTail}`);
    window.setTimeout(() => setAsrStatus(""), 7000);
  };

  const onConfirmTiming = () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const nextScenes = scenes.map((scene) => ({ ...scene, quality: "manual_confirmed" }));
    persist({ ...project, scenes: nextScenes, timing_status: "confirmed" });
  };


  const buildDirectorProjectSnapshot = () => ({
    ...project,
    project_mode: modeConfig.mode || project.project_mode || MANUAL_TIMING_STORY_VOICEOVER_MODE,
    project_kind: project.project_kind || (modeConfig.mode === MANUAL_TIMING_MUSIC_CLIP_MODE ? "clip" : (modeConfig.mode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE ? "podcast" : MANUAL_TIMING_STORY_PROJECT_KIND)),
    source: `manual_timing_${modeConfig.mode || MANUAL_TIMING_STORY_VOICEOVER_MODE}`,
    step: `${workflowLabels.pass.toLowerCase().replace(/\s+/g, "_")}_ready`,
    format: project.format,
    audio,
    audio_phrases: audioPhrases,
    story_blocks: storyBlocks,
    scenes: scenes.map((scene) => ({
      ...scene,
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: "",
    })),
    selectedSceneId: selectedScene?.scene_id || project.selectedSceneId || scenes[0]?.scene_id || "",
    timing_status: project.timing_status || "confirmed",
  });

  const onOpenDirectorBoard = async () => {
    if (!storyPassReadyForDirector) {
      setCopyStatus(`Сначала примените ${workflowLabels.pass} JSON`);
      return;
    }
    if (handoffStatus) return;

    let projectSnapshot = buildDirectorProjectSnapshot();
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

    persistManualTimingProject(projectSnapshot);
    persistManualClipBoardProject(projectSnapshot);
    if (!handoffWarning) setCopyStatus("Проект передан в режиссёрскую доску");
    setHandoffStatus("");
    navigate(MANUAL_CLIP_BOARD_ROUTE);
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

  const onDownloadTimingJson = () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    downloadJsonPayload(buildManualTimingExportJson(project), "manual_timing_export.json");
  };

  const onDownloadProjectBackup = () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    downloadJsonPayload(buildManualProjectBackupJson(project, { source: "manual_timing_editor" }), "manual_project_backup.json");
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

  const onCopyModePassJson = async () => {
    if (mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const payload = buildManualTimingModePassJson(project);
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      setCopyStatus(`JSON для ${workflowLabels.pass} скопирован`);
      window.setTimeout(() => setCopyStatus(""), 1600);
    } catch {
      setCopyStatus(`Не удалось скопировать JSON для ${workflowLabels.pass}`);
    }
  };

  const applyImportedTimingJson = (rawObject) => {
    const isBackupImport = rawObject?.backup_type === "photostudio_manual_project_backup";
    if (!isBackupImport && mainActionsDisabled) { setCopyStatus("Режим проекта не выбран"); return; }
    const importedObject = unwrapManualProjectBackupJson(rawObject);
    if (!isBackupImport) {
      const validations = [
        validateManualTimingStoryPassImport(importedObject, project),
        validateManualTimingClipPassImport(importedObject, project),
        validateManualTimingPodcastPassImport(importedObject, project),
      ];
      const failedValidation = validations.find((item) => !item.ok);
      if (failedValidation) {
        setCopyStatus(`${workflowLabels.pass} отклонён: ${failedValidation.errors.slice(0, 3).join(" ")}`);
        return;
      }
    }
    const nextProject = normalizeManualTimingProjectFromJson(importedObject, project);
    persist(nextProject);
    setJsonImportText(JSON.stringify(buildManualTimingExportJson(nextProject), null, 2));
    setCopyStatus(`JSON загружен: сцен ${nextProject.scenes?.length || 0}`);
    window.setTimeout(() => setCopyStatus(""), 1800);
    setAudioTime(0, { pause: true, clearBound: true });
  };

  const onImportTimingJson = () => {
    try {
      const raw = JSON.parse(jsonImportText || "{}");
      applyImportedTimingJson(raw);
    } catch (error) {
      setCopyStatus(`Ошибка JSON: ${error?.message || "неверный формат"}`);
    }
  };

  const onImportJsonFile = async (event) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    try {
      const text = await file.text();
      setJsonImportText(text);
      applyImportedTimingJson(JSON.parse(text));
    } catch (error) {
      setCopyStatus(`Ошибка файла JSON: ${error?.message || "неверный формат"}`);
    }
  };

  const onReset = () => {
    const nextMarkers = durationSec > 0 ? [0, durationSec] : [];
    const nextStoryBlocks = [MANUAL_TIMING_UNKNOWN_STORY_BLOCK];
    const nextScenes = nextMarkers.length ? hydrateManualTimingScenesWithStoryBlocks(buildManualTimingScenesFromMarkers(nextMarkers, [], { durationSec }), nextStoryBlocks) : [];
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
    const nextDuration = Number(audioEl.duration || 0);
    if (!(nextDuration > 0)) return;
    const currentDuration = Number(project?.audio?.duration_sec || 0);
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

  const onTimelineSegmentClick = (event, scene) => {
    event.stopPropagation();
    selectSceneAndSeekStart(scene, { pause: true });
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

  return (
    <>
    <div className={`manualTimingPage pageCard ${modeConfig.className}`}>
      <div className="manualTimingModeHeader">
        <div className="manualTimingModeTitleBlock">
          <h1 className="pageTitle">{modeConfig.title}</h1>
          <div className="manualTimingModeSubtitle">{modeConfig.subtitle}</div>
        </div>
        <div className="manualTimingModeHeaderActions">
          <button className="clipSB_btn clipSB_btnSecondary manualTimingBackButton" onClick={() => navigate(-1)}>← Назад к ноде</button>
          <span className="manualTimingModeBadge">{modeConfig.badge}</span>
        </div>
      </div>
      <div className="manualTimingModeHint">{modeConfig.hint}</div>
      {!isProjectModeSelected ? <div className="manualTimingModeMissing">Режим проекта не выбран. Вернитесь в ноду и выберите тип проекта.</div> : null}
      <div className="manualTimingMetaGrid">
        <div><b>Файл:</b> {audio.filename || "аудио не выбрано"}</div>
        <div><b>Длительность:</b> {formatTimingSec(durationSec)}</div>
        <div><b>Курсор:</b> {formatTimingSec(currentTime)}</div>
        <div><b>Сцен:</b> {scenes.length}</div>
        <div><b>Статус:</b> {readableTimingStatus}</div>
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
            playUntilRef.current = null;
            playStartGuardRef.current = null;
            setPlayingState(false);
            stopRafLoop();
            setDisplayTime(durationSecRef.current || durationSec);
          }}
        /> : <div className="manualTimingNoAudio">Аудио не выбрано. Подключите AUDIO-ноду или загрузите аудио в ноде “Тайминг песни”.</div>}

        <div className="manualTimingPlayerShell">
          <div className="manualTimingPlayerHeader">
            <div><b>Главная шкала разметки</b></div>
            <div>верхняя полоса — story blocks · нижняя — сцены · линия — текущее место · двойной клик по сцене — быстрая правка</div>
          </div>
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
                title={`${phrase.phrase_id}: ${formatTimingSec(phrase.start_sec)} – ${formatTimingSec(phrase.end_sec)} · ${phrase.text_en || "ASR phrase"}`}
              />)}
            </div> : null}
            {scenes.map((scene, idx) => {
              const isOpenTail = scene.scene_id === openTailSceneId;
              const isActive = selectedScene?.scene_id === scene.scene_id;
              const durationWarning = getManualTimingSceneDurationWarning(scene);
              return <button
                key={`player-${scene.scene_id}`}
                className={`manualTimingPlayerSegment ${isOpenTail ? "isOpenTail" : "isCut"} ${isActive ? "isActive" : ""}`}
                style={getSegmentStyle(scene, idx)}
                onClick={(event) => onTimelineSegmentClick(event, scene)}
                onDoubleClick={(event) => { event.stopPropagation(); openQuickEdit(scene); }}
                title={`${scene.scene_id}: ${formatTimingSec(scene.start_sec)} – ${formatTimingSec(scene.end_sec)}${durationWarning ? ` · ${durationWarning.text}` : ""}. Двойной клик — быстрая правка`}
              >
                <span>{scene.scene_id}</span>
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
          <div className="manualTimingPlayerLegend">
            <span><i className="legendBlock" /> story blocks</span>
            <span><i className="legendCut" /> сцены</span>
            <span><i className="legendTail" /> ещё не отрезано</span>
            <span><i className="legendCandidate" /> следующий отрезок</span>
            {audioPhrases.length ? <span><i className="legendAsrPhrase" /> ASR phrase map</span> : null}
            {SHOW_MISSING_PHRASE_TOOLS && audioPhrases.length ? <span><i className="legendMissingPhrase" /> пропущенная фраза</span> : null}
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

        <div className="manualTimingWorkflowActions" aria-label={`Основной workflow ${workflowLabels.pass}`}>
          <button className="clipSB_btn clipSB_btnPrimary" onClick={onCreateAudioPhraseMap} disabled={mainActionsDisabled || !audio.url || String(asrStatus || "").startsWith("ASR: распознаю")}>{workflowLabels.phraseMap}</button>
          <button className="clipSB_btn clipSB_btnPrimary" onClick={onBuildStoryScenesFromAsr} disabled={mainActionsDisabled || !audioPhrases.length}>{workflowLabels.buildScenes}</button>
          <button className="clipSB_btn clipSB_btnPrimary" onClick={onCopyModePassJson} disabled={mainActionsDisabled}>{workflowLabels.copyPass}</button>
          <button className="clipSB_btn clipSB_btnPrimary" onClick={() => { setIsJsonImportOpen(true); onImportTimingJson(); }} disabled={mainActionsDisabled || !jsonImportText.trim()}>{workflowLabels.applyPass}</button>
          <button className="clipSB_btn clipSB_btnSecondary" onClick={onConfirmTiming} disabled={mainActionsDisabled || !scenes.length}>Подтвердить</button>
          <button className="clipSB_btn clipSB_btnSecondary" onClick={onOpenDirectorBoard} disabled={mainActionsDisabled || !storyPassReadyForDirector || Boolean(handoffStatus)} title={openDirectorBoardTitle}>{handoffStatus || "Открыть режиссёрскую доску"}</button>
          {!storyPassReadyForDirector ? <span className="manualTimingWorkflowStatus">Сначала примените {workflowLabels.pass} JSON и подтвердите тайминг</span> : null}
        </div>

        {asrStatus ? <div className="manualTimingAsrStatus">{asrStatus}</div> : null}
        {audioPhrases.length ? <div className="manualTimingAsrNotice">ASR phrase map: audio_phrases покрывают только речь по word timestamps. Для storyboard используй “Собрать story scenes из ASR”: сцены станут gap-aware, покроют всю audio_duration_sec, а ChatGPT/Gemini затем заполнит перевод, story_blocks и смысловые поля без video_prompt/negative_prompt/sound_prompt.</div> : null}

        {!isProjectModeSelected ? <div className="manualTimingAdvancedDisabled">Ручные advanced-инструменты отключены: вернитесь в ноду и выберите тип проекта.</div> : null}

        {isProjectModeSelected ? <details className="manualTimingAdvancedPanel">
          <summary>Дополнительно / ручная правка</summary>
          <div className="manualTimingCompactActions">
            <button className="clipSB_btn" onClick={() => navigate(-1)}>Назад</button>
            <button className="clipSB_btn" onClick={onPlayPause} disabled={!audio.url}>{isPlaying ? "Пауза" : "▶ играть"}</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={() => playSegment(selectedScene)} disabled={!audio.url || !selectedScene}>▶ выбранный отрезок</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onStartOver} disabled={!audio.url}>В начало</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onPlayFromLastCut} disabled={!audio.url}>С последнего разреза</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onPlayAroundCursor} disabled={!audio.url}>±1 сек</button>
            <div className="manualTimingJumpBox">
              <span>Перейти к:</span>
              <div className="manualTimingTimecodeInput" aria-label="Точный переход по времени">
                <input
                  value={jumpTimeParts.min}
                  inputMode="numeric"
                  title="Минуты"
                  onFocus={(e) => e.target.select()}
                  onChange={(e) => updateJumpPart("min", e.target.value)}
                  onBlur={() => normalizeJumpPartOnBlur("min")}
                  onKeyDown={onJumpKeyDown}
                  disabled={!audio.url || !(durationSec > 0)}
                />
                <span>:</span>
                <input
                  value={jumpTimeParts.sec}
                  inputMode="numeric"
                  title="Секунды"
                  onFocus={(e) => e.target.select()}
                  onChange={(e) => updateJumpPart("sec", e.target.value)}
                  onBlur={() => normalizeJumpPartOnBlur("sec")}
                  onKeyDown={onJumpKeyDown}
                  disabled={!audio.url || !(durationSec > 0)}
                />
                <span>.</span>
                <input
                  value={jumpTimeParts.ms}
                  inputMode="numeric"
                  title="Миллисекунды"
                  onFocus={(e) => e.target.select()}
                  onChange={(e) => updateJumpPart("ms", e.target.value)}
                  onBlur={() => normalizeJumpPartOnBlur("ms")}
                  onKeyDown={onJumpKeyDown}
                  disabled={!audio.url || !(durationSec > 0)}
                />
              </div>
              <button className="clipSB_btn clipSB_btnSecondary" onClick={onJumpToTime} disabled={!audio.url || !(durationSec > 0)}>ОК</button>
              <button className="clipSB_btn clipSB_btnSecondary" onClick={useCurrentTimeForJump} disabled={!audio.url || !(durationSec > 0)}>из курсора</button>
            </div>
            <button className="clipSB_btn clipSB_btnPrimary" onClick={onAddMarker} disabled={!audio.url || !(durationSec > 0)}>Поставить разрез</button>
            <span className="manualTimingCutHint">Для исправления захвата следующей фразы не ставь новый разрез — используй микро-доводчик выбранной границы.</span>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onDeleteLastCut} disabled={markers.length <= 2}>Удалить последний</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onOpenAsrVerificationScenes} disabled={!audioPhrases.length}>Открыть ASR как проверочные сцены</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onCopyTimingJson} disabled={mainActionsDisabled}>Скопировать JSON</button>
            <label className="clipSB_btn clipSB_btnSecondary manualTimingFileBtn">
              Импорт backup / JSON
              <input type="file" accept=".json,application/json" onChange={onImportJsonFile} />
            </label>
            <button className="clipSB_btn clipSB_btnPrimary" onClick={onDownloadProjectBackup} disabled={mainActionsDisabled}>Скачать backup проекта</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onDownloadTimingJson} disabled={mainActionsDisabled}>Скачать текущий JSON</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onDownloadSampleJson} disabled={mainActionsDisabled}>Скачать JSON образец</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onDownloadAiSplitRequestJson} disabled={mainActionsDisabled}>Скачать JSON для AI-разбивки</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onCopyAiSplitRequestJson} disabled={mainActionsDisabled}>Скопировать JSON для AI</button>
            <button className="clipSB_btn clipSB_btnDanger" onClick={onReset}>Сбросить</button>
          </div>

          {SHOW_MISSING_PHRASE_TOOLS ? <div className="manualTimingMissingPhraseDraftPanel">
            <div className="manualTimingMissingPhraseDraftHeader">
              <strong>Разметка пропущенной фразы</strong>
              <span>отдельная audio_phrase, не разрезает сцены</span>
            </div>
            <div className="manualTimingMissingPhraseDraftHint">
              Для пропущенной фразы НЕ нажимай “Поставить разрез”. Используй метку пропущенной фразы — она не меняет сцены.
            </div>
            <div className="manualTimingMissingPhraseDraftValues">
              <span>Начало: <b>{hasTimingDraftValue(missingPhraseDraft.start_sec) ? formatTimingSec(missingPhraseDraft.start_sec) : "—"}</b></span>
              <span>Конец: <b>{hasTimingDraftValue(missingPhraseDraft.end_sec) ? formatTimingSec(missingPhraseDraft.end_sec) : "—"}</b></span>
            </div>
            <div className="manualTimingMissingPhraseDraftActions">
              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => setMissingPhraseDraftBoundary("start_sec")} disabled={!audio.url || !(durationSec > 0)}>Начало из курсора</button>
              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => setMissingPhraseDraftBoundary("end_sec")} disabled={!audio.url || !(durationSec > 0)}>Конец из курсора</button>
              <button className="clipSB_btn clipSB_btnSecondary manualTimingMissingPhraseButton" type="button" onClick={onAddMissingPhrase} disabled={!audio.url || !(durationSec > 0)}>Создать метку</button>
              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={resetMissingPhraseDraft} disabled={!hasTimingDraftValue(missingPhraseDraft.start_sec) && !hasTimingDraftValue(missingPhraseDraft.end_sec)}>Сбросить диапазон</button>
              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={onDeleteLastMissingPhrase} disabled={!audioPhrases.length}>Удалить последнюю пропущенную</button>
            </div>
          </div> : null}

          <div className="manualTimingNudgePanel">
            <div className="manualTimingNudgeTitle">Микро-доводчик выбранной границы — главный инструмент подгонки фразы</div>
            <div className="manualTimingNudgeGuidance">Если в конце сцены слышно начало следующей фразы — выбери сцену и двигай её конечную границу назад кнопками микро-доводчика. Это меняет границу между текущей и следующей сценой.</div>
            <div className="manualTimingNudgeButtons">
              {NUDGE_STEPS.map((step) => <button
                key={step}
                className="clipSB_btn clipSB_btnSecondary"
                disabled={!selectedBoundaryIsInternal}
                onClick={() => nudgeSelectedBoundary(step)}
              >{step > 0 ? `+${step.toFixed(2)}` : step.toFixed(2)} c</button>)}
            </div>
            <div className="manualTimingNudgeHint">− сдвигает конец сцены раньше, + сдвигает конец сцены позже. Начало следующей сцены становится этой же новой границей; текст, перевод и meaning выбранной сцены сохраняются.</div>
          </div>
        </details> : null}

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
                    <div><span>text_en</span><p>{String(phrase.text_en || "").trim() || "—"}</p></div>
                    <div><span>text_ru</span><p>{String(phrase.text_ru || "").trim() || "—"}</p></div>
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

        <div className="manualTimingJsonPanel">
          <div className="manualTimingJsonHeader">
            <strong>{workflowLabels.panelTitle}</strong>
            <span>{workflowLabels.panelHint}</span>
          </div>
          <div className="manualTimingJsonActions">
            <button className="clipSB_btn clipSB_btnSecondary" onClick={() => setIsJsonImportOpen((value) => !value)} disabled={!isProjectModeSelected}>
              {isJsonImportOpen ? "Скрыть поле JSON" : workflowLabels.insertPass}
            </button>
            <button className="clipSB_btn clipSB_btnPrimary" onClick={onImportTimingJson} disabled={mainActionsDisabled || !jsonImportText.trim()}>{workflowLabels.applyPass}</button>
            <button className="clipSB_btn clipSB_btnPrimary" onClick={onCopyModePassJson} disabled={mainActionsDisabled}>{workflowLabels.copyPass}</button>
          </div>
          {isJsonImportOpen ? <textarea
            className="manualTimingJsonTextarea"
            value={jsonImportText}
            placeholder={workflowLabels.placeholder}
            onChange={(e) => setJsonImportText(e.target.value)}
          /> : null}
        </div>

        {copyStatus ? <div className="manualTimingCopyStatus">{copyStatus}</div> : null}

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

      <section className="manualTimingTimeline" aria-label="шкала таймингов">
        <div className="manualTimingTimelineTrack">
          {timelineBlockRanges.length ? <div className="manualTimingBlockTrack" aria-label="смысловые блоки на шкале сцен">
            {timelineBlockRanges.map((blockRange) => <button
              key={`timeline-block-${blockRange.block_id}`}
              type="button"
              className="manualTimingBlockRange"
              style={getStoryBlockRangeStyle(blockRange)}
              onClick={(event) => { event.stopPropagation(); onStoryBlockClick(blockRange); }}
              title={`${blockRange.title}: ${formatTimingSec(blockRange.start_sec)} – ${formatTimingSec(blockRange.end_sec)}`}
            >
              <span className="manualTimingBlockRangeLabel">{blockRange.title}</span>
            </button>)}
          </div> : null}
          {scenes.map((scene, idx) => {
            const isOpenTail = scene.scene_id === openTailSceneId;
            return <button
              key={scene.scene_id}
              className={`manualTimingSegment ${isOpenTail ? "isOpenTail" : ""} ${selectedScene?.scene_id === scene.scene_id ? "isActive" : ""}`}
              style={getSegmentStyle(scene, idx)}
              onClick={() => selectSceneAndSeekStart(scene, { pause: true })}
              onDoubleClick={(event) => { event.stopPropagation(); openQuickEdit(scene); }}
              title={`${scene.scene_id} ${formatTimingSec(scene.start_sec)}–${formatTimingSec(scene.end_sec)}. Двойной клик — быстрая правка`}
            >{scene.scene_id}<span>{SECTION_LABELS[scene.section] || scene.section}/{scene.route}</span></button>;
          })}
          {markerPercents.map((marker) => <div key={marker.value} className="manualTimingMarker" style={{ left: marker.left }} title={formatTimingSec(marker.value)} />)}
        </div>
      </section>


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
