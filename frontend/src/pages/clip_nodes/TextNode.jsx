import React from "react";
import { Handle, Position, useReactFlow } from "@xyflow/react";

export default function TextNode({ data, id }) {
  const { setNodes } = useReactFlow();
  const value = data?.text || "";
  const kind = data?.textKind || "story";
  const setData = (patch) => {
    // Preferred: parent handler (if provided)
    if (data?.onChange) return data.onChange(id, patch);
    // Fallback: update via ReactFlow state (so UI + persist react)
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id ? { ...n, data: { ...(n.data || {}), ...(patch || {}) } } : n
      )
    );
  };

  const clearText = () => setData({ text: "" });

  return (
    <div className="clipSB_node">
      <div className="clipSB_nodeHeader">
        <div className="clipSB_nodeIcon">📝</div>
        <div className="clipSB_nodeTitle">TEXT</div>
      </div>

      <div className="clipSB_nodeBody">
        <div
          className="clipSB_textWrap"
          style={{
            position: "relative",
            border: "1px solid rgba(0,0,0,0.9)",
            borderRadius: 16,
            padding: 12,
            background: "rgba(0,0,0,0.22)",
            boxShadow:
              "inset 0 0 0 1px rgba(255,255,255,0.04), 0 0 12px rgba(0,0,0,0.35)",
          }}
        >
          <textarea
            className="clipSB_textarea"
            value={value}
            onChange={(e) => setData({ text: e.target.value })}
            placeholder="Текст для истории / слова песни / идея сюжета…"
            rows={6}
            style={{
              width: "100%",
              border: "none",
              outline: "none",
              background: "transparent",
              color: "inherit",
              resize: "vertical",
              borderRadius: 12,
              paddingRight: 44,
            }}
          />
          {!!value && (
            <button className="clipSB_clear" onClick={clearText} title="Очистить">
              ↺
            </button>
          )}
        </div>

        <div className="clipSB_hint" style={{ marginTop: 10 }}>
          вход: текст → BRAIN (дальше можно ElevenLabs)
        </div>
      </div>

      <div className="clipSB_nodeFooter">
        <div className="clipSB_hint">выход: TEXT → BRAIN</div>
      </div>

      <Handle type="source" position={Position.Right} id="text" className="clipSB_handle" />
    </div>
  );
}
