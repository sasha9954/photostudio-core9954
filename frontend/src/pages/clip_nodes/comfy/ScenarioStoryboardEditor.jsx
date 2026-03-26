import React, { useEffect, useMemo, useRef, useState } from "react";
const CLIP_TRACE_SCENARIO_GLOBAL_MUSIC = false;

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
  const t0 = Number(scene?.audioSliceStartSec ?? scene?.t0 ?? 0);
  const t1 = Number(scene?.audioSliceEndSec ?? scene?.t1 ?? t0);
  return Math.max(0, t1 - t0);
}

function resolveBlockStatus({ runtimeStatus = "", assetUrl = "" } = {}) {
  const status = String(runtimeStatus || "").trim().toLowerCase();
  if (["loading", "queued", "running", "generating"].includes(status)) return "loading";
  if (status === "error" || status === "not_found") return "error";
  if (status === "done" || String(assetUrl || "").trim()) return "done";
  return "idle";
}

function sceneBadges(scene = {}) {
  const badges = [];
  const mode = String(scene?.renderMode || "image_to_video").trim();
  if (mode === "lip_sync") badges.push("lip_sync");
  if (isFirstLastScene(scene)) badges.push("first_last");
  if (mode === "image_to_video") badges.push("i2v");
  return badges;
}

function isFirstLastScene(scene = {}) {
  const imageStrategy = String(scene?.imageStrategy || "").trim().toLowerCase();
  if (imageStrategy === "first_last") return true;
  const mode = String(scene?.renderMode || "").trim().toLowerCase();
  if (mode === "first_last") return true;
  if (scene?.requiresTwoFrames === true || scene?.needsTwoFrames === true) return true;
  const ltxMode = String(scene?.ltxMode || scene?.ltx_mode || "").trim().toLowerCase();
  return ["f_l", "f_l_as", "first_last"].includes(ltxMode);
}

function resolveMusicSource(audioData = {}) {
  if (String(audioData?.musicSource || "").trim()) return String(audioData.musicSource).trim().toLowerCase();
  if (String(audioData?.fileName || "").trim()) return "uploaded";
  if (String(audioData?.musicUrl || "").trim()) return "generated";
  return "none";
}

