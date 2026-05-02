import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { fetchJson } from "../../services/api.js";
import "./ManualClipDirectorPage.css";

const STORAGE_KEY = "manual_clip_board_active_project";
const ROUTES = ["ia2v", "i2v", "i2v_sound"];
const STATUS_VIDEO_READY = "video_ready";


function resolveManualVideoRoutePayload(scene = {}) {
  const route = String(scene.route || "i2v").trim();

  if (route === "ia2v") {
    return {
      resolvedWorkflowKey: "lip_sync_music",
      video_generation_route: "lip_sync_music",
      renderMode: "lip_sync_music",
      lipSync: true,
      requiresAudioSensitiveVideo: true,
      send_audio_to_generator: true,
      audioSliceUrl: String(scene.audio_slice_url || ""),
      keepGeneratedAudio: false,
      generatedAudioPolicy: "mute_generated_video_audio_use_master_track",
    };
  }

  if (route === "i2v_sound") {
    return {
      resolvedWorkflowKey: "i2v_sound",
      video_generation_route: "i2v_sound",
      renderMode: "i2v_sound",
      lipSync: false,
      requiresAudioSensitiveVideo: false,
      send_audio_to_generator: false,
      audioSliceUrl: "",
      keepGeneratedAudio: true,
      generatedAudioPolicy: "mix_generated_audio_under_master",
      generatedAudioGainDb: -16,
    };
  }

  return {
    resolvedWorkflowKey: "standard_video",
    video_generation_route: "i2v",
    renderMode: "standard_video",
    lipSync: false,
    requiresAudioSensitiveVideo: false,
    send_audio_to_generator: false,
    audioSliceUrl: "",
    keepGeneratedAudio: false,
    generatedAudioPolicy: "silent_video_use_master_track",
  };
}

async function startManualSceneVideo(payload) {
  return fetchJson("/api/clip/video/start", { method: "POST", timeoutMs: 180000, body: payload });
}

async function getManualSceneVideoStatus(jobId) {
  return fetchJson(`/api/clip/video/status/${encodeURIComponent(jobId)}`, { method: "GET", timeoutMs: 60000 });
}

function resolveVideoStartJobId(out = {}) {
  return String(out.jobId || out.job_id || out.id || "").trim();
}

function resolveManualSceneStatus(scene = {}) {
  if (scene.video_url) return STATUS_VIDEO_READY;
  if (scene.video_prompt && scene.image_url) return "prompt_ready";
  if (scene.image_url) return "photo_loaded";
  if (scene.audio_slice_url) return "audio_ready";
  return "draft";
}

function getSceneStatusLabel(scene = {}) {
  const status = scene?.status;
  if (status === "video_queued") return "очередь";
  if (status === "video_running") return "генерация";
  if (status === "video_error") return "ошибка";
  if (status === STATUS_VIDEO_READY) return "готово";
  if (scene?.audio_extracted && !scene?.video_url) return "аудио";
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
    short_note: String(scene.short_note || ""),
    scene_goal_ru: String(scene.scene_goal_ru || ""),
    prompt_hint_ru: String(scene.prompt_hint_ru || ""),
    story_position_ru: String(scene.story_position_ru || ""),
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
    video_job_id: String(scene.video_job_id || ""),
    video_error: String(scene.video_error || ""),
    video_has_audio: Boolean(scene.video_has_audio),
    generated_audio_policy: String(scene.generated_audio_policy || ""),
    generated_audio_gain_db: Number(scene.generated_audio_gain_db ?? -16),
    keep_generated_audio: Boolean(scene.keep_generated_audio),
    video_request_payload_preview: scene.video_request_payload_preview || null,
  };
}

