import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { getAccountScopedStorageKey } from "../clip_nodes/manualProjectBackup.js";
import {
  MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
  normalizeManualTimingAudio,
  persistManualTimingProject,
  readManualTimingProjectForNode,
} from "../clip_nodes/manual_timing/manualTimingDomain.js";
import "./PodcastAudioComposerPage.css";

const MAX_TRACK_COUNT = 4;

const OPERATION_LABELS = {
  cut_point: "Разрез",
  insert_silence: "Вставить тишину",
  replace_with_silence: "Заменить тишиной",
  cut_region: "Вырезать участок",
  insert_from_track: "Вставить из выбранной дорожки",
  replace_with_track_fragment: "Заменить выбранным фрагментом",
};

function readAudioFileMetadata(file) {
  return new Promise((resolve) => {
    if (!file) {
      resolve({ duration_sec: 0 });
      return;
    }

    const url = URL.createObjectURL(file);
    const audioEl = new Audio();
    let settled = false;

    const finish = (durationSec = 0) => {
      if (settled) return;
      settled = true;
      const safeDurationSec = Number.isFinite(Number(durationSec)) && Number(durationSec) > 0
        ? Number(Number(durationSec).toFixed(3))
        : 0;
      try {
        audioEl.removeAttribute("src");
        audioEl.load();
      } catch {}
      URL.revokeObjectURL(url);
      resolve({ duration_sec: safeDurationSec });
    };

    audioEl.preload = "metadata";
    audioEl.onloadedmetadata = () => finish(audioEl.duration || 0);
    audioEl.onerror = () => finish(0);
    audioEl.src = url;
  });
}

function createTrack(index, existing = {}) {
  const id = `podcast_${index}`;
  return {
    url: "",
    filename: "",
    duration_sec: 0,
    ...existing,
    id,
    label: existing?.label || `Подкаст ${index}`,
    gain_db: Number(existing?.gain_db ?? 0),
  };
}

function createDefaultProject(sourceNodeId = "", mainAudio = {}) {
  const safeAudio = normalizeManualTimingAudio(mainAudio);
  return {
    version: 1,
    sourceNodeId,
    main_audio: {
      url: safeAudio.url,
      filename: safeAudio.filename,
      duration_sec: Number(safeAudio.duration_sec || 0),
    },
    tracks: [],
    selection: {
      target_start_sec: 0,
      target_end_sec: 0,
      source_track_id: "",
      source_start_sec: 0,
      source_end_sec: 0,
      nudge_step_sec: 0.05,
      silence_duration_sec: 2.0,
    },
    operations: [],
    final_audio_url: "",
    final_audio_duration_sec: 0,
    updatedAt: Date.now(),
  };
}

