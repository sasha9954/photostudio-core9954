import React, { useEffect, useMemo, useState } from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";
import {
  NARRATIVE_INPUT_HANDLES,
  NARRATIVE_SOURCE_INPUT_HANDLES,
  NARRATIVE_SOURCE_OPTIONS,
  NARRATIVE_CONTEXT_INPUT_HANDLES,
  NARRATIVE_CONTENT_TYPE_OPTIONS,
  NARRATIVE_FORMAT_OPTIONS,
  NARRATIVE_RESULT_TABS,
  getSafeNarrativeContentType,
  isNarrativeContentTypeEnabled,
  summarizeNarrativeConnectedContext,
} from "./comfyNarrativeDomain";
import ScenarioTabTextViewer from "./ScenarioTabTextViewer";

const NARRATIVE_HANDLE_TOP = 104;
const NARRATIVE_HANDLE_STEP = 24;

const OUTPUT_HANDLES = [
  { id: "storyboard_out", labelRu: "Storyboard" },
  { id: "preview_out", labelRu: "Preview" },
];

function toPrettyJson(value) {
  try {
    return JSON.stringify(value ?? null, null, 2);
  } catch {
    return String(value || "");
  }
}

function stopNodeEvent(event) {
  event.stopPropagation();
}

function toText(value, fallback = "—") {
  if (Array.isArray(value)) return value.length ? value.join("\n") : fallback;
  const text = String(value ?? "").trim();
  return text || fallback;
}

function makeSceneSelector({ sceneOptions = [], selectedSceneId = "", onChange = null }) {
  if (!sceneOptions.length) return null;
  return (
    <label className="clipSB_scenarioTabSceneSelector nodrag nopan nowheel" onMouseDown={stopNodeEvent} onPointerDown={stopNodeEvent}>
      <span>Сцена</span>
      <select
        className="clipSB_select clipSB_scenarioTabSceneSelect nodrag nopan nowheel"
        value={selectedSceneId}
        onMouseDown={stopNodeEvent}
        onPointerDown={stopNodeEvent}
        onChange={(event) => onChange?.(event.target.value)}
      >
        {sceneOptions.map((scene) => (
          <option key={scene.sceneId} value={scene.sceneId}>{scene.sceneId}</option>
        ))}
      </select>
    </label>
  );
}

function renderHistoryTab({ directorOutput = {}, history = null }) {
  if (!history) return <div className="clipSB_small">История появится после генерации director output.</div>;

  const roleText = Array.isArray(history.characterRoles)
    ? history.characterRoles
      .map((item) => `${item?.displayName || item?.name || "—"}: ${item?.role || "—"}`)
      .join("\n")
    : "";

  return (
    <div className="clipSB_narrativeReadable">
      <ScenarioTabTextViewer title="summary" text={history.summary} minRows={3} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="fullScenario" text={history.fullScenario} minRows={8} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="directorSummary" text={history.directorSummary} minRows={6} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="roles" text={roleText} minRows={4} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="raw history JSON" text={toPrettyJson(directorOutput?.history || history)} minRows={6} copyLabel="Копировать" />
    </div>
  );
}

