import React, { useState } from "react";
import { fetchJson } from "../../../services/api.js";
import { Handle, Position } from "@xyflow/react";
import { useNavigate } from "react-router-dom";
import { NodeShell } from "../comfy/comfyNodeShared";
import "./ManualClipBoardNode.css";
import { buildManualAudioSlicePayload, buildManualClipSampleJson, buildMockSplitJson, getDefaultManualClipNodeData, normalizeManualAudio, normalizeScene, parseManualSplitJson } from "./manualClipBoardDomain";



function getSceneContractKey(scene = {}) {
  return [
    String(scene.scene_id || ""),
    Number(scene.start_sec || 0).toFixed(3),
    Number(scene.end_sec || 0).toFixed(3),
    String(scene.route || ""),
  ].join("|");
}

function mergeDirectorSceneWork(currentScenes = [], existingScenes = []) {
  const existingByKey = new Map(existingScenes.map((scene) => [getSceneContractKey(scene), scene]));
  return currentScenes.map((scene) => {
    const old = existingByKey.get(getSceneContractKey(scene));
    if (!old) return scene;
    return {
      ...scene,
      image_url: old.image_url || scene.image_url || "",
      video_url: old.video_url || scene.video_url || "",
      video_prompt: old.video_prompt || scene.video_prompt || "",
      negative_prompt: old.negative_prompt || scene.negative_prompt || "",
      sound_prompt: old.sound_prompt || scene.sound_prompt || "",
      audio_slice_url: old.audio_slice_url || scene.audio_slice_url || "",
      audio_slice_duration_sec: old.audio_slice_duration_sec || scene.audio_slice_duration_sec || 0,
      audio_extracted: Boolean(old.audio_extracted || scene.audio_extracted),
      status: old.status || scene.status || "draft",
      error: old.error || scene.error || "",
    };
  });
}


