import React from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";

const CLIP_TRACE_SCENARIO_COUNTERS = false;

function isSceneImageReady(scene = {}, runtime = {}) {
  const imageStrategy = String(scene?.imageStrategy || "").trim().toLowerCase() || "single";
  const hasImageUrl = !!String(scene?.imageUrl || "").trim();
  const hasStartFrameImageUrl = !!String(scene?.startFrameImageUrl || scene?.startFramePreviewUrl || scene?.startImageUrl || "").trim();
  const hasEndFrameImageUrl = !!String(scene?.endFrameImageUrl || scene?.endFramePreviewUrl || scene?.endImageUrl || "").trim();
  const imageStatus = String(runtime?.imageStatus || scene?.imageStatus || "").trim().toLowerCase();
  const startFrameStatus = String(runtime?.startFrameStatus || scene?.startFrameStatus || "").trim().toLowerCase();
  const endFrameStatus = String(runtime?.endFrameStatus || scene?.endFrameStatus || "").trim().toLowerCase();
  if (imageStrategy === "first_last") return (hasStartFrameImageUrl && hasEndFrameImageUrl) || (startFrameStatus === "done" && endFrameStatus === "done");
  if (imageStrategy === "continuation") return hasStartFrameImageUrl || hasImageUrl || startFrameStatus === "done" || imageStatus === "done";
  return hasImageUrl || imageStatus === "done";
}

function isSceneVideoReady(scene = {}, runtime = {}) {
  const hasVideoUrl = !!String(scene?.videoUrl || "").trim();
  const videoStatus = String(runtime?.videoStatus || scene?.videoStatus || "").trim().toLowerCase();
  return hasVideoUrl || videoStatus === "done";
}

export default function ScenarioStoryboardNode({ id, data }) {
  const scenes = Array.isArray(data?.scenes) ? data.scenes : [];
  const totalScenes = scenes.length;
  const generationMap = data?.sceneGeneration && typeof data.sceneGeneration === "object" ? data.sceneGeneration : {};
  const generatedImages = scenes.filter((scene, idx) => {
    const key = String(scene?.sceneId || `S${idx + 1}`);
    const runtime = generationMap[key] && typeof generationMap[key] === "object" ? generationMap[key] : {};
    const imageReady = isSceneImageReady(scene, runtime);
    const videoReady = isSceneVideoReady(scene, runtime);
    if (CLIP_TRACE_SCENARIO_COUNTERS) {
      console.debug("[SCENARIO COUNTERS]", {
        sceneId: key,
        imageReady,
        videoReady,
        imageStrategy: String(scene?.imageStrategy || "").trim().toLowerCase() || "single",
        hasImageUrl: !!String(scene?.imageUrl || "").trim(),
        hasStartFrameImageUrl: !!String(scene?.startFrameImageUrl || scene?.startFramePreviewUrl || scene?.startImageUrl || "").trim(),
        hasEndFrameImageUrl: !!String(scene?.endFrameImageUrl || scene?.endFramePreviewUrl || scene?.endImageUrl || "").trim(),
        hasVideoUrl: !!String(scene?.videoUrl || "").trim(),
        imageStatus: String(runtime?.imageStatus || scene?.imageStatus || "").trim(),
        startFrameStatus: String(runtime?.startFrameStatus || scene?.startFrameStatus || "").trim(),
        endFrameStatus: String(runtime?.endFrameStatus || scene?.endFrameStatus || "").trim(),
        videoStatus: String(runtime?.videoStatus || scene?.videoStatus || "").trim(),
      });
    }
    return imageReady;
  }).length;
  const generatedVideos = scenes.filter((scene, idx) => {
    const key = String(scene?.sceneId || `S${idx + 1}`);
    const runtime = generationMap[key] && typeof generationMap[key] === "object" ? generationMap[key] : {};
    return isSceneVideoReady(scene, runtime);
  }).length;
  const status = totalScenes > 0 ? "ready" : "idle";

  return (
    <>
      <Handle type="target" position={Position.Left} id="scenario_storyboard_in" className="clipSB_handle" style={handleStyle("scenario_storyboard_in")} />
      <Handle type="source" position={Position.Right} id="scenario_storyboard_out" className="clipSB_handle" style={handleStyle("scenario_storyboard_out")} />
      <NodeShell title="SCENARIO STORYBOARD" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎞️</span>} className="clipSB_nodeStoryboard">
        <div className="clipSB_assemblyStats" style={{ marginTop: 4 }}>
          <div className="clipSB_assemblyRow"><span>Сцен</span><strong>{totalScenes}</strong></div>
          <div className="clipSB_assemblyRow"><span>Фото</span><strong>{generatedImages}/{totalScenes || 0}</strong></div>
          <div className="clipSB_assemblyRow"><span>Видео</span><strong>{generatedVideos}/{totalScenes || 0}</strong></div>
          <div className="clipSB_assemblyRow"><span>Статус</span><strong>{status}</strong></div>
        </div>

        <div className="clipSB_selectHint" style={{ marginTop: 8 }}>Сцены готовы. Можно открыть editor.</div>
        <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
          <button className="clipSB_btn" onClick={() => data?.onOpenScenarioStoryboard?.(id)} disabled={totalScenes === 0} type="button">Открыть editor</button>
        </div>
      </NodeShell>
    </>
  );
}