export default function ScenarioStoryboardEditor({
  open,
  nodeId,
  storyboardRevision,
  scenes,
  sceneGeneration,
  audioData,
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
  const prevStoryboardRevisionRef = useRef("");

  useEffect(() => {
    if (!open) return;
    const firstSceneId = String(scenes?.[0]?.sceneId || "").trim();
    if (firstSceneId) {
      setActiveSelectionType("scene");
      setActiveSelectionId(firstSceneId);
    } else {
      setActiveSelectionType("bg_audio");
      setActiveSelectionId(BG_AUDIO_ITEM_ID);
    }
  }, [open, nodeId]);

  useEffect(() => {
    if (!open) return;
    const previousRevision = String(prevStoryboardRevisionRef.current || "");
    const nextRevision = String(storyboardRevision || "");
    const revisionChanged = Boolean(nextRevision) && previousRevision !== nextRevision;
    const safeScenes = Array.isArray(scenes) ? scenes : [];
    const firstSceneId = String(safeScenes?.[0]?.sceneId || "").trim();
    if (revisionChanged) {
      if (firstSceneId) {
        setActiveSelectionType("scene");
        setActiveSelectionId(firstSceneId);
      } else {
        setActiveSelectionType("bg_audio");
        setActiveSelectionId(BG_AUDIO_ITEM_ID);
      }
    }
    prevStoryboardRevisionRef.current = nextRevision;
    console.debug("[SCENARIO EDITOR SYNC]", {
      revisionChanged,
      usingNewPackage: revisionChanged,
      scenesCount: safeScenes.length,
    });
  }, [open, scenes, storyboardRevision]);

  useEffect(() => {
    if (!open || !infoModalOpen) return undefined;
    const onKeyDown = (event) => {
      if (event.key === "Escape") setInfoModalOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [open, infoModalOpen]);

  const safeScenes = Array.isArray(scenes) ? scenes : [];
  const safeGeneration = sceneGeneration && typeof sceneGeneration === "object" ? sceneGeneration : {};
  const safeAudioData = audioData && typeof audioData === "object" ? audioData : {};

  const phrases = useMemo(() => {
    if (Array.isArray(safeAudioData?.phrases) && safeAudioData.phrases.length) return safeAudioData.phrases;
    return safeScenes.map((scene, idx) => ({
      sceneId: String(scene?.sceneId || `S${idx + 1}`),
      startSec: Number(scene?.audioSliceStartSec ?? scene?.t0 ?? 0),
      endSec: Number(scene?.audioSliceEndSec ?? scene?.t1 ?? scene?.t0 ?? 0),
      text: String(scene?.localPhrase || scene?.summaryRu || "").trim(),
      energy: String(scene?.emotionRu || "").trim(),
      context: String(scene?.locationRu || "").trim(),
    }));
  }, [safeAudioData?.phrases, safeScenes]);

  const safeIndex = safeScenes.findIndex((scene, idx) => String(scene?.sceneId || `S${idx + 1}`) === activeSelectionId);
  const selectedScene = safeIndex >= 0 ? safeScenes[safeIndex] : null;
  const selectedSceneId = String(selectedScene?.sceneId || "").trim();
  const selectedRuntime = safeGeneration[selectedSceneId] && typeof safeGeneration[selectedSceneId] === "object" ? safeGeneration[selectedSceneId] : {};
  const resolvePhraseSceneId = (phrase, idx) => String(phrase?.sceneId || safeScenes[idx]?.sceneId || "").trim();
  const selectedPhraseIndex = phrases.findIndex((phrase, idx) => resolvePhraseSceneId(phrase, idx) === selectedSceneId);

  const handleSelectPhrase = (phrase, idx) => {
    const phraseSceneId = resolvePhraseSceneId(phrase, idx);
    if (!phraseSceneId) return;
    setActiveSelectionType("scene");
    setActiveSelectionId(phraseSceneId);
  };

  const jumpToPhrase = (startSec) => {
    if (!masterAudioRef.current) return;
    const t0 = Number(startSec);
    if (!Number.isFinite(t0)) return;
    masterAudioRef.current.currentTime = Math.max(0, t0);
    masterAudioRef.current.play().catch(() => {});
  };

  const resolveExtractedAudioStatus = (scene) => {
    const rawStatus = String(scene?.extractedAudioStatus || "").trim().toLowerCase();
    if (["not_extracted", "extracting", "ready", "error"].includes(rawStatus)) return rawStatus;
    if (String(scene?.extractedAudioUrl || "").trim()) return "ready";
    return "not_extracted";
  };

  const resolveAudioHeaderBadge = (scene) => {
    const status = resolveExtractedAudioStatus(scene);
    if (status === "ready") return "ready";
    if (status === "extracting") return "audio attached";
    return "not extracted";
  };

  const resolveExtractedAudioStatusTone = (scene) => {
    const status = resolveExtractedAudioStatus(scene);
    if (status === "ready") return "done";
    if (status === "extracting") return "loading";
    if (status === "error") return "error";
    return "idle";
  };

  const handleExtractSceneAudio = async (scene) => {
    const sceneId = String(scene?.sceneId || "").trim();
    if (!sceneId) return;
    const startSec = Number(scene?.audioSliceStartSec ?? scene?.t0 ?? 0);
    const endSec = Number(scene?.audioSliceEndSec ?? scene?.t1 ?? startSec);
    const durationSec = Math.max(0, endSec - startSec);
    onUpdateScene?.(nodeId, sceneId, {
      extractedAudioStatus: "extracting",
      extractedAudioDurationSec: durationSec,
      extractedAudioError: "",
    });
    try {
      const result = await onExtractSceneAudio?.(nodeId, sceneId);
      const extractedAudioUrl = String(
        result?.extractedAudioUrl
        || scene?.audioSliceUrl
        || safeAudioData?.audioUrl
        || "",
      ).trim();
      if (!extractedAudioUrl) {
        onUpdateScene?.(nodeId, sceneId, {
          extractedAudioStatus: "error",
          extractedAudioError: "Не найден источник для audio slice",
        });
        return;
      }
      onUpdateScene?.(nodeId, sceneId, {
        extractedAudioUrl,
        extractedAudioStatus: "ready",
        extractedAudioDurationSec: Number(result?.extractedAudioDurationSec ?? durationSec),
        extractedAudioError: "",
      });
    } catch (error) {
      onUpdateScene?.(nodeId, sceneId, {
        extractedAudioStatus: "error",
        extractedAudioError: String(error?.message || "Не удалось изъять аудио"),
      });
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
  const sourceImageUrl = String(selectedScene?.imageUrl || "").trim();
  const startFrameSourceUrl = String(selectedScene?.startImageUrl || selectedScene?.startFrameImageUrl || selectedScene?.startFramePreviewUrl || selectedScene?.imageUrl || "").trim();
  const endFrameSourceUrl = String(selectedScene?.endImageUrl || selectedScene?.endFrameImageUrl || selectedScene?.endFramePreviewUrl || "").trim();
  const sourceFrameFirstUrl = String(
    selectedScene?.startFrameImageUrl
    || selectedScene?.startImageUrl
    || selectedScene?.imageUrl
    || selectedScene?.imagePreviewUrl
    || "",
  ).trim();
  const sourceFrameLastUrl = String(selectedScene?.endImageUrl || selectedScene?.endFrameImageUrl || "").trim();
  const sourceFrameLastPlaceholder = isFirstLastVideoMode ? "Последний кадр не создан" : "Последний кадр не требуется";
  const sceneVideoUrl = String(selectedScene?.videoUrl || "").trim();
  const hasSceneVideo = Boolean(sceneVideoUrl);
  const bgMusicSource = resolveMusicSource(safeAudioData);
  const globalMusicPrompt = String(
    safeAudioData?.globalMusicPrompt
    || safeAudioData?.musicPromptRu
    || safeAudioData?.musicPromptEn
    || "",
  ).trim();
  const hasBgMusicPrompt = Boolean(globalMusicPrompt);
  const hasBgMusic = Boolean(String(safeAudioData?.musicUrl || "").trim());
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

  useEffect(() => {
    if (!CLIP_TRACE_SCENARIO_GLOBAL_MUSIC) return;
    console.debug("[SCENARIO STORYBOARD MUSIC]", {
      packageHasGlobalMusicPrompt: !!String(safeAudioData?.packageGlobalMusicPrompt || "").trim(),
      audioDataHasGlobalMusicPrompt: !!String(safeAudioData?.globalMusicPrompt || "").trim(),
      editorPromptVisible,
    });
  }, [safeAudioData?.packageGlobalMusicPrompt, safeAudioData?.globalMusicPrompt, editorPromptVisible]);

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

  const tabContent = (() => {
    if (activeTab === "scenario") {
      return (
        <div className="clipSB_scenarioEditorTabBody">
          <div className="clipSB_storyboardKv"><span>Сцен</span><strong>{safeScenes.length}</strong></div>
          <div className="clipSB_storyboardKv"><span>Текущая сцена</span><strong>{selectedSceneId || "—"}</strong></div>
          <div className="clipSB_storyboardKv"><span>summaryRu</span><strong>{selectedScene?.summaryRu || "—"}</strong></div>
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
          {phrases.map((phrase, idx) => {
            const phraseSceneId = resolvePhraseSceneId(phrase, idx);
            const isActive = idx === selectedPhraseIndex;
            return (
              <div
                key={`${phraseSceneId || idx}-${idx}`}
                className={`clipSB_scenarioEditorPhraseItem ${isActive ? "isActive" : ""}`}
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
                  <div className="clipSB_scenarioEditorPhraseText">{phrase.text || "—"}</div>
                </div>
                <button
                  className="clipSB_btn clipSB_btnSecondary clipSB_scenarioEditorPhraseJumpBtn"
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    handleSelectPhrase(phrase, idx);
                    jumpToPhrase(phrase.startSec);
                  }}
                >
                  ▶ Перемотать
                </button>
              </div>
            );
          })}
        </div>
      );
    }
    return (
      <div className="clipSB_scenarioEditorTabBody">
        <pre className="clipSB_scenarioEditorDebug">{JSON.stringify({
          sceneId: selectedSceneId,
          sceneRuntime: selectedRuntime,
          musicStatus: safeAudioData?.musicStatus || "idle",
        }, null, 2)}</pre>
      </div>
    );
  })();

  if (!open) return null;

  return (
    <div className="clipSB_scenarioOverlay" onClick={onClose}>
      <div className="clipSB_scenarioPanel clipSB_scenarioEditorPanel" onClick={(event) => event.stopPropagation()}>
        <div className="clipSB_scenarioHeader">
          <div>
            <div className="clipSB_scenarioTitle">Scenario Storyboard Editor</div>
            <div className="clipSB_scenarioMeta">Сцен: {safeScenes.length}</div>
          </div>
          <button className="clipSB_iconBtn" onClick={onClose} type="button">×</button>
        </div>

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
            <div className="clipSB_scenarioEditorInfoModal" onClick={(event) => event.stopPropagation()}>
              <div className="clipSB_scenarioEditorInfoModalHeader">
                <div className="clipSB_scenarioEditorInfoModalTitle">{TOP_TABS.find((tab) => tab.id === activeTab)?.label || "Инфо"}</div>
                <button className="clipSB_iconBtn" type="button" onClick={() => setInfoModalOpen(false)}>×</button>
              </div>
              <div className="clipSB_scenarioEditorInfoModalBody">
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

            {safeScenes.map((scene, idx) => {
              const sceneId = String(scene?.sceneId || `S${idx + 1}`);
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
                    <div className="clipSB_storyboardSceneId">[ {sceneId} · {fmtSec(scene?.audioSliceStartSec ?? scene?.t0)}–{fmtSec(scene?.audioSliceEndSec ?? scene?.t1)} ]</div>
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
                      <audio ref={masterAudioRef} controls className="clipSB_audioPlayer" src={safeAudioData.audioUrl} />
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
                      onChange={(event) => onUpdateMusic?.(nodeId, { musicPromptRu: event.target.value, globalMusicPrompt: event.target.value })}
                      placeholder="Сценарист ещё не предложил фоновую музыку"
                    />
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
                            <button className="clipSB_btn" type="button" onClick={() => onGenerateScene?.(nodeId, selectedSceneId, "image")} disabled={imageStatus === "loading"}>Создать изображение</button>
                            <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { imageUrl: "", imageStatus: "idle" })}>Удалить</button>
                          </div>
                        </div>
                        <div className="clipSB_scenarioEditorImageRight clipSB_scenarioEditorImageRightMain">
                          <div className="clipSB_scenarioEditorBlockHead">
                            <h4>IMAGE</h4>
                            <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${imageStatus}`}>{imageStatus}</span>
                          </div>
                          <div className={`clipSB_scenarioEditorImagePreviewWrap${selectedScene?.imageUrl ? "" : " clipSB_scenarioEditorImagePreviewWrap--empty"}`}>
                            {selectedScene?.imageUrl ? <img className="clipSB_scenarioEditorImagePreview" src={selectedScene.imageUrl} alt={`scene-${selectedSceneId}-image`} /> : (
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
                    <>
                      <div className="clipSB_scenarioEditorImageSubBlock">
                        <div className="clipSB_scenarioEditorBlockHead">
                          <h4>ПЕРВЫЙ КАДР</h4>
                          <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${startFrameStatus}`}>{startFrameStatus}</span>
                        </div>
                        <div className="clipSB_scenarioEditorImageBody">
                          <div className="clipSB_scenarioEditorImageLeft">
                            <textarea
                              className="clipSB_textarea"
                              rows={3}
                              value={String(selectedScene?.startFramePromptRu || "")}
                              onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { startFramePromptRu: event.target.value })}
                              placeholder="startFramePromptRu"
                            />
                            <details>
                              <summary>EN</summary>
                              <textarea
                                className="clipSB_textarea"
                                rows={2}
                                value={String(selectedScene?.startFramePromptEn || "")}
                                onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { startFramePromptEn: event.target.value })}
                                placeholder="startFramePromptEn"
                              />
                            </details>
                          </div>
                          <div className="clipSB_scenarioEditorImageRight">
                            {selectedScene?.startFrameImageUrl || selectedScene?.startFramePreviewUrl || selectedScene?.imageUrl ? (
                              <img
                                className="clipSB_scenarioEditorImagePreview"
                                src={selectedScene?.startFrameImageUrl || selectedScene?.startFramePreviewUrl || selectedScene?.imageUrl}
                                alt={`scene-${selectedSceneId}-start-frame`}
                              />
                            ) : <div className="clipSB_hint">preview первого кадра отсутствует</div>}
                          </div>
                        </div>
                        <div className="clipSB_scenarioEditorBtnRow">
                          <button className="clipSB_btn" type="button" onClick={() => onGenerateScene?.(nodeId, selectedSceneId, "start_frame")} disabled={startFrameStatus === "loading"}>Создать изображение</button>
                          <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { startFrameImageUrl: "", startFramePreviewUrl: "", startFrameStatus: "idle" })}>Удалить</button>
                        </div>
                      </div>
                      <div className="clipSB_scenarioEditorImageSubBlock">
                        <div className="clipSB_scenarioEditorBlockHead">
                          <h4>ПОСЛЕДНИЙ КАДР</h4>
                          <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${endFrameStatus}`}>{endFrameStatus}</span>
                        </div>
                        <div className="clipSB_scenarioEditorImageBody">
                          <div className="clipSB_scenarioEditorImageLeft">
                            <textarea
                              className="clipSB_textarea"
                              rows={3}
                              value={String(selectedScene?.endFramePromptRu || "")}
                              onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { endFramePromptRu: event.target.value })}
                              placeholder="endFramePromptRu"
                            />
                            <details>
                              <summary>EN</summary>
                              <textarea
                                className="clipSB_textarea"
                                rows={2}
                                value={String(selectedScene?.endFramePromptEn || "")}
                                onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { endFramePromptEn: event.target.value })}
                                placeholder="endFramePromptEn"
                              />
                            </details>
                          </div>
                          <div className="clipSB_scenarioEditorImageRight">
                            {selectedScene?.endFrameImageUrl || selectedScene?.endFramePreviewUrl ? (
                              <img
                                className="clipSB_scenarioEditorImagePreview"
                                src={selectedScene?.endFrameImageUrl || selectedScene?.endFramePreviewUrl}
                                alt={`scene-${selectedSceneId}-end-frame`}
                              />
                            ) : <div className="clipSB_hint">preview последнего кадра отсутствует</div>}
                          </div>
                        </div>
                        <div className="clipSB_scenarioEditorBtnRow">
                          <button className="clipSB_btn" type="button" onClick={() => onGenerateScene?.(nodeId, selectedSceneId, "end_frame")} disabled={endFrameStatus === "loading"}>Создать изображение</button>
                          <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { endFrameImageUrl: "", endFramePreviewUrl: "", endFrameStatus: "idle" })}>Удалить</button>
                        </div>
                      </div>
                    </>
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
                          <strong>{fmtSec(selectedScene.audioSliceStartSec)} c</strong>
                        </div>
                        <div className="clipSB_sceneAudioInfoCard">
                          <span>Конец</span>
                          <strong>{fmtSec(selectedScene.audioSliceEndSec)} c</strong>
                        </div>
                        <div className="clipSB_sceneAudioInfoCard">
                          <span>Длительность</span>
                          <strong>{fmtSec(Math.max(0, Number(selectedScene.audioSliceEndSec ?? selectedScene?.t1 ?? 0) - Number(selectedScene.audioSliceStartSec ?? selectedScene?.t0 ?? 0)))} c</strong>
                        </div>
                      </div>

                      <div className="clipSB_sceneAudioCol clipSB_sceneAudioActionCol">
                        <div className="clipSB_scenarioEditorBtnRow">
                          <button
                            className="clipSB_btn"
                            type="button"
                            onClick={() => handleExtractSceneAudio(selectedScene)}
                            disabled={resolveExtractedAudioStatus(selectedScene) === "extracting"}
                          >
                            {resolveExtractedAudioStatus(selectedScene) === "extracting" ? "Извлекаем..." : "Изъять аудио"}
                          </button>
                        </div>
                        {resolveExtractedAudioStatus(selectedScene) === "ready" && selectedScene?.extractedAudioUrl ? (
                          <div className="clipSB_sceneAudioReadyBox">
                            <audio controls className="clipSB_audioPlayer" src={selectedScene.extractedAudioUrl} />
                            <div className="clipSB_sceneAudioReadyMeta">
                              <span className="clipSB_tag clipSB_tagStatus clipSB_tagStatus--done">extracted / ready</span>
                              <span className="clipSB_small">Длительность: {fmtSec(selectedScene?.extractedAudioDurationSec ?? Math.max(0, Number(selectedScene.audioSliceEndSec ?? selectedScene?.t1 ?? 0) - Number(selectedScene.audioSliceStartSec ?? selectedScene?.t0 ?? 0)))} c</span>
                            </div>
                            <div className="clipSB_sceneAudioLipSyncReady">Готово для lip-sync и sound-enabled scene.</div>
                          </div>
                        ) : (
                          <div className="clipSB_sceneAudioPlaceholder">Аудио-кусок сцены ещё не подготовлен</div>
                        )}
                        {resolveExtractedAudioStatus(selectedScene) === "error" ? (
                          <div className="clipSB_hint" style={{ color: "#ff8a8a" }}>{String(selectedScene?.extractedAudioError || "Ошибка извлечения аудио")}</div>
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
                      <div className="clipSB_sourceFramesBlock">
                        <div className="clipSB_brainLabel">source кадры</div>
                        <div className="clipSB_sourceFramesGrid">
                          <div className="clipSB_sourceFrameTile">
                            <div className="clipSB_sourceFrameTileLabel">Первый кадр</div>
                            {sourceFrameFirstUrl ? (
                              <img
                                className="clipSB_sourceFramePreview"
                                src={sourceFrameFirstUrl}
                                alt={`scene-${selectedSceneId}-source-first-frame`}
                              />
                            ) : (
                              <div className="clipSB_sourceFramePlaceholder">Первый кадр не создан</div>
                            )}
                          </div>
                          <div className="clipSB_sourceFrameTile">
                            <div className="clipSB_sourceFrameTileLabel">Последний кадр</div>
                            {sourceFrameLastUrl ? (
                              <img
                                className="clipSB_sourceFramePreview"
                                src={sourceFrameLastUrl}
                                alt={`scene-${selectedSceneId}-source-last-frame`}
                              />
                            ) : (
                              <div className="clipSB_sourceFramePlaceholder">{sourceFrameLastPlaceholder}</div>
                            )}
                          </div>
                        </div>
                      </div>
                      <div className="clipSB_scenarioEditorBtnRow">
                        <button className="clipSB_btn" type="button" onClick={() => onGenerateScene?.(nodeId, selectedSceneId, "video")} disabled={videoStatus === "loading"}>Создать видео</button>
                        <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { videoUrl: "", videoStatus: "idle", videoError: "", videoJobId: "" })}>Удалить</button>
                      </div>
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
