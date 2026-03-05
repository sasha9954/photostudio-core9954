import React, { useRef, useState } from "react";
import { Handle, Position, useReactFlow } from "@xyflow/react";
import { API_BASE } from "../../services/api";


async function readDurationFromFile(file) {
  try {
    const url = URL.createObjectURL(file);
    const a = new Audio();
    a.preload = "metadata";
    a.src = url;
    const duration = await new Promise((resolve) => {
      const done = () => {
        const d = Number.isFinite(a.duration) ? a.duration : null;
        resolve(d);
      };
      a.addEventListener("loadedmetadata", done, { once: true });
      a.addEventListener("error", () => resolve(null), { once: true });
    });
    try { URL.revokeObjectURL(url); } catch {}
    return duration;
  } catch {
    return null;
  }
}

async function postJson(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data?.detail || data?.error || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

export default function AudioNode({ data, id }) {
  const { setNodes } = useReactFlow();
  const fileRef = useRef(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const audioUrl = data?.audioUrl || "";
  const audioName = data?.audioName || "";
  const audioKind = data?.audioKind || "auto"; // auto|speech|music|song

  const setNodeData = (patch) => {
    if (data?.setNodeData) return data.setNodeData(id, patch);
    setNodes((nds) =>
      nds.map((n) =>
        n.id === id ? { ...n, data: { ...(n.data || {}), ...(patch || {}) } } : n
      )
    );
  };

  const onPickFile = async (file) => {
    if (!file) return;
    setErr("");
    setBusy(true);
    try {
      const localDuration = await readDurationFromFile(file);

      const dataUrl = await new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onerror = () => reject(new Error("File read error"));
        fr.onload = () => resolve(fr.result);
        fr.readAsDataURL(file);
      });

      // IMPORTANT: store on backend (no blob in storage)
      const r = await postJson("/api/assets/fromDataUrl", { dataUrl });
      if (!r?.url) throw new Error("No url from server");

      setNodeData({
        audioUrl: r.url,
        audioName: file.name,
        audioMime: file.type || "",
        audioSize: file.size || 0,
        audioDurationSec: Number.isFinite(localDuration) ? localDuration : (data?.audioDurationSec || 0),
      });
    } catch (e) {
      setErr(e?.message || "Upload failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="clipNode">
      <div className="clipNodeHeader">
        <div className="clipNodeTitle">🎧 AUDIO</div>
      </div>

      <div className="clipNodeBody">
        <button
          className="clipBtn"
          disabled={busy}
          onClick={() => fileRef.current?.click()}
        >
          {busy ? "Загрузка…" : "Загрузить файл"}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="audio/*,.mp3,.wav,.ogg"
          style={{ display: "none" }}
          onChange={(e) => onPickFile(e.target.files?.[0])}
        />
        <div className="clipSub">mp3 / wav / ogg</div>

        <div style={{ marginTop: 10 }}>
          <select
            className="clipSelect"
            value={audioKind}
            onChange={(e) => setNodeData({ audioKind: e.target.value })}
            title="Тип входа"
          >
            <option value="auto">авто</option>
            <option value="speech">озвучка (речь)</option>
            <option value="music">музыка (фон)</option>
            <option value="song">песня (вокал+музыка)</option>
          </select>
        </div>

        {audioUrl ? (
          <div className="clipMini" style={{ marginTop: 10 }}>
            <div className="clipRow" style={{ justifyContent: "space-between" }}>
              <div className="clipMetaName" title={audioName || audioUrl}>
                {audioName || "audio"}
              </div>
              <a
                className="clipLink"
                href={audioUrl}
                target="_blank"
                rel="noreferrer"
              >
                открыть
              </a>
            </div>
            <audio
              controls
              src={audioUrl}
              preload="metadata"
              style={{ width: "100%", marginTop: 8 }}
              onLoadedMetadata={(e) => {
                const d = e?.currentTarget?.duration;
                if (Number.isFinite(d) && d > 0 && Math.abs((data?.audioDurationSec || 0) - d) > 0.5) {
                  setNodeData({ audioDurationSec: d });
                }
              }}
            />
            {Number.isFinite(data?.audioDurationSec) && data.audioDurationSec > 0 ? (
              <div className="clipSub" style={{ marginTop: 6 }}>
                длительность: {Math.floor(data.audioDurationSec / 60)}:{String(Math.round(data.audioDurationSec % 60)).padStart(2, "0")}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="clipSub" style={{ marginTop: 10 }}>
            нет аудио (можно работать только с текстом)
          </div>
        )}

        {err ? <div className="clipErr">{err}</div> : null}
      </div>

      <div className="clipNodeFooter">выход: AUDIO → BRAIN</div>

      <Handle
        type="source"
        position={Position.Right}
        id="audio"
        className="clipHandle"
      />
    </div>
  );
}
