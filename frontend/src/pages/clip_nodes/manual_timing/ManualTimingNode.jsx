import React, { useEffect } from "react";
import { Handle, Position } from "@xyflow/react";
import { useNavigate } from "react-router-dom";
import { NodeShell } from "../comfy/comfyNodeShared";
import "./ManualTimingNode.css";
import {
  MANUAL_TIMING_MUSIC_CLIP_MODE,
  MANUAL_TIMING_PODCAST_DIALOGUE_MODE,
  MANUAL_TIMING_STORY_VOICEOVER_MODE,
  MANUAL_TIMING_UNKNOWN_STORY_BLOCK,
  buildManualTimingExportJson,
  buildManualTimingStoryPassJson,
  getDefaultManualTimingNodeData,
  getManualTimingProjectKindForMode,
  normalizeManualTimingAudio,
  persistManualTimingProject,
  readManualTimingProjectForNode,
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
  if (projectMode === MANUAL_TIMING_STORY_VOICEOVER_MODE) return "Скопировать JSON истории";
  if (projectMode === MANUAL_TIMING_MUSIC_CLIP_MODE) return "JSON клипа будет позже";
  if (projectMode === MANUAL_TIMING_PODCAST_DIALOGUE_MODE) return "JSON подкаста будет позже";
  return "Скопировать JSON таймингов";
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
  const storyBlockCount = (Array.isArray(model.story_blocks) ? model.story_blocks : []).filter((block) => {
    const blockId = String(block?.block_id || "");
    const sceneIds = Array.isArray(block?.scene_ids) ? block.scene_ids : [];
    const sceneCount = sceneIds.length || (Array.isArray(model.scenes) ? model.scenes : []).filter((scene) => String(scene?.story_block_id || "") === blockId).length;
    return blockId !== MANUAL_TIMING_UNKNOWN_STORY_BLOCK.block_id || sceneCount > 0;
  }).length;
  const projectMode = String(model.project_mode || "").trim();
  const isProjectModeSelected = Boolean(projectMode);
  const isStoryVoiceover = projectMode === MANUAL_TIMING_STORY_VOICEOVER_MODE;
  const isModeReadyForJson = isStoryVoiceover;
  const copyJsonLabel = getManualTimingCopyButtonLabel(projectMode);
  const copyJsonTitle = isProjectModeSelected
    ? (isModeReadyForJson ? "Скопировать JSON выбранного режима" : "Этот режим будет подключён позже")
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
    const hasAsrStoryScenes = Array.isArray(project.scenes)
      && project.scenes.some((scene) => Array.isArray(scene?.source_phrase_ids) && scene.source_phrase_ids.length);
    const payload = hasAsrStoryScenes ? buildManualTimingStoryPassJson(project) : buildManualTimingExportJson(project);
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

  const onAudioUpload = (file) => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    const audioEl = new Audio();
    audioEl.preload = "metadata";
    audioEl.onloadedmetadata = () => {
      const durationSec = Number(audioEl.duration || 0);
      patch({
        audio: {
          url,
          filename: file.name,
          duration_sec: Number.isFinite(durationSec) ? Number(durationSec.toFixed(3)) : 0,
          duration_ms: Number.isFinite(durationSec) ? Math.round(durationSec * 1000) : 0,
        },
        timing_status: "draft",
        markers: [],
        story_blocks: [MANUAL_TIMING_UNKNOWN_STORY_BLOCK],
        audio_phrases: [],
        scenes: [],
        selectedSceneId: "",
        updatedAt: Date.now(),
      });
    };
    audioEl.onerror = () => {
      patch({
        audio: { url, filename: file.name, duration_sec: 0, duration_ms: 0 },
        timing_status: "draft",
        markers: [],
        story_blocks: [MANUAL_TIMING_UNKNOWN_STORY_BLOCK],
        audio_phrases: [],
        scenes: [],
        selectedSceneId: "",
        updatedAt: Date.now(),
      });
    };
    audioEl.src = url;
  };

  return (
    <NodeShell title="Тайминг песни" subtitle="ручная разметка" accent="var(--accentB)">
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

        <div className="manualTimingNodeActions manualTimingNode_actions">
          <button className="clipSB_btn" onClick={onOpenEditor} disabled={!isProjectModeSelected}>Открыть редактор</button>
          <button className="clipSB_btn clipSB_btnSecondary" onClick={onCopyTimingJson} disabled={!isProjectModeSelected || !isModeReadyForJson} title={copyJsonTitle}>{copyJsonLabel}</button>
          <label className="clipSB_btn clipSB_btnSecondary manualTimingNode_upload">
            Загрузить аудио
            <input type="file" accept="audio/*" onChange={(e) => onAudioUpload(e.target.files?.[0])} hidden />
          </label>
        </div>
      </div>
      <Handle type="source" position={Position.Right} id="manual_timing_out" />
    </NodeShell>
  );
}
