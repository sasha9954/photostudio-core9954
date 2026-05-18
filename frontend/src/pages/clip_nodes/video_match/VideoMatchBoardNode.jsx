import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Handle, Position } from "@xyflow/react";
import { useNavigate } from "react-router-dom";
import { NodeShell } from "../comfy/comfyNodeShared";
import {
  getDefaultVideoMatchBoardProject,
  getVideoMatchProjectStats,
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

function hasTimingContextMaterials(timingContext = {}) {
  if (!timingContext || typeof timingContext !== "object") return false;
  return Boolean(String(timingContext.sourceAudioUrl || "").trim())
    || Number(timingContext.audioDurationSec || 0) > 0
    || (Array.isArray(timingContext.timingScenes) && timingContext.timingScenes.length > 0)
    || (Array.isArray(timingContext.segments) && timingContext.segments.length > 0)
    || Boolean(timingContext.podcastEditManifest || timingContext.composerEditManifest);
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

  const modelTimingContext = hasTimingContextMaterials(model.timingContext) ? model.timingContext : null;
  const savedTimingContext = hasTimingContextMaterials(storedProject?.timingContext) ? storedProject.timingContext : null;
  const timingContext = modelTimingContext || savedTimingContext || {};
  const savedSegments = Array.isArray(storedProject?.matchSegments) ? storedProject.matchSegments : [];
  const modelSegments = Array.isArray(model.matchSegments) ? model.matchSegments : [];
  const matchSegments = savedSegments.length ? savedSegments : modelSegments;
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
  const selectedSegmentId = storedProject?.selectedSegmentId || model.selectedSegmentId || matchSegments[0]?.id || "";
  const selectedCandidateId = storedProject?.selectedCandidateId || model.selectedCandidateId || matchSegments[0]?.selectedCandidateId || blocks[0]?.candidateId || "";
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
    if (matchSegments.length) return `segments: ${matchSegments.length} · blocks: ${blocks.length}`;
    if (blocks.length) return `blocks: ${blocks.length}`;
    if (sourceVideo?.filename) return "video loaded";
    if (jsonInput) return "json ready";
    if (hasTiming) return "timing context ready";
    return "empty";
  }, [matchSegments.length, blocks.length, sourceVideo?.filename, jsonInput, hasTiming]);

  const onOpenBoard = () => {
    const savedProject = readVideoMatchBoardProjectForNode(id);
    const savedStats = getVideoMatchProjectStats(savedProject);
    const hasSavedMaterials = savedStats.materialScore > 0;
    const nextTimingContext = hasTimingContextMaterials(model.timingContext)
      ? model.timingContext
      : (hasTimingContextMaterials(savedProject?.timingContext) ? savedProject.timingContext : timingContext);
    const baseProject = {
      ...getDefaultVideoMatchBoardProject(id),
      ...(hasSavedMaterials ? (savedProject || {}) : {}),
      nodeId: id,
      sourceNodeId: id,
      timingContext: nextTimingContext,
      updatedAt: Date.now(),
    };
    const project = persistVideoMatchBoardProject({
      ...baseProject,
      sourceVideo: hasSavedMaterials ? (savedProject?.sourceVideo || sourceVideo || { filename: "", duration_sec: 0 }) : (sourceVideo || { filename: "", duration_sec: 0 }),
      sourceVideoUrl: hasSavedMaterials ? String(savedProject?.sourceVideoUrl || "") : sourceVideoUrl,
      matchSegments: hasSavedMaterials ? (Array.isArray(savedProject?.matchSegments) ? savedProject.matchSegments : []) : matchSegments,
      videoBlocks: hasSavedMaterials ? (Array.isArray(savedProject?.videoBlocks) ? savedProject.videoBlocks : []) : blocks,
      selectedSegmentId: hasSavedMaterials ? (savedProject?.selectedSegmentId || "") : selectedSegmentId,
      selectedCandidateId: hasSavedMaterials ? (savedProject?.selectedCandidateId || "") : selectedCandidateId,
      selectedBlockId: hasSavedMaterials ? (savedProject?.selectedBlockId || "") : selectedBlockId,
      jsonInput: hasSavedMaterials ? String(savedProject?.jsonInput || "") : jsonInput,
      jsonError: hasSavedMaterials ? String(savedProject?.jsonError || "") : jsonError,
    });

    if (typeof model.onPatchNodeData === "function") {
      model.onPatchNodeData(id, {
        sourceVideo: project.sourceVideo,
        sourceVideoUrl: project.sourceVideoUrl,
        timingContext: project.timingContext,
        selectedSegmentId: project.selectedSegmentId,
        selectedCandidateId: project.selectedCandidateId,
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
        <div className="videoMatchBoardNode_meta">Segments: {matchSegments.length || "—"}</div>
        <div className="videoMatchBoardNode_meta">Blocks: {blocks.length || "—"}</div>
        <div className="videoMatchBoardNode_meta">Video: {sourceVideo?.filename || "—"}</div>
        <div className="videoMatchBoardNode_meta">Audio timing: {hasTiming ? "ready" : "—"}</div>
        <button className="clipSB_btn clipSB_btnPrimary" type="button" onClick={onOpenBoard}>Открыть Video Match Board</button>
      </div>
      <Handle type="source" position={Position.Right} id="video_match_board_out" />
    </NodeShell>
  );
}
