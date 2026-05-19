import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { API_BASE, fetchJson } from "../../services/api.js";
import {
  getDefaultVideoMatchBoardProject,
  getVideoMatchBoardEmergencyStorageKey,
  getVideoMatchProjectStats,
  buildVideoBlocksFromMatchSegments,
  parseVideoMatchBoardJson,
  persistVideoMatchBoardProject,
  readVideoMatchBoardProjectForNode,
  normalizeVideoMatchSourceVideo,
  shouldSkipVideoMatchPersistToProtectMaterials,
  buildVideoMatchImportSignature,
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

function getBlockTargetStart(block = {}) {
  return Number(block?.targetStartSec ?? block?.target_t0 ?? 0) || 0;
}

function getBlockTargetEnd(block = {}) {
  const start = getBlockTargetStart(block);
  const end = Number(block?.targetEndSec ?? block?.target_t1 ?? start) || start;
  return Math.max(start, end);
}

function getBlockSourceStart(block = {}) {
  return Number(block?.sourceVideoStartSec ?? block?.video_t0 ?? 0) || 0;
}

function getBlockSourceEnd(block = {}) {
  const start = getBlockSourceStart(block);
  const end = Number(block?.sourceVideoEndSec ?? block?.video_t1 ?? start) || start;
  return Math.max(start, end);
}

function findAssemblyBlockByAudioTime(blocks = [], audioTimeSec = 0, fallbackIndex = 0) {
  if (!Array.isArray(blocks) || blocks.length === 0) return { block: null, index: -1 };
  const currentTime = Number(audioTimeSec || 0);
  const index = blocks.findIndex((block) => currentTime >= getBlockTargetStart(block) && currentTime < getBlockTargetEnd(block));
  if (index >= 0) return { block: blocks[index], index };
  const safeFallback = Math.max(0, Math.min(blocks.length - 1, Number(fallbackIndex || 0)));
  return { block: blocks[safeFallback] || blocks[0], index: safeFallback };
}

function resolveOutputUrl(outputUrl = "") {
  const raw = String(outputUrl || "").trim();
  if (!raw) return "";
  if (raw.startsWith("/")) return `${API_BASE}${raw}`;
  return raw;
}

function isBrowserSafeThumbnail(thumbnail = "") {
  const raw = String(thumbnail || "").trim();
  if (!raw) return false;
  if (/^[a-zA-Z]:[\\/]/.test(raw)) return false;
  if (raw.includes("\\")) return false;
  return raw.startsWith("http://")
    || raw.startsWith("https://")
    || raw.startsWith("/")
    || raw.startsWith("data:")
    || raw.startsWith("blob:");
}

function isOverrideCandidate(candidate = {}) {
  return candidate?.sourceKind === "override_video" || Boolean(candidate?.overrideVideoUrl || candidate?.overrideVideoPath);
}

function isOverrideBlock(block = {}) {
  return block?.sourceKind === "override_video" || block?.overrideVideoUrl || block?.overrideVideoPath;
}

function getResolvedOverrideUrl(blockOrCandidate = {}) {
  return resolveOutputUrl(blockOrCandidate?.overrideVideoUrl || "");
}

export default function VideoMatchBoardPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [searchParams] = useSearchParams();
  const nodeId = String(searchParams.get("nodeId") || location.state?.nodeId || "default").trim() || "default";
  const videoRef = useRef(null);
  const audioRef = useRef(null);
  const playbackRef = useRef(null);
  const activeVideoSourceKindRef = useRef("source");
  const objectUrlRef = useRef("");
  const audioObjectUrlRef = useRef("");
  const overrideUploadInputRef = useRef(null);

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
  const [audioDurationSec, setAudioDurationSec] = useState(Number(initialProject?.audioPreviewMeta?.duration_sec || 0));
  const [audioCurrentTimeSec, setAudioCurrentTimeSec] = useState(0);
  const [sourceVideoLoadMessage, setSourceVideoLoadMessage] = useState("");
  const [audioLoadMessage, setAudioLoadMessage] = useState("");
  const [previewCandidateId, setPreviewCandidateId] = useState("");
  const [isAssemblyPlaying, setIsAssemblyPlaying] = useState(false);
  const [isPlaybackActive, setIsPlaybackActive] = useState(false);
  const [assembleAudioPath, setAssembleAudioPath] = useState("");
  const [isAssemblingMp4, setIsAssemblingMp4] = useState(false);
  const [assembledPreview, setAssembledPreview] = useState(null);
  const [assembleError, setAssembleError] = useState("");

  const matchSegments = Array.isArray(project.matchSegments) ? project.matchSegments : [];
  const videoBlocks = Array.isArray(project.videoBlocks) ? project.videoBlocks : [];
  const assemblyBlocks = useMemo(() => [...videoBlocks].sort((a, b) => getBlockTargetStart(a) - getBlockTargetStart(b)), [videoBlocks]);
  const selectedBlock = videoBlocks.find((block) => block.id === project.selectedBlockId) || assemblyBlocks[0] || null;
  const sourceVideoUrl = String(project.sourceVideoUrl || "");
  const audioPreviewUrl = String(project.audioPreviewUrl || "");
  const runtimeSourceVideoUrlRef = useRef(String(initialProject?.sourceVideoUrl || "").startsWith("blob:") ? String(initialProject?.sourceVideoUrl || "") : "");
  const runtimeAudioPreviewUrlRef = useRef(String(initialProject?.audioPreviewUrl || "").startsWith("blob:") ? String(initialProject?.audioPreviewUrl || "") : "");
  const useAudioPreview = Boolean(project.useAudioPreview);
  const timelineDuration = videoDurationSec || Number(project?.sourceVideo?.duration_sec || 0) || 0;
  const effectiveAudioDurationSec = audioDurationSec || Number(project?.audioPreviewMeta?.duration_sec || 0) || 0;
  const assemblyDurationSec = Math.max(0, ...assemblyBlocks.map((block) => getBlockTargetEnd(block)));
  const assembledPreviewOutputUrl = resolveOutputUrl(assembledPreview?.outputUrl || "");
  const candidatesTotal = matchSegments.reduce((total, segment) => total + (Array.isArray(segment?.candidates) ? segment.candidates.length : 0), 0);
  const selectedSegment = matchSegments.find((segment) => segment.id === project.selectedSegmentId || segment.audioSceneId === project.selectedSegmentId) || matchSegments[0] || null;
  const selectedSegmentCandidates = Array.isArray(selectedSegment?.candidates) ? selectedSegment.candidates : [];
  const currentPlayingBlockId = videoBlocks.find((block) => {
    const start = getBlockSourceStart(block);
    const end = getBlockSourceEnd(block);
    return currentTimeSec >= start && currentTimeSec < end;
  })?.id || "";

  const patchProject = (patch = {}, persistOptions = {}) => {
    setProject((prev) => persistVideoMatchBoardProject({ ...prev, ...(patch || {}), nodeId, sourceNodeId: nodeId }, persistOptions));
  };

  const getSelectionPatchForBlock = (block = {}) => ({
    selectedBlockId: block?.id || "",
    selectedSegmentId: block?.segmentId || block?.audioSceneId || "",
    selectedCandidateId: block?.candidateId || block?.id || "",
  });

  const onSelectBlock = (block = {}) => {
    patchProject(getSelectionPatchForBlock(block), { lastGood: false });
  };

  useEffect(() => {
    setProject(initialProject);
    setVideoDurationSec(Number(initialProject?.sourceVideo?.duration_sec || 0));
    setAudioDurationSec(Number(initialProject?.audioPreviewMeta?.duration_sec || 0));
    setCurrentTimeSec(0);
    setAudioCurrentTimeSec(0);
    setSourceVideoLoadMessage("");
    setAudioLoadMessage("");
    setPreviewCandidateId("");
    setIsAssemblyPlaying(false);
    setIsPlaybackActive(false);
    setAssembleAudioPath(String(initialProject?.assembleAudioPath || initialProject?.audioPath || ""));
    setAssembledPreview(null);
    setAssembleError("");
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
    if (audioObjectUrlRef.current) URL.revokeObjectURL(audioObjectUrlRef.current);
  }, []);

  useEffect(() => {
    if (!String(initialProject?.sourceVideoUrl || "").startsWith("blob:")) return;
    runtimeSourceVideoUrlRef.current = "";
    setSourceVideoLoadMessage("Видео для предпросмотра недоступно после перезагрузки. Загрузите source video заново. Тайминг и candidates сохранены.");
    console.info("[VIDEO MATCH BLOB PREVIEW CLEARED]", {
      reason: "reload_blob_source_video_unavailable",
      keptSegments: Array.isArray(initialProject?.matchSegments) ? initialProject.matchSegments.length : 0,
      keptSourceVideoPath: String(initialProject?.sourceVideo?.path || initialProject?.source_video?.path || initialProject?.sourceVideoPath || "").trim(),
    });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialProject?.sourceVideoUrl]);

  useEffect(() => {
    if (!String(initialProject?.audioPreviewUrl || "").startsWith("blob:")) return;
    runtimeAudioPreviewUrlRef.current = "";
    setAudioLoadMessage("Аудио для предпросмотра недоступно после перезагрузки. Загрузите аудио заново. Тайминг сохранён.");
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialProject?.audioPreviewUrl]);

  const showMissingSourceVideoMessage = () => {
    setSourceVideoLoadMessage("Загрузите source video заново");
    patchProject({ jsonError: "Загрузите source video заново" }, { lastGood: false });
  };

  const showMissingAudioMessage = () => {
    setAudioLoadMessage("Загрузите аудио");
    patchProject({ jsonError: "Загрузите аудио для просмотра с аудио" }, { lastGood: false });
  };

  const stopPlayback = () => {
    playbackRef.current = null;
    setIsAssemblyPlaying(false);
    setIsPlaybackActive(false);
    if (videoRef.current) {
      videoRef.current.pause();
      videoRef.current.muted = false;
    }
    if (audioRef.current) audioRef.current.pause();
  };

  const restoreSourceVideoElement = () => {
    if (!videoRef.current || !sourceVideoUrl) return;
    activeVideoSourceKindRef.current = "source";
    const currentSrc = String(videoRef.current.src || "");
    const expectedSrc = String(sourceVideoUrl || "");
    const resolvedExpectedSrc = (() => {
      try {
        return new URL(expectedSrc, window.location.href).href;
      } catch {
        return expectedSrc;
      }
    })();
    if (currentSrc !== expectedSrc && currentSrc !== resolvedExpectedSrc) {
      videoRef.current.src = sourceVideoUrl;
    }
  };

  const getOverrideBlockEndSec = (block = {}) => {
    const overrideDuration = Number(block?.overrideDurationSec || 0);
    const sourceEnd = getBlockSourceEnd(block);
    const targetDuration = Math.max(0, getBlockTargetEnd(block) - getBlockTargetStart(block));
    const baseEnd = Math.max(0, overrideDuration || sourceEnd);
    return targetDuration > 0 ? Math.min(baseEnd, targetDuration) : baseEnd;
  };

  const playOverrideRange = async (block = {}, { muted = false } = {}) => {
    const overrideUrl = getResolvedOverrideUrl(block);
    if (!overrideUrl || !videoRef.current) return false;
    activeVideoSourceKindRef.current = "override";
    videoRef.current.muted = Boolean(muted);
    videoRef.current.src = overrideUrl;
    videoRef.current.currentTime = 0;
    try {
      await videoRef.current.play();
      return true;
    } catch (error) {
      patchProject({ jsonError: `Не удалось запустить override video: ${String(error?.message || error)}` }, { lastGood: false });
      return false;
    }
  };

  const playSourceRange = async (start = 0, end = 0, { muted = false } = {}) => {
    if (!sourceVideoUrl || !videoRef.current) {
      showMissingSourceVideoMessage();
      return false;
    }
    restoreSourceVideoElement();
    activeVideoSourceKindRef.current = "source";
    videoRef.current.muted = Boolean(muted);
    videoRef.current.currentTime = Math.max(0, Number(start || 0));
    try {
      await videoRef.current.play();
      return true;
    } catch (error) {
      patchProject({ jsonError: `Не удалось запустить video player: ${String(error?.message || error)}` }, { lastGood: false });
      return false;
    }
  };

  const playAudioFrom = async (start = 0) => {
    if (!audioPreviewUrl || !audioRef.current) {
      showMissingAudioMessage();
      return false;
    }
    audioRef.current.currentTime = Math.max(0, Number(start || 0));
    try {
      await audioRef.current.play();
      return true;
    } catch (error) {
      patchProject({ jsonError: `Не удалось запустить audio player: ${String(error?.message || error)}` }, { lastGood: false });
      return false;
    }
  };

  const startVideoOnlyBlock = async (block = {}) => {
    const isOverride = isOverrideBlock(block) && block.overrideVideoUrl;
    const start = isOverride ? 0 : getBlockSourceStart(block);
    const end = isOverride ? getOverrideBlockEndSec(block) : getBlockSourceEnd(block);
    playbackRef.current = { mode: isOverride ? "override_video_range" : "video_range", end, blocks: [], index: 0, currentBlockId: block.id || "" };
    if (audioRef.current) audioRef.current.pause();
    setIsAssemblyPlaying(false);
    setIsPlaybackActive(true);
    const didPlay = isOverride
      ? await playOverrideRange(block, { muted: false })
      : await playSourceRange(start, end, { muted: false });
    if (!didPlay) setIsPlaybackActive(false);
    return didPlay;
  };

  const startAudioSyncedBlock = async (block = {}) => {
    if (!audioPreviewUrl || !audioRef.current) {
      showMissingAudioMessage();
      return false;
    }
    const targetStart = getBlockTargetStart(block);
    const targetEnd = getBlockTargetEnd(block);
    playbackRef.current = {
      mode: "audio_range",
      blocks: [block],
      index: 0,
      targetEnd,
      currentBlockId: block.id || "",
    };
    setIsAssemblyPlaying(false);
    setIsPlaybackActive(true);
    const didStartVideo = (isOverrideBlock(block) && block.overrideVideoUrl)
      ? await playOverrideRange(block, { muted: true })
      : await playSourceRange(getBlockSourceStart(block), getBlockSourceEnd(block), { muted: true });
    if (!didStartVideo) {
      setIsPlaybackActive(false);
      return false;
    }
    const didStartAudio = await playAudioFrom(targetStart);
    if (!didStartAudio) {
      if (videoRef.current) videoRef.current.pause();
      setIsPlaybackActive(false);
      return false;
    }
    return true;
  };

  const onPlaySelectedBlock = async () => {
    if (!selectedBlock) return;
    onSelectBlock(selectedBlock);
    if (useAudioPreview) {
      await startAudioSyncedBlock(selectedBlock);
      return;
    }
    await startVideoOnlyBlock(selectedBlock);
  };

  const playAssemblyFromBlock = async (startBlock = null) => {
    if (!assemblyBlocks.length) return;
    restoreSourceVideoElement();
    if (!sourceVideoUrl || !videoRef.current) {
      showMissingSourceVideoMessage();
      return;
    }
    const rawIndex = assemblyBlocks.findIndex((block) => block.id === startBlock?.id);
    const startIndex = rawIndex >= 0 ? rawIndex : 0;
    const firstBlock = assemblyBlocks[startIndex] || assemblyBlocks[0];
    onSelectBlock(firstBlock);

    if (useAudioPreview) {
      if (!audioPreviewUrl || !audioRef.current) {
        showMissingAudioMessage();
        return;
      }
      playbackRef.current = {
        mode: "assembly_audio",
        blocks: assemblyBlocks,
        index: startIndex,
        targetEnd: getBlockTargetEnd(assemblyBlocks[assemblyBlocks.length - 1]),
        currentBlockId: firstBlock.id || "",
      };
      setIsAssemblyPlaying(true);
      setIsPlaybackActive(true);
      const didStartVideo = (isOverrideBlock(firstBlock) && firstBlock.overrideVideoUrl)
        ? await playOverrideRange(firstBlock, { muted: true })
        : await playSourceRange(getBlockSourceStart(firstBlock), getBlockSourceEnd(firstBlock), { muted: true });
      if (!didStartVideo) {
        setIsAssemblyPlaying(false);
        setIsPlaybackActive(false);
        return;
      }
      const didStartAudio = await playAudioFrom(getBlockTargetStart(firstBlock));
      if (!didStartAudio) {
        setIsAssemblyPlaying(false);
        setIsPlaybackActive(false);
        if (videoRef.current) videoRef.current.pause();
      }
      return;
    }

    playbackRef.current = {
      mode: "assembly_video",
      end: (isOverrideBlock(firstBlock) && firstBlock.overrideVideoUrl) ? getOverrideBlockEndSec(firstBlock) : getBlockSourceEnd(firstBlock),
      blocks: assemblyBlocks,
      index: startIndex,
      currentBlockId: firstBlock.id || "",
    };
    setIsAssemblyPlaying(true);
    setIsPlaybackActive(true);
    const didPlay = (isOverrideBlock(firstBlock) && firstBlock.overrideVideoUrl)
      ? await playOverrideRange(firstBlock, { muted: false })
      : await playSourceRange(getBlockSourceStart(firstBlock), getBlockSourceEnd(firstBlock), { muted: false });
    if (!didPlay) {
      setIsAssemblyPlaying(false);
      setIsPlaybackActive(false);
    }
  };

  const onTimeUpdate = () => {
    const current = Number(videoRef.current?.currentTime || 0);
    setCurrentTimeSec(current);
    const playback = playbackRef.current;
    if (!playback) return;

    if (playback.mode === "audio_range" || playback.mode === "assembly_audio") {
      const currentBlock = playback.blocks?.[playback.index] || selectedBlock;
      const sourceEnd = (isOverrideBlock(currentBlock) && currentBlock.overrideVideoUrl) ? getOverrideBlockEndSec(currentBlock) : getBlockSourceEnd(currentBlock);
      if (sourceEnd > 0 && current >= sourceEnd && videoRef.current) {
        videoRef.current.pause();
        videoRef.current.currentTime = sourceEnd;
        setCurrentTimeSec(sourceEnd);
      }
      return;
    }

    const stopAt = Number(playback?.end || 0);
    if (stopAt > 0 && current >= stopAt) {
      videoRef.current.pause();
      videoRef.current.currentTime = stopAt;
      setCurrentTimeSec(stopAt);
      const nextIndex = Number(playback?.index || 0) + 1;
      const nextBlock = Array.isArray(playback?.blocks) ? playback.blocks[nextIndex] : null;
      if (nextBlock) {
        playbackRef.current = { ...playback, index: nextIndex, end: getBlockSourceEnd(nextBlock), currentBlockId: nextBlock.id || "" };
        onSelectBlock(nextBlock);
        if (isOverrideBlock(nextBlock) && nextBlock.overrideVideoUrl) {
          playbackRef.current = { ...playbackRef.current, mode: "assembly_video", end: getOverrideBlockEndSec(nextBlock) };
          void playOverrideRange(nextBlock, { muted: false });
        } else {
          void playSourceRange(getBlockSourceStart(nextBlock), getBlockSourceEnd(nextBlock), { muted: false });
        }
        return;
      }
      playbackRef.current = null;
      setIsAssemblyPlaying(false);
      setIsPlaybackActive(false);
    }
  };

  const onAudioTimeUpdate = () => {
    const current = Number(audioRef.current?.currentTime || 0);
    setAudioCurrentTimeSec(current);
    const playback = playbackRef.current;
    if (!playback || (playback.mode !== "audio_range" && playback.mode !== "assembly_audio")) return;

    if (playback.mode === "audio_range") {
      const targetEnd = Number(playback.targetEnd || 0);
      if (targetEnd > 0 && current >= targetEnd) {
        stopPlayback();
      }
      return;
    }

    const blocks = Array.isArray(playback.blocks) ? playback.blocks : [];
    if (!blocks.length) return;
    const lastTargetEnd = Number(playback.targetEnd || getBlockTargetEnd(blocks[blocks.length - 1]));
    if (lastTargetEnd > 0 && current >= lastTargetEnd) {
      stopPlayback();
      return;
    }

    const found = findAssemblyBlockByAudioTime(blocks, current, playback.index);
    if (!found.block) return;
    if (found.index !== playback.index || found.block.id !== playback.currentBlockId) {
      playbackRef.current = {
        ...playback,
        index: found.index,
        currentBlockId: found.block.id || "",
      };
      onSelectBlock(found.block);
      if (isOverrideBlock(found.block) && found.block.overrideVideoUrl) {
        void playOverrideRange(found.block, { muted: true });
      } else {
        void playSourceRange(getBlockSourceStart(found.block), getBlockSourceEnd(found.block), { muted: true });
      }
    }
  };

  const onAudioEnded = () => {
    const playback = playbackRef.current;
    if (playback?.mode === "audio_range" || playback?.mode === "assembly_audio") stopPlayback();
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
  };

  const onPreviewCandidate = async (segment = {}, candidate = {}) => {
    setPreviewCandidateId(candidate.id || "");
    if (isOverrideCandidate(candidate) && candidate.overrideVideoUrl && videoRef.current) {
      playbackRef.current = null;
      setIsAssemblyPlaying(false);
      setIsPlaybackActive(true);
      if (audioRef.current) audioRef.current.pause();
      patchProject({ selectedSegmentId: segment.id || "", selectedCandidateId: candidate.id || "" }, { lastGood: false });
      const didPlay = await playOverrideRange(candidate, { muted: false });
      if (!didPlay) setIsPlaybackActive(false);
      return;
    }
    restoreSourceVideoElement();
    const start = Math.max(0, Number(candidate.sourceVideoStartSec || 0));
    const end = Math.max(start, Number(candidate.sourceVideoEndSec || start));
    playbackRef.current = { mode: "video_range", end, blocks: [], index: 0, currentBlockId: candidate.id || "" };
    setIsAssemblyPlaying(false);
    setIsPlaybackActive(true);
    if (audioRef.current) audioRef.current.pause();
    patchProject({ selectedSegmentId: segment.id || "", selectedCandidateId: candidate.id || "" }, { lastGood: false });
    const didPlay = await playSourceRange(start, end, { muted: false });
    if (!didPlay) setIsPlaybackActive(false);
  };

  const onUploadOverrideVideo = async (file) => {
    if (!file || !selectedSegment) return;
    const formData = new FormData();
    formData.append("file", file);
    formData.append("nodeId", nodeId);
    formData.append("segmentId", selectedSegment.id || selectedSegment.audioSceneId || "");
    formData.append("candidateType", "user_override");
    try {
      const response = await fetch(`${API_BASE}/api/video-match/override-upload`, { method: "POST", credentials: "include", body: formData });
      const data = await response.json();
      if (!response.ok || !data?.ok) throw new Error(data?.detail?.message || data?.message || "upload failed");
      const durationSec = Number(data.durationSec || 0);
      const segmentId = selectedSegment.id || selectedSegment.audioSceneId;
      const candidateId = `${segmentId}_override_${Date.now()}`;
      const nextSegments = matchSegments.map((segment) => {
        if (segment.id !== selectedSegment.id) return segment;
        const overrideCandidate = {
          id: candidateId, candidateType: "user_override", sourceKind: "override_video",
          overrideVideoPath: data.overrideVideoPath, overrideVideoUrl: data.overrideVideoUrl, overrideDurationSec: durationSec,
          sourceVideoStartSec: 0, sourceVideoEndSec: durationSec, video_t0: 0, video_t1: durationSec, fit_mode: "override",
          confidence: 1, matchReason: "Пользовательская замена видео / lip-sync override.", match_reason: "Пользовательская замена видео / lip-sync override.",
          visualType: "user_override", visual_type: "user_override", shotType: "custom", shot_type: "custom", warnings: [],
        };
        return { ...segment, candidates: [...(Array.isArray(segment.candidates) ? segment.candidates : []), overrideCandidate], selectedCandidateId: candidateId, selected_candidate_id: candidateId };
      });
      const nextBlocks = buildVideoBlocksFromMatchSegments(nextSegments, sourceVideoUrl);
      const nextBlock = nextBlocks.find((block) => block.segmentId === selectedSegment.id && block.candidateId === candidateId) || null;
      patchProject({ matchSegments: nextSegments, videoBlocks: nextBlocks, selectedSegmentId: selectedSegment.id, selectedCandidateId: candidateId, selectedBlockId: nextBlock?.id || "" });
    } catch (error) {
      patchProject({ jsonError: `Не удалось загрузить override: ${String(error?.message || error)}` }, { lastGood: false });
    }
  };

  const onVideoFileChange = (file) => {
    if (!file) return;
    const chosenPathRaw = String(file.path || file.webkitRelativePath || file.name || "").trim();
    const existingPath = String(project?.sourceVideo?.path || project?.source_video?.path || project?.sourceVideoPath || "").trim();
    const hasFullLocalPath = /^[a-zA-Z]:[\\/]/.test(chosenPathRaw) || /^\\\\[^\\]/.test(chosenPathRaw);
    const chosenPath = hasFullLocalPath ? chosenPathRaw : (existingPath || chosenPathRaw);
    if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    const url = URL.createObjectURL(file);
    objectUrlRef.current = url;
    runtimeSourceVideoUrlRef.current = url;
    setVideoDurationSec(0);
    setSourceVideoLoadMessage("");
    patchProject({
      sourceVideoUrl: url,
      sourceVideo: {
        ...(project.sourceVideo || {}),
        path: chosenPath,
        filename: file.name || "source.mp4",
        name: file.name || "source.mp4",
        durationSec: 0,
        duration_sec: 0,
        type: file.type || "video/mp4",
        size: file.size || 0,
      },
      source_video: {
        ...(project.source_video || {}),
        path: chosenPath,
        filename: file.name || "source.mp4",
        duration_sec: 0,
      },
      sourceVideoPath: chosenPath,
      jsonError: "",
    });
  };

  const onAudioFileChange = (file) => {
    if (!file) return;
    if (audioObjectUrlRef.current) URL.revokeObjectURL(audioObjectUrlRef.current);
    const url = URL.createObjectURL(file);
    audioObjectUrlRef.current = url;
    runtimeAudioPreviewUrlRef.current = url;
    setAudioDurationSec(0);
    setAudioCurrentTimeSec(0);
    setAudioLoadMessage("");
    patchProject({
      audioPreviewUrl: url,
      audioPreviewMeta: {
        filename: file.name || "audio.mp3",
        duration_sec: 0,
        type: file.type || "audio/mpeg",
        size: file.size || 0,
      },
      useAudioPreview: true,
    });
  };

  const onLoadedMetadata = () => {
    setSourceVideoLoadMessage("");
    const duration = Number(videoRef.current?.duration || 0);
    if (!Number.isFinite(duration) || duration <= 0) return;
    if (activeVideoSourceKindRef.current === "override") return;
    setVideoDurationSec(duration);
    patchProject({
      sourceVideo: {
        ...(project.sourceVideo || {}),
        duration_sec: Number(duration.toFixed(3)),
      },
    });
  };

  const onLoadedAudioMetadata = () => {
    setAudioLoadMessage("");
    const duration = Number(audioRef.current?.duration || 0);
    if (!Number.isFinite(duration) || duration <= 0) return;
    setAudioDurationSec(duration);
    patchProject({
      audioPreviewMeta: {
        ...(project.audioPreviewMeta || {}),
        duration_sec: Number(duration.toFixed(3)),
      },
    });
  };

  const onSourceVideoError = () => {
    setSourceVideoLoadMessage("Видео недоступно. Если страница перезагружалась, загрузите source video заново.");
    if (String(project.sourceVideoUrl || "").startsWith("blob:")) patchProject({ sourceVideoUrl: "" }, { lastGood: false });
  };

  const onAudioError = () => {
    setAudioLoadMessage("Аудио недоступно. Если страница перезагружалась, загрузите аудио заново.");
    if (String(project.audioPreviewUrl || "").startsWith("blob:")) patchProject({ audioPreviewUrl: "" }, { lastGood: false });
  };

  const onApplyJson = () => {
    try {
      const result = parseVideoMatchBoardJson(project.jsonInput || "", sourceVideoUrl);
      console.log("[VIDEO MATCH APPLY JSON RESULT]", {
        ok: result?.ok,
        error: result?.error,
        hasVideoBlocks: Array.isArray(result?.videoBlocks),
        videoBlocksCount: result?.videoBlocks?.length,
        hasMatchSegments: Array.isArray(result?.matchSegments),
        matchSegmentsCount: result?.matchSegments?.length,
        keys: Object.keys(result || {}),
      });
      if (!result || result.error || result.ok === false) {
        patchProject({ jsonError: String(result?.error || "JSON parse error") }, { lastGood: false });
        return;
      }

      const safeVideoBlocks = Array.isArray(result.videoBlocks) ? result.videoBlocks : [];
      const safeMatchSegments = Array.isArray(result.matchSegments) ? result.matchSegments : [];
      const videoElementDurationSec = getValidDurationSec(videoRef.current?.duration);
      const stateVideoDurationSec = getValidDurationSec(videoDurationSec);
      const loadedVideoDurationSec = videoElementDurationSec || (sourceVideoUrl ? stateVideoDurationSec : 0);
      const jsonDurationSec = getValidDurationSec(result.sourceVideo?.duration_sec);
      const nextDurationSec = loadedVideoDurationSec || jsonDurationSec;
      const currentFilename = String(project.sourceVideo?.filename || "").trim();
      const maxBlockEndSec = Math.max(0, ...safeVideoBlocks.map((block) => Number(block.sourceVideoEndSec || 0)));
      const durationWarning = loadedVideoDurationSec > 0 && maxBlockEndSec > loadedVideoDurationSec
        ? `Warning: JSON содержит video_t1/sourceVideoEndSec ${formatSec(maxBlockEndSec)} с, это больше реальной длительности загруженного видео ${formatSec(loadedVideoDurationSec)} с.`
        : "";

      if (!safeMatchSegments.length) {
        patchProject({
          jsonError: "JSON parsed, but no matchSegments were created. Check schema video_match_board_v2 / segments.",
        }, { lastGood: false });
        return;
      }

      const normalizedSourceVideo = normalizeVideoMatchSourceVideo({
        ...result,
        sourceVideo: result.sourceVideo,
      });
      const normalizedPath = String(normalizedSourceVideo.path || "").trim();
      const keptRuntimeSourceVideoUrl = String(runtimeSourceVideoUrlRef.current || "").startsWith("blob:") ? runtimeSourceVideoUrlRef.current : "";
      const keptRuntimeAudioPreviewUrl = String(runtimeAudioPreviewUrlRef.current || "").startsWith("blob:") ? runtimeAudioPreviewUrlRef.current : "";
      const importedAt = Date.now();
      const importDraft = {
        ...getDefaultVideoMatchBoardProject(nodeId),
        schema: result.schema,
        sourceVideoUrl: "",
        sourceVideo: {
          ...normalizedSourceVideo,
          filename: normalizedSourceVideo.filename || currentFilename || "source.mp4",
          name: normalizedSourceVideo.filename || currentFilename || "source.mp4",
          durationSec: Number(nextDurationSec.toFixed(3)),
          duration_sec: Number(nextDurationSec.toFixed(3)),
        },
        source_video: {
          ...(project.source_video || {}),
          path: normalizedPath,
          filename: normalizedSourceVideo.filename || currentFilename || "source.mp4",
          duration_sec: Number(nextDurationSec.toFixed(3)),
        },
        sourceVideoPath: normalizedPath,
        matchSegments: safeMatchSegments,
        videoBlocks: safeVideoBlocks,
        selectedSegmentId: result.selectedSegmentId,
        selectedCandidateId: result.selectedCandidateId,
        selectedBlockId: safeVideoBlocks[0]?.id || "",
        jsonInput: project.jsonInput || "",
        jsonError: durationWarning,
        audioPreviewUrl: "",
        importedAt,
        sourceNodeId: nodeId,
        nodeId,
      };
      importDraft.importSignature = buildVideoMatchImportSignature(importDraft);
      const importedProject = persistVideoMatchBoardProject(importDraft, { forceReplace: true, allowMaterialLoss: true });
      importedProject.sourceVideoUrl = keptRuntimeSourceVideoUrl;
      importedProject.audioPreviewUrl = keptRuntimeAudioPreviewUrl;
      console.info("[VIDEO MATCH IMPORT COMMIT]", {
        importedAt,
        importSignature: importedProject.importSignature,
        matchSegmentsCount: Array.isArray(importedProject.matchSegments) ? importedProject.matchSegments.length : 0,
        videoBlocksCount: Array.isArray(importedProject.videoBlocks) ? importedProject.videoBlocks.length : 0,
        sourceVideoPath: importedProject.sourceVideoPath,
        keptRuntimeSourceVideoUrl: Boolean(keptRuntimeSourceVideoUrl),
        keptRuntimeAudioPreviewUrl: Boolean(keptRuntimeAudioPreviewUrl),
      });
      console.log("[VIDEO MATCH IMPORT SOURCE VIDEO]", {
        inputSourceVideo: result.sourceVideo,
        inputSource_video: result.source_video,
        normalizedSourceVideo: importDraft.sourceVideo,
        sourceVideoPath: importDraft.sourceVideoPath,
      });
      setProject(importedProject);
      if (!loadedVideoDurationSec && jsonDurationSec > 0) setVideoDurationSec(jsonDurationSec);
    } catch (error) {
      patchProject({ jsonError: String(error?.message || error || "JSON parse error") }, { lastGood: false });
    }
  };

  const onAssembleMp4 = async () => {
    if (!assemblyBlocks.length) return;
    const sourceVideoPath = String(
      project?.sourceVideo?.path
      || project?.source_video?.path
      || project?.sourceVideoPath
      || project?.source_video_path
      || "",
    ).trim();
    if (!sourceVideoPath) {
      setAssembleError("Для сборки нужен sourceVideo.path из JSON или загрузите source video заново");
      console.log("[VIDEO MATCH ASSEMBLY MISSING SOURCE]", {
        sourceVideo: project.sourceVideo,
        source_video: project.source_video,
        sourceVideoPath: project.sourceVideoPath,
        keys: Object.keys(project || {}),
      });
      return;
    }
    setIsAssemblingMp4(true);
    setAssembleError("");
    setAssembledPreview(null);
    try {
      const response = await fetchJson("/api/video-match/assemble", {
        method: "POST",
        body: {
          sourceVideoPath,
          sourceVideo: project?.sourceVideo || {},
          source_video: project?.source_video || {},
          audioPath: assembleAudioPath || project?.timingContext?.sourceAudioPath || "",
          audioUrl: project?.timingContext?.sourceAudioUrl || "",
          outputFormat: "16:9",
          previewQuality: "720p",
          blocks: assemblyBlocks.map((block) => ({
            id: block.id,
            audioSceneId: block.audioSceneId || block.segmentId || "",
            targetStartSec: Number(block.targetStartSec || 0),
            targetEndSec: Number(block.targetEndSec || 0),
            sourceVideoStartSec: Number(block.sourceVideoStartSec || 0),
            sourceVideoEndSec: Number(block.sourceVideoEndSec || 0),
            candidateType: block.candidateType || "",
            sourceKind: block.sourceKind || "",
            overrideVideoPath: block.overrideVideoPath || "",
            overrideVideoUrl: block.overrideVideoUrl || "",
          })),
        },
      });
      setAssembledPreview(response || null);
      if (response?.warning === "audio_missing_backend_path") {
        setAssembleError("MP4 собран без аудио: укажите реальный путь к mp3.");
      }
    } catch (error) {
      setAssembleError(String(error?.message || error || "Не удалось собрать MP4"));
    } finally {
      setIsAssemblingMp4(false);
    }
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
          {
            id: "seg_01_c",
            video_t0: 41.6,
            video_t1: 46.4,
            fit_mode: "fallback",
            confidence: 0.69,
            match_reason: "Backup wide shot keeps the scene readable if the preferred opening feels too static.",
            visual_type: "intro",
            shot_type: "wide",
            emotion: "calm",
            action: "subject prepares the workspace",
            contains_face: true,
            mouth_visible: false,
            lip_sync_candidate: false,
            dialogue_present: false,
            motion_level: "low",
            camera_motion: "static",
            warnings: ["Less precise match", "Use only if pacing needs a calmer opening"],
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
          <p>Компактная доска подбора фрагментов большого видео под аудио-карту.</p>
        </div>
        <button className="btn" type="button" onClick={() => navigate(-1)}>Назад в граф</button>
      </div>

      <div className="videoMatchSummaryBar">
        <span>сцен: {matchSegments.length}</span>
        <span>выбрано сцен: {videoBlocks.length}</span>
        <span>вариантов: {candidatesTotal}</span>
        <span>длительность сборки: {formatSec(assemblyDurationSec)} с</span>
        <span>аудио: {project.audioPreviewMeta?.filename || "—"}</span>
      </div>

      <div className="videoMatchTopWorkspace">
        <section className="videoMatchPanel videoMatchPlayerPanel">
          <div className="videoMatchPanelHeader">
            <h2>Исходное видео</h2>
            <div className="videoMatchHeaderButtons">
              <label className="clipSB_btn clipSB_btnPrimary videoMatchUploadBtn">
                Загрузить видео
                <input type="file" accept="video/*" hidden onChange={(event) => onVideoFileChange(event.target.files?.[0])} />
              </label>
              <label className="clipSB_btn clipSB_btnSecondary videoMatchUploadBtn">
                + Аудио
                <input type="file" accept="audio/*" hidden onChange={(event) => onAudioFileChange(event.target.files?.[0])} />
              </label>
            </div>
          </div>

          <div className="videoMatchVideoBox">
            {sourceVideoUrl ? (
              <video ref={videoRef} src={sourceVideoUrl} controls onLoadedMetadata={onLoadedMetadata} onError={onSourceVideoError} onTimeUpdate={onTimeUpdate} />
            ) : (
              <div className="videoMatchEmptyVideo">Загрузите видеофайл для просмотра.</div>
            )}
          </div>
          <audio
            ref={audioRef}
            src={audioPreviewUrl || undefined}
            onLoadedMetadata={onLoadedAudioMetadata}
            onTimeUpdate={onAudioTimeUpdate}
            onEnded={onAudioEnded}
            onError={onAudioError}
            preload="metadata"
          />
          {sourceVideoLoadMessage ? <div className="videoMatchError">{sourceVideoLoadMessage}</div> : null}
          {audioLoadMessage ? <div className="videoMatchError videoMatchAudioNotice">{audioLoadMessage}</div> : null}

          <div className="videoMatchTimelineMeta">
            <span>{project.sourceVideo?.filename || "source.mp4"}</span>
            <span>{formatSec(currentTimeSec)} / {formatSec(timelineDuration)} с</span>
          </div>
          <div className="videoMatchTimeline" aria-label="Source timeline">
            <div className="videoMatchTimelineProgress" style={{ width: `${timelineDuration > 0 ? Math.min(100, (currentTimeSec / timelineDuration) * 100) : 0}%` }} />
            {videoBlocks.map((block) => (
              <button
                key={block.id}
                type="button"
                className={`videoMatchTimelineBlock ${block.id === project.selectedBlockId ? "isSelected" : ""} ${isAssemblyPlaying && block.id === project.selectedBlockId ? "isPlaying" : ""}`}
                style={{ left: `${getBlockLeft(block, timelineDuration)}%`, width: `${getBlockWidth(block, timelineDuration)}%` }}
                onClick={() => onSelectBlock(block)}
                title={`${block.id}: ${formatSec(block.sourceVideoStartSec)}–${formatSec(block.sourceVideoEndSec)}с`}
              />
            ))}
          </div>

          <div className="videoMatchAudioRow">
            <label className="videoMatchAudioToggle">
              <input
                type="checkbox"
                checked={useAudioPreview}
                onChange={(event) => patchProject({ useAudioPreview: event.target.checked }, { lastGood: false })}
              />
              <span>С аудио</span>
            </label>
            <span>{project.audioPreviewMeta?.filename || "аудио не загружено"}</span>
            <span>{formatSec(audioCurrentTimeSec)} / {formatSec(effectiveAudioDurationSec)} с</span>
          </div>

          <div className="videoMatchAssemblyHeader">
            <h2>Черновая сборка</h2>
            <span>длительность: {formatSec(assemblyDurationSec)} с</span>
          </div>
          {assemblyBlocks.length === 0 ? <div className="videoMatchEmptyList videoMatchCompactEmpty">Выбранные варианты появятся здесь как цветная лента сцен.</div> : null}
          <div className="videoMatchBlocksStrip" aria-label="Video blocks strip">
            {assemblyBlocks.map((block, index) => (
              <button
                key={block.id}
                type="button"
                className={`videoMatchStripSegment ${block.id === project.selectedBlockId ? "isSelected" : ""} ${block.id === currentPlayingBlockId ? "isCurrent" : ""} ${isAssemblyPlaying && block.id === project.selectedBlockId ? "isPlaying" : ""} ${block.sourceKind === "override_video" ? "isOverride" : ""}`}
                style={{ "--strip-color-index": index % 8 }}
                onClick={() => onSelectBlock(block)}
                title={`${block.audioSceneId || block.segmentId}: video ${formatSec(block.sourceVideoStartSec)}–${formatSec(block.sourceVideoEndSec)}с · candidate ${block.candidateId || block.id}`}
              >
                <span>{block.audioSceneId || block.segmentId || `seg_${String(index + 1).padStart(2, "0")}`}</span>
              </button>
            ))}
          </div>
          <div className="videoMatchActions videoMatchPlaybackActions">
            <button className="clipSB_btn clipSB_btnPrimary" type="button" disabled={!selectedBlock} onClick={onPlaySelectedBlock}>▶ Кусок</button>
            <button className="clipSB_btn clipSB_btnPrimary" type="button" disabled={!assemblyBlocks.length} onClick={() => playAssemblyFromBlock(assemblyBlocks[0])}>▶ Сборка</button>
            <button className="clipSB_btn clipSB_btnSecondary" type="button" disabled={!selectedBlock || !assemblyBlocks.length} onClick={() => playAssemblyFromBlock(selectedBlock)}>▶ Отсюда</button>
            <button className="clipSB_btn clipSB_btnSecondary" type="button" disabled={!isPlaybackActive} onClick={stopPlayback}>■ Стоп</button>
            <button className="clipSB_btn clipSB_btnSecondary" type="button" disabled={!assemblyBlocks.length || isAssemblingMp4} onClick={onAssembleMp4}>{isAssemblingMp4 ? "Собираем MP4..." : "⬇ MP4"}</button>
            <span>{selectedBlock ? `${selectedBlock.id}: ${formatSec(selectedBlock.sourceVideoStartSec)}–${formatSec(selectedBlock.sourceVideoEndSec)} с` : "Кусок не выбран"}</span>
          </div>
          <div className="videoMatchContextRows">
            <label>
              Путь к аудио для сборки
              <input
                type="text"
                value={assembleAudioPath}
                onChange={(event) => {
                  const value = event.target.value;
                  setAssembleAudioPath(value);
                  patchProject({ assembleAudioPath: value }, { lastGood: false });
                }}
                placeholder="C:\\path\\to\\practice_30sec_audio.mp3"
              />
            </label>
            <div className="videoMatchWarnings">
              Для MP4-сборки нужен реальный путь к mp3 на диске. Загруженный через +Аудио blob используется только для предпросмотра.
            </div>
            {assembleError ? <div className="videoMatchError">{assembleError}</div> : null}
            {assembledPreview?.ok && assembledPreviewOutputUrl ? (
              <div>
                <a href={assembledPreviewOutputUrl} target="_blank" rel="noreferrer">▶ Смотреть MP4</a>{" "}
                <button
                  className="clipSB_btn clipSB_btnSecondary"
                  type="button"
                  onClick={() => window.open(assembledPreviewOutputUrl, "_blank", "noopener,noreferrer")}
                >
                  ⬇ Скачать MP4
                </button>
                {assembledPreview.warning ? <div className="videoMatchWarnings">warning: {assembledPreview.warning}</div> : null}
              </div>
            ) : null}
          </div>
        </section>

        <aside className="videoMatchPanel videoMatchInspectorPanel">
          <div className="videoMatchPanelHeader">
            <h2>Сцена / Варианты</h2>
            <span>{selectedSegmentCandidates.length} вариантов</span>
          </div>
          <input ref={overrideUploadInputRef} type="file" accept="video/*" hidden onChange={(event) => { onUploadOverrideVideo(event.target.files?.[0]); event.target.value = ""; }} />
          <button className="clipSB_btn clipSB_btnSecondary videoMatchOverrideUploadBtn" type="button" disabled={!selectedSegment} onClick={() => overrideUploadInputRef.current?.click()}>🎭 Заменить видео</button>
          {selectedSegment ? (
            <>
              <div className="videoMatchSceneInfo">
                <b>{selectedSegment.audioSceneId || selectedSegment.id}</b>
                <span>story: {selectedSegment.storySceneId || "—"}</span>
                <span>тайминг: {formatSec(selectedSegment.targetStartSec)}–{formatSec(selectedSegment.targetEndSec)} с</span>
                <span>выбрано: {selectedSegment.selectedCandidateId || "—"}</span>
                {selectedSegment.text ? <p>{selectedSegment.text}</p> : null}
                {selectedSegment.visualNeed ? <small>Нужно: {selectedSegment.visualNeed}</small> : null}
              </div>
              <h3>Варианты</h3>
              <div className="videoMatchCandidatesList videoMatchInspectorCandidates">
                {selectedSegmentCandidates.map((candidate) => {
                  const isCandidateSelected = candidate.id === selectedSegment.selectedCandidateId;
                  const isPreviewCandidate = candidate.id === previewCandidateId;
                  return (
                    <div key={candidate.id} className={`videoMatchCandidateCard ${isCandidateSelected ? "isSelected" : ""} ${isPreviewCandidate ? "isPreview" : ""} ${isOverrideCandidate(candidate) ? "isOverride" : ""}`}>
                      {isBrowserSafeThumbnail(candidate.thumbnail) ? <img src={candidate.thumbnail} alt={`${candidate.id} thumbnail`} /> : null}
                      <div className="videoMatchCandidateBody">
                        <b>{candidate.id}{isCandidateSelected ? " · выбрано" : ""}{isPreviewCandidate ? " · просмотр" : ""}</b>
                        <span>видео: {formatSec(candidate.sourceVideoStartSec)}–{formatSec(candidate.sourceVideoEndSec)} · уверенность: {candidate.confidence ?? "—"}</span>
                        {isOverrideCandidate(candidate) ? <span className="videoMatchCandidateBadge">свой клип · lip-sync override</span> : null}
                        {isOverrideCandidate(candidate) ? <small>длина клипа: {formatSec(candidate.overrideDurationSec || candidate.sourceVideoEndSec)}с / цель: {formatSec((selectedSegment?.targetEndSec || 0) - (selectedSegment?.targetStartSec || 0))}с</small> : null}
                        {candidate.matchReason ? <small>{candidate.matchReason}</small> : null}
                        {candidate.warnings?.length ? <small className="videoMatchWarnings">Предупреждения: {candidate.warnings.join("; ")}</small> : null}
                      </div>
                      <div className="videoMatchCandidateActions">
                        <button className="clipSB_btn clipSB_btnSecondary videoMatchIconBtn" type="button" onClick={() => onPreviewCandidate(selectedSegment, candidate)}>▶</button>
                        <button className="clipSB_btn clipSB_btnSecondary videoMatchIconBtn" type="button" disabled={isCandidateSelected} title={isCandidateSelected ? "выбрано" : "Выбрать"} onClick={() => onSelectCandidate(selectedSegment.id, candidate.id)}>✓</button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="videoMatchEmptyList">Примените JSON и выберите сцену на strip.</div>
          )}
        </aside>
      </div>

      <div className="videoMatchBelowGrid videoMatchMiniPanels">
        <details className="videoMatchPanel videoMatchDetailsPanel videoMatchJsonPanel">
          <summary>JSON от Codex</summary>
          <div className="videoMatchJsonActions">
            <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => patchProject({ jsonInput: sampleJson }, { lastGood: false })}>Вставить пример</button>
            <button className="clipSB_btn clipSB_btnPrimary" type="button" onClick={onApplyJson}>Применить JSON</button>
          </div>
          <textarea value={project.jsonInput || ""} onChange={(event) => patchProject({ jsonInput: event.target.value, jsonError: "" }, { lastGood: false })} placeholder="Вставьте JSON schema video_match_board_v1 или video_match_board_v2..." />
          {project.jsonError ? <div className="videoMatchError">{project.jsonError}</div> : null}
        </details>

        <details className="videoMatchPanel videoMatchDetailsPanel">
          <summary>Аудио-карта</summary>
          <div className="videoMatchContextRows">
            <div>sourceAudioUrl: {project.timingContext?.sourceAudioUrl || "—"}</div>
            <div>audioDurationSec: {formatSec(project.timingContext?.audioDurationSec)}</div>
            <div>timingScenes: {Array.isArray(project.timingContext?.timingScenes) ? project.timingContext.timingScenes.length : 0}</div>
            <div>segments: {Array.isArray(project.timingContext?.segments) ? project.timingContext.segments.length : 0}</div>
            <div>podcastEditManifest: {project.timingContext?.podcastEditManifest ? "есть" : "—"}</div>
            <div>composerEditManifest: {project.timingContext?.composerEditManifest ? "есть" : "—"}</div>
          </div>
        </details>

        <details className="videoMatchPanel videoMatchDetailsPanel videoMatchBlocksPanel">
          <summary>Статистика / Debug</summary>
          <div className="videoMatchContextRows">
            <div>сцен: {matchSegments.length}</div>
            <div>вариантов: {candidatesTotal}</div>
            <div>video blocks: {videoBlocks.length}</div>
            <div>длительность сборки: {formatSec(assemblyDurationSec)} с</div>
            <div>аудио preview: {project.audioPreviewMeta?.filename || "—"}</div>
            <div>audioPreviewUrl: {project.audioPreviewUrl ? "есть" : "—"}</div>
            <div>selectedSegmentId: {project.selectedSegmentId || "—"}</div>
            <div>selectedCandidateId: {project.selectedCandidateId || "—"}</div>
            <div>selectedBlockId: {project.selectedBlockId || "—"}</div>
          </div>

          <details className="videoMatchNestedDebug">
            <summary>Segments / candidates</summary>
            {matchSegments.length === 0 ? <div className="videoMatchEmptyList">После применения JSON здесь появятся segments и candidates.</div> : null}
            <div className="videoMatchSegmentsList">
              {matchSegments.map((segment) => {
                const candidates = Array.isArray(segment.candidates) ? segment.candidates : [];
                const isSegmentSelected = segment.id === project.selectedSegmentId;
                const isOpen = segment.id === project.selectedSegmentId;
                return (
                  <div key={segment.id} className={`videoMatchSegmentCard ${isSegmentSelected ? "isSelected" : ""}`}>
                    <div className="videoMatchSegmentHeader">
                      <div className="videoMatchSegmentTitle">
                        <b>{segment.audioSceneId || segment.id}</b>
                        <span>story: {segment.storySceneId || "—"}</span>
                        <span>тайминг {formatSec(segment.targetStartSec)}–{formatSec(segment.targetEndSec)} · выбрано {segment.selectedCandidateId || "—"} · вариантов {candidates.length}</span>
                      </div>
                    </div>
                    {segment.text ? <p>{segment.text}</p> : null}
                    {segment.visualNeed ? <small className="videoMatchSegmentNeed">Нужно: {segment.visualNeed}</small> : null}
                    {isOpen ? (
                      <div className="videoMatchCandidatesList">
                        {candidates.map((candidate) => {
                          const isCandidateSelected = candidate.id === segment.selectedCandidateId;
                          const isPreviewCandidate = candidate.id === previewCandidateId;
                          return (
                            <div key={candidate.id} className={`videoMatchCandidateCard ${isCandidateSelected ? "isSelected" : ""} ${isPreviewCandidate ? "isPreview" : ""}`}>
                              {isBrowserSafeThumbnail(candidate.thumbnail) ? <img src={candidate.thumbnail} alt={`${candidate.id} thumbnail`} /> : null}
                              <div className="videoMatchCandidateBody">
                                <b>{candidate.id}{isPreviewCandidate ? " · просмотр" : ""}</b>
                                <span>видео {formatSec(candidate.sourceVideoStartSec)}–{formatSec(candidate.sourceVideoEndSec)} · уверенность {candidate.confidence ?? "—"}</span>
                                {candidate.matchReason ? <small>{candidate.matchReason}</small> : null}
                                {candidate.warnings?.length ? <small className="videoMatchWarnings">Предупреждения: {candidate.warnings.join("; ")}</small> : null}
                              </div>
                              <div className="videoMatchCandidateActions">
                                <button className="clipSB_btn clipSB_btnSecondary videoMatchIconBtn" type="button" onClick={() => onPreviewCandidate(segment, candidate)}>▶</button>
                                <button className="clipSB_btn clipSB_btnSecondary videoMatchIconBtn" type="button" disabled={isCandidateSelected} title={isCandidateSelected ? "выбрано" : "Выбрать"} onClick={() => onSelectCandidate(segment.id, candidate.id)}>✓</button>
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
          </details>

          <details className="videoMatchNestedDebug">
            <summary>Video blocks</summary>
            {videoBlocks.length === 0 ? <div className="videoMatchEmptyList">Выбранные варианты появятся здесь как video blocks.</div> : null}
            <div className="videoMatchBlocksList">
              {videoBlocks.map((block) => (
                <button key={block.id} type="button" className={`videoMatchBlockCard ${block.id === project.selectedBlockId ? "isSelected" : ""}`} onClick={() => onSelectBlock(block)}>
                  <b>{block.id}</b>
                  <span>audio: {block.audioSceneId || "—"} · тайминг {formatSec(block.targetStartSec)}–{formatSec(block.targetEndSec)}</span>
                  <span>видео {formatSec(block.sourceVideoStartSec)}–{formatSec(block.sourceVideoEndSec)} · уверенность {block.confidence ?? "—"}</span>
                  {block.matchReason ? <small>{block.matchReason}</small> : null}
                </button>
              ))}
            </div>
          </details>
        </details>
      </div>
    </div>
  );
}
