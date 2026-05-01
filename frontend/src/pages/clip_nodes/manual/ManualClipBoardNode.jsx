import React, { useMemo, useState } from "react";
import { Handle, Position } from "@xyflow/react";
import { NodeShell } from "../comfy/comfyNodeShared";
import "./ManualClipBoardNode.css";
import { buildMockSplitJson, buildMontageManifest, getDefaultManualClipNodeData, normalizeScene, ROUTES } from "./manualClipBoardDomain";

export default function ManualClipBoardNode({ id, data }) {
  const patch = (p) => data?.onPatchNodeData?.(id, p);
  const model = { ...getDefaultManualClipNodeData(), ...(data || {}) };
  const [splitInput, setSplitInput] = useState(model?.split_chat?.user_request || "");
  const scenes = Array.isArray(model.scenes) ? model.scenes : [];
  const aiScenes = Array.isArray(model?.split_chat?.raw_ai_json?.scenes) ? model.split_chat.raw_ai_json.scenes : [];
  const selectedScene = scenes.find((s) => s.scene_id === model.selectedSceneId) || scenes[0] || null;

  const onAudioUpload = (file) => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    patch({
      step: "audio_loaded",
      audio: { url, filename: file.name, duration_sec: Number(model?.audio?.duration_sec || 0), duration_ms: Number(model?.audio?.duration_ms || 0) },
    });
  };

  const onAskSplit = () => patch({ step: "split_chat_ready" });
  const onRunMockSplit = () => {
    const ai = buildMockSplitJson(model?.audio?.duration_sec || 24);
    patch({ step: "split_chat_ready", split_chat: { user_request: splitInput, ai_summary: ai.global_hint, raw_ai_json: ai } });
  };
  const onBuildScenes = () => {
    const rawScenes = Array.isArray(model?.split_chat?.raw_ai_json?.scenes) ? model.split_chat.raw_ai_json.scenes : [];
    const normalized = rawScenes.map((s, idx) => normalizeScene(s, idx));
    patch({ step: "scene_plan_ready", scenes: normalized, selectedSceneId: normalized[0]?.scene_id || "" });
  };

  const scenePreview = useMemo(() => buildMontageManifest(model), [model]);

  const updateScene = (sceneId, patchScene) => {
    const next = scenes.map((s) => (s.scene_id === sceneId ? normalizeScene({ ...s, ...patchScene, status: resolveStatus({ ...s, ...patchScene }) }, s.index - 1) : s));
    patch({ scenes: next });
  };

  const canAnalyze = model.step !== "empty";
  const canSplit = model.step !== "empty";
  const canBuildScenes = !!model?.split_chat?.raw_ai_json;
  const canOpenBoard = scenes.length > 0;

  const updateSplitSettings = (key, value) => {
    patch({ split_settings: { ...(model.split_settings || {}), [key]: value } });
  };

  return <NodeShell title="AI-разбивка клипа" onClose={() => data?.onRemoveNode?.(id)} icon={<span>✂️</span>} className="clipSB_nodeStoryboard manualClipBoardNode">
    <div className="manualClipBoardNode_body">
      {model.step === "empty" ? <div className="manualLockedState">
        <h3>Загрузите аудио, чтобы начать</h3>
        <label className="clipSB_btn">
          Загрузить аудио
          <input type="file" accept="audio/*" onChange={(e) => onAudioUpload(e.target.files?.[0])} hidden />
        </label>
      </div> : model.step === "director_board" && selectedScene ? <div className="manualBoardLayout"><div className="manualSceneList">{scenes.map((s) => <button key={s.scene_id} className="manualSceneItem" onClick={() => patch({ selectedSceneId: s.scene_id })}>{s.scene_id} · {s.start_sec}-{s.end_sec} · {s.status}</button>)}</div><div className="manualSceneCard"><input value={selectedScene.drama_hint} onChange={(e) => updateScene(selectedScene.scene_id, { drama_hint: e.target.value })} /><select value={selectedScene.route} onChange={(e) => updateScene(selectedScene.scene_id, { route: e.target.value })}>{ROUTES.map((r) => <option key={r} value={r}>{r}</option>)}</select><textarea placeholder="video_prompt" value={selectedScene.video_prompt} onChange={(e) => updateScene(selectedScene.scene_id, { video_prompt: e.target.value })} /><textarea placeholder="negative_prompt" value={selectedScene.negative_prompt} onChange={(e) => updateScene(selectedScene.scene_id, { negative_prompt: e.target.value })} />{selectedScene.route === "i2v_sound" ? <textarea placeholder="sound_prompt" value={selectedScene.sound_prompt} onChange={(e) => updateScene(selectedScene.scene_id, { sound_prompt: e.target.value })} /> : null}<input type="file" accept="image/*" onChange={(e) => updateScene(selectedScene.scene_id, { image_url: URL.createObjectURL(e.target.files?.[0]) })} /><div>{selectedScene.image_url ? <img alt="scene" src={selectedScene.image_url} className="manualPreview" /> : "Нет превью"}</div><div>{selectedScene.video_url ? <video src={selectedScene.video_url} controls className="manualPreview" /> : "Видео не создано"}</div><button className="clipSB_btn" onClick={() => updateScene(selectedScene.scene_id, { status: "video_ready", video_url: selectedScene.image_url || "mock://video" })}>Создать видео</button><pre>{JSON.stringify(scenePreview, null, 2)}</pre></div></div> : <div>
        <div className="manualHeaderRow">
          <div className="manualChip">Аудио: {model.audio?.url ? "готово" : "пусто"}</div>
          <div className="manualChip">Разбивка: {model.split_chat?.raw_ai_json ? "готово" : "пусто"}</div>
          <div className="manualChip">Сцен: {scenes.length}</div>
          <div className="manualChip">Режим: Клип</div>
          <div className="manualChip">Формат:
            <select value={model.format} onChange={(e) => patch({ format: e.target.value })}><option>9:16</option><option>16:9</option><option>1:1</option></select>
          </div>
          <div className="manualChip">Статус: {model.step}</div>
        </div>

        <div className="manualActionsRow">
          <label className="clipSB_btn">Загрузить аудио<input type="file" accept="audio/*" onChange={(e) => onAudioUpload(e.target.files?.[0])} hidden /></label>
          <button className="clipSB_btn" onClick={() => patch({ step: "audio_loaded" })} disabled={!canAnalyze}>Разобрать аудио</button>
          <button className="clipSB_btn" onClick={onAskSplit} disabled={!canSplit}>AI-разбивка</button>
          <button className="clipSB_btn" onClick={onBuildScenes} disabled={!canBuildScenes}>Собрать сцены</button>
          <button className="clipSB_btn" onClick={() => patch({ step: "director_board" })} disabled={!canOpenBoard}>Открыть режиссёрскую доску</button>
          <button className="clipSB_btn" onClick={() => patch(getDefaultManualClipNodeData())}>Сбросить</button>
        </div>

        <div className="manualSplitWorkspace">
          <section className="manualPanel">
            <h4>Черновик разбивки</h4>
            <p>Здесь хранится мини-задача для AI-разбивщика.</p>
            <label>Формат<select value={model.format} onChange={(e) => patch({ format: e.target.value })}><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
            <label>Цель сцен<select value={model.split_settings?.target_scene_count || "auto"} onChange={(e) => updateSplitSettings("target_scene_count", e.target.value)}><option value="auto">auto</option><option value="8">8</option><option value="10">10</option><option value="12">12</option><option value="16">16</option></select></label>
            <label>Lip-sync<select value={model.split_settings?.lipsync_ratio || "auto"} onChange={(e) => updateSplitSettings("lipsync_ratio", e.target.value)}><option value="auto">auto</option><option value="30%">30%</option><option value="50%">50%</option><option value="70%">70%</option></select></label>
            <label>Маршрут<select value={model.split_settings?.route_preference || "mixed"} onChange={(e) => updateSplitSettings("route_preference", e.target.value)}><option value="mixed">mixed</option><option value="mostly_i2v">mostly_i2v</option><option value="mostly_ia2v">mostly_ia2v</option></select></label>
            <button className="clipSB_btn" onClick={() => navigator.clipboard?.writeText(JSON.stringify(model.split_settings, null, 2))}>Скопировать JSON</button>
            <button className="clipSB_btn" onClick={() => navigator.clipboard?.writeText(splitInput)}>Скопировать задачу</button>
          </section>

          <section className="manualPanel">
            <h4>AI-чат / обсуждение клипа</h4>
            <textarea value={splitInput} onChange={(e) => setSplitInput(e.target.value)} placeholder="Например: Разбей по вокальным фразам. Криминальная драма Одесса 90-х. 30% lip-sync, остальное сюжетные сцены. Не режь слова, переходы на концах строк." />
            <button className="clipSB_btn" onClick={onRunMockSplit}>Отправить</button>
            <div className="manualAiSummary">{model.split_chat?.ai_summary || "AI-ответ появится после запроса."}</div>
            <small>AI создаёт только фразовую разбивку и краткую драматургию. Промты и фото пользователь добавляет вручную.</small>
          </section>

          <section className="manualPanel">
            <h4>Аудио-разбор</h4>
            <div>filename: {model.audio?.filename || "—"}</div>
            <div>duration_sec: {Number(model.audio?.duration_sec || 0).toFixed(2)}</div>
            <div>split_type: phrase_based</div>
            <div>candidate phrase boundaries: [0.00, 3.65, 8.20, 12.80]</div>
            <div>Анализ будет подключен позже</div>
            <button className="clipSB_btn" onClick={() => navigator.clipboard?.writeText(JSON.stringify(model.audio || {}, null, 2))}>Скопировать audio_map JSON</button>
            <button className="clipSB_btn" onClick={() => navigator.clipboard?.writeText("Анализ будет подключен позже")}>Скопировать анализ</button>
            <button className="clipSB_btn" onClick={() => navigator.clipboard?.writeText("0.00|3.65|8.20|12.80")}>Скопировать фразы</button>
          </section>

          <section className="manualPanel manualSplitPlan">
            <h4>План клипа</h4>
            {aiScenes.length === 0 ? <div>План клипа появится здесь после AI-разбивки.</div> : <div className="manualPlanRows">{aiScenes.map((s, idx) => <div key={`${s.scene_id || idx}-${idx}`} className="manualPlanRow">{s.scene_id || `SEG_${String(idx + 1).padStart(2, "0")}`} | {Number(s.start_sec || 0).toFixed(2)}–{Number(s.end_sec || 0).toFixed(2)} | {s.route || "ia2v"} | {s.quality || "check"} | {s.drama_hint || "—"}</div>)}</div>}
            <div className="manualActionsRow">
              <button className="clipSB_btn" onClick={onBuildScenes} disabled={!canBuildScenes}>Собрать сцены</button>
              <button className="clipSB_btn" onClick={() => patch({ step: "director_board" })} disabled={!canOpenBoard}>Открыть режиссёрскую доску</button>
            </div>
          </section>
        </div>
      </div>}
    </div>
    <Handle type="target" position={Position.Left} id="audio_in" />
  </NodeShell>;
}

function resolveStatus(scene) {
  if (scene.video_url) return "video_ready";
  if (scene.video_prompt && scene.image_url) return "prompt_ready";
  if (scene.image_url) return "photo_loaded";
  return "draft";
}
