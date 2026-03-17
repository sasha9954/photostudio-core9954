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
  const audioStoryMode = data?.audioStoryMode || "lyrics_music";
  const isParsing = parseStatus === "parsing";
  const isReady = parseStatus === "ready";
  const isError = parseStatus === "error";
  const statusLabel = isParsing
    ? "генерация..."
    : isReady
      ? "готово"
      : isError
        ? "ошибка"
        : "ожидание";
  const parseButtonLabel = isParsing ? "Разбираю..." : "Разобрать";
  const connectedRefsSummary = Array.isArray(data?.connectedRefsSummary) ? data.connectedRefsSummary : [];
  const connectedRefsWarnings = Array.isArray(data?.connectedRefsWarnings) ? data.connectedRefsWarnings : [];
  const parseHint = isParsing
    ? "Идёт анализ аудио и построение сцен. Повторный запуск временно заблокирован."
    : isReady
      ? "Storyboard собран. Можно открыть editor и продолжать работу."
      : isError
        ? "Не удалось завершить разбор. Проверь входы и попробуй снова."
        : "Готов к разбору storyboard.";
  const brainStateClass = isParsing
    ? "clipSB_nodeComfyBrainStateParsing"
    : isReady
      ? "clipSB_nodeComfyBrainStateReady"
      : isError
        ? "clipSB_nodeComfyBrainStateError"
        : "";

  return (<>
    {["audio","text","ref_character_1","ref_character_2","ref_character_3","ref_animal","ref_group","ref_location","ref_style","ref_props"].map((h, i) => (
      <Handle key={h} type="target" position={Position.Left} id={h} className="clipSB_handle" style={handleStyle(h === "ref_props" ? "ref_items" : h, { top: 36 + i * 18 })} />
    ))}
    <Handle type="source" position={Position.Right} id="comfy_plan" className="clipSB_handle" style={handleStyle("comfy_plan")} />
    <NodeShell title="COMFY BRAIN" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧠</span>} className={`clipSB_nodeComfyBrain ${brainStateClass}`.trim()}>
      <div className="clipSB_grid2" style={{ marginTop: 6 }}>
        <div><div className="clipSB_brainLabel">MODE</div><select className="clipSB_select" value={mode} onChange={(e) => data?.onMode?.(id, e.target.value)}><option value="clip">Клип</option><option value="kino">Кино</option><option value="reklama">Реклама</option><option value="scenario">Сценарий</option></select><div className="clipSB_selectHint">{`${modeMeta.descriptionRu} • MODE управляет драматургическим каркасом.`}</div></div>
        <div><div className="clipSB_brainLabel">OUTPUT</div><select className="clipSB_select" value={output} onChange={(e) => data?.onOutput?.(id, e.target.value)}><option value="comfy image">comfy image</option><option value="comfy text">comfy text</option></select><div className="clipSB_selectHint">Формат результата storyboard.</div></div>
      </div>

      <div style={{ marginTop: 8 }}><div className="clipSB_brainLabel">STYLE</div><select className="clipSB_select" value={styleKey} onChange={(e) => data?.onStyle?.(id, e.target.value)}><option value="realism">Реализм</option><option value="film">Кино-стиль</option><option value="neon">Неон</option><option value="glossy">Глянец</option><option value="soft">Мягкий</option></select><div className="clipSB_selectHint">{`${styleMeta.descriptionRu} • STYLE влияет на визуал и не переписывает сюжет MODE.`}</div></div>


      <div style={{ marginTop: 8 }}><div className="clipSB_brainLabel">AUDIO STORY MODE</div><select className="clipSB_select" value={audioStoryMode} onChange={(e) => data?.onAudioStoryMode?.(id, e.target.value)}><option value="lyrics_music">lyrics + music</option><option value="music_only">music only</option><option value="music_plus_text">music + text</option></select><div className="clipSB_selectHint">lyrics+music: lyrics + music jointly drive story; music_only: lyrics игнорируются, строим progression по ритму/энергии; music_plus_text: lyrics игнорируются, TEXT задаёт смысл.</div></div>

      <div className="clipSB_toggleRow"><label><input type="checkbox" checked={freezeStyle} onChange={(e) => data?.onFreezeStyle?.(id, e.target.checked)} /> freeze style</label></div>

      <div className="clipSB_small" style={{ marginTop: 8 }}>status: {statusLabel}{data?.parsedAt ? ` • ${data.parsedAt}` : ""}</div>
      <div className="clipSB_selectHint" style={{ marginTop: 6 }}>{parseHint}</div>
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button className="clipSB_btn" onClick={() => data?.onParse?.(id)} disabled={isParsing}>{parseButtonLabel}</button>
      </div>

      <div style={{ marginTop: 8 }}>
        <div className="clipSB_brainLabel">Подключённые рефы</div>
        {connectedRefsSummary.length ? (
          connectedRefsSummary.map((item, idx) => (
            <div key={`${item?.role || "role"}-${idx}`} className="clipSB_small">{String(item?.role || "ref")} — {String(item?.label || "персонаж")}</div>
          ))
        ) : (
          <div className="clipSB_small">Рефы не подключены</div>
        )}
        {connectedRefsWarnings.length ? (
          <div className="clipSB_refWarningsBlock">
            <div className="clipSB_brainLabel">Есть незавершённые рефы</div>
            {connectedRefsWarnings.map((item, idx) => (
              <div key={`${item?.role || "warn"}-${idx}`} className="clipSB_small">{String(item?.role || "ref")} — {String(item?.message || "добавьте реф")}</div>
            ))}
          </div>
        ) : null}
      </div>
    </NodeShell>
  </>);
}
