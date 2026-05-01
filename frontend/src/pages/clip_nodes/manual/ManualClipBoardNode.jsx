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
  const selectedScene = scenes.find((s) => s.scene_id === model.selectedSceneId) || scenes[0] || null;

  const onAudioUpload = (file) => {
    if (!file) return;
    const url = URL.createObjectURL(file);
    patch({ step: "audio_loaded", audio: { url, filename: file.name, duration_sec: Number(model?.audio?.duration_sec || 0), duration_ms: Number(model?.audio?.duration_ms || 0) } });
  };

  const onAskSplit = () => patch({ step: "split_chat_ready" });
  const onRunMockSplit = () => {
    const ai = buildMockSplitJson(model?.audio?.duration_sec || 24);
    patch({ split_chat: { user_request: splitInput, ai_summary: ai.global_hint, raw_ai_json: ai } });
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

  return <NodeShell title="AI Split Manual Clip" onClose={() => data?.onRemoveNode?.(id)} icon={<span>✂️</span>} className="clipSB_nodeStoryboard manualClipBoardNode">
    <div className="manualClipBoardNode_body">
      {model.step === "empty" && <div><p>Загрузите аудио, чтобы начать</p><input type="file" accept="audio/*" onChange={(e) => onAudioUpload(e.target.files?.[0])} /></div>}
      {model.step !== "empty" && <div>
        <div>{model.audio?.filename || "—"}</div><div>{Number(model.audio?.duration_sec || 0).toFixed(2)}s</div>
        <select value={model.format} onChange={(e) => patch({ format: e.target.value })}><option>9:16</option><option>16:9</option><option>1:1</option></select>
      </div>}
      {model.step === "audio_loaded" && <button className="clipSB_btn" onClick={onAskSplit}>Разбить по фразам</button>}
      {model.step === "split_chat_ready" && <div><h4>AI-разбивщик</h4><textarea value={splitInput} onChange={(e) => setSplitInput(e.target.value)} placeholder="Опиши, как разбить клип..." /><button className="clipSB_btn" onClick={onRunMockSplit}>Получить JSON</button>{model.split_chat?.raw_ai_json ? <button className="clipSB_btn" onClick={onBuildScenes}>Собрать сцены</button> : null}</div>}
      {model.step === "scene_plan_ready" && <div><h4>Сцены</h4>{scenes.map((s) => <div key={s.scene_id}>{s.scene_id} · {s.start_sec}-{s.end_sec} · {s.route} · {s.quality}</div>)}<button className="clipSB_btn" onClick={() => patch({ step: "director_board" })}>Открыть режиссёрскую доску</button></div>}
      {model.step === "director_board" && selectedScene && <div className="manualBoardLayout"><div className="manualSceneList">{scenes.map((s) => <button key={s.scene_id} className="manualSceneItem" onClick={() => patch({ selectedSceneId: s.scene_id })}>{s.scene_id} · {s.start_sec}-{s.end_sec} · {s.status}</button>)}</div><div className="manualSceneCard"><input value={selectedScene.drama_hint} onChange={(e) => updateScene(selectedScene.scene_id, { drama_hint: e.target.value })} /><select value={selectedScene.route} onChange={(e) => updateScene(selectedScene.scene_id, { route: e.target.value })}>{ROUTES.map((r) => <option key={r} value={r}>{r}</option>)}</select><textarea placeholder="Видео-промт" value={selectedScene.video_prompt} onChange={(e) => updateScene(selectedScene.scene_id, { video_prompt: e.target.value })} /><textarea placeholder="Негативный промт" value={selectedScene.negative_prompt} onChange={(e) => updateScene(selectedScene.scene_id, { negative_prompt: e.target.value })} />{selectedScene.route === "i2v_sound" ? <textarea placeholder="sound_prompt" value={selectedScene.sound_prompt} onChange={(e) => updateScene(selectedScene.scene_id, { sound_prompt: e.target.value })} /> : null}<input type="file" accept="image/*" onChange={(e) => updateScene(selectedScene.scene_id, { image_url: URL.createObjectURL(e.target.files?.[0]) })} /><button className="clipSB_btn" onClick={() => updateScene(selectedScene.scene_id, { status: "video_ready", video_url: selectedScene.image_url || "mock://video" })}>Создать видео</button><pre>{JSON.stringify(scenePreview, null, 2)}</pre></div></div>}
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
