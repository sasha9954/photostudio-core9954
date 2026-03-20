import React from "react";
import { Handle, Position, NodeShell, getModeDisplayMeta, getStyleDisplayMeta, handleStyle } from "./comfyNodeShared";

export default function ComfyStoryboardNode({ id, data }) {
  const scenes = Array.isArray(data?.mockScenes) ? data.mockScenes : [];
  const modeMeta = getModeDisplayMeta(data?.mode || "clip");
  const styleMeta = getStyleDisplayMeta(data?.stylePreset || "realism");
  const parseStatus = data?.parseStatus || (data?.isUpdating ? "updating" : (data?.isStale ? "stale" : "idle"));
  const readyImages = scenes.filter((scene) => !!scene?.imageUrl).length;
  const readyVideos = scenes.filter((scene) => !!scene?.videoUrl).length;
  const totalScenes = scenes.length || Number(data?.sceneCount || 0);
  const isUpdating = !!data?.isUpdating || parseStatus === "updating";
  const isBusy = !!data?.isBusy || !!data?.isGenerating || isUpdating;
  const hasScenes = totalScenes > 0;
  const canOpenEditor = !isBusy && hasScenes;
  const isReady = parseStatus === "ready";
  const isError = parseStatus === "error";
  const isStale = !isUpdating && (parseStatus === "stale" || (!!data?.isStale && !isReady));
  const warnings = Array.isArray(data?.warnings) ? data.warnings.filter(Boolean) : [];
  const weakSemanticWarning = warnings.find((item) => String(item || "").toLowerCase().includes("weak semantic context")) || "";
  const errorMessage = String(data?.errorMessage || "").trim();
  const statusLabel = isUpdating
    ? "обновляется..."
    : isError && isStale
      ? "ошибка • старый результат"
      : isStale
      ? (hasScenes ? "устарело" : "нужно обновить")
      : isReady
        ? (hasScenes ? `${totalScenes} сцен готово` : "Storyboard готов")
        : isError
          ? "ошибка"
          : "ожидание";
  const statusHint = isUpdating
    ? (hasScenes ? "Идёт новый parse. Текущие сцены сохранены как stale до прихода нового результата." : "Получаю сцены из COMFY BRAIN...")
    : isError && isStale
      ? (hasScenes ? "Новый parse завершился ошибкой. На экране остаётся прошлый storyboard, он помечен как stale. Editor откроет именно сохранённый старый результат." : "Новый parse завершился ошибкой, свежий storyboard не получен.")
    : isStale
      ? (hasScenes ? "Сцены сохранены, но входные данные изменились. Обновите storyboard." : "Данные изменились. Запустите обновление storyboard.")
      : isReady
        ? (hasScenes ? "Сцены готовы. Можно открыть editor." : "Сцены ещё не готовы для editor.")
        : isError
          ? "Storyboard не обновлён из-за ошибки parse."
          : "Ожидает parse из COMFY BRAIN.";
  const openEditorLabel = isError && isStale && hasScenes
    ? "Открыть stale editor"
    : isStale && hasScenes
      ? "Открыть stale editor"
      : "Открыть editor";
  const storyboardStateClass = isUpdating
    ? "clipSB_nodeComfyStoryboardStateUpdating"
    : isStale
      ? "clipSB_nodeComfyStoryboardStateStale"
      : isReady
        ? "clipSB_nodeComfyStoryboardStateReady"
        : isError
          ? "clipSB_nodeComfyStoryboardStateError"
          : "";

  return (<>
    <Handle type="target" position={Position.Left} id="comfy_plan" className="clipSB_handle" style={handleStyle("comfy_plan")} />
    <Handle type="source" position={Position.Right} id="comfy_scene_video_out" className="clipSB_handle" style={handleStyle("comfy_video", { top: 56 })} />
    <NodeShell title="COMFY STORYBOARD" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🧩</span>} className={`clipSB_nodeComfyStoryboard ${storyboardStateClass}`.trim()}>
      <div className="clipSB_assemblyStats" style={{ marginTop: 4 }}>
        <div className="clipSB_assemblyRow"><span>Сцен</span><strong>{totalScenes}</strong></div>
        <div className="clipSB_assemblyRow"><span>Фото</span><strong>{readyImages}/{totalScenes || 0}</strong></div>
        <div className="clipSB_assemblyRow"><span>Видео</span><strong>{readyVideos}/{totalScenes || 0}</strong></div>
        <div className="clipSB_assemblyRow"><span>Статус</span><strong>{statusLabel}</strong></div>
      </div>

      <div className="clipSB_small" style={{ marginTop: 8 }}>
        {modeMeta.labelRu} • {styleMeta.labelRu}
      </div>
      <div className="clipSB_selectHint" style={{ marginTop: 6 }}>{statusHint}</div>
      {errorMessage ? <div className="clipSB_small" style={{ marginTop: 6, color: "#ff8a8a" }}>Ошибка parse: {errorMessage}</div> : null}
      {isError && isStale && hasScenes ? <div className="clipSB_small" style={{ marginTop: 6, color: "#ffb86c" }}>⚠ Сохранён предыдущий storyboard. Это не свежий parse result.</div> : null}
      {weakSemanticWarning ? <div className="clipSB_small" style={{ marginTop: 6, color: "#ffb86c" }}>⚠ {weakSemanticWarning}</div> : null}
      <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
        <button className="clipSB_btn" onClick={() => data?.onOpenComfy?.(id)} disabled={!canOpenEditor} title={isError && isStale && hasScenes ? "Откроется сохранённый stale storyboard после failed parse" : undefined}>{openEditorLabel}</button>
      </div>
    </NodeShell>
  </>);
}
