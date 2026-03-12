import React from "react";
import { Handle, Position, NodeShell, getModeDisplayMeta, getStyleDisplayMeta, handleStyle } from "./comfyNodeShared";

export default function ComfyStoryboardNode({ id, data }) {
  const scenes = Array.isArray(data?.mockScenes) ? data.mockScenes : [];
  const modeMeta = getModeDisplayMeta(data?.mode || "clip");
  const styleMeta = getStyleDisplayMeta(data?.stylePreset || "realism");
  const parseStatus = data?.parseStatus || "idle";
  const summaryScene = scenes[0] || null;
  const debugFields = data?.debugFields || {};
  const readyImages = scenes.filter((scene) => !!scene?.imageUrl).length;
  const readyVideos = scenes.filter((scene) => !!scene?.videoUrl).length;
  const warnings = Array.isArray(data?.warnings) ? data.warnings : [];

  return (<>
    <Handle type="target" position={Position.Left} id="comfy_plan" className="clipSB_handle" style={handleStyle("comfy_plan")} />
    <Handle type="source" position={Position.Right} id="comfy_scene_video_out" className="clipSB_handle" style={handleStyle("comfy_video", { top: 56 })} />
    <NodeShell title="COMFY STORYBOARD" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧩</span>} className="clipSB_nodeComfyStoryboard">
      <div className="clipSB_badge">STORYBOARD EDITOR</div>
      <div className="clipSB_assemblyStats" style={{ marginTop: 8 }}>
        <div className="clipSB_assemblyRow"><span>Сцен</span><strong>{scenes.length || Number(data?.sceneCount || 0)}</strong></div>
        <div className="clipSB_assemblyRow"><span>Фото готово</span><strong>{readyImages}/{scenes.length || 0}</strong></div>
        <div className="clipSB_assemblyRow"><span>Видео готово</span><strong>{readyVideos}/{scenes.length || 0}</strong></div>
        <div className="clipSB_assemblyRow"><span>Статус</span><strong>{parseStatus}</strong></div>
      </div>

      <div className="clipSB_small" style={{ marginTop: 8 }}>
        mode: {modeMeta.labelRu} • style: {styleMeta.labelRu}
      </div>
      <div className="clipSB_small">output: {data?.output || "comfy image"} • mission: {debugFields.storyMissionSummary || data?.storyMissionSummary || "none"}</div>
      <div className="clipSB_small">scene focus: {summaryScene?.sceneNarrativeStep || summaryScene?.sceneGoal || data?.narrativeSource || "none"}</div>

      {warnings.length ? (
        <div className="clipSB_hint" style={{ marginTop: 8, color: "#ffb86c" }}>
          warnings: {warnings.slice(0, 2).join(" • ")}
        </div>
      ) : null}

      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button className="clipSB_btn" onClick={() => data?.onOpenComfy?.(id)}>Открыть editor</button>
        <button className="clipSB_btn clipSB_btnSecondary" disabled title="Подключение пакетных действий в следующем шаге">Batch actions soon</button>
      </div>
    </NodeShell>
  </>);
}
