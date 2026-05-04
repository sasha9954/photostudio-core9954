import React from "react";
import { Handle, Position } from "@xyflow/react";
import { useNavigate } from "react-router-dom";
import { NodeShell } from "../comfy/comfyNodeShared";
import "./ManualTimingNode.css";
import { buildManualTimingExportJson, getDefaultManualTimingNodeData, normalizeManualTimingAudio } from "./manualTimingDomain";

const ACTIVE_PROJECT_STORAGE_KEY = "manual_timing_active_project";
const ACTIVE_PROJECT_ID_STORAGE_KEY = "manual_timing_active_project_id";

function getManualTimingProjectStorageKey(nodeId = "") {
  const safeId = String(nodeId || "default").trim() || "default";
  return `manual_timing_project:${safeId}`;
}

function persistManualTimingProject(project = {}) {
  try {
    const serialized = JSON.stringify(project || {});
    localStorage.setItem(ACTIVE_PROJECT_STORAGE_KEY, serialized);
    const nodeId = String(project?.nodeId || "").trim();
    if (nodeId) {
      localStorage.setItem(ACTIVE_PROJECT_ID_STORAGE_KEY, nodeId);
      localStorage.setItem(getManualTimingProjectStorageKey(nodeId), serialized);
    }
  } catch {}
}

function formatDurationSec(value) {
  const sec = Number(value || 0);
  if (!Number.isFinite(sec) || sec <= 0) return "0.00s";
  return `${sec.toFixed(2)}s`;
}

export default function ManualTimingNode({ id, data }) {
  const navigate = useNavigate();
  const patch = (p) => data?.onPatchNodeData?.(id, p);
  const model = { ...getDefaultManualTimingNodeData(), ...(data || {}) };
  const connectedAudio = data?.connectedInputs?.audio_in || data?.connectedAudio || data?.audioInput || null;
  const normalizedConnectedAudio = normalizeManualTimingAudio(connectedAudio);
  const effectiveAudio = normalizedConnectedAudio?.url ? normalizedConnectedAudio : normalizeManualTimingAudio(model.audio);

  React.useEffect(() => {
    if (JSON.stringify(effectiveAudio) !== JSON.stringify(model.audio || {})) {
      patch({ audio: effectiveAudio, updatedAt: Date.now() });
    }
  }, [effectiveAudio, model.audio]);

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
    const payload = buildManualTimingExportJson(persistProject());
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
    } catch {}
  };

  const onResetTiming = () => {
    patch({ markers: [], scenes: [], timing_status: "empty", selectedSceneId: "", updatedAt: Date.now() });
  };

  return (
    <NodeShell title="Тайминг песни" subtitle="manual timing draft" accent="var(--accentB)">
      <Handle type="target" position={Position.Left} id="audio_in" />
      <div className="manualTimingNode_block">
        <div className="manualTimingNode_row"><b>Аудио:</b> {effectiveAudio.filename || "аудио не выбрано"}</div>
        <div className="manualTimingNode_row"><b>Длительность:</b> {formatDurationSec(effectiveAudio.duration_sec)}</div>
        <div className="manualTimingNode_row"><b>Сцен:</b> {Array.isArray(model.scenes) ? model.scenes.length : 0}</div>
        <div className="manualTimingNode_row"><b>Статус:</b> {String(model.timing_status || "empty")}</div>

        <label className="manualTimingNode_label">Формат</label>
        <select className="manualTimingNode_select" value={model.format || "9:16"} onChange={(e) => patch({ format: e.target.value, updatedAt: Date.now() })}>
          <option value="9:16">9:16</option>
          <option value="16:9">16:9</option>
          <option value="1:1">1:1</option>
        </select>

        <label className="manualTimingNode_label">Project kind</label>
        <select className="manualTimingNode_select" value={model.project_kind || "clip"} onChange={(e) => patch({ project_kind: e.target.value, updatedAt: Date.now() })}>
          <option value="clip">clip</option>
          <option value="story">story</option>
        </select>

        <div className="manualTimingNode_actions">
          <button className="clipSB_btn" onClick={onOpenEditor}>Открыть редактор</button>
          <button className="clipSB_btn clipSB_btnSecondary" onClick={onCopyTimingJson}>Скопировать JSON таймингов</button>
          <button className="clipSB_btn clipSB_btnDanger" onClick={onResetTiming}>Сбросить тайминги</button>
        </div>
      </div>
      <Handle type="source" position={Position.Right} id="manual_timing_out" />
    </NodeShell>
  );
}
