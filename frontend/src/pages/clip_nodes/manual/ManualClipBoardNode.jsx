import React, { useMemo, useState } from "react";
import { Handle, Position } from "@xyflow/react";
import { useNavigate } from "react-router-dom";
import { NodeShell } from "../comfy/comfyNodeShared";
import "./ManualClipBoardNode.css";
import { buildMockSplitJson, buildMontageManifest, getDefaultManualClipNodeData, normalizeManualAudio } from "./manualClipBoardDomain";

export default function ManualClipBoardNode({ id, data }) {
  const navigate = useNavigate();
  const patch = (p) => data?.onPatchNodeData?.(id, p);
  const model = { ...getDefaultManualClipNodeData(), ...(data || {}) };
  const [splitInput, setSplitInput] = useState(model?.split_chat?.user_request || "");
  const scenes = Array.isArray(model.scenes) ? model.scenes : [];
  const connectedAudio = data?.connectedInputs?.audio_in || data?.connectedAudio || data?.audioInput || null;
  const normalizedConnectedAudio = normalizeManualAudio(connectedAudio);
  const effectiveAudio = normalizedConnectedAudio?.url ? normalizedConnectedAudio : model.audio;
  const aiScenes = Array.isArray(model?.split_chat?.raw_ai_json?.scenes) ? model.split_chat.raw_ai_json.scenes : [];

  const onAudioUpload = (file) => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    const audioEl = new Audio();
    audioEl.preload = "metadata";

    audioEl.onloadedmetadata = () => {
      const durationSec = Number(audioEl.duration || 0);
      patch({
        step: "audio_loaded",
        audio_source: "local_upload",
        audio: {
          url,
          filename: file.name,
          duration_sec: Number(durationSec.toFixed(3)),
          duration_ms: Math.round(durationSec * 1000),
        },
      });
    };

    audioEl.onerror = () => {
      patch({
        step: "audio_loaded",
        audio_source: "local_upload",
        audio: {
          url,
          filename: file.name,
          duration_sec: 0,
          duration_ms: 0,
        },
      });
    };

    audioEl.src = url;
  };

  const onAskSplit = () => patch({ step: "split_chat_ready" });
  const onRunMockSplit = () => {
    const ai = buildMockSplitJson(effectiveAudio?.duration_sec || 24);
    patch({ step: "split_chat_ready", split_chat: { user_request: splitInput, ai_summary: ai.global_hint, raw_ai_json: ai } });
  };
  const onBuildScenes = () => {
    const rawScenes = Array.isArray(model?.split_chat?.raw_ai_json?.scenes) ? model.split_chat.raw_ai_json.scenes : [];
    const normalized = rawScenes.map((s, idx) => ({ ...s, index: idx + 1, scene_id: s.scene_id || `seg_${String(idx + 1).padStart(2, "0")}` }));
    patch({ step: "scene_plan_ready", scenes: normalized, selectedSceneId: normalized[0]?.scene_id || "" });
  };

  const scenePreview = useMemo(() => buildMontageManifest(model), [model]);

  const canAnalyze = model.step !== "empty";
  const canSplit = model.step !== "empty";
  const canBuildScenes = !!model?.split_chat?.raw_ai_json;
  const canOpenBoard = scenes.length > 0;

  const updateSplitSettings = (key, value) => {
    patch({ split_settings: { ...(model.split_settings || {}), [key]: value } });
  };

  const onOpenDirectorBoard = () => {
    const payload = {
      nodeId: id,
      mode: model.mode,
      format: model.format,
      audio: model.audio,
      split_chat: model.split_chat,
      scenes,
    };
    localStorage.setItem("manual_clip_board_active_project", JSON.stringify(payload));
    navigate("/studio/manual-clip-board");
  };

  return <NodeShell title="AI-разбивка клипа" onClose={() => data?.onRemoveNode?.(id)} icon={<span>✂️</span>} className="clipSB_nodeStoryboard manualClipBoardNode">
    <div className="manualClipBoardNode_body">
      {model.step === "empty" ? <div className="manualLockedState">
        <h3>Подключите аудио-ноду или загрузите аудио вручную</h3>
        <label className="clipSB_btn">
          Загрузить аудио
          <input type="file" accept="audio/*" onChange={(e) => onAudioUpload(e.target.files?.[0])} hidden />
        </label>
      </div> : <div>
        <div className="manualHeaderRow">
          <div className="manualChip">Аудио: {model.audio_source === "connected_audio_node" ? "подключено" : model.audio_source === "local_upload" ? "локальное" : effectiveAudio?.url ? "готово" : "пусто"}</div>
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
          <button className="clipSB_btn" onClick={onOpenDirectorBoard} disabled={!canOpenBoard}>Перейти в режиссёрскую доску</button>
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
            <div>filename: {effectiveAudio?.filename || "—"}</div>
            <div>duration_sec: {Number(effectiveAudio?.duration_sec || 0).toFixed(2)}</div>
            <div>split_type: phrase_based</div>
            <div>candidate phrase boundaries: [0.00, 3.65, 8.20, 12.80]</div>
            <div>Анализ будет подключен позже</div>
            <button className="clipSB_btn" onClick={() => navigator.clipboard?.writeText(JSON.stringify(effectiveAudio || {}, null, 2))}>Скопировать audio_map JSON</button>
            <button className="clipSB_btn" onClick={() => navigator.clipboard?.writeText("Анализ будет подключен позже")}>Скопировать анализ</button>
            <button className="clipSB_btn" onClick={() => navigator.clipboard?.writeText("0.00|3.65|8.20|12.80")}>Скопировать фразы</button>
          </section>

          <section className="manualPanel manualSplitPlan">
            <h4>План клипа</h4>
            {aiScenes.length === 0 ? <div>План клипа появится здесь после AI-разбивки.</div> : <div className="manualPlanRows">{aiScenes.map((s, idx) => <div key={`${s.scene_id || idx}-${idx}`} className="manualPlanRow">{(s.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`).toUpperCase()} | {Number(s.start_sec || 0).toFixed(2)}–{Number(s.end_sec || 0).toFixed(2)} | {s.route || "ia2v"} | {s.quality || "check"} | {s.drama_hint || "—"}</div>)}</div>}
            <div className="manualActionsRow">
              <button className="clipSB_btn" onClick={onBuildScenes} disabled={!canBuildScenes}>Собрать сцены</button>
              <button className="clipSB_btn" onClick={onOpenDirectorBoard} disabled={!canOpenBoard}>Перейти в режиссёрскую доску</button>
            </div>
            <pre>{JSON.stringify(scenePreview, null, 2)}</pre>
          </section>
        </div>
      </div>}
    </div>
    <Handle type="target" position={Position.Left} id="audio_in" />
  </NodeShell>;
}
