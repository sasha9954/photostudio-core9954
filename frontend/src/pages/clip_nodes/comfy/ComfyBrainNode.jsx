import React from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";

export default function ComfyBrainNode({ id, data }) {
  const mode = data?.mode || "clip";
  const plannerMode = data?.plannerMode || "legacy";
  const output = data?.output || "comfy image";
  const parseStatus = data?.parseStatus || "idle";
  const isParsing = parseStatus === "parsing";
  const isReady = parseStatus === "ready";
  const isError = parseStatus === "error";
  const parseButtonLabel = isParsing ? "Разбираю..." : "Разобрать";
  const brainStateClass = isParsing
    ? "clipSB_nodeComfyBrainStateParsing"
    : isReady
      ? "clipSB_nodeComfyBrainStateReady"
      : isError
        ? "clipSB_nodeComfyBrainStateError"
        : "";
  const plannerModeClass = plannerMode === "gemini_only" ? "clipSB_nodeComfyBrainPlannerGemini" : "clipSB_nodeComfyBrainPlannerLegacy";
  const visibleMode = ["clip", "kino"].includes(String(mode || "").toLowerCase()) ? mode : "clip";
  const visibleOutput = output === "comfy image" ? output : "comfy image";

  return (<>
    {["audio","text","ref_character_1","ref_character_2","ref_character_3","ref_animal","ref_group","ref_location","ref_style","ref_props"].map((h, i) => (
      <Handle key={h} type="target" position={Position.Left} id={h} className="clipSB_handle" style={handleStyle(h === "ref_props" ? "ref_items" : h, { top: 36 + i * 18 })} />
    ))}
    <Handle type="source" position={Position.Right} id="comfy_plan" className="clipSB_handle" style={handleStyle("comfy_plan")} />
    <NodeShell title="COMFY BRAIN" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧠</span>} className={`clipSB_nodeComfyBrain ${brainStateClass} ${plannerModeClass}`.trim()}>
      <div className="clipSB_comfyBrainPanel">
        <section className="clipSB_comfyBrainSection">
          <div className="clipSB_brainLabel">PLANNER</div>
          <div className="clipSB_comfyPlannerSwitch" role="tablist" aria-label="Planner mode switch">
            <button type="button" className={`clipSB_comfyPlannerSwitchBtn ${plannerMode === "legacy" ? "isActive" : ""}`.trim()} onClick={() => data?.onPlannerMode?.(id, "legacy")}>Current</button>
            <button type="button" className={`clipSB_comfyPlannerSwitchBtn ${plannerMode === "gemini_only" ? "isActive" : ""}`.trim()} onClick={() => data?.onPlannerMode?.(id, "gemini_only")}>Gemini</button>
          </div>
        </section>

        <section className="clipSB_comfyBrainSection clipSB_comfyBrainSectionMode">
          <div className="clipSB_brainLabel">MODE</div>
          <select className="clipSB_select clipSB_comfyModeSelect" value={visibleMode} onChange={(e) => data?.onMode?.(id, e.target.value)}>
            {!["clip", "kino"].includes(String(mode || "").toLowerCase()) ? <option value={mode} hidden>{String(mode || "clip")}</option> : null}
            <option value="clip">clip</option>
            <option value="kino">kino</option>
          </select>
        </section>

        <section className="clipSB_comfyBrainSection">
          <div className="clipSB_brainLabel">OUTPUT</div>
          <select className="clipSB_select" value={visibleOutput} onChange={(e) => data?.onOutput?.(id, e.target.value)}>
            {output !== "comfy image" ? <option value={output} hidden>{String(output || "comfy image")}</option> : null}
            <option value="comfy image">comfy image</option>
          </select>
        </section>

        <div className="clipSB_comfyBrainActions">
          <button className="clipSB_btn clipSB_comfyBrainParseBtn" onClick={() => data?.onParse?.(id)} disabled={isParsing}>{parseButtonLabel}</button>
        </div>
      </div>
    </NodeShell>
  </>);
}