function buildStoryPositionFallback(scene = {}, idx = 0, total = 0) {
  if (idx === 0) return "начало";
  if (idx === total - 1) return "финал";
  if (scene?.route === "ia2v") return "настоящее / lip-sync";
  return "развитие / визуальная сцена";
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
  const selectedSceneIndex = useMemo(() => scenes.findIndex((s) => s.scene_id === selectedScene?.scene_id), [scenes, selectedScene]);
  const storyPositionText = selectedScene
    ? (selectedScene.story_position_ru || buildStoryPositionFallback(selectedScene, selectedSceneIndex >= 0 ? selectedSceneIndex : 0, scenes.length))
    : "";
  const dramaturgyText = selectedScene ? (selectedScene.drama_hint || selectedScene.short_note || "—") : "—";
  const sceneGoalText = selectedScene ? (selectedScene.scene_goal_ru || selectedScene.drama_hint || selectedScene.short_note || "—") : "—";
  const promptHintText = selectedScene
    ? (selectedScene.prompt_hint_ru || "Напишите prompt вручную под выбранное изображение.")
    : "Напишите prompt вручную под выбранное изображение.";

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

  async function pollManualSceneVideo(sceneId, jobId, attempt = 0) {
    const maxAttempts = 180;
    const delayMs = 5000;
    try {
      const statusOut = await getManualSceneVideoStatus(jobId);
      const status = String(statusOut?.status || "").toLowerCase();
      if (status === "done" && statusOut?.videoUrl) {
        updateScene(sceneId, { status: "video_ready", video_url: String(statusOut.videoUrl || ""), video_job_id: jobId, video_error: "", error: "", video_has_audio: Boolean(statusOut?.videoHasAudio || statusOut?.hasAudio || false), generated_audio_policy: String(statusOut?.generatedAudioPolicy || ""), generated_audio_gain_db: Number(statusOut?.generatedAudioGainDb ?? -16) });
        return;
      }
      if (status === "error" || status === "stopped" || status === "not_found") {
        updateScene(sceneId, { status: "video_error", video_job_id: jobId, video_error: String(statusOut?.error || statusOut?.hint || "video_job_failed"), error: String(statusOut?.error || statusOut?.hint || "video_job_failed") });
        return;
      }
      if (attempt >= maxAttempts) {
        updateScene(sceneId, { status: "video_error", video_job_id: jobId, video_error: "video_poll_timeout", error: "video_poll_timeout" });
        return;
      }
      updateScene(sceneId, { status: "video_running", video_job_id: jobId, video_error: "" });
      setTimeout(() => pollManualSceneVideo(sceneId, jobId, attempt + 1), delayMs);
    } catch (err) {
      updateScene(sceneId, { status: "video_error", video_job_id: jobId, video_error: String(err?.message || "video_poll_failed"), error: String(err?.message || "video_poll_failed") });
    }
  }

  const onCreateVideo = async (scene) => {
    if (!scene.image_url || !scene.video_prompt.trim()) { updateScene(scene.scene_id, { error: "Добавьте image_url и video_prompt", status: "draft" }); return; }
    if (scene.route === "ia2v" && (!scene.audio_slice_url || !scene.audio_extracted)) { updateScene(scene.scene_id, { error: "Для ia2v сначала нажмите «Изъять аудио»", status: scene.status || "draft" }); return; }
    const routePayload = resolveManualVideoRoutePayload(scene);
    const requestedDurationSec = Number(scene.duration_sec || scene.audio_slice_duration_sec || 5);
    updateScene(scene.scene_id, { status: "video_queued", video_error: "", error: "", video_job_id: "" });
    try {
      const payload = { sceneId: scene.scene_id, imageUrl: scene.image_url, videoPrompt: scene.video_prompt, videoNegativePrompt: scene.negative_prompt || "", video_negative_prompt: scene.negative_prompt || "", requestedDurationSec, sceneStartSec: Number(scene.start_sec || 0), sceneEndSec: Number(scene.end_sec || 0), sceneDurationSec: Number(scene.duration_sec || requestedDurationSec), format: project?.format || "9:16", provider: "comfy_remote", ...routePayload, manualClip: true, manual_clip: true, source: "manual_clip_board", project_kind: project?.project_kind || "clip", generatedAudioPolicy: routePayload.generatedAudioPolicy, generatedAudioGainDb: routePayload.generatedAudioGainDb };
      const out = await startManualSceneVideo(payload);
      const jobId = resolveVideoStartJobId(out);
      if (out?.ok === false || !jobId) throw new Error(String(out?.detail || out?.error || "video_start_failed"));
      updateScene(scene.scene_id, { status: "video_running", video_job_id: jobId, video_error: "", error: "", keep_generated_audio: Boolean(payload.keepGeneratedAudio), generated_audio_policy: String(payload.generatedAudioPolicy || ""), generated_audio_gain_db: Number(payload.generatedAudioGainDb ?? -16), video_request_payload_preview: { sceneId: scene.scene_id, route: scene.route, resolvedWorkflowKey: payload.resolvedWorkflowKey, renderMode: payload.renderMode, lipSync: payload.lipSync, hasAudioSliceUrl: Boolean(payload.audioSliceUrl), keepGeneratedAudio: Boolean(payload.keepGeneratedAudio), generatedAudioPolicy: payload.generatedAudioPolicy } });
      pollManualSceneVideo(scene.scene_id, jobId, 0);
    } catch (err) {
      updateScene(scene.scene_id, { status: "video_error", video_error: String(err?.message || "video_start_failed"), error: String(err?.message || "video_start_failed") });
    }
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
          <strong>{idx + 1} сцена</strong><span>{scene.route}</span><span>{Number(scene.start_sec).toFixed(2)}–{Number(scene.end_sec).toFixed(2)} c</span><span className={`manualStatusBadge ${scene.status === STATUS_VIDEO_READY ? "ready" : scene.status === "video_error" ? "error" : (scene.status === "video_running" || scene.status === "video_queued") ? "running" : ""}`}>{getSceneStatusLabel(scene)}</span><small>{scene.drama_hint || scene.short_note || scene.scene_goal_ru || "—"}</small>
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

        <div className="manualSceneGuidance">
          <strong>Подсказка сцены</strong>
          <div>Позиция: {storyPositionText}</div>
          <div>Драматургия: {dramaturgyText}</div>
          <div>Смысл: {sceneGoalText}</div>
          <div>Что учесть в prompt: {promptHintText}</div>
        </div>

        <label className="manualPromptBlock">Prompt<textarea value={selectedScene.video_prompt} onChange={(e) => {
          const nextScene = { ...selectedScene, video_prompt: e.target.value };
          updateScene(selectedScene.scene_id, { video_prompt: e.target.value, status: resolveManualSceneStatus(nextScene) });
        }} /></label>
        <label className="manualNegativePromptBlock">Negative prompt<textarea value={selectedScene.negative_prompt} onChange={(e) => {
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
              video_job_id: "",
              video_error: "",
              video_has_audio: false,
              generated_audio_policy: "",
              generated_audio_gain_db: -16,
              keep_generated_audio: false,
              error: "",
              status: resolveManualSceneStatus(sceneWithoutVideo),
            });
          }}>{selectedScene.video_url ? "Удалить видео" : "Видео нет"}</button>
        </div>
        {selectedScene.error ? <div className="manualError">{selectedScene.error}</div> : null}
        {(["video_queued", "video_running", "video_error"].includes(selectedScene.status)) ? <div className="manualVideoDebug">job: {selectedScene.video_job_id || "—"} · route: {selectedScene.route} · workflow: {selectedScene.video_request_payload_preview?.resolvedWorkflowKey || "—"} · audioSlice: {selectedScene.video_request_payload_preview?.hasAudioSliceUrl ? "yes" : "no"} · keepAudio: {selectedScene.video_request_payload_preview?.keepGeneratedAudio ? "yes" : "no"}</div> : null}
        <section className="manualDirectorAudio">
          <div className="manualAudioMeta">Аудио: {selectedScene.audio_slice_url ? "готово" : "не готово"} | {Number(selectedScene.duration_sec || 0).toFixed(2)} c</div>
          {selectedScene.audio_slice_url ? <audio controls src={selectedScene.audio_slice_url} /> : <div>Аудио сцены ещё не нарезано</div>}
        </section>
      </section> : null}

      {selectedScene ? <section className="manualDirectorMedia"><h3>Media preview</h3><label className="clipSB_btn manualUploadBtn">Upload image<input type="file" accept="image/*" hidden onChange={(e) => onUploadImage(selectedScene.scene_id, e.target.files?.[0])} /></label><div className="manualMediaWindow">{selectedScene.video_url ? (selectedScene.video_url.startsWith("mock://") ? <div className="manualMockReady">Mock video ready</div> : <video controls src={selectedScene.video_url} />) : selectedScene.image_url ? <img src={selectedScene.image_url} alt="Scene preview" /> : <div>Нет image/video preview</div>}</div>{selectedScene.route === "i2v_sound" && selectedScene.video_url ? <div className="manualVideoInfo">Видео содержит сценический звук. В монтаже звук будет приглушён под основную музыку.</div> : null}{selectedScene.route === "ia2v" ? <div className="manualVideoInfo">Lip-sync сцена: в финальном монтаже используем основной аудиотрек, звук видео можно игнорировать.</div> : null}</section> : null}

    </div>
  </div>;
}