function renderScenesTab({
  storyboardScenes = [],
  directorScenes = [],
  selectedSceneId = "",
  setSelectedSceneId,
}) {
  if (!storyboardScenes.length && !directorScenes.length) return <div className="clipSB_small">Сцены появятся после генерации director output.</div>;

  const selectedStoryboardScene = storyboardScenes.find((scene) => String(scene?.sceneId || "") === selectedSceneId) || storyboardScenes[0] || null;
  const selectedDirectorScene = directorScenes.find((scene) => String(scene?.sceneId || "") === selectedSceneId) || directorScenes[0] || null;
  const sceneSource = selectedStoryboardScene || selectedDirectorScene || {};
  const warnings = Array.isArray(sceneSource?.contractWarnings)
    ? sceneSource.contractWarnings
    : Array.isArray(sceneSource?.warnings)
      ? sceneSource.warnings
      : [];
  const warningsText = warnings.length
    ? warnings.map((warning) => {
      if (typeof warning === "string") return warning;
      return String(warning?.label || warning?.code || "warning");
    }).join("\n")
    : "—";

  const sceneOptions = (storyboardScenes.length ? storyboardScenes : directorScenes)
    .map((scene, idx) => ({ sceneId: String(scene?.sceneId || `S${idx + 1}`) }))
    .filter((scene) => !!scene.sceneId);

  return (
    <div className="clipSB_narrativeReadable">
      <ScenarioTabTextViewer
        title="scene contract (selected scene)"
        text={toPrettyJson(sceneSource)}
        minRows={8}
        copyLabel="Копировать"
        copyAllLabel="Копировать всё"
        onCopyAll={() => navigator.clipboard.writeText(toPrettyJson(storyboardScenes.length ? storyboardScenes : directorScenes)).then(() => true).catch(() => false)}
        extraActions={makeSceneSelector({ sceneOptions, selectedSceneId, onChange: setSelectedSceneId })}
      />
      <ScenarioTabTextViewer title="summary" text={sceneSource?.summaryRu || sceneSource?.summary || sceneSource?.summaryEn} minRows={4} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="sceneGoal" text={sceneSource?.sceneGoalRu || sceneSource?.sceneGoal || sceneSource?.sceneGoalEn} minRows={4} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="imagePrompt" text={sceneSource?.imagePromptRu || sceneSource?.imagePrompt || sceneSource?.imagePromptEn} minRows={5} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="videoPrompt" text={sceneSource?.videoPromptRu || sceneSource?.videoPrompt || sceneSource?.videoPromptEn} minRows={5} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="warnings" text={warningsText} minRows={3} copyLabel="Копировать" />
    </div>
  );
}

function renderVideoTab({ storyboardScenes = [], videoRows = [], selectedSceneId = "", setSelectedSceneId }) {
  if (!videoRows.length && !storyboardScenes.length) return <div className="clipSB_small">Видео-описание появится после генерации director output.</div>;

  const selectedStoryboardScene = storyboardScenes.find((scene) => String(scene?.sceneId || "") === selectedSceneId) || storyboardScenes[0] || {};
  const selectedVideoRow = videoRows.find((scene) => String(scene?.sceneId || "") === selectedSceneId) || videoRows[0] || {};
  const sceneOptions = (storyboardScenes.length ? storyboardScenes : videoRows)
    .map((scene, idx) => ({ sceneId: String(scene?.sceneId || `S${idx + 1}`) }))
    .filter((scene) => !!scene.sceneId);

  const transitionDebug = [
    `sceneId: ${toText(selectedStoryboardScene?.sceneId || selectedVideoRow?.sceneId)}`,
    `startFrameSource: ${toText(selectedVideoRow?.startFrameSource || selectedStoryboardScene?.startFrameSource)}`,
    `continuation: ${toText(selectedVideoRow?.continuation || selectedStoryboardScene?.continuation)}`,
    `whyThisMode: ${toText(selectedVideoRow?.whyThisMode || selectedStoryboardScene?.whyThisMode)}`,
    `needsTwoFrames: ${String(Boolean(selectedVideoRow?.needsTwoFrames ?? selectedStoryboardScene?.needsTwoFrames))}`,
  ].join("\n");

  return (
    <div className="clipSB_narrativeReadable">
      <ScenarioTabTextViewer
        title="videoPrompt"
        text={selectedStoryboardScene?.videoPromptRu || selectedStoryboardScene?.videoPrompt || selectedVideoRow?.videoPrompt || selectedStoryboardScene?.videoPromptEn}
        minRows={6}
        copyLabel="Копировать"
        extraActions={makeSceneSelector({ sceneOptions, selectedSceneId, onChange: setSelectedSceneId })}
      />
      <ScenarioTabTextViewer title="renderMode" text={selectedStoryboardScene?.renderMode || selectedVideoRow?.renderMode} minRows={2} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="ltxMode" text={selectedStoryboardScene?.ltxMode || selectedVideoRow?.ltxMode} minRows={2} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="resolved model" text={selectedStoryboardScene?.resolvedModelKey || selectedVideoRow?.resolvedModelKey || selectedVideoRow?.model} minRows={2} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="transition / debug" text={transitionDebug} minRows={5} copyLabel="Копировать" />
    </div>
  );
}

