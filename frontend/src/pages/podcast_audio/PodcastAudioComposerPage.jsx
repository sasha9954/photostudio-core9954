import React, { useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { getAccountScopedStorageKey } from "../clip_nodes/manualProjectBackup.js";
import {
  MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
  normalizeManualTimingAudio,
  persistManualTimingProject,
  readManualTimingProjectForNode,
} from "../clip_nodes/manual_timing/manualTimingDomain.js";
import "./PodcastAudioComposerPage.css";

const TRACK_COUNT = 4;
const MICRO_STEPS = [-0.20, -0.10, -0.05, -0.02, 0.02, 0.05, 0.10, 0.20];

const OPERATION_LABELS = {
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
    tracks: Array.from({ length: TRACK_COUNT }, (_, index) => createTrack(index + 1)),
    selection: {
      target_start_sec: 0,
      target_end_sec: 0,
      source_track_id: "podcast_1",
      source_start_sec: 0,
      source_end_sec: 0,
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
    tracks: Array.from({ length: TRACK_COUNT }, (_, index) => createTrack(index + 1, rawTracks[index] || {})),
    selection: {
      ...defaults.selection,
      ...selection,
      target_start_sec: normalizeNumber(selection.target_start_sec, 0),
      target_end_sec: normalizeNumber(selection.target_end_sec, 0),
      source_start_sec: normalizeNumber(selection.source_start_sec, 0),
      source_end_sec: normalizeNumber(selection.source_end_sec, 0),
      source_track_id: String(selection.source_track_id || defaults.selection.source_track_id),
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

function formatDuration(value) {
  const sec = Number(value || 0);
  return Number.isFinite(sec) && sec > 0 ? `${sec.toFixed(2)} с` : "0.00 с";
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
  const [project, setProject] = useState(() => normalizeComposerProject(readComposerProject(sourceNodeId), sourceNodeId, sourceAudio));
  const [message, setMessage] = useState("");

  useEffect(() => {
    persistComposerProject(project);
  }, [project]);

  const selectedTrack = project.tracks.find((track) => track.id === project.selection.source_track_id) || project.tracks[0];
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

  const onTrackUpload = async (trackId, file) => {
    if (!file) return;
    const metadata = await readAudioFileMetadata(file);
    const url = URL.createObjectURL(file);
    updateProject((current) => ({
      ...current,
      tracks: current.tracks.map((track) => track.id === trackId ? {
        ...track,
        url,
        filename: file.name || track.filename,
        duration_sec: Number(metadata.duration_sec || 0),
      } : track),
    }));
  };

  const updateTrack = (trackId, patch) => updateProject((current) => ({
    ...current,
    tracks: current.tracks.map((track) => track.id === trackId ? { ...track, ...patch } : track),
    selection: current.selection.source_track_id === trackId ? { ...current.selection, ...patch.selection } : current.selection,
  }));

  const shiftBoundary = (delta) => {
    updateSelection({
      target_start_sec: Math.max(0, Number((project.selection.target_start_sec + delta).toFixed(2))),
      target_end_sec: Math.max(0, Number((project.selection.target_end_sec + delta).toFixed(2))),
    });
  };

  const addOperation = (type) => {
    const selection = project.selection;
    const durationSec = Math.max(0, normalizeNumber(selection.target_end_sec, 0) - normalizeNumber(selection.target_start_sec, 0));
    const sourceDurationSec = Math.max(0, normalizeNumber(selection.source_end_sec, 0) - normalizeNumber(selection.source_start_sec, 0));
    const operation = {
      id: `op_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      type,
      target_start_sec: normalizeNumber(selection.target_start_sec, 0),
      target_end_sec: normalizeNumber(selection.target_end_sec, 0),
      duration_sec: type === "insert_silence" ? durationSec : (sourceDurationSec || durationSec),
      source_track_id: type.includes("track") ? selection.source_track_id : "",
      source_start_sec: type.includes("track") ? normalizeNumber(selection.source_start_sec, 0) : 0,
      source_end_sec: type.includes("track") ? normalizeNumber(selection.source_end_sec, 0) : 0,
      gain_db: type.includes("track") ? normalizeNumber(selectedTrack?.gain_db, 0) : 0,
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

      <section className="podcastComposerCard">
        <h2>Основная дорожка</h2>
        <div className="podcastComposerMetaGrid">
          <span>Файл: <b>{project.main_audio.filename || "аудио не выбрано"}</b></span>
          <span>Длительность: <b>{formatDuration(project.main_audio.duration_sec)}</b></span>
        </div>
        {project.main_audio.url ? <audio controls src={project.main_audio.url} /> : <div className="podcastComposerEmpty">Нет main narrator audio.</div>}
        <div className="podcastComposerFields">
          <label>start_sec<input type="number" step="0.01" value={project.selection.target_start_sec} onChange={(e) => updateSelection({ target_start_sec: normalizeNumber(e.target.value, 0) })} /></label>
          <label>end_sec<input type="number" step="0.01" value={project.selection.target_end_sec} onChange={(e) => updateSelection({ target_end_sec: normalizeNumber(e.target.value, 0) })} /></label>
        </div>
        <div className="podcastComposerMicroButtons">
          {MICRO_STEPS.map((step) => <button key={step} type="button" onClick={() => shiftBoundary(step)}>{step > 0 ? "+" : ""}{step.toFixed(2)}</button>)}
        </div>
      </section>

      <section className="podcastComposerCard">
        <h2>Дополнительные дорожки</h2>
        <div className="podcastComposerTracks">
          {project.tracks.map((track) => (
            <article className="podcastComposerTrack" key={track.id}>
              <div className="podcastComposerTrackHeader">
                <h3>{track.label}</h3>
                <label><input type="radio" name="sourceTrack" checked={project.selection.source_track_id === track.id} onChange={() => updateSelection({ source_track_id: track.id })} /> выбрать</label>
              </div>
              <label className="podcastComposerUpload">Загрузить аудио<input type="file" accept="audio/*" onChange={(e) => onTrackUpload(track.id, e.target.files?.[0])} hidden /></label>
              <div className="podcastComposerTrackFile">{track.filename || "файл не выбран"} · {formatDuration(track.duration_sec)}</div>
              {track.url ? <audio controls src={track.url} /> : null}
              <div className="podcastComposerFields small">
                <label>source_start_sec<input type="number" step="0.01" value={project.selection.source_track_id === track.id ? project.selection.source_start_sec : 0} onChange={(e) => updateSelection({ source_track_id: track.id, source_start_sec: normalizeNumber(e.target.value, 0) })} /></label>
                <label>source_end_sec<input type="number" step="0.01" value={project.selection.source_track_id === track.id ? project.selection.source_end_sec : 0} onChange={(e) => updateSelection({ source_track_id: track.id, source_end_sec: normalizeNumber(e.target.value, 0) })} /></label>
                <label>gain_db<input type="number" step="0.5" value={track.gain_db} onChange={(e) => updateTrack(track.id, { gain_db: normalizeNumber(e.target.value, 0) })} /></label>
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="podcastComposerCard">
        <h2>Операции</h2>
        <div className="podcastComposerOperationButtons">
          {Object.entries(OPERATION_LABELS).map(([type, label]) => <button key={type} type="button" onClick={() => addOperation(type)}>{label}</button>)}
        </div>
        <div className="podcastComposerOperationsList">
          {project.operations.length ? project.operations.map((operation, index) => (
            <div className="podcastComposerOperation" key={operation.id}>
              <span>#{index + 1} {OPERATION_LABELS[operation.type] || operation.type}: {operation.target_start_sec}–{operation.target_end_sec} c</span>
              {operation.source_track_id ? <span>источник: {operation.source_track_id} {operation.source_start_sec}–{operation.source_end_sec} c</span> : null}
              <button type="button" onClick={() => deleteOperation(operation.id)}>Удалить</button>
            </div>
          )) : <div className="podcastComposerEmpty">Операции ещё не добавлены.</div>}
        </div>
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
