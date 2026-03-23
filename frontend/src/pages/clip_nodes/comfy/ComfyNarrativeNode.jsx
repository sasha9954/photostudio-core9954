import React from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";
import {
  NARRATIVE_INPUT_HANDLES,
  NARRATIVE_SOURCE_INPUT_HANDLES,
  NARRATIVE_SOURCE_OPTIONS,
  NARRATIVE_CONTEXT_INPUT_HANDLES,
  NARRATIVE_CONTENT_TYPE_OPTIONS,
  NARRATIVE_MODE_OPTIONS,
  NARRATIVE_STYLE_OPTIONS,
  NARRATIVE_RESULT_TABS,
  summarizeNarrativeConnectedContext,
} from "./comfyNarrativeDomain";

const NARRATIVE_HANDLE_TOP = 104;
const NARRATIVE_HANDLE_STEP = 24;

const OUTPUT_HANDLES = [
  { id: "storyboard_out", labelRu: "Storyboard" },
];

function renderKvRows(rows = []) {
  return (
    <div className="clipSB_narrativeReadable">
      {rows.map((row) => (
        <div key={row.label}>
          <strong>{row.label}:</strong> {row.value || "—"}
        </div>
      ))}
    </div>
  );
}

function renderHistoryTab(directorOutput) {
  const history = directorOutput?.history;
  if (!history) return <div className="clipSB_small">История появится после генерации director output.</div>;

  return (
    <div className="clipSB_narrativeReadable">
      <div className="clipSB_narrativeCard">
        <div className="clipSB_narrativeCardTitle">Краткий summary</div>
        <div>{history.summary || "—"}</div>
      </div>

      <div className="clipSB_narrativeCard">
        <div className="clipSB_narrativeCardTitle">Полный сценарий</div>
        <pre>{history.fullScenario || "—"}</pre>
      </div>

      <div className="clipSB_narrativeCard">
        <div className="clipSB_narrativeCardTitle">Роли персонажей</div>
        <div className="clipSB_narrativeList">
          {(history.characterRoles || []).map((item) => (
            <div key={`${item.name}:${item.role}`} className="clipSB_narrativeListItem">
              <strong>{item.name}</strong>
              <span>{item.role}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="clipSB_narrativeCard">
        <div className="clipSB_narrativeCardTitle">Tone & style direction</div>
        <div>{history.toneStyleDirection || "—"}</div>
      </div>

      <div className="clipSB_narrativeCard">
        <div className="clipSB_narrativeCardTitle">Director summary</div>
        <pre>{history.directorSummary || "—"}</pre>
      </div>
    </div>
  );
}

function renderScenesTab(directorOutput) {
  const scenes = Array.isArray(directorOutput?.scenes) ? directorOutput.scenes : [];
  if (!scenes.length) return <div className="clipSB_small">Сцены появятся после генерации director output.</div>;

  return (
    <div className="clipSB_narrativeStack">
      {scenes.map((scene) => (
        <div key={scene.sceneId} className="clipSB_narrativeCard">
          <div className="clipSB_narrativeCardHeader">
            <div className="clipSB_narrativeCardTitle">{scene.sceneId}</div>
            <div className="clipSB_narrativeCardMeta">{scene.duration}</div>
          </div>
          {renderKvRows([
            { label: "time_start", value: `${scene.timeStart}s` },
            { label: "time_end", value: `${scene.timeEnd}s` },
            { label: "duration", value: `${scene.duration}s` },
            { label: "Кто участвует", value: (scene.participants || []).join(", ") },
            { label: "Локация", value: scene.location },
            { label: "Props", value: (scene.props || []).join(", ") },
            { label: "Что происходит", value: scene.action },
            { label: "Эмоция", value: scene.emotion },
            { label: "Scene goal", value: scene.sceneGoal },
          ])}
        </div>
      ))}
    </div>
  );
}

function renderVideoTab(directorOutput) {
  const videoRows = Array.isArray(directorOutput?.video) ? directorOutput.video : [];
  if (!videoRows.length) return <div className="clipSB_small">Видео-описание появится после генерации director output.</div>;

  return (
    <div className="clipSB_narrativeStack">
      {videoRows.map((scene) => (
        <div key={scene.sceneId} className="clipSB_narrativeCard">
          <div className="clipSB_narrativeCardTitle">{scene.sceneId}</div>
          {renderKvRows([
            { label: "Frame description", value: scene.frameDescription },
            { label: "Action in frame", value: scene.actionInFrame },
            { label: "Camera idea", value: scene.cameraIdea },
            { label: "Image prompt", value: scene.imagePrompt },
            { label: "Video prompt", value: scene.videoPrompt },
            { label: "LTX mode", value: scene.ltxMode },
            { label: "Why this mode", value: scene.whyThisMode },
            { label: "Start frame source", value: scene.startFrameSource },
            { label: "Needs two frames", value: scene.needsTwoFrames ? "Да" : "Нет" },
            { label: "Continuation", value: scene.continuation },
          ])}
        </div>
      ))}
    </div>
  );
}

function renderSoundTab(directorOutput) {
  const soundRows = Array.isArray(directorOutput?.sound) ? directorOutput.sound : [];
  if (!soundRows.length) return <div className="clipSB_small">Звук появится после генерации director output.</div>;

  return (
    <div className="clipSB_narrativeStack">
      {soundRows.map((scene) => (
        <div key={scene.sceneId} className="clipSB_narrativeCard">
          <div className="clipSB_narrativeCardTitle">{scene.sceneId}</div>
          {renderKvRows([
            { label: "Narration mode", value: scene.narrationMode },
            { label: "Local phrase", value: scene.localPhrase },
            { label: "SFX", value: scene.sfx },
            { label: "Sound notes", value: scene.soundNotes },
            { label: "Pause / duck / silence notes", value: scene.pauseDuckSilenceNotes },
          ])}
        </div>
      ))}
    </div>
  );
}

function renderMusicTab(directorOutput) {
  const music = directorOutput?.music;
  if (!music) return <div className="clipSB_small">Музыка появится после генерации director output.</div>;
  return renderKvRows([
    { label: "Global music prompt", value: music.globalMusicPrompt },
    { label: "Mood", value: music.mood },
    { label: "Style", value: music.style },
    { label: "Pacing hints", value: music.pacingHints },
  ]);
}

function renderJsonTab(storyboardOut) {
  if (!storyboardOut) return <div className="clipSB_small">JSON появится после генерации storyboard_out.</div>;
  return <pre>{JSON.stringify(storyboardOut, null, 2)}</pre>;
}

export default function ComfyNarrativeNode({ id, data }) {
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
  const sourceStatusText = hasConnectedSource
    ? activeSourceMode === "AUDIO"
      ? "Подключён внешний аудио-источник"
      : activeSourceMode === "VIDEO_LINK"
        ? "Подключена внешняя ссылка на видео"
        : "Подключён внешний видеофайл"
    : "Подключите один source-of-truth: аудио, видеофайл или ссылку на видео.";

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
  if (activeResultTab === "history") resultBody = renderHistoryTab(directorOutput);
  if (activeResultTab === "scenes") resultBody = renderScenesTab(directorOutput);
  if (activeResultTab === "video") resultBody = renderVideoTab(directorOutput);
  if (activeResultTab === "sound") resultBody = renderSoundTab(directorOutput);
  if (activeResultTab === "music") resultBody = renderMusicTab(directorOutput);
  if (activeResultTab === "json") resultBody = renderJsonTab(storyboardOut);

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
                <select className="clipSB_select clipSB_narrativeSelect" value={data?.contentType || "story"} onChange={(e) => data?.onFieldChange?.(id, { contentType: e.target.value })}>
                  {NARRATIVE_CONTENT_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.labelRu}</option>)}
                </select>
              </label>

              <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                <div className="clipSB_brainLabel clipSB_brainLabel--compact">Как обработать</div>
                <select className="clipSB_select clipSB_narrativeSelect" value={data?.narrativeMode || "cinematic_expand"} onChange={(e) => data?.onFieldChange?.(id, { narrativeMode: e.target.value })}>
                  {NARRATIVE_MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.labelRu}</option>)}
                </select>
              </label>

              <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                <div className="clipSB_brainLabel clipSB_brainLabel--compact">Стиль обработки</div>
                <select className="clipSB_select clipSB_narrativeSelect" value={data?.styleProfile || "realistic"} onChange={(e) => data?.onFieldChange?.(id, { styleProfile: e.target.value })}>
                  {NARRATIVE_STYLE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.labelRu}</option>)}
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
              <button className="clipSB_btn clipSB_narrativeGenerate" onClick={() => (data?.onGenerateScenario || data?.onGenerate)?.(id)} disabled={!hasConnectedSource}>
                СОЗДАТЬ СЦЕНАРИЙ
              </button>
            </div>
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
              {hasPendingResult ? (
                <>
                  <span className="clipSB_narrativeStatusBadge isPending">Ожидает подтверждения</span>
                  <span className="clipSB_narrativeStatusText">Проверьте результат и подтвердите передачу в Storyboard.</span>
                </>
              ) : hasConfirmedResult ? (
                <>
                  <span className="clipSB_narrativeStatusBadge isConfirmed">Подтверждено</span>
                  <span className="clipSB_narrativeStatusText">Director output подтверждён. При необходимости отправьте storyboard_out в Storyboard.</span>
                </>
              ) : (
                <span className="clipSB_narrativeStatusText">Сначала создайте сценарий.</span>
              )}
            </div>

            <div className="clipSB_narrativeResultBody">
              {resultBody}
            </div>

            {hasDirectorResult ? (
              <div className="clipSB_narrativeConfirmActions">
                <div className="clipSB_narrativeConfirmHint">
                  {hasPendingResult ? "После проверки зафиксируйте director output или сразу передайте storyboard_out в Storyboard." : "Результат уже подтверждён. Storyboard остаётся основным consumer storyboard_out."}
                </div>
                <button className="clipSB_btn clipSB_btnSecondary" type="button" onClick={() => data?.onConfirmScenario?.(id)} disabled={!hasPendingResult}>
                  {hasPendingResult ? "ПОДТВЕРДИТЬ" : "ПОДТВЕРЖДЕНО"}
                </button>
                <button className="clipSB_btn" type="button" onClick={() => data?.onSendToStoryboard?.(id)}>
                  В СТОРИБОРД
                </button>
              </div>
            ) : null}
          </section>
        </div>
      </NodeShell>
    </>
  );
}
