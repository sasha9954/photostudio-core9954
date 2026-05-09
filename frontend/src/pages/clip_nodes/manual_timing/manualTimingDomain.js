import { CHATGPT_STORY_SPLIT_TASK, STORY_PREP_TEMPLATE_META } from "../manual/manualClipBoardDomain.js";
export const MANUAL_TIMING_MODE = "manual_timing";
export const MANUAL_TIMING_ACTIVE_PROJECT_KEY = "manual_timing_active_project";
export const MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY = "manual_timing_active_project_id";

export const MANUAL_TIMING_SECTIONS = ["intro", "verse", "chorus", "bridge", "instrumental", "outro"];
export const MANUAL_TIMING_ROUTES = ["ia2v", "i2v"];
export const MANUAL_TIMING_ENERGY = ["soft", "mid", "high"];

export const MANUAL_TIMING_UNKNOWN_STORY_BLOCK = {
  block_id: "block_unknown",
  title_ru: "Без блока",
  summary_ru: "",
  block_goal_ru: "",
  block_reveal_ru: "",
  block_emotion_ru: "",
  color: "#64748B",
  scene_ids: [],
  start_sec: 0,
  end_sec: 0,
};

const MANUAL_TIMING_SECTION_LABELS_RU = {
  intro: "вступление",
  verse: "куплет",
  chorus: "припев",
  bridge: "бридж",
  instrumental: "проигрыш",
  outro: "финал",
};

function sectionLabelRu(section = "") {
  const key = String(section || "").trim().toLowerCase();
  return MANUAL_TIMING_SECTION_LABELS_RU[key] || key || "не указана";
}

export function getManualTimingProjectStorageKey(nodeId = "") {
  const safeId = String(nodeId || "default").trim() || "default";
  return `manual_timing_project:${safeId}`;
}

export function readManualTimingJsonStorage(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function readManualTimingProjectForNode(nodeId = "") {
  const safeId = String(nodeId || "").trim();
  const active = readManualTimingJsonStorage(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
  if (active && (!safeId || String(active?.nodeId || "") === safeId)) return active;
  const scoped = readManualTimingJsonStorage(getManualTimingProjectStorageKey(safeId));
  if (scoped && (!safeId || String(scoped?.nodeId || "") === safeId)) return scoped;
  return null;
}

export function persistManualTimingProject(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  try {
    const serialized = JSON.stringify(safeProject);
    localStorage.setItem(MANUAL_TIMING_ACTIVE_PROJECT_KEY, serialized);
    const nodeId = String(safeProject?.nodeId || "").trim();
    if (nodeId) {
      localStorage.setItem(MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY, nodeId);
      localStorage.setItem(getManualTimingProjectStorageKey(nodeId), serialized);
    }
  } catch {}
}

export function removeManualTimingProjectForNode(nodeId = "") {
  const safeId = String(nodeId || "").trim();
  try {
    if (safeId) localStorage.removeItem(getManualTimingProjectStorageKey(safeId));
    const active = readManualTimingJsonStorage(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
    if (!safeId || String(active?.nodeId || "") === safeId) {
      localStorage.removeItem(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
      localStorage.removeItem(MANUAL_TIMING_ACTIVE_PROJECT_ID_KEY);
    }
  } catch {}
}

export function getDefaultManualTimingNodeData() {
  return {
    mode: MANUAL_TIMING_MODE,
    project_kind: "clip",
    format: "9:16",
    audio: {
      url: "",
      filename: "",
      duration_sec: 0,
      duration_ms: 0,
    },
    timing_status: "empty",
    markers: [],
    story_blocks: [MANUAL_TIMING_UNKNOWN_STORY_BLOCK],
    audio_phrases: [],
    scenes: [],
    selectedSceneId: "",
    updatedAt: 0,
  };
}

export function normalizeManualTimingAudio(audio = null) {
  if (!audio || typeof audio !== "object") return { url: "", filename: "", duration_sec: 0, duration_ms: 0 };
  const url = String(audio.url || audio.value || audio.href || "").trim();
  const filename = String(audio.filename || audio.fileName || audio.name || audio.preview || audio.meta?.filename || "").trim();
  const durationSecRaw = Number(
    audio.duration_sec
    ?? audio.durationSec
    ?? audio.audioDurationSec
    ?? audio.duration
    ?? audio.meta?.duration_sec
    ?? audio.meta?.durationSec
    ?? audio.meta?.audioDurationSec
    ?? audio.meta?.duration
    ?? 0
  );
  const durationMsRaw = Number(audio.duration_ms ?? audio.durationMs ?? audio.meta?.duration_ms ?? 0);
  const duration_sec = Number.isFinite(durationSecRaw) ? Number(durationSecRaw.toFixed(3)) : 0;
  const duration_ms = Number.isFinite(durationMsRaw) && durationMsRaw > 0 ? Math.round(durationMsRaw) : Math.round(duration_sec * 1000);
  return { url, filename, duration_sec, duration_ms };
}

export function roundTimingSec(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n)) return 0;
  return Number(n.toFixed(3));
}

export function formatTimingSec(value) {
  const n = Number(value || 0);
  if (!Number.isFinite(n) || n <= 0) return "0:00.000";
  const minutes = Math.floor(n / 60);
  const seconds = Math.floor(n % 60);
  const millis = Math.round((n - Math.floor(n)) * 1000);
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}

export function normalizeManualTimingSection(section = "") {
  const value = String(section || "").trim().toLowerCase();
  return MANUAL_TIMING_SECTIONS.includes(value) ? value : "verse";
}

export function normalizeManualTimingRoute(route = "") {
  const value = String(route || "").trim().toLowerCase();
  return MANUAL_TIMING_ROUTES.includes(value) ? value : "i2v";
}

export function normalizeManualTimingEnergy(energy = "") {
  const value = String(energy || "").trim().toLowerCase();
  return MANUAL_TIMING_ENERGY.includes(value) ? value : "mid";
}

export function getSectionDefaults(section = "") {
  const normalized = normalizeManualTimingSection(section);
  if (normalized === "intro" || normalized === "instrumental" || normalized === "outro") {
    return {
      section: normalized,
      route: "i2v",
      contains_vocal: false,
      contains_vocal_assumption: false,
      contains_instrumental_assumption: true,
    };
  }
  return {
    section: normalized,
    route: "ia2v",
    contains_vocal: true,
    contains_vocal_assumption: true,
    contains_instrumental_assumption: false,
  };
}


function normalizeStoryBlockColor(value = "") {
  const raw = String(value || "").trim();
  return /^#[0-9a-f]{6}$/i.test(raw) || /^#[0-9a-f]{3}$/i.test(raw) ? raw : MANUAL_TIMING_UNKNOWN_STORY_BLOCK.color;
}

export function normalizeManualTimingAudioPhrases(audioPhrases = []) {
  const rawPhrases = Array.isArray(audioPhrases) ? audioPhrases : [];
  const seen = new Set();

  return rawPhrases
    .map((phrase, idx) => {
      const rawId = String(phrase?.phrase_id || phrase?.phraseId || phrase?.id || "").trim();
      const phrase_id = rawId || `manual_missing_${String(idx + 1).padStart(3, "0")}`;
      if (seen.has(phrase_id)) return null;
      seen.add(phrase_id);

      const start = roundTimingSec(phrase?.start_sec ?? phrase?.startSec ?? phrase?.start ?? 0);
      const end = roundTimingSec(phrase?.end_sec ?? phrase?.endSec ?? phrase?.end ?? start);
      if (!(end > start)) return null;

      return {
        phrase_id,
        start_sec: start,
        end_sec: end,
        text_en: String(phrase?.text_en || phrase?.textEn || ""),
        text_ru: String(phrase?.text_ru || phrase?.textRu || ""),
        meaning_ru: String(phrase?.meaning_ru || phrase?.meaningRu || ""),
        status: String(phrase?.status || "needs_transcription"),
        assignment_status: String(phrase?.assignment_status || phrase?.assignmentStatus || (String(phrase?.status || "needs_transcription") === "needs_transcription" ? "unassigned" : "")),
        confidence: Number.isFinite(Number(phrase?.confidence)) ? Number(Number(phrase.confidence).toFixed(4)) : 0,
        source: String(phrase?.source || phrase?.timing_source || phrase?.timingSource || (String(phrase?.status || "") === "asr_raw" ? "asr" : "")),
        note_ru: String(phrase?.note_ru || phrase?.noteRu || ""),
      };
    })
    .filter(Boolean);
}

export function normalizeManualTimingSourcePhraseIds(value = []) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map((id) => String(id || "").trim()).filter(Boolean))];
}