function normalizeNumber(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

function normalizeComposerProject(raw = {}, sourceNodeId = "", mainAudio = {}) {
  const defaults = createDefaultProject(sourceNodeId, mainAudio);
  const rawTracks = Array.isArray(raw?.tracks) ? raw.tracks : [];
  const selection = raw?.selection && typeof raw.selection === "object" ? raw.selection : {};
  return {
    ...defaults,
    ...(raw && typeof raw === "object" ? raw : {}),
    version: 1,
    sourceNodeId,
    main_audio: {
      ...defaults.main_audio,
      ...(raw?.main_audio || {}),
    },
    tracks: rawTracks
      .map((track, index) => createTrack(getTrackNumber(track?.id) || index + 1, track || {}))
      .filter((track) => !isEmptyTrack(track))
      .slice(0, MAX_TRACK_COUNT),
    selection: {
      ...defaults.selection,
      ...selection,
      target_start_sec: normalizeNumber(selection.target_start_sec, 0),
      target_end_sec: normalizeNumber(selection.target_end_sec, 0),
      source_start_sec: normalizeNumber(selection.source_start_sec, 0),
      source_end_sec: normalizeNumber(selection.source_end_sec, 0),
      source_track_id: String(selection.source_track_id || ""),
      nudge_step_sec: normalizeNumber(selection.nudge_step_sec, defaults.selection.nudge_step_sec),
      silence_duration_sec: normalizeNumber(selection.silence_duration_sec, defaults.selection.silence_duration_sec),
    },
    operations: Array.isArray(raw?.operations) ? raw.operations : [],
    final_audio_url: String(raw?.final_audio_url || ""),
    final_audio_duration_sec: normalizeNumber(raw?.final_audio_duration_sec, 0),
    updatedAt: raw?.updatedAt || Date.now(),
  };
}

function getComposerStorageKey(sourceNodeId = "") {
  return getAccountScopedStorageKey(`podcast_audio_composer_project:${String(sourceNodeId || "default").trim() || "default"}`);
}

function readComposerProject(sourceNodeId = "") {
  try {
    const raw = localStorage.getItem(getComposerStorageKey(sourceNodeId));
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function persistComposerProject(project = {}) {
  try {
    localStorage.setItem(getComposerStorageKey(project.sourceNodeId), JSON.stringify(project));
  } catch {}
}

function downloadJson(project = {}) {
  const blob = new Blob([JSON.stringify(project, null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `podcast_audio_composer_${project.sourceNodeId || "project"}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function clampSeconds(value, fallback = 0) {
  return Math.max(0, normalizeNumber(value, fallback));
}

function roundSeconds(value, digits = 3) {
  return Number(clampSeconds(value).toFixed(digits));
}

function normalizeRange(start, end) {
  const safeStart = roundSeconds(start);
  const safeEnd = roundSeconds(end);
  return safeEnd >= safeStart
    ? { start: safeStart, end: safeEnd }
    : { start: safeEnd, end: safeStart };
}

function formatDuration(value) {
  const sec = Number(value || 0);
  return Number.isFinite(sec) && sec > 0 ? `${sec.toFixed(2)} с` : "0.00 с";
}

function formatTimestamp(value) {
  return roundSeconds(value).toFixed(3);
}

function getTrackNumber(trackId = "") {
  const match = String(trackId).match(/podcast_(\d+)/);
  return match ? Number(match[1]) : 0;
}

function isEmptyTrack(track = {}) {
  return !track?.url && !track?.filename;
}

function AudioEditTimeline({
  durationSec = 0,
  currentTimeSec = 0,
  selectionStartSec = 0,
  selectionEndSec = 0,
  operations = [],
  compact = false,
  onSeek,
  onSetSelectionStart,
  onSetSelectionEnd,
}) {
  const safeDuration = Math.max(1, normalizeNumber(durationSec, 0));
  const safeCurrent = Math.min(safeDuration, roundSeconds(currentTimeSec));
  const range = normalizeRange(selectionStartSec, selectionEndSec);
  const tickStep = safeDuration > 180 ? 30 : safeDuration > 90 ? 15 : safeDuration > 30 ? 5 : 1;
  const ticks = [];
  for (let second = 0; second <= Math.ceil(safeDuration); second += tickStep) {
    ticks.push(Math.min(second, safeDuration));
  }
  if (!ticks.includes(safeDuration)) ticks.push(safeDuration);

  const toPercent = (value) => `${Math.min(100, Math.max(0, (normalizeNumber(value, 0) / safeDuration) * 100))}%`;
  const seekFromEvent = (event) => {
    if (!onSeek) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = rect.width > 0 ? (event.clientX - rect.left) / rect.width : 0;
    onSeek(roundSeconds(Math.min(safeDuration, Math.max(0, ratio * safeDuration))));
  };

  return (
    <div className={`audioEditTimeline ${compact ? "compact" : ""}`}>
      <div className="audioEditTimelineBar" role="button" tabIndex={0} onClick={seekFromEvent} onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") seekFromEvent(event);
      }}>
        {range.end > range.start ? (
          <div className="audioEditSelection" style={{ left: toPercent(range.start), width: toPercent(range.end - range.start) }} />
        ) : null}
        {operations.map((operation) => {
          const start = normalizeNumber(operation.target_start_sec, 0);
          const duration = operation.type === "cut_point" ? 0 : Math.max(0.04, normalizeNumber(operation.duration_sec, 0) || (normalizeNumber(operation.target_end_sec, start) - start));
          const className = `audioEditOperation ${operation.type || "unknown"}`;
          return <div key={operation.id} className={className} style={{ left: toPercent(start), width: operation.type === "cut_point" ? "2px" : toPercent(duration) }} title={OPERATION_LABELS[operation.type] || operation.type} />;
        })}
        {ticks.map((tick) => (
          <div key={tick} className="audioEditTick" style={{ left: toPercent(tick) }}>
            <i />
            <span>{formatTimestamp(tick)}</span>
          </div>
        ))}
        <div className="audioEditPlayhead" style={{ left: toPercent(safeCurrent) }}>
          <b>{formatTimestamp(safeCurrent)}</b>
        </div>
      </div>
      <div className="audioEditTimelineActions">
        <button type="button" onClick={() => onSetSelectionStart?.(safeCurrent)}>Поставить start по курсору</button>
        <button type="button" onClick={() => onSetSelectionEnd?.(safeCurrent)}>Поставить end по курсору</button>
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
  const sourceProject = storedManualTimingProject || {};
  const sourceAudio = stateAudio.url ? stateAudio : normalizeManualTimingAudio(sourceProject?.audio);
  const mainAudioRef = useRef(null);
  const sourceAudioRefs = useRef({});
  const [project, setProject] = useState(() => normalizeComposerProject(readComposerProject(sourceNodeId), sourceNodeId, sourceAudio));
  const [message, setMessage] = useState("");
  const [mainCurrentTimeSec, setMainCurrentTimeSec] = useState(0);
  const [trackCurrentTimes, setTrackCurrentTimes] = useState({});

  useEffect(() => {
    persistComposerProject(project);
  }, [project]);

  const selectedTrack = project.tracks.find((track) => track.id === project.selection.source_track_id) || null;
  const targetRange = normalizeRange(project.selection.target_start_sec, project.selection.target_end_sec);
  const sourceRange = normalizeRange(project.selection.source_start_sec, project.selection.source_end_sec);
  const editJson = JSON.stringify(project, null, 2);

  const updateProject = (updater) => {
    setProject((current) => {
      const next = typeof updater === "function" ? updater(current) : { ...current, ...updater };
      return { ...next, updatedAt: Date.now() };
    });
  };

  const updateSelection = (patch) => updateProject((current) => ({
    ...current,
    selection: { ...current.selection, ...patch },
  }));

  const setTargetStart = (value) => updateSelection({
    target_start_sec: roundSeconds(value),
    target_end_sec: Math.max(roundSeconds(value), roundSeconds(project.selection.target_end_sec)),
  });

  const setTargetEnd = (value) => updateSelection({
    target_start_sec: Math.min(roundSeconds(value), roundSeconds(project.selection.target_start_sec)),
    target_end_sec: roundSeconds(value),
  });

  const seekMainAudio = (timeSec) => {
    const nextTime = roundSeconds(timeSec);
    setMainCurrentTimeSec(nextTime);
    if (mainAudioRef.current) mainAudioRef.current.currentTime = nextTime;
  };

  const seekTrackAudio = (trackId, timeSec) => {
    const nextTime = roundSeconds(timeSec);
    setTrackCurrentTimes((current) => ({ ...current, [trackId]: nextTime }));
    if (sourceAudioRefs.current[trackId]) sourceAudioRefs.current[trackId].currentTime = nextTime;
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

  const onApply = () => {
    if (!project.final_audio_url) {
      setMessage("Сначала нужно собрать итоговое аудио. В v1 пока сохраняется только edit JSON.");
      return;
    }

    const baseProject = sourceProject && typeof sourceProject === "object" ? sourceProject : {};
    const nextAudio = {
      url: project.final_audio_url,
      filename: "podcast_composed_audio.mp3",
      duration_sec: Number(project.final_audio_duration_sec || 0),
      duration_ms: Number(project.final_audio_duration_sec || 0) > 0 ? Math.round(Number(project.final_audio_duration_sec || 0) * 1000) : 0,
    };
    const nextProject = {
      ...baseProject,
      nodeId: sourceNodeId,
      sourceNodeId,
      audio: nextAudio,
      audio_source: "podcast_composer",
      podcast_composer_project: project,
      timing_status: "draft",
      markers: nextAudio.duration_sec > 0 ? [0, nextAudio.duration_sec] : [],
      story_blocks: [MANUAL_TIMING_UNKNOWN_STORY_BLOCK],
      audio_phrases: [],
      audio_words: [],
      asr_phrase_map: null,
      scenes: [],
      selectedSceneId: "",
      updatedAt: Date.now(),
    };
    persistManualTimingProject(nextProject);
    navigate("/studio/storyboard", {
      state: {
        focusManualTimingNodeId: sourceNodeId,
        manualBoardSkipOpenStateReason: "podcast_apply_to_node",
        closeManualDirectorBoard: true,
        closeLegacyScenarioEditors: true,
      },
    });
  };

  const updateTrack = (trackId, patch) => updateProject((current) => ({
    ...current,
    tracks: current.tracks.map((track) => track.id === trackId ? { ...track, ...patch } : track),
  }));

  const onTrackUpload = async (trackId, file) => {
    if (!file) return;
    const metadata = await readAudioFileMetadata(file);
    const url = URL.createObjectURL(file);
    updateTrack(trackId, {
      url,
      filename: file.name || "audio",
      duration_sec: Number(metadata.duration_sec || 0),
    });
  };

  const addTrack = () => {
    updateProject((current) => {
      if (current.tracks.length >= MAX_TRACK_COUNT) return current;
      const used = new Set(current.tracks.map((track) => getTrackNumber(track.id)));
      let nextIndex = 1;
      while (used.has(nextIndex) && nextIndex <= MAX_TRACK_COUNT) nextIndex += 1;
      const nextTrack = createTrack(nextIndex);
      return {
        ...current,
        tracks: [...current.tracks, nextTrack],
        selection: current.tracks.some((track) => track.id === current.selection.source_track_id) ? current.selection : { ...current.selection, source_track_id: nextTrack.id },
      };
    });
  };

  const deleteTrack = (trackId) => {
    updateProject((current) => {
      const tracks = current.tracks.filter((track) => track.id !== trackId);
      return {
        ...current,
        tracks,
        selection: current.selection.source_track_id === trackId
          ? { ...current.selection, source_track_id: tracks[0]?.id || "", source_start_sec: 0, source_end_sec: 0 }
          : current.selection,
      };
    });
  };

  const nudgeTarget = (kind, direction) => {
    const step = Math.max(0, normalizeNumber(project.selection.nudge_step_sec, 0.05)) * direction;
    const start = roundSeconds(project.selection.target_start_sec + (kind === "end" ? 0 : step));
    const end = roundSeconds(project.selection.target_end_sec + (kind === "start" ? 0 : step));
    const fixed = kind === "start" && start > end
      ? { start, end: start }
      : kind === "end" && end < start
        ? { start: end, end }
        : { start, end };
    updateSelection({ target_start_sec: fixed.start, target_end_sec: fixed.end });
  };

  const addOperation = (type) => {
    const selection = project.selection;
    const normalizedTarget = normalizeRange(selection.target_start_sec, selection.target_end_sec);
    const normalizedSource = normalizeRange(selection.source_start_sec, selection.source_end_sec);
    const sourceDurationSec = Math.max(0, normalizedSource.end - normalizedSource.start);
    const targetDurationSec = Math.max(0, normalizedTarget.end - normalizedTarget.start);
    const isTrackOperation = type === "insert_from_track" || type === "replace_with_track_fragment";
    const isPointOperation = type === "cut_point" || type === "insert_silence" || type === "insert_from_track";
    const operation = {
      id: `op_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      type,
      target_start_sec: type === "cut_point" || type === "insert_silence" || type === "insert_from_track" ? roundSeconds(mainCurrentTimeSec) : normalizedTarget.start,
      target_end_sec: isPointOperation ? roundSeconds(mainCurrentTimeSec) : normalizedTarget.end,
      duration_sec: type === "insert_silence" ? Math.max(0, normalizeNumber(selection.silence_duration_sec, 2)) : (sourceDurationSec || targetDurationSec),
      source_track_id: isTrackOperation ? selection.source_track_id : "",
      source_start_sec: isTrackOperation ? normalizedSource.start : 0,
      source_end_sec: isTrackOperation ? normalizedSource.end : 0,
      gain_db: isTrackOperation ? normalizeNumber(selectedTrack?.gain_db, 0) : 0,
    };
    updateProject((current) => ({ ...current, operations: [...current.operations, operation] }));
  };

  const deleteOperation = (operationId) => updateProject((current) => ({
    ...current,
    operations: current.operations.filter((operation) => operation.id !== operationId),
  }));

  return (
    <div className="podcastComposerPage">
      <header className="podcastComposerHeader">
        <div>
          <p className="podcastComposerEyebrow">Manual Timing · audio prep v1</p>
          <h1>Подкаст / монтаж аудио</h1>
          <p>Соберите edit JSON для будущего render-а и вернитесь к ноде без открытия режиссёрской доски.</p>
        </div>
        <div className="podcastComposerHeaderActions">
          <button type="button" onClick={onBackToNode}>Назад к ноде</button>
          <button type="button" className="primary" onClick={onApply}>Применить</button>
          <button type="button" onClick={() => downloadJson(project)}>Скачать edit JSON</button>
        </div>
      </header>

      {message ? <div className="podcastComposerMessage">{message}</div> : null}

      <section className="podcastComposerCard mainEditor">
        <div className="podcastComposerSectionHeader">
          <h2>Основная дорожка</h2>
          <div className="podcastComposerMetaGrid compact">
            <span>Файл: <b>{project.main_audio.filename || "аудио не выбрано"}</b></span>
            <span>Длительность: <b>{formatDuration(project.main_audio.duration_sec)}</b></span>
          </div>
        </div>

        {project.main_audio.url ? (
          <>
            <AudioEditTimeline
              durationSec={project.main_audio.duration_sec}
              currentTimeSec={mainCurrentTimeSec}
              selectionStartSec={targetRange.start}
              selectionEndSec={targetRange.end}
              operations={project.operations}
              onSeek={seekMainAudio}
              onSetSelectionStart={setTargetStart}
              onSetSelectionEnd={setTargetEnd}
            />
            <audio
              ref={mainAudioRef}
              controls
              src={project.main_audio.url}
              onTimeUpdate={(event) => setMainCurrentTimeSec(roundSeconds(event.currentTarget.currentTime))}
              onLoadedMetadata={(event) => setMainCurrentTimeSec(roundSeconds(event.currentTarget.currentTime))}
            />
          </>
        ) : <div className="podcastComposerEmpty">Нет main narrator audio.</div>}

        <div className="podcastComposerEditorGrid">
          <div className="podcastComposerFields compactFields">
            <label>start<input type="number" step="0.001" value={project.selection.target_start_sec} onChange={(e) => setTargetStart(e.target.value)} /></label>
            <label>end<input type="number" step="0.001" value={project.selection.target_end_sec} onChange={(e) => setTargetEnd(e.target.value)} /></label>
            <label>Пауза<input type="number" step="0.1" min="0" value={project.selection.silence_duration_sec} onChange={(e) => updateSelection({ silence_duration_sec: clampSeconds(e.target.value, 2) })} /></label>
            <label>Шаг<input type="number" step="0.01" min="0" value={project.selection.nudge_step_sec} onChange={(e) => updateSelection({ nudge_step_sec: clampSeconds(e.target.value, 0.05) })} /></label>
          </div>
          <div className="podcastComposerSelectionInfo">
            <span>start: <b>{formatTimestamp(targetRange.start)}</b></span>
            <span>end: <b>{formatTimestamp(targetRange.end)}</b></span>
            <span>длина: <b>{formatTimestamp(targetRange.end - targetRange.start)} c</b></span>
          </div>
        </div>

        <div className="podcastComposerNudgeBar" aria-label="Точная подгонка границ">
          <span>Шаг: {formatTimestamp(project.selection.nudge_step_sec)} сек</span>
          <button type="button" onClick={() => nudgeTarget("start", -1)}>← start</button>
          <button type="button" onClick={() => nudgeTarget("start", 1)}>start →</button>
          <button type="button" onClick={() => nudgeTarget("end", -1)}>← end</button>
          <button type="button" onClick={() => nudgeTarget("end", 1)}>end →</button>
          <button type="button" onClick={() => nudgeTarget("zone", -1)}>← зона</button>
          <button type="button" onClick={() => nudgeTarget("zone", 1)}>зона →</button>
        </div>

        <div className="podcastComposerOperationButtons compactToolbar">
          <button type="button" onClick={() => addOperation("cut_point")}>Разрез</button>
          <button type="button" onClick={() => setTargetStart(mainCurrentTimeSec)}>Выделить start</button>
          <button type="button" onClick={() => setTargetEnd(mainCurrentTimeSec)}>Выделить end</button>
          <button type="button" onClick={() => addOperation("insert_silence")}>Тишина +</button>
          <button type="button" onClick={() => addOperation("replace_with_silence")}>Заменить тишиной</button>
          <button type="button" onClick={() => addOperation("cut_region")}>Вырезать</button>
          <button type="button" onClick={() => addOperation("insert_from_track")}>Вставить из дорожки</button>
          <button type="button" onClick={() => addOperation("replace_with_track_fragment")}>Заменить фрагментом</button>
        </div>

        <div className="podcastComposerOperationsList compactList">
          {project.operations.length ? project.operations.map((operation, index) => (
            <div className="podcastComposerOperation" key={operation.id}>
              <span>#{index + 1} {OPERATION_LABELS[operation.type] || operation.type}: {formatTimestamp(operation.target_start_sec)}–{formatTimestamp(operation.target_end_sec)} c</span>
              {operation.duration_sec ? <span>{formatTimestamp(operation.duration_sec)} c</span> : <span>точка</span>}
              {operation.source_track_id ? <span>источник: {operation.source_track_id} {formatTimestamp(operation.source_start_sec)}–{formatTimestamp(operation.source_end_sec)} c</span> : null}
              <button type="button" onClick={() => deleteOperation(operation.id)}>Удалить</button>
            </div>
          )) : <div className="podcastComposerEmpty">Операции ещё не добавлены.</div>}
        </div>
      </section>

      <section className="podcastComposerCard">
        <div className="podcastComposerSectionHeader">
          <h2>Дополнительные дорожки</h2>
          <button type="button" className="primary addAudio" onClick={addTrack} disabled={project.tracks.length >= MAX_TRACK_COUNT}>+ Добавить аудио</button>
        </div>
        {project.tracks.length ? (
          <div className="podcastComposerTracks">
            {project.tracks.map((track) => {
              const trackTime = trackCurrentTimes[track.id] || 0;
              const isSelectedSource = project.selection.source_track_id === track.id;
              return (
                <article className={`podcastComposerTrack ${isSelectedSource ? "selected" : ""}`} key={track.id}>
                  <div className="podcastComposerTrackHeader">
                    <div>
                      <h3>{track.label}</h3>
                      <div className="podcastComposerTrackFile">{track.filename || "файл не выбран"} · {formatDuration(track.duration_sec)}</div>
                    </div>
                    <button type="button" onClick={() => deleteTrack(track.id)}>Удалить дорожку</button>
                  </div>
                  <label className="podcastComposerUpload">Загрузить аудио<input type="file" accept="audio/*" onChange={(e) => onTrackUpload(track.id, e.target.files?.[0])} hidden /></label>
                  {track.url ? (
                    <>
                      <AudioEditTimeline
                        compact
                        durationSec={track.duration_sec}
                        currentTimeSec={trackTime}
                        selectionStartSec={isSelectedSource ? sourceRange.start : 0}
                        selectionEndSec={isSelectedSource ? sourceRange.end : 0}
                        operations={[]}
                        onSeek={(timeSec) => seekTrackAudio(track.id, timeSec)}
                        onSetSelectionStart={(timeSec) => updateSelection({ source_track_id: track.id, source_start_sec: roundSeconds(timeSec), source_end_sec: Math.max(roundSeconds(timeSec), roundSeconds(project.selection.source_end_sec)) })}
                        onSetSelectionEnd={(timeSec) => updateSelection({ source_track_id: track.id, source_start_sec: Math.min(roundSeconds(timeSec), roundSeconds(project.selection.source_start_sec)), source_end_sec: roundSeconds(timeSec) })}
                      />
                      <audio
                        ref={(element) => { sourceAudioRefs.current[track.id] = element; }}
                        controls
                        src={track.url}
                        onTimeUpdate={(event) => setTrackCurrentTimes((current) => ({ ...current, [track.id]: roundSeconds(event.currentTarget.currentTime) }))}
                      />
                    </>
                  ) : <div className="podcastComposerEmpty">Загрузите файл, чтобы использовать дорожку как source clip.</div>}
                  <div className="podcastComposerFields small compactFields">
                    <label>source start<input type="number" step="0.001" value={isSelectedSource ? project.selection.source_start_sec : 0} onChange={(e) => updateSelection({ source_track_id: track.id, source_start_sec: roundSeconds(e.target.value) })} /></label>
                    <label>source end<input type="number" step="0.001" value={isSelectedSource ? project.selection.source_end_sec : 0} onChange={(e) => updateSelection({ source_track_id: track.id, source_end_sec: roundSeconds(e.target.value) })} /></label>
                    <label>gain_db<input type="number" step="0.5" value={track.gain_db} onChange={(e) => updateTrack(track.id, { gain_db: normalizeNumber(e.target.value, 0) })} /></label>
                  </div>
                  <div className="podcastComposerTrackActions">
                    <button type="button" onClick={() => updateSelection({ source_track_id: track.id, source_start_sec: roundSeconds(trackTime), source_end_sec: Math.max(roundSeconds(trackTime), roundSeconds(project.selection.source_end_sec)) })}>source start по курсору</button>
                    <button type="button" onClick={() => updateSelection({ source_track_id: track.id, source_start_sec: Math.min(roundSeconds(trackTime), roundSeconds(project.selection.source_start_sec)), source_end_sec: roundSeconds(trackTime) })}>source end по курсору</button>
                    <button type="button" onClick={() => updateSelection({ source_track_id: track.id })}>выбрать как источник</button>
                  </div>
                </article>
              );
            })}
          </div>
        ) : <div className="podcastComposerEmpty">Дополнительные дорожки скрыты. Нажмите “+ Добавить аудио”, чтобы создать Подкаст 1.</div>}
      </section>

      <section className="podcastComposerCard preview">
        <h2>Preview / edit JSON</h2>
        <div className="podcastComposerTimeline">
          {project.operations.map((operation, index) => <span key={operation.id}>{index + 1}. {OPERATION_LABELS[operation.type] || operation.type}</span>)}
          {!project.operations.length ? <span>Timeline пустой.</span> : null}
        </div>
        <textarea readOnly value={editJson} />
      </section>
    </div>
  );
}
