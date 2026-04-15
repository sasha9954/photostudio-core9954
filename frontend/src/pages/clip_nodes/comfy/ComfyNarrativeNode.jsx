import React, { useEffect } from "react";
import { Handle, Position, NodeShell, handleStyle } from "./comfyNodeShared";
import {
  NARRATIVE_INPUT_HANDLES,
  NARRATIVE_SOURCE_INPUT_HANDLES,
  NARRATIVE_SOURCE_OPTIONS,
  NARRATIVE_CONTEXT_INPUT_HANDLES,
  NARRATIVE_CONTENT_TYPE_OPTIONS,
  NARRATIVE_FORMAT_OPTIONS,
  getSafeNarrativeContentType,
  isNarrativeContentTypeEnabled,
  summarizeNarrativeConnectedContext,
} from "./comfyNarrativeDomain";

const NARRATIVE_HANDLE_TOP = 104;
const NARRATIVE_HANDLE_STEP = 24;

const OUTPUT_HANDLES = [
  { id: "storyboard_out", labelRu: "Storyboard" },
  { id: "preview_out", labelRu: "Preview" },
];

export default function ComfyNarrativeNode({ id, data }) {
  const safeContentType = getSafeNarrativeContentType(data?.contentType, "music_video");
  const resolvedSource = data?.resolvedSource || {};
  const connectedContext = summarizeNarrativeConnectedContext(data || {});
  const activeSourceMode = resolvedSource?.mode || null;
  const hasConnectedSource = resolvedSource?.origin === "connected" && !!String(resolvedSource?.value || "").trim();
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

  useEffect(() => {
    if ((data?.contentType || "story") !== safeContentType) {
      data?.onFieldChange?.(id, { contentType: safeContentType });
    }
  }, [data?.contentType, data?.onFieldChange, id, safeContentType]);

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
        <div className="clipSB_narrativeSubtitle">Upstream inputs / source-of-truth</div>

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
              <div className="clipSB_brainLabel">Режиссёрская задача / narrative note</div>
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

            <label className="clipSB_narrativeField clipSB_narrativeFormatField">
              <div className="clipSB_brainLabel">FORMAT</div>
              <select className="clipSB_select clipSB_narrativeSelect clipSB_narrativeSelect--bottom" value={data?.format || "9:16"} onChange={(e) => data?.onFieldChange?.(id, { format: e.target.value })}>
                {NARRATIVE_FORMAT_OPTIONS.map((option) => <option key={option} value={option}>{option}</option>)}
              </select>
            </label>

            <section className="clipSB_narrativeSection">
              <div className="clipSB_brainLabel">Route mix policy</div>
              <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                <div className="clipSB_brainLabel clipSB_brainLabel--compact">Route mix mode</div>
                <select
                  className="clipSB_select clipSB_narrativeSelect"
                  value={data?.routeMixMode || "auto"}
                  onChange={(e) => data?.onFieldChange?.(id, { routeMixMode: e.target.value })}
                >
                  <option value="auto">Auto</option>
                  <option value="custom">Custom</option>
                </select>
              </label>
              <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                <div className="clipSB_brainLabel clipSB_brainLabel--compact">Lipsync ratio: {Number(data?.lipsyncRatio ?? 0.25).toFixed(2)}</div>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={Number(data?.lipsyncRatio ?? 0.25)}
                  onChange={(e) => data?.onFieldChange?.(id, { lipsyncRatio: Number(e.target.value) })}
                />
              </label>
              <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                <div className="clipSB_brainLabel clipSB_brainLabel--compact">Max consecutive lipsync</div>
                <input
                  className="clipSB_input"
                  type="number"
                  min="1"
                  max="6"
                  step="1"
                  value={Number(data?.maxConsecutiveLipsync ?? 2)}
                  onChange={(e) => data?.onFieldChange?.(id, { maxConsecutiveLipsync: Number(e.target.value || 2) })}
                />
              </label>
            </section>

            {errorMessage ? <div className="clipSB_narrativeEmptyHint" role="alert" style={{ marginTop: 8 }}>{errorMessage}</div> : null}
          </section>
        </div>
      </NodeShell>
    </>
  );
}
