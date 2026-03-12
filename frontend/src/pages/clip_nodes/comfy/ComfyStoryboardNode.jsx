import React from "react";
import { Handle, Position, NodeShell, getModeDisplayMeta, getStyleDisplayMeta, handleStyle } from "./comfyNodeShared";

export default function ComfyStoryboardNode({ id, data }) {
  const scenes = Array.isArray(data?.mockScenes) ? data.mockScenes : [];
  const modeMeta = getModeDisplayMeta(data?.mode || "clip");
  const styleMeta = getStyleDisplayMeta(data?.stylePreset || "realism");
  const parseStatus = data?.parseStatus || "idle";
  const readyImages = scenes.filter((scene) => !!scene?.imageUrl).length;
  const readyVideos = scenes.filter((scene) => !!scene?.videoUrl).length;
  const totalScenes = scenes.length || Number(data?.sceneCount || 0);
  const isUpdating = parseStatus === "updating";
  const hasScenes = totalScenes > 0;
  const canOpenEditor = !isUpdating && hasScenes;
  const isReady = parseStatus === "ready";
  const isError = parseStatus === "error";
  const statusLabel = isUpdating
    ? "обновляется..."
    : isReady
      ? (hasScenes ? `${totalScenes} сцен готово` : "Storyboard готов")
      : isError
        ? "ошибка"
        : "ожидание";
  const statusHint = isUpdating
    ? "Получаю сцены из COMFY BRAIN..."
    : isReady
      ? (hasScenes ? "Сцены готовы. Можно открыть editor." : "Сцены ещё не готовы для editor.")
      : isError
        ? "Storyboard не обновлён из-за ошибки parse."
        : "Ожидает parse из COMFY BRAIN.";
  const storyboardStateClass = isUpdating
    ? "clipSB_nodeComfyStoryboardStateUpdating"
    : isReady
      ? "clipSB_nodeComfyStoryboardStateReady"
      : isError
        ? "clipSB_nodeComfyStoryboardStateError"
        : "";

  return (<>
    <Handle type="target" position={Position.Left} id="comfy_plan" className="clipSB_handle" style={handleStyle("comfy_plan")} />
    <Handle type="source" position={Position.Right} id="comfy_scene_video_out" className="clipSB_handle" style={handleStyle("comfy_video", { top: 56 })} />
    <NodeShell title="COMFY STORYBOARD" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧩</span>} className={`clipSB_nodeComfyStoryboard ${storyboardStateClass}`.trim()}>
      <div className="clipSB_assemblyStats" style={{ marginTop: 4 }}>
        <div className="clipSB_assemblyRow"><span>Сцен</span><strong>{totalScenes}</strong></div>
        <div className="clipSB_assemblyRow"><span>Фото</span><strong>{readyImages}/{totalScenes || 0}</strong></div>
        <div className="clipSB_assemblyRow"><span>Видео</span><strong>{readyVideos}/{totalScenes || 0}</strong></div>
        <div className="clipSB_assemblyRow"><span>Статус</span><strong>{statusLabel}</strong></div>
      </div>

      <div className="clipSB_small" style={{ marginTop: 8 }}>
        {modeMeta.labelRu} • {styleMeta.labelRu}
      </div>
      <div className="clipSB_selectHint" style={{ marginTop: 6 }}>{statusHint}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button className="clipSB_btn" onClick={() => data?.onOpenComfy?.(id)} disabled={!canOpenEditor}>Открыть editor</button>
      </div>
    </NodeShell>
  </>);
}
