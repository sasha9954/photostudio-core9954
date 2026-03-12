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

  return (<>
    {["audio","text","ref_character_1","ref_character_2","ref_character_3","ref_animal","ref_group","ref_location","ref_style","ref_props"].map((h, i) => (
      <Handle key={h} type="target" position={Position.Left} id={h} className="clipSB_handle" style={handleStyle(h === "ref_props" ? "ref_items" : h, { top: 36 + i * 18 })} />
    ))}
    <Handle type="source" position={Position.Right} id="comfy_plan" className="clipSB_handle" style={handleStyle("comfy_plan")} />
    <NodeShell title="COMFY BRAIN" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧠</span>} className="clipSB_nodeComfyBrain">
      <div className="clipSB_grid2" style={{ marginTop: 6 }}>
        <div><div className="clipSB_brainLabel">MODE</div><select className="clipSB_select" value={mode} onChange={(e) => data?.onMode?.(id, e.target.value)}><option value="clip">Клип</option><option value="kino">Кино</option><option value="reklama">Реклама</option><option value="scenario">Сценарий</option></select><div className="clipSB_selectHint">{modeMeta.descriptionRu}</div></div>
        <div><div className="clipSB_brainLabel">OUTPUT</div><select className="clipSB_select" value={output} onChange={(e) => data?.onOutput?.(id, e.target.value)}><option value="comfy image">comfy image</option><option value="comfy text">comfy text</option></select><div className="clipSB_selectHint">Формат результата storyboard.</div></div>
      </div>

      <div style={{ marginTop: 8 }}><div className="clipSB_brainLabel">STYLE</div><select className="clipSB_select" value={styleKey} onChange={(e) => data?.onStyle?.(id, e.target.value)}><option value="realism">Реализм</option><option value="film">Кино-стиль</option><option value="neon">Неон</option><option value="glossy">Глянец</option><option value="soft">Мягкий</option></select><div className="clipSB_selectHint">{styleMeta.descriptionRu}</div></div>

      <div className="clipSB_toggleRow"><label><input type="checkbox" checked={freezeStyle} onChange={(e) => data?.onFreezeStyle?.(id, e.target.checked)} /> freeze style</label></div>

      <div className="clipSB_small" style={{ marginTop: 8 }}>status: {parseStatus}{data?.parsedAt ? ` • ${data.parsedAt}` : ""}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button className="clipSB_btn" onClick={() => data?.onParse?.(id)}>Разобрать</button>
      </div>
    </NodeShell>
  </>);
}
