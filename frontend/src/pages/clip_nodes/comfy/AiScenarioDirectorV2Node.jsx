import React, { useMemo, useState } from "react";
import { Handle, Position } from "@xyflow/react";
import { NodeShell, handleStyle } from "./comfyNodeShared";

const INPUTS = [
  { id: "audio_in", label: "Audio", tone: "audio", placeholder: "No audio connected" },
  { id: "ref_character_1", label: "Character 1", tone: "character", placeholder: "Lead character ref" },
  { id: "ref_character_2", label: "Character 2", tone: "character", placeholder: "Second role ref" },
  { id: "ref_character_3", label: "Character 3", tone: "character", placeholder: "Third role ref" },
  { id: "ref_location", label: "Location", tone: "location", placeholder: "Scene location ref" },
  { id: "ref_style", label: "Style", tone: "style", placeholder: "Visual style mood" },
  { id: "video_ref_in", label: "Video ref", tone: "video", placeholder: "Reference video" },
  { id: "ref_props", label: "Props", tone: "props", placeholder: "Objects / props" },
  { id: "text_in", label: "Text idea", tone: "text", placeholder: "Idea or synopsis" },
];

const PLAN = [
  ["1", "IA2V", "Настоящее", "...", "Мужик поёт в купе поезда."],
  ["2", "I2V", "Воспоминание", "...", "Молодой герой дерётся во дворе."],
  ["3", "IA2V", "Настоящее", "...", "Мужик поёт в тамбуре с сигаретой в руке."],
  ["4", "I2V", "Воспоминание", "...", "Молодой герой ворует и убегает."],
  ["5", "IA2V", "Настоящее", "...", "Мужик в вагоне-ресторане за столом с едой."],
  ["6", "I2V", "Воспоминание", "...", "Молодой герой дарит цветы девушке."],
  ["7", "IA2V", "Финал", "...", "Мужик поёт в том же купе, поезд приближается к Одессе."],
];

const STAGES = [
  { key: "plan", label: "PLAN" },
  { key: "audio", label: "AUDIO" },
  { key: "core", label: "CORE" },
  { key: "roles", label: "ROLES" },
  { key: "scenes", label: "SCENES" },
  { key: "prompts", label: "PROMPTS" },
  { key: "final_video_prompt", label: "FINAL VIDEO PROMPT" },
  { key: "final", label: "FINAL" },
];

const viewerText = {
  plan: "PLAN: clip cards summary",
  audio: "Audio segmentation will appear here",
  core: "Story spine will appear here",
  roles: "Role assignment table will appear here",
  scenes: "Scene plan will appear here",
  prompts: "Photo/video prompts will appear here",
  final_video_prompt: "LTX-ready prompts will appear here",
  final: "Render manifest will appear here",
};

const toneToColor = {
  audio: "var(--family-audio)",
  character: "var(--family-ref-character)",
  location: "var(--family-ref-location)",
  style: "var(--family-ref-style)",
  video: "var(--family-video-ref)",
  props: "var(--family-ref-items)",
  text: "var(--family-text)",
};

export default function AiScenarioDirectorV2Node({ id, data }) {
  const [stage, setStage] = useState("plan");
  const statuses = useMemo(() => data?.stageStatuses || {}, [data?.stageStatuses]);
  const handleUiStubAction = (action) => {
    console.log("[AI SCENARIO DIRECTOR V2] action stub", { nodeId: id, action });
  };

  return (
    <>
      {INPUTS.map((item, index) => (
        <Handle key={item.id} type="target" position={Position.Left} id={item.id} className="clipSB_handle" style={{ ...handleStyle(item.id), top: 48 + index * 24 }} />
      ))}
      <Handle type="source" position={Position.Right} id="scenario_out_v2" className="clipSB_handle" style={handleStyle("scenario_out")} />
      <NodeShell title="AI SCENARIO" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎬</span>} className="clipSB_nodeStoryboard" style={{ minWidth: 980 }}>
        <div className="asdv2_sub">Director V2 / Clip planning</div>
        <div className="asdv2_row">
          <select className="asdv2_select" defaultValue="Clip"><option>Clip</option><option>Story</option><option>Advertisement</option><option>Kino</option><option>Test</option></select>
          <select className="asdv2_select" defaultValue="9:16"><option>9:16</option><option>16:9</option><option>1:1</option></select>
          <span className="asdv2_chip">Draft</span>
          <button className="clipSB_btn" type="button" title="UI stub: backend integration pending" onClick={() => handleUiStubAction("analyze_inputs")}>Analyze inputs</button>
          <button className="clipSB_btn" type="button" title="UI stub: backend integration pending" onClick={() => handleUiStubAction("build_plan")}>Build plan</button>
          <button className="clipSB_btn" type="button" title="UI stub: backend integration pending" onClick={() => handleUiStubAction("build_contract")}>Build contract</button>
          <button className="clipSB_btn" type="button" title="UI stub: backend integration pending" onClick={() => handleUiStubAction("run_next_stage")}>Run next stage</button>
        </div>
        <div className="asdv2_strip">
          {INPUTS.map((item) => {
            const connected = Boolean(data?.connections?.[item.id]);
            return <div key={item.id} className="asdv2_input" style={{ borderColor: toneToColor[item.tone] || "rgba(255,255,255,0.2)" }}><b>{item.label}</b><span>{connected ? "connected" : "empty"}</span><small>{item.placeholder}</small></div>;
          })}
        </div>
        <div className="asdv2_grid">
          <div className="asdv2_panel"><strong>AI chat</strong><p>Опиши клип, который хочешь получить.</p><input className="asdv2_inputLine" placeholder="Например: мужик поёт в купе, между сценами воспоминания молодости..." readOnly /><div className="asdv2_row"><button className="clipSB_btn" type="button">Больше сюжета</button><button className="clipSB_btn" type="button">Больше lip-sync</button><button className="clipSB_btn" type="button">50/50</button><button className="clipSB_btn" type="button">Без first_last</button></div></div>
          <div className="asdv2_panel"><div className="asdv2_row" style={{ justifyContent: "space-between" }}><strong>Director Contract preview</strong><button className="clipSB_btn" type="button">JSON</button></div><div className="asdv2_contract">{["Story intent", "Roles", "Worlds", "Routes", "Required scenes", "Ref usage", "Montage policy"].map((name) => <div key={name} className="asdv2_contractCard"><b>{name}</b><small>Draft contract section placeholder.</small></div>)}</div></div>
        </div>
        <div className="asdv2_panel"><strong>Clip Assembly Plan</strong><div className="asdv2_plan">{PLAN.map(([idx, route, timeline, phrase, text]) => <div key={idx} className="asdv2_scene"><div className="asdv2_row"><b>#{idx}</b><span className="asdv2_tag">{route}</span><span className="asdv2_tag">{timeline}</span><small>edit / lock / move</small></div><small>Фраза: "{phrase}"</small><p>{text}</p></div>)}</div></div>
        <div className="asdv2_panel"><strong>Pipeline rail</strong><div className="asdv2_rail">{STAGES.map((item) => { const info = statuses?.[item.key] || statuses?.[item.label] || {}; return <button key={item.key} type="button" className={`asdv2_stage ${stage === item.key ? "isActive" : ""}`} onClick={() => setStage(item.key)}><b>{item.label}</b><span>{String(info.status || "idle")}</span><small>{String(info.summary || "Summary placeholder")}</small></button>; })}</div><div className="asdv2_viewer">{viewerText[stage] || ""}</div></div>
      </NodeShell>
    </>
  );
}