export const MANUAL_TIMING_NEEDS_TRANSCRIPTION_RULE_RU = "Если audio_phrases содержит status='needs_transcription' или assignment_status='unassigned', это пропущенные фразы аудио. Не удаляй их. Нужно распознать/перевести эти фразы по аудио или предоставленному тексту, заполнить text_en, text_ru, meaning_ru и решить, куда их вставить: в предыдущую сцену, следующую сцену или отдельную новую сцену. Не менять тайминги без явного указания пользователя; если нужна новая сцена, предложить это явно.";
export const MANUAL_TIMING_ASR_SOURCE_OF_TRUTH_RULE_RU = "Локальный ASR/faster-whisper не должен понимать смысл истории и не должен делать финальные story_blocks: он возвращает только audio_phrases, word timestamps и точные start_sec/end_sec речи. Gap-aware builder локально собирает scenes с source_phrase_ids, continuous coverage от 0 до audio_duration_sec, speech_start_sec/speech_end_sec, pre_silence_sec/post_silence_sec и технические draft blocks. LLM Story Pass делает только перевод, meaning_hint_ru, story_blocks по смыслу, scene_goal_ru, photo_prompt_hint_ru, prompt_hint_ru, scene_role_in_block_ru и block_progress_ru; video_prompt, negative_prompt, sound_prompt оставить пустыми. Не менять audio_phrases, scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec и source_phrase_ids. Если кажется, что scenes нужно объединить или разделить, не делать это самостоятельно: заполнить user_note_ru с предложением, а тайминги оставить без изменений.";

export function buildManualTimingChatGptTask(hasUnresolvedAudioPhrases = false, hasAsrAudioPhrases = false) {
  const rules = Array.isArray(CHATGPT_STORY_SPLIT_TASK.rules_ru) ? CHATGPT_STORY_SPLIT_TASK.rules_ru : [];
  const nextRules = [...rules];
  if (hasUnresolvedAudioPhrases && !nextRules.includes(MANUAL_TIMING_NEEDS_TRANSCRIPTION_RULE_RU)) {
    nextRules.push(MANUAL_TIMING_NEEDS_TRANSCRIPTION_RULE_RU);
  }
  if (hasAsrAudioPhrases && !nextRules.includes(MANUAL_TIMING_ASR_SOURCE_OF_TRUTH_RULE_RU)) {
    nextRules.push(MANUAL_TIMING_ASR_SOURCE_OF_TRUTH_RULE_RU);
  }
  if (nextRules.length === rules.length) return CHATGPT_STORY_SPLIT_TASK;
  return {
    ...CHATGPT_STORY_SPLIT_TASK,
    rules_ru: nextRules,
  };
}

function rangesIntersect(aStart, aEnd, bStart, bEnd) {
  return Number(aStart) < Number(bEnd) - 0.001 && Number(aEnd) > Number(bStart) + 0.001;
}

export function getManualTimingPhrasesForScene(audioPhrases = [], scene = null) {
  if (!scene) return [];
  const sceneStart = Number(scene?.start_sec || 0);
  const sceneEnd = Number(scene?.end_sec || 0);
  if (!(sceneEnd > sceneStart)) return [];
  return normalizeManualTimingAudioPhrases(audioPhrases).filter((phrase) => rangesIntersect(phrase.start_sec, phrase.end_sec, sceneStart, sceneEnd));
}

export function normalizeManualTimingStoryBlocks(storyBlocks = []) {
  const rawBlocks = Array.isArray(storyBlocks) ? storyBlocks : [];
  const seen = new Set();
  const blocks = rawBlocks
    .map((block, idx) => {
      const rawId = String(block?.block_id || block?.blockId || block?.id || "").trim();
      const block_id = rawId || (idx === 0 ? MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id : `block_${idx + 1}`);
      if (seen.has(block_id)) return null;
      seen.add(block_id);
      return {
        block_id,
        title_ru: String(block?.title_ru || block?.titleRu || block?.title || (block_id === MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id ? MANUAL_TIMING_UNKNOWN_STORY_BLOCK.title_ru : block_id)),
        summary_ru: String(block?.summary_ru || block?.summaryRu || block?.summary || ""),
        block_goal_ru: String(block?.block_goal_ru || block?.blockGoalRu || block?.goal_ru || ""),
        block_reveal_ru: String(block?.block_reveal_ru || block?.blockRevealRu || block?.reveal_ru || ""),
        block_emotion_ru: String(block?.block_emotion_ru || block?.blockEmotionRu || block?.emotion_ru || ""),
        color: normalizeStoryBlockColor(block?.color || (block_id === MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id ? MANUAL_TIMING_UNKNOWN_STORY_BLOCK.color : "")),
        scene_ids: Array.isArray(block?.scene_ids || block?.sceneIds)
          ? (block?.scene_ids || block?.sceneIds).map((id) => String(id || "").trim()).filter(Boolean)
          : [],
        start_sec: roundTimingSec(block?.start_sec ?? block?.startSec ?? 0),
        end_sec: roundTimingSec(block?.end_sec ?? block?.endSec ?? 0),
      };
    })
    .filter(Boolean);

  if (!blocks.some((block) => block.block_id === MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id)) {
    blocks.push({ ...MANUAL_TIMING_UNKNOWN_STORY_BLOCK });
  }
  return blocks.length ? blocks : [{ ...MANUAL_TIMING_UNKNOWN_STORY_BLOCK }];
}


export function deriveStoryBlockRangeFromScenes(block, scenes = []) {
  const blockId = String(block?.block_id || "").trim();
  const safeScenes = Array.isArray(scenes) ? scenes : [];
  const sceneIds = Array.isArray(block?.scene_ids) ? block.scene_ids.map((sceneId) => String(sceneId || "").trim()).filter(Boolean) : [];

  let blockScenes = [];

  if (sceneIds.length) {
    blockScenes = sceneIds
      .map((sceneId) => safeScenes.find((scene) => String(scene?.scene_id || "") === String(sceneId)))
      .filter((scene) => scene && (!blockId || !scene?.story_block_id || String(scene.story_block_id || "") === blockId));
  }

  if (!blockScenes.length) {
    blockScenes = safeScenes.filter((scene) => String(scene?.story_block_id || "") === blockId);
  }

  if (!blockScenes.length) return null;

  const starts = blockScenes.map((scene) => Number(scene?.start_sec || 0)).filter(Number.isFinite);
  const ends = blockScenes.map((scene) => Number(scene?.end_sec || 0)).filter(Number.isFinite);
  if (!starts.length || !ends.length) return null;

  return {
    ...block,
    start_sec: roundTimingSec(Math.min(...starts)),
    end_sec: roundTimingSec(Math.max(...ends)),
    scene_ids: blockScenes.map((scene) => scene.scene_id),
    scene_count: blockScenes.length,
  };
}

export function syncManualTimingStoryBlocksWithScenes(storyBlocks = [], scenes = []) {
  const normalizedBlocks = normalizeManualTimingStoryBlocks(storyBlocks);
  const safeScenes = Array.isArray(scenes) ? scenes : [];

  return normalizedBlocks.map((block) => {
    const derived = deriveStoryBlockRangeFromScenes(block, safeScenes);
    if (derived) return derived;
    return {
      ...block,
      scene_ids: [],
      start_sec: 0,
      end_sec: 0,
      scene_count: 0,
    };
  });
}

export function hydrateManualTimingScenesWithStoryBlocks(scenes = [], storyBlocks = []) {
  const blocks = normalizeManualTimingStoryBlocks(storyBlocks);
  const blockById = new Map(blocks.map((block) => [String(block.block_id), block]));
  const blockIdBySceneId = new Map();
  blocks.forEach((block) => {
    (Array.isArray(block.scene_ids) ? block.scene_ids : []).forEach((sceneId) => {
      const safeSceneId = String(sceneId || "").trim();
      if (safeSceneId && !blockIdBySceneId.has(safeSceneId)) blockIdBySceneId.set(safeSceneId, block.block_id);
    });
  });

  return (Array.isArray(scenes) ? scenes : []).map((scene) => {
    const sceneId = String(scene?.scene_id || "").trim();
    const storyBlockId = String(scene?.story_block_id || blockIdBySceneId.get(sceneId) || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id).trim() || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id;
    const block = blockById.get(storyBlockId) || blockById.get(MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id) || MANUAL_TIMING_UNKNOWN_STORY_BLOCK;
    const sceneIds = Array.isArray(block.scene_ids) ? block.scene_ids.map((id) => String(id || "").trim()).filter(Boolean) : [];
    const positionIdx = sceneIds.indexOf(sceneId);
    const computedPosition = positionIdx >= 0 && sceneIds.length
      ? `сцена ${positionIdx + 1} из ${sceneIds.length} в блоке`
      : "";
    return {
      ...scene,
      story_block_id: storyBlockId,
      story_block_title_ru: String(scene?.story_block_title_ru || block.title_ru || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.title_ru),
      story_block_color: normalizeStoryBlockColor(scene?.story_block_color || block.color || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.color),
      story_block_position_ru: String(scene?.story_block_position_ru || computedPosition),
      story_block_goal_ru: String(scene?.story_block_goal_ru || block.block_goal_ru || ""),
      story_block_reveal_ru: String(scene?.story_block_reveal_ru || block.block_reveal_ru || ""),
      story_block_emotion_ru: String(scene?.story_block_emotion_ru || block.block_emotion_ru || ""),
    };
  });
}

