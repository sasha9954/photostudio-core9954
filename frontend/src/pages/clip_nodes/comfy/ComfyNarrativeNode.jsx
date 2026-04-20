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
const ROUTE_STRATEGY_PRESETS = [
  { key: "balanced_50_25_25", label: "Баланс 50/25/25", description: "на 8 сцен: 4 i2v / 2 ia2v / 2 первый-последний", targets: { i2v: 4, ia2v: 2, first_last: 2 }, maxConsecutiveIa2v: 2 },
  { key: "performance_35_50_15", label: "Больше пения 35/50/15", description: "на 8 сцен: 3 i2v / 4 ia2v / 1 первый-последний", targets: { i2v: 3, ia2v: 4, first_last: 1 }, maxConsecutiveIa2v: 3 },
  { key: "visual_65_10_25", label: "Больше кино 65/10/25", description: "на 8 сцен: 5 i2v / 1 ia2v / 2 первый-последний", targets: { i2v: 5, ia2v: 1, first_last: 2 }, maxConsecutiveIa2v: 1 },
  { key: "no_first_last_50_50_0", label: "Без первый/последний 50/50/0", description: "на 8 сцен: 4 i2v / 4 ia2v / 0 первый-последний", targets: { i2v: 4, ia2v: 4, first_last: 0 }, maxConsecutiveIa2v: 3 },
  { key: "all_lipsync_0_100_0", label: "Живое пение 0/100/0", description: "на 8 сцен: до 8 ia2v, но безвокальные/инструментальные окна автоматически идут в i2v", targets: { i2v: 0, ia2v: 8, first_last: 0 }, maxConsecutiveIa2v: 8 },
  { key: "story_safe_70_20_10", label: "История безопасно 70/20/10", description: "на 8 сцен: 6 i2v / 1-2 ia2v / 0-1 первый-последний", targets: { i2v: 6, ia2v: 1, first_last: 1 }, maxConsecutiveIa2v: 2 },
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


  const clipModeByContentType = safeContentType === "music_video";
  const clipModeByDirectorMode = String(data?.directorMode || data?.director_mode || "").trim().toLowerCase() === "clip";
  const clipModeByMode = String(data?.mode || "").trim().toLowerCase() === "clip";
  const isClipMode = clipModeByContentType || clipModeByDirectorMode || clipModeByMode;
  const routeStrategyMode = String(data?.routeStrategyMode || "auto").trim().toLowerCase();
  const safeRouteStrategyMode = ["auto", "preset", "custom_counts"].includes(routeStrategyMode) ? routeStrategyMode : "auto";
  const routeTargetsPerBlock = data?.routeTargetsPerBlock && typeof data.routeTargetsPerBlock === "object"
    ? data.routeTargetsPerBlock
    : { i2v: 4, ia2v: 2, first_last: 2 };
  const selectedPreset = ROUTE_STRATEGY_PRESETS.find((x) => x.key === data?.routeStrategyPreset) || ROUTE_STRATEGY_PRESETS[0];
  const baseSceneCount = Number(data?.baseSceneCount || 8) || 8;
  const routeTotal = Number(routeTargetsPerBlock?.i2v || 0) + Number(routeTargetsPerBlock?.ia2v || 0) + Number(routeTargetsPerBlock?.first_last || 0);
  const hasRouteTotalWarning = safeRouteStrategyMode === "custom_counts" && routeTotal !== baseSceneCount;
  const selectedStrategySummary = safeRouteStrategyMode === "auto"
    ? {
      title: "Выбрано: Авто",
      lines: [
        "Gemini сам выбирает i2v / ia2v / первый-последний кадр по аудио, вокалу и драматургии.",
      ],
    }
    : safeRouteStrategyMode === "preset"
      ? {
        title: `Выбрано: ${selectedPreset.label}`,
        lines: [
          selectedPreset.description,
          "Для 9+ сцен: лишние сцены добавляются как i2v.",
          "Для длинных клипов: стратегия повторяется блоками примерно по 30 секунд.",
        ],
      }
      : {
        title: "Выбрано: Ручной режим",
        lines: [
          `На 8 сцен: ${Number(routeTargetsPerBlock?.i2v ?? 0)} i2v / ${Number(routeTargetsPerBlock?.ia2v ?? 0)} ia2v / ${Number(routeTargetsPerBlock?.first_last ?? 0)} первый-последний`,
          `Итого: ${routeTotal} / 8 сцен`,
          ...(hasRouteTotalWarning
            ? ["Сумма отличается от 8. Лишние или недостающие сцены будут мягко скорректированы, безопасный маршрут по умолчанию — i2v."]
            : []),
        ],
      };
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

            {isClipMode ? (
              <section className="clipSB_narrativeSection">
                <div className="clipSB_brainLabel">СТРАТЕГИЯ СЦЕН</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 6 }}>
                  {[
                    { key: "auto", label: "Авто" },
                    { key: "preset", label: "Пресет" },
                    { key: "custom_counts", label: "Ручной" },
                  ].map((item) => (
                    <button
                      key={item.key}
                      type="button"
                      className={`clipSB_btn ${safeRouteStrategyMode === item.key ? "clipSB_btnPrimary clipSB_routeStrategyBtn--active" : "clipSB_btnSecondary"}`.trim()}
                      onClick={() => {
                        if (item.key === "preset") {
                          data?.onFieldChange?.(id, {
                            routeStrategyMode: "preset",
                            routeStrategyPreset: "balanced_50_25_25",
                            routeTargetsPerBlock: { i2v: 4, ia2v: 2, first_last: 2 },
                            maxConsecutiveIa2v: 2,
                          });
                          return;
                        }
                        data?.onFieldChange?.(id, { routeStrategyMode: item.key });
                      }}
                    >
                      {item.label}
                    </button>
                  ))}
                </div>

                {safeRouteStrategyMode === "auto" ? (
                  <div className="clipSB_narrativeEmptyHint">Gemini сам выбирает i2v / ia2v / первый-последний кадр по аудио, вокалу и драматургии.</div>
                ) : null}

                {safeRouteStrategyMode === "preset" ? (
                  <div className="clipSB_narrativeField clipSB_narrativeField--compact">
                    <div className="clipSB_brainLabel clipSB_brainLabel--compact">Русские пресеты</div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 6 }}>
                      {ROUTE_STRATEGY_PRESETS.map((preset) => (
                        <button
                          key={preset.key}
                          type="button"
                          className={`clipSB_btn ${(safeRouteStrategyMode === "preset" && selectedPreset.key === preset.key) ? "clipSB_btnPrimary clipSB_routeStrategyBtn--active clipSB_routeStrategyPresetBtn--active" : "clipSB_btnSecondary clipSB_routeStrategyPresetBtn"}`.trim()}
                          title={preset.description}
                          onClick={() => data?.onFieldChange?.(id, {
                            routeStrategyMode: "preset",
                            routeStrategyPreset: preset.key,
                            routeTargetsPerBlock: preset.targets,
                            maxConsecutiveIa2v: preset.maxConsecutiveIa2v,
                          })}
                        >
                          <div>{(safeRouteStrategyMode === "preset" && selectedPreset.key === preset.key) ? `✓ ${preset.label}` : preset.label}</div>
                          <small>{preset.description}</small>
                        </button>
                      ))}
                    </div>
                  </div>
                ) : null}

                {safeRouteStrategyMode === "custom_counts" ? (
                  <>
                    <div className="clipSB_narrativeEmptyHint">Расчёт на 8 сцен / до 30 сек.</div>
                    <div className="clipSB_narrativeEmptyHint">Для длинных клипов стратегия применяется блоками примерно по 30 секунд.</div>
                    <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                      <div className="clipSB_brainLabel clipSB_brainLabel--compact">i2v сцен</div>
                      <input className="clipSB_input" type="number" min="0" value={Number(routeTargetsPerBlock?.i2v ?? 0)} onChange={(e) => data?.onFieldChange?.(id, { routeTargetsPerBlock: { ...routeTargetsPerBlock, i2v: Number(e.target.value || 0) } })} />
                    </label>
                    <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                      <div className="clipSB_brainLabel clipSB_brainLabel--compact">ia2v / lip-sync сцен</div>
                      <input className="clipSB_input" type="number" min="0" value={Number(routeTargetsPerBlock?.ia2v ?? 0)} onChange={(e) => data?.onFieldChange?.(id, { routeTargetsPerBlock: { ...routeTargetsPerBlock, ia2v: Number(e.target.value || 0) } })} />
                    </label>
                    <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                      <div className="clipSB_brainLabel clipSB_brainLabel--compact">первый-последний кадр сцен</div>
                      <input className="clipSB_input" type="number" min="0" value={Number(routeTargetsPerBlock?.first_last ?? 0)} onChange={(e) => data?.onFieldChange?.(id, { routeTargetsPerBlock: { ...routeTargetsPerBlock, first_last: Number(e.target.value || 0) } })} />
                    </label>
                    <label className="clipSB_narrativeField clipSB_narrativeField--compact">
                      <div className="clipSB_brainLabel clipSB_brainLabel--compact">максимум ia2v подряд</div>
                      <input className="clipSB_input" type="number" min="1" max="8" value={Number(data?.maxConsecutiveIa2v ?? 2)} onChange={(e) => data?.onFieldChange?.(id, { maxConsecutiveIa2v: Number(e.target.value || 2) })} />
                    </label>
                    {hasRouteTotalWarning ? <div className="clipSB_narrativeEmptyHint" role="alert">Сумма отличается от 8. Лишние или недостающие сцены будут мягко скорректированы, безопасный маршрут по умолчанию — i2v.</div> : null}
                  </>
                ) : null}

                <div className="clipSB_routeStrategySummary" role="status" aria-live="polite">
                  <div className="clipSB_routeStrategySummaryTitle">{selectedStrategySummary.title}</div>
                  {selectedStrategySummary.lines.map((line) => (
                    <div key={line} className="clipSB_routeStrategySummaryLine">{line}</div>
                  ))}
                </div>
              </section>
            ) : (
              <section className="clipSB_narrativeSection">
                <div className="clipSB_brainLabel">ПАРАМЕТРЫ ИСТОРИИ</div>
                <div className="clipSB_narrativeEmptyHint">темп: Авто</div>
                <div className="clipSB_narrativeEmptyHint">плотность сцен: Авто</div>
                <div className="clipSB_narrativeEmptyHint">эмоциональная интенсивность: Авто</div>
              </section>
            )}

            {errorMessage ? <div className="clipSB_narrativeEmptyHint" role="alert" style={{ marginTop: 8 }}>{errorMessage}</div> : null}
          </section>
        </div>
      </NodeShell>
    </>
  );
}
