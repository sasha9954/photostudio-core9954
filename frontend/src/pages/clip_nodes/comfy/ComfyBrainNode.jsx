import React from "react";
import { Handle, Position, NodeShell, getModeDisplayMeta, getStyleDisplayMeta, handleStyle } from "./comfyNodeShared";

export default function ComfyBrainNode({ id, data }) {
  const mode = data?.mode || "clip";
  const output = data?.output || "comfy image";
  const styleKey = data?.styleKey || "realism";
  const modeMeta = getModeDisplayMeta(mode);
  const styleMeta = getStyleDisplayMeta(styleKey);
  const freezeStyle = !!data?.freezeStyle;
  const parseStatus = data?.parseStatus || "idle";
  const summary = data?.brainSummary || {};
  const warnings = Array.isArray(data?.brainWarnings) ? data.brainWarnings : [];
  const critical = Array.isArray(data?.brainCritical) ? data.brainCritical : [];

  return (<>
    {["audio","text","ref_character_1","ref_character_2","ref_character_3","ref_animal","ref_group","ref_location","ref_style","ref_props"].map((h, i) => (
      <Handle key={h} type="target" position={Position.Left} id={h} className="clipSB_handle" style={handleStyle(h === "ref_props" ? "ref_items" : h, { top: 36 + i * 18 })} />
    ))}
    <Handle type="source" position={Position.Right} id="comfy_plan" className="clipSB_handle" style={handleStyle("comfy_plan")} />
    <NodeShell title="COMFY BRAIN" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧠</span>} className="clipSB_nodeComfyBrain">
      <div className="clipSB_badge">CANON v1.2 • PLANNER</div>

      <div className="clipSB_assemblyStats" style={{ marginTop: 8 }}>
        <div className="clipSB_assemblyRow"><span>Mode</span><strong>{modeMeta.labelRu}</strong></div>
        <div className="clipSB_assemblyRow"><span>Output</span><strong>{output}</strong></div>
        <div className="clipSB_assemblyRow"><span>Style</span><strong>{styleMeta.labelRu}</strong></div>
        <div className="clipSB_assemblyRow"><span>Status</span><strong>{parseStatus}</strong></div>
      </div>

      <div className="clipSB_grid2" style={{ marginTop: 8 }}>
        <div><div className="clipSB_brainLabel">MODE</div><select className="clipSB_select" value={mode} onChange={(e) => data?.onMode?.(id, e.target.value)}><option value="clip">Клип</option><option value="kino">Кино</option><option value="reklama">Реклама</option><option value="scenario">Сценарий</option></select><div className="clipSB_selectHint">{modeMeta.descriptionRu}</div></div>
        <div><div className="clipSB_brainLabel">OUTPUT</div><select className="clipSB_select" value={output} onChange={(e) => data?.onOutput?.(id, e.target.value)}><option value="comfy image">comfy image</option><option value="comfy text">comfy text</option></select><div className="clipSB_selectHint">Формат результата для COMFY storyboard.</div></div>
      </div>

      <div style={{ marginTop: 8 }}><div className="clipSB_brainLabel">STYLE</div><select className="clipSB_select" value={styleKey} onChange={(e) => data?.onStyle?.(id, e.target.value)}><option value="realism">Реализм</option><option value="film">Кино-стиль</option><option value="neon">Неон</option><option value="glossy">Глянец</option><option value="soft">Мягкий</option></select><div className="clipSB_selectHint">{styleMeta.descriptionRu}</div></div>

      <div className="clipSB_brainSummaryBlock"><div className="clipSB_brainSummaryRow"><span>story source</span><strong>{summary.storySource || "none"}</strong></div><div className="clipSB_brainSummaryRow"><span>cast</span><strong>{summary.cast || "none connected"}</strong></div></div>
      <div className="clipSB_toggleRow"><label><input type="checkbox" checked={freezeStyle} onChange={(e) => data?.onFreezeStyle?.(id, e.target.checked)} /> freeze style</label></div>
      <div className="clipSB_brainWarnings">{critical.map((item) => <div key={`critical-${item}`} className="clipSB_brainPill clipSB_brainPillCritical">{item}</div>)}{warnings.map((item) => <div key={`warn-${item}`} className="clipSB_brainPill">{item}</div>)}{!critical.length && !warnings.length ? <div className="clipSB_brainPill clipSB_brainPillOk">Planner ready</div> : null}</div>

      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button className="clipSB_btn" onClick={() => data?.onParse?.(id)}>Разобрать</button>
        <button className="clipSB_btn clipSB_btnSecondary" disabled title="Расширенные действия планировщика будут подключены отдельно">Advanced soon</button>
      </div>
      <div className="clipSB_small">status: {parseStatus}{data?.parsedAt ? ` • ${data.parsedAt}` : ""}</div>
    </NodeShell>
  </>);
}
