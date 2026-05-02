import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import "./ManualClipDirectorPage.css";

const STORAGE_KEY = "manual_clip_board_active_project";
const ROUTES = ["ia2v", "i2v", "i2v_sound"];
const STATUS_VIDEO_READY = "video_ready";

function resolveManualSceneStatus(scene = {}) {
  if (scene.video_url) return STATUS_VIDEO_READY;
  if (scene.video_prompt && scene.image_url) return "prompt_ready";
  if (scene.image_url) return "photo_loaded";
  if (scene.audio_slice_url) return "audio_ready";
  return "draft";
}

function getSceneStatusLabel(scene = {}) {
  const status = scene?.status;
  if (scene?.audio_extracted && !scene?.video_url) return "аудио";
  if (status === STATUS_VIDEO_READY) return "готово";
  if (status === "photo_loaded") return "фото";
  if (status === "prompt_ready") return "промт";
  if (status === "audio_ready") return "аудио";
  return status || "draft";
}

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
    audio_extracted: Boolean(scene.audio_extracted),
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
    const nextScene = { ...(scenes.find((s) => s.scene_id === sceneId) || {}), image_url: imageUrl };
    updateScene(sceneId, { image_url: imageUrl, status: resolveManualSceneStatus(nextScene) });
  };

  const onCreateVideo = (scene) => {
    if (scene.route === "ia2v" && (!scene.audio_slice_url || !scene.audio_extracted)) {
      updateScene(scene.scene_id, { error: "Для ia2v сначала нажмите «Изъять аудио»", status: scene.status || "draft" });
      return;
    }
    if (!scene.image_url || !scene.video_prompt.trim()) {
      updateScene(scene.scene_id, { error: "Добавьте image_url и video_prompt", status: "draft" });
      return;
    }
    updateScene(scene.scene_id, { status: "video_rendering", error: "" });
    setTimeout(() => {
      updateScene(scene.scene_id, { status: STATUS_VIDEO_READY, video_url: "mock://manual-video-ready", error: "" });
    }, 350);
  };

  if (!project) return <div className="manualDirectorPage"><div className="manualDirectorEmpty"><h2>Проект режиссёрской доски не найден</h2><p>Сначала откройте AI-разбивку и нажмите «Перейти в режиссёрскую доску».</p><button className="clipSB_btn" onClick={() => navigate("/studio/storyboard")}>Вернуться в студию</button></div></div>;

  return <div className="manualDirectorPage">
    <div className="manualDirectorTopbar">
      <button className="clipSB_btn" onClick={() => navigate(-1)}>Назад к AI-разбивке</button>
      <button className="clipSB_btn" onClick={() => navigate("/studio/manual-clip-audio-preview")}>Прослушать сцены</button>
    </div>
    <div className="manualDirectorGrid">
      <aside className="manualDirectorScenes">
        {scenes.map((scene, idx) => <button key={scene.scene_id} className={`manualDirectorSceneItem ${selectedScene?.scene_id === scene.scene_id ? "active" : ""} ${scene.status === STATUS_VIDEO_READY ? "ready" : ""}`} onClick={() => setSelectedSceneId(scene.scene_id)}>
          <strong>{idx + 1} сцена</strong><span>{scene.route}</span><span>{Number(scene.start_sec).toFixed(2)}–{Number(scene.end_sec).toFixed(2)} c</span><span className={`manualStatusBadge ${scene.status === STATUS_VIDEO_READY ? "ready" : ""}`}>{getSceneStatusLabel(scene)}</span><small>{scene.drama_hint || "—"}</small>
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

        <label>Prompt<textarea value={selectedScene.video_prompt} onChange={(e) => {
          const nextScene = { ...selectedScene, video_prompt: e.target.value };
          updateScene(selectedScene.scene_id, { video_prompt: e.target.value, status: resolveManualSceneStatus(nextScene) });
        }} /></label>
        <label>Negative prompt<textarea value={selectedScene.negative_prompt} onChange={(e) => {
          const nextScene = { ...selectedScene, negative_prompt: e.target.value };
          updateScene(selectedScene.scene_id, { negative_prompt: e.target.value, status: resolveManualSceneStatus(nextScene) });
        }} /></label>
        {selectedScene.route === "i2v_sound" ? <div className="manualRouteHint">Для i2v_sound опишите звук и атмосферу прямо в основном Prompt.</div> : null}

        <div className="manualDirectorButtons">
          {selectedScene.route === "ia2v" ? <button className="clipSB_btn" onClick={() => {
            if (!selectedScene.audio_slice_url) {
              updateScene(selectedScene.scene_id, { error: "Аудио сцены ещё не нарезано" });
              return;
            }
            const nextScene = { ...selectedScene, audio_extracted: true };
            updateScene(selectedScene.scene_id, {
              audio_extracted: true,
              error: "",
              status: resolveManualSceneStatus(nextScene),
            });
          }}>{selectedScene.audio_extracted ? "Переизъять аудио" : "Изъять аудио"}</button> : null}
          {selectedScene.route === "ia2v" && selectedScene.audio_slice_url ? <span className="manualAudioReady">Аудио сцены готово</span> : null}
          {selectedScene.route === "ia2v" && selectedScene.audio_extracted ? <span className="manualAudioExtracted">Аудио изъято · готово к ia2v</span> : null}
          <button className="clipSB_btn" onClick={() => onCreateVideo(selectedScene)}>Создать видео</button>
          <button className="clipSB_btn" disabled={!selectedScene.video_url} onClick={() => {
            const sceneWithoutVideo = { ...selectedScene, video_url: "" };
            updateScene(selectedScene.scene_id, {
              video_url: "",
              video_error: "",
              error: "",
              status: resolveManualSceneStatus(sceneWithoutVideo),
            });
          }}>{selectedScene.video_url ? "Удалить видео" : "Видео нет"}</button>
        </div>
        {selectedScene.error ? <div className="manualError">{selectedScene.error}</div> : null}
      </section> : null}

      {selectedScene ? <section className="manualDirectorMedia"><h3>Media preview</h3><label className="clipSB_btn manualUploadBtn">Upload image<input type="file" accept="image/*" hidden onChange={(e) => onUploadImage(selectedScene.scene_id, e.target.files?.[0])} /></label><div className="manualMediaWindow">{selectedScene.video_url ? (selectedScene.video_url.startsWith("mock://") ? <div className="manualMockReady">Mock video ready</div> : <video controls src={selectedScene.video_url} />) : selectedScene.image_url ? <img src={selectedScene.image_url} alt="Scene preview" /> : <div>Нет image/video preview</div>}</div></section> : null}

      {selectedScene ? <section className="manualDirectorAudio"><h3>Аудио отображение</h3><div className="manualAudioMeta">scene_audio: {selectedScene.audio_slice_url ? "готово" : "не готово"} | duration: {Number(selectedScene.duration_sec || 0).toFixed(2)} c</div>{selectedScene.audio_slice_url ? <audio controls src={selectedScene.audio_slice_url} /> : <div>Аудио сцены ещё не нарезано</div>}</section> : null}
    </div>
  </div>;
}
