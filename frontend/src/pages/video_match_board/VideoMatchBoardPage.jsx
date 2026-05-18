import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import {
  getDefaultVideoMatchBoardProject,
  getVideoMatchBoardEmergencyStorageKey,
  getVideoMatchProjectStats,
  buildVideoBlocksFromMatchSegments,
  parseVideoMatchBoardJson,
  persistVideoMatchBoardProject,
  readVideoMatchBoardProjectForNode,
  shouldSkipVideoMatchPersistToProtectMaterials,
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

function getValidDurationSec(value) {
  const duration = Number(value || 0);
  return Number.isFinite(duration) && duration > 0 ? duration : 0;
}

export default function VideoMatchBoardPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const nodeId = String(searchParams.get("nodeId") || location.state?.nodeId || "default").trim() || "default";
  const videoRef = useRef(null);
  const playbackRef = useRef(null);
  const objectUrlRef = useRef("");
  const segmentRefs = useRef({});

  const initialProject = useMemo(() => {
    const stateProject = location.state?.project || null;
    const savedProject = readVideoMatchBoardProjectForNode(nodeId);
    const hasStateProject = Boolean(stateProject);
    const stateStats = getVideoMatchProjectStats(stateProject);
    const savedStats = getVideoMatchProjectStats(savedProject);
    let picked = "default";
    let nextProject = getDefaultVideoMatchBoardProject(nodeId);

    if (savedProject && (!stateProject || shouldSkipVideoMatchPersistToProtectMaterials(stateProject, savedProject))) {
      picked = "saved";
      nextProject = savedProject;
    } else if (stateProject) {
      picked = "state";
      nextProject = stateProject;
    }

    console.info("[VIDEO MATCH PAGE HYDRATE]", {
      nodeId,
      hasStateProject,
      stateStats,
      savedStats,
      picked,
    });
    return nextProject;
  }, [location.state?.project, nodeId]);

  const [project, setProject] = useState(initialProject);
  const [videoDurationSec, setVideoDurationSec] = useState(Number(initialProject?.sourceVideo?.duration_sec || 0));
  const [currentTimeSec, setCurrentTimeSec] = useState(0);
  const [sourceVideoLoadMessage, setSourceVideoLoadMessage] = useState("");
  const [expandedSegmentId, setExpandedSegmentId] = useState(String(initialProject?.selectedSegmentId || ""));
  const [previewCandidateId, setPreviewCandidateId] = useState("");
  const [isAssemblyPlaying, setIsAssemblyPlaying] = useState(false);

  const matchSegments = Array.isArray(project.matchSegments) ? project.matchSegments : [];
  const videoBlocks = Array.isArray(project.videoBlocks) ? project.videoBlocks : [];
  const assemblyBlocks = useMemo(() => [...videoBlocks].sort((a, b) => Number(a?.targetStartSec || 0) - Number(b?.targetStartSec || 0)), [videoBlocks]);
  const selectedBlock = videoBlocks.find((block) => block.id === project.selectedBlockId) || null;
  const sourceVideoUrl = String(project.sourceVideoUrl || "");
  const timelineDuration = videoDurationSec || Number(project?.sourceVideo?.duration_sec || 0) || 0;
  const assemblyDurationSec = Math.max(0, ...assemblyBlocks.map((block) => Number(block?.targetEndSec || 0)));
  const candidatesTotal = matchSegments.reduce((total, segment) => total + (Array.isArray(segment?.candidates) ? segment.candidates.length : 0), 0);
  const effectiveExpandedSegmentId = expandedSegmentId || project.selectedSegmentId;

  const patchProject = (patch = {}, persistOptions = {}) => {
    setProject((prev) => {
      const next = persistVideoMatchBoardProject({ ...prev, ...(patch || {}), nodeId, sourceNodeId: nodeId }, persistOptions);
      return next;
    });
  };

  useEffect(() => {
    setProject(initialProject);
    setVideoDurationSec(Number(initialProject?.sourceVideo?.duration_sec || 0));
    setSourceVideoLoadMessage("");
    setExpandedSegmentId(String(initialProject?.selectedSegmentId || ""));
    setPreviewCandidateId("");
    setIsAssemblyPlaying(false);
    playbackRef.current = null;
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

  useEffect(() => {
    if (!String(initialProject?.sourceVideoUrl || "").startsWith("blob:")) return;
    setSourceVideoLoadMessage("Загрузите source video заново");
    patchProject({ sourceVideoUrl: "" }, { lastGood: false });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialProject?.sourceVideoUrl]);

  const onVideoFileChange = (file) => {
    if (!file) return;
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    const url = URL.createObjectURL(file);
    objectUrlRef.current = url;
    setVideoDurationSec(0);
    setSourceVideoLoadMessage("");
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
    setSourceVideoLoadMessage("");
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

  const onSourceVideoError = () => {
    if (!sourceVideoUrl.startsWith("blob:")) return;
    setSourceVideoLoadMessage("Загрузите source video заново");
    patchProject({ sourceVideoUrl: "" }, { lastGood: false });
  };

  const onApplyJson = () => {
    const result = parseVideoMatchBoardJson(project.jsonInput, sourceVideoUrl);
    if (!result.ok) {
      patchProject({ jsonError: result.error }, { lastGood: false });
      return;
    }

    const videoElementDurationSec = getValidDurationSec(videoRef.current?.duration);
    const stateVideoDurationSec = getValidDurationSec(videoDurationSec);
    const loadedVideoDurationSec = videoElementDurationSec || (sourceVideoUrl ? stateVideoDurationSec : 0);
    const jsonDurationSec = getValidDurationSec(result.sourceVideo?.duration_sec);
    const nextDurationSec = loadedVideoDurationSec || jsonDurationSec;
    const currentFilename = String(project.sourceVideo?.filename || "").trim();
    const maxBlockEndSec = Math.max(0, ...result.videoBlocks.map((block) => Number(block.sourceVideoEndSec || 0)));
    const durationWarning = loadedVideoDurationSec > 0 && maxBlockEndSec > loadedVideoDurationSec
      ? `Warning: JSON содержит video_t1/sourceVideoEndSec ${formatSec(maxBlockEndSec)} с, это больше реальной длительности загруженного видео ${formatSec(loadedVideoDurationSec)} с.`
      : "";

    const nextSourceVideo = {
      ...(project.sourceVideo || {}),
      filename: currentFilename || result.sourceVideo?.filename || "source.mp4",
      duration_sec: Number(nextDurationSec.toFixed(3)),
    };
    patchProject({
      schema: result.schema,
      sourceVideo: nextSourceVideo,
      matchSegments: result.matchSegments,
      videoBlocks: result.videoBlocks,
      selectedSegmentId: result.selectedSegmentId,
      selectedCandidateId: result.selectedCandidateId,
      selectedBlockId: result.videoBlocks[0]?.id || "",
      jsonError: durationWarning,
    });
    setExpandedSegmentId(result.selectedSegmentId);
    if (!loadedVideoDurationSec && jsonDurationSec > 0) setVideoDurationSec(jsonDurationSec);
  };

  const onPlaySelectedBlock = async () => {
    if (!selectedBlock) return;
    if (!sourceVideoUrl || !videoRef.current) {
      showMissingSourceVideoMessage();
      return;
    }
    const start = Math.max(0, Number(selectedBlock.sourceVideoStartSec || 0));
    const end = Math.max(start, Number(selectedBlock.sourceVideoEndSec || start));
    playbackRef.current = { end, blocks: [], index: 0 };
    videoRef.current.currentTime = start;
    try {
      await videoRef.current.play();
    } catch (error) {
      patchProject({ jsonError: `Не удалось запустить video player: ${String(error?.message || error)}` }, { lastGood: false });
    }
  };

  const stopPlayback = () => {
    playbackRef.current = null;
    setIsAssemblyPlaying(false);
    if (videoRef.current) videoRef.current.pause();
  };

  const showMissingSourceVideoMessage = () => {
    setSourceVideoLoadMessage("Загрузите source video заново");
    patchProject({ jsonError: "Загрузите source video заново" }, { lastGood: false });
  };

  const playSourceRange = async (start = 0, end = 0) => {
    if (!sourceVideoUrl || !videoRef.current) {
      showMissingSourceVideoMessage();
      return false;
    }
    videoRef.current.currentTime = Math.max(0, Number(start || 0));
    try {
      await videoRef.current.play();
      return true;
    } catch (error) {
      patchProject({ jsonError: `Не удалось запустить video player: ${String(error?.message || error)}` }, { lastGood: false });
      return false;
    }
  };

  const onTimeUpdate = () => {
    const current = Number(videoRef.current?.currentTime || 0);
    setCurrentTimeSec(current);
    const playback = playbackRef.current;
    const stopAt = Number(playback?.end || 0);
    if (stopAt > 0 && current >= stopAt) {
      videoRef.current.pause();
      videoRef.current.currentTime = stopAt;
      setCurrentTimeSec(stopAt);
      const nextIndex = Number(playback?.index || 0) + 1;
      const nextBlock = Array.isArray(playback?.blocks) ? playback.blocks[nextIndex] : null;
      if (nextBlock) {
        playbackRef.current = { ...playback, index: nextIndex, end: Math.max(Number(nextBlock.sourceVideoStartSec || 0), Number(nextBlock.sourceVideoEndSec || 0)) };
        patchProject(getSelectionPatchForBlock(nextBlock), { lastGood: false });
        setExpandedSegmentId(nextBlock.segmentId || nextBlock.audioSceneId || "");
        setTimeout(() => scrollSegmentIntoView(nextBlock.segmentId || nextBlock.audioSceneId || ""), 0);
        void playSourceRange(nextBlock.sourceVideoStartSec, nextBlock.sourceVideoEndSec);
        return;
      }
      playbackRef.current = null;
      setIsAssemblyPlaying(false);
    }
  };

  const getSelectionPatchForBlock = (block = {}) => ({
    selectedBlockId: block?.id || "",
    selectedSegmentId: block?.segmentId || block?.audioSceneId || "",
    selectedCandidateId: block?.candidateId || block?.id || "",
  });

  const scrollSegmentIntoView = (segmentId = "") => {
    const node = segmentRefs.current[String(segmentId || "")];
    if (node) node.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  const onSelectBlock = (block = {}) => {
    const segmentId = block?.segmentId || block?.audioSceneId || "";
    setExpandedSegmentId(segmentId);
    patchProject(getSelectionPatchForBlock(block), { lastGood: false });
    setTimeout(() => scrollSegmentIntoView(segmentId), 0);
  };

  const onPreviewCandidate = async (segment = {}, candidate = {}) => {
    setPreviewCandidateId(candidate.id || "");
    setExpandedSegmentId(segment.id || "");
    const start = Math.max(0, Number(candidate.sourceVideoStartSec || 0));
    const end = Math.max(start, Number(candidate.sourceVideoEndSec || start));
    playbackRef.current = { end, blocks: [], index: 0 };
    setIsAssemblyPlaying(false);
    await playSourceRange(start, end);
  };

  const playAssemblyFromBlock = async (startBlock = null) => {
    if (!assemblyBlocks.length) return;
    if (!sourceVideoUrl || !videoRef.current) {
      showMissingSourceVideoMessage();
      return;
    }
    const startIndex = Math.max(0, assemblyBlocks.findIndex((block) => block.id === startBlock?.id));
    const firstBlock = assemblyBlocks[startIndex] || assemblyBlocks[0];
    playbackRef.current = {
      end: Math.max(Number(firstBlock.sourceVideoStartSec || 0), Number(firstBlock.sourceVideoEndSec || 0)),
      blocks: assemblyBlocks,
      index: startIndex,
    };
    setIsAssemblyPlaying(true);
    onSelectBlock(firstBlock);
    const didPlay = await playSourceRange(firstBlock.sourceVideoStartSec, firstBlock.sourceVideoEndSec);
    if (!didPlay) setIsAssemblyPlaying(false);
  };

  const onSelectCandidate = (segmentId = "", candidateId = "") => {
    const nextSegments = matchSegments.map((segment) => {
      if (segment.id !== segmentId) return segment;
      return {
        ...segment,
        selectedCandidateId: candidateId,
        selected_candidate_id: candidateId,
      };
    });
    const nextBlocks = buildVideoBlocksFromMatchSegments(nextSegments, sourceVideoUrl);
    const nextBlock = nextBlocks.find((block) => block.segmentId === segmentId && (block.candidateId === candidateId || block.id === candidateId)) || nextBlocks[0] || null;
    patchProject({
      matchSegments: nextSegments,
      videoBlocks: nextBlocks,
      selectedSegmentId: segmentId,
      selectedCandidateId: candidateId,
      selectedBlockId: nextBlock?.id || "",
    });
    setExpandedSegmentId(segmentId);
    setTimeout(() => scrollSegmentIntoView(segmentId), 0);
  };

  const sampleDurationSec = Math.max(getValidDurationSec(videoDurationSec), 130);
  const sampleJson = JSON.stringify({
    schema: "video_match_board_v2",
    source_video: { filename: "source.mp4", duration_sec: Number(sampleDurationSec.toFixed(3)) },
    segments: [
      {
        audio_scene_id: "seg_01",
        story_scene_id: "story_01",
        target_t0: 0,
        target_t1: 4.8,
        text: "Opening phrase",
        mood: "curious",
        visual_need: "intro establishing shot",
        selected_candidate_id: "seg_01_a",
        candidates: [
          {
            id: "seg_01_a",
            video_t0: 12.4,
            video_t1: 17.2,
            fit_mode: "exact",
            confidence: 0.86,
            match_reason: "Wide intro shot matches the opening mood.",
            visual_type: "intro",
            shot_type: "wide",
            emotion: "calm",
            action: "location reveal",
            contains_face: false,
            mouth_visible: false,
            lip_sync_candidate: false,
            dialogue_present: false,
            motion_level: "low",
            camera_motion: "slow_pan",
            thumbnail: "",
            warnings: [],
          },
          {
            id: "seg_01_b",
            video_t0: 38.0,
            video_t1: 42.8,
            fit_mode: "trim",
            confidence: 0.74,
            match_reason: "Alternate establishing shot with more movement.",
            visual_type: "intro",
            shot_type: "medium",
            emotion: "neutral",
            action: "subject enters frame",
            contains_face: true,
            mouth_visible: false,
            lip_sync_candidate: false,
            dialogue_present: false,
            motion_level: "medium",
            camera_motion: "handheld",
            warnings: ["More motion than requested"],
          },
        ],
      },
      {
        audio_scene_id: "seg_02",
        story_scene_id: "story_02",
        target_t0: 4.8,
        target_t1: 9.6,
        text: "Second beat",
        mood: "focused",
        visual_need: "detail or reaction shot",
        selected_candidate_id: "seg_02_a",
        candidates: [
          {
            id: "seg_02_a",
            video_t0: 64.2,
            video_t1: 69.0,
            fit_mode: "exact",
            confidence: 0.82,
            match_reason: "Close detail supports the focused narration.",
            visual_type: "detail",
            shot_type: "close_up",
            emotion: "focused",
            action: "hands work on object",
            contains_face: false,
            mouth_visible: false,
            lip_sync_candidate: false,
            dialogue_present: false,
            motion_level: "low",
            camera_motion: "static",
            warnings: [],
          },
          {
            id: "seg_02_b",
            video_t0: 92.5,
            video_t1: 97.3,
            fit_mode: "trim",
            confidence: 0.77,
            match_reason: "Reaction shot can bridge into the next line.",
            visual_type: "reaction",
            shot_type: "close_up",
            emotion: "thoughtful",
            action: "person looks off camera",
            contains_face: true,
            mouth_visible: true,
            lip_sync_candidate: false,
            dialogue_present: false,
            motion_level: "low",
            camera_motion: "static",
            thumbnail: "",
            warnings: ["Mouth is visible; avoid if narration feels lip-synced"],
          },
        ],
      },
    ],
  }, null, 2);

  return (
    <div className="videoMatchPage">
      <div className="videoMatchHeader">
        <div>
          <h1>Video Match Board</h1>
          <p>MVP-доска подбора фрагментов большого видео под audio/timing карту.</p>
        </div>
        <button className="btn" type="button" onClick={() => navigate(-1)}>Назад в граф</button>
      </div>

      <div className="videoMatchSummaryBar">
        <span>segments: {matchSegments.length}</span>
        <span>selected blocks: {videoBlocks.length}</span>
        <span>candidates total: {candidatesTotal}</span>
        <span>assembly duration: {formatSec(assemblyDurationSec)} sec</span>
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
              <video ref={videoRef} src={sourceVideoUrl} controls onLoadedMetadata={onLoadedMetadata} onError={onSourceVideoError} onTimeUpdate={onTimeUpdate} />
            ) : (
              <div className="videoMatchEmptyVideo">Загрузите видеофайл для preview.</div>
            )}
          </div>
          {sourceVideoLoadMessage ? <div className="videoMatchError">{sourceVideoLoadMessage}</div> : null}
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
                onClick={() => onSelectBlock(block)}
                title={`${block.id}: ${formatSec(block.sourceVideoStartSec)}–${formatSec(block.sourceVideoEndSec)}с`}
              />
            ))}
          </div>
          <div className="videoMatchActions">
            <button className="clipSB_btn clipSB_btnPrimary" type="button" disabled={!selectedBlock} onClick={onPlaySelectedBlock}>Play selected block</button>
            <span>{selectedBlock ? `${selectedBlock.id}: ${formatSec(selectedBlock.sourceVideoStartSec)} → ${formatSec(selectedBlock.sourceVideoEndSec)}` : "Block не выбран"}</span>
          </div>
        </section>

        <section className="videoMatchPanel videoMatchAssemblyPanel">
          <div className="videoMatchPanelHeader">
            <h2>Assembly preview</h2>
            <div className="videoMatchAssemblyControls">
              <button className="clipSB_btn clipSB_btnPrimary" type="button" disabled={!assemblyBlocks.length} onClick={() => playAssemblyFromBlock(assemblyBlocks[0])}>Play assembly preview</button>
              <button className="clipSB_btn clipSB_btnSecondary" type="button" disabled={!selectedBlock || !assemblyBlocks.length} onClick={() => playAssemblyFromBlock(selectedBlock)}>Play from selected scene</button>
              <button className="clipSB_btn clipSB_btnSecondary" type="button" disabled={!isAssemblyPlaying} onClick={stopPlayback}>Stop</button>
            </div>
          </div>
          {assemblyBlocks.length === 0 ? <div className="videoMatchEmptyList">Selected candidates появятся здесь как будущая сборка.</div> : null}
          <div className="videoMatchAssemblyTimeline" aria-label="Assembly preview timeline">
            {assemblyBlocks.map((block) => {
              const left = assemblyDurationSec > 0 ? Math.max(0, Math.min(100, (Number(block.targetStartSec || 0) / assemblyDurationSec) * 100)) : 0;
              const width = assemblyDurationSec > 0 ? Math.max(1.2, Math.min(100, ((Number(block.targetEndSec || 0) - Number(block.targetStartSec || 0)) / assemblyDurationSec) * 100)) : 0;
              return (
                <button
                  key={block.id}
                  type="button"
                  className={`videoMatchAssemblyBlock ${block.id === project.selectedBlockId ? "isSelected" : ""}`}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  onClick={() => onSelectBlock(block)}
                  title={`${block.audioSceneId || block.segmentId}: target ${formatSec(block.targetStartSec)}–${formatSec(block.targetEndSec)}с · candidate ${block.candidateId || block.id}`}
                >
                  <span>{block.audioSceneId || block.segmentId}</span>
                  <small>{block.candidateId || block.id}</small>
                </button>
              );
            })}
          </div>
          <div className="videoMatchAssemblyLegend">
            <span>{selectedBlock ? `selected: ${selectedBlock.audioSceneId || selectedBlock.segmentId} · ${selectedBlock.candidateId || selectedBlock.id}` : "selected: —"}</span>
            <span>target duration {formatSec(assemblyDurationSec)} sec</span>
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
          <textarea value={project.jsonInput || ""} onChange={(event) => patchProject({ jsonInput: event.target.value, jsonError: "" }, { lastGood: false })} placeholder="Вставьте JSON schema video_match_board_v1 или video_match_board_v2..." />
          <button className="clipSB_btn clipSB_btnPrimary" type="button" onClick={onApplyJson}>Применить JSON</button>
          {project.jsonError ? <div className="videoMatchError">{project.jsonError}</div> : null}
        </section>

        <section className="videoMatchPanel videoMatchBlocksPanel">
          <h2>Segments / candidates</h2>
          {matchSegments.length === 0 ? <div className="videoMatchEmptyList">После применения JSON здесь появятся segments и candidates.</div> : null}
          <div className="videoMatchSegmentsList">
            {matchSegments.map((segment) => {
              const candidates = Array.isArray(segment.candidates) ? segment.candidates : [];
              const isSegmentSelected = segment.id === project.selectedSegmentId;
              const isOpen = effectiveExpandedSegmentId === segment.id;
              return (
                <div
                  key={segment.id}
                  ref={(node) => { if (node) segmentRefs.current[segment.id] = node; }}
                  className={`videoMatchSegmentCard ${isSegmentSelected ? "isSelected" : ""}`}
                >
                  <div className="videoMatchSegmentHeader">
                    <button className="videoMatchSegmentTitle" type="button" onClick={() => { setExpandedSegmentId(isOpen ? "" : segment.id); patchProject({ selectedSegmentId: segment.id, selectedCandidateId: segment.selectedCandidateId || "" }, { lastGood: false }); }}>
                      <b>{segment.audioSceneId || segment.id}</b>
                      <span>story_scene_id: {segment.storySceneId || "—"}</span>
                      <span>target {formatSec(segment.targetStartSec)}–{formatSec(segment.targetEndSec)} · selected candidate {segment.selectedCandidateId || "—"} · candidates {candidates.length}</span>
                    </button>
                    <div className="videoMatchSegmentHeaderActions">
                      {segment.mood ? <small>{segment.mood}</small> : null}
                      <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => setExpandedSegmentId(isOpen ? "" : segment.id)}>{isOpen ? "Свернуть" : "Открыть candidates"}</button>
                    </div>
                  </div>
                  {segment.text ? <p>{segment.text}</p> : null}
                  {segment.visualNeed ? <small className="videoMatchSegmentNeed">Need: {segment.visualNeed}</small> : null}
                  {isOpen ? (
                    <div className="videoMatchCandidatesList">
                      {candidates.map((candidate) => {
                        const isCandidateSelected = candidate.id === segment.selectedCandidateId;
                        const isPreviewCandidate = candidate.id === previewCandidateId;
                        return (
                          <div key={candidate.id} className={`videoMatchCandidateCard ${isCandidateSelected ? "isSelected" : ""} ${isPreviewCandidate ? "isPreview" : ""}`}>
                            {candidate.thumbnail ? <img src={candidate.thumbnail} alt={`${candidate.id} thumbnail`} /> : null}
                            <div className="videoMatchCandidateBody">
                              <b>{candidate.id}{isPreviewCandidate ? " · preview" : ""}</b>
                              <span>video {formatSec(candidate.sourceVideoStartSec)}–{formatSec(candidate.sourceVideoEndSec)} · confidence {candidate.confidence ?? "—"}</span>
                              <span>{[candidate.fitMode, candidate.visualType, candidate.shotType, candidate.motionLevel].filter(Boolean).join(" · ") || "metadata —"}</span>
                              {candidate.matchReason ? <small>{candidate.matchReason}</small> : null}
                              {candidate.warnings?.length ? <small className="videoMatchWarnings">Warnings: {candidate.warnings.join("; ")}</small> : null}
                            </div>
                            <div className="videoMatchCandidateActions">
                              <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onPreviewCandidate(segment, candidate)}>Preview</button>
                              <button className="clipSB_btn clipSB_btnSecondary" type="button" disabled={isCandidateSelected} onClick={() => onSelectCandidate(segment.id, candidate.id)}>{isCandidateSelected ? "Выбран" : "Выбрать candidate"}</button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>

          <h2>Video blocks</h2>
          {videoBlocks.length === 0 ? <div className="videoMatchEmptyList">Selected candidates появятся здесь как video blocks.</div> : null}
          <div className="videoMatchBlocksList">
            {videoBlocks.map((block) => (
              <button key={block.id} type="button" className={`videoMatchBlockCard ${block.id === project.selectedBlockId ? "isSelected" : ""}`} onClick={() => onSelectBlock(block)}>
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
