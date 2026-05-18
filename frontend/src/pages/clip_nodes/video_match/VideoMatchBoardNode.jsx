import React, { useEffect, useMemo, useState } from "react";
import { Handle, Position } from "@xyflow/react";
import { useNavigate } from "react-router-dom";
import { NodeShell } from "../comfy/comfyNodeShared";
import {
  getDefaultVideoMatchBoardProject,
  persistVideoMatchBoardProject,
  readVideoMatchBoardProjectForNode,
} from "./videoMatchBoardDomain.js";
import "./VideoMatchBoardNode.css";

function formatDuration(value) {
  const sec = Number(value || 0);
  if (!Number.isFinite(sec) || sec <= 0) return "0.00 с";
  return `${sec.toFixed(2)} с`;
}

export default function VideoMatchBoardNode({ id, data }) {
  const navigate = useNavigate();
  const model = data || {};
  const [storedProject, setStoredProject] = useState(null);

  useEffect(() => {
    setStoredProject(readVideoMatchBoardProjectForNode(id));
  }, [id, model?.updatedAt, model?.videoMatchUpdatedAt]);

  const timingContext = model.timingContext || storedProject?.timingContext || {};
  const blocks = Array.isArray(model.videoBlocks) && model.videoBlocks.length
    ? model.videoBlocks
    : (Array.isArray(storedProject?.videoBlocks) ? storedProject.videoBlocks : []);
  const sourceVideo = model.sourceVideo || storedProject?.sourceVideo || {};
  const hasTiming = Boolean(timingContext?.sourceAudioUrl)
    || (Array.isArray(timingContext?.timingScenes) && timingContext.timingScenes.length > 0)
    || (Array.isArray(timingContext?.segments) && timingContext.segments.length > 0);

  const statusText = useMemo(() => {
    if (blocks.length) return `blocks: ${blocks.length}`;
    if (sourceVideo?.filename) return "video loaded";
    if (hasTiming) return "timing context ready";
    return "empty";
  }, [blocks.length, sourceVideo?.filename, hasTiming]);

  const onOpenBoard = () => {
    const project = persistVideoMatchBoardProject({
      ...getDefaultVideoMatchBoardProject(id),
      ...(storedProject || {}),
      nodeId: id,
      sourceNodeId: id,
      sourceVideo: sourceVideo || storedProject?.sourceVideo || { filename: "", duration_sec: 0 },
      sourceVideoUrl: model.sourceVideoUrl || storedProject?.sourceVideoUrl || "",
      timingContext,
      videoBlocks: blocks,
      selectedBlockId: model.selectedBlockId || storedProject?.selectedBlockId || blocks[0]?.id || "",
      jsonInput: model.jsonInput || storedProject?.jsonInput || "",
    });

    if (typeof model.onPatchNodeData === "function") {
      model.onPatchNodeData(id, {
        sourceVideo: project.sourceVideo,
        sourceVideoUrl: project.sourceVideoUrl,
        timingContext: project.timingContext,
        videoBlocks: project.videoBlocks,
        selectedBlockId: project.selectedBlockId,
        videoMatchUpdatedAt: Date.now(),
      });
    }

    navigate(`/studio/video-match-board?nodeId=${encodeURIComponent(id)}`, {
      state: { nodeId: id, project },
    });
  };

  return (
    <NodeShell title="Video Match Board" icon="🎯" className="videoMatchBoardNode" onClose={() => model.onRemoveNode?.(id)}>
      <Handle type="target" position={Position.Left} id="timing_in" />
      <div className="videoMatchBoardNode_body">
        <div className="videoMatchBoardNode_status">{statusText}</div>
        <div className="videoMatchBoardNode_meta">Audio: {timingContext?.sourceAudioUrl ? "есть" : "—"}</div>
        <div className="videoMatchBoardNode_meta">Audio duration: {formatDuration(timingContext?.audioDurationSec)}</div>
        <div className="videoMatchBoardNode_meta">Timing scenes: {Array.isArray(timingContext?.timingScenes) ? timingContext.timingScenes.length : 0}</div>
        <div className="videoMatchBoardNode_meta">Video: {sourceVideo?.filename || "—"}</div>
        <button className="clipSB_btn clipSB_btnPrimary" type="button" onClick={onOpenBoard}>Открыть Video Match Board</button>
      </div>
      <Handle type="source" position={Position.Right} id="video_match_board_out" />
    </NodeShell>
  );
}
