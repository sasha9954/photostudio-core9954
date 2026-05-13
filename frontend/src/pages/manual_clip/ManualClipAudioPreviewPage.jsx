import React, { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { writeManualClipBoardOpenState } from "../clip_nodes/manualProjectBackup.js";
import "./ManualClipAudioPreviewPage.css";

const STORAGE_KEY = "manual_clip_board_active_project";
const ACTIVE_PROJECT_ID_STORAGE_KEY = "manual_clip_board_active_project_id";

function getManualProjectStorageKey(nodeId = "") {
  const safeId = String(nodeId || "default").trim() || "default";
  return `manual_clip_board_project:${safeId}`;
}

function readJsonStorage(key) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function readManualActiveProject() {
  const active = readJsonStorage(STORAGE_KEY);
  if (active) return active;
  const activeNodeId = String(localStorage.getItem(ACTIVE_PROJECT_ID_STORAGE_KEY) || "").trim();
  if (activeNodeId) return readJsonStorage(getManualProjectStorageKey(activeNodeId));
  return null;
}

export default function ManualClipAudioPreviewPage() {
  const navigate = useNavigate();
  const project = useMemo(() => {
    return readManualActiveProject();
  }, []);
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];

  const onBackToDirectorBoard = () => {
    const sourceNodeId = String(project?.sourceNodeId || project?.nodeId || "").trim();
    writeManualClipBoardOpenState({
      isOpen: true,
      sourceNodeId,
      selectedSceneId: String(project?.selectedSceneId || project?.scenes?.[0]?.scene_id || "").trim(),
      project_id: String(project?.project_id || project?.projectId || "").trim(),
      input_signature: String(project?.input_signature || project?.inputSignature || "").trim(),
      routePath: "/studio/storyboard",
      updatedAt: Date.now(),
    });
    navigate("/studio/storyboard", {
      state: { openManualDirectorBoard: true, sourceNodeId, director_board: project, project },
    });
  };

  return <div className="manualAudioPreviewPage">
    <div className="manualAudioPreviewTopbar">
      <button className="clipSB_btn" onClick={onBackToDirectorBoard}>Назад в режиссёрскую доску</button>
    </div>
    <h2>Прослушать сцены</h2>
    {scenes.length === 0 ? <div className="manualAudioPreviewEmpty">Сцены не найдены. Сначала соберите сцены на Manual Clip Board.</div> : <div className="manualAudioPreviewList">
      {scenes.map((scene, idx) => <article key={scene.scene_id || idx} className="manualAudioSceneCard">
        <h3>{scene.scene_id || `seg_${idx + 1}`} / #{idx + 1}</h3>
        <div>route: {scene.route || "ia2v"}</div>
        <div>тайминг: {Number(scene.start_sec || 0).toFixed(2)} – {Number(scene.end_sec || 0).toFixed(2)} c</div>
        <div>длительность: {Number(scene.duration_sec || 0).toFixed(2)} c</div>
        <div>drama_hint: {scene.drama_hint || "—"}</div>
        {scene.audio_slice_url ? <audio controls src={scene.audio_slice_url} /> : <div className="manualAudioPending">Аудио ещё не нарезано</div>}
      </article>)}
    </div>}
  </div>;
}
