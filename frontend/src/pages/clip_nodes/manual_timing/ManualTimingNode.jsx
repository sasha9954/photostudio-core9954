import React, { useEffect, useState } from "react";
import { Handle, Position } from "@xyflow/react";
import { useNavigate } from "react-router-dom";
import { API_BASE } from "../../../services/api";
import { NodeShell } from "../comfy/comfyNodeShared";
import {
  buildManualProjectBackupJson,
  hasMeaningfulManualProject,
  readActiveManualClipBoardProject,
  readManualClipBoardProjectForNode,
  pickBestManualClipBoardProject,
  persistManualClipBoardProject,
  getManualClipBoardMaterialStats,
  writeManualClipBoardOpenState,
} from "../manualProjectBackup.js";
import "./ManualTimingNode.css";
import {
  MANUAL_TIMING_MUSIC_CLIP_MODE,
  MANUAL_TIMING_PODCAST_DIALOGUE_MODE,
  MANUAL_TIMING_STORY_VOICEOVER_MODE,
  MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
  buildManualTimingClipPassJson,
  buildManualTimingPodcastPassJson,
  buildManualTimingStoryPassJson,
  getDefaultManualTimingNodeData,
  getManualTimingProjectKindForMode,
  normalizeManualTimingAudio,
  persistManualTimingProject,
  readManualTimingProjectForNode,
  removeManualTimingProjectForNode,
} from "./manualTimingDomain";

function formatDurationSec(value) {
  const sec = Number(value || 0);
  if (!Number.isFinite(sec) || sec <= 0) return "0.00 с";
  return `${sec.toFixed(2)} с`;
}

function formatTimingStatus(status) {
  if (status === "confirmed") return "подтверждено";
  if (status === "draft") return "черновик";
  return "пусто";
}

function getManualProjectOwnerId(project = {}) {
  return String(project?.sourceNodeId || project?.nodeId || "").trim();
}

function projectBelongsToNode(project = {}, nodeId = "") {
  const safeNodeId = String(nodeId || "").trim();
  if (!safeNodeId) return true;
  return getManualProjectOwnerId(project) === safeNodeId;
}

function audioIdentityEquals(a = {}, b = {}) {
  return String(a?.url || "") === String(b?.url || "")
    && String(a?.filename || "") === String(b?.filename || "");
}

function audioMetaEquals(a = {}, b = {}) {
  return audioIdentityEquals(a, b)
    && Number(a?.duration_sec || 0) === Number(b?.duration_sec || 0)
    && Number(a?.duration_ms || 0) === Number(b?.duration_ms || 0);
}

function getManualTimingCopyButtonLabel(projectMode = "") {
  if (projectMode === MANUAL_TIMING_STORY_VOICEOVER_MODE) {
    return "Скопировать JSON для Story Pass";
  }
  if (projectMode === MANUAL_TIMING_MUSIC_CLIP_MODE) return "Скопировать JSON для Clip Pass";
  if (projectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) return "Скопировать JSON для Podcast Pass";
  return "Скопировать JSON таймингов";
}

async function uploadManualTimingAudioAsset(file) {
  const fd = new FormData();
  fd.append("file", file);

  const res = await fetch(`${API_BASE}/api/assets/upload`, {
    method: "POST",
    body: fd,
    credentials: "include",
  });
  if (!res.ok) {
    let message = `upload_failed:${res.status}`;
    try {
      const data = await res.json();
      message = data?.detail || data?.message || message;
    } catch {
      try {
        message = await res.text() || message;
      } catch {
        // keep default upload message
      }
    }
    throw new Error(message);
  }
  return await res.json();
}

function readAudioFileMetadata(file) {
  return new Promise((resolve) => {
    if (!file) {
      resolve({ duration_sec: 0, duration_ms: 0 });
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
      } catch {
        // ignore metadata cleanup errors
      }
      URL.revokeObjectURL(url);
      resolve({
        duration_sec: safeDurationSec,
        duration_ms: safeDurationSec > 0 ? Math.round(safeDurationSec * 1000) : 0,
      });
    };

    audioEl.preload = "metadata";
    audioEl.onloadedmetadata = () => finish(audioEl.duration || 0);
    audioEl.onerror = () => finish(0);
    audioEl.src = url;
  });
}

