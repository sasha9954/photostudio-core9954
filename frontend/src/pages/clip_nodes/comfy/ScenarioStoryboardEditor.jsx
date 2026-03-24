import React, { useEffect, useMemo, useRef, useState } from "react";

const RENDER_MODE_OPTIONS = [
  { value: "image_to_video", label: "image_to_video" },
  { value: "lip_sync", label: "lip_sync" },
  { value: "first_last", label: "first_last" },
];

const TOP_TABS = [
  { id: "scenario", label: "Сценарий" },
  { id: "context", label: "Контекст" },
  { id: "actors", label: "Актеры" },
  { id: "phrases", label: "Фразы" },
  { id: "preview", label: "Preview" },
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
  if (mode === "first_last" || scene?.needsTwoFrames) badges.push("first_last");
  if (mode === "image_to_video") badges.push("i2v");
  return badges;
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
  scenes,
  sceneGeneration,
  audioData,
  onClose,
  onUpdateScene,
  onGenerateScene,
  onUpdateMusic,
  onGenerateMusic,
}) {
  const [activeSelectionType, setActiveSelectionType] = useState("scene");
  const [activeSelectionId, setActiveSelectionId] = useState("");
  const [activeTab, setActiveTab] = useState("phrases");
  const [tabPanelOpen, setTabPanelOpen] = useState(true);
  const masterAudioRef = useRef(null);
  const bgMusicUploadRef = useRef(null);

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
  const selectedPhraseIndex = phrases.findIndex((phrase) => String(phrase?.sceneId || "") === selectedSceneId);

  const jumpToPhrase = (startSec) => {
    if (!masterAudioRef.current) return;
    const t0 = Number(startSec);
    if (!Number.isFinite(t0)) return;
    masterAudioRef.current.currentTime = Math.max(0, t0);
    masterAudioRef.current.play().catch(() => {});
  };

  const previewSceneAudioSlice = (scene) => {
    if (!scene || !masterAudioRef.current) return;
    const startSec = Number(scene?.audioSliceStartSec ?? scene?.t0 ?? 0);
    const endSec = Number(scene?.audioSliceEndSec ?? scene?.t1 ?? startSec);
    if (!Number.isFinite(startSec) || !Number.isFinite(endSec)) return;
    masterAudioRef.current.currentTime = Math.max(0, startSec);
    masterAudioRef.current.play().catch(() => {});
    const durationMs = Math.max(100, (Math.max(endSec, startSec) - startSec) * 1000);
    window.setTimeout(() => {
      if (!masterAudioRef.current) return;
      masterAudioRef.current.pause();
    }, durationMs);
  };

  const imageStatus = resolveBlockStatus({ runtimeStatus: selectedRuntime?.imageStatus || selectedRuntime?.status, assetUrl: selectedScene?.imageUrl });
  const videoStatus = resolveBlockStatus({ runtimeStatus: selectedRuntime?.videoStatus || selectedRuntime?.status || selectedScene?.videoStatus, assetUrl: selectedScene?.videoUrl });
  const musicStatus = resolveBlockStatus({ runtimeStatus: safeAudioData?.musicStatus, assetUrl: safeAudioData?.musicUrl });
  const isBgAudioSelected = activeSelectionType === "bg_audio";
  const bgMusicSource = resolveMusicSource(safeAudioData);
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
            const isActive = idx === selectedPhraseIndex;
            return (
              <div key={`${phrase.sceneId || idx}-${idx}`} className={`clipSB_scenarioEditorPhraseItem ${isActive ? "isActive" : ""}`}>
                <div className="clipSB_small">[{fmtSec(phrase.startSec)} - {fmtSec(phrase.endSec)}] {phrase.text || "—"}</div>
                <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => jumpToPhrase(phrase.startSec)}>▶ Перемотать</button>
              </div>
            );
          })}
        </div>
      );
    }
    if (activeTab === "preview") {
      return <div className="clipSB_scenarioEditorTabBody clipSB_hint">Preview panel оставлен как вторичная инфо-зона.</div>;
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
                  setTabPanelOpen(true);
                }}
              >
                {tab.label}
              </button>
            ))}
            <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => setTabPanelOpen((prev) => !prev)}>
              {tabPanelOpen ? "Скрыть панель" : "Показать панель"}
            </button>
          </div>
          {tabPanelOpen ? <div className="clipSB_scenarioEditorTabPanel">{tabContent}</div> : null}
        </div>

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
                      value={String(safeAudioData?.musicPromptRu || "")}
                      onChange={(event) => onUpdateMusic?.(nodeId, { musicPromptRu: event.target.value })}
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
                    <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${imageStatus}`}>{imageStatus}</span>
                  </div>
                  <textarea
                    className="clipSB_textarea"
                    rows={3}
                    value={String(selectedScene?.imagePromptRu || "")}
                    onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { imagePromptRu: event.target.value })}
                    placeholder="imagePromptRu"
                  />
                  <details>
                    <summary>EN</summary>
                    <textarea
                      className="clipSB_textarea"
                      rows={2}
                      value={String(selectedScene?.imagePromptEn || "")}
                      onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { imagePromptEn: event.target.value })}
                      placeholder="imagePromptEn"
                    />
                  </details>
                  {selectedScene?.imageUrl ? <img className="clipSB_scenarioEditorImagePreview" src={selectedScene.imageUrl} alt={`scene-${selectedSceneId}`} /> : <div className="clipSB_hint">preview изображения отсутствует</div>}
                  <div className="clipSB_scenarioEditorBtnRow">
                    <button className="clipSB_btn" type="button" onClick={() => onGenerateScene?.(nodeId, selectedSceneId)} disabled={imageStatus === "loading"}>Создать изображение</button>
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { imageUrl: "" })}>Удалить</button>
                  </div>
                </div>

                <div className="clipSB_scenarioEditorBlock">
                  <div className="clipSB_scenarioEditorBlockHead">
                    <h4>2. AUDIO (СЦЕНА)</h4>
                    <span className="clipSB_tag">{selectedScene?.localPhrase ? "audio attached" : "not used"}</span>
                  </div>
                  <div className="clipSB_storyboardKv"><span>narrationMode</span><strong>{selectedScene.narrationMode || "—"}</strong></div>
                  <div className="clipSB_storyboardKv"><span>localPhrase</span><strong>{selectedScene.localPhrase || "—"}</strong></div>
                  <div className="clipSB_storyboardKv"><span>audioSliceStartSec</span><strong>{fmtSec(selectedScene.audioSliceStartSec)}s</strong></div>
                  <div className="clipSB_storyboardKv"><span>audioSliceEndSec</span><strong>{fmtSec(selectedScene.audioSliceEndSec)}s</strong></div>
                  <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => previewSceneAudioSlice(selectedScene)}>▶ прослушать кусок</button>
                </div>

                <div className="clipSB_scenarioEditorBlock">
                  <div className="clipSB_scenarioEditorBlockHead">
                    <h4>3. VIDEO</h4>
                    <span className={`clipSB_tag clipSB_tagStatus clipSB_tagStatus--${videoStatus}`}>{videoStatus}</span>
                  </div>
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
                  <label className="clipSB_narrativeField">
                    <div className="clipSB_brainLabel">cameraRu</div>
                    <input
                      className="clipSB_input"
                      value={String(selectedScene?.cameraRu || "")}
                      onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { cameraRu: event.target.value })}
                    />
                  </label>
                  <label className="clipSB_narrativeField">
                    <div className="clipSB_brainLabel">renderMode</div>
                    <select className="clipSB_select" value={selectedScene.renderMode || "image_to_video"} onChange={(event) => onUpdateScene?.(nodeId, selectedSceneId, { renderMode: event.target.value })}>
                      {RENDER_MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </select>
                  </label>
                  {selectedScene?.videoUrl ? (
                    <video className="clipSB_scenarioEditorVideoPreview" controls src={selectedScene.videoUrl} />
                  ) : (
                    <div className="clipSB_hint">preview видео отсутствует</div>
                  )}
                  <div className="clipSB_scenarioEditorBtnRow">
                    <button className="clipSB_btn" type="button" onClick={() => onGenerateScene?.(nodeId, selectedSceneId)} disabled={videoStatus === "loading"}>Создать видео</button>
                    <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => onUpdateScene?.(nodeId, selectedSceneId, { videoUrl: "", videoStatus: "idle", videoError: "", videoJobId: "" })}>Удалить</button>
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
