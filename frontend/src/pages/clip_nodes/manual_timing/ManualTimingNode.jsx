import React, { useEffect } from "react";
import { Handle, Position } from "@xyflow/react";
import { useNavigate } from "react-router-dom";
import { API_BASE } from "../../../services/api";
import { NodeShell } from "../comfy/comfyNodeShared";
import { hasMeaningfulManualProject } from "../manualProjectBackup.js";
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

export default function ManualTimingNode({ id, data }) {
  const navigate = useNavigate();
  const patch = (p) => data?.onPatchNodeData?.(id, p);
  const model = { ...getDefaultManualTimingNodeData(), ...(data || {}) };
  const connectedAudio = data?.connectedInputs?.audio_in || data?.connectedAudio || data?.audioInput || null;
  const normalizedConnectedAudio = normalizeManualTimingAudio(connectedAudio);
  const effectiveAudio = normalizedConnectedAudio?.url ? normalizedConnectedAudio : normalizeManualTimingAudio(model.audio);
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
    navigate("/studio/manual-timing");
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

      patch({
        audio: {
          url: uploadedAssetUrl,
          filename: uploadedAssetFilename,
          duration_sec: Number.isFinite(durationSec) ? durationSec : 0,
          duration_ms: Number.isFinite(durationSec) && durationSec > 0 ? Math.round(durationSec * 1000) : Number(metadata.duration_ms || 0),
        },
        timing_status: "draft",
        markers: [],
        story_blocks: [MANUAL_TIMING_UNKNOWN_STORY_BLOCK],
        audio_phrases: [],
        scenes: [],
        selectedSceneId: "",
        audio_upload_status: "ready",
        audio_upload_error: "",
        updatedAt: Date.now(),
      });
    } catch (error) {
      patch({
        audio_upload_status: "error",
        audio_upload_error: `Не удалось загрузить аудио на backend. ASR не сможет работать с blob URL.${error?.message ? ` (${error.message})` : ""}`,
        updatedAt: Date.now(),
      });
    }
  };

  return (
    <NodeShell title="Тайминг песни" subtitle="ручная разметка" accent="var(--accentB)" onClose={onCloseNode}>
      <Handle type="target" position={Position.Left} id="audio_in" />
      <div className={`manualTimingNode_block ${getManualTimingNodeModeClass(projectMode)}`}>
        <div className="manualTimingNode_row"><b>Аудио:</b> {effectiveAudio.filename || "аудио не выбрано"}</div>
        <div className="manualTimingNode_row"><b>Длительность:</b> {formatDurationSec(effectiveAudio.duration_sec)}</div>
        <div className="manualTimingNode_row"><b>Сцен:</b> {Array.isArray(model.scenes) ? model.scenes.length : 0}</div>
        <div className="manualTimingNode_row"><b>Смысловых блоков:</b> {storyBlockCount}</div>
        <div className="manualTimingNode_row"><b>Статус:</b> {formatTimingStatus(model.timing_status)}</div>

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
          <button className="clipSB_btn" onClick={onOpenEditor} disabled={!isProjectModeSelected}>Открыть редактор</button>
          <button className="clipSB_btn clipSB_btnSecondary" onClick={onCopyTimingJson} disabled={!isProjectModeSelected || !isModeReadyForJson} title={copyJsonTitle}>{copyJsonLabel}</button>
          <label className={`clipSB_btn clipSB_btnSecondary manualTimingNode_upload ${isAudioUploading ? "isDisabled" : ""}`}>
            {isAudioUploading ? "Загрузка…" : "Загрузить аудио"}
            <input type="file" accept="audio/*" onChange={(e) => onAudioUpload(e.target.files?.[0])} disabled={isAudioUploading} hidden />
          </label>
        </div>
      </div>
      <Handle type="source" position={Position.Right} id="manual_timing_out" />
    </NodeShell>
  );
}
