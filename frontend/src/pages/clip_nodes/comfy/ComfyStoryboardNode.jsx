import React from "react";
import { Handle, Position, NodeShell, getModeDisplayMeta, getStyleDisplayMeta, handleStyle } from "./comfyNodeShared";

export default function ComfyStoryboardNode({ id, data }) {
  const scenes = Array.isArray(data?.mockScenes) ? data.mockScenes : [];
  const modeMeta = getModeDisplayMeta(data?.mode || "clip");
  const styleMeta = getStyleDisplayMeta(data?.stylePreset || "realism");
  const summaryScene = scenes[0] || null;
  return (<>
    <Handle type="target" position={Position.Left} id="comfy_plan" className="clipSB_handle" style={handleStyle("comfy_plan")} />
    <Handle type="source" position={Position.Right} id="comfy_scene_video_out" className="clipSB_handle" style={handleStyle("comfy_video", { top: 56 })} />
    <NodeShell title="COMFY STORYBOARD" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧩</span>} className="clipSB_nodeComfyStoryboard">
      <div className="clipSB_badge">EDITOR READY</div>
      <div className="clipSB_small">scene count: {scenes.length || Number(data?.sceneCount || 0)}</div>
      <div className="clipSB_small">mode: {modeMeta.labelRu} • style: {styleMeta.labelRu}</div>
      <div className="clipSB_small">status: {data?.parseStatus || "idle"} • output: {data?.output || "comfy image"}</div>
      <div className="clipSB_small">narrative: {summaryScene?.sceneNarrativeStep || data?.narrativeSource || "none"} • timeline: {data?.timelineSource || "logic"}</div>
      <button className="clipSB_btn" style={{ marginTop: 10 }} onClick={() => data?.onOpenComfy?.(id)}>Сценарий</button>
    </NodeShell>
  </>);
}
