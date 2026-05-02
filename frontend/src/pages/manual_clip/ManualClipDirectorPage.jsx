import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import "./ManualClipDirectorPage.css";

const STORAGE_KEY = "manual_clip_board_active_project";
const ROUTES = ["ia2v", "i2v", "i2v_sound"];

function normalizeScene(scene = {}, idx = 0) {
  const start = Number(scene.start_sec || 0);
  const end = Number(scene.end_sec || start);
  return {
    scene_id: String(scene.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`),
    index: Number(scene.index || idx + 1),
    route: ROUTES.includes(scene.route) ? scene.route : "ia2v",
    start_sec: start,
    end_sec: end,
    duration_sec: Number((Math.max(0, end - start)).toFixed(3)),
    drama_hint: String(scene.drama_hint || ""),
    video_prompt: String(scene.video_prompt || ""),
    negative_prompt: String(scene.negative_prompt || ""),
    sound_prompt: String(scene.sound_prompt || ""),
    image_url: String(scene.image_url || ""),
    video_url: String(scene.video_url || ""),
    audio_slice_url: String(scene.audio_slice_url || ""),
    audio_slice_duration_sec: Number(scene.audio_slice_duration_sec || 0),
    status: String(scene.status || "draft"),
    error: String(scene.error || ""),
  };
}

export default function ManualClipDirectorPage() {
  const navigate = useNavigate();
  const [project, setProject] = useState(null);
  const [selectedSceneId, setSelectedSceneId] = useState("");

  useEffect(() => {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    try {
      const parsed = JSON.parse(raw);
      const scenes = Array.isArray(parsed?.scenes) ? parsed.scenes.map(normalizeScene) : [];
      setProject({ ...parsed, scenes });
      setSelectedSceneId(scenes[0]?.scene_id || "");
    } catch {
      setProject(null);
    }
  }, []);

  const persistProject = (nextProject) => {
    setProject(nextProject);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(nextProject));
  };

  const scenes = Array.isArray(project?.scenes) ? project.scenes : [];
  const selectedScene = useMemo(() => scenes.find((s) => s.scene_id === selectedSceneId) || scenes[0] || null, [scenes, selectedSceneId]);

  const updateScene = (sceneId, patch) => {
    const nextScenes = scenes.map((s) => (s.scene_id !== sceneId ? s : { ...s, ...patch }));
    persistProject({ ...project, scenes: nextScenes });
  };

  const onUploadImage = (sceneId, file) => {
    if (!file) return;
    const imageUrl = URL.createObjectURL(file);
    updateScene(sceneId, { image_url: imageUrl, status: "photo_loaded" });
  };

  const onCreateVideo = (scene) => {
    if (!scene.image_url || !scene.video_prompt.trim()) {
      updateScene(scene.scene_id, { error: "Добавьте image_url и video_prompt", status: "draft" });
      return;
    }
    updateScene(scene.scene_id, { status: "video_rendering", error: "" });
    setTimeout(() => {
      updateScene(scene.scene_id, { status: "video_ready", video_url: "mock://manual-video-ready" });
    }, 350);
  };

  if (!project) return <div className="manualDirectorPage"><div className="manualDirectorEmpty"><h2>Проект режиссёрской доски не найден</h2><p>Сначала откройте AI-разбивку и нажмите «Перейти в режиссёрскую доску».</p><button className="clipSB_btn" onClick={() => navigate("/studio/storyboard")}>Вернуться в студию</button></div></div>;

  return <div className="manualDirectorPage">
    <div className="manualDirectorTopbar">
      <button className="clipSB_btn" onClick={() => navigate("/studio/manual-clip-audio-preview")}>Прослушать сцены</button>
    </div>
    <div className="manualDirectorGrid">
      <aside className="manualDirectorScenes">
        {scenes.map((scene, idx) => <button key={scene.scene_id} className={`manualDirectorSceneItem ${selectedScene?.scene_id === scene.scene_id ? "active" : ""}`} onClick={() => setSelectedSceneId(scene.scene_id)}>
          <strong>{idx + 1} сцена</strong><span>{scene.route}</span><span>{Number(scene.start_sec).toFixed(2)}–{Number(scene.end_sec).toFixed(2)} c</span><span className="manualStatusBadge">{scene.status}</span><small>{scene.drama_hint || "—"}</small>
        </button>)}
      </aside>

      {selectedScene ? <section className="manualDirectorCenter">
        <h2>Сцена {selectedScene.index}</h2>
        <label>Route
          <select value={selectedScene.route} onChange={(e) => updateScene(selectedScene.scene_id, { route: e.target.value })}>{ROUTES.map((route) => <option key={route} value={route}>{route}</option>)}</select>
        </label>

        <div className="manualTimingReadonly">
          <div>Тайминг сцены: {Number(selectedScene.start_sec).toFixed(2)} – {Number(selectedScene.end_sec).toFixed(2)} c</div>
          <div>Длительность: {Number(selectedScene.duration_sec).toFixed(2)} c</div>
        </div>

        <label>Prompt<textarea value={selectedScene.video_prompt} onChange={(e) => updateScene(selectedScene.scene_id, { video_prompt: e.target.value })} /></label>
        <label>Negative prompt<textarea value={selectedScene.negative_prompt} onChange={(e) => updateScene(selectedScene.scene_id, { negative_prompt: e.target.value })} /></label>
        {selectedScene.route === "i2v_sound" ? <label>Sound prompt<textarea value={selectedScene.sound_prompt} onChange={(e) => updateScene(selectedScene.scene_id, { sound_prompt: e.target.value })} /></label> : null}

        <div className="manualDirectorButtons">
          {selectedScene.route === "ia2v" ? <button className="clipSB_btn" onClick={() => updateScene(selectedScene.scene_id, { status: selectedScene.audio_slice_url ? "audio_ready" : selectedScene.status, error: selectedScene.audio_slice_url ? "" : "Аудио сцены ещё не нарезано" })}>Изъять аудио</button> : null}
          {selectedScene.route === "ia2v" && selectedScene.audio_slice_url ? <span className="manualAudioReady">Аудио сцены готово</span> : null}
          <button className="clipSB_btn" onClick={() => onCreateVideo(selectedScene)}>Создать видео</button>
          <button className="clipSB_btn" onClick={() => {
            const nextScenes = scenes.filter((s) => s.scene_id !== selectedScene.scene_id);
            persistProject({ ...project, scenes: nextScenes });
            setSelectedSceneId(nextScenes[0]?.scene_id || "");
          }}>Удалить</button>
        </div>
        {selectedScene.error ? <div className="manualError">{selectedScene.error}</div> : null}
      </section> : null}

      {selectedScene ? <section className="manualDirectorMedia"><h3>Media preview</h3><label className="clipSB_btn">Upload image<input type="file" accept="image/*" hidden onChange={(e) => onUploadImage(selectedScene.scene_id, e.target.files?.[0])} /></label><div className="manualMediaWindow">{selectedScene.video_url ? (selectedScene.video_url.startsWith("mock://") ? <div className="manualMockReady">Mock video ready</div> : <video controls src={selectedScene.video_url} />) : selectedScene.image_url ? <img src={selectedScene.image_url} alt="Scene preview" /> : <div>Нет image/video preview</div>}</div></section> : null}

      {selectedScene ? <section className="manualDirectorAudio"><h3>Аудио отображение</h3><div>scene_audio: {selectedScene.audio_slice_url ? "готово" : "не готово"}</div><div>duration: {Number(selectedScene.duration_sec || 0).toFixed(2)} c</div>{selectedScene.audio_slice_url ? <><div>Аудио сцены</div><audio controls src={selectedScene.audio_slice_url} /></> : <div>Аудио сцены ещё не нарезано</div>}</section> : null}
    </div>
  </div>;
}
