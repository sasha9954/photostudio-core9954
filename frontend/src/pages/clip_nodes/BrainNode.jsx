import React, { useEffect, useMemo, useState } from "react";
import { Handle, Position, useReactFlow } from "@xyflow/react";
import { API_BASE } from "../../services/api";

const MODES = [
  { key: "lyrics", label: "по смыслу песни" },
  { key: "new_story", label: "своя история под песню" },
  { key: "generic_plot", label: "любой сюжет под бит" },
  { key: "bg_music", label: "фоновая музыка (без текста)" }
];

export default function BrainNode({ id, data }) {
  const { setNodes } = useReactFlow();
  const [mode, setMode] = useState(data?.mode || "lyrics");
  const [text, setText] = useState(data?.text || "");

  const setNodeData = (patch) => {
    if (data?.setNodeData) return data.setNodeData(id, patch);
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id ? { ...n, data: { ...(n.data || {}), ...(patch || {}) } } : n
      )
    );
  };

  useEffect(() => setMode(data?.mode || "lyrics"), [data?.mode]);
  useEffect(() => setText(data?.text || ""), [data?.text]);

  const canRun = useMemo(() => {
    const hasText = (text || "").trim().length > 0;
    // режим bg_music может работать и без текста
    return mode === "bg_music" ? true : hasText || !!data?.audioUrl;
  }, [mode, text, data?.audioUrl]);

  const onMode = (v) => {
    setMode(v);
    setNodeData({ mode: v });
  };

  const onText = (v) => {
    setText(v);
    setNodeData({ text: v });
  };

  const run = async () => {
    if (!canRun) return;
    setNodeData({ status: "loading", error: "" });
    try {
      const r = await fetch(`${API_BASE}/api/clip/brain`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({
          mode,
          text: text || "",
          audioUrl: data?.audioUrl || null,
          audioKind: data?.audioKind || null,
          audioDurationSec: data?.audioDurationSec || null,
          textKind: data?.textKind || null
        })
      });
      const j = await r.json();
      if (!r.ok || !j?.ok) {
        throw new Error(j?.detail || j?.error || "brain failed");
      }
      setNodeData({ status: "done", scenes: j.scenes || [] });
    } catch (e) {
      setNodeData({ status: "error", error: String(e?.message || e) });
    }
  };

  const scenesCount = (data?.scenes || []).length;

  return (
    <div className="clipNode clipNodeBrain">
      <div className="clipNodeTitle">BRAIN</div>

      <div className="clipNodeRow">
        <select
          className="clipSelect"
          value={mode}
          onChange={(e) => onMode(e.target.value)}
        >
          {MODES.map((m) => (
            <option key={m.key} value={m.key}>
              {m.label}
            </option>
          ))}
        </select>
      </div>

      <textarea
        className="clipTextarea"
        placeholder="Вставь текст (слова/сюжет/идея). Можно пусто если фоновая музыка."
        value={text}
        onChange={(e) => onText(e.target.value)}
        rows={4}
      />

      <button className="clipBtn" onClick={run} disabled={!canRun || data?.status === "loading"}>
        {data?.status === "loading" ? "Думаю..." : "Разобрать"}
      </button>

      <div className="clipNodeHint">
        {data?.status === "done" ? `✅ сцен: ${scenesCount}` : "ждёт AUDIO и/или текст"}
      </div>

      {data?.error ? <div className="clipErr">{data.error}</div> : null}
      <Handle type="target" position={Position.Left} id="text" style={{ top: 90, background: "#f5c542", borderColor: "rgba(0,0,0,0.6)" }} />
      <Handle type="target" position={Position.Left} id="audio" style={{ top: 130, background: "#ff5b5b", borderColor: "rgba(0,0,0,0.6)" }} />
      <Handle type="target" position={Position.Left} id="ref" style={{ top: 170, background: "#37d67a", borderColor: "rgba(0,0,0,0.6)" }} />
      <Handle type="source" position={Position.Right} id="plan" style={{ top: 130, background: "#35c7ff", borderColor: "rgba(0,0,0,0.6)" }} />
</div>
  );
}
