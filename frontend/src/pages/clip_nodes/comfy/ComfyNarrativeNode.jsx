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
  { id: "scenario_out", labelRu: "Сценарий" },
  { id: "voice_script_out", labelRu: "Озвучка" },
  { id: "brain_package_out", labelRu: "Для мозга" },
  { id: "bg_music_prompt_out", labelRu: "Музыка" },
];

function renderBrainPackage(brainPackage) {
  if (!brainPackage) {
    return <div className="clipSB_small">Нажмите «СОЗДАТЬ СЦЕНАРИЙ», чтобы собрать пакет для мозга.</div>;
  }

  const entities = Array.isArray(brainPackage.entities) ? brainPackage.entities : [];
  const sceneLogic = Array.isArray(brainPackage.sceneLogic) ? brainPackage.sceneLogic : [];

  return (
    <div className="clipSB_narrativeReadable">
      <div><strong>Тип контента:</strong> {brainPackage.contentTypeLabel}</div>
      <div><strong>Стиль:</strong> {brainPackage.styleLabel}</div>
      <div><strong>Главный источник:</strong> {brainPackage.sourceLabel}</div>
      <div><strong>Происхождение:</strong> {brainPackage.sourceOrigin === "connected" ? "Подключённый источник" : "Источник не подключён"}</div>
      <div><strong>Превью источника:</strong> {brainPackage.sourcePreview || "—"}</div>
      <div><strong>Сущности:</strong> {entities.join(", ") || "—"}</div>
      <div>
        <strong>Логика сцен:</strong>
        <ol>
          {sceneLogic.map((item) => <li key={item}>{item}</li>)}
        </ol>
      </div>
      <div><strong>Аудио стратегия:</strong> {brainPackage.audioStrategy}</div>
      <div><strong>Режиссёрская задача:</strong> {brainPackage.directorNote}</div>
    </div>
  );
}

export default function ComfyNarrativeNode({ id, data }) {
  const activeResultTab = data?.activeResultTab || "scenario";
  const outputs = data?.outputs || {};
  const resolvedSource = data?.resolvedSource || {};
  const connectedContext = summarizeNarrativeConnectedContext(data || {});
  const activeSourceMode = resolvedSource?.mode || null;
  const hasConnectedSource = resolvedSource?.origin === "connected" && !!String(resolvedSource?.value || "").trim();
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
        <div className="clipSB_narrativeSubtitle">Главный director / planning узел для истории, контекста и сцен</div>

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
            <div className="clipSB_narrativeGrid">
              <label className="clipSB_narrativeField">
                <div className="clipSB_brainLabel">Тип видео</div>
                <select className="clipSB_select" value={data?.contentType || "story"} onChange={(e) => data?.onFieldChange?.(id, { contentType: e.target.value })}>
                  {NARRATIVE_CONTENT_TYPE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.labelRu}</option>)}
                </select>
              </label>

              <label className="clipSB_narrativeField">
                <div className="clipSB_brainLabel">Как обработать</div>
                <select className="clipSB_select" value={data?.narrativeMode || "cinematic_expand"} onChange={(e) => data?.onFieldChange?.(id, { narrativeMode: e.target.value })}>
                  {NARRATIVE_MODE_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.labelRu}</option>)}
                </select>
              </label>

              <label className="clipSB_narrativeField">
                <div className="clipSB_brainLabel">Стиль обработки</div>
                <select className="clipSB_select" value={data?.styleProfile || "realistic"} onChange={(e) => data?.onFieldChange?.(id, { styleProfile: e.target.value })}>
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
                RUN SCENARIO DIRECTOR
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

            <div className="clipSB_narrativeResultBody">
              {activeResultTab === "scenario" ? <pre>{outputs.scenario || "Пока нет сценария. Подключите источник и нажмите кнопку."}</pre> : null}
              {activeResultTab === "voice" ? <pre>{outputs.voiceScript || "Здесь появится текст для диктора и диалоги."}</pre> : null}
              {activeResultTab === "brain" ? renderBrainPackage(outputs.brainPackage) : null}
              {activeResultTab === "music" ? <pre>{outputs.bgMusicPrompt || "Здесь появится prompt только для фоновой музыки."}</pre> : null}
            </div>
          </section>
        </div>
      </NodeShell>
    </>
  );
}
