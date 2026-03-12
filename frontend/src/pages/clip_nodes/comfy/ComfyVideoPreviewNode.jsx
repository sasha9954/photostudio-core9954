import React from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";

export default function ComfyVideoPreviewNode({ id, data }) {
  return (<>
    <Handle type="target" position={Position.Left} id="comfy_scene_video_out" className="clipSB_handle" style={handleStyle("comfy_video")} />
    <NodeShell title="COMFY VIDEO PREVIEW" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎬</span>} className="clipSB_nodeComfyVideo">
      <div className="clipSB_badge">MP4 PREVIEW</div>
      <div className="clipSB_previewCard">{data?.previewUrl ? <video src={data.previewUrl} controls className="clipSB_videoPlayer" /> : <div className="clipSB_small">No preview yet</div>}</div>
      <div className="clipSB_small">{data?.previewStatus || "idle"} • {data?.workflowPreset || "comfy-default"} • {data?.format || "9:16"} • {data?.duration || 0}s</div>
    </NodeShell>
  </>);
}
