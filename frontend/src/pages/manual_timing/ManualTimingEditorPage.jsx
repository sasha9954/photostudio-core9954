import React, { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import "./ManualTimingEditorPage.css";
import {
  MANUAL_TIMING_ACTIVE_PROJECT_KEY,
  MANUAL_TIMING_ENERGY,
  MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
  MANUAL_TIMING_ROUTES,
  MANUAL_TIMING_SECTIONS,
  buildManualTimingAiSplitRequestJson,
  buildManualTimingExportJson,
  buildManualTimingSampleJson,
  buildManualTimingScenesFromMarkers,
  buildManualTimingWarnings,
  formatTimingSec,
  getDefaultManualTimingNodeData,
  getManualTimingSceneDurationWarning,
  hydrateManualTimingScenesWithStoryBlocks,
  normalizeManualTimingAudio,
  normalizeManualTimingMarkers,
  normalizeManualTimingProjectFromJson,
  normalizeManualTimingStoryBlocks,
  persistManualTimingProject,
  roundTimingSec,
  updateManualTimingSceneById,
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
    const raw = localStorage.getItem(MANUAL_TIMING_ACTIVE_PROJECT_KEY);
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
  const rawScenes = markers.length >= 2
    ? buildManualTimingScenesFromMarkers(markers, project.scenes || [], { durationSec: duration })
    : (Array.isArray(project.scenes) ? project.scenes : []);
  const scenes = hydrateManualTimingScenesWithStoryBlocks(rawScenes, story_blocks);
  return {
    ...project,
    audio,
    markers,
    story_blocks,
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

function getSceneStoryText(scene = {}) {
  const originalRaw = String(scene?.original_text || scene?.adapted_text_en || scene?.source_text_en || "").trim();
  const ruRaw = String(scene?.translated_text_ru || "").trim();
  const meaningRaw = String(scene?.meaning_hint_ru || "").trim();
  const hasAnyStoryText = Boolean(originalRaw || ruRaw || meaningRaw);
  const instrumental = isInstrumentalScene(scene) && !hasAnyStoryText;
  return {
    blockTitle: String(scene?.story_block_title_ru || "Без блока"),
    blockColor: String(scene?.story_block_color || "#64748B"),
    position: String(scene?.story_block_position_ru || "—"),
    original: originalRaw || "—",
    ru: ruRaw || "—",
    meaning: meaningRaw || (instrumental ? "Инструментальная / сюжетная сцена." : "—"),
  };
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
  const currentTimeRef = useRef(0);
  const isPlayingRef = useRef(false);
  const durationSecRef = useRef(0);
  const playStartGuardRef = useRef(null);

  const audio = normalizeManualTimingAudio(project.audio);
  const durationSec = Number(audio.duration_sec || 0);
  const markers = useMemo(() => normalizeManualTimingMarkers(project.markers, durationSec), [project.markers, durationSec]);
  const storyBlocks = useMemo(() => normalizeManualTimingStoryBlocks(project.story_blocks), [project.story_blocks]);
  const scenes = Array.isArray(project.scenes) ? project.scenes : [];
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
  const lastCutSec = getLastInternalMarker(markers);
  const candidateDurationSec = Math.max(0, Number(currentTime || 0) - Number(lastCutSec || 0));
  const selectedSceneStartSec = selectedScene ? Number(selectedScene.start_sec || 0) : 0;
  const selectedSceneEndSec = selectedScene ? Number(selectedScene.end_sec || 0) : 0;
  const selectedSceneDurationSec = selectedScene
    ? Number(selectedScene.duration_sec || (selectedSceneEndSec - selectedSceneStartSec))
    : 0;
  const selectedSceneDurationWarning = selectedScene
    ? getManualTimingSceneDurationWarning(selectedScene)
    : null;
  const selectedBoundarySec = selectedSceneEndSec;
  const selectedBoundaryIsInternal = selectedSceneIndex >= 0 && selectedSceneIndex < scenes.length - 1;
  const playheadPercent = durationSec > 0 ? Math.max(0, Math.min(100, (Number(currentTime || 0) / durationSec) * 100)) : 0;
  const lastCutPercent = durationSec > 0 ? Math.max(0, Math.min(100, (Number(lastCutSec || 0) / durationSec) * 100)) : 0;
  const candidateWidthPercent = durationSec > 0 ? Math.max(0, Math.min(100 - lastCutPercent, ((Number(currentTime || 0) - Number(lastCutSec || 0)) / durationSec) * 100)) : 0;
  const openTailSceneId = project.timing_status === "confirmed" ? "" : scenes[scenes.length - 1]?.scene_id || "";
  const candidateDurationLabel = candidateDurationSec > 0.001 ? formatTimingSec(candidateDurationSec) : "—";
  const storyBlockSummaries = useMemo(() => storyBlocks.map((block) => {
    const sceneIds = Array.isArray(block.scene_ids) ? block.scene_ids : [];
    const count = sceneIds.length || scenes.filter((scene) => String(scene.story_block_id || "") === String(block.block_id || "")).length;
    return { ...block, sceneCount: count };
  }).filter((block) => String(block.block_id || "") !== MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || block.sceneCount > 0), [storyBlocks, scenes]);

  useEffect(() => {
    currentTimeRef.current = Number(currentTime || 0);
  }, [currentTime]);

  useEffect(() => {
    durationSecRef.current = Number(durationSec || 0);
  }, [durationSec]);

  const persist = (nextProject) => {
    const safeProject = { ...nextProject, updatedAt: Date.now() };
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

  const rebuildFromMarkers = (nextMarkers, existingScenes = scenes, extraPatch = {}) => {
    const safeMarkers = normalizeManualTimingMarkers(nextMarkers, durationSec);
    const nextRawScenes = buildManualTimingScenesFromMarkers(safeMarkers, existingScenes, { durationSec });
    const nextScenes = hydrateManualTimingScenesWithStoryBlocks(nextRawScenes, project.story_blocks);
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
    rebuildFromMarkers(nextMarkers, scenes, { selectedSceneId: selectedScene.scene_id, timing_status: "draft" });
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
    const nextScenes = updateManualTimingSceneById(scenes, sceneId, patch);
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

  const onConfirmTiming = () => {
    const nextScenes = scenes.map((scene) => ({ ...scene, quality: "manual_confirmed" }));
    persist({ ...project, scenes: nextScenes, timing_status: "confirmed" });
  };

  const onCopyTimingJson = async () => {
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
    downloadJsonPayload(buildManualTimingExportJson(project), "manual_timing_export.json");
  };

  const onDownloadSampleJson = () => {
    downloadJsonPayload(buildManualTimingSampleJson(project), "manual_timing_sample_for_chatgpt.json");
  };

  const onDownloadAiSplitRequestJson = () => {
    downloadJsonPayload(buildManualTimingAiSplitRequestJson(project), "manual_timing_ai_split_request.json");
  };

  const onCopyAiSplitRequestJson = async () => {
    const payload = buildManualTimingAiSplitRequestJson(project);
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
      setCopyStatus("JSON для AI-разбивки скопирован");
      window.setTimeout(() => setCopyStatus(""), 1600);
    } catch {
      setCopyStatus("Не удалось скопировать JSON для AI-разбивки");
    }
  };

  const applyImportedTimingJson = (rawObject) => {
    const nextProject = normalizeManualTimingProjectFromJson(rawObject, project);
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
    const nextScenes = nextMarkers.length ? hydrateManualTimingScenesWithStoryBlocks(buildManualTimingScenesFromMarkers(nextMarkers, [], { durationSec }), project.story_blocks) : [];
    persist({
      ...project,
      markers: nextMarkers,
      story_blocks: normalizeManualTimingStoryBlocks(project.story_blocks),
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
    persist({ ...project, audio: nextAudio, markers: nextMarkers, story_blocks: normalizeManualTimingStoryBlocks(project.story_blocks), scenes: nextScenes, selectedSceneId: project.selectedSceneId || nextScenes[0]?.scene_id || "", timing_status: project.timing_status === "empty" ? "draft" : project.timing_status });
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

  const markerPercents = useMemo(() => {
    if (!(durationSec > 0)) return [];
    return normalizeManualTimingMarkers(project.markers, durationSec).map((marker) => ({
      value: marker,
      left: `${Math.max(0, Math.min(100, (Number(marker) / durationSec) * 100))}%`,
    }));
  }, [project.markers, durationSec]);

  return (
    <>
    <div className="manualTimingPage pageCard">
      <h1 className="pageTitle">Тайминг песни</h1>
      <div className="manualTimingMetaGrid">
        <div><b>Файл:</b> {audio.filename || "аудио не выбрано"}</div>
        <div><b>Длительность:</b> {formatTimingSec(durationSec)}</div>
        <div><b>Курсор:</b> {formatTimingSec(currentTime)}</div>
        <div><b>Сцен:</b> {scenes.length}</div>
        <div><b>Статус:</b> {STATUS_LABELS[project.timing_status] || String(project.timing_status || "пусто")}</div>
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
            <div>серое — ещё не размечено · цветное — готовые отрезки · линия — текущее место · двойной клик по сцене — быстрая правка</div>
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
            {markerPercents.map((marker) => <div key={`player-marker-${marker.value}`} className="manualTimingPlayerMarker" style={{ left: marker.left }} title={formatTimingSec(marker.value)} />)}
            <div className="manualTimingLastCutLine" style={{ left: `${lastCutPercent}%` }} title={`Старт следующего отрезка: ${formatTimingSec(lastCutSec)}`} />
            <div className="manualTimingPlayhead" style={{ left: `${playheadPercent}%` }}>
              <span>{formatTimingSec(currentTime)}</span>
            </div>
          </div>
          <div className="manualTimingPlayerLegend">
            <span><i className="legendCut" /> отрезано</span>
            <span><i className="legendTail" /> ещё не отрезано</span>
            <span><i className="legendCandidate" /> следующий отрезок</span>
          </div>
        </div>

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
          <button className="clipSB_btn clipSB_btnSecondary" onClick={onDeleteLastCut} disabled={markers.length <= 2}>Удалить последний</button>
          <button className="clipSB_btn clipSB_btnSecondary" onClick={onConfirmTiming} disabled={!scenes.length}>Подтвердить</button>
          <button className="clipSB_btn clipSB_btnSecondary" onClick={onCopyTimingJson}>Скопировать JSON</button>
          <button className="clipSB_btn clipSB_btnDanger" onClick={onReset}>Сбросить</button>
        </div>

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
          </div>
          {selectedScene ? <div className="manualTimingSceneTextPanel">
            <div className="manualTimingSceneTextHeader">
              <strong>Текст и смысл сцены</strong>
              <span className="manualTimingBlockBadge" style={{ "--story-block-color": selectedSceneText.blockColor }}>Story block: {selectedSceneText.blockTitle}</span>
            </div>
            <div className="manualTimingSceneTextGrid">
              <div><span>Position</span><strong>{selectedSceneText.position || "—"}</strong></div>
              <div><span>Original</span><p>{selectedSceneText.original}</p></div>
              <div><span>RU</span><p>{selectedSceneText.ru}</p></div>
              <div><span>Meaning</span><p>{selectedSceneText.meaning}</p></div>
            </div>
          </div> : null}
        </div>

        <div className="manualTimingNudgePanel">
          <div className="manualTimingNudgeTitle">Микро-доводчик выбранной границы</div>
          <div className="manualTimingNudgeButtons">
            {NUDGE_STEPS.map((step) => <button
              key={step}
              className="clipSB_btn clipSB_btnSecondary"
              disabled={!selectedBoundaryIsInternal}
              onClick={() => nudgeSelectedBoundary(step)}
            >{step > 0 ? `+${step.toFixed(2)}` : step.toFixed(2)} c</button>)}
          </div>
          <div className="manualTimingNudgeHint">Выбери сцену ниже. Доводчик двигает её конечную границу. Если двигать раннюю границу, следующие разрезы сдвинутся на такое же расстояние.</div>
        </div>

        <div className="manualTimingJsonPanel">
          <div className="manualTimingJsonHeader">
            <strong>JSON разметки</strong>
            <span>Загрузи JSON с таймингами — сцены сразу появятся на шкале, а подсказки попадут в строки сцен.</span>
          </div>
          <div className="manualTimingJsonActions">
            <label className="clipSB_btn clipSB_btnSecondary manualTimingFileBtn">
              Загрузить JSON файл
              <input type="file" accept=".json,application/json" onChange={onImportJsonFile} />
            </label>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={() => setIsJsonImportOpen((value) => !value)}>
              {isJsonImportOpen ? "Скрыть поле JSON" : "Вставить JSON текстом"}
            </button>
            <button className="clipSB_btn clipSB_btnPrimary" onClick={onImportTimingJson} disabled={!jsonImportText.trim()}>Применить JSON</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onDownloadTimingJson}>Скачать текущий JSON</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onDownloadSampleJson}>Скачать JSON образец</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onDownloadAiSplitRequestJson}>Скачать JSON для AI-разбивки</button>
            <button className="clipSB_btn clipSB_btnSecondary" onClick={onCopyAiSplitRequestJson}>Скопировать JSON для AI</button>
          </div>
          {isJsonImportOpen ? <textarea
            className="manualTimingJsonTextarea"
            value={jsonImportText}
            placeholder="Вставь сюда JSON с scenes/start_sec/end_sec/route/section/user_note_ru..."
            onChange={(e) => setJsonImportText(e.target.value)}
          /> : null}
        </div>

        {copyStatus ? <div className="manualTimingCopyStatus">{copyStatus}</div> : null}
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

      {warnings.length ? <section className="manualTimingWarnings">
        <strong>Проверка:</strong>
        {warnings.map((warning, idx) => <div key={`${warning}-${idx}`}>• {warning}</div>)}
      </section> : null}

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
              <label>Маршрут<select value={scene.route || "i2v"} onChange={(e) => updateScene(scene.scene_id, { route: e.target.value })}>{MANUAL_TIMING_ROUTES.map((item) => <option key={item} value={item}>{ROUTE_LABELS[item] || item}</option>)}</select></label>
              <label>Энергия<select value={scene.energy || "mid"} onChange={(e) => updateScene(scene.scene_id, { energy: e.target.value })}>{MANUAL_TIMING_ENERGY.map((item) => <option key={item} value={item}>{ENERGY_LABELS[item] || item}</option>)}</select></label>
              <label className="manualTimingCheck"><input type="checkbox" checked={Boolean(scene.contains_vocal)} onChange={(e) => updateScene(scene.scene_id, { contains_vocal: e.target.checked })} /> вокал</label>
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
              {MANUAL_TIMING_ROUTES.map((item) => <option key={item} value={item}>{ROUTE_LABELS[item] || item}</option>)}
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
            placeholder="Например: поезд начало, Привоз, банда во дворе, милиция, звук мигалок..."
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