export function normalizeManualTimingMarkers(markers = [], durationSec = 0) {
  const duration = roundTimingSec(durationSec);
  const values = (Array.isArray(markers) ? markers : [])
    .map((value) => roundTimingSec(value))
    .filter((value) => Number.isFinite(value) && value >= 0 && (!duration || value <= duration));

  values.push(0);
  if (duration > 0) values.push(duration);

  const sorted = [...new Set(values.map((value) => value.toFixed(3)))]
    .map((value) => Number(value))
    .sort((a, b) => a - b);

  const deduped = [];
  for (const value of sorted) {
    const prev = deduped[deduped.length - 1];
    if (prev === undefined || Math.abs(value - prev) >= 0.001) deduped.push(value);
  }
  return deduped;
}

function buildScenePreserveMaps(existingScenes = []) {
  const byId = new Map();
  const byTimeline = new Map();
  (Array.isArray(existingScenes) ? existingScenes : []).forEach((scene) => {
    const id = String(scene?.scene_id || "").trim();
    if (id && !byId.has(id)) byId.set(id, scene);
    const key = `${roundTimingSec(scene?.start_sec).toFixed(3)}|${roundTimingSec(scene?.end_sec).toFixed(3)}`;
    if (key !== "0.000|0.000" && !byTimeline.has(key)) byTimeline.set(key, scene);
  });
  return { byId, byTimeline };
}

export function getRangeOverlap(aStart, aEnd, bStart, bEnd) {
  return Math.max(0, Math.min(aEnd, bEnd) - Math.max(aStart, bStart));
}

export function findBestSceneByTimelineOverlap(start, end, existingScenes = [], usedOldSceneIds = new Set()) {
  const newDuration = Math.max(0.001, end - start);
  let best = null;

  (Array.isArray(existingScenes) ? existingScenes : []).forEach((scene) => {
    const oldId = String(scene?.scene_id || "");
    if (oldId && usedOldSceneIds.has(oldId)) return;

    const oldStart = Number(scene?.start_sec || 0);
    const oldEnd = Number(scene?.end_sec || 0);
    if (!(oldEnd > oldStart)) return;

    const overlap = getRangeOverlap(start, end, oldStart, oldEnd);
    if (overlap <= 0) return;

    const oldDuration = Math.max(0.001, oldEnd - oldStart);
    const newRatio = overlap / newDuration;
    const oldRatio = overlap / oldDuration;

    const score = Math.max(newRatio, oldRatio);

    if (!best || score > best.score) {
      best = { scene, score, newRatio, oldRatio, overlap };
    }
  });

  if (!best) return null;

  // Порог: сцена считается той же самой, если overlap достаточно большой.
  // Для чуть сдвинутых границ подходит.
  // Для новой короткой сцены после split не нужно воровать чужой текст бездумно.
  if (best.score < 0.55) return null;

  return best;
}

const MANUAL_TIMING_SPLIT_REVIEW_NOTE_RU = "Новая сцена после разреза — проверь текст/смысл";

export function buildManualTimingScenesFromMarkers(markers = [], existingScenes = [], options = {}) {
  const duration = Number(options.durationSec || 0);
  const safeMarkers = normalizeManualTimingMarkers(markers, duration);
  const safeExistingScenes = Array.isArray(existingScenes) ? existingScenes : [];
  const { byId, byTimeline } = buildScenePreserveMaps(safeExistingScenes);
  const scenes = [];
  const usedOldSceneIds = new Set();
  const sceneCountChanged = safeExistingScenes.length !== Math.max(0, safeMarkers.length - 1);
  const canUseIdFallback = options.allowIdFallback === true && !sceneCountChanged;

  for (let i = 0; i < safeMarkers.length - 1; i += 1) {
    const start = roundTimingSec(safeMarkers[i]);
    const end = roundTimingSec(safeMarkers[i + 1]);
    if (!(end > start)) continue;
    const sceneId = `seg_${String(i + 1).padStart(2, "0")}`;
    const timelineKey = `${start.toFixed(3)}|${end.toFixed(3)}`;
    let old = null;
    let matchType = "none";
    let overlapMatch = null;

    const exactOld = byTimeline.get(timelineKey) || null;
    if (exactOld) {
      old = exactOld;
      matchType = "exact";
    } else {
      overlapMatch = findBestSceneByTimelineOverlap(start, end, safeExistingScenes, usedOldSceneIds);
      if (overlapMatch?.scene) {
        old = overlapMatch.scene;
        matchType = "overlap";
      }
    }

    if (!old && canUseIdFallback) {
      old = byId.get(sceneId) || null;
      if (old) matchType = "id";
    }

    const shouldCarryTechnicalFields = Boolean(old);
    const canCarryStoryFields =
      matchType === "exact"
      || matchType === "id"
      || (
        matchType === "overlap"
        && overlapMatch
        && overlapMatch.newRatio >= 0.82
        && overlapMatch.oldRatio >= 0.82
      );
    if (old?.scene_id) usedOldSceneIds.add(String(old.scene_id));
    old = old || {};
    const needsSplitReviewNote = matchType === "overlap" && !canCarryStoryFields;
    const section = normalizeManualTimingSection(shouldCarryTechnicalFields ? old.section : (i === 0 ? "intro" : "verse"));
    const defaults = getSectionDefaults(section);
    const route = normalizeManualTimingRoute(shouldCarryTechnicalFields ? old.route : defaults.route);
    const containsVocal = shouldCarryTechnicalFields && typeof old.contains_vocal === "boolean"
      ? old.contains_vocal
      : Boolean((shouldCarryTechnicalFields ? old.contains_vocal_assumption : undefined) ?? defaults.contains_vocal);
    const containsInstrumental = shouldCarryTechnicalFields && typeof old.contains_instrumental === "boolean"
      ? old.contains_instrumental
      : Boolean((shouldCarryTechnicalFields ? old.contains_instrumental_assumption : undefined) ?? !containsVocal);
    const userNoteRu = canCarryStoryFields
      ? String(old.user_note_ru || old.user_notes_ru || "")
      : (needsSplitReviewNote ? MANUAL_TIMING_SPLIT_REVIEW_NOTE_RU : "");
    const speechStart = shouldCarryTechnicalFields
      ? roundTimingSec(old.speech_start_sec ?? old.speechStartSec ?? start)
      : start;
    const speechEnd = shouldCarryTechnicalFields
      ? roundTimingSec(old.speech_end_sec ?? old.speechEndSec ?? end)
      : end;
    const preSilence = roundTimingSec(Math.max(0, speechStart - start));
    const postSilence = roundTimingSec(Math.max(0, end - speechEnd));

    scenes.push({
      scene_id: options.preserveSceneIds ? String(old.scene_id || sceneId) : sceneId,
      index: i + 1,
      start_sec: start,
      end_sec: end,
      duration_sec: roundTimingSec(end - start),
      speech_start_sec: speechStart,
      speech_end_sec: speechEnd,
      pre_silence_sec: preSilence,
      post_silence_sec: postSilence,
      section,
      route,
      contains_vocal: containsVocal,
      contains_vocal_assumption: Boolean((shouldCarryTechnicalFields ? old.contains_vocal_assumption : undefined) ?? containsVocal),
      contains_instrumental_assumption: Boolean((shouldCarryTechnicalFields ? old.contains_instrumental_assumption : undefined) ?? containsInstrumental),
      use_sound_suggestion: shouldCarryTechnicalFields ? Boolean(old.use_sound_suggestion || false) : false,
      energy: normalizeManualTimingEnergy(shouldCarryTechnicalFields ? old.energy : "mid"),
      quality: String(old.quality || "manual_draft"),
      boundary_reason: String(old.boundary_reason || "manual_marker"),
      transition_out: String(old.transition_out || "manual_cut"),
      story_time: canCarryStoryFields ? String(old.story_time || "") : "",
      scene_type: String(old.scene_type || ""),
      drama_hint: canCarryStoryFields ? String(old.drama_hint || "") : "",
      short_note: canCarryStoryFields ? String(old.short_note || "") : "",
      scene_goal_ru: canCarryStoryFields ? String(old.scene_goal_ru || "") : "",
      photo_prompt_hint_ru: canCarryStoryFields ? String(old.photo_prompt_hint_ru || "") : "",
      prompt_hint_ru: canCarryStoryFields ? String(old.prompt_hint_ru || old.photo_prompt_hint_ru || "") : "",
      story_position_ru: canCarryStoryFields ? String(old.story_position_ru || old.story_time || "") : "",
      user_note_ru: userNoteRu,
      source_phrase_ids: canCarryStoryFields ? normalizeManualTimingSourcePhraseIds(old.source_phrase_ids || old.sourcePhraseIds) : [],
      story_block_id: String(shouldCarryTechnicalFields ? (old.story_block_id || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id) : MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id),
      story_block_title_ru: shouldCarryTechnicalFields ? String(old.story_block_title_ru || "") : "",
      story_block_color: shouldCarryTechnicalFields ? String(old.story_block_color || "") : "",
      story_block_position_ru: canCarryStoryFields ? String(old.story_block_position_ru || "") : "",
      scene_role_in_block_ru: canCarryStoryFields ? String(old.scene_role_in_block_ru || "") : "",
      block_progress_ru: canCarryStoryFields ? String(old.block_progress_ru || "") : "",
      original_text: canCarryStoryFields ? String(old.original_text || "") : "",
      translated_text_ru: canCarryStoryFields ? String(old.translated_text_ru || "") : "",
      meaning_hint_ru: canCarryStoryFields ? String(old.meaning_hint_ru || "") : "",
      source_text_en: canCarryStoryFields ? String(old.source_text_en || "") : "",
      adapted_text_en: canCarryStoryFields ? String(old.adapted_text_en || "") : "",
      video_prompt: String(old.video_prompt || ""),
      negative_prompt: String(old.negative_prompt || ""),
      sound_prompt: String(old.sound_prompt || ""),
    });
  }

  return scenes;
}