function renderSoundTab({ storyboardScenes = [], soundRows = [], selectedSceneId = "", setSelectedSceneId }) {
  if (!soundRows.length && !storyboardScenes.length) return <div className="clipSB_small">Звук появится после генерации director output.</div>;

  const selectedStoryboardScene = storyboardScenes.find((scene) => String(scene?.sceneId || "") === selectedSceneId) || storyboardScenes[0] || {};
  const selectedSoundRow = soundRows.find((scene) => String(scene?.sceneId || "") === selectedSceneId) || soundRows[0] || {};
  const sceneOptions = (storyboardScenes.length ? storyboardScenes : soundRows)
    .map((scene, idx) => ({ sceneId: String(scene?.sceneId || `S${idx + 1}`) }))
    .filter((scene) => !!scene.sceneId);

  const audioDebug = [
    `narrationMode: ${toText(selectedSoundRow?.narrationMode)}`,
    `localPhrase: ${toText(selectedSoundRow?.localPhrase || selectedStoryboardScene?.localPhrase)}`,
    `sfx: ${toText(selectedSoundRow?.sfx)}`,
    `soundNotes: ${toText(selectedSoundRow?.soundNotes)}`,
    `pauseDuckSilenceNotes: ${toText(selectedSoundRow?.pauseDuckSilenceNotes)}`,
    `raw: ${toPrettyJson(selectedSoundRow)}`,
  ].join("\n\n");

  return (
    <div className="clipSB_narrativeReadable">
      <ScenarioTabTextViewer
        title="lipSync"
        text={String(Boolean(selectedStoryboardScene?.lipSync ?? selectedSoundRow?.lipSync))}
        minRows={2}
        copyLabel="Копировать"
        extraActions={makeSceneSelector({ sceneOptions, selectedSceneId, onChange: setSelectedSceneId })}
      />
      <ScenarioTabTextViewer title="audioSliceUrl" text={selectedStoryboardScene?.audioSliceUrl || selectedSoundRow?.audioSliceUrl} minRows={3} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="requiresAudioSensitiveVideo" text={String(Boolean(selectedStoryboardScene?.requiresAudioSensitiveVideo ?? selectedSoundRow?.requiresAudioSensitiveVideo))} minRows={2} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="audio debug" text={audioDebug} minRows={6} copyLabel="Копировать" />
    </div>
  );
}

function renderMusicTab({ directorOutput = {}, audioData = {} }) {
  const music = directorOutput?.music || audioData || null;
  if (!music) return <div className="clipSB_small">Музыка появится после генерации director output.</div>;

  return (
    <div className="clipSB_narrativeReadable">
      <ScenarioTabTextViewer title="globalMusicPrompt" text={music.globalMusicPrompt || music.musicPromptSourceText || music.musicPromptRu} minRows={4} copyLabel="Копировать" />
      <ScenarioTabTextViewer title="music debug / raw" text={toPrettyJson(music)} minRows={7} copyLabel="Копировать" />
    </div>
  );
}

function renderJsonTab(storyboardOut) {
  if (!storyboardOut) return <div className="clipSB_small">JSON появится после генерации storyboard_out.</div>;
  return (
    <div className="clipSB_narrativeReadable">
      <ScenarioTabTextViewer title="raw JSON" text={toPrettyJson(storyboardOut)} minRows={12} copyLabel="Копировать" />
    </div>
  );
}

