import React, { useEffect, useMemo, useRef, useState } from "react";
import { resolveScenarioFinalRouteKey, resolveSceneDisplayTime } from "./scenarioStoryboardDomain";
import { resolveAssetUrl } from "./comfyNodeShared";
const CLIP_TRACE_SCENARIO_GLOBAL_MUSIC = false;
const CLIP_TRACE_SCENARIO_EDITOR_DEBUG = false;

const TOP_TABS = [
  { id: "scenario", label: "Сценарий" },
  { id: "context", label: "Контекст" },
  { id: "actors", label: "Актеры" },
  { id: "phrases", label: "Фразы" },
  { id: "debug", label: "Debug" },
];

const BG_AUDIO_ITEM_ID = "__bg_audio__";

function fmtSec(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  return num.toFixed(1);
}

function safeSceneDuration(scene = {}) {
  const explicit = Number(scene?.audioSliceExpectedDurationSec ?? scene?.durationSec);
  if (Number.isFinite(explicit) && explicit >= 0) return explicit;
  const { startSec, endSec } = resolveSceneDisplayTime(scene);
  return Math.max(0, Number(endSec) - Number(startSec));
}

function resolveBlockStatus({ runtimeStatus = "", assetUrl = "" } = {}) {
  const status = String(runtimeStatus || "").trim().toLowerCase();
  if (["loading", "queued", "running", "generating"].includes(status)) return "loading";
  if (status === "error" || status === "not_found") return "error";
  if (status === "done" || String(assetUrl || "").trim()) return "done";
  return "idle";
}

function hydrateSceneWithRuntime(scene = {}, runtime = {}) {
  const safeScene = scene && typeof scene === "object" ? scene : {};
  const safeRuntime = runtime && typeof runtime === "object" ? runtime : {};
  return {
    ...safeScene,
    audioSliceStatus: String(safeRuntime?.audioSliceStatus || safeScene?.audioSliceStatus || safeScene?.extractedAudioStatus || "").trim(),
    audioSliceUrl: String(safeRuntime?.audioSliceUrl || safeScene?.audioSliceUrl || safeScene?.extractedAudioUrl || "").trim(),
    audioSliceDurationSec: Number(safeRuntime?.audioSliceDurationSec ?? safeScene?.audioSliceDurationSec ?? safeScene?.extractedAudioDurationSec),
    audioSliceError: String(safeRuntime?.audioSliceError || safeScene?.audioSliceError || safeScene?.extractedAudioError || "").trim(),
    audioSliceLoadError: String(safeRuntime?.audioSliceLoadError || safeScene?.audioSliceLoadError || "").trim(),
  };
}

function sceneBadges(scene = {}) {
  const badges = [];
  const { finalRoute } = resolveUiRoute(scene);
  if (finalRoute === "lip_sync_music") badges.push("lip_sync_music");
  else if (finalRoute === "f_l") badges.push("first_last");
  else badges.push("i2v");
  const warnings = Array.isArray(scene?.contractWarnings) ? scene.contractWarnings : [];
  if (warnings.length) badges.push(`warnings:${warnings.length}`);
  return badges;
}

function resolveUiRoute(scene = {}) {
  const directCandidates = [
    { source: "videoGenerationRoute", value: scene?.videoGenerationRoute ?? scene?.video_generation_route },
    { source: "plannedVideoGenerationRoute", value: scene?.plannedVideoGenerationRoute ?? scene?.planned_video_generation_route },
    { source: "sourceRoute", value: scene?.sourceRoute ?? scene?.source_route },
  ];
  for (const candidate of directCandidates) {
    const normalized = String(candidate?.value || "").trim().toLowerCase();
    if (!normalized) continue;
    if (["avatar_lipsync", "lip_sync", "lip_sync_music"].includes(normalized)) return { source: candidate.source, value: normalized, finalRoute: "lip_sync_music" };
    if (["first_last", "f_l", "f_l_as", "imag-imag-video-bz"].includes(normalized)) return { source: candidate.source, value: normalized, finalRoute: "f_l" };
    return { source: candidate.source, value: normalized, finalRoute: "i2v" };
  }
  return { source: "legacy", value: "", finalRoute: resolveScenarioFinalRouteKey(scene) };
}

function renderContractWarnings(scene = {}) {
  const warnings = Array.isArray(scene?.contractWarnings) ? scene.contractWarnings : [];
  if (!warnings.length) return <span className="clipSB_tag clipSB_tagStatus clipSB_tagStatus--done">no warnings</span>;
  return warnings.map((warning, idx) => (
    <span key={`${warning?.code || idx}-${idx}`} className="clipSB_tag clipSB_tagStatus clipSB_tagStatus--error">
      {String(warning?.label || warning?.code || "warning")}
    </span>
  ));
}