export function validateSceneCoverage(scenes = [], audioDurationSec = 0) {
  const safeScenes = (Array.isArray(scenes) ? scenes : [])
    .map((scene, idx) => ({ ...scene, __idx: idx, start_sec: roundTimingSec(scene?.start_sec), end_sec: roundTimingSec(scene?.end_sec) }))
    .filter((scene) => Number.isFinite(scene.start_sec) && Number.isFinite(scene.end_sec))
    .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0));
  const duration = roundTimingSec(audioDurationSec);
  const tolerance = 0.01;
  const warnings = [];
  const errors = [];

  if (!safeScenes.length) {
    errors.push("Нет scenes для проверки покрытия аудио.");
    return { ok: false, warnings, errors };
  }

  if (Math.abs(Number(safeScenes[0].start_sec || 0)) > tolerance) {
    errors.push("Первая scene должна начинаться с 0.");
  }
  if (duration > 0 && Math.abs(Number(safeScenes[safeScenes.length - 1].end_sec || 0) - duration) > tolerance) {
    errors.push("Последняя scene должна заканчиваться на audio_duration_sec.");
  }

  safeScenes.forEach((scene, idx) => {
    if (!(Number(scene.end_sec) > Number(scene.start_sec))) {
      errors.push(`${scene.scene_id || `scene_${idx + 1}`}: end_sec должен быть больше start_sec.`);
    }
    if (idx === 0) return;
    const prev = safeScenes[idx - 1];
    const delta = roundTimingSec(Number(scene.start_sec || 0) - Number(prev.end_sec || 0));
    if (delta > tolerance) {
      errors.push(`Есть непокрытый участок аудио между scenes: ${prev.scene_id || idx} → ${scene.scene_id || idx + 1} (${delta.toFixed(3)} сек).`);
    } else if (delta < -tolerance) {
      errors.push(`Scenes перекрываются: ${prev.scene_id || idx} → ${scene.scene_id || idx + 1} (${Math.abs(delta).toFixed(3)} сек).`);
    }
  });

  const coveredSec = safeScenes.reduce((sum, scene) => sum + Math.max(0, Number(scene.end_sec || 0) - Number(scene.start_sec || 0)), 0);
  if (duration > 0 && Math.abs(roundTimingSec(coveredSec) - duration) > Math.max(tolerance, safeScenes.length * tolerance)) {
    warnings.push(`Сумма длительностей scenes (${roundTimingSec(coveredSec).toFixed(3)} сек) не равна полной длительности аудио (${duration.toFixed(3)} сек).`);
  }

  return { ok: errors.length === 0, warnings, errors };
}

function joinPhraseText(phrases = [], key = "text_en") {
  return phrases.map((phrase) => String(phrase?.[key] || "").trim()).filter(Boolean).join(" ");
}

export function buildGapAwareScenesFromAudioPhrases(audioPhrases = [], options = {}) {
  const phrases = normalizeManualTimingAudioPhrases(audioPhrases)
    .sort((a, b) => Number(a.start_sec || 0) - Number(b.start_sec || 0));
  if (!phrases.length) return [];

  const lastPhraseEnd = Math.max(...phrases.map((phrase) => Number(phrase.end_sec || 0)));
  const audioDurationSec = roundTimingSec(Math.max(Number(options.audioDurationSec || 0), lastPhraseEnd));
  const target = options.targetSceneDurationSec || {};
  const targetMin = Number(target.min || options.minSceneDurationSec || 4);
  const targetPreferred = Number(target.preferred || 6);
  const targetMax = Number(target.max || 9);
  const maxSceneDurationSec = Number(options.maxSceneDurationSec || 10);
  const projectKind = String(options.projectKind || "clip");
  const route = normalizeManualTimingRoute(options.route || "i2v");

  const groups = [];
  let current = [];
  const flush = () => {
    if (current.length) groups.push(current);
    current = [];
  };

  phrases.forEach((phrase, idx) => {
    if (!current.length) {
      current.push(phrase);
      return;
    }

    const first = current[0];
    const prev = current[current.length - 1];
    const nextBoundaryProbe = idx < phrases.length - 1
      ? (Number(phrase.end_sec || 0) + Number(phrases[idx + 1].start_sec || phrase.end_sec || 0)) / 2
      : audioDurationSec;
    const currentBoundaryProbe = (Number(prev.end_sec || 0) + Number(phrase.start_sec || prev.end_sec || 0)) / 2;
    const durationIfAdded = Math.max(0, nextBoundaryProbe - Number(first.start_sec || 0));
    const durationWithout = Math.max(0, currentBoundaryProbe - Number(first.start_sec || 0));
    const gapFromPrev = Math.max(0, Number(phrase.start_sec || 0) - Number(prev.end_sec || 0));
    const shouldSplit = (
      durationWithout >= targetMin
      && (
        durationIfAdded > maxSceneDurationSec
        || durationIfAdded > targetMax
        || (durationWithout >= targetPreferred && gapFromPrev >= 0.35)
      )
    );

    if (shouldSplit) flush();
    current.push(phrase);
  });
  flush();

  const boundaries = [0];
  for (let i = 0; i < groups.length - 1; i += 1) {
    const prevLast = groups[i][groups[i].length - 1];
    const nextFirst = groups[i + 1][0];
    const boundary = roundTimingSec((Number(prevLast.end_sec || 0) + Number(nextFirst.start_sec || prevLast.end_sec || 0)) / 2);
    boundaries.push(Math.max(boundaries[boundaries.length - 1], Math.min(audioDurationSec, boundary)));
  }
  boundaries.push(audioDurationSec);

  return groups.map((group, idx) => {
    const start = roundTimingSec(boundaries[idx]);
    const end = roundTimingSec(boundaries[idx + 1]);
    const speechStart = roundTimingSec(group[0]?.start_sec || start);
    const speechEnd = roundTimingSec(group[group.length - 1]?.end_sec || speechStart);
    const sourcePhraseIds = group.map((phrase) => phrase.phrase_id);
    const storyBlockId = `block_draft_${String(Math.floor(idx / 4) + 1).padStart(2, "0")}`;
    const duration = roundTimingSec(end - start);
    return {
      scene_id: `seg_${String(idx + 1).padStart(2, "0")}`,
      index: idx + 1,
      start_sec: start,
      end_sec: end,
      duration_sec: duration,
      speech_start_sec: speechStart,
      speech_end_sec: speechEnd,
      pre_silence_sec: roundTimingSec(Math.max(0, speechStart - start)),
      post_silence_sec: roundTimingSec(Math.max(0, end - speechEnd)),
      section: idx === 0 ? "intro" : "verse",
      route,
      contains_vocal: false,
      contains_vocal_assumption: false,
      contains_instrumental_assumption: true,
      use_sound_suggestion: false,
      energy: "mid",
      quality: "asr_gap_aware_story_draft",
      boundary_reason: "asr_gap_aware_midpoint_pause",
      transition_out: "manual_cut",
      story_time: "",
      scene_type: projectKind === "story" ? "story_scene_from_asr" : "clip_scene_from_asr",
      drama_hint: "",
      short_note: "Gap-aware ASR story scene — паузы включены в монтажную длительность.",
      source_phrase_ids: sourcePhraseIds,
      original_text: joinPhraseText(group, "text_en"),
      translated_text_ru: joinPhraseText(group, "text_ru"),
      meaning_hint_ru: joinPhraseText(group, "meaning_ru"),
      story_block_id: storyBlockId,
      story_block_title_ru: "",
      story_block_position_ru: "",
      scene_role_in_block_ru: "",
      block_progress_ru: "",
      scene_goal_ru: "",
      photo_prompt_hint_ru: "",
      prompt_hint_ru: "",
      story_position_ru: "",
      user_note_ru: "Собрано из ASR audio_phrases: scene duration включает паузы; video generation должна использовать duration_sec.",
      source_text_en: joinPhraseText(group, "text_en"),
      adapted_text_en: "",
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: "",
    };
  });
}

