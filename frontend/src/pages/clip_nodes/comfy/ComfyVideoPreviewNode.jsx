import React from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";

export default function ComfyVideoPreviewNode({ id, data }) {
  const scenarioPreview = data?.scenarioPreview && typeof data.scenarioPreview === "object" ? data.scenarioPreview : null;
  return (<>
    <Handle type="target" position={Position.Left} id="comfy_scene_video_out" className="clipSB_handle" style={handleStyle("comfy_video")} />
    <Handle type="target" position={Position.Left} id="scenario_preview_in" className="clipSB_handle" style={handleStyle("scenario_preview_in", { top: 86 })} />
    <NodeShell title="COMFY VIDEO PREVIEW" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎬</span>} className="clipSB_nodeComfyVideo">
      <div className="clipSB_badge">MP4 PREVIEW</div>
      <div className="clipSB_previewCard">{data?.previewUrl ? <video src={data.previewUrl} controls className="clipSB_videoPlayer" /> : <div className="clipSB_small">No preview yet</div>}</div>
      <div className="clipSB_small">{data?.previewStatus || "idle"} • {data?.workflowPreset || "comfy-default"} • {data?.format || "9:16"} • {data?.duration || 0}s</div>
      {scenarioPreview ? (
        <div className="clipSB_small" style={{ marginTop: 8 }}>
          <div><strong>Story (RU):</strong> {scenarioPreview.storySummaryRu || "—"}</div>
          <div><strong>Story (EN):</strong> {scenarioPreview.storySummaryEn || "—"}</div>
          <div><strong>World:</strong> {scenarioPreview.worldRu || scenarioPreview.worldEn || "—"}</div>
          <div><strong>Actors:</strong> {Array.isArray(scenarioPreview.actors) && scenarioPreview.actors.length ? scenarioPreview.actors.join(", ") : "—"}</div>
          <div><strong>Style:</strong> {scenarioPreview.styleProfile || "—"}</div>
        </div>
      ) : null}
    </NodeShell>
  </>);
}