async function sliceManualClipAudio(payload) {
  const data = await fetchJson("/api/manual-clip/slice-audio", { method: "POST", body: payload });
  if (data?.ok === false) {
    throw new Error(String(data?.detail || "audio_slice_failed"));
  }
  return data;
}

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
  const planKind = model.project_kind === "story" ? "истории" : "клипа";

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

  const onRunMockSplit = () => {
    const ai = buildMockSplitJson({
      projectKind: model.project_kind,
      splitSettings: model.split_settings,
      format: model.format,
      durationSec: effectiveAudio?.duration_sec || 24,
    });
    patch({
      step: "split_chat_ready",
      last_split_source: "ai",
      split_chat: { user_request: splitInput, ai_summary: ai.global_hint, raw_ai_json: ai },
      json_error: "",
    });
  };

  const onRunJsonSplit = () => {
    const parsed = parseManualSplitJson(model.json_input);
    if (!parsed.ok) {
      patch({ json_error: parsed.error });
      return;
    }

    patch({
      step: "split_chat_ready",
      last_split_source: "json",
      project_kind: parsed.splitJson.project_kind || model.project_kind,
      format: parsed.splitJson.format || model.format,
      split_chat: {
        user_request: "JSON import",
        ai_summary: parsed.splitJson.global_hint || "Разбивка загружена из JSON.",
        raw_ai_json: parsed.splitJson,
      },
      json_error: "",
    });
  };

  const onInsertSampleJson = () => {
    const sample = buildManualClipSampleJson({
      projectKind: model.project_kind,
      durationSec: effectiveAudio?.duration_sec || 56,
      format: model.format,
    });
    patch({ json_input: JSON.stringify(sample, null, 2), json_error: "" });
  };

  const onCopySampleJson = () => {
    const sample = buildManualClipSampleJson({
      projectKind: model.project_kind,
      durationSec: effectiveAudio?.duration_sec || 56,
      format: model.format,
    });
    navigator.clipboard?.writeText(JSON.stringify(sample, null, 2));
  };

  const onBuildScenes = async () => {
    const splitJson = model?.split_chat?.raw_ai_json;
    const rawScenes = Array.isArray(splitJson?.scenes) ? splitJson.scenes : [];
    const normalized = rawScenes.map((s, idx) => normalizeScene(s, idx));
    let mergedScenes = normalized;
    let splitAudioError = "";
    let splitAudioStatus = "idle";
    let splitAudioCount = 0;

    if (effectiveAudio?.url) {
      splitAudioStatus = "slicing";
      try {
        const payload = buildManualAudioSlicePayload({ audio: effectiveAudio, splitJson });
        const sliced = await sliceManualClipAudio(payload);
        const byId = new Map((Array.isArray(sliced?.scenes) ? sliced.scenes : []).map((scene) => [String(scene?.scene_id || ""), scene]));
        mergedScenes = normalized.map((scene) => {
          const fromBackend = byId.get(scene.scene_id);
          if (!fromBackend) return scene;
          return {
            ...scene,
            audio_slice_url: String(fromBackend.audio_slice_url || ""),
            audio_slice_duration_sec: Number(fromBackend.audio_slice_duration_sec || 0),
          };
        });
        splitAudioStatus = "done";
        splitAudioCount = mergedScenes.filter((scene) => !!scene.audio_slice_url).length;
      } catch (error) {
        splitAudioStatus = "error";
        splitAudioError = String(error?.message || "audio_slice_failed");
      }
    }

    patch({
      step: "scene_plan_ready",
      scenes: mergedScenes,
      selectedSceneId: mergedScenes[0]?.scene_id || "",
      split_audio_status: splitAudioStatus,
      split_audio_error: splitAudioError,
      split_audio_count: splitAudioCount,
    });
  };

  const canBuildScenes = !!model?.split_chat?.raw_ai_json;
  const canOpenBoard = scenes.length > 0;

  const updateSplitSettings = (key, value) => {
    patch({ split_settings: { ...(model.split_settings || {}), [key]: value } });
  };

  const onOpenDirectorBoard = () => {
    let nextScenes = scenes;
    try {
      const raw = localStorage.getItem("manual_clip_board_active_project");
      const existingProject = raw ? JSON.parse(raw) : null;
      if (existingProject?.nodeId === id) {
        const existingScenes = Array.isArray(existingProject?.scenes) ? existingProject.scenes : [];
        const currentKeys = scenes.map(getSceneContractKey);
        const existingKeys = existingScenes.map(getSceneContractKey);
        const isSameContract = currentKeys.length === existingKeys.length && currentKeys.every((key, idx) => key === existingKeys[idx]);
        if (isSameContract) {
          nextScenes = mergeDirectorSceneWork(scenes, existingScenes);
        }
      }
    } catch {
      nextScenes = scenes;
    }

    const payload = {
      nodeId: id,
      mode: model.mode,
      format: model.format,
      audio: effectiveAudio,
      split_chat: model.split_chat,
      project_kind: model.project_kind,
      last_split_source: model.last_split_source,
      scenes: nextScenes,
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
          <div className="manualChip">Режим: {model.project_kind === "story" ? "История" : "Клип"}</div>
          <div className="manualChip">Формат:
            <select value={model.format} onChange={(e) => patch({ format: e.target.value })}><option>9:16</option><option>16:9</option><option>1:1</option></select>
          </div>
          <div className="manualChip">Статус: {model.step}</div>
          <button className="clipSB_btn" onClick={() => patch(getDefaultManualClipNodeData())}>Сбросить</button>
        </div>

        <div className="manualSplitWorkspace">
          <section className="manualPanel manualPanelDraft">
            <h4>Черновик разбивки</h4>
            <p>Здесь хранится мини-задача для AI-разбивщика.</p>
            <label>Тип проекта<select value={model.project_kind} onChange={(e) => patch({ project_kind: e.target.value })}><option value="clip">Клип</option><option value="story">История</option></select></label>
            <label>Формат<select value={model.format} onChange={(e) => patch({ format: e.target.value })}><option>9:16</option><option>16:9</option><option>1:1</option></select></label>
            <label>Цель сцен<select value={model.split_settings?.target_scene_count || "auto"} onChange={(e) => updateSplitSettings("target_scene_count", e.target.value)}><option value="auto">auto</option><option value="8">8</option><option value="10">10</option><option value="12">12</option><option value="16">16</option></select></label>
            <label>Lip-sync<select value={model.split_settings?.lipsync_ratio || "auto"} onChange={(e) => updateSplitSettings("lipsync_ratio", e.target.value)}><option value="auto">auto</option><option value="30%">30%</option><option value="50%">50%</option><option value="70%">70%</option></select></label>
            <label>Маршрут<select value={model.split_settings?.route_preference || "mixed"} onChange={(e) => updateSplitSettings("route_preference", e.target.value)}><option value="mixed">mixed</option><option value="mostly_i2v">mostly_i2v</option><option value="mostly_ia2v">mostly_ia2v</option></select></label>
          </section>

          <section className="manualPanel manualPanelAi">
            <h4>AI/JSON разбивка</h4>
            <label>Способ разбора<select value={model.split_source} onChange={(e) => patch({ split_source: e.target.value })}><option value="ai">AI</option><option value="json">JSON</option></select></label>
            {model.split_source === "ai" ? <>
              <textarea className="manualAiTextarea" value={splitInput} onChange={(e) => setSplitInput(e.target.value)} placeholder="Например: Разбей по вокальным фразам. Криминальная драма Одесса 90-х. 30% lip-sync, остальное сюжетные сцены. Не режь слова, переходы на концах строк." />
              <button className="clipSB_btn" onClick={onRunMockSplit}>Разобрать при помощи AI</button>
              <div className="manualAiSummary">{model.split_chat?.ai_summary || "AI-ответ появится после запроса."}</div>
              <small>AI создаёт только фразовую разбивку и краткую драматургию. Промты и фото пользователь добавляет вручную.</small>
</> : <>
              <div className="manualActionsRow">
                <button className="clipSB_btn" onClick={onInsertSampleJson}>Вставить образец JSON</button>
                <button className="clipSB_btn" onClick={onCopySampleJson}>Скопировать образец JSON</button>
              </div>
              <small>Образец показывает структуру. Для реального клипа/истории заполните scenes на всю длину аудио.</small>
              <textarea className="manualJsonTextarea" value={model.json_input || ""} onChange={(e) => patch({ json_input: e.target.value })} placeholder="Вставьте JSON разбивки..." />
              <button className="clipSB_btn" onClick={onRunJsonSplit}>Разобрать при помощи JSON</button>
              {model.json_error ? <div className="manualJsonError">{model.json_error}</div> : null}
            </>}
          </section>

          <section className="manualPanel manualPanelAudio">
            <h4>Аудио-разбор</h4>
            <div>filename: {effectiveAudio?.filename || "—"}</div>
            <div>duration_sec: {Number(effectiveAudio?.duration_sec || 0).toFixed(2)}</div>
            <div>split_type: phrase_based</div>
            <div>candidate phrase boundaries: [0.00, 3.65, 8.20, 12.80]</div>
            <div>Анализ будет подключен позже</div>
          </section>

          <section className="manualPanel manualPanelPlan manualSplitPlan">
            <h4>План {planKind}</h4>
            {aiScenes.length === 0 ? <div>План появится после AI-разбивки или разбора JSON.</div> : <>
              <div className="manualPlanRows">{aiScenes.map((s, idx) => <div key={`${s.scene_id || idx}-${idx}`} className="manualPlanRow">{(s.scene_id || `seg_${String(idx + 1).padStart(2, "0")}`).toUpperCase()} | {Number(s.start_sec || 0).toFixed(2)}–{Number(s.end_sec || 0).toFixed(2)} | {s.route || "ia2v"} | {s.quality || "check"} | {s.drama_hint || "—"}</div>)}</div>
              <div className="manualActionsRow">
                <button className="clipSB_btn" onClick={onBuildScenes} disabled={!canBuildScenes}>Собрать сцены</button>
              </div>
            </>}
            {model.split_audio_error ? <div className="manualJsonError">Сцены собраны, но аудио-нарезка не удалась: {model.split_audio_error}</div> : null}
            {model.split_audio_status === "done" ? <div>Аудио сцен нарезано: {Number(model.split_audio_count || 0)}</div> : null}
            {scenes.length > 0 ? <div className="manualActionsRow">
              <button className="clipSB_btn" onClick={onOpenDirectorBoard} disabled={!canOpenBoard}>Перейти в режиссёрскую доску</button>
            </div> : null}
          </section>
        </div>
      </div>}
    </div>
    <Handle type="target" position={Position.Left} id="audio_in" />
  </NodeShell>;
}