function getManualTimingNodeModeClass(projectMode = "") {
  return projectMode ? `mode-${projectMode}` : "mode-unselected";
}

function formatManualBoardUpdatedAt(value) {
  if (!value) return "неизвестно";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleString("ru-RU");
}

function downloadManualBoardBackup(project) {
  const blob = new Blob([JSON.stringify(buildManualProjectBackupJson(project, { source: "manual_timing_node_active_board" }), null, 2)], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "manual_clip_board_backup.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function getEmptyManualTimingAudio() {
  return { url: "", filename: "", duration_sec: 0, duration_ms: 0 };
}

function buildManualTimingAudioResetPatch(base = {}, nextAudio = getEmptyManualTimingAudio(), audioSource = "") {
  const safeAudio = normalizeManualTimingAudio(nextAudio);
  const durationSec = Number(safeAudio.duration_sec || 0);
  const hasAudio = Boolean(safeAudio.url);
  return {
    audio: safeAudio,
    audio_source: audioSource,
    timing_status: hasAudio ? "draft" : "empty",
    markers: hasAudio && durationSec > 0 ? [0, durationSec] : [],
    story_blocks: [MANUAL_TIMING_UNKNOWN_STORY_BLOCK],
    audio_phrases: [],
    audio_words: [],
    asr_phrase_map: null,
    scenes: [],
    selectedSceneId: "",
    updatedAt: Date.now(),
  };
}

export default function ManualTimingNode({ id, data }) {
  const navigate = useNavigate();
  const patch = (p) => data?.onPatchNodeData?.(id, p);
  const model = { ...getDefaultManualTimingNodeData(), ...(data || {}) };
  const connectedAudio = data?.connectedInputs?.audio_in || data?.connectedAudio || data?.audioInput || null;
  const normalizedConnectedAudio = normalizeManualTimingAudio(connectedAudio);
  const manualAudio = normalizeManualTimingAudio(model.audio);
  const audioSource = String(model.audio_source || model.audioSource || "").trim();
  const effectiveAudio = audioSource === "none"
    ? getEmptyManualTimingAudio()
    : (audioSource === "manual_upload" && manualAudio.url
      ? manualAudio
      : (normalizedConnectedAudio?.url ? normalizedConnectedAudio : manualAudio));
  const hasConnectedAudio = Boolean(normalizedConnectedAudio?.url);
  const isManualAudioOverride = audioSource === "manual_upload" && Boolean(manualAudio.url);
  const isAudioBlocked = audioSource === "none";
  const storyBlockCount = (Array.isArray(model.story_blocks) ? model.story_blocks : []).filter((block) => (
    String(block?.block_id || "") !== MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id
  )).length;
  const projectMode = String(model.project_mode || "").trim();
  const isProjectModeSelected = Boolean(projectMode);
  const isModeReadyForJson = [MANUAL_TIMING_STORY_VOICEOVER_MODE, MANUAL_TIMING_MUSIC_CLIP_MODE, MANUAL_TIMING_PODCAST_DIALOGUE_MODE].includes(projectMode);
  const copyJsonLabel = getManualTimingCopyButtonLabel(projectMode);
  const isAudioUploading = model.audio_upload_status === "uploading";
  const audioUploadError = String(model.audio_upload_error || "").trim();
  const copyJsonTitle = isProjectModeSelected
    ? (isModeReadyForJson ? "Скопировать JSON выбранного режима" : "Режим не поддерживается")
    : "Сначала выберите режим проекта";
  const [storedActiveBoardProject, setStoredActiveBoardProject] = useState(() => readManualClipBoardProjectForNode(id) || readActiveManualClipBoardProject());
  const nodeDirectorBoard = model.director_board;
  const activeBoardProject = pickBestManualClipBoardProject([
    nodeDirectorBoard,
    storedActiveBoardProject,
  ]) || storedActiveBoardProject || nodeDirectorBoard;
  const activeBoardScenes = Array.isArray(activeBoardProject?.scenes) ? activeBoardProject.scenes : [];
  const activeBoardBlocks = (Array.isArray(activeBoardProject?.story_blocks) ? activeBoardProject.story_blocks : []).filter((block) => {
    const blockId = String(block?.block_id || block?.id || "").trim();
    return blockId
      && blockId !== MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id
      && blockId !== "unknown"
      && blockId !== "unknown_story_block"
      && blockId !== "__unknown__";
  });
  const activeBoardImageCount = activeBoardScenes.filter((scene) => String(scene?.image_url || scene?.start_image_url || scene?.end_image_url || "").trim()).length;
  const activeBoardPromptCount = activeBoardScenes.filter((scene) => (
    String(scene?.video_prompt || "").trim()
    || String(scene?.negative_prompt || "").trim()
    || String(scene?.sound_prompt || "").trim()
  )).length;
  const activeBoardVideoCount = activeBoardScenes.filter((scene) => String(scene?.video_url || "").trim()).length;
  const timingSceneCount = Array.isArray(model.scenes) ? model.scenes.length : 0;
  const hasTimingStoryboardContent = timingSceneCount > 0 || storyBlockCount > 0;


  useEffect(() => {
    setStoredActiveBoardProject(readManualClipBoardProjectForNode(id) || readActiveManualClipBoardProject());
  }, []);

  useEffect(() => {
    const refreshActiveBoard = () => setStoredActiveBoardProject(readManualClipBoardProjectForNode(id) || readActiveManualClipBoardProject());
    window.addEventListener("focus", refreshActiveBoard);
    return () => window.removeEventListener("focus", refreshActiveBoard);
  }, []);

  useEffect(() => {
    const stored = readManualTimingProjectForNode(id);
    if (!stored || typeof stored !== "object") return;
    const storedUpdatedAt = Number(stored?.updatedAt || 0);
    const currentUpdatedAt = Number(model?.updatedAt || 0);
    if (storedUpdatedAt <= currentUpdatedAt) return;
    patch({
      ...stored,
      nodeId: undefined,
      updatedAt: storedUpdatedAt,
    });
    // sync saved editor changes back to graph node once on mount / after returning.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!effectiveAudio?.url) return;
    if (audioMetaEquals(effectiveAudio, model.audio || {})) return;

    const sameAudioIdentity = audioIdentityEquals(effectiveAudio, model.audio || {});

    if (sameAudioIdentity) {
      patch({
        audio: effectiveAudio,
        updatedAt: Date.now(),
      });
      return;
    }

    patch({
      audio: effectiveAudio,
      markers: [],
      scenes: [],
      audio_phrases: [],
      selectedSceneId: "",
      story_blocks: [MANUAL_TIMING_UNKNOWN_STORY_BLOCK],
      timing_status: "draft",
      updatedAt: Date.now(),
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effectiveAudio?.url, effectiveAudio?.filename, effectiveAudio?.duration_sec, effectiveAudio?.duration_ms]);

  const persistProject = () => {
    const project = {
      ...model,
      nodeId: id,
      sourceNodeId: id,
      audio: effectiveAudio,
      markers: Array.isArray(model.markers) ? model.markers : [],
      scenes: Array.isArray(model.scenes) ? model.scenes : [],
      updatedAt: Date.now(),
    };
    persistManualTimingProject(project);
    return project;
  };

  const onOpenEditor = () => {
    persistProject();
    navigate(`/studio/manual-timing-editor?sourceNodeId=${encodeURIComponent(id)}`, {
      state: {
        sourceNodeId: id,
        fromStoryboard: true,
      },
    });
  };

  const onOpenPodcastComposer = () => {
    const project = persistProject();
    navigate(`/studio/podcast-audio-composer?sourceNodeId=${encodeURIComponent(id)}`, {
      state: {
        sourceNodeId: id,
        fromStoryboard: true,
        audio: effectiveAudio,
        project,
      },
    });
  };

  const onReturnToActiveBoard = () => {
    const nodeScopedProject = readManualClipBoardProjectForNode(id);
    const activeStoredProject = readActiveManualClipBoardProject();
    const matchingCandidates = [model.director_board, nodeScopedProject, activeStoredProject]
      .filter((candidateProject) => hasMeaningfulManualProject(candidateProject) && projectBelongsToNode(candidateProject, id));
    const activeProject = pickBestManualClipBoardProject(matchingCandidates) || null;

    if (hasMeaningfulManualProject(activeProject)) {
      const safeProject = {
        ...activeProject,
        nodeId: id,
        sourceNodeId: id,
      };
      setStoredActiveBoardProject(safeProject);
      const stats = getManualClipBoardMaterialStats(safeProject);
      if (stats.materialTotal > 0) {
        persistManualClipBoardProject({
          ...safeProject,
          updatedAt: Date.now(),
          lastPersistReason: "return_to_active_manual_board",
        }, { reason: "return_to_active_manual_board" });
      }
    }

    if (typeof data?.onOpenDirectorBoard === "function") {
      data.onOpenDirectorBoard(id);
      return;
    }

    if (hasMeaningfulManualProject(activeProject)) {
      const safeProject = {
        ...activeProject,
        nodeId: id,
        sourceNodeId: id,
      };
      writeManualClipBoardOpenState({
        isOpen: true,
        sourceNodeId: id,
        selectedSceneId: String(safeProject?.selectedSceneId || safeProject?.scenes?.[0]?.scene_id || "").trim(),
        project_id: String(safeProject?.project_id || safeProject?.projectId || "").trim(),
        input_signature: String(safeProject?.input_signature || safeProject?.inputSignature || "").trim(),
        routePath: "/studio/storyboard",
        updatedAt: Date.now(),
      });
      navigate("/studio/storyboard", {
        state: { openManualDirectorBoard: true, closeLegacyScenarioEditors: true, sourceNodeId: id, director_board: safeProject, project: safeProject },
      });
    }
  };

  const onDownloadActiveBoardBackup = () => {
    const storedProject = readManualClipBoardProjectForNode(id);
    const activeStoredProject = readActiveManualClipBoardProject();
    const activeProject = pickBestManualClipBoardProject([model.director_board, storedProject, activeStoredProject]
      .filter((candidateProject) => hasMeaningfulManualProject(candidateProject) && projectBelongsToNode(candidateProject, id)));
    if (!hasMeaningfulManualProject(activeProject)) return;
    const safeProject = {
      ...activeProject,
      nodeId: id,
      sourceNodeId: id,
    };
    setStoredActiveBoardProject(safeProject);
    downloadManualBoardBackup(safeProject);
  };

  const onStartFreshWithConfirm = () => {
    const confirmed = window.confirm("Начать новый разбор с текущим аудио? Это очистит только разметку тайминга/ASR в ноде. Режиссёрская доска не удаляется.");
    if (!confirmed) return;
    const currentAudio = effectiveAudio?.url ? effectiveAudio : getEmptyManualTimingAudio();
    const resetPatch = buildManualTimingAudioResetPatch(model, currentAudio, currentAudio.url ? (audioSource || (hasConnectedAudio ? "connected" : "manual_upload")) : "");
    const nextProject = {
      ...model,
      ...resetPatch,
      nodeId: id,
      sourceNodeId: id,
      audio_upload_status: "",
      audio_upload_error: "",
    };
    patch(nextProject);
    persistManualTimingProject(nextProject);
    navigate(`/studio/manual-timing-editor?sourceNodeId=${encodeURIComponent(id)}`, {
      state: {
        sourceNodeId: id,
        fromStoryboard: true,
      },
    });
  };

  const onCopyTimingJson = async () => {
    if (!isProjectModeSelected || !isModeReadyForJson) return;
    const project = persistProject();
    const payload = projectMode === MANUAL_TIMING_MUSIC_CLIP_MODE
      ? buildManualTimingClipPassJson(project)
      : (projectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE
        ? buildManualTimingPodcastPassJson(project)
        : buildManualTimingStoryPassJson(project));
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
    } catch {}
  };

  const onProjectModeChange = (event) => {
    const nextMode = event.target.value;
    patch({
      project_mode: nextMode,
      project_kind: getManualTimingProjectKindForMode(nextMode),
      updatedAt: Date.now(),
    });
  };

  const onCloseNode = () => {
    const storedProject = readManualTimingProjectForNode(id);
    const projectForCheck = storedProject && typeof storedProject === "object"
      ? storedProject
      : {
        ...model,
        nodeId: id,
        audio: effectiveAudio,
        markers: Array.isArray(model.markers) ? model.markers : [],
        scenes: Array.isArray(model.scenes) ? model.scenes : [],
        audio_phrases: Array.isArray(model.audio_phrases) ? model.audio_phrases : [],
      };
    if (hasMeaningfulManualProject(projectForCheck)) {
      const confirmed = window.confirm("Удалить ноду тайминга? Проект останется доступен через backup/localStorage.");
      if (!confirmed) return;
      data?.onRemoveNode?.(id);
      return;
    }
    removeManualTimingProjectForNode(id);
    data?.onRemoveNode?.(id);
  };

  const onAudioUpload = async (file) => {
    if (!file) return;
    patch({
      audio_upload_status: "uploading",
      audio_upload_error: "",
      updatedAt: Date.now(),
    });

    try {
      const [metadata, uploadedAsset] = await Promise.all([
        readAudioFileMetadata(file),
        uploadManualTimingAudioAsset(file),
      ]);
      const uploadedDurationSec = Number(uploadedAsset?.durationSec || uploadedAsset?.duration_sec || 0);
      const durationSec = uploadedDurationSec > 0
        ? Number(uploadedDurationSec.toFixed(3))
        : Number(metadata.duration_sec || 0);
      const uploadedAssetUrl = String(
        uploadedAsset?.url
        || uploadedAsset?.assetUrl
        || uploadedAsset?.asset_url
        || uploadedAsset?.publicUrl
        || uploadedAsset?.public_url
        || uploadedAsset?.path
        || ""
      ).trim();
      const uploadedAssetFilename = String(
        uploadedAsset?.name
        || uploadedAsset?.filename
        || uploadedAsset?.fileName
        || file.name
        || ""
      ).trim();
      if (!uploadedAssetUrl) throw new Error("asset_url_missing");

      const nextAudio = {
        url: uploadedAssetUrl,
        filename: uploadedAssetFilename,
        duration_sec: Number.isFinite(durationSec) ? durationSec : 0,
        duration_ms: Number.isFinite(durationSec) && durationSec > 0 ? Math.round(durationSec * 1000) : Number(metadata.duration_ms || 0),
      };
      const resetPatch = buildManualTimingAudioResetPatch(model, nextAudio, "manual_upload");
      const nextProject = {
        ...model,
        ...resetPatch,
        nodeId: id,
        audio_upload_status: "ready",
        audio_upload_error: "",
      };
      patch(nextProject);
      persistManualTimingProject(nextProject);
    } catch (error) {
      patch({
        audio_upload_status: "error",
        audio_upload_error: `Не удалось загрузить аудио на backend. ASR не сможет работать с blob URL.${error?.message ? ` (${error.message})` : ""}`,
        updatedAt: Date.now(),
      });
    }
  };

  const onDeleteAudio = () => {
    const confirmed = window.confirm("Удалить аудио и текущую разметку тайминга? Режиссёрская доска не удаляется — сначала скачайте backup, если она нужна.");
    if (!confirmed) return;
    const resetPatch = buildManualTimingAudioResetPatch(model, getEmptyManualTimingAudio(), "none");
    const nextProject = {
      ...model,
      ...resetPatch,
      nodeId: id,
      sourceNodeId: id,
      audio_upload_status: "",
      audio_upload_error: "",
    };
    patch(nextProject);
    persistManualTimingProject(nextProject);
  };

  const onUseConnectedAudio = () => {
    if (!normalizedConnectedAudio?.url) return;
    const resetPatch = buildManualTimingAudioResetPatch(model, normalizedConnectedAudio, "connected");
    const nextProject = {
      ...model,
      ...resetPatch,
      nodeId: id,
      sourceNodeId: id,
      audio_upload_status: "",
      audio_upload_error: "",
    };
    patch(nextProject);
    persistManualTimingProject(nextProject);
  };

  return (
    <NodeShell title="Тайминг песни" subtitle="ручная разметка" accent="var(--accentB)" onClose={onCloseNode}>
      <Handle type="target" position={Position.Left} id="audio_in" />
      <div className={`manualTimingNode_block ${getManualTimingNodeModeClass(projectMode)}`}>
        <div className="manualTimingNode_row"><b>Аудио:</b> {effectiveAudio.filename || "аудио не выбрано"}</div>
        {isManualAudioOverride ? <div className="manualTimingNode_row">Источник: загружено вручную</div> : null}
        {hasConnectedAudio && !isManualAudioOverride && !isAudioBlocked ? <div className="manualTimingNode_row">Источник: AUDIO-вход</div> : null}
        {isAudioBlocked ? <div className="manualTimingNode_row">Источник: аудио отключено вручную</div> : null}
        <div className="manualTimingNode_row"><b>Длительность:</b> {formatDurationSec(effectiveAudio.duration_sec)}</div>
        <div className="manualTimingNode_row"><b>Сцен:</b> {timingSceneCount}</div>
        <div className="manualTimingNode_row"><b>Смысловых блоков:</b> {storyBlockCount}</div>
        <div className="manualTimingNode_row"><b>Статус:</b> {formatTimingStatus(model.timing_status)}</div>

        {(hasMeaningfulManualProject(activeBoardProject) || hasTimingStoryboardContent) ? <div className="manualTimingNode_activeBoard">
          <div className="manualTimingNode_activeBoardTitle">🎬 Активная режиссёрская доска</div>
          <div className="manualTimingNode_activeBoardMeta">Сцен: {activeBoardScenes.length} · Фото: {activeBoardImageCount} · Промты: {activeBoardPromptCount} · Видео: {activeBoardVideoCount} · Блоков: {activeBoardBlocks.length} · Обновлено: {formatManualBoardUpdatedAt(activeBoardProject?.updatedAt || activeBoardProject?.updated_at)}</div>
          <div className="manualTimingNode_activeBoardActions">
            <button className="clipSB_btn clipSB_btnPrimary" type="button" onClick={onReturnToActiveBoard}>Вернуться в доску</button>
            <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={onDownloadActiveBoardBackup}>Скачать backup</button>
            <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={onStartFreshWithConfirm}>Начать заново</button>
          </div>
        </div> : null}

        <label className="manualTimingNode_label">Формат</label>
        <select className="manualTimingNode_select" value={model.format || "9:16"} onChange={(e) => patch({ format: e.target.value, updatedAt: Date.now() })}>
          <option value="9:16">9:16</option>
          <option value="16:9">16:9</option>
          <option value="1:1">1:1</option>
        </select>

        <label className="manualTimingNode_label">Тип проекта</label>
        <select
          className={`manualTimingNode_select ${!isProjectModeSelected ? "isError" : ""}`}
          value={projectMode}
          onChange={onProjectModeChange}
          aria-invalid={!isProjectModeSelected}
        >
          <option value="">— выберите режим —</option>
          <option value={MANUAL_TIMING_STORY_VOICEOVER_MODE}>История / Voice-over</option>
          <option value={MANUAL_TIMING_MUSIC_CLIP_MODE}>Клип / Music video</option>
          <option value={MANUAL_TIMING_PODCAST_DIALOGUE_MODE}>Подкаст / Dialogue</option>
        </select>
        {!isProjectModeSelected ? <div className="manualTimingNode_modeError">Выберите режим</div> : null}

        {audioUploadError ? <div className="manualTimingNode_uploadError">{audioUploadError}</div> : null}

        <div className="manualTimingNodeActions manualTimingNode_actions">
          <button className="clipSB_btn" onClick={onOpenEditor} disabled={!isProjectModeSelected}>Открыть редактор тайминга</button>
          <button className="clipSB_btn clipSB_btnSecondary manualTimingNode_podcastBtn" type="button" onClick={onOpenPodcastComposer}>Подкаст</button>
          <button className="clipSB_btn clipSB_btnSecondary" onClick={onCopyTimingJson} disabled={!isProjectModeSelected || !isModeReadyForJson} title={copyJsonTitle}>{copyJsonLabel}</button>
          <label className={`clipSB_btn clipSB_btnSecondary manualTimingNode_upload ${isAudioUploading ? "isDisabled" : ""}`}>
            {isAudioUploading ? "Загрузка…" : (effectiveAudio.url ? "Заменить аудио" : "Загрузить аудио")}
            <input type="file" accept="audio/*" onChange={(e) => onAudioUpload(e.target.files?.[0])} disabled={isAudioUploading} hidden />
          </label>
          <button className="clipSB_btn clipSB_btnDanger" type="button" onClick={onDeleteAudio} disabled={isAudioUploading || !effectiveAudio.url}>Удалить аудио</button>
          {hasConnectedAudio && (isManualAudioOverride || isAudioBlocked) ? <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={onUseConnectedAudio} disabled={isAudioUploading}>Взять AUDIO-вход</button> : null}
        </div>
        <div className="manualTimingNode_editorHint">Новая доска создаётся внутри редактора кнопкой ‘Создать новую доску из тайминга’.</div>
      </div>
      <Handle type="source" position={Position.Right} id="manual_timing_out" />
    </NodeShell>
  );
}