export default function ComfyNarrativeNode({ id, data }) {
  const safeContentType = getSafeNarrativeContentType(data?.contentType, "music_video");
  const activeResultTab = data?.activeResultTab || "history";
  const outputs = data?.outputs || {};
  const pendingOutputs = data?.pendingOutputs || null;
  const resultOutputs = pendingOutputs || outputs;
  const directorOutput = resultOutputs?.directorOutput || null;
  const storyboardOut = resultOutputs?.storyboardOut || null;
  const resolvedSource = data?.resolvedSource || {};
  const connectedContext = summarizeNarrativeConnectedContext(data || {});
  const activeSourceMode = resolvedSource?.mode || null;
  const hasConnectedSource = resolvedSource?.origin === "connected" && !!String(resolvedSource?.value || "").trim();
  const hasPendingResult = !!pendingOutputs?.directorOutput;
  const hasConfirmedResult = !!outputs?.directorOutput;
  const hasDirectorResult = !!directorOutput;
  const isGenerating = data?.isGenerating === true;
  const rawError = String(data?.error || "").trim();
  const errorMessage = rawError === "NO_SOURCE"
    ? "Подключите один active source-of-truth: audio, video_file или video_link."
    : rawError;
  const sourceStatusText = hasConnectedSource
    ? activeSourceMode === "AUDIO"
      ? "Подключён внешний аудио-источник"
      : activeSourceMode === "VIDEO_LINK"
        ? "Подключена внешняя ссылка на видео"
        : "Подключён внешний видеофайл"
    : "Подключите один source-of-truth: аудио, видеофайл или ссылку на видео.";

  const storyboardScenes = useMemo(() => (Array.isArray(storyboardOut?.scenes) ? storyboardOut.scenes : []), [storyboardOut?.scenes]);
  const directorScenes = useMemo(() => (Array.isArray(directorOutput?.scenes) ? directorOutput.scenes : []), [directorOutput?.scenes]);
  const videoRows = useMemo(() => (Array.isArray(directorOutput?.video) ? directorOutput.video : []), [directorOutput?.video]);
  const soundRows = useMemo(() => (Array.isArray(directorOutput?.sound) ? directorOutput.sound : []), [directorOutput?.sound]);

  const sceneOptions = useMemo(() => {
    const source = storyboardScenes.length
      ? storyboardScenes
      : directorScenes.length
        ? directorScenes
        : videoRows.length
          ? videoRows
          : soundRows;
    return source
      .map((scene, idx) => String(scene?.sceneId || `S${idx + 1}`))
      .filter(Boolean);
  }, [directorScenes, soundRows, storyboardScenes, videoRows]);

  const [selectedSceneId, setSelectedSceneId] = useState("");

  useEffect(() => {
    if (!sceneOptions.length) {
      setSelectedSceneId("");
      return;
    }
    if (!sceneOptions.includes(selectedSceneId)) {
      setSelectedSceneId(sceneOptions[0]);
    }
  }, [sceneOptions, selectedSceneId]);

  useEffect(() => {
    if ((data?.contentType || "story") !== safeContentType) {
      data?.onFieldChange?.(id, { contentType: safeContentType });
    }
  }, [data?.contentType, data?.onFieldChange, id, safeContentType]);

  const history = directorOutput?.history || storyboardOut?.history || null;

  const sourceInput = hasConnectedSource ? (
    <div className="clipSB_narrativeSourceStatus isConnected">
      <div className="clipSB_narrativeSourceStatusTitle">{sourceStatusText}</div>
      <div className="clipSB_narrativeSourceStatusHint">Источник выбран автоматически по входящему соединению ноды.</div>
      {resolvedSource?.preview ? (
        <div className="clipSB_narrativeSourceStatusPreview" title={resolvedSource.preview}>
          {resolvedSource.preview}
        </div>
      ) : null}
    </div>
  ) : (
    <div className="clipSB_narrativeField clipSB_narrativeField--disabled" aria-disabled="true">
      <div className="clipSB_brainLabel">Source of truth</div>
      <div className="clipSB_narrativeEmptyBlock">
        <div>Подключите источник:</div>
        <div>— Аудио</div>
        <div>— Видео файл</div>
        <div>— Ссылка на видео</div>
      </div>
      <div className="clipSB_narrativeEmptyHint">Нода ждёт ровно один активный вход: audio_in, video_file_in или video_link_in.</div>
    </div>
  );

  let resultBody = <div className="clipSB_small">Пока нет director output. Подключите источник и нажмите кнопку.</div>;
  if (activeResultTab === "history") resultBody = renderHistoryTab({ directorOutput, history });
  if (activeResultTab === "scenes") resultBody = renderScenesTab({ storyboardScenes, directorScenes, selectedSceneId, setSelectedSceneId });
  if (activeResultTab === "video") resultBody = renderVideoTab({ storyboardScenes, videoRows, selectedSceneId, setSelectedSceneId });
  if (activeResultTab === "sound") resultBody = renderSoundTab({ storyboardScenes, soundRows, selectedSceneId, setSelectedSceneId });
  if (activeResultTab === "music") resultBody = renderMusicTab({ directorOutput, audioData: storyboardOut?.audioData || {} });
  if (activeResultTab === "json") resultBody = renderJsonTab(storyboardOut || directorOutput);

  return (
    <>
      {NARRATIVE_INPUT_HANDLES.map((item, index) => (
        <Handle
          key={item.id}
          type="target"
          position={Position.Left}
          id={item.id}
          className="clipSB_handle"
          style={handleStyle(item.id, { top: NARRATIVE_HANDLE_TOP + index * NARRATIVE_HANDLE_STEP })}
        />
      ))}
      {OUTPUT_HANDLES.map((item, index) => (
        <Handle
          key={item.id}
          type="source"
          position={Position.Right}
          id={item.id}
          className="clipSB_handle"
          style={handleStyle(item.id, { top: NARRATIVE_HANDLE_TOP + index * NARRATIVE_HANDLE_STEP })}
        />
      ))}
      <NodeShell
        title="SCENARIO DIRECTOR"
        icon={<span aria-hidden>📚</span>}
        onClose={() => data?.onRemoveNode?.(id)}
        className="clipSB_nodeNarrative"
      >
        <div className="clipSB_narrativeSubtitle">Главный director / planning узел</div>

        <section className="clipSB_narrativeSection">
          <div className="clipSB_brainLabel">Активный source-of-truth</div>
          <div className="clipSB_narrativeIndicators" aria-label="Доступные источники narrative node" role="status">
            {NARRATIVE_SOURCE_OPTIONS.map((option) => {
              const isActive = activeSourceMode === option.value && hasConnectedSource;
              return (
                <span
                  key={option.value}
                  className={`clipSB_narrativeIndicator ${isActive ? "isActive" : ""}`.trim()}
                  aria-current={isActive ? "true" : "false"}
                >
                  <span>{option.labelRu}</span>
                  {isActive ? <span className="clipSB_narrativeIndicatorBadge">активный источник</span> : null}
                </span>
              );
            })}
          </div>
        </section>

        <div className="clipSB_narrativeLayout">
          <section className="clipSB_narrativeControlColumn">
            <div className="clipSB_narrativeGrid clipSB_narrativeGrid--compact">
              <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                <div className="clipSB_brainLabel clipSB_brainLabel--compact">Тип видео</div>
                <select
                  className="clipSB_select clipSB_narrativeSelect"
                  value={safeContentType}
                  onChange={(e) => {
                    const requested = e.target.value;
                    if (!isNarrativeContentTypeEnabled(requested)) return;
                    data?.onFieldChange?.(id, { contentType: requested });
                  }}
                >
                  {NARRATIVE_CONTENT_TYPE_OPTIONS.map((option) => (
                    <option
                      key={option.value}
                      value={option.value}
                      disabled={!option.isEnabled}
                      className={!option.isEnabled ? "clipSB_selectOptionDisabled" : undefined}
                    >
                      {option.isEnabled ? option.labelRu : `${option.labelRu} (soon)`}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <label className="clipSB_narrativeField">
              <div className="clipSB_brainLabel">Режиссёрская задача / что изменить / добавить</div>
              <textarea
                className="clipSB_textarea clipSB_narrativeTextarea clipSB_narrativeTextarea--compact"
                value={data?.directorNote || ""}
                onChange={(e) => data?.onFieldChange?.(id, { directorNote: e.target.value })}
                placeholder="Например: добавь экшена, сделай мрачнее, усиль конфликт"
                rows={3}
              />
            </label>

            {sourceInput}

            <section className="clipSB_narrativeSection">
              <div className="clipSB_brainLabel">Connected context</div>
              <div className="clipSB_narrativeContextCard">
                <div className="clipSB_narrativeContextRow">
                  <span className="clipSB_narrativeContextLabel">Активный источник</span>
                  <span className={`clipSB_narrativeContextValue${connectedContext.hasActiveSource ? " isReady" : ""}`.trim()}>
                    {connectedContext.activeSourceLabel}
                  </span>
                </div>

                <div className="clipSB_narrativeContextChips">
                  {NARRATIVE_SOURCE_INPUT_HANDLES.map((item) => {
                    const isConnected = !!connectedContext.sourceByHandle?.[item.id];
                    return (
                      <span key={item.id} className={`clipSB_narrativeContextChip${isConnected ? " isReady" : ""}`.trim()}>
                        {item.labelRu}: {isConnected ? "подключён" : "нет"}
                      </span>
                    );
                  })}
                </div>

                <div className="clipSB_narrativeContextGrid">
                  <div className="clipSB_narrativeContextStat">
                    <span>Characters</span>
                    <strong>{connectedContext.characterCount}</strong>
                  </div>
                  <div className="clipSB_narrativeContextStat">
                    <span>Props</span>
                    <strong>{connectedContext.hasProps ? "есть" : "нет"}</strong>
                  </div>
                  <div className="clipSB_narrativeContextStat">
                    <span>Location</span>
                    <strong>{connectedContext.hasLocation ? "есть" : "нет"}</strong>
                  </div>
                  <div className="clipSB_narrativeContextStat">
                    <span>Style</span>
                    <strong>{connectedContext.hasStyle ? "есть" : "нет"}</strong>
                  </div>
                </div>

                <div className="clipSB_narrativeContextHint">
                  Поддерживаемые context inputs: {NARRATIVE_CONTEXT_INPUT_HANDLES.map((item) => item.role || item.id).join(", ")}.
                </div>
              </div>
            </section>

            <div className="clipSB_narrativeActions">
              <button className="clipSB_btn clipSB_narrativeGenerate" onClick={() => (data?.onGenerateScenario || data?.onGenerate)?.(id)} disabled={!hasConnectedSource || isGenerating}>
                {isGenerating ? "ГЕНЕРИРУЕМ..." : "СОЗДАТЬ СЦЕНАРИЙ"}
              </button>
            </div>

            <label className="clipSB_narrativeField clipSB_narrativeFormatField">
              <div className="clipSB_brainLabel">FORMAT</div>
              <select className="clipSB_select clipSB_narrativeSelect clipSB_narrativeSelect--bottom" value={data?.format || "9:16"} onChange={(e) => data?.onFieldChange?.(id, { format: e.target.value })}>
                {NARRATIVE_FORMAT_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            </label>
          </section>

          <section className="clipSB_narrativeResultSection">
            <div className="clipSB_narrativeTabs">
              {NARRATIVE_RESULT_TABS.map((tab) => (
                <button
                  key={tab.value}
                  type="button"
                  className={`clipSB_narrativeTab ${activeResultTab === tab.value ? "isActive" : ""}`.trim()}
                  onClick={() => data?.onFieldChange?.(id, { activeResultTab: tab.value })}
                >
                  {tab.labelRu}
                </button>
              ))}
            </div>

            <div className="clipSB_narrativeResultStatus">
              {isGenerating ? (
                <>
                  <span className="clipSB_narrativeStatusBadge isPending">Генерация</span>
                  <span className="clipSB_narrativeStatusText">Scenario Director ждёт ответ Gemini backend. Повторный клик временно заблокирован.</span>
                </>
              ) : hasPendingResult ? (
                <>
                  <span className="clipSB_narrativeStatusBadge isPending">Ожидает подтверждения</span>
                  <span className="clipSB_narrativeStatusText">Проверьте результат и подтвердите сценарий.</span>
                </>
              ) : hasConfirmedResult ? (
                <>
                  <span className="clipSB_narrativeStatusBadge isConfirmed">Подтверждено</span>
                  <span className="clipSB_narrativeStatusText">Сценарий подтверждён. Перейдите в Storyboard для генерации сцен.</span>
                </>
              ) : (
                <span className="clipSB_narrativeStatusText">Сначала создайте сценарий.</span>
              )}
            </div>

            {errorMessage ? <div className="clipSB_narrativeEmptyHint" role="alert">{errorMessage}</div> : null}

            <div className="clipSB_narrativeResultBody">
              {resultBody}
            </div>

            {hasDirectorResult ? (
              <div className="clipSB_narrativeConfirmActions">
                <div className="clipSB_narrativeConfirmHint">
                  {hasPendingResult
                    ? "Проверьте вкладки с результатом и подтвердите сценарий."
                    : "Сценарий подтверждён. Перейдите в Storyboard для генерации сцен."}
                </div>
                <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => data?.onConfirmScenario?.(id)} disabled={!hasPendingResult}>
                  {hasPendingResult ? "ПОДТВЕРДИТЬ" : "ПОДТВЕРЖДЕНО"}
                </button>
              </div>
            ) : null}
          </section>
        </div>
      </NodeShell>
    </>
  );
}
