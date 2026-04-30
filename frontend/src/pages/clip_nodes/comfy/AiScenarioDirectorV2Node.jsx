import React, { useMemo, useState } from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";

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

const STAGES = ["PLAN", "AUDIO", "CORE", "ROLES", "SCENES", "PROMPTS", "FINAL VIDEO PROMPT", "FINAL"];

const viewerText = {
  PLAN: "PLAN: clip cards summary",
  AUDIO: "Audio segmentation will appear here",
  CORE: "Story spine will appear here",
  ROLES: "Role assignment table will appear here",
  SCENES: "Scene plan will appear here",
  PROMPTS: "Photo/video prompts will appear here",
  "FINAL VIDEO PROMPT": "LTX-ready prompts will appear here",
  FINAL: "Render manifest will appear here",
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
  const [stage, setStage] = useState("PLAN");
  const statuses = useMemo(() => data?.stageStatuses || {}, [data?.stageStatuses]);

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
          <button className="clipSB_btn" type="button">Analyze inputs</button>
          <button className="clipSB_btn" type="button">Build plan</button>
          <button className="clipSB_btn" type="button">Build contract</button>
          <button className="clipSB_btn" type="button">Run next stage</button>
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
        <div className="asdv2_panel"><strong>Pipeline rail</strong><div className="asdv2_rail">{STAGES.map((name) => <button key={name} type="button" className={`asdv2_stage ${stage === name ? "isActive" : ""}`} onClick={() => setStage(name)}><b>{name}</b><span>{String(statuses?.[name]?.status || "idle")}</span><small>{String(statuses?.[name]?.summary || "Summary placeholder")}</small></button>)}</div><div className="asdv2_viewer">{viewerText[stage]}</div></div>
      </NodeShell>
    </>
  );
}