export function buildDraftStoryBlocksFromGapAwareScenes(scenes = []) {
  const safeScenes = Array.isArray(scenes) ? scenes : [];
  const colorPalette = ["#2563EB", "#7C3AED", "#059669", "#D97706", "#DC2626", "#0891B2"];
  const blockIds = [...new Set(safeScenes.map((scene) => String(scene?.story_block_id || "").trim()).filter(Boolean))];
  return blockIds.map((blockId, idx) => {
    const blockScenes = safeScenes.filter((scene) => String(scene?.story_block_id || "") === blockId);
    const sceneIds = blockScenes.map((scene) => scene.scene_id);
    return {
      block_id: blockId,
      title_ru: `Черновой story block ${idx + 1}`,
      summary_ru: "Черновой блок из ASR. Название, summary и драматургию должен заполнить LLM-pass.",
      block_goal_ru: "",
      block_reveal_ru: "",
      block_emotion_ru: "",
      color: colorPalette[idx % colorPalette.length],
      start_sec: roundTimingSec(blockScenes[0]?.start_sec || 0),
      end_sec: roundTimingSec(blockScenes[blockScenes.length - 1]?.end_sec || 0),
      scene_ids: sceneIds,
    };
  });
}

export function updateManualTimingSceneById(scenes = [], sceneId = "", patch = {}) {
  const safePatch = patch && typeof patch === "object" ? patch : {};
  return (Array.isArray(scenes) ? scenes : []).map((scene) => {
    if (String(scene?.scene_id || "") !== String(sceneId || "")) return scene;
    const next = { ...scene, ...safePatch };
    const sectionChanged = Object.prototype.hasOwnProperty.call(safePatch, "section");
    const routeChanged = Object.prototype.hasOwnProperty.call(safePatch, "route");
    const containsVocalChanged = Object.prototype.hasOwnProperty.call(safePatch, "contains_vocal");

    if (sectionChanged) {
      const defaults = getSectionDefaults(safePatch.section);
      next.section = defaults.section;
      if (!routeChanged) next.route = defaults.route;
      if (!containsVocalChanged) {
        next.contains_vocal = defaults.contains_vocal;
        next.contains_vocal_assumption = defaults.contains_vocal_assumption;
        next.contains_instrumental_assumption = defaults.contains_instrumental_assumption;
      }
    }

    if (containsVocalChanged) {
      next.contains_vocal = Boolean(safePatch.contains_vocal);
      next.contains_vocal_assumption = Boolean(safePatch.contains_vocal);
      next.contains_instrumental_assumption = !Boolean(safePatch.contains_vocal);
    }

    if (routeChanged) next.route = safePatch.route;
    next.section = normalizeManualTimingSection(next.section);
    next.route = normalizeManualTimingRoute(next.route);
    next.energy = normalizeManualTimingEnergy(next.energy);
    next.use_sound_suggestion = Boolean(next.use_sound_suggestion);
    return next;
  });
}



function toManualTimingBool(value, fallback = false) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "y", "on", "да"].includes(normalized)) return true;
    if (["false", "0", "no", "n", "off", "нет", ""].includes(normalized)) return false;
  }
  return Boolean(fallback);
}

function pickManualTimingText(scene = {}, keys = []) {
  for (const key of keys) {
    const value = scene?.[key];
    if (value !== undefined && value !== null && String(value).trim()) return String(value);
  }
  return "";
}

function normalizeManualTimingSceneForImport(scene = {}, idx = 0) {
  const rawSection = scene?.section || scene?.story_section || scene?.song_section || scene?.scene_section;
  const section = normalizeManualTimingSection(rawSection || (idx === 0 ? "intro" : "verse"));
  const defaults = getSectionDefaults(section);
  const route = normalizeManualTimingRoute(scene?.route || scene?.video_generation_route || scene?.renderMode || defaults.route);
  const containsVocal = toManualTimingBool(
    scene?.contains_vocal,
    toManualTimingBool(scene?.contains_vocal_assumption, defaults.contains_vocal)
  );
  const containsInstrumental = toManualTimingBool(
    scene?.contains_instrumental,
    toManualTimingBool(scene?.contains_instrumental_assumption, !containsVocal)
  );

  const start = roundTimingSec(scene?.start_sec ?? scene?.startSec ?? scene?.start ?? 0);
  const end = roundTimingSec(scene?.end_sec ?? scene?.endSec ?? scene?.end ?? start);

  return {
    scene_id: String(scene?.scene_id || scene?.sceneId || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene?.index || idx + 1),
    start_sec: start,
    end_sec: end,
    duration_sec: roundTimingSec(scene?.duration_sec ?? scene?.durationSec ?? (end - start)),
    speech_start_sec: roundTimingSec(scene?.speech_start_sec ?? scene?.speechStartSec ?? scene?.speech_start ?? start),
    speech_end_sec: roundTimingSec(scene?.speech_end_sec ?? scene?.speechEndSec ?? scene?.speech_end ?? end),
    pre_silence_sec: roundTimingSec(scene?.pre_silence_sec ?? scene?.preSilenceSec ?? Math.max(0, (scene?.speech_start_sec ?? scene?.speechStartSec ?? start) - start)),
    post_silence_sec: roundTimingSec(scene?.post_silence_sec ?? scene?.postSilenceSec ?? Math.max(0, end - (scene?.speech_end_sec ?? scene?.speechEndSec ?? end))),
    section,
    route,
    contains_vocal: containsVocal,
    contains_vocal_assumption: toManualTimingBool(scene?.contains_vocal_assumption, containsVocal),
    contains_instrumental_assumption: toManualTimingBool(scene?.contains_instrumental_assumption, containsInstrumental),
    use_sound_suggestion: toManualTimingBool(scene?.use_sound_suggestion, false),
    energy: normalizeManualTimingEnergy(scene?.energy || "mid"),
    quality: String(scene?.quality || "manual_draft"),
    boundary_reason: String(scene?.boundary_reason || "json_import"),
    transition_out: String(scene?.transition_out || "manual_cut"),
    story_time: String(scene?.story_time || ""),
    scene_type: String(scene?.scene_type || ""),
    drama_hint: pickManualTimingText(scene, ["drama_hint", "dramaHint", "scene_drama_ru"]),
    short_note: pickManualTimingText(scene, ["short_note", "shortNote", "note", "summary_ru"]),
    scene_goal_ru: pickManualTimingText(scene, ["scene_goal_ru", "sceneGoalRu", "goal_ru", "goal"]),
    photo_prompt_hint_ru: pickManualTimingText(scene, ["photo_prompt_hint_ru", "photoPromptHintRu", "prompt_hint_ru", "visual_hint_ru"]),
    prompt_hint_ru: pickManualTimingText(scene, ["prompt_hint_ru", "photo_prompt_hint_ru", "promptHintRu", "visual_hint_ru"]),
    story_position_ru: pickManualTimingText(scene, ["story_position_ru", "story_time", "storyPositionRu"]),
    user_note_ru: pickManualTimingText(scene, ["user_note_ru", "user_notes_ru", "userNoteRu", "note_ru", "director_note_ru"]),
    source_phrase_ids: normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds),
    story_block_id: pickManualTimingText(scene, ["story_block_id", "storyBlockId", "block_id"]) || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id,
    story_block_title_ru: pickManualTimingText(scene, ["story_block_title_ru", "storyBlockTitleRu", "block_title_ru"]),
    story_block_color: pickManualTimingText(scene, ["story_block_color", "storyBlockColor", "block_color"]),
    story_block_position_ru: pickManualTimingText(scene, ["story_block_position_ru", "storyBlockPositionRu", "block_position_ru"]),
    scene_role_in_block_ru: pickManualTimingText(scene, ["scene_role_in_block_ru", "sceneRoleInBlockRu", "role_in_block_ru", "scene_block_role_ru"]),
    block_progress_ru: pickManualTimingText(scene, ["block_progress_ru", "blockProgressRu", "progress_in_block_ru", "story_block_progress_ru"]),
    original_text: pickManualTimingText(scene, ["original_text", "originalText"]),
    translated_text_ru: pickManualTimingText(scene, ["translated_text_ru", "translatedTextRu", "translation_ru"]),
    meaning_hint_ru: pickManualTimingText(scene, ["meaning_hint_ru", "meaningHintRu", "meaning_ru"]),
    source_text_en: pickManualTimingText(scene, ["source_text_en", "sourceTextEn", "source_text"]),
    adapted_text_en: pickManualTimingText(scene, ["adapted_text_en", "adaptedTextEn", "adapted_text"]),
    video_prompt: String(scene?.video_prompt || ""),
    negative_prompt: String(scene?.negative_prompt || ""),
    sound_prompt: String(scene?.sound_prompt || ""),
  };
}

export function buildManualTimingAiSplitRequestJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);

  return {
    chatgpt_task: buildManualTimingChatGptTask(
      false,
      normalizeManualTimingAudioPhrases(safeProject.audio_phrases).some((phrase) => String(phrase.status || "") === "asr_raw" || String(phrase.source || "") === "asr")
    ),
    prep_template_meta: STORY_PREP_TEMPLATE_META,
    mode: "manual_clip_board",
    project_kind: String(safeProject.project_kind || "story"),
    format: String(safeProject.format || "9:16"),
    split_type: "ai_story_blocks_split_request",
    audio_duration_sec: Number(audio.duration_sec || 0),
    language_source: "en",
    language_helper: "ru",
    scene_duration_target_sec: {
      min: 4,
      max: 8,
      preferred: 6,
    },
    route_policy: {
      voiceover: "i2v",
      singer_lipsync: "ia2v",
      instrumental: "i2v",
    },
    global_hint: "Сделай новую разбивку по смысловым блокам. Если есть audio_phrases из ASR, не меняй start_sec/end_sec у audio_phrases: группируй phrase_id в gap-aware scenes через source_phrase_ids. scene.start_sec/end_sec должны покрывать все паузы и всю audio_duration_sec без дыр/overlap; speech_start_sec/speech_end_sec должны соответствовать первой/последней ASR-фразе в scene. Сначала придумай story_blocks; для каждого story_block заполни title_ru, summary_ru, block_goal_ru, block_reveal_ru, block_emotion_ru, color, start_sec, end_sec, scene_ids. Для каждой scene заполни original_text/adapted_text_en, translated_text_ru, meaning_hint_ru, story_block_id, story_block_title_ru, story_block_position_ru, scene_role_in_block_ru, block_progress_ru, scene_goal_ru, photo_prompt_hint_ru, prompt_hint_ru. Не заполнять video_prompt, negative_prompt, sound_prompt.",
    story_request_ru: "",
    story_blocks: [],
    audio_phrases: normalizeManualTimingAudioPhrases(safeProject.audio_phrases),
    scenes: [],
  };
}

export const MANUAL_TIMING_STORY_PASS_TASK_RU = "Это JSON после ASR + gap-aware scene builder. Не меняй audio_phrases, scene_id, start_sec, end_sec, speech_start_sec, speech_end_sec, source_phrase_ids. Нужно только пересобрать story_blocks по смыслу и заполнить перевод/смысловые поля сцен. video_prompt, negative_prompt, sound_prompt оставить пустыми. Если LLM считает, что scenes нужно объединить или разделить, он НЕ должен делать это сам. Он должен заполнить поле user_note_ru с предложением, а тайминги оставить без изменений.";

export function buildManualTimingStoryPassJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const exportJson = buildManualTimingExportJson(safeProject);

  return {
    chatgpt_task: MANUAL_TIMING_STORY_PASS_TASK_RU,
    split_type: "asr_gap_aware_story_pass",
    audio_duration_sec: Number(audio.duration_sec || exportJson.audio_duration_sec || 0),
    audio_phrases: exportJson.audio_phrases,
    scenes: exportJson.scenes.map((scene) => ({
      ...scene,
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: "",
    })),
    story_blocks: exportJson.story_blocks,
  };
}

function canonicalManualTimingJson(value) {
  if (Array.isArray(value)) return value.map(canonicalManualTimingJson);
  if (value && typeof value === "object") {
    return Object.keys(value).sort().reduce((acc, key) => {
      acc[key] = canonicalManualTimingJson(value[key]);
      return acc;
    }, {});
  }
  return value;
}

function sameManualTimingJson(a, b) {
  return JSON.stringify(canonicalManualTimingJson(a)) === JSON.stringify(canonicalManualTimingJson(b));
}

function isManualTimingStoryPassPayload(raw = {}) {
  const splitType = String(raw?.split_type || raw?.splitType || "");
  const task = typeof raw?.chatgpt_task === "string"
    ? raw.chatgpt_task
    : JSON.stringify(raw?.chatgpt_task || "");
  return splitType === "asr_gap_aware_story_pass" || task.includes("после ASR + gap-aware scene builder");
}

export function validateManualTimingStoryPassImport(raw = {}, baseProject = {}) {
  if (!isManualTimingStoryPassPayload(raw)) return { ok: true, errors: [] };

  const basePayload = buildManualTimingStoryPassJson(baseProject);
  const importedAudioPhrases = normalizeManualTimingAudioPhrases(raw?.audio_phrases || raw?.audioPhrases || []);
  const baseAudioPhrases = normalizeManualTimingAudioPhrases(basePayload.audio_phrases);
  const rawScenes = Array.isArray(raw?.scenes) ? raw.scenes : [];
  const importedScenes = rawScenes.map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const baseScenes = basePayload.scenes.map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx));
  const storyBlocks = normalizeManualTimingStoryBlocks(raw?.story_blocks || []);
  const errors = [];

  if (!sameManualTimingJson(importedAudioPhrases, baseAudioPhrases)) {
    errors.push("audio_phrases изменились — Story Pass не должен менять ASR-фразы.");
  }

  if (importedScenes.length !== baseScenes.length) {
    errors.push(`Количество scenes изменилось: было ${baseScenes.length}, стало ${importedScenes.length}.`);
  }

  const byId = new Map(importedScenes.map((scene) => [String(scene.scene_id || ""), scene]));
  baseScenes.forEach((baseScene) => {
    const sceneId = String(baseScene.scene_id || "");
    const nextScene = byId.get(sceneId);
    if (!nextScene) {
      errors.push(`scene_id изменился или удалён: ${sceneId}.`);
      return;
    }
    ["start_sec", "end_sec", "speech_start_sec", "speech_end_sec"].forEach((key) => {
      if (roundTimingSec(nextScene[key]) !== roundTimingSec(baseScene[key])) {
        errors.push(`${sceneId}: ${key} изменился (${baseScene[key]} → ${nextScene[key]}).`);
      }
    });
    if (!sameManualTimingJson(normalizeManualTimingSourcePhraseIds(nextScene.source_phrase_ids), normalizeManualTimingSourcePhraseIds(baseScene.source_phrase_ids))) {
      errors.push(`${sceneId}: source_phrase_ids изменились.`);
    }
    [
      "translated_text_ru",
      "meaning_hint_ru",
      "scene_goal_ru",
      "photo_prompt_hint_ru",
      "prompt_hint_ru",
      "scene_role_in_block_ru",
      "block_progress_ru",
    ].forEach((key) => {
      if (!String(nextScene[key] || "").trim()) errors.push(`${sceneId}: не заполнено поле ${key}.`);
    });
    ["video_prompt", "negative_prompt", "sound_prompt"].forEach((key) => {
      if (String(nextScene[key] || "").trim()) {
        errors.push(`${sceneId}: Story Pass не должен заполнять ${key}.`);
      }
    });
  });

  if (!storyBlocks.length) {
    errors.push("story_blocks не заполнены.");
  }
  storyBlocks.forEach((block) => {
    const blockId = String(block.block_id || "block_without_id");
    ["title_ru", "summary_ru", "block_goal_ru", "block_reveal_ru", "block_emotion_ru"].forEach((key) => {
      if (!String(block[key] || "").trim()) errors.push(`${blockId}: не заполнено ${key}.`);
    });
    if (!Array.isArray(block.scene_ids) || !block.scene_ids.length) errors.push(`${blockId}: не заполнены scene_ids.`);
  });

  return { ok: !errors.length, errors };
}

