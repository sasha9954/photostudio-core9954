import React from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";

const STAGE_ORDER = ["story_core", "audio_map", "role_plan", "scene_plan", "scene_prompts", "finalize"];

function countByStatus(stageStatuses = {}) {
  const counters = { idle: 0, running: 0, done: 0, stale: 0, error: 0 };
  STAGE_ORDER.forEach((stageId) => {
    const status = String(stageStatuses?.[stageId]?.status || "idle").trim().toLowerCase() || "idle";
    if (!Object.prototype.hasOwnProperty.call(counters, status)) counters.idle += 1;
    else counters[status] += 1;
  });
  return counters;
}

export default function ScenarioPipelineDebugNode({ id, data }) {
  const contentType = String(data?.contentType || "story").trim();
  const format = String(data?.format || "9:16").trim();
  const refsCount = Number(data?.refsCount || 0) || 0;
  const rolesCount = Number(data?.rolesCount || 0) || 0;
  const hasAudio = Boolean(data?.hasAudio);
  const statusSummary = countByStatus(data?.stageStatuses && typeof data.stageStatuses === "object" ? data.stageStatuses : {});

  return (
    <>
      <Handle type="target" position={Position.Left} id="scenario_pipeline_debug_in" className="clipSB_handle" style={handleStyle("scenario_storyboard_in")} />
      <NodeShell title="SCENARIO PIPELINE DEBUG" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧪</span>} className="clipSB_nodeStoryboard">
        <div className="clipSB_assemblyStats" style={{ marginTop: 4 }}>
          <div className="clipSB_assemblyRow"><span>Mode</span><strong>{contentType || "—"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Format</span><strong>{format || "—"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Refs / Roles</span><strong>{refsCount} / {rolesCount}</strong></div>
          <div className="clipSB_assemblyRow"><span>Audio</span><strong>{hasAudio ? "yes" : "no"}</strong></div>
          <div className="clipSB_assemblyRow"><span>Done</span><strong>{statusSummary.done}/6</strong></div>
        </div>
        <div className="clipSB_selectHint" style={{ marginTop: 8 }}>Stage-by-stage pipeline lab.</div>
        <button className="clipSB_btn" style={{ marginTop: 10 }} type="button" onClick={() => data?.onOpenScenarioPipelineDebug?.(id)}>
          Open editor
        </button>
      </NodeShell>
    </>
  );
}
