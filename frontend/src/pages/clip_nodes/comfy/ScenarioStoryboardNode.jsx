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

function resolveScenarioModeBadge(modeValue = "") {
  const raw = String(modeValue || "").trim().toLowerCase();
  const normalized = raw === "music_video" ? "clip" : raw === "advertisement" ? "ad" : raw;
  if (normalized === "clip" || normalized === "music_video") {
    return { resolvedMode: "clip", displayLabel: "Клип", color: "#14b8a6", background: "rgba(20,184,166,0.18)" };
  }
  if (normalized === "story") {
    return { resolvedMode: "story", displayLabel: "История", color: "#3b82f6", background: "rgba(59,130,246,0.18)" };
  }
  if (normalized === "music") {
    return { resolvedMode: "music", displayLabel: "Музыка", color: "#a855f7", background: "rgba(168,85,247,0.2)" };
  }
  if (normalized === "ad") {
    return { resolvedMode: "ad", displayLabel: "Реклама", color: "#f59e0b", background: "rgba(245,158,11,0.2)" };
  }
  return {
    resolvedMode: normalized || "unknown",
    displayLabel: String(modeValue || "").trim() || "Неизвестно",
    color: "#94a3b8",
    background: "rgba(148,163,184,0.2)",
  };
}

export default function ScenarioStoryboardNode({ id, data }) {
  const storyboardRevision = String(data?.storyboardRevision || "");
  const scenes = Array.isArray(data?.scenes) ? data.scenes : [];
  const generationMap = data?.sceneGeneration && typeof data.sceneGeneration === "object" ? data.sceneGeneration : {};
  const audioData = data?.audioData && typeof data.audioData === "object" ? data.audioData : {};
  const totalScenes = scenes.length;
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
  const hasGenerationInProgress = scenes.some((scene, idx) => {
    const key = String(scene?.sceneId || `S${idx + 1}`);
    const runtime = generationMap[key] && typeof generationMap[key] === "object" ? generationMap[key] : {};
    return ["generating", "queued", "pending"].includes(String(runtime?.status || runtime?.imageStatus || runtime?.videoStatus || "").trim().toLowerCase());
  });
  const scenarioModeRaw = data?.scenarioMode || data?.contentType || "";
  const modeBadge = resolveScenarioModeBadge(scenarioModeRaw);
  const status = String(data?.status || "").trim().toLowerCase() || (totalScenes === 0 ? "idle" : (hasGenerationInProgress ? "generating" : "ready"));

  React.useEffect(() => {
    console.debug("[SCENARIO MODE BADGE]", {
      nodeId: String(id || ""),
      resolvedMode: modeBadge.resolvedMode,
      displayLabel: modeBadge.displayLabel,
    });
  }, [id, modeBadge.displayLabel, modeBadge.resolvedMode]);

  if (CLIP_TRACE_SCENARIO_COUNTERS) {
    console.debug("[SCENARIO STORYBOARD CARD SUMMARY]", {
      nodeId: String(id || ""),
      storyboardRevision,
      sceneIds: scenes.map((scene, idx) => String(scene?.sceneId || `S${idx + 1}`)),
      summary: {
        sceneCount: totalScenes,
        photoCount: generatedImages,
        videoCount: generatedVideos,
        status,
      },
      audioDataSnapshot: {
        hasAudioUrl: !!String(audioData?.audioUrl || "").trim(),
        musicStatus: String(audioData?.musicStatus || "idle"),
      },
    });
  }

  return (
    <>
      <Handle type="target" position={Position.Left} id="scenario_storyboard_in" className="clipSB_handle" style={handleStyle("scenario_storyboard_in")} />
      <Handle type="source" position={Position.Right} id="scenario_storyboard_out" className="clipSB_handle" style={handleStyle("scenario_storyboard_out")} />
      <NodeShell title="SCENARIO STORYBOARD" onClose={() => data?.onRemoveNode?.(id)} icon={<span aria-hidden>🎞️</span>} className="clipSB_nodeStoryboard">
        <div className="clipSB_assemblyStats" style={{ marginTop: 4 }}>
          <div className="clipSB_assemblyRow">
            <span>Режим</span>
            <strong
              style={{
                color: modeBadge.color,
                background: modeBadge.background,
                border: `1px solid ${modeBadge.color}`,
                borderRadius: 999,
                padding: "2px 8px",
                fontWeight: 700,
              }}
            >
              {modeBadge.displayLabel}
            </strong>
          </div>
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