export function buildManualTimingSampleJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const audioPhrases = normalizeManualTimingAudioPhrases(safeProject.audio_phrases);
  const existingScenes = Array.isArray(safeProject.scenes) ? safeProject.scenes : [];
  const scenes = existingScenes.length ? buildManualTimingExportJson(safeProject).scenes : [
    {
      scene_id: "seg_01",
      index: 1,
      start_sec: 0,
      end_sec: 5,
      duration_sec: 5,
      section: "intro",
      route: "i2v",
      contains_vocal: false,
      contains_vocal_assumption: false,
      contains_instrumental_assumption: true,
      use_sound_suggestion: true,
      energy: "soft",
      quality: "manual_draft",
      boundary_reason: "json_import",
      transition_out: "manual_cut",
      story_time: "настоящее / прошлое / флешбэк",
      scene_type: "intro / performance / flashback / cutaway",
      drama_hint: "Коротко: что происходит драматургически.",
      short_note: "Короткая подпись для карточки сцены.",
      scene_goal_ru: "Зачем нужна сцена в сюжете.",
      photo_prompt_hint_ru: "Что учесть при создании фото.",
      prompt_hint_ru: "Что учесть в видео-промте.",
      story_position_ru: "Позиция в истории.",
      user_note_ru: "Твоя заметка: звук, фраза, визуал, что не забыть.",
      source_phrase_ids: [],
      story_block_id: "block_01",
      story_block_title_ru: "Смысловой блок 1",
      story_block_color: "#F59E0B",
      story_block_position_ru: "сцена 1 из 1 в блоке",
      scene_role_in_block_ru: "Какую функцию выполняет сцена внутри смыслового блока.",
      block_progress_ru: "Как эта сцена продвигает раскрытие блока шаг за шагом.",
      original_text: "Original English phrase for this audio segment.",
      translated_text_ru: "Русский перевод фразы.",
      meaning_hint_ru: "Смысл сцены для режиссёра.",
      source_text_en: "",
      adapted_text_en: "",
      video_prompt: "",
      negative_prompt: "",
      sound_prompt: ""
    }
  ];

  let storyBlocks;

  if (existingScenes.length) {
    storyBlocks = normalizeManualTimingStoryBlocks(safeProject.story_blocks);
  } else {
    const normalizedStoryBlocks = normalizeManualTimingStoryBlocks(safeProject.story_blocks);
    const hasCustomStoryBlocks = normalizedStoryBlocks.some((block) => String(block.block_id || "") !== MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id);
    storyBlocks = hasCustomStoryBlocks ? normalizedStoryBlocks : [
      {
        block_id: "block_01",
        title_ru: "Смысловой блок 1",
        summary_ru: "Кратко опиши общий смысл первого блока без привязки к конкретному сюжету.",
        block_goal_ru: "Опиши драматургическую задачу блока по фактическому аудио.",
        block_reveal_ru: "Опиши, какое новое понимание должен получить зритель к концу блока.",
        block_emotion_ru: "эмоциональная дуга блока по фактическому аудио",
        color: "#F59E0B",
        scene_ids: ["seg_01"],
        start_sec: 0,
        end_sec: 5,
      },
      { ...MANUAL_TIMING_UNKNOWN_STORY_BLOCK },
    ];
  }

  return {
    chatgpt_task: buildManualTimingChatGptTask(
      audioPhrases.some((phrase) => (
        String(phrase.status || "") === "needs_transcription"
        || String(phrase.assignment_status || "") === "unassigned"
      )),
      audioPhrases.some((phrase) => String(phrase.status || "") === "asr_raw" || String(phrase.source || "") === "asr")
    ),
    prep_template_meta: STORY_PREP_TEMPLATE_META,
    mode: "manual_clip_board",
    project_kind: String(safeProject.project_kind || "clip"),
    format: String(safeProject.format || "9:16"),
    split_type: existingScenes.length ? "manual_timing_export_for_chatgpt" : "manual_timing_template_for_chatgpt",
    audio_duration_sec: Number(audio.duration_sec || 0),
    global_hint: "Заполни/поправь story_blocks и scenes: перевод/смысл, scene_goal_ru, photo_prompt_hint_ru, prompt_hint_ru, scene_role_in_block_ru и block_progress_ru. Prompts оставь пустыми. Если есть audio_phrases из ASR, audio_phrases являются источником истины по речи: не меняй их start_sec/end_sec; scenes должны быть gap-aware, покрывать всю audio_duration_sec без дыр/overlap, а requestedDurationSec для будущей video generation должен соответствовать scene.duration_sec, а не speech duration. Если есть audio_phrases со status=needs_transcription или assignment_status=unassigned, не удаляй их: распознай/переведи и реши, куда назначить phrase_id; не меняй тайминги без явного указания пользователя.",
    story_blocks: storyBlocks,
    audio_phrases: audioPhrases,
    scenes,
  };
}

export function normalizeManualTimingProjectFromJson(raw = {}, baseProject = {}) {
  const safeRaw = raw && typeof raw === "object" ? raw : {};
  const safeBase = baseProject && typeof baseProject === "object" ? baseProject : {};
  const baseAudio = normalizeManualTimingAudio(safeBase.audio);
  const rawDuration = Number(safeRaw.audio_duration_sec ?? safeRaw.audioDurationSec ?? safeRaw.duration_sec ?? safeRaw.durationSec ?? safeRaw.audio?.duration_sec ?? 0);
  const durationSec = roundTimingSec(baseAudio.duration_sec || rawDuration || 0);
  const storyBlocks = normalizeManualTimingStoryBlocks(safeRaw.story_blocks || safeBase.story_blocks);
  const audioPhrases = normalizeManualTimingAudioPhrases(safeRaw.audio_phrases || safeRaw.audioPhrases || safeBase.audio_phrases);
  const rawScenes = Array.isArray(safeRaw.scenes) ? safeRaw.scenes : [];
  const importedScenes = rawScenes
    .map((scene, idx) => normalizeManualTimingSceneForImport(scene, idx))
    .filter((scene) => Number(scene.end_sec) > Number(scene.start_sec));

  const markerValues = [];
  importedScenes.forEach((scene) => {
    markerValues.push(scene.start_sec);
    markerValues.push(scene.end_sec);
  });
  if (durationSec > 0) {
    markerValues.push(0);
    markerValues.push(durationSec);
  }

  const markers = normalizeManualTimingMarkers(markerValues, durationSec || importedScenes[importedScenes.length - 1]?.end_sec || 0);
  const finalDuration = durationSec || markers[markers.length - 1] || 0;
  const markerScenes = markers.length >= 2
    ? buildManualTimingScenesFromMarkers(markers, importedScenes, { durationSec: finalDuration, preserveSceneIds: true })
    : importedScenes;
  const scenes = hydrateManualTimingScenesWithStoryBlocks(markerScenes, storyBlocks);

  return {
    ...getDefaultManualTimingNodeData(),
    ...safeBase,
    project_kind: String(safeRaw.project_kind || safeRaw.projectKind || safeBase.project_kind || "clip"),
    format: String(safeRaw.format || safeBase.format || "9:16"),
    audio: {
      ...baseAudio,
      duration_sec: finalDuration || baseAudio.duration_sec || 0,
      duration_ms: Math.round((finalDuration || baseAudio.duration_sec || 0) * 1000),
    },
    timing_status: "draft",
    markers,
    story_blocks: storyBlocks,
    audio_phrases: audioPhrases,
    scenes,
    selectedSceneId: scenes[0]?.scene_id || "",
    updatedAt: Date.now(),
  };
}


export function getManualTimingSceneDurationWarning(scene = {}) {
  const start = Number(scene?.start_sec || 0);
  const end = Number(scene?.end_sec || 0);
  const durationSec = Number(scene?.duration_sec || (end - start));
  if (!Number.isFinite(durationSec) || durationSec <= 0) return null;

  const route = String(scene?.route || "").trim().toLowerCase();

  if (durationSec < 3.0) {
    return {
      type: "too_short",
      severity: "warning",
      label: "короткая",
      text: "Сцена короткая: LTX может не успеть раскрыть движение. Использовать можно для быстрых монтажных ударов.",
    };
  }
  if (durationSec < 3.5) {
    return {
      type: "short_but_ok",
      severity: "soft",
      label: "коротковата",
      text: "Сцена коротковата, но допустима. Лучше использовать простое действие.",
    };
  }
  if (route === "ia2v" && durationSec > 6.5 && durationSec < 8.0) {
    return {
      type: "ia2v_long",
      severity: "soft",
      label: "ia2v длинновата",
      text: "Lip-sync сцена длинновата. Можно оставить, если фраза цельная, но лучше 3.5–6.5 сек.",
    };
  }
  if (route === "i2v" && durationSec > 7.5 && durationSec < 8.0) {
    return {
      type: "i2v_long",
      severity: "soft",
      label: "i2v длинновата",
      text: "i2v сцена длинновата. Можно оставить для атмосферы, но движение должно быть простым.",
    };
  }
  if (durationSec >= 8.0 && durationSec < 10.0) {
    return {
      type: "long",
      severity: "warning",
      label: "длинная",
      text: "Сцена длинная: лучше держать простое движение или разделить.",
    };
  }
  if (durationSec >= 10.0) {
    return {
      type: "very_long",
      severity: "danger",
      label: "очень длинная",
      text: "Очень длинная сцена: лучше разделить на несколько сцен.",
    };
  }

  return null;
}

