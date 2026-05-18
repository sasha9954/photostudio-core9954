import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  getDefaultVideoMatchBoardProject,
  getVideoMatchBoardEmergencyStorageKey,
  parseVideoMatchBoardJson,
  persistVideoMatchBoardProject,
  readVideoMatchBoardProjectForNode,
} from "../clip_nodes/video_match/videoMatchBoardDomain.js";
import "./VideoMatchBoardPage.css";

function formatSec(value) {
  const sec = Number(value || 0);
  if (!Number.isFinite(sec) || sec < 0) return "0.00";
  return sec.toFixed(2);
}

function getBlockLeft(block, duration) {
  const dur = Number(duration || 0);
  if (!Number.isFinite(dur) || dur <= 0) return 0;
  return Math.max(0, Math.min(100, (Number(block?.sourceVideoStartSec || 0) / dur) * 100));
}

function getBlockWidth(block, duration) {
  const dur = Number(duration || 0);
  if (!Number.isFinite(dur) || dur <= 0) return 0;
  const start = Number(block?.sourceVideoStartSec || 0);
  const end = Number(block?.sourceVideoEndSec || start);
  return Math.max(0.8, Math.min(100, ((end - start) / dur) * 100));
}

export default function VideoMatchBoardPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const nodeId = String(searchParams.get("nodeId") || location.state?.nodeId || "default").trim() || "default";
  const videoRef = useRef(null);
  const playStopRef = useRef(null);
  const objectUrlRef = useRef("");

  const initialProject = useMemo(() => {
    return location.state?.project || readVideoMatchBoardProjectForNode(nodeId) || getDefaultVideoMatchBoardProject(nodeId);
  }, [location.state?.project, nodeId]);

  const [project, setProject] = useState(initialProject);
  const [videoDurationSec, setVideoDurationSec] = useState(Number(initialProject?.sourceVideo?.duration_sec || 0));
  const [currentTimeSec, setCurrentTimeSec] = useState(0);

  const videoBlocks = Array.isArray(project.videoBlocks) ? project.videoBlocks : [];
  const selectedBlock = videoBlocks.find((block) => block.id === project.selectedBlockId) || null;
  const sourceVideoUrl = String(project.sourceVideoUrl || "");
  const timelineDuration = videoDurationSec || Number(project?.sourceVideo?.duration_sec || 0) || 0;

  const patchProject = (patch = {}, persistOptions = {}) => {
    setProject((prev) => {
      const next = persistVideoMatchBoardProject({ ...prev, ...(patch || {}), nodeId, sourceNodeId: nodeId }, persistOptions);
      return next;
    });
  };

  useEffect(() => {
    setProject(initialProject);
    setVideoDurationSec(Number(initialProject?.sourceVideo?.duration_sec || 0));
  }, [initialProject]);

  useEffect(() => {
    const onBeforeUnload = () => {
      try {
        localStorage.setItem(getVideoMatchBoardEmergencyStorageKey(nodeId), JSON.stringify({ ...project, updatedAt: Date.now() }));
      } catch {}
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => window.removeEventListener("beforeunload", onBeforeUnload);
  }, [nodeId, project]);

  useEffect(() => () => {
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
  }, []);

  const onVideoFileChange = (file) => {
    if (!file) return;
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    const url = URL.createObjectURL(file);
    objectUrlRef.current = url;
    patchProject({
      sourceVideoUrl: url,
      sourceVideo: {
        filename: file.name || "source.mp4",
        duration_sec: 0,
        type: file.type || "video/mp4",
        size: file.size || 0,
      },
    });
  };

  const onLoadedMetadata = () => {
    const duration = Number(videoRef.current?.duration || 0);
    if (!Number.isFinite(duration) || duration <= 0) return;
    setVideoDurationSec(duration);
    patchProject({
      sourceVideo: {
        ...(project.sourceVideo || {}),
        duration_sec: Number(duration.toFixed(3)),
      },
    }, { lastGood: false });
  };

  const onApplyJson = () => {
    const result = parseVideoMatchBoardJson(project.jsonInput, sourceVideoUrl);
    if (!result.ok) {
      patchProject({ jsonError: result.error }, { lastGood: false });
      return;
    }
    const nextSourceVideo = {
      ...(project.sourceVideo || {}),
      filename: result.sourceVideo?.filename || project.sourceVideo?.filename || "source.mp4",
      duration_sec: Number(result.sourceVideo?.duration_sec || project.sourceVideo?.duration_sec || videoDurationSec || 0),
    };
    patchProject({
      sourceVideo: nextSourceVideo,
      videoBlocks: result.videoBlocks,
      selectedBlockId: result.videoBlocks[0]?.id || "",
      jsonError: "",
    });
    if (Number(nextSourceVideo.duration_sec || 0) > 0) setVideoDurationSec(Number(nextSourceVideo.duration_sec));
  };

  const onPlaySelectedBlock = async () => {
    if (!selectedBlock || !videoRef.current) return;
    const start = Math.max(0, Number(selectedBlock.sourceVideoStartSec || 0));
    const end = Math.max(start, Number(selectedBlock.sourceVideoEndSec || start));
    playStopRef.current = end;
    videoRef.current.currentTime = start;
    try {
      await videoRef.current.play();
    } catch (error) {
      patchProject({ jsonError: `Не удалось запустить video player: ${String(error?.message || error)}` }, { lastGood: false });
    }
  };

  const onTimeUpdate = () => {
    const current = Number(videoRef.current?.currentTime || 0);
    setCurrentTimeSec(current);
    const stopAt = Number(playStopRef.current || 0);
    if (stopAt > 0 && current >= stopAt) {
      videoRef.current.pause();
      videoRef.current.currentTime = stopAt;
      playStopRef.current = null;
      setCurrentTimeSec(stopAt);
    }
  };

  const sampleJson = `{"schema":"video_match_board_v1","source_video":{"filename":"source.mp4","duration_sec":123.4},"matches":[{"id":"match_001","audio_scene_id":"seg_01","target_t0":0,"target_t1":4.8,"video_t0":120.4,"video_t1":125.2,"match_reason":"reason","confidence":0.86}]}`;

  return (
    <div className="videoMatchPage">
      <div className="videoMatchHeader">
        <div>
          <h1>Video Match Board</h1>
          <p>MVP-доска подбора фрагментов большого видео под audio/timing карту.</p>
        </div>
        <button className="btn" type="button" onClick={() => navigate("/studio/storyboard")}>Назад в граф</button>
      </div>

      <div className="videoMatchGrid">
        <section className="videoMatchPanel videoMatchPlayerPanel">
          <div className="videoMatchPanelHeader">
            <h2>Source video</h2>
            <label className="clipSB_btn clipSB_btnPrimary videoMatchUploadBtn">
              Загрузить видео
              <input type="file" accept="video/*" hidden onChange={(event) => onVideoFileChange(event.target.files?.[0])} />
            </label>
          </div>
          <div className="videoMatchVideoBox">
            {sourceVideoUrl ? (
              <video ref={videoRef} src={sourceVideoUrl} controls onLoadedMetadata={onLoadedMetadata} onTimeUpdate={onTimeUpdate} />
            ) : (
              <div className="videoMatchEmptyVideo">Загрузите видеофайл для preview.</div>
            )}
          </div>
          <div className="videoMatchTimelineMeta">
            <span>{project.sourceVideo?.filename || "source.mp4"}</span>
            <span>{formatSec(currentTimeSec)} / {formatSec(timelineDuration)} с</span>
          </div>
          <div className="videoMatchTimeline" aria-label="Video timeline">
            <div className="videoMatchTimelineProgress" style={{ width: `${timelineDuration > 0 ? Math.min(100, (currentTimeSec / timelineDuration) * 100) : 0}%` }} />
            {videoBlocks.map((block) => (
              <button
                key={block.id}
                type="button"
                className={`videoMatchTimelineBlock ${block.id === project.selectedBlockId ? "isSelected" : ""}`}
                style={{ left: `${getBlockLeft(block, timelineDuration)}%`, width: `${getBlockWidth(block, timelineDuration)}%` }}
                onClick={() => patchProject({ selectedBlockId: block.id }, { lastGood: false })}
                title={`${block.id}: ${formatSec(block.sourceVideoStartSec)}–${formatSec(block.sourceVideoEndSec)}с`}
              />
            ))}
          </div>
          <div className="videoMatchActions">
            <button className="clipSB_btn clipSB_btnPrimary" type="button" disabled={!selectedBlock || !sourceVideoUrl} onClick={onPlaySelectedBlock}>Play selected block</button>
            <span>{selectedBlock ? `${selectedBlock.id}: ${formatSec(selectedBlock.sourceVideoStartSec)} → ${formatSec(selectedBlock.sourceVideoEndSec)}` : "Block не выбран"}</span>
          </div>
        </section>

        <section className="videoMatchPanel">
          <h2>Audio timing context</h2>
          <div className="videoMatchContextRows">
            <div>sourceAudioUrl: {project.timingContext?.sourceAudioUrl || "—"}</div>
            <div>audioDurationSec: {formatSec(project.timingContext?.audioDurationSec)}</div>
            <div>timingScenes: {Array.isArray(project.timingContext?.timingScenes) ? project.timingContext.timingScenes.length : 0}</div>
            <div>segments: {Array.isArray(project.timingContext?.segments) ? project.timingContext.segments.length : 0}</div>
            <div>podcastEditManifest: {project.timingContext?.podcastEditManifest ? "есть" : "—"}</div>
            <div>composerEditManifest: {project.timingContext?.composerEditManifest ? "есть" : "—"}</div>
          </div>
        </section>

        <section className="videoMatchPanel videoMatchJsonPanel">
          <div className="videoMatchPanelHeader">
            <h2>Codex JSON</h2>
            <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => patchProject({ jsonInput: sampleJson }, { lastGood: false })}>Вставить пример</button>
          </div>
          <textarea value={project.jsonInput || ""} onChange={(event) => patchProject({ jsonInput: event.target.value, jsonError: "" }, { lastGood: false })} placeholder="Вставьте JSON schema video_match_board_v1..." />
          <button className="clipSB_btn clipSB_btnPrimary" type="button" onClick={onApplyJson}>Применить JSON</button>
          {project.jsonError ? <div className="videoMatchError">{project.jsonError}</div> : null}
        </section>

        <section className="videoMatchPanel videoMatchBlocksPanel">
          <h2>Video blocks</h2>
          {videoBlocks.length === 0 ? <div className="videoMatchEmptyList">После применения JSON здесь появятся video blocks.</div> : null}
          <div className="videoMatchBlocksList">
            {videoBlocks.map((block) => (
              <button key={block.id} type="button" className={`videoMatchBlockCard ${block.id === project.selectedBlockId ? "isSelected" : ""}`} onClick={() => patchProject({ selectedBlockId: block.id }, { lastGood: false })}>
                <b>{block.id}</b>
                <span>audio: {block.audioSceneId || "—"} · target {formatSec(block.targetStartSec)}–{formatSec(block.targetEndSec)}</span>
                <span>video {formatSec(block.sourceVideoStartSec)}–{formatSec(block.sourceVideoEndSec)} · confidence {block.confidence ?? "—"}</span>
                {block.matchReason ? <small>{block.matchReason}</small> : null}
              </button>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}
