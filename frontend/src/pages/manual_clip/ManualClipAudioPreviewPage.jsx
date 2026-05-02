import React, { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import "./ManualClipAudioPreviewPage.css";

const STORAGE_KEY = "manual_clip_board_active_project";

export default function ManualClipAudioPreviewPage() {
  const navigate = useNavigate();
  const project = useMemo(() => {
    try { return JSON.parse(localStorage.getItem(STORAGE_KEY) || "null"); } catch { return null; }
  }, []);
  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];

  return <div className="manualAudioPreviewPage">
    <div className="manualAudioPreviewTopbar">
      <button className="clipSB_btn" onClick={() => navigate("/studio/manual-clip-board")}>Назад в режиссёрскую доску</button>
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