export function buildManualTimingWarnings(project = {}) {
  const audio = normalizeManualTimingAudio(project.audio);
  const scenes = Array.isArray(project.scenes) ? project.scenes : [];
  const audioPhrases = normalizeManualTimingAudioPhrases(project.audio_phrases);
  const warnings = [];
  const duration = Number(audio.duration_sec || 0);
  const durationWarningBuckets = new Map();

  if (!scenes.length) warnings.push("Нет сегментов разметки.");
  if (scenes.length) {
    const coverage = validateSceneCoverage(scenes, duration);
    warnings.push(...coverage.errors, ...coverage.warnings);
  }

  scenes.forEach((scene, idx) => {
    const start = Number(scene.start_sec || 0);
    const end = Number(scene.end_sec || 0);
    const dur = Number(scene.duration_sec || (end - start));
    if (idx > 0) {
      const prevEnd = Number(scenes[idx - 1]?.end_sec || 0);
      if (Math.abs(start - prevEnd) > 0.01) warnings.push(`${scene.scene_id}: есть разрыв или наложение с предыдущей сценой.`);
    }
    if (dur < 1.0) warnings.push(`${scene.scene_id}: длительность меньше 1 сек.`);
    if (dur > 9.0) warnings.push(`${scene.scene_id}: длительность больше 9 сек — проверь, не склеены ли разные фразы.`);
    if (scene.route === "ia2v" && !scene.contains_vocal) warnings.push(`${scene.scene_id}: ia2v стоит на участке без вокала.`);
    if (["intro", "instrumental"].includes(String(scene.section || "")) && scene.route === "ia2v") warnings.push(`${scene.scene_id}: секция “${sectionLabelRu(scene.section)}”, но выбран route=ia2v.`);

    const durationWarning = getManualTimingSceneDurationWarning(scene);
    if (durationWarning) {
      const bucketKey = durationWarning.type;
      const bucket = durationWarningBuckets.get(bucketKey) || { label: durationWarning.label, items: [] };
      bucket.items.push(`${scene.scene_id} (${roundTimingSec(dur).toFixed(3)} сек)`);
      durationWarningBuckets.set(bucketKey, bucket);
    }
  });

  const assignedPhraseIds = new Set();
  scenes.forEach((scene) => {
    normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds).forEach((phraseId) => assignedPhraseIds.add(phraseId));
  });

  audioPhrases.forEach((phrase) => {
    if (String(phrase.source || "") === "asr" && !assignedPhraseIds.has(String(phrase.phrase_id || ""))) {
      warnings.push(`ASR-фраза не назначена ни одной scene через source_phrase_ids: ${phrase.phrase_id}`);
    }

    if (String(phrase.status || "") === "needs_transcription") {
      warnings.push(`Есть пропущенная фраза без расшифровки: ${phrase.phrase_id} (${phrase.start_sec.toFixed(2)}–${phrase.end_sec.toFixed(2)})`);
    }

    if (String(phrase.assignment_status || "") === "unassigned") {
      warnings.push(`Пропущенная фраза ещё не назначена сцене: ${phrase.phrase_id}`);
    }
  });

  const durationWarningOrder = [
    ["too_short", "Короткие сцены"],
    ["short_but_ok", "Коротковатые сцены"],
    ["ia2v_long", "Lip-sync сцены длинноваты"],
    ["i2v_long", "i2v сцены длинноваты"],
    ["long", "Длинные сцены"],
    ["very_long", "Очень длинные сцены"],
  ];
  durationWarningOrder.forEach(([type, title]) => {
    const bucket = durationWarningBuckets.get(type);
    if (!bucket?.items?.length) return;
    warnings.push(`${title}: ${bucket.items.join(", ")}`);
  });

  return warnings;
}

export function buildManualTimingExportJson(project = {}) {
  const safeProject = project && typeof project === "object" ? project : {};
  const audio = normalizeManualTimingAudio(safeProject.audio);
  const audio_phrases = normalizeManualTimingAudioPhrases(safeProject.audio_phrases);
  const normalizedStoryBlocks = normalizeManualTimingStoryBlocks(safeProject.story_blocks);
  const hydratedScenes = hydrateManualTimingScenesWithStoryBlocks(Array.isArray(safeProject.scenes) ? safeProject.scenes : [], normalizedStoryBlocks);
  const scenes = hydratedScenes.map((scene, idx) => ({
    scene_id: String(scene?.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene?.index || idx + 1),
    start_sec: roundTimingSec(scene?.start_sec),
    end_sec: roundTimingSec(scene?.end_sec),
    duration_sec: roundTimingSec(scene?.duration_sec || (Number(scene?.end_sec || 0) - Number(scene?.start_sec || 0))),
    speech_start_sec: roundTimingSec(scene?.speech_start_sec ?? scene?.speechStartSec ?? scene?.start_sec),
    speech_end_sec: roundTimingSec(scene?.speech_end_sec ?? scene?.speechEndSec ?? scene?.end_sec),
    pre_silence_sec: roundTimingSec(scene?.pre_silence_sec ?? scene?.preSilenceSec ?? Math.max(0, Number(scene?.speech_start_sec ?? scene?.start_sec ?? 0) - Number(scene?.start_sec ?? 0))),
    post_silence_sec: roundTimingSec(scene?.post_silence_sec ?? scene?.postSilenceSec ?? Math.max(0, Number(scene?.end_sec ?? 0) - Number(scene?.speech_end_sec ?? scene?.end_sec ?? 0))),
    section: normalizeManualTimingSection(scene?.section),
    route: normalizeManualTimingRoute(scene?.route),
    contains_vocal: Boolean(scene?.contains_vocal),
    contains_vocal_assumption: Boolean(scene?.contains_vocal_assumption ?? scene?.contains_vocal),
    contains_instrumental_assumption: Boolean(scene?.contains_instrumental_assumption ?? !scene?.contains_vocal),
    use_sound_suggestion: Boolean(scene?.use_sound_suggestion),
    energy: normalizeManualTimingEnergy(scene?.energy),
    quality: String(scene?.quality || (safeProject.timing_status === "confirmed" ? "manual_confirmed" : "manual_draft")),
    boundary_reason: String(scene?.boundary_reason || "manual_marker"),
    transition_out: String(scene?.transition_out || "manual_cut"),
    story_time: String(scene?.story_time || ""),
    scene_type: String(scene?.scene_type || ""),
    drama_hint: String(scene?.drama_hint || ""),
    short_note: String(scene?.short_note || ""),
    scene_goal_ru: String(scene?.scene_goal_ru || ""),
    photo_prompt_hint_ru: String(scene?.photo_prompt_hint_ru || ""),
    prompt_hint_ru: String(scene?.prompt_hint_ru || scene?.photo_prompt_hint_ru || ""),
    story_position_ru: String(scene?.story_position_ru || scene?.story_time || ""),
    user_note_ru: String(scene?.user_note_ru || ""),
    source_phrase_ids: normalizeManualTimingSourcePhraseIds(scene?.source_phrase_ids || scene?.sourcePhraseIds),
    story_block_id: String(scene?.story_block_id || MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id),
    story_block_title_ru: String(scene?.story_block_title_ru || ""),
    story_block_color: normalizeStoryBlockColor(scene?.story_block_color || ""),
    story_block_position_ru: String(scene?.story_block_position_ru || ""),
    scene_role_in_block_ru: String(scene?.scene_role_in_block_ru || ""),
    block_progress_ru: String(scene?.block_progress_ru || ""),
    original_text: String(scene?.original_text || ""),
    translated_text_ru: String(scene?.translated_text_ru || ""),
    meaning_hint_ru: String(scene?.meaning_hint_ru || ""),
    source_text_en: String(scene?.source_text_en || ""),
    adapted_text_en: String(scene?.adapted_text_en || ""),
    video_prompt: String(scene?.video_prompt || ""),
    negative_prompt: String(scene?.negative_prompt || ""),
    sound_prompt: String(scene?.sound_prompt || ""),
  }));

  const story_blocks = syncManualTimingStoryBlocksWithScenes(normalizedStoryBlocks, scenes).map(({ scene_count, ...block }) => block);
  const hasUnresolvedAudioPhrases = audio_phrases.some((phrase) => (
    String(phrase.status || "") === "needs_transcription"
    || String(phrase.assignment_status || "") === "unassigned"
  ));

  return {
    chatgpt_task: buildManualTimingChatGptTask(
      hasUnresolvedAudioPhrases,
      audio_phrases.some((phrase) => String(phrase.status || "") === "asr_raw" || String(phrase.source || "") === "asr")
    ),
    prep_template_meta: STORY_PREP_TEMPLATE_META,
    mode: "manual_clip_board",
    project_kind: String(safeProject.project_kind || "clip"),
    format: String(safeProject.format || "9:16"),
    split_type: safeProject.timing_status === "confirmed" ? "manual_timing_confirmed" : "manual_timing_draft",
    audio_duration_sec: Number(audio.duration_sec || 0),
    global_hint: safeProject.timing_status === "confirmed" ? "Manual timing confirmed by user" : "Manual timing draft",
    story_blocks,
    audio_phrases,
    scenes,
  };
}
