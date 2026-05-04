import React from "react";
import { useNavigate } from "react-router-dom";
import "./ManualTimingEditorPage.css";
import { buildManualTimingExportJson, getDefaultManualTimingNodeData } from "../clip_nodes/manual_timing/manualTimingDomain";

const ACTIVE_PROJECT_STORAGE_KEY = "manual_timing_active_project";

function readActiveProject() {
  try {
    const raw = localStorage.getItem(ACTIVE_PROJECT_STORAGE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export default function ManualTimingEditorPage() {
  const navigate = useNavigate();
  const project = { ...getDefaultManualTimingNodeData(), ...(readActiveProject() || {}) };

  const onCopyTimingJson = async () => {
    const payload = buildManualTimingExportJson(project);
    try {
      await navigator.clipboard.writeText(JSON.stringify(payload, null, 2));
    } catch {}
  };

  return (
    <div className="manualTimingPage pageCard">
      <h1 className="pageTitle">Тайминг песни</h1>
      <p className="pageSubtitle">filename: {project?.audio?.filename || "аудио не выбрано"}</p>
      <p className="pageSubtitle">duration: {Number(project?.audio?.duration_sec || 0).toFixed(2)}s</p>
      <p className="pageSubtitle">timing_status: {String(project?.timing_status || "empty")}</p>
      <p className="pageSubtitle">scenes count: {Array.isArray(project?.scenes) ? project.scenes.length : 0}</p>
      <div className="manualTimingPage_actions">
        <button className="clipSB_btn" onClick={() => navigate(-1)}>Назад</button>
        <button className="clipSB_btn clipSB_btnSecondary" onClick={onCopyTimingJson}>Скопировать JSON таймингов</button>
      </div>
      <p className="manualTimingPage_hint">Редактор таймингов будет добавлен следующим этапом</p>
    </div>
  );
}