function ContractField({ label, value }) {
  const printable = Array.isArray(value) ? value.join(", ") : String(value || "").trim();
  return (
    <div
      className="clipSB_storyboardKv clipSB_copySelectable nodrag nopan"
      onMouseDown={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <span>{label}</span>
      <strong>{printable || "—"}</strong>
    </div>
  );
}

function ScenarioReadonlyTextField({ label, value, minRows = 3 }) {
  const printable = Array.isArray(value) ? value.join("\n") : String(value || "").trim();
  const lineCount = Math.max(minRows, String(printable || "—").split("\n").length);
  return (
    <div
      className="clipSB_storyboardKv clipSB_copySelectable clipSB_readonlyTextFieldWrap nodrag nopan nowheel"
      onMouseDown={(event) => event.stopPropagation()}
      onPointerDown={(event) => event.stopPropagation()}
    >
      <span>{label}</span>
      <textarea
        className="clipSB_readonlyTextField"
        readOnly
        rows={lineCount}
        value={printable || "—"}
        onMouseDown={(event) => event.stopPropagation()}
        onPointerDown={(event) => event.stopPropagation()}
      />
    </div>
  );
}

function isLongText(value) {
  const text = String(value || "").trim();
  return text.length > 80 || text.includes("\n");
}

function isFirstLastScene(scene = {}) {
  const imageStrategy = String(scene?.imageStrategy || "").trim().toLowerCase();
  if (imageStrategy === "first_last") return true;
  const mode = String(scene?.renderMode || "").trim().toLowerCase();
  if (mode === "first_last") return true;
  if (scene?.requiresTwoFrames === true || scene?.needsTwoFrames === true) return true;
  const ltxMode = String(scene?.ltxMode || scene?.ltx_mode || "").trim().toLowerCase();
  return ["f_l", "first_last"].includes(ltxMode);
}

function resolveScenePreviewSources(scene = {}) {
  const imageStrategy = String(scene?.imageStrategy || "").trim().toLowerCase() || "single";
  const transitionType = String(scene?.transitionType || "").trim().toLowerCase();
  const resolvedSinglePreviewSrc = String(resolveAssetUrl(scene?.imageUrl || "") || "").trim();
  const resolvedStartPreviewSrc = String(resolveAssetUrl(
    scene?.startImageUrl
    || scene?.startFrameImageUrl
    || scene?.startFramePreviewUrl
    || scene?.imageUrl
    || ""
  ) || "").trim();
  const resolvedEndPreviewSrc = String(resolveAssetUrl(
    scene?.endImageUrl
    || scene?.endFrameImageUrl
    || scene?.endFramePreviewUrl
    || ""
  ) || "").trim();
  const isContinuous = transitionType === "continuous" || imageStrategy === "continuation" || imageStrategy === "first_last";
  const resolvedPreviewSrc = isContinuous
    ? (resolvedStartPreviewSrc || resolvedEndPreviewSrc || resolvedSinglePreviewSrc)
    : (resolvedSinglePreviewSrc || resolvedStartPreviewSrc || resolvedEndPreviewSrc);
  return { resolvedPreviewSrc, resolvedSinglePreviewSrc, resolvedStartPreviewSrc, resolvedEndPreviewSrc };
}

function deriveFirstLastFramePrompts(scene = {}) {
  const startExplicit = String(scene?.startFramePromptRu || scene?.startFramePromptEn || scene?.startFramePrompt || "").trim();
  const endExplicit = String(scene?.endFramePromptRu || scene?.endFramePromptEn || scene?.endFramePrompt || "").trim();
  if (startExplicit && endExplicit) {
    return { start: startExplicit, end: endExplicit, derived: false };
  }

  const sceneGoal = String(scene?.sceneGoal || "").trim();
  const frameDescription = String(scene?.frameDescription || "").trim();
  const imagePrompt = String(scene?.imagePromptRu || scene?.imagePromptEn || scene?.imagePrompt || "").trim();
  const videoPrompt = String(scene?.videoPromptRu || scene?.videoPromptEn || scene?.videoPrompt || "").trim();
  const transitionType = String(scene?.transitionType || "state shift").trim().replaceAll("_", " ");
  const transitionSemantics = videoPrompt || `First→last transition with ${transitionType}.`;

  const start = startExplicit || frameDescription || sceneGoal || imagePrompt || videoPrompt;
  let end = endExplicit || sceneGoal || imagePrompt || frameDescription || videoPrompt;
  if (start) {
    end = end || start;
    end = `${end}. Финальное визуально изменённое состояние: ${transitionSemantics}. Обеспечьте читаемый A→B с явной композиционной разницей.`;
  }
  if (start && end && start.toLowerCase() === end.toLowerCase()) {
    end = `${end}. Финальный кадр должен заметно отличаться от первого.`;
  }

  return { start, end, derived: true };
}

function getFramePromptPlaceholder(kind = "start") {
  return kind === "end"
    ? "Опишите финальный визуальный state (что изменилось к концу сцены)."
    : "Опишите стартовый визуальный state (как выглядит первый кадр).";
}



function isShortMusicIntroPhrase(phrase = {}) {
  const text = String(phrase?.text || "").trim().toLowerCase();
  const normalizedText = text.replace(/[^a-z0-9а-я]+/gi, " ").trim();
  if (!["music intro", "instrumental intro"].includes(normalizedText)) return false;
  const startSec = Number(phrase?.startSec ?? phrase?.t0 ?? 0);
  const endRaw = Number(phrase?.endSec ?? phrase?.t1 ?? startSec);
  const endSec = Number.isFinite(endRaw) && endRaw >= startSec ? endRaw : startSec;
  const durationSec = Math.max(0, endSec - startSec);
  return Number.isFinite(startSec) && startSec <= 0.05 && durationSec <= 1.0;
}
function resolveMusicSource(audioData = {}) {
  if (String(audioData?.musicSource || "").trim()) return String(audioData.musicSource).trim().toLowerCase();
  if (String(audioData?.fileName || "").trim()) return "uploaded";
  if (String(audioData?.musicUrl || "").trim()) return "generated";
  return "none";
}

function toPrintable(value) {
  if (Array.isArray(value)) return value.length ? value.join(", ") : "—";
  const text = String(value ?? "").trim();
  return text || "—";
}

function resolveSceneId(scene = {}, idx = 0) {
  const direct = String(scene?.sceneId || "").trim();
  if (direct) return direct;
  const snakeCase = String(scene?.scene_id || "").trim();
  if (snakeCase) return snakeCase;
  const legacy = String(scene?.id || "").trim();
  if (legacy) return legacy;
  return `S${idx + 1}`;
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

export default function ScenarioStoryboardEditor({
  open,
  nodeId,
  storyboardRevision,
  storyboardSignature,
  scenes,
  sceneGeneration,
  audioData,
  scenarioMode,
  masterAudioUrl: masterAudioUrlProp,
  scenarioNodeAudioUrl = "",
  scenarioNodeMasterAudioUrl = "",
  connectedSourceAudioUrl = "",
  globalAudioUrl = "",
  onClose,
  onUpdateScene,
  onGenerateScene,
  onUpdateMusic,
  onGenerateMusic,
  onExtractSceneAudio,
}) {
  const [activeSelectionType, setActiveSelectionType] = useState("scene");
  const [activeSelectionId, setActiveSelectionId] = useState("");
  const [activeTab, setActiveTab] = useState("phrases");
  const [infoModalOpen, setInfoModalOpen] = useState(false);
  const [audioSceneOpen, setAudioSceneOpen] = useState(false);
  const masterAudioRef = useRef(null);
  const bgMusicUploadRef = useRef(null);
  const phrasePlaybackRef = useRef({ sceneId: "", phraseIndex: -1, t0: 0, t1: 0 });
  const [playingPhraseIndex, setPlayingPhraseIndex] = useState(-1);
  const [phrasePlaybackError, setPhrasePlaybackError] = useState("");
  const prevStoryboardRevisionRef = useRef("");
  const stopNodeDragEvent = (event) => event.stopPropagation();

  const safeScenes = Array.isArray(scenes) ? scenes : [];
  const safeGeneration = sceneGeneration && typeof sceneGeneration === "object" ? sceneGeneration : {};
  const normalizedScenes = useMemo(
    () => safeScenes.map((scene, idx) => {
      const normalized = { ...(scene || {}), sceneId: resolveSceneId(scene, idx) };
      const runtime = safeGeneration[String(normalized?.sceneId || "").trim()];
      return hydrateSceneWithRuntime(normalized, runtime);
    }),
    [safeGeneration, safeScenes]
  );
  const safeAudioData = audioData && typeof audioData === "object" ? audioData : {};
  const masterAudioResolution = useMemo(() => {
    const scenarioNodeAudioDataUrl = String(safeAudioData?.audioUrl || "").trim();
    if (scenarioNodeAudioDataUrl) {
      return { source: "scenario_node_audioData", resolvedMasterAudioUrl: scenarioNodeAudioDataUrl };
    }
    const scenarioNodeAudioUrlResolved = String(scenarioNodeAudioUrl || "").trim();
    if (scenarioNodeAudioUrlResolved) {
      return { source: "scenario_node_audioUrl", resolvedMasterAudioUrl: scenarioNodeAudioUrlResolved };
    }
    const scenarioNodeMasterAudioUrlResolved = String(scenarioNodeMasterAudioUrl || masterAudioUrlProp || "").trim();
    if (scenarioNodeMasterAudioUrlResolved) {
      return { source: "scenario_node_masterAudioUrl", resolvedMasterAudioUrl: scenarioNodeMasterAudioUrlResolved };
    }
    const connectedSourceAudioUrlResolved = String(connectedSourceAudioUrl || "").trim();
    if (connectedSourceAudioUrlResolved) {
      return { source: "connected_source_node", resolvedMasterAudioUrl: connectedSourceAudioUrlResolved };
    }
    const globalAudioUrlResolved = String(globalAudioUrl || "").trim();
    if (globalAudioUrlResolved) {
      return { source: "global_audio_node", resolvedMasterAudioUrl: globalAudioUrlResolved };
    }
    return { source: "missing", resolvedMasterAudioUrl: "" };
  }, [connectedSourceAudioUrl, globalAudioUrl, masterAudioUrlProp, safeAudioData?.audioUrl, scenarioNodeAudioUrl, scenarioNodeMasterAudioUrl]);
  const masterAudioUrl = masterAudioResolution.resolvedMasterAudioUrl;
  const hasBgAudioAvailable = Boolean(masterAudioUrl);

  useEffect(() => {
    console.debug("[SCENARIO MASTER AUDIO RESOLVED]", {
      nodeId: String(nodeId || ""),
      source: masterAudioResolution.source,
      resolvedMasterAudioUrl: masterAudioResolution.resolvedMasterAudioUrl,
    });
  }, [masterAudioResolution, nodeId]);

  useEffect(() => {
    if (!open) return;
    const firstSceneId = String(normalizedScenes?.[0]?.sceneId || "").trim();
    const selectedSceneStillExists = normalizedScenes.some((scene) => String(scene?.sceneId || "").trim() === String(activeSelectionId || "").trim());
    if (activeSelectionType === "bg_audio" && hasBgAudioAvailable) {
      setActiveSelectionId(BG_AUDIO_ITEM_ID);
      return;
    }
    if (activeSelectionType === "scene" && selectedSceneStillExists) {
      return;
    }
    if (firstSceneId) {
      setActiveSelectionType("scene");
      setActiveSelectionId(firstSceneId);
    } else {
      setActiveSelectionType("bg_audio");
      setActiveSelectionId(BG_AUDIO_ITEM_ID);
    }
  }, [activeSelectionId, activeSelectionType, hasBgAudioAvailable, nodeId, normalizedScenes, open]);

  useEffect(() => {
    if (!open) return;
    const previousRevision = String(prevStoryboardRevisionRef.current || "");
    const nextRevision = String(storyboardRevision || "");
    const revisionChanged = Boolean(nextRevision) && previousRevision !== nextRevision;
    const firstSceneId = String(normalizedScenes?.[0]?.sceneId || "").trim();
    const hasSelectedScene = normalizedScenes.some((scene) => String(scene?.sceneId || "").trim() === String(activeSelectionId || "").trim());
    const isBgAudioSelectedNow = activeSelectionType === "bg_audio";
    if (revisionChanged) {
      if (isBgAudioSelectedNow && hasBgAudioAvailable) {
        setActiveSelectionType("bg_audio");
        setActiveSelectionId(BG_AUDIO_ITEM_ID);
      } else if (hasSelectedScene) {
        setActiveSelectionType("scene");
      } else if (firstSceneId) {
        setActiveSelectionType("scene");
        setActiveSelectionId(firstSceneId);
      } else {
        setActiveSelectionType("bg_audio");
        setActiveSelectionId(BG_AUDIO_ITEM_ID);
      }
    }
    prevStoryboardRevisionRef.current = nextRevision;
    if (CLIP_TRACE_SCENARIO_EDITOR_DEBUG) {
      console.debug("[SCENARIO EDITOR SYNC]", {
        revisionChanged,
        usingNewPackage: revisionChanged,
        scenesCount: normalizedScenes.length,
        preservedSelectedScene: hasSelectedScene,
        selectionType: activeSelectionType,
        selectionId: activeSelectionId,
        isBgAudioSelected: isBgAudioSelectedNow,
      });
    }
  }, [activeSelectionId, activeSelectionType, hasBgAudioAvailable, normalizedScenes, open, storyboardRevision, storyboardSignature]);

  useEffect(() => {
    if (!open) return;
    const firstSceneId = String(normalizedScenes?.[0]?.sceneId || "").trim();
    const isBgAudioSelectedNow = activeSelectionType === "bg_audio";
    if (isBgAudioSelectedNow && hasBgAudioAvailable) {
      return;
    }
    const hasSelectedScene = Array.isArray(normalizedScenes) && normalizedScenes.some((scene) => String(scene?.sceneId || "") === activeSelectionId);
    if (!hasSelectedScene && !isBgAudioSelectedNow && firstSceneId) {
      setActiveSelectionType("scene");
      setActiveSelectionId(firstSceneId);
      return;
    }
    if (!hasSelectedScene && !firstSceneId) {
      setActiveSelectionType("bg_audio");
      setActiveSelectionId(BG_AUDIO_ITEM_ID);
    }
  }, [activeSelectionId, activeSelectionType, hasBgAudioAvailable, normalizedScenes, open]);

  useEffect(() => {
    if (!open || !infoModalOpen) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") setInfoModalOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, infoModalOpen]);

  const phrases = useMemo(() => {
    if (Array.isArray(safeAudioData?.phrases) && safeAudioData.phrases.length) return safeAudioData.phrases;
    return normalizedScenes.map((scene, idx) => ({
      sceneId: String(scene?.sceneId || resolveSceneId(scene, idx)),
      startSec: resolveSceneDisplayTime(scene).startSec,
      endSec: resolveSceneDisplayTime(scene).endSec,
      text: String(scene?.localPhrase || scene?.summaryRu || "").trim(),
      energy: String(scene?.emotionRu || "").trim(),
      context: String(scene?.locationRu || "").trim(),
    }));
  }, [normalizedScenes, safeAudioData?.phrases]);
  const phrasesForUi = useMemo(() => {
    if (!Array.isArray(phrases) || !phrases.length) return [];
    return phrases.filter((phrase) => !isShortMusicIntroPhrase(phrase));
  }, [phrases]);

  const safeIndex = normalizedScenes.findIndex((scene) => String(scene?.sceneId || "") === activeSelectionId);
  const selectedScene = safeIndex >= 0 ? normalizedScenes[safeIndex] : null;
  const selectedDisplayTime = resolveSceneDisplayTime(selectedScene);
  const selectedSceneId = String(selectedScene?.sceneId || "").trim();
  const selectedRuntime = safeGeneration[selectedSceneId] && typeof safeGeneration[selectedSceneId] === "object" ? safeGeneration[selectedSceneId] : {};
  const resolvePhraseSceneId = (phrase, idx) => String(phrase?.sceneId || normalizedScenes[idx]?.sceneId || "").trim();
  const selectedPhraseIndex = phrasesForUi.findIndex((phrase, idx) => resolvePhraseSceneId(phrase, idx) === selectedSceneId);
  const generateMeta = {
    activeTab,
    selectedTab: activeTab,
  };

  const handleSelectPhrase = (phrase, idx) => {
    const phraseSceneId = resolvePhraseSceneId(phrase, idx);
    if (!phraseSceneId) return;
    setActiveSelectionType("scene");
    setActiveSelectionId(phraseSceneId);
  };

  const jumpToPhrase = (phrase, idx) => {
    const audio = masterAudioRef.current;
    if (!audio) {
      setPhrasePlaybackError("Master audio плеер недоступен.");
      return;
    }
    if (!masterAudioUrl) {
      setPhrasePlaybackError("Master audio отсутствует: нет audioUrl/musicUrl.");
      return;
    }
    const t0 = Number(phrase?.startSec ?? phrase?.t0);
    const t1Raw = Number(phrase?.endSec ?? phrase?.t1);
    if (!Number.isFinite(t0)) {
      setPhrasePlaybackError("Некорректный start time у фразы.");
      return;
    }
    const t1 = Number.isFinite(t1Raw) && t1Raw > t0 ? t1Raw : t0 + 0.25;
    const phraseSceneId = resolvePhraseSceneId(phrase, idx);
    console.debug("[SCENARIO PHRASE JUMP]", {
      sceneId: phraseSceneId,
      phraseText: String(phrase?.text || "").trim(),
      t0,
      t1,
      masterAudioUrl,
      currentSrc: String(audio?.currentSrc || "").trim(),
    });
    phrasePlaybackRef.current = { sceneId: phraseSceneId, phraseIndex: idx, t0, t1 };
    setPlayingPhraseIndex(idx);
    setPhrasePlaybackError("");
    audio.currentTime = Math.max(0, t0);
    audio.play().catch((error) => {
      setPhrasePlaybackError(String(error?.message || "Не удалось запустить воспроизведение фразы."));
      setPlayingPhraseIndex(-1);
    });
  };

  useEffect(() => {
    const audio = masterAudioRef.current;
    if (!audio) return undefined;

    const onTimeUpdate = () => {
      const playback = phrasePlaybackRef.current;
      if (!playback || playback.phraseIndex < 0) return;
      if (audio.currentTime >= Number(playback.t1 || 0)) {
        audio.pause();
        setPlayingPhraseIndex(-1);
      }
    };
    const onEnded = () => {
      setPlayingPhraseIndex(-1);
    };
    const onError = () => {
      setPlayingPhraseIndex(-1);
      setPhrasePlaybackError("Ошибка воспроизведения master audio.");
    };

    audio.addEventListener("timeupdate", onTimeUpdate);
    audio.addEventListener("ended", onEnded);
    audio.addEventListener("error", onError);
    return () => {
      audio.removeEventListener("timeupdate", onTimeUpdate);
      audio.removeEventListener("ended", onEnded);
      audio.removeEventListener("error", onError);
    };
  }, [masterAudioUrl, open]);

  useEffect(() => {
    const audio = masterAudioRef.current;
    if (!audio) return;
    if (String(audio.getAttribute("src") || "").trim() !== masterAudioUrl) {
      audio.pause();
      audio.setAttribute("src", masterAudioUrl || "");
    }
    audio.load();
    console.debug("[SCENARIO MASTER AUDIO PLAYER]", {
      event: "load",
      masterAudioUrl,
      currentSrc: String(audio.currentSrc || "").trim(),
    });
  }, [masterAudioUrl]);

  const resolveSceneAudioSliceStatus = (scene) => {
    const rawStatus = String(scene?.audioSliceStatus || scene?.extractedAudioStatus || "").trim().toLowerCase();
    if (["loading", "queued", "running"].includes(rawStatus)) return "extracting";
    if (["not_extracted", "extracting", "ready", "error"].includes(rawStatus)) return rawStatus;
    if (String(scene?.audioSliceUrl || scene?.extractedAudioUrl || "").trim()) return "ready";
    return "not_extracted";
  };

  const resolveAudioHeaderBadge = (scene) => {
    const status = resolveSceneAudioSliceStatus(scene);
    if (status === "ready") return "ready";
    if (status === "extracting") return "audio attached";
    return "not extracted";
  };

  const resolveExtractedAudioStatusTone = (scene) => {
    const status = resolveSceneAudioSliceStatus(scene);
    if (status === "ready") return "done";
    if (status === "extracting") return "loading";
    if (status === "error") return "error";
    return "idle";
  };

  const handleExtractSceneAudio = async (scene) => {
    const sceneId = String(scene?.sceneId || "").trim();
    if (!sceneId) return;
    const displayTime = resolveSceneDisplayTime(scene);
    const startSec = Number(scene?.audioSliceStartSec ?? displayTime.startSec ?? 0);
    const endSec = Number(scene?.audioSliceEndSec ?? displayTime.endSec ?? startSec);
    const durationSec = Math.max(0, endSec - startSec);
    onUpdateScene?.(nodeId, sceneId, {
      audioSliceStatus: "loading",
      extractedAudioStatus: "extracting",
      audioSliceDurationSec: durationSec,
      audioSliceExpectedDurationSec: durationSec,
      audioSliceError: "",
      audioSliceLoadError: "",
    });
    try {
      const result = await onExtractSceneAudio?.(nodeId, sceneId);
      const audioSliceUrl = String(result?.audioSliceUrl || result?.extractedAudioUrl || "").trim();
      const masterAudioCandidateUrl = String(masterAudioUrl || "").trim();
      const globalAudioCandidateUrl = String(globalAudioUrl || "").trim();
      const safeAudioCandidateUrl = String(safeAudioData?.audioUrl || "").trim();
      const resolvedToFullTrack = Boolean(
        audioSliceUrl
        && (
          (masterAudioCandidateUrl && audioSliceUrl === masterAudioCandidateUrl)
          || (globalAudioCandidateUrl && audioSliceUrl === globalAudioCandidateUrl)
          || (safeAudioCandidateUrl && audioSliceUrl === safeAudioCandidateUrl)
        )
      );
      if (!audioSliceUrl) {
        onUpdateScene?.(nodeId, sceneId, {
          audioSliceStatus: "error",
          extractedAudioStatus: "error",
          audioSliceUrl: "",
          extractedAudioUrl: "",
          audioSliceActualDurationSec: null,
          extractedAudioDurationSec: null,
          audioSliceError: "Не найден источник для audio slice",
          audioSliceLoadError: "Не найден источник для audio slice",
        });
        return;
      }
      if (resolvedToFullTrack) {
        onUpdateScene?.(nodeId, sceneId, {
          audioSliceStatus: "error",
          extractedAudioStatus: "error",
          audioSliceUrl: "",
          extractedAudioUrl: "",
          audioSliceActualDurationSec: null,
          extractedAudioDurationSec: null,
          audioSliceError: "audio_slice_resolved_to_full_track",
          audioSliceLoadError: "audio_slice_resolved_to_full_track",
        });
        return {
          audioSliceUrl,
          audioSliceDurationSec: Number(result?.audioSliceDurationSec ?? result?.extractedAudioDurationSec ?? durationSec),
          audioSliceStatus: "error",
          audioSliceError: "audio_slice_resolved_to_full_track",
        };
      }
      onUpdateScene?.(nodeId, sceneId, {
        audioSliceUrl,
        audioSliceStatus: "ready",
        extractedAudioStatus: "ready",
        extractedAudioUrl: audioSliceUrl,
        audioSliceDurationSec: Number(result?.audioSliceDurationSec ?? result?.extractedAudioDurationSec ?? durationSec),
        extractedAudioDurationSec: Number(result?.audioSliceDurationSec ?? result?.extractedAudioDurationSec ?? durationSec),
        audioSliceExpectedDurationSec: durationSec,
        audioSliceError: "",
        audioSliceLoadError: "",
      });
      return {
        audioSliceUrl,
        audioSliceDurationSec: Number(result?.audioSliceDurationSec ?? result?.extractedAudioDurationSec ?? durationSec),
        audioSliceStatus: "ready",
      };
    } catch (error) {
      onUpdateScene?.(nodeId, sceneId, {
        audioSliceStatus: "error",
        extractedAudioStatus: "error",
        audioSliceUrl: "",
        extractedAudioUrl: "",
        audioSliceActualDurationSec: null,
        extractedAudioDurationSec: null,
        audioSliceError: String(error?.message || "Не удалось изъять аудио"),
        audioSliceLoadError: String(error?.message || "Не удалось изъять аудио"),
      });
      throw error;
    }
  };

  const imageStatus = resolveBlockStatus({ runtimeStatus: selectedRuntime?.imageStatus, assetUrl: selectedScene?.imageUrl });
  const startFrameStatus = resolveBlockStatus({
    runtimeStatus: selectedRuntime?.startFrameStatus || selectedScene?.startFrameStatus || selectedRuntime?.imageStatus || selectedScene?.imageStatus,
    assetUrl: selectedScene?.startImageUrl || selectedScene?.startFrameImageUrl || selectedScene?.startFramePreviewUrl || selectedScene?.imageUrl,
  });
  const endFrameStatus = resolveBlockStatus({
    runtimeStatus: selectedRuntime?.endFrameStatus || selectedScene?.endFrameStatus || selectedRuntime?.imageStatus || selectedScene?.imageStatus,
    assetUrl: selectedScene?.endImageUrl || selectedScene?.endFrameImageUrl || selectedScene?.endFramePreviewUrl,
  });
  const videoStatus = resolveBlockStatus({ runtimeStatus: selectedRuntime?.videoStatus || selectedScene?.videoStatus, assetUrl: selectedScene?.videoUrl });
  const musicStatus = resolveBlockStatus({ runtimeStatus: safeAudioData?.musicStatus, assetUrl: safeAudioData?.musicUrl });
  const isBgAudioSelected = activeSelectionType === "bg_audio";
  const sceneNeedsTwoFrames = isFirstLastScene(selectedScene);
  const isFirstLastVideoMode = sceneNeedsTwoFrames;
  const derivedFramePrompts = deriveFirstLastFramePrompts(selectedScene || {});
  const startFramePromptValue = String(selectedScene?.startFramePromptRu || selectedScene?.startFramePrompt || derivedFramePrompts.start || "");
  const endFramePromptValue = String(selectedScene?.endFramePromptRu || selectedScene?.endFramePrompt || derivedFramePrompts.end || "");
  const previewSources = resolveScenePreviewSources(selectedScene || {});
  const sourceImageUrl = previewSources.resolvedPreviewSrc;
  const startFrameSourceUrl = previewSources.resolvedStartPreviewSrc;
  const endFrameSourceUrl = previewSources.resolvedEndPreviewSrc;
  const sceneVideoUrl = String(selectedScene?.videoUrl || "").trim();
  const hasSceneVideo = Boolean(sceneVideoUrl);
  const sceneAudioSliceUrl = String(selectedScene?.audioSliceUrl || selectedScene?.extractedAudioUrl || "").trim();
  const sceneAudioSliceStatus = resolveSceneAudioSliceStatus(selectedScene);
  const sceneAudioDurationSec = Number(
    selectedScene?.audioSliceDurationSec
    ?? selectedScene?.audioSliceActualDurationSec
    ?? selectedScene?.audioSliceExpectedDurationSec
    ?? selectedScene?.extractedAudioDurationSec
    ?? Math.max(
      0,
      Number(selectedScene?.audioSliceEndSec ?? selectedDisplayTime.endSec ?? 0)
      - Number(selectedScene?.audioSliceStartSec ?? selectedDisplayTime.startSec ?? 0)
    )
  );
  const uiRouteMeta = resolveUiRoute(selectedScene || {});
  const sceneFinalRoute = uiRouteMeta.finalRoute;
  const sceneLipSync = sceneFinalRoute === "lip_sync_music" || Boolean(selectedScene?.isLipSync ?? selectedScene?.lipSync);
  const uiLipsyncSource = String(selectedScene?.uiLipsyncSource || (sceneFinalRoute === "lip_sync_music" ? "route" : (sceneLipSync ? "state" : "legacy")));
  const uiLipsyncValue = sceneLipSync ? "true" : "false";
  const lipSyncAudioMissing = sceneLipSync && !sceneAudioSliceUrl;
  const bgMusicSource = resolveMusicSource(safeAudioData);
  const musicPromptSourceKind = String(safeAudioData?.musicPromptSourceKind || "").trim().toLowerCase() || "empty";
  const realMusicPromptText = String(
    safeAudioData?.globalMusicPrompt
    || safeAudioData?.musicPromptSourceText
    || safeAudioData?.musicPromptRu
    || safeAudioData?.musicPromptEn
    || "",
  ).trim();
  const fallbackMusicPrompt = String(safeAudioData?.fallbackMusicPrompt || "").trim();
  const musicPromptSourceText = musicPromptSourceKind === "real"
    ? realMusicPromptText
    : musicPromptSourceKind === "fallback"
      ? fallbackMusicPrompt
      : "";
  const globalMusicPrompt = String(musicPromptSourceText).trim();
  const hasBgMusicPrompt = Boolean(globalMusicPrompt);
  const hasBgMusic = Boolean(String(safeAudioData?.musicUrl || "").trim());
  const modeBadge = useMemo(() => resolveScenarioModeBadge(scenarioMode), [scenarioMode]);

  useEffect(() => {
    if (!open) return;
    console.debug("[SCENARIO MODE BADGE]", {
      nodeId: String(nodeId || ""),
      resolvedMode: modeBadge.resolvedMode,
      displayLabel: modeBadge.displayLabel,
    });
  }, [modeBadge.displayLabel, modeBadge.resolvedMode, nodeId, open]);

  useEffect(() => {
    if (!CLIP_TRACE_SCENARIO_EDITOR_DEBUG) return;
    console.debug("[SCENARIO EDITOR PREVIEW SRC FINAL]", {
      sceneId: selectedSceneId,
      single: sourceImageUrl,
      start: startFrameSourceUrl,
      end: endFrameSourceUrl,
    });
  }, [selectedSceneId, sourceImageUrl, startFrameSourceUrl, endFrameSourceUrl]);
  useEffect(() => {
    if (!open || !selectedScene) return;
    console.debug("[SCENARIO UI ROUTE TRACE]", {
      sceneId: selectedSceneId,
      ui_route_source: uiRouteMeta.source || selectedScene?.uiRouteSource || "legacy",
      ui_route_value: uiRouteMeta.value || selectedScene?.uiRouteValue || sceneFinalRoute,
      ui_lipsync_source: uiLipsyncSource,
      ui_lipsync_value: uiLipsyncValue,
    });
  }, [open, sceneFinalRoute, selectedScene, selectedSceneId, uiLipsyncSource, uiLipsyncValue, uiRouteMeta.source, uiRouteMeta.value]);
  const usesBgMusicInMontage = hasBgMusic && Boolean(safeAudioData?.useInMontage);
  const bgMusicFileName = String(
    safeAudioData?.fileName
    || safeAudioData?.musicName
    || (bgMusicSource === "generated" && String(safeAudioData?.musicUrl || "").trim() ? "generated track" : "")
    || "",
  ).trim();
  const bgAudioStatusLabel = hasBgMusic ? "audio: есть" : "audio: нет";
  const bgMontageStatusLabel = hasBgMusic ? `монтаж: ${usesBgMusicInMontage ? "да" : "нет"}` : "";
  const bgSourceStatusLabel = hasBgMusic && bgMusicSource !== "none" ? `source: ${bgMusicSource}` : "";
  const bgPromptStatusLabel = hasBgMusicPrompt ? "prompt: есть" : "prompt: нет";
  const editorPromptVisible = hasBgMusicPrompt;
  const musicPromptSourceLabel = musicPromptSourceKind === "real"
    ? "generated music prompt"
    : musicPromptSourceKind === "fallback"
      ? "fallback music prompt (derived from mood/style/pacing)"
      : "music prompt not provided";

  useEffect(() => {
    if (!CLIP_TRACE_SCENARIO_EDITOR_DEBUG) return;
    console.debug("[SCENARIO EDITOR DEBUG]", {
      selectionType: activeSelectionType,
      selectionId: activeSelectionId,
      isBgAudioSelected,
      selectedSceneId,
      sceneNeedsTwoFrames,
      lipSync: sceneLipSync,
      audioSlicePresent: Boolean(sceneAudioSliceUrl),
    });
  }, [activeSelectionId, activeSelectionType, isBgAudioSelected, sceneAudioSliceUrl, sceneLipSync, sceneNeedsTwoFrames, selectedSceneId]);

  const handleUploadBgMusicClick = () => {
    bgMusicUploadRef.current?.click();
  };

  const handleUploadBgMusicFile = (event) => {
    const [file] = Array.from(event?.target?.files || []);
    if (!file) return;
    const fileUrl = URL.createObjectURL(file);
    onUpdateMusic?.(nodeId, {
      musicUrl: fileUrl,
      fileName: file.name,
      musicSource: "uploaded",
      musicStatus: "done",
    });
    event.target.value = "";
  };

  const copyTextToClipboard = async (text) => {
    const payload = String(text || "");
    if (!payload.trim()) return false;
    try {
      await navigator.clipboard.writeText(payload);
      return true;
    } catch (error) {
      const fallback = document.createElement("textarea");
      fallback.value = payload;
      fallback.setAttribute("readonly", "");
      fallback.style.position = "fixed";
      fallback.style.top = "-1000px";
      document.body.appendChild(fallback);
      fallback.select();
      const copied = document.execCommand("copy");
      document.body.removeChild(fallback);
      return copied;
    }
  };

  const notify = (detail) => {
    try {
      window.dispatchEvent(new CustomEvent("ps:notify", { detail }));
    } catch {
      // ignore
    }
  };

  const formatSceneForCopy = (scene = {}, idx = 0) => {
    const sceneId = String(scene?.sceneId || `S${idx + 1}`).trim() || `S${idx + 1}`;
    const { startSec: t0, endSec: t1 } = resolveSceneDisplayTime(scene);
    const duration = safeSceneDuration(scene);
    const warnings = Array.isArray(scene?.contractWarnings)
      ? scene.contractWarnings.map((warning) => String(warning?.label || warning?.code || "").trim()).filter(Boolean)
      : [];
    return [
      `SCENE ${sceneId}`,
      `t0: ${fmtSec(t0)} / t1: ${fmtSec(t1)} / duration: ${fmtSec(duration)}s`,
      `lyric: ${toPrintable(scene?.localPhrase || scene?.lyricText)}`,
      `summary: ${toPrintable(scene?.summaryRu || scene?.summaryEn)}`,
      `sceneGoal: ${toPrintable(scene?.sceneGoalRu || scene?.sceneGoalEn)}`,
      `sceneMeaning: ${toPrintable(scene?.sceneMeaningRu || scene?.sceneMeaningEn || scene?.sceneMeaning)}`,
      `actors: ${toPrintable(scene?.actors)}`,
      `primaryRole: ${toPrintable(scene?.primaryRole)}`,
      `secondaryRoles: ${toPrintable(scene?.secondaryRoles)}`,
      `mustAppear: ${toPrintable(scene?.mustAppear)}`,
      `lipSync: ${String(Boolean(scene?.lipSync))}`,
      `audioSliceUrl: ${toPrintable(scene?.audioSliceUrl)}`,
      `imagePromptRu: ${toPrintable(scene?.imagePromptRu || scene?.imagePromptEn)}`,
      `videoPromptRu: ${toPrintable(scene?.videoPromptRu || scene?.videoPromptEn)}`,
      `warnings: ${warnings.length ? warnings.join("; ") : "—"}`,
    ].join("\n");
  };

  const formatAllScenesForCopy = () => normalizedScenes.map((scene, idx) => formatSceneForCopy(scene, idx)).join("\n\n");

  const formatPromptsForCopy = () => normalizedScenes.map((scene, idx) => {
    const sceneId = String(scene?.sceneId || `S${idx + 1}`).trim() || `S${idx + 1}`;
    return [
      `SCENE ${sceneId}`,
      `imagePromptRu: ${toPrintable(scene?.imagePromptRu || scene?.imagePromptEn)}`,
      `videoPromptRu: ${toPrintable(scene?.videoPromptRu || scene?.videoPromptEn)}`,
    ].join("\n");
  }).join("\n\n");

  const formatRawForCopy = () => JSON.stringify({
    scenes: normalizedScenes,
    selectedSceneId,
    selectedSceneRuntime: selectedRuntime,
    audioData: safeAudioData,
  }, null, 2);
  const scenarioRawJson = useMemo(() => (
    activeTab === "debug" ? formatRawForCopy() : ""
  ), [activeTab, normalizedScenes, safeAudioData, selectedRuntime, selectedSceneId]);

  const handleCopyRawJson = async () => {
    const didCopy = await copyTextToClipboard(formatRawForCopy());
    if (!didCopy) return;
    notify({ type: "success", message: "JSON скопирован" });
  };

  const tabContent = (() => {
    if (activeTab === "scenario") {
      return (
        <div className="clipSB_scenarioEditorTabBody">
          <div className="clipSB_scenarioEditorBtnRow clipSB_scenarioEditorCopyRow">
            <button
              className="clipSB_btn clipSB_btnSecondary"
              type="button"
              onMouseDown={stopNodeDragEvent}
              onPointerDown={stopNodeDragEvent}
              onClick={() => copyTextToClipboard(formatSceneForCopy(selectedScene || normalizedScenes[0] || {}, safeIndex >= 0 ? safeIndex : 0))}
            >
              Копировать сцену
            </button>
            <button className="clipSB_btn clipSB_btnSecondary" type="button" onMouseDown={stopNodeDragEvent} onPointerDown={stopNodeDragEvent} onClick={() => copyTextToClipboard(formatAllScenesForCopy())}>Копировать весь сценарий</button>
            <button className="clipSB_btn clipSB_btnSecondary" type="button" onMouseDown={stopNodeDragEvent} onPointerDown={stopNodeDragEvent} onClick={handleCopyRawJson}>Копировать raw JSON</button>
            <button className="clipSB_btn clipSB_btnSecondary" type="button" onMouseDown={stopNodeDragEvent} onPointerDown={stopNodeDragEvent} onClick={() => copyTextToClipboard(formatPromptsForCopy())}>Копировать prompts</button>
          </div>
          <div className="clipSB_storyboardKv"><span>Сцен</span><strong>{normalizedScenes.length}</strong></div>
          {normalizedScenes.map((scene, idx) => {
            const sceneId = String(scene?.sceneId || `S${idx + 1}`);
            const displayTime = resolveSceneDisplayTime(scene);
            return (
              <details key={`contract-${sceneId}-${idx}`} style={{ marginBottom: 10 }}>
                <summary
                  className="clipSB_copySelectable nodrag nopan"
                  onMouseDown={stopNodeDragEvent}
                  onPointerDown={stopNodeDragEvent}
                >
                  {sceneId} · {fmtSec(displayTime.startSec)}–{fmtSec(displayTime.endSec)}
                </summary>
                <ContractField label="sceneId" value={sceneId} />
                <ContractField label="t0/t1" value={`${fmtSec(displayTime.startSec)} / ${fmtSec(displayTime.endSec)}`} />
                {isLongText(scene?.localPhrase || scene?.lyricText)
                  ? <ScenarioReadonlyTextField label="lyric text" value={scene?.localPhrase || scene?.lyricText} minRows={2} />
                  : <ContractField label="lyric text" value={scene?.localPhrase || scene?.lyricText} />}
                <ScenarioReadonlyTextField label="summary" value={scene?.summaryRu || scene?.summaryEn} minRows={3} />
                <ScenarioReadonlyTextField label="sceneGoal" value={scene?.sceneGoalRu || scene?.sceneGoalEn} minRows={3} />
                <ScenarioReadonlyTextField label="sceneMeaning" value={scene?.sceneMeaningRu || scene?.sceneMeaningEn || scene?.sceneMeaning} minRows={3} />
                <ContractField label="actors" value={scene?.actors || []} />
                <ContractField label="primaryRole" value={scene?.primaryRole} />
                <ContractField label="secondaryRoles" value={scene?.secondaryRoles || []} />
                <ContractField label="mustAppear" value={scene?.mustAppear || []} />
                <ScenarioReadonlyTextField label="imagePrompt" value={scene?.imagePromptRu || scene?.imagePromptEn} minRows={4} />
                <ScenarioReadonlyTextField label="videoPrompt" value={scene?.videoPromptRu || scene?.videoPromptEn} minRows={4} />
                <ContractField label="lipSync" value={String(Boolean(scene?.lipSync))} />
                <ContractField label="audioSliceUrl" value={scene?.audioSliceUrl} />
                <ContractField label="renderMode / ltxMode / model" value={`${scene?.renderMode || "—"} / ${scene?.ltxMode || "—"} / ${scene?.resolvedModelKey || "—"}`} />
                <div className="clipSB_scenarioEditorBadgeRow">
                  {renderContractWarnings(scene)}
                </div>
              </details>
            );
          })}
        </div>
      );
    }
    if (activeTab === "context") {
      return (
        <div className="clipSB_scenarioEditorTabBody">
          <div className="clipSB_storyboardKv"><span>locationRu</span><strong>{selectedScene?.locationRu || "—"}</strong></div>
          <div className="clipSB_storyboardKv"><span>emotionRu</span><strong>{selectedScene?.emotionRu || "—"}</strong></div>
          <div className="clipSB_storyboardKv"><span>duration</span><strong>{selectedScene ? `${fmtSec(safeSceneDuration(selectedScene))}s` : "—"}</strong></div>
        </div>
      );
    }
    if (activeTab === "actors") {
      return (
        <div className="clipSB_scenarioEditorTabBody">
          {Array.isArray(selectedScene?.actors) && selectedScene.actors.length ? selectedScene.actors.map((actor, idx) => (
            <div key={`${actor}-${idx}`} className="clipSB_scenarioEditorSimpleRow">• {actor}</div>
          )) : <div className="clipSB_hint">Актеры не указаны.</div>}
        </div>
      );
    }
    if (activeTab === "phrases") {
      return (
        <div className="clipSB_scenarioEditorPhraseList">
          {phrasesForUi.map((phrase, idx) => {
            const phraseSceneId = resolvePhraseSceneId(phrase, idx);
            const isActive = idx === selectedPhraseIndex;
            const isPlaying = idx === playingPhraseIndex;
            return (
              <div
                key={`${phraseSceneId || idx}-${idx}`}
                className={`clipSB_scenarioEditorPhraseItem ${isActive ? "isActive" : ""} ${isPlaying ? "isPlaying" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => handleSelectPhrase(phrase, idx)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    handleSelectPhrase(phrase, idx);
                  }
                }}
              >
                  <div className="clipSB_scenarioEditorPhraseMain">
                    <div className="clipSB_scenarioEditorPhraseMeta">[{fmtSec(phrase.startSec)} - {fmtSec(phrase.endSec)}]</div>
                  <div
                    className="clipSB_scenarioEditorPhraseText clipSB_copySelectable nodrag nopan"
                    onMouseDown={stopNodeDragEvent}
                    onPointerDown={stopNodeDragEvent}
                  >
                    {phrase.text || "—"}
                  </div>
                </div>
                <button
                  className="clipSB_btn clipSB_btnSecondary clipSB_scenarioEditorPhraseJumpBtn"
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    handleSelectPhrase(phrase, idx);
                    jumpToPhrase(phrase, idx);
                  }}
                >
                  ▶ Перемотать
                </button>
              </div>
            );
          })}
          {phrasePlaybackError ? <div className="clipSB_hint" style={{ color: "#ffb066" }}>{phrasePlaybackError}</div> : null}
          {!masterAudioUrl ? <div className="clipSB_hint">Прослушивание фраз недоступно: master audio отсутствует.</div> : null}
        </div>
      );
    }
    return (
      <div className="clipSB_scenarioEditorTabBody">
        <div
          className="clipSB_scenarioJsonReadonlyWrap nodrag nopan nowheel"
          onMouseDown={stopNodeDragEvent}
          onPointerDown={stopNodeDragEvent}
        >
          <button
            className="clipSB_scenarioJsonCopyBtn nodrag nopan nowheel"
            type="button"
            aria-label="Копировать JSON"
            title="Копировать JSON"
            onMouseDown={stopNodeDragEvent}
            onPointerDown={stopNodeDragEvent}
            onClick={handleCopyRawJson}
          >
            📋
          </button>
          <textarea
            className="clipSB_scenarioJsonReadonly nodrag nopan nowheel"
            readOnly
            value={scenarioRawJson}
            onMouseDown={stopNodeDragEvent}
            onPointerDown={stopNodeDragEvent}
          />
        </div>
      </div>
    );
  })();

  if (!open) return null;

  return (
    <div className="clipSB_scenarioOverlay" onClick={onClose}>
      <div
        className="clipSB_scenarioPanel clipSB_scenarioEditorPanel nodrag nopan nowheel"
        onClick={(event) => event.stopPropagation()}
        onMouseDown={stopNodeDragEvent}
        onPointerDown={stopNodeDragEvent}
      >
        <div className="clipSB_scenarioHeader">
          <div>
            <div className="clipSB_scenarioTitle">Scenario Storyboard Editor</div>
            <div className="clipSB_scenarioMeta" style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
              <span>Сцен: {normalizedScenes.length}</span>
              <span
                style={{
                  color: modeBadge.color,
                  background: modeBadge.background,
                  border: `1px solid ${modeBadge.color}`,
                  borderRadius: 999,
                  padding: "2px 8px",
                  fontWeight: 700,
                }}
              >
                Режим: {modeBadge.displayLabel}
              </span>
            </div>
          </div>
          <button className="clipSB_iconBtn" onClick={onClose} type="button">×</button>
        </div>

        {masterAudioUrl ? <audio key={masterAudioUrl} ref={masterAudioRef} src={masterAudioUrl} preload="metadata" style={{ display: "none" }} /> : null}

        <div className="clipSB_scenarioEditorTopTabs">
          <div className="clipSB_scenarioEditorTabsRow">
            {TOP_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                className={`clipSB_scenarioEditorTabBtn ${activeTab === tab.id ? "isActive" : ""}`}
                onClick={() => {
                  setActiveTab(tab.id);
                  setInfoModalOpen(true);
                }}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
        {infoModalOpen ? (
          <div className="clipSB_scenarioEditorInfoModalOverlay" onClick={() => setInfoModalOpen(false)}>
            <div
              className="clipSB_scenarioEditorInfoModal nodrag nopan nowheel"
              onClick={(event) => event.stopPropagation()}
              onMouseDown={stopNodeDragEvent}
              onPointerDown={stopNodeDragEvent}
            >
              <div className="clipSB_scenarioEditorInfoModalHeader">
                <div className="clipSB_scenarioEditorInfoModalTitle">{TOP_TABS.find((tab) => tab.id === activeTab)?.label || "Инфо"}</div>
                <button className="clipSB_iconBtn" type="button" onClick={() => setInfoModalOpen(false)}>×</button>
              </div>
              <div className="clipSB_scenarioEditorInfoModalBody nodrag nopan nowheel" onMouseDown={stopNodeDragEvent} onPointerDown={stopNodeDragEvent}>
                {tabContent}
              </div>
            </div>
          </div>
        ) : null}

        <div className="clipSB_scenarioBody clipSB_scenarioEditorBody">
          <div className="clipSB_scenarioList clipSB_scenarioEditorSceneList">
            <button
              className={`clipSB_scenarioItem clipSB_scenarioBgAudioItem ${isBgAudioSelected ? "isActive" : ""}`}
              type="button"
              onClick={() => {
                setActiveSelectionType("bg_audio");
                setActiveSelectionId(BG_AUDIO_ITEM_ID);
              }}
            >
              <div className="clipSB_scenarioItemTop">
                <div className="clipSB_storyboardSceneId">[ АУДИО ФОН ]</div>
              </div>
              <div className="clipSB_scenarioItemText">Глобальный музыкальный слой для всего ролика.</div>
              <div className="clipSB_scenarioEditorBadgeRow">
                <span className="clipSB_tag">bg audio</span>
                <span className="clipSB_tag">{bgAudioStatusLabel}</span>
                <span className="clipSB_tag">{bgPromptStatusLabel}</span>
                {bgMontageStatusLabel ? <span className="clipSB_tag">{bgMontageStatusLabel}</span> : null}
                {bgSourceStatusLabel ? <span className="clipSB_tag">{bgSourceStatusLabel}</span> : null}
              </div>
              <div className="clipSB_small clipSB_scenarioBgAudioMeta">глобальный слой</div>
            </button>

            {normalizedScenes.map((scene, idx) => {
              const sceneId = String(scene?.sceneId || `S${idx + 1}`);
              const displayTime = resolveSceneDisplayTime(scene);
              const runtime = safeGeneration[sceneId] && typeof safeGeneration[sceneId] === "object" ? safeGeneration[sceneId] : {};
              const status = resolveBlockStatus({ runtimeStatus: runtime?.status || runtime?.videoStatus || runtime?.imageStatus, assetUrl: scene?.videoUrl || scene?.imageUrl });
              return (
                <button
                  key={sceneId}
                  className={`clipSB_scenarioItem ${activeSelectionType === "scene" && sceneId === activeSelectionId ? "isActive" : ""}`}
                  type="button"
                  onClick={() => {
                    setActiveSelectionType("scene");
                    setActiveSelectionId(sceneId);
                  }}
                >
                  <div className="clipSB_scenarioItemTop">
                    <div className="clipSB_storyboardSceneId">[ {sceneId} · {fmtSec(displayTime.startSec)}–{fmtSec(displayTime.endSec)} ]</div>
                  </div>
                  <div className="clipSB_scenarioItemText">{scene?.summaryRu || scene?.localPhrase || "—"}</div>
                  <div className="clipSB_scenarioEditorBadgeRow">
                    {sceneBadges(scene).map((badge) => <span key={`${sceneId}-${badge}`} className="clipSB_tag">{badge}</span>)}
                    <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${status}`}>{status}</span>
                  </div>
                </button>
              );
            })}
          </div>

          <div className="clipSB_scenarioEdit clipSB_scenarioEditorWork">
            {isBgAudioSelected ? (
              <div className="clipSB_scenarioEditorBlock">
                <div className="clipSB_scenarioEditorBlockHead">
                  <h4>ФОНОВОЕ АУДИО</h4>
                  <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${musicStatus}`}>{musicStatus}</span>
                </div>
                <div className="clipSB_small clipSB_scenarioBgAudioMasterMeta">master audio · duration: {fmtSec(safeAudioData?.durationSec)}s</div>
                <div className="clipSB_scenarioBgAudioMasterAudio">
                  <details>
                    <summary>master audio source</summary>
                    {safeAudioData?.audioUrl ? (
                      <audio controls className="clipSB_audioPlayer" src={safeAudioData.audioUrl} />
                    ) : (
                      <div className="clipSB_hint">Master audio отсутствует.</div>
                    )}
                  </details>
                </div>
                <div className="clipSB_scenarioBgAudioGrid">
                  <div className="clipSB_scenarioBgAudioCol clipSB_scenarioBgAudioResult">
                    <h5>Результат аудио</h5>
                    <div className="clipSB_scenarioBgAudioMeta">
                      <div className="clipSB_storyboardKv"><span>Источник</span><strong>{bgMusicSource}</strong></div>
                      <div className="clipSB_storyboardKv"><span>Файл</span><strong>{bgMusicFileName || "Файл не выбран"}</strong></div>
                    </div>
                    <div className="clipSB_scenarioBgAudioPlayerWrap">
                      {safeAudioData?.musicUrl ? (
                        <audio controls className="clipSB_audioPlayer" src={safeAudioData.musicUrl} />
                      ) : (
                        <div className="clipSB_hint">Фоновое аудио пока не готово</div>
                      )}
                    </div>
                    <div className="clipSB_scenarioEditorBtnRow">
                      <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={handleUploadBgMusicClick}>Загрузить свою музыку</button>
                      <input
                        ref={bgMusicUploadRef}
                        type="file"
                        accept="audio/*,.mp3,.wav,.ogg,.m4a"
                        style={{ display: "none" }}
                        onChange={handleUploadBgMusicFile}
                      />
                    </div>
                    <button
                      className={`clipSB_bgAudioToggle ${usesBgMusicInMontage ? "isActive" : ""}`}
                      type="button"
                      aria-pressed={usesBgMusicInMontage}
                      onClick={() => onUpdateMusic?.(nodeId, { useInMontage: !Boolean(safeAudioData?.useInMontage) })}
                    >
                      использовать в монтаже
                    </button>
                    <div className="clipSB_storyboardKv"><span>Статус</span><strong>{musicStatus}</strong></div>
                  </div>

                  <div className="clipSB_scenarioBgAudioCol clipSB_scenarioBgAudioGenerateCol">
                    <h5>Prompt / генерация</h5>
                    <textarea
                      className="clipSB_textarea clipSB_scenarioBgAudioPrompt"
                      rows={3}
                      value={globalMusicPrompt}
                      onChange={(event) => onUpdateMusic?.(nodeId, {
                        musicPromptRu: event.target.value,
                        globalMusicPrompt: event.target.value,
                        musicPromptSourceText: event.target.value,
                        musicPromptSourceKind: event.target.value.trim() ? "real" : "empty",
                      })}
                      placeholder="Сценарист ещё не предложил фоновую музыку"
                    />
                    <div className="clipSB_hint" style={{ marginTop: 6 }}>
                      {musicPromptSourceLabel}
                    </div>
                    {musicPromptSourceKind === "empty" ? (
                      <div className="clipSB_hint">music prompt not provided</div>
                    ) : null}
                    {musicPromptSourceKind === "fallback" && fallbackMusicPrompt ? (
                      <div className="clipSB_hint">derived fallback length: {fallbackMusicPrompt.length}</div>
                    ) : null}
                    <details>
                      <summary>EN</summary>
                      <textarea
                        className="clipSB_textarea"
                        rows={2}
                        value={String(safeAudioData?.musicPromptEn || "")}
                        onChange={(event) => onUpdateMusic?.(nodeId, { musicPromptEn: event.target.value })}
                        placeholder="musicPromptEn"
                      />
                    </details>
                    <div className="clipSB_scenarioEditorBtnRow">
                      <button className="clipSB_btn" type="button" onClick={() => onGenerateMusic?.(nodeId)} disabled={musicStatus === "loading"}>
                        {musicStatus === "loading" ? "Генерирую..." : "Сгенерировать музыку"}
                      </button>
                      <button
                        className="clipSB_btn clipSB_btnSecondary"
                        type="button"
                        onClick={() => onUpdateMusic?.(nodeId, { musicUrl: "", fileName: "", musicSource: "none", musicStatus: "idle" })}
                      >
                        Удалить
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            ) : !selectedScene ? <div className="clipSB_empty">Нет выбранной сцены</div> : (
              <>
                <div className="clipSB_scenarioEditorSceneTitle">Сцена {selectedSceneId}</div>

                <div className="clipSB_scenarioEditorBlock">
                  <div className="clipSB_scenarioEditorBlockHead">
                    <h4>1. IMAGE</h4>
                  </div>
                  {!sceneNeedsTwoFrames ? (
                    <>
                      <div className="clipSB_scenarioEditorImageBody clipSB_scenarioEditorImageBodyMain">
                        <div className="clipSB_scenarioEditorImageLeft clipSB_scenarioEditorImageLeftMain">
                          <textarea
                            className="clipSB_textarea clipSB_scenarioEditorImagePromptTextarea"
                            rows={6}
                            value={String(selectedScene?.imagePromptRu || "")}
                            onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { imagePromptRu: event.target.value })}
                            placeholder="imagePromptRu"
                          />
                          <details className="clipSB_scenarioEditorImageEn">
                            <summary>EN</summary>
                            <textarea
                              className="clipSB_textarea"
                              rows={2}
                              value={String(selectedScene?.imagePromptEn || "")}
                              onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { imagePromptEn: event.target.value })}
                              placeholder="imagePromptEn"
                            />
                          </details>
                          <div className="clipSB_scenarioEditorBtnRow clipSB_scenarioEditorImageBtnRow">
                            <button
                              className="clipSB_btn"
                              type="button"
                              onClick={() => {
                                console.warn("[SCENARIO STORYBOARD EDITOR] image_generate_click", {
                                  nodeId: String(nodeId || ""),
                                  selectedSceneId,
                                  activeTab,
                                  disabled: imageStatus === "loading",
                                });
                                onGenerateScene?.(nodeId, selectedSceneId, "image", generateMeta);
                              }}
                              disabled={imageStatus === "loading"}
                            >
                              Создать изображение
                            </button>
                            <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { imageUrl: "", imageStatus: "idle" })}>Удалить</button>
                          </div>
                        </div>
                        <div className="clipSB_scenarioEditorImageRight clipSB_scenarioEditorImageRightMain">
                          <div className="clipSB_scenarioEditorBlockHead">
                            <h4>IMAGE</h4>
                            <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${imageStatus}`}>{imageStatus}</span>
                          </div>
                          <div className={`clipSB_scenarioEditorImagePreviewWrap${sourceImageUrl ? "" : " clipSB_scenarioEditorImagePreviewWrap--empty"}`}>
                            {sourceImageUrl ? <img className="clipSB_scenarioEditorImagePreview" src={sourceImageUrl} alt={`scene-${selectedSceneId}-image`} /> : (
                              <div className="clipSB_scenarioEditorPreviewPlaceholder" role="status" aria-live="polite">
                                <div className="clipSB_scenarioEditorPreviewPlaceholderIcon" aria-hidden="true">🖼️</div>
                                <div>Изображение сцены пока не создано</div>
                              </div>
                            )}
                          </div>
                        </div>
                      </div>
                    </>
                  ) : (
                    <div className="clipSB_scenarioEditorImageSubBlock clipSB_scenarioEditorFrameBlock">
                      <div className="clipSB_scenarioEditorBlockHead">
                        <h4>КАДРЫ СЦЕНЫ</h4>
                      </div>
                      <div className="clipSB_scenarioEditorFrameCards">
                        <div className="clipSB_scenarioEditorFrameCard">
                          <div className="clipSB_scenarioEditorBlockHead">
                            <h5>ПЕРВЫЙ КАДР</h5>
                            <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${startFrameStatus}`}>{startFrameStatus}</span>
                          </div>
                          <textarea className="clipSB_textarea" rows={3} value={startFramePromptValue} onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { startFramePromptRu: event.target.value })} placeholder={derivedFramePrompts.derived ? "Автоподсказка применена из sceneGoal/frameDescription/image+video prompt" : getFramePromptPlaceholder("start")} />
                          <details>
                            <summary>EN</summary>
                            <textarea className="clipSB_textarea" rows={2} value={String(selectedScene?.startFramePromptEn || selectedScene?.startFramePrompt || derivedFramePrompts.start || "")} onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { startFramePromptEn: event.target.value })} placeholder="Opening visual state (first frame)" />
                          </details>
                          <div className="clipSB_scenarioEditorFramePreviewWrap">
                            {startFrameSourceUrl ? (
                              <img className="clipSB_scenarioEditorImagePreview" src={startFrameSourceUrl} alt={`scene-${selectedSceneId}-start-frame`} />
                            ) : <div className="clipSB_hint">preview первого кадра отсутствует</div>}
                          </div>
                          <div className="clipSB_scenarioEditorBtnRow">
                            <button className="clipSB_btn" type="button" onClick={() => onGenerateScene?.(nodeId, selectedSceneId, "start_frame", generateMeta)} disabled={startFrameStatus === "loading"}>Создать изображение</button>
                            <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { startImageUrl: "", startFrameImageUrl: "", startFramePreviewUrl: "", startFrameStatus: "idle" })}>Удалить</button>
                          </div>
                        </div>
                        <div className="clipSB_scenarioEditorFrameCard">
                          <div className="clipSB_scenarioEditorBlockHead">
                            <h5>ПОСЛЕДНИЙ КАДР</h5>
                            <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${endFrameStatus}`}>{endFrameStatus}</span>
                          </div>
                          <textarea className="clipSB_textarea" rows={3} value={endFramePromptValue} onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { endFramePromptRu: event.target.value })} placeholder={derivedFramePrompts.derived ? "Автоподсказка применена из sceneGoal/frameDescription/image+video prompt" : getFramePromptPlaceholder("end")} />
                          <details>
                            <summary>EN</summary>
                            <textarea className="clipSB_textarea" rows={2} value={String(selectedScene?.endFramePromptEn || selectedScene?.endFramePrompt || derivedFramePrompts.end || "")} onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { endFramePromptEn: event.target.value })} placeholder="Changed/final visual state (last frame)" />
                          </details>
                          <div className="clipSB_scenarioEditorFramePreviewWrap">
                            {endFrameSourceUrl ? (
                              <img className="clipSB_scenarioEditorImagePreview" src={endFrameSourceUrl} alt={`scene-${selectedSceneId}-end-frame`} />
                            ) : <div className="clipSB_hint">{isFirstLastVideoMode ? "Последний кадр отсутствует" : "Последний кадр не требуется"}</div>}
                          </div>
                          <div className="clipSB_scenarioEditorBtnRow">
                            <button className="clipSB_btn" type="button" onClick={() => onGenerateScene?.(nodeId, selectedSceneId, "end_frame", generateMeta)} disabled={endFrameStatus === "loading" || !isFirstLastVideoMode}>Создать изображение</button>
                            <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { endImageUrl: "", endFrameImageUrl: "", endFramePreviewUrl: "", endFrameStatus: "idle" })} disabled={!isFirstLastVideoMode}>Удалить</button>
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                <div className="clipSB_scenarioEditorBlock clipSB_sceneAudioBlock">
                  <button className="clipSB_scenarioEditorCollapseHead" type="button" onClick={() => setAudioSceneOpen((prev) => !prev)} aria-expanded={audioSceneOpen}>
                    <h4>2. AUDIO (СЦЕНА)</h4>
                    <div className="clipSB_scenarioEditorCollapseHeadRight">
                      <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${resolveExtractedAudioStatusTone(selectedScene)}`}>
                        {resolveAudioHeaderBadge(selectedScene)}
                      </span>
                      <span className={`clipSB_scenarioEditorChevron ${audioSceneOpen ? "isOpen" : ""}`} aria-hidden="true">⌄</span>
                    </div>
                  </button>
                  {audioSceneOpen ? (
                    <div className="clipSB_scenarioEditorCollapsibleBody clipSB_sceneAudioGrid">
                      <div className="clipSB_sceneAudioCol clipSB_sceneAudioInfoCol">
                        <div className="clipSB_sceneAudioInfoCard">
                          <span>Режим речи</span>
                          <strong>{selectedScene.narrationMode || "—"}</strong>
                        </div>
                        <div className="clipSB_sceneAudioInfoCard">
                          <span>Фраза</span>
                          <strong>{selectedScene.localPhrase || "—"}</strong>
                        </div>
                        <div className="clipSB_sceneAudioInfoCard">
                          <span>Начало</span>
                          <strong>{fmtSec(selectedDisplayTime.startSec)} c</strong>
                        </div>
                        <div className="clipSB_sceneAudioInfoCard">
                          <span>Конец</span>
                          <strong>{fmtSec(selectedDisplayTime.endSec)} c</strong>
                        </div>
                        <div className="clipSB_sceneAudioInfoCard">
                          <span>Длительность</span>
                          <strong>{fmtSec(Math.max(0, Number(selectedDisplayTime.endSec) - Number(selectedDisplayTime.startSec)))} c</strong>
                        </div>
                      </div>

                      <div className="clipSB_sceneAudioCol clipSB_sceneAudioActionCol">
                        <div className="clipSB_scenarioEditorBtnRow">
                          <button
                            className="clipSB_btn"
                            type="button"
                            onClick={() => handleExtractSceneAudio(selectedScene)}
                            disabled={resolveSceneAudioSliceStatus(selectedScene) === "extracting"}
                          >
                            {resolveSceneAudioSliceStatus(selectedScene) === "extracting" ? "Извлекаем..." : "Изъять аудио"}
                          </button>
                        </div>
                        {sceneAudioSliceStatus === "ready" && sceneAudioSliceUrl ? (
                          <div className="clipSB_sceneAudioReadyBox">
                            <audio controls className="clipSB_audioPlayer" src={sceneAudioSliceUrl} />
                            <div className="clipSB_sceneAudioReadyMeta">
                              <span className="clipSB_tag clipSB_tagStatus clipSB_tagStatus--done">audioSlice / ready</span>
                              <span className="clipSB_small">Длительность: {fmtSec(sceneAudioDurationSec)} c</span>
                            </div>
                            <div className="clipSB_sceneAudioLipSyncReady">Готово для lip-sync и sound-enabled scene.</div>
                          </div>
                        ) : (
                          <div className="clipSB_sceneAudioPlaceholder">
                            {sceneLipSync
                              ? "Для lipSync audioSlice будет подготовлен автоматически при «Создать видео» (или можно извлечь вручную здесь)."
                              : "Аудио-кусок сцены ещё не подготовлен"}
                          </div>
                        )}
                        {sceneAudioSliceStatus === "error" ? (
                          <div className="clipSB_hint" style={{ color: "#ff8a8a" }}>{String(selectedScene?.audioSliceError || selectedScene?.extractedAudioError || "Ошибка извлечения аудио")}</div>
                        ) : null}
                      </div>
                    </div>
                  ) : null}
                </div>

                <div className="clipSB_scenarioEditorBlock">
                  <div className="clipSB_scenarioEditorBlockHead">
                    <h4>3. VIDEO</h4>
                    <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${videoStatus}`}>{videoStatus}</span>
                  </div>
                  <div className="clipSB_scenarioEditorVideoBody">
                    <div className="clipSB_scenarioEditorVideoLeft">
                      <textarea
                        className="clipSB_textarea"
                        rows={3}
                        value={String(selectedScene?.videoPromptRu || "")}
                        onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { videoPromptRu: event.target.value })}
                        placeholder="videoPromptRu"
                      />
                      <details>
                        <summary>EN</summary>
                        <textarea
                          className="clipSB_textarea"
                          rows={2}
                          value={String(selectedScene?.videoPromptEn || "")}
                          onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { videoPromptEn: event.target.value })}
                          placeholder="videoPromptEn"
                        />
                      </details>
                      <div className="clipSB_sceneVideoMeta">
                        <div className="clipSB_storyboardKv"><span>renderMode</span><strong>{selectedScene?.renderMode || "—"}</strong></div>
                        <div className="clipSB_storyboardKv"><span>workflow</span><strong>{selectedScene?.resolvedWorkflowKey || selectedScene?.ltxMode || "—"}</strong></div>
                        <div className="clipSB_storyboardKv"><span>ui_route_source</span><strong>{uiRouteMeta.source || selectedScene?.uiRouteSource || "legacy"}</strong></div>
                        <div className="clipSB_storyboardKv"><span>ui_route_value</span><strong>{uiRouteMeta.value || selectedScene?.uiRouteValue || sceneFinalRoute || "—"}</strong></div>
                        <div className="clipSB_storyboardKv"><span>ui_lipsync_source</span><strong>{uiLipsyncSource}</strong></div>
                        <div className="clipSB_storyboardKv"><span>ui_lipsync_value</span><strong>{uiLipsyncValue}</strong></div>
                        <div className="clipSB_storyboardKv"><span>lipSync</span><strong>{sceneLipSync ? "да" : "нет"}</strong></div>
                        <div className="clipSB_storyboardKv"><span>audioSlice</span><strong>{sceneAudioSliceUrl ? "present" : "missing"}</strong></div>
                        {sceneLipSync ? <div className="clipSB_hint clipSB_sceneVideoAudioHint">{sceneAudioSliceUrl ? "Этот audioSlice будет отправлен в video generation." : "Для lipSync audioSlice подготовится автоматически перед генерацией видео."}</div> : null}
                        {sceneAudioSliceUrl ? <audio controls className="clipSB_audioPlayer" src={sceneAudioSliceUrl} /> : null}
                      </div>
                      <div className="clipSB_scenarioEditorBtnRow">
                        <button
                          className="clipSB_btn"
                          type="button"
                          onClick={async () => {
                            const renderMode = String(selectedScene?.renderMode || "");
                            const resolvedWorkflowKey = String(selectedScene?.resolvedWorkflowKey || selectedScene?.ltxMode || "");
                            let preparedAudioSliceUrl = String(selectedScene?.audioSliceUrl || "").trim();
                            const autoPreparedAudio = sceneLipSync && !preparedAudioSliceUrl;
                            if (autoPreparedAudio) {
                              try {
                                const extractResult = await handleExtractSceneAudio(selectedScene);
                                preparedAudioSliceUrl = String(extractResult?.audioSliceUrl || "").trim();
                              } catch {
                                preparedAudioSliceUrl = "";
                              }
                            }
                            const whyBlocked = sceneLipSync && !preparedAudioSliceUrl ? "lip_sync_audio_missing_after_auto_extract" : "";
                            console.debug("[SCENARIO EDITOR VIDEO SEND]", {
                              videoSendRouteTriggered: true,
                              selectedSceneId,
                              lipSync: sceneLipSync,
                              audioSlicePresent: Boolean(preparedAudioSliceUrl || sceneAudioSliceUrl),
                              renderMode,
                              resolvedWorkflowKey,
                              firstFrameUrl: startFrameSourceUrl,
                              lastFrameUrl: endFrameSourceUrl,
                              sourceOfTruthKeys: {
                                firstFrame: ["scene.startImageUrl", "scene.startFrameImageUrl", "scene.startFramePreviewUrl", "scene.imageUrl"],
                                lastFrame: ["scene.endImageUrl", "scene.endFrameImageUrl", "scene.endFramePreviewUrl"],
                              },
                              autoPreparedAudio,
                              whyBlocked,
                            });
                            if (sceneLipSync && !preparedAudioSliceUrl) return;
                            onGenerateScene?.(nodeId, selectedSceneId, "video", generateMeta);
                          }}
                          disabled={videoStatus === "loading"}
                          title={sceneLipSync ? "Для lipSync audioSlice подготовится автоматически перед генерацией" : ""}
                        >
                          Создать видео
                        </button>
                        <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { videoUrl: "", videoStatus: "idle", videoError: "", videoJobId: "" })}>Удалить</button>
                      </div>
                      {lipSyncAudioMissing ? <div className="clipSB_hint" style={{ color: "#ffb066" }}>Для lipSync сцены audioSlice подготовится автоматически при «Создать видео». Если извлечение не удастся, покажем ошибку.</div> : null}
                    </div>
                    <div className="clipSB_scenarioEditorVideoRight clipSB_scenarioEditorVideoPreviewCol">
                      <div className={`clipSB_scenarioEditorImagePreviewWrap clipSB_scenarioEditorVideoPreviewBox${hasSceneVideo || isFirstLastVideoMode ? "" : " clipSB_scenarioEditorImagePreviewWrap--empty"}`}>
                        {hasSceneVideo ? (
                          <video className="clipSB_scenarioEditorVideoPreview" controls src={sceneVideoUrl} />
                        ) : isFirstLastVideoMode ? (
                          <div className="clipSB_scenarioEditorVideoFramesGrid">
                            <div className="clipSB_scenarioEditorVideoFrameTile">
                              <div className="clipSB_scenarioEditorVideoFrameLabel">ПЕРВЫЙ КАДР</div>
                              {startFrameSourceUrl ? (
                                <img
                                  className="clipSB_scenarioEditorImagePreview"
                                  src={startFrameSourceUrl}
                                  alt={`scene-${selectedSceneId}-video-start-frame`}
                                />
                              ) : (
                                <div className="clipSB_scenarioEditorPreviewPlaceholder clipSB_scenarioEditorVideoTilePlaceholder">Первый кадр отсутствует</div>
                              )}
                            </div>
                            <div className="clipSB_scenarioEditorVideoFrameTile">
                              <div className="clipSB_scenarioEditorVideoFrameLabel">ПОСЛЕДНИЙ КАДР</div>
                              {endFrameSourceUrl ? (
                                <img
                                  className="clipSB_scenarioEditorImagePreview"
                                  src={endFrameSourceUrl}
                                  alt={`scene-${selectedSceneId}-video-end-frame`}
                                />
                              ) : (
                                <div className="clipSB_scenarioEditorPreviewPlaceholder clipSB_scenarioEditorVideoTilePlaceholder">Последний кадр отсутствует</div>
                              )}
                            </div>
                          </div>
                        ) : sourceImageUrl ? (
                          <img className="clipSB_scenarioEditorImagePreview" src={sourceImageUrl} alt={`scene-${selectedSceneId}-video-source`} />
                        ) : (
                          <div className="clipSB_scenarioEditorPreviewPlaceholder" role="status" aria-live="polite">
                            <div className="clipSB_scenarioEditorPreviewPlaceholderIcon" aria-hidden="true">🖼️</div>
                            <div>Исходное изображение для видео отсутствует</div>
                          </div>
                        )}
                        {!hasSceneVideo ? (
                          <div className="clipSB_hint clipSB_scenarioEditorVideoHint clipSB_scenarioEditorVideoHint--inside">Видео сцены пока не создано</div>
                        ) : null}
                      </div>
                    </div>
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
