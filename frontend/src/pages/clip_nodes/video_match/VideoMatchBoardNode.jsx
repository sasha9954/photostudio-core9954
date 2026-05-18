import React, { useCallback, useEffect, useMemo, useState } from "react";
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

function hasSourceVideoMetadata(sourceVideo = {}) {
  if (!sourceVideo || typeof sourceVideo !== "object") return false;
  return Boolean(String(sourceVideo.filename || "").trim())
    || Number(sourceVideo.duration_sec || sourceVideo.durationSec || 0) > 0
    || Number(sourceVideo.size || 0) > 0;
}

export default function VideoMatchBoardNode({ id, data }) {
  const navigate = useNavigate();
  const model = data || {};
  const [storedProject, setStoredProject] = useState(null);

  const refreshStoredProject = useCallback(() => {
    setStoredProject(readVideoMatchBoardProjectForNode(id));
  }, [id]);

  useEffect(() => {
    refreshStoredProject();
  }, [refreshStoredProject, model?.updatedAt, model?.videoMatchUpdatedAt]);

  useEffect(() => {
    const onVisibilityChange = () => {
      if (!document.hidden) refreshStoredProject();
    };
    window.addEventListener("focus", refreshStoredProject);
    window.addEventListener("pageshow", refreshStoredProject);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      window.removeEventListener("focus", refreshStoredProject);
      window.removeEventListener("pageshow", refreshStoredProject);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [refreshStoredProject]);

  const timingContext = model.timingContext || storedProject?.timingContext || {};
  const savedBlocks = Array.isArray(storedProject?.videoBlocks) ? storedProject.videoBlocks : [];
  const modelBlocks = Array.isArray(model.videoBlocks) ? model.videoBlocks : [];
  const blocks = savedBlocks.length ? savedBlocks : modelBlocks;
  const sourceVideo = hasSourceVideoMetadata(storedProject?.sourceVideo)
    ? storedProject.sourceVideo
    : (hasSourceVideoMetadata(model.sourceVideo) ? model.sourceVideo : {});
  const sourceVideoUrl = Object.prototype.hasOwnProperty.call(storedProject || {}, "sourceVideoUrl")
    ? String(storedProject?.sourceVideoUrl || "")
    : String(model.sourceVideoUrl || "");
  const selectedBlockId = storedProject?.selectedBlockId || model.selectedBlockId || blocks[0]?.id || "";
  const jsonInput = Object.prototype.hasOwnProperty.call(storedProject || {}, "jsonInput")
    ? String(storedProject?.jsonInput || "")
    : String(model.jsonInput || "");
  const jsonError = Object.prototype.hasOwnProperty.call(storedProject || {}, "jsonError")
    ? String(storedProject?.jsonError || "")
    : String(model.jsonError || "");
  const hasTiming = Boolean(timingContext?.sourceAudioUrl)
    || (Array.isArray(timingContext?.timingScenes) && timingContext.timingScenes.length > 0)
    || (Array.isArray(timingContext?.segments) && timingContext.segments.length > 0);

  const statusText = useMemo(() => {
    if (blocks.length) return `blocks: ${blocks.length}`;
    if (sourceVideo?.filename) return "video loaded";
    if (jsonInput) return "json ready";
    if (hasTiming) return "timing context ready";
    return "empty";
  }, [blocks.length, sourceVideo?.filename, jsonInput, hasTiming]);

  const onOpenBoard = () => {
    const project = persistVideoMatchBoardProject({
      ...getDefaultVideoMatchBoardProject(id),
      ...(storedProject || {}),
      nodeId: id,
      sourceNodeId: id,
      sourceVideo: sourceVideo || { filename: "", duration_sec: 0 },
      sourceVideoUrl,
      timingContext,
      videoBlocks: blocks,
      selectedBlockId,
      jsonInput,
      jsonError,
    });

    if (typeof model.onPatchNodeData === "function") {
      model.onPatchNodeData(id, {
        sourceVideo: project.sourceVideo,
        sourceVideoUrl: project.sourceVideoUrl,
        timingContext: project.timingContext,
        videoBlocks: project.videoBlocks,
        selectedBlockId: project.selectedBlockId,
        jsonInput: project.jsonInput,
        jsonError: project.jsonError,
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
